"""AST checks for production definition ownership and import boundaries."""
from __future__ import annotations

import ast
from collections import defaultdict
from importlib.util import resolve_name
from pathlib import Path


def audit_sources(root: Path) -> dict:
    modules = {}
    for package in ("detector", "evolution", "experiments"):
        for path in sorted((root / package).glob("*.py")):
            name = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
            modules[name.removesuffix(".__init__")] = path
    duplicates, violations = [], []
    graph = defaultdict(set)

    def definitions(body, path, scope):
        names = defaultdict(list)
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names[node.name].append(node)
                definitions(node.body, path, f"{scope}.{node.name}")
        for name, nodes in names.items():
            # Property getter/setter definitions intentionally share a name.
            ordinary = [n for n in nodes if not any(
                isinstance(d, ast.Attribute) and d.attr in {"setter", "deleter"}
                for d in n.decorator_list)]
            if len(ordinary) > 1:
                duplicates.append({"file": path, "scope": scope, "name": name,
                                   "lines": [n.lineno for n in ordinary]})

    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        relative = path.relative_to(root).as_posix()
        definitions(tree.body, relative, name)
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = resolve_name("." * node.level + base, package)
                imports = [base, *[f"{base}.{alias.name}" for alias in node.names]]
            for imported in imports:
                if name.split(".")[0] == "detector" and imported.split(".")[0] in {"evolution", "experiments"}:
                    violations.append({"file": relative, "line": node.lineno, "import": imported})
                if imported in modules and imported != name:
                    graph[name].add(imported)

    visited, stack, cycles = set(), [], []

    def visit(name):
        if name in stack:
            cycle = stack[stack.index(name):] + [name]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        if name in visited:
            return
        stack.append(name)
        for imported in sorted(graph[name]):
            visit(imported)
        stack.pop()
        visited.add(name)

    for name in sorted(modules):
        visit(name)
    return {"files": len(modules), "duplicate_definitions": duplicates,
            "boundary_violations": violations, "import_cycles": cycles,
            "passed": not (duplicates or violations or cycles)}
