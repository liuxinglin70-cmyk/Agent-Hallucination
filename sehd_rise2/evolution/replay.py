"""Phase 1-B offline policy replay: deterministic adjudication re-execution.

Replays ONLY the deterministic adjudication over the recorded run tree:
the census / verification / defense provider outputs are read as recorded,
never re-called — zero provider cost. Policies from the validated
candidate set act on certificate classes (the closed vocabulary in
``sehd_rise2.evolution.policy``) through a binding layer that assigns
every surviving certificate its class with a recorded basis.

Label-free discipline: the binding worksheet exposes only the certificate
claim, its verification rationale and the candidate claim — never the
gold label, never the recorded decision — so class assignment cannot be
steered by outcomes. Bindings are evaluator-side data and must never
enter the detector input path.

Semantics (one case):

    surviving certificate = verification ``supported`` AND defense ``upheld``
    baseline decision     = verified_error iff any certificate survives
    policy action on a class:
      withhold_conviction                -> matching certificates never convict
      require_corroborating_content_error-> matching certificates cannot be
                                            the sole basis; a surviving
                                            content_contradiction certificate
                                            on the case keeps the conviction

The engine fails closed: an incompletely bound run tree, a class outside
the vocabulary, or a baseline that fails to reproduce the recorded
decisions is a validation error, never a silent replay.

CLI:

    python -m sehd_rise2.evolution.replay worksheet \
        --run runs/random100_20260815_142446 --out bindings_worksheet.json
    python -m sehd_rise2.evolution.replay validate \
        --run runs/random100_20260815_142446 \
        --bindings datasets/policy_candidates/v1/certificate_bindings.json
    python -m sehd_rise2.evolution.replay replay \
        --run runs/random100_20260815_142446 \
        --bindings datasets/policy_candidates/v1/certificate_bindings.json \
        --file datasets/policy_candidates/v1/policy_candidates.json \
        --out-dir datasets/policy_candidates/v1
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.evolution.policy import (
    CERTIFICATE_CLASSES,
    POLICY_CANDIDATES_SCHEMA,
    policies_of,
)

BINDINGS_SCHEMA = "sehd2.certificate_bindings.v1"
RUNTIME_BINDINGS_SCHEMA = "sehd2.certificate_bindings.runtime.v1"
BASELINE_CLASS = "content_contradiction"
REPLAY_RESULT_SCHEMA = "sehd2.policy_replay.v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _upheld(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("verdict") == "upheld"
    return value == "upheld"


def surviving_ids(result: Mapping[str, Any]) -> list[str]:
    """Candidate ids of the surviving certificates in one recorded result."""
    proof = result.get("proof_adjudication") or {}
    supported = {
        str(cert.get("candidate_id"))
        for cert in proof.get("certificates") or []
        if cert.get("verdict") == "supported"
    }
    upheld = {
        str(candidate_id)
        for candidate_id, verdict in (
            proof.get("defense_decisions") or {}
        ).items()
        if _upheld(verdict)
    }
    return sorted(supported & upheld, key=lambda cid: int(cid[1:]))


def load_run_tree(run_dir: Path) -> dict[str, dict[str, Any]]:
    """case_id -> {result, gold} from the run ledger join."""
    run_dir = Path(run_dir)
    gold = {
        str(entry["case"]): bool(entry["gold"])
        for entry in _read_json(run_dir / "eval_gold.json")["cases"]
    }
    tree: dict[str, dict[str, Any]] = {}
    for result_path in sorted((run_dir / "cases").glob("*/result.json")):
        result = _read_json(result_path)
        case_id = str(result.get("case_id") or "")
        if case_id and case_id in gold:
            tree[case_id] = {"result": result, "gold": gold[case_id]}
    return tree


def build_worksheet(run_tree: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """Label-free binding worksheet: one row per surviving certificate."""
    rows: list[dict[str, Any]] = []
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
            str(cert.get("candidate_id")): str(cert.get("rationale") or "")
            for cert in proof.get("certificates") or []
        }
        for candidate_id in surviving_ids(result):
            rows.append({
                "case_id": case_id,
                "candidate_id": candidate_id,
                "candidate_claim": claims.get(candidate_id, ""),
                "certificate_rationale": rationales.get(candidate_id, ""),
            })
    return rows


def load_bindings(
    path: Path,
) -> tuple[dict[tuple[str, str], dict[str, str]], dict[str, Any]]:
    payload = _read_json(path)
    bindings: dict[tuple[str, str], dict[str, str]] = {}
    for entry in payload.get("bindings") or []:
        key = (
            str(entry.get("case_id")), str(entry.get("candidate_id")),
        )
        bindings[key] = {
            "certificate_class": str(
                entry.get("certificate_class") or ""
            ),
            "basis": str(entry.get("basis") or ""),
        }
    return bindings, payload


def validate_bindings(
    payload: Mapping[str, Any],
    bindings: Mapping[tuple[str, str], Mapping[str, str]],
    run_tree: Mapping[str, Mapping[str, Any]],
    *, allow_unclassified: bool = False,
) -> dict[str, bool]:
    """Binding-layer validation; all checks must pass before any replay.

    Runtime binder outputs may carry ``unclassified`` (fail-closed
    perception: the policy does not fire, baseline behaviour). Oracle
    bindings must classify every surviving certificate.
    """
    surviving = {
        (case_id, candidate_id)
        for case_id in sorted(run_tree)
        for candidate_id in surviving_ids(run_tree[case_id]["result"])
    }
    accepted = set(CERTIFICATE_CLASSES) | (
        {"unclassified"} if allow_unclassified else set()
    )
    classes_ok = all(
        entry["certificate_class"] in accepted
        for entry in bindings.values()
    )
    basis_ok = all(entry["basis"] for entry in bindings.values())
    return {
        "schema_version_known": (
            payload.get("schema_version") == BINDINGS_SCHEMA
            or payload.get("schema_version") == RUNTIME_BINDINGS_SCHEMA
        ),
        "surviving_fully_bound": set(bindings) == surviving,
        "classes_in_vocabulary": classes_ok,
        "basis_stated": basis_ok,
        "baseline_matches_records": baseline_matches_records(
            run_tree,
            {key: value["certificate_class"] for key, value in
             bindings.items()},
        ),
    }


def baseline_matches_records(
    run_tree: Mapping[str, Mapping[str, Any]],
    classes: Mapping[tuple[str, str], str] | None = None,
) -> bool:
    """The no-policy adjudication rule must reproduce every recorded state.

    When ``classes`` is given the check also runs under each single-policy
    configuration to guard against binding drift; the baseline itself is
    class-independent.
    """
    for entry in run_tree.values():
        result = entry["result"]
        replayed = (
            "verified_error" if surviving_ids(result) else "verified_clean"
        )
        if replayed != result.get("state"):
            return False
    return True


def class_actions(
    policies: list[Mapping[str, Any]],
) -> dict[str, str]:
    """certificate class -> strongest action across the policy list."""
    actions: dict[str, str] = {}
    for policy in policies:
        trigger = policy.get("trigger") or {}
        certificate_class = str(trigger.get("certificate_class"))
        action = str(policy.get("action"))
        current = actions.get(certificate_class)
        if action == "withhold_conviction" or current is None:
            actions[certificate_class] = action
    return actions


def replay_case(
    surviving_classes: list[str], actions: Mapping[str, str],
) -> dict[str, Any]:
    """Deterministic policy adjudication of one case."""
    has_content = BASELINE_CLASS in surviving_classes
    fired: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    convicting = False
    for certificate_class in surviving_classes:
        action = actions.get(certificate_class)
        if action == "withhold_conviction":
            fired.append({
                "certificate_class": certificate_class,
                "action": action,
                "effect": "withheld",
            })
        elif action == "require_corroborating_content_error":
            if has_content:
                skipped.append({
                    "certificate_class": certificate_class,
                    "action": action,
                    "effect": "corroborated",
                })
                convicting = True
            else:
                fired.append({
                    "certificate_class": certificate_class,
                    "action": action,
                    "effect": "sole_basis_removed",
                })
        else:
            convicting = True
    return {
        "state": (
            "verified_error" if convicting else "verified_clean"
        ),
        "policy_evaluation": {"fired": fired, "skipped": skipped},
    }


def evaluate_config(
    *,
    name: str,
    policies: list[Mapping[str, Any]],
    run_tree: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[tuple[str, str], Mapping[str, str]],
    baseline: Mapping[str, str],
) -> dict[str, Any]:
    """Replay one policy configuration and score it against gold."""
    actions = class_actions(policies)
    decisions: dict[str, str] = {}
    audit: dict[str, Any] = {}
    for case_id in sorted(run_tree):
        surviving = surviving_ids(run_tree[case_id]["result"])
        surviving_classes = [
            bindings[(case_id, candidate_id)]["certificate_class"]
            for candidate_id in surviving
        ]
        outcome = replay_case(surviving_classes, actions)
        decisions[case_id] = outcome["state"]
        if outcome["policy_evaluation"]["fired"]:
            audit[case_id] = outcome["policy_evaluation"]
    gold = {
        case_id: run_tree[case_id]["gold"] for case_id in run_tree
    }
    tp = sum(
        1 for case_id in decisions
        if gold[case_id] and decisions[case_id] == "verified_error"
    )
    fp = sum(
        1 for case_id in decisions
        if not gold[case_id] and decisions[case_id] == "verified_error"
    )
    fn = sum(
        1 for case_id in decisions
        if gold[case_id] and decisions[case_id] == "verified_clean"
    )
    tn = sum(
        1 for case_id in decisions
        if not gold[case_id] and decisions[case_id] == "verified_clean"
    )
    changed = sorted(
        case_id for case_id in decisions
        if decisions[case_id] != baseline[case_id]
    )
    fp_fixed = sorted(
        case_id for case_id in changed
        if not gold[case_id]
        and decisions[case_id] == "verified_clean"
    )
    tp_lost = sorted(
        case_id for case_id in changed
        if gold[case_id] and decisions[case_id] == "verified_clean"
    )
    return {
        "configuration": name,
        "policy_ids": [
            str(policy.get("policy_id")) for policy in policies
        ],
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round((tp + tn) / len(decisions), 4),
        "fp_fixed": fp_fixed,
        "tp_lost": tp_lost,
        "decision_changes": len(changed),
        "changed_cases": changed,
        "must_survive_violations": must_survive_violations(
            policies, decisions,
        ),
        "firing_cases": sorted(audit),
        "policy_evaluation": audit,
    }


def must_survive_violations(
    policies: list[Mapping[str, Any]], decisions: Mapping[str, str],
) -> list[dict[str, str]]:
    """Declared tp_must_survive cases that this configuration acquitted."""
    violations: list[dict[str, str]] = []
    for policy in policies:
        effect = policy.get("expected_effect") or {}
        for example in effect.get("tp_must_survive") or []:
            case_id = str(example.get("case_id"))
            if decisions.get(case_id) == "verified_clean":
                violations.append({
                    "policy_id": str(policy.get("policy_id")),
                    "case_id": case_id,
                })
    return violations


def class_exposure(
    run_tree: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[tuple[str, str], Mapping[str, str]],
) -> dict[str, dict[str, int]]:
    """Per class: surviving certificates by gold polarity.

    A policy class holding zero gold-error certificates cannot demonstrate
    TP safety — the overfit tension indicator for the report.
    """
    exposure: dict[str, dict[str, int]] = {
        name: {"gold_error": 0, "gold_clean": 0}
        for name in (*CERTIFICATE_CLASSES, "unclassified")
    }
    for (case_id, _), entry in bindings.items():
        polarity = (
            "gold_error" if run_tree[case_id]["gold"] else "gold_clean"
        )
        exposure.setdefault(
            entry["certificate_class"],
            {"gold_error": 0, "gold_clean": 0},
        )[polarity] += 1
    return exposure


def compare_binding_layers(
    *,
    oracle: Mapping[tuple[str, str], Mapping[str, str]],
    runtime: Mapping[tuple[str, str], Mapping[str, str]],
    oracle_result: Mapping[str, Any],
    runtime_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Oracle vs runtime binding layers over the same certificates.

    P1-C3's headline numbers: does runtime-compatible perception preserve
    the oracle-replay gains?
    """
    keys = set(oracle) | set(runtime)
    unclassified = sorted(
        key for key, entry in runtime.items()
        if entry["certificate_class"] == "unclassified"
    )
    disagreements = sorted(
        {
            key for key in keys
            if oracle.get(key, {}).get("certificate_class")
            != runtime.get(key, {}).get("certificate_class")
        }
    )
    oracle_decisions = {
        config["configuration"]: config
        for config in oracle_result["configurations"]
    }
    runtime_decisions = {
        config["configuration"]: config
        for config in runtime_result["configurations"]
    }
    per_config_delta = []
    for name in sorted(
        set(oracle_decisions) & set(runtime_decisions),
    ):
        oracle_config = oracle_decisions[name]
        runtime_config = runtime_decisions[name]
        per_config_delta.append({
            "configuration": name,
            "oracle_accuracy": oracle_config["accuracy"],
            "runtime_accuracy": runtime_config["accuracy"],
            "oracle_fp": oracle_config["fp"],
            "runtime_fp": runtime_config["fp"],
            "oracle_fn": oracle_config["fn"],
            "runtime_fn": runtime_config["fn"],
            "oracle_tp_lost": len(oracle_config["tp_lost"]),
            "runtime_tp_lost": len(runtime_config["tp_lost"]),
        })
    return {
        "certificates": len(keys),
        "agreement_rate": round(
            1 - len(disagreements) / len(keys) if keys else 1.0, 4,
        ),
        "unclassified_count": len(unclassified),
        "unclassified_rate": round(
            len(unclassified) / len(keys) if keys else 0.0, 4,
        ),
        "disagreements": [
            {
                "case_id": case_id,
                "candidate_id": candidate_id,
                "oracle_class": oracle.get(
                    (case_id, candidate_id), {}
                ).get("certificate_class"),
                "runtime_class": runtime.get(
                    (case_id, candidate_id), {}
                ).get("certificate_class"),
            }
            for case_id, candidate_id in disagreements
        ],
        "per_configuration": per_config_delta,
    }


def run_replay(
    *,
    run_tree: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[tuple[str, str], Mapping[str, str]],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Baseline, each single policy, every subset and the full set."""
    policies = policies_of(payload)
    baseline_decisions = {
        case_id: run_tree[case_id]["result"].get("state")
        for case_id in run_tree
    }
    baseline = evaluate_config(
        name="baseline", policies=[], run_tree=run_tree,
        bindings=bindings, baseline=baseline_decisions,
    )
    configs = [baseline]
    by_id = {
        str(policy.get("policy_id")): policy for policy in policies
    }
    for policy_id in sorted(by_id):
        configs.append(evaluate_config(
            name=policy_id, policies=[by_id[policy_id]],
            run_tree=run_tree, bindings=bindings,
            baseline=baseline_decisions,
        ))
    subset_configs: list[dict[str, Any]] = []
    for size in range(2, len(policies)):
        for combo in combinations(sorted(by_id), size):
            subset_configs.append(evaluate_config(
                name="+".join(combo), policies=[by_id[i] for i in combo],
                run_tree=run_tree, bindings=bindings,
                baseline=baseline_decisions,
            ))
    full = evaluate_config(
        name="full_set", policies=list(policies),
        run_tree=run_tree, bindings=bindings,
        baseline=baseline_decisions,
    )
    return {
        "schema_version": REPLAY_RESULT_SCHEMA,
        "cases": len(run_tree),
        "class_exposure": class_exposure(run_tree, bindings),
        "configurations": configs + subset_configs + [full],
    }


def write_replay_report(result: Mapping[str, Any], out_path: Path) -> None:
    lines = [
        "# Phase 1-B Offline Policy Replay Report", "",
        f"- cases: {result['cases']}",
        f"- schema: {result.get('schema_version')}",
        "", "## Certificate class exposure (surviving certificates)", "",
        "| class | gold-error cases | gold-clean cases |",
        "|---|---|---|",
    ]
    for name, counts in result["class_exposure"].items():
        lines.append(
            f"| {name} | {counts['gold_error']} | {counts['gold_clean']} |"
        )
    singles = {
        config["configuration"]: config
        for config in result["configurations"]
        if len(config["policy_ids"]) == 1
    }
    main = [
        config for config in result["configurations"]
        if config["configuration"] == "baseline"
        or len(config["policy_ids"]) == 1
        or config["configuration"] == "full_set"
    ]
    lines += [
        "", "## Main configurations (baseline / singles / full set)", "",
        "| configuration | TP | FP | FN | TN | accuracy | TP lost |"
        " FP fixed | changes | must-survive violations |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for config in main:
        lines.append(
            f"| {config['configuration']} | {config['tp']} |"
            f" {config['fp']} | {config['fn']} | {config['tn']} |"
            f" {config['accuracy']:.1%} | {len(config['tp_lost'])} |"
            f" {len(config['fp_fixed'])} | {config['decision_changes']}"
            f" | {len(config['must_survive_violations'])} |"
        )
    lines += [
        "", "### Single-policy detail", "",
        "| policy | TP lost | FP fixed | firing cases |",
        "|---|---|---|---|",
    ]
    for policy_id in sorted(singles):
        config = singles[policy_id]
        lines.append(
            f"| {policy_id} | {len(config['tp_lost'])} |"
            f" {len(config['fp_fixed'])} |"
            f" {len(config['firing_cases'])} |"
        )
    subsets = [
        config for config in result["configurations"]
        if config["configuration"] not in {"baseline", "full_set"}
        and len(config["policy_ids"]) > 1
    ]
    best = max(
        subsets, key=lambda config: (
            config["accuracy"], -len(config["tp_lost"]),
        ),
    )
    zero_loss = [
        config for config in subsets if not config["tp_lost"]
    ]
    best_zero_loss = max(zero_loss, key=lambda config: config["accuracy"])
    full_config = next(
        config for config in result["configurations"]
        if config["configuration"] == "full_set"
    )
    lines += [
        "", "## Interpretation anchors", "",
        f"- best subset by accuracy: {best['configuration']} "
        f"({best['accuracy']:.1%}, TP lost {len(best['tp_lost'])})",
        f"- best subset with zero TP loss: "
        f"{best_zero_loss['configuration']} "
        f"({best_zero_loss['accuracy']:.1%}, FP "
        f"{best_zero_loss['fp']})",
        f"- full set: {full_config['accuracy']:.1%}",
        "- promotion gate (review): accuracy >= baseline, FP clearly "
        "reduced, FN increase <= 1, no must-survive violations",
        "",
    ]
    lost_lists = {
        tuple(config["tp_lost"]): config["configuration"]
        for config in result["configurations"]
        if config["tp_lost"]
    }
    for lost, configuration in lost_lists.items():
        lines.append(
            f"- TP lost ({', '.join(lost)}): every configuration "
            f"containing L2-P1, first seen as {configuration}"
        )
    for config in main:
        if config["must_survive_violations"]:
            lines.append(
                f"- MUST-SURVIVE violations under "
                f"{config['configuration']}: "
                + ", ".join(
                    f"{v['policy_id']}:{v['case_id']}"
                    for v in config["must_survive_violations"]
                )
            )
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_comparison_report(
    comparison: Mapping[str, Any], out_path: Path,
) -> None:
    lines = [
        "# Phase 1-C3 Runtime-Binding Replay — Oracle vs Runtime", "",
        f"- certificates compared: {comparison['certificates']}",
        f"- binding agreement: {comparison['agreement_rate']:.1%}",
        f"- unclassified: {comparison['unclassified_count']} "
        f"({comparison['unclassified_rate']:.1%})",
        "", "## Configuration deltas (oracle -> runtime)", "",
        "| configuration | accuracy | FP | FN | TP lost |",
        "|---|---|---|---|---|",
    ]
    for entry in comparison["per_configuration"]:
        if entry["configuration"] in {"baseline", "full_set"} or (
            "+" not in entry["configuration"]
        ):
            lines.append(
                f"| {entry['configuration']} | "
                f"{entry['oracle_accuracy']:.1%} -> "
                f"{entry['runtime_accuracy']:.1%} | "
                f"{entry['oracle_fp']} -> {entry['runtime_fp']} | "
                f"{entry['oracle_fn']} -> {entry['runtime_fn']} | "
                f"{entry['oracle_tp_lost']} -> "
                f"{entry['runtime_tp_lost']} |"
            )
    lines += [
        "", "## Binding disagreements (oracle vs runtime)", "",
    ]
    for entry in comparison["disagreements"]:
        lines.append(
            f"- {entry['case_id']}/{entry['candidate_id']}: "
            f"{entry['oracle_class']} vs {entry['runtime_class']}"
        )
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    worksheet_parser = sub.add_parser(
        "worksheet", help="dump the label-free binding worksheet",
    )
    worksheet_parser.add_argument("--run", type=Path, required=True)
    worksheet_parser.add_argument("--out", type=Path, required=True)
    validate_parser = sub.add_parser(
        "validate", help="validate the binding layer over a run tree",
    )
    validate_parser.add_argument("--run", type=Path, required=True)
    validate_parser.add_argument("--bindings", type=Path, required=True)
    validate_parser.add_argument(
        "--allow-unclassified", action="store_true",
        help="accept runtime bindings that abstain on certificates",
    )
    replay_parser = sub.add_parser(
        "replay", help="replay adjudication under policy configurations",
    )
    replay_parser.add_argument("--run", type=Path, required=True)
    replay_parser.add_argument("--bindings", type=Path, required=True)
    replay_parser.add_argument("--file", type=Path, required=True)
    replay_parser.add_argument("--out-dir", type=Path, required=True)
    compare_parser = sub.add_parser(
        "compare",
        help="replay under oracle and runtime bindings and compare",
    )
    compare_parser.add_argument("--run", type=Path, required=True)
    compare_parser.add_argument("--oracle-bindings", type=Path, required=True)
    compare_parser.add_argument(
        "--runtime-bindings", type=Path, required=True,
    )
    compare_parser.add_argument("--file", type=Path, required=True)
    compare_parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run_tree = load_run_tree(args.run)
    if not run_tree:
        print(f"no cases found under {args.run}")
        return 1
    if args.command == "worksheet":
        rows = build_worksheet(run_tree)
        _write_json(args.out, rows)
        print(f"worksheet written -> {args.out} ({len(rows)} certificates)")
        return 0
    if args.command == "compare":
        oracle, oracle_payload = load_bindings(args.oracle_bindings)
        runtime, runtime_payload = load_bindings(args.runtime_bindings)
        oracle_checks = validate_bindings(
            oracle_payload, oracle, run_tree,
        )
        runtime_checks = validate_bindings(
            runtime_payload, runtime, run_tree, allow_unclassified=True,
        )
        for name, passed in {**oracle_checks, **runtime_checks}.items():
            print(f"{'ok  ' if passed else 'FAIL'} {name}")
        if not (
            all(oracle_checks.values()) and all(runtime_checks.values())
        ):
            return 1
        policy_payload = _read_json(args.file)
        oracle_result = run_replay(
            run_tree=run_tree, bindings=oracle, payload=policy_payload,
        )
        runtime_result = run_replay(
            run_tree=run_tree, bindings=runtime, payload=policy_payload,
        )
        comparison = compare_binding_layers(
            oracle=oracle, runtime=runtime,
            oracle_result=oracle_result, runtime_result=runtime_result,
        )
        _write_json(args.out_dir / "runtime_replay_result.json", {
            "schema_version": REPLAY_RESULT_SCHEMA,
            "runtime": runtime_result,
            "comparison": comparison,
        })
        write_comparison_report(
            comparison, args.out_dir / "RUNTIME_REPLAY_REPORT.md",
        )
        print(
            f"comparison -> {args.out_dir / 'runtime_replay_result.json'}"
        )
        print(f"report -> {args.out_dir / 'RUNTIME_REPLAY_REPORT.md'}")
        return 0
    bindings, payload = load_bindings(args.bindings)
    checks = validate_bindings(
        payload, bindings, run_tree,
        allow_unclassified=getattr(args, "allow_unclassified", False),
    )
    for name, passed in checks.items():
        print(f"{'ok  ' if passed else 'FAIL'} {name}")
    if not all(checks.values()):
        return 1
    if args.command == "validate":
        return 0
    policy_payload = _read_json(args.file)
    if policy_payload.get("schema_version") != POLICY_CANDIDATES_SCHEMA:
        print("unknown policy candidate schema")
        return 1
    result = run_replay(
        run_tree=run_tree, bindings=bindings, payload=policy_payload,
    )
    _write_json(args.out_dir / "replay_result.json", result)
    write_replay_report(result, args.out_dir / "REPLAY_REPORT.md")
    print(f"replay result -> {args.out_dir / 'replay_result.json'}")
    print(f"replay report -> {args.out_dir / 'REPLAY_REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
