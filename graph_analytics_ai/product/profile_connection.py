"""Open the database a connection profile names (FR-6, NFR-24).

A profile's ``secret_refs["password"]`` says how to log in:

- ``{"kind": "env", "ref": "VAR"}`` — a password, resolved at runtime by the
  service's :class:`~graph_analytics_ai.product.secrets.SecretResolver`;
- ``{"kind": "platform_login"}`` — no secret at all: on the Arango platform
  the database is opened as the signed-in user (during a request) or as the
  user who started a run (in the background), on a token the integration
  sidecar mints for them. Whoever acts gets their own permissions, so one
  profile serves everyone in a workspace without sharing an account.

Every place that connects to a profile goes through :func:`open_profile_database`
so the two kinds cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from ..platform_auth import (
    SidecarError,
    current_caller,
    open_user_database,
    platform_endpoint,
)
from .exceptions import AccessDeniedError, LoginRequiredError, ValidationError

PLATFORM_LOGIN_KIND = "platform_login"
# Key in WorkflowRun.metadata naming the platform user who started the run,
# whom a background run acts as.
STARTED_BY_KEY = "started_by"


def platform_login_ref() -> Dict[str, str]:
    """The secret reference of a profile that uses the platform login."""
    return {"kind": PLATFORM_LOGIN_KIND}


def is_platform_login(secret_ref: Any) -> bool:
    """Whether *secret_ref* means "the platform login". Any other shape a
    resolver accepts (a mapping of another kind, an opaque string) is not."""
    return (
        isinstance(secret_ref, Mapping)
        and secret_ref.get("kind") == PLATFORM_LOGIN_KIND
    )


def uses_platform_login(profile: Any, secret_key: str = "password") -> bool:
    return is_platform_login(
        (getattr(profile, "secret_refs", None) or {}).get(secret_key)
    )


def acting_user(explicit: Optional[str] = None) -> Optional[str]:
    """Who a platform-login profile acts as: *explicit* (a run's starter), else
    the signed-in user of the request being served."""
    if explicit:
        return explicit
    caller = current_caller()
    return caller.user if caller else None


@dataclass
class ProfileDatabase:
    """An open database plus what was used to open it.

    ``secret`` is the resolved password (for masking it out of error text),
    ``None`` for the platform login; ``user`` is who the handle acts as.
    """

    db: Any
    user: str
    secret: Optional[str] = None


def _http_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "http_code", None)
    return code if isinstance(code, int) else None


def open_as_platform_user(
    database: str, user: Optional[str], *, check_access: bool = True
) -> Any:
    """*database* opened as *user* through the platform login.

    Raises :class:`LoginRequiredError` without a user, :class:`ValidationError`
    off the platform or when the sidecar cannot mint a token, and (when
    *check_access*) :class:`AccessDeniedError` if the user may not use the
    database, so a missing grant reads as such rather than as a driver error.
    """
    if platform_endpoint() is None:
        raise ValidationError(
            "This connection uses the platform login, which is only available "
            "when the service runs on the Arango platform"
        )
    if not user:
        raise LoginRequiredError(
            "This connection uses your platform login, and this request carried "
            "none. Sign in to the platform and reload."
        )
    try:
        db = open_user_database(database, user)
    except SidecarError as exc:
        raise ValidationError(
            f"Could not get a platform login for user {user!r}: {exc}"
        ) from exc
    if check_access:
        try:
            db.properties()
        except Exception as exc:  # noqa: BLE001 — classified below
            code = _http_code(exc)
            if code in (401, 403):
                raise AccessDeniedError(
                    f"User {user!r} cannot use database {database!r} "
                    f"(HTTP {code}). Ask an administrator for access."
                ) from exc
            raise ValidationError(
                f"Could not open database {database!r} as {user!r}: {exc}"
            ) from exc
    return db


def open_profile_database(
    profile: Any,
    *,
    secret_resolver: Any,
    db_connector: Callable[..., Any],
    secret_key: str = "password",
    verify_system: bool = True,
    user: Optional[str] = None,
    database: Optional[str] = None,
) -> ProfileDatabase:
    """Open the database *profile* names (or *database* on the same cluster).

    *user* names who a platform-login profile acts as when there is no request
    (a background run); during a request the signed-in user is used.
    *verify_system* applies to password profiles only: a platform user need
    not have ``_system`` access, so their check is on the database itself.
    """
    secret_ref = (profile.secret_refs or {}).get(secret_key)
    if not secret_ref:
        raise ValidationError(f"Connection profile is missing secret ref: {secret_key}")
    target = database or profile.database
    if is_platform_login(secret_ref):
        who = acting_user(user)
        return ProfileDatabase(db=open_as_platform_user(target, who), user=who or "")
    password = secret_resolver.resolve(secret_ref)
    db = db_connector(
        endpoint=profile.endpoint,
        username=profile.username,
        password=password,
        database=target,
        verify_ssl=profile.verify_ssl,
        verify_system=verify_system,
    )
    return ProfileDatabase(db=db, user=profile.username, secret=password)
