"""
ArangoDB Connection Helper

Provides a unified interface to connect to ArangoDB clusters.
"""

import logging
import os

from arango import ArangoClient

from .config import get_arango_config, parse_ssl_verify

# Default HTTP request timeout (seconds) for ArangoDB calls. python-arango's
# own default is 60s, which is too low for large graphs / busier clusters
# (a full agentic run against a multi-million-node graph can make queries
# that exceed a minute). Overridable via ARANGO_TIMEOUT.
DEFAULT_ARANGO_REQUEST_TIMEOUT = 300

logger = logging.getLogger(__name__)

#: How the workspace store connected, for ``GET /platform/diagnostics``.
#: ``mode`` is "service account via sidecar" or "password"; never a secret.
WORKSPACE_LOGIN: dict = {"mode": "not connected"}


def _resolve_request_timeout(request_timeout=None):
    """Resolve the HTTP request timeout from arg → ARANGO_TIMEOUT → default."""
    if request_timeout is not None:
        return request_timeout
    raw = os.getenv("ARANGO_TIMEOUT")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return DEFAULT_ARANGO_REQUEST_TIMEOUT


def connect_arango_database(
    endpoint,
    username,
    password,
    database,
    verify_ssl=True,
    verify_system=True,
    client_factory=None,
    request_timeout=None,
):
    """
    Connect to an explicit ArangoDB database descriptor.

    This helper is intended for UI connection profiles, where the profile is
    stored as non-secret metadata and the password is resolved at runtime from
    a secret reference. It does not read global environment configuration.

    Args:
        endpoint: ArangoDB endpoint URL.
        username: Database username.
        password: Resolved password or token.
        database: Target database name.
        verify_ssl: SSL verification setting, either bool or string.
        verify_system: If True, verify credentials against _system first.
        client_factory: Test seam for injecting an ArangoClient-compatible type.

    Returns:
        StandardDatabase: ArangoDB database connection.
    """
    verify = parse_ssl_verify(verify_ssl) if isinstance(verify_ssl, str) else verify_ssl
    if client_factory is None:
        client_factory = ArangoClient
    timeout = _resolve_request_timeout(request_timeout)
    try:
        client = client_factory(hosts=endpoint, request_timeout=timeout)
    except TypeError:
        # Test doubles / older client factories may not accept request_timeout.
        client = client_factory(hosts=endpoint)

    if verify_system:
        sys_db = client.db(
            "_system", username=username, password=password, verify=verify
        )
        try:
            sys_db.version()
            print(f"✓ Successfully connected to ArangoDB at {endpoint}")
        except Exception as e:
            error_msg = str(e).replace(str(password), "***MASKED***")
            error_str = str(e).lower()
            if (
                "401" in error_str
                or "not authorized" in error_str
                or "err 11" in error_str
            ):
                enhanced_msg = (
                    f"Failed to connect to ArangoDB: {error_msg}\n\n"
                    f"Authorization Error Detected\n\n"
                    f"This error means the server rejected your credentials or permissions.\n\n"
                    f"Common causes:\n"
                    f"  1. User doesn't have access to _system database (limited users)\n"
                    f"  2. Wrong username or password\n"
                    f"  3. Password has extra spaces (check .env file)\n"
                    f"  4. Endpoint missing port :8529\n\n"
                    f"Troubleshooting:\n"
                    f"  1. Verify credentials in .env file (no spaces, no quotes)\n"
                    f"  2. Check endpoint includes port: ARANGO_ENDPOINT=https://hostname:8529\n"
                    f"  3. Verify credentials work in web UI\n"
                    f"  4. For limited users, connect directly to target database (skip _system)\n"
                )
                raise ConnectionError(enhanced_msg)
            raise ConnectionError(f"Failed to connect to ArangoDB: {error_msg}")

        try:
            available_dbs = sys_db.databases()
            if available_dbs and isinstance(available_dbs[0], dict):
                db_names = [db["name"] for db in available_dbs]
            else:
                db_names = available_dbs
            if database not in db_names:
                raise ValueError(
                    f"Database '{database}' does not exist on this cluster. "
                    f"Available: {db_names}"
                )
        except ValueError:
            raise
        except Exception as e:
            error_str = str(e).lower()
            if (
                "401" in error_str
                or "not authorized" in error_str
                or "err 11" in error_str
            ):
                print(
                    "Warning: Cannot list databases (user may have limited permissions)"
                )
                print(f"   Attempting direct connection to '{database}' database...")
            else:
                print(f"Warning: Could not verify database existence: {e}")

    db = client.db(database, username=username, password=password, verify=verify)
    print(f"✓ Connected to database: {database}")
    return db


def _platform_workspace_connection():
    """The workspace store as the service account, on a sidecar-minted token.

    On the Arango platform (injected endpoint and integration sidecar) the
    workspace's own records are reached as ``AGA_SERVICE_USER`` (default
    ``aga-service``), whose token the sidecar mints and the connection renews,
    so no password is needed. ``None`` off the platform, or when that fails
    (then the configured password is used, as before, and the failure is
    reported by ``GET /platform/diagnostics``).
    """
    from .platform_auth import (
        SidecarTokenSource,
        open_renewing_database,
        platform_endpoint,
        platform_tls_verify,
        service_user,
        sidecar_address,
    )

    endpoint = platform_endpoint()
    if endpoint is None or sidecar_address() is None:
        return None
    database = os.getenv("ARANGO_DATABASE", "").strip() or "aga_workspace"
    user = service_user()
    try:
        db = open_renewing_database(
            endpoint,
            database,
            SidecarTokenSource(user),
            verify=platform_tls_verify(),
            request_timeout=_resolve_request_timeout(),
        )
        db.properties()  # the token works and the account can open the database
    except Exception as exc:  # noqa: BLE001 — reported, then the password path runs
        WORKSPACE_LOGIN.clear()
        WORKSPACE_LOGIN.update(
            mode="password (service account failed)",
            user=user,
            database=database,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        logger.warning(
            "Workspace login as %s via the sidecar failed: %s",
            user,
            WORKSPACE_LOGIN["error"],
        )
        return None
    WORKSPACE_LOGIN.clear()
    WORKSPACE_LOGIN.update(
        mode="service account via sidecar", user=user, database=database
    )
    logger.info("Workspace store connected as %s on a sidecar-minted token", user)
    return db


def get_db_connection():
    """
    Establish connection to ArangoDB cluster.

    On the Arango platform the workspace store is reached as a service account
    on a sidecar-minted token (see :func:`_platform_workspace_connection`);
    otherwise, or when that fails, with the configured password.

    Returns:
        StandardDatabase: ArangoDB database connection

    Raises:
        ValueError: If required credentials are missing
        ConnectionError: If connection fails
    """
    platform_db = _platform_workspace_connection()
    if platform_db is not None:
        return platform_db

    # Get configuration from environment
    config = get_arango_config()

    endpoint = config["endpoint"]
    username = config["user"]
    password = config["password"]
    database = config["database"]
    verify_ssl = parse_ssl_verify(config["verify_ssl"])

    db = connect_arango_database(
        endpoint=endpoint,
        username=username,
        password=password,
        database=database,
        verify_ssl=verify_ssl,
    )
    if WORKSPACE_LOGIN.get("mode") != "password (service account failed)":
        WORKSPACE_LOGIN.clear()
        WORKSPACE_LOGIN.update(mode="password", user=username, database=database)
    return db


def get_connection_info():
    """
    Get connection information without establishing a connection.

    Returns:
        dict: Connection configuration details
    """
    config = get_arango_config()

    return {
        "endpoint": config["endpoint"],
        "database": config["database"],
        "user": config["user"],
        "verify_ssl": config["verify_ssl"],
    }
