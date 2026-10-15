# 结果与证据索引

更新：2026-10-15。实验结论与分母由[实验日志](../experiment-log.md)集中说明；方法讨论见[研究主文档](../research-positioning-and-roadmap.md)。本目录保存可版本化的结果原件与派生核对，完整逐请求日志和proof保留在本地 `artifacts/`。

## 1. 当前100例最终合并

最新采用结果为99例权威、1例降级；主读数检测76/100、首错27/62。旧首轮72/100、23/62保留为中间阶段；最终结果与历史恢复完成的同100例68/100、22/62比较。6例恢复权威被采用，SmolAgents/043保留原降级结果。100份采用原件均已逐项核对provenance SHA-256。

| 文件 | 用途 |
| --- | --- |
| [final-evaluation.json](localization100-20261015/final-evaluation.json) | 最终100例指标、逐例结果、采用来源及合并规则 |
| [baseline-evaluation.json](localization100-20261015/baseline-evaluation.json) | 历史恢复完成100例，唯一主对照 |
| [first-round-evaluation.json](localization100-20261015/first-round-evaluation.json) | 当前完整首轮，含6降级与1未决 |
| [recovery-evaluation.json](localization100-20261015/recovery-evaluation.json) | 7例恢复评分 |
| [comparison.json](localization100-20261015/comparison.json) | 检测12修正/4回退、定位8修正/3回退及逐例对照 |
| [call-analysis.json](localization100-20261015/call-analysis.json) | 原调用汇总；采用结果740请求，原first100+recovery汇总825请求 |
| [request-journal-audit.json](localization100-20261015/request-journal-audit.json) | 对全部114个journal去重：866个started，859个finished，7个结束状态未知；覆盖废弃外层attempt |
| [recovery-manifest.json](localization100-20261015/recovery-manifest.json) | 原冻结源码/配置身份、期限和恢复计划；实际尝试范围需结合journal审计 |
| [manifest.json](localization100-20261015/manifest.json) | 原件来源、字节数与SHA-256；派生核对另列 |

清单列出的7份运行原件按原字节保存；`request-journal-audit.json` 是本次只读整理产生的派生结果，不改写原调用汇总。补测原目录为 `artifacts/detector/live_checks/live7_20261015_recovery/`，首轮及旧基线为相邻的 `live100_20261014_full/` 与 `live100_20261013_recovered/`。更早中断18例不计入本次866请求范围，历史所有实验总费用仍不能由此得到。

## 2. G0与三轮自进化

G0完成653例清单，651权威，检测490/653，全部419例阳性首错114/419。三轮进化完成，7个开发版本、4个正式候选，0准入；g1未启动。三个阶段的分母不同，不把653例写成每轮巡检规模。

| 文件 | 用途 |
| --- | --- |
| [baseline_report.json](full693-20260927/baseline_report.json) | G0完整机器报告 |
| [baseline_summary.json](full693-20260927/baseline_summary.json) | 原批次执行摘要 |
| [evolution_experiment.json](full693-20260927/evolution_experiment.json) | 三轮开发、门控与终局 |
| [evolution_ledger.jsonl](full693-20260927/evolution_ledger.jsonl) | 原始事件账本 |
| [run_plan.json](full693-20260927/run_plan.json) | 原30开发/10验证/653测量计划 |
| [manifest.json](full693-20260927/manifest.json) | 原件来源与固定SHA-256 |
| [G0论文参照分析](g0-baseline-analysis.md) | 与AgentHallu论文的数值参照及可比性边界 |
| [历史自进化审计](../archive/self-evolution-audit-20260928.md) | 当时的失效链、逐轮和逐案依据 |

其余同目录原件包含active registry、defect audit与候选归档摘要。它们是证据，不作旧文件删除；历史报告的旧路径和术语按原运行语境解释。

## 3. 复用协议与未执行计划

[693例复用划分](../../datasets/protocols/evolution_g0_reuse_20260928.json)将554例开发池与139例本轮隔离验证分开，验证集含90阳性/49正常；两集合合为693例。S24、G96、patrol434的确定性划分及每轮巡检150例由[运行配置](../../evolution/configs/full693_g0_evolution_20260928.json)和 `evolution.splits.build_split` 定义。该计划不等于已完成的新自进化运行，不改变已有全语料暴露事实。

[653例G0精简快照](../../datasets/protocols/g0_baseline_snapshot_20260927.json)用于机会分析与后续划分，源报告摘要保留；40例缺少这次G0结果，其中33例位于开发池、7例位于本轮验证。它不是原始proof或模型请求日志的替代。

完整语料历史上已用于开发，上述读数属于回顾性内部评估。后续独立泛化需要新任务；补测的输出容量、次数和权威性恢复也需纳入效果解释。

## 4. 归档与维护

原运行JSON/JSONL按原字节保存，Git的 `.gitattributes` 禁用这些证据的换行转换。新归档登记source、bytes、SHA-256；派生文件说明输入和计算范围。对历史错误另写有来源的更正，不覆盖原件。活动文档使用相对链接，历史审计按原日期阅读。
