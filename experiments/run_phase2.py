"""Phase 2 orchestration: materialize a batch, detect, bind, evolve.

One invocation executes one full evolution cycle (or the verification
pass) as three sequential subprocess phases with explicit environments:

1. detector — the batch's not-yet-recorded cases through the frozen
   detector (the split manifest's route env, fingerprint-asserted,
   10 workers). Cases that already have a fingerprint-matched record
   from an earlier run are copied in, so every batch is a
   self-contained run tree (batches never overlap by construction).
2. binder — the flash binder over the batch's surviving certificates,
   in a scrubbed environment (the .env flash defaults).
3. cycle — ``sehd_rise2.evolution.cycle run``: worksheet, proposals,
   cumulative replay gate, promotion, library update.

Usage:
    python -m experiments.run_phase2 --cycle 1
    python -m experiments.run_phase2 --verification
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sehd_rise2.detector.packet import EVALUATOR_ONLY_FIELDS

MANIFEST = Path("datasets/eval_splits/phase2_v1/manifest.json")
DATASET_ROOT = Path("datasets/raw/agenthallu/AgentHallu")
LIBRARY = Path("datasets/policy_library/v1")
BATCH_SEED = 20260819
BATCH_COUNT = 5
WORKERS = 20
DETECTOR_ENV_KEYS = (
    "SEHD_ROUTE_CENSUS_MODEL",
    "SEHD_ROUTE_VERIFICATION_MODEL",
    "SEHD_ROUTE_DEFENSE_MODEL",
    "SEHD_ROUTE_VERIFICATION_TIMEOUT_SECONDS",
    "SEHD_ROUTE_DEFENSE_TIMEOUT_SECONDS",
)


def batches(manifest: dict) -> list[list[str]]:
    """Seeded shuffle of the non-forced evolution pool into 5 batches."""
    forced = set(manifest["evolution_pool"]["forced_in"]["cases"])
    pool = [
        case for case in manifest["evolution_pool"]["cases"]
        if case not in forced
    ]
    rng = random.Random(BATCH_SEED)
    shuffled = rng.sample(pool, len(pool))
    size, remainder = divmod(len(shuffled), BATCH_COUNT)
    sizes = [size + (1 if index < remainder else 0)
             for index in range(BATCH_COUNT)]
    batches: list[list[str]] = []
    offset = 0
    for count in sizes:
        batches.append(sorted(shuffled[offset:offset + count]))
        offset += count
    return batches


def existing_records(
    cases: list[str], fingerprint: str,
) -> dict[str, Path]:
    """case_id -> recorded case dir for fingerprint-matched earlier runs."""
    found: dict[str, Path] = {}
    wanted = set(cases)
    for meta_path in sorted(Path("runs").glob("*/run.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("config_fingerprint") != fingerprint:
            continue
        for case_id in meta.get("case_ids") or []:
            if case_id in wanted and case_id not in found:
                case_dir = (
                    meta_path.parent / "cases"
                    / case_id.replace("/", "__")
                )
                if (case_dir / "result.json").exists():
                    found[case_id] = case_dir
    return found


def materialize_batch(
    *, run_id: str, cases: list[str], fingerprint: str,
) -> tuple[Path, list[str]]:
    """Copy recorded cases in; return (run dir, cases left to detect)."""
    run_dir = Path("runs") / run_id
    recorded = existing_records(cases, fingerprint)
    for case_id, case_dir in sorted(recorded.items()):
        target = run_dir / "cases" / case_id.replace("/", "__")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(case_dir, target)
    return run_dir, sorted(set(cases) - set(recorded))


def load_case(case_path: str) -> tuple[dict, bool | None]:
    raw = json.loads((DATASET_ROOT / case_path).read_text(encoding="utf-8"))
    value = raw.get("is_hallucination")
    gold = value if isinstance(value, bool) else {
        "true": True, "false": False,
    }.get(str(value).strip().lower())
    clean = {
        key: item for key, item in raw.items()
        if key not in EVALUATOR_ONLY_FIELDS
    }
    return clean, gold


def gold_join(run_dir: Path, cases: list[str]) -> None:
    from sehd_rise2.detector.result import prediction_state
    rows = []
    for case in sorted(cases):
        case_dir = run_dir / "cases" / case.replace("/", "__")
        result = json.loads(
            (case_dir / "result.json").read_text(encoding="utf-8"),
        )
        _, gold = load_case(case)
        rows.append({
            "case": case,
            "gold": gold,
            "state": result.get("state"),
            "prediction": prediction_state(result),
        })
    decisive = [row for row in rows if row["prediction"] is not None]
    correct = sum(1 for row in decisive if row["prediction"] == row["gold"])
    (run_dir / "eval_gold.json").write_text(
        json.dumps({
            "schema_version": "sehd2.eval_gold.v1",
            "run_id": run_dir.name,
            "cases": rows,
            "decisive_cases": len(decisive),
            "polarity_accuracy_decisive": (
                round(correct / len(decisive), 4) if decisive else None
            ),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def phase_detector(
    run_dir: Path, pending: list[str], env: dict[str, str],
) -> None:
    if not pending:
        return
    cases_file = run_dir.parent / f"{run_dir.name}_pending.json"
    cases_file.write_text(
        json.dumps(pending, ensure_ascii=False), encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable, "-m", "experiments.run_p1d",
            "--cases-file", str(cases_file),
            "--run-id", run_dir.name,
            "--manifest", str(MANIFEST),
            "--workers", str(WORKERS),
            "--detect-only",
        ],
        env=env, check=True, cwd=Path.cwd(),
    )
    cases_file.unlink()


def phase_binder(run_dir: Path, bindings_out: Path) -> None:
    scrubbed = {
        key: value for key, value in os.environ.items()
        if key not in DETECTOR_ENV_KEYS
    }
    subprocess.run(
        [
            sys.executable, "-m", "sehd_rise2.evolution.binder", "bind",
            "--run", str(run_dir), "--out", str(bindings_out),
        ],
        env=scrubbed, check=True, cwd=Path.cwd(),
    )


def phase_cycle(
    run_dir: Path, bindings_out: Path, prior_runs: list[Path],
    prior_bindings: list[Path],
) -> None:
    command = [
        sys.executable, "-m", "sehd_rise2.evolution.cycle", "run",
        "--library", str(LIBRARY),
        "--batch-run", str(run_dir),
        "--batch-bindings", str(bindings_out),
    ]
    for run in prior_runs:
        command += ["--prior-run", str(run)]
    for bindings in prior_bindings:
        command += ["--prior-bindings", str(bindings)]
    scrubbed = {
        key: value for key, value in os.environ.items()
        if key not in DETECTOR_ENV_KEYS
    }
    subprocess.run(command, env=scrubbed, check=True, cwd=Path.cwd())


def batch_artifacts(cycle: int) -> tuple[list[Path], list[Path]]:
    """Prior batch dirs and binding files from the library ledger."""
    library = json.loads((LIBRARY / "library.json").read_text(encoding="utf-8"))
    runs: list[Path] = []
    bindings: list[Path] = []
    for record in library["cycles"]:
        runs.append(Path(record["batch_run"]))
        bindings.append(Path(record["batch_bindings"]))
    return runs, bindings


def detector_env(manifest: dict) -> dict[str, str]:
    deployment = manifest["deployment"]["detector"]
    env = dict(os.environ)
    env.update(deployment["env"])
    return env


def verify_fingerprint(manifest: dict, env: dict[str, str]) -> None:
    script = (
        "from sehd_rise2.detector.config import load_config\n"
        "from sehd_rise2.detector.runs import config_fingerprint\n"
        "print(config_fingerprint(load_config()))"
    )
    probe = subprocess.run(
        [sys.executable, "-c", script], env=env, check=True,
        capture_output=True, text=True, cwd=Path.cwd(),
    )
    fingerprint = probe.stdout.strip()
    expected = manifest["deployment"]["detector"]["config_fingerprint"]
    if fingerprint != expected:
        raise SystemExit(
            f"fingerprint {fingerprint} != frozen {expected}",
        )


def finalize_batch(
    run_dir: Path, cases: list[str], fingerprint: str,
) -> None:
    """The orchestrator owns the batch meta: full case list + fingerprint."""
    from datetime import datetime, timezone
    (run_dir / "run.json").write_text(
        json.dumps({
            "schema_version": "sehd2.run_record.v1",
            "run_id": run_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_fingerprint": fingerprint,
            "case_ids": sorted(cases),
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    from sehd_rise2.detector.runs import summarize_run
    summarize_run("runs", run_dir.name)
    gold_join(run_dir, cases)


def run_cycle_pipeline(cycle: int, manifest: dict) -> None:
    batch_cases = batches(manifest)[cycle - 1]
    run_id = f"phase2_c{cycle}_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir, pending = materialize_batch(
        run_id=run_id, cases=batch_cases,
        fingerprint=manifest["deployment"]["detector"][
            "config_fingerprint"
        ],
    )
    env = detector_env(manifest)
    verify_fingerprint(manifest, env)
    print(
        f"cycle {cycle}: {len(batch_cases)} cases "
        f"({len(batch_cases) - len(pending)} reused, {len(pending)} live)"
        f" -> {run_dir}", flush=True,
    )
    phase_detector(run_dir, pending, env)
    finalize_batch(
        run_dir, batch_cases,
        manifest["deployment"]["detector"]["config_fingerprint"],
    )
    bindings_out = LIBRARY / "bindings" / f"c{cycle}.json"
    phase_binder(run_dir, bindings_out)
    prior_runs, prior_bindings = batch_artifacts(cycle)
    phase_cycle(run_dir, bindings_out, prior_runs, prior_bindings)


def run_verification(manifest: dict) -> Path:
    cases = [
        str(row["case_id"])
        for row in manifest["final_verification"]["cases"]
    ]
    run_id = f"phase2_verify_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir, pending = materialize_batch(
        run_id=run_id, cases=cases,
        fingerprint=manifest["deployment"]["detector"][
            "config_fingerprint"
        ],
    )
    env = detector_env(manifest)
    verify_fingerprint(manifest, env)
    print(
        f"verification: {len(cases)} cases "
        f"({len(cases) - len(pending)} reused, {len(pending)} live)"
        f" -> {run_dir}", flush=True,
    )
    phase_detector(run_dir, pending, env)
    finalize_batch(
        run_dir, cases,
        manifest["deployment"]["detector"]["config_fingerprint"],
    )
    phase_binder(run_dir, LIBRARY / "bindings" / "verification.json")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--cycle", type=int)
    target.add_argument("--verification", action="store_true")
    parser.add_argument("--init-library", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if args.init_library:
        subprocess.run(
            [
                sys.executable, "-m", "sehd_rise2.evolution.cycle", "init",
                "--library", str(LIBRARY),
            ],
            check=True, cwd=Path.cwd(),
        )
        return 0
    if args.verification:
        run_dir = run_verification(manifest)
        print(f"verification run ready -> {run_dir}")
        return 0
    if not (LIBRARY / "library.json").exists():
        raise SystemExit("library missing: run --init-library first")
    if not 1 <= args.cycle <= BATCH_COUNT:
        raise SystemExit(f"--cycle must be 1..{BATCH_COUNT}")
    run_cycle_pipeline(args.cycle, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
