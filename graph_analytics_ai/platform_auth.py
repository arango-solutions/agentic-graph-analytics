"""Platform login on the Arango platform (Container Manager / BYOC).

The platform injects into the pod:

- ``ARANGO_DEPLOYMENT_ENDPOINT``: the in-cluster coordinator URL;
- ``ARANGO_DEPLOYMENT_CA``: the CA that signs that endpoint's certificate (a
  file path, or the PEM text);
- an integration sidecar (``INTEGRATION_HTTP_ADDRESS[_FULL]``) that names the
  user a token belongs to (``/_integration/authn/v1/identity``) and mints
  tokens for a named user (``/_integration/authn/v1/createToken``).

The workspace's own records (``aga_workspace``) are reached as a dedicated
service account (``AGA_SERVICE_USER``, default ``aga-service``) on a token the
sidecar mints, so no password has to be baked into the bundle. The token is
renewed before it expires (:class:`SidecarTokenSource`,
:func:`open_renewing_database`). A token is never minted without a named user:
the sidecar would default to root.

Adapted from arango-cypher-py's ``arango_cypher/service/platform_auth.py`` and
arango-embedding-loader's ``backend/app/auth.py``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Optional, Union

import requests

DEPLOYMENT_ENDPOINT_ENV = "ARANGO_DEPLOYMENT_ENDPOINT"
DEPLOYMENT_CA_ENV = "ARANGO_DEPLOYMENT_CA"
SIDECAR_ADDRESS_ENVS = ("INTEGRATION_HTTP_ADDRESS_FULL", "INTEGRATION_HTTP_ADDRESS")
PLATFORM_AUTH_ENV = "AGA_PLATFORM_AUTH"
SERVICE_USER_ENV = "AGA_SERVICE_USER"
DEFAULT_SERVICE_USER = "aga-service"
TOKEN_LIFETIME_ENV = "AGA_SIDECAR_TOKEN_LIFETIME_S"
PLATFORM_CA_BUNDLE_ENV = "AGA_PLATFORM_CA_BUNDLE"
PLATFORM_VERIFY_TLS_ENV = "AGA_PLATFORM_VERIFY_TLS"

_DISABLED = frozenset({"off", "0", "false", "no"})
_ENABLED = frozenset({"on", "1", "true", "yes"})
_TIMEOUT_S = 5.0
_DEFAULT_LIFETIME_S = 3600
#: Renew a minted token this long before it expires.
_REFRESH_MARGIN_S = 300

logger = logging.getLogger(__name__)


class SidecarError(Exception):
    """The integration sidecar could not answer. Never contains a token."""


def platform_endpoint() -> Optional[str]:
    """The injected coordinator URL, or ``None`` off the platform or when
    ``AGA_PLATFORM_AUTH=off``."""
    if os.getenv(PLATFORM_AUTH_ENV, "auto").strip().lower() in _DISABLED:
        return None
    value = os.getenv(DEPLOYMENT_ENDPOINT_ENV, "").strip().rstrip("/")
    return value or None


_ca_lock = threading.Lock()
_ca_files: dict[str, str] = {}


def deployment_ca() -> Optional[str]:
    """A file holding the injected CA, or ``None``. PEM text is written once
    to a private temporary file, since TLS libraries take a path."""
    value = os.getenv(DEPLOYMENT_CA_ENV, "").strip()
    if not value:
        return None
    if "-----BEGIN" not in value:
        if os.path.isfile(value):
            return value
        logger.warning(
            "%s names no file (%s); the endpoint is not verified",
            DEPLOYMENT_CA_ENV,
            value,
        )
        return None
    digest = hashlib.sha256(value.encode()).hexdigest()
    with _ca_lock:
        path = _ca_files.get(digest)
        if path is None or not os.path.isfile(path):
            fd, path = tempfile.mkstemp(prefix="arango-deployment-ca-", suffix=".pem")
            with os.fdopen(fd, "w") as handle:
                handle.write(value if value.endswith("\n") else value + "\n")
            _ca_files[digest] = path
        return path


def platform_tls_verify() -> Union[bool, str]:
    """How to verify the injected endpoint: a configured bundle, an explicit
    on/off, the injected CA, or no verification when none was injected."""
    bundle = os.getenv(PLATFORM_CA_BUNDLE_ENV, "").strip()
    if bundle:
        return bundle
    mode = os.getenv(PLATFORM_VERIFY_TLS_ENV, "auto").strip().lower()
    if mode in _ENABLED:
        return True
    if mode in _DISABLED:
        return False
    return deployment_ca() or False


def describe_tls_verify(verify: Union[bool, str]) -> str:
    if isinstance(verify, str):
        if verify == deployment_ca():
            return f"verified against the injected CA ({DEPLOYMENT_CA_ENV})"
        return f"verified against {PLATFORM_CA_BUNDLE_ENV}"
    return "verified against the system trust store" if verify else "not verified"


def forwarded_token(authorization: Optional[str]) -> Optional[str]:
    """The JWT in an ``Authorization: bearer <jwt>`` header, or ``None``."""
    value = authorization or ""
    if value[:7].lower() != "bearer ":
        return None
    return value[7:].strip() or None


def sidecar_address() -> Optional[str]:
    for name in SIDECAR_ADDRESS_ENVS:
        value = os.getenv(name, "").strip().rstrip("/")
        if value:
            return value if "://" in value else f"http://{value}"
    return None


def service_user() -> str:
    return os.getenv(SERVICE_USER_ENV, "").strip() or DEFAULT_SERVICE_USER


def token_lifetime_s() -> int:
    raw = os.getenv(TOKEN_LIFETIME_ENV, "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_LIFETIME_S
    except ValueError:
        logger.warning(
            "%s=%r is not a number of seconds; using %s",
            TOKEN_LIFETIME_ENV,
            raw,
            _DEFAULT_LIFETIME_S,
        )
        return _DEFAULT_LIFETIME_S
    return max(_REFRESH_MARGIN_S + 60, value)


_identity_lock = threading.Lock()
_identities: dict[str, str] = {}
_MAX_IDENTITIES = 1000


def sidecar_identity(token: str) -> Optional[str]:
    """The user *token* belongs to, as the sidecar (which validates it) says;
    ``None`` off the platform or when unknown. Cached by the token's hash."""
    address = sidecar_address()
    if address is None:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    with _identity_lock:
        if key in _identities:
            return _identities[key]
    try:
        resp = requests.get(
            f"{address}/_integration/authn/v1/identity",
            headers={"Authorization": f"bearer {token}"},
            timeout=_TIMEOUT_S,
        )
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "integration sidecar identity lookup failed: %s", exc.__class__.__name__
        )
        return None
    if resp.status_code != 200:
        logger.warning(
            "integration sidecar identity lookup answered HTTP %s", resp.status_code
        )
        return None
    try:
        user = resp.json().get("user")
    except ValueError:
        return None
    if not isinstance(user, str) or not user:
        return None
    with _identity_lock:
        if len(_identities) >= _MAX_IDENTITIES:
            _identities.clear()
        _identities[key] = user
    return user


def sidecar_token(user: str, lifetime_s: int) -> str:
    """A new token for *user*, minted by the integration sidecar. Refuses an
    empty user: the sidecar would mint one for its default (root) account."""
    if not user:
        raise SidecarError("refusing to mint a token without a user")
    address = sidecar_address()
    if address is None:
        raise SidecarError("no integration sidecar is configured")
    try:
        resp = requests.post(
            f"{address}/_integration/authn/v1/createToken",
            json={"lifetime": f"{lifetime_s}s", "user": user},
            timeout=_TIMEOUT_S,
        )
    except requests.exceptions.RequestException as exc:
        raise SidecarError(
            f"the integration sidecar did not answer ({exc.__class__.__name__})"
        ) from exc
    if resp.status_code != 200:
        raise SidecarError(f"the integration sidecar answered HTTP {resp.status_code}")
    try:
        token = resp.json().get("token")
    except ValueError as exc:
        raise SidecarError(
            "the integration sidecar answered with something other than JSON"
        ) from exc
    if not isinstance(token, str) or not token:
        raise SidecarError("the integration sidecar answered without a token")
    return token


def _expires_at(token: str) -> Optional[float]:
    try:
        payload = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except (IndexError, ValueError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    return float(exp) if isinstance(exp, (int, float)) else None


class SidecarTokenSource:
    """Tokens for one named user, minted by the sidecar and renewed shortly
    before they expire. Thread-safe; the token itself is never logged."""

    def __init__(self, user: str, lifetime_s: Optional[int] = None) -> None:
        if not user:
            raise SidecarError("refusing to mint a token without a user")
        self.user = user
        self.lifetime_s = lifetime_s or token_lifetime_s()
        self._token: Optional[str] = None
        self._renew_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() >= self._renew_at:
                self._renew()
            assert self._token is not None
            return self._token

    def refresh(self) -> str:
        """A new token, after the server rejected the current one."""
        with self._lock:
            self._renew()
            assert self._token is not None
            return self._token

    def _renew(self) -> None:
        token = sidecar_token(self.user, self.lifetime_s)
        expires = _expires_at(token) or time.time() + self.lifetime_s
        self._token = token
        self._renew_at = expires - _REFRESH_MARGIN_S


def open_renewing_database(
    endpoint: str,
    database: str,
    source: SidecarTokenSource,
    verify: Union[bool, str] = True,
    request_timeout: Optional[float] = None,
) -> Any:
    """A database handle whose token comes from *source* before every request,
    and again once if the server answers 401.

    Built on python-arango's ``JwtSuperuserConnection``, which sends a token
    as-is (the server checks it and applies that user's permissions; nothing
    beyond the token's own rights is granted). Its constructor takes the
    client's internals, which python-arango 8.x has; if they are missing the
    handle uses one token without renewal, and says so.
    """
    from arango import ArangoClient
    from arango.connection import JwtSuperuserConnection
    from arango.database import StandardDatabase

    kwargs: dict[str, Any] = {"hosts": endpoint, "verify_override": verify}
    if request_timeout is not None:
        kwargs["request_timeout"] = request_timeout
    client = ArangoClient(**kwargs)

    class _RenewingConnection(JwtSuperuserConnection):
        def send_request(self, request):  # type: ignore[override]
            self._auth_header = f"bearer {source.token()}"
            response = super().send_request(request)
            if response.status_code == 401:
                self._auth_header = f"bearer {source.refresh()}"
                response = super().send_request(request)
            return response

    try:
        connection = _RenewingConnection(
            hosts=client._hosts,
            host_resolver=client._host_resolver,
            sessions=client._sessions,
            db_name=database,
            http_client=client._http,
            serializer=client._serializer,
            deserializer=client._deserializer,
            superuser_token=source.token(),
            request_compression=client._request_compression,
            response_compression=client._response_compression,
        )
    except (AttributeError, TypeError) as exc:
        logger.warning(
            "python-arango has no client internals for token renewal (%s); "
            "the %s token will not be renewed",
            exc.__class__.__name__,
            source.user,
        )
        return client.db(database, superuser_token=source.token())
    return StandardDatabase(connection)


def endpoint_answer(endpoint: str, token: str, verify: Union[bool, str]) -> str:
    """What one direct ``GET /_api/version`` with *token* gets: ``HTTP <code>``,
    or why it failed. Never includes the token."""
    try:
        resp = requests.get(
            f"{endpoint}/_api/version",
            headers={"Authorization": f"bearer {token}"},
            timeout=_TIMEOUT_S,
            verify=verify,
        )
    except requests.exceptions.SSLError as exc:
        return f"TLS verification failed ({exc.__class__.__name__})"
    except requests.exceptions.Timeout:
        return f"no answer within {_TIMEOUT_S:g}s"
    except requests.exceptions.ConnectionError as exc:
        return f"connection failed ({str(exc).replace(token, '<token>')[:200]})"
    except requests.exceptions.RequestException as exc:
        return f"request failed ({exc.__class__.__name__})"
    return f"HTTP {resp.status_code}"


def token_facts(token: str) -> dict:
    """Claim names and lifetime of a JWT, never a claim value or the token."""
    try:
        header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
        payload = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except (IndexError, ValueError):
        return {"parsable": False}
    if not isinstance(claims, dict) or not isinstance(header, dict):
        return {"parsable": False}
    exp, iat = claims.get("exp"), claims.get("iat")
    numeric = (int, float)
    return {
        "parsable": True,
        "alg": header.get("alg"),
        "iss": claims.get("iss"),
        "claims": sorted(claims),
        "lifetime_s": (
            exp - iat if isinstance(exp, numeric) and isinstance(iat, numeric) else None
        ),
        "expires_in_s": round(exp - time.time()) if isinstance(exp, numeric) else None,
    }
