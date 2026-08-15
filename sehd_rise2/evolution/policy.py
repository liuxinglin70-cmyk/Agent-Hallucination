"""L2 policy candidate layer: the Phase 1-A extraction contract.

Phase 1-A turns the twelve policy-gap false positives of the Phase 0
dataset into the first L2 Policy Candidate Set, in three layers:

    failure case (learning case) -> policy hypothesis -> candidate policy

A candidate policy adjusts the INTERPRETATION of adjudication evidence —
which certificates may convict, and under what corroboration. It never
produces a verdict of its own, never edits evidence, and never patches a
single case: a trigger is a certificate class from the closed vocabulary
below, and every policy must be supported by at least two failure cases.

Vocabulary governance mirrors the frozen gap taxonomy: the classes and
actions are code constants (single source of truth); extending them is a
code review backed by dataset evidence, never a runtime proposal.

CLI:

    python -m sehd_rise2.evolution.policy validate \
        --file datasets/policy_candidates/v1/policy_candidates.json \
        --cases datasets/learning_cases/v1/cases
    python -m sehd_rise2.evolution.policy report \
        --file datasets/policy_candidates/v1/policy_candidates.json \
        --cases datasets/learning_cases/v1/cases \
        --out datasets/policy_candidates/v1/REPORT.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterator, Mapping

from sehd_rise2.evolution.gap_taxonomy import GAP_CATEGORIES

POLICY_CANDIDATES_SCHEMA = "sehd2.policy_candidates.v1"

# Conviction-basis typing of a surviving certificate, Phase 1-C.5
# discriminative form: every definition is a boundary, not a description —
# ``CONVICTS WHEN`` is the test the certificate must pass, ``NOT WHEN``
# redirects the confusable neighbour. ``content_contradiction`` is the
# baseline residual: it carries no policy and binds only after every
# specific class has failed its test; the declaration order mirrors
# that procedure.
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
    "content_contradiction": (
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

POLICY_STATUSES = frozenset({"candidate", "replayed", "promoted", "rejected"})

FINDING_STATUSES = frozenset({"non_separable_under_current_representation"})

POLICY_GENOME_SCHEMA = "sehd2.policy_genome.v1"
POLICY_ITEM_VERSION = "v1"

POLICY_GAP_CATEGORY = "policy"
CASE_ID_IN_TEXT = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*/\d+\.json\b")


def load_case_facts(cases_dir: Path) -> dict[str, dict[str, Any]]:
    """Read the learning case tree into case_id -> polarity facts."""
    facts: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(cases_dir).glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case_id = str(case.get("case_id") or "")
        if not case_id:
            continue
        ground = case.get("ground_truth") or {}
        execution = case.get("detector_execution") or {}
        capability = case.get("capability_analysis") or {}
        gap = capability.get("capability_gap") or {}
        facts[case_id] = {
            "failure_type": (
                case.get("failure_analysis") or {}
            ).get("failure_type"),
            "gold": ground.get("hallucination_label"),
            "decision": execution.get("final_decision"),
            "gap_category": gap.get("category"),
        }
    return facts


def policy_fp_case_ids(case_facts: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """The false positives a policy candidate set must account for."""
    return {
        case_id
        for case_id, facts in case_facts.items()
        if facts["failure_type"] == "FP"
        and facts["gap_category"] == POLICY_GAP_CATEGORY
    }


def _iter_text(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_text(item)


def _text_case_ids(value: Any) -> set[str]:
    found: set[str] = set()
    for text in _iter_text(value):
        found.update(CASE_ID_IN_TEXT.findall(text))
    return found


def _policies(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    policies = payload.get("policies")
    return [
        item for item in policies if isinstance(item, Mapping)
    ] if isinstance(policies, list) else []


def policies_of(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Public reader for the policy list of one candidate set."""
    return _policies(payload)


def _trigger_definition(policy: Mapping[str, Any]) -> str:
    trigger = policy.get("trigger")
    trigger = trigger if isinstance(trigger, Mapping) else {}
    return str(trigger.get("definition") or "")


def _fp_targets(policy: Mapping[str, Any]) -> list[str]:
    effect = policy.get("expected_effect")
    effect = effect if isinstance(effect, Mapping) else {}
    targets = effect.get("fp_cases")
    return [str(item) for item in targets] if isinstance(targets, list) else []


def _tp_examples(policy: Mapping[str, Any]) -> list[str]:
    effect = policy.get("expected_effect")
    effect = effect if isinstance(effect, Mapping) else {}
    examples = effect.get("tp_must_survive")
    if not isinstance(examples, list):
        return []
    return [
        str(item.get("case_id"))
        for item in examples
        if isinstance(item, Mapping)
    ]


def _unaddressed(payload: Mapping[str, Any]) -> set[str]:
    entries = payload.get("unaddressed_cases")
    if not isinstance(entries, list):
        return set()
    return {
        str(entry.get("case_id"))
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("case_id")
    }


def validate_policy_set(
    payload: Mapping[str, Any],
    case_facts: Mapping[str, Mapping[str, Any]],
) -> dict[str, bool]:
    """Validate one candidate policy set; all checks must pass.

    ``case_facts`` comes from ``load_case_facts`` over the assembled
    learning case tree; every case reference inside the payload must
    resolve against it.
    """
    policies = _policies(payload)
    fp_ids = policy_fp_case_ids(case_facts)
    policy_gap_names = set(GAP_CATEGORIES[POLICY_GAP_CATEGORY])

    evidence_case_ok = True
    multi_case_ok = True
    classes_ok = True
    actions_ok = True
    statuses_ok = True
    gap_names_ok = True
    estimate_ok = True
    fn_risk_ok = True
    boundary_ok = True
    trigger_level_ok = True
    tp_ok = True
    all_gap_names: list[Any] = []
    for policy in policies:
        hypothesis = policy.get("hypothesis")
        hypothesis = hypothesis if isinstance(hypothesis, Mapping) else {}
        evidence = hypothesis.get("case_evidence")
        evidence = evidence if isinstance(evidence, list) else []
        evidence_ids = {
            str(item.get("case_id"))
            for item in evidence
            if isinstance(item, Mapping) and item.get("case_id")
        }
        evidence_case_ok &= evidence_ids <= fp_ids
        multi_case_ok &= len(evidence_ids) >= 2
        trigger = policy.get("trigger")
        trigger = trigger if isinstance(trigger, Mapping) else {}
        classes_ok &= (
            str(trigger.get("certificate_class")) in CERTIFICATE_CLASSES
        )
        actions_ok &= str(policy.get("action")) in POLICY_ACTIONS
        statuses_ok &= str(policy.get("status")) in POLICY_STATUSES
        source_names = hypothesis.get("source_gap_names")
        source_names = source_names if isinstance(source_names, list) else []
        all_gap_names.extend(source_names)
        effect = policy.get("expected_effect")
        effect = effect if isinstance(effect, Mapping) else {}
        estimate_ok &= (
            effect.get("estimated_fp_reduction") == len(_fp_targets(policy))
        )
        fn_risk_ok &= bool(str(effect.get("fn_risk") or ""))
        boundary = policy.get("risk_boundary")
        boundary = boundary if isinstance(boundary, Mapping) else {}
        do_not_apply = boundary.get("do_not_apply")
        boundary_ok &= bool(str(boundary.get("statement") or "")) and (
            isinstance(do_not_apply, list) and len(do_not_apply) > 0
        )
        trigger_level_ok &= not (
            CASE_ID_IN_TEXT.search(_trigger_definition(policy))
        )
        for case_id in _tp_examples(policy):
            facts = case_facts.get(case_id)
            tp_ok &= bool(
                facts
                and facts["gold"] is True
                and facts["decision"] == "verified_error"
            )

    fp_targets = {
        case_id for policy in policies for case_id in _fp_targets(policy)
    }
    targets_ok = fp_targets <= fp_ids
    findings = payload.get("non_separable_findings")
    findings = findings if isinstance(findings, list) else []
    abstained = {
        str(case_id)
        for finding in findings
        if isinstance(finding, Mapping)
        for case_id in finding.get("evidence_cases") or []
    }
    covered = fp_targets | _unaddressed(payload) | abstained
    references = _text_case_ids(payload)
    findings_known = True
    findings_multi_case = True
    findings_span_polarities = True
    findings_status_ok = True
    for finding in findings:
        if not isinstance(finding, Mapping):
            findings_known = False
            continue
        cases = finding.get("evidence_cases")
        cases = cases if isinstance(cases, list) else []
        findings_known &= all(
            str(case_id) in case_facts for case_id in cases
        )
        findings_multi_case &= len(set(map(str, cases))) >= 2
        polarities = {
            case_facts[str(case_id)]["gold"]
            for case_id in cases
            if str(case_id) in case_facts
        }
        findings_span_polarities &= polarities == {True, False}
        findings_status_ok &= (
            str(finding.get("status")) in FINDING_STATUSES
        )
    return {
        "schema_version_known": (
            payload.get("schema_version") == POLICY_CANDIDATES_SCHEMA
        ),
        "policies_present": bool(policies),
        "policy_ids_unique": len({
            str(policy.get("policy_id")) for policy in policies
        }) == len(policies),
        "classes_in_vocabulary": classes_ok,
        "actions_in_vocabulary": actions_ok,
        "statuses_in_vocabulary": statuses_ok,
        "gap_names_from_policy_category": (
            all(str(name) in policy_gap_names for name in all_gap_names)
        ),
        "evidence_cases_are_policy_fp": evidence_case_ok,
        "multi_case_support": multi_case_ok,
        "fp_targets_are_policy_fp": targets_ok,
        "fp_estimates_consistent": estimate_ok,
        "fn_risk_stated": fn_risk_ok,
        "risk_boundary_stated": boundary_ok,
        "trigger_is_class_level": trigger_level_ok,
        "referenced_cases_known": references <= set(case_facts),
        "fp_coverage_complete": fp_ids <= covered,
        "tp_examples_convicted": tp_ok,
        "findings_reference_known": findings_known,
        "findings_multi_case": findings_multi_case,
        "findings_span_polarities": findings_span_polarities,
        "findings_status_vocabulary": findings_status_ok,
    }


def _genome_hash(payload: Mapping[str, Any]) -> str:
    """Canonical sha256, mirroring ``detector.skills.load_genome_payload``.

    Same canonicalization (sorted keys, compact separators, utf-8 text)
    so a genome built here loads through the detector's fail-closed
    reader without any adapter.
    """
    hashable = {
        key: value for key, value in payload.items()
        if key != "content_hash"
    }
    canonical = json.dumps(
        hashable, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_policy_genome(
    payload: Mapping[str, Any], include_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Freeze the replay-gated policies into the deployable genome.

    The genome is the runtime contract: one item per policy with the
    closed action vocabulary — no verdict rewriting, no free-form
    rules. ``include_ids`` selects the promotion candidate subset
    (default: every policy that passed the oracle replay gate, i.e.
    status ``replayed``).
    """
    policies = _policies(payload)
    if include_ids is None:
        selected = [
            policy for policy in policies
            if policy.get("status") == "replayed"
        ]
    else:
        by_id = {
            str(policy.get("policy_id")): policy for policy in policies
        }
        unknown = [pid for pid in include_ids if pid not in by_id]
        if unknown:
            raise ValueError(f"unknown policy ids: {unknown}")
        selected = [by_id[pid] for pid in include_ids]
    items = []
    for policy in selected:
        trigger = policy.get("trigger") or {}
        boundary = policy.get("risk_boundary") or {}
        hypothesis = policy.get("hypothesis") or {}
        items.append({
            "policy_id": str(policy.get("policy_id")),
            "version": POLICY_ITEM_VERSION,
            "target_certificate_class": str(
                trigger.get("certificate_class")
            ),
            "precondition": str(trigger.get("definition") or ""),
            "exclusions": list(trigger.get("must_not_match") or []),
            "action": str(policy.get("action")),
            "risk_boundary": {
                "statement": str(boundary.get("statement") or ""),
                "do_not_apply": list(
                    boundary.get("do_not_apply") or []
                ),
            },
            "provenance": {
                "derived_from": dict(payload.get("derived_from") or {}),
                "source_gap_names": list(
                    hypothesis.get("source_gap_names") or []
                ),
                "failure_cases": sorted({
                    str(entry.get("case_id"))
                    for entry in hypothesis.get("case_evidence") or []
                    if isinstance(entry, Mapping)
                }),
                "candidate_set_status": str(payload.get("review_status")),
            },
        })
    genome: dict[str, Any] = {
        "schema_version": POLICY_GENOME_SCHEMA,
        "items": items,
    }
    genome["content_hash"] = _genome_hash(genome)
    return genome


def build_report(
    payload: Mapping[str, Any],
    case_facts: Mapping[str, Mapping[str, Any]],
) -> str:
    """Render the candidate set as the review-facing markdown report."""
    fp_ids = sorted(policy_fp_case_ids(case_facts))
    policies = _policies(payload)
    lines = [
        "# Phase 1-A L2 Policy Candidate Set", "",
        f"- schema: {payload.get('schema_version')}",
        f"- review status: {payload.get('review_status')}",
        f"- policies: {len(policies)}",
        f"- policy-gap false positives to account for: {len(fp_ids)}",
        "", "## Policies", "",
    ]
    for policy in policies:
        hypothesis = policy.get("hypothesis") or {}
        trigger = policy.get("trigger") or {}
        effect = policy.get("expected_effect") or {}
        boundary = policy.get("risk_boundary") or {}
        lines.append(f"### {policy.get('policy_id')} — {policy.get('title')}")
        lines += [
            "",
            f"- certificate class: `{trigger.get('certificate_class')}`",
            f"- action: `{policy.get('action')}`",
            f"- gap names aggregated: "
            f"{', '.join(hypothesis.get('source_gap_names') or [])}",
            f"- failure cases: "
            f"{len(hypothesis.get('case_evidence') or [])}",
            f"- expected FP reduction: "
            f"{effect.get('estimated_fp_reduction')}",
            "",
            hypothesis.get("statement") or "",
            "",
            f"Trigger: {trigger.get('definition')}",
            "",
            f"Risk boundary: {boundary.get('statement')}",
            "",
        ]
    lines += ["## FP coverage matrix", ""]
    header = "| case | " + " | ".join(
        str(policy.get("policy_id")) for policy in policies
    ) + " |"
    lines.append(header)
    lines.append("|---" * (len(policies) + 1) + "|")
    for case_id in fp_ids:
        marks = [
            "x" if case_id in _fp_targets(policy) else ""
            for policy in policies
        ]
        lines.append(f"| {case_id} | " + " | ".join(marks) + " |")
    covered = {
        case_id for policy in policies for case_id in _fp_targets(policy)
    }
    lines += [
        "",
        f"- covered by at least one policy: {len(covered)}/{len(fp_ids)}",
        f"- ceiling if every trigger fires and no FN/TP regresses: "
        f"{len(covered)} FP removed",
        "",
        "## Replay entry checklist (P1-B)", "",
        "1. certificate-class binding for every surviving certificate in "
        "the 100-case run tree, with audit;",
        "2. deterministic replay of adjudication under each policy alone "
        "and the set together;",
        "3. report FP delta, FN delta, TP retention and decision "
        "stability per policy;",
        "4. promotion requires net accuracy gain, no FN regression beyond "
        "the stated risk boundaries, and generalization beyond the "
        "deriving cases.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser(
        "validate", help="validate a candidate policy set",
    )
    validate_parser.add_argument("--file", type=Path, required=True)
    validate_parser.add_argument("--cases", type=Path, required=True)
    report_parser = sub.add_parser(
        "report", help="render the review-facing markdown report",
    )
    report_parser.add_argument("--file", type=Path, required=True)
    report_parser.add_argument("--cases", type=Path, required=True)
    report_parser.add_argument("--out", type=Path, required=True)
    genome_parser = sub.add_parser(
        "genome", help="freeze replay-gated policies into a policy genome",
    )
    genome_parser.add_argument("--file", type=Path, required=True)
    genome_parser.add_argument(
        "--include", default=None,
        help="comma-separated policy ids (default: all status=replayed)",
    )
    genome_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "genome":
        payload = json.loads(args.file.read_text(encoding="utf-8"))
        include = (
            [item.strip() for item in args.include.split(",")]
            if args.include else None
        )
        genome = build_policy_genome(payload, include)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(genome, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        ids = [item["policy_id"] for item in genome["items"]]
        print(
            f"genome written -> {args.out} "
            f"({len(ids)} items: {', '.join(ids)}; "
            f"hash {genome['content_hash'][:12]}...)"
        )
        return 0
    payload = json.loads(args.file.read_text(encoding="utf-8"))
    case_facts = load_case_facts(args.cases)
    if not case_facts:
        print(f"no learning cases found under {args.cases}")
        return 1
    if args.command == "validate":
        checks = validate_policy_set(payload, case_facts)
        for name, passed in checks.items():
            print(f"{'ok  ' if passed else 'FAIL'} {name}")
        return 0 if all(checks.values()) else 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        build_report(payload, case_facts), encoding="utf-8",
    )
    print(f"report written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
