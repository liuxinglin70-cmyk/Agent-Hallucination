# Open Full-Context R1.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the public trajectory dataset as raw full-context coding-agent cases and run an R1.3 pilot that evaluates detector output through judge adjudication.

**Architecture:** Keep the old evidence-card pool as an index/audit artifact only. Add a raw trajectory builder that extracts fields from public Hugging Face trajectory datasets into `swe_hallubench_full_context_case.v1`, then extend the R1.3 runner to consume those cases without using pre-cropped evidence cards as detector input.

**Tech Stack:** Python stdlib, Hugging Face Dataset Viewer API, unittest, AutoDL remote execution.

---

### Task 1: Raw Public Trajectory Dataset Builder

**Files:**
- Create: `验证文件/agent-hallucination-pilot/scripts/build_open_full_context_trajectory_dataset.py`
- Create: `验证文件/agent-hallucination-pilot/tests/test_open_full_context_trajectory_dataset.py`

- [ ] Write tests for preserving `source_payload`, extracting full trajectory/patch/execution fields, recoverability metadata, and split leakage checks.
- [ ] Verify the tests fail because the builder does not exist.
- [ ] Implement the builder with read-only Hugging Face Dataset Viewer pagination and source-audit split reuse.
- [ ] Run the focused tests and `py_compile`.

### Task 2: Runner Input Compatibility

**Files:**
- Modify: `验证文件/agent-hallucination-pilot/scripts/run_full_context_r1_3_validation.py`
- Modify: `验证文件/agent-hallucination-pilot/tests/test_full_context_r1_3_validation.py`

- [ ] Write tests for `--cases-jsonl` loading and prompt creation from rebuilt raw cases.
- [ ] Verify the tests fail before runner support exists.
- [ ] Add case-jsonl loading while preserving run-root behavior.
- [ ] Run focused tests and `py_compile`.

### Task 3: Remote AutoDL Experiment

**Files:**
- Remote project root: `/root/autodl-tmp/swe_hallubench_miniswe_verified`
- Local artifact mirror: `验证文件/agent-hallucination-pilot/data`, `outputs`, `results`

- [ ] Sync updated scripts/tests to AutoDL.
- [ ] Build a balanced raw public trajectory dataset from the five retained public sources.
- [ ] Run a dry-run prompt audit first.
- [ ] Run a cached full-context detector+judge pilot on a suitable sample.
- [ ] Pull back data, outputs, metrics, report, and logs.

### Task 4: Documentation Sync

**Files:**
- Create local backup: `过程文档/2026-06-22_swe_hallubench_technical_document_v7.md`
- Create Notion child page under `过程文档`

- [ ] Write the academic technical document with idea, data reconstruction, experiment setup, result, cost, caveats, and next plan.
- [ ] Create Notion page `7. ...` under the parent page.
- [ ] Fetch back the page and parent listing to verify it exists.
