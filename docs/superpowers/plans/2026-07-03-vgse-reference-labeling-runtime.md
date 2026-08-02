# VG-SE Reference Labeling Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a leakage-clean VG-SE canonical v2 substrate and a no-network-by-default adjudication runtime with content-addressed cache, bounded concurrency, role-level checkpoints, resume, adaptive judge routing, and offline preflight.

**Architecture:** Keep dataset construction and online runtime separate. Extend the substrate builder with supplemental/exclusion inputs, and add a focused runtime module that owns request identity, cache/checkpoint state, concurrency, retry classification, preflight, and cost accounting. Keep `run_vgse_adjudication.py` as the CLI/protocol orchestrator.

**Tech Stack:** Python stdlib, `unittest`, JSON/JSONL, `ThreadPoolExecutor`, atomic `Path.replace`, OpenAI-compatible HTTP only behind explicit `--execute`.

---

### Task 1: Canonical split exclusions

**Files:**
- Modify: `验证文件/agent-hallucination-pilot/tests/test_vgse_experimental_substrate.py`
- Modify: `验证文件/agent-hallucination-pilot/scripts/build_vgse_experimental_substrate.py`

- [ ] Write failing tests for supplemental sources, exclusion case IDs, overlap report, and zero-overlap quality gate.
- [ ] Run `python -m unittest tests.test_vgse_experimental_substrate -v` and verify the new tests fail because the CLI/functions do not exist.
- [ ] Add repeated `--supplemental-cases-jsonl` and `--exclude-cases-jsonl`; filter exclusions before selection and preserve provenance counts.
- [ ] Re-run the test module and verify green.

### Task 2: Request identity and cache safety

**Files:**
- Create: `验证文件/agent-hallucination-pilot/scripts/vgse_adjudication_runtime.py`
- Create: `验证文件/agent-hallucination-pilot/tests/test_vgse_adjudication_runtime.py`

- [ ] Write failing tests proving stable request keys, atomic successful cache reuse, and failed-cache retry.
- [ ] Verify red with `python -m unittest tests.test_vgse_adjudication_runtime -v`.
- [ ] Implement canonical request specs, schema-valid cache records, and atomic JSON writes.
- [ ] Verify green.

### Task 3: Checkpoint and resume

**Files:**
- Modify: `tests/test_vgse_adjudication_runtime.py`
- Modify: `scripts/vgse_adjudication_runtime.py`

- [ ] Write failing tests for role-level resume, config-hash mismatch, and final deterministic ordering.
- [ ] Verify red.
- [ ] Implement checkpoint state and reconstruction APIs.
- [ ] Verify green.

### Task 4: Bounded concurrency and retry

**Files:**
- Modify: `tests/test_vgse_adjudication_runtime.py`
- Modify: `scripts/vgse_adjudication_runtime.py`

- [ ] Write failing tests using an injected transport to prove max concurrency, no duplicate request execution, retryable failure recovery, and permanent failure behavior.
- [ ] Verify red.
- [ ] Implement `ThreadPoolExecutor`, per-model limits, retry classification, jittered backoff hooks, and `Retry-After` support.
- [ ] Verify green.

### Task 5: Offline preflight and safe CLI

**Files:**
- Modify: `tests/test_vgse_adjudication.py`
- Modify: `scripts/run_vgse_adjudication.py`
- Create: `验证文件/agent-hallucination-pilot/configs/autodl_model_catalog_20260703.json`

- [ ] Write failing tests that the default command performs preflight without keys or transport and that `--execute` is required for network calls.
- [ ] Verify red.
- [ ] Add immutable output directories, call manifest, prompt/context statistics, cache coverage, cost estimate, and explicit execute gate.
- [ ] Verify green and run the complete regression suite.

### Task 6: Build canonical v2 and offline artifacts

**Files:**
- Create: `验证文件/agent-hallucination-pilot/data/vgse_experimental_substrate_20260703_v2/*`
- Create: `验证文件/agent-hallucination-pilot/runs/vgse_reference_preflight_20260703/*`

- [ ] Run the builder with local screening+supplemental data, all historical exclusion sources, and `--fetch-source-limit 0`.
- [ ] Verify 600 rows, 200/100/300 split, overlap=0 for every exclusion source, and leakage=0.
- [ ] Run offline preflight only; verify no raw API response/cache file was created.

### Task 7: Documentation and verification

**Files:**
- Modify: `验证文件/agent-hallucination-pilot/README.md`
- Create: `过程文档/2026-07-03_vgse_gold_protocol_model_selection_and_runtime.md`
- Update: Notion `9.实验日志`

- [ ] Document model-market snapshot, reference-label tiers, cache/concurrency/resume behavior, commands, costs, and limitations.
- [ ] Run all relevant `unittest` modules and `py_compile`.
- [ ] Fetch/update/re-fetch Notion and confirm the 2026-07-03 section and artifact paths.
