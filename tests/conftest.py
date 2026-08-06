from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest


@pytest.fixture
def addressable_l1_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declare an addressable missing-certificate failure for runner tests.

    Most runner tests isolate mutation, replay, checkpoint, or probe behavior
    with deliberately minimal detector dictionaries.  L1 evolution now
    requires an explicit provider artifact, so these tests must state that
    causal precondition instead of relying on the former heuristic owner.
    Existing artifact projections remain authoritative.
    """
    from src.sehd_v6.core.bccse import evolution_runner as runner

    project = runner.project_provider_artifacts

    def project_with_fixture(
        *,
        detection_result: Mapping[str, Any],
        signal: Any,
        primary_type: Any,
    ) -> list[dict[str, Any]]:
        artifacts = project(
            detection_result=detection_result,
            signal=signal,
            primary_type=primary_type,
        )
        trigger_source = getattr(getattr(signal, "trigger_source", None), "value", "")
        adjudication = detection_result.get("proof_adjudication")
        if not isinstance(adjudication, Mapping):
            adjudication = {}
        boundary_path = detection_result.get("boundary_path")
        boundary_state = (
            str(boundary_path.get("state") or "")
            if isinstance(boundary_path, Mapping) else ""
        )
        base_attribution = runner.classify_failure({
            "adjudication": dict(adjudication),
            "boundary_path_state": boundary_state,
            "provider_artifacts": artifacts,
        })
        if (
            artifacts
            or not bool(getattr(signal, "should_evolve", False))
            or trigger_source == "signal_unavailable"
            or base_attribution.layer != "no_actionable"
        ):
            return artifacts
        return [{
            "kind": "error_certificate",
            "module_hint": "",
            "malformed": False,
            "missing_fields": ["certificate"],
            "artifact_id": "fixture_missing_error_certificate",
            "source": "runner_test_fixture",
        }]

    monkeypatch.setattr(runner, "project_provider_artifacts", project_with_fixture)
