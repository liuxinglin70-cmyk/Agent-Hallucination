"""Layered PACA-TCL v2 base layer of the standalone detector.

Batch experiment entry points (preflight / P0 manifest runs) have been removed;
experiment protocols live outside this package.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ...utils import ocse_packet as packet
from . import layered_contract as contract
from .basic_runner import (
    _responsible_step,
    candidate_provider_user_prompt,
    candidate_system_prompt,
    load_paca_census,
    parse_gold_label,
    private_ref,
    provider_call,
    validate_provider_census,
)


QUALIFICATION_SYSTEM = """You are PACA-TCL v2 qualification layer. Audit every supplied PACA candidate independently using only the trajectory. Decide only: violation, actor responsibility, and counter-boundary. Do not decide propagation, recovery, impact, final case label, or scores. A triggered boundary means the apparent deviation is attributable to environment/external input, explicitly authorized, or otherwise outside agent responsibility. Evidence references must be supplied event/interaction IDs. Return JSON only:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"candidate_id":"...","violation_state":"verified_violation|verified_non_violation|unresolved","actor_state":"agent_caused|agent_contributed|environment_caused|external_input_caused|unresolved","boundary_state":"cleared|triggered|unresolved","positive_evidence_refs":[],"actor_evidence_refs":[],"boundary_evidence_refs":[],"rationale":"..."}]}"""

PROPAGATION_SYSTEM = """You are PACA-TCL v2 propagation layer. Every supplied candidate already passed semantic/responsibility/boundary qualification. Decide only whether its addressable origin was consumed, caused impact, and was recovered. Do not revisit violation or actor responsibility. Use event IDs for causal nodes and event/interaction IDs for evidence. Return JSON only:
{"coverage_state":"complete|incomplete|unresolved","candidates":[{"candidate_id":"...","propagation_state":"no_consumption|recovered_before_impact|impacted_then_recovered|unrecovered_impact|unresolved","residual_impact":false,"consumption_event_ids":[],"consumption_evidence_refs":[],"impact_event_ids":[],"impact_evidence_refs":[],"recovery_event_id":null,"recovery_evidence_refs":[],"impact_surfaces":[],"rationale":"..."}]}"""

BOUNDARY_SYSTEM = """You are the independent BCCC-style case boundary path. Determine whether the complete trajectory contains an addressable agent obligation breach that crosses authorization, grounding, execution-state, or answer-integrity boundaries. Ordinary exploration, tool/environment failure, external misinformation, and fully recovered deviations are clean. Do not use scores or PACA/TCL conclusions. Return JSON only. Keep candidates as an empty list for transport compatibility:
{"state":"verified_error|verified_clean|unresolved","responsible_event_id":"event_001 or null","evidence_refs":[],"rationale":"...","candidates":[]}"""


def _provider_user(built: dict[str, Any], *, census: list[dict[str, Any]] | None = None, qualification: list[dict[str, Any]] | None = None) -> str:
    sections = ["TRAJECTORY_PACKET=\n" + packet.canonical_json(built)]
    if census is not None:
        sections.append("CANDIDATES=\n" + packet.canonical_json(census))
    if qualification is not None:
        sections.append("QUALIFICATION_OUTPUT=\n" + packet.canonical_json(qualification))
    value = "\n".join(sections)
    for field in ('"is_hallucination"', '"hallucination_step"', '"gold_label"', '"case_id"', '"source_dir"', '"outer_feedback"'):
        if field in value:
            raise ValueError("provider input firewall violation: " + field)
    return value


def _coverage(value: dict[str, Any], expected: set[str]) -> tuple[str, list[dict[str, Any]]]:
    rows = value.get("candidates")
    coverage = str(value.get("coverage_state") or "unresolved")
    if not isinstance(rows, list):
        return "unresolved", []
    returned = {str(row.get("candidate_id") or "") for row in rows if isinstance(row, dict)}
    if coverage != "complete" or returned != expected:
        coverage = "incomplete"
    return coverage, rows


def _validate_boundary(value: dict[str, Any], built: dict[str, Any]) -> dict[str, Any]:
    event_ids = {str(row["event_id"]) for row in built["events"]}
    evidence_ids = set(event_ids)
    for event in built["events"]:
        evidence_ids.update(str(row["interaction_id"]) for row in event.get("tool_interactions") or [])
    state = str(value.get("state") or "unresolved")
    responsible = value.get("responsible_event_id")
    evidence = value.get("evidence_refs")
    checks = {
        "state": state in contract.CASE_STATES,
        "responsible": responsible is None or responsible in event_ids,
        "error_has_origin": state != "verified_error" or responsible in event_ids,
        "evidence": isinstance(evidence, list) and all(ref in evidence_ids for ref in evidence),
        "error_has_evidence": state != "verified_error" or bool(evidence),
        "rationale": bool(str(value.get("rationale") or "").strip()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "state": state if not failed else "unresolved",
        "responsible_event_id": responsible if not failed and state == "verified_error" else None,
        "evidence_refs": evidence if not failed else [],
        "rationale": str(value.get("rationale") or "") if not failed else "",
        "contract_validation": {"decision": "pass" if not failed else "quarantine", "failed_checks": failed},
    }


def _empty_candidate_path(state: str) -> dict[str, Any]:
    return {
        "state": state, "responsible_event_id": None, "candidate_states": {},
        "candidate_state_counts": {}, "layer_audits": {}, "coverage_complete": state == "verified_clean",
    }


def adjudicate_case(*, case_id: str, record: dict[str, Any], config: dict[str, Any], output_dir: Path, census_root: Path | None = None) -> dict[str, Any]:
    built = packet.build_packet(record)
    census, census_audit = load_paca_census(case_id=case_id, built=built, cache_root=census_root)
    candidate_provider: dict[str, Any] = {"calls": 0, "source": "frozen_paca_stage1_cache"}
    if not census or census_audit.get("status") != "complete":
        try:
            value, meta = provider_call(
                route=config["candidate_fallback_route"], system=candidate_system_prompt(),
                user=candidate_provider_user_prompt(built), cache_dir=output_dir / "candidate_provider_cache",
            )
            census, generated = validate_provider_census(value, built)
            census_audit = {**generated, "fallback_from": census_audit}
            candidate_provider = {**meta, "calls": 1, "source": "provider_fallback"}
        except Exception as exc:
            census, census_audit = [], {"status": "fallback_failed", "error_type": type(exc).__name__, "error": str(exc)[:500]}
            candidate_provider = {"calls": 1, "source": "provider_fallback", "error_type": type(exc).__name__}

    result: dict[str, Any] = {
        "case_ref": private_ref(case_id, "case_"),
        "census": census, "census_audit": census_audit, "candidate_provider": candidate_provider,
    }
    providers: dict[str, Any] = {}
    try:
        if census:
            q_value, providers["qualification"] = provider_call(
                route=config["qualification_route"], system=QUALIFICATION_SYSTEM,
                user=_provider_user(built, census=census), cache_dir=output_dir / "qualification_cache",
            )
            q_coverage, qualifications = _coverage(q_value, {row["candidate_id"] for row in census})
            qualified = []
            for row in qualifications:
                if contract.validate_qualification(row, built)["decision"] == "pass" and contract.qualification_state(row) == "qualified":
                    qualified.append(row)
            qualified_ids = {row["candidate_id"] for row in qualified}
            qualified_census = [row for row in census if row["candidate_id"] in qualified_ids]
            if qualified_census:
                p_value, providers["propagation"] = provider_call(
                    route=config["propagation_route"], system=PROPAGATION_SYSTEM,
                    user=_provider_user(built, census=qualified_census, qualification=qualified),
                    cache_dir=output_dir / "propagation_cache",
                )
                p_coverage, propagations = _coverage(p_value, qualified_ids)
            else:
                p_coverage, propagations = "complete", []
                providers["propagation"] = {"calls": 0, "reason": "no_qualified_candidates"}
            candidate_path = contract.adjudicate_candidate_path(
                census=census, census_coverage="complete" if census_audit.get("status") == "complete" else "incomplete",
                qualifications=qualifications, qualification_coverage=q_coverage,
                propagations=propagations, propagation_coverage=p_coverage, packet=built,
            )
        else:
            qualifications, propagations = [], []
            candidate_path = _empty_candidate_path("verified_clean" if census_audit.get("status") == "complete" else "unresolved")
    except Exception as exc:
        qualifications, propagations = [], []
        candidate_path = _empty_candidate_path("unresolved")
        providers["candidate_path_error"] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}

    try:
        boundary_value, providers["boundary"] = provider_call(
            route=config["boundary_route"], system=BOUNDARY_SYSTEM, user=_provider_user(built),
            cache_dir=output_dir / "boundary_cache",
        )
        boundary_path = _validate_boundary(boundary_value, built)
    except Exception as exc:
        boundary_path = {"state": "unresolved", "responsible_event_id": None, "contract_validation": {"decision": "quarantine", "error_type": type(exc).__name__}}
        providers["boundary_error"] = {"error_type": type(exc).__name__, "error": str(exc)[:500]}

    final = contract.merge_case_paths(candidate_path, boundary_path)
    result.update({
        "runtime_valid": census_audit.get("status") != "fallback_failed" and not any(key.endswith("_error") for key in providers),
        "state": final["state"], "responsible_event_id": final["responsible_event_id"],
        "responsible_step": _responsible_step(final["responsible_event_id"], built),
        "qualifications": qualifications, "propagations": propagations,
        "candidate_path": candidate_path, "boundary_path": boundary_path,
        "adjudication": final, "providers": providers,
    })
    return result


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    confusion, states, path_pairs = Counter(), Counter(), Counter()
    runtime_valid = step_correct = positive_total = candidate_hits = 0
    for row in rows:
        raw_gold = row.get("gold_label")
        if raw_gold is None:
            continue
        gold = parse_gold_label(raw_gold)
        state = str(row.get("state") or "unresolved")
        predicted = state == "verified_error"
        positive_total += int(gold)
        runtime_valid += int(bool(row.get("runtime_valid")))
        states[row["state"]] += 1
        path_pairs[(row["candidate_path"]["state"], row["boundary_path"]["state"])] += 1
        if state == "unresolved":
            confusion["unresolved"] += 1
            continue
        if gold and predicted:
            confusion["tp"] += 1
            step_correct += int(row.get("responsible_step") in set(row.get("gold_steps") or []))
        elif gold:
            confusion["fn"] += 1
        elif predicted:
            confusion["fp"] += 1
        else:
            confusion["tn"] += 1
        census_steps = {
            str(item.get("source_step_id"))
            for item in row.get("census") or []
            if item.get("source_step_id") is not None
        }
        candidate_hits += int(gold and bool(census_steps & {str(step) for step in row.get("gold_steps") or []}))
    tp, fp, fn, tn = (confusion[key] for key in ("tp", "fp", "fn", "tn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1_pos = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    p_clean = tn / (tn + fn) if tn + fn else 0.0
    r_clean = tn / (tn + fp) if tn + fp else 0.0
    f1_clean = 2 * p_clean * r_clean / (p_clean + r_clean) if p_clean + r_clean else 0.0
    return {
        "case_count": len(rows), "runtime_valid_rate": runtime_valid / len(rows) if rows else 0.0,
        "confusion": dict(confusion), "macro_f1": (f1_pos + f1_clean) / 2,
        "precision": precision, "recall": recall, "clean_fpr": fp / (fp + tn) if fp + tn else 0.0,
        "responsible_step_recall_over_all_gold_positive": step_correct / positive_total if positive_total else 0.0,
        "candidate_origin_coverage_on_gold_positive": candidate_hits / positive_total if positive_total else 0.0,
        "tri_state_counts": dict(states),
        "path_pair_counts": {"|".join(key): value for key, value in path_pairs.items()},
    }
