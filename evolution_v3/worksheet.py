"""Mechanical failure attribution (worksheet).

This is the only place case ids and mechanism statistics meet. Candidate
provenance may carry a failure family and its case ids, but no case id may
appear in directive/body/claim prose (enforced by V1).
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from detector_v2.dataset import load_case

from evolution_v3.metrics import source_of


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _judge_parsed(result: Mapping[str, Any]) -> Mapping[str, Any]:
    return (result.get("proof") or {}).get("judge") or {}


def _upgrade_status(result: Mapping[str, Any]) -> str:
    statuses = result.get("stage_status") or {}
    return str((statuses.get("causal_upgrade") or {}).get("state") or "")


def _coverage(result: Mapping[str, Any]) -> dict[str, Any] | None:
    proof = result.get("proof") or {}
    coverage = proof.get("causal_upgrade_coverage")
    if isinstance(coverage, dict):
        return coverage
    rows = proof.get("causal_upgrade_rows")
    if isinstance(rows, list) and rows:
        return {
            "expected": len(rows),
            "answered": sum(
                1 for row in rows if row.get("coverage") == "answered"
            ),
            "missing": [
                row.get("candidate_id") for row in rows
                if row.get("coverage") == "missing"
            ],
        }
    return None


def failure_family(row: Mapping[str, Any]) -> str:
    """Mechanical failure-family id; never case-id conditioned."""
    gold = row.get("gold")
    prediction = row.get("prediction")
    result = row.get("result") or {}
    proof = result.get("proof") or {}
    if prediction is None:
        reasons = result.get("authority_failure_reasons") or []
        code = (
            str(reasons[0].get("component") or "unknown")
            if reasons else "unknown"
        )
        return f"FAM-unresolved-{_sha(code)}"
    if gold is True and prediction is False:
        judge = _judge_parsed(result)
        coverage = _coverage(result)
        if coverage and coverage.get("missing"):
            return "FAM-fn-upgrade_missing_rows"
        defense = proof.get("defense") or {}
        if bool(judge.get("is_hallucination")) and defense.get("verdict") == "refuted":
            return "FAM-fn-defense_refuted_judge"
        if (
            proof.get("causal_upgrade")
            and defense.get("verdict") == "refuted"
        ):
            return "FAM-fn-defense_refuted_recall_upgrade"
        recall_candidates = proof.get("recall_candidates")
        if not recall_candidates:
            return "FAM-fn-judge_false_no_recall_candidates"
        if not proof.get("causal_upgrade"):
            return "FAM-fn-judge_false_upgrade_no_upgrade"
        return f"FAM-fn-{_sha(str(judge.get('confidence') or 'none'))}"
    if gold is False and prediction is True:
        source = (proof.get("terminal_basis") or {}).get(
            "decisive_facts", {}
        ).get("positive_source")
        defense = (proof.get("defense") or {})
        verdict = defense.get("verdict")
        if source == "recall_upgrade":
            return "FAM-fp-recall_upgrade_conviction"
        if source == "judge" and verdict != "substantiated":
            return f"FAM-fp-judge_conviction_defense_{verdict or 'none'}"
        return "FAM-fp-judge_conviction"
    return "FAM-none"


def stable_failure_families(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_cases: int = 2,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        gold = row.get("gold")
        if gold is not True and gold is not False:
            continue
        if str(row.get("authority") or "") != "authoritative":
            continue
        result = row.get("result") or {}
        if result and not bool(result.get("runtime_valid", True)):
            continue
        statuses = result.get("stage_status") or {}
        if any(
            str((status or {}).get("state") or "").startswith(
                ("provider_error", "invalid", "contract_error", "degraded")
            )
            for status in statuses.values()
        ):
            continue
        if row.get("prediction") == gold:
            continue
        family = failure_family(row)
        groups.setdefault(family, []).append(row)
    families = []
    for family, members in sorted(groups.items()):
        if len(members) < min_cases:
            continue
        families.append({
            "failure_family": family,
            "count": len(members),
            "failure_cases": [str(row.get("case") or "") for row in members],
            "sources": sorted({
                source_of(str(row.get("case") or "")) for row in members
            }),
            "mechanism_summary": _mechanism_summary(family, members),
        })
    families.sort(key=lambda item: (-item["count"], item["failure_family"]))
    return families


def _mechanism_summary(
    family: str,
    members: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {"family": family, "n": len(members)}
    if family == "FAM-fn-upgrade_missing_rows":
        coverages = []
        for row in members:
            coverage = _coverage(row.get("result") or {})
            if coverage:
                coverages.append(coverage)
        if coverages:
            summary["mean_missing_rows"] = (
                sum(len(c.get("missing") or []) for c in coverages)
                / len(coverages)
            )
    if family.startswith("FAM-fn-judge_false"):
        judge_flags = [
            bool(_judge_parsed(row.get("result") or {}).get("is_hallucination"))
            for row in members
        ]
        summary["judge_false_fraction"] = (
            1.0 - sum(judge_flags) / len(judge_flags)
        )
    return summary


def worksheet_report(
    patrol_rows: Sequence[Mapping[str, Any]],
    families: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    errors = [row for row in patrol_rows if row.get("prediction") != row.get("gold")]
    return {
        "patrol_size": len(patrol_rows),
        "errors": len(errors),
        "fn": sum(1 for r in errors if r.get("gold") is True),
        "fp": sum(1 for r in errors if r.get("gold") is False),
        "stable_failure_families": families,
    }


_VIOLATION_VERDICTS = {
    "violated", "unresolved", "uncovered", "inconsistent",
}


def diagnose_failure_families(
    families: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    *,
    dataset_root: str | None = None,
) -> list[dict[str, Any]]:
    """Ground candidate templates in mechanical predicate evidence.

    This is a proposer-side quality improvement, not a gate relaxation: a
    family gets a ``predicate_signals`` table computed from the bare-base
    failure packets. Templates prefer predicates that actually fire on the
    family. No case id ever enters a candidate's prose.
    """
    from detector_v2.packet import build_packet
    from detector_v2.predicates import run_predicate

    row_by_case = {str(row.get("case") or ""): row for row in rows}
    diagnosed: list[dict[str, Any]] = []
    for family in families:
        item = dict(family)
        members = [
            row_by_case.get(str(case) or "")
            for case in item.get("failure_cases") or ()
        ]
        members = [row for row in members if row is not None]
        signal: dict[str, Any] = {
            "n": len(members),
            "predicate_signals": {},
        }
        if dataset_root:
            applicable: dict[str, int] = {}
            violations: dict[str, int] = {}
            for row in members:
                case = str(row.get("case") or "")
                result = row.get("result") or {}
                proof = result.get("proof") or {}
                judge = proof.get("judge") or {}
                candidates = proof.get("recall_candidates") or []
                defense = proof.get("defense") or {}
                refs = list(judge.get("evidence_refs") or [])
                quote = (judge.get("root_cause") or {}).get("quote") or ""
                if not refs and candidates:
                    refs = [
                        ref
                        for candidate in candidates
                        for ref in candidate.get("evidence_refs") or ()
                    ]
                    quote = quote or (candidates[0].get("quote") or "")
                if not refs and defense:
                    refs = list(defense.get("evidence_refs") or [])
                contexts = {
                    "required_action_present": {},
                    "verbatim_fidelity": {},
                    "evidence_refs_resolve": {
                        "evidence_refs": refs,
                        "quote": quote,
                    },
                    "contract_coverage": {
                        "contracts": judge.get("contracts") or [],
                    },
                    "delivered_anchor_consistency": {
                        "delivered_result": judge.get("delivered_result") or "",
                    },
                }
                try:
                    record, _gold = load_case(dataset_root, case)
                    packet = build_packet(record)
                except Exception:
                    continue
                for predicate, context in contexts.items():
                    try:
                        verdict = run_predicate(
                            predicate, packet, context,
                        ).get("verdict")
                    except Exception:
                        continue
                    if verdict != "not_applicable":
                        applicable[predicate] = applicable.get(predicate, 0) + 1
                    if verdict in _VIOLATION_VERDICTS:
                        violations[predicate] = violations.get(predicate, 0) + 1
            signal["predicate_signals"] = {
                predicate: {
                    "applicable": applicable.get(predicate, 0),
                    "violations": violations.get(predicate, 0),
                }
                for predicate in (
                    "required_action_present",
                    "verbatim_fidelity",
                    "evidence_refs_resolve",
                    "contract_coverage",
                    "delivered_anchor_consistency",
                )
            }
        item["diagnostics"] = signal
        diagnosed.append(item)
    return diagnosed


__all__ = [
    "diagnose_failure_families",
    "failure_family",
    "stable_failure_families",
    "worksheet_report",
]
