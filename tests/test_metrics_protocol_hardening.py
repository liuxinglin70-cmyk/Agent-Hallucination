from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from src.sehd_v6 import main
from src.sehd_v6.bccse_v5_data_partition import partition_data


def _row(
    case_id: str,
    gold: bool,
    *,
    state: str,
    incident_state: str | None = None,
    occurrence_state: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "case_id": case_id,
        "gold_label": gold,
        "state": state,
    }
    if incident_state is not None:
        row["incident_state"] = incident_state
    if occurrence_state is not None:
        row["occurrence_state"] = occurrence_state
    return row


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_metrics_use_incident_state_and_do_not_call_unresolved_clean() -> None:
    baseline = [
        _row("c1", False, state="verified_clean"),
        _row("c2", True, state="verified_clean"),
    ]
    skill = [
        _row(
            "c1",
            False,
            state="verified_clean",
            incident_state="unresolved",
        ),
        _row(
            "c2",
            True,
            state="verified_clean",
            incident_state="verified_hallucination",
        ),
    ]

    metrics = main.compute_metrics(skill, baseline)

    assert metrics["state_authority"].startswith("occurrence_state")
    assert metrics["skill_three_state_confusion"]["gold_negative"]["unresolved"] == 1
    assert metrics["skill_decisive_confusion"]["tn"] == 0
    assert metrics["skill_decision_coverage"] == pytest.approx(0.5)
    assert metrics["skill_selective_accuracy"] == pytest.approx(1.0)
    # The historical collapse remains available, but is explicitly labelled.
    assert metrics["skill_binary_compatibility"]["tn"] == 1
    assert "compatibility" in metrics["skill_binary_compatibility"]["warning"]
    assert metrics["paired_transition_table"]["verified_clean"]["unresolved"] == 1


def test_occurrence_state_overrides_legacy_incident_state() -> None:
    conflict = _row(
        "c1",
        True,
        state="verified_clean",
        incident_state="verified_non_hallucination",
        occurrence_state="verified_error",
    )
    assert main._prediction_state(conflict) == "verified_error"
    metrics = main.compute_metrics([conflict], [_row("c1", True, state="verified_clean")])
    assert metrics["skill_three_state_confusion"]["gold_positive"]["verified_error"] == 1


def test_nested_occurrence_sources_precede_structured_and_legacy_incident() -> None:
    assert main._prediction_state(
        {
            "proof_adjudication": {"occurrence_state": "verified_error"},
            "incident_state": "verified_non_hallucination",
            "state": "verified_clean",
        }
    ) == "verified_error"
    assert main._prediction_state(
        {
            "occurrence_ledger": {"state": "unresolved"},
            "incident_state": {"occurrence_state": "verified_clean"},
            "state": "verified_error",
        }
    ) == "unresolved"


def test_formal_baseline_rejects_invalid_decision_authority() -> None:
    invalid = {
        "state": "verified_error",
        "occurrence_state": "verified_error",
        "gold_label": True,
        "runtime_valid": True,
        "proof_adjudication": {"decision_authority": "invalid"},
    }
    assert main._decision_authority_invalid(invalid) is True
    with pytest.raises(RuntimeError, match="decision authority invalid"):
        main._normalize_cached_baseline("Source/001", invalid)


def test_exact_mcnemar_and_claim_gate_require_full_evidence() -> None:
    baseline = [
        _row(f"c{index}", True, state="verified_clean")
        for index in range(6)
    ]
    skill = [
        _row(f"c{index}", True, state="verified_error")
        for index in range(6)
    ]

    blocked = main.compute_metrics(skill, baseline)
    mcnemar = blocked["paired_correctness"]["mcnemar"]
    assert mcnemar["method"] == "exact_two_sided_binomial_no_scipy"
    assert mcnemar["p_value"] == pytest.approx(0.03125)
    assert mcnemar["significant"] is True
    assert blocked["claim_eligibility"]["eligible_for_champion_label"] is False
    assert "confidence_interval_missing" in blocked["claim_eligibility"]["blocking_reasons"]
    assert "outer_evaluation_missing" in blocked["claim_eligibility"]["blocking_reasons"]

    small_outer = main.compute_metrics(
        skill,
        baseline,
        claim_context={
            "outer_evaluated": True,
            "evaluation_set": "outer",
            "outer_custody_verified": True,
            "critical_provenance_complete": True,
        },
    )
    assert small_outer["paired_correctness"]["net_gain_confidence_interval"][
        "status"
    ] == "insufficient_sample"
    assert small_outer["claim_eligibility"]["eligible_for_champion_label"] is False
    assert "paired_ci_insufficient_sample" in small_outer["claim_eligibility"][
        "blocking_reasons"
    ]


def test_outer_claim_gate_is_reachable_but_inner_sets_are_not() -> None:
    baseline = [
        _row(f"c{index}", True, state="verified_clean")
        for index in range(20)
    ]
    skill = [
        _row(f"c{index}", True, state="verified_error")
        for index in range(20)
    ]
    outer = main.compute_metrics(
        skill,
        baseline,
        claim_context={
            "outer_evaluated": True,
            "evaluation_set": "outer",
            "outer_custody_verified": True,
            "critical_provenance_complete": True,
        },
    )
    interval = outer["paired_correctness"]["net_gain_confidence_interval"]
    assert interval["status"] == "complete"
    assert interval["method"].startswith("wilson_95_on_discordant")
    assert interval["total_pair_denominator"] == 20
    assert interval["lower"] > 0
    assert outer["claim_eligibility"]["eligible_for_champion_label"] is True

    no_custody = main.compute_metrics(
        skill,
        baseline,
        claim_context={
            "outer_evaluated": True,
            "evaluation_set": "outer",
            "outer_custody_verified": False,
            "critical_provenance_complete": True,
        },
    )
    assert "outer_custody_unverified" in no_custody["claim_eligibility"][
        "blocking_reasons"
    ]

    validation = main.compute_metrics(
        skill,
        baseline,
        claim_context={
            "outer_evaluated": True,
            "evaluation_set": "validation",
            "outer_custody_verified": True,
            "critical_provenance_complete": True,
        },
    )
    assert validation["claim_eligibility"]["eligible_for_champion_label"] is False


def test_abstention_only_migration_cannot_be_promoted() -> None:
    baseline = [
        _row(f"c{index}", True, state="unresolved")
        for index in range(6)
    ]
    skill = [
        _row(f"c{index}", True, state="verified_error")
        for index in range(6)
    ]
    metrics = main.compute_metrics(
        skill,
        baseline,
        claim_context={
            "outer_evaluated": True,
            "evaluation_set": "outer",
            "outer_custody_verified": True,
            "critical_provenance_complete": True,
        },
    )

    gate = metrics["claim_eligibility"]
    assert gate["paired_significant_correctness_gain"] is True
    assert gate["only_abstention_migration"] is True
    assert gate["eligible_for_champion_label"] is False


def test_frozen_p0_authorization_cannot_enter_validation() -> None:
    config = {
        "execution_authorized": True,
        "authorized_phase": "P0_diagnostic_only",
        "D_execution_authorized": False,
    }
    with pytest.raises(RuntimeError, match="diagnostic phase"):
        main._assert_stage_authorized(config, stage="validation")
    with pytest.raises(RuntimeError, match="diagnostic phase"):
        main._assert_stage_authorized(config, stage="outer")
    with pytest.raises(RuntimeError, match="D cases"):
        main._assert_stage_authorized(
            config,
            stage="evolve",
            contains_d_cases=True,
        )
    main._assert_stage_authorized(config, stage="evolve", contains_d_cases=False)
    main._assert_stage_authorized(
        {
            "execution_authorized": True,
            "authorized_phase": "D_validation_effectiveness",
            "D_execution_authorized": True,
        },
        stage="validation",
        contains_d_cases=True,
    )
    with pytest.raises(RuntimeError, match="outer_execution_authorized"):
        main._assert_stage_authorized(
            {
                "execution_authorized": True,
                "authorized_phase": "Outer_final_effectiveness",
                "D_execution_authorized": True,
                "outer_execution_authorized": False,
            },
            stage="outer",
        )
    main._assert_stage_authorized(
        {
            "execution_authorized": True,
            "authorized_phase": "Outer_final_effectiveness",
            "D_execution_authorized": True,
            "outer_execution_authorized": True,
        },
        stage="outer",
    )


def test_evaluate_entry_checks_authorization_before_artifact_access(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "load_config",
        lambda: {
            "execution_authorized": True,
            "authorized_phase": "P0_diagnostic_only",
            "D_execution_authorized": False,
        },
    )
    args = Namespace(project_root=None, set="test", genome="never-opened.json", workers=1)
    with pytest.raises(RuntimeError, match="diagnostic phase"):
        main.cmd_evaluate_concurrent(args)


def test_outer_entry_requires_explicit_outer_authorization(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "load_config",
        lambda: {
            "execution_authorized": True,
            "authorized_phase": "D_outer_final_effectiveness",
            "D_execution_authorized": True,
            "outer_execution_authorized": False,
        },
    )
    args = Namespace(project_root=None, set="outer", genome="never-opened.json", workers=1)
    with pytest.raises(RuntimeError, match="outer_execution_authorized"):
        main.cmd_evaluate_concurrent(args)


def _outer_manifest(partition: dict[str, object], case_ids: list[str]) -> dict[str, object]:
    refs = [main._case_ref(case_id) for case_id in case_ids]
    assignments = [
        {"case_id": case_id, "case_ref": case_ref, "role": "outer"}
        for case_id, case_ref in zip(case_ids, refs)
    ]
    return {
        "role": "outer",
        "outer_case_ids": case_ids,
        "outer_case_refs": refs,
        "role_assignments": assignments,
        "role_assignment_sha256": main._canonical_sha256(assignments),
        "partition_payload_sha256": main._canonical_sha256(partition),
    }


def test_outer_manifest_is_explicit_hash_bound_and_disjoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "bccse"
    root.mkdir()
    partition: dict[str, object] = {"schema_version": "partition.test"}
    (root / "validation_set_ids.json").write_text(
        json.dumps({"validation_case_ids": ["Inner/validation"]}),
        encoding="utf-8",
    )
    (root / "test_set_ids.json").write_text(
        json.dumps({"test_case_ids": ["Inner/test"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "BCCSE_V5_ROOT", root)
    monkeypatch.setattr(
        main,
        "_load_registered_evolution_ids",
        lambda: ["Inner/evolution"],
    )
    monkeypatch.setattr(
        main,
        "_load_inner_case_refs",
        lambda: {
            main._case_ref("Inner/evolution"),
            main._case_ref("Inner/validation"),
            main._case_ref("Inner/test"),
        },
    )

    with pytest.raises(FileNotFoundError, match="explicit outer_set_ids"):
        main._load_evaluation_ids(set_name="outer", partition=partition)

    manifest_path = root / "outer_set_ids.json"
    manifest_path.write_text(
        json.dumps(_outer_manifest(partition, ["Outer/final"])),
        encoding="utf-8",
    )
    ids, source = main._load_evaluation_ids(set_name="outer", partition=partition)
    assert ids == ["Outer/final"]
    assert source == str(manifest_path)

    bad = _outer_manifest(partition, ["Outer/final"])
    bad["partition_payload_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash-bound"):
        main._load_evaluation_ids(set_name="outer", partition=partition)

    overlap = _outer_manifest(partition, ["Inner/test"])
    manifest_path.write_text(json.dumps(overlap), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Test/outer overlap"):
        main._load_evaluation_ids(set_name="outer", partition=partition)


def test_partition_requires_outer_evidence_and_records_provenance(tmp_path: Path) -> None:
    d_path = tmp_path / "d.jsonl"
    p0_path = tmp_path / "p0.jsonl"
    output = tmp_path / "out"
    inner_rows = [
        {
            "case_ref": f"inner-{index}",
            "split": "inner",
            "state": "verified_clean",
            "gold_label": False,
        }
        for index in range(4)
    ]
    _write_jsonl(d_path, inner_rows)
    _write_jsonl(p0_path, [])

    with pytest.raises(RuntimeError, match="No explicit Outer marker"):
        partition_data(
            d_predictions_path=d_path,
            p0_predictions_path=p0_path,
            output_dir=output,
            evolution_pool_size=1,
            validation_size=1,
            main_evolution_size=1,
        )

    rows_with_outer = [
        *inner_rows,
        {
            "case_id": "Outer/001",
            "case_ref": main._case_ref("Outer/001"),
            "split": "outer",
            "state": "verified_clean",
            "gold_label": False,
        },
    ]
    unmarked = [dict(row) for row in rows_with_outer]
    unmarked[0].pop("split")
    _write_jsonl(d_path, unmarked)
    with pytest.raises(RuntimeError, match="Every partition row requires"):
        partition_data(
            d_predictions_path=d_path,
            p0_predictions_path=p0_path,
            output_dir=output,
            evolution_pool_size=1,
            validation_size=1,
            main_evolution_size=1,
        )

    _write_jsonl(d_path, rows_with_outer)
    result = partition_data(
        d_predictions_path=d_path,
        p0_predictions_path=p0_path,
        output_dir=output,
        evolution_pool_size=1,
        validation_size=1,
        main_evolution_size=1,
    )

    assert result["input_provenance"]["D"]["sha256"]
    assert result["input_role_counts"]["D"] == {"total": 5, "inner": 4, "outer": 1}
    assert result["mutual_exclusion"]["valid"] is True
    assert all(
        count == 0
        for count in result["mutual_exclusion"]["pairwise_overlap_counts"].values()
    )
    outer_manifest = json.loads(
        (output / "outer_set_ids.json").read_text(encoding="utf-8")
    )
    assert outer_manifest["outer_case_ids"] == ["Outer/001"]
    assert outer_manifest["role"] == "outer"
    assert outer_manifest["partition_payload_sha256"] == main._canonical_sha256(
        result
    )


def test_v3_partition_probe_and_final_holdout_are_disjoint_and_development_reusable(
    tmp_path: Path,
) -> None:
    d_path = tmp_path / "d.jsonl"
    output = tmp_path / "partition"
    rows = [
        {
            "case_id": f"Development/{index:03d}",
            "case_ref": main._case_ref(f"Development/{index:03d}"),
            "split": "development",
            "state": "unresolved" if index % 2 else "verified_clean",
            "gold_label": bool(index % 3),
        }
        for index in range(8)
    ]
    _write_jsonl(d_path, rows)

    result = partition_data(
        d_predictions_path=d_path,
        output_dir=output,
        seed=7,
        probe_size=2,
        holdout_size=2,
        main_evolution_size=4,
    )

    development = set(result["development_pool"]["case_refs"])
    probe = set(result["gold_probe"]["case_refs"])
    final_holdout = set(result["final_holdout"]["case_refs"])
    evolution = set(result["evolution_pool"]["case_refs"])
    assert len(development) == 6
    assert len(probe) == 2
    assert len(final_holdout) == 2
    assert probe <= development
    assert probe.isdisjoint(final_holdout)
    assert development.isdisjoint(final_holdout)
    assert evolution == development - probe
    assert result["validation_set"]["alias_of"] == "gold_probe"
    assert result["outer_sealed"]["alias_of"] == "final_holdout"
    assert result["holdout_sealed"] is True
    assert result["final_holdout"]["read_only"] is True

    development_manifest = json.loads(
        (output / "development_pool_ids.json").read_text(encoding="utf-8")
    )
    assert set(development_manifest["evolution_case_ids"]).isdisjoint(
        development_manifest["probe_case_ids"]
    )
    assert len(development_manifest["evolution_case_ids"]) == 4


def test_new_development_manifest_is_preferred_but_old_evolution_manifest_works(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "bccse"
    partition_dir = root / "partition"
    partition_dir.mkdir(parents=True)
    (root / "stratified_evolution_set.json").write_text(
        json.dumps({"evolution_case_ids": ["Legacy/001"]}),
        encoding="utf-8",
    )
    (partition_dir / "development_pool_ids.json").write_text(
        json.dumps(
            {
                "development_case_ids": ["Dev/001", "Dev/probe"],
                "probe_case_ids": ["Dev/probe"],
                "evolution_case_ids": ["Dev/001"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "BCCSE_V5_ROOT", root)
    monkeypatch.setattr(main, "_assert_inner_membership", lambda *args, **kwargs: None)
    assert main._load_registered_evolution_ids() == ["Dev/001"]

    (partition_dir / "development_pool_ids.json").unlink()
    assert main._load_registered_evolution_ids() == ["Legacy/001"]


def test_probe_metrics_never_open_the_final_claim_gate() -> None:
    baseline = [_row(f"c{index}", True, state="verified_clean") for index in range(20)]
    skill = [_row(f"c{index}", True, state="verified_error") for index in range(20)]
    metrics = main.compute_metrics(
        skill,
        baseline,
        claim_context={
            "evaluation_set": "probe",
            "critical_provenance_complete": True,
        },
    )
    gate = metrics["claim_eligibility"]
    assert gate["eligible_for_champion_label"] is False
    assert "final_holdout_evaluation_missing" in gate["blocking_reasons"]
