"""Promotion bridge: a frozen research candidate becomes a deployable version.

This is the implementation of the first optimization work package
(docs/technical-solution.md): one unified candidate object, one entry that
carries a frozen research candidate through the existing deployment gates, and
one version record written only when the gates actually pass.

Design invariants, all enforced here rather than in prose:

- **A research PASS is never a deployment PASS.** The research lane's own
  screening verdict is recorded as ``development`` evidence only. Admission
  requires STRUCTURAL / HARMLESSNESS / BEHAVIOR / M (plus the localization
  objective when the candidate claims it) to run on the promotion batch.
- **The promotion batch must be candidate-unused and frozen before the run.**
  Its case list, gold, gold steps, role assignment and concurrency are read
  from a protocol file that is hashed into the run identity; the batch is
  rejected if it overlaps the cases the candidate was developed on, and the
  protocol may not be rewritten once a promotion run has started.
- **Unmeasurable is not falsified.** When no promotion batch can score the
  candidate's mechanism, the run ends ``promotion_unmeasurable`` and the
  active registry is left exactly as it was.
- **Every attempt in budget is recorded**, not only the winner: the version
  ledger keeps the rejected candidates, their gate verdicts and the reasons.

The bridge deliberately reuses ``evolution.gates`` unchanged, so a promoted
candidate is admitted by exactly the code that admits a cycle candidate.
"""

from __future__ import annotations

from detector.replay import requires_fresh_evaluation

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.interventions import (
    validate_intervention,
    validate_intervention_batch,
)

from evolution import promotion_alignment as alignment
from evolution.candidate_versions import canonical_hash
from detector.identity import artifact_digest, digest, source_identity
from evolution.backends import make_backend
from evolution.config import resolve_path
from evolution.contracts import (
    candidate_contains_case_id,
    make_registry,
    save_json,
)
from evolution.gates import (
    arm_has_infrastructure_failure,
    behavior_gate,
    candidate_target_kind,
    harmlessness_gate,
    localization_behavior_gate,
    meets_localization,
    meets_m,
    meets_resolution,
    resolution_behavior_gate,
    sentinel_coverage,
    structural_gate,
)
from evolution.ledger import Ledger, load_ledger
from evolution.metrics import (
    gain_opportunity,
    localization_metrics,
    localization_opportunity,
    paired_delta,
    paired_localization_delta,
    protection_repair,
)
from evolution.registry import load_active_registry
from evolution.splits import bool_gold, load_gold, load_gold_step

PROMOTION_PROTOCOL_SCHEMA = "evolution.promotion_protocol.v3"
ARTIFACT_MANIFEST_SCHEMA = "evolution.artifact_manifest.v2"
PROMOTION_STATE_SCHEMA = "evolution.promotion_state.v1"
CANDIDATE_SCHEMA = "evolution.candidate.v1"

# Lifecycle states of the unified candidate object (plan §4.1). The names are
# the plan's; the transitions are enforced by ``_transition`` below.
CANDIDATE_STATES = (
    "draft", "developing", "frozen", "promotion_pending", "active", "retired",
)

PROMOTION_TERMINALS = (
    "promoted",
    "promotion_rejected",
    "promotion_unmeasurable",
    "promotion_blocked_preflight",
    "promotion_unresolved",
)

# Keys a protocol's own hash must not cover: they are added by loading and
# would otherwise make the hash depend on where the file was read from.
_PROTOCOL_HASH_EXCLUDED = frozenset(
    {"protocol_hash", "loaded_from", "resolved_cases"}
)


def protocol_self_hash(protocol: Mapping[str, Any]) -> str:
    """Bind frozen content using the same exclusions as alignment verification."""
    raw = {
        key: value for key, value in protocol.items()
        if key not in _PROTOCOL_HASH_EXCLUDED
    }
    return alignment.digest(raw)


class PromotionProtocolError(ValueError):
    """The promotion protocol file is unusable; refuse before spending budget."""


def build_candidate(
    *,
    candidate_id: str,
    change_kind: str,
    base_revision: str,
    artifacts: Sequence[Mapping[str, Any]],
    development: Mapping[str, Any] | None = None,
    target_kind: str = "detection",
    parent_id: str | None = None,
    artifact_refs: Sequence[str] = (),
    dependencies: Sequence[str] = (),
    notes: Sequence[Any] = (),
    mechanism: Mapping[str, Any] | None = None,
    development_cases: Sequence[str] = (),
) -> dict[str, Any]:
    """The unified candidate object (plan §4.1).

    ``artifacts`` are the executable entries (one or more interventions). The
    object is deliberately storage-agnostic: a research version, a cycle
    candidate and a hand-made patch all wrap into this shape, which is what
    lets one promotion path serve all of them.
    """
    entries = [dict(entry) for entry in artifacts]
    if not entries:
        raise ValueError("a candidate needs at least one artifact")
    validate_intervention_batch(entries, require_executable_bounds=True)
    payload: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA,
        "candidate_id": str(candidate_id),
        "parent_id": str(parent_id) if parent_id else None,
        "target_kind": str(target_kind),
        "change_kind": str(change_kind),
        "base_revision": str(base_revision),
        "artifacts": entries,
        "artifact_refs": [str(item) for item in artifact_refs],
        "dependencies": [str(item) for item in dependencies],
        "development_results": dict(development or {}),
        "status": "frozen",
        "notes": [item for item in notes],
    }
    if mechanism is not None:
        try:
            descriptor = alignment.mechanism_descriptor(mechanism)
            if target_kind != descriptor["objective"]:
                raise ValueError("candidate target_kind differs from mechanism objective")
            artifact_kinds = {candidate_target_kind(entry) for entry in entries}
            if target_kind != "joint" and artifact_kinds != {target_kind}:
                raise ValueError("candidate artifacts disagree with the declared objective")
            payload.update(
                schema_version=alignment.ALIGNED_CANDIDATE_SCHEMA,
                target_mechanism_id=descriptor["mechanism_id"],
                mechanism=descriptor,
                development_cases=alignment.case_ids(list(development_cases), name="development cases"),
            )
            payload["development_cases"] = alignment.development_cases(payload)
            payload["development_evidence_hash"] = alignment.digest(payload["development_results"])
        except ValueError as exc:
            raise PromotionProtocolError(str(exc)) from exc
    payload["content_hash"] = candidate_content_hash(payload)
    return payload


def candidate_content_hash(candidate: Mapping[str, Any]) -> str:
    """Identity by content, not by run-local version name (plan §4.1).

    Deliberately excludes ``status`` and ``development_results``: the same
    artifacts developed differently are the same deployable version, and a
    lifecycle transition must not change the identity a registry entry is
    attributed to. Hashed with sha256 rather than the builtin ``hash`` so the
    identity is stable across processes and runs.
    """
    payload = {
        key: value for key, value in candidate.items()
        if key not in {"content_hash", "status", "development_results"}
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_candidate_identity(candidate: Mapping[str, Any]) -> None:
    if not isinstance(candidate, Mapping):
        raise PromotionProtocolError("candidate must be an object")
    if candidate.get("content_hash") != candidate_content_hash(candidate):
        raise PromotionProtocolError("candidate content hash mismatch")
    if candidate.get("schema_version") == alignment.ALIGNED_CANDIDATE_SCHEMA:
        if candidate.get("development_evidence_hash") != alignment.digest(candidate.get("development_results") or {}):
            raise PromotionProtocolError("candidate development evidence changed")
        descriptor = alignment.mechanism_descriptor(candidate.get("mechanism") or {})
        if candidate.get("target_mechanism_id") != descriptor["mechanism_id"]:
            raise PromotionProtocolError("candidate mechanism binding mismatch")
        if candidate.get("development_cases") != alignment.development_cases(candidate):
            raise PromotionProtocolError("candidate development exposure is incomplete")


def bind_candidate(
    candidate: Mapping[str, Any], mechanism: Mapping[str, Any],
    *, extra_development_cases: Sequence[str] = (),
) -> dict[str, Any]:
    """Migrate an unbound artifact before freeze, never during evaluation."""
    _validate_candidate_identity(candidate)
    if candidate.get("target_mechanism_id") and candidate["target_mechanism_id"] != mechanism.get("mechanism_id"):
        raise PromotionProtocolError("cannot rebind a candidate to a different mechanism")
    return build_candidate(
        candidate_id=str(candidate["candidate_id"]),
        change_kind=str(candidate["change_kind"]), base_revision=str(candidate["base_revision"]),
        artifacts=candidate["artifacts"], development=candidate.get("development_results"),
        target_kind=str(candidate["target_kind"]), parent_id=candidate.get("parent_id"),
        artifact_refs=candidate.get("artifact_refs") or (), dependencies=candidate.get("dependencies") or (),
        notes=candidate.get("notes") or (), mechanism=mechanism,
        development_cases=sorted(set(alignment.development_cases(candidate)) | set(extra_development_cases)),
    )


def candidate_from_development(
    run_dir: str | Path, *, version_id: str, mechanism: Mapping[str, Any],
) -> dict[str, Any]:
    """Read an explicitly frozen development version and all observed cases."""
    if not version_id.startswith("v") or not version_id[1:].isdigit():
        raise PromotionProtocolError("invalid development version id")
    root = Path(run_dir).resolve()
    selected_path = root / version_id / "candidate.json"
    sidecar_path = root / version_id / "development.json"
    try:
        record = json.loads(selected_path.read_text(encoding="utf-8-sig"))
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8-sig"))
        descriptor = alignment.mechanism_descriptor(mechanism)
    except (OSError, ValueError) as exc:
        raise PromotionProtocolError(str(exc)) from exc
    if sidecar.get("status") != "frozen":
        raise PromotionProtocolError("development version must be frozen, not merely ready")
    if sidecar.get("mechanism_id") != descriptor["mechanism_id"]:
        raise PromotionProtocolError("development version targets another mechanism")
    artifacts = record.get("candidate")
    if not artifacts or record.get("candidate_hash") != canonical_hash(artifacts):
        raise PromotionProtocolError("development artifact hash mismatch")
    for level in ("l0", "l1"):
        if (sidecar.get(level) or {}).get("passed") is not True:
            raise PromotionProtocolError(f"development {level.upper()} did not pass")
        if (record.get("verdict") or {}).get(level) != sidecar.get(level):
            raise PromotionProtocolError("development verdict and sidecar disagree")
    exposed: set[str] = set()
    for path in sorted(root.glob("v*/development.json")):
        item = json.loads(path.read_text(encoding="utf-8-sig"))
        evidence = item.get("development_evidence") or {}
        for field in ("target_cases", "neighbor_cases"):
            exposed.update(alignment.case_ids(evidence.get(field, []), name=field))
        sibling = json.loads((path.parent / "candidate.json").read_text(encoding="utf-8-sig"))
        content = sibling.get("candidate") or []
        for entry in [content] if isinstance(content, Mapping) else content:
            exposed.update(alignment.case_ids((entry.get("provenance") or {}).get(
                "failure_cases", []), name="version provenance"))
    parent_id = sidecar.get("parent_version_id") or None
    evidence = {
        "source": "development", "run_dir": str(root), "version_id": version_id,
        "l0": sidecar["l0"], "l1": sidecar["l1"],
        "development_cases": sorted(exposed),
        "candidate_record_hash": alignment.digest(record),
        "development_sidecar_hash": alignment.digest(sidecar),
    }
    return build_candidate(
        candidate_id=f"development:{root.name}:{version_id}", change_kind=str(record.get("kind") or "draft"),
        base_revision=str((record.get("evaluation") or {}).get("identity", {}).get("code_baseline") or "development-unpinned"),
        artifacts=[artifacts] if isinstance(artifacts, Mapping) else artifacts,
        development=evidence, target_kind=descriptor["objective"], parent_id=parent_id,
        mechanism=descriptor, development_cases=sorted(exposed),
        artifact_refs=[str(selected_path), str(sidecar_path)],
    )


def _transition(candidate: dict[str, Any], state: str) -> dict[str, Any]:
    if state not in CANDIDATE_STATES:
        raise ValueError(f"unknown candidate state {state!r}")
    candidate["status"] = state
    return candidate


# ---------------------------------------------------------------------------
# research -> candidate bridge
def candidate_from_research(run_dir: str | Path) -> dict[str, Any]:
    """Wrap the selected version of a research run into a candidate object.

    Reads the research run's own artifacts (``selection.json`` and the selected
    ``vNNN/candidate.json``) and copies the screening verdict into
    ``development_results``. That verdict is development evidence: it is
    recorded, never treated as an admission.
    """
    run_dir = Path(run_dir)
    selection = json.loads((run_dir / "selection.json").read_text(encoding="utf-8-sig"))
    selected_by_route: dict[str, str] = {}
    for route, info in (selection.get("routes") or {}).items():
        version = info.get("selected")
        if version:
            selected_by_route[str(route)] = str(version)
    if not selected_by_route:
        raise PromotionProtocolError(
            f"research run {str(run_dir)!r} has no selected version to promote"
        )
    # One route wins. A tie is a protocol error rather than a silent choice:
    # which candidate to promote is a decision, not something to guess.
    if len(selected_by_route) > 1:
        raise PromotionProtocolError(
            "research run selected more than one route; the promotion protocol "
            f"must pin the version explicitly (selected: {selected_by_route})"
        )
    route, version = next(iter(selected_by_route.items()))
    record = json.loads(
        (run_dir / version / "candidate.json").read_text(encoding="utf-8-sig")
    )
    artifacts = record.get("candidate")
    if isinstance(artifacts, Mapping):
        artifacts = [artifacts]
    if not artifacts:
        raise PromotionProtocolError(
            f"{run_dir / version} has no candidate artifacts"
        )
    verdict = record.get("verdict") or {}
    target_kinds = {
        str((entry.get("claim") or {}).get("target_kind") or "detection")
        for entry in artifacts
    }
    target_kind = "joint" if "joint" in target_kinds else (
        "localization" if "localization" in target_kinds else "detection"
    )
    development = {
        "source": "research",
        "run_dir": str(run_dir),
        "route": route,
        "version": version,
        "screening_passed": bool(verdict.get("passed")),
        "screening_reasons": list(verdict.get("reasons") or ()),
        "training": verdict.get("training") or {},
        "neighbors": verdict.get("neighbors") or {},
        "candidate_hash": record.get("candidate_hash"),
    }
    manifest = {}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    return build_candidate(
        candidate_id=f"research:{run_dir.name}:{version}",
        change_kind=str(record.get("kind") or "draft"),
        base_revision=str(manifest.get("code_baseline") or "unknown"),
        artifacts=artifacts,
        development=development,
        target_kind=target_kind,
        parent_id=(
            f"research:{run_dir.name}:{record['parent_hash']}"
            if record.get("parent_hash") else None
        ),
        artifact_refs=[
            str(run_dir / version / "candidate.json"),
            *([str(run_dir / "manifest.json")] if manifest_path.is_file() else []),
        ],
        notes=list(record.get("notes") or ()),
    )


def candidate_from_registry_patch(
    patch_path: str | Path,
    *,
    candidate_id: str | None = None,
    target_kind: str | None = None,
    change_kind: str = "local-component",
    base_revision: str = "unknown",
    development: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a hand-written or tool-produced artifact file into a candidate.

    This is the path a non-research improvement takes (plan §4.1: intervention
    JSON, joint interventions and configuration/context patches all become the
    same lifecycle object), which is what keeps the promotion entry from being
    research-specific.
    """
    path = Path(patch_path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, Mapping) and payload.get("interventions"):
        artifacts = list(payload.get("interventions") or ())
    elif isinstance(payload, Mapping) and payload.get("type"):
        artifacts = [dict(payload)]
    elif isinstance(payload, list):
        artifacts = [dict(item) for item in payload]
    else:
        raise PromotionProtocolError(
            f"{str(path)!r} does not contain an intervention, a list of them, "
            "or an intervention registry"
        )
    kinds = {
        str((entry.get("claim") or {}).get("target_kind") or "detection")
        for entry in artifacts
    }
    resolved_kind = target_kind or (
        "joint" if "joint" in kinds else (
            "localization" if "localization" in kinds else "detection"
        )
    )
    return build_candidate(
        candidate_id=candidate_id or f"patch:{path.stem}",
        change_kind=change_kind,
        base_revision=base_revision,
        artifacts=artifacts,
        development=development,
        target_kind=resolved_kind,
        artifact_refs=[str(path)],
    )


# ---------------------------------------------------------------------------
# the promotion protocol: a frozen, candidate-unused batch
def freeze_protocol(
    path: str | Path,
    *,
    batch_path: str | Path,
    roles: Mapping[str, str],
    gold: Mapping[str, Any],
    gold_steps: Mapping[str, Any] | None = None,
    development_cases: Sequence[str] = (),
    detector_config: str | Path | None = None,
    workers: int = 8,
    timeout_seconds: float | None = None,
    min_protected_tp: int = 2,
    min_protected_tn: int = 2,
    reason: str = "",
    expected_authority: Mapping[str, Any] | None = None,
    baseline_roll: Mapping[str, Any] | None = None,
    require_fresh: bool = False,
    mechanism: Mapping[str, Any] | None = None,
    mechanism_membership: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
    dataset_root: str | Path | None = None,
    framework_config: Mapping[str, Any] | None = None,
    baseline_registry_hash: str | None = None,
    artifact_paths: Sequence[str | Path] = (),
    ledger: "Ledger | None" = None,
) -> dict[str, Any]:
    """Freeze a promotion batch once, before any model call.

    ``roles`` maps ``case_id -> role`` and accepts ``benefit`` (the frozen
    evaluation set that decides the objective) and ``protection`` (the paired
    set the HARMLESSNESS gate protects). A localization claim is scored on
    annotated gold-positive benefit cases, so ``gold_steps`` must cover them.

    ``baseline_roll`` pins which control-arm roll the run replays (see
    ``PromotionRun`` for why an unpinned roll can hide a localization
    mechanism). It is a mapping with ``path`` (a JSON file holding
    ``case_id -> result``) and ``declared_path`` (the ``positive_source`` the
    protocol intends to measure). Both are hashed into the protocol, so a roll
    cannot be swapped after the batch was frozen. Absent means the historical
    behaviour: whatever the backend produces, with frozen stages where the
    registry does not target them.

    ``require_fresh`` forces every stage to re-run instead. It is the honest
    choice when the mechanism under test touches a stage that would otherwise
    be replayed, at the cost of provider noise between the arms.

    The protection set is checked against the HARMLESSNESS coverage floor at
    freeze time. That floor is a *necessary* condition: whether a protected
    case is baseline-correct still depends on the control arm's behaviour. But
    a protection set with too few positives (or negatives) can never satisfy it,
    and freezing such a protocol would spend a whole batch to reach a
    predetermined verdict. The cycle protocol calls this same floor
    necessary-not-sufficient; here it is additionally enforced early because the
    batch is frozen and expensive.
    """
    aligned = any(value is not None for value in (mechanism, mechanism_membership, candidate))
    if aligned and any(value is None for value in (
        mechanism, mechanism_membership, candidate, dataset_root, baseline_registry_hash,
    )):
        raise PromotionProtocolError(
            "mechanism-aligned freeze needs candidate, mechanism, membership, dataset_root and baseline_registry_hash"
        )
    if aligned:
        _validate_candidate_identity(candidate)
    if baseline_roll is not None and require_fresh:
        raise PromotionProtocolError(
            "baseline_roll and require_fresh are mutually exclusive: a pinned "
            "roll is a replay, and a fresh run has no roll to pin"
        )
    path = Path(path)
    batch_path = Path(batch_path)
    cases = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    if isinstance(cases, Mapping):
        cases = list(cases.get("cases") or ())
    case_ids = [str(item) for item in cases]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise PromotionProtocolError(
            "promotion batch must be a non-empty list of unique case ids"
        )
    allowed_roles = {"benefit", "protection", "challenge"} if aligned else {"benefit", "protection"}
    unknown_roles = sorted(set(roles.values()) - allowed_roles)
    if unknown_roles:
        raise PromotionProtocolError(
            f"unknown role(s) {unknown_roles}; use 'benefit' or 'protection'"
        )
    if set(map(str, roles)) != set(case_ids):
        raise PromotionProtocolError(
            "every promotion case needs exactly one declared role"
        )
    if not any(role == "benefit" for role in roles.values()):
        raise PromotionProtocolError("promotion batch needs at least one benefit case")
    gold = {str(case): gold.get(str(case)) for case in case_ids}
    missing_gold = sorted(case for case, value in gold.items() if value is None)
    if missing_gold:
        raise PromotionProtocolError(
            "promotion batch contains case(s) without a boolean gold label: "
            f"{missing_gold[:5]}"
        )
    protection_cases = [
        case for case, role in roles.items() if str(role) == "protection"
    ]
    if protection_cases:
        # Only gold-true protected cases can ever be a protected TP, so the
        # polarity mix of the protection set is what the floor can be met with.
        protected_positives = sum(
            1 for case in protection_cases if gold[case] is True
        )
        protected_negatives = sum(
            1 for case in protection_cases if gold[case] is False
        )
        if protected_positives < min_protected_tp:
            raise PromotionProtocolError(
                "protection set can never satisfy the HARMLESSNESS coverage "
                f"floor: it holds {protected_positives} gold-true case(s) but "
                f"needs at least {min_protected_tp}. HARMLESSNESS protects "
                "baseline-correct cases of both polarities, so a protection set "
                "of a single polarity makes every promotion run on this batch "
                "unevaluable by construction."
            )
        if protected_negatives < min_protected_tn:
            raise PromotionProtocolError(
                "protection set can never satisfy the HARMLESSNESS coverage "
                f"floor: it holds {protected_negatives} gold-false case(s) but "
                f"needs at least {min_protected_tn}"
            )
    overlap = sorted(set(map(str, development_cases)) & set(case_ids))
    if overlap:
        raise PromotionProtocolError(
            "promotion batch must be candidate-unused; it overlaps the cases "
            f"the candidate was developed on: {overlap[:5]}"
        )
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
        raise PromotionProtocolError(
            f"promotion protocol {str(path)!r} is already frozen; a frozen "
            "protocol may not be rewritten (recorded at "
            f"{existing.get('frozen_at')!r})"
        )
    detector_config = detector_config or "detector/configs/autodl.json"
    resolved_detector = resolve_path(detector_config)
    config_payload = json.loads(
        resolved_detector.read_text(encoding="utf-8-sig")
    )
    gold_steps = dict(gold_steps or {})
    labeled: dict[str, Any] = {}
    for case in case_ids:
        labeled[case] = {
            "role": str(roles[case]),
            "gold": gold[case],
            "gold_step": gold_steps.get(case),
        }
    protocol = {
        "schema_version": PROMOTION_PROTOCOL_SCHEMA,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": str(reason),
        "batch_path": str(batch_path),
        "batch_hash": digest(json.loads(
            batch_path.read_text(encoding="utf-8-sig")
        )),
        "cases": case_ids,
        "roles": {str(case): str(role) for case, role in roles.items()},
        "gold": {str(case): gold[case] for case in case_ids},
        "gold_steps": {
            str(case): step for case, step in gold_steps.items() if step is not None
        },
        "classes": labeled,
        "development_cases": sorted(str(case) for case in development_cases),
        "detector_config": str(resolved_detector),
        "detector_config_hash": digest(config_payload),
        "code_baseline": source_identity("detector", "evolution"),
        # Case membership alone does not pin the trajectory content.
        "case_digests": (
            alignment.data_digests(dataset_root, case_ids)
            if dataset_root is not None else None
        ),
        # External model artifacts can change independently of source/config.
        "artifact_manifest": _artifact_manifest(*artifact_paths),
        "workers": int(workers),
        "timeout_seconds": (
            float(timeout_seconds) if timeout_seconds else None
        ),
        "coverage_floor": {
            "min_protected_tp": int(min_protected_tp),
            "min_protected_tn": int(min_protected_tn),
        },
        "expected_authority": dict(expected_authority or {}),
        "baseline_roll": _freeze_baseline_roll(
            baseline_roll, case_ids=case_ids,
        ),
        "require_fresh": bool(require_fresh),
    }
    if aligned:
        try:
            protocol = alignment.build_alignment(
                protocol, candidate=candidate, mechanism=mechanism,
                membership=mechanism_membership, dataset_root=dataset_root,
                config=framework_config or {}, baseline_registry_hash=baseline_registry_hash,
            )
        except (ValueError, OSError) as exc:
            raise PromotionProtocolError(str(exc)) from exc
    else:
        # Protection coverage and repair directions depend on the active deployment.
        if baseline_registry_hash is None:
            from evolution.config import DEFAULTS, load_config

            registry_config = dict(framework_config or {})
            registry_config.setdefault("registry_dir", DEFAULTS["registry_dir"])
            if not registry_config.get("registry_dir"):
                registry_config["registry_dir"] = load_config()["registry_dir"]
            registry = load_active_registry(registry_config)
            baseline_registry_hash = str(registry.registry_hash or "")
        protocol["baseline_registry_hash"] = str(baseline_registry_hash)
        protocol["protocol_hash"] = protocol_self_hash(protocol)
    _refuse_refrozen_batch(protocol, ledger=ledger)
    save_json(path, protocol)
    if ledger is not None:
        ledger.append("protocol_frozen", {
            "protocol_hash": protocol.get("protocol_hash"),
            "batch_hash": protocol.get("batch_hash"),
            "candidate_content_hash": protocol.get("candidate_content_hash"),
            "mechanism_id": protocol.get("mechanism_id"),
            "cases": len(protocol.get("cases") or ()),
            "path": str(path),
        })
    return protocol


def _refuse_refrozen_batch(
    protocol: Mapping[str, Any],
    *,
    ledger: "Ledger | None" = None,
) -> None:
    """Refuse to freeze the same batch and candidate a second time.

    §7.5: a failed L2 may not be retried by calling another candidate on the
    same frozen protocol, and a candidate may not be re-scored by re-freezing
    the batch it already ran on. The per-path claim file guards one path, which
    a second freeze at a new path (a new ``frozen_at``, hence a new
    ``protocol_hash``) defeats. The ledger is the cross-path record, so the
    guard is anchored there whenever a ledger is supplied.
    """
    if ledger is None:
        return
    identity = {
        "batch_hash": protocol.get("batch_hash"),
        "candidate_content_hash": protocol.get("candidate_content_hash"),
        "mechanism_id": protocol.get("mechanism_id"),
    }
    if not identity["batch_hash"]:
        return
    for record in ledger.read():
        if str(record.get("event")) != "protocol_frozen":
            continue
        payload = record.get("payload") or {}
        if all(payload.get(key) == value for key, value in identity.items()):
            raise PromotionProtocolError(
                "this batch and candidate were already frozen as a protocol "
                f"(recorded {record.get('timestamp')!r}); a rejected promotion "
                "may not be re-scored on the same frozen evidence. Change the "
                "candidate or freeze a different batch."
            )


def _verify_frozen_run_parameters(protocol: Mapping[str, Any]) -> None:
    """Refuse a protocol whose declared run parameters cannot be honoured.

    ``workers`` and ``timeout_seconds`` are the only two frozen fields that
    describe *how* the arms run rather than what they measure, and the run
    resolves both with ``or`` defaults: ``workers: 0`` becomes the default
    concurrency and ``timeout_seconds: 0`` becomes no wall clock at all. An
    operator typing 0 to mean "no limit" would therefore freeze a protocol
    that says one thing and runs another, and the record could not reproduce
    the conditions actually used.
    """
    workers = protocol.get("workers")
    if workers is None:
        raise PromotionProtocolError(
            "frozen protocol declares no worker count; a promotion run needs "
            "the concurrency it was frozen with"
        )
    try:
        workers_int = int(workers)
    except (TypeError, ValueError) as exc:
        raise PromotionProtocolError(
            f"frozen protocol worker count is not an integer: {workers!r}"
        ) from exc
    if workers_int < 1:
        raise PromotionProtocolError(
            f"frozen protocol worker count must be at least 1, got {workers!r}"
        )
    raw_timeout = protocol.get("timeout_seconds")
    if raw_timeout is not None:
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise PromotionProtocolError(
                f"frozen protocol timeout is not a number: {raw_timeout!r}"
            ) from exc
        if not (timeout > 0):
            raise PromotionProtocolError(
                "frozen protocol timeout must be positive or explicitly null "
                f"(no batch-level wall clock), got {raw_timeout!r}; a zero "
                "timeout silently means 'unbounded', not 'zero'"
            )


def _verify_frozen_policy(
    protocol: Mapping[str, Any], config: Mapping[str, Any] | None,
) -> None:
    """Refuse a run whose thresholds differ from the frozen floor.

    A frozen ``coverage_floor`` that the run never consults is decoration: the
    same protocol can be re-scored after the operator relaxes
    ``gates.harmlessness_min_protected_*``. The floor is a *necessary* condition
    (whether a protected case is baseline-correct still depends on the control
    arm), and freeze time already enforces it there; re-checking it here is what
    keeps the promise true at run time.
    """
    if config is None:
        return
    floor = protocol.get("coverage_floor")
    if not isinstance(floor, Mapping):
        return
    gates_cfg = config.get("gates") or {}
    expected = {
        "min_protected_tp": int(gates_cfg.get("harmlessness_min_protected_tp", 2)),
        "min_protected_tn": int(gates_cfg.get("harmlessness_min_protected_tn", 2)),
    }
    drifted = {
        key: (int(floor.get(key, value)), value)
        for key, value in expected.items()
        if int(floor.get(key, value)) != value
    }
    if drifted:
        raise PromotionProtocolError(
            "gate thresholds changed after the protocol was frozen; the "
            f"frozen coverage floor no longer matches the run ({drifted}). "
            "Re-freeze against the current thresholds instead of re-scoring "
            "the batch under a different standard."
        )


def _artifact_manifest(*paths: str | Path) -> dict[str, Any]:
    payload = {"schema_version": ARTIFACT_MANIFEST_SCHEMA,
               "artifacts": {str(path): artifact_digest(path) for path in paths}}
    return {**payload, "digest": digest(payload)}


def _verify_artifact_manifest(protocol: Mapping[str, Any]) -> None:
    """Validate the manifest before re-reading its declared artifact contents."""
    manifest = protocol.get("artifact_manifest")
    if not isinstance(manifest, Mapping):
        raise PromotionProtocolError(
            "frozen protocol carries no artifact manifest; refusing to "
            "evaluate a protocol whose learned artifacts are unpinned"
        )
    if (set(manifest) != {"schema_version", "artifacts", "digest"}
            or manifest["schema_version"] != ARTIFACT_MANIFEST_SCHEMA):
        raise PromotionProtocolError("unsupported artifact manifest schema or fields")
    declared = manifest["artifacts"]
    if not isinstance(declared, Mapping) or any(
        not isinstance(path, str) or not path
        or not isinstance(expected, str) or len(expected) != 64
        or any(char not in "0123456789abcdef" for char in expected)
        for path, expected in declared.items()
    ):
        raise PromotionProtocolError("invalid artifact manifest entries")
    payload = {"schema_version": ARTIFACT_MANIFEST_SCHEMA, "artifacts": dict(declared)}
    if manifest["digest"] != digest(payload):
        raise PromotionProtocolError("artifact manifest digest mismatch")
    for path, expected in sorted(declared.items()):
        try:
            actual = artifact_digest(path)
        except ValueError as exc:
            raise PromotionProtocolError(
                f"learned artifact is missing after the protocol was frozen: "
                f"{exc}"
            ) from exc
        if actual != expected:
            raise PromotionProtocolError(
                "learned artifact changed after the protocol was frozen: "
                f"{path}. The frozen protocol is evaluated on the artifacts "
                "it froze; re-freeze before measuring this version."
            )


def _freeze_baseline_roll(
    baseline_roll: Mapping[str, Any] | None,
    *,
    case_ids: Sequence[str],
) -> dict[str, Any] | None:
    """Validate and hash a pinned control roll at freeze time.

    The roll must cover the batch (a partial roll would silently measure the
    missing cases under different conditions) and must actually reproduce the
    path the protocol declares, otherwise the run would report a reading for a
    route it never exercised.
    """
    if baseline_roll is None:
        return None
    source = baseline_roll.get("path")
    if not source:
        raise PromotionProtocolError("baseline_roll requires a 'path'")
    roll_path = resolve_path(str(source))
    if not roll_path.is_file():
        raise PromotionProtocolError(f"baseline roll not found: {roll_path}")
    payload = json.loads(roll_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise PromotionProtocolError(
            "baseline roll must be a JSON object of case_id -> result"
        )
    roll = {
        str(key).replace("__", "/"): value for key, value in payload.items()
    }
    missing = [case for case in case_ids if case not in roll]
    if missing:
        raise PromotionProtocolError(
            f"baseline roll does not cover the batch: {missing[:5]}"
        )
    declared = baseline_roll.get("declared_path")
    if declared is not None:
        observed = sorted({
            _roll_positive_source(roll[case])
            for case in case_ids
            if _roll_positive_source(roll[case])
        })
        declared = str(declared)
        if observed != [declared]:
            raise PromotionProtocolError(
                "baseline roll does not reproduce the declared path: "
                f"declared {declared!r}, the roll yields {observed}. Freeze a "
                "roll from the run that actually shows this route."
            )
    return {
        "path": roll_path.as_posix(),
        "roll_hash": digest(dict(payload)),
        "declared_path": (
            str(baseline_roll.get("declared_path"))
            if baseline_roll.get("declared_path") is not None else None
        ),
        "source_run": (
            str(baseline_roll.get("source_run"))
            if baseline_roll.get("source_run") is not None else None
        ),
    }


def _roll_positive_source(result: Any) -> str:
    """The decisive positive source recorded in one archived result."""
    if not isinstance(result, Mapping):
        return ""
    proof = result.get("proof") or {}
    facts = (proof.get("terminal_basis") or {}).get("decisive_facts") or {}
    return str(facts.get("positive_source") or "")


def load_protocol(
    path: str | Path,
    *,
    dataset_root: str | Path,
    stages: Sequence[str],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load a frozen protocol and check it against the current machinery.

    The checks are the ``freeze``-time promises, re-derived: the protocol still
    hashes to the hash it was frozen with, the batch file still has the hash it
    was frozen with, the trajectory content is unchanged, the detector
    configuration is unchanged, and the roles still cover exactly the batch. A
    mismatch refuses the run instead of quietly measuring something else than
    what was frozen.

    ``config`` is optional and only used to cross-check the frozen coverage
    floor and run parameters against the thresholds the run will actually use.
    Absent, those checks are skipped rather than guessed.
    """
    path = Path(path)
    if not path.is_file():
        raise PromotionProtocolError(
            f"promotion protocol not found: {str(path)!r}; a promotion run "
            "needs a protocol frozen before any model call"
        )
    protocol = json.loads(path.read_text(encoding="utf-8-sig"))
    schema = str(protocol.get("schema_version"))
    if schema not in {PROMOTION_PROTOCOL_SCHEMA, alignment.ALIGNED_PROTOCOL_SCHEMA}:
        raise PromotionProtocolError("unsupported promotion protocol schema")
    frozen_hash = str(protocol.get("protocol_hash") or "")
    if not frozen_hash:
        raise PromotionProtocolError(
            "frozen protocol carries no protocol_hash; it cannot be shown to "
            "be the file that was frozen (re-freeze it with this build)"
        )
    if protocol_self_hash(protocol) != frozen_hash:
        raise PromotionProtocolError(
            "promotion protocol changed after it was frozen; the file no "
            "longer hashes to the value recorded at freeze time"
        )
    if schema == alignment.ALIGNED_PROTOCOL_SCHEMA:
        try:
            alignment.verify_alignment(protocol)
            if protocol["case_digests"] != alignment.data_digests(dataset_root, protocol["cases"]):
                raise ValueError("promotion trajectory content changed after freeze")
        except (ValueError, KeyError, OSError) as exc:
            raise PromotionProtocolError(str(exc)) from exc
    else:
        # Synthetic freezes have no trajectory identity and cannot be measured.
        case_digests = protocol.get("case_digests")
        if isinstance(case_digests, Mapping) and case_digests:
            current = alignment.data_digests(dataset_root, list(case_digests))
            changed = sorted(
                case for case, digest in case_digests.items()
                if current.get(case) != digest
            )
            if changed:
                raise PromotionProtocolError(
                    "promotion trajectory content changed after freeze: "
                    f"{changed[:5]}"
                )
        protocol["trajectory_pinned"] = bool(case_digests)
    batch_path = Path(str(protocol.get("batch_path") or ""))
    if not batch_path.is_file():
        raise PromotionProtocolError(f"promotion batch is missing: {batch_path}")
    batch_payload = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    if digest(batch_payload) != str(protocol.get("batch_hash") or ""):
        raise PromotionProtocolError(
            "promotion batch changed after it was frozen; refusing to measure "
            "a different case set than the frozen protocol describes"
        )
    if str(protocol.get("code_baseline") or "") != source_identity("detector", "evolution"):
        raise PromotionProtocolError(
            "detector/evolution source changed after the promotion protocol was "
            "frozen; a frozen protocol is evaluated on the machinery it froze"
        )
    _verify_artifact_manifest(protocol)
    _verify_frozen_run_parameters(protocol)
    _verify_frozen_policy(protocol, config)
    detector_config = Path(str(protocol.get("detector_config") or ""))
    if not detector_config.is_file():
        raise PromotionProtocolError(
            f"detector config is missing: {detector_config}"
        )
    config_payload = json.loads(detector_config.read_text(encoding="utf-8-sig"))
    if digest(config_payload) != str(
        protocol.get("detector_config_hash") or ""
    ):
        raise PromotionProtocolError(
            "detector configuration changed after the promotion protocol was "
            "frozen"
        )
    batch_ids = batch_payload
    if isinstance(batch_ids, Mapping):
        batch_ids = list(batch_ids.get("cases") or ())
    batch_ids = [str(item) for item in batch_ids]
    roles = {str(k): str(v) for k, v in (protocol.get("roles") or {}).items()}
    if set(batch_ids) != set(roles):
        raise PromotionProtocolError("promotion roles no longer cover the batch")
    protocol["loaded_from"] = str(path)
    protocol["resolved_cases"] = batch_ids
    # Re-derive gold and gold steps from the dataset every load: the frozen
    # protocol records the annotation it was frozen with, and a dataset whose
    # labels moved must not silently promote against different answers.
    derived_gold = {
        case: bool_gold(load_gold(dataset_root, case)) for case in batch_ids
    }
    derived_steps = {
        case: load_gold_step(dataset_root, case) for case in batch_ids
    }
    frozen_gold = {
        str(case): value
        for case, value in (protocol.get("gold") or {}).items()
    }
    gold_mismatch = {
        case: (frozen_gold.get(case), derived_gold.get(case))
        for case in batch_ids
        if case in frozen_gold and frozen_gold[case] != derived_gold[case]
    }
    if gold_mismatch:
        raise PromotionProtocolError(
            "frozen gold labels disagree with the dataset annotations: "
            f"{gold_mismatch}"
        )
    frozen_steps = {
        str(case): step
        for case, step in (protocol.get("gold_steps") or {}).items()
    }
    mismatch = {
        case: (frozen_steps.get(case), derived_steps.get(case))
        for case in batch_ids
        if frozen_steps.get(case) != derived_steps.get(case)
    }
    if mismatch:
        raise PromotionProtocolError(
            "declared gold steps disagree with the dataset annotations: "
            f"{mismatch}"
        )
    if schema != alignment.ALIGNED_PROTOCOL_SCHEMA:
        protocol["gold"] = derived_gold
        protocol["gold_steps"] = derived_steps
    unknown = sorted(set(stages) - {"structural", "harmlessness", "behavior", "m"})
    if unknown:
        raise PromotionProtocolError(f"unknown promotion stage(s): {unknown}")
    return protocol


def benefit_cases(protocol: Mapping[str, Any]) -> list[str]:
    roles = protocol.get("roles") or {}
    return sorted(
        str(case) for case, role in roles.items() if str(role) == "benefit"
    )


def protection_cases(protocol: Mapping[str, Any]) -> list[str]:
    roles = protocol.get("roles") or {}
    return sorted(
        str(case) for case, role in roles.items() if str(role) == "protection"
    )


# ---------------------------------------------------------------------------
# the promotion run
class PromotionRun:
    """One frozen-protocol promotion attempt for one candidate.

    Everything the run measures is derived from the protocol, and nothing in
    this class writes the active registry until every gate has passed.
    """

    def __init__(
        self,
        *,
        candidate: Mapping[str, Any],
        protocol: Mapping[str, Any],
        config: Mapping[str, Any],
        output_dir: str | Path,
        backend: Any | None = None,
        active_registry: Any | None = None,
        ledger: Any | None = None,
        stages: Sequence[str] = ("structural", "harmlessness", "behavior", "m"),
    ) -> None:
        self.candidate = dict(candidate)
        self.protocol = dict(protocol)
        self.config = dict(config)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.dataset_root = config["dataset_root"]
        self.stages = [str(stage) for stage in stages]
        self.registry = active_registry or load_active_registry(self.config)
        self.ledger = ledger or load_ledger(self.config)
        # A pinned roll needs a backend that knows about it, so the pin is
        # resolved before the backend is chosen. An explicitly injected
        # backend still wins: the offline harness must never be replaced.
        self.baseline_roll = self._load_baseline_roll()
        if backend is not None:
            self.backend = backend
        elif self.baseline_roll:
            self.backend = self._pinned_backend(())
        else:
            self.backend = make_backend(self.config)
        self.workers = max(1, int(protocol.get("workers") or 8))
        # Batch-level wall-clock budget. The plan requires each task to be
        # bounded in calls, tokens, wall clock and paths; without this a single
        # pathological case can hold the whole run for its own case timeout.
        # Expiry fail-closes the remaining cases to unresolved rows, which the
        # run then refuses to score rather than reading as a candidate gain.
        raw_timeout = protocol.get("timeout_seconds")
        self.timeout_seconds = (
            float(raw_timeout) if raw_timeout else None
        )
        self.deadline: float | None = None
        self._provided_rows: dict[str, list[dict[str, Any]]] = {}
        self.resumed_arms: list[str] = []
        # The gate verdicts of the most recent evaluation, kept so the
        # admission reason can name the gates that actually ran instead of a
        # constant. Set at the end of each run path.
        self._last_gates: dict[str, Any] = {}
        self.arm_health: dict[str, str] = {}
        # ``require_fresh`` re-runs every stage on both arms. It is the honest
        # choice when the mechanism touches a stage that would otherwise be
        # replayed from the control roll, at the cost of provider noise
        # between the arms.
        self.require_fresh = bool(protocol.get("require_fresh"))

    def _load_baseline_roll(self) -> dict[str, Any]:
        """The pinned control results, keyed by case id, or an empty mapping."""
        spec = self.protocol.get("baseline_roll")
        if not spec:
            return {}
        path = resolve_path(str(spec.get("path")))
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if digest(dict(payload)) != spec.get("roll_hash"):
            raise PromotionProtocolError(
                "pinned baseline roll changed after the protocol was frozen: "
                f"{path}"
            )
        return {
            str(key).replace("__", "/"): value
            for key, value in dict(payload).items()
        }

    # -- infrastructure ---------------------------------------------------
    def _pinned_backend(self, entries: Sequence[Mapping[str, Any]]) -> Any:
        """A backend whose control arm is the pinned roll.

        The roll is the *control* arm only. The candidate arm still runs live,
        so the comparison is pinned-control versus measured-candidate rather
        than two independent rolls of the same baseline. That is what makes
        the paired reading attributable to the intervention.
        """
        from evolution.backends import LiveDetectorBackend

        active = self.registry.registry if hasattr(self, "registry") else None
        backend = LiveDetectorBackend(
            self.config,
            baseline_results=self.baseline_roll,
        )
        return backend

    def _registry_for(self, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return make_registry(entries)

    def _arm_identity(
        self,
        entries: Sequence[Mapping[str, Any]],
        cases: Sequence[str],
        *,
        fresh: bool,
    ) -> dict[str, Any]:
        """What an arm's stored rows are only valid for.

        Resume must never re-draw a completed arm, and must never reuse rows
        measured under a different registry, case set, evidence condition or
        frozen protocol. The identity records exactly those inputs.
        """
        return {
            "candidate_content_hash": self.candidate.get("content_hash"),
            "registry_hash": str(
                self._registry_for(entries).get("content_hash") or ""
            ),
            "cases": list(cases),
            "fresh": bool(fresh),
            "gold": {
                case: (self.protocol.get("gold") or {}).get(case)
                for case in cases
            },
            "gold_steps": {
                case: (self.protocol.get("gold_steps") or {}).get(case)
                for case in cases
            },
            "workers": self.workers,
            "frozen_at": self.protocol.get("frozen_at"),
            # A stored arm is only reusable under the same pinned control roll;
            # otherwise a resume would pair rows measured against a different
            # baseline than the one the protocol froze.
            "protocol_hash": self.protocol.get("protocol_hash"),
            "mechanism_id": self.protocol.get("mechanism_id"),
            "baseline_roll_hash": (
                (self.protocol.get("baseline_roll") or {}).get("roll_hash")
            ),
        }

    def _run_arm(
        self,
        entries: Sequence[Mapping[str, Any]],
        cases: Sequence[str],
        *,
        fresh: bool,
        label: str,
    ) -> list[dict[str, Any]]:
        """Evaluate one arm with the protocol's declared concurrency.

        The case batch inside the backend already parallelises, so the two
        arms are run concurrently rather than the cases within an arm twice
        over. A completed arm is reused from disk under an identical identity:
        an interrupted run resumes without re-spending provider budget on work
        that was already measured, and a changed input forces a fresh arm
        instead of silently pairing two different measurements.
        """
        provided = self._provided_rows.get(label)
        if provided is not None:
            return provided
        path = self.output_dir / "arms" / f"{label}.json"
        identity = self._arm_identity(entries, cases, fresh=fresh)
        if path.is_file():
            try:
                stored = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                stored = None
            if isinstance(stored, Mapping) and stored.get("identity") == identity:
                self.resumed_arms.append(label)
                return [dict(row) for row in stored.get("rows") or ()]
        kwargs = dict(
            dataset_root=self.dataset_root,
            registry=self._registry_for(entries),
            gold_map={
                case: self.protocol["gold"][case]
                for case in cases
                if case in (self.protocol.get("gold") or {})
            },
            gold_step_map={
                case: (self.protocol.get("gold_steps") or {}).get(case)
                for case in cases
            },
            workers=self.workers,
            deadline=self.deadline,
        )
        evaluate = self.backend.evaluate_fresh if fresh else self.backend.evaluate
        rows = evaluate(list(cases), **kwargs)
        # An arm with an infrastructure failure is NOT a completed measurement
        # and is deliberately not persisted: storing it would let a later run
        # resume an arm that never produced a comparable pair, and an outage
        # would become a permanent property of the frozen protocol instead of a
        # retryable accident.
        unhealthy = arm_has_infrastructure_failure(rows)
        self.arm_health[label] = unhealthy
        if not unhealthy:
            save_json(path, {"label": label, "identity": identity, "rows": rows})
        return rows

    def install_rows(self, label: str, rows: Sequence[Mapping[str, Any]]) -> None:
        """Inject precomputed rows (offline harness and resume reuse)."""
        self._provided_rows[label] = [dict(row) for row in rows]

    # -- stages -----------------------------------------------------------
    def _structural(self) -> dict[str, Any]:
        if "structural" not in self.stages:
            return {"passed": True, "reason": "", "skipped": True}
        candidate = self.candidate
        development = candidate.get("development_results") or {}
        development_cases = sorted(
            {
                str(case)
                for case in (
                    (candidate.get("provenance") or {}).get("failure_cases") or []
                )
            }
        )
        for artifact in candidate.get("artifacts") or ():
            development_cases.extend(
                str(case)
                for case in (
                    (artifact.get("provenance") or {}).get("failure_cases") or []
                )
            )
        protocol_cases = set(self.protocol.get("resolved_cases") or ())
        leaked = [
            case for case in sorted(set(development_cases))
            if case in protocol_cases
        ]
        if leaked:
            return {
                "passed": False,
                "reason": (
                    "promotion batch is not candidate-unused: development case(s) "
                    f"appear in the batch: {leaked[:5]}"
                ),
                "evaluable": True,
            }
        reasons: list[str] = []
        for artifact in candidate.get("artifacts") or ():
            hit = candidate_contains_case_id(artifact)
            if hit:
                reasons.append(f"case id leak in artifact: {hit}")
            try:
                validate_intervention(artifact, require_executable_bounds=True)
            except ValueError as exc:
                reasons.append(f"schema/executor rejection: {exc}")
            artifact_dev = (
                (artifact.get("provenance") or {}).get("failure_cases") or []
            )
            overlap = sorted(set(map(str, artifact_dev)) & protocol_cases)
            if overlap:
                reasons.append(
                    f"artifact provenance cases are in the promotion batch: {overlap[:5]}"
                )
        if development.get("screening_passed") is False:
            reasons.append(
                "research screening verdict did not pass; a development failure "
                "cannot be promoted without a re-run"
            )
        if reasons:
            return {"passed": False, "reason": "; ".join(reasons), "evaluable": True}
        return {"passed": True, "reason": ""}

    def _detection_and_localization(
        self,
        *,
        control_rows: Sequence[Mapping[str, Any]],
        candidate_rows: Sequence[Mapping[str, Any]],
        benefit: Sequence[str],
        protection: Sequence[str],
    ) -> dict[str, Any]:
        control_benefit = [r for r in control_rows if r.get("case") in set(benefit)]
        candidate_benefit = [r for r in candidate_rows if r.get("case") in set(benefit)]
        detection = paired_delta(list(control_benefit), list(candidate_benefit))
        localization = paired_localization_delta(
            list(control_benefit), list(candidate_benefit),
            gold_steps=self.protocol.get("gold_steps") or {},
        )
        return {
            "detection": detection,
            "localization": localization,
            "protection_rows": [
                r for r in protection
            ],
        }

    def run(self) -> dict[str, Any]:
        if self.protocol.get("schema_version") == alignment.ALIGNED_PROTOCOL_SCHEMA:
            return self._run_aligned()
        # All four formal gates are mandatory in every promotion, not only in
        # the mechanism-aligned shape. `gates` defaults a skipped gate to
        # {"passed": True, "skipped": True}, so a run that drops one from
        # --stages admits the candidate and then records a reason string
        # claiming the gate passed. The gate set is part of what was frozen;
        # it is not an operator knob.
        if set(self.stages) != set(alignment.FORMAL_STAGES):
            report = {
                "schema_version": PROMOTION_STATE_SCHEMA,
                "state": "promotion_blocked_preflight",
                "admitted": False,
                "candidate_id": self.candidate.get("candidate_id"),
                "candidate_content_hash": self.candidate.get("content_hash"),
                "terminal_reason": (
                    "all four formal gates are mandatory in a promotion; "
                    f"requested {sorted(self.stages)}, expected "
                    f"{sorted(alignment.FORMAL_STAGES)}"
                ),
                "promotion_diagnosis": alignment.diagnosis("protocol_invalid"),
            }
            self._write(report)
            return report
        if self.candidate.get("target_mechanism_id"):
            report = {
                "schema_version": PROMOTION_STATE_SCHEMA,
                "state": "promotion_blocked_preflight", "admitted": False,
                "candidate_id": self.candidate.get("candidate_id"),
                "candidate_content_hash": self.candidate.get("content_hash"),
                "terminal_reason": "mechanism-bound candidates require a mechanism-aligned protocol",
                "promotion_diagnosis": alignment.diagnosis("mechanism_alignment_missing"),
            }
            self._write(report)
            return report
        # A measurement path may not run on a protocol that cannot show which
        # trajectories it froze. Synthetic freezes (no dataset root) record
        # null digests; they exist for tests and structural dry runs, not for
        # scoring a candidate against real evidence. `trajectory_pinned` is set
        # by ``load_protocol``, so a directly injected protocol (the offline
        # harness) is unaffected.
        if self.protocol.get("trajectory_pinned") is False:
            report = {
                "schema_version": PROMOTION_STATE_SCHEMA,
                "state": "promotion_blocked_preflight", "admitted": False,
                "candidate_id": self.candidate.get("candidate_id"),
                "candidate_content_hash": self.candidate.get("content_hash"),
                "terminal_reason": (
                    "the frozen protocol pins no trajectory content (it was "
                    "frozen without a dataset root); re-freeze it before "
                    "measuring a candidate"
                ),
                "promotion_diagnosis": alignment.diagnosis("protocol_invalid"),
            }
            self._write(report)
            return report
        import time as _time

        started = _time.monotonic()
        if self.timeout_seconds is not None:
            self.deadline = started + self.timeout_seconds
        candidate = self.candidate
        target_kind = candidate_target_kind(
            (candidate.get("artifacts") or [{}])[0]
        ) if candidate.get("artifacts") else "detection"
        declared = str(candidate.get("target_kind") or "detection")
        if declared == "joint" or "joint" in {
            str((artifact.get("claim") or {}).get("target_kind") or "detection")
            for artifact in candidate.get("artifacts") or ()
        }:
            target_kind = "joint"
        elif declared == "localization" or target_kind == "localization":
            target_kind = "localization"
        report: dict[str, Any] = {
            "schema_version": PROMOTION_STATE_SCHEMA,
            "candidate_id": candidate.get("candidate_id"),
            "candidate_content_hash": candidate.get("content_hash"),
            "target_kind": target_kind,
            "protocol": {
                "frozen_at": self.protocol.get("frozen_at"),
                "batch_size": len(self.protocol.get("resolved_cases") or ()),
                "benefit": list(benefit_cases(self.protocol)),
                "protection": list(protection_cases(self.protocol)),
                "workers": self.workers,
            },
            "stages_requested": list(self.stages),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        entries = list(self.registry.entries)
        artifacts = [dict(entry) for entry in candidate.get("artifacts") or ()]
        benefit = benefit_cases(self.protocol)
        protection = protection_cases(self.protocol)
        all_cases = sorted(set(benefit) | set(protection))

        # The control arm is whatever is deployed at run time. If the protocol
        # froze a baseline identity, a deployment that landed between freeze
        # and run has silently changed the comparison -- the delta would then
        # mix the candidate's effect with whatever the later version did.
        frozen_baseline = str(self.protocol.get("baseline_registry_hash") or "")
        if frozen_baseline:
            current_baseline = str(
                self._registry_for(entries).get("content_hash") or ""
            )
            if current_baseline != frozen_baseline:
                report.update(
                    state="promotion_blocked_preflight",
                    terminal_reason=(
                        "active registry changed after the protocol was frozen; "
                        "the control arm would not be the version the protocol "
                        "was frozen against"
                    ),
                    admitted=False,
                    gates={},
                    elapsed_seconds=round(_time.monotonic() - started, 3),
                )
                self._write(report)
                return report

        # A candidate already deployed is a conflict with the deployment, not
        # a runtime failure. The registry refuses the duplicate id at
        # construction time, before any model call; left to the broad handler
        # around the arms it would be recorded as `promotion_unresolved` /
        # `runtime_unresolved`, where the correct operator action ("pick
        # another candidate") reads as "retry".
        existing_ids = {
            str(entry.get("intervention_id") or "")
            for entry in self.registry.entries
        }
        duplicates = sorted(
            str(artifact.get("intervention_id") or "")
            for artifact in artifacts
            if str(artifact.get("intervention_id") or "") in existing_ids
        )
        if duplicates:
            report.update(
                state="promotion_blocked_preflight",
                terminal_reason=(
                    "candidate is already deployed: "
                    f"{duplicates}; a version cannot be promoted twice"
                ),
                admitted=False,
                gates={},
                elapsed_seconds=round(_time.monotonic() - started, 3),
            )
            self._write(report)
            return report

        # One frozen protocol, one attempt. Claimed before the free STRUCTURAL
        # check as well: the claim is about the evidence, not about how far the
        # run got.
        try:
            self._claim_protocol()
        except (ValueError, KeyError, OSError) as exc:
            report.update(
                state="promotion_blocked_preflight",
                terminal_reason=str(exc), admitted=False, gates={},
                elapsed_seconds=round(_time.monotonic() - started, 3),
            )
            self._write(report)
            return report

        # STRUCTURAL first: it is free. A candidate that is not even a legal
        # deployment unit must not spend a single model call.
        structural = self._structural()
        report["structural"] = structural
        if not structural["passed"]:
            report.update(
                state="promotion_rejected",
                terminal_reason=structural["reason"],
                gates={"structural": structural},
                elapsed_seconds=round(_time.monotonic() - started, 3),
            )
            self._write(report)
            return report

        active_registry_entries = entries
        control_registry = active_registry_entries
        candidate_registry = [*active_registry_entries, *artifacts]
        # Either the hooks demand it, or the protocol declares it. The two are
        # not alternatives: ``requires_fresh_evaluation`` already forces fresh
        # for judge hooks, and ``require_fresh`` widens that to every hook so a
        # mechanism on a replayed stage is still measured live.
        fresh = self.require_fresh or requires_fresh_evaluation(
            candidate_registry
        )
        try:
            # Both arms are measured inside one batch so they share the same
            # transport conditions; concurrency is the protocol's declared
            # worker count per arm.
            with ThreadPoolExecutor(max_workers=2) as pool:
                control_future = pool.submit(
                    self._run_arm, control_registry, all_cases,
                    fresh=fresh, label="control",
                )
                candidate_future = pool.submit(
                    self._run_arm, candidate_registry, all_cases,
                    fresh=fresh, label="candidate",
                )
                control_rows = control_future.result()
                candidate_rows = candidate_future.result()
        except Exception as exc:
            report.update(
                state="promotion_unresolved",
                terminal_reason=f"{type(exc).__name__}: {exc}",
                gates={"structural": structural},
                elapsed_seconds=round(_time.monotonic() - started, 3),
            )
            self._write(report)
            return report

        blocked = arm_has_infrastructure_failure(
            [*control_rows, *candidate_rows],
        )
        report["arm_health"] = {"blocked": blocked, "per_arm": dict(self.arm_health)}
        report["resumed_arms"] = sorted(self.resumed_arms)

        control_protection = [
            r for r in control_rows if r.get("case") in set(protection)
        ]
        candidate_protection = [
            r for r in candidate_rows if r.get("case") in set(protection)
        ]
        control_benefit = [r for r in control_rows if r.get("case") in set(benefit)]
        candidate_benefit = [
            r for r in candidate_rows if r.get("case") in set(benefit)
        ]

        # An arm that could not be measured is not a candidate result. An
        # unresolved control row counts as "not correct" while an unresolved
        # candidate row can still be correct, so scoring an outage would let a
        # transport failure masquerade as a marginal gain. The cycle protocol
        # already freezes on this condition; the promotion gate does the same
        # instead of adjudicating on a pair that was never comparable.
        if blocked:
            report["benefit"] = {
                "detection": _delta_summary(
                    paired_delta(list(control_benefit), list(candidate_benefit))
                ) if control_benefit and candidate_benefit else None,
                "localization": None,
                "note": (
                    "recorded for diagnosis only; the run is unresolved and "
                    "these deltas did not adjudicate anything"
                ),
            }
            report.update(
                state="promotion_unresolved",
                admitted=False,
                terminal_reason=(
                    "arm measurement could not be completed honestly: " + blocked
                ),
                gates={"structural": structural},
                elapsed_seconds=round(_time.monotonic() - started, 3),
            )
            self._write(report)
            return report

        gates: dict[str, Any] = {"structural": structural}

        # HARMLESSNESS on the protection set. Coverage is checked against the
        # control arm and is reported as unmeasured, never as a rejection.
        coverage = sentinel_coverage(control_protection, self.config)
        if "harmlessness" in self.stages:
            if not coverage["evaluable"]:
                gates["harmlessness"] = {
                    "passed": False,
                    "reason": (
                        "promotion protection set lacks baseline-correct "
                        "coverage: " + coverage["reason"]
                    ),
                    "evaluable": False,
                    "status": "not_evaluated",
                    "coverage": coverage,
                }
            else:
                gates["harmlessness"] = harmlessness_gate(
                    baseline_rows=list(control_protection),
                    candidate_rows=list(candidate_protection),
                    config=self.config,
                )
        else:
            gates["harmlessness"] = {"passed": True, "reason": "", "skipped": True}

        detection = paired_delta(list(control_benefit), list(candidate_benefit))
        localization = paired_localization_delta(
            list(control_benefit), list(candidate_benefit),
            gold_steps=self.protocol.get("gold_steps") or {},
        )
        report["benefit"] = {
            "detection": _delta_summary(detection),
            "localization": _localization_summary(localization),
            "control_localization": localization_metrics(
                control_benefit, gold_steps=self.protocol.get("gold_steps") or {},
            ),
            "candidate_localization": localization_metrics(
                candidate_benefit, gold_steps=self.protocol.get("gold_steps") or {},
            ),
            # Recorded, never scored: what the candidate did to cases the
            # control arm had wrong (repair) and to cases it had right
            # (regression). HARMLESSNESS sees only the second kind and M sees
            # neither, so without this reading a candidate that fixes the
            # control arm's errors is invisible. The two counts are reported
            # side by side and are deliberately not summed.
            "protection_repair": _repair_summary(
                protection_repair(control_protection, candidate_protection)
            ),
            "benefit_repair": _repair_summary(
                protection_repair(control_benefit, candidate_benefit)
            ),
        }

        behavior: dict[str, Any] = {"passed": True, "reason": "", "skipped": True}
        m: dict[str, Any] = {"passed": True, "reason": "", "skipped": True}
        # Repair opportunity is a property of the instrument, not of the
        # candidate: a batch where the control arm is already right in the
        # claimed direction cannot score the claim at all. Reporting that as a
        # rejection would call an unmeasured mechanism falsified.
        opportunity = gain_opportunity(control_benefit)
        localization_op = localization_opportunity(
            control_benefit, gold_steps=self.protocol.get("gold_steps") or {},
        )
        report["opportunity"] = {
            "detection": opportunity,
            "localization": localization_op,
        }
        if target_kind == "detection":
            scored_direction = any(
                opportunity.get(
                    str((artifact.get("claim") or {}).get("target_polarity") or "")
                )
                for artifact in artifacts
            )
            unmeasurable_claim = not scored_direction
        else:
            unmeasurable_claim = localization_op["improvable"] <= 0
        if gates["harmlessness"].get("passed"):
            # Each artifact must expose its own semantic behavior verdict, as
            # in a joint cycle unit: the group is sold as one version.
            member_behavior: dict[str, Any] = {}
            for artifact in artifacts:
                if "behavior" not in self.stages or unmeasurable_claim:
                    member_behavior[str(artifact.get("intervention_id"))] = {
                        "passed": False,
                        "reason": (
                            "no repair opportunity in the frozen promotion batch "
                            f"for this claim ({target_kind})"
                        ),
                        "evaluable": False,
                        "status": "not_evaluated",
                    }
                    continue
                kind = candidate_target_kind(artifact)
                if kind in {"localization", "joint"}:
                    member_behavior[str(artifact.get("intervention_id"))] = (
                        localization_behavior_gate(
                            artifact,
                            baseline_rows=list(control_benefit),
                            candidate_rows=list(candidate_benefit),
                            config=self.config,
                            gold_steps=self.protocol.get("gold_steps") or {},
                        )
                    )
                elif kind == "resolution":
                    member_behavior[str(artifact.get("intervention_id"))] = (
                        resolution_behavior_gate(
                            artifact,
                            baseline_rows=[*control_protection, *control_benefit],
                            candidate_rows=[*candidate_protection, *candidate_benefit],
                            config=self.config,
                        )
                    )
                else:
                    member_behavior[str(artifact.get("intervention_id"))] = (
                        behavior_gate(
                            artifact,
                            baseline_rows=[*control_protection, *control_benefit],
                            candidate_rows=[*candidate_protection, *candidate_benefit],
                            config=self.config,
                        )
                    )
            behavior = next(iter(member_behavior.values())) if len(
                member_behavior
            ) == 1 else {
                "passed": all(v["passed"] for v in member_behavior.values()),
                "reason": "; ".join(
                    f"{key}: {value['reason']}"
                    for key, value in member_behavior.items()
                    if not value["passed"]
                ),
                "members": member_behavior,
            }
            gates["behavior"] = behavior
            if behavior.get("passed") and "m" in self.stages:
                if target_kind == "detection":
                    passed, failures = meets_m(detection, config=self.config)
                elif target_kind == "resolution":
                    passed, failures = meets_resolution(detection, config=self.config)
                else:
                    passed, failures = meets_localization(
                        localization,
                        config=self.config,
                        detection_delta=detection,
                        target_kind=target_kind,
                    )
                m = {
                    "passed": passed,
                    "reason": "; ".join(failures),
                    "delta": detection,
                    "localization_delta": localization,
                }
                gates["m"] = m
        else:
            gates["behavior"] = {
                "passed": False, "reason": "not reached: harmlessness failed",
                "evaluable": False, "status": "not_evaluated",
            }
            gates["m"] = {
                "passed": False, "reason": "not reached: harmlessness failed",
                "evaluable": False, "status": "not_evaluated",
            }
        report["gates"] = gates
        self._last_gates = dict(gates)

        failing = [
            name for name, verdict in gates.items()
            if verdict.get("passed") is False
        ]
        unevaluable = all(
            (gates[name].get("evaluable") is False)
            or str(gates[name].get("status") or "") in {
                "not_evaluated", "skipped_unmeasured",
            }
            for name in failing
        ) if failing else False
        admitted = not failing
        if admitted:
            state = "promoted"
            terminal_reason = ""
        elif unevaluable:
            state = "promotion_unmeasurable"
            terminal_reason = "; ".join(
                f"{name}: {gates[name].get('reason')}" for name in failing
            )
        else:
            state = "promotion_rejected"
            terminal_reason = "; ".join(
                f"{name}: {gates[name].get('reason')}" for name in failing
            )
        report.update(
            state=state,
            terminal_reason=terminal_reason,
            admitted=admitted,
            elapsed_seconds=round(_time.monotonic() - started, 3),
        )

        if admitted:
            library_problems = self.registry.check_limits(
                candidate_registry, library_cfg=self.config.get("library") or {},
            )
            if library_problems:
                report.update(
                    state="promotion_blocked_preflight",
                    terminal_reason="; ".join(library_problems),
                    admitted=False,
                )
            else:
                report["version"] = self._publish()
        self._write(report)
        return report

    def _run_aligned(self) -> dict[str, Any]:
        started = time.monotonic()
        if self.timeout_seconds is not None:
            self.deadline = started + self.timeout_seconds
        protocol = self.protocol
        candidate = self.candidate
        mechanism_id = str(protocol.get("mechanism_id") or "")
        report: dict[str, Any] = {
            "schema_version": PROMOTION_STATE_SCHEMA,
            "candidate_id": candidate.get("candidate_id"),
            "candidate_content_hash": candidate.get("content_hash"),
            "target_kind": candidate.get("target_kind"),
            "mechanism_id": mechanism_id,
            "protocol_hash": protocol.get("protocol_hash"),
            "evidence_scope": "candidate_unused_development_data",
            "admitted": False, "gates": {},
            "protocol": {
                "benefit": benefit_cases(protocol),
                "protection": protection_cases(protocol),
                "challenge": list(protocol.get("challenge_cases") or ()),
                "workers": self.workers,
            },
        }

        def finish(state: str, code: str, reason: str = "") -> dict[str, Any]:
            report.update(
                state=state, terminal_reason=reason, admitted=state == "promoted",
                elapsed_seconds=round(time.monotonic() - started, 3),
                promotion_diagnosis=alignment.diagnosis(
                    code, mechanism_id=mechanism_id, reason=reason,
                ),
            )
            self._write(report)
            return report

        # An unbound candidate is a missing alignment, not a broken protocol: it
        # has to be reported as its own diagnosis so the next step (bind the
        # candidate at freeze time) is readable from the terminal state.
        if (
            candidate.get("schema_version") != alignment.ALIGNED_CANDIDATE_SCHEMA
            or not candidate.get("target_mechanism_id")
        ):
            return finish(
                "promotion_blocked_preflight", "mechanism_alignment_missing",
                "mechanism-bound promotion needs a v2 candidate bound to a "
                "mechanism; bind it before freezing the protocol",
            )
        try:
            alignment.verify_alignment(protocol)
            _validate_candidate_identity(candidate)
            if set(self.stages) != set(alignment.FORMAL_STAGES):
                raise ValueError(
                    "all four formal gates are mandatory in a mechanism-aligned promotion"
                )
            if candidate["content_hash"] != protocol["candidate_content_hash"]:
                raise ValueError("candidate changed after the protocol was frozen")
            if candidate["target_mechanism_id"] != mechanism_id:
                raise ValueError("candidate and protocol target different mechanisms")
            if alignment.digest(candidate["mechanism"]) != alignment.digest(protocol["mechanism"]):
                raise ValueError("candidate and protocol mechanism evidence differ")
            if set(alignment.development_cases(candidate)) - set(protocol["development_cases"]):
                raise ValueError("protocol omits candidate development exposure")
            if alignment.frozen_policy(self.config) != protocol["policy"]:
                raise ValueError("gate or library policy changed after freeze")
            if source_identity("detector", "evolution") != protocol["code_baseline"]:
                raise ValueError("source changed after protocol freeze")
            if digest(json.loads(Path(protocol["detector_config"]).read_text(encoding="utf-8-sig"))) != protocol["detector_config_hash"]:
                raise ValueError("detector configuration changed after freeze")
            if protocol["case_digests"] != alignment.data_digests(self.dataset_root, protocol["cases"]):
                raise ValueError("promotion trajectory content changed after freeze")
            if self._registry_for(self.registry.entries)["content_hash"] != protocol["baseline_registry_hash"]:
                raise ValueError("active baseline changed after protocol freeze")
            if not self.ledger.verify()["valid"]:
                raise ValueError("ledger integrity check failed")
            self._claim_aligned_protocol()
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return finish("promotion_blocked_preflight", "protocol_invalid", str(exc))

        artifacts = [dict(entry) for entry in candidate["artifacts"]]
        entries = list(self.registry.entries)
        exposed = set(alignment.development_cases(candidate))
        if exposed & set(protocol["cases"]):
            return finish("promotion_rejected", "candidate_invalid", "candidate-unused rule violated")
        # An already-deployed candidate is a deployment conflict, not an
        # outage: `make_registry` refuses the duplicate id before any model
        # call, and left to the broad handler around the arms that refusal
        # would be recorded as runtime_unresolved -- which reads as "retry".
        deployed = {
            str(entry.get("intervention_id") or "") for entry in entries
        }
        duplicates = sorted(
            str(artifact.get("intervention_id") or "")
            for artifact in artifacts
            if str(artifact.get("intervention_id") or "") in deployed
        )
        if duplicates:
            return finish(
                "promotion_blocked_preflight", "candidate_invalid",
                "candidate is already deployed: "
                f"{duplicates}; a version cannot be promoted twice",
            )
        try:
            validate_intervention_batch([*entries, *artifacts], require_executable_bounds=True)
            if any(candidate_contains_case_id(entry) for entry in artifacts):
                raise ValueError("case id leak in candidate executable prose")
            # Development results never substitute for formal gates. Failed or
            # missing L0/L1 on this source cannot be imported as a frozen success.
            development = candidate.get("development_results") or {}
            if development.get("source") == "development":
                if any((development.get(level) or {}).get("passed") is not True for level in ("l0", "l1")):
                    raise ValueError("development candidate lacks passing L0/L1 evidence")
            elif development.get("screening_passed") is False:
                raise ValueError("research screening verdict did not pass")
            for entry in artifacts:
                verdict = structural_gate(
                    entry, active_entries=entries, config=self.config,
                    dataset_root=self.dataset_root, batch_candidates=artifacts,
                )
                if not verdict["passed"]:
                    raise ValueError(verdict["reason"])
        except ValueError as exc:
            report["gates"]["structural"] = {"passed": False, "reason": str(exc)}
            return finish("promotion_rejected", "candidate_invalid", str(exc))
        report["gates"]["structural"] = {"passed": True, "reason": ""}
        all_cases = list(protocol["cases"])
        fresh = self.require_fresh or requires_fresh_evaluation([*entries, *artifacts])
        try:
            control = alignment.checked_rows(
                self._run_arm(entries, all_cases, fresh=fresh, label="control"), protocol,
            )
        except (ValueError, OSError, RuntimeError) as exc:
            return finish("promotion_unresolved", "runtime_unresolved", str(exc))
        measured = alignment.opportunity(protocol, control)
        report["opportunity"] = {"mechanism": measured, "detection": gain_opportunity(control)}
        if not measured["evaluable"]:
            state = "promotion_unresolved" if measured["unresolved"] else "promotion_unmeasurable"
            return finish(state, measured["reason_code"], "the frozen gain set has no measurable repair opportunity for this mechanism")
        protection_ids = set(protection_cases(protocol))
        challenge_ids = set(protocol["challenge_cases"])
        gain_ids = set(protocol["mechanism_gain_cases"])
        control_protection = [row for row in control if row["case"] in protection_ids]
        coverage = sentinel_coverage(control_protection, self.config)
        report["protection_coverage"] = coverage
        if not coverage["evaluable"]:
            return finish("promotion_unmeasurable", "protection_coverage_missing", coverage["reason"])
        objective = str(candidate["target_kind"])
        if objective in {"localization", "joint"}:
            preserved = paired_localization_delta(control_protection, control_protection, gold_steps=protocol["gold_steps"])
            if preserved["base_joint"] < 1:
                return finish("promotion_unmeasurable", "localization_protection_missing", "protection set has no baseline-correct localization")
        try:
            active = alignment.checked_rows(
                self._run_arm([*entries, *artifacts], all_cases, fresh=fresh, label="candidate"), protocol,
            )
        except (ValueError, OSError, RuntimeError) as exc:
            return finish("promotion_unresolved", "runtime_unresolved", str(exc))
        control_gain = [row for row in control if row["case"] in gain_ids]
        active_gain = [row for row in active if row["case"] in gain_ids]
        active_protection = [row for row in active if row["case"] in protection_ids]
        control_challenge = [row for row in control if row["case"] in challenge_ids]
        active_challenge = [row for row in active if row["case"] in challenge_ids]
        detection = paired_delta(control_gain, active_gain)
        localization = paired_localization_delta(control_gain, active_gain, gold_steps=protocol["gold_steps"])
        all_detection = paired_delta(control, active)
        all_localization = paired_localization_delta(control, active, gold_steps=protocol["gold_steps"])
        challenge_delta = paired_delta(control_challenge, active_challenge)
        report["benefit"] = {"detection": _delta_summary(detection), "localization": _localization_summary(localization)}
        report["challenge"] = {"metric_role": "promotion-authoritative", "delta": _delta_summary(challenge_delta)}
        report["localization_protection"] = _localization_summary(all_localization)
        harmlessness = harmlessness_gate(baseline_rows=control_protection, candidate_rows=active_protection, config=self.config)
        report["gates"]["harmlessness"] = harmlessness
        if not harmlessness["passed"]:
            return finish("promotion_rejected", "protection_regression", harmlessness["reason"])
        if challenge_delta["new_errors"] or challenge_delta["unresolved_added"]:
            report["gates"]["challenge"] = {"passed": False, "reason": "mechanism-external challenge regression"}
            return finish("promotion_rejected", "challenge_regression", "mechanism-external challenge regression")
        report["gates"]["challenge"] = {"passed": True, "reason": ""}
        if all_localization["joint_broken"]:
            return finish("promotion_rejected", "localization_regression", "a baseline-correct localization regressed")
        if all_detection["new_errors"] or all_detection["unresolved_added"]:
            return finish("promotion_rejected", "protection_regression", "a previously correct decision regressed")
        member_behavior = {}
        for entry in artifacts:
            kind = candidate_target_kind(entry)
            detection_behavior = behavior_gate(entry, baseline_rows=control_gain, candidate_rows=active_gain, config=self.config)
            local_behavior = localization_behavior_gate(entry, baseline_rows=control_gain, candidate_rows=active_gain, config=self.config, gold_steps=protocol["gold_steps"])
            if kind == "joint":
                verdict = {"passed": detection_behavior["passed"] or local_behavior["passed"],
                           "reason": "; ".join(v["reason"] for v in (detection_behavior, local_behavior) if not v["passed"]),
                           "detection": detection_behavior, "localization": local_behavior}
            elif kind == "localization":
                verdict = local_behavior
            elif kind == "resolution":
                verdict = resolution_behavior_gate(entry, baseline_rows=control_gain, candidate_rows=active_gain, config=self.config)
            else:
                verdict = detection_behavior
            member_behavior[str(entry["intervention_id"])] = verdict
        passed = all(item["passed"] for item in member_behavior.values())
        report["gates"]["behavior"] = {"passed": passed, "members": member_behavior}
        if not passed:
            return finish("promotion_rejected", "candidate_no_effect", "candidate did not improve its declared objective on mechanism gain cases")
        if objective == "detection":
            passed, reasons = meets_m(detection, config=self.config)
        elif objective == "resolution":
            passed, reasons = meets_resolution(detection, config=self.config)
        else:
            passed, reasons = meets_localization(localization, config=self.config, detection_delta=detection, target_kind=objective)
        report["gates"]["m"] = {"passed": passed, "reason": "; ".join(reasons), "delta": detection, "localization_delta": localization}
        if not passed:
            return finish("promotion_rejected", "candidate_no_effect", "; ".join(reasons))
        problems = self.registry.check_limits([*entries, *artifacts], library_cfg=self.config.get("library") or {})
        if problems:
            return finish("promotion_blocked_preflight", "library_limit", "; ".join(problems))
        self._last_gates = dict(report.get("gates") or {})
        report["version"] = self._publish()
        return finish("promoted", "promoted")

    def _claim_protocol(self) -> None:
        """Claim a frozen protocol for exactly one promotion attempt.

        Without a claim, a rejected candidate is re-scored simply by running
        again -- to the same output directory or a different one -- until the
        provider roll happens to come out favourable. The claim is keyed on the
        protocol's own hash, so it also carries over a protocol frozen before
        this guard existed: a run may not be retried on the same frozen
        evidence, and a new attempt means a new freeze.
        """
        path = Path(str(self.protocol.get("loaded_from") or ""))
        if not path.is_file():
            raise ValueError(
                "a promotion attempt needs the protocol's persisted source path"
            )
        protocol_hash = str(self.protocol.get("protocol_hash") or "")
        if not protocol_hash:
            raise ValueError("frozen protocol carries no protocol_hash to claim")
        claim_path = path.with_name(path.name + ".attempt.json")
        identity = {
            "protocol_hash": protocol_hash,
            "candidate_content_hash": self.candidate.get("content_hash"),
            "output_dir": str(self.output_dir.resolve()),
        }
        if claim_path.exists():
            recorded = json.loads(claim_path.read_text(encoding="utf-8"))
            if recorded.get("protocol_hash") != protocol_hash:
                raise ValueError(
                    "protocol already consumed by another promotion attempt"
                )
            if recorded != identity:
                raise ValueError(
                    "protocol already consumed by another promotion attempt "
                    f"(recorded {recorded.get('output_dir')!r}, this run "
                    f"{identity['output_dir']!r}); a frozen protocol is "
                    "measured once"
                )
            return        claim_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with claim_path.open("x", encoding="utf-8") as handle:
                json.dump(identity, handle, ensure_ascii=False, sort_keys=True)
        except FileExistsError:
            recorded = json.loads(claim_path.read_text(encoding="utf-8"))
            if recorded.get("protocol_hash") != protocol_hash:
                raise ValueError(
                    "protocol already consumed by another promotion attempt"
                )

    def _claim_aligned_protocol(self) -> None:
        path = Path(str(self.protocol.get("loaded_from") or ""))
        if not path.is_file():
            raise ValueError("mechanism-aligned protocol must have a persisted source path")
        claim_path = path.with_name(path.name + ".attempt.json")
        identity = {"protocol_hash": self.protocol["protocol_hash"],
                    "candidate_content_hash": self.candidate["content_hash"],
                    "output_dir": str(self.output_dir.resolve())}
        if claim_path.exists():
            if json.loads(claim_path.read_text(encoding="utf-8")) != identity:
                raise ValueError("protocol already consumed by another promotion attempt")
            return
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with claim_path.open("x", encoding="utf-8") as handle:
                json.dump(identity, handle, ensure_ascii=False, sort_keys=True)
        except FileExistsError:
            if json.loads(claim_path.read_text(encoding="utf-8")) != identity:
                raise ValueError("protocol already consumed by another promotion attempt")

    # -- publication ------------------------------------------------------
    def _publish(self) -> dict[str, Any]:
        """Commit the admitted version and record it in registry and ledger.

        This is the only place the active registry is written. The ledger
        entry carries the candidate identity so a later round can attribute
        the deployed version to the candidate and protocol that produced it.
        """
        artifacts = [dict(entry) for entry in self.candidate.get("artifacts") or ()]
        # The registry cycle counter must not be reset to 0 by a promotion:
        # rollback windows (`cycle - threshold`), the "start from an empty
        # registry" precondition of the full evolution run, and the version id
        # all read it. A protocol is free to carry no cycle field -- none was
        # ever written -- so fall back to the count the registry already has
        # rather than to zero.
        current_cycle = int(
            (self.registry.state or {}).get("cycle") or 0
        )
        cycle = int(self.protocol.get("cycle") or current_cycle or 0)
        gates_evaluated = sorted(
            name for name, verdict in (self._last_gates or {}).items()
            if verdict.get("passed") is True
        )
        # The recorded admission reason names the gates that actually ran.
        # A hardcoded "STRUCTURAL/HARMLESSNESS/BEHAVIOR/M" would make the
        # registry assert four gates on a run whose gate set was cut down,
        # which no later audit could detect from the record.
        gate_names = "/".join(name.upper() for name in gates_evaluated) or "NO-GATES"
        payload = self.registry.write(
            [*self.registry.entries, *artifacts],
            cycle=cycle,
            reason=(
                f"promotion: {gate_names} passed on the frozen promotion "
                f"protocol {self.protocol.get('frozen_at')}"
            ),
            library_cfg=self.config.get("library") or {},
        )
        version = {
            "version_id": (
                f"V{self.registry.state.get('cycle', 0)}-"
                f"{self.candidate.get('content_hash')}"
            ),
            "candidate_id": self.candidate.get("candidate_id"),
            "candidate_content_hash": self.candidate.get("content_hash"),
            "registry_hash": str(payload.get("content_hash") or ""),
            "base_revision": self.candidate.get("base_revision"),
            "change_kind": self.candidate.get("change_kind"),
            "artifacts": [
                str(entry.get("intervention_id") or "") for entry in artifacts
            ],
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "protocol": self.protocol.get("loaded_from"),
        }
        if self.protocol.get("schema_version") == alignment.ALIGNED_PROTOCOL_SCHEMA:
            version.update(
                mechanism_id=self.protocol["mechanism_id"],
                protocol_hash=self.protocol["protocol_hash"],
                parent_registry_hash=self.protocol["baseline_registry_hash"],
                development_cases=list(self.candidate["development_cases"]),
            )
        save_json(self.output_dir / "version.json", version)
        self.ledger.append("candidate_promoted", {
            "candidate_id": self.candidate.get("candidate_id"),
            "candidate_content_hash": self.candidate.get("content_hash"),
            "target_kind": self.candidate.get("target_kind"),
            "base_revision": self.candidate.get("base_revision"),
            "registry_hash": version["registry_hash"],
            "artifacts": version["artifacts"],
            "protocol": self.protocol.get("loaded_from"),
            "frozen_at": self.protocol.get("frozen_at"),
        })
        return version

    def _write(self, report: Mapping[str, Any]) -> None:
        if "promotion_diagnosis" not in report and isinstance(report, dict):
            state = str(report.get("state") or "")
            code = alignment.LEGACY_STATE_REASONS.get(state, "runtime_unresolved")
            report["promotion_diagnosis"] = alignment.diagnosis(code, reason=str(report.get("terminal_reason") or ""))
            report["evidence_scope"] = "legacy_polarity_evaluation"
        save_json(self.output_dir / "promotion.json", report)
        self.ledger.append("promotion_attempt", {
            "candidate_id": report.get("candidate_id"),
            "candidate_content_hash": report.get("candidate_content_hash"),
            "state": report.get("state"),
            "target_kind": report.get("target_kind"),
            "reason": report.get("terminal_reason"),
            "protocol": self.protocol.get("loaded_from"),
            "protocol_hash": self.protocol.get("protocol_hash"),
            "mechanism_id": report.get("mechanism_id"),
            "promotion_diagnosis": report.get("promotion_diagnosis"),
        })


def _delta_summary(delta: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "paired_n": int(delta.get("paired_n") or 0),
        "marginal_cases": int(delta.get("marginal_cases") or 0),
        "new_errors": int(delta.get("new_errors") or 0),
        "new_fn": int(delta.get("new_fn") or 0),
        "new_fp": int(delta.get("new_fp") or 0),
        "tp_lost": int(delta.get("tp_lost") or 0),
        "fp_added": int(delta.get("fp_added") or 0),
        "unresolved_added": int(delta.get("unresolved_added") or 0),
        "health_regressed": int(delta.get("health_regressed") or 0),
        "base_all_case_correct": int(delta.get("base_all_case_correct") or 0),
        "active_all_case_correct": int(delta.get("active_all_case_correct") or 0),
        "flips": dict(delta.get("flips") or {}),
        "rows": list(delta.get("rows") or ()),
    }


def _repair_summary(repair: Mapping[str, Any]) -> dict[str, Any]:
    """The direction-split reading of a set, with no combined score.

    ``repaired`` and ``regressed`` stay separate on purpose: summing them would
    be the weighted compensation plan §5.2 forbids.
    """
    return {
        "n": int(repair.get("n") or 0),
        "repaired": int(repair.get("repaired") or 0),
        "repaired_fn": int(repair.get("repaired_fn") or 0),
        "repaired_fp": int(repair.get("repaired_fp") or 0),
        "regressed": int(repair.get("regressed") or 0),
        "regressed_fn": int(repair.get("regressed_fn") or 0),
        "regressed_fp": int(repair.get("regressed_fp") or 0),
        "unresolved_added": int(repair.get("unresolved_added") or 0),
        "unresolved_removed": int(repair.get("unresolved_removed") or 0),
        "net": None,
        "rows": list(repair.get("rows") or ()),
    }


def _localization_summary(delta: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "localizable_n": int(delta.get("localizable_n") or 0),
        "base_joint": int(delta.get("base_joint") or 0),
        "active_joint": int(delta.get("active_joint") or 0),
        "joint_delta": int(delta.get("joint_delta") or 0),
        "joint_improved": int(delta.get("joint_improved") or 0),
        "joint_broken": int(delta.get("joint_broken") or 0),
        "rows": list(delta.get("rows") or ()),
    }


def promote(
    *,
    candidate: Mapping[str, Any],
    protocol_path: str | Path,
    output_dir: str | Path,
    config: Mapping[str, Any] | None = None,
    backend: Any | None = None,
    stages: Sequence[str] = ("structural", "harmlessness", "behavior", "m"),
) -> dict[str, Any]:
    """One call: freeze-check, gate, and record a promotion attempt.

    Returns the promotion report. The active registry is written only for a
    ``promoted`` result; every other terminal state leaves deployment unchanged
    and is recorded with its reason.
    """
    from evolution.config import load_config

    config = dict(config or load_config())
    dataset_root = config["dataset_root"]
    protocol = load_protocol(
        protocol_path, dataset_root=dataset_root, stages=stages, config=config,
    )
    candidate = dict(candidate)
    _transition(candidate, "promotion_pending")
    run = PromotionRun(
        candidate=candidate,
        protocol=protocol,
        config=config,
        output_dir=output_dir,
        backend=backend,
        stages=stages,
    )
    report = run.run()
    if report.get("state") == "promoted":
        _transition(candidate, "active")
    elif report.get("state") == "promotion_rejected":
        _transition(candidate, "frozen")
    report["candidate_status"] = candidate.get("status")
    return report


__all__ = [
    "CANDIDATE_SCHEMA",
    "CANDIDATE_STATES",
    "PROMOTION_PROTOCOL_SCHEMA",
    "PROMOTION_STATE_SCHEMA",
    "PROMOTION_TERMINALS",
    "PromotionProtocolError",
    "PromotionRun",
    "benefit_cases",
    "build_candidate",
    "candidate_content_hash",
    "candidate_from_registry_patch",
    "candidate_from_research",
    "freeze_protocol",
    "load_protocol",
    "promote",
    "protection_cases",
]
