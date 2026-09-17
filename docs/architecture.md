# 代码架构与执行逻辑

## 模块边界

依赖方向为 `evolution → detector`。底座独立运行，不反向导入进化框架。数据集只在加载与评估层出现，原始标签不进入检测器输入。进化生成的候选只能经 `detector.interventions` 的 registry 合同进入检测路径。

| 职责 | 文件 | 主要输入与输出 |
|---|---|---|
| 检测公开入口与编排 | `detector/api.py` | question/history、registry → 检测结果 |
| 数据投影和证据包 | `detector/dataset.py`、`packet.py` | 原始轨迹 → 无标签输入、事件/工具/交付锚点 |
| 共享判断语义 | `detector/semantics.py` | MATERIALITY 法则及各阶段提示词 |
| 结果判断 | `detector/judge.py` | 完整轨迹 → 结果性幻觉判断 |
| 候选召回与因果升级 | `detector/recaller.py` | 阴性/被驳回案例 → 候选及因果升级判断 |
| 定位与辩护 | `detector/step_tracer.py`、`defense.py` | 听证候选 → 最早因果步、辩护结论 |
| 裁决与结果合同 | `detector/adjudication.py`、`result.py` | 各阶段证据 → 终态与 authority |
| 扩展执行 | `detector/interventions.py`、`predicates.py`、`predicate_dsl.py` | registry → 有界干预与执行证据 |
| 服务调用 | `detector/provider.py`、`provider_worker.py`、`config.py`、`env.py` | 路由、超时、重试、多票、调用审计 |
| 批量和评估 | `detector/runner.py`、`evaluation.py`、`cli.py` | 案例清单 → 运行目录与评估指标 |
| 进化编排 | `evolution/engine.py` | split、backend、proposer → cycle 报告 |
| 机制诊断与候选 | `evolution/worksheet.py`、`proposer.py` | 重复失效 → 失败族、DSL 接地信息与提案 |
| 门控与指标 | `evolution/gates.py`、`metrics.py` | 配对结果 → 准入证据 |
| 持久状态 | `evolution/registry.py`、`ledger.py` | 预提交、快照、哈希链、淘汰与回滚 |
| 数据与执行后端 | `evolution/splits.py`、`backends.py` | 开发/正式划分、live/mock/cached 执行 |
| 元评估与能力面 | `evolution/meta_eval.py`、`capability_surface.py` | 门控行为、缺陷审计、可执行能力清单 |
| H_t 清单与差异 | `evolution/harness.py` | 已部署版本 → 五组件清单、哈希、代际差异 |
| 能力缺口工单箱 | `evolution/gaps.py` | 不可执行失败族 → 去重后备清单与处理历史 |
| 三类经验存储 | `evolution/memory.py` | 情景/程序性/改进经验 → 有界检索结果 |

## 检测流程

1. `Detector.detect` 校验输入、构建 packet、加载并编译干预计划。未提供 registry 时使用空计划或显式配置的 registry。
2. Outcome Judge 读取完整轨迹，按共享 MATERIALITY 法则判断交付结果。Judge、Causal Upgrade 和 Defense 支持独立多票，具体路由和票数由配置控制。
3. Judge 阳性先进入听证；阴性若存在分歧、弃权或低置信则进入 Recall Scanner。一致高置信阴性通常直接结束，但匹配的 `recall_trigger` 可以开启召回。
4. Scanner 只提出候选，不直接定罪。Causal Upgrade 要求候选同时具备内容错误和结果因果性；通过的候选按最早步骤排序。
5. 每场听证独立执行 Step Tracer 和 Defense。Judge 阳性被有证据驳回后，可转召回网继续审理。某场有效听证确认错误即可形成阳性终态。
6. 确定性 adjudication 根据封闭状态和机械 guard 生成终态。辩护未决或故障不会自动翻案，而会降低 authority；Judge 失败会产生 unresolved。
7. `proof` 保留 Judge、召回、升级、各场听证、机械证据与部署触发记录；结果评估区分终态、降级判断和基础设施未决。

## 一轮进化

1. `EvolutionEngine` 加载配置、分区、活跃库、账本与后端，检查来源和缓存身份。
2. 在 patrol 上运行已部署版本（H_t），并同时运行裸底座作为参照臂，必要时复测与补采，寻找重复的机制性失效。`worksheet` 区分可修复的检测失效与基础设施问题。参照臂使失败分为 `new_failures`（准入引入的退化）、`persisting_failures`（常驻残余）与 `resolved_by_active`（已修复），写入 `patrol.failure_contrast`；registry 为空时两臂是同一次评估。`patrol_registry: bare_base` 可恢复旧的单臂行为。
3. 失败族诊断提供内置谓词命中情况、typed views、共同字面量、可行干预路由。`HybridProposer` 结合 LLM 草拟与受约束模板；历史拒绝反馈用于抑制原样重提，检索到的 H_t 记忆（已准入的程序性经验、开放能力缺口）作为草拟输入一并提供。
4. `structural_gate` 校验合同、去重、禁止案例 ID 泄漏；新 DSL 必须在失败族成员上实际执行并一致命中所声明的能力。
5. `harmlessness_gate` 在 sentinel 配对比较，不允许超出配置的新 FN、FP 或 unresolved；只用程序化结果与标签评审。
6. `behavior_gate` 验证干预实际触发且产生与声明一致的行为证据。
7. 边际门 `meets_m` 和 engine 中的组合选择在 gain 集计算净收益及新错误，处理联合候选、贪心子集和 leave-one-out 归因。
8. 合格组合经 registry 预提交与限额检查后写入活跃库；账本记录决策。后续治理按贡献淘汰与回滚，恢复一致的 registry/state。
9. 准入写盘之后记录 H_t：写入五组件清单并与上一代做逐组件差异，把本轮不可执行的失败族写入能力缺口清单，把本轮尝试写入三类经验存储。这些只描述与记忆已发生的这一代，不参与任何门的判定。

报告中的门字段为 `structural`、`harmlessness`、`behavior`、`m`；`m` 是 marginal 的数学简称，不是软件版本。

## 评估覆盖与终态分类

`marginal_cases` 统计「全对案例净增」，因此某方向的 claim 只有在控制臂于该方向**判错**的案例上才可能得分。这不是任何候选的属性，而是仪器属性（gain 集构成 + 底座行为），所以按轮报告：

```python
gain_opportunity(control_gain_rows)  # -> {"recall": n, "precision": m}
```

cycle 报告的 `instrument` 字段给出：`gain_opportunity`（每极性可修复案例数）、`unmeasurable_polarities`（机会为 0 的方向）、`terminal_classification`（每个候选的终态：`admitted` / `rejected` / `not_evaluated` / `unresolved`）、`terminal_counts`、`instrument_limited`。账本另有 `proposal_memory` 与 `proposal_memory_used` 事件记录本轮提供的记忆规模。

**`not_evaluated` 与 `rejected` 的含义不同，不能互相替代：** 前者表示声明方向在 gain 集上无案例可修，即「未曾被测量」，不得当作「已被证伪」；后者表示门控给出了实质理由（如造成新错误或健康回退）。只有 BEHAVIOR 与 M 可能因缺测量而不可评估——STRUCTURAL 与 HARMLESSNESS 依据合同与 sentinel 保护判定，与 gain 集极性无关。判别逻辑集中在 `gate_rejection_is_unevaluable` 与 `terminal_classification`，拒绝反馈、轮次报告与经验存储共用同一判据，避免三处各写一遍而彼此漂移。

本轮默认仍按原规则阻断准入；区分「未评估」只改变结论如何被读出与被记忆。

## 覆盖响应：把「不可测量」变成决策

读数本身不改变行为，因此新增 `coverage_response` 策略，在覆盖不足时给出动作。**默认 `strict`，与改动前行为完全一致**：claim 记为未测量、准入照旧阻断。另两种模式必须显式开启，且可按方向分别配置：

| 模式 | 机会为 0 时的行为 |
|---|---|
| `strict`（默认） | 在默认 gain 集上测量，记为 `not_evaluated`，准入阻断。行为与改动前逐字一致 |
| `replacement` | 若已安装替代评估集，则**整个判定单元在替代集上重新测量**；门限不放宽，只更换所运行的仪器 |
| `replacement_or_skip` | 若没有匹配覆盖的评估集，记为 `skipped_unmeasured` 并跳过，而不是当作已被证伪 |

两处实现细节值得记录：

- **测量集在任何门运行之前决定。** 机会为 0 的召回类 claim 会先在 BEHAVIOR 的方向检查上失败，根本到不了 M；因此响应不能只在 M 处实现。单元先用默认 gain 计算机会，必要时改换整个测量集，再跑 HARMLESSNESS/BEHAVIOR/M。
- **替代集只驻留内存**（`set_replacement_gain_cases`）。持久化它需要与主 gain 集同等的 gold、协议与暴露保证，那是一个需要单独决定的问题；未安装时策略如实报告 `none_installed`，而不是假装发生过替换。

替代集可以改变准入结果——这正是它必须默认关闭、按方向开启、并把动作写进 `instrument.coverage_responses` 与账本的原因。

## H_t = (G, C, T, M, R)

每一代被表示为五个组件，`evolution/harness.py` 把它做成可装载、可核验的清单：

| 组件 | 含义 | 清单来源 |
|---|---|---|
| G | 工作流与状态转移 | 阶段顺序常量 + 实际入口点（导入失败即构建失败） |
| C | 上下文构造与压缩 | `detector.packet` 的视图预算与渲染器实测值 |
| T | 工具与技能实现 | 当前活跃 registry 的干预集合与哈希 |
| M | 可检索经验与技能记忆 | `evolution/memory.py` 三个存储的条目统计 |
| R | 路由、预算与停止策略 | 配置中的 patrol、budget、gates、library、split、proposer |

两条性质比完整性更重要：**组件是推导出来的，不是断言的**（阶段入口被重命名、上下文预算消失都会使构建失败，而不是记录一份过时描述）；**事后可核验**（每个组件对自己的载荷有摘要，清单对组件摘要再有摘要，装载时重新推导，被编辑过的清单会被发现而不是被信任）。

清单参与的流程只是报告：`record_harness` 在准入提交后写入，并记录逐组件差异（`changed` 与 `changed_keys`），使后续结果可以归因到真正变动的组件，而不是笼统的「系统」。清单与差异写入 cycle 报告与账本事件 `harness`；历史以 `harness_history.jsonl` 追加。

## 能力缺口清单与三类经验

**能力缺口清单（`evolution/gaps.py`）。** 可稳定复现、但当前动作词表无法处理的失败族，此前只写进当轮报告就被丢弃；现在落为后备项，保留 `reason` 与可行路由。身份是 `(failure_family, classification)`，并剥掉 worksheet 在按谓词画像切分时追加的 `-P<hash>` 后缀，因此同一个缺口只有一条记录、带 `occurrences`，而不是每轮一条。重复出现不重复追加；原因或路由变化才算新信息。解决通过 `resolve_gap` 记为历史而非删除，因此已处理的缺口仍可解释。记录缺口不放行也不拒绝任何候选。

**三类经验（`evolution/memory.py`）。** 情景经验（某轮具体尝试与各门结论）、程序性经验（只有**通过门控**的处理路线才写入，附预期行为与失败边界）、改进经验（哪类瓶颈对哪类补丁有反应）。两条规则防止退化为日志：没有真实内容就不写入；检索按机制过滤并设上限。检索结果显式区分 `falsified_intervention_ids` 与 `unevaluable_intervention_ids` —— 后者是「未曾被测量」，不允许当作「已被证伪」抑制同一机制。

**记忆如何进入下一轮。** `EvolutionEngine._proposal_memory_context` 为当前失败族检索已准入程序性经验，并附上开放能力缺口清单，交给 `LLMProposer.set_memory_context` 进入草拟提示。检索按族限量，因此提示词不随存储增长。供给与结果分别记入账本 `proposal_memory`、`proposal_memory_used`，使「系统用上了自己的经验」是可核对的说法；记忆作为先验输入参与草拟，但不参与任何门判定。

三个存储都是追加式 JSONL，带 schema 版本；`append_jsonl`/`read_jsonl` 由 `evolution/contracts.py` 共享，格式头部错误会显式报错而非被误读。重复写入按逻辑键去重，使被中断后 `--resume` 重放的轮次不会虚增计数或让存储描述一次并未发生的运行。

## 配对重放和 fresh 对照

`LiveDetectorBackend` 对可机械重放的候选冻结未受影响的阶段，听证按 `(source, candidate_id)` 匹配。缺失或被干预遮蔽的冻结条目会被拒绝。对改变模型调用或召回控制流的干预，`requires_fresh_evaluation` 选择 fresh 对照与 fresh 候选臂，避免把缓存结果和重新采样结果直接当成因果收益。

缓存身份包括代码摘要、配置摘要、输入与 registry 身份。mock 后端仅用于机械回归；真实模型能力应来自真实后端的独立测量。

## 运行与验证分层

`scripts/verify.py` 运行全部 pytest、本地 HTTP 子进程集成测试、命令帮助、能力面审计以及两个 mock smoke。测试数据、registry、账本和临时配置使用隔离目录；不覆盖日常真实运行状态。结果按源码逐文件摘要绑定到 `reports/latest/`，验证期间代码变化会使总检查失败。

`scripts/live_smoke.py` 独立使用真实 provider，生成两个小型合成案例，经公开 `batch` 和 `evaluate` 命令检查完整结果链。报告记录实际模型路由、超时、案例终态、authority、阶段状态、代码摘要及运行目录；不写入凭据。它没有运行 LLM proposer 或真实进化准入，不能证明自进化收益。

命令级结果也属于执行合同：`verify-ledger` 的退出码反映哈希链有效性；`smoke` 的退出码反映实际准入、审计、cycle 完成和账本有效性，而不是仅表示 Python 进程运行结束。
