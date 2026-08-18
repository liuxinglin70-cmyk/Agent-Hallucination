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
CACHE_VERSION = "v8-source-bound-cache"


def _file_digest(path: str | Path | None) -> str:
    if not path:
        return "missing"
    target = Path(path)
    if not target.exists():
        return "missing"
    return hashlib.sha256(target.read_bytes()).hexdigest()[:16]


def _source_digest() -> str:
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for package in (root / "detector_v2", root / "evolution_v3"):
        for path in sorted(package.glob("*.py")):
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def registry_key(registry: Mapping[str, Any] | None) -> str:
    if not registry:
        return "empty"
    return str(registry.get("content_hash") or "unsigned")


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
    ) -> list[dict[str, Any]]:
        gold_map = dict(gold_map or {})

        def one(case_id: str) -> dict[str, Any]:
            gold = gold_map.get(case_id, gold_map.get(case_id.replace("/", "__")))
            try:
                record = load_record(dataset_root, case_id)
                result = self.run_case(
                    record, case_id=case_id, registry=registry, gold=gold,
                )
            except Exception as exc:
                result = {
                    "schema_version": "detector_v2.detection_result.v1",
                    "case_id": case_id,
                    "state": "unresolved",
                    "decision_authority": "invalid",
                    "runtime_valid": False,
                    "authority_failure_reasons": [{
                        "component": "backend",
                        "code": "case_evaluation_exception",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }],
                    "stage_status": {},
                    "proof": {"deployment": {}, "state": "unresolved"},
                }
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
            with ThreadPoolExecutor(max_workers=int(workers)) as pool:
                futures = [pool.submit(one, case) for case in cases]
                for future in as_completed(futures):
                    rows.append(future.result())
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
    ) -> list[dict[str, Any]]:
        return self.evaluate(
            cases,
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
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
        proof = baseline.get("proof") or {}
        status = baseline.get("stage_status") or {}
        frozen: dict[str, Any] = {}

        def add(name: str, result: dict[str, Any]) -> None:
            frozen[f"{name}_result"] = result
            frozen[f"{name}_status"] = dict(status.get(name) or {})

        judge_status = status.get("judge") or {}
        if isinstance(proof.get("judge"), Mapping) and judge_status.get("state") == "ok":
            add("judge", {"ok": True, "parsed": dict(proof["judge"])})
        if "scanner.system" not in hooks and "judge_result" in frozen:
            recall_status = status.get("recall_scanner") or {}
            add("recall", {
                "ok": recall_status.get("state") == "ok",
                "candidates": list(proof.get("recall_candidates") or []),
            })
        if (
            "upgrade.pre" not in hooks
            and "upgrade.system" not in hooks
            and "recall_result" in frozen
        ):
            upgrade_status = status.get("causal_upgrade") or {}
            add("upgrade", {
                "ok": upgrade_status.get("state") == "ok",
                "upgrades": list(proof.get("causal_upgrade_rows") or []),
                "upgraded_candidate": proof.get("causal_upgrade"),
            })
        if (
            "tracer.system" not in hooks
            and "judge_result" in frozen
            and "recall_result" in frozen
            and "upgrade_result" in frozen
        ):
            tracer_status = status.get("step_tracer") or {}
            add("tracer", {
                "ok": tracer_status.get("state") == "ok",
                "parsed": proof.get("step_tracer"),
            })
        if (
            "defense.pre" not in hooks
            and "defense.system" not in hooks
            and "tracer_result" in frozen
        ):
            defense_status = status.get("defense") or {}
            add("defense", {
                "ok": defense_status.get("state") == "ok",
                "parsed": proof.get("defense"),
            })
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
        if frozen_stages:
            return self.detector.detect(
                record,
                case_id=case_id,
                interventions=registry,
                frozen_stages=frozen_stages,
            )
        return self.detector.detect(
            record, case_id=case_id, interventions=registry,
        )

    def evaluate_fresh(
        self,
        cases: Sequence[str],
        *,
        dataset_root: str | Path,
        registry: Mapping[str, Any] | None = None,
        gold_map: Mapping[str, bool | None] | None = None,
        workers: int = 1,
    ) -> list[dict[str, Any]]:
        """Run every stage without cached/frozen baseline replay."""
        direct = _DirectDetectorBackend(self.detector)
        return direct.evaluate(
            cases,
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            workers=workers,
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
            + _fingerprint({
                "run_root": str(Path(run_root).resolve()),
                "detector_config": _file_digest(config.get("detector_config")),
                "source": _source_digest(),
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
        record_hash = _fingerprint(record)
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
        namespace = "mock-" + CACHE_VERSION + "-" + _fingerprint({
            "base": mock_base or {},
            "overrides": mock_overrides or {},
        })
    elif name == "detector_v2":
        backend = DetectorV2Backend(config)
        namespace = "detector_v2-" + CACHE_VERSION + "-" + _fingerprint({
            "detector_config": _file_digest(config.get("detector_config")),
            "dataset_root": str(resolve_path(config["dataset_root"])),
            "source": _source_digest(),
        })
    else:
        raise ValueError(f"unknown evolution backend {name!r}")
    if bool(backend_cfg.get("cache", True)):
        cache_dir = resolve_path(config["work_root"]) / "cache"
        backend = CachingBackend(backend, cache_dir, namespace=namespace)
    return backend


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "CACHE_VERSION",
    "CachedRunBackend",
    "CachingBackend",
    "DetectionBackend",
    "DetectorV2Backend",
    "MockDetectorBackend",
    "make_backend",
    "prediction_from_state",
    "registry_key",
]
