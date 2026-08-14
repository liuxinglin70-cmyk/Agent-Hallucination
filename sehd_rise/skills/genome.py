"""L1 Skill Genome with natural language rules and typed slots.

The genome replaces the earlier typed-clause structure with natural language
rules that the model can generate directly and the deterministic post-court
executor can compile. Each module carries a list of behavioral rules plus a
parallel rule_meta list tracking per-rule bookkeeping (is_baseline,
source_case_id, fitness_status, direction, typed rule_slots, action_spec,
condition_spec, binding_proof, repair_edge). Rule TEXT and module activation participate in
genome identity; rule_meta is post-commit bookkeeping written after batch
commit, so it never changes ``content_hash`` (a snapshot would otherwise
change identity after every commit and break lineage replay and cache keys).
Rule meta stays serialized in ``to_dict``; it is simply excluded from
identity.

Executable fields (typed condition, ActionSpec, ConditionSpec) do
participate in a separate ``execution_hash`` because they change post-court
behavior; fitness, provenance and promotion status remain excluded.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from sehd_rise.skills.action import (
    ActionSpec,
    compile_action_spec,
    sanitize_action_spec,
)
from sehd_rise.skills.binding import (
    BINDING_BOUND,
    Binding,
    sanitize_binding_proof,
    validate_binding_contract,
)
from sehd_rise.skills.rule import (
    RuleSpec,
    render_rule,
    sanitize_condition_spec,
    sanitize_route_guidance,
    validate_condition_spec,
    validate_condition_spec_for_domain,
    validate_rule_spec,
)
from sehd_rise.skills.vocabulary.contracts import (
    ALLOWED_VERDICTS,
    ERROR_CHECK_FIELDS,
    FINAL_VERDICT_RE,
    FORBIDDEN_CONTENT_MARKERS,
    INSTANCE_ID_RE,
    LOCAL_NEGATION_MODULES,
    LOCAL_NEGATION_RE,
    MODULE_IDS,
    MODULE_STAGE,
    semantic_overlap,
    validate_feature_description,
)

MODULE_DISPLAY_NAMES: dict[str, str] = {
    "candidate_trigger": "候选发现",
    "evidence_obligation": "证据义务",
    "candidate_to_verifier_route": "验证路由",
    "atomic_verifier_instruction": "验证指令",
    "causal_link_test": "因果测试",
    "earliest_origin_tiebreak": "根源裁决",
}

# Per-module capacity (not uniform — based on expected traffic). Core modules
# hold more evolved rules without premature capacity rejection; auxiliary
# modules keep smaller capacity to prevent bloat.
MODULE_CAPACITY: dict[str, int] = {
    "candidate_trigger": 6,
    "evidence_obligation": 10,
    "candidate_to_verifier_route": 3,
    "atomic_verifier_instruction": 6,
    "causal_link_test": 6,
    "earliest_origin_tiebreak": 4,
}


# ============================================================
# Fitness status and verdict permissions
# ============================================================

FITNESS_STATUS_NONE = "none"
FITNESS_STATUS_WEAK = "weak"
FITNESS_STATUS_STRONG = "strong"

# Tiered semantic deduplication thresholds. Below DEDUP_LOW_THRESHOLD a new
# rule is sufficiently distinct; in
# [DEDUP_LOW_THRESHOLD, DEDUP_HIGH_THRESHOLD) the structural gate returns
# "merge_required" so the runner routes to the LLM merger; at or above
# DEDUP_HIGH_THRESHOLD the edit is rejected as a near-duplicate.
DEDUP_LOW_THRESHOLD = 0.70
DEDUP_HIGH_THRESHOLD = 0.95


# ============================================================
# Operations
# ============================================================

class SkillOp(str, Enum):
    """Allowed operations on skill modules."""
    ADD_RULE = "add_rule"
    MODIFY_RULE = "modify_rule"
    REMOVE_RULE = "remove_rule"


def sanitize_feature_description(content: str) -> str:
    """Return a persistence-safe feature description or an empty sentinel."""
    normalized = " ".join(str(content or "").split())[:500]
    valid, _ = validate_feature_description(normalized)
    return normalized if valid else ""


def validate_expected_effect(content: str) -> tuple[bool, str]:
    """Validate persisted counterfactual telemetry against prompt pollution."""
    normalized = " ".join(str(content or "").split())
    if len(normalized) > 240:
        return False, "expected_effect_too_long"
    valid, reason = validate_feature_description(normalized)
    if not valid:
        return False, reason.replace("feature_", "expected_effect_", 1)
    return True, ""


def sanitize_expected_effect(content: str) -> str:
    normalized = " ".join(str(content or "").split())[:240]
    valid, _ = validate_expected_effect(normalized)
    return normalized if valid else ""


# ============================================================
# Structured expected-effect contract
# ============================================================
# The source-replay delta is compared token-by-token, so the expected
# effect is persisted as structured provenance — which observable detector
# field the edit is supposed to move, in which direction, on which case
# subset, under what trigger condition — instead of a prose sentence that
# invites substring guessing.

OBSERVABLE_FIELDS: tuple[str, ...] = (
    "candidate_count",
    "prosecution_coverage",
    "defense_coverage",
    "prosecution_certificates",
    "defense_certificates",
    # Claim-level surviving-carrier set (content-fingerprint identity;
    # rename-invariant). The observable of the current certificate-targeted
    # operation (withhold_certificate).
    "surviving_certificates",
    "quarantined_certificates",
    "causal_field_coverage",
    "responsible_step",
    "localization_status",
    "target_local_score",
    "state",
    "trigger",
)

EXPECTED_DIRECTIONS: tuple[str, ...] = (
    "increase",
    "decrease",
    "added",
    "removed",
    "shift",
    "opened",
    "closed",
)

EXPECTED_SCOPES: tuple[str, ...] = (
    "all_cases",
    "positive_cases",
    "negative_cases",
    "quarantined_cases",
    "unresolved_cases",
)

# ``observable_process_delta`` reports movement in the past tense
# ("prosecution_coverage:increased"), while a contract declares an intent in
# the base form ("increase"). Both spellings therefore reach this contract
# from the same model in the same call, and rejecting the inflected one is a
# vocabulary accident rather than an invalid prediction.
_DIRECTION_ALIASES: dict[str, str] = {
    "increased": "increase",
    "increases": "increase",
    "decreased": "decrease",
    "decreases": "decrease",
    "add": "added",
    "adds": "added",
    "remove": "removed",
    "removes": "removed",
    "shifted": "shift",
    "shifts": "shift",
    "open": "opened",
    "opens": "opened",
    "close": "closed",
    "closes": "closed",
}


def normalize_expected_direction(value: Any) -> str:
    """Reduce a declared direction to the canonical EXPECTED_DIRECTIONS token."""
    text = str(value or "").strip().lower()
    return _DIRECTION_ALIASES.get(text, text)


def validate_expected_effect_contract(content: Any) -> tuple[bool, str]:
    """Validate the structured counterfactual contract.

    Empty contracts are allowed (an earlier edit may not have produced one). When
    present, the contract must carry an actionable observable_field, a
    recognised direction, and a recognised scope. The trigger_condition
    is sanity-checked for prompt-pollution / case-id leakage using the
    same rules as feature descriptions.
    """
    if content is None:
        return True, ""
    if not isinstance(content, dict):
        return False, "expected_effect_contract_not_object"
    if not content:
        return True, ""
    observable = str(content.get("observable_field") or "")
    if observable not in OBSERVABLE_FIELDS:
        return False, f"expected_effect_contract_invalid_field:{observable[:40]}"
    direction = normalize_expected_direction(content.get("expected_direction"))
    if direction not in EXPECTED_DIRECTIONS:
        return False, f"expected_effect_contract_invalid_direction:{direction[:40]}"
    scope = str(content.get("expected_scope") or "")
    if scope not in EXPECTED_SCOPES:
        return False, f"expected_effect_contract_invalid_scope:{scope[:40]}"
    trigger = str(content.get("trigger_condition") or "")
    if trigger:
        valid, reason = validate_feature_description(trigger)
        if not valid:
            return False, reason.replace("feature_", "expected_effect_contract_", 1)
    return True, ""


def sanitize_expected_effect_contract(content: Any) -> dict[str, Any]:
    """Return a persistence-safe structured contract or an empty dict."""
    if not isinstance(content, dict):
        return {}
    sanitized: dict[str, Any] = {}
    observable = str(content.get("observable_field") or "")
    if observable in OBSERVABLE_FIELDS:
        sanitized["observable_field"] = observable
    direction = normalize_expected_direction(content.get("expected_direction"))
    if direction in EXPECTED_DIRECTIONS:
        sanitized["expected_direction"] = direction
    scope = str(content.get("expected_scope") or "")
    if scope in EXPECTED_SCOPES:
        sanitized["expected_scope"] = scope
    trigger = " ".join(str(content.get("trigger_condition") or "").split())[:240]
    if trigger:
        valid, _ = validate_feature_description(trigger)
        if valid:
            sanitized["trigger_condition"] = trigger
    valid, _ = validate_expected_effect_contract(sanitized)
    return sanitized if valid else {}


def normalize_type_confidence(value: Any) -> float:
    """Bound untrusted serialized confidence without failing restoration."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(confidence):
        return 0.0
    return max(0.0, min(1.0, confidence))


def validate_runtime_rule(
    content: str,
    target_module: str,
    *,
    condition_scope: str | None = None,
) -> tuple[bool, str]:
    """Validate the non-negotiable runtime-observability rule contract.

    ``condition_scope`` is the typed condition slot when the edit carries a
    slot contract. Under the slot contract the diagnostic-field markers
    (coverage/CRS/trigger/projection identifiers) are checked against the
    condition only: they describe signals the provider cannot observe when
    deciding whether to fire, while the action slot may legitimately name
    the target module's own charter-owned output surface. State
    manipulation and label-leakage markers stay whole-text in both modes.
    """
    normalized = content.strip()
    lowered = normalized.lower()
    if len(normalized) < 10:
        return False, "content_too_short"
    has_if_then = (
        (
            normalized.startswith(("当", "如果"))
            and ("时" in normalized or "，" in normalized or "," in normalized)
        )
        or (
            lowered.startswith(("when ", "if "))
            and ("," in normalized or "，" in normalized)
        )
    )
    if not has_if_then:
        return False, "not_if_then_instruction"
    # A numbered list marker is (0|1|2|3) followed by a list punctuation;
    # a decimal like "2.5" is a value, not a step marker.
    if re.search(r"(?:^|[\s（(])(?:0|1|2|3)[.)、](?!\d)|首先|其次|然后|最后", normalized):
        return False, "multi_step_pseudocode"
    # Parse the condition/action boundary fail-closed: a form whose boundary
    # cannot be determined unambiguously (no comma, no terminal ``时``) is
    # rejected instead of silently truncating on an in-word ``时`` (e.g.
    # ``当时间戳缺失时保留证据`` must not yield condition=``当``).
    condition = normalized
    if lowered.startswith(("when ", "if ")):
        condition = re.split(r"[,，]", normalized, maxsplit=1)[0]
    elif normalized.startswith(("当", "如果")):
        boundary = re.search(r"时(?=[，,。；;：:\s]|$)", normalized)
        if boundary:
            condition = normalized[: boundary.start() + 1]
        else:
            comma = re.search(r"[,，]", normalized)
            if not comma:
                return False, "not_if_then_instruction"
            condition = normalized[: comma.start()]
    conjunctions = re.findall(
        r"且|并且|以及|或|\band\b|\bor\b",
        condition.lower(),
    )
    if len(conjunctions) > 1:
        return False, "condition_overload"
    action = normalized[len(condition):]
    if any(token in action for token in ("；", ";", "然后", "同时还", "并随后")):
        return False, "multiple_actions"

    # Diagnostic-field identifiers are unobservable to the firing provider;
    # under the slot contract they are checked against the condition slot
    # only, so an action that names the module's own output surface is not
    # rejected for mentioning the field it controls.
    _diagnostic_markers = (
        "crs",
        "coverage_state",
        "trigger_source",
        "prosecution_coverage",
        "defense_coverage",
        "proof_projection",
        "覆盖率",
        "控方覆盖",
        "辩方覆盖",
        "触发源",
        "五维评分",
        "证明投影",
    )
    diagnostic_scope = lowered if condition_scope is None else condition_scope.lower()
    for marker in _diagnostic_markers:
        if marker in diagnostic_scope:
            return False, f"forbidden_runtime_marker:{marker}"
    # State manipulation, label leakage and priority declarations are never
    # legal in any slot, so these stay whole-text in every mode.
    _state_leak_markers = (
        "boundary_path_state",
        "candidate_path_state",
        "状态设为",
        "状态设置为",
        "强制设置",
        "gold",
        "ground truth",
        "真值",
        "金标签",
        "标注标签",
        "正确标签",
        "真实标签",
        "答案标签",
        "本案例",
        "这个案例",
        "最高优先级",
        "优先于任何其他",
    )
    for marker in _state_leak_markers:
        if marker in lowered:
            return False, f"forbidden_runtime_marker:{marker}"
    if (
        re.search(r"\b[a-z][a-z_]+/\d+\b|https?://", lowered)
        or INSTANCE_ID_RE.search(lowered)
    ):
        return False, "case_specific_pattern"
    # --- Verdict permission (layered) ---
    # Detect final verdict emission (clean/error/unresolved).  The shared
    # FINAL_VERDICT_RE / LOCAL_NEGATION_RE vocabulary is the single source
    # of truth for both the free-text gate here and the slot-contract gate
    # in the rule module, so a phrasing cannot pass one gate and fail the
    # other by rule form.
    verdict_search = FINAL_VERDICT_RE.search(lowered)
    if verdict_search:
        verdict = next(g for g in verdict_search.groups() if g)
        allowed = ALLOWED_VERDICTS.get(target_module, frozenset())
        if verdict not in allowed:
            return False, f"verdict_not_allowed:{verdict}_for_{target_module}"
    else:
        # Detect local negation (weaker than final verdict)
        # e.g. "causal link does not hold" — weakens prosecution
        if LOCAL_NEGATION_RE.search(normalized) and target_module not in LOCAL_NEGATION_MODULES:
            return False, f"local_negation_not_allowed_for_{target_module}"
    return True, ""


# ============================================================
# Core Data Structures
# ============================================================

_RULE_SLOT_LIMITS = {
    "condition": 300,
    "action": 300,
    "evidence_anchor": 240,
}


def _sanitize_rule_slots(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: " ".join(str(value.get(key) or "").split())[:limit]
        for key, limit in _RULE_SLOT_LIMITS.items()
        if str(value.get(key) or "").strip()
    }


def _sanitize_repair_edge(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = (
        "edge_id", "producer_route", "output_object", "field", "consumer",
        "observed", "required", "owner_module", "source_artifact_id",
        "blocking_predicate",
    )
    return {
        key: str(value.get(key) or "")
        for key in allowed
        if str(value.get(key) or "")
    }


@dataclass
class SkillModule:
    """A skill module containing natural language behavioral rules.

    rule_meta is a parallel list to rules, tracking per-rule metadata:
      - is_baseline: True for immutable baseline rules
      - source_case_id: which case produced this evolved rule
      - fitness_status: none / weak / strong (protected modify support)
      - direction: clean / error / unresolved (rule action direction)
      - created_at_generation: genome generation when rule was added
    """
    module_id: str
    active: bool = True
    rules: list[str] = field(default_factory=list)
    rule_meta: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        while len(self.rule_meta) < len(self.rules):
            self.rule_meta.append({})
        self.rule_meta = self.rule_meta[: len(self.rules)]
        for meta in self.rule_meta:
            self._normalize_meta(meta)

    @staticmethod
    def _normalize_meta(meta: dict[str, Any]) -> None:
        if "feature_description" in meta:
            meta["feature_description"] = sanitize_feature_description(
                str(meta.get("feature_description") or "")
            )
        if "type_confidence" in meta:
            meta["type_confidence"] = normalize_type_confidence(
                meta.get("type_confidence")
            )
        if "rule_slots" in meta:
            meta["rule_slots"] = _sanitize_rule_slots(meta.get("rule_slots"))
        if "action_spec" in meta:
            meta["action_spec"] = sanitize_action_spec(meta.get("action_spec"))
        if "condition_spec" in meta:
            meta["condition_spec"] = sanitize_condition_spec(
                meta.get("condition_spec")
            )
        if "repair_edge" in meta:
            meta["repair_edge"] = _sanitize_repair_edge(meta.get("repair_edge"))
        if "route_guidance" in meta:
            meta["route_guidance"] = sanitize_route_guidance(
                meta.get("route_guidance")
            )

    @property
    def capacity(self) -> int:
        return MODULE_CAPACITY.get(self.module_id, 4)

    @property
    def is_full(self) -> bool:
        return len(self.rules) >= self.capacity

    def get_rule_meta(self, index: int) -> dict[str, Any]:
        if 0 <= index < len(self.rule_meta):
            return dict(self.rule_meta[index])
        return {}

    def set_rule_meta(self, index: int, meta: dict[str, Any]) -> None:
        while len(self.rule_meta) <= index:
            self.rule_meta.append({})
        normalized = dict(meta)
        self._normalize_meta(normalized)
        self.rule_meta[index] = normalized

    def is_rule_protected(self, index: int) -> bool:
        """Protected if baseline or passed fitness (weak/strong)."""
        meta = self.get_rule_meta(index)
        if meta.get("is_baseline"):
            return True
        return meta.get("fitness_status") in (FITNESS_STATUS_WEAK, FITNESS_STATUS_STRONG)

    def to_dict(self) -> dict[str, Any]:
        safe_meta: list[dict[str, Any]] = []
        for value in self.rule_meta:
            meta = dict(value)
            self._normalize_meta(meta)
            safe_meta.append(meta)
        return {
            "module_id": self.module_id,
            "active": self.active,
            "rules": list(self.rules),
            "rule_meta": safe_meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillModule":
        rules = data.get("rules", [])
        rule_meta = data.get("rule_meta", [])
        while len(rule_meta) < len(rules):
            rule_meta.append({})
        rule_meta = rule_meta[: len(rules)]
        return cls(
            module_id=data["module_id"],
            active=data.get("active", True),
            rules=rules,
            rule_meta=rule_meta,
        )


@dataclass
class SkillEdit:
    """A single edit to the skill genome."""
    edit_id: str
    target_module: str
    operation: str  # add_rule / modify_rule / remove_rule
    rule_index: int | None = None  # for modify/remove: which rule
    new_content: str = ""  # for add/modify: the rule text
    rationale: str = ""  # the model's reasoning
    source_case_id: str = ""
    # Historical audit field; merge edits are no longer auto-generated and
    # this flag never bypasses validation when old checkpoints are restored.
    is_merge: bool = False
    feature_description: str = ""
    hallucination_type: str = "unknown"
    secondary_hallucination_type: str = ""
    evolution_direction: str = "process_repair"
    type_confidence: float = 0.0
    routing_allowed_modules: tuple[str, ...] = ()
    # A short counterfactual contract supplied by the reflection engine. It is
    # telemetry, not a verdict: source replay remains authoritative.
    expected_effect: str = ""
    # Structured counterfactual contract; when populated, the audit function
    # compares this against the source-replay delta token-by-token instead of
    # doing substring matching on `expected_effect`.
    expected_effect_contract: dict[str, Any] = field(default_factory=dict)
    rule_slots: dict[str, str] = field(default_factory=dict)
    action_spec: dict[str, Any] = field(default_factory=dict)
    condition_spec: dict[str, Any] = field(default_factory=dict)
    binding_proof: dict[str, Any] = field(default_factory=dict)
    repair_edge: dict[str, Any] = field(default_factory=dict)
    # Route-guidance semantic payload (design D1): a route-guidance skill has
    # no deterministic backend, so its intervention is a compiled, firewall-
    # checked guidance block for the owner's producer route. ``mechanism`` and
    # ``expected_local_effect`` are audit fields; ``route_guidance`` is the
    # text the injection layer compiles into the provider route.
    route_guidance: str = ""
    failure_mechanism: str = ""
    expected_local_effect: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "target_module": self.target_module,
            "operation": (
                self.operation.value
                if isinstance(self.operation, SkillOp)
                else str(self.operation)
            ),
            "rule_index": self.rule_index,
            "new_content": self.new_content,
            "rationale": self.rationale,
            "source_case_id": self.source_case_id,
            "is_merge": self.is_merge,
            "feature_description": sanitize_feature_description(
                self.feature_description
            ),
            "hallucination_type": self.hallucination_type,
            "secondary_hallucination_type": self.secondary_hallucination_type,
            "evolution_direction": self.evolution_direction,
            "type_confidence": normalize_type_confidence(self.type_confidence),
            "routing_allowed_modules": [
                value
                for value in self.routing_allowed_modules
                if value in MODULE_IDS
            ],
            "expected_effect": sanitize_expected_effect(self.expected_effect),
            "expected_effect_contract": sanitize_expected_effect_contract(
                self.expected_effect_contract
            ),
            "rule_slots": _sanitize_rule_slots(self.rule_slots),
            "action_spec": sanitize_action_spec(self.action_spec),
            "condition_spec": sanitize_condition_spec(self.condition_spec),
            "binding_proof": sanitize_binding_proof(self.binding_proof),
            "repair_edge": _sanitize_repair_edge(self.repair_edge),
            "route_guidance": sanitize_route_guidance(self.route_guidance),
            "failure_mechanism": self.failure_mechanism[:500],
            "expected_local_effect": self.expected_local_effect[:240],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillEdit":
        return cls(
            edit_id=data["edit_id"],
            target_module=data["target_module"],
            operation=data["operation"],
            rule_index=data.get("rule_index"),
            new_content=data.get("new_content", ""),
            rationale=data.get("rationale", ""),
            source_case_id=data.get("source_case_id", ""),
            is_merge=bool(data.get("is_merge", False)),
            feature_description=sanitize_feature_description(
                str(data.get("feature_description") or "")
            ),
            hallucination_type=str(data.get("hallucination_type") or "unknown"),
            secondary_hallucination_type=str(
                data.get("secondary_hallucination_type") or ""
            ),
            evolution_direction=str(
                data.get("evolution_direction") or "process_repair"
            ),
            type_confidence=normalize_type_confidence(
                data.get("type_confidence")
            ),
            routing_allowed_modules=tuple(
                str(value)
                for value in (data.get("routing_allowed_modules") or ())
                if str(value) in MODULE_IDS
            ),
            expected_effect=sanitize_expected_effect(
                str(data.get("expected_effect") or "")
            ),
            expected_effect_contract=sanitize_expected_effect_contract(
                data.get("expected_effect_contract")
            ),
            rule_slots=_sanitize_rule_slots(data.get("rule_slots")),
            action_spec=sanitize_action_spec(data.get("action_spec")),
            condition_spec=sanitize_condition_spec(data.get("condition_spec")),
            binding_proof=sanitize_binding_proof(data.get("binding_proof")),
            repair_edge=_sanitize_repair_edge(data.get("repair_edge")),
            route_guidance=sanitize_route_guidance(data.get("route_guidance")),
            failure_mechanism=str(data.get("failure_mechanism") or "")[:500],
            expected_local_effect=str(
                data.get("expected_local_effect") or ""
            )[:240],
        )


@dataclass
class SkillGenome:
    """Complete skill genome: 6 modules with natural language rules."""
    genome_id: str
    modules: dict[str, SkillModule]
    parent_hash: str | None = None
    generation: int = 0
    edits_applied: list[SkillEdit] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for mid in MODULE_IDS:
            if mid not in self.modules:
                self.modules[mid] = SkillModule(module_id=mid, active=True)

    @property
    def content_hash(self) -> str:
        """Content identity of the genome: module activation and rule TEXT.

        ``rule_meta`` (fitness_status, direction, provenance) is bookkeeping
        written AFTER batch commit; including it made the same logical genome
        change identity after every commit (snapshot name drift, cache-key
        churn, and lineage-replay mismatch). Rule meta remains serialized in
        ``to_dict``; it is simply excluded from identity.
        """
        state = {
            "modules": {
                mid: {
                    "active": self.modules[mid].active,
                    "rules": list(self.modules[mid].rules),
                }
                for mid in MODULE_IDS
            }
        }
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def execution_hash(self) -> str:
        """Identity of every genome field that can change post-court behavior.

        ``content_hash`` stays the stable text-lineage id so historical
        checkpoints remain readable when bookkeeping is updated. Runtime
        compilation also consumes the typed rule condition, the ActionSpec
        and the route-guidance text stored in ``rule_meta``; those executable
        fields must therefore participate in a separate behavior identity.
        Fitness, provenance and promotion status remain excluded.
        """
        state = {
            "schema_version": "sehd.skill_execution_identity.v3",
            "modules": {
                mid: {
                    "active": self.modules[mid].active,
                    "rules": list(self.modules[mid].rules),
                    "executable_rule_meta": [
                        {
                            "rule_slots": _sanitize_rule_slots(
                                self.modules[mid].get_rule_meta(index).get(
                                    "rule_slots"
                                )
                            ),
                            "action_spec": sanitize_action_spec(
                                self.modules[mid].get_rule_meta(index).get(
                                    "action_spec"
                                )
                            ),
                            "condition_spec": sanitize_condition_spec(
                                self.modules[mid].get_rule_meta(index).get(
                                    "condition_spec"
                                )
                            ),
                            # Route-guidance text changes provider-route
                            # behavior at runtime, so it is part of behavior
                            # identity; fitness/provenance bookkeeping stays
                            # excluded.
                            "route_guidance": sanitize_route_guidance(
                                self.modules[mid].get_rule_meta(index).get(
                                    "route_guidance"
                                )
                            ),
                        }
                        for index in range(len(self.modules[mid].rules))
                    ],
                }
                for mid in MODULE_IDS
            },
        }
        text = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def total_rules(self) -> int:
        return sum(len(m.rules) for m in self.modules.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "genome_id": self.genome_id,
            "modules": {mid: self.modules[mid].to_dict() for mid in MODULE_IDS},
            "parent_hash": self.parent_hash,
            "generation": self.generation,
            "edits_applied": [e.to_dict() for e in self.edits_applied],
            "content_hash": self.content_hash,
            "execution_hash": self.execution_hash,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillGenome":
        modules = {
            mid: SkillModule.from_dict(mdata)
            for mid, mdata in data.get("modules", {}).items()
        }
        return cls(
            genome_id=data["genome_id"],
            modules=modules,
            parent_hash=data.get("parent_hash"),
            generation=data.get("generation", 0),
            edits_applied=[SkillEdit.from_dict(e) for e in data.get("edits_applied", [])],
            metadata=data.get("metadata", {}),
        )

    def clone(self) -> "SkillGenome":
        return SkillGenome.from_dict(self.to_dict())


# ============================================================
# Edit Application
# ============================================================

def _validate_persisted_rule_slots(
    edit: SkillEdit,
) -> tuple[bool, str]:
    slots = _sanitize_rule_slots(edit.rule_slots)
    if not slots:
        return True, ""
    if set(slots) != set(_RULE_SLOT_LIMITS):
        return False, "rule_slots_incomplete"
    spec = RuleSpec(
        condition=slots["condition"],
        action=slots["action"],
        evidence_anchor=slots["evidence_anchor"],
        module_id=edit.target_module,
        direction=_detect_rule_direction(edit.new_content),
    )
    valid, reason = validate_rule_spec(spec)
    if not valid:
        return False, f"rule_slots:{reason}"
    if render_rule(spec) != edit.new_content:
        return False, "rule_slots_render_mismatch"
    persisted_action_spec = sanitize_action_spec(edit.action_spec)
    if not persisted_action_spec:
        return False, "action_spec_missing"
    expected_action_spec = compile_action_spec(
        edit.target_module, slots["action"],
    ).to_dict()
    if persisted_action_spec != expected_action_spec:
        return False, "action_spec_rule_slots_mismatch"
    return True, ""


def _validate_route_guidance_edit(edit: SkillEdit) -> tuple[bool, str]:
    """Validate a route-guidance edit: applicability + firewall-clean guidance.

    The applicability predicate is the label-free ``condition_spec``; the
    guidance text must pass the D1 authority firewall (no verdict, evidence,
    certificate construction, case/gold identity). Neither an action spec nor
    a binding proof exists for a provider-route intervention.
    """
    if not edit.condition_spec:
        return False, "route_guidance_condition_spec_missing"
    condition_valid, condition_reason = validate_condition_spec(
        edit.condition_spec
    )
    if not condition_valid:
        return False, condition_reason
    guidance = sanitize_route_guidance(edit.route_guidance)
    if not guidance:
        return False, "route_guidance_rejected_by_firewall"
    repair_edge = _sanitize_repair_edge(edit.repair_edge)
    if repair_edge:
        if not repair_edge.get("edge_id"):
            return False, "repair_edge_id_missing"
        edge_owner = str(repair_edge.get("owner_module") or "")
        if edge_owner and edge_owner != edit.target_module:
            return False, "repair_edge_owner_mismatch"
    return True, ""


def validate_edit(edit: SkillEdit, genome: SkillGenome) -> tuple[bool, str]:
    """Validate an edit against structural constraints."""
    if edit.target_module not in MODULE_IDS:
        return False, f"invalid_module: {edit.target_module}"
    operation_value = (
        edit.operation.value
        if isinstance(edit.operation, SkillOp)
        else str(edit.operation)
    )
    allowed_operations = {operation.value for operation in SkillOp}
    if operation_value not in allowed_operations:
        return False, f"invalid_operation: {edit.operation}"
    # The caller's ``edit.operation`` is intentionally NOT rewritten
    # here — mutating the passed-in object from a validation function is a
    # side effect that silently type-drifts SkillOp into str for every other
    # consumer of the same edit (audit recording etc.). ``apply_edit``
    # normalizes the operation on its own copy when it commits.

    module = genome.modules[edit.target_module]

    if operation_value in {SkillOp.ADD_RULE.value, SkillOp.MODIFY_RULE.value}:
        if edit.route_guidance:
            # Route-guidance edit (design D1/D2): no deterministic backend,
            # so the intervention is a firewall-checked guidance block plus a
            # label-free applicability predicate. Only the genuinely
            # inapplicable deterministic-backend proofs (rule slots, action
            # spec, binding proof, action↔domain/edge checks) are exempt; the
            # shared invariants below (feature/effect provenance, confidence,
            # routing provenance, forbidden content, capacity, dedup, and the
            # modify-side immutable/protected guards) apply unchanged.
            guidance_valid, guidance_reason = _validate_route_guidance_edit(edit)
            if not guidance_valid:
                return False, guidance_reason
            slots = {}
            slot_contract_active = False
        else:
            slots = _sanitize_rule_slots(edit.rule_slots)
            slot_contract_active = bool(
                slots and set(slots) == set(_RULE_SLOT_LIMITS)
            )
            slots_valid, slots_reason = _validate_persisted_rule_slots(edit)
            if not slots_valid:
                return False, slots_reason
            if edit.condition_spec:
                condition_valid, condition_reason = validate_condition_spec(
                    edit.condition_spec
                )
                if not condition_valid:
                    return False, condition_reason
            binding_proof = sanitize_binding_proof(edit.binding_proof)
            if not binding_proof:
                return False, "source_binding_proof_missing"
            persisted_action = sanitize_action_spec(edit.action_spec)
            if not persisted_action:
                return False, "action_spec_missing"
            action_contract = ActionSpec.from_dict(persisted_action)
            if edit.condition_spec:
                domain_ok, domain_reason = validate_condition_spec_for_domain(
                    edit.condition_spec, action_contract.input_binding_domain,
                )
                if not domain_ok:
                    return False, domain_reason
            proof_bindings = tuple(
                Binding.from_dict(item)
                for item in binding_proof.get("bindings") or ()
                if isinstance(item, Mapping)
            )
            binding_status, binding_reason = validate_binding_contract(
                proof_bindings,
                domain=action_contract.input_binding_domain,
                required_fields=action_contract.required_binding_fields,
                cardinality=action_contract.cardinality,
            )
            if binding_status != BINDING_BOUND:
                return False, f"source_binding_proof_invalid:{binding_reason}"
            repair_edge = _sanitize_repair_edge(edit.repair_edge)
            if repair_edge:
                if not repair_edge.get("edge_id"):
                    return False, "repair_edge_id_missing"
                edge_owner = str(repair_edge.get("owner_module") or "")
                if edge_owner and edge_owner != edit.target_module:
                    return False, "repair_edge_owner_mismatch"
                persisted_action = sanitize_action_spec(edit.action_spec)
                edge_field = str(repair_edge.get("field") or "")
                output_field = str(persisted_action.get("output_field") or "")
                repair_targets = {edge_field}
                if edit.target_module == "atomic_verifier_instruction":
                    repair_targets.update(ERROR_CHECK_FIELDS.get(edge_field, ()))
                if output_field not in repair_targets:
                    return False, "action_spec_repair_edge_mismatch"

    feature_valid, feature_reason = validate_feature_description(
        edit.feature_description
    )
    if not feature_valid:
        return False, feature_reason
    effect_valid, effect_reason = validate_expected_effect(edit.expected_effect)
    if not effect_valid:
        return False, effect_reason
    contract_valid, contract_reason = validate_expected_effect_contract(
        edit.expected_effect_contract
    )
    if not contract_valid:
        return False, contract_reason
    # The delta-token contract vocabulary cannot name route-local observable
    # postconditions; a route-guidance edit's observable contract is the
    # owner's RouteAddressability postcondition instead, so the structured
    # contract requirement applies only to deterministic-backend edits.
    if (
        not edit.route_guidance
        and edit.expected_effect.strip()
        and not edit.expected_effect_contract
    ):
        return False, "expected_effect_contract_missing"
    try:
        confidence = float(edit.type_confidence)
    except (TypeError, ValueError):
        return False, "invalid_type_confidence"
    if not 0.0 <= confidence <= 1.0:
        return False, "invalid_type_confidence"
    if any(module_id not in MODULE_IDS for module_id in edit.routing_allowed_modules):
        return False, "invalid_routing_provenance"
    if (
        edit.routing_allowed_modules
        and edit.target_module not in edit.routing_allowed_modules
    ):
        return False, "target_outside_routing_provenance"

    # Check forbidden content
    content_lower = edit.new_content.lower()
    for marker in FORBIDDEN_CONTENT_MARKERS:
        if marker in content_lower:
            return False, f"forbidden_content: {marker}"

    if edit.operation == SkillOp.ADD_RULE:
        if module.is_full:
            return False, f"module_full: {len(module.rules)} >= {module.capacity}"
        if not edit.route_guidance:
            # The runtime-rule contract is slot-render oriented; a guidance
            # edit's content boundary is the D1 firewall checked by
            # ``_validate_route_guidance_edit`` instead.
            valid_rule, rule_reason = validate_runtime_rule(
                edit.new_content,
                edit.target_module,
                condition_scope=(
                    slots.get("condition") if slot_contract_active else None
                ),
            )
            if not valid_rule:
                return False, rule_reason
        # Semantic duplicate check against existing rules (tiered)
        for existing_rule in module.rules:
            overlap = semantic_overlap(existing_rule, edit.new_content)
            if overlap >= DEDUP_HIGH_THRESHOLD:
                return False, (
                    f"duplicate_rule: semantic overlap {overlap:.2f} "
                    f">= {DEDUP_HIGH_THRESHOLD} with existing rule"
                )
            if overlap >= DEDUP_LOW_THRESHOLD:
                return False, (
                    f"merge_required: semantic overlap {overlap:.2f} "
                    f"in [{DEDUP_LOW_THRESHOLD},{DEDUP_HIGH_THRESHOLD}) "
                    f"with existing rule"
                )

    elif edit.operation == SkillOp.MODIFY_RULE:
        if edit.rule_index is None or edit.rule_index < 0:
            return False, "invalid_rule_index"
        if edit.rule_index >= len(module.rules):
            return False, (
                f"rule_index_out_of_range: {edit.rule_index} >= {len(module.rules)}"
            )
        baseline_rules = BASELINE_RULE_TEXT.get(edit.target_module, ())
        if module.rules and module.rules[edit.rule_index] in baseline_rules:
            return False, "immutable_baseline_rule"
        # Evolved rules that passed fitness (weak/strong) cannot be modified
        # by subsequent cases; use add + challenge instead.
        if module.is_rule_protected(edit.rule_index):
            meta = module.get_rule_meta(edit.rule_index)
            status = meta.get("fitness_status", "unknown")
            return False, f"protected_rule: fitness_status={status}"
        if not edit.route_guidance:
            valid_rule, rule_reason = validate_runtime_rule(
                edit.new_content,
                edit.target_module,
                condition_scope=(
                    slots.get("condition") if slot_contract_active else None
                ),
            )
            if not valid_rule:
                return False, rule_reason
        # A modification that reproduces the rule it replaces is rejected
        # here so the structural gate and apply_edit speak one contract:
        # apply_edit raises semantically_equivalent on the same overlap.
        self_overlap = semantic_overlap(
            module.rules[edit.rule_index], edit.new_content,
        )
        if self_overlap >= DEDUP_HIGH_THRESHOLD:
            return False, (
                f"semantically_equivalent: overlap {self_overlap:.2f} "
                f">= {DEDUP_HIGH_THRESHOLD} with the rule being modified"
            )
        for index, existing_rule in enumerate(module.rules):
            if index == edit.rule_index:
                continue
            overlap = semantic_overlap(existing_rule, edit.new_content)
            if overlap >= DEDUP_HIGH_THRESHOLD:
                return False, (
                    f"duplicate_rule: semantic overlap {overlap:.2f} "
                    f">= {DEDUP_HIGH_THRESHOLD} with another rule"
                )
            if overlap >= DEDUP_LOW_THRESHOLD:
                return False, (
                    f"merge_required: semantic overlap {overlap:.2f} "
                    f"in [{DEDUP_LOW_THRESHOLD},{DEDUP_HIGH_THRESHOLD}) "
                    f"with another rule"
                )

    elif edit.operation == SkillOp.REMOVE_RULE:
        if edit.rule_index is None or edit.rule_index < 0:
            return False, "invalid_rule_index"
        if edit.rule_index >= len(module.rules):
            return False, f"rule_index_out_of_range: {edit.rule_index} >= {len(module.rules)}"
        baseline_rules = BASELINE_RULE_TEXT.get(edit.target_module, ())
        if module.rules[edit.rule_index] in baseline_rules:
            return False, "immutable_baseline_rule"
        if module.is_rule_protected(edit.rule_index):
            meta = module.get_rule_meta(edit.rule_index)
            status = meta.get("fitness_status", "unknown")
            return False, f"protected_rule: fitness_status={status}"

    return True, ""


def _detect_rule_direction(rule_text: str) -> str:
    """Detect the action direction of a rule for direction-consistency checks.

    Also recognizes verdict-equivalent phrasings (treat/regard as ...) so
    rule direction metadata stays consistent with the gate's verdict
    permission layer.
    """
    lowered = rule_text.lower()
    verdict_re = (
        r"判(?:定为|为)?\s*{v}|直接输出\s*{v}|应判.*{v}|结论.*{v}|"
        r"(?:treat|regard|consider|deem)\s+[^,，。；;]{{0,40}}\s+as\s+{v}\b|"
        r"(?:视为|视作|当作|认定为|判别为)\s*{v}"
    )
    if re.search(verdict_re.format(v="clean"), lowered):
        return "clean"
    if re.search(verdict_re.format(v="error"), lowered):
        return "error"
    if re.search(verdict_re.format(v="unresolved"), lowered):
        return "unresolved"
    if re.search(r"不成立|不应视为|不足以构成|非因果", rule_text):
        return "deny_causal"
    return "process"


def apply_edit(genome: SkillGenome, edit: SkillEdit) -> SkillGenome:
    """Apply a single edit to the genome, returning a NEW genome."""
    valid, reason = validate_edit(edit, genome)
    if not valid:
        raise ValueError(f"Invalid edit: {reason}")

    child = genome.clone()
    module = child.modules[edit.target_module]
    new_generation = genome.generation + 1
    new_meta = {
        "source_case_id": edit.source_case_id,
        "fitness_status": FITNESS_STATUS_NONE,
        "direction": _detect_rule_direction(edit.new_content),
        "feature_description": sanitize_feature_description(
            edit.feature_description
        ),
        "hallucination_type": edit.hallucination_type,
        "secondary_hallucination_type": edit.secondary_hallucination_type,
        "evolution_direction": edit.evolution_direction,
        "type_confidence": normalize_type_confidence(edit.type_confidence),
        "routing_allowed_modules": list(edit.routing_allowed_modules),
        "created_at_generation": new_generation,
        "rule_slots": _sanitize_rule_slots(edit.rule_slots),
        "action_spec": sanitize_action_spec(edit.action_spec),
        "condition_spec": sanitize_condition_spec(edit.condition_spec),
        "binding_proof": sanitize_binding_proof(edit.binding_proof),
        "repair_edge": _sanitize_repair_edge(edit.repair_edge),
        "route_guidance": sanitize_route_guidance(edit.route_guidance),
        "failure_mechanism": edit.failure_mechanism[:500],
        "expected_local_effect": edit.expected_local_effect[:240],
    }

    if edit.operation == SkillOp.ADD_RULE:
        module.rules.append(edit.new_content)
        module.set_rule_meta(len(module.rules) - 1, new_meta)

    elif edit.operation == SkillOp.MODIFY_RULE:
        old_rule = module.rules[edit.rule_index]
        # Merge provenance is not an escape hatch. Old checkpoints may carry
        # is_merge=True, but every restored edit must satisfy the same novelty
        # contract as a newly generated modification.
        similarity = semantic_overlap(old_rule, edit.new_content)
        if similarity >= DEDUP_HIGH_THRESHOLD:
            raise ValueError(
                f"semantically_equivalent: overlap={similarity:.2f} "
                f">= {DEDUP_HIGH_THRESHOLD} (new rule is near-identical "
                f"to existing — use a meaningful modification)"
            )
        module.rules[edit.rule_index] = edit.new_content
        module.set_rule_meta(edit.rule_index, new_meta)

    elif edit.operation == SkillOp.REMOVE_RULE:
        module.rules.pop(edit.rule_index)
        if edit.rule_index < len(module.rule_meta):
            module.rule_meta.pop(edit.rule_index)

    # Update lineage
    child.parent_hash = genome.content_hash
    child.generation = new_generation
    child.edits_applied = genome.edits_applied + [edit]
    child.genome_id = f"genome_g{child.generation}_{uuid.uuid4().hex[:8]}"

    return child


# ============================================================
# Baseline Genome Factory
# ============================================================

# Immutable baseline rule text, the single source of truth for the
# proof-court-native baseline. The proof court does not expose continuous
# CRS to runtime providers, so baseline rules mention only trajectory and
# certificate evidence the receiving stage can observe. Hot validation paths
# (validate_edit, the structural gate, reflection module displays) consult
# this constant directly instead of rebuilding a full genome, and
# create_baseline_genome() builds fresh mutable genomes from it.
BASELINE_RULE_TEXT: Mapping[str, tuple[str, ...]] = {
    "candidate_trigger": (
        "When an unsupported claim or action affects a later result, create one candidate at its earliest observable origin.",
        "When a tool failure is explicitly recovered before impact, do not create an error candidate from that failure alone.",
    ),
    "evidence_obligation": (
        "When cited evidence proves norm, breach, agent control, impact, and counterfactual link, support the error.",
        "When any causal-chain field is missing, treat the evidence as incomplete.",
    ),
    "candidate_to_verifier_route": (
        "When a candidate is a claim, action, omission, or adoption, route it to the matching verifier.",
    ),
    "atomic_verifier_instruction": (
        "When verifying a candidate, test it independently against cited trajectory events.",
        "When cited events do not establish the claimed causal chain, reject the certificate.",
    ),
    "causal_link_test": (
        "When correcting an origin would prevent the incorrect result, treat that origin as causal.",
        "When an error lacks cited propagation to benchmark impact, reject the causal link.",
    ),
    "earliest_origin_tiebreak": (
        "When multiple origins are causally sufficient, select the earliest cited event.",
    ),
}


def create_baseline_genome() -> SkillGenome:
    """Create a proof-court-native, immutable baseline Skill genome."""
    modules = {
        module_id: SkillModule(module_id=module_id, rules=list(rules))
        for module_id, rules in BASELINE_RULE_TEXT.items()
    }
    genome = SkillGenome(
        genome_id="baseline_paca_tcl_v3_5_proof_native",
        modules=modules,
        metadata={
            "source": "PACA-TCL v3.5 proof-court native",
            "baseline_rules_immutable": True,
            "continuous_crs_in_runtime_prompt": False,
        },
    )
    # Mark all baseline rules as immutable
    for mid in MODULE_IDS:
        mod = genome.modules[mid]
        for i in range(len(mod.rules)):
            mod.set_rule_meta(i, {"is_baseline": True})
    return genome
