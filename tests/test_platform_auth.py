"""Platform login (graph_analytics_ai/platform_auth.py, db_connection.py).

On the Arango platform the workspace store is reached as a service account on
a token the integration sidecar mints, renewed before it expires, so no
password is baked. One real HTTP server on localhost plays the sidecar
(``/_integration/authn/v1/*``) and the coordinator (``/_api/version`` and the
database's ``/_api/database/current``); database handles are real
python-arango objects (``tests/platform_fake.py``).
"""

import json
from unittest.mock import patch

import pytest

from graph_analytics_ai import db_connection, platform_auth

from .platform_fake import FORWARDED, PLATFORM_ENV, FakePlatform
from .platform_fake import make_jwt as _jwt

PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in PLATFORM_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(platform_auth, "_identities", {})
    monkeypatch.setattr(platform_auth, "_ca_files", {})
    db_connection.WORKSPACE_LOGIN.clear()
    db_connection.WORKSPACE_LOGIN["mode"] = "not connected"


@pytest.fixture
def fake(monkeypatch):
    platform = FakePlatform()
    monkeypatch.setenv("INTEGRATION_HTTP_ADDRESS_FULL", platform.address)
    monkeypatch.setenv("ARANGO_DEPLOYMENT_ENDPOINT", platform.address)
    yield platform
    platform.close()


class TestHelpers:
    def test_forwarded_token(self):
        assert platform_auth.forwarded_token("bearer abc") == "abc"
        assert platform_auth.forwarded_token("Bearer abc") == "abc"
        assert platform_auth.forwarded_token("Basic abc") is None
        assert platform_auth.forwarded_token(None) is None

    def test_endpoint_is_injected_only_and_can_be_switched_off(self, monkeypatch):
        assert platform_auth.platform_endpoint() is None
        monkeypatch.setenv("ARANGO_DEPLOYMENT_ENDPOINT", "https://c.svc:8529/")
        assert platform_auth.platform_endpoint() == "https://c.svc:8529"
        monkeypatch.setenv("AGA_PLATFORM_AUTH", "off")
        assert platform_auth.platform_endpoint() is None

    def test_the_injected_ca_verifies_the_endpoint(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.crt"
        ca.write_text(PEM)
        monkeypatch.setenv("ARANGO_DEPLOYMENT_CA", str(ca))
        assert platform_auth.platform_tls_verify() == str(ca)
        monkeypatch.setenv("ARANGO_DEPLOYMENT_CA", PEM)
        path = platform_auth.platform_tls_verify()
        assert (
            open(path).read() == PEM + "\n"
            and platform_auth.platform_tls_verify() == path
        )
        monkeypatch.setenv("AGA_PLATFORM_VERIFY_TLS", "off")
        assert platform_auth.platform_tls_verify() is False

    def test_service_user_defaults_to_aga_service(self, monkeypatch):
        assert platform_auth.service_user() == "aga-service"
        monkeypatch.setenv("AGA_SERVICE_USER", "other-svc")
        assert platform_auth.service_user() == "other-svc"

    def test_token_facts_show_shape_not_values(self):
        facts = platform_auth.token_facts(FORWARDED)
        assert facts["claims"] == ["exp", "iat", "iss", "preferred_username"]
        assert "alice" not in json.dumps(facts)


class TestSidecar:
    def test_identity_names_the_caller(self, fake):
        assert platform_auth.sidecar_identity(FORWARDED) == "alice"
        assert platform_auth.sidecar_identity(_jwt(sub="nobody")) is None

    def test_never_mints_without_a_user(self, fake):
        with pytest.raises(platform_auth.SidecarError, match="without a user"):
            platform_auth.sidecar_token("", 600)
        with pytest.raises(platform_auth.SidecarError, match="without a user"):
            platform_auth.SidecarTokenSource("")
        assert fake.minted == []

    def test_a_token_source_mints_once_and_renews_before_expiry(self, fake):
        source = platform_auth.SidecarTokenSource("aga-service", lifetime_s=3600)
        first = source.token()
        assert source.token() == first and len(fake.minted) == 1
        fake.expire_after = 60  # inside the renewal margin, so the next call renews
        source.refresh()
        second = source.token()
        third = source.token()
        assert second != first and third != second
        assert all(
            m == {"lifetime": "3600s", "user": "aga-service"} for m in fake.minted
        )


class TestRenewingDatabase:
    def test_each_request_carries_the_current_token(self, fake):
        source = platform_auth.SidecarTokenSource("aga-service")
        db = platform_auth.open_renewing_database(
            fake.address, "aga_workspace", source, verify=False
        )
        db.properties()
        assert fake.users[fake.seen_tokens[-1]] == "aga-service"

    def test_a_rejected_token_is_renewed_and_the_request_retried(self, fake):
        source = platform_auth.SidecarTokenSource("aga-service")
        db = platform_auth.open_renewing_database(
            fake.address, "aga_workspace", source, verify=False
        )
        stale = source.token()
        del fake.users[stale]  # the server no longer accepts it
        db.properties()
        assert fake.seen_tokens[-2] == stale and fake.seen_tokens[-1] != stale
        assert len(fake.minted) == 2


class TestWorkspaceConnection:
    def test_on_the_platform_the_workspace_is_the_service_account(
        self, fake, monkeypatch
    ):
        monkeypatch.setenv("ARANGO_DATABASE", "aga_workspace")
        with patch.object(db_connection, "connect_arango_database") as password_path:
            db = db_connection.get_db_connection()
        password_path.assert_not_called()
        assert db.name == "aga_workspace"
        assert db_connection.WORKSPACE_LOGIN == {
            "mode": "service account via sidecar",
            "user": "aga-service",
            "database": "aga_workspace",
        }

    def test_when_the_sidecar_refuses_the_password_is_used_and_the_failure_reported(
        self, fake, monkeypatch
    ):
        fake.fail_create = True
        monkeypatch.setenv("ARANGO_ENDPOINT", "https://cluster.example:8529")
        monkeypatch.setenv("ARANGO_PASSWORD", "pw")
        monkeypatch.setenv("ARANGO_DATABASE", "aga_workspace")
        sentinel = object()
        with patch.object(
            db_connection, "connect_arango_database", return_value=sentinel
        ):
            assert db_connection.get_db_connection() is sentinel
        assert (
            db_connection.WORKSPACE_LOGIN["mode"] == "password (service account failed)"
        )
        assert "HTTP 500" in db_connection.WORKSPACE_LOGIN["error"]

    def test_off_the_platform_the_password_is_used(self, monkeypatch):
        monkeypatch.setenv("ARANGO_ENDPOINT", "https://cluster.example:8529")
        monkeypatch.setenv("ARANGO_PASSWORD", "pw")
        monkeypatch.setenv("ARANGO_DATABASE", "aga_workspace")
        sentinel = object()
        with patch.object(
            db_connection, "connect_arango_database", return_value=sentinel
        ):
            assert db_connection.get_db_connection() is sentinel
        assert db_connection.WORKSPACE_LOGIN["mode"] == "password"


class TestDiagnostics:
    def test_reports_what_the_platform_provides_without_secrets(self, fake):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from graph_analytics_ai.product.fastapi_app import create_product_fastapi_app

        db_connection.WORKSPACE_LOGIN.update(
            mode="service account via sidecar", user="aga-service"
        )
        app = create_product_fastapi_app(
            service=object(), enable_agentic_supervisor=False
        )
        response = TestClient(app).get(
            "/platform/diagnostics", headers={"Authorization": f"bearer {FORWARDED}"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["endpoint"]["in_use"] is True
        assert body["workspace_login"]["mode"] == "service account via sidecar"
        assert body["tls"]["direct_request"] == "HTTP 200"
        assert body["sidecar"]["identity_found"] is True
        assert FORWARDED not in response.text and "alice" not in response.text
