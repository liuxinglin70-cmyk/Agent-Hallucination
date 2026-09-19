"""负向演练：候选把 clean 案例也翻转时，筛查门必须拦住（对应 v002@013 的 FP 形态）。"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "run_round2", Path(__file__).resolve().parent / "run_round2.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class OverEagerBackend(mod._FakeBackend):
    """有效候选把所有案例都判成 error（含 gold=false）——必须被筛查门拒绝。"""

    def evaluate_fresh(self, cases, **kwargs):
        rows = super().evaluate_fresh(cases, **kwargs)
        directive = ""
        for entry in (kwargs.get("registry") or {}).get("interventions") or []:
            directive += str((entry.get("body") or {}).get("directive") or "")
        if "quote" in directive.lower():
            for row in rows:
                row["prediction"] = True
                row["state"] = "verified_error"
                row["result"]["state"] = "verified_error"
                row["result"]["responsible_step"] = 1
        return rows


def main() -> int:
    manifest = json.loads(mod.DEFAULT_MANIFEST.read_text(encoding="utf-8-sig"))
    run_dir = Path(tempfile.mkdtemp(prefix="rsi_round2_neg_")) / "round2"
    run_dir.mkdir(parents=True)
    backend = OverEagerBackend(target_steps={
        str(i["case_id"]): i["target_step"]
        for i in manifest["cases"] if i.get("target_step")
    })
    screening = mod.run_research(
        str(mod.DEFAULT_MANIFEST), run_dir, backend, mod._FakeProposer(),
    )
    selection = json.loads((run_dir / "selection.json").read_text(encoding="utf-8-sig"))
    print("selected:", selection["routes"]["C"]["selected"])
    print("versions:", [
        {"id": v["version_id"], "passed": (v.get("verdict") or {}).get("passed"),
         "reasons": (v.get("verdict") or {}).get("reasons")}
        for v in screening["versions"]
    ])
    if selection["routes"]["C"]["selected"]:
        print("FAIL: over-eager candidate passed screening")
        return 1
    print("OK: over-eager candidate blocked at screening")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
