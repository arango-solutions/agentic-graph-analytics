"""Connection profiles that use the platform login (NFR-24 (b), (c)).

A profile whose password reference is ``{"kind": "platform_login"}`` stores no
secret: during a request the database is opened as the signed-in platform
user, and a background run acts as the user who started it, each on a token
the integration sidecar mints for them. The fake platform
(``tests/platform_fake.py``) is a real HTTP server, so the handles are real
python-arango objects and each request's acting user is observable.
"""

import importlib.util
import os
import sys

import pytest

from graph_analytics_ai import platform_auth
from graph_analytics_ai.product import (
    MappingSecretResolver,
    ProductService,
    create_connection_profile,
    create_graph_profile,
    create_workspace,
)
from graph_analytics_ai.product.agentic_run_supervisor import AgenticRunSupervisor
from graph_analytics_ai.product.exceptions import (
    AccessDeniedError,
    LoginRequiredError,
    ValidationError,
)
from graph_analytics_ai.product.models import (
    ConnectionVerificationStatus,
    DeploymentMode,
    WorkflowMode,
    WorkflowStep,
)
from graph_analytics_ai.product.profile_connection import (
    STARTED_BY_KEY,
    open_profile_database,
    platform_login_ref,
)

from .platform_fake import PLATFORM_ENV, FakePlatform

# The established in-memory repository from the service tests
# (tests/unit/product is not a package, so it is loaded by path).
_spec = importlib.util.spec_from_file_location(
    "_test_service_for_platform_profiles",
    os.path.join(os.path.dirname(__file__), "unit", "product", "test_service.py"),
)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
FakeProductRepository = _module.FakeProductRepository


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in PLATFORM_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(platform_auth, "_identities", {})
    monkeypatch.setattr(platform_auth, "_ca_files", {})
    monkeypatch.setattr(platform_auth, "_user_sources", {})
    monkeypatch.setattr(platform_auth, "_user_databases", {})


@pytest.fixture
def fake(monkeypatch):
    platform = FakePlatform()
    platform.readers["sales"] = {"alice"}
    monkeypatch.setenv("INTEGRATION_HTTP_ADDRESS_FULL", platform.address)
    monkeypatch.setenv("ARANGO_DEPLOYMENT_ENDPOINT", platform.address)
    yield platform
    platform.close()


def as_user(signed_in, fn, *args, **kwargs):
    """Run *fn* as if serving a request signed in as *signed_in*."""
    caller = platform_auth.PlatformCaller(signed_in) if signed_in else None
    return platform_auth.context_for(caller).run(fn, *args, **kwargs)


class _RecordingConnector:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return object()


def _seed(secret_refs, database="sales"):
    repository = FakeProductRepository()
    connector = _RecordingConnector()
    service = ProductService(
        repository=repository,
        secret_resolver=MappingSecretResolver({"PW": "s3cret"}),
        db_connector=connector,
    )
    workspace = create_workspace(
        customer_name="Acme", project_name="Risk", environment="dev"
    )
    repository.create_workspace(workspace)
    profile = create_connection_profile(
        workspace_id=workspace.workspace_id,
        name="sales",
        deployment_mode=DeploymentMode.SELF_MANAGED,
        endpoint="https://platform.example",
        database=database,
        username="alice",
        secret_refs=secret_refs,
    )
    repository.create_connection_profile(profile)
    return service, repository, connector, workspace, profile


PLATFORM_REFS = {"password": platform_login_ref()}


class TestOpenProfileDatabase:
    def test_a_platform_profile_acts_as_the_signed_in_user(self, fake):
        service, _, connector, _, profile = _seed(PLATFORM_REFS)
        opened = as_user("alice", service._open_profile, profile)
        opened.db.properties()
        assert opened.user == "alice" and opened.secret is None
        assert fake.user_of_last_request() == "alice"
        assert {"lifetime": "3600s", "user": "alice"} in fake.minted
        assert connector.calls == []  # no password path

    def test_a_user_without_access_is_refused_by_name(self, fake):
        service, _, _, _, profile = _seed(PLATFORM_REFS)
        with pytest.raises(
            AccessDeniedError, match="'bob' cannot use database 'sales'"
        ):
            as_user("bob", service._open_profile, profile)

    def test_without_a_signed_in_user_login_is_required(self, fake):
        service, _, _, _, profile = _seed(PLATFORM_REFS)
        with pytest.raises(LoginRequiredError):
            service._open_profile(profile)
        assert fake.minted == []

    def test_off_the_platform_a_platform_profile_is_refused(self):
        service, _, _, _, profile = _seed(PLATFORM_REFS)
        with pytest.raises(ValidationError, match="only available"):
            as_user("alice", service._open_profile, profile)

    def test_an_explicit_user_wins_over_the_request(self, fake):
        _, _, connector, _, profile = _seed(PLATFORM_REFS)
        opened = as_user(
            "bob",
            open_profile_database,
            profile,
            secret_resolver=None,
            db_connector=connector,
            user="alice",
        )
        assert opened.user == "alice"

    def test_password_profiles_are_unchanged(self, fake):
        service, _, connector, _, profile = _seed(
            {"password": {"kind": "env", "ref": "PW"}}
        )
        opened = as_user("alice", service._open_profile, profile, verify_system=False)
        assert opened.secret == "s3cret" and opened.user == "alice"
        assert connector.calls == [
            {
                "endpoint": "https://platform.example",
                "username": "alice",
                "password": "s3cret",
                "database": "sales",
                "verify_ssl": True,
                "verify_system": False,
            }
        ]
        assert fake.minted == []


class TestService:
    def test_defaults_offer_the_platform_login_on_the_platform(self, fake):
        service, *_ = _seed(PLATFORM_REFS)
        defaults = as_user("alice", service.get_connection_defaults)
        assert defaults["login"] == "platform"
        assert defaults["username"] == "alice"
        assert defaults["secret_ref"] == {"kind": "platform_login"}

    def test_defaults_off_the_platform_are_the_password(self):
        service, *_ = _seed(PLATFORM_REFS)
        defaults = service.get_connection_defaults()
        assert defaults["login"] == "password" and "secret_ref" not in defaults

    def test_zero_config_lists_the_databases_the_user_can_use(self, fake):
        service, *_ = _seed(PLATFORM_REFS)
        result = as_user("alice", service.list_default_cluster_databases)
        assert result["databases"] == ["aga_workspace", "sales"]
        assert result["username"] == "alice" and result["login"] == "platform"
        assert fake.user_of_last_request() == "alice"

    def test_zero_config_without_a_login_is_401_material(self, fake):
        service, *_ = _seed(PLATFORM_REFS)
        with pytest.raises(LoginRequiredError):
            service.list_default_cluster_databases()

    def test_verifying_records_success_for_a_reader(self, fake, monkeypatch):
        service, repository, _, _, profile = _seed(PLATFORM_REFS)
        monkeypatch.setattr(
            ProductService,
            "_check_gae_access",
            staticmethod(lambda: {"status": "skipped"}),
        )
        result = as_user(
            "alice", service.verify_connection_profile, profile.connection_profile_id
        )
        assert result.status == ConnectionVerificationStatus.SUCCESS.value
        stored = repository.get_connection_profile(profile.connection_profile_id)
        assert stored.last_verification_status == ConnectionVerificationStatus.SUCCESS

    def test_verifying_records_failure_naming_the_missing_grant(
        self, fake, monkeypatch
    ):
        service, _, _, _, profile = _seed(PLATFORM_REFS)
        monkeypatch.setattr(
            ProductService,
            "_check_gae_access",
            staticmethod(lambda: {"status": "skipped"}),
        )
        result = as_user(
            "bob", service.verify_connection_profile, profile.connection_profile_id
        )
        assert result.status == ConnectionVerificationStatus.FAILED.value
        assert "'bob' cannot use database 'sales'" in result.error_message

    def test_verifying_without_a_login_is_not_a_profile_failure(self, fake):
        service, repository, _, _, profile = _seed(PLATFORM_REFS)
        with pytest.raises(LoginRequiredError):
            service.verify_connection_profile(profile.connection_profile_id)
        stored = repository.get_connection_profile(profile.connection_profile_id)
        assert stored.last_verification_status == ConnectionVerificationStatus.UNKNOWN


def _seed_run(secret_refs):
    service, repository, connector, workspace, profile = _seed(secret_refs)
    graph_profile = create_graph_profile(
        workspace_id=workspace.workspace_id,
        connection_profile_id=profile.connection_profile_id,
        graph_name="sales-graph",
    )
    repository.create_graph_profile(graph_profile)
    run = service.create_workflow_run_from_steps(
        workspace_id=workspace.workspace_id,
        workflow_mode=WorkflowMode.AGENTIC,
        steps=[WorkflowStep(step_id="s", label="Find anomalies")],
        dag_edges=[],
        graph_profile_id=graph_profile.graph_profile_id,
    )
    return service, repository, run


class TestRuns:
    def test_starting_a_run_records_who_started_it(self, fake):
        service, repository, run = _seed_run(PLATFORM_REFS)
        as_user("alice", service.start_workflow_run, run.run_id)
        stored = repository.get_workflow_run(run.run_id)
        assert stored.metadata[STARTED_BY_KEY] == "alice"
        audit = [e for e in repository.audit_events if e.action == "start_workflow_run"]
        assert audit[-1].metadata["platform_user"] == "alice"

    def test_off_the_platform_no_starter_is_recorded(self):
        service, repository, run = _seed_run(PLATFORM_REFS)
        service.start_workflow_run(run.run_id)
        assert STARTED_BY_KEY not in repository.get_workflow_run(run.run_id).metadata

    def test_a_background_run_acts_as_its_starter(self, fake):
        service, repository, run = _seed_run(PLATFORM_REFS)
        as_user("alice", service.start_workflow_run, run.run_id)
        supervisor = AgenticRunSupervisor(service=service, max_workers=1)
        try:
            # No request context here: the run must name its own user.
            db = supervisor._build_db_connection(
                repository.get_workflow_run(run.run_id)
            )
            db.properties()
        finally:
            supervisor.shutdown()
        assert fake.user_of_last_request() == "alice"

    def test_a_run_started_without_a_platform_user_says_so(self, fake):
        service, repository, run = _seed_run(PLATFORM_REFS)
        supervisor = AgenticRunSupervisor(service=service, max_workers=1)
        try:
            with pytest.raises(RuntimeError, match="without a signed-in platform user"):
                supervisor._build_db_connection(repository.get_workflow_run(run.run_id))
        finally:
            supervisor.shutdown()
        assert fake.minted == []
