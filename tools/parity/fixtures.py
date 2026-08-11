"""Fixed fixture case set for old/new parity checks.

The ten cases are pinned by value from the project's own debug manifest
(datasets/raw/agenthallu/detector_debug_10_manifest.json) so the fixture
set cannot drift with the manifest. They cover all seven AgentHallu
benchmarks with balanced gold polarity (5 hallucination / 5 clean) across
four hallucination categories.

Terminal-state coverage (error / clean / unresolved) and at least one L2
policy-triggering case are properties of detector output, not of the raw
records; per plan §5 R7 they are asserted when Phase 3 detection parity
runs on warm-cache replay, not here.

The dataset is git-ignored (local-only); a missing record is a clear
error, never a silent skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = REPO_ROOT / "datasets" / "raw" / "agenthallu"

FIXTURE_CASE_IDS: tuple[str, ...] = (
    "BFCL/000",
    "BFCL/001",
    "Camel/000",
    "Magentic_One/000",
    "Octotools/000",
    "OpenDeepSearch/000",
    "OpenManus/000",
    "OpenManus/002",
    "SmolAgents/000",
    "SmolAgents/001",
)


def load_fixture_records(
    dataset_root: Path = DATASET_ROOT,
) -> dict[str, dict[str, Any]]:
    """Load the pinned fixture records as a case_id -> raw record mapping."""
    records: dict[str, dict[str, Any]] = {}
    for case_id in FIXTURE_CASE_IDS:
        benchmark, name = case_id.split("/")
        path = dataset_root / "AgentHallu" / benchmark / f"{name}.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"fixture record missing: {path} "
                "(datasets/ is local-only; fetch the AgentHallu dataset first)"
            )
        records[case_id] = json.loads(path.read_text(encoding="utf-8"))
    return records
