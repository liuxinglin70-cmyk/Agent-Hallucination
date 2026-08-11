"""Mechanism-level experience memory: the single Experience Truth.

Plan §2.3 names ``experience/memory.py`` the single authority for experience
truth. This module stores the structural failure-mechanism index keyed by the
episode's deterministic mechanism signature. Per signature it holds:

- observation count and outcome counts;
- four deterministic representative slots (effective / no-effect /
  structural-reject / no-proposal) holding the most informative recent
  attempt of that class;
- a bounded dedup ledger of opaque episode ids so checkpoint resume never
  double-counts an observation.

Retrieval walks the discrete structure levels (exact signature, then
layer+artifact+direction+consumer, then layer+artifact+direction) and returns
at most two prompt-safe historical references. No embeddings, similarity
thresholds or case identities participate in the lookup.

The interface aligns with the causal layer: ``mechanism_signature`` is the
index key and ``episode_id`` is the stable reference key; the memory does not
interpret failure semantics (owner / repairability / cut authority live in
``causal``). C5 (plan §2.6) dissolves the composition with the type-conditioned
store that lived in the typing layer: this memory holds the mechanism body
only, so it never imports typing and the bccse<->htc_tes cycle (S1) is
structurally impossible. The type-conditioned experience sub-store remains in
``typing/slices`` because it is keyed by ``HallucinationType`` (a typing-layer
vocabulary); pulling it here would re-introduce the experience->typing import
that the layering rule forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise.causal.episode import (
    CausalFailureEpisode,
    FailureAttempt,
    HistoricalEpisodeReference,
    build_mechanism_signature,
)
from sehd_rise.skills.genome import validate_runtime_rule

SCHEMA_VERSION = "sehd_rise.experience_memory.v1"
# Earlier schema versions are accepted only at this single from_dict boundary;
# in-memory state is immediately the current schema.
_EARLIER_SCHEMA_VERSIONS = frozenset({
    "sehd.evolution_experience_memory.v2",
    "sehd.evolution_experience_memory.v1",
})

# P6 reserved Meta Review sub-domains. They are initialized empty and do not
# affect generation or the gate at this stage; P7 fills them. The closed key
# set is the single authority for the meta_review payload shape.
META_REVIEW_KEYS = (
    "gate_experiences",
    "outcome_validations",
    "meta_state",
    "meta_commit_audit",
    "pending_validation_queue",
    "efficiency_profile",
    "drift_snapshots",
    "dedup_ledgers",
)


def _empty_meta_review() -> dict[str, Any]:
    return {key: ([] if key != "meta_state" else {}) for key in META_REVIEW_KEYS}


NO_EFFECT_REASONS = frozenset({
    "condition_not_matched",
    "provider_did_not_apply_action",
    "output_changed_then_removed_or_quarantined",
    "provider_output_changed_but_downstream_invariant",
    "route_not_reached",
    "candidate_not_injected",
    "unresolved_no_effect",
})

_SLOT_KEYS = ("effective", "no_effect", "structural_reject", "no_proposal")
# Bounded dedup ledger: opaque episode-id prefixes keep resume idempotent
# without persisting full episode lists. A 64-bit prefix collision is
# negligible for episode ids (sha256 hex); the window covers any realistic
# resume point and long same-mechanism runs.
_DEDUP_LEDGER_LIMIT = 1024
_DEDUP_ID_PREFIX = 16


@dataclass
class ExperienceRecord:
    """Compiler-only view of one detection experience.

    Stores the anonymized record of a single case's detection outcome for the
    evolution compiler, without raw response text. Owned by the experience
    layer (plan §2.3 Experience Truth) so every downstream consumer references
    one shape rather than re-deriving a record per module.
    """

    record_key: str
    partition: str
    gold_positive: bool | None
    predicted_positive: bool | None
    gold_responsible_ref: str | None
    predicted_responsible_ref: str | None
    candidate_origin_refs: tuple = ()
    analysis_dimension: str = ""
    origin_relation: str = ""
    evidence_obligation: str = ""
    clean_boundary: str = ""
    certificate_valid: bool = False
    unresolved: bool = False
    model_calls: int = 0
    latency_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_key": self.record_key,
            "partition": self.partition,
            "gold_positive": self.gold_positive,
            "predicted_positive": self.predicted_positive,
            "gold_responsible_ref": self.gold_responsible_ref,
            "predicted_responsible_ref": self.predicted_responsible_ref,
            "candidate_origin_refs": list(self.candidate_origin_refs),
            "analysis_dimension": self.analysis_dimension,
            "origin_relation": self.origin_relation,
            "evidence_obligation": self.evidence_obligation,
            "clean_boundary": self.clean_boundary,
            "certificate_valid": self.certificate_valid,
            "unresolved": self.unresolved,
            "model_calls": self.model_calls,
            "latency_seconds": self.latency_seconds,
            "metadata": self.metadata,
        }


def episode_signature(episode: CausalFailureEpisode) -> tuple[str, ...]:
    """Structural cross-case key of one episode.

    The single derivation of the mechanism signature from an episode: the
    memory stores it as the entry key and the retriever derives the query
    pattern from it, so both sides always compare the same vocabulary.
    """
    slice_payload = episode.causal_slice
    fields = sorted(set([*slice_payload.missing_fields, *slice_payload.malformed_fields]))
    return build_mechanism_signature(
        layer=episode.skill_layer,
        artifact=episode.failure_owner,
        reason_code=episode.reason_code,
        evolution_direction=str(episode.detection_snapshot.get("evolution_direction") or ""),
        candidate_kind=slice_payload.candidate_kind,
        failure_edge_consumer=slice_payload.consumer,
        missing_or_malformed_fields=fields,
    )


def _slot_for(attempt: FailureAttempt) -> str | None:
    decision = str(attempt.decision or "")
    if attempt.no_effect_reason in NO_EFFECT_REASONS:
        # An accepted-but-ineffective attempt (e.g. a policy accepted
        # without a terminal-state change) is a no-effect observation;
        # the accept alone must not claim the effective slot.
        return "no_effect"
    if decision.startswith("accept"):
        return "effective"
    if decision in ("no_edit", "unaddressable", "no_proposal",
                    "skip_no_policy", "skip_no_edit") or (
        attempt.structural_result == "no_proposal"
    ):
        return "no_proposal"
    if attempt.structural_result in ("rejected", "reject") or decision in (
        "structural_reject", "retry_invalid_mutation", "skip_invalid_mutation",
    ) or attempt.structural_result == "invalid_mutation":
        return "structural_reject"
    if decision.startswith("reject") or decision.startswith("rollback"):
        # ``rollback_batch`` / ``rollback_probe`` / ``rollback_resume`` carry
        # the same negative learning signal as a reject: without this clause
        # a worse batch outcome (no no_effect_reason) falls out of every
        # representative slot and the reflection never sees it.
        return "no_effect"
    return None


def _attempt_evidence_fields(attempt: FailureAttempt) -> int:
    return (
        int(bool(attempt.compiled))
        + int(bool(attempt.injected))
        + int(bool(attempt.applied))
        + len(attempt.observable_delta)
        + len(attempt.downstream_court_delta)
        + int(bool(attempt.no_effect_reason))
    )


def _tolerant_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _validated_rule_condition(representative: "Representative") -> str:
    """Recover the reusable discriminator from an admitted historical rule.

    Structural rejects are deliberately excluded: their text has not crossed
    the runtime rule contract and must not become prompt memory. Valid rules
    are reduced to the bounded condition clause; action semantics stay in the
    typed ActionSpec beside it.
    """
    outcome = representative.outcome or {}
    if str(outcome.get("structural_result") or "") != "accepted":
        return ""
    action_spec = representative.action_spec or {}
    module_id = str(action_spec.get("module_id") or "")
    rule_text = " ".join(str(representative.rule_text or "").split())
    valid, _reason = validate_runtime_rule(rule_text, module_id)
    if not valid or not rule_text.lower().startswith(("when ", "if ")):
        return ""
    head = rule_text.split(",", 1)[0]
    condition = head.split(" ", 1)[1] if " " in head else ""
    return condition.strip()[:320]


@dataclass
class Representative:
    completeness: str
    evidence_fields: int
    sequence: int
    episode_id: str
    causal_slice: dict[str, Any]
    action_spec: dict[str, Any]
    outcome: dict[str, Any]
    rule_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "completeness": self.completeness,
            "evidence_fields": self.evidence_fields,
            "sequence": self.sequence,
            "episode_id": self.episode_id,
            "causal_slice": dict(self.causal_slice),
            "action_spec": dict(self.action_spec),
            "outcome": dict(self.outcome),
            "rule_text": self.rule_text,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Representative":
        return cls(
            completeness=str(value.get("completeness") or "partial"),
            evidence_fields=_tolerant_int(value.get("evidence_fields")),
            sequence=_tolerant_int(value.get("sequence")),
            episode_id=str(value.get("episode_id") or ""),
            causal_slice=dict(value.get("causal_slice") or {}),
            action_spec=dict(value.get("action_spec") or {}),
            outcome=dict(value.get("outcome") or {}),
            rule_text=str(value.get("rule_text") or ""),
        )


def _better(
    candidate: Representative,
    current: Representative | None,
) -> bool:
    """Deterministic slot replacement order: completeness, then evidence
    field count, then logical sequence, then episode id tie-break."""
    if current is None:
        return True
    if candidate.completeness != current.completeness:
        return candidate.completeness == "complete"
    if candidate.evidence_fields != current.evidence_fields:
        return candidate.evidence_fields > current.evidence_fields
    if candidate.sequence != current.sequence:
        return candidate.sequence > current.sequence
    return candidate.episode_id < current.episode_id


@dataclass
class MechanismEntry:
    signature: tuple[str, ...]
    observation_count: int = 0
    outcome_counts: dict[str, int] = field(default_factory=dict)
    representatives: dict[str, Representative | None] = field(
        default_factory=dict
    )
    counted_episode_ids: list[str] = field(default_factory=list)
    last_sequence: int = 0

    def upsert_observation(self, episode_id: str) -> bool:
        """Count one observation idempotently; returns True when new."""
        ledger_key = episode_id[:_DEDUP_ID_PREFIX]
        if ledger_key in self.counted_episode_ids:
            return False
        self.counted_episode_ids.insert(0, ledger_key)
        del self.counted_episode_ids[_DEDUP_LEDGER_LIMIT:]
        self.observation_count += 1
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": list(self.signature),
            "observation_count": self.observation_count,
            "outcome_counts": dict(self.outcome_counts),
            "representatives": {
                key: (
                    self.representatives[key].to_dict()
                    if self.representatives.get(key) is not None
                    else None
                )
                for key in _SLOT_KEYS
            },
            "counted_episode_ids": list(self.counted_episode_ids),
            "last_sequence": self.last_sequence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MechanismEntry":
        entry = cls(
            signature=tuple(str(item) for item in value.get("signature") or ()),
            observation_count=_tolerant_int(value.get("observation_count")),
            outcome_counts={
                str(key): _tolerant_int(count)
                for key, count in dict(value.get("outcome_counts") or {}).items()
            },
            last_sequence=_tolerant_int(value.get("last_sequence")),
        )
        for key in _SLOT_KEYS:
            rep_payload = value.get("representatives") or {}
            rep = rep_payload.get(key)
            if isinstance(rep, Mapping):
                entry.representatives[key] = Representative.from_dict(rep)
        entry.counted_episode_ids = [
            str(item)[:_DEDUP_ID_PREFIX]
            for item in (value.get("counted_episode_ids") or ())[:_DEDUP_LEDGER_LIMIT]
        ]
        return entry


class ExperienceMemory:
    """Structural mechanism memory: the single Experience Truth (plan §2.3).

    Stores the mechanism index keyed by ``episode_signature``. C5 dissolved
    the composed type-conditioned store; this class holds the mechanism body
    only and never imports the typing layer.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, ...], MechanismEntry] = {}
        self._sequence = 0
        # P6: reserved Meta Review sub-domains (empty; no behavior change).
        self._meta_review: dict[str, Any] = _empty_meta_review()

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _entry_for(self, episode: CausalFailureEpisode) -> MechanismEntry:
        signature = episode_signature(episode)
        entry = self._entries.get(signature)
        if entry is None:
            entry = MechanismEntry(signature=signature)
            self._entries[signature] = entry
        return entry

    def upsert_observation(self, episode: CausalFailureEpisode) -> None:
        """Idempotently count the observation of one episode."""
        entry = self._entry_for(episode)
        if entry.upsert_observation(episode.episode_id):
            entry.last_sequence = max(entry.last_sequence, self._next_sequence())

    def upsert_attempt(self, episode: CausalFailureEpisode, attempt: FailureAttempt) -> None:
        """Record one attempt outcome into its mechanism slot."""
        entry = self._entry_for(episode)
        entry.last_sequence = max(entry.last_sequence, self._next_sequence())
        decision = str(attempt.decision or "unknown")
        entry.outcome_counts[decision] = entry.outcome_counts.get(decision, 0) + 1
        slot = _slot_for(attempt)
        if slot is None:
            return
        candidate = Representative(
            completeness=str(episode.causal_slice.completeness or "partial"),
            evidence_fields=_attempt_evidence_fields(attempt),
            sequence=entry.last_sequence,
            episode_id=episode.episode_id,
            causal_slice=episode.causal_slice.to_dict(),
            action_spec=dict(attempt.action_spec or {}),
            outcome={
                "decision": decision,
                "no_effect_reason": str(attempt.no_effect_reason or ""),
                "structural_result": str(attempt.structural_result or ""),
                "compiled": bool(attempt.compiled),
                "injected": bool(attempt.injected),
                "applied": bool(attempt.applied),
                "observable_delta": list(attempt.observable_delta),
                "downstream_court_delta": list(attempt.downstream_court_delta),
            },
            rule_text=" ".join(str(attempt.proposal_content or "").split()),
        )
        if _better(candidate, entry.representatives.get(slot)):
            entry.representatives[slot] = candidate

    def _reference_for(
        self,
        entry: MechanismEntry,
        current: CausalFailureEpisode,
        representative: Representative,
        level: Sequence[int] | None = None,
    ) -> HistoricalEpisodeReference:
        slice_payload = current.causal_slice
        current_fields = sorted(set(
            [*slice_payload.missing_fields, *slice_payload.malformed_fields]
        ))
        history_slice = representative.causal_slice or {}
        history_fields = sorted(set(
            [
                *(
                    str(item)
                    for item in history_slice.get("missing_fields") or ()
                ),
                *(
                    str(item)
                    for item in history_slice.get("malformed_fields") or ()
                ),
            ]
        ))
        shared_fields = sorted(set(current_fields).intersection(history_fields))
        # Only the facts that participated in the matched retrieval level are
        # presented as shared; a fallback level (e.g. reason_code excluded)
        # must not claim agreement it never checked.
        signature_facts = (
            ("layer", current.skill_layer),
            ("artifact", current.failure_owner),
            ("reason_code", current.reason_code),
            (
                "evolution_direction",
                str(current.detection_snapshot.get("evolution_direction") or ""),
            ),
            ("candidate_kind", slice_payload.candidate_kind),
            ("consumer", slice_payload.consumer),
        )
        matched = set(range(6)) if level is None else set(level)
        shared_facts = {
            label: value
            for index, (label, value) in enumerate(signature_facts)
            if index in matched
        }
        if level is None:
            shared_facts["normalized_fields"] = shared_fields
        return HistoricalEpisodeReference(
            episode_id=representative.episode_id,
            mechanism_signature=entry.signature,
            shared_facts=shared_facts,
            current_only_facts={
                "normalized_fields": sorted(
                    set(current_fields).difference(history_fields)
                ),
            },
            historical_only_facts={
                "normalized_fields": sorted(
                    set(history_fields).difference(current_fields)
                ),
            },
            rule_condition=_validated_rule_condition(representative),
            action_spec=(
                dict(representative.action_spec)
                if representative.action_spec else None
            ),
            local_outcome=(
                dict(representative.outcome)
                if representative.outcome else None
            ),
        )

    def _refs_for_entry(
        self,
        entry: MechanismEntry,
        current: CausalFailureEpisode,
        exclude_episode_id: str,
        level: Sequence[int] | None = None,
    ) -> list[HistoricalEpisodeReference]:
        reps = entry.representatives
        effective = reps.get("effective")
        negative = reps.get("no_effect") or reps.get("structural_reject")
        refs: list[HistoricalEpisodeReference] = []
        effective_added = False
        if effective is not None and effective.episode_id != exclude_episode_id:
            refs.append(self._reference_for(entry, current, effective, level))
            effective_added = True
        if negative is not None and negative.episode_id != exclude_episode_id:
            refs.append(self._reference_for(entry, current, negative, level))
        if not effective_added and len(refs) < 2:
            # Without a local-effective representative, the second slot is
            # filled by the most relevant no-proposal representative.
            no_proposal = reps.get("no_proposal")
            if (
                no_proposal is not None
                and no_proposal.episode_id != exclude_episode_id
            ):
                refs.append(self._reference_for(entry, current, no_proposal, level))
        return refs[:2]

    @staticmethod
    def _match(
        entry: MechanismEntry,
        indices: Sequence[int],
        values: Sequence[str],
    ) -> bool:
        return all(
            len(entry.signature) > index and entry.signature[index] == value
            for index, value in zip(indices, values)
        )

    def retrieve(
        self,
        *,
        episode: CausalFailureEpisode,
        exclude_episode_id: str,
    ) -> list[HistoricalEpisodeReference]:
        """Return at most two compatible historical references.

        Falls back level by level: exact mechanism signature, then
        layer+artifact+direction+consumer, then layer+artifact+direction,
        then no history. Within a level the candidates are consulted in
        deterministic order until one yields usable references.
        """
        signature = episode_signature(episode)
        exact = self._entries.get(signature)
        if exact is not None:
            refs = self._refs_for_entry(exact, episode, exclude_episode_id)
            if refs:
                return refs
        for indices in ((0, 1, 3, 5), (0, 1, 3)):
            candidates = [
                entry
                for entry in self._entries.values()
                if self._match(
                    entry,
                    indices,
                    tuple(signature[index] for index in indices),
                )
            ]
            ordered = sorted(
                candidates,
                key=lambda entry: (
                    -entry.observation_count,
                    -entry.last_sequence,
                    entry.signature,
                ),
            )
            for best in ordered:
                refs = self._refs_for_entry(
                    best, episode, exclude_episode_id, indices,
                )
                if refs:
                    return refs
        return []

    def entries(self) -> Sequence[MechanismEntry]:
        return tuple(self._entries.values())

    @property
    def meta_review(self) -> dict[str, Any]:
        """Reserved Meta Review sub-domains (P6: empty; P7 fills them)."""
        return self._meta_review

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "failure_mechanisms": [
                entry.to_dict() for entry in self._entries.values()
            ],
            "meta_review": {
                key: (
                    list(value) if isinstance(value, list) else dict(value)
                )
                for key, value in self._meta_review.items()
            },
        }

    def from_dict(self, data: Mapping[str, Any] | None) -> None:
        """Load the current schema, or accept an earlier artifact.

        Boundary discipline: an earlier-schema payload is upgraded to the
        current in-memory shape only here. C5 demoted the type-conditioned
        store out of this module, so a ``type_experience_memory`` block in an
        earlier artifact is not loaded (it is owned by the typing layer); any
        unknown schema fails closed with ``ValueError``.
        """
        data = data or {}
        schema = str(data.get("schema_version") or "")
        if schema == SCHEMA_VERSION or schema in _EARLIER_SCHEMA_VERSIONS:
            self._entries = {
                entry.signature: entry
                for entry in (
                    MechanismEntry.from_dict(dict(payload))
                    for payload in (data.get("failure_mechanisms") or ())
                    if isinstance(payload, Mapping)
                )
                if entry.signature
            }
            self._meta_review = self._normalize_meta_review(
                data.get("meta_review")
            )
        elif schema:
            raise ValueError(
                f"unknown experience memory schema_version: {schema!r}"
            )
        else:
            # Type-only artifact (no schema_version): it carried only the
            # demoted type-conditioned store, so the mechanism index is empty.
            self._entries = {}
            self._meta_review = _empty_meta_review()
        self._sequence = max(
            [entry.last_sequence for entry in self._entries.values()] or [0]
        )

    @staticmethod
    def _normalize_meta_review(value: Any) -> dict[str, Any]:
        """Normalize the meta_review sub-domains, dropping unknown keys."""
        normalized = _empty_meta_review()
        if not isinstance(value, Mapping):
            return normalized
        for key in META_REVIEW_KEYS:
            incoming = value.get(key)
            if key == "meta_state":
                if isinstance(incoming, Mapping):
                    normalized[key] = dict(incoming)
            elif isinstance(incoming, list):
                normalized[key] = list(incoming)
        return normalized
