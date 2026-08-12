"""SEHD-RISE loop state machine and outcome funnel.

Guideline §7 drives each processed case through the repair sequence
DETECTED -> CAUSAL_FAILURE_ANALYZED -> REPAIRABLE -> REPAIR_PLANNED ->
HYPOTHESIS_SEARCH -> PROPOSAL_VALIDATED -> EXECUTED -> TARGET_EDGE_CHANGED
-> BLOCKER_CLEARED -> FITNESS_EVALUATED -> EXPERIENCE_LEARNED ->
META_UPDATED, or exits early at a layer terminal. Guideline §2.9 replaces
the flat skip vocabulary with a four-layer outcome funnel: every terminal
carries one outcome naming the layer that stopped the case. Outcomes are a
closed vocabulary; the runner feeds outcomes in, the machine validates the
transition and keeps per-outcome counts for the result JSON.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator


class State(str, Enum):
    """Process states from guideline §7 (terminal exits live in Outcome)."""

    DETECTED = "DETECTED"
    CAUSAL_FAILURE_ANALYZED = "CAUSAL_FAILURE_ANALYZED"
    REPAIRABLE = "REPAIRABLE"
    REPAIR_PLANNED = "REPAIR_PLANNED"
    HYPOTHESIS_SEARCH = "HYPOTHESIS_SEARCH"
    PROPOSAL_VALIDATED = "PROPOSAL_VALIDATED"
    EXECUTED = "EXECUTED"
    TARGET_EDGE_CHANGED = "TARGET_EDGE_CHANGED"
    BLOCKER_CLEARED = "BLOCKER_CLEARED"
    FITNESS_EVALUATED = "FITNESS_EVALUATED"
    EXPERIENCE_LEARNED = "EXPERIENCE_LEARNED"
    META_UPDATED = "META_UPDATED"


class Layer(str, Enum):
    """The four §2.9 outcome layers; each terminal belongs to exactly one."""

    FRAMEWORK = "framework"
    SEARCH = "search"
    EXECUTION = "execution"
    FITNESS = "fitness"


class Outcome(str, Enum):
    """Closed §2.9 funnel terminal vocabulary.

    Framework: the case never produced a valid repair attempt.
    Search: planning produced no valid proposal.
    Execution: the proposal ran but the target edge did not move / blocker
    did not clear.
    Fitness: the edge moved and blocker cleared; fitness then judged the
    terminal improvement. Only fitness outcomes reach experience learning.
    """

    NON_EVOLVABLE = "NON_EVOLVABLE"
    UNADDRESSABLE = "UNADDRESSABLE"
    ROUTING_FAILURE = "ROUTING_FAILURE"
    SCHEMA_FAILURE = "SCHEMA_FAILURE"
    INFRA_FAILURE = "INFRA_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    NO_HYPOTHESIS = "NO_HYPOTHESIS"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    DUPLICATE_PROPOSAL = "DUPLICATE_PROPOSAL"
    ACTION_NOT_DISPATCHED = "ACTION_NOT_DISPATCHED"
    PROVIDER_NOT_COMPLIED = "PROVIDER_NOT_COMPLIED"
    TARGET_EDGE_UNCHANGED = "TARGET_EDGE_UNCHANGED"
    BLOCKER_NOT_CLEARED = "BLOCKER_NOT_CLEARED"
    SOURCE_NOT_IMPROVED = "SOURCE_NOT_IMPROVED"
    CANARY_REGRESSION = "CANARY_REGRESSION"
    TRANSFER_REGRESSION = "TRANSFER_REGRESSION"
    ACCEPTED = "ACCEPTED"


OUTCOME_LAYER: dict[Outcome, Layer] = {
    Outcome.NON_EVOLVABLE: Layer.FRAMEWORK,
    Outcome.UNADDRESSABLE: Layer.FRAMEWORK,
    Outcome.ROUTING_FAILURE: Layer.FRAMEWORK,
    Outcome.SCHEMA_FAILURE: Layer.FRAMEWORK,
    Outcome.INFRA_FAILURE: Layer.FRAMEWORK,
    Outcome.BUDGET_EXHAUSTED: Layer.FRAMEWORK,
    Outcome.NO_HYPOTHESIS: Layer.SEARCH,
    Outcome.INVALID_PROPOSAL: Layer.SEARCH,
    Outcome.DUPLICATE_PROPOSAL: Layer.SEARCH,
    Outcome.ACTION_NOT_DISPATCHED: Layer.EXECUTION,
    Outcome.PROVIDER_NOT_COMPLIED: Layer.EXECUTION,
    Outcome.TARGET_EDGE_UNCHANGED: Layer.EXECUTION,
    Outcome.BLOCKER_NOT_CLEARED: Layer.EXECUTION,
    Outcome.SOURCE_NOT_IMPROVED: Layer.FITNESS,
    Outcome.CANARY_REGRESSION: Layer.FITNESS,
    Outcome.TRANSFER_REGRESSION: Layer.FITNESS,
    Outcome.ACCEPTED: Layer.FITNESS,
}

FITNESS_OUTCOMES: frozenset[Outcome] = frozenset(
    {
        Outcome.ACCEPTED,
        Outcome.SOURCE_NOT_IMPROVED,
        Outcome.CANARY_REGRESSION,
        Outcome.TRANSFER_REGRESSION,
    }
)

# C2: a framework-invariant violation, counted on its own line and never
# folded into a no-edit / convergence bucket.
UNADDRESSABLE_OUTCOMES: frozenset[Outcome] = frozenset({Outcome.UNADDRESSABLE})


@dataclass(frozen=True)
class Transition:
    """One legal edge of the §7 machine.

    ``to`` is None for absorbing layer terminals; a fitness verdict sets
    ``outcome`` and continues to EXPERIENCE_LEARNED.
    """

    from_state: State
    to: State | None
    outcome: Outcome | None = None


def _t(
    from_state: State, to: State | None, outcome: Outcome | None = None
) -> Transition:
    return Transition(from_state, to, outcome)


LEGAL_TRANSITIONS: tuple[Transition, ...] = (
    _t(State.DETECTED, State.CAUSAL_FAILURE_ANALYZED),
    _t(State.CAUSAL_FAILURE_ANALYZED, State.REPAIRABLE),
    _t(State.CAUSAL_FAILURE_ANALYZED, None, Outcome.NON_EVOLVABLE),
    _t(State.CAUSAL_FAILURE_ANALYZED, None, Outcome.UNADDRESSABLE),
    _t(State.CAUSAL_FAILURE_ANALYZED, None, Outcome.SCHEMA_FAILURE),
    _t(State.REPAIRABLE, State.REPAIR_PLANNED),
    _t(State.REPAIRABLE, None, Outcome.UNADDRESSABLE),
    _t(State.REPAIR_PLANNED, State.HYPOTHESIS_SEARCH),
    _t(State.REPAIR_PLANNED, None, Outcome.ROUTING_FAILURE),
    _t(State.REPAIR_PLANNED, None, Outcome.INFRA_FAILURE),
    _t(State.REPAIR_PLANNED, None, Outcome.BUDGET_EXHAUSTED),
    _t(State.HYPOTHESIS_SEARCH, State.PROPOSAL_VALIDATED),
    _t(State.HYPOTHESIS_SEARCH, None, Outcome.NO_HYPOTHESIS),
    _t(State.HYPOTHESIS_SEARCH, None, Outcome.ROUTING_FAILURE),
    _t(State.HYPOTHESIS_SEARCH, None, Outcome.INFRA_FAILURE),
    _t(State.HYPOTHESIS_SEARCH, None, Outcome.BUDGET_EXHAUSTED),
    _t(State.PROPOSAL_VALIDATED, State.EXECUTED),
    _t(State.PROPOSAL_VALIDATED, None, Outcome.INVALID_PROPOSAL),
    _t(State.PROPOSAL_VALIDATED, None, Outcome.DUPLICATE_PROPOSAL),
    _t(State.EXECUTED, State.TARGET_EDGE_CHANGED),
    _t(State.EXECUTED, None, Outcome.ACTION_NOT_DISPATCHED),
    _t(State.EXECUTED, None, Outcome.PROVIDER_NOT_COMPLIED),
    _t(State.EXECUTED, None, Outcome.INFRA_FAILURE),
    _t(State.EXECUTED, None, Outcome.BUDGET_EXHAUSTED),
    _t(State.TARGET_EDGE_CHANGED, State.BLOCKER_CLEARED),
    _t(State.TARGET_EDGE_CHANGED, None, Outcome.TARGET_EDGE_UNCHANGED),
    _t(State.BLOCKER_CLEARED, State.FITNESS_EVALUATED),
    _t(State.BLOCKER_CLEARED, None, Outcome.BLOCKER_NOT_CLEARED),
    _t(State.FITNESS_EVALUATED, State.EXPERIENCE_LEARNED, Outcome.ACCEPTED),
    _t(
        State.FITNESS_EVALUATED,
        State.EXPERIENCE_LEARNED,
        Outcome.SOURCE_NOT_IMPROVED,
    ),
    _t(
        State.FITNESS_EVALUATED,
        State.EXPERIENCE_LEARNED,
        Outcome.CANARY_REGRESSION,
    ),
    _t(
        State.FITNESS_EVALUATED,
        State.EXPERIENCE_LEARNED,
        Outcome.TRANSFER_REGRESSION,
    ),
    _t(State.EXPERIENCE_LEARNED, State.META_UPDATED),
)


class IllegalTransition(Exception):
    """Raised when a case attempts a transition absent from the §7 table."""


class RepairStateMachine:
    """Drives one case through the §7 sequence.

    Instantiate per case. The runner calls ``transition`` with the next
    state (or ``None`` for an absorbing terminal) plus the outcome the case
    produced at that point. Absorbing terminals end the case; a fitness
    verdict continues to EXPERIENCE_LEARNED -> META_UPDATED so that the
    credit chain and experience record attach to the same case.
    """

    def __init__(self) -> None:
        self._state: State | None = State.DETECTED
        self._outcome: Outcome | None = None
        self._path: list[Transition] = []

    @property
    def state(self) -> State | None:
        return self._state

    @property
    def outcome(self) -> Outcome | None:
        return self._outcome

    @property
    def done(self) -> bool:
        # Absorbing terminals and META_UPDATED both end the case.
        return self._state is None or self._state is State.META_UPDATED

    @property
    def path(self) -> list[Transition]:
        return list(self._path)

    def transition(
        self, to: State | None, *, outcome: Outcome | None = None
    ) -> Transition:
        if self._state is None:
            raise IllegalTransition(
                f"case already terminated with outcome {self._outcome!r}"
            )
        spec = _match(self._state, to, outcome)
        if spec is None:
            raise IllegalTransition(
                f"no legal §7 edge from {self._state!r} to {to!r} "
                f"with outcome {outcome!r}"
            )
        if to is None:
            # Absorbing layer terminal: record outcome, end the case.
            self._outcome = outcome
            self._state = None
        else:
            self._state = to
            if outcome is not None:
                # Fitness verdict: recorded but the case keeps walking to
                # experience learning; the credit chain is attached here.
                self._outcome = outcome
        self._path.append(spec)
        return spec


def _match(
    frm: State, to: State | None, outcome: Outcome | None
) -> Transition | None:
    for spec in LEGAL_TRANSITIONS:
        if spec.from_state is frm and spec.to is to and spec.outcome is outcome:
            return spec
    return None


def legal_transitions() -> Iterator[Transition]:
    yield from LEGAL_TRANSITIONS


def is_legal(
    frm: State, to: State | None, outcome: Outcome | None = None
) -> bool:
    return _match(frm, to, outcome) is not None


class OutcomeFunnel:
    """Per-run §2.9 outcome aggregator.

    Each case deposits exactly one terminal outcome; the funnel keeps an
    independent count per outcome and per layer for the result JSON. C2
    keeps UNADDRESSABLE on its own counter, never folded into a no-edit or
    convergence line.
    """

    def __init__(self) -> None:
        self._counts: Counter[Outcome] = Counter()

    def record(self, outcome: Outcome) -> None:
        self._counts[outcome] += 1

    def count(self, outcome: Outcome) -> int:
        return self._counts[outcome]

    def layer_total(self, layer: Layer) -> int:
        return sum(
            n for outcome, n in self._counts.items() if OUTCOME_LAYER[outcome] is layer
        )

    def accepted(self) -> int:
        return self._counts[Outcome.ACCEPTED]

    def unaddressable(self) -> int:
        # C2: independent count, not part of no-edit / convergence stats.
        return self._counts[Outcome.UNADDRESSABLE]

    def to_dict(self) -> dict[str, Any]:
        layers: dict[str, dict[str, int]] = {
            layer.value: {outcome.value: self._counts[outcome] for outcome in _layer_members(layer)}
            for layer in Layer
        }
        return {
            "by_layer": layers,
            "totals": {
                layer.value: self.layer_total(layer) for layer in Layer
            },
            "accepted": self.accepted(),
            "unaddressable": self.unaddressable(),
        }


def _layer_members(layer: Layer) -> tuple[Outcome, ...]:
    return tuple(o for o in OUTCOME_LAYER if OUTCOME_LAYER[o] is layer)
