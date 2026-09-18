"""Three experience stores: episodic, procedural, improvement.

The design these implement separates three kinds of memory, because they are
retrieved differently and mean different things:

* **episodic** — what happened in a specific round: which family failed, what
  was proposed, which gate rejected it and why. Used for diagnosis and for
  recalling prior attempts so the same rejected mechanism is not re-derived.
* **procedural** — how to do something: which treatment route, on what kind of
  family, worked, plus the failure boundary observed with it. This is the store
  an improver should read before choosing a patch shape.
* **improvement** — which bottleneck class responded to which kind of patch.
  This is knowledge about the search itself, not about a case.

Two rules keep these from degenerating into a log:

1. Nothing is recorded until it has real content. An "attempt" is only
   procedural when it actually passed a gate; a rejection that was merely
   unevaluable (no repair opportunity in the marginal set) is recorded as
   such rather than as a falsified mechanism.
2. Retrieval is filtered and capped. Every retrieval takes a task/failure
   mechanism and returns a small number of entries, so a growing store does
   not silently become a growing prompt.

Recording is deliberately decoupled from admission: these functions never
score, admit, or reject anything.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution.contracts import append_jsonl, read_jsonl, save_json

EPISODIC_SCHEMA = "evolution.experience.episodic.v1"
PROCEDURAL_SCHEMA = "evolution.experience.procedural.v1"
IMPROVEMENT_SCHEMA = "evolution.experience.improvement.v1"

EPISODIC_NAME = "episodic.jsonl"
PROCEDURAL_NAME = "procedural.jsonl"
IMPROVEMENT_NAME = "improvement.jsonl"
RECALL_NAME = "recall.json"
CORRECTION_NAME = "episode_corrections.jsonl"
CORRECTION_SCHEMA = "evolution.experience.correction.v1"

DEFAULT_RECALL_LIMIT = 3


# ----------------------------------------------------------------------
# Store paths
# ----------------------------------------------------------------------
def memory_paths(root: str | Path) -> dict[str, Path]:
    root = Path(root)
    return {
        "root": root,
        "episodic": root / EPISODIC_NAME,
        "procedural": root / PROCEDURAL_NAME,
        "improvement": root / IMPROVEMENT_NAME,
        "recall": root / RECALL_NAME,
        "corrections": root / CORRECTION_NAME,
    }


def _ensure(root: str | Path) -> dict[str, Path]:
    paths = memory_paths(root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    return paths


def _logical_key(record: Mapping[str, Any], fields: Sequence[str]) -> tuple[Any, ...]:
    return tuple(record.get(field) for field in fields)


def _already_present(
    path: str | Path,
    key: tuple[Any, ...],
    fields: Sequence[str],
) -> bool:
    """Whether an equivalent record already exists.

    A resumed cycle replays its writes. Without this check a resume would
    inflate occurrence counts and make the stores describe a run that never
    happened.
    """
    return any(
        _logical_key(record, fields) == key for record in read_jsonl(path)
    )


# ----------------------------------------------------------------------
# Episodic
# ----------------------------------------------------------------------
_EPISODE_KEY_FIELDS = ("cycle", "intervention_id", "outcome")


def record_episode(
    root: str | Path,
    *,
    cycle: int,
    failure_family: str,
    polarity: str,
    outcome: str,
    gate: str | None = None,
    reason: str = "",
    intervention_id: str = "",
    intervention_type: str = "",
    hook: str = "",
    treatment_routes: Sequence[str] = (),
    unevaluable: bool = False,
) -> dict[str, Any]:
    """Record one attempt as it happened.

    ``outcome`` is the realised result (``admitted``/``rejected``/``audit``),
    ``gate`` names the first gate that decided, and ``unevaluable`` marks a
    rejection that came from missing evaluation coverage rather than from a
    falsified mechanism. Keeping those two apart is the whole point: a
    rejection that means "not measured" must not suppress the mechanism as if
    it had been refuted.
    """
    paths = _ensure(root)
    record = {
        "schema_version": EPISODIC_SCHEMA,
        "cycle": int(cycle),
        "failure_family": str(failure_family or ""),
        "polarity": str(polarity or ""),
        "outcome": str(outcome or ""),
        "gate": (str(gate) if gate else None),
        "reason": str(reason or ""),
        "intervention_id": str(intervention_id or ""),
        "intervention_type": str(intervention_type or ""),
        "hook": str(hook or ""),
        "treatment_routes": sorted({str(item) for item in treatment_routes}),
        "unevaluable": bool(unevaluable),
    }
    if _already_present(paths["episodic"], _logical_key(record, _EPISODE_KEY_FIELDS), _EPISODE_KEY_FIELDS):
        return record
    append_jsonl(paths["episodic"], record)
    return record


def episode_digest(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(record), sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def record_episode_correction(
    root: str | Path, original: Mapping[str, Any], *,
    unevaluable: bool, reason: str, evidence: str,
) -> dict[str, Any]:
    """Append a reviewed reclassification of an exact original episode.

    Hash binding prevents reuse across another run with the same cycle/id.
    Original observations, admission decisions and ledger entries are untouched.
    """
    paths = _ensure(root)
    target = episode_digest(original)
    if not any(episode_digest(row) == target for row in read_jsonl(paths["episodic"])):
        raise ValueError("correction target is not an original episode in this store")
    if not reason.strip() or not evidence.strip():
        raise ValueError("episode correction requires a reason and evidence")
    correction = {"schema_version": CORRECTION_SCHEMA, "target_digest": target,
                  "unevaluable": bool(unevaluable), "reason": reason, "evidence": evidence}
    if correction not in read_jsonl(paths["corrections"]):
        append_jsonl(paths["corrections"], correction)
    return correction


def load_episodes(root: str | Path) -> list[dict[str, Any]]:
    records = read_jsonl(memory_paths(root)["episodic"])
    for number, record in enumerate(records, start=1):
        schema = record.get("schema_version")
        if schema != EPISODIC_SCHEMA:
            raise ValueError(
                f"episodic store record {number} has unsupported schema {schema!r}"
            )
    corrections = {}
    for correction in read_jsonl(memory_paths(root)["corrections"]):
        if correction.get("schema_version") != CORRECTION_SCHEMA:
            raise ValueError("unsupported episode correction schema")
        corrections[correction["target_digest"]] = correction
    for record in records:
        correction = corrections.get(episode_digest(record))
        if correction:
            record["original_reason"] = record.get("reason", "")
            record["reason"] = correction["reason"]
            record["unevaluable"] = correction["unevaluable"]
            record["correction_evidence"] = correction["evidence"]
    return records


def episodic_attempts(root: str | Path) -> list[dict[str, Any]]:
    """Every attempt, with the falsified-versus-unevaluable distinction applied.

    ``falsified`` is True only when a gate rejected for a substantive reason.
    Consumers that want to suppress repeats must key on ``falsified``; keying
    on "was rejected" would also suppress mechanisms that were never measured.
    """
    attempts = []
    for record in load_episodes(root):
        attempts.append({
            **record,
            "falsified": (
                record.get("outcome") == "rejected"
                and not bool(record.get("unevaluable"))
            ),
        })
    return attempts


# ----------------------------------------------------------------------
# Procedural
# ----------------------------------------------------------------------
def record_procedure(
    root: str | Path,
    *,
    cycle: int,
    failure_family: str,
    polarity: str,
    treatment_route: str,
    intervention_type: str,
    hook: str,
    intervention_id: str,
    expected_behavior: str = "",
    failure_boundary: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a treatment route that actually cleared the gates.

    Only call this for admitted interventions: a procedure is a claim about
    how to do something, and an attempt that no gate accepted has not yet
    established that. ``failure_boundary`` is where the procedure was observed
    not to hold, which is what lets a later round narrow applicability instead
    of rediscovering the boundary.
    """
    paths = _ensure(root)
    record = {
        "schema_version": PROCEDURAL_SCHEMA,
        "cycle": int(cycle),
        "failure_family": str(failure_family or ""),
        "polarity": str(polarity or ""),
        "treatment_route": str(treatment_route or ""),
        "intervention_type": str(intervention_type or ""),
        "hook": str(hook or ""),
        "intervention_id": str(intervention_id or ""),
        "expected_behavior": str(expected_behavior or ""),
        "failure_boundary": str(failure_boundary or ""),
        "evidence": dict(evidence or {}),
    }
    if _already_present(paths["procedural"], _logical_key(record, ("cycle", "intervention_id")), ("cycle", "intervention_id")):
        return record
    append_jsonl(paths["procedural"], record)
    return record


def load_procedures(root: str | Path) -> list[dict[str, Any]]:
    records = read_jsonl(memory_paths(root)["procedural"])
    for number, record in enumerate(records, start=1):
        schema = record.get("schema_version")
        if schema != PROCEDURAL_SCHEMA:
            raise ValueError(
                f"procedural store record {number} has unsupported schema {schema!r}"
            )
    return records


# ----------------------------------------------------------------------
# Improvement
# ----------------------------------------------------------------------
def record_improvement(
    root: str | Path,
    *,
    cycle: int,
    bottleneck: str,
    patch_kind: str,
    outcome: str,
    note: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record which bottleneck class did or did not respond to which patch kind.

    ``bottleneck`` is the diagnosed problem class (for example an
    unexecutable-family reason), ``patch_kind`` the shape of the attempted
    change, and ``outcome`` whether it worked. This is the store that lets a
    later round skip a search direction already known to be unproductive.
    """
    paths = _ensure(root)
    record = {
        "schema_version": IMPROVEMENT_SCHEMA,
        "cycle": int(cycle),
        "bottleneck": str(bottleneck or ""),
        "patch_kind": str(patch_kind or ""),
        "outcome": str(outcome or ""),
        "note": str(note or ""),
        "evidence": dict(evidence or {}),
    }
    key = (record["cycle"], record["bottleneck"], record["patch_kind"], record["outcome"])
    fields = ("cycle", "bottleneck", "patch_kind", "outcome")
    if _already_present(paths["improvement"], key, fields):
        return record
    append_jsonl(paths["improvement"], record)
    return record


def load_improvements(root: str | Path) -> list[dict[str, Any]]:
    records = read_jsonl(memory_paths(root)["improvement"])
    for number, record in enumerate(records, start=1):
        schema = record.get("schema_version")
        if schema != IMPROVEMENT_SCHEMA:
            raise ValueError(
                f"improvement store record {number} has unsupported schema {schema!r}"
            )
    return records


# ----------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------
def recall(
    root: str | Path,
    *,
    failure_family: str = "",
    polarity: str = "",
    treatment_routes: Sequence[str] = (),
    bottleneck: str = "",
    limit: int = DEFAULT_RECALL_LIMIT,
) -> dict[str, Any]:
    """Retrieve a small, filtered set of relevant experience.

    Relevance is deliberately simple and inspectable: an entry matches on the
    family, its polarity, or an overlapping treatment route. Results are ranked
    by match strength and recency, then capped, so a large store yields a
    bounded prompt rather than an ever-growing one.
    """
    routes = {str(item) for item in treatment_routes}
    matches: list[tuple[int, int, dict[str, Any]]] = []

    def score(record: Mapping[str, Any]) -> int:
        points = 0
        if failure_family and str(record.get("failure_family") or "") == failure_family:
            points += 4
        if polarity and str(record.get("polarity") or "") == polarity:
            points += 2
        if routes & set(record.get("treatment_routes") or ()):
            points += 3
        if treatment_routes and str(record.get("treatment_route") or "") in routes:
            points += 3
        if bottleneck and str(record.get("bottleneck") or "") == bottleneck:
            points += 4
        return points

    for record in episodic_attempts(root):
        points = score(record)
        if points:
            matches.append((points, int(record.get("cycle") or 0), record))
    for record in load_procedures(root):
        points = score(record)
        if points:
            matches.append((points, int(record.get("cycle") or 0), record))
    for record in load_improvements(root):
        points = score(record)
        if points:
            matches.append((points, int(record.get("cycle") or 0), record))

    matches.sort(key=lambda item: (-item[0], -item[1]))
    selected = [dict(record) for _points, _cycle, record in matches[: max(0, limit)]]

    falsified_ids = {
        str(record.get("intervention_id") or "")
        for record in episodic_attempts(root)
        if record.get("falsified") and record.get("intervention_id")
    }
    unevaluable_ids = {
        str(record.get("intervention_id") or "")
        for record in episodic_attempts(root)
        if record.get("unevaluable") and record.get("intervention_id")
    }
    result = {
        "matched": len(matches),
        "returned": len(selected),
        "entries": selected,
        # Falsified mechanisms are suppressed for identical re-proposal;
        # unevaluable ones are explicitly NOT, because they were never judged.
        "falsified_intervention_ids": sorted(falsified_ids - unevaluable_ids),
        "unevaluable_intervention_ids": sorted(unevaluable_ids),
    }
    return result


def write_recall(
    root: str | Path,
    payload: Mapping[str, Any],
    *,
    name: str = RECALL_NAME,
) -> dict[str, Any]:
    paths = _ensure(root)
    save_json(paths["root"] / name, dict(payload))
    return dict(payload)


def memory_summary(root: str | Path) -> dict[str, Any]:
    episodes = episodic_attempts(root)
    return {
        "episodic": {
            "records": len(episodes),
            "falsified": sum(1 for item in episodes if item["falsified"]),
            "unevaluable": sum(1 for item in episodes if item["unevaluable"]),
            "admitted": sum(1 for item in episodes if item["outcome"] == "admitted"),
        },
        "procedural": {"records": len(load_procedures(root))},
        "improvement": {"records": len(load_improvements(root))},
    }


__all__ = [
    "DEFAULT_RECALL_LIMIT",
    "EPISODIC_NAME",
    "EPISODIC_SCHEMA",
    "IMPROVEMENT_NAME",
    "IMPROVEMENT_SCHEMA",
    "PROCEDURAL_NAME",
    "PROCEDURAL_SCHEMA",
    "RECALL_NAME",
    "episodic_attempts",
    "load_episodes",
    "load_improvements",
    "load_procedures",
    "memory_paths",
    "memory_summary",
    "recall",
    "record_episode",
    "record_improvement",
    "record_procedure",
    "write_recall",
]
