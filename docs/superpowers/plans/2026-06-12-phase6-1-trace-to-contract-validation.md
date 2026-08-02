# Phase 6.1 Trace-to-Contract Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the ICLR 2027 novelty-upgraded Trace-to-Evidence-Contract plan on complete coding-agent trajectories with contract, counterfactual, and replay-style migration metrics.

**Architecture:** Reuse Phase 5 unit-level evidence-card data and complete Phase 3 trajectories as the labeled held-out pool. Add a Phase 6.1 module that freezes v0/v1/v2 evidence contracts, executes them without gold labels, evaluates strongest direct baselines, performs counterfactual evidence ablations, and writes final go/no-go artifacts.

**Tech Stack:** Python standard library, existing Phase 5 pipeline helpers, DeepSeek-V4-Flash raw direct judge outputs already stored under `outputs/phase5_multitype/raw_chunks`, and `unittest`.

---

### Task 1: Contract Data Model And Tests

**Files:**
- Create: `tests/test_phase6_1_contracts.py`
- Create: `scripts/phase6_1_contracts.py`

- [ ] **Step 1: Write failing tests**

Create tests that build a small Phase 5-style unit, verify v2 contract predictions cite required evidence, verify support-removed counterfactuals reduce confidence/action to `request_evidence`, and verify summary metrics include contract accuracy and evidence sensitivity.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_phase6_1_contracts -v`

Expected: FAIL because `scripts.phase6_1_contracts` does not exist.

- [ ] **Step 3: Implement minimal contract module**

Add contract definitions, evidence graph construction, v0/v1/v2 executors, counterfactual builders, metric computation, split assignment, and report rendering.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_phase6_1_contracts -v`

Expected: PASS.

### Task 2: Runner And Artifacts

**Files:**
- Create: `scripts/run_phase6_1_experiment.py`
- Create: `prompts/phase6_1_contract/generated_checklist_prompt.md`
- Generate: `data/phase6_final/*`, `experiments/phase6_skills/*`, `outputs/phase6_final/*`, `results/phase6_*`

- [ ] **Step 1: Write runner smoke test**

Extend the Phase 6.1 test file to run the runner entry point on a temporary miniature dataset and assert manifest/metrics/report files are created.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_phase6_1_contracts -v`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement runner**

The runner loads `data/phase5_multitype/phase5_units.jsonl`, `data/phase3_80/phase3_80_raw.jsonl`, and `outputs/phase5_multitype/direct_full_trace_evidence_card_judge.json`; preserves complete trajectories for LLM baseline provenance; writes all Phase 6.1 artifacts.

- [ ] **Step 4: Run full validation**

Run: `python scripts/run_phase6_1_experiment.py`

Expected: metrics, counterfactual, migration, and go/no-go reports are created under `results/` and `outputs/phase6_final/`.

### Task 3: Novelty Assessment And Notion Update

**Files:**
- Generate: `results/phase6_idea_judge_final_assessment.md`
- Update: Notion page `37ca24fb2ccd8176918ac162d5a9e7e2`

- [ ] **Step 1: Use idea-judge**

Read `idea-judge/SKILL.md`, assess whether the Phase 6.1 validation supports ICLR 2027 positioning, and save the assessment.

- [ ] **Step 2: Append Notion result**

Append a concise Phase 6.1 result block with metrics, go/no-go decision, limitations, and ICLR risk.

- [ ] **Step 3: Verify completion**

Run unit tests and inspect generated artifacts before claiming the goal is complete.
