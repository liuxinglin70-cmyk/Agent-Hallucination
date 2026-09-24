"""``accumulate_usage`` and the ``usage_total`` it feeds on voting stages.

Why this exists. A vote-based stage returns its *last* ballot's provider meta
as the stage's sample, so the ``usage`` field records one ballot and not the
vote. Nothing downstream gates on it, which is exactly why the undercount
survived: it only misled the one question a run's cost must answer -- how many
tokens did this actually burn. Measured on 2026-09-21, the judge carried 3
ballots plus up to 2 topups, so its real burn is several times its report.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from detector.provider import accumulate_usage  # noqa: E402


def test_sums_numeric_leaves_and_nests_details_blocks():
    total: dict = {}
    accumulate_usage(total, {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "completion_tokens_details": {"reasoning_tokens": 4},
    })
    accumulate_usage(total, {
        "prompt_tokens": 20,
        "completion_tokens": 7,
        "completion_tokens_details": {"reasoning_tokens": 6},
    })
    assert total["prompt_tokens"] == 30
    assert total["completion_tokens"] == 12
    assert total["completion_tokens_details"]["reasoning_tokens"] == 10


def test_a_new_provider_counter_is_totalled_without_a_code_change():
    # The function must not carry a whitelist of field names: a gateway that
    # starts reporting a new counter would otherwise be silently dropped.
    total: dict = {}
    accumulate_usage(total, {"prompt_cache_read_tokens": 3, "novel_metric": 1})
    accumulate_usage(total, {"prompt_cache_read_tokens": 4, "novel_metric": 2})
    assert total["prompt_cache_read_tokens"] == 7
    assert total["novel_metric"] == 3


def test_booleans_are_not_summed_as_integers():
    # ``bool`` is a subclass of ``int``; summing it would report 2 cache hits
    # where the field means "was this a hit".
    total: dict = {}
    accumulate_usage(total, {"cache_hit": True})
    assert total.get("cache_hit") is None


def test_non_numeric_leaves_are_left_alone():
    total: dict = {}
    accumulate_usage(total, {"model": "glm-5.3-flash", "prompt_tokens": 5})
    assert total["prompt_tokens"] == 5
    assert "model" not in total


def test_empty_and_bool_payloads_are_safe():
    total: dict = {}
    accumulate_usage(total, {})
    assert total == {}
    accumulate_usage(total, {"cache_hit": False, "prompt_tokens": 2})
    assert total["prompt_tokens"] == 2
    assert "cache_hit" not in total


def test_a_vote_total_exceeds_its_last_ballot():
    """The property the fix exists for, stated as an assertion."""
    ballots = [
        {"completion_tokens": 900, "completion_tokens_details": {"reasoning_tokens": 800}},
        {"completion_tokens": 1200, "completion_tokens_details": {"reasoning_tokens": 1100}},
        {"completion_tokens": 700, "completion_tokens_details": {"reasoning_tokens": 600}},
    ]
    total: dict = {}
    for ballot in ballots:
        accumulate_usage(total, ballot)
    assert total["completion_tokens"] == sum(b["completion_tokens"] for b in ballots)
    # And the old field would have reported only the last one.
    assert total["completion_tokens"] > ballots[-1]["completion_tokens"]
