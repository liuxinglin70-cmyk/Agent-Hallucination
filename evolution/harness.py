"""The harness manifest: H_t = (G, C, T, M, R) as a loadable artifact.

The design document represents a generation of the system as a five-component
tuple:

* **G** workflow and state transitions
* **C** context construction and information compression
* **T** tools and skill implementations
* **M** retrievable experience and skill memory
* **R** routing, budget and stopping policy

Until now that tuple existed only in prose. Nothing in the repository could
answer "what exactly is version t?" or "what changed between t and t+1?", and
a round's result could not be attributed to a component. This module makes the
tuple a checkable artifact.

Two properties matter more than completeness:

1. **Derived, not asserted.** Every component is read out of the code and
   configuration that actually run. If a stage entry point is renamed or a
   context budget disappears, building the manifest fails instead of recording
   a stale description.
2. **Verifiable after the fact.** Each component carries a digest over its own
   payload, and the manifest carries a digest over the components. Loading a
   manifest re-derives those digests, so an edited manifest is detected rather
   than silently trusted.

A manifest is a description, never a score: it participates in no gate.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Mapping

from evolution.backends import fingerprint
from evolution.contracts import load_json, save_json

HARNESS_SCHEMA = "evolution.harness.v1"
HARNESS_FILENAME = "harness.json"
HARNESS_HISTORY_NAME = "harness_history.jsonl"

# The canonical stage order of one detection. Declared here so the manifest can
# state the workflow G explicitly, and cross-checked against the detector's real
# entry points by ``verify_workflow``.
WORKFLOW_STAGES: tuple[str, ...] = (
    "validate",
    "packet",
    "judge",
    "recall",
    "upgrade",
    "tracer",
    "defense",
    "adjudicate",
)

# stage -> (module, callable) for the stages that own a callable entry point.
# ``validate`` and ``packet`` are pure construction steps without a single
# named stage function, so they are described by their module instead.
STAGE_ENTRYPOINTS: dict[str, tuple[str, str]] = {
    "packet": ("detector.packet", "build_packet"),
    "judge": ("detector.judge", "run_judge"),
    "recall": ("detector.recaller", "run_recall_scan"),
    "upgrade": ("detector.recaller", "run_causal_upgrade"),
    "tracer": ("detector.step_tracer", "run_step_tracer"),
    "defense": ("detector.defense", "run_defense"),
    "adjudicate": ("detector.adjudication", "adjudicate"),
}

# The context budgets C is actually built from. Read live so the manifest
# records the values in force, not a copy that can drift.
CONTEXT_BUDGET_NAMES: tuple[str, ...] = (
    "_JUDGE_VIEW_CHARS",
    "_JUDGE_EVENT_CHARS",
    "_JUDGE_TOOL_CHARS",
    "_DEFENSE_VIEW_CHARS",
    "_DEFENSE_EVENT_CHARS",
    "_INDEX_VIEW_CHARS",
)

CONTEXT_RENDERERS: tuple[str, ...] = (
    "render_full_view",
    "render_index_tail_view",
    "render_judge_view",
    "render_defense_view",
)

COMPONENT_KEYS: tuple[str, ...] = ("G", "C", "T", "M", "R")


def verify_workflow() -> dict[str, str]:
    """Import every declared stage entry point, or fail.

    Building a manifest on a workflow that no longer exists would produce a
    confident, wrong description. This makes the manifest self-invalidating
    when the detector is reorganised.
    """
    resolved: dict[str, str] = {}
    for stage, (module_name, attribute) in STAGE_ENTRYPOINTS.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ValueError(
                f"workflow stage {stage!r} references an unimportable module "
                f"{module_name!r}: {exc}"
            ) from exc
        if not callable(getattr(module, attribute, None)):
            raise ValueError(
                f"workflow stage {stage!r} expects {module_name}.{attribute} "
                "to be callable, and it is not"
            )
        resolved[stage] = f"{module_name}.{attribute}"
    return resolved


def _workflow_component() -> dict[str, Any]:
    from evolution.backends import source_digest

    from detector.interventions import HOOKS, INTERVENTION_TYPES

    return {
        "stages": list(WORKFLOW_STAGES),
        "entrypoints": verify_workflow(),
        "source_digest": source_digest(),
        "intervention_types": sorted(INTERVENTION_TYPES),
        "hooks": sorted(HOOKS),
        "execution_graph_rewrite": False,
    }


def _context_component() -> dict[str, Any]:
    from detector import packet as packet_module

    budgets: dict[str, Any] = {}
    for name in CONTEXT_BUDGET_NAMES:
        if not hasattr(packet_module, name):
            raise ValueError(
                f"context budget {name!r} is declared but absent from "
                "detector.packet; the context component cannot be described"
            )
        budgets[name] = getattr(packet_module, name)
    renderers: dict[str, bool] = {}
    for name in CONTEXT_RENDERERS:
        renderers[name] = callable(getattr(packet_module, name, None))
    missing = [name for name, present in renderers.items() if not present]
    if missing:
        raise ValueError(f"context renderers are missing: {missing}")
    return {
        "packet_schema": packet_module.PACKET_SCHEMA,
        "budgets": budgets,
        "renderers": sorted(renderers),
    }


def _tools_component(registry: Mapping[str, Any] | None) -> dict[str, Any]:
    entries = list((registry or {}).get("interventions") or ())
    return {
        "intervention_count": len(entries),
        "intervention_ids": sorted(
            str(entry.get("intervention_id") or "") for entry in entries
        ),
        "interventions": [dict(entry) for entry in entries],
    }


def _memory_component(memory_root: str | Path | None) -> dict[str, Any]:
    from evolution.memory import memory_summary

    if memory_root is None:
        return {"configured": False, "stores": {}}
    summary = memory_summary(memory_root)
    return {"configured": True, "stores": summary}


def _policy_component(config: Mapping[str, Any]) -> dict[str, Any]:
    """R: routing, budget and stopping policy, as configured."""
    return {
        "patrol_registry": str(config.get("patrol_registry") or "active"),
        "cycles": int(config.get("cycles") or 0),
        "budget": dict(config.get("budget") or {}),
        "gates": dict(config.get("gates") or {}),
        "library": dict(config.get("library") or {}),
        "mini_effect": dict(config.get("mini_effect") or {}),
        "split": dict(config.get("split") or {}),
        "proposer": dict(config.get("proposer") or {}),
    }


def _component(version: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": version,
        "digest": fingerprint(dict(payload)),
        "payload": dict(payload),
    }


def build_manifest(
    *,
    config: Mapping[str, Any],
    registry: Mapping[str, Any] | None,
    memory_root: str | Path | None,
    cycle: int = 0,
    parent_digest: str | None = None,
) -> dict[str, Any]:
    """Describe the current generation H_t as a loadable manifest."""
    components = {
        "G": _component("detector.workflow.v1", _workflow_component()),
        "C": _component("detector.context.v1", _context_component()),
        "T": _component("detector.tools.v1", _tools_component(registry)),
        "M": _component("evolution.memory.v1", _memory_component(memory_root)),
        "R": _component("evolution.policy.v1", _policy_component(config)),
    }
    manifest: dict[str, Any] = {
        "schema_version": HARNESS_SCHEMA,
        "cycle": int(cycle),
        "parent_digest": parent_digest,
        "components": components,
        "registry_hash": str((registry or {}).get("content_hash") or ""),
    }
    manifest["digest"] = fingerprint(_digest_payload(manifest))
    return manifest


def _digest_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The parts of a manifest its digest covers (everything but the digest)."""
    return {
        "schema_version": manifest.get("schema_version"),
        "cycle": manifest.get("cycle"),
        "parent_digest": manifest.get("parent_digest"),
        "registry_hash": manifest.get("registry_hash"),
        "components": {
            key: {
                "version": (manifest.get("components") or {}).get(key, {}).get("version"),
                "digest": (manifest.get("components") or {}).get(key, {}).get("digest"),
            }
            for key in COMPONENT_KEYS
        },
    }


def verify_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Re-derive every digest; raise on any mismatch.

    Returns the verified manifest. Verification covers both levels: each
    component digest over its payload, and the manifest digest over the
    component digests.
    """
    if manifest.get("schema_version") != HARNESS_SCHEMA:
        raise ValueError(
            f"unsupported harness manifest schema {manifest.get('schema_version')!r}"
        )
    components = manifest.get("components")
    if not isinstance(components, Mapping):
        raise ValueError("harness manifest has no components object")
    missing = [key for key in COMPONENT_KEYS if key not in components]
    if missing:
        raise ValueError(f"harness manifest is missing components: {missing}")
    for key in COMPONENT_KEYS:
        component = components[key]
        if not isinstance(component, Mapping):
            raise ValueError(f"harness component {key} is not an object")
        payload = component.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError(f"harness component {key} has no payload object")
        recomputed = fingerprint(dict(payload))
        if str(component.get("digest") or "") != recomputed:
            raise ValueError(
                f"harness component {key} digest mismatch: "
                f"recorded={component.get('digest')}, recomputed={recomputed}"
            )
    expected = fingerprint(_digest_payload(manifest))
    if str(manifest.get("digest") or "") != expected:
        raise ValueError(
            f"harness manifest digest mismatch: recorded={manifest.get('digest')}, "
            f"recomputed={expected}"
        )
    return dict(manifest)


def load_manifest(path: str | Path) -> dict[str, Any]:
    return verify_manifest(load_json(path))


def write_manifest(
    path: str | Path,
    *,
    manifest: Mapping[str, Any],
    history_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist a manifest, appending a one-line history entry when asked.

    The history makes the sequence of generations inspectable without reading
    every full manifest: each line is the digest, cycle and per-component
    digests.
    """
    verified = verify_manifest(manifest)
    save_json(path, verified)
    if history_path is not None:
        from evolution.contracts import append_jsonl, read_jsonl

        entry = {
            "schema_version": HARNESS_SCHEMA,
            "cycle": int(verified.get("cycle") or 0),
            "digest": verified.get("digest"),
            "parent_digest": verified.get("parent_digest"),
            "registry_hash": verified.get("registry_hash"),
            "component_digests": {
                key: (verified["components"][key] or {}).get("digest")
                for key in COMPONENT_KEYS
            },
        }
        # A resumed cycle re-runs this write. Recording the same generation
        # twice would make the history describe a transition that never
        # happened, so an identical (cycle, digest) entry is not repeated.
        already = any(
            int(existing.get("cycle") or -1) == entry["cycle"]
            and str(existing.get("digest") or "") == str(entry["digest"] or "")
            for existing in read_jsonl(history_path)
        )
        if not already:
            append_jsonl(history_path, entry)
    return verified


def component_payload(manifest: Mapping[str, Any], key: str) -> dict[str, Any]:
    if key not in COMPONENT_KEYS:
        raise ValueError(f"unknown harness component {key!r}")
    verify_manifest(manifest)
    return dict(manifest["components"][key]["payload"])


def harness_diff(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Per-component difference between two generations.

    The diff is the answer to "what changed in H?", reported per component so a
    later round can attribute an outcome to the component that moved rather
    than to "the system".
    """
    current = verify_manifest(current)
    if previous is None:
        return {
            "previous_digest": None,
            "current_digest": current.get("digest"),
            "first_manifest": True,
            "changed": list(COMPONENT_KEYS),
            "components": {
                key: {"changed": True, "reason": "no previous manifest"}
                for key in COMPONENT_KEYS
            },
        }
    previous = verify_manifest(previous)
    components: dict[str, Any] = {}
    for key in COMPONENT_KEYS:
        before = dict(previous["components"][key])
        after = dict(current["components"][key])
        if before.get("digest") == after.get("digest"):
            components[key] = {"changed": False}
            continue
        entry: dict[str, Any] = {
            "changed": True,
            "version_before": before.get("version"),
            "version_after": after.get("version"),
        }
        entry["changed_keys"] = _changed_top_level_keys(
            before.get("payload") or {}, after.get("payload") or {},
        )
        components[key] = entry
    return {
        "previous_digest": previous.get("digest"),
        "current_digest": current.get("digest"),
        "first_manifest": False,
        "changed": [
            key for key in COMPONENT_KEYS if components[key]["changed"]
        ],
        "components": components,
    }


def _changed_top_level_keys(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> list[str]:
    keys = set(before) | set(after)
    changed = []
    for key in sorted(keys):
        if fingerprint(before.get(key)) != fingerprint(after.get(key)):
            changed.append(str(key))
    return changed


__all__ = [
    "COMPONENT_KEYS",
    "CONTEXT_BUDGET_NAMES",
    "CONTEXT_RENDERERS",
    "HARNESS_FILENAME",
    "HARNESS_HISTORY_NAME",
    "HARNESS_SCHEMA",
    "STAGE_ENTRYPOINTS",
    "WORKFLOW_STAGES",
    "build_manifest",
    "component_payload",
    "harness_diff",
    "load_manifest",
    "verify_manifest",
    "verify_workflow",
    "write_manifest",
]
