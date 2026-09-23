"""Offline probe: movement *extraction* vs movement *satisfaction*.

The movement branch had two independent defects. This probe separates them:

E) extraction: "proceed to <verb phrase>" is an instruction to act, not a
   destination. Markers that take a destination (go to / visit / drive to ...)
   stay; "proceed to" goes.
S) satisfaction: the runtime test demanded a *movement-verb* tool name, which
   encodes "movement is done by a navigation tool" rather than "some tool call
   referred to the target". Variants: any-tool-any-token (M2) vs all-tokens (M3).

Also measured: the action branch's tool-object condition with and without the
identifier-shape condition.
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
STOP = {"the", "a", "an", "to", "and", "of", "in", "on"}


def actions_tool_object(question: str, shape: bool) -> set[str]:
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
            if shape:
                matched = NAME_SHAPE.match(phrase)
                if matched is None:
                    continue
                phrase = matched.group(0)
            name = " ".join(phrase.split()).strip(" .-").lower()
            if name and name not in {"tool", "function", "api", "command"}:
                actions.add(name)
    for match in re.finditer(
        r"required_action\s*[:=]\s*[\"']([^\"']+)[\"']", question, re.I,
    ):
        actions.add(match.group(1).strip().lower())
    return {name for name in actions if name}


def movement_targets(texts, keep_proceed: bool) -> list[str]:
    pattern = re.compile(
        r"(swing by|head over to|head to|drive to|walk to|stop by|drop by|"
        r"travel to|visit"
        + (r"|proceed to" if keep_proceed else "")
        + r")\s+(?:the\s+)?",
        re.I,
    )
    found: list[str] = []
    for text in texts:
        for match in pattern.finditer(text):
            tail = TAIL.match(text[match.end(): match.end() + 80])
            if tail is None:
                continue
            target = " ".join(tail.group(1).split())
            if len(target) < 6:
                continue
            if target.split()[0].lower().strip(".-'") in FUNCTION_WORD_INITIAL:
                continue
            if target.lower() not in found:
                found.append(target.lower())
    return found


def satisfied(packet, target: str, mode: str) -> bool:
    tokens = {tok for tok in target.split() if tok not in STOP}
    if not tokens:
        return True
    for event in packet["events"]:
        for interaction in event["tool_interactions"]:
            name = str(interaction.get("tool_name") or "").lower()
            args = str(interaction.get("arguments") or "").lower()
            haystack = f"{name} {args}"
            if mode == "M1":  # runtime: movement-verb tool + any shared token
                movement = {"cd", "chdir", "navigate", "goto", "go", "travel",
                            "drive", "move", "visit", "head", "swing", "walk"}
                if any(verb in name for verb in movement) and any(
                    tok in args or tok in name for tok in tokens
                ):
                    return True
            elif mode == "M2" and any(tok in haystack for tok in tokens):
                return True
            elif mode == "M3" and all(tok in haystack for tok in tokens):
                return True
    return False


def measure(label, *, keep_proceed, sat_mode, action_shape) -> None:
    P._detect_required_actions = lambda q: actions_tool_object(q, action_shape)
    P._movement_requirements = lambda t: movement_targets(t, keep_proceed)
    # Patch the satisfaction test by wrapping the runtime predicate's helper use:
    # simplest faithful way is to run the real predicate with a stub that already
    # returns targets, then re-check satisfaction ourselves. Instead we rebuild the
    # branch here and compare against the runtime's own branch by name.
    counts: Counter = Counter()
    hits = []
    for path in sorted(CORPUS.rglob("*.json")):
        case = path.relative_to(CORPUS).as_posix()
        record, gold = load_case(CORPUS, case)
        packet = build_packet(record)
        clean = gold.get("is_hallucination") == "false"
        question = P._question_text(packet)
        names = P._detect_required_actions(question)
        tool_names = {t.lower() for t in P._tool_names(packet)}
        missing = sorted(n for n in names if not any(n in t for t in tool_names))
        targets = P._movement_requirements([question])
        unfilled = [t for t in targets if not satisfied(packet, t, sat_mode)]
        fired = bool(missing or unfilled)
        counts[("clean" if clean else "error", fired)] += 1
        if fired:
            hits.append((case, clean, missing, unfilled))
    print(f"[{label}] clean_fp={counts[('clean', True)]} "
          f"error_hits={counts[('error', True)]}")
    for case, clean, missing, unfilled in hits:
        print(f"     {'CLEAN' if clean else 'ERROR'} {case} "
              f"missing={missing} movement={unfilled}")


def main() -> None:
    original = (P._detect_required_actions, P._movement_requirements)
    print("== movement extraction/satisfaction matrix (action branch: tool-object only) ==")
    for keep in (True, False):
        for mode in ("M1", "M2", "M3"):
            measure(f"proceed={keep} sat={mode}", keep_proceed=keep,
                    sat_mode=mode, action_shape=False)
    print()
    print("== action branch: with vs without identifier shape (movement off) ==")
    P._movement_requirements = lambda t: []
    for shape in (False, True):
        measure(f"action_shape={shape}", keep_proceed=False, sat_mode="M3",
                action_shape=shape)
    P._detect_required_actions, P._movement_requirements = original


if __name__ == "__main__":
    main()
