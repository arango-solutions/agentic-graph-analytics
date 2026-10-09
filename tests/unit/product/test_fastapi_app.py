"""Unit tests for the optional FastAPI product adapter."""

import asyncio
import sys
import types
from typing import Any, Dict

import pytest

from graph_analytics_ai.product.api import PRODUCT_API_ENDPOINTS
from graph_analytics_ai.product.fastapi_app import create_product_fastapi_app


class FakeFastAPI:
    def __init__(self, title, version, lifespan=None):
        # Accept ``lifespan`` so the fixture stays compatible with the
        # FR-31a app factory which passes one to register the agentic
        # supervisor's startup-sweep / shutdown-drain hooks.
        self.title = title
        self.version = version
        self.lifespan = lifespan
        self.routes = []
        self.middlewares = []
        self.exception_handlers: Dict[type, Any] = {}

    def add_api_route(self, path, endpoint, methods, summary, tags):
        self.routes.append(
            {
                "path": path,
                "endpoint": endpoint,
                "methods": methods,
                "summary": summary,
                "tags": tags,
            }
        )

    def get(self, path, **kwargs):
        """Support the ``@app.get(...)`` decorator form.

        The factory registers /healthz this way rather than through the
        contract dispatcher, so that a health check can still report the
        running version when the database is unreachable.
        """

        def decorator(endpoint):
            self.routes.append(
                {
                    "path": path,
                    "endpoint": endpoint,
                    "methods": ["GET"],
                    "summary": (
                        (endpoint.__doc__ or "").strip().splitlines()[0]
                        if endpoint.__doc__
                        else ""
                    ),
                    "tags": kwargs.get("tags", []),
                }
            )
            return endpoint

        return decorator

    def add_middleware(self, middleware_class, **kwargs):
        # The factory unconditionally adds CORSMiddleware. Recording
        # rather than ignoring lets future tests assert on it.
        self.middlewares.append({"class": middleware_class, "kwargs": kwargs})

    def add_exception_handler(self, exc_type, handler):
        # FR-31a AC#8: the factory wires ConflictError → 409 etc.
        # Recording the handlers lets tests assert the mapping is
        # registered without standing up real FastAPI.
        self.exception_handlers[exc_type] = handler


class _FakeHeaders(dict):
    """Like Starlette's ``Headers``: a mapping with case-insensitive keys."""

    def __init__(self, values=None):
        super().__init__({k.lower(): v for k, v in (values or {}).items()})

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class FakeRequest:
    def __init__(
        self, method, path_params=None, query_params=None, body=None, headers=None
    ):
        self.method = method
        self.path_params = path_params or {}
        self.query_params = query_params or {}
        self.body = body or {}
        self.headers = _FakeHeaders(headers)

    async def json(self):
        return self.body


class _FakeCORSMiddleware:
    """Stand-in for ``fastapi.middleware.cors.CORSMiddleware``.

    The factory only references the class — never instantiates it
    itself — so an empty placeholder is sufficient for the fake
    ``add_middleware`` to record.
    """


@pytest.fixture
def fake_fastapi_module(monkeypatch):
    fastapi_module = types.ModuleType("fastapi")
    fastapi_module.FastAPI = FakeFastAPI
    fastapi_module.Request = FakeRequest
    # The factory imports ``fastapi.Response`` from the route-handler
    # closure. A minimal placeholder keeps the import resolvable.
    fastapi_module.Response = type("FakeResponse", (), {})
    monkeypatch.setitem(sys.modules, "fastapi", fastapi_module)

    # The real ``from fastapi.middleware.cors import CORSMiddleware``
    # resolves through fastapi.middleware.cors. Build that submodule
    # tree on the fake so the import succeeds without pulling real
    # FastAPI into the test process.
    middleware_module = types.ModuleType("fastapi.middleware")
    cors_module = types.ModuleType("fastapi.middleware.cors")
    cors_module.CORSMiddleware = _FakeCORSMiddleware
    middleware_module.cors = cors_module
    fastapi_module.middleware = middleware_module
    monkeypatch.setitem(sys.modules, "fastapi.middleware", middleware_module)
    monkeypatch.setitem(sys.modules, "fastapi.middleware.cors", cors_module)

    # FR-31a AC#8: the factory now does ``from fastapi.responses
    # import JSONResponse`` to build the exception-handler bodies.
    # A trivial stand-in is enough — the exception handler tests
    # only need to invoke it, not unwrap real FastAPI semantics.
    class _FakeJSONResponse:
        def __init__(self, status_code: int, content: Dict[str, Any]):
            self.status_code = status_code
            self.content = content

    responses_module = types.ModuleType("fastapi.responses")
    responses_module.JSONResponse = _FakeJSONResponse
    fastapi_module.responses = responses_module
    monkeypatch.setitem(sys.modules, "fastapi.responses", responses_module)

    return fastapi_module


def test_create_product_fastapi_app_registers_contract_routes(fake_fastapi_module):
    """FastAPI adapter registers every product API contract endpoint."""

    app = create_product_fastapi_app(service=object(), title="Product API", version="1")

    route_keys = {(route["methods"][0], route["path"]) for route in app.routes}
    contract_keys = {
        (endpoint.method, endpoint.path) for endpoint in PRODUCT_API_ENDPOINTS
    }

    assert app.title == "Product API"
    assert app.version == "1"
    # /healthz is intentionally outside the contract: it bypasses the
    # dispatcher so it can still report the running version when the database
    # is unreachable, which is when a deploy most needs identifying. Asserted
    # separately rather than folded into the contract, so a genuinely
    # unexpected route still fails this test.
    assert ("GET", "/healthz") in route_keys
    assert ("GET", "/platform/diagnostics") in route_keys
    ops_routes = {("GET", "/healthz"), ("GET", "/platform/diagnostics")}
    assert route_keys - ops_routes == contract_keys


def test_create_product_fastapi_app_can_bootstrap_default_service(fake_fastapi_module):
    """FastAPI adapter can build the product service from a database connector."""

    database = object()
    app = create_product_fastapi_app(
        db_connector=lambda: database,
        auto_initialize=False,
    )

    assert app.routes


def test_product_fastapi_route_dispatches_request(fake_fastapi_module):
    """Generated route handler delegates to the service dispatcher."""

    class Service:
        def get_workspace_overview(self, workspace_id, recent_limit):
            return {
                "workspace_id": workspace_id,
                "recent_limit": recent_limit,
            }

    app = create_product_fastapi_app(service=Service())
    route = next(
        route
        for route in app.routes
        if route["path"] == "/api/workspaces/{workspace_id}/overview"
    )
    request = FakeRequest(
        method="GET",
        path_params={"workspace_id": "workspace-1"},
        query_params={"recent_limit": "3"},
    )

    response = asyncio.run(route["endpoint"](request))

    assert response == {"workspace_id": "workspace-1", "recent_limit": "3"}


def test_product_fastapi_route_dispatches_as_the_signed_in_platform_user(
    fake_fastapi_module, monkeypatch
):
    """NFR-24: the service sees who is signed in, although it runs in an
    executor thread, and only for that request."""

    from graph_analytics_ai import platform_auth
    from graph_analytics_ai.product import fastapi_app

    seen_headers = []

    def identify(authorization):
        seen_headers.append(authorization)
        return platform_auth.PlatformCaller("alice") if authorization else None

    monkeypatch.setattr(fastapi_app, "identify_caller", identify)

    class Service:
        def get_workspace_overview(self, workspace_id, recent_limit=10):
            caller = platform_auth.current_caller()
            return {"user": caller.user if caller else None}

    app = create_product_fastapi_app(service=Service())
    route = next(
        route
        for route in app.routes
        if route["path"] == "/api/workspaces/{workspace_id}/overview"
    )
    signed_in = FakeRequest(
        method="GET",
        path_params={"workspace_id": "w"},
        headers={"Authorization": "bearer token"},
    )
    anonymous = FakeRequest(method="GET", path_params={"workspace_id": "w"})

    assert asyncio.run(route["endpoint"](signed_in)) == {"user": "alice"}
    assert asyncio.run(route["endpoint"](anonymous)) == {"user": None}
    assert seen_headers == ["bearer token", None]
    assert platform_auth.current_caller() is None


def test_create_product_fastapi_app_wires_supervisor_when_explicitly_enabled(
    fake_fastapi_module,
):
    """``enable_agentic_supervisor=True`` injects a supervisor + lifespan hooks."""

    class _Service:
        """Minimal service stub — the factory only assigns the supervisor on it."""

        _agentic_run_supervisor = None

    service = _Service()
    app = create_product_fastapi_app(service=service, enable_agentic_supervisor=True)

    # Supervisor must be assigned to the service so start_workflow_run /
    # cancel_workflow_run / get_workflow_run_status route through it.
    assert service._agentic_run_supervisor is not None
    supervisor = service._agentic_run_supervisor

    # Lifespan must be registered. We exercise it end-to-end so the
    # startup-sweep + shutdown-drain hooks are pinned, not just present.
    assert app.lifespan is not None
    sweep_calls: list = []
    shutdown_calls: list = []
    monkey_supervisor_orig_shutdown = supervisor.shutdown

    def record_sweep(*args, **kwargs):
        sweep_calls.append((args, kwargs))
        return []

    def record_shutdown(*args, **kwargs):
        shutdown_calls.append((args, kwargs))
        return monkey_supervisor_orig_shutdown(*args, **kwargs)

    supervisor.sweep_orphan_runs = record_sweep
    supervisor.shutdown = record_shutdown

    async def drive():
        async with app.lifespan(app):
            pass

    asyncio.run(drive())

    assert sweep_calls, "lifespan startup must call sweep_orphan_runs"
    assert shutdown_calls, "lifespan shutdown must drain the supervisor pool"
    assert shutdown_calls[0][1].get("wait") is False, (
        "shutdown should be non-blocking so the API process exits "
        "promptly even if a worker is mid-step"
    )


def test_create_product_fastapi_app_skips_supervisor_when_disabled(fake_fastapi_module):
    """When the supervisor is off, no hook is registered and the service is untouched."""

    class _Service:
        _agentic_run_supervisor = None

    service = _Service()
    app = create_product_fastapi_app(service=service, enable_agentic_supervisor=False)

    assert service._agentic_run_supervisor is None
    # Lifespan still exists (it's always passed) but exercising it must
    # be a no-op — no sweep, no shutdown — when the supervisor was not
    # constructed.
    assert app.lifespan is not None

    async def drive():
        async with app.lifespan(app):
            pass

    asyncio.run(drive())  # would raise if the lifespan tried to call None.sweep


def test_create_product_fastapi_app_resolves_supervisor_from_env(
    fake_fastapi_module, monkeypatch
):
    """``AGA_ENABLE_AGENTIC_SUPERVISOR=1`` enables the supervisor without an arg."""

    monkeypatch.setenv("AGA_ENABLE_AGENTIC_SUPERVISOR", "1")

    class _Service:
        _agentic_run_supervisor = None

    service = _Service()
    app = create_product_fastapi_app(service=service)

    assert service._agentic_run_supervisor is not None

    # Drain so the worker pool doesn't leak between tests.
    service._agentic_run_supervisor.shutdown(wait=False)
    # Reference ``app`` so the linter sees the test's intent of asserting the
    # factory didn't raise even when the arg is left to env-resolution.
    assert app.lifespan is not None


def test_create_product_fastapi_app_env_off_keeps_supervisor_disabled(
    fake_fastapi_module, monkeypatch
):
    """Falsy env values (``""``, ``"0"``, ``"off"``) leave the supervisor disabled."""

    monkeypatch.setenv("AGA_ENABLE_AGENTIC_SUPERVISOR", "0")

    class _Service:
        _agentic_run_supervisor = None

    service = _Service()
    app = create_product_fastapi_app(service=service)

    assert service._agentic_run_supervisor is None
    assert app.lifespan is not None


def test_create_product_fastapi_app_registers_domain_exception_handlers(
    fake_fastapi_module,
):
    """ConflictError → 409 (FR-31a AC#8) + the rest of the contract mapping."""

    from graph_analytics_ai.product.exceptions import (
        AccessDeniedError,
        ConflictError,
        DuplicateError,
        LoginRequiredError,
        NotFoundError,
        ValidationError,
    )

    app = create_product_fastapi_app(service=object())

    # Every product domain exception must have a handler registered.
    expected = {
        ValidationError: 400,
        NotFoundError: 404,
        ConflictError: 409,
        DuplicateError: 409,
        LoginRequiredError: 401,  # NFR-24: no usable platform login
        AccessDeniedError: 403,  # NFR-24: the platform user lacks access
    }
    for exc_type, status_code in expected.items():
        assert exc_type in app.exception_handlers, (
            f"{exc_type.__name__} must be wired as an exception handler "
            f"so HTTP callers see {status_code}, not 500"
        )
        # Drive the handler with a dummy request and assert it
        # returns a JSONResponse with the right status + a body that
        # explains what happened.
        handler = app.exception_handlers[exc_type]
        response = asyncio.run(handler(object(), exc_type("boom")))
        assert response.status_code == status_code
        assert response.content == {
            "error": exc_type.__name__,
            "detail": "boom",
        }


def test_create_product_fastapi_app_explains_missing_dependency(monkeypatch):
    """FastAPI adapter gives a clear message when the optional extra is absent."""

    monkeypatch.delitem(sys.modules, "fastapi", raising=False)

    class MissingFastAPIBlocker:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "fastapi":
                raise ImportError("blocked fastapi import")
            return None

    blocker = MissingFastAPIBlocker()
    monkeypatch.setattr(sys, "meta_path", [blocker])

    with pytest.raises(ImportError, match="graph-analytics-ai\\[api\\]"):
        create_product_fastapi_app(service=object())
