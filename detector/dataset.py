"""Dataset-side projection: raw benchmark records -> detector inputs.

The raw record may contain gold and metadata. ``project_record`` keeps only
the two fields the detector accepts; gold stays on the evaluator side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from detector.packet import ALLOWED_RECORD_KEYS
from detector.runtime import contained_path


def project_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return exactly ``{question, history}`` from a raw record."""
    return {
        key: raw.get(key) for key in sorted(ALLOWED_RECORD_KEYS)
        if key in raw
    }


def gold_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluator-side gold view of one raw record."""
    return {
        "is_hallucination": raw.get("is_hallucination"),
        "hallucination_step": raw.get("hallucination_step"),
        "hallucination_reason": raw.get("hallucination_reason"),
        "hallucination_category": raw.get("hallucination_category"),
        "hallucination_subcategory": raw.get("hallucination_subcategory"),
    }


def load_case(dataset_root: str | Path, case_id: str) -> tuple[dict, dict]:
    raw = json.loads(
        contained_path(dataset_root, case_id).read_text(encoding="utf-8-sig")
    )
    return project_record(raw), gold_fields(raw)
