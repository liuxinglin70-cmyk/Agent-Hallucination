"""跨轮读数汇总：R2–R5 每个单元、每个臂、每次重复的判定，按单元聚合。

用法：python output/rsi-planning/summarize_rounds.py > output/rsi-planning/rounds_summary.json
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PILOT = REPO / "reports/rsi_pilot_20260919"

# (label, stage_dir, expected_role_map) —— role 从 manifest 的 cases/validation_cases 读取
ROUNDS = [
    ("r2a1", "round2/frozen_recheck"),
    ("r2a2", "round2_attempt2/frozen_recheck"),
    ("r2a2-val", "round2_attempt2/unused_dev_validation"),
    ("r3", "round3/v001/results"),          # 种子复筛单次
    ("r4", "round4/frozen_recheck"),
    ("r4-val", "round4/unused_dev_validation_r4"),
    ("r5-seed", "round5/v001/results"),     # 种子复筛单次
    ("r5-conf", "round5/panel_confirm_r5"),
    ("r6-008", "round6/stable_008_r6"),
    ("r6-024", "round6/noise_024_r6"),
]

TIER = {  # 单元定级（依据历史证据，见各轮报告）
    "BFCL/002.json": "training_fix",
    "BFCL/153.json": "training_fix",
    "BFCL/008.json": "stable_regression",
    "BFCL/082.json": "stable_regression",
    "BFCL/148.json": "stable_regression",
    "BFCL/024.json": "noisy_regression",
    "BFCL/013.json": "clean_boundary",
    "BFCL/018.json": "clean_neighbor",
    "BFCL/020.json": "clean_neighbor",
    "BFCL/053.json": "clean_neighbor",
}

rows: dict[str, dict[str, list[dict]]] = {}
for label, rel in ROUNDS:
    stage = PILOT / rel
    if not stage.exists():
        continue
    for arm_dir in sorted(p for p in stage.iterdir() if p.is_dir()):
        arm = arm_dir.name
        for rep_dir in sorted(arm_dir.glob("rep*")):
            for path in sorted((rep_dir / "results").glob("*.json")):
                entry = json.loads(path.read_text(encoding="utf-8-sig"))
                cid = str(entry["case_id"])
                rows.setdefault(cid, {}).setdefault(arm, []).append({
                    "round": label,
                    "rep": rep_dir.name,
                    "state": entry.get("state"),
                    "authority": entry.get("authority"),
                    "prediction": bool(entry.get("prediction")),
                    "step": entry.get("responsible_step"),
                })
        # 单次种子复筛：results 直接在 stage 下
    for path in sorted((stage / "results").glob("*.json")) if (stage / "results").exists() else []:
        entry = json.loads(path.read_text(encoding="utf-8-sig"))
        cid = str(entry["case_id"])
        rows.setdefault(cid, {}).setdefault("v001", []).append({
            "round": label, "rep": "rep01",
            "state": entry.get("state"),
            "authority": entry.get("authority"),
            "prediction": bool(entry.get("prediction")),
            "step": entry.get("responsible_step"),
        })

summary = {}
for cid, arms in sorted(rows.items()):
    tier = TIER.get(cid, "?")
    if tier in ("training_fix", "stable_regression", "noisy_regression"):
        good = lambda e: bool(e["prediction"]) and e["authority"] == "authoritative"
    else:
        good = lambda e: (not e["prediction"]) and e["authority"] == "authoritative"
    summary[cid] = {"tier": tier, "arms": {}}
    for arm, entries in arms.items():
        ok = sum(1 for e in entries if good(e))
        summary[cid]["arms"][arm] = {
            "good": ok, "total": len(entries),
            "flips": [
                {"round": e["round"], "rep": e["rep"], "state": e["state"]}
                for e in entries if not good(e)
            ],
        }

print(json.dumps(summary, ensure_ascii=False, indent=1))
