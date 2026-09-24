"""Candidate proposer.

Drafting an intervention from a failure family is a generative step; a fixed
template library as the primary source would hard-code the experimenter's
hypotheses into the loop and silently cap what the framework can discover.
The LLM is therefore the primary proposal source. ``HybridProposer`` keeps a
deterministic typed-DSL template batch as a fallback, but only when the LLM
produces zero valid candidates in the same call; it never re-drafts after a
downstream gate (STRUCTURAL/HARMLESSNESS/BEHAVIOR/M) rejected an LLM
candidate — those rejections come back through ``set_rejection_feedback`` to
shape the next draft instead. Discipline stays mechanical: the draft must fit
the closed detector-side schema, must not echo an active intervention, and is
validated before it can spend gate budget. On any parse or provider error the
proposer returns zero candidates (the cycle is reported, not padded).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from detector.config import load_config as load_detector_config
from detector.interventions import (
    custom_conviction_verdicts_for,
    custom_fact_vocab_for,
    custom_predicate_verdicts_for,
    inline_predicate_programs,
    validate_intervention,
    validate_intervention_batch,
)
from detector.predicates import (
    PREDICATE_CONVICTION_VERDICTS,
    PREDICATE_VERDICTS,
)
from detector.provider import ProviderError, provider_call

from evolution.config import resolve_path
from evolution.contracts import redact_case_ids, structured_key
from evolution.mechanisms import mechanism_prompt_view


@dataclass
class ProposalBatch:
    candidates: list[dict[str, Any]] = field(default_factory=list)
    joint_groups: list[list[str]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    units: list[Any] = field(default_factory=list)

    def add_compiled_unit(self, unit: Any) -> None:
        """Register a CompiledUnit into the batch, maintaining candidate entries and joint groups."""
        self.units.append(unit)
        entry_ids = []
        for entry in getattr(unit, "entries", ()):
            self.candidates.append(dict(entry))
            entry_ids.append(str(entry.get("intervention_id") or ""))
        if len(entry_ids) > 1:
            self.joint_groups.append(entry_ids)



_LLM_DRAFT_SYSTEM_TEMPLATE = """\
Return ONLY one JSON object: {"interventions": [ ... ]}.
Do not explain your answer, use Markdown, case ids, labels, or case text.

Every intervention must use exactly ONE of these mutually exclusive schemas.

Text-boundary schema (changes one existing LLM stage prompt):
{"type":"text_boundary","hook":"judge.system|scanner.system|upgrade.system|tracer.system|defense.system","trigger":{"packet_features":["has_tool_calls"],"facts":[]},"body":{"directive":"nonempty behavior instruction","not_when":"nonempty hard exclusion"},"claim":{"expected_behavior":"nonempty","target_polarity":"recall|precision|boundary","target_kind":"detection|localization|joint"},"provenance":{"failure_family":"<one supplied family>"}}

Evidence schema (writes one mechanical evidence row):
{"type":"evidence_check","hook":"judge.post|upgrade.pre|defense.pre","trigger":{"packet_features":["has_tool_calls"],"facts":[]},"body":{"predicate":"<one predicate from the predicate list>","runner":"deterministic","output_verdicts":["<all verdicts listed for that predicate>"]},"claim":{"expected_behavior":"nonempty","target_polarity":"recall|precision|boundary","target_kind":"detection|localization|joint"},"provenance":{"failure_family":"<one supplied family>"}}

Evolved-evidence schema (defines one NEW typed predicate program and writes its row):
{"type":"evidence_check","hook":"judge.post|upgrade.pre|defense.pre","trigger":{"packet_features":["has_tool_calls"],"facts":[]},"body":{"predicate":"evo_<short_name>","runner":"typed_dsl","output_verdicts":["satisfied","violated","not_applicable"],"predicate_definition":{"schema_version":"detector.predicate_program.v1","name":"evo_<same_short_name>","description":"what this observation detects","verdicts":["satisfied","violated","not_applicable"],"conviction_verdicts":["violated"],"default_verdict":"not_applicable","rules":[{"field":"<one typed field below>","operator":"<valid operator for that field>","value":"<literal>","verdict":"violated","detail":"what the rule found"}]}},"claim":{"expected_behavior":"nonempty","target_polarity":"recall|precision|boundary","target_kind":"detection|localization|joint"},"provenance":{"failure_family":"<one supplied family>"}}
A rule may instead use "conditions":[{"field":...,"operator":...,"value":...}, ...] (2..4 conditions, ALL must match, AND semantics). Never put field/operator/value alongside conditions.

Recall-trigger schema (forces the recall net for matching judge-negative cases):
{"type":"recall_trigger","hook":"recall.trigger","trigger":{"packet_features":["has_tool_calls"],"facts":["judge_positive=false"]},"body":{"predicate":"evo_<short_name>","runner":"typed_dsl","output_verdicts":["satisfied","violated","not_applicable"],"trigger_verdicts":["violated"],"require_judge_negative":true,"predicate_definition":{"schema_version":"detector.predicate_program.v1","name":"evo_<same_short_name>","description":"when the recall net must hear this case","verdicts":["satisfied","violated","not_applicable"],"conviction_verdicts":["violated"],"default_verdict":"not_applicable","rules":[{"field":"<one typed field below>","operator":"<valid operator for that field>","value":"<literal>","verdict":"violated","detail":"what the rule found"}]}},"claim":{"expected_behavior":"nonempty","target_polarity":"recall","target_kind":"detection"},"provenance":{"failure_family":"<one supplied family>"}}
Recall-trigger is the correct route for an FN family whose recall_path is not_run or not_needed: it spends the LLM recall net only on cases the trigger names. A recall_trigger never turns a judge-positive case and never changes the verdict by itself; a recall upgrade still needs defense substantiation.
This route is available, not mandatory. Re-reading the same packet cannot
supply an absent tool specification, hidden benchmark requirement, or external
reference fact. Return an empty interventions list when no grounded treatment
is supported; do not manufacture a trigger just to fill the proposal budget.

Judge output contract (field semantics, not failure evidence):
When is_hallucination=false, hallucination_reason, hallucination_step,
evidence_refs and root_cause MUST be null. Their absence is normal and does
not mean the negative decision is unreasoned or missing a justification.
When is_hallucination=true, a nonempty hallucination_reason states the alleged
error; its existence is not self-refutation. High confidence alone is not
a failure mechanism. Distinguish these schema conventions from actual
packet evidence of a violated task requirement. Shared fields across failure
members do not establish that the same fields distinguish clean controls.

Semantic-evidence schema (asks an injected learned executor for one typed judgment):
{"type":"evidence_check","hook":"judge.post|upgrade.pre|defense.pre","trigger":{"packet_features":["has_tool_calls"],"facts":[]},"body":{"predicate":"<a task schema name, not a built-in predicate>","runner":"typed_semantic","output_verdicts":["supported","contradicted","abstained"]},"artifact":{"id":"<executor id>","digest":"<executor artifact digest>"},"claim":{"expected_behavior":"nonempty","target_polarity":"recall|precision|boundary","target_kind":"detection|localization|joint"},"provenance":{"failure_family":"<one supplied family>"}}
Use this ONLY when the judgement needs a semantic comparison the typed operator set cannot express (whether two values mean the same thing) and an executor is available. Three hard boundaries: the artifact{id,digest} is mandatory and must match the executor that actually runs, so never invent a digest; the verdict vocabulary is exactly supported/contradicted/abstained, where abstained means the executor could not tell; and this runner is evidence-only -- it can never convict, and it may not drive recall.trigger. A deterministic route that can express the same comparison is always preferable.

Guard schema (emits one closed adjudication action):
{"type":"decision_guard","hook":"adjudicate.guard","trigger":{"packet_features":["has_tool_calls"],"facts":["name=value from the fact list"]},"body":{"action":"withhold_conviction|restore_judge_conviction|require_full_view_defense|convict_mechanical_violation","evidence_predicate":"<required only for convict_mechanical_violation>","action_not_when":"nonempty explanation","action_not_when_facts":["name=value from the fact list"]},"claim":{"expected_behavior":"nonempty","target_polarity":"recall|precision|boundary","target_kind":"detection|localization|joint"},"provenance":{"failure_family":"<one supplied family>"}}

Predicates and their verdicts:
{predicate_block}

Conviction-authorized predicate verdicts:
{conviction_block}

Typed predicate-program fields:
question_text, last_prose_text, last_answer_tool_name,
last_answer_tool_arguments, last_answer_tool_observation, event_texts,
prose_texts, tool_names, tool_arguments, tool_observations, interaction_texts,
event_ids, interaction_ids, packet_features, context_evidence_refs,
context_contract_kinds, context_contract_requirements, context_quote,
context_judge_delivered_result, context_judge_positive,
context_judge_confidence, context_judge_hallucination_reason, event_count,
tool_call_count, interaction_count, prose_event_count.
Context fields are hook-specific: judge.post and recall.trigger support all
listed context fields; defense.pre supports only context_evidence_refs,
context_contract_kinds, context_contract_requirements and context_quote;
upgrade.pre supports no context_* fields. Packet fields remain available.
Scalar operators: equals, not_equals, contains, not_contains, starts_with,
not_starts_with, ends_with, not_ends_with, matches, not_matches, exists,
missing. List operators: any_contains, all_contain, none_contains,
any_equals, all_equal, none_equals, exists, missing. Count fields use
equals, not_equals, greater_than, greater_than_or_equal, less_than,
less_than_or_equal. ``exists`` and ``missing`` never consume a value: omit
the "value" key entirely for those two operators (a rule with a value is
rejected as invalid_contract). Rules match in order; the first matching
rule emits its verdict, otherwise default_verdict is emitted. Prefer the
narrowest typed field that captures the shared mechanism of every supplied
member.
Use "conditions" (AND) when one mechanism needs two typed observations, for
example question_text contains a required tool AND tool_names none_contains
that tool. Separate rules are OR alternatives; order safer rules first.
Every member_summary carries typed_samples (the exact truncated values the
typed fields see) and the family carries shared_literals (tokens/list items
present in every member). Copy literal spellings from those blocks; do not
invent a phrase that is not supported by every member sample.

Stage facts (guard triggers only):
judge_confidence=high|medium|low; judge_positive=true|false;
positive_source=judge|recall_upgrade|none;
defense_verdict=substantiated|refuted|inconclusive|none;
defense_view_degraded=true|false; upgrade_degraded=true|false;
recall_upgrade=present|none.
A predicate name itself is also a fact once its evidence row ran, with the
verdict that predicate produced (for example required_action_present=violated).

Guidance:
- Each family lists its open treatment_routes; draft only within them. A
  route absent from the list is structurally closed (its contract can never
  validate), so drafting it wastes the cycle budget.
- When several stable families are supplied, cover different families with
  different candidates instead of spending every slot on one family. If an
  FN family with recall_path not_run/not_needed has packet-grounded evidence
  supporting a treatment, consider one recall_trigger for it; if an FP family is present, draft at most one
  evidence+guard unit for it. A HARMLESSNESS rejection with new_fn/new_fp means that
  exact executable mechanism is forbidden, including renames: choose a
  different predicate/action or a different family.
- recall_directive maps to scanner.system; upgrade_directive maps to
  upgrade.system. recall_trigger maps to recall.trigger and is the preferred
  route when recall_path is not_run or not_needed. A text boundary must
  include both a positive directive and an explicit NOT-WHEN boundary
  grounded in the shared mechanism summary.
- For an FN family (detector said clean but gold says error), first determine
  that one mechanism is shared by the supplied member summaries. An
  evidence_check may be paired with a convict_mechanical_violation guard only
  when the mechanical_conviction route is open AND the evidence predicate is
  applicable and violating for every member;
  its trigger must also require judge_positive=false and positive_source=none,
  so it cannot override a positive hearing or defense. An evidence_check never
  changes a terminal verdict by itself: emit it only with a same-family guard
  that consumes its predicate fact; otherwise emit no candidate.
- For an FN family where a positive existed but was lost: restore_judge_conviction
  with the facts that identify the over-refutation.
- For an FP family: first choose the mechanical predicate whose
  predicate_signals show unanimous violation across the family (for example
  delivered_anchor_consistency). Then draft BOTH a same-family evidence_check
  for that predicate AND a withhold_conviction guard whose trigger facts
  bind that predicate plus judge_positive=true and positive_source=judge.
  Stage facts alone never need evidence; a predicate-named fact without its
  evidence partner can never fire and is rejected before the gate. Do not
  emit a guard-only candidate when the only discriminative signal is a
  predicate fact.
- Every claim must carry BOTH target fields, and they must agree with the
  family you drew it from. Each family in the block states its kind and its
  required_target_kind; copy that value.
  target_polarity: recall for FN, precision for FP. A LOCALIZATION family is
  neither: it is a case the detector already convicts correctly but whose
  named responsible step is wrong, so its claim treats the step, not the
  binary verdict.
  target_kind: detection for FN and FP, localization or joint for
  LOCALIZATION. A claim on a LOCALIZATION family that declares detection is
  refused by STRUCTURAL before any objective-specific gate can score it, so
  the intervention is never measured. When the treatment must both convict and
  name the right step, declare joint.
- A LOCALIZATION family's only route is the tracer (tracer.system text
  boundary): the conviction is already correct, so the stage that must move is
  the one that names the step. Do not force recall or add a conviction for
  such a family.
- Every guard needs action_not_when_facts. They are ANDed executable exclusions;
  use [] only when no exclusion is required.
 - Never re-propose a mechanism equivalent to an active intervention or a
   rejected candidate listed in the feedback.
"""


# The objective a family's mechanism belongs to, and the target_kind a claim
# drawn from it must declare. STRUCTURAL enforces this agreement, so the
# proposer has to be told it: a localization family treated as a detection
# family is refused before any objective-specific gate can score it.
FAMILY_KINDS = {
    "FAM-fn-": ("FN", "detection"),
    "FAM-fp-": ("FP", "detection"),
    "FAM-loc-": ("LOCALIZATION", "localization"),
    "FAM-unresolved-": ("UNRESOLVED", "detection"),
}


def _family_kind(family_id: Any) -> str:
    """Readable kind for one family id; never guesses between objective kinds.

    A localization family is *not* an FP family. The earlier ``"FN" if
    startswith("FAM-fn") else "FP"`` collapsed every non-FN family into FP,
    which told the model to answer with precision polarity for a localization
    family — and since every schema then omitted ``target_kind``, the claim
    defaulted to detection and STRUCTURAL rejected it. Two of the three
    objectives were therefore unmeasurable by construction.
    """
    text = str(family_id or "")
    for prefix, (kind, _target) in FAMILY_KINDS.items():
        if text.startswith(prefix):
            return kind
    return "UNKNOWN"


def family_target_kind(family_id: Any) -> str:
    """The ``target_kind`` a claim drawn from this family must declare."""
    text = str(family_id or "")
    for prefix, (_kind, target) in FAMILY_KINDS.items():
        if text.startswith(prefix):
            return target
    return "detection"


def _draft_system_prompt() -> str:
    predicate_block = "\n".join(
        f"{name}: {'|'.join(sorted(verdicts))}"
        for name, verdicts in sorted(PREDICATE_VERDICTS.items())
    )
    conviction_block = "\n".join(
        f"{name}: {'|'.join(sorted(verdicts))}"
        for name, verdicts in sorted(PREDICATE_CONVICTION_VERDICTS.items())
    )
    return (
        _LLM_DRAFT_SYSTEM_TEMPLATE
        .replace("{predicate_block}", predicate_block)
        .replace("{conviction_block}", conviction_block)
    )


class LLMProposer:
    name = "llm"

    def __init__(
        self,
        framework_config: Mapping[str, Any],
        *,
        active_entries: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        detector_config = load_detector_config(
            resolve_path(framework_config.get("detector_config"))
        )
        self.route = dict(detector_config["judge_route"])
        max_output_tokens = int(
            (framework_config.get("proposer") or {}).get(
                "max_output_tokens", 32768
            )
        )
        if max_output_tokens > 0:
            # Candidate drafting needs a compact JSON array. The ceiling must
            # clear the reasoning budget GLM-5.3-flash spends before the JSON
            # (measured 6.2k-12.5k completion tokens, 85-92% of it reasoning);
            # 4096 truncated before the array and yielded zero candidates.
            # This limit applies only to the proposer route, never to detector
            # stages or prior baselines.
            self.route["max_tokens"] = max_output_tokens
        thinking_level = str(
            (framework_config.get("proposer") or {}).get(
                "thinking_level", ""
            )
        ).strip()
        if thinking_level:
            # Off by default: GLM-5.3-flash always reasons and the endpoint
            # rejects any explicit `thinking` value on a large fraction of
            # replicas with a 400. Sending none is the only reliable request.
            # The option stays for providers that do accept a level.
            self.route["extra_body"] = {
                "thinking": {"type": thinking_level}
            }
        self.route["_route_name"] = "evolution_proposer"
        self.max_calls = max(
            0,
            int((framework_config.get("budget") or {}).get("llm_calls", 1)),
        )
        # The LLM budget is per cycle: the protocol caps failure-family
        # diagnosis plus candidate drafting at N calls per cycle, so a long
        # experiment must never exhaust its drafting ability mid-run.
        self.calls_by_cycle: dict[int, int] = {}
        self.repairs_by_cycle: dict[int, int] = {}
        self.max_repair_calls = max(
            0,
            int((framework_config.get("proposer") or {}).get(
                "max_repair_calls", 1,
            )),
        )
        self.active_entries = [dict(entry) for entry in active_entries]
        self.rejection_feedback: list[dict[str, Any]] = []
        self.rejected_keys: set[tuple[Any, ...]] = set()
        self.rejected_group_keys: set[frozenset[tuple[Any, ...]]] = set()
        self.memory_context: dict[str, Any] = {}
        # Bounded research-packet supply (research entry only, doc §8.2):
        # full training trajectories, per-stage baseline results and the
        # human-reviewed target-error description. Absent for every caller
        # but the research entry, in which case drafting is unchanged.
        self.research_packet: dict[str, Any] = {}
        # Reusable parts from the component store (§9.1 layer 2). Bounded and
        # retrieved by mechanism/capability/kind. A component is a validated
        # typed-DSL program that was useful somewhere, not a correct answer
        # anywhere: it is offered so a draft can reuse a working observation
        # instead of re-deriving it, and it changes no gate.
        self.components: list[dict[str, Any]] = []

    def set_memory_context(
        self, context: Mapping[str, Any] | None,
    ) -> None:
        """Attach retrieved H_t memory as drafting input.

        This is prior experience, not a constraint: an admitted procedure is a
        known-good route for a mechanism, and an open capability gap explains
        why some families are absent from the actionable list. Neither is
        scored, and supplying them changes no gate.
        """
        self.memory_context = dict(context or {})

    def set_components(
        self, components: Sequence[Mapping[str, Any]] | None,
    ) -> None:
        """Attach reusable parts as drafting input.

        Same discipline as ``set_memory_context``: context, not constraint.
        Only the prompt view is kept -- a component's provenance (which
        mechanism first needed it, which candidate produced it) is not drafting
        material and is deliberately not carried into the prompt.
        """
        from evolution.archive import component_prompt_view

        # A malformed entry is dropped rather than crashing the draft: this is
        # an optional drafting aid, and losing one part must not cost the whole
        # proposal round.
        self.components = [
            component_prompt_view(record)
            for record in (components or ())
            if isinstance(record, Mapping)
        ]

    def set_research_packet(
        self, packet: Mapping[str, Any] | None,
    ) -> None:
        """Attach the bounded research packet as drafting context.

        The packet carries what the truncated field profiles cannot: the
        complete post-``project_record`` question/history of the training
        cases, per-stage baseline results, and the human-reviewed target
        error description. It is drafting input only — it never reaches the
        detector, and the candidate it produces is checked for case-id
        leakage downstream.
        """
        self.research_packet = dict(packet or {})

    def set_rejection_feedback(
        self, feedback: Sequence[Mapping[str, Any]],
    ) -> None:
        self.rejection_feedback = [dict(item) for item in feedback]
        self.rejected_keys = {
            key for key in (
                _proposal_fingerprint(item.get("candidate") or {})
                for item in self.rejection_feedback if not item.get("not_evaluable")
            ) if key is not None
        }
        # Same not_evaluable filter as single candidates: missing evaluation
        # coverage is not a falsified mechanism, so an unevaluable group must
        # stay visible as diagnostic feedback without permanently banning the
        # combination from a later retry.
        self.rejected_group_keys = {
            frozenset(keys)
            for keys in (
                [
                    key for key in (
                        _proposal_fingerprint(candidate)
                        for candidate in item.get("candidates") or ()
                    ) if key is not None
                ]
                for item in self.rejection_feedback
                if not item.get("not_evaluable")
            )
            if len(keys) >= 2
        }

    def _registry_digest(self) -> list[dict[str, Any]]:
        digest = []
        for entry in self.active_entries:
            row: dict[str, Any] = {
                "id": str(entry.get("intervention_id") or ""),
                "type": str(entry.get("type") or ""),
                "hook": str(entry.get("hook") or ""),
            }
            body = entry.get("body") or {}
            if row["type"] == "evidence_check":
                row["predicate"] = str(body.get("predicate") or "")
                row["runner"] = str(body.get("runner") or "")
                if row["runner"] == "typed_dsl":
                    from detector.predicate_dsl import (
                        predicate_program_hash,
                        validate_predicate_program,
                    )

                    raw_program = body.get("predicate_definition") or {}
                    try:
                        program = validate_predicate_program(raw_program)
                        row["program_hash"] = predicate_program_hash(program)
                        row["description"] = str(
                            program.get("description") or ""
                        )[:160]
                    except ValueError:
                        row["program_hash"] = ""
            elif row["type"] == "recall_trigger":
                row["predicate"] = str(body.get("predicate") or "")
                row["runner"] = str(body.get("runner") or "")
                row["trigger_verdicts"] = [
                    str(item) for item in body.get("trigger_verdicts") or ()
                ]
                if row["runner"] == "typed_dsl":
                    from detector.predicate_dsl import (
                        predicate_program_hash,
                        validate_predicate_program,
                    )

                    raw_program = body.get("predicate_definition") or {}
                    try:
                        program = validate_predicate_program(raw_program)
                        row["program_hash"] = predicate_program_hash(program)
                        row["description"] = str(
                            program.get("description") or ""
                        )[:160]
                    except ValueError:
                        row["program_hash"] = ""
            elif row["type"] == "decision_guard":
                row["action"] = str(body.get("action") or "")
                row["facts"] = [
                    str(item) for item in (entry.get("trigger") or {}).get(
                        "facts"
                    ) or ()
                ]
            elif row["type"] == "text_boundary":
                row["directive"] = str(body.get("directive") or "")[:160]
            digest.append(row)
        return digest

    def propose(
        self,
        families: Sequence[Mapping[str, Any]],
        *,
        cycle: int,
        max_proposals: int = 3,
    ) -> ProposalBatch:
        if not families:
            return ProposalBatch()
        calls_used = self.calls_by_cycle.get(int(cycle), 0)
        if calls_used >= self.max_calls:
            return ProposalBatch(notes=[{
                "generator": "llm",
                "provider_called": False,
                "reason": "llm_calls budget exhausted for this cycle",
            }])
        family_cases = {
            str(family.get("failure_family") or ""): [
                str(case) for case in (family.get("failure_cases") or ())
            ]
            for family in families
            if str(family.get("failure_family") or "")
            and len(family.get("failure_cases") or ()) >= 2
        }
        if not family_cases:
            return ProposalBatch(notes=[{
                "generator": "llm",
                "provider_called": False,
                "reason": "no stable family with locally bindable provenance",
            }])
        family_block = json.dumps(
            [
                {
                    "failure_family": f.get("failure_family"),
                    "kind": _family_kind(f.get("failure_family")),
                    "required_target_kind": family_target_kind(
                        f.get("failure_family")
                    ),
                    "count": f.get("count"),
                    "sources": f.get("sources"),
                    "mechanism_summary": f.get("mechanism_summary"),
                    "mechanism_signature": f.get("mechanism_signature"),
                    # The task-level reading of the family: obligation,
                    # deviation, failing stage and the repair operation it
                    # implies. Present when the cycle derived mechanisms; the
                    # path signature above stays as the fallback reading.
                    "mechanism": mechanism_prompt_view(f.get("mechanism")),
                    "treatment_routes": (f.get("actionability") or {}).get(
                        "routes", []
                    ),
                    "predicate_signals": (f.get("diagnostics") or {}).get(
                        "predicate_signals", {}
                    ),
                    "shared_literals": (f.get("diagnostics") or {}).get(
                        "shared_literals", {}
                    ),
                    "member_summaries": (f.get("diagnostics") or {}).get(
                        "member_summaries", []
                    ),
                }
                for f in families[:5]
                if str(f.get("failure_family") or "") in family_cases
            ],
            ensure_ascii=False,
        )
        active_block = json.dumps(self._registry_digest(), ensure_ascii=False)
        memory_block = json.dumps(self.memory_context, ensure_ascii=False)
        components_block = json.dumps(self.components, ensure_ascii=False)
        research_block = json.dumps(
            self.research_packet, ensure_ascii=False,
        ) if self.research_packet else ""
        feedback_block = json.dumps(
            [
                {
                    "type": item.get("type"),
                    "hook": item.get("hook"),
                    "predicate": item.get("predicate"),
                    "action": item.get("action"),
                    "facts": item.get("facts"),
                    "gate": item.get("gate"),
                    "reason": redact_case_ids(item.get("reason")),
                    "delta": item.get("delta"),
                    "gain_opportunities": item.get("gain_opportunities"),
                    "behavior_delta": item.get("behavior_delta"),
                    "behavior_note": item.get("behavior_note"),
                    "not_evaluable": item.get("not_evaluable", False),
                    "combination": [
                        {
                            "type": candidate.get("type"),
                            "hook": candidate.get("hook"),
                            "predicate": (candidate.get("body") or {}).get(
                                "predicate"
                            ),
                            "action": (candidate.get("body") or {}).get("action"),
                        }
                        for candidate in item.get("candidates") or ()
                    ],
                }
                for item in self.rejection_feedback[-8:]
            ],
            ensure_ascii=False,
        )
        user = (
            f"Cycle {cycle}. Active interventions already deployed (do not "
            f"echo their mechanisms):\n{active_block}\n\n"
            f"Prior gate feedback: do not repeat falsified mechanisms unchanged. "
            f"not_evaluable means missing evaluation coverage, not a falsified mechanism:\n"
            f"{feedback_block}\n\n"
            f"Retrieved prior experience: admitted procedures describe routes "
            f"that already worked on a mechanism, and open capability gaps "
            f"explain families no current route can treat. Reuse a procedure "
            f"when the mechanism matches; do not re-derive it from scratch:\n"
            f"{memory_block}\n\n"
            + (
                f"Reusable validated components (already registered, not "
                f"deployed): each entry is a complete typed-DSL program that "
                f"was useful on some mechanism. A component may already fit "
                f"this family -- check before writing a new program, and reuse "
                f"its predicate_definition verbatim when it does. To use one, "
                f"carry it as the body.predicate_definition of an evidence_check "
                f"or recall_trigger, keeping its name and rules exactly as "
                f"given; a modified program is a new component, not a reuse:\n"
                f"{components_block}\n\n"
                if self.components else ""
            )
            + (
                f"Research packet (bounded, read-only): complete training-case "
                f"trajectories after label projection, per-stage baseline "
                f"results, and the reviewed target-error description. Use the "
                f"concrete evidence to locate which claim loses which fact at "
                f"which stage; the drafted intervention must describe a "
                f"transferable verification operation, never per-case "
                f"constants or case identifiers:\n{research_block}\n\n"
                if research_block else ""
            )
            + f"Stable bare-base failure families:\n{family_block}\n\n"
            f"Draft at most {max_proposals} interventions."
        )
        try:
            self.calls_by_cycle[int(cycle)] = calls_used + 1
            parsed, meta = provider_call(
                route=self.route,
                system=_draft_system_prompt(),
                user=user,
                purpose="evolution_proposer",
            )
        except ProviderError as exc:
            # ``rejected`` puts a retryable transport failure into the same
            # vocabulary the repair loop already scans, so the configured
            # repair call is actually available to a truncated reply. Without
            # it the note carried no ``rejected`` key, ``mechanical_rejections``
            # stayed empty, and a stochastic truncation silently downgraded the
            # cycle to the deterministic templates.
            #
            # A missing credential is deliberately excluded: re-asking cannot
            # mint one, so a repair call would only spend budget.
            retryable = str(exc.error_type or "") != "missing_credential"
            return ProposalBatch(notes=[{
                "generator": "llm",
                "provider_called": True,
                "provider_ok": False,
                "error_type": exc.error_type,
                "attempts": exc.attempts,
                **({"rejected": "provider_failure"} if retryable else {}),
                "reason": str(exc.message or ""),
            }])
        generation_note = {
            "generator": "llm",
            "provider_called": True,
            "provider_ok": True,
            "purpose": str(meta.get("purpose") or "evolution_proposer"),
            "model": str(meta.get("model") or ""),
            "attempts": int(meta.get("attempts") or 0),
            "usage": dict(meta.get("usage") or {}),
        }
        if isinstance(parsed, list):
            raw: Any = parsed
        elif isinstance(parsed, Mapping):
            raw = parsed.get("interventions")
        else:
            return ProposalBatch(notes=[{
                **generation_note,
                "reason": "provider response has no interventions list",
            }])
        if not isinstance(raw, list):
            return ProposalBatch(notes=[{
                **generation_note,
                "reason": "provider interventions field is not a list",
            }])

        active_keys = {
            key for key in (
                structured_key(entry) for entry in self.active_entries
            ) if key is not None
        }
        raw_proposals: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = [generation_note]
        for index, item in enumerate(raw[: max_proposals], start=1):
            if not isinstance(item, Mapping):
                continue
            candidate = dict(item)
            candidate.setdefault("schema_version", "detector.intervention.v1")
            candidate["intervention_id"] = f"IV-{cycle}-{index:03d}"
            raw_provenance = candidate.get("provenance") or {}
            family = str(raw_provenance.get("failure_family") or "")
            if family not in family_cases:
                notes.append({
                    "intervention_id": candidate["intervention_id"],
                    "generator": "llm",
                    "rejected": "unknown_failure_family",
                    "reason": f"failure_family {family!r} not in supplied families",
                })
                continue
            candidate["provenance"] = {
                "cycle": int(cycle),
                "failure_family": family,
                "failure_cases": list(family_cases[family]),
            }
            raw_proposals.append(candidate)

        # Capability-binding validation is a batch property: a guard may only
        # consume a custom predicate fact when the same batch defines that
        # predicate program. A malformed/conflicting capability is reported
        # instead of crashing the cycle (fail-safe proposal parsing). The
        # per-candidate pass still runs so each malformed item keeps its own
        # rejection note for the repair loop.
        try:
            validate_intervention_batch(
                raw_proposals, require_executable_bounds=True,
            )
        except ValueError as exc:
            notes.append({
                "generator": "llm",
                "rejected": "invalid_contract",
                "reason": f"batch capability contract: {exc}",
            })
        try:
            batch_programs = inline_predicate_programs(raw_proposals)
        except ValueError:
            batch_programs = {}
        batch_fact_vocab = custom_fact_vocab_for(batch_programs)
        batch_predicate_verdicts = custom_predicate_verdicts_for(batch_programs)
        batch_conviction_verdicts = custom_conviction_verdicts_for(batch_programs)
        proposals: list[dict[str, Any]] = []
        batch_keys: set[tuple[Any, ...]] = set()
        for candidate in raw_proposals:
            intervention_id = str(candidate.get("intervention_id") or "")
            try:
                validate_intervention(
                    candidate,
                    require_executable_bounds=True,
                    custom_fact_vocab=batch_fact_vocab,
                    custom_predicate_verdicts=batch_predicate_verdicts,
                    custom_conviction_verdicts=batch_conviction_verdicts,
                )
            except ValueError as exc:
                notes.append({
                    "intervention_id": intervention_id,
                    "generator": "llm",
                    "rejected": "invalid_contract",
                    "reason": str(exc),
                })
                continue
            key = structured_key(candidate)
            proposal_fingerprint = _proposal_fingerprint(candidate)
            if key is not None:
                if key in active_keys:
                    notes.append({
                        "intervention_id": intervention_id,
                        "generator": "llm",
                        "rejected": "echo_of_active",
                        "reason": f"structured key {key} already deployed",
                    })
                    continue
                if key in batch_keys:
                    notes.append({
                        "intervention_id": intervention_id,
                        "generator": "llm",
                        "rejected": "duplicate_in_batch",
                        "reason": f"structured key {key} drafted twice",
                    })
                    continue
                batch_keys.add(key)
            if proposal_fingerprint in self.rejected_keys:
                notes.append({
                    "intervention_id": intervention_id,
                    "generator": "llm",
                    "rejected": "echo_of_rejected",
                    "reason": "executable mechanism previously failed a gate",
                })
                continue
            proposals.append(candidate)
            notes.append({
                "intervention_id": intervention_id,
                "family": str(
                    (candidate.get("provenance") or {}).get("failure_family") or ""
                ),
                "generator": "llm",
                "frozen_judge_replay_compatible": str(
                    candidate.get("hook") or ""
                ) != "judge.system",
            })
        joint_groups = infer_joint_groups(proposals)
        grouped_ids = {item for group in joint_groups for item in group}
        evidence_sources = {
            (
                str((candidate.get("provenance") or {}).get("failure_family") or ""),
                str((candidate.get("body") or {}).get("predicate") or ""),
            )
            for candidate in proposals
            if candidate.get("type") == "evidence_check"
        }
        predicate_fact_names = set(PREDICATE_VERDICTS) | set(batch_programs)
        filtered: list[dict[str, Any]] = []
        for candidate in proposals:
            if candidate.get("type") != "decision_guard":
                filtered.append(candidate)
                continue
            body = candidate.get("body") or {}
            trigger_facts = list((candidate.get("trigger") or {}).get("facts") or ())
            consumed = {
                str(fact).split("=", 1)[0]
                for fact in [*trigger_facts, *(body.get("action_not_when_facts") or ())]
                if "=" in str(fact)
            }
            family = str((candidate.get("provenance") or {}).get("failure_family") or "")
            missing = sorted(
                name for name in consumed
                if name in predicate_fact_names and (family, name) not in evidence_sources
            )
            if missing:
                notes.append({
                    "intervention_id": candidate["intervention_id"],
                    "generator": "llm",
                    "rejected": "missing_evidence_partner",
                    "reason": (
                        f"predicate fact(s) {missing} require a same-family "
                        "evidence_check in the same batch"
                    ),
                })
                continue
            filtered.append(candidate)
        kept: list[dict[str, Any]] = []
        for candidate in filtered:
            if (
                candidate.get("type") == "decision_guard"
                and (candidate.get("body") or {}).get("action")
                == "convict_mechanical_violation"
                and str(candidate.get("intervention_id") or "") not in grouped_ids
            ):
                notes.append({
                    "intervention_id": candidate["intervention_id"],
                    "generator": "llm",
                    "rejected": "missing_evidence_partner",
                    "reason": "mechanical conviction requires a paired evidence_check",
                })
                continue
            kept.append(candidate)
        filtered = kept
        filtered = _drop_orphan_evidence(filtered, notes)
        filtered_keys = {
            key: candidate
            for candidate in filtered
            if (key := _proposal_fingerprint(candidate)) is not None
        }
        for group in self.rejected_group_keys:
            if not group <= set(filtered_keys):
                continue
            rejected_key = next(
                key for key in reversed(list(filtered_keys)) if key in group
            )
            rejected = filtered_keys[rejected_key]
            filtered.remove(rejected)
            notes.append({
                "intervention_id": rejected["intervention_id"],
                "generator": "llm",
                "rejected": "echo_of_rejected_combination",
                "reason": "candidate recreates a combination that failed M",
            })
            break
        batch = ProposalBatch(
            candidates=filtered,
            joint_groups=infer_joint_groups(filtered),
            notes=notes,
        )
        mechanical_rejections = [
            note for note in notes
            if note.get("rejected") in {
                "unknown_failure_family", "invalid_contract",
                "echo_of_active", "duplicate_in_batch", "echo_of_rejected",
                "missing_evidence_partner", "missing_terminal_partner",
                "echo_of_rejected_combination",
                # A provider failure is not a contract rejection, but it does
                # mean no candidate was drafted. The repair call re-asks with
                # the failure in context, which is the only configured remedy
                # for a stochastic truncation.
                "provider_failure",
            }
        ]
        repairs = self.repairs_by_cycle.get(int(cycle), 0)
        if (
            not batch.candidates
            and mechanical_rejections
            and repairs < self.max_repair_calls
            and self.calls_by_cycle.get(int(cycle), 0) < self.max_calls
        ):
            self.repairs_by_cycle[int(cycle)] = repairs + 1
            previous_feedback = list(self.rejection_feedback)
            self.rejection_feedback.extend({
                "gate": "LOCAL_CONTRACT",
                "reason": redact_case_ids(note.get("reason")),
                "type": None,
                "hook": None,
                "delta": None,
            } for note in mechanical_rejections)
            try:
                repaired = self.propose(
                    families,
                    cycle=cycle,
                    max_proposals=max_proposals,
                )
            finally:
                self.rejection_feedback = previous_feedback
            repaired_notes = []
            for note in repaired.notes:
                item = dict(note)
                if item.get("provider_called"):
                    item["repair"] = True
                repaired_notes.append(item)
            return ProposalBatch(
                candidates=repaired.candidates,
                joint_groups=repaired.joint_groups,
                notes=[*notes, *repaired_notes],
            )
        return batch


class OfflineProposer:
    """Explicit no-provider proposer for mock mechanics and CI runs."""

    name = "offline"

    def propose(
        self,
        families: Sequence[Mapping[str, Any]],
        *,
        cycle: int,
        max_proposals: int = 3,
    ) -> ProposalBatch:
        return ProposalBatch(notes=[{
            "generator": "offline",
            "provider_called": False,
            "reason": "offline proposer does not draft interventions",
        }])


def infer_joint_groups(proposals: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    """Pair each guard with the evidence row that produces its predicate fact.

    A joint L1+L2 admission is only principled when the guard's trigger
    consumes a mechanical fact the evidence candidate actually writes in the
    same cycle and for the same failure family; otherwise the pair has no
    causal dependency and must stand or fall individually.
    """
    evidence_by_predicate: dict[tuple[str, str], Mapping[str, Any]] = {}
    for candidate in proposals:
        if candidate.get("type") == "evidence_check":
            predicate = str((candidate.get("body") or {}).get("predicate") or "")
            family = str(
                (candidate.get("provenance") or {}).get("failure_family") or ""
            )
            evidence_by_predicate.setdefault((family, predicate), candidate)
    guards_by_evidence: dict[str, list[str]] = {}
    for candidate in proposals:
        if candidate.get("type") != "decision_guard":
            continue
        fact_names = {
            str(fact).split("=", 1)[0]
            for fact in (candidate.get("trigger") or {}).get("facts") or ()
            if "=" in str(fact)
        }
        family = str((candidate.get("provenance") or {}).get("failure_family") or "")
        for (evidence_family, predicate), evidence in evidence_by_predicate.items():
            if predicate not in fact_names:
                continue
            if evidence_family != family:
                continue
            evidence_id = str(evidence.get("intervention_id") or "")
            guards_by_evidence.setdefault(evidence_id, []).append(
                str(candidate.get("intervention_id") or "")
            )
            break
    return [
        [evidence_id, *guard_ids]
        for evidence_id, guard_ids in guards_by_evidence.items()
    ]


def _drop_orphan_evidence(
    proposals: Sequence[Mapping[str, Any]],
    notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    paired_evidence = {
        str(group[0]) for group in infer_joint_groups(proposals)
    }
    filtered: list[dict[str, Any]] = []
    for candidate in proposals:
        identifier = str(candidate.get("intervention_id") or "")
        if (
            candidate.get("type") == "evidence_check"
            and identifier not in paired_evidence
        ):
            notes.append({
                "intervention_id": identifier,
                "generator": "llm",
                "rejected": "missing_terminal_partner",
                "reason": (
                    "evidence_check cannot change the terminal verdict without "
                    "a same-family decision_guard consuming its predicate"
                ),
            })
            continue
        filtered.append(dict(candidate))
    return filtered


def _proposal_fingerprint(candidate: Mapping[str, Any]) -> tuple[Any, ...] | None:
    key = structured_key(candidate)
    if key is not None:
        return key
    if candidate.get("type") == "text_boundary":
        body = candidate.get("body") or {}
        return (
            "text_boundary",
            str(candidate.get("hook") or ""),
            tuple(sorted(
                str(item) for item in (candidate.get("trigger") or {}).get(
                    "packet_features"
                ) or ()
            )),
            str(body.get("directive") or "").strip(),
            str(body.get("not_when") or "").strip(),
        )
    return None


class HybridProposer:
    """LLM-first proposer with deterministic typed-DSL fallback templates.

    The LLM remains the semantic proposal source and spends its normal
    budget first. The template batch runs only when the LLM path yields zero
    valid candidates in the same call (provider error, parse failure, or
    local contract rejections) — never after the downstream
    STRUCTURAL/HARMLESSNESS/BEHAVIOR/M gates rejected an LLM candidate; those
    rejections reach the next draft through ``set_rejection_feedback``
    instead. Fallback emits mechanically safe candidates from the family
    diagnostics:
    shared packet-feature recall_trigger policies, a missing-search
    evidence+conviction unit, and unanimous built-in FP withhold units. All
    fallback candidates still pass STRUCTURAL-HARMLESSNESS-BEHAVIOR-M; they never bypass a gate.
    """

    name = "hybrid"

    def __init__(
        self,
        framework_config: Mapping[str, Any],
        *,
        active_entries: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.llm = LLMProposer(
            framework_config, active_entries=active_entries,
        )

    @property
    def active_entries(self) -> list[dict[str, Any]]:
        return self.llm.active_entries

    @active_entries.setter
    def active_entries(self, entries: Sequence[Mapping[str, Any]]) -> None:
        # The engine re-binds the registry before every drafting call with a
        # plain attribute assignment; forwarding the setter keeps the inner
        # LLM proposer in sync without changing that call site.
        self.llm.active_entries = [dict(entry) for entry in entries]

    def set_rejection_feedback(
        self, feedback: Sequence[Mapping[str, Any]],
    ) -> None:
        """Forward gate rejections so the next draft avoids them."""
        self.llm.set_rejection_feedback(feedback)

    def set_research_packet(
        self, packet: Mapping[str, Any] | None,
    ) -> None:
        """Forward the bounded research packet to the LLM drafting stage."""
        self.llm.set_research_packet(packet)

    def set_memory_context(
        self, context: Mapping[str, Any] | None,
    ) -> None:
        """Forward retrieved H_t memory to the LLM drafting stage."""
        self.llm.set_memory_context(context)

    def propose(
        self,
        families: Sequence[Mapping[str, Any]],
        *,
        cycle: int,
        max_proposals: int = 3,
    ) -> ProposalBatch:
        llm_batch = self.llm.propose(
            families, cycle=cycle, max_proposals=max_proposals,
        )
        if llm_batch.candidates:
            return llm_batch
        templates = self._template_batch(
            families, cycle=cycle, max_proposals=max_proposals,
        )
        notes = [*llm_batch.notes, {
            "generator": "hybrid_fallback",
            "provider_called": False,
            "reason": (
                "llm proposer produced no valid candidates; deterministic "
                "typed-DSL capability templates supplied instead"
            ),
        }]
        valid: list[dict[str, Any]] = []
        for candidate in templates:
            try:
                validate_intervention_batch(
                    [*valid, candidate], require_executable_bounds=True,
                )
                valid.append(candidate)
            except ValueError as exc:
                notes.append({
                    "intervention_id": str(candidate.get("intervention_id") or ""),
                    "generator": "hybrid_fallback",
                    "rejected": "invalid_contract",
                    "reason": str(exc),
                })
        return ProposalBatch(
            candidates=valid,
            joint_groups=infer_joint_groups(valid),
            notes=notes,
        )

    @staticmethod
    def _member_profiles(
        family: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        members = (family.get("diagnostics") or {}).get("member_summaries") or []
        return [
            (member.get("typed_samples") or {})
            for member in members
        ]

    def _template_batch(
        self,
        families: Sequence[Mapping[str, Any]],
        *,
        cycle: int,
        max_proposals: int,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for family in families:
            family_id = str(family.get("failure_family") or "")
            if not family_id or len(candidates) >= max_proposals:
                continue
            if family_id.startswith("FAM-fn-"):
                shared_features = self._shared_packet_features(family)
                for feature in shared_features[:2]:
                    if len(candidates) >= max_proposals:
                        break
                    slug = re.sub(r"[^a-z0-9_]", "", feature.lower())
                    if not slug:
                        continue
                    candidates.append(self._recall_trigger_candidate(
                        family, cycle, f"evo_recall_{slug}", feature,
                    ))
                if self._family_requires_search(family):
                    candidates.extend(
                        self._missing_search_candidates(family, cycle)
                    )
            elif family_id.startswith("FAM-fp-") and len(candidates) < max_proposals:
                candidates.extend(self._fp_candidates(family, cycle))
            elif family_id.startswith("FAM-loc-") and len(candidates) < max_proposals:
                # Without this branch a localization family produced nothing in
                # the fallback at all, so when the LLM proposer truncated the
                # entire localization objective fell through silently.
                candidates.extend(self._localization_candidates(family, cycle))
        return candidates[:max_proposals]

    @staticmethod
    def _shared_packet_features(
        family: Mapping[str, Any],
    ) -> list[str]:
        profiles = HybridProposer._member_profiles(family)
        if not profiles:
            return []
        shared = set(profiles[0].get("packet_features") or [])
        for profile in profiles[1:]:
            shared &= set(profile.get("packet_features") or [])
        return sorted(shared)

    @classmethod
    def _family_requires_search(cls, family: Mapping[str, Any]) -> bool:
        profiles = cls._member_profiles(family)
        if not profiles:
            return False
        for profile in profiles:
            question = str(profile.get("question_text") or "").lower()
            tools = [str(item).lower() for item in profile.get("tool_names") or ()]
            if "search" not in question:
                return False
            if any(
                marker in tool
                for tool in tools for marker in ("search", "grep", "find")
            ):
                return False
        return True

    @staticmethod
    def _base_provenance(family: Mapping[str, Any], cycle: int) -> dict[str, Any]:
        return {
            "cycle": int(cycle),
            "failure_family": str(family.get("failure_family") or ""),
            "failure_cases": [
                str(case) for case in family.get("failure_cases") or ()
            ],
        }

    @classmethod
    def _recall_trigger_candidate(
        cls,
        family: Mapping[str, Any],
        cycle: int,
        name: str,
        feature: str,
    ) -> dict[str, Any]:
        program = {
            "schema_version": "detector.predicate_program.v1",
            "name": name,
            "description": f"force recall for {feature} judge-negative cases",
            "verdicts": ["satisfied", "violated", "not_applicable"],
            "conviction_verdicts": ["violated"],
            "default_verdict": "not_applicable",
            "rules": [{
                "field": "packet_features",
                "operator": "any_equals",
                "value": feature,
                "verdict": "violated",
                "detail": f"shared family packet feature {feature}",
            }],
        }
        return {
            "schema_version": "detector.intervention.v1",
            "intervention_id": f"IV-{cycle}-{name}",
            "type": "recall_trigger",
            "hook": "recall.trigger",
            "trigger": {
                "packet_features": ["has_tool_calls"],
                "facts": ["judge_positive=false"],
            },
            "body": {
                "predicate": name,
                "runner": "typed_dsl",
                "output_verdicts": ["satisfied", "violated", "not_applicable"],
                "trigger_verdicts": ["violated"],
                "require_judge_negative": True,
                "predicate_definition": program,
            },
            "claim": {
                "expected_behavior": f"force recall for {feature}",
                "target_polarity": "recall",
                "target_kind": "detection",
            },
            "provenance": cls._base_provenance(family, cycle),
        }

    @classmethod
    def _missing_search_candidates(
        cls,
        family: Mapping[str, Any],
        cycle: int,
    ) -> list[dict[str, Any]]:
        name = "evo_missing_search_required"
        program = {
            "schema_version": "detector.predicate_program.v1",
            "name": name,
            "description": "question requires search but no search-like tool ran",
            "verdicts": ["satisfied", "violated", "not_applicable"],
            "conviction_verdicts": ["violated"],
            "default_verdict": "not_applicable",
            "rules": [
                {"field": "tool_names", "operator": "any_contains", "value": "search", "verdict": "satisfied", "detail": "search tool present"},
                {"field": "tool_names", "operator": "any_contains", "value": "grep", "verdict": "satisfied", "detail": "grep tool present"},
                {"field": "tool_names", "operator": "any_contains", "value": "find", "verdict": "satisfied", "detail": "find tool present"},
                {
                    "conditions": [
                        {"field": "question_text", "operator": "contains", "value": "search"},
                        {"field": "tool_names", "operator": "none_contains", "value": "search"},
                        {"field": "tool_names", "operator": "none_contains", "value": "grep"},
                        {"field": "tool_names", "operator": "none_contains", "value": "find"},
                    ],
                    "verdict": "violated",
                    "detail": "question requires search but no search-like tool ran",
                },
            ],
        }
        provenance = cls._base_provenance(family, cycle)
        evidence = {
            "schema_version": "detector.intervention.v1",
            "intervention_id": f"IV-{cycle}-E-{name}",
            "type": "evidence_check",
            "hook": "judge.post",
            "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
            "body": {
                "predicate": name,
                "runner": "typed_dsl",
                "output_verdicts": ["satisfied", "violated", "not_applicable"],
                "predicate_definition": program,
            },
            "claim": {
                "expected_behavior": "detect required search without search-like tool",
                "target_polarity": "recall",
                "target_kind": "detection",
            },
            "provenance": dict(provenance),
        }
        guard = {
            "schema_version": "detector.intervention.v1",
            "intervention_id": f"IV-{cycle}-G-{name}",
            "type": "decision_guard",
            "hook": "adjudicate.guard",
            "trigger": {
                "packet_features": ["has_tool_calls"],
                "facts": [
                    f"{name}=violated",
                    "judge_positive=false",
                    "positive_source=none",
                ],
            },
            "body": {
                "action": "convict_mechanical_violation",
                "evidence_predicate": name,
                "action_not_when": "never when a search-like tool ran",
                "action_not_when_facts": [f"{name}=satisfied"],
            },
            "claim": {
                "expected_behavior": "convict mechanically missing required search",
                "target_polarity": "recall",
                "target_kind": "detection",
            },
            "provenance": dict(provenance),
        }
        return [evidence, guard]

    @classmethod
    def _fp_candidates(
        cls,
        family: Mapping[str, Any],
        cycle: int,
    ) -> list[dict[str, Any]]:
        signals = (family.get("diagnostics") or {}).get("predicate_signals") or {}
        count = int(family.get("count") or 0)
        provenance = cls._base_provenance(family, cycle)
        for predicate, signal in signals.items():
            if not (
                int(signal.get("applicable") or 0) == count
                and int(signal.get("violations") or 0) == count
            ):
                continue
            verdicts = sorted(PREDICATE_VERDICTS.get(predicate) or ())
            violating = [
                verdict for verdict in verdicts
                if verdict not in {"satisfied", "consistent", "resolved", "covered", "present"}
            ]
            if not violating:
                continue
            verdict = violating[0]
            evidence = {
                "schema_version": "detector.intervention.v1",
                "intervention_id": f"IV-{cycle}-E-{predicate}",
                "type": "evidence_check",
                "hook": "judge.post",
                "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
                "body": {
                    "predicate": predicate,
                    "runner": "deterministic",
                    "output_verdicts": verdicts,
                },
                "claim": {
                    "expected_behavior": f"write {predicate} evidence",
                    "target_polarity": "precision",
                    "target_kind": "detection",
                },
                "provenance": dict(provenance),
            }
            guard = {
                "schema_version": "detector.intervention.v1",
                "intervention_id": f"IV-{cycle}-G-{predicate}",
                "type": "decision_guard",
                "hook": "adjudicate.guard",
                "trigger": {
                    "packet_features": ["has_tool_calls"],
                    "facts": [
                        f"{predicate}={verdict}",
                        "judge_positive=true",
                        "positive_source=judge",
                    ],
                },
                "body": {
                    "action": "withhold_conviction",
                    "action_not_when": "never when the predicate clears",
                    "action_not_when_facts": [
                        f"{predicate}={value}"
                        for value in verdicts if value != verdict
                    ],
                },
                "claim": {
                    "expected_behavior": f"withhold {predicate} conviction",
                    "target_polarity": "precision",
                    "target_kind": "detection",
                },
                "provenance": dict(provenance),
            }
            return [evidence, guard]
        return []

    @classmethod
    def _localization_candidates(
        cls,
        family: Mapping[str, Any],
        cycle: int,
    ) -> list[dict[str, Any]]:
        """Deterministic tracer directive for a localization family.

        The conviction is already correct on these cases, so the only stage
        that can change the outcome is the one that names the step. The
        directive is deliberately mechanism-level rather than case-level: it
        states the property the shared members exhibit (a delivery whose named
        step is not the first assistant-authored event that fixes the claim),
        which a tracer can apply without knowing the case.
        """
        shared = cls._shared_packet_features(family) or ["has_tool_calls"]
        provenance = cls._base_provenance(family, cycle)
        return [{
            "schema_version": "detector.intervention.v1",
            "intervention_id": f"IV-{cycle}-T-locate_delivered_claim",
            "type": "text_boundary",
            "hook": "tracer.system",
            "trigger": {
                "packet_features": sorted(shared),
                "facts": [],
            },
            "body": {
                "directive": (
                    "Name the step at which the delivered claim first becomes "
                    "unsupported, not the step at which the unsupported claim "
                    "is finally restated. When several steps repeat a wrong "
                    "value, the responsible step is the earliest "
                    "assistant-authored event that introduces it and would "
                    "have to change for the delivery to be correct."
                ),
                "not_when": (
                    "do not move the named step away from an event the packet "
                    "itself identifies as the delivery; do not use benchmark "
                    "annotations"
                ),
            },
            "claim": {
                "expected_behavior": (
                    "the tracer reports the earliest assistant-authored event "
                    "that fixes the delivered claim, so localization matches "
                    "the introduction of the error rather than its repetition"
                ),
                "target_polarity": "precision",
                "target_kind": "localization",
            },
            "provenance": dict(provenance),
        }]


def make_proposer(
    config: Mapping[str, Any],
    *,
    active_entries: Sequence[Mapping[str, Any]] = (),
) -> LLMProposer | OfflineProposer | HybridProposer:
    mode = str((config.get("proposer") or {}).get("mode") or "llm")
    if mode == "offline":
        return OfflineProposer()
    if mode == "hybrid":
        return HybridProposer(config, active_entries=active_entries)
    if mode != "llm":
        raise ValueError(f"unknown proposer mode {mode!r}")
    return LLMProposer(config, active_entries=active_entries)


__all__ = [
    "HybridProposer",
    "LLMProposer", "OfflineProposer", "ProposalBatch", "infer_joint_groups",
    "make_proposer",
]
