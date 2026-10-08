"""Prove that offline verification blocks external access before transport."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts.source_audit import audit_sources

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", ["datasets/raw/missing.json", "artifacts/baseline/runs/missing.json"])
def test_code_only_cannot_depend_on_local_evidence(path):
    with pytest.raises(PermissionError, match="cannot access raw data"):
        (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("host", ["192.0.2.1", "example.invalid", "::ffff:192.0.2.1"])
def test_external_connections_are_refused(host):
    with pytest.raises(PermissionError, match="only loopback"):
        socket.getaddrinfo(host, 80)


def test_numeric_connect_cannot_bypass_dns_guard():
    with socket.socket() as connection:
        with pytest.raises(PermissionError, match="only loopback"):
            connection.connect(("192.0.2.1", 80))


def test_udp_cannot_bypass_connection_guard():
    with socket.socket(type=socket.SOCK_DGRAM) as connection:
        with pytest.raises(PermissionError, match="only loopback"):
            connection.sendto(b"test", ("192.0.2.1", 80))


def test_legacy_dns_cannot_bypass_resolution_guard():
    with pytest.raises(PermissionError, match="only loopback"):
        socket.gethostbyname("example.invalid")


@pytest.mark.platform
@pytest.mark.parametrize("entry", ["python", "worker"])
def test_child_process_inherits_external_network_boundary(entry):
    if entry == "python":
        command = [sys.executable, "-c", "import socket; socket.create_connection(('192.0.2.1', 80))"]
        payload = None
    else:
        command = [sys.executable, "-m", "detector.provider_worker"]
        payload = json.dumps({"url": "http://192.0.2.1/v1", "headers": {},
                              "body_b64": base64.b64encode(b"{}").decode(), "timeout": 1})
    result = subprocess.run(command, input=payload, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "offline verification permits only loopback networking" in result.stderr


def test_source_audit_resolves_relative_boundaries_and_cycles(tmp_path):
    detector = tmp_path / "detector"
    detector.mkdir()
    (detector / "__init__.py").write_text("")
    (detector / "a.py").write_text("from . import b\nfrom evolution import metrics\ndef repeated(): pass\ndef repeated(): pass\n")
    (detector / "b.py").write_text("from . import a\n")
    audit = audit_sources(tmp_path)
    assert not audit["passed"]
    assert audit["duplicate_definitions"][0]["name"] == "repeated"
    assert audit["boundary_violations"]
    assert audit["import_cycles"]
