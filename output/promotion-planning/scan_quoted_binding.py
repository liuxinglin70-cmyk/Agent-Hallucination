"""Mechanical pre-screen for the promotion protocol's benefit set.

Mechanism under test: ``quoted_content_vs_delivered`` — the task quotes exact
required content and the delivered artifact (a written body, a posted message,
a committed value) deviates from that quoted span.

This scan looks for candidate-unused gold-true cases where the base detector is
expected to miss, i.e. where a task-quoted span should have been delivered and
the delivery step deviates. No model calls.

Two screening shapes are collected:

* ``body_deviation``: a delivery-style call (write/post/send/append/update/note)
  whose body argument differs from a task-quoted span while overlapping it;
* ``dedicated_param``: a task-quoted span plus separately required elements
  (tags/mentions/hashtags) that ended up embedded in the body.

Every hit is a shortlist entry only. Whether the error object is decidable from
the input alone — no hidden parameter, no external knowledge, no ambiguous
licence — is a review decision recorded separately and before any model run.
"""

from __future__ import annotations

import glob
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "datasets", "raw", "agenthallu", "AgentHallu")

QUOTE = re.compile(
    r"[\u201c\u2018\"']([^\u201c\u2018\u201d\u2019\"']{15,400})[\u201c\u2018\u201d\u2019\"']"
)

DELIVERY_HINT = re.compile(
    r"write|post|send|append|create|update|publish|note|message|tweet|email|"
    r"submit|save|draft|reply|comment|record|add_",
    re.IGNORECASE,
)
SEARCH_HINT = re.compile(r"search|retriev|lookup|find|query|browse|fetch|read|get_", re.IGNORECASE)

BODY_KEYS = {
    "content", "body", "text", "message", "tweet", "post", "description",
    "note_content", "summary", "caption", "comment", "email", "letter",
    "reply", "announcement", "title", "text_value", "note", "value",
}

USED = {
    "BFCL/003.json", "BFCL/024.json", "BFCL/042.json", "BFCL/054.json",
    "BFCL/013.json", "BFCL/020.json", "BFCL/018.json", "BFCL/053.json",
    "BFCL/017.json", "BFCL/028.json", "BFCL/041.json", "BFCL/062.json",
    "BFCL/002.json", "BFCL/008.json", "BFCL/082.json", "BFCL/118.json",
    "BFCL/129.json", "BFCL/148.json", "BFCL/153.json",
}
USED |= {
    f"BFCL/{n:03d}.json" for n in
    [6, 21, 25, 49, 59, 87, 94, 104, 112, 115, 123, 126, 130, 133,
     138, 140, 144, 146, 151, 155, 156, 158, 159, 161]
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


def iter_calls(raw):
    for index, event in enumerate(raw.get("history") or [], start=1):
        for call in event.get("tool_calls") or []:
            yield index, call


def main() -> int:
    hits: list[tuple] = []
    for path in glob.glob(os.path.join(ROOT, "*", "*.json")):
        case = path.replace(os.sep, "/").split("/")
        case_id = "/".join(case[-2:])
        if case_id in USED:
            continue
        try:
            raw = json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            continue
        if not is_gold_true(raw.get("is_hallucination")):
            continue
        spans = [
            s for s in QUOTE.findall(flat_question(raw.get("question")))
            if len(toks(s)) >= 4
        ]
        if not spans:
            continue
        for step, call in iter_calls(raw):
            name = str(call.get("name") or "")
            if SEARCH_HINT.search(name) and not DELIVERY_HINT.search(name):
                continue
            args = call.get("arguments")
            if not isinstance(args, dict):
                continue
            for key, value in args.items():
                if not isinstance(value, str) or len(value) < 8:
                    continue
                delivery_like = key in BODY_KEYS or DELIVERY_HINT.search(name)
                if not delivery_like:
                    continue
                value_tokens = toks(value)
                for span in spans:
                    span_tokens = toks(span)
                    overlap = len(value_tokens & span_tokens) / max(
                        1, len(value_tokens | span_tokens)
                    )
                    # Deleting part of the quoted span, or adding unquoted
                    # material, both land strictly between "unrelated" and
                    # "verbatim".
                    if 0.34 <= overlap < 1.0:
                        hits.append((
                            overlap, case_id, step, name, key, span, value,
                            raw.get("hallucination_step"),
                        ))
                        break
    hits.sort(reverse=True)
    print(f"{len(hits)} candidate-unused gold-true hits\n")
    for overlap, case_id, step, name, key, span, value, gold_step in hits:
        print(f"{overlap:.2f} {case_id:26s} gold_step={str(gold_step):4s} "
              f"call_step={step} {name[:30]:31s} {key[:12]}")
        print(f"      span ={span[:170]!r}")
        print(f"      deliv={value[:230]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
