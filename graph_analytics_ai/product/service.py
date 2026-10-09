"""Application services for product UI workflows.

The service layer exposes UI-ready read models and workflow operations without
coupling the core package to a web framework.
"""

import base64
import binascii
import hashlib
import html
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..ai.schema.acquire import (
    SchemaAcquisitionBundle,
    SchemaChangeReport,
    acquire_schema,
    describe_schema_change,
)
from ..ai.schema.graph_purpose import classify_graph_purpose
from ..ai.schema.sharding import detect_sharding_profile
from ..ai.schema.sensitivity import (
    classify_conceptual_schema,
    classify_schema_sensitivity,
)
from ..ai.schema.arango_products import detect_arango_products
from ..ai.schema.extractor import SchemaExtractor
from ..ai.schema.models import GraphSchema
from ..config import parse_ssl_verify
from ..db_connection import connect_arango_database
from ..platform_auth import platform_endpoint
from .constants import (
    AUDIT_EVENTS_COLLECTION,
    ANALYSIS_EXECUTIONS_COLLECTION,
    CHART_SPECS_COLLECTION,
    CONNECTION_PROFILES_COLLECTION,
    GRAPH_PROFILES_COLLECTION,
    DOCUMENTS_COLLECTION,
    PRODUCT_SCHEMA_VERSION,
    REPORT_MANIFESTS_COLLECTION,
    REPORT_SECTIONS_COLLECTION,
    REQUIREMENT_VERSIONS_COLLECTION,
    WORKFLOW_RUNS_COLLECTION,
)
from .exceptions import (
    ConflictError,
    DuplicateError,
    LoginRequiredError,
    ValidationError,
)
from .profile_connection import (
    STARTED_BY_KEY,
    ProfileDatabase,
    acting_user,
    is_platform_login,
    open_as_platform_user,
    open_profile_database,
    platform_login_ref,
)
from .models import (
    AnalysisEpoch,
    AnalysisExecution,
    AnalysisExecutionStatus,
    AnalysisTemplate,
    AnalysisTemplateStatus,
    AuditEvent,
    ChartSpec,
    ConnectionProfile,
    ConnectionVerificationStatus,
    CrossGraphLink,
    DeploymentMode,
    DocumentStorageMode,
    GraphProfile,
    GraphSet,
    PublishedSnapshot,
    ReportManifest,
    ReportSection,
    ReportStatus,
    RequirementInterview,
    RequirementInterviewStatus,
    RequirementVersion,
    RequirementVersionStatus,
    SourceDocument,
    UseCase,
    UseCaseOrigin,
    UseCaseStatus,
    Workspace,
    WorkspaceStatus,
    WorkflowDAGEdge,
    WorkflowMode,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
    create_audit_event,
    create_analysis_epoch,
    create_analysis_execution,
    create_analysis_template,
    create_connection_profile,
    create_graph_profile,
    create_graph_set,
    create_published_snapshot,
    create_requirement_interview,
    create_requirement_version,
    create_retention_policy,
    create_source_document,
    create_use_case,
    create_workspace,
    create_workflow_run,
    current_timestamp,
)
from .repository import ProductRepository, WorkspaceSchemaCache
from .secrets import EnvironmentSecretResolver, SecretResolver

logger = logging.getLogger(__name__)

# Product schema additions are backward-compatible collections. Keep the
# accepted bundle set explicit so an older workspace export remains importable
# after additive catalog/schema features land.
_SUPPORTED_BUNDLE_SCHEMA_VERSIONS = frozenset(
    {"1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", PRODUCT_SCHEMA_VERSION}
)


@dataclass
class WorkspaceOverview:
    """Workspace summary for dashboard and API landing pages."""

    workspace: Dict[str, Any]
    counts: Dict[str, int]
    latest_connection_profiles: List[Dict[str, Any]] = field(default_factory=list)
    latest_graph_profiles: List[Dict[str, Any]] = field(default_factory=list)
    latest_source_documents: List[Dict[str, Any]] = field(default_factory=list)
    latest_requirement_versions: List[Dict[str, Any]] = field(default_factory=list)
    latest_workflow_runs: List[Dict[str, Any]] = field(default_factory=list)
    latest_reports: List[Dict[str, Any]] = field(default_factory=list)
    latest_audit_events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert overview to an API-friendly dictionary."""

        return {
            "workspace": self.workspace,
            "counts": self.counts,
            "latest_connection_profiles": self.latest_connection_profiles,
            "latest_graph_profiles": self.latest_graph_profiles,
            "latest_source_documents": self.latest_source_documents,
            "latest_requirement_versions": self.latest_requirement_versions,
            "latest_workflow_runs": self.latest_workflow_runs,
            "latest_reports": self.latest_reports,
            "latest_audit_events": self.latest_audit_events,
        }


@dataclass
class WorkflowDAGView:
    """Workflow run DAG shape for the operational visualizer."""

    run_id: str
    workspace_id: str
    status: str
    workflow_mode: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert DAG view to an API-friendly dictionary."""

        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "workflow_mode": self.workflow_mode,
            "nodes": self.nodes,
            "edges": self.edges,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class ReportBundle:
    """Complete dynamic report payload for rendering or publication."""

    manifest: Dict[str, Any]
    sections: List[Dict[str, Any]]
    charts: List[Dict[str, Any]]
    snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert report bundle to an API-friendly dictionary."""

        return {
            "manifest": self.manifest,
            "sections": self.sections,
            "charts": self.charts,
            "snapshots": self.snapshots,
        }


@dataclass
class ReportExportResult:
    """Renderable export of a report (HTML or Markdown).

    Deliberately has NO ``to_dict`` method. The framework-neutral dispatcher
    (``ProductAPIDispatcher._serialize_response``) only auto-serializes
    objects with ``to_dict``, so by leaving it off we get pass-through
    behavior. The FastAPI adapter (``fastapi_app.py``) detects this type
    after dispatch and converts it to a non-JSON HTTP ``Response`` with the
    correct ``Content-Type`` and ``Content-Disposition`` headers, so the
    browser triggers a file download.
    """

    content: str
    media_type: str
    filename: str
    fmt: str


@dataclass
class WorkspaceBundle:
    """Portable workspace export payload."""

    schema_version: str
    workspace: Dict[str, Any]
    connection_profiles: List[Dict[str, Any]]
    graph_profiles: List[Dict[str, Any]]
    source_documents: List[Dict[str, Any]]
    requirement_interviews: List[Dict[str, Any]]
    requirement_versions: List[Dict[str, Any]]
    workflow_runs: List[Dict[str, Any]]
    reports: List[Dict[str, Any]]
    audit_events: List[Dict[str, Any]] = field(default_factory=list)
    analysis_epochs: List[Dict[str, Any]] = field(default_factory=list)
    analysis_executions: List[Dict[str, Any]] = field(default_factory=list)
    # FR-51 names templates explicitly. Use cases and analysis templates became
    # first-class product records in the FR-19..FR-26 work; this exporter
    # predates them and was never extended, so exports silently omitted both
    # and an "exported" workspace could not be restored intact. Defaulted so
    # older bundles (schema_version < 0.3) still import.
    use_cases: List[Dict[str, Any]] = field(default_factory=list)
    analysis_templates: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert bundle to an API-friendly dictionary."""

        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "connection_profiles": self.connection_profiles,
            "graph_profiles": self.graph_profiles,
            "source_documents": self.source_documents,
            "requirement_interviews": self.requirement_interviews,
            "requirement_versions": self.requirement_versions,
            "workflow_runs": self.workflow_runs,
            "reports": self.reports,
            "audit_events": self.audit_events,
            "analysis_epochs": self.analysis_epochs,
            "analysis_executions": self.analysis_executions,
            "use_cases": self.use_cases,
            "analysis_templates": self.analysis_templates,
        }


@dataclass
class WorkspaceImportResult:
    """Result summary for a workspace bundle import."""

    workspace_id: str
    counts: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        """Convert import result to an API-friendly dictionary."""

        return {
            "workspace_id": self.workspace_id,
            "counts": self.counts,
        }


@dataclass
class ConnectionVerificationResult:
    """Result of testing a connection profile."""

    connection_profile_id: str
    workspace_id: str
    status: str
    verified_at: str
    endpoint: str
    database: str
    error_message: Optional[str] = None
    # FR-7: best-effort GAE deployment reachability, e.g.
    # {"status": "success"} or {"status": "failed", "message": "..."}.
    # GAE credentials are deployment-wide env vars, not per-profile fields,
    # so this reports the deployment's GAE reachability rather than
    # anything specific to this connection profile.
    gae_status: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert verification result to an API-friendly dictionary."""

        return {
            "connection_profile_id": self.connection_profile_id,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "verified_at": self.verified_at,
            "endpoint": self.endpoint,
            "database": self.database,
            "error_message": self.error_message,
            "gae_status": self.gae_status,
        }


@dataclass
class GraphDiscoveryResult:
    """Result of discovering and persisting a graph profile."""

    graph_profile: Dict[str, Any]
    schema_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert discovery result to an API-friendly dictionary."""

        return {
            "graph_profile": self.graph_profile,
            "schema_summary": self.schema_summary,
        }


@dataclass
class WorkspaceGraphInventoryResult:
    """Result of bulk-discovering every named graph on a connection (FR-67).

    The plural variant of :class:`GraphDiscoveryResult`. Returned by
    :meth:`ProductService.discover_graph_profiles`. Callers iterate
    ``graph_profiles`` to render the workspace's complete graph
    inventory and use ``failures`` to surface per-graph errors
    without aborting the whole sweep.

    ``database_only`` is an optional fallback entry returned when
    the connection's database has no named graphs at all (so the
    UI can still show a "treat the database as a single graph"
    card with the same shape as the regular profile cards).
    """

    connection_profile_id: str
    workspace_id: str
    database: str
    discovered_graph_count: int
    graph_profiles: List[Dict[str, Any]]
    failures: List[Dict[str, Any]] = field(default_factory=list)
    database_only: Optional[Dict[str, Any]] = None
    arango_product: Optional[Dict[str, Any]] = None
    """First-party Arango product report (PRD v0.6 / FR-67 follow-up).

    When the connection's database contains artefacts created by an
    Arango product (today: Autograph corpus + KG projects), this
    block carries the detection result so the UI can badge the
    inventory and auto-suggest GraphSets. ``None`` when no product
    artefacts were detected.
    """
    auto_created_graph_sets: List[Dict[str, Any]] = field(default_factory=list)
    """GraphSets that were auto-created from the detection report.

    One entry per detected Autograph project (corpus + KG bundled
    together with the implicit ``rags.entity_types ->
    Entities.entity_type`` cross-graph link). Empty when no
    Autograph projects were detected.
    """

    def to_dict(self) -> Dict[str, Any]:
        """Convert inventory result to an API-friendly dictionary."""

        return {
            "connection_profile_id": self.connection_profile_id,
            "workspace_id": self.workspace_id,
            "database": self.database,
            "discovered_graph_count": self.discovered_graph_count,
            "graph_profiles": self.graph_profiles,
            "failures": self.failures,
            "database_only": self.database_only,
            "arango_product": self.arango_product,
            "auto_created_graph_sets": self.auto_created_graph_sets,
        }


@dataclass
class ConnectionGraphSummary:
    """Lightweight named-graph descriptor for a connection profile."""

    name: str
    is_system: bool
    vertex_collections: List[str]
    edge_collections: List[str]
    orphan_collections: List[str]
    edge_definitions: List[Dict[str, Any]]
    vertex_count: Optional[int] = None
    edge_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to an API-friendly dictionary."""

        return {
            "name": self.name,
            "is_system": self.is_system,
            "vertex_collections": self.vertex_collections,
            "edge_collections": self.edge_collections,
            "orphan_collections": self.orphan_collections,
            "edge_definitions": self.edge_definitions,
            "vertex_count": self.vertex_count,
            "edge_count": self.edge_count,
        }


@dataclass
class ConnectionGraphsResult:
    """Result of enumerating named graphs for a connection profile."""

    connection_profile_id: str
    workspace_id: str
    database: str
    graphs: List[ConnectionGraphSummary]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connection_profile_id": self.connection_profile_id,
            "workspace_id": self.workspace_id,
            "database": self.database,
            "graphs": [graph.to_dict() for graph in self.graphs],
        }


@dataclass
class RequirementsDraftResult:
    """Result of generating a Requirements Copilot draft."""

    requirement_interview: Dict[str, Any]
    draft_brd: str
    provenance_labels: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert draft result to an API-friendly dictionary."""

        return {
            "requirement_interview": self.requirement_interview,
            "draft_brd": self.draft_brd,
            "provenance_labels": self.provenance_labels,
        }


@dataclass
class WorkflowStepUpdateResult:
    """Result of updating workflow run step state."""

    workflow_run: Dict[str, Any]
    dag_view: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert update result to an API-friendly dictionary."""

        return {
            "workflow_run": self.workflow_run,
            "dag_view": self.dag_view,
        }


@dataclass
class SchemaChangeView:
    """Result of probing a graph profile's cached schema for staleness.

    PRD v0.6 / FR-60. Returned by
    :meth:`ProductService.get_graph_profile_schema_change`. The fields
    mirror :class:`graph_analytics_ai.ai.schema.SchemaChangeReport` plus
    the originating ``graph_profile_id`` so the UI can route the response
    back to the right profile card without re-resolving it.
    """

    graph_profile_id: str
    status: str
    current_shape_fingerprint: str
    current_full_fingerprint: str
    cached_shape_fingerprint: Optional[str]
    cached_full_fingerprint: Optional[str]
    needs_full_rebuild: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert to an API-friendly dictionary."""

        return {
            "graph_profile_id": self.graph_profile_id,
            "status": self.status,
            "current_shape_fingerprint": self.current_shape_fingerprint,
            "current_full_fingerprint": self.current_full_fingerprint,
            "cached_shape_fingerprint": self.cached_shape_fingerprint,
            "cached_full_fingerprint": self.cached_full_fingerprint,
            "needs_full_rebuild": self.needs_full_rebuild,
        }


@dataclass
class WorkspaceHealthResult:
    """Workspace product metadata health summary."""

    workspace_id: str
    status: str
    counts: Dict[str, int]
    issues: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert health result to an API-friendly dictionary."""

        return {
            "workspace_id": self.workspace_id,
            "status": self.status,
            "counts": self.counts,
            "issues": self.issues,
        }


def _collections_from_edge_definitions(
    edge_definitions: List[Dict[str, Any]],
    orphan_collections: List[str],
) -> tuple[List[str], List[str]]:
    """Derive deduplicated vertex/edge collection lists from edge definitions."""

    vertex: Dict[str, None] = {}
    edge: Dict[str, None] = {}
    for definition in edge_definitions:
        name = definition.get("edge_collection") or definition.get("collection")
        if name:
            edge[str(name)] = None
        for source in definition.get("from_vertex_collections") or []:
            vertex[str(source)] = None
        for source in definition.get("from") or []:
            vertex[str(source)] = None
        for target in definition.get("to_vertex_collections") or []:
            vertex[str(target)] = None
        for target in definition.get("to") or []:
            vertex[str(target)] = None
    for orphan in orphan_collections or []:
        vertex[str(orphan)] = None
    return list(vertex.keys()), list(edge.keys())


def _safe_collection_total(db: Any, collection_names: List[str]) -> Optional[int]:
    """Sum LENGTH() over the named collections, returning None on failure."""

    if not collection_names:
        return 0
    total = 0
    try:
        for name in collection_names:
            try:
                collection = db.collection(name)
                count = collection.count()
            except Exception:
                cursor = db.aql.execute(
                    "RETURN LENGTH(@@col)",
                    bind_vars={"@col": name},
                )
                count = next(iter(cursor), 0)
            total += int(count or 0)
    except Exception:
        return None
    return total


class ProductService:
    """Use-case oriented product operations for the future UI API."""

    def __init__(
        self,
        repository: ProductRepository,
        secret_resolver: Optional[SecretResolver] = None,
        db_connector: Optional[Callable[..., Any]] = None,
        schema_extractor_factory: Optional[Callable[..., Any]] = None,
        agentic_run_supervisor: Optional[Any] = None,
    ):
        """Initialize service.

        ``agentic_run_supervisor`` is the FR-31a Phase 1 hook that
        actually executes agentic runs. It's optional so existing
        callers (including the current test suite) don't have to
        wire one up — when absent, ``start_workflow_run`` keeps its
        legacy "flip status to RUNNING and return" behavior, which
        is what callers got before FR-31a.
        """

        self.repository = repository
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self.db_connector = db_connector or connect_arango_database
        self.schema_extractor_factory = schema_extractor_factory or SchemaExtractor
        # Optional FR-31a supervisor — the FastAPI app factory wires
        # one in via ``ProductService(..., agentic_run_supervisor=...)``
        # so it can also share its lifespan. Tests typically leave it
        # ``None`` (falling back to the legacy synchronous path) or
        # pass a fake supervisor with a known ``submit`` / ``cancel``
        # / ``get_status`` shape.
        self._agentic_run_supervisor = agentic_run_supervisor

    def create_workspace(
        self,
        customer_name: str,
        project_name: str,
        environment: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        actor: Optional[str] = None,
    ) -> Workspace:
        """Create customer/project workspace metadata."""

        if not customer_name.strip():
            raise ValidationError("Customer name is required")
        if not project_name.strip():
            raise ValidationError("Project name is required")
        if not environment.strip():
            raise ValidationError("Environment is required")

        workspace = create_workspace(
            customer_name=customer_name.strip(),
            project_name=project_name.strip(),
            environment=environment.strip(),
            description=description.strip(),
            tags=[tag.strip() for tag in tags or [] if tag.strip()],
        )
        self.repository.create_workspace(workspace)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace.workspace_id,
                actor=actor or "system",
                action="create_workspace",
                target_type="workspace",
                target_id=workspace.workspace_id,
            )
        )
        return workspace

    def list_workspaces(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Workspace]:
        """List workspaces visible to the caller."""

        return self.repository.list_workspaces(status=status, limit=limit)

    def update_workspace(
        self,
        workspace_id: str,
        customer_name: Optional[str] = None,
        project_name: Optional[str] = None,
        environment: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        actor: Optional[str] = None,
    ) -> Workspace:
        """Patch editable workspace metadata in place.

        Implements PRD FR-1 (workspace identity edit). All editable fields
        are optional so callers can update one column at a time. ``status``
        is intentionally NOT editable through this path — use
        :meth:`archive_workspace` instead so the lifecycle change always
        emits a dedicated audit event.
        """

        workspace = self.repository.get_workspace(workspace_id)
        changes: Dict[str, Any] = {}

        if customer_name is not None:
            stripped = customer_name.strip()
            if not stripped:
                raise ValidationError("Customer name cannot be empty")
            if stripped != workspace.customer_name:
                changes["customer_name"] = {
                    "from": workspace.customer_name,
                    "to": stripped,
                }
                workspace.customer_name = stripped

        if project_name is not None:
            stripped = project_name.strip()
            if not stripped:
                raise ValidationError("Project name cannot be empty")
            if stripped != workspace.project_name:
                changes["project_name"] = {
                    "from": workspace.project_name,
                    "to": stripped,
                }
                workspace.project_name = stripped

        if environment is not None:
            stripped = environment.strip()
            if not stripped:
                raise ValidationError("Environment cannot be empty")
            if stripped != workspace.environment:
                changes["environment"] = {"from": workspace.environment, "to": stripped}
                workspace.environment = stripped

        if description is not None:
            new_description = description.strip()
            if new_description != workspace.description:
                changes["description"] = {
                    "from": workspace.description,
                    "to": new_description,
                }
                workspace.description = new_description

        if tags is not None:
            normalized_tags = [tag.strip() for tag in tags if tag and tag.strip()]
            if normalized_tags != workspace.tags:
                changes["tags"] = {"from": list(workspace.tags), "to": normalized_tags}
                workspace.tags = normalized_tags

        # No-op updates do not emit audit events; they would just clutter
        # the timeline with zero-information rows. We still bump
        # ``updated_at`` only when something actually changed.
        if not changes:
            return workspace

        workspace.updated_at = current_timestamp()
        self.repository.update_workspace(workspace)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace.workspace_id,
                actor=actor or "system",
                action="update_workspace",
                target_type="workspace",
                target_id=workspace.workspace_id,
                details={"changes": changes},
            )
        )
        return workspace

    def archive_workspace(
        self,
        workspace_id: str,
        actor: Optional[str] = None,
    ) -> Workspace:
        """Soft-delete a workspace by flipping it to ARCHIVED.

        Idempotent: calling on an already-archived workspace returns it
        unchanged and does NOT emit a duplicate audit event. PRD FR-1
        treats archival as a soft-delete so historical reports/runs remain
        queryable for lineage/audit.
        """

        workspace = self.repository.get_workspace(workspace_id)
        if workspace.status == WorkspaceStatus.ARCHIVED:
            return workspace

        workspace.status = WorkspaceStatus.ARCHIVED
        workspace.updated_at = current_timestamp()
        self.repository.update_workspace(workspace)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace.workspace_id,
                actor=actor or "system",
                action="archive_workspace",
                target_type="workspace",
                target_id=workspace.workspace_id,
            )
        )
        return workspace

    def set_active_graph_profile(
        self,
        workspace_id: str,
        graph_profile_id: Optional[str],
        actor: Optional[str] = None,
    ) -> Workspace:
        """Set (or clear) the workspace's "current" graph profile (FR-67b).

        The workbench renders one graph profile at a time in its
        "Analyzing X" banner, and most agentic workflows default to
        operating on that same profile. Historically the active
        profile was picked positionally (latest-updated wins), which
        is fragile — running discovery, patching a tag, or simply the
        order in which graphs were created shuffles the banner. This
        method makes the choice explicit and durable.

        Pass ``graph_profile_id=None`` (or empty string) to clear the
        selection; the workbench then falls back to the deterministic
        positional rule.

        Validation:
        - The profile must exist and belong to this workspace. A
          cross-workspace id is rejected so a leaked id from another
          customer's workspace cannot be pointed at here.

        Idempotent: setting to the already-active value (or clearing
        an already-empty selection) returns the workspace unchanged
        and emits no audit event.
        """

        workspace = self.repository.get_workspace(workspace_id)

        normalized: Optional[str]
        if graph_profile_id is None:
            normalized = None
        else:
            stripped = graph_profile_id.strip()
            normalized = stripped or None

        if normalized is not None:
            # Re-resolve to enforce workspace ownership. ``get_graph_profile``
            # raises ENTITY_NOT_FOUND on missing ids; we wrap the cross-
            # workspace case into a ValidationError so the API surface
            # distinguishes "you sent us garbage" from "we lost the row".
            profile = self.repository.get_graph_profile(normalized)
            if profile.workspace_id != workspace_id:
                raise ValidationError("graph_profile_id must belong to this workspace")

        if normalized == workspace.active_graph_profile_id:
            return workspace

        previous = workspace.active_graph_profile_id
        workspace.active_graph_profile_id = normalized
        workspace.updated_at = current_timestamp()
        self.repository.update_workspace(workspace)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace.workspace_id,
                actor=actor or "system",
                action="set_active_graph_profile",
                target_type="workspace",
                target_id=workspace.workspace_id,
                details={"from": previous, "to": normalized},
            )
        )
        return workspace

    def get_workspace_overview(
        self,
        workspace_id: str,
        recent_limit: int = 5,
    ) -> WorkspaceOverview:
        """Build a workspace dashboard summary."""

        workspace = self.repository.get_workspace(workspace_id)
        connection_profiles = self.repository.list_connection_profiles(workspace_id)
        graph_profiles = self.repository.list_graph_profiles(workspace_id)
        source_documents = self.repository.list_source_documents(workspace_id)
        requirement_versions = self.repository.list_requirement_versions(workspace_id)
        # FR-73: ephemeral quick-analysis requirement versions are run inputs,
        # not curated requirements — keep them out of the consolidated
        # Requirements asset list / version dropdown (they remain queryable
        # by id for lineage and for the run that created them).
        requirement_versions = [
            version
            for version in requirement_versions
            if not (version.metadata or {}).get("ephemeral")
        ]
        workflow_runs = self.repository.list_workflow_runs(workspace_id)
        reports = self.repository.list_report_manifests(workspace_id)
        audit_events = self.repository.list_audit_events(
            workspace_id,
            limit=recent_limit,
        )

        counts = {
            "connection_profiles": len(connection_profiles),
            "graph_profiles": len(graph_profiles),
            "source_documents": len(source_documents),
            "requirement_versions": len(requirement_versions),
            "workflow_runs": len(workflow_runs),
            "reports": len(reports),
        }

        # The Assets panel is the ONLY entry point that can open any of these
        # entities, so a `recent_limit` slice here is not a "recent" affordance
        # — it is data loss at the UI layer. Anything past the cap becomes
        # permanently unreachable while `counts` keeps advertising it (observed
        # live: `counts.reports` said 10 while 5 were openable).
        #
        # This bit FR-17c first (requirement versions, "all historical versions
        # remain queryable and individually addressable"), then FR-41 (reports).
        # Rather than fix it a third time, every asset list is now returned in
        # full, newest first. A `latest_*` list may only be capped once a second,
        # uncapped navigation path to those entities exists.
        #
        # `latest_audit_events` stays capped: it is a genuine "recent activity"
        # feed, not a navigable asset list, and the repository applies the limit
        # in the query above.
        sorted_requirement_versions = sorted(
            requirement_versions,
            key=lambda v: (v.version, v.created_at),
            reverse=True,
        )
        sorted_connection_profiles = sorted(
            connection_profiles, key=lambda item: item.created_at, reverse=True
        )
        sorted_graph_profiles = sorted(
            graph_profiles, key=lambda item: item.created_at, reverse=True
        )
        sorted_source_documents = sorted(
            source_documents, key=lambda item: item.uploaded_at, reverse=True
        )
        sorted_workflow_runs = sorted(
            workflow_runs, key=lambda item: item.created_at, reverse=True
        )
        sorted_reports = sorted(reports, key=lambda item: item.created_at, reverse=True)
        return WorkspaceOverview(
            workspace=workspace.to_dict(),
            counts=counts,
            latest_connection_profiles=[
                profile.to_dict() for profile in sorted_connection_profiles
            ],
            latest_graph_profiles=[
                profile.to_dict() for profile in sorted_graph_profiles
            ],
            latest_source_documents=[
                document.to_dict() for document in sorted_source_documents
            ],
            latest_requirement_versions=[
                version.to_dict() for version in sorted_requirement_versions
            ],
            latest_workflow_runs=[run.to_dict() for run in sorted_workflow_runs],
            latest_reports=[report.to_dict() for report in sorted_reports],
            latest_audit_events=[event.to_dict() for event in audit_events],
        )

    def create_connection_profile(
        self,
        workspace_id: str,
        name: str,
        deployment_mode: DeploymentMode,
        endpoint: str,
        database: str,
        username: str,
        verify_ssl: bool = True,
        secret_refs: Optional[Dict[str, Dict[str, str]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConnectionProfile:
        """Create non-secret ArangoDB connection metadata for a workspace."""

        self.repository.get_workspace(workspace_id)
        if not name.strip():
            raise ValidationError("Connection profile name is required")
        if not endpoint.strip():
            raise ValidationError("Connection endpoint is required")
        if not database.strip():
            raise ValidationError("Database name is required")
        if not username.strip():
            raise ValidationError("Database username is required")

        # Reject a profile that targets a cluster/database/user this workspace
        # already has. Two profiles with the same target are indistinguishable
        # in the Assets panel, so discovering against "the wrong one" is silent
        # and unrecoverable — the product has no delete path for connection
        # profiles, so an accidental duplicate is permanent. Name is
        # deliberately NOT part of the identity: a differently-labelled profile
        # pointing at the same place is still the same connection.
        existing = self._find_matching_connection_profile(
            workspace_id=workspace_id,
            endpoint=endpoint.strip(),
            database=database.strip(),
            username=username.strip(),
            deployment_mode=deployment_mode,
        )
        if existing is not None:
            # Describe the profile that already exists, not the raw input —
            # echoing the caller's string reproduced its trailing slash and
            # casing ("arango.ai//IAM"), which reads like a different target.
            raise DuplicateError(
                f"This workspace already has a connection profile for "
                f"{existing.username}@{existing.endpoint.rstrip('/')}"
                f"/{existing.database}: '{existing.name}' "
                f"({existing.connection_profile_id}). "
                f"Use it instead of creating a duplicate."
            )

        profile = create_connection_profile(
            workspace_id=workspace_id,
            name=name.strip(),
            deployment_mode=deployment_mode,
            endpoint=endpoint.strip(),
            database=database.strip(),
            username=username.strip(),
            verify_ssl=verify_ssl,
            secret_refs=secret_refs or {},
            metadata=metadata or {},
        )
        self.repository.create_connection_profile(profile)
        return profile

    def _find_matching_connection_profile(
        self,
        workspace_id: str,
        endpoint: str,
        database: str,
        username: str,
        deployment_mode: DeploymentMode,
    ) -> Optional[ConnectionProfile]:
        """Return an existing profile with the same connection target, if any.

        Endpoint comparison is case-insensitive and ignores a trailing slash,
        so ``https://host:8529/`` and ``https://HOST:8529`` are recognised as
        the same cluster. Database and username stay case-sensitive because
        ArangoDB treats them that way.
        """

        def normalized_endpoint(value: str) -> str:
            return value.strip().rstrip("/").lower()

        target_endpoint = normalized_endpoint(endpoint)
        for candidate in self.repository.list_connection_profiles(workspace_id):
            if (
                normalized_endpoint(candidate.endpoint) == target_endpoint
                and candidate.database == database
                and candidate.username == username
                and candidate.deployment_mode is deployment_mode
            ):
                return candidate
        return None

    def delete_connection_profile(
        self,
        connection_profile_id: str,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete a connection profile that nothing depends on.

        Refuses while graph profiles still reference it: those carry the
        discovered schema and are what runs and reports are scoped by, so
        removing the connection underneath them would strand records pointing
        at an id that no longer resolves. The caller is told which profiles
        block the delete so they can remove those first.

        Returns a small summary rather than None so the API has a body to
        report, and audits the removal — deletion is the one operation with no
        surviving record of itself.
        """

        profile = self.repository.get_connection_profile(connection_profile_id)

        blocking = [
            graph_profile
            for graph_profile in self.repository.list_graph_profiles(
                profile.workspace_id
            )
            if graph_profile.connection_profile_id == connection_profile_id
        ]
        if blocking:
            names = ", ".join(
                f"'{item.graph_name or item.graph_profile_id}'" for item in blocking
            )
            raise ConflictError(
                f"Connection profile '{profile.name}' still has "
                f"{len(blocking)} graph profile(s) using it: {names}. "
                f"Delete those first."
            )

        self.repository.delete_document_by_key(
            CONNECTION_PROFILES_COLLECTION, connection_profile_id
        )
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=profile.workspace_id,
                actor=actor or "system",
                action="delete_connection_profile",
                target_type="connection_profile",
                target_id=connection_profile_id,
                details={
                    "name": profile.name,
                    "endpoint": profile.endpoint,
                    "database": profile.database,
                },
            )
        )
        return {
            "connection_profile_id": connection_profile_id,
            "workspace_id": profile.workspace_id,
            "deleted": True,
        }

    def delete_workflow_run(
        self,
        run_id: str,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete a run and the analysis records it produced.

        Replaces a UI action that only hid the row in local state: the run
        reappeared on refresh while the dialog claimed "This is an irreversible
        run action" (NFR-19).

        Cascades rather than refusing on dependents. Reports and executions
        exist *because of* the run and have no meaning without it, and there is
        no way to delete a report on its own — refusing on them would make this
        as unusable as connection-profile deletion was before graph profiles
        could be removed. Report sections and chart specs go with their
        manifest so no orphan rows survive.

        Two absolute refusals:

        * A **published** report (``published_snapshot_id`` set). FR-54 names
          "runs behind a published report" as never removable at any age; a
          published snapshot is a record someone may be relying on externally.
        * A run still ``RUNNING`` or ``QUEUED``. Cancel it first — deleting the
          record out from under an executing supervisor would leave work with
          nothing to report back to.
        """

        run = self.repository.get_workflow_run(run_id)

        if run.status in (WorkflowRunStatus.RUNNING, WorkflowRunStatus.QUEUED):
            raise ConflictError(
                f"Run {run_id} is {run.status.value}. Cancel it before deleting, "
                f"so the executor is not left writing to a record that is gone."
            )

        reports = [
            report
            for report in self.repository.list_report_manifests(run.workspace_id)
            if report.run_id == run_id
        ]
        published = [r for r in reports if r.published_snapshot_id]
        if published:
            titles = ", ".join(f"'{r.title or r.report_id}'" for r in published)
            raise ConflictError(
                f"Run {run_id} produced {len(published)} published report(s): "
                f"{titles}. Published reports are never removed, so this run "
                f"cannot be deleted."
            )

        executions = [
            execution
            for execution in self.repository.list_analysis_executions(run.workspace_id)
            if execution.run_id == run_id
        ]

        # Cascade: sections and charts first, then their manifest, then the
        # executions, then the run itself — so a failure part-way never leaves
        # a manifest pointing at rows that are already gone.
        deleted_sections = 0
        deleted_charts = 0
        for report in reports:
            for section_id in report.section_ids or []:
                if self.repository.delete_document_by_key(
                    REPORT_SECTIONS_COLLECTION, section_id
                ):
                    deleted_sections += 1
            for chart_id in report.chart_ids or []:
                if self.repository.delete_document_by_key(
                    CHART_SPECS_COLLECTION, chart_id
                ):
                    deleted_charts += 1
            self.repository.delete_document_by_key(
                REPORT_MANIFESTS_COLLECTION, report.report_id
            )
        for execution in executions:
            self.repository.delete_document_by_key(
                ANALYSIS_EXECUTIONS_COLLECTION, execution.analysis_execution_id
            )
        self.repository.delete_document_by_key(WORKFLOW_RUNS_COLLECTION, run_id)

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=run.workspace_id,
                actor=actor or "system",
                action="delete_workflow_run",
                target_type="workflow_run",
                target_id=run_id,
                details={
                    "status": getattr(run.status, "value", run.status),
                    "reports_deleted": len(reports),
                    "executions_deleted": len(executions),
                    "sections_deleted": deleted_sections,
                    "charts_deleted": deleted_charts,
                },
            )
        )
        return {
            "run_id": run_id,
            "workspace_id": run.workspace_id,
            "deleted": True,
            "reports_deleted": len(reports),
            "executions_deleted": len(executions),
        }

    def delete_graph_profile(
        self,
        graph_profile_id: str,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete a graph profile that nothing depends on.

        Counterpart to ``delete_connection_profile``, and the reason it was
        needed: connection-profile deletion refuses while a graph profile
        references the connection, so without this a blocked connection could
        never be unblocked.

        Refuses when the profile is the workspace's *active* one — that is a
        deliberate selection, and silently clearing it would leave the
        workspace analysing nothing without saying so; the user sets another
        active first. Also refuses while workflow runs, analysis executions,
        requirement interviews or graph sets reference it, because those
        records are scoped by the profile and would be left pointing at an id
        that no longer resolves. Every blocker is named and counted, so the
        user can see the whole chain rather than discovering it one refusal at
        a time.
        """

        profile = self.repository.get_graph_profile(graph_profile_id)
        workspace = self.repository.get_workspace(profile.workspace_id)

        if workspace.active_graph_profile_id == graph_profile_id:
            raise ConflictError(
                f"Graph profile '{profile.graph_name or graph_profile_id}' is the "
                f"workspace's active profile. Select a different active graph "
                f"profile first, then delete this one."
            )

        blockers: List[str] = []

        run_count = sum(
            1
            for run in self.repository.list_workflow_runs(profile.workspace_id)
            if run.graph_profile_id == graph_profile_id
        )
        if run_count:
            blockers.append(f"{run_count} workflow run(s)")

        execution_count = sum(
            1
            for execution in self.repository.list_analysis_executions(
                profile.workspace_id
            )
            if execution.graph_profile_id == graph_profile_id
        )
        if execution_count:
            blockers.append(f"{execution_count} analysis execution(s)")

        interview_count = sum(
            1
            for interview in self.repository.list_requirement_interviews(
                profile.workspace_id
            )
            if interview.graph_profile_id == graph_profile_id
        )
        if interview_count:
            blockers.append(f"{interview_count} requirements interview(s)")

        graph_set_names = [
            graph_set.name or graph_set.graph_set_id
            for graph_set in self.repository.list_graph_sets(profile.workspace_id)
            if graph_profile_id in (graph_set.graph_profile_ids or [])
        ]
        if graph_set_names:
            blockers.append(
                f"{len(graph_set_names)} graph set(s): "
                + ", ".join(f"'{name}'" for name in graph_set_names)
            )

        if blockers:
            raise ConflictError(
                f"Graph profile '{profile.graph_name or graph_profile_id}' is still "
                f"referenced by {'; '.join(blockers)}. Remove those first."
            )

        self.repository.delete_document_by_key(
            GRAPH_PROFILES_COLLECTION, graph_profile_id
        )
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=profile.workspace_id,
                actor=actor or "system",
                action="delete_graph_profile",
                target_type="graph_profile",
                target_id=graph_profile_id,
                details={
                    "graph_name": profile.graph_name,
                    "connection_profile_id": profile.connection_profile_id,
                },
            )
        )
        return {
            "graph_profile_id": graph_profile_id,
            "workspace_id": profile.workspace_id,
            "deleted": True,
        }

    def check_workspace_health(self, workspace_id: str) -> WorkspaceHealthResult:
        """Check workspace metadata readiness for admin and setup views."""

        self.repository.get_workspace(workspace_id)
        connection_profiles = self.repository.list_connection_profiles(workspace_id)
        graph_profiles = self.repository.list_graph_profiles(workspace_id)
        source_documents = self.repository.list_source_documents(workspace_id)
        requirement_interviews = self.repository.list_requirement_interviews(
            workspace_id
        )
        requirement_versions = self.repository.list_requirement_versions(workspace_id)
        workflow_runs = self.repository.list_workflow_runs(workspace_id)
        reports = self.repository.list_report_manifests(workspace_id)

        counts = {
            "connection_profiles": len(connection_profiles),
            "graph_profiles": len(graph_profiles),
            "source_documents": len(source_documents),
            "requirement_interviews": len(requirement_interviews),
            "requirement_versions": len(requirement_versions),
            "workflow_runs": len(workflow_runs),
            "reports": len(reports),
        }
        issues = self._workspace_health_issues(
            connection_profiles=connection_profiles,
            graph_profiles=graph_profiles,
            requirement_versions=requirement_versions,
            workflow_runs=workflow_runs,
            reports=reports,
        )
        status = "healthy" if not issues else "needs_attention"

        return WorkspaceHealthResult(
            workspace_id=workspace_id,
            status=status,
            counts=counts,
            issues=issues,
        )

    def get_workflow_dag_view(self, run_id: str) -> WorkflowDAGView:
        """Build the run-level operational DAG for visualization."""

        run = self.repository.get_workflow_run(run_id)
        return WorkflowDAGView(
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            status=run.status.value,
            workflow_mode=run.workflow_mode.value,
            nodes=[self._workflow_step_node(step, run) for step in run.steps],
            edges=[self._workflow_edge(edge) for edge in run.dag_edges],
            warnings=run.warnings,
            errors=run.errors,
        )

    def create_workflow_run_from_steps(
        self,
        workspace_id: str,
        workflow_mode: WorkflowMode,
        steps: List[WorkflowStep],
        dag_edges: List[WorkflowDAGEdge],
        requirement_version_id: Optional[str] = None,
        graph_profile_id: Optional[str] = None,
        template_ids: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRun:
        """Create a visualizable workflow run from planned steps and edges.

        FR-31a: when ``workflow_mode`` is ``AGENTIC``, any client-supplied
        steps and edges are ignored and replaced with the canonical
        six-step layout (schema_analysis → ... → reporting). This is
        the design decision locked in PRD v0.4: the visualizer must
        reflect what the runner actually does, not free-form labels
        a user typed. Traditional mode is unchanged — labels remain
        free-form there.
        """

        if self.is_supervised_agentic_mode(workflow_mode):
            # The substitution below is intentional, but it is *silent*, and
            # that has bitten callers: a seeder built six fully-populated
            # COMPLETED steps, passed them here, then stamped only the run as
            # completed — so the canonical replacements stayed `pending` and
            # the UI rendered a finished run whose every step read "pending".
            # Warn loudly so the next caller discovers the discard from a log
            # line instead of from a self-contradictory screen.
            if steps or dag_edges:
                logger.warning(
                    "create_workflow_run_from_steps: discarding %d caller-supplied "
                    "step(s) and %d edge(s) for %s mode — FR-31a substitutes the "
                    "canonical agentic DAG, whose steps start as PENDING. Stamp "
                    "status/timings on the RETURNED run.steps, not on the steps "
                    "passed in.",
                    len(steps),
                    len(dag_edges),
                    workflow_mode.value,
                )
            steps, dag_edges = self._build_canonical_agentic_dag(
                parallel=workflow_mode is WorkflowMode.PARALLEL_AGENTIC
            )

        self._validate_workflow_dag(steps, dag_edges)
        # Stamp executor_kind on agentic runs so we can distinguish
        # rows produced by the in-process supervisor from rows
        # produced by future durable executors (FR-31b).
        run_metadata = dict(metadata or {})
        if self.is_supervised_agentic_mode(workflow_mode):
            execution_meta = dict(run_metadata.get("execution") or {})
            execution_meta.setdefault("executor_kind", "inprocess")
            execution_meta.setdefault("last_outcome", "pending")
            run_metadata["execution"] = execution_meta

        run = create_workflow_run(
            workspace_id=workspace_id,
            workflow_mode=workflow_mode,
            status=WorkflowRunStatus.QUEUED,
            requirement_version_id=requirement_version_id,
            graph_profile_id=graph_profile_id,
            template_ids=template_ids or [],
            steps=steps,
            dag_edges=dag_edges,
            metadata=run_metadata,
        )
        self.repository.create_workflow_run(run)
        return run

    def quick_analysis(
        self,
        workspace_id: str,
        graph_profile_id: str,
        prompt: str,
        workflow_mode: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        actor: Optional[str] = None,
    ) -> WorkflowRun:
        """FR-73 one-shot prompt analysis.

        Runs the agentic pipeline once from a single natural-language
        prompt against a graph profile — with no manual requirement,
        use-case, or template approval steps. The prompt is persisted as
        an *ephemeral*, auto-approved ``RequirementVersion`` (carried in
        ``metadata.draft_brd``), which the ``AgenticRunSupervisor`` feeds
        to the runner as an in-memory document. The run is created in the
        canonical agentic layout and immediately started; the returned
        ``WorkflowRun`` is ``RUNNING`` once dispatched. Status is then
        observed via ``get_workflow_run_status`` / the run DAG.

        Artifacts created here are tagged ``origin="quick_prompt"`` and
        ``ephemeral=True`` so the UI can keep them out of the curated
        Requirements asset list (FR-17).
        """

        text = (prompt or "").strip()
        if not text:
            raise ValidationError("quick_analysis requires a non-empty prompt")

        # Scope validation: workspace exists and the graph profile is its own.
        self.repository.get_workspace(workspace_id)
        graph_profile = self.repository.get_graph_profile(graph_profile_id)
        if graph_profile.workspace_id != workspace_id:
            raise ValidationError(
                "graph_profile_id does not belong to the given workspace"
            )

        requested_mode = workflow_mode or "agentic"
        if isinstance(requested_mode, WorkflowMode):
            requested_mode = requested_mode.value
        if str(requested_mode) not in ("agentic", "parallel_agentic"):
            raise ValidationError(
                "quick_analysis workflow_mode must be 'agentic' or "
                "'parallel_agentic'"
            )

        # Ephemeral, auto-approved requirement version carrying the prompt
        # as the BRD draft. We do NOT supersede prior approved versions
        # (unlike the copilot approve path) — quick-analysis runs are
        # one-offs and must not disturb the curated requirement history.
        existing = self.repository.list_requirement_versions(workspace_id)
        next_version = max((v.version for v in existing), default=0) + 1
        requirement_version = create_requirement_version(
            workspace_id=workspace_id,
            version=next_version,
            status=RequirementVersionStatus.APPROVED,
            summary=text[:280],
            approved_at=current_timestamp(),
            metadata={
                "source": "quick_prompt",
                "origin": "quick_prompt",
                "ephemeral": True,
                "draft_brd": text,
            },
        )
        self.repository.create_requirement_version(requirement_version)

        # FR-34: parallel_agentic is now dispatched as itself — the supervisor
        # runs the async orchestrator path and the DAG shows the concurrent
        # phase-1 branch.
        run = self.create_workflow_run_from_steps(
            workspace_id=workspace_id,
            workflow_mode=(
                WorkflowMode.PARALLEL_AGENTIC
                if str(requested_mode) == "parallel_agentic"
                else WorkflowMode.AGENTIC
            ),
            steps=[],
            dag_edges=[],
            requirement_version_id=requirement_version.requirement_version_id,
            graph_profile_id=graph_profile_id,
            metadata={
                "origin": "quick_prompt",
                "ephemeral": True,
                "prompt": text[:1000],
                "requested_mode": str(requested_mode),
                "options": dict(options or {}),
            },
        )

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "workspace-ui",
                action="quick_analysis",
                target_type="workflow_run",
                target_id=run.run_id,
                metadata={
                    "graph_profile_id": graph_profile_id,
                    "requirement_version_id": (
                        requirement_version.requirement_version_id
                    ),
                    "requested_mode": str(requested_mode),
                },
            )
        )

        return self.start_workflow_run(run.run_id, actor=actor)

    @staticmethod
    def is_supervised_agentic_mode(workflow_mode: "WorkflowMode") -> bool:
        """Whether this mode is executed by the AgenticRunSupervisor.

        Both AGENTIC and PARALLEL_AGENTIC are supervisor-driven; they differ
        only in whether the runner walks its phases sequentially or through
        ``run_async``/``_run_parallel_workflow`` (FR-34).
        """

        return workflow_mode in (WorkflowMode.AGENTIC, WorkflowMode.PARALLEL_AGENTIC)

    def _build_canonical_agentic_dag(self, parallel: bool = False):
        """Seed the canonical agentic step layout.

        Lazily imports the supervisor module to avoid a circular import (the
        supervisor imports product.models).

        FR-34: when ``parallel`` is set the edge list reflects what
        ``OrchestratorAgent._run_parallel_workflow`` actually does — schema
        analysis and requirements extraction are concurrent roots that both
        feed use-case generation, rather than a straight chain. Everything
        downstream stays sequential because those phases genuinely depend on
        their predecessor's output.
        """

        from .agentic_run_supervisor import AGENTIC_STEP_LAYOUT

        steps = [
            WorkflowStep(step_id=canonical.step_id, label=canonical.label)
            for canonical in AGENTIC_STEP_LAYOUT
        ]

        if not parallel:
            edges: List[WorkflowDAGEdge] = []
            for previous, current in zip(AGENTIC_STEP_LAYOUT, AGENTIC_STEP_LAYOUT[1:]):
                edges.append(
                    WorkflowDAGEdge(
                        from_step_id=previous.step_id,
                        to_step_id=current.step_id,
                    )
                )
            return steps, edges

        # Concurrent phase 1: both roots converge on use_case_generation.
        remaining = [
            canonical.step_id
            for canonical in AGENTIC_STEP_LAYOUT
            if canonical.step_id not in ("schema_analysis", "requirements_extraction")
        ]
        edges = [
            WorkflowDAGEdge(
                from_step_id="schema_analysis",
                to_step_id="use_case_generation",
                label="parallel",
            ),
            WorkflowDAGEdge(
                from_step_id="requirements_extraction",
                to_step_id="use_case_generation",
                label="parallel",
            ),
        ]
        for previous, current in zip(remaining, remaining[1:]):
            edges.append(WorkflowDAGEdge(from_step_id=previous, to_step_id=current))
        return steps, edges

    def start_workflow_run(
        self, run_id: str, actor: Optional[str] = None
    ) -> WorkflowRun:
        """Mark a queued workflow run as running.

        FR-31a: when the run is in AGENTIC mode and a supervisor is
        wired up, also dispatch the run to the supervisor so the real
        agent pipeline executes in the background. The HTTP request
        returns immediately after flipping status to RUNNING — actual
        completion is reflected via per-step updates streamed by the
        :class:`StepStatusReporter`.

        Always emits a ``start_workflow_run`` audit event. For agentic
        runs the metadata records whether the supervisor accepted the
        submission, so audit logs can later distinguish "started but
        the supervisor wasn't wired" from "started and dispatched."
        """

        run = self.repository.get_workflow_run(run_id)
        run.status = WorkflowRunStatus.RUNNING
        run.started_at = run.started_at or current_timestamp()
        platform_user = acting_user()
        if platform_user:
            # NFR-24: a background run acts as the person who started it.
            run.metadata = {**(run.metadata or {}), STARTED_BY_KEY: platform_user}
        self.repository.update_workflow_run(run)

        dispatched = False
        if (
            self.is_supervised_agentic_mode(run.workflow_mode)
            and self._agentic_run_supervisor is not None
        ):
            # Submit to the supervisor. submit() is idempotent so a
            # double-start (e.g. user clicks twice) is safe.
            self._agentic_run_supervisor.submit(run_id)
            dispatched = True

        execution_meta = (run.metadata or {}).get("execution") or {}
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=run.workspace_id,
                actor=actor or "workspace-ui",
                action="start_workflow_run",
                target_type="workflow_run",
                target_id=run.run_id,
                metadata={
                    "workflow_mode": run.workflow_mode.value,
                    "dispatched_to_supervisor": dispatched,
                    "executor_kind": execution_meta.get("executor_kind"),
                    "platform_user": platform_user,
                },
            )
        )

        return run

    def cancel_workflow_run(
        self, run_id: str, actor: Optional[str] = None
    ) -> WorkflowRun:
        """Request cooperative cancellation of an agentic run.

        Phase 1 semantics:
        * If a supervisor is wired and owns the run, the cancel signal
          is delivered immediately. The orchestrator polls between
          steps; the run will transition to ``cancelled`` once the
          current step finishes.
        * If no supervisor is wired (or the run is unknown to it,
          e.g. after an API restart), the run is flipped to
          ``cancelled`` synchronously so the visualizer doesn't keep
          showing a perpetual RUNNING.
        * Always emits an audit event so cancellations are recorded
          alongside other workspace state changes.
        """

        run = self.repository.get_workflow_run(run_id)

        delivered_to_supervisor = False
        if self._agentic_run_supervisor is not None:
            try:
                delivered_to_supervisor = bool(
                    self._agentic_run_supervisor.cancel(run_id)
                )
            except Exception:  # noqa: BLE001
                delivered_to_supervisor = False

        if not delivered_to_supervisor:
            # Synchronous fallback. The supervisor either doesn't own
            # the run or doesn't exist — in either case we don't want
            # to leave the row in RUNNING.
            run.status = WorkflowRunStatus.CANCELLED
            run.completed_at = current_timestamp()
            run.metadata = dict(run.metadata or {})
            execution_meta = dict(run.metadata.get("execution") or {})
            execution_meta["last_outcome"] = "cancelled"
            execution_meta["cancel_path"] = "synchronous"
            run.metadata["execution"] = execution_meta
            self.repository.update_workflow_run(run)

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=run.workspace_id,
                actor=actor or "workflow-runner",
                action="cancel_workflow_run",
                target_type="workflow_run",
                target_id=run.run_id,
                metadata={"delivered_to_supervisor": delivered_to_supervisor},
            )
        )

        return self.repository.get_workflow_run(run_id)

    def get_workflow_run_status(self, run_id: str) -> Dict[str, Any]:
        """Return a concise execution-status snapshot for a run.

        Combines the persisted run.status with supervisor-side
        execution metadata so the UI can poll a single small endpoint
        for cancel results, orphan-sweep outcomes, and live-run
        outcome strings without re-fetching the whole DAG.
        """

        run = self.repository.get_workflow_run(run_id)
        execution_meta = (run.metadata or {}).get("execution") or {}

        supervisor_status: Dict[str, Any]
        if self._agentic_run_supervisor is not None:
            try:
                supervisor_status = dict(
                    self._agentic_run_supervisor.get_status(run_id)
                )
            except Exception:  # noqa: BLE001
                supervisor_status = {"supervised": False}
        else:
            supervisor_status = {"supervised": False}

        return {
            "run_id": run.run_id,
            "workspace_id": run.workspace_id,
            "workflow_mode": run.workflow_mode.value,
            "status": run.status.value,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": (
                run.completed_at.isoformat() if run.completed_at else None
            ),
            "executor_kind": execution_meta.get("executor_kind"),
            "last_outcome": execution_meta.get("last_outcome"),
            "errors": list(run.errors or []),
            "supervisor": supervisor_status,
        }

    # --- Retention (FR-54) ---

    RETENTION_CATEGORIES = (
        "drafts",
        "runs",
        "documents",
        "report_snapshots",
        "audit_logs",
    )

    def get_retention_policy(self, workspace_id: str) -> Dict[str, Any]:
        """Return the workspace's retention policy, defaulted when unset.

        An unset workspace reports ``enabled: false`` with zero windows —
        "keep everything" — so callers never have to distinguish "no policy"
        from "policy that deletes nothing".
        """

        self.repository.get_workspace(workspace_id)
        policy = self.repository.get_retention_policy(workspace_id)
        if policy is None:
            return {
                "workspace_id": workspace_id,
                "enabled": False,
                "draft_retention_days": 0,
                "run_retention_days": 0,
                "document_retention_days": 0,
                "report_snapshot_retention_days": 0,
                "audit_log_retention_days": 0,
                "configured": False,
            }
        result = policy.to_dict()
        result["configured"] = True
        return result

    def set_retention_policy(
        self,
        workspace_id: str,
        enabled: Optional[bool] = None,
        draft_retention_days: Optional[int] = None,
        run_retention_days: Optional[int] = None,
        document_retention_days: Optional[int] = None,
        report_snapshot_retention_days: Optional[int] = None,
        audit_log_retention_days: Optional[int] = None,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Configure retention windows for a workspace (FR-54).

        Windows are whole days; ``0`` means keep forever. Negative values are
        rejected rather than clamped — a negative window most likely means a
        caller computed it wrong, and silently treating it as "delete
        everything" would be destructive.
        """

        self.repository.get_workspace(workspace_id)
        windows = {
            "draft_retention_days": draft_retention_days,
            "run_retention_days": run_retention_days,
            "document_retention_days": document_retention_days,
            "report_snapshot_retention_days": report_snapshot_retention_days,
            "audit_log_retention_days": audit_log_retention_days,
        }
        for name, value in windows.items():
            if value is not None and int(value) < 0:
                raise ValidationError(f"{name} must be >= 0 (0 means keep forever)")

        policy = self.repository.get_retention_policy(workspace_id)
        created = policy is None
        if policy is None:
            policy = create_retention_policy(workspace_id=workspace_id)

        if enabled is not None:
            policy.enabled = bool(enabled)
        for name, value in windows.items():
            if value is not None:
                setattr(policy, name, int(value))
        policy.updated_by = actor
        policy.updated_at = current_timestamp()

        if created:
            self.repository.create_retention_policy(policy)
        else:
            self.repository.update_retention_policy(policy)

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="set_retention_policy",
                target_type="workspace",
                target_id=workspace_id,
                details={
                    "enabled": policy.enabled,
                    **{name: getattr(policy, name) for name in windows},
                },
            )
        )
        result = policy.to_dict()
        result["configured"] = True
        return result

    def apply_retention_policy(
        self,
        workspace_id: str,
        dry_run: bool = True,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sweep expired records for a workspace (FR-54).

        **Dry run by default.** This deletes data, so a caller must pass
        ``dry_run=False`` explicitly; the default returns exactly what *would*
        be removed. Returns the same shape either way, with ``deleted`` telling
        the caller which mode ran.

        Deliberately excluded from every category, regardless of age, because
        these are the records an audit would ask for:

        * APPROVED requirement versions (only DRAFT / REJECTED are eligible)
        * published report snapshots and any report manifest that has one
        * runs that produced a published report
        * audit events, unless ``audit_log_retention_days`` is set explicitly —
          they are the record of everything else being deleted

        Ephemeral quick-analysis runs (``metadata.ephemeral``) are swept under
        the ``runs`` window, which is the concrete behaviour the PRD names.
        """

        from datetime import timedelta

        self.repository.get_workspace(workspace_id)
        policy = self.repository.get_retention_policy(workspace_id)
        now = current_timestamp()

        result: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "deleted": not dry_run,
            "enabled": bool(policy and policy.enabled),
            "candidates": {category: [] for category in self.RETENTION_CATEGORIES},
            "protected": {},
            # Always present so callers get one response shape regardless of
            # whether a policy exists, is disabled, or actually swept.
            "counts": {category: 0 for category in self.RETENTION_CATEGORIES},
        }
        if policy is None or not policy.enabled:
            result["reason"] = (
                "No retention policy configured"
                if policy is None
                else "Retention policy is disabled"
            )
            return result

        def _expired(timestamp, days: int) -> bool:
            if not days or timestamp is None:
                return False
            return timestamp < now - timedelta(days=days)

        protected_report_ids = set()
        protected_run_ids = set()
        for manifest in self.repository.list_report_manifests(workspace_id):
            if manifest.published_snapshot_id:
                protected_report_ids.add(manifest.report_id)
                if manifest.run_id:
                    protected_run_ids.add(manifest.run_id)

        # Drafts: only unapproved requirement versions.
        draft_days = policy.draft_retention_days
        for version in self.repository.list_requirement_versions(workspace_id):
            if version.status in (
                RequirementVersionStatus.APPROVED,
                RequirementVersionStatus.SUPERSEDED,
            ):
                continue
            if _expired(version.created_at, draft_days):
                result["candidates"]["drafts"].append(
                    {
                        "id": version.requirement_version_id,
                        "collection": REQUIREMENT_VERSIONS_COLLECTION,
                        "label": f"v{version.version} ({version.status.value})",
                    }
                )

        # Runs: skip anything that produced a published report.
        run_days = policy.run_retention_days
        for run in self.repository.list_workflow_runs(workspace_id):
            if run.run_id in protected_run_ids:
                continue
            if _expired(run.created_at, run_days):
                result["candidates"]["runs"].append(
                    {
                        "id": run.run_id,
                        "collection": WORKFLOW_RUNS_COLLECTION,
                        "label": run.workflow_mode.value,
                        "ephemeral": bool((run.metadata or {}).get("ephemeral")),
                    }
                )

        document_days = policy.document_retention_days
        for document in self.repository.list_source_documents(workspace_id):
            if _expired(document.uploaded_at, document_days):
                result["candidates"]["documents"].append(
                    {
                        "id": document.document_id,
                        "collection": DOCUMENTS_COLLECTION,
                        "label": document.filename,
                    }
                )

        snapshot_days = policy.report_snapshot_retention_days
        for manifest in self.repository.list_report_manifests(workspace_id):
            if manifest.report_id in protected_report_ids:
                continue
            if _expired(manifest.created_at, snapshot_days):
                result["candidates"]["report_snapshots"].append(
                    {
                        "id": manifest.report_id,
                        "collection": REPORT_MANIFESTS_COLLECTION,
                        "label": manifest.title,
                    }
                )

        audit_days = policy.audit_log_retention_days
        if audit_days:
            for event in self.repository.list_audit_events(workspace_id, limit=10_000):
                if _expired(event.timestamp, audit_days):
                    result["candidates"]["audit_logs"].append(
                        {
                            "id": event.audit_event_id,
                            "collection": AUDIT_EVENTS_COLLECTION,
                            "label": event.action,
                        }
                    )

        result["protected"] = {
            "published_report_ids": sorted(protected_report_ids),
            "runs_with_published_reports": sorted(protected_run_ids),
        }
        result["counts"] = {
            category: len(items) for category, items in result["candidates"].items()
        }

        if dry_run:
            return result

        removed = 0
        for items in result["candidates"].values():
            for item in items:
                if self.repository.delete_document_by_key(
                    item["collection"], item["id"]
                ):
                    removed += 1
        result["removed"] = removed

        policy.last_applied_at = now
        self.repository.update_retention_policy(policy)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="apply_retention_policy",
                target_type="workspace",
                target_id=workspace_id,
                details={"removed": removed, "counts": result["counts"]},
            )
        )
        return result

    # --- Use cases (FR-19..FR-21) ---

    USE_CASE_TYPES = (
        "centrality",
        "community",
        "pathfinding",
        "pattern",
        "anomaly",
        "recommendation",
        "similarity",
    )
    USE_CASE_PRIORITIES = ("critical", "high", "medium", "low", "unknown")

    def create_use_case(
        self,
        workspace_id: str,
        title: str,
        description: str = "",
        use_case_type: str = "pattern",
        priority: str = "medium",
        requirement_version_id: Optional[str] = None,
        related_requirements: Optional[List[str]] = None,
        graph_algorithms: Optional[List[str]] = None,
        data_needs: Optional[List[str]] = None,
        expected_outputs: Optional[List[str]] = None,
        success_metrics: Optional[List[str]] = None,
        origin: str = "manual",
        actor: Optional[str] = None,
    ) -> UseCase:
        """Author a use case by hand (FR-19).

        Created as a DRAFT — approval is a separate, audited decision
        (FR-20). ``origin`` distinguishes user-authored rows from ones an
        agentic run generated, so provenance is visible in the UI.
        """

        if not title.strip():
            raise ValidationError("Use case title is required")
        self._validate_use_case_type(use_case_type)
        self._validate_use_case_priority(priority)
        if origin not in {item.value for item in UseCaseOrigin}:
            raise ValidationError(
                f"origin must be one of {[i.value for i in UseCaseOrigin]}"
            )
        self.repository.get_workspace(workspace_id)

        if requirement_version_id:
            version = self.repository.get_requirement_version(requirement_version_id)
            if version.workspace_id != workspace_id:
                raise ValidationError(
                    "requirement_version_id must belong to this workspace"
                )

        use_case = create_use_case(
            workspace_id=workspace_id,
            title=title.strip(),
            description=description.strip(),
            use_case_type=use_case_type,
            priority=priority,
            origin=UseCaseOrigin(origin),
            requirement_version_id=requirement_version_id,
            related_requirements=list(related_requirements or []),
            graph_algorithms=list(graph_algorithms or []),
            data_needs=list(data_needs or []),
            expected_outputs=list(expected_outputs or []),
            success_metrics=list(success_metrics or []),
            created_by=actor,
        )
        self.repository.create_use_case(use_case)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="create_use_case",
                target_type="use_case",
                target_id=use_case.use_case_id,
                details={"title": use_case.title, "origin": origin},
            )
        )
        return use_case

    def update_use_case(
        self,
        use_case_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        use_case_type: Optional[str] = None,
        priority: Optional[str] = None,
        related_requirements: Optional[List[str]] = None,
        graph_algorithms: Optional[List[str]] = None,
        data_needs: Optional[List[str]] = None,
        expected_outputs: Optional[List[str]] = None,
        success_metrics: Optional[List[str]] = None,
        actor: Optional[str] = None,
    ) -> UseCase:
        """Edit a draft use case (FR-19).

        Only DRAFT and REJECTED rows are editable. An APPROVED use case is
        the input to template generation, so silently mutating one would
        change what downstream templates claim to be derived from; a
        rejected one can be revised and re-submitted.
        """

        use_case = self.repository.get_use_case(use_case_id)
        if use_case.status in {UseCaseStatus.APPROVED, UseCaseStatus.ARCHIVED}:
            raise ConflictError(
                f"Use case {use_case_id} is {use_case.status.value} and cannot be "
                "edited. Approved use cases are inputs to template generation; "
                "archive and clone instead."
            )

        changes: Dict[str, Any] = {}
        if title is not None:
            stripped = title.strip()
            if not stripped:
                raise ValidationError("Use case title cannot be empty")
            if stripped != use_case.title:
                changes["title"] = {"from": use_case.title, "to": stripped}
                use_case.title = stripped
        if description is not None and description.strip() != use_case.description:
            changes["description"] = True
            use_case.description = description.strip()
        if use_case_type is not None and use_case_type != use_case.use_case_type:
            self._validate_use_case_type(use_case_type)
            changes["use_case_type"] = {
                "from": use_case.use_case_type,
                "to": use_case_type,
            }
            use_case.use_case_type = use_case_type
        if priority is not None and priority != use_case.priority:
            self._validate_use_case_priority(priority)
            changes["priority"] = {"from": use_case.priority, "to": priority}
            use_case.priority = priority

        for field_name, value in (
            ("related_requirements", related_requirements),
            ("graph_algorithms", graph_algorithms),
            ("data_needs", data_needs),
            ("expected_outputs", expected_outputs),
            ("success_metrics", success_metrics),
        ):
            if value is not None and list(value) != getattr(use_case, field_name):
                changes[field_name] = {"count": len(value)}
                setattr(use_case, field_name, list(value))

        if not changes:
            return use_case

        use_case.updated_at = current_timestamp()
        self.repository.update_use_case(use_case)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=use_case.workspace_id,
                actor=actor or "system",
                action="update_use_case",
                target_type="use_case",
                target_id=use_case.use_case_id,
                details={"changes": changes},
            )
        )
        return use_case

    def set_use_case_status(
        self,
        use_case_id: str,
        status: str,
        review_note: str = "",
        actor: Optional[str] = None,
    ) -> UseCase:
        """Approve, reject, or archive a use case (FR-20).

        Prioritisation is a separate concern handled by
        :meth:`update_use_case` (draft) and
        :meth:`set_use_case_priority` (any non-archived state), because a
        reviewer often needs to re-rank an already-approved backlog without
        reopening it for edits.

        ARCHIVED is terminal: an archived row is history, and re-approving
        it would silently resurrect a use case that downstream templates may
        already have been retired against.
        """

        try:
            target = UseCaseStatus(status)
        except ValueError:
            raise ValidationError(
                f"status must be one of {[s.value for s in UseCaseStatus]}"
            ) from None

        use_case = self.repository.get_use_case(use_case_id)
        if use_case.status is UseCaseStatus.ARCHIVED:
            raise ConflictError(
                f"Use case {use_case_id} is archived; archived use cases are "
                "terminal and cannot be re-opened."
            )
        if use_case.status is target:
            return use_case

        previous = use_case.status.value
        use_case.status = target
        use_case.reviewed_by = actor
        use_case.reviewed_at = current_timestamp()
        use_case.review_note = review_note.strip()
        use_case.updated_at = current_timestamp()
        self.repository.update_use_case(use_case)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=use_case.workspace_id,
                actor=actor or "system",
                action=f"{target.value}_use_case",
                target_type="use_case",
                target_id=use_case.use_case_id,
                details={"from": previous, "to": target.value, "note": review_note},
            )
        )
        return use_case

    def set_use_case_priority(
        self,
        use_case_id: str,
        priority: str,
        actor: Optional[str] = None,
    ) -> UseCase:
        """Re-prioritise a use case at any non-archived status (FR-20)."""

        self._validate_use_case_priority(priority)
        use_case = self.repository.get_use_case(use_case_id)
        if use_case.status is UseCaseStatus.ARCHIVED:
            raise ConflictError("Archived use cases cannot be re-prioritised")
        if use_case.priority == priority:
            return use_case

        previous = use_case.priority
        use_case.priority = priority
        use_case.updated_at = current_timestamp()
        self.repository.update_use_case(use_case)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=use_case.workspace_id,
                actor=actor or "system",
                action="prioritize_use_case",
                target_type="use_case",
                target_id=use_case.use_case_id,
                details={"from": previous, "to": priority},
            )
        )
        return use_case

    def get_use_case(self, use_case_id: str) -> UseCase:
        """Get a single use case."""

        return self.repository.get_use_case(use_case_id)

    def list_use_cases(
        self,
        workspace_id: str,
        status: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> List[UseCase]:
        """List a workspace's use cases, optionally filtered (FR-45)."""

        self.repository.get_workspace(workspace_id)
        rows = self.repository.list_use_cases(workspace_id)
        if status:
            normalized = UseCaseStatus(status)
            rows = [row for row in rows if row.status is normalized]
        if priority:
            rows = [row for row in rows if row.priority == priority]
        return rows

    def _validate_use_case_type(self, use_case_type: str) -> None:
        if use_case_type not in self.USE_CASE_TYPES:
            raise ValidationError(
                f"use_case_type must be one of {list(self.USE_CASE_TYPES)}, "
                f"got {use_case_type!r}"
            )

    def _validate_use_case_priority(self, priority: str) -> None:
        if priority not in self.USE_CASE_PRIORITIES:
            raise ValidationError(
                f"priority must be one of {list(self.USE_CASE_PRIORITIES)}, "
                f"got {priority!r}"
            )

    # --- Analysis templates (FR-22..FR-26) ---

    # FR-26: the import path reads ONLY these keys off an incoming dict and
    # ignores everything else. Nothing is eval'd, exec'd, imported by name, or
    # instantiated from a caller-supplied type — an imported template is inert
    # data until a user approves and runs it.
    IMPORTABLE_TEMPLATE_FIELDS = (
        "name",
        "description",
        "algorithm",
        "parameters",
        "config",
        "estimated_runtime_seconds",
    )

    def create_analysis_template(
        self,
        workspace_id: str,
        name: str,
        algorithm: str,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        use_case_id: Optional[str] = None,
        estimated_runtime_seconds: Optional[float] = None,
        actor: Optional[str] = None,
    ) -> AnalysisTemplate:
        """Create a draft analysis template (FR-22)."""

        if not name.strip():
            raise ValidationError("Template name is required")
        if not algorithm.strip():
            raise ValidationError("Template algorithm is required")
        self.repository.get_workspace(workspace_id)

        if use_case_id:
            use_case = self.repository.get_use_case(use_case_id)
            if use_case.workspace_id != workspace_id:
                raise ValidationError("use_case_id must belong to this workspace")

        template = create_analysis_template(
            workspace_id=workspace_id,
            name=name.strip(),
            algorithm=algorithm.strip(),
            description=description.strip(),
            parameters=dict(parameters or {}),
            config=dict(config or {}),
            use_case_id=use_case_id,
            estimated_runtime_seconds=estimated_runtime_seconds,
            created_by=actor,
        )
        self.repository.create_analysis_template(template)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="create_analysis_template",
                target_type="analysis_template",
                target_id=template.analysis_template_id,
                details={"name": template.name, "algorithm": template.algorithm},
            )
        )
        return template

    def update_analysis_template(
        self,
        analysis_template_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        algorithm: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        estimated_runtime_seconds: Optional[float] = None,
        actor: Optional[str] = None,
    ) -> AnalysisTemplate:
        """Edit algorithm parameters and config (FR-23), honouring FR-25.

        A DRAFT template is edited in place. An APPROVED template is
        immutable, so editing one instead creates the next version in the
        same lineage and flips the original to SUPERSEDED — any completed
        run still resolves the exact template row it executed. The returned
        template is whichever row the caller should now be looking at.
        """

        template = self.repository.get_analysis_template(analysis_template_id)
        if template.status in {
            AnalysisTemplateStatus.SUPERSEDED,
            AnalysisTemplateStatus.ARCHIVED,
        }:
            raise ConflictError(
                f"Template {analysis_template_id} is {template.status.value} and "
                "cannot be edited; edit its current version instead."
            )

        patch: Dict[str, Any] = {}
        if name is not None and name.strip() != template.name:
            if not name.strip():
                raise ValidationError("Template name cannot be empty")
            patch["name"] = name.strip()
        if description is not None and description.strip() != template.description:
            patch["description"] = description.strip()
        if algorithm is not None and algorithm.strip() != template.algorithm:
            if not algorithm.strip():
                raise ValidationError("Template algorithm cannot be empty")
            patch["algorithm"] = algorithm.strip()
        if parameters is not None and dict(parameters) != template.parameters:
            patch["parameters"] = dict(parameters)
        if config is not None and dict(config) != template.config:
            patch["config"] = dict(config)
        if (
            estimated_runtime_seconds is not None
            and estimated_runtime_seconds != template.estimated_runtime_seconds
        ):
            patch["estimated_runtime_seconds"] = estimated_runtime_seconds

        if not patch:
            return template

        if template.status is AnalysisTemplateStatus.DRAFT:
            for key, value in patch.items():
                setattr(template, key, value)
            template.updated_at = current_timestamp()
            self.repository.update_analysis_template(template)
            self.repository.create_audit_event(
                create_audit_event(
                    workspace_id=template.workspace_id,
                    actor=actor or "system",
                    action="update_analysis_template",
                    target_type="analysis_template",
                    target_id=template.analysis_template_id,
                    details={"changed_fields": sorted(patch.keys())},
                )
            )
            return template

        # APPROVED -> new version in the same lineage (FR-25).
        successor = create_analysis_template(
            workspace_id=template.workspace_id,
            name=patch.get("name", template.name),
            lineage_id=template.lineage_id,
            description=patch.get("description", template.description),
            algorithm=patch.get("algorithm", template.algorithm),
            parameters=patch.get("parameters", dict(template.parameters)),
            config=patch.get("config", dict(template.config)),
            version=template.version + 1,
            status=AnalysisTemplateStatus.DRAFT,
            use_case_id=template.use_case_id,
            estimated_runtime_seconds=patch.get(
                "estimated_runtime_seconds", template.estimated_runtime_seconds
            ),
            created_by=actor,
            metadata={"supersedes": template.analysis_template_id},
        )
        self.repository.create_analysis_template(successor)

        template.status = AnalysisTemplateStatus.SUPERSEDED
        template.superseded_by = successor.analysis_template_id
        template.updated_at = current_timestamp()
        self.repository.update_analysis_template(template)

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=template.workspace_id,
                actor=actor or "system",
                action="version_analysis_template",
                target_type="analysis_template",
                target_id=successor.analysis_template_id,
                details={
                    "supersedes": template.analysis_template_id,
                    "version": successor.version,
                    "changed_fields": sorted(patch.keys()),
                },
            )
        )
        return successor

    def approve_analysis_template(
        self,
        analysis_template_id: str,
        actor: Optional[str] = None,
    ) -> AnalysisTemplate:
        """Approve a draft template, making it immutable (FR-25)."""

        template = self.repository.get_analysis_template(analysis_template_id)
        if template.status is not AnalysisTemplateStatus.DRAFT:
            raise ConflictError(
                f"Only draft templates can be approved; {analysis_template_id} is "
                f"{template.status.value}."
            )

        template.status = AnalysisTemplateStatus.APPROVED
        template.approved_by = actor
        template.approved_at = current_timestamp()
        template.updated_at = current_timestamp()
        self.repository.update_analysis_template(template)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=template.workspace_id,
                actor=actor or "system",
                action="approve_analysis_template",
                target_type="analysis_template",
                target_id=template.analysis_template_id,
                details={"version": template.version},
            )
        )
        return template

    def import_analysis_templates(
        self,
        workspace_id: str,
        templates: List[Dict[str, Any]],
        actor: Optional[str] = None,
    ) -> List[AnalysisTemplate]:
        """Import template dictionaries without executing anything (FR-26).

        Accepts plain JSON objects and reads only
        :data:`IMPORTABLE_TEMPLATE_FIELDS` off each. Unknown keys are
        ignored rather than rejected, so a dictionary exported from a
        richer project still imports; nothing in the payload can name a
        Python type, module, or callable to construct, so a hostile
        dictionary is inert.

        Every imported row lands as a DRAFT for review — importing is not
        approving.
        """

        if not isinstance(templates, list):
            raise ValidationError("templates must be a list of template objects")
        self.repository.get_workspace(workspace_id)

        imported: List[AnalysisTemplate] = []
        for index, raw in enumerate(templates):
            if not isinstance(raw, dict):
                raise ValidationError(f"templates[{index}] must be an object")

            fields = {
                key: raw[key] for key in self.IMPORTABLE_TEMPLATE_FIELDS if key in raw
            }
            name = str(fields.get("name", "")).strip()
            algorithm = str(fields.get("algorithm", "")).strip()
            if not name:
                raise ValidationError(f"templates[{index}] is missing 'name'")
            if not algorithm:
                raise ValidationError(f"templates[{index}] is missing 'algorithm'")

            parameters = fields.get("parameters") or {}
            config = fields.get("config") or {}
            if not isinstance(parameters, dict):
                raise ValidationError(
                    f"templates[{index}].parameters must be an object"
                )
            if not isinstance(config, dict):
                raise ValidationError(f"templates[{index}].config must be an object")

            runtime = fields.get("estimated_runtime_seconds")
            template = create_analysis_template(
                workspace_id=workspace_id,
                name=name,
                algorithm=algorithm,
                description=str(fields.get("description", "")).strip(),
                parameters=dict(parameters),
                config=dict(config),
                estimated_runtime_seconds=(
                    float(runtime) if isinstance(runtime, (int, float)) else None
                ),
                created_by=actor,
                metadata={
                    "import_source": "template_dictionary",
                    "ignored_keys": sorted(
                        set(raw.keys()) - set(self.IMPORTABLE_TEMPLATE_FIELDS)
                    ),
                },
            )
            self.repository.create_analysis_template(template)
            imported.append(template)

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="import_analysis_templates",
                target_type="workspace",
                target_id=workspace_id,
                details={"imported": len(imported)},
            )
        )
        return imported

    # FR-49 / FR-50: vertical project import.
    #
    # The PRD names two source shapes — "AdTech-style YAML/docs projects" and
    # "clinical trials/CRO and open source intelligence analysis template
    # files" — but no such format exists in this repo or the historical sibling
    # repos it refers to, so there was nothing to parse against. Rather than
    # guess at a third party's schema, this defines ONE documented bundle
    # format with a ``vertical`` discriminator: the two requirements differ in
    # domain vocabulary, not in structure, so a second parser would be
    # duplication. A project exported from any vertical maps onto this shape.
    #
    # See docs/vertical_project_bundle.md for the schema.
    VERTICAL_IMPORT_SECTIONS = ("use_cases", "templates")

    def import_vertical_project(
        self,
        workspace_id: str,
        document: str,
        document_format: str = "yaml",
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import a vertical project bundle (FR-49 / FR-50).

        ``document`` is the raw YAML or JSON text of a bundle. Parsing uses
        ``yaml.safe_load``, never ``yaml.load``: the default PyYAML loader can
        construct arbitrary Python objects from tags like
        ``!!python/object/apply``, which would be exactly the arbitrary code
        execution FR-26/FR-49/FR-50 forbid. YAML is a superset of JSON, so the
        same parser reads both; ``document_format`` only affects the error
        message a malformed file produces.

        Everything lands as DRAFT for review — importing is not approving.
        Templates reference their use case by ``use_case`` title; an
        unresolvable reference imports the template unlinked and is reported in
        ``warnings`` rather than failing the whole bundle, since a partially
        linked import is more useful than none.
        """

        self.repository.get_workspace(workspace_id)

        normalized_format = (document_format or "yaml").lower()
        if normalized_format not in ("yaml", "yml", "json"):
            raise ValidationError("document_format must be 'yaml' or 'json'")

        try:
            import yaml

            bundle = yaml.safe_load(document or "")
        except ImportError as exc:  # pragma: no cover - PyYAML is a dependency
            raise ValidationError(f"YAML support unavailable: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - malformed input is user error
            raise ValidationError(
                f"Could not parse {normalized_format} bundle: {exc}"
            ) from exc

        if not isinstance(bundle, dict):
            raise ValidationError(
                "Bundle must be a mapping with at least a 'use_cases' or "
                "'templates' section"
            )
        if not any(bundle.get(section) for section in self.VERTICAL_IMPORT_SECTIONS):
            raise ValidationError(
                "Bundle contains no 'use_cases' or 'templates' to import"
            )

        vertical = str(bundle.get("vertical") or "unspecified").strip()
        project_name = str(bundle.get("name") or "").strip()
        warnings: List[str] = []

        imported_use_cases: List[UseCase] = []
        use_case_ids_by_title: Dict[str, str] = {}
        for index, raw in enumerate(bundle.get("use_cases") or []):
            if not isinstance(raw, dict):
                warnings.append(f"use_cases[{index}] is not a mapping; skipped")
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                warnings.append(f"use_cases[{index}] has no title; skipped")
                continue

            use_case_type = str(raw.get("type") or "pattern").strip()
            if use_case_type not in self.USE_CASE_TYPES:
                warnings.append(
                    f"use_cases[{index}] has unknown type {use_case_type!r}; "
                    "imported as 'pattern'"
                )
                use_case_type = "pattern"
            priority = str(raw.get("priority") or "medium").strip()
            if priority not in self.USE_CASE_PRIORITIES:
                warnings.append(
                    f"use_cases[{index}] has unknown priority {priority!r}; "
                    "imported as 'medium'"
                )
                priority = "medium"

            use_case = self.create_use_case(
                workspace_id=workspace_id,
                title=title,
                description=str(raw.get("description") or ""),
                use_case_type=use_case_type,
                priority=priority,
                graph_algorithms=[str(item) for item in (raw.get("algorithms") or [])],
                data_needs=[str(item) for item in (raw.get("data_needs") or [])],
                expected_outputs=[
                    str(item) for item in (raw.get("expected_outputs") or [])
                ],
                success_metrics=[
                    str(item) for item in (raw.get("success_metrics") or [])
                ],
                origin="generated",
                actor=actor,
            )
            imported_use_cases.append(use_case)
            use_case_ids_by_title[title.lower()] = use_case.use_case_id

        imported_templates: List[AnalysisTemplate] = []
        for index, raw in enumerate(bundle.get("templates") or []):
            if not isinstance(raw, dict):
                warnings.append(f"templates[{index}] is not a mapping; skipped")
                continue
            name = str(raw.get("name") or "").strip()
            algorithm = str(raw.get("algorithm") or "").strip()
            if not name or not algorithm:
                warnings.append(
                    f"templates[{index}] needs both 'name' and 'algorithm'; skipped"
                )
                continue

            linked_title = str(raw.get("use_case") or "").strip().lower()
            use_case_id = (
                use_case_ids_by_title.get(linked_title) if linked_title else None
            )
            if linked_title and use_case_id is None:
                warnings.append(
                    f"templates[{index}] references unknown use case "
                    f"{raw.get('use_case')!r}; imported unlinked"
                )

            parameters = raw.get("parameters") or {}
            config = raw.get("config") or {}
            if not isinstance(parameters, dict) or not isinstance(config, dict):
                warnings.append(
                    f"templates[{index}] has non-mapping parameters/config; skipped"
                )
                continue

            template = self.create_analysis_template(
                workspace_id=workspace_id,
                name=name,
                algorithm=algorithm,
                description=str(raw.get("description") or ""),
                parameters=dict(parameters),
                config=dict(config),
                use_case_id=use_case_id,
                actor=actor,
            )
            imported_templates.append(template)

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="import_vertical_project",
                target_type="workspace",
                target_id=workspace_id,
                details={
                    "vertical": vertical,
                    "project_name": project_name,
                    "use_cases": len(imported_use_cases),
                    "templates": len(imported_templates),
                    "warnings": len(warnings),
                },
            )
        )

        return {
            "workspace_id": workspace_id,
            "vertical": vertical,
            "project_name": project_name,
            "use_cases": [item.to_dict() for item in imported_use_cases],
            "templates": [item.to_dict() for item in imported_templates],
            "counts": {
                "use_cases": len(imported_use_cases),
                "templates": len(imported_templates),
            },
            "warnings": warnings,
        }

    def get_analysis_template(self, analysis_template_id: str) -> AnalysisTemplate:
        """Get a single analysis template."""

        return self.repository.get_analysis_template(analysis_template_id)

    def list_analysis_templates(
        self,
        workspace_id: str,
        status: Optional[str] = None,
        use_case_id: Optional[str] = None,
        include_superseded: bool = False,
    ) -> List[AnalysisTemplate]:
        """List a workspace's templates (FR-45).

        Superseded versions are hidden by default — the list should show
        the current state of each lineage, not its whole history.
        """

        self.repository.get_workspace(workspace_id)
        rows = self.repository.list_analysis_templates(workspace_id)
        if not include_superseded:
            rows = [
                row
                for row in rows
                if row.status is not AnalysisTemplateStatus.SUPERSEDED
            ]
        if status:
            normalized = AnalysisTemplateStatus(status)
            rows = [row for row in rows if row.status is normalized]
        if use_case_id:
            rows = [row for row in rows if row.use_case_id == use_case_id]
        return rows

    def get_analysis_template_versions(
        self,
        analysis_template_id: str,
    ) -> List[AnalysisTemplate]:
        """Return every version in a template's lineage, oldest first (FR-25)."""

        template = self.repository.get_analysis_template(analysis_template_id)
        rows = [
            row
            for row in self.repository.list_analysis_templates(template.workspace_id)
            if row.lineage_id == template.lineage_id
        ]
        return sorted(rows, key=lambda row: row.version)

    # --- Product Analysis Catalog (FR-31 / FR-45..FR-48) ---

    def create_analysis_epoch(
        self,
        workspace_id: str,
        name: str,
        description: str = "",
        timestamp: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_epoch_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> AnalysisEpoch:
        """Create a workspace-scoped analysis epoch."""

        self.repository.get_workspace(workspace_id)
        if parent_epoch_id:
            parent = self.repository.get_analysis_epoch(parent_epoch_id)
            if parent.workspace_id != workspace_id:
                raise ValidationError(
                    "parent_epoch_id does not belong to the given workspace"
                )

        epoch_timestamp = (
            datetime.fromisoformat(timestamp) if timestamp else current_timestamp()
        )
        epoch = create_analysis_epoch(
            workspace_id=workspace_id,
            name=(name or "").strip(),
            description=(description or "").strip(),
            timestamp=epoch_timestamp,
            tags=[tag.strip() for tag in (tags or []) if tag.strip()],
            parent_epoch_id=parent_epoch_id,
        )
        self.repository.create_analysis_epoch(epoch)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "workspace-ui",
                action="create_analysis_epoch",
                target_type="analysis_epoch",
                target_id=epoch.analysis_epoch_id,
            )
        )
        return epoch

    def get_analysis_epoch(self, analysis_epoch_id: str) -> AnalysisEpoch:
        """Get an analysis epoch."""

        return self.repository.get_analysis_epoch(analysis_epoch_id)

    def list_analysis_epochs(self, workspace_id: str) -> List[AnalysisEpoch]:
        """Browse the epochs in a workspace."""

        self.repository.get_workspace(workspace_id)
        return self.repository.list_analysis_epochs(workspace_id)

    def record_analysis_execution(
        self,
        run_id: str,
        algorithm: str,
        status: AnalysisExecutionStatus = AnalysisExecutionStatus.COMPLETED,
        *,
        template_id: Optional[str] = None,
        template_name: str = "",
        use_case_id: Optional[str] = None,
        epoch_id: Optional[str] = None,
        algorithm_version: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        graph_config: Optional[Dict[str, Any]] = None,
        results_location: Optional[str] = None,
        result_count: int = 0,
        performance_metrics: Optional[Dict[str, Any]] = None,
        result_sample: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        catalog_execution_id: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        actor: Optional[str] = None,
    ) -> AnalysisExecution:
        """Record one completed/failed algorithm execution for a workflow run."""

        run = self.repository.get_workflow_run(run_id)
        if not isinstance(status, AnalysisExecutionStatus):
            status = AnalysisExecutionStatus(status)

        epoch = None
        if epoch_id:
            epoch = self.repository.get_analysis_epoch(epoch_id)
            if epoch.workspace_id != run.workspace_id:
                raise ValidationError("epoch_id does not belong to the run's workspace")

        execution = create_analysis_execution(
            workspace_id=run.workspace_id,
            run_id=run.run_id,
            algorithm=(algorithm or "unknown").strip() or "unknown",
            status=status,
            graph_profile_id=run.graph_profile_id,
            requirement_version_id=run.requirement_version_id,
            use_case_id=use_case_id,
            template_id=template_id,
            template_name=template_name,
            epoch_id=epoch_id,
            algorithm_version=algorithm_version,
            parameters=dict(parameters or {}),
            graph_config=dict(graph_config or {}),
            results_location=results_location,
            result_count=max(0, int(result_count or 0)),
            performance_metrics=dict(performance_metrics or {}),
            result_sample=result_sample,
            error_message=error_message,
            workflow_mode=run.workflow_mode.value,
            catalog_execution_id=catalog_execution_id,
            started_at=started_at or run.started_at or current_timestamp(),
            completed_at=completed_at,
            metadata=dict(metadata or {}),
        )
        self.repository.create_analysis_execution(execution)

        if execution.analysis_execution_id not in run.analysis_execution_ids:
            run.analysis_execution_ids.append(execution.analysis_execution_id)
            self.repository.update_workflow_run(run)

        if epoch is not None:
            if execution.analysis_execution_id not in epoch.analysis_execution_ids:
                epoch.analysis_execution_ids.append(execution.analysis_execution_id)
            epoch.analysis_count = len(epoch.analysis_execution_ids)
            self.repository.update_analysis_epoch(epoch)

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=run.workspace_id,
                actor=actor or "workflow-runner",
                action="record_analysis_execution",
                target_type="analysis_execution",
                target_id=execution.analysis_execution_id,
                metadata={
                    "run_id": run.run_id,
                    "algorithm": execution.algorithm,
                    "status": execution.status.value,
                },
            )
        )
        return execution

    def record_workflow_analysis_executions(
        self, run_id: str, state: Any
    ) -> List[AnalysisExecution]:
        """Mirror an agent runner's execution results into the product catalog.

        The method is idempotent by GAE job ID so a supervisor retry cannot
        duplicate catalog rows.
        """

        run = self.repository.get_workflow_run(run_id)
        existing = self.repository.list_analysis_executions(run.workspace_id)
        known_job_ids = {
            str(item.metadata.get("gae_job_id"))
            for item in existing
            if item.run_id == run_id and item.metadata.get("gae_job_id")
        }
        templates = list(getattr(state, "templates", []) or [])
        recorded: List[AnalysisExecution] = []

        for index, result in enumerate(
            list(getattr(state, "execution_results", []) or [])
        ):
            job = getattr(result, "job", None)
            if job is None:
                continue
            job_id = str(getattr(job, "job_id", "") or "")
            if job_id and job_id in known_job_ids:
                continue

            template = next(
                (
                    candidate
                    for candidate in templates
                    if getattr(candidate, "name", None)
                    == getattr(job, "template_name", None)
                ),
                templates[index] if index < len(templates) else None,
            )
            template_config = getattr(template, "config", None)
            graph_config = (
                template_config.to_dict()
                if template_config is not None and hasattr(template_config, "to_dict")
                else {}
            )
            algorithm_params = getattr(
                getattr(template, "algorithm", None), "parameters", {}
            )
            success = bool(getattr(result, "success", False))
            result_rows = list(getattr(result, "results", []) or [])
            job_metrics = dict(getattr(result, "metrics", {}) or {})
            execution_seconds = getattr(job, "execution_time_seconds", None)
            if execution_seconds is not None:
                job_metrics.setdefault("execution_time_seconds", execution_seconds)

            execution = self.record_analysis_execution(
                run_id=run_id,
                algorithm=str(getattr(job, "algorithm", "unknown") or "unknown"),
                status=(
                    AnalysisExecutionStatus.COMPLETED
                    if success
                    else AnalysisExecutionStatus.FAILED
                ),
                template_id=(
                    run.template_ids[index] if index < len(run.template_ids) else None
                ),
                template_name=str(getattr(job, "template_name", "") or ""),
                use_case_id=getattr(template, "use_case_id", None),
                parameters=dict(algorithm_params or {}),
                graph_config=graph_config,
                results_location=getattr(job, "result_collection", None),
                result_count=int(
                    getattr(job, "result_count", None) or len(result_rows)
                ),
                performance_metrics=job_metrics,
                result_sample=(
                    {
                        "top_results": result_rows[:100],
                        "summary_stats": {},
                        "sample_size": min(len(result_rows), 100),
                    }
                    if result_rows
                    else None
                ),
                error_message=(
                    getattr(result, "error", None)
                    or getattr(job, "error_message", None)
                ),
                catalog_execution_id=(
                    (getattr(job, "metadata", {}) or {}).get("catalog_execution_id")
                ),
                started_at=(
                    getattr(job, "started_at", None)
                    or getattr(job, "submitted_at", None)
                ),
                completed_at=getattr(job, "completed_at", None),
                metadata={
                    "gae_job_id": job_id or None,
                    "warnings": list(getattr(result, "warnings", []) or []),
                },
            )
            recorded.append(execution)
            if job_id:
                known_job_ids.add(job_id)

        return recorded

    def get_analysis_execution(self, analysis_execution_id: str) -> AnalysisExecution:
        """Get one product-catalog execution."""

        return self.repository.get_analysis_execution(analysis_execution_id)

    def list_analysis_executions(
        self,
        workspace_id: str,
        algorithm: Optional[str] = None,
        status: Optional[str] = None,
        epoch_id: Optional[str] = None,
        graph_profile_id: Optional[str] = None,
        started_after: Optional[str] = None,
        started_before: Optional[str] = None,
        limit: int = 100,
    ) -> List[AnalysisExecution]:
        """Search executions by the filters required by FR-46."""

        self.repository.get_workspace(workspace_id)
        executions = self.repository.list_analysis_executions(workspace_id)
        after = datetime.fromisoformat(started_after) if started_after else None
        before = datetime.fromisoformat(started_before) if started_before else None
        normalized_status = AnalysisExecutionStatus(status) if status else None

        def matches(execution: AnalysisExecution) -> bool:
            return not (
                (algorithm and execution.algorithm != algorithm)
                or (normalized_status and execution.status != normalized_status)
                or (epoch_id and execution.epoch_id != epoch_id)
                or (graph_profile_id and execution.graph_profile_id != graph_profile_id)
                or (after and execution.started_at < after)
                or (before and execution.started_at > before)
            )

        return [item for item in executions if matches(item)][: max(0, limit)]

    def browse_analysis_catalog(self, workspace_id: str) -> Dict[str, Any]:
        """Browse workspace-scoped epochs, executions, templates, use cases,
        and requirements (FR-45).

        Templates and use cases are full product records now that FR-19..FR-26
        entities exist. Executions can still reference ids that have no product
        record — anything produced by an agentic run before those entities
        landed, or generated inside the AI layer without being mirrored — so
        those are surfaced separately as ``unresolved_*_ids`` rather than
        silently dropped, which would make lineage look complete when it isn't.
        """

        executions = self.list_analysis_executions(workspace_id, limit=500)
        epochs = self.list_analysis_epochs(workspace_id)
        requirements = self.repository.list_requirement_versions(workspace_id)
        use_cases = self.repository.list_use_cases(workspace_id)
        templates = self.list_analysis_templates(workspace_id)

        known_template_ids = {item.analysis_template_id for item in templates}
        known_use_case_ids = {item.use_case_id for item in use_cases}
        referenced_template_ids = {
            execution.template_id for execution in executions if execution.template_id
        }
        referenced_use_case_ids = {
            execution.use_case_id for execution in executions if execution.use_case_id
        }

        return {
            "workspace_id": workspace_id,
            "epochs": [epoch.to_dict() for epoch in epochs],
            "executions": [execution.to_dict() for execution in executions],
            "templates": [template.to_dict() for template in templates],
            "use_cases": [use_case.to_dict() for use_case in use_cases],
            "requirements": [
                {
                    "requirement_version_id": item.requirement_version_id,
                    "version": item.version,
                    "status": item.status.value,
                    "summary": item.summary,
                }
                for item in requirements
            ],
            "unresolved_template_ids": sorted(
                referenced_template_ids - known_template_ids
            ),
            "unresolved_use_case_ids": sorted(
                referenced_use_case_ids - known_use_case_ids
            ),
        }

    def get_analysis_catalog_stats(self, workspace_id: str) -> Dict[str, Any]:
        """Return workspace-scoped execution and epoch aggregates."""

        executions = self.list_analysis_executions(workspace_id, limit=100_000)
        epochs = self.list_analysis_epochs(workspace_id)
        by_status: Dict[str, int] = {}
        by_algorithm: Dict[str, int] = {}
        for execution in executions:
            by_status[execution.status.value] = (
                by_status.get(execution.status.value, 0) + 1
            )
            by_algorithm[execution.algorithm] = (
                by_algorithm.get(execution.algorithm, 0) + 1
            )
        timestamps = [execution.started_at for execution in executions]
        return {
            "workspace_id": workspace_id,
            "execution_count": len(executions),
            "epoch_count": len(epochs),
            "executions_by_status": by_status,
            "executions_by_algorithm": by_algorithm,
            "date_range": {
                "start": min(timestamps).isoformat() if timestamps else None,
                "end": max(timestamps).isoformat() if timestamps else None,
            },
        }

    def compare_analysis_executions(
        self, workspace_id: str, analysis_execution_ids: List[str]
    ) -> Dict[str, Any]:
        """Compare result counts and numeric metrics across executions/epochs."""

        if len(analysis_execution_ids) < 2:
            raise ValidationError("At least two analysis_execution_ids are required")
        executions = [
            self.repository.get_analysis_execution(execution_id)
            for execution_id in analysis_execution_ids
        ]
        if any(item.workspace_id != workspace_id for item in executions):
            raise ValidationError(
                "All analysis executions must belong to the given workspace"
            )

        baseline = executions[0]
        numeric_metric_keys = sorted(
            {
                key
                for item in executions
                for key, value in item.performance_metrics.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
        return {
            "workspace_id": workspace_id,
            "baseline_execution_id": baseline.analysis_execution_id,
            "executions": [item.to_dict() for item in executions],
            "deltas": [
                {
                    "analysis_execution_id": item.analysis_execution_id,
                    "result_count": item.result_count - baseline.result_count,
                    "performance_metrics": {
                        key: item.performance_metrics.get(key, 0)
                        - baseline.performance_metrics.get(key, 0)
                        for key in numeric_metric_keys
                    },
                }
                for item in executions
            ],
        }

    def get_analysis_lineage(self, analysis_execution_id: str) -> Dict[str, Any]:
        """Trace report → execution → template → use case → requirement."""

        execution = self.repository.get_analysis_execution(analysis_execution_id)
        reports = [
            report
            for report in self.repository.list_report_manifests(execution.workspace_id)
            if analysis_execution_id in report.analysis_execution_ids
            or report.run_id == execution.run_id
        ]
        return {
            "workspace_id": execution.workspace_id,
            "reports": [report.to_dict() for report in reports],
            "execution": execution.to_dict(),
            "template_id": execution.template_id,
            "use_case_id": execution.use_case_id,
            "requirement_version_id": execution.requirement_version_id,
        }

    def update_workflow_step(
        self,
        run_id: str,
        step_id: str,
        status: WorkflowStepStatus,
        outputs: Optional[Dict[str, Any]] = None,
        artifact_refs: Optional[List[Dict[str, str]]] = None,
        warnings: Optional[List[str]] = None,
        errors: Optional[List[str]] = None,
        checkpoint_id: Optional[str] = None,
        cost: Optional[Dict[str, Any]] = None,
        _internal: bool = False,
    ) -> WorkflowStepUpdateResult:
        """Update a workflow step and roll up run status for the visualizer.

        FR-31a AC#8: rejects manual updates against agentic runs because
        the :class:`AgenticRunSupervisor` is the sole authority on step
        transitions there — a UI-driven retry would race with the
        :class:`StepStatusReporter`. ``_internal=True`` bypasses the
        check; the supervisor passes it because it *is* the executor.
        The leading underscore intentionally hides the bypass from the
        FastAPI dispatcher (which maps JSON body keys to kwargs by
        name) so external HTTP callers cannot opt out.
        """

        run = self.repository.get_workflow_run(run_id)
        if not _internal and self.is_supervised_agentic_mode(run.workflow_mode):
            raise ConflictError(
                "Step transitions on agentic runs are managed by the "
                "AgenticRunSupervisor. Use POST /api/runs/{id}/cancel to "
                "stop a run; per-step retry on agentic runs is FR-31c."
            )
        step = self._find_workflow_step(run, step_id)
        previous_status = step.status
        step.status = status

        if status == WorkflowStepStatus.RUNNING and step.started_at is None:
            step.started_at = current_timestamp()
        if status in {
            WorkflowStepStatus.COMPLETED,
            WorkflowStepStatus.FAILED,
            WorkflowStepStatus.SKIPPED,
        }:
            step.completed_at = current_timestamp()
        if (
            previous_status == WorkflowStepStatus.FAILED
            and status == WorkflowStepStatus.RUNNING
        ):
            step.retry_count += 1

        if outputs is not None:
            step.outputs = outputs
        if artifact_refs is not None:
            step.artifact_refs = artifact_refs
        if warnings is not None:
            step.warnings = warnings
        if errors is not None:
            step.errors = errors
        if checkpoint_id is not None:
            step.checkpoint_id = checkpoint_id
        if cost is not None:
            step.cost = cost

        self._roll_up_workflow_run_status(run)
        self.repository.update_workflow_run(run)
        return WorkflowStepUpdateResult(
            workflow_run=run.to_dict(),
            dag_view=self.get_workflow_dag_view(run.run_id).to_dict(),
        )

    def supported_workflow_recovery_actions(self, run_id: str) -> Dict[str, List[str]]:
        """Return supported recovery actions keyed by workflow step ID."""

        run = self.repository.get_workflow_run(run_id)
        actions: Dict[str, List[str]] = {}
        for step in run.steps:
            if step.status == WorkflowStepStatus.FAILED:
                actions[step.step_id] = ["retry", "open_logs"]
            elif step.status == WorkflowStepStatus.PAUSED:
                actions[step.step_id] = ["resume", "cancel", "open_logs"]
            else:
                actions[step.step_id] = []
        return actions

    def get_report_bundle(self, report_id: str) -> ReportBundle:
        """Load a full dynamic report payload."""

        manifest = self.repository.get_report_manifest(report_id)
        sections = self.repository.list_report_sections(report_id)
        charts = self.repository.list_chart_specs(report_id)
        snapshots = self.repository.list_published_snapshots(report_id)

        return self._report_bundle(manifest, sections, charts, snapshots)

    def export_report(self, report_id: str, format: str = "html") -> ReportExportResult:
        """Render a report to a downloadable HTML or Markdown document.

        Implements PRD FR-42 / MVP acceptance #14. Returns a
        :class:`ReportExportResult` so the FastAPI adapter can stream the
        bytes back as an attachment with the correct media type rather than
        wrapping them in a JSON envelope.

        Args:
            report_id: Report manifest identifier.
            format: ``"html"`` or ``"markdown"`` (case-insensitive). Anything
                else raises :class:`ValidationError` so callers get a clean
                4xx instead of a server error.
        """

        normalized = (format or "html").lower()
        if normalized not in {"html", "markdown"}:
            raise ValidationError(
                f"Unsupported report export format: {format!r} "
                "(supported: html, markdown)"
            )

        manifest = self.repository.get_report_manifest(report_id)
        sections = self.repository.list_report_sections(report_id)
        charts = self.repository.list_chart_specs(report_id)

        # Sort sections by their ``order`` field so the export reads in the
        # same flow as the canvas. Charts retain insertion order; the report
        # canvas does the same.
        ordered_sections = sorted(sections, key=lambda section: section.order)
        slug = self._slugify(manifest.title) or manifest.report_id
        timestamp = current_timestamp().isoformat()

        if normalized == "markdown":
            content = self._render_report_markdown(
                manifest=manifest,
                sections=ordered_sections,
                charts=charts,
                exported_at=timestamp,
            )
            return ReportExportResult(
                content=content,
                media_type="text/markdown; charset=utf-8",
                filename=f"{slug}.md",
                fmt="markdown",
            )

        content = self._render_report_html(
            manifest=manifest,
            sections=ordered_sections,
            charts=charts,
            exported_at=timestamp,
        )
        return ReportExportResult(
            content=content,
            media_type="text/html; charset=utf-8",
            filename=f"{slug}.html",
            fmt="html",
        )

    @staticmethod
    def _slugify(value: str) -> str:
        """Lowercase, replace runs of non-alphanumerics with single ``-``."""

        cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value or "").strip("-").lower()
        return cleaned[:80]

    @staticmethod
    def _section_text(section: ReportSection) -> str:
        """Best-effort plain text for a section's content payload.

        Sections are stored as free-form dicts. The most common shape is
        ``{"text": "..."}`` (markdown body); fall back to a JSON dump so
        unknown shapes are still inspectable rather than silently dropped.
        """

        text = (
            section.content.get("text") if isinstance(section.content, dict) else None
        )
        if isinstance(text, str) and text.strip():
            return text
        if not section.content:
            return ""
        try:
            return json.dumps(section.content, indent=2, default=str)
        except (TypeError, ValueError):
            return repr(section.content)

    def _render_report_markdown(
        self,
        *,
        manifest: ReportManifest,
        sections: List[ReportSection],
        charts: List[ChartSpec],
        exported_at: str,
    ) -> str:
        lines: List[str] = []
        lines.append(f"# {manifest.title}")
        lines.append("")
        status_value = getattr(manifest.status, "value", manifest.status)
        lines.append(
            f"*Report v{manifest.version} · status: {status_value} · "
            f"exported {exported_at}*"
        )
        lines.append("")
        if manifest.summary:
            lines.append(manifest.summary)
            lines.append("")

        if sections:
            lines.append("## Sections")
            lines.append("")
            for section in sections:
                lines.append(f"### {section.title}")
                lines.append("")
                section_type = getattr(section.type, "value", section.type)
                lines.append(f"_{section_type}_")
                lines.append("")
                body = self._section_text(section)
                if body:
                    lines.append(body)
                    lines.append("")
                if section.evidence_refs:
                    lines.append(
                        f"_{len(section.evidence_refs)} evidence reference(s)._"
                    )
                    lines.append("")

        if charts:
            lines.append("## Charts")
            lines.append("")
            for chart in charts:
                chart_type = getattr(chart.chart_type, "value", chart.chart_type)
                lines.append(
                    f"- **{chart.title}** — `{chart_type}` "
                    f"(data source: `{chart.data_source}`)"
                )
            lines.append("")

        # PRD FR-44 lineage: include any populated lineage refs so the export
        # is auditable on its own (no need to cross-reference the live UI).
        lineage_lines: List[str] = []
        if manifest.run_id:
            lineage_lines.append(f"- Run: `{manifest.run_id}`")
        if manifest.workspace_id:
            lineage_lines.append(f"- Workspace: `{manifest.workspace_id}`")
        if manifest.requirement_version_id:
            lineage_lines.append(
                f"- Requirement version: `{manifest.requirement_version_id}`"
            )
        for use_case_id in manifest.use_case_ids:
            lineage_lines.append(f"- Use case: `{use_case_id}`")
        for template_id in manifest.template_ids:
            lineage_lines.append(f"- Template: `{template_id}`")
        for execution_id in manifest.analysis_execution_ids:
            lineage_lines.append(f"- Execution: `{execution_id}`")
        for collection in manifest.result_collections:
            lineage_lines.append(f"- Result collection: `{collection}`")

        if lineage_lines:
            lines.append("## Lineage")
            lines.append("")
            lines.extend(lineage_lines)
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _render_report_html(
        self,
        *,
        manifest: ReportManifest,
        sections: List[ReportSection],
        charts: List[ChartSpec],
        exported_at: str,
    ) -> str:
        # Self-contained HTML doc — inline minimal CSS so the export is
        # readable in any browser without external assets. User content is
        # always escaped via :func:`html.escape` (see calls below) to prevent
        # injection through report titles or sections.
        title_html = html.escape(manifest.title or manifest.report_id)
        status_value = getattr(manifest.status, "value", manifest.status)
        meta_html = html.escape(
            f"Report v{manifest.version} · status: {status_value} · "
            f"exported {exported_at}"
        )

        body_parts: List[str] = []
        body_parts.append(f"<h1>{title_html}</h1>")
        body_parts.append(f'<p class="meta">{meta_html}</p>')
        if manifest.summary:
            body_parts.append(f"<p>{html.escape(manifest.summary)}</p>")

        if sections:
            body_parts.append("<h2>Sections</h2>")
            for section in sections:
                section_type = getattr(section.type, "value", section.type)
                body_parts.append(
                    f'<section class="report-section">'
                    f"<h3>{html.escape(section.title)}</h3>"
                    f'<p class="muted">{html.escape(str(section_type))}</p>'
                )
                body = self._section_text(section)
                if body:
                    body_parts.append(f"<pre>{html.escape(body)}</pre>")
                if section.evidence_refs:
                    body_parts.append(
                        f'<p class="muted">{len(section.evidence_refs)} '
                        f"evidence reference(s).</p>"
                    )
                body_parts.append("</section>")

        if charts:
            body_parts.append("<h2>Charts</h2><ul>")
            for chart in charts:
                chart_type = getattr(chart.chart_type, "value", chart.chart_type)
                body_parts.append(
                    "<li>"
                    f"<strong>{html.escape(chart.title)}</strong> — "
                    f"<code>{html.escape(str(chart_type))}</code> "
                    f"(data source: <code>{html.escape(chart.data_source)}</code>)"
                    "</li>"
                )
            body_parts.append("</ul>")

        lineage_items: List[str] = []
        if manifest.run_id:
            lineage_items.append(
                f"<li>Run: <code>{html.escape(manifest.run_id)}</code></li>"
            )
        if manifest.workspace_id:
            lineage_items.append(
                f"<li>Workspace: <code>{html.escape(manifest.workspace_id)}</code></li>"
            )
        if manifest.requirement_version_id:
            lineage_items.append(
                "<li>Requirement version: "
                f"<code>{html.escape(manifest.requirement_version_id)}</code></li>"
            )
        for use_case_id in manifest.use_case_ids:
            lineage_items.append(
                f"<li>Use case: <code>{html.escape(use_case_id)}</code></li>"
            )
        for template_id in manifest.template_ids:
            lineage_items.append(
                f"<li>Template: <code>{html.escape(template_id)}</code></li>"
            )
        for execution_id in manifest.analysis_execution_ids:
            lineage_items.append(
                f"<li>Execution: <code>{html.escape(execution_id)}</code></li>"
            )
        for collection in manifest.result_collections:
            lineage_items.append(
                f"<li>Result collection: <code>{html.escape(collection)}</code></li>"
            )
        if lineage_items:
            body_parts.append("<h2>Lineage</h2><ul>")
            body_parts.extend(lineage_items)
            body_parts.append("</ul>")

        style = (
            "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "max-width:880px;margin:32px auto;padding:0 24px;color:#222;line-height:1.5;}"
            "h1{margin-bottom:4px;}"
            ".meta{color:#666;font-size:0.9em;margin-top:0;}"
            ".muted{color:#888;font-size:0.85em;}"
            ".report-section{border-top:1px solid #eee;padding-top:16px;margin-top:16px;}"
            "pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow-x:auto;white-space:pre-wrap;}"
            "code{background:#f6f8fa;padding:1px 4px;border-radius:3px;font-size:0.9em;}"
            "ul{padding-left:20px;}"
        )

        return (
            "<!DOCTYPE html>\n"
            '<html lang="en"><head>'
            f'<meta charset="utf-8"/><title>{title_html}</title>'
            f"<style>{style}</style>"
            "</head><body>" + "".join(body_parts) + "</body></html>"
        )

    def list_cluster_databases(
        self,
        endpoint: str,
        username: str,
        password_secret_env_var: str,
        verify_ssl: bool = True,
        include_system: bool = False,
    ) -> Dict[str, Any]:
        """List the databases visible to a set of cluster credentials.

        Part 1 of the two-step connect flow (FR-73 UX): rather than
        forcing the user to know a database name up front, connect to the
        cluster's ``_system`` database with the supplied credentials and
        enumerate the databases those credentials can see, so the UI can
        present a picker. The password is referenced by env-var name
        (``password_secret_env_var``) and resolved at runtime — no secret
        value is accepted or stored here.

        Returns ``{"endpoint": ..., "databases": [...]}``. Raises
        ``ValidationError`` (mapped to 400) when the credentials are
        missing/invalid or lack permission to list databases.
        """

        if not endpoint.strip():
            raise ValidationError("Cluster endpoint is required")
        if not username.strip():
            raise ValidationError("Cluster username is required")
        if not password_secret_env_var.strip():
            raise ValidationError("Password secret env var is required")

        password = self.secret_resolver.resolve(
            {"kind": "env", "ref": password_secret_env_var.strip()}
        )

        try:
            db = self.db_connector(
                endpoint=endpoint.strip(),
                username=username.strip(),
                password=password,
                database="_system",
                verify_ssl=verify_ssl,
                verify_system=False,
            )
            names = list(db.databases() or [])
        except Exception as exc:  # pragma: no cover - depends on driver/cluster
            raise ValidationError(
                self._mask_secret(
                    f"Failed to list databases on '{endpoint.strip()}': {exc}",
                    password,
                )
            ) from exc

        if not include_system:
            names = [name for name in names if not name.startswith("_")]
        return {"endpoint": endpoint.strip(), "databases": sorted(names)}

    def get_connection_defaults(self) -> Dict[str, Any]:
        """Non-secret connection defaults derived from the environment.

        Prefills the connection-profile form so an operator doesn't have
        to retype what the deployment already has configured (endpoint,
        user, database, SSL, deployment mode). This intentionally NEVER
        returns the password value — only the env-var *name* the password
        is referenced by (``ARANGO_PASSWORD``). All fields are best-effort:
        unset variables come back as empty strings so the form simply
        falls back to its own placeholders.
        """

        endpoint = (os.getenv("ARANGO_ENDPOINT") or "").strip()
        username = (os.getenv("ARANGO_USER") or "root").strip()
        database = (os.getenv("ARANGO_DATABASE") or "").strip()

        verify_ssl = True
        verify_raw = os.getenv("ARANGO_VERIFY_SSL")
        if verify_raw is not None:
            parsed = parse_ssl_verify(verify_raw)
            # A CA-path string still means "verify"; only an explicit
            # false disables it.
            verify_ssl = parsed if isinstance(parsed, bool) else True

        mode_str = (os.getenv("GAE_DEPLOYMENT_MODE") or "").strip().lower()
        if mode_str in ("amp", "managed", "arangograph"):
            deployment_mode = "amp"
        elif mode_str in ("self_managed", "self-managed", "genai", "gen-ai"):
            deployment_mode = "self_managed"
        else:
            deployment_mode = ""

        defaults = {
            "endpoint": endpoint,
            "username": username,
            "database": database,
            "verify_ssl": verify_ssl,
            "deployment_mode": deployment_mode,
            "password_secret_env_var": "ARANGO_PASSWORD",
            "login": "password",
        }
        if platform_endpoint() is not None:
            # NFR-24: on the platform a profile can use the signed-in user's
            # own login instead of a password; offer that first.
            defaults["login"] = "platform"
            defaults["username"] = acting_user() or ""
            defaults["secret_ref"] = platform_login_ref()
        return defaults

    def _list_platform_user_databases(
        self, defaults: Dict[str, Any], include_system: bool
    ) -> Dict[str, Any]:
        """The databases the signed-in platform user can use (NFR-24), for the
        zero-config connect flow: no endpoint, user or password to type."""

        user = acting_user()
        system_db = open_as_platform_user("_system", user, check_access=False)
        try:
            names = list(system_db.databases_accessible_to_user() or [])
        except Exception as exc:  # pragma: no cover - depends on driver/cluster
            raise ValidationError(
                f"Failed to list the databases {user!r} can use: {exc}"
            ) from exc
        if not include_system:
            names = [name for name in names if not name.startswith("_")]
        return {
            "endpoint": defaults.get("endpoint") or platform_endpoint(),
            "databases": sorted(names),
            "username": user,
            "verify_ssl": True,
            "deployment_mode": defaults.get("deployment_mode") or "",
            "login": "platform",
            "secret_ref": platform_login_ref(),
        }

    def list_default_cluster_databases(
        self,
        include_system: bool = False,
    ) -> Dict[str, Any]:
        """List databases on the cluster this deployment is already wired to.

        Zero-config connect. ``list_cluster_databases`` makes the caller supply
        endpoint, username and password env-var name — but for the common
        deployment those are the server's own, already in its environment, and
        the connect form was asking an operator to retype what
        ``get_connection_defaults`` had just handed it. That is not a security
        boundary: the password is resolved server-side either way, so taking
        the endpoint and username from the same place widens nothing.

        It matters most for bring-your-own-container, where the container runs
        in the same cluster as ArangoDB and ambient access *is* the deployment
        model, and for demos, where it turns a credentials form into "pick a
        database".

        Raises ``ValidationError`` (400) when the environment has no endpoint,
        which is the signal for the UI to fall back to asking explicitly.
        """

        defaults = self.get_connection_defaults()
        endpoint = (defaults.get("endpoint") or "").strip()
        if defaults.get("login") == "platform":
            return self._list_platform_user_databases(defaults, include_system)
        if not endpoint:
            raise ValidationError(
                "This deployment has no ARANGO_ENDPOINT configured; "
                "connect to a cluster explicitly instead"
            )

        result = self.list_cluster_databases(
            endpoint=endpoint,
            username=defaults.get("username") or "root",
            password_secret_env_var=(
                defaults.get("password_secret_env_var") or "ARANGO_PASSWORD"
            ),
            verify_ssl=bool(defaults.get("verify_ssl", True)),
            include_system=include_system,
        )
        # Echo the non-secret context so the UI can show what it connected as
        # without a second round trip.
        result["username"] = defaults.get("username") or "root"
        result["verify_ssl"] = bool(defaults.get("verify_ssl", True))
        result["deployment_mode"] = defaults.get("deployment_mode") or ""
        return result

    def verify_connection_profile(
        self,
        connection_profile_id: str,
        password_secret_key: str = "password",
        verify_system: bool = True,
    ) -> ConnectionVerificationResult:
        """Resolve a profile password and test its ArangoDB connection."""

        profile = self.repository.get_connection_profile(connection_profile_id)
        password_ref = profile.secret_refs.get(password_secret_key)
        if not password_ref:
            raise ValidationError(
                f"Connection profile is missing secret ref: {password_secret_key}"
            )

        platform_login = is_platform_login(password_ref)
        password = (
            None if platform_login else self.secret_resolver.resolve(password_ref)
        )
        verified_at = current_timestamp()

        try:
            if platform_login:
                open_as_platform_user(profile.database, acting_user())
            else:
                self.db_connector(
                    endpoint=profile.endpoint,
                    username=profile.username,
                    password=password,
                    database=profile.database,
                    verify_ssl=profile.verify_ssl,
                    verify_system=verify_system,
                )
        except LoginRequiredError:
            # Not a property of the profile: the request had no login.
            raise
        except Exception as exc:
            profile.last_verified_at = verified_at
            profile.last_verification_status = ConnectionVerificationStatus.FAILED
            self.repository.update_connection_profile(profile)
            return ConnectionVerificationResult(
                connection_profile_id=profile.connection_profile_id,
                workspace_id=profile.workspace_id,
                status=ConnectionVerificationStatus.FAILED.value,
                verified_at=verified_at.isoformat(),
                endpoint=profile.endpoint,
                database=profile.database,
                error_message=self._mask_secret(str(exc), password),
                gae_status=self._check_gae_access(),
            )

        profile.last_verified_at = verified_at
        profile.last_verification_status = ConnectionVerificationStatus.SUCCESS
        self.repository.update_connection_profile(profile)
        return ConnectionVerificationResult(
            connection_profile_id=profile.connection_profile_id,
            workspace_id=profile.workspace_id,
            status=ConnectionVerificationStatus.SUCCESS.value,
            verified_at=verified_at.isoformat(),
            endpoint=profile.endpoint,
            database=profile.database,
            gae_status=self._check_gae_access(),
        )

    @staticmethod
    def _check_gae_access() -> Dict[str, Any]:
        """Best-effort GAE deployment reachability check (FR-7).

        GAE credentials come from deployment-wide environment variables
        (see ``config.GAEConfig``), not from the connection profile, so
        this reports the deployment's GAE reachability rather than
        anything scoped to the profile being verified. Never raises —
        an unreachable or unconfigured GAE deployment degrades to
        ``{"status": "failed", ...}`` rather than blocking the DB
        verification result above.
        """

        try:
            from ..gae_connection import get_gae_connection

            connection = get_gae_connection()
            if hasattr(connection, "test_connection"):
                reachable = bool(connection.test_connection())
            else:
                connection.list_engines()
                reachable = True
            return {"status": "success" if reachable else "failed"}
        except Exception as exc:  # noqa: BLE001 — never block DB verification
            return {"status": "failed", "message": str(exc)}

    def list_connection_profile_graphs(
        self,
        connection_profile_id: str,
        password_secret_key: str = "password",
        verify_system: bool = True,
        include_system: bool = False,
        include_counts: bool = True,
    ) -> ConnectionGraphsResult:
        """Enumerate named graphs available on a connection profile."""

        profile = self.repository.get_connection_profile(connection_profile_id)
        db = self._open_profile(profile, password_secret_key, verify_system).db

        try:
            raw_graphs = list(db.graphs() or [])
        except Exception as exc:  # pragma: no cover - depends on driver
            raise ValidationError(
                f"Failed to enumerate graphs on '{profile.database}': {exc}"
            ) from exc

        summaries: List[ConnectionGraphSummary] = []
        for raw in raw_graphs:
            name = raw.get("name") or ""
            if not name:
                continue
            is_system = name.startswith("_")
            if is_system and not include_system:
                continue
            edge_definitions = list(raw.get("edge_definitions") or [])
            orphan_collections = list(raw.get("orphan_collections") or [])
            vertex_collections, edge_collections = _collections_from_edge_definitions(
                edge_definitions, orphan_collections
            )

            vertex_count: Optional[int] = None
            edge_count: Optional[int] = None
            if include_counts:
                vertex_count = _safe_collection_total(db, vertex_collections)
                edge_count = _safe_collection_total(db, edge_collections)

            summaries.append(
                ConnectionGraphSummary(
                    name=name,
                    is_system=is_system,
                    vertex_collections=vertex_collections,
                    edge_collections=edge_collections,
                    orphan_collections=orphan_collections,
                    edge_definitions=edge_definitions,
                    vertex_count=vertex_count,
                    edge_count=edge_count,
                )
            )

        summaries.sort(key=lambda graph: (graph.is_system, graph.name.lower()))

        return ConnectionGraphsResult(
            connection_profile_id=profile.connection_profile_id,
            workspace_id=profile.workspace_id,
            database=profile.database,
            graphs=summaries,
        )

    # Sentinel passed as ``graph_name`` to mean "ignore named graphs in
    # this database; create a database-scope profile covering every
    # collection". Surfaces in the UI / API as the literal string
    # ``default``. Kept as a constant so frontend, scripts, and tests
    # all reference the same well-known name.
    DEFAULT_GRAPH_NAME: str = "default"

    def discover_graph_profile(
        self,
        connection_profile_id: str,
        graph_name: Optional[str] = None,
        created_by: Optional[str] = None,
        password_secret_key: str = "password",
        sample_size: int = 100,
        max_samples_per_collection: int = 3,
        verify_system: bool = True,
        schema_strategy: str = "auto",
        force_database_scope: bool = False,
        force_llm: bool = False,
    ) -> GraphDiscoveryResult:
        """Discover graph schema from a connection profile and persist it.

        v0.6 (FR-56..FR-65) — after the legacy collection-typed extraction,
        we additionally run :func:`acquire_schema` to obtain a bundle that
        understands LPG / hybrid / RPT graphs, then stamp the rolled-up
        ``schema_kind`` and conceptual + physical mappings onto the
        persisted :class:`GraphProfile`. The bundle is also written through
        to ``aga_schema_snapshots`` via :class:`WorkspaceSchemaCache` so
        subsequent discoveries / requirements-copilot runs hit the cache.
        ``schema_strategy`` ("auto" | "analyzer" | "heuristic") is the
        FR-57 escalation knob — see :func:`acquire_schema` for the
        precedence rules.

        ``force_llm`` (FR-58): forces the LLM-assisted analyzer path even
        when the algorithmic/heuristic path would otherwise be judged
        sufficient — passed straight through to :func:`acquire_schema`.

        ``force_database_scope`` (FR-67b): create a database-scope
        profile covering every collection regardless of which named
        graphs exist. The persisted profile is named ``"default"`` and
        carries ``metadata.scope == "database"``. Caller-provided
        ``graph_name`` is ignored when this flag is set.
        """

        profile = self.repository.get_connection_profile(connection_profile_id)
        db = self._open_profile(profile, password_secret_key, verify_system).db
        extractor = self.schema_extractor_factory(
            db,
            sample_size=sample_size,
            max_samples_per_collection=max_samples_per_collection,
        )
        schema = extractor.extract()

        if force_database_scope:
            # FR-67b: explicit "default / all-collections" path.
            # Skip _select_graph_name (which would otherwise resolve
            # to schema.graph_names[0] and quietly produce a
            # named-graph profile) and label the profile with the
            # well-known DEFAULT_GRAPH_NAME so the UI / picker can
            # recognise it.
            selected_graph_name = self.DEFAULT_GRAPH_NAME
        else:
            selected_graph_name = self._select_graph_name(
                schema, graph_name, profile.database
            )

        graph_scope = self._scope_to_named_graph(db, schema, selected_graph_name)
        scoped_vertex_collections = sorted(graph_scope["vertex_collections"])
        scoped_edge_collections = sorted(graph_scope["edge_collections"])
        scoped_edge_definitions = graph_scope[
            "edge_definitions"
        ] or self._schema_edge_definitions(schema)
        scoped_counts: Dict[str, int] = {
            "vertex_collections": len(scoped_vertex_collections),
            "edge_collections": len(scoped_edge_collections),
            "document_collections": len(schema.document_collections),
            "total_documents": graph_scope.get(
                "total_documents", schema.total_documents
            ),
            "total_edges": graph_scope.get("total_edges", schema.total_edges),
            "relationships": len(scoped_edge_definitions),
        }

        # v0.6 enrichment: acquire a typed conceptual + physical bundle.
        # Run after the legacy extraction so a failure here can degrade
        # gracefully — the GraphProfile still gets created with its
        # collection lists; only the v0.6 fields are skipped.
        acquisition_bundle, snapshot_id = self._acquire_and_persist_bundle(
            db=db,
            workspace_id=profile.workspace_id,
            graph_name=selected_graph_name,
            strategy=schema_strategy,
            force_llm=force_llm,
        )

        v6_kwargs: Dict[str, Any] = {}
        if acquisition_bundle is not None:
            v6_kwargs["schema_kind"] = acquisition_bundle.schema_kind
            v6_kwargs["conceptual_schema"] = acquisition_bundle.conceptual_schema
            v6_kwargs["physical_mapping"] = acquisition_bundle.physical_mapping
            v6_kwargs["analyzer_metadata"] = acquisition_bundle.analyzer_metadata
            if snapshot_id is not None:
                v6_kwargs["schema_snapshot_id"] = snapshot_id

            # Phase 6b (FR-61..FR-63): classify the analytical purpose
            # so the UI can badge each profile (corpus / KG /
            # structured / analytics / hybrid / unknown). The
            # classification result is stamped into ``analyzer_metadata``
            # under "graph_purpose_classification" so reasons +
            # confidence + per-rule scores survive the round-trip and
            # back the workbench tooltip.
            try:
                classification = classify_graph_purpose(acquisition_bundle)
            except Exception:  # noqa: BLE001 — never block discovery
                classification = None
            if classification is not None:
                v6_kwargs["graph_purpose"] = classification.purpose
                merged_meta = dict(v6_kwargs["analyzer_metadata"])
                merged_meta["graph_purpose_classification"] = classification.to_dict()
                v6_kwargs["analyzer_metadata"] = merged_meta

            # Phase 6d (FR-72): tag every conceptual property with a
            # sensitivity level (high/medium/low/safe/unknown). Stored
            # under analyzer_metadata.sensitivity so the report
            # generator's masking pass + Graph Explorer overlay can read
            # it without re-running the classifier.
            try:
                sensitivity_report = classify_schema_sensitivity(acquisition_bundle)
            except Exception:  # noqa: BLE001 — never block discovery
                sensitivity_report = None
            if sensitivity_report is not None:
                merged_meta = dict(v6_kwargs["analyzer_metadata"])
                merged_meta["sensitivity"] = sensitivity_report.to_dict()
                v6_kwargs["analyzer_metadata"] = merged_meta

        # FR-65: read the deployment's sharding/multitenancy layout straight
        # from ArangoDB. This does NOT depend on the acquisition bundle — the
        # upstream analyzer never emits metadata.multitenancy /
        # metadata.shardingProfile, so waiting for it would leave FR-65
        # permanently blocked. Runs even when acquisition failed, and its own
        # failure is non-fatal.
        try:
            sharding_profile = detect_sharding_profile(db)
        except Exception:  # noqa: BLE001 — never block discovery
            sharding_profile = None
        if sharding_profile is not None:
            merged_meta = dict(v6_kwargs.get("analyzer_metadata") or {})
            merged_meta["sharding_profile"] = sharding_profile.to_dict()
            merged_meta["multitenancy"] = {
                "is_multitenant": sharding_profile.is_multitenant,
                "tenant_key": sharding_profile.tenant_key,
            }
            merged_meta["gae_projection_hints"] = (
                sharding_profile.gae_projection_hints()
            )
            v6_kwargs["analyzer_metadata"] = merged_meta

        # FR-11: re-discovering a graph_name already profiled on this
        # connection bumps the version in place instead of piling up
        # disconnected duplicate rows. Picking the most recently updated
        # match is defensive against pre-existing duplicates; the common
        # case is exactly one match.
        existing_matches = sorted(
            (
                candidate
                for candidate in self.repository.list_graph_profiles(
                    profile.workspace_id
                )
                if candidate.connection_profile_id == connection_profile_id
                and candidate.graph_name == selected_graph_name
            ),
            key=lambda candidate: candidate.updated_at,
        )
        existing_profile = existing_matches[-1] if existing_matches else None

        graph_profile = create_graph_profile(
            workspace_id=profile.workspace_id,
            connection_profile_id=profile.connection_profile_id,
            graph_name=selected_graph_name,
            vertex_collections=scoped_vertex_collections,
            edge_collections=scoped_edge_collections,
            edge_definitions=scoped_edge_definitions,
            counts=scoped_counts,
            created_by=created_by,
            metadata={
                "database": schema.database_name,
                "available_graphs": schema.graph_names,
                "scope": graph_scope.get("scope", "named_graph"),
                "schema_summary": schema.to_summary_dict(),
                "discovered_at": current_timestamp().isoformat(),
                "schema_strategy": schema_strategy,
            },
            **v6_kwargs,
        )

        if existing_profile is not None:
            # Keep the same graph_profile_id so every existing reference
            # (workspace.active_graph_profile_id, GraphSet membership,
            # WorkflowRun.graph_profile_id) still resolves after
            # re-discovery — only the version and discovered content move.
            graph_profile.graph_profile_id = existing_profile.graph_profile_id
            graph_profile.version = existing_profile.version + 1
            graph_profile.status = existing_profile.status
            graph_profile.created_at = existing_profile.created_at
            graph_profile.created_by = existing_profile.created_by or created_by
            graph_profile.collection_roles = existing_profile.collection_roles
            self.repository.update_graph_profile(graph_profile)
        else:
            self.repository.create_graph_profile(graph_profile)

        return GraphDiscoveryResult(
            graph_profile=graph_profile.to_dict(),
            schema_summary=schema.to_summary_dict(),
        )

    def discover_graph_profiles(
        self,
        connection_profile_id: str,
        created_by: Optional[str] = None,
        password_secret_key: str = "password",
        sample_size: int = 100,
        max_samples_per_collection: int = 3,
        verify_system: bool = True,
        schema_strategy: str = "auto",
        include_system: bool = False,
        force_llm: bool = False,
    ) -> WorkspaceGraphInventoryResult:
        """Bulk-discover every named graph on a connection (FR-67).

        Iterates :meth:`list_connection_profile_graphs`, calls
        :meth:`discover_graph_profile` for each non-system graph, and
        aggregates the resulting profiles into a single inventory
        result. Per-graph failures are collected into ``failures``
        rather than aborting the sweep — the UI can render a partial
        inventory with red flags on the broken entries instead of
        nothing at all.

        When the database exposes no named graphs (a common case for
        small / hand-built corpora), a single fallback profile is
        created against the whole database and returned in the
        ``database_only`` slot. This preserves the v0.5 single-graph
        UX for that case while still funnelling everything through
        the same downstream wiring (acquisition + classifier + cache).
        """

        # Enumerate the named graphs first so we can persist each one
        # individually. ``list_connection_profile_graphs`` already opens
        # a database handle and reads ``db.graphs()``; we rely on its
        # error mapping (ValidationError on driver failure).
        inventory = self.list_connection_profile_graphs(
            connection_profile_id=connection_profile_id,
            password_secret_key=password_secret_key,
            verify_system=verify_system,
            include_system=include_system,
            include_counts=False,
        )
        connection = self.repository.get_connection_profile(connection_profile_id)

        graph_profiles: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        database_only: Optional[Dict[str, Any]] = None

        eligible_names = [g.name for g in inventory.graphs if not g.is_system]

        # FR-67b: always create a "default" (database-scope) profile so
        # workspaces with multiple named graphs still expose an
        # all-collections view in the picker. When there are no named
        # graphs at all we *also* surface the same profile in the
        # legacy ``database_only`` slot for older frontends.
        try:
            result = self.discover_graph_profile(
                connection_profile_id=connection_profile_id,
                graph_name=None,
                created_by=created_by,
                password_secret_key=password_secret_key,
                sample_size=sample_size,
                max_samples_per_collection=max_samples_per_collection,
                verify_system=verify_system,
                schema_strategy=schema_strategy,
                force_database_scope=True,
                force_llm=force_llm,
            )
            graph_profiles.append(result.graph_profile)
            if not eligible_names:
                database_only = result.to_dict()
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "graph_name": self.DEFAULT_GRAPH_NAME,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )

        for graph_name in eligible_names:
            try:
                result = self.discover_graph_profile(
                    connection_profile_id=connection_profile_id,
                    graph_name=graph_name,
                    created_by=created_by,
                    password_secret_key=password_secret_key,
                    sample_size=sample_size,
                    max_samples_per_collection=max_samples_per_collection,
                    verify_system=verify_system,
                    schema_strategy=schema_strategy,
                    force_llm=force_llm,
                )
                graph_profiles.append(result.graph_profile)
            except Exception as exc:  # noqa: BLE001
                # Per-graph failure should NOT take down the sweep.
                # The UI surfaces the error next to the failing
                # graph card and the user can retry that one
                # individually with discover_graph_profile.
                failures.append(
                    {
                        "graph_name": graph_name,
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )

        # PRD v0.6 follow-up: detect first-party Arango product
        # artefacts (Autograph corpora + KGs) from the inventory and
        # auto-create one GraphSet per detected project. Failures here
        # are non-fatal — the inventory still returns even if the
        # detector or GraphSet creation throws.
        arango_product_dict, auto_graph_sets = self._detect_and_auto_pair_products(
            connection_profile_id=connection_profile_id,
            workspace_id=connection.workspace_id,
            graph_profiles=graph_profiles,
            inventory=inventory,
            actor=created_by,
        )

        return WorkspaceGraphInventoryResult(
            connection_profile_id=connection_profile_id,
            workspace_id=connection.workspace_id,
            database=connection.database,
            discovered_graph_count=len(graph_profiles),
            graph_profiles=graph_profiles,
            failures=failures,
            database_only=database_only,
            arango_product=arango_product_dict,
            auto_created_graph_sets=auto_graph_sets,
        )

    def _detect_and_auto_pair_products(
        self,
        *,
        connection_profile_id: str,
        workspace_id: str,
        graph_profiles: List[Dict[str, Any]],
        inventory: "ConnectionGraphsResult",
        actor: Optional[str],
    ) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Detect Autograph projects and auto-create one GraphSet each.

        Builds a minimal snapshot from the inventory's collection +
        named-graph names, runs :func:`detect_arango_products`, and
        for each detected project:

        - Locates the corpus + KG :class:`GraphProfile` records that
          belong to this project (matched by ``graph_name`` ==
          ``<project>_CorpusGraph`` / ``<project>_kg``).
        - Calls :meth:`create_graph_set` to register a workspace
          GraphSet wrapping both, with the implicit
          ``rags.entity_types -> Entities.entity_type`` cross-graph
          link pre-populated.

        Idempotent: if a GraphSet with the same name already exists
        for the workspace, we attach to it instead of creating a
        duplicate.

        Returns ``(arango_product_dict_or_None, auto_created_graph_sets)``.
        """

        # Build the snapshot the detector expects (just names — no
        # samples needed). Use the inventory we already have rather
        # than re-querying the DB.
        try:
            snapshot = {
                "collections": [
                    {"name": name}
                    for graph in inventory.graphs
                    for name in (
                        list(graph.vertex_collections)
                        + list(graph.edge_collections)
                        + list(graph.orphan_collections)
                    )
                ],
                "graphs": [{"name": g.name} for g in inventory.graphs],
            }
            # De-dupe collection list — multiple graphs may share collections.
            seen_names: set[str] = set()
            unique_collections: list[dict[str, str]] = []
            for entry in snapshot["collections"]:
                if entry["name"] not in seen_names:
                    seen_names.add(entry["name"])
                    unique_collections.append(entry)
            snapshot["collections"] = unique_collections

            report = detect_arango_products(snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Autograph detection failed for connection %s: %s",
                connection_profile_id,
                exc,
            )
            return None, []

        if report.is_empty:
            return None, []

        product_dict = report.to_dict()
        auto_created: List[Dict[str, Any]] = []

        # Index the just-discovered graph profiles by graph_name for
        # quick lookup when pairing corpus + KG into a GraphSet.
        profiles_by_graph_name: Dict[str, str] = {}
        for profile_dict in graph_profiles:
            graph_name = profile_dict.get("graph_name")
            graph_profile_id = profile_dict.get("graph_profile_id")
            if graph_name and graph_profile_id:
                profiles_by_graph_name[graph_name] = graph_profile_id

        for project in report.autograph_projects:
            corpus_pid = (
                profiles_by_graph_name.get(project.corpus_graph)
                if project.corpus_graph
                else None
            )
            kg_pid = (
                profiles_by_graph_name.get(project.kg_graph)
                if project.kg_graph
                else None
            )
            members = [pid for pid in (corpus_pid, kg_pid) if pid]
            if len(members) < 1:
                # No matching profile in the just-created sweep —
                # likely the project's graphs were skipped (system or
                # excluded). Skip auto-creation rather than guessing.
                continue

            primary = kg_pid or corpus_pid
            graph_set_name = f"autograph:{project.project_name}"

            # Build cross-graph links from the detector's implicit
            # links — but only when both endpoints map to actual
            # GraphProfiles. (When KG is missing for a corpus_only
            # project, no cross-graph link is possible.)
            cross_links: List[Dict[str, Any]] = []
            if corpus_pid and kg_pid:
                for link in project.implicit_links:
                    cross_links.append(
                        {
                            "from_graph_profile_id": corpus_pid,
                            "from_field": link["from"],
                            "to_graph_profile_id": kg_pid,
                            "to_field": link["to"],
                            "link_type": "equality",
                            "confidence": project.confidence,
                            "metadata": {
                                "kind": link["kind"],
                                "discovered_by": "autograph_detector",
                            },
                        }
                    )

            try:
                # Idempotent: re-use existing GraphSet if same name
                # already registered for this workspace.
                existing = [
                    gs
                    for gs in self.list_graph_sets(workspace_id=workspace_id)
                    if gs.name == graph_set_name
                ]
                if existing:
                    auto_created.append(existing[0].to_dict())
                    continue

                graph_set = self.create_graph_set(
                    workspace_id=workspace_id,
                    name=graph_set_name,
                    description=(
                        f"Auto-created from detected Autograph project "
                        f"'{project.project_name}' "
                        f"({project.completeness}). "
                        f"{'; '.join(project.warnings) if project.warnings else ''}"
                    ).strip(),
                    graph_profile_ids=members,
                    primary_graph_profile_id=primary,
                    cross_graph_links=cross_links,
                    actor=actor,
                )
                auto_created.append(graph_set.to_dict())
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Auto-creating GraphSet for Autograph project '%s' " "failed: %s",
                    project.project_name,
                    exc,
                )

        return product_dict, auto_created

    def _acquire_and_persist_bundle(
        self,
        db: Any,
        workspace_id: str,
        graph_name: str,
        strategy: str,
        force_llm: bool = False,
    ) -> tuple[Optional[SchemaAcquisitionBundle], Optional[str]]:
        """Run :func:`acquire_schema` and write through to ``aga_schema_snapshots``.

        Failures here are *non-fatal* — discover_graph_profile must still
        succeed and persist a v0.5-shaped profile. The fallback path the
        acquisition module already provides (analyzer → heuristic →
        warning) covers the most common failure mode (analyzer not
        installed); only a hard storage outage on the cache write or a
        DB-side AQL failure during sampling will surface here.

        ``force_llm`` (FR-58) is passed straight through to
        :func:`acquire_schema`, which is the actual owner of the
        algorithmic-first / LLM-escalation decision (FR-58).

        Returns ``(bundle, snapshot_id)``. ``snapshot_id`` is set when
        the cache write succeeded so the caller can stamp it onto the
        ``GraphProfile`` for the UI back-pointer.
        """

        try:
            cache = WorkspaceSchemaCache(self.repository, workspace_id)
            bundle = acquire_schema(
                db,
                strategy=strategy,  # type: ignore[arg-type]
                graph_name=graph_name,
                cache=cache,
                force_llm=force_llm,
            )
        except Exception:  # noqa: BLE001 — degrade gracefully on any failure
            return None, None

        # Look up the snapshot row that WorkspaceSchemaCache.set just
        # persisted, keyed by the same cache_key the acquisition module
        # uses. The lookup is best-effort — when missing, the GraphProfile
        # simply omits the back-pointer.
        snapshot_id: Optional[str] = None
        try:
            from ..ai.schema.acquire import cache_key as _cache_key

            persisted = self.repository.get_schema_snapshot_by_cache_key(
                _cache_key(database=bundle.database, graph_name=bundle.graph_name)
            )
            snapshot_id = persisted.schema_snapshot_id if persisted else None
        except Exception:  # noqa: BLE001
            snapshot_id = None

        return bundle, snapshot_id

    def get_graph_profile_schema_change(
        self,
        graph_profile_id: str,
        password_secret_key: str = "password",
        verify_system: bool = True,
    ) -> SchemaChangeView:
        """Lightweight schema-change probe for a graph profile (FR-60).

        Resolves the connection from the profile's
        ``connection_profile_id``, opens a database handle, and calls
        :func:`describe_schema_change` against the L2 cache. Read-only:
        does not mutate either cache, never re-runs the analyzer, and
        does not write a snapshot. Typical cost is well under 200ms.
        """

        graph_profile = self.repository.get_graph_profile(graph_profile_id)
        connection = self.repository.get_connection_profile(
            graph_profile.connection_profile_id
        )

        db = self._open_profile(connection, password_secret_key, verify_system).db
        cache = WorkspaceSchemaCache(self.repository, connection.workspace_id)
        report: SchemaChangeReport = describe_schema_change(
            db, graph_name=graph_profile.graph_name, cache=cache
        )
        return SchemaChangeView(
            graph_profile_id=graph_profile_id,
            status=report.status,
            current_shape_fingerprint=report.current_shape_fingerprint,
            current_full_fingerprint=report.current_full_fingerprint,
            cached_shape_fingerprint=report.cached_shape_fingerprint,
            cached_full_fingerprint=report.cached_full_fingerprint,
            needs_full_rebuild=report.needs_full_rebuild,
        )

    def update_graph_profile_conceptual_schema(
        self,
        graph_profile_id: str,
        conceptual_schema: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> GraphProfile:
        """Patch the conceptual schema on a graph profile (FR-64).

        Used by the Type Role editor in Graph Explorer when the user
        renames a logical entity, splits a hybrid label, or attaches
        a description that the analyzer could not infer. The patch is
        bounded — only ``conceptual_schema`` changes; physical mapping
        is owned by the analyzer and untouched here.

        Validates the incoming payload as a dict with at least
        ``entities`` and ``relationships`` keys (lists). Stamps an
        audit event with the actor + before/after summary so the
        change history is traceable.
        """

        if not isinstance(conceptual_schema, dict):
            raise ValidationError("conceptual_schema must be a JSON object")
        for required_key in ("entities", "relationships"):
            value = conceptual_schema.get(required_key)
            if not isinstance(value, list):
                raise ValidationError(
                    f"conceptual_schema.{required_key} must be a list "
                    "(got {})".format(type(value).__name__)
                )

        graph_profile = self.repository.get_graph_profile(graph_profile_id)
        before_entities = len(
            (graph_profile.conceptual_schema or {}).get("entities", [])
        )
        before_relationships = len(
            (graph_profile.conceptual_schema or {}).get("relationships", [])
        )

        graph_profile.conceptual_schema = conceptual_schema
        # Stamp a manual-override marker on analyzer_metadata so the UI
        # can show "edited by user" provenance and so the next
        # acquisition's reconciliation step can preserve user edits.
        meta = dict(graph_profile.analyzer_metadata or {})
        meta["manual_override"] = {
            "edited_at": current_timestamp().isoformat(),
            "edited_by": actor or "system",
            "field": "conceptual_schema",
        }
        # Phase 6d (FR-72): re-classify sensitivity now that the
        # entity/property names changed. Failures are non-fatal — we
        # keep the prior tags rather than blocking the user's edit.
        try:
            updated_sensitivity = classify_conceptual_schema(conceptual_schema)
            meta["sensitivity"] = updated_sensitivity.to_dict()
        except Exception:  # noqa: BLE001
            pass
        graph_profile.analyzer_metadata = meta

        self.repository.update_graph_profile(graph_profile)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=graph_profile.workspace_id,
                actor=actor or "system",
                action="update_graph_profile_conceptual_schema",
                target_type="graph_profile",
                target_id=graph_profile.graph_profile_id,
                metadata={
                    "before": {
                        "entities": before_entities,
                        "relationships": before_relationships,
                    },
                    "after": {
                        "entities": len(conceptual_schema.get("entities", [])),
                        "relationships": len(
                            conceptual_schema.get("relationships", [])
                        ),
                    },
                },
            )
        )
        return graph_profile

    def update_graph_profile_purpose(
        self,
        graph_profile_id: str,
        graph_purpose: str,
        actor: Optional[str] = None,
    ) -> GraphProfile:
        """Patch the analytical purpose tag on a graph profile (FR-65).

        Used when the user disagrees with the classifier's verdict.
        ``graph_purpose`` must be one of the closed set defined in
        :data:`graph_analytics_ai.ai.schema.graph_purpose.GraphPurpose`.

        The override is recorded on ``analyzer_metadata.manual_override``
        (with field ``graph_purpose``) so the UI can flag the badge as
        "user-set" and so subsequent re-classifications can defer to
        the user's choice unless explicitly reset.
        """

        valid_values = {
            "corpus",
            "knowledge_graph",
            "structured",
            "analytics",
            "hybrid",
            "unknown",
        }
        if graph_purpose not in valid_values:
            raise ValidationError(
                f"graph_purpose must be one of {sorted(valid_values)}, "
                f"got {graph_purpose!r}"
            )

        graph_profile = self.repository.get_graph_profile(graph_profile_id)
        before = graph_profile.graph_purpose

        graph_profile.graph_purpose = graph_purpose
        meta = dict(graph_profile.analyzer_metadata or {})
        meta["manual_override"] = {
            "edited_at": current_timestamp().isoformat(),
            "edited_by": actor or "system",
            "field": "graph_purpose",
            "previous_value": before,
        }
        graph_profile.analyzer_metadata = meta

        self.repository.update_graph_profile(graph_profile)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=graph_profile.workspace_id,
                actor=actor or "system",
                action="update_graph_profile_purpose",
                target_type="graph_profile",
                target_id=graph_profile.graph_profile_id,
                metadata={"before": before, "after": graph_purpose},
            )
        )
        return graph_profile

    def assign_graph_profile_collection_roles(
        self,
        graph_profile_id: str,
        collection_roles: Dict[str, List[str]],
        actor: Optional[str] = None,
    ) -> GraphProfile:
        """Assign analytical roles to collections on a graph profile (FR-10).

        ``collection_roles`` maps a role name (e.g. ``"entity"``,
        ``"fact"``, ``"dimension"`` — an open vocabulary, not a closed
        enum) to the collection names that play that role. Every
        referenced collection must already be part of the profile's
        discovered ``vertex_collections`` or ``edge_collections`` — this
        assigns a role to existing inventory, it does not add collections.

        Re-discovery (:meth:`discover_graph_profile`) preserves whatever
        roles are assigned here across schema refreshes.
        """

        if not isinstance(collection_roles, dict):
            raise ValidationError("collection_roles must be a JSON object")

        graph_profile = self.repository.get_graph_profile(graph_profile_id)
        known_collections = set(graph_profile.vertex_collections) | set(
            graph_profile.edge_collections
        )

        normalized: Dict[str, List[str]] = {}
        for role, collections in collection_roles.items():
            if not isinstance(role, str) or not role.strip():
                raise ValidationError("collection_roles keys must be non-empty strings")
            if not isinstance(collections, list) or not all(
                isinstance(name, str) for name in collections
            ):
                raise ValidationError(
                    f"collection_roles[{role!r}] must be a list of collection names"
                )
            unknown = sorted(set(collections) - known_collections)
            if unknown:
                raise ValidationError(
                    f"collection_roles[{role!r}] references collections not on "
                    f"this profile: {unknown}"
                )
            normalized[role] = list(collections)

        before = dict(graph_profile.collection_roles)
        graph_profile.collection_roles = normalized
        self.repository.update_graph_profile(graph_profile)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=graph_profile.workspace_id,
                actor=actor or "system",
                action="assign_graph_profile_collection_roles",
                target_type="graph_profile",
                target_id=graph_profile.graph_profile_id,
                metadata={"before": before, "after": normalized},
            )
        )
        return graph_profile

    # ------------------------------------------------------------------
    # GraphSet workbench (PRD v0.6 / FR-68..FR-70)
    # ------------------------------------------------------------------

    def create_graph_set(
        self,
        workspace_id: str,
        name: str,
        graph_profile_ids: List[str],
        description: Optional[str] = None,
        cross_graph_links: Optional[List[Dict[str, Any]]] = None,
        primary_graph_profile_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> GraphSet:
        """Create a curated multi-graph grouping (FR-68).

        Validates that:
        - the workspace exists,
        - every ``graph_profile_id`` belongs to that workspace,
        - ``primary_graph_profile_id`` (if set) is in the list,
        - cross-graph link endpoints reference profiles in the set
          (already enforced by :class:`GraphSet.__post_init__`, but
          we surface a friendlier ValidationError here too).
        """

        if not name or not name.strip():
            raise ValidationError("GraphSet name is required")
        if not graph_profile_ids:
            raise ValidationError("GraphSet must contain at least one graph_profile_id")

        # Reject duplicates so the workbench's side-by-side render
        # is deterministic and so the cross-graph link validator
        # operates on a clean ID set.
        seen: set[str] = set()
        deduped: List[str] = []
        for pid in graph_profile_ids:
            if pid in seen:
                raise ValidationError(f"Duplicate graph_profile_id in GraphSet: {pid}")
            seen.add(pid)
            deduped.append(pid)

        # Existence + workspace-scoping check. We never trust the
        # caller to have already validated this — the API surface
        # is workspace-scoped but the endpoint receives raw IDs.
        for pid in deduped:
            profile = self.repository.get_graph_profile(pid)
            if profile.workspace_id != workspace_id:
                raise ValidationError(
                    f"graph_profile {pid} does not belong to workspace {workspace_id}"
                )

        link_objs = [CrossGraphLink.from_dict(d) for d in (cross_graph_links or [])]
        graph_set = create_graph_set(
            workspace_id=workspace_id,
            name=name.strip(),
            graph_profile_ids=deduped,
            description=(description.strip() if description else None),
            cross_graph_links=link_objs,
            primary_graph_profile_id=(
                primary_graph_profile_id or (deduped[0] if deduped else None)
            ),
            created_by=actor,
        )
        self.repository.create_graph_set(graph_set)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="create_graph_set",
                target_type="graph_set",
                target_id=graph_set.graph_set_id,
                metadata={
                    "graph_profile_count": len(deduped),
                    "cross_graph_link_count": len(link_objs),
                },
            )
        )
        return graph_set

    def list_graph_sets(self, workspace_id: str) -> List[GraphSet]:
        """List all graph sets in a workspace, freshest first (FR-68)."""

        return self.repository.list_graph_sets(workspace_id)

    def get_graph_set(self, graph_set_id: str) -> GraphSet:
        """Get a graph set by ID (FR-68)."""

        return self.repository.get_graph_set(graph_set_id)

    def update_graph_set(
        self,
        graph_set_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        graph_profile_ids: Optional[List[str]] = None,
        cross_graph_links: Optional[List[Dict[str, Any]]] = None,
        primary_graph_profile_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> GraphSet:
        """Patch a graph set in place (FR-68 / FR-69).

        Each field is optional so the workbench can rename, retarget
        the primary, add/remove members, or update the link list
        independently. Re-validates the resulting set the same way
        :meth:`create_graph_set` does — duplicates rejected, members
        confirmed in the same workspace, links reference set members.
        """

        graph_set = self.repository.get_graph_set(graph_set_id)
        changes: Dict[str, Any] = {}

        if name is not None:
            stripped = name.strip()
            if not stripped:
                raise ValidationError("name cannot be empty")
            if stripped != graph_set.name:
                changes["name"] = {"from": graph_set.name, "to": stripped}
                graph_set.name = stripped

        if description is not None:
            new_desc = description.strip() if description else None
            if new_desc != graph_set.description:
                changes["description"] = True
                graph_set.description = new_desc

        if graph_profile_ids is not None:
            if not graph_profile_ids:
                raise ValidationError("graph_profile_ids cannot be empty")
            seen: set[str] = set()
            deduped: List[str] = []
            for pid in graph_profile_ids:
                if pid in seen:
                    raise ValidationError(f"Duplicate graph_profile_id: {pid}")
                seen.add(pid)
                deduped.append(pid)
            for pid in deduped:
                profile = self.repository.get_graph_profile(pid)
                if profile.workspace_id != graph_set.workspace_id:
                    raise ValidationError(
                        f"graph_profile {pid} does not belong to "
                        f"workspace {graph_set.workspace_id}"
                    )
            changes["graph_profile_ids"] = {
                "from": list(graph_set.graph_profile_ids),
                "to": deduped,
            }
            graph_set.graph_profile_ids = deduped
            # Demote primary if it dropped out of the list.
            if (
                graph_set.primary_graph_profile_id
                and graph_set.primary_graph_profile_id not in deduped
            ):
                graph_set.primary_graph_profile_id = deduped[0]

        if cross_graph_links is not None:
            ids = set(graph_set.graph_profile_ids)
            link_objs = [CrossGraphLink.from_dict(d) for d in cross_graph_links]
            for link in link_objs:
                if link.from_graph_profile_id not in ids:
                    raise ValidationError(
                        f"CrossGraphLink references unknown profile: "
                        f"{link.from_graph_profile_id}"
                    )
                if link.to_graph_profile_id not in ids:
                    raise ValidationError(
                        f"CrossGraphLink references unknown profile: "
                        f"{link.to_graph_profile_id}"
                    )
            changes["cross_graph_links"] = len(link_objs)
            graph_set.cross_graph_links = link_objs

        if primary_graph_profile_id is not None:
            if primary_graph_profile_id not in graph_set.graph_profile_ids:
                raise ValidationError(
                    "primary_graph_profile_id must be in graph_profile_ids"
                )
            if primary_graph_profile_id != graph_set.primary_graph_profile_id:
                changes["primary_graph_profile_id"] = {
                    "from": graph_set.primary_graph_profile_id,
                    "to": primary_graph_profile_id,
                }
                graph_set.primary_graph_profile_id = primary_graph_profile_id

        if not changes:
            return graph_set

        self.repository.update_graph_set(graph_set)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=graph_set.workspace_id,
                actor=actor or "system",
                action="update_graph_set",
                target_type="graph_set",
                target_id=graph_set.graph_set_id,
                metadata={"changed_fields": sorted(changes.keys())},
            )
        )
        return graph_set

    def discover_cross_graph_links(
        self,
        graph_set_id: str,
        max_links: int = 16,
        min_overlap: int = 2,
        probe_edges: bool = True,
        sample_size: int = 500,
    ) -> List[Dict[str, Any]]:
        """Suggest CrossGraphLinks across the profiles in a set (FR-69).

        Heuristic: any field name that appears in the conceptual
        schemas of two distinct profiles in the set, AND has a
        plausible "joinable identifier" name (id, key, _id, email,
        sha256, document_id, source_id, ssn) is surfaced as a
        candidate link with confidence 0.6. Confidence is bumped
        toward 0.85 when both sides come from the same connection
        (same database — the most common case for a workspace's own
        corpus + KG).

        FR-69 also requires inspecting real edges: when ``probe_edges`` is
        set (the default), each member profile's edge collections are sampled
        and their ``_from``/``_to`` endpoints are read. An edge whose endpoints
        land in collections belonging to two *different* member profiles is a
        cross-graph hop that actually exists in the data, so it is reported at
        confidence 0.95 — strictly higher than any name match, which is only a
        guess that two same-named fields mean the same thing.

        Edge probing needs a live connection. Any failure (no credentials,
        unreachable database, permissions) degrades to name-matching alone
        rather than failing the call, since suggestions are advisory.

        ``max_links`` caps the number of suggestions returned to keep
        the workbench tooltip manageable. ``min_overlap`` is the minimum
        number of sampled edges that must support a hop before it is
        reported, which keeps a single stray edge from looking structural.
        """

        graph_set = self.repository.get_graph_set(graph_set_id)

        # Collect (profile_id, set_of_field_names) per member.
        members: List[tuple[str, str, set[str]]] = []
        for pid in graph_set.graph_profile_ids:
            profile = self.repository.get_graph_profile(pid)
            field_names = self._collect_joinable_fields(profile)
            members.append((pid, profile.connection_profile_id, field_names))

        candidates: List[Dict[str, Any]] = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pid_a, conn_a, fields_a = members[i]
                pid_b, conn_b, fields_b = members[j]
                shared = sorted(fields_a & fields_b)
                for fld in shared:
                    confidence = 0.85 if conn_a == conn_b else 0.60
                    candidates.append(
                        {
                            "from_graph_profile_id": pid_a,
                            "to_graph_profile_id": pid_b,
                            "from_field": fld,
                            "to_field": fld,
                            "link_type": "equality",
                            "confidence": confidence,
                            "metadata": {
                                "discovery": "name_match",
                                "shared_field": fld,
                            },
                        }
                    )

        if probe_edges:
            # Observed hops outrank name guesses, so they are prepended and
            # dedupe wins over any name match for the same profile pair.
            observed = self._probe_cross_graph_edges(
                graph_set_id=graph_set_id,
                min_overlap=min_overlap,
                sample_size=sample_size,
            )
            seen_pairs = {
                (link["from_graph_profile_id"], link["to_graph_profile_id"])
                for link in observed
            }
            candidates = observed + [
                candidate
                for candidate in candidates
                if (
                    candidate["from_graph_profile_id"],
                    candidate["to_graph_profile_id"],
                )
                not in seen_pairs
            ]

        # Sort by confidence desc then field name for stable output.
        candidates.sort(
            key=lambda c: (
                -c["confidence"],
                c["from_field"],
                c["from_graph_profile_id"],
            )
        )
        return candidates[:max_links]

    def _probe_cross_graph_edges(
        self,
        graph_set_id: str,
        min_overlap: int,
        sample_size: int,
    ) -> List[Dict[str, Any]]:
        """Sample real edges and report hops that cross member profiles (FR-69).

        Returns an empty list on any failure: cross-graph suggestions are
        advisory, so a missing credential or unreachable database must not
        turn a workbench hint into an error.
        """

        graph_set = self.repository.get_graph_set(graph_set_id)
        profiles = []
        for pid in graph_set.graph_profile_ids:
            try:
                profiles.append(self.repository.get_graph_profile(pid))
            except Exception:  # noqa: BLE001
                continue
        if len(profiles) < 2:
            return []

        # collection name -> owning profile id. A collection shared by two
        # profiles is ambiguous, so it is excluded rather than attributed to
        # whichever profile happened to be seen first.
        owner: Dict[str, Optional[str]] = {}
        for profile in profiles:
            for name in list(profile.vertex_collections or []):
                owner[name] = None if name in owner else profile.graph_profile_id

        # Group edge collections by connection so each database is opened once.
        by_connection: Dict[str, List[GraphProfile]] = {}
        for profile in profiles:
            by_connection.setdefault(profile.connection_profile_id, []).append(profile)

        # (from_profile, to_profile, edge_collection) -> observed count
        hops: Dict[tuple, int] = {}

        for connection_profile_id, connection_profiles in by_connection.items():
            try:
                db = self._connect_for_connection_profile(connection_profile_id)
            except Exception:  # noqa: BLE001 — advisory feature, degrade quietly
                logger.info(
                    "Cross-graph edge probe skipped for connection %s",
                    connection_profile_id,
                    exc_info=True,
                )
                continue

            edge_collections = sorted(
                {
                    name
                    for profile in connection_profiles
                    for name in list(profile.edge_collections or [])
                }
            )
            for edge_collection in edge_collections:
                for from_id, to_id in self._sample_edge_endpoints(
                    db, edge_collection, sample_size
                ):
                    from_owner = owner.get(from_id.split("/", 1)[0])
                    to_owner = owner.get(to_id.split("/", 1)[0])
                    if not from_owner or not to_owner or from_owner == to_owner:
                        continue
                    key = (from_owner, to_owner, edge_collection)
                    hops[key] = hops.get(key, 0) + 1

        links: List[Dict[str, Any]] = []
        for (from_profile, to_profile, edge_collection), count in hops.items():
            if count < max(1, min_overlap):
                continue
            links.append(
                {
                    "from_graph_profile_id": from_profile,
                    "to_graph_profile_id": to_profile,
                    "from_field": "_from",
                    "to_field": "_to",
                    "link_type": "edge_traversal",
                    # Observed in the data, not inferred from a name.
                    "confidence": 0.95,
                    "metadata": {
                        "discovery": "edge_endpoint_probe",
                        "edge_collection": edge_collection,
                        "observed_edges": count,
                        "sample_size": sample_size,
                    },
                }
            )
        return links

    def _open_profile(
        self,
        profile: ConnectionProfile,
        password_secret_key: str = "password",
        verify_system: bool = True,
    ) -> ProfileDatabase:
        """Open *profile*'s database: with its password, or as the signed-in
        platform user for a platform-login profile (NFR-24)."""

        return open_profile_database(
            profile,
            secret_resolver=self.secret_resolver,
            db_connector=self.db_connector,
            secret_key=password_secret_key,
            verify_system=verify_system,
        )

    def _connect_for_connection_profile(
        self, connection_profile_id: str, password_secret_key: str = "password"
    ):
        """Open a database handle for a connection profile."""

        profile = self.repository.get_connection_profile(connection_profile_id)
        return self._open_profile(profile, password_secret_key, verify_system=False).db

    @staticmethod
    def _sample_edge_endpoints(db, edge_collection: str, sample_size: int):
        """Yield (_from, _to) pairs from a bounded edge sample.

        Only the two endpoint fields are projected — edge documents can carry
        large payloads and none of it is needed to detect a hop.
        """

        query = "FOR e IN @@edge LIMIT @limit " "RETURN {f: e._from, t: e._to}"
        try:
            cursor = db.aql.execute(
                query,
                bind_vars={"@edge": edge_collection, "limit": int(sample_size)},
            )
        except Exception:  # noqa: BLE001 — a missing collection is not fatal
            return
        for row in cursor or []:
            from_id = row.get("f") if isinstance(row, dict) else None
            to_id = row.get("t") if isinstance(row, dict) else None
            if isinstance(from_id, str) and isinstance(to_id, str):
                yield from_id, to_id

    @staticmethod
    def _collect_joinable_fields(profile: GraphProfile) -> set[str]:
        """Pull joinable-looking property names from a profile's schemas.

        Drawn from both the conceptual schema (entity properties +
        relationship properties) and the physical mapping (typeField,
        collectionName) so a heuristic name match against e.g.
        ``email`` or ``sha256`` works for both PG and LPG profiles.
        """
        joinable_patterns = {
            "id",
            "_id",
            "_key",
            "key",
            "email",
            "sha256",
            "uuid",
            "url",
            "document_id",
            "source_id",
            "source_document_id",
            "ssn",
            "phone",
            "tax_id",
            "ein",
            "isbn",
            "doi",
        }
        out: set[str] = set()
        if profile.conceptual_schema:
            for entity in profile.conceptual_schema.get("entities", []) or []:
                for prop in entity.get("properties", []) or []:
                    name = prop if isinstance(prop, str) else (prop or {}).get("name")
                    if isinstance(name, str) and name.lower() in joinable_patterns:
                        out.add(name.lower())
            for rel in profile.conceptual_schema.get("relationships", []) or []:
                for prop in rel.get("properties", []) or []:
                    name = prop if isinstance(prop, str) else (prop or {}).get("name")
                    if isinstance(name, str) and name.lower() in joinable_patterns:
                        out.add(name.lower())
        return out

    # ------------------------------------------------------------------
    # Source documents (FR-13..FR-15)
    # ------------------------------------------------------------------

    SUPPORTED_DOCUMENT_SUFFIXES = (".md", ".markdown", ".pdf", ".docx", ".txt")

    def upload_source_document(
        self,
        workspace_id: str,
        filename: str,
        content_base64: str,
        mime_type: str = "application/octet-stream",
        actor: Optional[str] = None,
        extract_requirements: bool = True,
    ) -> SourceDocument:
        """Upload a document, extract its text, and persist it (FR-13..FR-15).

        Content arrives base64-encoded in a JSON body rather than as
        multipart: every other route in this product API is JSON-only
        (see ``fastapi_app._request_json``), and introducing multipart
        for one endpoint would fork the dispatcher.

        Only the *extracted text* is persisted (``EXTRACT_ONLY``) — the
        raw upload is written to a temp file for parsing and deleted
        immediately. ArangoDB is product metadata storage, not a blob
        store, and PDFs/DOCX would bloat it for no downstream benefit.

        FR-15: structured requirements extraction runs best-effort and
        is stored under ``metadata["extracted_requirements"]``. It needs
        an LLM provider, so it is expected to be unavailable in many
        environments — failure never blocks the upload. Promoting an
        extraction into an approvable ``RequirementVersion`` is a
        separate flow (the Requirements Copilot owns that today).
        """

        if not filename or not filename.strip():
            raise ValidationError("filename is required")

        filename = filename.strip()
        suffix = Path(filename).suffix.lower()
        if suffix not in self.SUPPORTED_DOCUMENT_SUFFIXES:
            raise ValidationError(
                f"Unsupported document type '{suffix or filename}'. "
                f"Supported: {', '.join(self.SUPPORTED_DOCUMENT_SUFFIXES)}"
            )

        # Validates the workspace exists before doing any parsing work.
        self.repository.get_workspace(workspace_id)

        try:
            raw_bytes = base64.b64decode(content_base64 or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError(f"content_base64 is not valid base64: {exc}") from exc

        if not raw_bytes:
            raise ValidationError("Uploaded document is empty")

        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        parsed_document, extraction_errors = self._parse_uploaded_document(
            raw_bytes, filename, suffix
        )
        extracted_text = parsed_document.content if parsed_document else ""

        metadata: Dict[str, Any] = {
            "byte_size": len(raw_bytes),
            "uploaded_by": actor or "system",
        }
        if extraction_errors:
            metadata["extraction_errors"] = extraction_errors

        if extract_requirements and parsed_document and extracted_text.strip():
            extracted = self._extract_requirements(parsed_document)
            if extracted is not None:
                # The summary stays for at-a-glance display; the draft payload
                # is what `promote_extracted_requirements` needs, because the
                # summary is lossy by design (counts, not requirement text).
                metadata["extracted_requirements"] = extracted.to_summary_dict()
                metadata["extracted_requirements_draft"] = (
                    self._extracted_requirements_payload(extracted)
                )

        document = create_source_document(
            workspace_id=workspace_id,
            filename=filename,
            mime_type=mime_type or "application/octet-stream",
            sha256=sha256,
            storage_mode=DocumentStorageMode.EXTRACT_ONLY,
            extracted_text=extracted_text or None,
            metadata=metadata,
        )
        self.repository.create_source_document(document)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="upload_source_document",
                target_type="source_document",
                target_id=document.document_id,
                details={"filename": filename, "sha256": sha256},
            )
        )
        return document

    @staticmethod
    def _parse_uploaded_document(
        raw_bytes: bytes,
        filename: str,
        suffix: str,
    ) -> tuple[Any, List[str]]:
        """Extract text from uploaded bytes via the existing DocumentParser.

        ``DocumentParser`` is path-based for binary formats, so bytes are
        staged in a NamedTemporaryFile that is always removed. Returns
        ``(document_or_None, extraction_errors)`` — never raises, so a
        malformed PDF still yields a persisted metadata row the user can
        see and re-upload against.
        """

        try:
            from ..ai.documents.parser import DocumentParser
        except Exception as exc:  # noqa: BLE001 — optional parser deps
            return None, [f"Document parser unavailable: {exc}"]

        temp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_file.write(raw_bytes)
                temp_path = temp_file.name
            document = DocumentParser().parse(temp_path)
            # DocumentParser swallows per-format failures and reports them
            # on the returned document rather than raising.
            return document, list(getattr(document, "extraction_errors", []) or [])
        except Exception as exc:  # noqa: BLE001 — upload must still succeed
            return None, [str(exc)]
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    @staticmethod
    def _extract_requirements(parsed_document: Any) -> Optional[Any]:
        """Run LLM requirements extraction (FR-15), or return None.

        Returns the ``ExtractedRequirements`` itself rather than its summary:
        the caller needs the full content to build a promotable draft, and
        ``to_summary_dict()`` discards requirement text.

        Requires a configured LLM provider; returns ``None`` whenever
        that is unavailable or extraction fails, so document upload
        works in environments with no LLM credentials.
        """

        try:
            from ..ai.documents.extractor import RequirementsExtractor

            return RequirementsExtractor().extract([parsed_document])
        except Exception:  # noqa: BLE001 — extraction is best-effort
            logger.info(
                "Requirements extraction unavailable for uploaded document; "
                "persisting extracted text only",
                exc_info=True,
            )
            return None

    @staticmethod
    def _extracted_requirements_payload(extracted: Any) -> Dict[str, Any]:
        """Serialise extraction output into RequirementVersion shape (FR-15).

        ``ExtractedRequirements.to_summary_dict()`` is built for LLM prompting:
        it reports *counts* (``requirements_by_type``,
        ``success_criteria_count``) and drops requirement text entirely. That
        is unusable as the basis of a requirement version, which is why the
        extracted requirements could be stored but never promoted. This keeps
        the content — text, priority, type, success criteria — in the same
        ``{id, text, source}``-cored item shape the Copilot path produces, so
        both origins yield versions the rest of the product can read
        identically.
        """

        def _value(field: Any) -> Optional[str]:
            return getattr(field, "value", field) if field is not None else None

        objectives = [
            {
                "id": objective.id or f"OBJ-{index}",
                "text": objective.title,
                "source": "document_extraction",
                "description": objective.description,
                "priority": _value(objective.priority),
                "success_criteria": list(objective.success_criteria or []),
            }
            for index, objective in enumerate(extracted.objectives or [], start=1)
        ]
        requirements = [
            {
                "id": requirement.id or f"REQ-{index}",
                "text": requirement.text,
                "source": "document_extraction",
                "type": _value(requirement.requirement_type),
                "priority": _value(requirement.priority),
                "stakeholders": list(requirement.stakeholders or []),
                "source_document": requirement.source_document,
            }
            for index, requirement in enumerate(extracted.requirements or [], start=1)
        ]
        constraints = [
            {
                "id": f"CON-{index}",
                "text": constraint,
                "source": "document_extraction",
            }
            for index, constraint in enumerate(extracted.constraints or [], start=1)
        ]
        return {
            "summary": extracted.summary or "",
            "objectives": objectives,
            "requirements": requirements,
            "constraints": constraints,
        }

    def promote_extracted_requirements(
        self,
        document_id: str,
        actor: Optional[str] = None,
    ) -> RequirementVersion:
        """Promote a document's extracted requirements into a DRAFT version.

        FR-15: extraction ran on upload and the result was persisted, but
        nothing could consume it — there was no path from an uploaded document
        to an approvable ``RequirementVersion``, so the extracted requirements
        were effectively write-only.

        A DRAFT (not APPROVED) is created deliberately: machine extraction is
        a proposal, and the workspace's single active version should only
        change once a human approves it via ``approve_requirement_version``.
        """

        document = self.repository.get_source_document(document_id)
        payload = (document.metadata or {}).get("extracted_requirements_draft")
        if not payload:
            raise ValidationError(
                "This document has no extracted requirements to promote. "
                "Requirements extraction requires a configured LLM provider at "
                "upload time; re-upload the document once one is available."
            )

        existing_versions = self.repository.list_requirement_versions(
            document.workspace_id
        )
        next_version = max({v.version for v in existing_versions}, default=0) + 1

        requirement_version = create_requirement_version(
            workspace_id=document.workspace_id,
            version=next_version,
            status=RequirementVersionStatus.DRAFT,
            document_ids=[document.document_id],
            summary=payload.get("summary") or f"Extracted from {document.filename}",
            objectives=payload.get("objectives") or [],
            requirements=payload.get("requirements") or [],
            constraints=payload.get("constraints") or [],
            metadata={
                "source": "document_extraction",
                "source_document_id": document.document_id,
                "source_filename": document.filename,
                "promoted_by": actor,
            },
        )
        self.repository.create_requirement_version(requirement_version)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=document.workspace_id,
                actor=actor or "system",
                action="promote_extracted_requirements",
                target_type="requirement_version",
                target_id=requirement_version.requirement_version_id,
                details={
                    "document_id": document.document_id,
                    "version": next_version,
                },
            )
        )
        return requirement_version

    def approve_requirement_version(
        self,
        requirement_version_id: str,
        approved_by: Optional[str] = None,
    ) -> RequirementVersion:
        """Approve an existing DRAFT requirement version (FR-15/FR-17).

        The Copilot had the only approval path, and it approves *from an
        interview*. A version promoted from document extraction has no
        interview, so without this it could be created but never activated.
        """

        requirement_version = self.repository.get_requirement_version(
            requirement_version_id
        )
        if requirement_version.status is RequirementVersionStatus.APPROVED:
            return requirement_version
        if requirement_version.status is not RequirementVersionStatus.DRAFT:
            raise ValidationError(
                "Only DRAFT requirement versions can be approved; "
                f"v{requirement_version.version} is "
                f"{requirement_version.status.value}."
            )

        requirement_version.status = RequirementVersionStatus.APPROVED
        requirement_version.approved_at = current_timestamp()
        requirement_version.metadata = {
            **(requirement_version.metadata or {}),
            "approved_by": approved_by,
        }
        self.repository.update_requirement_version(requirement_version)

        self._supersede_prior_approved_versions(
            workspace_id=requirement_version.workspace_id,
            new_version_id=requirement_version.requirement_version_id,
        )
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=requirement_version.workspace_id,
                actor=approved_by or "system",
                action="approve_requirement_version",
                target_type="requirement_version",
                target_id=requirement_version.requirement_version_id,
                details={"version": requirement_version.version},
            )
        )
        return requirement_version

    def _supersede_prior_approved_versions(
        self,
        workspace_id: str,
        new_version_id: str,
    ) -> None:
        """Flip other APPROVED versions to SUPERSEDED.

        Keeps the workspace invariant of exactly one active requirement set.
        """

        for prior in self.repository.list_requirement_versions(workspace_id):
            if prior.requirement_version_id == new_version_id:
                continue
            if prior.status is RequirementVersionStatus.APPROVED:
                prior.status = RequirementVersionStatus.SUPERSEDED
                prior.metadata = {
                    **(prior.metadata or {}),
                    "superseded_by": new_version_id,
                    "superseded_at": current_timestamp().isoformat(),
                }
                self.repository.update_requirement_version(prior)

    def list_source_documents(self, workspace_id: str) -> List[SourceDocument]:
        """List uploaded source documents for a workspace (FR-13/FR-14)."""

        return self.repository.list_source_documents(workspace_id)

    def start_requirements_copilot(
        self,
        graph_profile_id: str,
        domain: Optional[str] = None,
        created_by: Optional[str] = None,
        based_on_version_id: Optional[str] = None,
    ) -> RequirementInterview:
        """Start a schema-aware Requirements Copilot interview.

        When ``based_on_version_id`` is provided, the new interview is
        pre-populated with synthesised answers derived from that version's
        summary / objectives / requirements / constraints, so the user is
        revising rather than retyping. The new interview is still tied to a
        fresh ``requirement_interview_id``; on approve, a new
        ``RequirementVersion`` is created and any prior APPROVED versions in
        the same workspace are flipped to ``SUPERSEDED``.
        """

        graph_profile = self.repository.get_graph_profile(graph_profile_id)
        schema_observations = self._schema_observations_from_graph_profile(
            graph_profile
        )
        questions = self._requirements_copilot_questions(schema_observations)

        prefilled_answers: List[Dict[str, Any]] = []
        prior_version_metadata: Dict[str, Any] = {}
        if based_on_version_id:
            prior = self.repository.get_requirement_version(based_on_version_id)
            if prior.workspace_id != graph_profile.workspace_id:
                raise ValidationError(
                    "based_on_version_id must belong to the same workspace as the graph profile"
                )
            prefilled_answers = self._prefill_answers_from_version(prior, questions)
            prior_version_metadata = {
                "based_on_version_id": prior.requirement_version_id,
                "based_on_version": prior.version,
            }
            # Inherit the prior version's domain when the caller didn't pass
            # one explicitly. The approve flow stamps `metadata["domain"]` onto
            # each new version, so this chains forward across v1 → v2 → vN
            # without the user retyping "AdTech" every time.
            if domain is None:
                prior_domain = prior.metadata.get("domain") if prior.metadata else None
                if isinstance(prior_domain, str) and prior_domain.strip():
                    domain = prior_domain

        metadata: Dict[str, Any] = {}
        if created_by:
            metadata["created_by"] = created_by
        metadata.update(prior_version_metadata)

        interview = create_requirement_interview(
            workspace_id=graph_profile.workspace_id,
            graph_profile_id=graph_profile.graph_profile_id,
            domain=domain,
            questions=questions,
            schema_observations=schema_observations,
            answers=prefilled_answers,
            metadata=metadata,
        )
        self.repository.create_requirement_interview(interview)
        return interview

    def _prefill_answers_from_version(
        self,
        version: RequirementVersion,
        questions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Synthesise interview answers from a prior approved RequirementVersion.

        Maps the structured fields of the prior version back onto the
        question_ids the copilot will ask, so users see their previous answers
        already filled in (and editable) rather than starting from a blank
        interview.
        """

        def _items_to_text(items: List[Dict[str, Any]]) -> str:
            # Items can come in two shapes:
            #   - Copilot-derived: {"id", "text", "source"}
            #   - BRD-imported:    {"id", "title", "description", "priority"}
            # Prefer the most descriptive field available, then fall back.
            lines: List[str] = []
            for item in items:
                text = str(item.get("text") or "").strip()
                title = str(item.get("title") or "").strip()
                description = str(item.get("description") or "").strip()
                if text:
                    lines.append(f"- {text}")
                elif title and description and title != description:
                    lines.append(f"- {title}: {description}")
                elif title:
                    lines.append(f"- {title}")
                elif description:
                    lines.append(f"- {description}")
            return "\n".join(lines)

        synthesised: Dict[str, str] = {
            "business_goal": (version.summary or "").strip(),
            "analytics_questions": _items_to_text(version.requirements),
            "constraints": _items_to_text(version.constraints),
        }
        # Some deployments may add extra question_ids; we only pre-fill the
        # ones we recognise to avoid leaking stale text into unrelated fields.
        valid_ids = {str(q.get("id")) for q in questions if q.get("id")}
        timestamp = current_timestamp().isoformat()
        return [
            {
                "question_id": qid,
                "answer": text,
                "actor": "system:prefilled-from-version",
                "answered_at": timestamp,
            }
            for qid, text in synthesised.items()
            if qid in valid_ids and text
        ]

    def answer_requirements_copilot_question(
        self,
        requirement_interview_id: str,
        question_id: str,
        answer: str,
        actor: Optional[str] = None,
    ) -> RequirementInterview:
        """Record or replace an answer in a Requirements Copilot session."""

        interview = self.repository.get_requirement_interview(requirement_interview_id)
        answers = [
            existing
            for existing in interview.answers
            if existing.get("question_id") != question_id
        ]
        answers.append(
            {
                "question_id": question_id,
                "answer": answer,
                "actor": actor,
                "answered_at": current_timestamp().isoformat(),
            }
        )
        interview.answers = answers
        self.repository.update_requirement_interview(interview)
        return interview

    def generate_requirements_copilot_draft(
        self,
        requirement_interview_id: str,
    ) -> RequirementsDraftResult:
        """Generate a deterministic BRD draft from schema observations and answers."""

        interview = self.repository.get_requirement_interview(requirement_interview_id)
        answer_map = {
            answer["question_id"]: answer.get("answer", "")
            for answer in interview.answers
        }
        draft_brd = self._build_requirements_draft(interview, answer_map)
        provenance_labels = self._build_requirements_provenance(interview, answer_map)

        interview.draft_brd = draft_brd
        interview.provenance_labels = provenance_labels
        interview.status = RequirementInterviewStatus.READY_FOR_REVIEW
        self.repository.update_requirement_interview(interview)

        return RequirementsDraftResult(
            requirement_interview=interview.to_dict(),
            draft_brd=draft_brd,
            provenance_labels=provenance_labels,
        )

    def approve_requirements_copilot_draft(
        self,
        requirement_interview_id: str,
        version: Optional[int] = None,
        approved_by: Optional[str] = None,
    ) -> RequirementVersion:
        """Approve a generated BRD draft into a requirement version.

        Behaviour:
        - If ``version`` is omitted, the next version number is computed as
          ``max(existing.version) + 1`` for the workspace (or 1 if none).
        - If ``version`` is provided AND collides with an existing version
          number for this workspace, ``ValidationError`` is raised; pass
          ``None`` (or omit) to take the auto-incremented value.
        - All currently APPROVED versions in the workspace are flipped to
          ``SUPERSEDED`` so there is always exactly one active version.
        """

        interview = self.repository.get_requirement_interview(requirement_interview_id)
        if not interview.draft_brd:
            raise ValidationError("Requirements Copilot draft must be generated first")

        existing_versions = self.repository.list_requirement_versions(
            interview.workspace_id
        )
        existing_numbers = {prior.version for prior in existing_versions}

        if version is None:
            next_version = max(existing_numbers, default=0) + 1
        else:
            if version in existing_numbers:
                raise ValidationError(
                    f"RequirementVersion v{version} already exists in this workspace; "
                    "omit the 'version' field to auto-increment."
                )
            next_version = version

        prior_version_metadata: Dict[str, Any] = {}
        if interview.metadata:
            for key in ("based_on_version_id", "based_on_version"):
                if interview.metadata.get(key) is not None:
                    prior_version_metadata[key] = interview.metadata[key]

        requirement_version = create_requirement_version(
            workspace_id=interview.workspace_id,
            version=next_version,
            status=RequirementVersionStatus.APPROVED,
            requirement_interview_id=interview.requirement_interview_id,
            summary=self._answer_for_question(interview, "business_goal")
            or "Requirements Copilot approved draft",
            objectives=self._requirement_items_from_answer(
                interview,
                "business_goal",
                prefix="OBJ",
            ),
            requirements=self._requirement_items_from_answer(
                interview,
                "analytics_questions",
                prefix="REQ",
            ),
            constraints=self._requirement_items_from_answer(
                interview,
                "constraints",
                prefix="CON",
            ),
            approved_at=current_timestamp(),
            metadata={
                "approved_by": approved_by,
                "source": "requirements_copilot",
                "draft_brd": interview.draft_brd,
                "provenance_labels": interview.provenance_labels,
                # Persist the interview's domain on the version so a later
                # "Reopen Copilot to Produce v(N+1)" can pre-fill the Domain
                # field instead of asking the user to retype it.
                **({"domain": interview.domain} if interview.domain else {}),
                **prior_version_metadata,
            },
        )
        self.repository.create_requirement_version(requirement_version)

        # Flip any prior APPROVED versions to SUPERSEDED so the workspace has
        # exactly one active set of requirements.
        self._supersede_prior_approved_versions(
            workspace_id=interview.workspace_id,
            new_version_id=requirement_version.requirement_version_id,
        )

        interview.status = RequirementInterviewStatus.APPROVED
        self.repository.update_requirement_interview(interview)
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=interview.workspace_id,
                actor=approved_by or "system",
                action="approve_requirement_version",
                target_type="requirement_version",
                target_id=requirement_version.requirement_version_id,
                details={"version": next_version},
            )
        )
        return requirement_version

    def export_workspace_bundle(
        self,
        workspace_id: str,
        include_audit_events: bool = True,
        audit_limit: int = 1000,
        actor: Optional[str] = None,
    ) -> WorkspaceBundle:
        """Export workspace metadata without resolved secrets or secret refs."""

        workspace = self.repository.get_workspace(workspace_id)
        connection_profiles = self.repository.list_connection_profiles(workspace_id)
        graph_profiles = self.repository.list_graph_profiles(workspace_id)
        source_documents = self.repository.list_source_documents(workspace_id)
        requirement_interviews = self.repository.list_requirement_interviews(
            workspace_id
        )
        requirement_versions = self.repository.list_requirement_versions(workspace_id)
        workflow_runs = self.repository.list_workflow_runs(workspace_id)
        report_manifests = self.repository.list_report_manifests(workspace_id)
        analysis_epochs = self.repository.list_analysis_epochs(workspace_id)
        analysis_executions = self.repository.list_analysis_executions(workspace_id)
        use_cases = self.list_use_cases(workspace_id)
        analysis_templates = self.list_analysis_templates(workspace_id)

        reports = [
            self.get_report_bundle(report.report_id).to_dict()
            for report in report_manifests
        ]
        audit_events = (
            [
                event.to_dict()
                for event in self.repository.list_audit_events(
                    workspace_id,
                    limit=audit_limit,
                )
            ]
            if include_audit_events
            else []
        )

        bundle = WorkspaceBundle(
            schema_version=PRODUCT_SCHEMA_VERSION,
            workspace=workspace.to_dict(),
            connection_profiles=[
                self._export_connection_profile(profile)
                for profile in connection_profiles
            ],
            graph_profiles=[profile.to_dict() for profile in graph_profiles],
            source_documents=[document.to_dict() for document in source_documents],
            requirement_interviews=[
                interview.to_dict() for interview in requirement_interviews
            ],
            requirement_versions=[
                version.to_dict() for version in requirement_versions
            ],
            workflow_runs=[run.to_dict() for run in workflow_runs],
            reports=reports,
            audit_events=audit_events,
            analysis_epochs=[epoch.to_dict() for epoch in analysis_epochs],
            analysis_executions=[
                execution.to_dict() for execution in analysis_executions
            ],
            use_cases=[use_case.to_dict() for use_case in use_cases],
            analysis_templates=[template.to_dict() for template in analysis_templates],
        )
        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor or "system",
                action="export_workspace_bundle",
                target_type="workspace",
                target_id=workspace_id,
            )
        )
        return bundle

    def import_workspace_bundle(
        self,
        bundle: WorkspaceBundle | Dict[str, Any],
        include_audit_events: bool = False,
        actor: Optional[str] = None,
    ) -> WorkspaceImportResult:
        """Import a workspace bundle after validating shape and secret handling."""

        bundle_doc = bundle.to_dict() if isinstance(bundle, WorkspaceBundle) else bundle
        self._validate_workspace_bundle(bundle_doc)

        workspace = Workspace.from_dict(bundle_doc["workspace"])
        self.repository.create_workspace(workspace)

        connection_profiles = [
            ConnectionProfile.from_dict(profile)
            for profile in bundle_doc.get("connection_profiles", [])
        ]
        graph_profiles = [
            GraphProfile.from_dict(profile)
            for profile in bundle_doc.get("graph_profiles", [])
        ]
        source_documents = [
            SourceDocument.from_dict(document)
            for document in bundle_doc.get("source_documents", [])
        ]
        requirement_interviews = [
            RequirementInterview.from_dict(interview)
            for interview in bundle_doc.get("requirement_interviews", [])
        ]
        requirement_versions = [
            RequirementVersion.from_dict(version)
            for version in bundle_doc.get("requirement_versions", [])
        ]
        analysis_epochs = [
            AnalysisEpoch.from_dict(epoch)
            for epoch in bundle_doc.get("analysis_epochs", [])
        ]
        analysis_executions = [
            AnalysisExecution.from_dict(execution)
            for execution in bundle_doc.get("analysis_executions", [])
        ]
        workflow_runs = [
            WorkflowRun.from_dict(run) for run in bundle_doc.get("workflow_runs", [])
        ]
        # FR-51: absent from bundles written before use cases and analysis
        # templates became product records, so both default to empty and older
        # bundles still import cleanly.
        use_cases = [
            UseCase.from_dict(use_case) for use_case in bundle_doc.get("use_cases", [])
        ]
        analysis_templates = [
            AnalysisTemplate.from_dict(template)
            for template in bundle_doc.get("analysis_templates", [])
        ]

        for profile in connection_profiles:
            self.repository.create_connection_profile(profile)
        for profile in graph_profiles:
            self.repository.create_graph_profile(profile)
        for document in source_documents:
            self.repository.create_source_document(document)
        for interview in requirement_interviews:
            self.repository.create_requirement_interview(interview)
        for version in requirement_versions:
            self.repository.create_requirement_version(version)
        for epoch in analysis_epochs:
            self.repository.create_analysis_epoch(epoch)
        for execution in analysis_executions:
            self.repository.create_analysis_execution(execution)
        for run in workflow_runs:
            self.repository.create_workflow_run(run)
        for use_case in use_cases:
            self.repository.create_use_case(use_case)
        for template in analysis_templates:
            self.repository.create_analysis_template(template)

        report_count = 0
        section_count = 0
        chart_count = 0
        snapshot_count = 0
        for report_doc in bundle_doc.get("reports", []):
            manifest = ReportManifest.from_dict(report_doc["manifest"])
            self.repository.create_report_manifest(manifest)
            report_count += 1

            for section_doc in report_doc.get("sections", []):
                self.repository.create_report_section(
                    ReportSection.from_dict(section_doc)
                )
                section_count += 1
            for chart_doc in report_doc.get("charts", []):
                self.repository.create_chart_spec(ChartSpec.from_dict(chart_doc))
                chart_count += 1
            for snapshot_doc in report_doc.get("snapshots", []):
                self.repository.create_published_snapshot(
                    PublishedSnapshot.from_dict(snapshot_doc)
                )
                snapshot_count += 1

        audit_count = 0
        if include_audit_events:
            for event_doc in bundle_doc.get("audit_events", []):
                self.repository.create_audit_event(AuditEvent.from_dict(event_doc))
                audit_count += 1

        self.repository.create_audit_event(
            create_audit_event(
                workspace_id=workspace.workspace_id,
                actor=actor or "system",
                action="import_workspace_bundle",
                target_type="workspace",
                target_id=workspace.workspace_id,
            )
        )

        return WorkspaceImportResult(
            workspace_id=workspace.workspace_id,
            counts={
                "connection_profiles": len(connection_profiles),
                "graph_profiles": len(graph_profiles),
                "source_documents": len(source_documents),
                "requirement_interviews": len(requirement_interviews),
                "requirement_versions": len(requirement_versions),
                "analysis_epochs": len(analysis_epochs),
                "analysis_executions": len(analysis_executions),
                "workflow_runs": len(workflow_runs),
                "use_cases": len(use_cases),
                "analysis_templates": len(analysis_templates),
                "reports": report_count,
                "report_sections": section_count,
                "chart_specs": chart_count,
                "published_snapshots": snapshot_count,
                "audit_events": audit_count,
            },
        )

    def publish_report(self, report_id: str, actor: str) -> ReportBundle:
        """Publish a report and record an immutable snapshot plus audit event."""

        manifest = self.repository.get_report_manifest(report_id)
        sections = self.repository.list_report_sections(report_id)
        charts = self.repository.list_chart_specs(report_id)
        rendered_snapshot = {
            "manifest": manifest.to_dict(),
            "sections": [section.to_dict() for section in sections],
            "charts": [chart.to_dict() for chart in charts],
        }
        content_hash = self._content_hash(rendered_snapshot)

        snapshot = create_published_snapshot(
            workspace_id=manifest.workspace_id,
            report_id=manifest.report_id,
            title=manifest.title,
            published_by=actor,
            content_hash=content_hash,
            rendered_snapshot=rendered_snapshot,
        )
        self.repository.create_published_snapshot(snapshot)

        manifest.status = ReportStatus.PUBLISHED
        manifest.published_snapshot_id = snapshot.published_snapshot_id
        self.repository.update_report_manifest(manifest)

        audit_event = create_audit_event(
            workspace_id=manifest.workspace_id,
            actor=actor,
            action="publish_report",
            target_type="report",
            target_id=manifest.report_id,
            details={"published_snapshot_id": snapshot.published_snapshot_id},
        )
        self.repository.create_audit_event(audit_event)

        return self._report_bundle(manifest, sections, charts, [snapshot])

    def _report_bundle(
        self,
        manifest: ReportManifest,
        sections: List[ReportSection],
        charts: List[ChartSpec],
        snapshots: List[PublishedSnapshot],
    ) -> ReportBundle:
        return ReportBundle(
            manifest=manifest.to_dict(),
            sections=[section.to_dict() for section in sections],
            charts=[chart.to_dict() for chart in charts],
            snapshots=[snapshot.to_dict() for snapshot in snapshots],
        )

    #: FR-37 — which run-level artifact each canonical agentic step produces.
    #: Keyed by canonical step id (see ``_build_canonical_agentic_dag``); the
    #: value is the ``WorkflowRun`` attribute and the ref ``type`` the UI
    #: routes on. ``use_case_generation`` has no entry because the run record
    #: does not carry use-case ids.
    _STEP_ARTIFACT_SOURCES: Dict[str, Tuple[str, str]] = {
        "schema_analysis": ("graph_profile_id", "graph_profile"),
        "requirements_extraction": ("requirement_version_id", "requirement_version"),
        "template_generation": ("template_ids", "analysis_template"),
        "execution": ("analysis_execution_ids", "analysis_execution"),
        "reporting": ("report_ids", "report"),
    }

    def _derive_step_artifact_refs(
        self, step: WorkflowStep, run: WorkflowRun
    ) -> List[Dict[str, str]]:
        """Attribute the run's artifacts to the step that produced them.

        FR-37 requires step details to link to what the step produced, but
        nothing in the product code ever wrote ``WorkflowStep.artifact_refs``
        — only tests did. Every step therefore reported "Artifacts: 0" for
        every real run, which is exactly the test-only shape the drift policy
        calls deceptive.

        The run itself already records what was produced (`report_ids`,
        `analysis_execution_ids`, and friends), and the canonical agentic DAG
        fixes which step produces which kind (FR-31a), so the mapping is
        knowable without the executor writing anything. Derive it here rather
        than backfilling stored rows, so runs completed before this existed
        also link correctly.

        Explicitly stored refs always win: a future executor that records real
        per-step provenance should not be second-guessed by this fallback.
        """

        source = self._STEP_ARTIFACT_SOURCES.get(step.step_id)
        if source is None:
            return []

        attribute, ref_type = source
        value = getattr(run, attribute, None)
        if value:
            ids = value if isinstance(value, list) else [value]
            return [
                {"type": ref_type, "id": artifact_id}
                for artifact_id in ids
                if artifact_id
            ]

        # The run's own back-references are not always filled in — the seeded
        # AdTech run has ten reports and an empty `report_ids`. Reports and
        # executions each carry `run_id` themselves, so that reverse edge is
        # the linkage that actually exists; fall back to it and label reports
        # with their titles, which beats showing the user a bare UUID.
        if ref_type == "report":
            return [
                {"type": ref_type, "id": report.report_id, "label": report.title}
                for report in self.repository.list_report_manifests(run.workspace_id)
                if getattr(report, "run_id", None) == run.run_id
            ]

        if ref_type == "analysis_execution":
            return [
                {"type": ref_type, "id": execution.analysis_execution_id}
                for execution in self.repository.list_analysis_executions(
                    run.workspace_id
                )
                if getattr(execution, "run_id", None) == run.run_id
            ]

        return []

    def _workflow_step_node(
        self, step: WorkflowStep, run: Optional[WorkflowRun] = None
    ) -> Dict[str, Any]:
        node = step.to_dict()
        node["id"] = step.step_id
        if run is not None and not node.get("artifact_refs"):
            refs = self._derive_step_artifact_refs(step, run)
            if refs:
                node["artifact_refs"] = refs
                # artifact_count is what the panel headlines; keep it honest.
                node["artifact_count"] = len(refs)
        return node

    def _workflow_edge(self, edge: WorkflowDAGEdge) -> Dict[str, Any]:
        return {
            "from": edge.from_step_id,
            "to": edge.to_step_id,
            **edge.to_dict(),
        }

    def _validate_workflow_dag(
        self,
        steps: List[WorkflowStep],
        dag_edges: List[WorkflowDAGEdge],
    ) -> None:
        step_ids = {step.step_id for step in steps}
        if len(step_ids) != len(steps):
            raise ValidationError("Workflow steps must have unique step_id values")
        for edge in dag_edges:
            if edge.from_step_id not in step_ids:
                raise ValidationError(
                    f"Workflow edge references missing from_step_id: {edge.from_step_id}"
                )
            if edge.to_step_id not in step_ids:
                raise ValidationError(
                    f"Workflow edge references missing to_step_id: {edge.to_step_id}"
                )

    def _find_workflow_step(self, run: WorkflowRun, step_id: str) -> WorkflowStep:
        for step in run.steps:
            if step.step_id == step_id:
                return step
        raise ValidationError(f"Workflow step not found: {step_id}")

    def _roll_up_workflow_run_status(self, run: WorkflowRun) -> None:
        statuses = [step.status for step in run.steps]
        if any(status == WorkflowStepStatus.FAILED for status in statuses):
            run.status = WorkflowRunStatus.FAILED
            run.completed_at = current_timestamp()
        elif any(status == WorkflowStepStatus.PAUSED for status in statuses):
            run.status = WorkflowRunStatus.PAUSED
        elif statuses and all(
            status in {WorkflowStepStatus.COMPLETED, WorkflowStepStatus.SKIPPED}
            for status in statuses
        ):
            run.status = WorkflowRunStatus.COMPLETED
            run.completed_at = current_timestamp()
        elif any(status == WorkflowStepStatus.RUNNING for status in statuses):
            run.status = WorkflowRunStatus.RUNNING
            run.started_at = run.started_at or current_timestamp()
        else:
            run.status = WorkflowRunStatus.QUEUED

    def _export_connection_profile(
        self,
        profile: ConnectionProfile,
    ) -> Dict[str, Any]:
        doc = profile.to_dict()
        secret_ref_keys = sorted(doc.get("secret_refs", {}).keys())
        doc.pop("secret_refs", None)
        doc["secret_ref_keys"] = secret_ref_keys
        return doc

    def _validate_workspace_bundle(self, bundle_doc: Dict[str, Any]) -> None:
        required_keys = {
            "schema_version",
            "workspace",
            "connection_profiles",
            "graph_profiles",
            "source_documents",
            "requirement_interviews",
            "requirement_versions",
            "workflow_runs",
            "reports",
        }
        missing = sorted(required_keys - set(bundle_doc.keys()))
        if missing:
            raise ValidationError(
                f"Workspace bundle is missing required keys: {', '.join(missing)}"
            )

        # PRD v0.6 bumped PRODUCT_SCHEMA_VERSION 1.0.0 → 1.1.0 (added the
        # aga_schema_snapshots collection — additive only, no breaking
        # changes to existing collections). Bundles emitted by 1.0.0
        # callers are forward-compatible: the new collection simply
        # remains empty until the next discover_graph_profile call
        # populates it. Accept the open set of supported versions so
        # exporters can keep emitting 1.0.0 during the transition.
        if bundle_doc["schema_version"] not in _SUPPORTED_BUNDLE_SCHEMA_VERSIONS:
            raise ValidationError(
                "Unsupported workspace bundle schema version: "
                f"{bundle_doc['schema_version']}"
            )

        workspace_id = bundle_doc["workspace"].get("workspace_id") or bundle_doc[
            "workspace"
        ].get("_key")
        if not workspace_id:
            raise ValidationError("Workspace bundle is missing workspace_id")

        for profile in bundle_doc.get("connection_profiles", []):
            if "secret_refs" in profile:
                raise ValidationError(
                    "Workspace bundle imports must not include connection secret_refs"
                )

        for collection_name in [
            "connection_profiles",
            "graph_profiles",
            "source_documents",
            "requirement_interviews",
            "requirement_versions",
            "analysis_epochs",
            "analysis_executions",
            "workflow_runs",
        ]:
            for item in bundle_doc.get(collection_name, []):
                self._validate_workspace_id(collection_name, item, workspace_id)

        for report in bundle_doc.get("reports", []):
            if "manifest" not in report:
                raise ValidationError("Workspace bundle report is missing manifest")
            self._validate_workspace_id(
                "reports.manifest", report["manifest"], workspace_id
            )
            for section in report.get("sections", []):
                self._validate_workspace_id("reports.sections", section, workspace_id)
            for chart in report.get("charts", []):
                self._validate_workspace_id("reports.charts", chart, workspace_id)
            for snapshot in report.get("snapshots", []):
                self._validate_workspace_id("reports.snapshots", snapshot, workspace_id)

        for event in bundle_doc.get("audit_events", []):
            self._validate_workspace_id("audit_events", event, workspace_id)

    def _validate_workspace_id(
        self,
        collection_name: str,
        item: Dict[str, Any],
        workspace_id: str,
    ) -> None:
        item_workspace_id = item.get("workspace_id")
        if item_workspace_id != workspace_id:
            raise ValidationError(
                f"Workspace bundle item in {collection_name} has mismatched "
                f"workspace_id: {item_workspace_id}"
            )

    def _mask_secret(self, message: str, secret_value: str) -> str:
        if not secret_value:
            return message
        return message.replace(secret_value, "***MASKED***")

    def _select_graph_name(
        self,
        schema: GraphSchema,
        requested_graph_name: Optional[str],
        fallback_name: str,
    ) -> str:
        if requested_graph_name:
            if schema.graph_names and requested_graph_name not in schema.graph_names:
                raise ValidationError(
                    f"Graph '{requested_graph_name}' was not found in database"
                )
            return requested_graph_name
        if schema.graph_names:
            return schema.graph_names[0]
        return fallback_name

    def _schema_edge_definitions(self, schema: GraphSchema) -> List[Dict[str, Any]]:
        definitions = []
        for relationship in schema.relationships:
            definitions.append(
                {
                    "edge_collection": relationship.edge_collection,
                    "from_vertex_collections": [relationship.from_collection],
                    "to_vertex_collections": [relationship.to_collection],
                    "edge_count": relationship.edge_count,
                    "relationship_type": relationship.relationship_type,
                }
            )
        return definitions

    def _scope_to_named_graph(
        self,
        db: Any,
        schema: GraphSchema,
        graph_name: str,
    ) -> Dict[str, Any]:
        """Resolve the vertex/edge collections for a named graph.

        Falls back to the full schema if the graph is the database name (no real
        named graph) or if the driver cannot fetch graph metadata.
        """

        scope: Dict[str, Any] = {
            "vertex_collections": list(schema.vertex_collections.keys()),
            "edge_collections": list(schema.edge_collections.keys()),
            "edge_definitions": [],
            "scope": "database",
            "total_documents": schema.total_documents,
            "total_edges": schema.total_edges,
        }

        if not graph_name or graph_name not in (schema.graph_names or []):
            return scope

        try:
            graph_handle = db.graph(graph_name)
            edge_definitions = list(graph_handle.edge_definitions() or [])
        except Exception:
            return scope

        try:
            orphan_collections = list(graph_handle.orphan_collections() or [])
        except Exception:
            orphan_collections = []

        vertex_collections, edge_collections = _collections_from_edge_definitions(
            edge_definitions, orphan_collections
        )
        if not vertex_collections and not edge_collections:
            return scope

        total_documents = _safe_collection_total(db, vertex_collections)
        total_edges = _safe_collection_total(db, edge_collections)

        scope.update(
            {
                "vertex_collections": vertex_collections,
                "edge_collections": edge_collections,
                "edge_definitions": edge_definitions,
                "scope": "named_graph",
                "total_documents": (
                    total_documents if total_documents is not None else 0
                )
                + sum(
                    schema.document_collections[name].document_count
                    for name in schema.document_collections
                    if name in orphan_collections
                ),
                "total_edges": total_edges if total_edges is not None else 0,
            }
        )
        return scope

    def _workspace_health_issues(
        self,
        connection_profiles: List[ConnectionProfile],
        graph_profiles: List[GraphProfile],
        requirement_versions: List[RequirementVersion],
        workflow_runs: List[WorkflowRun],
        reports: List[ReportManifest],
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        if not connection_profiles:
            issues.append(
                {
                    "severity": "warning",
                    "code": "missing_connection_profile",
                    "message": "Workspace has no connection profiles.",
                }
            )
        if not graph_profiles:
            issues.append(
                {
                    "severity": "warning",
                    "code": "missing_graph_profile",
                    "message": "Workspace has no discovered graph profiles.",
                }
            )
        if not requirement_versions:
            issues.append(
                {
                    "severity": "info",
                    "code": "missing_requirement_version",
                    "message": "Workspace has no approved or draft requirement versions.",
                }
            )

        failed_connections = [
            profile.connection_profile_id
            for profile in connection_profiles
            if profile.last_verification_status == ConnectionVerificationStatus.FAILED
        ]
        if failed_connections:
            issues.append(
                {
                    "severity": "error",
                    "code": "failed_connection_verification",
                    "message": "One or more connection profiles failed verification.",
                    "entity_ids": failed_connections,
                }
            )

        failed_runs = [
            run.run_id
            for run in workflow_runs
            if run.status == WorkflowRunStatus.FAILED
        ]
        if failed_runs:
            issues.append(
                {
                    "severity": "warning",
                    "code": "failed_workflow_runs",
                    "message": "One or more workflow runs failed.",
                    "entity_ids": failed_runs,
                }
            )

        draft_reports = [
            report.report_id
            for report in reports
            if report.status == ReportStatus.DRAFT
        ]
        if draft_reports:
            issues.append(
                {
                    "severity": "info",
                    "code": "draft_reports",
                    "message": "Workspace has draft reports that are not published.",
                    "entity_ids": draft_reports,
                }
            )
        return issues

    def _schema_observations_from_graph_profile(
        self,
        graph_profile: GraphProfile,
    ) -> Dict[str, Any]:
        """Build the schema context the Requirements Copilot reasons over.

        FR-72: when the profile carries a v0.6 conceptual schema
        (FR-62) the copilot should reason about *logical* entity and
        relationship types rather than raw collection names — on an LPG
        graph the collection list is often just ``Entities`` /
        ``Relationships``, which tells a business user nothing. The
        graph purpose (FR-67) and any cross-graph links declared on a
        GraphSet containing this profile (FR-68/69) are included for the
        same reason.

        Raw collection lists are always kept: they remain the only
        signal for profiles discovered before v0.6, and the draft
        builder falls back to them when no conceptual schema exists.
        """

        observations: Dict[str, Any] = {
            "graph_name": graph_profile.graph_name,
            "vertex_collections": graph_profile.vertex_collections,
            "edge_collections": graph_profile.edge_collections,
            "edge_definitions": graph_profile.edge_definitions,
            "collection_roles": graph_profile.collection_roles,
            "counts": graph_profile.counts,
            "schema_summary": graph_profile.metadata.get("schema_summary", {}),
        }

        if graph_profile.graph_purpose:
            observations["graph_purpose"] = graph_profile.graph_purpose

        conceptual = graph_profile.conceptual_schema or {}
        entities = conceptual.get("entities") or []
        relationships = conceptual.get("relationships") or []
        if entities or relationships:
            observations["entity_types"] = [
                name
                for name in (
                    entity.get("name")
                    for entity in entities
                    if isinstance(entity, dict)
                )
                if name
            ]
            observations["relationship_types"] = [
                {
                    "type": rel.get("type"),
                    "from": rel.get("fromEntity", "Any"),
                    "to": rel.get("toEntity", "Any"),
                }
                for rel in relationships
                if isinstance(rel, dict) and rel.get("type")
            ]
            observations["entity_type_count"] = len(observations["entity_types"])
            observations["relationship_type_count"] = len(
                observations["relationship_types"]
            )

        cross_graph_links = self._cross_graph_links_for_profile(graph_profile)
        if cross_graph_links:
            observations["cross_graph_links"] = cross_graph_links

        # FR-65c: surface the inferred tenant key so Copilot questions can be
        # tenant-scoped, and warn when an analysis would span tenants.
        analyzer_metadata = graph_profile.analyzer_metadata or {}
        multitenancy = analyzer_metadata.get("multitenancy") or {}
        if multitenancy.get("is_multitenant"):
            observations["multitenancy"] = {
                "tenant_key": multitenancy.get("tenant_key"),
                "note": (
                    "This deployment is sharded by "
                    f"{multitenancy.get('tenant_key')!r}. Scope questions to a "
                    "single tenant unless a cross-tenant view is intended."
                ),
            }
        sharding_profile = analyzer_metadata.get("sharding_profile") or {}
        if sharding_profile.get("deployment_kind") not in (None, "", "unknown"):
            observations["deployment_kind"] = sharding_profile["deployment_kind"]

        return observations

    def _cross_graph_links_for_profile(
        self,
        graph_profile: GraphProfile,
    ) -> List[Dict[str, Any]]:
        """Cross-graph links (FR-68/69) touching this profile, best-effort.

        Returns an empty list when the workspace has no GraphSets, when
        none of them contain this profile, or when the repository does
        not expose GraphSets at all — the copilot must still start for
        workspaces that never built one.
        """

        try:
            graph_sets = self.repository.list_graph_sets(graph_profile.workspace_id)
        except Exception:  # noqa: BLE001 — copilot must not fail on this
            return []

        links: List[Dict[str, Any]] = []
        for graph_set in graph_sets or []:
            if graph_profile.graph_profile_id not in (
                graph_set.graph_profile_ids or []
            ):
                continue
            for link in graph_set.cross_graph_links or []:
                links.append(
                    {
                        "graph_set": graph_set.name,
                        "from_graph_profile_id": link.from_graph_profile_id,
                        "to_graph_profile_id": link.to_graph_profile_id,
                        "from_field": link.from_field,
                        "to_field": link.to_field,
                    }
                )
        return links

    def _requirements_copilot_questions(
        self,
        schema_observations: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        graph_name = schema_observations.get("graph_name", "the graph")
        return [
            {
                "id": "business_goal",
                "text": f"What business decision should {graph_name} support?",
                "provenance": "user_provided",
            },
            {
                "id": "analytics_questions",
                "text": "What graph analytics questions should the system answer?",
                "provenance": "user_provided",
            },
            {
                "id": "audience",
                "text": "Who will consume the report and what level of detail do they need?",
                "provenance": "user_provided",
            },
            {
                "id": "constraints",
                "text": "What runtime, cost, freshness, sensitivity, or evidence constraints apply?",
                "provenance": "user_provided",
            },
        ]

    def _build_requirements_draft(
        self,
        interview: RequirementInterview,
        answer_map: Dict[str, str],
    ) -> str:
        observations = interview.schema_observations
        vertex_collections = (
            ", ".join(observations.get("vertex_collections", [])) or "None observed"
        )
        edge_collections = (
            ", ".join(observations.get("edge_collections", [])) or "None observed"
        )
        domain = interview.domain or "Unspecified domain"

        # FR-72: lead with logical entity/relationship types when the
        # profile carries a conceptual schema — on an LPG graph the raw
        # collection names ("Entities"/"Relationships") are meaningless
        # to a business reviewer. Raw collections stay below either way.
        schema_lines: List[str] = [
            f"- Graph: {observations.get('graph_name', interview.graph_profile_id)}"
        ]
        if observations.get("graph_purpose"):
            schema_lines.append(f"- Graph purpose: {observations['graph_purpose']}")

        entity_types = observations.get("entity_types") or []
        relationship_types = observations.get("relationship_types") or []
        if entity_types:
            schema_lines.append(
                f"- Entity types ({len(entity_types)}): {', '.join(entity_types)}"
            )
        if relationship_types:
            rendered_relationships = ", ".join(
                f"{rel['type']} ({rel['from']}→{rel['to']})"
                for rel in relationship_types
            )
            schema_lines.append(
                f"- Relationship types ({len(relationship_types)}): "
                f"{rendered_relationships}"
            )

        schema_lines.extend(
            [
                f"- Vertex collections: {vertex_collections}",
                f"- Edge collections: {edge_collections}",
                f"- Counts: {json.dumps(observations.get('counts', {}), sort_keys=True)}",
            ]
        )

        cross_graph_links = observations.get("cross_graph_links") or []
        if cross_graph_links:
            schema_lines.append(
                f"- Cross-graph links ({len(cross_graph_links)}): "
                + ", ".join(
                    f"{link['from_field']}→{link['to_field']} "
                    f"(via {link['graph_set']})"
                    for link in cross_graph_links
                )
            )

        return "\n".join(
            [
                "# Business Requirements Draft",
                "",
                "## Domain",
                domain,
                "",
                "## Observed Graph Schema",
                *schema_lines,
                "",
                "## Business Goal",
                answer_map.get("business_goal", "[Needs user input]"),
                "",
                "## Analytics Questions",
                answer_map.get("analytics_questions", "[Needs user input]"),
                "",
                "## Reporting Audience",
                answer_map.get("audience", "[Needs user input]"),
                "",
                "## Constraints",
                answer_map.get("constraints", "[Needs user input]"),
                "",
                "## Assumptions To Confirm",
                "- Generated requirements should be reviewed before use-case or template generation.",
                "- Graph schema observations may need business terminology refinement.",
            ]
        )

    def _build_requirements_provenance(
        self,
        interview: RequirementInterview,
        answer_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        labels = [
            {"path": "observed_schema.graph_name", "label": "observed_from_schema"},
            {
                "path": "observed_schema.vertex_collections",
                "label": "observed_from_schema",
            },
            {
                "path": "observed_schema.edge_collections",
                "label": "observed_from_schema",
            },
            {"path": "assumptions.review_required", "label": "assumption"},
        ]
        for question_id in sorted(answer_map):
            labels.append(
                {
                    "path": f"answers.{question_id}",
                    "label": "user_provided",
                }
            )
        if interview.domain:
            labels.append({"path": "domain", "label": "user_provided"})
        return labels

    def _answer_for_question(
        self,
        interview: RequirementInterview,
        question_id: str,
    ) -> str:
        for answer in interview.answers:
            if answer.get("question_id") == question_id:
                return answer.get("answer", "")
        return ""

    def _requirement_items_from_answer(
        self,
        interview: RequirementInterview,
        question_id: str,
        prefix: str,
    ) -> List[Dict[str, Any]]:
        answer = self._answer_for_question(interview, question_id)
        if not answer:
            return []
        items = [
            item.strip(" -")
            for item in answer.replace(";", "\n").split("\n")
            if item.strip(" -")
        ]
        return [
            {
                "id": f"{prefix}-{index}",
                "text": item,
                "source": "requirements_copilot",
            }
            for index, item in enumerate(items, start=1)
        ]

    def _content_hash(self, payload: Dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
