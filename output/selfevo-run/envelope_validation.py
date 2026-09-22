"""End-to-end check that the new envelope stops cutting real cases.

The claim under test. Under the old envelope (300s per attempt, 900s per call,
3600s per case) two of 18 real cases were killed at exactly 3600s while still
working; a cut call returns nothing and burns its whole budget, and it can land
on one arm only, which HARMLESSNESS then reads as a candidate-caused
regression. The fix raised the envelope to 900s / 2400s / 10800s.

This runs real cases through the real provider and reports, per case, the wall
clock, per-stage attempts, whether any stage hit the case deadline, and the
stage total token burn (the new ``usage_total``). It writes its rows to a JSON
file so the numbers in the report come from a file and not from a transcript.

It does not touch the evolution result cache: it calls the detector directly,
which is the same code path the evolution backend uses but without the
CachingBackend layer above it.

    python output/selfevo-run/envelope_validation.py \
        --cases Octotools/008.json Camel/048.json --out output/selfevo-run/envelope-validation.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from detector.api import Detector  # noqa: E402
from detector.config import load_config  # noqa: E402
from detector.dataset import load_case  # noqa: E402
from detector.env import load_local_env  # noqa: E402
from detector.interventions import build_intervention_registry  # noqa: E402


def stage_burn(result: dict) -> dict:
    """Per-stage attempts and total burn, preferring the new ``usage_total``."""
    providers = result.get("providers") or {}
    status = result.get("stage_status") or {}
    out = {}
    for stage, entry in providers.items():
        entry = entry or {}
        last = entry.get("usage") or {}
        total = entry.get("usage_total") or last
        out[stage] = {
            "attempts": entry.get("attempts"),
            "stage_state": (status.get(stage) or {}).get("state"),
            "last_ballot_completion_tokens": int(last.get("completion_tokens") or 0),
            "total_completion_tokens": int(total.get("completion_tokens") or 0),
            "total_reasoning_tokens": int(
                (total.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens"
                ) or 0
            ),
            "ballots": (entry.get("vote") or {}).get("cast"),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="detector/configs/autodl.json")
    parser.add_argument("--dataset", default="datasets/raw/agenthallu/AgentHallu")
    parser.add_argument("--out", default="")
    parser.add_argument("--label", default="new-envelope")
    parser.add_argument("cases", nargs="*")
    args = parser.parse_args()

    cases = args.cases or ["Octotools/008.json", "Camel/048.json"]
    load_local_env()
    config = load_config(args.config)
    detector = Detector(config)
    registry = build_intervention_registry([])

    print(json.dumps({
        "label": args.label,
        "case_timeout_seconds": config.get("case_timeout_seconds"),
        "per_attempt_timeout": (config.get("judge_route") or {}).get("timeout_seconds"),
        "per_call_timeout": (config.get("judge_route") or {}).get("total_timeout_seconds"),
        "cases": cases,
    }, ensure_ascii=False), flush=True)

    rows = []
    for case in cases:
        record, gold = load_case(args.dataset, case)
        started = time.monotonic()
        try:
            result = detector.detect(record, case_id=case, interventions=registry)
            elapsed = time.monotonic() - started
            burn = stage_burn(result)
            row = {
                "case": case,
                "label": args.label,
                "elapsed_s": round(elapsed, 1),
                "elapsed_min": round(elapsed / 60, 1),
                "state": result.get("state"),
                "authority": result.get("decision_authority"),
                "responsible_step": result.get("responsible_step"),
                "gold": (gold or {}).get("is_hallucination"),
                "gold_step": (gold or {}).get("hallucination_step"),
                "hit_case_deadline": any(
                    (entry or {}).get("state") == "case_deadline_exceeded"
                    for entry in (result.get("stage_status") or {}).values()
                ),
                "stages": burn,
                "total_completion_tokens": sum(
                    item["total_completion_tokens"] for item in burn.values()
                ),
                "total_attempts": sum(
                    int(item["attempts"] or 0) for item in burn.values()
                ),
            }
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            row = {
                "case": case,
                "label": args.label,
                "elapsed_s": round(time.monotonic() - started, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    if args.out:
        path = Path(args.out)
        existing = []
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "label": args.label,
            "envelope": {
                "case_timeout_seconds": config.get("case_timeout_seconds"),
                "per_attempt_timeout": (config.get("judge_route") or {}).get("timeout_seconds"),
                "per_call_timeout": (config.get("judge_route") or {}).get("total_timeout_seconds"),
            },
            "rows": existing + rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
