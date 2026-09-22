"""Offline calibration audit of the judge's stated confidence.

Reads archived evaluation rows -- the ``result.proof.judge.confidence`` the
detector actually recorded -- and reports a discrete reliability table per
level and per source. No provider call, no dataset read, no detector run.

The point is to answer one question honestly: does the confidence level carry
information? The report says so (or does not), and it says explicitly which
metrics could not be computed and why, rather than omitting them silently.

Usage:

    python scripts/audit_calibration.py                    # default archive
    python scripts/audit_calibration.py --rows path.jsonl  # explicit rows
    python scripts/audit_calibration.py --out report.json

Rows can be given as JSON lines or as a JSON list. Each row needs the shape
the evolution side already uses: ``case``, ``gold``, ``prediction``,
``authority`` and ``result.proof.judge.confidence``; anything else is ignored.

The audit is `calibration-only` (plan §19 rule 12): it never scores a
candidate, admits a version or relaxes a gate.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.calibration import calibration_report  # noqa: E402

DEFAULT_ARCHIVE = ROOT / "reports" / "error_scope_20260918" / "attempts"
DEFAULT_OUT = ROOT / "reports" / "calibration_audit.json"


def rows_from_archive(archive: pathlib.Path) -> tuple[list[dict], dict[str, str]]:
    """One row per case from an archived run tree, first occurrence wins.

    The archive holds the same case under several arms; a case counted once
    per arm would weight a case by how many arms happened to run it.
    """
    rows: dict[str, dict] = {}
    origin: dict[str, str] = {}
    for arm in sorted(archive.glob("*/run/checkpoints/cycle*/arms/*")):
        for path in sorted(arm.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            row = payload.get("row") or {}
            case = str(row.get("case") or "")
            if case and case not in rows:
                rows[case] = row
                origin[case] = path.relative_to(ROOT).as_posix()
    return list(rows.values()), origin


def rows_from_file(path: pathlib.Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise SystemExit(f"{path}: expected a JSON list of rows")
        return [dict(item) for item in payload if isinstance(item, dict)]
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows",
        help="JSON or JSONL file of evaluation rows; defaults to the archived runs",
    )
    parser.add_argument(
        "--archive",
        default=str(DEFAULT_ARCHIVE),
        help="archived run tree to read rows from when --rows is not given",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to write the report")
    args = parser.parse_args()

    if args.rows:
        rows = rows_from_file(pathlib.Path(args.rows))
        origin: dict[str, str] = {}
        source = str(pathlib.Path(args.rows))
    else:
        archive = pathlib.Path(args.archive)
        if not archive.is_dir():
            raise SystemExit(f"archive not found: {archive}")
        rows, origin = rows_from_archive(archive)
        source = str(archive.relative_to(ROOT)) if archive.is_relative_to(ROOT) else str(archive)

    if not rows:
        raise SystemExit(f"no evaluation rows found in {source}")

    report = calibration_report(rows)
    report["row_source"] = source
    report["source_files"] = origin

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"rows: {report['rows']}")
    print(f"levels emitted by the runtime: {report['confidence_levels']}")
    for entry in report["overall"]["levels"]:
        coverage = entry["coverage"]
        accuracy = entry["accuracy"]
        print(
            f"  {entry['level']:>8}  n={entry['n']:<4} correct={entry['correct']:<4} "
            f"wrong={entry['wrong']:<4} abstained={entry['abstained']:<4} "
            f"accuracy={accuracy if accuracy is not None else 'n/a'} "
            f"coverage={coverage if coverage is not None else 'n/a'}"
        )
    print(f"unstated-confidence rows: {report['overall']['unstated_level_rows']}")
    print(f"monotone: {report['monotonicity']['monotone']}")
    for violation in report["monotonicity"]["violations"]:
        print(f"  ordering violation: {violation}")
    print(f"sources: {report['per_source']['source_count']}")
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
