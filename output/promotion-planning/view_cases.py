"""Compact multi-case viewer for building the promotion batch.

Prints the label, category, task text and write/delivery calls of several
unreviewed BFCL cases at once, so a batch can be selected by a rule fixed in
advance rather than by how the candidate behaves.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")

QUOTE = re.compile(
    r"[\u201c\u2018\"']([^\u201c\u2018\u201d\u2019\"']{12,400})[\u201c\u2018\u201d\u2019\"']"
)
WRITE = re.compile(
    r"echo|write|post|send|append|create|update|add_|note|tweet|message|"
    r"email|publish|comment|touch",
    re.IGNORECASE,
)


def flat_question(question) -> str:
    if isinstance(question, list):
        parts: list[str] = []
        for item in question:
            if isinstance(item, (list, tuple)):
                parts.extend(str(x) for x in item)
            else:
                parts.append(str(item))
        return " ".join(parts)
    return str(question)


def gold(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def main() -> int:
    patterns = sys.argv[1:] or ["BFCL/*.json"]
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
            raw = json.load(open(path, encoding="utf-8-sig"))
            case_id = "/".join(path.replace(os.sep, "/").split("/")[-2:])
            question = flat_question(raw.get("question"))
            spans = [s for s in QUOTE.findall(question) if len(s) > 20]
            print("=" * 96)
            print(f"{case_id}  gold={gold(raw.get('is_hallucination'))} "
                  f"step={raw.get('hallucination_step')} "
                  f"cat={raw.get('hallucination_category')}")
            print(f"  Q: {question[:600]}")
            if spans:
                print(f"  quoted: {[s[:90] for s in spans[:3]]}")
            for index, event in enumerate(raw.get("history") or [], start=1):
                for call in event.get("tool_calls") or []:
                    name = str(call.get("name") or "")
                    if not WRITE.search(name):
                        continue
                    args = json.dumps(call.get("arguments"), ensure_ascii=False)
                    print(f"  step{index} {name}: {args[:260]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
