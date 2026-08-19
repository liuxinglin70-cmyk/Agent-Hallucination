"""Recall safety net for judge=false cases.

Two separate calls with different authority:

- ``run_recall_scan`` finds candidate deviations (high recall, no verdict);
- ``run_causal_upgrade`` turns a candidate into a positive ONLY when the
  deviation is both factually real and causally connected to the delivered
  result.

A scanner candidate can never convict by itself.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from detector_v2.interventions import InterventionPlan, empty_plan
from detector_v2.packet import packet_features, render_judge_view
from detector_v2.provider import ProviderError, provider_call
from detector_v2.semantics import (
    RECALL_CONTRACT_REPAIR,
    RECALL_SYSTEM,
    UPGRADE_CONTRACT_REPAIR,
    UPGRADE_MISSING_REPAIR,
    UPGRADE_SYSTEM,
)


def _valid_event_ids(packet: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for event in packet.get("events") or []:
        ids.add(str(event.get("event_id") or ""))
        for interaction in event.get("tool_interactions") or []:
            ids.add(str(interaction.get("interaction_id") or ""))
    return ids


def _parse_candidates(parsed: Any, packet: Mapping[str, Any]):
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("candidates"), list
    ):
        return None, "recall output is not a candidates object"
    universe = _valid_event_ids(packet)
    candidates = []
    seen: set[str] = set()
    for index, raw in enumerate(parsed["candidates"], start=1):
        if not isinstance(raw, Mapping):
            return None, f"candidate {index} is not an object"
        candidate_id = str(raw.get("candidate_id") or f"c{index}")
        if candidate_id in seen:
            return None, f"duplicate candidate id {candidate_id}"
        seen.add(candidate_id)
        claim = str(raw.get("claim") or "").strip()
        locus = str(raw.get("locus_event_id") or "").strip()
        refs_raw = raw.get("evidence_refs")
        if refs_raw is None:
            refs_raw = []
        if not isinstance(refs_raw, list) or any(
            not isinstance(item, str) for item in refs_raw
        ):
            return None, f"candidate {candidate_id} evidence_refs must be a list"
        refs = [str(x) for x in refs_raw if str(x)]
        if not claim or not locus:
            return None, f"candidate {candidate_id} needs claim and locus"
        if locus not in universe or any(ref not in universe for ref in refs):
            return None, f"candidate {candidate_id} references unknown event"
        candidates.append({
            "candidate_id": candidate_id,
            "claim": claim,
            "locus_event_id": locus,
            "evidence_refs": refs,
            "quote": str(raw.get("quote") or "").strip(),
        })
    return candidates, ""


def run_recall_scan(
    *,
    packet: Mapping[str, Any],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = plan or empty_plan()
    route = dict(config["recall_route"])
    route["_route_name"] = "recall_scanner"
    view_text, view_meta = render_judge_view(packet)
    system = RECALL_SYSTEM + plan.prompt_block(packet_features(packet), hook="scanner.system")
    user = (
        "Scan the trajectory below for candidate deviations. Do not judge "
        "whether the trajectory is hallucinated.\n\n" + view_text
    )
    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="recall_scanner",
        )
    except ProviderError as exc:
        return {
            "ok": False, "candidates": [], "error": exc.message,
            "error_type": exc.error_type,
        }, {
            "stage": "recall_scanner", "state": "provider_error",
            "attempts": exc.attempts,
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": [],
        }
    candidates, error = _parse_candidates(parsed, packet)
    warnings: list[dict[str, str]] = []
    if candidates is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = provider_call(
                route=route, system=system,
                user=user + RECALL_CONTRACT_REPAIR,
                purpose="recall_scanner_repair",
            )
            candidates, error = _parse_candidates(parsed, packet)
        except ProviderError as exc:
            return {
                "ok": False, "candidates": [], "error": exc.message,
                "error_type": exc.error_type,
            }, {
                "stage": "recall_scanner", "state": "provider_error",
                "attempts": exc.attempts,
                "view": "complete" if not view_meta.get("degraded") else "degraded",
                "warnings": warnings,
            }
    if candidates is None:
        return {
            "ok": False, "candidates": [], "error": error,
            "error_type": "contract_error",
        }, {
            "stage": "recall_scanner", "state": "contract_error",
            "attempts": int(meta.get("attempts") or 0),
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": warnings,
        }
    return {
        "ok": True, "candidates": candidates, "provider": meta,
        "view_meta": view_meta,
    }, {
        "stage": "recall_scanner", "state": "ok",
        "attempts": int(meta.get("attempts") or 0),
        "view": "complete" if not view_meta.get("degraded") else "degraded",
        "warnings": warnings,
    }


def _parse_upgrades(
    parsed: Any,
    candidates: Sequence[Mapping[str, Any]],
    packet: Mapping[str, Any] | None = None,
):
    """Parse explicit upgrade rows and report silently-missing candidates.

    Missing rows are returned separately so the caller can re-ask or degrade.
    They are never silently interpreted as ``content_error=false``.
    """
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("upgrades"), list
    ):
        return None, [], "upgrade output is not an upgrades object"
    valid_ids = {str(c.get("candidate_id")) for c in candidates}
    event_count = len(list((packet or {}).get("events") or ()))
    rows_by_id: dict[str, dict[str, Any]] = {}
    for raw in parsed["upgrades"]:
        if not isinstance(raw, Mapping):
            return None, [], "upgrade entry is not an object"
        candidate_id = str(raw.get("candidate_id") or "")
        if candidate_id not in valid_ids:
            continue
        if candidate_id in rows_by_id:
            return None, [], f"duplicate upgrade row for candidate {candidate_id}"
        content_error = raw.get("content_error")
        causal = raw.get("causal")
        if not isinstance(content_error, bool) or not isinstance(causal, bool):
            return None, [], (
                f"content_error and causal must be booleans "
                f"(candidate {candidate_id})"
            )
        corrected_step = raw.get("corrected_step")
        if content_error and causal:
            if not isinstance(corrected_step, int) or isinstance(
                corrected_step, bool
            ):
                return None, [], (
                    f"corrected_step must be an integer for upgrades "
                    f"(candidate {candidate_id})"
                )
            if packet is not None and not 1 <= corrected_step <= event_count:
                return None, [], (
                    f"corrected_step is outside the packet event range "
                    f"(candidate {candidate_id})"
                )
        elif corrected_step is not None:
            return None, [], (
                f"corrected_step must be null for non-upgrades "
                f"(candidate {candidate_id})"
            )
        rows_by_id[candidate_id] = {
            "candidate_id": candidate_id,
            "content_error": content_error,
            "causal": causal,
            "corrected_step": corrected_step,
            "reason": str(raw.get("reason") or "").strip(),
        }
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        row = rows_by_id.get(candidate_id)
        if row is None:
            missing.append(candidate_id)
            rows.append({
                "candidate_id": candidate_id,
                "content_error": False,
                "causal": False,
                "corrected_step": None,
                "reason": "missing from upgrade response (degraded, not a no-upgrade)",
                "coverage": "missing",
            })
        else:
            rows.append({**row, "coverage": "answered"})
    return rows, missing, ""


def run_causal_upgrade(
    *,
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = plan or empty_plan()
    if not candidates:
        return {
            "ok": True, "upgrades": [], "upgraded_candidate": None,
        }, {
            "stage": "causal_upgrade", "state": "not_needed",
            "attempts": 0, "view": "not_rendered", "warnings": [],
        }
    route = dict(config["judge_route"])
    route["_route_name"] = "causal_upgrade"
    evidence = plan.evidence_for(
        "upgrade.pre",
        packet,
        {"candidates": candidates},
    )
    evidence_block = ""
    if evidence:
        lines = [
            "\nMECHANICAL PRE-CHECKS (facts from the trajectory/contract, "
            "not a verdict):"
        ]
        for item in evidence:
            lines.append(
                f"- {item['predicate']}: {item['verdict']} ({item['detail']})"
            )
        evidence_block = "\n".join(lines)
    system = UPGRADE_SYSTEM + plan.prompt_block(packet_features(packet), hook="upgrade.system")
    view_text, view_meta = render_judge_view(packet)
    user = (
        "Decide whether any candidate below can upgrade this trajectory to "
        "hallucinated. Apply both tests to every candidate.\n\nCANDIDATES:\n"
        + "\n".join(
            f"- {c['candidate_id']}: {c['claim']} "
            f"(locus {c['locus_event_id']}, refs {c['evidence_refs']}, "
            f"quote {c['quote'] or '?'})"
            for c in candidates
        )
        + evidence_block
        + "\n\nTRAJECTORY:\n" + view_text
    )
    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose="causal_upgrade",
        )
    except ProviderError as exc:
        return {
            "ok": False, "upgrades": [], "error": exc.message,
            "error_type": exc.error_type,
        }, {
            "stage": "causal_upgrade", "state": "provider_error",
            "attempts": exc.attempts,
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": [],
        }
    upgrades, missing, error = _parse_upgrades(parsed, candidates, packet)
    warnings: list[dict[str, str]] = []
    if upgrades is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = provider_call(
                route=route, system=system,
                user=user + UPGRADE_CONTRACT_REPAIR,
                purpose="causal_upgrade_repair",
            )
            upgrades, missing, error = _parse_upgrades(parsed, candidates, packet)
        except ProviderError as exc:
            return {
                "ok": False, "upgrades": [], "error": exc.message,
                "error_type": exc.error_type,
            }, {
                "stage": "causal_upgrade", "state": "provider_error",
                "attempts": exc.attempts,
                "view": "complete" if not view_meta.get("degraded") else "degraded",
                "warnings": warnings,
            }
    if upgrades is None:
        return {
            "ok": False, "upgrades": [], "error": error,
            "error_type": "contract_error",
            "provider": meta,
        }, {
            "stage": "causal_upgrade", "state": "contract_error",
            "attempts": int(meta.get("attempts") or 0),
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": warnings,
        }

    if missing:
        # Per-candidate coverage repair: ask for the exact missing rows once.
        warnings.append({
            "attempt": "coverage",
            "error": f"upgrade response omitted candidates: {missing}",
        })
        repair_user = user + UPGRADE_MISSING_REPAIR.format(
            missing=", ".join(missing)
        )
        try:
            parsed, meta = provider_call(
                route=route, system=system, user=repair_user,
                purpose="causal_upgrade_coverage_repair",
            )
            upgrades, missing, error = _parse_upgrades(parsed, candidates, packet)
        except ProviderError as exc:
            return {
                "ok": False,
                "upgrades": upgrades,
                "upgraded_candidate": None,
                "error": exc.message,
                "error_type": exc.error_type,
                "coverage": {"expected": len(candidates), "missing": missing},
            }, {
                "stage": "causal_upgrade", "state": "provider_error",
                "attempts": exc.attempts,
                "view": "complete" if not view_meta.get("degraded") else "degraded",
                "warnings": warnings,
            }
        if upgrades is None:
            return {
                "ok": False, "upgrades": [], "error": error,
                "error_type": "contract_error",
            }, {
                "stage": "causal_upgrade", "state": "contract_error",
                "attempts": int(meta.get("attempts") or 0),
                "view": "complete" if not view_meta.get("degraded") else "degraded",
                "warnings": warnings,
            }

    coverage = {
        "expected": len(candidates),
        "answered": sum(1 for row in upgrades if row.get("coverage") == "answered"),
        "missing": list(missing),
    }
    if missing:
        # Never interpret an omitted row as a negative. The recall upgrade is
        # unavailable and the final authority degrades (still decisive clean).
        return {
            "ok": False,
            "upgrades": upgrades,
            "upgraded_candidate": None,
            "error": (
                f"upgrade response still omitted {len(missing)} candidate row(s) "
                f"after coverage repair: {missing}"
            ),
            "error_type": "degraded_missing_rows",
            "coverage": coverage,
            "provider": meta,
            "view_meta": view_meta,
        }, {
            "stage": "causal_upgrade", "state": "degraded_missing_rows",
            "attempts": int(meta.get("attempts") or 0),
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": warnings,
        }

    upgraded = next(
        (
            row for row in upgrades
            if row["content_error"] and row["causal"]
            and row.get("coverage") == "answered"
        ),
        None,
    )
    if upgraded is not None:
        candidate = next(
            c for c in candidates
            if c["candidate_id"] == upgraded["candidate_id"]
        )
        upgraded = {
            **upgraded,
            "locus_event_id": candidate["locus_event_id"],
            "evidence_refs": candidate["evidence_refs"],
            "claim": candidate["claim"],
            "quote": candidate["quote"],
        }
    return {
        "ok": True, "upgrades": upgrades, "upgraded_candidate": upgraded,
        "provider": meta, "view_meta": view_meta, "coverage": coverage,
    }, {
        "stage": "causal_upgrade", "state": "ok",
        "attempts": int(meta.get("attempts") or 0),
        "view": "complete" if not view_meta.get("degraded") else "degraded",
        "warnings": warnings,
    }


__all__ = ["run_causal_upgrade", "run_recall_scan"]
