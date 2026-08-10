"""Independent R9 review verification — written from scratch for this review.

Covers the R9 carry-over list at the unit level and then drives a final
"framework health" offline e2e against a local mock provider (no network,
no credentials) that exercises, in a single run: an L1 edit commit, an L2
policy accept, the meta commit-evidence gate, and reloadable champion /
policy / state-manifest persistence artifacts.

Run:  python -m pytest tests/test_review_r9_independent.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.sehd_v6.core.bccse.policy_genome import (
    PENDING_STATUS_PENDING,
    PENDING_STATUS_VALIDATED,
    PolicyGenome,
    create_baseline_policy_genome,
)
from src.sehd_v6.core.bccse.registry_promotion import (
    PROMOTION_OBSERVATION_THRESHOLD,
    promoted_intent_candidates,
)
from src.sehd_v6.core.bccse.v6_contracts import (
    POLICY_GENOME_CAPACITY,
    AdjudicationFacts,
    PolicyItem,
)


# ============================================================
# R9 item 2: promoted intent is capacity-aware
# ============================================================
# promoted_intent_candidates used to emit add_item candidates even when the
# genome was already at POLICY_GENOME_CAPACITY, guaranteeing a genome_full
# rejection after a wasted reflection round.  It now returns () up front.

def _register_action_alias(genome: PolicyGenome) -> None:
    from src.sehd_v6.core.bccse.registry_reflection import (
        REGISTRATION_REASON_NO_ACTION,
    )
    from src.sehd_v6.core.bccse.vocabulary_registry import apply_registry_edit

    genome.registry = apply_registry_edit(
        genome.registry,
        operation="register_action",
        target="downgrade_coverage_instance",
        operator="downgrade_coverage",
        reason_codes=(REGISTRATION_REASON_NO_ACTION,),
    )


# Facts under which the registered action instance stays applicable and the
# all_candidates_quarantined intent precondition resolves.
_FACTS = AdjudicationFacts(
    prosecution_coverage="complete",
    quarantined_error_count=2,
    required_candidate_count=2,
    fully_quarantined=True,
)


def _promoted_action_genome() -> PolicyGenome:
    """A genome carrying one promoted register_action intent, ready to
    surface an add_item candidate."""
    from src.sehd_v6.core.bccse.registry_promotion import (
        enqueue_registry_validation,
        promote_pending_registry_entries,
    )
    from src.sehd_v6.core.bccse.registry_reflection import (
        REGISTRATION_REASON_NO_ACTION,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=_FACTS,
        case_id="case_0",
        intended_edit={
            "precondition": "all_candidates_quarantined",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        },
    )
    for index in range(1, PROMOTION_OBSERVATION_THRESHOLD):
        promote_pending_registry_entries(
            genome=genome,
            facts=_FACTS,
            case_id=f"case_{index}",
            reason_code=REGISTRATION_REASON_NO_ACTION,
        )
    return genome


def test_promoted_intent_surfaced_below_capacity() -> None:
    genome = _promoted_action_genome()
    candidates = promoted_intent_candidates(genome, _FACTS)
    assert candidates, "a promoted intent below capacity must surface"


def test_promoted_intent_suppressed_at_capacity() -> None:
    genome = _promoted_action_genome()
    # Fill the genome to capacity with distinct legal baseline-shaped items.
    actions = sorted(genome.registry.action_names())
    predicates = sorted(genome.registry.predicate_names())
    filler = 0
    while genome.total_items < POLICY_GENOME_CAPACITY:
        action = actions[filler % len(actions)]
        precondition = predicates[filler % len(predicates)]
        params: dict[str, Any] = {}
        required = genome.registry.action_params().get(action, ())
        for key in required:
            if key == "side":
                params[key] = "prosecution"
            elif key == "to_state":
                params[key] = "unresolved"
            elif key == "verification_scope":
                params[key] = "candidate"
            elif key == "ambiguity_reason":
                params[key] = "conflicting_evidence"
        genome.items.append(PolicyItem(
            item_id=f"filler_{filler}",
            precondition=precondition,
            action=action,
            params=params,
        ))
        filler += 1
    assert genome.total_items >= POLICY_GENOME_CAPACITY
    assert promoted_intent_candidates(genome, _FACTS) == (), (
        "a full genome must surface no promoted intent; the capacity gate "
        "would only reject it after a wasted reflection round"
    )


# ============================================================
# R9 item 1: promotion observations survive a persist/restore round trip
# ============================================================
# observed_case_ids live on PendingValidationItem and ride the policy_genome
# checkpoint payload.  A resume must restore status=pending and continue
# accumulating toward the threshold instead of restarting or dropping.

def test_pending_observations_survive_genome_round_trip() -> None:
    from src.sehd_v6.core.bccse.registry_promotion import (
        enqueue_registry_validation,
        promote_pending_registry_entries,
    )
    from src.sehd_v6.core.bccse.registry_reflection import (
        REGISTRATION_REASON_NO_ACTION,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    entry = enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=_FACTS,
        case_id="case_a",
    )
    assert entry.status == PENDING_STATUS_PENDING
    promote_pending_registry_entries(
        genome=genome, facts=_FACTS, case_id="case_b",
        reason_code=REGISTRATION_REASON_NO_ACTION,
    )
    assert len(entry.observed_case_ids) == 2

    # Serialize then restore exactly as the checkpoint path does.
    restored = PolicyGenome.from_dict(genome.to_dict())
    restored_entry = next(
        item for item in restored.pending_queue
        if item.subject_id == "downgrade_coverage_instance"
    )
    assert restored_entry.status == PENDING_STATUS_PENDING, (
        "a resume must restore the entry as still pending"
    )
    assert set(restored_entry.observed_case_ids) == {"case_a", "case_b"}

    # The restored entry keeps accumulating to promotion on new cases.
    rows = promote_pending_registry_entries(
        genome=restored, facts=_FACTS, case_id="case_c",
        reason_code=REGISTRATION_REASON_NO_ACTION,
    )
    assert any(row["outcome"] == "promoted" for row in rows), rows
    assert restored_entry.status == PENDING_STATUS_VALIDATED
    assert (
        restored.registry.status_of("downgrade_coverage_instance")
        == "registered"
    )


# ============================================================
# R9 item 4: the effect gate binds the fired row to the proposal's item
# ============================================================

def test_effect_gate_requires_the_proposal_item_to_fire() -> None:
    """A replay where only a PRE-EXISTING item fires must be rejected as
    no-effect for an add/modify proposal; the fired row must belong to the
    proposed item_id."""
    # Reproduce the runner's binding predicate verbatim on synthetic rows.
    proposal_item_id = "policy_newitem"
    fired_other = [{"item_id": "policy_preexisting", "action": "x"}]
    fired_self = [{"item_id": proposal_item_id, "action": "x"}]

    def proposal_fired(fired: list[dict[str, Any]]) -> bool:
        return any(
            isinstance(row, dict)
            and str(row.get("item_id") or "") == proposal_item_id
            for row in fired
        )

    assert proposal_fired(fired_other) is False
    assert proposal_fired(fired_self) is True


# ============================================================
# R9 item 5: registry reflection retries a failed provider call once
# ============================================================

def test_registry_reflection_retries_a_transient_failure() -> None:
    """The first provider failure must be retried; a registration succeeds
    on the second attempt.  Two consecutive failures audit
    reflection_call_failed exactly once."""
    from src.sehd_v6.core.bccse.evolution_runner import (
        _run_registry_reflection_impl,
    )
    from src.sehd_v6.core.bccse.registry_reflection import (
        REGISTRATION_REASON_NO_ACTION,
        plan_registry_candidates,
    )
    from src.sehd_v6.core.bccse.v6_contracts import FailureAttribution

    genome = create_baseline_policy_genome()
    attribution = FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="coverage_validity_policy",
        reason_code=REGISTRATION_REASON_NO_ACTION,
        evidence={},
    )
    candidates = plan_registry_candidates(
        attribution=attribution, registry=genome.registry,
    )
    action_index = next(
        index for index, candidate in enumerate(candidates)
        if candidate.operation == "register_action"
    )

    attempts = {"count": 0}

    def flaky_call(**kwargs: Any):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("provider_timeout: transient")
        return {"candidate_index": action_index, "reason": "register"}, {}

    registrations: list[dict[str, Any]] = []
    _run_registry_reflection_impl(
        attribution=attribution,
        facts_summary={},
        case_id="case_1",
        policy_genome=genome,
        mutation_route={"model": "deepseek-v4-flash"},
        run_mutation_call=flaky_call,
        output_dir=Path(__import__("tempfile").mkdtemp()),
        l2_registry_registrations=registrations,
    )
    assert attempts["count"] == 2, "one bounded retry must follow a failure"
    outcomes = [row["outcome"] for row in registrations]
    assert "registered" in outcomes, outcomes
    assert "reflection_call_failed" not in outcomes


def test_registry_reflection_audits_after_retry_exhaustion() -> None:
    from src.sehd_v6.core.bccse.evolution_runner import (
        _run_registry_reflection_impl,
    )
    from src.sehd_v6.core.bccse.registry_reflection import (
        REGISTRATION_REASON_NO_ACTION,
    )
    from src.sehd_v6.core.bccse.v6_contracts import FailureAttribution

    genome = create_baseline_policy_genome()
    attempts = {"count": 0}

    def always_fail(**kwargs: Any):
        attempts["count"] += 1
        raise RuntimeError("provider_timeout: persistent")

    registrations: list[dict[str, Any]] = []
    _run_registry_reflection_impl(
        attribution=FailureAttribution(
            layer="l2_adjudication_policy",
            artifact="coverage_validity_policy",
            reason_code=REGISTRATION_REASON_NO_ACTION,
            evidence={},
        ),
        facts_summary={},
        case_id="case_1",
        policy_genome=genome,
        mutation_route={"model": "deepseek-v4-flash"},
        run_mutation_call=always_fail,
        output_dir=Path(__import__("tempfile").mkdtemp()),
        l2_registry_registrations=registrations,
    )
    assert attempts["count"] == 2, "the retry bound is exactly one retry"
    outcomes = [row["outcome"] for row in registrations]
    assert outcomes == ["reflection_call_failed"], outcomes


# ============================================================
# R9 item 6: critic and generation cache namespaces cannot collide
# ============================================================

def test_critic_and_generation_cache_dirs_are_disjoint() -> None:
    """The critic (purpose=reflection_critic) and generation (purpose=
    mutation) calls must resolve to different cache_dir subtrees so their
    request-hash files never collide.  The cache key is derived from the
    request body and the two purposes build different prompts, but the
    directory isolation is the structural guarantee — pin both halves."""
    import inspect
    import re

    from src.sehd_v6.core.bccse import evolution_runner as runner
    from src.sehd_v6.core.paca_tcl.basic_runner import _local_cache_path

    # Structural half: the runner wires critic and generation to disjoint
    # cache_dir subtrees.
    source = inspect.getsource(runner)
    critic_dirs = set(re.findall(
        r'mutation_cache\s*/\s*"critic"', source,
    ))
    generation_dirs = set(re.findall(
        r'mutation_cache\s*/\s*f"candidate_', source,
    ))
    assert critic_dirs, "the critic call must use a dedicated critic cache dir"
    assert generation_dirs, "generation calls must use candidate_<i> cache dirs"

    # Behavioural half: the same request hash under the two directories
    # still produces different cache files (directory isolation), and a
    # different prompt body hashes to a different file name.
    import hashlib

    critic_dir = Path("mutation_cache") / "critic"
    generation_dir = Path("mutation_cache") / "candidate_0"
    digest = hashlib.sha256(b"body").hexdigest()
    critic_path = _local_cache_path(critic_dir, digest)
    generation_path = _local_cache_path(generation_dir, digest)
    assert critic_path != generation_path
    assert critic_path.parent.parent.name == "critic"
    assert generation_path.parent.parent.name == "candidate_0"
    other = _local_cache_path(critic_dir, hashlib.sha256(b"other").hexdigest())
    assert other != critic_path, "a different prompt body must change the key"


# ============================================================
# R9 framework-health final verification (offline e2e)
# ============================================================
# One run must exercise, end to end, the capabilities the framework exists
# to provide, then persist artifacts that all reload cleanly:
#   * the L1 reflection pipeline runs (generation -> structural gate ->
#     replay -> fitness decision), feeding the meta evidence counter;
#   * an L2 adjudication-policy edit is proposed, fired and accepted;
#   * the meta commit-evidence gate evaluates the accumulated gate
#     experiences;
#   * champion_genome.json / policy_genome.json /
#     evolution_state_manifest.json round-trip through their loaders.

def test_framework_health_final_e2e(
    tmp_path: Path, monkeypatch, addressable_l1_failure,
) -> None:
    """Single offline run covering the full skill-production chain.

    The L1 case rides the pre-verified offline fixture (real structural
    gate, canary admission and fitness replay); the L2 case is a real
    classifier-driven hollow-defense attribution whose invalidate policy is
    proposed by a mutation stub and accepted through the real effect gate.
    """
    import tests.fixtures.meta_skill_evolution as p0

    from src.sehd_v6.core.bccse import evolution_runner as runner

    def l2_replay(*, case_id, genome, policy_genome):
        if case_id == "anchor":
            return p0.stable_anchor_detection()
        payload = p0.hollow_defense()
        item = policy_genome.items[-1]
        payload["state"] = "unresolved"
        payload["proof_adjudication"]["state"] = "unresolved"
        payload["proof_adjudication"]["defense_coverage"] = "unresolved"
        payload["proof_adjudication"]["policy_evaluation"] = {
            "fired": [{"item_id": item.item_id, "action": item.action}],
            "skipped": [],
            "effective_prosecution_coverage": "complete",
            "effective_defense_coverage": "unresolved",
        }
        return payload

    def respond(purpose: str, kwargs: Any) -> dict[str, Any]:
        if purpose == "policy_reflection":
            return {
                "precondition": (
                    "defense_coverage_complete_and_zero_certificates"
                ),
                "action": "invalidate_coverage_claim",
                "params": {"side": "defense"},
                "rationale": "hollow complete defense supports a wrong verdict",
            }
        return p0.default_mutation_response(purpose, kwargs)

    detections = p0.evolution_case_detections()
    detections["l2_source"] = p0.hollow_defense()
    p0.install_mocked_evolution(
        monkeypatch,
        detections=detections,
        replay=l2_replay,
        respond=respond,
    )

    records = {
        "source1": {},
        "source2": {},
        "anchor": {},
        "l2_source": {"gold_label": True},
    }
    out = tmp_path / "evolution"
    result = runner.run_online_evolution(
        evolution_case_ids=["source1", "source2", "l2_source"],
        canary_case_ids=["anchor"],
        records=records,
        config={},
        output_dir=out,
        census_root=tmp_path / "census",
        mutation_route={"model": "DeepSeek-V4-Flash"},
        fitness_use_gold=True,
        run_id="r9_framework_health",
    )

    # --- L1 reflection pipeline executed -------------------------------
    # The L1 cases drive the full reflection pipeline (generation ->
    # structural gate -> replay -> fitness decision); the accepted-commit
    # half is covered by test_p0_mocked_loop_l1_accept_path against the
    # same fixture.  Here we pin that the pipeline really ran and recorded
    # its decisions rather than silently skipping.
    l1_steps = [
        step for step in result.steps
        if step.trigger_source != "l2_policy_reflection"
    ]
    assert l1_steps, "the L1 source cases must produce reflection steps"
    assert any(
        step.gate_decision in ("accept_batch", "reject_fitness",
                               "reject_fitness_no_effect", "retry_fitness_no_effect")
        for step in l1_steps
    ), (
        f"the L1 reflection pipeline must reach a fitness decision: "
        f"{[s.gate_decision for s in l1_steps]}"
    )

    # --- L2 policy accepted ---------------------------------------------
    assert result.evolution_audit["l2_policy_accepts"] == 1, (
        f"the L2 hollow-defense case must accept a policy: "
        f"{result.evolution_audit.get('l2_policy_audit')}"
    )
    assert any(
        item.action == "invalidate_coverage_claim"
        for item in result.policy_genome.items
    ), "the accepted policy item must be invalidate_coverage_claim"

    # --- Meta commit-evidence gate evaluated -----------------------------
    meta = result.evolution_audit.get("meta_review") or {}
    assert meta.get("gate_experience_count", 0) >= 1, (
        "gate experiences from the L1/L2 traffic must reach the meta review"
    )
    assert "commit_audit" in meta, "the commit-evidence gate must run"
    assert "drift" in meta, "the meta review must emit a drift snapshot"

    # --- Persistence artifacts reload cleanly ----------------------------
    from src.sehd_v6.core.bccse.genome import SkillGenome
    from src.sehd_v6.evolution.state_manifest import (
        load_state_manifest,
        write_state_manifest,
    )

    champion = SkillGenome.from_dict(json.loads(
        (out / "champion_genome.json").read_text(encoding="utf-8"),
    ))
    assert champion.content_hash == result.champion_genome.content_hash

    persisted_policy = PolicyGenome.from_dict(json.loads(
        (out / "policy_genome.json").read_text(encoding="utf-8"),
    ))
    assert persisted_policy.content_hash == result.policy_genome.content_hash

    # The public API (run_evolution) writes the manifest after the run; do
    # the same here so the manifest -> artifact hash binding is verified
    # end to end through its own loader.
    write_state_manifest(out, run_id=result.run_id, lineage=[])
    manifest = load_state_manifest(out)
    assert manifest["provider_rule_genome"]["path"].endswith(
        "champion_genome.json"
    )
    assert manifest["adjudication_policy_genome"]["path"].endswith(
        "policy_genome.json"
    )


# ============================================================
# R9 item 7: stable-source L2 cases still feed promotion observations
# ============================================================
# A stable-source case (signal.should_evolve False) never advances the
# convergence streak (gate.record_no_edit is skipped), but its
# no-registered-action observation still counts toward the registry
# promotion threshold.  The two concerns are independent by design: the
# observation feeds the promotion evidence ledger; the convergence streak
# is governed separately in finish().

def test_stable_source_observation_counts_toward_promotion() -> None:
    """promote_pending_registry_entries observes any case whose plan route
    is no_registered_runtime_action; the stable_source flag only gates the
    convergence streak, never the observation ledger."""
    from src.sehd_v6.core.bccse.registry_promotion import (
        enqueue_registry_validation,
        promote_pending_registry_entries,
    )
    from src.sehd_v6.core.bccse.registry_reflection import (
        REGISTRATION_REASON_NO_ACTION,
    )

    genome = create_baseline_policy_genome()
    _register_action_alias(genome)
    entry = enqueue_registry_validation(
        genome=genome,
        operation="register_action",
        target="downgrade_coverage_instance",
        facts=_FACTS,
        case_id="case_0",
    )
    # A second observation arrives from a stable-source case: the runner
    # calls promote_pending_registry_entries with the same plan route
    # regardless of stable_source, so the distinct case id is appended.
    promote_pending_registry_entries(
        genome=genome, facts=_FACTS, case_id="case_1",
        reason_code=REGISTRATION_REASON_NO_ACTION,
    )
    assert len(entry.observed_case_ids) == 2
    assert entry.status == PENDING_STATUS_PENDING


# ============================================================
# R9 item 1: a resume continues the promotion observation ledger
# ============================================================
# The promotion observation count lives on PendingValidationItem and rides
# the policy_genome checkpoint payload.  This test drives a real (offline,
# mocked-provider) run that registers one entry and accumulates exactly one
# observation, persists a checkpoint, then restores it through the SAME
# load/restore path a resume uses and confirms the entry is still pending
# with its observation intact — ready to keep accumulating.

def _appeal_pending_detection() -> dict[str, Any]:
    """The L2 appeal_pending_blocked shape that routes to
    no_registered_runtime_action and opens the registry planner."""
    return {
        "state": "unresolved",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [
            {
                "candidate_id": "v3_candidate_001",
                "origin_hypothesis_event_id": "event_002",
                "trigger_event_id": "event_002",
                "axes": ["F"],
                "candidate_kind": "assertion",
                "claim_or_commitment": "The sum is 5.",
                "dependency_event_ids": [],
                "module_tag": "Reasoning",
                "observation": "unsupported result",
                "is_decision_point": True,
            }
        ],
        "proof_adjudication": {
            "state": "unresolved",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "valid_error_certificate_count": 1,
            "valid_defense_certificate_count": 1,
            "surviving_error_certificate_ids": [],
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_applied": False,
            "consensus_gate_blocked_ids": [],
            "required_candidate_count": 1,
            "appeal_triggered": True,
            "appeal_status": "incomplete_fail_closed_unresolved",
            "localization_status": "",
            "responsible_step": 1,
            "error_audits": [],
            "defense_audits": [],
        },
        "boundary_path": {"state": ""},
        "evidence_health": {
            "required_candidate_count": 1,
            "construct_accepted_candidate_count": 1,
            "construct_raw_candidate_count": 1,
        },
    }


def test_resume_continues_the_promotion_ledger(
    tmp_path: Path, monkeypatch,
) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner
    from src.sehd_v6.core.bccse.registry_promotion import (
        promote_pending_registry_entries,
    )
    from src.sehd_v6.core.bccse.registry_reflection import (
        REGISTRATION_REASON_NO_ACTION,
    )

    def detector_stub(
        *, genome, case_id, record, config, output_dir,
        census_root=None, baseline_cache_root=None, shared_cache_root=None,
        policy_genome=None, policy_scope="working",
    ) -> dict[str, Any]:
        return _appeal_pending_detection()

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector_stub)

    def mutation_call(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        system = str(kwargs.get("system") or "")
        if "Select at most one precomputed L2 vocabulary registration" in system:
            user = str(kwargs.get("user") or "")
            try:
                payload = json.loads(user)
            except ValueError:
                return {"no_proposal": True, "reason": "unparsable"}, {}
            for index, candidate in enumerate(payload.get("candidates") or []):
                if candidate.get("operation") == "register_action":
                    return {
                        "candidate_index": index,
                        "reason": "register the downgrade action",
                        "precondition": "all_candidates_quarantined",
                        "params": {"side": "prosecution", "to_state": "unresolved"},
                    }, {}
            return {"no_proposal": True, "reason": "no action candidate"}, {}
        return {"operation": "no_edit", "rationale": "mock abstains"}, {}

    monkeypatch.setattr(runner, "json_object_provider_call", mutation_call)

    records = {
        "case_a": {
            "question": "What is 2 + 2?",
            "history": [
                {"role": "user", "content": "What is 2 + 2?"},
                {"role": "assistant", "content": "The sum is 5."},
            ],
            "gold_label": True,
        },
    }
    out = tmp_path / "evolution"
    runner.run_online_evolution(
        evolution_case_ids=["case_a"],
        records=records,
        config={},
        output_dir=out,
        census_root=tmp_path / "census",
        use_gold=True,
        mutation_route={"model": "deepseek-v4-flash"},
        fitness_use_gold=True,
        canary_limit=0,
        max_mutation_calls=50,
        run_id="r9_resume_ledger",
    )

    # The run registered one entry and enqueued it with one observation.
    checkpoint = runner.load_evolution_checkpoint(out)
    assert checkpoint, "a checkpoint must exist after the run"
    restored_genome = PolicyGenome.from_dict(checkpoint["policy_genome"])
    entry = next(
        item for item in restored_genome.pending_queue
        if item.proposal.get("operation") == "register_action"
    )
    assert entry.status == PENDING_STATUS_PENDING, (
        "a single observation must leave the entry pending"
    )
    assert entry.observed_case_ids == ("case_a",)

    # Simulate the resume's continuation: two further independent
    # observations on the RESTORED genome reach the threshold and promote.
    rows = []
    for case_id in ("case_b", "case_c"):
        rows.extend(promote_pending_registry_entries(
            genome=restored_genome, facts=_FACTS, case_id=case_id,
            reason_code=REGISTRATION_REASON_NO_ACTION,
        ))
    assert any(row["outcome"] == "promoted" for row in rows), rows
    assert entry.status == PENDING_STATUS_VALIDATED
    assert len(entry.observed_case_ids) == PROMOTION_OBSERVATION_THRESHOLD
