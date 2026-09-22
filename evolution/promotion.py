"""Promotion bridge: a frozen research candidate becomes a deployable version.

This is the implementation of the first optimization work package
(docs/optimization-plan.md §4): one unified candidate object, one entry that
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

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.interventions import (
    build_intervention_registry,
    validate_intervention,
)

from evolution.backends import (
    fingerprint,
    make_backend,
    source_digest,
)
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
    requires_fresh_evaluation,
    sentinel_coverage,
    structural_gate,
)
from evolution.ledger import load_ledger
from evolution.metrics import (
    gain_opportunity,
    localization_metrics,
    localization_opportunity,
    paired_delta,
    paired_localization_delta,
    protection_repair,
    row_responsible_step,
)
from evolution.registry import load_active_registry
from evolution.splits import bool_gold, load_gold, load_gold_step

PROMOTION_PROTOCOL_SCHEMA = "evolution.promotion_protocol.v1"
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
    for entry in entries:
        validate_intervention(entry, require_executable_bounds=True)
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
    unknown_roles = sorted(set(roles.values()) - {"benefit", "protection"})
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
        "batch_hash": fingerprint(json.loads(
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
        "detector_config_hash": fingerprint(config_payload),
        "code_baseline": source_digest(),
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
    save_json(path, protocol)
    return protocol


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
        "roll_hash": fingerprint(dict(payload)),
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
) -> dict[str, Any]:
    """Load a frozen protocol and check it against the current machinery.

    The checks are the ``freeze``-time promises, re-derived: the batch file
    still has the hash it was frozen with, the detector configuration is
    unchanged, and the roles still cover exactly the batch. A mismatch refuses
    the run instead of quietly measuring something else than what was frozen.
    """
    path = Path(path)
    if not path.is_file():
        raise PromotionProtocolError(
            f"promotion protocol not found: {str(path)!r}; a promotion run "
            "needs a protocol frozen before any model call"
        )
    protocol = json.loads(path.read_text(encoding="utf-8-sig"))
    if str(protocol.get("schema_version")) != PROMOTION_PROTOCOL_SCHEMA:
        raise PromotionProtocolError(
            f"promotion protocol schema must be {PROMOTION_PROTOCOL_SCHEMA}"
        )
    batch_path = Path(str(protocol.get("batch_path") or ""))
    if not batch_path.is_file():
        raise PromotionProtocolError(f"promotion batch is missing: {batch_path}")
    batch_payload = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    if fingerprint(batch_payload) != str(protocol.get("batch_hash") or ""):
        raise PromotionProtocolError(
            "promotion batch changed after it was frozen; refusing to measure "
            "a different case set than the frozen protocol describes"
        )
    if str(protocol.get("code_baseline") or "") != source_digest():
        raise PromotionProtocolError(
            "detector/evolution source changed after the promotion protocol was "
            "frozen; a frozen protocol is evaluated on the machinery it froze"
        )
    detector_config = Path(str(protocol.get("detector_config") or ""))
    if not detector_config.is_file():
        raise PromotionProtocolError(
            f"detector config is missing: {detector_config}"
        )
    config_payload = json.loads(detector_config.read_text(encoding="utf-8-sig"))
    if fingerprint(config_payload) != str(
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
        if fingerprint(dict(payload)) != spec.get("roll_hash"):
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

    # -- publication ------------------------------------------------------
    def _publish(self) -> dict[str, Any]:
        """Commit the admitted version and record it in registry and ledger.

        This is the only place the active registry is written. The ledger
        entry carries the candidate identity so a later round can attribute
        the deployed version to the candidate and protocol that produced it.
        """
        artifacts = [dict(entry) for entry in self.candidate.get("artifacts") or ()]
        payload = self.registry.write(
            [*self.registry.entries, *artifacts],
            cycle=int(self.protocol.get("cycle") or 0),
            reason=(
                "promotion: STRUCTURAL/HARMLESSNESS/BEHAVIOR/M passed on the "
                f"frozen promotion protocol {self.protocol.get('frozen_at')}"
            ),
            library_cfg=self.config.get("library") or {},
        )
        version = {
            "version_id": f"V{self.registry.state.get('cycle', 0)}-{self.candidate.get('content_hash')}",
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
        save_json(self.output_dir / "promotion.json", report)
        self.ledger.append("promotion_attempt", {
            "candidate_id": report.get("candidate_id"),
            "candidate_content_hash": report.get("candidate_content_hash"),
            "state": report.get("state"),
            "target_kind": report.get("target_kind"),
            "reason": report.get("terminal_reason"),
            "protocol": self.protocol.get("loaded_from"),
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
        protocol_path, dataset_root=dataset_root, stages=stages,
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
