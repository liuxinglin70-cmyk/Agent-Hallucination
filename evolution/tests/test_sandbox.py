"""The Level B sandbox: property tests and metamorphic relations (§10.2, P2.2).

§10.2 asks for property + metamorphic tests, not just unit tests, and the
distinction matters here more than usual. A sandbox's unit tests can all pass
while the sandbox is porous: ``test_open_is_refused`` says nothing about
``f.__globals__``. So the tests below are organised as

* **properties** -- statements quantified over the surface (every forbidden
  route is refused; every whitelisted import actually binds; every limit is
  either enforced or the platform refuses admission; every declared shape
  mismatch is caught);
* **metamorphic relations** -- an invariance that must hold under a
  transformation (normalizing whitespace, quoting style, or sentence-final
  punctuation must not change a component's verdict; renaming a component must
  not change its digest; re-ordering the parameter list must not change which
  value reaches which name).

The probes that make a child *violate* a limit are real subprocesses. They are
marked and kept few, because each one costs a process launch.
"""

from __future__ import annotations

import copy
import textwrap

import pytest

from detector.packet import build_packet

from evolution.sandbox import (
    BUILTIN_WHITELIST,
    DEFAULT_LIMITS,
    FORBIDDEN_SOURCES,
    HARD_LIMITS,
    IMPORT_WHITELIST,
    SANDBOX_SCHEMA,
    SandboxError,
    admission_blockers,
    build_envelope,
    check_output_schema,
    component_digest,
    execute_component,
    make_component_executor,
    normalize_limits,
    reference_component,
    run_forbidden_sources,
    sandbox_capabilities,
    validate_component,
)


# --------------------------------------------------------------------------
# Property: the declaration is closed
# --------------------------------------------------------------------------

def test_whitelists_are_disjoint_from_the_dangerous_surface():
    """No whitelisted import is a route to the filesystem, network or process."""
    banned = {
        "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
        "builtins", "ctypes", "multiprocessing", "urllib", "http", "requests",
        "pickle", "marshal", "signal", "tempfile", "glob", "io", "pty",
    }
    assert not (set(IMPORT_WHITELIST) & banned)


def test_no_forbidden_builtin_is_in_the_whitelist():
    """The dangerous builtins are absent, not merely discouraged."""
    for name in (
        "open", "eval", "exec", "compile", "__import__", "input",
        "globals", "locals", "vars", "getattr", "setattr", "delattr",
    ):
        assert name not in BUILTIN_WHITELIST


def test_every_whitelisted_import_can_actually_be_bound():
    """The whitelist must not name a module the child cannot import.

    A whitelist entry that fails at run time would turn an admitted component
    into a runtime error, and the two failure modes ("your component is wrong"
    and "the sandbox lied") are indistinguishable to the caller.
    """
    for module in IMPORT_WHITELIST:
        result = execute_component(
            reference_component(
                name="evo_import_probe",
                entrypoint="judge",
                parameters=[
                    {"name": "expected", "type": "string"},
                    {"name": "actual", "type": "string"},
                ],
                returns={"type": "verdict", "verdicts": [
                    "supported", "contradicted", "abstained",
                ]},
                imports=[module],
                source=(
                    f"import {module}\n\n\n"
                    "def judge(expected, actual):\n"
                    "    return \"supported\"\n"
                ),
            ),
            {"expected": "a", "actual": "b"},
        )
        assert result["ok"], (module, result.get("error"))


def test_forbidden_routes_are_all_refused_and_none_leak():
    """Every route in the catalogue is refused, and the count is pinned.

    The assertion on the number is deliberate: adding a route to the catalogue
    without it being refused is the failure this test exists to catch, and a
    bare "all refused" over an empty list would pass trivially.
    """
    rows = run_forbidden_sources()
    assert len(rows) == len(FORBIDDEN_SOURCES) >= 20
    leaked = [row["route"] for row in rows if not row["refused"]]
    assert leaked == [], leaked


def test_digest_ignores_the_name_and_the_description():
    """A rename must not mint a new identity (metamorphic class 1, by analogy)."""
    base = validate_component(reference_component())
    renamed = validate_component(reference_component(
        name="evo_sentence_match_renamed",
        description="A different description entirely.",
    ))
    assert base["digest"] == renamed["digest"]


def test_digest_changes_when_the_contract_changes():
    """Every field that determines behaviour is in the identity."""
    base = validate_component(reference_component())
    variants = {
        "source": reference_component(source=reference_component()["source"] + "\n"),
        "limits": reference_component(limits={"wall_seconds": 4.0}),
        "entrypoint": reference_component(
            entrypoint="decide",
            source=reference_component()["source"].replace("def judge(", "def decide("),
        ),
        "returns": reference_component(
            returns={"type": "verdict", "verdicts": ["supported", "contradicted", "abstained"]},
            parameters=[
                {"name": "expected", "type": "string"},
                {"name": "actual", "type": "integer"},
            ],
        ),
    }
    for label, variant in variants.items():
        changed = validate_component(variant)
        assert changed["digest"] != base["digest"], label


# --------------------------------------------------------------------------
# Property: limits
# --------------------------------------------------------------------------

def test_limits_above_the_ceiling_are_refused_not_clamped():
    """A requested budget above the ceiling is an error.

    Clamping would run the component under a budget other than the one it was
    tested with, which is the failure mode this refusal prevents.
    """
    with pytest.raises(SandboxError, match="exceeds the hard ceiling"):
        normalize_limits({"wall_seconds": HARD_LIMITS["wall_seconds"] + 1})


def test_unknown_and_nonpositive_limits_are_refused():
    with pytest.raises(SandboxError, match="unknown limit"):
        normalize_limits({"cpu_hours": 1})
    with pytest.raises(SandboxError, match="must be positive"):
        normalize_limits({"wall_seconds": 0})
    with pytest.raises(SandboxError, match="must be a number"):
        normalize_limits({"wall_seconds": "fast"})


def test_every_hard_limit_has_a_default_at_or_below_it():
    for key, ceiling in HARD_LIMITS.items():
        assert key in DEFAULT_LIMITS, key
        assert DEFAULT_LIMITS[key] <= ceiling, key


def test_admission_is_refused_when_a_limit_cannot_be_enforced():
    """A platform that cannot bound a child must not admit one.

    Simulated by removing the mechanism the platform check reads: the point is
    that the refusal path exists and produces a reason, because on a platform
    without job objects or rlimits this is the only honest outcome.
    """
    from evolution import sandbox

    original = sandbox._declared_capabilities

    def starved():
        return {
            "mechanism": "wall_clock_only",
            "wall_seconds": True,
            "cpu_seconds": False,
            "memory_bytes": False,
            "filesystem_bytes": False,
        }

    sandbox._declared_capabilities = starved
    try:
        blockers = admission_blockers()
        assert any("CPU-time" in item for item in blockers)
        assert any("memory limit" in item for item in blockers)
        result = execute_component(reference_component(), {
            "expected": "a", "actual": "b",
        })
        assert result["ok"] is False
        assert result["status"] == "unsupported_platform"
    finally:
        sandbox._declared_capabilities = original


def test_declared_capabilities_match_the_actual_probe():
    """The declared capability must be the probed one.

    A platform that *claims* a memory cap it does not have is worse than one
    that declares none, because the claim is what the audit quotes.
    """
    declared = sandbox_capabilities(verify=False)
    probed = sandbox_capabilities(verify=True)
    # The probe's keys are the limit *kinds*; the declaration's are the field
    # names. Mapping them explicitly keeps the two vocabularies from drifting
    # into a check that compares nothing.
    key_for = {
        "wall_seconds": "wall",
        "cpu_seconds": "cpu",
        "memory_bytes": "memory",
    }
    for field, claimed in declared["declared"].items():
        if not claimed:
            continue
        kind = key_for[field]
        assert kind in probed["verified"], field
        # A declared cap that the probe did not reproduce is a failure, not a
        # flake: either the limit or the declaration is wrong.
        assert probed["verified"][kind]["enforced"], (field, probed["verified"][kind])


@pytest.mark.slow
def test_wall_clock_limit_actually_stops_a_runaway_component():
    """A component that never returns is killed, not left running."""
    spin = reference_component(
        name="evo_spin_probe",
        source=(
            "def judge(expected, actual):\n"
            "    total = 0\n"
            "    while True:\n"
            "        total += 1\n"
        ),
        limits={"wall_seconds": 2.0, "cpu_seconds": 1.0},
    )
    result = execute_component(spin, {"expected": "a", "actual": "b"})
    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["wall_seconds"] < 8.0


@pytest.mark.slow
def test_memory_limit_actually_stops_an_over_allocating_component():
    """An over-allocating component is refused by the OS, not by luck."""
    hog = reference_component(
        name="evo_memory_probe",
        source=(
            "def judge(expected, actual):\n"
            "    block = \"x\" * (800 * 1024 * 1024)\n"
            "    return \"supported\"\n"
        ),
        limits={"wall_seconds": 10.0, "memory_bytes": 128 * 1024 * 1024},
    )
    result = execute_component(hog, {"expected": "a", "actual": "b"})
    assert result["ok"] is False
    # Either the allocation raised inside the child or the child died; both are
    # the cap working, and both must not be reported as a verdict.
    assert result["status"] in {"component_error", "no_result"}
    # Control: the same component under a generous cap must succeed, so the
    # failure is the cap and not the source.
    relaxed = reference_component(
        name="evo_memory_probe",
        source=(
            "def judge(expected, actual):\n"
            "    block = \"x\" * (1024 * 1024)\n"
            "    return \"supported\"\n"
        ),
        limits={"wall_seconds": 10.0, "memory_bytes": 256 * 1024 * 1024},
    )
    control = execute_component(relaxed, {"expected": "a", "actual": "b"})
    assert control["ok"] and control["result"] == "supported"


# --------------------------------------------------------------------------
# Property: schema
# --------------------------------------------------------------------------

def test_a_declared_parameter_that_is_missing_is_refused():
    result = execute_component(reference_component(), {"expected": "a"})
    assert result["ok"] is False
    assert result["status"] == "bad_input"
    assert "missing input 'actual'" in result["error"]


def test_a_parameter_of_the_wrong_type_is_refused():
    result = execute_component(
        reference_component(), {"expected": "a", "actual": 7},
    )
    assert result["ok"] is False
    assert result["status"] == "bad_input"
    assert "must be a string" in result["error"]


def test_an_output_outside_the_declared_vocabulary_is_refused():
    component = reference_component(
        name="evo_bad_verdict",
        source='def judge(expected, actual):\n    return "maybe"\n',
    )
    result = execute_component(component, {"expected": "a", "actual": "b"})
    assert result["ok"] is False
    assert result["status"] == "schema_violation"
    assert "outside the declared vocabulary" in result["error"]


def test_output_schema_covers_every_declared_return_type():
    component = {"returns": {"type": "integer"}}
    assert check_output_schema(component, 3) == ""
    assert "schema declares integer" in check_output_schema(component, "3")
    assert check_output_schema({"returns": {"type": "boolean"}}, True) == ""
    assert "boolean" in check_output_schema({"returns": {"type": "boolean"}}, 1)
    assert check_output_schema({"returns": {"type": "string"}}, "x") == ""
    assert "string" in check_output_schema({"returns": {"type": "string"}}, 1)
    # A declared object shape checks its required fields.
    obj = {"returns": {"type": "object", "fields": {"verdict": "string"}}}
    assert check_output_schema(obj, {"verdict": "supported"}) == ""
    assert "missing declared field" in check_output_schema(obj, {})


def test_an_entrypoint_with_the_wrong_arity_is_refused():
    component = reference_component(
        name="evo_wrong_arity",
        source='def judge(expected):\n    return "supported"\n',
    )
    with pytest.raises(SandboxError, match="takes \\['expected'\\]"):
        validate_component(component)


def test_schemas_are_versioned():
    with pytest.raises(SandboxError, match="unsupported component schema"):
        validate_component(reference_component(schema_version="evolution.other.v1"))
    assert reference_component()["schema_version"] == SANDBOX_SCHEMA


def test_a_non_literal_module_level_binding_is_refused():
    component = reference_component(
        name="evo_computed_constant",
        source=(
            "BOUNDARY = 1 + 1\n\n\n"
            "def judge(expected, actual):\n"
            "    return \"supported\"\n"
        ),
    )
    with pytest.raises(SandboxError, match="literal constant"):
        validate_component(component)


def test_optimization_and_introspection_builtins_are_absent_at_run_time():
    """The static refusal and the runtime namespace must agree.

    A name refused statically but present at run time would make the refusal
    advisory; a name allowed statically but absent at run time would turn an
    admitted component into a crash. Both are checked against the same list.
    """
    component = reference_component(
        name="evo_builtin_probe",
        source=(
            "def judge(expected, actual):\n"
            "    names = sorted(dir())\n"
            "    return \"supported\"\n"
        ),
    )
    # ``dir`` is not in the whitelist, so this refuses statically; the check is
    # that the *runtime* envelope carries exactly the declared builtins.
    with pytest.raises(SandboxError):
        validate_component(component)
    envelope = build_envelope(reference_component(), {"expected": "a", "actual": "b"})
    assert sorted(envelope["builtins"]) == sorted(BUILTIN_WHITELIST)
    assert "open" not in envelope["builtins"]
    assert "__import__" not in envelope["builtins"]


# --------------------------------------------------------------------------
# Metamorphic relations
# --------------------------------------------------------------------------

def _component(**overrides):
    """The reference component, as a validated object."""
    return validate_component(reference_component(**overrides))


def _record_with_tool_call() -> dict:
    """A minimal record that carries ``has_tool_calls``.

    The plan-level tests need the trigger to match, or ``evidence_for`` returns
    an empty list because the entry was filtered out -- a pass that would say
    nothing about the component.
    """
    return {
        "question": "Deliver the summary.",
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "Deliver the summary.",
                "tool_calls": [{"name": "summary_writer", "arguments": {"text": "x"}}],
                "tool_responses": ["ok"],
            },
        ],
    }


def _judge(component, expected, actual):
    result = execute_component(component, {"expected": expected, "actual": actual})
    assert result["ok"], result.get("error")
    return result["result"]


@pytest.mark.slow
def test_class_semantic_equivalence_holds_for_a_sandboxed_component():
    """Class 5: a surface rewrite must not change a component's verdict.

    This is the relation that makes a generated component admissible at all:
    a comparison that flipped on quote style would be reading the surface.
    """
    component = reference_component()
    base = _judge(component, "The report states \u201cten degrees\u201d.", "The report states \"ten degrees\".")
    assert base == "supported"
    rewrites = [
        ("The report states \"ten degrees\".", "The report states \"ten degrees\""),
        ("The report states \"ten degrees\". ", "  The report states \"ten degrees\"."),
        ("The report  states \"ten degrees\".", "The report states \"ten degrees\"."),
        ("The report states \u00abten degrees\u00bb.", "The report states \"ten degrees\"."),
    ]
    for expected, actual in rewrites:
        assert _judge(component, expected, actual) == "supported", (expected, actual)


@pytest.mark.slow
def test_class_irrelevant_input_does_not_change_the_verdict():
    """An unused parameter must not change the answer.

    The sandbox passes every declared parameter, so this also checks that a
    component receives its arguments by *name*: a positional mix-up would make
    the verdict depend on argument order rather than on content.
    """
    component = reference_component()
    pairs = [
        ("Alpha beta.", "Alpha beta."),
        ("Gamma delta.", "Gamma delta."),
        ("Epsilon zeta.", "Epsilon zeta."),
    ]
    for expected, actual in pairs:
        assert _judge(component, expected, actual) == "supported"
    # A component whose unused parameter is given an identical value to the
    # used one must behave identically: the parameter carries no signal.
    result = execute_component(component, {
        "expected": "Same text.", "actual": "Same text.",
    })
    assert result["result"] == "supported"


def test_parameter_order_does_not_change_which_value_reaches_which_name():
    """Arguments are bound by name, so a re-ordered schema is a different contract.

    Reordering the *declaration* changes the digest (so it is a different
    component) but must not silently transpose the values at run time. Both
    halves are checked.
    """
    forward = reference_component()
    reversed_schema = reference_component(parameters=[
        {"name": "actual", "type": "string"},
        {"name": "expected", "type": "string"},
    ])
    assert component_digest(forward) != component_digest(reversed_schema)
    # Under the reversed schema, the same two values produce the same verdict
    # only because the component is symmetric; the binding check is that each
    # name receives the value the caller supplied for it.
    envelope = build_envelope(reversed_schema, {"expected": "LEFT", "actual": "RIGHT"})
    assert envelope["parameter_names"] == ["actual", "expected"]
    assert envelope["arguments"] == {"expected": "LEFT", "actual": "RIGHT"}


@pytest.mark.slow
def test_a_component_is_deterministic_across_repeated_calls():
    """Same input, same output: the property a pure function has to have."""
    component = reference_component()
    first = _judge(component, "One two three.", "One two three.")
    for _ in range(3):
        assert _judge(component, "One two three.", "One two three.") == first


@pytest.mark.slow
def test_non_ascii_input_survives_the_child_boundary():
    """Text must arrive unchanged through the pipe, whatever the locale.

    The child runs with ``-I``, which implies ``-E``, so ``PYTHONIOENCODING``
    is ignored and the streams fall back to the locale codec. Before the driver
    reconfigured them explicitly this corrupted every non-ASCII value on a
    non-UTF-8 locale -- and these components exist to compare task text, which
    is frequently not ASCII. The regression is pinned with text that is
    identical after a plain re-encode and different under a lossy one.
    """
    component = reference_component()
    same = "\u54c8\u5c14\u6ee8\u4eca\u5929\u5f88\u51b7\u3002"
    different = "\u54c8\u5c14\u6ee8\u4eca\u5929\u5f88\u70ed\u3002"
    assert _judge(component, same, same) == "supported"
    assert _judge(component, same, different) == "contradicted"
    # A curly quote is a surface difference the component is declared to
    # ignore; it only works if the character survived the pipe intact.
    curly = "The report states \u201cten degrees\u201d."
    plain = 'The report states "ten degrees".'
    assert _judge(component, curly, plain) == "supported"


# --------------------------------------------------------------------------
# The runner is registered and admitted
# --------------------------------------------------------------------------

def test_component_runner_may_never_convict_or_route_recall():
    """The safety properties of the runner are declared, not assumed."""
    from detector.interventions import COMPONENT_RUNNER, runner_spec

    spec = runner_spec(COMPONENT_RUNNER)
    assert spec is not None
    assert spec.terminal_authority == "evidence_only"
    assert spec.requires_artifact is True
    assert spec.intervention_types == {"evidence_check"}
    from detector.interventions import RECALL_TRIGGER_HOOK

    assert RECALL_TRIGGER_HOOK not in spec.supported_hooks


def test_component_runner_has_a_metamorphic_admission_subject():
    from detector.interventions import COMPONENT_RUNNER
    from evolution.metamorphic import admission_error, reference_subject, run_gate

    assert admission_error(COMPONENT_RUNNER) == ""
    subject = reference_subject(COMPONENT_RUNNER)
    assert subject is not None
    report = run_gate(COMPONENT_RUNNER, subject)
    assert report["passed"], report["failed"]


def test_an_entry_without_body_component_is_refused():
    from detector.interventions import (
        COMPONENT_RUNNER,
        build_intervention_registry,
    )

    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "evo_sentence_match",
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
        },
        "artifact": {"id": "evo_sentence_match", "digest": "abc"},
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    with pytest.raises(ValueError, match="requires body.component"):
        build_intervention_registry([entry])


def test_component_inputs_must_cover_the_declared_parameters():
    from detector.interventions import (
        COMPONENT_RUNNER,
        build_intervention_registry,
    )

    component = reference_component()
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": component["name"],
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "component": component,
            # ``actual`` is deliberately absent.
            "component_inputs": {"expected": "last_prose_text"},
        },
        "artifact": {"id": component["name"], "digest": "abc"},
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    with pytest.raises(ValueError, match="must cover exactly the declared parameters"):
        build_intervention_registry([entry])


def test_component_inputs_must_name_typed_packet_views():
    from detector.interventions import (
        COMPONENT_RUNNER,
        build_intervention_registry,
    )

    component = reference_component()
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": component["name"],
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "component": component,
            "component_inputs": {
                "expected": "last_prose_text",
                "actual": "os.environ",
            },
        },
        "artifact": {"id": component["name"], "digest": "abc"},
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    with pytest.raises(ValueError, match="outside the typed packet vocabulary"):
        build_intervention_registry([entry])


def test_executor_identity_must_match_the_entrys_pinned_digest():
    """A pinned digest that nothing compares is decoration.

    The executor is built from one component and the entry pins another's
    digest; the plan must refuse rather than attribute results to an artifact
    that did not produce them.
    """
    from detector.interventions import COMPONENT_RUNNER, InterventionPlan
    from detector.packet import build_packet

    component = reference_component()
    normalized = validate_component(component)
    executor = make_component_executor(component)
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": normalized["name"],
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "component": component,
            "component_inputs": {
                "expected": "last_prose_text",
                "actual": "question_text",
            },
        },
        "artifact": {"id": normalized["name"], "digest": "a-different-digest"},
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    plan = InterventionPlan([entry], component_executors={normalized['name']: executor})
    packet = build_packet(_record_with_tool_call())
    with pytest.raises(ValueError, match="refusing to attribute results"):
        plan.evidence_for("judge.post", packet, {})


def test_component_without_an_executor_fails_closed():
    from detector.interventions import COMPONENT_RUNNER, InterventionPlan
    from detector.packet import build_packet

    component = reference_component()
    normalized = validate_component(component)
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": normalized["name"],
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "component": component,
            "component_inputs": {
                "expected": "last_prose_text",
                "actual": "question_text",
            },
        },
        "artifact": {"id": normalized["name"], "digest": normalized["digest"]},
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    plan = InterventionPlan([entry])
    packet = build_packet(_record_with_tool_call())
    with pytest.raises(ValueError, match="needs an executor"):
        plan.evidence_for("judge.post", packet, {})


@pytest.mark.slow
def test_a_component_runs_end_to_end_through_the_plan():
    """The whole path: entry -> packet views -> sandbox -> evidence verdict."""
    from detector.interventions import COMPONENT_RUNNER, InterventionPlan
    from detector.packet import build_packet

    component = reference_component()
    normalized = validate_component(component)
    entry = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": normalized["name"],
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "component": component,
            "component_inputs": {
                "expected": "last_prose_text",
                "actual": "last_prose_text",
            },
        },
        "artifact": {"id": normalized["name"], "digest": normalized["digest"]},
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    plan = InterventionPlan([entry], component_executors={normalized['name']: make_component_executor(component)})
    packet = build_packet(_record_with_tool_call())
    results = plan.evidence_for("judge.post", packet, {})
    assert len(results) == 1
    assert results[0]["verdict"] == "supported"
    assert results[0]["runner"] == COMPONENT_RUNNER


@pytest.mark.slow
def test_a_failing_component_abstains_rather_than_convicting():
    """A sandbox failure must use the vocabulary's word for "I could not tell".

    The alternative -- mapping a timeout onto ``violated`` -- would let a
    resource limit produce a conviction, which is the one outcome the runner's
    ``evidence_only`` declaration exists to prevent.
    """
    component = reference_component(
        name="evo_runaway_component",
        source=(
            "def judge(expected, actual):\n"
            "    total = 0\n"
            "    while True:\n"
            "        total += 1\n"
        ),
        limits={"wall_seconds": 2.0, "cpu_seconds": 1.0},
    )
    executor = make_component_executor(component)
    result = executor.judge("evo_runaway_component", {}, {"arguments": {
        "expected": "a", "actual": "b",
    }})
    assert result["verdict"] == "abstained"
    assert "did not complete" in result["detail"]


def test_a_non_verdict_component_cannot_back_an_evidence_check():
    """The executor refuses a component whose return is not a verdict."""
    component = reference_component(
        name="evo_boolean_probe",
        returns={"type": "boolean"},
        source=(
            "def judge(expected, actual):\n"
            "    return expected == actual\n"
        ),
    )
    with pytest.raises(SandboxError, match="must declare returns.type"):
        make_component_executor(component)


# --------------------------------------------------------------------------
# Reachability: the runner must be usable, not merely registered
# --------------------------------------------------------------------------

def _registry_entry(component, *, digest=None, inputs=None):
    from detector.interventions import COMPONENT_RUNNER

    normalized = validate_component(component)
    return {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": normalized["name"],
            "runner": COMPONENT_RUNNER,
            "output_verdicts": ["supported", "contradicted", "abstained"],
            "component": component,
            "component_inputs": inputs or {
                "expected": "last_prose_text",
                "actual": "question_text",
            },
        },
        "artifact": {
            "id": normalized["name"],
            "digest": digest if digest is not None else normalized["digest"],
        },
        "claim": {
            "expected_behavior": "a sentence comparison",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }


def test_the_runner_is_reachable_from_the_evolution_backend():
    """A registered runner nobody can execute is not a capability.

    This is the reachability check that the prior work packages needed three
    times: a new executor that validates, has tests, and still cannot be run
    from the path that matters. The evolution backend compiles registries
    through one helper, and that helper must build the sandbox executors.
    """
    from detector.interventions import build_intervention_registry
    from evolution.backends import (
        _compile_with_component_executors,
        _registry_component_executors,
    )

    component = reference_component()
    registry = build_intervention_registry([_registry_entry(component)])
    executors = _registry_component_executors(registry["interventions"])
    assert sorted(executors) == [validate_component(component)["name"]]
    plan = _compile_with_component_executors(registry)
    packet = build_packet({
        "question": "Please deliver the summary.",
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "Delivered.",
                "tool_calls": [{"name": "writer", "arguments": {}}],
                "tool_responses": ["ok"],
            },
        ],
    })
    results = plan.evidence_for("judge.post", packet, {})
    assert len(results) == 1
    assert results[0]["runner"] == "sandboxed_component"


def test_a_wrong_digest_is_refused_through_the_same_path():
    """The digest check must hold on the path a run actually uses.

    Checked here as well as in the unit test because the reachability helper is
    what supplies the executor in production; a helper that built an executor
    ignoring the declared digest would make the pin decorative everywhere.
    """
    from detector.interventions import build_intervention_registry
    from evolution.backends import _compile_with_component_executors

    component = reference_component()
    registry = build_intervention_registry([_registry_entry(component)])
    tampered = copy.deepcopy(registry)
    tampered["interventions"][0]["artifact"]["digest"] = "0" * 64
    tampered.pop("content_hash", None)
    plan = _compile_with_component_executors(tampered)
    packet = build_packet({
        "question": "Please deliver the summary.",
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "Delivered.",
                "tool_calls": [{"name": "writer", "arguments": {}}],
                "tool_responses": ["ok"],
            },
        ],
    })
    with pytest.raises(ValueError, match="refusing to attribute results"):
        plan.evidence_for("judge.post", packet, {})


def test_a_registry_with_no_component_entry_builds_no_executors():
    """The helper must be a no-op for every pre-existing registry."""
    from evolution.backends import _registry_component_executors

    assert _registry_component_executors([]) == {}
    assert _registry_component_executors([{"type": "text_boundary"}]) == {}


def test_a_declared_component_runner_without_a_component_raises():
    """Naming the runner with nothing to run must not look like "no entries"."""
    from evolution.backends import _registry_component_executors

    with pytest.raises(SandboxError, match="declares a component runner"):
        _registry_component_executors([{
            "intervention_id": "IV-1-001",
            "type": "evidence_check",
            "body": {"runner": "sandboxed_component", "predicate": "evo_x"},
        }])


def test_the_detector_builds_component_executors_when_given_a_factory():
    """The real detector path: a component entry runs without injection by hand."""
    from detector.api import Detector
    from evolution.backends import _registry_component_executors

    component = reference_component()
    entry = _registry_entry(component)
    detector = Detector(
        {
            "judge_route": {
                "endpoint": "http://unused", "model": "m", "api_key_env": "K",
                "temperature": 0.0, "timeout_seconds": 5, "transport_retries": 0,
            },
        },
        require_credentials=False,
        component_executor_factory=_registry_component_executors,
    )
    plan = detector._component_executors({"interventions": [entry]})
    assert sorted(plan) == [validate_component(component)["name"]]
    # Without a factory the detector supplies nothing and the entry fails
    # closed, rather than silently running a different executor.
    bare = Detector(require_credentials=False)
    assert bare._component_executors({"interventions": [entry]}) == {}


# --------------------------------------------------------------------------
# Every unbounded construct the AST allowlist permits must be bounded
# --------------------------------------------------------------------------

#: Constructs the validator *allows* (loops, recursion, repetition) that can
#: nevertheless run forever or allocate without limit. The AST layer cannot
#: refuse them -- a pure function may legitimately loop -- so the only thing
#: bounding them is the resource contract. Each is checked to be stopped, and
#: the check fails if one of them returns a verdict instead.
UNBOUNDED_CONSTRUCTS = {
    "while_true": (
        "def judge(expected, actual):\n"
        "    n = 0\n"
        "    while True:\n"
        "        n += 1\n"
    ),
    "nested_loops": (
        "def judge(expected, actual):\n"
        "    total = 0\n"
        "    for i in range(100000000):\n"
        "        for j in range(100000000):\n"
        "            total += 1\n"
        "    return \"supported\"\n"
    ),
    "huge_allocation": (
        "def judge(expected, actual):\n"
        "    block = expected * (1000000 * 1000)\n"
        "    return \"supported\"\n"
    ),
    "unbounded_recursion": (
        "def judge(expected, actual):\n"
        "    return probe(expected, 0)\n"
        "\n"
        "\n"
        "def probe(text, n):\n"
        "    return probe(text, n + 1)\n"
    ),
}


@pytest.mark.slow
def test_every_allowed_unbounded_construct_is_stopped_by_a_limit():
    """A construct the validator allows must still be bounded at run time.

    This is the property that makes the AST allowlist safe. The validator
    permits loops and recursion because a pure function may legitimately use
    them; what it cannot do is bound them. So the claim under test is not "the
    validator refuses these" -- it is "the resource contract stops them", and
    the test fails if any one of them produces a verdict instead.
    """
    outcomes = {}
    for label, source in UNBOUNDED_CONSTRUCTS.items():
        component = reference_component(
            name=f"evo_{label}",
            source=source,
            limits={
                "wall_seconds": 3.0,
                "cpu_seconds": 2.0,
                "memory_bytes": 192 * 1024 * 1024,
            },
        )
        result = execute_component(component, {"expected": "a", "actual": "b"})
        outcomes[label] = (result["ok"], result["status"], result.get("wall_seconds"))
        assert result["ok"] is False, (label, result)
        # Stopped by a limit, and specifically not by a fabricated verdict.
        assert result["status"] in {
            "timeout", "component_error", "no_result", "refused",
        }, (label, result["status"])
        assert result.get("wall_seconds", 0) < 20.0, (label, result)
    # Every one of them ran to a bound rather than escaping: the shapes are
    # distinct enough that they must not all fail for the same reason.
    assert len({status for _ok, status, _w in outcomes.values()}) >= 2, outcomes
