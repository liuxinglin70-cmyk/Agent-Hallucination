"""Run the standalone detector as ``python -m sehd_rise detect``.

Accepts one label-free trajectory record (``--record``) or a batch of
records (``--records``), resolves the detector routes from one config, and
returns one prediction object per record.  Evaluation labels must be joined
by a caller after detection, never supplied to this runtime.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sehd_rise.api.detection import (
    detect_record,
    load_batch_records,
    load_detector_config,
    load_policy_genome,
    load_skill_genome,
    run_batch,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--record", type=Path, help="one JSON trajectory record")
    source.add_argument(
        "--records", type=Path,
        help="JSON/JSONL records or directory of trajectory JSON files",
    )
    parser.add_argument("--case-id", default="case")
    parser.add_argument("--output-dir", type=Path, default=Path("detector_output"))
    parser.add_argument("--max-parallel", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--genome", type=Path, default=None,
        help="optional persisted SkillGenome JSON; loading is explicit and fail-closed",
    )
    parser.add_argument(
        "--policy-genome", type=Path, default=None,
        help="optional persisted L2 PolicyGenome JSON; loading is explicit and fail-closed",
    )
    args = parser.parse_args()
    config = load_detector_config(args.config)
    genome = load_skill_genome(args.genome) if args.genome is not None else None
    genome_source = str(args.genome.resolve()) if args.genome is not None else None
    policy_genome = (
        load_policy_genome(args.policy_genome)
        if args.policy_genome is not None else None
    )
    if args.records is not None:
        records = load_batch_records(args.records)
        if args.limit is not None:
            if args.limit < 1:
                parser.error("--limit must be at least 1")
            records = dict(list(records.items())[:args.limit])
        summary = run_batch(
            records=records, config=config, output_dir=args.output_dir,
            max_parallel=args.max_parallel, resume=not args.no_resume,
            genome=genome, genome_source=genome_source,
            policy_genome=policy_genome,
        )
        print(args.output_dir / "summary.json")
        return 0 if summary["failed_case_count"] == 0 else 2
    record = json.loads(Path(args.record).read_text(encoding="utf-8"))
    result = detect_record(
        record=record, config=config, case_id=args.case_id,
        output_dir=args.output_dir,
        genome=genome, genome_source=genome_source,
        policy_genome=policy_genome,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "prediction.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
