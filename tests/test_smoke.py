"""Startup smoke tests for the API server, the Docker image, and the CLI.

These are deliberately lightweight: they assert the application *boots* and that
the security-relevant invariants hold, without standing in for the full
behavioural suites in ``test_api.py`` and friends.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catalog.api.app import create_app
from catalog.api.config import ApiSettings
from catalog.api.server import insecure_bind_warning, is_wildcard_host
from catalog.cli import main
from catalog.db import init_db

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- API startup -----------------------------------------------------------


def test_api_boots_and_health_is_ok(tmp_path):
    db_path = tmp_path / "catalog.sqlite"
    init_db(str(db_path))
    app = create_app(ApiSettings(db_path=str(db_path), cache_dir=str(tmp_path / "cache")))
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"]["connected"] is True


# --- Insecure-bind warning -------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "::", ""])
def test_wildcard_hosts_detected(host):
    assert is_wildcard_host(host)


def test_warning_when_wildcard_bind_without_api_key():
    assert insecure_bind_warning("0.0.0.0", ApiSettings()) is not None


def test_no_warning_for_loopback():
    assert insecure_bind_warning("127.0.0.1", ApiSettings()) is None


def test_no_warning_for_wildcard_with_api_key(monkeypatch):
    monkeypatch.setenv("NAVIGATE_API_KEY", "secret")
    settings = ApiSettings(require_api_key=True)
    assert insecure_bind_warning("0.0.0.0", settings) is None


# --- Docker invariants -----------------------------------------------------


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except OSError:
        return False


def test_dockerfile_runs_api_without_reload():
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # The container entrypoint is the top-level CMD (column 0), not the indented
    # CMD that is part of the HEALTHCHECK instruction.
    cmd = next(line for line in text.splitlines() if line.startswith("CMD "))
    assert "catalog" in cmd and "api" in cmd
    assert "--no-reload" in cmd


def test_compose_publishes_to_loopback_only():
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = re.findall(r'"(\d+\.\d+\.\d+\.\d+):\d+:\d+"', text)
    # Every host-published port must bind to loopback so the wildcard in-container
    # bind is not exposed to the network by default.
    assert published, "expected at least one published port mapping"
    assert all(ip == "127.0.0.1" for ip in published), published


@pytest.mark.skipif(not _docker_available(), reason="docker daemon not available")
def test_docker_image_builds_and_serves(tmp_path):  # pragma: no cover - heavy/optional
    tag = "navigate-smoke:test"
    build = subprocess.run(
        ["docker", "build", "-t", tag, "."], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert build.returncode == 0, build.stderr
    port = _free_port()
    name = "navigate-smoke-run"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    run = subprocess.run(
        ["docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:8000", tag],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    try:
        import urllib.request

        deadline = time.time() + 30
        ok = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                    if r.status == 200:
                        ok = True
                        break
            except Exception:
                time.sleep(1)
        assert ok, "container did not become healthy in time"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- CLI: doctor -----------------------------------------------------------


def test_doctor_runs_and_reports(tmp_path, capsys):
    db_path = tmp_path / "catalog.sqlite"
    init_db(str(db_path))
    code = main(
        [
            "--db",
            str(db_path),
            "--cache",
            str(tmp_path / "cache"),
            "doctor",
        ]
    )
    out = capsys.readouterr().out
    assert "environment health check" in out
    assert "database:" in out
    # A healthy local setup may still warn (no LLM key, Fuseki down); those are
    # warnings, not failures, so the default (non-strict) exit code is clean.
    assert code == 0


def test_doctor_json_output(tmp_path, capsys):
    db_path = tmp_path / "catalog.sqlite"
    init_db(str(db_path))
    code = main(["--db", str(db_path), "--cache", str(tmp_path / "cache"), "doctor", "--json"])
    out = capsys.readouterr().out
    import json

    report = json.loads(out)
    assert any(c["name"] == "database" for c in report)
    assert code == 0
