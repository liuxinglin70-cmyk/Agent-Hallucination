"""Two stores the framework was missing: candidate history and reusable parts.

Plan §9.1 separates three layers, and only the third existed:

1. **Candidate archive** -- every development candidate, *including the ones
   that failed*, with the reason it failed. Its job is to stop the framework
   paying twice for the same dead end: without a cross-mechanism record, "we
   already tried a tracer directive on this mechanism and it changed nothing"
   lives only in one run directory's files, if at all.
2. **Reusable development components** -- parts that were locally shown useful
   but have *not* become terminal behaviour.
3. **Active Registry** -- only what passed promotion.

The separation is the point, and this module keeps it mechanically: nothing here
can write to the registry. ``record_component`` cannot promote, and a component
carries no admission claim -- it records that a part worked well enough to be
worth reusing, not that it is correct.

What a component *is*, in this framework: a validated typed-DSL predicate
program. That is the one object with a stable digest, a closed vocabulary, no
free-form code, and two consumers (``evidence_check`` observes with it,
``recall_trigger`` routes with it), which is exactly the "extractor / parser /
semantic executor" role the plan describes. Prose directives are deliberately
not components: they have no digest and no composition, so "reuse" would mean
copying text, not sharing a part.

Everything is offline and append-only. Retrieval is bounded and inspectable,
in the same spirit as ``memory.recall``: a large store must yield a small
prompt, not an ever-growing one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.predicate_dsl import predicate_program_hash, validate_predicate_program
from evolution.contracts import append_jsonl, read_jsonl, save_json

CANDIDATE_ARCHIVE_SCHEMA = "evolution.candidate_archive.v1"
COMPONENT_SCHEMA = "evolution.reusable_component.v1"

CANDIDATE_STORE_NAME = "candidates.jsonl"
COMPONENT_STORE_NAME = "components.jsonl"
ARCHIVE_SUMMARY_NAME = "archive_summary.json"

# Component kinds, in the plan's vocabulary. Every one of them is realised by a
# typed-DSL program here; the kind says what role the program plays, which is
# what retrieval matches on.
COMPONENT_KINDS = (
    "extractor",
    "evidence_formatter",
    "parser",
    "semantic_executor",
    "localization_helper",
)

DEFAULT_RETRIEVAL_LIMIT = 5


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()


def _paths(root: str | Path) -> dict[str, Path]:
    base = Path(root)
    return {
        "root": base,
        "candidates": base / CANDIDATE_STORE_NAME,
        "components": base / COMPONENT_STORE_NAME,
        "summary": base / ARCHIVE_SUMMARY_NAME,
    }


def _ensure(root: str | Path) -> dict[str, Path]:
    paths = _paths(root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    return paths


# ----------------------------------------------------------------------
# Candidate archive
# ----------------------------------------------------------------------
def archive_candidate(
    root: str | Path,
    *,
    cycle: int,
    mechanism_id: str,
    family_id: str = "",
    candidate_hash: str,
    version_id: str = "",
    run_dir: str = "",
    action_class: str = "",
    intervention_type: str = "",
    hook: str = "",
    status: str = "",
    target_kind: str = "detection",
    failure_reason: str = "",
    reason_codes: Sequence[str] = (),
    parent_version_id: str = "",
    replay_boundary_key: str = "",
) -> dict[str, Any]:
    """Record one development candidate, admitted or not.

    ``status`` and ``failure_reason`` carry equal weight here: an archive that
    only kept successes could not answer the question it exists for. ``status``
    stays a free string because the version vocabulary belongs to
    ``candidate_versions`` and duplicating it here would let the two drift;
    ``reason_codes`` is the machine-readable half, and callers pass whatever
    codes the loop already produced.

    Deduplicated on ``(mechanism_id, candidate_hash)``: the same artifact
    reached through two runs is one candidate, and re-recording it would inflate
    every "how often did this fail" count.
    """
    if not str(candidate_hash or "").strip():
        raise ValueError("an archived candidate needs its content hash")
    paths = _ensure(root)
    record = {
        "schema_version": CANDIDATE_ARCHIVE_SCHEMA,
        "cycle": int(cycle),
        "mechanism_id": str(mechanism_id or ""),
        "family_id": str(family_id or ""),
        "candidate_hash": str(candidate_hash),
        "version_id": str(version_id or ""),
        "run_dir": str(run_dir or ""),
        "action_class": str(action_class or ""),
        "intervention_type": str(intervention_type or ""),
        "hook": str(hook or ""),
        "status": str(status or ""),
        "target_kind": str(target_kind or "detection"),
        "failure_reason": str(failure_reason or ""),
        "reason_codes": sorted({str(code) for code in reason_codes if str(code)}),
        "parent_version_id": str(parent_version_id or ""),
        "replay_boundary_key": str(replay_boundary_key or ""),
    }
    existing = load_candidates(root)
    for item in existing:
        if (
            item.get("mechanism_id") == record["mechanism_id"]
            and item.get("candidate_hash") == record["candidate_hash"]
        ):
            return item
    append_jsonl(paths["candidates"], record)
    return record


def load_candidates(root: str | Path) -> list[dict[str, Any]]:
    records = read_jsonl(_paths(root)["candidates"])
    for number, record in enumerate(records, start=1):
        if record.get("schema_version") != CANDIDATE_ARCHIVE_SCHEMA:
            raise ValueError(
                f"candidate archive record {number} has unsupported schema "
                f"{record.get('schema_version')!r}"
            )
    return records


def candidates_for_mechanism(
    root: str | Path, mechanism_id: str,
) -> list[dict[str, Any]]:
    wanted = str(mechanism_id or "")
    return [
        record for record in load_candidates(root)
        if record.get("mechanism_id") == wanted
    ]


def repeated_failure_reasons(
    root: str | Path, mechanism_id: str = "",
) -> dict[str, Any]:
    """Which failure reasons recur, so a loop need not rediscover them.

    Scoped to one mechanism when an id is given: a reason that recurs across
    mechanisms says something different (a general capability gap) from one
    that recurs on the same mechanism (a route that cannot repair it), and
    merging the two would report neither.
    """
    records = load_candidates(root)
    if mechanism_id:
        records = [
            record for record in records
            if record.get("mechanism_id") == str(mechanism_id)
        ]
    counts: dict[str, int] = {}
    for record in records:
        # A set per record: ``reason_codes`` is the machine-readable half and
        # ``failure_reason`` the prose half, and a caller that passes the same
        # string for both must not have one failure counted twice. Counting is
        # per candidate, so the number means "how many candidates hit this",
        # which is the question a caller repeating a dead end is asking.
        reasons = {str(code) for code in record.get("reason_codes") or () if code}
        prose = str(record.get("failure_reason") or "").strip()
        if prose:
            reasons.add(prose)
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
    repeated = {
        reason: count for reason, count in counts.items() if count > 1
    }
    return {
        "mechanism_id": str(mechanism_id or ""),
        "candidates": len(records),
        "reasons": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "repeated": dict(sorted(repeated.items(), key=lambda item: (-item[1], item[0]))),
    }


# ----------------------------------------------------------------------
# Reusable components
# ----------------------------------------------------------------------
def _component_canonical(program: Mapping[str, Any]) -> dict[str, Any]:
    """The validated program, canonically, so two spellings share a digest."""
    return validate_predicate_program(dict(program))


def component_digest(program: Mapping[str, Any]) -> str:
    """Stable content digest of one reusable program."""
    return predicate_program_hash(_component_canonical(program))


def record_component(
    root: str | Path,
    *,
    name: str,
    kind: str,
    program: Mapping[str, Any],
    cycle: int,
    mechanism_id: str = "",
    source_candidate_hash: str = "",
    capability: str = "",
    verdicts: Sequence[str] = (),
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Register a part that was locally useful but is not terminal behaviour.

    Refuses a program whose declared verdicts disagree with its own rules, and
    refuses an unknown ``kind``: retrieval matches on the kind, so an
    unclassified part could never be found and would only pad the store.
    """
    kind = str(kind or "")
    if kind not in COMPONENT_KINDS:
        raise ValueError(
            f"unknown component kind {kind!r}; use one of {sorted(COMPONENT_KINDS)}"
        )
    if not str(name or "").strip():
        raise ValueError("a reusable component needs a name")
    canonical = _component_canonical(program)
    if str(canonical.get("name") or "") != str(name):
        raise ValueError(
            f"component name {name!r} must equal program.name "
            f"{canonical.get('name')!r}: retrieval and the executor would "
            "otherwise disagree about what this part is"
        )
    declared = sorted({str(item) for item in verdicts}) or sorted(
        str(item) for item in canonical.get("verdicts") or ()
    )
    paths = _ensure(root)
    record = {
        "schema_version": COMPONENT_SCHEMA,
        "cycle": int(cycle),
        "name": str(name),
        "kind": kind,
        "digest": component_digest(canonical),
        "mechanism_id": str(mechanism_id or ""),
        "source_candidate_hash": str(source_candidate_hash or ""),
        "capability": str(capability or ""),
        "verdicts": declared,
        "conviction_verdicts": sorted(
            str(item) for item in canonical.get("conviction_verdicts") or ()
        ),
        "program": canonical,
        "evidence": dict(evidence or {}),
    }
    # Deduplicated on (kind, program digest), not the digest alone. The kind is
    # an input to retrieval, so the same program registered under a second role
    # is a second reachable part; collapsing it into the first would leave the
    # caller unable to find the role it just declared.
    for item in load_components(root):
        if item.get("digest") == record["digest"] and item.get("kind") == kind:
            return item
    append_jsonl(paths["components"], record)
    return record


def load_components(root: str | Path) -> list[dict[str, Any]]:
    records = read_jsonl(_paths(root)["components"])
    for number, record in enumerate(records, start=1):
        if record.get("schema_version") != COMPONENT_SCHEMA:
            raise ValueError(
                f"component store record {number} has unsupported schema "
                f"{record.get('schema_version')!r}"
            )
    return records


def retrieve_components(
    root: str | Path,
    *,
    mechanism_id: str = "",
    capability: str = "",
    kind: str = "",
    limit: int = DEFAULT_RETRIEVAL_LIMIT,
) -> dict[str, Any]:
    """A bounded, ranked set of reusable parts for a new candidate.

    Ranking is deliberately simple and inspectable, mirroring ``memory.recall``:
    an exact mechanism match ranks above a capability match, which ranks above
    a kind match. Parts from *other* mechanisms are still offered -- a parser
    that reads `required_action_present` is reusable regardless of which
    mechanism first needed it -- but they rank below the mechanism's own, so a
    bounded prompt keeps the most relevant entries.
    """
    wanted_kind = str(kind or "")
    if wanted_kind and wanted_kind not in COMPONENT_KINDS:
        raise ValueError(f"unknown component kind {wanted_kind!r}")
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for record in load_components(root):
        points = 0
        if mechanism_id and record.get("mechanism_id") == str(mechanism_id):
            points += 4
        if capability and record.get("capability") == str(capability):
            points += 3
        if wanted_kind and record.get("kind") == wanted_kind:
            points += 2
        if points:
            matches.append((points, int(record.get("cycle") or 0), record))
    matches.sort(key=lambda item: (-item[0], -item[1], str(item[2].get("name"))))
    selected = [dict(record) for _points, _cycle, record in matches[: max(0, limit)]]
    return {
        "matched": len(matches),
        "returned": len(selected),
        "components": selected,
        # Named so a reader can see the store is larger than the prompt, and
        # that the cap is a retrieval decision rather than an empty store.
        "store_size": len(load_components(root)),
    }


def component_prompt_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """One component as proposer-facing context: the part, not its provenance.

    The program is included because the proposer has to *reuse* it, and a
    program cannot be reused from a name alone. No case identifiers appear:
    components are documented by what they compute.
    """
    return {
        "name": str(record.get("name") or ""),
        "kind": str(record.get("kind") or ""),
        "digest": str(record.get("digest") or ""),
        "capability": str(record.get("capability") or ""),
        "verdicts": list(record.get("verdicts") or ()),
        "conviction_verdicts": list(record.get("conviction_verdicts") or ()),
        "description": str((record.get("program") or {}).get("description") or ""),
        "program": dict(record.get("program") or {}),
    }


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------
def archive_report(
    root: str | Path, *, component_root: str | Path | None = None,
) -> dict[str, Any]:
    """A compact view of both stores, for a cycle report or a researcher.

    ``component_root`` exists because the two stores have two writers with two
    configured paths: the candidate half is written where ``archive.root``
    says, and the component half where the component store lives (which is the
    same directory by default). A summary that read both from one of them would
    report an empty component half whenever an operator split the two -- the
    same "wrote one place, read another" mistake, one level up.
    """
    candidates = load_candidates(root)
    components = load_components(
        root if component_root is None else component_root
    )
    by_status: dict[str, int] = {}
    for record in candidates:
        status = str(record.get("status") or "(unstated)")
        by_status[status] = by_status.get(status, 0) + 1
    by_kind: dict[str, int] = {}
    for record in components:
        kind = str(record.get("kind") or "(unstated)")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "schema_version": CANDIDATE_ARCHIVE_SCHEMA,
        "metric_role": "diagnostic-only",
        "candidates": {
            "total": len(candidates),
            "by_status": dict(sorted(by_status.items())),
            "mechanisms": sorted({
                str(record.get("mechanism_id") or "")
                for record in candidates if record.get("mechanism_id")
            }),
        },
        "components": {
            "total": len(components),
            "by_kind": dict(sorted(by_kind.items())),
            "kinds_available": list(COMPONENT_KINDS),
        },
        "note": (
            "Candidate history and reusable parts. Neither store can admit a "
            "version: only the promotion protocol writes the Active Registry, "
            "and a component records local usefulness, not correctness."
        ),
    }


def record_archive_summary(
    root: str | Path, *, component_root: str | Path | None = None,
) -> dict[str, Any]:
    """Write the summary for one root, reading each store from its own root."""
    paths = _ensure(root)
    report = archive_report(root, component_root=component_root)
    save_json(paths["summary"], report)
    return report


def ingest_development_run(
    root: str | Path,
    run_dir: str | Path,
    *,
    cycle: int,
    mechanism_id: str = "",
    family_id: str = "",
) -> dict[str, Any]:
    """Archive every version of one development run, pass or fail.

    Reads the run's own records rather than the caller's summary of them: the
    per-version status lives in the sidecar the loop wrote, and trusting a
    caller to restate it is how the archive would start disagreeing with the
    loop it is supposed to remember.
    """
    from evolution.candidate_versions import CandidateVersionGraph

    graph = CandidateVersionGraph(Path(run_dir))
    graph.load_existing()
    recorded: list[dict[str, Any]] = []
    for version in graph.versions:
        version_id = str(version.get("version_id") or "")
        sidecar = graph.development_status(version_id)
        candidate = version.get("candidate") or {}
        if not isinstance(candidate, Mapping):
            candidate = {}
        # The version record is the authority on identity; the sidecar carries
        # the verdicts. Both are read from disk rather than restated by the
        # caller, so the archive cannot drift from the loop it remembers.
        candidate_hash = str(version.get("candidate_hash") or "")
        if not candidate_hash:
            # A version with no hash (an empty draft) has nothing to identify,
            # and archiving it under a placeholder would collide with the next
            # empty draft.
            continue
        l1 = (sidecar.get("l1") or {})
        reasons = [
            str(reason.get("note") or reason) if isinstance(reason, Mapping) else str(reason)
            for reason in sidecar.get("revision_reason") or () if reason
        ]
        recorded.append(archive_candidate(
            root,
            cycle=int(cycle),
            mechanism_id=str(mechanism_id or sidecar.get("mechanism_id") or ""),
            family_id=str(family_id or ""),
            candidate_hash=candidate_hash,
            version_id=version_id,
            run_dir=str(run_dir),
            action_class=str(sidecar.get("action_class") or ""),
            intervention_type=str(candidate.get("type") or ""),
            hook=str(candidate.get("hook") or ""),
            status=str(sidecar.get("status") or ""),
            target_kind=str(candidate.get("target_kind") or "detection"),
            failure_reason="; ".join(l1.get("reasons") or reasons),
            reason_codes=[
                str(code) for code in (l1.get("reason_codes") or ()) if code
            ],
            parent_version_id=str(sidecar.get("parent_version_id") or ""),
        ))
    return {
        "versions": len(graph.versions),
        "recorded": len(recorded),
        "report": archive_report(root),
    }


__all__ = [
    "CANDIDATE_ARCHIVE_SCHEMA",
    "CANDIDATE_STORE_NAME",
    "COMPONENT_KINDS",
    "COMPONENT_SCHEMA",
    "COMPONENT_STORE_NAME",
    "archive_candidate",
    "archive_report",
    "candidates_for_mechanism",
    "component_digest",
    "component_prompt_view",
    "ingest_development_run",
    "load_candidates",
    "load_components",
    "record_archive_summary",
    "record_component",
    "repeated_failure_reasons",
    "retrieve_components",
]
