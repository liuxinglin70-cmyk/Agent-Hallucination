"""打印 P2 未用开发验证的完整判定矩阵（分析辅助，不属于运行代码）。"""
import json
import pathlib

ROOT = pathlib.Path("reports/rsi_pilot_20260919/screening/unused_dev_validation")
CASES = ["BFCL/008.json", "BFCL/013.json", "BFCL/024.json"]
ARMS = ["h0", "v002", "v005"]
REPS = ["rep01", "rep02"]

for case in CASES:
    print("=====", case)
    for arm in ARMS:
        for rep in REPS:
            p = ROOT / arm / rep / "results" / case.replace("/", "__")
            if not p.exists():
                print(f"  {arm}/{rep}: MISSING")
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            print(f"  {arm}/{rep}: {d['state']} ({d['authority']}) step={d.get('responsible_step')}")
