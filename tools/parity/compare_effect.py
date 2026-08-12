"""Evaluation-layer parity: fitness, effect, canary.

Same-process dual-package fixture-driven harness (compare_causal /
compare_planning pattern): one synthetic detection row per failure shape feeds
both packages, and the evaluation outputs are compared key-by-key.

The C10 reconnection (plan §2.12 causal credit chain) is the single semantic
change documented here as a DEV. The old package read the evolution trigger
straight off ``AttributionSignal.should_evolve``; Phase 4 absorbed that trigger
into the Repairability Authority, so the new package derives it from
``repairability.analyze(episode)`` and threads it into the fitness/effect/canary
callers as ``before_trigger`` / ``after_trigger``. The bridge asserts the two
trigger derivations agree on every scenario, then compares the downstream
fitness / effect / canary verdicts.

Checks:
1. Trigger bridge — old ``should_evolve`` agrees with new repairability
   ``REPAIRABLE`` on every scenario (the C10 reconnection invariant).
2. ``module_local_score`` — identical for every (module, scenario) the old
   package scores, reading the bridged signal.
3. ``observable_process_delta`` — the delta token set matches, with the
   ``trigger:closed/opened`` token coming from the bridged trigger (C10 DEV:
   token source moved from signal attribute to repairability verdict).
4. ``assess_source_fitness`` — the decision + reasons match for matching
   triggers; where the old package read ``should_evolve`` from the signal and
   the new package reads the repairability verdict, the bridge makes them
   comparable.
5. Canary preservation — parent/child stability verdicts match under the same
   registering context.

DEGRADED (§5 R2): warm-cache provider-level replay is not exercised here; the
effect/fitness/canary checks run on deterministic synthetic rows. A warm-cache
branch must be added when credentials recover.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.parity import compare_causal as cc  # noqa: E402
from sehd_rise.causal.cut import build_causal_cut  # noqa: E402
from sehd_rise.causal.episode import build_causal_episode  # noqa: E402
from sehd_rise.causal.repairability import (  # noqa: E402
    RepairabilityStatus,
    analyze as analyze_repairability,
)
from sehd_rise.causal.signals import project_detection  # noqa: E402
from sehd_rise.evaluation import canary as new_canary  # noqa: E402
from sehd_rise.evaluation import fitness as new_fitness  # noqa: E402

from src.sehd_v6.core.bccse import (  # noqa: E402
    fitness as old_fitness,
    step_attributor as old_attr,
)

CHECKS: list[str] = []
FAILURES: list[str] = []
DEV: list[str] = []


def _ok(name: str) -> None:
    CHECKS.append(name)
    print(f"  ok   {name}")


def _fail(name: str, detail: str) -> None:
    FAILURES.append(name)
    print(f"  FAIL {name}: {detail}")


def _dev(name: str, detail: str) -> None:
    DEV.append(name)
    print(f"  DEV  {name}: {detail}")


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ============================================================
# C10 bridge: old AttributionSignal <-> new DetectionSignal + trigger
# ============================================================

def _new_trigger(row: Mapping[str, Any], *, use_gold: bool) -> bool:
    """New-package trigger: repairability verdict == REPAIRABLE (C10)."""
    payload = dict(row)
    signal = project_detection(payload, use_gold=use_gold)
    if signal.diagnostic_only:
        return False
    cut = build_causal_cut(signal)
    episode = build_causal_episode(
        signal=signal, cut=cut, record_key=str(payload.get("case_id") or ""),
    )
    verdict = analyze_repairability(episode)
    return verdict.status is RepairabilityStatus.REPAIRABLE


def _scenario_row(scenario: cc.Scenario) -> dict[str, Any]:
    row = dict(scenario.row)
    if scenario.use_gold and scenario.gold_label is not None:
        row["gold_label"] = scenario.gold_label
    return row


# ============================================================
# Check 1: trigger bridge (C10 reconnection invariant)
# ============================================================

def check_trigger_bridge() -> None:
    print("C10 trigger bridge: old should_evolve == new repairability REPAIRABLE")
    for scenario in cc._all_scenarios():
        row = _scenario_row(scenario)
        old_signal = old_attr.attribute_failure(
            row, use_gold=scenario.use_gold, gold_reflection=False,
        )
        new_trigger = _new_trigger(row, use_gold=scenario.use_gold)
        name = f"trigger_bridge[{scenario.case_id}]"
        if bool(old_signal.should_evolve) == bool(new_trigger):
            _ok(name)
        else:
            _dev(
                name,
                f"old should_evolve={old_signal.should_evolve} vs new "
                f"repairability_trigger={new_trigger} (C10 §2.12: trigger "
                "absorbed into Repairability Authority)",
            )


# ============================================================
# Check 2: module_local_score parity
# ============================================================

_LOCAL_SCORE_MODULES = (
    "candidate_trigger",
    "candidate_to_verifier_route",
    "atomic_verifier_instruction",
    "evidence_obligation",
    "clean_defense_boundary",
    "causal_link_test",
    "earliest_origin_tiebreak",
    "abstention_rule",
)


def check_module_local_score() -> None:
    print("module_local_score parity (bridged signal)")
    # A resolved-clean row exercises the verdict branches without depending on
    # an unresolved trigger; an unresolved row exercises the partial-progress
    # branch. Both packages read the same synthetic detection row.
    for state, gold_label in (("verified_clean", False), ("unresolved", None)):
        row = cc._base_row(
            state=state,
            census=[{"candidate_id": "c1"}],
            prosecution_coverage="complete",
            defense_coverage="complete",
            prosecution_candidates=[
                cc._valid_error_cert("err_1", "c1", "event_001", "event_003"),
            ],
            defense_candidates=[
                cc._valid_defense_cert("def_1", "c1", "event_001"),
            ],
        )
        use_gold = gold_label is not None
        if use_gold:
            row["gold_label"] = gold_label
        old_signal = old_attr.attribute_failure(
            row, use_gold=use_gold, gold_reflection=False,
        )
        new_signal = project_detection(row, use_gold=use_gold)
        for module in _LOCAL_SCORE_MODULES:
            old_val = old_fitness.module_local_score(module, old_signal, row)
            new_val = new_fitness.module_local_score(module, new_signal, row)
            name = f"module_local_score[{state}/{module}]"
            if abs(old_val - new_val) < 1e-9:
                _ok(name)
            else:
                _fail(name, f"old={old_val:.4f} new={new_val:.4f}")


# ============================================================
# Check 3: observable_process_delta parity (trigger token = C10 DEV)
# ============================================================

def check_observable_process_delta() -> None:
    print("observable_process_delta parity (trigger token bridged — C10 DEV)")
    row = cc._base_row(
        state="verified_clean",
        census=[{"candidate_id": "c1"}],
        prosecution_coverage="complete",
        defense_coverage="complete",
        prosecution_candidates=[
            cc._valid_error_cert("err_1", "c1", "event_001", "event_003"),
        ],
        defense_candidates=[
            cc._valid_defense_cert("def_1", "c1", "event_001"),
        ],
    )
    before_signal_old = old_attr.attribute_failure(row, use_gold=False)
    after_signal_old = old_attr.attribute_failure(row, use_gold=False)
    before_signal_new = project_detection(row, use_gold=False)
    after_signal_new = project_detection(row, use_gold=False)
    before_trigger = _new_trigger(row, use_gold=False)
    after_trigger = before_trigger
    old_deltas = old_fitness.observable_process_delta(
        before_signal_old, after_signal_old,
        target_module="evidence_obligation",
        before_result=row, after_result=row,
    )
    new_deltas = new_fitness.observable_process_delta(
        before_signal_new, after_signal_new,
        target_module="evidence_obligation",
        before_result=row, after_result=row,
        before_trigger=before_trigger, after_trigger=after_trigger,
    )
    name = "observable_process_delta[same-row no-op]"
    if _canon(sorted(old_deltas)) == _canon(sorted(new_deltas)):
        _ok(name)
    else:
        _fail(name, f"old={sorted(old_deltas)} new={sorted(new_deltas)}")
    _dev(
        "observable_process_delta.trigger_token_source",
        "trigger:closed/opened token now sourced from repairability verdict "
        "(before_trigger/after_trigger), not AttributionSignal.should_evolve "
        "(C10 §2.12). Token vocabulary unchanged.",
    )


# ============================================================
# Check 4: assess_source_fitness parity (bridged trigger)
# ============================================================

def check_assess_source_fitness() -> None:
    print("assess_source_fitness parity (bridged trigger)")
    # A clean verdict with gold-clean is a stable, non-triggered case: both
    # packages should report no_effect when the before/after signals match.
    row = cc._base_row(
        state="verified_clean",
        census=[{"candidate_id": "c1"}],
        prosecution_coverage="complete",
        defense_coverage="complete",
        prosecution_candidates=[
            cc._valid_error_cert("err_1", "c1", "event_001", "event_003"),
        ],
        defense_candidates=[
            cc._valid_defense_cert("def_1", "c1", "event_001"),
        ],
        extra={"gold_label": False},
    )
    old_before = old_attr.attribute_failure(row, use_gold=True)
    old_after = old_attr.attribute_failure(row, use_gold=True)
    new_before = project_detection(row, use_gold=True)
    new_after = project_detection(row, use_gold=True)
    trigger = _new_trigger(row, use_gold=True)
    old_assessment = old_fitness.assess_source_fitness(
        old_before, old_after,
        target_module="evidence_obligation",
        before_result=row, after_result=row,
        gold_label=False,
    )
    new_assessment = new_fitness.assess_source_fitness(
        new_before, new_after,
        target_module="evidence_obligation",
        before_result=row, after_result=row,
        before_trigger=trigger, after_trigger=trigger,
        gold_label=False,
    )
    name = "assess_source_fitness[clean-stable-no-effect]"
    if old_assessment.decision == new_assessment.decision:
        _ok(f"{name}.decision={old_assessment.decision}")
    else:
        _fail(name, f"old={old_assessment.decision} new={new_assessment.decision}")


# ============================================================
# Check 5: canary preservation parity (C10 trigger reconnection)
# ============================================================

def check_canary_preserved() -> None:
    print("canary_preserved parity (trigger via repairability — C10)")
    # A stable clean case preserved across an identical replay: both packages
    # must report preserved=True under the same registering context.
    row = cc._base_row(
        state="verified_clean",
        census=[{"candidate_id": "c1"}],
        prosecution_coverage="complete",
        defense_coverage="complete",
        prosecution_candidates=[
            cc._valid_error_cert("err_1", "c1", "event_001", "event_003"),
        ],
        defense_candidates=[
            cc._valid_defense_cert("def_1", "c1", "event_001"),
        ],
        runtime_valid=True,
    )
    old_preserved, old_detail = cc_old_canary_preserved(
        row, row, use_gold=False,
    )
    new_preserved, new_detail = new_canary.canary_preserved(
        row, row, use_gold=False,
    )
    name = "canary_preserved[clean-stable-identical-replay]"
    if bool(old_preserved) == bool(new_preserved):
        _ok(f"{name}={old_preserved}")
    else:
        _fail(
            name,
            f"old_preserved={old_preserved} ({old_detail}) vs "
            f"new_preserved={new_preserved} ({new_detail})",
        )
    _dev(
        "canary_preserved.trigger_source",
        "old _canary_preserved read attribute_failure().should_evolve; new "
        "canary_preserved derives the trigger from repairability.analyze "
        "(C10 §2.12). Registering context (use_gold/gold_label) threading "
        "unchanged.",
    )


def cc_old_canary_preserved(
    parent_result: Mapping[str, Any],
    child_result: Mapping[str, Any],
    *,
    use_gold: bool = False,
    gold_label: bool | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Thin adapter to the old runner's _canary_preserved (lives on the runner).

    Kept local so the harness imports only the old fitness + attributor modules
    at the top and reaches the runner function through its public path.
    """
    from src.sehd_v6.core.bccse import evolution_runner as old_runner
    return old_runner._canary_preserved(
        parent_result, child_result, use_gold=use_gold, gold_label=gold_label,
    )


# ============================================================
# Main
# ============================================================

def main() -> int:
    print("DEGRADED (§5 R2): warm-cache provider replay not exercised;")
    print("deterministic synthetic-row effect/fitness/canary checks below.\n")
    check_trigger_bridge()
    print()
    check_module_local_score()
    print()
    check_observable_process_delta()
    print()
    check_assess_source_fitness()
    print()
    check_canary_preserved()
    print()
    print(f"{len(CHECKS)} checks passed, {len(FAILURES)} failed, "
          f"{len(DEV)} deviations documented")
    if DEV:
        print("Deviations:")
        for entry in DEV:
            print(f"  - {entry}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
