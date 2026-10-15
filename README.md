# Agent Hallucination

面向任务交付的 Agent 幻觉检测、首错定位与受控自进化框架。检测器结合任务义务、原始证据、同偏差溯源和独立抗辩给出结论；开发框架使用配对回放及正常邻居保护评估候选更新。

后续研究主线是**以任务义务为单位的诊断规则学习**：让同一组对象、证据、时序和修复条件，同时接受合规处置反例与来源/症状对比的检验。单步动作约束已有明确工具失败后的窄场景原型；通用共享规则与两类反例学习仍为拟议方法。

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [核心技术文档](docs/technical-solution.md) | 当前模块、调用关系、输入输出、权限与维护合同 |
| [研究主线与创新路线](docs/research-positioning-and-roadmap.md) | 理论解释、最近工作比较、候选创新、单步扩展及论文表述 |
| [实验日志](docs/experiment-log.md) | 已完成实验的结论、分母、失败与成本解释 |
| [结果与证据索引](docs/results/README.md) | 原始报告归档、来源身份、校验清单及历史对比 |
| [组会汇报](docs/group-meetings/20261015-results-and-research.md) | 最新100例结果、方法解释与下一阶段研究问题 |
| [实施台账](IMPLEMENTATION_PLAN.md) | 当前任务、验收证据、待办及四项续接检查点 |

历史审计保留在 `docs/archive/`，按原日期阅读。当前行为以技术文档及代码为准，研究设计和历史审计不赋予新接口实现状态。

## 已完成结果

2026-10-15 补测最终合并结果与历史恢复完成的同一100例比较：

| 指标 | 历史恢复基线 | 当前最终合并 |
| --- | ---: | ---: |
| 权威判定覆盖 | 99/100 | 99/100 |
| 全请求正确率（非权威不计正确） | 68/100 = 68.00% | **76/100 = 76.00%** |
| 权威准确率 | 68/99 = 68.69% | **76/99 = 76.77%** |
| 全部62例阳性的首错命中 | 22/62 = 35.48% | **27/62 = 43.55%** |

最终有99例权威、1例降级，全部100例都有终局记录。配对检测为12例修正、4例回退，定位为8例修正、3例回退。该结果属于混合源码/预算的回顾性内部比较；尚无单模块因果消融、独立泛化或持续自进化收益。历史三轮进化仍为0准入。

采用结果的请求数为740；首轮及补测全部本地请求日志共记录866个已开始请求，其中7个缺少结束记录。成本分层、补测采用规则和原始证据见[实验日志](docs/experiment-log.md)。

## 安装与使用

Python 3.10及以上，在 Git 仓库根目录执行：

```bash
python -m pip install -e ".[test]"
python -m detector --help
python -m evolution --help
python -m experiments --help
python scripts/verify.py --scope code_only
```

默认检测配置为 `detector/configs/autodl.json`，凭据读取本地 `.env` 或 `AUTODL_API_KEY`，不入 Git。原始数据保持在 `datasets/raw/agenthallu/AgentHallu/`，由 `datasets/integrity.json` 校验。

以下检测命令会调用已配置的模型服务：

```bash
python -m detector detect --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json --case-id BFCL/001.json --output-dir artifacts/detector/single
```

当前诊断默认为 `tracer_context` / `semantic_proxy`，`off` 用作显式对照，`shadow` 输出旁路诊断。风险与动作审计无定罪或定位权限；真实 teacher-forced PPL 仍 unsupported，阴性反馈默认关闭。完整命令、回放、预算与包安装行为见[技术合同](docs/technical-solution.md)。

## 目录与维护

```text
detector/       检测、证据、运行时及模块测试
evolution/      候选开发、门控、版本与模块测试
experiments/    冻结计划、运行、配对评估及报告
scripts/        验证、测量与研究工具
tests/          跨模块与命令行回归
datasets/       原始数据完整性、冻结协议及暴露记录
docs/           当前技术、研究与实验文档
  group-meetings/  当前组会摘要
  results/         结果索引、版本化JSON证据及历史基线比较
  archive/         保留来源的历史审计
artifacts/      本地运行、请求日志、缓存及维护证据，不入Git
```

依赖为 `evolution → detector`、`experiments → detector`。新运行输出统一进入 `artifacts/`；每次实验在日志中记录读数，在结果索引中连接原件；新方法和代码行为分别更新研究、技术文档。合并或迁移文档时同步修订引用，保留原始数据、金标、协议、报告和账本。
