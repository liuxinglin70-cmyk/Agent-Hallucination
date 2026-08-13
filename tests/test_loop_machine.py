"""State machine and outcome funnel unit tests for sehd_rise.loop.

Verifies the §7 machine: every legal transition in ``LEGAL_TRANSITIONS``
is reachable from ``DETECTED``; illegal edges (cross-layer jumps, an
outcome absent from the table, a transition out of an absorbing terminal)
raise ``IllegalTransition``; and the ``OutcomeFunnel`` keeps an independent
per-outcome and per-layer count whose ``to_dict`` exposes the four §2.9
layers with ``unaddressable`` on its own line and no ``no_edit`` bucket.

Run: ``python tools/parity/test_loop_machine.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.loop.machine import (
    FITNESS_OUTCOMES,
    IllegalTransition,
    Layer,
    LEGAL_TRANSITIONS,
    Outcome,
    OutcomeFunnel,
    OUTCOME_LAYER,
    RepairStateMachine,
    State,
)


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
    if target in {State.CONSTRUCTOR_EXECUTED, State.CANDIDATE_CONSTRUCTED}:
        machine.transition(State.CONSTRUCTOR_EXECUTED)
        if target is State.CONSTRUCTOR_EXECUTED:
            return
        machine.transition(State.CANDIDATE_CONSTRUCTED)
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
    # Framework includes capability and binding-contract failures.
    # Search: NO_HYPOTHESIS + INVALID_PROPOSAL
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
    assert funnel.layer_total(Layer.FRAMEWORK) == 8
    assert funnel.layer_total(Layer.SEARCH) == 2
    assert funnel.layer_total(Layer.EXECUTION) == 7
    assert funnel.layer_total(Layer.FITNESS) == 4


def test_funnel_to_dict_structure() -> None:
    """to_dict exposes four layers, accepted and unaddressable; no no_edit."""
    funnel = OutcomeFunnel()
    funnel.record(Outcome.ACCEPTED)
    funnel.record(Outcome.UNADDRESSABLE)
    funnel.record(Outcome.NON_EVOLVABLE)
    payload = funnel.to_dict()
    assert set(payload) == {"by_layer", "totals", "accepted", "unaddressable"}, payload
    assert "no_edit" not in payload, payload
    assert set(payload["by_layer"]) == {
        Layer.FRAMEWORK.value,
        Layer.SEARCH.value,
        Layer.EXECUTION.value,
        Layer.FITNESS.value,
    }, payload["by_layer"]
    # C2: unaddressable sits on its own counter and inside the framework layer.
    assert payload["accepted"] == 1
    assert payload["unaddressable"] == 1
    assert payload["by_layer"][Layer.FRAMEWORK.value][Outcome.UNADDRESSABLE.value] == 1
    assert payload["by_layer"][Layer.FRAMEWORK.value][Outcome.NON_EVOLVABLE.value] == 1
    assert payload["totals"][Layer.FRAMEWORK.value] == 2
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


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
