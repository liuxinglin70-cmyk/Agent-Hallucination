# 检测器真实进化方案：从规则筛选转向可继承的能力改进

更新：2026-09-19；代码基线：`3333206`。本文是后续实施方案，尚未修改运行代码、配置、数据标签或准入阈值，也未启动新的真实模型实验。替代本文件 9 月 17 日版本中的过时现状与执行排序；历史实验结论仍以原报告为准。近期一手研究及检索范围见[RSI 资料表](../output/literature-search/rsi-detector/papers.md)。

> **执行记录（2026-09-19）**：P0/P1/P2 已按本方案完成并逐阶段提交。P1 冻结训练机制 `quoted_content_vs_delivered`；P2 两路线通过筛查与冻结配对复核，但候选未用开发验证未过（013 过度触发 + 边界案例轮间方差），部署保持不变。全部读数与产物见 [reports/rsi_pilot_20260919](../reports/rsi_pilot_20260919/README.md)；总消耗 77/96 次 case-evaluation、4 次真实 proposer 调用。

## 1. 决策建议

**保留严格部署准入，把主要投入转到“诊断—局部试验—能力实现—跨例验证—后代继承”。最小 improver 应提前，而不是等治理设施和全套终审数据都完备后才开始。** 当前已经有足够多的治理设施；缺少的是产生有效能力改动的工作过程。

下一阶段的交付目标不是“准入至少一个候选”，而是：找到一个输入上可识别的真实失败机制，自动生成可执行修复，在未参与该修复的开发案例上复现收益，并在下一代真正调用该能力。若失败，也要得到可区分的结论：缺信息、机制诊断错、实现无效、泛化不足或成本不合算。

三项优先行动：

1. 修复默认 HybridProposer 的跨轮状态/拒绝反馈转发，给学习闭环一个实际有效的输入接口。
2. 建立独立于部署门的机制训练区，允许预算内修订和保留中间分支；将研究搜索与部署认证拆开。
3. 首轮复用现有 `text_boundary@judge.system`，把完整证据、有限修订和 fresh 评估接通；证据显示现有表达面不足时，再增加一个受限的上下文接口。首轮不建设任意代码自修改系统。

**不建议继续在原 S=6/G=4 上重跑、换 seed、放宽 `m_min_marginal_cases`、调整相似度阈值，或增加 recall 条件来追求准入数。** 当前零准入不能单独证明筛选门过严，也不能证明所有候选无效。

## 2. 当前代码框架与实际可进化范围

### 2.1 检测链

```text
question/history
  → dataset.project_record：隔离原始标签
  → packet：事件、工具调用、交付锚点与多种文本视图
  → judge：结果性错误判断
      ├─ 阳性 → tracer + defense 听证
      └─ 阴性 → 按不确定性/干预/外部证据决定是否召回
                  → scanner：提出候选
                  → causal upgrade：核查错误及结果因果性
                  → tracer + defense 听证
  → adjudication：终态、authority、定位与 proof
```

外部证据通过 `detector.evidence` 的 snapshot 进入现有阶段，`detector.retrieval` 从已有语料构造 snapshot。进化后端的 `build_evidence_provider` 当前主要按问题检索，调用 `build_case_snapshot(corpus, question)`，没有把逐条交付 claim 的主动调查作为通用运行环节。

| 组件 | 主要代码位置 | 对机制分析的含义 |
|---|---|---|
| 输入与证据呈现 | `detector/dataset.py`、`packet.py`、`evidence.py` | 检测器可识别什么，取决于实际送入的证据，而不是标签理由知道什么 |
| 阶段流程 | `detector/api.py`，尤其 `_judge_negative_needs_recall` | 多票一致高置信阴性可以跳过 scanner；开启 scanner 只增加检查机会 |
| 判定语义 | `detector/semantics.py`、`docs/error-scope.md` | ES-1…ES-6 是任务定义，不能让候选为了得分自行改写 |
| 检索 | `detector/retrieval.py`、`evolution/backends.py:build_evidence_provider` | 已有可追溯语料检索，但没有通用的 claim→证据缺口→工具调查闭环 |
| 最终裁决 | `detector/adjudication.py`、`result.py` | 区分判定、authority、定位；“authoritative”是运行合同状态，不等于语义一定正确 |

### 2.2 进化链

```text
active registry 对应的 H_t
  → sentinel 基线保护覆盖预检
  → 缺陷注入审计
  → active patrol + bare-base 参照
  → worksheet 执行路径聚类、谓词画像、可用路由
  → gain 可修复方向预检
  → proposer 草拟干预 JSON
  → STRUCTURAL / HARMLESSNESS / BEHAVIOR / M
  → 联合候选与组合检查
  → registry 提交、ledger、checkpoint
  → H_t 清单、能力缺口和三类经验
```

当前主 patrol 已使用 active 版本，保留裸底座参照；旧方案中“仍只巡检裸底座”的判断已过时。`harness.py`、`gaps.py`、`memory.py`、覆盖响应和断点恢复也已实现，不应再次作为新工作的主要交付。

| 干预类型 | 现在能改变什么 | 仍缺什么 |
|---|---|---|
| `text_boundary` | 给五个既有 LLM 阶段追加指令及排除条件 | 不能增加任意新阶段，不能替代缺失的证据来源 |
| `evidence_check` | 内置谓词或新 typed DSL 产生证据行 | 新谓词仍读取固定类型字段，不等于新检索器、计算器或解析器 |
| `recall_trigger` | 在匹配阴性案例上开启既有召回网 | 不保证召回到正确问题，也不保证纠正原错误 |
| `decision_guard` | 四种封闭动作，包括暂缓、恢复及机械定罪等 | 主要改变裁决，不能普遍创造解决问题的方法 |

`evolution/capability_surface.py:capability_surface_report` 明确报告 `open_ended_self_evolution=False`，没有任意代码、执行图改写或权重更新能力。因此当前更准确的定位是**有界干预合成与部署治理**。记录了 H_t 五组件，不代表五组件已经都能被改进器修改和装载。

### 2.3 应保留的工程基础

保留标签投影隔离、可执行合同、配对回放、fresh 对照、registry 快照、账本、检查点、`not_evaluated` 与 `rejected` 区分。这些解决“变化是否真实执行、结果是否可追溯”的问题。

新增代码技能后，回放范围必须由输入依赖决定：修改了上下文、检索或新节点时，其下游不能继续重放旧输出。当前 `requires_fresh_evaluation` 只按既有 hook/动作判断，不能直接作为新技能的通用依赖分析器。

## 3. 为什么系统容易停留在过滤与阈值调整

以下区分已确认事实和待验证的因果解释。

### 3.1 直接阻断：目标集没有修复机会

9 月 18 日 attempt4 的[归档报告](../reports/error_scope_20260918/README.md)及 `run/reports/experiment.json` 一致记录：sentinel 保护覆盖为 2 TP + 2 TN，缺陷审计通过；patrol 有一个召回族，gain 的 4 例全部判对，`gain_opportunity={recall:0, precision:0}`，最终 `blocked_evaluation_preflight`，没有产生候选。

代码位置：`evolution/engine.py:_run_cycle` 第 1708–1721 行附近；`_measurable_families`、`gain_opportunity`。

这首先是**没有收益支持集**，不仅是通常意义上的“统计功效低”。G 中正负例都有，也不代表存在 FN/FP 可修。G 只有 4 例时，一个案例就占 25 个百分点，更不足以据此推断普遍收益。

现有预检适合阻止无意义的部署候选评估；问题在于它也成了研究探索的入口。训练侧本来可以研究 patrol 的真实失败，却因为另一组部署样本暂不可测而整体停止。

### 3.2 发现“同一路径失败”，尚未证明“同一原因失败”

`worksheet.py:_coarse_failure_family` 与 `_mechanism_signature` 主要按 judge/recall/upgrade/defense 状态分族；后续画像细分仍主要依赖现有谓词。`stable_failure_families` 的“stable”表示至少两条符合条件的案例归组，**该函数本身不验证同一案例多次运行的时间稳定性**。

同为 `judge_false_no_recall_candidates`，可能分别是：没开召回、召回能力弱、输入缺少正确事实、标签采用不同任务口径。聚为一族后要求同一触发器命中全部成员，会鼓励寻找公共词面特征，而不一定找到共同病因。

实际例子：9 月 17 日[单机制诊断](../reports/verification_20260917/single_mechanism/README.md)发现，BFCL/017 的可见任务与标签理由存在口径差异；OpenDeepSearch/086 的可见观察缺少标签理由依赖的事实。再次扫描同一记录不能自动补齐外部事实。

### 3.3 默认 hybrid 存在可复现的跨轮反馈缺口

`engine.py:1725–1728` 通过 `hasattr` 更新 proposer 的 `active_entries` 和 `set_rejection_feedback`。`HybridProposer`（`proposer.py:841` 起）持有 `self.llm`，但只转发 `set_memory_context`，未提供前两项接口。

本次无模型调用的实例检查结果：

```json
{
  "hybrid": {"active_entries": false, "set_rejection_feedback": false, "set_memory_context": true},
  "inner_llm": {"active_entries": true, "set_rejection_feedback": true}
}
```

含义：初始 registry 会在构造时传给内部 LLM，但同一进程后续轮次的更新与引擎保存的拒绝反馈会被静默跳过。记忆接口仍能工作，结构门仍能拒绝重复，因此不能说整个记忆或去重完全失效；问题是提案前的学习和节约搜索预算没有按设计接通。

另一个文档偏差：hybrid 实际只在 LLM 返回零个有效候选时调用模板，不会在下游四门拒绝候选后自动再试模板；模块开头“LLM 是唯一来源”的注释也已过时。应统一接口与描述，避免后续分析建立在错误流程上。

### 3.4 反馈信息偏薄，生成被压缩为一次 JSON 草拟

`LLMProposer.propose` 收到失败族摘要、路由、谓词画像、共同字面量、少量历史反馈。`typed_field_profile` 将标量截到 240 字符，列表最多 6 项、每项 160 字符。它有案例片段，不是完全看不到案例；但没有按需读取源码、完整相关事件、定位证据缺口和局部试做的工具过程。

对长轨迹机制，摘要足以猜测“可能缺召回”，通常不足以回答“哪个 claim 的哪个证据在何处丢了”。若核心信息不在可见字段内，反复优化 DSL 字面量只会增加选择规则。

### 3.5 把筛查信号变成定罪证据的风险

`structural_gate` 检查新谓词在失败成员上执行且一致输出声明 verdict；`adjudication.py:208–237` 接受合同有效的机械证据进行定罪。这保证接线和声明一致，**不自动证明谓词表达的是合法错误条件**。

一个“某公共词出现→violated”的谓词可能在选中的 FN 上全命中，也避开小型 sentinel。它仍可能只是失败样本的相关特征。这里是从代码结构推导出的风险，不是本次已经验证的攻击或已发生的错误准入。

应区分：弱信号可用于“值得调查”；只有具备独立语义依据的计算、状态和合同证明，才可用于机械定罪。不能让一个待学习规则自行声明 `conviction_verdicts`，就被解释为已获得普遍可信的因果证据。

### 3.6 即时终态收益与中间能力收益耦合过强

`behavior_gate` 除检查触发/文本注入/证据新增，还要求实际 TP 增加或 FP 消除；M 再检查 gain 集净收益。现有 joint groups 能测预先声明的协同组合，值得保留；但尚无可采样的完整研究分支档案，无法自然保存“这一代学会证据抽取、下一代再学会使用”的跨代中间能力。

新技能只改善证据完整性、还没改变终态时，不应部署，也不应被视为毫无研究价值。否则搜索倾向于最快翻转最终标签的 guard，而非较慢形成的证据处理能力。

### 3.7 标签命中不足以证明修好了原机制

已有真实诊断中，BFCL/017 某次由其他步骤的 hashtag 问题转为阳性，标签层面命中，但没有证明修好标注所指的文件比较问题。`authoritative`、`fired`、`marginal_cases>0` 都不能替代“错误对象一致、证据成立、因果链成立”。

必须并列记录 binary correctness 和 mechanism-aligned correction。定位标注本身有争议时单列，不能机械地把“步骤不同”全部判为无效，也不能全部记为机制成功。

## 4. 近期 RSI 系统给出的可迁移经验

这里的 RSI 指围绕固定模型的系统层自改进，包括任务 agent 与改进程序；不把所有提示优化都称为已实现递归能力增长。资料表包含 2025–2026 年工作，补查至 9 月 14 日发布的 Dream-RSI，以及 9 月 1 日更新的 AREX；较新的预印本只作为机制线索，不作为已复现结论。

| 来源 | 源文支持的经验 | 对本项目的设计推论 |
|---|---|---|
| [DGM](https://sakana.ai/dgm/) | 自修改代码、经验评估、多样候选档案 | 部署库之外保存可继续修改的完整父代，局部进展可作为后代起点 |
| [SICA](https://arxiv.org/abs/2504.15228) | agent 使用编程能力编辑自身实现 | 给 improver 读源码、局部运行、实现工具的能力，不能只要求输出干预 JSON |
| [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) | 可执行程序与自动评估形成搜索循环 | 对计算、解析、证据对齐建立便宜的客观局部反馈；不能假设开放世界幻觉都有确定 oracle |
| [GEPA](https://arxiv.org/abs/2507.19457) | 读取轨迹并反思，组合不同候选的互补经验 | 用逐例执行差异推动修订，保留不同机制上的优势；提供当前 hook 范围内的强基线 |
| [ShinkaEvolve](https://arxiv.org/abs/2509.19349) | 父代采样、新颖性筛查、模型选择提高搜索效率 | 先统计每种改动的真实产出率；新颖性只安排预算，不能作为正确性奖励 |
| [ACE](https://arxiv.org/abs/2510.04618) | 用增量生成、反思、整理维护上下文经验 | 保存可执行策略及失败边界，避免每轮整段重写；记忆被供给和记忆有效须分开验证 |
| [HyperAgents](https://arxiv.org/html/2603.19461v1) | 任务程序与元改进程序均可修改；单独测量改进能力 | 比较固定起点上旧/新 improver 产出更好后代的能力，不能用检测准确率替代 |
| [MCE](https://arxiv.org/html/2601.21557v2) | 上下文产物与产生它的技能共同演化 | 优先开放上下文构建技能，再研究诊断及修订策略的演化 |
| [Meta-Harness](https://arxiv.org/abs/2603.28052) | proposer 通过文件系统访问以往源码、分数和执行轨迹 | 为摘要提供按需展开入口，避免在信息被压缩后只能猜测修改方向 |
| [HarnessFix](https://arxiv.org/html/2606.06324v1) | 将轨迹和代码组织成带来源、控制流的表示，再做责任归因和限定修复 | 将路径族升级为有证据的修复说明，绑定具体阶段和最小改动范围 |
| [Phantom Guardrails](https://arxiv.org/html/2607.13083v1) | 在受控实验中，优化器可能为正常模式虚构待修错误 | 加入全正常、无可修错误的负控制，允许合理不改；提案必须引用可核查的错误事件 |
| [AREX](https://arxiv.org/abs/2607.21461v3) | 逐约束核查、针对未解 claim 再调查，保留已验证证据和未解约束 | 用证据缺口驱动定向召回；其模型训练效果不能直接归因于固定权重工作流 |
| [Dream-RSI](https://arxiv.org/html/2609.14858v1) | 用已实现的历史搜索树回放评价探索策略，再返回在线发现 | 分支积累后优化何时继续/停止；不能用回放估计树外新候选的真实表现 |

需要保留的区别：DGM/AlphaEvolve 的典型任务有较强可执行反馈；我们的任务存在信息不足、标签口径冲突和错误定位歧义。迁移的是搜索与继承机制，不是直接照搬其 fitness 或论文增益数值。HyperAgents 的主体实验也没有把所有外层机制全部开放修改，不能据其名称推导“让评估器自改就会更好”。

## 5. 收紧后的实施决定

上一版同时安排技能运行时、分支档案、开发课程和元改进，跨度过大。现在缩为 **三个必做工作包、一个有条件工作包**。先回答两个问题：项目是否有输入可识别、当前确实漏判的同机制案例？给改进器完整相关证据和真实反馈后，现有干预面能否修好它？

首轮交付是一个机制的可复现开发实验，允许结果为“没有合适靶标”或“当前路线无效”。不承诺准入，不把提示改进称作已实现开放式 RSI。

| 顺序 | 工作包 | 估计工程时间，不含模型运行 | 开始下一步的条件 |
|---|---|---|---|
| P0 | 修复 HybridProposer 状态与反馈转发 | 0.5–1 天 | 两轮及恢复路径离线测试通过 |
| P1 | 核查并基线测量指定案例，冻结一个训练机制 | 1–2 天 | 至少两条同机制、无口径争议的当前真实失败 |
| P2 | 在独立研究入口做现有 hook 的有限修订实验 | 2–3 天 | 训练机制修复且正常近邻不退化，才进入冻结验证 |
| P3（条件项） | 增加一个带来源的结构化上下文接口 | 另估 2–4 天 | 原始信息充分；上下文探针有效；P2 仍不能稳定利用该信息 |

P0 可先做；P1 没有通过，就不开发 P2/P3 的完整系统。元改进、任意代码生成执行、自动执行图改写、大型搜索树、主动联网检索全部后置。当前机器未找到 Docker CLI；独立 Python 进程只能提供超时/崩溃隔离，不能视为任意生成代码的安全沙箱。

## 6. 第一轮具体研究什么

### 6.1 当前三个巡检 FN 不能直接作为训练族

| 案例 | 本次核查结论 | 处理 |
|---|---|---|
| BFCL/017 | 既有诊断提示任务与标签口径分歧；另一个 hashtag 疑点命中阳性不证明修复原目标 | 争议案例，不作为正向修复目标 |
| BFCL/062 | step 4 启动返回门未锁、发动机停止；step 5 诚实说明未启动。标签因缺少前置调用判错，与 ES-3 存在分歧 | 从首轮召回靶标移出；保留原标签，作为边界审查 |
| OpenDeepSearch/035 | 既有诊断存在证据/口径争议 | 先调查可识别性，不合并生成定罪规则 |

BFCL/041 同属需要边界复核的历史案例。OpenDeepSearch/063、086 在 attempt4 已判为阳性，不能沿用旧失败身份计作新增召回收益。BFCL/107 的“完整交付”要求与原负标签也有争议；SmolAgents/019 存在事实核查成本，历史单例耗时约 1480 秒；Camel/048 的 degraded/unresolved 不应直接当作语义 FN。

这意味着当前需要一次**有限的机会筛查**，而不是立即对现有三个 FN 合成规则。

### 6.2 首批 12 例清单：先诊断，尚非已确认的训练集

原始文件均在 `datasets/raw/agenthallu/AgentHallu/BFCL/`。完整清单与文件/投影 hash 见[试点清单草案](../output/rsi-planning/pilot-cases.draft.json)。这些案例全部按开发暴露处理，尚未运行新的模型基线。

| 用途 | 案例 | 已能从 question/history 核查的关系 | 尚须核查 |
|---|---|---|---|
| 来源内容→实际交付 | 003 | sort 的 step 4 内容与 step 5/6 报告及发送顺序不同 | 用户是否要求原样传递排序结果；当前是否漏判 |
| 来源内容→实际交付 | 024 | 用户要求文件完整原样作为 tweet body；step 3 文件内容被 step 5 添加包装文字、标签等 | 精确交付约束是否成立；当前是否漏判 |
| 任务常量→实际动作 | 042 | 用户明定四胎 32–35；step 7 有两胎 30，但汇总布尔为 true，step 8 发布 healthy | 按任务常量优先规则复核；不要以布尔 true 直接覆盖用户区间 |
| 任务常量→实际动作 | 054 | 用户要总量 40；step 3 加入 40 后观察总量 50，step 4 确认 50 | 只用可见的目标与结果差异；不从隐藏初始油量推导正确加油参数 |
| 正常近邻候选 | 013、020 | 文件复制/排序，包含失败后恢复、结果呈现 | 013 有行格式解释问题，须复核，不能仅因 gold=false 当作干净负例 |
| 正常近邻候选 | 018、053 | 发动机前置调用最初缺失，补齐后成功；053 是“加 15 升”而非“总量 15 升” | 018 无用户自定胎压区间，不能照搬 042 的 32–35 条件 |
| 争议边界 | 017、028、041、062 | 028 的标签所指多余内容已出现在 diff 观察中；其余见历史/本次诊断 | 不计自动修复成功；028 另核对转义与实际字节差异 |

003/024 优先作为“来源内容与交付绑定”候选族；042/054 作为“任务约束与结果绑定”备选族。**不能仅因都属于 Incorrect Tool Arguments 就认定同机制。** 两条样本必须可由同一个明确操作修复，否则继续拆分。

暂不选 BFCL/006（verbatim 与 summary 混合要求，标签理由与实际发送正文有差异）、025（精度表示争议）、049/059（标签依赖未必可见的初始状态/参数）。不把 benchmark 的隐藏工具预条件变成检测器“常识”。

### 6.3 P1 的执行顺序与停止条件

1. 人工逐例填写：任务约束原文、来源事件、实际交付/动作、是否后来修复、ES 条款、两种竞争解释。保留原 gold，另写 `scope_review`；有分歧的案例不参与自动修复奖励。
2. 对上述 12 例各运行一次完整 H0 基线，保留所有阶段输出。H0 为启动时冻结的当前 registry；模型与配置固定。BFCL 试点统一不启用外部证据，因此不能与历史有证据的混合实验直接比较。
3. 只把“可识别且 H0 实际判错”的案例作为训练失败。当前已经判对的 003/024/042/054 转为回归样本，不能宣称获得新能力。
4. 若不足两条同机制失败：仅扩大一次，预先按 case ID 顺序筛查剩余 BFCL Incorrect Tool Arguments，选最多 12 条具备可见来源/任务约束/最终动作的案例；先冻结清单和原因再运行基线。排除当前清单、争议例和原 validation 案例。不得按候选效果换样本。
5. 最多基线测量 24 条后仍不足两条，就结束本轮：输出 `no_verified_repair_family` 及证据缺口清单。下一项任务是取得合适案例或澄清口径，不是调准入阈值。

现有 `detector/interventions.py` 要求 `provenance.failure_cases >= 2`，引擎结构门还有失败族约束。研究模式不会关闭这些合同，也不能用一条失败复制成两个 ID，或将判对案例填进 failure_cases。手工构造的边界变体只用于廉价检查，不能充当独立失败案例或泛化样本。

## 7. P0：精确修哪一个工程问题

直接在 `evolution/proposer.py:HybridProposer` 补两个转发：

- `active_entries` property 的 getter/setter 转发到 `self.llm.active_entries`，兼容引擎已有赋值方式。
- `set_rejection_feedback(feedback)` 转发到 `self.llm.set_rejection_feedback(feedback)`；已有 `set_memory_context` 保持。

不引入统一 context 框架，不重写 `engine._run_cycle`。同步说明 hybrid 仅在 LLM 无候选时 fallback，不在四门拒绝后 fallback。

`LLMProposer.set_rejection_feedback` 中单候选 rejected_keys 排除了 `not_evaluable`，联合 rejected_group_keys 当前没有同样过滤。P0 增补测试核查并统一此语义：未可测不是失败结论；仍保留反馈给诊断，但不能仅凭它永久封死重试。此处是静态代码发现，尚未运行新增回归测试。

离线验收落点 `evolution/tests/test_hybrid_proposer.py`：第二轮收到新增 active entry；单候选/联合候选的真实拒绝能转发并避免原样重复；not_evaluable 不进入失败指纹禁用集合；恢复后重新供给相同上下文；memory 转发不回退。使用 mock provider，零模型费用。

## 8. P2：最小研究入口，不重写部署引擎

### 8.1 文件与接口

以下新增名称是建议设计，当前尚未实现。

| 文件 | 最小改动 | 明确复用 |
|---|---|---|
| `evolution/research.py`（新增） | 读取冻结 manifest；执行 baseline → diagnose → draft → evaluate → revise → freeze；最多一个机制 | `LiveDetectorBackend.evaluate_fresh`、registry 校验、现有结果结构 |
| `evolution/proposer.py` | 给 LLMProposer 增加可选 research packet 供给；未提供时保持现有行为 | provider JSON 调用、候选解析、反馈与 memory |
| `evolution/research_artifacts.py`（新增，仅确有必要才拆文件） | 写入不可变版本、hash、父代与每例结果 | 现有 hash/序列化工具，不建数据库或树搜索器 |
| `evolution/tests/test_research.py`（新增） | 用 mock 验证预算、恢复、隔离、反馈、产物装载 | 现有 mock detector/backend |

独立入口只写 `reports/rsi_pilot/<run_id>/`。现有 `run_cycle`、S/G、四门、真实 registry 和标签不改。局部研究评分用于选择是否继续修订；最终晋升仍走原引擎与固定门，G 不可测时明确停在研究结果。

调用合同建议为 `run_research(manifest_path, output_dir, backend, proposer)`，先提供 Python API 与离线测试；验证可用后再加 CLI。不在文档中给尚不存在的命令，避免执行者误以为已有入口。

### 8.2 改进器拿到什么

提供当前 hook、相关函数源码、训练例的 **project_record 后完整 question/history**、packet 的 event/interaction 引用、逐阶段原始结果、人工核查过的目标错误说明、正常近邻、前一候选 diff 与逐例反馈。长输入按事件引用展开，不能继续只靠被截断的字段画像。

训练监督与 detector 输入严格分开：gold 与 scope_review 可在研究监督层使用；每次实际检测仍只通过 `project_record`，候选不得包含 case ID、隐藏 true_answer、标签理由中的专属答案或按样本写死的常量。展示同一原始证据的步骤可以学习，训练样本答案不能成为分类程序。

每轮改进器仅有三种输出：`no_change(reason)`、`draft(existing_intervention)`、`revise(parent_hash, existing_intervention, diagnosis)`。首轮不提供任意 shell、写源码或联网工具。

一个候选版本最多一个 `text_boundary@judge.system`，描述可迁移的核查操作。例如来源绑定机制：先定位任务要求原样交付的源字段，再查最终实际发送参数，检查是否后来更正；明确允许非精确要求下的摘要、引用包装等正常行为。最终具体补丁由实际失败证据生成，不能预先宣称此模板有效。

第一轮不允许新增 recall_trigger 或 decision_guard。目的是识别现有 judge 在获得具体核查步骤后是否能解决问题；不要同时改变路由和裁决，导致收益无法归因。

### 8.3 有限修订与版本继承

首轮只有两条路线：B 为修好反馈的现有 proposer，C 为完整轨迹诊断＋同一种 hook。二者均从 H0 出发，最多一个初稿、一次修订，故合计最多四个候选版本。不能把六次随机重抽改名为六个机制。另用 mock 正常轨迹核查 no_change 分支；真实正常近邻上的无根据改动必须在筛查中失败。

B/C 使用同样的训练例和正常近邻、每条路线最多三次 proposer 调用（含可选诊断），记录实际 input/output token 与 detector 调用数；次数相同不代表 token 完全相同。先跑一次配对筛查，再对各路线冻结选出的至多一个版本做完整重复。未选中的候选与失败结果全部保留。

第一版研究目录只需：

```text
manifest.json                 # 数据、配置、代码、H0、协议 hash
v001/{candidate.json,parent.json,diagnosis.json}
v001/results/<case>.json       # 实际执行 registry hash、阶段结果、耗时、异常
v002/...                      # 明确以哪个版本修订
selection.json                # 包含所有尝试，不只写胜者
report.md
```

修订应基于实际 parent 内容与反馈，不从摘要重新生成一个无关候选。恢复只能重用输入、代码、registry、模型包络全部一致的结果；改 prompt/代码后必须新版本、新评估身份。现有 CycleJournal 的缓存身份不能被误当作任意源码修改后的有效身份。运行故障可以保留为 partial，但不得删除后无限重跑。

### 8.4 评估及预算，先跑小实验

设训练为两条核实失败＋两条核实正常近邻；更多已筛查样本先记录用途，不能根据候选得分调换。当前 12 例全属于诊断开发暴露；从其余案例预先选出的保留样本也只可称“本轮候选未用开发样本”，不称独立测试。

| 阶段 | 上限 | 通过/停止 |
|---|---|---|
| 机会筛查 | 12 次 case-evaluation；仅一次扩展到累计 24 次 | 无两条同机制真实失败即停止 |
| B/C 草拟及修订筛查 | 4 个版本 × 4 例 = 16 次；共享阶段 1 的 H0 首次结果 | 两条训练失败须修复原目标，两个正常近邻不新增错误/未决，才进入复核 |
| 冻结配对复核 | 至多 B/C 两版本及 H0，各 4 例 × 2 次 = 24 次 | 固定重复次数；报告每次结果，不能选最好一轮 |
| 候选未用开发验证 | 胜者与 H0 各最多 8 例 × 2 次 = 32 次 | 预先审核并冻结案例；不足合适样本则只报告训练可修性 |

全部执行时上限为 **96 次 case-evaluation，外加最多 6 次 proposer 调用**；case-evaluation 内含多票与后续阶段，不等于一次模型 API 请求。未通过前一阶段就不花后一阶段预算。前 12 例设置 4 小时墙钟上限、最多 2 workers；本轮所有阶段累计 12 小时墙钟硬上限，先到预算即停止并保留 partial，不能声称会在该时间内跑完 96 次。费用按实际 token 单独报告；启动时还应写入模型报价及货币预算，如项目已有账户限额取更小值。

保留 `detector/configs/autodl.json` 当前模型、temperature、投票与 timeout 包络；不通过降票或换模型挤进预算。研究 manifest 固定 provider/model/config hash，但不写密钥。本试点统一走 fresh 完整检测：`judge.system` 本已要求 fresh；即使 cache 可用也不复用旧 judge。当前 `evaluate_fresh` 走 direct detector，BFCL 试点明确无外部证据；以后检索实验必须另行核对 evidence provider 传递，不能假定该入口自动继承证据快照。

首轮数值条件是**继续花研究预算的条件**，不是替换部署阈值。两条训练 FN 全修复，也只证明极小样本的训练可修性。总体准确率提升、跨来源泛化和 RSI 改进效率均须后续更大、独立或明确标注暴露的协议支持。

## 9. 何时才做 P3：新增什么能力

只有以下三项同时成立才开启：原始 packet 已含必要事实；由人工从原记录整理的带来源对齐表能修复同一错误；B/C 均难以复现该效果。人工表只是可修性探针，不能计作自动进化成果。探针若需要外部答案，则本路线不适用。

首个新增接口建议 `context_view@judge.context`：演化的是受限的数据处理配方，解释器由工程实现；不是任意生成代码。首版只允许解析 observation JSON、保留标量字段及 JSON path、按事件顺序列出调用参数/观察/最终发送参数，附任务原文和已有 event/interaction 引用。禁止生成 verdict、authority、gold 或修改原 packet。

JSON 解析失败须标记并保留原文；多调用使用已有 interaction 配对，不能跨事件误绑定；嵌套字段有来源 path；派生表不能冒充外部证据。先不做自动语义抽取、工具图推理、单位换算或模型自主选择真实世界事实。若该有限表示不能解决已定位问题，报告接口不足，不再追加特殊 case 分支。

| 代码位置 | 接入要点 |
|---|---|
| `detector/context_view.py`（新增） | 配方校验与确定性渲染；带来源，不直接判定 |
| `detector/interventions.py` | 新类型/新 hook 合同与编译；保留 provenance 要求 |
| `detector/judge.py` | 在原 full view 前附派生索引，保留原视图；记录实际使用的配方 hash |
| `detector/api.py`、`evolution/gates.py`、`evolution/backends.py` | 新 hook 必须 fresh；拒绝错误复用 frozen judge；BEHAVIOR 记录渲染与消费，而非只看配置存在 |
| `evolution/capability_surface.py` 与对应 tests | 如实报告有界能力扩展；测试多工具、非法 JSON、引文引用、标签隔离及缓存失效 |

新增 hook 前先运行离线合同测试；再次走第 8 节相同配对流程，预算另立，不能悄悄追加到首轮。对照至少保留 H0、最好的现有 hook 版本、同预算原始上下文版本；移除派生索引后重跑验证收益归因。如果只是增加 token 带来增益，要如实报告。

## 10. 怎样判定真实改进，以及如何进入部署

每个修复必须同时给出四份证据：

1. **目标一致**：修复的是审查确认的错误对象；不是用另一个疑点碰巧把标签翻成 true。
2. **运行生效**：保存实际 registry/prompt/配方 hash，指明消费位置；文件写入不算执行。
3. **对照成立**：H0 与候选输入、证据、模型包络一致，全部重复计入；新增 FP、FN、unresolved 都列出。
4. **边界清楚**：正常近邻不被“见 tool error 就定罪”“见非原样文本就定罪”误伤；派生测试不冒充独立样本。

输出表至少包含 case_id、用途、raw_gold、scope_review、H0/候选状态、目标 claim、source refs、实际修复对象、是否机制一致、耗时/token、失败原因。原 gold 的 binary 指标和经口径审查的机制指标分表呈现，不能把重标收益算成算法收益。所有失败与未决留在分母。

部署前冻结候选及来源；在候选未用过、协议已冻结的开发晋升批次上重新走现有 STRUCTURAL/HARMLESSNESS/BEHAVIOR/M 和组合检查，保护集维持原要求。没有足够的同机制可测 G，就输出 `promotion_unmeasurable`，部署保持不变。`set_replacement_gain_cases` 只是接口，不授权从训练 FN 临时挑 G 来追求通过。

当前 693 例与原 validation 的开发暴露不能通过重划分消除。新开发协议须明确与旧报告不可直接比较；正式泛化需要额外独立数据。首轮无需等独立测试数据才做训练诊断，但也不能提前形成对外性能结论。

两代继承放在首次局部收益之后：第二代从真实候选 hash 装载父代，面对不同训练案例，比较保留父代与去掉父代的结果。只有提示 diff 继承时，称提示策略继承；实际代码/配方被装载和消费后，才能称对应能力继承。元 improver 的演化须进一步固定起点与预算比较后代产出，当前不安排。

## 11. RSI 经验在这轮实际落成什么

| 经验来源（详见第 4 节一手来源） | 本轮采用 | 暂不采用及原因 |
|---|---|---|
| GEPA / Meta-Harness / HarnessFix | 完整相关轨迹、具体责任阶段、基于差异的一次修订 | 不先开发通用源码工具 agent，现有 JSON provider 足够做首轮受限实验 |
| DGM / ACE | 每个候选有真实父代、实际内容和失败边界；有效改动可继续修订 | 不建多岛屿、大档案采样器，当前连一个可信失败族尚未确认 |
| AlphaEvolve / MCE | 条件满足才加确定性上下文配方及廉价局部检查 | 检测任务缺少普遍确定 oracle，不能直接移植单一 reward |
| Phantom Guardrails | 正常近邻、允许 no_change、禁止无依据 guard 搭便车 | 不把“严格净增益”单独当充分证据 |
| HyperAgents / Dream-RSI | 保留后续比较改进器效率所需的版本与成本 | 尚无可靠搜索历史；元演化和回放策略延后 |

这轮只验证“完整证据＋有限修订”是否优于当前一次 JSON 草拟，以及是否值得开放一个新的能力接口。研究改动可以发生在现有 G 没有机会时；部署标准保持独立。这样得到的是可执行的因果判断，而不是新增一组筛选参数。

## 12. 本轮产物与核查范围

本文件是继续优化后的唯一主方案；[试点清单草案](../output/rsi-planning/pilot-cases.draft.json)保存 12 例的用途、来源 hash、可识别性备注与 `baseline_status=not_run`。草案不是 `evolution.development_split.v1` 配置，不能直接传入现有生产命令。

本轮新增逐例核查 BFCL 原始任务/工具结果，修正了 062 的首轮定位；收紧改动范围、明确 96 次 case-evaluation 上限与阶段停止条件。上一轮 27 项离线测试通过属于上一轮核查；本轮只修改文档及清单，未改运行代码、标签、配置、阈值，未调用模型运行新实验。

**后续执行顺序：P0 → P1；P1 证实有可修机制才做 P2；P2 证明表示能力不足才做 P3。任何阶段证据不足，交付明确诊断并停止该分支。**
