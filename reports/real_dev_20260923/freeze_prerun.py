"""Freeze the pre-run identity of the development-loop real experiment.

    python reports/real_dev_20260923/freeze_prerun.py

Run this BEFORE starting the cycle. It writes ``prerun_state.json`` with the
digests the run's own reports cite, so a reader can check after the fact that
the code, configuration and detector config that produced the readings are the
ones recorded here. Nothing is measured and no service is called.

Exit code is non-zero if a work root for this run already exists, because the
whole point of the file is that it describes a state that preceded the run.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evolution.backends import file_digest, source_digest  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "prerun_state_run2.json"
CONFIG = "evolution/configs/selfevo_real_dev2_20260923.json"


def main() -> int:
    config_path = REPO / CONFIG
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    work_root = REPO / str(config["work_root"])
    # What makes this state "pre-run" is that no measurement has happened yet,
    # and the evidence of a measurement is the ledger or a checkpoint -- not the
    # directory. ``ensure_dirs`` creates the work root (and an empty registry)
    # for any code path that loads a config, a read-only inspection included, so
    # keying on the directory would refuse to freeze a genuinely untouched
    # state. Keying on run evidence asks the question this guard exists to ask.
    run_evidence = [
        work_root / "ledger.jsonl",
        work_root / "checkpoints",
        work_root / "cycles" / "cycle001.json",
    ]
    present = [path for path in run_evidence if path.exists()]
    if present:
        print(
            f"refusing to freeze: run evidence already present under {work_root} "
            f"({[str(path.relative_to(REPO)) for path in present]}), so this "
            "state did not precede the run", file=sys.stderr,
        )
        return 2
    # The digest the plan's §23.8 pipeline cites is over the execution
    # semantics: detector + evolution source, tests excluded.
    source = source_digest()
    try:
        git_head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        git_head = "unknown"
    state = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "pre-run identity for the cycle-side development stage's first real "
            "run; written before the cycle started"
        ),
        "run_root": str(config["work_root"]),
        "run_config": CONFIG,
        "run_config_sha256": file_digest(config_path),
        "detector_config": config["detector_config"],
        "detector_config_sha256": file_digest(REPO / config["detector_config"]),
        "source_digest": source,
        "source_scope": "detector/** + evolution/** (.py, tests excluded)",
        "git_head": git_head,
        "split": config["split"],
        "budget": config["budget"],
        "gates": config["gates"],
        "development": config["development"],
        "archive": config["archive"],
        "detector_model": (
            json.loads(
                (REPO / config["detector_config"]).read_text(encoding="utf-8-sig")
            ).get("provider", {}).get("model")
        ),
        "config_body": config,
    }
    state["body_sha256"] = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode("utf-8"),
    ).hexdigest()
    OUT.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        key: state[key] for key in (
            "recorded_at_utc", "run_root", "run_config_sha256",
            "detector_config_sha256", "source_digest", "git_head",
            "detector_model",
        )
    }, indent=2, ensure_ascii=False))
    print(f"\nfrozen -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
