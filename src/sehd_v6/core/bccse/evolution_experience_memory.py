"""Online causal experience memory.

Composes the type-conditioned statistics (TypeExperienceMemory) with the
structural failure-mechanism index.  The mechanism index is keyed by the
episode's deterministic mechanism signature and stores, per signature:

- observation count and outcome counts;
- four deterministic representative slots (effective / no-effect /
  structural-reject / no-proposal) holding the most informative recent
  attempt of that class;
- a bounded dedup ledger of opaque episode ids so checkpoint resume never
  double-counts an observation.

Retrieval walks the discrete structure levels (exact signature, then
layer+artifact+direction+consumer, then layer+artifact+direction) and
returns at most two prompt-safe historical references.  No embeddings,
similarity thresholds or case identities participate in the lookup.

The artifact schema is ``sehd.evolution_experience_memory.v1``; loading a
legacy type-only artifact is handled by one migration boundary inside
``from_dict``, and in-memory state is immediately the current schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .failure_episode import (
    FailureAttempt,
    FailureEpisode,
    HistoricalEpisodeReference,
    build_mechanism_signature,
)
from ..htc_tes.type_aware_fitness import TypeExperienceMemory

SCHEMA_VERSION = "sehd.evolution_experience_memory.v1"

NO_EFFECT_REASONS = frozenset({
    "condition_not_matched",
    "provider_did_not_apply_action",
    "output_changed_then_removed_or_quarantined",
    "provider_output_changed_but_downstream_invariant",
    "expected_delta_observed",
    "unresolved_no_effect",
})

_SLOT_KEYS = ("effective", "no_effect", "structural_reject", "no_proposal")
# Bounded dedup ledger: opaque episode-id prefixes keep resume idempotent
# without persisting full episode lists.  A 64-bit prefix collision is
# negligible for episode ids (sha256 hex); the window covers any realistic
# resume point and long same-mechanism runs.
_DEDUP_LEDGER_LIMIT = 1024
_DEDUP_ID_PREFIX = 16


def _episode_signature(episode: FailureEpisode) -> tuple[str, ...]:
    slice_payload = episode.causal_slice
    fields = sorted(set([*slice_payload.missing_fields, *slice_payload.malformed_fields]))
    return build_mechanism_signature(
        layer=str(episode.attribution.get("layer") or ""),
        artifact=str(episode.attribution.get("artifact") or ""),
        reason_code=str(episode.attribution.get("reason_code") or ""),
        evolution_direction=str(episode.detection.get("evolution_direction") or ""),
        candidate_kind=slice_payload.candidate_kind,
        failure_edge_consumer=slice_payload.consumer,
        missing_or_malformed_fields=fields,
    )


def _slot_for(attempt: FailureAttempt) -> str | None:
    decision = str(attempt.decision or "")
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
    if attempt.no_effect_reason in NO_EFFECT_REASONS:
        return "no_effect"
    if decision.startswith("reject"):
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
            evidence_fields=int(value.get("evidence_fields") or 0),
            sequence=int(value.get("sequence") or 0),
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
            observation_count=int(value.get("observation_count") or 0),
            outcome_counts={
                str(key): int(count)
                for key, count in dict(value.get("outcome_counts") or {}).items()
            },
            last_sequence=int(value.get("last_sequence") or 0),
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


class EvolutionExperienceMemory:
    """Combined type statistics + structural mechanism memory."""

    def __init__(
        self,
        type_experience_memory: TypeExperienceMemory | None = None,
    ) -> None:
        self._type_memory = type_experience_memory or TypeExperienceMemory()
        self._entries: dict[tuple[str, ...], MechanismEntry] = {}
        self._sequence = 0

    @property
    def type_experience_memory(self) -> TypeExperienceMemory:
        return self._type_memory

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _entry_for(self, episode: FailureEpisode) -> MechanismEntry:
        signature = _episode_signature(episode)
        entry = self._entries.get(signature)
        if entry is None:
            entry = MechanismEntry(signature=signature)
            self._entries[signature] = entry
        return entry

    def upsert_observation(self, episode: FailureEpisode) -> None:
        """Idempotently count the observation of one episode."""
        entry = self._entry_for(episode)
        if entry.upsert_observation(episode.episode_id):
            entry.last_sequence = max(entry.last_sequence, self._next_sequence())

    def upsert_attempt(self, episode: FailureEpisode, attempt: FailureAttempt) -> None:
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
        current: FailureEpisode,
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
            ("layer", current.attribution.get("layer", "")),
            ("artifact", current.attribution.get("artifact", "")),
            ("reason_code", current.attribution.get("reason_code", "")),
            (
                "evolution_direction",
                current.detection.get("evolution_direction", ""),
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
        current: FailureEpisode,
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
        episode: FailureEpisode,
        exclude_episode_id: str,
    ) -> list[HistoricalEpisodeReference]:
        """Return at most two compatible historical references.

        Falls back level by level: exact mechanism signature, then
        layer+artifact+direction+consumer, then layer+artifact+direction,
        then no history.  Within a level the candidates are consulted in
        deterministic order until one yields usable references.
        """
        signature = _episode_signature(episode)
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "type_experience_memory": self._type_memory.to_dict(),
            "failure_mechanisms": [
                entry.to_dict() for entry in self._entries.values()
            ],
        }

    def from_dict(self, data: Mapping[str, Any] | None) -> None:
        """Load the current schema, or migrate a legacy type-only artifact."""
        data = data or {}
        if str(data.get("schema_version") or "") == SCHEMA_VERSION:
            if not isinstance(data.get("type_experience_memory"), Mapping):
                raise ValueError(
                    "current-schema experience memory artifact is missing "
                    "type_experience_memory; refusing to treat a partial "
                    "write as a legacy artifact"
                )
            self._entries = {
                entry.signature: entry
                for entry in (
                    MechanismEntry.from_dict(dict(payload))
                    for payload in (data.get("failure_mechanisms") or ())
                    if isinstance(payload, Mapping)
                )
                if entry.signature
            }
            self._type_memory.from_dict(dict(data["type_experience_memory"]))
        else:
            self._entries = {}
            self._type_memory.from_dict(data)
        self._sequence = max(
            [entry.last_sequence for entry in self._entries.values()] or [0]
        )
