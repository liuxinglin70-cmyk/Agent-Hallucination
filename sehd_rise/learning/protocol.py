"""SEHD-v7 learning protocol: five first-class learning types.

The review's §13 decouples the training protocol into five first-class types so
"worth how to fix" and "can the current RuleSpec express it" stop being merged
too early:

    FailureObservation    what actually happened
    RepairIntent          which causal edge to change, DSL-independent
    ExecutableCandidate   how the current capability space implements it
    EvaluationEvidence    what happened after execution
    EvolutionMemory       when this class of edit is worth retrying

The coupling that previously collapsed a real mechanism need into ``NO_HYPOTHESIS``
is broken here by ``RepairIntent``: it names the causal edge and a
``missing_capability`` even when the current registry has no primitive, so the
planner records ``CAPABILITY_GAP: missing capability = ...`` instead of a silent
abstention. ``classify_candidate_failure`` maps the closed prevalidation reasons
onto the §14#4 taxonomy (``DSL_NOT_REPRESENTABLE`` / ``ENTITY_NOT_FOUND`` /
``SEARCH_FAILURE`` / ``STRUCTURAL_INVALID`` / ``BINDING_AMBIGUOUS``), and
``CandidateArchive`` keeps a bounded, staged archive of rejected candidates
instead of a single mutation lineage.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise.causal.episode import CausalFailureEpisode

SCHEMA_VERSION = "sehd_rise.learning_protocol.v1"

# ============================================================
# Closed failure-class vocabulary (§14#4)
# ============================================================

FAILURE_CLASS_DSL_NOT_REPRESENTABLE = "dsl_not_representable"
FAILURE_CLASS_ENTITY_NOT_FOUND = "entity_not_found"
FAILURE_CLASS_SEARCH_FAILURE = "search_failure"
FAILURE_CLASS_STRUCTURAL_INVALID = "structural_invalid"
FAILURE_CLASS_BINDING_AMBIGUOUS = "binding_ambiguous"
FAILURE_CLASS_EXECUTABLE = "executable"

FAILURE_CLASSES = frozenset({
    FAILURE_CLASS_DSL_NOT_REPRESENTABLE,
    FAILURE_CLASS_ENTITY_NOT_FOUND,
    FAILURE_CLASS_SEARCH_FAILURE,
    FAILURE_CLASS_STRUCTURAL_INVALID,
    FAILURE_CLASS_BINDING_AMBIGUOUS,
    FAILURE_CLASS_EXECUTABLE,
})

# Candidate staging (§14#4): a candidate is preserved at the stage where it
# stopped, so the funnel can tell "the DSL could not express it" from "the
# search never found it" from "the entity was not locatable".
STAGE_RAW_HYPOTHESIS = "raw_hypothesis"
STAGE_SCHEMA_VALID = "schema_valid"
STAGE_BINDABLE = "bindable"
STAGE_EXECUTABLE = "executable"
CANDIDATE_STAGES = frozenset({
    STAGE_RAW_HYPOTHESIS,
    STAGE_SCHEMA_VALID,
    STAGE_BINDABLE,
    STAGE_EXECUTABLE,
})


def classify_candidate_failure(reason: str) -> str:
    """Map one closed prevalidation reason onto the §14#4 failure class.

    The three learnable classes are separated from each other and from the
    two mechanical classes, so a recurring ``DSL_NOT_REPRESENTABLE`` feeds the
    capability-gap curriculum while ``SEARCH_FAILURE`` feeds the search-policy
    learner (§2.8) and ``ENTITY_NOT_FOUND`` feeds the binding projector.
    """
    text = str(reason or "")
    if (
        text.startswith("condition_feature_not_bindable_in_domain:")
        or text.startswith("condition_feature_unregistered:")
        or text.startswith("condition_value_unregistered:")
        or text.startswith("condition_operator_invalid:")
        or text.startswith("condition_feature_source_invalid:")
    ):
        return FAILURE_CLASS_DSL_NOT_REPRESENTABLE
    if (
        text.startswith("target_entity_not_found:")
        or text.startswith("condition_not_consistent_with_target_entity:")
        or text == "BINDING_NOT_FOUND"
        or text.startswith("BINDING_NOT_FOUND")
        or "no_entity_satisfies_condition" in text
    ):
        return FAILURE_CLASS_ENTITY_NOT_FOUND
    if text == "BINDING_AMBIGUOUS" or text.startswith("BINDING_AMBIGUOUS"):
        return FAILURE_CLASS_BINDING_AMBIGUOUS
    if (
        text.startswith("action_not_bound_to_repair_edge:")
        or text.startswith("action_not_bound_to_failure_edge:")
        or text.startswith("repair_edge_id_missing")
        or text.startswith("repair_edge_id_unknown")
        or text.startswith("remove_rule_edge_id_missing")
        or text.startswith("remove_rule_edge_id_unknown")
        or text.startswith("repair_edge_owner_mismatch:")
        or text.startswith("remove_rule_edge_owner_mismatch:")
        # A wrong-polarity or unowned opcode is a search miss, not a DSL gap:
        # the registry can express the repair; the model selected the wrong
        # operation, so the curriculum must steer search, not extend the DSL.
        or text.startswith("action_polarity_mismatch:")
        or text.startswith("action_opcode_unowned:")
    ):
        return FAILURE_CLASS_SEARCH_FAILURE
    return FAILURE_CLASS_STRUCTURAL_INVALID


# ============================================================
# Five first-class learning types
# ============================================================

@dataclass(frozen=True)
class FailureObservation:
    """What actually happened: the detector's terminal proof state.

    Beyond the terminal proof, the observation carries the causal-edge
    identity (``output_object`` / ``output_field`` / ``consumer``) and the
    episode's cross-claim ``dependency_refs`` so a mechanism classifier can
    tell *why* this failure shape occurred. ``trajectory_hints`` holds the
    closed label-free locus-evidence tokens projected from the trajectory by
    ``skills.anchors.project_locus_evidence``; the loop attaches them because
    the episode alone never sees the raw record.
    """

    terminal_state: str
    blocking_predicate: str
    failure_owner: str
    skill_layer: str
    reason_code: str
    missing_fields: tuple[str, ...]
    malformed_fields: tuple[str, ...]
    candidate_kind: str = ""
    output_object: str = ""
    output_field: str = ""
    consumer: str = ""
    dependency_refs: tuple[str, ...] = ()
    trajectory_hints: tuple[str, ...] = ()

    @classmethod
    def from_episode(cls, episode: CausalFailureEpisode) -> "FailureObservation":
        slice_payload = episode.causal_slice
        edge = episode.causal_failure_edge
        return cls(
            terminal_state=episode.terminal_state,
            blocking_predicate=episode.blocking_predicate,
            failure_owner=episode.failure_owner,
            skill_layer=episode.skill_layer,
            reason_code=episode.reason_code,
            missing_fields=tuple(slice_payload.missing_fields),
            malformed_fields=tuple(slice_payload.malformed_fields),
            candidate_kind=slice_payload.candidate_kind,
            output_object=str(edge.output_object) if edge is not None else "",
            output_field=str(edge.field) if edge is not None else "",
            consumer=str(edge.consumer) if edge is not None else "",
            dependency_refs=tuple(slice_payload.dependency_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal_state": self.terminal_state,
            "blocking_predicate": self.blocking_predicate,
            "failure_owner": self.failure_owner,
            "skill_layer": self.skill_layer,
            "reason_code": self.reason_code,
            "missing_fields": list(self.missing_fields),
            "malformed_fields": list(self.malformed_fields),
            "candidate_kind": self.candidate_kind,
            "output_object": self.output_object,
            "output_field": self.output_field,
            "consumer": self.consumer,
            "dependency_refs": list(self.dependency_refs),
            "trajectory_hints": list(self.trajectory_hints),
        }


@dataclass(frozen=True)
class RepairIntent:
    """Which causal edge to change, expressed DSL-independently.

    ``missing_capability`` is non-empty exactly when the current registry has
    no primitive for the intent — the CAPABILITY_GAP signal the curriculum
    consumes, recorded as a first-class field instead of a silent no-edit.
    """

    owner: str
    output_field: str
    observed: str
    required: str
    skill_layer: str
    reason_code: str
    missing_capability: str = ""

    @classmethod
    def from_episode(
        cls,
        episode: CausalFailureEpisode,
        *,
        missing_capability: str = "",
    ) -> "RepairIntent":
        edge = episode.causal_failure_edge
        return cls(
            owner=episode.failure_owner,
            output_field=str(edge.field) if edge is not None else "",
            observed=str(edge.observed) if edge is not None else "",
            required=str(edge.required) if edge is not None else "",
            skill_layer=episode.skill_layer,
            reason_code=episode.reason_code,
            missing_capability=missing_capability,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "output_field": self.output_field,
            "observed": self.observed,
            "required": self.required,
            "skill_layer": self.skill_layer,
            "reason_code": self.reason_code,
            "missing_capability": self.missing_capability,
        }


@dataclass(frozen=True)
class ExecutableCandidate:
    """How the current capability space implements one RepairIntent.

    ``operation`` names a deterministic backend opcode; it is empty for a
    route-guidance candidate, whose intervention point is the provider route
    named by the RouteAddressability table and whose semantic payload is
    ``mechanism`` + ``guidance`` + ``expected_local_effect`` (the D1 typed
    contract compiled into a route-local guidance block, not free prose).
    """

    operation: str = ""
    target_module: str = ""
    action: str = ""
    condition: str = ""
    mechanism: str = ""
    guidance: str = ""
    expected_local_effect: str = ""
    stage: str = STAGE_RAW_HYPOTHESIS
    failure_class: str = FAILURE_CLASS_EXECUTABLE
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "target_module": self.target_module,
            "action": self.action,
            "condition": self.condition,
            "mechanism": self.mechanism,
            "guidance": self.guidance,
            "expected_local_effect": self.expected_local_effect,
            "stage": self.stage,
            "failure_class": self.failure_class,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EvaluationEvidence:
    """What happened after execution: the deterministic effect record."""

    fired: bool
    target_edge_changed: bool
    blocker_cleared: bool
    terminal_transition: str
    decision: str
    promotable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "fired": self.fired,
            "target_edge_changed": self.target_edge_changed,
            "blocker_cleared": self.blocker_cleared,
            "terminal_transition": self.terminal_transition,
            "decision": self.decision,
            "promotable": self.promotable,
        }


@dataclass(frozen=True)
class EvolutionMemory:
    """When a class of edit is worth retrying: mechanism + outcome slot."""

    mechanism_signature: tuple[str, ...]
    outcome_slot: str
    retry_worthy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism_signature": list(self.mechanism_signature),
            "outcome_slot": self.outcome_slot,
            "retry_worthy": self.retry_worthy,
        }


# ============================================================
# Candidate archive (§14#4)
# ============================================================

@dataclass(frozen=True)
class ArchivedCandidate:
    """One rejected candidate preserved at the stage where it stopped."""

    stage: str
    failure_class: str
    reason: str
    candidate: ExecutableCandidate
    case_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "failure_class": self.failure_class,
            "reason": self.reason,
            "candidate": self.candidate.to_dict(),
            "case_id": self.case_id,
        }


class CandidateArchive:
    """Bounded, staged archive of candidates the current lineage produced.

    Replaced the single-mutation-lineage lossy discard: a candidate rejected at
    one stage stays observable for the search-policy learner and the
    capability-gap curriculum, keyed by its failure class.
    """

    def __init__(self, capacity: int = 64) -> None:
        self._capacity = max(1, int(capacity))
        self._items: deque[ArchivedCandidate] = deque()

    def archive(
        self,
        *,
        stage: str,
        failure_class: str,
        reason: str,
        candidate: ExecutableCandidate,
        case_id: str = "",
    ) -> None:
        self._items.append(ArchivedCandidate(
            stage=stage if stage in CANDIDATE_STAGES else STAGE_RAW_HYPOTHESIS,
            failure_class=(
                failure_class if failure_class in FAILURE_CLASSES
                else FAILURE_CLASS_STRUCTURAL_INVALID
            ),
            reason=str(reason or "")[:200],
            candidate=candidate,
            case_id=str(case_id or ""),
        ))
        while len(self._items) > self._capacity:
            self._items.popleft()

    def recent(
        self,
        *,
        failure_class: str = "",
        stage: str = "",
        limit: int = 8,
    ) -> tuple[ArchivedCandidate, ...]:
        items = [
            item for item in self._items
            if (not failure_class or item.failure_class == failure_class)
            and (not stage or item.stage == stage)
        ]
        return tuple(reversed(items[-max(0, int(limit)):]))

    def failure_class_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter(item.failure_class for item in self._items)
        return {name: counts.get(name, 0) for name in FAILURE_CLASSES}

    def __len__(self) -> int:
        return len(self._items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "capacity": self._capacity,
            "items": [item.to_dict() for item in self._items],
            "failure_class_counts": self.failure_class_counts(),
        }


# ============================================================
# Archive -> policy routing (§14#4 feedback closure)
# ============================================================
# A consecutive same-class rejection run steers exactly one learner:
# DSL_NOT_REPRESENTABLE -> the capability curriculum, SEARCH_FAILURE -> the
# search-policy learner (minibatch meta), ENTITY_NOT_FOUND -> the anchor
# projector (entity-menu budget). The other classes carry no routing signal.
ARCHIVE_SIGNAL_DSL_CURRICULUM = "dsl_recurring_curriculum_refresh"
ARCHIVE_SIGNAL_SEARCH_POLICY = "search_recurring_policy_refresh"
ARCHIVE_SIGNAL_ANCHOR_PROJECTION = "entity_recurring_anchor_refresh"

_ARCHIVE_SIGNAL_BY_CLASS: dict[str, str] = {
    FAILURE_CLASS_DSL_NOT_REPRESENTABLE: ARCHIVE_SIGNAL_DSL_CURRICULUM,
    FAILURE_CLASS_SEARCH_FAILURE: ARCHIVE_SIGNAL_SEARCH_POLICY,
    FAILURE_CLASS_ENTITY_NOT_FOUND: ARCHIVE_SIGNAL_ANCHOR_PROJECTION,
}


def archive_policy_signals(
    archive: "CandidateArchive", *, threshold: int = 4,
) -> tuple[str, ...]:
    """Closed policy signals for a consecutive same-class rejection run.

    Only the current tail counts: walk the archive newest-first while the
    failure class stays identical, and emit that class's routing signal when
    the run reaches ``threshold``. Distinct classes never mix, so an
    interleaved tail emits nothing and one learner is never steered by
    another class's failures.
    """
    items = archive.recent(limit=len(archive))
    if not items:
        return ()
    head_class = items[0].failure_class
    run_length = 0
    for item in items:
        if item.failure_class != head_class:
            break
        run_length += 1
    signal = _ARCHIVE_SIGNAL_BY_CLASS.get(head_class, "")
    if signal and run_length >= max(1, int(threshold)):
        return (signal,)
    return ()


__all__ = [
    "ARCHIVE_SIGNAL_ANCHOR_PROJECTION",
    "ARCHIVE_SIGNAL_DSL_CURRICULUM",
    "ARCHIVE_SIGNAL_SEARCH_POLICY",
    "ArchivedCandidate",
    "CANDIDATE_STAGES",
    "CandidateArchive",
    "EvaluationEvidence",
    "EvolutionMemory",
    "ExecutableCandidate",
    "FAILURE_CLASSES",
    "FAILURE_CLASS_BINDING_AMBIGUOUS",
    "FAILURE_CLASS_DSL_NOT_REPRESENTABLE",
    "FAILURE_CLASS_ENTITY_NOT_FOUND",
    "FAILURE_CLASS_EXECUTABLE",
    "FAILURE_CLASS_SEARCH_FAILURE",
    "FAILURE_CLASS_STRUCTURAL_INVALID",
    "FailureObservation",
    "RepairIntent",
    "SCHEMA_VERSION",
    "STAGE_BINDABLE",
    "STAGE_EXECUTABLE",
    "STAGE_RAW_HYPOTHESIS",
    "STAGE_SCHEMA_VALID",
    "archive_policy_signals",
    "classify_candidate_failure",
]
