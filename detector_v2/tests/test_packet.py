from __future__ import annotations

import pytest

from detector_v2.packet import (
    PacketError,
    build_packet,
    delivered_result,
    packet_features,
    render_defense_view,
    render_judge_view,
    validate_record,
)


def test_build_packet_shapes(sample_record, sample_packet):
    assert sample_packet["schema_version"] == "detector_v2.packet.v1"
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
