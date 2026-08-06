"""SEHD v6 — Meta skill state and audited meta edits (P7).

The Meta layer reviews how the gate itself is evolving and guides better
skill/policy evolution.  It is deliberately boxed in (§13.4): a MetaEdit can
only add/remove closed-vocabulary guidance tokens, enable/disable
closed-vocabulary ranking preferences, or switch the gate profile.  It can
NEVER write a free-text gate rule, set an arbitrary numeric threshold, touch
the hard gate, change a detector verdict, create an operator, or directly
mint a PolicyItem.

``MetaSkillState`` carries a stable ``content_hash`` and lineage so a Meta
configuration is replay-auditable like any other genome.  Schema:
``sehd.meta_skill_state.v1``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

SCHEMA_VERSION = "sehd.meta_skill_state.v1"

# ============================================================
# Closed vocabularies (§13.5 / §13.6)
# ============================================================

GUIDANCE_TOKENS = frozenset({
    "bind_action_to_owned_output",
    "prefer_observable_discriminator",
    "reuse_effective_action_shape",
    "avoid_unexecuted_action_shape",
    "avoid_downstream_invariant_surface",
    "require_independent_anchor",
    "prefer_less_destructive_policy",
    "seek_distinct_candidate_direction",
    "prefer_registered_runtime_route",
    "avoid_pending_registry_dependency",
})

RANKING_PREFERENCES = frozenset({
    "owned_output_match",
    "action_shape_previously_effective",
    "action_shape_previously_no_effect",
    "observable_field_available",
    "downstream_edge_reachable",
    "independent_anchor_available",
    "destructive_scope",
    "duplicate_risk",
    "registry_stability",
    "cost_efficiency",
})

# Gate profiles (§13.10).  The hard gate is fixed under every profile; a
# profile may only reinterpret quality evidence, never the hard gate.
PROFILE_CONSERVATIVE = "conservative"
PROFILE_STEADY = "steady"
GATE_PROFILES = frozenset({PROFILE_CONSERVATIVE, PROFILE_STEADY})

# MetaEdit operations (§13.4) — the closed first-version set.
OP_ADD_GUIDANCE = "add_guidance_token"
OP_REMOVE_GUIDANCE = "remove_guidance_token"
OP_ENABLE_RANKING = "enable_ranking_preference"
OP_DISABLE_RANKING = "disable_ranking_preference"
OP_SWITCH_PROFILE = "switch_gate_profile"
META_EDIT_OPERATIONS = frozenset({
    OP_ADD_GUIDANCE,
    OP_REMOVE_GUIDANCE,
    OP_ENABLE_RANKING,
    OP_DISABLE_RANKING,
    OP_SWITCH_PROFILE,
})


# ============================================================
# MetaEdit
# ============================================================

@dataclass(frozen=True)
class MetaEdit:
    """One audited meta-level edit.  ``target`` is a closed-vocabulary token,
    preference or profile name; never free text or a numeric threshold."""

    edit_id: str
    operation: str
    target: str
    rationale: str = ""
    source_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "operation": self.operation,
            "target": self.target,
            "rationale": self.rationale,
            "source_evidence": list(self.source_evidence),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MetaEdit":
        if not isinstance(value, Mapping):
            raise ValueError("meta edit must contain an object")
        return cls(
            edit_id=str(value.get("edit_id") or ""),
            operation=str(value.get("operation") or ""),
            target=str(value.get("target") or ""),
            rationale=str(value.get("rationale") or ""),
            source_evidence=tuple(
                str(v) for v in (value.get("source_evidence") or ())
            ),
        )


# ============================================================
# MetaSkillState
# ============================================================

@dataclass
class MetaSkillState:
    """The evolving Meta configuration: guidance tokens, ranking preferences
    and the active gate profile, with a stable identity and lineage."""

    state_id: str
    guidance_tokens: tuple[str, ...] = ()
    ranking_preferences: tuple[str, ...] = ()
    gate_profile: str = PROFILE_CONSERVATIVE
    generation: int = 0
    parent_hash: str | None = None
    edits_applied: list[MetaEdit] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Closed-vocabulary enforcement: an unknown token/preference/profile
        # can never enter the state (fail-closed).
        self.guidance_tokens = tuple(
            dict.fromkeys(
                token for token in self.guidance_tokens
                if token in GUIDANCE_TOKENS
            )
        )
        self.ranking_preferences = tuple(
            dict.fromkeys(
                pref for pref in self.ranking_preferences
                if pref in RANKING_PREFERENCES
            )
        )
        if self.gate_profile not in GATE_PROFILES:
            self.gate_profile = PROFILE_CONSERVATIVE

    @property
    def content_hash(self) -> str:
        # Identity covers the configuration only; the audit trail and
        # timestamps never enter it.
        state = {
            "schema_version": SCHEMA_VERSION,
            "guidance_tokens": sorted(self.guidance_tokens),
            "ranking_preferences": sorted(self.ranking_preferences),
            "gate_profile": self.gate_profile,
        }
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "state_id": self.state_id,
            "guidance_tokens": list(self.guidance_tokens),
            "ranking_preferences": list(self.ranking_preferences),
            "gate_profile": self.gate_profile,
            "generation": self.generation,
            "parent_hash": self.parent_hash,
            "content_hash": self.content_hash,
            "edits_applied": [edit.to_dict() for edit in self.edits_applied],
            "audit": [dict(row) for row in self.audit],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MetaSkillState":
        if not isinstance(value, Mapping):
            raise ValueError("meta skill state must contain an object")
        if str(value.get("schema_version") or "") != SCHEMA_VERSION:
            raise ValueError(
                f"unknown meta skill state schema_version: "
                f"{value.get('schema_version')!r}"
            )
        return cls(
            state_id=str(value.get("state_id") or ""),
            guidance_tokens=tuple(
                str(v) for v in (value.get("guidance_tokens") or ())
            ),
            ranking_preferences=tuple(
                str(v) for v in (value.get("ranking_preferences") or ())
            ),
            gate_profile=str(value.get("gate_profile") or PROFILE_CONSERVATIVE),
            generation=int(value.get("generation") or 0),
            parent_hash=value.get("parent_hash"),
            edits_applied=[
                MetaEdit.from_dict(row)
                for row in (value.get("edits_applied") or ())
                if isinstance(row, Mapping)
            ],
            audit=[
                dict(row)
                for row in (value.get("audit") or ())
                if isinstance(row, Mapping)
            ],
        )

    def clone(self) -> "MetaSkillState":
        return MetaSkillState.from_dict(self.to_dict())


# ============================================================
# Edit validation and application (immutable lineage)
# ============================================================

def validate_meta_edit(edit: MetaEdit, state: MetaSkillState) -> tuple[bool, str]:
    """Validate one meta edit against the closed vocabularies.  Fail-closed."""
    if edit.operation not in META_EDIT_OPERATIONS:
        return False, f"invalid_operation:{edit.operation}"
    if edit.operation in (OP_ADD_GUIDANCE, OP_REMOVE_GUIDANCE):
        if edit.target not in GUIDANCE_TOKENS:
            return False, f"unknown_guidance_token:{edit.target}"
        if edit.operation == OP_ADD_GUIDANCE and edit.target in state.guidance_tokens:
            return False, "duplicate_guidance_token"
        if edit.operation == OP_REMOVE_GUIDANCE and edit.target not in state.guidance_tokens:
            return False, "guidance_token_absent"
    elif edit.operation in (OP_ENABLE_RANKING, OP_DISABLE_RANKING):
        if edit.target not in RANKING_PREFERENCES:
            return False, f"unknown_ranking_preference:{edit.target}"
        if edit.operation == OP_ENABLE_RANKING and edit.target in state.ranking_preferences:
            return False, "duplicate_ranking_preference"
        if edit.operation == OP_DISABLE_RANKING and edit.target not in state.ranking_preferences:
            return False, "ranking_preference_absent"
    elif edit.operation == OP_SWITCH_PROFILE:
        if edit.target not in GATE_PROFILES:
            return False, f"unknown_gate_profile:{edit.target}"
        if edit.target == state.gate_profile:
            return False, "profile_unchanged"
    return True, ""


def apply_meta_edit(state: MetaSkillState, edit: MetaEdit) -> MetaSkillState:
    """Apply one validated meta edit, returning a NEW state (immutable)."""
    valid, reason = validate_meta_edit(edit, state)
    if not valid:
        raise ValueError(f"Invalid meta edit: {reason}")

    child = state.clone()
    child.generation = state.generation + 1
    child.parent_hash = state.content_hash

    if edit.operation == OP_ADD_GUIDANCE:
        child.guidance_tokens = tuple(
            dict.fromkeys([*child.guidance_tokens, edit.target])
        )
    elif edit.operation == OP_REMOVE_GUIDANCE:
        child.guidance_tokens = tuple(
            token for token in child.guidance_tokens if token != edit.target
        )
    elif edit.operation == OP_ENABLE_RANKING:
        child.ranking_preferences = tuple(
            dict.fromkeys([*child.ranking_preferences, edit.target])
        )
    elif edit.operation == OP_DISABLE_RANKING:
        child.ranking_preferences = tuple(
            pref for pref in child.ranking_preferences if pref != edit.target
        )
    elif edit.operation == OP_SWITCH_PROFILE:
        child.gate_profile = edit.target

    child.edits_applied = state.edits_applied + [edit]
    child.audit = state.audit + [{
        "edit_id": edit.edit_id,
        "operation": edit.operation,
        "target": edit.target,
        "generation": child.generation,
        "before_hash": state.content_hash,
        "after_hash": child.content_hash,
    }]
    child.state_id = f"meta_skill_g{child.generation}_{uuid.uuid4().hex[:8]}"
    return child


def create_baseline_meta_skill() -> MetaSkillState:
    """The conservative baseline Meta state: no guidance, no ranking
    overrides, conservative gate profile."""
    return MetaSkillState(
        state_id="meta_skill_baseline_v1",
        gate_profile=PROFILE_CONSERVATIVE,
    )
