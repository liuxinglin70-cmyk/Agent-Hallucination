"""Entity-level grounding for L1 Skill applicability.

Conditions are evaluated over one capability-declared entity domain.  Every
atom returns entity bindings; ``all_of`` intersects by entity identity,
``any_of`` unions, and ``not`` performs an anti-join against the same domain
universe.  A true aggregate predicate therefore cannot authorize an action on
an unrelated candidate, certificate, origin, coverage row, boundary, or event.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from sehd_rise.skills.capabilities import (
    BINDING_CARDINALITIES,
    BINDING_DOMAINS,
)
from sehd_rise.skills.anchors import project_claim_anchors, select_relevant_anchors
from sehd_rise.skills.rule import (
    ConditionSpec,
    normalize_candidate_kind,
    normalize_module_tag,
    sanitize_condition_spec,
)


BINDING_BOUND = "BOUND"
BINDING_NOT_FOUND = "BINDING_NOT_FOUND"
BINDING_AMBIGUOUS = "BINDING_AMBIGUOUS"
BINDING_DOMAIN_MISMATCH = "BINDING_DOMAIN_MISMATCH"

@dataclass(frozen=True)
class Binding:
    """One grounded entity plus the local facts that justified the match."""

    domain: str
    entity_id: str
    identifiers: Mapping[str, str]
    parent_ids: Mapping[str, str] = field(default_factory=dict)
    features: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    matched_features: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return self.domain, self.entity_id

    def with_matched_features(self, values: Sequence[str]) -> "Binding":
        return Binding(
            domain=self.domain,
            entity_id=self.entity_id,
            identifiers=dict(self.identifiers),
            parent_ids=dict(self.parent_ids),
            features=dict(self.features),
            evidence_refs=self.evidence_refs,
            matched_features=tuple(sorted(set(values))),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_refs"] = list(self.evidence_refs)
        payload["matched_features"] = list(self.matched_features)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Binding":
        return cls(
            domain=str(value.get("domain") or ""),
            entity_id=str(value.get("entity_id") or ""),
            identifiers={
                str(key): str(item)
                for key, item in dict(value.get("identifiers") or {}).items()
                if str(key) and str(item)
            },
            parent_ids={
                str(key): str(item)
                for key, item in dict(value.get("parent_ids") or {}).items()
                if str(key) and str(item)
            },
            features=dict(value.get("features") or {}),
            evidence_refs=tuple(
                str(item) for item in value.get("evidence_refs") or () if str(item)
            ),
            matched_features=tuple(
                str(item) for item in value.get("matched_features") or () if str(item)
            ),
        )


@dataclass(frozen=True)
class BindingMatch:
    status: str
    domain: str
    cardinality: str
    bindings: tuple[Binding, ...]
    reason: str

    @property
    def bound(self) -> bool:
        return self.status == BINDING_BOUND

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "domain": self.domain,
            "cardinality": self.cardinality,
            "binding_count": len(self.bindings),
            "bindings": [binding.to_dict() for binding in self.bindings],
            "reason": self.reason,
        }


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value if str(item))
    return (str(value),) if str(value or "") else ()


def _evidence_refs(*rows: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for row in rows:
        for key in ("evidence_refs", "norm_evidence_refs", "impact_evidence_refs"):
            refs.extend(_strings(row.get(key)))
    return tuple(dict.fromkeys(refs))


def _record_features(record: Mapping[str, Any] | None) -> dict[str, Any]:
    roles: set[str] = set()
    tool_names: list[str] = []
    has_tool_error = False
    if isinstance(record, Mapping):
        for row in record.get("history") or ():
            if not isinstance(row, Mapping):
                continue
            role = str(row.get("role") or "")
            if role:
                roles.add(role)
            for call in row.get("tool_calls") or ():
                if isinstance(call, Mapping):
                    name = str(call.get("name") or "")
                    if name:
                        tool_names.append(name)
            for response in row.get("tool_responses") or ():
                lowered = str(response or "").lower()
                if any(token in lowered for token in ("error", "exception", "failed")):
                    has_tool_error = True
    return {
        "event_role": sorted(roles),
        "has_tool_call": bool(tool_names),
        "has_tool_error": has_tool_error,
        "has_tool_retry": len(tool_names) != len(set(tool_names)),
    }


def _certificate_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("combined_prosecution", "prosecution"):
        payload = result.get(key)
        rows = payload.get("candidates") if isinstance(payload, Mapping) else None
        mapped = [dict(row) for row in rows or () if isinstance(row, Mapping)]
        if mapped:
            return mapped
    return []


def _audit_by_certificate(result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    proof = result.get("proof_adjudication")
    audits = proof.get("error_audits") if isinstance(proof, Mapping) else ()
    mapped: dict[str, dict[str, Any]] = {}
    for audit in audits or ():
        if not isinstance(audit, Mapping):
            continue
        certificate_id = str(audit.get("certificate_id") or "")
        if certificate_id:
            mapped[certificate_id] = dict(audit)
    return mapped


def _candidate_features(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_kind": normalize_candidate_kind(row.get("candidate_kind")),
        "candidate_axis": list(_strings(row.get("axes"))),
        "module_tag": normalize_module_tag(row.get("module_tag")),
        "qualification_state": str(
            row.get("qualification_state") or row.get("violation_state") or "unknown"
        ).lower(),
    }


def project_binding_universe(
    result: Mapping[str, Any],
    *,
    domain: str,
    record: Mapping[str, Any] | None = None,
) -> tuple[Binding, ...]:
    """Project typed entity rows for one capability input domain."""
    if domain not in BINDING_DOMAINS:
        return ()
    common = _record_features(record)
    census = [
        dict(row) for row in result.get("census") or () if isinstance(row, Mapping)
    ]
    candidates = {
        str(row.get("candidate_id") or ""): row
        for row in census if str(row.get("candidate_id") or "")
    }
    certificates = _certificate_rows(result)
    audits = _audit_by_certificate(result)
    proof = result.get("proof_adjudication")
    proof = dict(proof) if isinstance(proof, Mapping) else {}
    surviving = {str(item) for item in proof.get("surviving_error_certificate_ids") or ()}

    certs_by_candidate: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for cert in certificates:
        cid = str(cert.get("certificate_id") or "")
        parent = str(cert.get("source_candidate_id") or "")
        certs_by_candidate.setdefault(parent, []).append((cert, audits.get(cid, {})))

    bindings: list[Binding] = []
    if domain == "claim_anchor":
        if isinstance(record, Mapping):
            for anchor in project_claim_anchors(record):
                bindings.append(Binding(
                    domain=domain,
                    entity_id=anchor.anchor_id,
                    identifiers={
                        "anchor_id": anchor.anchor_id,
                        "source_event_id": anchor.source_event_id,
                        "span_start": str(anchor.span_start),
                        "span_end": str(anchor.span_end),
                        "span_text_hash": anchor.span_text_hash,
                    },
                    parent_ids={"record_id": anchor.record_id},
                    features=dict(anchor.observable_features),
                    evidence_refs=(anchor.source_event_id, *anchor.upstream_ref_ids),
                ))
    elif domain == "candidate":
        for candidate_id, row in candidates.items():
            related = certs_by_candidate.get(candidate_id, [])
            missing = sorted({
                str(item) for _cert, audit in related
                for item in audit.get("missing_fields") or () if str(item)
            })
            failed = sorted({
                str(item) for _cert, audit in related
                for item in (
                    list(audit.get("failed_invariants") or ())
                    + list(audit.get("failed_checks") or ())
                ) if str(item)
            })
            verified = any(
                str(cert.get("certificate_id") or "") in surviving
                or str(audit.get("decision") or "") == "pass"
                for cert, audit in related
            )
            features = {
                **common, **_candidate_features(row),
                "missing_field": missing,
                "failed_invariant": failed,
                "evidence_verified": verified,
            }
            origin = str(row.get("origin_hypothesis_event_id") or "")
            bindings.append(Binding(
                domain=domain, entity_id=candidate_id,
                identifiers={"candidate_id": candidate_id},
                parent_ids=({"origin_event_id": origin} if origin else {}),
                features=features,
                evidence_refs=_evidence_refs(row),
            ))
    elif domain == "certificate":
        for row in certificates:
            certificate_id = str(row.get("certificate_id") or "")
            if not certificate_id:
                continue
            candidate_id = str(row.get("source_candidate_id") or "")
            origin = str(row.get("origin_event_id") or "")
            audit = audits.get(certificate_id, {})
            features = {
                **common,
                **_candidate_features(candidates.get(candidate_id, {})),
                "missing_field": list(_strings(audit.get("missing_fields"))),
                "failed_invariant": list(dict.fromkeys(
                    _strings(audit.get("failed_invariants"))
                    + _strings(audit.get("failed_checks"))
                )),
                "evidence_verified": (
                    certificate_id in surviving
                    or str(audit.get("decision") or "") == "pass"
                ),
            }
            bindings.append(Binding(
                domain=domain, entity_id=certificate_id,
                identifiers={"certificate_id": certificate_id},
                parent_ids={
                    key: value for key, value in {
                        "candidate_id": candidate_id, "origin_event_id": origin,
                    }.items() if value
                },
                features=features,
                evidence_refs=_evidence_refs(row, audit),
            ))
    elif domain == "origin":
        origin_rows: dict[str, list[Binding]] = {}
        for binding in project_binding_universe(result, domain="candidate", record=record):
            origin = str(binding.parent_ids.get("origin_event_id") or "")
            if origin:
                origin_rows.setdefault(origin, []).append(binding)
        for binding in project_binding_universe(result, domain="certificate", record=record):
            origin = str(binding.parent_ids.get("origin_event_id") or "")
            if origin:
                origin_rows.setdefault(origin, []).append(binding)
        for row in census:
            origin = str(row.get("origin_hypothesis_event_id") or "")
            if origin:
                origin_rows.setdefault(origin, [])
        for origin, children in origin_rows.items():
            feature_names = set(common)
            for child in children:
                feature_names.update(child.features)
            features: dict[str, Any] = dict(common)
            for name in feature_names:
                values = [child.features.get(name) for child in children]
                flat = [item for value in values for item in _strings(value)]
                if flat:
                    features[name] = sorted(set(flat))
            candidate_ids = sorted({
                str(child.parent_ids.get("candidate_id") or "")
                for child in children if child.parent_ids.get("candidate_id")
            })
            bindings.append(Binding(
                domain=domain, entity_id=origin,
                identifiers={"origin_event_id": origin},
                parent_ids={"candidate_ids": ",".join(candidate_ids)} if candidate_ids else {},
                features=features,
                evidence_refs=tuple(dict.fromkeys(
                    ref for child in children for ref in child.evidence_refs
                )),
            ))
    elif domain == "coverage":
        for side in ("prosecution", "defense"):
            state = str(proof.get(f"{side}_coverage") or "unknown").lower()
            bindings.append(Binding(
                domain=domain, entity_id=side,
                identifiers={"coverage_side": side},
                features={**common, "coverage_state": state, "coverage_side": side},
            ))
    elif domain == "boundary":
        boundary = result.get("clean_boundary") or proof.get("clean_boundary")
        if isinstance(boundary, Mapping):
            boundary_id = str(
                boundary.get("boundary_id") or boundary.get("event_id") or "clean_boundary"
            )
            bindings.append(Binding(
                domain=domain, entity_id=boundary_id,
                identifiers={"boundary_id": boundary_id},
                features={**common, **dict(boundary)},
                evidence_refs=_evidence_refs(boundary),
            ))
    elif domain == "event" and isinstance(record, Mapping):
        for index, row in enumerate(record.get("history") or ()):
            if not isinstance(row, Mapping):
                continue
            event_id = str(row.get("event_id") or f"event_{index}")
            features = {
                **common,
                "event_role": str(row.get("role") or ""),
                "has_tool_call": bool(row.get("tool_calls")),
            }
            bindings.append(Binding(
                domain=domain, entity_id=event_id,
                identifiers={"event_id": event_id},
                features=features,
                evidence_refs=_evidence_refs(row),
            ))
    return tuple(sorted(bindings, key=lambda item: item.key))


def _atom_matches(spec: ConditionSpec, binding: Binding) -> bool:
    observed = binding.features.get(spec.feature)
    if spec.operator == "contains":
        if isinstance(observed, (list, tuple, set, frozenset)):
            return spec.value in observed
        return observed == spec.value
    return observed == spec.value


def _evaluate(
    spec: ConditionSpec, universe: Mapping[tuple[str, str], Binding],
) -> dict[tuple[str, str], Binding]:
    combinator = spec.combinator
    if combinator == "atom":
        return {
            key: binding.with_matched_features((*binding.matched_features, spec.feature))
            for key, binding in universe.items() if _atom_matches(spec, binding)
        }
    children = [ConditionSpec.from_dict(child) for child in spec.children]
    if combinator == "any_of":
        result: dict[tuple[str, str], Binding] = {}
        for child in children:
            for key, binding in _evaluate(child, universe).items():
                prior = result.get(key)
                features = (
                    (*prior.matched_features, *binding.matched_features)
                    if prior else binding.matched_features
                )
                result[key] = binding.with_matched_features(features)
        return result
    if combinator == "not":
        excluded = _evaluate(children[0], universe)
        return {key: value for key, value in universe.items() if key not in excluded}
    if combinator == "all_of":
        current = dict(universe)
        matched_features: dict[tuple[str, str], set[str]] = {
            key: set() for key in universe
        }
        for child in children:
            child_matches = _evaluate(child, universe)
            current = {key: value for key, value in current.items() if key in child_matches}
            for key in tuple(matched_features):
                if key not in current:
                    matched_features.pop(key, None)
                else:
                    matched_features[key].update(child_matches[key].matched_features)
        return {
            key: binding.with_matched_features(matched_features.get(key, ()))
            for key, binding in current.items()
        }
    return {}


def match_condition_bindings(
    condition_spec: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    *,
    domain: str,
    cardinality: str,
    record: Mapping[str, Any] | None = None,
    preferred_entity_id: str = "",
) -> BindingMatch:
    """Evaluate a ConditionSpec as binding algebra on one typed domain.

    ``preferred_entity_id`` is the training-time entity identity: when the
    entity still exists in the projected universe and the predicate is true on
    it, the match binds that entity exactly instead of re-running the
    predicate over every entity (which is what turns an over-broad
    generalization predicate into BINDING_AMBIGUOUS). On a different record
    the stored entity is absent, so the predicate evaluation remains the
    generalization path.
    """
    if domain not in BINDING_DOMAINS or cardinality not in BINDING_CARDINALITIES:
        return BindingMatch(
            BINDING_DOMAIN_MISMATCH, domain, cardinality, (),
            f"invalid_binding_contract:{domain}:{cardinality}",
        )
    sanitized = sanitize_condition_spec(condition_spec)
    if not sanitized:
        return BindingMatch(
            BINDING_NOT_FOUND, domain, cardinality, (), "condition_spec_absent",
        )
    universe_items = project_binding_universe(result, domain=domain, record=record)
    universe = {binding.key: binding for binding in universe_items}
    if preferred_entity_id:
        preferred = universe.get((domain, str(preferred_entity_id)))
        if preferred is not None and _evaluate(
            ConditionSpec.from_dict(sanitized), {preferred.key: preferred},
        ):
            return BindingMatch(
                BINDING_BOUND, domain, cardinality, (preferred,),
                "preferred_entity_grounded",
            )
    matches = tuple(_evaluate(ConditionSpec.from_dict(sanitized), universe).values())
    if not matches:
        return BindingMatch(
            BINDING_NOT_FOUND, domain, cardinality, (), "no_entity_satisfies_condition",
        )
    if cardinality == "exactly_one" and len(matches) != 1:
        return BindingMatch(
            BINDING_AMBIGUOUS, domain, cardinality, matches,
            f"expected_one_binding_found:{len(matches)}",
        )
    return BindingMatch(BINDING_BOUND, domain, cardinality, matches, "entity_grounded")


def predicate_matches_binding(
    condition_spec: Mapping[str, Any] | None, binding: Binding,
) -> bool:
    """Whether a condition spec is true on one entity's projected features.

    This is the consistency check that separates entity identity from the
    generalization predicate: the model names the entity by id, and this
    verifies the reuse predicate it wrote actually fires on that entity.
    """
    sanitized = sanitize_condition_spec(condition_spec)
    if not sanitized:
        return False
    spec = ConditionSpec.from_dict(sanitized)
    return bool(_evaluate(spec, {binding.key: binding}))


def render_binding_menu(
    result: Mapping[str, Any],
    domain: str,
    *,
    record: Mapping[str, Any] | None = None,
    limit: int = 16,
) -> tuple[dict[str, Any], ...]:
    """Project prompt-safe entity rows for one binding domain.

    ``claim_anchor`` rows carry the anchor's bounded text so the model can
    select the entity to edit by id instead of guessing a uniquely-matching
    predicate; every other domain exposes the entity id plus its projected
    identifiers/features. The claim_anchor rows come from
    ``select_relevant_anchors`` (relevance-ordered, bounded), so the first N
    spans of document order can never crowd out the tool-grounded / terminal
    anchors a repair actually targets.
    """
    if domain not in BINDING_DOMAINS:
        return ()
    if domain == "claim_anchor" and isinstance(record, Mapping):
        rows: list[dict[str, Any]] = []
        for anchor in select_relevant_anchors(
            project_claim_anchors(record), limit,
        ):
            rows.append({
                "entity_id": anchor.anchor_id,
                "domain": "claim_anchor",
                "text": anchor.span_text[:200],
                "features": dict(anchor.observable_features),
            })
        return tuple(rows)
    return tuple({
        "entity_id": binding.entity_id,
        "domain": binding.domain,
        "identifiers": dict(binding.identifiers),
        "features": dict(binding.features),
    } for binding in project_binding_universe(
        result, domain=domain, record=record,
    )[:limit])


def validate_binding_contract(
    bindings: Sequence[Binding],
    *,
    domain: str,
    required_fields: Sequence[str],
    cardinality: str,
) -> tuple[str, str]:
    """Validate bindings before any executor mutation occurs."""
    if domain not in BINDING_DOMAINS:
        return BINDING_DOMAIN_MISMATCH, f"unknown_domain:{domain}"
    if cardinality not in BINDING_CARDINALITIES:
        return BINDING_DOMAIN_MISMATCH, f"unknown_cardinality:{cardinality}"
    if not bindings:
        return BINDING_NOT_FOUND, "binding_set_empty"
    wrong = [binding.domain for binding in bindings if binding.domain != domain]
    if wrong:
        return BINDING_DOMAIN_MISMATCH, f"expected:{domain}:observed:{','.join(wrong)}"
    for binding in bindings:
        missing = [field for field in required_fields if not binding.identifiers.get(field)]
        if missing:
            return BINDING_DOMAIN_MISMATCH, "binding_fields_missing:" + ",".join(missing)
    if cardinality == "exactly_one" and len(bindings) != 1:
        return BINDING_AMBIGUOUS, f"expected_one_binding_found:{len(bindings)}"
    return BINDING_BOUND, "binding_contract_satisfied"


def sanitize_binding_proof(value: Any) -> dict[str, Any]:
    """Canonicalize a source binding proof for persistent audit metadata."""
    if not isinstance(value, Mapping) or value.get("status") != BINDING_BOUND:
        return {}
    domain = str(value.get("domain") or "")
    cardinality = str(value.get("cardinality") or "")
    if domain not in BINDING_DOMAINS or cardinality not in BINDING_CARDINALITIES:
        return {}
    bindings = tuple(
        Binding.from_dict(item)
        for item in value.get("bindings") or () if isinstance(item, Mapping)
    )
    if (
        not bindings
        or any(not binding.entity_id or binding.domain != domain for binding in bindings)
        or (cardinality == "exactly_one" and len(bindings) != 1)
    ):
        return {}
    return BindingMatch(
        status=BINDING_BOUND,
        domain=domain,
        cardinality=cardinality,
        bindings=bindings,
        reason="entity_grounded",
    ).to_dict()


__all__ = [
    "BINDING_AMBIGUOUS", "BINDING_BOUND", "BINDING_CARDINALITIES",
    "BINDING_DOMAIN_MISMATCH", "BINDING_DOMAINS", "BINDING_NOT_FOUND",
    "Binding", "BindingMatch", "match_condition_bindings",
    "predicate_matches_binding", "project_binding_universe",
    "render_binding_menu", "sanitize_binding_proof",
    "validate_binding_contract",
]
