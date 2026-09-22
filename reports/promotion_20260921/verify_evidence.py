"""Read-only re-derivation of this report's numbers from the archived evidence.

Run: python reports/promotion_20260921/verify_evidence.py
No model calls, no writes. If this fails, the report and the archive disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE / "run"


def main() -> int:
    report = json.loads((RUN / "promotion.json").read_text(encoding="utf-8-sig"))
    ctrl = json.loads((RUN / "arms" / "control.json").read_text(encoding="utf-8-sig"))
    cand = json.loads((RUN / "arms" / "candidate.json").read_text(encoding="utf-8-sig"))
    protocol = json.loads(
        (HERE / "protocol" / "promotion-protocol-real.json").read_text(
            encoding="utf-8-sig",
        )
    )
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    check("terminal is promotion_unmeasurable",
          report["state"] == "promotion_unmeasurable", report["state"])
    check("admission did not happen", report["admitted"] is False)
    check("target_kind is detection", report["target_kind"] == "detection")
    check("both arms carry 20 rows",
          len(ctrl["rows"]) == 20 and len(cand["rows"]) == 20)

    det = report["benefit"]["detection"]
    check("marginal_cases == -1", det["marginal_cases"] == -1, str(det["marginal_cases"]))
    check("new_errors == 1", det["new_errors"] == 1, str(det["new_errors"]))
    check("tp_lost == 1", det["tp_lost"] == 1, str(det["tp_lost"]))
    check("no new false positives", det["new_fp"] == 0, str(det["new_fp"]))

    opp = report["opportunity"]
    check("recall opportunity == 6", opp["detection"]["recall"] == 6,
          str(opp["detection"]["recall"]))
    check("precision opportunity == 0", opp["detection"]["precision"] == 0)
    check("localization improvable == 1",
          opp["localization"]["improvable"] == 1)

    cov = report["gates"]["harmlessness"]["coverage"]
    check("protected_tp == 0 (the batch defect)",
          cov["protected_tp"] == 0, str(cov["protected_tp"]))
    check("protected_tn == 4", cov["protected_tn"] == 4, str(cov["protected_tn"]))
    # Gold is read from the control arm rows; the protocol frozen for this run
    # predates the protocol-level gold field added while fixing the batch defect.
    gold = {r["case"]: r["gold"] for r in ctrl["rows"]}
    roles = protocol["roles"]
    check("protection set is all gold-negative (the batch defect)",
          all(gold[c] is False for c, r in roles.items() if r == "protection"))

    # The three differing cases, re-derived from the two arms.
    cb = {r["case"]: r for r in ctrl["rows"]}
    nb = {r["case"]: r for r in cand["rows"]}
    diffs = {c: (cb[c]["prediction"], nb[c]["prediction"])
             for c in cb if cb[c]["prediction"] != nb[c]["prediction"]}
    expected = {
        "BFCL/001.json": (True, False),
        "BFCL/009.json": (True, False),
        "BFCL/032.json": (True, False),
    }
    check("exactly three differing cases with the reported directions",
          diffs == expected, json.dumps(diffs, sort_keys=True))
    check("BFCL/001 and BFCL/009 are protection, gold-negative, control FP",
          all(roles[c] == "protection" and cb[c]["gold"] is False
              and cb[c]["prediction"] is True for c in ("BFCL/001.json", "BFCL/009.json")))
    check("BFCL/032 is benefit, gold-positive, control TP",
          roles["BFCL/032.json"] == "benefit"
          and cb["BFCL/032.json"]["gold"] is True
          and cb["BFCL/032.json"]["prediction"] is True)
    check("both arms are authoritative on the differing cases",
          all(cb[c]["authority"] == "authoritative"
              and nb[c]["authority"] == "authoritative" for c in expected))

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
              + (f"  ({detail})" if detail and not ok else ""))
    print()
    if failed:
        print(f"FAIL: {len(failed)} check(s) disagree with the report")
        return 1
    print(f"PASS: all {len(checks)} checks reproduce the report's numbers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
