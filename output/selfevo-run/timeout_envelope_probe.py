"""Decisive probe: is the 300s per-attempt cap the source of the time variance?

Hypothesis under test. The autodl route allows 300s per attempt and 900s per
call. Measured deliberation is 20-46 tok/s and routinely produces 5,000-13,000
reasoning tokens, so a *successful* call can legitimately need 250-570s. Any
call that needs more than 300s is killed by the per-attempt cap and retried
with a longer timeout, paying the first attempt's tokens and wall clock again.

If that is what is happening, then raising the per-attempt cap should make
those calls complete on attempt 1 -- and the tell is a run with
``attempts == 1`` and ``elapsed > 300``, which is impossible under the old cap.

The control is the ticket: under the *old* cap, no single attempt can ever
exceed 300s. So an observed attempt-1 success above 300s is direct proof the
cap was the binding constraint, not the network and not the prompt size.

Writes nothing outside stdout; spends a few thousand provider tokens.

    python output/selfevo-run/timeout_envelope_probe.py [--repeats 3]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from detector.config import load_config  # noqa: E402
from detector.env import load_local_env  # noqa: E402
from detector.provider import ProviderError, provider_call  # noqa: E402

SYSTEM = (
    "You are a careful auditor. Consider every competing reading of the "
    "evidence, then reply with a single JSON object and nothing else."
)
USER = (
    "A trajectory claims 'statistics.txt' holds the mean of 5000, 3000, 2000 "
    "but wrote '5000\\n3000\\n2000'. Work through every competing reading, "
    "explain which survives and why. End with "
    '{"is_error": true|false, "reason": "..."}'
)

# The old envelope is the control: a call may not exceed 300s in one attempt.
OLD = {"timeout_seconds": 300, "total_timeout_seconds": 900, "transport_retries": 2}
NEW = {"timeout_seconds": 900, "total_timeout_seconds": 1800, "transport_retries": 2}


def one(route: dict, *, label: str, envelope: dict, index: int) -> dict:
    trial = dict(route)
    trial.update(envelope)
    trial["_route_name"] = f"probe_{label}_{index}"
    started = time.monotonic()
    try:
        parsed, meta = provider_call(
            route=trial, system=SYSTEM, user=USER, purpose=f"probe_{label}",
        )
        elapsed = time.monotonic() - started
        usage = meta.get("usage") or {}
        completion = int(usage.get("completion_tokens") or 0)
        reasoning = int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            or 0
        )
        attempts = int(meta.get("attempts") or 1)
        return {
            "envelope": label,
            "index": index,
            "ok": True,
            "elapsed_s": round(elapsed, 2),
            "attempts": attempts,
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "tok_per_s": round(completion / elapsed, 1) if elapsed else None,
            # The proof obligation: a single attempt over the old cap.
            "exceeds_old_cap_on_first_attempt": bool(
                attempts == 1 and elapsed > OLD["timeout_seconds"]
            ),
            "verdict": (parsed or {}).get("is_error"),
        }
    except ProviderError as exc:
        return {
            "envelope": label,
            "index": index,
            "ok": False,
            "elapsed_s": round(time.monotonic() - started, 2),
            "attempts": exc.attempts,
            "error_type": exc.error_type,
            "error": exc.message[:160],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="detector/configs/autodl.json")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    load_local_env()
    config = load_config(args.config)
    route = dict(config["judge_route"])
    if not os.environ.get(str(route.get("api_key_env") or "")):
        print(json.dumps({
            "ok": False,
            "error": f"credential env var {route.get('api_key_env')!r} is not set",
        }))
        return 2

    print(json.dumps({
        "note": "one deliberation prompt under two envelopes; the claim under "
                "test is that the 300s per-attempt cap, not the network, costs "
                "the extra wall clock",
        "old_envelope": OLD,
        "new_envelope": NEW,
        "model": route.get("model"),
    }, ensure_ascii=False), flush=True)

    for index in range(1, args.repeats + 1):
        print(json.dumps(one(route, label="old", envelope=OLD, index=index),
                         ensure_ascii=False), flush=True)
    for index in range(1, args.repeats + 1):
        print(json.dumps(one(route, label="new", envelope=NEW, index=index),
                         ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
