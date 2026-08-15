"""Static layer-dependency checker for the sehd_rise2 package.

Enforces the frozen boundary between the minimal detector package and the
future evolution framework:

    detector   -> nothing outside itself (flat package)
    evolution  -> detector (one-way)
    root       -> detector, evolution

Forbidden everywhere: imports of the removed packages (``sehd_rise``
without the ``2``, ``sehd_v6``) and relative imports.

Run from the repository root:

    python sehd_rise2/tools/check_layers.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT
PACKAGE_PREFIX = "sehd_rise2"

ROOT_LAYER = "root"
DETECTOR_LAYER = "detector"
EVOLUTION_LAYER = "evolution"

LAYER_RULES: dict[str, frozenset[str]] = {
    ROOT_LAYER: frozenset({DETECTOR_LAYER, EVOLUTION_LAYER}),
    DETECTOR_LAYER: frozenset(),
    EVOLUTION_LAYER: frozenset({DETECTOR_LAYER}),
}


def module_layer(path: Path) -> str:
    """Map a source file path under the package to its layer."""
    rel = path.relative_to(PACKAGE_ROOT)
    if len(rel.parts) == 1:
        return ROOT_LAYER
    return (
        DETECTOR_LAYER if rel.parts[0] == "detector"
        else EVOLUTION_LAYER if rel.parts[0] == "evolution"
        else ROOT_LAYER
    )


def import_layer(dotted: str) -> str | None:
    """Map an imported module name to its layer; None if not intra-package."""
    if not (dotted == PACKAGE_PREFIX or dotted.startswith(PACKAGE_PREFIX + ".")):
        return None
    rest = dotted[len(PACKAGE_PREFIX):].lstrip(".")
    parts = [p for p in rest.split(".") if p]
    if not parts:
        return ROOT_LAYER
    if parts[0] == "detector":
        return DETECTOR_LAYER
    if parts[0] == "evolution":
        return EVOLUTION_LAYER
    return ROOT_LAYER


def _is_removed_package(dotted: str) -> bool:
    """Imports of the removed trees are forbidden: ``sehd_rise`` without the
    ``2`` and ``sehd_v6`` no longer exist in the repository."""
    if dotted == "sehd_rise" or dotted.startswith("sehd_rise."):
        return True
    if dotted == "sehd_v6" or dotted.startswith("sehd_v6."):
        return True
    return False


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    source_layer = module_layer(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                violations.append(
                    f"{path.name}:{node.lineno}: relative import is forbidden"
                )
                continue
            targets = [node.module or ""]
        else:
            continue
        for target in targets:
            if _is_removed_package(target):
                violations.append(
                    f"{path.name}:{node.lineno}: import of the old package "
                    f"{target!r} is forbidden inside sehd_rise2"
                )
                continue
            target_layer = import_layer(target)
            if target_layer is None:
                continue
            if target_layer == source_layer:
                continue
            allowed = LAYER_RULES.get(source_layer, frozenset())
            if target_layer not in allowed:
                violations.append(
                    f"{path.name}:{node.lineno}: layer {source_layer!r} may "
                    f"not import layer {target_layer!r} ({target})"
                )
    return violations


def main() -> int:
    violations: list[str] = []
    checked = 0
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        checked += 1
        violations.extend(check_file(path))
    if violations:
        print("layer check FAILED:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print(f"layer check OK: {checked} file(s), no violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
