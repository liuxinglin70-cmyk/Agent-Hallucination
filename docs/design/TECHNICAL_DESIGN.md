# SEHD-RISE Technical Design

## Purpose

SEHD-RISE (Repairability-driven Integrated Self-Evolution) treats PACA-TCL
v3.5 proof-court verdicts as the runtime authority and organizes
self-evolution around one principle: **repairability before evolution**. A
causal repair layer is the single authority on failure ownership and
repairability; only repairable failures enter evolution; L1/L2/Meta skills
share one canonical RepairPlan protocol; and every repair attempt is
evidenced by an explicit causal credit chain and classified by a four-layer
outcome funnel.

The normative project charter — core objective, design philosophy and
advisor requirements that every agent must comply with — is the "Core
Objective and Design Discipline" section of the repository
[README](../../README.md); this document only implements it.

The framework lives in `sehd_rise/`. The pre-refactor baseline
`src/sehd_v6/` is frozen, git-untracked and never modified.

## Architecture

### Layers and dependency rules

The package is organized into 13 layers plus a root entry. Dependencies
point strictly downward (`A → B` means A may import B); reverse imports are
forbidden and the rule is enforced mechanically by
`tools/parity/check_layers.py`:

```text
platform        ← every layer may depend on it; depends on nothing in sehd_rise
skills          → platform
detection       → platform, skills
causal          → platform, skills, detection
experience      → platform, skills, causal
typing          → platform, skills, causal, experience
planning        → platform, skills, causal, experience, typing
execution       → platform, skills, detection, causal
evaluation      → platform, skills, causal, execution
meta            → platform, causal, experience, evaluation
loop            → every layer except api/cli
api, cli        → loop, detection, evaluation, planning
```

Layer responsibilities:

| Layer | Modules | Responsibility |
|---|---|---|
| `platform` | env / routes / models / usage / gold / jsonio | Research-agnostic infrastructure: `.env` loading, provider route resolution, canonical model names, usage ledger, gold normalization (evaluation side only), canonical JSON / stable hash / atomic writes |
| `skills` | genome / policy / rule / vocabulary/ | Evolvable-surface data models and closed vocabularies — pure data, no process logic. `vocabulary/` is the single source of truth for L2 predicates/actions, L1 module charters and court audit vocabulary |
| `detection` | packet / transport / court/ / policy / injection / detector / replicas / batch | Proof-court detection: trajectory packing, provider transport, the v3.5 court engine, the deterministic L2 policy evaluator, L1 rule injection at the provider boundary, `detect_record` entry, replica aggregation, batch runs |
| `causal` | signals / cut / episode / repairability / plan | The single causal authority: detection→evolution projection, terminal causal cut, `CausalFailureEpisode`, `RepairabilityStatus` triage, canonical `RepairPlan` |
| `experience` | memory | Mechanism-level experience memory — the single Experience Truth |
| `typing` | classifier / guidance / slices | HTC-TES advisory type intelligence: type tagging, retrieval/reflection priors, typed evaluation slices. No causal veto |
| `planning` | l1 / l2 / retrieval / candidates / search / feedback | Hypothesis search driven by RepairPlan: L1 LLM causal reflection, L2 closed-vocabulary policy planning, experience retrieval, retry feedback |
| `execution` | action / executor / replay / trace | Repair execution and evidence: `ActionSpec`, dispatch by `RepairPlan.executor`, genome-injected detection replay, `ExecutionTrace` |
| `evaluation` | effect / fitness / canary / gate / promotion | Causal effect records, source fitness, canary selection/keeping/eviction, structural + no-conflict gates, `PromotionDecision` |
| `meta` | strategy / review | Cross-case strategy learner: five-domain closed strategy vocabulary, evidence-gated commits, strategy suggestions only |
| `loop` | machine / runner / state / records / persistence / ledger / probes / shadow / result | Unified orchestration: the evolution state machine, the outcome funnel, case queue and budgets, checkpoints, GateEvent ledger, development probes, shadow batch commits |
| `api` | detection / evolution / manifests | Public facade — the only layer allowed to assemble |
| `cli` | evolve / detect | `python -m sehd_rise <evolve|detect>` argument surface |

### Authority relations (one fact, one authority)

| Fact | Single source of truth | Everything else |
|---|---|---|
| Detection Truth | `detection/court/` output (terminal basis) | `causal/signals.py` is the only legal projection into evolution |
| Failure Truth | `causal/episode.py` (`CausalFailureEpisode`) | attribution/classifier/episode are one merged site |
| Repair Truth | `causal/plan.py` (`RepairPlan`) | planning/execution never re-create repair semantics |
| Trigger / repairability | `causal/repairability.py` | replaces the old `should_evolve` + `skip_unaddressable_failure` dual authority |
| Execution Truth | `execution/trace.py` (`ExecutionTrace`) | |
| Effect Truth | `evaluation/effect.py` (`CausalEffectRecord`) | edge-level counterfactual evidence is judged only here |
| Fitness Truth | `evaluation/promotion.py` (`PromotionDecision`) | |
| Experience Truth | `experience/memory.py` | `typing/` holds slice views only, never copies |
| Meta Truth | `meta/strategy.py` (`StrategyState`) | |
| Gate events | `loop/ledger.py` (`GateEvent`) | single projection authority |

## Detection Authority

The detection path is:

```text
trajectory record
  -> H0: OCSE packet + layered base pass + construct census
  -> H1: qualification / propagation + focused prosecution and defense
  -> H2: calibrated appeal + informed defense + consensus gate
  -> H3: counterfactual localization
  -> verified_error | verified_clean | unresolved
```

The proof court is the source of truth for the three-state verdict. CRS is a
diagnostic projection used by causal analysis and must not be treated as an
independent verdict. Court invariants:

- A certificate must use addressable event or interaction references; an
  invalid certificate is quarantined and cannot support a clean or appeal
  decision.
- `supported_or_correct` is mechanism-limited and requires related evidence.
- Appeal positives require admissible independent initial support.
- Invalid counterfactual rankings fall back to deterministic localization.
- Unresolved is a first-class outcome; a negative unresolved case is never
  counted as decisive-clean accuracy.
- A formally scored case must have `runtime_valid=true`; base-layer
  quarantine outcomes are projected into machine-readable
  `authority_failure_reasons` (observability only, never a verdict input).

The five-dimensional CRS diagnostic projection uses frozen weights:
Factuality 0.35, Counterfactual 0.15, Recovery 0.20, Process 0.20, Scope
0.10.

## Causal Repair Layer

The causal layer (`causal/`) is where the old framework's competing
authorities were merged into one.

**Terminal causal cut** (`cut.py`). Root-cause localization finds the
minimal repairable cause preventing the correct terminal state, not merely
the first malformed artifact. Malformed/missing artifacts, quarantines,
coverage anomalies and appeal anomalies are evidence for constructing the
cut, not automatic owners. The deterministic failure classifier answers
exactly one question — at which layer, on which artifact, did the failure
happen — with no LLM and no heuristics, in priority order:

1. Provider artifact missing or malformed → L1; owner resolved from the
   artifact kind (candidate → `candidate_trigger`, error certificate →
   `evidence_obligation`, defense certificate → `clean_defense_boundary`).
2. Consensus gate blocked a non-empty candidate set → L2,
   `consensus_gate_policy`.
3. Hollow complete coverage (claims `complete` with zero valid certificates
   on that side) → L2, `coverage_validity_policy`.
4. Boundary path returned `verified_clean` but terminal state is
   `unresolved` → L2, `boundary_consumption_policy`.
5. Fall-through: unresolved with no actionable shape, or a terminal verdict
   contradicting the court's own certificate bookkeeping → not
   skill-addressable.

An L1 owner hypothesis additionally passes the gold polarity gate: a false
negative may only be owned by modules that tighten error detection, a false
positive only by modules that relax or bound the error claim; hypotheses on
the wrong polarity side are dropped. Unresolved terminals carry no polarity.

**CausalFailureEpisode** (`episode.py`). The single failure object every
downstream layer consumes: terminal state, terminal basis, blocking
predicate, causal failure edge, failure owner, skill layer, repairable
surface, legal repair operations, observable effect, repairability status.

**Repairability authority** (`repairability.py`). The only evolution
trigger. Every analyzed failure is triaged into exactly one status:

- `NON_EVOLVABLE` — detector/no-actionable/runtime-invalid outcomes; never
  produce a skill.
- `REPAIRABLE` — at least one legal RepairPlan compiles; only these enter
  evolution.
- `UNADDRESSABLE_FRAMEWORK_DEFECT` — the framework invariant violation: a
  failure declared to be on the evolvable surface for which no legal
  RepairPlan exists. Counted independently; never enters no-edit or
  convergence statistics.
- `FRAMEWORK_DEFECT` — detector-boundary origin (validator / transport /
  route / config). Routed to the framework side permanently; never produces
  a skill, so the genome can never learn to compensate for detector bugs.

**Canonical RepairPlan** (`plan.py`). L1 and L2 share one protocol:

```text
RepairPlan
├── failure identity
├── layer                  # L1 | L2
├── owner
├── target causal edge
├── executor               # provider-behavior | adjudication-policy
├── legal operations
├── effect contract
└── evaluation contract
```

L1 executes through the provider-behavior executor (injected rules); L2
executes through the adjudication-policy executor (the deterministic court
evaluator). Multi-layer novelty lives in the different learning surfaces,
not in different failure semantics.

## Two-Layer Evolvable Surface

The frozen base never changes: evolution mutates only the L1 rule genome and
the L2 policy genome; CRS weights, court decision rules and stage structure
are untouched, and no threshold is hand-tuned.

### L1 — provider-rule genome

Eight skill modules own the provider-emitted surfaces:

| Module | Stage | Role |
|--------|-------|------|
| `candidate_trigger` | Census | What constitutes a hallucination candidate |
| `evidence_obligation` | Verification | What evidence must support each candidate |
| `candidate_to_verifier_route` | Verification | How candidates are routed to verification |
| `atomic_verifier_instruction` | Verification | How to verify individual claims atomically |
| `clean_defense_boundary` | Verification | When to defend a candidate as clean |
| `causal_link_test` | Causal Court | How to test causal chains for localization |
| `earliest_origin_tiebreak` | Causal Court | How to resolve multi-origin localization ties |
| `abstention_rule` | Causal Court | When to output `unresolved` |

Rules are stored as typed English slots (`skills/rule.py`):

- `condition`: one trajectory-observable situation (≤ 300 chars);
- `action`: one module-local closed opcode from the owning module's charter,
  optionally `"<opcode>: <target>"` naming the exact output field (≤ 300
  chars); free-text actions are rejected as `action_opcode_unowned`;
- `evidence_anchor`: failure-signature bookkeeping for evolution triage
  (≤ 240 chars); never rendered into runtime prompts, so it cannot leak
  case identity into detection.

Prompt injection is a deterministic render — exactly `When <condition>,
<action>.` — so the structural gate validates the slot contract as the
single shape check. Each module carries a charter
(`skills/vocabulary/charters.py`): pipeline stage, influenceable surfaces,
forbidden surfaces, allowed verdicts, in-scope vs out-of-scope failure
examples. Only the allowed target modules' charters and opcode lists enter
the reflection prompt, constraining proposals at generation time. Runtime
rules may not encode dataset labels, case-specific identifiers, internal
diagnostic state, forbidden direct verdicts, unsafe cross-module references,
or baseline mutations.

### L2 — adjudication-policy genome

Closed-vocabulary policy items: one precondition predicate (a pure function
over the frozen `AdjudicationFacts` snapshot) paired with one enumerated
action whose parameters draw from closed vocabularies. Every predicate,
action and parameter token is owned by the versioned registry
(`skills/vocabulary/registry.py`); the reflection prompt's vocabulary table
is generated from that registry at call time, and every parsed proposal is
re-validated against the same closed sets.

Runtime semantics are owned by the deterministic evaluator
(`detection/policy.py`), executed mechanically through the proof-court
policy hook. If the proposing model also interpreted items at runtime, the
verdict would depend on an unrepeatable LLM reading of its own artifact; the
deterministic evaluator is what keeps the artifact-to-behaviour coupling
auditable. The hook is inert by default — an empty policy set is the
baseline, so every behavior change must be earned. Policies act on the
terminal edge only; coverage downgrades are monotone (a policy can lower but
never upgrade a coverage rank); unknown predicates, actions or parameters
fail closed into the evaluator's `skipped` list.

The surface is closed under correction: `add_item` / `modify_item` /
`remove_item` keep a wrong or dead policy repairable or eliminable.
Validation enforces vocabulary membership, genome capacity (12 items),
semantic deduplication, index bounds, immutable baseline items and
weak/strong fitness protection; application produces a new genome with
parent-hash lineage. The vocabulary itself is online-evolvable under
constraint: new predicates or court-local action instances register as
`pending` (shadow-only) and promote to `registered` only through the
evidence path (`skills/vocabulary/evolution.py`).

## Evolution Loop

### State machine

`loop/machine.py` implements the evolution state machine:

```text
DETECTED
  -> CAUSAL_FAILURE_ANALYZED
       -> NON_EVOLVABLE | FRAMEWORK_DEFECT        (terminal, framework layer)
       -> REPAIRABLE
  -> REPAIR_PLANNED
  -> HYPOTHESIS_SEARCH                            (NO_HYPOTHESIS: search layer)
  -> PROPOSAL_VALIDATED                           (INVALID_PROPOSAL / DUPLICATE_PROPOSAL)
  -> EXECUTED                                     (ACTION_NOT_DISPATCHED / PROVIDER_NOT_COMPLIED)
  -> TARGET_EDGE_CHANGED                          (TARGET_EDGE_UNCHANGED)
  -> BLOCKER_CLEARED                              (BLOCKER_NOT_CLEARED)
  -> FITNESS_EVALUATED
       -> REJECTED (SOURCE_NOT_IMPROVED / CANARY_REGRESSION / TRANSFER_REGRESSION)
       -> ACCEPTED
  -> EXPERIENCE_LEARNED
  -> META_UPDATED
```

### Outcome funnel

Every repair attempt terminates in exactly one of 17 outcomes across four
layers — `loop/machine.py::Outcome`, replacing the old `skip_*` vocabulary
entirely:

- **Framework**: `NON_EVOLVABLE`, `UNADDRESSABLE`, `ROUTING_FAILURE`,
  `SCHEMA_FAILURE`, `INFRA_FAILURE`, `BUDGET_EXHAUSTED`
- **Search**: `NO_HYPOTHESIS`, `INVALID_PROPOSAL`, `DUPLICATE_PROPOSAL`
- **Execution**: `ACTION_NOT_DISPATCHED`, `PROVIDER_NOT_COMPLIED`,
  `TARGET_EDGE_UNCHANGED`, `BLOCKER_NOT_CLEARED`
- **Fitness**: `SOURCE_NOT_IMPROVED`, `CANARY_REGRESSION`,
  `TRANSFER_REGRESSION`, `ACCEPTED`

Each outcome is counted independently in `evolution_result.json`, so the
true framework bottleneck is always recoverable from experiment output and
framework failure is never mixed with learning rejection.

### Convergence

The convergence window consumes only **complete repair attempts**: plan
valid → proposal valid → action executed → causal effect measured → no
acceptable improvement (i.e. fitness-layer rejections). Budget exhaustion,
infra failure, routing failure and invalid proposals are system-availability
events, not learning-saturation evidence, and never increment convergence.

### Causal credit chain

An accepted edit carries the full chain, each link evidenced by its own
layer — no intermediate layer may substitute for the next layer's evidence:

```text
Hypothesis -> RepairPlan selected -> Action dispatched
  -> Executor/provider complied -> Target causal edge changed
  -> Blocking predicate cleared -> Terminal behavior changed
  -> Task score improved -> Canary/transfer preserved -> Skill committed
```

### Orchestration

`loop/runner.py` is the single orchestration path for both layers: case
queue → detection → causal cut → episode → repairability triage → RepairPlan
→ planning → execution → evaluation → ledger → experience → meta. Supporting
machinery:

- **Shadow batch commits** (`shadow.py`): one edit per shadow batch;
  combined multi-edit replay is never attributed back to individual rules.
- **Checkpoints** (`persistence.py`): `evolution_checkpoint.json` /
  `champion_checkpoint.json` / `shadow_checkpoint.json`; resume re-validates
  vocabulary membership and content hashes fail-closed, and the outcome
  funnel is rebuilt from step projections after resume so an interrupted run
  is equivalent to a continuous one.
- **Development probes** (`probes.py`): a budgeted probe on stable
  development cases may steer or roll back development (both genomes roll
  back together); a probe is a direction gate, never generalization
  evidence.
- **GateEvent ledger** (`ledger.py`): every gate decision recorded once,
  projected everywhere.

### L1 loop (per case)

Detect → attribute (deterministic, gold-polarity-gated) → reflect (LLM with
evaluator-stripped evidence, module charters, closed opcodes, bounded
negative experience, optional support/counter-case contrast) → structural
pre-validation and gate → source replay with rule-trace and deterministic
no-effect classification (`output_changed_then_quarantined` /
`provider_output_changed_but_court_invariant` /
`no_observable_change_any_layer`) → one bounded same-source repair retry for
repairable structural or no-effect candidates → commit/rollback.

### L2 loop (per case)

Propose (one reflection call, one closed-vocabulary `PolicyEdit`, no gold in
prompt) → validate (vocabulary / capacity / dedup / index bounds /
protection) → apply to a candidate genome copy → source replay through
`adjudicate_case(policy_items=...)` at the court's convergence point with
the identical evidence bundle → effect gate (add/modify require the policy
to fire and move terminal state or effective coverage; remove requires the
targeted item to have actually fired and its removal to move an observable
output) → gold polarity gate (terminal movement must strictly improve
`task_state_score`) → accept or inherent rollback. Gold-verified
terminal-moving accepts earn `strong` protection against later
modify/remove; coverage-only accepts stay correctable.

## Typing — HTC-TES Advisory

HTC-TES survives as advisory type intelligence (`typing/`): experience
indexing, retrieval priors, mechanism clustering, type-conditioned
reflection priors, evaluation slicing and transfer analysis. It holds no
veto over the causal owner: when type classification and causal attribution
disagree, the attribution route stands.

The taxonomy is eight types: `factual`, `instructional`, `tool`,
`reasoning`, `omission`, `adoption`, `contextual`, `unknown`. The classifier
(`classifier.py`) combines runtime candidate structure, normalized CRS
signals, attribution and module evidence over the versioned, label-free
`TrajectoryFeatures` object (`htc_tes.trajectory_features.v1`). Typed
telemetry is recorded separately from gold correctness: direction and type
are bookkeeping, never evidence that a detector direction was optimized.

## Meta Skill — Cross-Case Strategy Learner

The meta layer (`meta/`) learns how to search, never what is causally
broken. `StrategyEdit`s operate on a five-domain closed vocabulary (22 live
tokens): search prior, operation preference, mechanism ranking, retry
strategy, candidate diversity. Tokens touching owner, causal cut,
repairability or hard gates do not exist — those channels are
unrepresentable at the type level, not merely rejected at validation.

Inputs are the GateEvent projection and verified RepairPlan outcomes from
the experience memory; outputs are strategy suggestions only, projected into
prompts with the fixed authority marker `evolution_guidance_only`. Commits
are evidence-gated (`MIN_COMMIT_EVIDENCE = 3` observations of the same
mechanism), `StrategyState` keeps immutable lineage with content-hash-derived
ids, and disable operations require manual review. The layer is fully
deterministic and makes no provider calls.

## Experience Memory

`experience/memory.py` is the single Experience Truth. Entries are
mechanism-level: what failure mechanism, under what conditions, repaired at
which skill layer by what operation, with what causal effect, and whether
transfer occurred — not merely "some rule once succeeded". Deduplication
keys on the deterministic attribution signature (`layer` + `artifact` +
`reason_code` + `operation`) plus feature/direction/success; repeated
observations merge into an outcome distribution instead of overwriting the
first snapshot.

The runner persists `htc_tes_experience_memory.json` and
`htc_tes_coverage.json`. Experience is evidence for future reflection, not
an accuracy claim: coverage improves only after the evaluation layer records
a successful source-level outcome. Cross-run continuation loads
`--initial-genome`, `--initial-policy-genome` and `--experience-memory`
explicitly; loading memory alone never mutates the registered baseline
genome.

## Evaluation and Promotion

The promotion hierarchy (`evaluation/`):

1. **Structural gate** (`gate.py`) rejects unsafe edits through the slot /
   vocabulary contracts, plus an LLM no-conflict gate.
2. **Effect** (`effect.py`): edge-level counterfactual verification —
   did the target causal edge move, did the blocking predicate clear, is the
   intervention the attributable cause.
3. **Source fitness** (`fitness.py`): before/after comparison on the
   triggering case; evidence-obligation local fitness rewards the weaker of
   prosecution and defense coverage rather than a large one-sided gap.
4. **Canary** (`canary.py`): a rolling set of previously stable cases must
   preserve decisive state, runtime validity and non-triggered process
   status; canary regression vetoes catastrophic forgetting.
5. **Promotion** (`promotion.py`): `PromotionDecision` with evidence
   grades; `no_effect` is never silently committed; attempts never share
   fitness credit.

Contrastive replay additionally replays an otherwise-accepted edit on one
nearest stable same-type neighbour; polarity flips and gold-informed
task-score regressions reject the edit. An accepted `abstention_rule`
clean-to-unresolved demotion re-queues the source exactly once for the
`evidence_obligation` rung, each rung gated and credited individually.

## Metrics

The funnel metrics the framework is designed to report (each step counted
independently in the outcome funnel):

| Metric | Definition |
|---|---|
| Repairability rate | failures with a legal RepairPlan / failures on the evolvable surface |
| Unaddressable framework failure rate | `UNADDRESSABLE` count — framework quality indicator, target ≈ 0 |
| Execution rate | executed repairs / legal RepairPlans |
| Causal effect rate | target edge changed / executed repairs |
| Blocker closure rate | blocking predicate cleared / target edge changed |
| Source improvement rate | source detector improved / blocker cleared |
| Transfer gain | real gain of an evolved skill on independent cases — the core value metric |
| Framework failure rate | unaddressable / authority conflict / route mismatch / schema mismatch / state desync / infra — never mixed with learning rejects |

Skill credit binds target edge, execution, effect, fitness and transfer;
provider resampling, proxy observables and random verdict movement earn no
credit.

## Design Principles

1. **Complex detector, simple evolution control plane.** The detection layer
   may be complex; the control plane must be verifiable.
2. **One fact, one authority.** Many projections, one source of truth.
3. **Repairability before evolution.** Repairability is established first;
   evolution starts after.
4. **Multi-layer skills, one causal contract.** L1/L2/Meta novelty comes
   from different learning surfaces, not different failure semantics.
5. **Meta learns strategy, not truth.**
6. **Type information organizes experience, not causal truth.**
7. **Framework failure ≠ learning failure**, separated in all results.
8. **Execution before credit.** No execution, no causal credit.
9. **Causal effect before fitness.** A coincidental label improvement
   without target-edge movement proves nothing.
10. **Generalization before strong skill claim.** Source-case improvement
    proves local repair; only cross-case transfer supports a skill claim.

## Semantic Change Register

The refactor allowed exactly ten behavior changes against the frozen
baseline; everything else is behavior-equivalent. This register is the
normative record for interpreting outcome differences between old and new
artifacts:

| # | Change | Where |
|---|---|---|
| C1 | Single repairability authority; `should_evolve` absorbed; only `REPAIRABLE` evolves | `causal/repairability.py` |
| C2 | Unaddressable redefined as `UNADDRESSABLE_FRAMEWORK_DEFECT`, peeled out of no-edit statistics | `causal/repairability.py`, `loop/machine.py` |
| C3 | Unified `CausalFailureEpisode` + terminal causal cut; three attribution sites merged into one | `causal/` |
| C4 | Hard detector/evolution ownership boundary; validator/transport/route/config defects never produce skills | `causal/repairability.py` |
| C5 | HTC-TES demoted to advisory; owner-override and module-filter powers removed | `typing/` |
| C6 | L1/L2 share the canonical RepairPlan protocol and one orchestration path | `causal/plan.py`, `loop/runner.py` |
| C7 | Meta redefined as cross-case strategy learner; owner/cut/repairability/hard-gate channels type-level removed | `meta/` |
| C8 | Four-layer outcome funnel replaces `skip_*` vocabulary | `loop/machine.py` |
| C9 | Convergence consumes only complete repair attempts | `loop/runner.py` |
| C10 | Full causal credit chain; each link evidenced by its own layer | `evaluation/`, `loop/` |

## Route Resolution

All provider routes resolve through `sehd_rise/platform/routes.py` without
dependencies: repository `.env` (never overriding process variables) →
route-level `SEHD_ROUTE_<ROUTE>_*` → purpose-level `SEHD_MUTATION_*` /
`SEHD_DETECTOR_*` / `SEHD_JUDGE_*` → global `SEHD_PROVIDER_*` → config file
values → official DeepSeek defaults. Only the name of the key environment
variable is retained; credentials are read at the HTTP boundary and never
written to source, prompts, result files or logs. Explicit non-official
proxy routes fail closed when their selected key is absent and never
silently substitute `DEEPSEEK_API_KEY`.

## Evidence Boundaries

What the repository establishes today:

- **Architecture**: 13 layers + root entry, acyclic import discipline
  mechanically enforced (`tools/parity/check_layers.py`, 83 files / 14
  layers, no violations); full-package import smoke clean.
- **Behavioral parity with the frozen baseline**: seven offline harnesses
  green — serialization round-trips, causal classification, detection
  contract (74 checks), effect/gate/promotion decisions, experience/typing,
  planning prompts, meta legality enumeration. All deviations are documented
  and bound to C1–C10 register entries.
- **Live operation**: real-provider end-to-end runs of both CLI entries
  (`detect` / `evolve`) with complete artifact sets; the loop state machine
  unit suite passes 10/10; e2e scenarios cover independent `UNADDRESSABLE`
  counting, non-learning events excluded from convergence, and checkpoint
  resume equivalence.

What is **not** established:

- No out-of-sample detector improvement is claimed. Evolution artifacts are
  development evidence; a generalization claim requires a sealed, independent
  holdout evaluation run as an experiment-layer protocol (see
  `experiments/README.md`), with concrete intermediate outputs per the
  advisor requirements.
- The end-to-end old-vs-new `compare_run.py` parity harness exists but was
  not part of acceptance (user decision); acceptance evidence is the
  real-provider evolution run plus the seven offline harnesses.
- Type telemetry and strategy suggestions are observability; they are not
  evidence that any detector direction improved.
