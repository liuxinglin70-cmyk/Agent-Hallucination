# Stage A New-20 Mechanical Repair Report

- Dataset: `detector_v2/experiments/v2_stageA_new20_cases.json`
- Composition: 10 positive + 10 negative
- Exclusions: pilot20, fresh10, fresh10b, and the prior fresh20; zero overlap
- Concurrency: `workers=20` for both runs
- Runs: `v4_qg_stageA_new20_mechanical_a1/a2`
- Repair: deterministic `final_delivery_present` predicate plus a
  `convict_mechanical_violation` guard
- LLM budget: unchanged, judge 3 votes / recall 1 call / upgrade 3 votes /
  defense 3 votes; the mechanical repair adds zero provider calls

## Results

| metric | a1 | a2 |
|---|---:|---:|
| decisive | 16/20 | 16/20 |
| unresolved | 4/20 | 4/20 |
| decisive accuracy | 0.625 | 0.813 |
| all-case accuracy | 0.500 | 0.650 |
| TP / FP / FN / TN | 7 / 6 / 0 / 3 | 8 / 3 / 0 / 5 |
| positive precision | 0.538 | 0.727 |
| positive recall | 1.000 | 1.000 |
| step localization | 3/10 | 3/10 |

### Consistency

- a1/a2 consistency: **14/20**
- disagreements:
  - `BFCL/076.json`: true vs unresolved
  - `Magentic_One/010.json`: unresolved vs true
  - `SmolAgents/045.json`: unresolved vs true
  - `Magentic_One/003.json`: false vs true
  - `Magentic_One/065.json`: false vs true
  - `Magentic_One/070.json`: true vs unresolved

## Gates

| gate | threshold | observed | result |
|---|---:|---:|---|
| accuracy | >= 0.60 | a1 0.500, a2 0.650 | **FAIL** |
| consistency | >= 18/20 | 14/20 | **FAIL** |
| unresolved | <= 1/20 | 4/20 each | **FAIL** |
| provider transport failure | < 10% | 0 hard failures | PASS |

Stage A is **not passed**. Stage B remains blocked.

## Root Cause

The deterministic candidate did not fire its guard on this new set. No case
had the mechanical pattern “terminal success with neither prose nor an answer
tool delivery anchor”, so it neither improved nor distorted these metrics.

The observed failures remain in the probabilistic path:

- `Defense inconclusive` caused 4 unresolved results across the two runs,
  including `Octotools/018`, `SmolAgents/045`, `BFCL/076`, and
  `OpenManus/000`.
- `substantiated_upgraded` caused unresolved results when Defense ballots were
  mixed, including `Magentic_One/010` and `Magentic_One/070`.
- Recall/Upgrade produced repeatable false positives on negative cases,
  especially `BFCL/038`, `BFCL/120`, and `Magentic_One/014`; other cases such
  as `Magentic_One/003`, `Magentic_One/065`, and `Magentic_One/070` varied
  between runs.
- a1 had two recovered transport warnings but zero terminal provider
  failures; a2 had zero such warnings.

This confirms that the previous text-boundary candidates were not acceptable:
the first version improved consistency by suppressing true positives such as
`BFCL/112` and `Camel/037`; the second version restored those cases but still
failed with 15/20 consistency and added FP/unresolved outcomes. Neither was
admitted as a valid self-evolution repair.

## Cost

| run | total tokens | provider attempts | hard provider failures |
|---|---:|---:|---:|
| a1 | 519,041 | 64 | 0 |
| a2 | 503,792 | 62 | 0 |
| total | 1,022,833 | 126 | 0 |

The run used 20-way concurrency, but concurrency affects wall-clock time only;
it does not change the per-case call budget.

## Decision

The mechanical guard is retained as a narrowly scoped, zero-call candidate for
future validation, but this new-20 run does not establish admission. No stage B
release is justified. The next admissible self-evolution slice must target the
deterministic interpretation of Defense/Upgrade evidence states, with paired
sentinel checks proving zero new FN and zero new FP before another fresh-20
read.
