"""Provider usage ledger: one auditable record per logical provider call.

A logical call is what the framework asked for (one detection replay, one
reflection, one registry/meta selection); transport attempts are the
physical HTTP requests the transport layer made to satisfy it, including
retries and the bounded JSON repair. A cache hit is a logical call with
zero fresh transport: its recorded usage belongs to the original call and
is reported but never re-billed.

Cost is derived only from a provider price table the caller supplies; with
no price table every record carries ``cost_status="unavailable"`` — an
unknown cost is never reported as zero.

Schema: ``sehd.provider_usage_ledger.v1``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "sehd.provider_usage_ledger.v1"

COST_UNAVAILABLE = "unavailable"
COST_ESTIMATED = "estimated"


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _nonnegative_float(value: Any) -> float:
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return result if result >= 0 else 0.0


def _usage_tokens(usage: Mapping[str, Any]) -> dict[str, int]:
    """Normalize a provider-billed usage object to the ledger token fields."""
    details = usage.get("completion_tokens_details")
    if not isinstance(details, Mapping):
        details = {}
    return {
        "prompt_tokens": _nonnegative_int(usage.get("prompt_tokens")),
        "completion_tokens": _nonnegative_int(usage.get("completion_tokens")),
        "reasoning_tokens": _nonnegative_int(details.get("reasoning_tokens")),
    }


@dataclass(frozen=True)
class UsageRecord:
    """One logical provider call and the transport behind it."""

    logical_call_id: str
    purpose: str
    request_hash: str
    cache_hit: bool
    cache_source: str
    transport_attempts: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    latency_seconds: float
    repair_used: bool
    cost_status: str = COST_UNAVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_call_id": self.logical_call_id,
            "purpose": self.purpose,
            "request_hash": self.request_hash,
            "cache_hit": self.cache_hit,
            "cache_source": self.cache_source,
            "transport_attempts": self.transport_attempts,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "latency_seconds": self.latency_seconds,
            "repair_used": self.repair_used,
            "cost_status": self.cost_status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UsageRecord":
        return cls(
            logical_call_id=str(value.get("logical_call_id") or ""),
            purpose=str(value.get("purpose") or ""),
            request_hash=str(value.get("request_hash") or ""),
            cache_hit=bool(value.get("cache_hit")),
            cache_source=str(value.get("cache_source") or ""),
            transport_attempts=_nonnegative_int(value.get("transport_attempts")),
            prompt_tokens=_nonnegative_int(value.get("prompt_tokens")),
            completion_tokens=_nonnegative_int(value.get("completion_tokens")),
            reasoning_tokens=_nonnegative_int(value.get("reasoning_tokens")),
            latency_seconds=_nonnegative_float(value.get("latency_seconds")),
            repair_used=bool(value.get("repair_used")),
            cost_status=str(value.get("cost_status") or COST_UNAVAILABLE),
        )


class ProviderUsageLedger:
    """Append-only ledger of logical provider calls.

    ``record`` derives identity deterministically: a caller-supplied
    ``logical_call_id`` wins; otherwise the request hash plus the per-purpose
    sequence keeps repeat logical calls of an identical request distinct.
    """

    def __init__(self, records: Sequence[UsageRecord] = ()) -> None:
        self._records: list[UsageRecord] = list(records)

    def record(
        self,
        *,
        purpose: str,
        meta: Mapping[str, Any] | None,
        logical_call_id: str = "",
    ) -> UsageRecord:
        """Append one logical call from a provider-call metadata object."""
        meta = meta if isinstance(meta, Mapping) else {}
        usage = meta.get("usage")
        tokens = _usage_tokens(usage if isinstance(usage, Mapping) else {})
        cache_hit = bool(meta.get("cache_hit"))
        call_id = str(logical_call_id or "")
        if not call_id:
            purpose_sequence = sum(
                1 for record in self._records if record.purpose == purpose
            )
            call_id = hashlib.sha256(
                json.dumps(
                    {
                        "purpose": str(purpose or ""),
                        "request_hash": str(meta.get("request_hash") or ""),
                        "purpose_sequence": purpose_sequence,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:16]
        record = UsageRecord(
            logical_call_id=call_id,
            purpose=str(purpose or ""),
            request_hash=str(meta.get("request_hash") or ""),
            cache_hit=cache_hit,
            cache_source=str(meta.get("cache_source") or ""),
            # A cache hit served zero network traffic for THIS logical call;
            # the cached entry's original attempts are reported by the
            # original record, not re-counted here.
            transport_attempts=(
                0 if cache_hit else _nonnegative_int(meta.get("transport_attempts"))
            ),
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            reasoning_tokens=tokens["reasoning_tokens"],
            latency_seconds=_nonnegative_float(meta.get("latency_seconds")),
            repair_used=bool(meta.get("repair_used")),
        )
        self._records.append(record)
        return record

    @property
    def records(self) -> tuple[UsageRecord, ...]:
        return tuple(self._records)

    def totals(self) -> dict[str, Any]:
        """Aggregate the ledger, keeping logical and transport counts apart."""
        fresh = [record for record in self._records if not record.cache_hit]
        return {
            "logical_calls": len(self._records),
            "cache_hits": sum(1 for record in self._records if record.cache_hit),
            "fresh_transport_calls": len(fresh),
            "transport_attempts": sum(
                record.transport_attempts for record in self._records
            ),
            "prompt_tokens": sum(
                record.prompt_tokens for record in fresh
            ),
            "completion_tokens": sum(
                record.completion_tokens for record in fresh
            ),
            "reasoning_tokens": sum(
                record.reasoning_tokens for record in fresh
            ),
            "latency_seconds": sum(
                record.latency_seconds for record in self._records
            ),
            "repairs": sum(1 for record in self._records if record.repair_used),
            "cost_status": COST_UNAVAILABLE,
            "by_purpose": {
                purpose: sum(
                    1 for record in self._records if record.purpose == purpose
                )
                for purpose in sorted(
                    {record.purpose for record in self._records}
                )
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "records": [record.to_dict() for record in self._records],
            "totals": self.totals(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderUsageLedger":
        if not isinstance(value, Mapping):
            raise ValueError("usage ledger artifact must contain an object")
        schema = str(value.get("schema_version") or "")
        if schema != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported usage ledger schema_version: {schema!r} "
                f"(expected {SCHEMA_VERSION!r})"
            )
        return cls(
            UsageRecord.from_dict(dict(row))
            for row in value.get("records") or ()
            if isinstance(row, Mapping)
        )
