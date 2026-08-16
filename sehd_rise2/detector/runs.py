"""Frozen run-record persistence: per-case process artifacts + derived stats.

The run tree is the durable, machine-readable record of every detection
case. It is the data-provision surface the future evolution framework
reads — every fact the evolution side needs (envelope, evidence, stage
audit, provider telemetry) is on disk, one case per directory:

    <runs_root>/<run_id>/
        run.json            # run metadata: schema, created_at, config
                            # fingerprint, case list
        summary.json        # label-free aggregate stats, derived strictly
                            # from the case ledger (single source of truth)
        cases/<case_id>/
            input.json      # the label-free input record exactly as detected
            packet.json     # the compiled packet (evidence positions/tools)
            result.json     # the frozen detection envelope (full)
            audit.json      # stage audit, provider telemetry, raw previews,
                            # authority failures, timeline

Everything under the run tree is label-free by construction: the detector
never receives gold, so it can never persist gold. Gold-joined metrics
(accuracy, localization) are the EVALUATOR's business and must be written
by the evaluation side into its own file (see experiments/run_10_gold.py
for the pattern: it writes ``eval_gold.json`` next to the detector's
files).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.packet import build_packet

RUN_SCHEMA = "sehd2.run_record.v1"


def default_runs_root() -> Path:
    return Path("runs")


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """Identity of a resolved config (never contains credential values)."""
    text = json.dumps(
        dict(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# The modules whose source defines the verdict surface: prompts, packet
# rendering and transport semantics. The detector fingerprint covers them
# because two runs with identical configs but different prompt code are
# NOT the same detector (the rework-era runs shared a config fingerprint
# across incompatible prompt surfaces).
_VERDICT_SURFACE_MODULES = (
    "court.py", "conventions.py", "packet.py", "provider.py",
)


def detector_fingerprint(config: Mapping[str, Any]) -> str:
    """Identity of the detector's full decision surface.

    Resolved config plus the source of every prompt/rendering module:
    same fingerprint <=> same verdict law. Never contains credential
    values (they live only in env vars the config names).
    """
    root = Path(__file__).resolve().parent
    parts = [
        json.dumps(
            dict(config), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ),
    ]
    for name in _VERDICT_SURFACE_MODULES:
        parts.append((root / name).read_text(encoding="utf-8"))
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_case_id(case_id: str) -> str:
    """Filesystem-safe directory name; the original id stays inside files."""
    return (
        str(case_id).replace("/", "__").replace("\\", "__")
        .replace(":", "_") or "case"
    )


def record_case_run(
    *,
    runs_root: str | Path,
    run_id: str,
    case_id: str,
    input_record: Mapping[str, Any],
    result: Mapping[str, Any],
    elapsed_seconds: float | None = None,
) -> Path:
    """Persist one detection case's full process record; returns its dir."""
    case_dir = Path(runs_root) / run_id / "cases" / _safe_case_id(case_id)
    packet = build_packet(dict(input_record))
    audit = {
        "schema_version": RUN_SCHEMA,
        "case_id": case_id,
        "recorded_at": _now(),
        "elapsed_seconds": elapsed_seconds,
        "stage_audit": result.get("stage_audit") or {},
        "authority_failure_reasons": (
            result.get("authority_failure_reasons") or []
        ),
        "providers": result.get("providers") or {},
    }
    _atomic_write(case_dir / "input.json", dict(input_record))
    _atomic_write(case_dir / "packet.json", packet)
    _atomic_write(case_dir / "result.json", dict(result))
    _atomic_write(case_dir / "audit.json", audit)
    return case_dir


def write_run_meta(
    *,
    runs_root: str | Path,
    run_id: str,
    config: Mapping[str, Any] | None = None,
    case_ids: Sequence[str] = (),
) -> Path:
    """Write the run metadata file; returns its path."""
    payload = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "created_at": _now(),
        "config_fingerprint": (
            config_fingerprint(config) if config is not None else ""
        ),
        "detector_fingerprint": (
            detector_fingerprint(config) if config is not None else ""
        ),
        "case_ids": sorted(set(str(value) for value in case_ids)),
    }
    path = Path(runs_root) / run_id / "run.json"
    _atomic_write(path, payload)
    return path


def _case_row(result: Mapping[str, Any]) -> dict[str, Any]:
    proof = result.get("proof_adjudication") or {}
    providers = result.get("providers") or {}
    tokens = sum(
        int((meta.get("usage") or {}).get("total_tokens") or 0)
        for meta in providers.values() if isinstance(meta, dict)
    )
    cache_tokens = sum(
        int(meta.get("prompt_cache_hit_tokens") or 0)
        for meta in providers.values() if isinstance(meta, dict)
    )
    attempts = sum(
        int(meta.get("attempts") or 0)
        for meta in providers.values() if isinstance(meta, dict)
    )
    return {
        "case_id": str(result.get("case_id") or ""),
        "state": result.get("state"),
        "decision_authority": result.get("decision_authority"),
        "runtime_valid": result.get("runtime_valid"),
        "responsible_event_id": result.get("responsible_event_id"),
        "responsible_step": result.get("responsible_step"),
        "candidate_count": (result.get("evidence_health") or {}).get(
            "candidate_count",
        ),
        "certificate_count": (result.get("evidence_health") or {}).get(
            "certificate_count",
        ),
        "error_certificate_count": (result.get("evidence_health") or {}).get(
            "error_certificate_count",
        ),
        "prosecution_coverage": proof.get("prosecution_coverage"),
        "defense_coverage": proof.get("defense_coverage"),
        "provider_attempts": attempts,
        "total_tokens": tokens,
        "prompt_cache_hit_tokens": cache_tokens,
        "provider_errors": sorted(
            str(meta.get("error_type"))
            for meta in providers.values()
            if isinstance(meta, dict) and meta.get("error_type")
        ),
    }


def summarize_run(runs_root: str | Path, run_id: str) -> dict[str, Any]:
    """Derive the aggregate summary strictly from the case ledger.

    The case ``result.json`` files are the single source of truth: the
    summary is a pure projection of them (a fresh run and a resumed run
    always derive identical counts).
    """
    cases_dir = Path(runs_root) / run_id / "cases"
    rows: list[dict[str, Any]] = []
    for result_path in sorted(cases_dir.glob("*/result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append(_case_row(result))
    rows.sort(key=lambda row: str(row.get("case_id")))
    states: dict[str, int] = {}
    for row in rows:
        key = str(row.get("state"))
        states[key] = states.get(key, 0) + 1
    summary = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "generated_at": _now(),
        "total_cases": len(rows),
        "runtime_valid_cases": sum(
            1 for row in rows if row.get("runtime_valid") is True
        ),
        "authority_invalid_cases": sum(
            1 for row in rows
            if row.get("decision_authority") != "authoritative"
        ),
        "state_counts": states,
        "total_provider_attempts": sum(
            int(row.get("provider_attempts") or 0) for row in rows
        ),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in rows),
        "total_prompt_cache_hit_tokens": sum(
            int(row.get("prompt_cache_hit_tokens") or 0) for row in rows
        ),
        "cases": rows,
    }
    path = Path(runs_root) / run_id / "summary.json"
    _atomic_write(path, summary)
    return summary


__all__ = [
    "RUN_SCHEMA",
    "config_fingerprint",
    "default_runs_root",
    "detector_fingerprint",
    "new_run_id",
    "record_case_run",
    "summarize_run",
    "write_run_meta",
]
