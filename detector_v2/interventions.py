"""Intervention deployment interface for detector_v2 (self-evolution hooks).

This is the detector-side half of the V3 self-evolution contract. An
intervention is only executable if its hook and runner are registered in this
module; anything else fails closed at load time.

The three intervention types and their write permissions:

- ``text_boundary``   -> only appends directive + NOT-WHEN to a ``*.system`` hook;
- ``evidence_check``  -> only appends ``proof.evidence`` entries;
- ``decision_guard``  -> only emits a closed-vocabulary adjudication action.

L1 can never touch authority/terminal fields and L1 triggers can only read
``packet_features``; ``decision_guard`` is the only L2 action writer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector_v2.packet import PACKET_FEATURE_NAMES, packet_features
from detector_v2.predicates import PREDICATES, PREDICATE_VERDICTS, run_predicate

INTERVENTION_REGISTRY_SCHEMA = "detector_v2.intervention_registry.v1"
INTERVENTION_SCHEMA = "detector_v2.intervention.v1"

INTERVENTION_TYPES = frozenset({
    "evidence_check", "text_boundary", "decision_guard",
})

HOOKS: dict[str, str] = {
    "judge.system": "L1",
    "judge.post": "L1",
    "scanner.system": "L1",
    "upgrade.pre": "L1",
    "upgrade.system": "L1",
    "tracer.system": "L1",
    "defense.pre": "L2",
    "defense.system": "L2",
    "adjudicate.guard": "L2",
}

SYSTEM_HOOKS = frozenset({
    "judge.system", "scanner.system", "upgrade.system",
    "tracer.system", "defense.system",
})
EVIDENCE_HOOKS = frozenset({"judge.post", "upgrade.pre", "defense.pre"})
GUARD_HOOK = "adjudicate.guard"

GUARD_ACTIONS = frozenset({
    "withhold_conviction",
    "restore_judge_conviction",
    "require_full_view_defense",
    "convict_mechanical_violation",
})

TARGET_POLARITIES = frozenset({"recall", "precision", "boundary"})

FACT_NAMES: dict[str, frozenset[str]] = {
    "judge_confidence": frozenset({"high", "medium", "low"}),
    "judge_positive": frozenset({"true", "false"}),
    "positive_source": frozenset({"judge", "recall_upgrade", "none"}),
    "defense_verdict": frozenset({
        "substantiated", "refuted", "inconclusive", "none",
    }),
    "defense_view_degraded": frozenset({"true", "false"}),
    "upgrade_degraded": frozenset({"true", "false"}),
    "recall_upgrade": frozenset({"present", "none"}),
    # Mechanical facts appended by evidence_check predicates. L1 can only
    # write these closed facts; decision_guard triggers may consume them.
    "required_action_present": frozenset({
        "satisfied", "violated", "not_applicable",
    }),
    "verbatim_fidelity": frozenset({
        "satisfied", "violated", "not_applicable",
    }),
    "evidence_refs_resolve": frozenset({
        "resolved", "unresolved", "not_applicable",
    }),
    "contract_coverage": frozenset({
        "covered", "uncovered", "not_applicable",
    }),
    "delivered_anchor_consistency": frozenset({
        "consistent", "inconsistent", "insufficient",
    }),
}

_EVIDENCE_HOOK_BY_TYPE: dict[str, frozenset[str]] = {
    "text_boundary": SYSTEM_HOOKS,
    "evidence_check": EVIDENCE_HOOKS,
    "decision_guard": frozenset({GUARD_HOOK}),
}


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"intervention field {field} must be non-empty text")
    return text


def _clean_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise ValueError(f"intervention field {field} must be a list")
    try:
        items = list(value)
    except TypeError as exc:
        raise ValueError(f"intervention field {field} must be a list") from exc
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"intervention field {field} must contain only strings")
    return [str(item) for item in items]


def intervention_content_hash(payload: Mapping[str, Any]) -> str:
    hashable = {
        key: value for key, value in payload.items()
        if key != "content_hash"
    }
    canonical = json.dumps(
        hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_trigger(entry: Mapping[str, Any], intervention_type: str) -> dict[str, Any]:
    trigger = entry.get("trigger")
    trigger = trigger if isinstance(trigger, Mapping) else {}
    features = {
        str(name) for name in trigger.get("packet_features") or ()
    }
    if not features or not features <= PACKET_FEATURE_NAMES:
        raise ValueError(
            f"{entry.get('intervention_id')}: trigger.packet_features must be "
            "a non-empty subset of the packet-feature vocabulary"
        )
    facts = _clean_list(trigger.get("facts"), "trigger.facts")
    if facts and intervention_type != "decision_guard":
        raise ValueError(
            f"{entry.get('intervention_id')}: only decision_guard may use trigger.facts"
        )
    parsed_facts = []
    for fact in facts:
        if "=" not in fact:
            raise ValueError(
                f"{entry.get('intervention_id')}: trigger fact {fact!r} must be name=value"
            )
        name, value = fact.split("=", 1)
        if name not in FACT_NAMES:
            raise ValueError(
                f"{entry.get('intervention_id')}: unknown trigger fact {name!r}"
            )
        if value not in FACT_NAMES[name]:
            raise ValueError(
                f"{entry.get('intervention_id')}: fact {name}={value!r} "
                f"outside {sorted(FACT_NAMES[name])}"
            )
        parsed_facts.append((name, value))
    return {
        "packet_features": features,
        "facts": parsed_facts,
    }


def _validate_fact_list(
    entry: Mapping[str, Any], value: Any, field: str,
) -> list[tuple[str, str]]:
    facts = _clean_list(value, field)
    parsed: list[tuple[str, str]] = []
    for fact in facts:
        if "=" not in fact:
            raise ValueError(
                f"{entry.get('intervention_id')}: {field} item {fact!r} "
                "must be name=value"
            )
        name, fact_value = fact.split("=", 1)
        if name not in FACT_NAMES:
            raise ValueError(
                f"{entry.get('intervention_id')}: unknown fact name {name!r}"
            )
        if fact_value not in FACT_NAMES[name]:
            raise ValueError(
                f"{entry.get('intervention_id')}: fact {name}={fact_value!r} "
                f"outside {sorted(FACT_NAMES[name])}"
            )
        parsed.append((name, fact_value))
    return parsed


def _validate_body(
    entry: Mapping[str, Any],
    intervention_type: str,
    *,
    require_executable_bounds: bool = False,
) -> dict[str, Any]:
    body = entry.get("body")
    body = body if isinstance(body, Mapping) else {}
    intervention_id = str(entry.get("intervention_id") or "?")
    if intervention_type == "text_boundary":
        directive = _require_text(body.get("directive"), f"{intervention_id}.body.directive")
        not_when = _require_text(body.get("not_when"), f"{intervention_id}.body.not_when")
        return {"directive": directive, "not_when": not_when}
    if intervention_type == "evidence_check":
        predicate = _require_text(body.get("predicate"), f"{intervention_id}.body.predicate")
        if predicate not in PREDICATES:
            raise ValueError(
                f"{intervention_id}: predicate {predicate!r} has no deterministic "
                "executor in detector_v2 (fail-closed)"
            )
        runner = _require_text(body.get("runner"), f"{intervention_id}.body.runner")
        if runner != "deterministic":
            raise ValueError(
                f"{intervention_id}: runner {runner!r} has no executor; "
                "only deterministic is executable"
            )
        verdicts = set(body.get("output_verdicts") or ())
        if verdicts != PREDICATE_VERDICTS[predicate]:
            raise ValueError(
                f"{intervention_id}: output_verdicts must declare the full "
                f"closed vocabulary {sorted(PREDICATE_VERDICTS[predicate])}"
            )
        return {
            "predicate": predicate,
            "runner": runner,
            "output_verdicts": verdicts,
        }
    action = _require_text(body.get("action"), f"{intervention_id}.body.action")
    if action not in GUARD_ACTIONS:
        raise ValueError(
            f"{intervention_id}: action {action!r} outside the guard vocabulary"
        )
    action_not_when = _require_text(
        body.get("action_not_when"), f"{intervention_id}.body.action_not_when"
    )
    # ``action_not_when`` is human-readable provenance; the executable
    # boundary is ``action_not_when_facts`` because prose alone cannot
    # constrain runtime behavior.
    if require_executable_bounds and "action_not_when_facts" not in body:
        raise ValueError(
            f"{intervention_id}: body.action_not_when_facts is required; "
            "prose-only exclusions are not executable"
        )
    if require_executable_bounds and not body.get("action_not_when_facts"):
        raise ValueError(
            f"{intervention_id}: body.action_not_when_facts must be non-empty"
        )
    not_when_facts = _validate_fact_list(
        entry,
        body.get("action_not_when_facts") or (),
        f"{intervention_id}.body.action_not_when_facts",
    )
    return {
        "action": action,
        "action_not_when": action_not_when,
        "action_not_when_facts": not_when_facts,
    }


def validate_intervention(
    entry: Mapping[str, Any], *, require_executable_bounds: bool = False,
) -> None:
    if not isinstance(entry, Mapping):
        raise ValueError("intervention entry must be an object")
    intervention_id = _require_text(
        entry.get("intervention_id"), "intervention_id"
    )
    intervention_type = _require_text(
        entry.get("type"), f"{intervention_id}.type"
    )
    if intervention_type not in INTERVENTION_TYPES:
        raise ValueError(
            f"{intervention_id}: type {intervention_type!r} outside "
            f"{sorted(INTERVENTION_TYPES)}"
        )
    hook = _require_text(entry.get("hook"), f"{intervention_id}.hook")
    if hook not in HOOKS:
        raise ValueError(f"{intervention_id}: hook {hook!r} is not registered")
    if hook not in _EVIDENCE_HOOK_BY_TYPE[intervention_type]:
        raise ValueError(
            f"{intervention_id}: type {intervention_type} cannot bind to hook {hook}"
        )
    _validate_trigger(entry, intervention_type)
    _validate_body(
        entry,
        intervention_type,
        require_executable_bounds=require_executable_bounds,
    )
    claim = entry.get("claim")
    claim = claim if isinstance(claim, Mapping) else {}
    _require_text(
        claim.get("expected_behavior"), f"{intervention_id}.claim.expected_behavior"
    )
    polarity = _require_text(
        claim.get("target_polarity"), f"{intervention_id}.claim.target_polarity"
    )
    if polarity not in TARGET_POLARITIES:
        raise ValueError(
            f"{intervention_id}: target_polarity {polarity!r} outside "
            f"{sorted(TARGET_POLARITIES)}"
        )
    provenance = entry.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    if not isinstance(provenance.get("cycle"), int) or isinstance(
        provenance.get("cycle"), bool
    ):
        raise ValueError(f"{intervention_id}: provenance.cycle must be an integer")
    _require_text(
        provenance.get("failure_family"),
        f"{intervention_id}.provenance.failure_family",
    )
    failure_cases = _clean_list(
        provenance.get("failure_cases"), f"{intervention_id}.provenance.failure_cases"
    )
    if len(failure_cases) < 2:
        raise ValueError(
            f"{intervention_id}: provenance.failure_cases needs >=2 cases"
        )
    persisted = str(entry.get("content_hash") or "")
    if persisted and persisted != intervention_content_hash(entry):
        raise ValueError(f"{intervention_id}: content_hash mismatch")
    schema = str(entry.get("schema_version") or "")
    if schema and schema != INTERVENTION_SCHEMA:
        raise ValueError(f"{intervention_id}: unsupported intervention schema {schema!r}")


def validate_registry(
    payload: Mapping[str, Any], *, require_executable_bounds: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("intervention registry must be an object")
    if payload.get("schema_version") != INTERVENTION_REGISTRY_SCHEMA:
        raise ValueError(
            f"unknown intervention registry schema {payload.get('schema_version')!r}"
        )
    entries = payload.get("interventions")
    if not isinstance(entries, list):
        raise ValueError("intervention registry must contain an interventions list")
    persisted = str(payload.get("content_hash") or "")
    if persisted and persisted != intervention_content_hash(payload):
        raise ValueError("intervention registry content_hash does not match")
    cleaned: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in entries:
        validate_intervention(
            entry, require_executable_bounds=require_executable_bounds,
        )
        entry_id = str(entry.get("intervention_id") or "")
        if entry_id in seen_ids:
            raise ValueError(f"duplicate intervention_id {entry_id}")
        seen_ids.add(entry_id)
        cleaned.append(dict(entry))
    return cleaned


def load_intervention_registry(
    path: str | Path, *, require_executable_bounds: bool = False,
) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    validate_registry(
        payload, require_executable_bounds=require_executable_bounds,
    )
    return payload


def build_intervention_registry(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": INTERVENTION_REGISTRY_SCHEMA,
        "interventions": [dict(entry) for entry in entries],
    }
    validate_registry(payload, require_executable_bounds=True)
    payload["content_hash"] = intervention_content_hash(payload)
    return payload


class InterventionPlan:
    """One validated intervention registry compiled for one detection call.

    The object is intentionally per-call: it owns the ``fired`` records and
    evidence written during that single detection so ``proof.deployment`` is
    a faithful append-only trace.
    """

    def __init__(self, entries: Sequence[Mapping[str, Any]]) -> None:
        self.entries = [dict(entry) for entry in entries]
        self.fired: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self.facts: dict[str, str] = {}
        self.guard_actions_applied: list[str] = []
        self.policy_conflict = False
        self.registry_hash = ""

    @classmethod
    def from_registry(cls, payload: Mapping[str, Any] | None) -> "InterventionPlan":
        if payload is None:
            return cls([])
        entries = validate_registry(payload, require_executable_bounds=True)
        plan = cls(entries)
        plan.registry_hash = str(payload.get("content_hash") or "")
        return plan

    def _facts_map(self, facts: Mapping[str, Any] | Sequence[str] | None) -> dict[str, str]:
        if facts is None:
            return {}
        if isinstance(facts, Mapping):
            return {
                str(key): str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in facts.items()
            }
        result: dict[str, str] = {}
        for item in facts:
            text = str(item)
            if "=" in text:
                name, value = text.split("=", 1)
                result[name] = value
        return result

    def _matches(self, entry: Mapping[str, Any], features: set[str], facts: Mapping[str, Any]) -> bool:
        trigger = entry.get("trigger") or {}
        required_features = {str(x) for x in trigger.get("packet_features") or ()}
        if not required_features <= set(features):
            return False
        fact_map = self._facts_map(facts)
        fact_map.update(self.facts)
        for fact in trigger.get("facts") or ():
            if isinstance(fact, str):
                if "=" not in fact:
                    return False
                name, value = fact.split("=", 1)
            else:
                name, value = fact
            if fact_map.get(name) != value:
                return False
        return True

    def _record(self, entry: Mapping[str, Any], event: str, detail: str = "") -> None:
        self.fired.append({
            "intervention_id": str(entry.get("intervention_id") or ""),
            "hook": str(entry.get("hook") or ""),
            "type": str(entry.get("type") or ""),
            "event": event,
            "detail": detail,
        })

    def prompt_block(self, features: Mapping[str, Any] | set[str], hook: str) -> str:
        feature_set = set(features)
        routed = [
            entry for entry in self.entries
            if entry.get("type") == "text_boundary"
            and entry.get("hook") == hook
            and self._matches(entry, feature_set, {})
        ]
        if not routed:
            return ""
        lines = [
            "\nDEPLOYED INTERVENTIONS for this hook (directive is mandatory "
            "when the trigger fires; NOT-WHEN is a hard boundary, never a hint):"
        ]
        for entry in routed:
            body = entry.get("body") or {}
            lines.append(
                f"- {body.get('directive')} NOT WHEN {body.get('not_when')}"
            )
            self._record(entry, "text_injected", f"hook={hook}")
        return "\n" + "\n".join(lines)

    def evidence_for(
        self,
        hook: str,
        packet: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        context = dict(context or {})
        features = packet_features(packet)
        entries = [
            entry for entry in self.entries
            if entry.get("type") == "evidence_check"
            and entry.get("hook") == hook
            and self._matches(entry, features, context)
        ]
        results: list[dict[str, Any]] = []
        for entry in entries:
            body = entry.get("body") or {}
            result = run_predicate(
                str(body.get("predicate") or ""), packet, context,
            )
            allowed_verdicts = set(body.get("output_verdicts") or ())
            if str(result.get("verdict") or "") not in allowed_verdicts:
                raise ValueError(
                    f"{entry.get('intervention_id')}: predicate returned "
                    f"undeclared verdict {result.get('verdict')!r}"
                )
            result["intervention_id"] = str(entry.get("intervention_id") or "")
            result["hook"] = hook
            self.evidence.append(dict(result))
            self.facts[str(result.get("predicate") or "")] = str(
                result.get("verdict") or ""
            )
            self._record(
                entry, "evidence_appended",
                f"predicate={result.get('predicate')} verdict={result.get('verdict')}",
            )
            results.append(result)
        return results

    def guard_actions(
        self,
        packet: Mapping[str, Any],
        facts: Mapping[str, Any] | Sequence[str] | None = None,
    ) -> list[str]:
        features = packet_features(packet)
        entries = [
            entry for entry in self.entries
            if entry.get("type") == "decision_guard"
            and entry.get("hook") == GUARD_HOOK
            and self._matches(entry, features, facts)
        ]
        actions: list[str] = []
        for entry in entries:
            body = entry.get("body") or {}
            fact_map = self._facts_map(facts)
            fact_map.update(self.facts)
            excluded = self._facts_map(
                body.get("action_not_when_facts") or ()
            )
            if excluded and all(
                fact_map.get(name) == value
                for name, value in excluded.items()
            ):
                self._record(
                    entry, "guard_suppressed",
                    "action_not_when_facts matched",
                )
                continue
            action = str((entry.get("body") or {}).get("action") or "")
            self._record(entry, "guard_fired", f"action={action}")
            actions.append(action)
        unique = list(dict.fromkeys(actions))
        if len(unique) > 1:
            self.policy_conflict = True
            self.fired.append({
                "intervention_id": "multiple",
                "hook": GUARD_HOOK,
                "type": "decision_guard",
                "event": "policy_conflict",
                "detail": f"conflicting guard actions disabled: {sorted(set(actions))}",
            })
            return []
        self.guard_actions_applied = unique
        return unique

    def deployment_summary(self, policy_conflict: bool | None = None) -> dict[str, Any]:
        conflict = self.policy_conflict if policy_conflict is None else bool(policy_conflict)
        return {
            "intervention_registry_hash": self.registry_hash,
            "active_interventions": [
                str(entry.get("intervention_id") or "") for entry in self.entries
            ],
            "fired": list(self.fired),
            "evidence": list(self.evidence),
            "facts": dict(self.facts),
            "guard_actions": list(self.guard_actions_applied),
            "policy_conflict": conflict,
        }


def compile_runtime_plan(
    intervention_registry: Mapping[str, Any] | None = None,
) -> InterventionPlan:
    return InterventionPlan.from_registry(intervention_registry)


def empty_plan() -> InterventionPlan:
    return InterventionPlan([])


__all__ = [
    "EVIDENCE_HOOKS",
    "FACT_NAMES",
    "GUARD_ACTIONS",
    "GUARD_HOOK",
    "HOOKS",
    "INTERVENTION_REGISTRY_SCHEMA",
    "INTERVENTION_SCHEMA",
    "INTERVENTION_TYPES",
    "InterventionPlan",
    "SYSTEM_HOOKS",
    "TARGET_POLARITIES",
    "build_intervention_registry",
    "compile_runtime_plan",
    "empty_plan",
    "intervention_content_hash",
    "load_intervention_registry",
    "validate_intervention",
    "validate_registry",
]
