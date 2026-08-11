"""Typed repair-vocabulary translation for proof-court certificate audits.

The proof court reports predicate names (for example ``ordered_chain``),
whereas providers emit certificate fields.  Treating those two namespaces as
the same made reflection propose field edits for non-existent fields.  This
module is the single translation owner used by both the court and evolution:

* ``failed_checks`` names the violated validation predicates;
* ``missing_fields`` names provider fields that are actually empty/absent;
* ``failed_fields`` names all provider fields participating in them;
* ``failed_invariants`` names predicates that require a relational repair.

The three projections deliberately coexist.  A field repair is appropriate
for an empty scalar/list; an invariant repair is appropriate when populated
fields fail a cross-field or trajectory-addressability relation.

The underlying check-to-field tables are owned by the closed-vocabulary
authority (``skills.vocabulary.contracts``); this module derives projections
from them and never re-declares them.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sehd_rise.skills.vocabulary.contracts import (
    DEFENSE_CHECK_FIELDS,
    DEFENSE_INVARIANT_TARGETS,
    DEFENSE_RELATIONAL_INVARIANTS,
    ERROR_CHECK_FIELDS,
    ERROR_INVARIANT_TARGETS,
    ERROR_RELATIONAL_INVARIANTS,
)

__all__ = [
    "DEFENSE_CHECK_FIELDS",
    "DEFENSE_INVARIANT_TARGETS",
    "DEFENSE_RELATIONAL_INVARIANTS",
    "ERROR_CHECK_FIELDS",
    "ERROR_INVARIANT_TARGETS",
    "ERROR_RELATIONAL_INVARIANTS",
    "project_audit_failures",
]


def project_audit_failures(
    *,
    row: Mapping[str, Any],
    failed_checks: Sequence[str],
    required_fields: Sequence[str],
    check_fields: Mapping[str, Sequence[str]],
    relational_invariants: frozenset[str],
) -> dict[str, list[str]]:
    """Return normalized provider-field and invariant repair targets."""
    failed = sorted({str(name) for name in failed_checks if str(name)})
    missing_required = (
        sorted(set(str(name) for name in required_fields).difference(row))
        if "required_fields" in failed else []
    )
    fields = set(missing_required)
    missing = set(missing_required)
    invariants: set[str] = set()
    for check in failed:
        if check == "required_fields":
            continue
        participating = tuple(
            str(name) for name in check_fields.get(check, ())
        )
        fields.update(participating)
        missing.update(
            name
            for name in participating
            if row.get(name) in (None, "", [], {})
        )
        if check in relational_invariants:
            invariants.add(check)
    return {
        "missing_fields": sorted(missing),
        "failed_fields": sorted(fields),
        "failed_invariants": sorted(invariants),
    }
