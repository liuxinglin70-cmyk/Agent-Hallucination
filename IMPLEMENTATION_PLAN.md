# 代码框架优化与因果审计实施台账

## 0. 当前任务与进度

| 项目 | 当前值 |
|---|---|
| 当前任务 | 根据本次用户请求恢复完整18包代码重构；按Pristine逐根因修复、离线验证并最终整理doc、README及实验日志 |
| 计划基线 | `main@071080622818a5da0647036f0966619ef94b0a1e`；2026-10-08 |
| 本轮已完成 | 代码提交8faf8b0；B43成本来源丢失已修复待独立复审：固定Git基线20项反例失败；完整2113项纯代码、58项平台及AST80/三CLI/源码不变通过。共享来源规则贯通runner、成本汇总、实验报告和脚本导出，未知来源不宣称精确值或已证明下界；数字读数及未知请求数保留。220条扫描命中逐行核销，5条公开合同保留必要行为并清除失效注释/命名。旧架构方案已移入docs/design-archive，仅修正搬迁后的日志链接，历史内容保留。最终清理、独立审查及18包验收未完成。 [纯代码](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/summary.json)、[平台](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/cf3533c6a15047909636604234cf0497/summary.json)、[公开合同](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/public-contract-review.json)。 |
| 代码实施 | M00已验收；M01–M17功能条件已有映射，B42/B43根因已修；当前全局清理、独立复审及M17最终验收未闭合 |
| 下一工作包 | 固定代码提交8faf8b0与源码35706f9f的证据已重绑定；完成M01–M17和全局清理的独立复审，处理发现并闭合18包验收。 |
| 实验状态 | 不执行；真实模型请求、基准测量、A/A/A/B、调参、自演化真实轮次均不在本期范围 |
| 更新时间 | 2026-10-11，Asia/Shanghai |

本文件是本轮工作的唯一计划、任务状态和续接入口。每次实施结束就地更新表格、缺陷状态和四项检查点，提交历史由 Git 保留，不另建 progress、日报或计划副本。

源材料：[研究设计](docs/uncertainty-guided-causal-audit-design-20261008.md)、[组会说明](doc/group-meetings/group-meeting-uncertainty-causal-feedback-20261008.md)、[当前实现合同](docs/technical-solution.md)。研究设计描述目标，当前实现合同描述已落地行为，本台账描述实施工作。实现完成后同步更新当前实现合同，避免把拟议接口写成已上线功能。

## 1. 范围、完成定义与默认决策

### 1.1 本期交付

| 交付 | 代码层面完成条件 |
|---|---|
| 旧框架重构 | 生产调用者使用唯一实现，职责和依赖清楚；被替代代码、私有 shim、重复规则及复述代码/修复历史/失效说明的注释清除，保留业务约束原因 |
| 缺陷修复 | 最小确定性用例先复现，源头修复后通过；相邻异常路径和调用者回归完成 |
| 风险与因果功能 | 画像、候选、真实依赖投影、审计、正向定位和阴性复审接口贯通，能由离线替身执行完整入口 |
| 自演化反馈 | 诊断进入实际 family、机制、提案和修订反馈；L0/L1、正式门、版本与记忆生命周期仍受约束 |
| 可维护交付 | 必要离线验证、独立审查、安装/CLI 检查和本台账更新完成，残余事项有明确证据和去向 |

离线单元测试、回归测试、合成轨迹、受控 Fake/Mock provider、本机回环 HTTP 测试、沙箱资源测试和静态审查属于代码验证。它们不能证明真实模型准确率、漏检下降、定位提升或跨轮进化收益。

本期不运行真实模型、不调用外部工具执行 Agent 任务、不重测历史 693 例、不生成新基准结果、不学习风险权重、不降低已有保护门、不修改原始标签或证据归档。

### 1.2 默认技术决策

1. 保持 Python ≥3.10 和标准库运行时；不引入新的工作流框架、数据库或训练依赖。测试优先复用现有 pytest。
2. 先实现确定性的语义风险代理和真实依赖核验。真实 teacher-forced PPL 后端延期；能力检测返回 `unsupported`，禁止把模型自评分包装成 PPL。
3. 运行时新增功能默认 `off`，提供 `shadow` 和 `tracer_context`；阴性反馈独立开关。模式用于控制可选业务功能，不用于绕过缺陷。
4. 无真实校准记录时，生产阴性扩展不激活。离线用显式注入的测试策略验证完整触发链；测试证书不得成为生产配置的有效校准来源。
5. 未知结构、缺失观察、证据冲突、闭包不全和预算不足输出有原因的未知/未决状态；保留原裁决权限。

这五项是本期可行实现范围，不需要等待真实模型实验。后续是否开启新行为由另行授权的实验和部署决定，不作为代码包验收的前置条件。

## 2. Pristine 执行要求及仓库适配

规则来源：[Pristine SKILL.md v2.2](https://github.com/newbanser/pristine-skill/blob/0b6f659fb5296ad19f6ca020d124714a8b9dac70/SKILL.md)，固定提交 `0b6f659fb5296ad19f6ca020d124714a8b9dac70`。本轮已读取原文；没有全局安装 skill、修改用户配置或复制第三方工具进仓库。

| 原则摘要 | 本项目执行方式 | 审查证据 |
|---|---|---|
| 查前提、找反证、修根因 | 先证明缺陷及影响；一次处理一个根因，改规则生产者与所有消费者 | 复现输入、反例、修复前后断言 |
| 名称清楚、规则单一来源 | 复用标准库和已有合同，统一引用、身份、升级派生和门读值；注释解释业务约束 | 调用检索、重复定义/规则检查 |
| 清除替代残留 | 修改现定义并删除失效实现，不留下 backup、注释代码、临时旁路或私有兼容 shim | 删除清单、引用核销、diff |
| 审查代码与运行代码一致 | 通过正式入口测试，冻结源码/配置/工件身份；跨进程 worker 使用同一包 | CLI/安装/worker 身份回归 |
| 持久状态简短且准确 | 本文件就地更新；四项检查点只保留当前状态 | 状态、命令结果、提交与审查链接 |

适配边界：已有实验报告、冻结协议和 ledger 是实际证据，不是可随意删除的草稿；仍在使用的公共 CLI、数据输入和权限合同也不是冗余代码。对新内部定义不增设无消费者的兼容层；历史证据如果需要读取，使用明确的离线读者并隔离于新运行复用，缺新身份的新运行工件直接拒绝。

Python 的真源标注使用合法的 `# SOURCE: <symbol>`，只指向符号，不在调用处复制规则。上游 `pristine-scan.js` 虽会扫描 Python 文本，重复定义与 SOURCE 检测主要针对 JavaScript 形式，因此不能用其零告警证明 Python 无死代码。M00 同时实施 Python AST 和调用者核查。

未来调用上游扫描工具须使用上述固定提交并记录路径/版本；原脚本与其 selftest 保持原样，在代码目录扫描。诊断结果人工核销，不把关键词命中自动当成可删除代码。Node 不可用时明确记未运行，不能伪造通过；纯 Python 验证继续完成。

## 3. 系统不变量与职责真源

| 不变量 | 唯一规则归属/主要验证者 |
|---|---|
| detector 只接收任务和轨迹，gold 不进入运行时或提案中的目标答案 | `dataset.py`、`packet.py`；跨模块输入防火墙测试 |
| event、interaction、position 和 source_step_id 含义稳定；工具观察是来源 | `packet.py`；图与投影合同 |
| 轨迹引用与外部证据引用各自解析，后者必须绑定适用快照 | `packet.py`、`evidence.py`；live/frozen 使用同一验证器 |
| 风险、模型审计、typed semantic 和受限组件不能自行定罪 | 因果合同、`interventions.py`、`adjudication.py` |
| Tracer 仅追溯当前 hearing 的偏差；Recall hearing 保持信息隔离 | hearing 合同、`step_tracer.py`、`api.py` |
| 判定权威与定位权威独立；未决不等于阴性 | `adjudication.py`、`result.py`、指标读取器 |
| 重试、repair、多票、多个候选共享 case deadline 与预算；取消/fatal 不被吞掉 | `runtime.py`、`provider.py`、案例执行记录 |
| 已校验的原始候选/升级行是唯一来源，派生列表不被再次信任 | 统一升级组装合同、回放验证 |
| 缓存、恢复和冻结只能复用相同真实输入，不掩盖目标改动 | execution identity、replay policy、backend/checkpoint |
| L1 与正式门先验证预期案例覆盖，收益不抵消保护损伤 | `development.py`、`metrics.py`、`gates.py` |
| 发布、回滚、恢复和记忆拥有独立快照；不可测不写成算法失败 | `registry.py`、`checkpoint.py`、`ledger.py`、`memory.py` |
| 新文档描述实态，计划不冒充实现；历史测量原始字节不改 | 本台账、技术合同、`.gitattributes` 和证据归档 |

## 4. 缺陷与风险登记

下列位置以计划基线为准。`已复现` 只表示审查确认，绝不表示修复完成。更新时保持 ID，补修复提交、测试与审查证据，不追加同一缺陷的新版本描述。

### 4.1 已确认的修复项

| ID | 当前证据及影响 | 根因修复方向 | 所属包 | 状态 |
|---|---|---|---|---|
| B01 | frozen Recall/Upgrade 解析器遗漏 snapshot，合法 E1 被拒 | 同一适用 snapshot 传入两个解析器；正式 Detector 回放验证 E1 到听证 | M06 | 关闭；e35f144，69 项相关回归与 review_offline_review 的 37 项独立检查通过 |
| B02 | frozen 仅重建单数项，未经验证的派生列表被优先消费 | recaller 的 assemble_upgraded_candidates 为 live/frozen 唯一组装规则；覆盖单/复数派生字段，按 corrected_step 排序，失败不产生阳性 | M06 | 关闭；e35f144，伪造列表反例修复、69 项相关回归及 37 项独立检查通过 |
| B03 | 更早 target 不在 anchor 窗口，却报告 complete=true | 窗口覆盖 min(anchor,target) 到 max(anchor,target)，边界截断显式给原因 | M02 | 关闭；565f191，detector 326、跨包 41、独立 39 项通过；review_offline_review 无阻断 |
| B04 | `runtime.py:232–238`：并行把非预期 `BaseException` 转 None，顺序却抛出；代码缺陷被当弃权 | 阶段显式转换可恢复弃权，运行时传播程序错误和中断 | M04 | 关闭；6733156，7 项异常/停止反例及相关 58 项回归；e35f144 独立审查无阻断（37 项通过） |
| B05 | 批次 resume 只比较案例 ID/配置，源码和逐例内容改变仍复用 | run_manifest.v3 与逐例 execution_identity 同时绑定源码、有效配置、record/packet、registry 与声明工件；缓存缺身份拒绝；漂移在写清单前拒绝 | M01/M08 | 关闭；5a0bff8 及修正 7f91c3e；相关 469 项、独立 25 项及 replay 反例 1 项通过，初次 P1 已核销；演化侧整体 identity 属 M01/M13 后续范围 |
| B06 | `runner.py:147–153`、`api.py:348–373`：整例重试只返回最后一次 execution，早期请求与 token 遗失 | measurement 为 request/case execution 唯一汇总规则，execution.v2 保留整例尝试，失败尝试立即落盘，嵌套请求只累计一次 | M04/M08 | 关闭；a2fed20，2 项最小反例先失败后通过，相关 82 项/批次失败 15 项/平台 3 项通过，AST 67 文件通过；独立 51 项及三票并发反例验证 6 请求/30 tokens 不重计，无阻断 |
| B07 | completed 错减缺失行导致负数；调度中断可能遗失已落盘案例 | run_summary.v3 从 expected/returned/started 集合定义 completed/not_started/missing；工作线程登记返回行，调度中断保留已提交案例；重复或多余行拒绝 | M08 | 关闭；9c40b5c，5 项最小反例修复后相关 51 项、2 项 CLI 回环及独立 19 项/81 个覆盖分区通过；review_offline_review 无阻断 |
| B08 | 默认g0需要registry且默认run_id=None；独立审查还确认按当前selector分类错误、明确缺plan默认回退、baseline认证失败exit0、未声明paired臂绕过验证 | 默认arm/逐臂policy与measurement身份已实现；输入/退出合同已修，run_snapshot按实际运行身份分类；恰好两比较臂，不凭selector判断 | M17 | 入口修复已复核；023c516完整1457/独立84通过；效果/健康10c9ed5独立9场景+63项核销，六脚本cfb461c独立25/相关64项核销；导出7c39f17独立发现悬空输出链接P2，正式反例先失败后通过、相关43通过，f5163e0独立17+2/相关26核销 |
| B09 | AST 基线报告明确检出两个 `_step_kind_split`；全仓调用检索只有两个定义，无消费者 | 删除两个无消费者定义，保留实际 `_joint_kind_split` 真源 | M15 | 关闭；4000669，AST/1020 项回归及 review_4000669 核销 |
| B10 | `development.py:375–402,562–563`：双臂同时漏掉预期 neighbor，目标改善仍能通过 L1 | 配对评分前要求两臂恰好覆盖 target∪neighbor；缺测独立返回 | M15 | 关闭；9dbf358，最小回归修复前 8 失败/2 通过，修复后相关 87 项、独立 17 项通过；review_offline_review 无阻断 |
| B11 | `development.py:567–577`：gold step 已知时，wrong→same wrong 被计为 regression，即使另一 target 改善 | 回退按 baseline 原本命中→失去命中定义；检测/权威退化单独处理 | M15 | 关闭；9dbf358，最小回归修复前 8 失败/2 通过，修复后相关 87 项、独立 17 项通过；review_offline_review 无阻断 |
| B12 | scoped semantic refs 未验证存在/范围，空 refs 自动填满窗口；executor 可修改待校验投影 | packet_reference_error 为轨迹引用验证真源；正向 verdict 必须有真实窗口引用，executor 输入独立副本，移除重复 identity/vocabulary 执行代码 | M03/M07 | 关闭；77a48c4，7 种非法引用及修改窗口反例先失败后通过；detector 430 项、跨包 80 项、AST 67 文件及独立 49 项通过，无阻断 |
| B13 | `route_failures.py:325–327,405–427`：追加 resolution 后 reducer 跳过它，已解决路线仍 insufficient | 按顺序归约 observation/resolution，关闭当前观察 epoch，保留历史并允许后续重新开放 | M16 真源；M14 消费 | 关闭；9b3bac0，6 项反例修复前失败，相关 route/capability 62 项及独立 6 项/旧解决记录重放反例通过，无阻断；历史字节保留，独立 measurement identity 仍属 M16 未完成项 |
| B14 | `route_failures.py:152–229`、`engine.py:2273–2275`：只读检测 delta，定位 joint_broken=1 被记为无改善无损伤 | outcome 明确 target_kind 与 delta basis；定位/联合损伤不能写为路线能力不足 | M15 真源；M14/M16 消费 | 关闭；d14e5f9，7 项反例先失败后通过，182 项扩展回归/AST 67 文件及独立 22 项通过，无新增阻断；普通/替换定位与联合机会迁移 ce6777d，136 项回归及独立 18 个正式单位入口用例通过 |
| B15 | 固定811662f独立子进程确认两种合法DSL正则在匹配开始后超过1.5秒；输入长度和模式启发式不能约束re执行时间 | 在标准库运行时实现可终止的匹配资源边界，共享case deadline/取消；超时不得授权not_matches或定罪 | M03 | 修复待审查；工作区采用单次0.5秒/程序共享1秒子进程预算，期限和取消传播；资源未知不写facts、不触发Recall、不定罪；4项超时反例修复、3停止路径及正式plan消费验证通过 |
| B16 | 两个未解决冲突只解释其中一个，citation_error/live Defense/frozen仍接受全部引用 | 对每个相关未解决冲突分别要求说明，保持单冲突业务合同 | M03 | 修复待审查；citation_error逐冲突校验，live/frozen复用；三入口拒绝遗漏说明、接受完整说明 |
| B17 | Alpha属性冲突的resolution可选择无关Gamma来源，三入口都接受 | resolved_by必须属于对应规范entity/attribute来源组 | M03 | 修复待审查；snapshot拒绝跨entity/attribute来源，直接conflict_report亦不认跨组resolution |
| B18 | 直接adjudicate自定义权限表可授权诊断内置/未知deterministic行；正常validated registry未复现此绑定 | terminal消费者始终核验deterministic谓词词汇与校准权限，保留声明typed DSL权限合同 | M03/M07 | 修复待审查；无runner及deterministic权限取内置词汇交集并校准，8项公开入口反例通过；typed DSL原合同回归通过 |
| B19 | submit_final_answer已交付，随后无内容terminate覆盖最后工具锚点，final_delivery_present误报missing | 交付身份保留实质answer工具通道，终止状态不能抹掉此前交付 | M03/M02 | 修复待审查；packet优先保留实质answer工具、无交付时保留状态锚点；final_delivery读统一通道规则。原校准源码指纹失效，保留原始字节并拒绝旧权限 |
| B20 | DSL规范化返回when_features/when_facts，二次校验只读when，限制被静默丢弃 | 规范形式保留when.packet_features，事实条件编译入typed conditions，规范化幂等 | M03 | 修复待审查；工作区正式规范化/原始与规范输入同语义反例通过；规则字段闭合，拒绝旧派生条件及其他未解释字段，3项反例修复前失败后通过 |
| B21 | when.facts接受任意上下文字段，gold条件能绕过typed字段及hook输入白名单 | 所有事实条件复用_validate_condition和阶段字段权限，删除任意context扫描执行路径 | M03/M07 | 修复待审查；gold/任意路径拒绝及upgrade.pre权限反例通过，新增5项最小回归修复前全部失败 |
| B22 | Judge/Tracer/Scanner只检查quote非空或直接透传，实际合成packet可接受FABRICATED_QUOTE | quote归属核验由packet真源提供，迁移live/frozen阶段消费者并核查替身 | M05 | 工作区修复待审查；18项正式反例先失败，packet统一核验迁移三parser及frozen；新增35项合同用例、相关130通过；8d0bfde1 code_only1647通过，独立实施审查未完成 |
| B23 | frozen验证浅复制，输入与规范化结果的refs/provider/status/hearing/metadata共享嵌套对象 | replay模块拥有独立信封快照，API仅编排；迁移全部正式消费者并删除旧私有入口 | M06 | 工作区修复待审查；验证侧12项双向修改及生成侧5项反例先失败；相关236通过，归档/定位模块只读17项通过；5b085a08 code_only1667/platform57通过，独立实施审查未完成 |
| B24 | Judge无有效票和不足quorum均写provider_error，合同/超时类别丢失 | 投票保留结构化失败；同类全失败保留类型，不足或混合失效归no_quorum | M04/M05 | 工作区修复待审查；3项分类反例先失败，相关84通过，fatal传播保留；5b085a08 code_only1667/platform57通过，独立实施审查未完成 |
| B25 | 冻结复用只核对packet/快照/听证目标及上下文，缺源码、阶段路由和基础prompt绑定；更换输入仍复用旧阶段结果 | replay生成闭合且自校验的逐阶段基础输入摘要，Detector强制验证，backend复制；下游漂移按依赖移除冻结结果 | M06 | 工作区修复待审查；7项反例先失败，新增14项及相关132通过，00288c0e code_only1681/platform57及AST/三CLI/源码不变通过；独立实施审查未完成 |
| B26 | 冻结基线带阶段干预，移除后仍复用旧Judge/Scanner/Upgrade/Tracer/Defense结果；5项正式反例均先失败 | replay基础输入摘要依共享hook政策绑定各阶段条目；活动mask在输入失效选择前核验，API删除重复mask判断 | M06 | 工作区修复待审查；5项反例先失败，新增10项及相关142通过；843db6e6 code_only1691/platform57及AST/三CLI/源码不变通过，独立实施审查未完成 |
| B27 | Judge阴性且Recall听证判阳时，首听证已有结果而顶层Tracer/Defense的proof/status/provider镜像为空 | 完整听证队列统一导出首听证镜像，删除Judge分支独占赋值及未使用中间值 | M06 | 工作区修复待审查；6项镜像反例先失败，相关166通过；bc0d5e0f code_only1714/platform57及AST/三CLI/源码不变通过，独立复审未完成 |
| B28 | Judge翻案后后续Recall定罪，决定性事实仍采用先前positive的答案字段且缺选中candidate_id | 从实际定罪听证的统一目标投影生成最终事实，明确记录选中候选ID | M06 | 工作区修复待审查；2项归属反例先失败，live/frozen多听证及相关166通过；bc0d5e0f code_only1714/platform57及AST/三CLI/源码不变通过，独立复审未完成 |
| B29 | frozen把ok作truthy读取，failed阶段可携带parsed成功裁决值并被后续消费 | 共享冻结阶段合同要求布尔ok、结果/状态一致，failed不得携带parsed裁决；保留无裁决的合法失败回放 | M06 | 工作区修复待审查；3项failed载荷及4项非法权限标记反例先失败，合法失败回放3项及相关166通过；bc0d5e0f code_only1714/platform57及AST/三CLI/源码不变通过，独立复审未完成 |
| B30 | 仅交换复合字段字典键顺序，packet摘要不变，但图/消费路径/上下文摘要改变 | bound_fields按规范字典键序遍历，保留列表顺序，所有画像/图/路径消费者使用同一源规则 | M01/M10/M12 | 修复待审查；正式键顺序反例先失败后通过，相关199项及bf05f7ce完整code_only1749/platform57/AST/三CLI/源码不变通过；[核验](artifacts/code_quality/bf05f7ceba0f93874490829ef28a752134fd87ec006283412e85d3ec167479dd/720befaf3d5044f6b8ff02e9207c9b3b/claim-lineage-verification.md)，独立复审未完成 |
| B31 | 原开发回调浅复制导致父版本、家族、机制、原反馈与输出互相污染；完整父程序缺trigger/claim，Hybrid无反馈读取也无法恢复原反馈 | development_adapter拥有修订输入/反馈快照与输出，完整父程序及别名反证投影，LLM/Hybrid输入和反馈合同一致 | M14/M16 | 修复待审查；六项正式反例先失败后通过，共14项新增/相关64项，7a512d25完整1818/57/AST79/CLI/源码不变通过；[开发适配核验](artifacts/code_quality/7a512d255f54bd8aff633e77d113fdd8eabb1e53dfd525bb2f7dc3577793dc93/bc5084ffee934e1f834923c5b422f3a9/development-adapter-verification.md)、[纯代码](artifacts/code_quality/7a512d255f54bd8aff633e77d113fdd8eabb1e53dfd525bb2f7dc3577793dc93/bc5084ffee934e1f834923c5b422f3a9/summary.json)、[平台](artifacts/code_quality/7a512d255f54bd8aff633e77d113fdd8eabb1e53dfd525bb2f7dc3577793dc93/4b2f8bcac10f43bda56244a398d2b2b8/summary.json)，独立复审未完成 |
| B32 | route_failure_state原按cycle去重，同观测跨cycle被计两次；numeric revision或缺身份记录能压制路线 | 门控先投影实际配对来源、输入/候选注册表和冻结范围，记录及能力规划匹配当前运行身份，原始叶来源去重；缺/错/重复只作诊断，历史字节保留 | M14/M16 | 修复待审查；三个正式反例先失败后通过；新增29项、相关143项/60项及末次42项通过；51d7ecf7全量1855通过/1失败已定位并修复实际Mock来源，原正向断言保留，bb62955c完整code_only1859/platform57、AST80/三CLI/源码不变通过。当前实际Engine→单位报告→存储→能力规划与重放边界已验证，无新独立复审  [来源消费核验](artifacts/code_quality/bb62955c9975b2d276619d14a6863f1825ff2c56262a91a7ee9f885eb7a9be47/2ce1ae1a9bb7420aa8ff53e802459a1c/route-observation-verification.md)、[纯代码](artifacts/code_quality/bb62955c9975b2d276619d14a6863f1825ff2c56262a91a7ee9f885eb7a9be47/2ce1ae1a9bb7420aa8ff53e802459a1c/summary.json)、[平台](artifacts/code_quality/bb62955c9975b2d276619d14a6863f1825ff2c56262a91a7ee9f885eb7a9be47/be78baf9601c44cb852d32096c49dceb/summary.json)。 |
| B33 | ActiveRegistry.write先改active与内存，再_save_state；注入OSError后磁盘state仍旧、active已新，重开触发active/state mismatch | active.json唯一原子提交绑定registry/state；共享读者核验、独立快照、进程租约和陈旧提交拒绝；异常重读实际终态 | M16 | 修复待审查；七条正式故障反例先失败后通过，新17项及相关50项通过。提交前失败保持旧提交并清理未引用快照，实际替换后OSError/KeyboardInterrupt传播且内存与重开一致；旧分离格式拒绝且字节不改。3331d727完整code_only1876/platform57、AST80/三CLI/源码不变通过；[提交核验](artifacts/code_quality/3331d727e592712ce25d53cba38c2aa9fc98ad0b6e2c0d414fa2f1ac24aeef1b/60a4b5270156423faa9786ea2b812bac/registry-commit-verification.md)、[纯代码](artifacts/code_quality/3331d727e592712ce25d53cba38c2aa9fc98ad0b6e2c0d414fa2f1ac24aeef1b/60a4b5270156423faa9786ea2b812bac/summary.json)、[平台](artifacts/code_quality/3331d727e592712ce25d53cba38c2aa9fc98ad0b6e2c0d414fa2f1ac24aeef1b/bc2a7886a7b349f885dace446f24db98/summary.json)，独立复审未完成 |
| B34 | PromotionRun._publish先部署后写version/ledger/terminal；版本失败后已部署却缺终态；公共promote持久化后补candidate_status导致返回/落盘不一致 | publication终态、版本和事件与部署原子提交；两种协议共用恢复；终态生成决定candidate_status，公共入口直接返回；事件幂等与冲突拒绝 | M15/M16 | 修复待审查；四条持久化及两条公共终态反例先失败后通过；新增28项，相关134项和末次28项通过。508b3bca第一轮1901/57、AST80/CLI/源码不变通过，公共入口缺字段后续反例补修；最终eae68b5b完整1904/57、AST80/三CLI/源码不变通过；[晋升核验](artifacts/code_quality/eae68b5b3cb516d7f59fbb28e60ed0c4cc3c9a236024a0101df10d7a226c4ef0/ca0828c4d48c48b6a9167d75f3eaa4c1/promotion-commit-verification.md)、[纯代码](artifacts/code_quality/eae68b5b3cb516d7f59fbb28e60ed0c4cc3c9a236024a0101df10d7a226c4ef0/ca0828c4d48c48b6a9167d75f3eaa4c1/summary.json)、[平台](artifacts/code_quality/eae68b5b3cb516d7f59fbb28e60ed0c4cc3c9a236024a0101df10d7a226c4ef0/2be24ae26b7f4ac6abd919ef2fb81ae7/summary.json)。独立复审未完成 |
| B35 | memory只比逻辑键，三个入口同键不同内容跳过落盘却返回新内容；检查/追加非原子；recall输出可覆盖JSONL或越界 | 唯一_append_record校验键与内容，在线程锁/现有进程租约内检查追加；纠正与recall共用写入边界，JSON快照不得覆盖历史或越界 | M16 | 修复待审查；六条重放/并发及五条输出边界正式反例先失败后通过。新44项、相关130项及末次58项/6项通过；含实际Engine、上下文委托、进程竞争、写前后中断、JSON类型/所有权和纠正。ef2ed2ca完整1947/58、AST80/三CLI/源码不变通过；[记忆核验](artifacts/code_quality/ef2ed2ca2f93198be6d5f9fe582bf3dbc36f237f2ad010ef7708fcd23f843a75/430f54e639134c6f8bfffa6d85982d43/memory-commit-verification.md)、[纯代码](artifacts/code_quality/ef2ed2ca2f93198be6d5f9fe582bf3dbc36f237f2ad010ef7708fcd23f843a75/430f54e639134c6f8bfffa6d85982d43/summary.json)、[平台](artifacts/code_quality/ef2ed2ca2f93198be6d5f9fe582bf3dbc36f237f2ad010ef7708fcd23f843a75/cbdff2c773744c61b7adc1fcf6a7468f/summary.json)，独立复审未完成 |
| B36 | CycleJournal.save先改state，持久化失败后内存completed而磁盘旧；Engine记录interrupted失败掩盖原异常 | checkpoint保存及权威绑定共用独立JSON发布，异常后核对实际提交，未知状态拒绝复用；周期记录分别尝试且次级错误附在原异常对象，准备/执行共用清理 | M16 | 修复待审查；首轮15失败/2通过，修复后新27项、首轮相关31项通过，覆盖写前后OSError/KeyboardInterrupt、输入和状态隔离、权威绑定、未知提交、并发、实际Engine三类原异常和双记录故障、恢复准备清理；扩大相关146项及9c28d9b6完整1974/58、AST80/三CLI/源码不变通过，独立复审未完成；[检查点核验](artifacts/code_quality/9c28d9b6dcf473df5f8d4de5575b1c8a94e0bd80ea6b5947ce957cc0cc4894ac/b162375f3f02432e8fafc3d7bc7f7dec/checkpoint-commit-verification.md)、[纯代码](artifacts/code_quality/9c28d9b6dcf473df5f8d4de5575b1c8a94e0bd80ea6b5947ce957cc0cc4894ac/b162375f3f02432e8fafc3d7bc7f7dec/summary.json)、[平台](artifacts/code_quality/9c28d9b6dcf473df5f8d4de5575b1c8a94e0bd80ea6b5947ce957cc0cc4894ac/e4de619663cd423fb7d20ca4222f43af/summary.json) |
| B37 | cycle_completed与报告先于终态checkpoint保存；completed保存失败后写interrupted，完成恢复快捷返回报告却不修复checkpoint | 唯一完成记录原子绑定报告/事件及身份，正常/预检/冻结共用；输出幂等投影，恢复仅补投影，提交不可改写为interrupted；全量入口走同一恢复 | M16 | 修复待审查；4条正式反例先失败后修复，新25项，首轮相关44通过/1失败为旧注入边界迁移，原恢复断言保留，后续64项及实际两周期恢复1项通过；扩大相关154项及159e505a完整1999/58、AST80/三CLI/源码不变通过，独立复审未完成；[周期发布核验](artifacts/code_quality/159e505ab06bc4ba0cb532ef1247226535e6303f21eac706631d593aad8594cc/82e4756a4123476892d2d4dff45742ec/cycle-publication-verification.md)、[纯代码](artifacts/code_quality/159e505ab06bc4ba0cb532ef1247226535e6303f21eac706631d593aad8594cc/82e4756a4123476892d2d4dff45742ec/summary.json)、[平台](artifacts/code_quality/159e505ab06bc4ba0cb532ef1247226535e6303f21eac706631d593aad8594cc/69644497dc064306b7bb57ae53046229/summary.json) |
| B38 | run_experiment返回cycles直接别名内部历史，修改后恢复统计与磁盘相反；零/前缀请求混入其他周期，空请求仍触发验证集评估 | 私有周期历史、公开独立快照，无整体替换入口；聚合与验证集消费仅使用本次请求实际完成记录，零请求不评估 | M16 | 修复待审查；九条快照/范围及一条零请求验证集正式反例先失败后修复；新15项及首轮相关34项通过，含正常/预检/冻结、追加/清空/嵌套污染、禁止整体替换、零/前缀及历史冻结不压制本次验证；扩大相关182项及5cc27418完整2014/58、AST80/三CLI/源码不变通过，无新独立复审；[聚合快照核验](artifacts/code_quality/5cc274185665ab5a6722ff4be57a0741e2aafe503579bec93764b4894f344285/43877f48f3ee44b6865706ee9a5f86f9/experiment-snapshot-verification.md)、[纯代码](artifacts/code_quality/5cc274185665ab5a6722ff4be57a0741e2aafe503579bec93764b4894f344285/43877f48f3ee44b6865706ee9a5f86f9/summary.json)、[平台](artifacts/code_quality/5cc274185665ab5a6722ff4be57a0741e2aafe503579bec93764b4894f344285/a1f30cad16cf4c0f992896904c8a3f53/summary.json) |

| B39 | 成功状态用substring识别，unsuccessful、success=false和普通query=success均触发交付缺失 | _reports_success统一精确状态、显式布尔True、合法JSON对象及固定完成banner，失败及未知不宣告成功 | M03 | 修复待审查；6失败/3通过正式反例，新13项及相关168项通过，0ef8b81c完整2027/58通过；[核验](artifacts/code_quality/0ef8b81c3820f52fbde5954da6494c0ee3226db8473b2351e0d5754460f9c805/4a43d55b087c44f3a742df215dce6471/terminal-success-verification.json) |
| B40 | 模型required_action合同使真实许可任务被补造启动或移动义务 | 内置动作谓词只解析packet.question，所有消费点共用；保留模型合同供其他声明输入和模型阶段解释 | M03/M05 | 修复待审查；4条正式反例先失败后通过，新8项、边界37项及初轮相关139项通过，33112d1a完整2035/58、AST80/三CLI/源码不变通过；[核验](artifacts/code_quality/33112d1afab82a14589b41d24bb1b9739bd2727614195cf6da999ef29b2da603/9e176ba7493b43deab4d0502f58471d0/action-grounding-verification.json)。B41有限指令规则另行修复待审查，未声称通用语义或真实检测收益 |

| B41 | 任务中的禁止、询问、解释、引用和陈述被误读为执行要求；仅排除否定不能证明肯定指令 | 工具/移动/启动共用有限_action_is_required，核对分句、前缀、缩写、引用及字面对象；实际明确要求与许可pending业务语义保留 | M03/M05 | 修复待审查；初轮14失败/10通过，追加肯定前缀4失败/31通过后修复，新41项及相关194项通过；最终7f3b8e7c完整2076/58、AST80/三CLI/源码不变通过。内置动作及合同覆盖指纹改变，历史校准/语料未改或重测；[核验](artifacts/code_quality/7f3b8e7c3382ff2eb5f3681162c31eac0687fcdff5c35fe562ab40e832797413/5dfbc6465360429aa8b0410ef4798424/task-directive-verification.json)。有限英文指令规则不证明通用语言判断或真实检测收益 |

| B42 | 周期只在创建/恢复比较运行输入，当前周期观测前后及完成发布未检查配置/scorer/案例/后端变化 | 单一_cycle_contract生成合同，独立固定输入；CycleJournal逐例与整臂核验，准入及完成发布前全池核验 | M13/M16 | 修复待独立复审；17项正式离线反例分别先失败后通过（16项观测/复用/完成边界，另1项准入边界）；完整2093/58、AST80/三CLI/源码不变通过。漂移拒绝落盘或终态发布，既有观测字节保留，中断记录可写；[纯代码](artifacts/code_quality/bc708d8534d13d3d98fa5f9a738be9e66e8e0b47bdd002858b369352577f7a2a/9795715170794a40a906d3888d7b34bb/summary.json)、[平台](artifacts/code_quality/bc708d8534d13d3d98fa5f9a738be9e66e8e0b47bdd002858b369352577f7a2a/3d0d144d121f4517bb1bf30e895ad1f8/summary.json)、[周期输入核验](artifacts/code_quality/bc708d8534d13d3d98fa5f9a738be9e66e8e0b47bdd002858b369352577f7a2a/9795715170794a40a906d3888d7b34bb/cycle-input-verification.json) |
| B43 | runner保留阶段usage来源，但case_execution/result_usage将其提升为请求观测；报告和脚本又丢失来源，低估读数可被误写成精确成本 | measurement统一request/lower-bound/unknown来源合并和只读解析，迁移报告及两脚本导出；保留数字和未知计数 | M08/M17 | 修复待独立复审；固定Git基线20项反例全部失败；完整2113项纯代码、58项平台及AST80/三CLI/源码不变通过；未知来源不宣称精确总量或已证下界，历史行只读。[基线反例](artifacts/code_quality/ffcbb08fd6aeb518befe6ea71f28542b9535e53742b2cd48e1ea93d0d61bde47/usage_source_baseline_red/summary.json)、[纯代码](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/summary.json)、[公开合同](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/public-contract-review.json)；[来源核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/usage-source-verification.json) |

B01–B08、B10/B11、B13/B14 的审查复现使用纯内存、AST 提取或 Fake 对象，无真实 provider、无历史测量写入。B12 修复保持 evidence-only 权限，反例证明引用和投影可信性缺陷，不声称直接越权定罪。


### 4.2 待验证风险与能力缺口

| ID | 观察 | 下一步与判定条件 | 所属包 |
|---|---|---|---|
| R01 | M02 已实现 event_window_projection.v2：内容/packet 摘要、独立数据、遗漏 ID/截断原因、交付锚点；后续活动仅名 has_subsequent_activity | 565f191 已核销（独立 39 项）；实际依赖闭包和已纠正证据由 M10/M11 实现 | M02/M07/M10 |
| R02 | M01 已实现文件/直接配置共用校验，保留 causal 块，拒绝未知键、负数、NaN、无穷及 bool 混作整数，无凭据解析不读 .env | 校验子任务 c58a147 独立 69 项核销；有效配置已进入 Detector/批次 execution identity，演化侧继续实施 | M01 |
| R03 | frozen Tracer 已比较实际 causal_context_digest；上下文证据摘要排除耗时/成本，策略变化重新执行 Tracer | 相同输入复用及策略变化重跑 2 个反例通过；共享detector.replay阶段reach、直接失效与fresh来源唯一；固定复核发现外部API丢失full-view action，完整/仅hearings信封共4正式反例修复后相关155通过，7e45ecb固定复核独立38+相关120通过；Scanner身份与完整周期仍由M06/M13核验 | M06/M12/M13 |
| R04 | 引用集合枚举、step→event、provider forwarding、executor 身份验证重复 | 实际executor绑定已统一为_validate_executor_binding，语义/组件共用；其他重复规则按职责和权限边界继续核销，不强行合并阶段信息权限 | M02/M03/M05/M07 |
| R05 | engine当前5322行、_run_cycle885行；共用准备与开发修订已各有唯一实现 | cycle_preparation及development_adapter已成为实际调用方，旧重复算法、适配回调及delta定义删除；7a512d25完整1818/57/AST79/CLI/源码不变通过。门编排和完整生命周期按M15/M16继续核验，独立复审未完成 | M14/M16 |
| R06 | 原viability仅接受结构不可能；本轮补齐当前听证/packet/实际策略重算的同claim任务证书 | 首个规范声明/实际交付材料性/符号修复均核验且独立指出与开发注释一致的首次位置，可接受verified_same_claim；另一指控、伪证书、漂移及定位冲突保持needs_review。20项新增及63fb723a完整1802/57通过，完整包和独立复审待完成 | M14 |
| R07 | wheel 不包含数据/实验协议，运行使用调用方提供的 --measurement/--plan；README 已明确路径合同 | 仓库外 wheel 安装后检测、批次、评估、baseline 和三个 help 入口通过；默认缺资源错误提示继续核验 | M17 |
| R08 | 一些测试使用历史 artifacts；禁真实 provider fixture 只在 evolution 测试域 | 全套离线网络护栏；改成最小合成夹具，不能依赖既往运行输出 | M00/M17 |
| R09 | Hybrid定位提案的无条件交付步限制与已核验首次引入冲突 | 排除规则改为无same-claim证据时不前移，保留另一错误、已纠正/未知修复及禁用注释约束；Engine→家族→机制→Fake LLM/Hybrid实际输入测试通过。63fb723a完整1802/57通过，独立复审待完成 | M14 |
| R10 | runtime.validate_path_id/contained_path 已统一 dataset、batch、record、evaluation 和实验读取路径；拒绝 drive/UNC、遍历、保留名、大小写/扁平化碰撞与链接越界 | 22 项代码反例及实际目录链接平台反例通过，相关 125 项合同回归通过；Windows 点前空格和 COM/LPT 上标¹²³设备别名五反例先失败后源头修复，相关64项通过/1排除；六脚本读者已统一snapshot，拒绝外来commit/持锁读取；固定审查待完成 | M02/M08/M17 |
| R11 | 引擎独立 Context/work root 租约、活跃作用域计数、关闭后拒绝调用、init/reset 及 smoke 新目录保护、独立配置快照已实现 | b7d6a8f 固定独立63项全部通过：原43、新20，含真实嵌套操作与with、同线程和其他线程close、初始化BaseException/GC、双向配置快照；e0d4d40 完整1431项通过。跨臂run_snapshot已整合paired/status/旧endpoint读者，完整复验与审查待完成 | M04/M08/M16/M17 |
| R12 | measurement加载/导出共用验证器，排除角色独立保存；子集保留父身份与当前manifest身份、选择摘要；未知计数不能导出 | 前缀导出7c39f17独立16通过/1失败、相关81通过；悬空输出链接缺口已复现并修复，相关43通过，待固定复核；完整协议字节未改 | M17 |
| R13 | B33注册表、B34晋升、B35记忆、B36 checkpoint、B37完成发布/恢复和B38聚合快照修复待审查；完整生命周期与整包验收未证明 | 逐项核验M16和18包合同/审查/清理 | M14/M15/M16 |
| R14 | 机械定罪逐例身份绑定实际校准视图；周期以首次消费绑定同一视图并拒绝新候选臂和完成resume中的漂移，未消费者不加载 | 17d3ae1逐例核销；周期2反例先失败后修，cycle_checkpoint.v3相关50/f2a1933全量1521通过；周期冷加载竞争P2已用加载锁/同次视图绑定/落盘复核修复，3反例与相关61通过，a85edf6固定独立14+相关42通过，原根因核销；完整包仍待验收 | M01/M03/M13 |
| R15 | 整个可选诊断/反馈阶段统一证明保留，允许/拒绝策略回调后检查整例期限与取消 | d97364f固定独立原反例56、新边界12、相关101通过，原拒绝分支P1核销；023c516完整1457项通过 | M04/M12/M13 |

没有调用图、反例或复现支持的事项只能留在风险栏，不能声称 bug 已证实，更不能据此删除代码。

### 4.3 修复优先级

优先级与证据状态独立；风险待确认不等于低优先级。以下是本期施工顺序，不是未经测量的模型影响评分。

| 优先级 | 对应事项 | 处理要求 |
|---|---|---|
| P0：先解除合同阻断 | B01/B02 冻结证据与候选、B04 异常吞掉、B05 恢复身份、B10 保护覆盖、B12 引用、B14 目标类型损伤 | M00 后先复现/修最小调用链；新功能消费这些合同前关闭，完整模块仍按依赖集成 |
| P1：同模块必须修复 | B03 投影、B06/B07 成本与分母、B08 默认入口、B11 定位回退、B13 路线 resolution | 在对应模块验收前关闭；B06/B07 可在 W1/W2 提前修，不能被新功能掩盖 |
| P2：清理核销 | B09 私有死实现、R04 真实重复、脚本/文档残留 | 调用核销后同模块清除，不拖到交付以后 |
| 风险前置核验 | R01/R02/R03/R08/R10/R11/R12/R13 | 在使用相关投影、配置、缓存、网络、存储或生命周期能力前验证；确认后登记修复，反驳则附证据 |

## 5. 目标结构与依赖边界

保持 `evolution → detector`、`experiments → detector`；detector 不导入 evolution。因果诊断是 detector 输出，开发侧消费它，运行时不能读取失败族的 gold。

新增模块必须有当前工作包、生产调用方、唯一职责和离线测试。先检查既有模块能否承担，再新增。以下是目标职责，不是要求一次创建所有目录：

```text
detector/
  packet.py               事件规范、ID/位置、交付、引用集合与只读投影
  evidence.py             外部快照、引用及冲突
  runtime.py              deadline、取消、并发、原子写及请求观测
  identity.py              无领域依赖的规范摘要与执行输入身份（M01）
  replay.py                frozen envelope 与阶段输入复用合同（M06）
  hearing.py               单指控目标规范化及Tracer/Defense听证执行（M06）
  causal_contracts.py      因果诊断 schema、枚举、引用和界限
  step_uncertainty.py      纯风险信号与候选排序
  causal_graph.py          实际依赖、闭包与遗漏
  causal_audit.py          候选核验和有界审计调度
  api.py                   单例入口及业务流，委派上述职责
evolution/
  backends.py              backend 构造与执行，调用统一身份/回放
  worksheet.py             稳定失败家族与开发侧证书
  mechanisms.py            机制和修复操作，禁止标签字面量入 key
  proposer.py              提案入口，委派 prompt/解析责任
  development.py           L0/L1/修订/冻结
  engine.py                周期及运行状态编排
  cycle_preparation.py     preview/cycle 共用准备职责（M14/M16）
  development_adapter.py   父版本与修订反馈适配（M14/M16）
  evaluation_flow.py       arm、覆盖及门读值编排（M15/M16）
```

`identity.py` 只依赖标准库，不做 registry、门或业务决策。是否提取 hearing/evolution 辅助文件由实际调用图决定；如果只是函数移动且没有减少耦合或重复，留在原模块。禁止为统一之名引入第二套 workflow engine、插件注册器或语义规则副本。

重构时先固定现有可观察行为，再抽取单个职责并一次替换所有消费者。新语义通过任务的合同变化明确引入；不要把重构、算法变化、指标口径变化揉成不可审查的大提交。

## 6. 工作包总表

状态：`未开始 → 实施中 → 待审查 → 已验收`；必要时 `阻塞`。缺少测试或审查只能待审查，不能已验收。`移出范围` 仅用于用户明确调整或本文件已声明延期的功能，不当作完成。

| ID | 工作包 | 前置 | 预计人日 | 状态 | 最新证据/提交/审查 |
|---|---|---|---:|---|---|
| M00 | 离线验证、合同基线、静态清理机制 | 本台账 | 1–2 | 已验收 | 4000669；4fcffecc/68845067：code_only 1020、AST/三 CLI 检查退出 0；平台 97f55299/8f21461a 52 通过；独立只读审查 review_4000669（12 项反例通过，无阻断） |
| M01 | 配置与执行身份真源 | M00 | 2–3 | 实施中 | validate_config与execution_identity.v2为当前真源，case/baseline/cache/run resume及harness/checkpoint实际消费；6个任务卡条件按实现和通过断言核对，见[逐条核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json)。f6da71cd完整2014/58及AST80/三CLI/源码不变通过；域摘要职责与历史/公开合同清理、当前固定源码独立审查仍待核销 |
| M02 | Packet、投影、事件引用规范 | M00 | 2–3 | 待审查 | packet为position/source_step_id、引用归属及window.v2投影真源；B03已关闭，B19交付锚点修复已提交94ee8a2。6个任务卡条件逐条核对前后target、内容摘要/独立投影、截断、混合角色和label防火墙，见[逐条核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json)。f6da71cd完整2014/58通过；当前固定源码独立复审及最终消费者核销未完成 |
| M03 | 外部证据、谓词与引用权限 | M02 | 1–2 | 待审查 | 快照/引用、DSL权限、内置校准及声明程序权限各有真源。B39–B41规则修复已相关194及最终2076/58验证，7项条件已映射；最终消费者/清理及固定源码独立复审未闭合；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M04 | Provider、并发、预算、取消、请求记录 | M00 | 2–3 | 实施中 | B04/B06 已关闭；8082ee9 新增预算共享且跨重试不重领；全运行 RunStopped 与案例 CaseDeadlineExceeded 分型，取消不再被 provider/backend 转成案例失败；两个反例先失败后通过，相关 85 项、扩展 584 项、AST 71 文件通过；recaller/defense重复forwarding已删除，直接消费provider_call的统一cache_tag签名，snapshot完整摘要覆盖初次/repair，6反例修复后相关158通过；模块完整收尾/复核待完成；当前条件重绑定[核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M05 | Judge/Recall/Tracer/Defense 阶段合同 | M03/M04 | 2–3 | 实施中 | 引用/quote、投票/quorum/repair、Recall输入隔离及Defense/Upgrade证据约束保留；B40/B41机械规则由真实任务和有限肯定指令生成义务，不由模型或陈述补造。Judge失效唯一召回说明已删除；当前6项条件重映射，完整2076/58通过，最终清理/消费者及独立复审未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M06 | API、听证、冻结验证与回放 | M01/M02/M03/M05 | 3–4 | 实施中 | hearing完整目标、阶段实际输入摘要与因果上下文决定复用；升级派生共用已验证行，各听证镜像和采纳事实绑定。当前7项条件已映射，33112d1a完整2035/58通过；最终消费者/清理及固定源码独立复审未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M07 | 干预执行器和 scope 合同 | M02/M03/M04 | 2–3 | 待审查 | 统一实际executor id/digest规则，两runner消费；删除重复循环与无用component局部变量。相关74项及4512647c完整2076/58通过，6项条件重绑当前实现及执行断言；最终消费者/清理和独立复审未完成；[清理证据](artifacts/code_quality/4512647c4138419f9b682fe868255b900ce404fe02891e4265503f7db9d039c4/153786988d464e018788e8097ae81ad7/executor-binding-cleanup.json)、[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M08 | 批处理、存储、恢复、成本和分母 | M01/M04/M06 | 2–3 | 实施中 | 6项条件已映射：输入/恢复身份、路径封闭、同attempt输入/结果/耗时提交、共同writer租约、集合守恒及三种成本作用范围；B43来源合并/只读解析贯通报告和脚本，20项反例先失败后通过。当前完整2113/58通过；最终消费者/清理和独立复审未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M09 | 风险画像与候选选择 | M01/M02/M03/M07 | 2–3 | 待审查 | 六项条件核对：稳定排序、未知/PPL状态、同上下文字段冲突、任务null locus、mandatory遗漏及只读正式入口；直接probe另核对证据完整性/位置tie、策略与画像摘要不匹配拒绝、context/snapshot及实际scorer内容身份。 此前相关276通过；本轮当前源码完整2113/58通过，条件已重绑定，独立审查和最终清理未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M10 | 实际依赖图与闭包 | M02/M03/M09 | 2–3 | 待审查 | 六项条件核对：实际来源/时序、词面不产生消费、字段/路径绑定、分支及旧值复用、图/投影预算未知、身份和独立投影。前向同claim声明路径与后向候选闭包职责不同，保留各自字段状态和权限。 此前相关276通过；本轮当前源码完整2113/58通过，条件已重绑定，独立审查和最终清理未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M11 | 有界因果审计与 shadow | M04/M06/M09/M10 | 2–3 | 待审查 | 六项条件核对：局部证书无裁决权、模型hypothesis与闭合schema、共享准入预算、shadow基础证据、终止合同及输入/成本身份；另8个schema/范围/限额probe拒绝非法模型输出。 此前相关276通过；本轮当前源码完整2113/58通过，条件已重绑定，独立审查和最终清理未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M12 | 定位上下文及受控阴性反馈 | M05/M06/M11 | 3–4 | 待审查 | 六项条件核对：同claim重算、hearing/Tracer正式入口、上下文回放/repair预算、无校准生产不触发、显式离线策略单次全链、预算及fatal/cancel保留基础证据。 此前相关276通过；本轮当前源码完整2113/58通过，条件已重绑定，独立审查和最终清理未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M13 | Backend、缓存、harness与回放合同 | M01/M06/M12 | 2–3 | 实施中 | 6项当前合同已重绑定，B42同周期漂移17反例修复，完整2093/58通过；隐式校准按首次消费绑定，不预加载。最终消费者/清理及固定源码独立复审未完成；[纯代码](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/summary.json)、[平台](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/cf3533c6a15047909636604234cf0497/summary.json)、[周期输入核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/cycle-input-verification.json) |
| M14 | 因果失败族、机制、能力和提案 | M11/M12/M13 | 3–4 | 待审查 | 6项条件核对同claim viability、机制身份、共用准备、真实proposer投影、独立修订及适用能力/观察；部署/提案无目标答案的实际断言通过。 本轮当前源码完整2113/58及AST80/三CLI通过，条件已重绑定；最终消费者/清理和独立复审未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M15 | 开发、指标、覆盖、四门和晋升 | M00；集成依赖 M13/M14 | 3–4 | 待审查 | 7项条件核对两臂expected覆盖、定位回退、L0/L1触发/保护/预算、共用目标读数、冻结身份、四门及publication公共终态；正式合成准入/拒绝/不可测回归已执行。 本轮当前源码完整2113/58及AST80/三CLI通过，条件已重绑定；最终消费者/清理和独立复审未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M16 | 周期编排、状态、registry 与记忆 | M13/M14/M15 | 3–5 | 待审查 | 8项条件核对共同准备/开发服务、registry CAS、ledger/记忆幂等、checkpoint原子性、completion恢复、独立测量epoch、请求聚合和writer/闭环生命周期；真实Fake入口断言已执行。 本轮当前源码完整2113/58及AST80/三CLI通过，条件已重绑定；最终消费者/清理和独立复审未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |
| M17 | CLI/打包/脚本、全局清理与交付审查 | M00–M16 | 2–3 | 实施中 | B43两脚本导出保留成本来源；21脚本用途/引用清单已建立。6项功能条件核对arm/policy、measurement/prefix、仓库外wheel正式CLI、共同snapshot、统计口径和离线/AST边界；最终清理、固定源码独立审查及18包验收门仍未完成。 本轮当前源码完整2113/58及AST80/三CLI通过，条件已重绑定；最终消费者/清理和独立复审未完成；[条件核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json) |

估计为熟悉仓库的工程实现与离线审查，不含任何模型实验。简单累计约 39–58 人日；经边界明确的并行实现可缩短日历时间，但共享 API/合同/identity/engine 文件须串行合入。发现新根因时在对应任务改范围和估计，禁止为守日期跳过验收。

M15 的 B09–B11 修复子任务可在 M00 后提前执行，不等待新因果功能；其全面门集成在 M13/M14 后完成。
B13 的 reducer 真源由 M16 在 `route_failures.py` 维护；B14 的目标 outcome/评分真源由 M15 维护，M14/M16 迁移消费。两个最小修复可在 M00 后先做；新因果反馈接入时复用同一合同。

## 7. 逐模块任务卡

### M00：离线验证与审查基线

- **输入**：`pyproject.toml`、全部测试域、`scripts/verify.py`、当前 package/CLI 入口。读取契约测试和夹具，不读取凭据。
- **实施**：提取 verify 中可复用的验证步骤，提供明确的纯代码验证路径；全套测试默认禁公网/真实 provider，允许受控回环 HTTP。测试主进程、CLI 子进程和 provider_worker 都遵守网络边界。建立按源目录的 AST 重复定义、导入边界和循环依赖检查。
- **清理/审查**：将依赖历史 artifacts 的测试改成必要的临时夹具。Pristine 原扫描、Python AST 与人工调用核销分别报告；不把扫描词频当完成标准。
- **验收**：没有 raw 数据/凭据也能运行合成代码验证；禁止外部 endpoint 的测试确实失败；本机 HTTP 正常；AST 检出 B09；缺测、取消和异常的基线断言已建立。
- **交付证据**：隔离的 `artifacts/code_quality/<code_identity>/<verification_run_id>/` 报告，含源码、环境、测试范围、命令、退出码、未运行项；每次验证的报告不可变，禁止覆盖此前验收、`artifacts/latest` 或历史模型报告。

### M01：配置和执行身份

- **输入**：`detector/config.py/runtime.py`，`runner.py`，`evolution/backends.py/harness.py/checkpoint.py` 及实际 digest 消费点。
- **实施**：统一规范序列化和摘要原语；ExecutionIdentity 包含源码、有效配置与 route、输入 record/packet、适用 evidence、registry/候选、审计策略、外部工件和实际 replay boundary。配置 JSON 与 `Detector(config=...)` 使用同一校验入口，审计未知键有明确策略。
- **修复/清理**：B05 的新 identity 同步用于 case cache、run resume 和 baseline memo；不只在 manifest 展示。不同职责的 domain namespace 保留，重复的原始 hash/路径推断实现替换后删除。拒绝负数、NaN、无穷和 bool 当整数；不要把 `.env` 或密钥值写入摘要报告。
- **验收**：源码、record 内容、解析后 route、证据、预算、候选或策略变化均影响正确的身份/重跑范围；JSON key 排序不影响摘要；无凭据可解析开发配置；声明而缺失的 scorer 工件拒绝，不能 hash 为 missing 哨兵后继续。
- **交付**：一个可复用的 identity 构造规则、版本化信封和对应缓存/恢复用例。只包含影响执行的输入，避免文档/测试编辑无故使生产缓存失效。

### M02：Packet、窗口、事件和引用

- **输入**：`packet.py` 及 Judge/Recall/Tracer/Defense/Adjudication 的事件查找调用。
- **实施**：统一 packet ID 集合、step→event 和引用归属；修 B03 的 anchor/target 双端范围。新投影记录内容摘要、选中/遗漏 ID、覆盖原因、任务与交付锚点，返回独立数据。存在后续活动与已纠正证据使用不同字段；移除旧误导语义及调用。
- **清理**：删除各阶段重复 ID 枚举。保留 canonical position 和 source_step_id 区别；不能把独立 role=tool 按邻近规则假绑定，新增原 call ID 必须先证明输入格式并保持 label-free 白名单。
- **验收**：target 在前/后、空内容、工具交互、混合角色、非连续原始 step、多轮任务、边界截断和 gold 注入；相同 IDs 内容改变摘要改变；投影修改不影响 packet；无关后续活动不证明恢复。
- **交付**：明确的 projection/ref API 与全调用者迁移，B03 关闭、R01 的字段与内容身份风险解决。

### M03：证据、谓词和权限

- **输入**：`evidence.py`、`predicates.py`、`predicate_context.py`、`predicate_dsl.py`、`conviction_calibration.py` 和快照消费阶段。
- **实施**：复用 packet 引用真源；区分轨迹 ref 与 snapshot citation，两类各自验证存在、适用性、摘录和冲突。统一重复引用检查入口；DSL 的字段可读性和资源限制仍可检查。
- **清理/边界**：谓词执行规则、可定罪 verdict 和校准权限各有真源。保留 permission pending、动作/内容交付和诚实报告失败等业务分支；不能将低层诊断失败统一转成任务违规。
- **验收**：E# 无 snapshot、引用未知来源、证据不适用、同源重复、相互冲突、超长/恶意 DSL、非法读 gold；未校准内置机械谓词及模型证据不能获得 terminal authority；typed DSL保留B18已验证程序与guard声明的权限合同。
- **交付**：可供 live/frozen/causal 调用的统一引用合同，固定事实与未知状态不混用。

### M04：Provider、运行控制与成本

- **输入**：`provider.py/provider_worker.py/runtime.py`、stage 多票和 `runner.detect_with_transport_retry`。
- **实施**：修 B04；provider/合同可恢复失败由阶段显式返回弃权，ValueError 等程序错误及 KeyboardInterrupt/SystemExit 传播。统一 case attempt 记录，累计 transport retry、整例 retry、repair 和多票的实际请求/usage，修 B06。
- **重构**：deadline/request slot/cancellation 是统一运行控制；各听证和审计预算使用剩余额度而非重新领预算。重复 forwarding wrapper 在统一调用签名后删除。保留 Windows worker 隔离和有限超时，不能用未测的纯线程替换。
- **验收**：顺序/并发异常语义一致；fatal 停止新提交；取消不被吞掉；多个尝试的成本求和且不重复计；部分 usage 缺失显式记录；失败路径也能完整写 execution 证据。
- **交付**：稳定的错误分类和案例执行记录；纯离线/回环/subprocess 测试证明同一预算边界，不运行真实服务。

### M05：模型阶段合同

- **输入**：`judge.py/recaller.py/step_tracer.py/defense.py/semantics.py` 及阶段 parser、投票与 repair。
- **实施**：梳理每阶段输入/输出、引用、quote、投票样本、quorum 和修复次数；公共低层验证复用，但 Judge 指控、Recall 候选、Upgrade 核验、Tracer 定位和 Defense 证词保持各自语义。
- **清理**：删除相同的引用枚举和 forwarding；近似的 positive 构造先定义 HearingTarget 的允许字段再复用。禁止把 Judge correct_result/判定透传 Recall hearing，禁止为了简化删除 Defense 支持确认步的证据约束。
- **验收**：tie/no_quorum/缺行/非法步号/未知引用/quote 不成立、多个候选和修复预算；解析同一 live/frozen 输入得相同结果；模型文本不能补造证据。
- **交付**：各阶段 parser 和共享合同的明确边界；规范化后的升级行/候选组装接口供 M06 使用。

### M06：API、听证和冻结回放

- **输入**：`api.py`、`adjudication.py`、`result.py` 和 `LiveDetectorBackend._frozen_stages`。
- **实施**：先修 B01/B02：所有 parser 收到同一 snapshot；升级列表唯一从验证行派生。将冻结验证/阶段输入 identity 提取到明确的 replay 职责；逐听证目标和上下文拥有 digest，禁止按 candidate_id 单独复用。
- **重构**：API 只保留阶段顺序和业务分支；听证和纯规范化组装委派；Adjudication 保留最终权限。回放 reach/是否可复用统一定义，不能在 detector mask 表与 evolution graph 各写一份相冲突规则。
- **验收**：同 snapshot 合法 E1 可回放；伪造升级列表拒绝或规范化重建；新增听证不复用另一个 Tracer/Defense；上下文/证据/route/prompt/目标不同重跑对应阶段；首 hearing 镜像冲突和实际采纳步可追踪。
- **交付**：API 全路径 Fake provider 回归，离线完成正/负/翻案/多候选/未决流程。旧缺身份信封只能由必要的离线读者解释，不能参与新运行复用。

### M07：干预与执行器

- **输入**：`interventions.py`、semantic executor、sandboxed_component dispatch、predicate DSL 与 Runner 能力绑定。
- **实施**：修 B12；executor identity、返回对象、verdict 和引用合同一处验证；引用必须存在且在声明 scope/投影内，空引用不能默认填满窗口。将窗口可见性、实际依赖闭包和语义 applicability 区分。
- **重构**：按合同校验、计划编译、runner 执行三个既有职责渐进拆分；统一 scoped/unscoped 重复验证。保留每种 runner 的真实能力/权限边界，不新增通用插件框架。
- **验收**：错工件摘要、executor 缺失、越界 refs、空 refs、闭包未知、DSL 限额、沙箱超时/内存、不同 scope；任何模型或沙箱审计输出保持 evidence-only。
- **交付**：实际可调用的干预合同和 scope 状态，R01/R04 的重复及伪闭包说明核销。

### M08：批处理、恢复与报告

- **输入**：`runner.py/measurement.py/evaluation.py`、原子写、single writer 和 `experiments/report.py`。
- **实施**：修 B05/B07 和 B06 的落盘部分。运行恢复核验源码/配置及逐例内容；attempt journal 保留真实重试及成本。requested、returned、not_started、missing、authoritative、unresolved 分别定义且集合守恒。
- **重构**：统一案例/run ID containment、原子写和 writer lock；用不可变 attempt envelope 一次提交结果、耗时和输入摘要，报告读取锁内固定的提交清单。区分 invocation_cost、case_attempt_cost、run_lifetime_cost，缓存命中本次成本为零，peak_requests 显式传递；从真实状态生成汇总，不复制健康/usage 规则。
- **验收**：空 rows/缺行/重复 case/路径碰撞及逃逸、部分写入、损坏 JSON、同 run_id 输入变化、并发写者拒绝、恢复同输入成功；completed 非负且不超过 requested；执行用量不重复、不漏早期请求；提交与 elapsed 属同一 attempt，中断后已提交结果不标为未开始。
- **交付**：隔离临时目录中的 batch/resume/report 入口回归，历史 artifacts 不参与测试也不被覆盖。

### M09：风险画像和候选排序

- **输入**：规范 packet、明确任务合同、适用 snapshot、机械谓词和新增因果合同。
- **实施**：新增 `StepRiskProfile v1`、纯信号提取和候选选择。首个可编码策略按核验偏差、绑定字段冲突候选、动作前提、无来源声明排序；同层按证据完整性和原位置稳定排序。版本/参数入 identity，不称幻觉概率。
- **合同**：每事件记录 producer、signal、verification_status、refs、score_status；区分 scored/unknown/unsupported/truncated。轨迹级 missing action 若没有引入事件，保留 null locus，不能摊给全部步骤。Top-K 与 mandatory/祖先增强候选分别记录。
- **验收**：缺失≠零风险、未知 role、工具观察不归责、同名数字/不同单位不自动冲突；mandatory 不静默遗漏，预算容不下则显式未知；无 labels/provider 也能得到确定性排序。
- **交付**：`causal_contracts.py/step_uncertainty.py` 实际调用和离线覆盖；PPL capability unsupported 路径有合同测试。

### M10：实际依赖与闭包

- **输入**：M02 的事件/交互及 M09 候选；`causal_graph.py`。
- **实施**：structural、字段/实体来源、实际消费、纠正/反证的边分别带 producer/ref/status。词面相同和模型解释只产生 hypothesis；同事件 arguments 不能消费后来 observation。
- **投影**：非连续上游依赖、真实后缀和交付锚点组成有界投影；记录节点/边/深度/字符预算、未解析依赖、遗漏和闭包状态。图内容和策略摘要独立于局部窗口 ID。
- **验收**：远距离前因、分支/汇合、多错误、独立 role=tool、缺 source、循环/异常结构、数量/单位/时间不匹配、纠正后重新使用旧值；闭包未知不标 complete，投影只读。
- **交付**：结构可解释的图/闭包纯函数，避免新建未经验证的全语言因果推理器。任意自然语言关系无法确定时降级，而非实现假因果。

### M11：有界审计和 shadow

- **输入**：M09 画像、M10 投影、HearingTarget 和统一预算；`causal_audit.py`。
- **实施**：批次信封包含多个候选，标 introduction/propagation/delivery/corrected/unknown 和 supported/refuted/inconclusive。supported 要有可核验过程和来源；合法引用或模型自述不能单独满足。
- **执行**：先用确定性算术/字段/合同核验，再通过现有 provider 接口提供可选慢审；测试只注入 Fake。shadow 在基础终态证明生成后使用剩余预算，不争抢基础阶段额度。真实后缀只证明观察到的消费；局部算术修正只证明局部结果，禁止伪造工具 replay 或状态变化。
- **验收**：schema/字段上限、相矛盾来源、材料性不足、假说不能自动 supported、预算跨多听证累计、deadline/fatal。可恢复 shadow 失败/诊断预算不足仅影响 audit health；fatal/用户取消仍传播原全运行中止合同，保留基础终态证明并如实记录执行停止；off 无额外调用，新诊断与必需 stage 健康分离。
- **交付**：生产入口可达的完整 shadow 模式，旧业务字段与同次基础执行一致；新增成本与模式另记，无任何真实模型请求。

### M12：定位与阴性反馈接线

- **输入**：`run_hearing/run_step_tracer/run_recall_scan`、审计和统一 replay policy。
- **定位**：传递 bounded causal_context，限定同一 claim，候选可拒绝；缺证据回退/未决必须在剩余预算内。Tracer 现有引用只接受 packet IDs；外部 E# 如果要消费，明确扩展 snapshot 合同，不混入旧 parser。
- **阴性**：具体可核查异常、策略许可及预算决定是否触发。Scanner 同时收到偏差/来源上下文，仍经 Upgrade/独立听证/Adjudication；`run_recall_net` 只运行一次。生产无校准记录保持未激活，不能自填经验门限。
- **验收**：阳性同指控首次引入/已纠正/第二错误；无新证据/无有效扩展策略的强一致阴性不额外触发，有效异常证据且策略允许可重开；高风险无证据不定罪，触发后完整链，无扩展策略/预算保留基础路径及现有适用 snapshot 触发；新增上下文使对应阶段重跑。
- **交付**：所有新路径有生产调用方和可复现 Fake provider 端到端测试。Defense 的新增证据输入如实施，单独测试它的输入身份和既有独立复核，不改变定罪权。

### M13：Backend、缓存与 harness

- **输入**：`backends.py`、`harness.py`、`checkpoint.py`、`config.py` 和真实/Mock/Caching backend。
- **实施**：共享 M01/M06 的 identity/replay 定义；baseline memo、跨周期缓存、frozen stages、fresh evaluation 明确核验真实输入。新 detector 策略配置与候选 hook 是不同变更来源，都产生明确 rerun reach。
- **清理**：移除重复 digest、replay graph 和 masked-stage 判断；保留 domain namespace 的作用。将“fresh”与“重跑哪些阶段”合为可推导而非相冲突规则；未知修改保守拒绝复用。
- **验收**：scorer/策略/证据/route/源码/候选变化正确 hit/miss；缓存控制臂与冻结 baseline 一致；未决不成为永久缓存；harness/checkpoint 不接受遗漏必要身份；记录新上下文成本。
- **交付**：真实 backend 逻辑由 Fake detector 驱动的入口回归；Mock 不能冒充 live 测量，旧报告不能作为新源码结果。

### M14：失败族、机制和提案

- **输入**：`worksheet.py/mechanisms.py/capability_planning.py/proposer.py`、`engine._all_families/_development_drafter`。
- **实施**：开发侧 `CausalFailureRecord` 分类 missed_detection/false_positive/late/early/ambiguous/evidence_missing/runtime_unmeasurable。将同 claim 首次引入的核验证书接进 viability；仅晚于 gold 或仅模型意见继续 needs_review。
- **重构**：preview/cycle 共用 family/diagnosis preparation；机制 key 使用偏差与修复操作，不含 case/step 字面量。prompt 是显式投影，诊断字段必须进入真实 LLM/Hybrid proposer 输入。修订带完整父程序、别名 target/normal、逐例反证和实际损伤。
- **验收**：证书成立/不成立、多错误、已纠正、无观察、未知能力、父版本修订；gold/G/validation 不进入部署端或提案的目标答案；prompt 快照验证确实含机制证据，未测不变永久负反馈；R09 边界按证据定义，修复路线后能力规划解除旧压制。
- **交付**：使用已有干预类型/hook 的实际提案与反馈，不实现任意 scorer 代码热更新；机制→能力→开发链可离线走通。

### M15：开发、覆盖、指标和四门

- **输入**：`development.py/metrics.py/gates.py/promotion.py/promotion_alignment.py/candidate_versions.py`。
- **先修根因**：B10 先验证两臂完整 expected set、唯一 case ID、合法 gold 和健康；B11 定位回退以原本精确命中被损失为准，unchanged 不混为 regression。B09 先核销再删除两个重复/未使用定义。
- **重构**：配对对齐、expected coverage、健康状态和定位读值复用唯一合同；M15 维护 outcome 真源，明确已有 detection/localization/joint/resolution 及 delta basis，M14/M16 不再造读值规则；修 B14 时缺少相应目标读值归未测。保护、行为、收益各司其职，已有 resolution 读值/门语义保留。冻结、晋升身份与退出终态不能弱化不可测/缺覆盖判定。
- **验收**：双臂同时缺邻居、单臂缺行、重复/多余 case、wrong→same/wrong→different/wrong→gold/gold→wrong、TP 丢失与权威退化；resolution 恢复/损伤/缺测及各目标类型转换；结构失败不执行候选、未触发不报改善、保护损伤挡准入；正式门全用固定合成 rows。
- **交付**：开发→冻结→四门→晋升或拒绝/不可测完整离线证据。保留既有统计口径；新增读数单独命名，不能重写历史分母。

### M16：周期、状态和经验生命周期

- **输入**：`engine.py/registry.py/checkpoint.py/ledger.py/memory.py/archive.py/gaps.py/route_failures.py`、M14/M15 服务。
- **重构**：按家族准备、开发适配、arm/门编排、周期状态与治理职责逐个提取，消除 preview/cycle 与修订路径重复；引擎保留周期顺序，不重写成新框架。registry/ledger/checkpoint 的原子性、锁和快照规则由现有模块承担。
- **生命周期**：候选未测/未触发/被反驳/保护损伤/通过分开写经验；M16 在 `route_failures.py` 修 B13 reducer，resolution 关闭当前 epoch，新 observation 可重开，历史不删除。独立观察绑定候选/测量/上下文 identity；缺身份旧记录保留为诊断，不累计为新运行独立证据，也不回写补造身份。仅合法准入更新 active；下一轮继承正确版本；拒绝和恢复不得污染身份或重抽正式集合。
- **验收**：Fake backend 固定阶段、case set、candidate identity、写入顺序和终态；中途停止/resume、同周期回滚、同秒快照、嵌套对象修改、坏 ledger、active/state 不一致；observation→insufficient→resolution→clear→新观察、幂等、冲突顺序、旧 observation 混合新 resolution、跨 cycle 重复测量和不同候选复用同 measurement；定位 harm 不写为不足。
- **交付**：M14/M15 服务成为真实调用方，重复路径删除；只声称代码闭环，不声称跨轮模型收益。

### M17：工具、CLI、打包与最终审查

- **输入**：`experiments/*`、`scripts/*`、三个包 CLI、`pyproject.toml`、README 和技术合同。
- **实施**：修 B08：plan arm→registry 解析唯一；逐臂 effective detector policy 支持离线构造。measurement 校验 schema、案例唯一/碰撞/排除集合、digest；子集使用独立选择摘要和计数并保留父身份。明确安装后协议资源/路径，仓库外 wheel 的 help/解析与 Fake 执行测试；命令层不以 help=0 代替功能验证。
- **清理**：脚本分为离线验证、配置/协议管理、历史分析、真实调用；同职责脚本共享实现。有实际 CLI、包装脚本、配置或文档调用的文件不因未 import 删除。历史日志和原始报告不当作新代码测试夹具。
- **验收**：全套已选择离线测试、导入/重复定义/权限/引用边界、安装包入口、调用一致性、Pristine 扫描核销；检查点和当前技术文档与实际代码一致。输出未运行的平台/环境项，不将它们写成通过。
- **交付**：所有工作包已验收或有明确未关闭阻塞；无遗留双实现/临时目录/无调用方模块/虚构收益；本台账当前状态准确。

## 8. 新因果接口和配置合同

### 8.1 最小接口

以下为当前生产接口，已核对真实定义与调用；版本化字段由§8.2及生产验证器负责。完整验收状态见§10.3，不把函数存在当作整包验收。

```python
validate_causal_config(raw, *, base_dir) -> dict
build_execution_identity(*, source, config, record, packet, evidence=None,
                         registry=None, replay=None, authority=None) -> dict
build_step_risk_profile(packet, *, policy, context=None, snapshot=None) -> dict
select_causal_candidates(profile, *, mandatory_event_ids, policy) -> dict
build_observed_dependencies(packet, *, policy) -> dict
project_dependency_closure(packet, *, candidates, graph, policy) -> dict
run_causal_audit(*, packet, profile, projection, target=None,
                 snapshot=None, context=None, config, budget) -> tuple[dict, dict]
context_for_hearing(audit, *, positive, packet, policy) -> dict | None
causal_recall_decision(audit, *, judge_result, policy, budget,
                      offline_policy=None) -> dict
```

公用输入引用/摘要使用既有 packet/evidence 规则。`supported` 候选的验证器、作用范围和证据必须可重放；图的确定性结构可 verified，未核验语义边只 hypothesis。模型预测的后续行为不标为执行验证。

### 8.2 合同字段

| 合同 | 必需字段及边界 |
|---|---|
| StepRiskProfile v1 | packet/scorer/policy digest；逐 event 类型、信号、状态、refs；pure Top-K 与 mandatory 分开；health/cost |
| DependencyGraph v2 | packet/policy digest；节点/边、关系、来源和验证状态，复制边的字段绑定/值/精确路径；未解析依赖、预算遗漏、完整性 |
| CausalProjection v1 | packet/graph/policy digest；目标、选中内容、交付锚点、遗漏 ID、闭包状态与具体原因 |
| CausalAudit v1 | packet/策略/投影/快照/听证 identity；候选偏差、角色、来源、claim_status；counterfactual kind/status/assumptions/verifier_refs；scope/health/cost |
| CausalFailureRecord v1 | 开发侧故障分类、观测偏差、核验证书、传播/反证、正常邻居、父版本、下一修复操作；不得原样回传 gold |

所有枚举闭合，列表/文本/节点/边有界。引用合法不等于断言真实；更早不等于更正确；已出现错误不等于最终交付违规。工具观察缺失或独立来源冲突不能由模型常识填补。

### 8.3 配置建议

```json
{
  "causal_audit": {
    "mode": "off",
    "score_source": "semantic_proxy",
    "policy_version": "causal-v1",
    "top_k": 3,
    "max_projection_events": 16,
    "max_graph_edges": 64,
    "max_dependency_depth": 8,
    "max_added_provider_requests_per_case": 1,
    "max_added_prompt_tokens_per_case": 6000,
    "max_added_output_tokens_per_case": 1500,
    "max_added_seconds_per_case": 30,
    "counterfactual_mode": "deterministic_only",
    "negative_feedback_enabled": false,
    "negative_feedback_policy": null
  }
}
```

这些数值是代码测试默认值，不是经实验确定的最佳参数。请求、token 和 deadline 预算覆盖每例全部候选/听证/repair/retry。tokenizer 不可用时说明输入预算是字符保守估算并记录实际 usage，不能承诺精确 token hard cap；最终响应由现有 provider max_tokens 限制。

## 9. 顺序、审查与删除方法

### 9.1 实施波次

| 波次 | 工作 | 进入下一波的代码门 |
|---|---|---|
| W0 | M00；B09–B11、B13/B14 最小修复可提前 | 离线边界及缺陷回归可执行；reducer/outcome 各有唯一真源 |
| W1 | M01–M04：身份、输入、证据、运行根因 | 引用/投影/成本/异常合同无已知阻断 |
| W2 | M05–M08 与对应 backend 前置：阶段、听证、干预、batch | 现生产流和恢复回放的离线端到端通过 |
| W3 | M09–M12：新风险、图、审计、定位与阴性接口 | 新生产分支离线接通；默认关闭；权限和预算完整 |
| W4 | M13–M16：演化缓存、机制、开发门与周期 | 固定 Fake 多轮生命周期、覆盖和保护门通过 |
| W5 | M17：工具/安装/全局审查 | 代码交付验收完成，实验仍未执行 |

同一文件只允许一个实施者写入。独立模块可以并行，合同拥有者先公布已确定接口；审查者使用固定 diff/源码身份，独立查反例，不仅认可实施者的成功用例。

### 9.2 每个子任务的执行闭环

1. **定位**：读取真源及调用方，确认输入、权限和期望行为，找最强反例；先写最小失败回归。
2. **实现**：修源头定义或提取一个职责，迁移全部消费者；新功能同包接通正式入口。
3. **清理**：核销重复函数、shim、旧字段、误导注释和测试替身；不可借机删必要校验/业务分支。
4. **验证与审查**：跑受影响 tests 和实际入口回归；独立检查错误/缺测/预算/身份/信息隔离，关闭意见。
5. **登记**：更新任务状态、缺陷、证据和检查点，再开始下一项。通过只表示已执行检查的结论。

### 9.3 删除判定

| 对象 | 删除前必要核销 |
|---|---|
| 重复函数/规则 | AST 定义、全部导入/调用、测试与配置动态消费者；证明替代语义同一 |
| 模块 | 公共导出、CLI、`python -m`、包资源、registry 字符串入口和外部契约 |
| 字段/参数 | 生产者、读者、冻结/缓存/结果合同与文档；如已存在真实输入需求，明确保留原因 |
| 脚本 | README/自动化/包装脚本/测试/配置调用；历史分析功能是否仍必要 |
| 文档/产物 | 当前真源替代是否已完成；原始数据、测量、ledger 与协议不能按“旧”删除 |

无调用方的新辅助模块不准验收。代码历史由 Git 保存；临时验证目录必须有明确所有者和生命周期，安全清理只限本任务创建的准确路径。

## 10. 离线验证体系与命令

### 10.1 验证层次

| 层次 | 覆盖 | 对应证据 |
|---|---|---|
| 静态 | Python AST、重复定义/导入、调用边界、Pristine 文本和真源核销 | 文件/符号/调用记录和核销结论 |
| 合同 | config、IDs、refs、schema、identity、scope、预算、覆盖、终态 | 参数化测试；失败/未知/恶意输入 |
| 入口回归 | Detector、batch/resume、CLI、backend、L1/四门、周期/恢复 | 固定 Fake 调用顺序、案例集合、信封和写入事件 |
| 平台/安装 | 回环 HTTP、worker subprocess、锁、沙箱、wheel 资源 | 隔离临时路径的退出码和平台限制 |
| 全局审查 | 多余实现、跨模块权限/真源、未关闭 bug、文档与代码 | 独立审查和最终台账 |

已有测试起点：`detector/tests/test_api.py/test_packet.py/test_scoped_semantic_projection.py/test_terminal_authority.py/test_voting_hearings.py/test_provider.py/test_runtime.py`，`evolution/tests/test_backends.py/test_development.py/test_localization_objective.py/test_measurement_integrity.py/test_promotion_integrity.py/test_checkpoint.py` 和根目录跨包测试。实施时按受影响路径选择，不能只测新辅助函数。

M00 已将测试显式归为 `code_only`（合成/临时夹具、无真实数据）、`local_data`（只读原始数据/证据核验）和 `platform`（锁、worker、沙箱及安装环境）并注册标记。完整集合仍用于对应环境回归；不能用忽略失败代替分组。纯代码组拒绝访问仓库原始数据及历史 artifacts，子进程继承回环网络边界。验证入口：

```powershell
python -m pytest -q -p no:cacheprovider -m code_only detector/tests/test_packet.py detector/tests/test_api.py
python -m pytest -q -p no:cacheprovider -m code_only evolution/tests/test_development.py evolution/tests/test_backends.py
python -m pytest -q -p no:cacheprovider -m code_only detector/tests evolution/tests tests
python -m detector --help
python -m evolution --help
python -m experiments --help
```

`python scripts/verify.py --scope code_only` 为默认纯代码入口；`--scope platform` 执行平台组，`--scope local_data` 执行只读本地数据核验及依赖本地数据的 Mock CLI 验证，`--scope all` 要求全部前置。报告写入 `artifacts/code_quality/<source_sha256>/<run_id>/`，记录命令、退出码、源码、环境和未运行分组。

模块涉及的平台项单独执行并记录前置能力；数据/证据完整性项只有相关本地输入存在时运行。无筛选的全套 pytest 仅在全部所需数据/平台前置满足且禁公网护栏生效后执行。必要的平台验证受阻时模块保持待验收/阻塞，不因纯代码组通过就声称整包完成。

`scripts/verify.py` 已分离纯代码、本地数据和平台验证，默认不要求 raw 数据，不覆盖 `artifacts/latest`；原有 Mock/数据核验功能在显式本地数据范围保留。

真实 `detector detect/batch`、`evolution experiment`、`experiments baseline`、live smoke、noise-floor、paired endpoint、研究轮及真实预检的调用不得在本期执行。CLI 功能测试只能使用明确 Fake/回环配置，不能落到默认真实 endpoint。

### 10.2 模块验收门

一个模块只有同时满足以下五项才改为已验收：

1. 任务列明的业务/新功能从正式入口可达，已修根因的回归及相关反例通过。
2. 输入、权限、引用、预算、并发、错误和身份等相关合同没有降低。
3. 替代实现的原消费者已迁移，旧实现/重复字段/无意义 shim/临时文件已核销。
4. 独立审查已处理，证据包含实际测试范围、命令、结果、源码/提交与剩余限制。
5. 本台账与技术合同只报告实际完成；没有实验也不要求实验收益作为代码门。

### 10.3 当前固定源码验收映射

源码`35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52`；完整2113项纯代码、58项平台及AST80/三CLI/源码不变通过。B42运行输入漂移与B43成本来源丢失已修复。M01–M17共108个条件已重绑定，其中107个为当前功能限定自检，M17最终清理/独立审查/18包验收门未完成；隐式校准按实际首次消费绑定，未消费时不加载。子集重叠不可相加，限定自检不等同每包完整验收。
机器索引：[当前18包执行子集](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/acceptance-inventory.json)；[107项功能自检及未完成最终门](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/task-card-review.json)；[纯代码](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/summary.json)、[平台](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/cf3533c6a15047909636604234cf0497/summary.json)、[周期输入核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/5263de900c824f55980d4880e570bd0f/cycle-input-verification.json)。之前缓存probe及服务入口核对保留在Git和原报告，本轮实际源码与JUnit重新绑定。

| 包 | 当前真源 | 已执行子集（纯代码/平台） | 剩余验收 |
|---|---|---:|---|
| M00 | [verify.py](scripts/verify.py)、[source_audit.py](scripts/source_audit.py)、[conftest.py](conftest.py) | 34/2 | 已验收基线；最终全局扫描和审查仍覆盖 |
| M01 | [config.py](detector/config.py)、[identity.py](detector/identity.py) | 107/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M02 | [packet.py](detector/packet.py) | 52/1 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M03 | [evidence.py](detector/evidence.py)、[predicate_context.py](detector/predicate_context.py)、[predicate_dsl.py](detector/predicate_dsl.py)、[conviction_calibration.py](detector/conviction_calibration.py) | 129/0 | 7项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M04 | [provider.py](detector/provider.py)、[provider_worker.py](detector/provider_worker.py)、[runtime.py](detector/runtime.py) | 58/2 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M05 | [judge.py](detector/judge.py)、[recaller.py](detector/recaller.py)、[step_tracer.py](detector/step_tracer.py)、[defense.py](detector/defense.py) | 79/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M06 | [api.py](detector/api.py)、[hearing.py](detector/hearing.py)、[replay.py](detector/replay.py)、[adjudication.py](detector/adjudication.py) | 88/0 | 7项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M07 | [interventions.py](detector/interventions.py)、[sandbox.py](evolution/sandbox.py) | 113/42 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M08 | [runner.py](detector/runner.py)、[measurement.py](detector/measurement.py)、[evaluation.py](detector/evaluation.py) | 81/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M09 | [step_uncertainty.py](detector/step_uncertainty.py)、[causal_contracts.py](detector/causal_contracts.py) | 53/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M10 | [causal_graph.py](detector/causal_graph.py) | 75/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M11 | [causal_audit.py](detector/causal_audit.py) | 97/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M12 | [causal_task.py](detector/causal_task.py)、[hearing.py](detector/hearing.py)、[api.py](detector/api.py) | 101/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M13 | [backends.py](evolution/backends.py)、[harness.py](evolution/harness.py)、[checkpoint.py](evolution/checkpoint.py) | 79/0 | 6项当前功能条件已重绑定；B42已修复；最终消费者/清理及固定源码独立审查未完成 |
| M14 | [causal_failures.py](evolution/causal_failures.py)、[cycle_preparation.py](evolution/cycle_preparation.py)、[development_adapter.py](evolution/development_adapter.py)、[capability_planning.py](evolution/capability_planning.py)、[proposer.py](evolution/proposer.py) | 88/0 | 6项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M15 | [development.py](evolution/development.py)、[metrics.py](evolution/metrics.py)、[gates.py](evolution/gates.py)、[promotion.py](evolution/promotion.py)、[candidate_versions.py](evolution/candidate_versions.py) | 169/0 | 7项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M16 | [engine.py](evolution/engine.py)、[registry.py](evolution/registry.py)、[ledger.py](evolution/ledger.py)、[memory.py](evolution/memory.py)、[route_failures.py](evolution/route_failures.py) | 276/1 | 8项当前功能条件已映射；最终消费者/清理及固定源码独立审查未完成 |
| M17 | [__main__.py](experiments/__main__.py)、[runner.py](experiments/runner.py)、[reading.py](experiments/reading.py)、[pyproject.toml](pyproject.toml)、[freeze_measured_prefix.py](scripts/freeze_measured_prefix.py) | 89/6 | 6项当前功能条件已映射；最终验收门、最终消费者/清理及固定源码独立审查未完成 |

测试类和全部逐例结果见机器索引及原summary.json/pytest.xml；local_data和真实实验未运行，未记为通过。

清理证据：此前已执行的[删除/移动核销](artifacts/code_quality/5cc274185665ab5a6722ff4be57a0741e2aafe503579bec93764b4894f344285/pristine_7364d0706e934ed3921bac5b35f9fb46/cleanup-inventory.json)保留原报告；当前[21脚本用途/引用清单](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/script-cleanup-inventory.json)、[唯一历史方案归档](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/document-cleanup-verification.json)、[文档链接/历史字节](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/documentation-check.json)、[原始695文件校验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/preserved-dataset-verification.json)均已核验。当前固定扫描[报告](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/pristine_usage_source/summary.json)的220条命中中，97条测试/夹具、77条保护约束、46条业务合同或关键词误报；5条旧公开合同标记已核实真实消费者并清除失效命名/注释，[语义核销](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/public-contract-review.json)保留必要读者和预算规则。限定自检不替代固定源码独立复审及最终18包验收。

## 11. 进度更新及续接规则

每次实施就地更新 §0、§4、§6 和本节检查点；任务卡只在范围/合同真正变化时修改。验收报告在 artifacts，长期进度在本文件，代码在 Git，不复制整份历史日志。

| 字段 | 更新规则 |
|---|---|
| 状态 | 开始编辑改实施中；通过相关测试但缺审查改待审查；五项门全通过改已验收 |
| 证据 | 记录实际路径、退出码/测试结果、源码身份或提交、审查结论；没有提交时明确工作区状态 |
| 缺陷 | 复现→修复待验证→验证待审查→关闭；反驳的风险给证据，不静默删除 |
| 阻塞 | 写明缺少的具体输入/平台能力和可继续工作；环境项未测不是通过 |
| 范围变化 | 替换当前目标/依赖/估计，指向 Git 历史；不另写 v2/final 计划 |

四项检查点（就地覆盖）：

- **当前任务**：按本次用户请求继续完整18包重构，遵守Pristine；整体完成后核销冗余脚本/文档、整合doc并将组会材料置于子目录。
- **进度**：代码提交8faf8b0、工作区已清洁；B43成本来源丢失已修复待独立复审：固定Git基线20项反例失败；完整2113项纯代码、58项平台及AST80/三CLI/源码不变通过。共享来源规则贯通runner、成本汇总、实验报告和脚本导出，未知来源不宣称精确值或已证明下界；数字读数及未知请求数保留。220条扫描命中逐行核销，5条公开合同保留必要行为并清除失效注释/命名。旧架构方案已移入docs/design-archive，仅修正搬迁后的日志链接，历史内容保留。最终清理、独立审查及18包验收未完成。
- **下一步**：对固定代码提交8faf8b0开展分模块独立复审，逐项核验源码、测试和清理证据，处理发现后更新18包验收状态。
- **未定事项**：107项功能自检通过，最终18包/全局清理独立审查门未闭合；已对固定代码提交请求3个只读子代理授权，未收到答复，未启动代理。原数据、校准、报告及实验正文保留，不做真实模型或重测。

## 12. 本期最终完成与延期项

本期最终交付是已接通且离线验证的代码框架，18 个包的验收与必要缺陷关闭、全局审查及当前文档更新齐备。若任一必须项受阻，应明确列出并保留未完成状态，不用测试数量或代码行数包装完成。

| 延期事项 | 本期处理 |
|---|---|
| 真实模型准确率、定位、FN/FP、成本收益验证 | 保留测量接口；不执行，不报告收益 |
| 真实 PPL/本地 teacher-forced 模型后端 | 实现 unsupported 能力合同；不下载或训练模型 |
| 自动学习 scorer 权重/部署任意新审计代码 | 不扩大候选执行类型；未来需独立工件/权限合同 |
| 外部新轨迹、人工因果标注和独立泛化评估 | 保留研究设计引用，不启动采集或标注 |
| 新行为生产激活与真实多轮准入 | 配置默认关闭；本期只以 Fake 验证完整接口和门 |

当前验证证据见§6、§10.3及四项检查点；固定扫描器原样selftest通过，220条命中逐行核销（97测试/夹具、77保护约束、46业务合同/关键词误报）。5条公开合同的实际消费者已核实，必要读者与预算语义保留；此为限定自检，独立复审及完整18包最终验收未完成。[扫描](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/pristine_usage_source/summary.json)、[分类](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/pristine_usage_source/classification.json)、[公开合同](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/public-contract-review.json)。没有真实模型收益结论。 [脚本清单](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/script-cleanup-inventory.json)、[文档归档核验](artifacts/code_quality/35706f9fe1f8df50b069722445de2450ced671ab739d76ae9a76509758966c52/document-cleanup-verification.json)。
