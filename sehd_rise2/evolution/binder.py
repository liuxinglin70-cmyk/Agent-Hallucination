"""P1-C2 runtime certificate-class binder: the perception stage of L2.

The binder assigns every surviving certificate its class from detector
facts ONLY — the candidate claim, the certificate verdict and rationale,
the cited evidence refs, a projection of the referenced packet events and
the defense outcome. The binding semantics (input builder, closed-input
discipline, answer normalization) live in ``detector.artifacts`` — the
same implementation the runtime court uses — while this module drives
them over a recorded run tree, concurrently, with a resumable checkpoint.

``unclassified`` is always an available answer: an unsure binder falls
back to baseline adjudication (the policy does not fire) instead of
guessing — fail-closed perception, mirroring the detector's authority
design. A model answer outside the closed vocabulary is a hard error,
never a silent downgrade.

CLI (concurrent per-case calls, incremental output, resume by re-running):

    python -m sehd_rise2.evolution.binder bind \
        --run runs/random100_20260815_142446 \
        --out datasets/policy_candidates/v1/runtime_bindings.json
"""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.artifacts import (
    assert_label_free,
    bind_surviving,
    binding_entries,
)
from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.provider import ProviderError
from sehd_rise2.evolution.replay import surviving_ids

RUNTIME_BINDINGS_SCHEMA = "sehd2.certificate_bindings.runtime.v1"


def load_run_packets(run_dir: Path) -> dict[str, dict[str, Any]]:
    """case_id -> {result, packet} — the label-free run facts (no gold)."""
    tree: dict[str, dict[str, Any]] = {}
    for result_path in sorted((Path(run_dir) / "cases").glob(
        "*/result.json",
    )):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        case_id = str(result.get("case_id") or "")
        if not case_id:
            continue
        packet_path = result_path.parent / "packet.json"
        tree[case_id] = {
            "result": result,
            "packet": (
                json.loads(packet_path.read_text(encoding="utf-8"))
                if packet_path.exists() else {"events": []}
            ),
        }
    return tree


def binding_inputs(
    run_tree: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One label-free binding input per case: all its surviving
    certificates together, so co-convicting certificates are classified
    with full case context."""
    inputs: list[dict[str, Any]] = []
    for case_id in sorted(run_tree):
        result = run_tree[case_id]["result"]
        proof = result.get("proof_adjudication") or {}
        surviving = surviving_ids(result)
        if not surviving:
            continue
        entries = binding_entries(
            census=proof.get("census") or [],
            certificates=proof.get("certificates") or [],
            surviving=surviving,
            packet=run_tree[case_id]["packet"],
        )
        inputs.append({"case_id": case_id, "certificates": entries})
    return inputs


def bind_all(
    *, run_dir: Path, out_path: Path, limit: int | None = None,
    workers: int = 20,
) -> int:
    """Bind every case's surviving certificates, one call per case.

    Cases are independent, so calls run concurrently (``workers``);
    persistence is lock-guarded and the output file stays a valid
    resumable checkpoint after every completed case.
    """
    run_tree = load_run_packets(run_dir)
    if not run_tree:
        print(f"no cases found under {run_dir}")
        return 1
    inputs = binding_inputs(run_tree)
    assert_label_free(inputs)
    total = sum(
        len(case_input["certificates"]) for case_input in inputs
    )
    if limit is not None:
        inputs = inputs[:limit]
    done: dict[tuple[str, str], dict[str, str]] = {}
    if out_path.exists():
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        for entry in previous.get("bindings") or []:
            done[(entry["case_id"], entry["candidate_id"])] = dict(entry)
    config = load_config()
    route = dict(config["verification_route"])
    route["_route_name"] = "binder"
    lock = threading.Lock()

    def _persist() -> None:
        payload = {
            "schema_version": RUNTIME_BINDINGS_SCHEMA,
            "derived_from": {
                "run": str(run_dir),
                "class_definitions": (
                    "sehd_rise2/detector/artifacts.py::CERTIFICATE_CLASSES"
                ),
            },
            "binding_source": (
                f"runtime binder route {route.get('model')} "
                "(verification route, per-case batches, "
                "label-free inputs)"
            ),
            "review_status": "machine_generated",
            "bindings": [done[key] for key in sorted(done)],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _bind_case(case_input: dict[str, Any]) -> int:
        case_id = case_input["case_id"]
        pending = [
            entry for entry in case_input["certificates"]
            if (case_id, entry["candidate_id"]) not in done
        ]
        if not pending:
            return 0
        try:
            records, _ = bind_surviving(
                route=route, case_id=case_id, entries=pending,
            )
        except (ProviderError, ValueError) as exc:
            print(f"FAIL {case_id}: {exc}")
            return 1
        with lock:
            for record in records:
                done[(record["case_id"], record["candidate_id"])] = record
            _persist()
        print(
            f"bound {case_id}: "
            + ", ".join(
                f"{r['candidate_id']}={r['certificate_class']}"
                for r in records
            )
        )
        return 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        failures = sum(
            executor.map(_bind_case, inputs),
        )
    remaining = total - len(done)
    print(
        f"bound {len(done)}/{total} certificates "
        f"({max(remaining, 0)} remaining, {failures} case failures)"
    )
    return 1 if remaining > 0 or failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bind_parser = sub.add_parser(
        "bind", help="bind every surviving certificate (concurrent, resumable)",
    )
    bind_parser.add_argument("--run", type=Path, required=True)
    bind_parser.add_argument("--out", type=Path, required=True)
    bind_parser.add_argument(
        "--limit", type=int, default=None,
        help="bind only the first N certificates (smoke runs)",
    )
    bind_parser.add_argument(
        "--workers", type=int, default=20,
        help="concurrent per-case binder calls",
    )
    args = parser.parse_args()
    return bind_all(
        run_dir=args.run, out_path=args.out, limit=args.limit,
        workers=args.workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
