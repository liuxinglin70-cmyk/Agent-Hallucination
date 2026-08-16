"""The failure router: a meta skill that learns to route.

Three design laws carried over from the project's meta-skill lineage
(the retired v6 closed-vocabulary edits with hint-only authority, the
retired strategy learner's evidence threshold, and the rule that every
evolving unit needs both a producer and a runtime consumer):

1. CLOSED VOCABULARY — the router outputs class names from a frozen
   list and its memory holds only deterministic counts. No free text,
   no self-authored heuristics.
2. HINT AUTHORITY — the outcome memory enters the routing prompt as a
   hint. It never touches gates, thresholds or deployment decisions.
3. EARNED TRUST — a class earns a "prefer" hint only after >= 3
   verified flips historically; there is no penalty side, because a
   wrong route already dies at the flip gate.

Mechanical classes are assigned before the provider call: unstable and
infrastructure are measurements, not judgements. Anything unknown or
missing from the provider's answer falls back to
``missing_capability``, the widest and most testable channel.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.provider import ProviderError, provider_call
from sehd_rise2.evolution.worksheet import evidence_pack

ROUTE_SCHEMA = "sehd2.failure_route.v1"
MEMORY_SCHEMA = "sehd2.router_memory.v1"
ROUTER_MEMORY = Path("datasets/router_memory.json")
PREFER_THRESHOLD = 3

ROUTED_CLASSES = (
    "missing_capability",
    "not_triggered",
    "execution_failure",
    "decision_conflict",
    "infrastructure",
)
MECHANICAL_CLASSES = ("unstable",)

ROUTER_SYSTEM = (
    "You are the failure router of a self-evolving hallucination "
    "detector. For each recorded failure you receive mechanically "
    "gathered facts — where the evidence chain died (attribution), "
    "which deployed components routed or produced output for the "
    "packet, the detector's own scan, and the gold error description "
    "or the convicting certificates. Route each failure to exactly "
    "ONE class from this closed list:\n"
    "- missing_capability: no deployed artifact performs the check "
    "this failure needs.\n"
    "- not_triggered: a matching deployed artifact exists but its "
    "mechanical packet trigger did not route for this case.\n"
    "- execution_failure: the judgement rule already exists in a "
    "stage prompt but the stage did not follow it.\n"
    "- decision_conflict: the evidence chain completed but the "
    "adjudication weighed it wrongly.\n"
    "- infrastructure: provider/degraded-view problem, not evolvable.\n"
    "Read component_routing first, it is measured ground truth:\n"
    "- a component ROUTED but PRODUCED nothing, and the failure is a "
    "miss whose attribution matches that component's stage -> "
    "not_triggered;\n"
    "- a component PRODUCED output yet the verdict still landed "
    "against gold -> decision_conflict;\n"
    "- no component routed or produced anything -> missing_capability "
    "unless the evidence clearly shows a known rule being ignored "
    "(execution_failure) or a provider problem (infrastructure).\n"
    "Decide from the facts. When genuinely undecided, choose "
    "missing_capability — the widest, most testable channel. When "
    "router_memory is provided, a class marked \"prefer\" has "
    "historically produced verified flips; treat it as a hint, never "
    "as an override of the facts. Reply with ONLY JSON: {\"routes\": "
    "[{\"case_id\": \"...\", \"failure_class\": \"...\", \"reason\": "
    "\"one sentence\"}]}. No commentary."
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )


def load_memory(path: Path = ROUTER_MEMORY) -> dict[str, Any]:
    if not Path(path).exists():
        return {"schema_version": MEMORY_SCHEMA, "per_class": {}}
    memory = _read_json(Path(path))
    if memory.get("schema_version") != MEMORY_SCHEMA:
        raise ValueError(f"{path}: unknown router memory schema")
    return memory


def _memory_hint(per_class: Mapping[str, Mapping[str, Any]]) -> list[str]:
    hints = []
    for name in ROUTED_CLASSES:
        block = per_class.get(name) or {}
        if int(block.get("flips_passed") or 0) >= PREFER_THRESHOLD:
            hints.append(name)
    return hints


def component_routing_facts(
    proof: Mapping[str, Any] | None,
    packet_features: Sequence[str],
    active_components: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Which existing executable components routed / produced here."""
    routing: dict[str, dict[str, Any]] = {}
    for component in active_components:
        component_id = str(component.get("artifact_id") or "")
        trigger = component.get("trigger_features") or []
        routed = bool(trigger) and set(trigger) <= set(packet_features or ())
        produced = False
        if isinstance(proof, Mapping):
            for record in (
                list(proof.get("capability_trace") or [])
                + list(proof.get("candidate_generator_trace") or [])
            ):
                if (
                    str(record.get("capability_id")) == component_id
                    and record.get("status") == "produced"
                ):
                    produced = True
        routing[component_id] = {
            "kind": str(component.get("kind") or ""),
            "routed": routed,
            "produced": produced,
        }
    return routing


def build_route_rows(
    *, run_tree: Mapping[str, Mapping[str, Any]],
    rerolled: Mapping[str, list[str]],
    attribution_by_case: Mapping[str, str],
    dataset_root: Path,
    active_components: Sequence[Mapping[str, Any]] = (),
    unstable_cases: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """The unrouted worksheet: measured facts + evidence per failure.

    Unstable and infrastructure rows get their class mechanically
    here; the provider call only ever classifies stable,
    polarity-clear failures.
    """
    unstable = {str(case_id) for case_id in unstable_cases}
    rows: list[dict[str, Any]] = []
    for case_id in sorted(rerolled):
        entry = run_tree[case_id]
        result = entry["result"]
        recorded_state = str(result.get("state") or "")
        attribution = str(attribution_by_case.get(case_id) or "")
        proof = result.get("proof_adjudication") or {}
        packet_features = list(
            (result.get("stage_audit") or {}).get("packet_features") or [],
        )
        if case_id in unstable:
            failure_class = "unstable"
        elif recorded_state == "unresolved" or attribution == (
            "view_degraded"
        ):
            failure_class = "infrastructure"
        else:
            failure_class = ""
        rows.append({
            "schema_version": ROUTE_SCHEMA,
            "case_id": case_id,
            "gold": bool(entry["gold"]),
            "recorded_state": recorded_state,
            "reroll_states": [str(s) for s in rerolled[case_id]],
            "attribution": attribution,
            "failure_class": failure_class,
            "component_routing": component_routing_facts(
                proof, packet_features, active_components,
            ),
            "census_candidates": len(proof.get("census") or []),
            "evidence": evidence_pack(
                entry, dataset_root, reroll_states=rerolled[case_id],
            ),
        })
    return rows


def _fact_route(row: Mapping[str, Any]) -> str:
    """Deterministic cold-start route from the component facts alone.

    The measured routing facts decide the two classes a wrong guess is
    cheapest on; everything else stays ``missing_capability``.
    """
    routing = row.get("component_routing") or {}
    routed_silent = any(
        facts.get("routed") and not facts.get("produced")
        for facts in routing.values()
        if isinstance(facts, Mapping)
    )
    produced_against_gold = any(
        facts.get("produced")
        for facts in routing.values()
        if isinstance(facts, Mapping)
    )
    if routed_silent:
        return "not_triggered"
    if produced_against_gold:
        return "decision_conflict"
    return "missing_capability"


def route_failures(
    rows: Sequence[Mapping[str, Any]],
    *,
    memory_path: Path = ROUTER_MEMORY,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fill ``failure_class`` on every open row via one routing call.

    Provider routes are validated against the closed vocabulary;
    anything unknown, missing, or claiming a mechanical class falls
    back to ``missing_capability`` and is recorded in the meta. When
    the provider call itself fails, the deterministic fact route
    takes over — the router degrades to its cold-start heuristic
    instead of losing the round.
    """
    rows = [dict(row) for row in rows]
    open_rows = [row for row in rows if not row.get("failure_class")]
    memory = load_memory(memory_path)
    meta: dict[str, Any] = {}
    if open_rows:
        config = load_config()
        route = dict(config["verification_route"])
        route["model"] = os.environ.get(
            "SEHD_MUTATION_MODEL", "deepseek-v4-pro",
        )
        route["_route_name"] = "failure_router"
        payload = {
            "failures": [
                {
                    key: row[key] for key in (
                        "case_id", "gold", "recorded_state", "attribution",
                        "component_routing", "census_candidates", "evidence",
                    )
                }
                for row in open_rows
            ],
        }
        prefer = _memory_hint(memory["per_class"])
        if prefer:
            payload["router_memory"] = {"prefer": prefer}
        try:
            parsed, provider_meta = provider_call(
                route=route, system=ROUTER_SYSTEM,
                user=json.dumps(payload, ensure_ascii=False, indent=1),
                purpose="failure_router",
            )
            if not isinstance(parsed, Mapping) or not isinstance(
                parsed.get("routes"), list,
            ):
                raise ProviderError(
                    "failure_router", "answer is not a routes object",
                    error_type="contract",
                )
        except ProviderError:
            meta["degraded_to_fact_route"] = True
            provider_meta = None
            parsed = None
        decisions = (
            {
                str(item.get("case_id")): item
                for item in parsed["routes"]
                if isinstance(item, Mapping)
            }
            if parsed is not None else {}
        )
        fallbacks: list[str] = []
        for row in open_rows:
            decision = decisions.get(str(row["case_id"])) or {}
            failure_class = str(decision.get("failure_class") or "")
            if failure_class not in ROUTED_CLASSES:
                fallbacks.append(str(row["case_id"]))
                failure_class = _fact_route(row)
            row["failure_class"] = failure_class
            row["route_reason"] = str(decision.get("reason") or "")[:400]
        meta.update({
            "provider": provider_meta,
            "routed": len(open_rows),
            "fallbacks": fallbacks,
        })
    meta["mechanical"] = len(rows) - len(open_rows)
    meta["prefer_hint"] = _memory_hint(memory["per_class"])
    return rows, meta


def record_router_outcomes(
    *,
    rows: Sequence[Mapping[str, Any]],
    accepted_by_case: Mapping[str, Sequence[str]],
    flipped_cases: Sequence[str],
    memory_path: Path = ROUTER_MEMORY,
) -> dict[str, Any]:
    """Fold one round's routing outcome into the persistent memory.

    ``accepted_by_case`` maps case_id -> artifact ids whose proposals
    passed G1; ``flipped_cases`` are the cases the flip gate verified.
    Every number here is a deterministic count — the memory stays a
    scoreboard, never a diary.
    """
    memory = load_memory(memory_path)
    per_class: dict[str, dict[str, int]] = dict(memory["per_class"])
    flipped = {str(case_id) for case_id in flipped_cases}
    for row in rows:
        failure_class = str(row.get("failure_class") or "")
        if failure_class not in {*ROUTED_CLASSES, *MECHANICAL_CLASSES}:
            continue
        block = per_class.setdefault(
            failure_class,
            {"routed": 0, "proposals_accepted": 0, "flips_passed": 0},
        )
        block["routed"] += 1
        block["proposals_accepted"] += len(
            accepted_by_case.get(str(row["case_id"])) or [],
        )
        if str(row["case_id"]) in flipped:
            block["flips_passed"] += 1
    memory["per_class"] = per_class
    _write_json(Path(memory_path), memory)
    return memory


__all__ = [
    "MECHANICAL_CLASSES",
    "MEMORY_SCHEMA",
    "PREFER_THRESHOLD",
    "ROUTED_CLASSES",
    "ROUTE_SCHEMA",
    "ROUTER_MEMORY",
    "_fact_route",
    "build_route_rows",
    "component_routing_facts",
    "load_memory",
    "record_router_outcomes",
    "route_failures",
]
