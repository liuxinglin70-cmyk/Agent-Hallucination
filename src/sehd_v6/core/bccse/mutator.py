"""BCCSE v4 reflective typed-skill mutator (DEPRECATED).

.. deprecated::
    This module is v4 legacy code and is NOT used by the v5 evolution
    main loop (``evolution_runner.py`` uses ``reflection_engine.py``
    instead). The ``MutationProvider`` Protocol below is still imported
    by ``proof_evaluator.DeepSeekMutationProvider``, so it is retained.
    All other functions (``propose_mutations``, ``parse_mutation_proposal``,
    ``_clause_signatures_payload``, etc.) reference v4 genome fields that
    no longer exist in v5 and will raise on call. Do not call them.

The mutator implements GEPA-style reflection over BCCSE's safer substrate:
anonymous boundary-contrastive failure episodes.  A DeepSeek-V4 model may
propose one or two atomic changes, but it cannot approve them, inspect raw
cases, change immutable layers, or emit free-form prompt replacements.

Provider access is dependency-injected through ``MutationProvider`` so the
evolution core remains testable and SDK-independent.  All provider output is
parsed into ``AtomicMutation`` objects and revalidated against the parent
genome before it can reach the independent critics.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .types import FailureEpisode
from .genome import (
    FORBIDDEN_CONTENT_MARKERS,
    MODULE_IDS,
    AtomicMutation,
    MutationOp,
    SkillGenome,
    all_clause_signatures,
    validate_mutation,
)


ALLOWED_MODELS = frozenset({
    "DeepSeek-V4-Flash",
    "DeepSeek-V4-Pro",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
})
MUTABLE_MODULES = frozenset(set(MODULE_IDS) - {"abstention_rule"})
MAX_ATOMIC_MUTATIONS = 2
MAX_EPISODES_PER_REQUEST = 24
MAX_NEW_CONTENT_CHARS = 1200
DSL_BODY_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\([^\r\n]*\)|\[[^\r\n]*\]|\s*[:=><].+)?$"
)


REFLECTIVE_MUTATION_SYSTEM = """You are the BCCSE v4 reflective mutation proposer.
You receive only anonymous, paired boundary-contrastive failure episodes and a
typed skill genome. Reflect on the shared failure mechanism, then propose one
or two minimal typed DSL mutations. Never use or request raw cases, identity,
gold explanation text, outer feedback, or continuous threshold search.
The request includes proposal_index; proposals with different indices must use
distinct, independently defensible mechanisms or operations.

CRITICAL DIVERSITY REQUIREMENTS:
- The request includes existing_clause_signatures for each mutable module.
  Your new_content MUST NOT be semantically equivalent to any existing clause
  body or negative boundary already present in the target module.
- Do NOT restate, rephrase, or trivially rename an existing clause.
- For proposal_index > 0, target a DIFFERENT module or mechanism than what
  earlier proposals would naturally choose. Diversify the search.
- Prefer mutations that address distinct failure mechanisms across episodes.

RESOLUTION MECHANISM GUIDANCE (Round 12, evidence-based):
- For unresolved episodes, the counterfactual_expected_change field tells you
  the expected resolution direction. Use it to choose the right mechanism.
- If the counterfactual says "resolves to verified_error", the prosecution
  pipeline is too weak. Consider tighten_precondition on evidence_obligation
  clauses (e.g. eo_002) to strengthen evidence requirements. DO NOT target
  atomic_verifier_instruction (avi_001) — Round 11 proved that tightening avi_001
  reduces candidate_origin_coverage from 1.0 to 0.5, causing G2 failure.
- If the counterfactual says "resolves to verified_clean", the defense boundary
  is too weak. Consider tighten_precondition on clean_defense_boundary clauses
  (e.g. cdb_001 or cdb_002) to broaden the pattern recognition.
- CRITICAL CONSTRAINTS from empirical evidence:
  * DO NOT modify atomic_verifier_instruction (avi_001 or avi_002). Rounds 10-11
    showed this causes candidate_origin_coverage regression (0.5 < 1.0).
  * DO NOT use broad add_negative_boundary (Round 10 showed it causes boundary
    regressions). PREFER tighten_precondition on existing clauses.
  * PREFERRED targets: evidence_obligation (eo_001, eo_002) and
    clean_defense_boundary (cdb_001, cdb_002, cdb_003, cdb_004).
- Target the MECHANISM that prevents resolution, not the specific case pattern.
  Ask: "what precise precondition change on eo or cdb clauses would help
  unresolved cases WITHOUT affecting coverage or currently correct cases?"

Immutable layers L0/L1/L6/L7 must not change. The abstention_rule is in L6 and
is therefore not a legal target. Do not change CRS weights:
factuality=0.35, counterfactual=0.15, recovery=0.20, process=0.20, scope=0.10.

Return one JSON object only:
{
  "reflection": "short mechanism-level reflection",
  "mutations": [{
    "target_module": "one legal mutable module id",
    "operation": "activate|deactivate|replace_typed_clause|tighten_precondition|add_negative_boundary|retire",
    "target_clause_id": "required for replace/tighten, otherwise null",
    "new_content": "typed DSL clause/precondition/boundary or null",
    "rationale": "episode-grounded mechanism rationale",
    "support_episode_ids": ["episode_..."]
  }]
}

Typed content must be one line and use one of these registered shapes:
bare_snake_case_predicate
predicate_name(argument=value)
predicate_name: typed_value
"""


class MutationProvider(Protocol):
    """Minimal provider interface used by the reflective mutator."""

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_format: Mapping[str, Any] | None = None,
    ) -> str | Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class ProposedMutation:
    """Atomic mutation with anonymous episode provenance."""

    mutation: AtomicMutation
    support_episode_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = self.mutation.to_dict()
        value["support_episode_ids"] = list(self.support_episode_ids)
        return value


@dataclass(frozen=True)
class MutationProposal:
    """Validated output of one reflective provider call."""

    model: str
    reflection: str
    proposed: tuple[ProposedMutation, ...]
    provider_payload_hash: str
    request_hash: str
    schema_version: str = "agenthallu.reflective_mutation_proposal.v4"

    @property
    def mutations(self) -> list[AtomicMutation]:
        return [item.mutation for item in self.proposed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model": self.model,
            "reflection": self.reflection,
            "mutations": [item.to_dict() for item in self.proposed],
            "provider_payload_hash": self.provider_payload_hash,
            "request_hash": self.request_hash,
        }


def _stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            value = "\n".join(lines[1:-1])
            if value.lstrip().startswith("json"):
                value = value.lstrip()[4:].lstrip()
    return value


def _response_object(value: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        result = dict(value)
    else:
        parsed = json.loads(_strip_json_fence(value))
        if not isinstance(parsed, dict):
            raise ValueError("mutator response root must be an object")
        result = parsed
    if set(result) - {"reflection", "mutations"}:
        raise ValueError("mutator response contains unregistered top-level fields")
    return result


def _firewall(value: Any) -> list[str]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    extra = {"raw_case_text", "gold_label", "gold_explanation", "case_id", "source_id", "outer_feedback"}
    return sorted(marker for marker in set(FORBIDDEN_CONTENT_MARKERS) | extra if marker in text)


def _episode_payload(episode: FailureEpisode) -> dict[str, Any]:
    value = episode.to_dict(include_hash=True)
    hits = _firewall(value)
    if hits:
        raise ValueError(f"failure episode violates mutator firewall: {hits}")
    return value


def _module_payload(genome: SkillGenome) -> dict[str, Any]:
    return {
        module_id: genome.modules[module_id].to_dict()
        for module_id in MODULE_IDS
        if module_id in MUTABLE_MODULES
    }


def _clause_signatures_payload(genome: SkillGenome) -> dict[str, list[str]]:
    """Build existing clause signatures payload for the mutator.

    This tells the provider what content already exists so it can avoid
    proposing semantically equivalent restatements.
    """
    signatures = all_clause_signatures(genome)
    return {
        module_id: sorted(sigs)
        for module_id, sigs in signatures.items()
        if sigs
    }


def build_reflective_request(
    genome: SkillGenome,
    episodes: Sequence[FailureEpisode],
    *,
    generation: int,
    proposal_index: int = 0,
) -> str:
    """Build a deterministic raw-case-free mutation request."""

    if not episodes:
        raise ValueError("reflective mutation requires at least one failure episode")
    if proposal_index < 0:
        raise ValueError("proposal_index must be non-negative")
    selected = sorted(episodes, key=lambda item: (item.error_type.value, item.episode_id))[
        :MAX_EPISODES_PER_REQUEST
    ]
    payload = {
        "schema_version": "agenthallu.reflective_mutation_request.v4",
        "generation": generation,
        "proposal_index": proposal_index,
        "parent_genome_hash": genome.content_hash,
        "mutable_modules": _module_payload(genome),
        "existing_clause_signatures": _clause_signatures_payload(genome),
        "failure_episodes": [_episode_payload(item) for item in selected],
        "constraints": {
            "maximum_atomic_mutations": MAX_ATOMIC_MUTATIONS,
            "maximum_modules_changed_per_mutation": 1,
            "immutable_modules": ["abstention_rule"],
            "crs_weights_frozen": [0.35, 0.15, 0.20, 0.20, 0.10],
            "do_not_restate_existing_clauses": True,
            "diversify_across_mechanisms": proposal_index > 0,
        },
    }
    hits = _firewall(payload)
    if hits:
        raise ValueError(f"reflective request violates identifier firewall: {hits}")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_typed_content(operation: MutationOp, content: str | None) -> None:
    operations_requiring_content = {
        MutationOp.REPLACE_TYPED_CLAUSE,
        MutationOp.TIGHTEN_PRECONDITION,
        MutationOp.ADD_NEGATIVE_BOUNDARY,
    }
    if operation in operations_requiring_content and not content:
        raise ValueError(f"{operation.value} requires new_content")
    if content is None:
        return
    if len(content) > MAX_NEW_CONTENT_CHARS:
        raise ValueError("new_content exceeds typed-clause size limit")
    if "\n\n" in content or content.count("\n") > 1:
        raise ValueError("new_content must be one atomic typed clause, not free prose")
    if operation == MutationOp.REPLACE_TYPED_CLAUSE and not DSL_BODY_PATTERN.match(content.strip()):
        raise ValueError("replacement content does not match typed DSL shape")


def parse_mutation_proposal(
    response: str | Mapping[str, Any],
    *,
    genome: SkillGenome,
    allowed_episode_ids: set[str],
    model: str,
    request_hash: str,
) -> MutationProposal:
    """Parse and validate provider output into atomic mutations."""

    if model not in ALLOWED_MODELS:
        raise ValueError(f"model is not authorized for BCCSE v4: {model}")
    raw = _response_object(response)
    hits = _firewall(raw)
    if hits:
        raise ValueError(f"provider output violates leakage firewall: {hits}")
    reflection = str(raw.get("reflection") or "").strip()
    if not reflection:
        raise ValueError("reflective mutation requires a non-empty reflection")
    rows = raw.get("mutations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ATOMIC_MUTATIONS:
        raise ValueError("provider must propose one or two atomic mutations")

    proposed: list[ProposedMutation] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"mutation {index} must be an object")
        allowed_fields = {
            "target_module", "operation", "target_clause_id", "new_content",
            "rationale", "support_episode_ids",
        }
        extras = set(row) - allowed_fields
        if extras:
            raise ValueError(f"mutation {index} has unregistered fields: {sorted(extras)}")
        target_module = str(row.get("target_module") or "")
        if target_module not in MUTABLE_MODULES:
            raise ValueError(f"mutation targets immutable or unknown module: {target_module}")
        operation = MutationOp(str(row.get("operation") or ""))
        content = None if row.get("new_content") is None else str(row["new_content"]).strip()
        _validate_typed_content(operation, content)
        support_ids = tuple(str(item) for item in row.get("support_episode_ids") or ())
        if not support_ids or not set(support_ids) <= allowed_episode_ids:
            raise ValueError("each mutation must cite only supplied failure episode ids")
        mutation = AtomicMutation(
            mutation_id=f"mutation_{uuid.uuid4().hex[:16]}",
            target_module=target_module,
            operation=operation,
            target_clause_id=None if row.get("target_clause_id") is None else str(row["target_clause_id"]),
            new_content=content,
            rationale=str(row.get("rationale") or "").strip(),
        )
        valid, reason = validate_mutation(mutation, genome)
        if not valid:
            raise ValueError(f"invalid atomic mutation {index}: {reason}")
        proposed.append(ProposedMutation(mutation=mutation, support_episode_ids=support_ids))

    payload_hash = _stable_hash(raw)
    return MutationProposal(
        model=model,
        reflection=reflection,
        proposed=tuple(proposed),
        provider_payload_hash=payload_hash,
        request_hash=request_hash,
    )


def propose_mutations(
    provider: MutationProvider,
    *,
    model: str,
    genome: SkillGenome,
    episodes: Sequence[FailureEpisode],
    generation: int,
    proposal_index: int = 0,
) -> MutationProposal:
    """Run one authorized reflective mutation call and validate its output."""

    if model not in ALLOWED_MODELS:
        raise ValueError(f"only DeepSeek-V4-Flash/Pro are authorized, got {model}")
    request = build_reflective_request(
        genome,
        episodes,
        generation=generation,
        proposal_index=proposal_index,
    )
    request_hash = hashlib.sha256(request.encode("utf-8")).hexdigest()
    response = provider.complete(
        model=model,
        system=REFLECTIVE_MUTATION_SYSTEM,
        user=request,
        response_format={"type": "json_object"},
    )
    return parse_mutation_proposal(
        response,
        genome=genome,
        allowed_episode_ids={item.episode_id for item in episodes},
        model=model,
        request_hash=request_hash,
    )
