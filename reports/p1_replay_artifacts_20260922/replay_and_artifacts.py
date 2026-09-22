"""P1.1 + P1.3 dry-run: the replay boundary and the artifact digest, offline.

Demonstrates three things without a provider call:

1. ``replay_boundary`` reports the rerun reach of a hook set, and an unknown
   hook widens to the whole flow instead of pretending to reach nothing;
2. the rerun reach is a strict subset of the full flow for downstream hooks,
   which is what makes a targeted development replay cheaper than a full arm;
3. a replaced weight file refuses the frozen protocol it was frozen with,
   while an unchanged one loads.

Run from the repository root:

    python reports/p1_replay_artifacts_20260922/replay_and_artifacts.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evolution.backends import (  # noqa: E402
    REPLAY_STAGES,
    artifact_digest,
    artifact_manifest,
    partial_replay_plan,
    replay_boundary,
    source_digest,
)
from evolution.contracts import make_intervention  # noqa: E402
from evolution.promotion import (  # noqa: E402
    PromotionProtocolError,
    freeze_protocol,
    load_protocol,
)

OUT = pathlib.Path(__file__).resolve().parent / "replay_and_artifacts.json"


def candidate(hook: str, body: dict) -> dict:
    return make_intervention(
        intervention_id="IV-1-001",
        intervention_type="decision_guard" if hook == "adjudicate.guard"
        else "text_boundary",
        hook=hook,
        packet_features=["has_tool_calls"],
        body=body,
        expected_behavior="whatever the mechanism needs",
        target_polarity="recall",
        cycle=1,
        failure_family="FAM-fn-demo",
        failure_cases=["A/001.json", "A/002.json"],
    )


def boundary_report() -> dict:
    guard = candidate(
        "adjudicate.guard",
        {"action": "withhold_conviction", "action_not_when": "never when proven"},
    )
    tracer = candidate(
        "tracer.system",
        {"directive": "name the responsible step", "not_when": "never when none"},
    )
    return {
        "stages": list(REPLAY_STAGES),
        "adjudicate_guard": partial_replay_plan([guard]),
        "tracer_system": partial_replay_plan([tracer]),
        "unknown_hook": replay_boundary(["no.such.hook"]),
    }


def artifact_report(root: pathlib.Path) -> dict:
    weights = root / "weights.bin"
    weights.write_bytes(b"model-version-1")
    batch = root / "batch.json"
    roles = {"A/001.json": "benefit", "A/002.json": "benefit",
             "C/001.json": "protection", "C/002.json": "protection",
             "E/001.json": "protection", "E/002.json": "protection"}
    gold = {"A/001.json": True, "A/002.json": False, "C/001.json": True,
            "C/002.json": True, "E/001.json": False, "E/002.json": False}
    data = root / "data"
    for case in roles:
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": "q", "history": [], "is_hallucination": gold[case],
        }), encoding="utf-8")
    batch.write_text(json.dumps(sorted(roles)), encoding="utf-8")
    detector_config = root / "detector.json"
    detector_config.write_text(
        json.dumps({"schema_version": "detector.config.v1"}), encoding="utf-8",
    )
    protocol_path = root / "protocol.json"
    frozen = freeze_protocol(
        protocol_path, batch_path=batch, roles=roles, gold=gold,
        detector_config=detector_config, workers=2,
        artifact_paths=[weights],
    )
    load_protocol(protocol_path, dataset_root=data, stages=("structural",))
    unchanged = "loaded"
    weights.write_bytes(b"model-version-2")
    try:
        load_protocol(protocol_path, dataset_root=data, stages=("structural",))
        replaced = "LOADED (guard failed)"
    except PromotionProtocolError as exc:
        replaced = f"refused: {exc}"
    return {
        "manifest_schema": frozen["artifact_manifest"]["schema_version"],
        "artifacts": sorted(frozen["artifact_manifest"]["artifacts"]),
        "manifest_digest": frozen["artifact_manifest"]["digest"][:16],
        "empty_manifest_digest": artifact_manifest()["digest"][:16],
        "unchanged_weights": unchanged,
        "replaced_weights": replaced,
        "weight_digest_after": artifact_digest(weights)[:16],
        "source_digest": source_digest(),
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        payload = {
            "schema_version": "evolution.p1_replay_artifacts_dryrun.v1",
            "evidence_grade": "diagnostic-only",
            "note": (
                "Offline and scripted: no provider call, no admission, and no "
                "statement about detector quality. Demonstrates the replay "
                "boundary and the artifact identity rules only."
            ),
            "boundary": boundary_report(),
            "artifacts": artifact_report(root),
        }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    boundary = payload["boundary"]
    print(json.dumps({
        "tracer_rerun_stages": boundary["tracer_system"]["rerun_stages"],
        "tracer_reusable": boundary["tracer_system"]["reusable_stages"],
        "tracer_boundary_key": boundary["tracer_system"]["boundary_key"],
        # Both sides are compared as sets: the report lists stages sorted and
        # REPLAY_STAGES is in flow order, so an equality of lists would fail on
        # ordering alone.
        "unknown_hook_reaches_whole_flow": (
            set(boundary["unknown_hook"]["rerun_stages"])
            == set(boundary["stages"])
        ),
        "replaced_weights": payload["artifacts"]["replaced_weights"][:60],
        "manifest_schema": payload["artifacts"]["manifest_schema"],
    }, ensure_ascii=False, indent=2))
    print(f"\nreport -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
