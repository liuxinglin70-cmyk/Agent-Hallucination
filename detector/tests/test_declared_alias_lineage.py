import pytest

from detector.tests.test_claim_lineage import context, lineage_record, value_field


def wrapped_alias_record():
    record = lineage_record()
    record["history"][2]["content"] = {"middleware": {"payload": {
        **value_field(entity="renamed_result", source="event_002"),
        "source_binding": {"entity": "result", "attribute": "value", "unit": None, "time": None},
    }}}
    return record


def test_declared_rename_and_wrapper_preserve_the_full_consumption_chain():
    _, _, _, ctx = context(wrapped_alias_record())
    lineage = ctx["observed_lineage"]
    assert lineage["observed_event_ids"] == ["event_002", "event_003"]
    assert lineage["delivery_event_ids"] == ["event_003"]
    edge = lineage["edges"][-1]["field"]
    assert edge["source_binding"] == ["result", "value", None, None]
    assert edge["binding"] == ["renamed_result", "value", None, None]
    assert edge["target_path"] == ["middleware", "payload"]
    assert lineage["decision_authority"] == lineage["localization_authority"] == "none"


@pytest.mark.parametrize("change", ["undeclared", "wrong_entity", "wrong_unit", "malformed", "future", "changed_value"])
def test_rename_without_a_matching_preceding_source_cannot_bridge(change):
    record = wrapped_alias_record()
    field = record["history"][2]["content"]["middleware"]["payload"]
    if change == "undeclared":
        field.pop("source_binding")
    elif change == "wrong_entity":
        field["source_binding"]["entity"] = "different_claim"
    elif change == "wrong_unit":
        field["source_binding"]["unit"] = "meters"
    elif change == "malformed":
        field["source_binding"] = {"entity": "result"}
    elif change == "future":
        field["source_ref"] = "event_003"
    else:
        field["value"] = 7
    _, _, _, ctx = context(record)
    assert ctx["observed_lineage"]["delivery_event_ids"] == []
