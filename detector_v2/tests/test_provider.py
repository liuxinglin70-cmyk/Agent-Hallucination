from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from detector_v2.provider import ProviderError, extract_json_object, provider_call


def test_extract_json_object_fences():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert extract_json_object("not json") is None


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        response = json.dumps({
            "choices": [{
                "message": {
                    "content": '{"is_hallucination": true, "hallucination_step": 2}'
                }
            }],
            "usage": {"prompt_tokens": 5, "total_tokens": 10},
            "model": "test-model",
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    thread.join(timeout=5)


def test_provider_call_returns_parsed_json(local_server, monkeypatch):
    monkeypatch.setenv("V2_TEST_KEY", "secret")
    route = {
        "endpoint": local_server,
        "model": "test-model",
        "api_key_env": "V2_TEST_KEY",
        "temperature": 0.0,
        "timeout_seconds": 5,
        "transport_retries": 1,
    }
    parsed, meta = provider_call(
        route=route, system="sys", user="user", purpose="test",
    )
    assert parsed["is_hallucination"] is True
    assert meta["model"] == "test-model"


def test_provider_call_missing_key():
    with pytest.raises(ProviderError) as exc:
        provider_call(
            route={
                "endpoint": "http://127.0.0.1:1/v1",
                "model": "m",
                "api_key_env": "DOES_NOT_EXIST_V2",
            },
            system="s", user="u", purpose="p",
        )
    assert exc.value.error_type == "missing_credential"
