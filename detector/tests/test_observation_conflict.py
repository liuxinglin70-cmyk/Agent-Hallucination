"""Grounding rule 6: conflicted observations are not settled by the record.

Regression for the 2026-09-18 review of ``OpenDeepSearch/077``: the judge
convicted a delivered answer that agreed with the dataset reference and with
two trajectory observations, resting on its own belief that the other
observation's value was right. Rule 6 forbids that route; these tests pin the
law text, the assembly of the judge prompt, and the settled/conflicted
boundary that keeps the unconflicted world-knowledge convictions working.
"""
from __future__ import annotations

from detector import semantics
from detector.judge import run_judge

CONFIG = {
    "judge_route": {
        "endpoint": "http://unused", "model": "m", "api_key_env": "K",
        "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
    },
}


def test_conflict_rule_is_in_every_convicting_prompt():
    """MATERIALITY is the single authority for the three convicting stages."""
    for prompt in (semantics.JUDGE_SYSTEM, semantics.UPGRADE_SYSTEM,
                   semantics.DEFENSE_SYSTEM):
        assert "CONFLICTED OBSERVATIONS" in prompt


def test_conflict_rule_text_carries_both_directions():
    rule = semantics.MATERIALITY
    assert "different values for the same point" in rule
    # The restriction is bounded: unconflicted public facts stay checkable.
    assert "is still checked against real-world knowledge" in rule
    assert "record the point as undecided" in rule


def test_judge_prompt_actually_carries_the_rule():
    """The rule must reach the provider call, not merely the module."""
    captured = {}

    def fake_call(*, route, system, user, purpose):
        captured["system"] = system
        return {
            "is_hallucination": False,
            "hallucination_step": None,
            "hallucination_reason": None,
            "delivered_result": "",
            "correct_result": "",
            "basis": "observation",
            "confidence": "high",
            "evidence_refs": None,
            "root_cause": None,
            "contracts": [{"kind": "exact_value", "requirement": "the winning entity"}],
        }, {"attempts": 1, "model": "m", "usage": {}}

    import detector.judge as judge_mod

    original = judge_mod.provider_call
    judge_mod.provider_call = fake_call
    try:
        from detector.packet import build_packet

        packet = build_packet({
            "question": "Who won?",
            "history": [
                {"step": 1, "role": "assistant", "content": "",
                 "tool_calls": [{"name": "search", "arguments": {"q": "x"}}],
                 "tool_responses": ["A won the prize."]},
                {"step": 2, "role": "assistant", "content": "A won the prize."},
            ],
        })
        result, status = run_judge(packet=packet, config=CONFIG)
    finally:
        judge_mod.provider_call = original
    assert status["state"] == "ok"
    assert result["ok"] is True
    assert "CONFLICTED OBSERVATIONS" in captured["system"]


def test_rule_does_not_disable_unconflicted_world_knowledge():
    """A public fact no observation addresses stays convictable (023/088)."""
    rule_start = semantics.MATERIALITY.index("6. CONFLICTED OBSERVATIONS")
    tail = semantics.MATERIALITY[rule_start:]
    assert "no observation addresses" in tail
    assert "every observation agrees on" in tail
