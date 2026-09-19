"""第 2 轮（v006）研究驱动：screening → frozen_recheck → unused_dev_validation。

从仓库根目录运行：
    python output/rsi-planning/run_round2.py             # 真实运行（调用模型服务）
    python output/rsi-planning/run_round2.py --dry-run   # 离线 mock 演练（写临时目录）

阶段门在冻结 manifest（output/rsi-planning/research-manifest-round2.json）中预先声明；
任一阶段未过即停止并报告原因。各阶段结果可按 identity 断点续跑（research.py 自带）。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from detector.env import load_local_env  # noqa: E402
from evolution.research import run_frozen_repeats, run_research  # noqa: E402

DEFAULT_MANIFEST = REPO / "output/rsi-planning/research-manifest-round2.json"
DEFAULT_OUTPUT = REPO / "reports/rsi_pilot_20260919/round2"


# ---------------------------------------------------------------------------
# --dry-run 用的最小 fake：只验证驱动编排与阶段门，不验证候选内容。
class _FakeBackend:
    """裸底座漏判所有 gold=true；候选指令含 'quote' 时精确翻转这些案例。"""

    name = "fake"

    def __init__(self, target_steps: dict | None = None) -> None:
        self.target_steps = target_steps or {}
        self.calls: list[dict] = []

    def evaluate_fresh(self, cases, *, dataset_root, registry=None,
                       gold_map=None, workers=1, deadline=None):
        self.calls.append({"cases": list(cases)})
        directive = ""
        for entry in (registry or {}).get("interventions") or []:
            directive += str((entry.get("body") or {}).get("directive") or "")
        effective = "quote" in directive.lower()
        rows = []
        for case_id in cases:
            gold = bool((gold_map or {}).get(case_id))
            hit = effective and gold
            rows.append({
                "case": case_id,
                "gold": gold,
                "prediction": hit,
                "state": "verified_error" if hit else "verified_clean",
                "authority": "authoritative",
                "result": {
                    "case_id": case_id,
                    "state": "verified_error" if hit else "verified_clean",
                    "decision_authority": "authoritative",
                    "runtime_valid": True,
                    "responsible_step": self.target_steps.get(case_id) if hit else None,
                },
                "backend": self.name,
            })
        return rows


class _FakeProposer:
    name = "fake"

    def __init__(self) -> None:
        self.received: list[tuple] = []

    def set_memory_context(self, context):
        self.received.append(("memory", context))

    def set_rejection_feedback(self, feedback):
        self.received.append(("feedback", feedback))

    def set_research_packet(self, packet):
        self.received.append(("packet", packet))

    def propose(self, families, *, cycle, max_proposals=3):
        self.received.append(("families", families, cycle))
        from evolution.proposer import ProposalBatch

        candidate = {
            "type": "text_boundary",
            "hook": "judge.system",
            "intervention_id": "IV-R2-FAKE",
            "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
            "body": {
                "directive": "Bind quoted content to delivered arguments (fake dry-run directive).",
                "not_when": "Do not apply when the task does not quote exact content.",
            },
            "claim": {
                "expected_behavior": "Fake expected behavior for dry-run.",
                "target_polarity": "recall",
            },
            "provenance": {
                "failure_family": "FAM-fake-dry-run",
                "cycle": 1,
                "failure_cases": ["dryA/001.json", "dryB/002.json"],
            },
            "schema_version": "detector.intervention.v1",
        }
        return ProposalBatch(candidates=[candidate], notes=[])


# ---------------------------------------------------------------------------
def _case_meta(manifest: dict, case_ids: list[str]) -> dict:
    meta: dict[str, dict] = {}
    for item in manifest["cases"]:
        meta[str(item["case_id"])] = {
            "raw_gold": bool(item["raw_gold"]),
            "target_step": item.get("target_step"),
            "role": str(item["role"]),
        }
    for item in manifest.get("validation_cases") or []:
        meta.setdefault(
            str(item["case_id"]),
            {
                "raw_gold": bool(item["raw_gold"]),
                "target_step": item.get("target_step"),
                "role": str(item["role"]),
            },
        )
    missing = [c for c in case_ids if c not in meta]
    if missing:
        raise SystemExit(f"case_meta missing for {missing}")
    return {c: meta[c] for c in case_ids}


def _screening_ids(manifest: dict) -> list[str]:
    return [str(item["case_id"]) for item in manifest["cases"]]


def _validation_ids(manifest: dict) -> list[str]:
    ids = [str(item["case_id"]) for item in manifest["validation_cases"]]
    if not ids:
        raise SystemExit("manifest has no validation_cases; validation not frozen")
    return ids


def _selected_candidate(run_dir: Path) -> str | None:
    selection = json.loads(
        (run_dir / "selection.json").read_text(encoding="utf-8-sig"),
    )
    selected = [
        str(info.get("selected"))
        for info in (selection.get("routes") or {}).values()
        if info.get("selected")
    ]
    return selected[0] if selected else None


def _gate_arm_passed(stage_dir: Path) -> bool | None:
    report = json.loads(
        (stage_dir / "report.json").read_text(encoding="utf-8-sig"),
    )
    arms = report.get("arms") or {}
    candidate_arms = [a for name, a in arms.items() if name != "h0"]
    if not candidate_arms:
        return None
    return all(
        a.get("passed") is True and a.get("complete") for a in candidate_arms
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--dry-run", action="store_true",
        help="离线演练：FakeBackend/FakeProposer，输出写到临时目录",
    )
    args = parser.parse_args()

    load_local_env()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))

    if args.dry_run:
        run_dir = Path(tempfile.mkdtemp(prefix="rsi_round2_dry_")) / "round2"
        backend = _FakeBackend(target_steps={
            str(item["case_id"]): item["target_step"]
            for item in manifest["cases"] if item.get("target_step")
        })
        proposer = _FakeProposer()
        print(json.dumps({"dry_run": True, "run_dir": str(run_dir)}, ensure_ascii=False))
    else:
        from evolution.backends import LiveDetectorBackend
        from evolution.proposer import LLMProposer

        run_dir = Path(args.output_dir)
        backend = LiveDetectorBackend(dict(manifest["framework_config"]))
        proposer = LLMProposer(dict(manifest["framework_config"]))

    run_dir.mkdir(parents=True, exist_ok=True)
    stage_log: dict = {"run_id": manifest["run_id"], "run_dir": str(run_dir)}

    # ---- stage 1: screening（含草拟/修订，消费 proposer 预算）------------
    screening = run_research(args.manifest, run_dir, backend, proposer)
    stage_log["screening"] = {
        "status": screening.get("status"),
        "stopped": screening.get("stopped"),
        "selected": _selected_candidate(run_dir),
        "budget": screening.get("budget"),
    }
    print(json.dumps({"stage": "screening", **stage_log["screening"]}, ensure_ascii=False))
    if not stage_log["screening"]["selected"]:
        stage_log["verdict"] = "screening_failed_no_candidate"
        print(json.dumps(stage_log, ensure_ascii=False))
        return 2

    # ---- stage 2: frozen paired recheck（3 次重复，训练集）--------------
    stage_cfg = manifest["stages"]["frozen_recheck"]
    recheck = run_frozen_repeats(
        args.manifest, run_dir, backend,
        stage="frozen_recheck",
        case_ids=_screening_ids(manifest),
        case_meta=_case_meta(manifest, _screening_ids(manifest)),
        repetitions=int(stage_cfg["repetitions"]),
        budget_case_evaluations=int(stage_cfg["budget_case_evaluations"]),
        workers=int(manifest["budget"]["workers"]),
    )
    recheck_passed = _gate_arm_passed(run_dir / "frozen_recheck")
    stage_log["frozen_recheck"] = {
        "status": recheck.get("status"), "stopped": recheck.get("stopped"),
        "candidate_passed": recheck_passed,
    }
    print(json.dumps(
        {"stage": "frozen_recheck", **stage_log["frozen_recheck"]},
        ensure_ascii=False,
    ))
    if recheck_passed is not True:
        stage_log["verdict"] = "frozen_recheck_failed"
        print(json.dumps(stage_log, ensure_ascii=False))
        return 3

    # ---- stage 3: candidate-unused development validation（3 次重复）----
    vcfg = manifest["stages"]["unused_dev_validation"]
    validation = run_frozen_repeats(
        args.manifest, run_dir, backend,
        stage="unused_dev_validation",
        case_ids=_validation_ids(manifest),
        case_meta=_case_meta(manifest, _validation_ids(manifest)),
        repetitions=int(vcfg["repetitions"]),
        budget_case_evaluations=int(vcfg["budget_case_evaluations"]),
        workers=int(manifest["budget"]["workers"]),
    )
    validation_passed = _gate_arm_passed(run_dir / "unused_dev_validation")
    stage_log["unused_dev_validation"] = {
        "status": validation.get("status"), "stopped": validation.get("stopped"),
        "candidate_passed": validation_passed,
    }
    print(json.dumps(
        {"stage": "unused_dev_validation", **stage_log["unused_dev_validation"]},
        ensure_ascii=False,
    ))
    stage_log["verdict"] = (
        "initial_validation_passed" if validation_passed is True
        else "unused_dev_validation_failed"
    )
    print(json.dumps(stage_log, ensure_ascii=False))
    return 0 if validation_passed is True else 4


if __name__ == "__main__":
    raise SystemExit(main())
