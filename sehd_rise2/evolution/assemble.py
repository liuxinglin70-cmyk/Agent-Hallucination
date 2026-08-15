"""Assemble, validate and report the Phase 0 Learning Case dataset.

The learning case tree is a PROJECTION, derived exactly like
``runs.summarize_run`` derives its ledger: the run tree holds the
detector facts, the dataset holds gold, ``annotations.json`` holds the
human layer, and every file under ``datasets/learning_cases/v1/cases/``
is regenerable from those three. Edit annotations, never the projection.

    python -m sehd_rise2.evolution.assemble assemble \
        --run runs/random100_20260815_142446 \
        --dataset datasets/raw/agenthallu/AgentHallu \
        --annotations datasets/learning_cases/v1/annotations.json \
        --out datasets/learning_cases/v1
    python -m sehd_rise2.evolution.assemble validate \
        --dir datasets/learning_cases/v1
    python -m sehd_rise2.evolution.assemble report \
        --dir datasets/learning_cases/v1 \
        --out datasets/learning_cases/v1/REPORT.md

Layer discipline: this module reads the detector's frozen outputs only
(``result.prediction_state`` authority reader, run-tree files). It never
imports the detection runtime, and the gold-bearing learning cases it
writes must never be fed back into the detector.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.result import prediction_state
from sehd_rise2.evolution.diagnosis import (
    classify_failure,
    derive_failure_stage,
    localization_facts,
)
from sehd_rise2.evolution.gap_taxonomy import TAXONOMY_FROZEN
from sehd_rise2.evolution.learning_case import (
    LEARNING_CASE_SCHEMA,
    validate_annotations,
    validate_learning_case,
)

_TRUE_TOKENS = frozenset({"true", "1", "yes"})
_FALSE_TOKENS = frozenset({"false", "0", "no"})


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _coerce_gold(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_TOKENS:
            return True
        if normalized in _FALSE_TOKENS:
            return False
        return None
    if value in (1,):
        return True
    if value in (0,):
        return False
    return None


def _safe_case_id(case_id: str) -> str:
    """Filesystem-safe file stem; the original id stays inside files."""
    cleaned = (
        str(case_id).replace("/", "__").replace("\\", "__")
        .replace(":", "_")
    )
    if cleaned.endswith(".json"):
        cleaned = cleaned[:-5]
    return cleaned or "case"


def load_run_index(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Read the run tree: case_id -> result / audit / packet."""
    index: dict[str, dict[str, Any]] = {}
    for result_path in sorted((run_dir / "cases").glob("*/result.json")):
        result = _read_json(result_path)
        case_id = str(result.get("case_id") or "")
        if not case_id:
            continue
        case_dir = result_path.parent
        index[case_id] = {
            "result": result,
            "audit": _read_json(case_dir / "audit.json"),
            "packet": _read_json(case_dir / "packet.json"),
        }
    return index


def _is_upheld(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("verdict") == "upheld"
    return value == "upheld"


def _locus_position(packet: Mapping[str, Any], locus_id: Any) -> int:
    """Event position of a locus id; interaction ids resolve to parent."""
    text = str(locus_id or "")
    if not text:
        return 0
    parent = text.rsplit("_tool_", 1)[0] if "_tool_" in text else text
    for event in packet.get("events") or []:
        if event.get("event_id") in (text, parent):
            return int(event.get("position") or 0)
    return 0


def _trajectory_summary(packet: Mapping[str, Any]) -> str:
    events = list(packet.get("events") or [])
    roles = Counter(str(event.get("role") or "?") for event in events)
    tools = sorted({
        str(interaction.get("tool_name"))
        for event in events
        for interaction in event.get("tool_interactions") or []
        if interaction.get("tool_name")
    })
    chars = sum(len(str(event.get("content") or "")) for event in events)
    return (
        f"{len(events)} events (roles: {dict(roles)}); "
        f"tools: {', '.join(tools) or 'none'}; content {chars} chars"
    )


def _routes(audit: Mapping[str, Any]) -> dict[str, str]:
    providers = audit.get("providers")
    providers = providers if isinstance(providers, Mapping) else {}
    return {
        str(name): str(meta.get("model"))
        for name, meta in providers.items()
        if isinstance(meta, Mapping) and meta.get("model")
    }


def build_learning_case(
    *, case_id: str, run_entry: Mapping[str, Any],
    dataset_root: Path, annotation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = run_entry["result"]
    audit = run_entry["audit"]
    packet = run_entry["packet"]
    stage_audit = audit.get("stage_audit") or {}
    proof = result.get("proof_adjudication") or {}
    decisive = (proof.get("terminal_basis") or {}).get(
        "decisive_facts"
    ) or {}
    raw = _read_json(dataset_root / case_id)
    gold = _coerce_gold(raw.get("is_hallucination"))
    gold_step = raw.get("hallucination_step")
    gold_step = int(gold_step) if str(gold_step or "").isdigit() else None
    category = " / ".join(
        str(raw.get(key) or "") for key in (
            "hallucination_category", "hallucination_subcategory",
        ) if raw.get(key)
    )
    annotation = annotation or {}
    failure_type = classify_failure(
        gold=gold, prediction=prediction_state(result),
        gold_step=gold_step, responsible_step=result.get(
            "responsible_step"
        ),
    )
    stage, fact = derive_failure_stage(
        failure_type=failure_type, decisive_facts=decisive,
    )
    if failure_type == "LOCALIZATION":
        defense = proof.get("defense_decisions") or {}
        loci = [
            {
                "candidate_id": candidate.get("candidate_id"),
                "position": _locus_position(
                    packet, candidate.get("locus_event_id"),
                ),
                "upheld": _is_upheld(
                    defense.get(str(candidate.get("candidate_id"))),
                ),
            }
            for candidate in proof.get("census") or []
        ]
        fact = fact + "; " + localization_facts(
            gold_step=gold_step, loci=loci,
        )
    override = annotation.get("failed_stage_override")
    analysis: dict[str, Any] = {
        "failure_type": failure_type,
        "failed_stage": str(override["stage"]) if override else stage,
        "failed_stage_source": "human_override" if override else "mechanical",
        "override_reason": (
            str(override["reason"]) if override else None
        ),
        "failure_observation": fact,
        "missed_candidates": annotation.get("missed_candidates") or [],
        "evidence_gap": annotation.get("evidence_gap"),
    }
    return {
        "schema_version": LEARNING_CASE_SCHEMA,
        "case_id": case_id,
        "source": {
            "run_id": audit.get("run_id") or "",
            "dataset": str(dataset_root),
            "family": case_id.split("/", 1)[0],
            "question_domain": raw.get("question_domain"),
            "question_source": raw.get("question_source"),
        },
        "input": {
            "trajectory_summary": _trajectory_summary(packet),
            "task_type": " / ".join(
                str(raw.get(key) or "") for key in (
                    "question_domain", "question_source",
                ) if raw.get(key)
            ),
            "agent_behavior": annotation.get("agent_behavior"),
        },
        "ground_truth": {
            "hallucination_label": gold,
            "hallucination_type": category,
            "gold_step": gold_step,
            "root_cause": annotation.get("root_cause"),
        },
        "detector_execution": {
            "final_decision": result.get("state"),
            "responsible_step": result.get("responsible_step"),
            "responsible_event_id": result.get("responsible_event_id"),
            "census": proof.get("census") or [],
            "verification": {
                "certificates": proof.get("certificates") or [],
            },
            "defense": {"decisions": proof.get("defense_decisions") or {}},
            "decisive_facts": decisive,
            "evidence_coverage": stage_audit.get("evidence_coverage") or {},
            "census_second_pass": stage_audit.get("census_second_pass"),
            "census_deterministic_augment": stage_audit.get(
                "census_deterministic_augment"
            ),
            "routes": _routes(audit),
        },
        "failure_analysis": analysis,
        "failure_hypothesis": annotation.get("failure_hypothesis"),
        "capability_analysis": annotation.get("capability_analysis"),
    }


def assemble(
    *, run_dir: Path, dataset_root: Path, annotations_path: Path | None,
    out_dir: Path,
) -> int:
    index = load_run_index(run_dir)
    if not index:
        print(f"no cases found under {run_dir}")
        return 1
    annotations: Mapping[str, Any] = {}
    if annotations_path is not None:
        payload = _read_json(annotations_path)
        checks = validate_annotations(payload)
        if not all(checks.values()):
            print(f"invalid annotations envelope: {checks}")
            return 1
        annotations = payload.get("cases") or {}
    unknown = sorted(set(annotations) - set(index))
    if unknown:
        print(f"annotations reference unknown cases: {unknown}")
        return 1
    invalid: list[str] = []
    for case_id in sorted(index):
        case = build_learning_case(
            case_id=case_id, run_entry=index[case_id],
            dataset_root=dataset_root,
            annotation=annotations.get(case_id),
        )
        checks = validate_learning_case(case)
        if not all(checks.values()):
            invalid.append(
                f"{case_id}: "
                + ",".join(
                    key for key, passed in checks.items() if not passed
                )
            )
        _write_json(out_dir / "cases" / f"{_safe_case_id(case_id)}.json", case)
    print(f"assembled {len(index)} learning cases -> {out_dir / 'cases'}")
    if invalid:
        print("validation failures:")
        for line in invalid:
            print(f"  {line}")
        return 1
    print("validation: all cases green")
    return 0


def validate_tree(*, cases_dir: Path) -> int:
    failures = 0
    count = 0
    for path in sorted(cases_dir.glob("*.json")):
        case = _read_json(path)
        checks = validate_learning_case(case)
        count += 1
        if not all(checks.values()):
            failures += 1
            failed = ",".join(
                key for key, passed in checks.items() if not passed
            )
            print(f"{path.name}: {failed}")
    print(f"validated {count} learning cases, {failures} invalid")
    return 1 if failures else 0


def build_report(*, cases_dir: Path) -> dict[str, Any]:
    cases = [
        _read_json(path) for path in sorted(cases_dir.glob("*.json"))
    ]
    by_type = Counter(
        str((case.get("failure_analysis") or {}).get("failure_type"))
        for case in cases
    )
    stage_by_type: dict[str, Counter[str]] = {}
    gap_counts: Counter[str] = Counter()
    overrides = 0
    for case in cases:
        analysis = case.get("failure_analysis") or {}
        failure_type = str(analysis.get("failure_type"))
        stage_by_type.setdefault(failure_type, Counter())[
            str(analysis.get("failed_stage"))
        ] += 1
        if analysis.get("failed_stage_source") == "human_override":
            overrides += 1
        gap = (
            (case.get("capability_analysis") or {}).get("capability_gap")
            or {}
        )
        if gap:
            gap_counts[
                f"{gap.get('category')}/{gap.get('name')}"
                f"{' (proposed)' if gap.get('status') == 'proposed' else ''}"
            ] += 1
    return {
        "total_cases": len(cases),
        "failure_type_counts": dict(by_type),
        "failed_stage_by_type": {
            key: dict(counter) for key, counter in stage_by_type.items()
        },
        "human_overrides": overrides,
        "capability_gap_counts": dict(
            gap_counts.most_common(),
        ),
        "taxonomy_frozen": TAXONOMY_FROZEN,
    }


def write_report(*, cases_dir: Path, out_path: Path) -> int:
    report = build_report(cases_dir=cases_dir)
    lines = [
        "# Phase 0 Learning Case Dataset — Capability Gap Report",
        "",
        f"- generated: {datetime.now(timezone.utc).isoformat()}",
        f"- total learning cases: {report['total_cases']}",
        f"- taxonomy frozen: {report['taxonomy_frozen']}",
        f"- human stage overrides: {report['human_overrides']}",
        "",
        "## Failure type counts",
        "",
        "| failure_type | count |",
        "|---|---|",
    ]
    for key, value in sorted(report["failure_type_counts"].items()):
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Failed stage by failure type", ""]
    for failure_type, counter in sorted(
        report["failed_stage_by_type"].items(),
    ):
        for stage, value in sorted(counter.items()):
            lines.append(f"- {failure_type} / {stage}: {value}")
    lines += [
        "", "## Capability gap distribution (annotated failures)", "",
    ]
    for key, value in report["capability_gap_counts"].items():
        lines.append(f"- {key}: {value}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written -> {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    assemble_parser = sub.add_parser(
        "assemble", help="assemble learning cases from run tree + gold",
    )
    assemble_parser.add_argument("--run", type=Path, required=True)
    assemble_parser.add_argument("--dataset", type=Path, required=True)
    assemble_parser.add_argument("--annotations", type=Path, default=None)
    assemble_parser.add_argument("--out", type=Path, required=True)
    validate_parser = sub.add_parser(
        "validate", help="validate an assembled learning case tree",
    )
    validate_parser.add_argument("--dir", type=Path, required=True)
    report_parser = sub.add_parser(
        "report", help="aggregate the capability gap distribution",
    )
    report_parser.add_argument("--dir", type=Path, required=True)
    report_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "assemble":
        return assemble(
            run_dir=args.run, dataset_root=args.dataset,
            annotations_path=args.annotations, out_dir=args.out,
        )
    if args.command == "validate":
        return validate_tree(cases_dir=args.dir)
    return write_report(cases_dir=args.dir, out_path=args.out)


if __name__ == "__main__":
    raise SystemExit(main())
