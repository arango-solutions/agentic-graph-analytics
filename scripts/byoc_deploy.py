#!/usr/bin/env python3
"""Deploy agentic-graph-analytics to the Arango Platform Container Manager.

    scripts/byoc_deploy.py list                 # packages + running services
    scripts/byoc_deploy.py update               # pre-flight, upload, swap, verify
    scripts/byoc_deploy.py verify               # poll the public URL, then deep-check
    scripts/byoc_deploy.py rollback --to 3.0.0-4
    scripts/byoc_deploy.py delete

Three facts shape this tool, each learned the hard way on this platform:

* **There is no in-place update.** POSTing /uds for an app_instance_name that is
  already running fails with "ServiceAccount ... cannot be imported into the
  current release" — each service is a Helm release and a second install cannot
  take ownership of the first's Kubernetes objects. An update is therefore
  delete-then-create, with real downtime. The upload happens BEFORE the delete
  so a rejected artifact fails while the old service is still serving.

* **A 200 proves nothing.** The build being replaced serves the root page just
  as happily as the new one. The only honest proof is reading the version back
  from the running service, which is why this tool refuses to call a deploy
  successful without it (see NFR-20).

* **Cold start looks like failure.** While the pod is coming up the gateway
  answers 404 (route not registered), 503 (route up, pod not ready) and even
  401 to an *authenticated* caller. All three are progress, not errors.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_APP_NAME = "agentic-graph-analytics"
DEFAULT_INSTANCE = "aga"
DEFAULT_BASE_IMAGE = "py12base"  # this cluster offers no py13base
DEFAULT_DISPLAY_NAME = "Agentic Graph Analytics"
DEFAULT_DESCRIPTION = (
    "Transform business requirements into actionable graph analytics insights "
    "with AI-powered automation."
)

ACP = "/_platform/acp/v1"
FILEMANAGER = "/_platform/filemanager/global/byoc/"
READY = {"DEPLOYED"}
FAILED = {"FAILED", "ERROR", "TERMINATED"}


class DeployError(RuntimeError):
    """Anything that should stop the deploy with a readable message."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def tar_member_name(name: str) -> str:
    """Archive members are written as ``./x``; compare on ``x``."""
    return name[2:] if name.startswith("./") else name


def mount_path(instance: str, db_name: str | None) -> str:
    """Where the platform will publish this instance (no trailing slash)."""
    if db_name:
        return f"/_service/uds/_db/{db_name}/{instance}"
    return f"/_service/uds/_global/{instance}"


def read_app_version() -> str:
    """The single source of truth (NFR-20): graph_analytics_ai/__init__.py."""
    init = REPO_ROOT / "graph_analytics_ai" / "__init__.py"
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        init.read_text(encoding="utf-8"),
        re.M,
    )
    if not match:
        raise DeployError(f"no __version__ in {init}")
    return match.group(1)


def _service_id_of(result: dict) -> tuple[str | None, str | None]:
    info = result.get("serviceInfo") if isinstance(result, dict) else None
    if not isinstance(info, dict):
        info = result if isinstance(result, dict) else {}
    return info.get("serviceId") or info.get("service_id"), info.get("status")


# --------------------------------------------------------------------------- #
# platform client
# --------------------------------------------------------------------------- #


class Platform:
    """Thin client over the Container Manager endpoints a release needs."""

    def __init__(self, base: str, user: str, password: str, *, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.user = user
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        # trust_env off: a proxy in the developer's environment silently breaks
        # the upload, and the failure looks like a platform problem.
        self.session.trust_env = False
        self._jwt: str | None = None

    def authenticate(self) -> None:
        response = self.session.post(
            f"{self.base}/_open/auth",
            json={"username": self.user, "password": self.password},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise DeployError(f"auth failed: HTTP {response.status_code}")
        token = response.json().get("jwt")
        if not token:
            raise DeployError("auth response carried no 'jwt' field")
        self._jwt = token

    def _headers(self) -> dict[str, str]:
        if self._jwt is None:
            self.authenticate()
        return {"Authorization": f"Bearer {self._jwt}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        url = f"{self.base}{path}"
        response = self.session.request(method, url, headers=self._headers(), **kwargs)
        if response.status_code == 401:  # one transparent re-auth (slow uploads)
            self.authenticate()
            response = self.session.request(
                method, url, headers=self._headers(), **kwargs
            )
        if response.status_code >= 400:
            raise DeployError(
                f"{method} {path} -> HTTP {response.status_code}: {response.text[:400]}"
            )
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text[:400]}

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", 30)
        return self.session.get(url, headers=self._headers(), **kwargs)

    def list_packages(self) -> list[dict]:
        return self._request("GET", FILEMANAGER).get("services", [])

    def list_services(self) -> list[dict]:
        return self._request("POST", f"{ACP}/list_services", json={}).get(
            "services", []
        )

    def upload(self, tarball: Path, name: str, version: str) -> dict:
        with tarball.open("rb") as handle:
            return self._request(
                "POST",
                FILEMANAGER,
                data={
                    "name": name,
                    "version": version,
                    "language": "python",
                    "type": "Service",
                },
                files={"file": (tarball.name, handle, "application/gzip")},
                timeout=600,
            )

    def deploy(
        self,
        name: str,
        version: str,
        instance: str,
        db_name: str | None,
        base_image: str,
        *,
        has_ui: bool = True,
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict:
        # Every value must be a string: the platform decodes `env` as a
        # protobuf string->string map and rejects a JSON boolean with
        # "invalid value for string field value: true".
        env: dict[str, str] = {
            "service_type": "base_type",
            "base_image": base_image,
            "app_instance_name": instance,
        }
        # Omitting db_name is what mounts the service under _global.
        if db_name:
            env["db_name"] = db_name
        if has_ui:
            env["has_ui"] = "true"
        if display_name:
            env["display_name"] = display_name
        if description:
            env["description"] = description
        return self._request(
            "POST",
            f"{ACP}/uds",
            json={"app_name": name, "app_version": version, "env": env},
            timeout=180,
        )

    def service_status(self, service_id: str) -> dict:
        return self._request("GET", f"{ACP}/service/{service_id}")

    def delete_service(self, service_id: str) -> None:
        self._request("DELETE", f"{ACP}/service/{service_id}", timeout=120)

    def find_instances(self, instance: str) -> list[dict]:
        found = []
        for service in self.list_services():
            uds = ((service.get("serviceMeta") or {}).get("udsMeta")) or {}
            if uds.get("appInstanceName") == instance:
                found.append(
                    {
                        "serviceId": service.get("serviceId"),
                        "version": uds.get("version"),
                        "status": service.get("status"),
                        "dbName": service.get("dbName"),
                    }
                )
        return found

    def resolve_instance(self, instance: str) -> dict | None:
        matches = self.find_instances(instance)
        if len(matches) > 1:
            raise DeployError(
                f"{len(matches)} services run as instance {instance!r}: "
                f"{[m['serviceId'] for m in matches]}. Delete the extras by id first."
            )
        return matches[0] if matches else None

    def wait_until_ready(
        self, service_id: str, *, timeout_s: float = 600.0, interval_s: float = 10.0
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        last: dict = {}
        while time.monotonic() < deadline:
            last = self.service_status(service_id)
            info = last.get("serviceInfo") if isinstance(last, dict) else {}
            info = info if isinstance(info, dict) else {}
            state = str(info.get("status") or last.get("status") or "").upper()
            if state in READY:
                return last
            if state in FAILED:
                raise DeployError(f"service {service_id} reached {state}: {last}")
            print(
                f"    status={state or '(unknown)'} — waiting {interval_s:.0f}s",
                flush=True,
            )
            time.sleep(interval_s)
        raise DeployError(f"timed out after {timeout_s:.0f}s; last status: {last}")


#: Exit code for a deploy that completed but whose build cannot be proven.
#: Distinct from 1 (failed) so a script can tell "did not work" from
#: "worked, but I cannot show you which code is running".
EXIT_UNVERIFIED = 2


def release_of(package_version: str) -> str | None:
    """``3.0.0-4`` -> ``3.0.0``; ``None`` for a build that predates NFR-20.

    Packages uploaded since NFR-20 are named ``<release>-<build>``, and the
    running service reports ``<release>`` from ``/healthz``, so the two can be
    compared. Earlier packages carry a plain version (``0.5.2``) and were built
    before ``/healthz`` existed — they report a hardcoded ``0.1.0`` from
    ``/openapi.json`` and nothing else — so no check can confirm which of them
    is live. Returning ``None`` makes that explicit instead of guessing.
    """

    head, sep, tail = package_version.rpartition("-")
    return head if sep and head and tail.isdigit() else None


def next_build_version(platform: Platform, name: str, release: str) -> str:
    """``<release>-<n>``: the platform keys packages on (name, version) and
    refuses to overwrite, so every upload needs a fresh suffix."""
    taken = {p["version"] for p in platform.list_packages() if p.get("name") == name}
    for build in range(1, 1000):
        candidate = f"{release}-{build}"
        if candidate not in taken:
            return candidate
    raise DeployError(f"no free build suffix for {release}")


# --------------------------------------------------------------------------- #
# pre-flight — refuse to upload an artifact that cannot work
# --------------------------------------------------------------------------- #


def preflight(tarball: Path, instance: str, db_name: str | None) -> None:
    if not tarball.exists():
        raise DeployError(
            f"no tarball at {tarball} — run scripts/platform/package.sh first"
        )
    problems: list[str] = []
    with tarfile.open(tarball, "r:gz") as archive:
        names = {tar_member_name(n): n for n in archive.getnames()}

        def read(member: str) -> str:
            raw = names.get(member)
            if not raw:
                return ""
            handle = archive.extractfile(raw)
            return handle.read().decode("utf-8", "replace") if handle else ""

        for required in (
            "entrypoint",
            "requirements.txt",
            "graph_analytics_ai/product/byoc_app.py",
            "static/index.html",
            "static/workspace/index.html",
        ):
            if required not in names:
                problems.append(f"{required} missing from the archive root layout")

        entry = read("entrypoint")
        if entry and not entry.splitlines()[0].startswith("entrypoint"):
            problems.append("entrypoint line 1 must start with the token `entrypoint`")

        env_text = read(".env")
        if not env_text:
            problems.append(
                ".env is not baked — the platform injects nothing but PORT, so every "
                "route would answer 500 and the UI would silently show demo data"
            )
        else:
            env = {
                k.strip(): v.strip()
                for k, v in (
                    line.split("=", 1)
                    for line in env_text.splitlines()
                    if "=" in line and not line.startswith("#")
                )
            }
            for key in ("ARANGO_ENDPOINT", "ARANGO_USER", "ARANGO_PASSWORD"):
                if not env.get(key):
                    problems.append(f"{key} missing from the baked .env")
            if re.search(r"localhost|127\.0\.0\.1", env.get("ARANGO_ENDPOINT", "")):
                problems.append(
                    "ARANGO_ENDPOINT points at loopback — unreachable from the platform"
                )
            # The product API keeps its metadata in its own database. Pointed at
            # the analytics graph it connects fine and reports no workspaces,
            # which reads as an empty product rather than a misconfiguration.
            if env.get("ARANGO_DATABASE", "") != "aga_workspace":
                problems.append(
                    f"ARANGO_DATABASE={env.get('ARANGO_DATABASE')!r} — the product API "
                    f"needs its own metadata database (aga_workspace), not the "
                    f"analytics graph"
                )
            expected = mount_path(instance, db_name)
            if env.get("SERVICE_URL_PATH_PREFIX", "").rstrip("/") != expected:
                problems.append(
                    f"SERVICE_URL_PATH_PREFIX={env.get('SERVICE_URL_PATH_PREFIX')!r} but "
                    f"the deploy will mount at {expected!r} — repackage with matching "
                    f"--instance/--db"
                )

        # NFR-21. Two distinct failures, both invisible server-side — the
        # service answers 200 while the browser renders nothing.
        index = read("static/workspace/index.html")
        if index:
            expected_prefix = mount_path(instance, db_name)
            if re.search(r'(?:src|href)="/(?!_service)', index):
                problems.append(
                    "exported assets are root-absolute — they resolve against the "
                    "cluster root and produce a blank page behind a 200"
                )
            # An asset prefix that is merely *a* prefix is not enough: a bundle
            # built for another instance fetches THAT service's assets, so the
            # page half-loads from a neighbour rather than 404ing honestly.
            asset_prefixes = {
                match.group(1)
                for match in re.finditer(
                    r'(?:src|href)="(/_service/uds/[^/]+/[^"]*?)/_next/', index
                )
            }
            wrong = {p for p in asset_prefixes if p != expected_prefix}
            if wrong:
                problems.append(
                    f"exported assets carry prefix {sorted(wrong)} but this deploy "
                    f"mounts at {expected_prefix!r} — the page would load another "
                    f"instance's assets. Repackage with matching --instance/--db"
                )

    if problems:
        raise DeployError("pre-flight failed:\n  - " + "\n  - ".join(problems))
    print(
        "    pre-flight OK (layout, entrypoint, baked .env, metadata db, mount "
        "prefix, prefixed assets)"
    )


# --------------------------------------------------------------------------- #
# verification — prove the RIGHT code is live
# --------------------------------------------------------------------------- #


def deep_verify(platform: Platform, url: str, expect_version: str | None) -> bool:
    """Version via /healthz, data via /api/workspaces, and every asset the page
    references — a prefix mismatch otherwise shows a blank page behind a 200."""
    ok = True
    try:
        health = platform.get(url + "healthz").json()
        print(f"    /healthz         {health}")
        if expect_version and health.get("version") != expect_version:
            print(
                f"    FAIL: live version is {health.get('version')}, expected "
                f"{expect_version}",
                file=sys.stderr,
            )
            ok = False
        if not (health.get("database") or {}).get("reachable"):
            print(
                f"    FAIL: database unreachable from the pod: "
                f"{(health.get('database') or {}).get('error')}",
                file=sys.stderr,
            )
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"    FAIL: /healthz unreadable: {exc}", file=sys.stderr)
        return False

    try:
        response = platform.get(url + "api/workspaces", timeout=90)
        workspaces = response.json()
        print(f"    /api/workspaces  {len(workspaces)} workspace(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"    FAIL: /api/workspaces unreadable: {exc}", file=sys.stderr)
        ok = False

    try:
        html = platform.get(url + "workspace/").text
        assets = list(dict.fromkeys(re.findall(r'(?:src|href)="([^"]+)"', html)))
        assets = [
            a for a in assets if not a.startswith(("http://", "https://", "data:"))
        ]
        broken = []
        for asset in assets:
            # Assets are absolute-with-prefix; resolve against the host.
            target = (
                platform.base + asset
                if asset.startswith("/")
                else url + asset.lstrip("./")
            )
            if platform.get(target).status_code != 200:
                broken.append(asset)
        print(f"    assets           {len(assets) - len(broken)}/{len(assets)} served")
        for asset in broken:
            print(f"    FAIL: asset 404 {asset}", file=sys.stderr)
            ok = False
        if not assets:
            print(
                "    FAIL: page references no assets — wrong page served?",
                file=sys.stderr,
            )
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"    FAIL: could not check assets: {exc}", file=sys.stderr)
        ok = False

    print("    => VERIFIED" if ok else "    => VERIFICATION FAILED")
    return ok


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def resolve_config(args: argparse.Namespace) -> tuple[Platform, str, str | None]:
    env = load_env(REPO_ROOT / ".env")
    endpoint = (
        args.endpoint or os.environ.get("ARANGO_ENDPOINT") or env.get("ARANGO_ENDPOINT")
    )
    user = os.environ.get("ARANGO_USER") or env.get("ARANGO_USER")
    password = os.environ.get("ARANGO_PASSWORD") or env.get("ARANGO_PASSWORD")
    if not (endpoint and user and password):
        raise DeployError("need ARANGO_ENDPOINT, ARANGO_USER and ARANGO_PASSWORD")
    return Platform(endpoint, user, password), endpoint, args.db


def cmd_list(args: argparse.Namespace) -> int:
    platform, endpoint, _ = resolve_config(args)
    print(f"==> {endpoint}")
    packages = [p for p in platform.list_packages() if p.get("name") == args.name]
    print(f"\npackages named {args.name!r} ({len(packages)}):")
    for package in sorted(packages, key=lambda p: str(p.get("version")))[-12:]:
        print(f"    {package.get('version')}")
    print("\nrunning services:")
    for service in platform.list_services():
        uds = ((service.get("serviceMeta") or {}).get("udsMeta")) or {}
        if (
            uds.get("appName") == args.name
            or uds.get("appInstanceName") == args.instance
        ):
            print(
                f"    {service.get('serviceId')}  instance={uds.get('appInstanceName')} "
                f"v{uds.get('version')}  {service.get('status')}  "
                f"db={service.get('dbName') or '(global)'}"
            )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    platform, endpoint, db_name = resolve_config(args)
    url = f"{endpoint.rstrip('/')}{mount_path(args.instance, db_name)}/"
    print(f"==> verifying {url}")
    deadline = time.monotonic() + args.wait_timeout
    while time.monotonic() < deadline:
        try:
            response = platform.get(url)
            code = response.status_code
            if code == 200:
                if getattr(args, "legacy_target", False):
                    # deep_verify would call /healthz, get the old bundle's 404
                    # page, fail to parse it, and report a working rollback as
                    # FAILED. Say exactly what is known instead.
                    print("    serving 200 — build identity cannot be proven")
                    print("    => UNVERIFIED (legacy build)")
                    return EXIT_UNVERIFIED
                return (
                    0
                    if deep_verify(platform, url, getattr(args, "expect_version", None))
                    else 1
                )
            # All three are cold-start states, not failures: 401 is the gateway
            # answering an authenticated caller before the pod is ready.
            hint = {
                404: "route not registered",
                401: "gateway/pod not ready yet",
                503: "pod not ready (or just deleted)",
            }.get(code, "not serving yet")
            print(
                f"    HTTP {code} ({hint}) — retrying in {args.poll_interval:.0f}s",
                flush=True,
            )
        except requests.RequestException as exc:
            print(
                f"    {type(exc).__name__} — retrying in {args.poll_interval:.0f}s",
                flush=True,
            )
        time.sleep(args.poll_interval)
    print(
        f"error: {url} never returned 200 within {args.wait_timeout:.0f}s",
        file=sys.stderr,
    )
    return 1


def _swap(
    platform: Platform, args: argparse.Namespace, db_name: str | None, version: str
) -> int:
    """Delete-then-create — what an update *is* here. A second install cannot
    take over the first one's Kubernetes objects."""
    existing = platform.resolve_instance(args.instance)
    if existing:
        print(
            f"==> replacing {existing['serviceId']} (v{existing['version']} -> v{version})"
        )
        platform.delete_service(existing["serviceId"])
        print("    old service deleted — the URL is down from here")
    else:
        print(f"==> no existing instance {args.instance!r}; creating fresh")
    print(
        f"    mounts at: {mount_path(args.instance, db_name)}/   base image: {args.base_image}"
    )
    result = platform.deploy(
        args.name,
        version,
        args.instance,
        db_name,
        args.base_image,
        has_ui=not args.no_ui,
        display_name=args.display_name,
        description=args.description,
    )
    service_id, state = _service_id_of(result)
    print(f"    created {service_id} status={state}")
    if service_id:
        print("==> waiting for DEPLOYED...")
        platform.wait_until_ready(service_id, timeout_s=args.wait_timeout)
        print("    DEPLOYED (the pod may still be installing dependencies)")
    args.poll_interval = getattr(args, "poll_interval", 15.0)
    return cmd_verify(args)


def cmd_update(args: argparse.Namespace) -> int:
    """Pre-flight, upload, swap, verify. Upload precedes delete so a rejected
    artifact fails while the old service still serves."""
    platform, _endpoint, db_name = resolve_config(args)
    tarball = Path(args.tarball)
    release = read_app_version()
    print(f"==> release {release} (graph_analytics_ai/__init__.py)")
    preflight(tarball, args.instance, db_name)
    version = args.version or next_build_version(platform, args.name, release)
    size_mb = tarball.stat().st_size / 1_048_576
    print(f"==> uploading {tarball.name} ({size_mb:.1f} MB) as {args.name} v{version}")
    platform.upload(tarball, args.name, version)
    print("    uploaded")
    args.expect_version = release
    return _swap(platform, args, db_name, version)


def cmd_rollback(args: argparse.Namespace) -> int:
    platform, _endpoint, db_name = resolve_config(args)
    available = sorted(
        {p["version"] for p in platform.list_packages() if p.get("name") == args.name}
    )
    if args.to not in available:
        raise DeployError(
            f"{args.name} v{args.to} is not uploaded. Available: {available[-10:]}"
        )
    print(
        "==> ROLLBACK to an already-uploaded package (code only; the database is untouched)"
    )
    # NFR-22: a rollback is a deploy, so it must prove which build is live.
    # This used to set expect_version = None for every target, which let a
    # rollback to a modern build pass without checking its version at all.
    args.expect_version = release_of(args.to)
    args.legacy_target = args.expect_version is None
    if args.legacy_target:
        print(
            f"    NOTE: v{args.to} predates NFR-20 — it has no /healthz and reports a "
            f"hardcoded version, so this rollback can be confirmed to SERVE but not "
            f"proven to be v{args.to}. It will be reported UNVERIFIED."
        )
    return _swap(platform, args, db_name, args.to)


def cmd_delete(args: argparse.Namespace) -> int:
    platform, _, _ = resolve_config(args)
    existing = platform.resolve_instance(args.instance)
    if not existing:
        print(f"no service runs as instance {args.instance!r} — nothing to delete")
        return 0
    print(
        f"==> deleting {existing['serviceId']} (instance {args.instance}, v{existing['version']})"
    )
    platform.delete_service(existing["serviceId"])
    print("    deleted")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    platform, _, _ = resolve_config(args)
    print(platform.service_status(args.service_id))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--endpoint", default=None, help="override ARANGO_ENDPOINT")
        p.add_argument("--name", default=DEFAULT_APP_NAME)
        p.add_argument("--instance", default=DEFAULT_INSTANCE)
        p.add_argument(
            "--db", default=None, help="db-scope the service (default: global)"
        )
        return p

    def deployable(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        common(p)
        p.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
        p.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
        p.add_argument("--description", default=DEFAULT_DESCRIPTION)
        p.add_argument("--no-ui", action="store_true")
        p.add_argument("--wait-timeout", type=float, default=900.0)
        p.add_argument("--poll-interval", type=float, default=15.0)
        return p

    p = sub.add_parser("list", help="show uploaded packages and deployed services")
    common(p).set_defaults(func=cmd_list)

    p = sub.add_parser(
        "update", help="pre-flight, upload, swap the live service, verify"
    )
    deployable(p)
    p.add_argument("--tarball", default=str(REPO_ROOT / "aga-service.tar.gz"))
    p.add_argument(
        "--version", default=None, help="override the derived <release>-<build>"
    )
    p.set_defaults(func=cmd_update)

    p = sub.add_parser(
        "verify", help="poll the public URL until it serves, then deep-check"
    )
    common(p)
    p.add_argument("--wait-timeout", type=float, default=600.0)
    p.add_argument("--poll-interval", type=float, default=10.0)
    p.add_argument("--expect-version", default=None)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser(
        "rollback", help="redeploy a previously uploaded package version"
    )
    deployable(p)
    p.add_argument("--to", required=True)
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("delete", help="remove the deployed service")
    common(p).set_defaults(func=cmd_delete)

    p = sub.add_parser("status", help="raw status of one service id")
    common(p)
    p.add_argument("service_id")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
