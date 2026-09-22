"""Inspect one shortlisted case's task text, delivery step and gold rationale."""

from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")


def main() -> int:
    case_id = sys.argv[1] if len(sys.argv) > 1 else "BFCL/023.json"
    raw = json.load(open(os.path.join(ROOT, case_id), encoding="utf-8-sig"))
    print("=" * 90)
    print("CASE", case_id)
    print("keys:", list(raw.keys()))
    print("is_hallucination:", raw.get("is_hallucination"),
          "step:", raw.get("hallucination_step"))
    print("category:", raw.get("hallucination_category"),
          "/", raw.get("hallucination_subcategory"))
    print("-" * 90)
    print("QUESTION:")
    print(json.dumps(raw.get("question"), ensure_ascii=False, indent=1))
    print("-" * 90)
    print("REASON:")
    print(str(raw.get("hallucination_reason"))[:2500])
    print("-" * 90)
    print("EXPLANATION:")
    print(str(raw.get("explanation"))[:1200])
    print("-" * 90)
    for index, event in enumerate(raw.get("history") or [], start=1):
        print(f"--- step {index} role={event.get('role')}")
        if event.get("content"):
            print("   content:", str(event["content"])[:900])
        for call in event.get("tool_calls") or []:
            print("   CALL", json.dumps(call, ensure_ascii=False)[:1100])
        for response in event.get("tool_responses") or []:
            print("   RESP", str(response)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
