"""The optional view must never spend what the diagnostic allowance reserved.

These are invariants rather than examples: whatever sequence of admissions a
stage performs, the parent budget's own counters stay the single source of
truth, and the sum of optional plus reserved work can never exceed the policy.
The forecast and repair stages read this view, so an accounting error here
silently converts a budget refusal into a paid request.
"""
import threading

import pytest

from detector.config import diagnostic_budget_reserve
from detector.runtime import AddedBudget, AddedBudgetExceeded, OptionalBudget

POLICY = {
    "max_added_seconds_per_case": 180,
    "max_added_prompt_tokens_per_case": 60000,
    "max_added_output_tokens_per_case": 8192,
    "max_added_provider_requests_per_case": 2,
}


def parent(**changes):
    return AddedBudget({**POLICY, **changes})


def optional(pa, **changes):
    """Build the view the way a caller must: reserve capped by the live policy.

    ``diagnostic_budget_reserve`` is computed against a policy, and a reserve can
    never exceed the policy it reserves from. The helper derives it from the
    parent's own policy so a deliberately small budget stays constructible.
    """
    reserve = {}
    for key, value in diagnostic_budget_reserve(pa._policy).items():
        reserve[key] = min(value, pa._policy[key])
    reserve.update(changes)
    return OptionalBudget(pa, reserve)


def reserve(budget, size=10, output=8, name="stage"):
    return budget.reserve(system="", user="x" * size, output_tokens=output)


def test_the_view_starts_at_zero_and_explains_itself():
    pa = parent()
    view = optional(pa)
    assert pa.requests == 0 and pa.input_bytes == 0
    # No deadline exists before the first admission opens the parent's clock;
    # the view must not invent one, and it must still admit work.
    assert view.deadline is None
    assert reserve(view) == 8
    assert pa.snapshot()["diagnostic_reservation"] is not None


def test_the_reserve_is_capped_by_the_policy_it_reserves_from():
    """A small allowance has no optional capacity, and the view refuses cleanly."""
    pa = parent(max_added_prompt_tokens_per_case=200)
    view = optional(pa)
    # diagnostic_budget_reserve caps the input reservation at the policy's own
    # 200 bytes, so nothing is left for optional work. This is the documented
    # fail-closed shape, not an accounting bug.
    assert view.reserve_limits["max_added_prompt_tokens_per_case"] == 200
    with pytest.raises(AddedBudgetExceeded) as failure:
        reserve(view, size=1)
    assert failure.value.resource == "input"
    assert pa.input_bytes == 0


def test_optional_work_cannot_use_the_reserved_diagnostic_requests():
    pa = parent(max_added_provider_requests_per_case=3)
    view = optional(pa, max_added_provider_requests_per_case=1)
    # 3 total, 1 reserved for diagnostics: exactly two optional requests fit.
    assert reserve(view) == 8
    assert reserve(view) == 8
    with pytest.raises(AddedBudgetExceeded) as failure:
        reserve(view)
    assert failure.value.resource == "requests"
    # The refusal must not have been counted.
    assert pa.requests == 2


def test_optional_output_cap_is_respected_before_the_parent_is_touched():
    pa = parent(max_added_output_tokens_per_case=100)
    view = optional(pa, max_added_output_tokens_per_case=60)
    assert reserve(view, output=50) == 40
    assert pa.output_tokens == 40
    with pytest.raises(AddedBudgetExceeded) as failure:
        reserve(view, output=10)
    assert failure.value.resource == "output"
    assert pa.output_tokens == 40


@pytest.mark.parametrize("total,resource,admitted", [
    (16384, "requests", 2),
    (8192, "output", 1),
])
def test_two_forecasts_require_two_full_output_allowances(total, resource, admitted):
    pa = parent(max_added_output_tokens_per_case=total)
    view = optional(pa)
    for _ in range(admitted):
        assert reserve(view, output=8192) == 8192
    with pytest.raises(AddedBudgetExceeded) as failure:
        reserve(view, output=8192)
    assert failure.value.resource == resource
    assert failure.value.origin == "diagnostic_reservation"
    assert pa.requests == admitted
    assert pa.output_tokens == admitted * 8192


def test_optional_input_cap_counts_bytes_exactly():
    pa = parent(max_added_prompt_tokens_per_case=2000)
    view = optional(pa, max_added_prompt_tokens_per_case=120)
    reserve(view, size=1870)
    assert pa.input_bytes == 1870
    with pytest.raises(AddedBudgetExceeded) as failure:
        reserve(view, size=20)
    assert failure.value.resource == "input"
    assert pa.input_bytes == 1870


def test_context_reservation_shares_the_same_input_counter():
    pa = parent(max_added_prompt_tokens_per_case=2000)
    view = optional(pa, max_added_prompt_tokens_per_case=100)
    view.reserve_context("y" * 1890)
    assert pa.input_bytes == 1890
    with pytest.raises(AddedBudgetExceeded):
        view.reserve_context("y" * 20)
    assert pa.input_bytes == 1890


def test_a_second_view_with_a_different_reserve_is_refused():
    pa = parent(max_added_provider_requests_per_case=3)
    optional(pa)
    with pytest.raises(ValueError, match="reservation changed"):
        OptionalBudget(pa, {**diagnostic_budget_reserve(POLICY),
                            "max_added_provider_requests_per_case": 2})


def test_a_second_view_with_the_same_reserve_is_allowed():
    pa = parent()
    first = optional(pa)
    second = optional(pa)
    reserve(first)
    reserve(second)
    assert pa.requests == 2


@pytest.mark.parametrize("field,value,match", [
    ("max_added_provider_requests_per_case", 99, "invalid diagnostic reservation"),
    ("max_added_prompt_tokens_per_case", -1, "invalid diagnostic reservation"),
    ("max_added_output_tokens_per_case", True, "invalid diagnostic reservation"),
])
def test_a_reserve_outside_the_policy_is_refused(field, value, match):
    pa = parent()
    with pytest.raises(ValueError, match=match):
        OptionalBudget(pa, {**diagnostic_budget_reserve(POLICY), field: value})


def test_an_incomplete_reserve_is_refused():
    with pytest.raises(ValueError, match="incomplete diagnostic reservation"):
        OptionalBudget(parent(), {"max_added_seconds_per_case": 10})


def test_a_reserve_equal_to_the_whole_policy_leaves_no_optional_capacity():
    pa = parent()
    view = OptionalBudget(pa, dict(POLICY))
    with pytest.raises(AddedBudgetExceeded):
        reserve(view)


def test_no_optional_admission_ever_exceeds_the_parent_under_concurrency():
    pa = parent(max_added_provider_requests_per_case=40,
                max_added_prompt_tokens_per_case=10 ** 6)
    view = optional(pa, max_added_provider_requests_per_case=10)
    admitted, refused = [], []
    lock = threading.Lock()

    def attempt():
        try:
            reserve(view, size=1)
        except AddedBudgetExceeded:
            with lock:
                refused.append(1)
        else:
            with lock:
                admitted.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(64)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # 40 total minus 10 reserved: exactly 30 optional admissions, no more.
    assert len(admitted) == 30
    assert len(refused) == 34
    assert pa.requests == 30
    # The invariant is against the parent's own policy, which is 40 here.
    assert (pa.requests + view.reserve_limits["max_added_provider_requests_per_case"]
            <= pa._policy["max_added_provider_requests_per_case"])


def test_the_view_reports_the_parent_digest_so_replay_identity_is_unchanged():
    pa = parent()
    assert optional(pa).policy_digest == pa.policy_digest


def test_the_refusal_origin_distinguishes_optional_from_case_allowance():
    """A queue that used the optional allowance is not a case that ran out."""
    # 3 total, 1 reserved for diagnostics: two optional admissions fit.
    pa = parent(max_added_provider_requests_per_case=3)
    view = optional(pa, max_added_provider_requests_per_case=1)
    reserve(view)
    reserve(view)
    with pytest.raises(AddedBudgetExceeded) as failure:
        reserve(view)
    assert failure.value.origin == "diagnostic_reservation"
    # The parent's own allowance, reached without the view, reports its origin.
    exhausted = parent(max_added_provider_requests_per_case=1)
    reserve(exhausted)
    with pytest.raises(AddedBudgetExceeded) as failure:
        reserve(exhausted)
    assert failure.value.origin == "case_budget"
    # The message is unchanged, so recorded readings still parse.
    assert "added case budget exhausted: requests" in str(failure.value)


def test_the_view_deadline_keeps_the_reserved_seconds_for_diagnostics():
    pa = parent(max_added_seconds_per_case=180)
    view = optional(pa, max_added_seconds_per_case=30)
    pa.begin()
    # The optional view must stop 30s before the parent's own deadline.
    assert view.deadline == pytest.approx(pa.deadline - 30, abs=1e-6)
