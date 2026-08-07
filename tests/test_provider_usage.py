"""Tests for the provider usage ledger (P0)."""

import pytest

from src.sehd_v6.core.provider_usage import (
    COST_UNAVAILABLE,
    SCHEMA_VERSION,
    ProviderUsageLedger,
)


def _meta(**overrides):
    value = {
        "cache_hit": False,
        "request_hash": "req_1",
        "cache_source": "local",
        "transport_attempts": 2,
        "repair_used": True,
        "latency_seconds": 1.5,
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    }
    value.update(overrides)
    return value


def test_record_distinguishes_logical_call_from_transport_attempts():
    ledger = ProviderUsageLedger()
    record = ledger.record(purpose="mutation", meta=_meta())
    assert record.transport_attempts == 2
    totals = ledger.totals()
    assert totals["logical_calls"] == 1
    assert totals["fresh_transport_calls"] == 1
    assert totals["transport_attempts"] == 2
    assert totals["prompt_tokens"] == 120
    assert totals["completion_tokens"] == 30
    assert totals["reasoning_tokens"] == 12
    assert totals["repairs"] == 1


def test_cache_hit_is_a_logical_call_with_zero_fresh_transport():
    """A cache hit served no network traffic: the cached entry's original
    attempts belong to the original record and must not be re-counted."""
    ledger = ProviderUsageLedger()
    ledger.record(purpose="mutation", meta=_meta())
    ledger.record(
        purpose="mutation",
        meta=_meta(cache_hit=True, transport_attempts=2, latency_seconds=0.0),
    )
    totals = ledger.totals()
    assert totals["logical_calls"] == 2
    assert totals["cache_hits"] == 1
    assert totals["fresh_transport_calls"] == 1
    assert totals["transport_attempts"] == 2
    # Billed tokens accumulate over fresh transport only.
    assert totals["prompt_tokens"] == 120


def test_logical_call_ids_are_deterministic_and_distinct_per_repeat():
    first = ProviderUsageLedger()
    second = ProviderUsageLedger()
    for ledger in (first, second):
        ledger.record(purpose="mutation", meta=_meta())
        ledger.record(purpose="mutation", meta=_meta())
    ids = [record.logical_call_id for record in first.records]
    assert ids == [record.logical_call_id for record in second.records]
    assert ids[0] != ids[1]
    explicit = ProviderUsageLedger()
    record = explicit.record(
        purpose="detector", meta=_meta(), logical_call_id="case_1:census",
    )
    assert record.logical_call_id == "case_1:census"


def test_unknown_cost_is_unavailable_never_zero():
    ledger = ProviderUsageLedger()
    record = ledger.record(purpose="detector", meta=_meta())
    assert record.cost_status == COST_UNAVAILABLE
    assert ledger.totals()["cost_status"] == COST_UNAVAILABLE
    assert "cost" not in ledger.totals()


def test_missing_meta_degrades_to_zero_record():
    ledger = ProviderUsageLedger()
    record = ledger.record(purpose="mutation", meta=None)
    assert record.transport_attempts == 0
    assert record.prompt_tokens == 0
    assert record.cache_hit is False


def test_round_trip_preserves_records():
    ledger = ProviderUsageLedger()
    ledger.record(purpose="mutation", meta=_meta())
    ledger.record(purpose="policy_reflection", meta=_meta(cache_hit=True))
    restored = ProviderUsageLedger.from_dict(ledger.to_dict())
    assert restored.to_dict() == ledger.to_dict()


def test_string_false_booleans_are_not_coerced_to_true():
    """A persisted string 'false' must not load as True (bool('false'))."""
    from src.sehd_v6.core.provider_usage import UsageRecord

    record = UsageRecord.from_dict({
        "logical_call_id": "call_1",
        "purpose": "mutation",
        "request_hash": "h",
        "cache_hit": "false",
        "repair_used": "false",
        "transport_attempts": 0,
    })
    assert record.cache_hit is False
    assert record.repair_used is False

    record_true = UsageRecord.from_dict({
        "logical_call_id": "call_2",
        "purpose": "mutation",
        "request_hash": "h",
        "cache_hit": True,
        "transport_attempts": 0,
    })
    assert record_true.cache_hit is True


def test_unknown_schema_version_is_rejected():
    with pytest.raises(ValueError, match="unsupported usage ledger schema"):
        ProviderUsageLedger.from_dict({"schema_version": "v0", "records": []})


def test_totals_by_purpose():
    ledger = ProviderUsageLedger()
    ledger.record(purpose="mutation", meta=_meta())
    ledger.record(purpose="mutation", meta=_meta())
    ledger.record(purpose="detector", meta=_meta())
    assert ledger.totals()["by_purpose"] == {"detector": 1, "mutation": 2}
    assert SCHEMA_VERSION == "sehd.provider_usage_ledger.v1"
