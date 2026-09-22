"""Intervention deployment interface for detector (self-evolution hooks).

This is the detector-side half of the BEHAVIOR self-evolution contract. An
intervention is only executable if its hook and runner are registered in this
module; anything else fails closed at load time.

The four intervention types and their write permissions:

- ``text_boundary``   -> only appends directive + NOT-WHEN to a ``*.system`` hook;
- ``evidence_check``  -> only appends ``proof.evidence`` entries;
- ``recall_trigger``  -> only forces the existing recall net for matching
  judge-negative cases (a recall upgrade still needs defense);
- ``decision_guard``  -> only emits a closed-vocabulary adjudication action.

L1 can never touch authority/terminal fields and L1 triggers can only read
``packet_features``; ``decision_guard`` is the only L2 action writer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.packet import PACKET_FEATURE_NAMES, packet_features
from detector.predicate_context import judge_predicate_context, validate_program_hook
from detector.predicate_dsl import (
    PROGRAM_RUNNER,
    predicate_program_hash,
    run_predicate_program,
    validate_predicate_program,
)
from detector.predicates import (
    PREDICATES,
    PREDICATE_CONVICTION_VERDICTS,
    PREDICATE_VERDICTS,
    run_predicate,
)

INTERVENTION_REGISTRY_SCHEMA = "detector.intervention_registry.v1"
INTERVENTION_SCHEMA = "detector.intervention.v1"

INTERVENTION_TYPES = frozenset({
    "evidence_check", "text_boundary", "decision_guard", "recall_trigger",
})

HOOKS: dict[str, str] = {
    "judge.system": "L1",
    "judge.post": "L1",
    "recall.trigger": "L1",
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
RECALL_TRIGGER_HOOK = "recall.trigger"

GUARD_ACTIONS = frozenset({
    "withhold_conviction",
    "restore_judge_conviction",
    "require_full_view_defense",
    "convict_mechanical_violation",
})

TARGET_POLARITIES = frozenset({"recall", "precision", "boundary"})

# What a candidate claims to improve. ``detection`` is the original binary
# objective and stays the default when the field is absent, so every existing
# registry keeps its exact meaning. ``localization`` claims the responsible
# step improves on cases that are already convicted correctly; ``joint``
# requires at least one objective to improve while the other does not
# regress. The two objectives are scored by different gates and their values
# must not be added into a single weighted number.
TARGET_KINDS = frozenset({"detection", "localization", "joint"})

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
    "final_delivery_present": frozenset({
        "present", "missing", "not_applicable",
    }),
}

_EVIDENCE_HOOK_BY_TYPE: dict[str, frozenset[str]] = {
    "text_boundary": SYSTEM_HOOKS,
    "evidence_check": EVIDENCE_HOOKS,
    "decision_guard": frozenset({GUARD_HOOK}),
    "recall_trigger": frozenset({RECALL_TRIGGER_HOOK}),
}

# ---------------------------------------------------------------------------
# Runner registry
# ---------------------------------------------------------------------------
# Every executor an intervention may name declares what it is and what it may
# do. Before this the runner space was an implicit if/else: anything that was
# not ``typed_dsl`` fell to the built-in predicate table, and a new executor
# could only be added by editing two dispatch sites -- a shape where the
# *absence* of a declaration was the same as "use the default".
#
# Each spec answers the questions a gate or a reviewer actually needs:
#
#   supported_hooks       which hooks this runner may be bound to
#   intervention_types    which intervention kinds may carry it
#   input_schema          what the body must declare
#   deterministic         same input -> same output, no external state
#   learned               reads an artifact whose content must be pinned
#   requires_artifact     the entry must carry artifact{id,digest}
#   terminal_authority    whether its verdicts may ever authorize a conviction
#   metamorphic_classes   which §15.2 invariances the runner must survive
#
# ``terminal_authority`` is the safety-relevant one. A learned executor is
# registered ``evidence_only``: it may report, and it may never be the thing
# that convicts. That is plan §12.3's rule ("learned executor only outputs
# evidence, no independent terminal conviction") expressed where it can be
# enforced rather than in prose.
#
# ``metamorphic_classes`` is the other half of the same idea: declaring what a
# runner *is* only means something if something also requires what it must not
# do. The vocabulary lives here, next to the declaration that uses it, and is
# executed by ``evolution.metamorphic`` (which imports these names rather than
# keeping a second copy). The class names are §15.2's transformations.

TERMINAL_NONE = "none"
TERMINAL_EVIDENCE_ONLY = "evidence_only"
TERMINAL_CONVICTION = "conviction"

#: The §15.2 metamorphic transformations. A runner declares the subset that
#: applies to it, and ``evolution.metamorphic`` enforces exactly that subset.
METAMORPHIC_CLASSES = frozenset({
    "case_id_rename",
    "source_rename",
    "irrelevant_tool_call",
    "neighbor_surface_token",
    "semantic_equivalence",
    "localization_event_move",
})

#: Every runner reads a packet, so every runner owes the three packet-level
#: invariances; the two renaming classes belong to the mechanism layer that
#: every candidate inherits. A learned runner adds semantic equivalence.
_PACKET_CLASSES = frozenset({
    "irrelevant_tool_call", "neighbor_surface_token", "localization_event_move",
})
_IDENTITY_CLASSES = frozenset({"case_id_rename", "source_rename"})


class RunnerSpec:
    """What one executor is and what it may do."""

    __slots__ = (
        "name", "supported_hooks", "intervention_types", "input_schema",
        "deterministic", "learned", "requires_artifact", "terminal_authority",
        "metamorphic_classes", "description",
    )

    def __init__(
        self,
        *,
        name: str,
        supported_hooks: frozenset[str],
        intervention_types: frozenset[str],
        input_schema: str,
        deterministic: bool,
        learned: bool,
        requires_artifact: bool,
        terminal_authority: str,
        metamorphic_classes: frozenset[str] | None = None,
        description: str = "",
    ) -> None:
        if terminal_authority not in {
            TERMINAL_NONE, TERMINAL_EVIDENCE_ONLY, TERMINAL_CONVICTION,
        }:
            raise ValueError(
                f"runner {name!r}: unknown terminal_authority "
                f"{terminal_authority!r}"
            )
        if learned and terminal_authority == TERMINAL_CONVICTION:
            # A learned runner's decisions come from an artifact that can be
            # swapped. Allowing it to convict terminally would make the
            # detector's verdict a function of whatever weights happen to be
            # loaded, with no calibration gate in between.
            raise ValueError(
                f"runner {name!r}: a learned runner may not hold terminal "
                "conviction authority"
            )
        declared = (
            set(_PACKET_CLASSES)
            if metamorphic_classes is None
            else {str(item) for item in metamorphic_classes}
        )
        unknown = declared - METAMORPHIC_CLASSES
        if unknown:
            raise ValueError(
                f"runner {name!r}: unknown metamorphic class(es) "
                f"{sorted(unknown)}; known classes are "
                f"{sorted(METAMORPHIC_CLASSES)}"
            )
        # A runner that reads a learned artifact is exactly the one whose
        # answer can depend on how a sentence is written, so it cannot opt out
        # of the semantic-equivalence class. The requirement is added rather
        # than checked-and-rejected, so a declaration cannot silently omit it.
        if learned:
            declared = declared | {"semantic_equivalence"}
        self.metamorphic_classes = frozenset(declared)
        self.name = name
        self.supported_hooks = frozenset(supported_hooks)
        self.intervention_types = frozenset(intervention_types)
        self.input_schema = input_schema
        self.deterministic = bool(deterministic)
        self.learned = bool(learned)
        self.requires_artifact = bool(requires_artifact)
        self.terminal_authority = terminal_authority
        self.description = description

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "supported_hooks": sorted(self.supported_hooks),
            "intervention_types": sorted(self.intervention_types),
            "input_schema": self.input_schema,
            "deterministic": self.deterministic,
            "learned": self.learned,
            "requires_artifact": self.requires_artifact,
            "terminal_authority": self.terminal_authority,
            "metamorphic_classes": sorted(self.metamorphic_classes),
            "description": self.description,
        }


# The semantic executor's verdict vocabulary. ``abstained`` is a first-class
# outcome: a semantic runner that cannot decide must say so, and the vocabulary
# makes "I could not tell" distinguishable from "I checked and it holds".
SEMANTIC_VERDICTS = frozenset({"supported", "contradicted", "abstained"})

RUNNER_REGISTRY: dict[str, RunnerSpec] = {
    "deterministic": RunnerSpec(
        name="deterministic",
        supported_hooks=EVIDENCE_HOOKS | frozenset({RECALL_TRIGGER_HOOK}),
        intervention_types=frozenset({"evidence_check", "recall_trigger"}),
        input_schema=(
            "body.predicate names a built-in predicate; body.output_verdicts "
            "must equal that predicate's full verdict vocabulary"
        ),
        deterministic=True,
        learned=False,
        requires_artifact=False,
        terminal_authority=TERMINAL_CONVICTION,
        # A built-in predicate reads the packet and is not allowed to notice
        # how the case was identified, so it owes the identity classes as well
        # as the packet ones.
        metamorphic_classes=_PACKET_CLASSES | _IDENTITY_CLASSES,
        description=(
            "A built-in mechanical predicate. Its verdicts are computed from "
            "the packet by fixed code, so a conviction verdict proves a task-"
            "level mechanical violation."
        ),
    ),
    PROGRAM_RUNNER: RunnerSpec(
        name=PROGRAM_RUNNER,
        supported_hooks=EVIDENCE_HOOKS | frozenset({RECALL_TRIGGER_HOOK}),
        intervention_types=frozenset({"evidence_check", "recall_trigger"}),
        input_schema=(
            "body.predicate_definition carries a validated "
            "detector.predicate_program.v1 program whose name equals "
            "body.predicate"
        ),
        deterministic=True,
        learned=False,
        requires_artifact=False,
        terminal_authority=TERMINAL_CONVICTION,
        metamorphic_classes=_PACKET_CLASSES | _IDENTITY_CLASSES,
        description=(
            "An inline typed-DSL program. Non-Turing-complete and interpreted "
            "by fixed operators, so it is deterministic and may hold "
            "conviction verdicts."
        ),
    ),
    "typed_semantic": RunnerSpec(
        name="typed_semantic",
        # Evidence only, and initially evidence_check only: a semantic
        # judgment routed into recall control flow would let a learned model
        # decide *when* to run the recall net, which is a stronger power than
        # reporting an observation.
        supported_hooks=EVIDENCE_HOOKS,
        intervention_types=frozenset({"evidence_check"}),
        input_schema=(
            "body.predicate names the task schema the executor judges; "
            "body.output_verdicts must equal the semantic verdict vocabulary"
        ),
        deterministic=False,
        learned=True,
        requires_artifact=True,
        terminal_authority=TERMINAL_EVIDENCE_ONLY,
        # ``semantic_equivalence`` is added by the constructor for every
        # learned runner; it is listed here to be read, not because the gate
        # depends on the listing.
        metamorphic_classes=_PACKET_CLASSES | _IDENTITY_CLASSES | {
            "semantic_equivalence",
        },
        description=(
            "A typed semantic judgment produced by an injected executor. "
            "Evidence only: it can never authorize a terminal conviction, and "
            "its artifact must be digest-pinned."
        ),
    ),
}


def runner_spec(runner: str) -> RunnerSpec | None:
    """The declaration for a runner name, or None when it is unregistered."""
    return RUNNER_REGISTRY.get(str(runner or ""))


class SemanticExecutor:
    """Base class for the injected executor behind ``typed_semantic``.

    A concrete executor owns a model artifact and answers typed judgments. Two
    properties are load-bearing:

    * ``artifact_id`` / ``artifact_digest`` say *which* artifact this executor
      actually loaded, so an entry's declared digest can be checked against it
      rather than trusted. Pinning a digest in an entry is only meaningful if
      something compares it with reality at run time.
    * ``judge`` returns a declared verdict and may return ``abstained``. It
      never returns a conviction; the runner's ``terminal_authority`` is
      ``evidence_only``, so its output cannot reach a terminal decision.

    ``learned = True`` is what the registry reads to require an artifact; a
    subclass must set both identity fields or be refused when it is used.
    """

    name = ""

    def __init__(self, *, artifact_id: str, artifact_digest: str) -> None:
        self.artifact_id = str(artifact_id or "")
        self.artifact_digest = str(artifact_digest or "")

    def judge(
        self,
        predicate: str,
        packet: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    def identity(self) -> dict[str, str]:
        return {"id": self.artifact_id, "digest": self.artifact_digest}


def runner_declarations() -> list[dict[str, Any]]:
    """Every registered runner, for a report or an audit."""
    return [
        RUNNER_REGISTRY[name].as_dict() for name in sorted(RUNNER_REGISTRY)
    ]


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


def _effective_fact_vocab(
    custom_fact_vocab: Mapping[str, Any] | None,
) -> dict[str, frozenset[str]]:
    vocab = {name: frozenset(values) for name, values in FACT_NAMES.items()}
    for name, values in (custom_fact_vocab or {}).items():
        vocab[str(name)] = frozenset(str(item) for item in values or ())
    return vocab


def _validate_trigger(
    entry: Mapping[str, Any],
    intervention_type: str,
    *,
    custom_fact_vocab: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
    if facts and intervention_type not in {"decision_guard", "recall_trigger"}:
        raise ValueError(
            f"{entry.get('intervention_id')}: only decision_guard or "
            "recall_trigger may use trigger.facts"
        )
    vocab = _effective_fact_vocab(custom_fact_vocab)
    parsed_facts = []
    for fact in facts:
        if "=" not in fact:
            raise ValueError(
                f"{entry.get('intervention_id')}: trigger fact {fact!r} must be name=value"
            )
        name, value = fact.split("=", 1)
        if name not in vocab:
            raise ValueError(
                f"{entry.get('intervention_id')}: unknown trigger fact {name!r}"
            )
        if value not in vocab[name]:
            raise ValueError(
                f"{entry.get('intervention_id')}: fact {name}={value!r} "
                f"outside {sorted(vocab[name])}"
            )
        parsed_facts.append((name, value))
    return {
        "packet_features": features,
        "facts": parsed_facts,
    }


def _validate_fact_list(
    entry: Mapping[str, Any],
    value: Any,
    field: str,
    *,
    custom_fact_vocab: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    facts = _clean_list(value, field)
    vocab = _effective_fact_vocab(custom_fact_vocab)
    parsed: list[tuple[str, str]] = []
    for fact in facts:
        if "=" not in fact:
            raise ValueError(
                f"{entry.get('intervention_id')}: {field} item {fact!r} "
                "must be name=value"
            )
        name, fact_value = fact.split("=", 1)
        if name not in vocab:
            raise ValueError(
                f"{entry.get('intervention_id')}: unknown fact name {name!r}"
            )
        if fact_value not in vocab[name]:
            raise ValueError(
                f"{entry.get('intervention_id')}: fact {name}={fact_value!r} "
                f"outside {sorted(vocab[name])}"
            )
        parsed.append((name, fact_value))
    return parsed


def inline_predicate_programs(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Collect the typed-DSL predicate programs embedded in evidence checks.

    Duplicate names with different programs are rejected: the runtime can only
    bind one deterministic executor per predicate name in a registry.
    """
    programs: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("type") or "") != "evidence_check":
            continue
        body = entry.get("body")
        body = body if isinstance(body, Mapping) else {}
        runner = str(body.get("runner") or "")
        raw = body.get("predicate_definition")
        if raw is None and runner != PROGRAM_RUNNER:
            continue
        if runner != PROGRAM_RUNNER:
            raise ValueError(
                f"{entry.get('intervention_id')}: runner {runner!r} needs a "
                "body.predicate_definition"
            )
        program = validate_predicate_program(raw)
        name = str(program.get("name") or "")
        predicate = str(body.get("predicate") or "")
        if predicate != name:
            raise ValueError(
                f"{entry.get('intervention_id')}: body.predicate {predicate!r} "
                f"must equal predicate_definition.name {name!r}"
            )
        previous = programs.get(name)
        if previous is not None and predicate_program_hash(previous) != predicate_program_hash(program):
            raise ValueError(
                f"{entry.get('intervention_id')}: predicate program {name!r} "
                "is redefined with a different program in one registry"
            )
        programs[name] = program
    return programs


def inline_trigger_programs(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Collect typed-DSL programs embedded in recall_trigger entries."""
    programs: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("type") or "") != "recall_trigger":
            continue
        body = entry.get("body")
        body = body if isinstance(body, Mapping) else {}
        runner = str(body.get("runner") or "")
        if runner != PROGRAM_RUNNER:
            continue
        program = validate_predicate_program(body.get("predicate_definition"))
        name = str(program.get("name") or "")
        predicate = str(body.get("predicate") or "")
        if predicate != name:
            raise ValueError(
                f"{entry.get('intervention_id')}: body.predicate {predicate!r} "
                f"must equal predicate_definition.name {name!r}"
            )
        previous = programs.get(name)
        if previous is not None and predicate_program_hash(previous) != predicate_program_hash(program):
            raise ValueError(
                f"{entry.get('intervention_id')}: recall-trigger program "
                f"{name!r} is redefined with a different program"
            )
        programs[name] = program
    return programs


def custom_fact_vocab_for(
    programs: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    return {
        str(program.get("name") or ""): list(program.get("verdicts") or ())
        for program in programs.values()
        if program.get("name")
    }


def custom_predicate_verdicts_for(
    programs: Mapping[str, Mapping[str, Any]],
) -> dict[str, frozenset[str]]:
    return {
        str(program.get("name") or ""): frozenset(program.get("verdicts") or ())
        for program in programs.values()
        if program.get("name")
    }


def custom_conviction_verdicts_for(
    programs: Mapping[str, Mapping[str, Any]],
) -> dict[str, frozenset[str]]:
    return {
        str(program.get("name") or ""): frozenset(
            program.get("conviction_verdicts") or ()
        )
        for program in programs.values()
        if program.get("name")
    }


def validate_intervention_batch(
    entries: Sequence[Mapping[str, Any]],
    *,
    require_executable_bounds: bool = False,
) -> None:
    """Validate a proposal/registry batch with cross-entry capability binding.

    Inline predicate programs define new fact names and conviction verdicts
    for the guards that consume them in the same batch. This is the only
    cross-entry dependency in the otherwise closed intervention contract.
    """
    clean = [entry for entry in entries if isinstance(entry, Mapping)]
    programs = inline_predicate_programs(clean)
    trigger_programs = inline_trigger_programs(clean)
    for name, trigger_program in trigger_programs.items():
        evidence_program = programs.get(name)
        if (
            evidence_program is not None
            and predicate_program_hash(evidence_program)
            != predicate_program_hash(trigger_program)
        ):
            raise ValueError(
                f"predicate program {name!r} is shared by an evidence_check "
                "and a recall_trigger with different definitions"
            )
    fact_vocab = custom_fact_vocab_for(programs)
    custom_verdicts = custom_predicate_verdicts_for(programs)
    custom_convictions = custom_conviction_verdicts_for(programs)
    # Predicate-named facts only exist when a same-family evidence_check ran
    # and wrote them. A guard that consumes such a fact without that evidence
    # partner can structurally never fire; reject it before gate budget.
    evidence_sources = {
        (
            str((entry.get("provenance") or {}).get("failure_family") or ""),
            str((entry.get("body") or {}).get("predicate") or ""),
        )
        for entry in clean
        if entry.get("type") == "evidence_check"
    }
    predicate_fact_names = set(PREDICATE_VERDICTS) | set(programs)
    for entry in clean:
        if entry.get("type") != "decision_guard":
            continue
        family = str((entry.get("provenance") or {}).get("failure_family") or "")
        consumed = {
            str(fact).split("=", 1)[0]
            for fact in (entry.get("trigger") or {}).get("facts") or ()
            if "=" in str(fact)
        } | {
            str(fact).split("=", 1)[0]
            for fact in ((entry.get("body") or {}).get("action_not_when_facts") or ())
            if "=" in str(fact)
        }
        missing = sorted(
            name for name in consumed
            if name in predicate_fact_names and (family, name) not in evidence_sources
        )
        if missing:
            raise ValueError(
                f"{entry.get('intervention_id')}: guard consumes predicate "
                f"fact(s) {missing} but no same-family evidence_check writes "
                "them in this batch"
            )
    for entry in clean:
        validate_intervention(
            entry,
            require_executable_bounds=require_executable_bounds,
            custom_fact_vocab=fact_vocab,
            custom_predicate_verdicts=custom_verdicts,
            custom_conviction_verdicts=custom_convictions,
        )


def _validate_runner_binding(
    entry: Mapping[str, Any],
    intervention_type: str,
    hook: str,
) -> None:
    """Check the entry against its runner's declared capability contract.

    The declaration only means something if something enforces it. These are
    the two reachable parts of the contract at validation time: which hooks a
    runner may bind to, and which intervention kinds may carry it. Both matter
    for ``typed_semantic``, whose declaration deliberately excludes
    ``recall.trigger`` -- a learned judgment must not decide *when* the recall
    net runs, only report what it observes.
    """
    body = entry.get("body")
    body = body if isinstance(body, Mapping) else {}
    runner = str(body.get("runner") or "")
    if not runner:
        return
    intervention_id = str(entry.get("intervention_id") or "?")
    spec = runner_spec(runner)
    if spec is None:
        raise ValueError(
            f"{intervention_id}: runner {runner!r} is not registered; known "
            f"runners are {sorted(RUNNER_REGISTRY)}"
        )
    if hook not in spec.supported_hooks:
        raise ValueError(
            f"{intervention_id}: runner {runner!r} does not support hook "
            f"{hook!r}; it declares {sorted(spec.supported_hooks)}"
        )
    if intervention_type not in spec.intervention_types:
        raise ValueError(
            f"{intervention_id}: runner {runner!r} cannot be carried by a "
            f"{intervention_type!r} intervention; it declares "
            f"{sorted(spec.intervention_types)}"
        )


def _validate_artifact_binding(
    entry: Mapping[str, Any],
    intervention_id: str,
) -> dict[str, str]:
    """Check the artifact a learned runner's entry declares.

    The runner declaration already says an artifact is required; this is the
    entry-level half. Both fields must be present and non-empty, because an
    entry cannot be identified by a runner name alone when the behaviour comes
    from what the runner loaded (plan §23.2: learned artifacts must have a
    digest).
    """
    raw = entry.get("artifact")
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"{intervention_id}: this runner reads a learned artifact, so the "
            "entry must declare artifact{id, digest}"
        )
    binding: dict[str, str] = {}
    for field in ("id", "digest"):
        value = str(raw.get(field) or "").strip()
        if not value:
            raise ValueError(
                f"{intervention_id}: artifact.{field} is required and must be "
                "non-empty"
            )
        binding[field] = value
    return binding


def _validate_body(
    entry: Mapping[str, Any],
    intervention_type: str,
    *,
    require_executable_bounds: bool = False,
    custom_fact_vocab: Mapping[str, Any] | None = None,
    custom_predicate_verdicts: Mapping[str, Any] | None = None,
    custom_conviction_verdicts: Mapping[str, Any] | None = None,
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
        runner = _require_text(body.get("runner"), f"{intervention_id}.body.runner")
        if predicate in PREDICATES:
            if runner != "deterministic":
                raise ValueError(
                    f"{intervention_id}: built-in predicate {predicate!r} only "
                    f"has the deterministic runner"
                )
            if "predicate_definition" in body:
                raise ValueError(
                    f"{intervention_id}: built-in predicate {predicate!r} must "
                    "not carry predicate_definition"
                )
            expected = set(PREDICATE_VERDICTS[predicate])
        elif runner == PROGRAM_RUNNER:
            raw = body.get("predicate_definition")
            program = validate_predicate_program(raw)
            if str(program.get("name") or "") != predicate:
                raise ValueError(
                    f"{intervention_id}: predicate_definition.name "
                    f"{program.get('name')!r} must equal body.predicate {predicate!r}"
                )
            expected = set(program.get("verdicts") or ())
        elif runner in RUNNER_REGISTRY:
            # A registered runner whose predicate the entry did not define.
            # The runner is known, so the fix is about the entry, and saying
            # "unregistered" here would send the caller after the wrong thing.
            expected = set(SEMANTIC_VERDICTS)
        else:
            registered = runner_spec(runner)
            if registered is None:
                raise ValueError(
                    f"{intervention_id}: runner {runner!r} is not registered; "
                    f"known runners are {sorted(RUNNER_REGISTRY)}"
                )
            raise ValueError(
                f"{intervention_id}: predicate {predicate!r} has no registered "
                "executor; use a built-in predicate, carry a validated "
                f"{PROGRAM_RUNNER} predicate_definition, or name a registered "
                "semantic runner"
            )
        verdicts = set(body.get("output_verdicts") or ())
        if verdicts != expected:
            raise ValueError(
                f"{intervention_id}: output_verdicts must declare the full "
                f"executor vocabulary {sorted(expected)}"
            )
        spec = runner_spec(runner)
        if spec is not None and spec.requires_artifact:
            _validate_artifact_binding(entry, intervention_id)
        return {
            "predicate": predicate,
            "runner": runner,
            "output_verdicts": verdicts,
            **(
                {"predicate_definition": validate_predicate_program(
                    body.get("predicate_definition")
                )}
                if runner == PROGRAM_RUNNER else {}
            ),
        }
    if intervention_type == "recall_trigger":
        predicate = _require_text(body.get("predicate"), f"{intervention_id}.body.predicate")
        runner = _require_text(body.get("runner"), f"{intervention_id}.body.runner")
        if predicate in PREDICATES:
            if runner != "deterministic":
                raise ValueError(
                    f"{intervention_id}: built-in predicate {predicate!r} only "
                    f"has the deterministic runner"
                )
            if "predicate_definition" in body:
                raise ValueError(
                    f"{intervention_id}: built-in predicate {predicate!r} must "
                    "not carry predicate_definition"
                )
            expected = set(PREDICATE_VERDICTS[predicate])
        elif runner == PROGRAM_RUNNER:
            raw = body.get("predicate_definition")
            program = validate_predicate_program(raw)
            if str(program.get("name") or "") != predicate:
                raise ValueError(
                    f"{intervention_id}: predicate_definition.name "
                    f"{program.get('name')!r} must equal body.predicate {predicate!r}"
                )
            expected = set(program.get("verdicts") or ())
        else:
            # Name the runner's own restriction when that is the reason. A
            # proposer reads this text in its repair loop, and "no registered
            # executor" for a runner that *is* registered but may not drive
            # recall control flow sends it looking for the wrong problem.
            spec = runner_spec(runner)
            if spec is not None and "recall_trigger" not in spec.intervention_types:
                raise ValueError(
                    f"{intervention_id}: runner {runner!r} cannot be carried by "
                    f"a recall_trigger; it declares "
                    f"{sorted(spec.intervention_types)}. A runner whose "
                    "verdicts cannot convict is not allowed to decide when the "
                    "recall net runs."
                )
            raise ValueError(
                f"{intervention_id}: predicate {predicate!r} has no registered "
                "executor; use a built-in predicate or carry a validated "
                f"{PROGRAM_RUNNER} predicate_definition"
            )
        verdicts = set(body.get("output_verdicts") or ())
        if verdicts != expected:
            raise ValueError(
                f"{intervention_id}: output_verdicts must declare the full "
                f"executor vocabulary {sorted(expected)}"
            )
        spec = runner_spec(runner)
        if spec is not None and spec.requires_artifact:
            _validate_artifact_binding(entry, intervention_id)
        trigger_verdicts = set(body.get("trigger_verdicts") or ())
        if not trigger_verdicts or not trigger_verdicts <= verdicts:
            raise ValueError(
                f"{intervention_id}: trigger_verdicts must be a non-empty "
                "subset of output_verdicts"
            )
        require_judge_negative = bool(body.get("require_judge_negative", True))
        return {
            "predicate": predicate,
            "runner": runner,
            "output_verdicts": verdicts,
            "trigger_verdicts": trigger_verdicts,
            "require_judge_negative": require_judge_negative,
            **( 
                {"predicate_definition": validate_predicate_program(
                    body.get("predicate_definition")
                )}
                if runner == PROGRAM_RUNNER else {}
            ),
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
        custom_fact_vocab=custom_fact_vocab,
    )
    evidence_predicate = str(body.get("evidence_predicate") or "").strip()
    if action == "convict_mechanical_violation":
        custom_verdicts = dict(custom_predicate_verdicts or {})
        if evidence_predicate not in PREDICATES and evidence_predicate not in custom_verdicts:
            raise ValueError(
                f"{intervention_id}: convict_mechanical_violation requires a "
                "valid body.evidence_predicate"
            )
        trigger_facts = _validate_trigger(
            entry,
            intervention_type,
            custom_fact_vocab=custom_fact_vocab,
        )["facts"]
        bound_values = {
            value for name, value in trigger_facts if name == evidence_predicate
        }
        if not bound_values:
            raise ValueError(
                f"{intervention_id}: trigger.facts must bind evidence_predicate "
                f"{evidence_predicate!r}"
            )
        allowed_convictions = (
            PREDICATE_CONVICTION_VERDICTS.get(evidence_predicate, frozenset())
            if evidence_predicate in PREDICATES
            else frozenset(str(item) for item in (
                dict(custom_conviction_verdicts or {}).get(
                    evidence_predicate
                ) or ()
            ))
        )
        if not bound_values <= allowed_convictions:
            raise ValueError(
                f"{intervention_id}: convict_mechanical_violation must bind "
                f"a conviction-authorized verdict for {evidence_predicate!r}"
            )
        trigger_facts = set(trigger_facts)
        required_context = {
            ("judge_positive", "false"),
            ("positive_source", "none"),
        }
        if not required_context <= trigger_facts:
            raise ValueError(
                f"{intervention_id}: convict_mechanical_violation requires "
                "judge_positive=false and positive_source=none"
            )
    return {
        "action": action,
        "action_not_when": action_not_when,
        "action_not_when_facts": not_when_facts,
        "evidence_predicate": evidence_predicate,
    }


def validate_intervention(
    entry: Mapping[str, Any],
    *,
    require_executable_bounds: bool = False,
    custom_fact_vocab: Mapping[str, Any] | None = None,
    custom_predicate_verdicts: Mapping[str, Any] | None = None,
    custom_conviction_verdicts: Mapping[str, Any] | None = None,
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
    _validate_trigger(
        entry,
        intervention_type,
        custom_fact_vocab=custom_fact_vocab,
    )
    _validate_body(
        entry,
        intervention_type,
        require_executable_bounds=require_executable_bounds,
        custom_fact_vocab=custom_fact_vocab,
        custom_predicate_verdicts=custom_predicate_verdicts,
        custom_conviction_verdicts=custom_conviction_verdicts,
    )
    _validate_runner_binding(entry, intervention_type, hook)
    program = (entry.get("body") or {}).get("predicate_definition")
    if program:
        validate_program_hook(program, hook)
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
    # Absent means "detection": every intervention written before this field
    # existed claimed the binary objective, and its meaning must not change.
    target_kind = str(claim.get("target_kind") or "detection")
    if target_kind not in TARGET_KINDS:
        raise ValueError(
            f"{intervention_id}: target_kind {target_kind!r} outside "
            f"{sorted(TARGET_KINDS)}"
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
    validate_intervention_batch(
        entries, require_executable_bounds=require_executable_bounds,
    )
    cleaned: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in entries:
        entry_id = str(entry.get("intervention_id") or "")
        if entry_id in seen_ids:
            raise ValueError(f"duplicate intervention_id {entry_id}")
        seen_ids.add(entry_id)
        cleaned.append(dict(entry))
    return cleaned


def load_intervention_registry(
    path: str | Path, *, require_executable_bounds: bool = False,
) -> dict[str, Any]:
    """Load a registry from disk with content-hash verification.

    A registry read from disk is a trusted deployment input, so the content
    hash is REQUIRED here: an edited registry that dropped (or never carried)
    its hash would otherwise load silently and defeat the only tamper check
    the format has. In-memory payloads built by
    :func:`build_intervention_registry` compute their hash after validation,
    which is why this requirement lives on the loader, not in
    :func:`validate_registry`.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    persisted = (
        str(payload.get("content_hash") or "")
        if isinstance(payload, Mapping) else ""
    )
    if not persisted:
        raise ValueError(
            f"intervention registry {str(path)!r} has no content_hash; refusing "
            "to load an unverifiable registry from disk"
        )
    # The registry-level hash is computed over the whole payload including
    # every entry, so it transitively protects entry content; entries do not
    # each need their own hash for the loader to be tamper-evident.
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

    def __init__(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        semantic_executor: "SemanticExecutor | None" = None,
    ) -> None:
        self.entries = [dict(entry) for entry in entries]
        self.fired: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self.facts: dict[str, str] = {}
        self.guard_actions_applied: list[str] = []
        self.mechanical_guard_evidence: list[dict[str, Any]] = []
        self.policy_conflict = False
        self.registry_hash = ""
        self.predicate_programs = inline_predicate_programs(self.entries)
        self.recall_trigger_programs = inline_trigger_programs(self.entries)
        self.predicate_conviction_verdicts = {
            **PREDICATE_CONVICTION_VERDICTS,
            **custom_conviction_verdicts_for(self.predicate_programs),
        }
        self.semantic_executor = semantic_executor

    def _semantic_result(
        self,
        entry: Mapping[str, Any],
        packet: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Run one ``typed_semantic`` judgment, or fail closed.

        The executor's own identity is compared with the entry's declared
        artifact before it is used. Without that comparison the pinned digest
        would be decoration: a loader could hand over model B while the entry
        names model A, and every downstream check would still pass because it
        only ever saw the declared value.
        """
        intervention_id = str(entry.get("intervention_id") or "?")
        executor = self.semantic_executor
        if executor is None:
            raise ValueError(
                f"{intervention_id}: typed_semantic needs an executor; none was "
                "supplied to this detection call. A learned judgment cannot be "
                "simulated by the deterministic default."
            )
        declared = entry.get("artifact") or {}
        actual = executor.identity()
        for field in ("id", "digest"):
            if not str(declared.get(field) or ""):
                raise ValueError(
                    f"{intervention_id}: typed_semantic entry declares no "
                    f"artifact.{field}"
                )
            if str(declared.get(field)) != str(actual.get(field) or ""):
                raise ValueError(
                    f"{intervention_id}: the executor loaded artifact "
                    f"{actual.get(field)!r} but the entry pins "
                    f"{declared.get(field)!r}; refusing to attribute results to "
                    "an artifact that did not produce them"
                )
        body = entry.get("body") or {}
        predicate = str(body.get("predicate") or "")
        result = executor.judge(predicate, packet, dict(context))
        if not isinstance(result, Mapping):
            raise ValueError(
                f"{intervention_id}: the semantic executor must return a mapping"
            )
        verdict = str(result.get("verdict") or "")
        if verdict not in SEMANTIC_VERDICTS:
            raise ValueError(
                f"{intervention_id}: semantic verdict {verdict!r} is outside the "
                f"declared vocabulary {sorted(SEMANTIC_VERDICTS)}"
            )
        return {
            "predicate": predicate,
            "verdict": verdict,
            "detail": str(result.get("detail") or ""),
            "runner": "typed_semantic",
        }

    @classmethod
    def from_registry(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        semantic_executor: "SemanticExecutor | None" = None,
    ) -> "InterventionPlan":
        if payload is None:
            return cls([], semantic_executor=semantic_executor)
        entries = validate_registry(payload, require_executable_bounds=True)
        plan = cls(entries, semantic_executor=semantic_executor)
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

    def _run_predicate(
        self,
        entry: Mapping[str, Any],
        runner: str,
        predicate: str,
        packet: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Dispatch one predicate to its declared executor.

        A lookup, not a fallthrough: before this, anything that was not
        ``typed_dsl`` ran the built-in table, so an unregistered runner name
        silently executed a *different* executor than the one it named. An
        unregistered runner is now an error, which is what makes
        ``RUNNER_REGISTRY`` the authority on what can run.
        """
        spec = runner_spec(runner)
        if spec is None:
            raise ValueError(
                f"{entry.get('intervention_id')}: runner {runner!r} is not "
                f"registered; known runners are {sorted(RUNNER_REGISTRY)}"
            )
        if runner == PROGRAM_RUNNER:
            return run_predicate_program(
                (entry.get("body") or {}).get("predicate_definition") or {},
                packet,
                context,
            )
        if runner == "deterministic":
            return run_predicate(predicate, packet, context)
        if runner == "typed_semantic":
            return self._semantic_result(entry, packet, context)
        # A runner registered without an executor is a registry bug, not a
        # caller error: fail loudly rather than falling back to another runner.
        raise ValueError(
            f"{entry.get('intervention_id')}: runner {runner!r} is declared but "
            "has no executor wired into InterventionPlan"
        )

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
            predicate = str(body.get("predicate") or "")
            runner = str(body.get("runner") or "")
            result = self._run_predicate(
                entry, runner, predicate, packet, context,
            )
            allowed_verdicts = set(body.get("output_verdicts") or ())
            if str(result.get("verdict") or "") not in allowed_verdicts:
                raise ValueError(
                    f"{entry.get('intervention_id')}: predicate returned "
                    f"undeclared verdict {result.get('verdict')!r}"
                )
            result["intervention_id"] = str(entry.get("intervention_id") or "")
            result["hook"] = hook
            result["failure_family"] = str(
                (entry.get("provenance") or {}).get("failure_family") or ""
            )
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
        *,
        fact_rows: Sequence[Mapping[str, Any]] | None = None,
        record: bool = True,
    ) -> list[str]:
        """Evaluate decision guards and return the actions that fire.

        ``record=False`` answers the same question without writing anything
        (no ``fired`` trace, no mechanical evidence, no ``policy_conflict``).
        Stages that only need to know whether a guard *would* apply must use
        it: a side-effecting eligibility probe duplicated the guard's trace
        and let ``deployment.guard_actions`` reflect a probe rather than the
        terminal decision.
        """
        features = packet_features(packet)
        contexts = [dict(row) for row in (fact_rows or ())]
        if not contexts:
            contexts = [self._facts_map(facts)]
        entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for entry in self.entries:
            if entry.get("type") != "decision_guard" or entry.get("hook") != GUARD_HOOK:
                continue
            matched = next(
                (
                    context for context in contexts
                    if self._matches(entry, features, context)
                ),
                None,
            )
            if matched is not None:
                entries.append((entry, matched))
        actions: list[str] = []
        for entry, matched_facts in entries:
            body = entry.get("body") or {}
            fact_map = self._facts_map(matched_facts)
            fact_map.update(self.facts)
            excluded = self._facts_map(
                body.get("action_not_when_facts") or ()
            )
            if excluded and all(
                fact_map.get(name) == value
                for name, value in excluded.items()
            ):
                if record:
                    self._record(
                        entry, "guard_suppressed",
                        "action_not_when_facts matched",
                    )
                continue
            action = str((entry.get("body") or {}).get("action") or "")
            if action == "convict_mechanical_violation":
                predicate = str(
                    (entry.get("body") or {}).get("evidence_predicate") or ""
                )
                evidence = next(
                    (
                        item for item in self.evidence
                        if str(item.get("predicate") or "") == predicate
                        and str(item.get("failure_family") or "") == str(
                            (entry.get("provenance") or {}).get(
                                "failure_family"
                            ) or ""
                        )
                        and str(item.get("verdict") or "")
                        in self.predicate_conviction_verdicts.get(
                            predicate, frozenset(),
                        )
                    ),
                    None,
                )
                if evidence is None:
                    if record:
                        self._record(
                            entry,
                            "guard_suppressed",
                            "mechanical evidence binding missing or non-violating",
                        )
                    continue
                if record:
                    self.mechanical_guard_evidence.append({
                        "intervention_id": str(entry.get("intervention_id") or ""),
                        "action": action,
                        "predicate": predicate,
                        "evidence_intervention_id": str(
                            evidence.get("intervention_id") or ""
                        ),
                        "failure_family": str(
                            (entry.get("provenance") or {}).get(
                                "failure_family"
                            ) or ""
                        ),
                        "verdict": str(evidence.get("verdict") or ""),
                        "detail": str(evidence.get("detail") or ""),
                    })
            if record:
                self._record(entry, "guard_fired", f"action={action}")
            actions.append(action)
        unique = list(dict.fromkeys(actions))
        if len(unique) > 1:
            if record:
                self.policy_conflict = True
                self.fired.append({
                    "intervention_id": "multiple",
                    "hook": GUARD_HOOK,
                    "type": "decision_guard",
                    "event": "policy_conflict",
                    "detail": f"conflicting guard actions disabled: {sorted(set(actions))}",
                })
            return []
        if record:
            self.guard_actions_applied = unique
        return unique

    def recall_triggered(
        self,
        packet: Mapping[str, Any],
        *,
        judge_positive: bool,
        judge_parsed: Mapping[str, Any] | None = None,
    ) -> bool:
        """Evaluate recall.trigger policies after the judge posts evidence.

        A trigger can force the recall net for a judge-negative case when its
        predicate emits a declared ``trigger_verdict``. It never converts a
        judge-positive case: those already enter the hearing queue.
        """
        features = packet_features(packet)
        judge_parsed = dict(judge_parsed or {})
        context = judge_predicate_context(judge_parsed)
        context["judge_positive"] = judge_positive
        for entry in self.entries:
            if (
                entry.get("type") != "recall_trigger"
                or entry.get("hook") != RECALL_TRIGGER_HOOK
            ):
                continue
            body = entry.get("body") or {}
            if judge_positive and bool(body.get("require_judge_negative", True)):
                continue
            if not self._matches(entry, features, context):
                continue
            predicate = str(body.get("predicate") or "")
            runner = str(body.get("runner") or "")
            result = self._run_predicate(
                entry, runner, predicate, packet, context,
            )
            verdict = str(result.get("verdict") or "")
            if verdict in set(body.get("trigger_verdicts") or ()):
                self._record(
                    entry,
                    "recall_triggered",
                    f"predicate={predicate} verdict={verdict}",
                )
                return True
            self._record(
                entry,
                "recall_trigger_not_matched",
                f"predicate={predicate} verdict={verdict}",
            )
        return False

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
            "mechanical_guard_evidence": list(self.mechanical_guard_evidence),
            "typed_predicate_programs": [
                {
                    "name": str(program.get("name") or ""),
                    "definition_hash": predicate_program_hash(program),
                    "rules": len(list(program.get("rules") or ())),
                }
                for program in self.predicate_programs.values()
            ],
            "policy_conflict": conflict,
        }


def compile_runtime_plan(
    intervention_registry: Mapping[str, Any] | None = None,
    *,
    semantic_executor: "SemanticExecutor | None" = None,
) -> InterventionPlan:
    return InterventionPlan.from_registry(
        intervention_registry, semantic_executor=semantic_executor,
    )


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
    "RECALL_TRIGGER_HOOK",
    "SYSTEM_HOOKS",
    "TARGET_KINDS",
    "TARGET_POLARITIES",
    "build_intervention_registry",
    "compile_runtime_plan",
    "custom_conviction_verdicts_for",
    "custom_fact_vocab_for",
    "empty_plan",
    "inline_predicate_programs",
    "inline_trigger_programs",
    "intervention_content_hash",
    "load_intervention_registry",
    "validate_intervention",
    "validate_intervention_batch",
    "validate_registry",
]
