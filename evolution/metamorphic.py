"""Metamorphic admission gate for runners (plan §15.2 / §23.6 item 3).

P2.1 turned the runner space from an implicit if/else into a declared registry
(``detector.interventions.RUNNER_REGISTRY``), so a new executor is now added by
*declaring* it. That is exactly why the gate belongs here and now: an admission
criterion installed before the first real executor exists is a contract every
executor is measured against, whereas one installed afterwards has to be argued
against a de facto standard set by that first executor.

A metamorphic relation is an invariance: apply a transformation that *must not*
change an answer the framework depends on, and require the answer to be
unchanged. Six classes, from §15.2:

1. ``case_id_rename``          mechanism and candidate identity
2. ``source_rename``           mechanism identity across sources
3. ``irrelevant_tool_call``    a specific evidence checker's verdict
4. ``neighbor_surface_token``  a rule must not fire on a normal neighbour
5. ``semantic_equivalence``    a typed semantic judgment
6. ``localization_event_move`` the root-cause relationship

Three properties keep the gate from being decoration:

* **Every mutation must be effective.** A transformation that changed nothing
  would "pass" every relation and prove nothing, so each check reports
  ``mutation_effective`` and the gate fails when it is false.
* **Every mutation must be neutral by construction.** "Irrelevant" is not
  assumed: the generators refuse an insertion that would touch a delivery
  anchor, the routing feature set, or a signal vocabulary, and the check
  reports the precondition verdict instead of silently mutating something
  load-bearing.
* **The gate is admission-only.** Its verdict is never a promotion score, and
  it neither relaxes nor replaces any formal gate: a runner that fails it
  cannot be *named* by a candidate, which is the opposite direction from
  lowering a threshold. ``authority`` is reported so a reader cannot mistake it
  for a measurement (§19.12).

The gate needs a *subject*: the thing being admitted, expressed as calls it can
make. ``MetamorphicSubject`` is that adapter, and ``REFERENCE_SUBJECTS`` maps
each registered runner to the subject used to admit its *shape*. A real learned
executor is admitted separately, by running the gate with the executor itself
(``run_gate(runner, subject)``), which is what makes the digest in the report
meaningful: the report binds to the artifact that was actually exercised.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Mapping, Sequence

from detector.interventions import (
    COMPONENT_RUNNER,
    METAMORPHIC_CLASSES,
    PROGRAM_RUNNER,
    RUNNER_REGISTRY,
    SEMANTIC_VERDICTS,
    runner_spec,
)
from detector.packet import build_packet, delivered_result, packet_features
from detector.predicates import run_predicate
from evolution.mechanisms import infer_mechanism, member_reading, reading_signature

GATE_SCHEMA = "evolution.metamorphic_gate.v1"

# The transformations take a value the framework acts on and rewrite it in a
# way the answer must not notice. Each name is one §15.2 class.
CASE_ID_RENAME = "case_id_rename"
SOURCE_RENAME = "source_rename"
IRRELEVANT_TOOL_CALL = "irrelevant_tool_call"
NEIGHBOR_SURFACE_TOKEN = "neighbor_surface_token"
SEMANTIC_EQUIVALENCE = "semantic_equivalence"
LOCALIZATION_EVENT_MOVE = "localization_event_move"

#: Which class each transformation implements, in the plan's order.
CLASS_ORDER = (
    CASE_ID_RENAME,
    SOURCE_RENAME,
    IRRELEVANT_TOOL_CALL,
    NEIGHBOR_SURFACE_TOKEN,
    SEMANTIC_EQUIVALENCE,
    LOCALIZATION_EVENT_MOVE,
)

# A verdict in this set means "the rule fired". It is the same vocabulary
# ``check_l0`` treats as a normal-neighbour violation, so the metamorphic
# relation and the development check agree on what misfiring means.
FIRING_VERDICTS = frozenset({"violated", "inconsistent", "unresolved", "missing"})

# --------------------------------------------------------------------------
# Neutrality: what makes an insertion "irrelevant" is defined, not assumed.
# --------------------------------------------------------------------------

# A tool interaction may be inserted into a trajectory without changing any
# mechanical reading only if it introduces none of the vocabularies the
# predicates read. These are chosen to be disjoint from every signal the
# deterministic executors look for: no answer-tool hint, no movement verb, no
# required-action noun, no permission phrasing, no digits, no completion word.
IRRELEVANT_TOOL_NAME = "unrelated_lookup"
IRRELEVANT_TOOL_ARGUMENTS = {"topic": "unrelated"}
IRRELEVANT_OBSERVATION = "no result of interest"
IRRELEVANT_PROSE = "Noted, continuing."

# Signals that must not appear in an inserted event. Kept explicit so a future
# predicate that reads one of these words makes the generator refusable rather
# than quietly load-bearing.
_INERT_FORBIDDEN_SUBSTRINGS = (
    "success", "finish", "final_answer", "submit_answer", "terminate",
    "would you like", "should i", "do you want", "shall i", "may i",
    "swing by", "head over", "go to", "drive to", "walk to", "visit",
    "stop by", "drop by", "proceed to", "travel to",
)


def _is_inert_text(text: str) -> tuple[bool, str]:
    """Whether a string is free of every mechanical signal the predicates read."""
    lowered = str(text or "").lower()
    for needle in _INERT_FORBIDDEN_SUBSTRINGS:
        if needle in lowered:
            return False, f"carries the signal {needle!r}"
    if any(char.isdigit() for char in lowered):
        return False, "carries digits, which set has_numeric_content"
    return True, ""


# --------------------------------------------------------------------------
# The subject: what the gate asks of the thing it admits
# --------------------------------------------------------------------------

class MetamorphicSubject:
    """The gate's view of a runner, expressed as the calls each class needs.

    A subject implements only the classes its runner can reach, and
    ``supported_classes`` reports which. Implementing nothing is legal and is
    reported as such -- the default is the empty set, so a subject that claims
    a class it does not implement is refused rather than silently skipped.
    """

    name = ""
    #: Free-form identity of what was exercised, e.g. an artifact digest.
    identity: Mapping[str, str] = {}

    def supported_classes(self) -> frozenset[str]:
        """The classes this subject can be exercised on.

        Empty by default rather than raising: the gate has to be able to
        *report* an unimplemented class, and a subject that cannot be asked
        what it supports cannot be admitted or refused.
        """
        return frozenset()

    # -- class 1/2: the mechanism layer ----------------------------------
    def mechanism_rows(
        self, family_id: str, member_cases: Sequence[str],
    ) -> Sequence[Mapping[str, Any]]:
        """Label-free rows the mechanism reading is taken from."""
        return ()

    def mechanism_group(
        self, rows: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        """The mechanism(s) the group layer derives from the rows."""
        return ()

    # -- class 3/4: a verdict-producing rule -----------------------------
    def verdict(
        self, packet: Mapping[str, Any], context: Mapping[str, Any],
    ) -> str:
        return ""

    def rule_prose(self) -> str:
        return ""

    def neighbor_rows(self) -> Sequence[Mapping[str, Any]]:
        return ()

    # -- class 5: a typed semantic judgment ------------------------------
    def semantic_verdict(
        self, expected: str, actual: str,
    ) -> str:
        return ""

    def semantic_pairs(self) -> Sequence[tuple[str, str]]:
        return ()

    # -- class 6: the localization reading -------------------------------
    def localization_case(self) -> dict[str, Any]:
        return {}


class _MemberCarryingSubject(MetamorphicSubject):
    """A subject with renamable members, used by the identity classes.

    A separate base so the rename check can ask "can you be renamed?" without
    knowing which concrete subject it holds, and so a subclass that customizes
    grouping keeps its own behaviour through the rename.
    """

    def with_members(
        self, members: Sequence[Mapping[str, Any]],
    ) -> "_MemberCarryingSubject":
        raise NotImplementedError


class MechanismSubject(_MemberCarryingSubject):
    """Mechanism identity, admitted for the two renaming classes.

    The subject is deliberately the *grouping* layer rather than a single
    mechanism: a renamed case or source has to leave every mechanism the family
    layer derives unchanged, not just the one mechanism the caller happened to
    look at.
    """

    name = "mechanism_layer"

    def __init__(
        self,
        *,
        family_id: str = "FAM-fn-judge_false_no_recall_candidates",
        member_cases: Sequence[str] = ("SourceA/case_001.json", "SourceB/case_002.json"),
        members: Sequence[Mapping[str, Any]] | None = None,
        path_signature: Mapping[str, Any] | None = None,
    ) -> None:
        self.family_id = str(family_id)
        self.member_cases = [str(case) for case in member_cases]
        self._members = (
            [dict(member) for member in members]
            if members is not None else [dict(_member_summary()) for _ in self.member_cases]
        )
        self._signature = dict(path_signature or _PATH_SIGNATURE)

    def with_members(
        self, members: Sequence[Mapping[str, Any]],
    ) -> "MechanismSubject":
        return type(self)(
            family_id=self.family_id,
            member_cases=self.member_cases,
            members=members,
            path_signature=self._signature,
        )

    def supported_classes(self) -> frozenset[str]:
        return frozenset({CASE_ID_RENAME, SOURCE_RENAME, LOCALIZATION_EVENT_MOVE})

    def mechanism_rows(
        self, family_id: str, member_cases: Sequence[str],
    ) -> Sequence[Mapping[str, Any]]:
        return [
            {
                "failure_family": family_id,
                "failure_cases": list(member_cases),
                "count": len(member_cases),
                "member_summaries": [dict(member) for member in self._members],
                "local_states": ["mislocated"] * len(member_cases),
                "path_signature": dict(self._signature),
                "target_kind": "detection",
            }
        ]

    def mechanism_group(
        self, rows: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        """Derive one mechanism per subgroup, exactly as the worksheet does.

        ``member_reading`` -> ``reading_signature`` -> ``infer_mechanism`` is
        the same chain ``worksheet.mechanism_subgroups`` runs; re-deriving it
        from the public functions keeps the gate on the identity that is
        actually minted instead of on a copy of the rule.
        """
        out: list[dict[str, Any]] = []
        for row in rows:
            family_id = str(row.get("failure_family") or "")
            cases = [str(case) for case in row.get("failure_cases") or ()]
            members = [dict(member) for member in row.get("member_summaries") or ()]
            states = list(row.get("local_states") or ())
            groups: dict[tuple[str, ...], list[int]] = {}
            for index, member in enumerate(members):
                reading = member_reading(
                    member,
                    family_id=family_id,
                    objective="detection",
                    local_state=(
                        states[index] if index < len(states) else "mislocated"
                    ),
                )
                groups.setdefault(reading_signature(reading), []).append(index)
            for signature_key, indices in sorted(groups.items()):
                group = [members[index] for index in indices]
                mechanism = infer_mechanism(
                    family_id,
                    target_kind="detection",
                    members=group,
                    path_signature=row.get("path_signature"),
                    local_states=[states[index] for index in indices if index < len(states)],
                    member_cases=[cases[index] for index in indices],
                    count=len(indices),
                )
                out.append({
                    "group": list(signature_key),
                    "mechanism_id": mechanism.get("mechanism_id"),
                    "status": mechanism.get("status"),
                    "member_count": len(indices),
                })
        return out

    def localization_case(self) -> dict[str, Any]:
        return _localization_case()


class PacketRuleSubject(MetamorphicSubject):
    """A verdict-producing rule over packets, for classes 3, 4 and 6.

    The rule is injected as a callable rather than hard-coded as a predicate
    name, so a ``typed_dsl`` program, a built-in predicate and a scripted test
    double all use one implementation of the gate.
    """

    name = "packet_rule"

    def __init__(
        self,
        *,
        name: str,
        run: Any,
        prose: str = "",
        packets: Sequence[Mapping[str, Any]] = (),
        localization: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = str(name)
        self._run = run
        self._prose = str(prose)
        self._packets = [dict(packet) for packet in packets]
        self._localization = dict(localization or {})

    def supported_classes(self) -> frozenset[str]:
        return frozenset({
            IRRELEVANT_TOOL_CALL, NEIGHBOR_SURFACE_TOKEN, LOCALIZATION_EVENT_MOVE,
        })

    def verdict(
        self, packet: Mapping[str, Any], context: Mapping[str, Any],
    ) -> str:
        return str(self._run(packet, dict(context)) or "")

    def rule_prose(self) -> str:
        return self._prose

    def neighbor_rows(self) -> Sequence[Mapping[str, Any]]:
        return [dict(packet) for packet in self._packets]

    def localization_case(self) -> dict[str, Any]:
        return dict(self._localization)


class SemanticSubject(MetamorphicSubject):
    """A typed semantic judgment, for the semantic-equivalence class."""

    name = "semantic_checker"

    def __init__(self, *, name: str, judge: Any, pairs: Sequence[Sequence[str]]):
        self.name = str(name)
        self._judge = judge
        self._pairs = [(str(left), str(right)) for left, right in pairs]

    def supported_classes(self) -> frozenset[str]:
        return frozenset({SEMANTIC_EQUIVALENCE})

    def semantic_verdict(self, expected: str, actual: str) -> str:
        return str(self._judge(str(expected), str(actual)) or "")

    def semantic_pairs(self) -> Sequence[tuple[str, str]]:
        return list(self._pairs)


class CompositeSubject(MetamorphicSubject):
    """One admission subject assembled from the layers a runner's classes need.

    The six classes do not all belong to the same layer: renaming a case is a
    property of the mechanism reading, inserting a tool call is a property of
    the rule's verdict, and equivalence is a property of the semantic judgment.
    A runner that must survive all three therefore owes a subject that covers
    all three, and composing them here keeps each class's implementation next
    to the layer it actually tests instead of one monolithic double.

    ``supported_classes`` is the union of the parts, so a composite that is
    missing a layer is refused by the gate for exactly that class rather than
    being reported as admitted.
    """

    def __init__(
        self,
        *,
        name: str,
        mechanism: MetamorphicSubject | None = None,
        packet_rule: MetamorphicSubject | None = None,
        semantic: MetamorphicSubject | None = None,
        identity: Mapping[str, str] | None = None,
    ) -> None:
        self.name = str(name)
        self.identity = dict(identity or {})
        self._parts: dict[str, MetamorphicSubject] = {}
        if mechanism is not None:
            self._parts["mechanism"] = mechanism
        if packet_rule is not None:
            self._parts["packet_rule"] = packet_rule
        if semantic is not None:
            self._parts["semantic"] = semantic

    def _layer_for(self, class_name: str) -> MetamorphicSubject | None:
        if class_name in {CASE_ID_RENAME, SOURCE_RENAME}:
            return self._parts.get("mechanism")
        if class_name in {IRRELEVANT_TOOL_CALL, NEIGHBOR_SURFACE_TOKEN}:
            return self._parts.get("packet_rule")
        if class_name == SEMANTIC_EQUIVALENCE:
            return self._parts.get("semantic")
        if class_name == LOCALIZATION_EVENT_MOVE:
            # Either layer can carry it: the mechanism reading is the one that
            # must not move, and a rule that reads the packet layout is the
            # other half. Preference order is mechanism first because the
            # recorded responsible step lives on that side.
            return self._parts.get("mechanism") or self._parts.get("packet_rule")
        return None

    def supported_classes(self) -> frozenset[str]:
        out: set[str] = set()
        for class_name in CLASS_ORDER:
            if self._layer_for(class_name) is not None:
                out.add(class_name)
        return frozenset(out)

    def mechanism_rows(self, family_id, member_cases):
        layer = self._parts.get("mechanism")
        return layer.mechanism_rows(family_id, member_cases) if layer else ()

    def mechanism_group(self, rows):
        layer = self._parts.get("mechanism")
        return layer.mechanism_group(rows) if layer else ()

    def verdict(self, packet, context):
        layer = self._parts.get("packet_rule")
        return layer.verdict(packet, context) if layer else ""

    def rule_prose(self) -> str:
        layer = self._parts.get("packet_rule")
        return layer.rule_prose() if layer else ""

    def neighbor_rows(self):
        layer = self._parts.get("packet_rule")
        return layer.neighbor_rows() if layer else ()

    def semantic_verdict(self, expected, actual):
        layer = self._parts.get("semantic")
        return layer.semantic_verdict(expected, actual) if layer else ""

    def semantic_pairs(self):
        layer = self._parts.get("semantic")
        return layer.semantic_pairs() if layer else ()

    def localization_case(self):
        layer = self._layer_for(LOCALIZATION_EVENT_MOVE)
        return layer.localization_case() if layer else {}


# --------------------------------------------------------------------------
# Fixtures: small, label-free, and shared by the gate and its tests
# --------------------------------------------------------------------------

#: The path signature of a judge-false family: the shape several §4 tests use.
_PATH_SIGNATURE = {
    "judge_positive": False,
    "upgrade_positive": False,
    "recall_path": "not_needed",
    "defense_verdict": "none",
    "positive_source": "none",
}


def _member_summary(
    *,
    name: str = "member_001",
    predicates: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One label-free member summary, in the shape the worksheet emits."""
    return {
        "member": name,
        "contracts": ["verbatim_content"],
        "stages": {
            "judge": "ok",
            "recall_scanner": "not_needed",
            "causal_upgrade": "not_needed",
            "step_tracer": "ok",
            "defense": "ok",
        },
        "judge_positive": False,
        "recall_path": "not_needed",
        "upgrade_positive": False,
        "defense_verdict": "none",
        "predicates": dict(predicates or {
            "required_action_present": "satisfied",
            "verbatim_fidelity": "violated",
            "evidence_refs_resolve": "not_applicable",
            "contract_coverage": "covered",
            "delivered_anchor_consistency": "consistent",
            "final_delivery_present": "present",
        }),
        "typed_samples": {"packet_features": ["has_tool_calls"]},
    }


def _record(
    *,
    question: str,
    history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {"question": question, "history": [dict(row) for row in history]}


def normal_record() -> dict[str, Any]:
    """A well-behaved trajectory: the required action was taken and delivered.

    It is the neighbour the surface-token class perturbs: a normal case that
    acquires the rule's vocabulary must still not fire the rule.
    """
    return _record(
        question="Please check the weather in Harbin and summarise it for me.",
        history=[
            {
                "step": 1,
                "role": "assistant",
                "content": "I will look that up.",
                "tool_calls": [
                    {"name": "weather_lookup", "arguments": {"city": "Harbin"}}
                ],
                "tool_responses": ["Harbin is cold in winter."],
            },
            {
                "step": 2,
                "role": "assistant",
                "content": "Harbin is cold in winter.",
            },
        ],
    )


def tool_call_record() -> dict[str, Any]:
    """A trajectory with a delivery anchor, used for the insertion class."""
    return _record(
        question=(
            "Verify that the report quotes the phrase \"ten degrees\" and then "
            "deliver the summary."
        ),
        history=[
            {
                "step": 1,
                "role": "assistant",
                "content": "Retrieving the measurement.",
                "tool_calls": [
                    {"name": "measurement_lookup", "arguments": {"sensor": "outdoor"}}
                ],
                "tool_responses": ["ten degrees"],
            },
            {
                "step": 2,
                "role": "assistant",
                "content": "The report says \"ten degrees\".",
            },
        ],
    )


def _localization_case() -> dict[str, Any]:
    """A convicted localization case whose root cause sits at step 2.

    ``gold_steps`` is the evaluator-side annotation and stays out of every
    detector-facing value; the gate only uses it to compute the same
    ``mislocated`` / ``hit`` reading the evaluator uses.
    """
    return {
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


# --------------------------------------------------------------------------
# The six relations
# --------------------------------------------------------------------------

def _result(
    class_name: str,
    *,
    passed: bool,
    mutation_effective: bool,
    detail: str = "",
    evidence: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if evidence is None:
        payload: Any = {}
    elif isinstance(evidence, Mapping):
        payload = dict(evidence)
    else:
        # A per-packet trace is a list, and it has to survive as one: collapsing
        # it into a mapping would hide which packet moved.
        payload = [dict(entry) for entry in evidence]
    return {
        "class": class_name,
        "passed": bool(passed),
        # A mutation that changed nothing cannot demonstrate an invariance.
        # Reported per check so an ineffective generator is a failure, not a
        # free pass.
        "mutation_effective": bool(mutation_effective),
        "detail": str(detail),
        "evidence": payload,
    }


def check_case_id_rename(subject: MetamorphicSubject) -> dict[str, Any]:
    """§15.2 (1): renaming a case id must not change the mechanism.

    The rename is applied to the member cases and to the member names -- the
    two places a case identifier can reach a diagnosis -- and the derived
    mechanisms must be identical. Effectiveness is checked by the rename
    actually changing the input, so a generator wired to a no-op is reported.
    """
    cases = ["SourceA/case_001.json", "SourceB/case_002.json"]
    members = [_member_summary(name="member_001"), _member_summary(name="member_002")]
    before = list(subject.mechanism_group(subject.mechanism_rows(
        "FAM-fn-judge_false_no_recall_candidates", cases,
    )))
    renamed = [f"SourceA/renamed_{index:03d}.json" for index in range(len(cases))]
    renamed_members = [
        {**member, "member": f"renamed_member_{index:03d}"}
        for index, member in enumerate(members)
    ]
    renamed_subject = _with_members(subject, renamed_members)
    after = list(renamed_subject.mechanism_group(renamed_subject.mechanism_rows(
        "FAM-fn-judge_false_no_recall_candidates", renamed,
    )))
    effective = renamed != cases and renamed_members != members
    same = _group_projection(before) == _group_projection(after)
    return _result(
        CASE_ID_RENAME,
        passed=same and effective,
        mutation_effective=effective,
        detail=(
            "" if same else
            f"mechanism identity changed under case rename: "
            f"{_group_projection(before)} -> {_group_projection(after)}"
        ),
        evidence={
            "before": _group_projection(before),
            "after": _group_projection(after),
        },
    )


def check_source_rename(subject: MetamorphicSubject) -> dict[str, Any]:
    """§15.2 (2): renaming the source must not change the mechanism.

    Two cases from different sources must keep their mechanism when the source
    prefix changes, and the rename must *actually* land -- the reported sources
    have to show the new prefix, or the check would pass on a no-op.
    """
    cases = ["SourceA/case_001.json", "SourceB/case_002.json"]
    before = list(subject.mechanism_group(subject.mechanism_rows(
        "FAM-fn-judge_false_no_recall_candidates", cases,
    )))
    renamed = ["SourceZ/case_001.json", "SourceY/case_002.json"]
    after = list(subject.mechanism_group(subject.mechanism_rows(
        "FAM-fn-judge_false_no_recall_candidates", renamed,
    )))
    sources_before = {str(case).split("/", 1)[0] for case in cases}
    sources_after = {str(case).split("/", 1)[0] for case in renamed}
    effective = sources_after != sources_before and renamed != cases
    same = _group_projection(before) == _group_projection(after)
    return _result(
        SOURCE_RENAME,
        passed=same and effective,
        mutation_effective=effective,
        detail=(
            "" if same else
            f"mechanism identity changed under source rename: "
            f"{_group_projection(before)} -> {_group_projection(after)}"
        ),
        evidence={
            "sources_before": sorted(sources_before),
            "sources_after": sorted(sources_after),
            "mechanisms": _group_projection(after),
        },
    )


def insert_irrelevant_tool_call(
    packet: Mapping[str, Any],
    *,
    position: int | None = None,
) -> tuple[dict[str, Any], str]:
    """Insert an unrelated tool call in the middle of a trajectory.

    Returns the mutated packet and a reason string (empty when the insertion is
    admissible). The insertion is refused, rather than adjusted, whenever it
    would touch something the mechanical readings depend on: the question, the
    last event, the delivered-anchor set, the routing feature set, or the
    signal vocabulary. Refusing is the honest outcome -- a "neutral" mutation
    that moved an anchor would be testing something else.
    """
    ok, why = _is_inert_text(IRRELEVANT_OBSERVATION)
    if not ok:
        return dict(packet), f"fixed insertion text is not inert: {why}"
    ok, why = _is_inert_text(IRRELEVANT_PROSE)
    if not ok:
        return dict(packet), f"fixed insertion prose is not inert: {why}"
    events = [dict(event) for event in packet.get("events") or ()]
    if len(events) < 2:
        return dict(packet), "packet has fewer than two events; nothing is safe to insert before"
    before_anchors = delivered_result(packet)
    before_features = packet_features(packet)
    index = len(events) - 1 if position is None else int(position)
    index = max(1, min(index, len(events) - 1))
    inserted = {
        "event_id": "event_000_unrelated",
        "position": 0,
        "role": "assistant",
        "content": IRRELEVANT_PROSE,
        "tool_interactions": [{
            "interaction_id": "event_000_unrelated_tool_01",
            "tool_name": IRRELEVANT_TOOL_NAME,
            "arguments": dict(IRRELEVANT_TOOL_ARGUMENTS),
            "observation": IRRELEVANT_OBSERVATION,
        }],
    }
    mutated_events = [*events[:index], inserted, *events[index:]]
    for ordinal, event in enumerate(mutated_events, start=1):
        event["position"] = ordinal
    mutated = {**dict(packet), "events": mutated_events}
    after_anchors = delivered_result(mutated)
    if after_anchors != before_anchors:
        return dict(packet), "insertion would move a delivered-result anchor"
    if packet_features(mutated) != before_features:
        return dict(packet), "insertion would change the routing feature set"
    return mutated, ""


def check_irrelevant_tool_call(subject: MetamorphicSubject) -> dict[str, Any]:
    """§15.2 (3): an unrelated tool call must not change a checker's verdict.

    Run over the subject's own packets, so the class tests the checker the
    subject was admitted with, and require the verdict to be identical before
    and after. When the insertion is refused as non-neutral the check fails
    with the refusal reason: a class that could not be exercised is not a pass.
    """
    rows = list(subject.neighbor_rows())
    if not rows:
        return _result(
            IRRELEVANT_TOOL_CALL, passed=False, mutation_effective=False,
            detail="the subject declares no packet to insert into",
        )
    evidence: list[dict[str, Any]] = []
    effective = False
    for index, packet in enumerate(rows):
        mutated, refusal = insert_irrelevant_tool_call(packet)
        if refusal:
            return _result(
                IRRELEVANT_TOOL_CALL, passed=False, mutation_effective=False,
                detail=f"packet {index}: {refusal}",
            )
        before = subject.verdict(packet, {})
        after = subject.verdict(mutated, {})
        evidence.append({"packet": index, "before": before, "after": after})
        if len(mutated.get("events") or ()) != len(packet.get("events") or ()):
            effective = True
        if before != after:
            return _result(
                IRRELEVANT_TOOL_CALL, passed=False, mutation_effective=True,
                detail=(
                    f"packet {index}: verdict changed from {before!r} to "
                    f"{after!r} when an unrelated tool call was inserted"
                ),
                evidence=evidence,
            )
    return _result(
        IRRELEVANT_TOOL_CALL, passed=effective, mutation_effective=effective,
        detail="" if effective else "no packet changed shape; nothing was tested",
        evidence=evidence,
    )


def inject_surface_tokens(
    packet: Mapping[str, Any], tokens: Sequence[str],
) -> tuple[dict[str, Any], str]:
    """Insert the rule's own vocabulary into a normal neighbour.

    A rule that fires as soon as a normal case contains its keywords has
    learned the words rather than the mechanism. As with the tool-call
    insertion, non-neutral insertions are refused: the token text must be
    inert, the routing features must not move, and the delivered anchors must
    not move.
    """
    wanted = [str(token) for token in tokens if str(token)]
    if not wanted:
        return dict(packet), "no surface tokens to inject"
    text = ", ".join(sorted(set(wanted)))
    for field in (IRRELEVANT_PROSE, text):
        ok, why = _is_inert_text(field)
        if not ok:
            # Digits are the usual reason a rule's own prose is not inert; the
            # injected sentence is then reduced to the inert subset rather than
            # the whole class being skipped.
            if "digits" in why:
                continue
            return dict(packet), f"injected text is not inert: {why}"
    text = "".join(char for char in text if not char.isdigit())
    # Punctuation alone carries no vocabulary, so a token set that reduces to
    # separators is a refusal: injecting ", " would test nothing while
    # reporting a pass.
    if not any(char.isalnum() for char in text):
        return dict(packet), "no inert surface token remained after filtering"
    events = [dict(event) for event in packet.get("events") or ()]
    if len(events) < 2:
        return dict(packet), "packet has fewer than two events"
    before_anchors = delivered_result(packet)
    before_features = packet_features(packet)
    injected_text = f"{IRRELEVANT_PROSE} {text}"
    ok, why = _is_inert_text(injected_text)
    if not ok:
        return dict(packet), f"injected sentence is not inert: {why}"
    inserted = {
        "event_id": "event_000_surface",
        "position": 0,
        "role": "assistant",
        "content": injected_text,
        "tool_interactions": [],
    }
    mutated_events = [events[0], inserted, *events[1:]]
    for ordinal, event in enumerate(mutated_events, start=1):
        event["position"] = ordinal
    mutated = {**dict(packet), "events": mutated_events}
    if delivered_result(mutated) != before_anchors:
        return dict(packet), "injection would move a delivered-result anchor"
    if packet_features(mutated) != before_features:
        return dict(packet), "injection would change the routing feature set"
    return mutated, ""


def check_neighbor_surface_token(subject: MetamorphicSubject) -> dict[str, Any]:
    """§15.2 (4): a normal neighbour carrying the rule's words must not fire."""
    rows = list(subject.neighbor_rows())
    if not rows:
        return _result(
            NEIGHBOR_SURFACE_TOKEN, passed=False, mutation_effective=False,
            detail="the subject declares no normal neighbour packet",
        )
    prose = subject.rule_prose()
    tokens = [
        word for word in str(prose).replace(",", " ").split()
        if len(word) >= 4 and word.isascii()
    ][:12]
    if not tokens:
        return _result(
            NEIGHBOR_SURFACE_TOKEN, passed=False, mutation_effective=False,
            detail="the subject declares no rule prose to take surface tokens from",
        )
    evidence: list[dict[str, Any]] = []
    effective = False
    for index, packet in enumerate(rows):
        base = subject.verdict(packet, {})
        if base in FIRING_VERDICTS:
            return _result(
                NEIGHBOR_SURFACE_TOKEN, passed=False, mutation_effective=False,
                detail=(
                    f"packet {index}: the rule already fires on the normal "
                    f"neighbour ({base!r}); the class cannot distinguish a "
                    "keyword match from a real one"
                ),
                evidence=evidence,
            )
        mutated, refusal = inject_surface_tokens(packet, tokens)
        if refusal:
            return _result(
                NEIGHBOR_SURFACE_TOKEN, passed=False, mutation_effective=False,
                detail=f"packet {index}: {refusal}",
            )
        after = subject.verdict(mutated, {})
        effective = True
        evidence.append({
            "packet": index, "tokens": tokens, "before": base, "after": after,
        })
        if after in FIRING_VERDICTS:
            return _result(
                NEIGHBOR_SURFACE_TOKEN, passed=False, mutation_effective=True,
                detail=(
                    f"packet {index}: adding the rule's own surface tokens made "
                    f"it fire ({after!r}) on a normal neighbour"
                ),
                evidence=evidence,
            )
    return _result(
        NEIGHBOR_SURFACE_TOKEN, passed=effective, mutation_effective=effective,
        detail="" if effective else "no neighbour was perturbed",
        evidence=evidence,
    )


#: The rewrites this gate treats as meaning-preserving. Documented rather than
#: implied, because "semantically equivalent" is a claim: these change surface
#: form only -- Unicode form, quote style, whitespace, sentence-final
#: punctuation -- and never a value, a negation, or a number. A checker that
#: flips a verdict under one of them is reading the surface, not the meaning.
#:
#: The class is *declared* rather than discovered, and a checker must meet it.
#: That direction matters: if the generator instead inferred "equivalence" from
#: whatever the checker already accepted, the class would pass by construction
#: and test nothing.
def semantic_equivalents(text: str) -> list[str]:
    """Surface rewrites of ``text`` that must not change a judgment."""
    raw = str(text)
    rewritten = {unicodedata.normalize("NFC", raw)}
    quoted = raw
    for source, target in (
        ("\u201c", '"'), ("\u201d", '"'),
        ("\u2018", "'"), ("\u2019", "'"),
        ("\u00ab", '"'), ("\u00bb", '"'),
    ):
        quoted = quoted.replace(source, target)
    rewritten.add(quoted)
    rewritten.add(" ".join(raw.split()))
    rewritten.add(f"{raw.rstrip()} ")
    for candidate in ("\u3002", "."):
        if raw.endswith(candidate):
            rewritten.add(raw[: -len(candidate)].rstrip())
    # A re-wrapped line is the same sentence with different whitespace.
    tokens = raw.split()
    if len(tokens) >= 6:
        half = len(tokens) // 2
        rewritten.add(" ".join(tokens[:half]) + "\n" + " ".join(tokens[half:]))
    return sorted(
        item for item in rewritten
        if item.strip() and item != raw
    )


def check_semantic_equivalence(subject: MetamorphicSubject) -> dict[str, Any]:
    """§15.2 (5): an equivalent rewrite of expected/actual keeps the judgment."""
    pairs = list(subject.semantic_pairs())
    if not pairs:
        return _result(
            SEMANTIC_EQUIVALENCE, passed=False, mutation_effective=False,
            detail="the subject declares no expected/actual pair to rewrite",
        )
    evidence: list[dict[str, Any]] = []
    effective = False
    for index, (expected, actual) in enumerate(pairs):
        base = subject.semantic_verdict(expected, actual)
        if base not in SEMANTIC_VERDICTS:
            return _result(
                SEMANTIC_EQUIVALENCE, passed=False, mutation_effective=False,
                detail=(
                    f"pair {index}: verdict {base!r} is outside the semantic "
                    f"vocabulary {sorted(SEMANTIC_VERDICTS)}"
                ),
            )
        for field, value, other in (
            ("expected", expected, actual),
            ("actual", actual, expected),
        ):
            for rewrite in semantic_equivalents(value):
                effective = True
                if field == "expected":
                    observed = subject.semantic_verdict(rewrite, other)
                else:
                    observed = subject.semantic_verdict(other, rewrite)
                evidence.append({
                    "pair": index, "field": field, "rewrite": rewrite,
                    "before": base, "after": observed,
                })
                if observed != base:
                    return _result(
                        SEMANTIC_EQUIVALENCE, passed=False, mutation_effective=True,
                        detail=(
                            f"pair {index}: rewriting {field} to {rewrite!r} "
                            f"changed the judgment from {base!r} to {observed!r}"
                        ),
                        evidence=evidence[-3:],
                    )
    return _result(
        SEMANTIC_EQUIVALENCE, passed=effective, mutation_effective=effective,
        detail="" if effective else "no rewrite was produced; nothing was tested",
        evidence=evidence[:6],
    )


def move_irrelevant_event(
    packet: Mapping[str, Any],
    *,
    from_position: int,
    to_position: int,
) -> tuple[dict[str, Any], dict[int, int], str]:
    """Move one event and report the induced position permutation.

    Returns the mutated packet, the map from old position to new position, and
    a refusal reason (empty when admissible). The permutation is the point: a
    position that moves is exactly why "the root cause is at step N" cannot be
    compared naively across the transformation, and returning the map keeps the
    relation testable instead of assuming the coordinates did not move.
    """
    events = [dict(event) for event in packet.get("events") or ()]
    total = len(events)
    source = int(from_position)
    target = int(to_position)
    if not 1 <= source <= total or not 1 <= target <= total:
        return dict(packet), {}, "position outside the packet"
    if source == target:
        return dict(packet), {}, "the move would be a no-op"
    order = list(range(1, total + 1))
    order.insert(target - 1, order.pop(source - 1))
    moved = [events[index - 1] for index in order]
    for ordinal, event in enumerate(moved, start=1):
        event["position"] = ordinal
    mutated = {**dict(packet), "events": moved}
    mapping = {old: new for new, old in enumerate(order, start=1)}
    return mutated, mapping, ""


def check_localization_event_move(subject: MetamorphicSubject) -> dict[str, Any]:
    """§15.2 (6): moving an unrelated event keeps the root-cause relationship.

    Two readings are required, and the difference between them is the whole
    point of the class:

    * the *recorded* responsible step must not be recomputed from the packet's
      layout -- the reading is a function of the result, so moving an event
      must leave it exactly as it was;
    * the *evaluator's* hit/mislocated decision must be invariant under the
      coordinate change, i.e. when both the asserted step and the annotation
      are relabelled by the same permutation the decision is unchanged.

    Without the second reading the class would be vacuous: "step 2" after a
    move is not the same coordinate as "step 2" before it.
    """
    from evolution.metrics import row_responsible_step
    from evolution.worksheet import localization_state

    case = subject.localization_case()
    if not case:
        return _result(
            LOCALIZATION_EVENT_MOVE, passed=False, mutation_effective=False,
            detail="the subject declares no localization case",
        )
    row = dict(case.get("row") or {})
    packet = row.get("packet") or {}
    unrelated = int(case.get("row", {}).get("unrelated_position") or 1)
    gold_step = row.get("gold_step")
    if not packet or gold_step is None:
        return _result(
            LOCALIZATION_EVENT_MOVE, passed=False, mutation_effective=False,
            detail="the localization case needs a packet and a gold step",
        )
    before_step = row_responsible_step(row)
    before_state = localization_state(row, {str(row.get("case")): int(gold_step)})
    mutated, mapping, refusal = move_irrelevant_event(
        packet, from_position=unrelated, to_position=len(packet.get("events") or ()),
    )
    if refusal:
        return _result(
            LOCALIZATION_EVENT_MOVE, passed=False, mutation_effective=False,
            detail=f"no admissible move: {refusal}",
        )
    after_step = row_responsible_step(row)
    relocated = {
        **row,
        "result": {
            **(row.get("result") or {}),
            "responsible_step": mapping.get(int(before_step), int(before_step)),
        },
    }
    after_state = localization_state(
        relocated, {str(row.get("case")): mapping.get(int(gold_step), int(gold_step))},
    )
    stable = before_step == after_step and before_state == after_state
    effective = mapping.get(unrelated) != unrelated
    return _result(
        LOCALIZATION_EVENT_MOVE,
        passed=stable and effective,
        mutation_effective=effective,
        detail=(
            "" if stable else
            f"root-cause reading moved: step {before_step} -> {after_step}, "
            f"state {before_state!r} -> {after_state!r}"
        ),
        evidence={
            "position_map": {str(key): value for key, value in mapping.items()},
            "responsible_step": before_step,
            "localization_state": before_state,
            "relabelled_state": after_state,
        },
    )


CHECKERS: dict[str, Any] = {
    CASE_ID_RENAME: check_case_id_rename,
    SOURCE_RENAME: check_source_rename,
    IRRELEVANT_TOOL_CALL: check_irrelevant_tool_call,
    NEIGHBOR_SURFACE_TOKEN: check_neighbor_surface_token,
    SEMANTIC_EQUIVALENCE: check_semantic_equivalence,
    LOCALIZATION_EVENT_MOVE: check_localization_event_move,
}


def _with_members(
    subject: MetamorphicSubject, members: Sequence[Mapping[str, Any]],
) -> MetamorphicSubject:
    """A copy of a mechanism subject with different member names.

    The copy keeps the subject's own type and constructor arguments, so a
    subject that customizes the grouping layer is renamed *as itself*. Building
    a base ``MechanismSubject`` here instead would silently substitute the
    base implementation for the subject under test, and the class would then
    pass or fail on code that is not being admitted -- the exact kind of
    vacuous pass this gate exists to prevent.
    """
    if isinstance(subject, _MemberCarryingSubject):
        return subject.with_members(members)
    return subject


def _group_projection(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The mechanism identity of a group reading, with case ids dropped.

    A projection rather than the groups themselves: the member case list is
    *supposed* to change under a case rename, and comparing it would test the
    rename instead of the identity.
    """
    return sorted(
        (
            {
                "group": list(group.get("group") or ()),
                "mechanism_id": str(group.get("mechanism_id") or ""),
                "status": str(group.get("status") or ""),
                "member_count": int(group.get("member_count") or 0),
            }
            for group in groups
        ),
        key=lambda item: (item["mechanism_id"], item["group"]),
    )


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

def run_gate(
    runner: str,
    subject: MetamorphicSubject | None = None,
) -> dict[str, Any]:
    """Run the required classes against a subject and report the verdict.

    With no subject the registered reference subject for the runner is used,
    which admits the runner's *shape*. Pass a subject to admit a specific
    implementation (a learned executor, an inline program) -- the report then
    carries that subject's identity, which is what makes it attributable.
    """
    spec = runner_spec(runner)
    if subject is None:
        subject = reference_subject(runner)
    required = set(spec.metamorphic_classes) if spec is not None else set()
    if not required:
        required = set(METAMORPHIC_CLASSES)
    checks: list[dict[str, Any]] = []
    missing = sorted(required - set(subject.supported_classes())) if subject else sorted(required)
    for class_name in CLASS_ORDER:
        if class_name not in required:
            continue
        check = CHECKERS[class_name]
        if subject is None or class_name in missing:
            checks.append(_result(
                class_name, passed=False, mutation_effective=False,
                detail="the subject does not implement this class",
            ))
            continue
        try:
            checks.append(check(subject))
        except Exception as exc:  # noqa: BLE001 - a crashing subject is a failure
            checks.append(_result(
                class_name, passed=False, mutation_effective=False,
                detail=f"{type(exc).__name__}: {exc}",
            ))
    failed = [
        f"{item['class']}: {item['detail']}" for item in checks if not item["passed"]
    ]
    ineffective = [
        item["class"] for item in checks if not item["mutation_effective"]
    ]
    return {
        "schema_version": GATE_SCHEMA,
        "runner": str(runner),
        "subject": subject.name if subject is not None else "",
        "identity": dict(subject.identity) if subject is not None else {},
        "required_classes": sorted(required),
        "declared_classes": sorted(subject.supported_classes()) if subject else [],
        "checks": checks,
        "failed": failed,
        "mutation_ineffective": ineffective,
        "passed": bool(checks) and not failed,
        # §19.12: a new criterion must say what its verdict is worth. This one
        # admits or refuses a runner; it is not a score and not evidence of
        # detection quality.
        "authority": "admission-only",
    }


def reference_subject(runner: str) -> MetamorphicSubject | None:
    """The subject used to admit a runner's shape, or None when unregistered.

    A runner with no reference subject cannot be admitted, and the gate says
    so explicitly instead of passing it by default: the failure mode this
    prevents is a new executor arriving with no invariance contract at all and
    silently becoming the standard.

    Each entry is a factory rather than a shared instance: a subject holds the
    packets it was built with, and a caller that mutated one would otherwise
    change the fixture every later admission ran against.
    """
    factory = REFERENCE_SUBJECTS.get(str(runner))
    return factory() if factory is not None else None


def admission_error(runner: str) -> str:
    """The reason a runner is not admissible, or an empty string.

    This is the call the admission paths use, so they refuse on the same
    grounds the gate reports. A missing subject is a refusal rather than an
    exemption; the three runners registered when the gate landed all carry one.
    """
    name = str(runner or "")
    if not name:
        return ""
    spec = runner_spec(name)
    if spec is None:
        return f"runner {name!r} is not registered"
    if reference_subject(name) is None:
        return (
            f"runner {name!r} has no metamorphic admission subject; a new "
            "runner must ship with its §15.2 transformations before a "
            "candidate may name it"
        )
    return ""


def admitted_runners() -> list[str]:
    """Registered runners with an admission subject, sorted."""
    return sorted(
        name for name in RUNNER_REGISTRY if reference_subject(name) is not None
    )


def metamorphic_report() -> dict[str, Any]:
    """Run the gate for every registered runner, for an audit or a report.

    Executed rather than declared: the report contains the verdict of each
    class on each runner, so an audit that prints it has actually exercised the
    transformations. A registered runner with no subject appears as
    ``admitted: false`` with the reason, which is what makes the report usable
    as a gate rather than a catalogue.
    """
    rows: list[dict[str, Any]] = []
    for name in sorted(RUNNER_REGISTRY):
        report = run_gate(name)
        rows.append({
            "runner": name,
            "admitted": bool(report["passed"]),
            "required_classes": report["required_classes"],
            "failed": report["failed"],
            "authority": report["authority"],
        })
    return {
        "schema_version": GATE_SCHEMA,
        "classes": list(CLASS_ORDER),
        "runners": rows,
        "all_admitted": all(row["admitted"] for row in rows),
        "authority": "admission-only",
    }


# --------------------------------------------------------------------------
# Reference subjects for the registered runners
# --------------------------------------------------------------------------

def _reference_predicate_run(name: str) -> Any:
    def run(packet: Mapping[str, Any], context: Mapping[str, Any]) -> str:
        return str(run_predicate(name, packet, context).get("verdict") or "")

    return run


def _semantic_reference_judge() -> Any:
    """A judgment that compares meaning, not surface.

    It is a *reference* implementation of the typed_semantic contract: the
    verdict comes from a normalized comparison, so the surface rewrites of
    class 5 cannot move it. A real learned executor is admitted separately by
    running the gate with itself as the subject.

    Normalization is deliberately the same surface set ``semantic_equivalents``
    declares -- Unicode form, quote style, whitespace, sentence-final
    punctuation -- and nothing beyond it. Negation, numbers and values are
    untouched, so the judge is insensitive to exactly what it is declared to be
    insensitive to and to nothing more.
    """
    quotes = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
              "\u00ab": '"', "\u00bb": '"'}

    def normalize(text: str) -> str:
        value = unicodedata.normalize("NFC", str(text))
        for source, target in quotes.items():
            value = value.replace(source, target)
        value = " ".join(value.split()).strip()
        while value and value[-1] in {".", "\u3002"}:
            value = value[:-1].rstrip()
        return value

    def judge(expected: str, actual: str) -> str:
        left = normalize(expected)
        right = normalize(actual)
        if not left or not right:
            return "abstained"
        if left == right:
            return "supported"
        return "contradicted"

    return judge


def _semantic_reference_verdict(
    packet: Mapping[str, Any], context: Mapping[str, Any],
) -> str:
    """The packet-level verdict of the semantic reference subject.

    ``typed_semantic`` is an evidence_check runner, so the rule half of its
    admission has to produce a verdict from a packet too. It compares the
    judge-side delivered text with the mechanical anchor using the same
    normalized comparison as the semantic half, which is what makes the
    surface-token class meaningful for a learned runner: its verdict must not
    turn on quotation style.
    """
    judge = _semantic_reference_judge()
    delivered = str(context.get("judge_delivered_result") or "")
    if not delivered:
        delivered = str((context.get("delivered_result") or ""))
    if not delivered:
        return "abstained"
    anchors = " ".join(
        str(event.get("content") or "")
        for event in packet.get("events") or ()
    )
    return judge(delivered, anchors) if anchors.strip() else "abstained"


def _typed_dsl_program() -> dict[str, Any]:
    """A minimal typed-DSL program for the ``typed_dsl`` reference subject.

    One rule on a packet-level aggregate, so the program behaves like a real
    delivery probe while staying short enough to read. Its verdict vocabulary
    is the standard three-way one, and the conviction verdict is declared
    rather than assumed, because the validator requires an explicit choice.
    """
    return {
        "schema_version": "detector.predicate_program.v1",
        "name": "evo_reference_delivery_probe",
        "description": "Reference program for metamorphic admission of typed_dsl.",
        "verdicts": ["satisfied", "violated", "not_applicable"],
        "conviction_verdicts": ["violated"],
        "rules": [
            {
                "conditions": [
                    {
                        "field": "event_count",
                        "operator": "equals",
                        "value": 0,
                    }
                ],
                "verdict": "violated",
                "detail": "no events at all",
            }
        ],
        "default_verdict": "not_applicable",
        "default_detail": "no delivery probe matched",
    }


def _typed_dsl_run() -> Any:
    from detector.predicate_dsl import run_predicate_program

    program = _typed_dsl_program()

    def run(packet: Mapping[str, Any], context: Mapping[str, Any]) -> str:
        return str(run_predicate_program(program, packet, context).get("verdict") or "")

    return run


def _reference_packet_rule(*, name: str, run: Any, prose: str) -> PacketRuleSubject:
    return PacketRuleSubject(
        name=name,
        run=run,
        prose=prose,
        packets=[
            build_packet(normal_record()),
            build_packet(tool_call_record()),
        ],
        localization=_localization_case(),
    )


def _reference_component_rule(*, name: str, prose: str) -> PacketRuleSubject:
    """A packet rule whose verdicts come out of the Level B sandbox.

    The point of admitting ``sandboxed_component`` through the *sandbox* rather
    than a stand-in is that the class results then describe the path a real
    component takes: the packet views are resolved by the entry, coerced to the
    declared parameter types, run in a child under the limits, and mapped back
    onto the evidence vocabulary.
    """
    from evolution.sandbox import execute_component, reference_component

    component = reference_component(name=name)

    def run(packet: Mapping[str, Any], context: Mapping[str, Any]) -> str:
        from detector.interventions import _resolve_component_input

        arguments = {
            "expected": _resolve_component_input("last_prose_text", packet, context),
            "actual": _resolve_component_input("question_text", packet, context),
        }
        outcome = execute_component(component, arguments)
        if not outcome.get("ok"):
            return "abstained"
        return str(outcome.get("result") or "abstained")

    return PacketRuleSubject(
        name=name,
        run=run,
        prose=prose,
        packets=[
            build_packet(normal_record()),
            build_packet(tool_call_record()),
        ],
        localization=_localization_case(),
    )


#: The subject each registered runner is admitted on. Its presence *is* the
#: admission: a runner added to ``RUNNER_REGISTRY`` without an entry here is
#: refused by the gate, and the test suite fails, rather than passing by
#: default.
REFERENCE_SUBJECTS: dict[str, Any] = {
    "deterministic": lambda: CompositeSubject(
        name="reference_deterministic_checker",
        mechanism=MechanismSubject(),
        packet_rule=_reference_packet_rule(
            name="reference_deterministic_checker",
            run=_reference_predicate_run("verbatim_fidelity"),
            prose=(
                "Flag a delivery that omits a phrase the task required "
                "verbatim, and never apply when the delivered text carries "
                "the phrase."
            ),
        ),
    ),
    PROGRAM_RUNNER: lambda: CompositeSubject(
        name="reference_typed_dsl_program",
        mechanism=MechanismSubject(),
        packet_rule=_reference_packet_rule(
            name="reference_typed_dsl_program",
            run=_typed_dsl_run(),
            prose=(
                "Report when the trajectory delivers a summary that omits the "
                "required measurement phrase."
            ),
        ),
    ),
    "typed_semantic": lambda: CompositeSubject(
        name="reference_semantic_checker",
        mechanism=MechanismSubject(),
        packet_rule=_reference_packet_rule(
            name="reference_semantic_checker",
            run=_semantic_reference_verdict,
            prose=(
                "Judge whether the delivered text states the required "
                "measurement phrase."
            ),
        ),
        semantic=SemanticSubject(
            name="reference_semantic_checker",
            judge=_semantic_reference_judge(),
            pairs=[
                (
                    "The report states \u201cten degrees\u201d.",
                    'The report states "ten degrees".',
                ),
                ("Deliver the summary.", "Deliver the summary. "),
                (
                    "The sensor reading is 10 degrees.",
                    "The sensor reading is 10 degrees.",
                ),
            ],
        ),
    ),
    COMPONENT_RUNNER: lambda: CompositeSubject(
        name="reference_sandboxed_component",
        mechanism=MechanismSubject(),
        packet_rule=_reference_component_rule(
            name="evo_reference_component",
            prose=(
                "Compare the delivered sentence with the required one and "
                "report whether the trajectory delivered it."
            ),
        ),
    ),
}

__all__ = [
    "CASE_ID_RENAME",
    "CLASS_ORDER",
    "CompositeSubject",
    "FIRING_VERDICTS",
    "GATE_SCHEMA",
    "IRRELEVANT_TOOL_CALL",
    "LOCALIZATION_EVENT_MOVE",
    "MechanismSubject",
    "MetamorphicSubject",
    "NEIGHBOR_SURFACE_TOKEN",
    "PacketRuleSubject",
    "REFERENCE_SUBJECTS",
    "SEMANTIC_EQUIVALENCE",
    "SOURCE_RENAME",
    "SemanticSubject",
    "admission_error",
    "admitted_runners",
    "check_case_id_rename",
    "check_irrelevant_tool_call",
    "check_localization_event_move",
    "check_neighbor_surface_token",
    "check_semantic_equivalence",
    "check_source_rename",
    "inject_surface_tokens",
    "insert_irrelevant_tool_call",
    "metamorphic_report",
    "move_irrelevant_event",
    "normal_record",
    "reference_subject",
    "run_gate",
    "semantic_equivalents",
    "tool_call_record",
]
