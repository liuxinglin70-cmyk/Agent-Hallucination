"""Atomic registry and governance commits with immutable deployment snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4

from detector.interventions import build_registry_commit, validate_registry_commit, validate_registry
from detector.runtime import atomic_json, contained_path, exclusive_writer
from evolution.config import resolve_path
from evolution.contracts import empty_registry, make_registry

ACTIVE_FILENAME = "active.json"
SNAPSHOTS_DIR = "snapshots"


class LibraryLimitError(ValueError):
    pass


class RegistryConflictError(RuntimeError):
    pass


class ActiveRegistry:
    def __init__(self, registry_dir: str | Path) -> None:
        self.dir = Path(registry_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / SNAPSHOTS_DIR).mkdir(parents=True, exist_ok=True)
        self.active_path = self.dir / ACTIVE_FILENAME
        self._lock = threading.RLock()
        self._commit: dict[str, Any] | None = None
        with self._lock, exclusive_writer(self.dir):
            if self.active_path.exists():
                self._commit = self._read_commit()
            else:
                if (self.dir / "state.json").exists():
                    raise ValueError("separate registry state is historical; use a new registry directory")
                self._publish(empty_registry(), self._empty_state())

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"cycle": 0, "net_contribution_history": [], "interventions": {}, "snapshots": []}

    def _read_commit(self) -> dict[str, Any]:
        payload = json.loads(self.active_path.read_text(encoding="utf-8-sig"))
        validate_registry_commit(payload)
        return payload

    def _known_commit(self) -> dict[str, Any]:
        if self._commit is None:
            self._commit = self._read_commit()
        return self._commit

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.registry["interventions"]

    @property
    def registry(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._known_commit()["registry"])

    @property
    def registry_hash(self) -> str:
        return str(self.registry["content_hash"])

    @property
    def state(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._known_commit()["state"])

    @contextmanager
    def _mutation(self):
        with self._lock, exclusive_writer(self.dir):
            current = self._read_commit()
            if self._commit is None or self._commit["commit_id"] != current["commit_id"]:
                raise RegistryConflictError("registry commit changed; reopen before mutation")
            self._commit = current
            yield deepcopy(self._commit["registry"]), deepcopy(self._commit["state"])

    def _publish(self, registry: Mapping[str, Any], state: Mapping[str, Any], artifacts=()) -> dict[str, Any]:
        state = deepcopy(state)
        state["active_hash"] = registry["content_hash"]
        commit = build_registry_commit(registry, state, commit_id=uuid4().hex)
        created = []
        try:
            for name, payload in artifacts:
                path = contained_path(self.dir, SNAPSHOTS_DIR, name)
                if path.exists():
                    raise FileExistsError(f"snapshot already exists: {path}")
                created.append(path)
                atomic_json(path, payload)
            atomic_json(self.active_path, commit)
        except BaseException as failure:
            # A signal may arrive after replace; reconcile against the sole commit point.
            try:
                self._commit = self._read_commit()
            except Exception:
                self._commit = None
            if self._commit is not None:
                referenced = {item.get("file") for item in self._commit["state"]["snapshots"]}
                try:
                    for path in created:
                        if path.name not in referenced:
                            path.unlink(missing_ok=True)
                finally:
                    raise failure
            raise
        self._commit = commit
        return deepcopy(commit["registry"])

    def check_limits(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        library_cfg: Mapping[str, Any] | None = None,
    ) -> list[str]:
        library_cfg = library_cfg or {}
        problems: list[str] = []
        max_width = int(library_cfg.get("max_width", 3))
        max_text_per_hook = int(library_cfg.get("max_text_boundary_per_hook", 1))
        max_guards = int(library_cfg.get("max_decision_guards", 2))
        if len(entries) > max_width:
            problems.append(f"library width {len(entries)} exceeds {max_width}")
        text_counts: dict[str, int] = {}
        guard_count = 0
        for entry in entries:
            hook = str(entry.get("hook") or "")
            if entry.get("type") == "text_boundary":
                text_counts[hook] = text_counts.get(hook, 0) + 1
            if entry.get("type") == "decision_guard":
                guard_count += 1
        for hook, count in text_counts.items():
            if count > max_text_per_hook:
                problems.append(
                    f"hook {hook} has {count} text_boundary interventions "
                    f"(limit {max_text_per_hook})"
                )
        if guard_count > max_guards:
            problems.append(
                f"{guard_count} decision_guard interventions (limit {max_guards})"
            )
        return problems

    def _prepare_write(self, entries, *, current, state, cycle, reason, library_cfg=None):
        problems = self.check_limits(entries, library_cfg=library_cfg)
        if problems:
            raise LibraryLimitError("; ".join(problems))
        payload = make_registry(deepcopy(entries))
        artifacts = []
        if not state["snapshots"]:
            baseline_cycle = max(0, int(cycle) - 1)
            name = f"cycle{baseline_cycle:03d}_baseline_{uuid4().hex}.json"
            artifacts.append((name, current))
            state["snapshots"].append({
                "cycle": baseline_cycle, "file": name, "hash": current["content_hash"],
                "previous_hash": "", "reason": "pre-admission baseline",
                "entries": [entry["intervention_id"] for entry in current["interventions"]],
            })
        name = f"cycle{int(cycle):03d}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex}.json"
        artifacts.append((name, payload))
        state["cycle"] = int(cycle)
        state["snapshots"].append({
            "cycle": int(cycle), "file": name, "hash": payload["content_hash"],
            "previous_hash": current["content_hash"], "reason": str(reason),
            "entries": [entry["intervention_id"] for entry in payload["interventions"]],
        })
        return payload, artifacts

    def write(self, entries: Sequence[Mapping[str, Any]], *, cycle: int, reason: str,
              library_cfg: Mapping[str, Any] | None = None,
              publication_id: str | None = None, publication: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._mutation() as (current, state):
            payload, artifacts = self._prepare_write(entries, current=current, state=state,
                                                     cycle=cycle, reason=reason, library_cfg=library_cfg)
            if (publication_id is None) != (publication is None):
                raise ValueError("publication identity and record must be supplied together")
            if publication is not None:
                records = state.setdefault("publications", {})
                if publication_id in records:
                    raise RegistryConflictError("publication already committed; recover its record")
                records[publication_id] = {"registry_hash": payload["content_hash"], "record": deepcopy(publication)}
            return self._publish(payload, state, artifacts)

    def record_marginal(self, intervention_id: str, marginal: int, *, cycle: int, fired_rate: float) -> None:
        with self._mutation() as (current, state):
            info = state["interventions"].setdefault(intervention_id, {})
            history = info.setdefault("marginal_history", [])
            existing = next((item for item in history if item["cycle"] == int(cycle)), None)
            value = {"cycle": int(cycle), "marginal_cases": int(marginal), "fired_rate": float(fired_rate)}
            if existing is None:
                history.append(value)
            else:
                existing.update(value)
            info.setdefault("admitted_cycle", int(cycle))
            self._publish(current, state)

    def record_net_contribution(self, net_correct: int, *, cycle: int) -> None:
        with self._mutation() as (current, state):
            history = state["net_contribution_history"]
            existing = next((item for item in history if item["cycle"] == int(cycle)), None)
            value = {"cycle": int(cycle), "net_correct_cases": int(net_correct)}
            if existing is None:
                history.append(value)
            else:
                existing.update(value)
            self._publish(current, state)

    @staticmethod
    def _retire_dependencies(entries, retiring_ids):
        retiring = set(retiring_ids)
        predicates = {str(entry["body"].get("predicate") or "") for entry in entries
                      if entry["intervention_id"] in retiring and entry["type"] == "evidence_check"}
        return sorted(entry["intervention_id"] for entry in entries
                      if entry["intervention_id"] not in retiring and entry["type"] == "decision_guard"
                      and predicates & {str(fact).split("=", 1)[0] for fact in entry["trigger"].get("facts", ()) if "=" in str(fact)})

    def check_retire_dependencies(self, retiring_ids: Sequence[str]) -> list[str]:
        return self._retire_dependencies(self.entries, retiring_ids)

    def retire(self, intervention_ids: Sequence[str], *, cycle: int,
               library_cfg: Mapping[str, Any] | None = None) -> list[str]:
        with self._mutation() as (current, state):
            ids = set(intervention_ids)
            orphans = self._retire_dependencies(current["interventions"], ids)
            if orphans:
                raise ValueError(f"Cannot retire {sorted(ids)}: remaining guard(s) {orphans} depend on evidence being retired. Retire the full unit closure instead.")
            retired = [entry["intervention_id"] for entry in current["interventions"] if entry["intervention_id"] in ids]
            payload, artifacts = current, []
            if retired:
                payload, artifacts = self._prepare_write(
                    [entry for entry in current["interventions"] if entry["intervention_id"] not in ids],
                    current=current, state=state, cycle=cycle, reason="outcome-driven retirement", library_cfg=library_cfg)
            for identifier in ids:
                if identifier in state["interventions"]:
                    state["interventions"][identifier]["retired_cycle"] = int(cycle)
            self._publish(payload, state, artifacts)
            return retired

    def restore(self, payload: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        validated = make_registry(validate_registry(payload, require_executable_bounds=True))
        with self._mutation() as (current, state):
            if current["content_hash"] != validated["content_hash"]:
                state["snapshots"].append({
                    "cycle": state["cycle"], "file": "", "hash": validated["content_hash"],
                    "previous_hash": current["content_hash"], "reason": str(reason),
                    "entries": [entry["intervention_id"] for entry in validated["interventions"]],
                })
            return self._publish(validated, state)

    def rollback_to_snapshot(self, cycle: int) -> dict[str, Any] | None:
        with self._mutation() as (current, state):
            eligible = [item for item in reversed(state["snapshots"])
                        if int(item["cycle"]) <= int(cycle) and item.get("file")
                        and contained_path(self.dir, SNAPSHOTS_DIR, item["file"]).is_file()]
            target = max(eligible, key=lambda item: item["cycle"], default=None)
            if target is None:
                return None
            path = contained_path(self.dir, SNAPSHOTS_DIR, target["file"])
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            payload = make_registry(validate_registry(raw, require_executable_bounds=True))
            if payload["content_hash"] != target["hash"]:
                raise ValueError("rollback snapshot hash does not match committed index")
            target_cycle = int(target["cycle"])
            target_ids = {entry["intervention_id"] for entry in payload["interventions"]}
            for identifier, info in state["interventions"].items():
                if identifier not in target_ids:
                    info["rolled_back_out"] = True
                    continue
                info["marginal_history"] = [item for item in info.get("marginal_history", ()) if item["cycle"] <= target_cycle]
                info.pop("retired_cycle", None)
                info.pop("rolled_back_out", None)
            state["net_contribution_history"] = [item for item in state["net_contribution_history"] if item["cycle"] <= target_cycle]
            state["cycle"] = target_cycle
            state["rolled_back_to"] = {"cycle": int(cycle), "snapshot": target["file"], "hash": payload["content_hash"]}
            return self._publish(payload, state)

    def clear(self) -> None:
        with self._mutation() as (_, current_state):
            state = self._empty_state()
            if "publications" in current_state:
                state["publications"] = current_state["publications"]
            self._publish(empty_registry(), state)


def load_active_registry(config: Mapping[str, Any]) -> ActiveRegistry:
    return ActiveRegistry(resolve_path(config["registry_dir"]))


__all__ = ["ActiveRegistry", "LibraryLimitError", "RegistryConflictError", "load_active_registry"]
