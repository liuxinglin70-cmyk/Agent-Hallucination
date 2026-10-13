"""Bounded-revision research entry: one mechanism, existing hooks only.

This module is the independent research lane from the current technical
solution (docs/technical-solution.md §7). It is deliberately separate from the
deployment engine: ``run_cycle``, the S/G splits, the four gates and the
active registry are neither consulted nor modified. The entry reads a frozen
research manifest and executes baseline -> draft -> evaluate -> revise ->
select for ONE mechanism, writing immutable artifacts under
``reports/rsi_pilot/<run_id>/``.

Protocol invariants enforced here:

- Intervention surface: by default one ``text_boundary`` on ``judge.system``
  (first-round restriction). A frozen manifest may declare
  ``intervention_surface`` (types, hooks, max_entries) for later rounds; with
  ``max_entries > 1`` every contract-valid entry in one proposer batch
  deploys together as ONE joint candidate version. Any undeclared type or
  hook is rejected before it can spend evaluation budget.
- Training supervision (gold, reviewed target-error descriptions) stays on
  the research side: evaluations run through the normal fresh backend with
  the candidate as the only intervention, and every candidate is checked for
  case-id leakage before it is evaluated.
- Budget: per-stage case-evaluation caps, per-route proposer-call caps and a
  version cap are counted; an exhausted budget stops the run as ``partial``
  instead of padding results.
- Recovery: stored per-version evaluation results may be reused only when the
  case set, registry hash, config hash and code baseline all match; anything
  else forces a fresh evaluation identity.
- Selection never discards failures: ``selection.json`` records every
  attempt, and the promoted stage (frozen paired recheck) unlocks only when
  the screening gate passes.
"""

from __future__ import annotations

from evolution.backends import evaluate_owned_inputs

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.dataset import project_record
from detector.interventions import (
    build_intervention_registry,
    validate_intervention,
)

from evolution.candidate_versions import (
    CandidateVersionGraph,
    canonical_hash,
    case_file_name,
)
from evolution.contracts import candidate_contains_case_id, save_json
from evolution.splits import load_record

RESEARCH_MANIFEST_SCHEMA = "evolution.research_manifest.v1"
RESEARCH_REPORT_SCHEMA = "evolution.research_report.v1"

# First-round restriction (doc §8.2): a candidate version may only carry one
# text boundary on the judge system prompt. Wider surfaces (recall triggers,
# decision guards, new hooks) are later-round work with their own protocol.
ALLOWED_INTERVENTION_TYPE = "text_boundary"
ALLOWED_HOOK = "judge.system"


@dataclass
class ResearchBudget:
    baseline_case_evaluations: int = 0
    screening_case_evaluations: int = 16
    proposer_calls_per_route: int = 3
    max_versions: int = 4
    workers: int = 2
    used_baseline_evaluations: int = 0
    used_screening_evaluations: int = 0
    proposer_calls: dict[str, int] = field(default_factory=dict)
    versions_created: int = 0

    def screening_left(self) -> int:
        return self.screening_case_evaluations - self.used_screening_evaluations

    def baseline_left(self) -> int:
        return self.baseline_case_evaluations - self.used_baseline_evaluations

    def proposer_left(self, route: str) -> int:
        return self.proposer_calls_per_route - self.proposer_calls.get(route, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "caps": {
                "baseline_case_evaluations": self.baseline_case_evaluations,
                "screening_case_evaluations": self.screening_case_evaluations,
                "proposer_calls_per_route": self.proposer_calls_per_route,
                "max_versions": self.max_versions,
            },
            "used": {
                "baseline_case_evaluations": self.used_baseline_evaluations,
                "screening_case_evaluations": self.used_screening_evaluations,
                "proposer_calls": dict(self.proposer_calls),
                "versions_created": self.versions_created,
            },
        }


class ResearchRun:
    """One frozen-manifest research execution for a single mechanism."""

    def __init__(
        self,
        manifest_path: str | Path,
        output_dir: str | Path,
        backend: Any,
        proposer: Any,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.output_dir = Path(output_dir)
        self.backend = backend
        self.proposer = proposer
        raw = json.loads(
            self.manifest_path.read_text(encoding="utf-8-sig"),
        )
        if str(raw.get("schema_version")) != RESEARCH_MANIFEST_SCHEMA:
            raise ValueError(
                f"research manifest schema must be {RESEARCH_MANIFEST_SCHEMA}, "
                f"got {raw.get('schema_version')!r}",
            )
        self.manifest = raw
        cases = self.manifest.get("cases") or []
        self.cases = list(cases)
        self.case_ids = [str(item.get("case_id")) for item in self.cases]
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("research manifest contains duplicate case ids")
        roles = {str(item.get("role")) for item in self.cases}
        if not {"training_failure", "normal_neighbor"} <= roles:
            raise ValueError(
                "research manifest needs >=1 training_failure and "
                ">=1 normal_neighbor case",
            )
        budget = dict(self.manifest.get("budget") or {})
        self.budget = ResearchBudget(
            baseline_case_evaluations=int(budget.get("baseline_case_evaluations", 0)),
            screening_case_evaluations=int(
                budget.get("screening_case_evaluations", 16),
            ),
            proposer_calls_per_route=int(
                budget.get("proposer_calls_per_route", 3),
            ),
            max_versions=int(budget.get("max_versions", 4)),
            workers=int(budget.get("workers", 2)),
        )
        self.dataset_root = self.manifest["dataset_root"]
        # Intervention surface: first-round restriction by default; a frozen
        # manifest may widen it for later rounds (doc §9-style decisions).
        surface = dict(self.manifest.get("intervention_surface") or {})
        self.surface_types = frozenset(
            str(item) for item in (
                surface.get("types") or [ALLOWED_INTERVENTION_TYPE]
            )
        )
        self.surface_hooks = frozenset(
            str(item) for item in (surface.get("hooks") or [ALLOWED_HOOK])
        )
        self.max_entries = max(1, int(surface.get("max_entries") or 1))
        self.gold_map = {
            str(item.get("case_id")): bool(item.get("raw_gold"))
            for item in self.cases
        }
        self.target_steps = {
            str(item.get("case_id")): item.get("target_step")
            for item in self.cases
            if item.get("target_step") is not None
        }
        self.routes: dict[str, dict[str, Any]] = {}
        self.versions: list[dict[str, Any]] = []
        self.stopped: str | None = None
        # Populated by ``execute``; the default exists so a run that stopped
        # in its baseline stage can still write its report (the manifest
        # packet reads this mapping).
        self.baseline_rows: dict[str, dict[str, Any]] = {}
        # The shared version store (also used by the deployment-side development
        # loop). ``self.versions`` / ``self.routes`` below stay as the
        # research-side views the rest of this class reads.
        self.version_graph = CandidateVersionGraph(self.output_dir)

    # ------------------------------------------------------------------
    # stage 0: freeze the run manifest
    def freeze_manifest(self) -> dict[str, Any]:
        manifest_path = self.output_dir / "manifest.json"
        if manifest_path.exists():
            # Frozen exactly once per run directory: a resume must not
            # silently rewrite the run's identity or hashes.
            frozen = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            self.frozen_manifest = frozen
            return frozen
        config = json.loads(
            Path(self.manifest["detector_config"]).read_text(
                encoding="utf-8-sig",
            ),
        )
        integrity_path = self._locate_integrity_manifest()
        integrity = json.loads(integrity_path.read_text(encoding="utf-8-sig"))
        raw_prefix = str(integrity_path.parent / "raw").replace("\\", "/")
        files = integrity.get("files") or {}
        for case_id in self.case_ids:
            rel = str(
                Path(self.dataset_root, case_id).resolve(),
            ).replace("\\", "/")
            if not rel.startswith(raw_prefix + "/"):
                raise ValueError(
                    f"case {case_id!r} lives outside the integrity manifest "
                    f"root {raw_prefix!r}",
                )
            expected = files.get(rel[len(raw_prefix) + 1:])
            if expected is None:
                raise ValueError(
                    f"case {case_id!r} not in datasets/integrity.json",
                )
            actual = hashlib.sha256(
                Path(self.dataset_root, case_id).read_bytes(),
            ).hexdigest()
            if actual != expected:
                raise ValueError(
                    f"case {case_id!r} sha256 mismatch against integrity.json",
                )
        try:
            code_baseline = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip() or "unknown"
        except Exception:
            code_baseline = "unknown"
        frozen = {
            "schema_version": RESEARCH_REPORT_SCHEMA,
            "run_id": self.manifest.get("run_id"),
            "input_manifest": self.manifest,
            "input_manifest_hash": canonical_hash(self.manifest),
            "code_baseline": code_baseline,
            "detector_config_hash": canonical_hash(config),
            "mechanism": self.manifest.get("mechanism") or {},
            "cases": self.cases,
            "routes": list(self.manifest.get("routes") or ["B", "C"]),
            "evaluation_mode": "fresh_full_detection_no_judge_cache",
            "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        save_json(self.output_dir / "manifest.json", frozen)
        return frozen

    def _locate_integrity_manifest(self) -> Path:
        """Find ``datasets/integrity.json`` above the dataset root.

        The real layout is ``datasets/raw/<source>/<split>``; walking upward
        keeps the research entry layout-independent for tests.
        """
        current = Path(self.dataset_root).resolve()
        for _ in range(4):
            candidate = current / "integrity.json"
            if candidate.exists():
                return candidate
            if current.parent == current:
                break
            current = current.parent
        raise FileNotFoundError(
            "integrity.json not found above the dataset root "
            f"{self.dataset_root!r}",
        )

    # ------------------------------------------------------------------
    # stage 1: baseline — reuse shared H0 results, fall back to fresh runs
    def resolve_baseline(self) -> dict[str, dict[str, Any]]:
        baseline_dir = self.output_dir / "baseline"
        references = self.manifest.get("baseline_ref") or {}
        expected_hash = references.get("detector_config_hash")
        rows: dict[str, dict[str, Any]] = {}
        for case_id in self.case_ids:
            reused = None
            for ref in references.get("runs") or []:
                source = Path(ref["run_dir"]) / "cases" / (
                    case_file_name(case_id)
                ) / "result.json"
                if not source.exists():
                    continue
                result = json.loads(source.read_text(encoding="utf-8-sig"))
                if str(result.get("case_id") or "") != case_id:
                    continue
                if result.get("decision_authority") != "authoritative":
                    continue
                if result.get("runtime_valid") is not True:
                    continue
                reused = {"result": dict(result), "source_run": str(ref["run_dir"])}
                break
            if reused is None:
                if self.budget.baseline_left() <= 0:
                    self.stopped = "baseline_budget_exhausted"
                    raise RuntimeError(
                        f"no reusable baseline for {case_id!r} and the "
                        "baseline case-evaluation budget is exhausted",
                    )
                registry = build_intervention_registry([])
                results = evaluate_owned_inputs(self.backend.evaluate_fresh,
                    [case_id],
                    dataset_root=self.dataset_root,
                    registry=registry,
                    gold_map=self.gold_map,
                    workers=1,
                )
                self.budget.used_baseline_evaluations += 1
                row = results[0]
                if row.get("authority") != "authoritative":
                    raise RuntimeError(
                        f"fresh baseline for {case_id!r} is not authoritative",
                    )
                reused = {
                    "result": dict(row.get("result") or {}),
                    "source_run": "fresh_baseline",
                }
            if expected_hash is not None and reused["source_run"] != "fresh_baseline":
                run_manifest_path = (
                    Path(reused["source_run"]) / "run.json"
                )
                if run_manifest_path.exists():
                    run_manifest = json.loads(
                        run_manifest_path.read_text(encoding="utf-8-sig"),
                    )
                    if str(run_manifest.get("config_hash")) != str(expected_hash):
                        raise ValueError(
                            f"baseline run {reused['source_run']} was produced "
                            "under a different detector config hash",
                        )
            save_json(baseline_dir / case_file_name(case_id), reused)
            result = reused["result"]
            rows[case_id] = {
                "state": result.get("state"),
                "prediction": result.get("state") == "verified_error",
                "authority": result.get("decision_authority"),
                "responsible_step": result.get("responsible_step"),
                "source_run": reused["source_run"],
            }
        return rows

    # ------------------------------------------------------------------
    # stage 2: diagnosis inputs (deterministic; no model call)
    def build_family(self) -> dict[str, Any]:
        family = dict(self.manifest.get("family") or {})
        failures = [
            str(item.get("case_id"))
            for item in self.cases
            if item.get("role") == "training_failure"
        ]
        family["failure_cases"] = failures
        family["count"] = len(failures)
        diagnostics = dict(family.get("diagnostics") or {})
        members = []
        for case_id in failures:
            record = project_record(load_record(self.dataset_root, case_id))
            members.append({
                "case_id": case_id,
                "typed_samples": {
                    "question_text": record.get("question") or "",
                    "event_texts": [
                        str(event.get("content") or "")
                        for event in (record.get("history") or [])
                        if event.get("content")
                    ],
                },
                "target_error": str(
                    (self.manifest.get("mechanism") or {}).get(
                        "target_error_description", ""
                    )
                ),
            })
        diagnostics["member_summaries"] = members
        family["diagnostics"] = diagnostics
        return family

    def build_research_packet(self) -> dict[str, Any]:
        """Full-trajectory packet for route C (doc §8.2).

        Gold and label rationale stay out: the packet carries the
        post-projection records, the per-stage baseline readouts and the
        reviewed target-error descriptions from the frozen manifest.
        """
        training, neighbors = [], []
        for item in self.cases:
            record = project_record(load_record(self.dataset_root, str(item["case_id"])))
            entry = {
                "role": item.get("role"),
                "record": record,
                "baseline": self.baseline_rows.get(str(item["case_id"])),
            }
            if item.get("target_error"):
                entry["target_error"] = str(item["target_error"])
            if item.get("role") == "training_failure":
                training.append(entry)
            else:
                neighbors.append(entry)
        return {
            "mechanism": self.manifest.get("mechanism") or {},
            "constraints": {
                "allowed_intervention_types": sorted(self.surface_types),
                "allowed_hooks": sorted(self.surface_hooks),
                "max_interventions_per_candidate": self.max_entries,
                "joint_candidate_convention": (
                    "when more than one intervention is allowed, every "
                    "contract-valid intervention in the returned array is "
                    "deployed together as ONE joint candidate version"
                ) if self.max_entries > 1 else "",
                "boundary_rule": (
                    "the directive must include an explicit not_when: tasks "
                    "that do not quote the exact required content allow "
                    "paraphrase, renumbering and summary presentation"
                ),
            },
            "training_cases": training,
            "normal_neighbors": neighbors,
        }

    # ------------------------------------------------------------------
    # stage 3: drafting through the existing proposer
    def _count_proposer_call(self, route: str) -> None:
        self.budget.proposer_calls[route] = (
            self.budget.proposer_calls.get(route, 0) + 1
        )

    def _draft(self, route: str, *, packet: Mapping[str, Any] | None) -> Any:
        """One proposer call; returns the research-contract candidate or None."""
        if self.budget.proposer_left(route) <= 0:
            self.routes[route]["notes"].append({
                "reason": "proposer_call_budget_exhausted",
            })
            return None
        self._count_proposer_call(route)
        packet = dict(packet or {})
        if hasattr(self.proposer, "set_research_packet"):
            self.proposer.set_research_packet(packet.get("research_packet"))
        if hasattr(self.proposer, "set_memory_context"):
            self.proposer.set_memory_context(packet.get("memory_context"))
        if hasattr(self.proposer, "set_rejection_feedback"):
            self.proposer.set_rejection_feedback(
                packet.get("rejection_feedback") or [],
            )
        batch = self.proposer.propose(
            [self.build_family()],
            cycle=int(packet.get("cycle") or 1),
            max_proposals=int(packet.get("max_proposals") or 3),
        )
        notes = list(batch.notes)
        if self.max_entries > 1:
            # Joint-candidate mode: every contract-valid entry in the batch
            # deploys together as one candidate version. Oversized or partly
            # invalid batches fail closed instead of being trimmed.
            accepted: list[Mapping[str, Any]] = []
            for candidate in batch.candidates:
                verdict, reason = self._candidate_verdict(candidate)
                if verdict != "accepted":
                    notes.append({
                        "intervention_id": candidate.get("intervention_id"),
                        "rejected": reason,
                    })
                    continue
                accepted.append(candidate)
            if not accepted:
                return {"candidate": None, "notes": notes}
            verdict, reason = self._joint_verdict(accepted)
            if verdict != "accepted":
                notes.append({"joint_rejected": reason})
                return {"candidate": None, "notes": notes}
            return {"candidate": list(accepted), "notes": notes}
        for candidate in batch.candidates:
            verdict, reason = self._candidate_verdict(candidate)
            if verdict != "accepted":
                notes.append({
                    "intervention_id": candidate.get("intervention_id"),
                    "rejected": reason,
                })
                continue
            return {"candidate": candidate, "notes": notes}
        return {"candidate": None, "notes": notes}

    def _candidate_verdict(
        self, candidate: Mapping[str, Any],
    ) -> tuple[str, str]:
        if str(candidate.get("type") or "") not in self.surface_types:
            return "rejected", "research_type_not_allowed"
        if str(candidate.get("hook") or "") not in self.surface_hooks:
            return "rejected", "research_hook_not_allowed"
        leaked = candidate_contains_case_id(candidate)
        if leaked:
            return "rejected", f"case_id_leakage:{leaked}"
        try:
            validate_intervention(candidate, require_executable_bounds=True)
        except ValueError as exc:
            return "rejected", f"invalid_contract:{exc}"
        return "accepted", ""

    def _joint_verdict(
        self, entries: Sequence[Mapping[str, Any]],
    ) -> tuple[str, str]:
        """Validate a multi-entry joint candidate against the run surface."""
        if not entries:
            return "rejected", "joint_candidate_empty"
        if len(entries) > self.max_entries:
            return "rejected", (
                f"joint_candidate_too_large:{len(entries)}>"
                f"{self.max_entries}"
            )
        hooks: list[str] = []
        for entry in entries:
            verdict, reason = self._candidate_verdict(entry)
            if verdict != "accepted":
                return "rejected", f"entry:{reason}"
            hooks.append(str(entry.get("hook") or ""))
        if len(set(hooks)) != len(hooks):
            return "rejected", "joint_candidate_duplicate_hooks"
        return "accepted", ""

    # ------------------------------------------------------------------
    # stage 4: paired screening evaluation of one candidate version
    def _registry_for(
        self, candidate: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if isinstance(candidate, Mapping):
            return build_intervention_registry([dict(candidate)])
        return build_intervention_registry([dict(entry) for entry in candidate])

    def _version_identity(
        self, candidate: Mapping[str, Any], registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.version_graph.identity(
            candidate,
            case_ids=self.case_ids,
            registry_hash=str(registry.get("content_hash") or ""),
            config_hash=self.frozen_manifest["detector_config_hash"],
            code_baseline=self.frozen_manifest["code_baseline"],
            evaluation_mode="fresh_full_detection_no_judge_cache",
        )

    def _find_reusable_results(
        self, identity: Mapping[str, Any],
    ) -> tuple[str | None, dict[str, dict[str, Any]] | None]:
        """Reuse prior stored results with a fully identical identity.

        Recovery may reuse results only when input, code, registry and model
        envelope all match (doc §8.3); the identity bundle encodes exactly
        that. Incomplete stored sets are ignored.
        """
        return self.version_graph.find_reusable_results(
            identity, required_cases=self.case_ids,
        )

    def _evaluate_version(self, route: str, version_id: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
        version_dir = self.output_dir / version_id
        registry = self._registry_for(candidate)
        identity = self._version_identity(candidate, registry)
        source_version, reused_results = self._find_reusable_results(identity)
        if reused_results is not None:
            return {
                "identity": identity,
                "results": reused_results,
                "reused": True,
                "source_version": source_version,
            }
        identity_path = version_dir / "identity.json"
        results_dir = version_dir / "results"
        if identity_path.exists():
            # Declared evaluation identity for this version slot: it may only
            # be completed under the same identity; anything else demands a
            # new version id instead of overwriting results.
            stored = json.loads(identity_path.read_text(encoding="utf-8-sig"))
            if stored != dict(identity):
                raise ValueError(
                    f"{version_id} already exists with a different evaluation "
                    "identity; a changed prompt/code/registry requires a new "
                    "version id instead of overwriting results",
                )
            cases, results = self._load_stored_results(results_dir)
        else:
            save_json(identity_path, identity)
            results = {}
        missing = [
            case_id for case_id in self.case_ids if case_id not in results
        ]
        if missing:
            if self.budget.screening_left() < len(missing):
                self.stopped = "screening_budget_exhausted"
                raise RuntimeError(
                    "screening case-evaluation budget cannot cover the "
                    f"missing cases ({len(missing)} missing, "
                    f"{self.budget.screening_left()} left)",
                )
            rows = evaluate_owned_inputs(self.backend.evaluate_fresh,
                missing,
                dataset_root=self.dataset_root,
                registry=registry,
                gold_map=self.gold_map,
                workers=self.budget.workers,
            )
            self.budget.used_screening_evaluations += len(missing)
            for row in rows:
                case_id = str(row.get("case"))
                result = dict(row.get("result") or {})
                entry = {
                    "case_id": case_id,
                    "role": self._role_of(case_id),
                    "gold": self.gold_map.get(case_id),
                    "state": result.get("state"),
                    "authority": result.get("decision_authority"),
                    "prediction": result.get("state") == "verified_error",
                    "responsible_step": result.get("responsible_step"),
                    "registry_hash": identity["registry_hash"],
                    "runtime_valid": result.get("runtime_valid"),
                    "result": result,
                }
                results[case_id] = entry
                save_json(
                    results_dir / case_file_name(case_id), entry,
                )
        return {
            "identity": identity,
            "results": results,
            "reused": len(missing) == 0,
        }

    def _load_stored_results(self, results_dir: Path) -> tuple[set[str], dict[str, dict[str, Any]]]:
        return self.version_graph.load_results(results_dir)

    def _role_of(self, case_id: str) -> str:
        for item in self.cases:
            if str(item.get("case_id")) == case_id:
                return str(item.get("role"))
        return "unknown"

    # ------------------------------------------------------------------
    # stage 5: screening verdict against the frozen protocol gate
    def screening_verdict(
        self, candidate: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        training, neighbors, reasons = {}, {}, []
        for case_id in self.case_ids:
            row = results[case_id]
            role = row.get("role")
            state = row.get("state")
            authoritative = row.get("authority") == "authoritative"
            if role == "training_failure":
                repaired = (
                    bool(row.get("prediction"))
                    and state == "verified_error"
                    and authoritative
                )
                target_step = self.target_steps.get(case_id)
                target_ok = (
                    row.get("responsible_step") is None
                    or target_step is None
                    or int(row.get("responsible_step") or -1) == int(target_step)
                )
                training[case_id] = {
                    "repaired": repaired and target_ok,
                    "state": state,
                    "responsible_step": row.get("responsible_step"),
                    "target_step": target_step,
                    "target_aligned": target_ok,
                }
            else:
                clean = (
                    not bool(row.get("prediction"))
                    and state == "verified_clean"
                    and authoritative
                )
                neighbors[case_id] = {"clean": clean, "state": state}
                if not clean:
                    reasons.append(
                        f"normal neighbor {case_id} degraded to {state!r}"
                    )
        passed = (
            all(item["repaired"] for item in training.values())
            and all(item["clean"] for item in neighbors.values())
        )
        if not passed and not reasons:
            for case_id, item in training.items():
                if not item["repaired"]:
                    reasons.append(
                        f"training failure {case_id} not repaired "
                        f"(state={item['state']!r}, "
                        f"step={item['responsible_step']!r})"
                    )
        return {
            "passed": passed,
            "training": training,
            "neighbors": neighbors,
            "reasons": reasons,
        }

    # ------------------------------------------------------------------
    # Version bookkeeping is delegated to ``evolution.candidate_versions``, the
    # store the deployment-side development loop also uses. The disk format and
    # the numbering rule are that component's contract; these wrappers keep the
    # research-side names and the ``self.versions`` / ``self.routes`` views the
    # rest of this class and its tests read.
    def _new_version_id(self) -> str:
        # The version cap is enforced here, at the one place every new version
        # passes through. Reporting ``caps.max_versions`` next to a larger
        # ``used.versions_created`` made the budget a promise the run could
        # break silently; exceeding it now stops the run as ``partial``
        # instead of creating a version that was never budgeted.
        if self.version_graph.versions_created >= int(self.budget.max_versions):
            raise RuntimeError(
                f"version budget exhausted: {self.version_graph.versions_created} "
                f"version(s) already created, max_versions="
                f"{int(self.budget.max_versions)}"
            )
        version_id = self.version_graph.new_version_id()
        self.budget.versions_created = self.version_graph.versions_created
        return version_id

    def _load_existing_versions(self) -> None:
        self.version_graph.versions_created = self.budget.versions_created
        self.version_graph.load_existing()
        self.budget.versions_created = self.version_graph.versions_created
        for record in self.version_graph.versions:
            self.versions.append(dict(record))
            route = str(record.get("route") or "?")
            self.routes.setdefault(route, {"versions": [], "notes": []})
            version_id = str(record.get("version_id"))
            if version_id not in self.routes[route]["versions"]:
                self.routes[route]["versions"].append(version_id)

    def _record_version(
        self,
        route: str,
        version_id: str,
        candidate: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        *,
        parent: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        diagnosis: Mapping[str, Any] | None,
        draft_notes: Sequence[Mapping[str, Any]],
        evaluation: Mapping[str, Any] | None,
        verdict: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        record = self.version_graph.record(
            route, version_id, candidate,
            parent=parent, diagnosis=diagnosis, draft_notes=draft_notes,
            evaluation=evaluation, verdict=verdict,
        )
        self.versions.append(record)
        self.routes.setdefault(route, {"versions": [], "notes": []})
        self.routes[route]["versions"].append(version_id)
        self.budget.versions_created = max(
            self.budget.versions_created, self.version_graph.versions_created,
        )
        return record

    def _no_change_version(
        self, route: str, draft: Mapping[str, Any] | None, packet: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        notes = list((draft or {}).get("notes") or [])
        version_id = self._new_version_id()
        return self._record_version(
            route, version_id, None, parent=None, diagnosis=None,
            draft_notes=[*notes, {"no_change": True, "reason": "no research-contract candidate"}],
            evaluation=None, verdict=None,
        )

    # ------------------------------------------------------------------
    def _screen_route(self, route: str, packet: Mapping[str, Any]) -> dict[str, Any]:
        draft = self._draft(route, packet=packet)
        candidate = (draft or {}).get("candidate")
        if candidate is None:
            return {"route": route, "outcome": "no_change", "record": self._no_change_version(route, draft, packet)}
        version_id = self._new_version_id()
        evaluation = self._evaluate_version(route, version_id, candidate)
        verdict = self.screening_verdict(candidate, evaluation["results"])
        record = self._record_version(
            route, version_id, candidate, parent=None, diagnosis=None,
            draft_notes=list((draft or {}).get("notes") or []),
            evaluation=evaluation, verdict=verdict,
        )
        return {"route": route, "outcome": "screened", "record": record}

    # Route cycle offsets: the proposer's LLM budget is counted per cycle on
    # the shared instance, so two routes sharing cycle 1 would silently starve
    # the second route (observed live: route C's draft came back
    # "llm_calls budget exhausted"). B drafts at cycle 1 / revises at 2; C
    # drafts at 3 / revises at 4.
    ROUTE_DRAFT_CYCLE = {"B": 1, "C": 3}
    ROUTE_REVISION_CYCLE = {"B": 2, "C": 4}

    def _revision_packet(
        self, route: str, parent: Mapping[str, Any], diagnosis: Mapping[str, Any],
    ) -> dict[str, Any]:
        base = dict(self._route_packet(route))
        base["cycle"] = self.ROUTE_REVISION_CYCLE.get(route, 2)
        if route == "C":
            packet = dict(base.get("research_packet") or {})
            packet["revision"] = diagnosis
            base["research_packet"] = packet
        else:
            base["rejection_feedback"] = [{
                "gate": "RESEARCH_SCREEN",
                "candidate": (
                    [dict(entry) for entry in parent]
                    if not isinstance(parent, Mapping) else dict(parent)
                ),
                "type": parent.get("type") if isinstance(parent, Mapping) else None,
                "hook": parent.get("hook") if isinstance(parent, Mapping) else None,
                "reason": "; ".join(diagnosis.get("reasons") or []),
                "delta": None,
                "not_evaluable": False,
            }]
        return base

    def _route_packet(self, route: str) -> dict[str, Any]:
        packet: dict[str, Any] = {
            "cycle": self.ROUTE_DRAFT_CYCLE.get(route, 1),
            "max_proposals": 3,
            "memory_context": self.manifest.get("memory_context") or {},
        }
        if route == "C":
            packet["research_packet"] = self.build_research_packet()
        return packet

    def _maybe_revise(self, route: str, screened: Mapping[str, Any]) -> dict[str, Any] | None:
        verdict = screened["record"].get("verdict") or {}
        if verdict.get("passed") or self.budget.proposer_left(route) <= 0:
            return None
        parent = screened["record"].get("candidate")
        if not parent:
            return None
        return self._revise(route, parent, verdict)

    def _revise(
        self, route: str, parent: Mapping[str, Any],
        verdict: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        diagnosis = {
            "parent_hash": canonical_hash(parent),
            "reasons": list(verdict.get("reasons") or []),
            "per_case": {
                "training": verdict.get("training") or {},
                "neighbors": verdict.get("neighbors") or {},
            },
            "instruction": (
                "revise the parent candidate so the described verification "
                "operation actually repairs the failing training cases while "
                "leaving the normal neighbors untouched; keep the same "
                "intervention type and hook"
            ),
        }
        revision = self._draft(route, packet=self._revision_packet(route, parent, diagnosis))
        candidate = (revision or {}).get("candidate")
        if candidate is None:
            self._no_change_version(route, revision, None)
            return None
        version_id = self._new_version_id()
        evaluation = self._evaluate_version(route, version_id, candidate)
        verdict2 = self.screening_verdict(candidate, evaluation["results"])
        record = self._record_version(
            route, version_id, candidate, parent=parent, diagnosis=diagnosis,
            draft_notes=list((revision or {}).get("notes") or []),
            evaluation=evaluation, verdict=verdict2,
        )
        return {"route": route, "outcome": "screened", "record": record}

    # ------------------------------------------------------------------
    def select(self) -> dict[str, Any]:
        selection: dict[str, Any] = {"routes": {}, "all_attempts": self.versions}
        for route, info in self.routes.items():
            passing = []
            for version_id in info["versions"]:
                record = next(
                    (v for v in self.versions if v["version_id"] == version_id),
                    None,
                )
                if record and record.get("candidate") and (record.get("verdict") or {}).get("passed"):
                    passing.append(version_id)
            selection["routes"][route] = {
                "versions": list(info["versions"]),
                "notes": list(info["notes"]),
                "selected": passing[-1] if passing else None,
                "all_passing": passing,
            }
        save_json(self.output_dir / "selection.json", selection)
        return selection

    def write_report(self, status: str) -> dict[str, Any]:
        selection = self.selection or {}
        report = {
            "schema_version": RESEARCH_REPORT_SCHEMA,
            "run_id": self.manifest.get("run_id"),
            "status": status,
            "mechanism": self.manifest.get("mechanism") or {},
            "routes": selection.get("routes") or {},
            "versions": [
                {
                    "version_id": v["version_id"],
                    "route": v["route"],
                    "kind": v["kind"],
                    "has_candidate": bool(v.get("candidate")),
                    "candidate_hash": v.get("candidate_hash"),
                    "parent_hash": v.get("parent_hash"),
                    "verdict_passed": (v.get("verdict") or {}).get("passed"),
                }
                for v in self.versions
            ],
            "budget": self.budget.as_dict(),
            "stopped": self.stopped,
            "next_stage": (
                "frozen_paired_recheck"
                if status == "screening_complete" and any(
                    (s.get("selected") for s in (selection.get("routes") or {}).values())
                ) else None
            ),
        }
        save_json(self.output_dir / "report.json", report)
        lines = [
            f"# Research run {self.manifest.get('run_id')} — {status}",
            "",
            f"- mechanism: {(self.manifest.get('mechanism') or {}).get('name')}",
            f"- evaluation mode: fresh full detection, no judge cache",
            f"- budget: {json.dumps(self.budget.as_dict(), ensure_ascii=False)}",
            "",
        ]
        for v in self.versions:
            verdict = v.get("verdict") or {}
            lines.append(
                f"## {v['version_id']} (route {v['route']}, {v['kind']}) — "
                f"{'PASS' if verdict.get('passed') else 'fail / no candidate'}",
            )
            if verdict:
                lines.append(f"- reasons: {json.dumps(verdict.get('reasons') or [], ensure_ascii=False)}")
                lines.append(f"- training: {json.dumps(verdict.get('training') or {}, ensure_ascii=False)}")
                lines.append(f"- neighbors: {json.dumps(verdict.get('neighbors') or {}, ensure_ascii=False)}")
            lines.append("")
        (self.output_dir / "report.md").write_text(
            "\n".join(lines), encoding="utf-8",
        )
        return report

    # ------------------------------------------------------------------
    def _route_records(self, route: str) -> list[dict[str, Any]]:
        return [v for v in self.versions if str(v.get("route")) == route]

    @staticmethod
    def _draft_reached_provider(record: Mapping[str, Any]) -> bool:
        # A draft that never reached the provider (infrastructure failure,
        # e.g. a starved per-cycle budget) did not test the route and may be
        # redrafted on resume; a real empty provider return is terminal.
        return any(
            note.get("provider_called") for note in record.get("notes") or []
        )

    # ------------------------------------------------------------------
    def _seed_route_candidate(self, spec: Mapping[str, Any]) -> None:
        """Load a frozen candidate from a prior run into this one.

        A seed lets an instrument round re-measure an already-frozen
        candidate without re-drafting: the manifest pins the source run,
        version and candidate hash, the record is re-validated by the same
        contract checks as a fresh draft, and it is re-screened under this
        run's own evaluation identity. Any mismatch fails closed before
        evaluation budget is spent; a seed never bypasses the screening
        verdict and never touches the proposer budget.
        """
        spec = dict(spec)
        route = str(spec.get("route") or "C")
        source = (
            Path(spec["source_run"]) / str(spec["version"]) / "candidate.json"
        )
        try:
            record = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"seed candidate record unreadable at {source!r}: {exc}"
            ) from exc
        candidate = record.get("candidate")
        if not isinstance(candidate, Mapping) and not isinstance(candidate, list):
            raise RuntimeError(
                f"seed candidate record {source!r} carries no candidate"
            )
        pinned = str(spec.get("candidate_hash") or "")
        actual = canonical_hash(candidate)
        if not pinned or actual != pinned:
            raise RuntimeError(
                f"seed candidate hash mismatch: manifest pins {pinned!r}, "
                f"source record hashes to {actual!r}"
            )
        if isinstance(candidate, list):
            verdict, reason = self._joint_verdict(candidate)
        else:
            verdict, reason = self._candidate_verdict(candidate)
        if verdict != "accepted":
            raise RuntimeError(
                f"seed candidate rejected by contract: {reason}"
            )
        version_id = self._new_version_id()
        evaluation = self._evaluate_version(route, version_id, candidate)
        screening = self.screening_verdict(candidate, evaluation["results"])
        self._record_version(
            route, version_id, candidate, parent=None, diagnosis=None,
            draft_notes=[{
                "seeded_from": {
                    "source_run": str(spec["source_run"]),
                    "version": str(spec["version"]),
                    "candidate_hash": pinned,
                },
            }],
            evaluation=evaluation, verdict=screening,
        )

    def execute(self) -> dict[str, Any]:
        self.frozen_manifest = self.freeze_manifest()
        self._load_existing_versions()
        # The baseline resolves inside the budget's own try: an exhausted
        # baseline budget is a budget terminal state like any other, and the
        # documented behaviour is a ``partial`` report -- not a traceback with
        # no report at all. Writing the baseline summary is part of the same
        # attempt, so a run that stopped at its baseline still produces the
        # report its reader needs.
        seed_spec = dict(
            (self.frozen_manifest.get("input_manifest") or {}).get(
                "seed_candidate"
            )
            or {}
        )
        status = "screening_complete"
        try:
            self.baseline_rows = self.resolve_baseline()
            save_json(
                self.output_dir / "baseline" / "summary.json",
                self.baseline_rows,
            )
            if seed_spec:
                seed_route = str(seed_spec.get("route") or "C")
                # Idempotent on resume: the seed is already on disk as a
                # route version, so a partial run continues instead of
                # re-seeding.
                if not self._route_records(seed_route):
                    self._seed_route_candidate(seed_spec)
            for route in self.frozen_manifest["routes"]:
                self.routes.setdefault(route, {"versions": [], "notes": []})
                records = self._route_records(route)
                candidates = [v for v in records if v.get("candidate")]
                if any(
                    (v.get("verdict") or {}).get("passed")
                    for v in candidates
                ):
                    # This route already froze a screening-passing version.
                    continue
                if candidates:
                    # A draft exists and failed: only its one revision may
                    # still run on a resume.
                    if not any(
                        v.get("kind") == "revision" for v in records
                    ):
                        latest = candidates[-1]
                        self._revise(
                            route, latest["candidate"],
                            latest.get("verdict") or {},
                        )
                    continue
                drafts = [v for v in records if v.get("kind") == "draft"]
                if drafts and self._draft_reached_provider(drafts[-1]):
                    # A real no-change draft is terminal for this route.
                    continue
                screened = self._screen_route(route, self._route_packet(route))
                self._maybe_revise(route, screened)
        except RuntimeError as exc:
            status = "partial"
            self.stopped = self.stopped or str(exc)
        self.selection = self.select()
        report = self.write_report(status)
        return report


def run_frozen_repeats(
    manifest_path: str | Path,
    run_dir: str | Path,
    backend: Any,
    *,
    stage: str,
    case_ids: Sequence[str],
    case_meta: Mapping[str, Mapping[str, Any]],
    repetitions: int,
    budget_case_evaluations: int,
    workers: int = 2,
) -> dict[str, Any]:
    """Fixed-repetition paired evaluation after the screening gate.

    Stage ``frozen_recheck`` pairs H0 with every route's selected version on
    the training set; stage ``unused_dev_validation`` does the same on a
    frozen candidate-unused development list. Protocol rules enforced here:
    the repetition count is fixed in advance, every repetition is reported
    (the best round is never selected post hoc), each (arm, repetition) pair
    is a full fresh detection with its own identity bundle, and stored
    results are reused only under an identical identity.

    ``case_meta`` maps case_id -> {"raw_gold": bool, "target_step": int|None,
    "role": str}; it is research-side supervision and never reaches the
    detector.
    """
    run_dir = Path(run_dir)
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8-sig"),
    )
    selection = json.loads(
        (run_dir / "selection.json").read_text(encoding="utf-8-sig"),
    )
    arms: list[dict[str, Any]] = [{"arm": "h0", "version_id": None, "candidate": None}]
    for route, info in (selection.get("routes") or {}).items():
        selected = info.get("selected")
        if not selected:
            continue
        record = json.loads(
            (run_dir / selected / "candidate.json").read_text(
                encoding="utf-8-sig",
            ),
        )
        arms.append({
            "arm": selected, "version_id": selected,
            "candidate": record.get("candidate"),
        })
    gold_map = {
        case_id: bool(case_meta[case_id]["raw_gold"]) for case_id in case_ids
    }
    target_steps = {
        case_id: case_meta[case_id].get("target_step")
        for case_id in case_ids
        if case_meta[case_id].get("target_step") is not None
    }
    stage_dir = run_dir / stage
    used = 0
    stopped: str | None = None
    arm_reports: dict[str, Any] = {}
    for arm in arms:
        candidate = arm["candidate"]
        if candidate is None:
            registry = build_intervention_registry([])
            candidate_hash = None
        elif isinstance(candidate, Mapping):
            registry = build_intervention_registry([dict(candidate)])
            candidate_hash = canonical_hash(candidate)
        else:
            registry = build_intervention_registry(
                [dict(entry) for entry in candidate],
            )
            candidate_hash = canonical_hash(list(candidate))
        reps: list[dict[str, Any]] = []
        for rep in range(1, int(repetitions) + 1):
            identity = {
                "stage": stage,
                "arm": arm["arm"],
                "candidate_hash": candidate_hash,
                "registry_hash": str(registry.get("content_hash") or ""),
                "repetition": rep,
                "case_set": list(case_ids),
                "config_hash": manifest["detector_config_hash"],
                "code_baseline": manifest["code_baseline"],
                "evaluation_mode": "fresh_full_detection_no_judge_cache",
            }
            arm_dir = stage_dir / arm["arm"] / f"rep{rep:02d}"
            identity_path = arm_dir / "identity.json"
            results_dir = arm_dir / "results"
            results: dict[str, dict[str, Any]] = {}
            if identity_path.exists():
                stored = json.loads(identity_path.read_text(encoding="utf-8-sig"))
                if stored != identity:
                    raise ValueError(
                        f"{stage}/{arm['arm']}/rep{rep} exists with a "
                        "different identity; repetitions are frozen and "
                        "must not be silently re-run",
                    )
                for path in results_dir.glob("*.json"):
                    entry = json.loads(path.read_text(encoding="utf-8-sig"))
                    results[str(entry.get("case_id"))] = entry
            missing = [c for c in case_ids if c not in results]
            if missing:
                if used + len(missing) > budget_case_evaluations:
                    stopped = (
                        f"{stage} case-evaluation budget exhausted "
                        f"({used} used, {len(missing)} missing)"
                    )
                    break
                rows = evaluate_owned_inputs(backend.evaluate_fresh,
                    missing,
                    dataset_root=manifest["input_manifest"]["dataset_root"],
                    registry=registry,
                    gold_map=gold_map,
                    workers=workers,
                )
                used += len(missing)
                for row in rows:
                    case_id = str(row.get("case"))
                    result = dict(row.get("result") or {})
                    entry = {
                        "case_id": case_id,
                        "gold": gold_map.get(case_id),
                        "state": result.get("state"),
                        "authority": result.get("decision_authority"),
                        "prediction": result.get("state") == "verified_error",
                        "responsible_step": result.get("responsible_step"),
                        "registry_hash": identity["registry_hash"],
                        "runtime_valid": result.get("runtime_valid"),
                        "result": result,
                    }
                    results[case_id] = entry
                    save_json(results_dir / case_file_name(case_id), entry)
            if identity_path.exists() is False:
                save_json(identity_path, identity)
            reps.append(results)
        if stopped:
            break
        # Verdict over ALL repetitions: the best round is never selected.
        rep_verdicts = []
        for results in reps:
            rep_verdicts.append(_repeat_verdict(
                results, case_ids, case_meta, target_steps,
                candidate=arm["candidate"] is not None,
            ))
        if arm["candidate"] is None:
            passed = None  # the H0 arm is the contrast, not a gate
        else:
            passed = bool(rep_verdicts) and all(
                item["passed"] for item in rep_verdicts
            )
        arm_reports[arm["arm"]] = {
            "version_id": arm["version_id"],
            "repetitions": len(reps),
            "complete": len(reps) == repetitions,
            "passed": passed,
            "per_repetition": rep_verdicts,
        }
    report = {
        "schema_version": RESEARCH_REPORT_SCHEMA,
        "stage": stage,
        "arms": arm_reports,
        "case_ids": list(case_ids),
        "repetitions": repetitions,
        "budget": {
            "cap": budget_case_evaluations, "used": used,
        },
        "stopped": stopped,
        "status": (
            "complete" if not stopped and all(
                item["complete"] for item in arm_reports.values()
            ) else "partial"
        ),
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_json(stage_dir / "report.json", report)
    return report


def _repeat_verdict(
    results: Mapping[str, Mapping[str, Any]],
    case_ids: Sequence[str],
    case_meta: Mapping[str, Mapping[str, Any]],
    target_steps: Mapping[str, Any],
    *,
    candidate: bool,
) -> dict[str, Any]:
    """One repetition's gate read, per frozen case role.

    ``training_failure`` must be repaired on target; ``regression`` (already
    detected before the candidate) must stay detected; every other role
    (normal neighbor, boundary) must stay clean.
    """
    training, neighbors, reasons = {}, {}, []
    for case_id in case_ids:
        row = results[case_id]
        role = str(case_meta[case_id].get("role") or "unknown")
        authoritative = row.get("authority") == "authoritative"
        positive = (
            bool(row.get("prediction"))
            and row.get("state") == "verified_error"
            and authoritative
        )
        clean = (
            not bool(row.get("prediction"))
            and row.get("state") == "verified_clean"
            and authoritative
        )
        if role == "training_failure":
            target_step = target_steps.get(case_id)
            target_ok = (
                row.get("responsible_step") is None
                or target_step is None
                or int(row.get("responsible_step") or -1) == int(target_step)
            )
            training[case_id] = {
                "repaired": positive and target_ok,
                "state": row.get("state"),
                "responsible_step": row.get("responsible_step"),
                # Contrast read for the H0 arm (no gate of its own).
                "prediction": bool(row.get("prediction")),
            }
        elif role == "regression":
            neighbors[case_id] = {
                "kept_positive": positive, "state": row.get("state"),
            }
            if not positive:
                reasons.append(
                    f"regression {case_id} lost detection "
                    f"({row.get('state')!r})"
                )
        else:
            neighbors[case_id] = {"clean": clean, "state": row.get("state")}
            if not clean:
                reasons.append(f"{case_id} degraded to {row.get('state')!r}")
    passed = (
        all(item["repaired"] for item in training.values())
        and not reasons
    ) if candidate else False
    return {
        "passed": passed, "training": training,
        "neighbors": neighbors, "reasons": reasons,
    }


def run_research(
    manifest_path: str | Path,
    output_dir: str | Path,
    backend: Any,
    proposer: Any,
) -> dict[str, Any]:
    """Execute one frozen-manifest research run (doc §8.1 contract).

    ``backend`` must expose ``evaluate_fresh(cases, dataset_root=..., registry=...,
    gold_map=..., workers=..., deadline=...)`` (the existing
    ``DetectionBackend`` contract). ``proposer`` must expose ``propose`` and,
    when available, receives ``set_research_packet``/``set_memory_context``/
    ``set_rejection_feedback`` forwarding.
    """
    run = ResearchRun(manifest_path, output_dir, backend, proposer)
    return run.execute()


__all__ = [
    "ALLOWED_HOOK", "ALLOWED_INTERVENTION_TYPE", "RESEARCH_MANIFEST_SCHEMA",
    "RESEARCH_REPORT_SCHEMA", "ResearchBudget", "ResearchRun",
    "run_frozen_repeats", "run_research",
]
