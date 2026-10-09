"""Hook reach, direct stage invalidation and fresh execution policy."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from detector.identity import digest


REPLAY_STAGES = ("judge", "recall_scanner", "causal_upgrade", "step_tracer",
                 "defense", "adjudication")


@dataclass(frozen=True)
class HookPolicy:
    stage: str
    reach: tuple[str, ...]
    frozen_key: str | None = None
    fresh: bool = False


HOOK_POLICIES = MappingProxyType({
    "judge.system": HookPolicy("judge", REPLAY_STAGES, "judge_result", True),
    # Post-processing still executes on a frozen judge result.
    "judge.post": HookPolicy("judge", REPLAY_STAGES[1:]),
    "recall.trigger": HookPolicy("recall_scanner", REPLAY_STAGES[1:], "recall_result"),
    "scanner.system": HookPolicy("recall_scanner", REPLAY_STAGES[1:], "recall_result"),
    "upgrade.pre": HookPolicy("causal_upgrade", REPLAY_STAGES[2:], "upgrade_result"),
    "upgrade.system": HookPolicy("causal_upgrade", REPLAY_STAGES[2:], "upgrade_result"),
    "tracer.system": HookPolicy("step_tracer", ("step_tracer", "adjudication"), "tracer_result"),
    "defense.pre": HookPolicy("defense", REPLAY_STAGES[4:], "defense_result"),
    "defense.system": HookPolicy("defense", REPLAY_STAGES[4:], "defense_result"),
    "adjudicate.guard": HookPolicy("adjudication", ("adjudication",)),
})


def requires_fresh_evaluation(entries: Sequence[Mapping[str, Any]]) -> bool:
    for entry in entries:
        policy = HOOK_POLICIES.get(str(entry.get("hook") or ""))
        if policy is None or policy.fresh:
            return True
        if entry.get("type") == "decision_guard" and (
            entry.get("body") or {}
        ).get("action") == "require_full_view_defense":
            return True
    return False


def stage_invalidated(frozen_key: str, hooks: Iterable[str]) -> bool:
    for hook in hooks:
        policy = HOOK_POLICIES.get(str(hook))
        if policy is None or policy.frozen_key == frozen_key:
            return True
    return False


def frozen_masked_stages(frozen: Mapping[str, Any], hooks: Iterable[str]) -> list[str]:
    keys = {policy.frozen_key for policy in HOOK_POLICIES.values() if policy.frozen_key}
    targeted = tuple(hooks)
    return sorted(key.removesuffix("_result") for key in keys
                  if stage_invalidated(key, targeted) and (
                      key in frozen or any(key in row for row in frozen.get("hearings") or ())
                  ))


def replay_boundary(hooks: Iterable[str], *, fresh: bool = False) -> dict[str, Any]:
    selected = sorted({str(hook) for hook in hooks if str(hook)})
    unknown = [hook for hook in selected if hook not in HOOK_POLICIES]
    fresh = fresh or bool(unknown) or any(
        HOOK_POLICIES[hook].fresh for hook in selected if hook not in unknown
    )
    reach = set(REPLAY_STAGES) if fresh else {
        stage for hook in selected for stage in HOOK_POLICIES[hook].reach
    }
    boundary = {
        "schema_version": "detector.replay_boundary.v1",
        "hooks": selected,
        "rerun_stages": sorted(reach),
        "reusable_stages": sorted(set(REPLAY_STAGES) - reach),
        "stage_of_hook": {
            hook: HOOK_POLICIES[hook].stage if hook not in unknown else "" for hook in selected
        },
        "fresh_required": fresh,
        "unknown_hooks": unknown,
    }
    return {**boundary, "boundary_key": digest(boundary)}


def partial_replay_plan(entries: Sequence[Mapping[str, Any]], *,
                        active_entries: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    selected = [*active_entries, *entries]
    return replay_boundary((str(entry.get("hook") or "") for entry in selected),
                           fresh=requires_fresh_evaluation(selected))
