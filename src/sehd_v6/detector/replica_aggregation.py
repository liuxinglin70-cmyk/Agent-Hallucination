"""Test-time-scaling aggregation for independent detector replicas.

Pure, dependency-free functions that combine the three-state verdicts of
several independent detection runs of the same trajectory. The aggregation is
strictly fail-closed: disagreement, ties, or any replica that lost runtime
validity can only downgrade to ``unresolved`` — aggregation never upgrades a
verdict, and it never fabricates a decisive state that the replicas do not
jointly support.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

VALID_STATES: tuple[str, ...] = ("verified_error", "verified_clean", "unresolved")

POLICY_CONSENSUS = "consensus"
POLICY_MAJORITY = "majority"
POLICIES: tuple[str, ...] = (POLICY_CONSENSUS, POLICY_MAJORITY)

UNRESOLVED = "unresolved"


def _normalize_state(value: Any) -> str:
    state = str(value or "").strip()
    return state if state in VALID_STATES else UNRESOLVED


def aggregate_states(
    states: Sequence[str], *, policy: str = POLICY_MAJORITY
) -> str:
    """Combine replica verdicts into one fail-closed verdict.

    - ``consensus``: every replica must agree; anything else is unresolved.
    - ``majority``: one state needs at least ceil(k/2) votes AND strictly
      more than any rival (ties stay unresolved).

    Unknown or empty states are treated as unresolved votes.
    """
    if not states:
        raise ValueError("aggregate_states requires at least one replica state")
    if policy not in POLICIES:
        raise ValueError(f"unknown aggregation policy: {policy!r}")
    normalized = [_normalize_state(state) for state in states]
    counts = Counter(normalized)
    top_state, top_count = counts.most_common(1)[0]
    if policy == POLICY_CONSENSUS:
        return top_state if top_count == len(normalized) else UNRESOLVED
    threshold = (len(normalized) + 1) // 2
    rivals = [count for state, count in counts.items() if state != top_state]
    if top_count >= threshold and top_count > max(rivals, default=0):
        return top_state
    return UNRESOLVED


def aggregate_replicas(
    predictions: Sequence[Mapping[str, Any]], *, policy: str = POLICY_MAJORITY
) -> dict[str, Any]:
    """Aggregate full replica predictions with fail-closed runtime semantics.

    Any replica with ``runtime_valid != True`` invalidates the whole aggregate
    (a crashed replica means the adversarial test was incomplete). The verdict
    itself is still computed from the surviving states so audits can see what
    the healthy replicas believed.
    """
    if not predictions:
        raise ValueError("aggregate_replicas requires at least one prediction")
    replica_states: list[str] = []
    invalid_replicas: list[int] = []
    for index, prediction in enumerate(predictions):
        runtime_valid = prediction.get("runtime_valid") is True
        state = _normalize_state(prediction.get("state"))
        replica_states.append(state)
        if not runtime_valid:
            invalid_replicas.append(index)
    state = aggregate_states(replica_states, policy=policy)
    if invalid_replicas:
        state = UNRESOLVED
    # Unanimity means agreement on a decisive outcome: a set of replicas
    # that all abstain reports the same state but has no decisive verdict
    # to be unanimous about.
    unanimous = (
        len(set(replica_states)) == 1
        and replica_states[0] != UNRESOLVED
        and not invalid_replicas
    )
    return {
        "state": state,
        "policy": policy,
        "replica_count": len(predictions),
        "invalid_replica_indexes": invalid_replicas,
        "replica_states": replica_states,
        "unanimous": unanimous,
        "runtime_valid": not invalid_replicas,
    }
