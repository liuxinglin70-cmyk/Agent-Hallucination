# 当前实施台账

更新：2026-10-20，Asia/Singapore。当前任务在本页，已完成实验在[实验日志](docs/experiment-log.md)，机器原件在[结果索引](docs/results/README.md)，历史计划在[归档索引](docs/archive/README.md)。

## 当前任务与范围

用户已授权汇总论文标准/逐步提示与完整框架结果，更新相应文档后提交并push到origin/main，并处理提交编码。仅整理既有测量、独立离线核验和Git交付，不新增真实调用。检测器源码、输入、金标、旧报告/冻结身份与组会不动；两轮论文baseline分别归档。活动文档UTF-8/LF、证据原字节及受保护成本块按Git索引复核。

## 四项续接检查点

- **当前任务**：三方案汇总、文档同步与编码核验已完成，交付本次Git提交及origin/main推送。
- **进度**：README和实验日志已加入统一693分母对照表；技术文档补充论文测量边界与UTF-8维护规则；两轮报告、逐例评分、配对、全成本、冻结及审计归档，研究方向与结果登记表同步。
- **验证**：新归档27个manifest文件逐项摘要通过；原生产源码/配置、输入、组会及旧全693结果不改。UTF-8初扫无无效字符/BOM/替换字符；7份归档Python语法和活动链接通过。6+6项适配器检查及11项成本消费者测试通过，共23项离线检查；无新增供应商调用。活动文档固定UTF-8/LF，实验日志校准块及归档使用原字节；Git暂存/提交blob将逐项匹配工作区和manifest。
- **未定事项**：Git提交/推送身份与远端一致性以本轮最终回执及HEAD/origin/main记录为准；逐文件暂存/提交字节核对写入artifacts/repository-maintenance/baseline-publication-20261020。标准提示1未解析、框架3未决不续跑，逐步693可评；定位框架仅206对204，思考/总预算仍混杂。

## 最新完成状态

论文逐步提示及三方案已完成：[汇总报告](docs/results/deepseek-stepwise-20261020/report.md)、[统一693评分](docs/results/deepseek-stepwise-20261020/aggregate-results.json)、[三组配对](docs/results/deepseek-stepwise-20261020/paired-comparison.json)、[审计](docs/results/deepseek-stepwise-20261020/completion-audit.json)及[成本](docs/results/deepseek-stepwise-20261020/all-attempt-costs.json)。主要新增认识是逐步提示定位接近完整框架、框架优势集中于减少误报；研究方向与实验日志已同步。

论文标准提示独立测量已完成，详见[报告](docs/results/deepseek-standard-20261020/report.md)、[审计](docs/results/deepseek-standard-20261020/completion-audit.json)及[全部成本](docs/results/deepseek-standard-20261020/all-attempt-costs.json)。直接提示收益与模型能力的分析更新于研究方向；真实请求不再追加，检测器及旧证据保持。

全693最终690/693权威、531/690=76.96%检测、macro-F1 75.98%、首错206/443=46.50%，TP/FP/FN/TN=335/53/106/196。原主轮、recovery1、failed2/degraded1与687权威阶段独立保留，2,849旧运行文件和710结果摘要已通过补测终验。[完整结果](docs/results/full693-20261020/report.md)。

当前311个源码/配置文件逐项匹配2809测试/19门禁的完整验收清单，执行账本2809收集/2809执行且无遗漏/重复；验收原件已复制至[可推送证据](docs/results/repository-maintenance-20261020/manifest.json)。另全量前140补充回归通过；这些是冻结源码的离线合同验收，本轮整理不复述为新运行测试结果。共同权威648案例相对G0检测56修正/44回退；映射一致414阳性定位101修正/18回退，底座和预算差异限制方法归因。[对比报告](docs/results/full693-20261020/historical-paper-comparison.md)。

## 后续工作边界

下一阶段研究以同源码、同预算的事前对照检验候选选择、证据可复核与正常邻居保护。通用规则及双反例学习仍属研究计划，不把已完成P0修复继续列为待办，不把所有历史数据当独立留出。任何新真实实验须有对应授权和独立冻结身份；禁止用已完成轮次的历史控制器自动续跑。具体研究取舍见[研究方向](docs/research-positioning-and-roadmap.md)。
