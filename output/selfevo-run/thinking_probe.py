"""Read-only probe: can the autodl route bound its own reasoning length?

Motivation. ``detector/configs/deepseek.json`` asks its gateway to turn
thinking off (``extra_body.thinking.type = disabled``), because every detector
stage wants a small structured JSON verdict, not a deliberation. The autodl
route carries no such control, and measurement on 2026-09-21 shows the model
spending 90-97% of its completion tokens on reasoning: judge ballots of 12,401
tokens, recall scans of 11,904, and an identical prompt answered once in 802
tokens (23s) and once in 11,365 tokens (547s). Wall clock tracks those tokens
at 20-46 tok/s, which is what puts cases past the 3600s case deadline.

This probe sends the *same* question under several thinking controls and
reports, per variant, whether the gateway accepted it, how long it took, and
how many tokens it burned. It answers one question: is the reasoning length
something the route can bound, or is it the model's unconditional behaviour?

Nothing here writes to the repo, the result cache, or the registry: the output
is stdout JSON lines. It spends a few thousand provider tokens.

    python output/selfevo-run/thinking_probe.py [--config detector/configs/autodl.json]
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
    "but wrote '5000\\n3000\\n2000'. Is that a content error? End with "
    '{"is_error": true|false, "reason": "..."}'
)

# Candidate controls. The first is exactly what the DeepSeek route already
# sends; the rest are the spellings other OpenAI-compatible gateways accept.
VARIANTS: list[tuple[str, dict | None]] = [
    ("baseline", None),
    ("thinking_disabled", {"thinking": {"type": "disabled"}}),
    ("enable_thinking_false", {"enable_thinking": False}),
    ("reasoning_effort_none", {"reasoning_effort": "none"}),
    ("reasoning_effort_low", {"reasoning_effort": "low"}),
]


def one(route: dict, *, label: str, extra: dict | None) -> dict:
    trial = dict(route)
    trial["_route_name"] = f"probe_{label}"
    if extra is not None:
        trial["extra_body"] = extra
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
        return {
            "variant": label,
            "accepted": True,
            "elapsed_s": round(elapsed, 2),
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "reasoning_share": round(reasoning / completion, 3) if completion else None,
            "attempts": meta.get("attempts"),
            "finish_reason": meta.get("finish_reason"),
            "tok_per_s": round(completion / elapsed, 1) if elapsed else None,
            "verdict": (parsed or {}).get("is_error"),
            "reason_preview": str((parsed or {}).get("reason") or "")[:120],
        }
    except ProviderError as exc:
        return {
            "variant": label,
            "accepted": False,
            "elapsed_s": round(time.monotonic() - started, 2),
            "error_type": exc.error_type,
            "error": exc.message[:200],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="detector/configs/autodl.json")
    parser.add_argument("--only", default="")
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
        "note": "one prompt, five thinking controls, single calls at zero "
                "concurrency; 'accepted' means the gateway took the field, "
                "not that the answer is right",
        "model": route.get("model"),
    }, ensure_ascii=False), flush=True)

    wanted = {part for part in args.only.split(",") if part}
    for label, extra in VARIANTS:
        if wanted and label not in wanted:
            continue
        print(json.dumps(one(route, label=label, extra=extra),
                         ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
