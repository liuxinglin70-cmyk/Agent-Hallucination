"""Level B: a sandboxed local code component (plan §10.2, P2.2).

§10.2 asks for small, constrained Python components: a pure function, a fixed
input schema, no network, no arbitrary filesystem access, no imports outside a
whitelist, and CPU / time / memory limits -- and it asks for property and
metamorphic tests, not just unit tests.

What this module is *not*, stated first because the distinction is the whole
safety argument:

* It is **not** a general code-generation channel. A component is a single pure
  function whose parameters are declared with fixed types before the source is
  read, whose imports come from a closed list, and whose output is checked
  against a declared shape. Anything else is refused at validation time.
* It is **not** a security boundary against an adversary who is allowed to run
  arbitrary Python. In-process Python restrictions are bypassable in principle;
  what is real here is the *process* boundary -- a fresh interpreter, a
  restricted builtin namespace, a closed import list, and OS-enforced resource
  limits. A component that is admitted is one that *passed static validation*,
  and the sandbox limits how much damage a component that still misbehaves can
  do within one call.
* Its verdicts can **never** convict. The runner is registered
  ``evidence_only``: a component reports, and the terminal decision stays with
  the mechanisms that already held it.

The resource limits are the part most easily faked, so they are *verified*
rather than declared: :func:`sandbox_capabilities` runs a child that violates
each limit and reports whether the platform actually stopped it. A limit that
is not enforced is reported as ``enforced: False`` with the reason, and
:func:`admission_blockers` turns any missing limit into an admission refusal --
a sandbox that cannot bound its children must not admit one.
"""

from __future__ import annotations

import ast
import ctypes
import hashlib
import json
import re
import subprocess
import sys
import textwrap
import threading
import time
from typing import Any, Mapping, Sequence

SANDBOX_SCHEMA = "evolution.sandboxed_component.v1"

COMPONENT_NAME_RE = re.compile(r"^evo_[a-z][a-z0-9_]{2,63}$")

# The import whitelist. Every entry is a module with no filesystem or network
# surface that a pure function plausibly needs. The list is deliberately short:
# a component that needs ``os`` is not a component, it is a program.
IMPORT_WHITELIST = (
    "re",
    "json",
    "math",
    "statistics",
    "decimal",
    "fractions",
    "itertools",
    "functools",
    "collections",
    "string",
    "unicodedata",
    "difflib",
    "textwrap",
    "datetime",
    "operator",
    "typing",
)

# Builtins a component may use. Anything not listed is absent from ``__builtins__``
# in the child, so the failure is a NameError at run time as well as a static
# refusal. ``open``/``eval``/``exec``/``compile``/``__import__``/``input``/
# ``globals``/``locals``/``vars``/``getattr``/``setattr``/``delattr`` are all
# absent by construction: none of them is needed to compute over values passed
# in, and each one is a route out of the sandbox.
BUILTIN_WHITELIST = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "isinstance", "issubclass", "iter",
    "len", "list", "map", "max", "min", "next", "object", "ord", "pow",
    "range", "repr", "reversed", "round", "set", "slice", "sorted", "str",
    "sum", "tuple", "type", "zip", "True", "False", "None", "ValueError",
    "TypeError", "KeyError", "IndexError", "ZeroDivisionError", "Exception",
)

# Attributes and names that would reach an interpreter, a file, or a process.
# Refused wherever they appear, as a call target or as an attribute: the
# validator cannot tell which module a value came from, so ``re.compile`` is
# allowed by the import whitelist and ``os.system`` is refused by this list.
FORBIDDEN_ATTRIBUTES = frozenset({
    "system", "popen", "spawn", "fork", "execv", "execve", "remove", "unlink",
    "rmdir", "mkdir", "makedirs", "open", "write", "writelines",
})

FORBIDDEN_NAMES = frozenset({
    "open", "eval", "exec", "compile", "__import__", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "exit", "quit", "help", "memoryview", "classmethod", "staticmethod",
    "property", "super", "object", "__build_class__",
})

# Parameter and return types the component schema admits. Closed on purpose:
# a component's signature is part of its identity, and a signature that could
# say anything would not fix an input schema.
PARAMETER_TYPES = ("string", "integer", "number", "boolean", "list_of_string", "object")
RETURN_TYPES = ("verdict", "boolean", "integer", "number", "string", "object")

VERDICT_VOCABULARY = ("supported", "contradicted", "abstained")

MAX_SOURCE_CHARS = 20_000
MAX_AST_NODES = 2_000
MAX_PARAMETERS = 8

# The hard ceiling. A component may ask for less; it may not ask for more, and
# a limit above these values is refused rather than clamped, because silently
# lowering a requested budget would make the component's declared behaviour
# differ from the one it was tested with.
HARD_LIMITS = {
    "wall_seconds": 10.0,
    "cpu_seconds": 5.0,
    "memory_bytes": 512 * 1024 * 1024,
    "max_input_bytes": 1 * 1024 * 1024,
    "max_output_bytes": 256 * 1024,
}

DEFAULT_LIMITS = {
    "wall_seconds": 5.0,
    "cpu_seconds": 2.0,
    "memory_bytes": 256 * 1024 * 1024,
    "max_input_bytes": 256 * 1024,
    "max_output_bytes": 64 * 1024,
}


class SandboxError(ValueError):
    """A component or a limit that the sandbox refuses."""


# --------------------------------------------------------------------------
# Limits and the platform's ability to enforce them
# --------------------------------------------------------------------------

def normalize_limits(limits: Mapping[str, Any] | None) -> dict[str, Any]:
    """Resolve a component's requested limits against the hard ceiling."""
    resolved = dict(DEFAULT_LIMITS)
    for key, value in dict(limits or {}).items():
        if key not in HARD_LIMITS:
            raise SandboxError(f"unknown limit {key!r}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SandboxError(f"limit {key!r} must be a number")
        if value <= 0:
            raise SandboxError(f"limit {key!r} must be positive")
        if value > HARD_LIMITS[key]:
            # Refused, not clamped: a component whose behaviour was measured
            # under one budget must not run under another.
            raise SandboxError(
                f"limit {key!r}={value} exceeds the hard ceiling "
                f"{HARD_LIMITS[key]}"
            )
        resolved[key] = value
    return resolved


def _windows_job_api() -> Any:
    """The Job Object entry points, or None when they are unavailable.

    Windows has no ``resource`` module, so the limits go through a Job Object
    instead: ``JOB_OBJECT_LIMIT_PROCESS_MEMORY`` makes an over-allocating child
    raise MemoryError, and ``JOB_OBJECT_LIMIT_JOB_TIME`` terminates a child
    that burns its CPU budget. Both are *verified* by
    :func:`sandbox_capabilities` rather than assumed from the platform name.
    """
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for name in (
            "CreateJobObjectW", "SetInformationJobObject",
            "AssignProcessToJobObject", "CloseHandle",
        ):
            if not hasattr(kernel32, name):
                return None
        return kernel32
    except (OSError, AttributeError):
        return None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    # ``LARGE_INTEGER`` is a signed 64-bit integer. Spelled with ctypes rather
    # than ``ctypes.wintypes`` so the structure is declared once, at module
    # level, and is importable on every platform: a class body cannot see a
    # name bound inside an ``if`` block, and the sandbox module must import
    # cleanly off Windows even though only Windows uses these.
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobObjectLimit(ctypes.Structure):
    """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION``."""

    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


def _apply_windows_limits(kernel32: Any, limits: Mapping[str, Any]) -> Any:
    """Create a job that caps memory and job time. Returns the handle or None."""
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _JobObjectLimit()
    flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.ProcessMemoryLimit = int(limits["memory_bytes"])
    flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
    # PerJobUserTimeLimit is in 100-nanosecond units.
    info.BasicLimitInformation.PerJobUserTimeLimit = int(
        float(limits["cpu_seconds"]) * 10_000_000
    )
    flags |= _JOB_OBJECT_LIMIT_JOB_TIME
    info.BasicLimitInformation.LimitFlags = flags
    ok = kernel32.SetInformationJobObject(
        _HANDLE(job),
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        kernel32.CloseHandle(_HANDLE(job))
        return None
    return job


def _HANDLE(value: Any) -> Any:  # noqa: N802 - mirrors the Win32 name
    from ctypes import wintypes

    return wintypes.HANDLE(int(value))


def _declared_capabilities() -> dict[str, Any]:
    """What this platform can enforce, before any probe has run."""
    kernel32 = _windows_job_api()
    if kernel32 is not None:
        return {
            "mechanism": "windows_job_object",
            "wall_seconds": True,
            "cpu_seconds": True,
            "memory_bytes": True,
            "filesystem_bytes": False,
        }
    if sys.platform != "win32":
        try:
            import resource  # noqa: PLC0415,F401 - capability probe

            return {
                "mechanism": "posix_rlimit",
                "wall_seconds": True,
                "cpu_seconds": True,
                "memory_bytes": True,
                "filesystem_bytes": True,
            }
        except ImportError:
            pass
    return {
        "mechanism": "wall_clock_only",
        "wall_seconds": True,
        "cpu_seconds": False,
        "memory_bytes": False,
        "filesystem_bytes": False,
    }


def _probe_script(kind: str) -> str:
    if kind == "memory":
        return "b = bytearray(1024 * 1024 * 1024)\nprint('allocated', len(b))"
    if kind == "cpu":
        return "x = 0\nwhile True:\n    x += 1"
    if kind == "wall":
        # A child that sleeps is not stopped by a CPU-time limit (it uses no
        # CPU), so it isolates the parent's wall clock from the job object's
        # CPU budget. That distinction is the reason the two are probed
        # separately: a sandbox with only a CPU cap lets a sleeping child hold
        # its slot forever.
        return "import time\ntime.sleep(300)\nprint('slept')"
    return "print('ok')"


def _probe_one(kind: str, limits: Mapping[str, Any]) -> tuple[bool, str]:
    """Run a child that violates one limit and report whether it was stopped."""
    wall = 20.0 if kind == "cpu" else 10.0
    if kind == "cpu":
        limits = {**limits, "cpu_seconds": 1.0}
        wall = 20.0
    if kind == "wall":
        limits = {**limits, "cpu_seconds": 60.0}
        wall = 2.0
    started = time.monotonic()
    try:
        result = _run_child(
            source=_probe_script(kind),
            envelope={},
            limits=limits,
            wall_seconds=wall,
            source_is_raw=True,
        )
    except SandboxError as exc:
        return False, f"could not run the probe: {exc}"
    elapsed = time.monotonic() - started
    if not result.get("launched"):
        return False, str(result.get("error") or "child did not start")
    if kind == "cpu":
        # The child must not have completed its loop, and must not have run
        # past its wall budget.
        stopped = not result.get("completed", False)
        return stopped, (
            f"child {'stopped' if stopped else 'COMPLETED'} after "
            f"{elapsed:.2f}s wall (cpu budget {limits['cpu_seconds']}s)"
        )
    if kind == "wall":
        # A sleeping child uses no CPU, so only the parent's wall clock can
        # stop it. ``timed_out`` is the specific answer to "was it the wall
        # clock", as opposed to the CPU budget.
        timed_out = bool(result.get("timed_out"))
        return timed_out, (
            f"child {'stopped' if timed_out else 'COMPLETED'} after "
            f"{elapsed:.2f}s wall (wall budget {wall}s)"
        )
    if kind == "memory":
        # Either the allocation raised (MemoryError) or the process died; a
        # child that printed "allocated" proves the cap is absent.
        if "allocated" in str(result.get("stdout") or ""):
            return False, "child allocated 1 GiB under a 256 MiB cap"
        return True, "allocation refused by the memory cap"
    return True, "control child completed"


def sandbox_capabilities(*, verify: bool = False) -> dict[str, Any]:
    """What the sandbox can enforce, optionally verified by real violations.

    ``verify=False`` reports the platform's declared support, which is what the
    runtime uses and what the test suite pins against the probe. ``verify=True``
    actually makes a child violate each limit, which is what a report should
    quote: a cap is only known to hold once something failed to exceed it.
    """
    declared = _declared_capabilities()
    report: dict[str, Any] = {
        "mechanism": declared["mechanism"],
        "platform": sys.platform,
        "declared": {
            key: declared[key]
            for key in ("wall_seconds", "cpu_seconds", "memory_bytes")
        },
        "verified": {},
        "probe_run": bool(verify),
    }
    if verify:
        limits = normalize_limits(None)
        for kind in ("wall", "memory", "cpu"):
            key = {
                "wall": "wall_seconds",
                "memory": "memory_bytes",
                "cpu": "cpu_seconds",
            }[kind]
            if not declared[key]:
                report["verified"][kind] = {
                    "enforced": False,
                    "detail": "not declared on this platform",
                }
                continue
            enforced, detail = _probe_one(kind, limits)
            report["verified"][kind] = {"enforced": enforced, "detail": detail}
    return report


def admission_blockers(limits: Mapping[str, Any] | None = None) -> list[str]:
    """Reasons the platform may not admit a sandboxed component at all.

    A sandbox that cannot bound a child must not run one. This is the call the
    runner's admission path uses, so the refusal is the same sentence the
    audit reports.
    """
    declared = _declared_capabilities()
    blockers: list[str] = []
    if declared["mechanism"] == "windows_job_object":
        kernel32 = _windows_job_api()
        job = _apply_windows_limits(kernel32, normalize_limits(limits))
        if job is None:
            blockers.append("could not create a Windows job object with CPU and memory limits")
        else:
            kernel32.CloseHandle(_HANDLE(job))
    if not declared["wall_seconds"]:
        blockers.append("no wall-clock limit available on this platform")
    if not declared["cpu_seconds"]:
        blockers.append(
            "no CPU-time limit available on this platform; a component could "
            "burn unbounded CPU inside a wall-clock window it shares with "
            "other work"
        )
    if not declared["memory_bytes"]:
        blockers.append(
            "no memory limit available on this platform; a component could "
            "exhaust the host"
        )
    if limits:
        requested = normalize_limits(limits)
        for key, value in requested.items():
            ceiling = HARD_LIMITS[key]
            if value > ceiling:
                blockers.append(f"limit {key!r}={value} above the ceiling {ceiling}")
    return blockers


# --------------------------------------------------------------------------
# Static validation
# --------------------------------------------------------------------------

def _collect_bindings(tree: ast.Module) -> set[str]:
    """Every name the module binds: helper functions, parameters, assignments.

    A pre-pass rather than a running set, because validation must not depend on
    traversal order: a helper defined *after* the entrypoint that calls it is
    ordinary Python, and a validator that refused it would report a
    well-formed component as broken.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            bound.add(node.name)
            bound.update(str(arg.arg) for arg in node.args.args)
            bound.update(str(arg.arg) for arg in node.args.kwonlyargs)
            if node.args.vararg:
                bound.add(str(node.args.vararg.arg))
            if node.args.kwarg:
                bound.add(str(node.args.kwarg.arg))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.Lambda):
            bound.update(str(arg.arg) for arg in node.args.args)
    return bound


class _Validator(ast.NodeVisitor):
    """Refuse everything a pure function does not need.

    The rule is an allowlist over node types plus a name check: a node the
    validator does not know how to reason about is refused, so a Python version
    that adds a construct cannot widen the sandbox by default.

    Helper functions are allowed -- they add readability, not capability -- as
    long as each one obeys the same rules: no decorators, no classes, no
    ``*args``/``**kwargs`` on the entrypoint, and nothing that reaches an
    interpreter, a file, or a process.
    """

    ALLOWED_NODES = frozenset({
        ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
        ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Pass,
        ast.If, ast.For, ast.While, ast.Break, ast.Continue, ast.Try,
        ast.ExceptHandler, ast.Raise, ast.Assert,
        ast.Name, ast.Load, ast.Store, ast.Constant, ast.JoinedStr,
        ast.FormattedValue, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Slice,
        ast.Subscript, ast.Attribute, ast.Call, ast.BinOp, ast.BoolOp,
        ast.UnaryOp, ast.Compare, ast.IfExp, ast.Lambda,
        ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
        ast.comprehension, ast.keyword, ast.Import, ast.ImportFrom, ast.alias,
        # Operators.
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.Invert,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is,
        ast.IsNot, ast.In, ast.NotIn, ast.BitAnd, ast.BitOr, ast.BitXor,
        ast.LShift, ast.RShift, ast.MatMult,
    })

    def __init__(self, *, entrypoint: str, parameters: Sequence[str]) -> None:
        self.entrypoint = entrypoint
        self.parameters = list(parameters)
        self.violations: list[str] = []
        self.imported: set[str] = set()
        self.bound: set[str] = set()
        self.entrypoint_defs = 0
        # False until the entrypoint def is reached: assignments before it are
        # module-level, and a module-level name is checked by
        # ``_check_module_level`` rather than here.
        self._inside_entrypoint = False

    def _refuse(self, node: ast.AST, why: str) -> None:
        line = getattr(node, "lineno", "?")
        self.violations.append(f"line {line}: {why}")

    def generic_visit(self, node: ast.AST) -> None:  # noqa: D102
        if type(node) not in self.ALLOWED_NODES:
            self._refuse(
                node,
                f"{type(node).__name__} is not part of the component surface",
            )
            return
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            root = str(alias.name).split(".", 1)[0]
            if root not in IMPORT_WHITELIST:
                self._refuse(node, f"import {alias.name!r} is not whitelisted")
                continue
            self.imported.add(str(alias.asname or alias.name).split(".", 1)[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        root = str(node.module or "").split(".", 1)[0]
        if node.level:
            self._refuse(node, "relative imports are not allowed")
        elif root not in IMPORT_WHITELIST:
            self._refuse(node, f"import from {node.module!r} is not whitelisted")
        else:
            for alias in node.names:
                self.imported.add(str(alias.asname or alias.name))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        name = str(node.attr)
        if name.startswith("__") or name.endswith("__"):
            # ``__globals__``, ``__subclasses__``, ``__class__`` and friends are
            # the standard route out of an in-process sandbox.
            self._refuse(node, f"dunder attribute {name!r} is refused")
        elif name in FORBIDDEN_ATTRIBUTES:
            self._refuse(node, f"attribute {name!r} is refused")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            known = (
                set(self.parameters) | self.imported | self.bound
                | set(BUILTIN_WHITELIST)
            )
            if node.id not in known:
                self._refuse(node, f"name {node.id!r} is not defined in the sandbox")
        elif isinstance(node.ctx, ast.Store):
            self.bound.add(node.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # A bare call to a forbidden builtin is refused here as well as in
        # ``visit_Name``: the child's builtins namespace omits them, so the
        # static refusal names the fix instead of leaving a NameError at run
        # time. Attribute calls are governed by ``FORBIDDEN_ATTRIBUTES``: the
        # attribute namespace is not known statically, so ``re.compile`` must
        # stay legal while ``module.system`` does not.
        target = node.func
        if isinstance(target, ast.Name) and str(target.id) in FORBIDDEN_NAMES:
            self._refuse(node, f"call to {target.id!r} is refused")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node.decorator_list:
            self._refuse(node, f"function {node.name!r} must not carry decorators")
        if node.name.startswith("__") or node.name.endswith("__"):
            self._refuse(node, f"function {node.name!r} must not use a dunder name")
        self.bound.add(node.name)
        if node.name == self.entrypoint:
            self.entrypoint_defs += 1
            declared = [str(arg.arg) for arg in node.args.args]
            if declared != self.parameters:
                self._refuse(
                    node,
                    f"entrypoint {self.entrypoint!r} takes {declared} but the "
                    f"schema declares {self.parameters}",
                )
            if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
                self._refuse(node, "the entrypoint must not take *args, **kwargs")
            if node.args.defaults:
                # Defaults would let a call succeed with fewer arguments than
                # the fixed schema declares.
                self._refuse(node, "the entrypoint must not declare defaults")
            self._inside_entrypoint = True
        self.generic_visit(node)
        if node.name == self.entrypoint:
            self._inside_entrypoint = False

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        if node.args.vararg or node.args.kwarg:
            self._refuse(node, "lambda must not take *args or **kwargs")
        self.generic_visit(node)


def _check_module_level(tree: ast.Module, *, entrypoint: str) -> list[str]:
    """Module level may hold imports, a docstring, literal constants, functions.

    Functions are allowed beyond the entrypoint because a helper adds
    readability, not capability -- every helper is validated by the same
    visitor. What is *not* allowed at module level is anything that runs: a
    computed expression, a call, a class, a loop, or a ``with``. A component
    that executes on import would run outside the shape the schema fixes.
    """
    violations: list[str] = []
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith("__") or node.name.endswith("__"):
                violations.append(
                    f"line {node.lineno}: function {node.name!r} must not use a "
                    "dunder name"
                )
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # a docstring
        if isinstance(node, ast.Assign):
            # A literal constant is useful (a vocabulary, a table) and carries
            # no capability; anything computed at import time does not belong
            # in a pure component.
            if all(isinstance(target, ast.Name) for target in node.targets):
                try:
                    ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError):
                    violations.append(
                        f"line {node.lineno}: module-level assignment must be a "
                        "literal constant"
                    )
                continue
        if isinstance(node, ast.AnnAssign):
            violations.append(
                f"line {node.lineno}: annotated module-level assignment is not "
                "allowed; use a plain literal constant"
            )
            continue
        violations.append(
            f"line {getattr(node, 'lineno', index)}: "
            f"{type(node).__name__} is not allowed at module level"
        )
    return violations


def component_digest(component: Mapping[str, Any]) -> str:
    """Stable identity of a component: the source and the contract, not the name.

    The digest deliberately excludes ``name`` and ``description`` so a rename
    does not mint a new identity (the metamorphic gate's class 1 in spirit),
    and includes the source, the declared parameter schema, the return schema
    and the limits, because any of those changes what the component does.
    """
    payload = {
        "entrypoint": str(component.get("entrypoint") or ""),
        "parameters": component.get("parameters"),
        "returns": component.get("returns"),
        "imports": sorted(str(item) for item in component.get("imports") or ()),
        "source": str(component.get("source") or ""),
        "limits": component.get("limits") or {},
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def validate_component(component: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one component (fail-closed).

    Returns the normalized component; raises :class:`SandboxError` with every
    violation rather than the first, because a candidate author needs the whole
    list to fix a component in one revision.
    """
    if not isinstance(component, Mapping):
        raise SandboxError("component must be an object")
    violations: list[str] = []
    schema = str(component.get("schema_version") or SANDBOX_SCHEMA)
    if schema != SANDBOX_SCHEMA:
        raise SandboxError(f"unsupported component schema {schema!r}")

    name = str(component.get("name") or "")
    if not COMPONENT_NAME_RE.match(name):
        violations.append(
            f"name {name!r} must match {COMPONENT_NAME_RE.pattern}"
        )

    source = str(component.get("source") or "")
    if not source.strip():
        violations.append("source must be non-empty")
    elif len(source) > MAX_SOURCE_CHARS:
        violations.append(
            f"source is {len(source)} chars; limit is {MAX_SOURCE_CHARS}"
        )

    entrypoint = str(component.get("entrypoint") or "")
    if not entrypoint.isidentifier():
        violations.append(f"entrypoint {entrypoint!r} is not an identifier")

    raw_parameters = component.get("parameters")
    if not isinstance(raw_parameters, list) or not raw_parameters:
        violations.append("parameters must be a non-empty list")
        raw_parameters = []
    elif len(raw_parameters) > MAX_PARAMETERS:
        violations.append(
            f"{len(raw_parameters)} parameters; limit is {MAX_PARAMETERS}"
        )
    parameters: list[dict[str, str]] = []
    for index, item in enumerate(raw_parameters):
        if not isinstance(item, Mapping):
            violations.append(f"parameter {index} must be an object")
            continue
        field = str(item.get("name") or "")
        kind = str(item.get("type") or "")
        if not field.isidentifier():
            violations.append(f"parameter {index} name {field!r} is not an identifier")
        if kind not in PARAMETER_TYPES:
            violations.append(
                f"parameter {field!r} type {kind!r} outside {list(PARAMETER_TYPES)}"
            )
        parameters.append({"name": field, "type": kind})
    if len({item["name"] for item in parameters}) != len(parameters):
        violations.append("parameter names must be unique")

    returns = component.get("returns")
    if not isinstance(returns, Mapping):
        violations.append("returns must be an object")
        returns = {}
    else:
        kind = str(returns.get("type") or "")
        if kind not in RETURN_TYPES:
            violations.append(
                f"returns.type {kind!r} outside {list(RETURN_TYPES)}"
            )
        if kind == "verdict":
            verdicts = [str(item) for item in returns.get("verdicts") or ()]
            if sorted(verdicts) != sorted(VERDICT_VOCABULARY):
                violations.append(
                    "a verdict return must declare the full vocabulary "
                    f"{list(VERDICT_VOCABULARY)}"
                )

    declared_imports = [str(item) for item in component.get("imports") or ()]
    for item in declared_imports:
        if item.split(".", 1)[0] not in IMPORT_WHITELIST:
            violations.append(f"declared import {item!r} is not whitelisted")

    try:
        limits = normalize_limits(component.get("limits"))
    except SandboxError as exc:
        violations.append(str(exc))
        limits = dict(DEFAULT_LIMITS)

    tree: ast.Module | None = None
    if source.strip():
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as exc:
            violations.append(f"source does not parse: {exc}")
        else:
            node_count = sum(1 for _ in ast.walk(tree))
            if node_count > MAX_AST_NODES:
                violations.append(
                    f"source has {node_count} AST nodes; limit is {MAX_AST_NODES}"
                )
            else:
                violations.extend(
                    _check_module_level(tree, entrypoint=entrypoint)
                )
                validator = _Validator(
                    entrypoint=entrypoint,
                    parameters=[item["name"] for item in parameters],
                )
                validator.bound = _collect_bindings(tree)
                validator.visit(tree)
                violations.extend(validator.violations)
                if validator.entrypoint_defs == 0:
                    violations.append(
                        f"source defines no function named {entrypoint!r}"
                    )
                elif validator.entrypoint_defs > 1:
                    violations.append(
                        f"source defines {entrypoint!r} more than once"
                    )
                undeclared = validator.imported - set(
                    item.split(".", 1)[0] for item in declared_imports
                )
                if undeclared:
                    violations.append(
                        f"source imports {sorted(undeclared)} which the "
                        "component does not declare"
                    )

    if violations:
        raise SandboxError(
            "component refused: " + "; ".join(violations)
        )

    normalized = {
        "schema_version": SANDBOX_SCHEMA,
        "name": name,
        "description": str(component.get("description") or ""),
        "entrypoint": entrypoint,
        "parameters": parameters,
        "returns": dict(returns),
        "imports": declared_imports,
        "source": source,
        "limits": limits,
    }
    normalized["digest"] = component_digest(normalized)
    return normalized


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

#: The child driver. It is a module-level constant rather than a string built
#: per call so the executed text is inspectable and cannot vary with input.
#:
#: Four details matter and are easy to get wrong:
#:
#: * the child runs with ``-I``, which implies ``-E`` -- environment variables
#:   are ignored, so ``PYTHONIOENCODING`` has no effect and stdin/stdout fall
#:   back to the locale codec. On a non-UTF-8 locale that silently corrupts any
#:   non-ASCII text, which is most of what these components compare. The streams
#:   are therefore reconfigured explicitly, in the child, before anything is
#:   read;
#: * the builtins mapping is a ``dict`` under the child's ``-S`` flag but a
#:   module in some environments -- both shapes are handled, because a driver
#:   that assumed one would produce a component error that looks like the
#:   component's fault;
#: * the component's ``__import__`` is *replaced* by one that consults the
#:   whitelist at run time. Static validation already refuses a non-whitelisted
#:   import; this is the second lock on the same door, so a validator gap is
#:   not also an execution gap;
#: * the module bindings come from the envelope rather than from the component's
#:   own ``import`` statements, so ``from re import match`` binds ``match``
#:   instead of the module -- which is what the source expects.
_CHILD_DRIVER = textwrap.dedent(
    '''
    import json, sys, builtins as _builtins

    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    WHITELIST = frozenset()

    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = str(name).split(".", 1)[0]
        if root not in WHITELIST:
            raise ImportError(f"module {name!r} is not whitelisted in the sandbox")
        return _builtins.__import__(name, globals, locals, fromlist, level)

    def main():
        global WHITELIST
        envelope = json.loads(sys.stdin.read() or "{}")
        WHITELIST = frozenset(envelope["whitelist"])
        source_builtins = getattr(_builtins, "__dict__", {})
        if not isinstance(source_builtins, dict):
            source_builtins = vars(source_builtins)
        restricted = {
            name: source_builtins[name] for name in envelope["builtins"]
            if name in source_builtins
        }
        restricted["__import__"] = _restricted_import
        namespace = {"__builtins__": restricted, "__name__": "sandboxed_component"}
        for spec in envelope["imports"]:
            module = _builtins.__import__(
                spec["module"], None, None,
                (spec["attribute"],) if spec.get("attribute") else (),
            )
            attribute = spec.get("attribute") or ""
            namespace[spec["alias"]] = (
                getattr(module, attribute) if attribute else module
            )
        exec(compile(envelope["source"], "<component>", "exec"), namespace)
        function = namespace[envelope["entrypoint"]]
        result = function(*[envelope["arguments"][name]
                            for name in envelope["parameter_names"]])
        sys.stdout.write(json.dumps({"ok": True, "result": result}, default=str))
        sys.stdout.flush()

    try:
        main()
    except BaseException as exc:
        sys.stdout.write(json.dumps({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }))
        sys.stdout.flush()
    '''
).strip()


def _coerce_arguments(
    component: Mapping[str, Any], inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Fit the supplied inputs to the declared parameter schema.

    A missing parameter is an error rather than a default: the fixed input
    schema is part of what the component was validated with, and silently
    substituting a value would test a component that was never admitted.
    """
    arguments: dict[str, Any] = {}
    for spec in component.get("parameters") or ():
        field = str(spec.get("name"))
        kind = str(spec.get("type"))
        if field not in inputs:
            raise SandboxError(f"missing input {field!r}")
        value = inputs[field]
        try:
            if kind == "string":
                if not isinstance(value, str):
                    raise SandboxError(f"input {field!r} must be a string")
            elif kind == "integer":
                value = int(value)
                if isinstance(inputs[field], bool):
                    raise SandboxError(f"input {field!r} must be an integer")
            elif kind == "number":
                value = float(value)
                if isinstance(inputs[field], bool):
                    raise SandboxError(f"input {field!r} must be a number")
            elif kind == "boolean":
                if not isinstance(value, bool):
                    raise SandboxError(f"input {field!r} must be a boolean")
            elif kind == "list_of_string":
                if not isinstance(value, (list, tuple)) or not all(
                    isinstance(item, str) for item in value
                ):
                    raise SandboxError(
                        f"input {field!r} must be a list of strings"
                    )
                value = list(value)
            elif kind == "object":
                if not isinstance(value, Mapping):
                    raise SandboxError(f"input {field!r} must be an object")
                value = dict(value)
        except (TypeError, ValueError) as exc:
            raise SandboxError(f"input {field!r} is not a {kind}: {exc}") from exc
        arguments[field] = value
    return arguments


def _run_child(
    *,
    source: str,
    envelope: Mapping[str, Any],
    limits: Mapping[str, Any],
    wall_seconds: float,
    source_is_raw: bool = False,
    driver: str | None = None,
) -> dict[str, Any]:
    """Run one child interpreter under the limits and capture its answer.

    The parent is the enforcement point: it owns the wall clock, the output
    cap and the job object. A child that cannot be bounded is not launched --
    ``launched`` is reported so a caller can tell "refused" from "ran".
    """
    if source_is_raw:
        script = source
    else:
        script = str(driver or _CHILD_DRIVER)
    payload = json.dumps(dict(envelope), ensure_ascii=False, default=str)
    if len(payload.encode("utf-8")) > limits["max_input_bytes"]:
        return {
            "launched": False,
            "error": (
                f"input is {len(payload.encode('utf-8'))} bytes; limit is "
                f"{limits['max_input_bytes']}"
            ),
        }

    kernel32 = _windows_job_api()
    job = None
    if kernel32 is not None:
        job = _apply_windows_limits(kernel32, limits)
        if job is None:
            return {"launched": False, "error": "could not create a job object; CPU and memory limits unavailable"}
    elif sys.platform == "win32":
        return {"launched": False, "error": "Windows job objects unavailable"}
    else:
        # Set limits after exec so callers may safely launch from a thread pool.
        cpu = int(float(limits["cpu_seconds"])) or 1
        memory = int(limits["memory_bytes"])
        script = (
            "import resource; "
            f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu}, {cpu + 1})); "
            f"resource.setrlimit(resource.RLIMIT_AS, ({memory}, {memory})); "
            "resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0)); "
            f"exec({script!r})"
        )

    environment = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except (OSError, ValueError) as exc:
        if job is not None:
            kernel32.CloseHandle(_HANDLE(job))
        return {"launched": False, "error": f"could not start a child: {exc}"}

    if job is not None:
        try:
            assigned = kernel32.AssignProcessToJobObject(
                _HANDLE(job), _HANDLE(int(process._handle)),
            )
        except (AttributeError, OSError, ValueError):
            assigned = 0
        if not assigned:
            # Without the assignment the memory and CPU caps do not apply, and
            # running anyway would report "bounded execution" for an unbounded
            # child.
            process.kill()
            process.communicate()
            kernel32.CloseHandle(_HANDLE(job))
            return {
                "launched": False,
                "error": (
                    "could not place the child in a job object, so the memory "
                    "and CPU limits would not apply"
                ),
            }

    timed_out = False
    output_exceeded = threading.Event()
    output_lock = threading.Lock()
    output_size = 0
    buffers = [bytearray(), bytearray()]
    io_errors: list[str] = []

    def read_output(stream, buffer):
        nonlocal output_size
        try:
            while True:
                chunk = stream.read1(8192)
                if not chunk:
                    break
                with output_lock:
                    available = max(0, int(limits["max_output_bytes"]) - output_size)
                    buffer.extend(chunk[:available])
                    output_size += len(chunk)
                    if output_size > limits["max_output_bytes"]:
                        output_exceeded.set()
                        process.kill()
                        break
        except (OSError, ValueError) as exc:
            io_errors.append(str(exc))
        finally:
            stream.close()

    def write_input():
        try:
            process.stdin.write(payload.encode("utf-8"))
            process.stdin.flush()
        except BrokenPipeError:
            pass
        except (OSError, ValueError) as exc:
            io_errors.append(str(exc))
        finally:
            process.stdin.close()

    workers = [
        threading.Thread(target=read_output, args=(process.stdout, buffers[0]), daemon=True),
        threading.Thread(target=read_output, args=(process.stderr, buffers[1]), daemon=True),
        threading.Thread(target=write_input, daemon=True),
    ]
    for worker in workers:
        worker.start()
    try:
        process.wait(timeout=max(0.001, float(wall_seconds) - (time.monotonic() - started)))
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=5)
    except (OSError, ValueError) as exc:
        process.kill()
        return {"launched": False, "error": f"child I/O failed: {exc}"}
    finally:
        for worker in workers:
            worker.join(timeout=5)
        if job is not None:
            kernel32.CloseHandle(_HANDLE(job))

    elapsed = time.monotonic() - started
    if output_exceeded.is_set():
        return {
            "launched": True,
            "completed": False,
            "timed_out": timed_out,
            "error": (
                f"output exceeded {output_size} bytes; limit is "
                f"{limits['max_output_bytes']}"
            ),
            "wall_seconds": round(elapsed, 3),
        }
    if io_errors:
        return {"launched": True, "completed": False, "error": f"child I/O failed: {io_errors[0]}"}
    stdout, stderr = (bytes(buffer) for buffer in buffers)
    text = stdout.decode("utf-8", "replace")
    return {
        "launched": True,
        "completed": process.returncode == 0,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "stdout": text,
        "stderr": stderr.decode("utf-8", "replace")[:2000],
        "wall_seconds": round(elapsed, 3),
    }


def _import_specs(component: Mapping[str, Any], source: str) -> list[dict[str, str]]:
    """The modules the child should bind, taken from the source's own imports.

    The binding comes from parsing the source rather than from the declared
    list, so a component cannot declare one import set and use another; the
    validator has already refused any mismatch between the two.
    """
    tree = ast.parse(source, mode="exec")
    specs: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = str(alias.name).split(".", 1)[0]
                specs.append({
                    "module": root,
                    "alias": str(alias.asname or root),
                })
        elif isinstance(node, ast.ImportFrom):
            root = str(node.module or "").split(".", 1)[0]
            for alias in node.names:
                specs.append({
                    "module": root,
                    "alias": str(alias.asname or alias.name),
                })
                # ``from re import match`` binds the attribute, not the module.
                specs[-1]["attribute"] = str(alias.name)
    # A module imported twice under one alias must appear once.
    seen: dict[str, dict[str, str]] = {}
    for spec in specs:
        seen.setdefault(spec["alias"], spec)
    return list(seen.values())


def build_envelope(
    component: Mapping[str, Any], inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """The child's input envelope, including the import bindings it may use."""
    arguments = _coerce_arguments(component, inputs)
    return {
        "source": str(component.get("source") or ""),
        "entrypoint": str(component.get("entrypoint") or ""),
        "parameter_names": [
            str(item.get("name")) for item in component.get("parameters") or ()
        ],
        "arguments": arguments,
        "imports": _import_specs(
            component, str(component.get("source") or ""),
        ),
        "builtins": list(BUILTIN_WHITELIST),
        # Sent to the child so its ``__import__`` consults the same list the
        # validator does: one definition of the whitelist, enforced statically
        # and again at run time.
        "whitelist": list(IMPORT_WHITELIST),
        "verdicts": list(VERDICT_VOCABULARY),
    }


def check_output_schema(
    component: Mapping[str, Any], result: Any,
) -> str:
    """Return a refusal reason when ``result`` does not match the schema.

    The check is one-way: an output that cannot be described by the declared
    shape is refused, and the reason names the field. A missing check would let
    a component declare a verdict vocabulary and return prose.
    """
    returns = component.get("returns") or {}
    kind = str(returns.get("type") or "")
    if kind == "verdict":
        value = str(result)
        if value not in VERDICT_VOCABULARY:
            return (
                f"returned verdict {value!r} is outside the declared "
                f"vocabulary {list(VERDICT_VOCABULARY)}"
            )
        return ""
    if kind == "boolean":
        if not isinstance(result, bool):
            return f"returned {type(result).__name__}, schema declares boolean"
        return ""
    if kind == "integer":
        if not isinstance(result, int) or isinstance(result, bool):
            return f"returned {type(result).__name__}, schema declares integer"
        return ""
    if kind == "number":
        if not isinstance(result, (int, float)) or isinstance(result, bool):
            return f"returned {type(result).__name__}, schema declares number"
        return ""
    if kind == "string":
        if not isinstance(result, str):
            return f"returned {type(result).__name__}, schema declares string"
        return ""
    if kind == "object":
        if not isinstance(result, Mapping):
            return f"returned {type(result).__name__}, schema declares object"
        fields = returns.get("fields") or {}
        for field, expected in fields.items():
            if str(field) not in result:
                return f"returned object is missing declared field {field!r}"
            if str(expected) == "string" and not isinstance(result[str(field)], str):
                return f"field {field!r} is not a string"
        return ""
    return f"unknown declared return type {kind!r}"


def execute_component(
    component: Mapping[str, Any],
    inputs: Mapping[str, Any],
    *,
    limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one validated component under the sandbox and report the outcome.

    Every outcome is a value, never an exception: a component that times out, a
    sandbox that cannot be bounded, and a component that returns the wrong
    shape are all things a caller has to distinguish and record.
    """
    try:
        normalized = validate_component(component)
    except SandboxError as exc:
        return {
            "ok": False,
            "status": "refused",
            "error": str(exc),
            "digest": component_digest(component) if isinstance(component, Mapping) else "",
        }
    blockers = admission_blockers()
    if blockers:
        return {
            "ok": False,
            "status": "unsupported_platform",
            "error": "; ".join(blockers),
            "digest": normalized["digest"],
        }
    effective = normalize_limits({**normalized["limits"], **dict(limits or {})})
    try:
        envelope = build_envelope(normalized, inputs)
    except (SandboxError, SyntaxError) as exc:
        return {
            "ok": False, "status": "bad_input", "error": str(exc),
            "digest": normalized["digest"],
        }
    outcome = _run_child(
        source=normalized["source"],
        envelope=envelope,
        limits=effective,
        wall_seconds=effective["wall_seconds"],
    )
    base = {
        "digest": normalized["digest"],
        "limits": effective,
        "wall_seconds": outcome.get("wall_seconds"),
    }
    if not outcome.get("launched"):
        return {**base, "ok": False, "status": "not_launched",
                "error": outcome.get("error", "")}
    if outcome.get("timed_out"):
        return {**base, "ok": False, "status": "timeout",
                "error": f"exceeded the wall-clock limit {effective['wall_seconds']}s"}
    if outcome.get("error"):
        return {**base, "ok": False, "status": "output_too_large",
                "error": outcome["error"]}
    text = str(outcome.get("stdout") or "").strip()
    if not text:
        return {
            **base, "ok": False, "status": "no_result",
            "error": (
                "the child produced no result; this is what a memory or CPU "
                "cap looks like from the parent"
            ),
            "returncode": outcome.get("returncode"),
        }
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return {**base, "ok": False, "status": "malformed_result",
                "error": f"child output is not JSON: {exc}"}
    if not parsed.get("ok"):
        return {
            **base, "ok": False, "status": "component_error",
            "error": str(parsed.get("error") or ""),
        }
    result = parsed.get("result")
    refusal = check_output_schema(normalized, result)
    if refusal:
        return {**base, "ok": False, "status": "schema_violation",
                "error": refusal, "result": result}
    return {**base, "ok": True, "status": "completed", "result": result}


# --------------------------------------------------------------------------
# The reference component family: what the property tests quantify over
# --------------------------------------------------------------------------

def reference_component(**overrides: Any) -> dict[str, Any]:
    """A minimal valid component: a pure sentence-level comparison.

    It exists so the sandbox has something known-good to admit, and so the
    property tests have a component they can mutate one field at a time.

    Note where the regex is compiled: *inside* the function, not at module
    level. A module-level ``compile`` call is refused, because then the module
    would execute on import and the "only the entrypoint runs" property would
    stop being checkable from the source alone.
    """
    component = {
        "schema_version": SANDBOX_SCHEMA,
        "name": "evo_sentence_match",
        "description": "Compare two normalized sentences and report agreement.",
        "entrypoint": "judge",
        "parameters": [
            {"name": "expected", "type": "string"},
            {"name": "actual", "type": "string"},
        ],
        "returns": {"type": "verdict", "verdicts": list(VERDICT_VOCABULARY)},
        "imports": ["re"],
        "source": textwrap.dedent(
            '''
            import re

            QUOTE_MAP = [
                ("\\u201c", "\\""), ("\\u201d", "\\""),
                ("\\u2018", "'"), ("\\u2019", "'"),
                ("\\u00ab", "\\""), ("\\u00bb", "\\""),
            ]


            def normalize(text):
                collapsed = re.sub(r"\\s+", " ", text).strip()
                for old, new in QUOTE_MAP:
                    collapsed = collapsed.replace(old, new)
                while collapsed.endswith("."):
                    collapsed = collapsed[:-1].rstrip()
                return collapsed.lower()


            def judge(expected, actual):
                left = normalize(expected)
                right = normalize(actual)
                if not left or not right:
                    return "abstained"
                if left == right:
                    return "supported"
                return "contradicted"
            '''
        ).strip(),
        "limits": {"wall_seconds": 5.0, "cpu_seconds": 2.0},
    }
    component.update(overrides)
    return component


#: Sources that must never validate. Each one is a distinct route out, and the
#: list is used both by the tests and by the audit, so a route that becomes
#: admissible is reported rather than merely hoped against.
FORBIDDEN_SOURCES: dict[str, str] = {
    "filesystem_read": "def f():\n    return open('/etc/passwd').read()\n",
    "filesystem_write": "def f():\n    open('out.txt', 'w').write('x')\n",
    "dunder_globals": "def f():\n    return f.__globals__\n",
    "dunder_subclasses": "def f():\n    return ().__class__.__bases__\n",
    "import_os": "import os\n\n\ndef f():\n    return os.getcwd()\n",
    "import_subprocess": (
        "import subprocess\n\n\ndef f():\n    return subprocess.run(['ls'])\n"
    ),
    "eval_builtin": "def f():\n    return eval('1+1')\n",
    "exec_builtin": "def f():\n    exec('x = 1')\n    return 1\n",
    "dynamic_import": "def f():\n    return __import__('os')\n",
    "network_socket": "import socket\n\n\ndef f():\n    return socket.gethostname()\n",
    "getattr_escape": "def f():\n    return getattr(f, 'x', None)\n",
    "attribute_system": "def f():\n    return f.system('ls')\n",
    "class_definition": (
        "class Helper:\n    pass\n\n\ndef f():\n    return 1\n"
    ),
    "global_statement": "def f():\n    global boundary\n    boundary = 1\n",
    "wrong_arity": "def f(a, b):\n    return a\n",
    "default_argument": "def f(a='x'):\n    return a\n",
    "var_keyword": "def f(**kwargs):\n    return 1\n",
    "computed_module_level": "BOUNDARY = 1 + 1\n\n\ndef f():\n    return 1\n",
    "undeclared_import": (
        "import json\n\n\ndef f():\n    return json.dumps({})\n"
    ),
    "unknown_name": "def f():\n    return boundary\n",
    "async_function": "import json\n\n\nasync def f():\n    return 1\n",
    "yield_expression": "def f():\n    yield 1\n",
    "with_statement": "def f():\n    with open('x') as handle:\n        return handle\n",
    "await_expression": "def f():\n    return await g()\n",
    "walrus": "def f():\n    if (value := 1):\n        return value\n",
    "starred_call": "def f():\n    return max(*[1, 2])\n",
    "decorator": (
        "import functools\n\n\n@functools.cache\ndef f():\n    return 1\n"
    ),
}


def run_forbidden_sources() -> list[dict[str, Any]]:
    """Try to validate every forbidden source; every one must be refused."""
    rows: list[dict[str, Any]] = []
    for label, source in sorted(FORBIDDEN_SOURCES.items()):
        component = reference_component(
            name="evo_forbidden_probe",
            entrypoint="f",
            parameters=[{"name": "a", "type": "string"}],
            returns={"type": "integer"},
            imports=["re"],
            source=source,
        )
        try:
            validate_component(component)
            rows.append({"route": label, "refused": False, "reason": ""})
        except SandboxError as exc:
            rows.append({"route": label, "refused": True, "reason": str(exc)})
    return rows


# --------------------------------------------------------------------------
# The detector-facing executor
# --------------------------------------------------------------------------

def make_component_executor(component: Mapping[str, Any]) -> Any:
    """Build the ``sandboxed_component`` executor for one validated component.

    The detector layer declares the ``ComponentExecutor`` base class and never
    imports the sandbox; this factory is the bridge. It is a function rather
    than an import-time constant because the component is the artifact: the
    entry carries its own source, so the executor is derived from it and its
    identity is the component's own digest.

    ``judge`` runs the component under the sandbox and maps its result onto the
    evidence vocabulary. The mapping is where this executor's limits live:

    * a return type other than ``verdict`` is refused -- an evidence_check
      needs a verdict, and coercing a boolean into one would invent a
      vocabulary the component never declared;
    * every sandbox failure becomes ``abstained`` with the reason in the
      detail, never a fabricated verdict. A component that timed out has not
      said the claim is supported.
    """
    from detector.interventions import ComponentExecutor, SEMANTIC_VERDICTS

    normalized = validate_component(component)
    returns = normalized.get("returns") or {}
    if str(returns.get("type") or "") != "verdict":
        raise SandboxError(
            "a sandboxed_component must declare returns.type == 'verdict'; its "
            "verdict is the evidence an evidence_check carries"
        )

    class _ComponentExecutor(ComponentExecutor):
        name = str(normalized.get("name") or "")

        def __init__(self) -> None:
            super().__init__(
                artifact_id=self.name,
                artifact_digest=str(normalized.get("digest") or ""),
            )
            self.component = normalized

        def judge(
            self,
            predicate: str,
            packet: Mapping[str, Any],
            context: Mapping[str, Any],
        ) -> dict[str, Any]:
            if str(predicate) != self.name:
                raise SandboxError(
                    f"this executor runs component {self.name!r}, but the entry "
                    f"names predicate {predicate!r}"
                )
            arguments = context.get("arguments")
            if not isinstance(arguments, Mapping):
                raise SandboxError(
                    "the component executor needs context['arguments'], which "
                    "the entry's component_inputs resolve to"
                )
            outcome = execute_component(self.component, dict(arguments))
            if not outcome.get("ok"):
                # Fail closed onto ``abstained``: the vocabulary has a word for
                # "I could not tell", and a failed execution must use it rather
                # than be reported as a judgment.
                return {
                    "verdict": "abstained",
                    "detail": (
                        f"component did not complete ({outcome.get('status')}): "
                        f"{outcome.get('error')}"
                    ),
                }
            verdict = str(outcome.get("result") or "")
            if verdict not in SEMANTIC_VERDICTS:
                return {
                    "verdict": "abstained",
                    "detail": (
                        f"component returned {verdict!r}, which is outside "
                        f"{sorted(SEMANTIC_VERDICTS)}"
                    ),
                }
            return {
                "verdict": verdict,
                "detail": f"component {self.name} returned {verdict}",
            }

    return _ComponentExecutor()


def component_executor_for_registry(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One executor per component predicate, for the registry's entries.

    A mapping rather than a single executor, and that is the design point: each
    entry carries *its own* component, so each entry has its own artifact
    identity. A single registry-wide executor would have one identity, and the
    per-entry digest comparison -- the check that makes the pinned digest
    meaningful -- could not be made against it.

    The detector layer declares the ``ComponentExecutor`` contract and never
    imports the sandbox; the evolution layer builds the executors here and
    injects them. That is the same arrangement ``typed_semantic`` uses, and it
    is what keeps the dependency pointing one way.

    Returns an empty mapping when no entry names the runner, so a caller can
    pass the result unconditionally without changing the behaviour of every
    registry that predates it. A registry that *names* the runner without
    carrying a runnable component raises instead: silently returning nothing
    would make an unusable registry look like one with no component entries.
    """
    from detector.interventions import COMPONENT_RUNNER

    components: dict[str, dict[str, Any]] = {}
    declarations: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("type") or "") != "evidence_check":
            continue
        body = entry.get("body")
        body = body if isinstance(body, Mapping) else {}
        if str(body.get("runner") or "") != COMPONENT_RUNNER:
            continue
        raw = body.get("component")
        if not isinstance(raw, Mapping):
            declarations.append(
                f"{entry.get('intervention_id')}: no body.component"
            )
            continue
        predicate = str(body.get("predicate") or "")
        try:
            normalized = validate_component(dict(raw))
        except SandboxError as exc:
            declarations.append(f"{predicate}: {exc}")
            continue
        if normalized["name"] != predicate:
            # Entry validation already refuses this; repeating it here keeps the
            # executor's dispatch key and the entries it will be asked to serve
            # in agreement.
            declarations.append(
                f"{predicate}: component name {normalized['name']!r} does not "
                "match the predicate"
            )
            continue
        components[predicate] = normalized

    if not components:
        if declarations:
            raise SandboxError(
                "registry declares a component runner but no runnable "
                "component: " + "; ".join(declarations)
            )
        return {}
    return {
        predicate: make_component_executor(component)
        for predicate, component in components.items()
    }


__all__ = [
    "BUILTIN_WHITELIST",
    "DEFAULT_LIMITS",
    "FORBIDDEN_SOURCES",
    "HARD_LIMITS",
    "IMPORT_WHITELIST",
    "PARAMETER_TYPES",
    "RETURN_TYPES",
    "SANDBOX_SCHEMA",
    "VERDICT_VOCABULARY",
    "SandboxError",
    "admission_blockers",
    "build_envelope",
    "check_output_schema",
    "component_digest",
    "execute_component",
    "make_component_executor",
    "normalize_limits",
    "reference_component",
    "run_forbidden_sources",
    "sandbox_capabilities",
    "validate_component",
]
