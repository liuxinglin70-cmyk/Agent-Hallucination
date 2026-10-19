# 机制优化同组100例：99有效、1例用户允许失败

排除案例：Magentic_One/031.json。因持续模型请求超时，用户要求停止并不再补测；原100例顺序/输入/金标与历史记录保留。

| 同一99例指标 | 前次预测基线 | 本次 |
|---|---:|---:|
| 权威结果 | 99/99 | 99/99 |
| 检测正确 | 75/99 (75.76%) | 73/99 (73.74%) |
| 首错定位 | 26/61 (42.62%) | 25/61 (40.98%) |
| 误报FP | 4 | 6 |
| 漏报FN | 20 | 20 |

本轮未达预期收益。检测与定位下降，误报增加；不能将运行完成率或结构化比较记录当作效果收益。

原100例请求口径：99权威、1失败，73例权威判对；全请求正确数73/100，首错25/62。失败例不是一次阴性判定。

99例采用路径开始请求843；失败例开始请求24；全部开始请求867。
终止时有1个已开始请求缺结束记录；另12条结束记录仅排队失败，未计为已开始请求。未知远端完成/usage不补造，不估算货币费用。
有效案例耗时中位数970.900秒，p95为3789.300秒。

实际使用预测上下文42例，其中有模型约束预测26例；产生行动候选5例，比较修复至少成功一次2例。

配对变化、三套历史基线和成本见[benefit-comparison.json](benefit-comparison.json)；逐例采用来源见[final-evaluation.json](final-evaluation.json)；验收证据见[completion-audit.json](completion-audit.json)。

One user-authorized runtime exclusion, applied to every matched baseline. Original 100-case denominators retained separately. Historical source/budget differences and same-cohort reuse preclude isolated causal or independent generalization claims.

同99例采用路径请求对比：786→843（+7.25%）；耗时中位数966.2→970.9秒（+0.49%），p95为3901.4→3789.3秒（-2.87%）。全部878条finished中70条缺usage，已知token仅为下界，不构成完整账单。

后续按用户此前授权进入优化分析；证据与候选根因见[optimization-analysis.json](optimization-analysis.json)，不重新请求排除例。
