# SEHD v6 — Framework Model and Mechanism-Effectiveness Analysis (R5)

This note is the review-round-5 (R5) model of the SEHD v6 detector +
self-evolution framework and an analysis of which mechanisms are effective,
which are latent/inert, and what the R5 fixes restored. It is a point-in-time
audit: the code is the authority, this document only records the reasoning.

## 1. Framework model

SEHD v6 is two standalone modules with a shared core:

```
detector (sehd_v6.detector)
  detect_record(record, config, genome?, policy_genome?, census_root?)
    -> proof_engine.adjudicate_case
         Phase 0: base layer (layered_engine v2) + construct census (parallel)
         Phase 1: H1 focused prosecution (per-candidate, parallel) + defense
         Phase 2: H2 consensus gate + appeal (prosecutor/defender, bounded retry)
         Phase 3: H3 counterfactual localization (earliest-origin tiebreak)
         L2 hook:  adjudicate_proof_court(..., policy_items, policy_registry)
                   replays the terminal edge with deterministic policy evaluator

evolution (sehd_v6.evolution)
  run_evolution(...) -> run_online_evolution (bccse.evolution_runner)
    per case: detect -> attribute (deterministic classifier) ->
    HTC-TES type routing -> reflection (LLM) -> critic -> structural gate ->
    source replay + canary fitness -> commit/rollback -> experience memory
    L2 closed loop: policy reflection -> gate -> replay -> gold polarity ->
    accept/rollback; registry reflection for vocabulary growth
```

### State surfaces

- L1: `SkillGenome` — 8 modules of controlled natural-language slot rules;
  baseline immutable (`BASELINE_RULE_TEXT`), edits add/modify/remove.
- L2: `PolicyGenome` — closed-vocabulary policy items (predicate + action +
  params) evaluated deterministically by `policy_evaluator`; the genome owns
  its `registry` (baseline `L2_REGISTRY` or evolved).
- Experience memory: `EvolutionExperienceMemory` (artifact-first dedup on
  layer+artifact+reason_code+operation) + `TypeExperienceMemory`
  (per-type outcome distributions).
- Meta state: `MetaSkillState` — advisory guidance tokens / ranking
  preferences, restored from `causal_memory.meta_review` (single authority).

### The evolution loop's invariant chain

1. Fail-closed detection: any provider crash / malformed output / missing
   authority yields `runtime_valid=False` or `unresolved`, never a fabricated
   verdict.
2. Deterministic attribution: `classify_failure` answers layer/artifact
   without LLM; L1 owners pass the gold-polarity gate; non-L1 failures go to
   the L2 loop.
3. Edit gate: closed opcode + slot contract + capacity + dedup + baseline
   protection, then source replay must show an observable effect.
4. Promotion: gold-assisted task-score movement or independent canary
   non-regression; never self-certified by a label-free replay.

## 2. Mechanism-effectiveness analysis

### 2.1 Effective mechanisms (verified live in R5)

- **Court pipeline fail-closed semantics**: verified on a real provider run
  (BFCL 000 sample): a case with provider timeouts produced
  `runtime_valid=True`, `authoritative`, `unresolved` — the audit records the
  failure while the verdict stays honest.
- **Census fallback**: missing/invalid/corrupt census cache now degrades to
  the provider census instead of crashing the case (R5 fix).
- **L1 edit loop**: structural gate -> replay -> commit/rollback was traced
  end-to-end; budget accounting is correct (no double-count).
- **L2 policy hook**: `adjudicate_proof_court(policy_items, policy_registry)`
  replays the terminal edge deterministically; the R5 fix wires the genome's
  own registry into the evolution runner's detector calls, so evolved
  vocabulary is no longer silently skipped (this was the single most
  consequential L2 bug: L2 items referencing a registered predicate/action
  were previously evaluated against the baseline registry and always skipped).
- **Experience memory dedup**: artifact-first signature is consistent between
  writer (runner) and reader (retriever).
- **Meta review**: L2 `feedback_code` now reaches the gate-event reasons, so
  the Meta loop's evidence gate can actually observe L2 outcomes.

### 2.2 Latent / advisory mechanisms (by design, low risk)

- **Meta auto-commit**: `MIN_COMMIT_EVIDENCE = 3`; the loop is deliberately
  narrow (guidance tokens only). With the R5 feedback-code wiring it has a
  live evidence path; it remains advisory and never touches the hard gate.
- **Pro-mutation escalation**: one bounded Pro diagnosis attempt on a
  categorical no-candidate outcome; docstring corrected to match code.
- **Structured experience retrieval**: previously dead (episode key never
  existed, mechanism signature always empty, dishonest prefilter). R5 wired
  it: the runner passes the real `FailureEpisode` + memory, and the shared
  `episode_signature` makes query and index speak the same vocabulary.

### 2.3 Mechanisms that were broken and fixed in R5 (with evidence)

| Mechanism | Defect | Fix | Verification |
|---|---|---|---|
| L2 evolution effect | `policy_registry` dropped in runner's detector calls | pass `policy_genome.registry` | code trace + test |
| Census cache | corrupt file crashed the case | `load_paca_census` returns invalid audit | corrupt-JSON test |
| Replica guard | `_all_routes_deterministic` never fired (empty temperatures) | missing temp = 0 | unit test |
| Batch CLI | `--census-root` silently ignored | thread into `run_batch` | call-args test |
| Evaluation metrics | recall/FPR used predicted-side denominators | gold-side denominators | confusion-matrix test |
| Resume | stale attempt index; meta state not restored; empty policy items dropped; items validated against baseline registry; corrupt pending-probe silently cleared; phantom halted events | six targeted fixes | restore test on real checkpoint |
| Type classification | 5 reachable `ErrorType`s unmapped (silent signal loss) | full mapping | no-gap test |
| Experience retrieval | dead block + empty signatures + dishonest prefilter | wiring + shared signature | signature test |
| Registry vocabulary | pre-ledger operator proposable as instance | post-ledger filter in planner | planner test |
| No-effect metric | "explicit no-error" over-counted certificate producers | source flag `no_error_declared_flag` | provider-mock test |
| Zero-denominator rates | acceptance rates reported 1.0 (masked crashes) | `None` | evaluate test |
| Causal backtrack | insertion-order choice, not earliest | `min()` over propagate sources | graph test |

### 2.4 Costs and honesty boundaries (unchanged by R5)

- Provider calls are cached (shared cache + per-route caches); cache hits do
  not consume mutation budget.
- Gold never enters rule content, policy content, or reflection prompts
  unless explicitly enabled (`use_gold`, `gold_reflection`).
- Canary regression is a veto, never a correctness authority; development
  probes and holdouts live outside the framework (experiment protocols).
- `unanimous` now requires a decisive state: three all-unresolved replicas are
  not "unanimous".

## 3. Residual risks

1. **Prompt-injection surface**: L1 rules are rendered into provider prompts;
   the slot contract and opcode ownership constrain generation-time content,
   but a rule that passes the gate and later provokes a provider misbehavior
   is still a research risk (mitigated by fail-closed replay + rollback).
2. **Meta loop drift**: `COMMIT_EVIDENCE_SOURCES` and `_fuzzy_codes_for`
   must stay in lockstep with runner reason strings; a new reason class needs
   a mapping or it is silently invisible to the Meta loop.
3. **Resume contract**: checkpoint schema is versioned (v5) and hash-bound;
   the v4->v5 migration only adds Meta sub-domains. Any future schema change
   must keep the migration boundary in `load_evolution_checkpoint`.
4. **Per-case real cost**: the minimal real run (1 case, 5 steps) took
   ~200 s and roughly a dozen provider calls; experiments must budget
   `max_mutation_calls` and cache aggressively.

## 4. R5 verification summary

- 22 independent tests in `tests/test_review_r5_independent.py` (no
  dependency on the pre-existing suite) — all pass.
- Full pre-existing suite: 850 passed (2 layout-pinning tests updated for the
  `if True:` dedent; 1 telemetry test updated for the honest
  `_localization_bias_signal` contract).
- One minimal real run: detector produced `runtime_valid=True` with
  `unresolved` on a provider-timeout case; the evolution loop ran a full
  detect->attribute->HTC-TES->reflection->fail-closed step and persisted all
  artifacts (result, champion, policy genome, checkpoints, memory, state
  manifest); checkpoint restore validated against the real artifacts.
