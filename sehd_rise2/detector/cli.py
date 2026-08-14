"""Run the standalone detector as ``python -m sehd_rise2.detector``.

Accepts one label-free trajectory record (``--record``), resolves the
detector config, and writes one prediction object to
``<output-dir>/prediction.json``. Evaluation labels must be joined by a
caller after detection, never supplied to this runtime.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sehd_rise2.detector.api import Detector
from sehd_rise2.detector.config import default_config_path, load_config
from sehd_rise2.detector.skills import load_policy_genome, load_skill_genome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=None,
        help="detector config JSON; defaults to the packaged detector_v1.json",
    )
    parser.add_argument(
        "--record", type=Path, required=True,
        help="one JSON trajectory record",
    )
    parser.add_argument("--case-id", default="case")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("detector_output"),
    )
    parser.add_argument(
        "--genome", type=Path, default=None,
        help="optional persisted skill genome JSON; loading is explicit and fail-closed",
    )
    parser.add_argument(
        "--policy-genome", type=Path, default=None,
        help="optional persisted policy genome JSON; loading is explicit and fail-closed",
    )
    args = parser.parse_args()
    config = load_config(
        args.config if args.config is not None else default_config_path(),
    )
    detector = Detector(config=config, require_credentials=False)
    genome = load_skill_genome(args.genome) if args.genome else None
    policy_genome = (
        load_policy_genome(args.policy_genome) if args.policy_genome else None
    )
    record = json.loads(args.record.read_text(encoding="utf-8"))
    result = detector.detect(
        record, case_id=args.case_id, genome=genome,
        policy_genome=policy_genome,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "prediction.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(output_path)
    print(f"state={result['state']} authority={result['decision_authority']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
