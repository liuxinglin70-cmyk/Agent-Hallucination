"""Regression coverage for the capability-gap backlog.

The backlog exists so an unexecutable failure family is a durable work item
rather than a line in one cycle report. These tests pin the properties that
make it usable: identity is stable across cycle and profile split, repeats do
not multiply entries, new information about the same gap is kept, and
resolution is history rather than deletion.
"""

from __future__ import annotations

import pytest

from evolution.gaps import (
    GAP_SCHEMA,
    gap_identity,
    load_gap_observations,
    open_gaps,
    gap_summary,
    record_gaps,
    resolve_gap,
)


def _gap(family, classification="capability_gap", reason="no route", count=2,
         routes=("evolved_predicate",)):
    return {
        "failure_family": family,
        "classification": classification,
        "reason": reason,
        "count": count,
        "sources": ["BFCL"],
        "routes": list(routes),
    }


def test_identity_is_stable_across_cycle_and_profile_split():
    plain = gap_identity("FAM-fn-foo", "capability_gap")
    split = gap_identity("FAM-fn-foo-Pa007b166787d", "capability_gap")
    # The worksheet appends a profile suffix when it splits a family by
    # predicate; the backlog must still see one gap, not one per split.
    assert plain == split == "FAM-fn-foo|capability_gap"


def test_recording_a_gap_makes_it_a_backlog_item(tmp_path):
    store = tmp_path / "gaps.jsonl"
    backlog = record_gaps(store, cycle=1, gaps=[_gap("FAM-fn-foo")])
    assert len(backlog) == 1
    item = backlog[0]
    assert item["failure_family"] == "FAM-fn-foo"
    assert item["reason"] == "no route"
    assert item["routes"] == ["evolved_predicate"]
    assert item["first_seen_cycle"] == 1
    assert item["last_seen_cycle"] == 1
    assert item["occurrences"] == 1
    assert item["resolved_cycle"] is None


def test_unchanged_repeat_does_not_multiply_entries(tmp_path):
    store = tmp_path / "gaps.jsonl"
    for cycle in (1, 2, 3):
        backlog = record_gaps(store, cycle=cycle, gaps=[_gap("FAM-fn-foo")])
    assert len(backlog) == 1
    assert backlog[0]["occurrences"] == 1
    assert backlog[0]["first_seen_cycle"] == 1
    # An identical repeat is not new information, so it is not appended at all.
    assert len(load_gap_observations(store)) == 1


def test_new_information_about_the_same_gap_is_kept(tmp_path):
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-fn-foo")])
    backlog = record_gaps(
        store, cycle=2,
        gaps=[_gap("FAM-fn-foo", reason="different reason")],
    )
    assert len(backlog) == 1
    item = backlog[0]
    assert item["occurrences"] == 2
    assert item["first_seen_cycle"] == 1
    assert item["last_seen_cycle"] == 2
    assert item["reason"] == "different reason"


def test_resumed_cycle_does_not_double_count(tmp_path):
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-fn-foo")])
    # A resume replays the same cycle write.
    record_gaps(store, cycle=1, gaps=[_gap("FAM-fn-foo")])
    assert len(load_gap_observations(store)) == 1


def test_resolution_is_recorded_as_history_not_deletion(tmp_path):
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-fn-foo")])
    identity = open_gaps(store)[0]["identity"]
    resolve_gap(store, identity=identity, cycle=3, note="capability built")
    assert open_gaps(store) == []
    # The observation survives, so a handled gap stays explainable.
    assert len(load_gap_observations(store)) == 2
    summary = gap_summary(store)
    assert summary["open"] == 0
    assert summary["resolved"] == 1


def test_resolving_an_unknown_gap_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="unknown capability gap"):
        resolve_gap(tmp_path / "gaps.jsonl", identity="nope", cycle=1, note="x")


def test_backlog_orders_by_frequency(tmp_path):
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-a")])
    record_gaps(store, cycle=2, gaps=[_gap("FAM-b")])
    # A second write inside the same cycle is the same observation (this is
    # also what a resumed cycle replays), so it does not add an occurrence.
    record_gaps(store, cycle=2, gaps=[_gap("FAM-b", reason="changed")])
    record_gaps(store, cycle=3, gaps=[_gap("FAM-b", reason="changed")])
    backlog = open_gaps(store)
    assert backlog[0]["failure_family"] == "FAM-b"
    assert backlog[0]["occurrences"] == 2
    assert backlog[1]["failure_family"] == "FAM-a"
    assert backlog[1]["occurrences"] == 1


def test_one_observation_per_gap_per_cycle(tmp_path):
    """At most one observation per gap per cycle, regardless of rewrites."""
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-foo")])
    record_gaps(store, cycle=1, gaps=[_gap("FAM-foo", reason="different")])
    assert len(load_gap_observations(store)) == 1


def test_summary_groups_open_gaps(tmp_path):
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[
        _gap("FAM-a", classification="capability_gap", reason="no route"),
        _gap("FAM-b", classification="unclassified", reason="unclear"),
    ])
    summary = gap_summary(store)
    assert summary["schema_version"] == GAP_SCHEMA
    assert summary["open"] == 2
    assert summary["by_classification"] == {"capability_gap": 1, "unclassified": 1}
    assert summary["by_reason"] == {"no route": 1, "unclear": 1}


def test_empty_store_is_an_empty_backlog(tmp_path):
    store = tmp_path / "does-not-exist.jsonl"
    assert open_gaps(store) == []
    assert gap_summary(store)["open"] == 0


def test_unsupported_schema_is_rejected(tmp_path):
    store = tmp_path / "gaps.jsonl"
    store.write_text(
        '{"schema_version": "evolution.capability_gap.v99"}\n', encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported schema"):
        open_gaps(store)


def test_malformed_store_fails_loudly(tmp_path):
    store = tmp_path / "gaps.jsonl"
    store.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        open_gaps(store)
