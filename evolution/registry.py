"""Pre-commit active registry with library limits and snapshots.

The registry file is the only object detector consumes at runtime. Writes
happen only after STRUCTURAL-BEHAVIOR-M all pass, and every write is snapshotted so the
mechanical drift rollback in the design has a concrete implementation.
"""

from __future__ import annotations

import json
from copy import deepcopy
import os
import tempfile
import threading
import time
from uuid import uuid4
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector.interventions import (
    validate_registry,
)

from evolution.config import resolve_path
from evolution.contracts import empty_registry, make_registry

ACTIVE_FILENAME = "active.json"
STATE_FILENAME = "state.json"
SNAPSHOTS_DIR = "snapshots"


class LibraryLimitError(ValueError):
    pass


class ActiveRegistry:
    def __init__(self, registry_dir: str | Path) -> None:
        self.dir = Path(registry_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / SNAPSHOTS_DIR).mkdir(parents=True, exist_ok=True)
        self.active_path = self.dir / ACTIVE_FILENAME
        self.state_path = self.dir / STATE_FILENAME
        self._entries = self._load_entries()
        self._state = self._load_state()
        self._lock = threading.RLock()
        expected_hash = str(self._state.get("active_hash") or "")
        if expected_hash and expected_hash != self.registry_hash:
            raise RuntimeError(
                "registry active/state mismatch: "
                f"active={self.registry_hash}, state={expected_hash}"
            )
        if not self.active_path.exists():
            self._atomic_write(self.active_path, empty_registry())

    @staticmethod
    def _atomic_write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
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

    @property
    def entries(self) -> list[dict[str, Any]]:
        return deepcopy(self._entries)

    @property
    def registry(self) -> dict[str, Any]:
        return make_registry(deepcopy(self._entries))

    @property
    def registry_hash(self) -> str:
        return str(self.registry.get("content_hash") or "")

    @property
    def state(self) -> dict[str, Any]:
        return deepcopy(self._state)

    def _load_entries(self) -> list[dict[str, Any]]:
        if not self.active_path.exists():
            return []
        payload = json.loads(self.active_path.read_text(encoding="utf-8-sig"))
        return validate_registry(payload, require_executable_bounds=True)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "cycle": 0,
                "net_contribution_history": [],
                "interventions": {},
                "snapshots": [],
            }
        return json.loads(self.state_path.read_text(encoding="utf-8-sig"))

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

    def write(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        cycle: int,
        reason: str,
        library_cfg: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            problems = self.check_limits(entries, library_cfg=library_cfg)
            if problems:
                raise LibraryLimitError("; ".join(problems))
            payload = make_registry(entries)
            previous_hash = self.registry_hash
            if not self._state.get("snapshots"):
                baseline_name = (
                    f"cycle{max(0, int(cycle) - 1):03d}_baseline.json"
                )
                baseline_payload = make_registry(self._entries)
                self._atomic_write(
                    self.dir / SNAPSHOTS_DIR / baseline_name,
                    baseline_payload,
                )
                self._state.setdefault("snapshots", []).append({
                    "cycle": max(0, int(cycle) - 1),
                    "file": baseline_name,
                    "hash": str(baseline_payload.get("content_hash") or ""),
                    "previous_hash": "",
                    "reason": "pre-admission baseline",
                    "entries": [
                        str(e.get("intervention_id") or "")
                        for e in self._entries
                    ],
                })
            snapshot_name = f"cycle{int(cycle):03d}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex}.json"
            snapshot_path = self.dir / SNAPSHOTS_DIR / snapshot_name
            self._atomic_write(snapshot_path, payload)
            self._atomic_write(self.active_path, payload)
            self._entries = deepcopy(list(entries))
            self._state["cycle"] = int(cycle)
            self._state["active_hash"] = str(payload.get("content_hash") or "")
            self._state.setdefault("snapshots", []).append({
                "cycle": int(cycle),
                "file": snapshot_name,
                "hash": str(payload.get("content_hash") or ""),
                "previous_hash": previous_hash,
                "reason": str(reason),
                "entries": [str(e.get("intervention_id") or "") for e in entries],
            })
            self._save_state()
            return payload

    def record_marginal(
        self,
        intervention_id: str,
        marginal: int,
        *,
        cycle: int,
        fired_rate: float,
    ) -> None:
        info = self._state.setdefault("interventions", {}).setdefault(
            intervention_id, {}
        )
        history = info.setdefault("marginal_history", [])
        current = next(
            (item for item in history if int(item.get("cycle") or 0) == int(cycle)),
            None,
        )
        payload = {
            "cycle": int(cycle),
            "marginal_cases": int(marginal),
            "fired_rate": float(fired_rate),
        }
        if current is None:
            history.append(payload)
        else:
            current.update(payload)
        info["admitted_cycle"] = info.get("admitted_cycle", int(cycle))
        self._save_state()

    def record_net_contribution(self, net_correct: int, *, cycle: int) -> None:
        history = self._state.setdefault("net_contribution_history", [])
        for entry in history:
            if int(entry.get("cycle") or 0) == int(cycle):
                # A retried cycle overwrites its own record instead of
                # appending; the rollback threshold reads cycle-disjoint
                # entries, not rerun artifacts.
                entry["net_correct_cases"] = int(net_correct)
                self._save_state()
                return
        history.append({"cycle": int(cycle), "net_correct_cases": int(net_correct)})
        self._save_state()

    def check_retire_dependencies(
        self,
        retiring_ids: Sequence[str],
    ) -> list[str]:
        """Check if retiring any of these IDs would orphan a remaining guard."""
        to_retire = set(retiring_ids)
        remaining = [
            e for e in self._entries
            if str(e.get("intervention_id") or "") not in to_retire
        ]
        retired_predicates = {
            str((e.get("body") or {}).get("predicate") or "")
            for e in self._entries
            if str(e.get("intervention_id") or "") in to_retire
            and e.get("type") == "evidence_check"
        }
        orphans = []
        for e in remaining:
            if e.get("type") == "decision_guard":
                consumed = {
                    str(fact).split("=", 1)[0]
                    for fact in (e.get("trigger") or {}).get("facts") or ()
                    if "=" in str(fact)
                }
                if consumed & retired_predicates:
                    orphans.append(str(e.get("intervention_id")))
        return sorted(orphans)

    def retire(
        self,
        intervention_ids: Sequence[str],
        *,
        cycle: int,
        library_cfg: Mapping[str, Any] | None = None,
    ) -> list[str]:
        with self._lock:
            ids = set(intervention_ids)
            orphans = self.check_retire_dependencies(intervention_ids)
            if orphans:
                raise ValueError(
                    f"Cannot retire {sorted(ids)}: remaining guard(s) {orphans} "
                    "depend on evidence being retired. Retire the full unit closure instead."
                )
            retired = [
                entry for entry in self._entries
                if str(entry.get("intervention_id") or "") in ids
            ]
            remaining = [
                entry for entry in self._entries
                if str(entry.get("intervention_id") or "") not in ids
            ]
            if retired:
                self.write(
                    remaining,
                    cycle=cycle,
                    reason="outcome-driven retirement",
                    library_cfg=library_cfg,
                )
            for intervention_id in ids:
                info = self._state.get("interventions", {}).get(intervention_id)
                if info is not None:
                    info["retired_cycle"] = int(cycle)
            self._save_state()
            return [
                str(entry.get("intervention_id") or "") for entry in retired
            ]


    def restore(
        self,
        payload: Mapping[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Reinstate a previously recorded registry payload.

        Used when resuming a cycle whose own admission was already committed:
        the resumed run must be evaluated against the state the cycle started
        from, or its gates would treat the candidate as already deployed. The
        snapshot history is retained, so the admission committed by the
        interrupted run stays recoverable rather than being discarded.
        """
        # ``validate_registry`` returns the entry list; rebuild the envelope so
        # the content hash is recomputed rather than trusted from the file.
        entries = validate_registry(dict(payload), require_executable_bounds=True)
        validated = make_registry(entries)
        restored_hash = str(validated.get("content_hash") or "")
        previous_hash = self.registry_hash
        with self._lock:
            self._atomic_write(self.active_path, validated)
            self._entries = deepcopy(list(validated.get("interventions") or ()))
            self._state["active_hash"] = restored_hash
            if previous_hash != restored_hash:
                self._state.setdefault("snapshots", []).append({
                    "cycle": int(self._state.get("cycle") or 0),
                    "file": "",
                    "hash": restored_hash,
                    "previous_hash": previous_hash,
                    "reason": str(reason),
                    "entries": [
                        str(entry.get("intervention_id") or "")
                        for entry in self._entries
                    ],
                })
            self._save_state()
        return validated

    def rollback_to_snapshot(self, cycle: int) -> dict[str, Any] | None:
        snapshots = list(self._state.get("snapshots") or [])
        if not snapshots:
            return None
        target = max(
            (item for item in reversed(snapshots)
             if int(item.get("cycle") or 0) <= int(cycle)
             and item.get("file")
             and (self.dir / SNAPSHOTS_DIR / str(item["file"])).is_file()),
            key=lambda item: int(item.get("cycle") or 0),
            default=None,
        )
        if target is None:
            return None
        # A snapshot recorded without a file (the resume path records the
        # state it restored) has nothing to restore from. An empty name must
        # not resolve to the snapshots directory itself: `exists()` is true
        # for a directory and reading it raises instead of reporting "no
        # snapshot".
        name = str(target.get("file") or "")
        if not name:
            return None
        path = self.dir / SNAPSHOTS_DIR / name
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        validate_registry(payload, require_executable_bounds=True)
        self._atomic_write(self.active_path, payload)
        self._entries = validate_registry(
            payload, require_executable_bounds=True,
        )
        target_cycle = int(target.get("cycle") or 0)
        target_ids = {
            str(entry.get("intervention_id") or "") for entry in self._entries
        }
        interventions = self._state.setdefault("interventions", {})
        for intervention_id in list(interventions):
            info = interventions[intervention_id]
            if intervention_id not in target_ids:
                # Preserve audit history, but prevent retired/removed entries
                # from influencing future governance after rollback.
                info["rolled_back_out"] = True
                continue
            info["marginal_history"] = [
                item for item in info.get("marginal_history") or []
                if int(item.get("cycle") or 0) <= target_cycle
            ]
            info.pop("retired_cycle", None)
            info.pop("rolled_back_out", None)
        self._state["net_contribution_history"] = [
            item for item in self._state.get("net_contribution_history") or []
            if int(item.get("cycle") or 0) <= target_cycle
        ]
        self._state["cycle"] = target_cycle
        self._state["rolled_back_to"] = {
            "cycle": int(cycle),
            "snapshot": str(target.get("file") or ""),
            "hash": str(payload.get("content_hash") or ""),
        }
        self._state["active_hash"] = str(payload.get("content_hash") or "")
        self._save_state()
        return payload

    def _save_state(self) -> None:
        self._atomic_write(self.state_path, self._state)

    def clear(self) -> None:
        payload = empty_registry()
        self._atomic_write(self.active_path, payload)
        self._entries = []
        self._state = {
            "cycle": 0,
            "active_hash": str(payload.get("content_hash") or ""),
            "net_contribution_history": [],
            "interventions": {},
            "snapshots": [],
        }
        self._save_state()


def load_active_registry(config: Mapping[str, Any]) -> ActiveRegistry:
    return ActiveRegistry(resolve_path(config["registry_dir"]))


__all__ = [
    "ActiveRegistry",
    "LibraryLimitError",
    "load_active_registry",
]
