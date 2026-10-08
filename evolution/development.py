"""Candidate development loop: draft, test cheaply, revise, then freeze.

The deployment side used to go straight from one proposal to the formal gates.
A candidate that failed was not revised: the rejection was recorded and the next
cycle drafted something else. Research runs, by contrast, have always revised --
draft, evaluate, diagnose, revise the same parent -- which is where the pilot's
working candidates came from. Two paths, two different notions of what a
candidate version is.

This module is the deployment-side loop the plan asks for (P0.3), built on the
same version store (``evolution.candidate_versions``) so both paths share one
lineage:

    draft v1 -> L0 structural checks -> L1 targeted replay -> diagnosis
             -> revise the parent -> ... -> freeze the best version

The two levels are the point. L0 is deterministic and costs nothing; L1 runs
only the stages a change can reach, using the hook dependency map, so a
candidate is measured before it is allowed to spend formal promotion budget.
Freezing is still the only thing that hands a candidate to the gates, and
``freeze`` refuses a version that has not passed L1 -- a candidate that breaks a
normal neighbour must not reach a frozen protocol.

Nothing here admits, rejects or scores: it produces candidates and development
evidence, and the gates remain the only authority.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from detector.interventions import validate_intervention
from evolution.backends import partial_replay_plan
from evolution.candidate_versions import CandidateVersionGraph, canonical_hash
from evolution.contracts import (
    candidate_contains_case_id,
    identity_incompleteness,
    structured_key,
)
from evolution.gates import arm_has_infrastructure_failure
from evolution.metrics import PairedCoverageError, paired_delta, row_responsible_step
from evolution.mechanisms import repair_requirements
from evolution.metamorphic import admission_error
from evolution.route_failures import route_of

DEVELOPMENT_SCHEMA = "evolution.candidate_development.v1"

# How a version ended. The vocabulary lives with the version graph
# (``candidate_versions.VERSION_STATUSES``), which is the module that writes it;
# a second copy here would be free to drift from the one on disk.


def _batch_reason(batch: Any) -> dict[str, Any]:
    """The proposer's own explanation for an empty batch, if it gave one.

    A ``ProposalBatch`` carries ``notes``; a caller that injects a plain
    callable may return a bare sequence, which has no notes and yields an empty
    report rather than an error. Only the first note is kept: one empty answer
    carries one reason, and the caller sees a bounded record rather than a
    prompt's worth of notes. ``stopped_reason`` is the note's coarse,
    machine-readable half -- "the proposer's own call budget was spent" and "it
    was asked and produced nothing" are the two a reader must be able to tell
    apart -- and is derived from the note rather than invented, so a proposer
    with a new kind of refusal is reported as ``other`` with its text intact.
    """
    notes = getattr(batch, "notes", None) or ()
    for item in notes:
        if not isinstance(item, Mapping):
            continue
        record = dict(item)
        text = " ".join(
            str(record.get(key) or "") for key in ("reason", "rejected")
        ).lower()
        if "budget" in text or "exhausted" in text:
            record["stopped_reason"] = "budget_exhausted"
        elif "provider_failure" in text or record.get("provider_ok") is False:
            record["stopped_reason"] = "provider_failure"
        elif str(record.get("reason") or "").strip():
            record["stopped_reason"] = "no_draft_returned"
        else:
            record["stopped_reason"] = "other"
        return record
    return {}


class DevelopmentUnmeasurable(Exception):
    """One replay could not score the version it was run for.

    Raised by a replay runner when the rows it produced carry an
    infrastructure failure -- a provider error, a deadline skip, a
    non-authoritative terminal -- so no honest comparison can be made. It is an
    exception rather than a value because the runner's contract is "rows to
    score"; a row set that cannot be scored is a different kind of answer, and
    returning it as rows is exactly how "not measured" was read as "no
    improvement" and recorded as a falsification.

    The loop catches it and marks the version ``unmeasured``: the candidate was
    never judged, so it must not be archived or remembered as one that failed.
    """

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "the replay produced no scorable rows")
        super().__init__(self.reason)


class DevelopmentBudget:
    """Caps for one development run, separate from the promotion budget.

    Development and promotion are different spends: development is meant to be
    repeated and cheap, promotion is frozen and expensive. Sharing one counter
    would let a long development loop consume the budget the formal protocol
    needs, so they are kept apart by construction.
    """

    def __init__(
        self,
        *,
        max_versions_per_mechanism: int = 4,
        max_provider_calls: int = 6,
        max_l1_cases: int = 12,
        require_normal_neighbors: int = 2,
        allow_action_class_change: bool = True,
    ) -> None:
        self.max_versions_per_mechanism = int(max_versions_per_mechanism)
        self.max_provider_calls = int(max_provider_calls)
        self.max_l1_cases = int(max_l1_cases)
        self.require_normal_neighbors = int(require_normal_neighbors)
        self.allow_action_class_change = bool(allow_action_class_change)
        self.provider_calls = 0
        self.l1_cases_used = 0

    def provider_calls_left(self) -> int:
        return self.max_provider_calls - self.provider_calls

    def spend_provider(self, count: int = 1) -> None:
        """Charge the provider budget for one model-backed step.

        Its unit is a call, not a case: the L1 replay's cost is capped
        separately by ``max_l1_cases``, and what this cap bounds is the number
        of actual provider requests reported by the proposer, including its
        internal format repairs. An empty model response still costs a call;
        a preflight refusal does not.
        """
        self.provider_calls += int(count)

    def l1_cases_left(self) -> int:
        return self.max_l1_cases - self.l1_cases_used

    def as_dict(self) -> dict[str, Any]:
        return {
            "caps": {
                "max_versions_per_mechanism": self.max_versions_per_mechanism,
                "max_provider_calls": self.max_provider_calls,
                "max_l1_cases": self.max_l1_cases,
                "require_normal_neighbors": self.require_normal_neighbors,
                "allow_action_class_change": self.allow_action_class_change,
            },
            "used": {
                "provider_calls": self.provider_calls,
                "l1_cases": self.l1_cases_used,
            },
        }


def check_l0(
    candidate: Mapping[str, Any],
    *,
    mechanism: Mapping[str, Any],
    active_entries: Sequence[Mapping[str, Any]] = (),
    normal_neighbors: Sequence[Mapping[str, Any]] = (),
    predicate_runner: Callable[[Mapping[str, Any], Mapping[str, Any]], str] | None = None,
    target_kind: str = "detection",
    target_cases: Sequence[str] = (),
    gold_steps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic pre-flight: everything checkable without a model call.

    L0 exists to spend no provider budget on a candidate that is invalid,
    redundant, aimed at the wrong target, or already wrong on a normal
    neighbour. It is not a weaker gate: it answers different questions, all of
    them cheap, and a candidate that fails here is never measured.
    """
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    # 1. Schema and executor validity, including the executable bounds a
    #    registry entry needs. Any exception is recorded rather than raised: a
    #    malformed candidate is the thing L0 exists to reject, and letting a
    #    validator's own error escape would abort the loop instead of failing
    #    this version.
    try:
        validate_intervention(candidate, require_executable_bounds=True)
        record("schema", True)
    except Exception as exc:  # noqa: BLE001 - recorded as this version's failure
        record("schema", False, f"{type(exc).__name__}: {exc}")

    # 2. No case identifier in prose. The gates enforce this too; failing it
    #    here means the candidate never reaches a measurement.
    leaked = candidate_contains_case_id(candidate)
    record(
        "no_case_id",
        leaked is None,
        f"case id {leaked!r} appears in directive/body/claim" if leaked else "",
    )

    # 2b. The named runner must have passed §15.2's metamorphic admission gate.
    #     Checked here as well as in STRUCTURAL because L0 is where a candidate
    #     is *developed*: a version drafted against an inadmissible executor
    #     should fail its development round instead of consuming an L1 replay
    #     first. The gate admits runners, not candidates -- it is not a
    #     competitor to the four gates and does not score anything.
    runner_error = admission_error(
        str((candidate.get("body") or {}).get("runner") or "")
    )
    record("metamorphic_runner", not runner_error, runner_error)

    # 3. Structured identity: the candidate must *have* an executable identity
    #    before it can be compared, and it must not be the same executable
    #    thing as something already deployed. Prose similarity is deliberately
    #    not tested here -- that is the STRUCTURAL gate's job, and duplicating
    #    it would reject candidates L0 cannot judge.
    #
    #    A learned runner with no pinned artifact is refused rather than
    #    compared: the framework cannot tell whether it duplicates a deployed
    #    intervention or merely resembles one, and "cannot tell" must not pass
    #    as "different" (§23.2).
    incomplete = identity_incompleteness(candidate)
    candidate_key = structured_key(candidate)
    conflict = ""
    if not incomplete and candidate_key is not None:
        for entry in active_entries:
            if identity_incompleteness(entry):
                continue
            if structured_key(entry) == candidate_key:
                conflict = str(entry.get("intervention_id") or "")
                break
    record(
        "structured_identity",
        not incomplete and not conflict,
        incomplete or (
            f"identical executable identity to {conflict!r}" if conflict else ""
        ),
    )

    # 4. The candidate targets the mechanism it claims to target: its repair
    #    operation has to reach the stage the mechanism actually failed at.
    requirement = repair_requirements(mechanism)
    hook = str(candidate.get("hook") or "")
    reaches = plan_reaches(hook, str(mechanism.get("detector_failure", {}).get(
        "stage") or ""))
    record(
        "targets_mechanism",
        reaches,
        (
            f"hook {hook!r} cannot reach the failing stage "
            f"{mechanism.get('detector_failure', {}).get('stage')!r} "
            f"(operation {requirement.get('operation')!r})"
        ) if not reaches else "",
    )

    # 5. Deterministic predicates must not already fire on a normal neighbour.
    #    A rule that is wrong before it is deployed cannot be repaired by
    #    measuring it more. A runner that raises is a failure, not a clean
    #    neighbour: skipping it would report "no neighbour fires" for a check
    #    that never ran.
    if predicate_runner is not None and normal_neighbors:
        offending = ""
        errors: list[str] = []
        for neighbor in normal_neighbors:
            case = str(neighbor.get("case") or neighbor.get("case_id") or "")
            try:
                verdict = str(predicate_runner(candidate, neighbor) or "")
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(f"{case or '<unnamed>'}: {type(exc).__name__}: {exc}")
                continue
            if verdict in {"violated", "inconsistent", "unresolved", "missing"}:
                offending = case
                break
        detail = ""
        if offending:
            detail = f"predicate fires on normal neighbour {offending!r}"
        elif errors:
            detail = "neighbour check could not be run: " + "; ".join(errors[:3])
        record("normal_neighbors_clean", not offending and not errors, detail)

    # 6. A localization candidate is scored against the annotated step, so
    #    without that annotation the L1 replay cannot show it improved
    #    anything. Refusing here keeps an unscorable candidate out of a
    #    measurement that would only report it as "no target improved".
    if target_kind in {"localization", "joint"}:
        annotated = [
            case for case in (target_cases or ())
            if _step_of((gold_steps or {}).get(str(case))) is not None
        ]
        record(
            "localization_annotation",
            bool(annotated) or not target_cases,
            (
                "no target case carries a positive gold step, so a "
                "localization improvement cannot be shown"
            ) if target_cases and not annotated else "",
        )

    passed = all(item["passed"] for item in checks)
    return {
        "level": "L0",
        "passed": passed,
        "checks": checks,
        "failed": [item for item in checks if not item["passed"]],
        # Whether every declared check actually ran. A check whose inputs are
        # absent is not a pass, and callers need to be able to tell the two
        # apart without parsing the check list.
        "checks_run": len(checks),
    }


def plan_reaches(hook: str, stage: str) -> bool:
    """Whether a change at ``hook`` can reach ``stage``.

    The hook dependency map is the authority; a hook it does not know cannot be
    shown to reach anything, so it is reported as not reaching.
    """
    from evolution.backends import HOOK_DEPENDENCY_GRAPH

    reached = HOOK_DEPENDENCY_GRAPH.get(str(hook or ""))
    if reached is None:
        return False
    return str(stage or "") == "" or str(stage) in reached


def check_l1(
    *,
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    target_cases: Sequence[str],
    neighbor_cases: Sequence[str],
    intervention_id: str = "",
    localization: bool = False,
    gold_steps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a complete targeted replay without netting gains against regressions."""
    gold_steps = dict(gold_steps or {})
    try:
        delta = paired_delta(
            baseline_rows, candidate_rows,
            expected_cases=set(target_cases) | set(neighbor_cases),
        )
    except PairedCoverageError as exc:
        return _unmeasured_l1(target_cases, str(exc), "coverage_error")
    except ValueError as exc:
        return _unmeasured_l1(target_cases, str(exc), "invalid_measurement")
    unhealthy = (
        arm_has_infrastructure_failure(baseline_rows)
        or arm_has_infrastructure_failure(candidate_rows)
    )
    if unhealthy:
        return _unmeasured_l1(target_cases, unhealthy)
    baseline_by_case = {
        str(row.get("case") or ""): row for row in baseline_rows
    }
    candidate_by_case = {
        str(row.get("case") or ""): row for row in candidate_rows
    }

    target_improved: list[str] = []
    target_unchanged: list[str] = []
    for case in target_cases:
        base = baseline_by_case.get(str(case)) or {}
        active = candidate_by_case.get(str(case)) or {}
        if _case_improved(
            base, active, localization=localization, gold_step=gold_steps.get(str(case)),
        ):
            target_improved.append(str(case))
        else:
            target_unchanged.append(str(case))

    neighbor_regressed: list[str] = []
    for case in neighbor_cases:
        base = baseline_by_case.get(str(case)) or {}
        active = candidate_by_case.get(str(case)) or {}
        if _case_regressed(
            base, active, localization=localization, gold_step=gold_steps.get(str(case)),
        ):
            neighbor_regressed.append(str(case))

    target_regressed: list[str] = []
    for case in target_cases:
        base = baseline_by_case.get(str(case)) or {}
        active = candidate_by_case.get(str(case)) or {}
        if _case_regressed(
            base, active, localization=localization, gold_step=gold_steps.get(str(case)),
        ):
            target_regressed.append(str(case))

    fired_cases = (
        _fired_cases(candidate_rows, intervention_id)
        if intervention_id else []
    )
    delta_new_errors = int(delta.get("new_errors") or 0)
    delta_tp_lost = int(delta.get("tp_lost") or 0)
    reasons: list[str] = []
    reason_codes: list[str] = []
    if not target_improved:
        reasons.append(
            "no target case improved on the development replay"
        )
        reason_codes.append("no_target_improvement")
    if target_regressed:
        reasons.append(
            "target case(s) regressed: " + ", ".join(target_regressed)
        )
        reason_codes.append("target_regression")
    if neighbor_regressed:
        reasons.append(
            "normal neighbour(s) regressed: " + ", ".join(neighbor_regressed)
        )
        reason_codes.append("neighbor_regression")
    if int(delta.get("health_regressed") or 0) > 0:
        reasons.append(
            f"runtime health regressed on {delta['health_regressed']} case(s)"
        )
        reason_codes.append("health_regression")
    # The paired delta's own reading of the same replay. It is not redundant
    # with the per-case lists: ``new_errors`` also counts a lost true positive
    # that the per-case regression test cannot see (a target the baseline got
    # right by *not convicting* a clean case), and a replayed pair that nets a
    # gain while adding an error is not an improvement to build on.
    if delta_new_errors > 0 or delta_tp_lost > 0:
        reasons.append(
            f"the paired replay added {delta_new_errors} new error(s) and "
            f"lost {delta_tp_lost} true positive(s)"
        )
        reason_codes.append("paired_delta_regression")
    if intervention_id and not fired_cases:
        reasons.append(
            f"the candidate {intervention_id!r} never reached the runtime on "
            "any development case, so this replay measured nothing about it"
        )
        reason_codes.append("hook_never_fired")
    return {
        "level": "L1",
        "passed": not reasons,
        "target_improved": target_improved,
        "target_unchanged": target_unchanged,
        "target_regressed": target_regressed,
        "neighbor_regressed": neighbor_regressed,
        "fired_cases": fired_cases,
        "delta": delta,
        "reasons": reasons,
        "reason_codes": reason_codes,
    }


def _unmeasured_l1(
    target_cases: Sequence[str], reason: str, code: str = "unmeasurable",
) -> dict[str, Any]:
    return {
        "level": "L1", "passed": False, "unmeasurable": True,
        "reasons": [f"unmeasurable: {reason}"], "reason_codes": [code],
        "target_improved": [], "target_unchanged": list(target_cases),
        "target_regressed": [], "neighbor_regressed": [], "fired_cases": [],
        "delta": {},
    }


def _fired_cases(
    rows: Sequence[Mapping[str, Any]], intervention_id: str,
) -> list[str]:
    """Cases where one intervention actually fired, from the deployment record."""
    fired: list[str] = []
    for row in rows:
        deployment = ((row.get("result") or {}).get("proof") or {}).get(
            "deployment"
        ) or {}
        if any(
            str(item.get("intervention_id") or "") == str(intervention_id)
            and str(item.get("event") or "")
            not in {"recall_trigger_not_matched", "predicate_evaluated"}
            for item in deployment.get("fired") or ()
        ):
            fired.append(str(row.get("case") or ""))
    return fired


def _prediction(row: Mapping[str, Any]) -> bool | None:
    value = row.get("prediction")
    if value is None:
        result = row.get("result") or {}
        if result:
            state = str(result.get("state") or "")
            if state == "verified_error":
                return True
            if state == "verified_clean":
                return False
        return None
    return bool(value)


def _authoritative(row: Mapping[str, Any]) -> bool:
    authority = str(row.get("authority") or "")
    if authority:
        return authority == "authoritative"
    return str((row.get("result") or {}).get("decision_authority") or "") == (
        "authoritative"
    )


def _case_improved(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    localization: bool,
    gold_step: Any = None,
) -> bool:
    """Whether one case moved toward the claimed objective."""
    if not candidate:
        return False
    if not _authoritative(candidate):
        return False
    gold = candidate.get("gold", baseline.get("gold"))
    base_prediction = _prediction(baseline)
    candidate_prediction = _prediction(candidate)
    if localization:
        if gold is not True or candidate_prediction is not True:
            return False
        annotated = _step_of(gold_step)
        if annotated is None:
            # Without the annotation there is no way to tell a corrected step
            # from a differently wrong one, so no localization improvement can
            # be claimed. The caller passes the annotation whenever it has it.
            return False
        return (
            row_responsible_step(candidate) == annotated
            and (base_prediction is not True or row_responsible_step(baseline) != annotated)
        )
    if gold is None:
        return False
    if base_prediction == gold:
        return False
    return candidate_prediction == gold


def _case_regressed(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    localization: bool,
    gold_step: Any = None,
) -> bool:
    """Whether one case moved away from what the baseline had right."""
    if not candidate:
        return False
    gold = candidate.get("gold", baseline.get("gold"))
    base_prediction = _prediction(baseline)
    candidate_prediction = _prediction(candidate)
    if localization:
        if gold is not True or base_prediction is not True:
            return False
        annotated = _step_of(gold_step)
        if annotated is not None:
            # With the annotation, the question is whether a case the baseline
            # localized correctly still does.
            return (
                row_responsible_step(baseline) == annotated
                and (candidate_prediction is not True
                     or row_responsible_step(candidate) != annotated)
            )
        # A conviction that stops naming a step is a localization regression
        # even though the binary decision is unchanged.
        return row_responsible_step(baseline) is not None and (
            candidate_prediction is not True or row_responsible_step(candidate) is None
        )
    if gold is None:
        return False
    if base_prediction != gold:
        return False
    return candidate_prediction != gold


def _step_of(value: Any) -> int | None:
    """The annotated responsible step, or None when not annotated."""
    if value is None or isinstance(value, bool):
        return None
    try:
        step = int(value)
    except (TypeError, ValueError):
        return None
    return step if step > 0 else None


class CandidateDeveloper:
    """The draft -> L0 -> L1 -> revise loop for one mechanism.

    The proposer is injected as a callable so the loop can be driven by an LLM,
    a scripted stand-in, or a test:

        propose(mechanism, plan, parent, diagnosis, cycle) -> candidates

    Returning an unchanged candidate is legal and is recorded as such: a loop
    that silently re-tests the same version would burn budget without producing
    lineage.
    """

    def __init__(
        self,
        *,
        output_dir: str | Path,
        budget: DevelopmentBudget,
        mechanism: Mapping[str, Any],
        capability_plan: Mapping[str, Any] | None = None,
        route_failure_records: Sequence[Mapping[str, Any]] = (),
        target_cases: Sequence[str] = (),
        neighbor_cases: Sequence[str] = (),
        active_entries: Sequence[Mapping[str, Any]] = (),
        normal_neighbors: Sequence[Mapping[str, Any]] = (),
        target_kind: str = "detection",
        gold_steps: Mapping[str, Any] | None = None,
        l1_runner: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
        predicate_runner: Callable[[Mapping[str, Any], Mapping[str, Any]], str] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.budget = budget
        self.mechanism = dict(mechanism)
        self.capability_plan = dict(capability_plan or {})
        self.route_failure_records = list(route_failure_records)
        self.target_cases = [str(case) for case in target_cases]
        self.neighbor_cases = [str(case) for case in neighbor_cases]
        self.active_entries = [dict(entry) for entry in active_entries]
        self.normal_neighbors = [dict(row) for row in normal_neighbors]
        self.target_kind = str(target_kind or "detection")
        # Evaluator-side annotation, used only to score a localization target
        # (never written into a candidate or a candidate hash).
        self.gold_steps = dict(gold_steps or {})
        self.l1_runner = l1_runner
        self.predicate_runner = predicate_runner
        self.graph = CandidateVersionGraph(self.output_dir)
        self.graph.load_existing()

    # -- helpers ---------------------------------------------------------
    def _mechanism_id(self) -> str:
        return str(self.mechanism.get("mechanism_id") or "")

    def _route(self) -> str:
        """The route this run develops against.

        Read from the capability plan so the loop never drafts against a route
        the planner has already shown cannot act on this mechanism.
        """
        sufficient = list(self.capability_plan.get("sufficient_routes") or ())
        if sufficient:
            return str(sufficient[0])
        return ""

    def _replay_cost(self) -> int:
        """Case evaluations one L1 replay spends: two arms over the case list."""
        return 2 * (len(self.target_cases) + len(self.neighbor_cases))

    def _exhausted(self) -> str:
        if len(self.graph.versions) >= self.budget.max_versions_per_mechanism:
            return "max_versions_per_mechanism"
        if self.budget.provider_calls_left() <= 0:
            return "max_provider_calls"
        if self.budget.l1_cases_left() < self._replay_cost():
            return "max_l1_cases"
        return ""

    def _l1_rows(
        self, candidate: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Baseline and candidate rows over the development case set.

        The runner is injected: the deployment loop passes one that evaluates
        through the real backend, and a test passes a scripted one. Development
        never calls a provider itself, so the budget is enforced by the caller
        that owns the backend.

        A runner that refuses to score the rows it was asked for (``deadline``
        exhausted, no authority to decide) raises
        :class:`DevelopmentUnmeasurable`; the loop turns that into an
        ``unmeasured`` version. Rows that *carry* an infrastructure failure are
        caught by the caller, which is where the same reading lives for a
        runner that returns them instead of raising.
        """
        if self.l1_runner is None:
            return [], []
        cases = [*self.target_cases, *self.neighbor_cases]
        rows = list(self.l1_runner(
            cases, candidate=candidate, plan=self.capability_plan,
        ))
        baseline = [row for row in rows if row.get("arm") == "baseline"]
        active = [row for row in rows if row.get("arm") == "candidate"]
        return baseline, active

    # -- the loop --------------------------------------------------------
    def develop(
        self,
        propose: Callable[..., Any],
        *,
        cycle: int,
        max_versions: int | None = None,
        revise: bool = True,
    ) -> dict[str, Any]:
        """Run the loop until the budget is spent or a version is ready.

        ``propose`` returns the drafted candidates: a plain sequence, or an
        object carrying ``candidates`` and ``notes`` (the deployment path's
        ``ProposalBatch``). The notes are the proposer's own explanation for an
        empty answer and are reported through ``stopped_note``; a caller that
        returns a bare sequence simply has none.

        Returns the run record: every version, its L0/L1 verdicts, the lineage,
        the replay boundary of the selected version, and the version selected
        for freezing (if any).
        """
        cap = int(max_versions or self.budget.max_versions_per_mechanism)
        route = self._route()
        versions: list[dict[str, Any]] = []
        parent: Mapping[str, Any] | None = None
        parent_version_id = ""
        diagnosis: Mapping[str, Any] | None = None
        selected = ""
        selected_candidate: Mapping[str, Any] | None = None
        selected_boundary: dict[str, Any] = {}
        stopped = ""
        # The proposer's own explanation for an empty answer, carried into the
        # run record. Empty for every other stop reason.
        stopped_note: dict[str, Any] = {}
        while len(versions) < cap:
            reason = self._exhausted()
            if reason:
                stopped = reason
                break
            drafted = propose(
                self.mechanism,
                self.capability_plan,
                parent,
                diagnosis,
                cycle,
            )
            # A proposal object carries ``candidates`` and ``notes``; a bare
            # sequence carries the candidates only. Both are accepted, because
            # the notes are the proposer's own explanation for an empty answer
            # and a caller that cannot supply them must not be refused.
            batch = list(
                drafted.candidates
                if hasattr(drafted, "candidates") else (drafted or ())
            )
            # Charge actual provider requests, including internal repair
            # requests.  A refusal before calling the provider must not appear
            # as a successful network call in the development ledger.  Legacy
            # proposers without notes retain the one-call accounting.
            notes = getattr(drafted, "notes", None)
            calls = (
                sum(
                    note.get("provider_called") is True
                    for note in notes if isinstance(note, Mapping)
                )
                if notes is not None else 1
            )
            if calls:
                self.budget.spend_provider(calls)
            if not batch:
                # "No candidate was drafted" is one word for three different
                # situations: the proposer's own call budget was already spent,
                # it was asked and returned nothing, or it answered with a note
                # explaining why. The note is carried through -- without it a
                # budget-exhausted round and an empty answer are the same
                # record, and a reader has to guess which one happened. The
                # reason is a *report*, not a control-flow input: the stop stays
                # ``no_candidates`` either way.
                stopped = "no_candidates"
                stopped_note = _batch_reason(drafted)
                break
            candidate = dict(batch[0])
            actual_route = route_of(candidate) or "unknown"
            version_id = self.graph.new_version_id()
            l0 = check_l0(
                candidate,
                mechanism=self.mechanism,
                active_entries=self.active_entries,
                normal_neighbors=self.normal_neighbors,
                predicate_runner=self.predicate_runner,
                target_kind=self.target_kind,
                target_cases=self.target_cases,
                gold_steps=self.gold_steps,
            )
            l1: dict[str, Any] | None = None
            status = "failed_l0"
            if l0["passed"]:
                # A replay that could not score the candidate is not a
                # falsification. The runner is asked for rows; if it says the
                # rows cannot be scored, or hands back rows that carry an
                # infrastructure failure, the version is *unmeasured* -- a
                # distinction the archive, the episodic memory and the next
                # drafting prompt all read, and one that was previously lost
                # by writing every non-pass as ``failed_l1``.
                unmeasurable = ""
                try:
                    baseline_rows, candidate_rows = self._l1_rows(candidate)
                except DevelopmentUnmeasurable as exc:
                    baseline_rows, candidate_rows = [], []
                    unmeasurable = str(exc.reason)
                if unmeasurable:
                    l1 = _unmeasured_l1(self.target_cases, unmeasurable)
                else:
                    l1 = check_l1(
                        baseline_rows=baseline_rows,
                        candidate_rows=candidate_rows,
                        target_cases=self.target_cases,
                        neighbor_cases=self.neighbor_cases,
                        intervention_id=str(
                            candidate.get("intervention_id") or ""
                        ),
                        localization=self.target_kind
                        in {"localization", "joint"},
                        gold_steps=self.gold_steps,
                    )
                if l1 and l1.get("passed"):
                    status = "ready_for_freeze"
                elif (l1 or {}).get("unmeasurable"):
                    # Not judged, so not falsified: the vocabulary owns the
                    # word (candidate_versions.VERSION_STATUSES).
                    status = "unmeasured"
                else:
                    status = "failed_l1"
                if self.l1_runner is not None:
                    self.budget.l1_cases_used += self._replay_cost()
            record = self.graph.record(
                actual_route,
                version_id,
                candidate,
                parent=parent,
                diagnosis=diagnosis,
                draft_notes=[{
                    "mechanism_id": self._mechanism_id(),
                    "capability_plan": self.capability_plan.get(
                        "classification", "",
                    ),
                    "route": actual_route,
                    "planned_route": route,
                }],
                evaluation=None,
                verdict={"l0": l0, "l1": l1},
                status=status,
                revision_reason=list((diagnosis or {}).get("reasons") or ()),
            )
            self.graph.write_development(version_id, {
                "schema_version": DEVELOPMENT_SCHEMA,
                "mechanism_id": self._mechanism_id(),
                "parent_version_id": parent_version_id,
                "action_class": actual_route,
                "planned_route": route,
                "route_replanned": actual_route != route,
                "status": status,
                "l0": l0,
                "l1": l1,
                "development_evidence": {
                    "target_cases": self.target_cases,
                    "neighbor_cases": self.neighbor_cases,
                    "l1_delta": (l1 or {}).get("delta"),
                },
            })
            self.graph.log({
                "cycle": int(cycle),
                "mechanism_id": self._mechanism_id(),
                "version_id": version_id,
                "parent_version_id": parent_version_id,
                "status": status,
                "l0_passed": l0["passed"],
                "l1_passed": bool(l1 and l1.get("passed")),
            })
            entry = {
                "version_id": version_id,
                "parent_version_id": parent_version_id,
                "status": status,
                "candidate_hash": record.get("candidate_hash"),
                "route": actual_route,
                "planned_route": route,
                "route_replanned": actual_route != route,
                "l0": l0,
                "l1": l1,
            }
            versions.append(entry)
            if status == "ready_for_freeze":
                selected = version_id
                selected_candidate = candidate
                # The rerun boundary this version was developed under. It makes
                # "development is cheaper than promotion" an auditable claim
                # rather than an assumption: a reader can see which stages the
                # candidate forced to re-run, and the boundary key is what the
                # result cache was keyed on.
                selected_boundary = partial_replay_plan(
                    [candidate], active_entries=self.active_entries,
                )
                break
            if not revise:
                stopped = "revision_disabled"
                break
            # Revision: the same parent is developed further, with the failure
            # diagnosis handed back. The parent is the last candidate that was
            # measured, not the last one that failed L0 -- a candidate that
            # never ran produced no evidence to revise from.
            if l0["passed"]:
                parent = candidate
                parent_version_id = version_id
            diagnosis = {
                "version_id": version_id,
                "l0": l0,
                "l1": l1,
                "reasons": [
                    *(f"L0: {item['detail']}" for item in l0["failed"]),
                    *((l1 or {}).get("reasons") or ()),
                ],
            }
        else:
            # The loop fell out of the bottom: it ran cap versions without one
            # passing. When every measured attempt was unmeasurable, that is
            # the honest name for the stop -- the candidate was never falsified,
            # the replay simply produced no rows to judge it on.
            measured = [item for item in versions if item.get("l1")]
            if measured and all(
                (item.get("l1") or {}).get("unmeasurable") for item in measured
            ):
                stopped = "no_measurement"
            else:
                stopped = "max_versions_per_mechanism"
        return {
            "schema_version": DEVELOPMENT_SCHEMA,
            "mechanism_id": self._mechanism_id(),
            "route": (route_of(selected_candidate) or "unknown") if selected_candidate else route,
            "planned_route": route,
            "cycle": int(cycle),
            "versions": versions,
            "selected_version_id": selected,
            "selected_candidate": (
                dict(selected_candidate) if selected_candidate else None
            ),
            "lineage": (
                self.graph.lineage(selected) if selected else []
            ),
            "replay_boundary": selected_boundary,
            "stopped": stopped,
            # Why the proposer produced nothing, when that is what stopped the
            # loop: the note itself plus a coarse ``stopped_reason``. Empty for
            # every other stop, so a reader can tell "the proposer had nothing"
            # from "the loop ran out of ways to stop".
            "stopped_note": stopped_note,
            # Two outcomes only: something is ready to freeze, or nothing is.
            # A run that exhausted its budget without a passing version is
            # "discarded" rather than "failed", because the versions carry
            # their own L0/L1 verdicts and a single run-level word should not
            # overwrite them.
            "status": "ready_for_freeze" if selected else "discarded",
            "budget": self.budget.as_dict(),
        }

    # -- hand-off --------------------------------------------------------
    def freeze(self, version_id: str) -> dict[str, Any]:
        """Hand one version to the promotion path, or refuse.

        The only exit from development. Refusing an unfrozen-status version is
        what keeps a candidate that failed a neighbour check from reaching a
        frozen protocol: the gates judge candidates, they do not re-run the
        development loop's job.
        """
        known = {
            str(item.get("version_id") or "")
            for item in self.graph.versions
        }
        if str(version_id) not in known:
            # Unknown is reported before status: a version that does not exist
            # has no status, and "not ready" would misdescribe the problem.
            raise ValueError(f"unknown version {version_id!r}")
        status = self.graph.development_status(version_id)
        state = str(status.get("status") or "")
        if state != "ready_for_freeze":
            raise ValueError(
                f"{version_id} is {state!r}, not ready_for_freeze; a candidate "
                "that did not pass L1 must not reach a promotion protocol"
            )
        record = next(
            (
                item for item in self.graph.versions
                if str(item.get("version_id") or "") == str(version_id)
            ),
            None,
        )
        candidate = (record or {}).get("candidate")
        if not candidate:
            raise ValueError(f"{version_id} carries no candidate")
        self.graph.write_development(version_id, {"status": "frozen"})
        return {
            "version_id": str(version_id),
            "mechanism_id": str(status.get("mechanism_id") or ""),
            "route": str(status.get("action_class") or ""),
            "candidate": copy.deepcopy(candidate),
            "candidate_hash": canonical_hash(candidate),
            "lineage": self.graph.lineage(version_id),
            "development_evidence": dict(
                status.get("development_evidence") or {},
            ),
        }


def development_report(result: Mapping[str, Any]) -> dict[str, Any]:
    """A compact view of one development run for a cycle report.

    ``l1_reason_codes`` travels beside ``l1_reasons`` so the report answers
    "how did this fail" in the same machine-readable vocabulary the archive
    stores, and ``unmeasured`` marks a version that was never judged -- a
    reader that saw only ``l1_passed: false`` would call that a falsification.
    """
    versions = list(result.get("versions") or ())
    return {
        "schema_version": DEVELOPMENT_SCHEMA,
        "mechanism_id": result.get("mechanism_id"),
        "route": result.get("route"),
        "planned_route": result.get("planned_route"),
        "cycle": result.get("cycle"),
        "status": result.get("status"),
        "stopped": result.get("stopped"),
        "stopped_note": dict(result.get("stopped_note") or {}),
        "versions": [
            {
                "version_id": item.get("version_id"),
                "parent_version_id": item.get("parent_version_id"),
                "status": item.get("status"),
                "route": item.get("route"),
                "planned_route": item.get("planned_route"),
                "route_replanned": item.get("route_replanned"),
                "l0_passed": bool((item.get("l0") or {}).get("passed")),
                "l1_passed": bool((item.get("l1") or {}).get("passed")),
                "unmeasured": bool((item.get("l1") or {}).get("unmeasurable")),
                "l1_reasons": list((item.get("l1") or {}).get("reasons") or ()),
                "l1_reason_codes": list(
                    (item.get("l1") or {}).get("reason_codes") or ()
                ),
            }
            for item in versions
        ],
        "selected_version_id": result.get("selected_version_id"),
        "lineage": list(result.get("lineage") or ()),
        "budget": result.get("budget"),
    }


__all__ = [
    "DEVELOPMENT_SCHEMA",
    "CandidateDeveloper",
    "DevelopmentBudget",
    "DevelopmentUnmeasurable",
    "check_l0",
    "check_l1",
    "development_report",
    "plan_reaches",
]
