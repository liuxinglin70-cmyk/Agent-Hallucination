"""Offline sweep of the conviction predicates over the AgentHallu corpus.

Pure predicate evaluation: builds packets from ``{question, history}`` and calls
``detector.predicates.run_predicate``. No provider, no judge, no network, no
dataset writes. The gold label is read only to *group* the result rows; gold
never enters a packet (``detector.packet.build_packet`` rejects it).

Usage:

    python scripts/predicate_corpus_probe.py                 # summary + hits
    python scripts/predicate_corpus_probe.py --out report.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector.dataset import gold_fields, load_case, project_record  # noqa: E402
from detector.packet import build_packet  # noqa: E402
from detector.predicates import (  # noqa: E402
    PREDICATE_CONVICTION_VERDICTS,
    run_predicate,
)

DEFAULT_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"


def iter_cases(root: pathlib.Path):
    for path in sorted(root.rglob("*.json")):
        case_id = path.relative_to(root).as_posix()
        try:
            record, gold = load_case(root, case_id)
        except Exception as exc:  # pragma: no cover - corpus shape guard
            yield case_id, None, None, f"{type(exc).__name__}: {exc}"
            continue
        yield case_id, project_record(record), gold, ""


def sweep(root: pathlib.Path) -> dict:
    rows = []
    for case_id, record, gold, error in iter_cases(root):
        if record is None:
            rows.append({"case": case_id, "gold": None, "error": error})
            continue
        try:
            packet = build_packet(record)
        except Exception as exc:
            rows.append({"case": case_id, "gold": None, "error": f"{type(exc).__name__}: {exc}"})
            continue
        verdicts = {}
        for name in PREDICATE_CONVICTION_VERDICTS:
            verdicts[name] = run_predicate(name, packet, {})
        rows.append({
            "case": case_id,
            "gold": {
                **gold_fields(gold),
                # The corpus spells the label as the string "true"/"false".
                "gold_clean": _is_clean(gold.get("is_hallucination")),
            },
            "verdicts": verdicts,
        })
    return {"rows": rows}


def _is_clean(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in {"false", "clean", "no", "0"}:
        return True
    if text in {"true", "1"}:
        return False
    return None


def summarize(report: dict) -> dict:
    rows = report["rows"]
    clean = [r for r in rows if (r.get("gold") or {}).get("gold_clean") is True]
    error = [r for r in rows if (r.get("gold") or {}).get("gold_clean") is False]
    unlabelled = [
        r for r in rows if (r.get("gold") or {}).get("gold_clean") is None
    ]
    out = {
        "cases": len(rows),
        "clean": len(clean),
        "error": len(error),
        "unlabelled_or_failed": len(unlabelled),
        "predicates": {},
    }
    for name, convicting in PREDICATE_CONVICTION_VERDICTS.items():
        entry: dict = {"convicting_verdicts": sorted(convicting)}
        for label, group in (("clean", clean), ("error", error)):
            counts = Counter(
                str((row.get("verdicts") or {}).get(name, {}).get("verdict") or "?")
                for row in group
            )
            hits = sorted(
                row["case"] for row in group
                if str((row.get("verdicts") or {}).get(name, {}).get("verdict") or "")
                in convicting
            )
            entry[label] = {
                "verdict_counts": dict(sorted(counts.items())),
                "convicting_hits": len(hits),
                "convicting_cases": hits,
            }
        out["predicates"][name] = entry
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--out", help="write the per-case sweep as JSON")
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"corpus not found: {root}")

    report = sweep(root)
    summary = summarize(report)
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"per-case sweep -> {path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
