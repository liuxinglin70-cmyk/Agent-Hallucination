"""Anonymous offline fixtures for the optimized evolution loop.

Frozen by P0 of the v3 optimized-execution plan.  Every preset is rebuilt
from scratch per call and uses synthetic identifiers only — no real case
IDs, no raw trajectories, no gold prompts, and no external API.  The
presets cover the fixture classes the optimized loop must reproduce
deterministically:

- consensus_blocked_unresolved: gate blocked a live candidate and the
  court stayed unresolved (L2 consensus_gate_policy attribution);
- consensus_blocked_terminal_verdict: gate blocked candidates but the
  court still reached a verdict — must NOT attribute to L2;
- boundary_verified_clean_unresolved: verified-clean boundary path never
  consumed by the terminal edge (L2 boundary_consumption_policy);
- boundary_conflict_with_surviving_error: boundary conflict while an
  error certificate survives — the post-ledger consume route must stay
  fail-closed on this shape;
- hollow_prosecution / hollow_defense: complete coverage claim with zero
  valid certificates on that side (L2 coverage_validity_policy);
- l1_malformed_candidate: provider artifact defect (L1 attribution);
- attempt_structural_reject / attempt_compiled_no_effect /
  attempt_accepted_local_movement: L1 attempt-outcome shapes;
- runtime_invalid_detection: detector never completed (diagnostic only);
- install_mocked_evolution: end-to-end mocked provider harness covering
  L1 proposal, L2 proposal, replay, canary and checkpoint/resume, with
  planned interruption at the observation / proposal / outcome positions.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from tests.fixtures.causal_evolution import (
    court_result,
    detection_result,
    signal_for,
)

# ------------------------------------------------------------------
# L2 adjudication-layer fixtures (runner-shaped detection dicts)
# ------------------------------------------------------------------


def consensus_blocked_unresolved() -> dict[str, Any]:
    """Appeal-sourced certificate blocked by the consensus gate; the court
    cannot reach a verdict -> L2 / consensus_gate_policy."""
    return detection_result(
        state="unresolved",
        adjudication=court_result(
            state="unresolved",
            prosecution_coverage="complete",
            defense_coverage="incomplete",
            valid_error_certificate_count=1,
            consensus_gate_applied=True,
            consensus_gate_blocked_ids=["appeal_error_1"],
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "appeal_error_1"}],
        responsible_step="event_4",
    )


def consensus_blocked_terminal_verdict() -> dict[str, Any]:
    """The gate filtered appeal candidates yet the court still reached a
    verdict on independently supported evidence: protection working as
    designed, never an L2 failure attribution."""
    return detection_result(
        state="verified_error",
        adjudication=court_result(
            state="verified_error",
            prosecution_coverage="complete",
            defense_coverage="incomplete",
            valid_error_certificate_count=2,
            surviving_error_certificate_ids=["error_1"],
            consensus_gate_applied=True,
            consensus_gate_blocked_ids=["appeal_error_2"],
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[
            {"certificate_id": "error_1"},
            {"certificate_id": "appeal_error_2"},
        ],
        responsible_step="event_4",
    )


def boundary_verified_clean_unresolved() -> dict[str, Any]:
    """Boundary path verified clean but the terminal edge stayed unresolved
    -> L2 / boundary_consumption_policy."""
    return detection_result(
        state="unresolved",
        adjudication=court_result(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            valid_error_certificate_count=1,
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "error_1"}],
        responsible_step="event_5",
        boundary_path_state="verified_clean",
    )


def boundary_conflict_with_surviving_error() -> dict[str, Any]:
    """Boundary conflict while an error certificate survives: attribution
    still routes to the boundary-consumption policy, but any post-ledger
    consume action on this shape must fail closed (surviving evidence
    forbids a verified-clean terminal move)."""
    return detection_result(
        state="unresolved",
        adjudication=court_result(
            state="unresolved",
            prosecution_coverage="complete",
            defense_coverage="incomplete",
            valid_error_certificate_count=1,
            surviving_error_certificate_ids=["error_1"],
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "error_1"}],
        responsible_step="event_5",
        boundary_path_state="verified_clean",
    )


def hollow_prosecution() -> dict[str, Any]:
    """Prosecution claims complete coverage with zero valid certificates
    -> L2 / coverage_validity_policy (hollow_complete_prosecution)."""
    return detection_result(
        state="unresolved",
        adjudication=court_result(
            state="unresolved",
            prosecution_coverage="complete",
            defense_coverage="incomplete",
            valid_error_certificate_count=0,
            valid_defense_certificate_count=1,
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        defense=[{"certificate_id": "defense_1"}],
        responsible_step="event_4",
    )


def hollow_defense() -> dict[str, Any]:
    """Defense claims complete coverage with zero valid certificates
    -> L2 / coverage_validity_policy (hollow_complete_defense)."""
    return detection_result(
        state="unresolved",
        adjudication=court_result(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="complete",
            valid_error_certificate_count=1,
            valid_defense_certificate_count=0,
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        prosecution=[{"certificate_id": "error_1"}],
        responsible_step="event_4",
    )


def l1_malformed_candidate() -> dict[str, Any]:
    """Provider emitted a malformed candidate artifact -> L1 /
    candidate_trigger (provider_artifact_malformed)."""
    result = detection_result(
        state="unresolved",
        adjudication=court_result(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            required_candidate_count=1,
        ),
        census=[],
        responsible_step="event_2",
    )
    result["provider_artifacts"] = [
        {
            "kind": "candidate",
            "module_hint": "",
            "malformed": True,
            "missing_fields": [],
            "artifact_id": "candidate_1",
            "source": "fixture",
        }
    ]
    return result


def runtime_invalid_detection() -> dict[str, Any]:
    """Detector never completed: diagnostic-only shape, never evidence."""
    result = detection_result(state="runtime_invalid", runtime_valid=False)
    result["runtime_error"] = "provider_timeout: simulated offline outage"
    result["runtime_error_category"] = "provider_transport"
    return result


def classifier_record(detection: Mapping[str, Any]) -> dict[str, Any]:
    """Project one fixture into the failure classifier's input contract.

    Fixtures declare their provider-artifact integrity explicitly; fixtures
    without an artifact defect carry an empty projection so rule 1 stays
    silent and the adjudication-layer rules decide.
    """
    adjudication = detection.get("proof_adjudication") or {}
    if not isinstance(adjudication, Mapping):
        adjudication = {}
    boundary = detection.get("boundary_path") or {}
    if not isinstance(boundary, Mapping):
        boundary = {}
    return {
        "adjudication": dict(adjudication),
        "boundary_path_state": str(boundary.get("state") or ""),
        "provider_artifacts": [
            dict(item)
            for item in detection.get("provider_artifacts") or ()
            if isinstance(item, Mapping)
        ],
    }


# ------------------------------------------------------------------
# L1 attempt-outcome fixtures (FailureAttempt-shaped dicts)
# ------------------------------------------------------------------


def _attempt(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "attempt_id": "attempt_fixture",
        "proposal_signature": "proposal_fixture",
        "operation": "add_rule",
        "target_module": "evidence_obligation",
        "action_spec": {
            "module_id": "evidence_obligation",
            "action": "require_evidence_field",
        },
        "structural_result": "accepted",
        "compiled": True,
        "injected": True,
        "applied": True,
        "observable_delta": [],
        "downstream_court_delta": [],
        "no_effect_reason": "",
        "decision": "reject_fitness",
        "proposal_content": "require cited trajectory evidence for claims",
    }
    value.update(overrides)
    return value


def attempt_structural_reject() -> dict[str, Any]:
    """Proposal failed the deterministic slot gate: never compiled."""
    return _attempt(
        structural_result="rejected",
        compiled=False,
        injected=False,
        applied=False,
        decision="skip_invalid_mutation",
        no_effect_reason="",
    )


def attempt_compiled_no_effect() -> dict[str, Any]:
    """Compiled, injected and applied, yet no owned observable moved."""
    return _attempt(
        decision="reject_fitness",
        no_effect_reason="provider_output_changed_but_downstream_invariant",
    )


def attempt_accepted_local_movement() -> dict[str, Any]:
    """Legal edit whose source replay moved an owned observable and closed
    the trigger."""
    return _attempt(
        decision="accept_batch",
        observable_delta=["prosecution_coverage:incomplete->complete"],
        downstream_court_delta=["state:unresolved->verified_clean"],
    )


# ------------------------------------------------------------------
# End-to-end mocked provider harness
# ------------------------------------------------------------------

INTERRUPTION_POSITIONS = ("observation", "proposal", "outcome")


# Distinct rule templates for the offline L1 responder.  Each template is
# a legal, semantically different evidence_obligation rule so two triggered
# cases never collide with the structural duplicate gate; the request hash
# picks the template, keeping the response a pure function of the prompt
# (resume-safe without any queue bookkeeping).
_L1_RULE_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "a factual claim lacks a cited trajectory event",
        "require_evidence_field: impact_evidence_refs",
    ),
    (
        "an emitted certificate omits its propagation path",
        "require_evidence_field: benchmark_causal_evidence_refs",
    ),
    (
        "a claimed impact has no downstream consumption evidence",
        "require_evidence_field: consumption_evidence_refs",
    ),
    (
        "a recovery assertion lacks residual-impact evidence",
        "require_evidence_field: recovery_evidence_refs",
    ),
)


def default_mutation_response(purpose: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic offline mutation-route responses keyed by purpose.

    Responses are a pure function of the request, so a resumed run that
    re-issues the same logical call receives the identical payload without
    any queue bookkeeping.
    """
    if purpose == "policy_reflection":
        return {"candidate_index": 0, "reason": "planned offline repair"}
    if purpose == "reflection_diagnosis":
        return {
            "owner_executable": False,
            "owner_module": None,
            "failure_field": "",
            "observed_gap": "",
            "reasoning": "no deeper diagnosis in the offline harness",
        }
    import hashlib

    digest = hashlib.sha256(
        str(kwargs.get("user") or "").encode("utf-8")
    ).hexdigest()
    condition, action = _L1_RULE_TEMPLATES[int(digest, 16) % len(_L1_RULE_TEMPLATES)]
    return {
        "target_module": "evidence_obligation",
        "operation": "add_rule",
        "rule_index": None,
        "condition": condition,
        "action": action,
        "evidence_anchor": f"offline anchor for: {condition}",
        "feature_description": condition,
        "rationale": "Tighten the evidence obligation for unsupported claims.",
        "expected_effect": "prosecution evidence coverage increases",
        "expected_effect_contract": {
            "observable_field": "prosecution_coverage",
            "expected_direction": "increase",
            "expected_scope": "all_cases",
            "trigger_condition": "prosecution evidence is incomplete",
        },
    }


def l1_source_detection() -> dict[str, Any]:
    """Triggered L1 source shape for the mocked loop: an unresolved case
    whose missing certificate projects an evidence_obligation defect."""
    return detection_result(
        state="unresolved",
        adjudication=court_result(
            state="unresolved",
            prosecution_coverage="incomplete",
            defense_coverage="incomplete",
            valid_error_certificate_count=0,
            valid_defense_certificate_count=0,
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        responsible_step="event_4",
    )


def l1_source_replay_detection() -> dict[str, Any]:
    """Candidate replay of the L1 source: the edit closed the trigger."""
    return detection_result(
        state="verified_clean",
        adjudication=court_result(
            state="verified_clean",
            prosecution_coverage="complete",
            defense_coverage="complete",
            valid_error_certificate_count=0,
            valid_defense_certificate_count=1,
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        defense=[{"certificate_id": "defense_1"}],
        responsible_step="event_4",
    )


def stable_anchor_detection() -> dict[str, Any]:
    """Label-free canary anchor: stays stable under every genome."""
    return detection_result(
        state="verified_clean",
        adjudication=court_result(
            state="verified_clean",
            prosecution_coverage="incomplete",
            defense_coverage="complete",
            valid_defense_certificate_count=1,
            required_candidate_count=1,
        ),
        census=[{"candidate_id": "candidate_1", "origin_refs": ["event_1"]}],
        defense=[{"certificate_id": "defense_1"}],
        responsible_step="event_3",
    )


def evolution_case_detections() -> dict[str, dict[str, Any]]:
    """Default two-source scenario for the mocked loop.

    ``initial`` is the payload while the genome carries no fix for the
    case; ``fixed`` is the payload once the genome holds at least one
    evidence_obligation rule (None = the case is never repaired, so its
    candidate replay deterministically shows no effect).  Responses are a
    pure function of (case_id, genome content) — never of call history —
    so an interrupted-then-resumed run answers every detector call exactly
    like an uninterrupted one.
    """
    return {
        "source1": {
            "initial": l1_source_detection(),
            "fixed": l1_source_replay_detection(),
        },
        "source2": {"initial": l1_source_detection(), "fixed": None},
    }


def install_mocked_evolution(
    monkeypatch: Any,
    *,
    detections: Mapping[str, Any] | None = None,
    replay: Callable[..., Mapping[str, Any]] | None = None,
    respond: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    interrupt_at: str | None = None,
    interrupt_case: str = "",
) -> dict[str, Any]:
    """Wire the evolution runner to deterministic offline provider stubs.

    ``detections`` maps a case id to either a plain detection payload, an
    Exception instance (raised; the runner's runtime lapse path turns it
    into a diagnostic-only skip), or an ``{"initial", "fixed"}`` pair as
    produced by ``evolution_case_detections``.  A plain payload is
    returned for every call; a pair answers ``initial`` while the genome
    content hash equals the one seen at this case's first observation and
    ``fixed`` once the genome content has moved on (what a candidate or
    canary replay sees).  Genome content hashes — not call counts — key
    the switch, so an interrupted-then-resumed run answers every detector
    call exactly like an uninterrupted one.  ``replay`` handles L2
    candidate replays: it fires exactly when the call carries a non-empty
    policy genome.  (Both keys assume each scenario starts from the empty
    baseline surfaces; the L2 key additionally assumes a single L2 case,
    because post-accept initial detections also carry policy items.)

    ``respond`` answers mutation-route calls (default:
    ``default_mutation_response``).

    ``interrupt_at`` simulates a hard crash (KeyboardInterrupt, which the
    runner deliberately does not swallow) at one of three positions:
    ``observation`` — inside the initial detection of ``interrupt_case``;
    ``proposal`` — inside the first mutation reflection call;
    ``outcome`` — inside the first replay detection of ``interrupt_case``.
    Only the crash trigger consults call history; response content never
    does.
    """
    if interrupt_at is not None and interrupt_at not in INTERRUPTION_POSITIONS:
        raise ValueError(f"unknown interruption position: {interrupt_at}")
    from src.sehd_v6.core.bccse import evolution_runner as runner

    scripted = {
        str(key): (
            value
            if isinstance(value, Exception)
            else {k: v for k, v in dict(value).items()}
            if isinstance(value, Mapping) and "initial" in value
            else dict(value)
        )
        for key, value in (detections or {}).items()
    }
    responder = respond or default_mutation_response
    calls: dict[str, Any] = {
        "detector": [],
        "mutation": [],
        "observed_cases": [],
        "baseline_genome_hash": {},
    }

    def detector(*, genome: Any, case_id: str, policy_genome: Any = None, **kwargs: Any) -> dict[str, Any]:
        first_observation = case_id not in calls["observed_cases"]
        if first_observation:
            calls["observed_cases"].append(case_id)
            calls["baseline_genome_hash"][case_id] = genome.content_hash
        is_replay = (
            genome.content_hash != calls["baseline_genome_hash"][case_id]
        )
        policy_active = policy_genome is not None and bool(policy_genome.items)
        calls["detector"].append({
            "case_id": case_id,
            "generation": genome.generation,
            "policy_items": (
                len(policy_genome.items) if policy_genome is not None else 0
            ),
            "observation": first_observation,
        })
        if (
            interrupt_at == "observation"
            and first_observation
            and case_id == interrupt_case
        ):
            raise KeyboardInterrupt(f"planned interruption: observation:{case_id}")
        if (
            interrupt_at == "outcome"
            and is_replay
            and case_id == interrupt_case
        ):
            raise KeyboardInterrupt(f"planned interruption: outcome:{case_id}")
        payload = scripted.get(case_id)
        if isinstance(payload, Exception):
            raise payload
        if replay is not None and policy_active:
            return dict(replay(
                case_id=case_id, genome=genome, policy_genome=policy_genome,
            ))
        if isinstance(payload, Mapping) and "initial" in payload:
            fixed_payload = payload.get("fixed")
            if is_replay and fixed_payload is not None:
                return dict(fixed_payload)
            return dict(payload["initial"])
        if isinstance(payload, Mapping):
            return dict(payload)
        return stable_anchor_detection()

    def mutation(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        purpose = str(kwargs.get("purpose") or "")
        calls["mutation"].append({
            "purpose": purpose,
            "user": str(kwargs.get("user") or ""),
        })
        if interrupt_at == "proposal":
            raise KeyboardInterrupt("planned interruption: proposal")
        return dict(responder(purpose, kwargs)), {"cache_hit": False}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    # The step attributor is stubbed at the same boundary the standalone
    # runner tests use; the deterministic failure classifier and every
    # downstream evolution mechanism stay real.
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result.get("state") or "")
        ),
    )
    monkeypatch.setattr(runner, "json_object_provider_call", mutation)
    return calls
