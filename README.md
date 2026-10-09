# Agent Hallucination

结果性幻觉检测器及受控自进化框架。检测器判断 Agent 轨迹中的交付错误并定位责任步骤；进化框架诊断稳定失败、开发干预，经配对评估和四门准入后发布版本。

## 📚 文档

- [代码优化实施台账](IMPLEMENTATION_PLAN.md)：逐模块重构、缺陷修复、因果审计实现、离线验收和当前进度；本期不开展实验。
- [实验结果与证据索引](docs/results/README.md)：已完成结果、待执行计划、原始报告和账本归档。

- [疑惑度与因果轨迹审计优化设计](docs/uncertainty-guided-causal-audit-design-20261008.md)：拟议的快慢审计、定位反哺检测、代码接入点及验收方案；风险画像、局部审计与听证上下文已接通，完整验收状态见实施台账。
- [组会汇报｜疑惑度反哺检测](docs/group-meeting-uncertainty-causal-feedback-20261008.md)：可直接讨论的简明方案与决策点。
- [核心技术文档](docs/technical-solution.md)：系统模型、模块职责、合同、审查结论和维护规则。
- [实验日志](docs/experiment-log.md)：历史实验、数值、失败、更正、旧方案及来源摘要。
- [检测器 vs AgentHallu 论文基线对比](docs/detector-vs-agenthallu-baseline-20260927.md)：653 例全量基线与论文 13 个单模型基线的判定／定位对比，含可比性限制。
- [693 例自进化划分与执行计划](docs/evolution-g0-reuse-plan-20260928.md)：完整语料的开发、门控、验证分工及 G0 快照入口。
- [自进化失败机制分析](docs/self_evolution_failure_analysis.md)：失败机制、测量限制及修复讨论。
- [自进化结果分析](docs/self-evolution-analysis-20260927.md)：三轮 0 准入的逐轮记录、结构性归因与修复建议。

首个面向 693 例语料的真实基线测量已完成（2026-09-27）：653 例测量清单全覆盖，651 条权威、2 条降级；权威判定准确率 0.7527，责任步精确定位 114/419 = 0.2721。这是**回顾性内部评估**——`development_exposure.json` 覆盖全部 693 例，不构成独立留出集，不主张泛化。随附的 3 轮自进化以 **0 准入**结束（`active_registry` 为空），按运行计划未启动 g1 对比批次，因此**仍无任何证据表明自进化带来稳定收益**。离线测试验证程序行为，不代表模型准确率或独立泛化。

可选因果诊断默认 `off`。`shadow` 生成有界诊断；`tracer_context` 为当前听证提供局部核验证据，并按上下文身份决定 Tracer 回放。新增阴性反馈需要独立开关和有效校准；当前生产配置保持未激活，完整链由显式离线策略和 Fake provider 验证。代码合同见技术文档，进度与验证证据以实施台账为准。

## 🚀 使用

Python 3.10 及以上，在仓库根目录执行：

```bash
python -m pip install -e ".[test]"
python -m detector --help
python -m evolution --help
python -m experiments --help
python scripts/verify.py --scope code_only
```

真实检测需要本地模型服务凭据，会产生模型调用：

```bash
python -m detector detect --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json --case-id BFCL/001.json --output-dir artifacts/detector/single
python -m evolution preview --cycle 1 --mini
python -m evolution experiment --mini
```

默认配置为 detector/configs/autodl.json，凭据读取 AUTODL_API_KEY，.env 不入 Git。数据放在 datasets/raw/agenthallu/AgentHallu，由 datasets/integrity.json 校验。本地完整数据有 693 个案例，默认协议是开发复测，不是独立终审。

## 🗂️ 结构

```text
detector/     检测、证据、模型调用、结果合同及测试
evolution/    开发、门控、晋升、版本、记忆及测试
experiments/  冻结计划、运行和报告
scripts/      验证、预检、研究轮和测量工具
tests/        跨模块与入口回归
datasets/     原始数据完整性、划分与暴露协议
docs/         核心技术文档、唯一实验日志、派生对比报告
artifacts/    本地输出和缓存，不入 Git
```

依赖方向：evolution 和 experiments 使用 detector；detector 不依赖 evolution。新输出统一写 artifacts，长期结论归入实验日志。旧 reports/output 和分散文档从版本库移除；必要回放材料作为测试夹具保留。
