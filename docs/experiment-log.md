# 实验日志

更新：2026-10-15。本文集中记录已完成实验的读数与解释；原始机器证据见[结果索引](results/README.md)，当前实现见[技术合同](technical-solution.md)，新方法设计见[研究主文档](research-positioning-and-roadmap.md)。

## 1. 当前结论与统一读数

| 运行 | 权威覆盖 | 权威判定正确 | 全请求正确 | 全部标注阳性首错命中 |
| --- | ---: | ---: | ---: | ---: |
| 2026-09-27 G0，653例 | 651/653 | 490/651 = 75.27% | 490/653 = 75.04% | 114/419 = 27.21% |
| 2026-10-13 历史恢复合并，100例 | 99/100 | 68/99 = 68.69% | 68/100 = 68.00% | 22/62 = 35.48% |
| 2026-10-14 当前首轮，原100例 | 93/100 | 72/93 = 77.42% | 72/100 = 72.00% | 23/62 = 37.10% |
| **2026-10-15 补测最终合并，原100例** | **99/100** | **76/99 = 76.77%** | **76/100 = 76.00%** | **27/62 = 43.55%** |

全请求正确只计权威且判对；降级与未决保留在请求分母。最终100例有99例权威、1例降级，没有缺失结果或缺失终局。评分中的 `unresolved=1` 表示非权威行，不能写成仍有1例未返回。若接受降级终局预测，另一个口径是77/100；本文主读数继续使用76/100。

G0混淆矩阵为TP291、FP34、FN127、TN199。历史100例基线为TP35、FP5、FN26、TN33；当前最终为TP42、FP4、FN19、TN34。最终阳性精确率42/46=91.30%，权威阳性召回率42/61=68.85%，全部62例阳性的权威检出率为42/62=67.74%；Macro-F1为76.61%。权威召回与全部阳性检出率不可混用。

来源：[G0原始报告](results/full693-20260927/baseline_report.json)、[历史100例](results/localization100-20261015/baseline-evaluation.json)、[当前首轮](results/localization100-20261015/first-round-evaluation.json)、[最终合并](results/localization100-20261015/final-evaluation.json)。后3个文件为原件的逐字节归档，身份见[清单](results/localization100-20261015/manifest.json)。

## 2. 100例补测、收益与成本

### 2.1 实际结果采用

补测选择原首轮6例降级及1例未决：BFCL/052、BFCL/122、OpenManus/064、SmolAgents/024、SmolAgents/043、Magentic_One/031、Camel/012。七例中有六例恢复权威并被采用；SmolAgents/043仍非权威，保留首轮降级结果。采用依据为权威性，不依据是否判对；Camel/012恢复为权威阴性但仍与阳性标签不符。

BFCL/052最终为权威阳性，首错第3步与原标签一致。100个采用结果的 `result_sha256` 已与原始文件逐项核对。当前首轮与原始运行报告保留，最终合并不覆盖它们。

冻结manifest描述七例单轮恢复；磁盘实际上保留每例两个外层attempt目录，BFCL/052另有独立续测记录。因此单轮描述不能完整代表实际尝试历史。最终结果按落盘provenance复核，费用按所有日志计入；额外尝试和Defense输出容量变化也是比较口径的一部分。

补测只取消Defense显式32768输出上限，使用服务默认容量；生产源码身份仍为 `460d36ec35487a7c1d5054b3e1a571900fdc6f174ddb19960dfa70042f71d116`。三票、补票、引用验证、听证权限及其他路由按原运行合同保留。补测源文件见[冻结manifest](results/localization100-20261015/recovery-manifest.json)和[7例评分](results/localization100-20261015/recovery-evaluation.json)。

### 2.2 配对变化及推断范围

| 与历史恢复基线的同100例比较 | 修正 | 回退 | 净增 | 描述性单侧精确值 |
| --- | ---: | ---: | ---: | ---: |
| 权威检测正确 | 12 | 4 | +8 | 0.0384 |
| 全部阳性首错命中 | 8 | 3 | +5 | 0.1133 |

数值来自[逐例比较](results/localization100-20261015/comparison.json)，精确值按不一致对的二项尾概率重算。检测结果出现积极的内部配对变化；没有匹配预算与稳定独立复测，不能沿用正式协议的“稳定显著收益”判定，也不能归因于单步模块、并行执行或某项提示修改。两次权威覆盖虽同为99%，非权威案例与状态不同。

当前首轮相对基线为检测10修正/6回退、定位6修正/5回退。补测后较首轮多4个权威正确和4个定位命中，说明可测性恢复影响了部分效果读数；不能把恢复与方法变化合成一个无条件因果结论。

### 2.3 请求数量的三个范围

| 范围 | 请求数 | 含义 |
| --- | ---: | --- |
| 最终采用的100个结果 | 740 | 原调用分析的 `adopted100`；用于描述采用结果的执行构成 |
| 原调用分析中的首轮+补测 | 752+73=825 | 汇总了首轮与补测规范结果，未覆盖全部废弃外层尝试 |
| 所有对应磁盘请求日志去重 | **752+114=866** | 100个首轮journal及14个补测journal；均有queued/started记录 |

866个started请求中859个有finished记录：828个response、31个transport_error；另外7个没有结束记录，其远端是否完成和用量未知。原先“全部HTTP200”不适用于全部请求。该范围不含更早中断的18例运行，也不代表所有历史实验总费用。

[请求日志核对](results/localization100-20261015/request-journal-audit.json)保留每个journal的路径、摘要与计数。原[调用分析](results/localization100-20261015/call-analysis.json)保持原字节，其 `all_attempts` 字段名不能被直接解释为覆盖所有外层attempt。

采用结果的中位案例耗时787.597秒、p95为4154.768秒；历史采用结果分别为430.464秒、3319.947秒。工作时间和不是批次墙钟。缺少usage不记为零费用，目前没有整体提速或精确成本倍率结论。

### 2.4 原首轮7例失败的机制

Defense输出反复达到32768导致OpenManus/064、SmolAgents/024、Camel/012截断或有效票不足；BFCL/052与BFCL/122的Recall引用未通过原通道/locus核验；Magentic_One/031有长推理与运输故障；SmolAgents/043在有效票后仍多数inconclusive。它们分别是容量、引用、服务和语义不确定问题，不能统一归因为模型判断错误。

详细原件：[失败分析](../artifacts/detector/live_checks/live7_20261015_recovery/failure_analysis.md)、[逐例机器证据](../artifacts/detector/live_checks/live7_20261015_recovery/first_round_failures.json)。这两份文件记录其生成时观察，后来的全部请求范围由本节核对补充。

## 3. 历史G0与三轮自进化

2026-09-24至09-27运行将693例分为30例开发、10例验证与653例后续G0测量。三轮进化使用开发池中的18例巡检，未将653例用于每轮进化。

| 轮次 | 开发版本 | L1通过并冻结 | 正式候选结果 | 准入 |
| --- | ---: | ---: | --- | ---: |
| C1 | 3 | 2 | 1不可评估，1收益方向不符 | 0 |
| C2 | 3 | 2 | 2不可评估 | 0 |
| C3 | 1 | 0 | 无正式候选 | 0 |
| 合计 | 7 | 4 | 3 not_evaluated、1 rejected；M均未运行 | 0 |

3个开发版本损伤正常邻居。唯一完成正式行为比较的候选修复1个FP，TP未增加，未满足预先声明的recall目标；这条精度线索不能事后改变目标追认准入。另3个不可评估不能写成算法无效。最终active为空，10例验证仍为8/10，g1测量未启动，周期完成但效果条件未通过，CLI退出2。

保留五类不同失败：独立事实证据缺失（OpenDeepSearch/086）；开发与正式集合的机制机会错位（BFCL/062）；正式回放不稳定（Magentic_One/049）；收益声明错位；正常邻居受损（Camel/048）。后续共享规则研究针对可概括的判别条件，服务故障另行处理。

来源：[三轮原始实验](results/full693-20260927/evolution_experiment.json)、[账本](results/full693-20260927/evolution_ledger.jsonl)、[历史审计](archive/self-evolution-audit-20260928.md)、[G0与论文参照](results/g0-baseline-analysis.md)。后者的系统、底座和样本不同，不能将跨论文数值差异解释为某模块因果增益。

## 4. 其他运行与验证里程碑

| 日期/范围 | 记录与结论 |
| --- | --- |
| 09-16至09-21早期试点 | 有小样本局部修复、正常邻居回退与中断；未形成持续准入收益，不并入G0分母 |
| 09-28复用划分 | 554例开发池、139例本轮隔离验证；全部693例已有开发暴露，不是独立泛化。S24/G96/patrol434的计划和配置见结果索引，无对应新收益结果 |
| 10-09至10-11重构 | 离线验收2138项/59检查属于当时源码，不代表当前模型效果 |
| 10-12十例风险验证 | 8/10完成，检测6/8，全部阳性定位1/6；2条长尾被中止。原件在 `artifacts/detector/live_checks/live10_20261012_riskv2/` |
| 10-12至10-13原100例 | 首次72权威、20案例截止、7 Judge未决、1降级；全请求检测49/100、定位14/62。按权威恢复合并后为68/100、22/62 |
| 历史95交集 | 检测71/95→65/95；全阳性定位18/57→20/57，5修正/3回退。双方检出30例定位15/30→19/30，4修正/0回退；该子集不能代表总体0退化 |
| 10-14最终代码验收 | 源码460d36ec…；2551项/18检查通过。此记录继承原台账，本次只做文档与目录核验，不重跑模型或全量代码验收 |

完整历史审查见 `docs/archive/`。原混合日志和旧实施台账含大量机器输出，原字节保留于本地 `artifacts/repository-maintenance/20261015/`，原始运行JSON及账本继续位于各自目录；旧叙述不作为当前API合同。

## 5. 记录与维护规则

新增记录应包含运行ID、输入和源码身份、完整请求分母、权威/终局/定位状态、采用规则、失败和全部费用范围。已有记录就地更正并保留原始来源；不再粘贴完整proof、源码、逐请求JSON或整份旧设计。原件及其摘要进入结果目录，组会只提炼主要发现，研究主文档解释方法意义。

## 6. 历史成本模型的冻结输入

下列命名块由 `scripts/cost_model_693.py::harvest` 读取，保留原始内容和 `original_source`。它是历史选择样本，不能代替当前100例实际费用。

<details>
<summary>展开冻结校准样本</summary>

<!-- cost-calibration:start -->
```json
{
  "per_case": [
    {
      "case": "OpenDeepSearch/076.json",
      "seconds": 56.1,
      "tokens": 6582,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "OpenDeepSearch/078.json",
      "seconds": 184.3,
      "tokens": 10996,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "SmolAgents/022.json",
      "seconds": 191.8,
      "tokens": 40227,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "BFCL/012.json",
      "seconds": 39.3,
      "tokens": 3234,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "Camel/028.json",
      "seconds": 32.3,
      "tokens": 25603,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "Magentic_One/067.json",
      "seconds": 40.8,
      "tokens": 8918,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "OpenDeepSearch/076.json",
      "seconds": 53.2,
      "tokens": 6867,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "OpenDeepSearch/078.json",
      "seconds": 379.6,
      "tokens": 21186,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "SmolAgents/022.json",
      "seconds": 218.5,
      "tokens": 41174,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "BFCL/012.json",
      "seconds": 43.2,
      "tokens": 3401,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "Camel/028.json",
      "seconds": 42.0,
      "tokens": 25547,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "Magentic_One/067.json",
      "seconds": 34.2,
      "tokens": 8691,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "case": "BFCL/012.json",
      "seconds": 226.2,
      "tokens": 9677,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json#recall_phase"
    },
    {
      "case": "Camel/028.json",
      "seconds": 36.0,
      "tokens": 52150,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json#recall_phase"
    },
    {
      "case": "Magentic_One/067.json",
      "seconds": 101.6,
      "tokens": 31145,
      "state": "verified_clean",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real\\summary.json#recall_phase"
    },
    {
      "case": "BFCL/017.json",
      "seconds": 284.96,
      "source": "docs/experiment-log.md",
      "tokens": 15506,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\BFCL__017.json.rep1.json"
    },
    {
      "case": "BFCL/017.json",
      "seconds": 414.73,
      "source": "docs/experiment-log.md",
      "tokens": 9906,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\BFCL__017.json.rep2.json"
    },
    {
      "case": "Magentic_One/036.json",
      "seconds": 408.39,
      "source": "docs/experiment-log.md",
      "tokens": 21756,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\Magentic_One__036.json.rep1.json"
    },
    {
      "case": "Magentic_One/036.json",
      "seconds": 900.02,
      "source": "docs/experiment-log.md",
      "tokens": 9838,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\Magentic_One__036.json.rep2.json"
    },
    {
      "case": "OpenDeepSearch/002.json",
      "seconds": 12.58,
      "source": "docs/experiment-log.md",
      "tokens": 2953,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\OpenDeepSearch__002.json.rep1.json"
    },
    {
      "case": "OpenDeepSearch/002.json",
      "seconds": 8.71,
      "source": "docs/experiment-log.md",
      "tokens": 2423,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\OpenDeepSearch__002.json.rep2.json"
    },
    {
      "case": "OpenDeepSearch/086.json",
      "seconds": 270.6,
      "source": "docs/experiment-log.md",
      "tokens": 5204,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\OpenDeepSearch__086.json.rep1.json"
    },
    {
      "case": "OpenDeepSearch/086.json",
      "seconds": 128.23,
      "source": "docs/experiment-log.md",
      "tokens": 5681,
      "state": null,
      "study": "single_mechanism",
      "original_source": "reports\\verification_20260917\\single_mechanism\\OpenDeepSearch__086.json.rep2.json"
    },
    {
      "case": "Magentic_One/085.json",
      "seconds": 113.599,
      "tokens": 17359,
      "state": "verified_clean",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\00bfd8430fc7.json"
    },
    {
      "case": "OpenDeepSearch/067.json",
      "seconds": 978.5,
      "tokens": 55799,
      "state": "verified_error",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\25b86ebcdc32.json"
    },
    {
      "case": "OpenDeepSearch/002.json",
      "seconds": 92.094,
      "tokens": 12619,
      "state": "verified_clean",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\28649dd5e405.json"
    },
    {
      "case": "OpenDeepSearch/088.json",
      "seconds": 613.396,
      "tokens": 43268,
      "state": "verified_error",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\2ba6f44854e5.json"
    },
    {
      "case": "SmolAgents/019.json",
      "seconds": 2396.408,
      "tokens": 146730,
      "state": "verified_error",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\2f0d1f4ff7f1.json"
    },
    {
      "case": "OpenDeepSearch/077.json",
      "seconds": 145.602,
      "tokens": 13696,
      "state": "verified_clean",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\403836705db1.json"
    },
    {
      "case": "OpenManus/064.json",
      "seconds": 1593.322,
      "tokens": 74580,
      "state": "verified_error",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\49683a275045.json"
    },
    {
      "case": "Octotools/005.json",
      "seconds": 798.956,
      "tokens": 60471,
      "state": "verified_error",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\9ec0e72cf3c8.json"
    },
    {
      "case": "OpenDeepSearch/063.json",
      "seconds": 201.217,
      "tokens": 14760,
      "state": "verified_clean",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\b201f1303c69.json"
    },
    {
      "case": "Octotools/030.json",
      "seconds": 1433.546,
      "tokens": 93754,
      "state": "verified_error",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\bd198e0c90b3.json"
    },
    {
      "case": "BFCL/041.json",
      "seconds": 180.679,
      "tokens": 12273,
      "state": "verified_clean",
      "authority": "authoritative",
      "source": "docs/experiment-log.md",
      "original_source": "reports\\real_evolution_20260923\\contaminated_attempt\\checkpoints\\cycle001\\arms\\508acdf65658\\f5f761b6fbf5.json"
    },
    {
      "case": "SmolAgents/019.json",
      "seconds": 1480.4,
      "tokens": null,
      "state": "verified_error",
      "source": "docs/experiment-log.md",
      "stage_seconds": {
        "judge": 3,
        "step_tracer": 1,
        "defense": 3
      },
      "original_source": "reports\\error_scope_20260918\\diagnostics\\019_timing.json"
    },
    {
      "case": "Magentic_One/049.json",
      "seconds": 5628.0,
      "tokens": null,
      "state": null,
      "source": "docs/experiment-log.md",
      "note": "audit slowest, run 2; run 1 measured the same case at 36.0 min",
      "prose_only": true,
      "original_source": "reports/real_dev_20260923/run2_README.md"
    },
    {
      "case": "Magentic_One/049.json",
      "seconds": 2160.0,
      "tokens": null,
      "state": null,
      "source": "docs/experiment-log.md",
      "note": "audit slowest, run 1 -- the spread on one case is 2.6x",
      "prose_only": true,
      "original_source": "reports/real_dev_20260923/run2_README.md"
    },
    {
      "case": "Camel/018.json",
      "seconds": 6060.0,
      "tokens": null,
      "state": null,
      "source": "docs/experiment-log.md",
      "note": "patrol, run 1",
      "prose_only": true,
      "original_source": "reports/real_dev_20260923/run1_README.md"
    },
    {
      "case": "Magentic_One/050.json",
      "seconds": 6480.0,
      "tokens": null,
      "state": null,
      "source": "docs/experiment-log.md",
      "note": "patrol, run 1; both ran while 8 workers shared the batch",
      "prose_only": true,
      "original_source": "reports/real_dev_20260923/run1_README.md"
    }
  ],
  "batches": [
    {
      "name": "real_path_check",
      "cases": 6,
      "wall_minutes": null,
      "workers": 2,
      "source": "docs/experiment-log.md",
      "detail": "6 cases x 2 repetitions, workers=2; per-repetition wall clock not recorded, per-case rows are",
      "original_source": "reports\\real\\summary.json"
    },
    {
      "name": "audit",
      "source": "docs/experiment-log.md",
      "cases": 12,
      "wall_minutes": 118.37,
      "detail": "sentinel 6 + gain 6, workers=8, the same 12-case set whose per-case rows are archived under reports/real_evolution_20260923/contaminated_attempt/",
      "original_source": "reports/real_dev_20260923/readings_run2.json"
    },
    {
      "name": "patrol",
      "source": "docs/experiment-log.md",
      "cases": 8,
      "wall_minutes": 52.86,
      "detail": "patrol batch, workers=8, unresolved=0 -- the run in which the batch actually fit its budget",
      "original_source": "reports/real_dev_20260923/readings_run2.json"
    }
  ],
  "notes": [
    "the 2026-09-17 verification rows ran under study-specific settings (evidence arms, single-mechanism repeats); they are the same model and endpoint, so they witness the per-case cost scale, but a study that changed the route is not a measurement of the shipped route",
    "run 2 timing phases give audit/patrol wall clock; the case lists come from the same run's contract (sentinel+gain) and ledger (patrol)",
    "Magentic_One/049, Camel/018 and Magentic_One/050 exist only in prose; the README states them in minutes, so they are recorded as such and flagged instead of being silently mixed into the JSON readings"
  ]
}
```
<!-- cost-calibration:end -->

</details>
