"""Phase 9b end-to-end loop verification (plan §4.2/§4.3).

Three controlled scenarios over the converged ``loop/runner`` pipeline plus
checkpoint-resume equivalence. Scenarios 1-3 stub the detector / episode
builder so the run is offline and deterministic; scenario 4 drives real
fixtures through the pipeline with a real provider (warm cache when
available). Assertions:

- C2: an unaddressable case deposits UNADDRESSABLE on its own funnel line
  and never counts as stable / no-edit / convergence.
- C9: an infra failure is diagnostic telemetry and never advances the
  convergence window; the convergence window only consumes complete repair
  attempts with no acceptable improvement.
- C8: the result JSON carries the four-layer funnel (``outcome_funnel``).
- Checkpoint resume: a resumed run matches the continuous run on queue
  position / budget / ledger.
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import deque
from pathlib import Path

from tools.parity.fixtures import load_fixture_records

REPO_ROOT = Path(__file__).resolve().parents[2]


def _config() -> dict:
    with (REPO_ROOT / "sehd_rise" / "configs" / "detector_v3_5.json").open(
        encoding="utf-8"
    ) as handle:
        return json.load(handle)


def _assert(cond: bool, message: str) -> None:
    if not cond:
        print(f"FAIL: {message}")
        sys.exit(1)


def _new_loop(case_ids, records, output_dir, **overrides):
    from sehd_rise.loop.runner import EvolutionLoop

    params = dict(
        evolution_case_ids=case_ids,
        records=records,
        config=_config(),
        output_dir=output_dir,
        mutation_route={
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
        },
        use_gold=False,
        fitness_check=True,
        max_mutation_calls=30,
        reflection_candidate_count=1,
        max_mutation_attempts_per_case=1,
    )
    params.update(overrides)
    return EvolutionLoop(**params)


def _finish(loop):
    loop.run()
    return loop._finalize()


def scenario_1_unaddressable(records: dict, out: Path) -> None:
    """C2: a declared-but-uncompilable episode is UNADDRESSABLE, not no-edit.

    The episode builder is stubbed to fail closed for the only case, so the
    runner sees an evolvable cut with no addressable episode and must record
    UNADDRESSABLE on its own funnel line — never a stable case and never a
    convergence no-edit.
    """
    import sehd_rise.loop.runner as runner_mod

    original_build = runner_mod.build_causal_episode

    def _stub_episode(*args, **kwargs):
        raise RuntimeError("synthetic_episode_compile_failure")

    runner_mod.build_causal_episode = _stub_episode
    try:
        loop = _new_loop(
            ["BFCL/000"], records, out / "s1",
            max_mutation_calls=1,
        )
        loop._initialize_state()
        result = _finish(loop)
    finally:
        runner_mod.build_causal_episode = original_build

    funnel = result.evolution_audit["outcome_funnel"]
    _assert(
        funnel["unaddressable"] == 1,
        f"C2: UNADDRESSABLE must be counted on its own line, got "
        f"{funnel['unaddressable']}",
    )
    _assert(
        result.stable_cases == 0,
        "C2: an unaddressable case must not count as stable",
    )
    _assert(
        result.edits_rejected == 0,
        "C2: an unaddressable case must not count as a rejected edit",
    )
    _assert(
        funnel["by_layer"]["framework"]["UNADDRESSABLE"] == 1,
        "C8: UNADDRESSABLE must appear in the framework layer",
    )
    steps = result.steps
    _assert(
        steps and steps[-1].gate_decision == "UNADDRESSABLE",
        "C8: the unaddressable step must carry the Outcome value",
    )
    print("  scenario 1 (unaddressable C2): OK")


def scenario_2_provider_fault(records: dict, out: Path) -> None:
    """C9: a detector that raises is INFRA_FAILURE, never convergence."""
    import sehd_rise.loop.runner as runner_mod

    original_detect = runner_mod.EvolutionLoop.run_detection

    def _stub_detect(self, *args, **kwargs):
        raise RuntimeError("synthetic_provider_outage")

    runner_mod.EvolutionLoop.run_detection = _stub_detect
    try:
        loop = _new_loop(
            ["BFCL/000"], records, out / "s2",
            max_mutation_calls=1,
        )
        loop._initialize_state()
        result = _finish(loop)
    finally:
        runner_mod.EvolutionLoop.run_detection = original_detect

    funnel = result.evolution_audit["outcome_funnel"]
    _assert(
        result.runtime_invalid_cases == 1,
        "C9: provider fault must be recorded as runtime_invalid telemetry",
    )
    _assert(
        funnel["by_layer"]["framework"]["INFRA_FAILURE"] == 1,
        "C8: provider fault must deposit INFRA_FAILURE",
    )
    _assert(
        result.triggered_cases == 0 and result.stable_cases == 0,
        "C9: infra failure is diagnostic telemetry, never triggered/stable",
    )
    _assert(
        not result.halted,
        "C9: an infra failure must not halt the run",
    )
    print("  scenario 2 (provider fault C9): OK")


def scenario_3_budget_exhausted(records: dict, out: Path) -> None:
    """C9: budget exhaustion is not a complete repair attempt (no converge)."""
    from sehd_rise.loop.runner import EvolutionLoop

    # Pre-spend the mutation budget so the first triggered case must stop at
    # BUDGET_EXHAUSTED without running any reflection.
    loop = _new_loop(
        ["BFCL/000", "BFCL/001"], records, out / "s3",
        max_mutation_calls=5,
    )
    loop._initialize_state()
    loop.budget_usage["mutation_calls"] = loop.max_mutation_calls
    result = _finish(loop)

    funnel = result.evolution_audit["outcome_funnel"]
    _assert(
        funnel["by_layer"]["framework"]["BUDGET_EXHAUSTED"] >= 1,
        "C8: budget exhaustion must deposit BUDGET_EXHAUSTED",
    )
    _assert(
        result.evolution_audit.get("mutation_calls") == loop.max_mutation_calls,
        "budget exhaustion must not run any further mutation call",
    )
    _assert(
        not result.halted,
        "C9: budget exhaustion is infra, not a complete repair attempt, so "
        "it must not halt the run",
    )
    print("  scenario 3 (budget exhausted C9): OK")


def scenario_4_checkpoint_resume(records: dict, out: Path) -> None:
    """Continuous run == interrupted-then-resumed run (queue/budget/ledger).

    The interrupted run processes exactly one case, then raises a sentinel
    inside ``record_step`` (after the checkpoint persist), so the on-disk
    checkpoint holds the run mid-queue. A fresh loop resumed from that
    directory must finish with the same funnel totals, budget usage, step
    decisions and edit counters as the uninterrupted run.
    """
    from sehd_rise.loop.runner import EvolutionLoop

    case_ids = ["BFCL/000", "BFCL/001", "Camel/000"]

    continuous = _new_loop(case_ids, records, out / "continuous")
    continuous._initialize_state()
    result_continuous = _finish(continuous)

    # Interrupted run: process one case, then stop with a checkpointed state.
    class _Interrupt(Exception):
        pass

    interrupted = _new_loop(case_ids, records, out / "interrupted")
    interrupted._initialize_state()
    original_record_step = EvolutionLoop.record_step
    interrupted_steps = 0

    def _record_and_stop(self, step, *, next_case_index):
        nonlocal interrupted_steps
        original_record_step(self, step, next_case_index=next_case_index)
        interrupted_steps += 1
        if interrupted_steps >= 1:
            raise _Interrupt("checkpoint-after-one-case")

    EvolutionLoop.record_step = _record_and_stop
    try:
        interrupted.run()
    except _Interrupt:
        pass
    finally:
        EvolutionLoop.record_step = original_record_step

    resumed = _new_loop(
        case_ids, records, out / "resumed",
        resume_from=out / "interrupted",
    )
    resumed._initialize_state()
    result_resumed = _finish(resumed)

    _assert(
        result_resumed.evolution_audit["outcome_funnel"]["totals"]
        == result_continuous.evolution_audit["outcome_funnel"]["totals"],
        "resumed funnel totals must match the continuous run",
    )
    _assert(
        result_resumed.evolution_audit.get("mutation_calls")
        == result_continuous.evolution_audit.get("mutation_calls"),
        "resumed budget usage must match the continuous run",
    )
    _assert(
        [s.gate_decision for s in result_resumed.steps]
        == [s.gate_decision for s in result_continuous.steps],
        "resumed step decisions must match the continuous run",
    )
    _assert(
        result_resumed.edits_accepted == result_continuous.edits_accepted
        and result_resumed.edits_rejected == result_continuous.edits_rejected,
        "resumed edit counters must match the continuous run",
    )
    _assert(
        result_resumed.halted == result_continuous.halted,
        "resumed halt state must match the continuous run",
    )
    print("  scenario 4 (checkpoint resume): OK")


def main() -> None:
    records = load_fixture_records()
    root = Path(tempfile.mkdtemp(prefix="sehd_9b_e2e_"))
    print("scenario 1: unaddressable (C2) ...")
    scenario_1_unaddressable(records, root / "s1")
    print("scenario 2: provider fault (INFRA_FAILURE, C9) ...")
    scenario_2_provider_fault(records, root / "s2")
    print("scenario 3: budget exhausted (C9) ...")
    scenario_3_budget_exhausted(records, root / "s3")
    print("scenario 4: checkpoint resume equivalence ...")
    scenario_4_checkpoint_resume(records, root / "s4")
    print("ALL E2E SCENARIOS PASSED")


if __name__ == "__main__":
    main()
