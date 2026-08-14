"""The single Detector API of the minimal framework.

``Detector`` owns one resolved config; ``detect`` runs the frozen pipeline
(census -> verification -> defense -> deterministic court) label-free and
fail-closed. Evolved capability is deployed explicitly through
``genome`` / ``policy_genome`` payloads (loaded via ``skills.py``); the
minimal detector records them in provenance and defers their execution
semantics to the evolution framework.
"""

from __future__ import annotations

from typing import Any, Mapping

from sehd_rise2.detector.config import default_config_path, load_config
from sehd_rise2.detector.court import run_court
from sehd_rise2.detector.packet import build_packet, validate_record
from sehd_rise2.detector.result import build_detection_output
from sehd_rise2.detector.skills import load_policy_genome, load_skill_genome


class Detector:
    """One configured detector instance.

    The instance is stateless between calls: every ``detect`` runs the
    frozen pipeline independently. ``config=None`` loads the packaged
    detector config.
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        require_credentials: bool = True,
    ) -> None:
        self.config = (
            config if config is not None
            else load_config(require_credentials=require_credentials)
        )

    def detect(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str = "case",
        genome: Mapping[str, Any] | None = None,
        policy_genome: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Detect one label-free trajectory and return the frozen envelope."""
        validate_record(record)
        packet = build_packet(record)
        proof, providers, stage_audit = run_court(packet, self.config)
        provenance: dict[str, Any] = {}
        if genome is not None:
            provenance["genome_hash"] = genome.get("content_hash") or ""
        if policy_genome is not None:
            provenance["policy_genome_hash"] = (
                policy_genome.get("content_hash") or ""
            )
        output = build_detection_output(
            case_id=case_id, packet=packet, proof=proof,
            providers=providers, provenance=provenance,
        )
        output["stage_audit"] = stage_audit
        return output


_DEFAULT_DETECTOR: Detector | None = None


def _default_detector() -> Detector:
    global _DEFAULT_DETECTOR
    if _DEFAULT_DETECTOR is None:
        _DEFAULT_DETECTOR = Detector()
    return _DEFAULT_DETECTOR


def detect(
    record: Mapping[str, Any],
    *,
    case_id: str = "case",
    config: Mapping[str, Any] | None = None,
    genome: Mapping[str, Any] | None = None,
    policy_genome: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience entry: one detection with an explicit or default config."""
    detector = Detector(config) if config is not None else _default_detector()
    return detector.detect(
        record, case_id=case_id, genome=genome,
        policy_genome=policy_genome,
    )


__all__ = [
    "Detector",
    "build_packet",
    "default_config_path",
    "detect",
    "load_config",
    "load_policy_genome",
    "load_skill_genome",
    "validate_record",
]
