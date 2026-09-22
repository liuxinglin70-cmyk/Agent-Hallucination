"""Rebuild failure mechanisms over the archived evaluated rows (read-only).

This is the E0 dry-run of plan §16: reconstruct mechanisms from history without
touching detector behaviour, so the grouping can be spot-checked by a human
before any code depends on it.

It reads only archived rows. Gold labels are copied into the report for the
spot-check and never enter a mechanism key (``evolution/mechanisms.py`` does not
read them). Cases whose mechanical profile group did not survive the upstream
split are listed under ``excluded`` so the patrol's failure count stays
reconstructible.

Run from the repository root:

    python reports/mechanism_20260922/rebuild_mechanisms.py
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evolution.config import load_config  # noqa: E402
from evolution.splits import load_gold_step  # noqa: E402
from evolution.worksheet import (  # noqa: E402
    diagnose_failure_families,
    excluded_failure_cases,
    mechanism_subgroups,
    refine_failure_families,
    stable_failure_families,
    stable_localization_families,
    worksheet_report,
)

ARCHIVE = REPO / "reports" / "error_scope_20260918" / "attempts"
OUT = pathlib.Path(__file__).resolve().parent / "mechanism_dryrun.json"


def load_rows() -> tuple[list[dict], dict[str, str]]:
    """One row per case, plus the archive file each came from."""
    rows: dict[str, dict] = {}
    origin: dict[str, str] = {}
    for arm in sorted(ARCHIVE.glob("*/run/checkpoints/cycle*/arms/*")):
        for path in sorted(arm.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            row = payload.get("row") or {}
            case = str(row.get("case") or "")
            if case and case not in rows:
                rows[case] = row
                origin[case] = path.relative_to(REPO).as_posix()
    return list(rows.values()), origin


def main() -> int:
    config = load_config(str(REPO / "evolution" / "configs" / "development.json"))
    dataset_root = config["dataset_root"]
    rows, origin = load_rows()
    by_case = {str(row.get("case") or ""): row for row in rows}
    gold_steps = {}
    for case in by_case:
        try:
            step = load_gold_step(dataset_root, case)
        except Exception:
            step = None
        if step is not None:
            gold_steps[case] = step

    coarse = [
        *stable_failure_families(rows),
        *stable_localization_families(
            rows, gold_steps=gold_steps, records={},
        ),
    ]
    diagnosed = diagnose_failure_families(
        coarse, rows, dataset_root=dataset_root,
    )
    refined = refine_failure_families(diagnosed)
    excluded = excluded_failure_cases(coarse, refined)
    subgroups, unrepresented = mechanism_subgroups(refined)
    report = worksheet_report(
        [], subgroups, unrepresented=unrepresented, excluded=excluded,
    )

    mechanism_cases: dict[str, dict] = {}
    for subgroup in subgroups:
        mechanism = subgroup["mechanism"]
        entry = mechanism_cases.setdefault(str(mechanism["mechanism_id"]), {
            "status": mechanism["status"],
            "task_obligation": mechanism["task_obligation"]["kind"],
            "observed_deviation": mechanism["observed_deviation"]["kind"],
            "repair_operation": mechanism["repair_hypothesis"]["operation"],
            "detector_failure": mechanism["detector_failure"]["stage"]
            + "/" + mechanism["detector_failure"]["failure_mode"],
            "members": [],
        })
        for case in subgroup["failure_cases"]:
            row = by_case.get(case, {})
            entry["members"].append({
                "case": case,
                "source": case.split("/")[0],
                "gold": row.get("gold"),
                "prediction": row.get("prediction"),
                "archived_row": origin.get(case),
            })

    proposable = sum(subgroup["count"] for subgroup in subgroups)
    unrepresentable = sum(item["case_count"] for item in unrepresented)
    excluded_cases = sum(item["excluded_count"] for item in excluded)
    payload = {
        "schema_version": "evolution.mechanism_dryrun.v1",
        "evidence_grade": "diagnostic-only",
        "note": (
            "Mechanism reconstruction over archived evaluated rows; no detector "
            "call and no detector behaviour change. Gold is carried for human "
            "review only and never enters a mechanism key."
        ),
        "source_archive": ARCHIVE.relative_to(REPO).as_posix(),
        "coarse_failure_cases": sum(family["count"] for family in coarse),
        "proposable_cases": proposable,
        "unrepresented_cases": unrepresentable,
        "excluded_cases": excluded_cases,
        "accounting_closed": (
            proposable + unrepresentable + excluded_cases
            == sum(family["count"] for family in coarse)
        ),
        "coarse_families": [
            {
                "failure_family": family["failure_family"],
                "count": family["count"],
                "failure_cases": family["failure_cases"],
                "sources": family["sources"],
            }
            for family in coarse
        ],
        "mechanisms": report["mechanisms"]["mechanisms"],
        "unrepresented": report["mechanisms"]["unrepresented"],
        "excluded": report["mechanisms"]["excluded"],
        "mechanism_cases": mechanism_cases,
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "out": OUT.relative_to(REPO).as_posix(),
        "coarse_failure_cases": payload["coarse_failure_cases"],
        "mechanisms": len(payload["mechanisms"]),
        "proposable_cases": proposable,
        "unrepresented_cases": unrepresentable,
        "excluded_cases": excluded_cases,
        "accounting_closed": payload["accounting_closed"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
