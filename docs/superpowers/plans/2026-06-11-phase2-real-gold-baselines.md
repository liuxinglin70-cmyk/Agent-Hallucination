# Phase 2 Real Gold And Strong Baselines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run Phase 2 for real coding-agent trajectories: operator gold labels, complete-trajectory LLM baselines, static evidence checks, and paper-style metrics.

**Architecture:** Reuse the existing `agent-hallucination-pilot` layout and Phase 1 DeepSeek runner. Keep core schema, chunking, metrics, and static baseline in `scripts/phase2_pipeline.py`; keep API orchestration in `scripts/run_phase2_experiment.py`; write reports under `results/phase2_*`.

**Tech Stack:** Python standard library, `unittest`, DeepSeek-V4-Flash via AutoDL chat completions, Notion MCP update.

---

### Task 1: Phase 2 Core Utilities

**Files:**
- Create: `验证文件/agent-hallucination-pilot/scripts/phase2_pipeline.py`
- Test: `验证文件/agent-hallucination-pilot/tests/test_phase2_pipeline.py`

- [x] Write failing tests for Phase 2 gold schema, complete-record chunking, metric computation, static evidence detection, and method ranking.
- [x] Implement minimal utilities to pass those tests.
- [x] Run `python -m unittest tests.test_phase2_pipeline -v`.

### Task 2: Prompt And Runner

**Files:**
- Create: `验证文件/agent-hallucination-pilot/prompts/phase2_real_gold_baselines/*.md`
- Create: `验证文件/agent-hallucination-pilot/scripts/run_phase2_experiment.py`

- [ ] Add strict JSON prompts for direct full-trace judge, AgentHallu/MIRAGE-style attribution, generic code review, hierarchical skill verifier, and optional operator annotation.
- [ ] Implement chunked complete-trajectory API runner with per-chunk raw outputs and merged predictions.
- [ ] Add static evidence baseline without API calls.

### Task 3: Reports

**Files:**
- Create: `验证文件/agent-hallucination-pilot/scripts/summarize_phase2_results.py`
- Create: `验证文件/agent-hallucination-pilot/results/phase2_summary.md`

- [ ] Compute precision, recall, F1, first-step localization, unsupported-unit score, evidence citation F1, ordinary-failure FP rate, and long-context degradation.
- [ ] Write a concise Markdown summary and JSON metrics.

### Task 4: Verification And Notion

**Files:**
- Modify: Notion page `Coding Agent Skill Agent`

- [ ] Run Phase 2 with DeepSeek-V4-Flash using complete trajectory chunks.
- [ ] Run the full local test suite.
- [ ] Run Idea Judge with current literature checks.
- [ ] Append Phase 2 results and Idea Judge judgement to Notion.
