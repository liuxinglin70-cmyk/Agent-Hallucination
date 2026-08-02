# Verified Small-Set Skill Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the real SWE-bench Verified Qwen n=3 trajectories and official-style evaluator reports into evidence records, then run a small R0 -> R1 skill self-evolution loop with Qwen as `L_skill` and DeepSeek-V4-Pro as `L_judge`.

**Architecture:** Reuse the Phase 17 evidence and audit functions instead of inventing a new skill loop. Add a focused converter/runner that reads remote Verified artifacts, produces Phase 17 compatible records with post-hoc evaluator evidence, and then runs induction, baseline audit, skill-v0 audit, revision, and skill-v1 audit with strict role separation.

**Tech Stack:** Python 3.12, mini-SWE-agent trajectory JSON, SWE-bench evaluator reports, OpenAI-compatible AutoDL API, existing Phase 17 self-evolution utilities, Notion/local experiment logs.

---

### Task 1: Verify Current Inputs

**Files:**
- Read: `C:\Users\liuxinglin\Desktop\agent hallucination\过程文档\2026-06-16_swe_hallubench_miniswe_autodl_log.md`
- Read: Notion page `381a24fb2ccd81ce9df0eb5763b427bd`
- Read: Notion page `380a24fb2ccd817b8f93c34125fdbe3d`
- Remote read: `/root/autodl-tmp/swe_hallubench_miniswe_verified/runs/verified_qwen_step30_n3/verified_qwen_step30_n3_final_metrics.json`

- [x] **Step 1: Confirm role policy**

Confirm that trajectory generation used only `L_acting=Qwen3.5-397B-A17B`, and judge/model audit happens only after trajectories and evaluator reports exist.

- [x] **Step 2: Confirm usable small set**

Require three records with non-empty final worktree diffs and evaluator reports: one resolved and two unresolved.

### Task 2: Add Tested Verified Record Converter

**Files:**
- Create: `C:\Users\liuxinglin\Desktop\agent hallucination\验证文件\agent-hallucination-pilot\scripts\run_verified_smallset_skill_evolution.py`
- Create: `C:\Users\liuxinglin\Desktop\agent hallucination\验证文件\agent-hallucination-pilot\tests\test_verified_smallset_skill_evolution.py`
- Read: `C:\Users\liuxinglin\Desktop\agent hallucination\验证文件\agent-hallucination-pilot\scripts\run_phase17_mini_swe_self_evolution.py`

- [x] **Step 1: Write failing converter tests**

Test that a synthetic trajectory plus evaluator report becomes a Phase 17 compatible record with:
- `case_id`
- `message_count`
- `assistant_step_count`
- `patch`
- `public_validation`
- `hidden_validation`
- `evidence_cards`
- `post_validation_public`
- `post_validation_hidden`
- at least one `agent_claim`

- [x] **Step 2: Run the focused tests and observe failure**

Run:

```powershell
python -m pytest "C:\Users\liuxinglin\Desktop\agent hallucination\验证文件\agent-hallucination-pilot\tests\test_verified_smallset_skill_evolution.py" -q
```

Expected: import/module missing failure before implementation.

- [x] **Step 3: Implement converter and runner**

Implement CLI flags:

```bash
--lab /root/autodl-tmp/swe_hallubench_miniswe_verified
--run-id verified_qwen_step30_n3
--phase verified_qwen_real_smallset_skill_evolution
--skill-model Qwen3.5-397B-A17B
--judge-model DeepSeek-V4-Pro
--train-count 1
--dev-count 1
--audit-tokens 7000
--skill-tokens 7000
--reuse-records
```

The runner must not call mini-SWE-agent. It consumes existing trajectories and evaluator reports only.

- [x] **Step 4: Run tests and py_compile**

Run:

```powershell
python -m pytest "C:\Users\liuxinglin\Desktop\agent hallucination\验证文件\agent-hallucination-pilot\tests\test_verified_smallset_skill_evolution.py" -q
python -m py_compile "C:\Users\liuxinglin\Desktop\agent hallucination\验证文件\agent-hallucination-pilot\scripts\run_verified_smallset_skill_evolution.py"
```

Expected: both commands exit 0.

### Task 3: Execute Remote Small-Set Self-Evolution

**Files:**
- Remote create/update: `/root/autodl-tmp/swe_hallubench_miniswe_verified/scripts/run_verified_smallset_skill_evolution.py`
- Remote output: `/root/autodl-tmp/swe_hallubench_miniswe_verified/data/verified_qwen_real_smallset_skill_evolution/records.jsonl`
- Remote output: `/root/autodl-tmp/swe_hallubench_miniswe_verified/outputs/verified_qwen_real_smallset_skill_evolution/skill_v0.json`
- Remote output: `/root/autodl-tmp/swe_hallubench_miniswe_verified/outputs/verified_qwen_real_smallset_skill_evolution/skill_v1.json`
- Remote output: `/root/autodl-tmp/swe_hallubench_miniswe_verified/results/verified_qwen_real_smallset_skill_evolution_metrics.json`

- [ ] **Step 1: Upload and compile remote script**

Use Paramiko/SFTP or existing upload path. Run remote `.venv/bin/python -m py_compile`.

Stop note from 2026-06-16: local implementation and tests are complete. Remote upload/run did not start successfully before the user-requested stop. The upload attempt failed because the local Unicode path segment `验证文件` was mangled to `????` inside the ad hoc Paramiko wrapper, and the AutoDL SSH tunnel also continued to intermittently time out. Next run should upload from an ASCII staging path such as `tmp/upload_scripts/run_verified_smallset_skill_evolution.py`, then run this step.

- [ ] **Step 2: Build/reuse records**

Run the script with `--build-records-only` first and verify:
- 3 records
- 3 non-empty patches
- 2 unresolved, 1 resolved
- post-hoc evaluator evidence exists

- [ ] **Step 3: Run R0 -> R1 self-evolution**

Run with:

```bash
source env/autodl_api.env
.venv/bin/python scripts/run_verified_smallset_skill_evolution.py \
  --lab /root/autodl-tmp/swe_hallubench_miniswe_verified \
  --run-id verified_qwen_step30_n3 \
  --phase verified_qwen_real_smallset_skill_evolution \
  --skill-model Qwen3.5-397B-A17B \
  --judge-model DeepSeek-V4-Pro \
  --train-count 1 \
  --dev-count 1 \
  --skill-tokens 7000 \
  --audit-tokens 7000 \
  --reuse-records
```

Expected: metrics JSON and report Markdown are created.

### Task 4: Cost and Decision Logging

**Files:**
- Modify: `C:\Users\liuxinglin\Desktop\agent hallucination\过程文档\2026-06-16_swe_hallubench_miniswe_autodl_log.md`
- Update: Notion page `381a24fb2ccd81ce9df0eb5763b427bd`

- [ ] **Step 1: Extract actual usage**

Extract Qwen skill usage and DeepSeek judge usage from raw API response files. Keep Qwen acting usage from the existing n=3 trajectory metrics.

- [ ] **Step 2: Estimate full-flow costs**

Estimate observed n=3 total and project 10, 20, 50, and 500 cases using the measured per-case acting cost and measured self-evolution overhead.

- [ ] **Step 3: Record decision**

Record whether the idea is supported, needs iteration, or blocked. The decision must be tied to evidence-contract metrics, validation-failure detection, and repairs/regressions, not just model agreement.

### Task 5: Final Verification

**Files:**
- Remote metrics/report files from Task 3
- Local log and plan files
- Notion page `381a24fb2ccd81ce9df0eb5763b427bd`

- [ ] **Step 1: Verify remote artifacts**

Read remote metrics and report. Confirm role separation, records count, usage count, and decision.

- [ ] **Step 2: Verify local and Notion logs**

Search local log and fetch Notion page. Confirm the new section is present and contains the latest decision and next step.
