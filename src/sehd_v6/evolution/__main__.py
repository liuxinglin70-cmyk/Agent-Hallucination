"""Run standalone self-evolution as ``python -m src.sehd_v6.evolution``.

Example:

    python -m src.sehd_v6.evolution \
      --records records.jsonl \
      --config src/sehd_v6/configs/paca_tcl_detector_v3_5.json \
      --output-dir evolution_output
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runner import DEFAULT_REFLECTION_CANDIDATES, run_evolution


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone detector -> self-evolution -> experience loop",
    )
    parser.add_argument(
        "--records", type=Path, required=True,
        help="JSON mapping or JSONL of raw trajectories",
    )
    parser.add_argument(
        "--config", type=Path, required=True,
        help="detector config; provider routes are resolved from env",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--census-root", type=Path, default=None,
        help="optional precomputed census cache",
    )
    parser.add_argument(
        "--mutation-route", type=Path, default=None,
        help="optional mutation route JSON; defaults to SEHD_MUTATION_* env",
    )
    parser.add_argument("--initial-genome", type=Path, default=None)
    parser.add_argument("--experience-memory", type=Path, default=None)
    parser.add_argument(
        "--resume-from", type=Path, default=None,
        help="resume an interrupted run from its checkpoint directory",
    )
    parser.add_argument("--use-gold", action="store_true")
    parser.add_argument("--fitness-use-gold", action="store_true")
    parser.add_argument(
        "--use-gold-reflection", action="store_true",
        help="explicit opt-in: expose evolution gold polarity to the mutation "
        "LLM as a bounded diagnostic reference (default: label-free)",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--module-cooldown", type=int, default=0)
    parser.add_argument("--max-edits", type=int, default=200)
    parser.add_argument("--convergence-window", type=int, default=20)
    parser.add_argument("--max-mutation-attempts", type=int, default=2)
    parser.add_argument(
        "--reflection-candidates", type=int,
        default=DEFAULT_REFLECTION_CANDIDATES,
        help="independent reflection samples per triggered case (1..5)",
    )
    parser.add_argument("--max-mutation-calls", type=int, default=100)
    parser.add_argument("--canary-limit", type=int, default=2)
    args = parser.parse_args()

    result = run_evolution(
        records=args.records,
        detector_config=args.config,
        output_dir=args.output_dir,
        census_root=args.census_root,
        mutation_route=args.mutation_route,
        initial_genome=args.initial_genome,
        experience_memory_path=args.experience_memory,
        use_gold=args.use_gold,
        fitness_use_gold=args.fitness_use_gold,
        gold_reflection=args.use_gold_reflection,
        run_id=args.run_id,
        resume_from=args.resume_from,
        module_cooldown=args.module_cooldown,
        max_total_edits=args.max_edits,
        convergence_window=args.convergence_window,
        max_mutation_attempts_per_case=args.max_mutation_attempts,
        reflection_candidate_count=args.reflection_candidates,
        max_mutation_calls=args.max_mutation_calls,
        canary_limit=args.canary_limit,
    )
    summary = {
        "schema_version": "agenthallu.evolution_run_summary.v1",
        "run_id": result.run_id,
        "output_dir": str(Path(args.output_dir)),
        "total_cases": result.total_cases,
        "triggered_cases": result.triggered_cases,
        "edits_accepted": result.edits_accepted,
        "edits_rejected": result.edits_rejected,
        "stable_cases": result.stable_cases,
        "halted": result.halted,
        "halt_reason": result.halt_reason,
        "champion_genome_hash": result.champion_genome.content_hash,
        "artifacts": [
            str(Path(args.output_dir) / name)
            for name in (
                "evolution_result.json",
                "champion_genome.json",
                "htc_tes_experience_memory.json",
                "htc_tes_coverage.json",
            )
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
