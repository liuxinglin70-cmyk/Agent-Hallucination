"""Framework configuration.

Paths in ``configs/framework.json`` are resolved against the repository root
(the parent of this package directory). Process env overrides use the
``EVO3_*`` prefix for the few operational settings that matter in CI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
CONFIG_SCHEMA = "evolution_v3.framework.v1"

DEFAULT_CONFIG_PATH = PACKAGE_DIR / "configs" / "framework.json"

DEFAULTS: dict[str, Any] = {
    "schema_version": CONFIG_SCHEMA,
    "detector_config": "detector_v2/configs/detector_v2.json",
    "dataset_root": "datasets/raw/agenthallu/AgentHallu",
    "split_manifest": "datasets/eval_splits/phase2_v1/manifest.json",
    "mini_split_manifest": "datasets/eval_splits/v3_evo50/manifest.json",
    "work_root": "evolution_v3/work",
    "registry_dir": "evolution_v3/work/runtime_registry",
    "ledger_path": "evolution_v3/work/runtime_ledger.jsonl",
    "cycles": 3,
    "split": {
        "seed": 20260817,
        "sentinel_size": 20,
        "sentinel_positives": 10,
        "sentinel_negatives": 10,
        "gain_set_size": 80,
        "patrol_size": 120,
        "mini_pool_size": 32,
        "mini_gain_size": 16,
    },
    "budget": {
        "patrol_cases": 120,
        "stable_failure_rerun_cases": 40,
        "sentinel_cases": 40,
        "marginal_cases": 160,
        "llm_calls": 4,
        "max_proposals": 3,
        "max_admits": 2,
    },
    "gates": {
        "v1_jaccard_threshold": 0.72,
        "v2_new_fn_allowed": 0,
        "v2_new_fp_allowed": 0,
        "v2_new_unresolved_allowed": 0,
        "v3_require_fired": True,
        "m_min_marginal_cases": 1,
        "m_new_errors_allowed": 0,
    },
    "library": {
        "max_width": 6,
        "max_text_boundary_per_hook": 1,
        "max_decision_guards": 4,
        "retire_after_cycles_marginal_le0": 2,
        "rollback_after_cycles_net_negative": 2,
    },
    "audit": {
        "enabled": True,
    },
    "backend": {
        "name": "detector_v2",
        "workers": 1,
        "cache": True,
    },
    "proposer": {
        "mode": "llm",
        "max_output_tokens": 4096,
        "disable_thinking": True,
    },
}


def resolve_path(value: str | os.PathLike[str], base: Path = REPO_ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], dict(value))
        else:
            merged[key] = value
    return merged


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    config_path = (
        resolve_path(path) if path is not None else DEFAULT_CONFIG_PATH
    )
    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("evolution_v3 config must be a JSON object")
    if raw.get("schema_version") and raw.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError(
            f"unsupported evolution_v3 config schema {raw.get('schema_version')!r}"
        )
    merged = _deep_merge(DEFAULTS, raw)
    merged["schema_version"] = CONFIG_SCHEMA
    # Resolve every configured filesystem path now so downstream code cannot
    # accidentally depend on the caller's cwd.
    for key in (
        "detector_config", "dataset_root", "split_manifest",
        "mini_split_manifest", "work_root", "registry_dir", "ledger_path",
    ):
        merged[key] = str(resolve_path(merged[key]))
    merged["config_path"] = str(config_path)
    return merged


def work_paths(config: Mapping[str, Any]) -> dict[str, Path]:
    root = resolve_path(config["work_root"])
    return {
        "root": root,
        "cycles": root / "cycles",
        "runs": root / "runs",
        "cache": root / "cache",
        "reports": root / "reports",
        "final": root / "final",
    }


def ensure_dirs(config: Mapping[str, Any]) -> dict[str, Path]:
    paths = work_paths(config)
    for value in paths.values():
        value.mkdir(parents=True, exist_ok=True)
    registry_dir = resolve_path(config["registry_dir"])
    registry_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = registry_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    resolve_path(config["ledger_path"]).parent.mkdir(parents=True, exist_ok=True)
    return paths


__all__ = [
    "CONFIG_SCHEMA",
    "DEFAULT_CONFIG_PATH",
    "DEFAULTS",
    "PACKAGE_DIR",
    "REPO_ROOT",
    "ensure_dirs",
    "load_config",
    "resolve_path",
    "work_paths",
]
