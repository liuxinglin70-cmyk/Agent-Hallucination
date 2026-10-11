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

from detector.replay import requires_fresh_evaluation

import json
from contextvars import Context
from functools import wraps
from threading import RLock
from weakref import finalize
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution.backends import (
    CACHE_FORMAT,
    DetectionBackend,
    LiveDetectorBackend,
    backend_runtime_identity,
    make_backend,
)
from evolution.config import ensure_dirs, load_config, resolve_path
from evolution.route_observations import build_route_measurement, measurement_for_candidate
from detector.runtime import exclusive_writer
from evolution.contracts import (
    empty_registry,
    make_registry,
    redact_case_ids,
    save_json,
)
from detector.identity import digest, source_identity, artifact_digest
from detector.dataset import gold_fields
from evolution.gates import (
    arm_has_infrastructure_failure,
    candidate_target_kind,
    defect_candidates,
    localization_floor_rests_on_unstable,
    marginal_floor_rests_on_unstable,
    meets_localization,
    meets_m,
    structural_gate,
    harmlessness_gate,
    localization_behavior_gate,
    sentinel_coverage,
    behavior_gate,
)
from evolution.ledger import Ledger, load_ledger
from evolution.meta_eval import write_meta_report
from evolution.metrics import (
    STEP_KINDS as METRIC_STEP_KINDS,
    STEP_KIND_UNCLASSIFIED,
    NOISE_FLOOR_MEASURED,
    fired_rate,
    flipped_cases,
    gain_opportunity,
    localization_opportunity,
    mechanism_opportunity,
    mcnemar_improvement_p,
    noise_floor,
    paired_delta,
    paired_localization_delta,
    row_responsible_step,
    row_responsible_step_kind,
    source_deltas,
    target_outcome,
    target_opportunity,
)
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
    load_episodes,
    record_episode,
    record_procedure,
    recall as recall_experience,
    write_recall,
)
from evolution.development import DevelopmentUnmeasurable
from evolution.development_adapter import make_development_drafter, feedback_delta
from evolution.cycle_preparation import prepare_patrol, prepare_families
from evolution.mechanisms import mechanism_prompt_view, repair_requirements
from evolution.archive import retrieve_components
from evolution.route_failures import (
    ROUTE_FAILURE_NAME,
    insufficiency_evidence,
    load_route_failures,
    record_route_failures,
    route_of,
    route_failure_summary,
)
from evolution.splits import (
    EvolutionSplit,
    build_split,
    bool_gold,
    load_gold,
    load_gold_step,
    load_record,
    split_manifest_report,
)
from evolution.worksheet import (
    mechanism_report,
    stable_failure_families,
    stable_localization_families,
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


def gate_rejection_is_unevaluable(
    *,
    gate: str,
    verdict: Mapping[str, Any],
    report: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    """Whether a failing gate failed for want of a measurement.

    Missing measurement capacity is distinct from an observed regression,
    including insufficient protected sentinel coverage.
    """
    # An arm that could not be measured honestly makes its own regressions:
    # fail-closed rows read as lost true positives. The marker is set by the
    # unit that saw those rows, and it wins over the manufactured deltas --
    # otherwise "the provider died" is stored as "the mechanism is falsified".
    if verdict.get("infrastructure_failure"):
        return True
    # The same argument for the instrument's own jitter: a rejection whose
    # entire harm, or whose entire recorded benefit, sits on cases the control
    # configuration read two ways is not a verdict about the candidate. It must
    # be checked before the delta counts below, for the same reason the
    # infrastructure marker is -- those counts are exactly what the marker
    # exists to override.
    if verdict.get("stability_limited") or verdict.get("harm_entirely_on_unstable"):
        return True
    delta = verdict.get("delta") or {}
    if delta.get("new_errors") or delta.get("health_regressed") or delta.get("unresolved_added"):
        return False
    if verdict.get("evaluable") is False:
        return True
    if gate not in {"behavior", "m"}:
        return False
    # An M verdict that reports itself as unmeasured, or as skipped because no
    # evaluation set had matching coverage, carries no substantive rejection.
    status = str(verdict.get("status") or "")
    if gate == "m" and status in {"not_evaluable", "skipped_unmeasured"}:
        delta = verdict.get("delta") or {}
        return not (delta.get("new_errors") or delta.get("health_regressed"))
    polarity = str((candidate.get("claim") or {}).get("target_polarity") or "")
    opportunity = target_opportunity(
        target_kind=candidate_target_kind(candidate), polarity=polarity,
        detection=report.get("gain_opportunities") or {},
        localization=report.get("localization_opportunities"),
    )
    if opportunity != 0:
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


def evaluation_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """Separate measurement, mechanism evidence and observed utility.

    This is diagnostic state derived from the same formal gate rows. It does
    not grant admission or reinterpret a failed claim as a successful one.
    """
    candidate = report.get("candidate") or {}
    structural = report.get("structural") or {}
    harmlessness = report.get("harmlessness") or {}
    behavior = report.get("behavior") or {}
    marginal = report.get("m") or {}
    delta = behavior.get("delta") or marginal.get("delta") or {}
    mechanism_opportunity = report.get("mechanism_opportunity")
    polarity = str((candidate.get("claim") or {}).get("target_polarity") or "")
    unmeasured = list(report.get("unmeasured_cases") or ())
    coverage_missing = (
        isinstance(mechanism_opportunity, Mapping)
        and mechanism_opportunity.get("evaluable") is False
    )
    no_measurement = bool(unmeasured) or any(
        verdict.get("infrastructure_failure") or verdict.get("stability_limited")
        for verdict in (harmlessness, behavior, marginal)
    )
    if structural.get("passed") is False:
        state, next_action = "invalid_candidate", "revise_contract"
    elif no_measurement:
        state, next_action = "measurement_pending", "retest_frozen_candidate"
    elif coverage_missing or (
        not report.get("candidate_rows") and report.get("coverage_response")
    ):
        state, next_action = "coverage_missing", "add_mechanism_matched_validation"
    elif (
        polarity == "recall"
        and behavior.get("passed") is False
        and float(behavior.get("fired_rate") or 0.0) > 0.0
        and int(behavior.get("behavior_events") or 0) > 0
        and int((delta.get("flips") or {}).get("T->F") or 0) > 0
        and not int(delta.get("new_errors") or 0)
        and not int(delta.get("health_regressed") or 0)
    ):
        state, next_action = "secondary_hypothesis", "validate_secondary_benefit"
    elif behavior.get("passed") and marginal.get("passed") and harmlessness.get("passed"):
        state, next_action = "qualified", "consider_admission"
    else:
        state, next_action = "claim_unconfirmed", "revise_or_investigate"
    measurable = state not in {"measurement_pending", "coverage_missing"} and bool(
        report.get("candidate_rows")
    )
    utility = None
    if measurable and delta:
        flips = delta.get("flips") or {}
        localization = marginal.get("localization_delta") or {}
        utility = {
            **(feedback_delta(delta) or {}),
            "tp_gain": int(flips.get("F->T") or 0),
            "fp_removed": int(flips.get("T->F") or 0),
            "tp_lost": int(delta.get("tp_lost") or 0),
            "fp_added": int(delta.get("fp_added") or 0),
            "joint_improved": int(localization.get("joint_improved") or 0),
            "joint_broken": int(localization.get("joint_broken") or 0),
        }
    return {
        "state": state,
        "measurable": measurable,
        "mechanism_supported": (
            None if not measurable else
            False if behavior.get("passed") is False else
            True if mechanism_opportunity is not None and behavior.get("passed") else
            None
        ),
        "protected": (
            bool(harmlessness.get("passed")) if measurable else None
        ),
        "utility_delta": utility,
        "next_action": next_action,
    }


COVERAGE_POLICY_MODES = ("strict", "replacement", "replacement_or_skip")


def _unmeasurable_cases(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    """Cases whose rows could not be measured honestly, from either arm."""
    out: set[str] = set()
    for row in rows:
        if arm_has_infrastructure_failure([row]):
            out.add(str(row.get("case") or ""))
    return out


def _defect_rejection_after_exclusions(
    *,
    unit: Mapping[str, Any],
    baseline_rows: Sequence[Mapping[str, Any]],
    sentinel_cases: Sequence[str],
    gain_cases: Sequence[str],
    config: Mapping[str, Any],
) -> str:
    """Whether a defect rejection survives dropping the unmeasurable rows.

    Returns an empty string when the rejection is sound (the audit may certify)
    and a reason when it is not (the audit is inconclusive and must freeze).

    The defect-injection audit exists to prove the gate *logic* rejects a known
    bad candidate. A rejection produced by a row nobody could measure proves
    nothing, so such rows are removed from both arms and the rejection is
    re-derived on the rest. If it still fails there, the gates were exercised;
    if the remaining cases are too few to score, the audit stays inconclusive
    instead of certifying on an empty comparison.

    Only HARMLESSNESS and M are re-derived. BEHAVIOR needs per-row deployment
    traces, which the audit's row summary does not carry, so re-running it here
    would report "never fired" for every defect and manufacture a rejection --
    the exact artifact this function exists to catch.
    """
    candidate_rows = list(unit.get("candidate_rows") or ())
    unusable = _unmeasurable_cases([*baseline_rows, *candidate_rows])
    if not unusable:
        # Nothing was excluded, so the verdict already rests on measurable
        # rows only.
        return ""
    healthy_baseline = [
        row for row in baseline_rows
        if str(row.get("case") or "") not in unusable
    ]
    healthy_candidate = [
        row for row in candidate_rows
        if str(row.get("case") or "") not in unusable
    ]
    if not healthy_baseline or not healthy_candidate:
        return (
            "defect evaluation has no measurable case left after excluding "
            f"{len(unusable)} unmeasured case(s)"
        )
    sentinel = list(sentinel_cases)
    gain_cases = list(gain_cases)
    # The unit's rows cover sentinel then gain; split them the same way the
    # unit itself did so HARMLESSNESS sees the sentinel subset and M the gain
    # subset. Without the split the whole batch would be treated as the gain
    # set, which is the stricter reading for M and skips HARMLESSNESS.
    if sentinel:
        baseline_sentinel = _subset_rows(healthy_baseline, sentinel)
        candidate_sentinel = _subset_rows(healthy_candidate, sentinel)
    else:
        baseline_sentinel = []
        candidate_sentinel = []
    if gain_cases:
        baseline_gain = _subset_rows(healthy_baseline, gain_cases)
        candidate_gain = _subset_rows(healthy_candidate, gain_cases)
    else:
        baseline_gain = list(healthy_baseline)
        candidate_gain = list(healthy_candidate)

    if baseline_sentinel and candidate_sentinel:
        harmlessness = harmlessness_gate(
            baseline_rows=baseline_sentinel,
            candidate_rows=candidate_sentinel,
            config=config,
        )
        if harmlessness.get("passed") is False and harmlessness.get("evaluable") is False:
            return (
                "defect evaluation has insufficient measurement coverage after "
                f"excluding {len(unusable)} unmeasured case(s)"
            )
        if harmlessness.get("passed") is False:
            return ""

    # The benefit-set comparison must be non-empty on both arms. `meets_m`
    # reports marginal_cases=0 < 1 on an empty pairing, which reads exactly
    # like "the gate rejected the defect" -- so an outage that fail-closed the
    # whole gain set would certify the audit without exercising anything. The
    # whole-batch emptiness check above cannot catch it: it runs before the
    # sentinel/gain split, and here the exclusions can land entirely in gain.
    if baseline_gain and candidate_gain:
        delta = paired_delta(list(baseline_gain), list(candidate_gain))
        passed, _failures = meets_m(delta, config=config)
    elif gain_cases:
        return (
            "defect evaluation has no measurable gain case left after "
            f"excluding {len(unusable)} unmeasured case(s)"
        )
    else:
        passed = False
    if passed:
        return (
            "the defect's rejection does not survive excluding "
            f"{len(unusable)} unmeasured case(s): it passes M on the "
            "measurable cases"
        )
    return ""


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
        # Only authoritative rows may seed it: a fail-closed row carries an
        # empty envelope, and seeding it makes the frozen replay of that case
        # raise for every later deployment arm in the cycle, which resurfaces
        # as an unexplained "unresolved" row far from its cause.
        if not (registry or {}).get("interventions") and not fresh:
            backend = self.engine.backend
            while hasattr(backend, "backend"):
                backend = backend.backend
            if hasattr(backend, "_base_results"):
                backend._base_results.update({
                    r["case"]: r["result"] for r in rows
                    if arm_has_infrastructure_failure([r]) == ""
                })
        return rows


def _engine_operation(method):
    @wraps(method)
    def execute(self, *args, **kwargs):
        with self._operation_lock:
            self._require_open()
            self._operation_depth += 1
            try:
                return method(self, *args, **kwargs)
            finally:
                self._operation_depth -= 1
                self._release_writer_if_closed()
    return execute


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
        self._operation_lock = RLock()
        self._closed = False
        self._operation_depth = 0
        self.config = dict(load_config()) if config is None else dict(config)
        self.paths = ensure_dirs(self.config)
        self.mini = mini
        self._writer_release: Any | None = None
        if (self.config.get("runtime") or {}).get("exclusive_work_root", True):
            # Engine ownership cannot inherit another engine's ambient lease.
            context = Context()
            lease = exclusive_writer(self.paths["root"])
            context.run(lease.__enter__)
            self._writer_release = finalize(self, context.run, lease.__exit__, None, None, None)
        try:
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
            # Evaluator-side localization annotation. It rides beside ``gold_map``
            # and never reaches the detector: the backend copies it onto result
            # rows for the evolution side to score. Without it every patrol row
            # carries ``gold_step=None``, and a case the detector convicts
            # correctly but localizes wrongly stays invisible to the development
            # loop -- which is the objective plan §5.1 asks to bring into the loop.
            self.gold_step_map = {
                case: load_gold_step(self.config["dataset_root"], case)
                for case in self.split.pool
            }
            self.cycle_reports: list[dict[str, Any]] = []
            # H_t = (G, C, T, M, R) base. These describe and remember the
            # generation that ran; they are read for reporting and for what the
            # next round starts from, and they never score a candidate.
            self.harness_dir = Path(self.config["harness_dir"])
            self.memory_root = Path(self.config["memory_root"])
            # Reviewed overlays may correct an older rejection without rewriting
            # either its original ledger event or its original episode.
            for episode in load_episodes(self.memory_root):
                if not episode.get("correction_evidence"):
                    continue
                for feedback in self.rejection_feedback:
                    candidate = feedback.get("candidate") or {}
                    if (feedback.get("cycle") == episode.get("cycle")
                        and feedback.get("intervention_id") == episode.get("intervention_id")
                        and feedback.get("gate") == episode.get("gate")
                        and feedback.get("reason") == episode.get("original_reason")
                        and (candidate.get("provenance") or {}).get("failure_family") == episode.get("failure_family")):
                        feedback.update(reason=episode["reason"], not_evaluable=episode["unevaluable"],
                                        correction_evidence=episode["correction_evidence"])
            self.gap_store = self.harness_dir / GAP_STORE_NAME
            self.route_failure_store = self.harness_dir / ROUTE_FAILURE_NAME
            # Optional mechanism-level scoring binding. Absent by default, which is
            # what keeps every existing cycle's semantics unchanged.
            self.mechanism_binding: dict[str, Any] = {}
            self.mechanism_membership: dict[str, Any] = {}
            self._mechanism_binding_error = ""
            self._load_mechanism_binding()
            # Optional, explicitly installed replacement evaluation set.
            self._replacement_gain_cases: list[str] = []
            self._previous_manifest = self._load_previous_manifest()
            # §28.6: where the candidate archive is written, and whether this
            # engine writes it at all. ``on`` is a declared choice rather than an
            # implicit one -- see the ``archive`` block in ``config.DEFAULTS`` for
            # what each value claims -- and ``off`` is the default so that adding
            # the wiring cannot change what an existing run records.
            # Whether this cycle runs the development loop before its gates, and
            # how many mechanisms it may develop if so (§23.3.6). Read once at
            # construction: a cycle's path is a property of its configuration, not
            # something a later stage may flip.
            development_cfg = self.config.get("development") or {}
            self.development_enabled = bool(development_cfg.get("enabled", False))
            self.development_max_mechanisms = int(
                development_cfg.get("max_mechanisms_per_cycle", 1)
            )
            archive_cfg = self.config.get("archive") or {}
            self.archive_on = str(archive_cfg.get("on") or "off")
            if self.archive_on not in {"off", "development_end", "cycle_end"}:
                raise ValueError(
                    f"archive.on {self.archive_on!r} is not one of "
                    "off, development_end, cycle_end"
                )
            self.archive_root = Path(
                str(archive_cfg.get("root") or "").strip()
                or str(Path(self.config["work_root"]) / "candidates")
            )
            # Layer 2 of §9.1: reusable parts. Separate from the registry by
            # construction -- nothing this store holds has passed a promotion gate.
            #
            # This path is where components are both *written* (see
            # ``archive_development_run``) and *read* (``_reusable_components``).
            # The two used to disagree: the archive registered components under
            # ``archive.root`` while retrieval read this attribute, so with the
            # default configuration no component a run produced could ever reach a
            # drafting prompt -- the archive reported a row and the prompt saw an
            # empty store. One path now, named here and used by both sides.
            self.component_store = Path(
                (self.config.get("component_root") or "").strip()
                or str(Path(self.config["work_root"]) / "components")
            )
            # The two stores normally live in two directories -- ``work_root/
            # components`` for parts, ``work_root/candidates`` for candidate
            # history, so a run's components are *not* under its archive root. That
            # asymmetry is exactly what made the archive look empty: reading
            # ``load_components(archive_root)`` finds nothing while the run did
            # register a part. It is stated here rather than left to be discovered
            # by an empty prompt, and it is silent only when the two paths really
            # are the same directory.
            self.component_store_warning = (
                ""
                if self.component_store == self.archive_root
                else (
                    f"components are written to and read from "
                    f"{str(self.component_store)}, not from archive.root "
                    f"{str(self.archive_root)}, which holds candidate records "
                    "only; configure component_root to place the component store "
                    "somewhere else"
                )
            )
            self.component_prompt_limit = int(
                (self.config.get("proposer") or {}).get("max_components", 4)
            )
        except BaseException:
            self.close()
            raise

    # ------------------------------------------------------------------
    def _load_mechanism_binding(self) -> None:
        """Read the optional mechanism binding declared by configuration.

        Both files or neither: a mechanism without membership cannot be scored
        (membership is the evidence a case belongs to the mechanism), and
        membership without a mechanism names no target.
        """
        mechanism_path = self.config.get("mechanism")
        membership_path = self.config.get("mechanism_membership")
        if not mechanism_path and not membership_path:
            return
        if not (mechanism_path and membership_path):
            self._mechanism_binding_error = (
                "a mechanism binding needs both 'mechanism' and "
                "'mechanism_membership'"
            )
            return
        try:
            self.mechanism_binding = dict(json.loads(
                resolve_path(str(mechanism_path)).read_text(encoding="utf-8-sig")
            ))
            self.mechanism_membership = dict(json.loads(
                resolve_path(str(membership_path)).read_text(encoding="utf-8-sig")
            ))
        except (OSError, ValueError) as exc:
            self._mechanism_binding_error = f"{type(exc).__name__}: {exc}"

    def _cycle_seconds(self) -> float | None:
        raw = (self.config.get("budget") or {}).get("cycle_timeout_seconds")
        return float(raw) if raw else None

    def _ledger(self, event: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key is not None:
            return self.ledger.append(event, payload, idempotency_key=idempotency_key)
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

    def _reusable_components(
        self, diagnosed_families: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Bounded reusable parts for the mechanisms under proposal this round.

        One retrieval per mechanism, then a global cap, so a store that grows
        over a long run cannot grow the drafting prompt with it. A component is
        offered by mechanism first and by capability second: a part that reads
        a typed field is reusable by any mechanism whose repair needs that
        observation, not only the one that first needed it.
        """
        if not self.component_store.is_dir() and not (
            self.component_store / "components.jsonl"
        ).exists():
            return []
        selected: dict[str, dict[str, Any]] = {}
        for family in diagnosed_families:
            mechanism = family.get("mechanism") or {}
            if not isinstance(mechanism, Mapping):
                continue
            # The capability a component is matched on is the one the *repair*
            # needs (``control_flow`` / ``guard`` / ``observation`` /
            # ``prompt``), not the actionability verdict. The two are different
            # vocabularies that happen to share a word: ``actionable`` says
            # whether this framework can act, while the repair requirement says
            # what kind of action is needed. Matching on the former would find
            # nothing, because no component is registered under it.
            required = []
            try:
                required = list(
                    repair_requirements(mechanism).get("required_capabilities")
                    or ()
                )
            except (ValueError, KeyError):
                required = []
            for capability in required or [""]:
                retrieved = retrieve_components(
                    self.component_store,
                    mechanism_id=str(mechanism.get("mechanism_id") or ""),
                    capability=str(capability),
                    limit=3,
                )
                for record in retrieved.get("components") or ():
                    digest = str(record.get("digest") or "")
                    kind = str(record.get("kind") or "")
                    if digest:
                        selected.setdefault(f"{kind}:{digest}", record)
        return list(selected.values())[: self.component_prompt_limit]

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

    @_engine_operation
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

        ``strict`` never substitutes a gain set. Missing coverage stops drafting
        or evaluation with admission blocked. Alternatives must be configured
        explicitly and have measured baseline repair opportunities.
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

        Returns ``None`` under the strict policy (no replacement). Otherwise
        it reports whether a replacement
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
        if localization_opportunity(rows, gold_steps=self.gold_step_map)["improvable"] > 0:
            available.append("localization")
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
                "no replacement evaluation set configured; the claim cannot "
                "be measured on the default gain set"
            ),
        }

    def _evidence_report(self) -> dict[str, Any]:
        """Whether this cycle's runs carried external evidence, and which.

        Evidence is a harness input, not a learned intervention: a run that
        depended on it must be readable as such, and a cycle with no
        evidence must not be confused with one that had it.
        """
        provider = getattr(self.backend, "evidence_provider", None)
        block = self.config.get("evidence")
        if not isinstance(block, Mapping) or not block:
            return {"active": False}
        pins = block.get("cases") if isinstance(block.get("cases"), Mapping) else {}
        return {
            "active": True,
            "corpus": str(block.get("corpus") or "") or None,
            "pinned_cases": sorted(str(key) for key in pins),
            "provider_attached": provider is not None,
        }

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("evolution engine is closed")

    def _release_writer_if_closed(self) -> None:
        if self._closed and self._operation_depth == 0:
            release, self._writer_release = self._writer_release, None
            if release is not None:
                release()

    def close(self) -> None:
        """Reject new operations; release ownership when active scopes finish."""
        with self._operation_lock:
            self._closed = True
            self._release_writer_if_closed()

    def __enter__(self) -> "EvolutionEngine":
        self._operation_lock.acquire()
        try:
            self._require_open()
            self._operation_depth += 1
            return self
        except BaseException:
            self._operation_lock.release()
            raise

    def __exit__(self, *_exc: Any) -> None:
        try:
            self.close()
        finally:
            self._operation_depth -= 1
            try:
                self._release_writer_if_closed()
            finally:
                self._operation_lock.release()

    def _instrument_report(
        self,
        *,
        baseline_gain: Sequence[Mapping[str, Any]],
        gate_reports: Sequence[Mapping[str, Any]],
        admitted_ids: set[str],
        blocked_families: Sequence[Mapping[str, Any]] = (),
        noise: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """What this round's measurement could and could not decide.

        Reported per round because it is a property of the instrument rather
        than of a candidate: which claim directions the gain set can actually
        score, and how each candidate's outcome should be read. A direction
        with zero opportunities is unmeasurable, not failed, and the report
        states that instead of leaving it to be inferred from a rejection
        reason.

        ``noise`` is the round's own jitter reading (``metrics.noise_floor``):
        how many cases the *active configuration* moved when it was rolled
        twice. It is part of the instrument block because it is a property of
        the instrument, and a reader who cannot see it cannot tell a candidate's
        one-case gain from the instrument's own one-case drift. A round that
        took no reroll reports ``not_measured``/``deterministic_replay``
        instead of a zero, which is the difference between "checked and still"
        and "not checked".
        """
        opportunity = gain_opportunity(baseline_gain)
        # "Measured" means the round could actually read a gain decision for
        # the directions it reports. A gain arm that fail-closed to unresolved
        # rows scores zero opportunities for every polarity, which is
        # indistinguishable from a genuinely exhausted repair space unless the
        # unmeasured rows are counted here.
        unmeasured_gain = sorted(_unmeasurable_cases(baseline_gain))
        gain_measured = (
            bool(baseline_gain) and len(unmeasured_gain) < len(list(baseline_gain))
        )
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
        noise_block = dict(noise) if noise is not None else noise_floor()
        unstable_ids = sorted(
            str(key)
            for key, value in classifications.items()
            if value == "not_evaluated"
        )
        return {
            "gain_opportunity": opportunity,
            "gain_measured": gain_measured,
            "unmeasured_gain_cases": unmeasured_gain,
            "blocked_families": list(blocked_families),
            "unmeasurable_polarities": sorted(
                polarity for polarity, value in opportunity.items() if value == 0
            ),
            "replacement_gain_cases": len(self._replacement_gain_cases),
            "coverage_responses": responses,
            "coverage_response_counts": response_counts,
            "terminal_classification": classifications,
            "terminal_counts": counts,
            # How much the instrument moved on its own this round. Reported
            # beside the per-candidate readings because without it a reader
            # cannot tell a candidate's one-case gain from the instrument's own
            # one-case drift -- and a round that took no reroll says so instead
            # of publishing a zero.
            "noise_floor": noise_block,
            "unstable_cases": list(noise_block.get("flipped_cases") or ()),
            "stability_limited_units": unstable_ids,
            # A skip is reported as not_evaluated in the terminal vocabulary;
            # both mean the claim was not measured, so one check covers them.
            "instrument_limited": bool(counts.get("not_evaluated") or blocked_families),
        }

    def _audit_should_be_retried(self, audit: Mapping[str, Any]) -> bool:
        """Whether a stored audit is a measurement failure worth re-running.

        Only a verdict that froze *because rows could not be measured* is
        re-attempted, and only when the journal holds one from an earlier
        attempt of the same cycle. A decided verdict -- passed or a real
        rejection -- is reused as before, so this cannot turn "the gates
        rejected the defect" into "ask again until they pass".
        """
        if not audit.get("frozen") or not audit.get("retryable"):
            return False
        attempts = int((self.config.get("audit") or {}).get("max_attempts", 2))
        return int(audit.get("attempt") or 1) < max(1, attempts)

    def _stop_preflight(
        self, cycle: int, preflight: Mapping[str, Any], *,
        baseline_gain: Sequence[Mapping[str, Any]] | None = None,
        audit: Mapping[str, Any] | None = None,
        patrol: Mapping[str, Any] | None = None,
        provider_stage: bool = False,
        state: str = "blocked_evaluation_preflight",
    ) -> dict[str, Any]:
        instrument = self._instrument_report(
            baseline_gain=baseline_gain or (), gate_reports=(), admitted_ids=set(),
            blocked_families=preflight.get("blocked_families") or (),
            # A round that stops before drafting never takes the second roll,
            # so its floor is unmeasured -- unless nothing in the comparison
            # used a provider stage, in which case every arm is a frozen replay
            # and the zero is real. Reporting the first case as the second is
            # how a stopped round would look still when nobody looked.
            noise=noise_floor(provider_stage=provider_stage),
        )
        if baseline_gain is None:
            instrument.update(gain_measured=False, gain_opportunity=None,
                              unmeasurable_polarities=[])
        instrument["instrument_limited"] = True
        report = {
            "cycle": cycle, "state": state,
            # Neither path ran: the round stopped before drafting, so there is
            # no batch whose route could be described. Named explicitly rather
            # than omitted, so a reader can tell "no path was taken" from "this
            # build has no such field".
            "round_path": "not_reached",
            "development": None,
            "split": split_manifest_report(self.split), "preflight": dict(preflight),
            "audit": dict(audit or {"enabled": bool((self.config.get("audit") or {}).get("enabled")),
                                    "passed": False, "status": "not_evaluated"}),
            "patrol": dict(patrol or {}), "instrument": instrument,
            "evidence": self._evidence_report(),
            "proposals": [], "gates": [],
            "proposal_summary": {"candidate_count": 0, "empty_proposal_reason": preflight["reason"]},
            "marginal_selection": {"selected": [], "library_rejections": []},
            "active_after_cycle": [entry["intervention_id"] for entry in self.registry.entries],
        }
        self._ledger("evaluation_preflight_blocked", {"cycle": cycle, **dict(preflight)})
        report["ledger_integrity"] = self.ledger.verify()
        return report

    def _mechanism_opportunity(
        self, gain_rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """The mechanism-level opportunity reading, or None when unbound.

        Returns None unless this run was given an evaluator binding (a
        mechanism file plus a membership file). An unbound run must keep the
        historical polarity semantics: there is no evaluator-confirmed
        membership for the gain set, and deriving one from identifiers,
        sources or a shared polarity is exactly the inference the mechanism
        work exists to forbid.
        """
        mechanism = getattr(self, "mechanism_binding", None)
        membership = getattr(self, "mechanism_membership", None)
        # A declared binding that cannot be read is not "no binding": the run
        # asked to be scored against a mechanism, so it is reported as not
        # evaluable rather than silently falling back. Checked before the
        # unbound case, because an unreadable file leaves both dicts empty and
        # would otherwise read as a run that never asked to be bound.
        if self._mechanism_binding_error:
            return {
                "schema_version": "evolution.mechanism_opportunity.v1",
                "metric_role": "promotion-authoritative",
                "mechanism_id": str((mechanism or {}).get("mechanism_id") or ""),
                "evaluable": False,
                "reason_code": "mechanism_membership_invalid",
                "reason": self._mechanism_binding_error,
                "repairable_errors": 0,
            }
        if not mechanism or not membership:
            return None
        try:
            reading = mechanism_opportunity(
                list(gain_rows), mechanism, membership,
                gold_steps=self.gold_step_map,
            )
        except ValueError as exc:
            return {
                "schema_version": "evolution.mechanism_opportunity.v1",
                "metric_role": "promotion-authoritative",
                "mechanism_id": str(mechanism.get("mechanism_id") or ""),
                "evaluable": False,
                "reason_code": "mechanism_measurement_unresolved",
                "reason": str(exc),
                "repairable_errors": 0,
            }
        return reading

    def _measurable_families(
        self, families: Sequence[Mapping[str, Any]],
        baseline_gain: Sequence[Mapping[str, Any]], arms: _ArmRunner,
        *, gain_measured: bool = True,
    ) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
        """Split families by whether the gain set can score their direction.

        A localization family is not scored by the detection opportunity: its
        claim is that the *named step* improves on cases the control arm
        already convicts correctly. Measuring it against recall/precision
        counts would report the mechanism unmeasurable for a reason that does
        not apply to it, so it is checked against the localization opportunity
        on the same rows.

        ``gain_measured`` distinguishes "this direction has no repairable
        case" from "the gain arm never produced a decision": both read as zero
        opportunities, and only the first licenses a statement about the
        action space. When the arm could not be measured, every family is
        blocked as unmeasured rather than as opportunity-free.
        """
        opportunity = gain_opportunity(baseline_gain)
        localization = localization_opportunity(
            baseline_gain, gold_steps=self.gold_step_map,
        )
        unmeasured_reason = (
            "the gain arm produced no measurable decision this round, so the "
            "absence of repair opportunities is not a reading about the "
            "action space"
        ) if not gain_measured else ""
        replacement = None
        replacement_localization = None
        ready, blocked = [], []
        for family in families:
            name = str(family.get("failure_family") or "")
            if name.startswith("FAM-loc-"):
                count = int(localization.get("improvable") or 0)
                if (
                    count == 0
                    and self._coverage_mode("localization") != "strict"
                    and self._replacement_gain_cases
                ):
                    if replacement_localization is None:
                        rows = arms.run(
                            self._replacement_gain_cases,
                            registry=self.registry.registry,
                            fresh=requires_fresh_evaluation(self.registry.entries),
                        )
                        replacement_localization = localization_opportunity(
                            rows, gold_steps=self.gold_step_map,
                        )
                    count = int(
                        replacement_localization.get("improvable") or 0
                    )
                if count:
                    ready.append(family)
                else:
                    blocked.append({
                        "failure_family": name,
                        "target_kind": "localization",
                        "polarity": "localization",
                        "status": "not_evaluated",
                        "reason": unmeasured_reason or (
                            "no mislocated-but-correctly-convicted case in gain"
                        ),
                    })
                continue
            polarity = "recall" if name.startswith("FAM-fn-") else (
                "precision" if name.startswith("FAM-fp-") else "boundary")
            count = opportunity.get(polarity, sum(opportunity.values()))
            if count == 0 and self._coverage_mode(polarity) != "strict" and self._replacement_gain_cases:
                if replacement is None:
                    rows = arms.run(self._replacement_gain_cases, registry=self.registry.registry,
                                    fresh=requires_fresh_evaluation(self.registry.entries))
                    replacement = gain_opportunity(rows)
                count = replacement.get(polarity, sum(replacement.values()))
            if count:
                ready.append(family)
            else:
                blocked.append({"failure_family": name, "polarity": polarity,
                                "status": "not_evaluated",
                                "reason": unmeasured_reason or "no repair opportunities in gain"})
        return ready, blocked

    @_engine_operation
    def record_harness(self, cycle: int) -> dict[str, Any]:
        """Write H_t for this cycle and diff it against the previous one.

        Called after a committed registry write, so the manifest describes the
        version the next round will actually load.
        """
        journal = getattr(self, "_journal", None)
        parent = (
            self._previous_manifest.get("digest")
            if self._previous_manifest else None
        )
        manifest = build_manifest(
            config=self.config,
            registry=self.registry.registry,
            memory_root=self.memory_root,
            runtime_identity=backend_runtime_identity(self.backend, cases=self.split.pool),
            cycle=cycle,
            parent_digest=parent,
        )
        diff = harness_diff(self._previous_manifest, manifest)
        write_manifest(
            self.harness_dir / HARNESS_FILENAME,
            manifest=manifest,
            history_path=self.harness_dir / HARNESS_HISTORY_NAME,
        )
        if journal is not None:
            # Persist the parent this cycle built on, so a resumed attempt
            # reconstructs the same manifest instead of recording a second,
            # differently-parented version of the same generation.
            journal.save(harness_parent_digest=parent)
        self._previous_manifest = manifest
        self._ledger("harness", {
            "cycle": cycle,
            "digest": manifest["digest"],
            "parent_digest": manifest.get("parent_digest"),
            "changed_components": diff["changed"],
            "registry_hash": manifest.get("registry_hash"),
        })
        return {"manifest": manifest, "diff": diff}

    @_engine_operation
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

    @_engine_operation
    def route_failure_records(self) -> list[dict[str, Any]]:
        """The route-falsification memory as stored, for a planner or a report."""
        if not self.route_failure_store.exists():
            return []
        return load_route_failures(self.route_failure_store)

    @_engine_operation
    def develop_rows(
        self,
        cases: Sequence[str],
        *,
        candidate: Mapping[str, Any],
        split: EvolutionSplit | None = None,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Paired rows for one development check: baseline and candidate arms.

        L1 measures the same case set twice -- once with the deployed version
        and once with the candidate added -- and hands both back tagged, so the
        development loop scores the pair without touching the gates. The
        candidate arm's registry is the active one plus this candidate, which is
        what makes the reading a statement about *this* change.

        ``split`` is the data-separation check, not a source of cases: the
        development loop may measure a candidate on patrol findings, never on
        the sentinel or gain sets -- and never on the validation set, which
        under the mini protocol is the only out-of-sample reading the run has.
        Spending a formal evaluation case during development turns it into
        development exposure, and the promotion protocol's candidate-unused
        rule would then be guarding a number that was measured on data the
        candidate had already seen.

        ``deadline`` is a monotonic wall-clock timestamp, the same budget the
        cycle's gate arms run under. Without it development ran unbounded: a
        provider that stalled produced unresolved rows, and an unresolved row
        is not a measurement -- "no target improved" was read off the missing
        prediction and archived as a falsification. With it the replay
        fail-closes exactly as the gates do, and the failure is reported as
        "not measured" instead.
        """
        if split is not None:
            # Every set reserved for a formal reading, not only the two the B
            # gates score on: ``validation`` is the mini protocol's only
            # out-of-sample measurement and ``final`` is read-once, so a
            # development replay that consumed either would destroy the
            # reading it was supposed to leave alone.
            formal = {str(case) for case in (
                *split.sentinel, *split.gain, *split.validation,
            )}
            formal.update(
                str(item.get("case") or item) for item in (split.final or ())
            )
            spent = sorted(formal & {str(case) for case in cases})
            if spent:
                raise ValueError(
                    "development must not measure a candidate on formal "
                    f"evaluation cases: {spent[:5]}"
                )
        arms = _ArmRunner(self, deadline)
        active = self.registry.registry
        active_entries = list(self.registry.entries)
        unit_entries = [dict(candidate)]
        candidate_registry = make_registry([*active_entries, *unit_entries])
        fresh = requires_fresh_evaluation([*active_entries, *unit_entries])
        baseline_rows = arms.run(
            list(cases), registry=active, fresh=(
                requires_fresh_evaluation(active_entries)
            ),
        )
        candidate_rows = arms.run(
            list(cases), registry=candidate_registry, fresh=fresh,
        )
        self.development_provider_calls = getattr(
            self, "development_provider_calls", 0,
        ) + (len(cases) if fresh else 0)
        # Refuse to hand back rows that cannot be scored. Raising is the point:
        # the caller has a word for "not measured" (``unmeasured``) while the
        # rows themselves spell the reading "no target improved", which is how
        # a provider outage became a recorded falsification.
        for arm_name, rows in (
            ("baseline", baseline_rows), ("candidate", candidate_rows),
        ):
            infrastructure = arm_has_infrastructure_failure(rows)
            if infrastructure:
                raise DevelopmentUnmeasurable(
                    f"{arm_name} arm could not be measured: {infrastructure}"
                )
        return [
            {**dict(row), "arm": "baseline"} for row in baseline_rows
        ] + [
            {**dict(row), "arm": "candidate"} for row in candidate_rows
        ]

    def _development_targets(
        self, families: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The mechanisms this cycle may develop, and why the rest were skipped.

        A target needs a mechanism identity (the loop drafts against a
        mechanism, not a path) and at least two member cases, because a
        candidate's provenance must name a family with two or more patrol cases
        to be a valid proposal at all. A family that cannot yield a valid
        proposal is skipped *here*, before any budget is spent, and reported
        rather than dropped.
        """
        targets: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for family in families:
            family_id = str(family.get("failure_family") or "")
            mechanism = family.get("mechanism")
            reason = ""
            if not isinstance(mechanism, Mapping):
                reason = "no mechanism attached to the family"
            elif not str(mechanism.get("mechanism_id") or ""):
                reason = "mechanism has no id"
            else:
                cases = [str(case) for case in family.get("failure_cases") or ()]
                if len(cases) < 2:
                    reason = (
                        "family has fewer than two member cases, so a candidate "
                        "drawn against it could not carry a valid provenance"
                    )
            if reason:
                skipped.append({"failure_family": family_id, "reason": reason})
                continue
            mechanism = dict(mechanism)
            members = {str(case) for case in mechanism.get("member_cases") or ()}
            member_cases = [
                str(case) for case in family.get("failure_cases") or ()
            ]
            targets.append({
                "failure_family": family_id,
                "family": dict(family),
                "mechanism": mechanism,
                "target_cases": member_cases,
                "neighbor_cases": [
                    str(case) for case in mechanism.get("normal_neighbors") or ()
                    if str(case) not in members
                    and str(case) not in set(member_cases)
                ],
                "target_kind": (
                    "localization"
                    if str(family.get("target_kind") or "") == "localization"
                    or family_id.startswith("FAM-loc-")
                    else "detection"
                ),
            })
        return targets, skipped

    def _development_deadline(self) -> float | None:
        """The wall-clock budget the development replay runs under.

        The same one the cycle's gate arms run under: development is a stage of
        the cycle, so it spends the cycle's deadline rather than a fresh one.
        Without it the replay was unbounded -- a stalled provider produced
        unresolved rows and the loop read the missing prediction as "no target
        improved", so an outage was archived as a falsified candidate.
        """
        seconds = self._cycle_seconds()
        if seconds is None:
            return None
        journal = getattr(self, "_journal", None)
        if journal is not None:
            # A resumed cycle has already spent the seconds it recorded before
            # the restart; the fresh budget would otherwise be the full cap
            # again. Mirrors the arithmetic ``_run_cycle`` uses for its arms.
            seconds = max(
                0.001,
                seconds - float(getattr(journal, "previous_seconds", 0.0)),
            )
        return _time_monotonic() + seconds

    def _record_development_episodes(
        self,
        *,
        cycle: int,
        family: Mapping[str, Any],
        developer: Any,
        result: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Remember the versions this cycle's development loop falsified.

        The episodic store is written for every attempt a gate decides on, and
        a development version that failed L1 *is* decided -- by the loop rather
        than by a gate. Without it the next round's recall saw no record that
        "this mechanism, along this route, was already tried and failed" and
        drafted the same mechanism again.

        ``unevaluable`` is the same distinction the gate path makes: a version
        whose replay could not score it (``unmeasured``) was never judged, so
        it is stored as an unevaluable attempt and is explicitly *not* a
        falsification (``memory.episodic_attempts`` derives ``falsified`` from
        exactly this pair).

        Deliberately not appended to ``self.rejection_feedback``:
        ``proposer.set_rejection_feedback`` turns every non-unevaluable entry
        into a ``rejected_keys`` fingerprint that permanently refuses that
        executable mechanism from later drafts, and ``make_development_drafter``
        documents that a development failure is restored afterwards rather than
        banning the candidate. The memory write is what makes the failure
        visible to the next draft; a permanent ban would be a stronger claim
        than "L1 refused this version".

        The stored intervention id is qualified with the version
        (``IV-1-D001#v001``). The episodic store deduplicates on
        ``(cycle, intervention_id, outcome)``, and every version the loop drafts
        for one mechanism carries the *same* intervention id -- so an
        unqualified record would silently swallow the gate's own episode for the
        later version that did pass L1 and reach the gates. The two records name
        different artifacts (different candidate hashes), which is what the
        version qualifier preserves.
        """
        candidates_by_version = {
            str(item.get("version_id") or ""): dict(item.get("candidate") or {})
            for item in getattr(developer.graph, "versions", ())
            if isinstance(item.get("candidate"), Mapping)
        }
        family_id = str(family.get("failure_family") or "")
        actionability = family.get("actionability") or {}
        polarity = str(actionability.get("polarity") or "")
        recorded: list[dict[str, Any]] = []
        for version in result.get("versions") or ():
            status = str(version.get("status") or "")
            if status not in {"failed_l1", "unmeasured"}:
                continue
            version_id = str(version.get("version_id") or "")
            candidate = candidates_by_version.get(version_id) or {}
            intervention_id = str(candidate.get("intervention_id") or "")
            l1 = version.get("l1") or {}
            reasons = "; ".join(
                str(item) for item in (l1.get("reasons") or ())
            )
            unevaluable = bool(l1.get("unmeasurable"))
            actual_route = route_of(candidate)
            # The reason travels into the next round's drafting prompt through
            # memory recall, so it is redacted here: an L1 reason names the
            # cases it scored, and case identifiers must not cross into
            # proposer prose. The raw reason stays in the cycle report, which
            # is a runtime record rather than prompt input.
            record = record_episode(
                self.memory_root,
                cycle=int(cycle),
                failure_family=family_id,
                polarity=polarity,
                outcome="rejected",
                gate="DEVELOPMENT",
                reason=redact_case_ids(
                    reasons or f"{version_id} failed L1 in development"
                ),
                intervention_id=(
                    f"{intervention_id}#{version_id}" if intervention_id else ""
                ),
                intervention_type=str(candidate.get("type") or ""),
                hook=str(candidate.get("hook") or ""),
                treatment_routes=[actual_route] if actual_route else [],
                unevaluable=unevaluable,
            )
            recorded.append({
                "version_id": version_id,
                "gate": "DEVELOPMENT",
                "intervention_id": record.get("intervention_id"),
                "candidate_intervention_id": intervention_id,
                "status": status,
                "unevaluable": unevaluable,
                "reason": reasons or f"{version_id} failed L1 in development",
            })
        return recorded

    def _develop_candidates(
        self,
        *,
        cycle: int,
        families: Sequence[Mapping[str, Any]],
        capability_plan: Mapping[str, Any],
    ) -> tuple[ProposalBatch | None, dict[str, Any] | None]:
        """Run the development loop for this cycle's mechanisms (plan §23.3.6).

        Returns ``(batch, block)``. A batch means the round's candidates are
        the versions that passed L0 and L1 here; an empty batch with a block
        means development ran and certified nothing, and the *reason* is the
        versions' own verdicts. ``(None, None)`` means development had no
        target at all, and the caller falls back to single-shot drafting
        unchanged.

        Why this is a filter and not a second opinion: a candidate that fails
        its targeted replay is not sent to the gates anyway -- the gates judge
        candidates, they do not re-run the development loop's job -- and the
        gate path costs orders of magnitude more than the L1 replay that
        already falsified it. The first real cycle showed the cost of not
        having this: a single drafted candidate was carried into a 62-minute
        paired promotion run and came back rejected.
        """
        from evolution.development import (
            CandidateDeveloper,
            DevelopmentBudget,
            development_report,
        )

        development_cfg = dict(self.config.get("development") or {})
        development_root = (
            Path(self.config["work_root"]) / "development" / f"cycle{int(cycle):03d}"
        )
        targets, skipped = self._development_targets(families)
        cap = max(0, int(self.development_max_mechanisms))
        capped = targets[cap:]
        targets = targets[:cap]
        skipped.extend({
            "failure_family": str(item["failure_family"]),
            "reason": (
                f"development.max_mechanisms_per_cycle={cap} was already "
                "reached by earlier mechanisms this cycle"
            ),
        } for item in capped)
        if not targets:
            # No mechanism can carry a valid candidate this round, so the round
            # falls back to its single-shot path and says why it did.
            return None, {
                "schema_version": "evolution.cycle_development.v1",
                "cycle": int(cycle),
                "root": str(development_root),
                "targets": 0,
                "skipped": skipped,
                "runs": [],
                "frozen_versions": [],
                "candidate_count": 0,
                "reason": "no mechanism was eligible for development this cycle",
                "metric_role": "diagnostic-only",
            }
        requirements = {
            str(item.get("mechanism_id") or ""): dict(item)
            for item in (capability_plan.get("requirements") or ())
        }
        runs: list[dict[str, Any]] = []
        frozen: list[dict[str, Any]] = []
        # The deadline the loop's replays run under, computed once for the
        # whole stage so every mechanism's L1 shares one budget with the gate
        # arms rather than each spending a fresh one.
        development_deadline = self._development_deadline()
        for target in targets:
            mechanism = target["mechanism"]
            mechanism_id = str(mechanism.get("mechanism_id") or "")
            requirement = requirements.get(mechanism_id, {})
            run_dir = development_root / mechanism_id
            budget = DevelopmentBudget(
                max_versions_per_mechanism=int(
                    development_cfg.get("max_versions_per_mechanism", 4),
                ),
                max_provider_calls=int(
                    development_cfg.get("max_provider_calls", 6),
                ),
                max_l1_cases=int(development_cfg.get("max_l1_cases", 24)),
                require_normal_neighbors=int(
                    development_cfg.get("require_normal_neighbors", 2),
                ),
                allow_action_class_change=bool(
                    development_cfg.get("allow_action_class_change", True),
                ),
            )
            draft_identifier = f"IV-{int(cycle)}-D{len(runs) + 1:03d}"
            developer = CandidateDeveloper(
                output_dir=run_dir,
                budget=budget,
                mechanism=mechanism,
                capability_plan=requirement,
                route_failure_records=self.route_failure_records(),
                target_cases=target["target_cases"],
                neighbor_cases=target["neighbor_cases"],
                active_entries=self.registry.entries,
                target_kind=target["target_kind"],
                gold_steps=self.gold_step_map,
                l1_runner=lambda cases, *, candidate, plan: self.develop_rows(
                    cases, candidate=candidate, split=self.split,
                    deadline=development_deadline,
                ),
            )
            # The id the drafter stamps on every version of this mechanism is
            # computed here, before the loop, so the recovery branch below can
            # name the version it recovered with the same id the drafter would
            # have used. (It referenced a caller-scope name that does not exist
            # in this scope: entering the branch raised NameError.)
            result = developer.develop(
                make_development_drafter(
                    proposer=self.proposer, cycle=cycle,
                    family=target["family"],
                    mechanism_id=mechanism_id,
                    identifier=draft_identifier,
                ),
                cycle=int(cycle),
                max_versions=int(
                    development_cfg.get("max_versions_per_mechanism", 4),
                ),
            )
            block = development_report(result)
            block["failure_family"] = target["failure_family"]
            block["target_kind"] = target["target_kind"]
            block["target_cases"] = list(target["target_cases"])
            block["neighbor_cases"] = list(target["neighbor_cases"])
            block["run_dir"] = str(run_dir)
            block["archive_policy"] = self.archive_on
            # The versions this development run falsified are written to the
            # episodic store here: they never reach a gate, so the gate path's
            # own memory write could not see them, and the next round's recall
            # then had no record that this mechanism and route had already been
            # tried and failed.
            block["episodes"] = self._record_development_episodes(
                cycle=int(cycle), family=target["family"],
                developer=developer, result=result,
            )
            # §28.6 ``cycle_end``: a cycle that develops several mechanisms
            # remembers all of them in one place, which is why the decision is
            # taken here rather than inside the loop. With ``development_end``
            # the caller owns that boundary (``develop`` archives its own run),
            # and with ``off`` nothing is written -- so neither policy is
            # silently upgraded into this one.
            if self.archive_on == "cycle_end":
                block["archive"] = self.archive_development_run(
                    run_dir,
                    cycle=int(cycle),
                    mechanism_id=mechanism_id,
                    family_id=target["failure_family"],
                    mechanism=mechanism,
                )
            selected = str(result.get("selected_version_id") or "")
            if selected:
                # ``freeze`` is the loop's only exit and the only writer of the
                # status the promotion path accepts, so a version is frozen
                # here rather than merely reported as ready.
                handed = developer.freeze(selected)
                candidate = dict(handed.get("candidate") or {})
                candidate.pop("content_hash", None)
                frozen.append(candidate)
                block["frozen"] = {
                    "version_id": handed["version_id"],
                    "candidate_hash": handed["candidate_hash"],
                    "intervention_id": str(candidate.get("intervention_id") or ""),
                }
            else:
                recovered = self._recover_certified_version(developer)
                if recovered is not None:
                    candidate = dict(recovered["candidate"])
                    identifier_present = str(
                        candidate.get("intervention_id") or ""
                    )
                    if not identifier_present:
                        # The id the drafter would have stamped. A recovered
                        # version is normally the drafter's own output and
                        # carries one; a version written by another path (a
                        # ``develop`` run, a fixture) may not, and a proposal
                        # without an id cannot be validated downstream.
                        candidate["intervention_id"] = draft_identifier
                    candidate.pop("content_hash", None)
                    frozen.append(candidate)
                    block["recovered"] = {
                        "version_id": recovered["version_id"],
                        "candidate_hash": recovered["candidate_hash"],
                        "status": recovered["status"],
                        "reason": (
                            "a version from an earlier pass of this run already "
                            "passed L1; it is used rather than re-developed"
                        ),
                    }
                    block["frozen"] = {
                        "version_id": recovered["version_id"],
                        "candidate_hash": recovered["candidate_hash"],
                        "intervention_id": str(
                            candidate.get("intervention_id") or ""
                        ),
                    }
                    block["status"] = "ready_for_freeze"
            runs.append(block)
        # The draft calls are *not* folded into ``development_provider_calls``:
        # that attribute counts fresh case evaluations spent by
        # ``develop_rows``, and one counter holding two units is a reading
        # nobody can check. The draft cost travels in each run's ``budget``
        # block instead.
        batch = ProposalBatch(
            candidates=frozen,
            joint_groups=infer_joint_groups(frozen),
            notes=[{
                "generator": "development",
                "provider_called": False,
                "reason": (
                    f"{len(frozen)} version(s) passed development and were frozen "
                    f"from {len(runs)} developed mechanism(s)"
                ),
            }],
        )
        block = {
            "schema_version": "evolution.cycle_development.v1",
            "cycle": int(cycle),
            "root": str(development_root),
            "targets": len(targets),
            "skipped": skipped,
            "runs": runs,
            "frozen_versions": [run.get("frozen") for run in runs if run.get("frozen")],
            "candidate_count": len(frozen),
            "metric_role": "diagnostic-only",
        }
        return batch, block

    @staticmethod
    def _recover_certified_version(developer: Any) -> dict[str, Any] | None:
        """A version already on disk that this run certified but never used.

        The loop's output directory is keyed by cycle and mechanism, so
        re-entering it is a live possibility: a process that dies *after* a
        version passed but before the round persisted its batch leaves a
        ``frozen`` version behind. Re-running the loop then finds its budget
        already spent, drafts nothing, and would report "development produced
        nothing" -- losing certified work and describing the stage as empty
        when it was not.

        So when the loop stops without a selection, the last version on disk
        whose status admits it to promotion is used instead. Only the two
        statuses ``freeze`` can produce are accepted: ``ready_for_freeze`` and
        ``frozen``. A ``failed_l0``/``failed_l1`` version is still a failure on
        re-entry, exactly as it was the first time.
        """
        admissible = {"ready_for_freeze", "frozen"}
        recovered: dict[str, Any] | None = None
        for version in developer.graph.versions:
            version_id = str(version.get("version_id") or "")
            status = str(
                developer.graph.development_status(version_id).get("status") or ""
            )
            if status not in admissible:
                continue
            candidate = version.get("candidate")
            if not isinstance(candidate, Mapping):
                continue
            recovered = {
                "version_id": version_id,
                "candidate_hash": str(version.get("candidate_hash") or ""),
                "candidate": dict(candidate),
                "status": status,
            }
        return recovered

    @_engine_operation
    def archive_development_run(
        self,
        run_dir: str | Path,
        *,
        cycle: int,
        mechanism_id: str = "",
        family_id: str = "",
        mechanism: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Remember one development run's versions, pass or fail (§28.6).

        Called when a development run *ends*, which is the point at which a
        version has left the loop and its verdicts are final for that run. The
        versions and their statuses are read from the run's own records rather
        than restated by the caller -- see ``archive.ingest_development_run``
        -- so the archive cannot disagree with the loop it remembers.

        ``force`` exists so a caller can archive regardless of the configured
        policy, which is what a test or a one-off analysis needs. A run that
        did not opt in gets a refusal rather than a write, and the refusal is
        reported rather than raised: an unwritable archive must not fail a
        cycle that has already spent provider budget.

        The components each archived candidate contributes are registered in
        the same call, and only for versions that **passed L1**. Local
        usefulness is the claim a component makes, and L1 is the only reading
        in this framework that establishes it: a version that failed its
        targeted replay was not shown to be useful, so registering its parts as
        reusable would launder a rejected candidate into the next round's
        drafting prompt.

        Candidate records go to ``archive_root``; components go to
        ``component_store``, which is the *same* directory unless
        ``component_root`` was configured (see the warning field the
        constructor sets when they differ). Writing the component half to the
        archive root while retrieval read a different path is why no archived
        component ever reached a drafting prompt.
        """
        if self.archive_on == "off" and not force:
            return {
                "archived": False,
                "reason": (
                    "archive.on is 'off'; set it to 'development_end' or "
                    "'cycle_end' to record development runs"
                ),
                "policy": self.archive_on,
            }
        from evolution.archive import (
            ingest_development_run,
            record_archive_summary,
            record_component,
        )

        ingested = ingest_development_run(
            self.archive_root,
            run_dir,
            cycle=int(cycle),
            mechanism_id=str(mechanism_id or ""),
            family_id=str(family_id or ""),
        )
        registered: list[dict[str, Any]] = []
        for record in self._components_from_development(
            run_dir,
            cycle=int(cycle),
            mechanism_id=str(mechanism_id or ""),
            mechanism=mechanism,
        ):
            try:
                registered.append(record_component(self.component_store, **record))
            except (ValueError, KeyError) as exc:
                # A component the store refuses is reported, not fatal: the
                # candidate archive half has already been written and is the
                # part the loop needs.
                registered.append({
                    "name": record.get("name"),
                    "registered": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                })
        return {
            "archived": True,
            "policy": self.archive_on,
            "root": str(self.archive_root),
            "component_root": str(self.component_store),
            "versions": ingested.get("versions"),
            "recorded": ingested.get("recorded"),
            "components": registered,
            "report": record_archive_summary(
                self.archive_root, component_root=self.component_store,
            ),
        }

    def _components_from_development(
        self,
        run_dir: str | Path,
        *,
        cycle: int,
        mechanism_id: str,
        mechanism: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """The reusable parts a development run's passing versions contribute.

        Which programs count: an ``evidence_check`` whose body carries an
        inline typed-DSL ``predicate_definition``. That is the only object in
        this framework with a stable digest, a closed vocabulary and two
        consumers, which is the component definition ``archive`` uses; a
        deterministic built-in predicate is framework code rather than
        something this run produced, and a prose directive has no digest.

        Which *versions* count: those whose status says they passed L1.
        ``freeze`` rewrites the sidecar's status to ``frozen`` and is reachable
        only from ``ready_for_freeze``, so accepting both is accepting exactly
        the versions the loop certified and not a version that merely stopped
        iterating -- ``failed_l0`` and ``failed_l1`` are neither.
        """
        admissible = {"ready_for_freeze", "frozen"}
        from evolution.candidate_versions import CandidateVersionGraph

        graph = CandidateVersionGraph(Path(run_dir))
        graph.load_existing()
        parts: list[dict[str, Any]] = []
        for version in graph.versions:
            version_id = str(version.get("version_id") or "")
            sidecar = graph.development_status(version_id)
            if str(sidecar.get("status") or "") not in admissible:
                continue
            candidate = version.get("candidate") or {}
            body = candidate.get("body") or {}
            program = body.get("predicate_definition")
            if not isinstance(program, Mapping):
                continue
            parts.append({
                "name": str(program.get("name") or body.get("predicate") or ""),
                "kind": "parser",
                "program": dict(program),
                "cycle": int(cycle),
                "mechanism_id": str(mechanism_id or ""),
                "source_candidate_hash": str(version.get("candidate_hash") or ""),
                "capability": self.component_capability(mechanism),
                "verdicts": list(program.get("verdicts") or ()),
                "evidence": {
                    "version_id": version_id,
                    "l1": (sidecar.get("l1") or {}).get("delta"),
                },
            })
        return parts

    @staticmethod
    def component_capability(mechanism: Mapping[str, Any] | None) -> str:
        """The capability vocabulary value a component is retrieved under.

        Read from the mechanism's own repair requirement, so the key a
        component is *stored* under and the key retrieval *matches* on are the
        same vocabulary. Those were once different words in one field and
        retrieval could never match (see §26.3); deriving both from
        ``repair_requirements`` is what keeps that from coming back.

        Taking the mechanism rather than looking it up is deliberate. An
        earlier version searched ``cycle_reports`` for the id and returned
        ``""`` when the mechanism was not there -- which is the normal case for
        a ``develop`` run, since that path never populates the attribute. The
        component was then stored unclassified and ranked below every
        mechanism-matched part.
        """
        if not mechanism:
            return ""
        try:
            required = repair_requirements(mechanism)
        except (ValueError, KeyError):
            return ""
        caps = [str(item) for item in required.get("required_capabilities") or ()]
        return caps[0] if caps else ""

    @_engine_operation
    def record_route_failures(
        self,
        *,
        cycle: int,
        diagnosed_families: Sequence[Mapping[str, Any]],
        gate_reports: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Remember routes that fired on a mechanism and changed nothing.

        This is the negative half of capability planning. ``record_capability_gaps``
        records that nothing could be tried; this records that something was
        tried, reached the runtime, moved what it promised to move, and still
        left the target outcome alone. Without it the next round re-proposes
        the same route against the same mechanism, which is what the pilot's
        tracer directive did ten times.

        Nothing here scores, admits or rejects: it only writes down what the
        gates already measured.
        """
        family_by_name = {
            str(item.get("failure_family") or ""): item
            for item in diagnosed_families
        }
        observations: list[dict[str, Any]] = []
        for report in gate_reports:
            candidate = dict(report.get("candidate") or {})
            provenance = candidate.get("provenance") or {}
            family_id = str(provenance.get("failure_family") or "")
            family = family_by_name.get(family_id)
            mechanism_id = str((family or {}).get("mechanism", {}).get(
                "mechanism_id") or "")
            if not mechanism_id:
                continue
            behavior = report.get("behavior") or {}
            if behavior.get("passed"):
                continue
            polarity = str(
                (candidate.get("claim") or {}).get("target_polarity") or ""
            )
            opportunity = target_opportunity(
                target_kind=candidate_target_kind(candidate), polarity=polarity,
                detection=report.get("gain_opportunities") or {},
                localization=report.get("localization_opportunities"),
            )
            observation = insufficiency_evidence(
                candidate=candidate,
                behavior=behavior,
                outcome=report.get("target_outcome"),
                opportunity=opportunity or 0,
                substantive_rejection=not bool(
                    gate_rejection_is_unevaluable(
                        gate="behavior",
                        verdict=behavior,
                        report=report,
                        candidate=candidate,
                    )
                ),
            )
            if observation is None:
                continue
            observation["measurement"] = measurement_for_candidate(report.get("route_measurement"), candidate)
            observations.append({
                "mechanism_id": mechanism_id,
                "family_id": family_id,
                "observation": observation,
            })
        appended = record_route_failures(
            self.route_failure_store, cycle=cycle, observations=observations,
        )
        if observations:
            self._ledger("route_insufficiency", {
                "cycle": cycle,
                "recorded": [
                    {
                        "mechanism_id": item["mechanism_id"],
                        "route": item["observation"]["route"],
                        "evidence": item["observation"]["evidence"],
                    }
                    for item in observations
                ],
                "appended": len(appended),
                "duplicates_skipped": len(observations) - len(appended),
            })
        return {
            "observed": len(observations),
            "appended": len(appended),
            "state": route_failure_summary(
                load_route_failures(self.route_failure_store), runtime_identity=backend_runtime_identity(self.backend),
            ),
        }

    @_engine_operation
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
            "source": source_identity("detector", "evolution"),
            "runtime_identity": backend_runtime_identity(self.backend, cases=self.split.pool),
            # The verdict is produced by the gates, so the gate thresholds are
            # part of its identity: reusing a verdict recorded under different
            # thresholds would certify gates that never ran in that form.
            "gates": dict(self.config.get("gates") or {}),
            "budget": dict(self.config.get("budget") or {}),
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
                case: artifact_digest(Path(self.config["dataset_root"]) / case)
                for case in [*self.split.sentinel, *self.split.gain]
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
            and cached.get("fingerprint") == digest(fingerprint_payload)
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
                    audit=True,
                )
                report["harmlessness"] = unit["harmlessness"]
                if (unit["harmlessness"] or {}).get("passed"):
                    report["behavior"] = unit["behavior"]
                    if unit["behavior"]["passed"]:
                        report["m"] = unit["m"]
                        if unit["m"]["passed"]:
                            report["audit_rejected"] = False
                            report["audit_passed"] = True
                if not report["audit_passed"]:
                    # Two independent reasons a rejection proves nothing, and
                    # they must not be conflated:
                    #
                    # 1. The gate could not score the defect at all (its verdict
                    #    is not evaluable). Insufficient baseline-correct
                    #    coverage means the gates were never exercised.
                    # 2. The gate could score it, but only because of rows that
                    #    could not be measured. Removing those rows and
                    #    re-deriving the rejection tests exactly that.
                    #
                    # The whole-batch sweep this replaces treated *any*
                    # unmeasurable row anywhere as disqualifying, which froze an
                    # entire multi-cycle run because one sentinel case hit its
                    # case deadline -- a case in neither the verdict's evidence
                    # nor its cause. A genuinely leaking defect still leaks on
                    # the healthy cases, so excluding the unmeasured ones
                    # weakens nothing; and an audit that refuses to certify
                    # whenever any background case degrades is not more honest,
                    # only more fragile.
                    unmeasured = any(
                        verdict.get("passed") is False
                        and verdict.get("evaluable") is False
                        for verdict in (
                            unit.get("harmlessness") or {},
                            unit.get("behavior") or {},
                            unit.get("m") or {},
                        )
                    )
                    if unmeasured:
                        infrastructure = (
                            "defect evaluation has insufficient measurement "
                            "coverage"
                        )
                    else:
                        infrastructure = _defect_rejection_after_exclusions(
                            unit=unit,
                            baseline_rows=[*baseline_s, *baseline_g],
                            sentinel_cases=list(self.split.sentinel),
                            gain_cases=list(self.split.gain),
                            config=self.config,
                        )
                    if infrastructure:
                        report["audit_inconclusive"] = True
                        report["audit_inconclusive_reason"] = infrastructure
                        # The exact rows that could not be measured, kept on
                        # the report so a reader can tell "the gates rejected
                        # the defect" from "nobody measured it" without
                        # re-deriving it from the delta.
                        report["unmeasured_rows"] = [
                            row for row in (
                                *(unit.get("candidate_rows") or ()),
                                *baseline_s,
                                *baseline_g,
                            )
                            if arm_has_infrastructure_failure([row])
                        ]
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
            # A freeze that rests on cases nobody could measure is a statement
            # about the machine, not about the gates, so the machinery is free
            # to try again (see the cache write below).
            "retryable": inconclusive_any and not passed_any,
            "unmeasured_cases": sorted(
                _unmeasurable_cases([
                    *baseline_s,
                    *baseline_g,
                    *[
                        row
                        for report in reports
                        for row in (report.get("unmeasured_rows") or ())
                    ],
                ])
            ),
        }
        if not verdict["retryable"]:
            # Only a decided verdict is cached. An inconclusive one records
            # that the measurement failed, and caching it would turn one
            # provider outage into a permanent property of this work_root:
            # every later cycle would take the cache hit and freeze again
            # without ever re-measuring. That is the same defect as reading
            # "not measured" as "falsified" (see §32.3), one layer up.
            save_json(cache_path, {
                "fingerprint": digest(fingerprint_payload),
                "passed": verdict["passed"],
                "frozen": verdict["frozen"],
                "reports": reports,
            })
        self._ledger("audit", {
            "cycle": cycle,
            "passed": verdict["passed"],
            "reports": reports,
            "cache": "miss",
            "retryable": verdict["retryable"],
            "unmeasured_cases": verdict["unmeasured_cases"],
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
        unstable_cases: Sequence[str] | None = None,
        arms: _ArmRunner,
        audit: bool = False,
    ) -> dict[str, Any]:
        """One admission unit: one S+G arm scored by HARMLESSNESS, BEHAVIOR and M.

        The unit's registry is ``active + partners + candidate``; joint units
        pass their whole group so members are never sold separately. A fresh
        arm (any member on an unfreezing hook) pairs with the shared fresh
        control roll produced once per cycle; a deterministic arm pairs with
        the cached active baseline so its delta carries no provider noise.

        ``unstable_cases`` is the case set the control configuration itself
        read two ways across two rolls, or ``None`` when no reroll was taken
        this round. It travels to the gates as an annotation: M scores its
        benefit floor on gain that does not rest on those cases, and a
        rejection whose whole harm sits on them is marked as an unstable
        reading rather than as a falsified mechanism. It can only ever make
        admission stricter.
        """
        intervention_id = str(candidate.get("intervention_id") or "")
        unit_entries = [*partners, dict(candidate)]
        registry = make_registry([*active_entries, *unit_entries])
        fresh = requires_fresh_evaluation([*active_entries, *unit_entries])
        polarity = str(
            (candidate.get("claim") or {}).get("target_polarity") or ""
        )
        kind = candidate_target_kind(candidate)
        coverage_direction = "localization" if kind in {"localization", "joint"} else polarity
        sentinel = list(self.split.sentinel)
        control = (
            fresh_control_rows if fresh and fresh_control_rows is not None
            else list(active_rows)
        )
        control_g = _subset_rows(control, self.split.gain)
        opportunity = gain_opportunity(control_g)
        localization_opportunities = localization_opportunity(control_g, gold_steps=self.gold_step_map)
        primary_opportunity = target_opportunity(
            target_kind=kind, polarity=polarity, detection=opportunity,
            localization=localization_opportunities,
        )
        # Mechanism-level opportunity applies only when an evaluator binding was
        # supplied for this run. Cycles that carry no binding keep the polarity
        # reading exactly as before: the framework has no evaluator-confirmed
        # membership for the gain set, and inventing one from case ids or from a
        # shared polarity is what the mechanism work exists to prevent.
        mechanism_reading = self._mechanism_opportunity(control_g)

        # Decide coverage before spending a candidate arm, including candidates
        # supplied explicitly or partners with a different declared polarity.
        response = (
            self._coverage_response(coverage_direction, control_g)
            if coverage_direction and primary_opportunity == 0
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
            replacement_localization = localization_opportunity(control_g, gold_steps=self.gold_step_map)
            response["replacement_localization_opportunity"] = replacement_localization
            response["replacement_has_coverage"] = (
                (target_opportunity(target_kind=kind, polarity=polarity,
                                    detection=replacement_opportunity,
                                    localization=replacement_localization) or 0) > 0
            )
        baseline_s = _subset_rows(control, sentinel)
        coverage = sentinel_coverage(baseline_s, self.config)
        measured_opportunities = gain_opportunity(control_g)
        localization_opportunities = localization_opportunity(control_g, gold_steps=self.gold_step_map)
        primary_opportunity = target_opportunity(
            target_kind=kind, polarity=polarity, detection=measured_opportunities,
            localization=localization_opportunities,
        )
        unavailable = [
            candidate_target_kind(entry) + ":" + str((entry.get("claim") or {}).get("target_polarity") or "")
            for entry in unit_entries
            if target_opportunity(
                target_kind=candidate_target_kind(entry),
                polarity=str((entry.get("claim") or {}).get("target_polarity") or ""),
                detection=measured_opportunities, localization=localization_opportunities,
            ) == 0
        ]
        if not coverage["evaluable"] or (unavailable and not audit) or (
            mechanism_reading is not None
            and not mechanism_reading["evaluable"]
            and not audit
        ):
            if mechanism_reading is not None and not mechanism_reading["evaluable"]:
                reason = (
                    "mechanism-aligned gain set cannot score this mechanism: "
                    + str(mechanism_reading.get("reason_code") or "")
                )
            elif not coverage["evaluable"]:
                reason = (
                    "insufficient baseline-correct sentinel coverage: "
                    + coverage["reason"]
                )
            else:
                reason = (
                    f"no repair opportunities in gain for "
                    f"{sorted(set(unavailable))}"
                )
            blocked = {"passed": False, "reason": reason, "delta": None,
                       "evaluable": False, "status": "not_evaluated"}
            if response is not None and response["kind"] == "skip":
                blocked["status"] = "skipped_unmeasured"
            if response is not None:
                blocked["coverage_response"] = response
            return {
                "intervention_id": intervention_id,
                "candidate": dict(candidate),
                "partners": [dict(entry) for entry in partners],
                "fresh": fresh,
                "gain_opportunities": measured_opportunities,
                "localization_opportunities": localization_opportunities,
                "mechanism_opportunity": mechanism_reading,
                "measurement_gain_cases": measurement_gain,
                "coverage_response": response,
                "candidate_rows": [],
                "harmlessness": blocked if not coverage["evaluable"] else None,
                "behavior": None,
                "member_behavior": {},
                "m": blocked if coverage["evaluable"] else None,
            }

        candidate_rows = arms.run(
            [*sentinel, *measurement_gain], registry=registry, fresh=fresh,
        )
        candidate_s = _subset_rows(candidate_rows, sentinel)
        control_g = _subset_rows(control, measurement_gain)
        candidate_g = _subset_rows(candidate_rows, measurement_gain)
        delta = paired_delta(
            control_g, candidate_g, unstable_cases=unstable_cases,
            expected_cases=measurement_gain,
        )
        localization_delta = (
            paired_localization_delta(
                control_g, candidate_g, gold_steps=self.gold_step_map,
                unstable_cases=unstable_cases, expected_cases=measurement_gain,
            )
            if any(candidate_target_kind(entry) in {"localization", "joint"}
                   for entry in unit_entries) else None
        )
        # Rows that could not be measured honestly (provider failure, deadline,
        # non-authoritative terminal) manufacture "regressions" the moment a
        # gate compares them: unresolved counts as incorrect, so an arm that
        # never ran reads as a candidate that broke things. Record them here so
        # no failing verdict on this unit can be stored as a falsification, and
        # so admission stays fail-closed instead of passing on partial rows.
        unmeasured = sorted(_unmeasurable_cases([*control, *candidate_rows]))
        member_outcomes = {
            str(entry.get("intervention_id") or ""): target_outcome(
                target_kind=candidate_target_kind(entry),
                polarity=str((entry.get("claim") or {}).get("target_polarity") or ""),
                delta_basis="benefit_set", detection_delta=None if unmeasured else delta,
                localization_delta=localization_delta,
            ) for entry in unit_entries
        }
        harmlessness = harmlessness_gate(
            baseline_rows=baseline_s,
            candidate_rows=candidate_s,
            config=self.config,
            unstable_cases=(
                None if unstable_cases is None else list(unstable_cases)
            ),
        )
        behavior = None
        member_behavior: dict[str, dict[str, Any]] = {}
        m = None
        if harmlessness["passed"]:
            # A localization claim is scored by the objective it declares. The
            # detection BEHAVIOR/M pair asks whether the binary verdict moved,
            # which a tracer directive deliberately leaves alone, so scoring a
            # localization claim there would report "never fired" or "no
            # marginal gain" for a claim that did exactly what it promised.
            for member in [*partners, dict(candidate)]:
                member_id = str(member.get("intervention_id") or "")
                member_behavior[member_id] = (
                    localization_behavior_gate(
                        member,
                        baseline_rows=control,
                        candidate_rows=candidate_rows,
                        config=self.config,
                        gold_steps=self.gold_step_map,
                    )
                    if candidate_target_kind(member) in {"localization", "joint"}
                    else behavior_gate(
                        member,
                        baseline_rows=control,
                        candidate_rows=candidate_rows,
                        config=self.config,
                    )
                )
            behavior = member_behavior[intervention_id]
            if all(verdict["passed"] for verdict in member_behavior.values()):
                if kind in {"localization", "joint"}:
                    passed, failures = meets_localization(
                        localization_delta,
                        config=self.config,
                        detection_delta=delta,
                        target_kind=kind,
                    )
                    m = {
                        "passed": passed,
                        "reason": "; ".join(failures),
                        "delta": delta,
                        "localization_delta": localization_delta,
                        "target_kind": kind,
                    }
                else:
                    passed, failures = meets_m(delta, config=self.config)
                    m = {
                        "passed": passed,
                        "reason": "; ".join(failures),
                        "delta": delta,
                        "target_kind": kind,
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
                elif primary_opportunity == 0 and not passed:
                    # Strict policy: record the rejection as unmeasured rather
                    # than as a failed claim, admission blocked as before.
                    m.update(
                        evaluable=False,
                        status="not_evaluated",
                        reason=(
                            f"no {coverage_direction} repair opportunities in gain; "
                            "claim was not measured (admission remains blocked)"
                        ),
                    )
                # Last, so a verdict that is already unevaluable for a
                # different reason is not re-labelled: the marker means "this
                # round could not score the claim", and the first honest reason
                # for that is the one worth keeping.
                if not passed and m.get("evaluable") is not False:
                    floor_from_noise = (
                        localization_floor_rests_on_unstable(
                            localization_delta,
                            config=self.config,
                            detection_delta=delta,
                            target_kind=kind,
                        )
                        if kind in {"localization", "joint"}
                        else marginal_floor_rests_on_unstable(
                            delta, config=self.config,
                        )
                    )
                    if floor_from_noise:
                        m["evidence_on_unstable_cases"] = True
                        m["stability_limited"] = True
        def _mark_unmeasured(verdict: Any) -> Any:
            """Mark a failing verdict whose arm could not be measured honestly.

            ``passed`` is left alone (admission is blocked by the caller's
            fail-closed check), but the verdict stops claiming to be evidence
            about the candidate: an infrastructure failure is not a refutation.
            """
            if not unmeasured or not isinstance(verdict, dict):
                return verdict
            if verdict.get("passed") is not False or verdict.get("evaluable") is False:
                return verdict
            marked = dict(verdict)
            marked["infrastructure_failure"] = True
            marked["evaluable"] = False
            marked["status"] = "not_evaluated"
            note = (
                f"{len(unmeasured)} case(s) could not be measured honestly "
                f"(first: {unmeasured[0]}); the rejection is not attributable "
                "to the candidate"
            )
            reason = str(verdict.get("reason") or "")
            marked["reason"] = f"{reason}; {note}" if reason else note
            return marked

        def _mark_unstable(verdict: Any) -> Any:
            """Mark a failing verdict whose entire evidence is instrument jitter.

            A rejection caused by a case the control configuration itself reads
            two ways across two rolls says nothing about the candidate, exactly
            as a rejection caused by an unmeasurable row does. The verdict keeps
            ``passed=False`` -- admission is blocked, and the gates are not
            relaxed by one point -- but it stops being stored as a falsified
            mechanism, which is the difference between "the mechanism is wrong"
            and "this case cannot be scored in one round".
            """
            if not isinstance(verdict, dict):
                return verdict
            if verdict.get("passed") is not False:
                return verdict
            cases = [str(case) for case in verdict.get("harm_on_unstable_cases") or ()]
            note = ""
            if verdict.get("harm_entirely_on_unstable") is True:
                note = (
                    "every recorded harm sits on a case the control "
                    "configuration itself read two ways"
                    + (f" ({', '.join(cases[:3])})" if cases else "")
                    + "; the rejection is an unstable reading, not a "
                    "falsification of the candidate"
                )
            elif verdict.get("evidence_on_unstable_cases") is True:
                note = (
                    "the benefit this claim recorded rests on case(s) the "
                    "control configuration read two ways; an unstable gain is "
                    "not evidence of repair"
                )
            if not note:
                return verdict
            marked = dict(verdict)
            marked["evidence_on_unstable_cases"] = True
            marked["stability_limited"] = True
            marked["evaluable"] = False
            marked["status"] = "not_evaluated"
            reason = str(verdict.get("reason") or "")
            marked["reason"] = f"{reason}; {note}" if reason else note
            return marked

        return {
            "intervention_id": intervention_id,
            "candidate": dict(candidate),
            "partners": [dict(entry) for entry in partners],
            "fresh": fresh,
            "gain_opportunities": measured_opportunities,
            "mechanism_opportunity": mechanism_reading,
            "measurement_gain_cases": measurement_gain,
            "coverage_response": response,
            "candidate_rows": _summarise_rows(candidate_rows),
            "route_measurement": build_route_measurement(
                candidate=candidate, partners=partners, cases=measurement_gain,
                control_rows=control_g, candidate_rows=candidate_g,
                runtime=backend_runtime_identity(self.backend),
                control_registry=make_registry(active_entries), candidate_registry=registry,
            ),
            "target_outcome": member_outcomes[intervention_id],
            "member_target_outcomes": member_outcomes,
            "localization_opportunities": localization_opportunities,
            "unmeasured_cases": unmeasured,
            "harmlessness": _mark_unstable(_mark_unmeasured(harmlessness)),
            "behavior": _mark_unmeasured(behavior),
            "member_behavior": {
                key: _mark_unmeasured(verdict)
                for key, verdict in member_behavior.items()
            },
            "m": _mark_unstable(_mark_unmeasured(m)),
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

    def _localization_records(
        self, rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Label-free records for the rows a localization task could form on.

        Reading full records for every patrol row would be wasteful and would
        touch the corpus for cases that are not localization candidates. Only
        the convicted-correct rows whose named step may be wrong are loaded.
        """
        wanted = {
            str(row.get("case") or "")
            for row in rows
            if row.get("gold") is True
            and row.get("prediction") is True
        }
        records: dict[str, Any] = {}
        for case in sorted(wanted):
            try:
                records[case] = load_record(self.config["dataset_root"], case)
            except Exception:
                # A case whose record cannot be read stays out of the
                # viability evidence; it is then "needs_review" rather than
                # silently accepted as a repairable mislocation.
                continue
        return records

    def _causal_policy(self) -> Mapping[str, Any] | None:
        backend = getattr(self.backend, "backend", self.backend)
        return getattr(getattr(backend, "detector", None), "config", {}).get("causal_audit")

    def _all_families(
        self, patrol_rows: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Every targetable family of the patrol: detection and localization.

        Both kinds must come from one place. The localization objective is
        only useful to the framework if a mislocated case can become a task,
        and a call site that reads ``stable_failure_families`` alone silently
        drops those cases -- they are convicted correctly, so no detection
        family ever contains them.

        Localization families are additionally filtered by
        ``localization_repair_viability``: a step that differs from the
        annotation needs structural impossibility or recomputed same-claim
        evidence. A second real error or an unsupported causal relation stays
        outside automatically repairable localization families.
        """
        return [
            *stable_failure_families(patrol_rows),
            *stable_localization_families(
                patrol_rows,
                gold_steps=self.gold_step_map,
                records=self._localization_records(patrol_rows),
                causal_policy=self._causal_policy(),
            ),
        ]

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

    @_engine_operation
    def preview_cycle(self, cycle: int) -> dict[str, Any]:
        """Patrol + proposal preview without gates, ledger, or registry writes.

        This is the cheap pre-flight for experimenters: it shows whether the
        current cycle would produce any candidate at all, before spending
        gate detection budget.
        """
        # Preview runs without a journal, so the runner evaluates directly.
        arms = _ArmRunner(self, None)
        patrol = prepare_patrol(
            cycle=cycle, split=self.split, budget=self.config.get("budget") or {}, arms=arms,
            evaluate=self._patrol_arms, find_families=self._all_families, extend=self._extend_patrol,
        )
        patrol_cases = patrol.cases
        patrol_rows = patrol.rows
        patrol_contrast = patrol.contrast
        bootstrap_cases = patrol.bootstrap_cases
        route_failure_records = self.route_failure_records()
        preparation = prepare_families(
            patrol.families, patrol_rows, dataset_root=self.config["dataset_root"],
            causal_policy=self._causal_policy(), route_failure_records=route_failure_records,
            runtime_identity=backend_runtime_identity(self.backend),
        )
        diagnosed = preparation.families
        mechanism_excluded = preparation.excluded
        mechanism_unrepresented = preparation.unrepresented
        capability_gaps = preparation.capability_gaps
        capability_plan = preparation.capability_plan
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
            # Which path a real cycle would take with this configuration, and
            # how many mechanisms it may develop. The preview itself drafts
            # single-shot -- running the loop here would spend L1 replay budget
            # to answer a question the preview exists to avoid spending on --
            # so the field is a statement about the configured cycle, not about
            # the candidates shown below.
            "round_path": (
                "development" if self.development_enabled else "single_shot"
            ),
            "development": {
                "enabled": self.development_enabled,
                "max_mechanisms_per_cycle": self.development_max_mechanisms,
            },
            "patrol_size": len(patrol_cases),
            "bootstrap_count": len(bootstrap_cases),
            "families": [
                {
                    "failure_family": item.get("failure_family"),
                    "coarse_failure_family": item.get("coarse_failure_family"),
                    "target_kind": item.get("target_kind") or "detection",
                    "count": item.get("count"),
                    "failure_cases": list(item.get("failure_cases") or ()),
                    "sources": list(item.get("sources") or ()),
                    # The prompt view deliberately strips case identifiers;
                    # the preview is a runtime record for the researcher and
                    # the development loop, both of which need the case list.
                    "mechanism": mechanism_prompt_view(item.get("mechanism")),
                    "mechanism_detail": item.get("mechanism"),
                    "actionability": item.get("actionability"),
                    "diagnostics": item.get("diagnostics"),
                }
                for item in diagnosed
            ],
            "mechanisms": mechanism_report(
                diagnosed, mechanism_unrepresented, mechanism_excluded,
            ),
            # The capability planner's reading travels with the preview: a
            # researcher deciding whether to spend development budget needs to
            # see which routes can act on each mechanism, not only which routes
            # were enumerated.
            "capability_plan": capability_plan,
            "route_failures": route_failure_summary(route_failure_records, runtime_identity=backend_runtime_identity(self.backend)),
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
    @_engine_operation
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
        contract = {"schema_version": "evolution.cycle_contract.v2", "cycle": cycle, "config": digest(self.config), "source": source_identity("detector", "evolution"),
                    "runtime_identity": backend_runtime_identity(self.backend, cases=self.split.pool),
                    "split": split_manifest_report(self.split),
                    "data": {case: artifact_digest(Path(self.config["dataset_root"]) / case) for case in self.split.pool}}
        # The contract deliberately excludes the registry hash: a cycle that
        # committed its own admission and then died must still be resumable.
        # The registry it started from is recorded separately and re-applied
        # below, so a resumed run is evaluated against its true starting point.
        self._journal = CycleJournal(
            self.paths["root"] / "checkpoints" / f"cycle{cycle:03d}",
            contract,
            resume=resume,
            registry_before=self.registry.registry,
        )
        try:
            self._resuming = resume
            if "completion" in self._journal.state:
                return self._project_cycle_completion(cycle, self._journal.state["completion"])
            if report_path.exists() or any(row["event"] == "cycle_completed" and row["payload"].get("cycle") == cycle
                                          for row in self.ledger.read()):
                raise RuntimeError("cycle already recorded complete without a committed completion record")
            if resume:
                self._restore_resume_registry(cycle)
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
            report = self._run_cycle(cycle, proposal_batch=proposal_batch)
            return self._publish_cycle_completion(cycle, report)
        except BaseException as exc:
            recording_errors = []
            committed = False
            try:
                committed = "completion" in self._journal.state
                if not committed:
                    self._journal.save(status="interrupted", error_type=type(exc).__name__)
            except BaseException as failure:
                recording_errors.append(("checkpoint", failure))
            try:
                if not committed:
                    self._ledger("cycle_interrupted", {"cycle": cycle, "error_type": type(exc).__name__})
            except BaseException as failure:
                recording_errors.append(("ledger", failure))
            if recording_errors:
                exc.cycle_recording_errors = tuple(recording_errors)
            raise
        finally:
            self._journal = None
            self._resuming = False

    def _restore_resume_registry(self, cycle: int) -> None:
        """Re-establish the state a resumed cycle started from.

        Two things can be stale after an interruption: the registry may already
        contain this cycle's own admission, and the harness parent may have
        been advanced by this cycle's provisional manifest. Both are reverted
        so the resumed run reproduces the interrupted attempt rather than
        measuring against its own partial output.
        """
        journal = self._journal
        if journal is None:
            return
        recorded = journal.state.get("registry_before") or {}
        expected = str(recorded.get("content_hash") or "")
        current = self.registry.registry_hash
        if expected and expected != current:
            self.registry.restore(
                recorded,
                reason=(
                    f"resume cycle {cycle}: revert this cycle's uncommitted "
                    "admission"
                ),
            )
            self._ledger("resume_registry_restored", {
                "cycle": cycle,
                "restored_hash": expected,
                "previous_hash": current,
            })
        # The parent recorded when this cycle first wrote its manifest.
        if "harness_parent_digest" in journal.state:
            recorded_parent = journal.state.get("harness_parent_digest")
            current_manifest = self._previous_manifest
            if recorded_parent is None:
                self._previous_manifest = None
            elif (
                current_manifest is None
                or str(current_manifest.get("digest") or "") != str(recorded_parent)
            ):
                # The manifest this cycle was going to build on is no longer on
                # disk; report an honest first-manifest diff instead of a wrong
                # parent.
                self._previous_manifest = None

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

        # Coverage is a property of the baseline, not a hypothesis to spend
        # proposer or defect-arm budget on. Run even when auditing is disabled.
        active = self.registry.registry
        active_fresh = requires_fresh_evaluation(self.registry.entries)
        baseline_sentinel = arms.run(self.split.sentinel, registry=active, fresh=active_fresh)
        coverage = sentinel_coverage(baseline_sentinel, self.config)
        if not coverage["evaluable"]:
            return self._stop_preflight(cycle, {
                "reason": "insufficient baseline-correct sentinel coverage: " + coverage["reason"],
                "sentinel": coverage,
            }, provider_stage=active_fresh)

        audit = self._journal.state.get("audit")
        if audit is None or self._audit_should_be_retried(audit):
            attempts = int((self.config.get("audit") or {}).get("max_attempts", 2))
            audit = None
            for attempt in range(max(1, attempts)):
                audit = self._audit(cycle, arms)
                audit["attempt"] = attempt + 1
                if audit.get("retryable") and attempt + 1 < max(1, attempts):
                    # An inconclusive audit rests on rows nobody could measure
                    # -- a transient provider state, not a property of the
                    # gates. One bounded re-measurement is the difference
                    # between "the gates could not be exercised now" and "this
                    # work_root can never run": the first real multi-cycle run
                    # froze on a single gain-set case that timed out, and a
                    # cached inconclusive verdict would have made that
                    # permanent for the work_root.
                    continue
                break
            self._journal.save(audit=audit)
        if audit.get("frozen"):
            report = {
                "cycle": cycle,
                "state": "frozen_after_defect_injection_audit",
                "audit": audit,
            }
            return report

        patrol = prepare_patrol(
            cycle=cycle, split=self.split, budget=self.config.get("budget") or {}, arms=arms,
            evaluate=self._patrol_arms, find_families=self._all_families, extend=self._extend_patrol,
        )
        patrol_size = patrol.initial_size
        patrol_cases = patrol.cases
        patrol_rows = patrol.rows
        patrol_contrast = patrol.contrast
        bootstrap_cases = patrol.bootstrap_cases
        route_failure_records = load_route_failures(self.route_failure_store)
        preparation = prepare_families(
            patrol.families, patrol_rows, dataset_root=self.config["dataset_root"],
            causal_policy=self._causal_policy(), route_failure_records=route_failure_records,
            runtime_identity=backend_runtime_identity(self.backend),
        )
        diagnosed_families = preparation.families
        mechanism_excluded = preparation.excluded
        mechanism_unrepresented = preparation.unrepresented
        capability_gaps = preparation.capability_gaps
        capability_plan = preparation.capability_plan
        worksheet = worksheet_report(
            patrol_rows,
            diagnosed_families,
            unrepresented=mechanism_unrepresented,
            excluded=mechanism_excluded,
        )
        worksheet["patrol_registry"] = self._patrol_registry_mode()
        worksheet["failure_contrast"] = patrol_contrast
        worksheet["bootstrap"] = {
            "initial_patrol_size": patrol_size,
            "bootstrap_cases": bootstrap_cases,
            "bootstrap_count": len(bootstrap_cases),
            # True when the reruns produced at least one family that survived
            # mechanism profiling and actionability classification. Whether the
            # round can *score* it is decided later, against the gain arm's
            # coverage: read `evaluation_preflight.gain_measured` beside this
            # flag, or the round's `instrument.gain_measured`, before treating
            # a found family as a usable one.
            "family_found": bool(diagnosed_families),
            "family_found_basis": (
                "actionable families after mechanism profiling; gain coverage "
                "is checked separately in evaluation_preflight"
            ),
            "family_count": len(diagnosed_families),
        }
        worksheet["capability_gaps"] = capability_gaps
        worksheet["capability_plan"] = capability_plan
        worksheet["route_failures"] = route_failure_summary(
            route_failure_records, runtime_identity=backend_runtime_identity(self.backend),
        )
        patrol_measurement_counts: dict[str, int] = {}
        for row in patrol_rows:
            status = str(row.get("measurement") or "missing")
            patrol_measurement_counts[status] = (
                patrol_measurement_counts.get(status, 0) + 1
            )
        unmeasured_patrol_cases = sorted({
            str(row.get("case") or "")
            for row in patrol_rows
            if (
                str(row.get("measurement") or "") != "authoritative"
                or str(row.get("authority") or "") != "authoritative"
                or row.get("prediction") is None
            )
        } - {""})
        worksheet["measurement_coverage"] = {
            "cases": len(patrol_rows),
            "authoritative": len(patrol_rows) - len(unmeasured_patrol_cases),
            "unmeasured": len(unmeasured_patrol_cases),
            "unmeasured_cases": unmeasured_patrol_cases,
            "status_counts": patrol_measurement_counts,
        }
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
            # The preflight reader gets the honest partial answer: the active
            # registry has been rolled once and no candidate has been drafted,
            # so either the run is a frozen replay (a known zero) or a provider
            # stage is pending with its reroll not yet taken (unknown).
            noise=noise_floor(provider_stage=active_fresh),
        )

        proposal_families, blocked_families = self._measurable_families(
            diagnosed_families, baseline_gain, arms,
            gain_measured=instrument["gain_measured"],
        )
        preflight = {"sentinel": coverage, "blocked_families": blocked_families,
                     "gain_opportunity": instrument["gain_opportunity"],
                     "gain_measured": instrument["gain_measured"],
                     "unmeasured_gain_cases": instrument["unmeasured_gain_cases"],
                     "patrol_measurement": worksheet["measurement_coverage"]}
        self._ledger("evaluation_preflight", {"cycle": cycle, **preflight})
        if (
            not diagnosed_families
            and not capability_gaps
            and unmeasured_patrol_cases
        ):
            reason = (
                "patrol inconclusive: "
                f"{len(unmeasured_patrol_cases)} of {len(patrol_rows)} case(s) "
                "were not measured authoritatively; the absence of a stable "
                "failure family cannot be concluded"
            )
            report = self._stop_preflight(
                cycle,
                {**preflight, "reason": reason},
                baseline_gain=baseline_gain,
                audit=audit,
                patrol=worksheet,
                provider_stage=active_fresh,
                state="blocked_patrol_measurement",
            )
            report["capability_backlog"] = self.record_capability_gaps(
                cycle, capability_gaps,
            )
            report["capability_plan"] = capability_plan
            if capability_plan.get("gaps"):
                record_gaps(
                    self.gap_store, cycle=cycle,
                    gaps=capability_plan["gaps"],
                )
            report["route_failures"] = self.record_route_failures(
                cycle=cycle,
                diagnosed_families=diagnosed_families,
                gate_reports=(),
            )
            return report
        if diagnosed_families and not proposal_families and proposal_batch is None:
            reason = "no diagnosed failure direction has repair opportunities in gain"
            if not instrument["gain_measured"]:
                reason = (
                    "the gain arm produced no measurable decision this round "
                    f"({len(instrument['unmeasured_gain_cases'])} unmeasured "
                    "case(s)); no repair opportunity could be read"
                )
            report = self._stop_preflight(cycle, {
                **preflight, "reason": reason,
            }, baseline_gain=baseline_gain, audit=audit, patrol=worksheet,
               provider_stage=active_fresh)
            # H_t bookkeeping for the round that stops here. The early return
            # used to skip the calls further down, so a round that found a
            # capability gap *and* an unmeasurable family reported the gap in
            # its worksheet and then dropped it: the next round had to
            # rediscover it, and nothing in the repository recorded that the
            # capability was missing. Only the accounting is added here -- the
            # stop path itself is unchanged.
            report["capability_backlog"] = self.record_capability_gaps(
                cycle, capability_gaps,
            )
            report["capability_plan"] = capability_plan
            if capability_plan.get("gaps"):
                record_gaps(
                    self.gap_store, cycle=cycle,
                    gaps=capability_plan["gaps"],
                )
            # No candidate reached a gate, so there is no route-falsification
            # reading to store; the empty call still records the attempt in the
            # ledger, which is what tells "no route failed" apart from "this
            # path never asked".
            report["route_failures"] = self.record_route_failures(
                cycle=cycle,
                diagnosed_families=diagnosed_families,
                gate_reports=(),
            )
            return report

        development_block: dict[str, Any] | None = None
        # Whether the loop's batch is what this round carries to the gates.
        # Kept apart from ``development_block``, which is the loop's *record*:
        # a round can enable the loop, find no mechanism it may develop, and
        # fall back to single-shot drafting. It then has a development record
        # describing why nothing was developed, while the candidates that reach
        # the gates came from the proposer -- and reporting that round as
        # "development" would misdescribe which path produced them. The ledger
        # is the cross-check: it records a ``development`` event only when the
        # loop's batch is adopted, so the two readings agree.
        development_used = False
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
            # Reusable parts from the component store (§9.1 layer 2), retrieved
            # for the mechanisms this round can act on. Retrieval is bounded
            # for the same reason memory retrieval is: the drafting prompt must
            # not grow with the store.
            components = self._reusable_components(diagnosed_families)
            if hasattr(self.proposer, "set_components"):
                self.proposer.set_components(components)
            self._ledger("proposal_memory", {
                "cycle": cycle,
                "procedures": len(memory_context["procedures"]),
                "open_gaps": len(memory_context["open_capability_gaps"]),
                "recurring_gaps": len(memory_context["recurring_capability_gaps"]),
                "components": len(components),
            })
            if self.development_enabled:
                # §23.3.6: with the loop on, the round's candidates are the
                # versions that passed L0 and L1, not the first drafts. The
                # empty batch is deliberate -- a round whose developed versions
                # were all falsified has *measured* that, and letting the
                # single-shot proposer draft again would silently restore the
                # behaviour this path exists to replace. `--resume` reuses the
                # persisted batch, so the loop is not re-entered either.
                developed, development_block = self._develop_candidates(
                    cycle=cycle,
                    families=proposal_families,
                    capability_plan=capability_plan,
                )
                if developed is not None:
                    proposal_batch = developed
                    development_used = True
                    self._ledger("development", {
                        "cycle": cycle, **{
                            key: value for key, value in development_block.items()
                            if key not in {"runs"}
                        },
                    })
            if proposal_batch is None:
                proposal_batch = self.proposer.propose(
                    proposal_families,
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
        if proposal_batch is None:  # pragma: no cover - defensive, see below
            raise RuntimeError("no proposal batch was produced this cycle")
        if development_block is None:
            # A resumed cycle restores its batch from the checkpoint and never
            # re-enters drafting, so the development record -- and which path it
            # describes -- have to come back from there too. Without this, a
            # resumed development round would describe itself as single-shot,
            # exactly the distinction the field exists to make, inverted.
            restored = self._journal.state.get("development")
            development_block = restored if isinstance(restored, Mapping) else None
            development_used = bool(self._journal.state.get("development_used"))
        self._journal.save(proposing=False, proposal={
            "candidates": proposal_batch.candidates, "joint_groups": proposal_batch.joint_groups,
            "notes": proposal_batch.notes},
            development=development_block, development_used=development_used)
        # Which path this round took, read from the run rather than asserted:
        # `single_shot` is one draft carried to the gates, `development` is
        # draft -> L0 -> L1 -> revise with only a certified version admitted to
        # them. A reader of the cycle report must be able to tell these apart,
        # because the readings are not comparable: a gate rejection under
        # development has already survived a targeted replay.
        round_path = "development" if development_used else "single_shot"
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
        # Whether anything this round measured came from a live provider stage
        # rather than a frozen replay. It decides how the round's own jitter has
        # to be *reported*: a frozen replay is a known zero, a provider stage
        # with only one roll is an unknown.
        provider_stage_round = bool(active_fresh or has_provider_stage)
        if isinstance(self.backend, LiveDetectorBackend):
            # A frozen judge does not freeze scanner, upgrade, tracer or
            # defense.  Downstream hooks can still issue live provider
            # requests, so an unrerolled comparison cannot claim zero jitter.
            provider_stage_round = provider_stage_round or any(
                str(entry.get("hook") or "") != "adjudicate.guard"
                for entry in [*self.registry.entries, *proposals]
            )
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
        # The noise floor is the only reading in the round that says how much
        # the instrument moved on its own. Two rolls of the same active
        # configuration give it directly; the cases those rolls disagreed on
        # are the ones no single-case claim may rest on. Both were already
        # measured before this point -- they are now carried to the gates and
        # into the report instead of being computed and dropped.
        noise = noise_floor(
            reroll=reroll_noise,
            flipped=(
                flipped_cases(active_rows, fresh_control_rows)
                if reroll_noise is not None and fresh_control_rows is not None
                else ()
            ),
            provider_stage=provider_stage_round,
        )
        # ``None`` -- not ``[]`` -- when no reroll was taken. The distinction is
        # load-bearing at the gates: an empty list means "the instrument was
        # rolled twice and nothing moved", while ``None`` means "nobody looked".
        # Only the first licenses scoring a delta as if its cases were stable.
        unstable_cases: list[str] | None = None
        if str(noise.get("status") or "") == NOISE_FLOOR_MEASURED:
            unstable_cases = [
                str(case) for case in noise.get("flipped_cases") or ()
            ]

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
                unstable_cases=unstable_cases,
                arms=arms,
            )
            report.update({
                "harmlessness": unit["harmlessness"],
                "behavior": unit["behavior"],
                "m": unit["m"],
                "fresh": unit["fresh"],
                "candidate_rows": unit["candidate_rows"],
                "unmeasured_cases": unit.get("unmeasured_cases") or [],
                "gain_opportunities": unit["gain_opportunities"],
                "mechanism_opportunity": unit.get("mechanism_opportunity"),
                "measurement_gain_cases": unit["measurement_gain_cases"],
                "target_outcome": unit.get("target_outcome"),
                "route_measurement": unit.get("route_measurement"),
                "localization_opportunities": unit.get("localization_opportunities"),
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
                and not unit.get("unmeasured_cases")
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
            # Skipped joint units also propagate their unmeasured M outcome
            # to every member, without inventing a harmlessness verdict.
            report.update({
                "harmlessness": unit["harmlessness"],
                "behavior": (unit.get("member_behavior") or {}).get(identifier),
                "target_outcome": (unit.get("member_target_outcomes") or {}).get(identifier),
                "route_measurement": unit.get("route_measurement"),
                "localization_opportunities": unit.get("localization_opportunities"),
                "m": unit["m"],
                "fresh": unit["fresh"],
                "candidate_rows": unit["candidate_rows"],
                "gain_opportunities": unit["gain_opportunities"],
                "mechanism_opportunity": unit.get("mechanism_opportunity"),
                "measurement_gain_cases": unit["measurement_gain_cases"],
                "coverage_response": unit.get("coverage_response"),
            })

        for report in gate_reports:
            report["evaluation"] = evaluation_summary(report)

        # One combination arm when several units qualify on their own; this
        # is the only additional sweep the protocol needs. A unit whose
        # members form a joint group is evaluated jointly above and never
        # enters the combination.
        combination = self._check_combination(
            qualified,
            active_entries=self.registry.entries,
            fresh_control_rows=fresh_control_rows,
            active_rows=active_rows,
            unstable_cases=unstable_cases,
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
                "delta": feedback_delta(combination.get("delta")),
                "not_evaluable": bool(combination.get("stability_limited")),
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
                        "delta": feedback_delta(verdict.get("delta")),
                        "gain_opportunities": report.get("gain_opportunities"),
                        "behavior_delta": feedback_delta((report.get("behavior") or {}).get("delta")),
                        "behavior_note": (report.get("behavior") or {}).get("behavior_note"),
                        "evaluation_state": (report.get("evaluation") or {}).get("state"),
                        "next_action": (report.get("evaluation") or {}).get("next_action"),
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
            # Which path this round's candidates travelled (plan §23.3.6).
            # Reported even when the loop is off, because "single_shot" is the
            # reading a later comparison needs and an absent field cannot be
            # told apart from a version of the framework that had no such
            # concept.
            "round_path": round_path,
            "development": development_block,
            "preflight": preflight,
            "split": split_manifest_report(self.split),
            "audit": audit,
            "patrol": worksheet,
            "instrument": self._instrument_report(
                baseline_gain=baseline_gain,
                gate_reports=gate_reports,
                blocked_families=blocked_families,
                admitted_ids={
                    str(c.get("intervention_id") or "") for c in selected
                },
                noise=noise,
            ),
            "evidence": self._evidence_report(),
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
                        "cases_flipped": len(
                            noise.get("flipped_cases") or ()
                        ),
                    },
                    # The same reading the instrument block carries, kept here
                    # as well so a reader following one block does not have to
                    # find the other. `status` is what distinguishes a measured
                    # zero from a round that never took a second roll.
                    "noise_floor": dict(noise),
                }
            ),
            "combination": {
                "unit_ids": combination["unit_ids"],
                "delta": combination["delta"],
                "reason": combination["reason"],
                "stability_limited": bool(combination.get("stability_limited")),
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
        # The planner's reading of why each family was or was not treatable,
        # recorded as backlog items so "a route exists but the harness cannot
        # use it" becomes a standing, countable gap instead of a quiet round.
        report["capability_plan"] = capability_plan
        if capability_plan.get("gaps"):
            record_gaps(
                self.gap_store, cycle=cycle, gaps=capability_plan["gaps"],
            )
        # Route falsification: a route that fired, changed observable
        # behaviour, and moved no target outcome on a set where it could have
        # been rewarded. The write side of the memory the planner reads above.
        report["route_failures"] = self.record_route_failures(
            cycle=cycle,
            diagnosed_families=diagnosed_families,
            gate_reports=gate_reports,
        )
        self._record_cycle_experience(
            cycle=cycle,
            diagnosed_families=diagnosed_families,
            gate_reports=gate_reports,
            rejected=cycle_rejections,
            selected=selected,
        )
        report["harness"] = self.record_harness(cycle)
        return report

    def _check_combination(
        self,
        qualified: Sequence[Mapping[str, Any]],
        *,
        active_entries: Sequence[Mapping[str, Any]],
        fresh_control_rows: Sequence[Mapping[str, Any]] | None,
        active_rows: Sequence[Mapping[str, Any]],
        unstable_cases: Sequence[str] | None = None,
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
            unstable_cases=(
                None if unstable_cases is None else list(unstable_cases)
            ),
        )
        control_g = _subset_rows(control, self.split.gain)
        combo_g = _subset_rows(combo_rows, self.split.gain)
        delta = paired_delta(
            control_g, combo_g,
            unstable_cases=(
                None if unstable_cases is None else list(unstable_cases)
            ),
        )
        passed_m, failures = meets_m(delta, config=self.config)
        passed = combo_harmlessness["passed"] and passed_m
        if not combo_harmlessness["passed"]:
            failures = [combo_harmlessness["reason"], *failures]
        # A combination that only fails because its whole gain sits on cases
        # the control configuration reads two ways is not evidence that the
        # members interfere; it is a round that cannot score them together.
        stability_limited = bool(
            not passed_m
            and marginal_floor_rests_on_unstable(delta, config=self.config)
        )
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
                "stability_limited": False,
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
            "stability_limited": stability_limited,
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
            gold_step_map=self.gold_step_map,
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
    @_engine_operation
    def run_experiment(self, cycles: int | None = None, *, resume: bool = False) -> dict[str, Any]:
        import time as _time

        requested = int(self.config.get("cycles", 3) if cycles is None else cycles)
        cycles = requested
        if cycles < 0:
            raise ValueError("cycles must be non-negative")
        for cycle in range(1, cycles + 1):
            started = any(r["event"] == "cycle_started" and r["payload"].get("cycle") == cycle for r in self.ledger.read())
            result = self.run_cycle(cycle, resume=resume and started)
            if result.get("state") != "completed":
                break
        completed = [
            item for item in self.cycle_reports
            if item.get("state") == "completed"
        ]
        # A run that stopped early must say so. Otherwise a six-cycle request
        # that ended after one blocked preflight is indistinguishable from a
        # six-cycle run by anyone reading the report -- the cycles list is the
        # only trace, and counting it is not a contract.
        stopped_early = len(completed) < requested
        report = {
            "cycles": self.cycle_reports,
            "cycles_requested": requested,
            "cycles_completed": len(completed),
            "stopped_early": stopped_early,
            "stop_reason": (
                str(self.cycle_reports[-1].get("state") or "") if stopped_early
                and self.cycle_reports else ""
            ),
            "active": [
                str(c.get("intervention_id") or "") for c in self.registry.entries
            ],
            "ledger_integrity": self.ledger.verify(),
            "registry_state": self.registry.state,
        }
        if self.mini and self.split.validation:
            if all(item.get("state") == "completed" for item in self.cycle_reports):
                report["mini_effect"] = self._evaluate_mini_effect()
            else:
                # "Not measured" must be stated, not implied by a missing key:
                # a reader that treats an absent field as success would read a
                # skipped out-of-sample check as a passed one.
                report["mini_effect"] = {
                    "status": "not_run",
                    "reason": (
                        "the out-of-sample effect is only computed when every "
                        "cycle completed; stopped with "
                        f"{completed and len(completed) or 0} completed cycle(s)"
                    ),
                    "passed": None,
                }
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
            gold_step_map=self.gold_step_map,
            workers=workers,
            deadline=deadline,
        )

    # ------------------------------------------------------------------
    @_engine_operation
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
                # A truncated arm counts unresolved as "not correct", so a
                # cut-off active arm looks like a *gain* over the empty one --
                # and the leave-one-out direction flips with it: the entry
                # reads as harmful when it was never measured. Attribution is
                # a measurement, so an unmeasurable arm records nothing.
                blocked = arm_has_infrastructure_failure(
                    [*without_rows, *active_rows],
                )
                attribution.append({
                    "intervention_id": intervention_id,
                    "delta": delta,
                    "attribution": "leave_one_out_committed_configuration",
                    "measured": not blocked,
                    "unmeasured_reason": blocked,
                })
        for item in attribution:
            if not item["measured"]:
                continue
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
            # Reported explicitly so "no entry was retired because nothing was
            # measurable" is distinguishable from "every entry is earning its
            # place". Both leave `retired` empty.
            "attribution_measured": all(
                bool(item.get("measured")) for item in attribution
            ),
            "unmeasured_attribution": [
                item["intervention_id"] for item in attribution
                if not item.get("measured")
            ],
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
            # Count distinct cycles, not rows. Both the admission path and the
            # governance path append to this history, so one cycle can leave
            # two rows: a row slice would then read a single measurement as
            # the threshold's worth of evidence and retire on one bad round.
            recent = _recent_cycles(history, threshold)
            if len(recent) >= threshold and all(
                int(item.get("marginal_cases", 0)) <= 0 for item in recent
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
        # A truncated active arm reads as *worse than nothing*: unresolved
        # counts as incorrect, so an arm the deadline cut short records a
        # negative net contribution it did not earn, and two such rounds
        # trigger the rollback that removes a working library. Rollback is
        # irreversible from this path, so an unmeasurable comparison records
        # nothing at all.
        blocked = arm_has_infrastructure_failure([*empty_rows, *active_rows])
        if blocked:
            return {
                "triggered": False,
                "measured": False,
                "unmeasured_reason": blocked,
                "threshold": threshold,
                "history": list(
                    self.registry.state.get("net_contribution_history") or []
                )[-threshold:],
            }
        net = paired_delta(empty_rows, active_rows)
        self.registry.record_net_contribution(
            int(net["marginal_cases"]), cycle=cycle,
        )
        history = list(self.registry.state.get("net_contribution_history") or [])
        recent = _recent_cycles(history, threshold)
        if len(recent) >= threshold and all(
            int(item.get("net_correct_cases", 0)) < 0 for item in recent
        ):
            payload = self.registry.rollback_to_snapshot(cycle - threshold)
            # A snapshot that does not exist restores nothing. Reporting
            # "triggered" anyway would record a rollback that never happened
            # -- and the usual cause (the window reaching back before the
            # first snapshot) is not rare in a run with early zero-admission
            # cycles.
            restored = bool(payload)
            self._ledger("rolled_back", {
                "cycle": cycle,
                "reason": (
                    f"empty-vs-active net contribution negative for the last "
                    f"{threshold} cycles"
                ),
                "restored_snapshot": (
                    str(payload.get("content_hash") or "") if payload else ""
                ),
                "triggered": restored,
                "cause": "" if restored else (
                    f"no snapshot to restore at cycle {cycle - threshold}"
                ),
            })
            return {
                "triggered": restored,
                "measured": True,
                "threshold": threshold,
                "history": history[-threshold:],
                "unrestorable_reason": "" if restored else (
                    f"no snapshot to restore at cycle {cycle - threshold}; "
                    "the rollback window reaches back before the first "
                    "snapshot"
                ),
            }
        return {
            "triggered": False,
            "measured": True,
            "threshold": threshold,
            "history": history[-threshold:],
        }

    # ------------------------------------------------------------------
    @_engine_operation
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
        decisive_fixed = int(effect.get("decisive_fixed") or 0)
        decisive_broken = int(effect.get("decisive_broken") or 0)
        # Margin without a repaired decision is a calibration or abstention
        # effect, not a repaired mechanism: the case count can rise purely by
        # deciding cases the empty arm left unresolved. Requiring at least one
        # decisive correction keeps the criterion about the mechanism.
        main_pass = (
            int(effect["marginal_cases"]) >= int(
                (self.config.get("gates") or {}).get("m_min_marginal_cases", 1)
            )
            and decisive_fixed > 0
            and decisive_broken == 0
            and int(effect["new_errors"]) == 0
            and int(effect["correctness_worsened"]) == 0
            and int(effect["unresolved_added"]) == 0
            and int(effect["health_regressed"]) == 0
        )
        p_value = mcnemar_improvement_p(effect)
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
                "decisive_fixed": decisive_fixed,
                "decisive_broken": decisive_broken,
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
                "decisive_corrections": decisive_fixed,
                "zero_decisive_breakage": decisive_broken == 0,
                "health_not_regressed": int(effect["health_regressed"]) == 0,
                "overall": (
                    "pass"
                    if main_pass and p_value < 0.05
                    and decisive_fixed > 0
                    and decisive_broken == 0
                    and int(effect["unresolved_added"]) <= 0
                    and bool(step_check["not_regressed"])
                    and bool(self.registry.entries)
                    else "fail"
                ),
                "statistical_power_note": (
                    "the McNemar test counts decisive flips (a real decision "
                    "replaced by a different one), not abstention changes: "
                    "with n=300 and a symmetric paired test, one-sided p<0.05 "
                    "requires at least 5 net decisive corrections and zero "
                    "decisive breakage; a small true effect can be real yet "
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

    def _publish_cycle_completion(self, cycle: int, report: Mapping[str, Any]) -> dict[str, Any]:
        if self._journal is None:
            raise RuntimeError("cycle completion requires its journal")
        report = json.loads(json.dumps(dict(report), ensure_ascii=False, allow_nan=False))
        completion = {
            "schema_version": "evolution.cycle_completion.v1",
            "contract_digest": digest(self._journal.state["contract"]),
            "registry_hash": self.registry.registry_hash,
            "report": report,
            "report_hash": digest(report),
            "event": {"cycle": cycle, "state": report["state"],
                      "active_registry_hash": self.registry.registry_hash},
        }
        completion["digest"] = digest(completion)
        self._journal.save(status="completed", completion=completion)
        return self._project_cycle_completion(cycle, completion)

    def _project_cycle_completion(self, cycle: int, completion: Mapping[str, Any]) -> dict[str, Any]:
        report = completion["report"]
        path = self.paths["cycles"] / f"cycle{cycle:03d}.json"
        key = digest({"completion_contract": completion["contract_digest"], "event": "cycle_completed"})
        events = [row for row in self.ledger.read()
                  if row["event"] == "cycle_completed" and row["payload"].get("cycle") == cycle]
        if len(events) > 1 or any(row.get("idempotency_key") != key or digest(row["payload"]) != digest(completion["event"])
                                 for row in events):
            raise RuntimeError("cycle already recorded complete with conflicting publication")
        if path.exists() and digest(json.loads(path.read_text(encoding="utf-8"))) != completion["report_hash"]:
            raise RuntimeError("cycle report conflicts with committed completion")
        if (not events or not path.exists()) and self.registry.registry_hash != completion["registry_hash"]:
            raise RuntimeError("cycle already recorded complete, but deployment changed before publication recovery")
        self._save_cycle_report(cycle, report)
        self._ledger("cycle_completed", completion["event"], idempotency_key=key)
        if not any(row.get("cycle") == cycle for row in self.cycle_reports):
            self.cycle_reports.append(json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False)))
        return json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))

    def _save_cycle_report(self, cycle: int, report: Mapping[str, Any]) -> None:
        path = self.paths["cycles"] / f"cycle{int(cycle):03d}.json"
        if not path.exists():
            save_json(path, dict(report))


def _time_monotonic() -> float:
    import time

    return time.monotonic()


def _recent_cycles(history: Sequence[Mapping[str, Any]], count: int) -> list[Mapping[str, Any]]:
    """The most recent ``count`` distinct cycles in a history, newest first.

    Retirement and rollback thresholds are written in cycles ("negative for
    the last two cycles"), but the histories they read are append-only rows:
    one cycle can contribute several rows, and a repeated governance call for
    the same cycle adds more. Slicing rows lets a single measurement fill the
    whole window. Keeping the newest row per cycle is what the thresholds
    mean.
    """
    seen: dict[int, Mapping[str, Any]] = {}
    order: list[int] = []
    for item in history:
        cycle = int(item.get("cycle") or 0)
        if cycle not in seen:
            order.append(cycle)
        seen[cycle] = item
    return [seen[cycle] for cycle in order[-count:]]


def _step_localization_check(
    dataset_root: str,
    baseline_rows: Sequence[Mapping[str, Any]],
    active_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Whether the responsible step survived, reported per step kind.

    ``n``/``baseline_hits``/``active_hits``/``not_regressed`` keep their exact
    previous definitions and the verdict still reads only those. Added beside
    them: the same hits split by whether the blamed step is an action step (the
    event carries only tool calls) or a content step (it carries the
    assistant's own prose). The kind is read off the arm's own envelope when it
    recorded ``responsible_step_kind``; for a row written before that field
    existed it is classified from the same dataset trajectory this function
    already reads for the gold step -- the same mechanical rule on the same
    bytes, never a guess about the row's numbers. A step that is neither
    (unclassifiable) is reported as such rather than folded into either side.
    """
    base_map = {str(r.get("case") or ""): r for r in baseline_rows}
    hit_base = hit_active = n = errors = 0
    kinds: dict[str, dict[str, int]] = {
        side: {"scorable": 0, "hits": 0} for side in ("baseline", "active")
    }
    by_kind: dict[str, dict[str, dict[str, int]]] = {
        kind: {side: {"scorable": 0, "hits": 0} for side in ("baseline", "active")}
        for kind in METRIC_STEP_KINDS
    }
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
            base = base_map.get(case)
            active_hit = (
                row.get("prediction") is True
                and (row.get("result") or {}).get("responsible_step") == gold_step
            )
            base_hit = bool(
                base
                and base.get("prediction") is True
                and (base.get("result") or {}).get("responsible_step") == gold_step
            )
            hit_active += int(active_hit)
            hit_base += int(base_hit)
            for side, arm, hit in (
                ("baseline", base, base_hit), ("active", row, active_hit),
            ):
                if arm is None:
                    continue
                kind = row_responsible_step_kind(arm)
                if kind == STEP_KIND_UNCLASSIFIED:
                    kind = _step_kind_from_record(raw, row_responsible_step(arm))
                if kind not in by_kind:
                    continue
                kinds[side]["scorable"] += 1
                kinds[side]["hits"] += int(hit)
                by_kind[kind][side]["scorable"] += 1
                by_kind[kind][side]["hits"] += int(hit)
        except Exception:
            errors += 1
    split: dict[str, Any] = {}
    for kind in METRIC_STEP_KINDS:
        for side in ("baseline", "active"):
            bucket = by_kind[kind][side]
            scorable, hits = bucket["scorable"], bucket["hits"]
            split[f"{side}_{kind}_scorable"] = scorable
            split[f"{side}_{kind}_hits"] = hits
            split[f"{side}_{kind}_accuracy"] = (
                (hits / scorable) if scorable else None
            )
    return {
        "n": n,
        "errors": errors,
        "baseline_hits": hit_base,
        "active_hits": hit_active,
        "not_regressed": bool(n > 0 and errors == 0 and hit_active >= hit_base),
        # Reporting only. Nothing below feeds ``not_regressed``.
        "step_kind_split": split,
        "baseline_classified": kinds["baseline"]["scorable"],
        "active_classified": kinds["active"]["scorable"],
        "note": (
            "action-step and content-step accuracy answer different "
            "questions (finding the right tool call vs the right sentence) "
            "and must not be averaged into one localization number"
        ),
    }


def _step_kind_from_record(
    raw: Mapping[str, Any], step: int | None,
) -> str:
    """Classify a step from the raw dataset record's own event structure.

    Used only when an arm's envelope predates ``responsible_step_kind``. It
    applies ``detector.packet.step_kind`` to the packet built from the same
    ``{question, history}`` projection the detector itself ingests, so the
    answer is the same mechanical rule on the same bytes -- not an inference
    from the row's numbers and not a string heuristic.
    """
    if step is None:
        return STEP_KIND_UNCLASSIFIED
    try:
        from detector.packet import build_packet, step_kind
        from detector.dataset import project_record
    except Exception:
        return STEP_KIND_UNCLASSIFIED
    try:
        packet = build_packet(project_record(raw))
    except Exception:
        return STEP_KIND_UNCLASSIFIED
    return step_kind(packet, step) or STEP_KIND_UNCLASSIFIED


def run_experiment(
    *,
    cycles: int | None = None,
    config: Mapping[str, Any] | None = None,
    backend: DetectionBackend | None = None,
    mini: bool = False,
) -> dict[str, Any]:
    with EvolutionEngine(config, backend=backend, mini=mini) as engine:
        return engine.run_experiment(cycles)


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
    with EvolutionEngine(cfg, backend=backend, mini=False) as engine:
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
                "source_digest": source_identity("detector", "evolution"),
                "runtime_identity": backend_runtime_identity(engine.backend),
            })
        cycle_reports = []
        target_cycles = int(cfg.get("cycles", 3))
        for cycle in range(1, target_cycles + 1):
            report = engine.run_cycle(cycle, resume=cycle in resumable or cycle in completed)
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
