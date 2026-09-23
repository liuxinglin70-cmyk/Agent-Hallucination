"""Offline probe: refined candidate rules, measured through the real predicate.

Variant under test (one piece at a time, then combined):

* required-action extraction becomes a *double condition*: the obligation marker
  must be followed by an explicit tool-semantics object (tool/function/api/
  command/endpoint) and the name in front of it must be identifier-shaped
  (<= 3 words, no sentence punctuation).
* movement extraction keeps destination-taking markers only ("proceed to" takes
  an infinitive verb phrase -- "proceed to close the ticket" -- so it is not a
  movement marker) and rejects targets that start with a function word.
* the pending-permission engine check requires the permission ask to be the
  trajectory's *last* event, not merely present somewhere in it.
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from detector.dataset import load_case  # noqa: E402
from detector.packet import build_packet  # noqa: E402
from detector import predicates as P  # noqa: E402

CORPUS = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"

TOOL_OBJECT = re.compile(r"\b(tool|function|api|command|endpoint)\b", re.I)
NAME_SHAPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\s+[A-Za-z0-9_]+){0,2}")
LEAD_IN = re.compile(r"^(?:the|a|an)\s+", re.I)


def actions_double_condition(question: str) -> set[str]:
    actions: set[str] = set()
    lowered = question.lower()
    for marker in P._ACTION_MARKERS:
        start = 0
        while True:
            pos = lowered.find(marker, start)
            if pos < 0:
                break
            start = pos + max(1, len(marker))
            tail = question[pos + len(marker): pos + len(marker) + 60]
            obj = TOOL_OBJECT.search(tail)
            if obj is None or obj.start() > 40:
                continue
            phrase = LEAD_IN.sub("", tail[:obj.start()].strip(" \t:-"))
            shape = NAME_SHAPE.match(phrase)
            if shape is None:
                continue
            name = " ".join(shape.group(0).split()).strip(" .-").lower()
            if name and name not in {"tool", "function", "api", "command"}:
                actions.add(name)
    for match in re.finditer(
        r"required_action\s*[:=]\s*[\"']([^\"']+)[\"']", question, re.I,
    ):
        actions.add(match.group(1).strip().lower())
    return {name for name in actions if name}


DESTINATION = re.compile(
    r"(?:swing by|head over to|head to|drive to|walk to|stop by|drop by|"
    r"travel to|visit)\s+(?:the\s+)?",
    re.I,
)
TAIL = re.compile(
    r"([A-Za-z0-9_ .'-]+?)(?=\s+(?:and|or|to|for|before|while|with|when|,|\.)|[.?]|$)"
)
FUNCTION_WORD_INITIAL = frozenset({
    "and", "or", "but", "then", "also", "so", "because", "if", "when", "while",
    "as", "that", "to", "for", "with", "without", "in", "on", "at", "by",
    "from", "need", "needs", "visit", "yet", "nor", "than", "which", "who",
})


def movement_destination_only(texts) -> list[str]:
    found: list[str] = []
    for text in texts:
        for match in DESTINATION.finditer(text):
            tail = TAIL.match(text[match.end(): match.end() + 80])
            if tail is None:
                continue
            target = " ".join(tail.group(1).split())
            if len(target) < 3:
                continue
            first = target.split()[0].lower().strip(".-'")
            if first in FUNCTION_WORD_INITIAL:
                continue
            if target.lower() not in found:
                found.append(target.lower())
    return found


def permission_terminal_only(packet) -> bool:
    events = list(packet.get("events") or [])
    if not events:
        return False
    text = str(events[-1].get("content") or "").lower()
    return "?" in text and any(
        phrase in text
        for phrase in ("would you like me to", "should i", "do you want me to",
                       "shall i", "may i")
    )


def measure(label: str) -> Counter:
    counts: Counter = Counter()
    hits = []
    for path in sorted(CORPUS.rglob("*.json")):
        case = path.relative_to(CORPUS).as_posix()
        record, gold = load_case(CORPUS, case)
        packet = build_packet(record)
        clean = gold.get("is_hallucination") == "false"
        result = P.run_predicate("required_action_present", packet, {})
        verdict = str(result.get("verdict") or "")
        counts[("clean" if clean else "error", verdict)] += 1
        if verdict == "violated":
            hits.append((case, clean, result["detail"]))
    print(f"[{label}] clean violated={counts[('clean', 'violated')]} "
          f"error violated={counts[('error', 'violated')]}")
    for case, clean, detail in hits:
        print(f"     {'CLEAN' if clean else 'ERROR'} {case}: {detail[:110]}")
    return counts


def main() -> None:
    original = (P._detect_required_actions, P._movement_requirements,
                P._permission_pending)
    measure("baseline")

    P._detect_required_actions = actions_double_condition
    measure("A: double-condition action extraction")
    P._detect_required_actions = original[0]

    P._movement_requirements = movement_destination_only
    measure("M: destination-only movement extraction")
    P._movement_requirements = original[1]

    P._permission_pending = permission_terminal_only
    measure("S: terminal-only permission ask")
    P._permission_pending = original[2]

    P._detect_required_actions = actions_double_condition
    P._movement_requirements = movement_destination_only
    P._permission_pending = permission_terminal_only
    final = measure("COMBINED")
    P._detect_required_actions, P._movement_requirements, P._permission_pending = original
    print("combined verdict table:",
          {f"{k[0]}/{k[1]}": v for k, v in sorted(final.items())})


if __name__ == "__main__":
    main()
