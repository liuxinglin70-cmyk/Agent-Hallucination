# Phase 7 Local Agent Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether the Trace-to-Evidence-Contract idea works on the local trajectory dataset strongly enough to proceed to real Agent system validation.

**Architecture:** Reuse Phase 6.1 contracts and TraceBench cache. Add a local-readiness layer that samples 4 agent frameworks, parses SWE-agent and Terminus2 trajectories, runs a non-oracular Unitizer that extracts candidates from evidence only, evaluates contract-v2 on those units, and produces a runtime-readiness go/no-go report.

**Tech Stack:** Python standard library, existing TraceBench artifact readers, existing Phase 6.1 contract executor, `unittest`, Notion MCP.

---

### Task 1: Four-Framework Trace Reader

**Files:**
- Create: `验证文件/agent-hallucination-pilot/tests/test_phase7_local_readiness.py`
- Create: `验证文件/agent-hallucination-pilot/scripts/phase7_local_readiness.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_swe_agent_traj_builds_trace():
    record = parse_swe_agent_members("case", {"case.traj": b"{...}"}, {})
    assert record["agent_system"] == "SWE-agent"
    assert record["trace"][0]["tool_call"]["arguments"]["command"] == "pytest"

def test_parse_terminus_episode_logs_builds_trace():
    record = parse_terminus_members("case", {"episode-0/response.txt": b"{...}"}, {})
    assert record["agent_system"] == "Terminus2"
    assert record["trace"][0]["agent_claim"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_phase7_local_readiness -v`

Expected: FAIL because `scripts.phase7_local_readiness` does not exist.

- [ ] **Step 3: Implement minimal parsers**

Implement `parse_swe_agent_members`, `parse_terminus_members`, `record_from_tracebench_row`, and safe artifact selection over cached or downloadable TraceBench rows.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_phase7_local_readiness -v`

Expected: PASS.

### Task 2: Non-Oracular Unitizer And Readiness Metrics

**Files:**
- Modify: `验证文件/agent-hallucination-pilot/tests/test_phase7_local_readiness.py`
- Modify: `验证文件/agent-hallucination-pilot/scripts/phase7_local_readiness.py`
- Create: `验证文件/agent-hallucination-pilot/scripts/run_phase7_local_readiness.py`

- [ ] **Step 1: Write failing tests for Unitizer**

Tests must verify the Unitizer returns candidate units with no `trigger`, no gold label, and enough evidence cards for contract execution.

- [ ] **Step 2: Implement Unitizer and gold surrogate**

Use only visible trace evidence to extract symbol availability, patch-effect, helper-wiring, and ordinary-boundary units. Gold surrogate is a separate Codex-operator adjudication function and not visible to contract prediction.

- [ ] **Step 3: Implement readiness gate**

Gate requires 4 agent frameworks, Evidence F1 >= 0.70, Boundary FP <= 0.10, evidence sensitivity AUC >= 0.70, runtime false interruption <= 0.10, and generated units free of oracle trigger fields.

### Task 3: Run And Report

**Files:**
- Generate: `data/phase7_local_readiness/*`
- Generate: `outputs/phase7_local_readiness/*`
- Generate: `results/phase7_local_readiness_*`
- Update: Notion page `37ca24fb2ccd8176918ac162d5a9e7e2`

- [ ] **Step 1: Run local readiness experiment**

Run: `python scripts/run_phase7_local_readiness.py --per-agent 8`

Expected: writes manifest, units, predictions, metrics, and go/no-go report.

- [ ] **Step 2: Run full tests**

Run: `python -m unittest discover -s tests -v`

Expected: PASS.

- [ ] **Step 3: Use idea-judge and nature-academic-search**

Use current literature snapshot to assess whether the expanded local evidence supports real-agent-system validation.

- [ ] **Step 4: Append Notion result**

Append concise Phase 7 result, metrics, limitations, and real-agent-system decision to the Notion page.
