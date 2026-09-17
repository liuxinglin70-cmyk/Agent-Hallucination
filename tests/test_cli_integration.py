"""Real command processes and HTTP transport with a local deterministic provider."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]


def test_detect_batch_evaluate_through_http(tmp_path):
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
               "DETECTOR_API_KEY_ENV": "CLI_INTEGRATION_KEY"}

        def command(*args):
            result = subprocess.run([sys.executable, "-m", "detector", *args], cwd=ROOT, env=env,
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
        assert "hidden-gold-marker" not in json.dumps(requests)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
