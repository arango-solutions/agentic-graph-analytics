"""Tests for scripts/byoc_deploy.py — the rollback half of NFR-22.

A rollback is a deploy, so it has to prove which build is live. It used to set
``expect_version = None`` for every target, which let a rollback to a modern
build pass without checking its version — and, in the other direction, made a
rollback to a pre-NFR-20 build report FAILED even when it worked, because those
builds have no ``/healthz`` and the verifier tried to parse the 404 page.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import byoc_deploy  # noqa: E402


@pytest.mark.parametrize(
    "package_version, expected",
    [
        ("3.0.0-1", "3.0.0"),
        ("3.0.0-12", "3.0.0"),
        # Plain versions predate the <release>-<build> convention and NFR-20.
        ("0.5.2", None),
        ("0.3.0", None),
        # A suffix that is not a build number is not a build number.
        ("1.0.0-rc", None),
        ("3.0.0-", None),
    ],
)
def test_release_of(package_version, expected):
    assert byoc_deploy.release_of(package_version) == expected


class _FakePlatform:
    """Just enough of Platform for rollback and verify."""

    def __init__(self, packages, status_code=200):
        self._packages = packages
        self._status_code = status_code
        self.deep_verified_with = "not called"

    def list_packages(self):
        return [
            {"name": "agentic-graph-analytics", "version": v} for v in self._packages
        ]

    def get(self, url, **_kwargs):
        return type("R", (), {"status_code": self._status_code})()


def _rollback_args(target):
    return argparse.Namespace(
        name="agentic-graph-analytics",
        instance="aga",
        db=None,
        endpoint=None,
        to=target,
        base_image="py12base",
        display_name="x",
        description="x",
        no_ui=False,
        wait_timeout=5.0,
        poll_interval=0.0,
    )


@pytest.fixture
def captured(monkeypatch):
    """Run rollback without touching a platform: record what _swap receives."""

    seen = {}

    def fake_swap(platform, args, db_name, version):
        seen["args"] = args
        seen["version"] = version
        return 0

    platform = _FakePlatform(["0.5.2", "3.0.0-1"])
    monkeypatch.setattr(
        byoc_deploy, "resolve_config", lambda args: (platform, "https://x", None)
    )
    monkeypatch.setattr(byoc_deploy, "_swap", fake_swap)
    return seen


def test_rollback_to_a_modern_build_expects_its_release(captured):
    """The regression: this used to be None, so the version was never checked."""

    byoc_deploy.cmd_rollback(_rollback_args("3.0.0-1"))

    assert captured["args"].expect_version == "3.0.0"
    assert captured["args"].legacy_target is False


def test_rollback_to_a_legacy_build_is_marked_unverifiable(captured, capsys):
    byoc_deploy.cmd_rollback(_rollback_args("0.5.2"))

    assert captured["args"].expect_version is None
    assert captured["args"].legacy_target is True
    assert "predates NFR-20" in capsys.readouterr().out


def test_verify_reports_a_legacy_build_as_unverified_not_failed(monkeypatch):
    """A legacy build answering 200 is neither a success nor a failure.

    deep_verify would call /healthz, get the old bundle's 404 page and report a
    working rollback as FAILED. It must not be called; the outcome is its own
    exit code so a script can tell the two apart.
    """

    platform = _FakePlatform([], status_code=200)
    monkeypatch.setattr(
        byoc_deploy, "resolve_config", lambda args: (platform, "https://x", None)
    )
    called = []
    monkeypatch.setattr(
        byoc_deploy, "deep_verify", lambda *a, **k: called.append(a) or True
    )

    args = argparse.Namespace(
        instance="aga",
        db=None,
        endpoint=None,
        wait_timeout=5.0,
        poll_interval=0.0,
        expect_version=None,
        legacy_target=True,
    )
    assert byoc_deploy.cmd_verify(args) == byoc_deploy.EXIT_UNVERIFIED
    assert called == [], "deep_verify must not run against a build with no /healthz"


def test_verify_deep_checks_a_modern_build_against_its_release(monkeypatch):
    platform = _FakePlatform([], status_code=200)
    monkeypatch.setattr(
        byoc_deploy, "resolve_config", lambda args: (platform, "https://x", None)
    )
    seen = {}

    def fake_deep_verify(platform, url, expect_version):
        seen["expect_version"] = expect_version
        return True

    monkeypatch.setattr(byoc_deploy, "deep_verify", fake_deep_verify)

    args = argparse.Namespace(
        instance="aga",
        db=None,
        endpoint=None,
        wait_timeout=5.0,
        poll_interval=0.0,
        expect_version="3.0.0",
        legacy_target=False,
    )
    assert byoc_deploy.cmd_verify(args) == 0
    assert seen["expect_version"] == "3.0.0"
