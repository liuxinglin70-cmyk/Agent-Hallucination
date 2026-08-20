"""Detection backends for paired gold evaluation.

``DetectorV2Backend`` is the real runtime: it imports nothing except the
public ``detector_v2`` API and executes intervention registries through the
fail-closed hook plan. ``MockDetectorBackend`` is an offline oracle used to
validate gate mechanics and the experiment protocol without burning LLM
budget; its results are marked ``backend=mock`` in every report.
"""

from __future__ import annotations

import json
import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from detector_v2.api import Detector
from detector_v2.config import load_config as load_detector_config
from detector_v2.interventions import build_intervention_registry
from detector_v2.result import prediction_state

from evolution_v3.config import resolve_path
from evolution_v3.contracts import save_json
from evolution_v3.metrics import source_of
from evolution_v3.splits import bool_gold, load_record


def prediction_from_state(state: Any) -> bool | None:
    if state == "verified_error":
        return True
    if state == "verified_clean":
        return False
    return None


def _prediction_from_result(result: Mapping[str, Any]) -> bool | None:
    """Only authoritative terminal decisions enter evolution metrics."""
    if str(result.get("decision_authority") or "") != "authoritative":
        return None
    return prediction_from_state(result.get("state"))


# Bump whenever the detector-side execution or paired-replay semantics change;
# this invalidates stale result caches instead of silently reusing them.
CACHE_VERSION = "v11-per-hearing-frozen-envelope"


def _valid_stage_entry(
    result: Mapping[str, Any] | None,
    status: Mapping[str, Any] | None,
) -> bool:
    """A persisted stage result/status pair usable as a freeze entry."""
    if not isinstance(result, Mapping) or not isinstance(status, Mapping):
        return False
    return bool(result.get("ok")) == (str(status.get("state") or "") == "ok")


def file_digest(path: str | Path | None) -> str:
    if not path:
        return "missing"
    target = Path(path)
    if not target.exists():
        return "missing"
    return hashlib.sha256(target.read_bytes()).hexdigest()[:16]


def source_digest() -> str:
    """Digest of the detector_v2 + evolution_v3 source trees.

    Execution-semantics fingerprint: any change to how arms are replayed or
    gated must invalidate derived caches (audit verdicts, row caches).
    """
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for package in (root / "detector_v2", root / "evolution_v3"):
        for path in sorted(package.glob("*.py")):
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def registry_key(registry: Mapping[str, Any] | None) -> str:
    if not registry:
        return "empty"
    return str(registry.get("content_hash") or "unsigned")


def _unresolved_backend_result(
    case_id: str,
    code: str,
    message: str,
    error_type: str,
) -> dict[str, Any]:
    return {
        "schema_version": "detector_v2.detection_result.v1",
        "case_id": case_id,
        "state": "unresolved",
        "decision_authority": "invalid",
        "runtime_valid": False,
        "authority_failure_reasons": [{
            "component": "backend",
            "code": code,
            "error_type": error_type,
            "message": message,
        }],
        "stage_status": {},
        "proof": {"deployment": {}, "state": "unresolved"},
    }


class DetectionBackend:
    name = "abstract"

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def evaluate(
        self,
        cases: Sequence[str],
        *,
        dataset_root: str | Path,
        registry: Mapping[str, Any] | None = None,
        gold_map: Mapping[str, bool | None] | None = None,
        workers: int = 1,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate cases paired by id.

        ``deadline`` is a monotonic wall-clock timestamp for the whole batch.
        A case that cannot start before it fail-closes to an unresolved row:
        bounded runtime never silently drops or invents outcomes.
        """
        gold_map = dict(gold_map or {})

        def one(case_id: str) -> dict[str, Any]:
            gold = gold_map.get(case_id, gold_map.get(case_id.replace("/", "__")))
            if deadline is not None and time.monotonic() >= deadline:
                result = _unresolved_backend_result(
                    case_id,
                    "batch_deadline_exceeded",
                    "batch deadline exceeded before this case started",
                    "Timeout",
                )
            else:
                try:
                    record = load_record(dataset_root, case_id)
                    result = self.run_case(
                        record, case_id=case_id, registry=registry, gold=gold,
                    )
                except Exception as exc:
                    result = _unresolved_backend_result(
                        case_id,
                        "case_evaluation_exception",
                        str(exc),
                        type(exc).__name__,
                    )
            return {
                "case": case_id,
                "gold": gold,
                "prediction": _prediction_from_result(result),
                "state": result.get("state"),
                "authority": result.get("decision_authority"),
                "result": result,
                "backend": self.name,
            }

        if int(workers) > 1 and len(cases) > 1:
            rows: list[dict[str, Any]] = []
            total = len(cases)
            progress = bool(os.environ.get("EVO3_PROGRESS"))
            if progress:
                print(
                    f"[evo3] batch start: {total} cases, workers={workers}, "
                    f"registry={registry_key(registry)}",
                    file=sys.stderr,
                    flush=True,
                )
            with ThreadPoolExecutor(max_workers=int(workers)) as pool:
                futures = [pool.submit(one, case) for case in cases]
                for future in as_completed(futures):
                    row = future.result()
                    rows.append(row)
                    if progress:
                        print(
                            f"[evo3] {len(rows)}/{total} {row['case']} -> "
                            f"{row['prediction']} ({row['state']})",
                            file=sys.stderr,
                            flush=True,
                        )
            return sorted(rows, key=lambda row: str(row["case"]))
        return [one(case) for case in cases]

    def evaluate_fresh(
        self,
        cases: Sequence[str],
        *,
        dataset_root: str | Path,
        registry: Mapping[str, Any] | None = None,
        gold_map: Mapping[str, bool | None] | None = None,
        workers: int = 1,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        return self.evaluate(
            cases,
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
            deadline=deadline,
        )


class DetectorV2Backend(DetectionBackend):
    """Real detector backend with frozen-judge paired replay.

    The baseline (empty registry) judge output for a case is captured once.
    Any non-empty registry that does not target ``judge.*`` is replayed with
    that frozen judge, so V2/V3/M compare intervention effects instead of
    two independent LLM rolls of the judge. Registries targeting judge hooks
    are run normally and marked ``frozen_judge_replay=false``.
    """

    name = "detector_v2"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        baseline_results: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        detector_config_path = resolve_path(config.get("detector_config"))
        detector_config = load_detector_config(detector_config_path)
        self.detector = Detector(detector_config)
        self._base_results: dict[str, dict[str, Any]] = {
            str(key).replace("__", "/"): dict(value)
            for key, value in (baseline_results or {}).items()
        }

    def _active_entries(self, registry: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        if not registry:
            return []
        from detector_v2.interventions import validate_registry

        return validate_registry(registry, require_executable_bounds=True)

    def _baseline(self, record: Mapping[str, Any], *, case_id: str) -> dict[str, Any]:
        cached = self._base_results.get(str(case_id))
        if cached is not None:
            return cached
        result = self.detector.detect(
            record,
            case_id=case_id,
            interventions=build_intervention_registry([]),
        )
        self._base_results[str(case_id)] = dict(result)
        return result

    @staticmethod
    def _frozen_stages(
        baseline: Mapping[str, Any],
        hooks: set[str],
    ) -> dict[str, Any]:
        """Build the frozen replay envelope from one baseline result.

        A stage is frozen once its outcome is settled: ``ok`` stages replay
        their verdict, terminal failure states replay the failure (an
        intervention that does not target the stage's hook cannot change a
        transport or contract failure, and a re-roll only contributes
        provider noise). ``not_needed`` / ``not_run`` stages are NOT frozen:
        the live control flow reproduces them deterministically with zero
        provider calls, and freezing a ``not_needed`` stage as ``ok=False``
        would turn a valid no-call into a downstream degradation. Stages
        whose hook an intervention targets are excluded so replay can never
        mask the intervention under test.
        """
        proof = baseline.get("proof") or {}
        status = baseline.get("stage_status") or {}
        providers = baseline.get("providers") or {}
        frozen: dict[str, Any] = {}

        def add(
            frozen_name: str,
            status_name: str,
            result: dict[str, Any],
        ) -> None:
            """Store a replay stage with its canonical detector status name."""
            frozen[f"{frozen_name}_result"] = result
            frozen[f"{frozen_name}_status"] = dict(status.get(status_name) or {})

        def settled(name: str) -> bool:
            state = str((status.get(name) or {}).get("state") or "")
            return bool(state) and state not in {"not_run", "not_needed"}

        def state_of(name: str) -> str:
            return str((status.get(name) or {}).get("state") or "")

        judge_state = state_of("judge")
        if judge_state == "ok" and isinstance(proof.get("judge"), Mapping):
            judge_result: dict[str, Any] = {"ok": True, "parsed": dict(proof["judge"])}
            judge_provider = providers.get("judge")
            if isinstance(judge_provider, Mapping) and isinstance(
                judge_provider.get("vote"), Mapping
            ):
                # Preserve the vote record so the recall decision replays the
                # same consensus instead of forcing a recall net on every
                # candidate arm (which would re-run provider stages).
                judge_result["provider"] = {"vote": dict(judge_provider["vote"])}
            add("judge", "judge", judge_result)
        elif settled("judge"):
            add("judge", "judge", {
                "ok": False,
                "parsed": None,
                "error": f"judge stage failed in baseline ({judge_state})",
                "error_type": judge_state,
            })
        if (
            "scanner.system" not in hooks
            and "judge_result" in frozen
            and settled("recall_scanner")
        ):
            if state_of("recall_scanner") == "ok":
                add("recall", "recall_scanner", {
                    "ok": True,
                    "candidates": list(proof.get("recall_candidates") or []),
                })
            else:
                add("recall", "recall_scanner", {
                    "ok": False,
                    "candidates": [],
                    "error": (
                        "recall stage failed in baseline "
                        f"({state_of('recall_scanner')})"
                    ),
                    "error_type": state_of("recall_scanner"),
                })
        if (
            "upgrade.pre" not in hooks
            and "upgrade.system" not in hooks
            and "recall_result" in frozen
            and settled("causal_upgrade")
        ):
            if state_of("causal_upgrade") == "ok":
                add("upgrade", "causal_upgrade", {
                    "ok": True,
                    "upgrades": list(proof.get("causal_upgrade_rows") or []),
                    "upgraded_candidate": proof.get("causal_upgrade"),
                    "upgraded_candidates": list(
                        proof.get("causal_upgrades")
                        or (
                            [proof.get("causal_upgrade")]
                            if proof.get("causal_upgrade") else []
                        )
                    ),
                })
            else:
                rows = proof.get("causal_upgrade_rows")
                add("upgrade", "causal_upgrade", {
                    "ok": False,
                    "upgrades": list(rows) if isinstance(rows, list) else [],
                    "error": (
                        "causal_upgrade stage failed in baseline "
                        f"({state_of('causal_upgrade')})"
                    ),
                    "error_type": state_of("causal_upgrade"),
                    **(
                        {"coverage": dict(proof["causal_upgrade_coverage"])}
                        if isinstance(proof.get("causal_upgrade_coverage"), dict)
                        else {}
                    ),
                })
        freeze_tracer = "tracer.system" not in hooks
        freeze_defense = (
            "defense.pre" not in hooks and "defense.system" not in hooks
        )
        if freeze_tracer or freeze_defense:
            # Tracer/defense replay per hearing: freezing only the top-level
            # results would paste one hearing's outcome into every other
            # hearing of a multi-hearing case, so each hearing row carries
            # its own freeze entry with exactly the stages this replay may
            # use (a targeted hook keeps its stage live).
            hearing_rows = [
                row for row in proof.get("hearings") or []
                if isinstance(row, Mapping)
            ]
            valid_rows = hearing_rows and all(
                _valid_stage_entry(
                    row.get("tracer_result"), row.get("tracer_status"),
                )
                and _valid_stage_entry(
                    row.get("defense_result"), row.get("defense_status"),
                )
                for row in hearing_rows
            )
            if valid_rows:
                frozen["hearings"] = [
                    {
                        "source": row.get("source"),
                        "candidate_id": row.get("candidate_id"),
                        "positive": dict(row.get("positive") or {}),
                        **(
                            {
                                "tracer_result": dict(row["tracer_result"]),
                                "tracer_status": dict(row["tracer_status"]),
                            }
                            if freeze_tracer
                            else {}
                        ),
                        **(
                            {
                                "defense_result": dict(row["defense_result"]),
                                "defense_status": dict(row["defense_status"]),
                            }
                            if freeze_defense
                            else {}
                        ),
                    }
                    for row in hearing_rows
                ]
        return frozen

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        if not registry:
            return self._baseline(record, case_id=case_id)
        entries = self._active_entries(registry)
        if not entries:
            return self._baseline(record, case_id=case_id)
        hooks = {
            str(entry.get("hook") or "") for entry in entries
        }
        if "judge.system" in hooks:
            # The intervention changes the judge itself; replay would mask it.
            return self.detector.detect(
                record, case_id=case_id, interventions=registry,
            )
        baseline = self._base_results.get(str(case_id))
        if baseline is None:
            baseline = self._baseline(record, case_id=case_id)
        frozen_stages = self._frozen_stages(baseline, hooks)
        if not frozen_stages:
            # With a settled baseline stage always freezable, an empty
            # envelope means the paired-replay contract is broken; a silent
            # live re-roll would attribute provider noise to the candidate.
            raise ValueError(
                f"frozen replay envelope is empty for {case_id}: the baseline "
                "carries no settled stage to replay"
            )
        return self.detector.detect(
            record,
            case_id=case_id,
            interventions=registry,
            frozen_stages=frozen_stages,
        )

    def evaluate_fresh(
        self,
        cases: Sequence[str],
        *,
        dataset_root: str | Path,
        registry: Mapping[str, Any] | None = None,
        gold_map: Mapping[str, bool | None] | None = None,
        workers: int = 1,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run every stage without cached/frozen baseline replay."""
        direct = _DirectDetectorBackend(self.detector)
        return direct.evaluate(
            cases,
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
            deadline=deadline,
        )


class _DirectDetectorBackend(DetectionBackend):
    name = "detector_v2_fresh"

    def __init__(self, detector: Detector) -> None:
        self.detector = detector

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        return self.detector.detect(
            record,
            case_id=case_id,
            interventions=(registry or build_intervention_registry([])),
        )


class MockDetectorBackend(DetectionBackend):
    """Offline detector with explicit failure injections and intervention effects.

    - Without an explicit ``base_predictions`` entry the mock uses gold as its
      prediction (oracle default), so it is only useful for testing the
      evolution protocol, never for measuring detector quality.
    - ``overrides`` maps intervention_id -> {case_id: prediction|None}.
    """

    name = "mock"

    def __init__(
        self,
        *,
        base_predictions: Mapping[str, bool | None] | None = None,
        overrides: Mapping[str, Mapping[str, bool | None]] | None = None,
        fired_always: bool = True,
    ) -> None:
        self.base_predictions = {
            str(k).replace("__", "/"): v for k, v in (base_predictions or {}).items()
        }
        self.overrides: dict[str, dict[str, bool | None]] = {}
        for intervention_id, values in (overrides or {}).items():
            self.overrides[str(intervention_id)] = {
                str(k).replace("__", "/"): v for k, v in values.items()
            }

    def _active_ids(self, registry: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        if not registry:
            return []
        from detector_v2.interventions import validate_registry

        return validate_registry(registry, require_executable_bounds=True)

    def _deployment(self, registry: Mapping[str, Any] | None) -> dict[str, Any]:
        entries = self._active_ids(registry)
        fired: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        for entry in entries:
            intervention_id = str(entry.get("intervention_id") or "")
            if entry.get("type") == "evidence_check":
                evidence.append({
                    "predicate": (entry.get("body") or {}).get("predicate"),
                    "verdict": "satisfied",
                    "detail": "mock evidence row",
                    "intervention_id": intervention_id,
                    "hook": entry.get("hook"),
                })
            fired.append({
                "intervention_id": intervention_id,
                "hook": entry.get("hook"),
                "type": entry.get("type"),
                "event": "guard_fired" if entry.get("type") == "decision_guard" else "text_injected",
                "detail": "mock backend fired record",
            })
        return {
            "intervention_registry_hash": str(
                (registry or {}).get("content_hash") or ""
            ),
            "active_interventions": [
                str(e.get("intervention_id") or "") for e in entries
            ],
            "fired": fired,
            "evidence": evidence,
            "guard_actions": [],
            "policy_conflict": False,
        }

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        active_ids = {
            str(entry.get("intervention_id") or "")
            for entry in self._active_ids(registry)
        }
        prediction = self.base_predictions.get(case_id)
        if prediction is None:
            prediction = gold
        for intervention_id, values in self.overrides.items():
            if intervention_id in active_ids and case_id in values:
                prediction = values[case_id]
        state = (
            "verified_error" if prediction is True
            else "verified_clean" if prediction is False
            else "unresolved"
        )
        return {
            "schema_version": "detector_v2.detection_result.v1",
            "case_id": case_id,
            "state": state,
            "decision_authority": (
                "authoritative" if prediction is not None else "invalid"
            ),
            "runtime_valid": prediction is not None,
            "responsible_event_id": None,
            "responsible_step": None,
            "incident_occurred": prediction,
            "occurrence_state": state,
            "proof": {
                "state": state,
                "deployment": self._deployment(registry),
                "evidence": self._deployment(registry)["evidence"],
            },
            "stage_status": {},
            "providers": {},
            "authority_failure_reasons": (
                [] if prediction is not None
                else [{"component": "mock", "code": "injected_unresolved"}]
            ),
            "evidence_health": {},
            "provenance": {},
            "_packet_event_count": len(list(
                (record or {}).get("history") or ()
            )),
        }


class CachedRunBackend(DetectionBackend):
    """Replay an existing detector_v2 run tree as the deterministic baseline.

    - Empty registry: returns the cached ``result.json`` with zero LLM cost.
    - Non-empty registry: calls the real detector, but with the cached run's
      judge output as ``frozen_judge`` (unless the registry targets
      ``judge.system``). Candidate arms are cached by registry hash.
    """

    name = "cached_run"

    def __init__(
        self,
        config: Mapping[str, Any],
        run_root: str | Path,
        cases: Sequence[str],
        *,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.config = config
        self.case_ids = [str(case) for case in cases]
        self.results: dict[str, dict[str, Any]] = {}
        for case in self.case_ids:
            safe = str(case).replace("/", "__").replace("\\", "__")
            path = Path(run_root) / "cases" / safe / "result.json"
            self.results[str(case)] = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        self.live = DetectorV2Backend(
            config, baseline_results=self.results,
        )
        cache_path = (
            Path(cache_dir)
            if cache_dir is not None
            else resolve_path(config["work_root"]) / "cache"
        )
        namespace = (
            "cached-" + CACHE_VERSION + "-"
            + fingerprint({
                "run_root": str(Path(run_root).resolve()),
                "detector_config": file_digest(config.get("detector_config")),
                "source": source_digest(),
                "cases": sorted(self.case_ids),
                "baseline_results": self.results,
            })
        )
        self.candidate_cache = CachingBackend(
            self.live, cache_path, namespace=namespace,
        )

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        entries = (registry or {}).get("interventions") or []
        if not entries:
            cached = self.results.get(str(case_id))
            if cached is None:
                raise KeyError(f"no cached baseline result for {case_id}")
            return cached
        return self.candidate_cache.run_case(
            record, case_id=case_id, registry=registry, gold=gold,
        )

    def evaluate_fresh(self, cases: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        return self.live.evaluate_fresh(cases, **kwargs)


class CachingBackend(DetectionBackend):
    def __init__(
        self,
        backend: DetectionBackend,
        cache_dir: str | Path,
        *,
        namespace: str = "default",
    ) -> None:
        self.backend = backend
        self.cache_dir = Path(cache_dir) / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.name = backend.name

    def _path(
        self,
        case_id: str,
        registry: Mapping[str, Any] | None,
        record: Mapping[str, Any],
    ) -> Path:
        safe_case = str(case_id).replace("/", "__").replace("\\", "__")
        record_hash = fingerprint(record)
        return (
            self.cache_dir / registry_key(registry)
            / f"{safe_case}.{record_hash}.json"
        )

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        path = self._path(case_id, registry, record)
        if path.exists():
            try:
                result = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                result = None
            if result is not None:
                # Keep the real backend's frozen-judge baseline in sync with the
                # cached baseline arm, otherwise paired replay would compare the
                # candidate against a different judge roll.
                base_results = getattr(self.backend, "_base_results", None)
                if isinstance(base_results, dict) and not registry:
                    base_results[str(case_id)] = dict(result)
                return result
        result = self.backend.run_case(
            record, case_id=case_id, registry=registry, gold=gold,
        )
        save_json(path, result)
        return result

    def evaluate_fresh(self, cases: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        return self.backend.evaluate_fresh(cases, **kwargs)


def make_backend(
    config: Mapping[str, Any],
    mode: str | None = None,
    *,
    mock_base: Mapping[str, bool | None] | None = None,
    mock_overrides: Mapping[str, Mapping[str, bool | None]] | None = None,
) -> DetectionBackend:
    backend_cfg = config.get("backend") or {}
    name = mode or str(backend_cfg.get("name") or "detector_v2")
    if name == "mock":
        backend: DetectionBackend = MockDetectorBackend(
            base_predictions=mock_base,
            overrides=mock_overrides,
        )
        namespace = "mock-" + CACHE_VERSION + "-" + fingerprint({
            "base": mock_base or {},
            "overrides": mock_overrides or {},
        })
    elif name == "detector_v2":
        backend = DetectorV2Backend(config)
        namespace = "detector_v2-" + CACHE_VERSION + "-" + fingerprint({
            "detector_config": file_digest(config.get("detector_config")),
            "dataset_root": str(resolve_path(config["dataset_root"])),
            "source": source_digest(),
        })
    else:
        raise ValueError(f"unknown evolution backend {name!r}")
    if bool(backend_cfg.get("cache", True)):
        cache_dir = resolve_path(config["work_root"]) / "cache"
        backend = CachingBackend(backend, cache_dir, namespace=namespace)
    return backend


__all__ = [
    "CACHE_VERSION",
    "CachedRunBackend",
    "CachingBackend",
    "DetectionBackend",
    "DetectorV2Backend",
    "MockDetectorBackend",
    "file_digest",
    "fingerprint",
    "make_backend",
    "prediction_from_state",
    "registry_key",
    "source_digest",
]
