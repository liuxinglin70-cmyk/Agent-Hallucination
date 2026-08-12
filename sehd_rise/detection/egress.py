"""Egress layers over the single transport primitive.

Each egress encodes its own contract so behavior is guaranteed by structure,
not by a ``purpose`` string shared with the primitive.

Detection path — the proof-court surfaces wrap the transport primitives and
self-record per-surface usage at call time (no post-hoc depth-1 walk). The two
module-level names ``provider_call`` / ``json_object_provider_call`` are the
injection seam: ``detection.injection.integrated_engine_provider`` patches them
to append a genome's evolved rules to detection prompts. They exist only here,
on the detection path.

Evolution path — L1/L2/conflict-gate emit strict JSON and structurally silence
chain-of-thought on DeepSeek-V4 (the reasoning budget can truncate before any
``content`` is emitted, ``finish_reason=length``, empty body, which the
strict-JSON transport fail-closes). These calls bind the transport primitive
directly (the aliased imports), so the injection adapter never reaches them.
"""

from __future__ import annotations

from typing import Any, Mapping

from sehd_rise.detection.transport import (
    json_object_provider_call as _transport_json_object_call,
    provider_call as _transport_provider_call,
)
from sehd_rise.platform.models import canonical_model_name
from sehd_rise.platform.usage import ProviderUsageLedger

# Detection-path injection seam. The skill-injection adapter patches these
# two module-level names; evolution-path egress never reads them.
provider_call = _transport_provider_call
json_object_provider_call = _transport_json_object_call


def _is_deepseek_v4(model: Any) -> bool:
    """Return whether the model resolves to a DeepSeek-V4 variant."""
    name = str(model or "").strip()
    if not name:
        return False
    try:
        name = canonical_model_name(name)
    except ValueError:
        return False
    return name.lower().startswith("deepseek-v4")


def _with_v4_thinking_off(route: Mapping[str, Any]) -> dict[str, Any]:
    """Return a route whose DeepSeek-V4 models emit no chain-of-thought.

    A V4 route is stamped ``thinking.type=disabled`` over any explicit
    setting; a non-V4 route passes through unchanged. The primitive's
    ``provider_thinking_control`` honors an explicit route setting first, so
    this stamp is the thinking authority — no ``purpose`` string is consulted.
    The input route is never mutated.
    """
    stamped = dict(route)
    if _is_deepseek_v4(stamped.get("model")):
        stamped["thinking"] = {"type": "disabled"}
    return stamped


def _strict_json_call(
    *,
    route: Mapping[str, Any],
    system: str,
    user: str,
    purpose: str,
    ledger: ProviderUsageLedger | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Strict-JSON evolution call: V4 thinking off, usage self-recorded."""
    route = _with_v4_thinking_off(route)
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
