"""Offline stand-in detector for the 693-case baseline dry run.

Kept as a module (not inline in the test) so the same fake serves both the
pytest dry run and the command-line one:

    python scripts/run_baseline_693.py --detector-factory \
        dryrun_fake_detector:OfflineDetector --workers 8 \
        --run-id dryrun693 --runs-root artifacts/baseline/runs

It never opens a socket: no provider import, no urllib, nothing but the case
file and arithmetic. Verdicts are read off the corpus (a per-case rule, not a
model call), which is exactly what makes the accounting -- coverage, confusion
cells, localization, timing -- testable without a service.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

RESULT_SCHEMA = "detector.detection_result.v1"


class OfflineDetector:
    """Deterministic, socket-free stand-in for ``detector.api.Detector``."""

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = dict(config or {})
        root = self.config.get("dataset_root")
        self.dataset_root = Path(root) if root else None
        self.calls: list[str] = []

    def _gold(self, case_id: str) -> tuple[bool, int | None]:
        if self.dataset_root is None:
            return False, None
        raw = json.loads(
            (self.dataset_root / case_id).read_text(encoding="utf-8-sig")
        )
        label = raw.get("is_hallucination")
        step = raw.get("hallucination_step")
        return (
            str(label).strip().lower() == "true",
            int(step) if str(step or "").strip().isdigit() else None,
        )

    def detect(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        interventions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(case_id)
        gold, gold_step = self._gold(case_id)
        number = int(str(case_id).split("/")[-1].split(".")[0])
        # One rule, three outcomes, chosen so a single offline pass exercises
        # every branch the report must keep apart.
        if number % 10 == 7:
            return self._envelope(
                case_id, "verified_clean", "degraded_judge_only",
            )
        if number % 2 == 0:
            return self._envelope(
                case_id, "verified_error", "authoritative",
                step=gold_step if number % 4 == 0 else (gold_step or 1) + 1,
            )
        return self._envelope(case_id, "verified_clean", "authoritative")

    @staticmethod
    def _envelope(case_id, state, authority, step=None):
        return {
            "schema_version": RESULT_SCHEMA,
            "case_id": case_id,
            "state": state,
            "decision_authority": authority,
            "localization_authority": (
                authority if state == "verified_error" else None
            ),
            "runtime_valid": True,
            "responsible_event_id": None,
            "responsible_step": step,
            "incident_occurred": state == "verified_error",
            "occurrence_state": state,
            "proof": {},
            "stage_status": {},
            "providers": {},
            "authority_failure_reasons": [],
            "evidence_health": {},
            "provenance": {},
        }
