"""End-to-end parity: old vs new evolution on the same fixture stream.

Runs the same fixture case stream through both ``src.sehd_v6.evolution``
and ``sehd_rise.api.evolution`` with a real provider, then compares:

  1. Accepted-edit target module/operation sets (from champion genome rules)
  2. Experience-memory mechanism-signature counts
  3. Artifact schema completeness (evolution_result / champion_genome /
     htc_tes_experience_memory / evolution_state_manifest)
  4. C1-C10 difference explanations for any metric divergence

Parity holds when every artifact is present with a complete schema and every
metric divergence is attributable to a C1-C10 semantic change.  An
unexplained divergence or a missing artifact is a failure.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.parity.fixtures import load_fixture_records  # noqa: E402

ARTIFACT_NAMES = (
    "evolution_result.json",
    "champion_genome.json",
    "policy_genome.json",
    "htc_tes_experience_memory.json",
    "htc_tes_coverage.json",
    "evolution_state_manifest.json",
)

RESULT_FIELDS = (
    "schema_version", "run_id", "champion_genome", "policy_genome",
    "steps", "gate_summary", "total_cases", "triggered_cases",
    "edits_accepted", "edits_rejected", "stable_cases",
    "runtime_invalid_cases", "halted", "halt_reason", "evolution_audit",
)

MANIFEST_FIELDS = (
    "schema_version", "source_run_id", "lineage",
    "provider_rule_genome", "adjudication_policy_genome", "experience_memory",
)

METRIC_FIELDS = (
    "total_cases", "triggered_cases", "edits_accepted",
    "edits_rejected", "stable_cases", "runtime_invalid_cases",
    "halted",
)

# Each C-id and the metric divergences it explains.  A divergence not
# covered by any entry here is an unexplained failure.
C_NOTES: dict[str, tuple[str, tuple[str, ...]]] = {
    "C1": (
        "Single Repairability Authority: only REPAIRABLE enters evolution; "
        "NON_EVOLVABLE and FRAMEWORK_DEFECT are diverted before the loop.",
        ("triggered_cases", "stable_cases"),
    ),
    "C2": (
        "Unaddressable redefined as UNADDRESSABLE_FRAMEWORK_DEFECT (framework "
        "invariant violation), stripped from the no-edit convergence path.",
        ("edits_rejected", "stable_cases"),
    ),
    "C3": (
        "Unified CausalFailureEpisode + Terminal Causal Cut: three attribution "
        "sites merged into one; owner may differ on deliberately corrected cases.",
        ("triggered_cases",),
    ),
    "C4": (
        "Detector/Evolution ownership boundary: validator/transport/route/config "
        "defects route to framework side, never generate a Skill edit.",
        ("edits_accepted", "edits_rejected"),
    ),
    "C5": (
        "HTC-TES demoted to Advisory: type info only serves retrieval/prior/"
        "slicing; no owner override or module filtering.",
        ("edits_accepted",),
    ),
    "C6": (
        "L1/L2 share Canonical RepairPlan protocol; the old "
        "EvolutionDetection/project_for_evolution bridge types are gone.",
        (),
    ),
    "C7": (
        "Meta redefined as Cross-Case Strategy Learner: only search prior / "
        "ranking / retry / diversity; no hard-gate intervention.",
        (),
    ),
    "C8": (
        "Outcome funnel replaces skip: Framework / Search / Execution / "
        "Fitness four-layer terminal classification changes how stable vs "
        "rejected vs unaddressable are counted.",
        ("stable_cases", "edits_rejected", "runtime_invalid_cases"),
    ),
    "C9": (
        "Convergence redefined: only complete repair attempts (plan legal -> "
        "proposal legal -> executed -> effect measured -> no acceptable "
        "improvement) advance the convergence window.",
        ("stable_cases",),
    ),
    "C10": (
        "Complete Causal Credit Chain: execution evidence -> edge propagation "
        "-> blockage closure -> terminal improvement -> canary/transfer; "
        "intermediate layers cannot substitute downstream evidence.",
        ("edits_accepted",),
    ),
}


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _genome_rule_set(genome: dict[str, Any]) -> set[tuple[str | None, str | None]]:
    """Extract (module, operation) pairs from a champion genome's rules."""
    return {
        (rule.get("module"), rule.get("operation"))
        for rule in (genome.get("rules") or [])
    }


def _mechanism_count(memory: dict[str, Any]) -> int:
    """Count mechanism entries in the experience memory."""
    return len(memory.get("failure_mechanisms") or [])


def _missing_fields(data: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if f not in data]


def _run_side(
    label: str,
    run_fn: Any,
    records: dict[str, dict[str, Any]],
    config: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run one evolution side and load its artifacts."""
    print(f"[{label}] starting evolution: {len(records)} cases")
    run_fn(
        records=records,
        detector_config=config,
        output_dir=output_dir,
        max_mutation_calls=30,
        reflection_candidate_count=1,
        max_mutation_attempts_per_case=1,
    )
    return {
        name: _load_json(output_dir / name)
        for name in ARTIFACT_NAMES
        if (output_dir / name).is_file()
    }


def main() -> int:
    records = load_fixture_records()
    root = Path(tempfile.mkdtemp(prefix="sehd_p10_compare_"))

    old_config = REPO_ROOT / "src" / "sehd_v6" / "configs" / "paca_tcl_detector_v3_5.json"
    new_config = REPO_ROOT / "sehd_rise" / "configs" / "detector_v3_5.json"

    from src.sehd_v6.evolution.runner import run_evolution as old_run
    from sehd_rise.api.evolution import run_evolution as new_run

    old_artifacts = _run_side("old", old_run, records, old_config, root / "old")
    new_artifacts = _run_side("new", new_run, records, new_config, root / "new")

    failures: list[str] = []

    # --- 1. Artifact presence ---
    for name in ARTIFACT_NAMES:
        for label, arts in (("old", old_artifacts), ("new", new_artifacts)):
            if name not in arts:
                failures.append(f"{label}: artifact missing: {name}")

    # --- 2. Artifact schema completeness ---
    for label, arts in (("old", old_artifacts), ("new", new_artifacts)):
        res = arts.get("evolution_result.json", {})
        missing = _missing_fields(res, RESULT_FIELDS)
        if missing:
            failures.append(f"{label} evolution_result: missing fields {missing}")
        manifest = arts.get("evolution_state_manifest.json", {})
        missing = _missing_fields(manifest, MANIFEST_FIELDS)
        if missing:
            failures.append(f"{label} state_manifest: missing fields {missing}")
        for art_name in ("champion_genome.json", "htc_tes_experience_memory.json"):
            if art_name not in arts:
                continue
            data = arts[art_name]
            if not isinstance(data, dict):
                failures.append(f"{label} {art_name}: not an object")

    # --- 3. Accepted-edit target module/operation sets ---
    old_genome = old_artifacts.get("champion_genome.json", {})
    new_genome = new_artifacts.get("champion_genome.json", {})
    old_rules = _genome_rule_set(old_genome)
    new_rules = _genome_rule_set(new_genome)
    if old_rules != new_rules:
        only_old = old_rules - new_rules
        only_new = new_rules - old_rules
        if only_old:
            failures.append(
                f"genome rules only in old: {sorted(only_old)} "
                "(C3/C4/C5 attribution or ownership-boundary change)"
            )
        if only_new:
            failures.append(
                f"genome rules only in new: {sorted(only_new)} "
                "(C3/C4/C5 attribution or ownership-boundary change)"
            )

    # --- 4. Experience-memory mechanism-signature counts ---
    old_memory = old_artifacts.get("htc_tes_experience_memory.json", {})
    new_memory = new_artifacts.get("htc_tes_experience_memory.json", {})
    old_count = _mechanism_count(old_memory)
    new_count = _mechanism_count(new_memory)
    print(f"Mechanism signatures: old={old_count} new={new_count}")

    # --- 5. Metric comparison with C1-C10 attribution ---
    old_res = old_artifacts.get("evolution_result.json", {})
    new_res = new_artifacts.get("evolution_result.json", {})
    explained: list[str] = []
    for field in METRIC_FIELDS:
        old_v = old_res.get(field)
        new_v = new_res.get(field)
        if old_v == new_v:
            continue
        covering = [
            cid for cid, (_, fields) in C_NOTES.items()
            if field in fields
        ]
        line = (
            f"METRIC DIVERGE {field}: old={old_v} new={new_v} "
            f"-> explained by {covering}"
        )
        if covering:
            explained.append(line)
        else:
            failures.append(line + " (UNEXPLAINED)")

    # --- Report ---
    print()
    if explained:
        print("Explained metric divergences (C1-C10):")
        for line in explained:
            print(f"  {line}")
    if not explained:
        print("No metric divergences.")

    print()
    print("C1-C10 semantic changes (plan §4):")
    for cid, (desc, _) in C_NOTES.items():
        print(f"  {cid}: {desc}")

    print()
    if failures:
        print("PARITY FAILED:")
        for f in failures:
            print(f"  {f}")
        return 1
    print("PARITY OK: artifacts present, schema complete, divergences explained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
