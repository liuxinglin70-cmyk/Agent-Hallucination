import pytest

from detector.packet import delivered_result, packet_features
from detector.predicates import predicate_delivered_anchor_consistency


@pytest.mark.parametrize("code,recognized", [
    ('final_answer("76 tons")', True),
    ('value = 76\nfinal_answer(value)', True),
    ('# final_answer("76 tons")', False),
    ('def f():\n    final_answer("76 tons")', False),
    ('if False:\n    final_answer("76 tons")', False),
    ('print(\'final_answer("76 tons")\')', False),
    ('not valid python', False),
])
def test_wrapped_delivery_is_syntactic_and_consistent_with_routing(code, recognized):
    packet = {"events": [{"event_id": "event_001", "position": 1,
        "tool_interactions": [{"tool_name": "Code Action Space", "arguments": code}]}]}
    result = delivered_result(packet)
    assert result["has_explicit_final_answer"] is recognized
    assert ("has_answer_tool" in packet_features(packet)) is recognized
    assert result["answer_anchor_status"] == ("recognized" if recognized else "unknown")
    if recognized:
        assert result["last_answer_tool_call"]["arguments"] == code
        assert result["last_answer_tool_call"]["wrapper_tool_name"] == "Code Action Space"


def test_literal_mismatch_does_not_prove_semantic_contradiction():
    packet = {"events": [{"event_id": "event_001", "position": 1,
                         "content": "The answer is four.", "tool_interactions": []}]}
    assert predicate_delivered_anchor_consistency(packet, {"delivered_result": "4"})["verdict"] == "insufficient"
