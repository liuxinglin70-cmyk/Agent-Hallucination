# 原始行动通道优化：最终99例复测

| 指标 | 前次预测 | 首轮机制 | 本次优化 |
|---|---:|---:|---:|
| 权威数 | 99 | 99 | 98 |
| 全99例正确率 | 0.7575757575757576 | 0.7373737373737373 | 0.7272727272727273 |
| 误报FP | 4 | 6 | 5 |
| 漏报FN | 20 | 20 | 21 |
| 首错定位 | 26/61 | 25/61 | 24/61 |

原效果门槛及用户允许的覆盖例外是否满足：False。
采用路径开始请求752，全部开始请求798，缺结束0。
用量缺失27条；无实际账单单价，不估算金额。

Second and final authorized run, same ordered 99 eligible cases; original 100-case denominators and one allowed runtime exclusion also reported. Repeated same-cohort optimization is internal development validation.

排除例从未发请求。逐例原件、配对变化及机制/成本分别见final-evaluation.json、benefit-comparison.json、completion-audit.json。
