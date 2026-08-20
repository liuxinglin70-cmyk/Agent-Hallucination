"""Candidate proposer.

The LLM is the only proposal source. Drafting an intervention from a failure
family is a generative step; a fixed template library would hard-code the
experimenter's hypotheses into the loop and silently cap what the framework
can discover. Discipline stays mechanical: the draft must fit the closed
detector-side schema, must not echo an active intervention, and is validated
before it can spend gate budget. On any parse or provider error the proposer
returns zero candidates (the cycle is reported, not padded).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from detector_v2.config import load_config as load_detector_config
from detector_v2.predicates import (
    PREDICATE_CONVICTION_VERDICTS,
    PREDICATE_VERDICTS,
)
from detector_v2.provider import ProviderError, provider_call

from evolution_v3.config import resolve_path
from evolution_v3.contracts import redact_case_ids, structured_key


@dataclass
class ProposalBatch:
    candidates: list[dict[str, Any]] = field(default_factory=list)
    joint_groups: list[list[str]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)


_LLM_DRAFT_SYSTEM_TEMPLATE = """\
Return ONLY one JSON object: {"interventions": [ ... ]}.
Do not explain your answer, use Markdown, case ids, labels, or case text.

Every intervention must use exactly ONE of these mutually exclusive schemas.

Evidence schema (writes one mechanical evidence row):
{"type":"evidence_check","hook":"judge.post|upgrade.pre|defense.pre","trigger":{"packet_features":["has_tool_calls"],"facts":[]},"body":{"predicate":"<one predicate from the predicate list>","runner":"deterministic","output_verdicts":["<all verdicts listed for that predicate>"]},"claim":{"expected_behavior":"nonempty","target_polarity":"recall|precision|boundary"},"provenance":{"failure_family":"<one supplied family>"}}

Guard schema (emits one closed adjudication action):
{"type":"decision_guard","hook":"adjudicate.guard","trigger":{"packet_features":["has_tool_calls"],"facts":["name=value from the fact list"]},"body":{"action":"withhold_conviction|restore_judge_conviction|require_full_view_defense|convict_mechanical_violation","evidence_predicate":"<required only for convict_mechanical_violation>","action_not_when":"nonempty explanation","action_not_when_facts":["name=value from the fact list"]},"claim":{"expected_behavior":"nonempty","target_polarity":"recall|precision|boundary"},"provenance":{"failure_family":"<one supplied family>"}}

Predicates and their verdicts:
{predicate_block}

Conviction-authorized predicate verdicts:
{conviction_block}

Stage facts (guard triggers only):
judge_confidence=high|medium|low; judge_positive=true|false;
positive_source=judge|recall_upgrade|none;
defense_verdict=substantiated|refuted|inconclusive|none;
defense_view_degraded=true|false; upgrade_degraded=true|false;
recall_upgrade=present|none.
A predicate name itself is also a fact once its evidence row ran, with the
verdict that predicate produced (for example required_action_present=violated).

Guidance:
- For an FN family (detector said clean but gold says error), first determine
  that one mechanism is shared by the supplied member summaries. An
  evidence_check may be paired with a convict_mechanical_violation guard only
  when the evidence predicate is applicable and violating for every member;
  its trigger must also require judge_positive=false and positive_source=none,
  so it cannot override a positive hearing or defense; otherwise prefer an
  evidence-only candidate or no candidate.
- For an FN family where a positive existed but was lost: restore_judge_conviction
  with the facts that identify the over-refutation.
- For an FP family: withhold_conviction with the facts that identify the
  unjustified positive, or an evidence_check that exposes unresolvable evidence.
- target_polarity must match the family kind: recall for FN, precision for FP.
- Every guard needs action_not_when_facts. They are ANDed executable exclusions;
  use [] only when no exclusion is required.
 - Never re-propose a mechanism equivalent to an active intervention or a
   rejected candidate listed in the feedback.
"""


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
                "max_output_tokens", 4096
            )
        )
        if max_output_tokens > 0:
            # Candidate drafting needs a compact JSON array, not an
            # unbounded reasoning response.  This limit applies only to the
            # proposer route, never to detector stages or prior baselines.
            self.route["max_tokens"] = max_output_tokens
        if bool((framework_config.get("proposer") or {}).get(
            "disable_thinking", True
        )):
            # DeepSeek V4 otherwise spends its whole response budget in
            # reasoning_content before emitting the required JSON object.
            self.route["extra_body"] = {"thinking": {"type": "disabled"}}
        self.route["_route_name"] = "evolution_proposer"
        self.max_calls = max(
            0,
            int((framework_config.get("budget") or {}).get("llm_calls", 1)),
        )
        # The LLM budget is per cycle: the protocol caps failure-family
        # diagnosis plus candidate drafting at N calls per cycle, so a long
        # experiment must never exhaust its drafting ability mid-run.
        self.calls_by_cycle: dict[int, int] = {}
        self.active_entries = [dict(entry) for entry in active_entries]
        self.rejection_feedback: list[dict[str, Any]] = []
        self.rejected_keys: set[tuple[Any, ...]] = set()
        self.rejected_group_keys: set[frozenset[tuple[Any, ...]]] = set()

    def set_rejection_feedback(
        self, feedback: Sequence[Mapping[str, Any]],
    ) -> None:
        self.rejection_feedback = [dict(item) for item in feedback]
        self.rejected_keys = {
            key for key in (
                _proposal_fingerprint(item.get("candidate") or {})
                for item in self.rejection_feedback
            ) if key is not None
        }
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
                    "kind": "FN" if str(f.get("failure_family") or "").startswith("FAM-fn") else "FP",
                    "count": f.get("count"),
                    "sources": f.get("sources"),
                    "mechanism_summary": f.get("mechanism_summary"),
                    "mechanism_signature": f.get("mechanism_signature"),
                    "predicate_signals": (f.get("diagnostics") or {}).get(
                        "predicate_signals", {}
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
            f"Previously rejected mechanisms (do not repeat unchanged):\n"
            f"{feedback_block}\n\n"
            f"Stable bare-base failure families:\n{family_block}\n\n"
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
            return ProposalBatch(notes=[{
                "generator": "llm",
                "provider_called": True,
                "provider_ok": False,
                "error_type": exc.error_type,
                "attempts": exc.attempts,
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
        proposals: list[dict[str, Any]] = []
        batch_keys: set[tuple[Any, ...]] = set()
        notes: list[dict[str, Any]] = [generation_note]
        for index, item in enumerate(raw[: max_proposals], start=1):
            if not isinstance(item, Mapping):
                continue
            candidate = dict(item)
            candidate.setdefault("schema_version", "detector_v2.intervention.v1")
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
            try:
                from detector_v2.interventions import validate_intervention
                validate_intervention(
                    candidate, require_executable_bounds=True,
                )
            except ValueError as exc:
                notes.append({
                    "intervention_id": candidate["intervention_id"],
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
                        "intervention_id": candidate["intervention_id"],
                        "generator": "llm",
                        "rejected": "echo_of_active",
                        "reason": f"structured key {key} already deployed",
                    })
                    continue
                if key in batch_keys:
                    notes.append({
                        "intervention_id": candidate["intervention_id"],
                        "generator": "llm",
                        "rejected": "duplicate_in_batch",
                        "reason": f"structured key {key} drafted twice",
                    })
                    continue
                batch_keys.add(key)
            if proposal_fingerprint in self.rejected_keys:
                notes.append({
                    "intervention_id": candidate["intervention_id"],
                    "generator": "llm",
                    "rejected": "echo_of_rejected",
                    "reason": "executable mechanism previously failed a gate",
                })
                continue
            proposals.append(candidate)
            notes.append({
                "intervention_id": candidate["intervention_id"],
                "family": family,
                "generator": "llm",
                "frozen_judge_replay_compatible": str(
                    candidate.get("hook") or ""
                ) != "judge.system",
            })
        joint_groups = _joint_groups(proposals)
        grouped_ids = {item for group in joint_groups for item in group}
        filtered: list[dict[str, Any]] = []
        for candidate in proposals:
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
            filtered.append(candidate)
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
        return ProposalBatch(
            candidates=filtered,
            joint_groups=_joint_groups(filtered),
            notes=notes,
        )


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


def _joint_groups(proposals: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    """Pair each guard with the evidence row that produces its predicate fact.

    A joint L1+L2 admission is only principled when the guard's trigger
    consumes a mechanical fact the evidence candidate actually writes in the
    same cycle and for the same failure family; otherwise the pair has no
    causal dependency and must stand or fall individually.
    """
    evidence_by_predicate: dict[str, Mapping[str, Any]] = {}
    for candidate in proposals:
        if candidate.get("type") == "evidence_check":
            predicate = str((candidate.get("body") or {}).get("predicate") or "")
            evidence_by_predicate.setdefault(predicate, candidate)
    groups: list[list[str]] = []
    for candidate in proposals:
        if candidate.get("type") != "decision_guard":
            continue
        fact_names = {
            str(fact).split("=", 1)[0]
            for fact in (candidate.get("trigger") or {}).get("facts") or ()
            if "=" in str(fact)
        }
        family = str((candidate.get("provenance") or {}).get("failure_family") or "")
        for predicate, evidence in evidence_by_predicate.items():
            if predicate not in fact_names:
                continue
            if str((evidence.get("provenance") or {}).get("failure_family") or "") != family:
                continue
            groups.append([
                str(evidence.get("intervention_id") or ""),
                str(candidate.get("intervention_id") or ""),
            ])
            break
    return groups


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


def make_proposer(
    config: Mapping[str, Any],
    *,
    active_entries: Sequence[Mapping[str, Any]] = (),
) -> LLMProposer | OfflineProposer:
    mode = str((config.get("proposer") or {}).get("mode") or "llm")
    if mode == "offline":
        return OfflineProposer()
    if mode != "llm":
        raise ValueError(f"unknown proposer mode {mode!r}")
    return LLMProposer(config, active_entries=active_entries)


__all__ = ["LLMProposer", "OfflineProposer", "ProposalBatch", "make_proposer"]
