"""Offline probe: which delivery-side conditions separate the missing-verdict cases."""
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from detector.dataset import load_case  # noqa: E402
from detector.packet import build_packet, delivered_result  # noqa: E402

CORPUS = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
WRAPPER = re.compile(r"^Observed output of cmd `[^`]*` executed:")
STATUS_LINE = "The interaction has been completed with status: success"
NEGATIVE = ('"success": false', '"success":false', "'success': False")


def body(obs: str) -> str:
    text = WRAPPER.sub("", obs).strip()
    return text.replace(STATUS_LINE, "").strip()


def main() -> None:
    stat = Counter()
    rows = []
    for path in sorted(CORPUS.rglob("*.json")):
        case = path.relative_to(CORPUS).as_posix()
        record, gold = load_case(CORPUS, case)
        packet = build_packet(record)
        clean = gold.get("is_hallucination") == "false"
        interactions = [
            (i.get("tool_name"), str(i.get("arguments") or ""),
             str(i.get("observation") or ""))
            for e in packet["events"] for i in e["tool_interactions"]
        ]
        success = [
            item for item in interactions
            if "success" in (item[1] + item[2]).lower()
        ]
        if not success:
            continue
        stat["terminal_cases"] += 1
        if all(any(flag in item[1] for flag in NEGATIVE) for item in success):
            stat["terminal_only_from_negative"] += 1
        best = max((len(body(item[2])) for item in success), default=0)
        anchors = delivered_result(packet)
        has_prose = bool(anchors.get("last_prose_event_id"))
        answer = anchors.get("last_answer_tool_call") or {}
        has_answer_tool = bool(answer) and not any(
            marker in str(answer.get("tool_name") or "").lower()
            for marker in ("terminate", "finish", "success")
        )
        if not has_prose and not has_answer_tool:
            rows.append((case, clean, best))
    print(dict(stat))
    print("missing-verdict cases:", len(rows),
          "clean", sum(1 for r in rows if r[1]),
          "error", sum(1 for r in rows if not r[1]))
    for case, clean, best in rows:
        print(f"   {'CLEAN' if clean else 'ERROR'} {case} max_observation_body={best}")


if __name__ == "__main__":
    main()
