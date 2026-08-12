"""Package dispatch entry point: ``python -m sehd_rise <evolve|detect> ...``.

Delegates to ``sehd_rise.cli.__main__`` so the dispatch logic lives in the
cli layer and the root entry point stays a thin caller.
"""

from sehd_rise.cli.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
