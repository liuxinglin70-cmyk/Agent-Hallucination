"""Framework configuration.

Paths in ``configs/development.json`` are resolved against the repository root
(the parent of this package directory).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
CONFIG_SCHEMA = "evolution.framework.v1"

DEFAULT_CONFIG_PATH = PACKAGE_DIR / "configs" / "development.json"

DEFAULTS: dict[str, Any] = {
    "schema_version": CONFIG_SCHEMA,
    "detector_config": "detector/configs/autodl.json",
    "dataset_root": "datasets/raw/agenthallu/AgentHallu",
    "split_manifest": None,
    "mini_split_manifest": "datasets/protocols/development.json",
    "work_root": "artifacts/evolution",
    "registry_dir": "artifacts/evolution/registry",
    "ledger_path": "artifacts/evolution/ledger.jsonl",
    # H_t = (G, C, T, M, R) base: the harness manifest chain and the three
    # experience stores. These describe the generation that ran; they never
    # participate in admission.
    "harness_dir": "artifacts/evolution/harness",
    "memory_root": "artifacts/evolution/memory",
    "historical_development_manifests": [
        "datasets/protocols/development_exposure.json",
    ],
    "cycles": 3,
    # Which version the patrol measures: "active" aims each round at the
    # residual failures of the deployed harness, "bare_base" restores the
    # older behaviour where proposals only ever came from the bare base.
    "patrol_registry": "active",
    # How to respond when a claim's direction has no repairable case in the
    # gain set. "strict" preserves the original behaviour: the claim is
    # recorded as unmeasured and admission stays blocked. "replacement"
    # re-measures on an alternative evaluation set when one is installed;
    # "replacement_or_skip" additionally marks the claim skipped when no set
    # matches. Per-direction overrides go under "policies".
    "coverage_response": {"mode": "strict", "policies": {}},
    "split": {
        "seed": 20260817,
        "sentinel_size": 20,
        "sentinel_positives": 10,
        "sentinel_negatives": 10,
        "gain_set_size": 80,
        "gain_set_min_positives": 20,
        "gain_set_min_negatives": 20,
        "patrol_size": 120,
        "mini_pool_size": 32,
        "mini_gain_size": 16,
        "mini_gain_min_positives": 1,
        "mini_gain_min_negatives": 1,
    },
    "budget": {
        "patrol_cases": 120,
        "stable_failure_rerun_cases": 40,
        "llm_calls": 4,
        "max_proposals": 3,
        "max_admits": 2,
    },
    "gates": {
        "structural_jaccard_threshold": 0.72,
        "harmlessness_new_fn_allowed": 0,
        "harmlessness_new_fp_allowed": 0,
        "harmlessness_new_unresolved_allowed": 0,
        "harmlessness_min_protected_tp": 2,
        "harmlessness_min_protected_tn": 2,
        "behavior_require_fired": True,
        "m_min_marginal_cases": 1,
        "m_new_errors_allowed": 0,
        # Localization objective (target_kind=localization/joint). The joint
        # delta is counted over the frozen batch, so a case that stops being
        # convicted cannot leave the denominator. Breaking a localization the
        # control arm already had right is charged against the candidate
        # instead of being netted out by new gains.
        "localization_min_joint_cases": 1,
        "localization_joint_broken_allowed": 0,
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
        "name": "detector",
        "workers": 1,
        "cache": True,
    },
    "proposer": {
        "mode": "llm",
        "max_output_tokens": 32768,
        "thinking_level": "",
        "max_repair_calls": 1,
    },
    "mini_effect": {
        "min_marginal_cases": 1,
        "max_new_errors": 0,
        "max_unresolved_added": 0,
    },
    # The development loop's own budget, deliberately separate from
    # ``budget``: development is meant to be repeated and cheap, promotion is
    # frozen and expensive, and one counter would let a long development run
    # consume what the formal protocol needs. ``enabled`` is false by default so
    # an existing cycle keeps its single-shot behaviour until the loop is turned
    # on deliberately.
    # ``max_l1_cases`` counts case *evaluations*: one L1 replay measures the
    # target and neighbour cases twice (a baseline arm and a candidate arm), so
    # a replay over 6 cases costs 12. It is 24 rather than the plan's 12
    # because the plan's number was written before the development loop charged
    # both arms; at the honest rate, 12 would stop the loop before a single
    # replay whenever a mechanism carries its neighbour set.
    # §28.6: who archives, and when. The two stores in ``evolution.archive``
    # are writable from a run path, but nothing wrote them by default, because
    # "archive at the end of the development loop" and "archive at the end of
    # the cycle" are different claims about what a run remembers:
    #
    #   off               no run-path writes (the default: an existing run's
    #                     behaviour does not change)
    #   development_end   every version a development run produced, pass or
    #                     fail, archived when that run finishes. Chosen by a
    #                     caller that runs ``develop`` deliberately: that is
    #                     where a version *exits* the loop, and where "we tried
    #                     this route before" is asked.
    #   cycle_end         the same ingestion, run once per cycle, so a cycle
    #                     that develops several mechanisms remembers all of
    #                     them in one place.
    #
    # ``root`` defaults to ``work_root/candidates``. It is a separate root from
    # the Active Registry by construction; nothing in the archive can admit a
    # version.
    "archive": {
        "on": "off",
        "root": "",
    },
    # ``enabled`` turns the deployment-side development loop on inside a
    # cycle: the mechanisms this round diagnosed are drafted against, revised
    # through L0/L1, and only a version that passed both reaches the gates.
    # ``off`` keeps the single-shot path -- one draft, straight to the gates --
    # which is what every cycle did before this was wired, so the default
    # cannot change an existing run's behaviour.
    #
    # ``max_mechanisms_per_cycle`` bounds how many mechanisms one cycle may
    # develop: each one costs its own drafts and L1 replays, and a cycle that
    # develops every diagnosed mechanism would spend the whole patrol's budget
    # before the gates run. The mechanisms beyond the cap are reported as
    # skipped with that reason rather than silently dropped.
    "development": {
        "enabled": False,
        "max_mechanisms_per_cycle": 1,
        "max_versions_per_mechanism": 4,
        "max_provider_calls": 6,
        "max_l1_cases": 24,
        "require_normal_neighbors": 2,
        "allow_action_class_change": True,
    },
}


def resolve_path(value: str | os.PathLike[str], base: Path = REPO_ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _copy_container(value: Any) -> Any:
    """A deep copy of a nested default, so a loaded config owns its own blocks.

    ``_deep_merge`` returns a new *top-level* dict, and any block the config
    file mentions is rebuilt on the way through. A block the file does **not**
    mention used to be carried over by reference, so it was the same object as
    ``DEFAULTS[...]`` -- and mutating it through one loaded config changed the
    defaults every later load in the process saw. Six blocks are in that
    position for the shipped config file (``archive``, ``development``,
    ``coverage_response``, ``patrol_registry``, ``harness_dir``,
    ``memory_root``), and ``archive`` and ``development`` are both read from
    run paths that callers mutate -- a test or script setting the archive
    policy silently re-configured every later load.
    """
    return copy.deepcopy(value)


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = {key: _copy_container(value) for key, value in base.items()}
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
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("evolution config must be a JSON object")
    if raw.get("schema_version") and raw.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError(
            f"unsupported evolution config schema {raw.get('schema_version')!r}"
        )
    merged = _deep_merge(DEFAULTS, raw)
    merged["schema_version"] = CONFIG_SCHEMA
    # Resolve every configured filesystem path now so downstream code cannot
    # accidentally depend on the caller's cwd.
    for key in (
        "detector_config", "dataset_root", "split_manifest",
        "mini_split_manifest", "work_root", "registry_dir", "ledger_path",
        "harness_dir", "memory_root", "registry_source",
    ):
        if key not in merged or not merged[key]:
            continue
        merged[key] = str(resolve_path(merged[key]))
    merged["historical_development_manifests"] = [
        str(resolve_path(item))
        for item in merged.get("historical_development_manifests") or ()
    ]
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
