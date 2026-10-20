# 结果与证据索引

更新：2026-10-20。结果解释、分母、失败和费用集中见[实验日志](../experiment-log.md)，本页只维护证据入口。机器登记表见[experiments.json](experiments.json)。完整proof、逐请求日志和每次尝试保留在原`artifacts/`路径，Git内结果副本按各自manifest保持原字节。

## 当前全693最终结果

| 入口 | 用途 |
| --- | --- |
| [最终报告](full693-20261020/report.md) / [逐例评分](full693-20261020/final-evaluation.json) | 690/693权威、531正确、206/443首错；3例未决停止重试 |
| [同案配对](full693-20261020/paired-comparison.json) | 最近random100共同权威97例检测6修正/2回退 |
| [完成审计](full693-20261020/completion-audit.json) / [全部尝试成本](full693-20261020/all-attempt-costs.json) | 冻结身份、原件、commit及5,875次请求的费用范围 |
| [分类补测政策](full693-20261020/category-round2-policy.json) / [采纳清单](full693-20261020/category-round2-outcomes.json) | 失败类1例、降级类5例，逻辑第二轮，恢复3例，之后不再重试 |
| [历史653例与论文对比](full693-20261020/historical-paper-comparison.md) / [逐例派生数据](full693-20261020/historical-paper-comparison.json) | 共同权威648例、映射一致阳性414例；不同模型与预算参照 |
| [归档摘要](full693-20261020/manifest.json) / [原始运行目录](../../artifacts/detector/live_checks/full693_20261020/) | 主轮、recovery1及两类末轮全部原件 |
| [第一次恢复后687权威阶段](../../artifacts/detector/live_checks/full693_20261020/history/before-category2_20261019T220416Z/docs/report.md) | 独立历史汇总，不能替代当前690权威结果 |

## 历史实验与离线证据

本次[DeepSeek论文标准提示693例](deepseek-standard-20261020/report.md)：作者固定提交、非thinking、输出1024、64并发，692可解析，454/693正确，原始step首错181/443。见[同案配对](deepseek-standard-20261020/paired-comparison.json)、[完成审计](deepseek-standard-20261020/completion-audit.json)、[全部尝试成本](deepseek-standard-20261020/all-attempt-costs.json)及[归档摘要](deepseek-standard-20261020/manifest.json)；原请求和响应在`artifacts/paper_baselines/deepseek_standard_20261020/`，不覆写检测器轮次。

后续[DeepSeek逐步提示与三方案汇总](deepseek-stepwise-20261020/report.md)：693/693可评，463正确、macro-F1 59.09%、原始step首错204/443。见[统一693分母](deepseek-stepwise-20261020/aggregate-results.json)、[三组同案配对](deepseek-stepwise-20261020/paired-comparison.json)、[完成审计](deepseek-stepwise-20261020/completion-audit.json)、[全部成本](deepseek-stepwise-20261020/all-attempt-costs.json)及[归档摘要](deepseek-stepwise-20261020/manifest.json)。实际3303前缀、3308请求，首次判错后跳过1966前缀；全部可能请求和真实尝试在`artifacts/paper_baselines/deepseek_stepwise_20261020/`保留。

| 日期 / 实验 | 报告与原件 | 说明 |
| --- | --- | --- |
| 10-19随机100 | [报告](random100-20261019/report.md)、[目录](random100-20261019/)、[manifest](random100-20261019/manifest.json) | DeepSeek随机新样本；主轮与两次恢复分开保留 |
| 10-18新100机制两轮 | [报告](next-action-validation100-20261018/report.md)、[跨轮比较](next-action-validation100-20261018/comparison-across-100-case-runs.md)、[manifest](next-action-validation100-20261018/manifest.json) | 定位改善、原冻结验收未通过 |
| 10-16—17旧样本机制两轮 | [第一轮](mechanism100-20261016/report.md)、[优化复测](mechanism100-optimization-rerun/final-summary.md) | 99案例范围及98共同权威分开解释，完整摘要在各目录archive-manifest |
| 10-16下一步预测100 | [报告](next-action100-20261015/report.md)、[目录](next-action100-20261015/) | 相对最近同案基线净收益0，成本含技术预试 |
| 10-13—15原100补测 | [目录](localization100-20261015/)、[原评分](localization100-20261015/final-evaluation.json)、[请求审计](localization100-20261015/request-journal-audit.json) | 保留首轮、恢复、基线及采用与全部请求的区别 |
| 09-27 G0与三轮进化 | [目录](full693-20260927/)、[G0报告](full693-20260927/baseline_report.json)、[旧论文参照](g0-baseline-analysis.md) | 653案例；旧分析仅按当时语境阅读 |
| 10-18排序与宽度探针 | [manifest](infrastructure-20261018/manifest.json) | 离线候选覆盖，不当作终判效果 |
| 10-19 API容量 | [报告](api-capacity-20261019/report.md)、[manifest](api-capacity-20261019/manifest.json) | 合成压力与模型路由，不是benchmark |
| 10-19及10-20维护 | [原验收记录](repository-maintenance-20261019/README.md)、[本次核对与离线验收](repository-maintenance-20261020/manifest.json) | 文档、引用、脚本和证据保留，不新增真实模型测量 |

## 冻结协议与研究计划

[653例G0快照](../../datasets/protocols/g0_baseline_snapshot_20260927.json)保留源报告摘要；它不是完整proof/journal的替代。旧G0缺少40例，不能称为旧693全量。[复用划分](../../datasets/protocols/evolution_g0_reuse_20260928.json)为554开发/139本轮隔离验证，配置见[运行声明](../../evolution/configs/full693_g0_evolution_20260928.json)；它不代表已完成新一轮进化，也不消除全语料历史曝光。

## 归档规则

登记表区分完成、历史及未执行计划，并连接评分、轮次、冻结身份与原始路径。新实验独立建目录，所有尝试保留；历史manifest中的绝对路径和摘要不因整理重写。更新活动日志及索引，不在多个文档复制整份报告。历史审计和旧计划见[历史文档索引](../archive/README.md)，组会目录保持原样。报告与协议不是清理缓存的目标。
