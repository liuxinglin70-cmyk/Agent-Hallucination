# Experiments

Workspace for experiment plans, protocols, caches, progress, results and
logs.  Experiment protocols are deliberately kept outside the framework code
(`src/sehd_v6/detector` and `src/sehd_v6/evolution`).

```text
experiments/
├── README.md            # this file
├── plans/               # experiment plans and task lists (tracked)
├── protocols/           # experiment protocol + benchmark workspace (README tracked)
├── cache/               # experiment-run caches (git-ignored)
│   └── provider/        #   global provider cache (PACA_GLOBAL_CACHE_ROOT)
├── progress/            # reviews and progress notes (git-ignored)
├── results/             # experiment outputs and archives (git-ignored)
└── logs/                # run logs (git-ignored)
```

Only `README.md`, `plans/` and `protocols/README.md` are tracked by git.
`cache/`, `progress/`, `results/`, `logs/` and the content of `protocols/`
are local-only workspaces.

## Placement guide

- Plans and task lists → `plans/`
- Protocol definitions and benchmark scripts that will be run later →
  `protocols/` (see `protocols/README.md`)
- Reviews and progress notes → `progress/`
- Outputs, artifacts and archives → `results/`
- Run logs → `logs/`

## Caches and logs

Experiment runs write their caches and logs under `experiments/` so the
framework never depends on a fixed system path:

```text
experiments/
├── cache/provider/     # global provider cache
├── logs/               # run logs
└── results/<run>/      # per-run artifacts, including shared_cache/
```

To point the framework's global provider cache here:

```powershell
$env:PACA_GLOBAL_CACHE_ROOT = "$PWD\experiments\cache\provider"
```

Per-run caches (`shared_cache`, provider route caches) are written under the
run's output directory inside `results/<run>/` by the framework itself.
