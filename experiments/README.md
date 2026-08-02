# Experiments

Workspace for experiment plans, protocols, progress, results and logs.
Experiment protocols are deliberately kept outside the framework code
(`src/sehd_v6/detector` and `src/sehd_v6/evolution`).

```text
experiments/
├── README.md            # this file
├── plans/               # experiment plans and task lists (tracked)
├── protocols/           # experiment protocol + benchmark workspace (README tracked)
├── progress/            # reviews and progress notes (git-ignored)
├── results/             # experiment outputs and archives (git-ignored)
└── logs/                # run logs (git-ignored)
```

Only `README.md`, `plans/` and `protocols/README.md` are tracked by git.
`progress/`, `results/`, `logs/` and the content of `protocols/` are
local-only workspaces.

## Placement guide

- Plans and task lists → `plans/`
- Protocol definitions and benchmark scripts that will be run later →
  `protocols/` (see `protocols/README.md`)
- Reviews and progress notes → `progress/`
- Outputs, artifacts and archives → `results/`
- Run logs → `logs/`
