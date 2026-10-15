from __future__ import annotations

import json
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import detector.provider as provider_module
from detector.provider import (
    ProviderError,
    _isolated_transport,
    case_deadline_ctx,
    extract_json_object,
    provider_call,
)


def test_extract_json_object_fences():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert extract_json_object("not json") is None


def test_case_capacity_deadline_is_a_case_failure_not_global_cancellation(monkeypatch):
    from detector.runtime import CaseDeadlineExceeded, RunControl, deadline_scope
    with deadline_scope(deadline=0), pytest.raises(CaseDeadlineExceeded):
        with RunControl().request_slot():
            raise AssertionError("expired case cannot acquire provider capacity")

    def expired(**kwargs):
        raise CaseDeadlineExceeded("case capacity timed out")
    monkeypatch.setattr(provider_module, "_provider_call", expired)
    with pytest.raises(ProviderError) as failure:
        provider_call(route={}, system="", user="", purpose="case")
    assert failure.value.error_type == "case_deadline_exceeded"
    assert not failure.value.retryable


def test_actual_capacity_wait_does_not_retry_or_record_an_undispatched_request(monkeypatch):
    from detector.runtime import RunControl, deadline_scope, run_scope, usage_scope

    control = RunControl(1)
    monkeypatch.setenv("CAPACITY_TEST_KEY", "offline-dummy")
    monkeypatch.setattr(provider_module, "_isolated_transport",
                        lambda **kwargs: pytest.fail("capacity wait cannot dispatch transport"))
    with control.request_slot():
        with run_scope(control), deadline_scope(seconds=0.05), usage_scope() as requests:
            with pytest.raises(ProviderError) as failure:
                provider_call(route={"endpoint": "http://127.0.0.1:1/v1", "model": "fake",
                    "api_key_env": "CAPACITY_TEST_KEY", "transport_retries": 2},
                    system="s", user="u", purpose="capacity")
            assert failure.value.error_type == "case_deadline_exceeded"
            assert not failure.value.retryable
            assert requests == []
    control.check()


@pytest.mark.parametrize("votes", [1, 3])
def test_capacity_deadline_survives_judge_and_whole_case_retry(monkeypatch, votes):
    from unittest.mock import patch
    from detector.api import Detector
    from detector.runner import detect_with_transport_retry
    from detector.runtime import RunControl, deadline_scope, run_scope
    monkeypatch.setenv("CAPACITY_TEST_KEY", "offline-dummy")
    monkeypatch.setattr(provider_module, "_isolated_transport",
                        lambda **kwargs: pytest.fail("capacity wait cannot dispatch transport"))
    detector = Detector({"provider": {"endpoint": "http://127.0.0.1:1/v1", "model": "fake",
        "api_key_env": "CAPACITY_TEST_KEY", "votes": votes, "vote_topup": 2}}, require_credentials=False)
    control = RunControl(1)
    with patch.object(detector, "detect", wraps=detector.detect) as detect:
        with control.request_slot(), run_scope(control), deadline_scope(seconds=0.05):
            result = detect_with_transport_retry(detector,
                {"question": "What is 2+2?", "history": [{"role": "assistant", "content": "4"}]},
                case_id="capacity.json")
        assert detect.call_count == 1
    assert result["authority_failure_reasons"][0]["error_type"] == "case_deadline_exceeded"
    assert result["execution"]["provider_attempts"] == 0
    control.check()


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


@pytest.mark.platform
def test_isolated_transport_returns_error_status_instead_of_crashing(monkeypatch):
    # An error status is a provider answer: the worker must hand it back so the
    # caller can report "http <status>" and classify the failure, rather than
    # dying with a traceback the parent can only truncate.
    import subprocess
    import sys

    class _ErrorHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({
                "error": {"message": "model does not support this option"}
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({
            "url": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            "headers": {"Content-Type": "application/json"},
            "body_b64": base64.b64encode(b"{}").decode("ascii"),
            "timeout": 20,
        })
        completed = subprocess.run(
            [sys.executable, "-m", "detector.provider_worker"],
            input=payload, capture_output=True, text=True, timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        envelope = json.loads(completed.stdout)
        assert envelope["status"] == 400
        assert "model does not support" in base64.b64decode(
            envelope["body_b64"]
        ).decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_provider_call_returns_parsed_json(local_server, monkeypatch):
    monkeypatch.setenv("DETECTOR_TEST_KEY", "secret")
    route = {
        "endpoint": local_server,
        "model": "test-model",
        "api_key_env": "DETECTOR_TEST_KEY",
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
                "api_key_env": "MISSING_TEST_KEY",
            },
            system="s", user="u", purpose="p",
        )
    assert exc.value.error_type == "missing_credential"


def test_provider_call_total_timeout_bounds_all_retries(monkeypatch):
    """A route-wide deadline must not be spent once per retry."""
    monkeypatch.setenv("DETECTOR_TEST_KEY", "secret")
    now = [0.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(provider_module.time, "sleep", lambda _delay: None)
    timeouts: list[float] = []

    def _timed_out_transport(*, timeout, **_kwargs):
        timeouts.append(timeout)
        now[0] = 3.0
        raise TimeoutError("simulated")

    monkeypatch.setattr(provider_module, "_isolated_transport", _timed_out_transport)
    with pytest.raises(ProviderError) as exc:
        provider_call(
            route={
                "endpoint": "http://127.0.0.1:1/v1",
                "model": "test-model",
                "api_key_env": "DETECTOR_TEST_KEY",
                "timeout_seconds": 10,
                "total_timeout_seconds": 3,
                "transport_retries": 2,
            },
            system="sys", user="user", purpose="test",
        )
    assert exc.value.attempts == 1
    assert timeouts == [3.0]
    assert "total timeout exceeded" in str(exc.value)


def test_isolated_transport_strips_inherited_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

        def __init__(self, *_args, **kwargs):
            captured.update(kwargs)

        def poll(self):
            return self.returncode

        def communicate(self, *_args, **_kwargs):
            return json.dumps({
                "status": 200,
                "body_b64": base64.b64encode(b"{}").decode("ascii"),
            }).encode("utf-8"), b""

    monkeypatch.setattr(provider_module.subprocess, "Popen", Completed)
    status, raw = _isolated_transport(
        url="https://www.autodl.art/api/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        body=b"{}",
        timeout=5,
    )
    assert (status, raw) == (200, b"{}")
    assert "HTTPS_PROXY" not in captured["env"]


@pytest.mark.parametrize("choices", ["invalid", ["invalid"], [{"message": "invalid"}], [{"message": {"content": {"ok": True}}}]])
def test_invalid_provider_envelope_is_a_structured_failure(monkeypatch, choices):
    monkeypatch.setenv("DETECTOR_TEST_KEY", "offline")
    monkeypatch.setattr(provider_module, "_isolated_transport", lambda **kwargs: (200, json.dumps({"choices": choices}).encode()))
    with pytest.raises(ProviderError) as caught:
        provider_call(route={"endpoint": "http://127.0.0.1", "model": "fake", "api_key_env": "DETECTOR_TEST_KEY", "transport_retries": 0},
                      system="s", user="u", purpose="test")
    assert caught.value.error_type == "invalid_json"


def test_invalid_usage_shape_does_not_crash_a_valid_reply(monkeypatch):
    monkeypatch.setenv("DETECTOR_TEST_KEY", "offline")
    monkeypatch.setattr(provider_module, "_isolated_transport", lambda **kwargs: (200, json.dumps({
        "choices": [{"message": {"content": '{"ok": true}'}}], "usage": "invalid",
    }).encode()))
    parsed, meta = provider_call(route={"endpoint": "http://127.0.0.1", "model": "fake", "api_key_env": "DETECTOR_TEST_KEY", "transport_retries": 0},
                                 system="s", user="u", purpose="test")
    assert parsed == {"ok": True} and meta["usage"] == {}


def test_provider_call_passes_route_scoped_extra_body(monkeypatch):
    monkeypatch.setenv("DETECTOR_TEST_KEY", "secret")
    captured: dict[str, object] = {}

    def _response(*, body, **_kwargs):
        captured.update(json.loads(body.decode("utf-8")))
        return 200, json.dumps({
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "model": "test-model",
        }).encode("utf-8")

    monkeypatch.setattr(provider_module, "_isolated_transport", _response)
    parsed, _meta = provider_call(
        route={
            "endpoint": "https://example.invalid/v1",
            "model": "test-model",
            "api_key_env": "DETECTOR_TEST_KEY",
            "transport_retries": 0,
            "extra_body": {"thinking": {"type": "low"}},
        },
        system="sys", user="user", purpose="test",
    )
    assert parsed == {"ok": True}
    assert captured["thinking"] == {"type": "low"}


def test_provider_call_enables_context_cache(monkeypatch):
    monkeypatch.setenv("DETECTOR_TEST_KEY", "secret")
    captured: dict[str, object] = {}

    def _response(*, body, **_kwargs):
        captured.update(json.loads(body.decode("utf-8")))
        return 200, json.dumps({
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {
                "prompt_cache_hit_tokens": 7,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        }).encode("utf-8")

    monkeypatch.setattr(provider_module, "_isolated_transport", _response)
    parsed, meta = provider_call(
        route={
            "endpoint": "https://example.invalid/v1",
            "model": "test-model",
            "api_key_env": "DETECTOR_TEST_KEY",
            "transport_retries": 0,
            "context_cache": {
                "enabled": True,
                "mode": "prompt_cache_key",
                "key_prefix": "test-cache",
                "retention": "24h",
            },
        },
        system="sys", user="user", purpose="judge",
    )
    assert parsed == {"ok": True}
    assert captured["prompt_cache_key"] == "test-cache:judge"
    assert captured["prompt_cache_retention"] == "24h"
    assert meta["prompt_cache_hit_tokens"] == 7
    assert meta["prompt_cache_read_tokens"] == 3
    assert meta["prompt_cache_write_tokens"] == 2


def test_case_deadline_bounds_provider_call(monkeypatch):
    monkeypatch.setenv("DETECTOR_TEST_KEY", "secret")
    clock = {"now": 0.0}

    def _monotonic():
        return clock["now"]

    monkeypatch.setattr(provider_module.time, "monotonic", _monotonic)
    monkeypatch.setattr(provider_module.time, "sleep", lambda _delay: None)
    timeouts: list[float] = []

    def _timed_out_transport(*, timeout, **_kwargs):
        timeouts.append(timeout)
        clock["now"] += 1.0
        raise TimeoutError("simulated")

    monkeypatch.setattr(provider_module, "_isolated_transport", _timed_out_transport)
    with case_deadline_ctx(2.0):
        with pytest.raises(ProviderError) as exc:
            provider_call(
                route={
                    "endpoint": "http://127.0.0.1:1/v1",
                    "model": "test-model",
                    "api_key_env": "DETECTOR_TEST_KEY",
                    "timeout_seconds": 100,
                    "total_timeout_seconds": 100,
                    "transport_retries": 2,
                },
                system="sys", user="user", purpose="test",
            )
    assert "total timeout exceeded" in str(exc.value)
    assert all(t <= 2.0 for t in timeouts)
    assert len(timeouts) > 1
    assert exc.value.error_type == "case_deadline_exceeded"
    assert not exc.value.retryable


def test_case_deadline_raises_when_already_exceeded(monkeypatch):
    monkeypatch.setenv("DETECTOR_TEST_KEY", "secret")
    clock = {"now": 0.0}

    monkeypatch.setattr(provider_module.time, "monotonic", lambda: clock["now"])
    with case_deadline_ctx(1.0):
        clock["now"] = 10.0
        with pytest.raises(ProviderError) as exc:
            provider_call(
                route={
                    "endpoint": "http://127.0.0.1:1/v1",
                    "model": "test-model",
                    "api_key_env": "DETECTOR_TEST_KEY",
                },
                system="s", user="u", purpose="p",
            )
    assert exc.value.error_type == "case_deadline_exceeded"


@pytest.mark.parametrize("dispatched", [False, True])
def test_added_budget_stop_preserves_dispatched_request(dispatched):
    from contextlib import contextmanager
    from unittest.mock import patch
    from detector.runtime import (
        AddedBudgetExceeded, RunControl, run_scope, usage_scope, request_observer_scope,
    )

    control = RunControl(1)

    @contextmanager
    def expired_capacity(**kwargs):
        raise AddedBudgetExceeded("time")
        yield

    observations = []
    with patch.dict("os.environ", {"OFFLINE_BUDGET_KEY": "offline-dummy"}), patch(
        "detector.provider._isolated_transport", side_effect=AddedBudgetExceeded("time")
    ) as transport:
        capacity = (
            patch.object(control, "request_slot", wraps=control.request_slot) if dispatched
            else patch.object(control, "request_slot", expired_capacity)
        )
        with capacity, run_scope(control), usage_scope() as requests, request_observer_scope(observations.append):
            with pytest.raises(AddedBudgetExceeded):
                provider_call(
                    route={"endpoint": "http://127.0.0.1:1/v1", "model": "fake",
                           "api_key_env": "OFFLINE_BUDGET_KEY", "transport_retries": 2},
                    system="s", user="u", purpose="action_forecast",
                )
            assert transport.call_count == int(dispatched)
            assert len(requests) == int(dispatched)
            expected = ["request_queued", "request_started", "request_finished"] if dispatched else ["request_queued"]
            assert [row["event"] for row in observations] == expected
            if dispatched:
                assert requests[0]["status"] == "added_budget_exceeded"
                assert requests[0]["resource"] == "time"
                assert requests[0]["usage"] is None
    control.check()
