"""Candidate version graph: parent-child lineage shared by every dev path.

``evolution/research.py`` already had a working version store: numbered
``vNNN`` directories, a ``candidate.json`` per version, ``parent.json`` +
``diagnosis.json`` for revisions, an ``identity.json`` that pins the evaluation
envelope, and ``results/`` for the per-case measurements. It was private to
``ResearchRun``, so the deployment-side development loop (plan P0.3) had no
lineage to build on and would have grown a second, subtly different one.

This module is that store, extracted. The on-disk format is unchanged -- the
archived runs under ``reports/rsi_pilot_20260919/`` must stay readable, and
``evolution/tests/test_research.py`` pins the behaviour.

Development-specific bookkeeping lives in sidecar files (``development.json``,
``development_log.jsonl``) rather than in the existing records: a candidate
version's shape is a frozen contract that other reports already parse.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolution.contracts import save_json

# Status values a development version can carry (plan §6.2).
VERSION_STATUSES = (
    "draft",
    "failed_l0",
    "failed_l1",
    # The replay could not score this version: a provider failure, a deadline
    # skip, a non-authoritative terminal. Deliberately not ``failed_l1`` --
    # "the loop measured this and it did not work" and "the loop could not
    # measure this" are different claims about a candidate, and only the first
    # is a falsification. The archive, the episodic memory and the next
    # drafting prompt all read the difference.
    "unmeasured",
    "ready_for_freeze",
    "frozen",
    "discarded",
)

DEVELOPMENT_SIDECAR = "development.json"
DEVELOPMENT_LOG = "development_log.jsonl"


def canonical_hash(payload: Any) -> str:
    """Stable hash of a JSON payload, used for candidate and manifest identity."""
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def case_file_name(case_id: str) -> str:
    """File-system-safe artifact name for one case id."""
    safe = str(case_id).replace("/", "__").replace("\\", "__")
    return safe if safe.endswith(".json") else f"{safe}.json"


class CandidateVersionGraph:
    """The ``vNNN`` store, with revision lineage.

    Numbering is continuous across resumed runs: existing version directories
    on disk are never overwritten, so a run that restarts continues instead of
    reusing an id whose results are already published.
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        versions_created: int = 0,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.versions: list[dict[str, Any]] = []
        self.versions_created = int(versions_created)

    # -- ids --------------------------------------------------------------
    def new_version_id(self) -> str:
        numbers = [self.versions_created]
        for path in self.output_dir.glob("v*"):
            name = path.name
            if name.startswith("v") and name[1:].isdigit():
                numbers.append(int(name[1:]))
        self.versions_created = max(numbers) + 1
        return f"v{self.versions_created:03d}"

    def load_existing(self) -> None:
        """Rebuild the version list from disk, without creating anything."""
        for path in sorted(self.output_dir.glob("v*/candidate.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, Mapping) or not record.get("version_id"):
                continue
            self.versions.append(dict(record))
            version_id = str(record.get("version_id"))
            if version_id.startswith("v") and version_id[1:].isdigit():
                self.versions_created = max(
                    self.versions_created, int(version_id[1:]),
                )

    def route_versions(self) -> dict[str, list[str]]:
        """Version ids per route, in creation order."""
        routes: dict[str, list[str]] = {}
        for record in self.versions:
            route = str(record.get("route") or "?")
            version_id = str(record.get("version_id") or "")
            entries = routes.setdefault(route, [])
            if version_id and version_id not in entries:
                entries.append(version_id)
        return routes

    def parent_of(self, version_id: str) -> str | None:
        """Which version this one was revised from, from its sidecar.

        The revision record on disk carries ``parent_hash``, not a version id,
        so the lineage edge is read from the parent sidecar when it is there.
        """
        sidecar = self.output_dir / str(version_id) / "parent.json"
        if not sidecar.exists():
            return None
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        parent_hash = str(record.get("parent_hash") or "")
        if not parent_hash:
            return None
        for candidate in self.versions:
            if str(candidate.get("candidate_hash") or "") == parent_hash:
                return str(candidate.get("version_id") or "") or None
        return None

    def lineage(self, version_id: str) -> list[str]:
        """The chain of versions leading to ``version_id``, oldest first."""
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = str(version_id)
        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            current = self.parent_of(current)
        chain.reverse()
        return chain

    # -- records ----------------------------------------------------------
    def record(
        self,
        route: str,
        version_id: str,
        candidate: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        *,
        parent: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        diagnosis: Mapping[str, Any] | None,
        draft_notes: Sequence[Mapping[str, Any]],
        evaluation: Mapping[str, Any] | None,
        verdict: Mapping[str, Any] | None,
        status: str | None = None,
        revision_reason: Sequence[Any] = (),
    ) -> dict[str, Any]:
        """Write one version: candidate, lineage and evaluation envelope.

        ``candidate.json`` keeps exactly the fields it always had. The
        development status goes to a sidecar so an existing reader of the
        research format cannot be surprised by a new key.
        """
        version_dir = self.output_dir / version_id
        stored_candidate: Any = None
        if candidate is not None:
            stored_candidate = (
                [dict(entry) for entry in candidate]
                if not isinstance(candidate, Mapping) else dict(candidate)
            )
        record = {
            "version_id": version_id,
            "route": route,
            "kind": "draft" if diagnosis is None else "revision",
            "candidate": stored_candidate,
            "candidate_hash": (
                canonical_hash(candidate) if candidate else None
            ),
            "parent_hash": (
                canonical_hash(parent)
                if parent is not None and diagnosis is not None
                else None
            ),
            "notes": list(draft_notes),
            "evaluation": (
                {
                    "identity": evaluation["identity"],
                    "reused": evaluation["reused"],
                    "source_version": evaluation.get("source_version"),
                }
                if evaluation else None
            ),
            "verdict": verdict,
        }
        save_json(version_dir / "candidate.json", record)
        if parent is not None and diagnosis is not None:
            save_json(version_dir / "parent.json", {
                "parent_hash": canonical_hash(parent),
                "parent_candidate": (
                    [dict(entry) for entry in parent]
                    if not isinstance(parent, Mapping) else dict(parent)
                ),
            })
            save_json(version_dir / "diagnosis.json", diagnosis)
        self.versions.append(record)
        if status or revision_reason:
            self.write_development(version_id, {
                "status": status or "draft",
                "revision_reason": [
                    item if isinstance(item, Mapping) else {"note": str(item)}
                    for item in revision_reason
                ],
            })
        return record

    def write_development(
        self, version_id: str, payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Write or update the development sidecar of one version."""
        path = self.output_dir / str(version_id) / DEVELOPMENT_SIDECAR
        current: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, Mapping):
                    current = dict(loaded)
            except (OSError, ValueError):
                current = {}
        current.update(dict(payload))
        save_json(path, current)
        return current

    def development_status(self, version_id: str) -> dict[str, Any]:
        path = self.output_dir / str(version_id) / DEVELOPMENT_SIDECAR
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}

    def log(self, payload: Mapping[str, Any]) -> None:
        """Append one machine-readable development event."""
        path = self.output_dir / DEVELOPMENT_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")

    def read_log(self) -> list[dict[str, Any]]:
        path = self.output_dir / DEVELOPMENT_LOG
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records

    # -- evaluation envelope ---------------------------------------------
    @staticmethod
    def identity(
        candidate: Mapping[str, Any],
        *,
        case_ids: Sequence[str],
        registry_hash: str,
        config_hash: str,
        code_baseline: str,
        evaluation_mode: str,
    ) -> dict[str, Any]:
        """The bundle that decides whether stored results may be reused.

        Input, code, registry and model envelope all participate: recovery may
        reuse results only when every one of them matches (doc §8.3).
        """
        return {
            "candidate_hash": canonical_hash(candidate),
            "registry_hash": str(registry_hash or ""),
            "case_set": list(case_ids),
            "config_hash": config_hash,
            "code_baseline": code_baseline,
            "evaluation_mode": evaluation_mode,
        }

    def find_reusable_results(
        self,
        identity: Mapping[str, Any],
        *,
        required_cases: Sequence[str],
    ) -> tuple[str | None, dict[str, dict[str, Any]] | None]:
        """Prior stored results with a fully identical identity, if complete."""
        for path in sorted(self.output_dir.glob("v*/identity.json")):
            try:
                stored = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if stored != dict(identity):
                continue
            cases, results = self.load_results(path.parent / "results")
            wanted = [str(case) for case in required_cases]
            if set(wanted) <= cases:
                return path.parent.name, {
                    case: results[case] for case in wanted
                }
        return None, None

    @staticmethod
    def load_results(
        results_dir: Path,
    ) -> tuple[set[str], dict[str, dict[str, Any]]]:
        results: dict[str, dict[str, Any]] = {}
        for path in results_dir.glob("*.json"):
            try:
                entry = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            results[str(entry.get("case_id"))] = entry
        return set(results), results

    def save_result(self, results_dir: Path, entry: Mapping[str, Any]) -> None:
        save_json(
            results_dir / case_file_name(str(entry.get("case_id") or "")),
            dict(entry),
        )

    def read_identity(self, version_id: str) -> dict[str, Any]:
        path = self.output_dir / str(version_id) / "identity.json"
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}


__all__ = [
    "DEVELOPMENT_LOG",
    "DEVELOPMENT_SIDECAR",
    "VERSION_STATUSES",
    "CandidateVersionGraph",
    "canonical_hash",
    "case_file_name",
]
