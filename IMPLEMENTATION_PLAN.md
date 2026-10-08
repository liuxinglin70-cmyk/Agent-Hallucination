# 代码框架优化与因果审计实施台账

## 0. 当前任务与进度

| 项目 | 当前值 |
|---|---|
| 当前任务 | 逐模块重构现有框架、修复根因缺陷、实现疑惑度与因果审计功能；本期仅代码工作 |
| 计划基线 | `main@071080622818a5da0647036f0966619ef94b0a1e`；2026-10-08 |
| 本轮已完成 | M00/M02 已验收；B01/B02/B03/B04/B09 关闭；M01 配置/Detector/批次身份已审查；B05 关闭（7f91c3e）；B07 已关闭（9c40b5c），51 项回归、2 项 CLI 回环及独立 19 项/81 个覆盖分区通过；B10/B11 已关闭（9dbf358），87 项回归与独立 17 项通过；B14 outcome 真源已接入周期 |
| 代码实施 | 2/18 个工作包已验收；M01 配置/Detector/批次身份子任务已实现，backend/harness/checkpoint 继续实施 |
| 下一工作包 | M15 开发门覆盖和定位回退已实现，继续目标类型 outcome；M01 演化身份、M08 路径/运行记录待完成 |
| 实验状态 | 不执行；真实模型请求、基准测量、A/A/A/B、调参、自演化真实轮次均不在本期范围 |
| 更新时间 | 2026-10-08，Asia/Shanghai |

本文件是本轮工作的唯一计划、任务状态和续接入口。每次实施结束就地更新表格、缺陷状态和四项检查点，提交历史由 Git 保留，不另建 progress、日报或计划副本。

源材料：[研究设计](docs/uncertainty-guided-causal-audit-design-20261008.md)、[组会说明](docs/group-meeting-uncertainty-causal-feedback-20261008.md)、[当前实现合同](docs/technical-solution.md)。研究设计描述目标，当前实现合同描述已落地行为，本台账描述实施工作。实现完成后同步更新当前实现合同，避免把拟议接口写成已上线功能。

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
| B06 | `runner.py:147–153`、`api.py:348–373`：整例重试只返回最后一次 execution，早期请求与 token 遗失 | case 级统一 attempt journal 与成本归并，不覆盖早期尝试 | M04/M08 | 已复现，待修 |
| B07 | completed 错减缺失行导致负数；调度中断可能遗失已落盘案例 | run_summary.v3 从 expected/returned/started 集合定义 completed/not_started/missing；工作线程登记返回行，调度中断保留已提交案例；重复或多余行拒绝 | M08 | 关闭；9c40b5c，5 项最小反例修复后相关 51 项、2 项 CLI 回环及独立 19 项/81 个覆盖分区通过；review_offline_review 无阻断 |
| B08 | `experiments/__main__.py:159` 默认 g0；`runner.py:76–86` 仅直接识别 empty，默认入口报需 registry | arm 名称按 `ExperimentPlan.arms` 解析，一处处理角色和 registry | M17 | 已复现，待修 |
| B09 | AST 基线报告明确检出两个 `_step_kind_split`；全仓调用检索只有两个定义，无消费者 | 删除两个无消费者定义，保留实际 `_joint_kind_split` 真源 | M15 | 关闭；4000669，AST/1020 项回归及 review_4000669 核销 |
| B10 | `development.py:375–402,562–563`：双臂同时漏掉预期 neighbor，目标改善仍能通过 L1 | 配对评分前要求两臂恰好覆盖 target∪neighbor；缺测独立返回 | M15 | 关闭；9dbf358，最小回归修复前 8 失败/2 通过，修复后相关 87 项、独立 17 项通过；review_offline_review 无阻断 |
| B11 | `development.py:567–577`：gold step 已知时，wrong→same wrong 被计为 regression，即使另一 target 改善 | 回退按 baseline 原本命中→失去命中定义；检测/权威退化单独处理 | M15 | 关闭；9dbf358，最小回归修复前 8 失败/2 通过，修复后相关 87 项、独立 17 项通过；review_offline_review 无阻断 |
| B12 | `interventions.py:1655`：scoped semantic refs 未验证存在/范围；空 refs 自动填满窗口 | 统一 executor 输出合同，引用必须由生产者提供并验证，不补造来源 | M07 | 静态确认，待修 |
| B13 | `route_failures.py:325–327,405–427`：追加 resolution 后 reducer 跳过它，已解决路线仍 insufficient | 按顺序归约 observation/resolution，关闭当前观察 epoch，保留历史并允许后续重新开放 | M16 真源；M14 消费 | 已复现，待修 |
| B14 | `route_failures.py:152–229`、`engine.py:2273–2275`：只读检测 delta，定位 joint_broken=1 被记为无改善无损伤 | outcome 明确 target_kind 与 delta basis；定位/联合损伤不能写为路线能力不足 | M15 真源；M14/M16 消费 | 验证待审查；当前工作区，7 项反例修复前失败，target_outcome 拥有 detection/localization/joint/resolution 与 delta basis；周期按实际 benefit set 生成各成员 outcome，路线反馈只记可测且无改善/损伤；41 项针对性及 182 项扩展回归通过，AST 67 文件通过；待固定提交审查 |

B01–B08、B10/B11、B13/B14 的审查复现使用纯内存、AST 提取或 Fake 对象，无真实 provider、无历史测量写入。B12 当前仍受 evidence-only 权限约束，不能夸大为已证明直接越权定罪。

### 4.2 待验证风险与能力缺口

| ID | 观察 | 下一步与判定条件 | 所属包 |
|---|---|---|---|
| R01 | M02 已实现 event_window_projection.v2：内容/packet 摘要、独立数据、遗漏 ID/截断原因、交付锚点；后续活动仅名 has_subsequent_activity | 565f191 已核销（独立 39 项）；实际依赖闭包和已纠正证据由 M10/M11 实现 | M02/M07/M10 |
| R02 | M01 已实现文件/直接配置共用校验，保留 causal 块，拒绝未知键、负数、NaN、无穷及 bool 混作整数，无凭据解析不读 .env | 校验子任务 c58a147 独立 69 项核销；有效配置已进入 Detector/批次 execution identity，演化侧继续实施 | M01 |
| R03 | frozen hearing 没有因果上下文身份，新增配置不是已有 hook | 新功能接线前建立阶段真实输入摘要及统一 replay policy | M06/M12/M13 |
| R04 | 引用集合枚举、step→event、provider forwarding、executor 身份验证重复 | 证明职责相同后归一，保持阶段信息权限差异 | M02/M03/M05/M07 |
| R05 | `engine.py` 5532 行、`_run_cycle` 936 行；preview 与 cycle 准备流程相似 | 沿既有职责提取家族准备、开发适配和门编排；先用入口测试确认重复 | M14/M16 |
| R06 | `stable_localization_families` 默认仅自动接受结构不可能定位；普通晚判仍 needs_review | 引入有核验的同指控因果证书；多错误和仅模型意见继续待审查 | M14 |
| R07 | 包安装规则未包含默认协议资源；`experiments.plan` 从包上层读取 datasets | 仓库外 wheel/CLI 验证后决定显式资源参数或打包确实需要的默认协议 | M17 |
| R08 | 一些测试使用历史 artifacts；禁真实 provider fixture 只在 evolution 测试域 | 全套离线网络护栏；改成最小合成夹具，不能依赖既往运行输出 | M00/M17 |
| R09 | `HybridProposer._localization_candidates` 的交付步限制可能与首次引入目标冲突 | 用同一声明、已纠正与多错误反例审查 prompt，不强制迎合 gold | M14 |
| R10 | `dataset.py` 直接拼接 case 路径，case/run ID 的 containment 不完整 | 用临时目录核验绝对路径、../、Windows drive/UNC/保留名和碰撞，统一入口验证 | M02/M08/M17 |
| R11 | result/elapsed 分两次提交，报告在 writer lock 释放后读 rows；缓存历史 usage 与本次 elapsed=0 混算 | 以单一不可变 attempt envelope 和固定清单读报告；成本按 invocation/attempt/lifetime 分开 | M04/M08/M17 |
| R12 | measurement 未重算 digest；prefix 继承全量身份与 gold 计数 | 校验 schema/case set/digest，区分 parent_manifest_digest 与 selected_cases_digest 和子集统计 | M17 |
| R13 | 多文件 registry 提交、route observation 独立性、promotion 返回/落盘状态一致性未充分证明 | 故障注入与独立 observation 身份测试，按实际证据决定修复 | M14/M15/M16 |

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
  hearing.py               单指控目标规范化/听证编排（M06，确认职责后提取）
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
| M01 | 配置与执行身份真源 | M00 | 2–3 | 实施中 | config.validate_config 为文件/直接配置唯一入口，causal 块保留且默认 off；数字范围/未知键/工件缺失验证；c58a147 配置独立 69 项通过；identity.py 已供 packet/api/runner 共用，B05 三项反例修复后 detector/repository/offline/backend/env 共 469 通过、5 未选，AST 67 文件通过；演化身份迁移未完成 |
| M02 | Packet、投影、事件引用规范 | M00 | 2–3 | 已验收 | 565f191；detector 326、跨包 41、AST 66 文件通过；独立 review_offline_review 39 项通过，无阻断；window v2 技术合同已同步 |
| M03 | 外部证据、谓词与引用权限 | M02 | 1–2 | 未开始 | — |
| M04 | Provider、并发、预算、取消、请求记录 | M00 | 2–3 | 实施中 | B04 修复前 4 个反例失败，复用 bounded_map 后 runtime/voting/judge/provider 共 58 通过；成本与 case journal 尚未完成 |
| M05 | Judge/Recall/Tracer/Defense 阶段合同 | M03/M04 | 2–3 | 未开始 | — |
| M06 | API、听证、冻结验证与回放 | M01/M02/M03/M05 | 3–4 | 实施中 | B01/B02 源头修复；evidence_flow/api/recaller/voting/upgrade/backend/localization 共 69 通过、17 未选；完整阶段 identity 与 hearing 编排未完成 |
| M07 | 干预执行器和 scope 合同 | M02/M03/M04 | 2–3 | 未开始 | — |
| M08 | 批处理、存储、恢复、成本和分母 | M01/M04/M06 | 2–3 | 实施中 | B05 已关闭（7f91c3e）；B07 已关闭（9c40b5c），相关 51 项、2 项 CLI 回环及独立覆盖分区通过；B06 与路径/存储一致性继续实施 |
| M09 | 风险画像与候选选择 | M01/M02/M03/M07 | 2–3 | 未开始 | — |
| M10 | 实际依赖图与闭包 | M02/M03/M09 | 2–3 | 未开始 | — |
| M11 | 有界因果审计与 shadow | M04/M06/M09/M10 | 2–3 | 未开始 | — |
| M12 | 定位上下文及受控阴性反馈 | M05/M06/M11 | 3–4 | 未开始 | — |
| M13 | 演化 backend、缓存、replay 与 harness | M01/M06/M08/M12 | 2–3 | 未开始 | — |
| M14 | 因果失败族、机制、能力和提案 | M11/M12/M13 | 3–4 | 未开始 | — |
| M15 | 开发、指标、覆盖、四门和晋升 | M00；集成依赖 M13/M14 | 3–4 | 实施中 | B09 已关闭；B10/B11 已实现：confusion_rows 拥有 expected coverage，check_l1 将缺覆盖/非法读值/不健康判为未测，定位回退要求基线原本命中；9dbf358 相关 87 项、独立 17 项通过，B10/B11 关闭；B14 指标 outcome 已接入周期收益集合及路线反馈，182 项扩展回归与 AST 67 文件通过，待固定提交审查；完整门集成待完成 |
| M16 | 周期编排、状态、registry 与记忆 | M13/M14/M15 | 3–5 | 未开始 | — |
| M17 | CLI/打包/脚本、全局清理与交付审查 | M00–M16 | 2–3 | 未开始 | — |

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
- **验收**：E# 无 snapshot、引用未知来源、证据不适用、同源重复、相互冲突、超长/恶意 DSL、非法读 gold；未校准谓词及模型证据不能获得 terminal authority。
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

下列为待实现签名。实现时采用现有 Mapping/dict 或必要的 dataclass，不为形式引入大型 schema 框架。

```python
validate_causal_config(raw, *, base_dir) -> dict
build_execution_identity(inputs) -> dict
build_step_risk_profile(packet, *, policy, context=None, snapshot=None) -> dict
select_causal_candidates(profile, *, mandatory_event_ids, policy) -> dict
build_observed_dependencies(packet, *, policy) -> dict
project_dependency_closure(packet, *, candidates, graph, policy) -> dict
run_causal_audit(*, packet, profile, projection, target=None,
                 snapshot=None, config, budget) -> tuple[dict, dict]
context_for_hearing(audit, *, positive, packet) -> dict | None
causal_recall_decision(audit, *, judge_result, policy, budget) -> dict
```

公用输入引用/摘要使用既有 packet/evidence 规则。`supported` 候选的验证器、作用范围和证据必须可重放；图的确定性结构可 verified，未核验语义边只 hypothesis。模型预测的后续行为不标为执行验证。

### 8.2 合同字段

| 合同 | 必需字段及边界 |
|---|---|
| StepRiskProfile v1 | packet/scorer/policy digest；逐 event 类型、信号、状态、refs；pure Top-K 与 mandatory 分开；health/cost |
| DependencyGraph v1 | packet/policy digest；节点/边、关系、来源和验证状态；未解析依赖、预算遗漏、完整性 |
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

- **当前任务**：逐模块完成本期重构及因果接口；当前处理 M15 开发覆盖、定位回退及目标 outcome。
- **进度**：2/18 工作包已验收（M00/M02）；B01/B02/B03/B04/B05/B07/B09 已关闭。B07 9c40b5c 独立 19 项及 81 个覆盖分区通过。B10/B11 9dbf358 相关 87 项及独立 17 项通过，已关闭。B14 七项反例已修复，41 项针对性、182 项扩展回归及 AST 67 文件通过，待固定提交审查。M01/M04/M06/M08/M15 仍实施中。
- **下一步**：提交 B14 并有限独立审查；继续 B13 reducer；随后 M01 演化身份及 M08 路径/B06 请求记录。
- **未定事项**：M17 包资源分发与跨平台能力按实际入口验证；本期不运行模型实验。用户允许子代理审查，要求优先重构、避免扩大护栏和审查工程。

## 12. 本期最终完成与延期项

本期最终交付是已接通且离线验证的代码框架，18 个包的验收与必要缺陷关闭、全局审查及当前文档更新齐备。若任一必须项受阻，应明确列出并保留未完成状态，不用测试数量或代码行数包装完成。

| 延期事项 | 本期处理 |
|---|---|
| 真实模型准确率、定位、FN/FP、成本收益验证 | 保留测量接口；不执行，不报告收益 |
| 真实 PPL/本地 teacher-forced 模型后端 | 实现 unsupported 能力合同；不下载或训练模型 |
| 自动学习 scorer 权重/部署任意新审计代码 | 不扩大候选执行类型；未来需独立工件/权限合同 |
| 外部新轨迹、人工因果标注和独立泛化评估 | 保留研究设计引用，不启动采集或标注 |
| 新行为生产激活与真实多轮准入 | 配置默认关闭；本期只以 Fake 验证完整接口和门 |

当前验证证据见 §6 和四项检查点；固定 Pristine 扫描器已验证与上游固定提交文本相同，selftest 通过，4fcffecc/pristine_00bf223e 报告记录 209 个文本命中。关键词命中不是删除依据；历史读者、保护门键和约束参数保留，各模块中的失效说明及无调用定义继续核销。没有真实模型请求，没有改写历史原始证据。
