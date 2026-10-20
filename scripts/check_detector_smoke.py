"""Read-only acceptance gate between a detector smoke run and a larger batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector.action_context import forecast_prefix
from detector.action_forecast import FORECAST_SCHEMA, comparison_prompt
from detector.causal_contracts import validate_seal
from detector.config import load_config
from detector.dataset import load_case
from detector.identity import digest, source_identity
from detector.measurement import measurement_status
from detector.packet import build_packet
from detector.runner import RUN_SCHEMA, SUMMARY_SCHEMA, safe_case_id, validate_case_commit
from detector.runtime import exclusive_writer


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def check_smoke(
    run_dir: Path, *, cases: Sequence[str], config: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate committed results without gold labels or provider access."""
    cases = list(cases)
    if not cases or len(cases) != len(set(cases)):
        raise ValueError("smoke cases must be nonempty and unique")
    errors: list[str] = []
    rows = []
    model_predictions = 0
    forecast_responses = 0
    policy = config["causal_audit"]
    forecast_enabled = policy["mode"] != "off" and policy["next_action_mode"] != "off"
    with exclusive_writer(run_dir):
        manifest = _object(run_dir / "run.json")
        summary = _object(run_dir / "summary.json")
        for key, expected in (("schema_version", RUN_SCHEMA), ("run_id", run_dir.name),
                              ("cases", cases), ("case_set_digest", digest({"cases": cases})),
                              ("config_hash", digest(config)),
                              ("source_identity", source_identity("detector"))):
            if manifest.get(key) != expected:
                errors.append(f"manifest {key} differs from launch inputs")
        if (summary.get("schema_version") != SUMMARY_SCHEMA
                or summary.get("run_id") != run_dir.name or summary.get("cases") != cases):
            errors.append("summary identity differs from smoke inputs")
        measured = summary.get("measurement") or {}
        if (summary.get("completed") != len(cases) or summary.get("stop") != {}
                or any(measured.get(k) != len(cases) for k in ("requested", "returned", "authoritative"))
                or any(measured.get(k) != 0 for k in ("missing", "not_started"))):
            errors.append("smoke requires complete authoritative coverage and no stop")
        for case in cases:
            issues = []
            forecast_health = None
            try:
                result = _object(run_dir / "cases" / safe_case_id(case) / "result.json")
                record, _ = load_case(manifest["dataset_root"], case)
                validate_case_commit(result, run_id=run_dir.name, case_id=case, input_record=record)
                identity = (result.get("provenance") or {}).get("execution_identity")
                if not identity or identity != (manifest.get("execution_identities") or {}).get(case):
                    issues.append("case execution identity differs from manifest")
                elif (any(identity.get(k) != v for k, v in (
                        ("source", manifest.get("source_identity")), ("config", digest(config)),
                        ("record", digest(record)), ("packet", digest(build_packet(record)))))
                        or identity.get("digest") != digest({k: v for k, v in identity.items() if k != "digest"})):
                    issues.append("case execution identity does not bind the launch inputs")
                if measurement_status(result) != "authoritative":
                    issues.append("case is not authoritative")
                proof = result.get("proof") or {}
                if any(proof.get(k) != result.get(k) for k in ("state", "decision_authority", "runtime_valid")):
                    issues.append("proof and envelope disagree")
                stages = result.get("stage_status") or {}
                if (stages.get("judge") or {}).get("state") != "ok":
                    issues.append("judge stage is not healthy")
                source = ((proof.get("terminal_basis") or {}).get("decisive_facts") or {}).get("positive_source")
                if result.get("state") == "verified_error" and source != "mechanical_violation":
                    for name in ("step_tracer", "defense"):
                        if (stages.get(name) or {}).get("state") != "ok":
                            issues.append(f"required stage {name} is not healthy")
                for name, status in stages.items():
                    if status.get("state") not in {"ok", "not_needed"}:
                        issues.append(f"stage {name} is not healthy")
                for name, provider in (result.get("providers") or {}).items():
                    if (provider.get("vote") or {}).get("quorum_met") is False:
                        issues.append(f"stage {name} lacks quorum")
                if forecast_enabled:
                    packet = build_packet(record)
                    forecast = proof.get("action_forecast")
                    if not isinstance(forecast, Mapping):
                        raise ValueError("enabled forecast is missing")
                    validate_seal(forecast, schema=FORECAST_SCHEMA)
                    if forecast.get("packet_digest") != digest(packet) or forecast.get("policy_digest") != digest(policy):
                        issues.append("forecast input identity differs")
                    health = forecast.get("health")
                    forecast_health = health
                    if health == "task_alignment_unknown":
                        if not packet["events"] or any(
                            forecast_prefix(packet, e["position"])[1]["state"] != "unknown"
                            for e in packet["events"]
                        ):
                            issues.append("forecast alignment skip is not supported by the input")
                    elif health != "ok":
                        issues.append(f"forecast is not healthy: {health}")
                    if comparison_prompt(forecast, packet) and proof.get("action_forecast_used") is not True:
                        issues.append("forecast comparisons were not used by Judge")
                    model_predictions += sum(
                        p.get("producer") == "model_hypothesis" for p in forecast.get("predictions", [])
                    )
                    forecast_responses += sum(
                        r.get("purpose") == "action_forecast" and r.get("status") == "response"
                        for r in (result.get("execution") or {}).get("requests", [])
                    )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                issues.append(str(exc))
            rows.append({"case": case, "ready": not issues, "forecast_health": forecast_health, "errors": issues})
        if forecast_enabled and (model_predictions == 0 or forecast_responses == 0):
            errors.append("smoke did not exercise a successful model forecast; include an aligned case")
    return {"ready": not errors and all(r["ready"] for r in rows), "run_id": run_dir.name,
            "errors": errors, "cases": rows, "model_predictions": model_predictions,
            "forecast_responses": forecast_responses}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--cases-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        cases = json.loads(args.cases_file.read_text(encoding="utf-8-sig"))
        if not isinstance(cases, list) or any(not isinstance(c, str) for c in cases):
            raise ValueError("cases-file must contain an ordered JSON string array")
        report = check_smoke(args.run_dir, cases=cases, config=load_config(args.config, require_credentials=False))
    except (OSError, ValueError, RuntimeError) as exc:
        report = {"ready": False, "errors": [str(exc)]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
