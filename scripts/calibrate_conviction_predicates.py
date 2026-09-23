"""Measure and record which conviction predicates may authorize a conviction.

The record is ``detector/calibration/conviction_predicates.json`` and is read at
run time by ``detector.conviction_calibration``: a built-in deterministic
predicate whose record is missing, stale or nonzero-false-positive cannot
satisfy a ``convict_mechanical_violation`` guard binding, so the case falls
through to the judge/defense path instead of being convicted.

Fully offline: this runs the predicates over ``{question, history}`` packets and
reads gold only to group the outcome. No provider, no judge, no defense, and
nothing under ``datasets/`` is written.

Usage:

    python scripts/calibrate_conviction_predicates.py            # measure, show, no write
    python scripts/calibrate_conviction_predicates.py --write     # write the record
    python scripts/calibrate_conviction_predicates.py --verify    # exit 1 if the record is stale

``--verify`` is the audit: it re-measures from the corpus and compares the
resulting record with the file on disk, field by field, so a record that no
longer describes the code or the data fails loudly instead of quietly
authorizing convictions.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector.conviction_calibration import (  # noqa: E402
    DEFAULT_ARTIFACT,
    DEFAULT_CORPUS_ROOT,
    MIN_CLEAN_CASES,
    build_artifact,
    content_digest,
)

#: Fields a re-measurement must reproduce exactly. ``generated_at_utc`` is the
#: one field that legitimately differs between two measurements of the same
#: code and data, and it is excluded from ``content_digest`` too.
_VOLATILE_FIELDS = ("generated_at_utc", "content_digest")


def _comparable(artifact: dict) -> dict:
    return {
        key: value for key, value in artifact.items()
        if key not in _VOLATILE_FIELDS
    }


def _report(artifact: dict, *, label: str) -> None:
    corpus = artifact["corpus"]
    print(f"{label}: {corpus['cases']} cases "
          f"({corpus['clean_cases']} clean, {corpus['error_cases']} error), "
          f"clean set >= {MIN_CLEAN_CASES}: "
          f"{corpus['clean_cases'] >= MIN_CLEAN_CASES}")
    print(f"  corpus fingerprint {corpus['fingerprint'][:16]}  "
          f"clean {corpus['clean_fingerprint'][:16]}")
    print(f"  {'predicate':<26} {'clean FP':>8} {'error hits':>10}  admitted")
    for name, entry in sorted(artifact["predicates"].items()):
        print(f"  {name:<26} {entry['clean_violations']:>8} "
              f"{entry['error_hits']:>10}  {entry['admitted']}")
        for case in entry["clean_violation_cases"]:
            print(f"      FALSE POSITIVE on clean case {case} "
                  f"(verdicts {entry['convicting_verdicts']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS_ROOT))
    parser.add_argument("--out", default=str(DEFAULT_ARTIFACT))
    parser.add_argument("--write", action="store_true",
                        help="write the measured record to --out")
    parser.add_argument("--verify", action="store_true",
                        help="exit nonzero unless the record on disk matches a fresh measurement")
    args = parser.parse_args()

    corpus = pathlib.Path(args.corpus)
    if not corpus.is_dir():
        raise SystemExit(f"corpus not found: {corpus}")

    measured = build_artifact(corpus)
    _report(measured, label="measured")

    out = pathlib.Path(args.out)
    if args.verify:
        if not out.is_file():
            print(f"VERIFY FAILED: no record at {out}")
            return 1
        recorded = json.loads(out.read_text(encoding="utf-8"))
        if str(recorded.get("content_digest") or "") != content_digest(recorded):
            print("VERIFY FAILED: the record was edited after it was written "
                  "(content digest mismatch)")
            return 1
        if _comparable(recorded) != _comparable(measured):
            print("VERIFY FAILED: the record does not match a fresh measurement")
            for key in sorted(set(_comparable(recorded)) | set(_comparable(measured))):
                if _comparable(recorded).get(key) != _comparable(measured).get(key):
                    print(f"  field {key!r} differs")
            return 1
        print(f"VERIFY OK: {out} describes the current code and data")
        return 0

    if args.write:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(measured, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"record -> {out}")
    refused = sorted(
        name for name, entry in measured["predicates"].items()
        if not entry["admitted"]
    )
    if refused:
        print(f"refused conviction authority: {refused}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
