"""Capability-gap backlog: an unexecutable failure family is a work item.

A stable failure family that the current action vocabulary cannot treat is a
measured statement about the harness, not a quiet round. Until now the engine
classified those families, wrote them into the cycle report, and dropped them:
the next round had to rediscover the same gap from scratch, and nothing in the
repository recorded that a capability was missing.

This store keeps gaps as first-class, deduplicated backlog items carrying the
reason and the treatment routes the worksheet already computed. It is
deliberately independent of the admission pipeline: recording a gap never
admits, rejects, or scores anything.

Gap identity is ``(failure_family, classification)`` without the per-cycle
mechanism hash suffix, so the same standing gap keeps one entry with an
occurrence count instead of producing one entry per round.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution.contracts import append_jsonl, read_jsonl, save_json

GAP_SCHEMA = "evolution.capability_gap.v1"
GAP_STORE_NAME = "gaps.jsonl"
GAP_SUMMARY_NAME = "gaps_summary.json"

# Why a family is not treatable. The first entry is the original reading: the
# action space has no route for this mechanism. The rest come from
# ``evolution.capability_planning`` and separate "no route exists" from "a
# route exists but the harness cannot use it" -- a distinction that was
# invisible while the store carried only the first kind, which is why the
# backlog stayed nearly empty.
#
# Identity is ``(failure_family, classification)``, so a gap that sharpens from
# ``capability_gap`` to ``semantic_judgment_missing`` becomes a second entry.
# That is intended: the later entry is a better name for the same standing gap,
# and the store keeps both so the refinement stays visible instead of
# overwriting the earlier reading.
GAP_REASONS = (
    "capability_gap",
    "route_exists_but_insufficient",
    "missing_information",
    "missing_representation",
    "semantic_judgment_missing",
    "control_flow_missing",
    "executor_missing",
    "missing_localization_operator",
    "localization_attribution_missing",
    "prompt_only",
)

# ``worksheet`` appends ``-P<hash>`` when it splits a family by predicate
# profile, and ``-M<hash>`` when it splits one by mechanism reading. The backlog
# tracks the underlying family, so those suffixes are removed before an identity
# is computed.
_PROFILE_SUFFIX = re.compile(r"-P[0-9a-f]{6,}$")
_MECHANISM_SUFFIX = re.compile(r"-M[0-9a-f]{6,}$")


def _profile_free_family(failure_family: str) -> str:
    """Family id without the per-split hash suffixes.

    Two suffixes can follow a coarse family: ``-P<hash>`` from the
    predicate-profile split and ``-M<hash>`` from the mechanism subgroup split.
    The backlog tracks the underlying family, so both are stripped -- a gap must
    not reopen every time a split sharpens its name for the same family.
    """
    family = str(failure_family or "").strip()
    # The mechanism split runs after the profile split, so a family carrying
    # both reads ``...-P<profile>-M<mechanism>``. Strip until stable rather than
    # in a fixed order, so either suffix order is handled instead of silently
    # leaving one behind and minting a second identity for one family.
    while True:
        stripped = _MECHANISM_SUFFIX.sub("", _PROFILE_SUFFIX.sub("", family))
        if stripped == family:
            break
        family = stripped
    return family.strip()


def gap_identity(failure_family: str, classification: str) -> str:
    """Stable backlog id for a gap, independent of cycle and family splits."""
    return f"{_profile_free_family(failure_family)}|{str(classification or '').strip()}"


def gap_record(
    *,
    cycle: int,
    failure_family: str,
    classification: str,
    reason: str,
    count: int = 0,
    sources: Sequence[str] = (),
    routes: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one gap observation as written by a cycle."""
    return {
        "schema_version": GAP_SCHEMA,
        "identity": gap_identity(failure_family, classification),
        "failure_family": str(failure_family or ""),
        "classification": str(classification or ""),
        "reason": str(reason or ""),
        "count": int(count),
        "sources": sorted({str(item) for item in sources}),
        "routes": sorted({str(item) for item in routes}),
        "cycle": int(cycle),
    }


def record_gaps(
    path: str | Path,
    *,
    cycle: int,
    gaps: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Append observations for this cycle's gaps and return the open backlog.

    One observation per gap per cycle, appended whenever the gap is *observed*
    -- not only when its content changes. Re-seeing a standing gap in a later
    round is itself the information the backlog exists to carry:
    ``occurrences`` is what :func:`open_gaps` ranks by and what the engine's
    ``recurring_capability_gaps`` gates on (``occurrences >= 2``). Comparing
    only reason/routes/count left a gap that persisted in exactly the same
    shape stuck at ``occurrences == 1`` forever, so the strongest memory signal
    the proposer gets could never fire.

    Identity still dedups the *entries*, and
    :func:`_already_recorded_for_cycle` keeps a resumed cycle's replay to one
    observation per cycle, so the store stays a backlog rather than becoming a
    per-cycle log with one row per round.
    """
    path = Path(path)
    appended: list[dict[str, Any]] = []
    for gap in gaps:
        identity = str(gap.get("identity") or gap_identity(
            str(gap.get("failure_family") or ""),
            str(gap.get("classification") or ""),
        ))
        if _already_recorded_for_cycle(path, identity, cycle):
            continue
        observation = gap_record(
            cycle=cycle,
            failure_family=str(gap.get("failure_family") or ""),
            classification=str(gap.get("classification") or ""),
            reason=str(gap.get("reason") or ""),
            count=int(gap.get("count") or 0),
            sources=gap.get("sources") or (),
            routes=gap.get("routes") or (),
        )
        append_jsonl(path, {**observation, "identity": identity})
        appended.append(observation)
    return open_gaps(path)


def _already_recorded_for_cycle(path: Path, identity: str, cycle: int) -> bool:
    return any(
        record.get("identity") == identity
        and int(record.get("cycle") or -1) == int(cycle)
        and record.get("kind") != "resolution"
        for record in load_gap_observations(path)
    )


def load_gap_observations(path: str | Path) -> list[dict[str, Any]]:
    """Every recorded observation, in append order, schema-checked."""
    records = read_jsonl(path)
    for number, record in enumerate(records, start=1):
        schema = record.get("schema_version")
        if schema != GAP_SCHEMA:
            raise ValueError(
                f"gap store record {number} has unsupported schema {schema!r}"
            )
    return records


def load_gap_state(path: str | Path) -> dict[str, dict[str, Any]]:
    """Collapse observations into one entry per identity.

    ``first_seen_cycle``/``last_seen_cycle`` and ``occurrences`` come from the
    observation history; the remaining fields describe the latest observation.
    """
    state: dict[str, dict[str, Any]] = {}
    for record in load_gap_observations(path):
        if record.get("kind") == "resolution":
            continue
        identity = str(record["identity"])
        current = state.get(identity)
        if current is None:
            state[identity] = {
                **record,
                "first_seen_cycle": int(record["cycle"]),
                "last_seen_cycle": int(record["cycle"]),
                "occurrences": 1,
                "resolved_cycle": None,
                "resolution": None,
            }
            continue
        current["last_seen_cycle"] = int(record["cycle"])
        current["occurrences"] = int(current["occurrences"]) + 1
        for key, value in record.items():
            if key not in {"first_seen_cycle", "last_seen_cycle", "occurrences"}:
                current[key] = value
    for record in load_gap_resolutions(path):
        identity = str(record.get("identity") or "")
        if identity in state:
            state[identity]["resolved_cycle"] = int(record["cycle"])
            state[identity]["resolution"] = str(record.get("note") or "")
    return state


def resolve_gap(
    path: str | Path,
    *,
    identity: str,
    cycle: int,
    note: str,
) -> dict[str, Any]:
    """Close a gap: the missing capability now exists.

    Resolution is recorded as history rather than by deleting the observation,
    so a gap that was once open stays explainable after it is handled.
    """
    state = load_gap_state(path)
    if identity not in state:
        raise ValueError(f"cannot resolve unknown capability gap {identity!r}")
    record = {
        "schema_version": GAP_SCHEMA,
        "kind": "resolution",
        "identity": identity,
        "cycle": int(cycle),
        "note": str(note or ""),
    }
    append_jsonl(path, record)
    return record


def load_gap_resolutions(path: str | Path) -> list[dict[str, Any]]:
    return [
        record for record in load_gap_observations(path)
        if record.get("kind") == "resolution"
    ]


def open_gaps(path: str | Path) -> list[dict[str, Any]]:
    """Backlog entries still awaiting a capability, most frequent first."""
    entries = [
        {key: value for key, value in record.items() if key != "kind"}
        for record in load_gap_state(path).values()
        if record.get("resolved_cycle") is None
    ]
    entries.sort(
        key=lambda item: (
            -int(item.get("occurrences") or 0),
            str(item.get("identity") or ""),
        )
    )
    return entries


def gap_summary(path: str | Path) -> dict[str, Any]:
    state = load_gap_state(path)
    open_entries = open_gaps(path)
    by_classification: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for entry in open_entries:
        classification = str(entry.get("classification") or "")
        by_classification[classification] = by_classification.get(classification, 0) + 1
        reason = str(entry.get("reason") or "")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "schema_version": GAP_SCHEMA,
        "recorded": len(state),
        "open": len(open_entries),
        "resolved": sum(
            1 for item in state.values() if item.get("resolved_cycle") is not None
        ),
        "by_classification": by_classification,
        "by_reason": by_reason,
        "observations": len(load_gap_observations(path)),
    }


def write_gap_summary(path: str | Path, *, summary_path: str | Path) -> dict[str, Any]:
    summary = gap_summary(path)
    save_json(summary_path, summary)
    return summary


__all__ = [
    "GAP_SCHEMA",
    "GAP_STORE_NAME",
    "GAP_SUMMARY_NAME",
    "gap_identity",
    "gap_record",
    "gap_summary",
    "load_gap_observations",
    "load_gap_resolutions",
    "load_gap_state",
    "open_gaps",
    "record_gaps",
    "resolve_gap",
    "write_gap_summary",
]
