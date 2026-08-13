# SEHD-RISE 技术设计

## 目的

SEHD-RISE（Repairability-driven Integrated Self-Evolution，可修复性驱动的一体化
自进化）把 PACA-TCL v3.5 证明法庭的裁决当作运行时权威，并围绕一条原则组织自进化：
**可修复性先于进化（repairability before evolution）**。因果修复层是对失败属主
（ownership）与可修复性的唯一权威；只有可修复的失败进入进化；L1/L2/Meta 技能共享
一份规范 RepairPlan 协议；每次修复尝试都由显式的因果归功链作为证据，并由五层结果
漏斗分类。

规范性的项目章程——每个 agent 都必须遵守的核心目标、设计理念与导师要求——是仓库
[README](../../README.md) 的「核心目标与设计纪律」一节；本文档只是把它落实为设计。
框架代码位于 `sehd_rise/`。重构前基线 `src/sehd_v6/` 已冻结、未被 git 跟踪、绝不
修改。

## 架构

### 层级与依赖规则

包按 13 层加一个根入口组织。依赖严格向下（`A → B` 表示 A 可以导入 B）；反向导入
被禁止，该规则由 `tools/check_layers.py` 机械强制：

```text
platform        ← 所有层都可以依赖它；不依赖 sehd_rise 内任何东西
skills          → platform
detection       → platform, skills
causal          → platform, skills, detection
experience      → platform, skills, causal
learning        → causal
typing          → platform, skills, causal, experience
planning        → platform, skills, causal, experience, typing
execution       → platform, skills, detection, causal
evaluation      → platform, skills, causal, execution
meta            → platform, causal, experience, evaluation
loop            → api/cli 之外的每一层
api, cli        → loop, detection, evaluation, planning
```

各层职责：

| 层 | 模块 | 职责 |
|---|---|---|
| `platform` | env / routes / models / usage / gold / jsonio | 与研究无关的基础设施：`.env` 加载、provider 路由解析、规范模型名、用量账本（usage ledger）、gold 归一化（仅评估侧）、规范 JSON / 稳定哈希 / 原子写入 |
| `skills` | genome / policy / rule / vocabulary/ | 可进化面的数据模型与封闭词表——纯数据，无流程逻辑。`vocabulary/` 是 L2 谓词/动作、L1 模块章程与法庭审计词表的唯一真源 |
| `detection` | packet / transport / court/ / policy / injection / detector / replicas / batch | 证明法庭检测：轨迹打包、provider 传输、v3.5 法庭引擎、确定性 L2 策略求值器、L1 post-court Skill 编译与执行、`detect_record` 入口、副本聚合、批量运行 |
| `causal` | signals / cut / episode / repairability / plan | 唯一的因果权威：检测→进化投影、终态因果切点、`CausalFailureEpisode`、`RepairabilityStatus` 分诊、规范 `RepairPlan` |
| `experience` | memory | 机制级经验记忆——单一 Experience Truth |
| `learning` | protocol | SEHD-v7 学习协议：五个一等类型（FailureObservation / RepairIntent / ExecutableCandidate / EvaluationEvidence / EvolutionMemory）+ CandidateArchive + 候选失败分类。纯数据投影，绝不重推修复语义或权威 |
| `typing` | classifier / guidance / slices | HTC-TES 咨询型类型智能：类型标注、检索/反思先验、类型化评估切片。无因果否决权 |
| `planning` | l1 / l2 / retrieval / candidates / search / feedback | 由 RepairPlan 驱动的假设搜索：L1 LLM 因果反思、L2 封闭词表策略规划、经验检索、重试反馈 |
| `execution` | action / executor / replay / trace | 修复执行与证据：`ActionSpec`、按 `RepairPlan.executor` 分发、带新鲜 child genome 的检测回放、`ExecutionTrace` |
| `evaluation` | effect / fitness / canary / gate / promotion | 因果效应记录、源 fitness、canary 选择/保留/驱逐、结构门 + 无冲突门、`PromotionDecision` |
| `meta` | strategy / review | 跨案例策略学习器：五域封闭策略词表、证据门控的提交、仅输出策略建议 |
| `loop` | machine / runner / state / records / persistence / ledger / probes / shadow / result | 统一编排：进化状态机、结果漏斗、案例队列与预算、检查点、GateEvent 账本、开发探针、shadow 批次提交 |
| `api` | detection / evolution / manifests | 公共门面——唯一允许做装配的层 |
| `cli` | evolve / detect | `python -m sehd_rise <evolve|detect>` 参数面 |

### 权威关系（一个事实，一个权威）

| 事实 | 唯一真源 | 其余 |
|---|---|---|
| 检测真相 | `detection/court/` 输出（终态依据） | `causal/signals.py` 是进入进化的唯一合法投影 |
| 失败真相 | `causal/episode.py`（`CausalFailureEpisode`） | attribution/classifier/episode 是同一处合并站点 |
| 修复真相 | `causal/plan.py`（`RepairPlan`） | planning/execution 永不重建修复语义 |
| 触发 / 可修复性 | `causal/repairability.py` | 取代旧的 `should_evolve` + `skip_unaddressable_failure` 双权威 |
| 执行真相 | `execution/trace.py`（`ExecutionTrace`） | |
| 效应真相 | `evaluation/effect.py`（`CausalEffectRecord`） | 边级反事实证据只在这里判定 |
| Fitness 真相 | `evaluation/promotion.py`（`PromotionDecision`） | |
| 经验真相 | `experience/memory.py` | `typing/` 只持有切片视图，绝不复制 |
| Meta 真相 | `meta/strategy.py`（`StrategyState`） | |
| 门控事件 | `loop/ledger.py`（`GateEvent`） | 唯一的投影权威 |

## 检测权威

检测路径为：

```text
trajectory record（原始轨迹记录）
  -> H0: OCSE packet + 分层基座通道 + construct census
  -> H1: qualification / propagation + focused prosecution 与 defense
  -> H2: 校准式 appeal + informed defense + 共识门
  -> H3: counterfactual localization
  -> verified_error | verified_clean | unresolved
```

证明法庭是三态裁决的真源。CRS 是因果分析使用的诊断投影，绝不能被当作独立裁决。
法庭不变量：

- 证书（certificate）必须使用可寻址的事件或交互引用；无效证书被隔离（quarantine），
  不能支撑 clean 或 appeal 裁决。
- `supported_or_correct` 受机制限制，且必须附相关证据。
- Appeal 阳性判定必须有可采信的独立初始支撑。
- 无效的反事实排序回退到确定性定位。
- `unresolved` 是一等终态；一个阴性 unresolved 案例绝不记为 decisive-clean 准确率。
- 形式化评分的案例必须 `runtime_valid=true`；基座层隔离结果被投影为机器可读的
  `authority_failure_reasons`（仅可观测性，绝不作为裁决输入）。

五维 CRS 诊断投影使用冻结权重：Factuality 0.35、Counterfactual 0.15、Recovery
0.20、Process 0.20、Scope 0.10。

## 因果修复层

因果层（`causal/`）是旧框架里相互竞争的多处权威被合并为单一权威的地方。

**终态因果切点**（`cut.py`）。根因定位找的是阻止正确终态出现的最小可修复原因，
而不只是第一个畸形产物。畸形/缺失产物、隔离、覆盖异常与 appeal 异常是构造切点的
证据，不是自动的属主。确定性失败分类器只回答一个问题——失败发生在哪一层、哪个
产物上——不调用 LLM、不使用启发式，按优先级顺序：

1. Proof-court 结果中的产物失败 → L1；属主按产物类型解析（candidate →
   `candidate_trigger`，错误证书 → `evidence_obligation`；defense 证书已无 L1 属主，
   归 detector 边界或 L2）。若缺少 error candidate，仍保留 `candidate_trigger` 因果属主，
   并可编译为受限的 `ClaimAnchor -> construct_candidate` RepairPlan；其他尚无注册构造
   能力的缺失产物仍进入 `CAPABILITY_GAP`。
2. 共识门挡下了一个非空候选集 → L2，`consensus_gate_policy`。
3. 空洞的完整覆盖（该侧声称 `complete` 却零有效证书）→ L2，`coverage_validity_policy`。
4. Boundary 路径返回 `verified_clean` 但终态是 `unresolved` → L2，
   `boundary_consumption_policy`。
5. 兜底：无可操作形状的 unresolved，或终态裁决与法庭自己的证书账本矛盾 → 不在
   技能可寻址范围内。

Gold 极性只确定需要的终态迁移方向，不改写已找到的因果属主。缺失 candidate 的假阴性
可进入 label-free constructive harness；缺失 certificate 等未注册构造能力仍保持原属主
并进入 `CAPABILITY_GAP`。假阳性只允许查询与 error→clean 方向相容的既有产物抑制能力。
Unresolved 终态不带极性。

**CausalFailureEpisode**（`episode.py`）。每个下游层都消费的单一失败对象：终态、
终态依据、阻塞谓词、因果失败边、失败属主、技能层、可修复面、合法修复操作、可观测
效应、可修复性状态。

**可修复性权威**（`repairability.py`）。唯一的进化触发器。每个被分析的失败恰好被
分诊为一种状态：

- `CAPABILITY_GAP` —— 已学到、归因清楚，但当前 Skill 注册表没有可表达的执行原语；
  记录 failure / owner / desired_transition / missing_capability，不伪造替代动作。
- `NON_EVOLVABLE` —— 非学习目标或稳定检测结果；绝不用于掩盖缺失能力。
- `REPAIRABLE` —— 至少存在一份可编译的合法 RepairPlan；只有这些进入进化。
- `UNADDRESSABLE_FRAMEWORK_DEFECT` —— 框架不变量违例：某个被声明在可进化面上、
  却不存在合法 RepairPlan 的失败。独立计数；绝不进入 no-edit 或收敛统计。
- `FRAMEWORK_DEFECT` —— 检测器边界来源的缺陷（validator / transport / route /
  config）。永久路由到框架侧；绝不产出技能，因此基因组永远不会学会为检测器 bug
  打补丁。

**规范 RepairPlan**（`plan.py`）。L1 与 L2 共享同一协议：

```text
RepairPlan
├── failure identity（失败身份）
├── layer                  # L1 | L2
├── owner（属主）
├── target causal edge（目标因果边）
├── executor               # l1_court_transform | l1_constructive_harness | l2_adjudication_policy
├── legal operations（合法操作）
├── target domain          # claim_anchor | candidate | certificate | origin | coverage | boundary | event
├── grounding query（实体绑定查询）
├── required binding schema（字段与基数契约）
├── effect contract（效应契约）
└── evaluation contract（评估契约）
```

L1 的既有实体修复经 `l1_court_transform` 执行七个确定性抑制/选择变换；缺失 candidate
只经 `l1_constructive_harness` 绑定一个确定性 `ClaimAnchor`、调用冻结 constructor，
再从 qualification 开始局部续跑。Skill 仍不能编写 evidence/certificate/verdict，续跑也
不能重新采样 census。L2 经
`l2_adjudication_policy`（确定性法庭求值器）执行。所有可提议操作由
`skills/capabilities.py::SKILL_CAPABILITY_REGISTRY` 唯一授权；诊断、计划、反思、
预验证、执行与 fitness 必须查询同一注册表。

## 双层可进化面

冻结基座永不改变：进化只改动 L1 规则基因组与 L2 策略基因组；CRS 权重、法庭决策
规则与阶段结构原封不动，任何阈值都不手调。

### L1 —— grounded Skill 基因组

六个技能模块拥有既有 proof-court 结果上的抑制/选择面；其中 `candidate_trigger` 另有唯一
受限的正向召回面：

| 模块 | 阶段 | 作用 |
|--------|-------|------|
| `candidate_trigger` | Constructive / Post-court | 从一个 `ClaimAnchor` 构造中性候选；或抑制已有候选 |
| `evidence_obligation` | Post-court | 保持已有 coverage 未完成 |
| `candidate_to_verifier_route` | Post-court | 暂停已有候选资格 |
| `atomic_verifier_instruction` | Post-court | 撤下已有证书 |
| `causal_link_test` | Post-court | 拒绝已有的非因果 origin |
| `earliest_origin_tiebreak` | Post-court | 在已有 origin 中选择/抑制 |

规则以类型化英文槽位存储（`skills/rule.py`）：

- `condition`：一个 proof-court 结果可观测情境（≤ 300 字符）；
- `action`：所属模块章程中的一个模块内封闭操作码（opcode），可选
  `"<opcode>: <target>"` 形式指名具体输出字段（≤ 300 字符）；自由文本动作以
  `action_opcode_unowned` 拒绝；
- `evidence_anchor`：供进化分诊使用的失败签名记账（≤ 240 字符）；永不渲染进
  运行时提示词，因此不会把案例身份泄露进检测。

规则文本只用于持久化和反思展示，确定性渲染为 `When <condition>, <action>.`；
它不注入 provider prompt，也不决定运行时动作。类型化 `condition_spec` 在已完成的
court result 或预先投影的 `ClaimAnchor` universe 上返回实体级 `Binding[]`，而不是对聚合结果返回布尔值：`all_of` 按同一
实体做 join，`any_of` 做去重 union，`not` 在 capability 声明的 domain 上做 anti-join。
只有 binding 的 domain、必需标识字段和 cardinality 全部满足注册表契约，执行器才会
派发操作；不得回退到 first-match，也不得把 action 的字段名当作实例 id。每个模块带
一份从能力注册表派生的
章程（`skills/vocabulary/charters.py`）：
流水线阶段、可影响面、禁止面、允许的裁决、在范围内与范围外的失败示例。只有被允许
的目标模块的章程与操作码列表进入反思提示词，在生成时就约束提案。运行时规则不得
编码数据集标签、案例特定标识符、内部诊断状态、禁止的直接裁决、不安全的跨模块引用
或基线变异。

### L2 —— 裁决策略基因组

封闭词表策略项：一个前置谓词（冻结的 `AdjudicationFacts` 快照上的纯函数）配一个
枚举动作，动作参数取自封闭词表。每个谓词、动作与参数 token 都由带版本的注册表
（`skills/vocabulary/registry.py`）拥有；反思提示词的词表表在调用时由该注册表生成，
每个被解析的提案都对着同一组封闭集合重新验证。

运行时语义由确定性求值器（`detection/policy.py`）拥有，经证明法庭的策略钩子机械
执行。如果提案模型在运行时也自己解释策略项，裁决就会依赖对自身产物的不可复现的
LLM 解读；确定性求值器正是让「产物→行为」耦合可审计的东西。钩子默认惰性——空策略
集就是基线，因此任何行为变化都必须被挣得。策略只作用于终态边；覆盖降级是单调的
（策略只能降低、绝不提升覆盖等级）；未知谓词、动作或参数失败即关闭进求值器的
`skipped` 列表。

该面对修正封闭：`add_item` / `modify_item` / `remove_item` 让错误或失效的策略保持
可修复或可消除。验证强制词表成员、基因组容量（12 项）、语义去重、索引边界、不可变
基线项与弱/强 fitness 保护；应用产生带父哈希谱系的新基因组。词表本身在约束下可
在线进化：新谓词或法庭本地动作实例先注册为 `pending`（仅 shadow），只通过证据路径
（`skills/vocabulary/evolution.py`）晋升为 `registered`。

## 进化循环

### 状态机

`loop/machine.py` 实现进化状态机：

```text
DETECTED
  -> CAUSAL_FAILURE_ANALYZED
       -> CAPABILITY_GAP | NON_EVOLVABLE | FRAMEWORK_DEFECT
                                                     （终态，framework 层）
       -> REPAIRABLE
  -> REPAIR_PLANNED
  -> HYPOTHESIS_SEARCH                            （NO_HYPOTHESIS：search 层）
  -> PROPOSAL_VALIDATED                           （INVALID_PROPOSAL / DUPLICATE_PROPOSAL）
  -> BOUND                                        （BINDING_NOT_FOUND / BINDING_AMBIGUOUS /
                                                    BINDING_DOMAIN_MISMATCH）
  -> CONSTRUCTOR_EXECUTED -> CANDIDATE_CONSTRUCTED（仅 constructive path；分层 construction outcome）
  -> EXECUTED                                     （ACTION_NOT_DISPATCHED / EXECUTION_RUNTIME_FAILURE）
  -> TARGET_EDGE_CHANGED                          （TARGET_EDGE_UNCHANGED）
  -> BLOCKER_CLEARED                              （BLOCKER_NOT_CLEARED）
  -> FITNESS_EVALUATED
       -> REJECTED （SOURCE_NOT_IMPROVED / CANARY_REGRESSION / TRANSFER_REGRESSION）
       -> ACCEPTED
  -> EXPERIENCE_LEARNED
  -> META_UPDATED
```

### 结果漏斗

每次修复尝试恰好终止于五层共 26 种终态之一——`loop/machine.py::Outcome`，彻底取代
旧的 `skip_*` 词表：

- **Framework**：`CAPABILITY_GAP`、`NON_EVOLVABLE`、`UNADDRESSABLE`、`ROUTING_FAILURE`、
  `SCHEMA_FAILURE`、`INFRA_FAILURE`
- **Search**：`NO_HYPOTHESIS`、`INVALID_PROPOSAL`、`DUPLICATE_PROPOSAL`
- **Grounding**（提案已通过验证但实体未绑定 / 构造未物化，发生在 executor 派发之前）：
  `BINDING_DOMAIN_MISMATCH`、`BINDING_NOT_FOUND`、`BINDING_AMBIGUOUS`、
  `CONSTRUCTION_NO_CANDIDATE`、`CONSTRUCTION_ANCHOR_MISMATCH`、
  `CONSTRUCTED_CANDIDATE_DUPLICATE`、`CONSTRUCTED_CANDIDATE_NOT_QUALIFIED`、
  `CONSTRUCTED_CANDIDATE_NOT_VERIFIED`、`CONSTRUCTED_CANDIDATE_NO_ERROR_CERTIFICATE`
- **Execution**：`ACTION_NOT_DISPATCHED`、`EXECUTION_RUNTIME_FAILURE`、
  `TARGET_EDGE_UNCHANGED`、`BLOCKER_NOT_CLEARED`
- **Fitness**：`SOURCE_NOT_IMPROVED`、`CANARY_REGRESSION`、`TRANSFER_REGRESSION`、
  `ACCEPTED`

每种终态在 `evolution_result.json` 中独立计数，因此真正的框架瓶颈总能从实验输出
中恢复出来，框架失败从不与学习拒绝混在一起；binding/construction 类失败按 grounding
层单独计数，不会再伪装成 executor 失败去误导执行层优化。

### 收敛

收敛窗口只消费**完整的修复尝试**：计划有效 → 提案有效 → 动作已执行 → 因果效应已
测量 → 无可接受改进（即 fitness 层拒绝）。基础设施故障、路由失败与无效提案是系统
可用性事件，不是学习饱和的证据，绝不推进收敛计数。

### 因果归功链

一个被接受的编辑携带完整链条，每一环都由自己的层出具证据——中间任何一层都不得
替代下一层的证据：

```text
Hypothesis -> RepairPlan selected -> Source entity BOUND -> Action dispatched
  -> Executor/provider complied -> Target causal edge changed
  -> Blocking predicate cleared -> Terminal behavior changed
  -> Task score improved -> Canary/transfer preserved -> Skill committed
```

### 编排

`loop/runner.py` 是两条层共用的唯一编排路径：案例队列 → 检测 → 因果切点 →
episode → 可修复性分诊 → RepairPlan → 规划 → 执行 → 评估 → 账本 → 经验 → meta。
支撑机制：

- **Shadow 批次提交**（`shadow.py`）：每个 shadow 批次一个编辑；合并的多编辑回放
  绝不回溯归功到单条规则。
- **检查点**（`persistence.py`）：`evolution_checkpoint.json` /
  `champion_checkpoint.json` / `shadow_checkpoint.json`；续跑时以失败即关闭的方式
  重新验证词表成员与内容哈希，续跑后从步骤投影重建结果漏斗，因此被打断的运行等价
  于连续运行。
- **开发探针**（`probes.py`）：在稳定开发案例上的带预算探针可转向或回滚开发（两个
  基因组一起回滚）；探针是方向门，绝不是泛化证据。
- **GateEvent 账本**（`ledger.py`）：每次门控决策只记一次，处处投影。

### L1 循环（每案例）

检测 → 归因（确定性、gold 极性门控）→ 反思（LLM，用剥离求值器字段的证据、模块
章程、封闭操作码、有界负面经验、可选的支持/反例对比）→ 结构预验证与门控 → 源回放
（带规则轨迹与确定性 no-effect 分类：`output_changed_then_quarantined` /
`provider_output_changed_but_court_invariant` / `no_observable_change_any_layer`）
→ 对可修复的结构或无效应候选做一次有界的同源修复重试 → 提交/回滚。

### L2 循环（每案例）

提案（一次反思调用、一个封闭词表 `PolicyEdit`、提示词无 gold）→ 验证（词表 /
容量 / 去重 / 索引边界 / 保护）→ 应用到候选基因组副本 → 在法庭收敛点用完全相同的
证据束经 `adjudicate_case(policy_items=...)` 做源回放 → 效应门（add/modify 要求
策略确实触发并移动终态或有效覆盖；remove 要求被定向的项确实触发过、且其移除移动了
可观测输出）→ gold 极性门（终态移动必须严格提升 `task_state_score`）→ 接受或固有
回滚。gold 验证的终态移动接受获得 `strong` 保护，防后续 modify/remove；仅覆盖的
接受保持可修正。

## Typing —— HTC-TES 咨询层

HTC-TES 以咨询型类型智能（`typing/`）存活：经验索引、检索先验、机制聚类、类型条件
反思先验、评估切片与迁移分析。它对因果属主没有否决权：类型分类与因果归因不一致时，
以归因路径为准。

分类法共八型：`factual`、`instructional`、`tool`、`reasoning`、`omission`、
`adoption`、`contextual`、`unknown`。分类器（`classifier.py`）在带版本的、无标签的
`TrajectoryFeatures` 对象（`htc_tes.trajectory_features.v1`）上组合运行时候选结构、
归一化 CRS 信号、归因与模块证据。类型化遥测与 gold 正确性分开记录：方向与类型是
记账，绝不是「某检测方向被优化过」的证据。

## Meta 技能 —— 跨案例策略学习器

meta 层（`meta/`）学习的是如何搜索，绝不是什么东西在因果上坏了。`StrategyEdit` 在
五域封闭词表（22 个活 token）上操作：搜索先验、操作偏好、机制排序、重试策略、候选
多样性。触及属主、因果切点、可修复性或硬门控的 token 根本不存在——这些通道在类型
层面就不可表示，而不只是在校验时被拒。

输入是 GateEvent 投影与经验记忆中的已验证 RepairPlan 结果；输出只是策略建议，以
固定权威标记 `evolution_guidance_only` 投影进提示词。提交由证据门控
（`MIN_COMMIT_EVIDENCE = 3`，同一机制的 3 次观测），`StrategyState` 以内容哈希派生的
id 保持不可变谱系，禁用操作需要人工评审。该层完全确定性，不发出任何 provider 调用。

## 经验记忆

`experience/memory.py` 是单一 Experience Truth。条目是机制级的：什么失败机制、在
什么条件下、在哪个技能层、由什么操作修复、带来什么因果效应、是否发生迁移——而不只
是「某条规则曾经成功过」。去重以确定性归因签名（`layer` + `artifact` +
`reason_code` + `operation`）加特征/方向/成败为键；重复观测合并进一个结果分布，
而不是覆盖第一份快照。

runner 持久化 `htc_tes_experience_memory.json` 与 `htc_tes_coverage.json`。经验是
未来反思的证据，不是准确率主张：只有评估层记录了成功的源级结果之后，覆盖才会提升。
跨运行续跑显式加载 `--initial-genome`、`--initial-policy-genome` 与
`--experience-memory`；只加载记忆绝不改动已注册的基线基因组。

## 学习协议（SEHD-v7 五类型）

`learning/protocol.py` 把训练协议解耦成五个一等类型，让「值得怎么修」与「当前
RuleSpec 能否精确表达」不再过早合并：

```text
FailureObservation    真实发生了什么（终态 + 阻塞谓词 + 属主 + 因果边身份 +
                      缺失/畸形字段 + dependency refs + 轨迹 locus 证据）
RepairIntent          要改哪条因果边，DSL 无关；missing_capability 非空时即为
                      CAPABILITY_GAP 信号（记录「缺什么」，而非静默 no-edit）
ExecutableCandidate   当前能力空间里怎么实现
EvaluationEvidence    执行后发生了什么（fired / edge changed / blocker cleared / transition）
EvolutionMemory       什么条件下这类改动值得再次尝试
```

五个类型现在是运行时的类型总线，而非仅 schema 层定义：runner 在每个触发路径上从
episode 构造 `FailureObservation.from_episode`（`CAPABILITY_GAP` 步骤把
`failure_observation` 与 `RepairIntent` 一并写入审计）；REPAIRABLE 路径构造
`RepairIntent.from_episode` 作为 planner 的声明修复目标（`<repair_intent>` 块）；
fitness 路径把 `EvaluationEvidence`（fired / target_edge_changed / blocker_cleared /
terminal_transition / decision / promotable）写进 `fitness_check`，作为 fitness
消费的唯一效果记录。

`CandidateArchive` 是一个有界、按阶段（raw_hypothesis / schema_valid / bindable /
executable）归档被拒候选的集合，取代「只追单条 mutation lineage」的丢弃。候选失败
经 `classify_candidate_failure` 映射到 `learning/protocol.py` 的封闭分类：
`DSL_NOT_REPRESENTABLE`（能力语言表达不了）、`ENTITY_NOT_FOUND`（实体定位失败）、
`SEARCH_FAILURE`（绑定到 repair 边失败）、`BINDING_AMBIGUOUS`、`STRUCTURAL_INVALID`。
只有 DSL 缺口喂能力课程，只有搜索失败喂策略学习器（§2.8），二者不再压成同一个
`INVALID_PROPOSAL`。归档现在闭环回到下一次候选生成：`archive_policy_signals`
读归档尾部**连续同类别**拒绝串（默认阈值 4），`dsl_not_representable` 追加能力课程
条目、`search_failure` 立即刷新搜索策略（复用 minibatch meta 的
`_run_meta_update`）、`entity_not_found` 把 anchor projector 的 entity-menu 预算翻倍
（16 → 32 → 64 封顶）；不同 failure class 永不互相污染，信号历史与课程条目写入
`meta_review_summary`。

## FN 能力覆盖矩阵

`skills/capabilities.py::fn_capability_coverage_matrix()` 是正向召回学习面的唯一审计
视图：每个 FN 家族记录 `covered`、`operations`、`coverage_kind` 与（未覆盖时）
`required_capability` + `blocker`。三类 coverage：

- `primitive` —— 一个注册的确定性 opcode **本身**就是该机制（当前仅
  `construct_candidate` 之于 missing_claim_candidate）。
- `condition_specialization` —— 同一 `construct_candidate` primitive 覆盖该家族，家族
  的区分度来自学到的 condition / anchor，而非独立 opcode（cross_claim_dependency /
  tool_grounding / temporal_numeric_entity_consistency）。
- `provider_side_capability` —— 确定性 L1 后处理面表达不了，需在 provider-side / L2 层
  发明新能力（missing_evidence_* / wrong_qualification / missing_verifier_route /
  wrong_verifier_instruction / missing_certificate_path / coverage_blind_spot）。
  `blocker` 镜像 owner 注册的 `does_not_support` 事实（如 `collect missing evidence` /
  `invoke a verifier` / `create missing certificate`），是课程输入而非新 L1 opcode——
  新增这些 opcode 会推翻「删除生成性 L1 opcode、保留确定性 transform」的决策并跨越
  权威边界（Skill 不得写证据/证书/verdict）。

### L1 能力边界（不自称发明新 detector capability）

`CAPABILITY_GAP` 被 100% 正确识别后**只记录、不合成**：runner 把 `RepairIntent` +
`failure_observation` + `fn_family` 写入审计，`CandidateArchive` 的连续同类失败信号
追加能力课程条目，但没有任何环节把这些信号变成新 capability。当前 L1 自进化的诚实
定义是**固定 primitive 词表上的规则特化**——学习 condition / anchor 判别器，让八个
确定性 L1 变换（七个抑制/选择 + 一个受限构造）与 L2 判定策略在更多失败形状上正确
触发；系统不宣称能发明新 detector capability。`RepairIntent → CapabilityCandidate → Sandbox executor
合成 → interface/static checks → 源行为证明 → canary → 多案例复发 → holdout →
registry 晋升` 是明确的后续工作（安全引入新执行器必须先有确定性运行时语义与选择门），
不是当前框架行为；在实现前，能力课程（`meta_review_summary.capability_curriculum` +
FN 矩阵的 `required_capability`/`blocker`）是唯一的跨运行输入。

## 运行模式三分

`loop/run_mode.py::resolve_run_mode` 把公开 API 此前塌缩进 `use_gold` /
`fitness_use_gold` / `canary_case_ids` 默认值的三种 regime 显式化：

| 模式 | gold | canary | claim_learning |
|---|---|---|---|
| `supervised_evolution` | 是 | 否 | 是 |
| `label_free_diagnostic` | 否（强制） | 否 | 否 |
| `protected_label_free_adaptation` | 否 | **必需**（fail-fast） | 否（仅 adaptation） |

「没学到 skill」是 planner 不行还是证据不足不允许 commit，就此可区分，不再混在
`skill_count = 0` 里。`shadow._shadow_commit_eligible` 的 canary-vs-gold 准入谓词不变，
本模块只做 regime 的显式化与前置校验。

## 评估与晋升

晋升层级（`evaluation/`）：

1. **结构门**（`gate.py`）通过槽位/词表契约拒绝不安全编辑，外加一个 LLM 无冲突门。
2. **效应**（`effect.py`）：边级反事实验证——目标因果边是否移动、阻塞谓词是否清除、
   干预是否为可归因的原因。
3. **源 fitness**（`fitness.py`）：触发案例上的前后对比；evidence-obligation 本地
   fitness 奖励公诉与辩护覆盖中的较弱者，而不是单边的大差距。
4. **Canary**（`canary.py`）：一组滚动的、先前稳定的案例必须保持决定性状态、运行时
   有效性与未触发的进程状态；canary 回归否决灾难性遗忘。
5. **晋升**（`promotion.py`）：带证据等级的 `PromotionDecision`；`no_effect` 绝不
   静默提交；不同尝试绝不共享 fitness 归功。

对比回放（contrastive replay）额外把一份原本会被接受的编辑回放到一个最近的、稳定
的同类邻居上；极性翻转与 gold 知情（gold-informed）的任务分回归拒绝该编辑。

## 度量

框架设计要报告的漏斗度量（每个步骤在结果漏斗中独立计数）：

| 度量 | 定义 |
|---|---|
| 可修复率 | 有合法 RepairPlan 的失败 / 位于可进化面上的失败 |
| 不可寻址框架失败率 | `UNADDRESSABLE` 计数——框架质量指标，目标 ≈ 0 |
| 编译后绑定率 | `BOUND` / 已编译 proposal |
| 绑定后执行率 | 已执行的修复 / `BOUND` |
| 因果效应率 | 目标边已移动 / 已执行的修复 |
| 阻塞关闭率 | 阻塞谓词已清除 / 目标边已移动 |
| 源改进率 | 源检测器已改进 / 阻塞谓词已清除 |
| 迁移增益 | 进化技能在独立案例上的真实增益——核心价值度量 |
| 框架失败率 | 不可寻址 / 权威冲突 / 路由失配 / 模式失配 / 状态失步 / 基础设施——绝不与学习拒绝混算 |

技能归功绑定目标边、执行、效应、fitness 与迁移；provider 重采样、代理可观测量与
随机裁决移动不挣任何归功。

## 设计原则

1. **复杂检测器，简单进化控制面。** 检测层可以复杂；控制面必须可验证。
2. **一个事实，一个权威。** 许多投影，一个真源。
3. **可修复性先于进化。** 先确立可修复性；之后才开始进化。
4. **多层技能，一个因果契约。** L1/L2/Meta 的新颖性来自不同的学习面，而不是不同
   的失败语义。
5. **Meta 学策略，不学真相。**
6. **类型信息组织经验，不组织因果真相。**
7. **框架失败 ≠ 学习失败**，在所有结果中分离。
8. **执行先于归功。** 没有执行，就没有因果归功。
9. **因果效应先于 fitness。** 没有目标边移动的巧合性标签改进证明不了任何东西。
10. **泛化先于强技能主张。** 源案例改进只证明局部修复；只有跨案例迁移才支持技能
    主张。

## 语义变更登记表

这次重构相对冻结基线恰好允许十项行为变更；其余一切行为等价。本登记表是解释新旧
产物结果差异的规范记录：

| # | 变更 | 位置 |
|---|---|---|
| C1 | 单一可修复性权威；吸收 `should_evolve`；只有 `REPAIRABLE` 进化 | `causal/repairability.py` |
| C2 | 不可寻址重新定义为 `UNADDRESSABLE_FRAMEWORK_DEFECT`，从 no-edit 统计中剥离 | `causal/repairability.py`、`loop/machine.py` |
| C3 | 统一 `CausalFailureEpisode` + 终态因果切点；三处归因站点合并为一处 | `causal/` |
| C4 | 硬性的检测/进化属主边界；validator/transport/route/config 缺陷绝不产出技能 | `causal/repairability.py` |
| C5 | HTC-TES 降级为咨询层；移除属主覆盖与模块过滤权 | `typing/` |
| C6 | L1/L2 共享规范 RepairPlan 协议与单一条编排路径 | `causal/plan.py`、`loop/runner.py` |
| C7 | Meta 重新定义为跨案例策略学习器；属主/切点/可修复性/硬门控通道在类型层面移除 | `meta/` |
| C8 | 五层结果漏斗取代 `skip_*` 词表 | `loop/machine.py` |
| C9 | 收敛只消费完整的修复尝试 | `loop/runner.py` |
| C10 | 完整因果归功链；每一环由自己的层出具证据 | `evaluation/`、`loop/` |

## 框架加固（自进化正确性 + 适度自由度，2026-08）

相对冻结基线新增以下行为。它们不改变检测权威、不进 prompt 语义、不触碰硬门；全部是
「修根因 + 受控自由度 + 纯观测遥测」，并有回归测试锁定。

- **E1 反思方向契约修复**（`loop/runner.py`）：`_ReflectionSignalView.error_type` 改为
  从 gold 极性 + 终态推导（`false_positive` / `false_negative_clean` /
  `unresolved_should_*`），不再回退到因果 reason_code。修复前 gold 锁定的 FP/FN 案例
  的方向契约与极性准入池被静默禁用。
- **E2 触发关闭归功修复**（`loop/runner.py`）：`assess_source_fitness` /
  `_expected_effect_audit` 重新接通 `before_trigger` / `after_trigger`，`source_failure_closed`
  与 `new_failure_trigger_introduced` 两条对称通道恢复生效。
- **E3 极性伙伴面修复**（`planning/candidates.py`）：`_allowed_l1_modules` 增加
  `secondary_owner` 参数，经极性池过滤后把 cut 的 partner 模块（如 FP 的
  `atomic_verifier_instruction` / `withhold_certificate`）纳入合法目标。
- **E4 自适应搜索预算**（`loop/runner.py`）：`_attempt_cap` 给 gold 极性 FP/FN 案例
  一个受上限约束的额外 mutation 重试预算（`_MAX_ATTEMPT_CAP = 6`）；`max_mutation_attempts_per_case`
  上限从 3 放宽到 8。非极性路径行为不变。
- **E5 L1 可观测特征扩展**（`skills/rule.py`）：封闭词表新增布尔特征 `has_tool_retry`
  （同名工具重复调用），确定性投影、经 `_FEATURE_CONTRACT` 校验，仍是 first-call
  label-free 特征。
- **E6 meta 证据接线**（`meta/review.py`）：为 5 个已声明但无证据源、因此永远无法晋升
  的策略 token 补上封闭 feedback-code 映射（search_prior ×2、candidate_diversity ×2、
  retry_strategy ×1）。meta 仍「只学策略、不学真相」，不新增任何可触及属主/切点/门控的
  token。
- **E7 L2 词表候选扩展**（`skills/vocabulary/evolution.py`）：注册候选新增
  `quarantine_reason_other` 谓词（既有 schema/evidence/causal 桶已被支配谓词占用）。
- **E8 Goodhart 背离遥测**（`loop/result.py::_goodhart_divergence`）：把 `directional_evidence`
  （纯代理移动）与 gold 目标改进/回归分开计数，输出 `divergence_rate`。纯报告，绝不
  否决编辑或喂入 prompt。
- **E9 谱系多样性/塌陷遥测**（`loop/result.py::_lineage_diversity`）：逐模块进化规则数、
  接受编辑分布、L2 工作项数，以及塌陷 flag（单模块主导、无进化规则等）。纯报告。

- **E10 实体身份/泛化谓词分离**（`skills/binding.py`、`planning/candidates.py`、
  `detection/injection.py`）：`match_condition_bindings` 增加 `preferred_entity_id`，
  训练期实体按 id 精确绑定（`preferred_entity_grounded`），过宽的泛化谓词不再把源回放
  打成 `BINDING_AMBIGUOUS`；`compile_genome_rules` 输出 5 元组（含 `binding_entity_id`）
  但该 id 不固化进谓词——跨案例复用仍按 `condition_spec` 在新案例 universe 上求值。
  反思提示词按能力注册表枚举 `<entity_menu>`，模型选 `target_entity_id` 而非猜谓词定位。
- **E11 失败分类 + 候选归档**（`learning/protocol.py`）：`classify_candidate_failure`
  区分 DSL / entity / search / binding / structural 五类失败；`CandidateArchive` 按阶段
  归档被拒候选，供能力课程与策略学习器消费。
- **E12 minibatch meta 搜索策略更新**（`loop/runner.py`）：`_run_meta_update` 从
  `_finalize` 抽出；`meta_update_every=N` 使同一 run 后续 case 每 N 个 case 刷新一次
  策略配置（默认 5，`0` 表示仅 run 后 finalize 更新），meta 从「run 后事后」向
  「run 内优化器状态」迈一步。仍只学搜索策略，不碰门控/属主/切点。
- **E13 FN 覆盖矩阵**（`skills/capabilities.py`）：`fn_capability_coverage_matrix` +
  `fn_families_for_owner` 输出正向召回能力覆盖；`loop/mechanism.py::classify_failure_mechanism`
  把 `FailureObservation`（因果边身份 + missing/malformed fields + 轨迹 locus 证据）
  分类到 FN 家族，同一 owner 下的多个家族不再塌缩到表首命中。
  `CAPABILITY_GAP` 步骤把 `RepairIntent`、`failure_observation` 与 `fn_family` 写入审计。

**仍保留为预留**（框架不擅自实现）：engine-level L2 动作 `reclassify_candidate_state` /
`require_route_replay` 仍是 `runtime_owner="engine"` 的预留操作符，不可提议、不可执行；
只有先实现确定性运行时语义、更新 `_POSTCONDITION`/求值器并把它们从预留集移入
court-local 集，才能真正落地。这个顺序被有意保留，因为「能重新调用 provider / 重分类
候选」的动作会显著放大表达力，也就同时放大过拟合与归功复杂度——它必须先有选择门
（见 README「下一步正式实验」）再放开。同理，FN 覆盖矩阵中标 `provider_side_capability`
的七个家族（missing_evidence_* 等）也不在 L1 确定性层实现：它们需要 provider-side /
L2 新能力，属于能力课程输入，而非「再加一个 prompt 规则」。

## 下一步正式实验（规划，尚未实施）

以下为**实验层**规划（实现于 `experiments/`，绝不进 `sehd_rise/`）：密封 holdout +
公开基线对标；holdout 回放作为「泛化选择」信号；测试时 best-of-N 确定性投票；课程/
主动学习与负样本挖掘/红队循环。详见 README「下一步正式实验」。

## 路由解析

所有 provider 路由都经 `sehd_rise/platform/routes.py` 解析，无第三方依赖：仓库
`.env`（绝不覆盖进程变量）→ 路由级 `SEHD_ROUTE_<ROUTE>_*` → 用途级
`SEHD_MUTATION_*` / `SEHD_DETECTOR_*` / `SEHD_JUDGE_*` → 全局 `SEHD_PROVIDER_*` →
配置文件值 → DeepSeek 官方默认值。只保留密钥所在环境变量的名字；凭据在 HTTP 边界
读取，绝不写入源码、提示词、结果文件或日志。显式的非官方代理路由在所选密钥缺失时
失败即关闭，绝不静默替换成 `DEEPSEEK_API_KEY`。

## Provider 出口（三层出口）

所有 provider 调用只有**一个 HTTP 原语**：`detection/transport.py` 的
`provider_call_with_schema`（含 `json_object_provider_call`）。重试、超时、有界
JSON 修复与请求哈希都在这一原语内实现（`time.monotonic()` 截止期跨 attempt 生效）；
它不因任何 `purpose` 字符串改变行为。在它之上，`detection/egress.py` 按各自契约
建了三个特化出口——差异靠结构保证，不靠 `purpose` 串：

| 出口 | 函数 | 契约 | usage |
|---|---|---|---|
| 检测 | `detection_call` / `detection_rankings_call` | census 信封（或 counterfactual 的 schema-relaxed rankings） | 每 surface 调用时自带 record |
| 进化 L1 | `l1_call` | strict JSON；DeepSeek-V4 结构性禁用 thinking | 自带 record |
| 进化 L2 / Pro | `l2_call` | closed-vocabulary；DeepSeek-V4 结构性禁用 thinking | 自带 record |
| ConflictGate | `conflict_gate_call` | 跨层无冲突安全检查；V4 结构性禁用 thinking | 自带 record |

要点：

- **thinking 靠结构**。DeepSeek-V4 默认开启推理，推理预算可在任何 `content` 输出
  前截断（`finish_reason=length`、空 body），strict-JSON 传输随即失败即关闭。进化
  出口在把路由交给原语前对 V4 路线盖 `thinking.type=disabled`；`provider_thinking_control`
  只回读显式路由设置，绝不从 `purpose` 串推导。
- **usage 靠出口自带**。每个出口在调用点就把 surface/purpose 记进
  `platform/usage.py` 的 `ProviderUsageLedger`；不再有事后对 `result["providers"]`
  的 depth-1 遍历。检测侧 14 个 proof-court 调用点各自穿过 `provider_usage_ledger`；
  `ProviderUsageLedger.record` 持锁，支撑 focused prosecution 的 `max_focused_parallel`
  并发扇出。
- **注入接缝在检测出口**。`detection/injection.py` 的 `integrated_engine_provider`
  通过猴子补丁 `egress.provider_call` / `egress.json_object_provider_call`（两个模块级
  别名）把进化 L1 规则追加进检测提示词；进化出口直接绑定原语别名，注入绝不触及
  进化路径。
- **无预算上限**。所有 LLM 调用只保留重试/超时控制；预算上限与仅服务预算判定的
  计数器已移除。`mutation_calls` / `detector_calls` / `conflict_gate_calls` 等计数
  保留为运行遥测，供给结果审计与收敛之外的观测。

### 检测热路径读超时（2026-08-14 观测）

`provider_call_with_schema` 的 per-attempt 读超时默认
`timeout_seconds=300`，但检测路由（`detector_v3_5.json` 的 `candidate_fallback_route` /
`qualification_route` / `propagation_route` / `proof_court` 相关子调用）显式配置
`timeout_seconds=120`、`total_timeout_seconds=480`。`_read_response_body` 把整个响应体
的 socket 读也纳入单调截止期（`sock.settimeout(remaining)`），所以**不是**「只测连接、
不测正文」的经典误配——正文流式停顿会被正确抬成 `TimeoutError` 并进入重试。

真实 5 例并发 census 里 4/5 例的 `INFRA_FAILURE` 都来自这条超时（
`TimeoutError The read operation timed out`），发生在 `proof_court` / `census` /
`candidate_path` / `appeal` 这些检测热路径；叠加 `finish_reason=length` 空 body 的
`provider_empty_content`。结论是**检测侧 V4 推理模型在并发放大下、单个 120s 读窗口内
产不出完整 JSON**，而检测出口（`detection_call`）刻意不盖 `thinking.type=disabled`
（与进化出口不同）：检测让模型完整推理以换证据质量，代价是尾部推理吃满时间与
token 预算。这是一个**吞吐/延迟预算与推理模型的匹配问题**（框架层），不是代理、不是
代码回归；在路由或并发上收紧（降并发串行、或为推理模型配更长读窗口）是下一步
实验配置，不手调检测阈值。

## 证据边界

随时可运行的常驻验证：

- `tools/check_layers.py` —— 无环层级纪律（93 文件 / 15 层，零违例），包括「任何
  框架模块都不得导入冻结基线」的守卫。
- `python -m pytest tests/` —— 循环状态机套件（所有合法迁移可达、非法迁移被拒绝、
  每个漏斗终态独立计数），以及 P0 一致性、实体身份绑定、学习协议/覆盖矩阵/运行模式
  等不变量测试（99 项全绿）。
- 全包导入冒烟 —— 所有模块干净导入。

重构验收（对照冻结基线执行，记录在 git 历史中；验收后迁移 harness 已从树中移除）：

- 序列化往返、因果分类、检测契约、effect/gate/promotion 决策、经验/typing、规划
  提示词与 meta 合法性枚举的行为等价——所有偏差均已记录并绑定到 C1–C10 登记表条目。
- 两个 CLI 入口（`detect` / `evolve`）的真实 provider 端到端运行，产物集完整；e2e
  场景覆盖独立 `UNADDRESSABLE` 计数、非学习事件被排除出收敛、检查点续跑等价。
- 新旧 `compare_run` 的端到端对等运行不属于验收范围（用户决定）；验收证据是真实
  provider 进化运行加离线等价 harness。

**未**确立的内容：

- 不主张任何样本外（out-of-sample）检测改进。进化产物是开发证据；泛化主张需要一次
  密封的、独立的留出集评估运行作为实验层协议（见 `experiments/README.md`），并按
  导师要求给出具体中间输出。
- 类型遥测与策略建议是可观测性；它们不是「某个检测方向改进过」的证据。
