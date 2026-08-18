# agent-hallucination 工作区（当前入口）

本仓库当前只有一个活跃方向：**基于 `detector_v2` 的自进化幻觉检测器**。

## 从这里开始（活跃设计文档）

- **[SELF_EVOLUTION_V3_DESIGN_FOR_DETECTOR_V2.md](SELF_EVOLUTION_V3_DESIGN_FOR_DETECTOR_V2.md)** —— V3 自进化框架设计真源：
  机制级失效面、L1/L2 双向机制与防 hack、artifact 合同、G1/G2a/G2b/G3 门、
  skill 贡献淘汰、evaluation / meta-evaluation、M0-M5 里程碑。
- **[detector_v2/README.md](detector_v2/README.md)** —— 当前检测器（冻结基座）的使用与结构说明。
- **[evolution_v3/README.md](evolution_v3/README.md)** —— 干预式自进化框架的实现与实验说明。
- **[SELF_EVOLUTION_V3_AUDIT_AND_EXPERIMENT_PROTOCOL.md](SELF_EVOLUTION_V3_AUDIT_AND_EXPERIMENT_PROTOCOL.md)**
  —— 2026-08-19 系统审查结论、修复清单、实验阻断条件和真实验证协议。
- **[evolution_v3/experiments/tuning100_validation50_experiment_report.md](evolution_v3/experiments/tuning100_validation50_experiment_report.md)**
  —— 历史 100+50 实验记录，仅作探索性机制证据，不是独立泛化结论。
- **[Recursive 与 AI4AI 前沿进展调研.pdf](Recursive%20%E4%B8%8E%20AI4AI%20%E5%89%8D%E6%B2%BF%E8%BF%9B%E5%B1%95%E8%B0%83%E7%A0%94.pdf)** —— RSI / AI4AI 调研（V3 设计的理论输入）。

## 当前状态（2026-08-19）

- 检测器：`detector_v2`（Outcome Judge → Recall Scanner → Causal Upgrade →
  Step Tracer → Defense → 确定性裁决）。
- 检测器基线：`runs_v2/v2_100_20w`（100 例，20 并发）已冻结；20 例 pilot 与
  80 例补充实验均已出结果。
- 自进化框架：`evolution_v3` 已实现 V1→V2→V3→M 四门、defect-injection audit、
  frozen-stage 配对重放、hash-chained ledger、pre-commit registry、rollback 状态恢复、
  source-bound cache 和 run manifest。
- 历史 active registry：`evolution_v3/registry/active.json`，共 5 条干预，可按当前
  executable contract 加载；默认 runtime registry 仍为空。
- 历史 100+50 结果只能作为探索性机制证据：数据被用于开发/重复调优，active
  验证臂主要是 frozen replay，不能证明独立外部泛化提升。
- 全量测试当前为 `191 passed`；正式 393/300 split 因已知历史污染会被代码阻断。
- LLM provider：默认已切换为 **AutoDL**（`https://www.autodl.art/api/v1`，
  `AUTODL_API_KEY`）；官方 DeepSeek 配置保留为显式回滚路径。
- 原计划冻结 300 例终审尚未读取，但已发现与 tuning100 重叠 44 例，因此不能
  继续作为干净确认集，必须重建 holdout。
- 旧框架 `sehd_rise2/` 已冻结，不再作为进化方向；其历史设计与证据归档在
  `archive/legacy_docs/`，仅作追溯，不作为当前工作依据。

## 旧文档去向

根目录不再保留过时设计文档。历史文档（含旧 EvoGuard / fix-val / SEHD-RISE2
诊断与重构方案）统一归档在：

- `archive/legacy_docs/`
  - `README_SEHD_RISE2_LEGACY.md`
  - `DETECTOR_OUTCOME_V2_PROPOSAL.md`
  - `EvoGuard_experiment_plan_and_iteration_roadmap.md`
  - `EvoGuard_self_evolving_hallucination_detection_project_design.md`
  - `FIX_VALIDATE_ITERATION_PLAN.md`
  - `SEHD_RISE2_ARCHITECTURE_DIAGNOSIS.md`
  - `SEHD_RISE2_V2_REFACTOR_PLAN.md`
  - `SELF_EVOLUTION_REDESIGN_PROPOSAL.md`
  - `V2_1_IMPROVEMENT_PLAN.md`

这些文件不再出现在工作区入口，需要追溯历史时再查阅。
