"""The instrument's own jitter, measured and wired into the judgements.

The detector is not deterministic at temperature 0. In the RSI pilot,
``BFCL/008`` and ``BFCL/024`` read 6/2 across 8 repeats of one configuration,
same packet hash, same interventions hash. A framework whose admission can
rest on one case therefore has to know how much it moves with nothing changed,
and has to refuse to bank a gain that only exists on the moving part.

These tests are the offline instrument for that: a mock whose *active*
configuration reads one case two ways, plus the gates and the report that are
supposed to notice. They pin four things:

* the round report publishes the noise floor, and distinguishes a measured
  zero from a round that took no second roll at all;
* a candidate that gains only on a case the instrument cannot read twice is
  *not* admitted, and its rejection is recorded as an unstable reading rather
  than as a falsified mechanism;
* a candidate that gains on a stable case is admitted, with the unstable part
  of its gain visible but not counted toward the floor;
* a round with no provider stage (frozen replay) reports "not applicable"
  instead of inventing a zero.

Everything here is offline: ``MockDetectorBackend`` and a scripted proposer, no
HTTP. The guard in ``conftest.py`` stays armed, and nothing in this module
constructs a live backend, so no seam to a provider exists to be reached.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from threading import Event
from pathlib import Path

import pytest

from evolution.backends import MockDetectorBackend
from evolution.config import load_config
from evolution.checkpoint import CycleJournal
from evolution.contracts import empty_registry
from evolution.engine import (
    EvolutionEngine,
    _ArmRunner,
    gate_rejection_is_unevaluable,
    terminal_classification,
)
from evolution.gates import (
    harmlessness_gate,
    marginal_floor_rests_on_unstable,
    meets_m,
)
from evolution.metrics import (
    NOISE_FLOOR_DETERMINISTIC,
    NOISE_FLOOR_MEASURED,
    NOISE_FLOOR_NOT_MEASURED,
    flipped_cases,
    noise_floor,
    noise_floor_flipped_cases,
    paired_delta,
    repeated_roll_disagreement,
)
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import (
    _make_dataset,
    _scanner_candidate,
    _scripted_proposer,
)


# ---------------------------------------------------------------------------
# The planted scenario
# ---------------------------------------------------------------------------
# A/004.json is a stable recall failure: wrong on every roll of the active
# configuration, and the candidate that fixes it is fixing something real.
STABLE_FIX = "A/004.json"
# A/012.json and A/014.json are the patrol members of the same failure family,
# so the round diagnoses a two-case recall family and the candidate is
# draftable at all.
PATROL_FAILURES = ("A/012.json", "A/014.json")
# A/008.json is the unstable case: the active configuration itself reads it two
# ways. Nothing about the candidate is involved in that difference.
UNSTABLE = "A/008.json"


def _gold_of(case: str) -> bool:
    """The gold ``_make_dataset`` wrote: even index is a hallucination."""
    return int(Path(case).stem) % 2 == 0


def _config(tmp_path: Path) -> dict:
    config = load_config()
    config["dataset_root"] = str(tmp_path / "data")
    config["split_manifest"] = str(tmp_path / "manifest.json")
    config["mini_split_manifest"] = str(tmp_path / "mini_manifest.json")
    config["work_root"] = str(tmp_path / "work")
    config["registry_dir"] = str(tmp_path / "registry")
    config["ledger_path"] = str(tmp_path / "ledger.jsonl")
    config["harness_dir"] = str(tmp_path / "harness")
    config["memory_root"] = str(tmp_path / "memory")
    config["backend"]["name"] = "mock"
    config["backend"]["cache"] = False
    config["budget"]["patrol_cases"] = 8
    # The defect-injection audit is a separate mechanism and it is switched off
    # here deliberately. Its own control comparison pairs the frozen active rows
    # against a defect arm, so a planted instability lands on one side of that
    # pair and makes a known-bad defect "pass" -- for reasons that have nothing
    # to do with the gate logic those tests exist to check. The audit has its
    # own coverage (``test_engine_smoke``, ``test_audit_inconclusive_scope``).
    config["audit"]["enabled"] = False
    return config


def _split(cases: list[str]) -> EvolutionSplit:
    return EvolutionSplit(
        name="synthetic",
        pool=cases,
        sentinel=cases[:4],
        gain=cases[4:12],
        patrol_pool=cases[12:],
        mini=True,
    )


def _scenario(tmp_path: Path) -> tuple:
    """Dataset, config, split and base predictions for the planted scenario.

    The base predictions are what the *active* configuration reads with nothing
    changed: the two patrol members and one gain case are wrong (all three are
    gold errors), everything else reads gold. Two patrol members are needed
    because a failure family is only diagnosed at two same-mechanism cases.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = _split(cases)
    wrong = {*PATROL_FAILURES, STABLE_FIX}
    base = {case: (not _gold_of(case)) for case in wrong}
    return cases, config, split, base


class _RerollUnstableBackend(MockDetectorBackend):
    """A mock whose active configuration reads one case two ways.

    ``run_case`` returns ``unstable_roll_one`` for ``flip_case`` on the frozen
    roll -- ``evaluate`` of the intervention-free registry, what the cycle
    computes first -- and ``unstable_roll_two`` on the fresh roll of that same
    registry, which is the shared control roll.

    Both entry points are overridden so the mapping is declared rather than
    inferred. ``evaluate_fresh`` calls ``evaluate`` internally, so the outer
    call marks the roll and the inner one leaves the mark alone.

    That asymmetry is the point. It is the ordering that manufactures an
    illusory gain: a case the control roll happened to lose looks repaired by
    whatever arm is compared against it next, including a candidate that did
    nothing. A real detector produces the same shape through sampling variance
    at temperature 0; this mock produces it deterministically so the test can
    assert what the framework does about it.

    Only intervention-free arms are rewritten. A candidate's own arm is left
    with whatever the candidate does, which is what makes the override in
    ``overrides`` the thing under test.
    """

    def __init__(
        self,
        *,
        flip_case: str = UNSTABLE,
        unstable_roll_one: bool = True,
        unstable_roll_two: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.flip_case = str(flip_case)
        self.unstable_roll_one = unstable_roll_one
        self.unstable_roll_two = unstable_roll_two
        self._roll: ContextVar[str | None] = ContextVar("synthetic_roll", default=None)
        self.fresh_reads: list[list[str]] = []

    def evaluate(self, cases, **kwargs):  # type: ignore[no-untyped-def]
        token = self._roll.set(self._roll.get() or "frozen")
        try:
            return MockDetectorBackend.evaluate(self, cases, **kwargs)
        finally:
            self._roll.reset(token)

    def evaluate_fresh(self, cases, **kwargs):  # type: ignore[no-untyped-def]
        self.fresh_reads.append(sorted(str(case) for case in cases))
        token = self._roll.set("fresh")
        try:
            return MockDetectorBackend.evaluate_fresh(self, cases, **kwargs)
        finally:
            self._roll.reset(token)

    def run_case(self, record, *, case_id, registry=None, gold=None):  # type: ignore[no-untyped-def]
        entries = (registry or {}).get("interventions") or []
        row = MockDetectorBackend.run_case(
            self, record, case_id=case_id, registry=registry, gold=gold,
        )
        roll = self._roll.get()
        if str(case_id) != self.flip_case or entries or roll is None:
            return row
        prediction = (
            self.unstable_roll_one if roll == "frozen"
            else self.unstable_roll_two
        )
        state = "verified_error" if prediction else "verified_clean"
        row["state"] = state
        row["decision_authority"] = "authoritative"
        row["runtime_valid"] = True
        row["incident_occurred"] = prediction
        row["occurrence_state"] = state
        row["authority_failure_reasons"] = []
        row["proof"]["state"] = state
        return row


def _gain_candidate(cycle: int = 1) -> dict:
    """A judge.system text boundary: the hook that forces a fresh control."""
    candidate = _scanner_candidate(cycle=cycle)
    candidate["hook"] = "judge.system"
    candidate.pop("content_hash", None)
    return candidate


def _engine(
    config, split, base, proposer, overrides,
    *, unstable_roll_one=True, unstable_roll_two=False,
    flip_case=UNSTABLE,
) -> EvolutionEngine:
    return EvolutionEngine(
        config,
        backend=_RerollUnstableBackend(
            flip_case=flip_case,
            unstable_roll_one=unstable_roll_one,
            unstable_roll_two=unstable_roll_two,
            base_predictions=base,
            overrides=overrides,
        ),
        split=split,
        mini=True,
        proposer=proposer,
    )


# ---------------------------------------------------------------------------
# A candidate that gains only on the unstable case is not admitted
# ---------------------------------------------------------------------------
def test_gain_only_on_unstable_case_is_not_admitted(tmp_path):
    """The noise floor decides this round, and the rejection says so.

    The active configuration reads ``A/008.json`` correct on the frozen roll and
    wrong on the fresh control roll. A candidate that overrides exactly that
    case therefore looks like a one-case repair while having repaired nothing
    measurable: the same configuration reads the case the other way on a second
    roll with no candidate involved.
    """
    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {UNSTABLE: True}},
    )
    report = engine.run_cycle(1)

    # The instrument block publishes the jitter, and it is the reading the two
    # raw rolls support.
    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_MEASURED
    assert noise["applicable"] is True
    assert noise["rolls"] == 2
    assert noise["flipped_cases"] == [UNSTABLE]
    assert noise["cases_flipped"] == 1
    # A/008 is a gold error, so the second roll losing it is a lost true
    # positive: the floor moved one case in the harmful direction.
    assert noise["marginal_cases"] == -1
    # The flip table covers every paired case; the one entry that matters
    # is the case that moved.
    assert noise["flips"].get("T->F") == 1
    assert noise["flips"].get("F->T") is None
    assert noise["new_errors"] == 1
    assert noise["unresolved_added"] == 0
    assert report["fresh_control"]["noise_floor"] == noise
    assert report["fresh_control"]["reroll_vs_cached_baseline"][
        "cases_flipped"
    ] == 1
    assert report["fresh_control"]["reused_across_units"] is True
    assert report["instrument"]["unstable_cases"] == [UNSTABLE]

    # The candidate was measured, and the gain it recorded sits on that case.
    unit = report["gates"][0]
    assert unit["m"]["passed"] is False
    assert unit["m"]["delta"]["marginal_cases"] == 1
    assert unit["m"]["delta"]["marginal_cases_on_unstable"] == 1
    assert unit["m"]["delta"]["marginal_cases_on_stable"] == 0
    assert unit["m"]["delta"]["stability_applicable"] is True
    assert unit["m"]["stability_limited"] is True
    assert unit["m"]["evidence_on_unstable_cases"] is True
    assert "unstable" in unit["m"]["reason"]

    # Not admitted, and the rejection is not stored as a falsification.
    assert report["marginal_selection"]["selected"] == []
    assert report["active_after_cycle"] == []
    assert report["instrument"]["terminal_classification"][
        candidate["intervention_id"]
    ] == "not_evaluated"
    assert report["instrument"]["stability_limited_units"] == [
        candidate["intervention_id"]
    ]
    rejection = next(
        item for item in engine.rejection_feedback
        if item.get("intervention_id") == candidate["intervention_id"]
        and item.get("gate") == "M"
    )
    assert rejection["not_evaluable"] is True
    assert "unstable" in rejection["reason"]


def test_unstable_gain_alone_cannot_clear_the_marginal_floor():
    """The same scenario stated as the gate rule rather than as a cycle.

    Kept apart from the cycle test because this is the rule every caller of
    ``meets_m`` gets, including the ones that never run a cycle.
    """
    config = load_config()
    minimum = int(config["gates"]["m_min_marginal_cases"])
    assert minimum >= 1  # the rule below is vacuous if the floor is zero

    delta = paired_delta(
        [
            {"case": UNSTABLE, "gold": True, "prediction": False,
             "state": "verified_clean", "authority": "authoritative"},
            {"case": STABLE_FIX, "gold": True, "prediction": False,
             "state": "verified_clean", "authority": "authoritative"},
        ],
        [
            {"case": UNSTABLE, "gold": True, "prediction": True,
             "state": "verified_error", "authority": "authoritative"},
            {"case": STABLE_FIX, "gold": True, "prediction": False,
             "state": "verified_clean", "authority": "authoritative"},
        ],
        unstable_cases=[UNSTABLE],
    )
    assert delta["marginal_cases"] == 1
    assert delta["marginal_cases_on_unstable"] == 1
    assert delta["marginal_cases_on_stable"] == 0
    passed, failures = meets_m(delta, config=config)
    assert passed is False
    assert any("on_unstable=1" in item for item in failures)
    assert marginal_floor_rests_on_unstable(delta, config=config) is True

    # The same rows with the stable gain present clear the floor, and the
    # unstable part is still on the record.
    mixed = paired_delta(
        [
            {"case": UNSTABLE, "gold": True, "prediction": False,
             "state": "verified_clean", "authority": "authoritative"},
            {"case": STABLE_FIX, "gold": True, "prediction": False,
             "state": "verified_clean", "authority": "authoritative"},
        ],
        [
            {"case": UNSTABLE, "gold": True, "prediction": True,
             "state": "verified_error", "authority": "authoritative"},
            {"case": STABLE_FIX, "gold": True, "prediction": True,
             "state": "verified_error", "authority": "authoritative"},
        ],
        unstable_cases=[UNSTABLE],
    )
    assert mixed["marginal_cases"] == 2
    assert mixed["marginal_cases_on_stable"] == 1
    passed, failures = meets_m(mixed, config=config)
    assert passed is True, failures

    # The identical rows, minus a reroll, keep the old reading -- which is what
    # the change must not touch.
    unannotated = paired_delta(
        [
            {"case": UNSTABLE, "gold": True, "prediction": False,
             "state": "verified_clean", "authority": "authoritative"},
        ],
        [
            {"case": UNSTABLE, "gold": True, "prediction": True,
             "state": "verified_error", "authority": "authoritative"},
        ],
    )
    passed, failures = meets_m(unannotated, config=config)
    assert passed is True, failures
    assert marginal_floor_rests_on_unstable(unannotated, config=config) is False


def test_an_unannotated_delta_is_never_called_unstable():
    """A delta carrying only the counts must not be marked from them.

    The counts are meaningless without the flip set they were derived from, so
    the marker has to require ``stability_applicable``: a caller that never
    took a reroll may not be told its gain was unstable.
    """
    config = load_config()
    delta = {
        "marginal_cases": 1,
        "marginal_cases_on_stable": 0,
        "marginal_cases_on_unstable": 1,
        "new_errors": 0,
        "unresolved_added": 0,
        "health_regressed": 0,
    }
    assert marginal_floor_rests_on_unstable(delta, config=config) is False
    # And with the annotation, the same numbers are exactly the unstable case.
    annotated = {**delta, "stability_applicable": True}
    assert marginal_floor_rests_on_unstable(annotated, config=config) is True


# ---------------------------------------------------------------------------
# A candidate that gains on a stable case is admitted, with the jitter visible
# ---------------------------------------------------------------------------
def test_gain_on_a_stable_case_is_admitted_and_the_jitter_stays_visible(tmp_path):
    """The unstable case is not subtracted -- it is reported and not banked.

    The fresh control roll read ``A/008.json`` *wrong* while the frozen roll
    read it right, so the candidate's arm reads it right and the raw delta shows
    two gains. Only one of them is evidence: the one on the case the instrument
    reads the same way twice. That one clears the floor, the admission stands,
    and the report still shows the other.
    """
    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {STABLE_FIX: True}},
    )
    report = engine.run_cycle(1)

    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_MEASURED
    assert noise["flipped_cases"] == [UNSTABLE]
    assert noise["cases_flipped"] == 1

    unit = report["gates"][0]
    delta = unit["m"]["delta"]
    assert delta["marginal_cases"] == 2
    assert delta["marginal_cases_on_unstable"] == 1
    assert delta["marginal_cases_on_stable"] == 1
    assert unit["m"]["passed"] is True
    assert "stability_limited" not in unit["m"]

    assert report["marginal_selection"]["selected"] == [
        candidate["intervention_id"]
    ]
    assert report["active_after_cycle"] == [candidate["intervention_id"]]
    assert report["instrument"]["terminal_classification"][
        candidate["intervention_id"]
    ] == "admitted"
    # The unstable part of the gain is still on the record, next to the floor it
    # was not allowed to clear.
    assert report["instrument"]["unstable_cases"] == [UNSTABLE]
    assert report["instrument"]["stability_limited_units"] == []


# ---------------------------------------------------------------------------
# A round with nothing to reroll says so instead of publishing a zero
# ---------------------------------------------------------------------------
def test_frozen_replay_round_reports_not_applicable_not_zero(tmp_path):
    """No provider stage means no reroll, and that is a *known* zero.

    A ``scanner.system`` candidate replays frozen stages, so every arm of this
    comparison is deterministic and the floor really is zero. The report has to
    say that, and not the same thing it says about a provider round whose reroll
    was never taken.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = _split(cases)
    candidate = _scanner_candidate(cycle=1)  # scanner.system: frozen replay
    base = {case: (not _gold_of(case)) for case in [*PATROL_FAILURES, STABLE_FIX]}
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions=base,
            overrides={candidate["intervention_id"]: {STABLE_FIX: True}},
        ),
        split=split,
        mini=True,
        proposer=_scripted_proposer(candidate),
    )
    report = engine.run_cycle(1)

    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_DETERMINISTIC
    assert noise["applicable"] is False
    assert noise["rolls"] == 1
    assert noise["marginal_cases"] == 0
    assert noise["cases_flipped"] == 0
    assert noise["flipped_cases"] == []
    assert "frozen replay" in noise["reason"]
    # No fresh control was taken, so the block that would carry one is absent
    # rather than present-and-zero.
    assert report["fresh_control"] is None
    assert report["instrument"]["unstable_cases"] == []
    # And the candidate is judged exactly as it was before this work: no
    # annotation, no marker, M on the raw delta.
    unit = report["gates"][0]
    assert unit["m"]["delta"]["stability_applicable"] is False
    assert unit["m"]["passed"] is True
    assert "stability_limited" not in unit["m"]
    assert report["marginal_selection"]["selected"] == [
        candidate["intervention_id"]
    ]


def test_provider_stage_without_a_reroll_reports_not_measured(tmp_path):
    """A provider round that took one roll has an *unknown* floor, not a zero.

    With ``judge.system`` already active the cycle reuses its own single roll as
    the control and never draws a second one. Publishing ``0`` there would say
    the instrument was checked and found still.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = _split(cases)
    active_entry = _gain_candidate()
    candidate = _scanner_candidate(cycle=2)
    candidate["hook"] = "judge.system"
    candidate["intervention_id"] = "IV-1-002"
    candidate["body"] = {
        "directive": (
            "Cross-check the delivered answer against the cited tool output."
        ),
        "not_when": "Never when no citation is present.",
    }
    candidate.pop("content_hash", None)
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            base_predictions={
                case: (not _gold_of(case))
                for case in [*PATROL_FAILURES, STABLE_FIX]
            },
            overrides={candidate["intervention_id"]: {STABLE_FIX: True}},
        ),
        split=split,
        mini=True,
        proposer=_scripted_proposer(candidate),
    )
    engine.registry.write(
        [active_entry], cycle=0, reason="test: a provider-stage entry is live",
    )
    report = engine.run_cycle(1)

    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_NOT_MEASURED
    assert noise["applicable"] is False
    assert noise["rolls"] == 1
    # None, not 0: the difference between an unlooked-at floor and a known one.
    assert noise["marginal_cases"] is None
    assert noise["cases_flipped"] is None
    assert noise["flips"] is None
    assert noise["flipped_cases"] == []
    assert report["fresh_control"] is None
    # With no flip set, nothing can be marked unstable, and the candidate is
    # judged on the plain rule.
    assert report["instrument"]["unstable_cases"] == []
    assert noise_floor_flipped_cases(noise) == []
    unit = report["gates"][0]
    assert unit["m"]["delta"]["stability_applicable"] is False
    assert "stability_limited" not in unit["m"]


# ---------------------------------------------------------------------------
# The 6/2 split itself, and the two-roll reading of it
# ---------------------------------------------------------------------------
def _roll(case: str, *, state: str, prediction: bool | None) -> dict:
    return {
        "case": case,
        "gold": True,
        "prediction": prediction,
        "state": state,
        "authority": "authoritative" if prediction is not None else "invalid",
        "result": {
            "state": state,
            "decision_authority": (
                "authoritative" if prediction is not None else "invalid"
            ),
            "runtime_valid": prediction is not None,
            "stage_status": {},
        },
    }


def test_eight_repeats_of_one_configuration_split_six_two():
    """The pilot's shape: 8 repeats, one configuration, a 6/2 split.

    ``repeated_roll_disagreement`` is what turns that into a usable reading --
    the cases that may not be scored alone -- instead of leaving it as a
    paragraph in a report. Two rolls can miss a case that only disagrees once in
    eight; this is the estimate that does not.
    """
    stable = "BFCL/002.json"
    split_at_six = True
    rolls = []
    for index in range(8):
        rolls.append([
            _roll(
                "BFCL/008.json",
                state=(
                    "verified_error" if index < 6 else "verified_clean"
                ),
                prediction=index < 6,
            ),
            _roll("BFCL/024.json", state="verified_error", prediction=True),
            _roll(stable, state="verified_error", prediction=True),
        ])
    reading = repeated_roll_disagreement(rolls)
    assert reading["rolls"] == 8
    assert reading["unstable_cases"] == ["BFCL/008.json"]
    assert reading["unstable_case_count"] == 1
    assert reading["stable_case_count"] == 2
    by_case = {item["case"]: item for item in reading["cases"]}
    assert by_case["BFCL/008.json"]["tallies"] == {
        "verified_clean": 2, "verified_error": 6,
    }
    assert by_case["BFCL/008.json"]["reads"] == 8
    assert by_case["BFCL/008.json"]["unstable"] is True
    assert by_case["BFCL/024.json"]["tallies"] == {"verified_error": 8}
    assert by_case["BFCL/024.json"]["unstable"] is False
    assert split_at_six  # the 6/2 split above is what the tally asserts

    # Any two-roll pair containing the disagreement flags the case, which is
    # what the cycle's own two-roll floor consumes.
    assert flipped_cases(rolls[0], rolls[7]) == ["BFCL/008.json"]
    # Two rolls that agree on it do not, and that is the honest limit of a
    # two-roll reading: `repeated_roll_disagreement` is the wider estimate.
    assert flipped_cases(rolls[0], rolls[1]) == []


def test_flip_set_reads_health_and_state_not_only_the_verdict():
    """A case that ends the same way but through a failed stage is unstable.

    Comparing terminal verdicts alone would call it stable. A row that reads
    "provider_error on causal_upgrade" on one roll is not a stable measurement
    of anything, and a candidate scored against it would be scored against a
    different pipeline.
    """
    healthy = _roll("A/001.json", state="verified_error", prediction=True)
    degraded = _roll("A/001.json", state="verified_error", prediction=True)
    degraded["result"] = {**degraded["result"], "stage_status": {
        "causal_upgrade": {"state": "provider_error"},
    }}
    assert flipped_cases([healthy], [degraded]) == ["A/001.json"]
    # The terminal verdict is identical, so a verdict-only comparison would
    # have missed it: that is the whole point of the wider reading.
    assert healthy["prediction"] == degraded["prediction"]
    assert healthy["state"] == degraded["state"]

    # A case present on one roll only is not a pair; it is reported unstable
    # rather than silently dropped.
    assert flipped_cases([healthy], []) == ["A/001.json"]
    assert flipped_cases([], [degraded]) == ["A/001.json"]
    # And two identical rolls are not unstable.
    assert flipped_cases([healthy], [dict(healthy)]) == []


def test_noise_floor_distinguishes_measured_from_unmeasured():
    """The three states of the block, stated directly."""
    measured = noise_floor(
        reroll=paired_delta(
            [{"case": "A", "gold": True, "prediction": True,
              "state": "verified_error", "authority": "authoritative"}],
            [{"case": "A", "gold": True, "prediction": False,
              "state": "verified_clean", "authority": "authoritative"}],
        ),
        flipped=["A"],
    )
    assert measured["status"] == NOISE_FLOOR_MEASURED
    assert measured["cases_flipped"] == 1
    assert measured["new_errors"] == 1
    assert noise_floor_flipped_cases(measured) == ["A"]

    deterministic = noise_floor(provider_stage=False)
    assert deterministic["status"] == NOISE_FLOOR_DETERMINISTIC
    assert deterministic["marginal_cases"] == 0
    assert deterministic["applicable"] is False
    # A known zero is not a flippable set.
    assert noise_floor_flipped_cases(deterministic) == []

    unmeasured = noise_floor(provider_stage=True)
    assert unmeasured["status"] == NOISE_FLOOR_NOT_MEASURED
    assert unmeasured["marginal_cases"] is None
    assert unmeasured["applicable"] is False
    assert noise_floor_flipped_cases(unmeasured) == []

    # And the three are distinguishable from each other, which is the entire
    # reason for having three.
    assert len({
        measured["status"], deterministic["status"], unmeasured["status"],
    }) == 3


# ---------------------------------------------------------------------------
# Harm is never discounted, and an unstable-only reading is not a falsification
# ---------------------------------------------------------------------------
def _harm_row(case: str, *, gold: bool, prediction: bool | None) -> dict:
    state = (
        "unresolved" if prediction is None
        else "verified_error" if prediction else "verified_clean"
    )
    return {
        "case": case,
        "gold": gold,
        "prediction": prediction,
        "state": state,
        "authority": "authoritative" if prediction is not None else "invalid",
        "result": {
            "state": state,
            "decision_authority": (
                "authoritative" if prediction is not None else "invalid"
            ),
            "runtime_valid": prediction is not None,
            "stage_status": {},
        },
    }


def _harmlessness_config() -> dict:
    config = load_config()
    # This comparison is a 2 TP / 1 TN sentinel, so the test opts out of the
    # (higher) default coverage floor explicitly. The floor itself is covered
    # by test_harmlessness_floor_blocks_thin_sentinels.
    config["gates"] = {
        **config["gates"],
        "harmlessness_min_protected_tp": 1,
        "harmlessness_min_protected_tn": 1,
    }
    return config


def test_harmlessness_still_fails_on_an_unstable_case_but_says_where_it_came_from():
    """The conservative direction, spelled out.

    The gate does not subtract the noise: a lost true positive measured against
    the matched control is a violation and the candidate is blocked. What the
    annotation buys is attribution -- the verdict says every recorded harm sits
    on a case the instrument reads two ways, so the engine can record the
    rejection as an unstable reading instead of blacklisting the mechanism.
    """
    config = _harmlessness_config()
    baseline = [
        _harm_row("tp", gold=True, prediction=True),
        _harm_row("tn", gold=False, prediction=False),
        _harm_row("flaky", gold=True, prediction=True),
    ]
    candidate = [
        _harm_row("tp", gold=True, prediction=True),
        _harm_row("tn", gold=False, prediction=False),
        _harm_row("flaky", gold=True, prediction=False),
    ]
    verdict = harmlessness_gate(
        baseline_rows=baseline,
        candidate_rows=candidate,
        config=config,
        unstable_cases=["flaky"],
    )
    assert verdict["passed"] is False
    assert verdict["delta"]["new_fn"] == 1
    assert verdict["harm_on_unstable_cases"] == ["flaky"]
    assert verdict["harm_entirely_on_unstable"] is True
    assert verdict["unstable_cases"] == ["flaky"]

    # A harm that lands somewhere stable is not excused by a neighbour being
    # flaky, and the marker must not fire for it.
    changed = [dict(row) for row in baseline]
    changed[0] = _harm_row("tp", gold=True, prediction=False)
    verdict = harmlessness_gate(
        baseline_rows=baseline,
        candidate_rows=changed,
        config=config,
        unstable_cases=["flaky"],
    )
    assert verdict["passed"] is False
    assert verdict["delta"]["new_fn"] == 1
    assert verdict["harm_on_unstable_cases"] == []
    assert verdict["harm_entirely_on_unstable"] is False

    # Without a reroll there is no unstable set, and the verdict carries none.
    verdict = harmlessness_gate(
        baseline_rows=baseline, candidate_rows=candidate, config=config,
    )
    assert verdict["passed"] is False
    assert verdict["unstable_cases"] == []
    assert verdict["harm_entirely_on_unstable"] is False


def _planted_control(split: EvolutionSplit) -> list[dict]:
    """Control rows for one unit: gold everywhere except one gain case.

    The unit needs a repair opportunity in its gain set or it fail-closes before
    reaching a gate, and the harm has to land on the sentinel, so the two are
    planted separately: ``A/004.json`` is a gold error the control arm gets
    wrong (the opportunity), and the sentinel rows read gold -- so a candidate
    that loses one of them has recorded a real harm.
    """
    rows = []
    for case in [*split.sentinel, *split.gain]:
        gold = _gold_of(case)
        prediction = gold
        if case == STABLE_FIX:
            prediction = not gold
        rows.append(_harm_row(case, gold=gold, prediction=prediction))
    return rows


def test_an_unstable_only_harm_is_a_reading_not_a_falsification(tmp_path):
    """The engine's attribution, at the level where it is decided.

    The candidate loses a protected sentinel case on a round where the
    instrument itself reads that case two ways. The unit is still blocked --
    that is the conservative direction -- but the verdict records an unstable
    reading, so the rejection is not written to memory as a falsified mechanism
    and the proposer is not told to avoid it.

    This is exercised on one unit rather than through a whole cycle because an
    unstable case cannot reach a cycle's sentinel: the round's coverage
    preflight reads the frozen roll, and a case that roll gets wrong is not
    protected. The fresh control is where the two rolls can disagree, and that
    is the row this scenario plants.
    """
    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    flip_sentinel = split.sentinel[2]  # A/002.json: a gold error, a lost TP
    assert _gold_of(flip_sentinel) is True
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {flip_sentinel: False}},
    )
    arms = _ArmRunner(engine, None)
    control = _planted_control(split)
    unit = engine._run_unit(
        candidate,
        partners=[],
        active_entries=engine.registry.entries,
        active_rows=control,
        fresh_control_rows=control,
        unstable_cases=[flip_sentinel],
        arms=arms,
    )

    verdict = unit["harmlessness"]
    assert verdict["passed"] is False
    assert verdict["delta"]["new_fn"] == 1
    assert verdict["harm_on_unstable_cases"] == [flip_sentinel]
    assert verdict["harm_entirely_on_unstable"] is True
    assert verdict["stability_limited"] is True
    assert verdict["evaluable"] is False
    assert verdict["status"] == "not_evaluated"
    assert "unstable reading" in verdict["reason"]

    # The two consumers that decide whether this rejection is remembered as
    # evidence both read it as "not measured", not as "wrong mechanism".
    assert gate_rejection_is_unevaluable(
        gate="harmlessness",
        verdict=verdict,
        report={"candidate": candidate},
        candidate=candidate,
    ) is True
    assert terminal_classification(
        {"candidate": candidate, "harmlessness": verdict}, admitted=False,
    ) == "not_evaluated"


def test_an_unstable_harm_no_longer_blocks_the_mechanism_from_retry(tmp_path):
    """The same verdict without the marker keeps its old, banning meaning.

    The fix has to be visible in the difference, so this asserts both sides:
    with the flip set the rejection is unevaluable, and without it the very
    same rows are a plain falsification.
    """
    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    flip_sentinel = split.sentinel[2]
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {flip_sentinel: False}},
    )
    arms = _ArmRunner(engine, None)
    control = _planted_control(split)

    marked = engine._run_unit(
        candidate, partners=[], active_entries=engine.registry.entries,
        active_rows=control, fresh_control_rows=control,
        unstable_cases=[flip_sentinel], arms=arms,
    )["harmlessness"]
    plain = engine._run_unit(
        candidate, partners=[], active_entries=engine.registry.entries,
        active_rows=control, fresh_control_rows=control,
        arms=arms,
    )["harmlessness"]

    assert marked["passed"] is plain["passed"] is False
    assert marked["delta"]["new_fn"] == plain["delta"]["new_fn"] == 1
    assert marked["unstable_cases"] == [flip_sentinel]
    assert plain["unstable_cases"] == []
    assert marked.get("stability_limited") is True
    assert plain.get("stability_limited") is None
    assert terminal_classification(
        {"candidate": candidate, "harmlessness": marked}, admitted=False,
    ) == "not_evaluated"
    assert terminal_classification(
        {"candidate": candidate, "harmlessness": plain}, admitted=False,
    ) == "rejected"


def test_a_stability_marker_blocks_admission_without_relaxing_a_threshold(tmp_path):
    """The direction of the change: stricter only.

    A candidate the old rule would have admitted -- raw marginal_cases = 1,
    which is exactly the floor -- is blocked, and every gate threshold in the
    shipped configuration is asserted unchanged.
    """
    cases, config, split, base = _scenario(tmp_path)
    assert {
        "m_min_marginal_cases": config["gates"]["m_min_marginal_cases"],
        "m_new_errors_allowed": config["gates"]["m_new_errors_allowed"],
        "harmlessness_new_fn_allowed": config["gates"]["harmlessness_new_fn_allowed"],
        "harmlessness_new_fp_allowed": config["gates"]["harmlessness_new_fp_allowed"],
        "harmlessness_new_unresolved_allowed": (
            config["gates"]["harmlessness_new_unresolved_allowed"]
        ),
        "harmlessness_min_protected_tp": (
            config["gates"]["harmlessness_min_protected_tp"]
        ),
        "harmlessness_min_protected_tn": (
            config["gates"]["harmlessness_min_protected_tn"]
        ),
        "localization_min_joint_cases": (
            config["gates"]["localization_min_joint_cases"]
        ),
        "localization_joint_broken_allowed": (
            config["gates"]["localization_joint_broken_allowed"]
        ),
    } == {
        "m_min_marginal_cases": 1,
        "m_new_errors_allowed": 0,
        "harmlessness_new_fn_allowed": 0,
        "harmlessness_new_fp_allowed": 0,
        "harmlessness_new_unresolved_allowed": 0,
        "harmlessness_min_protected_tp": 2,
        "harmlessness_min_protected_tn": 2,
        "localization_min_joint_cases": 1,
        "localization_joint_broken_allowed": 0,
    }
    candidate = _gain_candidate()
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {UNSTABLE: True}},
    )
    report = engine.run_cycle(1)
    assert report["gates"][0]["m"] is not None, report["gates"][0]
    # The raw delta is one case better, which is exactly what the floor of 1
    # asks for. It was not banked.
    assert report["gates"][0]["m"]["delta"]["marginal_cases"] == 1
    assert report["gates"][0]["m"]["delta"]["marginal_cases_on_stable"] == 0
    assert report["marginal_selection"]["selected"] == []


def test_engine_report_matches_a_standalone_pairing_of_the_two_rolls(tmp_path):
    """The published floor is the paired reading of the two rolls, recomputed.

    A wiring that published a different number than the raw rows support would
    be the same class of defect this whole exercise is about, so the report is
    checked against an independent pairing of the journaled rows.
    """
    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {UNSTABLE: True}},
    )
    report = engine.run_cycle(1)
    noise = report["instrument"]["noise_floor"]

    # Recover every arm the cycle observed from the checkpoint journal.
    arms = sorted((tmp_path / "work" / "checkpoints").rglob("arms/*/*.json"))
    assert arms, "the cycle should have journaled its arm observations"
    rows = [
        json.loads(path.read_text(encoding="utf-8"))["row"] for path in arms
    ]
    active_reads = [
        row for row in rows
        if not (
            ((row.get("result") or {}).get("proof") or {})
            .get("deployment") or {}
        ).get("active_interventions")
    ]
    unstable_reads = [
        row for row in active_reads if str(row.get("case")) == UNSTABLE
    ]
    predictions = {row.get("prediction") for row in unstable_reads}
    assert predictions == {True, False}, (
        "the two rolls of the active configuration must disagree on "
        f"{UNSTABLE}, saw {sorted(str(p) for p in predictions)}"
    )
    recomputed = flipped_cases(
        [row for row in unstable_reads if row.get("prediction") is True],
        [row for row in unstable_reads if row.get("prediction") is False],
    )
    assert recomputed == noise["flipped_cases"] == [UNSTABLE]
    assert noise["cases_flipped"] == len(recomputed)
    # The other case the two rolls were compared on agreed, so it is absent
    # from the flip set even though the candidate moves it.
    assert STABLE_FIX not in noise["flipped_cases"]


def test_a_measured_zero_flags_nothing_and_blocks_nobody(tmp_path):
    """The floor must not over-trigger: two agreeing rolls are a still instrument.

    This is the other half of the fix. A round whose two rolls agree everywhere
    has a *measured* zero, which is a different fact from an unmeasured one --
    and it must not stop a candidate that gains on stable cases from being
    admitted. A wiring that treated "no flips recorded" the same as "no reroll
    taken" would pass the blocking test and fail this one.
    """
    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {STABLE_FIX: True}},
        # Both rolls read the unstable case the same way (and correctly), so
        # nothing is flagged and the floor is a real zero.
        unstable_roll_one=True,
        unstable_roll_two=True,
    )
    report = engine.run_cycle(1)

    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_MEASURED
    assert noise["cases_flipped"] == 0
    assert noise["flipped_cases"] == []
    assert noise["marginal_cases"] == 0
    assert noise["new_errors"] == 0
    assert report["instrument"]["unstable_cases"] == []
    assert report["instrument"]["stability_limited_units"] == []

    # The candidate's gain is on a stable case, so it clears the floor
    # unchanged: an empty flip set is not an excuse to block anything.
    unit = report["gates"][0]
    assert unit["m"]["passed"] is True
    assert unit["m"]["delta"]["marginal_cases"] == 1
    assert unit["m"]["delta"]["marginal_cases_on_stable"] == 1
    assert unit["m"]["delta"]["marginal_cases_on_unstable"] == 0
    assert unit["m"]["delta"]["stability_applicable"] is True
    assert "stability_limited" not in unit["m"]
    assert report["marginal_selection"]["selected"] == [
        candidate["intervention_id"]
    ]
    assert report["instrument"]["terminal_classification"][
        candidate["intervention_id"]
    ] == "admitted"


def test_an_ordinary_zero_gain_rejection_is_not_relabelled_as_unstable(tmp_path):
    """A candidate that moved nothing is rejected for zero gain, full stop.

    The marker means "this round could not score the claim", and a round that
    could score it -- the instrument was rolled twice and held still -- must not
    borrow that excuse for a claim that simply did not earn its floor.
    """
    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={},
        unstable_roll_one=True,
        unstable_roll_two=True,
    )
    report = engine.run_cycle(1)
    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_MEASURED
    assert noise["cases_flipped"] == 0
    unit = report["gates"][0]
    assert unit["harmlessness"]["passed"] is True
    # A candidate that moved nothing fails BEHAVIOR before M is reached, so it
    # never earns a marginal reading at all -- and no part of this rejection may
    # be attributed to the instrument.
    assert unit["behavior"]["passed"] is False
    assert unit["m"] is None
    assert "stability_limited" not in (unit["behavior"] or {})
    assert unit["harmlessness"].get("stability_limited") is None
    assert report["marginal_selection"]["selected"] == []
    assert report["active_after_cycle"] == []


def test_a_stopped_round_does_not_report_a_zero_it_did_not_measure(tmp_path):
    """The preflight stop path has the same obligation as the completed one.

    A round that stops before drafting never draws a second roll, so it cannot
    report a measured zero. When the active registry is on a provider stage the
    floor there is unmeasured, and saying otherwise would let a stopped round
    look still.
    """
    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = _split(cases)
    # An active provider-stage entry: the round is a provider round from its
    # first arm, and it stops at the coverage preflight before drafting.
    engine = EvolutionEngine(
        config,
        backend=MockDetectorBackend(
            # Every sentinel case reads wrong, so baseline-correct coverage is
            # zero and the preflight blocks.
            base_predictions={case: True for case in cases[:4]},
        ),
        split=split,
        mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    engine.registry.write(
        [_gain_candidate()], cycle=0, reason="test: provider stage is live",
    )
    report = engine.run_cycle(1)
    assert report["state"] == "blocked_evaluation_preflight"
    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_NOT_MEASURED
    assert noise["marginal_cases"] is None
    assert noise["cases_flipped"] is None
    assert report["instrument"]["unstable_cases"] == []

    # The same stop with nothing but frozen stages is a known zero -- the
    # distinction is the point, so both sides are asserted.
    # A fresh work root, because a cycle may not be re-run in place.
    frozen_config = _config(tmp_path / "frozen")
    frozen_config["dataset_root"] = config["dataset_root"]
    frozen = EvolutionEngine(
        frozen_config, backend=MockDetectorBackend(
            base_predictions={case: True for case in cases[:4]},
        ),
        split=split, mini=True,
        proposer=_scripted_proposer(_scanner_candidate()),
    )
    report = frozen.run_cycle(1)
    assert report["state"] == "blocked_evaluation_preflight"
    noise = report["instrument"]["noise_floor"]
    assert noise["status"] == NOISE_FLOOR_DETERMINISTIC
    assert noise["marginal_cases"] == 0
    assert noise["applicable"] is False


@pytest.mark.parametrize("provider_stage", [False, True])
def test_noise_floor_block_is_json_serializable(provider_stage):
    """The block is written into the cycle report on disk.

    A value that only survives in memory is not a reading; the report is what
    later analysis reads.
    """
    block = noise_floor(provider_stage=provider_stage)
    assert json.loads(json.dumps(block)) == block


# ---------------------------------------------------------------------------
# The offline measurement tool
# ---------------------------------------------------------------------------
def _write_roll(root: Path, index: int, reads: dict[str, str]) -> Path:
    """One run tree, shaped exactly like the detector's own run writer."""
    tree = root / f"rep{index:02d}"
    for case, state in reads.items():
        case_dir = tree / "cases" / case.replace("/", "__")
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "result.json").write_text(json.dumps({
            "case_id": case,
            "state": state,
            "decision_authority": "authoritative",
            "responsible_step": 3,
        }), encoding="utf-8")
    return tree


def test_the_offline_tool_measures_the_six_two_split(tmp_path):
    """`scripts/measure_noise_floor.py rolls` on the pilot's shape.

    The eight repeats are written as eight run trees -- the layout the detector's
    own writer produces -- so this exercises the tool the way a real calibration
    would: read files, tally, report. It is the offline, re-runnable form of the
    measurement the cycle can only make with two rolls.
    """
    from scripts import measure_noise_floor as tool

    cases = {"BFCL/002.json": "verified_error", "BFCL/024.json": "verified_error"}
    trees = []
    for index in range(8):
        reads = dict(cases)
        reads["BFCL/008.json"] = (
            "verified_error" if index < 6 else "verified_clean"
        )
        trees.append(_write_roll(tmp_path, index, reads))

    payload = tool.report_rolls(trees)
    assert payload["roll_count"] == 8
    assert payload["cases_per_roll"] == [3] * 8
    assert payload["total_cases"] == 3
    assert payload["disagreeing_cases"] == ["BFCL/008.json"]
    assert payload["one_sided_cases"] == []
    assert payload["case_set_mismatch"] is False
    by_case = {item["case"]: item for item in payload["cases"]}
    assert by_case["BFCL/008.json"]["tallies"] == {
        "verified_clean": 2, "verified_error": 6,
    }

    # The rendering says the same thing, and says what the reader must do with
    # it: a disagreeing case may not be scored alone.
    text = tool.render(payload)
    assert "6" in text and "2" in text
    assert "BFCL/008.json" in text
    assert "may not be scored alone" in text
    assert "BFCL/024.json" not in text.split("one-sided")[0].split(
        "disagreeing"
    )[0]

    # The payload is what a caller gates on, so it has to survive a round trip.
    assert json.loads(json.dumps(payload)) == payload
    assert tool.main(["rolls", *[str(tree) for tree in trees], "--json"]) == 0


def test_the_offline_tool_separates_a_coverage_gap_from_jitter(tmp_path):
    """Rolls over different case sets are not pooled into one jitter number.

    A case no roll but one ran is a coverage difference in how the runs were
    built. Counting it as jitter would inflate the noise floor with cases the
    detector was never asked about twice.
    """
    from scripts import measure_noise_floor as tool

    first = _write_roll(tmp_path, 1, {
        "A/001.json": "verified_error", "A/002.json": "verified_clean",
    })
    second = _write_roll(tmp_path, 2, {"A/001.json": "verified_error"})
    payload = tool.report_rolls([first, second])
    assert payload["case_set_mismatch"] is True
    assert payload["one_sided_cases"] == ["A/002.json"]
    assert payload["disagreeing_cases"] == []
    assert payload["read_by_every_roll"] == ["A/001.json"]
    text = tool.render(payload)
    assert "coverage difference, not measured jitter" in text
    # And the tool does not claim a case is scoreable when it was read once.
    assert "the floor reads zero for this case set" in text


def test_the_offline_tool_reports_the_cycle_floor_and_the_unstable_units(tmp_path):
    """`cycle` mode reads what the round already published.

    This is the calibration a real run needs: after a cycle, the report says
    how much the instrument moved and which candidates were limited by it,
    without re-running anything.
    """
    from scripts import measure_noise_floor as tool

    cases, config, split, base = _scenario(tmp_path)
    candidate = _gain_candidate()
    engine = _engine(
        config, split, base,
        proposer=_scripted_proposer(candidate),
        overrides={candidate["intervention_id"]: {UNSTABLE: True}},
    )
    report = engine.run_cycle(1)
    path = tmp_path / "cycle001.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    payload = tool.report_cycle(path)
    assert payload["noise_floor"]["status"] == NOISE_FLOOR_MEASURED
    assert payload["noise_floor"]["flipped_cases"] == [UNSTABLE]
    assert payload["unstable_cases"] == [UNSTABLE]
    assert payload["stability_limited_units"] == [
        candidate["intervention_id"]
    ]
    text = tool.render(payload)
    assert "noise_floor: measured" in text
    assert f"unstable case: {UNSTABLE}" in text
    assert "unit limited by instability" in text

    # A report from before the field existed is reported as such, not as a
    # zero -- the distinction this whole change rests on.
    old = tmp_path / "cycle000.json"
    old.write_text(json.dumps({"cycle": 0, "state": "completed"}), encoding="utf-8")
    older = tool.report_cycle(old)
    assert older["noise_floor"] is None
    assert "predates the noise-floor field" in older["note"]
    assert "noise_floor: absent" in tool.render(older)


def test_the_offline_tool_exits_nonzero_on_a_missing_path(tmp_path):
    """A missing run tree is an error, not a zero reading."""
    from scripts import measure_noise_floor as tool

    assert tool.main(["rolls", str(tmp_path / "nope")]) == 2
    assert tool.main(["cycle", str(tmp_path / "nope.json")]) == 2



def test_journal_reroll_survives_another_case_finishing(tmp_path, monkeypatch):
    dataset = tmp_path / "data"
    _make_dataset(dataset, n=20)
    backend = _RerollUnstableBackend()
    journal = CycleJournal(tmp_path / "checkpoint", {"synthetic": True}, resume=False)
    cases = [STABLE_FIX, UNSTABLE]
    first_entered, second_entered, first_returned = Event(), Event(), Event()
    original_run = backend.run_case
    def run(record, *, case_id, **kwargs):
        if case_id == STABLE_FIX:
            first_entered.set()
            assert second_entered.wait(5)
        else:
            second_entered.set()
            assert first_returned.wait(5)
        return original_run(record, case_id=case_id, **kwargs)
    monkeypatch.setattr(backend, "run_case", run)
    def evaluate(cases, **kwargs):
        if cases == [UNSTABLE]:
            assert first_entered.wait(5)
        rows = backend.evaluate_fresh(cases, **kwargs)
        if cases == [STABLE_FIX]:
            first_returned.set()
        return rows
    registry = empty_registry()
    rows = journal.evaluate(cases, registry=registry, fresh=True, evaluate=evaluate,
                            kwargs={"dataset_root": dataset, "registry": registry,
                                    "gold_map": {case: _gold_of(case) for case in cases}, "workers": 2})
    assert {row["case"]: row["prediction"] for row in rows}[UNSTABLE] is False


def test_reroll_context_reaches_inner_parallel_backend_workers(tmp_path):
    dataset = tmp_path / "data"
    cases = _make_dataset(dataset, n=20)
    backend = _RerollUnstableBackend()
    kwargs = {"dataset_root": dataset, "registry": empty_registry(), "workers": 8}
    frozen = backend.evaluate(cases, **kwargs)
    fresh = backend.evaluate_fresh(cases, **kwargs)
    assert next(row for row in frozen if row["case"] == UNSTABLE)["prediction"] is True
    assert next(row for row in fresh if row["case"] == UNSTABLE)["prediction"] is False
