"""B4 trigger-source consistency: the shadow stable-canary admission guard
reads the same Repairability Authority predicate as the runner's canary
guard, so a decisive but still REPAIRABLE replay never poisons the pool."""

from __future__ import annotations

from sehd_rise.causal import repairability as causal_repairability
from sehd_rise.causal.repairability import triggered_episode
from sehd_rise.loop import runner as loop_runner
from sehd_rise.loop import shadow as loop_shadow


def _result(state: str, *, surviving: list[str] | None = None) -> dict:
    return {
        "state": state,
        "runtime_valid": True,
        "decision_authority": "authoritative",
        "responsible_event_id": "event_001",
        "responsible_step": 1,
        "proof_adjudication": {
            "state": state,
            "decision_authority": "authoritative",
            "prosecution_coverage": "complete",
            "defense_coverage": "complete",
            "surviving_error_certificate_ids": list(surviving or []),
            "responsible_event_id": "event_001",
        },
    }


def test_runner_and_shadow_share_the_single_trigger_source() -> None:
    """The three trigger consumers are the same imported predicate, so a
    trigger definition can never drift between them again."""
    assert loop_runner.triggered_episode is causal_repairability.triggered_episode
    assert loop_shadow.triggered_episode is causal_repairability.triggered_episode


def test_decisive_but_repairable_replay_is_triggered() -> None:
    """A resolved replay that still compiles a RepairPlan (a hollow complete
    defense routes to the L2 policy surface) must not be admitted as a
    stable canary: the shadow guard evaluates ``not triggered_episode``
    before pooling."""
    triggered = triggered_episode(
        _result("verified_clean"),
        use_gold=False,
        gold_label=None,
    )
    assert triggered is True


def test_decisive_non_repairable_replay_is_stable() -> None:
    """A gold false positive without the planner's module context compiles
    no plan, so it is decisive, not triggered, and passes the admission
    guard as a stable regression anchor."""
    triggered = triggered_episode(
        {**_result("verified_error", surviving=["e1"]), "gold_label": False},
        use_gold=True,
        gold_label=False,
    )
    assert triggered is False
