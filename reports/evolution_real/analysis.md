# 真实进化收益复核与优化方案

复核日期：2026-09-16。范围：原开发 30 例、第一轮候选和第二轮已有账本；未读取 10 例 validation 标签。原实验、registry、候选、门限、数据划分均未修改。本报告区分原始观测、确定性复核、独立真实诊断和待验证假设。

## 核心判断

原第一轮完成了真实提案、部署评估、拒绝和记账，但没有候选准入。其原因不是单一的候选质量差：收益集没有覆盖候选试图修复的错误方向；失败族把执行路径和表面特征相同当作修复机制相同；部分特征又来自锚点提取不足；模型全阶段重跑削弱了改善的归因；反馈没有保留“行为成功但测量集无修复机会”的差别。

“零准入”成立；“所有候选完全没有改善”不成立。“两轮完整实验已经失败”不成立，因为第二轮没有完成。原候选的局部改善也不能直接升级为稳定泛化收益。

## 1. 重新计算的结果

证据：`recheck.json`，来源为原 `artifacts/evolution_real/cycles/cycle001.json` 和 30 例空库缓存（运行目录已回收，归档副本见 [../verification_20260917/evolution_run/cycles/](../verification_20260917/evolution_run/cycles/)）。`recheck.py` 可重复执行确定性检查；不调用模型、不改变原账本。

### 1.1 Patrol 混入了运行失败

| 指标 | 复核值 |
|---|---:|
| 案例数 | 20 |
| 权威、可判定终态 | 17 |
| TP / TN | 9 / 3 |
| FN / FP | 4 / 1 |
| unresolved（按权威输出口径） | 3 |
| 全案例正确率 | 12/20 = 60% |
| 可判定案例正确率 | 12/17 ≈ 70.59% |

`worksheet_report` 将 `prediction != gold` 的全部记录按 gold 分成 fn/fp，因此把未决也计入了“5 FN、3 FP”。这不是标准混淆矩阵。两例（Camel/018、Magentic_One/049）是 judge 超时，另一例（Magentic_One/050）虽 state=verified_error，但 authority=degraded_judge_only。其顶层摘要写defense=not_run，深入hearing记录才看到：第一场defense已完成并refuted，第二场defense因case_deadline_exceeded失败。因此三例未决均包含实际超时问题，且顶层摘要掩盖了多听证的具体失败位置。不能把三例都解释为可由语义干预修复的检测错误。

### 1.2 三个候选的实际情况

| 候选 | 原 S+G 评估 | 原门限结果 | 确定性回放补充 |
|---|---|---|---|
| IV-1-001 | fired_rate=0.2，但 recall_triggered_cases=0 | BEHAVIOR 拒绝 | 在两例原失败族上均能真正触发；S 和 G 均不触发 |
| IV-1-002 | 召回触发1例；BFCL/041 从 FN→TP；正确数6→7，无新增错误 | BEHAVIOR通过，G上M=0，拒绝 | 原30例中实际触发7例：patrol 6、S 1、G 0 |
| IV-1-003 | OpenDeepSearch/077 从 FP→TN；正确数6→7，无新增错误 | 声明recall而实际无TP增加，BEHAVIOR拒绝 | 固定上游后，在原两例失败族和BFCL/041上均无注入、无召回、无输出变化 |

`fired_rate` 统计 deployment 事件，其中包含 `recall_trigger_not_matched`，不能解读为有效动作率。IV-1-001 的0.2说明有两例留下了事件，而非两例强制召回。

原 S+G 的两个“+1”属于不同候选、不同运行，不能相加成组合收益。原候选 fresh 臂没有保存完整阶段结果，因此不能离线证明 BFCL/041 的那次翻转恰好由新增召回造成。复核脚本用缓存基线重算终态差分，与原结果一致；这不等于恢复了原 fresh 控制臂的全部中间状态。

### 1.3 Gain 集没有目标错误

| G案例 | gold | 基线 | 可改善方向 |
|---|---|---|---|
| Camel/048 | clean | positive | precision：修误报 |
| Magentic_One/093 | error | positive | 已正确 |
| Octotools/005 | error | positive | 已正确 |
| OpenDeepSearch/002 | clean | negative | 已正确 |

G包含2 TP、1 TN、1 FP、0 FN。三个候选却全部从FN族产生、声明recall。纯粹修复漏报在这次G上没有收益空间；只有顺带修复Camel/048才可能达到M≥1。

IV-1-002在S上的改进可以通过行为门，但M只计算G；S中的该错误又不属于无害门保护的基线正确案例。因此，“在S纠正错误”和“满足G准入”是不同事件。规则执行符合现有代码，协议覆盖却不足以判断本轮召回能力是否有效。

另外，无害门实际只保护6例S中基线正确的3例（2TP+1TN）。通过无害门只支持在这3例未发现退化，不能描述成广泛安全性已经成立。

## 2. 根因：从表面模式到修复机制之间存在断层

### 2.1 相同执行路径不等于相同错误原因

两例被分为一个族，是因为 judge-negative、recall-not-needed，以及六个机械谓词的结果相同。`stable_failure_families` 中的 stable 指至少两例共现，不是多次模型运行中稳定失败。本轮没有对族成员进行独立重复稳定性确认。

然而已有gold解释给出了不同错误来源：

- BFCL/017：将应比较文件的步骤变为覆盖文件。judge把任务解释成“使两个文件相同”，认为后续写入已修复中间失败。
- OpenDeepSearch/086：gold指向检索摘要遗漏更符合实体上下文的116 tons；输入日志只给出76 tons，最终回答也为76 tons。
- BFCL/041（原候选改善例）：gold指向启动前缺少踩刹车；judge认为已经诚实报告启动失败，因此不构成错误。

这至少涉及操作意图/前置条件、检索证据完整性、失败是否构成任务违约等不同问题。共同出现None不构成共同修复因果。

账本中两轮patrol案例集合和worksheet完全相同；代码启用缓存并遍历全部20例patrol池。因此第二轮的相同诊断不能当作一次独立复现。缓存还持久化了provider失败结果；后续设计应在基线冻结前按预定预算解决瞬时故障，或将其标为运行不可评估，而非让相同超时记录持续进入语义进化的错误总数。

gold解释用于本次离线诊断，没有进入候选模型输入。它也不是无条件正确的外部真理：必须检查标签语义是否与检测任务一致，以及理由所需信息是否在允许输入中。不能为提高gold一致率把案例答案或隐含规则硬编码进干预。

### 2.2 锚点表示缺失污染了失败族

`packet.delivered_result` 通过工具名中的final_answer/answer/finish等词识别答案工具。OpenDeepSearch/086的工具名是Code Action Space，实际参数却是`final_answer("76 tons")`。当前表示没有识别这个嵌套调用，导致`last_answer_tool_observation`为空。

此外，`delivered_anchor_consistency` 将judge的交付描述与末条自然语言/答案工具字符串作有限的字面比较。它不证明真实语义矛盾。三例焦点样本都产生inconsistent，其中包括包装工具漏识别，以及长任务总结与末条回复的表达差异。

于是诊断把“未识别到锚点”升级为“交付未被支持”，proposer再把这一特征写成召回条件。IV-1-002在28例有judge结果的案例中，谓词判violated共16例；运行时受judge-negative等条件限制，最终强制触发7例。该信号明显比目标族宽，包含基线正确的clean案例Magentic_One/085。这里只能证明会增加复查，不能证明会制造误报；后者需要完整下游结果。

这7例中3例基线原本已运行recall，真正新打开召回路径的是4例（BFCL/017、BFCL/041、OpenDeepSearch/086、Magentic_One/085）。应分别统计规则触发数和相对基线新增的执行路径。

### 2.3 检测语义与标签依据没有完全对齐

底座语义将工具观测视为环境事实，排除已修复中间错误、无虚假完成的失败，并强调最终交付。同时又允许用世界知识检查检索源的公共事实，存在需要明确的判断边界。

对OpenDeepSearch/086，完整question/history中没有116这个值；对BFCL/017，没有明确的diff指令；对BFCL/041，没有brake/pressBrakePedal规则。这是输入检查，不能据此单独断言标签错误或语义无法推导。但它说明：重新召回相同轨迹不能自动补充缺失的工具契约、来源全文或外部事实。

应把此类错误分为：输入内可证实违约、契约/任务语义争议、需要外部核验。后两类不得自动冒充已有机械证据充分的“actionable族”。

### 2.4 下游干预缺少上游可达性检查

IV-1-003只改upgrade.system。目标族原始状态为recall未启动，因而upgrade不可能收到候选。冻结原judge及投票后，阻断所有模型调用进行真实控制流回放，三个焦点案例全部保持clean，deployment事件为空。

这给出了确定性证据：单独部署此候选无法修复原目标路径。需要一个上游触发器作为显式依赖，或直接拒绝不可达候选；不能依靠全阶段重跑让judge偶然改票后才“碰到”下游。

### 2.5 全阶段重跑与简化反馈阻碍机制学习

`requires_fresh_evaluation` 将recall.trigger、upgrade.system等均标为fresh，实际调用DirectDetectorBackend重跑全部阶段。已有LiveDetectorBackend支持保留judge，但本轮gate绕过了这条配对路径。

原对照仅重掷一次且终态一致，不能排除候选臂随机性，也不能证明中间证据一致。proposer输出的`frozen_judge_replay_compatible=true`仅由hook是否为judge.system计算，不能当作实际使用冻结回放的证据。

拒绝反馈只传失败门的摘要：IV-1-002反馈M=0，丢掉了行为门的+1、触发1例及收益所在分区；IV-1-001没有区分目标族能触发但评估集无覆盖。随后相同机制会被去重阻止。其风险是把“没有足够观测机会”学习成“机制无效”，促使下一轮换一种语法而非修正原因。第二轮候选正文未持久化，不能断言它已经发生这种退化。

### 2.6 冻结回放把“固定上游”误当成“固定整条控制流”

真实诊断触发了`ValueError: frozen stage replay is missing a hearing freeze entry`。`detector/api.py`用`frozen is not None`判断整条flow冻结，并假定基线已执行所有可能的hearing。对于judge-negative基线，新的recall干预恰恰需要创建基线没有的hearing，因而这个假设与新增召回能力相冲突。

这项缺陷影响低噪声的阶段回放路线；原第一轮使用fresh=true，不能据此声称原第一轮也遇到了同一异常。它解释了为什么“已有冻结judge支持”仍不能直接拿来完成有效召回干预的全链路评估。优化须按节点的输入依赖决定复用，新出现的hearing必须真实运行，不能伪造历史hearing或静默丢弃。

## 3. 独立真实诊断

协议在执行前保存于`recheck_live/protocol.json`。固定IV-1-002，不重新提案；使用BFCL/017、OpenDeepSearch/086、BFCL/041三例，每例3次；固定原judge和其投票，仅让下游真实运行。三个空库控制臂已通过冻结回放还原clean终态，并用异常拦截防止意外judge/recall调用。

这是条件于原judge结果的机制诊断，不是原两轮实验续跑，不替代原准入、不提供干净泛化估计。完整独立端到端重复和clean负对照仍是后续需要。每次结果独立落盘，已完成结果可复用。

实时结果见`recheck_live/summary.json`。冻结接口抛出缺失hearing错误的尝试保留为技术失败。为完成这些尝试的机制诊断，脚本另设`--complete-hearings`：仅补全部此类技术失败，在run_judge边界回放相同的真实历史judge结果及vote，让下游原始生产函数正常调用模型；不传frozen_stages，避免接口把新hearing视为非法。补充协议和结果另存`hearing_completion/`，不覆盖原尝试，也不把技术补测当作额外独立样本。

### 3.1 已完成结果

首次9次真实候选尝试：5次完成且均判clean，4次因上述冻结接口错误终止。对全部4次技术失败执行一次独立补测，结果为3次clean、1次error。它们不是13个独立统计样本；技术补测重新调用下游，不能恢复被异常打断时的原模型输出，因此下表只描述完成的诊断路径，不估计无偏修复概率。

| 案例 | 首次尝试 | 技术补测 | 完成路径中的正确判定 | 具体阻塞位置 |
|---|---|---|---:|---|
| BFCL/017 | 2 clean、1接口异常 | 1 clean | 0/3 | 每条完成路径找到3–4个召回候选，但upgrade认为None不是失败、两文件已写入相同内容或偏差不影响交付，全部未升级 |
| OpenDeepSearch/086 | 3 clean | 无 | 0/3 | 每次召回2–3个候选，但upgrade将日志中的76 tons作为环境事实；缺少gold指向的来源差异证据 |
| BFCL/041 | 3接口异常 | 2 clean、1 error | 1/3 | 一次scanner返回空；一次只提出轮胎候选且被否决；一次提出发动机及轮胎候选，经defense确认发动机问题 |

三例空库控制均保持authoritative clean。以上所有9条完成路径均真正触发recall；因此原族未修复不能归结为触发器不执行。BFCL/041表明在固定同一judge及vote时局部翻转可以发生，不能再把所有改善都归为judge重掷；但三次补测只一次成功，尚不支持稳定能力收益。这里没有新增clean对照的真实下游复测，不能外推安全性。

### 3.2 唯一正确翻转的机制证据仍不充分

BFCL/041的正向补测最终responsible_step=3，与gold步号一致；但tracer说的是调用startEngine前应锁门，defense确认第4步未锁门重试导致发动机未启动。gold则描述第3步未踩刹车。不能把“标签对且步号对”自动视为“解释命中了gold机制”。当前输入确实支持门锁错误；这并不证明补测理由无效，而是表明它与gold解释不是同一个机制。

同一次补测还发现：第二条hearing的候选是轮胎条件，tracer却仍定位到发动机启动。源码中recall-positive的delivered_result/correct_result为空，run_step_tracer又没有收到对应candidate的claim/quote。这是候选与定位证据绑定不足的具体迹象。该轮胎候选随后被defense refuted，未决定最终定罪，但它说明定位验收不能只比较最终步号。

### 3.3 校验与边界

- 原账本12条哈希链valid=true；原轮次JSON、账本、active registry哈希未变。
- detector源码和配置与真实诊断启动时的哈希一致；未修改生产代码或原准入门限。
- 与门限、backend和多听证有关的46项现有测试全部通过；它们未覆盖本次发现的新增hearing冻结错误。
- 10例validation标签未读取；本次只做开发案例机制诊断，不能作为独立泛化或统计显著性证据。
- 完整派生结果：`recheck_live/analysis.json`；原尝试、技术失败、补测结果和逐阶段输出均保留。

## 4. 优化方案与验收标准

### P0：先修测量、留痕和可恢复性

1. 将报表分成TP/FP/FN/TN/U、authority、阶段失败和全案例正确率。fn/fp只统计可判定错误，U单列并计入全案例分母。验收：原patrol可复算为9TP/3TN/4FN/1FP/3U。
2. 将deployment事件拆成eligible、predicate_evaluated、predicate_matched、action_executed、terminal_changed。验收：IV-1-001显示“执行检查但未在S+G触发动作”，不能只报fired_rate。
3. 运行前保存完整候选JSON、配置/源码/数据hash；每个case/arm/repetition原子落盘完整proof和模型usage；门结果随完成随写。ledger保存引用和hash，增加时间戳、阶段和退出状态。
4. 用run_id+cycle+candidate_hash+case+arm+rep作为幂等键；resume校验hash后只补缺项，不重提案，不覆盖旧账本。统一正面区分started/completed/interrupted；中断原因未知就记录unknown。验收：在单例完成后中断，恢复时已完成例零重复调用，旧12条账本hash保持有效。
5. 修复节点级回放：保留上游judge不应禁止新增hearing；复用必须检查该hearing的输入hash，目标节点没有冻结结果时真实执行。补上“negative→新增recall hearing”和“只解冻tracer/defense”的真实控制流回归测试，避免仅检查函数参数。
6. 区分语义结果缓存与瞬时运行失败缓存。在基线冻结前采用预登记、两臂一致的失败重试规则，保留每次attempt；冻结后不得只重试不利结果。增加hearing级失败汇总，使第二场defense超时不会被顶层简写成not_run。

### P1：修复生成方向与收益证据之间的连接

1. 保留原30+10实验作为历史证据，下一轮另立冻结协议。固定裸底座后，先按错误方向和来源检查gain可评估性；没有FN机会时将recall标为not_evaluable，不向生成器泄漏gain案例、标签或内容。
2. 若总量仍只有30例，改用预先登记的交叉拟合：每个候选只在未参与其发现的折上评分，S仍保护独立的基线正确样本；不能看过候选表现再挑折。它是开发估计，并非独立终审。若资源允许，优先扩大开发池，使不同来源中均有可测错误机会，再冻结划分。
   若为机制检验富集基线错误，必须将该组明确标为挑战集，同时保留代表性样本评估总体效果；不能把富集后的高修复率当作自然分布准确率。当前G每改善1例就相当于25个百分点，粒度本身也不足以衡量温和增益。
3. 分别报告family内修复、未参与发现的同机制迁移、总体净收益。保留M≥1和零新增错误作为开发准入条件；区分“不生效”“样本不覆盖”“不稳定”“有害”。不得把S上已看到的改进事后搬到G并宣称原协议成功。
4. 反馈传递分区级聚合：可评估机会数、动作率、各门差分、运行健康和重复一致性；不传gain具体内容。对coverage不足的机制不永久禁用；对经充分机会验证的无效/有害机制才做去重抑制。

验收：构造或选择预先冻结的开发比较后，修复真实FN可在预定收益分区被计量；无FN的recall轮次明确not_evaluable；不能通过放松门限制造准入。

### P2：修复表示与机制诊断

1. 为Code Action Space等工具增加保守、可追溯的嵌套调用规范化，保留原文和event_id；可用安全语法解析识别调用，绝不执行代码。未知包装器输出unknown，而非断言不存在答案。
2. 将“锚点未识别”“没有来源”“字面不匹配”“语义矛盾”分开。无语义证据时不得产生可定罪的inconsistent；内容归一化必须保留数值、单位和任务约束。
3. 失败族增加输入可验证的契约类型、最早失败阶段、缺失证据类型与候选可达性。相同谓词结果只作为检索线索；至少对族成员检查一个共同修复假设。将同批次共现与跨重复稳定性分开命名。
4. 对工具前置条件使用真实工具schema/环境规范；对检索遗漏使用允许的来源核验能力。若输入不支持目标label语义，明确受限任务或评估兼容子集，同时保留全数据结果，不能静默剔除难例。
5. 先在发现集验证候选确实触发且改变错误路径，再进入未暴露的收益评估。family内修复是机制筛查，不是泛化证据。
6. 将candidate_id、claim、locus和证据refs传入对应tracer，并要求tracer与defense引用同一偏差；保存二分类正确、步号正确、理由相符三个独立指标。验收：轮胎hearing不能用发动机偏差作为自己的定位证据。

验收：OpenDeepSearch/086的嵌套最终答案能被正确表示；None不等价于工具失败；单独的upgrade候选在不可达路径上被便宜地拒绝；不得硬编码76/116或案例ID修标签。

### P3：建立可归因且成本可控的实验

1. 机制诊断冻结所有不受干预影响的上游，干预节点及其依赖后继重新运行；输入/节点hash不匹配时不得复用下游。recall触发器保持同一个judge及vote，upgrade干预保持同一召回候选集。
   当前三个候选均不修改judge。若阶段回放正确实现，仅三个10例候选臂就可理论上避免3×10×3=90个初始judge票请求（不含重试/加票）。这是调度上的可省调用量，不是已经测得的token或时间节省。
2. 独立端到端确认时两个臂都完整重跑，预先固定重复次数、并交错顺序，报告每例翻转分布及阶段健康。不要将冻结机制诊断和端到端总效应混成一个指标。
3. 最小对照：裸底座、固定通用召回（资源预算匹配）、进化触发器、必要时显式触发器+升级组合。比较准确性、U、定位、调用/token和延迟，回答收益来自选择性机制还是单纯多调用。
4. 三次重复仅作排错和决定是否继续，不作显著性/泛化证明。只有机制筛查、负对照和未参与发现的开发收益均通过，才进入冻结验证集及更大独立数据。

停止规则：若触发3/3但scanner持续找不到相关偏差，转向证据/语义而非继续加触发；若找到偏差但upgrade持续拒绝，检查因果/任务定义；若只有终态对、定位/理由不对，不能认定修复机制成立；若服务失败占主要部分，先解决运行可靠性。

## 5. 建议执行顺序

本次三例固定judge诊断及逐阶段复核已完成。下一步实现P0记录/回放/统计和P2表示/可达性/证据绑定，再冻结新的开发评估协议（P1），最后做预算匹配对照（P3）。不建议立即原样重跑两轮：旧G没有漏报机会、旧失败族混合成因，重跑会继续消耗预算而难以回答真正的问题。

当前尚未实现上述生产代码优化。已执行的是离线复核与独立诊断脚本；原实验结论仍为第一轮零准入、第二轮未完成。

## 证据入口

- 原始轮次：[../verification_20260917/evolution_run/cycles/cycle001.json](../verification_20260917/evolution_run/cycles/cycle001.json)
- 原始账本：[../verification_20260917/evolution_run/ledger.jsonl](../verification_20260917/evolution_run/ledger.jsonl)
- 确定性复核：`reports/evolution_real/recheck.py`、`recheck.json`
- 独立诊断：`reports/evolution_real/recheck_live/protocol.json`及逐例结果
- 计分/门限：`evolution/metrics.py`、`evolution/gates.py`、`evolution/worksheet.py`
- 调度/反馈：`evolution/engine.py`、`evolution/proposer.py`、`evolution/backends.py`
- 表示/语义：`detector/packet.py`、`detector/predicates.py`、`detector/semantics.py`
