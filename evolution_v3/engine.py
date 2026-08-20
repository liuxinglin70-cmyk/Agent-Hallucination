"""Evolution cycle engine.

One cycle (B stage):

1. defect-injection audit of the gate, once per machinery fingerprint;
2. bare-base patrol on a fresh sample P (never from deployed skills);
3. mechanical stable failure families (worksheet);
4. at most three candidate proposals;
5. V1 structural screening, then one detection arm per admission unit on
   S+G; V2 (sentinel subset), V3 (S+G claim consistency) and M (gain
   subset) all score the same arm, any failure is terminal;
6. one combination arm when several units qualify;
7. pre-commit registry write and append-only ledger.

After all cycles, ``run_governance`` performs the measured-library
accounting that never needs to precede admission: leave-one-out
attribution, outcome-driven retirement and drift rollback.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution_v3.backends import (
    DetectionBackend,
    file_digest,
    fingerprint,
    make_backend,
    source_digest,
)
from evolution_v3.config import ensure_dirs, load_config, resolve_path
from evolution_v3.contracts import empty_registry, make_registry, save_json
from detector_v2.dataset import gold_fields
from evolution_v3.gates import (
    defect_candidates,
    meets_m,
    requires_fresh_evaluation,
    v1_structural,
    v2_harmlessness,
    v3_semantics,
)
from evolution_v3.ledger import Ledger, load_ledger
from evolution_v3.metrics import fired_rate, paired_delta, source_deltas
from evolution_v3.proposer import ProposalBatch, make_proposer
from evolution_v3.registry import ActiveRegistry, load_active_registry
from evolution_v3.splits import (
    EvolutionSplit,
    build_split,
    bool_gold,
    load_gold,
    split_manifest_report,
)
from evolution_v3.worksheet import (
    classify_family_actionability,
    diagnose_failure_families,
    refine_failure_families,
    stable_failure_families,
    worksheet_report,
)


def _summarise_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "case": row.get("case"),
            "gold": row.get("gold"),
            "prediction": row.get("prediction"),
            "state": row.get("state"),
            "authority": row.get("authority"),
        }
        for row in rows
    ]


def _subset_rows(
    rows: Sequence[Mapping[str, Any]],
    cases: Sequence[str],
) -> list[dict[str, Any]]:
    wanted = {str(case) for case in cases}
    return [row for row in rows if str(row.get("case") or "") in wanted]


def split_actionable_families(
    families: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition diagnosed families into actionable ones and capability gaps.

    Gap families stay first-class in the cycle report (a gap is a measured
    outcome about the action space, not a silent empty batch), but they never
    reach the proposer: drafting against them can only produce contracts the
    validators must reject.
    """
    actionable: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for family in families:
        verdict = classify_family_actionability(family)
        item = dict(family)
        item["actionability"] = verdict
        if verdict["classification"] == "actionable":
            actionable.append(item)
        else:
            gaps.append({
                "failure_family": item.get("failure_family"),
                "count": item.get("count"),
                "sources": item.get("sources"),
                "classification": verdict["classification"],
                "reason": verdict["reason"],
            })
    return actionable, gaps


class _ArmRunner:
    """Every gate arm of one cycle, with a shared fail-closed deadline.

    All evaluations within a cycle converge to one wall-clock deadline:
    provider-stage candidate arms keep the ability to run fresh while the
    total runtime stays bounded, and a batch that cannot start in time
    fail-closes to unresolved rows instead of hanging the cycle.
    """

    def __init__(self, engine: "EvolutionEngine", deadline: float | None) -> None:
        self.engine = engine
        self.deadline = deadline

    def run(
        self,
        cases: Sequence[str],
        *,
        registry: Mapping[str, Any] | None,
        fresh: bool = False,
    ) -> list[dict[str, Any]]:
        evaluate = (
            self.engine.backend.evaluate_fresh if fresh
            else self.engine.backend.evaluate
        )
        return evaluate(
            list(cases),
            dataset_root=self.engine.config["dataset_root"],
            registry=registry,
            gold_map=self.engine.gold_map,
            workers=int(
                (self.engine.config.get("backend") or {}).get("workers", 1)
            ),
            deadline=self.deadline,
        )


class EvolutionEngine:
    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        backend: DetectionBackend | None = None,
        split: EvolutionSplit | None = None,
        registry: ActiveRegistry | None = None,
        ledger: Ledger | None = None,
        proposer: Any | None = None,
        mini: bool = False,
        mock_base: Mapping[str, bool | None] | None = None,
        mock_overrides: Mapping[str, Mapping[str, bool | None]] | None = None,
    ) -> None:
        self.config = dict(load_config()) if config is None else dict(config)
        self.paths = ensure_dirs(self.config)
        self.mini = mini
        self.backend = backend or make_backend(
            self.config,
            mock_base=mock_base,
            mock_overrides=mock_overrides,
        )
        self.split = split or build_split(
            config=self.config,
            dataset_root=self.config["dataset_root"],
            mini=mini,
        )
        self.registry = registry or load_active_registry(self.config)
        self.ledger = ledger or load_ledger(self.config)
        self.proposer = proposer or make_proposer(
            self.config, active_entries=self.registry.entries
        )
        self.rejection_feedback: list[dict[str, Any]] = []
        for record in self.ledger.read():
            payload = record.get("payload") or {}
            if (
                record.get("event") == "rejected"
                and isinstance(payload, Mapping)
                and (
                    isinstance(payload.get("candidate"), Mapping)
                    or isinstance(payload.get("candidates"), list)
                )
            ):
                self.rejection_feedback.append(dict(payload))
        self.gold_map = {
            case: load_gold(self.config["dataset_root"], case)
            for case in [*self.split.pool, *self.split.validation]
        }
        self.cycle_reports: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _cycle_seconds(self) -> float | None:
        raw = (self.config.get("budget") or {}).get("cycle_timeout_seconds")
        return float(raw) if raw else None

    def _ledger(self, event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.ledger.append(event, payload)

    # ------------------------------------------------------------------
    def _audit(self, cycle: int, arms: _ArmRunner) -> dict[str, Any]:
        """Run the defect-injection audit once per machinery fingerprint.

        The verdict depends only on code, detector config, the S/G case sets
        with gold, and the active registry. It is cached under a fingerprint
        of exactly those inputs, so repeating cycles with unchanged machinery
        cost zero provider calls while any change re-runs the audit.
        """
        if not bool((self.config.get("audit") or {}).get("enabled", True)):
            return {"enabled": False, "passed": True, "frozen": False, "reports": []}
        fingerprint_payload = {
            "cache_version": "audit-v1",
            "source": source_digest(),
            "detector_config": file_digest(self.config.get("detector_config")),
            "sentinel": sorted(self.split.sentinel),
            "gain": sorted(self.split.gain),
            "gold": {
                case: self.gold_map.get(case)
                for case in [*self.split.sentinel, *self.split.gain]
            },
            "active_registry": [
                str(entry.get("intervention_id") or "")
                for entry in self.registry.entries
            ],
        }
        cache_path = (
            self.paths["reports"] / "defect_audit_verdict.json"
        )
        cached: dict[str, Any] | None = None
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                cached = None
        if (
            cached is not None
            and cached.get("fingerprint") == fingerprint(fingerprint_payload)
        ):
            self._ledger("audit", {
                "cycle": cycle,
                "passed": cached["passed"],
                "reports": cached["reports"],
                "cache": "hit",
            })
            return {
                "enabled": True,
                "passed": cached["passed"],
                "frozen": cached["frozen"],
                "reports": cached["reports"],
                "cache": "hit",
            }

        active = self.registry.registry
        active_fresh = requires_fresh_evaluation(self.registry.entries)
        baseline_s = arms.run(
            self.split.sentinel, registry=active, fresh=active_fresh,
        )
        baseline_g = arms.run(
            self.split.gain, registry=active, fresh=active_fresh,
        )
        reports = []
        passed_any = False
        for candidate in defect_candidates():
            # Defects enter the same V1 -> one S+G arm -> V2/V3/M pipeline
            # as real candidates; a leaked defect would pass all of them.
            v1 = v1_structural(
                candidate, active_entries=self.registry.entries, config=self.config,
            )
            report: dict[str, Any] = {
                "intervention_id": candidate.get("intervention_id"),
                "v1": v1,
                "v2": None,
                "v3": None,
                "m": None,
                "audit_expected_reject": True,
                "audit_rejected": not v1["passed"],
                "audit_passed": False,
            }
            if v1["passed"]:
                unit = self._run_unit(
                    candidate,
                    partners=[],
                    active_entries=self.registry.entries,
                    active_rows=[*baseline_s, *baseline_g],
                    arms=arms,
                )
                report["v2"] = unit["v2"]
                if unit["v2"]["passed"]:
                    report["v3"] = unit["v3"]
                    if unit["v3"]["passed"]:
                        report["m"] = unit["m"]
                        if unit["m"]["passed"]:
                            report["audit_rejected"] = False
                            report["audit_passed"] = True
            if not report["audit_passed"]:
                report["audit_rejected"] = True
            if report["audit_passed"]:
                passed_any = True
            reports.append(report)
        verdict = {
            "enabled": True,
            "passed": not passed_any,
            "frozen": passed_any,
            "reports": reports,
            "cache": "miss",
        }
        save_json(cache_path, {
            "fingerprint": fingerprint(fingerprint_payload),
            "passed": verdict["passed"],
            "frozen": verdict["frozen"],
            "reports": reports,
        })
        self._ledger("audit", {
            "cycle": cycle,
            "passed": verdict["passed"],
            "reports": reports,
            "cache": "miss",
        })
        return verdict

    def _run_unit(
        self,
        candidate: Mapping[str, Any],
        *,
        partners: Sequence[Mapping[str, Any]],
        active_entries: Sequence[Mapping[str, Any]],
        active_rows: Sequence[Mapping[str, Any]],
        fresh_control_rows: Sequence[Mapping[str, Any]] | None = None,
        arms: _ArmRunner,
    ) -> dict[str, Any]:
        """One admission unit: one S+G arm scored by V2, V3 and M.

        The unit's registry is ``active + partners + candidate``; joint units
        pass their whole group so members are never sold separately. A fresh
        arm (any member on an unfreezing hook) pairs with the shared fresh
        control roll produced once per cycle; a deterministic arm pairs with
        the cached active baseline so its delta carries no provider noise.
        """
        intervention_id = str(candidate.get("intervention_id") or "")
        unit_entries = [*partners, dict(candidate)]
        registry = make_registry([*active_entries, *unit_entries])
        fresh = requires_fresh_evaluation([*active_entries, *unit_entries])
        candidate_rows = arms.run(
            [*self.split.sentinel, *self.split.gain],
            registry=registry,
            fresh=fresh,
        )
        control = (
            fresh_control_rows if fresh and fresh_control_rows is not None
            else list(active_rows)
        )
        baseline_s = _subset_rows(control, self.split.sentinel)
        candidate_s = _subset_rows(candidate_rows, self.split.sentinel)
        v2 = v2_harmlessness(
            baseline_rows=baseline_s,
            candidate_rows=candidate_s,
            config=self.config,
        )
        v3 = None
        m = None
        if v2["passed"]:
            v3 = v3_semantics(
                candidate,
                baseline_rows=control,
                candidate_rows=candidate_rows,
                config=self.config,
            )
            if v3["passed"]:
                control_g = _subset_rows(control, self.split.gain)
                candidate_g = _subset_rows(candidate_rows, self.split.gain)
                delta = paired_delta(control_g, candidate_g)
                passed, failures = meets_m(delta, config=self.config)
                m = {
                    "passed": passed,
                    "reason": "; ".join(failures),
                    "delta": delta,
                }
        return {
            "intervention_id": intervention_id,
            "candidate": dict(candidate),
            "partners": [dict(entry) for entry in partners],
            "fresh": fresh,
            "candidate_rows": _summarise_rows(candidate_rows),
            "v2": v2,
            "v3": v3,
            "m": m,
        }

    # ------------------------------------------------------------------
    def preview_cycle(self, cycle: int) -> dict[str, Any]:
        """Patrol + proposal preview without gates, ledger, or registry writes.

        This is the cheap pre-flight for experimenters: it shows whether the
        current cycle would produce any candidate at all, before spending
        gate detection budget.
        """
        patrol_size = int(
            (self.config.get("budget") or {}).get("patrol_cases", 120)
        )
        patrol_cases = self.split.patrol_sample(cycle, patrol_size)
        # Patrol is always the bare base: candidates may only be distilled
        # from failures of the empty registry, never from deployed skills.
        patrol_rows = self._evaluate(empty_registry(), patrol_cases)
        families = stable_failure_families(patrol_rows)
        bootstrap_cases: list[str] = []
        if not families:
            rerun_budget = int(
                (self.config.get("budget") or {}).get(
                    "stable_failure_rerun_cases", 40
                )
            )
            remaining = [
                case for case in self.split.patrol_pool
                if case not in set(patrol_cases)
            ]
            if rerun_budget > 0 and remaining:
                rng = random.Random(
                    int(self.split.seed) + int(cycle) * 104729 + 17
                )
                bootstrap_cases = rng.sample(
                    remaining, min(rerun_budget, len(remaining)),
                )
                patrol_cases = [*patrol_cases, *bootstrap_cases]
                patrol_rows = [
                    *patrol_rows,
                    *self._evaluate(empty_registry(), bootstrap_cases),
                ]
                families = stable_failure_families(patrol_rows)
        diagnosed = diagnose_failure_families(
            families,
            patrol_rows,
            dataset_root=self.config["dataset_root"],
        )
        diagnosed = refine_failure_families(diagnosed)
        diagnosed, capability_gaps = split_actionable_families(diagnosed)
        proposal_batch = self.proposer.propose(
            diagnosed,
            cycle=cycle,
            max_proposals=int(
                (self.config.get("budget") or {}).get("max_proposals", 3)
            ),
        )
        if not diagnosed:
            empty_reason = (
                "no stable bare-base failure family after initial patrol and "
                "bootstrap reruns"
            )
        elif capability_gaps:
            empty_reason = (
                f"{len(capability_gaps)} stable family(ies) classified as "
                "capability gap(s); proposer received only actionable "
                "families"
            )
        elif not proposal_batch.candidates:
            empty_reason = (
                "stable failure families exist but the proposer drafted no "
                "valid candidate"
            )
        else:
            empty_reason = ""
        report = {
            "mode": "preview",
            "cycle": cycle,
            "patrol_size": len(patrol_cases),
            "bootstrap_count": len(bootstrap_cases),
            "families": [
                {
                    "failure_family": item.get("failure_family"),
                    "count": item.get("count"),
                    "diagnostics": item.get("diagnostics"),
                }
                for item in diagnosed
            ],
            "capability_gaps": capability_gaps,
            "candidates": proposal_batch.candidates,
            "joint_groups": proposal_batch.joint_groups,
            "notes": proposal_batch.notes,
            "empty_proposal_reason": empty_reason,
        }
        save_json(
            self.paths["reports"] / f"proposal_preview_cycle{int(cycle):03d}.json",
            report,
        )
        return report

    # ------------------------------------------------------------------
    def run_cycle(
        self,
        cycle: int,
        *,
        proposal_batch: ProposalBatch | None = None,
    ) -> dict[str, Any]:
        import time as _time

        ledger_integrity = self.ledger.verify()
        if not ledger_integrity["valid"]:
            raise RuntimeError(
                f"ledger integrity broken before cycle {cycle}: "
                f"{ledger_integrity['reason']}"
            )
        self._ledger("cycle_started", {"cycle": cycle})
        seconds = self._cycle_seconds()
        arms = _ArmRunner(
            self, (_time.monotonic() + seconds) if seconds else None,
        )

        audit = self._audit(cycle, arms)
        if audit.get("frozen"):
            report = {
                "cycle": cycle,
                "state": "frozen_after_defect_injection_audit",
                "audit": audit,
            }
            self._save_cycle_report(cycle, report)
            self.cycle_reports.append(report)
            return report

        patrol_size = int(
            (self.config.get("budget") or {}).get("patrol_cases", 120)
        )
        patrol_cases = self.split.patrol_sample(cycle, patrol_size)
        # Patrol always runs the bare base; only the gate baselines below use
        # the active registry. Candidates are never distilled from deployed
        # skills (VaG contamination guard).
        patrol_rows = arms.run(patrol_cases, registry=empty_registry())
        families = stable_failure_families(patrol_rows)

        # Source-quality bootstrap: a random patrol sample can legitimately
        # miss the second member of a failure family. The protocol budget has
        # up to 40 stable-failure reruns; use them before declaring no family.
        bootstrap_cases: list[str] = []
        if not families:
            rerun_budget = int(
                (self.config.get("budget") or {}).get(
                    "stable_failure_rerun_cases", 40
                )
            )
            remaining = [
                case for case in self.split.patrol_pool
                if case not in set(patrol_cases)
            ]
            if rerun_budget > 0 and remaining:
                rng = random.Random(
                    int(self.split.seed) + int(cycle) * 104729 + 17
                )
                bootstrap_cases = rng.sample(
                    remaining, min(rerun_budget, len(remaining)),
                )
                patrol_cases = [*patrol_cases, *bootstrap_cases]
                patrol_rows = [
                    *patrol_rows,
                    *arms.run(bootstrap_cases, registry=empty_registry()),
                ]
                families = stable_failure_families(patrol_rows)

        diagnosed_families = diagnose_failure_families(
            families,
            patrol_rows,
            dataset_root=self.config["dataset_root"],
        )
        diagnosed_families = refine_failure_families(diagnosed_families)
        (
            diagnosed_families, capability_gaps,
        ) = split_actionable_families(diagnosed_families)
        worksheet = worksheet_report(patrol_rows, diagnosed_families)
        worksheet["bootstrap"] = {
            "initial_patrol_size": patrol_size,
            "bootstrap_cases": bootstrap_cases,
            "bootstrap_count": len(bootstrap_cases),
            "family_found": bool(diagnosed_families),
        }
        worksheet["capability_gaps"] = capability_gaps
        self._ledger("patrol", {
            "cycle": cycle,
            "cases": patrol_cases,
            "worksheet": worksheet,
        })

        if proposal_batch is None:
            if hasattr(self.proposer, "active_entries"):
                self.proposer.active_entries = self.registry.entries
            if hasattr(self.proposer, "set_rejection_feedback"):
                self.proposer.set_rejection_feedback(self.rejection_feedback)
            proposal_batch = self.proposer.propose(
                diagnosed_families,
                cycle=cycle,
                max_proposals=int(
                    (self.config.get("budget") or {}).get("max_proposals", 3)
                ),
            )
        self._validate_proposal_batch(
            proposal_batch,
            cycle=cycle,
            patrol_cases=patrol_cases,
            diagnosed_families=diagnosed_families,
        )
        proposals = list(proposal_batch.candidates)
        joint_groups = [
            [str(identifier) for identifier in group]
            for group in proposal_batch.joint_groups
        ]
        joint_partners: dict[str, list[str]] = {}
        for group in joint_groups:
            for identifier in group:
                joint_partners[str(identifier)] = [
                    str(other) for other in group if str(other) != identifier
                ]
        self._ledger("proposed", {
            "cycle": cycle,
            "interventions": [
                candidate.get("intervention_id") for candidate in proposals
            ],
            "joint_groups": joint_groups,
            "notes": list(proposal_batch.notes),
        })
        if not diagnosed_families:
            if capability_gaps:
                empty_proposal_reason = (
                    f"{len(capability_gaps)} stable family(ies) classified "
                    "as capability gap(s); no family is treatable by the "
                    "closed action vocabulary"
                )
            else:
                empty_proposal_reason = (
                    "no stable bare-base failure family (>=2 same-mechanism "
                    "cases) after initial patrol and bootstrap reruns"
                )
        elif not proposals:
            empty_proposal_reason = (
                "stable failure families exist but the proposer drafted no "
                "valid candidate"
            )
        else:
            empty_proposal_reason = ""

        # One active reference arm on S+G shared by every unit this cycle.
        active = self.registry.registry
        active_fresh = requires_fresh_evaluation(self.registry.entries)
        baseline_sentinel = arms.run(
            self.split.sentinel, registry=active, fresh=active_fresh,
        )
        baseline_gain = arms.run(
            self.split.gain, registry=active, fresh=active_fresh,
        )
        active_rows = [*baseline_sentinel, *baseline_gain]
        has_provider_stage = requires_fresh_evaluation([
            *self.registry.entries, *proposals,
        ])
        fresh_control_rows = None
        reroll_noise = None
        if proposals and has_provider_stage:
            # Provider-stage hooks cannot replay a frozen baseline, so their
            # matched control is a fresh roll of the active registry. The
            # roll is generated once and reused by every such unit; comparing
            # units against different control rolls would measure provider
            # variance, not the interventions.
            if active_fresh:
                fresh_control_rows = active_rows
            else:
                fresh_control_rows = arms.run(
                    [*self.split.sentinel, *self.split.gain],
                    registry=active,
                    fresh=True,
                )
                reroll_noise = paired_delta(active_rows, fresh_control_rows)

        by_id = {
            str(candidate.get("intervention_id") or ""): dict(candidate)
            for candidate in proposals
        }
        joint_ids = {identifier for group in joint_groups for identifier in group}

        gate_reports: list[dict[str, Any]] = []
        qualified: list[dict[str, Any]] = []
        unit_outcomes: dict[str, Mapping[str, Any]] = {}
        v1_preflight: dict[str, dict[str, Any]] = {}
        for candidate in proposals:
            intervention_id = str(candidate.get("intervention_id") or "")
            v1 = v1_structural(
                candidate,
                active_entries=self.registry.entries,
                config=self.config,
                allowed_failure_cases=patrol_cases,
                diagnosed_families=diagnosed_families,
            )
            v1_preflight[intervention_id] = v1
            report: dict[str, Any] = {
                "intervention_id": intervention_id,
                "candidate": candidate,
                "v1": v1,
                "v2": None,
                "v3": None,
                "m": None,
                "partner_ids": joint_partners.get(intervention_id, []),
            }
            gate_reports.append(report)
            if not v1["passed"]:
                continue
            partners: list[dict[str, Any]] = []
            if intervention_id in joint_ids:
                partner_ids = joint_partners.get(intervention_id, [])
                if not all(
                    (v1_preflight.get(partner_id) or {}).get("passed")
                    for partner_id in partner_ids
                ):
                    continue
                partners = [
                    by_id[partner_id]
                    for partner_id in partner_ids
                    if partner_id in by_id
                ]
            unit = self._run_unit(
                candidate,
                partners=partners,
                active_entries=self.registry.entries,
                active_rows=active_rows,
                fresh_control_rows=fresh_control_rows,
                arms=arms,
            )
            report.update({
                "v2": unit["v2"],
                "v3": unit["v3"],
                "m": unit["m"],
                "fresh": unit["fresh"],
                "candidate_rows": unit["candidate_rows"],
            })
            # A joint unit's outcome belongs to every member; the member the
            # loop reaches first never runs a solo arm.
            unit_outcomes[intervention_id] = unit
            for partner in unit["partners"]:
                unit_outcomes.setdefault(
                    str(partner.get("intervention_id") or ""), unit,
                )
            if unit["v2"]["passed"] and unit["v3"]["passed"] and unit["m"]["passed"]:
                qualified.append(unit)

        # One combination arm when several units qualify on their own; this
        # is the only additional sweep the protocol needs. A unit whose
        # members form a joint group is evaluated jointly above and never
        # enters the combination.
        combination = self._check_combination(
            qualified,
            active_entries=self.registry.entries,
            fresh_control_rows=fresh_control_rows,
            active_rows=active_rows,
            arms=arms,
        )
        failed_combination = list(combination.get("failed_entries") or ())
        if len(failed_combination) >= 2:
            payload = {
                "cycle": cycle,
                "intervention_id": "combination",
                "candidates": [dict(entry) for entry in failed_combination],
                "type": "combination",
                "hook": "multiple",
                "gate": "COMBINATION_M",
                "reason": combination["reason"],
                "delta": _feedback_delta(combination.get("delta")),
            }
            self._ledger("rejected", payload)
            self.rejection_feedback.append(payload)

        for report in gate_reports:
            intervention_id = str(report.get("intervention_id") or "")
            for gate in ("v1", "v2", "v3", "m"):
                verdict = report.get(gate)
                if verdict is None:
                    continue
                if gate == "m" and combination.get("unit_ids") == [intervention_id]:
                    continue
                if not verdict["passed"]:
                    candidate = dict(report.get("candidate") or {})
                    payload = {
                        "cycle": cycle,
                        "intervention_id": intervention_id,
                        "candidate": candidate,
                        "type": candidate.get("type"),
                        "hook": candidate.get("hook"),
                        "predicate": (candidate.get("body") or {}).get("predicate"),
                        "action": (candidate.get("body") or {}).get("action"),
                        "facts": list(
                            (candidate.get("trigger") or {}).get("facts") or ()
                        ),
                        "gate": gate.upper(),
                        "reason": verdict["reason"],
                        "delta": _feedback_delta(verdict.get("delta")),
                    }
                    self._ledger("rejected", payload)
                    self.rejection_feedback.append(payload)

        selected = list(combination["entries"])
        library_problems = self.registry.check_limits(
            [*self.registry.entries, *selected],
            library_cfg=self.config.get("library") or {},
        )
        if library_problems:
            for candidate in selected:
                payload = {
                    "cycle": cycle,
                    "intervention_id": str(candidate.get("intervention_id") or ""),
                    "candidate": dict(candidate),
                    "type": candidate.get("type"),
                    "hook": candidate.get("hook"),
                    "gate": "LIBRARY",
                    "reason": "; ".join(library_problems),
                    "delta": None,
                }
                self._ledger("rejected", payload)
                self.rejection_feedback.append(payload)
            selected = []
        if selected:
            previous_ids = [
                str(c.get("intervention_id") or "")
                for c in self.registry.entries
            ]
            self.registry.write(
                [*self.registry.entries, *selected],
                cycle=cycle,
                reason="V1-V2-V3-M passed; pre-commit write",
                library_cfg=self.config.get("library") or {},
            )
            for candidate in selected:
                intervention_id = str(candidate.get("intervention_id") or "")
                report = next(
                    (
                        item for item in gate_reports
                        if item.get("intervention_id") == intervention_id
                    ),
                    {},
                )
                unit = unit_outcomes.get(intervention_id) or {}
                marginal = int(
                    ((unit.get("m") or {}).get("delta") or {}).get(
                        "marginal_cases"
                    ) or 0
                )
                fired = float((unit.get("v3") or {}).get("fired_rate", 0.0))
                partner_ids = list(report.get("partner_ids") or [])
                self.registry.record_marginal(
                    intervention_id,
                    marginal,
                    cycle=cycle,
                    fired_rate=fired,
                )
                self._ledger("admitted", {
                    "cycle": cycle,
                    "intervention_id": intervention_id,
                    "hook": candidate.get("hook"),
                    "marginal_cases": marginal,
                    "fired_rate": fired,
                    "joint_with": partner_ids,
                })
            self._ledger("registry_written", {
                "cycle": cycle,
                "previous": previous_ids,
                "active": [
                    str(c.get("intervention_id") or "")
                    for c in self.registry.entries
                ],
                "reason": "pre-commit",
            })

        report = {
            "cycle": cycle,
            "state": "completed",
            "split": split_manifest_report(self.split),
            "audit": audit,
            "patrol": worksheet,
            "proposal_summary": {
                "stable_families": [
                    str(item.get("failure_family") or "")
                    for item in diagnosed_families
                ],
                "capability_gaps": capability_gaps,
                "candidate_count": len(proposals),
                "joint_groups": joint_groups,
                "empty_proposal_reason": empty_proposal_reason,
            },
            "proposals": [
                {
                    "intervention_id": str(c.get("intervention_id") or ""),
                    "type": c.get("type"),
                    "hook": c.get("hook"),
                    "failure_family": (c.get("provenance") or {}).get(
                        "failure_family"
                    ),
                    "target_polarity": (c.get("claim") or {}).get(
                        "target_polarity"
                    ),
                    "frozen_judge_replay_compatible": next(
                        (
                            note.get("frozen_judge_replay_compatible")
                            for note in proposal_batch.notes
                            if note.get("intervention_id")
                            == c.get("intervention_id")
                        ),
                        None,
                    ),
                }
                for c in proposals
            ],
            "gates": gate_reports,
            "fresh_control": (
                None if reroll_noise is None
                else {
                    "reused_across_units": True,
                    "reroll_vs_cached_baseline": {
                        "marginal_cases": int(reroll_noise["marginal_cases"]),
                        "new_errors": int(reroll_noise["new_errors"]),
                        "unresolved_added": int(reroll_noise["unresolved_added"]),
                    },
                }
            ),
            "combination": {
                "unit_ids": combination["unit_ids"],
                "delta": combination["delta"],
                "reason": combination["reason"],
            },
            "marginal_selection": {
                "selected": [
                    str(c.get("intervention_id") or "") for c in selected
                ],
                "library_rejections": library_problems,
            },
            "active_after_cycle": [
                str(c.get("intervention_id") or "")
                for c in self.registry.entries
            ],
            "active_effect_on_g": paired_delta(
                (
                    _subset_rows(fresh_control_rows, self.split.gain)
                    if selected
                    and fresh_control_rows is not None
                    and requires_fresh_evaluation(self.registry.entries)
                    else baseline_gain
                ),
                self._evaluate(self.registry.registry, self.split.gain),
            ),
            "ledger_integrity": self.ledger.verify(),
        }
        self._save_cycle_report(cycle, report)
        self.cycle_reports.append(report)
        return report

    def _check_combination(
        self,
        qualified: Sequence[Mapping[str, Any]],
        *,
        active_entries: Sequence[Mapping[str, Any]],
        fresh_control_rows: Sequence[Mapping[str, Any]] | None,
        active_rows: Sequence[Mapping[str, Any]],
        arms: _ArmRunner,
    ) -> dict[str, Any]:
        """Admit every qualified unit; when more than one qualifies, verify
        the combination keeps meeting M on a single extra S+G arm.

        Rejection of the combination is terminal for the late units: the
        units that already earned admission keep it, the rest are rejected
        so a failing interaction cannot silently enter the registry.
        """
        if not qualified:
            return {
                "unit_ids": [],
                "entries": [],
                "delta": None,
                "reason": "no qualified unit",
            }
        if len(qualified) == 1:
            unit = qualified[0]
            return {
                "unit_ids": [unit["intervention_id"]],
                "entries": [*unit["partners"], unit["candidate"]],
                "delta": None,
                "reason": "single qualified unit; no combination arm needed",
            }
        # Drop units whose entries are already inside another unit's joint
        # group (a joint unit subsumes its members).
        seen_ids: set[str] = set()
        units: list[Mapping[str, Any]] = []
        for unit in qualified:
            entry_ids = {
                str(entry.get("intervention_id") or "")
                for entry in [*unit["partners"], unit["candidate"]]
            }
            if entry_ids & seen_ids:
                continue
            seen_ids.update(entry_ids)
            units.append(unit)
        if len(units) == 1:
            unit = units[0]
            return {
                "unit_ids": [unit["intervention_id"]],
                "entries": [*unit["partners"], unit["candidate"]],
                "delta": None,
                "reason": "qualified units collapse into one joint group",
            }
        max_admits = int(
            (self.config.get("budget") or {}).get("max_admits", 2)
        )
        units = units[:max(1, max_admits)]
        entries: list[dict[str, Any]] = []
        for unit in units:
            entries.extend([*unit["partners"], unit["candidate"]])
        fresh = requires_fresh_evaluation([*active_entries, *entries])
        control = (
            fresh_control_rows if fresh and fresh_control_rows is not None
            else active_rows
        )
        combo_rows = arms.run(
            [*self.split.sentinel, *self.split.gain],
            registry=make_registry([*active_entries, *entries]),
            fresh=fresh,
        )
        control_g = _subset_rows(control, self.split.gain)
        combo_g = _subset_rows(combo_rows, self.split.gain)
        delta = paired_delta(control_g, combo_g)
        passed, failures = meets_m(delta, config=self.config)
        unit_ids = [unit["intervention_id"] for unit in units]
        if passed:
            return {
                "unit_ids": unit_ids,
                "entries": entries,
                "delta": delta,
                "reason": "combination arm met M",
                "dropped_entries": [],
                "failed_entries": [],
            }
        # The earliest units keep their earned admission; the tail units are
        # rejected because their interaction with the earlier ones breaks M.
        survivors = units[:1]
        kept_ids = {survivors[0]["intervention_id"]}
        dropped = [unit_id for unit_id in unit_ids if unit_id not in kept_ids]
        dropped_entries = [
            dict(entry)
            for unit in units[1:]
            for entry in [*unit["partners"], unit["candidate"]]
        ]
        survivor_entries = []
        for unit in survivors:
            survivor_entries.extend(
                [*unit["partners"], unit["candidate"]]
            )
        return {
            "unit_ids": [survivors[0]["intervention_id"]],
            "entries": survivor_entries,
            "delta": delta,
            "reason": (
                "combination arm failed M ("
                + "; ".join(failures)
                + f"); rejected later unit(s) {dropped}"
            ),
            "dropped_entries": dropped_entries,
            "failed_entries": [dict(entry) for entry in entries],
        }

    def _evaluate(
        self,
        registry: Mapping[str, Any] | None,
        cases: Sequence[str],
    ) -> list[dict[str, Any]]:
        workers = int((self.config.get("backend") or {}).get("workers", 1))
        entries = list((registry or {}).get("interventions") or ())
        evaluate = (
            self.backend.evaluate_fresh
            if requires_fresh_evaluation(entries)
            else self.backend.evaluate
        )
        return evaluate(
            list(cases),
            dataset_root=self.config["dataset_root"],
            registry=registry,
            gold_map=self.gold_map,
            workers=workers,
        )

    def _validate_proposal_batch(
        self,
        batch: ProposalBatch,
        *,
        cycle: int,
        patrol_cases: Sequence[str],
        diagnosed_families: Sequence[Mapping[str, Any]],
    ) -> None:
        candidates = list(batch.candidates)
        ids = [str(item.get("intervention_id") or "") for item in candidates]
        if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
            raise ValueError("proposal batch must contain unique non-empty ids")
        family_cases = {
            str(item.get("failure_family") or ""): {
                str(case) for case in item.get("failure_cases") or ()
            }
            for item in diagnosed_families
        }
        patrol = {str(case) for case in patrol_cases}
        for candidate in candidates:
            provenance = candidate.get("provenance") or {}
            if int(provenance.get("cycle", -1)) != int(cycle):
                raise ValueError(
                    f"proposal {candidate.get('intervention_id')} has stale cycle provenance"
                )
            family = str(provenance.get("failure_family") or "")
            cases = {str(case) for case in provenance.get("failure_cases") or ()}
            if family not in family_cases or len(cases) < 2 or not cases <= patrol:
                raise ValueError(
                    f"proposal {candidate.get('intervention_id')} has invalid patrol provenance"
                )
            if not cases <= family_cases[family]:
                raise ValueError(
                    f"proposal {candidate.get('intervention_id')} is outside its diagnosed family"
                )
        candidate_ids = set(ids)
        grouped: set[str] = set()
        for group in batch.joint_groups:
            group_ids = [str(item) for item in group]
            if len(group_ids) < 2 or len(group_ids) != len(set(group_ids)):
                raise ValueError("joint groups need at least two unique candidates")
            if not set(group_ids) <= candidate_ids or grouped.intersection(group_ids):
                raise ValueError("joint groups must reference unique batch candidates")
            grouped.update(group_ids)

    # ------------------------------------------------------------------
    def run_experiment(self, cycles: int | None = None) -> dict[str, Any]:
        import time as _time

        cycles = int(self.config.get("cycles", 3) if cycles is None else cycles)
        if cycles < 0:
            raise ValueError("cycles must be non-negative")
        for cycle in range(1, cycles + 1):
            self.run_cycle(cycle)
        report = {
            "cycles": self.cycle_reports,
            "active": [
                str(c.get("intervention_id") or "") for c in self.registry.entries
            ],
            "ledger_integrity": self.ledger.verify(),
            "registry_state": self.registry.state,
        }
        if self.mini and self.split.validation:
            report["mini_effect"] = self._evaluate_mini_effect()
        return report

    def _evaluate_mini_effect(self) -> dict[str, Any]:
        """Evaluate the committed registry on cases excluded from B gates.

        The empty arm is the single cached roll of the bare base; the active
        arm replays that same roll through the frozen envelope, so the
        comparison measures the interventions alone. This arm can never
        generate proposals (VaG separation from the gain-set signal).
        """
        seconds = self._cycle_seconds()
        deadline = (_time_monotonic() + seconds) if seconds else None
        workers = int((self.config.get("backend") or {}).get("workers", 1))
        empty_rows = self.backend.evaluate(
            self.split.validation,
            dataset_root=self.config["dataset_root"],
            registry=empty_registry(),
            gold_map=self.gold_map,
            workers=workers,
            deadline=deadline,
        )
        active_fresh = requires_fresh_evaluation(self.registry.entries)
        active_rows = self._evaluate_arm(
            self.split.validation, self.registry.registry, active_fresh, deadline,
        )
        if not self.registry.entries:
            # Empty-vs-empty is the same rows by construction; a delta here
            # is machinery inconsistency, not a measured effect.
            effect = paired_delta(empty_rows, [
                dict(row) for row in empty_rows
            ])
            if any(int(effect[key]) != 0 for key in (
                "marginal_cases", "new_errors", "unresolved_added",
            )):
                raise RuntimeError(
                    "empty-registry mini effect is not zero: paired replay "
                    "machinery is inconsistent"
                )
        else:
            effect = paired_delta(empty_rows, active_rows)
        cfg = self.config.get("mini_effect") or {}
        passed = (
            bool(self.registry.entries)
            and int(effect["marginal_cases"]) >= int(
                cfg.get("min_marginal_cases", 1)
            )
            and int(effect["new_errors"]) <= int(
                cfg.get("max_new_errors", 0)
            )
            and int(effect["unresolved_added"]) <= int(
                cfg.get("max_unresolved_added", 0)
            )
        )
        return {
            "cases": len(self.split.validation),
            "paired_replay": not active_fresh,
            "effect": {
                "marginal_cases": int(effect["marginal_cases"]),
                "new_errors": int(effect["new_errors"]),
                "unresolved_added": int(effect["unresolved_added"]),
                "correctness_improved": int(effect["correctness_improved"]),
                "correctness_worsened": int(effect["correctness_worsened"]),
                "baseline_metrics": effect["baseline_metrics"],
                "active_metrics": effect["active_metrics"],
                "flips": effect["flips"],
            },
            "active_interventions": [
                str(item.get("intervention_id") or "")
                for item in self.registry.entries
            ],
            "passed": passed,
        }

    def _evaluate_arm(
        self,
        cases: Sequence[str],
        registry: Mapping[str, Any] | None,
        fresh: bool,
        deadline: float | None,
    ) -> list[dict[str, Any]]:
        workers = int((self.config.get("backend") or {}).get("workers", 1))
        evaluate = (
            self.backend.evaluate_fresh if fresh else self.backend.evaluate
        )
        return evaluate(
            list(cases),
            dataset_root=self.config["dataset_root"],
            registry=registry,
            gold_map=self.gold_map,
            workers=workers,
            deadline=deadline,
        )

    # ------------------------------------------------------------------
    def run_governance(self, cycle: int | None = None) -> dict[str, Any]:
        """Post-B library governance: attribution, retirement, rollback.

        These accounting measurements never gate admission, so they run once
        after the B stage instead of sweeping the gain set inside every
        cycle: leave-one-out marginal attribution for the committed
        registry, outcome-driven retirement, and the empty-vs-active net
        contribution rollback check.
        """
        import time as _time

        cycle = int(
            cycle if cycle is not None else self.config.get("cycles", 3)
        )
        seconds = self._cycle_seconds()
        deadline = (_time.monotonic() + seconds) if seconds else None
        arms = _ArmRunner(self, deadline)

        attribution: list[dict[str, Any]] = []
        if self.registry.entries:
            fresh = requires_fresh_evaluation(self.registry.entries)
            active_rows = arms.run(
                self.split.gain, registry=self.registry.registry, fresh=fresh,
            )
            for entry in self.registry.entries:
                intervention_id = str(entry.get("intervention_id") or "")
                without = [
                    item for item in self.registry.entries
                    if str(item.get("intervention_id") or "") != intervention_id
                ]
                without_rows = arms.run(
                    self.split.gain, registry=make_registry(without), fresh=fresh,
                )
                delta = paired_delta(without_rows, active_rows)
                attribution.append({
                    "intervention_id": intervention_id,
                    "delta": delta,
                    "attribution": "leave_one_out_committed_configuration",
                })
        for item in attribution:
            self.registry.record_marginal(
                item["intervention_id"],
                int(item["delta"]["marginal_cases"]),
                cycle=cycle,
                fired_rate=fired_rate(
                    self._evaluate(
                        self.registry.registry, self.split.gain,
                    ),
                    item["intervention_id"],
                ),
            )

        retirement = self._apply_retirement(cycle)
        rollback = self._apply_rollback_check(cycle, arms)
        report = {
            "cycle": cycle,
            "attribution": attribution,
            "retirement": retirement,
            "rollback": rollback,
            "active_after_governance": [
                str(c.get("intervention_id") or "")
                for c in self.registry.entries
            ],
            "ledger_integrity": self.ledger.verify(),
        }
        save_json(
            self.paths["reports"] / f"governance_cycle{int(cycle):03d}.json",
            report,
        )
        self._ledger("governance", {
            "cycle": cycle,
            "retired": retirement.get("retired") or [],
            "rollback_triggered": bool(rollback.get("triggered")),
        })
        return report

    def _apply_retirement(self, cycle: int) -> dict[str, Any]:
        threshold = int(
            (self.config.get("library") or {}).get(
                "retire_after_cycles_marginal_le0", 2
            )
        )
        retire_ids: list[str] = []
        for entry in self.registry.entries:
            intervention_id = str(entry.get("intervention_id") or "")
            info = self.registry.state.get("interventions", {}).get(
                intervention_id, {}
            )
            history = info.get("marginal_history") or []
            if len(history) >= threshold and all(
                int(item.get("marginal_cases", 0)) <= 0
                for item in history[-threshold:]
            ):
                retire_ids.append(intervention_id)
        if not retire_ids:
            return {"retired": [], "threshold": threshold}
        retired = self.registry.retire(
            retire_ids,
            cycle=cycle,
            library_cfg=self.config.get("library") or {},
        )
        for intervention_id in retired:
            self._ledger("retired", {
                "cycle": cycle,
                "intervention_id": intervention_id,
                "reason": (
                    f"marginal <= 0 for the last {threshold} cycles "
                    "(outcome-driven retirement)"
                ),
            })
        return {"retired": retired, "threshold": threshold}

    def _apply_rollback_check(
        self, cycle: int, arms: _ArmRunner,
    ) -> dict[str, Any]:
        threshold = int(
            (self.config.get("library") or {}).get(
                "rollback_after_cycles_net_negative", 2
            )
        )
        fresh = requires_fresh_evaluation(self.registry.entries)
        empty_rows = arms.run(
            self.split.gain, registry=empty_registry(), fresh=fresh,
        )
        active_rows = arms.run(
            self.split.gain, registry=self.registry.registry, fresh=fresh,
        )
        net = paired_delta(empty_rows, active_rows)
        self.registry.record_net_contribution(
            int(net["marginal_cases"]), cycle=cycle,
        )
        history = list(self.registry.state.get("net_contribution_history") or [])
        if len(history) >= threshold and all(
            int(item.get("net_correct_cases", 0)) < 0
            for item in history[-threshold:]
        ):
            payload = self.registry.rollback_to_snapshot(cycle - threshold)
            self._ledger("rolled_back", {
                "cycle": cycle,
                "reason": (
                    f"empty-vs-active net contribution negative for the last "
                    f"{threshold} cycles"
                ),
                "restored_snapshot": (
                    str(payload.get("content_hash") or "") if payload else ""
                ),
            })
            return {
                "triggered": True,
                "threshold": threshold,
                "history": history[-threshold:],
            }
        return {
            "triggered": False,
            "threshold": threshold,
            "history": history[-threshold:],
        }

    # ------------------------------------------------------------------
    def run_final(self) -> dict[str, Any]:
        if self.split.final_holdout is None:
            raise RuntimeError("mini split has no read-once final verification set")
        if not self.registry.entries:
            raise RuntimeError(
                "final verification requires a non-empty active registry"
            )
        final_cases = self.split.final_holdout.read(
            "final_verification", commit=False,
        )
        case_ids = [str(item.get("case_id") or "") for item in final_cases]
        final_gold = {
            str(item.get("case_id") or ""): bool_gold(item.get("gold"))
            for item in final_cases
        }
        if any(value is None for value in final_gold.values()):
            raise ValueError("final verification contains an unknown gold label")
        empty = empty_registry()
        active = self.registry.registry
        workers = int((self.config.get("backend") or {}).get("workers", 1))
        # The empty arm is the single cached roll; the active arm replays it
        # through the frozen envelope so the final effect measures the
        # committed interventions alone. Provider-stage interventions cannot
        # be replayed, so they compare against a fresh control roll (their
        # only measurable contrast, same rule as the cycle gates).
        active_fresh = requires_fresh_evaluation(self.registry.entries)
        empty_rows = self.backend.evaluate(
            case_ids,
            dataset_root=self.config["dataset_root"],
            registry=empty,
            gold_map=final_gold,
            workers=workers,
        )
        if active_fresh:
            active_rows = self.backend.evaluate_fresh(
                case_ids,
                dataset_root=self.config["dataset_root"],
                registry=active,
                gold_map=final_gold,
                workers=workers,
            )
        else:
            active_rows = self.backend.evaluate(
                case_ids,
                dataset_root=self.config["dataset_root"],
                registry=active,
                gold_map=final_gold,
                workers=workers,
            )
        effect = paired_delta(empty_rows, active_rows)
        main_pass = (
            int(effect["marginal_cases"]) >= int(
                (self.config.get("gates") or {}).get("m_min_marginal_cases", 1)
            )
            and int(effect["unresolved_added"]) <= 0
        )
        p_value = _mcnemar_improvement_p(effect)
        step_check = _step_localization_check(
            self.config["dataset_root"], empty_rows, active_rows,
        )
        main_pass = main_pass and bool(step_check["not_regressed"])
        report = {
            "schema_version": "evolution_v3.final_verification.v1",
            "registry_hash": str(active.get("content_hash") or ""),
            "final_cases": len(case_ids),
            "effect": {
                "marginal_cases": int(effect["marginal_cases"]),
                "new_fn": int(effect["new_fn"]),
                "new_fp": int(effect["new_fp"]),
                "tp_lost": int(effect["tp_lost"]),
                "unresolved_added": int(effect["unresolved_added"]),
                "flips": effect["flips"],
                "baseline_metrics": effect["baseline_metrics"],
                "active_metrics": effect["active_metrics"],
            },
            "cross_source": source_deltas(empty_rows, active_rows),
            "verdict": {
                "active_registry_nonempty": bool(self.registry.entries),
                "main_criterion": "pass" if main_pass else "fail",
                "mcnemar_one_sided_p": p_value,
                "step_localization_not_regressed": step_check,
                "unresolved_not_increased": int(effect["unresolved_added"]) <= 0,
                "overall": (
                    "pass"
                    if main_pass and p_value < 0.05
                    and int(effect["unresolved_added"]) <= 0
                    and bool(step_check["not_regressed"])
                    and bool(self.registry.entries)
                    else "fail"
                ),
                "statistical_power_note": (
                    "with n=300 and a symmetric paired test, one-sided "
                    "p<0.05 requires at least 5 net improving flips and zero "
                    "worsening flips; a small true effect can be real yet "
                    "underpowered, which is a property of the pre-registered "
                    "criterion, not of the gate machinery"
                ),
            },
            "active_interventions": [
                str(item.get("intervention_id") or "") for item in self.registry.entries
            ],
        }
        save_json(self.paths["final"] / "final_report.json", report)
        self.split.final_holdout.commit("final_verification")
        self._ledger("final_verification", {
            "cycle": int(self.config.get("cycles", 3)),
            "verdict": report["verdict"],
            "effect": report["effect"],
        })
        return report

    def _save_cycle_report(self, cycle: int, report: Mapping[str, Any]) -> None:
        path = self.paths["cycles"] / f"cycle{int(cycle):03d}.json"
        save_json(path, dict(report))


def _time_monotonic() -> float:
    import time

    return time.monotonic()


def _feedback_delta(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    keys = (
        "marginal_cases", "new_fn", "new_fp", "new_errors",
        "unresolved_added", "health_regressed", "correctness_improved",
        "correctness_worsened",
    )
    return {key: int(value.get(key) or 0) for key in keys}


def _mcnemar_improvement_p(delta: Mapping[str, Any]) -> float:
    """One-sided exact binomial p for symmetric discordance (McNemar)."""
    improved = int(delta.get("correctness_improved") or 0)
    worsened = int(delta.get("correctness_worsened") or 0)
    n = improved + worsened
    if n == 0:
        return 1.0
    if improved <= worsened:
        return 1.0
    tail = 0.0
    for k in range(improved, n + 1):
        tail += math.comb(n, k) * (0.5 ** n)
    return min(1.0, tail)


def _step_localization_check(
    dataset_root: str,
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_map = {str(r.get("case") or ""): r for r in baseline_rows}
    hit_base = hit_active = n = errors = 0
    for row in active_rows:
        if not row.get("gold"):
            continue
        try:
            case = str(row.get("case") or "")
            raw = json.loads(
                (Path(dataset_root) / case).read_text(encoding="utf-8-sig")
            )
            gold_step = gold_fields(raw).get("hallucination_step")
            if not str(gold_step or "").isdigit():
                continue
            gold_step = int(gold_step)
            n += 1
            if (
                row.get("prediction") is True
                and (row.get("result") or {}).get("responsible_step") == gold_step
            ):
                hit_active += 1
            base = base_map.get(case)
            if (
                base
                and base.get("prediction") is True
                and (base.get("result") or {}).get("responsible_step") == gold_step
            ):
                hit_base += 1
        except Exception:
            errors += 1
    return {
        "n": n,
        "errors": errors,
        "baseline_hits": hit_base,
        "active_hits": hit_active,
        "not_regressed": bool(n > 0 and errors == 0 and hit_active >= hit_base),
    }


def run_experiment(
    *,
    cycles: int | None = None,
    config: Mapping[str, Any] | None = None,
    backend: DetectionBackend | None = None,
    mini: bool = False,
) -> dict[str, Any]:
    return EvolutionEngine(config, backend=backend, mini=mini).run_experiment(cycles)


__all__ = ["EvolutionEngine", "run_experiment"]
