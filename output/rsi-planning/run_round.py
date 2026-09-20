"""RSI 研究轮驱动：screening（草拟或种子）→ manifest 声明的冻结阶段序列。

从仓库根目录运行：
    python output/rsi-planning/run_round.py \
        --manifest output/rsi-planning/research-manifest-round3.json \
        --output-dir reports/rsi_pilot_20260919/round3
    python output/rsi-planning/run_round.py --dry-run --manifest <manifest>   # 离线演练

阶段与门在冻结 manifest 中预先声明（stages + panel_gate）；任一阶段未过即
停止并报告原因。各阶段结果可按 identity 断点续跑（research.py 自带）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from detector.env import load_local_env  # noqa: E402
from evolution.research import run_frozen_repeats, run_research  # noqa: E402

DEFAULT_MANIFEST = REPO / "output/rsi-planning/research-manifest-round3.json"
DEFAULT_OUTPUT = REPO / "reports/rsi_pilot_20260919/round3"


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
            "intervention_id": "IV-R-FAKE",
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
    ids = [str(item["case_id"]) for item in manifest.get("validation_cases") or []]
    if not ids:
        raise SystemExit("manifest has no validation_cases")
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


def _candidate_arm_passed(stage_dir: Path) -> bool | None:
    """严格读：候选臂每次重复都须通过（训练修复 + 全部回归保持 + clean）。"""
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


def _fisher_degradation_p(cand_kept: int, cand_total: int,
                          h0_kept: int, h0_total: int) -> float:
    """单侧 Fisher 精确检验：候选保持率显著低于 H0 的 p 值。

    边缘固定的 2x2 表下，P(X=k) = C(K, k)*C(N-K, n-k)/C(N, n)，其中
    N 为两臂总测量数，K 为总保持数，n 为候选测量数；单侧 p = sum_{k<=cand_kept}。
    """
    from math import comb

    n = cand_total
    total = cand_total + h0_total
    kept = cand_kept + h0_kept
    if total == 0 or kept > total:
        return 1.0
    denom = comb(total, n)
    if denom == 0:
        return 1.0
    p = 0.0
    for k in range(0, min(cand_kept, kept) + 1):
        if n - k > total - kept:
            continue
        p += comb(kept, k) * comb(total - kept, n - k) / denom
    return min(1.0, p)


def _panel_gate_passed(stage_dir: Path, spec: dict) -> bool | None:
    """预注册面板读：训练/clean/稳定回归零容忍，噪声案例按预注册规则；逐例照报。"""
    report = json.loads(
        (stage_dir / "report.json").read_text(encoding="utf-8-sig"),
    )
    arms = report.get("arms") or {}
    candidate_arms = {k: v for k, v in arms.items() if k != "h0"}
    if not candidate_arms:
        return None
    training = [str(c) for c in spec.get("training_cases") or []]
    clean = [str(c) for c in spec.get("clean_cases") or []]
    regression = [str(c) for c in spec.get("regression_cases") or []]
    for arm in candidate_arms.values():
        if not (arm.get("complete") and arm.get("per_repetition")):
            return False
        for pv in arm["per_repetition"]:
            training_rows = pv.get("training") or {}
            for cid in training:
                row = training_rows.get(cid)
                if row is None or not row.get("repaired"):
                    return False
            neighbors = pv.get("neighbors") or {}
            for cid in clean:
                row = neighbors.get(cid)
                if row is None or not row.get("clean"):
                    return False
            for cid in regression:
                row = neighbors.get(cid)
                if row is None or not row.get("kept_positive"):
                    return False

    def _kept(arm: dict, cid: str) -> int | None:
        reps = arm.get("per_repetition") or []
        if not reps:
            return None
        return sum(
            1 for pv in reps
            if ((pv.get("neighbors") or {}).get(cid) or {}).get("kept_positive")
        )

    h0_arm = arms.get("h0") or {}
    h0_reps = len(h0_arm.get("per_repetition") or [])
    for item in spec.get("noisy_cases") or []:
        cid = str(item.get("case_id"))
        rule = str(item.get("rule") or "dominance")
        for arm in candidate_arms.values():
            cand_kept = _kept(arm, cid)
            h0_kept = _kept(h0_arm, cid)
            cand_reps = len(arm.get("per_repetition") or [])
            if cand_kept is None or h0_kept is None:
                return False
            if rule == "no_significant_degradation":
                # 单侧 Fisher：只有在候选保持率显著低于 H0 时才判定退化。
                if _fisher_degradation_p(
                    cand_kept, cand_reps, h0_kept, h0_reps
                ) < 0.05:
                    return False
            else:
                tolerance = int(item.get("tolerance") or 0)
                if cand_kept < h0_kept - tolerance:
                    return False
    return True


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
        run_dir = Path(tempfile.mkdtemp(prefix="rsi_round_dry_")) / "round"
        backend = _FakeBackend(target_steps={
            str(item["case_id"]): item["target_step"]
            for item in manifest["cases"] if item.get("target_step")
        })
        proposer = _FakeProposer()
        if manifest.get("seed_candidate"):
            # 种子源暂存：把真实源版本记录拷进临时源运行目录，演练哈希钉住。
            source = REPO / manifest["seed_candidate"]["source_run"]
            staged = run_dir.parent / "staged_source"
            version = manifest["seed_candidate"]["version"]
            (staged / version).mkdir(parents=True, exist_ok=True)
            shutil.copy(
                source / version / "candidate.json",
                staged / version / "candidate.json",
            )
            manifest = dict(manifest)
            manifest["seed_candidate"] = dict(manifest["seed_candidate"])
            manifest["seed_candidate"]["source_run"] = str(staged)
            manifest_path = run_dir.parent / "dry_manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
            )
            args = argparse.Namespace(**{**vars(args), "manifest": str(manifest_path)})
        print(json.dumps({"dry_run": True, "run_dir": str(run_dir)}, ensure_ascii=False))
    else:
        from evolution.backends import LiveDetectorBackend
        from evolution.proposer import LLMProposer

        run_dir = Path(args.output_dir)
        backend = LiveDetectorBackend(dict(manifest["framework_config"]))
        proposer = LLMProposer(dict(manifest["framework_config"]))

    run_dir.mkdir(parents=True, exist_ok=True)
    stage_log: dict = {"run_id": manifest["run_id"], "run_dir": str(run_dir)}

    # ---- stage 1: screening（草拟或种子；消费/不消费 proposer 预算）------
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

    # ---- stage 2..n: manifest 声明的冻结阶段 ------------------------------
    for index, (stage_name, cfg) in enumerate(
        (manifest.get("stages") or {}).items()
    ):
        explicit = cfg.get("case_ids")
        if explicit:
            case_ids = [str(c) for c in explicit]
        elif cfg.get("case_set") == "screening":
            case_ids = _screening_ids(manifest)
        else:
            case_ids = _validation_ids(manifest)
        run_frozen_repeats(
            args.manifest, run_dir, backend,
            stage=stage_name,
            case_ids=case_ids,
            case_meta=_case_meta(manifest, case_ids),
            repetitions=int(cfg["repetitions"]),
            budget_case_evaluations=int(cfg["budget_case_evaluations"]),
            workers=int(manifest["budget"]["workers"]),
        )
        stage_dir = run_dir / stage_name
        if cfg.get("gate") == "panel":
            panel_spec = dict(cfg.get("panel") or manifest.get("panel_gate") or {})
            passed = _panel_gate_passed(stage_dir, panel_spec)
        else:
            passed = _candidate_arm_passed(stage_dir)
        stage_log[stage_name] = {
            "gate": cfg.get("gate") or "candidate_all_reps",
            "candidate_passed": passed,
        }
        print(json.dumps(
            {"stage": stage_name, **stage_log[stage_name]},
            ensure_ascii=False,
        ))
        if passed is not True:
            stage_log["verdict"] = f"{stage_name}_failed"
            print(json.dumps(stage_log, ensure_ascii=False))
            return 3 + index

    stage_log["verdict"] = "initial_validation_passed"
    print(json.dumps(stage_log, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
