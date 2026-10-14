import pytest

from detector.code_lineage import build_code_lineage, code_ancestor_events
from detector.packet import build_packet


def code_packet(codes):
    return build_packet({"question": "Compute and deliver the result", "history": [
        {"role": "Coder", "tool_calls": [{"name": "python_interpreter", "arguments": {"code": code}}],
         "tool_responses": ["recorded output"]} for code in codes]})


def test_renamed_variables_and_middleware_wrappers_reach_recorded_origin():
    packet = code_packet(["value = fetch()", "alias = value", "payload = {'data': alias}", "print(payload['data'])"])
    lineage = build_code_lineage(packet)
    assert code_ancestor_events(lineage, "event_004") == ["event_001", "event_002", "event_003"]
    assert lineage["execution_state"] == "unverified"
    assert lineage["decision_authority"] == lineage["localization_authority"] == "none"


@pytest.mark.parametrize("replacement", ["alias = 4", "del alias", "if flag:\n    alias = 4"])
def test_overwrite_deletion_and_unknown_control_cannot_keep_old_alias(replacement):
    packet = code_packet(["value = fetch()", "alias = value", replacement, "print(alias)"])
    ancestors = code_ancestor_events(build_code_lineage(packet), "event_004")
    assert "event_001" not in ancestors


def test_parse_hints_do_not_execute_recorded_code(tmp_path):
    target = tmp_path / "must_not_exist"
    packet = code_packet([f"open({str(target)!r}, 'w').write('wrong')"])
    assert build_code_lineage(packet)["edges"] == []
    assert not target.exists()


def test_code_capacity_and_syntax_failures_are_explicit():
    packet = code_packet(["v = fetch()", "a = v", "b = a", "bad code ???", "x" * 32001])
    lineage = build_code_lineage(packet, max_edges=1)
    assert len(lineage["edges"]) == 1
    assert lineage["omitted_interaction_ids"] == ["event_003_tool_01", "event_004_tool_01", "event_005_tool_01"]
