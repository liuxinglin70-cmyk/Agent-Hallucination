"""Failure worksheet primitives: run trees, stability, attribution.

Measured foundation (2026-08-16 disk re-diagnosis): single-run failure
censuses carry heavy sampling noise — cross-run verdict churn 24%,
FP/FN set Jaccard 0.39/0.48. The worksheet keeps EVERY failure and
marks each stable or unstable with one unguided re-roll: stable
failures teach directly, unstable ones (the detector flip-flops on the
case) are first-class stabilisation targets. All replays downstream
run through ``detect_with_retry`` against the detector's frozen-census
boundary, so label flips stay attributable to artifacts rather than to
census resampling.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.api import Detector
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.packet import EVALUATOR_ONLY_FIELDS

WRONGFUL_CONVICTION_ATTRIBUTION = "wrongful_conviction"
TEXT_MAX_CHARS = 400
GOLD_REASON_MAX_CHARS = 600
CENSUS_CLAIM_LIMIT = 8
CENSUS_CLAIM_CHARS = 200
UPHELD_CERTIFICATE_LIMIT = 4
DEFENSE_RATIONALE_CHARS = 300


def attribute_miss(result: Mapping[str, Any]) -> str:
    """Mechanical FN triage from the court's recorded facts.

    Priority: an unseen trajectory dominates everything (infrastructure);
    then discovery, then abstention, then adjudication flips.
    """
    proof = result.get("proof_adjudication") or {}
    facts = proof.get("miss_triage_facts") or {}
    if facts.get("census_view_degraded"):
        return "view_degraded"
    if int(facts.get("census_candidates") or 0) == 0:
        return "census_miss"
    if int(facts.get("abstained_count") or 0) > 0:
        return "evidence_insufficient"
    if int(facts.get("overturned_count") or 0) > 0:
        return "defense_overturn"
    return "verification_reject"


def load_run_tree(run_dir: Path) -> dict[str, dict[str, Any]]:
    """case_id -> {result, gold} from the run ledger join."""
    run_dir = Path(run_dir)
    gold_rows = {
        str(entry["case"]): entry
        for entry in json.loads(
            (run_dir / "eval_gold.json").read_text(encoding="utf-8"),
        )["cases"]
    }
    tree: dict[str, dict[str, Any]] = {}
    for result_path in sorted((run_dir / "cases").glob("*/result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        case_id = str(result.get("case_id") or "")
        if case_id and case_id in gold_rows:
            tree[case_id] = {
                "result": result,
                "gold": bool(gold_rows[case_id]["gold"]),
            }
    return tree


def triage_misses(
    run_tree: Mapping[str, Mapping[str, Any]],
) -> list[dict]:
    """One first-class row per miss, carrying its attribution."""
    rows = []
    for case_id in sorted(run_tree):
        entry = run_tree[case_id]
        if not entry["gold"]:
            continue
        if str(entry["result"].get("state")) != "verified_clean":
            continue
        proof = entry["result"].get("proof_adjudication") or {}
        rows.append({
            "case_id": case_id,
            "attribution": attribute_miss(entry["result"]),
            "census_claims": [
                str(candidate.get("claim") or "")
                for candidate in proof.get("census") or []
            ],
            "stage_audit": entry["result"].get("stage_audit") or {},
        })
    return rows


def upheld_certificates(
    proof: Mapping[str, Any],
) -> list[dict[str, str]]:
    """The certificates that convicted one case, with defence rationales."""
    claims = {
        str(candidate.get("candidate_id")): str(
            candidate.get("claim") or ""
        )
        for candidate in proof.get("census") or []
    }
    decisions = proof.get("defense_decisions") or {}
    return [
        {
            "candidate_id": candidate_id,
            "claim": claims.get(candidate_id, ""),
            "defense_rationale": str(
                (decisions.get(candidate_id) or {}).get("rationale") or ""
            )[:TEXT_MAX_CHARS],
        }
        for candidate_id in sorted(decisions)
        if (decisions.get(candidate_id) or {}).get("verdict") == "upheld"
    ]


def wrongful_convictions(
    run_tree: Mapping[str, Mapping[str, Any]],
) -> list[dict]:
    """Gold-clean cases the detector convicted, with their proof chain."""
    rows = []
    for case_id in sorted(run_tree):
        entry = run_tree[case_id]
        if entry["gold"] or str(
            entry["result"].get("state"),
        ) != "verified_error":
            continue
        rows.append({
            "case_id": case_id,
            "upheld_certificates": upheld_certificates(
                entry["result"].get("proof_adjudication") or {},
            ),
        })
    return rows


def confirm_failures(
    *, run_tree: Mapping[str, Mapping[str, Any]],
    dataset_root: Path, workers: int = 20, rolls: int = 1,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """One unguided re-roll per recorded failure; classify stability.

    A failure whose re-roll repeats it is stable teaching material. A
    disagreement marks the case UNSTABLE: the detector sits on the
    decision boundary and flip-flops across runs — that is a
    first-class learning target, not noise to discard. Both kinds flow
    into the worksheet; the router and the flip gate treat them
    differently.
    """
    misses = triage_misses(run_tree)
    convictions = wrongful_convictions(run_tree)
    detector = Detector(load_config())
    lock = threading.Lock()
    rerolled: dict[str, list[str]] = {}

    def roll(row: Mapping[str, Any]) -> None:
        case_id = str(row["case_id"])
        record = case_record(case_id, dataset_root)
        states = [
            str(detect_with_retry(
                detector, case_id, record, None,
            ).get("state"))
            for _ in range(max(1, int(rolls)))
        ]
        with lock:
            rerolled[case_id] = states

    jobs: list[Mapping[str, Any]] = [*misses, *convictions]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for _ in executor.map(roll, jobs):
            pass

    def annotate(
        rows: list[dict], failure_state: str,
    ) -> tuple[list[dict], int]:
        kept = []
        unstable = 0
        for row in rows:
            states = rerolled.get(str(row["case_id"])) or []
            row["reroll_states"] = list(states)
            row["unstable"] = not (
                states and sum(
                    1 for state in states if state == failure_state
                ) > len(states) / 2
            )
            if row["unstable"]:
                unstable += 1
            kept.append(row)
        return kept, unstable

    kept_misses, unstable_misses = annotate(misses, "verified_clean")
    kept_convictions, unstable_convictions = annotate(
        convictions, "verified_error",
    )
    stats = {
        "misses_total": len(misses),
        "misses_stable": len(misses) - unstable_misses,
        "misses_unstable": unstable_misses,
        "convictions_total": len(convictions),
        "convictions_stable": len(convictions) - unstable_convictions,
        "convictions_unstable": unstable_convictions,
        "unstable_case_ids": sorted(
            str(row["case_id"]) for row in [
                *kept_misses, *kept_convictions,
            ] if row["unstable"]
        ),
        "rolls": int(rolls),
        "rerolled_states": {
            str(case_id): list(states)
            for case_id, states in sorted(rerolled.items())
        },
    }
    return kept_misses, kept_convictions, stats


def case_record(case_id: str, dataset_root: Path) -> dict[str, Any]:
    raw = json.loads(
        (Path(dataset_root) / case_id).read_text(encoding="utf-8"),
    )
    return {
        key: value for key, value in raw.items()
        if key not in EVALUATOR_ONLY_FIELDS
    }


def gold_reason(case_id: str, dataset_root: Path) -> str:
    """The evaluator-side error description for one gold-error case."""
    raw = json.loads(
        (Path(dataset_root) / case_id).read_text(encoding="utf-8"),
    )
    reason = str(raw.get("hallucination_reason") or "")
    step = raw.get("hallucination_step")
    if str(step or "").isdigit():
        reason = f"(step {step}) " + reason
    return reason[:GOLD_REASON_MAX_CHARS]


def evidence_pack(
    entry: Mapping[str, Any], dataset_root: Path,
    reroll_states: Sequence[str] = (),
) -> dict[str, Any]:
    """The mechanical case file the capability proposer drafts against.

    Misses carry the gold error description and what the detector's own
    scan saw; wrongful convictions carry the certificates that convicted
    the clean case. With ``reroll_states`` that disagree with the
    recorded state, the pack also carries the swing facts — the
    proposer must know it is drafting for a case that flip-flops, and
    that stabilising it to the correct side is the acceptance
    standard. Label-free fields only — the pack informs drafting,
    never gate decisions.
    """
    result = entry["result"]
    proof = result.get("proof_adjudication") or {}
    pack: dict[str, Any] = {
        "packet_features": list(
            (result.get("stage_audit") or {}).get("packet_features") or [],
        ),
        "census_claims": [
            str(candidate.get("claim") or "")[:CENSUS_CLAIM_CHARS]
            for candidate in proof.get("census") or []
        ][:CENSUS_CLAIM_LIMIT],
    }
    if entry["gold"]:
        pack["gold_error"] = gold_reason(
            str(result.get("case_id")), dataset_root,
        )
    else:
        pack["upheld_certificates"] = [
            {
                "claim": str(item.get("claim") or "")[:CENSUS_CLAIM_CHARS],
                "defense_rationale": str(
                    item.get("defense_rationale") or ""
                )[:DEFENSE_RATIONALE_CHARS],
            }
            for item in upheld_certificates(proof)
        ][:UPHELD_CERTIFICATE_LIMIT]
    states = [str(state) for state in reroll_states]
    if states and str(result.get("state")) not in states:
        pack["swing"] = {
            "recorded_state": str(result.get("state")),
            "reroll_states": states,
            "note": (
                "the verdict flip-flops across runs on this case; a "
                "good component must stabilise it to the correct "
                "verdict, judged by a three-roll majority"
            ),
        }
    return pack


def detect_with_retry(
    detector: Detector, case_id: str, record: Mapping[str, Any],
    artifacts: Mapping[str, Any] | None,
    frozen_census: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """One detection arm with transport-failure retry (verdict-neutral)."""
    result = None
    for _attempt in range(3):
        result = detector.detect(
            record, case_id=case_id, artifacts=artifacts,
            frozen_census=frozen_census,
        )
        if result.get("state") != "unresolved":
            return result
        transport = any(
            str(reason.get("error_type")) in {
                "provider_error", "transport_error", "invalid_json",
            }
            for reason in result.get("authority_failure_reasons") or []
        )
        if not transport:
            return result
    return result


__all__ = [
    "WRONGFUL_CONVICTION_ATTRIBUTION",
    "attribute_miss",
    "case_record",
    "confirm_failures",
    "detect_with_retry",
    "evidence_pack",
    "gold_reason",
    "load_run_tree",
    "triage_misses",
    "upheld_certificates",
    "wrongful_convictions",
]
