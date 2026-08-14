"""Egress layers over the single transport primitive.

Each egress encodes its own contract so behavior is guaranteed by structure,
not by a ``purpose`` string shared with the primitive.

Detection path — the proof-court surfaces wrap the transport primitives and
self-record per-surface usage at call time (no post-hoc depth-1 walk). The
provider boundary is no longer a skill-injection seam: L1 rules are applied
deterministically after the court returns (plan B).

Evolution path — L1/L2/conflict-gate emit strict JSON. The chain-of-thought
toggle lives in the single platform authority (``platform.routes``): without
an explicit route setting, DeepSeek-V4 models run with thinking disabled on
every structured-JSON surface, so the reasoning budget can never truncate
before ``content`` is emitted. These calls bind the transport primitive
directly (the aliased imports), so the injection adapter never reaches them.
"""

from __future__ import annotations

from typing import Any, Mapping

from sehd_rise2.detector.pipeline.transport import (
    json_object_provider_call as _transport_json_object_call,
    provider_call as _transport_provider_call,
)
from sehd_rise2.detector.runtime.usage import ProviderUsageLedger

provider_call = _transport_provider_call
json_object_provider_call = _transport_json_object_call


def _strict_json_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str,
    ledger: ProviderUsageLedger | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Strict-JSON evolution call: usage self-recorded."""
    value, meta = _transport_json_object_call(
        route=route,
        system=system,
        user=user,
    )
    if ledger is not None:
        ledger.record(purpose=purpose, meta=meta)
    return value, meta


def l1_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str,
    ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """L1 skill-mutation egress (critic / diagnosis / targeted / policy)."""
    return _strict_json_call(
        route=route, system=system, user=user, purpose=purpose, ledger=ledger,
    )


def l2_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str = "pro",
    ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """L2 policy / pro egress (closed-vocabulary, V4 thinking off)."""
    return _strict_json_call(
        route=route, system=system, user=user, purpose=purpose, ledger=ledger,
    )


def conflict_gate_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cross-layer no-conflict gate egress (V4 thinking off, self-recorded)."""
    return _strict_json_call(
        route=route,
        system=system,
        user=user,
        purpose="conflict_gate",
        ledger=ledger,
    )


def detection_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str,
    ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Detection census-envelope egress; per-surface usage self-recorded.

    ``purpose`` names the proof-court surface (e.g. ``detector:qualification``)
    and becomes the ledger purpose verbatim, so a surface's cost is attributed
    at call time — no post-hoc walk of ``result["providers"]``. A ``None``
    ledger (standalone detection) skips recording. Reads the module-level
    ``provider_call`` seam so the injection adapter can append genome rules.
    """
    value, meta = provider_call(route=route, system=system, user=user)
    if ledger is not None:
        ledger.record(purpose=purpose, meta=meta)
    return value, meta


def detection_rankings_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str,
    ledger: ProviderUsageLedger | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Schema-relaxed detection egress (rankings contract), self-recorded.

    Reads the module-level ``json_object_provider_call`` seam so the injection
    adapter can append genome rules.
    """
    value, meta = json_object_provider_call(route=route, system=system, user=user)
    if ledger is not None:
        ledger.record(purpose=purpose, meta=meta)
    return value, meta


__all__ = [
    "conflict_gate_call",
    "detection_call",
    "detection_rankings_call",
    "l1_call",
    "l2_call",
]
