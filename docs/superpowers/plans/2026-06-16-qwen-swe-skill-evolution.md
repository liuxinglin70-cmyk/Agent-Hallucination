# Qwen SWE Skill Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a small, real mini-SWE-agent experiment with Qwen3.5-397B-A17B as both acting model and skill-evolution model, while keeping DeepSeek-V4-Pro only for judge/detector checks.

**Architecture:** Add a reusable runner that separates `L_acting`, `L_skill`, and `L_judge` instead of sharing one model constant. The runner should first create higher-budget Qwen acting trajectories with non-empty patches, then run a small self-evolution loop over those records and estimate full-pipeline cost.

**Tech Stack:** Python 3.12, mini-SWE-agent 2.4.1, OpenAI-compatible AutoDL API, existing Phase 17/19 local self-evolution utilities, Notion/local experiment logs.

---

### Task 1: Confirm Role Boundaries

**Files:**
- Read: Notion page `5. 标准实验技术文档：SWE-HalluBench-Verified`
- Read: Notion page `SWE-HalluBench-Verified 实验日志：AutoDL mini-SWE-agent pilot（2026-06-16）`
- Modify: `过程文档/2026-06-16_swe_hallubench_miniswe_autodl_log.md`

- [x] **Step 1: Verify protocol text**

Confirm that trajectory generation uses only `L_acting`, skill generation/revision uses `L_skill`, and judging/auditing uses `L_judge` or `L_detector`.

- [x] **Step 2: Record the corrected policy**

Append a dated note saying judge is not part of trajectory generation and that the next run uses:

```text
L_acting = Qwen3.5-397B-A17B
L_skill = Qwen3.5-397B-A17B
L_judge = DeepSeek-V4-Pro
```

### Task 2: Create Three-Model Runner

**Files:**
- Create: `验证文件/agent-hallucination-pilot/scripts/run_qwen_swe_skill_evolution.py`
- Read: `验证文件/agent-hallucination-pilot/scripts/run_phase17_mini_swe_self_evolution.py`
- Read: `验证文件/agent-hallucination-pilot/scripts/run_phase19_taxonomy_skill_validation.py`

- [x] **Step 1: Add model CLI flags**

The runner must accept:

```bash
--acting-model Qwen3.5-397B-A17B
--skill-model Qwen3.5-397B-A17B
--judge-model DeepSeek-V4-Pro
--step-limit 30
--case-limit 5
```

- [x] **Step 2: Ensure acting calls only use `--acting-model`**

The mini-SWE-agent command must set:

```text
model.model_name=openai/{acting_model}
agent.step_limit={step_limit}
agent.cost_limit=0
```

- [x] **Step 3: Ensure skill evolution only uses `--skill-model`**

Skill induction and revision OpenAI calls must use `model={skill_model}`.

- [x] **Step 4: Ensure judge/audit only uses `--judge-model`**

Audit/judge calls must use `model={judge_model}` and must happen only after trajectory records exist.

### Task 3: Run Small Experiment

**Files:**
- Remote root: `/root/autodl-tmp/swe_hallubench_miniswe_verified`
- Local log: `过程文档/2026-06-16_swe_hallubench_miniswe_autodl_log.md`
- Notion log page: `381a24fb2ccd81ce9df0eb5763b427bd`

- [x] **Step 1: Connect to AutoDL**

Use the current SSH command. If DNS fails, resolve via public DNS and test the SSH port.

- [x] **Step 2: Run higher-budget trajectories**

Run 5 small instances with `Qwen3.5-397B-A17B` and `step_limit=30`. Continue only if at least one non-empty patch or submission is generated.

- [x] **Step 3: Evaluate patches**

Run the non-Docker official-style evaluator for non-empty patches and record `docker_isolation=false`.

- [x] **Step 4: Run self-evolution**

Run R0 -> R1 on the small records:

```text
skill_v0 = Qwen induction from train records
baseline audit = DeepSeek judge on heldout records
skill_v0 audit = DeepSeek judge on heldout records
skill_v1 = Qwen revision from dev metrics and judge critiques
skill_v1 audit = DeepSeek judge on heldout records
```

### Task 4: Cost Report and Docs

**Files:**
- Modify: `过程文档/2026-06-16_swe_hallubench_miniswe_autodl_log.md`
- Update: Notion page `381a24fb2ccd81ce9df0eb5763b427bd`

- [x] **Step 1: Summarize actual token usage**

Extract prompt/completion tokens from raw API responses where available.

- [x] **Step 2: Estimate full-flow costs**

Estimate 5-case, 50-case, and 500-case costs for:

```text
trajectory generation with Qwen acting
skill induction/revision with Qwen skill
heldout audit with DeepSeek judge
patch evaluation overhead
```

- [x] **Step 3: Record decision**

Write whether the idea currently works, needs iteration, or is blocked by insufficient patch generation.
