"""Fix-val round orchestrator: baseline -> evolve -> screen -> deploy -> report.

One round over a fresh resampled split (100 = 60 evolution + 40
validation), driven by four subcommands:

    baseline   run the bare detector (no artifacts) over all 100 cases
               (the round's pre-evolution anchor)
    evolve     stability re-roll (k=1; disagreement = unstable
               learning target) -> fact-driven failure classification
               -> capability proposals -> G1 + bounded revision ->
               flip gate -> deployables folded into the PERSISTENT
               capability library
    deploy     per-round regression screen over every accumulated
               artifact; precision artifacts deploy in the paired
               frozen-census arm, recall artifacts in the repeated
               full-census arm
    report     paired/full comparisons on the read-once validation set,
               evolution-set decomposition, capability traces and
               localization retention

Usage:
    python -m experiments.run_fixval_round baseline --round 3
    python -m experiments.run_fixval_round evolve --round 3
    python -m experiments.run_fixval_round deploy --round 3
    python -m experiments.run_fixval_round report --round 3
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.api import Detector
from sehd_rise2.detector.artifacts import (
    ARTIFACT_REGISTRY_SCHEMA,
    registry_hash,
    validate_registry,
)
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.conventions import CONVENTIONS
from sehd_rise2.detector.court import (
    CENSUS_SYSTEM,
    DEFENSE_SYSTEM,
    VERIFICATION_SYSTEM,
)
from sehd_rise2.detector.packet import EVALUATOR_ONLY_FIELDS
from sehd_rise2.detector.result import prediction_state
from sehd_rise2.detector.runs import (
    default_runs_root,
    new_run_id,
    record_case_run,
    summarize_run,
    write_run_meta,
)
from sehd_rise2.evolution.artifact_validation import (
    localization_keep,
    validate_artifacts,
)
from sehd_rise2.evolution.capability_evolution import (
    build_registry,
    generate_proposals,
)
from sehd_rise2.evolution.capability_library import (
    filter_registry_by_state,
    load_registry,
    merge_round_registry,
    record_promotions,
)
from sehd_rise2.evolution.gates import load_calibration
from sehd_rise2.evolution.router import (
    build_route_rows,
    record_router_outcomes,
    route_failures,
)
from sehd_rise2.evolution.worksheet import (
    WRONGFUL_CONVICTION_ATTRIBUTION,
    attribute_miss,
    confirm_failures,
    load_run_tree,
)

DATASET_ROOT = Path("datasets/raw/agenthallu/AgentHallu")
WORKERS = 20
# The accumulating memory of the convention channel: the deploy arm
# composes it, so the round measures the FULL evolved detector against
# the bare baseline.
CONVENTION_LIBRARY = Path("datasets/convention_library/registry.json")
CAPABILITY_LIBRARY_STATE = Path(
    "datasets/convention_library/library_state.json"
)


def _active_component_inventory(artifacts: list[dict]) -> list[dict]:
    return [
        {
            "artifact_id": str(artifact.get("artifact_id")),
            "kind": str(artifact.get("kind")),
            "trigger_features": list(
                ((artifact.get("trigger") or {}).get("packet_features")) or []
            ),
        }
        for artifact in artifacts
        if artifact.get("kind") in {
            "evidence_predicate", "candidate_generator",
        }
    ]


def _baseline_rule_texts(artifacts: list[dict]) -> list[str]:
    """Existing stage rules used by the redundancy gate."""
    texts = [CENSUS_SYSTEM, VERIFICATION_SYSTEM, DEFENSE_SYSTEM]
    for spec in CONVENTIONS.values():
        texts.append(str(spec.get("directive") or ""))
    for artifact in artifacts:
        if artifact.get("kind") != "prompt_directive":
            continue
        intervention = artifact.get("intervention") or {}
        texts.append(str(intervention.get("directive") or ""))
        texts.append(str(intervention.get("exclusion") or ""))
    return texts


def composed_registry(*registries: Path) -> dict[str, Any]:
    """Merge validated registries into one deployable payload."""
    artifacts: list[dict[str, Any]] = []
    for path in registries:
        if Path(path).exists():
            artifacts.extend(
                validate_registry(load_registry(Path(path))),
            )
    payload: dict[str, Any] = {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "artifacts": artifacts,
    }
    payload["content_hash"] = registry_hash(payload)
    return payload


def split_dir(round_no: int) -> Path:
    # Round 3 = the V3 capability experiment split (50 evolution cases,
    # validation deferred); earlier rounds keep their own directories.
    if round_no == 3:
        return Path("datasets/eval_splits/v3_evo50")
    return Path(f"datasets/eval_splits/fixval_round{round_no}_v1")


def _case_list(round_no: int, which: str) -> list[str]:
    name = "evolution_cases" if which == "evolution" else "validation_cases"
    return json.loads(
        (split_dir(round_no) / f"{name}.json").read_text(encoding="utf-8"),
    )


def _all_cases(round_no: int) -> list[str]:
    return sorted(
        set(_case_list(round_no, "evolution"))
        | set(_case_list(round_no, "validation")),
    )


def _load_gold(case_path: str) -> tuple[dict, bool | None]:
    raw = json.loads(
        (DATASET_ROOT / case_path).read_text(encoding="utf-8"),
    )
    value = raw.get("is_hallucination")
    if isinstance(value, bool):
        gold = value
    else:
        gold = {"true": True, "false": False}.get(
            str(value).strip().lower(),
        )
    clean = {
        key: item for key, item in raw.items()
        if key not in EVALUATOR_ONLY_FIELDS
    }
    return clean, gold


def gold_join(run_id: str, cases: list[str]) -> dict:
    rows = []
    for case in sorted(cases):
        result = json.loads(
            (
                default_runs_root() / run_id / "cases"
                / case.replace("/", "__") / "result.json"
            ).read_text(encoding="utf-8"),
        )
        clean, gold = _load_gold(case)
        raw = json.loads(
            (DATASET_ROOT / case).read_text(encoding="utf-8"),
        )
        gold_step = raw.get("hallucination_step")
        if str(gold_step or "").isdigit():
            gold_step = int(gold_step)
        else:
            gold_step = None
        proof = result.get("proof_adjudication") or {}
        rows.append({
            "case": case,
            "gold": gold,
            "gold_step": gold_step,
            "state": result.get("state"),
            "prediction": prediction_state(result),
            "responsible_step": result.get("responsible_step"),
            "runtime_valid": result.get("runtime_valid"),
            "active_artifacts": proof.get("active_artifacts") or [],
            "capability_trace": proof.get("capability_trace") or [],
            "candidate_generator_trace": (
                proof.get("candidate_generator_trace") or []
            ),
        })
    decisive = [row for row in rows if row["prediction"] is not None]
    correct = sum(
        1 for row in decisive if row["prediction"] == row["gold"]
    )
    return {
        "schema_version": "sehd2.eval_gold.v1",
        "run_id": run_id,
        "cases": rows,
        "decisive_cases": len(decisive),
        "unresolved_cases": len(rows) - len(decisive),
        "accuracy": (
            round(correct / len(decisive), 4) if decisive else None
        ),
        "tp": sum(
            1 for r in decisive if r["gold"] and r["prediction"] is True
        ),
        "fp": sum(
            1 for r in decisive if not r["gold"] and r["prediction"] is True
        ),
        "fn": sum(
            1 for r in decisive if r["gold"] and r["prediction"] is False
        ),
        "tn": sum(
            1 for r in decisive if not r["gold"] and r["prediction"] is False
        ),
    }


def _run_cases(
    run_id: str, cases: list[str],
    artifacts: dict[str, Any] | None = None,
) -> None:
    detector = Detector(load_config())
    run_dir = default_runs_root() / run_id / "cases"

    def run(case: str) -> str:
        if (run_dir / case.replace("/", "__") / "result.json").exists():
            return case
        clean, _gold = _load_gold(case)
        started = time.monotonic()
        result = detector.detect(
            clean, case_id=case, artifacts=artifacts,
        )
        for _attempt in range(3):
            if result.get("state") != "unresolved":
                break
            transport = any(
                str(reason.get("error_type")) in {
                    "provider_error", "transport_error", "invalid_json",
                }
                for reason in result.get("authority_failure_reasons") or []
            )
            if not transport:
                break
            result = detector.detect(
                clean, case_id=case, artifacts=artifacts,
            )
        record_case_run(
            runs_root=default_runs_root(), run_id=run_id, case_id=case,
            input_record=clean, result=result,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        print(
            f"  [case] {case:<32} state={result.get('state'):<15} "
            f"artifacts={result.get('proof_adjudication', {}).get('active_artifacts')}",
            flush=True,
        )
        return case

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(run, case) for case in cases]
        for future in as_completed(futures):
            future.result()


def _existing_run(prefix: str) -> str:
    matches = sorted(
        path.name for path in default_runs_root().glob(f"{prefix}_*")
        if (path / "eval_gold.json").exists()
    )
    if not matches:
        raise SystemExit(f"no completed run found for prefix {prefix!r}")
    return matches[-1]


def _finalize_run(run_id: str, cases: list[str]) -> dict:
    write_run_meta(
        runs_root=default_runs_root(), run_id=run_id,
        config=load_config(), case_ids=cases,
    )
    summarize_run(default_runs_root(), run_id)
    report = gold_join(run_id, cases)
    (default_runs_root() / run_id / "eval_gold.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    condensed = {k: v for k, v in report.items() if k != "cases"}
    print(json.dumps(condensed, ensure_ascii=False, indent=1), flush=True)
    return report


def cmd_baseline(round_no: int, workers: int) -> None:
    global WORKERS
    WORKERS = workers
    cases = _all_cases(round_no)
    run_id = new_run_id(f"fixval_r{round_no}_baseline")
    print(f"baseline run {run_id}: {len(cases)} cases", flush=True)
    _run_cases(run_id, cases)
    _finalize_run(run_id, cases)


def _round_constraints() -> dict:
    """Runner whitelist + composition tolerance for this round."""
    return load_calibration()


def cmd_evolve(round_no: int) -> None:
    calibration = _round_constraints()
    baseline_id = _existing_run(f"fixval_r{round_no}_baseline")
    baseline_dir = default_runs_root() / baseline_id
    evolution_cases = set(_case_list(round_no, "evolution"))
    if not evolution_cases:
        raise SystemExit(
            f"{split_dir(round_no)} defines no evolution pool; refusing "
            "to evolve over nothing"
        )
    full_tree = load_run_tree(baseline_dir)
    tree = {
        case_id: entry
        for case_id, entry in full_tree.items()
        if case_id in evolution_cases
    }

    # Stability pass. One unguided re-roll per recorded failure: a
    # repeated failure is stable teaching material, a disagreement
    # marks the case unstable — a first-class stabilisation target.
    _misses, _convictions, confirm_stats = confirm_failures(
        run_tree=tree, dataset_root=DATASET_ROOT, workers=WORKERS, rolls=1,
    )
    print(
        f"worksheet: {confirm_stats['misses_stable']}"
        f"/{confirm_stats['misses_total']} misses stable, "
        f"{confirm_stats['convictions_stable']}"
        f"/{confirm_stats['convictions_total']} convictions stable, "
        f"{len(confirm_stats['unstable_case_ids'])} unstable",
        flush=True,
    )

    # Active registry: what the detector already knows. The proposal
    # gate uses it for trigger-amendment and redundancy checks.
    existing_paths = [
        path for path in (CONVENTION_LIBRARY,)
        if Path(path).exists()
    ]
    active_artifacts = composed_registry(*existing_paths)["artifacts"]
    active_components = _active_component_inventory(active_artifacts)
    baseline_texts = _baseline_rule_texts(active_artifacts)

    def worksheet_attribution(result: dict) -> str:
        if str(result.get("state")) == "verified_error":
            return WRONGFUL_CONVICTION_ATTRIBUTION
        return attribute_miss(result)

    # The router meta skill: mechanical classes first (unstable /
    # infrastructure are measurements), then one closed-vocabulary
    # provider call over the facts. Its outcome memory earns "prefer"
    # hints across rounds; hints never touch gates.
    attribution_by_case = {}
    for case_id, entry in tree.items():
        if case_id in confirm_stats["rerolled_states"]:
            attribution_by_case[case_id] = worksheet_attribution(
                entry["result"],
            )
    rows = build_route_rows(
        run_tree=tree,
        rerolled=confirm_stats["rerolled_states"],
        attribution_by_case=attribution_by_case,
        dataset_root=DATASET_ROOT,
        active_components=active_components,
        unstable_cases=confirm_stats["unstable_case_ids"],
    )
    diagnoses, routing_meta = route_failures(rows)
    print(
        f"routing: {routing_meta.get('mechanical')} mechanical, "
        f"{routing_meta.get('routed')} routed"
        f"{', prefer hint ' + str(routing_meta['prefer_hint']) if routing_meta.get('prefer_hint') else ''}"
        f"{', ' + str(len(routing_meta['fallbacks'])) + ' fallbacks' if routing_meta.get('fallbacks') else ''}",
        flush=True,
    )

    # G1 fuses into the proposal call so an ungated proposal can
    # never leave the generator; the revision loop feeds G1
    # rejections back to drafting.
    proposed, meta = generate_proposals(
        diagnoses=diagnoses,
        baseline_rule_texts=baseline_texts,
        active_artifacts=active_artifacts,
        allowed_predicate_runners=(
            calibration.get("capability_proposal") or {}
        ).get("predicate_runners"),
        revision_rounds=2,
    )
    out_dir = split_dir(round_no)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "capability_proposals.json").write_text(
        json.dumps(
            {
                "diagnoses": diagnoses,
                "routing_meta": routing_meta,
                "proposals": proposed,
                "generation_meta": meta,
                "confirm": confirm_stats,
            },
            ensure_ascii=False, indent=2,
        ) + "\n", encoding="utf-8",
    )
    print(
        f"proposals: {meta.get('revision_attempts')} drafting attempt(s) -> "
        f"{len(proposed)} final; rejections="
        f"{len(meta.get('rejections') or [])}",
        flush=True,
    )

    if not proposed:
        registry = build_registry([])
        (out_dir / "capability_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("no capability proposals passed G1", flush=True)
        flipped_cases: list[str] = []
    else:
        validation = validate_artifacts(
            run_tree=tree, artifacts=proposed, dataset_root=DATASET_ROOT,
            out_dir=out_dir, workers=WORKERS,
            unstable_cases=confirm_stats["unstable_case_ids"],
        )
        deployable = validation["deployable"]
        registry = build_registry([
            artifact for artifact in proposed
            if str(artifact["artifact_id"]) in deployable
        ])
        (out_dir / "capability_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        flipped_cases = sorted({
            case_id
            for report in validation["per_artifact"].values()
            for case_id in report["metrics"]["flipped_cases"]
        })
        print(
            f"flip gate deployable: {deployable} -> "
            f"{out_dir / 'capability_registry.json'}",
            flush=True,
        )

    # The router's scoreboard: which routes produced accepted proposals
    # and verified flips this round. Deterministic counts only.
    accepted_by_case: dict[str, list[str]] = {}
    for artifact in proposed:
        for case_id in (
            (artifact.get("provenance") or {}).get("case_evidence") or []
        ):
            accepted_by_case.setdefault(str(case_id), []).append(
                str(artifact["artifact_id"]),
            )
    record_router_outcomes(
        rows=diagnoses,
        accepted_by_case=accepted_by_case,
        flipped_cases=flipped_cases,
    )

    library = merge_round_registry(
        CONVENTION_LIBRARY, registry, round_no,
    )
    record_promotions(
        state_path=CAPABILITY_LIBRARY_STATE,
        artifact_ids=[
            str(artifact["artifact_id"])
            for artifact in library["artifacts"]
            if str(artifact["artifact_id"]).startswith(f"R{round_no}-")
        ],
        round_no=round_no,
    )
    print(
        f"capability library -> {CONVENTION_LIBRARY} "
        f"({len(library['artifacts'])} accumulated artifacts)",
        flush=True,
    )


def _run_paired(
    run_id: str, baseline_id: str, cases: list[str],
    artifacts: dict[str, Any],
) -> None:
    """Paired deploy arm: the baseline's frozen census + deployed registry.

    Only the adjudication stages re-run (verification, defense and the
    runtime binder); the candidate set is the baseline arm's recorded
    census, so baseline-vs-paired flips isolate the registry's
    adjudication-layer effect from census resampling noise.
    """
    detector = Detector(load_config())
    run_dir = default_runs_root() / run_id / "cases"
    baseline_dir = default_runs_root() / baseline_id / "cases"

    def run(case: str) -> str:
        if (run_dir / case.replace("/", "__") / "result.json").exists():
            return case
        clean, _gold = _load_gold(case)
        baseline = json.loads(
            (
                baseline_dir / case.replace("/", "__") / "result.json"
            ).read_text(encoding="utf-8"),
        )
        census = (baseline.get("proof_adjudication") or {}).get("census") or []
        started = time.monotonic()
        result = detector.detect(
            clean, case_id=case, artifacts=artifacts, frozen_census=census,
        )
        for _attempt in range(3):
            if result.get("state") != "unresolved":
                break
            transport = any(
                str(reason.get("error_type")) in {
                    "provider_error", "transport_error", "invalid_json",
                }
                for reason in result.get("authority_failure_reasons") or []
            )
            if not transport:
                break
            result = detector.detect(
                clean, case_id=case, artifacts=artifacts, frozen_census=census,
            )
        record_case_run(
            runs_root=default_runs_root(), run_id=run_id, case_id=case,
            input_record=clean, result=result,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        print(
            f"  [case] {case:<32} base={baseline.get('state'):<15} "
            f"paired={result.get('state'):<15} "
            f"artifacts={result.get('proof_adjudication', {}).get('active_artifacts')}",
            flush=True,
        )
        return case

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(run, case) for case in cases]
        for future in as_completed(futures):
            future.result()


def _registry_payload(artifacts: list[dict]) -> dict[str, Any]:
    payload = {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "artifacts": [dict(artifact) for artifact in artifacts],
    }
    payload["content_hash"] = registry_hash(payload)
    return payload


def _run_full_repeated(
    run_id: str, cases: list[str], artifacts: dict[str, Any],
    rolls: int = 2,
) -> None:
    """Full-detection deploy arm for recall artifacts.

    Candidate generators cannot be measured under frozen census; this arm
    lets them run on the full pipeline. Each case is rolled ``rolls``
    times and the majority terminal state is recorded (ties keep the
    first roll). The roll states are persisted on the result envelope so
    the report can compare them with the pure-retest noise distribution.
    """
    detector = Detector(load_config())
    run_dir = default_runs_root() / run_id / "cases"

    def run(case: str) -> str:
        case_dir = run_dir / case.replace("/", "__")
        if (case_dir / "result.json").exists():
            return case
        clean, _gold = _load_gold(case)
        results = []
        for _ in range(max(1, int(rolls))):
            started = time.monotonic()
            result = detector.detect(
                clean, case_id=case, artifacts=artifacts,
            )
            for _attempt in range(3):
                if result.get("state") != "unresolved":
                    break
                transport = any(
                    str(reason.get("error_type")) in {
                        "provider_error", "transport_error", "invalid_json",
                    }
                    for reason in result.get("authority_failure_reasons") or []
                )
                if not transport:
                    break
                result = detector.detect(
                    clean, case_id=case, artifacts=artifacts,
                )
            results.append((result, time.monotonic() - started))
        states = [str(result.get("state")) for result, _elapsed in results]
        error_votes = sum(1 for state in states if state == "verified_error")
        clean_votes = sum(1 for state in states if state == "verified_clean")
        chosen, elapsed = results[0]
        if error_votes > clean_votes and error_votes > 0:
            for result, _elapsed in results:
                if result.get("state") == "verified_error":
                    chosen, elapsed = result, _elapsed
                    break
        elif clean_votes > error_votes and clean_votes > 0:
            for result, _elapsed in results:
                if result.get("state") == "verified_clean":
                    chosen, elapsed = result, _elapsed
                    break
        envelope = dict(chosen)
        envelope["full_arm_roll_states"] = states
        record_case_run(
            runs_root=default_runs_root(), run_id=run_id, case_id=case,
            input_record=clean, result=envelope,
            elapsed_seconds=round(elapsed, 1),
        )
        print(
            f"  [full] {case:<32} states={states} chosen={chosen.get('state')}",
            flush=True,
        )
        return case

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(run, case) for case in cases]
        for future in as_completed(futures):
            future.result()


def cmd_deploy(round_no: int, workers: int) -> None:
    global WORKERS
    WORKERS = workers
    baseline_id = _existing_run(f"fixval_r{round_no}_baseline")
    cases = _all_cases(round_no)
    out_dir = split_dir(round_no)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Everything the library earned deploys as-is: each artifact entered
    # through the flip gate, so no post-admission re-screening runs.
    # The deployment split mirrors the artifact plane: adjudication-
    # layer artifacts replay on the baseline's frozen census (paired
    # arm), recall artifacts run the full pipeline (live-census arm).
    raw_registry = composed_registry(CONVENTION_LIBRARY)
    deploy_artifacts = filter_registry_by_state(
        raw_registry, CAPABILITY_LIBRARY_STATE,
    )
    round_registry_path = out_dir / "capability_registry.json"
    if round_registry_path.exists():
        deploy_artifacts.extend(
            validate_registry(
                json.loads(round_registry_path.read_text(encoding="utf-8")),
            )
        )
    if not deploy_artifacts:
        raise SystemExit(
            "the capability library is empty: nothing to deploy"
        )
    registry = _registry_payload(deploy_artifacts)
    kinds: dict[str, int] = {}
    for artifact in registry["artifacts"]:
        kinds[str(artifact["kind"])] = kinds.get(str(artifact["kind"]), 0) + 1
    print(
        f"deploying the full library: {len(deploy_artifacts)} "
        f"artifacts {kinds}, registry hash "
        f"{registry['content_hash'][:12]}...",
        flush=True,
    )

    recall_artifacts = [
        artifact for artifact in deploy_artifacts
        if artifact.get("kind") == "candidate_generator"
        or str((artifact.get("gap") or {}).get("gap_type") or "") == "recall"
    ]
    paired_artifacts = [
        artifact for artifact in deploy_artifacts
        if artifact not in recall_artifacts
    ]

    if paired_artifacts:
        run_id = new_run_id(f"fixval_r{round_no}_deployed")
        print(
            f"paired deploy run {run_id}: {len(cases)} cases",
            flush=True,
        )
        _run_paired(
            run_id, baseline_id, cases, _registry_payload(paired_artifacts),
        )
        _finalize_run(run_id, cases)
    if recall_artifacts:
        run_id = new_run_id(f"fixval_r{round_no}_deployed_full")
        print(
            f"full deploy run {run_id}: {len(cases)} cases",
            flush=True,
        )
        _run_full_repeated(
            run_id, cases, _registry_payload(recall_artifacts),
        )
        _finalize_run(run_id, cases)


def _paired_report(
    baseline: dict, deployed: dict, which: str,
) -> dict[str, Any]:
    base_rows = {row["case"]: row for row in baseline["cases"]}
    dep_rows = {row["case"]: row for row in deployed["cases"]}
    shared = sorted(set(base_rows) & set(dep_rows))
    flips = []
    fn_fixed = fp_fixed = tp_lost = fp_added = fn_added = 0
    capability_ids: dict[str, int] = {}
    trace_cases = 0
    generator_trace_cases = 0
    for case in shared:
        before = base_rows[case]
        after = dep_rows[case]
        before_pred = before["prediction"]
        after_pred = after["prediction"]
        for record in after.get("capability_trace") or []:
            if record.get("status") == "produced":
                capability_ids[str(record.get("capability_id"))] = (
                    capability_ids.get(str(record.get("capability_id")), 0) + 1
                )
                trace_cases += 1
        for record in after.get("candidate_generator_trace") or []:
            if record.get("status") == "produced":
                capability_ids[str(record.get("capability_id"))] = (
                    capability_ids.get(str(record.get("capability_id")), 0) + 1
                )
                generator_trace_cases += 1
        if before_pred == after_pred:
            continue
        entry = {
            "case": case,
            "gold": before["gold"],
            "before": before_pred,
            "after": after_pred,
        }
        flips.append(entry)
        if before["gold"] and before_pred is False and after_pred is True:
            fn_fixed += 1
        elif not before["gold"] and before_pred is True and after_pred is False:
            fp_fixed += 1
        elif before["gold"] and before_pred is True and after_pred is False:
            tp_lost += 1
        elif not before["gold"] and before_pred is False and after_pred is True:
            fp_added += 1
        else:
            fn_added += 1
    localization = localization_keep(
        baseline=[base_rows[case] for case in shared],
        deployed=[dep_rows[case] for case in shared],
    )
    return {
        "which": which,
        "cases": len(shared),
        "baseline": {
            k: baseline[k] for k in ("accuracy", "tp", "fp", "fn", "tn")
        },
        "deployed": {
            k: deployed[k] for k in ("accuracy", "tp", "fp", "fn", "tn")
        },
        "flips": {
            "fn_fixed": fn_fixed,
            "fp_fixed": fp_fixed,
            "tp_lost": tp_lost,
            "fp_added": fp_added,
            "other": fn_added,
            "net_improvement": fn_fixed + fp_fixed - tp_lost - fp_added,
        },
        "flip_table": flips,
        "capability_metrics": {
            "produced_by_capability": dict(sorted(capability_ids.items())),
            "capability_trace_cases": trace_cases,
            "candidate_generator_trace_cases": generator_trace_cases,
            "localization": localization,
        },
    }


def _round_funnel(round_no: int) -> dict[str, Any]:
    """Evolution funnel: proposals -> gates -> deploy, from on-disk stage outputs.

    A framework this strict can drift from "learning badly" to "not
    learning"; the funnel is where that drift becomes visible.
    """
    out = split_dir(round_no)
    funnel: dict[str, Any] = {}

    proposals_path = out / "capability_proposals.json"
    if proposals_path.exists():
        payload = json.loads(proposals_path.read_text(encoding="utf-8"))
        confirm = payload.get("confirm") or {}
        diagnoses = payload.get("diagnoses") or []
        funnel["diagnosed_failures"] = len(diagnoses)
        funnel["unstable_failures"] = sum(
            1 for row in diagnoses
            if isinstance(row, Mapping) and row.get("failure_class") == "unstable"
        )
        routing = payload.get("routing_meta") or {}
        funnel["routing_fallbacks"] = len(routing.get("fallbacks") or [])
        funnel["routing_prefer_hint"] = routing.get("prefer_hint") or []
        meta = payload.get("generation_meta") or {}
        for attempt in meta.get("revision") or []:
            funnel.setdefault("revision_attempts", []).append({
                "attempt": attempt.get("attempt"),
                "raw_proposals": attempt.get("raw_proposals"),
                "g1_accepted": attempt.get("g1_accepted"),
            })
        funnel["g1_accepted"] = len(payload.get("proposals") or [])

    validation_path = out / "artifact_validation.json"
    if validation_path.exists():
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
        deployable = set(payload.get("deployable") or [])
        per = payload.get("per_artifact") or {}
        funnel["flip_gate_deployable"] = sorted(str(a) for a in deployable)
        funnel["flips_by_deployable"] = {
            artifact_id: (report.get("metrics") or {}).get("flips")
            for artifact_id, report in per.items()
            if artifact_id in deployable
        }
        stabilised = sorted({
            case_id
            for report in per.values()
            if isinstance(report, Mapping)
            for case_id, row in (
                (report.get("per_target") or {}).items()
            )
            if isinstance(row, Mapping)
            and not row.get("stable")
            and row.get("flipped")
        })
        funnel["unstable_stabilised"] = stabilised

    if CAPABILITY_LIBRARY_STATE.exists():
        state = json.loads(
            CAPABILITY_LIBRARY_STATE.read_text(encoding="utf-8")
        )
        statuses = [
            str(entry.get("status"))
            for entry in (state.get("artifacts") or {}).values()
            if isinstance(entry, Mapping)
        ]
        funnel["library"] = {
            "active": statuses.count("active"),
            "held": statuses.count("held"),
            "retired": statuses.count("retired"),
        }
    return funnel


def cmd_report(round_no: int) -> None:
    baseline_id = _existing_run(f"fixval_r{round_no}_baseline")
    deployment_matches = sorted(
        path.name for path in default_runs_root().glob(
            f"fixval_r{round_no}_deployed*"
        )
        if (path / "eval_gold.json").exists()
    )
    paired_matches = [
        name for name in deployment_matches if "_deployed_full_" not in name
    ]
    full_matches = [
        name for name in deployment_matches if "_deployed_full_" in name
    ]
    if not deployment_matches:
        raise SystemExit("no completed deployed run found")
    baseline = json.loads(
        (default_runs_root() / baseline_id / "eval_gold.json").read_text(
            encoding="utf-8",
        ),
    )
    paired = None
    if paired_matches:
        paired = json.loads(
            (default_runs_root() / paired_matches[-1] / "eval_gold.json")
            .read_text(encoding="utf-8"),
        )
    elif deployment_matches:
        paired = json.loads(
            (default_runs_root() / deployment_matches[-1] / "eval_gold.json")
            .read_text(encoding="utf-8"),
        )
    full = None
    if full_matches:
        full = json.loads(
            (default_runs_root() / full_matches[-1] / "eval_gold.json")
            .read_text(encoding="utf-8"),
        )
    evolution_cases = set(_case_list(round_no, "evolution"))
    validation_cases = set(_case_list(round_no, "validation"))

    def subset(report: dict, cases: set[str]) -> dict:
        rows = [r for r in report["cases"] if r["case"] in cases]
        decisive = [r for r in rows if r["prediction"] is not None]
        correct = sum(
            1 for r in decisive if r["prediction"] == r["gold"]
        )
        return {
            "cases": rows,
            "accuracy": (
                round(correct / len(decisive), 4) if decisive else None
            ),
            "tp": sum(1 for r in decisive if r["gold"] and r["prediction"]),
            "fp": sum(
                1 for r in decisive
                if not r["gold"] and r["prediction"] is True
            ),
            "fn": sum(
                1 for r in decisive
                if r["gold"] and r["prediction"] is False
            ),
            "tn": sum(
                1 for r in decisive
                if not r["gold"] and r["prediction"] is False
            ),
        }

    report: dict[str, Any] = {
        "schema_version": "sehd2.fixval_round_report.v1",
        "round": round_no,
        "baseline_run": baseline_id,
        "deployed_run": paired_matches[-1] if paired_matches else (
            full_matches[-1] if full_matches else None
        ),
        "funnel": _round_funnel(round_no),
    }
    if paired is not None:
        report["validation"] = _paired_report(
            subset(baseline, validation_cases),
            subset(paired, validation_cases),
            "validation (read once, paired adjudication arm)",
        )
        report["evolution_checkpoint"] = _paired_report(
            subset(baseline, evolution_cases),
            subset(paired, evolution_cases),
            "evolution (decomposition, paired arm)",
        )
    if full is not None:
        report["full_census_arm"] = _paired_report(
            subset(baseline, validation_cases),
            subset(full, validation_cases),
            "validation (read once, full census arm)",
        )
        report["full_census_evolution"] = _paired_report(
            subset(baseline, evolution_cases),
            subset(full, evolution_cases),
            "evolution (full census arm)",
        )
    out = split_dir(round_no) / "round_report.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for section in (
        "validation", "evolution_checkpoint", "full_census_arm",
        "full_census_evolution",
    ):
        view = report.get(section)
        if not view:
            continue
        print(
            f"\n== {view['which']} ==\n"
            f"baseline {view['baseline']} \n"
            f"deployed {view['deployed']}\n"
            f"flips    {view['flips']}\n"
            f"capability {view['capability_metrics']}",
        )
    print(f"\nfunnel: {report['funnel']}")
    print(f"\nreport -> {out}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["baseline", "evolve", "deploy", "report"],
    )
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()
    if args.command == "baseline":
        cmd_baseline(args.round, args.workers)
    elif args.command == "evolve":
        cmd_evolve(args.round)
    elif args.command == "deploy":
        cmd_deploy(args.round, args.workers)
    else:
        cmd_report(args.round)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
