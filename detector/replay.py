"""Hook reach, direct stage invalidation and fresh execution policy."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from detector.identity import artifact_digest, digest
import detector.defense as defense_stage
import detector.judge as judge_stage
import detector.recaller as recall_stage
import detector.step_tracer as tracer_stage
from detector.defense import _parse_defense, _validate_defense_evidence
from detector.judge import _parse_judge
from detector.recaller import _parse_candidates, _parse_upgrades, assemble_upgraded_candidates
from detector.step_tracer import _parse_tracer


REPLAY_STAGES = ("judge", "recall_scanner", "causal_upgrade", "step_tracer",
                 "defense", "adjudication")
REPLAY_INPUTS_SCHEMA = "detector.replay_inputs.v1"


def match_frozen_hearing(
    frozen: Mapping[str, Any] | None, target: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Select a validated hearing by its complete model input."""
    if frozen is None:
        return None
    return next((entry for entry in frozen.get("hearings") or ()
                 if isinstance(entry, Mapping)
                 and str(entry.get("source") or "") == str(target.get("source") or "")
                 and str(entry.get("candidate_id") or "") == str(target.get("candidate_id") or "")
                 and entry.get("positive") == target), None)


def build_replay_inputs(
    *, packet: Mapping[str, Any], config: Mapping[str, Any], source: str,
    snapshot: Mapping[str, Any] | None = None,
    entries: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Bind the base model inputs; hearing targets and audit context bind separately."""
    prompts = {
        "judge": (judge_stage.JUDGE_SYSTEM, judge_stage.JUDGE_CONTRACT_REPAIR),
        "recall_scanner": (recall_stage.RECALL_SYSTEM, recall_stage.RECALL_CONTRACT_REPAIR),
        "causal_upgrade": (recall_stage.UPGRADE_SYSTEM, recall_stage.UPGRADE_CONTRACT_REPAIR,
                           recall_stage.UPGRADE_MISSING_REPAIR),
        "step_tracer": (tracer_stage.TRACER_SYSTEM, tracer_stage.TRACER_CONTRACT_REPAIR),
        "defense": (defense_stage.DEFENSE_SYSTEM, defense_stage.DEFENSE_CONTRACT_REPAIR),
    }
    routes = {"judge": "judge_route", "recall_scanner": "recall_route",
              "causal_upgrade": "judge_route", "step_tracer": "judge_route",
              "defense": "defense_route"}
    policy = config.get("causal_audit") or {}
    artifact = policy.get("scorer_artifact")
    audit = {"policy": policy, "scorer": artifact_digest(artifact) if artifact is not None else None}
    review_policy = {
        "enabled": policy.get("negative_feedback_enabled", False),
        "offline_policy": config.get("offline_feedback_policy"),
    }
    if review_policy["enabled"] or review_policy["offline_policy"] is not None:
        review_policy["audit"] = audit
    stages = {}
    for stage, route in routes.items():
        stage_entries = [entry for entry in entries
                         if (policy := HOOK_POLICIES.get(str(entry.get("hook") or "")))
                         and policy.stage == stage and policy.frozen_key is not None]
        payload = {"source": source, "packet": digest(packet),
                   "route": config[route], "prompts": prompts[stage],
                   "interventions": stage_entries}
        if stage in {"recall_scanner", "causal_upgrade", "defense"}:
            payload["evidence"] = digest(snapshot)
        if stage == "step_tracer":
            payload["audit"] = audit
        if stage == "judge":
            payload["review_policy"] = review_policy
            if (config.get("causal_audit") or {}).get("next_action_mode", "off") != "off":
                payload["action_forecast_policy"] = audit
        stages[stage] = digest(payload)
    payload = {"schema_version": REPLAY_INPUTS_SCHEMA, "stage_digests": stages}
    return {**payload, "digest": digest(payload)}


def _select_replay_stages(frozen: dict[str, Any], expected: Mapping[str, Any]) -> None:
    recorded = frozen.get("replay_inputs")
    if not isinstance(recorded, Mapping):
        raise ValueError("frozen stage replay identity is required")
    payload = {key: value for key, value in recorded.items() if key != "digest"}
    stages = recorded.get("stage_digests")
    if (set(recorded) != {"schema_version", "stage_digests", "digest"}
            or recorded.get("schema_version") != REPLAY_INPUTS_SCHEMA
            or not isinstance(stages, Mapping)
            or set(stages) != set(REPLAY_STAGES[:-1])
            or any(not isinstance(value, str) or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value)
                   for value in stages.values())
            or recorded.get("digest") != digest(payload)):
        raise ValueError("frozen stage replay identity is invalid")
    changed = {stage for stage in stages if stages[stage] != expected["stage_digests"][stage]}
    if "judge" in changed:
        raise ValueError("frozen judge replay identity differs; fresh evaluation is required")
    if "recall_scanner" in changed:
        for key in ("recall_result", "recall_status", "upgrade_result", "upgrade_status"):
            frozen.pop(key, None)
    elif "causal_upgrade" in changed:
        for key in ("upgrade_result", "upgrade_status"):
            frozen.pop(key, None)
    if not isinstance(frozen.get("hearings"), list):
        return
    selected = []
    for entry in frozen["hearings"]:
        if not isinstance(entry, Mapping):
            selected.append(entry)
            continue
        entry = dict(entry)
        if changed.intersection({"recall_scanner", "causal_upgrade"}) and entry.get("source") == "recall_upgrade":
            continue
        for stage, name in (("step_tracer", "tracer"), ("defense", "defense")):
            if stage in changed:
                entry.pop(f"{name}_result", None)
                entry.pop(f"{name}_status", None)
        selected.append(entry)
    frozen["hearings"] = selected


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


def frozen_masked_stages(frozen: Mapping[str, Any],
                         entries: Sequence[Mapping[str, Any]]) -> list[str]:
    keys = {policy.frozen_key for policy in HOOK_POLICIES.values() if policy.frozen_key}
    targeted = tuple(str(entry.get("hook") or "") for entry in entries)
    fresh = requires_fresh_evaluation(entries)
    rows = frozen.get("hearings")
    hearings = rows if isinstance(rows, list) else ()
    return sorted(key.removesuffix("_result") for key in keys
                  if (fresh or stage_invalidated(key, targeted)) and (
                      key in frozen or any(isinstance(row, Mapping) and key in row for row in hearings)
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


def _frozen_stage(
    frozen: Mapping[str, Any], name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = frozen.get(f"{name}_result")
    status = frozen.get(f"{name}_status")
    if not isinstance(result, Mapping) or not isinstance(status, Mapping):
        raise ValueError(f"frozen {name} replay requires result and status objects")
    result_copy = dict(result)
    status_copy = dict(status)
    if not isinstance(result_copy.get("ok"), bool):
        raise ValueError(f"frozen {name} ok must be a boolean")
    if result_copy["ok"] != (status_copy.get("state") == "ok"):
        raise ValueError(f"frozen {name} result/status authority mismatch")
    if not result_copy["ok"] and result_copy.get("parsed") is not None:
        raise ValueError(f"frozen failed {name} cannot carry a parsed verdict")
    return result_copy, status_copy


def validate_frozen_stages(
    frozen: Mapping[str, Any], packet: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None = None,
    *, expected_inputs: Mapping[str, Any] | None = None,
    active_entries: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Normalize replay payloads through the same contracts as live stages."""
    if not isinstance(frozen, Mapping):
        raise ValueError("frozen stage replay payload must be an object")
    frozen = deepcopy(dict(frozen))
    masked = frozen_masked_stages(frozen, active_entries)
    if masked:
        raise ValueError(
            "frozen stage replay is refused: active interventions "
            f"target the frozen stage(s) {masked} (it would mask "
            "the intervention under test)"
        )
    if expected_inputs is not None:
        _select_replay_stages(frozen, expected_inputs)
    normalized = dict(frozen)
    if frozen.get("packet_hash") and frozen["packet_hash"] != digest(packet):
        raise ValueError("frozen stages belong to a different packet")
    judge_result, judge_status = _frozen_stage(frozen, "judge")
    if judge_result.get("ok"):
        parsed, error = _parse_judge(judge_result.get("parsed"), packet)
        if parsed is None:
            raise ValueError(f"frozen judge_result contract error: {error}")
        judge_result["parsed"] = parsed
    normalized["judge_result"] = judge_result
    normalized["judge_status"] = judge_status

    if "recall_result" in frozen:
        recall_result, recall_status = _frozen_stage(frozen, "recall")
        if recall_result.get("ok"):
            candidates, error = _parse_candidates(
                {"candidates": recall_result.get("candidates") or []}, packet, snapshot,
            )
            if candidates is None:
                raise ValueError(f"frozen recall_result contract error: {error}")
            recall_result["candidates"] = candidates
        normalized["recall_result"] = recall_result
        normalized["recall_status"] = recall_status

    if "upgrade_result" in frozen:
        upgrade_result, upgrade_status = _frozen_stage(frozen, "upgrade")
        recall_candidates = (
            normalized.get("recall_result") or {}
        ).get("candidates") or []
        if upgrade_result.get("ok"):
            upgrades, missing, error = _parse_upgrades(
                {
                    "upgrades": upgrade_result.get("upgrades") or [],
                },
                recall_candidates,
                packet, snapshot,
            )
            if upgrades is None or missing:
                raise ValueError(
                    f"frozen upgrade_result contract error: {error or missing}"
                )
            upgraded_candidates = assemble_upgraded_candidates(
                recall_candidates, upgrades,
            )
            upgrade_result["upgraded_candidates"] = upgraded_candidates
            upgrade_result["upgraded_candidate"] = (
                upgraded_candidates[0] if upgraded_candidates else None
            )
            upgrade_result["upgrades"] = upgrades
        else:
            upgrade_result["upgraded_candidates"] = []
            upgrade_result["upgraded_candidate"] = None
        normalized["upgrade_result"] = upgrade_result
        normalized["upgrade_status"] = upgrade_status

    if "hearings" in frozen:
        entries = frozen.get("hearings")
        if not isinstance(entries, list):
            raise ValueError("frozen hearings must be a list")
        normalized_hearings: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValueError("frozen hearing entry must be an object")
            clean = {
                "source": str(entry.get("source") or ""),
                "candidate_id": entry.get("candidate_id"),
                "positive": dict(entry.get("positive") or {}),
            }
            if "tracer_result" in entry or "tracer_status" in entry:
                tracer_result, tracer_status = _frozen_stage(entry, "tracer")
                if tracer_result.get("ok"):
                    parsed, error = _parse_tracer(
                        tracer_result.get("parsed"), packet,
                    )
                    if parsed is None:
                        raise ValueError(
                            f"frozen tracer_result contract error: {error}"
                        )
                    tracer_result["parsed"] = parsed
                clean["tracer_result"] = tracer_result
                clean["tracer_status"] = tracer_status
            if "defense_result" in entry or "defense_status" in entry:
                defense_result, defense_status = _frozen_stage(entry, "defense")
                if defense_result.get("ok"):
                    parsed, error = _parse_defense(defense_result.get("parsed"))
                    if parsed is None:
                        raise ValueError(
                            f"frozen defense_result contract error: {error}"
                        )
                    positive = {"step": clean["positive"].get("step")}
                    parsed, error = _validate_defense_evidence(
                        parsed, packet, positive, snapshot,
                    )
                    if parsed is None:
                        raise ValueError(
                            f"frozen defense_result evidence error: {error}"
                        )
                    defense_result["parsed"] = parsed
                clean["defense_result"] = defense_result
                clean["defense_status"] = defense_status
            normalized_hearings.append(clean)
        normalized["hearings"] = normalized_hearings
    return normalized
