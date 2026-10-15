# Agent Hallucination

面向任务交付的 Agent 幻觉检测、首错定位与受控自进化框架。检测器结合任务义务、原始证据、同偏差溯源和独立抗辩给出结论；开发框架使用配对回放及正常邻居保护评估候选更新。

已支持高危步骤的前缀约束预测与真实下一步比较：复用风险筛选，受限地预测允许/禁止动作，再交由原Judge投票及听证核验。新增诊断不自行定罪，默认关闭。通用共享义务规则、严格微事件时序与两类反例学习仍为后续研究。

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [核心技术文档](docs/technical-solution.md) | 当前模块、调用关系、输入输出、权限与维护合同 |
| [研究主线与创新路线](docs/research-positioning-and-roadmap.md) | 理论解释、最近工作比较、候选创新、单步扩展及论文表述 |
| [实验日志](docs/experiment-log.md) | 已完成实验的结论、分母、失败与成本解释 |
| [结果与证据索引](docs/results/README.md) | 原始报告归档、来源身份、校验清单及历史对比 |
| [组会汇报](docs/group-meetings/20261015-results-and-research.md) | 2026-10-15阶段结果、方法解释与研究问题 |
| [实施台账](IMPLEMENTATION_PLAN.md) | 当前任务、验收证据、待办及四项续接检查点 |

历史审计保留在 `docs/archive/`，按原日期阅读。当前行为以技术文档及代码为准，研究设计和历史审计不赋予新接口实现状态。

## 已完成结果

2026-10-16，下一步预测在原同组100例上完成真实实验及4例统一补测：

| 指标 | 更早历史恢复 | 最近恢复 | 下一步预测本轮 |
| --- | ---: | ---: | ---: |
| 权威判定覆盖 | 99/100 | 99/100 | **100/100** |
| 全请求检测正确 | 68/100 | 76/100 | **76/100** |
| 全部62例阳性的首错命中 | 22/62 | 27/62 | **27/62** |

相对最近基线，检测4修正/4回退、定位2修正/2回退，净增益均为0。43例接受并向Judge发送模型预测，但代表票只有8条模型比较为非unknown；引用有效不等于语义正确。当前没有支持默认开启的综合收益，保留显式开关。

采用结果的本地started请求740→797（+7.70%），案例耗时中位787.597→966.587秒（+22.73%）；p95下降6.10%。全部尝试计入中断技术预试后为940请求，其中14个缺少结束记录；正式100例及补测本身为858请求。已记录token用量存在缺失，不能据此认定降费。前后源码及预算不同，属于内部回顾性比较。

完整结果、成本范围与采用来源见[实验报告](docs/results/next-action100-20261015/report.md)、[实验日志](docs/experiment-log.md)和[结果索引](docs/results/README.md)。历史三轮自进化仍为0准入，本次未执行真实进化。

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

当前诊断默认为 `tracer_context` / `semantic_proxy`。新增 `causal_audit.next_action_mode` 支持 `off`（默认）、`shadow`、`judge_context`；复现实验使用[完整冻结配置](docs/results/next-action100-20261015/config.json)，其中新增工作额度不同于默认值。风险与动作审计无独立定罪或定位权限；真实 teacher-forced PPL 仍 unsupported，阴性反馈默认关闭。完整命令、回放与预算见[技术合同](docs/technical-solution.md)。

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
