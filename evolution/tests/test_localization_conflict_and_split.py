"""Localization conflict and event-kind contracts.

Synthetic packets verify current adjudication, output and metric boundaries.
The corpus audit reads original archived step disagreements and event kinds;
it does not replay historical stage outputs as current-contract evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from detector.adjudication import (
    AUTHORITY_AUTHORITATIVE,
    AUTHORITY_DEGRADED_JUDGE_ONLY,
    LOCALIZATION_AGREEMENT_TRACER_EARLIER,
    LOCALIZATION_AUTHORITY_AUTHORITATIVE,
    LOCALIZATION_AUTHORITY_DEGRADED_CONFLICT,
    LOCALIZATION_CONFLICT_FIRST_HEARING_TRACER,
    LOCALIZATION_STEP_PRIORITY,
    adjudicate,
)
from detector.packet import (
    STEP_KIND_ACTION,
    STEP_KIND_CONTENT,
    STEP_KIND_EMPTY,
    build_packet,
    step_kind,
)
from detector.result import build_detection_output, prediction_state
from evolution.engine import _step_kind_from_record, _step_localization_check
from evolution.metrics import (
    STEP_KIND_UNCLASSIFIED,
    localization_metrics,
    paired_localization_delta,
    row_localization_conflicting,
    row_responsible_step_kind,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = REPO_ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
ARCHIVE = REPO_ROOT / "evolution/tests/fixtures/localization_replay.json"

# 5 events: act, speak, act, act, speak. Positions 1/3/4 are action steps.
RECORD = {
    "question": "Do the task.",
    "history": [
        {"step": 1, "role": "assistant", "content": "",
         "tool_calls": [{"name": "ls", "arguments": {"a": True}}],
         "tool_responses": ["files"]},
        {"step": 2, "role": "assistant", "content": "Files are file1, file2."},
        {"step": 3, "role": "assistant", "content": "",
         "tool_calls": [{"name": "post", "arguments": {"text": "wrong"}}],
         "tool_responses": ["posted"]},
        {"step": 4, "role": "assistant", "content": "",
         "tool_calls": [{"name": "ls", "arguments": {}}],
         "tool_responses": ["files"]},
        {"step": 5, "role": "assistant", "content": "Done."},
    ],
}
PACKET = build_packet(RECORD)

JUDGE_PARSED = {
    "is_hallucination": True,
    "hallucination_step": 3,
    "hallucination_reason": "wrong text",
    "delivered_result": "wrong",
    "correct_result": "right",
    "basis": "task_requirement",
    "confidence": "high",
    "evidence_refs": ["event_003"],
    "root_cause": {"step": 3, "quote": "wrong", "referent": "right"},
    "contracts": [{"kind": "exact_value", "requirement": "the right text"}],
}


def _tracer(step: int) -> dict:
    return {
        "ok": True,
        "parsed": {
            "step": step,
            "quote": "q",
            "referent": "r",
            "evidence_refs": [f"event_{step:03d}"],
            "reason": "earliest",
        },
    }


def _defense(verdict: str, step: int | None) -> dict:
    return {
        "ok": True,
        "parsed": {
            "verdict": verdict,
            "confirmed_step": step,
            "evidence_refs": [f"event_{step:03d}"] if step else ["event_001"],
            "reason": "r",
        },
    }


def _hearing(
    source: str,
    step: int,
    verdict: str,
    tracer_step: int | None,
    confirmed_step: int | None,
    candidate_id: str | None = None,
) -> dict:
    return {
        "source": source,
        "candidate_id": candidate_id,
        "step": step,
        "reason": "allegation",
        "delivered_result": "wrong",
        "correct_result": "right",
        "evidence_refs": [f"event_{step:03d}"],
        "quote": "q",
        "tracer_result": _tracer(tracer_step) if tracer_step else {
            "ok": False, "parsed": None, "error": "contract_error",
            "error_type": "contract_error",
        },
        "defense_result": _defense(verdict, confirmed_step),
    }


def _run(hearings, *, judge=None, upgrade=None, packet=PACKET):
    first = hearings[0]
    return adjudicate(
        packet=packet,
        judge_result={"ok": True, "parsed": judge or dict(JUDGE_PARSED)},
        judge_status={"stage": "judge", "state": "ok"},
        recall_result={"ok": True, "candidates": []},
        recall_status={"stage": "recall_scanner", "state": "ok"},
        upgrade_result=upgrade or {
            "ok": True, "upgrades": [], "upgraded_candidate": None,
            "upgraded_candidates": [],
        },
        upgrade_status={"stage": "causal_upgrade", "state": "ok"},
        tracer_result=first.get("tracer_result"),
        tracer_status={"stage": "step_tracer", "state": "ok"},
        defense_result=first.get("defense_result"),
        defense_status={"stage": "defense", "state": "ok"},
        hearings=hearings,
    )


# --------------------------------------------------------------------------
# Task 1: contradictions are recorded and degrade localization authority
# --------------------------------------------------------------------------
def test_tracer_naming_an_earlier_step_is_recorded_not_charged():
    """The tracer disagreeing with the defense's restated allegation.

    ``detector/defense.py`` pins ``confirmed_step`` to the alleged step, so a
    differing tracer can only be reporting an *earlier* locus -- the one thing
    ``LOCALIZATION_LAW`` asks of it. The adopted step is then the tracer's, the
    row agrees with itself, and the disagreement is recorded in
    ``localization_sources`` rather than charged as a contradiction. Charging
    it would make the signal mean "the tracer worked".
    """
    proof = _run([_hearing("judge", 3, "substantiated",
                           tracer_step=2, confirmed_step=3)])
    assert proof["state"] == "verified_error"
    assert proof["responsible_step"] == 2           # tracer keeps priority
    assert proof["localization_conflict"] is None
    assert proof["localization_authority"] == (
        LOCALIZATION_AUTHORITY_AUTHORITATIVE
    )
    sources = proof["localization_sources"]
    assert sources["resolution"] == LOCALIZATION_AGREEMENT_TRACER_EARLIER
    assert sources["adopted_rule"] == LOCALIZATION_STEP_PRIORITY
    assert sources["adopted_step"] == 2
    assert "convicted_hearing_defense_confirmed" in sources["overridden_sources"]
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE


def test_first_hearing_tracer_disagreeing_with_adopted_step_is_flagged():
    """The exact archived shape: judge hearing refuted, later hearing convicts.

    ``proof.step_tracer`` mirrors the *first* hearing, so a row here reports
    step 5 in its tracer slot and step 1 as its responsible step. That is two
    responsible steps on one row, and it must not read as authoritative
    localization.
    """
    upgrade = {
        "ok": True,
        "upgrades": [{"candidate_id": "c1", "corrected_step": 1}],
        "upgraded_candidate": {"candidate_id": "c1", "corrected_step": 1},
        "upgraded_candidates": [{"candidate_id": "c1", "corrected_step": 1}],
    }
    hearings = [
        _hearing("judge", 5, "refuted", tracer_step=5, confirmed_step=None),
        _hearing("recall_upgrade", 1, "substantiated",
                 tracer_step=1, confirmed_step=1, candidate_id="c1"),
    ]
    proof = _run(hearings, upgrade=upgrade)
    assert proof["state"] == "verified_error"
    assert proof["responsible_step"] == 1
    # What the envelope reports as the tracer's step is the first hearing's.
    assert proof["terminal_basis"]["decisive_facts"]["traced_step"] == 1
    conflict = proof["localization_conflict"]
    assert conflict is not None
    assert LOCALIZATION_CONFLICT_FIRST_HEARING_TRACER in conflict["disagreements"]
    assert conflict["first_hearing_tracer_step"] == 5
    assert conflict["adopted_step"] == 1
    assert conflict["adopted_rule"] == LOCALIZATION_STEP_PRIORITY
    assert proof["localization_authority"] == (
        LOCALIZATION_AUTHORITY_DEGRADED_CONFLICT
    )
    assert proof["terminal_basis"]["decisive_facts"][
        "localization_resolution"
    ] == LOCALIZATION_CONFLICT_FIRST_HEARING_TRACER
    # The verdict itself is untouched: which locus was adopted never decided
    # whether the case was convicted.
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE


def test_agreeing_sources_record_no_conflict():
    """An uncontested conviction carries no conflict object and stays clean."""
    proof = _run([_hearing("judge", 3, "substantiated",
                           tracer_step=3, confirmed_step=3)])
    assert proof["localization_conflict"] is None
    assert proof["localization_authority"] == (
        LOCALIZATION_AUTHORITY_AUTHORITATIVE
    )
    sources = proof["localization_sources"]
    assert sources["resolution"] == "all_sources_agree"
    assert sources["overridden_sources"] == []
    assert {entry["step"] for entry in sources["sources"]} == {3}


def test_tracer_earlier_than_the_allegation_is_not_a_conflict():
    """Finding an earlier locus than the allegation is the tracer's job.

    Treating that as a disagreement would degrade every successful
    early-locus trace, which is the tracer's entire purpose.
    """
    proof = _run([_hearing("judge", 4, "substantiated",
                           tracer_step=2, confirmed_step=4)])
    assert proof["responsible_step"] == 2
    assert proof["localization_conflict"] is None
    assert proof["localization_sources"]["resolution"] == (
        LOCALIZATION_AGREEMENT_TRACER_EARLIER
    )


def test_defense_fallback_step_is_adopted_when_the_tracer_failed():
    """The documented priority still applies: tracer, else confirmation."""
    proof = _run([_hearing("judge", 3, "substantiated",
                           tracer_step=None, confirmed_step=3)])
    assert proof["responsible_step"] == 3
    # One source spoke and nothing contradicted it.
    assert proof["localization_conflict"] is None


def test_upgraded_defense_verdict_is_not_read_as_a_conflicting_step():
    """``substantiated_upgraded`` has no confirmed step by contract.

    Reading its ``null`` as a disagreement would invent a conflict the vote
    never produced.
    """
    proof = _run([_hearing("judge", 3, "substantiated_upgraded",
                           tracer_step=3, confirmed_step=None)])
    assert proof["localization_authority"] == (
        LOCALIZATION_AUTHORITY_AUTHORITATIVE
    )
    assert proof["localization_conflict"] is None
    assert proof["decision_authority"] == AUTHORITY_DEGRADED_JUDGE_ONLY


# --------------------------------------------------------------------------
# Task 2: the step kind is structural, and it reaches the envelope
# --------------------------------------------------------------------------
def test_step_kind_reads_event_structure_not_text():
    assert step_kind(PACKET, 1) == STEP_KIND_ACTION     # tool call, no prose
    assert step_kind(PACKET, 2) == STEP_KIND_CONTENT    # prose only
    assert step_kind(PACKET, 4) == STEP_KIND_ACTION
    assert step_kind(PACKET, 99) is None                # out of range: unknown


def test_step_kind_is_content_for_an_event_that_both_speaks_and_acts():
    """A step with prose is locally checkable text, so it is a content step."""
    packet = build_packet({
        "question": "q",
        "history": [
            {"step": 1, "role": "assistant", "content": "I will post it.",
             "tool_calls": [{"name": "post", "arguments": {}}],
             "tool_responses": ["ok"]},
        ],
    })
    assert step_kind(packet, 1) == STEP_KIND_CONTENT


def test_step_kind_reports_empty_when_the_event_has_neither():
    packet = build_packet({
        "question": "q",
        "history": [{"step": 1, "role": "assistant", "content": ""}],
    })
    assert step_kind(packet, 1) == STEP_KIND_EMPTY


def test_conviction_envelope_records_the_step_kind_and_conflict():
    """The second hearing convicts at a step the row's tracer slot denies."""
    upgrade = {
        "ok": True,
        "upgrades": [{"candidate_id": "c1", "corrected_step": 3}],
        "upgraded_candidate": {"candidate_id": "c1", "corrected_step": 3},
        "upgraded_candidates": [{"candidate_id": "c1", "corrected_step": 3}],
    }
    hearings = [
        _hearing("judge", 4, "refuted", tracer_step=4, confirmed_step=None),
        _hearing("recall_upgrade", 3, "substantiated",
                 tracer_step=3, confirmed_step=3, candidate_id="c1"),
    ]
    proof = _run(hearings, upgrade=upgrade)
    assert proof["responsible_step_kind"] == STEP_KIND_ACTION
    output = build_detection_output(
        case_id="A/1.json", packet=PACKET, proof=proof,
        stage_status={}, providers={}, provenance={},
    )
    assert output["responsible_step_kind"] == STEP_KIND_ACTION
    assert output["localization_authority"] == (
        LOCALIZATION_AUTHORITY_DEGRADED_CONFLICT
    )
    assert output["localization_conflict"]["adopted_step"] == 3
    assert output["localization_conflict"][
        "first_hearing_tracer_step"
    ] == 4
    # The detection contract downstream reads is unchanged by any of this.
    assert prediction_state(output) is True


def test_split_metrics_exclude_conflicted_rows_and_never_mix_kinds():
    def row(case, step, kind, prediction=True, conflict=None):
        return {
            "case": case,
            "gold": True,
            "prediction": prediction,
            "responsible_step": step,
            "responsible_step_kind": kind,
            "localization_conflict": conflict,
        }

    rows = [
        row("A/1.json", 1, STEP_KIND_ACTION),          # hit, action step
        row("B/1.json", 5, STEP_KIND_ACTION),          # miss, action step
        row("C/1.json", 3, STEP_KIND_CONTENT),         # hit, content step
        row("D/1.json", 9, STEP_KIND_CONTENT),         # miss, content step
        row("E/1.json", 2, STEP_KIND_ACTION,           # conflicted: excluded
            conflict={"disagreements": ["first_hearing_tracer_vs_adopted"]}),
    ]
    gold = {"A/1.json": 1, "B/1.json": 2, "C/1.json": 3, "D/1.json": 4,
            "E/1.json": 2}
    metrics = localization_metrics(rows, gold_steps=gold)
    # Headline numbers keep their old meaning, conflict row included.
    assert metrics["annotated_positive"] == 5
    assert metrics["authoritative_detected"] == 5
    # 3 hits: A/1 and C/1 and the conflicted E/1, which the headline count
    # still scores exactly as before the split existed.
    assert metrics["joint_hits"] == 3
    assert metrics["localization_conflict_rows"] == 1
    split = metrics["step_kinds"]
    # The conflicted action row is out of the decomposition, so only one of
    # the two action-step rows is scorable.
    assert split["action_step_scorable"] == 2
    assert split["action_step_hits"] == 1
    assert split["content_step_scorable"] == 2
    assert split["content_step_hits"] == 1
    assert split["action_step_accuracy"] == 0.5
    assert split["content_step_accuracy"] == 0.5
    # Never a combined number.
    assert "step_kinds_accuracy" not in metrics


def test_paired_delta_decomposes_each_arm_by_its_own_kind():
    def row(case, kind, step, prediction=True):
        return {
            "case": case, "gold": True, "prediction": prediction,
            "responsible_step": step, "responsible_step_kind": kind,
        }

    gold = {"A/1.json": 1, "B/1.json": 2}
    baseline = [
        row("A/1.json", STEP_KIND_ACTION, 1),          # correct
        row("B/1.json", STEP_KIND_CONTENT, 9),         # wrong
    ]
    active = [
        row("A/1.json", STEP_KIND_ACTION, 1),          # preserved
        row("B/1.json", STEP_KIND_CONTENT, 2),         # repaired
    ]
    delta = paired_localization_delta(baseline, active, gold_steps=gold)
    assert delta["joint_delta"] == 1
    assert delta["joint_improved"] == 1
    assert delta["localization_conflict_rows"] == 0
    assert delta["baseline_step_kinds"]["action_step_hits"] == 1
    assert delta["baseline_step_kinds"]["content_step_hits"] == 0
    assert delta["active_step_kinds"]["content_step_hits"] == 1
    # Each arm is classified by the step it blamed, not by the other arm's.
    assert delta["rows"][1]["baseline_step_kind"] == STEP_KIND_CONTENT
    assert delta["rows"][1]["active_step_kind"] == STEP_KIND_CONTENT


def test_rows_without_the_field_are_unclassified_not_guessed():
    """An old row must not be silently bucketed by a fabricated kind."""
    row = {"case": "A/1.json", "gold": True, "prediction": True,
           "responsible_step": 3}
    assert row_responsible_step_kind(row) == STEP_KIND_UNCLASSIFIED
    assert row_localization_conflicting(row) is False
    assert row_responsible_step_kind(
        {"result": {"proof": {"responsible_step_kind": STEP_KIND_CONTENT}}}
    ) == STEP_KIND_CONTENT
    # An unknown value is not accepted as a kind.
    assert row_responsible_step_kind(
        {"responsible_step_kind": "probably_action"}
    ) == STEP_KIND_UNCLASSIFIED


def test_engine_split_classifies_legacy_rows_from_the_trajectory(tmp_path):
    """A row written before the field existed is still classified, correctly.

    ``_step_localization_check`` already reads the dataset record for the gold
    step; the split applies the same structural rule to that same record, so a
    legacy row is decomposed by measurement rather than skipped into
    ``unclassified``.
    """
    case = "BFCL/002.json"
    source = DATASET_ROOT / case
    if not source.exists():
        pytest.skip("raw dataset is not present")
    dataset_root = tmp_path / "data"
    (dataset_root / "BFCL").mkdir(parents=True)
    (dataset_root / case).write_text(
        source.read_text(encoding="utf-8-sig"), encoding="utf-8",
    )
    raw = json.loads((dataset_root / case).read_text(encoding="utf-8-sig"))
    gold_step = int(raw["hallucination_step"])
    kind = _step_kind_from_record(raw, gold_step)
    assert kind in {STEP_KIND_CONTENT, STEP_KIND_ACTION, STEP_KIND_EMPTY}
    check = _step_localization_check(
        str(dataset_root),
        [{"case": case, "gold": True, "prediction": True,
          "result": {"responsible_step": gold_step}}],
        [{"case": case, "gold": True, "prediction": True,
          "result": {"responsible_step": gold_step}}],
    )
    assert check["active_hits"] == 1
    assert check["not_regressed"] is True
    assert check["active_classified"] == 1
    assert check["step_kind_split"][f"active_{kind}_hits"] == 1
    assert check["step_kind_split"][f"active_{kind}_scorable"] == 1
    # No other kind picked up the row.
    others = [
        key for key, value in check["step_kind_split"].items()
        if key.startswith("active_") and key.endswith("_scorable")
        and key != f"active_{kind}_scorable"
    ]
    assert all(check["step_kind_split"][key] == 0 for key in others)


def test_engine_split_verdict_does_not_read_the_decomposition(tmp_path):
    """``not_regressed`` must keep reading only its original two counts."""
    case = "BFCL/002.json"
    source = DATASET_ROOT / case
    if not source.exists():
        pytest.skip("raw dataset is not present")
    dataset_root = tmp_path / "data"
    (dataset_root / "BFCL").mkdir(parents=True)
    (dataset_root / case).write_text(
        source.read_text(encoding="utf-8-sig"), encoding="utf-8",
    )
    raw = json.loads((dataset_root / case).read_text(encoding="utf-8-sig"))
    gold_step = int(raw["hallucination_step"])
    check = _step_localization_check(
        str(dataset_root),
        [{"case": case, "gold": True, "prediction": True,
          "result": {"responsible_step": gold_step}}],
        [{"case": case, "gold": True, "prediction": True,
          "result": {"responsible_step": gold_step + 1}}],
    )
    # Same counts, same verdict as before the split existed.
    assert check["n"] == 1
    assert check["baseline_hits"] == 1
    assert check["active_hits"] == 0
    assert check["not_regressed"] is False
    # The row still appears in the decomposition; it is simply a miss.
    assert check["active_classified"] == 1


# --------------------------------------------------------------------------
# Task 3: the archived corpus agrees with the fix
# --------------------------------------------------------------------------
def _archive_records():
    records = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    return [(Path(row["source"]), row["result"]) for row in records]


def test_conflict_degradation_leaves_the_localization_gates_untouched():
    """The chosen path must not move any downstream contract.

    Degrading ``localization_authority`` (a new axis) rather than
    ``decision_authority`` is the whole reason no gate changes behaviour: every
    localization consumer -- ``paired_localization_delta``,
    ``localization_behavior_gate``, ``localization_opportunity``,
    ``mechanism_opportunity``, ``_row_health`` -- reads the row's
    ``prediction``/``authority``/``responsible_step``, all of which the
    conflict path leaves exactly as the hearing flow set them. This test pins
    that by running the real localization BEHAVIOR gate over rows that differ
    only by the conflict marker.
    """
    from evolution.gates import localization_behavior_gate

    def row(case, step, conflict):
        return {
            "case": case,
            "gold": True,
            "prediction": True,
            # What the conflict path leaves alone:
            "authority": "authoritative",
            "responsible_step": step,
            "responsible_step_kind": STEP_KIND_ACTION,
            "localization_conflict": conflict,
            "state": "verified_error",
            "result": {
                "case_id": case,
                "state": "verified_error",
                "decision_authority": "authoritative",
                "localization_authority": (
                    LOCALIZATION_AUTHORITY_DEGRADED_CONFLICT if conflict
                    else LOCALIZATION_AUTHORITY_AUTHORITATIVE
                ),
                "localization_conflict": conflict,
                "runtime_valid": True,
                "responsible_step": step,
                "responsible_step_kind": STEP_KIND_ACTION,
                "proof": {"deployment": {"fired": [
                    {"intervention_id": "IV-L-001", "event": "text_injected"}
                ]}},
                "stage_status": {},
            },
        }

    gold = {"A/1.json": 4}
    config = {"gates": {"behavior_require_fired": True,
                        "localization_joint_broken_allowed": 0}}
    candidate = {"intervention_id": "IV-L-001",
                 "claim": {"target_kind": "localization"}}
    clean = [row("A/1.json", 2, None)]
    baseline = [row("A/1.json", 9, None)]
    candidate_clean = [row("A/1.json", 4, None)]
    candidate_conflicted = [row("A/1.json", 4, {"disagreements": [
        LOCALIZATION_CONFLICT_FIRST_HEARING_TRACER]})]

    def verdict(candidate_rows):
        return localization_behavior_gate(
            candidate, baseline_rows=baseline, candidate_rows=candidate_rows,
            config=config, gold_steps=gold,
        )

    clean_verdict = verdict(candidate_clean)
    conflicted_verdict = verdict(candidate_conflicted)
    # The gate reads the same delta either way; the conflict marker changes
    # nothing about whether the claim was met.
    assert clean_verdict["passed"] is True
    assert conflicted_verdict["passed"] is True
    assert clean_verdict["delta"]["joint_improved"] == 1
    assert conflicted_verdict["delta"]["joint_improved"] == 1
    assert clean_verdict["delta"]["joint_broken"] == 0
    assert conflicted_verdict["delta"]["joint_broken"] == 0
    # The decomposition records the conflict marker without letting it decide
    # the scored delta: the same step change is credited either way.
    base_conflicted = paired_localization_delta(
        [row("A/1.json", 9, {"disagreements": [
            LOCALIZATION_CONFLICT_FIRST_HEARING_TRACER]})],
        candidate_conflicted, gold_steps=gold,
    )
    assert base_conflicted["localization_conflict_rows"] == 1
    assert base_conflicted["joint_improved"] == 1
    assert base_conflicted["joint_delta"] == 1
    assert base_conflicted["base_joint"] == 0
    assert base_conflicted["active_joint"] == 1
    # And a clean pair reports zero conflicted rows.
    clean_pair = paired_localization_delta(
        baseline, candidate_clean, gold_steps=gold,
    )
    assert clean_pair["localization_conflict_rows"] == 0
    assert clean_pair["joint_improved"] == 1
    # The conflicted arm is simply out of the per-kind decomposition: it
    # reports no scorable action-step row rather than scoring one under a step
    # the row contradicts.
    assert base_conflicted["active_step_kinds"]["action_step_scorable"] == 0
    assert clean_pair["active_step_kinds"]["action_step_scorable"] == 1
    # Sanity: the uncontested row really is uncontested.
    assert pair_has_no_conflict(clean, baseline)


def pair_has_no_conflict(*arms) -> bool:
    return all(
        not row_localization_conflicting(row) for arm in arms for row in arm
    )


def test_archived_corpus_preserves_original_step_readings():
    """Check the historical audit's denominator without revalidating old stages."""
    records = _archive_records()
    assert len(records) == 142
    conflicts = 0
    kinds = {"content_step": 0, "action_step": 0, "empty_step": 0, None: 0}
    for path, envelope in records:
        raw = json.loads((DATASET_ROOT / str(envelope["case_id"])).read_text(encoding="utf-8-sig"))
        packet = build_packet({"question": raw["question"], "history": raw["history"]})
        assert envelope["state"] == "verified_error", path
        adopted = envelope["responsible_step"]
        mirrored = ((envelope.get("proof") or {}).get("step_tracer") or {}).get("step")
        kinds[step_kind(packet, adopted)] += 1
        if isinstance(mirrored, int) and mirrored != adopted:
            conflicts += 1
    assert conflicts == 3
    assert kinds == {"content_step": 25, "action_step": 117, "empty_step": 0, None: 0}
