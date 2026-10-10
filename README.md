# Agent Hallucination

结果性幻觉检测器及受控自进化框架。检测器判断 Agent 轨迹中的交付错误并定位责任步骤；进化框架诊断稳定失败、开发干预，经配对评估和四门准入后发布版本。

当前按用户请求继续逐包重构。M00 已验收；本轮修改待独立复审。共用准备及开发修订已提取，父版本污染 B31 已修复。B32 的测量来源标识基础已实现，新增12项、相关70项通过，完整纯代码1830项及平台57项通过；路线独立观察计数仍待接通。完整生命周期、最终冗余清理与 `doc` 归档尚未完成；状态和证据见 [实施台账](IMPLEMENTATION_PLAN.md)。

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

可选因果诊断默认 `off`。`shadow` 生成有界诊断；`tracer_context` 为当前听证提供引用与结果值一致的局部算术证书，歧义或缺值回退，并按上下文身份决定 Tracer 回放；证书没有定罪或定位权限。新增阴性反馈需要独立开关和有效校准；当前生产配置保持未激活，完整链由显式离线策略和 Fake provider 验证。Live/Caching的fresh评估携带原逐例证据并重跑全部模型阶段；Detector拒绝可能掩盖干预的外部冻结信封，full-view guard不能复用局部Defense。代码合同见技术文档，进度与验证证据以实施台账为准。

## 🚀 使用

Python 3.10 及以上，在仓库根目录执行：

```bash
python -m pip install -e ".[test]"
python -m detector --help
python -m evolution --help
python -m experiments --help
python scripts/verify.py --scope code_only
```

离线 smoke 在配置的 work_root 下创建独立目录。已有 smoke/capability_smoke 目录会被拒绝；重复验证使用新的 work_root，保留此前输出。Python 调用 EvolutionEngine 时使用 with 或显式 close，关闭后的实例不能继续执行。

真实检测需要本地模型服务凭据，会产生模型调用：

```bash
python -m detector detect --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json --case-id BFCL/001.json --output-dir artifacts/detector/single
python -m evolution preview --cycle 1 --mini
python -m evolution experiment --mini
```

默认配置为 detector/configs/autodl.json，凭据读取 AUTODL_API_KEY，.env 不入 Git。数据放在 datasets/raw/agenthallu/AgentHallu，由 datasets/integrity.json 校验。本地完整数据有 693 个案例，默认协议是开发复测，不是独立终审。

明确传入的 --plan 缺失时直接报错；安装包缺协议资源时使用 --measurement 指定清单。baseline --cases 接受非负前缀长度，0 选择空前缀；paired 接受恰好两个不同的已声明臂，输入错误退出码为 2。实验入口按计划中声明的 arm 解析 registry；默认 `g0` 和 `g0_roll2` 使用显式空 registry。measurement 清单会核验案例顺序摘要、路径冲突、排除集合和标签计数。选择子集后报告保留父清单身份，使用独立选择摘要；无法从逐例标签核算的子集计数显示为未知。运行进度写入 stderr，stdout 输出完整 JSON 报告，保留选择身份与 invocation/case attempt/run lifetime 成本。baseline 有停止原因时退出码为 2。paired/status 和旧配对脚本在选中既有 run 的共同租约内读取；噪声/效果分类使用实际逐例执行身份，缺运行身份或任务输入变化时拒绝评分。

paired --baseline-roll 指定独立同输入的基线复测；相同目录拒绝作为独立roll。experiments.reading为两入口效果读数真源，先排除prediction/state/健康状态改变的不稳定样本；缺复测或没有稳定样本结论未知。默认要求稳定样本决定性修正多于回退且单侧精确McNemar p<0.05，全量读数独立保留；演化终止报告共用metrics.mcnemar_improvement_p。

wheel 包含三个运行包及运行配置；数据集和实验协议由调用方提供。仓库外运行实验时使用 `python -m experiments --measurement /absolute/path/measurement.json baseline --config /absolute/path/config.json --dataset-root /absolute/path/data --runs-root /absolute/path/runs`；自定义计划另加 `--plan /absolute/path/plan.json`。源码和仓库外 wheel 的回环检测、批次、评估及实验 baseline 入口已通过离线验证。

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

冻结前缀时，`scripts/freeze_measured_prefix.py --dataset-root <corpus>` 从实际标签核算导出计数；文件保留案例摘要、父身份与排除角色，可直接作为 `--measurement` 输入。空样本、未知标签、覆盖既有文件及写入运行目录或原始语料目录均拒绝。
