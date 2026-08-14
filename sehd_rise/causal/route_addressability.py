"""RouteAddressability authority (Wave 2 design D2): the closed table of
replayable intervention points.

Once the capability registry stops deciding learning admission, "a
replayable intervention point exists for this failure edge" needs its own
closed authority — otherwise every consumer improvises a second table. This
module is that authority: for each evolvable L1 owner (the cut vocabulary,
``MODULE_IDS``) it declares

- ``producer_route`` -- the detector provider route whose prompt produces
  the artifact the owner is responsible for;
- ``replay_scope`` -- the frozen downstream provider-route chain replayed
  from the entry route; the deterministic proof court always recomputes the
  terminal after the last route (the court is not a provider route, so it
  is not listed);
- ``binding_domains`` -- the condition-binding domains whose entities the
  guidance attaches to;
- ``observable_postcondition`` -- the fingerprint-compared assertion object
  the drift-tolerant attribution (D3) reads after a replay.

The detector's named provider routes (``detection.detector.REQUIRED_ROUTES``)
are the route-side closed vocabulary; the test suite pins every
``producer_route``/``replay_scope`` entry as a member. Three routes
(``candidate_fallback_route`` / ``appeal_prosecutor_route`` /
``appeal_defender_route``) carry no L1 owner: appeal-mechanism cuts are
L2-owned and evolve through the vocabulary registry, not route injection.

L2 artifact owners are deliberately absent from this table: their repair
surface is the adjudication vocabulary, served by registry evolution. An
owner with neither a deterministic backend nor an entry here is the only
true ``CAPABILITY_GAP``; a declared-evolvable owner whose entry is missing
is an ``UNADDRESSABLE_FRAMEWORK_DEFECT`` — the import-time closure check
below makes that state unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sehd_rise.skills.capabilities import OWNER_PRODUCER_ROUTE
from sehd_rise.skills.vocabulary.contracts import MODULE_IDS


@dataclass(frozen=True)
class RouteAddress:
    """One owner's replayable intervention point."""

    owner: str
    producer_route: str
    replay_scope: tuple[str, ...]
    binding_domains: tuple[str, ...]
    observable_postcondition: str


_FROZEN_VERIFICATION_CHAIN = (
    "qualification_route",
    "propagation_route",
    "focused_prosecution_route",
    "defense_route",
)
_PROSECUTION_CHAIN = ("focused_prosecution_route", "defense_route")


def _route(owner: str) -> str:
    return OWNER_PRODUCER_ROUTE[owner]


ROUTE_ADDRESSABILITY: Mapping[str, RouteAddress] = {
    "candidate_trigger": RouteAddress(
        owner="candidate_trigger",
        producer_route=_route("candidate_trigger"),
        replay_scope=(_route("candidate_trigger"), *_FROZEN_VERIFICATION_CHAIN),
        binding_domains=("claim_anchor", "candidate"),
        observable_postcondition="census_candidate_fingerprints",
    ),
    "candidate_to_verifier_route": RouteAddress(
        owner="candidate_to_verifier_route",
        producer_route=_route("candidate_to_verifier_route"),
        replay_scope=_FROZEN_VERIFICATION_CHAIN,
        binding_domains=("candidate",),
        observable_postcondition="candidate_qualification_states",
    ),
    "causal_link_test": RouteAddress(
        owner="causal_link_test",
        producer_route=_route("causal_link_test"),
        replay_scope=(_route("causal_link_test"), *_PROSECUTION_CHAIN),
        binding_domains=("candidate", "origin"),
        observable_postcondition="propagation_dependency_edges",
    ),
    "atomic_verifier_instruction": RouteAddress(
        owner="atomic_verifier_instruction",
        producer_route=_route("atomic_verifier_instruction"),
        replay_scope=_PROSECUTION_CHAIN,
        binding_domains=("certificate",),
        observable_postcondition="certificate_audit_fingerprints",
    ),
    "evidence_obligation": RouteAddress(
        owner="evidence_obligation",
        producer_route=_route("evidence_obligation"),
        replay_scope=_PROSECUTION_CHAIN,
        binding_domains=("certificate", "coverage"),
        observable_postcondition="certificate_audit_fingerprints",
    ),
    "earliest_origin_tiebreak": RouteAddress(
        owner="earliest_origin_tiebreak",
        producer_route=_route("earliest_origin_tiebreak"),
        replay_scope=(_route("earliest_origin_tiebreak"),),
        binding_domains=("origin", "boundary"),
        observable_postcondition="boundary_path_state",
    ),
}

_MISSING_OWNERS = [module for module in MODULE_IDS if module not in ROUTE_ADDRESSABILITY]
if _MISSING_OWNERS:
    raise ValueError(
        "route addressability incomplete for evolvable L1 owners: "
        + ", ".join(_MISSING_OWNERS)
    )


def address_for(owner: str) -> RouteAddress | None:
    """The owner's replayable intervention point, or None (true gap)."""
    return ROUTE_ADDRESSABILITY.get(str(owner or ""))


__all__ = [
    "ROUTE_ADDRESSABILITY",
    "RouteAddress",
    "address_for",
]
