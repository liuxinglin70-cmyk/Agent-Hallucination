"""BCCSE v5 — Structural constraint gate for skill evolution.

Replaces v4's canary-based safety gate with lightweight structural constraints.
The design principle: "Structural prevention > post-hoc verification".

Four constraint layers:
1. Typed DSL: edits must use the typed clause format (not free prose)
2. Module targeting: edits must point to a specific module (not global)
3. Generalization filter: edits must not contain case-specific information
4. Consistency: edits must not contradict existing rules in the same module

Additional frequency controls:
- Per-module cooldown: same module cannot be edited within N cases
- Total edit cap: maximum number of edits per evolution run
- Convergence detection: stop if no edits accepted for K consecutive cases
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .genome import (
    FORBIDDEN_CONTENT_MARKERS,
    MODULE_IDS,
    SkillOp,
    SkillGenome,
    semantic_overlap,
    validate_runtime_rule,
    DEDUP_LOW_THRESHOLD,
    DEDUP_HIGH_THRESHOLD,
)


# ============================================================
# Configuration
# ============================================================

@dataclass
class GateConfig:
    """Configuration for the structural constraint gate.

    v5.4: these defaults are the single source of truth. ``run_online_evolution``
    and the CLI both default to these values (0/200/20).
    """

    # Frequency control (minimal — rely on structural constraints, not artificial limits)
    module_cooldown: int = 0  # no cooldown; rely on duplicate detection
    max_total_edits: int = 200  # effectively unlimited (capacity constraint prevents bloat)
    convergence_window: int = 20  # generous window before declaring convergence

    # Generalization filter
    min_content_length: int = 10  # reject overly specific (short) edits
    # Rule quality constraints (v5.2 R2)
    max_modify_ratio: float = 1.5  # modify_rule: new length <= 1.5x original
    max_same_prefix_rules: int = 3  # max rules sharing same condition prefix


# ============================================================
# Gate Certificate
# ============================================================

@dataclass
class GateCertificate:
    """Auditable record of a single gate evaluation."""

    timestamp: float
    case_id: str
    target_module: str
    operation: str
    content_preview: str
    decision: str  # "accept" | "reject"
    reject_reasons: list[str]
    edit_index: int  # which edit number this is
    case_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "case_id": self.case_id,
            "target_module": self.target_module,
            "operation": self.operation,
            "content_preview": self.content_preview,
            "decision": self.decision,
            "reject_reasons": self.reject_reasons,
            "edit_index": self.edit_index,
            "case_index": self.case_index,
        }


# ============================================================
# Structural Constraint Gate
# ============================================================

@dataclass
class StructuralGate:
    """Structural constraint gate for online skill evolution.

    Validates proposed edits against multiple constraint layers:
    1. Typed DSL / module targeting / generalization
    2. Causal coherence (edit must address attributed cause)
    3. Module capacity (anti-bloat: max 6 clauses + 2 boundaries)
    4. Signal-driven edit scheduling (intensity matches urgency)
    5. Signal decay monitoring (direction guarantee, zero-cost)
    """

    config: GateConfig
    certificates: list[GateCertificate] = field(default_factory=list)
    _module_last_edit_idx: dict[str, int] = field(default_factory=dict)
    _total_edits: int = field(default=0)
    _consecutive_no_edit: int = field(default=0)
    _converged: bool = field(default=False)
    _halted: bool = field(default=False)
    _halt_reason: str = field(default="")

    @property
    def is_halted(self) -> bool:
        """Whether evolution should stop."""
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def accepted_count(self) -> int:
        return sum(1 for c in self.certificates if c.decision == "accept")

    @property
    def rejected_count(self) -> int:
        return sum(1 for c in self.certificates if c.decision == "reject")

    def record_no_edit(self) -> None:
        """Record a no-edit case without skipping unseen registered cases."""
        self._consecutive_no_edit += 1
        if self._consecutive_no_edit >= self.config.convergence_window:
            self._converged = True

    def _restore_last_edit_index(self, module: str) -> None:
        prior = [
            certificate.case_index
            for certificate in self.certificates
            if certificate.decision == "accept"
            and certificate.target_module == module
        ]
        if prior:
            self._module_last_edit_idx[module] = max(prior)
        else:
            self._module_last_edit_idx.pop(module, None)

    def revoke_structural_accept(
        self,
        certificate: GateCertificate,
        reasons: Sequence[str],
    ) -> None:
        """Convert a structural accept into a post-replay fitness rejection."""
        if certificate.decision != "accept":
            return
        certificate.decision = "reject"
        certificate.reject_reasons.extend(str(reason) for reason in reasons)
        self._total_edits = max(0, self._total_edits - 1)
        self._restore_last_edit_index(certificate.target_module)
        self.record_no_edit()

    def get_allowed_operations(self, module: str) -> list[str]:
        """All operations are always allowed — trust the LLM's judgment.

        The LLM causal reflection decides what operation is appropriate.
        Structural constraints (capacity, duplicates) prevent abuse.

        Returns v5 SkillOp values (add_rule / modify_rule / remove_rule).
        """
        return ["add_rule", "modify_rule", "remove_rule"]

    def evaluate(
        self,
        *,
        case_id: str,
        case_index: int,
        target_module: str,
        operation: str,
        content: str,
        preconditions: Sequence[str],
        current_genome: SkillGenome,
        rule_index: int | None = None,
        expected_module: str | Sequence[str] | None = None,
    ) -> tuple[bool, GateCertificate, list[str]]:
        """Evaluate whether a proposed edit passes structural constraints.

        Returns:
            (accepted, certificate, reject_reasons)
        """
        reject_reasons: list[str] = []

        # --- Check halt ---
        if self._halted:
            reject_reasons.append(f"halted: {self._halt_reason}")

        operation = operation.value if isinstance(operation, SkillOp) else str(operation)
        allowed_operations = {value.value for value in SkillOp}
        if operation not in allowed_operations:
            reject_reasons.append(f"invalid_operation: {operation}")

        # --- Layer 1: concise if-then behavioral instruction ---
        if operation in {SkillOp.ADD_RULE.value, SkillOp.MODIFY_RULE.value}:
            valid_rule, rule_reason = validate_runtime_rule(content, target_module)
            if not valid_rule:
                reject_reasons.append(f"runtime_rule_contract:{rule_reason}")
            if not content or len(content.strip()) < self.config.min_content_length:
                reject_reasons.append(
                    f"content_too_short: {len(content.strip())} < "
                    f"{self.config.min_content_length}"
                )
            normalized = content.strip()
            has_if_then = (
                (
                    normalized.startswith(("当", "如果"))
                    and (
                        "时" in normalized
                        or "，" in normalized
                        or "," in normalized
                    )
                )
                or (
                    normalized.lower().startswith(("when ", "if "))
                    and ("," in normalized or "，" in normalized)
                )
            )
            if not has_if_then:
                reject_reasons.append("not_if_then_instruction")
            if re.search(r"(?:^|[\s（(])(?:0|1|2|3)[.)、]|首先|其次|然后|最后", normalized):
                reject_reasons.append("multi_step_pseudocode")
            condition = normalized.split("时", 1)[0]
            if (
                normalized.lower().startswith(("when ", "if "))
                or normalized.startswith("如果")
            ):
                condition = re.split(r"[,，]", normalized, maxsplit=1)[0]
            conjunctions = re.findall(
                r"且|并且|以及|或|\band\b|\bor\b",
                condition.lower(),
            )
            if len(conjunctions) > 1:
                reject_reasons.append(
                    f"condition_overload: {len(conjunctions) + 1} conditions > 2"
                )
            action = normalized[len(condition):]
            if any(token in action for token in ("；", ";", "然后", "同时还", "并随后")):
                reject_reasons.append("multiple_actions")

        # --- Layer 2: Module targeting ---
        if target_module not in MODULE_IDS:
            reject_reasons.append(f"invalid_module: {target_module}")
        if expected_module is not None:
            expected_modules = (
                {expected_module}
                if isinstance(expected_module, str)
                else {str(value) for value in expected_module}
            )
            if target_module not in expected_modules:
                reject_reasons.append(
                    "module_attribution_mismatch: "
                    f"{target_module} not in {sorted(expected_modules)}"
                )

        # --- Layer 3: Generalization filter ---
        # Check forbidden content markers
        content_lower = content.lower()
        for marker in FORBIDDEN_CONTENT_MARKERS:
            if marker in content_lower:
                reject_reasons.append(f"forbidden_content: {marker}")

        # Check for case-specific patterns (IDs, specific names)
        case_id_patterns = [
            r'\bcase_\d+\b',
            r'\bsource_\d+\b',
            r'\bag_\w{8,}\b',  # agent IDs
            r'\b0x[0-9a-f]{6,}\b',  # hex IDs
            r'\b[a-z][a-z_]+/\d+\b',  # actual AgentHallu case IDs
            r'https?://',
        ]
        for pattern in case_id_patterns:
            if re.search(pattern, content_lower):
                reject_reasons.append(f"case_specific_pattern: {pattern}")
                break
        leakage_markers = (
            "gold",
            "ground truth",
            "golden label",
            "真值",
            "金标签",
            "标注标签",
            "本案例",
            "这个案例",
        )
        for marker in leakage_markers:
            if marker in content_lower:
                reject_reasons.append(f"label_or_case_leakage: {marker}")
                break

        # --- Layer 4: Module capacity (anti-bloat) ---
        module = current_genome.modules.get(target_module)
        if module is not None:
            # The genome's registered per-module capacity is authoritative.
            if len(module.rules) >= module.capacity:
                if operation == "modify_rule" or operation == "remove_rule":
                    pass  # modifying/removing doesn't grow
                else:
                    reject_reasons.append(
                        f"module_full: {len(module.rules)} rules >= "
                        f"{module.capacity} (registered module capacity)"
                    )

            # Check for duplicate content within target module
            for rule in module.rules:
                if rule.strip().lower() == content.strip().lower():
                    reject_reasons.append("duplicate_content: identical to existing rule")
                    break

            if operation in {"modify_rule", "remove_rule"}:
                if rule_index is None or rule_index < 0:
                    reject_reasons.append("invalid_rule_index")
                elif rule_index >= len(module.rules):
                    reject_reasons.append(
                        f"rule_index_out_of_range: {rule_index} >= "
                        f"{len(module.rules)}"
                    )
                else:
                    from .genome import create_baseline_genome

                    baseline_rules = create_baseline_genome().modules[
                        target_module
                    ].rules
                    if module.rules[rule_index] in baseline_rules:
                        reject_reasons.append("immutable_baseline_rule")

        # --- Layer 5: Cross-module tiered semantic deduplication ---
        # v5.3.5: tiered thresholds avoid wasting evolution experience.
        #   overlap < 0.70: accept (sufficiently distinct)
        #   0.70 <= overlap < 0.95: signal "merge_required" — the new edit
        #       should be merged into the existing rule rather than added
        #       as a standalone rule. The runner routes this to LLM-assisted
        #       merging (for fuzzy semantic rules) or structural merge
        #       (for condition-list rules).
        #   overlap >= 0.95: reject as near-duplicate (drop the new edit)
        if operation in {"add_rule", "modify_rule"}:
            for mid in MODULE_IDS:
                mod = current_genome.modules.get(mid)
                if not mod or not mod.active:
                    continue
                # Skip self for modify_rule (the rule being modified).
                # FIX: skip by rule_index, not by content match — the new
                # content differs from the old rule (otherwise why modify?),
                # so the old content-based skip never triggered.
                for rule_idx, existing_rule in enumerate(mod.rules):
                    if (
                        operation == "modify_rule"
                        and mid == target_module
                        and rule_idx == rule_index
                    ):
                        continue
                    overlap = semantic_overlap(existing_rule, content)
                    if overlap >= DEDUP_HIGH_THRESHOLD:
                        reject_reasons.append(
                            f"cross_module_near_duplicate: overlap={overlap:.2f} "
                            f">= {DEDUP_HIGH_THRESHOLD} with rule in [{mid}]: "
                            f"\"{existing_rule[:50]}...\""
                        )
                        break
                    if overlap >= DEDUP_LOW_THRESHOLD:
                        # Signal merge requirement; runner handles the merge.
                        reject_reasons.append(
                            f"cross_module_merge_required: overlap={overlap:.2f} "
                            f"in [{DEDUP_LOW_THRESHOLD},{DEDUP_HIGH_THRESHOLD}) "
                            f"with rule in [{mid}]: "
                            f"\"{existing_rule[:50]}...\""
                        )
                        break
                else:
                    continue
                break  # Found a duplicate or merge candidate, stop checking

        # --- Layer 6: Rule quality — forbidden state manipulation ---
        # Rules must NOT try to manipulate internal pipeline state variables
        _state_manipulation_markers = [
            "boundary_path_state", "candidate_path_state",
            "强制设为", "强制设置为", "无条件地将",
            "不得修改此状态", "不得更改此状态",
            "具有最高优先级", "优先于任何其他",
            "不得以任何理由修改", "不得以任何理由更改",
        ]
        for marker in _state_manipulation_markers:
            if marker in content:
                reject_reasons.append(
                    f"state_manipulation: rule tries to control internal state ('{marker}')"
                )
                break

        # Diagnostic signals are available to reflection but not to runtime
        # detector prompts. Rules conditioned on them are semantically dead.
        _diagnostic_only_markers = [
            "crs",
            "coverage_state",
            "trigger_source",
            "prosecution_coverage",
            "defense_coverage",
            "proof_projection",
        ]
        for marker in _diagnostic_only_markers:
            if marker in content_lower:
                reject_reasons.append(
                    f"diagnostic_only_condition: runtime cannot observe '{marker}'"
                )
                break

        # Verdict permission is now enforced in validate_runtime_rule
        # via ALLOWED_VERDICTS / LOCAL_NEGATION_MODULES (genome layer).
        # The genome check supports layered verdict permissions:
        # abstention_rule → {clean, error, unresolved}
        # clean_defense_boundary → {clean}
        # causal_link_test → local negation only
        # all others → no direct verdict

        # --- Layer 7: Rule quality — forbidden cross-module references ---
        # Rules must NOT reference other modules' rules by number/name
        _cross_ref_patterns = [
            r'规则\d',           # "规则2", "规则5"
            r'模块.*规则',       # "证据义务模块规则5"
            r'abstention_rule.*规则',
            r'evidence_obligation.*规则',
            r'按照.*模块',
            r'遵循.*规则\d',
        ]
        for pattern in _cross_ref_patterns:
            if re.search(pattern, content):
                reject_reasons.append(
                    f"cross_module_reference: rule references other modules' rules ('{pattern}')"
                )
                break

        # --- Layer 8: Rule quality — condition diversity ---
        # Prevent too many rules starting with the same condition prefix
        if operation in {"add_rule", "modify_rule"} and module is not None:
            # Extract condition prefix (first clause before comma or period)
            prefix = content.split("，")[0].split(",")[0][:20]
            same_prefix_count = sum(
                1 for r in module.rules
                if r.split("，")[0].split(",")[0][:20] == prefix
            )
            if same_prefix_count >= self.config.max_same_prefix_rules:
                reject_reasons.append(
                    f"condition_overload: {same_prefix_count} rules already share prefix '{prefix}...'"
                )

        # --- Layer 9: Modify bloat prevention ---
        if operation == "modify_rule" and module is not None:
            if rule_index is not None and 0 <= rule_index < len(module.rules):
                old_len = len(module.rules[rule_index])
                max_allowed = old_len * self.config.max_modify_ratio
                if len(content) > max_allowed and len(content) > 80:
                    reject_reasons.append(
                        f"modify_bloat: new={len(content)} > {max_allowed:.0f} "
                        f"(old={old_len} * {self.config.max_modify_ratio})"
                    )

        # --- Frequency control (minimal) ---
        if (
            target_module in self._module_last_edit_idx
            and self.config.module_cooldown > 0
            and case_index - self._module_last_edit_idx[target_module]
            <= self.config.module_cooldown
        ):
            reject_reasons.append(
                "module_cooldown: "
                f"{case_index - self._module_last_edit_idx[target_module]} <= "
                f"{self.config.module_cooldown}"
            )
        # Total edit cap (safety net only, set very high)
        if self._total_edits >= self.config.max_total_edits:
            reject_reasons.append(
                f"total_edit_cap: {self._total_edits} >= {self.config.max_total_edits}"
            )
            if not self._halted:
                self._halted = True
                self._halt_reason = f"total_edit_cap reached: {self._total_edits}"

        # --- Decision ---
        accepted = len(reject_reasons) == 0

        if accepted:
            self._total_edits += 1
            self._module_last_edit_idx[target_module] = case_index
            self._consecutive_no_edit = 0
            self._converged = False
        else:
            self.record_no_edit()

        cert = GateCertificate(
            timestamp=time.time(),
            case_id=case_id,
            target_module=target_module,
            operation=operation,
            content_preview=content[:100],
            decision="accept" if accepted else "reject",
            reject_reasons=reject_reasons,
            edit_index=self._total_edits,
            case_index=case_index,
        )
        self.certificates.append(cert)

        return accepted, cert, reject_reasons

    def summary(self) -> dict[str, Any]:
        """Generate a summary of all gate evaluations."""
        return {
            "schema_version": "agenthallu.bccse_v5_structural_gate.v2",
            "config": {
                "module_cooldown": self.config.module_cooldown,
                "max_total_edits": self.config.max_total_edits,
                "convergence_window": self.config.convergence_window,
            },
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "convergence_signal": self._converged,
            "consecutive_no_edit": self._consecutive_no_edit,
            "total_evaluations": len(self.certificates),
            "accepted": self.accepted_count,
            "rejected": self.rejected_count,
            "total_edits_applied": self._total_edits,
            "certificates": [c.to_dict() for c in self.certificates],
        }


def create_structural_gate(
    *,
    module_cooldown: int = 0,
    max_total_edits: int = 200,
    convergence_window: int = 20,
) -> StructuralGate:
    """Create a structural constraint gate with the registered defaults."""
    config = GateConfig(
        module_cooldown=module_cooldown,
        max_total_edits=max_total_edits,
        convergence_window=convergence_window,
    )
    return StructuralGate(config=config)
