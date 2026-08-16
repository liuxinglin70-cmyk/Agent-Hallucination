"""The single Detector API of the minimal framework.

``Detector`` owns one resolved config; ``detect`` runs the frozen pipeline
(census -> verification -> defense -> deterministic court) label-free and
fail-closed. Evolved capability deploys through exactly one channel: the
``artifacts`` registry (``detector/artifacts.py``). Every artifact kind is
bound to an execution point inside the pipeline — prompt directives inject
into all three stage prompts, adjudication policies apply inside the
deterministic court — so deployed evolution always executes.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sehd_rise2.detector.artifacts import load_artifact_registry
from sehd_rise2.detector.deploy import compile_deploy_plan
from sehd_rise2.detector.config import default_config_path, load_config
from sehd_rise2.detector.court import run_court
from sehd_rise2.detector.packet import build_packet, validate_record
from sehd_rise2.detector.result import build_detection_output


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
        artifacts: Mapping[str, Any] | None = None,
        frozen_census: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Detect one label-free trajectory and return the frozen envelope.

        ``artifacts`` is the deployed evolution-artifact registry: its
        prompt-directive entries route into all stage prompts and its
        adjudication policies apply inside the deterministic court
        (validated fail-closed before the run). ``frozen_census`` replays
        that candidate set instead of rolling the census stage (the
        paired-replay anchor; adjudication only).
        """
        validate_record(record)
        packet = build_packet(record)
        plan = compile_deploy_plan(artifacts)
        proof, providers, stage_audit = run_court(
            packet, self.config,
            plan=plan,
            frozen_census=(
                list(frozen_census) if frozen_census is not None else None
            ),
        )
        provenance: dict[str, Any] = {}
        if artifacts is not None:
            provenance["artifacts_hash"] = (
                artifacts.get("content_hash") or ""
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
    artifacts: Mapping[str, Any] | None = None,
    frozen_census: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convenience entry: one detection with an explicit or default config."""
    detector = Detector(config) if config is not None else _default_detector()
    return detector.detect(
        record, case_id=case_id, artifacts=artifacts,
        frozen_census=frozen_census,
    )


__all__ = [
    "Detector",
    "build_packet",
    "default_config_path",
    "detect",
    "load_artifact_registry",
    "load_config",
    "validate_record",
]
