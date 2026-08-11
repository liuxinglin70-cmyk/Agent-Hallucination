"""Content-anchored certificate identity for cross-replay comparisons.

Rule injection re-samples the detection provider.  The provider freely
re-enumerates its certificates, and ``_merge_prosecutions`` then re-mints
positional ids (``{initial,appeal}_error_{index}``) from enumeration order.
The e2e drift probe showed an BFCL/000 replay shift its whole census from
``appeal_error_001..005`` to ``initial_error_001..002`` while the surviving
claim — and the verdict — stayed the same.

Positional ids therefore carry no cross-run identity: comparing
``surviving_error_certificate_ids`` between a baseline and a replay conflates
renaming with removal.  Any acceptance decision built on that comparison
is noise (the old ``targeted_certificate_withheld`` postcondition reported
"withheld" on a pure rename, and whole-surface counts move whenever the
provider reorganizes its census).

The claim-level identity of a certificate is its categorical signature —
origin event, breach, claim kind, norm source, impact kind.  These fields
are closed-vocabulary values or deterministic packet event anchors, so the
same claim keeps the same fingerprint across re-sampling while a genuinely
different claim never collides with it.  Adjudicative fields
(``benchmark_causal_status``, ``recovery_state``) and free text
(``rationale``) are deliberately excluded: they are outputs the evolution
is allowed to move, not the claim's identity.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

# Closed-vocabulary / deterministic-anchor fields that name a claim.
# Positional ids, candidate ids, adjudicative outcomes, evidence citation
# lists and free text are all excluded on purpose.
CLAIM_IDENTITY_FIELDS: tuple[str, ...] = (
    "origin_event_id",
    "breach_type",
    "claim_kind",
    "norm_source",
    "impact_kind",
)


def certificate_claim_fingerprint(row: Mapping[str, Any]) -> str:
    """Stable content fingerprint of one certificate row.

    Returns an empty string when the row carries none of the identity
    fields; callers then fall back to the row's own id token.
    """
    key = {
        field: " ".join(str(row.get(field) or "").split())
        for field in CLAIM_IDENTITY_FIELDS
    }
    if not any(key.values()):
        return ""
    canonical = json.dumps(key, ensure_ascii=False, sort_keys=True)
    return "fp:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _prosecution_rows(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("combined_prosecution", "prosecution"):
        payload = result.get(key)
        if not isinstance(payload, Mapping):
            continue
        rows = payload.get("candidates") or payload.get("certificates") or []
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, Mapping)]
    return []


def surviving_carrier_fingerprints(
    result: Mapping[str, Any] | None,
) -> frozenset[str] | None:
    """Content fingerprints of the court's surviving error carriers.

    ``None`` means the result never reported a surviving set (fail-closed
    callers keep their unreported semantics).  A surviving id whose row is
    absent from the prosecution surface (e.g. a 32-row merge truncation)
    contributes an opaque id token: it keeps the legacy positional behavior
    for that row only, and marks the projection as unresolved for strict
    consumers.
    """
    if not isinstance(result, Mapping):
        return None
    proof = result.get("proof_adjudication")
    if not isinstance(proof, Mapping):
        return None
    surviving = proof.get("surviving_error_certificate_ids")
    if not isinstance(surviving, list):
        return None
    rows_by_id = {
        str(row.get("certificate_id") or ""): row
        for row in _prosecution_rows(result)
        if str(row.get("certificate_id") or "")
    }
    tokens: set[str] = set()
    for value in surviving:
        cid = str(value or "")
        if not cid:
            continue
        row = rows_by_id.get(cid)
        fingerprint = certificate_claim_fingerprint(row) if row else ""
        tokens.add(fingerprint if fingerprint else f"id:{cid}")
    return frozenset(tokens)


def certificate_carrier_movement(
    before_result: Mapping[str, Any] | None,
    after_result: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Claim-level carrier movement between a baseline and a replay.

    Returns ``None`` when either side never reported a surviving set.
    ``withheld`` are baseline carrier fingerprints no longer surviving in
    the replay; ``emitted`` are replay carriers with no baseline ancestor.
    A pure positional rename yields two empty movement sets;
    ``resolved`` is False when any carrier had to fall back to its opaque
    positional id, in which case strict consumers must fail closed rather
    than read rename artifacts as claim movement.
    """
    before = surviving_carrier_fingerprints(before_result)
    after = surviving_carrier_fingerprints(after_result)
    if before is None or after is None:
        return None
    resolved = not any(token.startswith("id:") for token in before | after)
    return {
        "before": frozenset(before),
        "after": frozenset(after),
        "withheld": frozenset(before - after),
        "emitted": frozenset(after - before),
        "resolved": resolved,
    }
