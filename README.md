# SEHD-RISE — Hallucination Detector and Self-Evolution Framework

SEHD-RISE (Repairability-driven Integrated Self-Evolution) is a court-style
hallucination detector (PACA-TCL v3.5) wrapped in a repairability-centric
self-evolution framework. A causal repair layer decides whether a detected
failure is repairable at all; only repairable failures enter evolution, where
they are repaired through a unified RepairPlan protocol shared by two
evolvable surfaces — an L1 provider-rule genome of eight controlled
natural-language slot-rule modules, and an L2 adjudication-policy genome of
closed-vocabulary policy items evaluated deterministically by the proof
court. Every repair attempt is classified by a four-layer outcome funnel and
carries a full causal credit chain from dispatch to cross-case transfer.

The framework lives in `sehd_rise/` (13 layers + root entry). The
pre-refactor baseline `src/sehd_v6/` is kept as a local, git-untracked
backup (see `src/README.md`); the migration-period parity harnesses under
`tools/parity/` still import it.

For the framework architecture and design contracts see
[docs/design/TECHNICAL_DESIGN.md](docs/design/TECHNICAL_DESIGN.md).

## Core Objective and Design Discipline (必读 / Mandatory)

Every agent working in this repository — on framework code, experiments, or
documentation — must read this section first and comply with it. It is the
single source of truth for why this project exists and how decisions are
made; downstream documents only reference it, never restate it.

### Core objective (核心目标)

Produce **effective self-evolving detection skills** that measurably improve
agent-hallucination detection quality. The detector base (PACA-TCL v3.5) is
frozen; all improvement must come from the two evolvable surfaces (L1
provider rules, L2 adjudication policies) and must be demonstrated by
independent holdout measurement, not by framework-internal signals alone.
Every framework change is justified by its contribution to this objective;
correctness, auditability and call-efficiency work are enablers, never ends
in themselves.

### Design philosophy (设计理念)

1. **Systemic over patch-style.** Fix root causes with structural, bottom-up
   redesign; never accumulate special-case `if`s, hand-tuned knobs, or
   scenario mappings (see the First-Time Principle: single source of truth,
   no residue, local/deployed parity).
2. **Evidence-driven gates, no hand-tuned thresholds.** Acceptance decisions
   rest on observable replay effects, closed-vocabulary contracts and gold
   polarity — never on a manually adjusted numeric threshold or CRS weight
   (weights are frozen at 0.35/0.15/0.20/0.20/0.10).
3. **Fail-closed discipline.** Missing authority, unparseable output, or
   vocabulary violations always reject; uncertainty never passes as a
   verdict or an accepted edit.
4. **Evolvable surfaces are closed under correction.** Every layer that can
   produce a defective artifact must also be able to repair or remove it
   (L1: add/modify/remove rules; L2: add_item/modify_item/remove_item).
5. **Gold isolation.** Gold labels select polarity and fitness credit only;
   they never enter skill content, policy content or reflection prompts.
6. **Provider discipline.** Only DeepSeek-V4-Flash / DeepSeek-V4-Pro routes
   are allowed; credential values never enter code or artifacts.

### Advisor requirements (导师要求, 2026-08 discussion)

1. **Never hard-tune thresholds.** A single hand-adjusted threshold is always
   wrong in the end; prefer experiential judgment expressed as evolved
   skills (the agent decides from accumulated experience, not from a
   hand-picked number).
2. **Alternatives to a single threshold are encouraged.** Multiple
   thresholds / multiple outputs with best-of-N selection (test-time
   scaling) is a legitimate direction; broaden the option space instead of
   tuning one value.
3. **Regression-style validation (e.g. canary regression) is an optimizable
   mechanism**, adopted selectively where it earns its cost; it is a guard,
   not a correctness authority.
4. **Skill effects must be validated on an independent validation set.**
   Progress reports must show concrete intermediate experiment outputs, not
   only framework descriptions.

Experiment protocols must state how they comply with this section; reviews
check compliance before results are accepted.

## Layout

```text
sehd_rise/                # the framework (13 layers + root entry)
├── platform/             #   env / routes / models / usage / gold / jsonio
├── skills/               #   L1 genome / L2 policy genome / rule slots / vocabulary
├── detection/            #   packet / transport / court / policy / injection / detector
├── causal/               #   signals / cut / episode / repairability / plan
├── experience/           #   mechanism memory (single Experience Truth)
├── typing/               #   HTC-TES advisory: classifier / guidance / slices
├── planning/             #   L1/L2 reflection, retrieval, candidates, search, feedback
├── execution/            #   action / executor / replay / trace
├── evaluation/           #   effect / fitness / canary / gate / promotion
├── meta/                 #   cross-case strategy learner (strategy / review)
├── loop/                 #   unified orchestration (machine / runner / ledger / …)
├── api/                  #   public facade: detection / evolution / manifests
├── cli/                  #   python -m sehd_rise <evolve|detect>
├── configs/              #   detector config (detector_v3_5.json)
└── __main__.py           #   root entry dispatching to cli

docs/design/              # framework technical design (core document)
tools/parity/             # migration-period verification harnesses + layer check
experiments/              # local experiment workspace (artifacts git-ignored)
datasets/                 # raw datasets (local-only, git-ignored)
src/sehd_v6/              # pre-refactor frozen baseline (local backup, git-untracked)
```

The layer dependency rules are acyclic and mechanically enforced by
`tools/parity/check_layers.py`; see TECHNICAL_DESIGN.md for the rule table.

## Usage

### Standalone Detector

The detector runs independently from evolution. The input JSON must contain a
non-empty `history` and must not contain dataset gold fields such as
`is_hallucination`, `hallucination_step`, or `gold_label`.

```bash
# Required credential. Route-specific and purpose-specific variables override
# the provider defaults; credential values are never stored in config files.
set DEEPSEEK_API_KEY=<secret>

python -m sehd_rise detect \
  --config sehd_rise/configs/detector_v3_5.json \
  --record trajectory.json \
  --case-id example \
  --output-dir detector_output
```

The prediction is written to `detector_output/prediction.json`. Batch mode
(`--records`, with `--max-parallel` / `--limit` / `--no-resume`) is
available through the same command, and an optional precomputed census cache
can be injected via `--census-root`. Provider settings use one precedence
rule throughout:

- `SEHD_ROUTE_<ROUTE>_*`, for example `SEHD_ROUTE_DEFENSE_MODEL`
- `SEHD_DETECTOR_*` or `SEHD_JUDGE_*`, selected by route responsibility
- `SEHD_PROVIDER_*`
- values in the JSON config
- official DeepSeek endpoint/model defaults

Supported suffixes are `ENDPOINT`, `MODEL`, `API_KEY_ENV`, `TEMPERATURE`,
`MAX_TOKENS`, `TRANSPORT_RETRIES`, `TIMEOUT_SECONDS`, and
`TOTAL_TIMEOUT_SECONDS`. The process environment overrides repository `.env`.

Genome injection is explicit: `--genome` / `--policy-genome` (or
`detect_record(..., genome=..., policy_genome=...)`) deploy an evolved
two-layer capability without any framework-global state.

### Standalone Self-Evolution

The evolution framework accepts raw trajectories (one case or a batch), runs
the detect → causal analysis → repairability triage → RepairPlan → execution
→ evaluation loop, and writes the evolved champion genomes plus the
mechanism experience memory.

```bash
python -m sehd_rise evolve \
  --records records.jsonl \
  --config sehd_rise/configs/detector_v3_5.json \
  --output-dir evolution_output
```

`records.jsonl` is one record per line; each line is either
`{"case_id": "...", "record": {...}}` or a record with an embedded
`case_id`. A JSON object mapping `case_id -> record` also works.

Useful bounds and continuation flags:

```bash
python -m sehd_rise evolve \
  --records records.jsonl \
  --config sehd_rise/configs/detector_v3_5.json \
  --output-dir evolution_output_2 \
  --reflection-candidates 3 \        # independent mutation samples per case (1..5)
  --max-mutation-calls 100 \         # hard LLM budget, fail-closed when exhausted
  --initial-genome evolution_output/champion_genome.json \
  --initial-policy-genome evolution_output/policy_genome.json \
  --experience-memory evolution_output/htc_tes_experience_memory.json \
  --resume-from evolution_output/evolution_checkpoint.json
```

Artifacts written to the output directory:

- `evolution_result.json` — audit trail with the four-layer outcome funnel
- `champion_genome.json` — evolved L1 skill genome
- `policy_genome.json` — evolved L2 adjudication-policy genome
- `htc_tes_experience_memory.json` — mechanism-level experience memory
- `htc_tes_coverage.json` — typed coverage projection
- `evolution_state_manifest.json` — hash-bound state manifest

Provider routes are loaded from the repository-root `.env` (or the process
environment; process values win). No credential value is written to code or
artifacts. The default mutation route is official DeepSeek V4-Flash; see
TECHNICAL_DESIGN.md for the full route resolution order.

## Public API

The stable application-facing imports are `sehd_rise.api.detection` and
`sehd_rise.api.evolution`:

```python
from sehd_rise.api.detection import detect_record, load_detector_config
from sehd_rise.api.evolution import run_evolution, load_records
```

Layer-level imports follow the 13-layer package structure (`platform`,
`skills`, `detection`, `causal`, `experience`, `typing`, `planning`,
`execution`, `evaluation`, `meta`, `loop`, `api`, `cli`); the acyclic import
discipline is enforced by `tools/parity/check_layers.py`.

## Caches and Logs

Run artifacts live in the local `experiments/` workspace (git-ignored) so
the framework never depends on a fixed system path. The global provider
cache is configured with `PACA_GLOBAL_CACHE_ROOT`:

```text
PACA_GLOBAL_CACHE_ROOT=experiments/cache/provider
```

A warm provider cache makes replay runs deterministic and zero-cost; editing
a prompt invalidates only the affected mutation cache entries. All caches
are local-only and safe to delete between runs.

## Key Design Decisions

1. **Repairability before evolution.** `causal/repairability.py` is the
   single trigger authority: only `REPAIRABLE` failures enter skill
   evolution; `NON_EVOLVABLE` and `FRAMEWORK_DEFECT` are diverted before any
   search begins, and an evolvable-surface failure without a legal RepairPlan
   is an `UNADDRESSABLE` framework invariant violation, never a no-edit.
2. **One causal object, one RepairPlan protocol.** All downstream layers
   consume the `CausalFailureEpisode` and the canonical `RepairPlan`
   (failure identity / layer / owner / target edge / executor / legal
   operations / effect contract / evaluation contract); no layer re-invents
   failure semantics.
3. **Controlled natural-language slot rules** (not a typed DSL, not free
   text): L1 rules are typed `condition` / `action` / `evidence_anchor`
   slots rendered deterministically as `When <condition>, <action>.`
   (`skills/rule.py`), so the structural gate validates a slot contract
   instead of re-guessing free text.
4. **Closed-vocabulary L2 policies, closed under correction**: policy items
   pair a closed precondition predicate with an enumerated action; runtime
   semantics are owned by the deterministic proof-court evaluator
   (`detection/policy.py`), and add/modify/remove keep defective policies
   repairable instead of permanent.
5. **Four-layer outcome funnel instead of `skip`.** Every repair attempt
   ends in exactly one of 17 terminal outcomes across the framework /
   search / execution / fitness layers (`loop/machine.py`), so framework
   bottlenecks are never compressed into a single skip bucket.
6. **Convergence consumes only complete repair attempts.** Budget
   exhaustion, infra failure and invalid proposals are not learning
   evidence; the convergence window counts only fitness-layer rejections of
   fully executed, effect-measured repairs.
7. **Execution before credit, effect before fitness.** An accepted edit
   carries the full chain — dispatch → target edge moved → blocker cleared →
   terminal improvement → canary preserved — and no intermediate layer may
   substitute for the next layer's evidence.
8. **HTC-TES is advisory.** Type intelligence (`typing/`) organizes
   experience, retrieval and evaluation slices; it never overrides the
   causal owner.
9. **Meta learns strategy, not truth.** The meta layer (`meta/`) is a
   cross-case strategy learner over a five-domain closed vocabulary (search
   prior / operation preference / mechanism ranking / retry strategy /
   candidate diversity); owner, causal cut, repairability and hard-gate
   channels are unrepresentable at the type level.
10. **Fail-closed, gold-isolated, frozen-base discipline** as stated in the
    mandatory charter above.

## Development Guidelines

1. **No experiment protocols inside the framework.** Partitions, manifests,
   execution authorization, dataset splits and evaluation gates must not be
   added to `sehd_rise/`; they belong to the experiment layer.
2. **Experiments live in `experiments/`.** Protocol records, run scripts and
   run outputs stay in the local experiment workspace (git-ignored) and
   consume the framework only through the public API/CLI above. See
   `experiments/README.md`.
3. **Keep the framework callable standalone.** The framework must remain
   runnable with a raw trajectory as the only input; any experiment layer is
   an optional consumer, never a required dependency.
4. **Shared improvements belong in the framework; experiment-specific
   behavior belongs in the experiment.** If an experiment needs a new
   framework capability, add it as a generic API in `sehd_rise/` and keep
   the experiment-specific policy in `experiments/`.
5. **The frozen baseline is read-only.** `src/sehd_v6/` is never modified;
   defects found in old code are fixed by implementing the correct
   semantics in `sehd_rise/`.

## License

Private research repository.
