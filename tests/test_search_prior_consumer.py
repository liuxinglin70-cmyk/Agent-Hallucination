"""B7 meta deterministic consumer: the committed search_prior token changes
the contrast-neighbor selection output deterministically."""

from __future__ import annotations

from sehd_rise.meta.strategy import (
    SEARCH_PRIOR,
    StrategyEdit,
    StrategyState,
    apply_strategy_edit,
    create_baseline_strategy,
)
from sehd_rise.planning.search import _select_contrast_neighbor


def _entries() -> list[dict]:
    return [
        {
            "case_id": "case_1",
            "state": "verified_clean",
            "owner": "candidate_trigger",
        },
        {
            "case_id": "case_2",
            "state": "verified_clean",
            "owner": "earliest_origin_tiebreak",
        },
        {
            "case_id": "case_3",
            "state": "verified_error",
            "owner": "candidate_trigger",
        },
    ]


def test_same_owner_neighbor_is_skipped_when_independence_required() -> None:
    entries = _entries()
    relaxed = _select_contrast_neighbor(entries, case_id="case_4")
    assert relaxed["case_id"] == "case_3"
    strict = _select_contrast_neighbor(
        entries,
        case_id="case_4",
        require_independent_owner=True,
        current_owner="candidate_trigger",
    )
    assert strict["case_id"] == "case_2"


def test_no_independent_neighbor_yields_none() -> None:
    entries = [
        {"case_id": "case_1", "state": "verified_clean", "owner": "m1"},
        {"case_id": "case_2", "state": "verified_error", "owner": "m1"},
    ]
    assert _select_contrast_neighbor(
        entries, case_id="case_3",
        require_independent_owner=True, current_owner="m1",
    ) is None
    # Without the requirement the nearest resolved row is still selectable.
    assert _select_contrast_neighbor(
        entries, case_id="case_3",
    )["case_id"] == "case_2"


def test_committed_strategy_token_changes_selection_output() -> None:
    entries = _entries()
    baseline = create_baseline_strategy()
    committed = apply_strategy_edit(baseline, StrategyEdit(
        edit_id="edit_anchor",
        operation="enable_strategy",
        domain=SEARCH_PRIOR,
        target="require_independent_anchor",
        rationale="recurring independent_regression_risk",
    ))
    assert "require_independent_anchor" not in baseline.enabled_in(SEARCH_PRIOR)
    assert "require_independent_anchor" in committed.enabled_in(SEARCH_PRIOR)

    def selection(state: StrategyState) -> str:
        require = (
            "require_independent_anchor" in state.enabled_in(SEARCH_PRIOR)
        )
        neighbor = _select_contrast_neighbor(
            entries,
            case_id="case_4",
            require_independent_owner=require,
            current_owner="candidate_trigger",
        )
        return str(neighbor["case_id"])

    assert selection(baseline) == "case_3"
    assert selection(committed) == "case_2"
