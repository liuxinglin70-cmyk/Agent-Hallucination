"""Dispatch entry point: ``python -m sehd_rise <evolve|detect> ...``."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: python -m sehd_rise <evolve|detect> ...")
    command = sys.argv[1]
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    if command == "evolve":
        from sehd_rise.cli.evolve import main as evolve_main
        return evolve_main()
    if command == "detect":
        from sehd_rise.cli.detect import main as detect_main
        return detect_main()
    sys.exit(f"unknown command: {command!r} (expected 'evolve' or 'detect')")


if __name__ == "__main__":
    raise SystemExit(main())
