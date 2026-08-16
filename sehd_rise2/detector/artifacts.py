"""Evolution artifact execution plane: closed vocabularies, registry
loading, shared adjudication semantics and the runtime class binder.

An EvolutionArtifact is the single deployable unit the evolution framework
may produce. Its kind binds mechanically to an execution point that exists
in this package:

- ``prompt_directive``    -> dual-boundary judgement directive injected
  into all three stage prompts (rendered through ``conventions.py``);
  this is an execution-reliability patch, NOT a new capability;
- ``adjudication_policy`` -> conviction rule applied inside
  ``court.adjudicate``;
- ``evidence_predicate``  -> executable structured check whose verdict is
  rendered into verification/defense as evidence (``capabilities.py``);
- ``candidate_generator`` -> recall-side census extension stage whose
  parsed candidates re-enter the normal verification pipeline.

An artifact whose kind has no executor here cannot load, so a deployable
payload without runtime semantics is structurally impossible. Every
artifact also carries the capability contract — a gap block and a
``capability_delta``: the loader rejects rule-only entries, because an
artifact must state which capability it acquires, not merely which rule
it adds.

The certificate-class vocabulary and policy actions are defined here (the
detector owns its adjudication semantics); the evolution framework imports
from this module, never the other way round.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from sehd_rise2.detector.capabilities import (
    CANDIDATE_GENERATOR_RUNNERS,
    CAPABILITY_INPUT_FIELDS,
    GENERATOR_INPUT_FIELDS,
    PREDICATE_RUNNERS,
    PREDICATE_VERDICTS,
)
from sehd_rise2.detector.conventions import PACKET_FEATURES
from sehd_rise2.detector.provider import ProviderError, provider_call

ARTIFACT_REGISTRY_SCHEMA = "sehd2.evolution_artifact.v1"

# kind -> the one execution point wired in this package. A kind without an
# entry here cannot deploy; new kinds land together with their executor.
ARTIFACT_KINDS: dict[str, str] = {
    "prompt_directive": "stage_prompts",
    "adjudication_policy": "adjudication",
    "evidence_predicate": "verification_predicates",
    "candidate_generator": "census_extension",
}

GAP_TYPES = frozenset({"recall", "precision", "boundary"})
GAP_STAGES = frozenset({"census", "verification", "defense", "adjudication"})
BASELINE_CLASS = "content_contradiction"
UNCLASSIFIED = "unclassified"

# Conviction-basis typing of a surviving certificate: every definition is a
# boundary — ``CONVICTS WHEN`` is the test the certificate must pass,
# ``NOT WHEN`` redirects the confusable neighbour. ``content_contradiction``
# is the baseline residual: it carries no policy and binds only after every
# specific class has failed its test; the declaration order mirrors that
# procedure.
CERTIFICATE_CLASSES: dict[str, str] = {
    "requirement_compliance": (
        "CONVICTS WHEN the certificate's complaint is the violation of "
        "an instruction about HOW to work — which channel or source to "
        "use, output form or format, internal procedure or tool-prompt "
        "composition, effort-scope or plan content, answer-quality "
        "preference — and the violation is the channel, procedure or "
        "quality itself: no wrong, fabricated or missing content is "
        "asserted (asking the human instead of the required source, "
        "offloading the solving to the user, answering below the "
        "preferred identification under partial retrieval are "
        "archetypes). NOT WHEN the certificate names content the agent "
        "got wrong or omitted: a wrong tool argument, fabricated "
        "placeholder inputs, a redundant or unnecessary action, an "
        "unanswered user question, a wrong derived value or conclusion "
        "convicts as content_contradiction even though the phrasing "
        "reads like a procedure complaint ('instead of ...', 'failed "
        "to ...')"
    ),
    "delivery_form": (
        "CONVICTS WHEN the session was closed (or the answer withheld "
        "from the expected channel) after the content was already "
        "extracted; carries NO policy — the abstain class recorded as "
        "non-separable under the current evidence representation "
        "(gold labels diverge on certificate-level-identical evidence). "
        "NOT WHEN the certificate asserts any defect in the content "
        "itself beyond non-delivery (the defect's own class binds)"
    ),
    "presentational_addition": (
        "CONVICTS WHEN the convicted material is interpretive or "
        "presentational layering over specifics the cited observations "
        "already contain — evaluative qualifiers, code-to-name or "
        "unit-and-format renderings of an observed figure, prose "
        "expansion of an observed list — introducing no new "
        "independently checkable specific. NOT WHEN the added material "
        "asserts a specific the observations do not contain and that is "
        "not a conventional rendering of one they do (fabrication → "
        "content_contradiction)"
    ),
    "expression_variance": (
        "CONVICTS WHEN a prose restatement differs from its cited source "
        "only in wording, formatting, rounding or near-tie directionality "
        "— a direction flip inside the source's own precision — "
        "preserving the referent, the operative meaning and the claimed "
        "precision (no more significant digits than the source gave), "
        "outside tool arguments. NOT WHEN the restatement changes what "
        "is claimed beyond rounding granularity or near-tie tolerance, "
        "changes the referent or instruction content, or restates an "
        "observed value with more significant digits than the source "
        "gave — asserted precision the source did not make is a "
        "content claim (content_contradiction)"
    ),
    "premise_conflict": (
        "CONVICTS WHEN the convicted specific is inherited from premises "
        "the task text or user statements establish — the agent "
        "restated, applied or followed a premise rather than authoring "
        "the specific; quote the premise sentence as the basis — and "
        "the certificate's whole authority is outside knowledge, with "
        "no cited observation contradicting the content (a user "
        "message or the task text is a premise, not an observation). "
        "NOT WHEN the agent authored the deviating specific: its own "
        "computed value, conclusion, classification, endorsement or "
        "claim, even one convicted by outside calculation, is "
        "content_contradiction"
    ),
    "absence_based": (
        "CONVICTS WHEN the certificate convicts a summary or "
        "process-narration claim because one cited slice lacks the "
        "material while no cited slice contradicts it and the material "
        "is grounded elsewhere in the trajectory. NOT WHEN some cited "
        "slice positively contradicts the claim (content_contradiction)"
    ),
    BASELINE_CLASS: (
        "the residual baseline class, carries no policy and binds only "
        "after every specific class has failed its test. CONVICTS WHEN "
        "the certificate asserts the agent authored or performed a "
        "wrong, fabricated, redundant or missing checkable specific — "
        "a wrong value, argument, entity, quantity, result, "
        "classification or self-assessment, fabricated inputs or "
        "outputs, a missing required answer, or an instruction "
        "violation with a factual consequence — where the deviation "
        "is in WHAT the agent asserted or delivered, not merely in "
        "which channel, procedure or effort level it used. NOT WHEN "
        "the defect is a channel, procedure or quality violation with "
        "no wrong content named (requirement_compliance), only "
        "interpretive or presentational layering over observed "
        "specifics (presentational_addition), only wording, formatting "
        "or near-tie restatement (expression_variance), only one "
        "slice's silence while the material is grounded elsewhere "
        "(absence_based), premise-inherited content convicted only by "
        "outside knowledge (premise_conflict), or non-delivery through "
        "the expected channel (delivery_form)"
    ),
}

POLICY_ACTIONS: dict[str, str] = {
    "withhold_conviction": (
        "a matching surviving certificate cannot convict"
    ),
    "require_corroborating_content_error": (
        "a matching certificate cannot be the sole conviction basis; a "
        "surviving content_contradiction certificate on the same case "
        "still convicts"
    ),
}

# Classes that carry no policy by design (recorded non-separable).
POLICY_FREE_CLASSES = frozenset({"delivery_form"})


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"artifact field {field} must be a non-empty string")
    return text


def _validate_evidence_predicate(
    *, artifact_id: str, gap: Mapping[str, Any],
    trigger: Mapping[str, Any], entry: Mapping[str, Any],
) -> None:
    """Closed contract for executable evidence predicates.

    The predicate must be a component, not prose: it carries an input
    contract, a closed output contract and an implementation runner
    from the detector-owned vocabulary. The flip gate proves the
    capability end-to-end; the loader only guarantees the contract is
    well-formed.
    """
    if gap["affected_stage"] != "verification":
        raise ValueError(
            f"{artifact_id}: evidence_predicate must affect verification, "
            f"got {gap['affected_stage']!r}"
        )
    features = {
        str(name) for name in trigger.get("packet_features") or ()
    }
    if not features or not features <= set(PACKET_FEATURES):
        raise ValueError(
            f"{artifact_id}: evidence_predicate trigger.packet_features "
            "empty or outside the packet-feature vocabulary"
        )
    capability = entry.get("capability")
    capability = capability if isinstance(capability, Mapping) else {}
    name = _require_text(
        capability.get("name"), f"{artifact_id}.capability.name",
    )
    _require_text(
        capability.get("failure_mode"),
        f"{artifact_id}.capability.failure_mode",
    )
    input_schema = capability.get("input")
    input_schema = input_schema if isinstance(input_schema, Mapping) else {}
    if not input_schema:
        raise ValueError(
            f"{artifact_id}: capability.input must be a non-empty object"
        )
    unknown_inputs = sorted(set(input_schema) - CAPABILITY_INPUT_FIELDS)
    if unknown_inputs:
        raise ValueError(
            f"{artifact_id}: capability input fields {unknown_inputs} "
            "outside the closed predicate vocabulary"
        )
    output_schema = capability.get("output")
    output_schema = (
        output_schema if isinstance(output_schema, Mapping) else {}
    )
    verdict_spec = output_schema.get("verdict")
    verdict_spec = verdict_spec if isinstance(verdict_spec, Mapping) else {}
    verdict_enum = verdict_spec.get("enum")
    if not isinstance(verdict_enum, list) or not verdict_enum:
        raise ValueError(
            f"{artifact_id}: capability.output.verdict.enum must be a "
            "non-empty list"
        )
    if not set(verdict_enum) <= PREDICATE_VERDICTS:
        raise ValueError(
            f"{artifact_id}: capability output verdicts {sorted(verdict_enum)} "
            f"outside {sorted(PREDICATE_VERDICTS)}"
        )
    guarantees = capability.get("guarantees")
    if not isinstance(guarantees, list) or not guarantees:
        raise ValueError(
            f"{artifact_id}: capability.guarantees must be a non-empty list"
        )
    implementation = entry.get("implementation")
    implementation = (
        implementation if isinstance(implementation, Mapping) else {}
    )
    runner = _require_text(
        implementation.get("runner"),
        f"{artifact_id}.implementation.runner",
    )
    if runner not in PREDICATE_RUNNERS:
        raise ValueError(
            f"{artifact_id}: implementation.runner {runner!r} outside "
            f"{sorted(PREDICATE_RUNNERS)}"
        )
    if runner == "llm_structured":
        _require_text(
            implementation.get("system"),
            f"{artifact_id}.implementation.system",
        )
    delta = entry.get("capability_delta")
    delta = delta if isinstance(delta, Mapping) else {}
    if str(delta.get("capability_id") or "") != artifact_id:
        raise ValueError(
            f"{artifact_id}: capability_delta.capability_id must bind to "
            "the artifact id"
        )
    if str(delta.get("runner") or "") != runner:
        raise ValueError(
            f"{artifact_id}: capability_delta.runner must bind to "
            f"{runner!r}"
        )
    _require_text(
        str(name), f"{artifact_id}.capability.name",
    )

def _validate_candidate_generator(
    *, artifact_id: str, gap: Mapping[str, Any],
    trigger: Mapping[str, Any], entry: Mapping[str, Any],
) -> None:
    """Closed contract for recall-side candidate generators.

    Unlike the retired census-probe prompt patch, a candidate generator
    is a separate LLM stage whose output is parsed, re-identified and
    re-entered into the normal verification pipeline. It must therefore
    carry the same component discipline as an evidence predicate.
    """
    if gap["affected_stage"] != "census":
        raise ValueError(
            f"{artifact_id}: candidate_generator must affect census, "
            f"got {gap['affected_stage']!r}"
        )
    if gap["gap_type"] != "recall":
        raise ValueError(
            f"{artifact_id}: candidate_generator requires gap_type=recall, "
            f"got {gap['gap_type']!r}"
        )
    features = {
        str(name) for name in trigger.get("packet_features") or ()
    }
    if not features or not features <= set(PACKET_FEATURES):
        raise ValueError(
            f"{artifact_id}: candidate_generator trigger.packet_features "
            "empty or outside the packet-feature vocabulary"
        )
    capability = entry.get("capability")
    capability = capability if isinstance(capability, Mapping) else {}
    _require_text(capability.get("name"), f"{artifact_id}.capability.name")
    _require_text(
        capability.get("failure_mode"),
        f"{artifact_id}.capability.failure_mode",
    )
    input_schema = capability.get("input")
    input_schema = input_schema if isinstance(input_schema, Mapping) else {}
    if not input_schema:
        raise ValueError(
            f"{artifact_id}: capability.input must be a non-empty object"
        )
    unknown_inputs = sorted(set(input_schema) - GENERATOR_INPUT_FIELDS)
    if unknown_inputs:
        raise ValueError(
            f"{artifact_id}: capability input fields {unknown_inputs} "
            "outside the closed generator vocabulary"
        )
    output_schema = capability.get("output")
    output_schema = (
        output_schema if isinstance(output_schema, Mapping) else {}
    )
    if output_schema.get("candidates") != {
        "candidate_id": "text", "claim": "text",
        "locus_event_id": "text", "evidence_refs": ["text"],
    }:
        raise ValueError(
            f"{artifact_id}: candidate_generator capability.output must "
            "declare the closed candidates contract"
        )
    guarantees = capability.get("guarantees")
    if not isinstance(guarantees, list) or not guarantees:
        raise ValueError(
            f"{artifact_id}: capability.guarantees must be a non-empty list"
        )
    implementation = entry.get("implementation")
    implementation = (
        implementation if isinstance(implementation, Mapping) else {}
    )
    runner = _require_text(
        implementation.get("runner"),
        f"{artifact_id}.implementation.runner",
    )
    if runner not in CANDIDATE_GENERATOR_RUNNERS:
        raise ValueError(
            f"{artifact_id}: candidate_generator runner {runner!r} outside "
            f"{sorted(CANDIDATE_GENERATOR_RUNNERS)}"
        )
    _require_text(
        implementation.get("system"),
        f"{artifact_id}.implementation.system",
    )
    delta = entry.get("capability_delta")
    delta = delta if isinstance(delta, Mapping) else {}
    if str(delta.get("capability_id") or "") != artifact_id:
        raise ValueError(
            f"{artifact_id}: capability_delta.capability_id must bind to "
            "the artifact id"
        )
    if str(delta.get("runner") or "") != runner:
        raise ValueError(
            f"{artifact_id}: capability_delta.runner must bind to "
            f"{runner!r}"
        )


def validate_artifact(entry: Mapping[str, Any]) -> None:
    """Closed contract check; any defect raises (fail-closed loading)."""
    artifact_id = _require_text(entry.get("artifact_id"), "artifact_id")
    kind = _require_text(entry.get("kind"), f"{artifact_id}.kind")
    if kind not in ARTIFACT_KINDS:
        raise ValueError(
            f"{artifact_id}: kind {kind!r} has no executor in this package"
        )
    execution_point = _require_text(
        entry.get("execution_point"), f"{artifact_id}.execution_point",
    )
    if execution_point != ARTIFACT_KINDS[kind]:
        raise ValueError(
            f"{artifact_id}: kind {kind!r} must execute at "
            f"{ARTIFACT_KINDS[kind]!r}, got {execution_point!r}"
        )
    gap = entry.get("gap")
    gap = gap if isinstance(gap, Mapping) else {}
    for field in (
        "category", "name", "gap_type", "failure_signature",
        "affected_stage", "expected_capability",
    ):
        _require_text(gap.get(field), f"{artifact_id}.gap.{field}")
    if gap["gap_type"] not in GAP_TYPES:
        raise ValueError(
            f"{artifact_id}: gap.gap_type {gap['gap_type']!r} outside "
            f"{sorted(GAP_TYPES)}"
        )
    if gap["affected_stage"] not in GAP_STAGES:
        raise ValueError(
            f"{artifact_id}: gap.affected_stage {gap['affected_stage']!r} "
            f"outside {sorted(GAP_STAGES)}"
        )
    delta = entry.get("capability_delta")
    delta = delta if isinstance(delta, Mapping) else {}
    for field in ("before_capability", "after_capability"):
        _require_text(
            delta.get(field), f"{artifact_id}.capability_delta.{field}",
        )
    trigger = entry.get("trigger")
    trigger = trigger if isinstance(trigger, Mapping) else {}
    intervention = entry.get("intervention")
    intervention = intervention if isinstance(intervention, Mapping) else {}
    if kind == "prompt_directive":
        features = {
            str(name) for name in trigger.get("packet_features") or ()
        }
        if not features or not features <= set(PACKET_FEATURES):
            raise ValueError(
                f"{artifact_id}: trigger.packet_features empty or outside "
                "the packet-feature vocabulary"
            )
        for field in ("directive", "exclusion"):
            _require_text(
                intervention.get(field), f"{artifact_id}.intervention.{field}",
            )
    elif kind == "adjudication_policy":
        certificate_class = _require_text(
            trigger.get("certificate_class"),
            f"{artifact_id}.trigger.certificate_class",
        )
        if certificate_class not in CERTIFICATE_CLASSES:
            raise ValueError(
                f"{artifact_id}: certificate class {certificate_class!r} "
                "outside the closed vocabulary"
            )
        if certificate_class in POLICY_FREE_CLASSES:
            raise ValueError(
                f"{artifact_id}: class {certificate_class!r} is policy-free "
                "by design"
            )
        action = _require_text(
            intervention.get("action"), f"{artifact_id}.intervention.action",
        )
        if action not in POLICY_ACTIONS:
            raise ValueError(
                f"{artifact_id}: action {action!r} outside the closed "
                "vocabulary"
            )
        precondition = intervention.get("precondition")
        if precondition is not None:
            if not isinstance(precondition, Mapping):
                raise ValueError(
                    f"{artifact_id}: intervention.precondition must be an object"
                )
            if not str(precondition.get("capability_id") or ""):
                raise ValueError(
                    f"{artifact_id}: intervention.precondition.capability_id "
                    "is required"
                )
            expects = precondition.get("expect")
            if not isinstance(expects, list) or not expects:
                raise ValueError(
                    f"{artifact_id}: intervention.precondition.expect must "
                    "be a non-empty list of predicate verdicts"
                )
    elif kind == "candidate_generator":
        _validate_candidate_generator(
            artifact_id=artifact_id, gap=gap, trigger=trigger,
            entry=entry,
        )
    else:
        _validate_evidence_predicate(
            artifact_id=artifact_id, gap=gap, trigger=trigger,
            entry=entry,
        )


def registry_hash(payload: Mapping[str, Any]) -> str:
    """Canonical sha256 over the registry with the hash field removed."""
    hashable = {
        key: value for key, value in payload.items()
        if key != "content_hash"
    }
    canonical = json.dumps(
        hashable, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_registry(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate one registry payload; returns its artifact list."""
    if not isinstance(payload, Mapping):
        raise ValueError("artifact registry must be an object")
    if payload.get("schema_version") != ARTIFACT_REGISTRY_SCHEMA:
        raise ValueError(
            f"artifact registry schema {payload.get('schema_version')!r} "
            f"is not {ARTIFACT_REGISTRY_SCHEMA!r}"
        )
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("artifact registry must contain an artifacts list")
    persisted = str(payload.get("content_hash") or "")
    if persisted and persisted != registry_hash(payload):
        raise ValueError(
            "persisted artifact registry content_hash does not match content"
        )
    for entry in artifacts:
        if not isinstance(entry, Mapping):
            raise ValueError("artifact registry entries must be objects")
        validate_artifact(entry)
    return [dict(entry) for entry in artifacts]


def load_artifact_registry(
    path: str | Path, *, expected_hash: str | None = None,
) -> dict[str, Any]:
    """Load and verify one persisted artifact registry (fail-closed)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_registry(payload)
    if expected_hash is not None:
        computed = registry_hash(payload)
        if computed != expected_hash:
            raise ValueError(
                "loaded artifact registry hash does not match expected_hash"
            )
    return payload


def prompt_directive_entries(
    artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Prompt-directive artifacts in the conventions renderer's shape."""
    entries = []
    for artifact in artifacts:
        if artifact.get("kind") != "prompt_directive":
            continue
        trigger = artifact.get("trigger") or {}
        intervention = artifact.get("intervention") or {}
        entries.append({
            "convention_id": str(artifact.get("artifact_id")),
            "trigger_features": list(
                trigger.get("packet_features") or ()
            ),
            "directive": str(intervention.get("directive") or ""),
            "exclusion": str(intervention.get("exclusion") or ""),
        })
    return entries


def strongest_actions(
    pairs: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """certificate class -> strongest action (withhold dominates)."""
    actions: dict[str, str] = {}
    for certificate_class, action in pairs:
        current = actions.get(certificate_class)
        if action == "withhold_conviction" or current is None:
            actions[certificate_class] = action
    return actions


def policy_actions_of(
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Class -> action map over the deployed adjudication-policy artifacts."""
    pairs = []
    for artifact in artifacts:
        if artifact.get("kind") != "adjudication_policy":
            continue
        trigger = artifact.get("trigger") or {}
        intervention = artifact.get("intervention") or {}
        pairs.append((
            str(trigger.get("certificate_class")),
            str(intervention.get("action")),
        ))
    return strongest_actions(pairs)


def policy_preconditions_of(
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Class -> capability precondition of the strongest deployed action.

    A precondition ties a conviction-suppressing policy to a structured
    capability verdict. Without a verdict on the candidate, the policy
    does not fire (fail-closed to baseline conviction).
    """
    selected: dict[str, tuple[str, Mapping[str, Any] | None]] = {}
    for artifact in artifacts:
        if artifact.get("kind") != "adjudication_policy":
            continue
        trigger = artifact.get("trigger") or {}
        intervention = artifact.get("intervention") or {}
        certificate_class = str(trigger.get("certificate_class") or "")
        action = str(intervention.get("action") or "")
        precondition = intervention.get("precondition")
        precondition = (
            dict(precondition) if isinstance(precondition, Mapping) else None
        )
        current = selected.get(certificate_class)
        if current is None or action == "withhold_conviction":
            selected[certificate_class] = (action, precondition)
    return {
        certificate_class: precondition or {}
        for certificate_class, (_action, precondition) in selected.items()
        if precondition
    }


def apply_policy_actions(
    surviving: Sequence[tuple[str, str | None]],
    actions: Mapping[str, str],
    preconditions: Mapping[str, Mapping[str, Any]] | None = None,
    predicate_outputs: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """Shared conviction semantics of the adjudication execution point.

    ``surviving`` pairs every surviving (upheld) certificate id with its
    bound class — ``None`` when the binder abstained or was never run.
    Returns ``(convicting_ids, fired, skipped)``; a certificate with an
    unknown class keeps baseline behaviour (it convicts). A corroboration
    requirement whose sole-basis premise cannot be verified because a
    fellow surviving certificate is unbound does not fire either — the
    abstention discipline of the binder propagates to the action.

    When a class carries a capability precondition, the action fires only
    if the predicate produced the expected structured verdict for that
    candidate. Otherwise the policy is skipped and the conviction stands:
    a policy can no longer delete a certificate without a capability
    trace on the same candidate.
    """
    preconditions = preconditions or {}
    predicate_outputs = predicate_outputs or {}
    has_content = any(
        certificate_class == BASELINE_CLASS
        for _, certificate_class in surviving
    )
    has_unbound = any(
        certificate_class is None for _, certificate_class in surviving
    )
    convicting: list[str] = []
    fired: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for candidate_id, certificate_class in surviving:
        action = (
            actions.get(certificate_class) if certificate_class else None
        )
        precondition = preconditions.get(certificate_class) if (
            certificate_class and action
        ) else None
        if precondition:
            capability_id = str(precondition.get("capability_id") or "")
            expected = {
                str(value) for value in precondition.get("expect") or ()
            }
            actual = (
                predicate_outputs.get(candidate_id, {}).get(capability_id)
                if capability_id else None
            )
            if actual not in expected:
                skipped.append({
                    "candidate_id": candidate_id,
                    "certificate_class": str(certificate_class),
                    "action": action,
                    "effect": "precondition_unmet",
                })
                convicting.append(candidate_id)
                continue
        if action == "withhold_conviction":
            fired.append({
                "candidate_id": candidate_id,
                "certificate_class": str(certificate_class),
                "action": action,
                "effect": "withheld",
            })
        elif action == "require_corroborating_content_error":
            if has_content:
                skipped.append({
                    "candidate_id": candidate_id,
                    "certificate_class": str(certificate_class),
                    "action": action,
                    "effect": "corroborated",
                })
                convicting.append(candidate_id)
            elif has_unbound:
                skipped.append({
                    "candidate_id": candidate_id,
                    "certificate_class": str(certificate_class),
                    "action": action,
                    "effect": "corroboration_unverifiable",
                })
                convicting.append(candidate_id)
            else:
                fired.append({
                    "candidate_id": candidate_id,
                    "certificate_class": str(certificate_class),
                    "action": action,
                    "effect": "sole_basis_removed",
                })
        else:
            convicting.append(candidate_id)
    return convicting, fired, skipped


# ---------------------------------------------------------------------------
# Runtime certificate-class binder: the perception stage the adjudication
# execution point requires. Label-free by construction — a closed input
# allowlist plus a forbidden-token scan; ``unclassified`` keeps baseline
# adjudication instead of guessing.

BINDING_INPUT_FIELDS = (
    "candidate_id",
    "candidate_claim",
    "certificate_verdict",
    "certificate_rationale",
    "evidence_refs",
    "defense_outcome",
    "packet_events",
)
FORBIDDEN_KEY_TOKENS = (
    "gold", "hallucination", "label", "failure", "prediction",
    "decision", "capability", "gap", "expected",
)
EVENT_CONTENT_CHARS = 1200

BINDER_SYSTEM = (
    "You are the certificate-class binder of a hallucination detector's "
    "policy layer. For every surviving verification certificate listed "
    "below, decide which conviction-basis class it belongs to, using "
    "ONLY the certificate, its candidate and the cited trajectory "
    "events.\n\n"
    "Classes (each definition is a boundary: CONVICTS WHEN is the test "
    "to run, NOT WHEN redirects to the confusable class):\n"
    + "\n".join(
        f"- {name}: {definition}"
        for name, definition in CERTIFICATE_CLASSES.items()
    )
    + "\n- unclassified: the evidence shown does not let you run the "
    "decisive test confidently; the policy layer then keeps baseline "
    "behaviour.\n\n"
    "Decision procedure, per certificate:\n"
    "1. Test every specific class BEFORE content_contradiction — it is "
    "the residual and binds only when every other class's test fails.\n"
    "2. Judge what the asserted deviation actually IS, not how the "
    "certificate words it: a certificate phrasing a wording-level, "
    "near-tie, presentational, absence-based or premise-level "
    "difference as a 'contradiction' or 'failure' still binds to the "
    "specific class whose test the underlying difference passes.\n"
    "3. Before binding content_contradiction on a deviation from a "
    "cited source, put the two sides side by side and run the "
    " neighbour tests on the pair: (a) if the opposing source is the "
    "task text or a user statement — a premise, not an observation — "
    "and the agent's content inherits it, bind premise_conflict; "
    "(b) if the pair differs only in wording, formatting, rounding or "
    "a near-tie margin inside the source's precision, bind "
    "expression_variance; (c) if the added material unpacks, renders "
    "or evaluatively qualifies an observed specific, bind "
    "presentational_addition; (d) if the source merely lacks the "
    "material while no cited slice contradicts it and it is grounded "
    "elsewhere, bind absence_based. content_contradiction needs a "
    "genuine contradiction: a source asserting what the agent's "
    "specific denies, or an agent-authored specific no source "
    "supports.\n"
    "4. Bind every certificate in this case; certificates of the same "
    "case may legitimately get different classes.\n"
    "5. Abstaining is a normal answer for this layer. Answer confidence "
    "high only when your quoted phrase settles the class test by "
    "itself; answer low when two classes stayed plausible or you had "
    "to weigh material not shown. Low confidence falls back to "
    "baseline behaviour — a guessed class suppresses or keeps a "
    "conviction incorrectly.\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"bindings": [{"candidate_id": "c1", "class": "<class name or '
    'unclassified>", "confidence": "<high or low>", "evidence_basis": '
    '"<one sentence quoting the decisive phrase; for premise_conflict '
    'quote the premise sentence>"}]}'
)


def _event_projection(
    packet: Mapping[str, Any], wanted_ids: set[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event in packet.get("events") or []:
        event_id = str(event.get("event_id") or "")
        if event_id not in wanted_ids:
            continue
        interactions = [
            {
                "tool_name": interaction.get("tool_name"),
                "arguments": json.dumps(
                    interaction.get("arguments"),
                    ensure_ascii=False,
                )[:400],
                # The observation is the decisive side of every class
                # boundary test (e.g. expression_variance must see the
                # observed value the restatement is checked against).
                "observation": str(
                    interaction.get("observation") or ""
                )[:EVENT_CONTENT_CHARS],
            }
            for interaction in event.get("tool_interactions") or []
        ]
        events.append({
            "event_id": event_id,
            "role": event.get("role"),
            "content": str(event.get("content") or "")[:EVENT_CONTENT_CHARS],
            "tool_interactions": interactions,
        })
    return events


def binding_entries(
    *,
    census: Sequence[Mapping[str, Any]],
    certificates: Sequence[Mapping[str, Any]],
    surviving: Sequence[str],
    packet: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """One label-free binder input entry per surviving certificate.

    Works identically over a recorded proof (offline bindings) and the
    live court facts (runtime bindings): the caller supplies the same
    census/certificate lists and the surviving (upheld) candidate ids.
    """
    candidates = {
        str(candidate.get("candidate_id")): candidate
        for candidate in census
    }
    proofs = {
        str(certificate.get("candidate_id")): certificate
        for certificate in certificates
    }
    entries: list[dict[str, Any]] = []
    for candidate_id in surviving:
        candidate = candidates.get(candidate_id, {})
        certificate = proofs.get(candidate_id, {})
        refs = [str(ref) for ref in candidate.get("evidence_refs") or []]
        locus = str(candidate.get("locus_event_id") or "")
        entries.append({
            "candidate_id": candidate_id,
            "candidate_claim": str(candidate.get("claim") or ""),
            "certificate_verdict": str(certificate.get("verdict") or ""),
            "certificate_rationale": str(
                certificate.get("rationale") or ""
            ),
            "evidence_refs": refs,
            "defense_outcome": "upheld",
            "packet_events": _event_projection(
                packet, set(refs) | {locus},
            ),
        })
    return entries


def assert_label_free(inputs: Sequence[Mapping[str, Any]]) -> None:
    """Mechanical gold-exclusion: allowlisted keys, no forbidden tokens."""

    def _scan(value: Any, path: str, *, cert_level: bool) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key).lower()
                if cert_level and str(key) not in BINDING_INPUT_FIELDS:
                    raise ValueError(
                        f"certificate field {key!r} outside the allowlist"
                    )
                if any(
                    token in key_text for token in FORBIDDEN_KEY_TOKENS
                ):
                    raise ValueError(f"forbidden key {key!r} at {path}")
                _scan(item, f"{path}.{key}", cert_level=False)
        elif isinstance(value, list):
            cert_children = path.endswith(".certificates")
            for item in value:
                _scan(item, path, cert_level=cert_children)

    for entry in inputs:
        for key in entry:
            if key not in ("case_id", "certificates"):
                raise ValueError(
                    f"binding input field {key!r} outside the allowlist"
                )
        _scan(entry, "", cert_level=False)
        text = json.dumps(entry, ensure_ascii=False).lower()
        for token in (
            "is_hallucination", "hallucination_step", "gold_label",
        ):
            if token in text:
                raise ValueError(
                    f"gold-bearing token {token!r} in binding input "
                    f"{entry.get('case_id')}"
                )


def normalize_binding(
    parsed: Any, *, case_id: str, expected_ids: Sequence[str],
) -> list[dict[str, str]]:
    """Validate one batched binder answer for a whole case.

    Every listed certificate must receive exactly one binding inside the
    closed vocabulary; anything else is a hard error, never a silent
    downgrade. A ``low`` confidence on a classified answer is demoted
    mechanically to ``unclassified`` — the fail-closed abstention gate,
    enforced here rather than trusted to the model's own hesitation.
    """
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{case_id}: binder returned no JSON object")
    answers = parsed.get("bindings")
    if not isinstance(answers, list):
        raise ValueError(f"{case_id}: binder answer lacks a bindings list")
    records: list[dict[str, str]] = []
    for answer in answers:
        if not isinstance(answer, Mapping):
            raise ValueError(f"{case_id}: non-object binding entry")
        candidate_id = str(answer.get("candidate_id") or "")
        if candidate_id not in expected_ids:
            raise ValueError(
                f"{case_id}: binding for unknown certificate "
                f"{candidate_id!r}"
            )
        certificate_class = str(answer.get("class") or "")
        confidence = str(answer.get("confidence") or "")
        basis = str(answer.get("evidence_basis") or "")
        if certificate_class == UNCLASSIFIED:
            record_class, status = UNCLASSIFIED, UNCLASSIFIED
        elif certificate_class in CERTIFICATE_CLASSES:
            if confidence not in ("high", "low"):
                raise ValueError(
                    f"{case_id}/{candidate_id}: classified binding "
                    f"without high/low confidence"
                )
            if not basis:
                raise ValueError(
                    f"{case_id}/{candidate_id}: classified binding "
                    f"without basis"
                )
            if confidence == "low":
                record_class, status = UNCLASSIFIED, UNCLASSIFIED
                basis = (
                    f"low-confidence demotion from {certificate_class}: "
                    f"{basis}"
                )
            else:
                record_class, status = certificate_class, "classified"
        else:
            raise ValueError(
                f"{case_id}/{candidate_id}: answer {certificate_class!r} "
                "outside the closed vocabulary"
            )
        records.append({
            "case_id": case_id,
            "candidate_id": candidate_id,
            "certificate_class": record_class,
            "basis": basis or "binder abstained",
            "binding_status": status,
        })
    answered = {record["candidate_id"] for record in records}
    missing = [cid for cid in expected_ids if cid not in answered]
    if missing:
        raise ValueError(
            f"{case_id}: binder left certificates unanswered: {missing}"
        )
    return records


def bind_surviving(
    *, route: Mapping[str, Any], case_id: str,
    entries: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """One binder provider call over a case's surviving certificates."""
    user = json.dumps(
        {"case_id": case_id, "certificates": list(entries)},
        ensure_ascii=False, indent=1,
    )
    parsed, meta = provider_call(
        route=dict(route), system=BINDER_SYSTEM, user=user,
        purpose="binder",
    )
    records = normalize_binding(
        parsed, case_id=case_id,
        expected_ids=[str(entry["candidate_id"]) for entry in entries],
    )
    return records, meta


__all__ = [
    "ARTIFACT_KINDS",
    "ARTIFACT_REGISTRY_SCHEMA",
    "BASELINE_CLASS",
    "BINDER_SYSTEM",
    "BINDING_INPUT_FIELDS",
    "CERTIFICATE_CLASSES",
    "EVENT_CONTENT_CHARS",
    "FORBIDDEN_KEY_TOKENS",
    "GAP_STAGES",
    "GAP_TYPES",
    "POLICY_ACTIONS",
    "POLICY_FREE_CLASSES",
    "UNCLASSIFIED",
    "apply_policy_actions",
    "assert_label_free",
    "bind_surviving",
    "binding_entries",
    "load_artifact_registry",
    "normalize_binding",
    "policy_actions_of",
    "policy_preconditions_of",
    "prompt_directive_entries",
    "registry_hash",
    "strongest_actions",
    "validate_artifact",
    "validate_registry",
]






