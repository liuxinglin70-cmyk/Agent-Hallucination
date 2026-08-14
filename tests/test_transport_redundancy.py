"""Transport redundancy: progressive per-attempt timeout windows, a
backoff-inclusive default total budget, and retry-then-succeed behavior."""

from __future__ import annotations

import json

from sehd_rise.detection import transport
from sehd_rise.detection.transport import _transport_budget


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._chunks = [payload, b""]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read1(self, _size: int) -> bytes:
        return self._chunks.pop(0)


def _good_body() -> bytes:
    return json.dumps({
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {},
    }).encode("utf-8")


def _route() -> dict:
    return {
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "_provider_route_resolved": True,
        "transport_retries": 2,
        "timeout_seconds": 120,
    }


def test_budget_grows_windows_and_includes_backoff() -> None:
    windows, total = _transport_budget(
        {"timeout_growth_factor": 2.0, "timeout_cap_seconds": 480},
        retries=4,
        per_attempt_timeout=120,
    )
    assert windows == (120, 240, 480, 480, 480)
    assert total == sum(windows) + 15  # backoff 1+2+4+8


def test_budget_fixed_windows_by_default() -> None:
    windows, total = _transport_budget({}, retries=2, per_attempt_timeout=120)
    assert windows == (120, 120, 120)
    assert total == 360 + 3


def test_budget_explicit_cap_bounds_growth() -> None:
    windows, _total = _transport_budget(
        {"timeout_growth_factor": 3.0, "timeout_cap_seconds": 200},
        retries=2,
        per_attempt_timeout=120,
    )
    assert windows == (120, 200, 200)


def test_transport_retries_timeouts_then_succeeds(monkeypatch) -> None:
    calls = {"count": 0}
    sleeps = []

    class FakeOpener:
        def open(self, req, timeout):
            calls["count"] += 1
            if calls["count"] <= 2:
                raise TimeoutError("The read operation timed out")
            return _FakeResponse(_good_body())

    monkeypatch.setattr(transport, "DIRECT_PROVIDER_OPENER", FakeOpener())
    monkeypatch.setattr(
        transport, "resolve_provider_route",
        lambda route, purpose: dict(route),
    )
    monkeypatch.setattr(
        transport, "resolve_provider_credential",
        lambda route, purpose: "test-key",
    )
    monkeypatch.setattr(transport.time, "sleep", sleeps.append)
    value, meta = transport.provider_call_with_schema(
        route=_route(), system="s", user="u",
        cache_prefix="test_redundancy", require_candidates=False,
    )
    assert value == {"ok": True}
    assert meta["transport_attempts"] == 3
    assert len(sleeps) == 2
    assert sleeps[0] >= 1.0  # first backoff, jitter included


def test_transport_fails_fast_on_contract_error(monkeypatch) -> None:
    calls = {"count": 0}
    sleeps = []

    class FakeOpener:
        def open(self, req, timeout):
            calls["count"] += 1
            # Empty content both on the primary and the repair call.
            return _FakeResponse(
                json.dumps({
                    "choices": [{"message": {"content": ""}}],
                    "usage": {},
                }).encode("utf-8")
            )

    monkeypatch.setattr(transport, "DIRECT_PROVIDER_OPENER", FakeOpener())
    monkeypatch.setattr(
        transport, "resolve_provider_route",
        lambda route, purpose: dict(route),
    )
    monkeypatch.setattr(
        transport, "resolve_provider_credential",
        lambda route, purpose: "test-key",
    )
    monkeypatch.setattr(transport.time, "sleep", sleeps.append)
    try:
        transport.provider_call_with_schema(
            route=_route(), system="s", user="u",
            cache_prefix="test_redundancy_failfast", require_candidates=False,
        )
    except ValueError as exc:
        assert "provider_empty_content" in str(exc)
    else:
        raise AssertionError("expected contract failure to raise")
    # One primary attempt + one bounded JSON repair; no retry sleep.
    assert calls["count"] == 2
    assert sleeps == []
