"""Diagnostics for the quoted-binding scan: why did the shortlist come back empty?"""

from __future__ import annotations

import glob
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")
QUOTE = re.compile(r"[\u201c\u201d\"']([^\u201c\u201d\"']{12,300})[\u201c\u201d\"']")


def toks(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def flat_question(question) -> str:
    if isinstance(question, list):
        parts = []
        for item in question:
            if isinstance(item, (list, tuple)):
                parts.extend(str(x) for x in item)
            else:
                parts.append(str(item))
        return " ".join(parts)
    return str(question)


def main() -> int:
    total = quoted = string_args = gold_true = 0
    by_source: dict[str, int] = {}
    arg_keys: dict[str, int] = {}
    for path in glob.glob(os.path.join(ROOT, "*", "*.json")):
        case = path.replace(os.sep, "/").split("/")
        source = case[-2]
        by_source[source] = by_source.get(source, 0) + 1
        try:
            raw = json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            continue
        total += 1
        if raw.get("is_hallucination") is True:
            gold_true += 1
        if [s for s in QUOTE.findall(flat_question(raw.get("question")))
                if len(toks(s)) >= 4]:
            quoted += 1
        for event in raw.get("history") or []:
            for call in event.get("tool_calls") or []:
                args = call.get("arguments")
                if not isinstance(args, dict):
                    continue
                for key, value in args.items():
                    if isinstance(value, str) and len(value) >= 12:
                        string_args += 1
                        arg_keys[key] = arg_keys.get(key, 0) + 1
    print(f"total={total} gold_true={gold_true} with_quoted_spans={quoted} "
          f"string_args={string_args}")
    print("by source:", by_source)
    print("top arg keys:", sorted(arg_keys.items(), key=lambda x: -x[1])[:25])
    sample = glob.glob(os.path.join(ROOT, "Camel", "*.json"))[:2]
    for path in sample:
        raw = json.load(open(path, encoding="utf-8-sig"))
        print("---", os.path.basename(path), raw.get("is_hallucination"))
        print(flat_question(raw.get("question"))[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
