"""Static layer-dependency checker for the sehd_rise package.

Implements the acyclic layer rules of SEHD_RISE_Refactor_Execution_Plan.md
§2.2 and the import discipline of §2.4: absolute imports only, no sehd_v6
references inside the new package, and no imports from the package root
(the root exports nothing). Run from the repo root:

    python tools/parity/check_layers.py

Exits 1 and prints every violation when any rule is broken. Also asserts
the scaffold itself is complete (every layer directory present with an
__init__.py), so a green run on the empty Phase 0 skeleton is a real
assertion rather than a vacuous pass.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "sehd_rise"
PACKAGE_PREFIX = "sehd_rise"
FROZEN_PACKAGE = "sehd_v6"

# Plan §2.2: value = the layers the key layer may import. Intra-layer
# imports are always allowed and not listed. The plan groups api/cli with
# the same target set; cli additionally may import api because the shell
# must reach its facade.
LAYER_RULES: dict[str, frozenset[str]] = {
    "platform": frozenset(),
    "skills": frozenset({"platform"}),
    "detection": frozenset({"platform", "skills"}),
    "causal": frozenset({"platform", "skills", "detection"}),
    "experience": frozenset({"platform", "skills", "causal"}),
    "typing": frozenset({"platform", "skills", "causal", "experience"}),
    "planning": frozenset({"platform", "skills", "causal", "experience", "typing"}),
    "execution": frozenset({"platform", "skills", "detection", "causal"}),
    "evaluation": frozenset({"platform", "skills", "causal", "execution"}),
    "meta": frozenset({"platform", "causal", "experience", "evaluation"}),
    "loop": frozenset({
        "platform", "skills", "detection", "causal", "experience",
        "typing", "planning", "execution", "evaluation", "meta",
    }),
    "api": frozenset({"loop", "detection", "evaluation", "planning"}),
    "cli": frozenset({"api", "loop", "detection", "evaluation", "planning"}),
}

ROOT_LAYER = "<root>"


def module_layer(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT)
    if len(relative.parts) == 1:
        return ROOT_LAYER
    return relative.parts[0]


def import_layer(dotted: str) -> str | None:
    """Map an imported module name to its layer; None if not intra-package."""
    if not (dotted == PACKAGE_PREFIX or dotted.startswith(PACKAGE_PREFIX + ".")):
        return None
    remainder = dotted[len(PACKAGE_PREFIX):].lstrip(".")
    if not remainder:
        return ROOT_LAYER
    return remainder.split(".")[0]


def check_file(path: Path) -> list[str]:
    source_layer = module_layer(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                violations.append(
                    f"{path.name}:{node.lineno}: relative import is forbidden "
                    "(plan §2.4 rule 1: absolute imports only)"
                )
                continue
            targets = [node.module or ""]
        else:
            continue
        for target in targets:
            if FROZEN_PACKAGE in target.split("."):
                violations.append(
                    f"{path.name}:{node.lineno}: import of the frozen "
                    f"reference package {target!r} is forbidden inside "
                    "sehd_rise (only tools/parity may reference both)"
                )
                continue
            target_layer = import_layer(target)
            if target_layer is None:
                continue
            if target_layer == ROOT_LAYER:
                violations.append(
                    f"{path.name}:{node.lineno}: import the leaf module, "
                    "not the package root (the root exports nothing)"
                )
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


def check_scaffold() -> list[str]:
    missing = [
        f"layer directory missing or lacks __init__.py: {layer}"
        for layer in sorted(LAYER_RULES)
        if not (PACKAGE_ROOT / layer / "__init__.py").is_file()
    ]
    if not (PACKAGE_ROOT / "__init__.py").is_file():
        missing.append("package root __init__.py missing")
    return missing


def main() -> int:
    violations = check_scaffold()
    checked = 0
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        checked += 1
        violations.extend(check_file(path))
    if violations:
        print("layer check FAILED:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print(f"layer check OK: {checked} file(s), {len(LAYER_RULES)} layers, no violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
