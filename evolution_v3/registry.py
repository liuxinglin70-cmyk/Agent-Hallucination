"""Pre-commit active registry with library limits and snapshots.

The registry file is the only object detector_v2 consumes at runtime. Writes
happen only after V1-V3-M all pass, and every write is snapshotted so the
mechanical drift rollback in the design has a concrete implementation.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from detector_v2.interventions import (
    validate_registry,
)

from evolution_v3.config import resolve_path
from evolution_v3.contracts import empty_registry, make_registry, save_json

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

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    @property
    def registry(self) -> dict[str, Any]:
        return make_registry(self._entries)

    @property
    def registry_hash(self) -> str:
        return str(self.registry.get("content_hash") or "")

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def _load_entries(self) -> list[dict[str, Any]]:
        if not self.active_path.exists():
            return []
        payload = json.loads(self.active_path.read_text(encoding="utf-8-sig"))
        return validate_registry(payload)

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
        problems = self.check_limits(entries, library_cfg=library_cfg)
        if problems:
            raise LibraryLimitError("; ".join(problems))
        payload = make_registry(entries)
        self.active_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        snapshot_name = f"cycle{int(cycle):03d}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        snapshot_path = self.dir / SNAPSHOTS_DIR / snapshot_name
        save_json(snapshot_path, payload)
        previous_hash = self.registry_hash
        self._entries = [dict(entry) for entry in entries]
        self._state["cycle"] = int(cycle)
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
        history.append({
            "cycle": int(cycle),
            "marginal_cases": int(marginal),
            "fired_rate": float(fired_rate),
        })
        info["admitted_cycle"] = info.get("admitted_cycle", int(cycle))
        self._save_state()

    def record_net_contribution(self, net_correct: int, *, cycle: int) -> None:
        history = self._state.setdefault("net_contribution_history", [])
        history.append({"cycle": int(cycle), "net_correct_cases": int(net_correct)})
        self._save_state()

    def retire(self, intervention_ids: Sequence[str], *, cycle: int) -> list[str]:
        ids = set(intervention_ids)
        retired = [
            entry for entry in self._entries
            if str(entry.get("intervention_id") or "") in ids
        ]
        self._entries = [
            entry for entry in self._entries
            if str(entry.get("intervention_id") or "") not in ids
        ]
        payload = make_registry(self._entries)
        self.active_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for intervention_id in ids:
            info = self._state.get("interventions", {}).get(intervention_id)
            if info is not None:
                info["retired_cycle"] = int(cycle)
        self._save_state()
        return [str(entry.get("intervention_id") or "") for entry in retired]

    def rollback_to_snapshot(self, cycle: int) -> dict[str, Any] | None:
        snapshots = list(self._state.get("snapshots") or [])
        if not snapshots:
            return None
        target = max(
            (item for item in snapshots if int(item.get("cycle") or 0) <= int(cycle)),
            key=lambda item: int(item.get("cycle") or 0),
            default=None,
        )
        if target is None:
            return None
        path = self.dir / SNAPSHOTS_DIR / str(target.get("file") or "")
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        validate_registry(payload)
        shutil.copyfile(path, self.active_path)
        self._entries = validate_registry(payload)
        self._state["rolled_back_to"] = {
            "cycle": int(cycle),
            "snapshot": str(target.get("file") or ""),
            "hash": str(payload.get("content_hash") or ""),
        }
        self._save_state()
        return payload

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def clear(self) -> None:
        payload = empty_registry()
        self.active_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._entries = []
        self._state = {
            "cycle": 0,
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
