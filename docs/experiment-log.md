# 实验日志

更新：2026-10-20（Asia/Singapore）。本文维护已完成实验的样本、轮次、读数、失败、成本与可比性；[结果索引](results/README.md)连接机器原件，[技术文档](technical-solution.md)维护实现，[研究方向](research-positioning-and-roadmap.md)维护待验证方法。旧日志全文及本次整理前文档保存在[历史文档快照](../artifacts/repository-maintenance/20261020/documents-before.zip)。

## 实验总表

正确数仅计权威且判对；未决仍留在全请求分母。定位以全部标注阳性为分母，定位权限未通过者不计命中。不同数据、模型、源码与预算分别解释。

| 日期 / 实验 | 权威 / 请求 | 权威检测正确 | 全请求正确 | 首错命中 / 阳性 | 证据 |
| --- | ---: | ---: | ---: | ---: | --- |
| 09-27 G0历史测量 | 651/653 | 490/651 | 490/653 | 114/419 | [原报告](results/full693-20260927/baseline_report.json) |
| 10-13 原100例恢复基线 | 99/100 | 68/99 | 68/100 | 22/62 | [评分](results/localization100-20261015/baseline-evaluation.json) |
| 10-14 原100例首轮 | 93/100 | 72/93 | 72/100 | 23/62 | [评分](results/localization100-20261015/first-round-evaluation.json) |
| 10-15 原100例补测合并 | 99/100 | 76/99 | 76/100 | 27/62 | [评分](results/localization100-20261015/final-evaluation.json) |
| 10-16 下一步预测100例及恢复 | 100/100 | 76/100 | 76/100 | 27/62 | [报告](results/next-action100-20261015/report.md) |
| 10-16 机制第一轮，排除031后99例 | 99/99 | 73/99 | 73/99 | 25/61 | [归档](results/mechanism100-20261016/archive-manifest.json) |
| 10-17 机制优化复测99例 | 98/99 | 72/98 | 72/99 | 24/61 | [归档](results/mechanism100-optimization-rerun/archive-manifest.json) |
| 10-18 新100例机制第一轮 | 100/100 | 81/100 | 81/100 | 34/62 | [评分](results/next-action-validation100-20261018/round1-final-evaluation.json) |
| 10-18 新100例机制第二轮 | 100/100 | 82/100 | 82/100 | 36/62 | [评分](results/next-action-validation100-20261018/round2-final-evaluation.json) |
| 10-19 随机100例，两次恢复后 | 97/100 | 73/97 | 73/100 | 33/67 | [报告](results/random100-20261019/report.md) |
| 10-20 全693例，主轮及最终分类恢复 | 690/693 | 531/690 | 531/693 | 206/443 | [报告](results/full693-20261020/report.md) |

## 全693例：预检、主轮、恢复与终验

总体693例（443阳性、250阴性），695个原始文件；冻结顺序、输入、有效配置及源码后运行。基座为DeepSeek-V4.1-Flash-1017。预检核对2809项完整离线测试/19项门禁与源码身份匹配，另140项补充回归通过；真实smoke3为3/3权威并产生4次模型预测。离线通过、HTTP200和终态文件不能替代逐例commit、合同、quorum、权限及最终评分。

| 运行ID | 案例与规则 | 本轮新增权威恢复 |
| --- | --- | ---: |
| full693_main | 全693例，16案例/16请求并发 | 主轮682权威 |
| full693_recovery1 | 主轮全部11例非权威，一次统一恢复 | 5，合并687权威 |
| full693_failed2 | 固定Camel/039.json，失败类逻辑第二轮 | 1 |
| full693_degraded1 | 固定Camel/042、Camel/048、Octotools/015、OpenDeepSearch/000、SmolAgents/078；名称按用户指定，逻辑仍为第二轮 | 2 |

集合按运行状态冻结，选例及采纳不读金标、不按正确性挑选；只用新authoritative行替换先前非权威行，其他保留原选定来源。最终新增恢复Camel/039、Camel/048、OpenDeepSearch/000；仍未决Camel/042、Octotools/015、SmolAgents/078，均为Defense inconclusive / degraded_judge_only，失败类已清零。每类轮次已到上限，停止重试，automation693暂停，后台退出。BFCL162例行动预测因缺轮次事件跳过，属于结构限制，未列入补测。

最终权威覆盖690/693=99.57%；权威准确率531/690=76.96%，全请求531/693=76.62%；macro-F1 75.98%，首错206/443=46.50%，TP/FP/FN/TN=335/53/106/196。对最近random100的共同权威97例，检测6修正/2回退（73→77正确）；共同权威阳性64例首错4修正/3回退（33→34命中）。源码、提示、预算及并发与该历史轮不同，不能归因于单一模块。

监督期间曾因控制器末尾审计把有效Defense回退误作失败而退出；只修正监督审计后安全续接，旧attention和现场保留，未重跑已权威案例。最终按冻结生产裁决重放复核。第二轮分类恢复未修改detector、配置、提示、预算、输入或冻结身份。2,849个旧运行文件与710份逐例结果摘要通过复核；旧687权威阶段的汇总在原目录history中独立保留。

全部尝试含smoke、主轮、recovery1及最后两类补测：5,875 queued/started/finished，5,870响应、5传输错误；5条结束记录缺usage，0条开始后结束未知。已知usage下界70,352,749 tokens。最后分类恢复新增69请求、1,119,341 tokens、0缺usage；失败类530.4秒、降级类719.6秒，两类并发，不相加为总墙钟。主轮墙钟约3.66小时；逐例耗时之和不是墙钟。未将缺usage记作零，不估造账单。

证据集中在[最终报告](results/full693-20261020/report.md)、[评分](results/full693-20261020/final-evaluation.json)、[全部尝试成本](results/full693-20261020/all-attempt-costs.json)、[完成审计](results/full693-20261020/completion-audit.json)和[冻结身份](../artifacts/detector/live_checks/full693_20261020/freeze.json)。完整逐例原件、请求日志、阶段文件及控制器现场保留在`artifacts/detector/live_checks/full693_20261020/`；后续不启动其中历史监督/恢复入口来改写已完成轮次。

## 与旧653例及论文的比较

旧653个案例ID与二分类金标均匹配。当前取相同653例为650权威、500正确、首错195/419；共同权威648例检测487→499正确，56修正/44回退，净增12。FP34→49、FN127→100，召回改善伴随误报上升。排除OpenManus/014与BFCL/043两例步号映射变化后，414例共同权威阳性定位112→195，101修正/18回退。旧记录无独立定位权限字段，按旧合同解释，不能声称合同完全一致。

原论文AgentHallu Table 4/5已核对。当前macro-F1与定位数值高于所列主要baseline；旧GLM、当前DeepSeek及论文模型、预算、弃权与映射方式不同，不能据此主张同条件方法胜出。全693已开发暴露，不是独立盲测；未测论文解释质量G-EVAL。逐例清单、类别与家族差异见[完整对比](results/full693-20261020/historical-paper-comparison.md)及[派生数据](results/full693-20261020/historical-paper-comparison.json)。本项仅只读复算，新增模型调用0。

## 随机100例与机制验证

10月19日从693总体以seed=20261019抽100例（67阳性/33阴性），并发24，DeepSeek基座。修复json_mode提示词缺json触发HTTP400和新增工作额度后，以新run_id启动；旧中止批次保留并排除效果。正式主批次95权威，两次状态恢复后97权威；TP46/FP6/FN18/TN27，73/97=75.26%，首错33/67=49.25%；3例Defense inconclusive。796请求均HTTP200、缺usage0，已知8,836,609 tokens；修复前中止批次另921,052 tokens，不混入正式结果。主批次23.3分钟、补测8.7/7.3分钟；逐例之和5.84小时不是墙钟。

该轮作为测量可接受，未通过预注册100/100权威标准。19例可选输出额度不足以发第二次预测、19例BFCL缺轮次事件；前者后续已离线修复并在全693使用新的冻结预算，后者仍为结构性限制。历史summary和评分不补写，[随机100报告](results/random100-20261019/report.md)保留当时状态。

10月18日新100例单臂两轮以同案G0的82/100、18/62为基线：定位34/62→36/62，检测81→82，FP7→7（G0为6）；原冻结验收未通过。旧样本机制两轮的98共同权威检测均72正确、定位均24命中，结构覆盖改善未成为净收益。两组的源码、预算和样本不同，不能直接跨组相减。见[新样本报告](results/next-action-validation100-20261018/report.md)、[旧机制第一轮](results/mechanism100-20261016/report.md)与[优化复测](results/mechanism100-optimization-rerun/final-summary.md)。

10月16日下一步预测100例加4例状态恢复后100权威、76正确、27/62定位，相对最近原100例补测结果检测4修正/4回退、定位2修正/2回退，净收益0。43例采用模型预测，代表票8条非unknown不是语义正确率。采用路径797请求；计技术预试的全部started940，14条开始后无结束，60条结束缺usage；不得把采用路径费用当全部成本。见[报告与分层成本](results/next-action100-20261015/report.md)。

10月15日原100例补测合并99权威、76正确、27/62定位，相对10月13日基线68/100、22/62为检测12修正/4回退、定位8修正/3回退。SmolAgents/043未恢复；额外外层尝试及Defense容量变化保留。采用路径740请求，旧汇总825，全部对应磁盘journal去重866 started、859 finished，7条无结束；828响应、31传输错误。见[请求日志审计](results/localization100-20261015/request-journal-audit.json)。这些范围不同，不混为全部历史成本。

## 历史G0、自进化及其他里程碑

09月G0测量653例（另30开发、10验证），并非旧693全量。TP291/FP34/FN127/TN199；主权威准确率75.27%，定位27.21%。报告usage下界41,050,704 tokens，4,087次provider attempt中171条缺usage；含8轮重试的历史累计约53.5M，不能与采用结果费用混用。[历史报告](results/full693-20260927/baseline_report.json)保持原字节。

三轮自进化使用开发池18例巡检，不是每轮使用653例新证据：共7个开发版本、4正式候选，3不可评估、1收益声明不符，0准入；3个版本损伤正常邻居。M未运行，active为空，验证8/10，g1未启动；不能把周期结束写作效果达标。见[原实验与账本](results/full693-20260927/evolution_experiment.json)和[历史审计](archive/self-evolution-audit-20260928.md)。

| 范围 | 记录与边界 |
| --- | --- |
| 09-16—09-21试点 | 局部修复、邻居回退及中断，不并入G0分母 |
| 09-28复用划分 | 554开发/139本轮隔离验证，全部693此前已暴露；计划不是新运行结果 |
| 10-09—10-14重构与恢复 | 历史2138/59、2551/18等离线记录只归属当时源码；十例风险轮8/10返回、检测6/8、定位1/6；95交集检测71→65、定位18/57→20/57不代表总体无回退 |
| 10-16—10-18离线链路 | 2594/18、2623/18、2641/18、两轮2649/18分别对应原轮源码；历史2684为文字记录，不冒充本次机器复核 |
| 10-18排序探针 | 443阳性top-1 38.15%→37.25%、top-3 65.24%→64.79%；候选宽度3→6覆盖289→394，仅是上限，不是终判收益 |
| 10-19代码修复 | 授权约束绑定正文、同源最早听证修复，完整2748/19；后续全693前源码由2809/19及140补充回归覆盖，见各自报告 |
| 10-19 API容量 | 合成最高全通过128并发，日常建议64；256档有内容错误，未确认供应商硬限额；不作为检测准确率实验 |
| 10-20仓库整理 | 统一实验总表、结果索引、脚本分类、最新默认预算和台账；清理生成缓存、重复草稿及明确测试临时目录，组会及原始证据按摘要保留 |

证据：[排序探针](results/infrastructure-20261018/manifest.json)、[代码验收来源](results/repository-maintenance-20261019/README.md)、[API容量](results/api-capacity-20261019/report.md)、[本次维护核对](../artifacts/repository-maintenance/20261020/verification.json)。更早混合日志原文保存在`artifacts/repository-maintenance/20261015/historical-experiment-log.md`，整理前文档见本页顶部快照，不再作为当前接口合同。

## 后续实验记录与管理

每个实验使用独立study/run_id，事前冻结样本顺序、输入/源码/有效配置身份、预算、采纳与停止规则。重测按运行状态选择，不按正确性挑选；新版本和不同预算分轮保存，旧结果不覆盖。运行中区分排队、尾部耗时、语义未决、运行故障与效果变化；完成后登记权威覆盖、全请求分母、检测混淆矩阵、定位权限、同案修正/回退、全部尝试/usage缺失与墙钟。

一个实验在本日志只维护一个主记录，恢复轮次归到该主记录；结果索引负责路径，不重复整段结论。便于机器查找的[实验登记表](results/experiments.json)记录状态与证据位置；原始proof/journal/manifest保持各自原路径。代码实现更新技术文档，拟议研究更新研究方向；不把试验控制器、完整源码或逐请求JSON粘入日志。已经停止且轮次用尽的实验，不由旧监督脚本自动重启。

## 历史成本模型的冻结输入

下列命名块是`scripts/cost_model_693.py::harvest`的实际输入，保持原字节。它是历史校准样本，不能代替当前实验成本。

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

---
