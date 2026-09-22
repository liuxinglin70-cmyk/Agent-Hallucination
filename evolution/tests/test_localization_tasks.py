"""Localization failures become development tasks, not just a final metric.

Before this, the development loop could not see a localization failure at all:
``stable_failure_families`` keeps only rows where ``prediction != gold``, so a
case the detector convicts correctly but localizes wrongly was dropped before
any family existed, and no candidate could be asked to fix it. The promotion
side had a localization objective (``target_kind``, the joint delta, the
localization BEHAVIOR/benefit gates), but nothing upstream ever produced such a
candidate — the objective had no producer.

These tests lock the producer side:

* the workspace derives ``FAM-loc-*`` families from convicted-correct but
  mislocated rows, under the same admission rules as detection families;
* a wrong step and a missing step are different families, because they need
  different treatments;
* the family's kind and the claim's ``target_kind`` must agree at STRUCTURAL,
  so a localization family cannot be scored by the binary gate (or the
  reverse) — an objective mismatch would hide the mechanism entirely;
* the engine decides whether a localization family is measurable from the
  localization opportunity, not from recall/precision counts.
"""

from __future__ import annotations

from evolution.engine import EvolutionEngine
from evolution.gates import structural_gate
from evolution.worksheet import (
    classify_family_actionability,
    localization_annotation_choices,
    localization_family,
    localization_repair_viability,
    localization_repair_viable,
    localization_state,
    stable_localization_families,
)

GOLD_STEPS = {
    "A/001.json": 3,
    "B/001.json": 3,
    "C/001.json": 9,
    "D/001.json": 5,
}


def _row(case, gold, prediction, step, *, source="judge", verdict="substantiated"):
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "authority": "authoritative",
        "responsible_step": step,
        "result": {
            "case_id": case,
            "state": "verified_error" if prediction else "verified_clean",
            "decision_authority": "authoritative",
            "runtime_valid": True,
            "responsible_step": step,
            "stage_status": {
                "judge": {"state": "ok"},
                "recall_scanner": {"state": "ok"},
                "causal_upgrade": {"state": "ok"},
                "step_tracer": {"state": "ok"},
                "defense": {"state": "ok"},
            },
            "proof": {
                "judge": {"is_hallucination": True},
                "defense": {"verdict": verdict},
                "terminal_basis": {
                    "decisive_facts": {"positive_source": source},
                },
            },
        },
    }


def test_localization_state_separates_the_three_failure_modes():
    assert localization_state(_row("A/001.json", True, True, 7), GOLD_STEPS) == "mislocated"
    assert localization_state(_row("A/001.json", True, True, None), GOLD_STEPS) == "step_missing"
    # Correct step is not a failure.
    assert localization_state(_row("A/001.json", True, True, 3), GOLD_STEPS) == "hit"
    # A miss is a detection failure, not a localization one.
    assert localization_state(_row("A/001.json", True, False, 7), GOLD_STEPS) == "not_convicted"
    # A clean case has no responsible step to score.
    assert localization_state(_row("X/001.json", False, True, 2), GOLD_STEPS) == "not_positive"
    # Unannotated positives are excluded, not silently counted as hits.
    assert localization_state(_row("Z/001.json", True, True, 2), GOLD_STEPS) == "unannotated"


def test_mislocated_and_step_missing_are_different_families():
    """Different fault, different family: the treatments differ."""
    mislocated = localization_family(_row("A/001.json", True, True, 7), gold_steps=GOLD_STEPS)
    missing = localization_family(_row("A/001.json", True, True, None), gold_steps=GOLD_STEPS)
    assert mislocated.startswith("FAM-loc-mislocated-")
    assert missing.startswith("FAM-loc-step_missing-")
    assert mislocated != missing


def test_localization_family_refuses_a_row_that_is_not_mislocated():
    """A family whose members were never shown to be mislocated is not a family.

    Deriving the state without the annotation used to yield
    ``FAM-loc-unannotated-*``, which would have put unannotated and
    correctly-localized rows into the proposer's task list.
    """
    import pytest

    with pytest.raises(ValueError, match="not a localization failure"):
        localization_family(_row("A/001.json", True, True, 7))  # no gold steps
    with pytest.raises(ValueError, match="not a localization failure"):
        localization_family(_row("A/001.json", True, True, 3), gold_steps=GOLD_STEPS)
    with pytest.raises(ValueError, match="not a localization failure"):
        localization_family(_row("A/001.json", True, False, 7), gold_steps=GOLD_STEPS)


def test_families_group_only_convicted_correct_but_mislocated_cases():
    rows = [
        _row("A/001.json", True, True, 7),   # mislocated
        _row("B/001.json", True, True, 8),   # mislocated, same signature
        _row("C/001.json", True, True, 9),   # hit -> excluded
        _row("D/001.json", True, False, 1),  # missed -> excluded
        _row("X/001.json", False, True, 2),  # clean -> excluded
    ]
    # Grouping is tested without the viability filter; the filter itself is
    # covered by the viability tests below.
    families = stable_localization_families(
        rows, gold_steps=GOLD_STEPS, min_cases=2, require_viable=False,
    )
    assert len(families) == 1
    family = families[0]
    assert family["target_kind"] == "localization"
    assert family["count"] == 2
    assert family["failure_cases"] == ["A/001.json", "B/001.json"]


def test_localization_family_opens_the_tracer_route():
    families = stable_localization_families(
        [_row("A/001.json", True, True, 7), _row("B/001.json", True, True, 8)],
        gold_steps=GOLD_STEPS,
        min_cases=2,
        require_viable=False,
    )
    actionability = classify_family_actionability(families[0])
    assert actionability["classification"] == "actionable"
    assert "tracer_directive" in actionability["routes"]


def test_structural_refuses_an_objective_the_family_cannot_deliver():
    """A detection claim on a localization family (and the reverse) is refused.

    Both directions matter: a localization family scored by the binary gate
    has its mechanism hidden, and a detection family claiming the localization
    objective would be scored by a gate that never looks at its mechanism.
    """
    loc_family = localization_family(
        _row("A/001.json", True, True, 7), gold_steps=GOLD_STEPS,
    )

    def candidate(family, target_kind, hook="tracer.system", polarity="boundary"):
        return {
            "intervention_id": "IV-T-001",
            "type": "text_boundary",
            "hook": hook,
            "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
            "body": {
                "directive": "Name the earliest step that introduced the error.",
                "not_when": "Never apply when the trajectory is clean.",
            },
            "claim": {
                "expected_behavior": "the tracer names the annotated step",
                "target_polarity": polarity,
                "target_kind": target_kind,
            },
            "provenance": {
                "cycle": 1,
                "failure_family": family,
                "failure_cases": ["A/001.json", "B/001.json"],
            },
            "schema_version": "detector.intervention.v1",
        }

    config = {"gates": {"structural_jaccard_threshold": 0.72}}
    family = {
        "failure_family": loc_family,
        "failure_cases": {"A/001.json", "B/001.json"},
        "count": 2,
        "diagnostics": {"predicate_signals": {}},
    }
    detection_family = {
        "failure_family": "FAM-fn-dev",
        "failure_cases": {"A/001.json", "B/001.json"},
        "count": 2,
        "diagnostics": {"predicate_signals": {}},
    }

    mismatched = structural_gate(
        candidate(loc_family, "detection"),
        active_entries=[],
        config=config,
        diagnosed_families=[family],
    )
    assert mismatched["passed"] is False
    assert "localization or" in mismatched["reason"]

    matched = structural_gate(
        candidate(loc_family, "localization"),
        active_entries=[],
        config=config,
        diagnosed_families=[family],
    )
    assert matched["passed"] is True

    # The reverse direction: a detection family declaring the localization
    # objective. Recall polarity is supplied so the check under test is the one
    # that fires, not the polarity rule that precedes it.
    reverse = structural_gate(
        candidate(
            "FAM-fn-dev", "localization", hook="judge.system", polarity="recall",
        ),
        active_entries=[],
        config=config,
        diagnosed_families=[detection_family],
    )
    assert reverse["passed"] is False
    assert "cannot claim target_kind" in reverse["reason"]


def _record(steps):
    """A label-free record shaped like ``project_record`` output."""
    return {
        "question": "task",
        "history": [
            {
                "step": index,
                "role": role,
                "content": content,
                "tool_calls": calls,
                "tool_responses": responses,
            }
            for index, (role, content, calls, responses) in enumerate(steps, 1)
        ],
    }


def test_a_step_with_no_text_cannot_host_a_content_error():
    """The BFCL/008 shape: the recall path names an argument-less ``ls`` call.

    Step 1 is ``ls {"a": true}`` -- no prose and no string argument, so it
    introduces no content and cannot be the origin of a content deviation.
    Naming it is wrong for a reason that does not depend on the annotation.
    """
    # Step 1 is the bare ``ls``; step 3 carries the tagged-in-content deviation.
    record = _record([
        ("assistant", "", [{"name": "ls", "arguments": {"a": True}}],
         ['{"files": ["file1.txt", "file2.txt"]}']),
        ("assistant", "The directory holds file1.txt and file2.txt.", [], None),
        ("assistant", "", [{"name": "post_tweet", "arguments": {
            "content": "file1.txt, file2.txt #fileshowcase"}}], ["ok"]),
    ])
    row = _row("BFCL/008.json", True, True, 1)
    classification, reason = localization_repair_viability(
        row, record, gold_steps={"BFCL/008.json": 3},
    )
    assert classification == "structurally_impossible"
    assert "carries no text" in reason
    assert localization_repair_viable(
        row, record, gold_steps={"BFCL/008.json": 3},
    )[0] is True


def test_a_later_step_carrying_its_own_text_needs_review():
    """The BFCL/148 shape: step 15 is a false escalation claim of its own.

    It is a real hallucination, just not the annotated one. Repairing it would
    mean teaching the detector to abandon a true detection.
    """
    record = _record([
        ("user", "book the flight", [], None),
        ("assistant", "", [{"name": "book_flight", "arguments": {
            "card_id": "1234-5678"}}], ['{"error": "Card not registered"}']),
        ("user", "still book it", [], None),
        ("assistant", "I have escalated your request to support.",
         [{"name": "contact_support", "arguments": {"message": "invoice"}}],
         ['{"error": "Booking not found"}']),
    ])
    row = _row("BFCL/148.json", True, True, 4)
    classification, reason = localization_repair_viability(
        row, record, gold_steps={"BFCL/148.json": 2},
    )
    assert classification == "needs_review"
    assert "later than the annotated" in reason


def test_an_observation_can_never_be_the_responsible_step():
    """Naming a tool observation is wrong for a structural reason.

    It belongs to the repairable class: no annotation argument can make an
    observation the origin of an assistant error.
    """
    record = _record([
        ("assistant", "", [{"name": "ls", "arguments": {"a": True}}], ["ok"]),
        ("tool", '{"files": ["a.txt"]}', [], None),
    ])
    row = _row("X/001.json", True, True, 2)
    classification, reason = localization_repair_viability(
        row, record, gold_steps={"X/001.json": 1},
    )
    assert classification == "structurally_impossible"
    assert "cannot originate in an observation" in reason


def test_viability_defaults_to_needs_review_without_a_record():
    """Absent evidence must not be read as proof of a repairable defect."""
    row = _row("A/001.json", True, True, 7)
    classification, reason = localization_repair_viability(
        row, None, gold_steps=GOLD_STEPS,
    )
    assert classification == "needs_review"
    assert "not checked" in reason


def test_the_family_builder_drops_rows_that_need_review_and_reports_them():
    """Only ``structurally_impossible`` rows become tasks.

    The excluded row must be visible in ``localization_annotation_choices``:
    dropping it silently would make the localization opportunity look larger
    than the repairable set.
    """
    record = _record([
        ("assistant", "", [{"name": "ls", "arguments": {"a": True}}], ["ok"]),
        ("assistant", "The directory holds a.txt.", [], None),
        ("assistant", "final answer", [], None),
    ])
    rows = [
        _row("A/001.json", True, True, 1),   # no text -> repairable
        _row("B/001.json", True, True, 2),   # carries text -> needs review
    ]
    records = {"A/001.json": record, "B/001.json": record}
    gold_steps = {"A/001.json": 3, "B/001.json": 3}

    families = stable_localization_families(
        rows, gold_steps=gold_steps, min_cases=1, records=records,
    )
    assert len(families) == 1
    assert families[0]["failure_cases"] == ["A/001.json"]

    excluded = localization_annotation_choices(
        rows, gold_steps=gold_steps, records=records,
    )
    assert [item["case"] for item in excluded] == ["B/001.json"]


def test_engine_measures_a_localization_family_by_localization_opportunity():
    """The gate that decides measurability must match the family's objective.

    Recall/precision opportunity counts say nothing about whether a
    localization claim can be scored: this batch has zero recall opportunity
    and one improvable localization, and only the second should matter.
    """
    control = [
        _row("A/001.json", True, True, 3),   # localized right -> preserved
        _row("B/001.json", True, True, 8),   # localized wrong -> improvable
        _row("C/001.json", True, True, 9),   # localized right -> preserved
    ]
    engine = EvolutionEngine.__new__(EvolutionEngine)
    engine.gold_step_map = dict(GOLD_STEPS)
    engine._coverage_mode = lambda _direction: "strict"
    engine._replacement_gain_cases = []

    class _NeverRuns:
        def run(self, *args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("no replacement run should be needed here")

    localization_family_id = localization_family(
        _row("B/001.json", True, True, 8), gold_steps=GOLD_STEPS,
    )
    # A detection family whose direction is recall, which this batch offers no
    # opportunity for: it must be blocked while the localization family is
    # ready, proving the two are judged by different opportunity counts.
    detection_family_id = "FAM-fn-judge_false_no_recall_candidates"
    ready, blocked = engine._measurable_families(
        [
            {"failure_family": localization_family_id},
            {"failure_family": detection_family_id},
        ],
        control,
        _NeverRuns(),
    )
    assert [f["failure_family"] for f in ready] == [localization_family_id]
    assert [f["failure_family"] for f in blocked] == [detection_family_id]
    assert blocked[0]["polarity"] == "recall"
    assert blocked[0]["reason"] == "no repair opportunities in gain"
