from __future__ import annotations

from pathlib import Path
from types import MethodType

from src.sehd_v6.core.bccse.step_attributor import ErrorType
from src.sehd_v6.core.bccse.types import ReplayEpisode
from src.sehd_v6.core.paca_tcl import proof_contract
from src.sehd_v6.core.paca_tcl import proof_contract_v3_4
from src.sehd_v6.core.paca_tcl import proof_engine
from src.sehd_v6.core.paca_tcl.detector import detect_record
from src.sehd_v6.core.paca_tcl.evolution_contract import project_for_evolution
from src.sehd_v6.core.paca_tcl.skill_integration import (
    PACA_MODULE_STAGE,
    PacaStage,
    compile_genome_for_paca,
)
from src.sehd_v6.core.bccse.genome import create_baseline_genome
from src.sehd_v6.core.paca_tcl.proof_evaluator import (
    PacaTclV35EvolutionEvaluator,
    _prediction_state,
    experience_record_from_row,
    metric_snapshot,
)
from src.sehd_v6.core.paca_tcl.proof_engine_v3_4 import _occurrence_first_rows


def _packet() -> dict:
    return {
        "events": [
            {"event_id": "event_001", "tool_interactions": []},
            {"event_id": "event_002", "tool_interactions": []},
        ]
    }


def _candidate() -> dict:
    return {
        "candidate_id": "candidate_001",
        "origin_hypothesis_event_id": "event_001",
    }


def _error_certificate(*, ordered: bool = True) -> dict:
    impact_event = "event_002" if ordered else "event_001"
    control_event = "event_001" if ordered else "event_002"
    return {
        "certificate_id": "error_001",
        "source_candidate_id": "candidate_001",
        "origin_event_id": "event_001",
        "claim_kind": "agent_assertion",
        "norm_source": "prior_trajectory_evidence",
        "norm_evidence_refs": ["event_001"],
        "breach_type": "contradiction",
        "violation_evidence_refs": ["event_001"],
        "agent_control_event_id": control_event,
        "agent_control_evidence_refs": [control_event],
        "impact_kind": "final_answer",
        "impact_event_ids": [impact_event],
        "impact_evidence_refs": [impact_event],
        "recovery_state": "post_impact_residual",
        "recovery_evidence_refs": ["event_002"],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": [impact_event],
        "rationale": (
            "The agent assertion contradicts prior evidence, caused an incorrect "
            "final answer, and was only repaired after the impact occurred."
        ),
    }


def _defense(defense_type: str = "supported_or_correct") -> dict:
    return {
        "certificate_id": "defense_001",
        "target_candidate_id": "candidate_001",
        "target_origin_event_id": "event_001",
        "defense_type": defense_type,
        "evidence_refs": ["event_001"],
        "rationale": "The shared event is offered as a correctness defense.",
    }


def _metric_row(*, proof: dict, prosecution: dict) -> dict:
    return {
        "gold_label": False,
        "gold_steps": [],
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "state": str(proof.get("state") or "unresolved"),
        "responsible_step": None,
        "candidate_path": {"state": "unresolved"},
        "boundary_path": {"state": "unresolved"},
        "census": [],
        "prosecution": prosecution,
        "combined_prosecution": None,
        "proof_adjudication": proof,
        "evidence_health": {},
    }


def test_construct_exception_and_empty_base_cannot_be_clean_valid(
    monkeypatch, tmp_path: Path,
) -> None:
    construct_route = {"name": "construct"}
    defense_route = {"name": "defense"}

    def base_case(**_kwargs):
        return {
            "runtime_valid": True,
            "state": "verified_clean",
            "census": [],
            "qualifications": [],
            "propagations": [],
            "boundary_path": {
                "state": "verified_clean",
                "responsible_event_id": None,
                "evidence_refs": [],
                "rationale": "No base candidate.",
            },
            "adjudication": {"state": "verified_clean"},
            "providers": {},
        }

    def provider(*, route, **_kwargs):
        if route is construct_route:
            raise RuntimeError("construct unavailable")
        assert route is defense_route
        return {"coverage_state": "complete", "candidates": []}, {}

    monkeypatch.setattr(proof_engine.v2, "adjudicate_case", base_case)
    monkeypatch.setattr(proof_engine, "provider_call", provider)

    result = proof_engine.adjudicate_case(
        case_id="case",
        record={
            "question": "test",
            "history": [{"step": 1, "role": "assistant", "content": "answer"}],
        },
        config={
            "construct_census_route": construct_route,
            "defense_route": defense_route,
        },
        output_dir=tmp_path,
        census_root=tmp_path,
    )

    assert result["state"] == "unresolved"
    assert result["runtime_valid"] is False
    assert result["proof_adjudication"]["decision_authority"] == "invalid"
    reason_codes = {
        item["code"]
        for item in result["proof_adjudication"]["authority_failure_reasons"]
    }
    assert "construct_not_complete" in reason_codes


def test_supported_or_correct_defense_prevents_occurrence_append() -> None:
    prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate()],
    }
    for module in (proof_contract_v3_4, proof_contract):
        kwargs = (
            {"apply_consensus_gate_flag": False}
            if module is proof_contract else {}
        )
        proof = module.adjudicate_proof_court(
            required_candidates=[_candidate()],
            prosecution=prosecution,
            defense={"coverage_state": "complete", "candidates": [_defense()]},
            packet=_packet(),
            **kwargs,
        )

        assert proof["state"] == "verified_clean"
        assert proof["incident_occurred"] is False
        assert proof["occurrence_state"] == "verified_clean"
        assert proof["occurrence_error_certificate_ids"] == []


def test_post_impact_recovery_does_not_erase_adjudicated_occurrence() -> None:
    prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate()],
    }
    for module in (proof_contract_v3_4, proof_contract):
        kwargs = (
            {"apply_consensus_gate_flag": False}
            if module is proof_contract else {}
        )
        proof = module.adjudicate_proof_court(
            required_candidates=[_candidate()],
            prosecution=prosecution,
            defense={
                "coverage_state": "complete",
                "candidates": [_defense("insufficient_evidence")],
            },
            packet=_packet(),
            **kwargs,
        )

        assert proof["state"] == "verified_error"
        assert proof["incident_occurred"] is True
        assert proof["occurrence_state"] == "verified_error"
        assert proof["occurrence_error_certificate_ids"] == ["error_001"]
        assert proof["occurrence_impact_status"] == "recovered_after_impact"
        assert proof["occurrence_recovery_state"] == "post_impact_residual"


def test_causal_metric_rejects_malformed_chain_and_is_undefined_without_predictions() -> None:
    malformed_prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate(ordered=False)],
    }
    malformed_proof = proof_contract.adjudicate_proof_court(
        required_candidates=[_candidate()],
        prosecution=malformed_prosecution,
        defense={"coverage_state": "complete", "candidates": [_defense()]},
        packet=_packet(),
        apply_consensus_gate_flag=False,
    )
    malformed_metrics = proof_engine.evaluate([
        _metric_row(proof=malformed_proof, prosecution=malformed_prosecution)
    ])

    assert malformed_metrics["predicted_causal_error_certificate_count"] == 1
    assert malformed_metrics["valid_causal_error_certificate_count"] == 0
    assert malformed_metrics["causal_chain_validity"] == 0.0

    empty_prosecution = {"coverage_state": "complete", "candidates": []}
    no_prediction_metrics = proof_engine.evaluate([
        _metric_row(
            proof={
                "state": "verified_clean",
                "occurrence_state": "verified_clean",
                "error_audits": [],
            },
            prosecution=empty_prosecution,
        )
    ])
    assert no_prediction_metrics["causal_chain_validity"] is None
    assert no_prediction_metrics["causal_chain_validity_defined"] is False


def test_evaluator_uses_occurrence_before_conflicting_legacy_clean_state() -> None:
    row = _metric_row(
        proof={
            # Lower-priority nested and compatibility fields intentionally
            # conflict with the authoritative top-level occurrence state.
            "state": "verified_clean",
            "occurrence_state": "verified_clean",
            "error_audits": [],
        },
        prosecution={"coverage_state": "complete", "candidates": []},
    )
    row.update({
        "case_ref": "case_ref",
        "gold_label": True,
        "state": "verified_clean",
        "incident_state": "verified_non_hallucination",
        "occurrence_state": "verified_error",
        "responsible_step": 99,
        "occurrence_responsible_step": 2,
        "gold_steps": [2],
    })

    assert _prediction_state(row) is True
    experience = experience_record_from_row(row, include_gold=True)
    assert experience.predicted_positive is True
    assert experience.predicted_responsible_ref == "2"
    assert experience.unresolved is False
    assert experience.metadata["proof_state"] == "verified_error"
    assert experience.metadata["prediction_state_authority"] == "occurrence"

    snapshot = metric_snapshot([row])
    assert snapshot.hallucination_recall == 1.0
    assert snapshot.precision == 1.0
    assert snapshot.responsible_step_accuracy == 1.0
    assert snapshot.unresolved_rate == 0.0


def test_evaluator_occurrence_authority_fallback_order() -> None:
    assert _prediction_state({
        "proof_adjudication": {"occurrence_state": "verified_error"},
        "incident_state": "verified_non_hallucination",
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "proof_adjudication": {
            "occurrence_ledger": {"occurrence_state": "verified_error"},
        },
        "incident_state": "verified_non_hallucination",
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "occurrence_ledger": {"occurrence_state": "verified_error"},
        "incident_state": {"occurrence_state": "verified_clean"},
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "incident_state": {"occurrence_state": "verified_error"},
        "state": "verified_clean",
    }) is True
    assert _prediction_state({
        "incident_state": "verified_hallucination",
        "state": "verified_clean",
    }) is True


def test_invalid_authority_abstains_without_erasing_occurrence_ledger() -> None:
    occurrence_ledger = {
        "state": "verified_error",
        "incident_occurred": True,
        "certificate_ids": ["error_001"],
    }
    proof = {
        "state": "unresolved",
        "occurrence_state": "verified_error",
        "incident_occurred": True,
        "occurrence_ledger": occurrence_ledger,
        "decision_authority": "invalid",
        "error_audits": [],
    }
    row = _metric_row(
        proof=proof,
        prosecution={"coverage_state": "complete", "candidates": []},
    )
    row.update({
        "case_ref": "invalid_case",
        "gold_label": False,
        "runtime_valid": False,
        "state": "verified_clean",
        "occurrence_state": "verified_error",
        "decision_authority": "invalid",
        "occurrence_ledger": occurrence_ledger,
    })

    assert _prediction_state(row) is None
    experience = experience_record_from_row(row)
    assert experience.predicted_positive is None
    assert experience.unresolved is True
    assert experience.certificate_valid is False
    assert experience.metadata["occurrence_state"] == "verified_error"
    assert experience.metadata["prediction_state_authority"] == "invalid"

    projected = _occurrence_first_rows([row])[0]
    assert projected["state"] == "unresolved"
    assert projected["occurrence_ledger"]["state"] == "verified_error"
    assert row["occurrence_ledger"]["state"] == "verified_error"

    detector_metrics = proof_engine.evaluate([row])
    assert detector_metrics["tri_state_counts"] == {"unresolved": 1}
    snapshot = metric_snapshot([row])
    assert snapshot.unresolved_rate == 1.0
    assert snapshot.macro_f1 == 0.0
    assert snapshot.valid_output_rate == 0.0
    assert snapshot.invalid_rate == 1.0

    authority_only = dict(row)
    authority_only["runtime_valid"] = True
    assert _prediction_state(authority_only) is None
    assert _occurrence_first_rows([authority_only])[0]["state"] == "unresolved"

    runtime_only = dict(row)
    runtime_only["decision_authority"] = "authoritative"
    runtime_only["proof_adjudication"] = {
        **proof,
        "decision_authority": "authoritative",
    }
    assert _prediction_state(runtime_only) is None
    assert _occurrence_first_rows([runtime_only])[0]["state"] == "unresolved"


def test_invalid_occurrence_cannot_count_as_replay_correction() -> None:
    parent_proof = {
        "state": "verified_error",
        "occurrence_state": "verified_error",
        "error_audits": [],
    }
    parent = _metric_row(
        proof=parent_proof,
        prosecution={"coverage_state": "complete", "candidates": []},
    )
    parent.update({
        "case_ref": "source",
        "state": "verified_error",
        "occurrence_state": "verified_error",
    })
    child = {
        **parent,
        "runtime_valid": False,
        "decision_authority": "invalid",
        "proof_adjudication": {
            **parent_proof,
            "state": "unresolved",
            "decision_authority": "invalid",
            "occurrence_ledger": {"state": "verified_error"},
        },
    }

    evaluator = object.__new__(PacaTclV35EvolutionEvaluator)
    evaluator._get_rows = MethodType(
        lambda _self, genome, _keys: (parent,) if genome == "parent" else (child,),
        evaluator,
    )
    evaluator._anonymous_case_map = MethodType(
        lambda _self, rows: {str(row["case_ref"]): row for row in rows},
        evaluator,
    )
    replay = evaluator.paired_replay(
        parent="parent",
        child="child",
        case_keys=("source",),
        episodes=(ReplayEpisode(
            record_key="source",
            error_type=ErrorType.FALSE_POSITIVE,
            support_refs=("source",),
        ),),
    )

    assert replay.targeted_error_total == 1
    assert replay.targeted_error_corrected == 0
    assert replay.valid_outputs == 0


def test_malformed_required_appeal_defense_fails_closed() -> None:
    prosecution = {
        "coverage_state": "complete",
        "candidates": [_error_certificate()],
    }
    for module in (proof_contract_v3_4, proof_contract):
        kwargs = {"apply_consensus_gate_flag": False} if module is proof_contract else {}
        try:
            module.adjudicate_proof_court(
                required_candidates=[_candidate()],
                prosecution=prosecution,
                defense={"coverage_state": "complete", "candidates": [_defense()]},
                appeal_defense={"coverage_state": "incomplete", "candidates": []},
                packet=_packet(),
                **kwargs,
            )
        except ValueError as exc:
            assert "appeal defense" in str(exc)
        else:
            raise AssertionError("invalid required appeal defense must fail closed")


def test_focused_prosecution_respects_declared_coverage(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        proof_engine,
        "provider_call",
        lambda **_kwargs: ({
            "coverage_state": "incomplete",
            "candidates": [_error_certificate()],
        }, {}),
    )
    certificates, audits, meta = proof_engine._run_focused_prosecution_for_candidate(
        candidate=_candidate(),
        built=_packet(),
        base={"census": [_candidate()]},
        config={"focused_prosecution_route": {}},
        output_dir=tmp_path,
    )
    assert certificates == []
    assert audits == []
    assert meta["coverage_error"]["declared_coverage_state"] == "incomplete"


def test_compiled_stage_modules_are_registered() -> None:
    compiled = compile_genome_for_paca(create_baseline_genome())
    for stage in PacaStage:
        expected = tuple(
            module_id for module_id, registered_stage in PACA_MODULE_STAGE.items()
            if registered_stage == stage
        )
        assert compiled.stages[stage].module_ids == expected
        assert expected


def test_standalone_detector_rejects_gold_and_returns_label_free(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "src.sehd_v6.core.paca_tcl.detector.adjudicate_case",
        lambda **_kwargs: {
            "state": "verified_clean",
            "runtime_valid": True,
            "decision_authority": "authoritative",
        },
    )
    result = detect_record(
        record={"history": [{"role": "assistant", "content": "answer"}]},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
    )
    assert result == {
        "state": "verified_clean",
        "runtime_valid": True,
        "decision_authority": "authoritative",
    }

    try:
        detect_record(
            record={"history": [], "gold_label": False},
            config={}, output_dir=tmp_path, census_root=tmp_path,
        )
    except ValueError as exc:
        assert "label-free" in str(exc)
    else:
        raise AssertionError("standalone detector must reject gold fields")


def test_evolution_projection_is_label_free_and_canonical() -> None:
    result = {
        "state": "verified_clean",
        "occurrence_state": "verified_clean",
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": None,
        "responsible_step": None,
        "census": [],
        "prosecution": {"coverage_state": "complete", "candidates": []},
        "defense": {"coverage_state": "complete", "candidates": []},
        "proof_adjudication": {
            "occurrence_state": "verified_clean",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
        },
        "gold_label": True,
    }
    projected = project_for_evolution(result).to_dict()
    assert projected["state"] == "verified_clean"
    assert projected["crs_complete"] is True
    assert "gold_label" not in projected


def test_stable_detector_package_does_not_load_evolution_modules() -> None:
    import subprocess
    import sys

    script = (
        "import sys; import src.sehd_v6.detector; "
        "assert not any(n.endswith('.bccse') or '.bccse.' in n for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def test_standalone_detector_validates_trajectory_before_provider(monkeypatch, tmp_path: Path) -> None:
    called = False

    def adjudicator(**_kwargs):
        nonlocal called
        called = True
        return {}

    try:
        detect_record(
            record={"history": []}, config={}, output_dir=tmp_path,
            adjudicator=adjudicator,
        )
    except ValueError as exc:
        assert "history" in str(exc)
    else:
        raise AssertionError("empty trajectories must be rejected")
    assert called is False


def test_standalone_config_ignores_experiment_policy(tmp_path: Path, monkeypatch) -> None:
    import json

    from src.sehd_v6.core.paca_tcl.detector import REQUIRED_ROUTES, load_detector_config

    config = {
        "execution_authorized": False,
        "authorized_phase": "forbidden_experiment_phase",
        **{name: {} for name in REQUIRED_ROUTES},
    }
    path = tmp_path / "detector.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    loaded = load_detector_config(path, require_credentials=False)
    assert loaded["detector_mode"] == "standalone"
    assert loaded["execution_authorized"] is False


def test_quarantine_only_focused_prosecution_fails_closed_and_triggers_appeal(
    monkeypatch, tmp_path: Path,
) -> None:
    """v3.5.2: a candidate whose focused certificates are all quarantined must
    (a) force incomplete prosecution coverage and (b) be re-opened by the H2
    calibrated trigger instead of being silently dropped."""
    packet = _packet()
    candidate = _candidate()
    base = {"census": [candidate], "qualifications": [], "propagations": [], "boundary_path": {}}

    def bad_provider(**_kwargs):
        return ({"coverage_state": "complete", "candidates": [_error_certificate(ordered=False)]}, {"usage": {}})

    monkeypatch.setattr(proof_engine, "provider_call", bad_provider)
    merged, audits, metas = proof_engine._run_focused_prosecution(
        built=packet, base=base,
        config={"focused_prosecution_route": {}, "max_focused_parallel": 4, "focused_prosecution_retries": 0},
        output_dir=tmp_path,
    )
    assert merged["candidates"] == []
    assert merged["coverage_state"] == "incomplete"
    assert metas["candidate_001"]["all_quarantined"] is True
    assert audits and all(a["decision"] == "quarantine" for a in audits)

    initial_proof = proof_contract.adjudicate_proof_court(
        required_candidates=base["census"], prosecution=merged,
        defense={"coverage_state": "complete", "candidates": []}, packet=packet,
        apply_materiality_check=True, apply_commitment_check=True,
        apply_consensus_gate_flag=False,
    )
    triggered = proof_engine._appeal_trigger_calibrator_v35(
        base=base, proof=initial_proof, prosecution=merged,
        defense={"coverage_state": "complete", "candidates": []}, built=packet,
        focused_prosecution_metas=metas,
    )
    assert [row["candidate_id"] for row in triggered] == ["candidate_001"]


def test_focused_prosecution_explicit_no_error_stays_complete(
    monkeypatch, tmp_path: Path,
) -> None:
    """LLM-empty (explicit no-error) must not be conflated with quarantine."""
    packet = _packet()
    candidate = _candidate()
    base = {"census": [candidate], "qualifications": [], "propagations": [], "boundary_path": {}}

    def empty_provider(**_kwargs):
        return ({"coverage_state": "complete", "candidates": []}, {"usage": {}})

    monkeypatch.setattr(proof_engine, "provider_call", empty_provider)
    merged, _audits, metas = proof_engine._run_focused_prosecution(
        built=packet, base=base,
        config={"focused_prosecution_route": {}, "max_focused_parallel": 4},
        output_dir=tmp_path,
    )
    assert merged["coverage_state"] == "complete"
    assert "all_quarantined" not in metas["candidate_001"]


def test_provider_empty_content_goes_through_json_repair(
    tmp_path: Path, monkeypatch,
) -> None:
    """An empty provider body (reasoning consumed the whole budget) shares the
    truncated-JSON repair path instead of failing before it: both are one
    bounded repair, and failing the empty case early silently burns the
    recovery mechanism the truncated case already uses."""
    import json

    from src.sehd_v6.core.paca_tcl import basic_runner

    calls: list[dict] = []

    class _FakeResponse:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._payload

    class _FakeOpener:
        def __init__(self):
            self._queue: list[bytes] = []

        def open(self, request, timeout=None):
            calls.append({"body": request.data})
            raw = self._queue.pop(0)
            return _FakeResponse(raw)

    opener = _FakeOpener()
    valid = json.dumps({
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {},
    }).encode("utf-8")
    empty = json.dumps({
        "choices": [{"message": {
            "content": "",
            "reasoning_content": "reasoned until the budget ran out",
        }}],
        "usage": {},
    }).encode("utf-8")
    opener._queue = [empty, valid]

    route = {"endpoint": "https://example.invalid", "timeout_seconds": 5}
    monkeypatch.setattr(basic_runner, "DIRECT_PROVIDER_OPENER", opener)
    monkeypatch.setattr(
        basic_runner, "resolve_provider_credential", lambda *_a, **_k: "dummy-key",
    )
    monkeypatch.setenv("PACA_GLOBAL_CACHE_ROOT", str(tmp_path / "global"))

    parsed, meta = basic_runner.provider_call_with_schema(
        route=route,
        system="s",
        user="u",
        cache_dir=tmp_path,
        require_candidates=False,
        purpose="detector",
    )

    assert parsed == {"ok": True}
    assert meta["repair_used"] is True
    assert len(calls) == 2
    assert b"JSON_RECOVERY" in calls[1]["body"]


def test_global_cache_is_opt_in_without_fixed_system_path(
    tmp_path: Path, monkeypatch,
) -> None:
    """v3.5.2: the global cache is disabled unless PACA_GLOBAL_CACHE_ROOT is
    set; the previous hard-coded remote path must never be assumed."""
    from src.sehd_v6.core.paca_tcl import basic_runner

    monkeypatch.delenv("PACA_GLOBAL_CACHE_ROOT", raising=False)
    assert basic_runner._global_cache_root() is None
    assert basic_runner._global_cache_path("abcd1234") is None

    monkeypatch.setenv("PACA_GLOBAL_CACHE_ROOT", str(tmp_path))
    root = basic_runner._global_cache_root()
    assert root == tmp_path
    path = basic_runner._global_cache_path("abcd1234")
    assert path is not None
    assert path == tmp_path / "ab" / "abcd1234.json"


def test_detect_record_fail_closed_clears_decisive_fields(
    monkeypatch, tmp_path: Path,
) -> None:
    """An invalid run must not carry stale decisive prediction fields."""
    monkeypatch.setattr(
        "src.sehd_v6.core.paca_tcl.detector.adjudicate_case",
        lambda **_kwargs: {
            "runtime_valid": False,
            "state": "verified_error",
            "incident_state": "verified_hallucination",
            "responsible_event_id": "event_001",
            "responsible_step": 1,
            "impact_status": "unrecovered",
            "impact_kind": "final_answer",
            "wide_construct_state": "verified_hallucination",
            "occurrence_state": "verified_error",
            "incident_occurred": True,
            "occurrence_responsible_event_id": "event_001",
            "occurrence_responsible_step": 1,
            "occurrence_impact_status": "unrecovered",
            "occurrence_impact_kind": "final_answer",
            "occurrence_recovery_state": "none",
            "occurrence_ledger": {"state": "verified_error"},  # append-only: preserved
        },
    )
    result = detect_record(
        record={"history": [{"role": "assistant", "content": "answer"}]},
        config={}, output_dir=tmp_path, census_root=tmp_path,
    )
    assert result["state"] == "unresolved"
    assert result["decision_authority"] == "invalid"
    assert result["incident_state"] == "unresolved"
    assert result["responsible_event_id"] is None
    assert result["responsible_step"] is None
    assert result["impact_kind"] is None
    assert result["wide_construct_state"] == "unresolved"
    assert result["occurrence_state"] == "unresolved"
    assert result["incident_occurred"] is None
    assert result["occurrence_responsible_event_id"] is None
    assert result["occurrence_impact_kind"] is None
    assert result["occurrence_ledger"] == {"state": "verified_error"}


def test_detect_record_invalid_authority_fails_closed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "src.sehd_v6.core.paca_tcl.detector.adjudicate_case",
        lambda **_kwargs: {
            "runtime_valid": True,
            "decision_authority": "invalid",
            "state": "verified_error",
            "incident_state": "verified_hallucination",
            "occurrence_state": "verified_error",
        },
    )
    result = detect_record(
        record={"history": [{"role": "assistant", "content": "answer"}]},
        config={}, output_dir=tmp_path,
    )
    assert result["state"] == "unresolved"
    assert result["occurrence_state"] == "unresolved"
    assert result["decision_authority"] == "invalid"


def test_standalone_rejects_nested_evaluator_fields(tmp_path: Path) -> None:
    for record in (
        {
            "gold_explanation": "label rationale",
            "history": [{"role": "assistant", "content": "answer"}],
        },
        {
            "history": [{
                "role": "assistant",
                "content": "answer",
                "gold_label": True,
            }],
        },
        {
            "history": [{
                "role": "assistant",
                "content": "answer",
                "tool_calls": [{
                    "gold_label": True,
                    "function": {"name": "tool", "arguments": {}},
                }],
            }],
        },
    ):
        try:
            detect_record(record=record, config={}, output_dir=tmp_path)
        except ValueError as exc:
            assert "label-free" in str(exc)
        else:
            raise AssertionError("nested evaluator metadata entered detector")


def test_standalone_allows_evaluator_like_key_inside_tool_arguments(
    tmp_path: Path,
) -> None:
    """Opaque tool payload content is task evidence, not dataset identity:
    a tool argument legitimately named ``gold_label`` must not trip the
    input firewall (aligned with the OCSE packet firewall semantics)."""
    try:
        detect_record(
            record={"history": [{
                "role": "assistant",
                "content": "answer",
                "tool_calls": [{"function": {
                    "name": "tool", "arguments": {"gold_label": True},
                }}],
            }]},
            config={}, output_dir=tmp_path,
            adjudicator=lambda **_kwargs: {
                "runtime_valid": True,
                "decision_authority": "authoritative",
                "state": "verified_clean",
            },
        )
    except ValueError as exc:
        raise AssertionError(
            "tool-argument key must not trip the input firewall"
        ) from exc


def test_standalone_rejects_nested_evaluator_output(tmp_path: Path) -> None:
    try:
        detect_record(
            record={"history": [{"role": "assistant", "content": "answer"}]},
            config={}, output_dir=tmp_path,
            adjudicator=lambda **_kwargs: {
                "runtime_valid": True,
                "decision_authority": "authoritative",
                "state": "verified_clean",
                "proof": {"gold_label": False},
            },
        )
    except RuntimeError as exc:
        assert "evaluation fields" in str(exc)
    else:
        raise AssertionError("nested evaluator metadata left detector")


def test_counterfactual_localization_requires_all_origins(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        proof_engine, "json_object_provider_call",
        lambda **_kwargs: ({
            "rankings": {
                "event_002": {
                    "causal_sufficiency_score": 1.0,
                    "is_root_cause": True,
                },
            },
        }, {}),
    )
    rankings = proof_engine._run_counterfactual_localization(
        built=_packet(),
        surviving_errors=[
            _error_certificate(),
            {**_error_certificate(), "origin_event_id": "event_002"},
        ],
        config={"focused_prosecution_route": {}}, output_dir=tmp_path,
    )
    assert rankings is None


def test_causal_chain_completeness_uses_scaffold_markers() -> None:
    """v3.5.2: chain completeness must be marker/section based so the metric
    does not saturate on ubiquitous keywords."""
    full = _error_certificate()
    full["rationale"] = (
        "NORM: the arithmetic invariant was violated | "
        "ORIGIN: event_001 misread the table | "
        "CONTROL: the agent generated the claim | "
        "PROPAGATION: the value carried into the final answer | "
        "IMPACT: the wrong final answer was produced | "
        "COUNTERFACTUAL: correcting event_001 alone fixes the result"
    )
    audit = proof_contract.validate_focused_prosecution(
        full, _packet(), {"candidate_001": "event_001"},
    )
    assert audit["causal_chain_completeness"] == 1.0
    assert audit["missing_chain_stages"] == []

    sparse = dict(full, rationale=(
        "NORM: x | ORIGIN: x | CONTROL: x | PROPAGATION: x | IMPACT: x | COUNTERFACTUAL: x"
    ))
    audit = proof_contract.validate_focused_prosecution(
        sparse, _packet(), {"candidate_001": "event_001"},
    )
    assert audit["causal_chain_completeness"] == 0.0
    assert set(audit["missing_chain_stages"]) == set(proof_contract.CAUSAL_CHAIN_STAGES)


def test_appeal_prompts_include_only_triggered_candidate_evidence() -> None:
    candidate = _candidate()
    other = {"candidate_id": "candidate_002", "origin_hypothesis_event_id": "event_002"}
    prosecution = {
        "coverage_state": "complete",
        "candidates": [
            _error_certificate(),
            {**_error_certificate(), "certificate_id": "error_002", "source_candidate_id": "candidate_002", "origin_event_id": "event_002"},
        ],
    }
    defense = {
        "coverage_state": "complete",
        "candidates": [
            _defense(),
            {**_defense(), "certificate_id": "defense_002", "target_candidate_id": "candidate_002", "target_origin_event_id": "event_002"},
        ],
    }
    base = {"qualifications": [], "propagations": [], "boundary_path": {}}
    proof = {"state": "unresolved", "error_audits": [{"large": "omitted"}]}
    prosecutor = proof_engine._appeal_prosecutor_user_v35(
        built=_packet(), candidates=[candidate], base=base,
        prosecution=prosecution, defense=defense, proof=proof,
    )
    defender = proof_engine._appeal_defender_user_v35(
        built=_packet(), candidates=[candidate], base=base,
        prosecution=prosecution, defense=defense, proof=proof,
        appeal_prosecution={"coverage_state": "complete", "candidates": []},
    )
    for prompt in (prosecutor, defender):
        assert "candidate_001" in prompt
        assert "candidate_002" not in prompt
        assert '"large":"omitted"' not in prompt
    assert other["candidate_id"] == "candidate_002"


def test_initial_error_local_authority_requires_complete_closed_proof() -> None:
    proof = {
        "state": "verified_error",
        "decision_authority": "authoritative",
        "prosecution_coverage": "complete",
        "defense_coverage": "complete",
        "quarantined_error_certificate_ids": [],
        "quarantined_defense_certificate_ids": [],
        "required_candidate_count": 2,
        "resolved_candidate_count": 2,
        "winning_error_certificate_id": "error_001",
        "surviving_error_certificate_ids": ["error_001"],
    }
    assert proof_engine._initial_error_has_local_authority(proof) is True

    for field, invalid in (
        ("state", "unresolved"),
        ("decision_authority", "invalid"),
        ("prosecution_coverage", "incomplete"),
        ("defense_coverage", "unresolved"),
        ("quarantined_error_certificate_ids", ["error_002"]),
        ("quarantined_defense_certificate_ids", ["defense_001"]),
        ("resolved_candidate_count", 1),
        ("winning_error_certificate_id", "error_missing"),
    ):
        assert proof_engine._initial_error_has_local_authority(
            {**proof, field: invalid},
        ) is False

    preserved = proof_engine._resolve_incomplete_appeal(
        proof, RuntimeError("appeal transport failed"),
    )
    assert preserved["state"] == "verified_error"
    assert preserved["decision_authority"] == "authoritative"
    assert preserved["verdict_authority_scope"] == "initial_local_positive"
    assert preserved["authority_failure_reasons"] == []
    assert preserved["non_authoritative_appeal_diagnostic"]["component"] == "appeal"

    failed_closed = proof_engine._resolve_incomplete_appeal(
        {**proof, "resolved_candidate_count": 1},
        RuntimeError("appeal transport failed"),
    )
    assert failed_closed["state"] == "unresolved"
    # An incomplete appeal is an evidence-insufficient verdict, not a runtime
    # failure: it must stay inside the authoritative three-state output so
    # the evolution loop can consume the unresolved signal instead of
    # skipping the case as invalid.
    assert failed_closed["decision_authority"] == "authoritative"
    assert failed_closed["verdict_authority_scope"] == "unresolved_appeal_incomplete"
    assert failed_closed["responsible_event_id"] is None
    assert failed_closed["surviving_error_certificate_ids"] == []


def test_appeal_prosecution_contract_accepts_complete_empty_candidates() -> None:
    """v3.5.3: coverage_state=complete + reviewed_candidate_ids=[all] +
    candidates=[] is a legitimate 'reviewed all, no prosecution' outcome."""
    allowed = {"candidate_001"}
    response = {
        "coverage_state": "complete",
        "reviewed_candidate_ids": ["candidate_001"],
        "candidates": [],
    }
    # No retry call should be invoked when the contract is satisfied.
    providers: dict = {}
    result = proof_engine._validate_appeal_prosecution_contract_or_retry(
        response, allowed_ids=allowed,
        retry_call=lambda _cat: (_ for _ in ()).throw(AssertionError("no retry")),
        providers=providers,
    )
    assert result is response
    assert "appeal_prosecutor_retry" not in providers


def test_appeal_prosecution_contract_retries_on_missing_reviewed_ids() -> None:
    """v3.5.3: missing reviewed_candidate_ids triggers ONE bounded retry with a
    different request body; a successful retry is accepted and audited."""
    allowed = {"candidate_001"}
    bad_response = {
        "coverage_state": "complete",
        "candidates": [],  # missing reviewed_candidate_ids
    }
    good_response = {
        "coverage_state": "complete",
        "reviewed_candidate_ids": ["candidate_001"],
        "candidates": [],
    }
    calls: list[str] = []
    providers: dict = {}

    def retry_call(failure_category: str):
        calls.append(failure_category)
        return good_response, {"usage": {"retry": True}}

    result = proof_engine._validate_appeal_prosecution_contract_or_retry(
        bad_response, allowed_ids=allowed,
        retry_call=retry_call, providers=providers,
    )
    assert result is good_response
    assert calls == ["missing_reviewed_candidate_ids"]
    assert providers["appeal_prosecutor_retry"]["outcome"] == "repaired"
    assert providers["appeal_prosecutor_retry"]["parent_failure_category"] == "missing_reviewed_candidate_ids"


def test_appeal_prosecution_contract_fail_closes_after_retry_failure() -> None:
    """v3.5.3: when the bounded retry still fails the contract, the function
    raises ValueError so the caller fail-closes via _resolve_incomplete_appeal
    — authority is never fabricated."""
    allowed = {"candidate_001"}
    bad_response = {
        "coverage_state": "incomplete",
        "candidates": [],
    }
    still_bad = {
        "coverage_state": "complete",
        "candidates": [],  # still missing reviewed_candidate_ids
    }
    calls: list[str] = []
    providers: dict = {}

    def retry_call(failure_category: str):
        calls.append(failure_category)
        return still_bad, {"usage": {"retry": True}}

    try:
        proof_engine._validate_appeal_prosecution_contract_or_retry(
            bad_response, allowed_ids=allowed,
            retry_call=retry_call, providers=providers,
        )
    except ValueError as exc:
        assert "bounded retry" in str(exc)
    else:
        raise AssertionError("retry failure must raise to fail-closed")
    assert calls == ["incomplete_coverage_state"]
    assert providers["appeal_prosecutor_retry"]["outcome"] == "failed_closed"
    assert providers["appeal_prosecutor_retry"]["retry_failure_category"] == "missing_reviewed_candidate_ids"


def test_appeal_prosecution_contract_rejects_foreign_candidate_after_retry() -> None:
    """v3.5.3: a foreign candidate id is a repairable failure that triggers
    retry; if the retry returns a foreign id again it fail-closes."""
    allowed = {"candidate_001"}
    bad_response = {
        "coverage_state": "complete",
        "reviewed_candidate_ids": ["candidate_001"],
        "candidates": [{
            "certificate_id": "appeal_error_001",
            "source_candidate_id": "candidate_999",  # foreign
        }],
    }
    providers: dict = {}
    retry_calls: list[str] = []

    def retry_call(failure_category: str):
        retry_calls.append(failure_category)
        # Retry returns a valid complete-empty response.
        return {
            "coverage_state": "complete",
            "reviewed_candidate_ids": ["candidate_001"],
            "candidates": [],
        }, {}

    result = proof_engine._validate_appeal_prosecution_contract_or_retry(
        bad_response, allowed_ids=allowed,
        retry_call=retry_call, providers=providers,
    )
    assert retry_calls == ["foreign_candidate_id"]
    assert result["candidates"] == []
    assert providers["appeal_prosecutor_retry"]["outcome"] == "repaired"


def test_appeal_prosecutor_retry_prompt_differs_from_initial() -> None:
    """v3.5.3: the retry user prompt must differ from the initial prompt so the
    provider cache key changes (otherwise the retry would hit the same cache
    entry and return the same invalid response)."""
    base_kwargs = dict(
        built=_packet(),
        candidates=[_candidate()],
        base={"qualifications": [], "propagations": [], "boundary_path": {}},
        prosecution={"coverage_state": "complete", "candidates": []},
        defense={"coverage_state": "complete", "candidates": []},
        proof={"state": "unresolved"},
    )
    initial = proof_engine._appeal_prosecutor_user_v35(**base_kwargs)
    retry = proof_engine._appeal_prosecutor_retry_user_v35(
        failure_category="missing_reviewed_candidate_ids", **base_kwargs,
    )
    assert retry.startswith(initial)
    assert "APPEAL_RETRY_FEEDBACK=" in retry
    assert "missing_reviewed_candidate_ids" in retry
    # No evaluator metadata leaked into the retry feedback block.
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"'):
        assert field not in retry


# ============================================================
# v3.5.4 — appeal defender symmetric retry + prosecutor
# missing_field feedback + appeal route transport hardening
# ============================================================


def _good_defense_response(candidate_id: str = "candidate_001") -> dict:
    return {
        "coverage_state": "complete",
        "candidates": [{
            "certificate_id": "appeal_defense_001",
            "target_candidate_id": candidate_id,
            "target_origin_event_id": "event_001",
            "defense_type": "insufficient_evidence",
            "evidence_refs": ["event_001"],
            "rationale": "No specific defense proven.",
        }],
    }


def test_appeal_prosecutor_retry_feedback_names_missing_field() -> None:
    """v3.5.4: the retry feedback block carries the concrete missing field
    name so the model knows exactly what to repair (not just a category)."""
    base_kwargs = dict(
        built=_packet(),
        candidates=[_candidate()],
        base={"qualifications": [], "propagations": [], "boundary_path": {}},
        prosecution={"coverage_state": "complete", "candidates": []},
        defense={"coverage_state": "complete", "candidates": []},
        proof={"state": "unresolved"},
    )
    # Each category maps to the specific missing field name.
    cases = {
        "missing_coverage_state": "coverage_state",
        "incomplete_coverage_state": "coverage_state",
        "missing_reviewed_candidate_ids": "reviewed_candidate_ids",
        "unreviewed_candidate_ids": "reviewed_candidate_ids",
        "foreign_candidate_id": "candidates[].source_candidate_id",
    }
    for category, field in cases.items():
        retry = proof_engine._appeal_prosecutor_retry_user_v35(
            failure_category=category, **base_kwargs,
        )
        assert f'"missing_field":"{field}"' in retry, category
        assert category in retry
    # Truncation / provider exception have no single field.
    for category in ("response_truncated", "provider_exception"):
        retry = proof_engine._appeal_prosecutor_retry_user_v35(
            failure_category=category, **base_kwargs,
        )
        assert '"missing_field":null' in retry, category


def test_appeal_defense_contract_accepts_complete_full_coverage() -> None:
    """v3.5.4: coverage_state=complete + one defense per supplied id is the
    satisfied contract; no retry is invoked."""
    allowed = {"candidate_001"}
    response = _good_defense_response()
    providers: dict = {}
    result = proof_engine._validate_appeal_defense_contract_or_retry(
        response, allowed_ids=allowed,
        retry_call=lambda _cat: (_ for _ in ()).throw(AssertionError("no retry")),
        providers=providers,
    )
    assert result is response
    assert "appeal_defender_retry" not in providers


def test_appeal_defense_contract_retries_on_missing_candidates() -> None:
    """v3.5.4: a complete coverage_state but empty/missing candidates is a
    repairable failure that triggers ONE bounded retry; a good retry is
    accepted and audited as repaired."""
    allowed = {"candidate_001"}
    bad_response = {
        "coverage_state": "complete",
        "candidates": [],  # no defense certificates
    }
    good_response = _good_defense_response()
    calls: list[str] = []
    providers: dict = {}

    def retry_call(failure_category: str):
        calls.append(failure_category)
        return good_response, {"usage": {"retry": True}}

    result = proof_engine._validate_appeal_defense_contract_or_retry(
        bad_response, allowed_ids=allowed,
        retry_call=retry_call, providers=providers,
    )
    assert result is good_response
    assert calls == ["missing_defense_candidates"]
    assert providers["appeal_defender_retry"]["outcome"] == "repaired"
    assert providers["appeal_defender_retry"]["parent_failure_category"] == "missing_defense_candidates"


def test_appeal_defense_contract_retries_on_coverage_mismatch() -> None:
    """v3.5.4: target_candidate_ids that do not match allowed_ids is a
    repairable failure; the retry can repair it."""
    allowed = {"candidate_001"}
    bad_response = {
        "coverage_state": "complete",
        "candidates": [{
            "certificate_id": "appeal_defense_001",
            "target_candidate_id": "candidate_999",  # foreign
            "target_origin_event_id": "event_001",
            "defense_type": "insufficient_evidence",
            "evidence_refs": ["event_001"],
            "rationale": "wrong target.",
        }],
    }
    good_response = _good_defense_response()
    calls: list[str] = []
    providers: dict = {}

    def retry_call(failure_category: str):
        calls.append(failure_category)
        return good_response, {"usage": {"retry": True}}

    result = proof_engine._validate_appeal_defense_contract_or_retry(
        bad_response, allowed_ids=allowed,
        retry_call=retry_call, providers=providers,
    )
    assert result is good_response
    assert calls == ["defense_coverage_mismatch"]
    assert providers["appeal_defender_retry"]["outcome"] == "repaired"


def test_appeal_defense_contract_fail_closes_after_retry_failure() -> None:
    """v3.5.4: when the bounded retry still fails the contract, the function
    raises ValueError so the caller fail-closes via _resolve_incomplete_appeal
    — authority is never fabricated (symmetric with the prosecutor path)."""
    allowed = {"candidate_001"}
    bad_response = {
        "coverage_state": "incomplete",
        "candidates": [],
    }
    still_bad = {
        "coverage_state": "complete",
        "candidates": [],  # still missing defense certificates
    }
    calls: list[str] = []
    providers: dict = {}

    def retry_call(failure_category: str):
        calls.append(failure_category)
        return still_bad, {"usage": {"retry": True}}

    try:
        proof_engine._validate_appeal_defense_contract_or_retry(
            bad_response, allowed_ids=allowed,
            retry_call=retry_call, providers=providers,
        )
    except ValueError as exc:
        assert "bounded retry" in str(exc)
    else:
        raise AssertionError("retry failure must raise to fail-closed")
    assert calls == ["incomplete_coverage_state"]
    assert providers["appeal_defender_retry"]["outcome"] == "failed_closed"
    assert providers["appeal_defender_retry"]["retry_failure_category"] == "missing_defense_candidates"


def test_appeal_defender_retry_prompt_differs_from_initial() -> None:
    """v3.5.4: the defender retry user prompt must differ from the initial
    prompt so the provider cache key changes (otherwise the retry would hit
    the same cache entry and return the same invalid response)."""
    base_kwargs = dict(
        built=_packet(),
        candidates=[_candidate()],
        base={"qualifications": [], "propagations": [], "boundary_path": {}},
        prosecution={"coverage_state": "complete", "candidates": []},
        defense={"coverage_state": "complete", "candidates": []},
        proof={"state": "unresolved"},
        appeal_prosecution={"coverage_state": "complete", "candidates": []},
    )
    initial = proof_engine._appeal_defender_user_v35(**base_kwargs)
    retry = proof_engine._appeal_defender_retry_user_v35(
        failure_category="missing_defense_candidates", **base_kwargs,
    )
    assert retry.startswith(initial)
    assert "APPEAL_RETRY_FEEDBACK=" in retry
    assert "missing_defense_candidates" in retry
    assert '"missing_field":"candidates"' in retry
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"'):
        assert field not in retry


def test_appeal_routes_carry_bounded_transport_budget() -> None:
    """v3.5.4: appeal prosecutor/defender routes must ship an explicit output
    budget and bounded timeouts so a long Camel/000-style math trajectory
    cannot truncate the appeal certificate and fail-close the whole case.

    The contract is fail-closed: the route values just give the provider room
    to return a complete structured response; they do NOT relax the
    coverage_state / reviewed_candidate_ids hard requirements.
    """
    import json
    from pathlib import Path

    config_path = Path(__file__).resolve().parent.parent / "src" / "sehd_v6" / "configs" / "paca_tcl_detector_v3_5.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for route_name in ("appeal_prosecutor_route", "appeal_defender_route"):
        route = config[route_name]
        assert route["max_tokens"] == 8192, route_name
        assert route["timeout_seconds"] == 180, route_name
        assert route["total_timeout_seconds"] == 240, route_name
        assert route["transport_retries"] == 3, route_name


def test_appeal_defender_system_retry_prompt_requires_complete_coverage() -> None:
    """v3.5.4: the defender retry system prompt must state the two hard
    requirements (coverage_state=complete + target_candidate_id coverage)
    so the model cannot satisfy the retry with a partial envelope."""
    prompt = proof_engine.APPEAL_DEFENDER_RETRY_SYSTEM_V35
    assert 'coverage_state' in prompt
    assert '"complete"' in prompt
    assert "target_candidate_id" in prompt
    assert "APPEAL_RETRY_FEEDBACK" in prompt
    assert "response_truncated" in prompt


def test_appeal_prosecutor_retry_system_prompt_states_hard_requirements() -> None:
    """v3.5.4: the prosecutor retry system prompt must state the two hard
    requirements explicitly and map each failure_category to a fix."""
    prompt = proof_engine.APPEAL_PROSECUTOR_RETRY_SYSTEM_V35
    assert "TWO HARD REQUIREMENTS" in prompt
    assert '"complete"' in prompt
    assert "reviewed_candidate_ids" in prompt
    assert "response_truncated" in prompt
    assert "foreign_candidate_id" in prompt
    assert "missing_reviewed_candidate_ids" in prompt


def test_appeal_defense_deep_repair_runs_and_fail_closes_when_still_invalid() -> None:
    """Deep certificate-level defense repair is bounded and never weakens the
    court validator: a still-invalid repair returns the original response and
    the court fail-closes as before."""
    packet = _packet()
    # Initial response is invalid at the certificate level (wrong origin), so
    # the top-level coverage contract passes but the deep validator fails.
    response = {
        "coverage_state": "complete",
        "candidates": [
            {
                "certificate_id": "ad_001",
                "target_candidate_id": "candidate_001",
                "target_origin_event_id": "event_002",  # wrong origin
                "defense_type": "supported_or_correct",
                "evidence_refs": ["event_001"],
                "rationale": "ok",
            },
        ],
    }
    required = [_candidate()]

    def retry_call(invalid_audits):
        return (
            {
                "coverage_state": "complete",
                "candidates": [
                    {
                        "certificate_id": "ad_002",
                        "target_candidate_id": "candidate_001",
                        "target_origin_event_id": "event_001",
                        "defense_type": "supported_or_correct",
                        "evidence_refs": ["event_001"],
                        "rationale": "repaired",
                    },
                ],
            },
            {"cache_hit": False},
        )

    providers: dict = {}
    repaired = proof_engine._validate_appeal_defense_deep_or_repair(
        response,
        built=packet,
        candidates=required,
        base={},
        prosecution={},
        defense={},
        proof={},
        appeal_prosecution=None,
        required_candidates=required,
        retry_call=retry_call,
        providers=providers,
    )
    assert providers["appeal_defender_deep"]["outcome"] == "repaired"
    assert repaired["candidates"][0]["certificate_id"] == "ad_002"

    # A still-invalid repair keeps the original response and records the audit;
    # the court validator (unchanged) will fail-close downstream.
    def bad_retry_call(invalid_audits):
        return (
            {
                "coverage_state": "complete",
                "candidates": [
                    {
                        "certificate_id": "ad_003",
                        "target_candidate_id": "candidate_001",
                        # wrong origin on purpose
                        "target_origin_event_id": "event_002",
                        "defense_type": "supported_or_correct",
                        "evidence_refs": ["event_001"],
                        "rationale": "still wrong",
                    },
                ],
            },
            {"cache_hit": False},
        )

    providers2: dict = {}
    kept = proof_engine._validate_appeal_defense_deep_or_repair(
        response,
        built=packet,
        candidates=required,
        base={},
        prosecution={},
        defense={},
        proof={},
        appeal_prosecution=None,
        required_candidates=required,
        retry_call=bad_retry_call,
        providers=providers2,
    )
    assert providers2["appeal_defender_deep"]["outcome"] == "still_invalid"
    assert kept is response
    # The court still rejects this invalid appeal defense (fail-closed).
    import pytest

    with pytest.raises(ValueError):
        proof_contract.adjudicate_proof_court(
            required_candidates=required,
            prosecution={"coverage_state": "complete", "candidates": []},
            defense={"coverage_state": "complete", "candidates": []},
            packet=packet,
            appeal_defense=kept,
        )


# ---------------------------------------------------------------------------
# v5.8 fix 5: base-layer failure surfacing (observability).  A run that is
# invalidated by the base layer or the construct view must carry a
# machine-readable cause in authority_failure_reasons; the projection is
# diagnostic only and never changes a verdict.
# ---------------------------------------------------------------------------


def test_failure_reasons_surface_base_provider_error():
    base = {
        "providers": {
            "candidate_path_error": {
                "error_type": "IncompleteRead",
                "error": "IncompleteRead(61313 bytes read)",
            },
            "qualification": {"calls": 1},
        },
        "census_audit": {"status": "complete"},
        "candidate_path": {},
    }
    reasons = proof_engine._surface_runtime_failure_reasons(
        proof_reasons=[], base=base,
        construct_authoritative=True, construct_authority_reasons=[],
    )
    assert any(
        r["component"] == "base_layer" and r["code"] == "candidate_path_error"
        for r in reasons
    )
    # Healthy provider entries never surface.
    assert not any(r["code"] == "qualification" for r in reasons)


def test_failure_reasons_surface_base_certificate_quarantine():
    base = {
        "providers": {},
        "census_audit": {"status": "complete"},
        "candidate_path": {
            "state": "unresolved",
            "layer_audits": {
                "candidate_001": {
                    "qualification": {"decision": "pass", "failed_checks": []},
                    "propagation": {
                        "decision": "quarantine",
                        "failed_checks": ["impact_surfaces"],
                    },
                },
            },
        },
    }
    reasons = proof_engine._surface_runtime_failure_reasons(
        proof_reasons=[], base=base,
        construct_authoritative=True, construct_authority_reasons=[],
    )
    quarantined_entries = [
        r for r in reasons if r.get("code") == "certificate_quarantine"
    ]
    assert quarantined_entries
    entry = quarantined_entries[0]
    assert entry["component"] == "base_layer"
    assert entry["quarantined"][0]["candidate_id"] == "candidate_001"
    assert entry["quarantined"][0]["failed_checks"] == ["impact_surfaces"]


def test_failure_reasons_surface_census_fallback_and_construct():
    base = {
        "providers": {},
        "census_audit": {
            "status": "fallback_failed",
            "error_type": "URLError",
            "error": "SSL EOF",
        },
        "candidate_path": {},
    }
    construct_reasons = [{
        "component": "construct_census",
        "code": "construct_not_complete",
        "observed_status": "provider_error",
    }]
    reasons = proof_engine._surface_runtime_failure_reasons(
        proof_reasons=[], base=base,
        construct_authoritative=False,
        construct_authority_reasons=construct_reasons,
    )
    codes = {r.get("code") for r in reasons}
    assert "census_fallback_failed" in codes
    assert "construct_not_complete" in codes


def test_failure_reasons_never_empty_on_invalidation():
    reasons = proof_engine._surface_runtime_failure_reasons(
        proof_reasons=[],
        base={
            "providers": {},
            "census_audit": {"status": "complete"},
            "candidate_path": {},
        },
        construct_authoritative=True, construct_authority_reasons=[],
    )
    assert reasons == [{
        "component": "runtime",
        "code": "unknown_runtime_invalidation",
    }]


def test_failure_reasons_keep_proof_reasons_first():
    proof_reasons = [{"component": "proof_court", "code": "proof_court_exception"}]
    base = {
        "providers": {
            "boundary_error": {"error_type": "ValueError", "error": "bad"},
        },
        "census_audit": {"status": "complete"},
        "candidate_path": {},
    }
    reasons = proof_engine._surface_runtime_failure_reasons(
        proof_reasons=proof_reasons, base=base,
        construct_authoritative=True, construct_authority_reasons=[],
    )
    assert reasons[0]["code"] == "proof_court_exception"
    assert any(r["code"] == "boundary_error" for r in reasons)


def test_load_policy_genome_roundtrip_and_fail_closed(tmp_path: Path) -> None:
    """A persisted L2 policy genome loads explicitly and fail-closed: a
    tampered payload (hash mismatch) or an empty object is rejected."""
    import json

    from src.sehd_v6.core.bccse.policy_genome import (
        PolicyEdit,
        PolicyItem,
        PolicyOp,
        apply_policy_edit,
        create_baseline_policy_genome,
    )
    from src.sehd_v6.core.paca_tcl.detector import load_policy_genome

    genome = create_baseline_policy_genome()
    genome = apply_policy_edit(genome, PolicyEdit(
        edit_id="e1",
        operation=PolicyOp.ADD_ITEM,
        item_index=None,
        new_item=PolicyItem(
            item_id="p1",
            precondition="defense_coverage_complete_and_zero_certificates",
            action="downgrade_coverage",
            params={"side": "defense", "to_state": "incomplete"},
            rationale="hollow defense coverage must not stay complete",
        ),
        rationale="r",
    ))
    path = tmp_path / "policy_genome.json"
    path.write_text(
        json.dumps(genome.to_dict(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    loaded = load_policy_genome(path)
    assert loaded.content_hash == genome.content_hash
    assert loaded.items[0].action == "downgrade_coverage"

    tampered = dict(genome.to_dict())
    tampered["content_hash"] = "deadbeef"
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(
        json.dumps(tampered, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        load_policy_genome(tampered_path)
    except ValueError as exc:
        assert "content_hash" in str(exc)
    else:
        raise AssertionError("tampered policy genome must fail closed")

    empty_path = tmp_path / "empty.json"
    empty_path.write_text("[]\n", encoding="utf-8")
    try:
        load_policy_genome(empty_path)
    except ValueError as exc:
        assert "object" in str(exc)
    else:
        raise AssertionError("non-object policy genome must fail closed")


def test_detect_record_forwards_policy_genome(tmp_path: Path, monkeypatch) -> None:
    """detect_record forwards L2 policy items into the adjudicator and
    records the policy genome hash in provenance."""
    from src.sehd_v6.core.bccse.policy_genome import (
        PolicyEdit,
        PolicyItem,
        PolicyOp,
        apply_policy_edit,
        create_baseline_policy_genome,
    )
    from src.sehd_v6.core.paca_tcl import detector as detector_module

    received: dict = {}

    def fake_adjudicate_case(**kwargs):
        received.update(kwargs)
        return {
            "state": "unresolved",
            "runtime_valid": True,
            "decision_authority": "authoritative",
        }

    monkeypatch.setattr(detector_module, "adjudicate_case", fake_adjudicate_case)

    genome = create_baseline_policy_genome()
    genome = apply_policy_edit(genome, PolicyEdit(
        edit_id="e1",
        operation=PolicyOp.ADD_ITEM,
        item_index=None,
        new_item=PolicyItem(
            item_id="p1",
            precondition="defense_coverage_complete_and_zero_certificates",
            action="downgrade_coverage",
            params={"side": "defense", "to_state": "incomplete"},
            rationale="r",
        ),
        rationale="r",
    ))
    # The standalone detector deploys only the deployable scope (baseline +
    # strong); a freshly accepted item starts ungraded and must be promoted
    # by measured evidence before it can change a deployed verdict.
    meta = genome.get_item_meta(0)
    meta["fitness_status"] = "strong"
    genome.set_item_meta(0, meta)

    result = detect_record(
        record={"history": [{"role": "assistant", "content": "answer"}]},
        config={},
        output_dir=tmp_path,
        policy_genome=genome,
    )
    assert received["policy_items"] == genome.items_for_scope("deployable")
    assert result["provenance"]["policy_genome_hash"] == genome.content_hash
    assert "genome_hash" not in result["provenance"]


def test_load_policy_genome_rejects_vocabulary_drift(tmp_path: Path) -> None:
    """A persisted policy genome whose item drifted from the current closed
    vocabulary (e.g. a reserved engine-level action) fails closed at load
    time instead of being silently skipped by the evaluator (W8)."""
    import json

    from src.sehd_v6.core.bccse.policy_genome import (
        PolicyItem,
        create_baseline_policy_genome,
    )
    from src.sehd_v6.core.paca_tcl.detector import load_policy_genome

    drifted = create_baseline_policy_genome()
    drifted.items.append(PolicyItem(
        item_id="drifted_1",
        precondition="boundary_state_conflicts_with_terminal",
        action="reclassify_candidate_state",
        params={"candidate_ref": "c1", "state": "verified_clean"},
        rationale="reserved engine-level action",
    ))
    payload = drifted.to_dict()
    # Recompute the hash over the drifted payload so only the vocabulary
    # contract (not the hash check) can reject it.
    payload["content_hash"] = drifted.content_hash
    path = tmp_path / "drifted_policy_genome.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        load_policy_genome(path)
    except ValueError as exc:
        assert "closed vocabulary contract" in str(exc)
    else:
        raise AssertionError(
            "vocabulary-drifted policy genome must fail closed at load"
        )

def _built_with_interaction() -> dict:
    return {
        "events": [
            {
                "event_id": "event_001",
                "source_step_id": 1,
                "position": 1,
                "tool_interactions": [
                    {
                        "interaction_id": "event_001_tool_01",
                        "tool_name": "lookup",
                        "arguments": {},
                        "observation": "result",
                    }
                ],
            },
            {
                "event_id": "event_002",
                "source_step_id": 2,
                "position": 2,
                "tool_interactions": [],
            },
        ]
    }


def test_provider_census_accepts_tool_interaction_dependency() -> None:
    from src.sehd_v6.core.paca_tcl.basic_runner import validate_provider_census

    value = {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "construct_candidate_001",
                "origin_hypothesis_event_id": "event_002",
                "trigger_event_id": "event_002",
                "axes": ["P"],
                "candidate_kind": "omission",
                "claim_or_commitment": "the agent skipped a required step",
                "dependency_event_ids": ["event_001_tool_01"],
                "module_tag": "Planning",
                "observation": "the tool result was the dependency",
                "is_decision_point": True,
            }
        ],
    }
    census, audit = validate_provider_census(value, _built_with_interaction())
    assert audit["status"] == "complete"
    assert len(census) == 1


def test_provider_census_rejects_foreign_dependency_reference() -> None:
    from src.sehd_v6.core.paca_tcl.basic_runner import validate_provider_census

    value = {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "construct_candidate_001",
                "origin_hypothesis_event_id": "event_002",
                "trigger_event_id": "event_002",
                "axes": ["P"],
                "candidate_kind": "omission",
                "claim_or_commitment": "the agent skipped a required step",
                "dependency_event_ids": ["event_999"],
                "module_tag": "Planning",
                "observation": "invented reference",
                "is_decision_point": True,
            }
        ],
    }
    census, audit = validate_provider_census(value, _built_with_interaction())
    assert audit["status"] == "partial"
    assert audit["failures"][0]["failed_checks"] == ["dependencies"]
    assert census == []


def test_candidate_validation_accepts_tool_interaction_dependency() -> None:
    from src.sehd_v6.core.paca_tcl import contract_v1

    candidate = {
        "candidate_id": "candidate_001",
        "origin_event_id": "event_001",
        "trigger_event_id": "event_001",
        "axes": ["P"],
        "candidate_kind": "omission",
        "claim_or_commitment": "the agent skipped a required step",
        "dependency_event_ids": ["event_001_tool_01"],
        "violation_state": "unresolved",
        "actor_state": "unresolved",
        "boundary_state": "unresolved",
        "propagation_state": "no_consumption",
        "residual_impact": False,
        "impact_surfaces": [],
        "positive_evidence_refs": [],
        "actor_evidence_refs": [],
        "boundary_evidence_refs": [],
        "consumption_event_ids": [],
        "consumption_evidence_refs": [],
        "impact_event_ids": [],
        "impact_evidence_refs": [],
        "recovery_event_id": None,
        "recovery_evidence_refs": [],
        "rationale": "dependency is a tool result",
    }
    validation = contract_v1.validate_candidate(candidate, _built_with_interaction())
    assert validation["decision"] == "pass", validation["failed_checks"]


def test_provider_census_accepts_tool_interaction_trigger() -> None:
    from src.sehd_v6.core.paca_tcl.basic_runner import validate_provider_census

    value = {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "construct_candidate_001",
                "origin_hypothesis_event_id": "event_001",
                "trigger_event_id": "event_001_tool_01",
                "axes": ["P"],
                "candidate_kind": "invalid_tool_argument",
                "claim_or_commitment": "the tool call was invalid",
                "dependency_event_ids": ["event_001_tool_01"],
                "module_tag": "Tool-Use",
                "observation": "the tool call itself is the trigger",
                "is_decision_point": True,
            }
        ],
    }
    census, audit = validate_provider_census(value, _built_with_interaction())
    assert audit["status"] == "complete"
    assert len(census) == 1


def test_provider_census_normalizes_interaction_origin_to_parent_event() -> None:
    from src.sehd_v6.core.paca_tcl.basic_runner import validate_provider_census

    value = {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "construct_candidate_001",
                "origin_hypothesis_event_id": "event_001_tool_01",
                "trigger_event_id": "event_002",
                "axes": ["P"],
                "candidate_kind": "omission",
                "claim_or_commitment": "the agent skipped a required step",
                "dependency_event_ids": [],
                "module_tag": "Planning",
                "observation": "the tool interaction is the causal origin",
                "is_decision_point": True,
            }
        ],
    }
    census, audit = validate_provider_census(value, _built_with_interaction())
    assert audit["status"] == "complete"
    assert audit["normalized_interaction_origin_count"] == 1
    assert census[0]["origin_hypothesis_event_id"] == "event_001"
    assert census[0]["source_step_id"] == 1


def test_provider_census_rejects_foreign_interaction_origin() -> None:
    from src.sehd_v6.core.paca_tcl.basic_runner import validate_provider_census

    value = {
        "coverage_state": "complete",
        "candidates": [
            {
                "candidate_id": "construct_candidate_001",
                "origin_hypothesis_event_id": "event_999_tool_01",
                "trigger_event_id": "event_002",
                "axes": ["P"],
                "candidate_kind": "omission",
                "claim_or_commitment": "the agent skipped a required step",
                "dependency_event_ids": [],
                "module_tag": "Planning",
                "observation": "foreign interaction is not addressable",
                "is_decision_point": True,
            }
        ],
    }
    census, audit = validate_provider_census(value, _built_with_interaction())
    assert audit["status"] == "partial"
    assert audit["failures"][0]["failed_checks"] == ["origin"]
    assert census == []


# ============================================================
# H3 localization regression: appeal skipped, counterfactual
# re-adjudication must run on a legal evidence bundle.
# ============================================================


def _h3_census_candidate(candidate_id: str, origin: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "origin_hypothesis_event_id": origin,
        "trigger_event_id": origin,
        "axes": ["P"],
        "candidate_kind": "claim",
        "claim_or_commitment": "the agent made an unsupported claim",
        "dependency_event_ids": [],
        "module_tag": "Planning",
        "observation": "observable",
        "is_decision_point": True,
    }


def _h3_error_certificate(
    certificate_id: str,
    candidate_id: str,
    origin: str,
    consumption: str,
    control: str,
    impact: str,
) -> dict:
    return {
        "certificate_id": certificate_id,
        "source_candidate_id": candidate_id,
        "origin_event_id": origin,
        "claim_kind": "agent_assertion",
        "norm_source": "prior_trajectory_evidence",
        "norm_evidence_refs": [origin],
        "breach_type": "contradiction",
        "violation_evidence_refs": [origin],
        "agent_control_event_id": control,
        "agent_control_evidence_refs": [control],
        "impact_kind": "final_answer",
        "impact_event_ids": [impact],
        "impact_evidence_refs": [impact],
        "recovery_state": "post_impact_residual",
        "recovery_evidence_refs": [impact],
        "benchmark_causal_status": "causes_incorrect_result",
        "benchmark_causal_evidence_refs": [impact],
        "consumption_event_ids": [consumption],
        "rationale": (
            "The agent assertion contradicts prior evidence, caused an "
            "incorrect final answer, and was only repaired after the impact."
        ),
    }


def test_h3_localization_runs_when_appeal_is_skipped(
    monkeypatch, tmp_path: Path,
) -> None:
    """Appeal-skipped multi-survivor verified_error must not crash the H3
    counterfactual re-adjudication on the default appeal bundle.

    The default bundle is an EMPTY COMPLETE defense; an "incomplete" stub
    made ``adjudicate_proof_court`` raise ``ValueError`` inside H3, which
    the outer try degraded into a runtime-invalid unresolved result and
    erased the localization outcome (regression guard).
    """
    record = {
        "question": "test",
        "history": [
            {"step": i, "role": "assistant", "content": f"e{i}"}
            for i in range(1, 5)
        ],
    }
    construct_route = {"name": "construct"}
    defense_route = {"name": "defense"}

    def base_case(**_kwargs):
        return {
            "runtime_valid": True,
            "state": "unresolved",
            "census": [],
            "qualifications": [],
            "propagations": [],
            "boundary_path": {
                "state": "unresolved",
                "responsible_event_id": None,
                "evidence_refs": [],
                "rationale": "none",
            },
            "adjudication": {"state": "unresolved"},
            "providers": {},
        }

    def provider(*, route, **_kwargs):
        if route is construct_route:
            return {"coverage_state": "complete", "candidates": [
                _h3_census_candidate("candidate_001", "event_001"),
                _h3_census_candidate("candidate_002", "event_002"),
            ]}, {}
        assert route is defense_route
        return {"coverage_state": "incomplete", "candidates": []}, {}

    def focused_prosecution(*, built, base, config, output_dir):
        # The union census deterministically renames candidates to
        # v3_candidate_00x, so certificates must reference those ids.
        return {"coverage_state": "complete", "candidates": [
            _h3_error_certificate(
                "e1", "v3_candidate_001", "event_001",
                "event_002", "event_002", "event_003",
            ),
            _h3_error_certificate(
                "e2", "v3_candidate_002", "event_002",
                "event_003", "event_003", "event_004",
            ),
        ]}, [], {}

    monkeypatch.setattr(proof_engine.v2, "adjudicate_case", base_case)
    monkeypatch.setattr(proof_engine, "provider_call", provider)
    monkeypatch.setattr(
        proof_engine, "_run_focused_prosecution", focused_prosecution,
    )
    # Appeal never triggers: the initial multi-survivor proof must still
    # reach H3 localization from base evidence.
    monkeypatch.setattr(
        proof_engine, "_appeal_trigger_calibrator_v35", lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        proof_engine, "_run_counterfactual_localization",
        lambda **_kwargs: {
            "event_001": {"causal_sufficiency_score": 1.0, "is_root_cause": True},
        },
    )

    result = proof_engine.adjudicate_case(
        case_id="case",
        record=record,
        config={
            "construct_census_route": construct_route,
            "defense_route": defense_route,
        },
        output_dir=tmp_path,
    )

    proof = result.get("proof_adjudication") or {}
    assert result.get("runtime_valid") is True
    assert result.get("state") == "verified_error"
    assert proof.get("localization_status") == "complete"
    assert proof.get("surviving_error_certificate_ids") == ["e1", "e2"]
