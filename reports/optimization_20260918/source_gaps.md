# 来源核验与剩余覆盖缺口

机械检索输入只使用任务问题，不接收 true_answer 或标签理由；语料不采纳基准答案转载。人工检查曾打开包含标签的原始记录，因此本轮属于已暴露开发复测，不是盲测或独立泛化评估。来源发现不等于快照覆盖，快照 schema 合法也不等于来源能支持所问属性。冻结的原 mini 划分仍为 S=6、G=4、seed=20260824；原门限保持 2 TP + 2 TN。诊断划分只生成离线覆盖表，不作为原 mini 预检失败后的替代实验。

| 案例 | 核验来源与发现 | 未覆盖原因或覆盖结论 |
|---|---|---|
| BFCL/041 | [NIST 单位换算表](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b8)提供美制加仑到升的系数。车辆操作和发帖约束只能由任务与工具轨迹核验，外部网页不能证明车辆状态。 | 检索器把引号内 tweet 文本识别为实体，单位换算资料不能建立该实体属性；保留缺口，未编造车辆事实。 |
| Octotools/023 | [FC Bayern 官方统计](https://fcbayern.com/en/matches/profis/champions-league/2009-2010/fc-bayern-muenchen-inter-mailand-22-05-2010/statistics)与 [UEFA 原表](https://www.uefa.com/newsfiles/UCL/2010/2000488_TS.pdf)可核对犯规统计。官方日期为 5 月 22 日，与题目 5 月 23 日不同；直接下载均遇到 403，web 检索可读。 | 保存俱乐部短摘录及真实日期，没有把日期改成题目日期。提取实体为 Champions League，表格数字未形成可用属性值，未计覆盖。 |
| OpenDeepSearch/077 | [欧盟 PREDEMICS 结项报告](https://cordis.europa.eu/docs/results/278/278433/final1-predemics-final-report-compilation-130717-final.pdf)第 39 页记载 Colizza 获奖。原 PDF、页文本和渲染图均留存。 | ASCII 分词截断 Erdős，且 who 问题被分为 number 模式；有来源但提取不支持，未用别名或手写答案兜底。 |
| OpenDeepSearch/088 | [UT Austin 课程资料](https://research.cm.utexas.edu/nbauld/teach/ch610bnotes/ch18/ch18.html)说明烯醇溴化的一般机理。精确题目搜索主要返回基准题转载，已排除。 | 一般机理不是该特定分子的直接实验来源；当前检索不支持结构推导，未覆盖。 |
| SmolAgents/019 | [磷手性 NMR 实验研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC2084221/)涉及磷中心立体异构与 NMR 区分，但没有验证题述两种未指明醇的完整合成过程。精确题目搜索出现基准转载，已排除。 | 需要立体化学与反应过程推导，不能将相邻化学体系的峰数当作本题事实；未覆盖。 |
| OpenDeepSearch/080 | [UNESCO WWDR 2022](https://www.unesco.at/fileadmin/Redaktion/Publikationen/Publikations-Dokumente/WWDR_2022_EN_report_web_0.pdf)物理第 111 页、印刷第 93 页。 | 唯一新增 mini 快照：774；与同一句另一个统计口径的 225 区分。人工核对 PDF 页面与提取值，schema 通过。 |
| Octotools/005 | [NIST Cs-137 测量研究](https://www.nist.gov/publications/reference-material-evaluation-cs-137-radiochronometric-measurements)提供半衰期，[NNDC 衰变数据](https://www.nndc.bnl.gov/nudat3/getdecaydataset.jsp?dsid=137cs+bM+decay&nucleus=137BA)提供光子分支信息。 | 需要活动度、时间与几何接受度的计算；资料中的数字不能直接当作探测计数。检索器错误提取 After 为实体，未覆盖。 |
| Magentic_One/050 | [ClinicalTrials.gov NCT03411733 API](https://clinicaltrials.gov/api/v2/studies/NCT03411733)原 JSON 已留存，实际入组 90。Jan-May 2018 对应首次发布/更新，研究本身日期为 2017-03 至 2018-03。 | 检索器提取 Jan May 为实体，未匹配；保留日期歧义，没有编造与问题措辞完全相同的来源标题。 |

语料目录包含旧来源与四项新增记录。一般机制/常数资料仅列为缺口研究线索，不注入会让无关数字被当成答案的记录。PDF 全页文本采用固定空白合并与句界分段，原文与 SHA256 保留；没有从 benchmark 答案生成句子。

已知限制仍包括：ASCII 分词、最长大写串实体启发式、数字/曲目名提取范围、表格行列关联不足、词面匹配漏掉别名。本轮没有调整检索阈值或新增实体别名，也没有把这些限制掩盖成来源缺失。
