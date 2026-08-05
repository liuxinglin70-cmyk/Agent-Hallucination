# Experiments

Workspace for experiment plans, protocol records and local run artifacts.
Experiment protocols are deliberately kept outside the framework code
(`src/sehd_v6/detector` and `src/sehd_v6/evolution`).

**Before designing or running any experiment here, read and comply with the
"Core Objective and Design Discipline" section of the repository-root
[README](../README.md)** (核心目标、设计理念、导师要求). Every plan under
`plans/` must state how it complies; reviews check compliance before
results are accepted.

```text
experiments/
├── README.md                  # this file (tracked)
├── plans/                     # experiment plans and task lists (tracked)
├── protocols/                 # protocol records (tracked)
│   ├── README.md              #   directory guide
│   └── used_case_ledger.txt   #   used-case ledger
├── select_batch_cases.py      # stratified 10-case selector (tracked)
├── run_batch_concurrent.py    # isolated 10x1 runner (tracked)
├── analyze_batch.py           # per-case trajectory reader (tracked)
├── select_50_cases.py         # stratified 50-case selector (tracked)
├── analyze_decision_profile.py  # offline decision-profile analysis (tracked)
└── run_decision_replicas.py     # test-time-scaling replica experiment (tracked)
```

The selectors, concurrent runner, and trajectory analyzer are tracked because
they define the reproducible experiment protocol. Dataset builds, caches,
outputs, logs, and ad-hoc analysis remain local-only and git-ignored; none is
required by the framework runtime.

## Used-case ledger

`protocols/used_case_ledger.txt` lists every `case_id` already consumed by
past trials, evolution subsets and reserved holdouts.  Any new experiment
must exclude every `case_id` in the ledger.  See `protocols/README.md`.

## Placement guide

- Plans and task lists → `plans/`
- Used-case tracking and protocol records → `protocols/`
- Reusable experiment orchestration → tracked scripts under `experiments/`
- Dataset builds, caches, outputs and logs → local-only locations under
  `experiments/` (git-ignored, created on demand)

## Caches and logs

Experiment runs write their caches and logs under `experiments/` so the
framework never depends on a fixed system path.  Conventional locations:

```text
experiments/cache/provider/     # global provider cache
experiments/logs/               # run logs
experiments/results/<run>/      # per-run artifacts, including shared_cache/
```

These directories are git-ignored, created on demand by runs, and safe to
delete between runs.  To point the framework's global provider cache here:

```powershell
$env:PACA_GLOBAL_CACHE_ROOT = "$PWD\experiments\cache\provider"
```

Per-run caches (`shared_cache`, provider route caches) are written under the
run's output directory by the framework itself.
