"""Pre-run readiness check for a real (provider-backed) experiment.

    python scripts/preflight_real_run.py --config evolution/configs/<run>.json
    python scripts/preflight_real_run.py --config <cfg> --phase baseline
    python scripts/preflight_real_run.py --config <cfg> --json out.json

Answers one question: **if this run is started now, will its readings mean what
they claim?** Every check is about a way a long real run has already gone wrong
in this repository, or a way the framework would silently record something
other than what it measured.

The check never calls a provider and never writes to the run's own directories.
It reads the corpus, the configuration, the calibration record, the source
identity and the work root; ``--phase`` selects which end-to-end path is being
prepared (``baseline`` = full-corpus detection measurement, ``evolution`` = the
self-evolution cycle).

Exit status is 0 when nothing blocks the run, 1 when at least one BLOCKER is
present. Warnings never fail the check but are printed with their reason.

Usage notes
-----------
* Credentials are only checked for *presence*; the value is never read into the
  report. A run started without them fails after the first case, hours in.
* ``source_digest`` is recorded here so the identity the run cites can be shown
  to be the one that preceded it (the ``freeze_prerun`` pattern, generalised).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CHECK_ORDER = (
    "corpus",
    "labels",
    "split",
    "gates_satisfiable",
    "timeouts",
    "budget",
    "forecast_budget",
    "forecast_alignment",
    "long_tail",
    "credentials",
    "detector_env",
    "work_root",
    "path_length",
    "calibration",
    "runner_admission",
    "source_identity",
    "cost",
)

# Windows MAX_PATH is 260; the framework's own cache-path guard budgets 250 for
# a cache file and degrades the name beyond that. A work root whose derived
# paths could not fit made a whole round fail closed with only a per-row
# message (2026-09: "cache directory leaves -59 characters").
PATH_BUDGET = 250


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, check: str, status: str, detail: str, **extra) -> None:
        self.items.append({
            "check": check, "status": status, "detail": detail, **extra,
        })

    @property
    def blockers(self) -> list[dict]:
        return [item for item in self.items if item["status"] == "BLOCKER"]

    @property
    def warnings(self) -> list[dict]:
        return [item for item in self.items if item["status"] == "WARN"]

    def payload(self, phase: str, config_path: str) -> dict:
        return {
            "schema_version": "evolution.preflight_real_run.v1",
            "phase": phase,
            "config": config_path,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "passed": not self.blockers,
            "blockers": len(self.blockers),
            "warnings": len(self.warnings),
            "checks": self.items,
        }


def _blocker(report: Report, check: str, detail: str, **extra) -> None:
    report.add(check, "BLOCKER", detail, **extra)


def _warn(report: Report, check: str, detail: str, **extra) -> None:
    report.add(check, "WARN", detail, **extra)


def _ok(report: Report, check: str, detail: str, **extra) -> None:
    report.add(check, "OK", detail, **extra)


def check_corpus(report: Report, cfg: dict) -> list[str]:
    """The case list must reconcile with the corpus: no gaps, no shadowing."""
    root = Path(cfg["dataset_root"])
    if not root.is_dir():
        _blocker(report, "corpus", f"dataset root does not exist: {root}")
        return []
    manifest_path = REPO / "datasets/protocols/development_exposure.json"
    if not manifest_path.is_file():
        _blocker(report, "corpus", f"exposure manifest missing: {manifest_path}")
        return []
    listed = [
        str(item) for item in json.loads(
            manifest_path.read_text(encoding="utf-8-sig")
        ).get("cases") or ()
    ]
    on_disk = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*.json")
    )
    missing = sorted(set(listed) - set(on_disk))
    extra = sorted(set(on_disk) - set(listed))
    duplicates = len(listed) - len(set(listed))
    if missing:
        _blocker(report, "corpus", f"{len(missing)} listed case(s) are absent",
                 cases=missing[:5])
        return []
    if extra:
        _warn(report, "corpus",
              f"{len(extra)} file(s) on disk are not in the manifest",
              cases=extra[:5])
    if duplicates:
        _blocker(report, "corpus", f"{duplicates} duplicate case id(s) in the manifest")
        return []
    # ``safe_case_id`` folds "/" and "\" to "__", so two ids can collide on one
    # result path and silently overwrite each other.
    folded: dict[str, str] = {}
    collisions = []
    for case in listed:
        safe = case.replace("/", "__").replace("\\", "__")
        previous = folded.setdefault(safe, case)
        if previous != case:
            collisions.append((previous, case))
    if collisions:
        _blocker(report, "corpus",
                 f"{len(collisions)} case id(s) collide on one result path",
                 pairs=collisions[:3])
    if not report.blockers:
        _ok(report, "corpus", f"{len(listed)} case(s) reconcile with {root.name}")
    return listed


def check_labels(report: Report, cfg: dict, cases: list[str]) -> dict:
    """Gold labels and gold steps, read through the framework's own reader.

    ``is_hallucination`` is the string "true"/"false" in this corpus, so any
    reader that does not normalise it answers a different question (a bare
    truth test on the string says every case is a hallucination). This check
    reads it the way the gates do and refuses a corpus that does not parse.
    """
    from evolution.splits import bool_gold, load_gold, load_gold_step

    root = Path(cfg["dataset_root"])
    positives = negatives = steps = 0
    bad: list[str] = []
    for case in cases:
        try:
            value = bool_gold(load_gold(root, case))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            bad.append(f"{case}: {exc}")
            continue
        if value is None:
            bad.append(f"{case}: no label")
        elif value:
            positives += 1
            if load_gold_step(root, case) is not None:
                steps += 1
        else:
            negatives += 1
    if bad:
        _blocker(report, "labels",
                 f"{len(bad)} case(s) have an unreadable gold label", cases=bad[:3])
    elif not positives or not negatives:
        _blocker(report, "labels",
                 f"corpus is single-polarity (positives={positives}, "
                 f"negatives={negatives}); every paired reading needs both")
    else:
        _ok(report, "labels",
            f"positives={positives}, negatives={negatives}, "
            f"with gold step={steps}")
    if positives and steps < positives:
        _warn(report, "labels",
              f"{positives - steps} gold-positive case(s) carry no responsible "
              "step; localization readings cannot cover them")
    return {"positives": positives, "negatives": negatives, "with_step": steps}


def check_split(report: Report, cfg: dict, cases: list[str], phase: str) -> dict:
    from evolution.splits import audit_case_exposure, build_split

    if phase == "baseline":
        _ok(report, "split", "baseline phase measures the whole corpus; no split")
        return {}
    try:
        split = build_split(
            config=cfg, dataset_root=cfg["dataset_root"], mini=True,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        _blocker(report, "split", f"split could not be built: {exc}")
        return {}
    parts = {
        "pool": len(split.pool),
        "sentinel": len(split.sentinel),
        "gain": len(split.gain),
        "patrol_pool": len(split.patrol_pool),
        "validation": len(split.validation),
    }
    overlap = set(split.pool) & set(cases) == set(split.pool)
    if not overlap:
        _blocker(report, "split", "the evolution pool is not drawn from the corpus")
        return parts
    if not split.gain:
        _blocker(report, "split", "the gain set is empty; nothing can be scored")
    if not split.sentinel:
        _blocker(report, "split", "the sentinel set is empty")
    exposure = audit_case_exposure(config=cfg, split=split)
    if not exposure["clean"]:
        details = []
        if exposure["validation_contamination"]:
            details.append(f"validation contaminated: {exposure['validation_contamination'][:3]}")
        if exposure["final_verification_contamination"]:
            details.append(f"final verification contaminated: {exposure['final_verification_contamination'][:3]}")
        _blocker(report, "split", f"data exposure check failed: {'; '.join(details)}")
        return parts
    budget = cfg.get("budget") or {}
    patrol = int(budget.get("patrol_cases", 0))
    if patrol > len(split.patrol_pool):
        _blocker(report, "split",
                 f"patrol_cases={patrol} exceeds the patrol pool "
                 f"({len(split.patrol_pool)})")
    else:
        _ok(report, "split", f"pool={parts['pool']}, sentinel={parts['sentinel']}, "
                             f"gain={parts['gain']}, patrol_pool={parts['patrol_pool']}, "
                             f"validation={parts['validation']}")
    return parts


def check_gates_satisfiable(report: Report, cfg: dict, phase: str) -> None:
    """Static satisfiability of the admission gates' coverage floor.

    ``sentinel_coverage`` needs at least N baseline-correct cases of each
    polarity in the sentinel set. The floor is a *necessary* condition: whether
    the control arm is correct still depends on the provider. What can be
    checked here is whether the set is even large enough to have a chance -- a
    single-polarity sentinel made a whole promotion batch unevaluable by
    construction before this was enforced at freeze time.
    """
    if phase == "baseline":
        _ok(report, "gates_satisfiable", "baseline phase runs no admission gate")
        return
    from evolution.splits import build_split, load_gold

    gates = cfg.get("gates") or {}
    need_tp = int(gates.get("harmlessness_min_protected_tp", 2))
    need_tn = int(gates.get("harmlessness_min_protected_tn", 2))
    try:
        split = build_split(config=cfg, dataset_root=cfg["dataset_root"], mini=True)
    except (ValueError, OSError, json.JSONDecodeError):
        _warn(report, "gates_satisfiable", "split unavailable; floor not checked")
        return
    root = Path(cfg["dataset_root"])
    try:
        sentinel_gold = [load_gold(root, case) for case in split.sentinel]
    except (ValueError, OSError) as exc:
        _blocker(report, "gates_satisfiable", f"sentinel labels unreadable: {exc}")
        return
    available_tp = sum(1 for value in sentinel_gold if value)
    available_tn = sum(1 for value in sentinel_gold if not value)
    problems = []
    if available_tp < need_tp:
        problems.append(f"sentinel holds {available_tp} gold-positive case(s), "
                        f"the floor needs {need_tp} baseline-correct")
    if available_tn < need_tn:
        problems.append(f"sentinel holds {available_tn} gold-negative case(s), "
                        f"the floor needs {need_tn} baseline-correct")
    if problems:
        _blocker(report, "gates_satisfiable", "; ".join(problems))
    else:
        _ok(report, "gates_satisfiable",
            f"sentinel has {available_tp} positive / {available_tn} negative "
            f"case(s) for a floor of {need_tp}/{need_tn}")


def check_timeouts(report: Report, cfg: dict) -> None:
    """A case must not be able to outlast the round that contains it.

    ``case_timeout_seconds`` is the ceiling on one case; ``budget.
    cycle_timeout_seconds`` is the ceiling on the whole round. When the former
    exceeds the latter, one pathological case consumes the entire round budget
    and every later batch in that round fail-closes -- which then has to be
    distinguished from a real result by hand.
    """
    detector_config = Path(cfg.get("detector_config") or "")
    if not detector_config.is_file():
        _blocker(report, "timeouts",
                 f"detector config missing: {detector_config}")
        return
    detector = json.loads(detector_config.read_text(encoding="utf-8-sig"))
    case_timeout = detector.get("case_timeout_seconds")
    provider = detector.get("provider") or {}
    cycle_timeout = (cfg.get("budget") or {}).get("cycle_timeout_seconds")
    if not case_timeout or not cycle_timeout:
        _warn(report, "timeouts",
              "case_timeout_seconds or cycle_timeout_seconds is unset; "
              "an unbounded case can hold the round open")
        return
    ratio = float(case_timeout) / float(cycle_timeout)
    detail = (
        f"case_timeout={case_timeout}s ({case_timeout / 3600:.1f}h), "
        f"cycle_timeout={cycle_timeout}s ({cycle_timeout / 3600:.1f}h), "
        f"ratio={ratio:.2f}"
    )
    if ratio >= 1.0:
        _blocker(report, "timeouts",
                 f"a single case may consume the whole round: {detail}")
    elif ratio > 0.5:
        _warn(report, "timeouts",
              f"one case can take over half the round budget: {detail}")
    else:
        _ok(report, "timeouts", detail)
    if provider.get("timeout_seconds") and int(provider["timeout_seconds"]) >= int(case_timeout):
        _warn(report, "timeouts",
              "provider.timeout_seconds is not below case_timeout_seconds; "
              "the per-transport retry cannot help before the case dies")


def _cost_sample() -> dict:
    path = REPO / "artifacts/cost_model_693.json"
    if not path.is_file():
        return {}
    try:
        model = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return model.get("sample") or {}


def check_budget(report: Report, cfg: dict, phase: str) -> dict:
    """Does one round's declared workload fit its declared wall clock?

    Counting case evaluations is not enough. A round can evaluate the right
    number of cases and still be unable to finish: the arithmetic that decides
    it is ``cycle_timeout_seconds`` against *measured* per-case wall time at the
    configured worker count. The 2026-09-24 multi-generation attempts are the
    worked example -- the audit alone re-measures S+G, whose slowest member took
    over an hour, and the round spent its budget before patrol ran. The run then
    reported "0 admissions" for a reason that was arithmetic rather than
    scientific.
    """
    if phase == "baseline":
        _ok(report, "budget", "baseline phase is one batch; round budget N/A")
        return {}
    budget = cfg.get("budget") or {}
    cycle_timeout = budget.get("cycle_timeout_seconds")
    patrol = int(budget.get("patrol_cases", 0))
    rerun = int(budget.get("stable_failure_rerun_cases", 0))
    audit_defects = 2
    gain = int((cfg.get("split") or {}).get("mini_gain_size", 0))
    sentinel = int((cfg.get("split") or {}).get("sentinel_positives", 0)) + int(
        (cfg.get("split") or {}).get("sentinel_negatives", 0)
    )
    # Fresh evaluations one round can spend, before any candidate is admitted.
    fresh = patrol + rerun + sentinel + gain + audit_defects * (sentinel + gain)
    development = cfg.get("development") or {}
    if development.get("enabled"):
        fresh += int(development.get("max_l1_cases", 0))
    workers = int((cfg.get("backend") or {}).get("workers", 1) or 1)
    sample = _cost_sample()
    mean_minutes = sample.get("mean_minutes")
    max_minutes = sample.get("max_minutes")
    parts = {
        "patrol": patrol, "bootstrap_rerun": rerun, "audit": audit_defects,
        "sentinel": sentinel, "gain": gain,
        "development_l1": int(development.get("max_l1_cases", 0)),
        "fresh_case_evaluations_upper_bound": fresh,
        "cycle_timeout_seconds": cycle_timeout,
        "workers": workers,
        "development_enabled": bool(development.get("enabled")),
        "archive_on": (cfg.get("archive") or {}).get("on"),
    }
    if mean_minutes and cycle_timeout:
        # Work floor: a round cannot finish faster than its total case-work
        # divided by its workers. Compared against the declared round budget
        # with the cost model's own caveat in mind -- its sample is small and
        # skewed toward hard cases, so this is a floor to sanity-check against,
        # not a prediction.
        floor_minutes = float(mean_minutes) * fresh / max(1, workers)
        parts["work_floor_minutes"] = round(floor_minutes, 1)
        parts["work_floor_fraction_of_budget"] = round(
            floor_minutes * 60 / float(cycle_timeout), 3
        )
        if max_minutes:
            parts["slowest_observed_case_minutes"] = float(max_minutes)
        if floor_minutes * 60 >= float(cycle_timeout):
            _blocker(
                report, "budget",
                f"one round bounds ~{fresh} fresh case evaluations, whose work "
                f"floor is {floor_minutes:.0f} min at workers={workers}, against "
                f"cycle_timeout_seconds={cycle_timeout}: the round cannot finish "
                "its own declared workload",
            )
        elif floor_minutes * 60 >= 0.7 * float(cycle_timeout):
            _warn(
                report, "budget",
                f"one round's work floor is {floor_minutes:.0f} min against a "
                f"{float(cycle_timeout) / 60:.0f} min budget "
                f"({parts['work_floor_fraction_of_budget']:.0%}); the long tail "
                "decides whether patrol runs",
            )
        else:
            _ok(report, "budget",
                f"~{fresh} fresh case evaluations fit {cycle_timeout}s "
                f"(work floor {floor_minutes:.0f} min at workers={workers})")
    elif not cycle_timeout:
        _warn(report, "budget", "no cycle_timeout_seconds: the round is unbounded")
    else:
        _warn(report, "budget",
              f"one round bounds ~{fresh} fresh case evaluations, but no "
              "measured cost sample exists to check that against")
    if development.get("enabled") and not development.get("max_provider_calls"):
        _warn(report, "budget",
              "development is enabled but max_provider_calls is unset")
    if not development.get("enabled"):
        _warn(report, "budget",
              "development is disabled: candidates go to the gates undrafted, "
              "which is what §31.1 found the hard way")
    return parts


def check_forecast_budget(report: Report, cfg: dict) -> None:
    """Two optional predictions need two conservative output reservations."""
    from detector.config import load_config

    try:
        detector = load_config(cfg["detector_config"], require_credentials=False)
    except (OSError, ValueError) as exc:
        _blocker(report, "forecast_budget", f"detector config unreadable: {exc}")
        return
    policy = detector["causal_audit"]
    if (policy["next_action_mode"] == "off"
            or policy["max_added_provider_requests_per_case"] < 2):
        _ok(report, "forecast_budget", "two-forecast output capacity not required by this policy")
        return
    total = policy["max_added_output_tokens_per_case"]
    per_request = policy["max_forecast_output_tokens_per_request"]
    required = 2 * per_request
    detail = (f"max_added_output_tokens_per_case={total}; two forecasts require "
              f"2 * {per_request} = {required} output tokens")
    if total < required:
        _blocker(report, "forecast_budget", detail + "; the second forecast can be refused after the first reservation")
    else:
        _ok(report, "forecast_budget", detail)


def check_forecast_alignment(report: Report, cfg: dict, cases: list[str]) -> None:
    """Report missing turn metadata without guessing task boundaries."""
    from detector.dataset import load_case

    affected = []
    unreadable = []
    families: dict[str, int] = {}
    for case in cases:
        try:
            record, _ = load_case(cfg["dataset_root"], case)
        except (OSError, ValueError) as exc:
            unreadable.append(f"{case}: {exc}")
            continue
        question = record.get("question")
        history = record.get("history") or []
        if not isinstance(question, list) or len(question) < 2:
            continue
        if any(isinstance(event, dict) and (event.get("task_turn") is not None
                                          or event.get("role") == "user") for event in history):
            continue
        affected.append(case)
        family = case.split("/", 1)[0]
        families[family] = families.get(family, 0) + 1
    detail = (f"{len(affected)} case(s) have a multi-turn question and no task_turn/user events")
    if affected:
        detail += (f"; families={dict(sorted(families.items()))}; examples={affected[:5]}; "
                   "model forecasts will be skipped because task alignment is unknown")
    if unreadable:
        detail += f"; {len(unreadable)} case(s) could not be scanned: {unreadable[:3]}"
    emit = _warn if affected or unreadable else _ok
    emit(report, "forecast_alignment", detail, affected_cases=len(affected),
         families=dict(sorted(families.items())), cases=affected[:5], unreadable=unreadable[:3])


def check_long_tail(report: Report, cfg: dict, phase: str) -> None:
    """The known slow cases decide the floor, so they must be inside budget."""
    if phase == "baseline":
        _ok(report, "long_tail",
            "the baseline batch runs the whole corpus, so the long tail is "
            "already inside its own batch budget")
        return
    model_path = REPO / "artifacts/cost_model_693.json"
    if not model_path.is_file():
        _warn(report, "long_tail",
              "no cost model on file; the slow-case floor is unknown")
        return
    model = json.loads(model_path.read_text(encoding="utf-8-sig"))
    sample = model.get("sample") or {}
    minutes = sample.get("max_minutes")
    if minutes is None:
        _warn(report, "long_tail", "cost model records no usable slow case")
        return
    minutes = float(minutes)
    cycle_timeout = (cfg.get("budget") or {}).get("cycle_timeout_seconds")
    detail = (f"slowest observed case takes {minutes:.1f} min "
              f"({sample.get('unique_cases')} unique cases sampled)")
    if cycle_timeout and minutes * 60 >= float(cycle_timeout):
        _blocker(report, "long_tail",
                 f"{detail} >= the round budget ({cycle_timeout}s); it would "
                 "consume the round on its own")
    elif cycle_timeout and minutes * 60 >= 0.4 * float(cycle_timeout):
        _warn(report, "long_tail",
              f"{detail} is over 40% of the round budget; batch-level "
              "fail-closed is likely")
    else:
        _ok(report, "long_tail", detail)


def check_credentials(report: Report, cfg: dict, phase: str) -> None:
    """Presence only, never the value."""
    detector_config = Path(cfg.get("detector_config") or "")
    if not detector_config.is_file():
        _warn(report, "credentials", "detector config missing; nothing to read")
        return
    detector = json.loads(detector_config.read_text(encoding="utf-8-sig"))
    provider = detector.get("provider") or {}
    env_name = str(provider.get("api_key_env") or "")
    if not env_name:
        _warn(report, "credentials", "no api_key_env declared in the detector config")
        return
    present = bool(os.environ.get(env_name))
    repo_env = REPO / ".env"
    in_env_file = False
    if not present and repo_env.is_file():
        in_env_file = any(
            line.split("=", 1)[0].strip() == env_name
            for line in repo_env.read_text(encoding="utf-8-sig").splitlines()
            if "=" in line and not line.strip().startswith("#")
        )
    if present or in_env_file:
        _ok(report, "credentials",
            f"{env_name} is available ({'process env' if present else '.env'})")
    else:
        _blocker(report, "credentials",
                 f"{env_name} is not set and not present in .env; the run would "
                 "fail after its first case")


def check_detector_env(report: Report, cfg: dict) -> None:
    """No environment override may silently contradict the frozen config."""
    from detector.config import DETECTOR_ENV_KEYS

    present = {
        key: os.environ[key] for key in DETECTOR_ENV_KEYS if os.environ.get(key)
    }
    if not present:
        _ok(report, "detector_env", "no DETECTOR_* overrides in the environment")
        return
    _warn(report, "detector_env",
          "environment overrides are active and will be recorded in the run's "
          "identity: " + ", ".join(sorted(present)))


def check_work_root(report: Report, cfg: dict, phase: str) -> None:
    """A fresh run needs a work root with no foreign ledger in it."""
    root = Path(cfg["work_root"])
    ledger_path = Path(cfg["ledger_path"])
    if not ledger_path.exists():
        _ok(report, "work_root", f"{root.name} is clean (no ledger yet)")
        return
    try:
        lines = [
            json.loads(line) for line in
            ledger_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        _blocker(report, "work_root", f"ledger is unreadable: {exc}")
        return
    events = {str(record.get("event") or "") for record in lines}
    if phase == "evolution" and "full_self_evolution_started" not in events:
        _blocker(report, "work_root",
                 f"{ledger_path} holds {len(lines)} record(s) but no "
                 "full_self_evolution_started; a full run refuses a foreign "
                 "ledger. Use a new work_root.")
        return
    registry = Path(cfg["registry_dir"]) / "active.json"
    if registry.is_file():
        from detector.interventions import load_intervention_registry

        entries = load_intervention_registry(registry, require_executable_bounds=True)["interventions"]
        if entries:
            _warn(report, "work_root",
                  f"the active registry already holds {len(entries)} "
                  "intervention(s); this is a continuation, not a first run",
                  interventions=[str(item.get("intervention_id") or "")
                                 for item in entries][:5])
        else:
            _ok(report, "work_root",
                f"existing run at {root.name} with an empty registry")
    else:
        _warn(report, "work_root",
              f"{ledger_path} already exists ({len(lines)} record(s))")


def check_path_length(report: Report, cfg: dict, cases: list[str]) -> None:
    """Deep work roots have failed a whole round on Windows.

    The cache path is ``<work_root>/cache/<namespace digest>/<registry key>/
    <case>.<record hash>.json``; the framework degrades an over-long name to a
    16-character digest and *fails* when even that does not fit (2026-09: a
    round fail-closed with only a per-row "cache directory leaves -59
    characters"). The namespace and registry key are digests of fixed width,
    so the worst case is computable from the work root and the longest case id.
    """
    from evolution.backends import _CACHE_PATH_MAX, _DIGEST_NAME_WIDTH

    root = Path(cfg["work_root"]).resolve()
    longest = max((str(case) for case in cases), key=len, default="A/000.json")
    folded = longest.replace("/", "__").replace("\\", "__")
    # namespace: sha256 hex; registry key: content hash or "empty".
    directory = (
        root / "cache" / ("0" * 64) / "unsigned"
        / f"{folded}.{'0' * 16}.json"
    )
    # The remaining budget after the directory: a 16-hex digest name plus suffix.
    dir_length = len(str(directory.parent)) + 1
    allowance = _CACHE_PATH_MAX - dir_length
    floor = _DIGEST_NAME_WIDTH + len(".json")
    detail = (
        f"longest case '{longest}' leaves {allowance} characters for the cache "
        f"file name (digest floor {floor}); cache root {directory.parent}"
    )
    if allowance < floor:
        _blocker(report, "path_length",
                 f"derived cache paths cannot hold even a digest name: {detail}. "
                 "Use a shorter work_root.")
    elif allowance < len(f"{folded}.{'0' * 16}.json"):
        _warn(report, "path_length",
              f"some case names will degrade to a bare digest (still correct, "
              f"just unreadable): {detail}")
    else:
        _ok(report, "path_length", detail)


def check_calibration(report: Report) -> None:
    """A missing or stale calibration record silently disables conviction."""
    from detector.conviction_calibration import ConvictionCalibration

    try:
        calibration = ConvictionCalibration()
        summary = calibration.status()
        warnings = calibration.warnings()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        _blocker(report, "calibration", f"calibration unreadable: {exc}")
        return
    if not summary:
        _blocker(report, "calibration",
                 "no conviction predicates are registered at all")
        return
    admitted = [name for name, item in summary.items() if item.get("admitted")]
    revoked = [name for name, item in summary.items() if not item.get("admitted")]
    if not admitted:
        _blocker(report, "calibration",
                 "no predicate can currently authorize a conviction: "
                 + ", ".join(
                     f"{name} ({','.join(item.get('reasons') or [])})"
                     for name, item in sorted(summary.items())
                 ))
        return
    for warning in warnings:
        _warn(report, "calibration", f"calibration check degraded: {warning}")
    if revoked:
        _warn(report, "calibration",
              f"{len(revoked)} predicate(s) revoked for want of calibration: "
              f"{revoked}; they cannot authorize a conviction",
              revoked=revoked)
    _ok(report, "calibration",
        f"{len(admitted)} conviction predicate(s) calibrated: {admitted}")


def check_runner_admission(report: Report) -> None:
    from evolution.metamorphic import metamorphic_report

    try:
        verdict = metamorphic_report()
    except (ValueError, OSError) as exc:
        _blocker(report, "runner_admission", f"runner audit failed: {exc}")
        return
    rows = verdict.get("runners") or []
    failed = [row for row in rows if not row.get("admitted")]
    if not rows:
        _blocker(report, "runner_admission", "no runners are registered")
    elif failed:
        _blocker(report, "runner_admission",
                 "runner(s) not admitted: "
                 + ", ".join(
                     f"{row['runner']} ({','.join(row.get('failed') or [])})"
                     for row in failed
                 ))
    else:
        _ok(report, "runner_admission",
            f"{len(rows)} runner(s) admitted by the metamorphic gate")


def check_source_identity(report: Report, cfg: dict) -> None:
    from detector.identity import artifact_digest, source_identity

    path = Path(cfg["detector_config"]).resolve()
    if not path.is_file():
        _blocker(report, "source_identity", f"declared detector config does not exist: {path}")
        return
    identity = {"source_digest": source_identity("detector", "evolution"),
                "detector_config": {"path": str(path), "digest": artifact_digest(path)}}
    _ok(report, "source_identity",
        f"source digest {identity['source_digest'][:16]}… and declared detector config recorded",
        identity=identity)


def check_cost(report: Report, phase: str, cfg: dict) -> None:
    model_path = REPO / "artifacts/cost_model_693.json"
    if not model_path.is_file():
        _warn(report, "cost", "no cost model on file")
        return
    model = json.loads(model_path.read_text(encoding="utf-8-sig"))
    workers = int((cfg.get("backend") or {}).get("workers") or 1)
    estimate = (model.get("estimates") or {}).get(f"W{workers}") or {}
    token_note = (model.get("tokens") or {}).get("corpus_estimate")
    if not estimate:
        _warn(report, "cost", f"cost model has no estimate for workers={workers}")
        return
    simulated = (estimate.get("simulated_makespan") or {}).get("p50_minutes")
    tail = (estimate.get("tail") or {}).get("tail_bound_hours")
    text = (
        f"work floor {estimate.get('work_lower_bound_hours')}h, "
        f"simulated p50 {round(simulated / 60, 1) if simulated else '?'}h, "
        f"efficiency-adjusted {estimate.get('efficiency_adjusted_hours')}h, "
        f"tail floor {tail}h"
    )
    _warn(report, "cost",
          f"one full-corpus pass at workers={workers}: {text}"
          + (f"; ~{token_note:,} tokens" if isinstance(token_note, (int, float))
             else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True,
                        help="evolution config for the run being prepared")
    parser.add_argument("--phase", choices=("baseline", "evolution"),
                        default="evolution",
                        help="baseline = full-corpus detection measurement")
    parser.add_argument("--json", help="also write the report here")
    args = parser.parse_args()

    from evolution.config import load_config

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 1
    cfg = load_config(config_path)

    report = Report()
    cases = check_corpus(report, cfg)
    if cases:
        check_labels(report, cfg, cases)
    check_split(report, cfg, cases, args.phase)
    check_gates_satisfiable(report, cfg, args.phase)
    check_timeouts(report, cfg)
    check_budget(report, cfg, args.phase)
    check_forecast_budget(report, cfg)
    check_forecast_alignment(report, cfg, cases)
    check_long_tail(report, cfg, args.phase)
    check_credentials(report, cfg, args.phase)
    check_detector_env(report, cfg)
    check_work_root(report, cfg, args.phase)
    check_path_length(report, cfg, cases)
    check_calibration(report)
    check_runner_admission(report)
    check_source_identity(report, cfg)
    check_cost(report, args.phase, cfg)

    payload = report.payload(args.phase, str(config_path))
    width = max(len(item["check"]) for item in report.items)
    for item in report.items:
        print(f"{item['check']:<{width}}  {item['status']:<7}  {item['detail']}")
    print()
    print(f"phase={args.phase}  blockers={len(report.blockers)}  "
          f"warnings={len(report.warnings)}  "
          f"{'READY' if not report.blockers else 'NOT READY'}")
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if not report.blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
