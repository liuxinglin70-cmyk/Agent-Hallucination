"""Time one case's provider calls end to end, with generous budgets.

Diagnostic only: it does not touch a frozen protocol, a registry or a
ledger. It answers one question the audit freeze raised — how long does
SmolAgents/019 actually need, and which stage's per-call cap binds — so the
case-budget decision rests on measurement instead of a guess.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import detector.provider as provider_mod
from detector.config import load_config
from detector.dataset import project_record
from detector.api import Detector

CASE = "SmolAgents/019.json"
OUT = ROOT / "artifacts" / "diagnostic_019_timing"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    config = load_config(ROOT / "detector/configs/autodl.json")
    # Generous budgets so nothing is cut; the point is to measure, not to bound.
    config["case_timeout_seconds"] = 5400
    for block in ("provider", "judge_route", "defense_route", "recall_route"):
        route = config.get(block)
        if isinstance(route, dict):
            route["timeout_seconds"] = 600
            route["total_timeout_seconds"] = 1200

    original = provider_mod.provider_call
    calls: list[dict] = []

    def timed_call(*, route, system, user, purpose, cache_tag=""):
        started = time.monotonic()
        entry = {"purpose": purpose, "started_at": datetime.now(timezone.utc).isoformat()}
        calls.append(entry)
        try:
            result, meta = original(
                route=route, system=system, user=user, purpose=purpose,
                cache_tag=cache_tag,
            )
            usage = (meta or {}).get("usage") or {}
            entry.update(
                ok=True,
                seconds=round(time.monotonic() - started, 1),
                completion_tokens=usage.get("completion_tokens"),
                prompt_tokens=usage.get("prompt_tokens"),
                finish_reason=(meta or {}).get("finish_reason"),
            )
            return result, meta
        except Exception as exc:
            entry.update(
                ok=False,
                seconds=round(time.monotonic() - started, 1),
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            raise

    provider_mod.provider_call = timed_call
    record = project_record(json.loads(
        (ROOT / "datasets/raw/agenthallu/AgentHallu" / CASE).read_text(encoding="utf-8")
    ))
    detector = Detector(config=config)
    started = time.monotonic()
    try:
        result = detector.detect(record, case_id=CASE)
        error = None
    except Exception as exc:  # a diagnostic must still report what it measured
        result, error = None, f"{type(exc).__name__}: {exc}"
    elapsed = round(time.monotonic() - started, 1)

    report = {
        "case": CASE,
        "elapsed_seconds": elapsed,
        "case_timeout_seconds": config["case_timeout_seconds"],
        "stage_status": (result or {}).get("stage_status"),
        "state": (result or {}).get("state"),
        "decision_authority": (result or {}).get("decision_authority"),
        "error": error,
        "calls": calls,
        "call_seconds_total": round(sum(c.get("seconds") or 0 for c in calls), 1),
    }
    (OUT / "timing.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "elapsed_seconds": elapsed,
        "state": report["state"],
        "authority": report["decision_authority"],
        "stage_status": report["stage_status"],
        "calls": [
            {k: c.get(k) for k in ("purpose", "ok", "seconds", "completion_tokens")}
            for c in calls
        ],
        "error": error,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
