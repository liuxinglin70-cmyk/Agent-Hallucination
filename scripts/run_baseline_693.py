#!/usr/bin/env python
"""Full-corpus baseline measurement over the 693-case AgentHallu development set.

This is the entry point that finally measures the detector on more than the ten
cases the archived runs happened to cover. It owns no execution logic of its
own: the batch itself is ``detector.runner.run_batch`` (concurrency, per-case
``result.json``, the run manifest and its fingerprint, resume-by-existing-result
all live there), and the scoring is ``detector.evaluation.evaluate_run`` (whose
main line counts only ``authoritative`` decisions and lists degraded rows
separately). What this file adds is the three things a full-corpus measurement
needs and neither of those has:

1. **Case-list reconciliation.** The 693 ids come from
   ``datasets/protocols/development_exposure.json`` (the exposure manifest the
   evolution framework already treats as the development corpus), and the
   directory walk of ``dataset_root`` is compared against it field by field --
   missing files, extra files, duplicates, and ``safe_case_id`` path
   collisions. A list that disagrees with the corpus is a measurement of a
   different corpus, so the disagreement is reported rather than smoothed over.
2. **Coverage accounting.** ``unresolved`` / ``degraded`` rows are the
   difference between "the detector answered" and "the provider did"; the
   report separates them, and never lets an unanswered case read as an answer.
3. **Per-case wall clock.** The batch total is decided by its slowest case, so
   the report carries p50/p90/max and the five slowest cases by name.

Only ``datasets/`` is read, never written. Nothing here calls a model service:
the detector is whatever ``detector.runner.Detector`` is at call time, and
``--detector-factory`` substitutes a fake for it. That flag is the documented
injection point for the offline dry run, and it exists because the pytest HTTP
guard covers pytest only -- a script that constructs a real detector would
reach the provider. See ``tests/test_baseline_693_dryrun.py``.

Resume: re-invoking with the same ``--run-id`` and ``--runs-root`` skips every
case whose ``result.json`` is already there, provided ``run.json`` still
matches (config, interventions, case list). ``--retry-unresolved`` re-runs the
rows a provider outage left undecided instead of accepting them as answers,
which is what turns a partial run into a full-coverage one.

    # offline dry run over the real 693-case list, no provider call
    python scripts/run_baseline_693.py --detector-factory tests...:Fake

    # the real thing (needs credentials; hours, not minutes -- see --workers)
    python scripts/run_baseline_693.py --workers 8 \
        --run-id baseline693 --runs-root artifacts/baseline/runs
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector.config import load_config
from detector.dataset import load_case
from detector.evaluation import binary_metrics, evaluate_run
from detector.packet import build_packet, validate_record
from detector.runner import run_batch, safe_case_id

REPORT_SCHEMA = "detector.baseline_report.v1"
DEFAULT_MANIFEST = ROOT / "datasets" / "protocols" / "development_exposure.json"
DEFAULT_DATASET_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
GOLD_ERROR = "error"
GOLD_CLEAN = "clean"


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    """Linear-interpolation percentile (the convention ``numpy`` defaults to).

    Percentiles over a handful of rows are read as *estimates of a tail*, so
    the interpolation rule is stated instead of left implicit: with n=1 both
    p50 and p90 are that single value, and with n>=2 the result never leaves
    the observed range.
    """
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * weight


def load_case_list(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    dataset_root: str | Path = DEFAULT_DATASET_ROOT,
    manifest_key: str = "cases",
) -> dict[str, Any]:
    """Reconcile the exposure manifest's case list with the corpus on disk.

    Returns the reconciled list plus every difference, under keys that say
    which side is which. ``order_identical`` is reported because a reordered
    list is not a defect but is worth noticing: the run summary prints cases in
    the order given, so a diff of two summaries only lines up if the order does.
    """
    manifest_path = Path(manifest_path)
    dataset_root = Path(dataset_root)
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest_key not in payload:
        raise ValueError(
            f"{manifest_path} has no {manifest_key!r} key; keys are "
            f"{sorted(payload)}"
        )
    manifest_cases: list[str] = [str(item) for item in payload[manifest_key]]

    if not dataset_root.is_dir():
        raise FileNotFoundError(f"dataset root does not exist: {dataset_root}")
    disk_cases = sorted(
        path.relative_to(dataset_root).as_posix()
        for path in dataset_root.glob("*/*.json")
    )

    duplicates = sorted(
        {case for case in manifest_cases if manifest_cases.count(case) > 1}
    )
    collisions: dict[str, list[str]] = {}
    for case in manifest_cases:
        collisions.setdefault(safe_case_id(case), []).append(case)
    path_collisions = {
        safe: sorted(set(cases))
        for safe, cases in sorted(collisions.items())
        if len(set(cases)) > 1
    }
    sources: dict[str, int] = {}
    for case in manifest_cases:
        sources[case.split("/")[0]] = sources.get(case.split("/")[0], 0) + 1
    return {
        "manifest_path": str(manifest_path),
        "dataset_root": str(dataset_root),
        "manifest_cases": len(manifest_cases),
        "disk_cases": len(disk_cases),
        "manifest_unique": len(set(manifest_cases)),
        "disk_unique": len(set(disk_cases)),
        "missing_on_disk": sorted(set(manifest_cases) - set(disk_cases)),
        "not_in_manifest": sorted(set(disk_cases) - set(manifest_cases)),
        "duplicates": duplicates,
        "path_collisions": path_collisions,
        "order_identical": manifest_cases == disk_cases,
        "source_counts": dict(sorted(sources.items())),
        "cases": manifest_cases,
    }


def preflight_cases(
    *,
    dataset_root: str | Path,
    cases: Sequence[str],
) -> dict[str, Any]:
    """Prove every id in the list is a case the detector can actually be given.

    Reading the file and running the record contract is the difference between
    "the list has 693 strings" and "693 cases are measurable". A record that
    fails ``validate_record`` would fail inside the batch, one case at a time,
    an hour into a run.
    """
    dataset_root = Path(dataset_root)
    unreadable: list[dict[str, str]] = []
    invalid: list[dict[str, str]] = []
    event_counts: list[int] = []
    for case in cases:
        try:
            record, _gold = load_case(dataset_root, case)
        except Exception as exc:  # noqa: BLE001 - the point is to name any failure
            unreadable.append({"case": case, "error": f"{type(exc).__name__}: {exc}"})
            continue
        try:
            validate_record(record)
        except Exception as exc:  # noqa: BLE001
            invalid.append({"case": case, "error": f"{type(exc).__name__}: {exc}"})
            continue
        event_counts.append(len(build_packet(record).get("events") or []))
    return {
        "checked": len(cases),
        "unreadable": unreadable,
        "invalid_records": invalid,
        "events": {
            "min": min(event_counts) if event_counts else None,
            "max": max(event_counts) if event_counts else None,
            "mean": (
                round(sum(event_counts) / len(event_counts), 2)
                if event_counts else None
            ),
        },
    }


def _case_elapsed(runs_root: Path, run_id: str, case: str) -> float | None:
    path = (
        runs_root / run_id / "cases" / safe_case_id(case) / "elapsed_seconds.json"
    )
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return None
    value = payload.get("elapsed_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _case_usage(runs_root: Path, run_id: str, case: str) -> dict[str, Any] | None:
    """Tokens one case burned, summed over its stages.

    ``usage`` under a stage is the *last ballot's* provider meta, not the
    stage's spend; the summed vote lives beside it as ``usage_total``
    (``detector.provider.accumulate_usage``). One archived real row reads
    4948 against 17359 -- a 3.5x undercount -- and the burn is the one number a
    run's cost has to be sized from, so ``usage_total`` wins wherever it is
    present and ``usage`` is only the fallback for a non-voting stage. Summing
    both would double-count the final ballot, so exactly one is read per stage.
    """
    path = runs_root / run_id / "cases" / safe_case_id(case) / "result.json"
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return None
    total: dict[str, Any] = {}
    found = False
    for provider in (result.get("providers") or {}).values():
        if not isinstance(provider, dict):
            continue
        usage = provider.get("usage_total") or provider.get("usage") or {}
        if not usage:
            continue
        found = True
        _fold_usage(total, usage)
    return total if found else None


def _fold_usage(total: dict[str, Any], usage: Mapping[str, Any]) -> None:
    """Add every numeric leaf of *usage* into *total*, keyed by its path.

    Deliberately path-agnostic: providers report nested details blocks
    (``completion_tokens_details.reasoning_tokens`` and friends) and a new
    counter must be totalled without a code change here, which is the same
    bargain ``detector.provider.accumulate_usage`` strikes.
    """
    for key, value in usage.items():
        if isinstance(value, Mapping):
            nested = total.setdefault(str(key), {})
            if isinstance(nested, dict):
                _fold_usage(nested, value)
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)):
            total[str(key)] = total.get(str(key), 0) + value


def _case_usage_missing(runs_root: Path, run_id: str, case: str) -> int:
    """Provider attempts of one case that returned no usage metadata.

    A transport failure can burn provider tokens and still report nothing, so
    these attempts are absent from ``_case_usage`` rather than zero inside it.
    Counted separately, because the difference between "this case cost nothing"
    and "this case's cost was not reported" is the difference between a total
    and a lower bound.
    """
    path = runs_root / run_id / "cases" / safe_case_id(case) / "result.json"
    if not path.exists():
        return 0
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return 0
    execution = result.get("execution") or {}
    value = execution.get("usage_missing_attempts")
    return int(value) if isinstance(value, (int, float)) else 0


def usage_report(
    *,
    runs_root: str | Path,
    run_id: str,
    cases: Sequence[str],
) -> dict[str, Any]:
    """Token burn for the run, per case and in total.

    Reported because it has been missing from every archived real run: the
    §30/§31 reports carry ``provider_usage: {}``, which forced the 693-case
    cost model to lean on four hand-transcribed prose readings that make up 61%
    of its sample minutes and move the workers=8 work floor between 9.4h and
    21.3h. A measurement whose cost is unknown is a measurement that cannot be
    scheduled.

    The total is a floor, not the spend: attempts that failed in transport
    report no counters at all, and their tokens are missing from the sum rather
    than counted as zero. ``attempts_without_usage`` carries that count so a
    reader can see the total is a lower bound instead of having to reconstruct
    it from the per-case files.
    """
    runs_root = Path(runs_root)
    per_case: list[dict[str, Any]] = []
    total: dict[str, Any] = {}
    missing_attempts = 0
    cases_with_missing: list[str] = []
    for case in cases:
        missing = _case_usage_missing(runs_root, run_id, case)
        if missing:
            missing_attempts += missing
            cases_with_missing.append(case)
        usage = _case_usage(runs_root, run_id, case)
        if usage is None:
            continue
        per_case.append({"case": case, "usage": usage})
        _fold_usage(total, usage)
    values = [
        float(entry["usage"]["total_tokens"])
        for entry in per_case
        if isinstance(entry["usage"].get("total_tokens"), (int, float))
    ]
    return {
        "measured_cases": len(per_case),
        "unmeasured_cases": len(cases) - len(per_case),
        "total": total or None,
        "total_tokens": int(sum(values)) if values else None,
        "mean_tokens_per_case": (
            round(sum(values) / len(values), 1) if values else None
        ),
        "max_tokens_case": (
            max(
                (
                    (float(entry["usage"]["total_tokens"]), entry["case"])
                    for entry in per_case
                    if isinstance(entry["usage"].get("total_tokens"), (int, float))
                ),
                default=None,
            )
        ),
        "attempts_without_usage": missing_attempts,
        "cases_with_missing_usage": len(cases_with_missing),
        "total_is_lower_bound": bool(missing_attempts),
        "scope": (
            f"Sum of provider counters actually returned. {missing_attempts} "
            "provider attempt(s) across "
            f"{len(cases_with_missing)} case(s) returned no usage metadata "
            "(transport failures), so the true spend is at or above this total."
            if missing_attempts
            else "Sum of provider counters actually returned; every attempt "
                 "reported usage metadata."
        ),
        "per_case": per_case,
    }


def timing_report(
    *,
    runs_root: str | Path,
    run_id: str,
    cases: Sequence[str],
    slowest: int = 5,
) -> dict[str, Any]:
    """Per-case wall clock: the distribution, and the cases that set it."""
    runs_root = Path(runs_root)
    measured: list[tuple[str, float]] = []
    for case in cases:
        elapsed = _case_elapsed(runs_root, run_id, case)
        if elapsed is not None:
            measured.append((case, elapsed))
    values = [elapsed for _case, elapsed in measured]
    ranked = sorted(measured, key=lambda item: (-item[1], item[0]))
    return {
        "measured_cases": len(measured),
        "unmeasured_cases": len(cases) - len(measured),
        "sum_seconds": round(sum(values), 1) if values else None,
        "mean_seconds": (
            round(sum(values) / len(values), 1) if values else None
        ),
        "min_seconds": round(min(values), 1) if values else None,
        "p50_seconds": _round_or_none(_percentile(values, 0.50)),
        "p90_seconds": _round_or_none(_percentile(values, 0.90)),
        "max_seconds": round(max(values), 1) if values else None,
        "slowest": [
            {"case": case, "elapsed_seconds": round(elapsed, 1)}
            for case, elapsed in ranked[: max(0, int(slowest))]
        ],
    }


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _gold_key(gold: bool) -> str:
    return GOLD_ERROR if gold else GOLD_CLEAN


def _rows_by(
    rows: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(key(row), []).append(row)
    return dict(sorted(grouped.items()))


def build_report(
    *,
    dataset_root: str | Path,
    runs_root: str | Path,
    run_id: str,
    cases: Sequence[str],
    case_list: Mapping[str, Any],
    batch_summary: Mapping[str, Any] | None,
    slowest: int = 5,
) -> dict[str, Any]:
    """Score a run tree into the full-corpus report.

    Split out from ``run_baseline`` so a run that already exists can be scored
    again -- after a crash, or after a resume added rows -- without re-running
    anything.
    """
    dataset_root = Path(dataset_root)
    runs_root = Path(runs_root)
    evaluation = evaluate_run(
        runs_root=runs_root,
        run_id=run_id,
        dataset_root=dataset_root,
        cases=list(cases),
    )
    rows = evaluation["rows"]
    metrics = evaluation["metrics"]
    elapsed = {
        row["case"]: _case_elapsed(runs_root, run_id, row["case"])
        for row in rows
    }
    decorated = [
        {
            **row,
            "source": str(row["case"]).split("/")[0],
            "elapsed_seconds": elapsed.get(row["case"]),
        }
        for row in rows
    ]
    by_source = {
        source: {
            "n": len(group),
            "metrics": binary_metrics(group),
        }
        for source, group in _rows_by(rows, lambda r: str(r["case"]).split("/")[0]).items()
    }
    by_gold = {
        key: {"n": len(group), "metrics": binary_metrics(group)}
        for key, group in _rows_by(rows, lambda r: _gold_key(bool(r["gold"]))).items()
    }
    return {
        "schema_version": REPORT_SCHEMA,
        "run_id": run_id,
        "dataset_root": str(dataset_root),
        "runs_root": str(runs_root),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_list": dict(case_list),
        "batch": dict(batch_summary) if batch_summary is not None else None,
        "metrics": metrics,
        "by_source": by_source,
        "by_gold": by_gold,
        "coverage": {
            "requested_cases": len(cases),
            "found_results": metrics["found_results"],
            "missing_cases": evaluation["missing_cases"],
            "authoritative": metrics["authoritative"],
            "degraded": metrics["degraded"],
            "degraded_disagreeing": metrics["degraded_disagreeing"],
            "no_terminal_decision": metrics["no_terminal_decision"],
            "provider_unresolved": metrics["provider_unresolved"],
            # ``metrics["unresolved"]`` is ``decisive``'s complement, so it
            # includes the degraded rows -- they produced a verdict, just not an
            # authoritative one. Said out loud here because "unresolved=67" next
            # to "provider_unresolved=0" otherwise reads like a contradiction.
            "undecided_decomposition": {
                "metrics_unresolved": metrics["unresolved"],
                "degraded_rows_with_a_non_authoritative_answer": metrics["degraded"],
                "rows_with_no_answer_at_all": metrics["no_terminal_decision"],
                "identity": (
                    "metrics.unresolved == degraded + no_terminal_decision"
                    " + degraded rows that were also wrong (already inside"
                    " degraded)"
                ),
            },
            "state_counts": {
                state: sum(1 for row in rows if row["state"] == state)
                for state in sorted({str(row["state"]) for row in rows})
            },
            "authority_counts": {
                authority: sum(1 for row in rows if row["authority"] == authority)
                for authority in sorted(
                    {str(row["authority"]) for row in rows}
                )
            },
        },
        "timing": timing_report(
            runs_root=runs_root, run_id=run_id, cases=list(cases), slowest=slowest,
        ),
        "usage": usage_report(
            runs_root=runs_root, run_id=run_id, cases=list(cases),
        ),
        "errors": evaluation["errors"],
        "degraded_disagreements": evaluation["degraded_disagreements"],
        "rows": decorated,
    }


@contextlib.contextmanager
def _detector_factory(factory: Callable[..., Any] | None) -> Iterable[None]:
    """Substitute the class ``run_batch`` constructs its detector from.

    This is the injection point for an offline run. ``run_batch`` builds its
    own detector from ``detector.runner.Detector``, so a caller cannot pass one
    in; patching that one name is the whole mechanism, and it is deliberately
    the only place a fake can enter. A script that wants to prove "no provider
    was called" needs this, because the repository's HTTP guard lives in the
    pytest conftest and does not cover a plain ``python scripts/...``.
    """
    if factory is None:
        yield
        return
    import detector.runner as runner_module

    original = runner_module.Detector
    runner_module.Detector = factory  # type: ignore[assignment]
    try:
        yield
    finally:
        runner_module.Detector = original  # type: ignore[assignment]


def run_baseline(
    *,
    dataset_root: str | Path = DEFAULT_DATASET_ROOT,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    run_id: str,
    runs_root: str | Path,
    report_path: str | Path | None = None,
    workers: int = 8,
    config: Mapping[str, Any] | None = None,
    interventions: Mapping[str, Any] | None = None,
    retry_unresolved: bool = False,
    detector_factory: Callable[..., Any] | None = None,
    skip_preflight: bool = False,
    slowest: int = 5,
) -> dict[str, Any]:
    """Reconcile, run/resume the batch, score it, and write the report."""
    case_list = load_case_list(
        manifest_path=manifest_path, dataset_root=dataset_root,
    )
    if case_list["missing_on_disk"] or case_list["duplicates"] or case_list["path_collisions"]:
        raise ValueError(
            "case list does not reconcile with the corpus: "
            f"{len(case_list['missing_on_disk'])} missing, "
            f"{len(case_list['duplicates'])} duplicated, "
            f"{len(case_list['path_collisions'])} path collision(s)"
        )
    cases: list[str] = list(case_list["cases"])

    preflight = None
    if not skip_preflight:
        preflight = preflight_cases(dataset_root=dataset_root, cases=cases)
        if preflight["unreadable"] or preflight["invalid_records"]:
            raise ValueError(
                "case list contains records the detector cannot be given: "
                f"{len(preflight['unreadable'])} unreadable, "
                f"{len(preflight['invalid_records'])} invalid"
            )

    with _detector_factory(detector_factory):
        batch_summary = run_batch(
            dataset_root=dataset_root,
            cases=cases,
            run_id=run_id,
            runs_root=runs_root,
            # A fake brings its own behaviour; letting ``run_batch`` fall back
            # to ``load_config()`` here would make the offline path demand real
            # credentials and fingerprint the real routes into the manifest of
            # a run that called nothing. The stub keeps the manifest honest and
            # the dry run credential-free.
            config=(
                config if config is not None
                else ({"offline_dry_run": True} if detector_factory is not None
                      else None)
            ),
            interventions=interventions,
            workers=workers,
            retry_unresolved=retry_unresolved,
        )

    report = build_report(
        dataset_root=dataset_root,
        runs_root=runs_root,
        run_id=run_id,
        cases=cases,
        case_list=case_list,
        batch_summary=batch_summary,
        slowest=slowest,
    )
    report["preflight"] = preflight
    report["sampled_at_utc"] = datetime.now(timezone.utc).isoformat()
    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
    return report


def import_factory(spec: str) -> Callable[..., Any]:
    """Resolve ``module:attribute`` to the callable a fake detector lives at."""
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise SystemExit(
            "--detector-factory must look like 'package.module:ClassName'"
        )
    import importlib

    module = importlib.import_module(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise SystemExit(f"{spec}: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--run-id", default=time.strftime("baseline693_%Y%m%d"))
    parser.add_argument("--runs-root", default=str(ROOT / "artifacts" / "baseline" / "runs"))
    parser.add_argument("--report", default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--config", default=None,
                        help="detector config JSON; default is detector/config.py's default")
    parser.add_argument("--interventions", default=None)
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--slowest", type=int, default=5)
    parser.add_argument("--list-only", action="store_true",
                        help="reconcile the case list and stop; runs nothing")
    parser.add_argument("--detector-factory", default=None,
                        help="'module:Class' replacing detector.runner.Detector;"
                             " the offline injection point")
    args = parser.parse_args(argv)

    case_list = load_case_list(
        manifest_path=args.manifest, dataset_root=args.dataset_root,
    )
    print(json.dumps({
        key: case_list[key] for key in (
            "manifest_path", "dataset_root", "manifest_cases", "disk_cases",
            "manifest_unique", "disk_unique", "missing_on_disk",
            "not_in_manifest", "duplicates", "path_collisions",
            "order_identical", "source_counts",
        )
    }, ensure_ascii=False, indent=2), flush=True)
    if args.list_only:
        return 0

    config = None
    if args.detector_factory is None:
        # Only a real run needs real routes: the fake brings its own config,
        # and requiring credentials for an offline dry run would be the very
        # coupling this flag exists to remove.
        config = (
            load_config(args.config) if args.config
            else load_config()
        )
    elif args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    interventions = None
    if args.interventions:
        from detector.interventions import load_intervention_registry

        interventions = load_intervention_registry(
            args.interventions, require_executable_bounds=True,
        )
    report_path = args.report or (
        ROOT / "artifacts" / f"baseline_693_{args.run_id}.json"
    )
    report = run_baseline(
        dataset_root=args.dataset_root,
        manifest_path=args.manifest,
        run_id=args.run_id,
        runs_root=args.runs_root,
        report_path=report_path,
        workers=args.workers,
        config=config,
        interventions=interventions,
        retry_unresolved=args.retry_unresolved,
        detector_factory=(
            import_factory(args.detector_factory) if args.detector_factory else None
        ),
        skip_preflight=args.skip_preflight,
        slowest=args.slowest,
    )
    print(json.dumps({
        "run_id": report["run_id"],
        "coverage": report["coverage"],
        "metrics": {
            key: report["metrics"][key] for key in (
                "decisive", "unresolved", "authoritative", "degraded",
                "degraded_disagreeing", "no_terminal_decision",
                "decisive_accuracy", "all_case_accuracy", "tp", "fp", "fn", "tn",
                "step_localization",
            )
        },
        "timing": report["timing"],
        "usage": {
            key: report["usage"][key] for key in (
                "measured_cases", "unmeasured_cases", "total_tokens",
                "mean_tokens_per_case", "max_tokens_case",
            )
        },
        "report_path": report.get("report_path"),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
