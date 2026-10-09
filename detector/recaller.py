"""Recall safety net for judge=false cases.

Two separate calls with different authority:

- ``run_recall_scan`` finds candidate deviations (high recall, no verdict);
- ``run_causal_upgrade`` turns a candidate into a positive ONLY when the
  deviation is both factually real and causally connected to the delivered
  result.

A scanner candidate can never convict by itself.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping, Sequence

from detector.evidence import (
    citation_error,
    render_evidence_block,
    snapshot_digest,
)
from detector.interventions import InterventionPlan, empty_plan
from detector.packet import (
    evidence_refs_support_event,
    evidence_refs_support_step,
    packet_features,
    packet_reference_error,
    render_judge_view,
)
from detector.provider import raise_if_terminal
from detector.provider import (
    ProviderError,
    majority_vote,
    provider_call,
    required_quorum,
)
from detector.semantics import (
    RECALL_CONTRACT_REPAIR,
    RECALL_SYSTEM,
    UPGRADE_CONTRACT_REPAIR,
    UPGRADE_MISSING_REPAIR,
    UPGRADE_SYSTEM,
)


def _provider_call(
    *, route: Mapping[str, Any], system: str, user: str, purpose: str,
    cache_tag: str = "",
) -> tuple[Any, dict[str, Any]]:
    """``provider_call`` forwarding the snapshot cache tag when one exists.

    Keeping the no-snapshot call shape unchanged preserves every existing
    replay/caching contract; the tag exists solely to key prompt caches on
    the evidence digest that rides outside the prompt text.
    """
    if cache_tag:
        return provider_call(
            route=route, system=system, user=user, purpose=purpose,
            cache_tag=cache_tag,
        )
    return provider_call(route=route, system=system, user=user, purpose=purpose)


def _parse_candidates(
    parsed: Any, packet: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None = None,
):
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("candidates"), list
    ):
        return None, "recall output is not a candidates object"
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
        reference_error = packet_reference_error(packet, refs_raw)
        if reference_error:
            return None, f"candidate {candidate_id}: {reference_error}"
        refs = list(refs_raw)
        if not claim or not locus:
            return None, f"candidate {candidate_id} needs claim and locus"
        reference_error = packet_reference_error(packet, [locus], required=True)
        if reference_error:
            return None, f"candidate {candidate_id}: {reference_error}"
        if not evidence_refs_support_event(packet, refs, locus):
            return None, (
                f"candidate {candidate_id} evidence_refs do not support its locus"
            )
        cited_raw = raw.get("evidence_citations")
        cited_raw = [] if cited_raw is None else cited_raw
        if not isinstance(cited_raw, list) or any(
            not isinstance(item, str) for item in cited_raw
        ):
            return None, (
                f"candidate {candidate_id} evidence_citations must be a list"
            )
        cited = [str(x) for x in cited_raw if str(x)]
        error = citation_error(
            snapshot, cited, where=f"candidate {candidate_id}",
        )
        if error:
            return None, error
        candidates.append({
            "candidate_id": candidate_id,
            "claim": claim,
            "locus_event_id": locus,
            "evidence_refs": refs,
            "evidence_citations": cited,
            "quote": str(raw.get("quote") or "").strip(),
        })
    return candidates, ""


def run_recall_scan(
    *,
    packet: Mapping[str, Any],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
    snapshot: Mapping[str, Any] | None = None,
    causal_context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = plan or empty_plan()
    route = dict(config["recall_route"])
    route["_route_name"] = "recall_scanner"
    view_text, view_meta = render_judge_view(packet)
    system = RECALL_SYSTEM + plan.prompt_block(packet_features(packet), hook="scanner.system")
    evidence_block = (
        "\n\n" + render_evidence_block(snapshot) if snapshot is not None else ""
    )
    cache_tag = (
        snapshot_digest(snapshot)[:16] if snapshot is not None else ""
    )
    user = (
        "Scan the trajectory below for candidate deviations. Do not judge "
        "whether the trajectory is hallucinated.\n\n" + view_text
        + evidence_block
    )
    if causal_context is not None:
        from detector.causal_contracts import validate_seal
        from detector.identity import digest
        validate_seal(causal_context, schema="detector.causal_recall_context.v1")
        if causal_context["packet_digest"] != digest(packet):
            raise ValueError("causal recall context packet identity differs")
        user += "\n\nCAUSAL ANOMALIES: " + json.dumps(causal_context, ensure_ascii=False)
        cache_tag = digest({"snapshot": cache_tag, "causal_context": causal_context["digest"]})
    try:
        parsed, meta = _provider_call(
            route=route, system=system, user=user, purpose="recall_scanner",
            cache_tag=cache_tag,
        )
    except ProviderError as exc:
        raise_if_terminal(exc)
        return {
            "ok": False, "candidates": [], "error": exc.message,
            "error_type": exc.error_type,
        }, {
            "stage": "recall_scanner", "state": "provider_error",
            "attempts": exc.attempts,
            "view": "complete" if not view_meta.get("degraded") else "degraded",
            "warnings": [],
        }
    candidates, error = _parse_candidates(parsed, packet, snapshot)
    warnings: list[dict[str, str]] = []
    if candidates is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = _provider_call(
                route=route, system=system,
                user=user + RECALL_CONTRACT_REPAIR,
                purpose="recall_scanner_repair",
                cache_tag=cache_tag,
            )
            candidates, error = _parse_candidates(parsed, packet, snapshot)
        except ProviderError as exc:
            raise_if_terminal(exc)
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
    snapshot: Mapping[str, Any] | None = None,
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
        cited_raw = raw.get("evidence_refs")
        cited_raw = [] if cited_raw is None else cited_raw
        if not isinstance(cited_raw, list) or any(
            not isinstance(item, str) for item in cited_raw
        ):
            return None, [], (
                f"evidence_refs must be a list of snapshot ids "
                f"(candidate {candidate_id})"
            )
        cited = [str(item) for item in cited_raw if str(item)]
        conflict_note = str(raw.get("conflict_note") or "").strip()
        error = citation_error(
            snapshot, cited,
            where=f"candidate {candidate_id}",
            require_settled=bool(content_error and causal),
            conflict_note=conflict_note,
        )
        if error:
            return None, [], error
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
            if packet is not None:
                candidate = next(
                    item for item in candidates
                    if str(item.get("candidate_id") or "") == candidate_id
                )
                if not evidence_refs_support_step(
                    packet, candidate.get("evidence_refs") or [], corrected_step,
                ):
                    return None, [], (
                        "candidate evidence_refs do not support corrected_step "
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
            "evidence_refs": cited,
            "conflict_note": conflict_note,
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
                "evidence_refs": [],
                "conflict_note": "",
            })
        else:
            rows.append({**row, "coverage": "answered"})
    return rows, missing, ""


def assemble_upgraded_candidates(
    candidates: Sequence[Mapping[str, Any]],
    upgrades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Derive hearing inputs from validated candidates and answered upgrade rows."""
    by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    positives = sorted(
        (row for row in upgrades
         if row.get("content_error") is True and row.get("causal") is True
         and row.get("coverage") == "answered"),
        key=lambda row: row["corrected_step"],
    )
    assembled = []
    for row in positives:
        candidate = by_id[row["candidate_id"]]
        assembled.append(deepcopy({
            **row,
            "locus_event_id": candidate["locus_event_id"],
            # Trajectory references and snapshot citations use separate ID spaces.
            "evidence_refs": candidate["evidence_refs"],
            "evidence_citations": row.get("evidence_refs") or [],
            "conflict_note": str(row.get("conflict_note") or ""),
            "claim": candidate["claim"],
            "quote": candidate["quote"],
        }))
    return assembled


def _upgrade_sample(
    *,
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    route: Mapping[str, Any],
    system: str,
    user: str,
    view_state: str,
    snapshot: Mapping[str, Any] | None = None,
) -> tuple[dict | None, dict[str, Any], list[dict[str, str]], dict[str, Any] | None, list[str]]:
    """One upgrade ballot.

    Returns ``(rows_or_None, provider_meta, warnings, failure, missing)``
    where a ``None`` row set abstains, ``failure`` (a status-shaped dict)
    marks the sample unusable, and ``missing`` lists candidates the
    coverage repair still did not answer. Coverage repair for
    silently-missing rows is part of the sample.
    """
    warnings: list[dict[str, str]] = []
    cache_tag = (
        snapshot_digest(snapshot)[:16] if snapshot is not None else ""
    )
    try:
        parsed, meta = _provider_call(
            route=route, system=system, user=user, purpose="causal_upgrade",
            cache_tag=cache_tag,
        )
    except ProviderError as exc:
        raise_if_terminal(exc)
        return None, {}, warnings, {
            "state": "provider_error", "error": exc.message,
            "error_type": exc.error_type, "attempts": exc.attempts,
        }, []
    upgrades, missing, error = _parse_upgrades(
        parsed, candidates, packet, snapshot,
    )
    if upgrades is None:
        warnings.append({"attempt": "initial", "error": f"contract:{error}"})
        try:
            parsed, meta = _provider_call(
                route=route, system=system,
                user=user + UPGRADE_CONTRACT_REPAIR,
                purpose="causal_upgrade_repair",
                cache_tag=cache_tag,
            )
            upgrades, missing, error = _parse_upgrades(
                parsed, candidates, packet, snapshot,
            )
        except ProviderError as exc:
            raise_if_terminal(exc)
            return None, {}, warnings, {
                "state": "provider_error", "error": exc.message,
                "error_type": exc.error_type, "attempts": exc.attempts,
            }, []
    if upgrades is None:
        return None, dict(meta or {}), warnings, {
            "state": "contract_error", "error": error,
            "error_type": "contract_error", "attempts": 0,
        }, []
    if missing:
        warnings.append({
            "attempt": "coverage",
            "error": f"upgrade response omitted candidates: {missing}",
        })
        repair_user = user + UPGRADE_MISSING_REPAIR.format(
            missing=", ".join(missing)
        )
        try:
            parsed, meta = _provider_call(
                route=route, system=system, user=repair_user,
                purpose="causal_upgrade_coverage_repair",
                cache_tag=cache_tag,
            )
            upgrades, missing, error = _parse_upgrades(
                parsed, candidates, packet, snapshot,
            )
        except ProviderError as exc:
            raise_if_terminal(exc)
            return None, dict(meta or {}), warnings, {
                "state": "provider_error", "error": exc.message,
                "error_type": exc.error_type, "attempts": exc.attempts,
            }, []
        if upgrades is None:
            return None, dict(meta or {}), warnings, {
                "state": "contract_error", "error": error,
                "error_type": "contract_error", "attempts": 0,
            }, []
    return upgrades, dict(meta or {}), warnings, None, list(missing)


def run_causal_upgrade(
    *,
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    plan: InterventionPlan | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decide every candidate; a candidate upgrades only on strict majority.

    Independent samples vote per candidate: a candidate needs more than
    half of the cast ballots with both flags true. Ties and no-quorum
    keep the negative — a conviction is the extraordinary claim. Every
    upgrading candidate is returned (earliest first), so the defense can
    hear each of them.
    """
    plan = plan or empty_plan()
    if not candidates:
        return {
            "ok": True, "upgrades": [], "upgraded_candidate": None,
            "upgraded_candidates": [],
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
    view_state = "complete" if not view_meta.get("degraded") else "degraded"
    snapshot_block = (
        "\n\n" + render_evidence_block(snapshot) if snapshot is not None else ""
    )
    user = (
        "Decide whether any candidate below can upgrade this trajectory to "
        "hallucinated. Apply both tests to every candidate.\n\nCANDIDATES:\n"
        + "\n".join(
            f"- {c['candidate_id']}: {c['claim']} "
            f"(locus {c['locus_event_id']}, refs {c['evidence_refs']}, "
            f"evidence {c.get('evidence_citations') or []}, "
            f"quote {c['quote'] or '?'})"
            for c in candidates
        )
        + evidence_block
        + "\n\nTRAJECTORY:\n" + view_text
        + snapshot_block
    )

    votes = max(1, int(route.get("votes", 1) or 1))
    samples: list[list[dict[str, Any]]] = []
    sample_meta: dict[str, Any] = {}
    total_attempts = 0
    warnings: list[dict[str, str]] = []
    failure: dict[str, Any] | None = None
    for _ in range(votes):
        rows, meta, sample_warnings, sample_failure, sample_missing = _upgrade_sample(
            packet=packet, candidates=candidates, route=route,
            system=system, user=user, view_state=view_state,
            snapshot=snapshot,
        )
        total_attempts += int(meta.get("attempts") or 0)
        warnings.extend(sample_warnings)
        if sample_failure is not None:
            failure = sample_failure
            continue
        if rows is not None:
            samples.append(rows)
            sample_meta = dict(meta)
    if not samples:
        error = failure or {}
        return {
            "ok": False, "upgrades": [], "error": error.get("error"),
            "error_type": error.get("error_type") or "provider_error",
            "provider": sample_meta,
        }, {
            "stage": "causal_upgrade",
            "state": error.get("error_type") or "provider_error",
            "attempts": total_attempts, "view": view_state,
            "warnings": warnings,
        }
    quorum = required_quorum(votes)
    if len(samples) < quorum:
        # One surviving sample must not upgrade a candidate: per-candidate
        # majority over a shrunken quorum is not a majority. Report the stage
        # as undecided so the case degrades instead of convicting on jitter.
        return {
            "ok": False, "upgrades": [], "upgraded_candidate": None,
            "upgraded_candidates": [],
            "error": (
                f"causal upgrade quorum not reached: {len(samples)} usable "
                f"sample(s) of {votes} (need {quorum})"
            ),
            "error_type": "no_quorum",
            "provider": sample_meta,
        }, {
            "stage": "causal_upgrade", "state": "no_quorum",
            "attempts": total_attempts, "view": view_state,
            "warnings": warnings,
        }
    still_missing: set[str] = set()
    for rows in samples:
        still_missing.update(
            str(row.get("candidate_id") or "")
            for row in rows
            if row.get("coverage") == "missing"
        )
    if still_missing:
        missing = sorted(still_missing)
        coverage = {
            "expected": len(candidates),
            "answered": len(candidates) - len(missing),
            "missing": missing,
        }
        return {
            "ok": False,
            "upgrades": samples[0],
            "upgraded_candidate": None,
            "upgraded_candidates": [],
            "error": (
                f"upgrade response still omitted {len(missing)} candidate "
                f"row(s) after coverage repair: {missing}"
            ),
            "error_type": "degraded_missing_rows",
            "coverage": coverage,
            "provider": sample_meta,
            "view_meta": view_meta,
        }, {
            "stage": "causal_upgrade", "state": "degraded_missing_rows",
            "attempts": total_attempts, "view": view_state,
            "warnings": warnings,
        }

    cast = len(samples)
    by_id: dict[str, list[dict[str, Any]]] = {
        str(candidate.get("candidate_id") or ""): []
        for candidate in candidates
    }
    for rows in samples:
        for row in rows:
            by_id.setdefault(str(row.get("candidate_id") or ""), []).append(row)
    merged: list[dict[str, Any]] = []
    vote_facts: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        rows = by_id.get(candidate_id) or []
        positive_votes = sum(
            1 for row in rows
            if row.get("content_error") is True and row.get("causal") is True
            and row.get("coverage") == "answered"
        )
        winner, _ = majority_vote(
            [
                bool(
                    row.get("content_error") is True
                    and row.get("causal") is True
                    and row.get("coverage") == "answered"
                )
                for row in rows
            ],
            on_tie=False,
        )
        anchor = next(
            (row for row in rows
             if (row.get("content_error") is True and row.get("causal") is True
                 and row.get("coverage") == "answered") == winner),
            next(
                (row for row in rows if row.get("coverage") == "answered"),
                {"candidate_id": candidate_id, "content_error": False,
                 "causal": False, "corrected_step": None,
                 "reason": "no sample answered this candidate"},
            ),
        )
        vote_facts[candidate_id] = {
            "cast": cast, "positives": positive_votes,
            "winner": winner, "tie": positive_votes * 2 == cast,
        }
        merged.append({**anchor, "coverage": "answered"})
    coverage = {
        "expected": len(candidates),
        "answered": sum(
            1 for row in merged if row.get("coverage") == "answered"
        ),
        "missing": [],
    }
    sample_meta["vote"] = {
        "votes": votes, "cast": cast, "per_candidate": vote_facts,
    }

    upgraded_candidates = assemble_upgraded_candidates(candidates, merged)
    return {
        "ok": True, "upgrades": merged,
        "upgraded_candidate": upgraded_candidates[0] if upgraded_candidates else None,
        "upgraded_candidates": upgraded_candidates,
        "provider": sample_meta, "view_meta": view_meta, "coverage": coverage,
        "snapshot_digest": (
            snapshot_digest(snapshot) if snapshot is not None else None
        ),
    }, {
        "stage": "causal_upgrade", "state": "ok",
        "attempts": total_attempts, "view": view_state,
        "warnings": warnings,
    }


__all__ = ["assemble_upgraded_candidates", "run_causal_upgrade", "run_recall_scan"]
