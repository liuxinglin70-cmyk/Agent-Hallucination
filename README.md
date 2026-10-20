# Agent Hallucination

面向任务交付的 Agent 幻觉检测、首错定位与受控自进化框架。检测器结合任务义务、原始证据、同偏差溯源和独立抗辩给出结论；开发框架使用配对回放及正常邻居保护评估候选更新。

已支持高危步骤的前缀约束预测与真实下一步比较：复用风险筛选，受限地预测允许/禁止动作，再交由原Judge投票及听证核验。新增诊断不自行定罪；部署配置默认开启judge_context，自定义配置缺省off。通用共享义务规则、严格微事件时序与两类反例学习仍为后续研究。

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [核心技术文档](docs/technical-solution.md) | 当前模块、调用关系、输入输出、权限与维护合同 |
| [研究主线与创新路线](docs/research-positioning-and-roadmap.md) | 理论解释、最近工作比较、候选创新、单步扩展及论文表述 |
| [实验日志](docs/experiment-log.md) | 已完成实验的结论、分母、失败与成本解释 |
| [结果与证据索引](docs/results/README.md) | 原始报告归档、来源身份、校验清单及历史对比 |
| [组会汇报](docs/group-meetings/20261015-results-and-research.md) | 2026-10-15阶段结果、方法解释与研究问题 |
| [脚本入口](docs/technical-solution.md#脚本入口) | 离线验证、证据读取、冻结预检与真实运行边界 |
| [实施台账](IMPLEMENTATION_PLAN.md) | 当前任务、验收证据、待办及四项续接检查点 |

历史审计保留在 `docs/archive/`，按原日期阅读。当前行为以技术文档及代码为准，研究设计和历史审计不赋予新接口实现状态。

## 已完成结果

最新全693例已完成主轮、一次统一恢复及最后分类补测：690/693权威，检测531/690=76.96%（全请求76.62%），macro-F1 75.98%，首错206/443=46.50%，TP/FP/FN/TN=335/53/106/196。三例Defense inconclusive未决，补测轮次已用尽，停止重试。[最终报告](docs/results/full693-20261020/report.md)保留全部轮次、采纳与成本审计。

对历史653例的共同权威648例，检测56修正/44回退；排除两例步号映射变化后，414例阳性定位101修正/18回退。不同底座、预算和开发曝光限制归因，论文baseline只作数值参照，见[同案与论文对比](docs/results/full693-20261020/historical-paper-comparison.md)。近期随机100、新100机制两轮、旧样本复测与早期自进化的历史分母及失败统一见[实验日志](docs/experiment-log.md)；候选覆盖、HTTP200和离线验收不等同真实净收益，尚无新增真实进化准入。

## 安装与使用

Python 3.10及以上，在 Git 仓库根目录执行：

```bash
python -m pip install -e ".[test]"
python -m detector --help
python -m evolution --help
python -m experiments --help
python scripts/verify.py --scope code_only
```

默认检测配置为 `detector/configs/autodl.json`，模型为 `DeepSeek-V4.1-Flash-1017`（judge、defense、recall 同步）。[API 容量实测](docs/results/api-capacity-20261019/report.md)建议日常 API 并发64，合成负载最高全通过档位128；未确认供应商硬限额。凭据读取本地 `.env` 或 `AUTODL_API_KEY`，不入 Git。原始数据保持在 `datasets/raw/agenthallu/AgentHallu/`，由 `datasets/integrity.json` 校验。

以下检测命令会调用已配置的模型服务：

```bash
python -m detector detect --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json --case-id BFCL/001.json --output-dir artifacts/detector/single
```

当前诊断默认为 `tracer_context` / `semantic_proxy`。默认部署配置（AutoDL及DeepSeek）以 `causal_audit.next_action_mode=judge_context` 开启下一步预测与行动比较；`off` 显式关闭，`shadow` 为旁路。当前AutoDL部署的新增额度为10800秒、1048576输入UTF-8字节、65536输出token及2次可选请求，单次预测/比较修复上限32768；DeepSeek配置保留自身180秒/60000字节/8192 token额度，仍保护原诊断预留；显式自定义配置不继承部署文件，未声明该字段时保持off。复现实验使用[完整冻结配置](docs/results/next-action100-20261015/config.json)，该文件属于历史测量，不能当作当前AutoDL默认配置。风险与动作审计无独立定罪或定位权限；真实 teacher-forced PPL 仍 unsupported，阴性反馈默认关闭。完整命令、回放与预算见[技术合同](docs/technical-solution.md)。

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
