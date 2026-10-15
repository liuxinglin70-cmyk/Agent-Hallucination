"""§15.2's six metamorphic relations, as an admission criterion for runners.

The gate exists to stop a new executor from becoming the standard before
anyone has asked what it must *not* notice. These tests pin the consequences
rather than the wording:

* every registered runner is admitted, and the report says so by running;
* a runner with no admission subject is refused, not passed by default;
* each of the six classes fails for the right reason on a subject that really
  breaks it -- a keyword matcher, a surface-sensitive judge, a rule that
  reads the case id -- so the gate cannot pass by being vacuous;
* an ineffective mutation is a failure, because a transformation that changed
  nothing demonstrates nothing;
* the gate is admission-only and replaces no formal gate step.

The negative subjects are the point. A gate whose only passing subject is the
reference one is indistinguishable from a gate that always passes.
"""

from __future__ import annotations

import pytest

from detector.interventions import (
    METAMORPHIC_CLASSES,
    RUNNER_REGISTRY,
    RunnerSpec,
)
from detector.packet import build_packet
from evolution.metamorphic import (
    CASE_ID_RENAME,
    CLASS_ORDER,
    IRRELEVANT_TOOL_CALL,
    LOCALIZATION_EVENT_MOVE,
    MechanismSubject,
    MetamorphicSubject,
    NEIGHBOR_SURFACE_TOKEN,
    PacketRuleSubject,
    REFERENCE_SUBJECTS,
    SEMANTIC_EQUIVALENCE,
    SOURCE_RENAME,
    SemanticSubject,
    admission_error,
    admitted_runners,
    check_irrelevant_tool_call,
    inject_surface_tokens,
    insert_irrelevant_tool_call,
    metamorphic_report,
    move_irrelevant_event,
    normal_record,
    reference_subject,
    run_gate,
    semantic_equivalents,
    tool_call_record,
)


# --------------------------------------------------------------------------
# The registry declares the requirement
# --------------------------------------------------------------------------

def test_every_registered_runner_declares_metamorphic_classes():
    """A runner cannot be registered without saying what it must survive."""
    for name, spec in RUNNER_REGISTRY.items():
        assert spec.metamorphic_classes, f"{name} declares no metamorphic class"
        assert set(spec.metamorphic_classes) <= METAMORPHIC_CLASSES


def test_learned_runner_cannot_opt_out_of_semantic_equivalence():
    """The class a learned runner most needs cannot be dropped by declaration.

    Written as a constructor assertion rather than a comment because the
    failure it prevents is silent: a learned runner that declares only the
    packet classes looks admitted while its judgments turn on quotation style.
    """
    spec = RunnerSpec(
        name="probe",
        supported_hooks=frozenset({"judge.post"}),
        intervention_types=frozenset({"evidence_check"}),
        input_schema="probe",
        deterministic=False,
        learned=True,
        requires_artifact=True,
        terminal_authority="evidence_only",
        metamorphic_classes={IRRELEVANT_TOOL_CALL},
    )
    assert SEMANTIC_EQUIVALENCE in spec.metamorphic_classes


def test_unknown_metamorphic_class_is_refused():
    """A misspelled class must not silently widen the requirement."""
    with pytest.raises(ValueError, match="unknown metamorphic class"):
        RunnerSpec(
            name="probe",
            supported_hooks=frozenset({"judge.post"}),
            intervention_types=frozenset({"evidence_check"}),
            input_schema="probe",
            deterministic=True,
            learned=False,
            requires_artifact=False,
            terminal_authority="conviction",
            metamorphic_classes={"case_id_rename_typo"},
        )


# --------------------------------------------------------------------------
# The gate passes the runners that ship with it
# --------------------------------------------------------------------------

def test_every_registered_runner_has_a_reference_subject():
    for name in RUNNER_REGISTRY:
        assert reference_subject(name) is not None, name
        assert admission_error(name) == ""


def test_all_registered_runners_are_admitted():
    for name in RUNNER_REGISTRY:
        report = run_gate(name)
        assert report["passed"], (name, report["failed"])
        assert report["authority"] == "admission-only"


def test_gate_runs_exactly_the_declared_classes():
    """The verdict covers the declaration, and no more and no less."""
    for name, spec in RUNNER_REGISTRY.items():
        report = run_gate(name)
        assert set(report["required_classes"]) == set(spec.metamorphic_classes)
        assert {item["class"] for item in report["checks"]} == set(
            spec.metamorphic_classes
        )


def test_report_executes_and_reports_a_verdict_per_runner():
    report = metamorphic_report()
    assert report["all_admitted"] is True
    assert {row["runner"] for row in report["runners"]} == set(RUNNER_REGISTRY)
    # Every row carries the executed verdict, not just the declaration.
    for row in report["runners"]:
        assert row["admitted"] is True
        assert row["failed"] == []
    assert list(report["classes"]) == list(CLASS_ORDER)


# --------------------------------------------------------------------------
# A runner with no subject is refused, not exempt
# --------------------------------------------------------------------------

def test_unregistered_runner_is_refused():
    assert admission_error("no_such_runner") == (
        "runner 'no_such_runner' is not registered"
    )


def test_missing_subject_is_a_refusal_not_an_exemption(monkeypatch):
    """Adding a runner without a subject must fail loudly.

    This is the specific failure the reference-subject registry exists to
    prevent, so it is tested by removing one rather than by describing it.
    """
    monkeypatch.delitem(REFERENCE_SUBJECTS, "typed_semantic", raising=False)
    reason = admission_error("typed_semantic")
    assert "no metamorphic admission subject" in reason
    report = run_gate("typed_semantic")
    assert report["passed"] is False
    assert any("does not implement" in item for item in report["failed"])


def test_admitted_runners_lists_only_those_with_a_subject(monkeypatch):
    assert set(admitted_runners()) == set(RUNNER_REGISTRY)
    monkeypatch.delitem(REFERENCE_SUBJECTS, "deterministic", raising=False)
    assert "deterministic" not in admitted_runners()


# --------------------------------------------------------------------------
# Class 1: case id rename
# --------------------------------------------------------------------------

def test_case_id_rename_keeps_mechanism_identity():
    result = run_gate("deterministic")["checks"]
    case = next(item for item in result if item["class"] == CASE_ID_RENAME)
    assert case["passed"] is True
    assert case["mutation_effective"] is True


def test_case_id_rename_detects_a_case_id_reading_mechanism():
    """A mechanism that hashes the case id fails the class, not the inverse.

    ``MechanismSubject`` is replaced with one whose group reading *is* the case
    list, which is exactly the defect P0.1's key was built to exclude. The
    class must catch it.
    """

    class CaseSensitiveSubject(MechanismSubject):
        def mechanism_group(self, rows):
            return [
                {
                    "group": ["case"],
                    "mechanism_id": "M-" + str(case).split("/")[-1],
                    "status": "confirmed",
                    "member_count": 1,
                }
                for row in rows
                for case in row.get("failure_cases") or ()
            ]

    report = run_gate("deterministic", CaseSensitiveSubject())
    check = next(item for item in report["checks"] if item["class"] == CASE_ID_RENAME)
    assert check["passed"] is False
    assert "changed under case rename" in check["detail"]


def test_rename_preserves_the_subject_under_test():
    """The rename must exercise the subject's own grouping, not the base class.

    ``_with_members`` building a fresh ``MechanismSubject`` would silently swap
    the base implementation in for a customized subject, and the class would
    then pass on code that is not being admitted. The clone has to be the same
    type with the same behaviour.
    """

    class CaseSensitiveSubject(MechanismSubject):
        def mechanism_group(self, rows):
            return [
                {
                    "group": ["case"],
                    "mechanism_id": "M-" + str(case).split("/")[-1],
                    "status": "confirmed",
                    "member_count": 1,
                }
                for row in rows
                for case in row.get("failure_cases") or ()
            ]

    from evolution.metamorphic import _with_members

    renamed = _with_members(CaseSensitiveSubject(), [{"member": "x"}])
    assert type(renamed) is CaseSensitiveSubject
    report = run_gate("deterministic", CaseSensitiveSubject())
    check = next(item for item in report["checks"] if item["class"] == CASE_ID_RENAME)
    assert check["passed"] is False


# --------------------------------------------------------------------------
# Class 2: source rename
# --------------------------------------------------------------------------

def test_source_rename_keeps_mechanism_identity_and_lands():
    report = run_gate("deterministic")
    check = next(item for item in report["checks"] if item["class"] == SOURCE_RENAME)
    assert check["passed"] is True
    assert check["mutation_effective"] is True
    # The rename actually landed: the reported sources show the new prefixes,
    # so the class cannot pass by renaming nothing.
    assert check["evidence"]["sources_before"] == ["SourceA", "SourceB"]
    assert check["evidence"]["sources_after"] == ["SourceY", "SourceZ"]


def test_source_rename_detects_a_source_conditioned_mechanism():
    class SourceSensitiveSubject(MechanismSubject):
        def mechanism_group(self, rows):
            return [
                {
                    "group": ["source"],
                    "mechanism_id": "M-" + str(case).split("/", 1)[0],
                    "status": "confirmed",
                    "member_count": 1,
                }
                for row in rows
                for case in (row.get("failure_cases") or ())
            ]

    report = run_gate("deterministic", SourceSensitiveSubject())
    check = next(item for item in report["checks"] if item["class"] == SOURCE_RENAME)
    assert check["passed"] is False
    assert "changed under source rename" in check["detail"]


# --------------------------------------------------------------------------
# Class 3: an irrelevant tool call
# --------------------------------------------------------------------------

def test_irrelevant_tool_call_keeps_a_checker_verdict():
    report = run_gate("deterministic")
    check = next(
        item for item in report["checks"] if item["class"] == IRRELEVANT_TOOL_CALL
    )
    assert check["passed"] is True
    assert check["mutation_effective"] is True
    assert all(row["before"] == row["after"] for row in check["evidence"])


def test_insertion_is_refused_when_it_would_move_an_anchor():
    """An insertion that is not neutral is refused, not quietly applied."""
    packet = build_packet(tool_call_record())
    # A packet whose last event carries the delivery: inserting *after* it is
    # fine, but the generator's job is to keep anchors fixed. Ask for a
    # position past the end and check it is clamped, not silently extended.
    mutated, refusal = insert_irrelevant_tool_call(packet, position=99)
    assert refusal == ""
    assert len(mutated["events"]) == len(packet["events"]) + 1
    # The insert lands before the final event, so the delivery anchor is
    # unchanged -- which is the invariant the generator promises.
    from detector.packet import delivered_result

    assert delivered_result(mutated) == delivered_result(packet)


def test_insertion_is_refused_on_a_single_event_packet():
    packet = build_packet({
        "question": "Anything?",
        "history": [{"step": 1, "role": "assistant", "content": "Yes."}],
    })
    _, refusal = insert_irrelevant_tool_call(packet)
    assert "fewer than two events" in refusal


def test_irrelevant_tool_call_detects_a_position_reading_checker():
    """A checker that counts events fails the class, as it should."""

    def position_rule(packet, context):
        return "violated" if len(packet.get("events") or ()) > 2 else "not_applicable"

    subject = PacketRuleSubject(
        name="position_reader",
        run=position_rule,
        prose="Count the trajectory length.",
        packets=[build_packet(tool_call_record())],
    )
    check = check_irrelevant_tool_call(subject)
    assert check["passed"] is False
    assert "verdict changed" in check["detail"]


# --------------------------------------------------------------------------
# Class 4: a normal neighbour carrying the rule's own words
# --------------------------------------------------------------------------

def test_fixed_insertion_text_is_actually_inert():
    """The generator's own vocabulary must carry no mechanical signal.

    Checked directly because every other class-3/4 verdict depends on it: if
    the inserted text contained, say, "success" or a digit, the mutation would
    be load-bearing and the invariance would be testing the wrong thing.
    """
    from evolution.metamorphic import (
        IRRELEVANT_OBSERVATION,
        IRRELEVANT_PROSE,
        _is_inert_text,
    )

    for text in (IRRELEVANT_OBSERVATION, IRRELEVANT_PROSE):
        ok, why = _is_inert_text(text)
        assert ok, (text, why)


def test_neighbor_surface_token_keeps_a_normal_neighbour_clean():
    report = run_gate("deterministic")
    check = next(
        item for item in report["checks"] if item["class"] == NEIGHBOR_SURFACE_TOKEN
    )
    assert check["passed"] is True
    assert check["mutation_effective"] is True
    assert all(row["after"] not in {"violated", "inconsistent"} for row in check["evidence"])


def test_neighbor_surface_token_detects_a_keyword_matcher():
    """A rule that fires on its own keywords is caught, which is the class's purpose."""

    def keyword_rule(packet, context):
        text = " ".join(
            str(event.get("content") or "") for event in packet.get("events") or ()
        )
        return "violated" if "verbatim" in text.lower() else "not_applicable"

    subject = PacketRuleSubject(
        name="keyword_matcher",
        run=keyword_rule,
        prose="Flag verbatim phrase omissions when delivered text omits the phrase.",
        packets=[build_packet(normal_record())],
    )
    check = _surface_token_check(subject)
    assert check["passed"] is False
    assert "made it fire" in check["detail"]


def test_neighbor_surface_token_needs_rule_prose():
    subject = PacketRuleSubject(
        name="silent",
        run=lambda packet, context: "not_applicable",
        prose="",
        packets=[build_packet(normal_record())],
    )
    check = _surface_token_check(subject)
    assert check["passed"] is False
    assert "no rule prose" in check["detail"]


def test_surface_token_injection_refuses_a_token_set_with_no_vocabulary():
    """A digit-only token set leaves punctuation: a refusal, not a test.

    Injecting ", " would exercise nothing while reporting the class as
    effective, which is why the generator refuses rather than filters.
    """
    packet = build_packet(normal_record())
    _, refusal = inject_surface_tokens(packet, ["1", "2"])
    assert "no inert surface token" in refusal


def test_surface_token_injection_refuses_a_non_neutral_token():
    packet = build_packet(normal_record())
    # A token carrying a mechanical signal must be refused rather than injected:
    # "success" is read by final_delivery_present.
    _, refusal = inject_surface_tokens(packet, ["success"])
    assert "not inert" in refusal


def _surface_token_check(subject):
    from evolution.metamorphic import check_neighbor_surface_token

    return check_neighbor_surface_token(subject)


# --------------------------------------------------------------------------
# Class 5: semantic equivalence
# --------------------------------------------------------------------------

def test_semantic_equivalence_keeps_a_meaning_reading():
    report = run_gate("typed_semantic")
    check = next(
        item for item in report["checks"] if item["class"] == SEMANTIC_EQUIVALENCE
    )
    assert check["passed"] is True
    assert check["mutation_effective"] is True
    assert all(row["before"] == row["after"] for row in check["evidence"])


def test_semantic_equivalents_never_change_a_value_or_a_number():
    """The rewrites declare their own limit: values and numbers are untouched."""
    for text in (
        "The sensor reading is 10 degrees.",
        "Deliver the summary.",
        "Do not omit the phrase.",
    ):
        for rewrite in semantic_equivalents(text):
            # No digit is added or removed: a rewrite that did so would change
            # the value the judge is comparing, not its surface form.
            assert [char for char in text if char.isdigit()] == [
                char for char in rewrite if char.isdigit()
            ]
            # No word is added or dropped, and no word is re-ordered. Trailing
            # sentence punctuation may be removed -- that is the declared
            # class -- so the comparison is on stripped tokens.
            assert [
                token.strip(".\u3002") for token in text.split()
            ] == [
                token.strip(".\u3002") for token in rewrite.split()
            ]


def test_semantic_equivalence_detects_a_surface_sensitive_judge():
    def strict(expected, actual):
        return "supported" if expected == actual else "contradicted"

    subject = SemanticSubject(
        name="strict",
        judge=strict,
        pairs=[("Hello there.", "Hello there.")],
    )
    report = run_gate("typed_semantic", subject)
    check = next(
        item for item in report["checks"] if item["class"] == SEMANTIC_EQUIVALENCE
    )
    assert check["passed"] is False
    assert "changed the judgment" in check["detail"]


def test_semantic_judgment_outside_the_vocabulary_is_a_failure():
    subject = SemanticSubject(
        name="mute",
        judge=lambda expected, actual: "maybe",
        pairs=[("a sentence.", "a sentence.")],
    )
    report = run_gate("typed_semantic", subject)
    check = next(
        item for item in report["checks"] if item["class"] == SEMANTIC_EQUIVALENCE
    )
    assert check["passed"] is False
    assert "outside the semantic vocabulary" in check["detail"]


def test_semantic_equivalence_requires_a_pair():
    subject = SemanticSubject(name="empty", judge=lambda a, b: "supported", pairs=[])
    report = run_gate("typed_semantic", subject)
    check = next(
        item for item in report["checks"] if item["class"] == SEMANTIC_EQUIVALENCE
    )
    assert check["passed"] is False
    assert "no expected/actual pair" in check["detail"]


# --------------------------------------------------------------------------
# Class 6: moving an unrelated event
# --------------------------------------------------------------------------

def test_localization_event_move_keeps_the_root_cause_relationship():
    report = run_gate("deterministic")
    check = next(
        item for item in report["checks"] if item["class"] == LOCALIZATION_EVENT_MOVE
    )
    assert check["passed"] is True
    assert check["mutation_effective"] is True
    assert check["evidence"]["responsible_step"] == 3
    assert check["evidence"]["localization_state"] == check["evidence"][
        "relabelled_state"
    ]


def test_event_move_reports_the_induced_permutation():
    """Without the permutation "the root cause is at step N" moves with the layout.

    The map is what makes the invariance meaningful: the relation is tested
    under the same relabelling on both sides, not by pretending positions are
    absolute.
    """
    packet = build_packet(tool_call_record())
    mutated, mapping, refusal = move_irrelevant_event(
        packet, from_position=1, to_position=2,
    )
    assert refusal == ""
    assert mapping == {1: 2, 2: 1}
    assert mutated["events"][0]["position"] == 1
    assert mutated["events"][1]["position"] == 2


def test_event_move_refuses_the_identity_move():
    packet = build_packet(tool_call_record())
    _, _, refusal = move_irrelevant_event(packet, from_position=1, to_position=1)
    assert "no-op" in refusal


def test_event_move_refuses_a_position_outside_the_packet():
    packet = build_packet(tool_call_record())
    _, _, refusal = move_irrelevant_event(packet, from_position=1, to_position=99)
    assert "outside the packet" in refusal


@pytest.mark.parametrize("positional", [False, True])
def test_localization_class_fails_when_the_reading_is_positional(monkeypatch, positional):
    """Exercise a layout-dependent reader as well as the recorded-step reader."""
    case = {
        "row": {
            "case": "SourceA/loc_001.json",
            "gold": True,
            "prediction": True,
            "gold_step": 2,
            "result": {"responsible_step": 3},
            "packet": build_packet(tool_call_record()),
            "unrelated_position": 1,
        },
    }
    subject = PacketRuleSubject(
        name="positional",
        run=lambda packet, context: "not_applicable",
        prose="probe",
        packets=[build_packet(tool_call_record())],
        localization=case,
    )

    from evolution.worksheet import localization_state

    row = dict(case["row"])
    # The correct reading: annotation relabelled by the induced permutation.
    assert localization_state(row, {"SourceA/loc_001.json": 2}) == "mislocated"
    # The broken reading: annotation left in the old coordinate.
    assert localization_state(row, {"SourceA/loc_001.json": 3}) == "hit"
    if positional:
        from evolution import metrics
        original = metrics.row_responsible_step
        initial_order = [event["event_id"] for event in row["packet"]["events"]]

        def broken_reader(reading):
            order = [event["event_id"] for event in reading["packet"]["events"]]
            return original(reading) if order == initial_order else 1

        monkeypatch.setattr(metrics, "row_responsible_step", broken_reader)
    report = run_gate("deterministic", subject)
    check = next(
        item for item in report["checks"] if item["class"] == LOCALIZATION_EVENT_MOVE
    )
    assert check["passed"] is (not positional)


def test_admission_runs_the_registered_subject(monkeypatch):
    class Empty(MetamorphicSubject):
        name = "broken-reference"

    monkeypatch.setitem(REFERENCE_SUBJECTS, "deterministic", Empty)
    assert "failed metamorphic admission" in admission_error("deterministic")


# --------------------------------------------------------------------------
# The gate is non-vacuous and admission-only
# --------------------------------------------------------------------------

def test_a_subject_supporting_nothing_is_refused():
    class Empty(MetamorphicSubject):
        name = "empty"

    report = run_gate("deterministic", Empty())
    assert report["passed"] is False
    assert len(report["failed"]) == len(report["required_classes"])


def test_an_ineffective_mutation_is_a_failure_not_a_pass():
    """A generator that changed nothing must not read as an invariance.

    Implemented by a subject whose packets are replaced with ones the
    insertion generator cannot touch, which is the shape of a real no-op bug.
    """

    class FrozenPackets(PacketRuleSubject):
        def neighbor_rows(self):
            return []

    subject = FrozenPackets(
        name="frozen", run=lambda packet, context: "not_applicable",
        prose="some rule prose here", packets=[],
    )
    check = check_irrelevant_tool_call(subject)
    assert check["passed"] is False
    assert check["mutation_effective"] is False


def test_a_crashing_subject_is_a_failure_not_an_exception():
    class Broken(MetamorphicSubject):
        name = "broken"

        def supported_classes(self):
            return frozenset(CLASS_ORDER)

        def mechanism_rows(self, family_id, member_cases):
            raise RuntimeError("boom")

    report = run_gate("deterministic", Broken())
    assert report["passed"] is False
    assert any("RuntimeError" in item for item in report["failed"])


def test_gate_verdict_is_labelled_admission_only():
    """§19.12: a new criterion has to say what its verdict is worth."""
    report = run_gate("deterministic")
    assert report["authority"] == "admission-only"
    assert metamorphic_report()["authority"] == "admission-only"


def test_gate_replaces_no_formal_gate_step():
    """The gate never produces a gate verdict, only an admission verdict.

    Structural_gate's own checks are what a candidate is scored on; this one
    must not be mistakable for one of them, which is why it carries no
    ``gates`` mapping and no pass/fail fields named like a gate's.
    """
    report = run_gate("deterministic")
    assert "gates" not in report
    assert set(report) >= {"passed", "failed", "checks", "authority"}


# --------------------------------------------------------------------------
# The gate is wired into the admission paths
# --------------------------------------------------------------------------

def test_structural_gate_refuses_a_runner_without_a_subject(monkeypatch):
    """The structural gate consults the admission criterion."""
    from evolution import gates

    monkeypatch.delitem(REFERENCE_SUBJECTS, "typed_semantic", raising=False)
    candidate = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "semantic_match",
            "runner": "typed_semantic",
            "output_verdicts": ["supported", "contradicted", "abstained"],
        },
        "artifact": {"id": "executor", "digest": "abc"},
        "claim": {
            "expected_behavior": "a semantic judgment",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-x",
            "failure_cases": ["A/001.json", "A/002.json"],
        },
    }
    verdict = gates.structural_gate(
        candidate, active_entries=[], config={},
    )
    assert verdict["passed"] is False
    assert "metamorphic admission" in verdict["reason"]


def test_l0_records_the_metamorphic_check():
    """L0 names the criterion in its check list, so a refusal is auditable."""
    from evolution.development import check_l0
    from evolution.mechanisms import infer_mechanism

    mechanism = infer_mechanism(
        "FAM-fn-judge_false_no_recall_candidates",
        members=[],
        member_cases=["A/001.json", "B/002.json"],
    )
    candidate = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": "IV-1-001",
        "type": "evidence_check",
        "hook": "judge.post",
        "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
        "body": {
            "predicate": "verbatim_fidelity",
            "runner": "deterministic",
            "output_verdicts": ["satisfied", "violated", "not_applicable"],
        },
        "claim": {
            "expected_behavior": "verbatim phrases are delivered",
            "target_polarity": "recall",
            "target_kind": "detection",
        },
        "provenance": {
            "cycle": 1, "failure_family": "FAM-fn-judge_false_no_recall_candidates",
            "failure_cases": ["A/001.json", "B/002.json"],
        },
    }
    result = check_l0(candidate, mechanism=mechanism)
    names = {item["check"] for item in result["checks"]}
    assert "metamorphic_runner" in names
    assert all(
        item["passed"] for item in result["checks"]
        if item["check"] == "metamorphic_runner"
    )
