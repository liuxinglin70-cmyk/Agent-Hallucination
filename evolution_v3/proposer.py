"""Candidate proposer.

Candidates are drafted only from stable bare-base failure families. The
default proposer is deterministic template-based (no LLM cost) and is
sufficient to exercise every gate. ``LLMProposer`` is available for the M2
text-boundary drafting path; it is deliberately conservative: on any parse or
provider error it returns zero candidates (the cycle is reported, not padded).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from detector_v2.config import load_config as load_detector_config
from detector_v2.predicates import PREDICATE_VERDICTS
from detector_v2.provider import ProviderError, provider_call

from evolution_v3.config import resolve_path
from evolution_v3.contracts import make_intervention


def _signal(family_info: Mapping[str, Any], predicate: str) -> dict[str, Any]:
    diagnostics = family_info.get("diagnostics") or {}
    signals = diagnostics.get("predicate_signals") or {}
    return signals.get(predicate) or {}


def _templates_for_family(
    family: str,
    family_info: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return ranked candidate templates for one failure family.

    Ranking prefers deterministic executors and hooks that do NOT target the
    judge, because non-judge hooks remain eligible for frozen-judge paired
    replay (the strongest available noise control).
    """
    family_info = family_info or {}
    templates: list[dict[str, Any]] = []
    if family == "FAM-fn-upgrade_missing_rows":
        required_violations = int(
            _signal(family_info, "required_action_present").get("violations") or 0
        )
        verbatim_violations = int(
            _signal(family_info, "verbatim_fidelity").get("violations") or 0
        )
        predicate = (
            "verbatim_fidelity"
            if verbatim_violations > required_violations
            else "required_action_present"
        )
        templates.append({
            "name": "upgrade_pre_mechanical_contract",
            "quality": 3,
            "type": "evidence_check",
            "hook": "upgrade.pre",
            "packet_features": ["has_tool_calls"],
            "body": {
                "predicate": predicate,
                "runner": "deterministic",
                "output_verdicts": sorted(PREDICATE_VERDICTS[predicate]),
            },
            "expected_behavior": (
                f"when trigger fires, the causal upgrade prompt gains a "
                f"{predicate} evidence row before any candidate is evaluated"
            ),
            "target_polarity": "recall",
        })
        templates.append({
            "name": "upgrade_system_row_coverage_boundary",
            "quality": 2,
            "type": "text_boundary",
            "hook": "upgrade.system",
            "packet_features": ["has_tool_calls"],
            "body": {
                "directive": (
                    "Return one explicit row for every candidate listed in "
                    "CANDIDATES; never omit a candidate. Use the exact JSON "
                    "schema and repeat every candidate_id."
                ),
                "not_when": (
                    "Do not apply when every candidate already has an explicit "
                    "row and no candidate was omitted."
                ),
            },
            "expected_behavior": (
                "when trigger fires, the upgrade response contains an explicit "
                "row for every recall candidate"
            ),
            "target_polarity": "recall",
        })
        return templates

    if family == "FAM-fn-defense_refuted_judge":
        templates.append({
            "name": "restore_judge_conviction_guard",
            "quality": 3,
            "type": "decision_guard",
            "hook": "adjudicate.guard",
            "packet_features": ["has_tool_calls"],
            "facts": ["positive_source=judge", "defense_verdict=refuted"],
            "body": {
                "action": "restore_judge_conviction",
                "action_not_when": (
                    "Do not apply when defense refuted with evidence refs "
                    "that resolve to the quoted offending content."
                ),
            },
            "expected_behavior": (
                "when defense refutes a judge conviction, the guard restores "
                "the judge conviction so the true positive is not lost"
            ),
            "target_polarity": "recall",
        })
        return templates

    if family == "FAM-fn-defense_refuted_recall_upgrade":
        # Deterministic joint candidate:
        #   L1 evidence_check writes required_action_present=violated
        #   L2 guard restores the recall-upgrade conviction only when that
        #     mechanical fact is true and defense refuted it.
        # No LLM text boundary; no case id.
        templates.append({
            "name": "judge_post_required_action_evidence",
            "quality": 3,
            "type": "evidence_check",
            "hook": "judge.post",
            "packet_features": ["has_tool_calls"],
            "body": {
                "predicate": "required_action_present",
                "runner": "deterministic",
                "output_verdicts": [
                    "satisfied", "violated", "not_applicable",
                ],
            },
            "expected_behavior": (
                "when trigger fires, proof gains a required_action_present "
                "mechanical evidence row after the judge"
            ),
            "target_polarity": "boundary",
        })
        templates.append({
            "name": "restore_required_action_conviction_guard",
            "quality": 2,
            "type": "decision_guard",
            "hook": "adjudicate.guard",
            "packet_features": ["has_tool_calls"],
            "facts": [
                "positive_source=recall_upgrade",
                "defense_verdict=refuted",
                "required_action_present=violated",
            ],
            "body": {
                "action": "restore_judge_conviction",
                "action_not_when": (
                    "Do not apply when the required_action_present fact is "
                    "satisfied or not_applicable."
                ),
            },
            "expected_behavior": (
                "when a recall-upgrade positive is refuted while a required "
                "action is mechanically violated, the guard restores the "
                "conviction"
            ),
            "target_polarity": "recall",
        })
        return templates

    if family.startswith("FAM-fn-judge_false"):
        required_signal = int(
            _signal(family_info, "required_action_present").get("violations") or 0
        )
        verbatim_signal = int(
            _signal(family_info, "verbatim_fidelity").get("violations") or 0
        )
        # Multiple recall-side text boundaries, ordered by the mechanical
        # predicate evidence found in the actual failure packets. This raises
        # the probability that at least one proposal matches the family's
        # true mechanism without ever writing a case id into the candidate.
        scanner_boundaries = [
            {
                "name": "scanner_required_action_recall_boundary",
                "directive": (
                    "Before returning candidates, extract every explicitly "
                    "required tool action from the question and create one "
                    "candidate for each required action that has no matching "
                    "tool call in the trajectory."
                ),
                "not_when": (
                    "Do not apply when every explicitly required action has "
                    "a matching tool call and the delivered result satisfies "
                    "the task contract."
                ),
                "expected_behavior": (
                    "when trigger fires, recall scanner candidates cover "
                    "missing required_action tool calls from the question"
                ),
            },
            {
                "name": "scanner_verbatim_contract_recall_boundary",
                "directive": (
                    "Before returning candidates, extract every quoted "
                    "verbatim_content or exact_value the question explicitly "
                    "fixes and create one candidate whenever the delivered "
                    "text or final answer tool arguments do not reproduce it "
                    "literally."
                ),
                "not_when": (
                    "Do not apply when every quoted verbatim_content and "
                    "exact_value contract is delivered literally."
                ),
                "expected_behavior": (
                    "when trigger fires, recall scanner candidates cover "
                    "verbatim_content and exact_value contract violations"
                ),
            },
            {
                "name": "scanner_observation_fidelity_recall_boundary",
                "directive": (
                    "Before returning candidates, compare each tool call and "
                    "later assistant text against its own tool observation; "
                    "create one candidate for every misreported, fabricated "
                    "or reordered observation."
                ),
                "not_when": (
                    "Do not apply when every tool call and assistant text "
                    "faithfully reports its own tool observation."
                ),
                "expected_behavior": (
                    "when trigger fires, recall scanner candidates cover "
                    "misreported tool observations"
                ),
            },
        ]
        if required_signal >= verbatim_signal and required_signal > 0:
            scanner_boundaries.sort(
                key=lambda item: 0
                if "required_action" in item["name"] else 1
            )
        elif verbatim_signal > 0:
            scanner_boundaries.sort(
                key=lambda item: 0
                if "verbatim" in item["name"] else 1
            )
        for index, boundary in enumerate(scanner_boundaries):
            templates.append({
                "name": boundary["name"],
                "quality": 3 - index,
                "type": "text_boundary",
                "hook": "scanner.system",
                "packet_features": ["has_tool_calls"],
                "body": {
                    "directive": boundary["directive"],
                    "not_when": boundary["not_when"],
                },
                "expected_behavior": boundary["expected_behavior"],
                "target_polarity": "recall",
            })

        # When upgrade already produced candidates but did not convict,
        # mechanical evidence is more stable than more prose.
        if family.endswith("upgrade_no_upgrade"):
            predicate = (
                "required_action_present"
                if required_signal > 0 else "verbatim_fidelity"
            )
            templates.append({
                "name": "upgrade_pre_mechanical_contract",
                "quality": 3,
                "type": "evidence_check",
                "hook": "upgrade.pre",
                "packet_features": ["has_tool_calls"],
                "body": {
                    "predicate": predicate,
                    "runner": "deterministic",
                    "output_verdicts": sorted(PREDICATE_VERDICTS[predicate]),
                },
                "expected_behavior": (
                    f"when trigger fires, the upgrade prompt gains a "
                    f"{predicate} mechanical evidence row"
                ),
                "target_polarity": "recall",
            })
        return templates

    if family.startswith("FAM-fp-recall_upgrade"):
        # A blunt withhold_conviction guard would also kill recall-upgrade
        # true positives (their defense verdict is substantiated too), so the
        # default precision lever is a stricter defense boundary plus
        # mechanical ref-resolution evidence.
        templates.append({
            "name": "defense_recall_upgrade_rigor_boundary",
            "quality": 3,
            "type": "text_boundary",
            "hook": "defense.system",
            "packet_features": ["has_tool_calls"],
            "body": {
                "directive": (
                    "For a recall_upgrade positive, choose substantiated only "
                    "when the exact quoted text is located in a cited event "
                    "and that text contradicts an explicit task contract or "
                    "a tool observation. If the quote cannot be located, or "
                    "the claimed deviation is a process/style preference, "
                    "choose refuted."
                ),
                "not_when": (
                    "Do not apply when every cited recall-upgrade quote is "
                    "already located and contradicted by a task contract or "
                    "tool observation."
                ),
            },
            "expected_behavior": (
                "when trigger fires, defense rejects recall-upgrade positives "
                "whose evidence refs or quotes do not resolve to a real "
                "contract/observation contradiction"
            ),
            "target_polarity": "precision",
        })
        templates.append({
            "name": "defense_pre_ref_resolution_evidence",
            "quality": 2,
            "type": "evidence_check",
            "hook": "defense.pre",
            "packet_features": ["has_tool_calls"],
            "body": {
                "predicate": "evidence_refs_resolve",
                "runner": "deterministic",
                "output_verdicts": [
                    "resolved", "unresolved", "not_applicable",
                ],
            },
            "expected_behavior": (
                "when trigger fires, defense input gains an "
                "evidence_refs_resolve mechanical evidence row for the "
                "recall-upgrade evidence"
            ),
            "target_polarity": "precision",
        })
        return templates

    if family.startswith("FAM-fp-judge_conviction"):
        templates.append({
            "name": "defense_substantiation_rigor_boundary",
            "quality": 3,
            "type": "text_boundary",
            "hook": "defense.system",
            "packet_features": ["has_tool_calls"],
            "body": {
                "directive": (
                    "Before choosing substantiated, verify that the exact "
                    "quoted text is located in a cited event and that it "
                    "contradicts an explicit task contract or a tool "
                    "observation. If the quote cannot be located, or the "
                    "alleged deviation is a process/style preference, "
                    "choose refuted."
                ),
                "not_when": (
                    "Do not apply when every cited quote is already located "
                    "and contradicted by a task contract or tool observation."
                ),
            },
            "expected_behavior": (
                "when trigger fires, defense substantiates a judge positive "
                "only when its quote and contract/observation contradiction "
                "are mechanically locatable"
            ),
            "target_polarity": "precision",
        })
        templates.append({
            "name": "require_full_view_defense_guard",
            "quality": 2,
            "type": "decision_guard",
            "hook": "adjudicate.guard",
            "packet_features": ["has_tool_calls"],
            "facts": ["positive_source=judge"],
            "body": {
                "action": "require_full_view_defense",
                "action_not_when": (
                    "Do not apply when the defense view is already complete "
                    "and no evidence was omitted."
                ),
            },
            "expected_behavior": (
                "when a judge positive reaches defense, the defense stage is "
                "forced to use the full trajectory view"
            ),
            "target_polarity": "precision",
        })
        return templates
    return []


@dataclass
class ProposalBatch:
    candidates: list[dict[str, Any]] = field(default_factory=list)
    joint_groups: list[list[str]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)


_L1_HOOKS = frozenset({
    "judge.system", "judge.post", "scanner.system", "upgrade.pre",
    "upgrade.system", "tracer.system",
})


class TemplateProposer:
    name = "template"

    def propose(
        self,
        families: Sequence[Mapping[str, Any]],
        *,
        cycle: int,
        max_proposals: int = 3,
    ) -> ProposalBatch:
        proposals: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        family_template_lists: list[
            tuple[Mapping[str, Any], list[dict[str, Any]]]
        ] = []
        for family_info in sorted(
            families,
            key=lambda item: (
                -int(item.get("count") or 0),
                str(item.get("failure_family") or ""),
            ),
        ):
            family = str(family_info.get("failure_family") or "")
            templates = sorted(
                _templates_for_family(family, family_info),
                key=lambda item: -int(item.get("quality") or 0),
            )
            if templates:
                family_template_lists.append((family_info, templates))

        def add_template(
            family_info: Mapping[str, Any],
            template: Mapping[str, Any],
        ) -> bool:
            if len(proposals) >= max_proposals:
                return False
            family = str(family_info.get("failure_family") or "")
            body = dict(template.get("body") or {})
            body_fingerprint = hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True).encode(
                    "utf-8"
                )
            ).hexdigest()[:10]
            key = (
                str(template.get("type") or ""),
                str(template.get("hook") or ""),
                body_fingerprint,
            )
            if key in seen_keys:
                return True
            seen_keys.add(key)
            seq = len(proposals) + 1
            candidate = make_intervention(
                intervention_id=f"IV-{int(cycle)}-{seq:03d}",
                intervention_type=str(template["type"]),
                hook=str(template["hook"]),
                packet_features=list(template["packet_features"]),
                facts=list(template.get("facts") or ()),
                body=body,
                expected_behavior=str(template["expected_behavior"]),
                target_polarity=str(template["target_polarity"]),
                cycle=int(cycle),
                failure_family=family,
                failure_cases=list(family_info.get("failure_cases") or []),
            )
            proposals.append(candidate)
            notes.append({
                "intervention_id": candidate.get("intervention_id"),
                "family": family,
                "template": template.get("name"),
                "quality": template.get("quality"),
                "frozen_judge_replay_compatible": str(
                    candidate.get("hook") or ""
                ) != "judge.system",
            })
            return True

        # Joint-first pass: when one family has both an L1 template and a
        # decision_guard, and the proposal budget can hold both, emit them
        # immediately as the M3 joint pair. Otherwise the diversity pass
        # below would spend the whole budget on other families and starve
        # the guard.
        joint_family_index: int | None = None
        if max_proposals - len(proposals) >= 2:
            for index, (family_info, templates) in enumerate(family_template_lists):
                l1 = next(
                    (
                        template for template in templates
                        if str(template.get("hook") or "") in _L1_HOOKS
                    ),
                    None,
                )
                l2 = next(
                    (
                        template for template in templates
                        if template.get("type") == "decision_guard"
                    ),
                    None,
                )
                if l1 is not None and l2 is not None:
                    if add_template(family_info, l1) and add_template(family_info, l2):
                        joint_family_index = index
                        break

        # Pass 1: one best template per remaining failure family, so a mixed
        # FN/FP cycle can still form a joint L1+L2 proposal instead of being
        # starved by the largest family.
        for index, (family_info, templates) in enumerate(family_template_lists):
            if index == joint_family_index:
                continue
            for template in templates:
                if add_template(family_info, template):
                    break
        # Pass 2: fill the remaining proposal budget, prioritizing families
        # that still have a decision_guard template so mixed FN/FP cycles can
        # form a joint L1+L2 proposal instead of being starved by more L1
        # boundaries.
        fill_lists = sorted(
            family_template_lists,
            key=lambda item: (
                not any(
                    template.get("type") == "decision_guard"
                    for template in item[1]
                ),
                -int((item[0].get("count") or 0)),
            ),
        )
        for index, (family_info, templates) in enumerate(fill_lists):
            if index == joint_family_index:
                continue
            for template in templates:
                add_template(family_info, template)

        # M3 joint path: when the same cycle drafts at least one L1 candidate
        # and one L2 decision_guard, they are only admitted together if the
        # joint configuration passes M (never packaged separately).
        joint_groups: list[list[str]] = []
        l1 = next(
            (
                c for c in proposals
                if str(c.get("hook") or "") in _L1_HOOKS
            ),
            None,
        )
        l2 = next(
            (
                c for c in proposals
                if c.get("type") == "decision_guard"
            ),
            None,
        )
        if l1 is not None and l2 is not None:
            joint_groups.append([
                str(l1.get("intervention_id") or ""),
                str(l2.get("intervention_id") or ""),
            ])
        return ProposalBatch(
            candidates=proposals,
            joint_groups=joint_groups,
            notes=notes,
        )


_LLM_DRAFT_SYSTEM = """\
You draft self-evolution interventions for a hallucination detector.

Return ONLY a JSON object:
{"interventions": [ ... ]}

Each intervention must obey this exact schema:
{
  "intervention_id": "IV-<cycle>-<seq>",
  "type": "evidence_check | text_boundary | decision_guard",
  "hook": "judge.system | judge.post | scanner.system | upgrade.pre | upgrade.system | tracer.system | defense.pre | defense.system | adjudicate.guard",
  "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
  "body": {
    "directive": "...", "not_when": "...",
    "predicate": "required_action_present", "runner": "deterministic",
    "output_verdicts": ["satisfied"], "action": "...", "action_not_when": "..."
  },
  "claim": {"expected_behavior": "...", "target_polarity": "recall | precision | boundary"},
  "provenance": {"cycle": 1, "failure_family": "...", "failure_cases": ["...", "..."]}
}

Rules: only executable hooks/actions; text_boundary needs directive + not_when;
decision_guard facts may only use the closed fact vocabulary; no case ids in
directive/body/claim text; at most three interventions.
"""


class LLMProposer:
    name = "llm"

    def __init__(self, framework_config: Mapping[str, Any]) -> None:
        detector_config = load_detector_config(
            resolve_path(framework_config.get("detector_config"))
        )
        self.route = dict(detector_config["judge_route"])
        self.route["_route_name"] = "evolution_proposer"

    def propose(
        self,
        families: Sequence[Mapping[str, Any]],
        *,
        cycle: int,
        max_proposals: int = 3,
    ) -> ProposalBatch:
        if not families:
            return ProposalBatch()
        family_block = json.dumps(
            [
                {
                    "failure_family": f.get("failure_family"),
                    "count": f.get("count"),
                    "sources": f.get("sources"),
                }
                for f in families[:5]
            ],
            ensure_ascii=False,
        )
        user = (
            f"Cycle {cycle}. Stable bare-base failure families:\n"
            f"{family_block}\n\nDraft at most {max_proposals} interventions."
        )
        try:
            parsed, _meta = provider_call(
                route=self.route,
                system=_LLM_DRAFT_SYSTEM,
                user=user,
                purpose="evolution_proposer",
            )
        except ProviderError:
            return ProposalBatch()
        if isinstance(parsed, list):
            raw: Any = parsed
        elif isinstance(parsed, Mapping):
            raw = parsed.get("interventions")
        else:
            return ProposalBatch()
        if not isinstance(raw, list):
            return ProposalBatch()
        proposals: list[dict[str, Any]] = []
        for index, item in enumerate(raw[: max_proposals], start=1):
            if not isinstance(item, Mapping):
                continue
            candidate = dict(item)
            candidate.setdefault("schema_version", "detector_v2.intervention.v1")
            if not candidate.get("intervention_id"):
                candidate["intervention_id"] = f"IV-{cycle}-{index:03d}"
            provenance = candidate.setdefault("provenance", {})
            provenance.setdefault("cycle", cycle)
            try:
                from detector_v2.interventions import validate_intervention
                validate_intervention(candidate)
            except ValueError:
                continue
            proposals.append(candidate)
        return ProposalBatch(candidates=proposals, joint_groups=[])


def make_proposer(
    config: Mapping[str, Any],
    mode: str | None = None,
) -> TemplateProposer | LLMProposer:
    mode = mode or str((config.get("proposer") or {}).get("mode") or "template")
    if mode == "llm":
        return LLMProposer(config)
    return TemplateProposer()


__all__ = ["LLMProposer", "ProposalBatch", "TemplateProposer", "make_proposer"]
