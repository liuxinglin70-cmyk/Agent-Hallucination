# VG-SE v10 Novelty and Protocol Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Notion `10.` 重命名并重写为简洁、证据充分、novelty 可防守且实验最小充分的技术文档。

**Architecture:** 先将相关工作按“经验来源—更新对象—接受器—外部验证”四个机制轴归纳，再用最近邻比较确定唯一主创新。实验只保留能验证该创新的 acceptor validation、main comparison、mechanism ablation 和 transfer/runtime boundary。

**Tech Stack:** Notion MCP、Markdown、本地 VG-SE 数据与结果文档、arXiv/ACL/OpenReview/PMLR 一手文献。

---

### Task 1: Source and terminology audit

**Files:**
- Read: existing v10 draft, renamed during execution to `过程文档/2026-07-03_coding_agent_hallucination_detection_evidence_grounded_skill_evolution_v10_1.md`
- Read: Notion page `392a24fb-2ccd-81df-9cd4-efcf4685e24b`

- [ ] 回读标题、论证结构、重复段落和过度实验设计。
- [ ] 固定术语：`coding-agent hallucination detection`、`evidence-grounded skill evolution`、`candidate update`、`evolution gate`、`machine-verifiable feedback`、`gold-light audit`。

### Task 2: Literature landscape

**Files:**
- Modify: `过程文档/2026-07-03_coding_agent_hallucination_detection_evidence_grounded_skill_evolution_v10_1.md`

- [ ] 检索 agent reflection/memory、agent/workflow evolution、skill evolution、evaluation reliability、hallucination/process evaluation 五条文献线。
- [ ] 仅采用已读的一手论文；按机制归类，不按年份罗列。
- [ ] 标出最接近工作及其与本项目的实质差异。

### Task 3: Novelty battle test

**Files:**
- Modify: `过程文档/2026-07-03_coding_agent_hallucination_detection_evidence_grounded_skill_evolution_v10_1.md`

- [ ] 删除“self-evolving skill 本身很新”“无 dense gold 本身很新”等弱主张。
- [ ] 保留一个主创新：对 coding-agent hallucination detector 的结构化 skill update，采用 claim-level machine-verifiable evidence 来独立决定是否 commit。
- [ ] 给出最近邻对照、审稿攻击和可证伪证据要求。

### Task 4: Minimal experiment rewrite

**Files:**
- Modify: `过程文档/2026-07-03_coding_agent_hallucination_detection_evidence_grounded_skill_evolution_v10_1.md`
- Modify: `验证文件/agent-hallucination-pilot/results/r1_4_experiment_registry.md`
- Modify: `验证文件/agent-hallucination-pilot/results/r1_4_baseline_matrix.md`

- [ ] 将实验压缩为 E0 gate validation、E1 main evolution、E2 transfer/runtime 三阶段。
- [ ] 每个实验只绑定一个 claim、一个主比较和必要指标。
- [ ] 删除不能支持主创新的组合实验和重复指标。

### Task 5: Notion update and verification

**Files:**
- Update: Notion page `392a24fb-2ccd-81df-9cd4-efcf4685e24b`

- [ ] 将标题更新为编号加信息性标题。
- [ ] 用重写后的 Markdown 替换正文。
- [ ] 回读页面，验证标题、父页面、核心 novelty、数据事实、实验主线和引用均存在。
- [ ] 运行本地 VG-SE 测试和文档关键字段检查。
