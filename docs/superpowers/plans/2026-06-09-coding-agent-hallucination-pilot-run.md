# Coding Agent Hallucination Pilot Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a 50-sample controlled coding-agent hallucination pilot, judge it with DeepSeek-V4-Flash through AutoDL, compute metrics, and write the final feasibility judgement.

**Architecture:** Create a self-contained `agent-hallucination-pilot/` directory with JSONL data, prompt templates, Python scripts, tests, outputs, results, and analysis. Keep data generation deterministic and keep API credentials only in environment variables.

**Tech Stack:** Python standard library, `pytest`, OpenAI-compatible HTTP calls via `urllib.request`, JSONL files, Markdown reports.

---

### Task 1: Test and Implement Local Pipeline

**Files:**
- Create: `agent-hallucination-pilot/tests/test_pipeline.py`
- Create: `agent-hallucination-pilot/scripts/pipeline.py`

- [ ] Write tests for JSONL validation, prompt rendering, response parsing, and metric computation.
- [ ] Run `python -m pytest agent-hallucination-pilot/tests -q` and confirm failure because `scripts.pipeline` does not exist.
- [ ] Implement `scripts/pipeline.py` with validation, prompt loading, JSON extraction, normalized scoring, and metric helpers.
- [ ] Run tests and confirm they pass.

### Task 2: Generate Controlled Pilot Data

**Files:**
- Create: `agent-hallucination-pilot/scripts/generate_pilot_data.py`
- Create: `agent-hallucination-pilot/data/pilot_50_gold.jsonl`
- Create: `agent-hallucination-pilot/data/pilot_50_raw.jsonl`
- Create: `agent-hallucination-pilot/annotation_guideline.md`
- Create: `agent-hallucination-pilot/schema.json`

- [ ] Implement deterministic generation of 50 traces: 8 belief, 8 reasoning, 8 acting, 8 claiming-effect, 8 scope, 10 honest failure.
- [ ] Include source metadata using HumanEval, MBPP, BigCodeBench, and manual controlled scope examples.
- [ ] Run generator.
- [ ] Validate all 50 samples with local validation.

### Task 3: Build Judge Prompts and Runner

**Files:**
- Create: `agent-hallucination-pilot/prompts/judge_prompt_zero_shot.md`
- Create: `agent-hallucination-pilot/prompts/judge_prompt_few_shot.md`
- Create: `agent-hallucination-pilot/scripts/run_judge.py`
- Create: `agent-hallucination-pilot/scripts/compute_metrics.py`

- [ ] Write zero-shot and few-shot prompt templates.
- [ ] Implement runner with `AUTODL_API_KEY`, `AUTODL_BASE_URL`, and `JUDGE_MODEL` env vars.
- [ ] Implement raw output saving, parsed JSONL saving, retry-on-invalid-JSON, and metadata logging.
- [ ] Implement metrics computation and confusion matrix writing.

### Task 4: Run DeepSeek-V4-Flash Evaluation

**Files:**
- Create: `agent-hallucination-pilot/outputs/deepseek_predictions_raw.jsonl`
- Create: `agent-hallucination-pilot/outputs/deepseek_predictions_parsed.jsonl`
- Create: `agent-hallucination-pilot/results/pilot_metrics.csv`
- Create: `agent-hallucination-pilot/results/pilot_metrics.md`
- Create: `agent-hallucination-pilot/results/confusion_matrix.csv`
- Create: `agent-hallucination-pilot/analysis/final_feasibility_judgement.md`

- [ ] Run a 5-sample API smoke test.
- [ ] If model ID fails, retry only with casing variant `deepseek-v4-flash` and record the actual model used.
- [ ] Run all 50 samples.
- [ ] Compute metrics.
- [ ] Write final judgement based on pilot gates.

### Task 5: Verification

**Files:**
- Read: all generated outputs and reports.

- [ ] Run `python -m pytest agent-hallucination-pilot/tests -q`.
- [ ] Run local data validation.
- [ ] Confirm `pilot_50_gold.jsonl` has exactly 50 records and the required category distribution.
- [ ] Confirm predictions and metrics files exist.
- [ ] Confirm no real API key is written to disk.
