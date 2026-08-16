"""Artifact validation: the flip gate.

One artifact at a time, replayed against the detector's own boundary.
The gate asks exactly one question: does the artifact FLIP a failed
case to the correct verdict?

- stable targets (the failure reproduced under the worksheet re-roll):
  one guided roll must land on the correct polarity;
- unstable targets (the detector flip-flops on the case): three guided
  rolls, majority must land on the correct polarity — the artifact is
  credited with stabilising the case to the right side.

Precision artifacts replay on a frozen census (one fresh unguided roll
fixes the candidate set) so the flip is attributable to the artifact;
recall artifacts replay on a live census because generators cannot run
under a frozen candidate set. Everything else — engagement traces,
counterfactual suites, sentinel tolerances — is deliberately absent:
the flip is the evidence, the read-once validation report is the
representative measurement.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.api import Detector
from sehd_rise2.detector.artifacts import (
    ARTIFACT_REGISTRY_SCHEMA,
    registry_hash,
)
from sehd_rise2.detector.config import load_config
from sehd_rise2.evolution.worksheet import (
    case_record,
    detect_with_retry,
    load_run_tree,
)

VALIDATION_SCHEMA = "sehd2.artifact_validation.v1"
UNSTABLE_ROLLS = 3


def localization_keep(
    *,
    baseline: Sequence[Mapping[str, Any]],
    deployed: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Report-stage responsible-step localization retention.

    ``rows`` are evaluator rows with keys ``case``, ``gold``,
    ``prediction``, ``responsible_step`` and ``gold_step``.
    """
    base_by_case = {str(row["case"]): row for row in baseline}
    dep_by_case = {str(row["case"]): row for row in deployed}
    shared = sorted(set(base_by_case) & set(dep_by_case))
    baseline_located = 0
    kept = 0
    lost: list[str] = []
    for case in shared:
        before = base_by_case[case]
        after = dep_by_case[case]
        gold_step = before.get("gold_step")
        if gold_step is None:
            continue
        before_ok = (
            before.get("prediction") is True
            and before.get("responsible_step") == gold_step
        )
        after_ok = (
            after.get("prediction") is True
            and after.get("responsible_step") == gold_step
        )
        baseline_located += int(bool(before_ok))
        kept += int(bool(before_ok and after_ok))
        if before_ok and not after_ok:
            lost.append(case)
    rate = round(kept / baseline_located, 4) if baseline_located else None
    return {
        "baseline_located": baseline_located,
        "kept": kept,
        "lost": lost,
        "keep_rate": rate,
    }


def artifact_is_recall(artifact: Mapping[str, Any]) -> bool:
    gap = artifact.get("gap") or {}
    return (
        artifact.get("kind") == "candidate_generator"
        or str(gap.get("gap_type") or "") == "recall"
    )


def target_cases(artifact: Mapping[str, Any]) -> list[str]:
    provenance = artifact.get("provenance") or {}
    evidence = provenance.get("case_evidence") or []
    if isinstance(evidence, list):
        return sorted({str(case_id) for case_id in evidence})
    return []


def one_artifact_registry(artifact: Mapping[str, Any]) -> dict[str, Any]:
    registry = {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "artifacts": [dict(artifact)],
    }
    registry["content_hash"] = registry_hash(registry)
    return registry


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )


def _correct_state(state: str, gold: bool) -> bool:
    """Is this terminal state on the gold-correct polarity?"""
    return (
        state == "verified_error" if gold
        else state in ("verified_clean", "evidence_insufficient")
    )


def validate_artifacts(
    *,
    run_tree: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    dataset_root: Path,
    out_dir: Path,
    workers: int = 20,
    unstable_cases: Sequence[str] = (),
) -> dict[str, Any]:
    """The flip gate, one artifact at a time, over its target cases."""
    detector = Detector(load_config())
    unstable = set(str(case_id) for case_id in unstable_cases)
    lock = threading.Lock()
    records: dict[str, Mapping[str, Any]] = {}
    censuses: dict[str, list[Mapping[str, Any]]] = {}
    scopes = {
        str(artifact.get("artifact_id")): target_cases(artifact)
        for artifact in artifacts
    }
    replay_pool = sorted(
        {case_id for targets in scopes.values() for case_id in targets}
    )

    def roll_case(case_id: str) -> None:
        record = case_record(case_id, dataset_root)
        result = detect_with_retry(detector, case_id, record, None, None)
        census = [
            dict(candidate)
            for candidate in (
                (result.get("proof_adjudication") or {}).get("census")
                or []
            )
        ]
        with lock:
            records[case_id] = record
            censuses[case_id] = census

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for _ in executor.map(roll_case, replay_pool):
            pass

    reports: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        artifact_id = str(artifact.get("artifact_id") or "")
        registry = one_artifact_registry(artifact)
        recall = artifact_is_recall(artifact)

        def guided_state(case_id: str) -> str:
            rolls = UNSTABLE_ROLLS if case_id in unstable else 1
            states = []
            for _ in range(rolls):
                frozen = None if recall else censuses.get(case_id)
                result = detect_with_retry(
                    detector, case_id, records[case_id], registry, frozen,
                )
                states.append(str(result.get("state")))
            return max(set(states), key=states.count)

        rows: dict[str, dict[str, Any]] = {}
        for case_id in scopes[artifact_id]:
            gold = bool(run_tree[case_id]["gold"])
            state = guided_state(case_id)
            rows[case_id] = {
                "gold": gold,
                "stable": case_id not in unstable,
                "guided_state": state,
                "flipped": _correct_state(state, gold),
            }
        flipped = sorted(
            case_id for case_id, row in rows.items() if row["flipped"]
        )
        reports[artifact_id] = {
            "artifact": dict(artifact),
            "metrics": {
                "targets": sorted(rows),
                "flipped_cases": flipped,
                "flips": len(flipped),
            },
            "per_target": rows,
            "deployable": bool(flipped),
        }
    deployable = [
        artifact_id for artifact_id, report in reports.items()
        if report["deployable"]
    ]
    payload = {
        "schema_version": VALIDATION_SCHEMA,
        "per_artifact": reports,
        "deployable": deployable,
    }
    _write_json(Path(out_dir) / "artifact_validation.json", payload)
    return payload


__all__ = [
    "UNSTABLE_ROLLS",
    "VALIDATION_SCHEMA",
    "artifact_is_recall",
    "localization_keep",
    "one_artifact_registry",
    "target_cases",
    "validate_artifacts",
]
