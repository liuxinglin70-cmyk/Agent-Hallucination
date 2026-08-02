# Evidence-Gated VG-SE Technical Document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and verify a human-readable Notion page named `10.` under the specified process-document parent, grounded in current local artifacts, current Notion records, and primary literature on self-evolving agents and skills.

**Architecture:** Treat local data/scripts and Notion experiment records as the project-state layer, primary papers as the method-evidence layer, and the new page as the synthesis layer. Separate evolution proposal from evolution acceptance, replace dense judge labels with evidence-gated/metamorphic evaluation where valid, and preserve a gold-light semantic audit only where executable truth is unavailable.

**Tech Stack:** Local Markdown/JSON/Python artifacts, Notion MCP, primary arXiv/OpenReview/ACL/PMLR sources, PowerShell verification.

---

### Task 1: Establish authoritative project state

**Files:**
- Inspect: `README.md`
- Inspect: `验证文件/agent-hallucination-pilot/README.md`
- Inspect: `验证文件/agent-hallucination-pilot/data/vgse_experimental_substrate_20260703_v2/`
- Inspect: `验证文件/agent-hallucination-pilot/scripts/run_vgse_adjudication.py`
- Inspect: `验证文件/agent-hallucination-pilot/scripts/vgse_adjudication_runtime.py`
- Inspect: `验证文件/agent-hallucination-pilot/configs/vgse/`

- [ ] Fetch the Notion parent and identify the current numbered child pages.
- [ ] Fetch the latest technical pages and experiment log before drafting.
- [ ] Record current dataset counts, split, leakage, evidence tier, verifier limits, runtime behavior, and API status from machine-readable artifacts.

### Task 2: Complete the literature map

**Sources:**
- Primary papers on Reflexion, Voyager, Self-Refine, DGM, SICA, ADAS, AFlow, AgentSquare.
- Primary papers on SkillOpt, EvoSkill, SkillRevise, Socratic-SWE, CODESKILL, PACE, and experience faithfulness.
- Primary papers on reward overoptimization, intrinsic self-correction limits, behavioral/metamorphic testing, weak supervision, and unlabeled accuracy estimation.

- [ ] For each core work, extract what evolves, how direction is proposed, how a change is accepted, what external signal is used, and what limitation applies.
- [ ] Mark peer-reviewed work versus recent preprints.
- [ ] Derive the non-circular design requirements for this project.

### Task 3: Adapt literature into the VG-SE protocol

**Deliverable sections:**
- Dataset construction and split policy.
- Evidence capability tiers and abstention policy.
- Evolution proposal, bounded edit grammar, archive, rejected-edit buffer, and rollback.
- Evidence-gated paired acceptance with statistical control.
- Machine-oracle, metamorphic, causal-faithfulness, transfer, cost, and runtime-intervention metrics.
- Gold-light semantic audit and permitted paper claims.

- [ ] Preserve train/dev/test = 200/100/300 and all existing historical exclusions.
- [ ] Demote LLM judges from truth/acceptance authorities to proposal, triage, and dispute roles.
- [ ] Define exact experiment stages, inputs, outputs, gates, metrics, baselines, ablations, and stopping rules.
- [ ] State what current B-tier data can and cannot prove.

### Task 4: Draft and publish Notion page `10.`

**Notion parent:** `https://app.notion.com/p/378a24fb2ccd806ea3a5ec884137b659`

- [ ] Draft a comprehensive report with an executive verdict, tables, equations/definitions where useful, implementation checklist, risks, and sources.
- [ ] Cite relevant Notion pages inline with page mentions and include public paper links.
- [ ] Create exactly one child page titled `10.` under the requested parent.

### Task 5: Verify completion

- [ ] Re-fetch the created page and verify title, parent, required major sections, key dataset facts, evolution gate, metrics, and sources.
- [ ] Confirm no LLM API experiment was executed as part of documentation work.
- [ ] Compare the final page against every numbered requirement in the user objective.
- [ ] Mark the goal complete only after all checks pass.
