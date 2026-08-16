"""Phase 2 policy-proposal generation: the hypothesis-search step.

One provider call turns a cycle's failure worksheet into policy
proposals (new / amend / retire). The model only PROPOSES — every
structural guarantee is enforced mechanically afterwards by
``validate_proposals`` against the closed vocabularies and the
worksheet facts, mirroring the Phase 1-A discipline:

- a policy is mechanism-level: at least two distinct false positives
  support it, its trigger is a certificate class (never a case id) and
  its action is withholding or corroboration — the policy layer can
  only suppress convictions, so false negatives enter the worksheet as
  recorded context and can never be a proposal target;
- every new or amended policy declares its risk: a false-negative
  risk statement, do-not-apply boundaries and true-positive sentinels
  that must stay convicted.

CLI (worksheet in, validated proposals out):

    python -m sehd_rise2.evolution.proposal generate \
        --worksheet datasets/policy_library/v1/cycles/c1_worksheet.json \
        --registry datasets/policy_library/v1/artifacts/registry_v1.json \
        --out datasets/policy_library/v1/cycles/c1_proposals.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.artifacts import (
    CERTIFICATE_CLASSES,
    POLICY_ACTIONS,
    POLICY_FREE_CLASSES,
    load_artifact_registry,
)
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.provider import ProviderError, provider_call
from sehd_rise2.evolution.gap_taxonomy import GAP_CATEGORIES
from sehd_rise2.evolution.convention_evolution import attribute_miss

PROPOSAL_SCHEMA = "sehd2.policy_proposals.v1"
POLICY_GAP_NAMES = frozenset(GAP_CATEGORIES["policy"])
CASE_ID_IN_TEXT = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*/\d+\.json\b")
MIN_SUPPORT_CASES = 2

PROPOSAL_SYSTEM = (
    "You are the policy-evolution proposer of a hallucination "
    "detector's L2 layer. You receive this cycle's failure worksheet: "
    "wrongful convictions (gold-clean cases the detector convicted, "
    "with every surviving certificate, its class, claim and rationale), "
    "true-positive sentinels (gold-error cases currently convicted — a "
    "policy must never acquit them), and recorded misses (gold-error "
    "cases the detector acquitted, each with its mechanical FN-triage "
    "attribution — NOT actionable here: the policy layer can only "
    "withhold convictions, never create them).\n\n"
    "Current deployed policies follow the worksheet.\n\n"
    "Propose ONLY mechanism-level adjustments:\n"
    "1. 'new' — a withheld conviction pattern supported by at least two "
    "DISTINCT wrongful convictions sharing one mechanism;\n"
    "2. 'amend' — tighten or correct an existing policy's boundary when "
    "its mechanism proved wrong or too narrow on this worksheet;\n"
    "3. 'retire' — remove a policy that misfires (acquits sentinels) or "
    "never acts.\n\n"
    "Hard rules:\n"
    "- trigger: exactly one certificate class from the vocabulary, plus "
    "a class-level definition sentence (never a case id, never a case "
    "list) and optional must-not-match exclusions;\n"
    "- action: withhold_conviction or require_corroborating_content_"
    "error only;\n"
    "- every new/amended policy declares fn_risk, do_not_apply "
    "boundaries and at least one tp_must_survive sentinel from the "
    "worksheet;\n"
    "- fp_cases: only worksheet wrongful convictions whose surviving "
    "certificates include your target class;\n"
    "- source_gap_names: from ['" + "', '".join(sorted(POLICY_GAP_NAMES))
    + "'] only;\n"
    "- if no mechanism meets the bar, return an empty proposals list — "
    "that is a valid, expected answer.\n\n"
    "Classes (boundary definitions):\n"
    + "\n".join(
        f"- {name}: {definition}"
        for name, definition in CERTIFICATE_CLASSES.items()
    )
    + "\n\nReply with ONLY a JSON object of the exact shape:\n"
    '{"proposals": [{"kind": "new"|"amend"|"retire", "policy_id": '
    '"<new unique id like L2-P6, or the existing id for amend/retire>", '
    '"title": "<short>", "target_certificate_class": "<class>", '
    '"trigger": {"definition": "<class-level sentence>", '
    '"must_not_match": ["<exclusion>", "..."]}, "action": '
    '"<withhold_conviction|require_corroborating_content_error>", '
    '"hypothesis": {"statement": "<mechanism>", "case_evidence": '
    '[{"case_id": "<worksheet fp case>", "observation": "<what the '
    'certificates did>", "policy_fit": "<why the mechanism covers it>"}], '
    '"source_gap_names": ["<gap name>"]}, "expected_effect": {"fp_cases": '
    '["<worksheet fp case>", "..."], "fn_risk": "<what acquitting '
    'wrongly would cost>", "tp_must_survive": [{"case_id": "<worksheet '
    'sentinel>", "why": "<why it must stay convicted>"}]}, '
    '"risk_boundary": {"statement": "<boundary>", "do_not_apply": '
    '["<case-pattern>", "..."]}}]}\n'
    "For 'retire' entries only kind, policy_id, title and "
    "hypothesis.case_evidence are required."
)


def load_registry_artifacts(path: Path) -> list[dict[str, Any]]:
    """Load one persisted artifact registry; returns its artifact list."""
    return load_artifact_registry(Path(path))["artifacts"]


def _surviving_entries(
    run_tree: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[tuple[str, str], Mapping[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """case_id -> surviving certificate entries with class + claim."""
    from sehd_rise2.evolution.replay import surviving_ids

    entries: dict[str, list[dict[str, str]]] = {}
    for case_id in sorted(run_tree):
        result = run_tree[case_id]["result"]
        proof = result.get("proof_adjudication") or {}
        claims = {
            str(candidate.get("candidate_id")): str(
                candidate.get("claim") or ""
            )
            for candidate in proof.get("census") or []
        }
        rationales = {
            str(cert.get("candidate_id")): str(
                cert.get("rationale") or ""
            )
            for cert in proof.get("certificates") or []
        }
        rows = []
        for candidate_id in surviving_ids(result):
            key = (case_id, candidate_id)
            if key not in bindings:
                continue
            rows.append({
                "candidate_id": candidate_id,
                "certificate_class": bindings[key]["certificate_class"],
                "candidate_claim": claims.get(candidate_id, ""),
                "certificate_rationale": rationales.get(candidate_id, ""),
            })
        entries[case_id] = rows
    return entries


def build_worksheet(
    *,
    run_tree: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[tuple[str, str], Mapping[str, str]],
    genome_artifacts: list[Mapping[str, Any]],
    source: str,
) -> dict[str, Any]:
    """The cycle's evaluator-side failure view handed to the proposer.

    Gold polarity is evaluator knowledge (this module lives on the
    evolution side, never in the detector path); the certificate fields
    themselves are detector products.
    """
    surviving = _surviving_entries(run_tree, bindings)
    wrongful, sentinels, misses = [], [], []
    for case_id in sorted(run_tree):
        gold = run_tree[case_id]["gold"]
        state = run_tree[case_id]["result"].get("state")
        if not gold and state == "verified_error":
            wrongful.append({
                "case_id": case_id,
                "surviving_certificates": surviving.get(case_id, []),
            })
        elif gold and state == "verified_error":
            sentinels.append({
                "case_id": case_id,
                "surviving_certificates": surviving.get(case_id, []),
            })
        elif gold and state == "verified_clean":
            misses.append({
                "case_id": case_id,
                "attribution": attribute_miss(
                    run_tree[case_id]["result"],
                ),
            })
    return {
        "schema_version": "sehd2.policy_worksheet.v1",
        "source": source,
        "wrongful_convictions": wrongful,
        "tp_sentinels": sentinels,
        "recorded_misses": misses,
        "deployed_policies": [
            {
                "policy_id": artifact.get("artifact_id"),
                "target_certificate_class": (
                    artifact.get("trigger") or {}
                ).get("certificate_class"),
                "precondition": (
                    artifact.get("intervention") or {}
                ).get("exclusions"),
                "action": (
                    artifact.get("intervention") or {}
                ).get("action"),
                "risk_boundary": artifact.get("risk_boundary"),
            }
            for artifact in genome_artifacts
        ],
    }


def worksheet_facts(
    worksheet: Mapping[str, Any],
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """(wrongful ids, sentinel ids, wrongful id -> surviving classes)."""
    wrongful = {
        str(row["case_id"])
        for row in worksheet.get("wrongful_convictions") or []
    }
    sentinels = {
        str(row["case_id"]) for row in worksheet.get("tp_sentinels") or []
    }
    classes_of: dict[str, set[str]] = {}
    for row in worksheet.get("wrongful_convictions") or []:
        classes_of[str(row["case_id"])] = {
            str(entry.get("certificate_class"))
            for entry in row.get("surviving_certificates") or []
        }
    return wrongful, sentinels, classes_of


def validate_proposals(
    proposals: list[Mapping[str, Any]],
    *,
    worksheet: Mapping[str, Any],
    registry_artifacts: list[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Mechanical gate per proposal; returns rejection records.

    A proposal that survives every check returns no record. Checks are
    the Phase 1-A discipline restated against worksheet facts.
    """
    wrongful, sentinels, classes_of = worksheet_facts(worksheet)
    known_ids = {
        str(artifact.get("artifact_id"))
        for artifact in registry_artifacts
    }
    rejections: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for proposal in proposals:
        policy_id = str(proposal.get("policy_id") or "")

        def reject(reason: str) -> None:
            rejections.append({
                "policy_id": policy_id, "reason": reason,
            })

        if policy_id in seen_ids:
            reject("duplicate policy_id in one proposal batch")
            continue
        seen_ids.add(policy_id)
        kind = str(proposal.get("kind") or "")
        if kind not in {"new", "amend", "retire"}:
            reject(f"unknown kind {kind!r}")
            continue
        hypothesis = proposal.get("hypothesis") or {}
        evidence = hypothesis.get("case_evidence") or []
        evidence_ids = {
            str(entry.get("case_id")) for entry in evidence
            if isinstance(entry, Mapping) and entry.get("case_id")
        }
        if kind == "retire":
            if policy_id not in known_ids:
                reject("retire targets an unknown policy")
            elif len(evidence_ids) < 1:
                reject("retire without evidence case")
            elif not evidence_ids <= (wrongful | sentinels):
                reject("retire evidence outside the worksheet")
            continue
        if kind == "new" and policy_id in known_ids:
            reject("new policy collides with a deployed id")
        if kind == "amend" and policy_id not in known_ids:
            reject("amend targets an unknown policy")
        target = str(proposal.get("target_certificate_class") or "")
        if target not in CERTIFICATE_CLASSES:
            reject(f"class {target!r} outside the vocabulary")
            continue
        if target in POLICY_FREE_CLASSES:
            reject(f"class {target!r} is policy-free by design")
            continue
        action = str(proposal.get("action") or "")
        if action not in POLICY_ACTIONS:
            reject(f"action {action!r} outside the vocabulary")
            continue
        trigger = proposal.get("trigger") or {}
        definition = str(trigger.get("definition") or "")
        if not definition:
            reject("trigger without a definition")
            continue
        if CASE_ID_IN_TEXT.search(definition):
            reject("trigger definition contains a case id")
            continue
        if not evidence_ids <= wrongful:
            reject("evidence case outside the wrongful convictions")
            continue
        if len(evidence_ids) < MIN_SUPPORT_CASES:
            reject(
                f"mechanism supported by {len(evidence_ids)} case(s), "
                f"needs {MIN_SUPPORT_CASES}",
            )
            continue
        effect = proposal.get("expected_effect") or {}
        fp_cases = {
            str(case) for case in effect.get("fp_cases") or []
        }
        if not fp_cases:
            reject("no fp_cases declared")
            continue
        if not fp_cases <= wrongful:
            reject("fp_cases outside the wrongful convictions")
            continue
        unarmed = sorted(
            case for case in fp_cases if target not in classes_of.get(
                case, set(),
            )
        )
        if unarmed:
            reject(
                f"fp_cases without a surviving {target} certificate: "
                f"{', '.join(unarmed)}"
            )
            continue
        if not str(effect.get("fn_risk") or ""):
            reject("fn_risk not stated")
            continue
        must_survive = {
            str(entry.get("case_id"))
            for entry in effect.get("tp_must_survive") or []
            if isinstance(entry, Mapping)
        }
        if not must_survive:
            reject("no tp_must_survive sentinel declared")
            continue
        if not must_survive <= sentinels:
            reject("tp_must_survive outside the worksheet sentinels")
            continue
        boundary = proposal.get("risk_boundary") or {}
        do_not_apply = boundary.get("do_not_apply")
        if not str(boundary.get("statement") or ""):
            reject("risk_boundary.statement missing")
            continue
        if not isinstance(do_not_apply, list) or not do_not_apply:
            reject("risk_boundary.do_not_apply missing")
            continue
        source_names = hypothesis.get("source_gap_names") or []
        if not source_names or not set(
            str(name) for name in source_names
        ) <= POLICY_GAP_NAMES:
            reject("source_gap_names outside the policy gap vocabulary")
    return rejections


def generate_proposals(
    *, worksheet: Mapping[str, Any], registry_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One provider call; validated proposals and the call meta."""
    registry_artifacts = load_registry_artifacts(registry_path)
    user = json.dumps(worksheet, ensure_ascii=False, indent=1)
    config = load_config()
    route = dict(config["verification_route"])
    route["model"] = os.environ.get(
        "SEHD_MUTATION_MODEL", "deepseek-v4-pro",
    )
    route["_route_name"] = "proposal"
    parsed, meta = provider_call(
        route=route, system=PROPOSAL_SYSTEM, user=user,
        purpose="policy_proposal",
    )
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("proposals"), list,
    ):
        raise ProviderError(
            "proposal", "answer is not a proposals object",
            error_type="contract",
        )
    proposals = [
        item for item in parsed["proposals"]
        if isinstance(item, Mapping)
    ]
    rejections = validate_proposals(
        proposals, worksheet=worksheet,
        registry_artifacts=registry_artifacts,
    )
    rejected_ids = {
        record["policy_id"] for record in rejections
    }
    accepted = [
        dict(item) for item in proposals
        if str(item.get("policy_id")) not in rejected_ids
    ]
    return accepted, {
        "model": meta.get("model"),
        "attempts": meta.get("attempts"),
        "raw_proposals": len(proposals),
        "accepted": len(accepted),
        "rejections": rejections,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="worksheet -> proposals")
    gen.add_argument("--worksheet", type=Path, required=True)
    gen.add_argument("--registry", type=Path, required=True)
    gen.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    worksheet = json.loads(
        args.worksheet.read_text(encoding="utf-8"),
    )
    accepted, meta = generate_proposals(
        worksheet=worksheet, registry_path=args.registry,
    )
    payload = {
        "schema_version": PROPOSAL_SCHEMA,
        "worksheet": str(args.worksheet),
        "registry": str(args.registry),
        "proposals": accepted,
        "generation_meta": meta,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"{meta['raw_proposals']} raw -> {meta['accepted']} accepted "
        f"({len(meta['rejections'])} rejected) -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
