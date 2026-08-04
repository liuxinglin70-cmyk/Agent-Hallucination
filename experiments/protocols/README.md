# Experiment Protocols

This directory holds the tracked protocol records for experiments that run
on top of the standalone detector and self-evolution framework.  The
framework code (`src/sehd_v6/detector` and `src/sehd_v6/evolution`) is
intentionally protocol-free; data partitioning, execution authorization and
evaluation gates are layered here instead.

```text
experiments/protocols/
├── README.md              # this file
└── used_case_ledger.txt   # used-case ledger
```

These two files are the only tracked content of this directory.

## Used-case ledger

`used_case_ledger.txt` lists every `case_id` already consumed by past
trials, evolution subsets and reserved holdouts.  **Any new experiment must
exclude every `case_id` in the ledger.**  Before building a dataset or
launching a run, subtract the full ledger from the candidate case pool; this
is a handoff discipline, not a suggestion.

## Everything else is local-only

Dataset builds, run scripts and output directories are local workspace
artifacts, not tracked content: they are produced under the local
`experiments/` workspace (git-ignored), recreated per run, and never
required by the framework.  Nothing in this directory besides `README.md`
and `used_case_ledger.txt` is tracked by git.
