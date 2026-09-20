"""研究轮驱动的阶段门：Fisher 非劣性判据与面板规则（离线，无模型调用）。

驱动脚本位于 ``output/rsi-planning/run_round.py``（与冻结 manifest 同目录，
使一轮研究的所有输入物自包含）；这里按路径加载它，锁定门逻辑本身。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "output" / "rsi-planning" / "run_round.py"


def _driver():
    spec = importlib.util.spec_from_file_location("research_round", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fisher_degradation_p_known_values():
    fisher = _driver()._fisher_degradation_p
    # Identical arms: no evidence of degradation.
    assert fisher(8, 8, 8, 8) == pytest.approx(1.0)
    # One flip below a perfect H0 in 8 vs 8 is far from significant.
    assert fisher(7, 8, 8, 8) == pytest.approx(0.5)
    # 5/8 vs 8/8: still above the 0.05 threshold.
    assert fisher(5, 8, 8, 8) > 0.05
    # 4/8 vs 8/8 crosses into the significant-degradation region.
    assert fisher(4, 8, 8, 8) < 0.05
    # Symmetric and monotone in the candidate count.
    assert fisher(6, 8, 8, 8) > fisher(5, 8, 8, 8)


def _report_dir(tmp_path, candidate_kept: int, h0_kept: int, reps: int):
    tmp_path.mkdir(parents=True, exist_ok=True)
    def rows(kept: int):
        return [
            {
                "passed": False,
                "training": {},
                "neighbors": {
                    "BFCL/024.json": {"kept_positive": i < kept},
                },
                "reasons": [],
            }
            for i in range(reps)
        ]

    report = {
        "arms": {
            "v001": {"complete": True, "per_repetition": rows(candidate_kept)},
            "h0": {"complete": True, "per_repetition": rows(h0_kept)},
        },
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def test_noise_gate_uses_statistical_non_inferiority(tmp_path):
    gate = _driver()._panel_gate_passed
    spec = {
        "noisy_cases": [
            {"case_id": "BFCL/024.json", "rule": "no_significant_degradation"},
        ],
    }
    # 5/8 vs 8/8 is within noise: the gate passes.
    assert gate(_report_dir(tmp_path / "a", 5, 8, 8), spec) is True
    # 3/8 vs 8/8 is a significant degradation: the gate fails.
    assert gate(_report_dir(tmp_path / "b", 3, 8, 8), spec) is False


def test_tolerance_rule_and_zero_tolerance_default(tmp_path):
    gate = _driver()._panel_gate_passed
    tolerant = {
        "noisy_cases": [
            {"case_id": "BFCL/024.json", "tolerance": 1},
        ],
    }
    assert gate(_report_dir(tmp_path / "c", 5, 6, 6), tolerant) is True
    assert gate(_report_dir(tmp_path / "d", 4, 6, 6), tolerant) is False
    strict = {"noisy_cases": [{"case_id": "BFCL/024.json"}]}
    assert gate(_report_dir(tmp_path / "e", 5, 6, 6), strict) is False


def test_zero_tolerance_units_checked_per_repetition(tmp_path):
    gate = _driver()._panel_gate_passed
    spec = {
        "training_cases": ["BFCL/002.json"],
        "clean_cases": [],
        "regression_cases": [],
    }
    good = {
        "arms": {
            "v001": {
                "complete": True,
                "per_repetition": [
                    {"training": {"BFCL/002.json": {"repaired": True}},
                     "neighbors": {}, "passed": True},
                    {"training": {"BFCL/002.json": {"repaired": False}},
                     "neighbors": {}, "passed": False},
                ],
            },
            "h0": {"complete": True, "per_repetition": []},
        },
    }
    (tmp_path / "report.json").write_text(json.dumps(good), encoding="utf-8")
    assert gate(tmp_path, spec) is False
