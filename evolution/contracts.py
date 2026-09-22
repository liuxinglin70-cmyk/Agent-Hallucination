"""Evolution-side intervention contracts.

All schema validation and hashing are delegated to ``detector.interventions``
so a deployable payload can never drift from the executor that actually runs
it. The evolution package only adds STRUCTURAL-facing text/claim helpers.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from detector.interventions import (
    RUNNER_REGISTRY,
    build_intervention_registry,
    intervention_content_hash,
    load_intervention_registry,
    validate_intervention,
)
from detector.predicate_dsl import PROGRAM_RUNNER, predicate_program_hash

_CASE_ID_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_-]{0,40}(?:/|__)\d{3}\.json\b",
    re.IGNORECASE,
)

# Runners whose executable identity is fully described by the entry itself: the
# declared predicate program *is* the behaviour. Any other runner reads
# something the entry does not contain -- a model, a tokenizer, a calibration
# file -- so its identity has to carry that artifact.
#
# Derived from ``RUNNER_REGISTRY`` rather than hand-listed. The list used to be
# a literal here with a comment saying it would be replaced by a lookup once
# P2.1 landed; leaving it would mean a runner could be registered with
# ``requires_artifact=False`` and still be treated as artifact-reading here, or
# the reverse -- two authorities on one question, which is how the two silently
# disagree.
DETERMINISTIC_RUNNERS = tuple(
    sorted(
        name for name, spec in RUNNER_REGISTRY.items()
        if not spec.requires_artifact and not spec.learned
    )
)

# Where an entry declares the artifact its runner depends on. ``id`` names the
# artifact, ``digest`` pins its content. The *path* is deliberately not part of
# the identity: the same weights at a different path are the same intervention,
# and the digest is what says so.
ARTIFACT_FIELDS = ("id", "digest")


def empty_registry() -> dict[str, Any]:
    return build_intervention_registry([])


def make_registry(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return build_intervention_registry(entries)


def make_intervention(
    *,
    intervention_id: str,
    intervention_type: str,
    hook: str,
    packet_features: Sequence[str],
    body: Mapping[str, Any],
    expected_behavior: str,
    target_polarity: str,
    cycle: int,
    failure_family: str,
    failure_cases: Sequence[str],
    facts: Sequence[str] | None = None,
    artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "detector.intervention.v1",
        "intervention_id": intervention_id,
        "type": intervention_type,
        "hook": hook,
        "trigger": {
            "packet_features": [str(x) for x in packet_features],
            "facts": [str(x) for x in (facts or ())],
        },
        "body": dict(body),
        "claim": {
            "expected_behavior": str(expected_behavior),
            "target_polarity": str(target_polarity),
        },
        "provenance": {
            "cycle": int(cycle),
            "failure_family": str(failure_family),
            "failure_cases": [str(x) for x in failure_cases],
        },
    }
    # A learned runner's entry must pin the artifact it reads, and the
    # validator enforces that: building the payload without it would fail on
    # construction, which is the right place to learn about it.
    if artifact is not None:
        payload["artifact"] = {
            field: str(artifact.get(field) or "") for field in ARTIFACT_FIELDS
        }
    if intervention_type == "decision_guard":
        if "action_not_when_facts" not in payload["body"]:
            payload["body"]["action_not_when_facts"] = [
                "defense_view_degraded=true"
            ]
    validate_intervention(payload, require_executable_bounds=True)
    payload["content_hash"] = intervention_content_hash(payload)
    return payload


def validate_candidate(entry: Mapping[str, Any]) -> dict[str, Any]:
    validate_intervention(entry)
    return dict(entry)


def load_registry(path: str | Path) -> dict[str, Any]:
    return load_intervention_registry(path)


def iter_strings(value: Any, *, keys: Iterable[str] | None = None) -> list[str]:
    """Depth-first strings, optionally restricted to selected dict keys."""
    rows: list[str] = []
    if isinstance(value, str):
        rows.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if keys is None or key in keys:
                rows.extend(iter_strings(item, keys=keys))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            rows.extend(iter_strings(item, keys=keys))
    return rows


def candidate_prose_text(entry: Mapping[str, Any]) -> str:
    body = entry.get("body") or {}
    claim = entry.get("claim") or {}
    strings = [
        str(body.get("directive") or ""),
        str(body.get("not_when") or ""),
        str(body.get("action_not_when") or ""),
        str(body.get("predicate") or ""),
        str(body.get("runner") or ""),
        str(body.get("action") or ""),
        str(claim.get("expected_behavior") or ""),
        str(claim.get("target_polarity") or ""),
    ]
    program = body.get("predicate_definition")
    if isinstance(program, Mapping):
        # Evolved predicate programs are executable literals too. A case id
        # hidden in a DSL literal is the same leakage as one in prose and
        # must be rejected by the same STRUCTURAL check.
        strings.append(json.dumps(program, ensure_ascii=False, sort_keys=True))
    return "\n".join(strings)


def candidate_contains_case_id(entry: Mapping[str, Any]) -> str | None:
    text = candidate_prose_text(entry)
    match = _CASE_ID_RE.search(text)
    return match.group(0) if match else None


def redact_case_ids(value: Any) -> str:
    """Remove case identifiers before text crosses the proposer boundary."""
    return _CASE_ID_RE.sub("[case]", str(value or ""))


def declared_artifact(entry: Mapping[str, Any]) -> dict[str, str]:
    """The artifact binding an entry declares, as ``{id, digest}``.

    Returns empty strings for the fields it does not declare, so a caller can
    tell "no artifact" from "an artifact whose id I forgot".
    """
    raw = entry.get("artifact")
    if not isinstance(raw, Mapping):
        return {"id": "", "digest": ""}
    return {
        field: str(raw.get(field) or "").strip()
        for field in ARTIFACT_FIELDS
    }


def learned_runner_of(entry: Mapping[str, Any]) -> str:
    """The entry's runner when it needs an external artifact, else ``""``."""
    body = entry.get("body") or {}
    if not isinstance(body, Mapping):
        return ""
    runner = str(body.get("runner") or "")
    if not runner or runner in DETERMINISTIC_RUNNERS:
        return ""
    return runner


def identity_incompleteness(entry: Mapping[str, Any]) -> str:
    """Why this entry has no trustworthy executable identity, or ``""``.

    Two interventions are the same only if they execute the same thing. A
    runner that reads an artifact the entry does not pin can be swapped for a
    different one without changing a single declared field, so an entry in that
    state must not be treated as identical to anything -- nor as *different*
    from a twin, since the framework cannot tell which it is.

    This is the §23.2 red line ("learned artifacts must have a digest") in the
    one place that can enforce it before a measurement.
    """
    runner = learned_runner_of(entry)
    if not runner:
        return ""
    artifact = declared_artifact(entry)
    if not artifact["id"]:
        return (
            f"runner {runner!r} reads an external artifact but the entry "
            "declares no artifact.id"
        )
    if not artifact["digest"]:
        return (
            f"runner {runner!r} declares artifact {artifact['id']!r} without a "
            "digest, so its behaviour is not pinned to any content"
        )
    return ""


def _artifact_identity(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    """The runner/artifact component of a structured key.

    Empty for a deterministic runner, which is every entry deployed today. The
    key therefore grew a trailing element rather than changing any existing
    one: deterministic entries all receive the same empty suffix, so the
    *equivalence relation* the redundancy check relies on is exactly what it
    was -- two entries compare equal iff they did before. Nothing persists a
    key, so the changed tuple shape is not a compatibility break.

    A learned runner contributes its name plus the id and digest of the
    artifact it binds. The path is excluded on purpose: the same weights at a
    different path are the same behaviour.
    """
    runner = learned_runner_of(entry)
    if not runner:
        return ("", "", "")
    artifact = declared_artifact(entry)
    return (runner, artifact["id"], artifact["digest"])


def structured_key(entry: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """Executable identity for deterministic intervention types.

    Prose similarity is the wrong redundancy test for deterministic hooks:
    two evidence_check entries that share ``judge.post`` but execute
    different predicates are distinct and must not be rejected just because
    their natural-language claims overlap. ``text_boundary`` remains prose
    compared because its body is natural language by definition.

    The key carries the runner *and* the artifact it binds, because a runner
    that reads a model is not identified by its predicate name: two entries
    that agree on every declared field but name different weights execute
    different things. Returns ``None`` when no structural identity can be
    formed, which is the caller's signal to fall back to prose comparison; an
    entry whose identity is merely *incomplete* is reported by
    ``identity_incompleteness`` and refused before it is compared.
    """
    entry_type = str(entry.get("type") or "")
    hook = str(entry.get("hook") or "")
    body = entry.get("body") or {}
    trigger = entry.get("trigger") or {}
    features = tuple(sorted(
        str(item) for item in trigger.get("packet_features") or ()
    ))
    if entry_type == "evidence_check":
        program_hash = ""
        if str(body.get("runner") or "") == PROGRAM_RUNNER:
            raw = body.get("predicate_definition") or {}
            if isinstance(raw, Mapping):
                try:
                    from detector.predicate_dsl import validate_predicate_program

                    program_hash = predicate_program_hash(
                        validate_predicate_program(raw)
                    )
                except ValueError:
                    program_hash = ""
        return (
            "evidence_check",
            hook,
            features,
            str(body.get("predicate") or ""),
            str(body.get("runner") or ""),
            program_hash,
            _artifact_identity(entry),
        )
    if entry_type == "decision_guard":
        facts = [str(item) for item in (trigger.get("facts") or ())]
        return (
            "decision_guard",
            hook,
            features,
            tuple(sorted(facts)),
            str(body.get("action") or ""),
            str(body.get("evidence_predicate") or ""),
            tuple(sorted(
                str(item) for item in body.get("action_not_when_facts") or ()
            )),
            _artifact_identity(entry),
        )
    if entry_type == "recall_trigger":
        program_hash = ""
        if str(body.get("runner") or "") == PROGRAM_RUNNER:
            raw = body.get("predicate_definition") or {}
            if isinstance(raw, Mapping):
                try:
                    from detector.predicate_dsl import validate_predicate_program

                    program_hash = predicate_program_hash(
                        validate_predicate_program(raw)
                    )
                except ValueError:
                    program_hash = ""
        return (
            "recall_trigger",
            hook,
            features,
            tuple(sorted(
                str(item) for item in (trigger.get("facts") or ())
            )),
            str(body.get("predicate") or ""),
            str(body.get("runner") or ""),
            program_hash,
            tuple(sorted(
                str(item) for item in body.get("trigger_verdicts") or ()
            )),
            _artifact_identity(entry),
        )
    return None


def tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
    }


def jaccard(left: str, right: str) -> float:
    a = tokenize(left)
    b = tokenize(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path: str | Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        # Repeating a long cache filename in the temporary prefix can cross
        # Windows MAX_PATH even when the final path itself is valid.
        prefix=".tmp-", suffix=".json", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_jsonl(path: str | Path, payload: Mapping[str, Any]) -> None:
    """Append one record to an append-only JSONL store.

    The experience, gap and harness stores are append-only for the same reason
    the ledger is: a later round must be able to see what an earlier round
    believed, and rewriting a store would erase that. Each line carries its own
    schema so a mixed-version store fails loudly instead of being misread.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read an append-only JSONL store; a missing file is an empty store."""
    path = Path(path)
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{number} is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number} is not a JSON object")
        records.append(value)
    return records


__all__ = [
    "ARTIFACT_FIELDS",
    "DETERMINISTIC_RUNNERS",
    "append_jsonl",
    "candidate_contains_case_id",
    "candidate_prose_text",
    "declared_artifact",
    "empty_registry",
    "identity_incompleteness",
    "jaccard",
    "learned_runner_of",
    "load_json",
    "load_registry",
    "make_intervention",
    "make_registry",
    "read_jsonl",
    "redact_case_ids",
    "save_json",
    "structured_key",
    "validate_candidate",
]
