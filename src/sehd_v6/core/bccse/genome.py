"""BCCSE v5 — Skill Genome with natural language rules.

Replaces v4's typed clause structure with natural language rules that:
1. LLM can directly generate (no format adaptation needed)
2. Can be directly injected into detection prompts (no compilation needed)
3. Support natural operations: add_rule / modify_rule / remove_rule

Design principles:
- Each module contains a list of natural language behavioral rules
- Rules are free-form text that the detection LLM can understand and follow
- Module-to-stage mapping is preserved for injection targeting
- Per-module capacity limits prevent bloat
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ============================================================
# Module IDs and Stage Mapping (preserved from v4)
# ============================================================

MODULE_IDS = (
    "candidate_trigger",
    "evidence_obligation",
    "candidate_to_verifier_route",
    "atomic_verifier_instruction",
    "clean_defense_boundary",
    "causal_link_test",
    "earliest_origin_tiebreak",
    "abstention_rule",
)

MODULE_DISPLAY_NAMES: dict[str, str] = {
    "candidate_trigger": "候选发现",
    "evidence_obligation": "证据义务",
    "candidate_to_verifier_route": "验证路由",
    "atomic_verifier_instruction": "验证指令",
    "clean_defense_boundary": "辩护边界",
    "causal_link_test": "因果测试",
    "earliest_origin_tiebreak": "根源裁决",
    "abstention_rule": "弃权规则",
}

# Per-module capacity (not uniform — based on expected traffic).
# v5.3.5: core modules (clean_defense_boundary, evidence_obligation,
# abstention_rule) have higher capacity to accommodate more evolved rules
# without triggering premature capacity rejection. Auxiliary modules keep
# smaller capacity to prevent bloat.
MODULE_CAPACITY: dict[str, int] = {
    "candidate_trigger": 6,
    "evidence_obligation": 10,
    "candidate_to_verifier_route": 3,
    "atomic_verifier_instruction": 6,
    "clean_defense_boundary": 10,
    "causal_link_test": 6,
    "earliest_origin_tiebreak": 4,
    "abstention_rule": 8,
}

# Stage mapping for injection
MODULE_STAGE: dict[str, str] = {
    "candidate_trigger": "stage_1_census",
    "evidence_obligation": "stage_2_verification",
    "candidate_to_verifier_route": "stage_2_verification",
    "atomic_verifier_instruction": "stage_2_verification",
    "clean_defense_boundary": "stage_2_verification",
    "causal_link_test": "stage_3_causal_court",
    "earliest_origin_tiebreak": "stage_3_causal_court",
    "abstention_rule": "stage_3_causal_court",
}


# ============================================================
# Fitness status and verdict permissions (v5.3 optimization)
# ============================================================

FITNESS_STATUS_NONE = "none"
FITNESS_STATUS_WEAK = "weak"
FITNESS_STATUS_STRONG = "strong"

# v5.3.5 tiered semantic deduplication thresholds.
# < DEDUP_LOW_THRESHOLD: no action (new rule is sufficiently distinct)
# DEDUP_LOW_THRESHOLD..DEDUP_HIGH_THRESHOLD: merge new rule's distinct
#   conditions into the existing rule (LLM-assisted at compile time;
#   structural gate returns a "merge_required" signal so the runner can
#   route to the LLM merger). The merged content replaces the existing rule.
# >= DEDUP_HIGH_THRESHOLD: reject as near-duplicate (drop the new edit).
DEDUP_LOW_THRESHOLD = 0.70
DEDUP_HIGH_THRESHOLD = 0.95

# Modules allowed to directly emit a final verdict.
# Key = module_id, Value = frozenset of allowed verdict tokens.
# Rationale (from v5.1 success + R2 failure analysis):
#   - abstention_rule: its job IS the final adjudication boundary
#   - clean_defense_boundary: v5.1 proved defense→clean is effective
#   - causal_link_test: may deny a causal link (local negation only)
#   - all others: CRS/coverage not yet computed or wrong semantic layer
ALLOWED_VERDICTS: dict[str, frozenset[str]] = {
    "abstention_rule": frozenset({"clean", "error", "unresolved"}),
    "clean_defense_boundary": frozenset({"clean"}),
}

# Modules allowed to perform local negation (not a final verdict).
# causal_link_test may say "causal link does not hold" which weakens
# the prosecution but does not directly emit clean/error/unresolved.
LOCAL_NEGATION_MODULES: frozenset[str] = frozenset({"causal_link_test"})


# ============================================================
# Operations
# ============================================================

class SkillOp(str, Enum):
    """Allowed operations on skill modules."""
    ADD_RULE = "add_rule"
    MODIFY_RULE = "modify_rule"
    REMOVE_RULE = "remove_rule"


# ============================================================
# Forbidden content (firewall against leakage)
# ============================================================

FORBIDDEN_CONTENT_MARKERS = frozenset({
    "case_id", "source_id", "agent_id", "model_id",
    "gold_explanation_text", "outer_metric", "outer_error",
    "example_specific_fact", "continuous_threshold_search",
})


def validate_runtime_rule(content: str, target_module: str) -> tuple[bool, str]:
    """Validate the non-negotiable runtime-observability rule contract."""
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
    if re.search(r"(?:^|[\s（(])(?:0|1|2|3)[.)、]|首先|其次|然后|最后", normalized):
        return False, "multi_step_pseudocode"
    condition = normalized.split("时", 1)[0]
    if lowered.startswith(("when ", "if ")) or normalized.startswith("如果"):
        condition = re.split(r"[,，]", normalized, maxsplit=1)[0]
    conjunctions = re.findall(
        r"且|并且|以及|或|\band\b|\bor\b",
        condition.lower(),
    )
    if len(conjunctions) > 1:
        return False, "condition_overload"
    action = normalized[len(condition):]
    if any(token in action for token in ("；", ";", "然后", "同时还", "并随后")):
        return False, "multiple_actions"

    forbidden_runtime_markers = (
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
    for marker in forbidden_runtime_markers:
        if marker in lowered:
            return False, f"forbidden_runtime_marker:{marker}"
    if re.search(r"\b[a-z][a-z_]+/\d+\b|https?://", lowered):
        return False, "case_specific_pattern"
    # --- Verdict permission (layered, v5.3 optimization) ---
    # Detect final verdict emission (clean/error/unresolved)
    final_verdict_re = (
        r"判定为\s*(clean|error|unresolved)|"
        r"直接输出\s*(clean|error|unresolved)|"
        r"(?:return|classify as|label as)\s+(clean|error|unresolved)\b|"
        r"应判(?:定为|为)?\s*(clean|error|unresolved)|"
        r"结论(?:为|是)\s*(clean|error|unresolved)"
    )
    verdict_search = re.search(final_verdict_re, lowered)
    if verdict_search:
        verdict = next(g for g in verdict_search.groups() if g)
        allowed = ALLOWED_VERDICTS.get(target_module, frozenset())
        if verdict not in allowed:
            return False, f"verdict_not_allowed:{verdict}_for_{target_module}"
    else:
        # Detect local negation (weaker than final verdict)
        # e.g. "causal link does not hold" — weakens prosecution
        negation_re = r"不成立|不应视为|不足以构成|非因果|因果链不成立|不构成因果"
        if re.search(negation_re, normalized) and target_module not in LOCAL_NEGATION_MODULES:
            return False, f"local_negation_not_allowed_for_{target_module}"
    return True, ""


# ============================================================
# Core Data Structures
# ============================================================

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
        self.rule_meta[index] = dict(meta)

    def is_rule_protected(self, index: int) -> bool:
        """Protected if baseline or passed fitness (weak/strong)."""
        meta = self.get_rule_meta(index)
        if meta.get("is_baseline"):
            return True
        return meta.get("fitness_status") in (FITNESS_STATUS_WEAK, FITNESS_STATUS_STRONG)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "active": self.active,
            "rules": list(self.rules),
            "rule_meta": [dict(m) for m in self.rule_meta],
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
    rationale: str = ""  # LLM's reasoning
    source_case_id: str = ""
    is_merge: bool = False  # v5.3.5: marks merge-generated edits to bypass similarity check

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "target_module": self.target_module,
            "operation": self.operation,
            "rule_index": self.rule_index,
            "new_content": self.new_content,
            "rationale": self.rationale,
            "source_case_id": self.source_case_id,
            "is_merge": self.is_merge,
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
        )


@dataclass
class SkillGenome:
    """Complete skill genome: 8 modules with natural language rules."""
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
        state = {"modules": {mid: self.modules[mid].to_dict() for mid in MODULE_IDS}}
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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

    def get_all_rules_text(self) -> str:
        """Get all rules formatted for LLM context."""
        parts = []
        for mid in MODULE_IDS:
            mod = self.modules[mid]
            if not mod.active or not mod.rules:
                continue
            display = MODULE_DISPLAY_NAMES.get(mid, mid)
            parts.append(f"[{display}] ({mid})")
            for i, rule in enumerate(mod.rules, 1):
                parts.append(f"  {i}. {rule}")
        return "\n".join(parts)

    def get_module_rules_text(self, module_id: str) -> str:
        """Get rules for a specific module."""
        mod = self.modules.get(module_id)
        if not mod or not mod.rules:
            return "(无规则)"
        return "\n".join(f"  {i}. {r}" for i, r in enumerate(mod.rules, 1))


# ============================================================
# Edit Application
# ============================================================

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
    edit.operation = operation_value

    module = genome.modules[edit.target_module]

    # Check forbidden content
    content_lower = edit.new_content.lower()
    for marker in FORBIDDEN_CONTENT_MARKERS:
        if marker in content_lower:
            return False, f"forbidden_content: {marker}"

    if edit.operation == SkillOp.ADD_RULE:
        if module.is_full:
            return False, f"module_full: {len(module.rules)} >= {module.capacity}"
        valid_rule, rule_reason = validate_runtime_rule(
            edit.new_content,
            edit.target_module,
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
        baseline_rules = create_baseline_genome().modules[edit.target_module].rules
        if module.rules and module.rules[edit.rule_index] in baseline_rules:
            return False, "immutable_baseline_rule"
        # Protected modify check (v5.3): evolved rules that passed fitness
        # (weak/strong) cannot be modified by subsequent cases.
        # Use add + challenge mechanism instead.
        if module.is_rule_protected(edit.rule_index):
            meta = module.get_rule_meta(edit.rule_index)
            status = meta.get("fitness_status", "unknown")
            return False, f"protected_rule: fitness_status={status}"
        valid_rule, rule_reason = validate_runtime_rule(
            edit.new_content,
            edit.target_module,
        )
        if not valid_rule:
            return False, rule_reason
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
        baseline_rules = create_baseline_genome().modules[edit.target_module].rules
        if module.rules[edit.rule_index] in baseline_rules:
            return False, "immutable_baseline_rule"

    return True, ""


def _legacy_semantic_overlap(text_a: str, text_b: str) -> float:
    """Legacy whitespace-token Jaccard retained for audit comparison."""
    tokens_a = set(text_a.lower().replace("，", " ").replace("。", " ").split())
    tokens_b = set(text_b.lower().replace("，", " ").replace("。", " ").split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def semantic_overlap(text_a: str, text_b: str) -> float:
    """Compute language-agnostic character-bigram Jaccard similarity."""
    def tokens(text: str) -> set[str]:
        normalized = re.sub(r"[\W_]+", "", text.lower(), flags=re.UNICODE)
        if len(normalized) < 2:
            return {normalized} if normalized else set()
        return {
            normalized[index:index + 2]
            for index in range(len(normalized) - 1)
        }

    tokens_a = tokens(text_a)
    tokens_b = tokens(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _detect_rule_direction(rule_text: str) -> str:
    """Detect the action direction of a rule for direction-consistency checks."""
    lowered = rule_text.lower()
    if re.search(r"判(?:定为|为)?\s*clean|直接输出\s*clean|应判.*clean|结论.*clean", lowered):
        return "clean"
    if re.search(r"判(?:定为|为)?\s*error|直接输出\s*error|应判.*error|结论.*error", lowered):
        return "error"
    if re.search(r"判(?:定为|为)?\s*unresolved|直接输出\s*unresolved|应判.*unresolved|结论.*unresolved", lowered):
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
        "created_at_generation": new_generation,
    }

    if edit.operation == SkillOp.ADD_RULE:
        module.rules.append(edit.new_content)
        module.set_rule_meta(len(module.rules) - 1, new_meta)

    elif edit.operation == SkillOp.MODIFY_RULE:
        old_rule = module.rules[edit.rule_index]
        # v5.3.5: skip similarity check for merge-generated edits, since
        # merged content naturally overlaps with the original rule.
        if not getattr(edit, "is_merge", False):
            similarity = semantic_overlap(old_rule, edit.new_content)
            if similarity > 0.7:
                raise ValueError(
                    f"semantically_equivalent: overlap={similarity:.2f} > 0.70 "
                    f"(new rule is too similar to existing)"
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

def _create_legacy_baseline_genome() -> SkillGenome:
    """Create the baseline genome (PACA-TCL v3.5 default behavior).

    The baseline has minimal rules that describe the existing v3.5 behavior.
    Evolution adds/modifies rules on top of this.
    """
    modules: dict[str, SkillModule] = {}

    modules["candidate_trigger"] = SkillModule(
        module_id="candidate_trigger",
        rules=[
            "使用四维扫描（F/Fa/P/S）发现幻觉候选",
            "合并 PACA 候选、构造普查和确定性过程普查的结果",
        ],
    )

    modules["evidence_obligation"] = SkillModule(
        module_id="evidence_obligation",
        rules=[
            "每个 CRS 维度评分必须有明确的证据引用支持",
            "CRS 权重固定为 factuality=0.35, counterfactual=0.15, recovery=0.20, process=0.20, scope=0.10",
        ],
    )

    modules["candidate_to_verifier_route"] = SkillModule(
        module_id="candidate_to_verifier_route",
        rules=[
            "根据候选类型路由到对应的专项验证器",
        ],
    )

    modules["atomic_verifier_instruction"] = SkillModule(
        module_id="atomic_verifier_instruction",
        rules=[
            "对每个候选执行因果链脚手架验证（NORM, ORIGIN, CONTROL, PROPAGATION, IMPACT, COUNTERFACTUAL）",
            "验证失败时最多重试 2 次（隔离反馈重试）",
        ],
    )

    modules["clean_defense_boundary"] = SkillModule(
        module_id="clean_defense_boundary",
        rules=[
            "如果 agent 的行为有工具输出支持，则不应被标记为幻觉",
            "工具返回错误且 agent 未忽略该错误，属于正常失败而非幻觉",
            "agent 尝试了纠正操作（重试/恢复），支持 clean 判定",
            "错误在最终输出前被修正，不应判定为幻觉",
        ],
    )

    modules["causal_link_test"] = SkillModule(
        module_id="causal_link_test",
        rules=[
            "如果修正根源后结果会改变，则因果链成立",
            "错误必须通过可识别的步骤传播到最终输出",
        ],
    )

    modules["earliest_origin_tiebreak"] = SkillModule(
        module_id="earliest_origin_tiebreak",
        rules=[
            "在多个因果充分的根源中，选择事件顺序最早的",
        ],
    )

    modules["abstention_rule"] = SkillModule(
        module_id="abstention_rule",
        rules=[
            "当 CRS 分数处于模糊区间时，应判定 unresolved",
            "当辩方证据压倒性地强于控方时，应判定 unresolved 或 clean",
        ],
    )

    return SkillGenome(
        genome_id="baseline_paca_tcl_v3_5",
        modules=modules,
        metadata={"source": "PACA-TCL v3.5 fixed", "f1_d": 0.747},
    )


def create_baseline_genome() -> SkillGenome:
    """Create a proof-court-native, immutable baseline Skill genome.

    PACA-TCL v3.5 explicitly does not expose continuous CRS to runtime
    providers. Baseline rules therefore mention only trajectory and
    certificate evidence that the receiving stage can actually observe.
    """
    modules = {
        "candidate_trigger": SkillModule(
            module_id="candidate_trigger",
            rules=[
                "When an unsupported claim or action affects a later result, create one candidate at its earliest observable origin.",
                "When a tool failure is explicitly recovered before impact, do not create an error candidate from that failure alone.",
            ],
        ),
        "evidence_obligation": SkillModule(
            module_id="evidence_obligation",
            rules=[
                "When cited evidence proves norm, breach, agent control, impact, and counterfactual link, support the error.",
                "When any causal-chain field is missing, treat the evidence as incomplete.",
            ],
        ),
        "candidate_to_verifier_route": SkillModule(
            module_id="candidate_to_verifier_route",
            rules=[
                "When a candidate is a claim, action, omission, or adoption, route it to the matching verifier.",
            ],
        ),
        "atomic_verifier_instruction": SkillModule(
            module_id="atomic_verifier_instruction",
            rules=[
                "When verifying a candidate, test it independently against cited trajectory events.",
                "When cited events do not establish the claimed causal chain, reject the certificate.",
            ],
        ),
        "clean_defense_boundary": SkillModule(
            module_id="clean_defense_boundary",
            rules=[
                "When tool output directly supports the agent claim, treat that claim as clean.",
                "When the agent acknowledges and repairs a tool failure before impact, treat the failure as clean.",
                "When a deviation is authorized by the user or forced by the environment, do not attribute it to the agent.",
                "When an error is fully corrected before downstream impact, treat it as clean.",
            ],
        ),
        "causal_link_test": SkillModule(
            module_id="causal_link_test",
            rules=[
                "When correcting an origin would prevent the incorrect result, treat that origin as causal.",
                "When an error lacks cited propagation to benchmark impact, reject the causal link.",
            ],
        ),
        "earliest_origin_tiebreak": SkillModule(
            module_id="earliest_origin_tiebreak",
            rules=[
                "When multiple origins are causally sufficient, select the earliest cited event.",
            ],
        ),
        "abstention_rule": SkillModule(
            module_id="abstention_rule",
            rules=[
                "When neither a complete causal error proof nor a complete clean defense is established, return unresolved.",
                "When a complete clean defense defeats every error candidate, return clean.",
            ],
        ),
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


# ============================================================
# V4-compatible mutation types (used by mutator.py)
# ============================================================

class MutationOp(str, Enum):
    REPLACE_TYPED_CLAUSE = "replace_typed_clause"
    TIGHTEN_PRECONDITION = "tighten_precondition"
    ADD_NEGATIVE_BOUNDARY = "add_negative_boundary"
    APPLY_FIX = "apply_fix"


@dataclass
class AtomicMutation:
    """A single atomic mutation proposed by the LLM."""

    operation: MutationOp
    target_module: str
    rule_index: int | None
    new_content: str | None = None
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value,
            "target_module": self.target_module,
            "rule_index": self.rule_index,
            "new_content": self.new_content,
            "rationale": self.rationale,
        }


def all_clause_signatures(genome: SkillGenome) -> list[str]:
    """Get all rule signatures from the genome (for v4 compat)."""
    signatures: list[str] = []
    for mid in MODULE_IDS:
        mod = genome.modules.get(mid)
        if mod and mod.rules:
            for i, rule in enumerate(mod.rules):
                signatures.append(f"{mid}[{i}]: {rule[:80]}")
    return signatures


def validate_mutation(
    mutation: AtomicMutation, genome: SkillGenome
) -> tuple[bool, str]:
    """Validate an atomic mutation against the genome."""
    if mutation.target_module not in MODULE_IDS:
        return False, f"invalid_module: {mutation.target_module}"

    module = genome.modules[mutation.target_module]
    content = (mutation.new_content or "").strip()

    if mutation.operation == MutationOp.APPLY_FIX:
        if mutation.rule_index is None or mutation.rule_index >= len(module.rules):
            return False, f"invalid_rule_index: {mutation.rule_index}"
        if not content:
            return False, "empty_content"

    elif mutation.operation in (
        MutationOp.REPLACE_TYPED_CLAUSE,
        MutationOp.TIGHTEN_PRECONDITION,
        MutationOp.ADD_NEGATIVE_BOUNDARY,
    ):
        if not content:
            return False, "empty_content"
        if mutation.rule_index is not None and mutation.rule_index >= len(module.rules):
            return False, f"invalid_rule_index: {mutation.rule_index}"

    # Check forbidden content
    content_lower = content.lower()
    for marker in FORBIDDEN_CONTENT_MARKERS:
        if marker in content_lower:
            return False, f"forbidden_content: {marker}"

    return True, "ok"
