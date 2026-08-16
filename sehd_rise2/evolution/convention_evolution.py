"""Convention evolution: misses and wrongful convictions to dual-boundary
judgement conventions.

The evidence-backed failure model of the census-probe channel (deleted):
one-sided check obligations had a tiny repair pool (census misses) and a
large overflow pool (every clean case matching a wide trigger), with no
exclusion boundary to stop the overflow — measurably net-negative.

The evolved unit here is the SAME shape as the hand conventions that
measurably worked (detector/conventions.py): a judgement directive with
an explicit NOT-WHEN exclusion, routed by mechanical packet features.
It covers both failure directions at the adjudication layer where 69%
of the misses actually live (defense_overturn + verification_reject),
plus the discovery-layer remainder:

    evolution-set detections + gold
        -> failure worksheet (misses with attribution + wrongful
           convictions with their upheld certificates)
        -> one provider call extracts dual-boundary convention drafts,
           each supported by >= 2 same-mechanism cases
        -> G1 mechanical gate: closed vocabularies, >= 2 supporting
           cases per direction, no case ids, mandatory exclusion,
           trigger features inside the packet-feature vocabulary
        -> convention genome: [{convention_id, trigger_features,
           directive, exclusion, case_evidence}]
        -> G2 targeted replay, ONE convention at a time (attribution):
           guided vs unguided over the convention's target cases, the
           convicted sentinels and the trigger-matching clean pool;
           deploys only on strict paired improvement with zero
           sentinel loss and zero new wrongful conviction.

CLI:
    python -m sehd_rise2.evolution.convention_evolution extract \
        --run runs/<id> --out conventions_raw.json
    python -m sehd_rise2.evolution.convention_evolution gate \
        --raw conventions_raw.json --run runs/<id> --out conventions_gated.json
    python -m sehd_rise2.evolution.convention_evolution genome \
        --conventions conventions_gated.json --out convention_genome.json
    python -m sehd_rise2.evolution.convention_evolution validate \
        --run runs/<id> --conventions conventions_gated.json --out-dir <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.api import Detector
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.conventions import PACKET_FEATURES
from sehd_rise2.detector.packet import EVALUATOR_ONLY_FIELDS
from sehd_rise2.detector.provider import ProviderError, provider_call

CONVENTION_GENOME_SCHEMA = "sehd2.convention_genome.v1"
CONVENTION_RAW_SCHEMA = "sehd2.convention_proposals.v1"
VALIDATION_SCHEMA = "sehd2.convention_validation.v1"
CASE_ID_IN_TEXT = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*/\d+\.json\b")
MIN_SUPPORT_CASES = 2
TEXT_MAX_CHARS = 400

TRIGGER_VOCABULARY = frozenset(PACKET_FEATURES)

EXTRACTOR_SYSTEM = (
    "You are the convention extractor of a hallucination detector's "
    "evolution layer. You receive this cycle's failure worksheet: MISSES "
    "(real errors the detector acquitted, each with the gold error "
    "description, the detector's own scan and its mechanical FN-triage "
    "attribution) and WRONGFUL CONVICTIONS (clean cases the detector "
    "convicted, with the upheld certificates that convicted them).\n"
    "For each failure FAMILY sharing one judgement mechanism, draft a "
    "dual-boundary convention the detector will apply at ALL stages:\n"
    "- directive: one or two sentences fixing WHEN the behaviour IS a "
    "deviation (or, for wrongful convictions, WHEN it is NOT) — class "
    "level, never quoting case-specific content;\n"
    "- exclusion: the NOT-WHEN boundary — the confusable legitimate "
    "behaviour the directive must never condemn (or, for wrongful "
    "convictions, the true deviation it must not start excusing);\n"
    "- trigger_features: the tightest subset of "
    + str(sorted(TRIGGER_VOCABULARY))
    + " that must ALL hold for the convention to route.\n\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"conventions": [{"title": "<short>", "trigger_features": '
    '["<feature>", "..."], "directive": "<WHEN it is a deviation>", '
    '"exclusion": "<NOT WHEN boundary>", "failure_direction": '
    '"miss|wrongful_conviction", "case_evidence": [{"case_id": '
    '"<worksheet case>", "observation": "<the shared mechanism>"}]}]}\n'
    "Every convention needs >= 2 supporting cases of the SAME direction "
    "and mechanism. A failure with no class-level reading is dropped. No "
    "commentary."
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )


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
    """case_id -> {result, gold, gold_step} from the run ledger join."""
    run_dir = Path(run_dir)
    gold_rows = {
        str(entry["case"]): entry
        for entry in _read_json(run_dir / "eval_gold.json")["cases"]
    }
    tree: dict[str, dict[str, Any]] = {}
    for result_path in sorted((run_dir / "cases").glob("*/result.json")):
        result = _read_json(result_path)
        case_id = str(result.get("case_id") or "")
        if case_id and case_id in gold_rows:
            row = gold_rows[case_id]
            tree[case_id] = {
                "result": result,
                "gold": bool(row["gold"]),
                "gold_step": row.get("gold_step"),
            }
    return tree


def triage_misses(run_tree: Mapping[str, Mapping[str, Any]]) -> list[dict]:
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
        proof = entry["result"].get("proof_adjudication") or {}
        claims = {
            str(candidate.get("candidate_id")): str(
                candidate.get("claim") or ""
            )
            for candidate in proof.get("census") or []
        }
        rows.append({
            "case_id": case_id,
            "upheld_certificates": [
                {
                    "candidate_id": cid,
                    "claim": claims.get(cid, ""),
                    "defense_rationale": str(
                        (proof.get("defense_decisions") or {}).get(
                            cid, {},
                        ).get("rationale") or "",
                    )[:TEXT_MAX_CHARS],
                }
                for cid in sorted(
                    proof.get("defense_decisions") or {}
                )
                if (proof.get("defense_decisions") or {})[cid].get(
                    "verdict"
                ) == "upheld"
            ],
        })
    return rows


def _load_gold_reason(case_id: str, dataset_root: Path) -> str:
    raw = json.loads(
        (Path(dataset_root) / case_id).read_text(encoding="utf-8"),
    )
    reason = str(raw.get("hallucination_reason") or "")
    step = raw.get("hallucination_step")
    if str(step or "").isdigit():
        reason = f"(step {step}) " + reason
    return reason[:1500]


def _worksheet_user(
    misses: Sequence[Mapping[str, Any]],
    convictions: Sequence[Mapping[str, Any]],
    dataset_root: Path,
) -> str:
    miss_rows = [
        {
            "case_id": miss["case_id"],
            "attribution": miss["attribution"],
            "gold_error": _load_gold_reason(miss["case_id"], dataset_root),
            "detector_scan": miss.get("census_claims") or [],
            "packet_features": (
                miss.get("stage_audit") or {}
            ).get("packet_features") or [],
        }
        for miss in misses
    ]
    return json.dumps(
        {"misses": miss_rows, "wrongful_convictions": list(convictions)},
        ensure_ascii=False, indent=1,
    )


def extract_conventions(
    *, run_tree: Mapping[str, Mapping[str, Any]],
    dataset_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One provider call over the failure worksheet; raw conventions."""
    misses = triage_misses(run_tree)
    convictions = wrongful_convictions(run_tree)
    config = load_config()
    route = dict(config["verification_route"])
    import os
    route["model"] = os.environ.get(
        "SEHD_MUTATION_MODEL", "deepseek-v4-pro",
    )
    route["_route_name"] = "convention_extract"
    parsed, meta = provider_call(
        route=route, system=EXTRACTOR_SYSTEM,
        user=_worksheet_user(misses, convictions, dataset_root),
        purpose="convention_extract",
    )
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("conventions"), list,
    ):
        raise ProviderError(
            "convention_extract",
            "answer is not a conventions object",
            error_type="contract",
        )
    conventions = [
        dict(item) for item in parsed["conventions"]
        if isinstance(item, Mapping)
    ]
    return conventions, {
        "model": meta.get("model"),
        "misses_total": len(misses),
        "misses_by_attribution": {
            key: sum(
                1 for miss in misses if miss["attribution"] == key
            )
            for key in {miss["attribution"] for miss in misses}
        },
        "wrongful_convictions_total": len(convictions),
        "raw_conventions": len(conventions),
    }


def gate_conventions(
    conventions: Sequence[Mapping[str, Any]],
    *,
    run_tree: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """G1 mechanical gate; returns (accepted, rejections).

    The exclusion is mandatory: a directive without a NOT-WHEN boundary
    is a one-sided amplifier, and the census-probe channel's measured
    failure mode.
    """
    worksheet_ids = {
        row["case_id"] for row in triage_misses(run_tree)
    } | {
        row["case_id"] for row in wrongful_convictions(run_tree)
    }
    rejections: list[dict[str, str]] = []
    accepted: list[dict[str, Any]] = []
    for raw in conventions:
        title = str(raw.get("title") or "<untitled>")

        def reject(reason: str) -> None:
            rejections.append({"title": title, "reason": reason})

        directive = str(raw.get("directive") or "")
        exclusion = str(raw.get("exclusion") or "")
        if not directive or len(directive) > TEXT_MAX_CHARS:
            reject("directive missing or over length")
            continue
        if not exclusion or len(exclusion) > TEXT_MAX_CHARS:
            reject("exclusion boundary missing or over length")
            continue
        if CASE_ID_IN_TEXT.search(directive) or CASE_ID_IN_TEXT.search(
            exclusion,
        ):
            reject("directive or exclusion contains a case id")
            continue
        direction = str(raw.get("failure_direction") or "")
        if direction not in ("miss", "wrongful_conviction"):
            reject(f"failure_direction {direction!r} invalid")
            continue
        triggers = {
            str(name) for name in raw.get("trigger_features") or ()
        }
        if not triggers or not triggers <= TRIGGER_VOCABULARY:
            reject("trigger_features empty or outside the vocabulary")
            continue
        evidence = raw.get("case_evidence") or []
        evidence_ids = [
            str(entry.get("case_id")) for entry in evidence
            if isinstance(entry, Mapping) and entry.get("case_id")
        ]
        unknown = [
            case_id for case_id in evidence_ids
            if case_id not in worksheet_ids
        ]
        if unknown:
            reject(f"evidence outside the worksheet: {unknown[0]}")
            continue
        if len(set(evidence_ids)) < MIN_SUPPORT_CASES:
            reject(
                f"mechanism supported by {len(set(evidence_ids))} case(s), "
                f"needs {MIN_SUPPORT_CASES}",
            )
            continue
        accepted.append({
            "convention_id": f"EC-{len(accepted) + 1:02d}",
            "title": title,
            "trigger_features": sorted(triggers),
            "directive": directive,
            "exclusion": exclusion,
            "failure_direction": direction,
            "case_evidence": [
                {"case_id": case_id}
                for case_id in dict.fromkeys(evidence_ids)
            ],
        })
    return accepted, rejections


def build_convention_genome(
    conventions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    genome = {
        "schema_version": CONVENTION_GENOME_SCHEMA,
        "conventions": [dict(entry) for entry in conventions],
    }
    hashable = {
        key: value for key, value in genome.items()
        if key != "content_hash"
    }
    genome["content_hash"] = hashlib.sha256(
        json.dumps(
            hashable, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    ).hexdigest()
    return genome


def load_convention_genome(path: Path) -> dict[str, Any]:
    genome = _read_json(Path(path))
    if not isinstance(genome, dict) or genome.get(
        "schema_version"
    ) != CONVENTION_GENOME_SCHEMA:
        raise ValueError(f"{path}: not a convention genome")
    return genome


def _case_record(case_id: str, dataset_root: Path) -> dict[str, Any]:
    raw = json.loads(
        (Path(dataset_root) / case_id).read_text(encoding="utf-8"),
    )
    return {
        key: value for key, value in raw.items()
        if key not in EVALUATOR_ONLY_FIELDS
    }


def _detect_with_retry(
    detector: Detector, case_id: str, record: Mapping[str, Any],
    genome: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """One detection arm with transport-failure retry (verdict-neutral)."""
    result = None
    for _attempt in range(3):
        result = detector.detect(
            record, case_id=case_id, evolved_conventions=genome,
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


def validate_conventions(
    *,
    run_tree: Mapping[str, Mapping[str, Any]],
    conventions: Sequence[Mapping[str, Any]],
    dataset_root: Path,
    out_dir: Path,
    workers: int = 20,
) -> dict[str, Any]:
    """G2 targeted replay, ONE convention at a time (clean attribution).

    Per convention: targets = its supporting worksheet cases; sentinels =
    every gold-error case the baseline convicted (must stay convicted);
    clean pool = trigger-matching gold-clean cases (must stay clean).
    Deploys only on strict paired improvement with zero sentinel loss
    and zero new wrongful conviction.
    """
    detector = Detector(load_config())
    lock = threading.Lock()
    per_convention: dict[str, dict[str, Any]] = {}

    def run_case(job: tuple[str, str, Mapping[str, Any] | None]) -> None:
        convention_id, case_id, genome = job
        record = _case_record(case_id, dataset_root)
        guided = _detect_with_retry(detector, case_id, record, genome)
        unguided = _detect_with_retry(detector, case_id, record, None)
        row = {
            "gold": run_tree[case_id]["gold"],
            "unguided_state": unguided.get("state"),
            "guided_state": guided.get("state"),
        }
        with lock:
            per_convention[convention_id]["flips"][case_id] = row

    miss_ids = {row["case_id"] for row in triage_misses(run_tree)}
    fp_ids = {
        row["case_id"] for row in wrongful_convictions(run_tree)
    }
    sentinel_ids = [
        case_id for case_id, entry in sorted(run_tree.items())
        if entry["gold"]
        and str(entry["result"].get("state")) == "verified_error"
    ]
    clean_pool = [
        case_id for case_id, entry in sorted(run_tree.items())
        if not entry["gold"]
        and str(entry["result"].get("state")) == "verified_clean"
    ]

    jobs: list[tuple[str, str, Mapping[str, Any] | None]] = []
    for convention in conventions:
        convention_id = str(convention["convention_id"])
        per_convention[convention_id] = {"flips": {}}
        genome = build_convention_genome([convention])
        targets = [
            str(entry["case_id"])
            for entry in convention.get("case_evidence") or []
        ]
        trigger = {
            str(name) for name in convention.get("trigger_features") or ()
        }
        clean_matched = [
            case_id for case_id in clean_pool
            if trigger
            and trigger <= set(
                (run_tree[case_id]["result"].get("stage_audit") or {})
                .get("packet_features") or [],
            )
        ]
        per_convention[convention_id]["scope"] = {
            "targets": targets,
            "sentinels": sentinel_ids,
            "clean_matched": clean_matched,
        }
        for case_id in sorted(
            set(targets) | set(sentinel_ids) | set(clean_matched),
        ):
            jobs.append((convention_id, case_id, genome))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for _ in executor.map(run_case, jobs):
            pass

    reports: dict[str, dict[str, Any]] = {}
    for convention in conventions:
        convention_id = str(convention["convention_id"])
        block = per_convention[convention_id]
        flips = block["flips"]
        targets = block["scope"]["targets"]
        clean_matched = block["scope"]["clean_matched"]
        miss_targets = [c for c in targets if c in miss_ids]
        fp_targets = [c for c in targets if c in fp_ids]
        repaired = [
            c for c in miss_targets if c in flips
            and flips[c]["guided_state"] == "verified_error"
        ]
        acquitted = [
            c for c in fp_targets if c in flips
            and flips[c]["guided_state"] != "verified_error"
        ]
        sentinel_lost = [
            c for c in sentinel_ids if c in flips
            and flips[c]["unguided_state"] == "verified_error"
            and flips[c]["guided_state"] != "verified_error"
        ]
        clean_convicted = [
            c for c in clean_matched if c in flips
            and flips[c]["unguided_state"] == "verified_clean"
            and flips[c]["guided_state"] == "verified_error"
        ]
        net = len(repaired) + len(acquitted) - len(
            sentinel_lost,
        ) - len(clean_convicted)
        reports[convention_id] = {
            "convention": dict(convention),
            "scope": block["scope"],
            "paired_flips": flips,
            "metrics": {
                "miss_targets": len(miss_targets),
                "miss_repaired": len(repaired),
                "fp_targets": len(fp_targets),
                "fp_acquitted": len(acquitted),
                "sentinel_lost": sentinel_lost,
                "clean_convicted": clean_convicted,
                "net_gain": net,
            },
            "gate": {
                "repaired_something": bool(repaired or acquitted),
                "zero_sentinel_loss": not sentinel_lost,
                "zero_clean_conviction": not clean_convicted,
                "strictly_positive": net > 0,
            },
        }
        reports[convention_id]["gate"]["deployable"] = all(
            reports[convention_id]["gate"].values()
        )
    deployable = [
        convention_id for convention_id, report in reports.items()
        if report["gate"]["deployable"]
    ]
    report = {
        "schema_version": VALIDATION_SCHEMA,
        "per_convention": reports,
        "deployable": deployable,
    }
    _write_json(Path(out_dir) / "convention_validation.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    dataset_root = Path("datasets/raw/agenthallu/AgentHallu")
    extract = sub.add_parser("extract", help="worksheet -> raw conventions")
    extract.add_argument("--run", type=Path, required=True)
    extract.add_argument("--out", type=Path, required=True)
    extract.add_argument("--dataset-root", type=Path, default=dataset_root)
    gate = sub.add_parser("gate", help="G1 mechanical gate")
    gate.add_argument("--raw", type=Path, required=True)
    gate.add_argument("--run", type=Path, required=True)
    gate.add_argument("--out", type=Path, required=True)
    genome = sub.add_parser("genome", help="gated -> genome")
    genome.add_argument("--conventions", type=Path, required=True)
    genome.add_argument("--out", type=Path, required=True)
    validate = sub.add_parser("validate", help="G2 per-convention replay")
    validate.add_argument("--run", type=Path, required=True)
    validate.add_argument("--conventions", type=Path, required=True)
    validate.add_argument("--out-dir", type=Path, required=True)
    validate.add_argument("--dataset-root", type=Path, default=dataset_root)
    validate.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    if args.command == "extract":
        run_tree = load_run_tree(args.run)
        conventions, meta = extract_conventions(
            run_tree=run_tree, dataset_root=args.dataset_root,
        )
        _write_json(args.out, {
            "schema_version": CONVENTION_RAW_SCHEMA,
            "run": str(args.run),
            "conventions": conventions,
            "meta": meta,
        })
        print(f"{meta['raw_conventions']} raw conventions -> {args.out}")
    elif args.command == "gate":
        raw = _read_json(args.raw)
        run_tree = load_run_tree(args.run)
        accepted, rejections = gate_conventions(
            raw.get("conventions") or [], run_tree=run_tree,
        )
        _write_json(args.out, {
            "schema_version": CONVENTION_RAW_SCHEMA,
            "source": str(args.raw),
            "conventions": accepted,
            "rejections": rejections,
        })
        print(
            f"{len(accepted)} accepted, {len(rejections)} rejected "
            f"-> {args.out}",
        )
    elif args.command == "genome":
        payload = _read_json(args.conventions)
        genome = build_convention_genome(
            payload.get("conventions") or [],
        )
        _write_json(args.out, genome)
        print(f"convention genome -> {args.out}")
    elif args.command == "validate":
        payload = _read_json(args.conventions)
        run_tree = load_run_tree(args.run)
        report = validate_conventions(
            run_tree=run_tree,
            conventions=payload.get("conventions") or [],
            dataset_root=args.dataset_root,
            out_dir=args.out_dir,
            workers=args.workers,
        )
        for convention_id, block in report["per_convention"].items():
            print(
                f"{convention_id}: {block['metrics']} "
                f"deployable={block['gate']['deployable']}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
