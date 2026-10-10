"""Detection backends for paired gold evaluation.

``LiveDetectorBackend`` is the real runtime: it imports nothing except the
public ``detector`` API and executes intervention registries through the
fail-closed hook plan. ``MockDetectorBackend`` is an offline oracle used to
validate gate mechanics and the experiment protocol without burning LLM
budget; its results are marked ``backend=mock`` in every report.
"""

from __future__ import annotations

from detector.replay import requires_fresh_evaluation, stage_invalidated

import json
import copy
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from detector.api import Detector
from detector.config import (
    load_config as load_detector_config,
)
from detector.interventions import (
    build_intervention_registry,
    compile_runtime_plan,
    authority_identity,
)
from detector.identity import build_execution_identity, digest, source_identity, artifact_digest as declared_artifact_digest
from detector.measurement import measurement_status
from detector.packet import build_packet
from detector.result import prediction_state
from detector.runtime import bounded_map, deadline_scope

from evolution.config import resolve_path
from evolution.contracts import save_json
from evolution.splits import load_record


def _registry_component_executors(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The sandbox executor for each component predicate, built on demand.

    A module-level function rather than a lambda so the detector can be handed
    something inspectable, and so the sandbox import stays inside the
    evolution layer where it belongs.
    """
    from evolution.sandbox import component_executor_for_registry

    return component_executor_for_registry(entries)


def _compile_with_component_executors(
    registry: Mapping[str, Any] | None,
) -> Any:
    """Compile a registry with the sandbox executors its component entries need.

    ``sandboxed_component`` entries carry their own source, so the executor is
    derived from the registry rather than injected by a caller. Doing it here
    means every evolution-side execution path -- mock backend, real backend,
    deployment summary -- reaches a component entry the same way, instead of
    each site remembering to build the executors.
    """
    from evolution.sandbox import component_executor_for_registry

    entries: list[Mapping[str, Any]] = []
    if isinstance(registry, Mapping):
        raw = registry.get("interventions")
        if isinstance(raw, list):
            entries = [entry for entry in raw if isinstance(entry, Mapping)]
    return compile_runtime_plan(
        registry,
        component_executors=component_executor_for_registry(entries),
    )




def _prediction_from_result(result: Mapping[str, Any]) -> bool | None:
    """Only authoritative terminal decisions enter evolution metrics."""
    return prediction_state(result, require_authoritative=True)


# Bump whenever the detector-side execution or paired-replay semantics change;
# this invalidates stale result caches instead of silently reusing them.
CACHE_FORMAT = "stage-replay-bound-hearings"

ARTIFACT_MANIFEST_SCHEMA = "evolution.artifact_manifest.v1"


def _valid_stage_entry(
    result: Mapping[str, Any] | None,
    status: Mapping[str, Any] | None,
) -> bool:
    """A persisted stage result/status pair usable as a freeze entry."""
    if not isinstance(result, Mapping) or not isinstance(status, Mapping):
        return False
    return bool(result.get("ok")) == (str(status.get("state") or "") == "ok")


def source_digest() -> str:
    """Digest of the detector + evolution source trees.

    Execution-semantics fingerprint: any change to how arms are replayed or
    gated must invalidate derived caches (audit verdicts, row caches).

    Recursive, so a subpackage or a module moved into one is covered. The
    non-recursive form missed every file below a package root, which meant a
    new runner implementation dropped into a subdirectory could change results
    without changing any fingerprint that was supposed to pin them.

    Test trees are excluded. They are the only subdirectory that exists today,
    and they are not execution semantics: a test never runs during a detection,
    so letting its edits invalidate every cache and refuse every frozen
    protocol would spend the digest's authority on something it is not
    describing. Runtime code, including anything future subpackaged, stays
    covered.
    """
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for package in (root / "detector", root / "evolution"):
        for path in sorted(package.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if "tests" in path.relative_to(package).parts:
                continue
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def artifact_digest(path: str | Path) -> str:
    """Content digest of a declared artifact; missing inputs invalidate the freeze."""
    target = Path(path)
    if target.is_dir():
        digest = hashlib.sha256()
        for item in sorted(
            p for p in target.rglob("*") if p.is_file()
            and "__pycache__" not in p.parts
        ):
            digest.update(str(item.relative_to(target)).encode("utf-8"))
            digest.update(item.read_bytes())
        return digest.hexdigest()
    if not target.is_file():
        raise ValueError(f"artifact not found or not a file: {target}")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def artifact_manifest(*paths: str | Path) -> dict[str, Any]:
    """A freeze-ready manifest of the artifacts a runner depends on.

    Shape: ``{"schema_version", "artifacts": {label: digest}, "digest"}``. The
    manifest digest covers the labels and the per-artifact digests together, so
    swapping two artifacts' contents cannot leave the manifest digest intact.
    An empty manifest is legal and means "this runner declares no external
    artifact" -- which is what a deterministic runner must declare, and is a
    different statement from declaring one and failing to read it.
    """
    artifacts: dict[str, str] = {}
    for path in paths:
        target = Path(path)
        artifacts[str(target)] = artifact_digest(target)
    payload = {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA,
        "artifacts": artifacts,
    }
    return {
        **payload,
        "digest": hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest(),
    }


def fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def registry_key(registry: Mapping[str, Any] | None) -> str:
    if not registry:
        return "empty"
    return str(registry.get("content_hash") or "unsigned")


def _hooks_of(registry: Mapping[str, Any] | None) -> list[str]:
    """The hooks a registry's entries target, for the replay boundary."""
    return sorted({
        str(entry.get("hook") or "")
        for entry in ((registry or {}).get("interventions") or [])
        if str(entry.get("hook") or "")
    })


def backend_runtime_identity(backend: Any, *, cases: Sequence[str] = ()) -> dict[str, Any]:
    underlying = getattr(backend, "backend", backend)
    config = getattr(getattr(underlying, "detector", None), "config", {})
    scorer = (config.get("causal_audit") or {}).get("scorer_artifact")
    evidence_for = getattr(underlying, "evidence_for", None)
    payload = {
        "schema_version": "evolution.backend_runtime_identity.v1",
        "source": source_identity("detector", "evolution"),
        "backend": underlying.name, "effective_config": digest(config),
        "artifacts": {"scorer": declared_artifact_digest(scorer)} if scorer is not None else {},
        "evidence": {str(case): digest(evidence_for(str(case))) for case in sorted(cases)}
        if callable(evidence_for) else {},
        "mock": {"base": getattr(underlying, "base_predictions", None),
                 "overrides": getattr(underlying, "overrides", None)},
    }
    return {**payload, "digest": digest(payload)}


def backend_execution_identity(
    backend: Any, record: Mapping[str, Any], *, case_id: str,
    registry: Mapping[str, Any] | None = None, replay: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    detector = getattr(backend, "detector", None)
    config = getattr(detector, "config", {})
    runtime_identity = backend_runtime_identity(backend)
    evidence_for = getattr(backend, "evidence_for", None)
    evidence = evidence_for(case_id) if callable(evidence_for) else None
    identity = build_execution_identity(
        source=runtime_identity["source"], config=config,
        record=record, packet=build_packet(record), evidence=evidence,
        registry=registry, replay=replay,
        authority=authority_identity(registry),
    )
    payload = {
        "schema_version": "evolution.backend_execution_identity.v1",
        "case_id": str(case_id), "backend": backend.name, "inputs": identity,
        "runtime": runtime_identity,
    }
    return {**payload, "digest": digest(payload)}


def _unresolved_backend_result(
    case_id: str,
    code: str,
    message: str,
    error_type: str,
) -> dict[str, Any]:
    return {
        "schema_version": "detector.detection_result.v1",
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
        gold_step_map: Mapping[str, Any] | None = None,
        workers: int = 1,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate cases paired by id.

        ``deadline`` is a monotonic wall-clock timestamp for the whole batch. A
        case that cannot start before it fail-closes to an unresolved row:
        bounded runtime never silently drops or invents outcomes. The deadline
        also reaches the detector, so a case already running when it passes is
        stopped rather than allowed to exceed it.

        ``gold_step_map`` carries the evaluator-side localization annotation so
        a row can be scored jointly on detection and responsible step. It is
        gold and therefore never reaches the detector.
        """
        gold_map = dict(gold_map or {})
        gold_step_map = dict(gold_step_map or {})

        def one(case_id: str) -> dict[str, Any]:
            started = time.monotonic()
            gold = gold_map.get(case_id, gold_map.get(case_id.replace("/", "__")))
            gold_step = gold_step_map.get(
                case_id, gold_step_map.get(case_id.replace("/", "__")),
            )
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
                    with deadline_scope(deadline=deadline):
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
                "gold_step": gold_step,
                "prediction": _prediction_from_result(result),
                "state": result.get("state"),
                "authority": result.get("decision_authority"),
                "responsible_step": result.get("responsible_step"),
                "responsible_step_kind": result.get("responsible_step_kind"),
                # How this row reads as a *measurement*, in one vocabulary
                # shared with the batch runner. ``prediction is None`` alone
                # conflates "the provider died" with "the case was answered but
                # degraded", and every downstream count needs them apart.
                "measurement": measurement_status(result),
                "failure_reasons": [
                    dict(item) for item in result.get("authority_failure_reasons") or ()
                    if isinstance(item, Mapping)
                ],
                # Wall time this row actually cost, including a cache hit
                # (which is near zero) and a deadline skip. Recorded because a
                # batch's total is dominated by its slowest case: the
                # 2026-09-21 promotion run spent roughly 50 of its 62 minutes
                # on one case, and without a per-row reading that tail is only
                # visible as an unexplained total.
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "result": result,
                "backend": self.name,
            }

        if int(workers) > 1 and len(cases) > 1:
            rows: list[dict[str, Any]] = []
            total = len(cases)
            progress = bool(os.environ.get("EVOLUTION_PROGRESS"))
            # An optional machine-readable per-case line for a live dashboard.
            # It is a log, never an input: nothing downstream reads this file,
            # so a truncated or missing line changes no measurement.
            progress_log = os.environ.get("EVOLUTION_PROGRESS_LOG")
            handle = None
            if progress_log:
                try:
                    handle = open(progress_log, "a", encoding="utf-8")
                except OSError:
                    handle = None
            if progress:
                print(
                    f"[evolution] batch start: {total} cases, workers={workers}, "
                    f"registry={registry_key(registry)}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                for row in bounded_map(one, cases, workers=int(workers)):
                    rows.append(row)
                    if progress:
                        print(
                            f"[evolution] {len(rows)}/{total} {row['case']} -> "
                            f"{row['prediction']} ({row['state']})",
                            file=sys.stderr,
                            flush=True,
                        )
                    if handle is not None:
                        handle.write(json.dumps({
                            "done": len(rows),
                            "total": total,
                            "case": row.get("case"),
                            "prediction": row.get("prediction"),
                            "state": row.get("state"),
                            "authority": row.get("authority"),
                            "measurement": row.get("measurement"),
                            "responsible_step": row.get("responsible_step"),
                            "elapsed_seconds": row.get("elapsed_seconds"),
                        }, ensure_ascii=False) + "\n")
                        handle.flush()
            finally:
                if handle is not None:
                    handle.close()
            return sorted(rows, key=lambda row: str(row["case"]))
        return [one(case) for case in cases]

    def evaluate_fresh(
        self,
        cases: Sequence[str],
        *,
        dataset_root: str | Path,
        registry: Mapping[str, Any] | None = None,
        gold_map: Mapping[str, bool | None] | None = None,
        gold_step_map: Mapping[str, Any] | None = None,
        workers: int = 1,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        return self.evaluate(
            cases,
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            gold_step_map=gold_step_map,
            workers=workers,
            deadline=deadline,
        )


class LiveDetectorBackend(DetectionBackend):
    # Class-level defaults: tests and tools build this backend with
    # ``__new__`` to inject a fake detector, and evidence must read as off
    # rather than raising for them.
    _evidence_provider: Any = None
    _evidence_cache: dict[str, Any] = {}

    """Real detector backend with frozen-judge paired replay.

    The baseline (empty registry) judge output for a case is captured once.
    Any non-empty registry that does not target ``judge.*`` is replayed with
    that frozen judge, so HARMLESSNESS/BEHAVIOR/M compare intervention effects instead of
    two independent LLM rolls of the judge. Registries targeting judge hooks
    are run normally and marked ``frozen_judge_replay=false``.
    """

    name = "detector"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        baseline_results: Mapping[str, Mapping[str, Any]] | None = None,
        evidence_provider: Any | None = None,
    ) -> None:
        detector_config_path = resolve_path(config.get("detector_config"))
        detector_config = load_detector_config(detector_config_path)
        self.detector = Detector(
            detector_config,
            component_executor_factory=_registry_component_executors,
        )
        self._base_results: dict[str, dict[str, Any]] = {
            str(key).replace("__", "/"): dict(value)
            for key, value in (baseline_results or {}).items()
        }
        # An optional callable ``case_id -> snapshot|None``. The snapshot is
        # a harness input: it reaches the detector beside the record, and
        # every cached or frozen artefact must be able to tell which
        # evidence it was produced under.
        self._evidence_provider = evidence_provider
        self._evidence_cache: dict[str, Any] = {}
        # Public alias: reporting code asks whether evidence is attached
        # without reaching into a private attribute.
        self.evidence_provider = evidence_provider

    def evidence_for(self, case_id: str) -> Any | None:
        """The snapshot for one case, memoized (providers may do I/O)."""
        if self._evidence_provider is None:
            return None
        key = str(case_id)
        if key not in self._evidence_cache:
            self._evidence_cache[key] = self._evidence_provider(key)
        return self._evidence_cache[key]

    def evidence_tag(self, case_id: str) -> str:
        """A short, stable cache tag for the evidence a case runs under."""
        snapshot = self.evidence_for(case_id)
        if snapshot is None:
            return "noevidence"
        from detector.evidence import snapshot_digest

        return "ev-" + snapshot_digest(snapshot)[:16]

    def _active_entries(self, registry: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        if not registry:
            return []
        from detector.interventions import validate_registry

        return validate_registry(registry, require_executable_bounds=True)

    def _baseline(self, record: Mapping[str, Any], *, case_id: str) -> dict[str, Any]:
        identity = backend_execution_identity(
            self, record, case_id=case_id, registry=build_intervention_registry([]),
        )
        cached = self._base_results.get(str(case_id))
        if cached is not None and (cached.get("provenance") or {}).get(
            "backend_execution_identity"
        ) == identity:
            return copy.deepcopy(cached)
        snapshot = self.evidence_for(case_id)
        result = self.detector.detect(
            record,
            case_id=case_id,
            interventions=build_intervention_registry([]),
            **(dict(stage_kwargs) if (stage_kwargs := (
                {"evidence": snapshot} if snapshot is not None else {}
            )) else {}),
        )
        result["provenance"] = {**(result.get("provenance") or {}),
                                "backend_execution_identity": identity}
        self._base_results[str(case_id)] = copy.deepcopy(result)
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
        provenance = baseline.get("provenance") or {}
        packet_hash = provenance.get("packet_hash")
        if packet_hash:
            frozen["packet_hash"] = packet_hash
        if provenance.get("evidence_snapshot_digest"):
            # Replay may reuse these stages only for the same evidence; the
            # detector compares this digest with its own active snapshot.
            frozen["evidence_snapshot_digest"] = str(
                provenance["evidence_snapshot_digest"]
            )

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
        if not stage_invalidated("judge_result", hooks) and judge_state == "ok" and isinstance(proof.get("judge"), Mapping):
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
        elif not stage_invalidated("judge_result", hooks) and settled("judge"):
            add("judge", "judge", {
                "ok": False,
                "parsed": None,
                "error": f"judge stage failed in baseline ({judge_state})",
                "error_type": judge_state,
            })
        if (
            not stage_invalidated("recall_result", hooks)
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
            not stage_invalidated("upgrade_result", hooks)
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
        freeze_tracer = not stage_invalidated("tracer_result", hooks)
        freeze_defense = not stage_invalidated("defense_result", hooks)
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
        snapshot = self.evidence_for(case_id)
        evidence_kwargs = {"evidence": snapshot} if snapshot is not None else {}
        if requires_fresh_evaluation(entries):
            return self.detector.detect(
                record, case_id=case_id, interventions=registry,
                **evidence_kwargs,
            )
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
            **evidence_kwargs,
        )

    def evaluate_fresh(
        self,
        cases: Sequence[str],
        *,
        dataset_root: str | Path,
        registry: Mapping[str, Any] | None = None,
        gold_map: Mapping[str, bool | None] | None = None,
        gold_step_map: Mapping[str, Any] | None = None,
        workers: int = 1,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run every stage without cached/frozen baseline replay."""
        direct = _DirectDetectorBackend(self)
        return direct.evaluate(
            cases,
            dataset_root=dataset_root,
            registry=registry,
            gold_map=gold_map,
            gold_step_map=gold_step_map,
            workers=workers,
            deadline=deadline,
        )


class _DirectDetectorBackend(DetectionBackend):
    name = "detector_fresh"

    def __init__(self, backend: LiveDetectorBackend) -> None:
        self.backend = backend

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        snapshot = self.backend.evidence_for(case_id)
        return self.backend.detector.detect(
            record,
            case_id=case_id,
            interventions=(registry or build_intervention_registry([])),
            **({"evidence": snapshot} if snapshot is not None else {}),
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
        from detector.interventions import validate_registry

        return validate_registry(registry, require_executable_bounds=True)

    def _deployment(self, registry: Mapping[str, Any] | None) -> dict[str, Any]:
        plan = _compile_with_component_executors(registry)
        entries = self._active_ids(registry)
        for entry in entries:
            intervention_id = str(entry.get("intervention_id") or "")
            event = (
                "guard_fired" if entry.get("type") == "decision_guard"
                else "text_injected"
            )
            plan._record(entry, event, "mock backend explicit active record")
        summary = plan.deployment_summary()
        summary["active_interventions"] = [
            str(e.get("intervention_id") or "") for e in entries
        ]
        if not summary.get("evidence"):
            summary["evidence"] = [
                {
                    "predicate": (entry.get("body") or {}).get("predicate"),
                    "verdict": "satisfied",
                    "detail": "mock explicit evidence row",
                    "intervention_id": str(entry.get("intervention_id") or ""),
                    "hook": entry.get("hook"),
                }
                for entry in entries
                if entry.get("type") == "evidence_check"
            ]
        return summary

    def _execute_plan(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None,
        prediction: bool | None,
    ) -> tuple[bool | None, Any, list[str]]:
        """Run the real InterventionPlan against the synthetic record.

        This is the mock detector's executable effect model: evidence hooks run
        the deterministic predicate/DSL executors, guard facts are derived from
        the base prediction, and guard actions can flip the terminal prediction.
        ``overrides`` are applied afterwards by ``run_case`` and therefore stay
        the explicit oracle override used by protocol tests.
        """
        plan = _compile_with_component_executors(registry)
        packet = None
        try:
            packet = build_packet(record)
        except Exception:
            packet = None
        if packet is not None:
            context = {
                "contracts": [],
                "evidence_refs": [],
                "delivered_result": "",
                "quote": "",
            }
            plan.evidence_for("judge.post", packet, context)
            plan.evidence_for("upgrade.pre", packet, context)
            plan.evidence_for("defense.pre", packet, context)
            facts = {
                "judge_positive": (
                    "true" if prediction is True else "false"
                ),
                "judge_confidence": (
                    "high" if prediction is True else "low"
                ),
                "positive_source": "judge" if prediction is True else "none",
                "defense_verdict": "none",
                "defense_view_degraded": "false",
                "upgrade_degraded": "false",
                "recall_upgrade": "none",
            }
            actions = plan.guard_actions(packet, fact_rows=[facts])
        else:
            actions = []
        if "convict_mechanical_violation" in actions and prediction is not True:
            prediction = True
        elif "withhold_conviction" in actions and prediction is True:
            prediction = False
        return prediction, plan, actions

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
        plan = None
        actions: list[str] = []
        if registry:
            prediction, plan, actions = self._execute_plan(
                record, case_id=case_id, registry=registry, prediction=prediction,
            )
        for intervention_id, values in self.overrides.items():
            if intervention_id in active_ids and case_id in values:
                prediction = values[case_id]
        state = (
            "verified_error" if prediction is True
            else "verified_clean" if prediction is False
            else "unresolved"
        )
        if plan is not None:
            deployment = plan.deployment_summary()
            # Protocol tests historically use the mock as an explicit oracle:
            # every active entry is recorded as fired unless the executed plan
            # produced a richer trace. Keep the explicit oracle firing/evidence
            # record but preserve the plan's real evidence rows when the
            # trigger actually matched.
            explicit = self._deployment(registry)
            deployment["fired"] = explicit.get("fired") or []
            deployment["active_interventions"] = explicit.get(
                "active_interventions"
            ) or []
            if not deployment.get("evidence"):
                deployment["evidence"] = explicit.get("evidence") or []
            deployment["guard_actions"] = list(actions)
        else:
            deployment = self._deployment(registry)
        return {
            "schema_version": "detector.detection_result.v1",
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
                "deployment": deployment,
                "evidence": list(deployment.get("evidence") or []),
                "terminal_basis": {
                    "decisive_facts": {
                        "positive_source": (
                            "mechanical_violation"
                            if "convict_mechanical_violation" in actions
                            else "judge" if prediction is True else "none"
                        ),
                        "guard_actions": list(actions),
                    },
                },
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




# Windows' legacy path ceiling is 260 characters and an over-long write fails
# with WinError 3 from ``os.replace``, which reads like a missing directory.
# Keep the whole path under a ceiling that also leaves room for the temporary
# sibling file ``save_json`` creates (``.tmp-`` + 8 random chars + ``.json``).
_CACHE_PATH_MAX = 250
_CACHE_TEMP_NAME = ".tmp-XXXXXXXX.json"
_DIGEST_NAME_WIDTH = 16
# Reports use a short boundary label; cache reuse verifies the full input identity.


class CachingBackend(DetectionBackend):
    def __init__(
        self,
        backend: DetectionBackend,
        cache_dir: str | Path,
        *,
        namespace: str = "default",
        extra_key: Any | None = None,
    ) -> None:
        self.backend = backend
        self.cache_dir = Path(cache_dir) / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.name = backend.name
        # An optional ``case_id -> str`` tag for inputs that ride outside
        # the record (an evidence snapshot). Without it in the key, a cached
        # result produced under one snapshot would be served for another.
        self._extra_key = extra_key
        self.evidence_provider = getattr(backend, "evidence_provider", None)

    def _path(
        self,
        case_id: str,
        registry: Mapping[str, Any] | None,
        record: Mapping[str, Any],
    ) -> Path:
        safe_case = str(case_id).replace("/", "__").replace("\\", "__")
        record_hash = digest(record)[:12]
        tag = ""
        if self._extra_key is not None:
            tag = "." + str(self._extra_key(case_id))
        directory = self.cache_dir / digest(registry)
        name = f"{safe_case}.{record_hash}{tag}.json"
        # The name carries case id, record hash and evidence tag, so a
        # snapshot-tagged case under a nested work root can cross the ceiling
        # while its neighbours fit. Past the budget the name becomes a digest
        # of exactly those inputs; a read still verifies the payload's case id
        # and registry hash, so a collision would cost a recomputation and
        # never return another case's result.
        allowance = _CACHE_PATH_MAX - len(str(directory)) - 1
        if len(name) > allowance:
            name = hashlib.sha256(name.encode("utf-8")).hexdigest()
            name = name[:_DIGEST_NAME_WIDTH].rstrip(".") + ".json"
            if len(name) > allowance:
                # A directory deep enough to overflow even the digest cannot
                # be served by a shorter name; fail with the real reason
                # instead of an os.replace that blames the path.
                raise ValueError(
                    f"cache directory leaves {allowance} characters for a "
                    f"file name, which cannot hold even a truncated digest: "
                    f"{directory}"
                )
        return directory / name

    def run_case(
        self,
        record: Mapping[str, Any],
        *,
        case_id: str,
        registry: Mapping[str, Any] | None = None,
        gold: bool | None = None,
    ) -> dict[str, Any]:
        replay = None
        if isinstance(self.backend, LiveDetectorBackend) and (registry or {}).get("interventions"):
            hooks = set(_hooks_of(registry))
            if not requires_fresh_evaluation(self.backend._active_entries(registry)):
                baseline = self.backend._baseline(record, case_id=case_id)
                replay = self.backend._frozen_stages(baseline, hooks)
        identity = {
            "schema_version": "evolution.cache_identity.v2",
            "execution": backend_execution_identity(
                self.backend, record, case_id=case_id, registry=registry, replay=replay,
            ),
            "namespace": self.cache_dir.name,
            "mock_gold": gold if isinstance(self.backend, MockDetectorBackend) else None,
            "extra": str(self._extra_key(case_id)) if self._extra_key is not None else None,
        }
        path = self._path(case_id, registry, record)
        if path.exists():
            try:
                envelope = json.loads(path.read_text(encoding="utf-8-sig"))
                result = envelope.get("result") if isinstance(envelope, Mapping) else None
                valid = (
                    isinstance(envelope, Mapping)
                    and envelope.get("schema_version") == "evolution.cache_entry.v2"
                    and envelope.get("identity") == identity
                    and isinstance(result, Mapping)
                    and envelope.get("result_digest") == digest(result)
                    and result.get("decision_authority") == "authoritative"
                    and str(result.get("case_id") or "") == str(case_id)
                )
            except (OSError, ValueError, TypeError):
                valid = False
            if valid:
                base_results = getattr(self.backend, "_base_results", None)
                if isinstance(base_results, dict) and not (registry or {}).get("interventions"):
                    base_results[str(case_id)] = copy.deepcopy(result)
                return result
        result = self.backend.run_case(
            record, case_id=case_id, registry=registry, gold=gold,
        )
        if result.get("decision_authority") == "authoritative":
            save_json(path, {"schema_version": "evolution.cache_entry.v2", "identity": identity,
                             "result": result, "result_digest": digest(result)})
        return result

    def evaluate_fresh(self, cases: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        return self.backend.evaluate_fresh(cases, **kwargs)


def build_evidence_provider(
    config: Mapping[str, Any],
) -> Any | None:
    """A per-case snapshot provider, or ``None`` when evidence is off.

    Configuration shape (all optional)::

        "evidence": {
          "corpus": "reports/.../corpus",   # corpus directory
          "cases": {"Case/Id.json": "path/to/snapshot.json"}  # explicit pins
        }

    Explicit pins win, and a pinned file is validated like any other
    snapshot. Cases without a pin get a mechanically retrieved snapshot from
    the corpus, or no snapshot at all when nothing in it bears on the case.
    """
    from detector.retrieval import build_case_snapshot, load_corpus

    block = config.get("evidence")
    if not isinstance(block, Mapping) or not block:
        return None
    pinned: dict[str, str] = {}
    raw_pins = block.get("cases")
    if isinstance(raw_pins, Mapping):
        pinned = {str(key): str(value) for key, value in raw_pins.items()}
    corpus = None
    if block.get("corpus"):
        corpus = load_corpus(
            resolve_path(str(block["corpus"]))
        )
    cache: dict[str, Any] = {}

    def provider(case_id: str) -> Any | None:
        key = str(case_id)
        if key in cache:
            return cache[key]
        snapshot = None
        pin = pinned.get(key) or pinned.get(key.replace("/", "__"))
        if pin:
            from detector.evidence import load_snapshot

            snapshot = load_snapshot(resolve_path(pin))
        elif corpus is not None:
            question = str(block.get("questions", {}).get(key) or "") if isinstance(
                block.get("questions"), Mapping
            ) else ""
            if not question:
                if not config.get("dataset_root"):
                    cache[key] = None
                    return None
                root = resolve_path(config["dataset_root"]).resolve()
                target = (root / key).resolve()
                if root not in target.parents:
                    raise ValueError("evidence case path escapes dataset_root")
                record = load_record(root, key)
                question = build_packet(record)["question"]
            snapshot, _report = build_case_snapshot(corpus, question)
        cache[key] = snapshot
        return snapshot

    return provider


def evidence_namespace(
    config: Mapping[str, Any],
    provider: Any | None,
) -> dict[str, Any]:
    """A cache-namespace fingerprint of the evidence configuration."""
    block = config.get("evidence")
    if not isinstance(block, Mapping) or not block or provider is None:
        return {"enabled": False}
    from detector.retrieval import load_corpus

    corpus = load_corpus(resolve_path(str(block["corpus"]))) if block.get("corpus") else None
    return {
        "enabled": True,
        "corpus": declared_artifact_digest(
            resolve_path(str(block["corpus"])) / "corpus.json"
        ) if block.get("corpus") else "none",
        "cases": sorted(str(key) for key in (block.get("cases") or {})),
        "documents": {
            doc["doc_id"]: hashlib.sha256(doc["text"].encode("utf-8")).hexdigest()
            for doc in (corpus["documents"] if corpus else [])
        },
        "pins": {
            str(key): declared_artifact_digest(resolve_path(str(path)))
            for key, path in (block.get("cases") or {}).items()
        },
        "questions": digest(block.get("questions") or {}),
    }


def make_backend(
    config: Mapping[str, Any],
    mode: str | None = None,
    *,
    mock_base: Mapping[str, bool | None] | None = None,
    mock_overrides: Mapping[str, Mapping[str, bool | None]] | None = None,
    evidence_provider: Any | None = None,
) -> DetectionBackend:
    backend_cfg = config.get("backend") or {}
    name = mode or str(backend_cfg.get("name") or "detector")
    if name == "mock":
        backend: DetectionBackend = MockDetectorBackend(
            base_predictions=mock_base,
            overrides=mock_overrides,
        )
        namespace = "mock-" + CACHE_FORMAT + "-" + digest({
            "base": mock_base or {},
            "overrides": mock_overrides or {},
        })[:12]
    elif name == "detector":
        provider = evidence_provider or build_evidence_provider(config)
        backend = LiveDetectorBackend(config, evidence_provider=provider)
        namespace = "detector-" + CACHE_FORMAT + "-" + digest({
            "detector_config": digest(backend.detector.config),
            "dataset_root": str(resolve_path(config["dataset_root"])),
            "source": source_identity("detector", "evolution"),
            "cache_scope": config.get("diagnostic_cache_scope"),
            "evidence": evidence_namespace(config, provider),
        })[:12]
    else:
        raise ValueError(f"unknown evolution backend {name!r}")
    if bool(backend_cfg.get("cache", True)):
        cache_dir = resolve_path(config["work_root"]) / "cache"
        tag = None
        if isinstance(backend, LiveDetectorBackend) and backend._evidence_provider:
            tag = backend.evidence_tag
        backend = CachingBackend(
            backend, cache_dir, namespace=namespace, extra_key=tag,
        )
    return backend


__all__ = [
    "ARTIFACT_MANIFEST_SCHEMA",
    "CACHE_FORMAT",
    "artifact_digest",
    "artifact_manifest",
    "backend_execution_identity",
    "backend_runtime_identity",
    "CachingBackend",
    "DetectionBackend",
    "LiveDetectorBackend",
    "MockDetectorBackend",
    "fingerprint",
    "make_backend",
    "registry_key",
    "source_digest",
]
