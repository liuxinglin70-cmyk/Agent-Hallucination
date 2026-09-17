"""Evolution cycle engine.

One cycle (B stage):

1. defect-injection audit of the gate, once per machinery fingerprint;
2. patrol of the deployed version on a fresh sample P, with the bare base kept
   as the reference arm so new failures and the standing residual separate;
3. mechanical stable failure families (worksheet);
4. at most three candidate proposals;
5. STRUCTURAL structural screening, then one detection arm per admission unit on
   S+G; HARMLESSNESS (sentinel subset), BEHAVIOR (S+G claim consistency) and M (gain
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
import os
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution.backends import (
    CACHE_FORMAT,
    DetectionBackend,
    file_digest,
    fingerprint,
    make_backend,
    source_digest,
)
from evolution.config import ensure_dirs, load_config, resolve_path
from evolution.contracts import empty_registry, make_registry, save_json
from detector.dataset import gold_fields
from evolution.gates import (
    arm_has_infrastructure_failure,
    defect_candidates,
    meets_m,
    requires_fresh_evaluation,
    structural_gate,
    harmlessness_gate,
    behavior_gate,
)
from evolution.ledger import Ledger, load_ledger
from evolution.meta_eval import write_meta_report
from evolution.metrics import fired_rate, paired_delta, source_deltas
from evolution.proposer import ProposalBatch, infer_joint_groups, make_proposer
from evolution.registry import ActiveRegistry, load_active_registry
from evolution.checkpoint import CycleJournal
from evolution.gaps import (
    GAP_STORE_NAME,
    GAP_SUMMARY_NAME,
    open_gaps,
    record_gaps,
    write_gap_summary,
)
from evolution.harness import (
    HARNESS_FILENAME,
    HARNESS_HISTORY_NAME,
    build_manifest,
    harness_diff,
    load_manifest,
    write_manifest,
)
from evolution.memory import (
    record_episode,
    record_improvement,
    record_procedure,
    recall as recall_experience,
    write_recall,
)
from evolution.splits import (
    EvolutionSplit,
    build_split,
    bool_gold,
    load_gold,
    split_manifest_report,
)
from evolution.worksheet import (
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


def _row_is_failure(row: Mapping[str, Any]) -> bool:
    """A row fails when it cannot deliver the gold decision.

    Unresolved and non-authoritative rows count as failures: from the base
    version's point of view an undecided case is an unsolved case. This is a
    diagnostic label for patrol only, never an admission score.
    """
    gold = row.get("gold")
    if gold is not True and gold is not False:
        return True
    prediction = row.get("prediction")
    return prediction is None or prediction != gold


def _failure_contrast(
    bare_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
    *,
    active_registry_size: int,
) -> dict[str, Any]:
    """Split the active patrol's failures into new versus persisting ones.

    The patrol runs the deployed version so the next round is aimed at the
    failures the system actually still has. The bare-base arm is kept as the
    long-term reference, which makes the two classes separable:

    * ``new_failures``: wrong on the active version, right on the bare base —
      a regression introduced by admitted interventions;
    * ``persisting_failures``: wrong on both — the standing residual.
    """
    bare_by_case = {str(row.get("case") or ""): row for row in bare_rows}
    new_cases: list[str] = []
    persisting_cases: list[str] = []
    resolved_cases: list[str] = []
    for row in active_rows:
        case = str(row.get("case") or "")
        active_failed = _row_is_failure(row)
        bare_row = bare_by_case.get(case)
        bare_failed = _row_is_failure(bare_row) if bare_row else True
        if active_failed and bare_failed:
            persisting_cases.append(case)
        elif active_failed:
            new_cases.append(case)
        elif bare_failed:
            resolved_cases.append(case)
    return {
        "active_registry_size": int(active_registry_size),
        "new_failures": {"count": len(new_cases), "cases": sorted(new_cases)},
        "persisting_failures": {
            "count": len(persisting_cases), "cases": sorted(persisting_cases),
        },
        "resolved_by_active": {
            "count": len(resolved_cases), "cases": sorted(resolved_cases),
        },
    }


def gain_opportunity(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How many cases the marginal set can actually reward, per polarity.

    ``marginal_cases`` counts all-case correct predictions gained, so a claim
    can only be scored where the control arm is *wrong in that direction*. This
    is a property of the instrument (the gain set together with the baseline's
    behaviour), not of any candidate, which is why it is reported per round
    rather than inferred from a rejection.
    """
    return {
        "recall": sum(
            1 for row in rows
            if row.get("gold") is True and row.get("prediction") is False
        ),
        "precision": sum(
            1 for row in rows
            if row.get("gold") is False and row.get("prediction") is True
        ),
    }


def gate_rejection_is_unevaluable(
    *,
    gate: str,
    verdict: Mapping[str, Any],
    report: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    """Whether a failing gate failed for want of a measurement.

    Only BEHAVIOR and M can be unevaluable: STRUCTURAL and HARMLESSNESS decide
    from contract and sentinel protection, which do not depend on the marginal
    set's polarity mix. A rejection caused by an actual new error or a health
    regression is substantive and never counts as unevaluable.
    """
    if gate not in {"behavior", "m"}:
        return False
    # An M verdict that reports itself as unmeasured, or as skipped because no
    # evaluation set had matching coverage, carries no substantive rejection.
    status = str(verdict.get("status") or "")
    if gate == "m" and status in {"not_evaluable", "skipped_unmeasured"}:
        delta = verdict.get("delta") or {}
        return not (delta.get("new_errors") or delta.get("health_regressed"))
    polarity = str((candidate.get("claim") or {}).get("target_polarity") or "")
    opportunities = report.get("gain_opportunities") or {}
    if opportunities.get(polarity) != 0:
        return False
    delta = verdict.get("delta") or {}
    return not (delta.get("new_errors") or delta.get("health_regressed"))


def terminal_classification(
    report: Mapping[str, Any],
    *,
    admitted: bool,
) -> str:
    """Classify a candidate's outcome as admitted, falsified or unmeasured.

    The distinction matters because "rejected" and "not evaluated" license
    different follow-up: a falsified mechanism should not be re-proposed, while
    an unmeasured one has simply not been judged yet and must not be treated as
    refuted.
    """
    if admitted:
        return "admitted"
    candidate = report.get("candidate") or {}
    failing = [
        gate for gate in ("structural", "harmlessness", "behavior", "m")
        if (report.get(gate) or {}).get("passed") is False
    ]
    if not failing:
        return "unresolved"
    if all(
        gate_rejection_is_unevaluable(
            gate=gate, verdict=report.get(gate) or {},
            report=report, candidate=candidate,
        )
        for gate in failing
    ):
        return "not_evaluated"
    return "rejected"


COVERAGE_POLICY_MODES = ("strict", "replacement", "replacement_or_skip")


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
        kwargs = dict(
            dataset_root=self.engine.config["dataset_root"],
            registry=registry,
            gold_map=self.engine.gold_map,
            workers=int(
                (self.engine.config.get("backend") or {}).get("workers", 1)
            ),
            deadline=self.deadline,
        )
        journal = getattr(self.engine, "_journal", None)
        if journal is None:
            return evaluate(list(cases), **kwargs)
        rows = journal.evaluate(list(cases), registry=registry, fresh=fresh,
                                evaluate=evaluate, kwargs=kwargs)
        # Restored baseline observations must seed the replay backend, even
        # after restarting the process with an empty in-memory baseline map.
        if not (registry or {}).get("interventions") and not fresh:
            backend = self.engine.backend
            while hasattr(backend, "backend"):
                backend = backend.backend
            if hasattr(backend, "_base_results"):
                backend._base_results.update({r["case"]: r["result"] for r in rows})
        return rows


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
            for case in self.split.pool
        }
        self.cycle_reports: list[dict[str, Any]] = []
        # H_t = (G, C, T, M, R) base. These describe and remember the
        # generation that ran; they are read for reporting and for what the
        # next round starts from, and they never score a candidate.
        self.harness_dir = Path(self.config["harness_dir"])
        self.memory_root = Path(self.config["memory_root"])
        self.gap_store = self.harness_dir / GAP_STORE_NAME
        # Optional replacement evaluation set for coverage response. Empty by
        # default, which is what keeps the strict policy behaviour-identical.
        self._replacement_gain_cases: list[str] = []
        self._previous_manifest = self._load_previous_manifest()

    # ------------------------------------------------------------------
    def _cycle_seconds(self) -> float | None:
        raw = (self.config.get("budget") or {}).get("cycle_timeout_seconds")
        return float(raw) if raw else None

    def _ledger(self, event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if getattr(self, "_resuming", False):
            for record in self.ledger.read():
                if record["event"] == event and record["payload"] == payload:
                    return record
        return self.ledger.append(event, payload)

    # ------------------------------------------------------------------
    # H_t = (G, C, T, M, R) base.
    #
    # These methods describe and remember the generation that ran. They are
    # reporting and starting-point facilities: none of them scores, admits or
    # rejects a candidate, so nothing here can move a gate outcome.
    # ------------------------------------------------------------------
    def _load_previous_manifest(self) -> dict[str, Any] | None:
        """The generation the next round starts from, if one was recorded."""
        path = self.harness_dir / HARNESS_FILENAME
        if not path.is_file():
            return None
        return load_manifest(path)

    def _proposal_memory_context(
        self,
        diagnosed_families: Sequence[Mapping[str, Any]],
        capability_gaps: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Assemble the H_t memory handed to the proposer.

        Two things are retrieved, both already stored by earlier rounds:

        * admitted procedures for the mechanisms currently failing, so a route
          that already worked is reused rather than re-derived;
        * the open capability backlog, so a family that keeps failing because no
          route can treat it is legible instead of looking like a drafting
          failure.

        Retrieval is bounded per family, so the drafting prompt does not grow
        with the size of the memory stores.
        """
        procedures: list[dict[str, Any]] = []
        for family in diagnosed_families:
            name = str(family.get("failure_family") or "")
            if not name:
                continue
            retrieved = recall_experience(
                self.memory_root,
                failure_family=name,
                polarity=str(
                    (family.get("actionability") or {}).get("polarity") or ""
                ),
                treatment_routes=list(
                    (family.get("actionability") or {}).get("routes") or ()
                ),
                limit=2,
            )
            procedures.extend(retrieved.get("entries") or [])
        # Both lists are capped. The prompt must not grow with the size of the
        # stores, or a long run turns memory retrieval into an unbounded prompt.
        max_procedures = 6
        max_gaps = 8
        backlog = open_gaps(self.gap_store)
        current = {
            str(family.get("failure_family") or "") for family in diagnosed_families
        }
        current_gaps = [
            item for item in backlog
            if str(item.get("failure_family") or "") in current
        ]
        return {
            "procedures": procedures[:max_procedures],
            # Open gaps are ranked by occurrence in ``open_gaps``, so the cap
            # keeps the most persistent ones.
            "open_capability_gaps": [
                {
                    "failure_family": item.get("failure_family"),
                    "reason": item.get("reason"),
                    "routes": item.get("routes"),
                    "occurrences": item.get("occurrences"),
                }
                for item in backlog[:max_gaps]
            ],
            # A gap that keeps reappearing in this round's own patrol and has
            # been seen more than once is the strongest signal that the missing
            # capability, not the drafting, is the bottleneck.
            "recurring_capability_gaps": [
                {
                    "failure_family": item.get("failure_family"),
                    "reason": item.get("reason"),
                    "occurrences": item.get("occurrences"),
                }
                for item in current_gaps
                if int(item.get("occurrences") or 0) >= 2
            ][:max_gaps],
        }

    def set_replacement_gain_cases(
        self, cases: Sequence[str] | None,
    ) -> None:
        """Install an alternative evaluation set for coverage response.

        Held only in memory: persisting it would need the same gold, protocol
        and exposure guarantees as the primary gain set, which is a separate
        decision. Until it is set the replacement policy reports that no
        matching set exists rather than inventing one.
        """
        self._replacement_gain_cases = [str(case) for case in (cases or ())]

    def _coverage_mode(self, polarity: str) -> str:
        """Resolve the coverage response mode for one claim direction.

        ``strict`` (the default) keeps the original rule: a claim whose
        direction has no repairable case in the gain set is measured there and
        comes back ``not_evaluated``, with admission still blocked. The other
        modes make coverage a decision instead of only a reading, and must be
        turned on explicitly and per direction.
        """
        cfg = self.config.get("coverage_response") or {}
        per_direction = cfg.get("policies") or {}
        mode = str(per_direction.get(polarity) or cfg.get("mode") or "strict")
        if mode not in COVERAGE_POLICY_MODES:
            raise ValueError(
                f"coverage_response mode for {polarity!r} must be one of "
                f"{list(COVERAGE_POLICY_MODES)}, got {mode!r}"
            )
        return mode

    def _coverage_response(
        self, polarity: str, rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """What to do when a claim's direction is not repairable on ``rows``.

        Returns ``None`` under the strict policy, which preserves the existing
        behaviour exactly. Otherwise it reports whether a replacement
        evaluation set with matching coverage exists, whether the claim was
        therefore skipped as unmeasured, or that no set matched either.
        """
        mode = self._coverage_mode(polarity)
        if mode == "strict":
            return None
        available = sorted(
            direction
            for direction, value in gain_opportunity(rows).items()
            if value > 0
        )
        if bool(self._replacement_gain_cases):
            return {
                "kind": "replacement",
                "policy": mode,
                "available_polarities": available,
                "replacement_cases": len(self._replacement_gain_cases),
                "note": (
                    "direction is not repairable on the default gain set; "
                    "re-measured on the replacement evaluation set"
                ),
            }
        if mode == "replacement_or_skip":
            return {
                "kind": "skip",
                "policy": mode,
                "available_polarities": available,
                "note": (
                    "no evaluation set with matching coverage; the claim was "
                    "not measured (admission remains blocked)"
                ),
            }
        return {
            # No alternative set is installed, so nothing can be re-measured:
            # report that rather than claiming a replacement happened.
            "kind": "none_installed",
            "policy": mode,
            "available_polarities": available,
            "replacement_cases": 0,
            "note": (
                "no replacement evaluation set configured; the claim was "
                "measured on the default gain set"
            ),
        }

    def _instrument_report(
        self,
        *,
        baseline_gain: Sequence[Mapping[str, Any]],
        gate_reports: Sequence[Mapping[str, Any]],
        admitted_ids: set[str],
    ) -> dict[str, Any]:
        """What this round's measurement could and could not decide.

        Reported per round because it is a property of the instrument rather
        than of a candidate: which claim directions the gain set can actually
        score, and how each candidate's outcome should be read. A direction
        with zero opportunities is unmeasurable, not failed, and the report
        states that instead of leaving it to be inferred from a rejection
        reason.
        """
        opportunity = gain_opportunity(baseline_gain)
        classifications = {
            str(item.get("intervention_id") or ""): terminal_classification(
                item,
                admitted=str(item.get("intervention_id") or "") in admitted_ids,
            )
            for item in gate_reports
        }
        counts: dict[str, int] = {}
        for value in classifications.values():
            counts[value] = counts.get(value, 0) + 1
        # How the engine responded to missing coverage, per candidate, so that
        # "admission stayed 0 because nothing was measurable" is distinguishable
        # from "admission stayed 0 although the claim was re-measured".
        responses: dict[str, dict[str, Any]] = {}
        for item in gate_reports:
            response = (
                item.get("coverage_response")
                or (item.get("m") or {}).get("coverage_response")
            )
            if response:
                responses[str(item.get("intervention_id") or "")] = {
                    "kind": response.get("kind"),
                    "policy": response.get("policy"),
                    "passed": response.get("passed"),
                    "evaluated_on": (
                        "default" if response.get("kind") != "replacement"
                        else "replacement"
                    ),
                }
        response_counts: dict[str, int] = {}
        for value in responses.values():
            kind = str(value.get("kind") or "")
            response_counts[kind] = response_counts.get(kind, 0) + 1
        return {
            "gain_opportunity": opportunity,
            "unmeasurable_polarities": sorted(
                polarity for polarity, value in opportunity.items() if value == 0
            ),
            "replacement_gain_cases": len(self._replacement_gain_cases),
            "coverage_responses": responses,
            "coverage_response_counts": response_counts,
            "terminal_classification": classifications,
            "terminal_counts": counts,
            # A skip is reported as not_evaluated in the terminal vocabulary;
            # both mean the claim was not measured, so one check covers them.
            "instrument_limited": bool(counts.get("not_evaluated")),
        }

    def record_harness(self, cycle: int) -> dict[str, Any]:
        """Write H_t for this cycle and diff it against the previous one.

        Called after a committed registry write, so the manifest describes the
        version the next round will actually load.
        """
        manifest = build_manifest(
            config=self.config,
            registry=self.registry.registry,
            memory_root=self.memory_root,
            cycle=cycle,
            parent_digest=(
                self._previous_manifest.get("digest")
                if self._previous_manifest else None
            ),
        )
        diff = harness_diff(self._previous_manifest, manifest)
        write_manifest(
            self.harness_dir / HARNESS_FILENAME,
            manifest=manifest,
            history_path=self.harness_dir / HARNESS_HISTORY_NAME,
        )
        self._previous_manifest = manifest
        self._ledger("harness", {
            "cycle": cycle,
            "digest": manifest["digest"],
            "parent_digest": manifest.get("parent_digest"),
            "changed_components": diff["changed"],
            "registry_hash": manifest.get("registry_hash"),
        })
        return {"manifest": manifest, "diff": diff}

    def record_capability_gaps(
        self,
        cycle: int,
        capability_gaps: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Keep unexecutable families as backlog items instead of dropping them.

        A gap is a measured statement that the action vocabulary is missing
        something. Recording it admits and rejects nothing; it is the only
        reason a later round can see the capability is still absent rather than
        rediscovering it.
        """
        if capability_gaps:
            record_gaps(self.gap_store, cycle=cycle, gaps=capability_gaps)
        backlog = open_gaps(self.gap_store)
        summary = write_gap_summary(
            self.gap_store,
            summary_path=self.harness_dir / GAP_SUMMARY_NAME,
        )
        self._ledger("capability_gaps", {
            "cycle": cycle,
            "recorded": [
                {
                    "failure_family": item.get("failure_family"),
                    "classification": item.get("classification"),
                    "reason": item.get("reason"),
                }
                for item in capability_gaps
            ],
            "open": len(backlog),
        })
        return {"open": backlog, "summary": summary}

    def recall_experience(
        self,
        *,
        failure_family: str = "",
        polarity: str = "",
        treatment_routes: Sequence[str] = (),
        bottleneck: str = "",
    ) -> dict[str, Any]:
        """Read a bounded slice of prior experience for the current mechanism."""
        retrieved = recall_experience(
            self.memory_root,
            failure_family=failure_family,
            polarity=polarity,
            treatment_routes=treatment_routes,
            bottleneck=bottleneck,
        )
        write_recall(self.memory_root, retrieved)
        return retrieved

    def _record_cycle_experience(
        self,
        *,
        cycle: int,
        diagnosed_families: Sequence[Mapping[str, Any]],
        gate_reports: Sequence[Mapping[str, Any]],
        rejected: Sequence[Mapping[str, Any]],
        selected: Sequence[Mapping[str, Any]],
    ) -> None:
        """Persist this cycle's attempts and, for admitted ones, procedures.

        Only admitted interventions become procedural memory: a procedure
        claims how to do something, and that claim is only established once a
        gate has accepted it. A rejection that was merely unevaluable is kept
        distinguishable from a falsified mechanism.
        """
        family_by_name = {
            str(item.get("failure_family") or ""): item
            for item in diagnosed_families
        }
        rejected_by_id = {
            str(item.get("intervention_id") or ""): item
            for item in rejected
        }
        admitted_ids = {
            str(item.get("intervention_id") or "") for item in selected
        }
        for report in gate_reports:
            candidate = dict(report.get("candidate") or {})
            intervention_id = str(candidate.get("intervention_id") or "")
            provenance = candidate.get("provenance") or {}
            family = str(provenance.get("failure_family") or "")
            polarity = str(
                (candidate.get("claim") or {}).get("target_polarity") or ""
            )
            routes = list(
                ((family_by_name.get(family) or {}).get("actionability") or {})
                .get("routes") or ()
            )
            if intervention_id in admitted_ids:
                outcome, gate, reason, unevaluable = "admitted", None, "", False
            else:
                rejection = rejected_by_id.get(intervention_id) or {}
                outcome = "rejected"
                gate = str(rejection.get("gate") or "") or None
                reason = str(rejection.get("reason") or "")
                unevaluable = bool(rejection.get("not_evaluable"))
            record_episode(
                self.memory_root,
                cycle=cycle,
                failure_family=family,
                polarity=polarity,
                outcome=outcome,
                gate=gate,
                reason=reason,
                intervention_id=intervention_id,
                intervention_type=str(candidate.get("type") or ""),
                hook=str(candidate.get("hook") or ""),
                treatment_routes=routes,
                unevaluable=unevaluable,
            )
            if intervention_id in admitted_ids:
                record_procedure(
                    self.memory_root,
                    cycle=cycle,
                    failure_family=family,
                    polarity=polarity,
                    treatment_route=(routes[0] if routes else ""),
                    intervention_type=str(candidate.get("type") or ""),
                    hook=str(candidate.get("hook") or ""),
                    intervention_id=intervention_id,
                    expected_behavior=str(
                        (candidate.get("claim") or {}).get("expected_behavior") or ""
                    ),
                    failure_boundary=str(
                        (report.get("behavior") or {}).get("behavior_note") or ""
                    ),
                )

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
            "cache_version": "defect-audit",
            "backend": {
                "name": str(
                    getattr(self.backend, "name", type(self.backend).__name__)
                ),
                "cache_version": CACHE_FORMAT,
            },
            "source": source_digest(),
            "detector_config": file_digest(self.config.get("detector_config")),
            "sentinel": sorted(self.split.sentinel),
            "gain": sorted(self.split.gain),
            "gold": {
                case: self.gold_map.get(case)
                for case in [*self.split.sentinel, *self.split.gain]
            },
            "active_registry_hash": self.registry.registry_hash,
            "active_registry": self.registry.registry,
            # The coverage policy and replacement set change how a defect's M
            # verdict is produced, so they belong in the audit's identity.
            "coverage_response": dict(self.config.get("coverage_response") or {}),
            "replacement_gain_cases": sorted(self._replacement_gain_cases),
            "records": {
                case: file_digest(Path(self.config["dataset_root"]) / case)
                for case in [*self.split.sentinel, *self.split.gain]
            },
            "provider_routes": {
                key: os.environ.get(key, "")
                for key in (
                    "DETECTOR_ENDPOINT",
                    "DETECTOR_JUDGE_MODEL",
                    "DETECTOR_RECALL_MODEL",
                    "DETECTOR_UPGRADE_MODEL",
                    "DETECTOR_TRACER_MODEL",
                    "DETECTOR_DEFENSE_MODEL",
                )
            },
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
        inconclusive_any = False
        for candidate in defect_candidates():
            # Defects enter the same STRUCTURAL -> one S+G arm -> HARMLESSNESS/BEHAVIOR/M pipeline
            # as real candidates; a leaked defect would pass all of them.
            structural = structural_gate(
                candidate, active_entries=self.registry.entries, config=self.config,
            )
            report: dict[str, Any] = {
                "intervention_id": candidate.get("intervention_id"),
                "structural": structural,
                "harmlessness": None,
                "behavior": None,
                "m": None,
                "audit_expected_reject": True,
                "audit_rejected": not structural["passed"],
                "audit_passed": False,
                "audit_inconclusive": False,
            }
            if structural["passed"]:
                unit = self._run_unit(
                    candidate,
                    partners=[],
                    active_entries=self.registry.entries,
                    active_rows=[*baseline_s, *baseline_g],
                    arms=arms,
                )
                report["harmlessness"] = unit["harmlessness"]
                if unit["harmlessness"]["passed"]:
                    report["behavior"] = unit["behavior"]
                    if unit["behavior"]["passed"]:
                        report["m"] = unit["m"]
                        if unit["m"]["passed"]:
                            report["audit_rejected"] = False
                            report["audit_passed"] = True
                if not report["audit_passed"]:
                    # A rejection that came from an unresolved/deadline/
                    # failed-stage arm is not evidence that the gate LOGIC
                    # rejected the defect. Mark it inconclusive so the audit
                    # cannot certify itself without exercising the gates.
                    infrastructure = arm_has_infrastructure_failure(
                        unit["candidate_rows"],
                    )
                    if infrastructure:
                        report["audit_inconclusive"] = True
                        report["audit_inconclusive_reason"] = infrastructure
            if not report["audit_passed"]:
                report["audit_rejected"] = True
            if report["audit_passed"]:
                passed_any = True
            if report["audit_inconclusive"]:
                inconclusive_any = True
            reports.append(report)
        verdict = {
            "enabled": True,
            # A defect leaking through fails the audit; an audit that could
            # not score a defect is not a pass either, so it freezes too.
            "passed": not passed_any and not inconclusive_any,
            "frozen": passed_any or inconclusive_any,
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
        """One admission unit: one S+G arm scored by HARMLESSNESS, BEHAVIOR and M.

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
        polarity = str(
            (candidate.get("claim") or {}).get("target_polarity") or ""
        )
        sentinel = list(self.split.sentinel)
        control = (
            fresh_control_rows if fresh and fresh_control_rows is not None
            else list(active_rows)
        )
        control_g = _subset_rows(control, self.split.gain)
        opportunity = gain_opportunity(control_g)

        # Coverage is decided before any gate runs, because a claim whose
        # direction is unrepairable on the default gain set fails the BEHAVIOR
        # polarity check first and would never reach M. Under the strict policy
        # (the default) this changes nothing.
        response = (
            self._coverage_response(polarity, control_g)
            if polarity and opportunity.get(polarity) == 0
            else None
        )
        measurement_gain = list(self.split.gain)
        if (
            response is not None
            and response["kind"] == "replacement"
            and self._replacement_gain_cases
        ):
            # Re-measure the whole unit on the replacement set: the gate is not
            # relaxed, the instrument it runs on changes.
            measurement_gain = list(self._replacement_gain_cases)
            control = arms.run(
                [*sentinel, *measurement_gain],
                registry=make_registry(active_entries),
                fresh=fresh,
            )
            control_g = _subset_rows(control, measurement_gain)
            replacement_opportunity = gain_opportunity(control_g)
            response["replacement_opportunity"] = replacement_opportunity
            response["replacement_has_coverage"] = (
                int(replacement_opportunity.get(polarity) or 0) > 0
            )
        candidate_rows = arms.run(
            [*sentinel, *measurement_gain],
            registry=registry,
            fresh=fresh,
        )
        if response is not None and response["kind"] == "skip":
            # No evaluation set carries the declared direction: the claim is
            # marked unmeasured and skipped rather than rejected as false.
            return {
                "intervention_id": intervention_id,
                "candidate": dict(candidate),
                "partners": [dict(entry) for entry in partners],
                "fresh": fresh,
                "gain_opportunities": opportunity,
                "measurement_gain_cases": measurement_gain,
                "coverage_response": response,
                "candidate_rows": _summarise_rows(candidate_rows),
                "harmlessness": None,
                "behavior": None,
                "member_behavior": {},
                "m": {
                    "passed": False,
                    "reason": response["note"],
                    "delta": None,
                    "evaluable": False,
                    "status": "skipped_unmeasured",
                    "coverage_response": response,
                },
            }

        baseline_s = _subset_rows(control, sentinel)
        candidate_s = _subset_rows(candidate_rows, sentinel)
        harmlessness = harmlessness_gate(
            baseline_rows=baseline_s,
            candidate_rows=candidate_s,
            config=self.config,
        )
        behavior = None
        member_behavior: dict[str, dict[str, Any]] = {}
        m = None
        if harmlessness["passed"]:
            for member in [*partners, dict(candidate)]:
                member_id = str(member.get("intervention_id") or "")
                member_behavior[member_id] = behavior_gate(
                    member,
                    baseline_rows=control,
                    candidate_rows=candidate_rows,
                    config=self.config,
                )
            behavior = member_behavior[intervention_id]
            if all(verdict["passed"] for verdict in member_behavior.values()):
                control_g = _subset_rows(control, measurement_gain)
                candidate_g = _subset_rows(candidate_rows, measurement_gain)
                delta = paired_delta(control_g, candidate_g)
                passed, failures = meets_m(delta, config=self.config)
                m = {
                    "passed": passed,
                    "reason": "; ".join(failures),
                    "delta": delta,
                }
                if response is not None:
                    response["passed"] = passed
                    response["failure_reason"] = "; ".join(failures)
                    m["coverage_response"] = response
                    if not passed and response["kind"] == "replacement":
                        m.update(
                            evaluable=False,
                            status="not_evaluable",
                            reason=f"{response['note']}; {response['failure_reason']}",
                        )
                elif opportunity.get(polarity) == 0 and not passed:
                    # Strict policy: record the rejection as unmeasured rather
                    # than as a failed claim, admission blocked as before.
                    m.update(
                        evaluable=False,
                        status="not_evaluated",
                        reason=(
                            f"no {polarity} repair opportunities in gain; "
                            "claim was not measured (admission remains blocked)"
                        ),
                    )
        return {
            "intervention_id": intervention_id,
            "candidate": dict(candidate),
            "partners": [dict(entry) for entry in partners],
            "fresh": fresh,
            "gain_opportunities": opportunity,
            "measurement_gain_cases": measurement_gain,
            "coverage_response": response,
            "candidate_rows": _summarise_rows(candidate_rows),
            "harmlessness": harmlessness,
            "behavior": behavior,
            "member_behavior": member_behavior,
            "m": m,
        }

    # ------------------------------------------------------------------
    def _patrol_registry_mode(self) -> str:
        """Which version the patrol measures: ``active`` or ``bare_base``.

        ``active`` (default) patrols the version that is actually deployed, so
        a round is aimed at the residual failures of the current harness
        instead of re-deriving proposals from the bare base forever. The bare
        base still runs every cycle as the reference arm, which is what keeps
        the contamination guarantee observable rather than assumed.
        """
        mode = str(self.config.get("patrol_registry") or "active").strip().lower()
        if mode not in {"active", "bare_base"}:
            raise ValueError(
                "patrol_registry must be 'active' or 'bare_base', got "
                f"{mode!r}"
            )
        return mode

    def _patrol_uses_single_arm(self) -> bool:
        """True when the patrol arm and its reference are the same evaluation.

        That is the case for an explicitly bare-base patrol and for any cold
        start with an empty registry; the second sweep is only needed once an
        intervention is actually deployed.
        """
        return self._patrol_registry_mode() == "bare_base" or not self.registry.entries

    def _patrol_arms(
        self,
        arms: "_ArmRunner",
        cases: Sequence[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Evaluate the patrol arm plus its bare-base reference.

        Returns ``(patrol_rows, bare_rows, contrast)``. While the registry is
        empty the two arms are the same evaluation, so a cold start costs the
        same one sweep as before; once interventions are deployed the second
        arm is the explicit price of keeping the reference.
        """
        active_entries = self.registry.entries
        if self._patrol_uses_single_arm():
            bare_rows = arms.run(cases, registry=empty_registry())
            return bare_rows, bare_rows, _failure_contrast(
                bare_rows, bare_rows, active_registry_size=len(active_entries),
            )
        patrol_rows = arms.run(
            cases,
            registry=self.registry.registry,
            fresh=requires_fresh_evaluation(active_entries),
        )
        bare_rows = arms.run(cases, registry=empty_registry())
        return patrol_rows, bare_rows, _failure_contrast(
            bare_rows, patrol_rows, active_registry_size=len(active_entries),
        )

    def _extend_patrol(
        self,
        arms: "_ArmRunner",
        *,
        patrol_rows: Sequence[Mapping[str, Any]],
        bare_rows: Sequence[Mapping[str, Any]],
        bootstrap_cases: Sequence[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Append bootstrap cases to both patrol arms, keeping them in step.

        The stable-failure rerun only exists because a random patrol sample can
        miss the second member of a mechanism. Both arms must extend over the
        same cases or the new-versus-persisting contrast silently compares two
        different case sets.
        """
        active_entries = self.registry.entries
        bare_extension = arms.run(bootstrap_cases, registry=empty_registry())
        if self._patrol_uses_single_arm():
            # A cold start or an explicit bare-base patrol: one shared arm.
            extended_bare = [*bare_rows, *bare_extension]
            extended_patrol = list(extended_bare)
        else:
            extended_patrol = [
                *patrol_rows,
                *arms.run(
                    bootstrap_cases,
                    registry=self.registry.registry,
                    fresh=requires_fresh_evaluation(active_entries),
                ),
            ]
            extended_bare = [*bare_rows, *bare_extension]
        contrast = _failure_contrast(
            extended_bare,
            extended_patrol,
            active_registry_size=len(active_entries),
        )
        return extended_patrol, extended_bare, contrast

    def preview_cycle(self, cycle: int) -> dict[str, Any]:
        """Patrol + proposal preview without gates, ledger, or registry writes.

        This is the cheap pre-flight for experimenters: it shows whether the
        current cycle would produce any candidate at all, before spending
        gate detection budget.
        """
        # Preview runs without a journal, so the runner evaluates directly.
        arms = _ArmRunner(self, None)
        patrol_size = int(
            (self.config.get("budget") or {}).get("patrol_cases", 120)
        )
        patrol_cases = self.split.patrol_sample(cycle, patrol_size)
        # Preview measures the same version as the real cycle, so a preview
        # that finds no family means the deployed version has no family left
        # on that sample rather than the bare base having none.
        patrol_rows, bare_patrol_rows, patrol_contrast = self._patrol_arms(
            arms, patrol_cases,
        )
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
                patrol_rows, bare_patrol_rows, patrol_contrast = (
                    self._extend_patrol(
                        arms,
                        patrol_cases=patrol_rows,
                        bare_rows=bare_patrol_rows,
                        bootstrap_cases=bootstrap_cases,
                    )
                )
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
        self, cycle: int, *, proposal_batch: ProposalBatch | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        if not self.ledger.verify()["valid"]:
            raise RuntimeError("ledger integrity broken before cycle")
        started = any(r["event"] == "cycle_started" and r["payload"].get("cycle") == cycle
                      for r in self.ledger.read())
        if started and not resume:
            raise RuntimeError(f"cycle {cycle} has already started; use --resume, not a new search")
        report_path = self.paths["cycles"] / f"cycle{cycle:03d}.json"
        if resume and report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if any(r["event"] == "cycle_completed" and r["payload"].get("cycle") == cycle for r in self.ledger.read()):
                self.cycle_reports.append(report)
                return report
        contract = {"cycle": cycle, "config": fingerprint(self.config), "source": source_digest(),
                    "detector_config": file_digest(self.config.get("detector_config")),
                    "split": split_manifest_report(self.split), "registry": self.registry.registry_hash,
                    "data": {case: file_digest(Path(self.config["dataset_root"]) / case) for case in self.split.pool}}
        self._journal = CycleJournal(self.paths["root"] / "checkpoints" / f"cycle{cycle:03d}",
                                     contract, resume=resume)
        self._resuming = resume
        backend = self.backend
        while hasattr(backend, "backend"):
            backend = backend.backend
        # A new cycle may retry an outage; within this cycle keep the exact
        # baseline, including failures, for all paired candidate arms.
        memory = getattr(backend, "_base_results", None)
        if isinstance(memory, dict):
            for key in list(memory):
                if memory[key].get("decision_authority") != "authoritative":
                    del memory[key]
        if "proposal" in self._journal.state:
            saved = ProposalBatch(**self._journal.state["proposal"])
            if proposal_batch is not None and proposal_batch != saved:
                raise RuntimeError("resume may not replace persisted proposals")
            proposal_batch = saved
        try:
            report = self._run_cycle(cycle, proposal_batch=proposal_batch)
            self._journal.save(status="completed")
            return report
        except BaseException as exc:
            self._journal.save(status="interrupted", error_type=type(exc).__name__)
            self._ledger("cycle_interrupted", {"cycle": cycle, "error_type": type(exc).__name__})
            raise
        finally:
            self._journal = None
            self._resuming = False

    def _run_cycle(
        self,
        cycle: int,
        *,
        proposal_batch: ProposalBatch | None = None,
    ) -> dict[str, Any]:
        import time as _time

        if not self._resuming and any(
            record.get("event") == "cycle_started"
            and int((record.get("payload") or {}).get("cycle") or -1)
            == int(cycle)
            for record in self.ledger.read()
        ):
            raise RuntimeError(
                f"cycle {cycle} has already started; fixed S/G may not be "
                "reused for additional proposal search"
            )
        ledger_integrity = self.ledger.verify()
        if not ledger_integrity["valid"]:
            raise RuntimeError(
                f"ledger integrity broken before cycle {cycle}: "
                f"{ledger_integrity['reason']}"
            )
        self._ledger("cycle_started", {"cycle": cycle})
        seconds = self._cycle_seconds()
        if seconds is not None:
            seconds = max(0.001, seconds - self._journal.previous_seconds)
        arms = _ArmRunner(
            self, (_time.monotonic() + seconds) if seconds else None,
        )

        audit = self._journal.state.get("audit")
        if audit is None:
            audit = self._audit(cycle, arms)
            self._journal.save(audit=audit)
        if audit.get("frozen"):
            self._ledger("cycle_completed", {
                "cycle": cycle,
                "state": "frozen_after_defect_injection_audit",
            })
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
        # Patrol measures the deployed version (see ``_patrol_registry_mode``),
        # with the bare base kept as the reference arm so newly introduced
        # failures and the standing residual stay distinguishable.
        patrol_rows, bare_patrol_rows, patrol_contrast = self._patrol_arms(
            arms, patrol_cases,
        )
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
                patrol_rows, bare_patrol_rows, patrol_contrast = (
                    self._extend_patrol(
                        arms,
                        patrol_rows=patrol_rows,
                        bare_rows=bare_patrol_rows,
                        bootstrap_cases=bootstrap_cases,
                    )
                )
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
        worksheet["patrol_registry"] = self._patrol_registry_mode()
        worksheet["failure_contrast"] = patrol_contrast
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

        # One active reference arm on S+G shared by every unit this cycle.
        # It is computed before drafting because the gain set's repairable
        # directions are an input to drafting, not only a post-hoc read: a claim
        # whose direction has no repairable case cannot be scored at all.
        active = self.registry.registry
        active_fresh = requires_fresh_evaluation(self.registry.entries)
        baseline_sentinel = arms.run(
            self.split.sentinel, registry=active, fresh=active_fresh,
        )
        baseline_gain = arms.run(
            self.split.gain, registry=active, fresh=active_fresh,
        )
        active_rows = [*baseline_sentinel, *baseline_gain]
        instrument = self._instrument_report(
            baseline_gain=baseline_gain,
            gate_reports=(),
            admitted_ids=set(),
        )

        if proposal_batch is None:
            self._journal.save(proposing=True)
            if hasattr(self.proposer, "active_entries"):
                self.proposer.active_entries = self.registry.entries
            if hasattr(self.proposer, "set_rejection_feedback"):
                self.proposer.set_rejection_feedback(self.rejection_feedback)
            memory_context = self._proposal_memory_context(
                diagnosed_families, capability_gaps,
            )
            # Telling the proposer which directions cannot be measured this
            # round is the difference between drafting a scorable claim and
            # drafting one that can only come back not_evaluated.
            memory_context["scoreable_polarities"] = sorted(
                polarity
                for polarity, value in instrument["gain_opportunity"].items()
                if value > 0
            )
            memory_context["unmeasurable_polarities"] = list(
                instrument["unmeasurable_polarities"]
            )
            if hasattr(self.proposer, "set_memory_context"):
                self.proposer.set_memory_context(memory_context)
            self._ledger("proposal_memory", {
                "cycle": cycle,
                "procedures": len(memory_context["procedures"]),
                "open_gaps": len(memory_context["open_capability_gaps"]),
                "recurring_gaps": len(memory_context["recurring_capability_gaps"]),
            })
            proposal_batch = self.proposer.propose(
                diagnosed_families,
                cycle=cycle,
                max_proposals=int(
                    (self.config.get("budget") or {}).get("max_proposals", 3)
                ),
            )
            # Only a batch drawn without memory can establish what memory
            # changed. This records the counterfactual, not an assertion that
            # memory improved the drafting.
            self._ledger("proposal_memory_used", {
                "cycle": cycle,
                "supplied": bool(
                    memory_context["procedures"]
                    or memory_context["open_capability_gaps"]
                ),
                "candidates": len(proposal_batch.candidates),
            })
        self._journal.save(proposing=False, proposal={
            "candidates": proposal_batch.candidates, "joint_groups": proposal_batch.joint_groups,
            "notes": proposal_batch.notes})
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
            "candidates": proposals,
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

        # The reference arms were computed before drafting (see above); only
        # the fresh control roll depends on the drafted candidates.
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
        structural_preflight: dict[str, dict[str, Any]] = {}
        for candidate in proposals:
            intervention_id = str(candidate.get("intervention_id") or "")
            structural = structural_gate(
                candidate,
                active_entries=self.registry.entries,
                config=self.config,
                allowed_failure_cases=patrol_cases,
                diagnosed_families=diagnosed_families,
                dataset_root=self.config["dataset_root"],
                batch_candidates=proposals,
                patrol_rows=patrol_rows,
                joint_groups=joint_groups,
            )
            structural_preflight[intervention_id] = structural
            report: dict[str, Any] = {
                "intervention_id": intervention_id,
                "candidate": candidate,
                "structural": structural,
                "harmlessness": None,
                "behavior": None,
                "m": None,
                "partner_ids": joint_partners.get(intervention_id, []),
            }
            gate_reports.append(report)
            if not structural["passed"]:
                continue
            partners: list[dict[str, Any]] = []
            if intervention_id in joint_ids:
                partner_ids = joint_partners.get(intervention_id, [])
                if not all(
                    (structural_preflight.get(partner_id) or {}).get("passed")
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
                "harmlessness": unit["harmlessness"],
                "behavior": unit["behavior"],
                "m": unit["m"],
                "fresh": unit["fresh"],
                "candidate_rows": unit["candidate_rows"],
                "gain_opportunities": unit["gain_opportunities"],
                "measurement_gain_cases": unit["measurement_gain_cases"],
                "coverage_response": unit.get("coverage_response"),
            })
            # A joint unit's outcome belongs to every member; the member the
            # loop reaches first never runs a solo arm.
            unit_outcomes[intervention_id] = unit
            for partner in unit["partners"]:
                unit_outcomes.setdefault(
                    str(partner.get("intervention_id") or ""), unit,
                )
            if (
                (unit.get("harmlessness") or {}).get("passed")
                and all(
                    verdict["passed"]
                    for verdict in (unit.get("member_behavior") or {}).values()
                )
                and unit["m"] is not None
                and unit["m"]["passed"]
            ):
                qualified.append(unit)

        # A joint arm runs once, but each member must expose its own semantic
        # verdict and the shared HARMLESSNESS/M outcome in the audit report.
        for report in gate_reports:
            identifier = str(report.get("intervention_id") or "")
            unit = unit_outcomes.get(identifier)
            if not unit or report.get("harmlessness") is not None:
                continue
            # A skipped unit carries no harmlessness verdict; its M verdict is
            # already on the report, so nothing needs backfilling.
            if unit.get("harmlessness") is None:
                continue
            report.update({
                "harmlessness": unit["harmlessness"],
                "behavior": (unit.get("member_behavior") or {}).get(identifier),
                "m": unit["m"],
                "fresh": unit["fresh"],
                "candidate_rows": unit["candidate_rows"],
                "gain_opportunities": unit["gain_opportunities"],
                "measurement_gain_cases": unit["measurement_gain_cases"],
                "coverage_response": unit.get("coverage_response"),
            })

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
            for gate in ("structural", "harmlessness", "behavior", "m"):
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
                        "gain_opportunities": report.get("gain_opportunities"),
                        "behavior_delta": _feedback_delta((report.get("behavior") or {}).get("delta")),
                        "behavior_note": (report.get("behavior") or {}).get("behavior_note"),
                        "not_evaluable": gate_rejection_is_unevaluable(
                            gate=gate,
                            verdict=verdict,
                            report=report,
                            candidate=candidate,
                        ),
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
                reason="STRUCTURAL-HARMLESSNESS-BEHAVIOR-M passed; pre-commit write",
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
                fired = float((unit.get("behavior") or {}).get("fired_rate", 0.0))
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
            "instrument": self._instrument_report(
                baseline_gain=baseline_gain,
                gate_reports=gate_reports,
                admitted_ids={
                    str(c.get("intervention_id") or "") for c in selected
                },
            ),
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
                arms.run(self.split.gain, registry=self.registry.registry,
                         fresh=requires_fresh_evaluation(self.registry.entries)),
            ),
            "ledger_integrity": self.ledger.verify(),
        }
        # H_t bookkeeping. The committed registry write above is what decides
        # admission; the manifest, the gap backlog and the experience stores
        # only describe and remember the generation that resulted.
        cycle_rejections = [
            item for item in self.rejection_feedback
            if int(item.get("cycle") or -1) == int(cycle)
        ]
        report["capability_backlog"] = self.record_capability_gaps(
            cycle, capability_gaps,
        )
        self._record_cycle_experience(
            cycle=cycle,
            diagnosed_families=diagnosed_families,
            gate_reports=gate_reports,
            rejected=cycle_rejections,
            selected=selected,
        )
        report["harness"] = self.record_harness(cycle)
        self._save_cycle_report(cycle, report)
        self._ledger("cycle_completed", {"cycle": cycle, "state": "completed",
                                        "active_registry_hash": self.registry.registry_hash})
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
        max_admits = int(
            (self.config.get("budget") or {}).get("max_admits", 2)
        )
        if max_admits <= 0:
            return {
                "unit_ids": [],
                "entries": [],
                "delta": None,
                "reason": "admission budget is zero",
            }
        if len(qualified) == 1:
            unit = qualified[0]
            unit_entries = [*unit["partners"], unit["candidate"]]
            if len(unit_entries) > max_admits:
                return {
                    "unit_ids": [],
                    "entries": [],
                    "delta": None,
                    "reason": (
                        f"qualified unit needs {len(unit_entries)} admission "
                        f"slots; limit is {max_admits}"
                    ),
                }
            return {
                "unit_ids": [unit["intervention_id"]],
                "entries": unit_entries,
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
            unit_entries = [*unit["partners"], unit["candidate"]]
            if len(unit_entries) > max_admits:
                return {
                    "unit_ids": [],
                    "entries": [],
                    "delta": None,
                    "reason": (
                        f"joint unit needs {len(unit_entries)} admission slots; "
                        f"limit is {max_admits}"
                    ),
                }
            return {
                "unit_ids": [unit["intervention_id"]],
                "entries": unit_entries,
                "delta": None,
                "reason": "qualified units collapse into one joint group",
            }
        selected_units: list[Mapping[str, Any]] = []
        selected_entry_count = 0
        for unit in units:
            size = len([*unit["partners"], unit["candidate"]])
            if selected_entry_count + size > max_admits:
                continue
            selected_units.append(unit)
            selected_entry_count += size
        units = selected_units
        if not units:
            return {
                "unit_ids": [],
                "entries": [],
                "delta": None,
                "reason": "no qualified unit fits the admission entry budget",
            }
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
        combo_harmlessness = harmlessness_gate(
            baseline_rows=_subset_rows(control, self.split.sentinel),
            candidate_rows=_subset_rows(combo_rows, self.split.sentinel),
            config=self.config,
        )
        control_g = _subset_rows(control, self.split.gain)
        combo_g = _subset_rows(combo_rows, self.split.gain)
        delta = paired_delta(control_g, combo_g)
        passed_m, failures = meets_m(delta, config=self.config)
        passed = combo_harmlessness["passed"] and passed_m
        if not combo_harmlessness["passed"]:
            failures = [combo_harmlessness["reason"], *failures]
        unit_ids = [unit["intervention_id"] for unit in units]
        if passed:
            return {
                "unit_ids": unit_ids,
                "entries": entries,
                "delta": delta,
                "harmlessness": combo_harmlessness,
                "reason": "combination arm met HARMLESSNESS and M",
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
            "harmlessness": combo_harmlessness,
            "reason": (
                "combination arm failed HARMLESSNESS/M ("
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
        maximum = int(
            (self.config.get("budget") or {}).get("max_proposals", 3)
        )
        if len(candidates) > maximum:
            raise ValueError(
                f"proposal batch has {len(candidates)} candidates; limit is {maximum}"
            )
        ids = [str(item.get("intervention_id") or "") for item in candidates]
        if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
            raise ValueError("proposal batch must contain unique non-empty ids")
        from detector.interventions import validate_intervention_batch

        try:
            validate_intervention_batch(
                candidates, require_executable_bounds=True,
            )
        except ValueError as exc:
            raise ValueError(f"proposal batch contract rejection: {exc}") from exc
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
        inferred = {tuple(group) for group in infer_joint_groups(candidates)}
        declared = {
            tuple(str(item) for item in group) for group in batch.joint_groups
        }
        paired_evidence = {group[0] for group in inferred}
        for candidate in candidates:
            identifier = str(candidate.get("intervention_id") or "")
            if (
                candidate.get("type") == "evidence_check"
                and identifier not in paired_evidence
            ):
                raise ValueError(
                    f"proposal {identifier} is standalone evidence without a "
                    "terminal-writing partner"
                )
        missing_groups = inferred - declared
        if missing_groups:
            raise ValueError(
                "causal evidence/guard groups must be declared jointly: "
                f"{sorted(missing_groups)}"
            )
        extra_groups = declared - inferred
        if extra_groups:
            raise ValueError(
                "joint groups need a same-family evidence fact consumed by "
                f"their guard: {sorted(extra_groups)}"
            )

    # ------------------------------------------------------------------
    def run_experiment(self, cycles: int | None = None, *, resume: bool = False) -> dict[str, Any]:
        import time as _time

        cycles = int(self.config.get("cycles", 3) if cycles is None else cycles)
        if cycles < 0:
            raise ValueError("cycles must be non-negative")
        for cycle in range(1, cycles + 1):
            started = any(r["event"] == "cycle_started" and r["payload"].get("cycle") == cycle for r in self.ledger.read())
            self.run_cycle(cycle, resume=resume and started)
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
        # The validation set is disjoint from the evolution pool, but
        # ``gold_map`` is built from the pool only, so its gold must be added
        # BEFORE the arm runs. Filling it afterwards left every validation row
        # with ``gold=None`` and made this effect check crash on
        # ``paired_delta`` -- i.e. the only out-of-sample read never ran.
        self.gold_map.update({
            case: load_gold(self.config["dataset_root"], case)
            for case in self.split.validation
        })
        missing_gold = [
            case for case in self.split.validation
            if self.gold_map.get(case) is None
        ]
        if missing_gold:
            raise RuntimeError(
                "mini effect requires boolean gold for every validation case; "
                f"missing: {missing_gold[:5]}"
            )
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
        if not bool(
            (self.config.get("final_evaluation") or {}).get("enabled", False)
        ):
            raise RuntimeError(
                "final evaluation is disabled by default because manifests contain inline "
                "labels; use full-self-evolution or a sealed-label scorer"
            )
        if self.split.final_holdout is None:
            raise RuntimeError("mini split has no read-once final verification set")
        allow_empty = bool(
            (self.config.get("experiment") or {}).get("allow_empty_final", False)
        )
        if not self.registry.entries and not allow_empty:
            raise RuntimeError(
                "final verification requires a non-empty active registry"
            )
        final_cases = self.split.final_holdout.read("final_verification")
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
        # Deterministic registries replay one empty baseline exactly. A
        # provider-stage registry instead runs both arms fresh.
        active_fresh = requires_fresh_evaluation(self.registry.entries)
        if not self.registry.entries:
            empty_rows = self.backend.evaluate(
                case_ids,
                dataset_root=self.config["dataset_root"],
                registry=empty,
                gold_map=final_gold,
                workers=workers,
            )
            active_rows = [dict(row) for row in empty_rows]
        elif active_fresh:
            # Provider-stage interventions cannot use frozen replay. Both arms
            # must be fresh in the final contrast; otherwise provider variance
            # is attributed to the registry.
            empty_rows = self.backend.evaluate_fresh(
                case_ids,
                dataset_root=self.config["dataset_root"],
                registry=empty,
                gold_map=final_gold,
                workers=workers,
            )
            active_rows = self.backend.evaluate_fresh(
                case_ids,
                dataset_root=self.config["dataset_root"],
                registry=active,
                gold_map=final_gold,
                workers=workers,
            )
        else:
            empty_rows = self.backend.evaluate(
                case_ids,
                dataset_root=self.config["dataset_root"],
                registry=empty,
                gold_map=final_gold,
                workers=workers,
            )
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
            and int(effect["new_errors"]) == 0
            and int(effect["correctness_worsened"]) == 0
            and int(effect["unresolved_added"]) == 0
            and int(effect["health_regressed"]) == 0
        )
        p_value = _mcnemar_improvement_p(effect)
        step_check = _step_localization_check(
            self.config["dataset_root"], empty_rows, active_rows,
        )
        main_pass = main_pass and bool(step_check["not_regressed"])
        report = {
            "schema_version": "evolution.final_verification.v1",
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
                "zero_new_errors": int(effect["new_errors"]) == 0,
                "zero_correctness_worsened": int(
                    effect["correctness_worsened"]
                ) == 0,
                "health_not_regressed": int(effect["health_regressed"]) == 0,
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
        self.split.final_holdout.complete("final_verification")
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


def run_full_self_evolution(
    *,
    config: Mapping[str, Any] | None = None,
    backend: DetectionBackend | None = None,
) -> dict[str, Any]:
    """Run evolution on a declared partition and evaluate its own registry."""
    cfg = dict(load_config()) if config is None else dict(config)
    experiment = cfg.get("experiment") or {}
    if str(experiment.get("mode") or "") != "full_self_evolution":
        raise RuntimeError(
            "full self-evolution requires experiment.mode=full_self_evolution"
        )
    engine = EvolutionEngine(cfg, backend=backend, mini=False)
    final_ids = {
        str(item.get("case_id") or "") for item in engine.split.final
    }
    evolution_ids = set(engine.split.pool)
    raw_ids = {
        path.relative_to(Path(cfg["dataset_root"])).as_posix()
        for path in Path(cfg["dataset_root"]).rglob("*.json")
    }
    expected_evolution = int(experiment.get("expected_evolution_cases", len(evolution_ids)))
    expected_evaluation = int(experiment.get("expected_evaluation_cases", len(final_ids)))
    expected_total = int(experiment.get("expected_dataset_cases", len(raw_ids)))
    if (
        len(evolution_ids) != expected_evolution
        or len(final_ids) != expected_evaluation
        or len(raw_ids) != expected_total
        or evolution_ids & final_ids
        or evolution_ids | final_ids != raw_ids
    ):
        raise RuntimeError(
            "full self-evolution split must partition all raw cases exactly: "
            f"evolution={len(evolution_ids)}, evaluation={len(final_ids)}, "
            f"raw={len(raw_ids)}, overlap={len(evolution_ids & final_ids)}"
        )
    reports = engine.ledger.read()
    if reports and not any(
        record.get("event") == "full_self_evolution_started"
        for record in reports
    ):
        raise RuntimeError(
            "full self-evolution work root contains a foreign ledger"
        )
    completed = {
        int((record.get("payload") or {}).get("cycle") or 0)
        for record in reports
        if record.get("event") == "cycle_completed"
        and (record.get("payload") or {}).get("state") == "completed"
    }
    started = {
        int((record.get("payload") or {}).get("cycle") or 0)
        for record in reports
        if record.get("event") == "cycle_started"
    }
    interrupted = sorted(started - completed)
    # An interrupted cycle may be resumed ONLY from its own durable
    # checkpoint, which re-validates code/config/data/split/registry identity
    # and refuses a fresh proposal draw. Without a checkpoint the fixed S/G
    # search cannot be reconstructed, so the run must start over in a new work
    # root instead of silently re-searching the same cases.
    resumable: set[int] = set()
    for cycle in interrupted:
        checkpoint = (
            engine.paths["root"] / "checkpoints" / f"cycle{cycle:03d}"
            / "checkpoint.json"
        )
        if not checkpoint.exists():
            raise RuntimeError(
                "an evolution cycle was interrupted with no resumable "
                "checkpoint; fixed S/G cannot be searched again: "
                f"{interrupted}"
            )
        resumable.add(cycle)
    if not reports:
        if engine.registry.entries or int(engine.registry.state.get("cycle") or 0):
            raise RuntimeError(
                "full self-evolution must start from an empty registry"
            )
        engine._ledger("full_self_evolution_started", {
            "evolution_cases": len(evolution_ids),
            "evaluation_cases": len(final_ids),
            "initial_registry_hash": engine.registry.registry_hash,
            "source_digest": source_digest(),
            "detector_config_digest": file_digest(cfg.get("detector_config")),
        })
    cycle_reports = []
    for path in sorted(engine.paths["cycles"].glob("cycle*.json")):
        cycle_reports.append(json.loads(path.read_text(encoding="utf-8-sig")))
    target_cycles = int(cfg.get("cycles", 3))
    for cycle in range(1, target_cycles + 1):
        if cycle in completed:
            continue
        report = engine.run_cycle(cycle, resume=cycle in resumable)
        cycle_reports.append(report)
        if report.get("state") != "completed":
            raise RuntimeError(
                f"full self-evolution cycle {cycle} did not complete: "
                f"{report.get('state')}"
            )
    ledger_status = engine.ledger.verify()
    if not ledger_status["valid"]:
        raise RuntimeError(
            f"full self-evolution ledger is invalid: {ledger_status['reason']}"
        )
    final_report = engine.run_final()
    meta_report = write_meta_report(
        cycle_reports,
        engine.ledger,
        engine.paths["reports"] / "meta_eval.json",
    )
    nonempty = bool(engine.registry.entries)
    overall = (
        "pass"
        if nonempty and final_report["verdict"]["overall"] == "pass"
        and bool(meta_report["defect_injection_audit"]["passed"])
        and bool(ledger_status["valid"])
        else "fail"
    )
    report = {
        "schema_version": "evolution.full_self_evolution.v1",
        "experiment_scope": "disjoint_evolution_and_sealed_holdout",
        "partition_sizes": {
            "evolution": len(evolution_ids), "evaluation": len(final_ids),
            "dataset": len(raw_ids),
        },
        "historical_contamination_ignored": bool(
            experiment.get("ignore_historical_contamination", False)
        ),
        "initial_registry": "empty",
        "cycles": cycle_reports,
        "active_interventions": [
            str(item.get("intervention_id") or "")
            for item in engine.registry.entries
        ],
        "registry_hash": engine.registry.registry_hash,
        "final": final_report,
        "meta": meta_report,
        "ledger_integrity": ledger_status,
        "overall": overall,
    }
    save_json(engine.paths["reports"] / "full_self_evolution.json", report)
    return report


__all__ = ["EvolutionEngine", "run_experiment", "run_full_self_evolution"]
