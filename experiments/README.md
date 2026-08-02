# Experiments

Workspace for experiment plans, progress, results and logs.  Experiment
protocols are deliberately kept outside the framework code
(`src/sehd_v6/detector` and `src/sehd_v6/evolution`).

```text
experiments/
├── README.md            # this file
├── plans/               # experiment plans and task lists (tracked)
├── progress/            # reviews and progress notes (git-ignored)
├── results/             # experiment outputs and archives (git-ignored)
└── logs/                # run logs (git-ignored)
```

Only `README.md` and `plans/` are tracked by git.  `progress/`, `results/`
and `logs/` are local-only workspaces.
