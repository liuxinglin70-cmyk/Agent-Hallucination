# RSI 系统机制资料与本项目迁移依据

检索日期：2026-09-19。用途：支撑[检测器进化执行方案](../../../docs/optimization-directions.md)，不作论文新颖性裁决。主要覆盖 2025–2026，重点补查 2026 年 6–9 月。只以论文作者原文、arXiv 和官方项目页面支持技术结论；未复现实验。表中“已读”限定本次核验深度，摘要级来源不用于推断未检查的算法细节。

## 选定来源

| 工作 / 时间 | 来源状态与已读范围 | 类型 | 可迁移机制与边界 |
|---|---|---|---|
| [SICA: A Self-Improving Coding Agent](https://arxiv.org/abs/2504.15228)，2025-04 | arXiv 摘要；另发现 ICLR 2025 SSI-FM workshop 版本，未当作主会结论 | system/tool | 自编辑实现；支持提供真实编程工具，不支持保证任意检测任务均可改善 |
| [DGM: Darwin Gödel Machine](https://sakana.ai/dgm/)，2025-05-30 | 官方技术介绍；[论文入口](https://arxiv.org/abs/2505.22954) | system/tool | 多样父代、代码修改、经验评估；借鉴档案继承，不复制其编程基准得分 |
| [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)，2025-05-14 | 官方方法说明；另核查 [2026-05-07 应用更新](https://deepmind.google/blog/alphaevolve-impact/) | system/tool | 程序数据库与自动评价；适用于本项目可执行局部子问题，开放事实判断不等于程序测试 |
| [GEPA](https://arxiv.org/abs/2507.19457)，2025-07 | arXiv 摘要及[作者仓库](https://github.com/gepa-ai/gepa)；论文 HTML 访问失败，未用其细粒度伪代码 | pure method | 轨迹反思、提示演化与互补候选；可作为现有 hook 范围内的强基线 |
| [ShinkaEvolve](https://arxiv.org/abs/2509.19349v1)，2025-09-17 | arXiv v1 摘要及[官方方法介绍](https://sakana.ai/shinka-evolve/) | system/tool | 父代采样、代码新颖性筛查、bandit 模型选择；新颖性用于搜索效率，不证明正确 |
| [ACE](https://arxiv.org/abs/2510.04618)，2025-10 | arXiv 摘要，未核定主会接收状态 | pure method | 增量维护策略上下文，减少整段改写的信息流失；程序性记忆仍需真实使用与效果验证 |
| [MCE](https://arxiv.org/html/2601.21557v2)，2026-01，v2 2026-02-11 | arXiv v2 摘要与方法概述 | pure method | 产生上下文的技能与上下文产物共同演化；为上下文工具开放修改面提供依据 |
| [HyperAgents](https://arxiv.org/html/2603.19461v1)，2026-03-19 | arXiv HTML v1，§3–5、D.3/D.4 | system/tool | 任务/元程序共同修改；improvement@k 分离改进能力。主体实验的外层父代选择仍固定，不宜宣称完全无人工约束 |
| [Meta-Harness](https://arxiv.org/abs/2603.28052v1)，2026-03-30 | arXiv v1 摘要 | system/tool | 改进器通过文件系统读取历史源码、分数与轨迹；最贴近“超越压缩后的 JSON 提案”的工程路线 |
| [HarnessFix](https://arxiv.org/html/2606.06324v1)，2026-06-04 | arXiv v1，摘要、§III-C/D 修复与验证 | system/tool | 带来源与控制流的轨迹表示、责任层归因、限定补丁说明；使用固定修复算子，不代表自动产生任意能力 |
| [Phantom Guardrails](https://arxiv.org/html/2607.13083v1)，2026-07-13 | arXiv v1，摘要、§4.5、§5 | method + benchmark | 正常模式可诱发虚构失败；受控实验中无根据 guard 可混入有收益组合。加入无错误负控制和组件依据核验；不能外推其发生率到本项目 |
| [AREX](https://arxiv.org/abs/2607.21461v3)，首发 2026-07-23，v3 2026-09-01 | arXiv v3 摘要与版本历史 | system/tool | 逐约束审计、对未解 claim 定向研究、压缩中保留验证状态；同时采用模型训练，固定权重迁移只能检验工作流部分 |
| [Dream-RSI](https://arxiv.org/html/2609.14858v1)，2026-09-14 | arXiv v1，§2/3、实验设置与回放接口 | system/tool | 历史搜索树作为已实现空间的回放器，用于探索策略优化；模型、评价器与执行接口固定，树外结果必须在线测量 |

## 本项目最相关的三个工作群

**带证据的局部修复：Meta-Harness、HarnessFix、Phantom Guardrails。** 已覆盖丰富历史输入、定位责任层、限制补丁范围和审查虚构失败。我们的未验证问题是：在幻觉检测中，标签不同、事实缺失、实际机制故障能否被可靠区分。直接落点是 cause hypothesis + 原 claim + 证据 + 正常近邻，而不是再增加一种相似度过滤。

**可继承能力：DGM、SICA、HyperAgents、MCE。** 已提供源码/元策略修改与档案继承的先例。我们的迁移难点是把证据解析、调查、上下文工具接入原检测链，并证明两代之间确实执行祖先实现。单纯保存 H_t 清单、记忆文字或更高最终分数不覆盖这个问题。

**低成本搜索与证据状态：GEPA、ShinkaEvolve、ACE、AREX、Dream-RSI。** 可借鉴轨迹反思、预算分配和未解问题驱动后续行动。建议先做可运行技能和反馈链，数据积累后再用历史树优化搜索策略；不把历史回放变成新候选的替代评分器。

以上差异与落点是针对本地代码的推论，不是原论文为本项目做出的结论。没有建立“领域无人做过”的新颖性主张，也不使用跨论文不同比例的收益数字为本项目设定目标。

## 检索与筛选记录

检索使用公开方法名和通用词，没有发送项目代码、私有案例或内部报告文本。主要查询包括：`Darwin Godel Machine self improving agents 2025 2026 archive evaluation`、`AlphaEvolve 2025 2026 evolutionary coding agent`、`recursive self improvement agents 2026 GEPA`、`site.arxiv.org Hyperagents`、`site.arxiv.org self improving coding agent SICA`、`site.arxiv.org self-improving 2026 Aug agent`、`site.arxiv.org recursive self-improvement 2026 Sep`、`site.arxiv.org AREX Deep Research`。

额外筛查了 2026 年 7 月 self-improvement survey、9 月 RSI 路线文章、MetaRSI、Continual Harness、Hierarchical Self-Improvement 和 Darwinian Evolver 等线索；主方案优先保留直接匹配诊断、能力实现、继承与评估问题的一手来源。路线/综述不作为有效性证据；二手介绍、聚合站、社交帖子只用于发现原文，没有作为技术依据。未纳入来源政策排除的出版方。

局限：不是系统综述，未穷尽最新工作；13 项来源核验深度不一，已在表中说明。没有复算论文数值或审计作者代码；尤其 9 月新预印本仅用于设计启发。GEPA HTML 失败由作者仓库与摘要补充，未把未读全文写成已验证算法实现。

## 后续实验需要的文献对照

先做现有 hybrid、GEPA 风格轨迹反思、限定代码技能三者的等预算比较，再比较继承开/关。若要主张元层 RSI，再按 HyperAgents 的思想冻结新旧改进器、统一初始检测器和任务，按开发分数选后代后测保留集。Dream-RSI 回放策略放在有足够历史树后，另以真实在线搜索验证。
