"""Executable prompt contracts; these do not measure model accuracy."""
import ast
from pathlib import Path
import re

import pytest

from detector import semantics
from detector.action_forecast import (COMPARISON_REPAIR_SYSTEM, COMPARE_INSTRUCTION,
                                      FORECAST_JUDGE_EXTENSION, FORECAST_SYSTEM)


@pytest.mark.parametrize("prompt", [semantics.JUDGE_SYSTEM, semantics.UPGRADE_SYSTEM, semantics.DEFENSE_SYSTEM],
                         ids=["judge", "upgrade", "defense"])
def test_causality_never_requires_one_edit_to_repair_every_deliverable(prompt):
    normalized = " ".join(prompt.split())
    assert not re.search(r"(?:correction|corrected).*?would (?:make every|every).*?deliverable", normalized)
    assert "would every deliverable become correct" not in normalized


def test_recall_uncertainty_is_not_a_second_conflicting_candidate_rule():
    normalized = " ".join(semantics.RECALL_SYSTEM.split())
    assert "Include candidates even when you are uncertain" not in normalized


@pytest.mark.parametrize("name,prompt", [
    ("judge", semantics.JUDGE_SYSTEM), ("recall", semantics.RECALL_SYSTEM),
    ("upgrade", semantics.UPGRADE_SYSTEM), ("tracer", semantics.TRACER_SYSTEM),
    ("defense", semantics.DEFENSE_SYSTEM), ("forecast", FORECAST_SYSTEM),
    ("comparison", COMPARE_INSTRUCTION), ("extension", FORECAST_JUDGE_EXTENSION),
    ("comparison_repair", COMPARISON_REPAIR_SYSTEM),
], ids=["judge", "recall", "upgrade", "tracer", "defense", "forecast", "comparison", "extension", "comparison_repair"])
def test_control_prompts_have_no_revision_tags_or_benchmark_label_hints(name, prompt):
    assert not re.search(r"\bP[012]-\d|benchmark's clean label|[\u3400-\u9fff]", prompt), name


def test_forecast_control_literals_and_diagnostics_are_english():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "action_forecast.py").read_text(encoding="utf-8"))
    docstrings = {id(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            assert not re.search(r"[\u3400-\u9fff]", node.value), f"line {node.lineno}"
