from __future__ import annotations

import pytest

from detector.packet import (
    PacketError,
    build_packet,
    delivered_result,
    event_id_for_step,
    evidence_refs_support_event,
    evidence_refs_support_step,
    packet_features,
    packet_reference_index,
    render_defense_view,
    render_judge_view,
    validate_record,
    validate_packet,
)


def test_build_packet_shapes(sample_record, sample_packet):
    assert sample_packet["schema_version"] == "detector.packet.v1"
    assert [e["position"] for e in sample_packet["events"]] == [1, 2]
    tool = sample_packet["events"][0]["tool_interactions"][0]
    assert tool["interaction_id"] == "event_001_tool_01"
    assert tool["tool_name"] == "calculator"


def test_validate_record_rejects_gold_and_metadata(sample_record):
    validate_record(sample_record)
    bad = {**sample_record, "true_answer": "4"}
    with pytest.raises(PacketError):
        validate_record(bad)
    bad = {**sample_record, "model_id": "x"}
    with pytest.raises(PacketError):
        validate_record(bad)


def test_validate_record_rejects_unknown_top_level():
    with pytest.raises(PacketError):
        validate_record({"question": "q", "history": [], "extra": 1})


def test_validate_record_requires_nonempty_question(sample_record):
    for question in (None, "", "  "):
        bad = {**sample_record, "question": question}
        with pytest.raises(PacketError):
            validate_record(bad)


def test_validate_record_recurses_through_tool_observations(sample_record):
    bad = {**sample_record, "history": [
        {
            "role": "tool",
            "tool_responses": [{"nested": {"true_answer": "4"}}],
        },
    ]}
    with pytest.raises(PacketError):
        validate_record(bad)


def test_validate_record_allows_source_in_tool_arguments(sample_record):
    """File-tool arguments legitimately name source/destination paths."""
    record = {**sample_record, "history": [
        {
            "role": "assistant",
            "tool_calls": [{
                "name": "move_file",
                "arguments": {"source": "log.txt", "destination": "out.txt"},
            }],
        },
    ]}
    validate_record(record)


def test_delivered_result(sample_packet):
    result = delivered_result(sample_packet)
    assert result["last_prose_event_id"] == "event_002"
    assert result["has_explicit_final_answer"] is False


def test_judge_view_and_features(sample_packet):
    text, meta = render_judge_view(sample_packet)
    assert "QUESTION" in text and "event_002" in text
    assert meta["degraded"] is False
    features = packet_features(sample_packet)
    assert "has_tool_calls" in features
    assert "has_numeric_content" in features


def test_defense_view_cited(sample_packet):
    text, meta = render_defense_view(sample_packet, 2)
    assert "[CITED]" in text
    assert "DELIVERED RESULT ANCHORS" in text
    assert meta["packet_truncated"] is False


def test_build_packet_flattens_multi_turn_question(sample_record):
    record = {**sample_record, "question": [["first turn"], ["second turn"]]}
    packet = build_packet(record)
    assert packet["question"] == (
        "[turn 1] first turn\n[turn 2] second turn"
    )
    assert "multi_turn_question" in packet_features(packet)


def test_build_packet_keeps_single_turn_list_question(sample_record):
    record = {**sample_record, "question": [["only turn"]]}
    packet = build_packet(record)
    assert packet["question"] == "only turn"
    assert "multi_turn_question" not in packet_features(packet)


def test_build_packet_rejects_empty_question_turns(sample_record):
    for question in (None, [], [[]], ["ok", " "]):
        record = {**sample_record, "question": question}
        with pytest.raises(PacketError):
            build_packet(record)


def test_reference_ownership_uses_observed_interactions(sample_packet):
    assert packet_reference_index(sample_packet) == {
        "event_001": "event_001", "event_001_tool_01": "event_001",
        "event_002": "event_002",
    }
    assert evidence_refs_support_step(sample_packet, ["event_001_tool_01"], 1)
    assert not evidence_refs_support_step(sample_packet, ["event_001_tool_99"], 1)
    assert not evidence_refs_support_step(sample_packet, ["event_001_tool_01"], 2)
    assert evidence_refs_support_event(
        sample_packet, ["event_001"], "event_001_tool_01",
    )
    assert not evidence_refs_support_event(sample_packet, ["missing"], "missing")


def test_packet_positions_do_not_bind_independent_tool_observations():
    packet = build_packet({"question": [["first task"], ["second task"]], "history": [
        {"step": 10, "role": "assistant", "tool_calls": [{"name": "fetch"}]},
        {"step": 20, "role": "tool", "content": {"value": 4}},
        {"step": 40, "role": "assistant", "content": "4"},
    ]})
    assert [event["source_step_id"] for event in packet["events"]] == [10, 20, 40]
    assert event_id_for_step(packet, 2) == "event_002"
    assert event_id_for_step(packet, 20) is None
    assert event_id_for_step(packet, True) is None
    assert packet["events"][0]["tool_interactions"][0]["observation"] is None
    assert packet["events"][1]["tool_interactions"] == []


@pytest.mark.parametrize("position", [True, "1", 1.0])
def test_validate_packet_rejects_coerced_positions(sample_packet, position):
    sample_packet["events"][0]["position"] = position
    with pytest.raises(PacketError, match="positions must be integers"):
        validate_packet(sample_packet)
