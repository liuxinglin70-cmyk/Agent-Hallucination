# 检测定位优化：证据、实现与待验收项

## 当前结论

本轮以提交 `a2fa56c` 为代码检查点。原始数据、标签、历史运行和恢复报告保持原字节。当前代码完成模块回归420 passed，最终冻结全量离线验收2449 passed、0 failed/error/skipped，18/18检查通过且源码未变。按用户最新指令取消95例真实回测，本轮收尾不报告准确率恢复或定位增益。

恢复报告的100条首错定位为22/62（35.48%）；95条交集中的权威检测准确率为65/94（69.15%），全请求正确率为65/95（68.42%），首错定位为20/57（35.09%）。历史与恢复版共同检出的真阳性30例中，恢复版命中19例（63.33%）；这两个检测分母及两个定位集合不能混用。

## 九个退化样本的证据诊断

证据入口为 `artifacts/detector/live_checks/live100_20261013_recovered/evaluation.json` 的 rows 与 provenance；下述原因来自关联 result.json 的 proof、stage_status，以及原始轨迹。样本名仅用于审计，运行时规则不读取它们。

| 案例 | 观察到的裁决路径 | 根因与通用处理方向 |
|---|---|---|
| BFCL/115 | Judge 阴性 high，Recall 未运行。请求说消息应 “be like” 模板；实际消息省略 “in my account” | 标签执行严格字符串，Judge 按语义等价判断。不能为标签将全部类似模板改为逐字义务；应区分明确固定工具字段、逐字要求与近似模板。置信度聚合修复可恢复弱票复审，但不保证改变此案裁决。 |
| BFCL/030 | Judge 将删除前的 ls -a 当作删除后目录请求已完成，high 阴性跳过 Recall | 后续交付义务被早先观察替代。共同语义规则需按用户请求的时序和当前状态核验；动作成功不能证明后续查询已交付。 |
| BFCL/056 | 启动失败要求锁门，助手报告失败并请求许可；Judge 把诚实报告当作全部履约 | 需分别判断失败真实性、可见恢复能力、原始授权及核心任务是否完成。不能凭错误文本臆造工具能力，也不能把诚实报告一概定罪。 |
| SmolAgents/024 | Judge 分票后 Recall 发现姓名问题，Defense refuted；并非 Recall 漏触发 | Defense 混用“没有快照不能证实”与“同一人姓名变体”两种理由。是否为不同身份须独立证实，不能把所有拼写差异自动当语义等价，也不能从金标注入姓名。 |
| BFCL/144 | Judge 阴性 high，Recall 未运行；指定 ticket description 后追加解释 | 固定字段值与包含指定文本的边界。请求还要求完整说明，须从任务用语判断赋值是否闭合；不得依标签将所有附加说明定罪。 |
| Magentic_One/065 | Judge 多数阴性，Recall/Defense 因未显示完整页面内容而定罪 | 已显示 viewport 只覆盖页面部分，却被用于否定全文分析结果；缺少支持不等于已有反证。分析工具的输出不能自行证明真实，但可见片段也不能证明其余页面不存在。 |
| OpenDeepSearch/071 | Judge/Defense 将一致观察的月份改为模型记忆中的月份，无外部快照 | 世界知识事实判断错误风险。不能用该案例标签指定月份；证据不足的事实修正应明确不确定性，不能把模型记忆描述成检索证据。 |
| BFCL/055 | 工具返回无单位的 780.0，助手按 km→mile 转换；Judge/Defense 认定 780 必须直接传参 | 源单位、目标单位与工具契约缺失。不能从字段名或数值差异单独证明换算违法；单位未知时应保留未知，不凭最终工具可接受性证明单位正确。 |
| Magentic_One/031 | 恢复后仍无权威结论；canonical 结果无 Judge/阶段结果 | 长尾推理与预算失败。必须核验各次请求日志，限制单请求输出及整例预算；无结果不能写阴性，也不能以缩短输入直接宣称问题解决。 |

额外已复现的共同根因：相同的 high/medium/high 阴性票组，由首张代表票决定 Recall 开关。修复后使用所有阴性支持票中的最低置信度；保留原始票、多数裁决及 quorum。新增测试修复前 3 failed/3 passed，修复后连同已有 API/投票回归 43 passed。

## 十三个 later 的审计分组

| 案例 | 金标→当前定位 | 当前证据提示 |
|---|---:|---|
| Magentic_One/061 | 1→43 | 当前 hearing 指控最终编造修订数，标签指控最初任务解释；需核对是否同一偏差，不能把无关 hearing 强行前移。 |
| BFCL/155 | 6→9 | 标签指控保险参数，当前 hearing 指控发票星期描述；属于不同偏差选择。 |
| SmolAgents/022 | 1→4 | 早先计划遗失任务固定比例，后续采用外部规则；需沿任务约束消费追溯。 |
| SmolAgents/066 | 3→6 | 错实体检索及访问失败先于最终回答；需要实体身份与查询链核验。 |
| BFCL/071 | 4→5 | 标签计失败的启动调用，Tracer 计后续未恢复的交付遗漏；需区分失败尝试与责任动作。 |
| SmolAgents/047 | 2→6 | 早期对数展开丢失定义域，Tracer 只检出后期计数；需检查变换假设传播。 |
| OpenDeepSearch/090 | 1→4 | 早期形式体系假设先于推导；不能只追踪最后数值。 |
| OpenDeepSearch/007 | 2→4 | 检索实体/解释问题与后期选项承诺的首源关系待核验。 |
| OpenManus/031 | 8→9 | 标签指控人类提示错误，hearing 指控空成功交付；同案不同偏差。 |
| Camel/024 | 1→6 | 初始任务拆解与后期密度推导的关系，同时存在模型物理事实争议。 |
| Magentic_One/090 | 1→3 | 早期猜测变为后续确定性前提，需区分启发猜测与实际采用。 |
| BFCL/130 | 7→10 | 错币种报错先于错误预算动作；现 hearing 只关注后者，应验证其因果责任。 |
| Camel/012 | 18→27 | 中间 note 包装了检索结果，后续摘要再消费；存在长通道截断，需保留注记与消费引用。 |

以上计数说明“13 条全部是依赖断链”并未成立。不同偏差、规划假设、动作失败口径和中间包装需分别测试。

## 单步动作边界接口

`detector.next_action.predict_action_boundary(packet, event_id)` 只依据任务与截至 t 的前缀，识别明确工具失败。返回不可变 `ActionBoundary`，绑定 prefix_digest、来源事件、失败工具与证据引用。合法边界包括重试/修复、可用 fallback、诚实汇报；禁止没有观察到恢复就声称成功。不生成下一步内容或外部 Observation。

`compare_next_action` 只比较真实 t+1 事件，返回 compliant/divergent/unknown。不同工具的调用仅为 possible_fallback_unverified；缺少下一步仍为 unknown。重试观察有明确成功标记才支持成功声明。所有输出为 hypothesis，decision_authority 和 localization_authority 均为 none。

失败信号进入静态 risk profile，Top K 候选携带单步比较进入 Tracer 的已绑定 risk context。Judge、Defense 与投票不消费动作审计。Tracer 仍必须核验同一 hearing 的真实偏差、引用、修复与物质性，不从风险排序直接定罪或回填步骤。

## 自进化归档接口

`evolution.causal_failures.build_action_failure_triplets(record, policy=...)` 重建 provider-safe packet 和静态候选，输出密封的 `evolution.action_failure_triplet.v1`：

1. predecessor_risk：前缀风险与合法动作边界；
2. next_action：真实 t+1 的状态、偏离模式与引用；
3. terminal_delivery：实际交付的有界摘录、完整摘要与 unverified 偏差状态。

gold 答案不进入三元组。归档不能将启发式单步偏离冒充已验证的终端错误或已确认的最早责任。`build_causal_failure_record` 自动附加可构建的 action_triplets；草案 prompt 仅获得抽象 action_rule_conditions，不携带交付答案、案例身份、步号或工具字面量。

## 复合规则与正常邻居保护

`shared_action_rule_conditions` 对至少两个成员的风险与动作偏离模式求交集，向LLM起草器提供compound_action_conditions。Hybrid的定位fallback以“observed_tool_failure AND success_claim_without_observed_recovery”起草tracer.system指令；禁止将动作偏离写成Judge/Defense投票或定罪谓词。模板明确排除恢复、合法重试、诚实汇报、未知fallback、无下一步、不同义务及未核验的终端实质性。

`diagnose_failure_families` 只在已提供的开发测量行中收集健康且正确检出的正常轨迹，读取provider-safe记录；不扩展到holdout。相同风险且下一步compliant的记录组成正常邻居库，起草prompt不携带这些记录或案例身份。Engine将匹配邻居加入两臂回放列表。L0必须至少有两个匹配且即将回放的正常邻居；缺policy、记录、健康、匹配动作或回放覆盖均拒绝通过。L1沿既有完整覆盖、行为变化、目标收益与零邻居退化规则裁决；新增完整开发用例证明目标定位改善不能抵消正常邻居误报，失败版本不能冻结。

## 长轨迹与跨副本接口

`render_judge_view` 对短轨迹保留完整视图，长输入转为不超过48000字符的逐通道稀疏投影，保留事件坐标、工具引用、头尾及失败/结果片段。省略明确标记为excerpt gap；完整索引自身无法放入预算时记录omitted_event_ids。超过4000字符的任务进入同一安全投影，避免默默遗漏后续请求。degraded视图不允许依三张high阴性直接跳过Recall。投影不修改packet，也不声称未显示内容不存在。

`trajectory_budget.bound_request_route` 为所有基础请求及重试设置统一输出上限：默认16384 tokens；system+user达到48000字符时降至8192；保留更小的已有max_tokens。配置项max_output_tokens_per_request、long_input_threshold_characters、long_output_tokens_per_request可显式设置为正整数，null和非正值拒绝。新增审计请求仍沿原added-budget规则预留默认1500输出，不能被基础请求的新默认上限放大。provider记录实际request_budget，runner记录request_budget_policy；累计deadline、取消、quorum及truncated_output弃权保持有效。

全步静态动作扫描覆盖所有事件，投影容量以外的明确失败仍可进入前K候选；不存在真实PPL输出。`code_lineage` 不执行代码，只从记录中解析直线def-use、变量别名和包装消费，将实际源码引用作为Tracer假设。覆写、删除、未知分支、语法或容量不足不保留旧别名的确定性链。结构字段的显式source_binding允许重命名，只有匹配的前序来源、数值和单位/时间绑定才能连接；缺声明、错误实体/单位、未来引用或值变化拒绝连接。

## 离线验收与实验取消记录

模块回归日志为`artifacts/code_quality/deep_optimization_20261013/module_regressions.log`（420 passed）；源码审计85文件、重复定义/边界违规/导入环均为0。九类通用边界测试不含案例ID；语义提示词合同测试只证明共享边界和输入形状，不证明真实模型裁决正确。

初次阶段全套测试在运行中发生源码修改，触发输入身份保护，已停止并保留iteration1.log及iteration1_interruption.json，不能算验收。最终使用`scripts/run_deep_optimization.py prepare/execute`冻结95例、源码、配置、原数据摘要及30例保护队列。编排先执行verify --scope all；只有0失败/错误/跳过、18检查通过且源码不变才启动新95例真实批次。失败阶段保存原证据，下一尝试通过--attempt建立独立目录，不能覆盖旧清单。所有案例统一使用新源码与预算，不根据gold选择重试或替换结果。

2026-10-14复核第一次冻结验收：2445 passed、1 failed、0 error/skipped，17/18检查通过且源码不变，95例真实运行未启动。唯一失败为审计生产者把整例剩余输出quota写入request.max_tokens，零quota触发配置校验而非资源耗尽。现已移除该错误赋值，由请求route拥有上限、AddedBudget拥有整例剩余额度；新增三项route/小额剩余/default的所有权用例，80项相关回归通过。旧失败目录及live95协议保持原始字节，下一协议使用live95_attempt2。

2026-10-14用户要求完成修复后收尾、不运行实验，取消95例真实回测。自动编排入口已停止，第二次冻结全量离线验证独立完成。本轮只验收代码、测试与接口；检测准确率、首错命中率及真实耗时改善均未重新测量。历史指标保留，不作为新源码收益。

最终验收：`D:\python\python.exe scripts/verify.py --scope all`，2449 passed，0 failures/errors/skipped，18/18 checks。完整[日志](../artifacts/code_quality/acbc8dea6add0729a44338feb76422908af95f40d07438e2b42aa73eab194d07/650fd71cb2c54880b0fec9b652557de4/pytest.log)、[JUnit](../artifacts/code_quality/acbc8dea6add0729a44338feb76422908af95f40d07438e2b42aa73eab194d07/650fd71cb2c54880b0fec9b652557de4/pytest.xml)及[报告](../artifacts/code_quality/acbc8dea6add0729a44338feb76422908af95f40d07438e2b42aa73eab194d07/650fd71cb2c54880b0fec9b652557de4/summary.json)绑定源码`acbc8dea6add0729a44338feb76422908af95f40d07438e2b42aa73eab194d07`；网络边界为loopback_only。该验收包含detector/tests、evolution/tests以及其余仓库回归，不调用真实provider或运行95例实验。
