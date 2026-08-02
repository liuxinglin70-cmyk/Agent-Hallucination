"""Standalone PACA-TCL v3.5 detector entry point.

This module intentionally has no BCCSE/evolution dependency.  It accepts one
label-free trajectory record, resolves the detector routes from one config, and
returns one prediction object.  Evaluation labels must be joined by a caller
after detection, never supplied to this runtime.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from ...provider_routes import resolve_config_provider_routes
from ...utils import ocse_packet
from . import proof_engine
from .basic_runner import read_json

# Kept as an injectable seam for lightweight adapters and tests. Production
# callers should use detect_record rather than importing this implementation.
adjudicate_case = proof_engine.adjudicate_case


REQUIRED_ROUTES = (
    "candidate_fallback_route",
    "construct_census_route",
    "qualification_route",
    "propagation_route",
    "boundary_route",
    "focused_prosecution_route",
    "defense_route",
    "appeal_prosecutor_route",
    "appeal_defender_route",
)
FORBIDDEN_INPUT_FIELDS = frozenset({
    "gold_label",
    "gold_steps",
    "is_hallucination",
    "hallucination_step",
    "hallucination_reason",
    "outer_feedback",
})
FORBIDDEN_OUTPUT_FIELDS = FORBIDDEN_INPUT_FIELDS


def validate_record(record: dict[str, Any]) -> None:
    """Validate one label-free raw trajectory before any provider call."""
    if not isinstance(record, dict):
        raise TypeError("record must be an object")
    leaked = sorted(FORBIDDEN_INPUT_FIELDS.intersection(record))
    if leaked:
        raise ValueError("detector input must be label-free: " + ", ".join(leaked))
    ocse_packet.build_packet(record)


def resolve_detector_config(
    config: Mapping[str, Any], *, require_credentials: bool = False,
) -> dict[str, Any]:
    """Resolve one detector config without partition or experiment policy.

    Credential validation is intentionally separate from config loading so
    offline tests and provider adapters can construct the detector first.
    """
    resolved = resolve_config_provider_routes(dict(config), purpose="detector")
    missing = [name for name in REQUIRED_ROUTES if not isinstance(resolved.get(name), dict)]
    if missing:
        raise ValueError("detector config missing routes: " + ", ".join(missing))
    resolved["detector_mode"] = "standalone"
    if require_credentials:
        from ...provider_routes import resolve_provider_credential, route_purpose
        for name in REQUIRED_ROUTES:
            resolve_provider_credential(resolved[name], purpose=route_purpose(name))
    return resolved


def load_detector_config(path: Path, *, require_credentials: bool = True) -> dict[str, Any]:
    """Load detector routes without partition or execution authorization checks."""
    return resolve_detector_config(
        read_json(path), require_credentials=require_credentials,
    )


def detect_record(
    *, record: dict[str, Any], config: dict[str, Any], case_id: str = "case",
    output_dir: Path | None = None, census_root: Path | None = None,
    genome: Any | None = None, adjudicator: Any | None = None,
) -> dict[str, Any]:
    """Detect one trajectory and return a label-free result."""
    validate_record(record)
    output_dir = Path(output_dir or Path.cwd() / "detector_output")
    if genome is None:
        run_adjudication = adjudicator or adjudicate_case
        result = run_adjudication(
            case_id=case_id, record=record, config=config,
            output_dir=output_dir, census_root=census_root,
        )
    else:
        from .skill_integration import integrated_v35_provider
        with integrated_v35_provider(genome=genome, config=config):
            result = proof_engine.adjudicate_case(
                case_id=case_id, record=record, config=config,
                output_dir=output_dir, census_root=census_root,
            )
    if not isinstance(result, dict):
        raise RuntimeError("detector returned a non-object result")
    leaked = sorted(FORBIDDEN_OUTPUT_FIELDS.intersection(result))
    if leaked:
        raise RuntimeError("detector output contains evaluation fields: " + ", ".join(leaked))
    if result.get("runtime_valid") is not True:
        result["state"] = "unresolved"
        result["decision_authority"] = "invalid"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True, help="JSON trajectory record")
    parser.add_argument("--case-id", default="case")
    parser.add_argument("--output-dir", type=Path, default=Path("detector_output"))
    parser.add_argument(
        "--census-root", type=Path, default=None,
        help="optional precomputed census cache; provider census is used when omitted",
    )
    args = parser.parse_args()
    config = load_detector_config(args.config)
    record = read_json(args.record)
    result = detect_record(
        record=record, config=config, case_id=args.case_id,
        output_dir=args.output_dir, census_root=args.census_root,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "prediction.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
