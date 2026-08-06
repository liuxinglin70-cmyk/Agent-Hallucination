"""Phase 0 baseline: freeze causal evolution fixtures and behaviors.

This suite locks the deterministic inputs of the causal skill loop before
any episode/memory work lands: fixture identity, attribution ownership,
ActionSpec compilation, offline replay call counts and the evaluator label
firewall.  Any of these changing in later phases is a contract break.
"""

from __future__ import annotations

import json

import pytest

from src.sehd_v6.core.bccse.action_spec import compile_action_spec
from src.sehd_v6.core.bccse.module_charter import (
    MODULE_ACTION_OPCODES,
    MODULE_OPERATION_TARGETS,
)
from src.sehd_v6.core.bccse.reflection_engine import _strip_evaluator_fields
from src.sehd_v6.core.bccse.v6_contracts import (
    LAYER_L1,
    LAYER_L2,
    LAYER_NONE,
    L2_ARTIFACT_CONSENSUS_GATE,
)

from tests.fixtures.causal_evolution import (
    classifier_contract,
    detection_result,
    edit_payload,
    frozen_attribution,
    invalid_edit_payload,
    l2_policy_movement,
    malformed_certificate,
    missing_evidence_field,
    signal_for,
    stable_clean,
    unconsumed_provider_output,
)

PRESETS = (
    stable_clean,
    missing_evidence_field,
    malformed_certificate,
    unconsumed_provider_output,
    l2_policy_movement,
)


def _serialized(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def test_fixture_detection_results_are_deterministic() -> None:
    for preset in PRESETS:
        first = preset()
        second = preset()
        assert _serialized(first) == _serialized(second)
        assert frozen_attribution(first) == frozen_attribution(second)
        assert classifier_contract(first) == classifier_contract(second)


def test_fixture_attribution_ownership_is_frozen() -> None:
    assert frozen_attribution(stable_clean()) == {
        "layer": LAYER_NONE,
        "artifact": "",
        "reason_code": "no_failure_shape",
    }
    assert frozen_attribution(missing_evidence_field()) == {
        "layer": LAYER_L1,
        "artifact": "evidence_obligation",
        "reason_code": "provider_artifact_missing",
    }
    assert frozen_attribution(malformed_certificate()) == {
        "layer": LAYER_L1,
        "artifact": "atomic_verifier_instruction",
        "reason_code": "provider_artifact_malformed",
    }
    assert frozen_attribution(unconsumed_provider_output()) == {
        "layer": LAYER_NONE,
        "artifact": "",
        "reason_code": "no_failure_shape",
    }
    assert frozen_attribution(l2_policy_movement()) == {
        "layer": LAYER_L2,
        "artifact": L2_ARTIFACT_CONSENSUS_GATE,
        "reason_code": "consensus_gate_blocked",
    }


def test_fixture_action_spec_compilation_is_deterministic() -> None:
    for module_id, opcodes in MODULE_ACTION_OPCODES.items():
        targets = MODULE_OPERATION_TARGETS.get(module_id, {})
        for operation, _template in opcodes:
            if not targets.get(operation):
                continue
            action = f"{operation}: {targets[operation][0]}"
            first = compile_action_spec(module_id, action).to_dict()
            second = compile_action_spec(module_id, action).to_dict()
            assert first == second
            assert first["module_id"] == module_id
            assert first["operation"] == operation
    with pytest.raises(ValueError):
        compile_action_spec("evidence_obligation", "quarantine_certificate: x")


def test_offline_fixture_replay_makes_no_external_calls(
    tmp_path, monkeypatch, addressable_l1_failure,
) -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    results: dict[str, dict] = {}

    def detector(*, genome, case_id, **kwargs):
        value = results.get(case_id)
        if value is None:
            value = {
                "source": missing_evidence_field(),
                "anchor": stable_clean(),
            }[case_id]
            results[case_id] = value
        return value

    monkeypatch.setattr(runner, "_run_detection_with_genome", detector)
    monkeypatch.setattr(
        runner,
        "attribute_failure",
        lambda result, use_gold=False, gold_reflection=False: signal_for(
            str(result["state"])
        ),
    )
    monkeypatch.setattr(
        runner, "json_object_provider_call",
        lambda **kwargs: (edit_payload(), {}),
    )

    def run(output_dir):
        return runner.run_online_evolution(
            evolution_case_ids=("source",),
            canary_case_ids=("anchor",),
            records={"source": {}, "anchor": {}},
            config={},
            output_dir=output_dir,
            census_root=output_dir,
            mutation_route=None,
            run_id="fixture_offline",
        )

    first = run(tmp_path / "first")
    second = run(tmp_path / "second")

    assert first.evolution_audit["mutation_calls"] == 0
    assert first.evolution_audit["detector_calls"] == 2  # 1 source + 1 canary
    assert first.evolution_audit["provider_calls"] == 2
    assert [step.gate_decision for step in first.steps] == ["skip_no_edit"]
    assert [step.gate_decision for step in second.steps] == [
        step.gate_decision for step in first.steps
    ]
    assert second.evolution_audit["mutation_calls"] == 0


def test_label_firewall_strips_evaluator_fields() -> None:
    from src.sehd_v6.core.bccse import evolution_runner as runner

    poisoned = {
        "state": "unresolved",
        "trajectory": {
            "events": [
                {"step": 3, "content": "ok"},
                {
                    "step": 4,
                    "gold_label": True,
                    "gold_explanation": "secret",
                    "is_hallucination": "true",
                    "hallucination_step": "4",
                },
            ]
        },
        "case_id": "leaked_case",
    }
    stripped = _strip_evaluator_fields(poisoned)
    serialized = json.dumps(stripped, ensure_ascii=False)
    for secret in ("gold_label", "gold_explanation", "is_hallucination",
                   "hallucination_step", "leaked_case"):
        assert secret not in serialized

    record = {
        "gold_label": True,
        "gold_explanation": "x",
        "history": [{"role": "assistant", "content": "ok"}],
    }
    envelope = runner._detector_record(record)
    assert "gold_label" not in envelope
    assert "gold_explanation" not in envelope
    assert envelope["history"][0]["content"] == "ok"


def test_invalid_mutation_payload_fails_action_spec_compile() -> None:
    payload = invalid_edit_payload()
    from src.sehd_v6.core.bccse.action_spec import compile_action_spec
    from src.sehd_v6.core.bccse.module_charter import validate_action_surface

    valid, reason = validate_action_surface(
        payload["target_module"], payload["action"],
    )
    assert not valid
    assert "action_opcode_unowned" in reason
    with pytest.raises(ValueError):
        compile_action_spec(payload["target_module"], payload["action"])


def test_replay_pair_presets_are_distinct() -> None:
    assert detection_result(
        state="unresolved",
    ) != stable_clean()
    assert unconsumed_provider_output()["proof_adjudication"][
        "valid_error_certificate_count"
    ] == 1
    assert malformed_certificate()["evidence_health"][
        "quarantined_error_certificate_count"
    ] == 1
