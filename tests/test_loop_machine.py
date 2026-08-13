"""State machine and outcome funnel unit tests for sehd_rise.loop.

Verifies the §7 machine: every legal transition in ``LEGAL_TRANSITIONS``
is reachable from ``DETECTED``; illegal edges (cross-layer jumps, an
outcome absent from the table, a transition out of an absorbing terminal)
raise ``IllegalTransition``; and the ``OutcomeFunnel`` keeps an independent
per-outcome and per-layer count whose ``to_dict`` exposes the five §2.9
layers with ``unaddressable`` on its own line and no ``no_edit`` bucket.
The funnel is a pure projection of the terminal step ledger (C8), so the
projection tests pin the fresh/resume equivalence invariant: the same
ledger always yields the same funnel, and post-hoc terminal updates
(shadow commit, probe veto) are reflected on re-projection.

Run: ``python tests/test_loop_machine.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.loop.machine import (
    FITNESS_OUTCOMES,
    IllegalTransition,
    INTERIM_GATE_DECISIONS,
    is_legal_gate_decision,
    is_terminal_gate_decision,
    Layer,
    LEGAL_TRANSITIONS,
    Outcome,
    OutcomeFunnel,
    OUTCOME_LAYER,
    RepairStateMachine,
    State,
)
from sehd_rise.loop.result import EvolutionStep


def _drive_to(machine: RepairStateMachine, target: State) -> None:
    """Walk the happy path from DETECTED to ``target`` with no outcomes."""
    if target is State.DETECTED:
        return
    machine.transition(State.CAUSAL_FAILURE_ANALYZED)
    if target is State.CAUSAL_FAILURE_ANALYZED:
        return
    machine.transition(State.REPAIRABLE)
    if target is State.REPAIRABLE:
        return
    machine.transition(State.REPAIR_PLANNED)
    if target is State.REPAIR_PLANNED:
        return
    machine.transition(State.HYPOTHESIS_SEARCH)
    if target is State.HYPOTHESIS_SEARCH:
        return
    machine.transition(State.PROPOSAL_VALIDATED)
    if target is State.PROPOSAL_VALIDATED:
        return
    machine.transition(State.BOUND)
    if target is State.BOUND:
        return
    machine.transition(State.EXECUTED)
    if target is State.EXECUTED:
        return
    machine.transition(State.TARGET_EDGE_CHANGED)
    if target is State.TARGET_EDGE_CHANGED:
        return
    machine.transition(State.BLOCKER_CLEARED)
    if target is State.BLOCKER_CLEARED:
        return
    machine.transition(State.FITNESS_EVALUATED)
    if target is State.FITNESS_EVALUATED:
        return
    machine.transition(State.EXPERIENCE_LEARNED, outcome=Outcome.ACCEPTED)
    if target is State.EXPERIENCE_LEARNED:
        return
    machine.transition(State.META_UPDATED)


def test_legal_transition_count() -> None:
    assert len(LEGAL_TRANSITIONS) == 44, len(LEGAL_TRANSITIONS)


def test_every_legal_transition_reachable() -> None:
    """Each table edge fires from its source state without error."""
    for spec in LEGAL_TRANSITIONS:
        machine = RepairStateMachine()
        _drive_to(machine, spec.from_state)
        machine.transition(spec.to, outcome=spec.outcome)
        if spec.to is None:
            # Absorbing terminal: outcome recorded, case ended.
            assert machine.done, spec
            assert machine.outcome is spec.outcome, spec
            assert machine.state is None, spec
        else:
            assert machine.state is spec.to, spec
            if spec.outcome is not None:
                # Fitness verdict: outcome recorded, case keeps walking.
                assert machine.outcome is spec.outcome, spec


def test_illegal_cross_layer_jump_rejected() -> None:
    """DETECTED -> REPAIRABLE skips CAUSAL_FAILURE_ANALYZED; illegal."""
    machine = RepairStateMachine()
    try:
        machine.transition(State.REPAIRABLE)
    except IllegalTransition:
        return
    raise AssertionError("cross-layer jump was accepted")


def test_illegal_outcome_rejected() -> None:
    """ACCEPTED is only valid at FITNESS_EVALUATED, not CAUSAL_FAILURE_ANALYZED."""
    machine = RepairStateMachine()
    machine.transition(State.CAUSAL_FAILURE_ANALYZED)
    try:
        machine.transition(None, outcome=Outcome.ACCEPTED)
    except IllegalTransition:
        return
    raise AssertionError("fitness outcome accepted from a framework state")


def test_terminal_absorbs() -> None:
    """A case that absorbed at an outcome terminal cannot transition again."""
    machine = RepairStateMachine()
    machine.transition(State.CAUSAL_FAILURE_ANALYZED)
    machine.transition(None, outcome=Outcome.NON_EVOLVABLE)
    assert machine.done
    assert machine.state is None
    try:
        machine.transition(State.REPAIRABLE)
    except IllegalTransition:
        return
    raise AssertionError("transition accepted out of an absorbing terminal")


def test_fitness_outcomes_continue_to_experience() -> None:
    """A fitness verdict records its outcome and walks to META_UPDATED."""
    for outcome in FITNESS_OUTCOMES:
        machine = RepairStateMachine()
        _drive_to(machine, State.FITNESS_EVALUATED)
        machine.transition(State.EXPERIENCE_LEARNED, outcome=outcome)
        assert machine.state is State.EXPERIENCE_LEARNED
        assert machine.outcome is outcome
        machine.transition(State.META_UPDATED)
        assert machine.done
        assert machine.state is State.META_UPDATED


def test_funnel_per_outcome_counts() -> None:
    """Each outcome has its own counter, independent of every other."""
    funnel = OutcomeFunnel()
    for outcome in (
        Outcome.ACCEPTED, Outcome.ACCEPTED, Outcome.ACCEPTED,
        Outcome.SOURCE_NOT_IMPROVED, Outcome.SOURCE_NOT_IMPROVED,
        Outcome.CANARY_REGRESSION,
        Outcome.CAPABILITY_GAP,
        Outcome.BINDING_NOT_FOUND,
        Outcome.BINDING_AMBIGUOUS,
        Outcome.BINDING_DOMAIN_MISMATCH,
        Outcome.CONSTRUCTION_ANCHOR_MISMATCH,
        Outcome.CONSTRUCTED_CANDIDATE_NOT_QUALIFIED,
        Outcome.NON_EVOLVABLE,
        Outcome.UNADDRESSABLE, Outcome.UNADDRESSABLE,
        Outcome.INFRA_FAILURE,
    ):
        funnel.record(outcome)
    assert funnel.accepted() == 3
    assert funnel.count(Outcome.ACCEPTED) == 3
    assert funnel.count(Outcome.SOURCE_NOT_IMPROVED) == 2
    assert funnel.count(Outcome.CANARY_REGRESSION) == 1
    assert funnel.count(Outcome.CAPABILITY_GAP) == 1
    assert funnel.count(Outcome.BINDING_NOT_FOUND) == 1
    assert funnel.count(Outcome.BINDING_AMBIGUOUS) == 1
    assert funnel.count(Outcome.BINDING_DOMAIN_MISMATCH) == 1
    assert funnel.count(Outcome.CONSTRUCTION_ANCHOR_MISMATCH) == 1
    assert funnel.count(Outcome.CONSTRUCTED_CANDIDATE_NOT_QUALIFIED) == 1
    assert funnel.count(Outcome.NON_EVOLVABLE) == 1
    assert funnel.unaddressable() == 2
    assert funnel.count(Outcome.INFRA_FAILURE) == 1


def test_funnel_layer_totals() -> None:
    funnel = OutcomeFunnel()
    # Framework includes capability and other non-repair-attempt failures.
    # Search: NO_HYPOTHESIS + INVALID_PROPOSAL
    # Grounding: binding/construction stops before any executor invocation.
    # Execution: ACTION_NOT_DISPATCHED + TARGET_EDGE_UNCHANGED
    # Fitness: ACCEPTED + SOURCE_NOT_IMPROVED + CANARY_REGRESSION + TRANSFER_REGRESSION
    for outcome in (
        Outcome.CAPABILITY_GAP, Outcome.BINDING_DOMAIN_MISMATCH,
        Outcome.CONSTRUCTION_NO_CANDIDATE,
        Outcome.CONSTRUCTION_ANCHOR_MISMATCH,
        Outcome.CONSTRUCTED_CANDIDATE_DUPLICATE,
        Outcome.NON_EVOLVABLE,
        Outcome.UNADDRESSABLE, Outcome.INFRA_FAILURE,
        Outcome.NO_HYPOTHESIS, Outcome.INVALID_PROPOSAL,
        Outcome.BINDING_NOT_FOUND, Outcome.BINDING_AMBIGUOUS,
        Outcome.CONSTRUCTED_CANDIDATE_NOT_QUALIFIED,
        Outcome.CONSTRUCTED_CANDIDATE_NOT_VERIFIED,
        Outcome.CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE,
        Outcome.ACTION_NOT_DISPATCHED, Outcome.TARGET_EDGE_UNCHANGED,
        Outcome.ACCEPTED, Outcome.SOURCE_NOT_IMPROVED,
        Outcome.CANARY_REGRESSION, Outcome.TRANSFER_REGRESSION,
    ):
        funnel.record(outcome)
    assert funnel.layer_total(Layer.FRAMEWORK) == 4
    assert funnel.layer_total(Layer.SEARCH) == 2
    assert funnel.layer_total(Layer.GROUNDING) == 9
    assert funnel.layer_total(Layer.EXECUTION) == 2
    assert funnel.layer_total(Layer.FITNESS) == 4


def test_funnel_to_dict_structure() -> None:
    """to_dict exposes five layers, accepted and unaddressable; no no_edit."""
    funnel = OutcomeFunnel()
    funnel.record(Outcome.ACCEPTED)
    funnel.record(Outcome.UNADDRESSABLE)
    funnel.record(Outcome.NON_EVOLVABLE)
    funnel.record(Outcome.BINDING_NOT_FOUND)
    payload = funnel.to_dict()
    assert set(payload) == {"by_layer", "totals", "accepted", "unaddressable"}, payload
    assert "no_edit" not in payload, payload
    assert set(payload["by_layer"]) == {
        Layer.FRAMEWORK.value,
        Layer.SEARCH.value,
        Layer.GROUNDING.value,
        Layer.EXECUTION.value,
        Layer.FITNESS.value,
    }, payload["by_layer"]
    # C2: unaddressable sits on its own counter and inside the framework layer.
    assert payload["accepted"] == 1
    assert payload["unaddressable"] == 1
    assert payload["by_layer"][Layer.FRAMEWORK.value][Outcome.UNADDRESSABLE.value] == 1
    assert payload["by_layer"][Layer.FRAMEWORK.value][Outcome.NON_EVOLVABLE.value] == 1
    assert payload["by_layer"][Layer.GROUNDING.value][Outcome.BINDING_NOT_FOUND.value] == 1
    assert payload["totals"][Layer.FRAMEWORK.value] == 2
    assert payload["totals"][Layer.GROUNDING.value] == 1
    assert payload["totals"][Layer.FITNESS.value] == 1


def test_outcome_layer_partition_is_complete_and_disjoint() -> None:
    """Every outcome belongs to exactly one layer; layers partition the set."""
    members: dict[Layer, set[Outcome]] = {layer: set() for layer in Layer}
    for outcome, layer in OUTCOME_LAYER.items():
        members[layer].add(outcome)
    union = set()
    for layer in Layer:
        assert members[layer], layer
        union |= members[layer]
    assert union == set(Outcome), (len(union), len(set(Outcome)))
    # Disjoint: each outcome counted once.
    total = sum(len(v) for v in members.values())
    assert total == len(Outcome), (total, len(Outcome))
    # Fitness outcomes are exactly the four §2.9 fitness terminals.
    assert members[Layer.FITNESS] == set(FITNESS_OUTCOMES)


def _step(gate_decision: str, *, case_id: str = "case_x") -> EvolutionStep:
    return EvolutionStep(
        case_id=case_id,
        case_index=0,
        trigger_source="test",
        attribution=None,
        edit_proposal=None,
        mutation_applied=None,
        gate_decision=gate_decision,
        genome_hash_before="a",
        genome_hash_after="b",
        detection_state="verified_error",
        timestamp=0.0,
    )


def test_gate_decision_vocabulary_is_closed() -> None:
    """Every Outcome and declared interim marker is legal; garbage is not."""
    for outcome in Outcome:
        assert is_terminal_gate_decision(outcome.value), outcome
        assert is_legal_gate_decision(outcome.value), outcome
    for marker in INTERIM_GATE_DECISIONS:
        assert is_legal_gate_decision(marker), marker
        assert not is_terminal_gate_decision(marker), marker
    for retry_marker in ("retry_structural", "retry_fitness_no_effect"):
        assert is_legal_gate_decision(retry_marker), retry_marker
        assert not is_terminal_gate_decision(retry_marker), retry_marker
    for garbage in ("", "accepted", "ACCEPT", "provisional_no_gold", "x"):
        assert not is_legal_gate_decision(garbage), garbage


def test_funnel_from_steps_projects_terminal_outcomes_only() -> None:
    """The projection counts terminal outcomes and skips interim markers."""
    steps = [
        _step(Outcome.ACCEPTED.value, case_id="c1"),
        _step("provisional", case_id="c2"),
        _step(Outcome.SOURCE_NOT_IMPROVED.value, case_id="c3"),
        _step("retry_structural", case_id="c4"),
        _step("halted", case_id="c5"),
        _step("rollback_resume", case_id="c6"),
        _step(Outcome.CANARY_REGRESSION.value, case_id="c7"),
        _step(Outcome.CAPABILITY_GAP.value, case_id="c8"),
    ]
    funnel = OutcomeFunnel.from_steps(steps)
    assert funnel.accepted() == 1
    assert funnel.count(Outcome.SOURCE_NOT_IMPROVED) == 1
    assert funnel.count(Outcome.CANARY_REGRESSION) == 1
    assert funnel.count(Outcome.CAPABILITY_GAP) == 1
    assert sum(funnel.to_dict()["totals"].values()) == 4


def test_funnel_projection_reflects_post_hoc_terminal_updates() -> None:
    """Shadow commit and probe veto rewrite the step; re-projection follows."""
    steps = [
        _step("provisional", case_id="c1"),
        _step("provisional", case_id="c2"),
        _step("provisional", case_id="c3"),
    ]
    assert OutcomeFunnel.from_steps(steps).accepted() == 0
    # Shadow flush commits c1/c2 and a later probe veto rolls c2 back.
    steps[0].gate_decision = Outcome.ACCEPTED.value
    steps[1].gate_decision = Outcome.ACCEPTED.value
    steps[2].gate_decision = Outcome.SOURCE_NOT_IMPROVED.value
    committed = OutcomeFunnel.from_steps(steps)
    assert committed.accepted() == 2
    assert committed.count(Outcome.SOURCE_NOT_IMPROVED) == 1
    steps[1].gate_decision = Outcome.CANARY_REGRESSION.value
    vetoed = OutcomeFunnel.from_steps(steps)
    assert vetoed.accepted() == 1
    assert vetoed.count(Outcome.CANARY_REGRESSION) == 1


def test_funnel_projection_survives_step_serialization() -> None:
    """Fresh == resume: a checkpoint round-trip preserves the projection."""
    steps = [
        _step(Outcome.ACCEPTED.value, case_id="c1"),
        _step(Outcome.NON_EVOLVABLE.value, case_id="c2"),
        _step("provisional", case_id="c3"),
        _step(Outcome.UNADDRESSABLE.value, case_id="c4"),
    ]
    restored = [EvolutionStep.from_dict(step.to_dict()) for step in steps]
    assert (
        OutcomeFunnel.from_steps(steps).to_dict()
        == OutcomeFunnel.from_steps(restored).to_dict()
    )


def test_runner_l1_happy_path_is_drivable() -> None:
    """The runner's L1 wiring: detection -> dispatch -> shadow-admit fitness.

    Locks the exact sequence the loop drives so a refactor that reorders the
    phases raises here instead of on a live run.
    """
    machine = RepairStateMachine()
    machine.transition(State.CAUSAL_FAILURE_ANALYZED)
    machine.transition(State.REPAIRABLE)
    machine.transition(State.REPAIR_PLANNED)
    machine.transition(State.HYPOTHESIS_SEARCH)
    machine.transition(State.PROPOSAL_VALIDATED)
    machine.transition(State.BOUND)
    machine.transition(State.EXECUTED)
    machine.transition(State.TARGET_EDGE_CHANGED)
    machine.transition(State.BLOCKER_CLEARED)
    machine.transition(State.FITNESS_EVALUATED)
    machine.transition(State.EXPERIENCE_LEARNED, outcome=Outcome.ACCEPTED)
    machine.transition(State.META_UPDATED)
    assert machine.done


def test_runner_detection_crash_terminal_is_drivable() -> None:
    """A detector crash terminal (INFRA_FAILURE) is legal directly from DETECTED."""
    machine = RepairStateMachine()
    machine.transition(None, outcome=Outcome.INFRA_FAILURE)
    assert machine.done
    assert machine.outcome is Outcome.INFRA_FAILURE


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
