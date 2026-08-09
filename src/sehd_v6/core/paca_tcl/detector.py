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
DETECTOR_SCHEMA_VERSION = "agenthallu.paca_tcl_detector_config.v3_5"
FORBIDDEN_INPUT_FIELDS = ocse_packet.EVALUATOR_ONLY_FIELDS


def _forbidden_field_hits(value: Any) -> set[str]:
    """Find evaluator-only keys at any detector boundary depth.

    Used on the OUTPUT side: the framework-produced result must never carry
    evaluation fields at any depth, so the check is recursive there.
    """
    if isinstance(value, Mapping):
        hits = {
            str(key) for key in value
            if str(key) in FORBIDDEN_INPUT_FIELDS
        }
        for nested in value.values():
            hits.update(_forbidden_field_hits(nested))
        return hits
    if isinstance(value, (list, tuple)):
        hits: set[str] = set()
        for nested in value:
            hits.update(_forbidden_field_hits(nested))
        return hits
    return set()


def _forbidden_input_hits(record: Mapping[str, Any]) -> set[str]:
    """Find evaluator-only keys on the raw-trajectory input's control layers.

    Mirrors the OCSE packet firewall: opaque tool payload content (the
    arguments of a tool call) is task evidence, not dataset identity, so it
    is never audited.  Only the schema layers this adapter owns are checked:
    the record top level, the history rows, and the tool-call rows.
    """
    hits: set[str] = {
        str(key) for key in record
        if str(key) in FORBIDDEN_INPUT_FIELDS
    }
    for row in record.get("history") or ():
        if not isinstance(row, Mapping):
            continue
        hits.update(
            str(key) for key in row
            if str(key) in FORBIDDEN_INPUT_FIELDS
        )
        for call in row.get("tool_calls") or ():
            if not isinstance(call, Mapping):
                continue
            hits.update(
                str(key) for key in call
                if str(key) in FORBIDDEN_INPUT_FIELDS
            )
            function = call.get("function")
            if isinstance(function, Mapping):
                hits.update(
                    str(key) for key in function
                    if str(key) in FORBIDDEN_INPUT_FIELDS
                )
    return hits


def validate_record(record: dict[str, Any]) -> None:
    """Validate one label-free raw trajectory before any provider call."""
    if not isinstance(record, dict):
        raise TypeError("record must be an object")
    leaked = sorted(_forbidden_input_hits(record))
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
    schema_version = str(config.get("schema_version") or "")
    if schema_version and schema_version != DETECTOR_SCHEMA_VERSION:
        raise ValueError(f"unsupported detector config schema: {schema_version}")
    resolved = resolve_config_provider_routes(dict(config), purpose="detector")
    missing = [name for name in REQUIRED_ROUTES if not isinstance(resolved.get(name), dict)]
    if missing:
        raise ValueError("detector config missing routes: " + ", ".join(missing))
    try:
        max_parallel = int(resolved.get("max_focused_parallel", 4))
        focused_retries = int(resolved.get("focused_prosecution_retries", 2))
    except (TypeError, ValueError) as exc:
        raise ValueError("detector concurrency and retry settings must be integers") from exc
    if max_parallel < 1:
        raise ValueError("max_focused_parallel must be at least 1")
    if focused_retries < 0:
        raise ValueError("focused_prosecution_retries must be non-negative")
    resolved["max_focused_parallel"] = max_parallel
    resolved["focused_prosecution_retries"] = focused_retries
    route_names = list(REQUIRED_ROUTES)
    if isinstance(resolved.get("counterfactual_route"), dict):
        route_names.append("counterfactual_route")
    for name in route_names:
        route = resolved[name]
        for field in ("endpoint", "model", "api_key_environment"):
            if not str(route.get(field) or "").strip():
                raise ValueError(f"detector route {name} missing {field}")
        for field in ("max_tokens", "timeout_seconds", "total_timeout_seconds"):
            if route.get(field) is not None and int(route[field]) <= 0:
                raise ValueError(f"detector route {name} requires positive {field}")
        if route.get("transport_retries") is not None and int(route["transport_retries"]) < 0:
            raise ValueError(f"detector route {name} requires non-negative transport_retries")
    resolved["detector_mode"] = "standalone"
    if require_credentials:
        from ...provider_routes import resolve_provider_credential, route_purpose
        for name in route_names:
            resolve_provider_credential(resolved[name], purpose=route_purpose(name))
    return resolved


def load_detector_config(path: Path, *, require_credentials: bool = True) -> dict[str, Any]:
    """Load detector routes without partition or execution authorization checks."""
    return resolve_detector_config(
        read_json(path), require_credentials=require_credentials,
    )


def load_skill_genome(path: Path, *, expected_hash: str | None = None) -> Any:
    """Explicitly load and validate one persisted evolved genome.

    Loading is intentionally opt-in.  A malformed or stale genome fails closed
    instead of silently falling back to the baseline detector.
    """
    from ..bccse.genome import MODULE_IDS, SkillGenome

    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("genome file must contain an object")
    modules = payload.get("modules")
    if not isinstance(modules, dict) or set(modules) != set(MODULE_IDS):
        raise ValueError("genome must contain exactly the registered modules")
    genome = SkillGenome.from_dict(payload)
    persisted_hash = str(payload.get("content_hash") or "")
    if persisted_hash and persisted_hash != genome.content_hash:
        raise ValueError("persisted genome content_hash does not match content")
    if expected_hash is not None and genome.content_hash != expected_hash:
        raise ValueError("loaded genome hash does not match expected_hash")
    return genome


def load_policy_genome(path: Path, *, expected_hash: str | None = None) -> Any:
    """Explicitly load and validate one persisted L2 adjudication-policy genome.

    Loading is intentionally opt-in and fail-closed, mirroring
    ``load_skill_genome``; a malformed or vocabulary-drifted payload is
    rejected instead of silently changing adjudication.
    """
    from ..bccse.policy_genome import (
        PolicyGenome,
        validate_policy_genome_items,
    )

    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("policy genome file must contain an object")
    genome = PolicyGenome.from_dict(payload)
    items_ok, items_reason = validate_policy_genome_items(genome)
    if not items_ok:
        raise ValueError(
            f"policy genome failed the closed vocabulary contract: "
            f"{items_reason}"
        )
    persisted_hash = str(payload.get("content_hash") or "")
    if persisted_hash and persisted_hash != genome.content_hash:
        raise ValueError(
            "persisted policy genome content_hash does not match content"
        )
    if expected_hash is not None and genome.content_hash != expected_hash:
        raise ValueError("loaded policy genome hash does not match expected_hash")
    return genome


def detect_record(
    *, record: dict[str, Any], config: dict[str, Any], case_id: str = "case",
    output_dir: Path | None = None, census_root: Path | None = None,
    genome: Any | None = None, genome_source: str | None = None,
    policy_genome: Any | None = None,
    adjudicator: Any | None = None,
) -> dict[str, Any]:
    """Detect one trajectory and return a label-free result.

    ``genome`` injects the evolved L1 provider-rule skill; ``policy_genome``
    supplies the evolved L2 adjudication-policy items to the proof court.
    Either may be supplied independently, so a deployed detector can carry
    the complete two-layer capability.
    """
    validate_record(record)
    output_dir = Path(output_dir or Path.cwd() / "detector_output")
    # Standalone deployment uses the deployable scope: only baseline and
    # strong items carry measured, independent correctness evidence, so a
    # weak working item never changes a deployed verdict.  Conditional
    # artifacts are absent from the genome items entirely (pending queue).
    policy_items = (
        policy_genome.items_for_scope("deployable")
        if policy_genome is not None else None
    )
    policy_registry = (
        policy_genome.registry if policy_genome is not None else None
    )
    if genome is None:
        run_adjudication = adjudicator or adjudicate_case
        result = run_adjudication(
            case_id=case_id, record=record, config=config,
            output_dir=output_dir, census_root=census_root,
            **(
                {
                    "policy_items": policy_items,
                    "policy_registry": policy_registry,
                }
                if policy_items is not None else {}
            ),
        )
    else:
        from .skill_integration import integrated_v35_provider
        with integrated_v35_provider(genome=genome, config=config):
            result = proof_engine.adjudicate_case(
                case_id=case_id, record=record, config=config,
                output_dir=output_dir, census_root=census_root,
                policy_items=policy_items,
                policy_registry=policy_registry,
            )
    if not isinstance(result, dict):
        raise RuntimeError("detector returned a non-object result")
    leaked = sorted(_forbidden_field_hits(result))
    if leaked:
        raise RuntimeError("detector output contains evaluation fields: " + ", ".join(leaked))
    if (
        result.get("runtime_valid") is not True
        or result.get("decision_authority") != "authoritative"
    ):
        # Fail closed: an invalid run must not carry a decisive prediction.
        # The occurrence ledger is append-only evidence and is preserved, but
        # every top-level decisive field is reset so standalone consumers of
        # prediction.json cannot read a stale verdict off an invalid run.
        result["state"] = "unresolved"
        result["decision_authority"] = "invalid"
        result["incident_state"] = "unresolved"
        result["responsible_event_id"] = None
        result["responsible_step"] = None
        result["impact_status"] = None
        result["impact_kind"] = None
        result["wide_construct_state"] = "unresolved"
        result["occurrence_state"] = "unresolved"
        result["incident_occurred"] = None
        result["occurrence_responsible_event_id"] = None
        result["occurrence_responsible_step"] = None
        result["occurrence_impact_status"] = None
        result["occurrence_impact_kind"] = None
        result["occurrence_recovery_state"] = None
    if genome is not None or policy_genome is not None:
        provenance: dict[str, Any] = {}
        if genome is not None:
            provenance["genome_hash"] = genome.content_hash
            provenance["genome_source"] = genome_source
        if policy_genome is not None:
            provenance["policy_genome_hash"] = policy_genome.content_hash
        result["provenance"] = provenance
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--record", type=Path, help="one JSON trajectory record")
    source.add_argument(
        "--records", type=Path,
        help="JSON/JSONL records or directory of trajectory JSON files",
    )
    parser.add_argument("--case-id", default="case")
    parser.add_argument("--output-dir", type=Path, default=Path("detector_output"))
    parser.add_argument("--max-parallel", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--census-root", type=Path, default=None,
        help="optional precomputed census cache; provider census is used when omitted",
    )
    parser.add_argument(
        "--genome", type=Path, default=None,
        help="optional persisted SkillGenome JSON; loading is explicit and fail-closed",
    )
    parser.add_argument(
        "--policy-genome", type=Path, default=None,
        help="optional persisted L2 PolicyGenome JSON; loading is explicit and fail-closed",
    )
    args = parser.parse_args()
    config = load_detector_config(args.config)
    genome = load_skill_genome(args.genome) if args.genome is not None else None
    genome_source = str(args.genome.resolve()) if args.genome is not None else None
    policy_genome = (
        load_policy_genome(args.policy_genome)
        if args.policy_genome is not None else None
    )
    if args.records is not None:
        from ...detector.batch import load_batch_records, run_batch
        records = load_batch_records(args.records)
        if args.limit is not None:
            if args.limit < 1:
                parser.error("--limit must be at least 1")
            records = dict(list(records.items())[:args.limit])
        summary = run_batch(
            records=records, config=config, output_dir=args.output_dir,
            max_parallel=args.max_parallel, resume=not args.no_resume,
            genome=genome, genome_source=genome_source,
            policy_genome=policy_genome,
            census_root=args.census_root,
        )
        print(args.output_dir / "summary.json")
        return 0 if summary["failed_case_count"] == 0 else 2
    record = read_json(args.record)
    result = detect_record(
        record=record, config=config, case_id=args.case_id,
        output_dir=args.output_dir, census_root=args.census_root,
        genome=genome, genome_source=genome_source,
        policy_genome=policy_genome,
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
