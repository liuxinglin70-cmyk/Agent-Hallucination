"""Evolution cycle engine.

One cycle:

1. defect-injection audit of the gate (freeze on any leak-through);
2. bare-base patrol on a fresh sample P (never from deployed skills);
3. mechanical stable failure families (worksheet);
4. at most three candidate proposals;
5. V1 -> V2 -> V3 -> M, any failure is terminal for that candidate;
6. pre-commit registry write, outcome-driven retirement, drift rollback;
7. append-only ledger and meta report.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution_v3.backends import DetectionBackend, make_backend
from evolution_v3.config import ensure_dirs, load_config, resolve_path
from evolution_v3.contracts import empty_registry, make_registry, save_json
from evolution_v3.gates import (
    audit_defects,
    marginal_selection,
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
    diagnose_failure_families,
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
        self.gold_map = {
            case: load_gold(self.config["dataset_root"], case)
            for case in self.split.pool
        }
        self.cycle_reports: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _evaluate(
        self,
        registry: Mapping[str, Any] | None,
        cases: Sequence[str],
    ) -> list[dict[str, Any]]:
        workers = int((self.config.get("backend") or {}).get("workers", 1))
        return self.backend.evaluate(
            list(cases),
            dataset_root=self.config["dataset_root"],
            registry=registry,
            gold_map=self.gold_map,
            workers=workers,
        )

    def _ledger(self, event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.ledger.append(event, payload)

    def _audit(self, cycle: int) -> dict[str, Any]:
        if not bool((self.config.get("audit") or {}).get("enabled", True)):
            return {"enabled": False, "passed": True, "frozen": False, "reports": []}
        active = self.registry.registry
        baseline_s = self._evaluate(active, self.split.sentinel)
        baseline_g = self._evaluate(active, self.split.gain)
        report = audit_defects(
            cycle=cycle,
            config=self.config,
            backend=self.backend,
            dataset_root=self.config["dataset_root"],
            active_entries=self.registry.entries,
            baseline_sentinel_rows=baseline_s,
            sentinel_cases=self.split.sentinel,
            gain_cases=self.split.gain,
            baseline_gain_rows=baseline_g,
            gold_map=self.gold_map,
            workers=int((self.config.get("backend") or {}).get("workers", 1)),
        )
        self._ledger("audit", {
            "cycle": cycle,
            "passed": report["passed"],
            "reports": report["reports"],
        })
        return report

    # ------------------------------------------------------------------
    def preview_cycle(self, cycle: int) -> dict[str, Any]:
        """Patrol + proposal preview without gates, ledger, or registry writes.

        This is the cheap pre-flight for experimenters: it shows whether the
        current cycle would produce any candidate at all, before spending
        V2/M detection budget.
        """
        patrol_size = int(
            (self.config.get("budget") or {}).get("patrol_cases", 120)
        )
        patrol_cases = self.split.patrol_sample(cycle, patrol_size)
        active = self.registry.registry
        patrol_rows = self._evaluate(active, patrol_cases)
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
                    *self._evaluate(active, bootstrap_cases),
                ]
                families = stable_failure_families(patrol_rows)
        diagnosed = diagnose_failure_families(
            families,
            patrol_rows,
            dataset_root=self.config["dataset_root"],
        )
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
        ledger_integrity = self.ledger.verify()
        if not ledger_integrity["valid"]:
            raise RuntimeError(
                f"ledger integrity broken before cycle {cycle}: "
                f"{ledger_integrity['reason']}"
            )
        self._ledger("cycle_started", {"cycle": cycle})

        audit = self._audit(cycle)
        if audit.get("frozen"):
            report = {
                "cycle": cycle,
                "state": "frozen_after_defect_injection_audit",
                "audit": audit,
            }
            self._save_cycle_report(cycle, report)
            self.cycle_reports.append(report)
            return report

        # Re-evaluate deployed entries before proposing replacements. This
        # creates capacity from measured retirement, not from wider limits.
        retirement = self._apply_retirement(cycle)

        patrol_size = int(
            (self.config.get("budget") or {}).get("patrol_cases", 120)
        )
        patrol_cases = self.split.patrol_sample(cycle, patrol_size)
        active = self.registry.registry
        patrol_rows = self._evaluate(active, patrol_cases)
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
                    *self._evaluate(active, bootstrap_cases),
                ]
                families = stable_failure_families(patrol_rows)

        diagnosed_families = diagnose_failure_families(
            families,
            patrol_rows,
            dataset_root=self.config["dataset_root"],
        )
        worksheet = worksheet_report(patrol_rows, diagnosed_families)
        worksheet["bootstrap"] = {
            "initial_patrol_size": patrol_size,
            "bootstrap_cases": bootstrap_cases,
            "bootstrap_count": len(bootstrap_cases),
            "family_found": bool(diagnosed_families),
        }
        self._ledger("patrol", {
            "cycle": cycle,
            "cases": patrol_cases,
            "worksheet": worksheet,
        })

        if proposal_batch is None:
            if hasattr(self.proposer, "active_entries"):
                self.proposer.active_entries = self.registry.entries
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
                    str(other) for other in group if str(other) != str(identifier)
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
            empty_proposal_reason = (
                "no stable bare-base failure family (>=2 same-mechanism cases) "
                "after initial patrol and bootstrap reruns"
            )
        elif not proposals:
            empty_proposal_reason = (
                "stable failure families exist but the proposer drafted no "
                "valid candidate"
            )
        else:
            empty_proposal_reason = ""

        # Shared baseline arms used by every gate this cycle.
        baseline_sentinel = self._evaluate(active, self.split.sentinel)
        baseline_gain = self._evaluate(active, self.split.gain)
        active_gain = baseline_gain

        gate_reports: list[dict[str, Any]] = []
        v3_candidates: list[dict[str, Any]] = []
        preflight: dict[str, dict[str, Any]] = {}
        for candidate in proposals:
            intervention_id = str(candidate.get("intervention_id") or "")
            v1 = v1_structural(
                candidate,
                active_entries=self.registry.entries,
                config=self.config,
                allowed_failure_cases=patrol_cases,
                diagnosed_families=diagnosed_families,
            )
            v2 = None
            if v1["passed"]:
                v2 = v2_harmlessness(
                    candidate,
                    active_entries=self.registry.entries,
                    baseline_rows=baseline_sentinel,
                    backend=self.backend,
                    dataset_root=self.config["dataset_root"],
                    sentinel_cases=self.split.sentinel,
                    config=self.config,
                    gold_map=self.gold_map,
                    workers=int(
                        (self.config.get("backend") or {}).get("workers", 1)
                    ),
                )
            preflight[intervention_id] = {"v1": v1, "v2": v2}
        for candidate in proposals:
            intervention_id = str(candidate.get("intervention_id") or "")
            v1 = preflight[intervention_id]["v1"]
            v2 = preflight[intervention_id]["v2"]
            report: dict[str, Any] = {
                "intervention_id": intervention_id,
                "candidate": candidate,
                "v1": v1,
                "v2": None,
                "v3": None,
                "m": None,
                "terminal": False,
            }
            report["v2"] = v2
            if v1["passed"] and v2["passed"]:
                    eligible_ids = {
                        item_id for item_id, status in preflight.items()
                        if status["v1"]["passed"] and status["v2"]["passed"]
                    }
                    v3_group_entries = [candidate]
                    for partner_id in joint_partners.get(intervention_id, []):
                        if partner_id not in eligible_ids:
                            continue
                        partner = next(
                            (
                                item for item in proposals
                                if str(item.get("intervention_id") or "")
                                == partner_id
                            ),
                            None,
                        )
                        if partner is not None:
                            v3_group_entries.append(partner)
                    v3_registry = make_registry([
                        *self.registry.entries, *v3_group_entries,
                    ])
                    v3_control_entries = [
                        *self.registry.entries,
                        *[
                            entry for entry in v3_group_entries
                            if str(entry.get("intervention_id") or "")
                            != intervention_id
                        ],
                    ]
                    v3_control = make_registry(v3_control_entries)
                    fresh = any(
                        str(entry.get("hook") or "") in {
                            "judge.system", "scanner.system", "upgrade.pre",
                            "upgrade.system", "tracer.system", "defense.pre",
                            "defense.system",
                        }
                        for entry in v3_group_entries
                    )
                    evaluate = (
                        self.backend.evaluate_fresh
                        if fresh else self.backend.evaluate
                    )
                    evaluate_kwargs = {
                        "dataset_root": self.config["dataset_root"],
                        "gold_map": self.gold_map,
                        "workers": int(
                            (self.config.get("backend") or {}).get("workers", 1)
                        ),
                    }
                    control_s = evaluate(
                        self.split.sentinel,
                        registry=v3_control,
                        **evaluate_kwargs,
                    )
                    control_g = evaluate(
                        self.split.gain,
                        registry=v3_control,
                        **evaluate_kwargs,
                    )
                    candidate_s = evaluate(
                        self.split.sentinel,
                        registry=v3_registry,
                        **evaluate_kwargs,
                    )
                    candidate_g = evaluate(
                        self.split.gain,
                        registry=v3_registry,
                        **evaluate_kwargs,
                    )
                    v3 = v3_semantics(
                        candidate,
                        baseline_rows=[*control_s, *control_g],
                        candidate_rows=[*candidate_s, *candidate_g],
                        config=self.config,
                    )
                    report["v3"] = v3
                    report["candidate_rows"] = _summarise_rows(
                        [*candidate_s, *candidate_g]
                    )
                    if v3["passed"]:
                        v3_candidates.append(candidate)
            if not v1["passed"]:
                self._ledger("rejected", {
                    "cycle": cycle,
                    "intervention_id": intervention_id,
                    "gate": "V1",
                    "reason": v1["reason"],
                })
            elif not report["v2"]["passed"]:
                self._ledger("rejected", {
                    "cycle": cycle,
                    "intervention_id": intervention_id,
                    "gate": "V2",
                    "reason": report["v2"]["reason"],
                })
            elif not report["v3"]["passed"]:
                self._ledger("rejected", {
                    "cycle": cycle,
                    "intervention_id": intervention_id,
                    "gate": "V3",
                    "reason": report["v3"]["reason"],
                })
            gate_reports.append(report)

        selection = marginal_selection(
            v3_candidates,
            active_entries=self.registry.entries,
            backend=self.backend,
            dataset_root=self.config["dataset_root"],
            gain_cases=self.split.gain,
            active_rows=active_gain,
            config=self.config,
            gold_map=self.gold_map,
            workers=int((self.config.get("backend") or {}).get("workers", 1)),
            max_admits=int(
                (self.config.get("budget") or {}).get("max_admits", 2)
            ),
            joint_groups=joint_groups,
        )
        selected = selection["selected"]
        selected_ids = {str(c.get("intervention_id") or "") for c in selected}
        for item in selection["reports"]:
            intervention_id = str(
                (item.get("candidate") or {}).get("intervention_id") or ""
            )
            if item["passed"]:
                continue
            self._ledger("rejected", {
                "cycle": cycle,
                "intervention_id": intervention_id,
                "gate": "M",
                "reason": item["reason"],
            })
        for item in selection["joint_reports"]:
            candidate_ids = [
                str(identifier) for identifier in item.get("candidate_ids") or []
            ]
            if item.get("passed") and all(
                identifier in selected_ids for identifier in candidate_ids
            ):
                continue
            reason = item.get("reason") or "joint configuration did not pass M"
            for intervention_id in candidate_ids:
                self._ledger("rejected", {
                    "cycle": cycle,
                    "intervention_id": intervention_id,
                    "gate": "M",
                    "reason": f"joint {candidate_ids}: {reason}",
                })

        library_problems = self.registry.check_limits(
            [*self.registry.entries, *selected],
            library_cfg=self.config.get("library") or {},
        )
        if library_problems:
            for candidate in selected:
                self._ledger("rejected", {
                    "cycle": cycle,
                    "intervention_id": str(candidate.get("intervention_id") or ""),
                    "gate": "LIBRARY",
                    "reason": "; ".join(library_problems),
                })
            selected = []
        admitted_ids = [str(c.get("intervention_id") or "") for c in selected]
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
                row = next(
                    (
                        item for item in selection["reports"]
                        if str((item.get("candidate") or {}).get("intervention_id") or "")
                        == intervention_id
                    ),
                    None,
                )
                joint_report = None
                if row is None:
                    joint_report = next(
                        (
                            item for item in selection["joint_reports"]
                            if intervention_id
                            in {str(x) for x in item.get("candidate_ids") or []}
                        ),
                        None,
                    )
                marginal = int(
                    {
                        str(item.get("intervention_id") or ""): item
                        for item in selection.get("selected_attribution", [])
                    }.get(intervention_id, {}).get("delta", {}).get(
                        "marginal_cases",
                        (row or joint_report or {}).get("combined_delta", {}).get(
                            "marginal_cases", 0
                        ),
                    )
                )
                joint_with = (
                    [
                        str(x) for x in joint_report.get("candidate_ids") or []
                        if str(x) != intervention_id
                    ]
                    if joint_report is not None else []
                )
                gate_report = next(
                    (
                        item for item in gate_reports
                        if item.get("intervention_id") == intervention_id
                    ),
                    {},
                )
                fired = float((gate_report.get("v3") or {}).get("fired_rate", 0.0))
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
                    "joint_with": joint_with,
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

        # Library governance: retirement and mechanical drift rollback.
        rollback = self._apply_rollback_check(cycle)

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
            "marginal_selection": {
                "reports": selection["reports"],
                "joint_reports": selection["joint_reports"],
                "selected": [
                    str(c.get("intervention_id") or "") for c in selected
                ],
                "removed_redundant": selection["removed_redundant"],
                "selected_attribution": selection.get("selected_attribution", []),
                "library_rejections": library_problems,
            },
            "active_after_cycle": [
                str(c.get("intervention_id") or "")
                for c in self.registry.entries
            ],
            "retirement": retirement,
            "rollback": rollback,
            "active_effect_on_g": paired_delta(
                baseline_gain,
                self._evaluate(self.registry.registry, self.split.gain),
            ),
            "ledger_integrity": self.ledger.verify(),
        }
        self._save_cycle_report(cycle, report)
        self.cycle_reports.append(report)
        return report

    def _apply_retirement(self, cycle: int) -> dict[str, Any]:
        threshold = int(
            (self.config.get("library") or {}).get(
                "retire_after_cycles_marginal_le0", 2
            )
        )
        retire_ids: list[str] = []
        active_rows = self._evaluate(self.registry.registry, self.split.gain)
        for entry in self.registry.entries:
            intervention_id = str(entry.get("intervention_id") or "")
            without = [
                item for item in self.registry.entries
                if str(item.get("intervention_id") or "") != intervention_id
            ]
            without_rows = self._evaluate(make_registry(without), self.split.gain)
            contribution = paired_delta(without_rows, active_rows)
            fired = fired_rate(active_rows, intervention_id)
            self.registry.record_marginal(
                intervention_id,
                int(contribution["marginal_cases"]),
                cycle=cycle,
                fired_rate=fired,
            )
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

    def _apply_rollback_check(self, cycle: int) -> dict[str, Any]:
        threshold = int(
            (self.config.get("library") or {}).get(
                "rollback_after_cycles_net_negative", 2
            )
        )
        history = list(self.registry.state.get("net_contribution_history") or [])
        net = paired_delta(
            self._evaluate(empty_registry(), self.split.gain),
            self._evaluate(self.registry.registry, self.split.gain),
        )
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
        return {"triggered": False, "threshold": threshold, "history": history[-threshold:]}

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
        cycles = int(self.config.get("cycles", 3) if cycles is None else cycles)
        if cycles < 0:
            raise ValueError("cycles must be non-negative")
        for cycle in range(1, cycles + 1):
            self.run_cycle(cycle)
        return {
            "cycles": self.cycle_reports,
            "active": [
                str(c.get("intervention_id") or "") for c in self.registry.entries
            ],
            "ledger_integrity": self.ledger.verify(),
            "registry_state": self.registry.state,
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
        empty_rows = self.backend.evaluate(
            case_ids,
            dataset_root=self.config["dataset_root"],
            registry=empty,
            gold_map=final_gold,
            workers=int((self.config.get("backend") or {}).get("workers", 1)),
        )
        active_rows = self.backend.evaluate(
            case_ids,
            dataset_root=self.config["dataset_root"],
            registry=active,
            gold_map=final_gold,
            workers=int((self.config.get("backend") or {}).get("workers", 1)),
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
                str(c.get("intervention_id") or "") for c in self.registry.entries
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
            from detector_v2.dataset import gold_fields
            import json as _json
            from pathlib import Path as _Path
            case = str(row.get("case") or "")
            raw = _json.loads(
                (_Path(dataset_root) / case).read_text(encoding="utf-8-sig")
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
