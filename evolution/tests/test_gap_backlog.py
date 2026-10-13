"""Regression coverage for the capability-gap backlog.

The backlog exists so an unexecutable failure family is a durable work item
rather than a line in one cycle report. These tests pin the properties that
make it usable: identity is stable across cycle and profile split, repeats do
not multiply entries but do advance ``occurrences``, new information about the
same gap is kept, and resolution is history rather than deletion.
"""

from __future__ import annotations

import json

import pytest

from evolution.backends import MockDetectorBackend
from evolution.config import load_config
from evolution.engine import EvolutionEngine
from evolution.gaps import (
    GAP_SCHEMA,
    gap_identity,
    load_gap_observations,
    open_gaps,
    gap_summary,
    record_gaps,
    resolve_gap,
)
from evolution.splits import EvolutionSplit


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


def test_repeat_does_not_multiply_entries_but_counts_occurrences(tmp_path):
    store = tmp_path / "gaps.jsonl"
    for cycle in (1, 2, 3):
        backlog = record_gaps(store, cycle=cycle, gaps=[_gap("FAM-fn-foo")])
    # One backlog entry, three sightings: identity dedups the entries, and the
    # repeated sighting is what ``occurrences`` counts. The count is the point
    # -- ``recurring_capability_gaps`` gates on ``occurrences >= 2``, so a
    # gap that is re-observed unchanged must not stay at 1.
    assert len(backlog) == 1
    assert backlog[0]["occurrences"] == 3
    assert backlog[0]["first_seen_cycle"] == 1
    assert backlog[0]["last_seen_cycle"] == 3
    assert len(load_gap_observations(store)) == 3


def test_same_gap_across_two_cycles_reaches_the_recurring_threshold(tmp_path):
    """The proposer's recurring signal needs a real second sighting.

    ``EvolutionEngine._proposal_memory_context`` offers a gap as a
    ``recurring_capability_gap`` only when the stored entry has
    ``occurrences >= 2``. While the store appended an observation only when
    the *content* changed, the same unchanged gap reported in ten rounds still
    read as ``occurrences == 1`` and the signal never fired.
    """
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-fp-judge_conviction-abc")])
    backlog = record_gaps(
        store, cycle=2, gaps=[_gap("FAM-fp-judge_conviction-abc")],
    )
    assert len(backlog) == 1
    assert backlog[0]["occurrences"] == 2


def test_ten_identical_rounds_make_the_gap_recurring(tmp_path):
    """The audit's shape: ten rounds of one unchanged gap.

    Measured before the fix: ``occurrences == 1`` after ten cycles, so the
    engine's ``recurring_capability_gaps`` (gated on ``occurrences >= 2``)
    never fired; the count only moved when some *other* field changed.
    """
    store = tmp_path / "gaps.jsonl"
    for cycle in range(1, 11):
        record_gaps(store, cycle=cycle, gaps=[_gap("FAM-fp-mech-abc")])
    entry = open_gaps(store)[0]
    assert entry["occurrences"] == 10
    assert entry["first_seen_cycle"] == 1
    assert entry["last_seen_cycle"] == 10


def _engine(tmp_path):
    import json as _json

    data = tmp_path / "data"
    cases = []
    for index in range(20):
        case = f"A/{index:03d}.json"
        cases.append(case)
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({
            "question": "task",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
            "is_hallucination": bool(index % 2 == 0),
            "hallucination_step": 1,
            "true_answer": "gold",
        }), encoding="utf-8")
    config = load_config()
    config.update({
        "dataset_root": str(data),
        "work_root": str(tmp_path / "work"),
        "registry_dir": str(tmp_path / "registry"),
        "ledger_path": str(tmp_path / "ledger.jsonl"),
        "harness_dir": str(tmp_path / "harness"),
        "memory_root": str(tmp_path / "memory"),
    })
    config["backend"].update({"name": "mock", "cache": False})
    split = EvolutionSplit(
        name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
        patrol_pool=cases[12:], mini=True,
    )
    return EvolutionEngine(
        config, backend=MockDetectorBackend(), split=split, mini=True,
    )


def test_engine_offers_a_twice_seen_gap_as_recurring(tmp_path):
    """End to end: two rounds of the same gap reach the proposer's memory.

    The store's counting and the engine's reader are pinned together here,
    because either half alone leaves the signal silent: ``occurrences`` has to
    advance, and the reader compares the stored family id with this round's
    verbatim.
    """
    engine = _engine(tmp_path)
    family = "FAM-fp-judge_conviction-63529fd9afbf-P70fe7f80fd90"
    for cycle in (1, 2):
        record_gaps(engine.gap_store, cycle=cycle, gaps=[_gap(family)])

    context = engine._proposal_memory_context(
        [{
            "failure_family": family,
            "count": 2,
            "actionability": {"routes": []},
        }],
        [],
    )
    recurring = context["recurring_capability_gaps"]
    assert [item["failure_family"] for item in recurring] == [family]
    assert recurring[0]["occurrences"] == 2
    # One sighting is not recurring.
    fresh = "FAM-fp-judge_conviction-other"
    record_gaps(engine.gap_store, cycle=3, gaps=[_gap(fresh)])
    context = engine._proposal_memory_context(
        [{"failure_family": fresh, "count": 2, "actionability": {"routes": []}}],
        [],
    )
    assert context["recurring_capability_gaps"] == []


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


def test_a_gap_re_observed_after_resolution_is_open_again(tmp_path):
    """Resolution closes an epoch; a later sighting is a new open gap.

    A capability that was built and later observed missing again is exactly
    the information the backlog exists to carry. The previous behaviour let
    the stale resolution permanently close the entry, so the strongest
    recurring-gap signal could never fire for a gap that came back.
    """
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-fn-foo")])
    identity = open_gaps(store)[0]["identity"]
    resolve_gap(store, identity=identity, cycle=2, note="capability built")
    assert open_gaps(store) == []

    # Cycle 4 observes the same gap again: it is open, and the resolution is
    # kept as history (`resolved_earlier_cycle`) rather than erased.
    backlog = record_gaps(store, cycle=4, gaps=[_gap("FAM-fn-foo")])
    assert [item["failure_family"] for item in backlog] == ["FAM-fn-foo"]
    assert backlog[0]["last_seen_cycle"] == 4
    assert backlog[0]["resolved_cycle"] is None
    assert backlog[0]["resolved_earlier_cycle"] == 2
    summary = gap_summary(store)
    assert summary["open"] == 1
    assert summary["resolved"] == 0


def test_a_replayed_resolution_still_closes_the_entry(tmp_path):
    """The same resolved epoch replayed must not reopen the gap.

    A resumed run can re-append the resolution after the observation; the
    resolution belongs to that epoch and must still close it.
    """
    store = tmp_path / "gaps.jsonl"
    record_gaps(store, cycle=1, gaps=[_gap("FAM-fn-foo")])
    identity = open_gaps(store)[0]["identity"]
    resolve_gap(store, identity=identity, cycle=1, note="capability built")
    resolve_gap(store, identity=identity, cycle=1, note="capability built")
    assert open_gaps(store) == []
    assert gap_summary(store)["open"] == 0


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
