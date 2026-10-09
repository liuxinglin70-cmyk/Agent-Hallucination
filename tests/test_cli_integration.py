"""Real command processes and HTTP transport with a local deterministic provider."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["source", "wheel"])
def cli_runtime(request, tmp_path):
    if request.param == "source":
        return ROOT, os.environ["PYTHONPATH"]
    source = tmp_path / "package_source"
    source.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
    for package in ("detector", "evolution", "experiments"):
        shutil.copytree(ROOT / package, source / package, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    wheels = tmp_path / "wheels"
    installed = tmp_path / "installed"
    outside = tmp_path / "outside"
    outside.mkdir()
    guard = str(ROOT / "scripts" / "offline_guard")
    environment = {**os.environ, "PYTHONPATH": guard, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    def pip_command(arguments):
        result = subprocess.run([sys.executable, "-m", "pip", *arguments], cwd=outside, env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    pip_command(["wheel", str(source), "--no-deps", "--no-build-isolation", "--no-cache-dir", "--wheel-dir", str(wheels)])
    pip_command(["install", str(next(wheels.glob("*.whl"))), "--no-deps", "--no-cache-dir", "--target", str(installed)])
    pythonpath = os.pathsep.join([guard, str(installed)])
    probe = subprocess.run([sys.executable, "-c", "import detector; print(detector.__file__)"],
                           cwd=outside, env={**environment, "PYTHONPATH": pythonpath}, capture_output=True,
                           text=True, encoding="utf-8", timeout=10)
    assert probe.returncode == 0 and str(installed) in probe.stdout, probe.stdout + probe.stderr
    return outside, pythonpath


def test_detect_batch_evaluate_through_http(tmp_path, cli_runtime):
    working_directory, pythonpath = cli_runtime
    requests = []
    judge = {"is_hallucination": False, "hallucination_step": None,
             "hallucination_reason": None, "delivered_result": "4", "correct_result": "4",
             "basis": None, "confidence": "high", "evidence_refs": [], "root_cause": None,
             "contracts": [{"kind": "exact_value", "requirement": "4"}]}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": json.dumps(judge)}}],
                               "model": "local-test-model", "usage": {"total_tokens": 10}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        route = {"endpoint": f"http://127.0.0.1:{server.server_port}/v1", "model": "local-test-model",
                 "api_key_env": "CLI_INTEGRATION_KEY", "votes": 3, "vote_topup": 0,
                 "timeout_seconds": 10, "total_timeout_seconds": 10, "transport_retries": 0}
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"provider": route, "case_timeout_seconds": 30}), encoding="utf-8")
        data = tmp_path / "data"
        data.mkdir()
        record = data / "clean.json"
        record.write_text(json.dumps({"question": "What is 2 + 2?", "history": [
            {"step": 1, "role": "assistant", "content": "4"}],
            "is_hallucination": False, "true_answer": "hidden-gold-marker"}), encoding="utf-8")
        cases = tmp_path / "cases.json"
        cases.write_text('["clean.json"]', encoding="utf-8")
        env = {**os.environ, "CLI_INTEGRATION_KEY": "local-test-only", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONUTF8": "1", "DETECTOR_ENDPOINT": route["endpoint"],
               "DETECTOR_API_KEY_ENV": "CLI_INTEGRATION_KEY", "PYTHONPATH": pythonpath}

        def command(*args):
            result = subprocess.run([sys.executable, "-m", "detector", *args], cwd=working_directory, env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=60)
            assert result.returncode == 0, result.stdout + result.stderr

        command("detect", "--record", str(record), "--config", str(config),
                "--output-dir", str(tmp_path / "single"))
        prediction = json.loads((tmp_path / "single/prediction.json").read_text(encoding="utf-8"))
        assert prediction["state"] == "verified_clean"
        assert prediction["decision_authority"] == "authoritative"
        common = ["--dataset-root", str(data), "--cases-file", str(cases),
                  "--runs-root", str(tmp_path / "runs"), "--run-id", "integration"]
        command("batch", *common, "--config", str(config), "--workers", "2")
        command("evaluate", *common)
        report = json.loads((tmp_path / "runs/integration/evaluation.json").read_text(encoding="utf-8"))
        assert report["metrics"]["tn"] == 1
        assert report["metrics"]["unresolved"] == 0
        assert len(requests) == 6
        from experiments.plan import case_list_digest
        measurement = tmp_path / "measurement.json"
        measurement.write_text(json.dumps({"schema_version": "detector.measurement_manifest.v1",
            "cases": ["clean.json"], "cases_digest": case_list_digest(["clean.json"]),
            "gold_positive": 0, "gold_negative": 1}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "experiments", "--measurement", str(measurement),
            "baseline", "--config", str(config), "--dataset-root", str(data),
            "--runs-root", str(tmp_path / "experiments"), "--run-id", "baseline"],
            cwd=working_directory, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["coverage"]["measured"] == 1
        assert len(requests) == 9
        for package in ("detector", "evolution", "experiments"):
            help_result = subprocess.run([sys.executable, "-m", package, "--help"], cwd=working_directory,
                                         env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
            assert help_result.returncode == 0, help_result.stdout + help_result.stderr
        assert "hidden-gold-marker" not in json.dumps(requests)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_batch_cli_returns_nonzero_after_fatal_provider_error(tmp_path):
    """A recorded 401 is a failed measurement, not a successful CLI run."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"error": {
                "message": "Invalid authentication credentials",
                "type": "invalid_request_error",
            }}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1"
        route = {
            "endpoint": endpoint,
            "model": "local-test-model",
            "api_key_env": "CLI_AUTH_FAILURE_KEY",
            "votes": 1,
            "vote_topup": 0,
            "timeout_seconds": 10,
            "total_timeout_seconds": 10,
            "transport_retries": 0,
        }
        config = tmp_path / "config.json"
        config.write_text(json.dumps({
            "provider": route,
            "case_timeout_seconds": 30,
        }), encoding="utf-8")
        data = tmp_path / "data"
        data.mkdir()
        for name in ("one.json", "two.json"):
            (data / name).write_text(json.dumps({
                "question": "What is 2 + 2?",
                "history": [{"step": 1, "role": "assistant", "content": "4"}],
                "is_hallucination": False,
                "true_answer": "4",
            }), encoding="utf-8")
        cases = tmp_path / "cases.json"
        cases.write_text('["one.json", "two.json"]', encoding="utf-8")
        env = {
            **os.environ,
            "CLI_AUTH_FAILURE_KEY": "local-test-only",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "DETECTOR_ENDPOINT": endpoint,
            "DETECTOR_API_KEY_ENV": "CLI_AUTH_FAILURE_KEY",
        }
        result = subprocess.run(
            [sys.executable, "-m", "detector", "batch",
             "--dataset-root", str(data), "--cases-file", str(cases),
             "--runs-root", str(tmp_path / "runs"), "--run-id", "unauthorized",
             "--config", str(config), "--workers", "1"],
            cwd=ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=60,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        summary = json.loads(
            (tmp_path / "runs/unauthorized/summary.json").read_text(encoding="utf-8")
        )
        assert summary["stop"]["error_type"] == "authentication_error"
        assert summary["measurement"]["status_counts"]["fatal_provider"] == 1
        assert summary["measurement"]["not_started"] == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
