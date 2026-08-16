"""Executable capability layer: evidence predicates that transform proof.

This is the detector-side execution plane the rebuilt evolution loop
produces into. An ``evidence_predicate`` artifact is NOT prose injected
into an existing prompt: it is a separate computation whose structured
output enters verification/defense as evidence. The distinction is the
whole point of the Round-2 post-mortem:

- prompt_directive changes what an existing LLM stage is asked to do;
- evidence_predicate adds a new function to the detector's state space
  (``input -> claim/source pair -> structured verdict``) and the verdict
  is then consumed by the existing stages.

The module keeps the layer discipline of the detector package: it imports
only detector primitives and never evolution code.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.conventions import packet_features
from sehd_rise2.detector.packet import render_census_view, render_evidence_view
from sehd_rise2.detector.provider import ProviderError, provider_call

# Runner implementations a capability artifact may reference. Adding a
# runner is a detector-side code change; evolution may only select from
# this closed vocabulary.
PREDICATE_RUNNERS = frozenset({"deterministic", "llm_structured"})
PREDICATE_VERDICTS = frozenset({
    "equivalent", "not_equivalent", "insufficient",
})

# The only fields a capability input contract may request. Keeping this
# closed is the label-free discipline of the predicate layer: no gold,
# no dataset identity, no recorded decisions may enter a predicate.
CAPABILITY_INPUT_FIELDS = frozenset({
    "candidate_id", "candidate_claim", "locus_event_id", "evidence_refs",
    "cited_evidence", "task_question",
})

_NUMERIC = re.compile(r"-?\d+(?:\.\d+)?")
_TASK_TOKEN = re.compile(r"'[A-Za-z0-9_\-]{1,24}'")


# ---------------------------------------------------------------------------
# deterministic predicate implementations
# ---------------------------------------------------------------------------


def _last_assistant_content(packet: Mapping[str, Any]) -> str:
    for event in reversed(packet.get("events") or []):
        if str(event.get("role") or "") == "assistant":
            content = str(event.get("content") or "")
            if content:
                return content
    return ""


def _cited_evidence_text(
    packet: Mapping[str, Any], candidate: Mapping[str, Any],
) -> str:
    refs = [str(ref) for ref in candidate.get("evidence_refs") or []]
    locus = str(candidate.get("locus_event_id") or "")
    if locus and locus not in refs:
        refs = [locus, *refs]
    if not refs:
        return ""
    text, _meta = render_evidence_view(packet, refs)
    return text


def _numbers(value: Any) -> list[float]:
    found: list[float] = []
    for match in _NUMERIC.finditer(str(value or "")):
        try:
            found.append(float(match.group(0)))
        except ValueError:
            continue
    return found


def _required_tokens(question: Any) -> set[str]:
    return {
        token[1:-1].lower()
        for token in _TASK_TOKEN.findall(str(question or ""))
    }


def deterministic_required_entity_set_preserved(
    *, packet: Mapping[str, Any], candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Mechanical entity-preservation predicate.

    The task's quoted literals are the required referents. The final
    assistant prose must still contain every one of them; a wording
    change that drops or replaces one is ``not_equivalent``.
    """
    required = _required_tokens(packet.get("question"))
    final_text = _last_assistant_content(packet).lower()
    missing = sorted(token for token in required if token not in final_text)
    if not required:
        return {
            "verdict": "insufficient",
            "basis": "the task text contains no quoted literal token",
        }
    if missing:
        return {
            "verdict": "not_equivalent",
            "basis": "final answer omits or changes required referents: "
            + ", ".join(repr(token) for token in missing),
        }
    return {
        "verdict": "equivalent",
        "basis": "final answer preserves every task-required referent",
    }


def deterministic_restated_value_consistency(
    *, packet: Mapping[str, Any], candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Mechanical value-consistency predicate.

    Every numeric literal present in the cited observations must survive
    into the final assistant prose with the same magnitude (formatting
    and trailing zeros are tolerated; magnitude changes are not).
    """
    evidence = _cited_evidence_text(packet, candidate)
    observed = _numbers(evidence)
    final_text = _last_assistant_content(packet)
    reported = _numbers(final_text)
    if not observed:
        return {
            "verdict": "insufficient",
            "basis": "the cited evidence contains no numeric literal",
        }
    tolerance = max(0.001, 0.005 * max(abs(value) for value in observed))
    for value in observed:
        if not any(abs(value - other) <= tolerance for other in reported):
            return {
                "verdict": "not_equivalent",
                "basis": (
                    f"cited numeric {value:g} does not survive into the "
                    "final answer within the 0.5% tolerance"
                ),
            }
    return {
        "verdict": "equivalent",
        "basis": "every cited numeric value survives into the final answer",
    }


def deterministic_numeric_field_alignment(
    *, packet: Mapping[str, Any], candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Mechanical field-alignment predicate.

    A numeric final answer is equivalent only when at least one numeric
    literal from the cited observation matches the reported value. If
    the cited source has numbers and none matches the final report, the
    restatement is field- or value-misaligned.
    """
    evidence = _cited_evidence_text(packet, candidate)
    observed = _numbers(evidence)
    reported = _numbers(_last_assistant_content(packet))
    if not observed or not reported:
        return {
            "verdict": "insufficient",
            "basis": "numeric alignment needs both a cited numeric source "
            "and a numeric final report",
        }
    tolerance = max(0.001, 0.005 * max(abs(value) for value in observed))
    if any(any(abs(value - other) <= tolerance for other in observed)
           for value in reported):
        return {
            "verdict": "equivalent",
            "basis": "the final numeric report aligns with a cited value",
        }
    return {
        "verdict": "not_equivalent",
        "basis": (
            "no final numeric value matches any cited observation; "
            "the report is field- or value-misaligned"
        ),
    }


DETERMINISTIC_RUNNERS: dict[str, Any] = {
    "required_entity_set_preserved": deterministic_required_entity_set_preserved,
    "restated_value_consistency": deterministic_restated_value_consistency,
    "numeric_field_alignment": deterministic_numeric_field_alignment,
}


# ---------------------------------------------------------------------------
# artifact routing + runtime execution
# ---------------------------------------------------------------------------


def _project_input(
    packet: Mapping[str, Any], candidate: Mapping[str, Any],
    input_schema: Mapping[str, Any],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in input_schema:
        if field not in CAPABILITY_INPUT_FIELDS:
            raise ValueError(
                f"capability input field {field!r} outside the closed "
                "predicate vocabulary"
            )
        if field == "candidate_id":
            values[field] = str(candidate.get("candidate_id") or "")
        elif field == "candidate_claim":
            values[field] = str(candidate.get("claim") or "")
        elif field == "locus_event_id":
            values[field] = str(candidate.get("locus_event_id") or "")
        elif field == "evidence_refs":
            values[field] = [
                str(ref) for ref in candidate.get("evidence_refs") or ()
            ]
        elif field == "cited_evidence":
            values[field] = _cited_evidence_text(packet, candidate)
        elif field == "task_question":
            values[field] = str(packet.get("question") or "")
    return values


def _parse_predicate_output(
    parsed: Any, *, output_schema: Mapping[str, Any], capability_id: str,
) -> dict[str, Any]:
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{capability_id}: predicate returned no object")
    verdict_spec = output_schema.get("verdict")
    verdict_spec = verdict_spec if isinstance(verdict_spec, Mapping) else {}
    allowed = verdict_spec.get("enum") or sorted(PREDICATE_VERDICTS)
    verdict = str(parsed.get("verdict") or "")
    if verdict not in allowed:
        raise ValueError(
            f"{capability_id}: verdict {verdict!r} outside {allowed}"
        )
    return {
        "verdict": verdict,
        "basis": str(parsed.get("basis") or ""),
    }


def run_predicate_on_candidate(
    *, artifact: Mapping[str, Any], packet: Mapping[str, Any],
    candidate: Mapping[str, Any], config: Mapping[str, Any],
    provider_call_fn: Any = None,
) -> dict[str, Any]:
    """Execute one evidence predicate over one census candidate."""
    capability_id = str(artifact.get("artifact_id") or "")
    capability = artifact.get("capability") or {}
    capability = capability if isinstance(capability, Mapping) else {}
    input_schema = capability.get("input")
    input_schema = input_schema if isinstance(input_schema, Mapping) else {}
    output_schema = capability.get("output")
    output_schema = (
        output_schema if isinstance(output_schema, Mapping) else {}
    )
    implementation = artifact.get("implementation") or {}
    implementation = (
        implementation if isinstance(implementation, Mapping) else {}
    )
    runner = str(implementation.get("runner") or "")
    payload = _project_input(packet, candidate, input_schema)
    try:
        if runner == "deterministic":
            fn = DETERMINISTIC_RUNNERS.get(str(capability.get("name") or ""))
            if fn is None:
                return {
                    "capability_id": capability_id,
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "status": "not_applicable",
                    "output": {"verdict": "insufficient",
                               "basis": "unknown deterministic runner"},
                    "meta": {},
                }
            output = _parse_predicate_output(
                fn(packet=packet, candidate=candidate),
                output_schema=output_schema, capability_id=capability_id,
            )
            return {
                "capability_id": capability_id,
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "status": "produced",
                "output": output,
                "input_refs": {
                    "locus_event_id": str(candidate.get("locus_event_id") or ""),
                    "evidence_refs": [
                        str(ref) for ref in candidate.get("evidence_refs") or ()
                    ],
                    "input_fields": sorted(input_schema),
                },
                "meta": {"runner": "deterministic"},
            }
        if runner == "llm_structured":
            route = dict(config.get("verification_route") or {})
            route["_route_name"] = f"capability:{capability_id}"
            parsed, meta = (provider_call_fn or provider_call)(
                route=route,
                system=str(implementation.get("system") or ""),
                user=json.dumps(payload, ensure_ascii=False, indent=1),
                purpose=f"capability:{capability_id}",
            )
            output = _parse_predicate_output(
                parsed, output_schema=output_schema,
                capability_id=capability_id,
            )
            return {
                "capability_id": capability_id,
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "status": "produced",
                "output": output,
                "input_refs": {
                    "locus_event_id": str(candidate.get("locus_event_id") or ""),
                    "evidence_refs": [
                        str(ref) for ref in candidate.get("evidence_refs") or ()
                    ],
                    "input_fields": sorted(input_schema),
                },
                "meta": meta,
            }
    except (ProviderError, ValueError) as exc:
        return {
            "capability_id": capability_id,
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "status": "failed",
            "output": {
                "verdict": "insufficient",
                "basis": str(exc)[:400],
            },
            "input_refs": {
                "locus_event_id": str(candidate.get("locus_event_id") or ""),
                "evidence_refs": [
                    str(ref) for ref in candidate.get("evidence_refs") or ()
                ],
                "input_fields": sorted(input_schema),
            },
            "meta": {"error": str(exc)[:400]},
        }
    return {
        "capability_id": capability_id,
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "status": "not_applicable",
        "output": {"verdict": "insufficient",
                   "basis": f"unknown runner {runner!r}"},
        "meta": {},
    }


def run_evidence_predicates(
    *, packet: Mapping[str, Any],
    census: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    predicates: Sequence[Mapping[str, Any]],
    provider_call_fn: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run every routed evidence predicate over every census candidate.

    ``predicates`` are the already-routed entries from the deployment
    plan; this executor does no trigger filtering of its own. Returns
    ``(trace, outputs)``. ``outputs`` is the structured payload
    rendered into the verification/defense prompts; the trace is the
    runtime-engagement evidence (an artifact id alone is no longer
    enough — the predicate must actually produce output).
    """
    trace: list[dict[str, Any]] = []
    outputs: dict[str, list[dict[str, Any]]] = {}
    if not predicates or not census:
        return trace, outputs
    for artifact in predicates:
        for candidate in census:
            result = run_predicate_on_candidate(
                artifact=artifact, packet=packet, candidate=candidate,
                config=config, provider_call_fn=provider_call_fn,
            )
            trace.append(result)
            if result["status"] == "produced":
                outputs.setdefault(
                    str(result["capability_id"]), [],
                ).append(result)
    return trace, outputs


def render_capability_evidence(outputs: Mapping[str, Any]) -> str:
    """Render predicate outputs as evidence, never as an instruction."""
    lines: list[str] = []
    for capability_id, records in sorted(outputs.items()):
        for record in records:
            output = record.get("output") or {}
            lines.append(
                f"- candidate {record.get('candidate_id')}: "
                f"{capability_id} -> {output.get('verdict')} "
                f"({output.get('basis') or 'no basis'})"
            )
    if not lines:
        return ""
    return (
        "\nCAPABILITY EVIDENCE (computed by executable predicates; treat "
        "as evidence about the claim, not as a decision):\n"
        + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# candidate generators: recall-side execution plane
# ---------------------------------------------------------------------------

CANDIDATE_GENERATOR_RUNNERS = frozenset({"llm_structured"})
GENERATOR_INPUT_FIELDS = frozenset({"packet_view"})


def _normalize_generated_candidates(
    parsed: Any, *, artifact_id: str, offset: int,
) -> list[dict[str, Any]]:
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("candidates"), list,
    ):
        raise ValueError(
            f"{artifact_id}: candidate generator returned no candidates list"
        )
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(parsed["candidates"], start=1):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"{artifact_id}: candidate {index} is not an object"
            )
        claim = str(item.get("claim") or "").strip()
        locus = str(item.get("locus_event_id") or "").strip()
        refs = [
            str(ref) for ref in item.get("evidence_refs") or ()
            if str(ref)
        ]
        if not claim or not locus or not refs:
            raise ValueError(
                f"{artifact_id}: candidate {index} needs claim, "
                "locus_event_id and at least one evidence_refs"
            )
        normalized.append({
            "candidate_id": f"g{offset + index}",
            "claim": claim,
            "locus_event_id": locus,
            "evidence_refs": refs,
        })
    return normalized


def run_candidate_generators(
    *, packet: Mapping[str, Any], config: Mapping[str, Any],
    generators: Sequence[Mapping[str, Any]],
    provider_call_fn: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run routed candidate generators and return (trace, candidates).

    ``generators`` are the already-routed entries from the deployment
    plan; this executor does no trigger filtering of its own. A
    generator is an additional census extension stage, not prose in
    the existing census prompt. Its output re-enters the normal
    verification/defense/adjudication pipeline, so a recall artifact
    changes the detector's execution graph.
    """
    trace: list[dict[str, Any]] = []
    generated: list[dict[str, Any]] = []
    if not generators:
        return trace, generated
    view_text, _meta = render_census_view(packet)
    for artifact in generators:
        artifact_id = str(artifact.get("artifact_id") or "")
        implementation = artifact.get("implementation") or {}
        implementation = (
            implementation if isinstance(implementation, Mapping) else {}
        )
        capability = artifact.get("capability") or {}
        capability = capability if isinstance(capability, Mapping) else {}
        input_schema = capability.get("input") or {}
        input_schema = input_schema if isinstance(input_schema, Mapping) else {}
        payload: dict[str, Any] = {}
        for field in input_schema:
            if field not in GENERATOR_INPUT_FIELDS:
                raise ValueError(
                    f"{artifact_id}: generator input field {field!r} "
                    "outside the closed vocabulary"
                )
            if field == "packet_view":
                payload[field] = view_text
        route = dict(config.get("verification_route") or {})
        route["_route_name"] = f"candidate_generator:{artifact_id}"
        try:
            parsed, meta = (provider_call_fn or provider_call)(
                route=route,
                system=str(implementation.get("system") or ""),
                user=json.dumps(payload, ensure_ascii=False, indent=1),
                purpose=f"candidate_generator:{artifact_id}",
            )
            normalized = _normalize_generated_candidates(
                parsed, artifact_id=artifact_id,
                offset=len(generated),
            )
            generated.extend(normalized)
            trace.append({
                "capability_id": artifact_id,
                "status": "produced",
                "candidate_count": len(normalized),
                "meta": meta,
            })
        except (ProviderError, ValueError) as exc:
            trace.append({
                "capability_id": artifact_id,
                "status": "failed",
                "candidate_count": 0,
                "meta": {"error": str(exc)[:400]},
            })
    return trace, generated


def merge_generated_candidates(
    census: Sequence[Mapping[str, Any]],
    generated: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge census-extension candidates behind the original census."""
    base = [dict(candidate) for candidate in census]
    existing_ids = {
        str(candidate.get("candidate_id")) for candidate in base
    }
    merged = list(base)
    for candidate in generated:
        if str(candidate.get("candidate_id")) in existing_ids:
            continue
        existing_ids.add(str(candidate.get("candidate_id")))
        merged.append(dict(candidate))
    return merged


__all__ = [
    "CANDIDATE_GENERATOR_RUNNERS",
    "CAPABILITY_INPUT_FIELDS",
    "DETERMINISTIC_RUNNERS",
    "GENERATOR_INPUT_FIELDS",
    "PREDICATE_RUNNERS",
    "PREDICATE_VERDICTS",
    "merge_generated_candidates",
    "render_capability_evidence",
    "run_candidate_generators",
    "run_evidence_predicates",
    "run_predicate_on_candidate",
]




