"""Independent R8 review verification — written from scratch for this review.

Verifies the mechanism fixes landed during the R8 review iteration at the
unit level and then drives the L2 registry-vocabulary chain offline against
a local mock provider (no network, no credentials): a real classifier
attribution opens the registry planner, a registration is selected and
enqueued, independent observations promote the entry, and the promoted
intent is selected and committed as a policy item.

Run:  python -m pytest tests/test_review_r8_independent.py -v
"""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from src.sehd_v6.core.bccse.evolution_runner import (
    _shadow_commit_eligible,
)
from src.sehd_v6.core.bccse.meta_review import (
    MIN_COMMIT_EVIDENCE,
    GateExperience,
    evaluate_commit_evidence,
)
from src.sehd_v6.core.bccse.registry_reflection import (
    REGISTRATION_REASON_NO_ACTION,
    plan_registry_candidates,
)
from src.sehd_v6.core.bccse.v6_contracts import (
    ROUTE_NO_REGISTERED_RUNTIME_ACTION,
    FailureAttribution,
)


# ============================================================
# R8 fix 1: the registry chain opens on the PLAN route decision
# ============================================================
# The classifier names the L2 symptom (e.g. appeal_pending_blocked); the
# planner decides no registered runtime action can repair it.  The registry
# planner and promotion chain were keyed on the classifier's raw reason code,
# which can never equal REGISTRATION_REASON_NO_ACTION — the whole
# register -> observe -> promote -> intent chain was unreachable in
# production.  The runner now feeds the plan route result into the chain.

def _appeal_pending_adjudication() -> dict[str, Any]:
    return {
        "adjudication": {
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
        "boundary_path_state": "",
        "provider_artifacts": [],
    }


def test_real_classifier_attribution_opens_the_registry_planner() -> None:
    """The fixed chain must open with the REAL classifier output, not a
    stubbed reason code: appeal_pending_blocked is a symptom, the plan route
    is the decision that opens the vocabulary."""
    from src.sehd_v6.core.bccse.failure_classifier import classify_failure
    from src.sehd_v6.core.bccse.policy_genome import create_baseline_policy_genome
    from src.sehd_v6.core.bccse.policy_reflection import plan_policy_candidates

    attribution = classify_failure(_appeal_pending_adjudication())
    assert attribution.layer == "l2_adjudication_policy"
    assert attribution.reason_code == "appeal_pending_blocked"
    plan = plan_policy_candidates(
        attribution=attribution,
        genome=create_baseline_policy_genome(),
    )
    assert plan.route_result == ROUTE_NO_REGISTERED_RUNTIME_ACTION
    # The runner keys the registry chain on the plan route; the registry
    # planner must then actually enumerate candidates.
    candidates = plan_registry_candidates(
        attribution=FailureAttribution(
            layer=attribution.layer,
            artifact=attribution.artifact,
            reason_code=plan.route_result,
            evidence=attribution.evidence,
        ),
        registry=create_baseline_policy_genome().registry,
    )
    assert candidates, (
        "the registry planner must open under the plan route decision "
        "(REGISTRATION_REASON_NO_ACTION)"
    )
    assert plan.route_result == REGISTRATION_REASON_NO_ACTION


# ============================================================
# R8 fix 2: registry intent-drop is audited, not silent
# ============================================================

def test_intent_drop_is_audited_when_candidate_is_kept() -> None:
    """select_registry_candidate returns (candidate, reason) when the model
    supplies an invalid intent; the runner must record intent_dropped so the
    audit distinguishes 'no intent' from 'invalid intent supplied'."""
    from src.sehd_v6.core.bccse.policy_genome import create_baseline_policy_genome
    from src.sehd_v6.core.bccse.registry_reflection import (
        build_registry_selection_prompt,
    )
    from src.sehd_v6.core.bccse.evolution_runner import _run_registry_reflection_impl

    genome = create_baseline_policy_genome()
    candidates = plan_registry_candidates(
        attribution=FailureAttribution(
            layer="l2_adjudication_policy",
            artifact="coverage_validity_policy",
            reason_code=REGISTRATION_REASON_NO_ACTION,
            evidence={},
        ),
        registry=genome.registry,
    )
    action_candidates = [
        index for index, candidate in enumerate(candidates)
        if candidate.operation == "register_action"
    ]
    assert action_candidates, "the baseline registry must offer action instances"
    action_index = action_candidates[0]

    def fake_mutation_call(**kwargs: Any):
        # Model selects the action candidate but names an unknown predicate
        # as its intended precondition — the intent must be dropped.
        return {
            "candidate_index": action_index,
            "reason": "register the downgrade action",
            "precondition": "predicate_that_does_not_exist",
            "params": {"side": "prosecution", "to_state": "unresolved"},
        }, {}

    registrations: list[dict[str, Any]] = []
    attribution = FailureAttribution(
        layer="l2_adjudication_policy",
        artifact="coverage_validity_policy",
        reason_code=REGISTRATION_REASON_NO_ACTION,
        evidence={},
    )
    system, user = build_registry_selection_prompt(
        attribution=attribution, facts_summary={}, candidates=candidates,
    )
    _run_registry_reflection_impl(
        attribution=attribution,
        facts_summary={},
        case_id="case_1",
        policy_genome=genome,
        mutation_route={"model": "mock"},
        run_mutation_call=fake_mutation_call,
        output_dir=Path(__import__("tempfile").mkdtemp()),
        l2_registry_registrations=registrations,
    )
    outcomes = [row["outcome"] for row in registrations]
    assert "intent_dropped" in outcomes
    drop_row = next(
        row for row in registrations if row["outcome"] == "intent_dropped"
    )
    assert drop_row["reason"].startswith("intent_precondition_unknown:")
    # The registration itself still happens (fail-closed on the intent only).
    assert "registered" in outcomes


# ============================================================
# R8 fix 3: commit-evidence gate counts per mechanism class
# ============================================================

def _experience(code: str, signature: tuple[str, ...], sequence: int):
    return GateExperience(
        gate_event_id=f"ev{sequence}",
        mechanism_signature=signature,
        layer=signature[0],
        artifact=signature[1],
        stage="gate",
        evidence_grade="reject",
        activation_state="none",
        feedback_codes=(code,),
        execution_trace_class="none",
        sequence=sequence,
    )


def test_scattered_mechanism_occurrences_do_not_sum_to_commit() -> None:
    """Independent reproduction is per mechanism class: three occurrences of
    the same fuzzy code from three different mechanisms must not commit a
    guidance token that only one mechanism reproduced."""
    from src.sehd_v6.core.bccse.meta_skill import OP_ADD_GUIDANCE, MetaEdit

    experiences = [
        _experience("policy_route_unavailable", ("l2", "policy_a"), 1),
        _experience("policy_route_unavailable", ("l2", "policy_b"), 2),
        _experience("policy_route_unavailable", ("l1", "candidate_trigger"), 3),
    ]
    row = evaluate_commit_evidence(
        MetaEdit(
            edit_id="m",
            operation=OP_ADD_GUIDANCE,
            target="prefer_registered_runtime_route",
        ),
        experiences,
        [],
    )
    assert row["committed"] is False
    assert row["blocked_reason"] == "insufficient_evidence"
    assert row["evidence_observed"] == 1, (
        "only the max per-mechanism count may count, not the global sum"
    )


def test_same_mechanism_reproduction_reaches_commit() -> None:
    from src.sehd_v6.core.bccse.meta_skill import OP_ADD_GUIDANCE, MetaEdit

    signature = ("l2_adjudication_policy", "policy")
    experiences = [
        _experience("policy_route_unavailable", signature, i)
        for i in range(MIN_COMMIT_EVIDENCE)
    ]
    row = evaluate_commit_evidence(
        MetaEdit(
            edit_id="m",
            operation=OP_ADD_GUIDANCE,
            target="prefer_registered_runtime_route",
        ),
        experiences,
        [],
    )
    assert row["committed"] is True
    assert row["evidence_observed"] == MIN_COMMIT_EVIDENCE
    assert row["evidence_by_mechanism"][0]["observed"] == MIN_COMMIT_EVIDENCE


# ============================================================
# R8 fix 4: shadow admission/commit invariant at the flush boundary
# ============================================================
# An edit admitted to the reversible shadow genome under the unified
# predicate must never be predicate-rejected at flush; the rejected path is
# only reachable for edits that were never admissible (e.g. worse).

def _flushed_decision(
    decision: str, *, canary_audits: tuple[dict[str, Any], ...] = (),
) -> bool:
    """Run the flush admission predicate on one decision exactly as the
    flush loop does (source decision + frozen canary audits)."""
    return _shadow_commit_eligible(
        decision,
        fitness_use_gold=False,
        canary_audits=canary_audits,
    )


def test_flush_predicate_accepts_every_admitted_decision() -> None:
    admitted = [
        ("strong_improvement", ()),
        ("local_improvement", ()),
        ("directional_evidence", ({"preserved": True},)),
    ]
    for decision, canaries in admitted:
        assert _flushed_decision(decision, canary_audits=canaries) is True, (
            f"admitted decision {decision} must never be rejected at flush"
        )


def test_flush_predicate_rejects_only_non_admissible_decisions() -> None:
    rejected = [
        ("worse", ()),
        ("no_effect", ()),
        ("directional_evidence", ()),
        ("directional_evidence", ({"preserved": False},)),
    ]
    for decision, canaries in rejected:
        assert _flushed_decision(decision, canary_audits=canaries) is False


# ============================================================
# R8 fix 5: the promoted-intent chain is reachable offline
# ============================================================
# Offline e2e over the L2 registry vocabulary: real classifier -> real
# planner -> real registry reflection (HTTP mock mutation route) ->
# registration -> independent observations -> promotion -> intent ->
# policy selection (HTTP mock) -> commit.

EVENT_IDS = ["event_001", "event_002"]


def _l2_source_detection() -> dict[str, Any]:
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


def _l2_policy_replay(policy_genome: Any = None) -> dict[str, Any]:
    """The promoted intent fires in the candidate replay.  R9 binds the
    effect gate to the proposal's own item_id; the runner assigns that id
    (``policy_<uuid8>``) and passes the candidate genome here, so the fired
    row references its last item instead of a hard-coded id."""
    item_id = ""
    if policy_genome is not None and getattr(policy_genome, "items", None):
        item_id = str(policy_genome.items[-1].item_id)
    result = _l2_source_detection()
    result["state"] = "verified_error"
    result["proof_adjudication"]["state"] = "verified_error"
    result["proof_adjudication"]["policy_evaluation"] = {
        "fired": [{"item_id": item_id}],
        "skipped": [],
        "effective_prosecution_coverage": "complete",
        "effective_defense_coverage": "incomplete",
    }
    return result


def _registry_selection_response(user: str) -> dict[str, Any]:
    """Select the downgrade_coverage action candidate with an intended edit."""
    try:
        payload = json.loads(user)
    except ValueError:
        return {"no_proposal": True, "reason": "mock: unparsable user"}
    candidates = payload.get("candidates") or []
    for index, candidate in enumerate(candidates):
        if (
            candidate.get("operation") == "register_action"
            and candidate.get("operator") == "downgrade_coverage"
        ):
            return {
                "candidate_index": index,
                "reason": "register the downgrade action",
                "precondition": "all_candidates_quarantined",
                "params": {"side": "prosecution", "to_state": "unresolved"},
            }
    return {"no_proposal": True, "reason": "mock: no action candidate"}


def _make_l2_mutation_handler() -> type[BaseHTTPRequestHandler]:
    def _route_payload(system: str, user: str) -> dict[str, Any]:
        if "Select at most one precomputed L2 vocabulary registration" in system:
            return _registry_selection_response(user)
        if "Select at most one precomputed L2 intervention" in system:
            return {"candidate_index": 0, "reason": "select the promoted intent"}
        return {"operation": "no_edit", "rationale": "mock abstains"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
                messages = body.get("messages") or []
                system = str(messages[0].get("content") or "") if messages else ""
                user = str(messages[1].get("content") or "") if len(messages) > 1 else ""
                parsed = _route_payload(system, user)
                response = {
                    "choices": [{
                        "message": {
                            "content": json.dumps(parsed, ensure_ascii=False),
                        },
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                }
                payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:  # pragma: no cover - diagnostic
                payload = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            return

    return Handler


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def mutation_provider():
    port = _free_port()
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), _make_l2_mutation_handler()
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env_pins = {
        "DEEPSEEK_API_KEY": "mock-key-not-a-real-credential",
        "SEHD_PROVIDER_ENDPOINT": "",
        "SEHD_PROVIDER_MODEL": "",
        "SEHD_PROVIDER_API_KEY_ENV": "",
        "SEHD_DETECTOR_ENDPOINT": "",
        "SEHD_DETECTOR_MODEL": "",
        "SEHD_DETECTOR_API_KEY_ENV": "",
        "SEHD_MUTATION_ENDPOINT": "",
        "SEHD_MUTATION_MODEL": "",
        "SEHD_MUTATION_API_KEY_ENV": "",
        "SEHD_JUDGE_ENDPOINT": "",
        "SEHD_JUDGE_MODEL": "",
        "SEHD_JUDGE_API_KEY_ENV": "",
    }
    previous = {}
    for name, value in env_pins.items():
        previous[name] = os.environ.get(name)
        os.environ[name] = value
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_l2_registry_chain_registers_promotes_and_commits(
    mutation_provider: str, tmp_path: Path, monkeypatch,
) -> None:
    """The full P3B chain now runs offline with the real classifier/planner:
    register -> observe -> promote -> intent -> commit.  Before the R8 fix
    the registry planner was unreachable because the classifier's raw reason
    code never matched REGISTRATION_REASON_NO_ACTION."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector_stub(
        *, genome, case_id, record, config, output_dir,
        census_root=None, baseline_cache_root=None, shared_cache_root=None,
        policy_genome=None, policy_scope="working",
    ) -> dict[str, Any]:
        if policy_genome is not None:
            return _l2_policy_replay(policy_genome)
        return _l2_source_detection()

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector_stub)

    records = {}
    for case_id in ("case_a", "case_b", "case_c"):
        records[case_id] = {
            "question": "What is 2 + 2?",
            "history": [
                {"role": "user", "content": "What is 2 + 2?"},
                {"role": "assistant", "content": "The sum is 5."},
            ],
            "gold_label": True,
        }
    mutation_route = {
        "endpoint": mutation_provider,
        "model": "deepseek-v4-flash",
        "api_key_environment": "DEEPSEEK_API_KEY",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    result = runner.run_online_evolution(
        evolution_case_ids=list(records),
        records=records,
        config={},
        output_dir=tmp_path / "evolution",
        census_root=tmp_path / "census",
        use_gold=True,
        mutation_route=mutation_route,
        fitness_use_gold=True,
        canary_limit=0,
        max_mutation_calls=50,
        run_id="r8_l2_registry_chain",
    )
    registrations = result.evolution_audit["l2_registry_registrations"]
    outcomes = [row["outcome"] for row in registrations]
    assert "registered" in outcomes, outcomes
    assert "promoted" in outcomes, outcomes
    policy_audit = result.evolution_audit.get("l2_policy_audit") or []
    accepted = [
        row for row in policy_audit if row.get("decision") == "accept_policy"
    ]
    assert accepted, f"no accepted policy rows in {policy_audit}"
    # The promoted intent became the committed item.
    assert any(
        row.get("proposal", {}).get("new_item", {}).get("action")
        == "downgrade_coverage_instance"
        for row in accepted
    ), f"accepted rows must carry the promoted intent: {accepted}"
    assert any(
        item.action == "downgrade_coverage_instance"
        for item in result.policy_genome.items
    ), "the policy genome must contain the promoted intent item"


def test_l2_run_without_fix_chain_would_never_register() -> None:
    """Pins the pre-fix gap as a regression guard: with the raw classifier
    reason code the registry planner returns nothing, so a run that stubs
    only the detector could never register an entry."""
    from src.sehd_v6.core.bccse.failure_classifier import classify_failure
    from src.sehd_v6.core.bccse.registry_reflection import plan_registry_candidates
    from src.sehd_v6.core.bccse.policy_genome import create_baseline_policy_genome

    attribution = classify_failure(_appeal_pending_adjudication())
    # The OLD wiring passed the classifier attribution straight through.
    assert plan_registry_candidates(
        attribution=attribution,
        registry=create_baseline_policy_genome().registry,
    ) == (), (
        "the raw classifier reason must not open the registry planner; "
        "only the plan route decision may"
    )


# ============================================================
# R8 fix 6: multi-candidate collaboration reaches the critic
# ============================================================

def test_multi_candidate_round_reaches_critic_selection() -> None:
    """reflection_candidate_count > 1 must drive generation -> critic ->
    selection, and the critic's pick must be the candidate that proceeds."""
    from src.sehd_v6.core.bccse.reflection_engine import (
        build_intervention_critic_prompt,
        extract_intervention_candidates,
        select_intervention_candidate,
    )

    response = {
        "candidates": [
            {
                "target_module": "evidence_obligation",
                "operation": "add_rule",
                "condition": "a claim lacks cited evidence",
                "action": "require_evidence_field: impact_evidence_refs",
                "evidence_anchor": "claim without cited evidence",
            },
            {
                "target_module": "evidence_obligation",
                "operation": "add_rule",
                "condition": "prosecution coverage is incomplete",
                "action": "require_evidence_field: norm_evidence_refs",
                "evidence_anchor": "incomplete prosecution coverage",
            },
        ],
    }
    candidates, _ = extract_intervention_candidates(response, limit=3)
    assert len(candidates) == 2
    system, user = build_intervention_critic_prompt(
        candidates=candidates,
        runtime_observation={"observed_fields": {"prosecution_certificates": 0}},
        current_skill_text="rules",
        allowed_target_modules=("evidence_obligation",),
    )
    assert "do not generate" in system.lower()
    selected, audit = select_intervention_candidate(
        candidates,
        {"decision": "select", "selected_index": 1, "reason": "owned output"},
    )
    assert selected is not None
    assert audit["selected_index"] == 1
    assert selected["condition"] == "prosecution coverage is incomplete"
    # The same candidates must be deduplicated to their distinct shapes so
    # the runner's pre-critic extraction never collapses the round.
    repeated = {"candidates": [candidates[0], dict(candidates[0])]}
    deduped, _ = extract_intervention_candidates(repeated, limit=3)
    assert len(deduped) == 1


def _l1_source_detection() -> dict[str, Any]:
    """A missing-provider-artifact L1 attribution shape: the detector
    reports a required candidate that never reached the census, so the real
    classifier attributes an L1 provider_artifact_missing failure."""
    return {
        "state": "unresolved",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "census": [],
        "proof_adjudication": {
            "state": "unresolved",
            "prosecution_coverage": "incomplete",
            "defense_coverage": "incomplete",
            "valid_error_certificate_count": 0,
            "valid_defense_certificate_count": 0,
            "surviving_error_certificate_ids": [],
            "quarantined_error_certificate_ids": [],
            "quarantined_defense_certificate_ids": [],
            "consensus_gate_applied": False,
            "consensus_gate_blocked_ids": [],
            "required_candidate_count": 1,
            "appeal_triggered": False,
            "appeal_status": "",
            "localization_status": "",
            "responsible_step": None,
            "error_audits": [],
            "defense_audits": [],
        },
        "boundary_path": {"state": ""},
        "evidence_health": {
            "required_candidate_count": 1,
            "construct_accepted_candidate_count": 0,
            "construct_raw_candidate_count": 0,
        },
    }


def _l1_replay_improved() -> dict[str, Any]:
    result = _l1_source_detection()
    result["state"] = "verified_error"
    result["proof_adjudication"]["state"] = "verified_error"
    result["census"] = [
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
    ]
    return result


def test_runner_audits_critic_decision_when_multi_candidate(
    tmp_path: Path, monkeypatch,
) -> None:
    """Offline e2e: with reflection_candidate_count=2 the runner must call
    the critic and record its selection in the reflection audit."""
    from src.sehd_v6.core.bccse import evolution_runner as runner

    def detector_stub(
        *, genome, case_id, record, config, output_dir,
        census_root=None, baseline_cache_root=None, shared_cache_root=None,
        policy_genome=None, policy_scope="working",
    ) -> dict[str, Any]:
        if policy_genome is not None:
            return _l1_replay_improved()
        return _l1_source_detection()

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector_stub)

    def mutation_call(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        system = str(kwargs.get("system") or "")
        user = str(kwargs.get("user") or "")
        purpose = str(kwargs.get("purpose") or "")
        if purpose == "reflection_critic":
            return {
                "decision": "select",
                "selected_index": 1,
                "reason": "critic prefers the second candidate",
            }, {}
        if "module_charters" in system:
            index = 1 if '<independent_generation index="1">' in user else 0
            if index == 0:
                return {
                    "candidates": [{
                        "target_module": "candidate_trigger",
                        "operation": "add_rule",
                        "condition": "a factual claim lacks a census candidate",
                        "action": "emit_candidate: candidate",
                        "evidence_anchor": "factual claim without census candidate",
                        "feature_description": "factual claim without census candidate",
                    }],
                }, {}
            return {
                "candidates": [{
                    "target_module": "candidate_trigger",
                    "operation": "add_rule",
                    "condition": "an assertion is missing from the census",
                    "action": "emit_candidate: candidate",
                    "evidence_anchor": "assertion missing from the census",
                    "feature_description": "assertion missing from the census",
                }],
            }, {}
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
    result = runner.run_online_evolution(
        evolution_case_ids=["case_a"],
        records=records,
        config={},
        output_dir=tmp_path / "evolution",
        census_root=tmp_path / "census",
        use_gold=True,
        mutation_route={"model": "deepseek-v4-flash"},
        fitness_use_gold=True,
        canary_limit=0,
        max_mutation_calls=50,
        reflection_candidate_count=2,
        run_id="r8_multi_candidate",
    )
    step = next(
        step for step in result.steps
        if (step.fitness_check or {}).get("reflection")
    )
    reflection = step.fitness_check["reflection"]
    assert reflection.get("generated_candidates") == 2, reflection
    critic = reflection.get("critic")
    assert critic is not None, f"critic audit missing: {reflection}"
    assert critic["selected_index"] == 1, critic
