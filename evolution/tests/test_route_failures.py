"""Route falsification memory: what fired, changed behaviour, and repaired nothing.

The distinction this module exists for is the one the pilot learned the hard
way: a route can be generatable and triggerable without being repair-capable.
Attempt 5 produced a tracer directive that fired, changed the reasoning, and
left the responsible step wrong -- and nothing recorded it, so the next round
proposed the same route again.

These tests lock both halves: that a genuine insufficiency is recognised and
remembered, and that everything which merely looks like one (an outage, an
unmeasured claim, a harmful candidate, a hook that never fired) is kept out.
"""

from __future__ import annotations

import pytest

from evolution.metrics import target_outcome

from evolution.route_failures import (
    REPEAT_OBSERVATION_THRESHOLD,
    ROUTE_FAILURE_SCHEMA,
    TREATMENT_ROUTES,
    falsified_routes,
    insufficiency_evidence,
    load_route_failures,
    record_route_failures,
    resolve_route_failure,
    route_failure_state,
    route_failure_summary,
    route_of,
    unit_route,
)


def candidate(**overrides) -> dict:
    base = {
        "intervention_id": "IV-1-T",
        "type": "text_boundary",
        "hook": "tracer.system",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {"directive": "name the step where the content deviated",
                 "not_when": "never when the step carries text"},
        "claim": {"expected_behavior": "responsible step changes",
                  "target_polarity": "recall"},
        "provenance": {"cycle": 1, "failure_family": "FAM-loc-x",
                       "failure_cases": ["A/001.json", "B/002.json"]},
    }
    base.update(overrides)
    return base


def fired_but_unchanged() -> tuple[dict, dict, dict]:
    """The Attempt-5 shape: fired, changed behaviour, outcome untouched."""
    behavior = {
        "passed": False,
        "reason": "target polarity mismatch: recall claim but no TP gain",
        "fired_rate": 0.9,
        "behavior_events": 7,
        "behavior_event_kind": "text_injected",
    }
    delta = {
        "flips": {"F->F": 4, "T->T": 2},
        "paired_n": 6,
        "marginal_cases": 0,
        "correctness_improved": 0,
        "tp_lost": 0,
        "fp_added": 0,
        "new_errors": 0,
        "unresolved_added": 0,
        "health_regressed": 0,
    }
    return candidate(), behavior, delta


def _outcome(entry, delta, localization=None):
    claim = entry['claim']
    return target_outcome(
        target_kind=claim.get('target_kind', 'detection'),
        polarity=claim['target_polarity'], delta_basis='benefit_set',
        detection_delta=delta, localization_delta=localization,
    )


# --------------------------------------------------------------------------
# Route derivation
# --------------------------------------------------------------------------
def test_route_of_reads_the_candidate_shape_not_the_family_route_list():
    """The family's route list says what was available; the candidate says what was used."""
    assert route_of(candidate()) == "tracer_directive"
    assert route_of(candidate(hook="scanner.system")) == "recall_directive"
    assert route_of(candidate(type="recall_trigger", hook="recall.trigger")) == (
        "recall_trigger"
    )
    assert route_of(candidate(
        type="decision_guard", hook="adjudicate.guard",
        body={"action": "convict_mechanical_violation"},
    )) == "mechanical_conviction"
    assert route_of(candidate(
        type="decision_guard", hook="adjudicate.guard",
        body={"action": "withhold_conviction"},
    )) == "withhold_guard"
    # An evidence check is the observation half of a conviction pair; it is the
    # program it carries that decides which capability was used.
    assert route_of(candidate(
        type="evidence_check", hook="judge.post",
        body={"predicate": "evo_x", "runner": "typed_dsl"},
    )) == "evolved_predicate"
    assert route_of(candidate(
        type="evidence_check", hook="judge.post",
        body={"predicate": "verbatim_fidelity", "runner": "deterministic"},
    )) == "mechanical_conviction"
    # Unroutable shapes report None rather than a guess.
    assert route_of(candidate(type="recall_trigger", hook="upgrade.pre")) is None
    assert route_of(candidate(type="evidence_check", hook="upgrade.pre")) is None
    assert route_of(candidate(type="no_such_type")) is None
    # A text boundary is prose wherever it is injected; the hook names the
    # stage, and every system hook has a route.
    assert route_of(candidate(hook="upgrade.pre")) == "upgrade_directive"
    assert route_of(candidate(hook="judge.system")) == "judge_directive"


def test_unit_route_requires_the_members_to_agree():
    def evidence(runner: str) -> dict:
        return candidate(
            type="evidence_check", hook="judge.post",
            body={"predicate": "evo_x" if runner == "typed_dsl"
                  else "verbatim_fidelity", "runner": runner},
        )

    def guard() -> dict:
        return candidate(
            type="decision_guard", hook="adjudicate.guard",
            body={"action": "convict_mechanical_violation"},
        )

    # A newly created observation riding the conviction guard: capability
    # creation, which is the distinction the two routes exist to draw.
    assert unit_route([evidence("typed_dsl"), guard()]) == "evolved_predicate"
    # A built-in predicate on the same guard is the closed mechanical route.
    assert unit_route([evidence("deterministic"), guard()]) == (
        "mechanical_conviction"
    )
    # Two genuinely different acting routes cannot be attributed to one.
    assert unit_route([candidate(), candidate(hook="scanner.system")]) is None
    assert unit_route([]) is None


def test_every_route_is_a_declared_treatment_route():
    shapes = [
        candidate(),
        candidate(hook="scanner.system"),
        candidate(type="recall_trigger", hook="recall.trigger"),
        candidate(type="decision_guard", hook="adjudicate.guard",
                  body={"action": "convict_mechanical_violation"}),
        candidate(type="decision_guard", hook="adjudicate.guard",
                  body={"action": "withhold_conviction"}),
        candidate(type="decision_guard", hook="adjudicate.guard",
                  body={"action": "restore_judge_conviction"}),
        candidate(type="evidence_check", hook="judge.post",
                  body={"predicate": "evo_x", "runner": "typed_dsl"}),
    ]
    for shape in shapes:
        route = route_of(shape)
        assert route in TREATMENT_ROUTES, route


# --------------------------------------------------------------------------
# The insufficiency predicate
# --------------------------------------------------------------------------
def test_fired_but_unchanged_is_an_insufficiency_observation():
    """The Attempt-5 shape is recorded, with machine-readable evidence."""
    entry, behavior, delta = fired_but_unchanged()
    observation = insufficiency_evidence(
        candidate=entry,
        behavior=behavior,
        outcome=_outcome(entry, delta),
        opportunity=6,
        substantive_rejection=True,
    )
    assert observation is not None
    assert observation["route"] == "tracer_directive"
    assert observation["status"] == "insufficient_for_mechanism"
    assert observation["evidence"]["output_changed"] == 7
    assert observation["evidence"]["target_outcome_changed"] == 0
    assert observation["evidence"]["hook_fired"] == 0.9


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda b, d: (b, {**d, "marginal_cases": 1, "correctness_improved": 1}),
         "target improved"),
        (lambda b, d: (b, {**d, "tp_lost": 1}), "candidate caused harm"),
        (lambda b, d: (b, {**d, "health_regressed": 1}), "runtime regressed"),
        (lambda b, d: ({**b, "fired_rate": 0.0}, d), "hook never fired"),
        (lambda b, d: ({**b, "behavior_events": 0}, d), "no behaviour change"),
        (lambda b, d: ({**b, "passed": True}, d), "gate passed"),
    ],
)
def test_insufficiency_is_not_recorded_for_anything_else(mutate, reason):
    entry, behavior, delta = fired_but_unchanged()
    behavior, delta = mutate(behavior, delta)
    assert insufficiency_evidence(
        candidate=entry,
        behavior=behavior,
        outcome=_outcome(entry, delta),
        opportunity=6,
        substantive_rejection=True,
    ) is None, reason


def test_insufficiency_requires_a_measurable_opportunity_and_a_substantive_rejection():
    entry, behavior, delta = fired_but_unchanged()
    # No repairable case in the measured set: the route was never given a
    # chance to repair anything, so its failure says nothing about the route.
    assert insufficiency_evidence(
        candidate=entry, behavior=behavior, outcome=_outcome(entry, delta),
        opportunity=0, substantive_rejection=True,
    ) is None
    # An unevaluable rejection is not a substantive one.
    assert insufficiency_evidence(
        candidate=entry, behavior=behavior, outcome=_outcome(entry, delta),
        opportunity=6, substantive_rejection=False,
    ) is None
    # A transient outage leaves behaviour unmeasurable.
    assert insufficiency_evidence(
        candidate=entry, behavior={**behavior, "fired_rate": 0.0},
        outcome=_outcome(entry, delta), opportunity=6, substantive_rejection=True,
    ) is None


def test_precision_and_boundary_polarities_are_read_correctly():
    entry, behavior, delta = fired_but_unchanged()
    precision = insufficiency_evidence(
        candidate={**entry, "claim": {"target_polarity": "precision"}},
        behavior=behavior, outcome=_outcome({**entry, "claim": {"target_polarity": "precision"}}, delta), opportunity=4,
        substantive_rejection=True,
    )
    assert precision is not None
    # A precision claim that removed a false positive did repair something.
    assert insufficiency_evidence(
        candidate={**entry, "claim": {"target_polarity": "precision"}},
        behavior=behavior,
        outcome=_outcome({**entry, "claim": {"target_polarity": "precision"}}, {**delta, "flips": {"T->F": 1}, "marginal_cases": 1,
                    "correctness_improved": 1}),
        opportunity=4, substantive_rejection=True,
    ) is None
    # An unknown polarity is not scored rather than guessed.
    assert insufficiency_evidence(
        candidate={**entry, "claim": {"target_polarity": "unknown"}},
        behavior=behavior, outcome=_outcome({**entry, "claim": {"target_polarity": "unknown"}}, delta), opportunity=4,
        substantive_rejection=True,
    ) is None


@pytest.mark.parametrize("kind", ["localization", "joint"])
@pytest.mark.parametrize("readings", [{"joint_improved": 0, "joint_broken": 1},
                                    {"joint_improved": 1, "joint_broken": 0}, {}])
def test_localization_gain_harm_or_missing_readings_are_not_route_insufficiency(kind, readings):
    entry, behavior, delta = fired_but_unchanged()
    entry["claim"]["target_kind"] = kind
    localization = ({
        "n": 6, "localizable_n": 6,
        "joint_delta": readings.get("joint_improved", 0) - readings.get("joint_broken", 0),
        **readings,
    } if readings else None)
    assert insufficiency_evidence(
        candidate=entry, behavior=behavior, outcome=_outcome(entry, delta, localization),
        opportunity=6, substantive_rejection=True,
    ) is None


def test_empty_delta_is_unmeasured_instead_of_unchanged():
    entry, behavior, _ = fired_but_unchanged()
    assert insufficiency_evidence(
        candidate=entry, behavior=behavior, outcome=_outcome(entry, {}), opportunity=6,
        substantive_rejection=True,
    ) is None


@pytest.mark.parametrize("kind", ["detection", "localization", "joint", "resolution"])
def test_each_objective_records_only_measured_unchanged_outcomes(kind):
    entry, behavior, delta = fired_but_unchanged()
    entry["claim"]["target_kind"] = kind
    localization = {"n": 6, "localizable_n": 6, "joint_improved": 0,
                    "joint_broken": 0, "joint_delta": 0}
    outcome = _outcome(entry, delta, localization)
    assert outcome["status"] == "unchanged"
    observation = insufficiency_evidence(
        candidate=entry, behavior=behavior, outcome=outcome, opportunity=6,
        substantive_rejection=True,
    )
    assert observation["evidence"]["target_kind"] == kind
    assert observation["evidence"]["delta_basis"] == "benefit_set"


@pytest.mark.parametrize("flips,harm,status", [({"U->T": 1}, 0, "improved"),
                                             ({"U->F": 1}, 0, "improved"),
                                             ({"U->T": 1}, 1, "regressed"),
                                             ({}, 0, "unchanged")])
def test_resolution_outcome_preserves_resolution_gain_and_harm(flips, harm, status):
    entry, _, delta = fired_but_unchanged()
    entry["claim"]["target_kind"] = "resolution"
    outcome = _outcome(entry, {**delta, "flips": flips, "new_errors": harm})
    assert outcome["status"] == status


@pytest.mark.parametrize("fault", ["missing", "boolean_count", "negative_count", "different_set"])
def test_invalid_objective_readings_are_unmeasured(fault):
    entry, _, delta = fired_but_unchanged()
    entry["claim"]["target_kind"] = "localization"
    localization = {"n": 6, "localizable_n": 6, "joint_improved": 0,
                    "joint_broken": 0, "joint_delta": 0}
    if fault == "missing":
        delta.pop("health_regressed")
    elif fault == "boolean_count":
        delta["new_errors"] = False
    elif fault == "negative_count":
        localization["joint_broken"] = -1
    else:
        localization["n"] = 5
    assert _outcome(entry, delta, localization)["status"] == "unmeasured"


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------
def _observation(mechanism="M-aaa", route="tracer_directive") -> dict:
    return {
        "mechanism_id": mechanism,
        "family_id": "FAM-loc-x",
        "observation": {
            "route": route,
            "status": "insufficient_for_mechanism",
            "evidence": {"hook_fired": 0.9, "output_changed": 7,
                         "target_outcome_changed": 0},
        },
    }


def test_store_round_trip_and_schema(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    appended = record_route_failures(
        path, cycle=1, observations=[_observation()],
    )
    assert len(appended) == 1
    records = load_route_failures(path)
    assert len(records) == 1
    assert records[0]["schema_version"] == ROUTE_FAILURE_SCHEMA
    assert records[0]["route"] == "tracer_directive"
    assert records[0]["cycle"] == 1


def test_a_resumed_cycle_does_not_duplicate_its_own_observations(tmp_path):
    """A replay must not count as a second independent observation.

    If it did, one cycle would satisfy the two-observation threshold on its own
    and falsify a route on a single round's evidence.
    """
    path = tmp_path / "route_failures.jsonl"
    record_route_failures(path, cycle=1, observations=[_observation()])
    again = record_route_failures(path, cycle=1, observations=[_observation()])
    assert again == []
    assert len(load_route_failures(path)) == 1


def test_one_observation_is_not_yet_a_falsification(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    record_route_failures(path, cycle=1, observations=[_observation()])
    state = route_failure_state(load_route_failures(path))
    entry = state[("M-aaa", "tracer_directive")]
    assert entry["observations"] == 1
    assert entry["insufficient"] is False
    assert falsified_routes(load_route_failures(path), "M-aaa") == {}


def test_repeated_observations_falsify_the_route(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    for cycle in (1, 2):
        record_route_failures(path, cycle=cycle, observations=[_observation()])
    records = load_route_failures(path)
    state = route_failure_state(records)
    entry = state[("M-aaa", "tracer_directive")]
    assert entry["observations"] == REPEAT_OBSERVATION_THRESHOLD
    assert entry["insufficient"] is True
    assert entry["basis"] == "repeat_observation"
    assert "tracer_directive" in falsified_routes(records, "M-aaa")


def test_revision_evidence_falsifies_on_its_own(tmp_path):
    """The plan's condition: the route was revised and still repaired nothing."""
    path = tmp_path / "route_failures.jsonl"
    record_route_failures(path, cycle=1, observations=[
        {**_observation(), "revisions_observed": 1},
    ])
    entry = route_failure_state(load_route_failures(path))[
        ("M-aaa", "tracer_directive")
    ]
    assert entry["observations"] == 1
    assert entry["insufficient"] is True
    assert entry["basis"] == "revision"


def test_falsification_is_scoped_to_the_mechanism(tmp_path):
    """A route that fails on one mechanism says nothing about another."""
    path = tmp_path / "route_failures.jsonl"
    for cycle in (1, 2):
        record_route_failures(path, cycle=cycle, observations=[
            _observation(mechanism="M-aaa"),
        ])
    records = load_route_failures(path)
    assert set(falsified_routes(records, "M-aaa")) == {"tracer_directive"}
    assert falsified_routes(records, "M-bbb") == {}


def test_resolution_is_history_not_deletion(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    for cycle in (1, 2):
        record_route_failures(path, cycle=cycle, observations=[_observation()])
    resolve_route_failure(
        path, mechanism_id="M-aaa", route="tracer_directive",
        cycle=3, note="tracer directive now moves the step on this mechanism",
    )
    records = load_route_failures(path)
    assert len(records) == 3
    assert any(record.get("kind") == "resolution" for record in records)
    assert falsified_routes(records, "M-aaa") == {}
    assert route_failure_summary(records)["pairs"] == 0


@pytest.mark.parametrize("new_cycles,insufficient", [([], False), ([4], False), ([4, 5], True)])
def test_resolution_closes_the_observation_epoch(tmp_path, new_cycles, insufficient):
    path = tmp_path / "route_failures.jsonl"
    for cycle in (1, 2):
        record_route_failures(path, cycle=cycle, observations=[_observation()])
    original = path.read_bytes()
    resolve_route_failure(path, mechanism_id="M-aaa", route="tracer_directive",
                          cycle=3, note="repaired")
    for cycle in new_cycles:
        record_route_failures(path, cycle=cycle, observations=[_observation()])
    records = load_route_failures(path)
    assert path.read_bytes().startswith(original)
    state = route_failure_state(records)
    entry = state.get(("M-aaa", "tracer_directive"))
    if not new_cycles:
        assert entry is None
    else:
        assert entry["observations"] == len(new_cycles)
        assert entry["cycles"] == new_cycles
        assert entry["insufficient"] is insufficient


def test_resolution_is_scoped_and_duplicate_observations_do_not_count_twice(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    record_route_failures(path, cycle=1, observations=[_observation(), _observation(mechanism="M-other")])
    records = load_route_failures(path)
    assert route_failure_state([*records, *records])[("M-aaa", "tracer_directive")]["insufficient"] is False
    resolve_route_failure(path, mechanism_id="M-aaa", route="tracer_directive", cycle=2, note="repaired")
    state = route_failure_state(load_route_failures(path))
    assert ("M-aaa", "tracer_directive") not in state
    assert state[("M-other", "tracer_directive")]["observations"] == 1


def test_resuming_resolution_is_idempotent_and_conflicting_content_is_rejected(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    kwargs = dict(mechanism_id="M-aaa", route="tracer_directive", cycle=2, note="repaired")
    first = resolve_route_failure(path, **kwargs)
    original = path.read_bytes()
    assert resolve_route_failure(path, **kwargs) == first
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="conflicting resolution"):
        resolve_route_failure(path, **{**kwargs, "note": "different"})
    assert path.read_bytes() == original


def test_unknown_schema_is_rejected(tmp_path):
    path = tmp_path / "route_failures.jsonl"
    path.write_text('{"schema_version": "something.else.v1"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported schema"):
        load_route_failures(path)
