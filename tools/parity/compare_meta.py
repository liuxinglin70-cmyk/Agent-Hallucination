"""Meta-layer parity: strategy-learner legality, candidate comparison, contract.

Same-process dual-package fixture-driven harness (compare_causal pattern):
one shared GateEvent sequence feeds both packages, and the strategy outputs are
compared key-by-key.

Checks:
1. StrategyEdit legality enum (plan §Phase 8 验证 3) — the closed surface:
   only enable/disable on a token that belongs to its declared domain is
   accepted; every operation/target outside the five strategy domains
   (including every v6 owner/cut/gate channel) is rejected.
2. Old-model contrast — the v6 meta skill accepted owner-guidance tokens and
   gate-profile switches; the C7 learner rejects them structurally (§2.8).
3. GateEvent-sequence candidate comparison (plan §Phase 8 验证 4) — the same
   projection fed to both packages; shared tokens match, C7 additions and the
   evidence-source rewire are documented per item (§2.8).
4. Self-evolution loop (plan §2.8) — the evidence-gated commit mechanism:
   cross-case evidence clears MIN_COMMIT_EVIDENCE to commit, shadow
   otherwise; lineage advances; disables stay manual.
5. Offline contract — schema/serialization round-trip and deterministic
   generation. The meta layer makes no provider call (§2.8), so offline
   checks are the complete verification for this phase (plan §3.3 R2 does
   not apply to a deterministic layer).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sehd_rise.experience.memory import MechanismEntry  # noqa: E402
from sehd_rise.meta import review as new_rev  # noqa: E402
from sehd_rise.meta import strategy as new_strat  # noqa: E402

from src.sehd_v6.core.bccse import (  # noqa: E402
    gate_feedback as old_gf,
    meta_review as old_mr,
    meta_skill as old_ms,
)

CHECKS: list[str] = []
FAILURES: list[str] = []
DEV: list[str] = []


def _ok(name: str) -> None:
    CHECKS.append(name)
    print(f"  ok  {name}")


def _fail(name: str, detail: str) -> None:
    FAILURES.append(name)
    print(f"  FAIL {name}: {detail}")


def _dev(name: str, detail: str) -> None:
    DEV.append(name)
    print(f"  DEV {name}: {detail}")


# ============================================================
# 1. StrategyEdit legality enum (plan §Phase 8 验证 3)
# ============================================================

# Owner / cut / repairability / hard-gate channels that lived in the v6 meta
# skill. None has a home in any C7 strategy domain, so every one is rejected
# on every domain (§2.8 "Meta 不负责").
_REMOVED_TARGETS = (
    "bind_action_to_owned_output",
    "avoid_downstream_invariant_surface",
    "avoid_unowned_output_target",
    "avoid_scope_or_ownership_mismatch",
    "owned_output_match",
    "downstream_edge_reachable",
)
_REMOVED_GATE_PROFILES = ("conservative", "steady")
_V6_OPERATIONS = (
    "add_guidance_token",
    "remove_guidance_token",
    "enable_ranking_preference",
    "disable_ranking_preference",
    "switch_gate_profile",
)


def check_strategy_edit_legality() -> None:
    """Closed vocabulary: only enable/disable of an in-domain token is valid."""
    state = new_strat.create_baseline_strategy()

    # A valid enable on each domain's first token is accepted.
    for domain in new_strat.STRATEGY_DOMAINS:
        token = next(iter(new_strat.DOMAIN_TOKENS[domain]))
        edit = new_strat.StrategyEdit(
            edit_id=f"e_{domain}", operation=new_strat.OP_ENABLE,
            domain=domain, target=token,
        )
        valid, reason = new_strat.validate_strategy_edit(edit, state)
        _ok(f"legality[enable {domain}/{token}] valid={valid} {reason}")
        if not valid:
            _fail(f"legality[enable {domain}/{token}]", reason)

    # Every v6 operation is rejected (C7 narrowed the operation set).
    for op in _V6_OPERATIONS:
        domain = new_strat.SEARCH_PRIOR
        token = next(iter(new_strat.DOMAIN_TOKENS[domain]))
        valid, reason = new_strat.validate_strategy_edit(
            new_strat.StrategyEdit("e", op, domain, token), state,
        )
        _ok(f"legality[v6 op {op}] rejected={not valid} ({reason})")
        if valid:
            _fail(f"legality[v6 op {op}]", "v6 operation accepted by C7 learner")

    # Unknown domain rejected.
    valid, reason = new_strat.validate_strategy_edit(
        new_strat.StrategyEdit("e", new_strat.OP_ENABLE, "gate_relaxation", "x"),
        state,
    )
    _ok(f"legality[unknown domain] rejected={not valid} ({reason})")
    if valid:
        _fail("legality[unknown domain]", "bogus domain accepted")

    # A token valid in one domain is rejected in every other domain.
    src_token = next(iter(new_strat.DOMAIN_TOKENS[new_strat.SEARCH_PRIOR]))
    for domain in new_strat.STRATEGY_DOMAINS:
        if domain == new_strat.SEARCH_PRIOR:
            continue
        valid, reason = new_strat.validate_strategy_edit(
            new_strat.StrategyEdit("e", new_strat.OP_ENABLE, domain, src_token),
            state,
        )
        _ok(f"legality[cross-domain {src_token} in {domain}] rejected={not valid} ({reason})")
        if valid:
            _fail(f"legality[cross-domain {domain}]", "token accepted in wrong domain")

    # Every removed owner/cut/gate target is rejected on every domain.
    for target in _REMOVED_TARGETS:
        for domain in new_strat.STRATEGY_DOMAINS:
            valid, reason = new_strat.validate_strategy_edit(
                new_strat.StrategyEdit("e", new_strat.OP_ENABLE, domain, target),
                state,
            )
            if valid:
                _fail(f"legality[{target} in {domain}]", "removed channel accepted")
        _ok(f"legality[removed {target}] rejected on all domains")

    # Gate profiles are not strategy targets on any domain.
    for profile in _REMOVED_GATE_PROFILES:
        for domain in new_strat.STRATEGY_DOMAINS:
            valid, _ = new_strat.validate_strategy_edit(
                new_strat.StrategyEdit("e", new_strat.OP_ENABLE, domain, profile),
                state,
            )
            if valid:
                _fail(f"legality[profile {profile} in {domain}]", "gate profile accepted")
        _ok(f"legality[gate profile {profile}] rejected on all domains")

    # Duplicate enable and disable-absent guards.
    domain = new_strat.MECHANISM_RANKING
    token = next(iter(new_strat.DOMAIN_TOKENS[domain]))
    enabled = new_strat.apply_strategy_edit(
        state, new_strat.StrategyEdit("e1", new_strat.OP_ENABLE, domain, token),
    )
    dup, dup_reason = new_strat.validate_strategy_edit(
        new_strat.StrategyEdit("e2", new_strat.OP_ENABLE, domain, token), enabled,
    )
    _ok(f"legality[duplicate enable] rejected={not dup} ({dup_reason})")
    if dup:
        _fail("legality[duplicate enable]", "re-enabling accepted")
    dis, dis_reason = new_strat.validate_strategy_edit(
        new_strat.StrategyEdit("e3", new_strat.OP_DISABLE, domain, token), state,
    )
    _ok(f"legality[disable absent] rejected={not dis} ({dis_reason})")
    if dis:
        _fail("legality[disable absent]", "disabling a non-enabled token accepted")


# ============================================================
# 2. Old-model contrast (C7 narrowing proof)
# ============================================================

def check_v6_contrast() -> None:
    """The v6 meta skill accepted owner guidance and gate-profile switches;
    the C7 learner rejects them structurally (§2.8)."""
    old_state = old_ms.create_baseline_meta_skill()

    # v6 accepted an owner-guidance add (bind_action_to_owned_output).
    owner_edit = old_ms.MetaEdit(
        edit_id="v6_owner", operation=old_ms.OP_ADD_GUIDANCE,
        target="bind_action_to_owned_output",
    )
    old_owner_valid, _ = old_ms.validate_meta_edit(owner_edit, old_state)
    new_owner_valid, _ = new_strat.validate_strategy_edit(
        new_strat.StrategyEdit(
            "c7_owner", new_strat.OP_ENABLE, new_strat.SEARCH_PRIOR,
            "bind_action_to_owned_output",
        ),
        new_strat.create_baseline_strategy(),
    )
    _dev(
        "owner channel narrowed (§2.8: Meta 不负责 当前 failure owner)",
        f"v6 add_guidance bind_action_to_owned_output valid={old_owner_valid} "
        f"-> C7 rejected={not new_owner_valid}",
    )
    if not old_owner_valid:
        _fail("contrast[owner]", "v6 did not accept owner guidance")
    if new_owner_valid:
        _fail("contrast[owner]", "C7 accepted owner guidance")

    # v6 accepted a gate-profile switch; C7 has no profile target at all.
    profile_edit = old_ms.MetaEdit(
        edit_id="v6_profile", operation=old_ms.OP_SWITCH_PROFILE,
        target=old_ms.PROFILE_STEADY,
    )
    old_profile_valid, _ = old_ms.validate_meta_edit(profile_edit, old_state)
    new_profile_any = False
    for domain in new_strat.STRATEGY_DOMAINS:
        valid, _ = new_strat.validate_strategy_edit(
            new_strat.StrategyEdit("c7_profile", new_strat.OP_ENABLE, domain, "steady"),
            new_strat.create_baseline_strategy(),
        )
        new_profile_any = new_profile_any or valid
    _dev(
        "gate channel narrowed (§2.8: Meta 不负责 hard gate relaxation)",
        f"v6 switch_gate_profile steady valid={old_profile_valid} "
        f"-> C7 rejected={not new_profile_any}",
    )
    if not old_profile_valid:
        _fail("contrast[gate]", "v6 did not accept profile switch")
    if new_profile_any:
        _fail("contrast[gate]", "C7 accepted a gate profile")


# ============================================================
# 3. GateEvent-sequence candidate comparison (plan §Phase 8 验证 4)
# ============================================================

def _gate_event(
    event_id: str, *, quality: tuple[str, ...] = (), hard: tuple[str, ...] = (),
) -> old_gf.GateEvent:
    return old_gf.GateEvent(
        schema_version="sehd.gate_event.v1",
        event_id=event_id,
        subject_id=f"subj_{event_id}",
        episode_id=f"ep_{event_id}",
        attempt_id=f"att_{event_id}",
        layer="l1",
        artifact="error_certificate",
        operation="add_rule",
        stage="structural",
        legacy_decision="reject",
        evidence_grade="conditional",
        activation_state="",
        hard_reject_codes=hard,
        quality_reason_codes=quality,
    )


def _mechanism_entry(
    sig: tuple[str, ...], *, effective: bool = False, no_effect: bool = False,
    structural_reject: bool = False,
) -> MechanismEntry:
    reps: dict[str, Any] = {}
    outcome_counts: dict[str, int] = {}
    if effective:
        reps["effective"] = {
            "completeness": "complete", "evidence_fields": 3, "sequence": 1,
            "episode_id": "ep_eff", "causal_slice": {}, "action_spec": {},
            "outcome": {"decision": "accept"}, "rule_text": "",
        }
        outcome_counts["accept"] = 1
    if no_effect:
        reps["no_effect"] = {
            "completeness": "partial", "evidence_fields": 1, "sequence": 1,
            "episode_id": "ep_noeffect", "causal_slice": {}, "action_spec": {},
            "outcome": {"decision": "no_edit"}, "rule_text": "",
        }
        outcome_counts["no_edit"] = 1
    if structural_reject:
        reps["structural_reject"] = {
            "completeness": "partial", "evidence_fields": 2, "sequence": 1,
            "episode_id": "ep_reject", "causal_slice": {}, "action_spec": {},
            "outcome": {"decision": "reject"}, "rule_text": "",
        }
        outcome_counts["reject"] = 1
    return MechanismEntry.from_dict({
        "signature": list(sig), "observation_count": sum(outcome_counts.values()),
        "outcome_counts": outcome_counts, "representatives": reps,
        "counted_episode_ids": [], "last_sequence": 1,
    })


def _old_candidates(
    events: list[old_gf.GateEvent], *, exact_false_reject: bool = False,
) -> set[tuple[str, str]]:
    experiences = [
        old_mr.aggregate_gate_experience(event, sequence=event.sequence)
        for event in events
    ]
    validations = []
    if exact_false_reject:
        validations.append(old_mr.OutcomeValidation(
            outcome=old_mr.OUTCOME_EXACT_FALSE_REJECT,
            gate_event_id=events[0].event_id,
            validation_event_id="val_1",
        ))
    return {
        (edit.operation, edit.target)
        for edit in old_mr.generate_meta_edit_candidates(
            experiences, validations, old_ms.create_baseline_meta_skill(),
        )
    }


def _new_candidates(
    events: list[old_gf.GateEvent], outcomes: list[MechanismEntry],
) -> set[tuple[str, str]]:
    old_experiences = [
        old_mr.aggregate_gate_experience(event, sequence=event.sequence)
        for event in events
    ]
    new_experiences = [
        new_rev.GateExperience.from_dict(exp.to_dict()) for exp in old_experiences
    ]
    return {
        (edit.domain, edit.target)
        for edit in new_rev.generate_strategy_suggestions(
            new_experiences, outcomes, new_strat.create_baseline_strategy(),
        )
    }


def check_gate_event_sequence_comparison() -> None:
    """Same GateEvent sequence -> comparable candidate suggestions; C7
    additions and the evidence-source rewire documented per item (§2.8)."""
    sig = ("l1", "error_certificate", "structural", "add_rule")

    # A: policy_route_unavailable feedback -> both propose prefer_registered_runtime_route.
    events_a = [_gate_event("a1", quality=("route_unavailable",))]
    old_a = _old_candidates(events_a)
    new_a = _new_candidates(events_a, [])
    shared_a = ("operation_preference", "prefer_registered_runtime_route")
    _ok(f"seq[A route_unavailable] old has token={'prefer_registered_runtime_route' in {t for _, t in old_a}}")
    _ok(f"seq[A route_unavailable] new has token={shared_a in new_a}")
    if shared_a not in new_a:
        _fail("seq[A]", "new lost the shared route token")

    # B: registry_entry_pending feedback -> both propose avoid_pending_registry_dependency.
    events_b = [_gate_event("b1", quality=("registry_pending",))]
    old_b = _old_candidates(events_b)
    new_b = _new_candidates(events_b, [])
    shared_b = ("operation_preference", "avoid_pending_registry_dependency")
    _ok(f"seq[B registry_pending] old has token={'avoid_pending_registry_dependency' in {t for _, t in old_b}}")
    _ok(f"seq[B registry_pending] new has token={shared_b in new_b}")
    if shared_b not in new_b:
        _fail("seq[B]", "new lost the shared registry token")

    # C: exact_false_reject (v6) + effective representative (C7) -> both propose
    #    action_shape_previously_effective; new also proposes reuse_effective_action_shape.
    events_c = [_gate_event("c1", quality=("grounding",))]
    old_c = _old_candidates(events_c, exact_false_reject=True)
    new_c = _new_candidates(events_c, [_mechanism_entry(sig, effective=True)])
    shared_c = ("mechanism_ranking", "action_shape_previously_effective")
    _ok(f"seq[C effective] old has action_shape_previously_effective={'action_shape_previously_effective' in {t for _, t in old_c}}")
    _ok(f"seq[C effective] new has action_shape_previously_effective={shared_c in new_c}")
    if shared_c not in new_c:
        _fail("seq[C]", "new lost the shared effective ranking token")
    _dev(
        "evidence source rewired (§2.8: 从多次已验证的 RepairPlan outcome 中学习)",
        "v6 backed action_shape_previously_effective with an exact_false_reject "
        "OutcomeValidation; C7 backs it with a verified effective representative "
        "from experience/memory.MechanismEntry",
    )
    if ("retry_strategy", "reuse_effective_action_shape") not in new_c:
        _dev("seq[C] C7 addition reuse_effective_action_shape", "absent")
    else:
        _dev(
            "seq[C] C7 addition reuse_effective_action_shape (§2.8 retry strategy)",
            "new proposes an outcome-backed retry token v6 did not",
        )

    # D: no_effect representative -> C7 proposes no_effect ranking + retry-avoid;
    #    v6 has no no-effect mapping.
    events_d = [_gate_event("d1", quality=("grounding",))]
    new_d = _new_candidates(events_d, [_mechanism_entry(sig, no_effect=True)])
    _dev(
        "seq[D] C7 outcome-backed additions (§2.8 mechanism ranking / retry)",
        f"new proposes action_shape_previously_no_effect="
        f"{('mechanism_ranking', 'action_shape_previously_no_effect') in new_d} "
        f"avoid_action_shape_previously_failed="
        f"{('retry_strategy', 'avoid_action_shape_previously_failed') in new_d} "
        "v6 had no no-effect evidence path",
    )

    # E: redundant_intervention feedback -> C7 proposes avoid_redundant_intervention;
    #    v6 COMMIT_EVIDENCE_SOURCES had no redundant mapping.
    events_e = [_gate_event("e1", quality=("duplicate",))]
    new_e = _new_candidates(events_e, [])
    _dev(
        "seq[E] C7 feedback-backed addition avoid_redundant_intervention (§2.8)",
        f"new proposes={('operation_preference', 'avoid_redundant_intervention') in new_e} "
        "v6 had no redundant-intervention evidence path",
    )


# ============================================================
# 4. Offline contract (R2) — schema / serialization / determinism
# ============================================================

def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def check_offline_contract() -> None:
    """Schema/serialization round-trip and deterministic generation (R2)."""
    base = new_strat.create_baseline_strategy()
    domain = new_strat.RETRY_STRATEGY
    token = next(iter(new_strat.DOMAIN_TOKENS[domain]))
    evolved = new_strat.apply_strategy_edit(
        base, new_strat.StrategyEdit("e1", new_strat.OP_ENABLE, domain, token),
    )

    # State round-trip: content_hash and enabled set stable.
    rt = new_strat.StrategyState.from_dict(evolved.to_dict())
    _ok(f"contract[state roundtrip] hash_stable={rt.content_hash == evolved.content_hash}")
    if rt.content_hash != evolved.content_hash:
        _fail("contract[state roundtrip]", "content_hash changed across round-trip")
    if rt.enabled != evolved.enabled:
        _fail("contract[state roundtrip]", "enabled set changed across round-trip")

    # Edit round-trip.
    edit = evolved.edits_applied[0]
    rt_edit = new_strat.StrategyEdit.from_dict(edit.to_dict())
    _ok(f"contract[edit roundtrip] stable={_canon(rt_edit.to_dict()) == _canon(edit.to_dict())}")
    if _canon(rt_edit.to_dict()) != _canon(edit.to_dict()):
        _fail("contract[edit roundtrip]", "edit drifted across round-trip")

    # GateExperience round-trip.
    ge = new_rev.GateExperience(
        gate_event_id="g1", mechanism_signature=("l1", "art", "s", "op"),
        layer="l1", artifact="art", stage="s", evidence_grade="conditional",
        activation_state="", feedback_codes=(new_rev.FEEDBACK_REGISTRY_ENTRY_PENDING,),
        execution_trace_class="compiled",
    )
    rt_ge = new_rev.GateExperience.from_dict(ge.to_dict())
    _ok(f"contract[gate_experience roundtrip] stable={_canon(rt_ge.to_dict()) == _canon(ge.to_dict())}")
    if _canon(rt_ge.to_dict()) != _canon(ge.to_dict()):
        _fail("contract[gate_experience roundtrip]", "drifted across round-trip")

    # Deterministic generation: same inputs -> same candidate ids and keys.
    exps = [new_rev.GateExperience.from_dict(ge.to_dict())]
    outcomes = [_mechanism_entry(("l1", "art", "s", "op"), effective=True, no_effect=True)]
    first = new_rev.generate_strategy_suggestions(exps, outcomes, base)
    second = new_rev.generate_strategy_suggestions(exps, outcomes, base)
    first_keys = [(e.domain, e.target) for e in first]
    second_keys = [(e.domain, e.target) for e in second]
    _ok(f"contract[deterministic generation] keys_stable={first_keys == second_keys}")
    if first_keys != second_keys:
        _fail("contract[deterministic generation]", "candidate order/keys not deterministic")
    first_ids = [e.edit_id for e in first]
    second_ids = [e.edit_id for e in second]
    _ok(f"contract[deterministic generation] ids_stable={first_ids == second_ids}")
    if first_ids != second_ids:
        _fail("contract[deterministic generation]", "candidate ids not deterministic")

    # Prompt projection stays guidance-only and gate-free.
    projection = new_rev.project_strategy_for_prompt(evolved)
    _ok(f"contract[projection authority] = {projection['authority']}")
    if projection["authority"] != "evolution_guidance_only":
        _fail("contract[projection authority]", "authority widened")
    if "gate_profile" in projection:
        _fail("contract[projection authority]", "gate profile leaked into projection")
    else:
        _ok("contract[projection] gate_profile absent (C7 §2.8)")


def check_strategy_self_evolution() -> None:
    """The evidence-gated commit loop (plan §2.8 self-evolution).

    A candidate commits only when cross-case evidence clears the threshold
    (MIN_COMMIT_EVIDENCE=3), else it stays in shadow; a committed edit
    advances the immutable lineage. A disable stays manual. This is the
    deterministic self-evolution mechanism — the heart of the Strategy
    Learner — and it needs no provider call (§2.8).
    """
    base = new_strat.create_baseline_strategy()
    experiences_empty: list[new_rev.GateExperience] = []

    # Outcome-backed: 3 mechanisms each with a verified effective
    # representative -> action_shape_previously_effective clears the gate.
    outcomes_full = [
        _mechanism_entry(("l1", "cert", "structural", f"op{i}"), effective=True)
        for i in range(3)
    ]
    candidates_full = new_rev.generate_strategy_suggestions(
        experiences_empty, outcomes_full, base,
    )
    evolved, audit_full = new_rev.commit_validated_strategy_edits(
        base, candidates_full, experiences_empty, outcomes_full,
    )
    eff = next(
        (r for r in audit_full
         if r["domain"] == "mechanism_ranking"
         and r["target"] == "action_shape_previously_effective"),
        None,
    )
    committed = eff is not None and eff["committed"]
    _ok(
        f"self_evolve[effective x3] committed={committed} "
        f"observed={eff['evidence_observed'] if eff else '?'}"
    )
    if not committed:
        _fail("self_evolve[effective x3]", f"did not commit: {eff}")

    # Lineage advanced: generation +1, content_hash changed, token enabled.
    _ok(
        f"self_evolve[lineage] gen={evolved.generation} "
        f"hash_changed={evolved.content_hash != base.content_hash} "
        f"enabled={sorted(evolved.enabled_in('mechanism_ranking'))}"
    )
    if evolved.generation <= base.generation:
        _fail("self_evolve[lineage]", "generation did not advance")
    if "action_shape_previously_effective" not in evolved.enabled_in(
        new_strat.MECHANISM_RANKING
    ):
        _fail("self_evolve[lineage]", "token not enabled after commit")

    # Insufficient evidence: 1 mechanism -> proposed but stays in shadow.
    outcomes_one = [_mechanism_entry(("l1", "cert", "structural", "op0"), effective=True)]
    candidates_one = new_rev.generate_strategy_suggestions(
        experiences_empty, outcomes_one, base,
    )
    shadow_state, audit_one = new_rev.commit_validated_strategy_edits(
        base, candidates_one, experiences_empty, outcomes_one,
    )
    one = next(
        (r for r in audit_one if r["target"] == "action_shape_previously_effective"),
        None,
    )
    shadowed = one is not None and not one["committed"]
    _ok(
        f"self_evolve[effective x1] shadowed={shadowed} "
        f"reason={one['blocked_reason'] if one else '?'}"
    )
    if not shadowed:
        _fail("self_evolve[effective x1]", "committed below threshold")
    else:
        if one["blocked_reason"] != "insufficient_evidence":
            _fail("self_evolve[effective x1]", f"wrong reason: {one['blocked_reason']}")
    if shadow_state.content_hash != base.content_hash:
        _fail("self_evolve[effective x1]", "state mutated on a shadow rejection")

    # Feedback-backed: 3 same-mechanism experiences with policy_route_unavailable
    # -> prefer_registered_runtime_route clears the per-mechanism gate.
    sig_b = ("l1", "cert", "structural", "add_rule")
    experiences_fb = [
        new_rev.GateExperience(
            gate_event_id=f"fb{i}", mechanism_signature=sig_b, layer="l1",
            artifact="cert", stage="structural", evidence_grade="conditional",
            activation_state="",
            feedback_codes=(new_rev.FEEDBACK_POLICY_ROUTE_UNAVAILABLE,),
            execution_trace_class="compiled",
        )
        for i in range(3)
    ]
    candidates_fb = new_rev.generate_strategy_suggestions(experiences_fb, [], base)
    _, audit_fb = new_rev.commit_validated_strategy_edits(
        base, candidates_fb, experiences_fb, [],
    )
    route = next(
        (r for r in audit_fb if r["target"] == "prefer_registered_runtime_route"),
        None,
    )
    _ok(
        f"self_evolve[route x3 same-mech] committed={route is not None and route['committed']} "
        f"observed={route['evidence_observed'] if route else '?'}"
    )
    if route is None or not route["committed"]:
        _fail("self_evolve[route x3]", f"did not commit: {route}")

    # A disable is never auto-committed (lineage-visible policy decision).
    enabled = new_strat.apply_strategy_edit(
        base, new_strat.StrategyEdit(
            "e1", new_strat.OP_ENABLE, new_strat.RETRY_STRATEGY,
            "reuse_effective_action_shape",
        ),
    )
    disable_edit = new_strat.StrategyEdit(
        "d1", new_strat.OP_DISABLE, new_strat.RETRY_STRATEGY,
        "reuse_effective_action_shape",
    )
    _, audit_dis = new_rev.commit_validated_strategy_edits(
        enabled, [disable_edit], experiences_empty, [],
    )
    dis = audit_dis[0]
    _ok(f"self_evolve[disable] manual_only={not dis['committed']} reason={dis['blocked_reason']}")
    if dis["committed"]:
        _fail("self_evolve[disable]", "auto-commit accepted a disable")
    if dis["blocked_reason"] != "operation_requires_manual_review":
        _fail("self_evolve[disable]", f"wrong reason: {dis['blocked_reason']}")


def main() -> int:
    print("== compare_meta ==")
    print("R2 note: meta layer is deterministic (no provider call, §2.8); "
          "warm-cache availability does not affect Phase 8 verification. "
          "Offline contract checks (legality / candidate / serialization / "
          "self-evolution) are the complete verification, recorded per §4.6.")
    check_strategy_edit_legality()
    print()
    check_v6_contrast()
    print()
    check_gate_event_sequence_comparison()
    print()
    check_offline_contract()
    print()
    check_strategy_self_evolution()
    print()
    print(
        f"{len(CHECKS)} checks passed, {len(FAILURES)} failed, "
        f"{len(DEV)} deviations documented"
    )
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
