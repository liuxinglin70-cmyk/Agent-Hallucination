"""Locked observations and recorded identities for read-only comparisons."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from detector.identity import digest, EXECUTION_IDENTITY_SCHEMA
from detector.result import prediction_state
from detector.runner import RUN_SCHEMA, case_elapsed, safe_case_id, validate_case_commit
from detector.runtime import contained_path, exclusive_writer, validate_path_id


def _read_rows(root: Path, cases: Sequence[str]) -> list[dict[str, Any]]:
    validate_path_id(root.name, nested=False)
    rows = []
    for case in cases:
        path = contained_path(root, "cases", safe_case_id(case), "result.json")
        if not path.is_file():
            continue
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        validate_case_commit(result, run_id=root.name, case_id=case)
        rows.append({"case": case, "result": result, "elapsed_seconds": case_elapsed(result)})
    return rows


def _manifest(root: Path) -> dict[str, Any]:
    path = contained_path(root, "run.json")
    if not path.is_file():
        raise ValueError(f"run lacks its recorded manifest: {root}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("schema_version") != RUN_SCHEMA or value.get("run_id") != root.name:
        raise ValueError("run manifest schema or identity differs")
    if any(not isinstance(value.get(key), str) or not value[key]
           for key in ("source_identity", "config_hash", "interventions_hash")):
        raise ValueError("run manifest lacks its execution inputs")
    cases = value.get("cases")
    if not isinstance(cases, list) or any(not isinstance(case, str) for case in cases):
        raise ValueError("run manifest cases must be identifiers")
    paths = [safe_case_id(case).casefold() for case in cases]
    if len(paths) != len(set(paths)) or value.get("case_set_digest") != digest({"cases": cases}):
        raise ValueError("run manifest case set differs")
    identities = value.get("execution_identities")
    if not isinstance(identities, dict) or set(identities) != set(cases):
        raise ValueError("run manifest lacks complete execution identities")
    fields = {"schema_version", "source", "config", "record", "packet", "evidence", "registry", "replay", "authority", "artifacts", "digest"}
    for identity in identities.values():
        if (not isinstance(identity, dict) or set(identity) != fields
                or identity.get("schema_version") != EXECUTION_IDENTITY_SCHEMA
                or identity.get("digest") != digest({key: val for key, val in identity.items() if key != "digest"})
                or not isinstance(identity.get("artifacts"), dict)
                or any(not isinstance(identity.get(key), str) or not identity[key]
                       for key in fields - {"artifacts", "schema_version", "digest"})):
            raise ValueError("run execution identity is invalid")
        if (identity["source"] != value.get("source_identity") or identity["config"] != value.get("config_hash")
                or identity["registry"] != value.get("interventions_hash")):
            raise ValueError("run manifest differs from its execution inputs")
    return value


@dataclass(frozen=True)
class RunSnapshot:
    roots: Mapping[str, Path | None]
    manifests: Mapping[str, dict[str, Any]]
    rows: Mapping[str, list[dict[str, Any]]]

    def sample(self, cases: Sequence[str]) -> dict[str, Any]:
        measured = {name: {row["case"] for row in rows if prediction_state(row["result"]) is not None}
                    for name, rows in self.rows.items()}
        common = set(cases) if measured else set()
        for found in measured.values():
            common &= found
        ordered = [case for case in cases if case in common]
        return {"arms": {name: len(found) for name, found in measured.items()}, "size": len(ordered), "common": ordered,
                "note": "the common measured set is chosen by which cases survived measurement, not by their verdicts; "
                        "because slow cases are the ones an outage removes, it is biased toward the easier part of the "
                        "corpus and must not be read as a representative sample"}

    def same_execution(self, first: str, second: str, cases: Sequence[str]) -> bool:
        same = True
        for case in cases:
            left = self.manifests[first]["execution_identities"][case]
            right = self.manifests[second]["execution_identities"][case]
            if any(left[key] != right[key] for key in ("source", "record", "packet")):
                raise ValueError(f"paired source or task input differs: {case}")
            same = same and left == right
        return same

    def require_independent_roll(self, baseline: str, roll: str, cases: Sequence[str]) -> None:
        if self.roots[baseline] == self.roots[roll]:
            raise ValueError("baseline rolls must be independent run roots")
        if any(case not in self.manifests[name]["execution_identities"] for name in (baseline, roll) for case in cases):
            raise ValueError("baseline rolls do not cover the paired cases")
        if not self.same_execution(baseline, roll, cases):
            raise ValueError("baseline rolls have different recorded execution inputs")


@contextmanager
def run_snapshot(roots: Mapping[str, str | Path], cases: Sequence[str] | None = None) -> Iterator[RunSnapshot]:
    captured = {}
    for name, supplied in roots.items():
        root = Path(supplied).resolve()
        validate_path_id(root.name, nested=False)
        if root.exists() and not root.is_dir():
            raise ValueError(f"run root is not a directory: {root}")
        captured[name] = root if root.is_dir() else None
    with ExitStack() as leases:
        for root in sorted({root for root in captured.values() if root is not None}, key=lambda path: str(path).casefold()):
            leases.enter_context(exclusive_writer(root))
        manifests, rows = {}, {}
        for name, root in captured.items():
            if root is None:
                rows[name] = []
                continue
            manifest = _manifest(root)
            selected = list(cases) if cases is not None else manifest["cases"]
            observed = _read_rows(root, selected)
            for row in observed:
                case, result = row["case"], row["result"]
                identity = manifest["execution_identities"].get(case)
                if (identity is None or (result.get("provenance") or {}).get("execution_identity") != identity
                        or result["case_commit"]["input_digest"] != identity["record"]):
                    raise ValueError(f"case observation differs from its run input: {case}")
            manifests[name], rows[name] = manifest, observed
        yield RunSnapshot(captured, manifests, rows)
