"""Offline probe: the simplest defensible observation rule for final_delivery_present.

Rule under test: with the runtime envelope ("Observed output of cmd `x`
executed:") and the fixed completion banner removed, an observation that still
carries at least N characters is a delivery channel. No other filter.

Measured through the real predicate via ``_RUNNERS``.
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from detector.dataset import load_case  # noqa: E402
from detector.packet import build_packet, delivered_result  # noqa: E402
from detector import predicates as P  # noqa: E402

CORPUS = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
ENVELOPE = re.compile(r"Observed output of cmd `[^`]*` executed:")
BANNER = re.compile(r"The interaction has been completed with status: success")


def payload(observation) -> str:
    text = ENVELOPE.sub("", str(observation or ""))
    return BANNER.sub("", text).strip()


def make_variant(minimum: int, failure_filter: bool = False):
    failure = re.compile(
        r"(error:|traceback|\"success\":\s*false|'success':\s*false|could not"
        r"|can'?t be started|invalid|not found|unavailable)", re.I)

    def variant(packet, context):
        anchors = delivered_result(packet)
        has_prose = bool(anchors.get("last_prose_event_id"))
        answer = anchors.get("last_answer_tool_call") or {}
        name = str(answer.get("tool_name") or "").lower()
        has_answer_tool = bool(answer) and not any(
            marker in name for marker in ("terminate", "finish", "success"))
        terminal_success = any(
            "success" in str(i.get("arguments") or "").lower()
            or "success" in str(i.get("observation") or "").lower()
            for e in packet.get("events") or ()
            for i in e.get("tool_interactions") or ()
        )
        delivered = False
        for event in packet.get("events") or ():
            for interaction in event.get("tool_interactions") or ():
                text = payload(interaction.get("observation"))
                if failure_filter and failure.search(text):
                    continue
                if len(text) >= minimum:
                    delivered = True
        if terminal_success and not has_prose and not has_answer_tool and not delivered:
            return {"predicate": "final_delivery_present", "verdict": "missing",
                    "detail": "probe"}
        if not terminal_success:
            return {"predicate": "final_delivery_present",
                    "verdict": "not_applicable", "detail": "probe"}
        return {"predicate": "final_delivery_present", "verdict": "present",
                "detail": "probe"}
    return variant


def measure(label: str, variant) -> None:
    P._RUNNERS["final_delivery_present"] = variant
    counts: Counter = Counter()
    hits = []
    for path in sorted(CORPUS.rglob("*.json")):
        case = path.relative_to(CORPUS).as_posix()
        record, gold = load_case(CORPUS, case)
        packet = build_packet(record)
        clean = gold.get("is_hallucination") == "false"
        verdict = str(P.run_predicate("final_delivery_present", packet, {})
                      .get("verdict") or "")
        counts[("clean" if clean else "error", verdict)] += 1
        if verdict == "missing":
            hits.append((case, clean))
    print(f"[{label}] clean missing={counts[('clean', 'missing')]} "
          f"error missing={counts[('error', 'missing')]} "
          f"clean present={counts[('clean', 'present')]} "
          f"error present={counts[('error', 'present')]}")
    print("     missing: " + ", ".join(
        ("CLEAN " if c else "ERROR ") + s for s, c in hits))


def main() -> None:
    original = P._RUNNERS["final_delivery_present"]
    measure("baseline", original)
    for minimum in (20, 40):
        measure(f"envelope-stripped payload >= {minimum}",
                make_variant(minimum))
    measure("payload >= 40 with failure filter",
            make_variant(40, failure_filter=True))
    P._RUNNERS["final_delivery_present"] = original


if __name__ == "__main__":
    main()
