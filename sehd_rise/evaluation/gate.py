"""Structural constraint gate + LLM no-conflict gate for skill evolution.

Two safety surfaces merged into one module:

1. ``StructuralGate`` — lightweight structural constraints on proposed edits
   (typed DSL, module targeting, generalization, capacity, cross-module
   deduplication, state-manipulation firewall). "Structural prevention >
   post-hoc verification": a rule that violates a structural invariant is
   rejected before it ever reaches a provider.

2. ``llm_no_conflict_gate`` — a provider consultation that checks whether a new
   rule semantically conflicts with the target module's existing rules. It runs
   only with a live credential and is fail-closed (any provider exception or
   parse failure is a conflict). The provider transport is routed through
   ``execution.replay._run_conflict_gate_call`` because the evaluation layer may
   not import detection directly; the gate keeps its own budget bookkeeping so a
   conflict-gate call cannot exhaust the mutation budget.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise.execution.replay import _run_conflict_gate_call
from sehd_rise.platform.routes import has_provider_credential
from sehd_rise.skills.genome import (
    BASELINE_RULE_TEXT,
    DEDUP_HIGH_THRESHOLD,
    DEDUP_LOW_THRESHOLD,
    MODULE_IDS,
    SkillGenome,
    SkillOp,
    _detect_rule_direction,
    validate_runtime_rule,
)
from sehd_rise.skills.rule import RuleSpec, validate_rule_spec
from sehd_rise.skills.vocabulary.contracts import (
    FORBIDDEN_CONTENT_MARKERS,
    semantic_overlap,
)


# ============================================================
# Configuration
# ============================================================

@dataclass
class GateConfig:
    """Configuration for the structural constraint gate.

    These defaults are the single source of truth; the online evolution entry
    point and the CLI both default to them.
    """

    # Frequency control (minimal — rely on structural constraints, not artificial limits)
    module_cooldown: int = 0  # no cooldown; rely on duplicate detection
    max_total_edits: int = 200  # effectively unlimited (capacity constraint prevents bloat)
    convergence_window: int = 20  # generous window before declaring convergence

    # Generalization filter
    min_content_length: int = 10  # reject overly specific (short) edits
    # Rule quality constraints
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
    prior_consecutive_no_edit: int = 0
    prior_converged: bool = False

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
            "prior_consecutive_no_edit": self.prior_consecutive_no_edit,
            "prior_converged": self.prior_converged,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GateCertificate":
        return cls(
            timestamp=float(data.get("timestamp") or 0.0),
            case_id=str(data.get("case_id") or ""),
            target_module=str(data.get("target_module") or ""),
            operation=str(data.get("operation") or ""),
            content_preview=str(data.get("content_preview") or ""),
            decision=str(data.get("decision") or "reject"),
            reject_reasons=[
                str(value) for value in (data.get("reject_reasons") or ())
            ],
            edit_index=int(data.get("edit_index") or 0),
            case_index=int(data.get("case_index") or 0),
            prior_consecutive_no_edit=int(
                data.get("prior_consecutive_no_edit") or 0
            ),
            prior_converged=bool(data.get("prior_converged", False)),
        )


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
    _total_edits: int = 0
    _consecutive_no_edit: int = 0
    _converged: bool = False
    _halted: bool = False
    _halt_reason: str = ""

    @property
    def is_halted(self) -> bool:
        """Whether evolution should stop."""
        return self._halted

    @property
    def converged(self) -> bool:
        """Whether the no-edit convergence window terminated the run."""
        return self._converged

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

    def reset_convergence(self) -> None:
        """Clear a convergence marker when skipped work is being retried."""
        self._consecutive_no_edit = 0
        self._converged = False

    def snapshot_convergence(self) -> dict[str, Any]:
        """Snapshot the convergence counters for a later synchronized restore.

        Used by delayed rollbacks (e.g. a failed development probe): the
        per-certificate restore in ``revoke_structural_accept`` is only exact
        when the revoke immediately follows the accept, so a batch rollback
        must restore one probe-time snapshot instead of replaying stale
        per-certificate priors across intervening cases.
        """
        return {
            "consecutive_no_edit": self._consecutive_no_edit,
            "converged": self._converged,
        }

    def restore_convergence(self, snapshot: Mapping[str, Any]) -> None:
        """Restore the convergence counters from a ``snapshot_convergence``."""
        self._consecutive_no_edit = max(
            0, int(snapshot.get("consecutive_no_edit") or 0)
        )
        self._converged = bool(snapshot.get("converged"))

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
        *,
        restore_streak: bool = True,
    ) -> None:
        """Convert a structural accept into a post-replay fitness rejection.

        ``restore_streak=True`` restores the certificate's accept-time
        convergence checkpoint; it is only exact when the revoke immediately
        follows the accept (same-case replay reject). Delayed batch rollbacks
        (a failed development probe spanning multiple accepted cases) must pass
        ``restore_streak=False`` and restore one probe-time snapshot via
        ``restore_convergence`` instead, otherwise intervening no-edit counting
        is silently erased or a stale high prior prematurely converges the run.
        """
        if certificate.decision != "accept":
            return
        certificate.decision = "reject"
        certificate.reject_reasons.extend(str(reason) for reason in reasons)
        self._total_edits = max(0, self._total_edits - 1)
        self._restore_last_edit_index(certificate.target_module)
        if restore_streak:
            # Structural acceptance is provisional until source replay succeeds.
            # Restore the case-level convergence checkpoint so a failed proposal
            # does not erase the preceding no-edit streak. The runner records one
            # final no-edit outcome only after all attempts for this case fail.
            self._consecutive_no_edit = certificate.prior_consecutive_no_edit
            self._converged = certificate.prior_converged

    def defer_structural_reject(self, certificate: GateCertificate) -> None:
        """Undo attempt-level convergence accounting before a bounded retry.

        The certificate remains a real rejected proposal in the audit trail, but
        convergence is defined over source cases rather than mutation attempts.
        A later retry will either reset the streak on acceptance or record
        exactly one no-edit outcome when the source is exhausted.
        """
        if certificate.decision != "reject":
            return
        self._consecutive_no_edit = certificate.prior_consecutive_no_edit
        self._converged = certificate.prior_converged

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
        rule_slots: Mapping[str, Any] | None = None,
    ) -> tuple[bool, GateCertificate, list[str]]:
        """Evaluate whether a proposed edit passes structural constraints.

        Returns ``(accepted, certificate, reject_reasons)``.
        """
        reject_reasons: list[str] = []
        prior_consecutive_no_edit = self._consecutive_no_edit
        prior_converged = self._converged

        # --- Check halt ---
        if self._halted:
            reject_reasons.append(f"halted: {self._halt_reason}")

        operation = operation.value if isinstance(operation, SkillOp) else str(operation)
        allowed_operations = {value.value for value in SkillOp}
        if operation not in allowed_operations:
            reject_reasons.append(f"invalid_operation: {operation}")

        # --- Layer 1: rule shape/authority contract ---
        # add/modify arriving from the generation path carry typed slots, and
        # the slot contract is the single shape check because the rendered text
        # is a pure function of the slots (a construction guarantee, not a
        # re-guess). The free-text path serves only remove_rule callers that
        # never reach this branch and historical checkpoint replay.
        slot_contract_active = (
            isinstance(rule_slots, Mapping)
            and set(rule_slots) == {"condition", "action", "evidence_anchor"}
            and operation in {SkillOp.ADD_RULE.value, SkillOp.MODIFY_RULE.value}
        )
        if (
            rule_slots is not None
            and not slot_contract_active
            and operation in {SkillOp.ADD_RULE.value, SkillOp.MODIFY_RULE.value}
        ):
            # A partial slot dict is a corrupt artifact, not a free-text caller:
            # the persisted-slot contract requires the exact three keys, and
            # accepting a partial dict here would admit an edit that apply_edit
            # later rejects.
            reject_reasons.append("rule_slots_incomplete")
        if operation in {SkillOp.ADD_RULE.value, SkillOp.MODIFY_RULE.value}:
            if slot_contract_active:
                assert rule_slots is not None
                spec = RuleSpec(
                    condition=str(rule_slots.get("condition") or ""),
                    action=str(rule_slots.get("action") or ""),
                    evidence_anchor=str(rule_slots.get("evidence_anchor") or ""),
                    module_id=target_module,
                    direction=_detect_rule_direction(content),
                )
                spec_ok, spec_reason = validate_rule_spec(spec)
                if not spec_ok:
                    reject_reasons.append(f"rule_spec_contract:{spec_reason}")
            else:
                valid_rule, rule_reason = validate_runtime_rule(content, target_module)
                if not valid_rule:
                    reject_reasons.append(f"runtime_rule_contract:{rule_reason}")
            if not content or len(content.strip()) < self.config.min_content_length:
                reject_reasons.append(
                    f"content_too_short: {len(content.strip())} < "
                    f"{self.config.min_content_length}"
                )

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
        # Suppressed under the slot contract: validate_rule_spec already ran the
        # forbidden-content / pollution firewall on every slot and the authority
        # checks on the rendered text, so re-reporting here would only duplicate
        # the same rejection.
        content_lower = content.lower()
        if not slot_contract_active:
            for marker in FORBIDDEN_CONTENT_MARKERS:
                if marker in content_lower:
                    reject_reasons.append(f"forbidden_content: {marker}")

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
                    baseline_rules = BASELINE_RULE_TEXT.get(
                        target_module, ()
                    )
                    if module.rules[rule_index] in baseline_rules:
                        reject_reasons.append("immutable_baseline_rule")
                    elif module.is_rule_protected(rule_index):
                        status = module.get_rule_meta(rule_index).get(
                            "fitness_status", "unknown"
                        )
                        reject_reasons.append(
                            f"protected_rule: fitness_status={status}"
                        )

        # --- Layer 5: Cross-module tiered semantic deduplication ---
        #   overlap < 0.70: accept (sufficiently distinct)
        #   0.70 <= overlap < 0.95: signal "merge_required" — the new edit
        #       should be merged into the existing rule rather than added as a
        #       standalone rule.
        #   overlap >= 0.95: reject as near-duplicate (drop the new edit)
        if operation in {"add_rule", "modify_rule"}:
            for mid in MODULE_IDS:
                mod = current_genome.modules.get(mid)
                if not mod or not mod.active:
                    continue
                # Skip self for modify_rule (the rule being modified). Skip by
                # rule_index, not by content match — the new content differs
                # from the old rule (otherwise why modify?), so the old
                # content-based skip never triggered.
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
        # Rules must NOT try to manipulate internal pipeline state variables.
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
        # detector prompts. A rule CONDITION on them is semantically inert: the
        # provider cannot observe the signal when deciding whether to fire. The
        # action slot may legitimately name the target module's own output
        # surface (charter-owned), so under the slot contract the marker check
        # applies to the condition only; the free-text path keeps the whole
        # text check as a conservative fallback.
        _diagnostic_only_markers = [
            "crs",
            "coverage_state",
            "trigger_source",
            "prosecution_coverage",
            "defense_coverage",
            "proof_projection",
        ]
        diagnostic_scope = content_lower
        if slot_contract_active and rule_slots is not None:
            diagnostic_scope = str(
                rule_slots.get("condition") or ""
            ).lower()
        for marker in _diagnostic_only_markers:
            if marker in diagnostic_scope:
                reject_reasons.append(
                    f"diagnostic_only_condition: runtime cannot observe '{marker}'"
                )
                break

        # Verdict permission is enforced in validate_runtime_rule via
        # ALLOWED_VERDICTS / LOCAL_NEGATION_MODULES (genome layer). The genome
        # check supports layered verdict permissions:
        # abstention_rule → {clean, error, unresolved}
        # clean_defense_boundary → {clean}
        # causal_link_test → local negation only
        # all others → no direct verdict

        # --- Layer 7: Rule quality — forbidden cross-module references ---
        # Rules must NOT reference other modules' rules by number/name.
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
        # Prevent too many rules starting with the same condition prefix. A
        # modify_rule must not count the rule it replaces (its prefix trivially
        # matches until the replacement lands).
        if operation in {"add_rule", "modify_rule"} and module is not None:
            prefix = content.split("，")[0].split(",")[0][:20]
            same_prefix_count = sum(
                1 for index, r in enumerate(module.rules)
                if index != rule_index
                and r.split("，")[0].split(",")[0][:20] == prefix
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
            prior_consecutive_no_edit=prior_consecutive_no_edit,
            prior_converged=prior_converged,
        )
        self.certificates.append(cert)

        return accepted, cert, reject_reasons

    def summary(self) -> dict[str, Any]:
        """Generate a summary of all gate evaluations."""
        return {
            "schema_version": "sehd_rise.evaluation.gate.v1",
            "config": {
                "module_cooldown": self.config.module_cooldown,
                "max_total_edits": self.config.max_total_edits,
                "convergence_window": self.config.convergence_window,
            },
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "convergence_signal": self._converged,
            "consecutive_no_edit": self._consecutive_no_edit,
            "module_last_edit_idx": dict(self._module_last_edit_idx),
            "total_evaluations": len(self.certificates),
            "accepted": self.accepted_count,
            "rejected": self.rejected_count,
            "total_edits_applied": self._total_edits,
            "certificates": [c.to_dict() for c in self.certificates],
        }

    def restore_summary(self, summary: Mapping[str, Any]) -> None:
        """Restore gate state from an atomically persisted summary."""
        self.certificates = [
            GateCertificate.from_dict(value)
            for value in (summary.get("certificates") or ())
            if isinstance(value, Mapping)
        ]
        self._module_last_edit_idx = {
            str(module): int(index)
            for module, index in (summary.get("module_last_edit_idx") or {}).items()
        }
        self._total_edits = int(summary.get("total_edits_applied") or 0)
        self._consecutive_no_edit = int(summary.get("consecutive_no_edit") or 0)
        self._converged = bool(summary.get("convergence_signal", False))
        self._halted = bool(summary.get("halted", False))
        self._halt_reason = str(summary.get("halt_reason") or "")


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


# ============================================================
# LLM no-conflict gate (T5)
# ============================================================

def llm_no_conflict_gate(
    new_rule: str,
    existing_rules: Sequence[str],
    *,
    target_module: str,
    mutation_route: dict[str, Any] | None,
    cache_dir: Path | None = None,
    budget_usage: dict[str, int] | None = None,
    mutation_call_limit: int | None = None,
) -> tuple[bool, str, list[int], bool]:
    """LLM no-conflict gate (T5).

    Checks whether ``new_rule`` semantically conflicts with the target module's
    existing rules. Two rules conflict when, under the same trigger conditions,
    they prescribe contradictory verdicts/actions or one directly negates the
    other. Mere condition overlap or complementarity is NOT a conflict.

    The gate runs only when a live provider credential is configured, so
    offline/test runs without a credential skip it (structural dedup still
    provides the baseline safety). When the gate runs it is fail-closed: any
    provider exception or parse failure is treated as a conflict, because a
    safety gate must never default to "pass" on uncertainty.

    Returns ``(passed, reason, conflicting_rule_indices, ran)``. ``ran`` is True
    only when the LLM was actually consulted; a skipped gate returns
    ``passed=True`` with ``ran=False`` so callers can distinguish a verified "no
    conflict" from a gate that could not run.
    """
    existing = [rule for rule in existing_rules if rule and rule.strip()]
    if not existing:
        return True, "no_existing_rules", [], False
    if mutation_route is None or not has_provider_credential(mutation_route):
        return True, "skipped_no_live_route", [], False
    system_prompt = (
        "你是规则冲突判定器。判断新增规则是否与目标模块已有规则存在语义冲突。"
        "冲突指：在相同触发条件下，两条规则给出相互矛盾的判定或动作，或一条规则"
        "直接否定另一条的结论。仅条件重叠或互补不构成冲突。"
    )
    rules_block = "\n".join(
        f"[{i}] {rule}" for i, rule in enumerate(existing)
    )
    user_prompt = (
        f"【目标模块】{target_module}\n"
        f"【已有规则】\n{rules_block}\n\n"
        f"【新增规则】{new_rule}\n\n"
        "判断新增规则是否与已有规则冲突。仅返回JSON："
        '{"decision":"pass"或"conflict","reason":"简短理由",'
        '"conflicting_rule_indices":[冲突的已有规则索引]}'
    )
    try:
        result, _meta = _run_conflict_gate_call(
            route=mutation_route,
            system=system_prompt,
            user=user_prompt,
            cache_dir=(
                cache_dir if cache_dir is not None
                else Path(".conflict_gate_cache")
            ),
            budget_usage=budget_usage,
            mutation_call_limit=mutation_call_limit,
        )
    except RuntimeError:
        # Budget exhaustion surfaces as a distinct closed reason.
        return False, "conflict_gate_budget_exhausted", [], False
    except Exception:
        if budget_usage is not None:
            budget_usage["mutation_errors"] = (
                budget_usage.get("mutation_errors", 0) + 1
            )
        return False, "provider_exception", [], True
    if budget_usage is not None and isinstance(_meta, Mapping):
        if _meta.get("cache_hit"):
            budget_usage["mutation_cache_hits"] = (
                budget_usage.get("mutation_cache_hits", 0) + 1
            )
        else:
            # A cache hit served zero network traffic: the cached record carries
            # the ORIGINAL transport attempts/repair flag, so accumulating them
            # here would inflate the counters once per replay of the same entry.
            budget_usage["mutation_transport_attempts"] = (
                budget_usage.get("mutation_transport_attempts", 0)
                + int(_meta.get("transport_attempts") or 0)
            )
            budget_usage["mutation_repairs"] = (
                budget_usage.get("mutation_repairs", 0)
                + int(bool(_meta.get("repair_used")))
            )
    if not isinstance(result, dict):
        return False, "parse_failure", [], True
    decision = str(result.get("decision", "")).strip().lower()
    reason = str(result.get("reason", "")).strip() or "llm_conflict"
    raw_indices = result.get("conflicting_rule_indices", [])
    if not isinstance(raw_indices, list):
        raw_indices = []
    indices: list[int] = []
    for value in raw_indices:
        try:
            indices.append(int(value))
        except (TypeError, ValueError):
            continue
    if decision == "pass":
        return True, reason, [], True
    return False, reason, indices, True


__all__ = [
    "GateCertificate",
    "GateConfig",
    "StructuralGate",
    "create_structural_gate",
    "llm_no_conflict_gate",
]
