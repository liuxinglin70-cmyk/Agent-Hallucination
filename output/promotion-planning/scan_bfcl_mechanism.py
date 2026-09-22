"""Find candidate-unused BFCL cases carrying the frozen mechanism.

The frozen mechanism is ``quoted_content_vs_delivered``: the task quotes exact
content that must be delivered, and the delivery step deviates from that quoted
span (unquoted additions, or separately required elements embedded in the quoted
body while their dedicated parameter stays empty).

The pilot reviewed 31 BFCL "Incorrect Tool Arguments" cases and measured 19.
The remaining BFCL cases were never reviewed, so they are the candidate-unused
pool. This scan is mechanical and label-free: it only proposes a shortlist, and
every hit still needs the human review recorded by ``review_cases.py``.
"""

from __future__ import annotations

import glob
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")

QUOTE = re.compile(
    r"[\u201c\u2018\"']([^\u201c\u2018\u201d\u2019\"']{12,400})[\u201c\u2018\u201d\u2019\"']"
)
DEDICATED = re.compile(r"hashtag|mention|tags?\b|label", re.IGNORECASE)
WRITE_CALL = re.compile(
    r"echo|write|post|send|append|create|update|add_|note|tweet|message|"
    r"email|publish|comment",
    re.IGNORECASE,
)

# Cases already measured or reviewed during the pilot: unusable as the
# promotion batch, which must be candidate-unused.
MEASURED = {
    "BFCL/002.json", "BFCL/003.json", "BFCL/008.json", "BFCL/013.json",
    "BFCL/017.json", "BFCL/018.json", "BFCL/020.json", "BFCL/024.json",
    "BFCL/028.json", "BFCL/041.json", "BFCL/042.json", "BFCL/053.json",
    "BFCL/054.json", "BFCL/062.json", "BFCL/082.json", "BFCL/118.json",
    "BFCL/129.json", "BFCL/148.json", "BFCL/153.json",
}
REVIEWED_OUT = {
    f"BFCL/{n:03d}.json" for n in
    [6, 21, 25, 49, 59, 87, 94, 104, 112, 115, 123, 126, 130, 133, 138,
     140, 144, 146, 151, 155, 156, 158, 159, 161]
}


def toks(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def is_gold_true(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


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


def main() -> int:
    hits = []
    for path in sorted(glob.glob(os.path.join(ROOT, "BFCL", "*.json"))):
        case_id = "BFCL/" + os.path.basename(path)
        if case_id in MEASURED or case_id in REVIEWED_OUT:
            continue
        raw = json.load(open(path, encoding="utf-8-sig"))
        question = flat_question(raw.get("question"))
        spans = [s for s in QUOTE.findall(question) if len(toks(s)) >= 4]
        if not spans:
            continue
        history = raw.get("history") or []
        for step, event in enumerate(history, start=1):
            for call in event.get("tool_calls") or []:
                name = str(call.get("name") or "")
                if not WRITE_CALL.search(name):
                    continue
                args = call.get("arguments")
                if not isinstance(args, dict):
                    continue
                for key, value in args.items():
                    if not isinstance(value, str) or len(value) < 8:
                        continue
                    value_tokens = toks(value)
                    for span in spans:
                        span_tokens = toks(span)
                        if not span_tokens:
                            continue
                        contained = span_tokens <= value_tokens
                        overlap = len(value_tokens & span_tokens) / max(
                            1, len(value_tokens | span_tokens)
                        )
                        # Either the quoted span was extended with unquoted
                        # material, or it was written into a parameter while
                        # separately required elements went missing.
                        if contained and len(value_tokens) > len(span_tokens):
                            hits.append((
                                "unquoted_addition", case_id, step, name, key,
                                span, value, raw.get("hallucination_step"),
                                raw.get("is_hallucination"),
                            ))
                        elif overlap >= 0.5 and overlap < 1.0:
                            hits.append((
                                "partial_deviation", case_id, step, name, key,
                                span, value, raw.get("hallucination_step"),
                                raw.get("is_hallucination"),
                            ))
                        break
    print(f"{len(hits)} mechanism-shaped hits among candidate-unused BFCL cases\n")
    for kind, case_id, step, name, key, span, value, gold_step, gold in hits:
        print(f"{kind:18s} {case_id:20s} gold={str(gold):5s} "
              f"gold_step={str(gold_step):4s} call_step={step} {name}")
        print(f"      span ={span[:150]!r}")
        print(f"      deliv={value[:200]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
