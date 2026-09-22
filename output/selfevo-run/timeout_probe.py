"""Read-only probe: where do the seconds in a detector case actually go?

Motivation. The 2026-09-21 self-evolution run showed a 30x spread in
seconds-per-provider-call (12s .. 400s) for one model on one config. The plan
needs to know whether that is (a) token volume the model itself chooses,
(b) gateway contention from N concurrent workers, or (c) retry escalation.

This probe answers that with controlled single calls at *zero* concurrency:

  * a tiny call, to read the floor (round-trip + tiny answer)
  * a call that asks for a long deliberation, to read sustained tok/s
  * the same long call repeated, to see whether latency is stable when the
    gateway is idle

It never touches the evolution result cache (that cache keys on a case record
at the backend layer, above this one) and writes nothing to the repo: the
output is stdout JSON lines only. It does spend a few thousand provider
tokens.

    python output/selfevo-run/timeout_probe.py [--config detector/configs/autodl.json]
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

TINY_SYSTEM = "You answer with a single JSON object and nothing else."
TINY_USER = 'Reply with exactly {"ok": true}.'

# Asks for the kind of deliberation the judge prompt triggers, without sending
# a 19k-token packet: the question is whether *thinking* is what costs wall
# clock, and thinking is requested by instruction, not by prompt length.
THINK_SYSTEM = (
    "You are a careful auditor. Think through the problem step by step, "
    "consider every alternative, and reply with a single JSON object."
)
THINK_USER = (
    "A trajectory claims a file 'statistics.txt' contains the mean of the "
    "values 5000, 3000 and 2000, and it wrote '5000\\n3000\\n2000'. Decide "
    "whether this is a content error, list every competing reading of the "
    "evidence, and state which one survives scrutiny. End with "
    '{"is_error": true|false, "reason": "..."}'
)


def one(route: dict, *, tag: str, system: str, user: str) -> dict:
    started = time.monotonic()
    try:
        parsed, meta = provider_call(
            route=route, system=system, user=user, purpose=f"probe_{tag}",
        )
        elapsed = time.monotonic() - started
        usage = meta.get("usage") or {}
        completion = int(usage.get("completion_tokens") or 0)
        reasoning = int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            or 0
        )
        return {
            "probe": tag,
            "ok": True,
            "elapsed_s": round(elapsed, 2),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "attempts": meta.get("attempts"),
            "finish_reason": meta.get("finish_reason"),
            "tok_per_s": round(completion / elapsed, 1) if elapsed else None,
            "parsed_ok": parsed is not None,
        }
    except ProviderError as exc:
        return {
            "probe": tag,
            "ok": False,
            "elapsed_s": round(time.monotonic() - started, 2),
            "error_type": exc.error_type,
            "error": exc.message[:200],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="detector/configs/autodl.json")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()

    load_local_env()
    config = load_config(args.config)
    # ``load_config`` flattens the ``provider`` block into each route, so the
    # provider defaults are read off the route the detector actually uses.
    route = dict(config["judge_route"])
    route["_route_name"] = "probe"
    if not os.environ.get(str(route.get("api_key_env") or "")):
        print(json.dumps({
            "ok": False,
            "error": f"credential env var {route.get('api_key_env')!r} is not set",
        }))
        return 2

    print(json.dumps({
        "note": "single calls, zero concurrency; tokens here are the model's "
                "own deliberation, not a prompt-size effect",
        "endpoint": route.get("endpoint"),
        "model": route.get("model"),
        "timeout_seconds": route.get("timeout_seconds"),
        "total_timeout_seconds": route.get("total_timeout_seconds"),
    }, ensure_ascii=False), flush=True)

    rows = [one(route, tag="tiny", system=TINY_SYSTEM, user=TINY_USER)]
    for index in range(args.repeats):
        rows.append(one(
            route,
            tag=f"think{index + 1}",
            system=THINK_SYSTEM,
            user=THINK_USER,
        ))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
