from copy import deepcopy

import pytest

from detector.api import _judge_negative_needs_recall
from detector.config import validate_config
from detector.packet import build_packet, render_judge_view, render_sparse_view
from detector.trajectory_budget import bound_request_route


@pytest.mark.parametrize("size,configured,limit,long", [
    (100, {}, 16384, False), (48000, {}, 8192, True),
    (50000, {"max_tokens": 1024}, 1024, True),
    (100, {"max_tokens": 100000}, 16384, False),
    (50000, {"long_output_tokens_per_request": 4000}, 4000, True),
])
def test_request_ceilings_are_adaptive_and_do_not_mutate_routes(size, configured, limit, long):
    original = deepcopy(configured)
    route, budget = bound_request_route(configured, system="", user="x" * size)
    assert route["max_tokens"] == budget.output_token_limit == limit
    assert budget.long_input is long
    assert configured == original
    retried, _ = bound_request_route(route, system="", user="x" * (size + 1000))
    assert retried["max_tokens"] <= limit


@pytest.mark.parametrize("value", [None, False, 0, -1, "invalid"])
def test_invalid_budget_limits_are_rejected_before_requests(value):
    with pytest.raises(ValueError):
        validate_config({"provider": {"long_output_tokens_per_request": value}})


def test_sparse_projection_keeps_early_middle_and_terminal_coordinates_and_failure():
    packet = build_packet({"question": "Return the result and list the final directory.", "history": [
        {"role": "assistant", "content": "start " + "x" * 40000},
        {"role": "assistant", "tool_calls": [{"name": "fetch", "arguments": {"id": "target"}}],
         "tool_responses": ["a" * 20000 + "\nError: denied\n" + "b" * 20000]},
        {"role": "assistant", "content": "The operation was completed."},
    ]})
    original = deepcopy(packet)
    text, meta = render_judge_view(packet)
    assert len(text) <= 48000
    assert meta["degraded"] is True
    assert meta["rendered_event_ids"] == ["event_001", "event_002", "event_003"]
    assert "Error: denied" in text
    assert "The operation was completed." in text
    assert "gaps are not evidence of absence" in text
    assert packet == original
    assert _judge_negative_needs_recall({"view_meta": meta}, {"judge_route": {"votes": 3}})


def test_question_after_four_thousand_characters_remains_visible():
    packet = build_packet({"question": "Context " + "x" * 4100 + " Also list hidden files after deletion.",
                           "history": [{"content": "done"}]})
    text, meta = render_judge_view(packet)
    assert "Also list hidden files after deletion" in text
    assert meta["task_truncated"] is False


def test_impossibly_large_index_reports_explicit_omissions():
    packet = build_packet({"question": "Report result", "history": [{"content": "continue"} for _ in range(1000)]})
    text, meta = render_sparse_view(packet, max_characters=1024)
    assert len(text) <= 1024
    assert meta["omitted_event_ids"]
    assert meta["packet_truncated"] is True


def test_actual_provider_uses_the_same_cap_for_transport_retries(monkeypatch):
    import json
    from detector.provider import provider_call
    monkeypatch.setenv("TEST_BOUNDED_KEY", "fixture")
    requests = []
    def transport(**kwargs):
        # The provider passes a serialized request object to its isolated transport.
        requests.append(kwargs)
        assert json.loads(kwargs["body"])["max_tokens"] == 8192
        if len(requests) == 1:
            return 503, b'{"error":"temporary fixture outage"}'
        return 200, json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()
    monkeypatch.setattr("detector.provider._isolated_transport", transport)
    monkeypatch.setattr("detector.provider.time.sleep", lambda seconds: None)
    _, meta = provider_call(route={"endpoint": "http://fixture.invalid/v1", "api_key_env": "TEST_BOUNDED_KEY",
                                  "max_tokens": 100000, "transport_retries": 1}, system="", user="x" * 50000, purpose="judge")
    assert meta["request_budget"]["output_token_limit"] == 8192
    assert len(requests) == 2
