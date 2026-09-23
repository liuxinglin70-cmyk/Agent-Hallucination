"""Minimal batch runner: runs cases concurrently and records the run tree."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.api import Detector
from detector.config import load_config
from detector.dataset import load_case
from detector.result import RESULT_SCHEMA

DEFAULT_RUNS_ROOT = Path("artifacts/detector/runs")


def new_run_id(prefix: str = "detector") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"


def safe_case_id(case_id: str) -> str:
    return (
        str(case_id).replace("/", "__").replace("\\", "__")
        .replace(":", "_") or "case"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_transport_unresolved(result: Mapping[str, Any]) -> bool:
    if result.get("state") != "unresolved":
        return False
    reasons = result.get("authority_failure_reasons") or []
    return any(
        str(reason.get("error_type")) in {"provider_error", "invalid_json"}
        for reason in reasons
        if isinstance(reason, Mapping)
    )


def _cached_envelope_is_reusable(
    result: Mapping[str, Any], *, retry_unresolved: bool,
) -> bool:
    """Whether an already-written ``result.json`` may stand in for a fresh run.

    Two questions, and the old code asked neither. *Is this envelope ours?* --
    a file whose schema is not this runner's result schema is some other tool's
    output, and reusing it would silently mix two measurements in one run tree.
    *Is it a decision about the case?* -- an ``unresolved`` row records what
    the provider did during an outage, not what the detector says about the
    case, so a resume that treats it as finished freezes the batch at whatever
    the network happened to be doing in that hour. That is the difference
    between a resumable long run and a permanently contaminated one, and it is
    why re-running those rows is opt-in rather than assumed: a caller who wants
    the outage recorded keeps the default.

    The third question -- *was it produced by this configuration?* -- is
    answered by the run manifest, which is why a run directory that holds case
    results without one is refused rather than resumed: the fingerprint that
    would answer it is exactly what is missing.
    """
    if str(result.get("schema_version") or "") != RESULT_SCHEMA:
        raise ValueError(
            "cached result is not a "
            f"{RESULT_SCHEMA} envelope: found "
            f"{result.get('schema_version')!r}; refusing to reuse it"
        )
    if retry_unresolved and str(result.get("state") or "") == "unresolved":
        return False
    return True


def detect_with_transport_retry(
    detector: Detector,
    record: Mapping[str, Any],
    *,
    case_id: str,
    interventions: Mapping[str, Any] | None = None,
    attempts: int = 3,
) -> dict[str, Any]:
    """Whole-detection retry for transport-only unresolved results."""
    result: dict[str, Any] = {}
    for _ in range(max(1, attempts)):
        result = detector.detect(
            record, case_id=case_id, interventions=interventions,
        )
        if not _is_transport_unresolved(result):
            return result
    return result


def record_case(
    *,
    runs_root: str | Path,
    run_id: str,
    case_id: str,
    input_record: Mapping[str, Any],
    result: Mapping[str, Any],
    elapsed_seconds: float,
) -> Path:
    case_dir = Path(runs_root) / run_id / "cases" / safe_case_id(case_id)
    _write_json(case_dir / "input.json", input_record)
    _write_json(case_dir / "result.json", result)
    (case_dir / "elapsed_seconds.json").write_text(
        json.dumps({"elapsed_seconds": elapsed_seconds}) + "\n",
        encoding="utf-8",
    )
    return case_dir


def run_batch(
    *,
    dataset_root: str | Path,
    cases: Sequence[str],
    run_id: str | None = None,
    runs_root: str | Path = DEFAULT_RUNS_ROOT,
    config: Mapping[str, Any] | None = None,
    interventions: Mapping[str, Any] | None = None,
    workers: int = 8,
    retry_unresolved: bool = False,
) -> dict[str, Any]:
    """Run *cases* concurrently; skip cases whose result already exists.

    Resuming is only safe against a verified manifest, so the run directory's
    ``run.json`` is checked first: it carries the config, intervention and case
    fingerprints, and a mismatch is refused rather than partially reused. A run
    directory that holds case results but no manifest cannot be resumed at all
    (see ``_cached_envelope_is_reusable``).

    ``retry_unresolved`` re-runs cases whose cached row is ``unresolved``
    instead of accepting it as a finished case. A long batch that hit a
    provider outage should be resumable to a full-coverage measurement, and
    only the caller knows whether this resume is that one.
    """
    if len(cases) != len(set(map(str, cases))):
        raise ValueError("batch cases must be unique")
    normalized_paths = {}
    for case_id in cases:
        safe = safe_case_id(case_id)
        previous = normalized_paths.setdefault(safe, str(case_id))
        if previous != str(case_id):
            raise ValueError(
                f"case ids collide in result path {safe!r}: {previous!r} and {case_id!r}"
            )
    config = config or load_config()
    detector = Detector(config)
    run_id = run_id or new_run_id()
    case_root = Path(runs_root) / run_id / "cases"
    run_manifest = {
        "schema_version": "detector.run_manifest.v1",
        "run_id": run_id,
        "cases": list(cases),
        "config_hash": _fingerprint(config),
        "interventions_hash": (
            str(interventions.get("content_hash") or _fingerprint(interventions))
            if interventions is not None else None
        ),
    }
    manifest_path = Path(runs_root) / run_id / "run.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if previous != run_manifest:
            raise ValueError(
                f"run {run_id!r} already exists with a different manifest"
            )
    else:
        # Results without a manifest are results whose configuration cannot be
        # verified: the fingerprint that would say which config produced them
        # is the file that is missing. Writing a fresh manifest over them would
        # then *claim* the current config for rows it never produced, so this
        # is refused instead.
        orphaned = sorted(case_root.glob("*/result.json"))
        if orphaned:
            raise ValueError(
                f"run {run_id!r} holds {len(orphaned)} case result(s) but no "
                "run.json manifest; refusing to reuse results whose "
                "configuration cannot be verified. Restore run.json, or point "
                "the run at a new run_id."
            )
        _write_json(manifest_path, run_manifest)
    summaries: list[dict[str, Any]] = []

    def run(case_id: str) -> dict[str, Any]:
        out_path = case_root / safe_case_id(case_id) / "result.json"
        if out_path.exists():
            result = json.loads(out_path.read_text(encoding="utf-8-sig"))
            if str(result.get("case_id") or "") != str(case_id):
                raise ValueError(
                    f"cached result at {out_path} belongs to "
                    f"{result.get('case_id')!r}, not {case_id!r}"
                )
            if _cached_envelope_is_reusable(
                result, retry_unresolved=retry_unresolved,
            ):
                summaries.append(result)
                return result
            print(f"  [{case_id}] re-running cached {result.get('state')} row",
                  flush=True)
        record, _gold = load_case(dataset_root, case_id)
        started = time.monotonic()
        result = detect_with_transport_retry(
            detector, record, case_id=case_id,
            interventions=interventions,
        )
        record_case(
            runs_root=runs_root,
            run_id=run_id,
            case_id=case_id,
            input_record=record,
            result=result,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        summaries.append(result)
        print(
            f"  [{case_id}] state={result.get('state')} "
            f"authority={result.get('decision_authority')}",
            flush=True,
        )
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, case_id) for case_id in cases]
        for future in as_completed(futures):
            future.result()

    summary = {
        "schema_version": "detector.run_summary.v1",
        "run_id": run_id,
        "cases": list(cases),
        "completed": len(summaries),
        "state_counts": {
            state: sum(
                1 for row in summaries if row.get("state") == state
            )
            for state in ("verified_error", "verified_clean", "unresolved")
        },
    }
    _write_json(Path(runs_root) / run_id / "summary.json", summary)
    return summary
