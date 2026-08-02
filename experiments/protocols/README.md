# Experiment Protocols and Benchmarks

This directory is the workspace for experiment protocols and evaluation
(benchmark) scripts that run on top of the standalone detector and
self-evolution framework.

```text
experiments/protocols/
├── README.md            # this file (tracked)
├── protocols/           # experiment protocol definitions (local-only)
├── benchmarks/          # evaluation / benchmark scripts (local-only)
└── runners/             # run orchestration for protocol executions (local-only)
```

The framework code (`src/sehd_v6/detector` and `src/sehd_v6/evolution`) is
intentionally protocol-free.  Protocols, data partitioning, execution
authorization and evaluation gates are layered here.

## Suggested layout for a protocol

```text
protocols/<name>/
├── protocol.json        # protocol definition (routes, phases, authorization)
├── run.py               # orchestration entry
├── partition.py         # dataset split if required by the protocol
└── results/             # outputs (git-ignored)
```

Benchmarks follow the same pattern under `benchmarks/<name>/`.

Only this README is tracked by git.  All other content in this directory is
local-only unless explicitly staged.
