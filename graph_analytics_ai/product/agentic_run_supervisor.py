"""Agentic run supervisor for FR-31a Phase 1.

This module bridges the product-layer ``WorkflowRun`` record to the
underlying ``AgenticWorkflowRunner``. It is responsible for:

* spawning agentic runs onto a bounded ``ThreadPoolExecutor`` so HTTP
  requests do not block on multi-minute LLM calls,
* attaching a :class:`StepStatusReporter` to the runner's trace
  collector so live ``STEP_START`` / ``STEP_END`` / ``AGENT_ERROR``
  events update product ``WorkflowStep`` rows in real time,
* exposing cooperative cancellation via a per-run ``threading.Event``
  the orchestrator polls between steps,
* sweeping orphan runs left in ``RUNNING`` after API restart (Phase 1
  is in-process, so a restart loses any in-flight runs — sweep flips
  them to ``failed`` with a ``stale_run_detected`` reason so the UI
  doesn't lie about state).

Phase 1 deliberately uses a process-local executor matching the
existing MCP background pattern. Migration to a durable task queue
(Arq, Celery, RQ) is FR-31b — see the PRD for the migration plan.

Naming notes:
* ``AgenticRunSupervisor`` (not ``WorkflowExecutor``) — "executor" is
  the name of ``concurrent.futures.Executor`` and we don't want
  callers thinking they can ``submit(fn, *args)`` arbitrary callables.
* ``StepStatusReporter`` (not ``StepStatusBridge``) — this is a
  listener, not a GoF bridge.
* ``LLMProviderFactory.for_workspace(workspace_id)`` — the
  ``workspace_id`` argument is the seam through which future
  per-workspace ``LLMProfile`` records will plug in. Phase 1 ignores
  it and returns the env default.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..ai.agents.constants import WorkflowSteps
from ..ai.agents.orchestrator import WorkflowCancelled
from ..ai.tracing import TraceEvent, TraceEventType
from .profile_connection import (
    STARTED_BY_KEY,
    is_platform_login,
    open_as_platform_user,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical step layout
# ---------------------------------------------------------------------------


# The canonical six-step layout that ``AgenticWorkflowRunner`` always
# walks (see ``WorkflowSteps.STANDARD_WORKFLOW``). The product layer
# seeds these steps when ``workflow_mode == AGENTIC`` so the visualizer
# rows always line up with the runner's actual phases. Each entry maps
# the canonical step_id used in the run row to a human label and the
# trace event ``agent_name`` (a.k.a. the runner's phase name) that
# should drive its status transitions.
@dataclass(frozen=True)
class CanonicalStep:
    step_id: str
    label: str
    runner_phase: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "step_id": self.step_id,
            "label": self.label,
            "runner_phase": self.runner_phase,
        }


AGENTIC_STEP_LAYOUT: List[CanonicalStep] = [
    CanonicalStep("schema_analysis", "Schema Analysis", WorkflowSteps.SCHEMA_ANALYSIS),
    CanonicalStep(
        "requirements_extraction",
        "Requirements Extraction",
        WorkflowSteps.REQUIREMENTS_EXTRACTION,
    ),
    CanonicalStep(
        "use_case_generation",
        "Use Case Generation",
        WorkflowSteps.USE_CASE_GENERATION,
    ),
    CanonicalStep(
        "template_generation",
        "Template Generation",
        WorkflowSteps.TEMPLATE_GENERATION,
    ),
    CanonicalStep("execution", "Execution", WorkflowSteps.EXECUTION),
    CanonicalStep("reporting", "Reporting", WorkflowSteps.REPORTING),
    # FR-33: the PRD lists catalog persistence as a workflow stage, and it
    # genuinely is one — but it runs in the *product* layer
    # (record_workflow_analysis_executions), not inside the agent runner, so
    # no trace event will ever carry this phase. CATALOG_PERSISTENCE_PHASE is
    # a product-owned sentinel and the supervisor drives this row directly.
    CanonicalStep("catalog_persistence", "Catalog Persistence", "catalog_persistence"),
]

# The step the supervisor updates itself rather than via a runner trace event.
CATALOG_PERSISTENCE_STEP_ID = "catalog_persistence"

# Reverse lookup: runner phase name → canonical step_id. The reporter
# uses this to route STEP_START / STEP_END events to the right
# WorkflowStep row.
_PHASE_TO_STEP_ID: Dict[str, str] = {
    canonical.runner_phase: canonical.step_id for canonical in AGENTIC_STEP_LAYOUT
}


def canonical_steps() -> List[CanonicalStep]:
    """Return a fresh copy of the canonical layout (defensive)."""

    return list(AGENTIC_STEP_LAYOUT)


def step_id_for_phase(phase: Optional[str]) -> Optional[str]:
    """Map a runner phase name to its canonical product step_id.

    Returns ``None`` for unknown phases so the reporter can drop
    events that aren't part of the canonical layout (e.g.
    ``WORKFLOW_START``) without raising.
    """

    if not phase:
        return None
    return _PHASE_TO_STEP_ID.get(phase)


# ---------------------------------------------------------------------------
# LLM provider factory seam
# ---------------------------------------------------------------------------


class LLMProviderFactory:
    """Phase 1 LLM provider factory.

    Exposes ``for_workspace(workspace_id)`` so the supervisor never
    talks to ``create_llm_provider`` directly. In Phase 1 the
    workspace_id argument is ignored and the env default is returned.
    Phase 2 (FR-31b+) will look up per-workspace ``LLMProfile`` records
    here without any change required at the supervisor call site.

    The factory is constructed with an optional ``loader`` for tests.
    Production code uses the default which calls
    ``graph_analytics_ai.ai.llm.factory.create_llm_provider``.
    """

    def __init__(self, loader: Optional[Callable[..., Any]] = None) -> None:
        # The loader is a callable matching ``create_llm_provider``'s
        # signature. Tests pass a stub returning a fake provider so
        # they don't need real API keys.
        self._loader = loader

    def for_workspace(self, workspace_id: str) -> Any:  # noqa: ARG002 - Phase 1 seam
        """Return an LLMProvider for the given workspace.

        Phase 1 ignores ``workspace_id`` and returns the env default
        — the seam exists so Phase 2 can switch to a per-workspace
        lookup by changing only this method body.
        """

        loader = self._loader
        if loader is None:
            # Imported lazily so the supervisor module is importable
            # without the LLM extras installed.
            from ..ai.llm.factory import create_llm_provider as loader  # type: ignore[no-redef]

        return loader()


# ---------------------------------------------------------------------------
# Step status reporter
# ---------------------------------------------------------------------------


@dataclass
class _RunHandle:
    """Per-run bookkeeping owned by the supervisor."""

    run_id: str
    workspace_id: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Optional[Future] = None
    started_at_monotonic: Optional[float] = None


class StepStatusReporter:
    """Translate ``TraceEvent``s into product ``WorkflowStep`` updates.

    Subscribes to a :class:`TraceCollector` (via ``add_listener``) and
    forwards step transitions to the product service's
    ``update_workflow_step`` method. Listener exceptions are
    intentionally swallowed by ``TraceCollector.record_event`` — the
    reporter logs them so failures are visible without breaking the
    workflow run.

    The reporter is intentionally service-shape-only (it just needs an
    object with an ``update_workflow_step`` method) so unit tests can
    pass a dataclass / mock without spinning up the full
    ``ProductService``.
    """

    def __init__(
        self,
        run_id: str,
        service: Any,
    ) -> None:
        self._run_id = run_id
        self._service = service
        # We import the enums lazily inside __call__ so this module
        # has no hard import dependency on product.models. Tests can
        # use the real enum values via the same lazy import.

    def __call__(self, event: TraceEvent) -> None:
        """Handle a single trace event from the runner."""

        from .models import WorkflowStepStatus  # local import — see ctor note

        event_type = event.event_type
        phase = event.agent_name
        step_id = step_id_for_phase(phase)

        if step_id is None:
            # Not a phase event we care about (e.g. WORKFLOW_START,
            # SUB_AGENT_INVOKED, METRIC, etc.). Reporter is intentionally
            # narrow — broader telemetry should subscribe with its own
            # listener.
            return

        try:
            if event_type == TraceEventType.STEP_START:
                self._service.update_workflow_step(
                    run_id=self._run_id,
                    step_id=step_id,
                    status=WorkflowStepStatus.RUNNING,
                    _internal=True,
                )
            elif event_type == TraceEventType.STEP_END:
                # STEP_END can carry a duration / output payload in
                # event.data. We surface duration in step.cost so the
                # visualizer can show "schema took 12s".
                cost = None
                if event.duration_ms is not None:
                    cost = {"duration_ms": event.duration_ms}
                outputs = None
                if isinstance(event.data, dict):
                    outputs = {
                        k: v for k, v in event.data.items() if k not in ("step",)
                    }
                self._service.update_workflow_step(
                    run_id=self._run_id,
                    step_id=step_id,
                    status=WorkflowStepStatus.COMPLETED,
                    outputs=outputs,
                    cost=cost,
                    _internal=True,
                )
            elif event_type == TraceEventType.AGENT_ERROR:
                error_message = ""
                if isinstance(event.data, dict):
                    error_message = str(
                        event.data.get("error") or event.data.get("message") or ""
                    )
                self._service.update_workflow_step(
                    run_id=self._run_id,
                    step_id=step_id,
                    status=WorkflowStepStatus.FAILED,
                    errors=[error_message] if error_message else None,
                    _internal=True,
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "StepStatusReporter failed to translate event %s for run=%s step=%s",
                event_type,
                self._run_id,
                step_id,
            )


# ---------------------------------------------------------------------------
# Run status enum (string sentinels, not exposed as a model enum)
# ---------------------------------------------------------------------------


# These sentinels are returned by ``AgenticRunSupervisor.get_status``
# and recorded as ``run.metadata["execution"]["last_outcome"]`` so
# operators can distinguish "the run failed because the orchestrator
# raised" from "the run failed because the supervisor itself crashed
# before delegating".
RUN_OUTCOME_PENDING = "pending"
RUN_OUTCOME_RUNNING = "running"
RUN_OUTCOME_COMPLETED = "completed"
RUN_OUTCOME_CANCELLED = "cancelled"
RUN_OUTCOME_FAILED = "failed"


# ---------------------------------------------------------------------------
# AgenticRunSupervisor
# ---------------------------------------------------------------------------


# Default phase-1 caps. The supervisor honors environment overrides so
# operators can dial these down without a code change while we collect
# evidence about cost and concurrency on real workloads.
def _default_max_workers() -> int:
    raw = os.getenv("AGA_AGENTIC_MAX_WORKERS", "2")
    try:
        return max(1, int(raw))
    except ValueError:
        return 2


def _default_max_executions() -> int:
    raw = os.getenv("AGA_AGENTIC_MAX_EXECUTIONS", "3")
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return 3


class AgenticRunSupervisor:
    """Owns the lifecycle of agentic ``WorkflowRun`` rows.

    Public surface (intentionally small):
        * :meth:`submit`     — spawn a run on the worker pool
        * :meth:`cancel`     — request cooperative cancellation
        * :meth:`get_status` — supervisor-level status of a run
        * :meth:`shutdown`   — drain the pool on app shutdown
        * :meth:`sweep_orphan_runs` — flip RUNNING rows left over
          from a previous process to FAILED with a stale-run reason

    The supervisor is constructed with optional injection points so
    unit tests can replace the heavy parts (the runner factory, the
    DB connector, the secret resolver, and the LLM provider factory)
    with stubs.
    """

    def __init__(
        self,
        service: Any,
        *,
        max_workers: Optional[int] = None,
        runner_factory: Optional[Callable[..., Any]] = None,
        llm_provider_factory: Optional[LLMProviderFactory] = None,
        db_connector: Optional[Callable[..., Any]] = None,
        secret_resolver: Optional[Any] = None,
    ) -> None:
        self._service = service
        # The runner factory is the test seam for the heavy
        # ``AgenticWorkflowRunner`` import. Tests pass a callable that
        # returns a stub runner driving a known event sequence.
        self._runner_factory = runner_factory or _default_runner_factory
        self._llm_provider_factory = llm_provider_factory or LLMProviderFactory()
        # The supervisor reuses whatever the service is already using
        # for db_connector and secret_resolver so the connection
        # lifecycle is unified — there is one place to swap in a fake
        # in tests.
        self._db_connector = db_connector or getattr(service, "db_connector", None)
        self._secret_resolver = secret_resolver or getattr(
            service, "secret_resolver", None
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers or _default_max_workers(),
            thread_name_prefix="agentic-run",
        )
        self._handles: Dict[str, _RunHandle] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def submit(
        self,
        run_id: str,
        *,
        max_executions: Optional[int] = None,
    ) -> _RunHandle:
        """Submit an agentic run for background execution.

        Idempotent: if a run with this ``run_id`` is already in flight
        the existing handle is returned. The method intentionally
        doesn't validate the run's mode — that's the service's job —
        so the supervisor is focused on lifecycle, not policy.
        """

        with self._lock:
            existing = self._handles.get(run_id)
            if (
                existing is not None
                and existing.future is not None
                and not existing.future.done()
            ):
                return existing

            run = self._service.repository.get_workflow_run(run_id)
            handle = _RunHandle(run_id=run_id, workspace_id=run.workspace_id)
            self._handles[run_id] = handle

            future = self._executor.submit(
                self._run_workflow,
                handle,
                max_executions or _default_max_executions(),
            )
            handle.future = future
            return handle

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self, run_id: str) -> bool:
        """Request cooperative cancellation of a run.

        Returns True if the cancel signal was delivered to a handle we
        own (the orchestrator will observe it before the next step).
        Returns False if the run is unknown to this supervisor (e.g.
        because the API process restarted between submit and cancel).
        """

        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None:
            return False
        handle.cancel_event.set()
        return True

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self, run_id: str) -> Dict[str, Any]:
        """Return supervisor-side execution status for a run.

        The returned dict is the source of truth for the
        ``GET /api/runs/{run_id}/status`` endpoint added in FR-31a so
        the UI can poll for cancel / orphan-sweep results without
        re-fetching the full workflow DAG.
        """

        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None:
            return {
                "run_id": run_id,
                "supervised": False,
                "outcome": RUN_OUTCOME_PENDING,
                "cancel_requested": False,
            }
        outcome = self._handle_outcome(handle)
        return {
            "run_id": run_id,
            "supervised": True,
            "outcome": outcome,
            "cancel_requested": handle.cancel_event.is_set(),
        }

    # ------------------------------------------------------------------
    # Orphan sweep
    # ------------------------------------------------------------------

    def sweep_orphan_runs(
        self,
        *,
        reason: str = "stale_run_detected",
    ) -> List[str]:
        """Flip stale RUNNING rows to FAILED on supervisor startup.

        Phase 1's executor is process-local, so any run left in
        RUNNING after the API process restarts is by definition
        orphaned. This method finds those rows and flips them to
        FAILED with a sentinel reason so the visualizer doesn't
        report a perpetual "in flight" state.

        Returns the list of run_ids that were swept so the caller
        can log how many were affected.
        """

        from .models import WorkflowRunStatus  # local import for cycle safety

        repository = self._service.repository
        # Repositories are not required to expose a global "list all
        # runs" — we ask for everything we can see in a way that's
        # both unit-test-friendly and production-safe. If the
        # repository doesn't support workspace-scoped listing we just
        # bail; the visualizer will reconcile lazily next render.
        try:
            all_runs = repository.list_workflow_runs_by_status(WorkflowRunStatus.RUNNING)  # type: ignore[attr-defined]
        except AttributeError:
            return []

        swept: List[str] = []
        for run in all_runs:
            run.status = WorkflowRunStatus.FAILED
            run.errors = list(run.errors or []) + [reason]
            run.metadata = dict(run.metadata or {})
            execution_meta = dict(run.metadata.get("execution") or {})
            execution_meta["last_outcome"] = RUN_OUTCOME_FAILED
            execution_meta["sweep_reason"] = reason
            run.metadata["execution"] = execution_meta
            repository.update_workflow_run(run)
            swept.append(run.run_id)
        return swept

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(
        self,
        *,
        wait: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        """Drain the pool. Used by the FastAPI app's lifespan handler.

        With ``wait=False`` (the default for fast graceful shutdown)
        any in-flight runs are signalled to cancel and the pool is
        shut down without waiting. The FastAPI lifespan handler can
        then mark in-flight rows as ``failed`` with a
        ``shutdown_in_progress`` reason via :meth:`sweep_orphan_runs`
        on the next startup.
        """

        with self._lock:
            for handle in self._handles.values():
                handle.cancel_event.set()

        if wait and timeout_seconds is not None:
            # Older Pythons don't accept timeout on shutdown(); cope
            # by waiting on each future individually.
            for handle in list(self._handles.values()):
                future = handle.future
                if future is None:
                    continue
                try:
                    future.result(timeout=timeout_seconds)
                except Exception:  # noqa: BLE001
                    pass
            self._executor.shutdown(wait=False)
        else:
            self._executor.shutdown(wait=wait)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _execute_runner(
        self,
        runner: Any,
        run: Any,
        handle: "_RunHandle",
        max_executions: int,
    ) -> Any:
        """Run the workflow, choosing the sequential or parallel path (FR-34).

        PARALLEL_AGENTIC runs go through ``runner.run_async``, which walks
        ``OrchestratorAgent._run_parallel_workflow`` (schema + requirements
        concurrently, then templates and reports fanned out). Worker threads
        have no running event loop, so ``asyncio.run`` creates and tears down
        one per run — the loop is confined to this thread, which is what makes
        it safe to have several runs in flight on the pool at once.

        Falls back to the sequential path when the runner predates
        ``run_async`` (the test doubles do), so a missing async surface
        degrades to a working sequential run instead of failing the run.
        """

        from .models import WorkflowMode  # cycle-safe local import

        kwargs = {
            "input_documents": self._build_input_documents(run),
            "database_config": None,
            "max_executions": max_executions,
            "cancel_token": handle.cancel_event,
        }

        wants_parallel = run.workflow_mode is WorkflowMode.PARALLEL_AGENTIC
        run_async = getattr(runner, "run_async", None)
        if wants_parallel and callable(run_async):
            logger.info("Running %s through the parallel async path", run.run_id)
            return asyncio.run(run_async(enable_parallelism=True, **kwargs))

        if wants_parallel:
            logger.warning(
                "Runner for %s has no run_async; falling back to the "
                "sequential path",
                run.run_id,
            )
        return runner.run(**kwargs)

    def _update_catalog_step(
        self,
        run_id: str,
        status: str,
        outputs: Optional[Dict[str, Any]] = None,
        errors: Optional[List[str]] = None,
    ) -> None:
        """Drive the FR-33 catalog-persistence step. Never raises.

        A failure to *report* catalog persistence must not change the run's
        outcome — the same reasoning that makes the catalog write itself a
        warning rather than a run failure.
        """

        from .models import WorkflowStepStatus

        try:
            self._service.update_workflow_step(
                run_id=run_id,
                step_id=CATALOG_PERSISTENCE_STEP_ID,
                status=WorkflowStepStatus(status),
                outputs=outputs,
                errors=errors,
                _internal=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to update catalog-persistence step for run %s", run_id
            )

    def _run_workflow(self, handle: _RunHandle, max_executions: int) -> None:
        """Body executed inside the worker thread."""

        from .models import WorkflowRunStatus  # cycle-safe local import

        run_id = handle.run_id
        # Reload the run inside the worker — submission time and
        # execution time can be far apart, and other writers may have
        # touched the row in between.
        run = self._service.repository.get_workflow_run(run_id)

        try:
            db = self._build_db_connection(run)
            llm_provider = self._llm_provider_factory.for_workspace(run.workspace_id)
            graph_name = self._resolve_graph_name(run)

            # The kwarg name MUST match ``AgenticWorkflowRunner.__init__``
            # — ``db_connection`` (not ``db``). The fake runner the
            # tests use accepts arbitrary kwargs, so this bug only
            # surfaced under live integration. Pinned by
            # ``test_supervisor_passes_kwargs_compatible_with_real_runner``.
            runner = self._runner_factory(
                db_connection=db,
                llm_provider=llm_provider,
                graph_name=graph_name,
                core_collections=self._collection_role_subset(
                    run, role_keys=("core", "primary", "entity")
                ),
                satellite_collections=self._collection_role_subset(
                    run, role_keys=("satellite", "metadata", "lookup")
                ),
                enable_tracing=True,
            )

            # Subscribe the reporter so trace events from this runner
            # produce live WorkflowStep updates.
            reporter = StepStatusReporter(run_id=run_id, service=self._service)
            trace_collector = getattr(runner, "trace_collector", None)
            if trace_collector is not None and hasattr(trace_collector, "add_listener"):
                trace_collector.add_listener(reporter)

            state = self._execute_runner(runner, run, handle, max_executions)

            # FR-31: mirror each algorithm result into the workspace-scoped
            # product Analysis Catalog before marking the run complete. Catalog
            # persistence is observability/lineage, not the analysis critical
            # path, so a metadata write failure becomes a warning rather than
            # converting a successful graph analysis into a failed run.
            # FR-33: bracket the catalog write with its own DAG step so the
            # visualizer shows the stage the PRD describes. The supervisor
            # drives it directly because this work happens here, not in the
            # runner — there is no trace event to translate.
            self._update_catalog_step(run_id, "running")
            try:
                recorded = self._service.record_workflow_analysis_executions(
                    run_id, state
                )
                self._update_catalog_step(
                    run_id,
                    "completed",
                    outputs={"analysis_executions_recorded": len(recorded or [])},
                )
            except Exception as catalog_exc:  # noqa: BLE001
                logger.exception(
                    "Failed to record Analysis Catalog rows for run %s", run_id
                )
                self._update_catalog_step(run_id, "failed", errors=[str(catalog_exc)])
                current = self._service.repository.get_workflow_run(run_id)
                current.warnings = list(current.warnings or []) + [
                    f"Analysis Catalog persistence failed: {catalog_exc}"
                ]
                self._service.repository.update_workflow_run(current)

            self._finalize_run(
                run_id, RUN_OUTCOME_COMPLETED, status=WorkflowRunStatus.COMPLETED
            )

        except WorkflowCancelled as cancelled:
            self._finalize_run(
                run_id,
                RUN_OUTCOME_CANCELLED,
                status=WorkflowRunStatus.CANCELLED,
                error_message=str(cancelled),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agentic run %s failed", run_id)
            self._finalize_run(
                run_id,
                RUN_OUTCOME_FAILED,
                status=WorkflowRunStatus.FAILED,
                error_message=str(exc),
            )

    def _finalize_run(
        self,
        run_id: str,
        outcome: str,
        *,
        status: Any,
        error_message: Optional[str] = None,
    ) -> None:
        """Record final status and outcome on the run + audit event.

        FR-31a AC#5: when ``outcome == cancelled``, also flip any
        in-flight or pending steps to ``CANCELLED`` so the DAG
        doesn't show a permanent ``running`` stripe on the step the
        orchestrator was processing when the cancel token fired.
        Symmetrically, when a run fails after a step started but
        never emitted ``STEP_END``, leave the step's existing status
        alone — the executing agent's last reported state is the
        truthful one.
        """

        from .models import (
            WorkflowStepStatus,
            create_audit_event,
            current_timestamp,
        )  # cycle-safe local import

        run = self._service.repository.get_workflow_run(run_id)
        run.status = status
        run.completed_at = current_timestamp()
        if error_message:
            run.errors = list(run.errors or []) + [error_message]
        run.metadata = dict(run.metadata or {})
        execution_meta = dict(run.metadata.get("execution") or {})
        execution_meta["last_outcome"] = outcome
        execution_meta.setdefault("executor_kind", "inprocess")
        run.metadata["execution"] = execution_meta

        if outcome == RUN_OUTCOME_CANCELLED:
            now = current_timestamp()
            for step in run.steps or []:
                if step.status in (
                    WorkflowStepStatus.RUNNING,
                    WorkflowStepStatus.PENDING,
                ):
                    step.status = WorkflowStepStatus.CANCELLED
                    if step.started_at and not step.completed_at:
                        step.completed_at = now

        self._service.repository.update_workflow_run(run)

        # Emit a matching audit event so the run lifecycle is fully
        # captured. actor="workflow-runner" keeps it distinguishable
        # from user-initiated cancel events (which carry the user's
        # email / system identifier).
        try:
            self._service.repository.create_audit_event(
                create_audit_event(
                    workspace_id=run.workspace_id,
                    actor="workflow-runner",
                    action="execute_workflow",
                    target_type="workflow_run",
                    target_id=run.run_id,
                    metadata={
                        "outcome": outcome,
                        "executor_kind": execution_meta.get("executor_kind"),
                        "step_count": len(run.steps or []),
                        "error_message": error_message,
                    },
                )
            )
        except Exception:  # noqa: BLE001
            # Audit failure must not mask the actual run outcome —
            # the visualizer should still see status=COMPLETED even
            # if audit write hits a transient repo error.
            logger.exception(
                "Failed to write execute_workflow audit for run=%s", run_id
            )

    def _build_db_connection(self, run: Any) -> Any:
        """Resolve the ArangoDB connection for the run's graph profile."""

        if not self._db_connector:
            raise RuntimeError(
                "AgenticRunSupervisor has no db_connector — pass one or wire "
                "service.db_connector before calling submit()"
            )
        if not run.graph_profile_id:
            raise RuntimeError(
                f"Workflow run {run.run_id} has no graph_profile_id; cannot "
                "resolve which database to connect to"
            )

        graph_profile = self._service.repository.get_graph_profile(run.graph_profile_id)
        connection_profile = self._service.repository.get_connection_profile(
            graph_profile.connection_profile_id
        )

        password_ref = (connection_profile.secret_refs or {}).get("password")
        if not password_ref:
            raise RuntimeError(
                f"Connection profile {connection_profile.connection_profile_id} "
                "missing 'password' secret ref"
            )

        if is_platform_login(password_ref):
            # NFR-24: the run acts as the person who started it, on a token
            # minted for them; there is no request (and no forwarded login)
            # by the time it executes.
            started_by = (run.metadata or {}).get(STARTED_BY_KEY)
            if not started_by:
                raise RuntimeError(
                    f"Workflow run {run.run_id} uses the platform login of "
                    f"connection profile {connection_profile.connection_profile_id}, "
                    "but was started without a signed-in platform user; start it "
                    "again from the workspace UI"
                )
            return open_as_platform_user(connection_profile.database, started_by)

        if self._secret_resolver is None:
            raise RuntimeError(
                "AgenticRunSupervisor has no secret_resolver — pass one or "
                "wire service.secret_resolver"
            )
        password = self._secret_resolver.resolve(password_ref)

        return self._db_connector(
            endpoint=connection_profile.endpoint,
            username=connection_profile.username,
            password=password,
            database=connection_profile.database,
            verify_ssl=connection_profile.verify_ssl,
        )

    def _resolve_graph_name(self, run: Any) -> str:
        """Return the graph name to pass to the runner."""

        graph_profile = self._service.repository.get_graph_profile(run.graph_profile_id)
        return graph_profile.graph_name

    def _collection_role_subset(self, run: Any, *, role_keys: tuple) -> List[str]:
        """Pull collection names from the graph profile by role label.

        ``GraphProfile.collection_roles`` is a dict mapping collection
        name → list of role labels (e.g. ``{"Device": ["core"]}``).
        The runner's ``core_collections`` / ``satellite_collections``
        kwargs let the analysis agents reason about which collections
        carry primary signal vs. which carry metadata. We surface them
        here so agentic runs benefit from any role tagging the
        discovery flow has already done; profiles without role labels
        return an empty list and the runner falls back to its
        defaults.
        """

        try:
            graph_profile = self._service.repository.get_graph_profile(
                run.graph_profile_id
            )
        except Exception:  # noqa: BLE001
            return []
        roles = graph_profile.collection_roles or {}
        normalized_keys = {key.lower() for key in role_keys}
        result: List[str] = []
        for collection_name, role_labels in roles.items():
            if not role_labels:
                continue
            for label in role_labels:
                if isinstance(label, str) and label.lower() in normalized_keys:
                    result.append(collection_name)
                    break
        return result

    def _build_input_documents(self, run: Any) -> List[Dict[str, Any]]:
        """Resolve requirement-version BRD content as runner input documents.

        If the run has a requirement_version_id and the version has a
        draft BRD on its metadata, surface that as a single input
        document. Otherwise pass an empty list — the runner / agents
        are expected to handle missing requirements gracefully (the
        Requirements agent will emit a "low confidence" warning).
        """

        if not run.requirement_version_id:
            return []
        try:
            version = self._service.repository.get_requirement_version(
                run.requirement_version_id
            )
        except Exception:  # noqa: BLE001
            return []

        metadata = version.metadata or {}
        draft = metadata.get("draft_brd")
        if not draft:
            return []
        return [
            {
                "name": f"requirements-{version.requirement_version_id}.md",
                "content": draft,
                "source": "requirement_version",
            }
        ]

    def _handle_outcome(self, handle: _RunHandle) -> str:
        """Compute the outcome string from the handle's future state."""

        future = handle.future
        if future is None:
            return RUN_OUTCOME_PENDING
        if not future.done():
            return RUN_OUTCOME_RUNNING
        # done — was it cancel/fail/complete? Look at the persisted
        # row rather than re-raising the future exception so the
        # supervisor's view is consistent with the visualizer's view.
        try:
            run = self._service.repository.get_workflow_run(handle.run_id)
        except Exception:  # noqa: BLE001
            return RUN_OUTCOME_FAILED
        execution_meta = (run.metadata or {}).get("execution") or {}
        return execution_meta.get("last_outcome", RUN_OUTCOME_COMPLETED)


# ---------------------------------------------------------------------------
# Default runner factory
# ---------------------------------------------------------------------------


def _default_runner_factory(**kwargs: Any) -> Any:
    """Build the real ``AgenticWorkflowRunner``.

    Imported lazily so ``AgenticRunSupervisor`` is importable in
    environments where the AI extras are missing (e.g. unit tests of
    the product service that don't exercise the runner).
    """

    from ..ai.agents.runner import AgenticWorkflowRunner

    return AgenticWorkflowRunner(**kwargs)
