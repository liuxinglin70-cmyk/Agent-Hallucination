"""Small dependency-free loader for the repository-local ``.env`` file.

The loader is intentionally conservative: an already-present process
environment variable always wins, values are never printed, and only simple
``KEY=VALUE`` assignments are accepted.  This is sufficient for provider
credentials without adding a third-party dotenv dependency to the runtime.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_local_env(path: str | Path | None = None) -> Path | None:
    """Load the nearest repository ``.env`` without overriding the process.

    ``path`` is primarily useful for tests.  With no explicit path, parent
    directories of this module are searched, which includes the workspace
    root when the package is imported from ``src/sehd_v6``.
    """

    if path is not None:
        candidates = (Path(path),)
    else:
        here = Path(__file__).resolve()
        candidates = tuple(parent / ".env" for parent in here.parents)
    loaded: Path | None = None
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            # Strip trailing inline comments (``KEY=VALUE # comment``) only
            # for unquoted values, so a quoted value keeps any literal '#'.
            if not (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value.split(" #", 1)[0].rstrip()
            if _KEY.fullmatch(key):
                os.environ.setdefault(key, _unquote(value))
        # A candidate that parsed no assignments (e.g. an empty .env in a
        # nested directory) must not shadow a valid repository-root .env, so
        # keep scanning upward instead of returning immediately.
        loaded = candidate
    return loaded
