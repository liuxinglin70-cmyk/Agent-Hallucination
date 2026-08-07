# DeepSeek 执行总单：L1↔L2 双向反馈、分级门控与 Meta skill 自进化指导层（v2）

## 0. 文档身份

本文档是 SEHD v6 下一阶段代码任务的执行总单，供 DeepSeek 按阶段实施、测试和汇报。本文档针对以下升级目标：

```text
L1 provider-rule skill
  <-> L2 adjudication-policy skill（可进化的注册词表）
  <- Meta skill（独立的进化指导层，门控本身也自进化）
```

本文档建立在以下代码事实之上：

- `src/sehd_v6/core/bccse/failure_episode.py` 已是因果失败案例的唯一来源；
- `src/sehd_v6/core/bccse/evolution_experience_memory.py` 已提供跨案例机制记忆（schema `sehd.evolution_experience_memory.v1`）；
- `src/sehd_v6/core/bccse/policy_genome.py`、`policy_reflection.py` 和 `src/sehd_v6/core/paca_tcl/policy_evaluator.py` 已提供 L2 的闭词表项、planner 候选、确定性执行和可逆 genome；当前 `L2_PREDICATES`/`L2_ACTIONS` 是冻结常量集（`v6_contracts.py`）；
- `src/sehd_v6/core/bccse/evolution_runner.py` 已统一编排 L1、L2、source replay、canary、contrast、probe、checkpoint 和 resume；L1 主 prompt 构造于 `evolution_runner.py:5035-5049`，L2 selection prompt 构造于 `evolution_runner.py:3593-3597` 与 `policy_reflection.py:176-196`；
- `src/sehd_v6/core/paca_tcl/proof_contract.py:653-688` 已提供 L2 policy hook（只调整终态边，不重算 occurrence ledger）；
- 当前不存在独立 Meta skill 实现、Meta 记忆、可进化注册词表、L2 到 L1 的统一反馈事件或 L2 分级门控。

本文档不替代 `docs/design/SELF_EVOLUTION_FRAMEWORK_REVIEW.md` 中已完成的 FailureEpisode、经验记忆和闭环契约；本文档只规定下一阶段新增和调整的范围。发生冲突时，以下优先级生效：

1. `README.md:16-73` 的核心目标和设计纪律；
2. `.agents/skills/pristine/SKILL.md` 的 First-Time Principle；
3. 本文档的新增 Meta/L2 执行边界；
4. 现有测试和既有代码契约。

### v2 相对 v1 的变更（由导师/同学确认）

- **L2 可进化面放开（设计 A）**：L2 词表从「冻结常量集」升级为「版本化可进化注册词表」。前提可用受审白名单字段上的约束 DSL 组合，动作是可注册的固定算子参数化实例；**每条策略的执行语义仍由确定性代码拥有**。禁止自由文本裁决逻辑。
- **分级门控前置**：阶段顺序改为 P0 → P1（L2 可执行面+注册表）→ P2（分级门控，放开门控）→ P3（双向反馈）→ P4/P5（Meta 层，含门控阈值自进化）→ P6/P7/P8 → P9。
- **label-free 放开**：新动作（consensus restore / boundary consume）在无 gold 批量下最高 `weak_accept`；仅独立证据可升级 `strong_accept`。
- **Meta 自进化闭环**：门控阈值与生成指导本身进入进化循环（误拒率 → 候选 → 验证 → 提交），而不是静态表。
- **验收过程化**：E2/E3 通过条件改为可证伪的过程条件，不依赖模型行为。

执行模型不得因为接受率低而直接降低硬门，不得把 gold 引入 prompt，不得把 Meta skill 变成 detector 裁决器。

---

## 1. 执行规则

### 1.1 必须遵守

- 先阅读 `README.md:16-73`、本文档和目标文件，再修改代码。
- 每个阶段先改代码和测试，再执行该阶段验收；前一阶段未通过不得进入下一阶段。
- 每个阶段保持可运行、可测试、可提交状态。
- 所有 L2 runtime semantics 由确定性代码拥有：模型可以（a）从预计算候选中选择、（b）在受审 DSL/动作白名单内提议**新的注册词条**；但每条已注册 predicate/action 的求值与执行都是确定性代码。
- 新注册的 predicate/action 词条默认 `conditional`/`weak_accept`，只有独立证据后才可升级 `strong_accept`。
- 所有 L1 proposal 继续经过现有 slot contract、ActionSpec、StructuralGate、source replay 和 canary/probe。
- Meta skill 只能输出生成指导、候选排序信息、门控阈值经验、注册表使用指导和审计结果。
- Meta skill 不得直接修改 `proof_adjudication.state`、occurrence ledger、CRS 或 detector 基础逻辑。
- gold 只能进入 attribution polarity、fitness credit、development probe 和最终统计，不得进入 skill、policy、Meta 内容或 reflection prompt。
- 未知 schema、unknown predicate、unknown action、invalid parameter、authority invalid 一律 fail-closed。
- 新实验必须排除 `experiments/protocols/used_case_ledger.txt` 中全部 case ID。
- provider 只能使用 DeepSeek-V4-Flash / DeepSeek-V4-Pro，并继续从环境变量解析凭据。
- 任何新调用都必须说明用途、调用上限、计数位置和成本影响。

### 1.2 禁止事项

- 禁止通过修改单一数值阈值提高接受率。
- 禁止给 L2 增加自由文本 predicate、action、通用 `rewrite_verdict` 或任何不由确定性代码执行的裁决逻辑。
- 禁止用非确定性方式注册新词条：每个新 predicate 必须编译为 `AdjudicationFacts` 白名单字段上的纯函数，每个新 action 必须是注册算子的参数化实例。
- 禁止把 Meta skill 注册成第九个 L1 module。
- 禁止让模型解释或执行 L2 policy。
- 禁止把 `source_case_id`、case identity、gold label、gold explanation、完整 provider response、prompt text 写入 Meta prompt。
- 禁止新增旁路 `meta_skill_memory.json` 作为第二个权威记忆源；Meta 记忆随 `htc_tes_experience_memory.json` 持久化。
- 禁止恢复 quarantined、被 dominating defense 击败或 process-only 的 consensus certificate。
- 禁止把 `conditional` 结果静默写入 deployed champion。
- 禁止用 50 例开发集做 final holdout claim。
- 禁止覆盖工作区中已有但非本任务生成的修改。

---

## 2. 真实实验基线与问题定位

### 2.1 基线运行

完整 50 例运行的权威产物：

```text
experiments/results/evolve_50/run/evolution_result.json
experiments/results/evolve_50/run.stdout.log
experiments/results/evolve_50/run/policy_genome.json
experiments/results/evolve_50/run/htc_tes_experience_memory.json
```

该运行不是 final holdout champion：

- `final_champion_claim=false`；
- 没有本轮 canary/probe 认证；
- 结果属于 development candidate。

### 2.2 50 例运行数字

| 指标 | 实际值 | 解释 |
|---|---:|---|
| total cases | 50 | 真实案例数量，不等于 steps 数 |
| triggered cases | 19 | 进入进化处理的案例 |
| stable cases | 30 | 稳定案例 |
| runtime-invalid | 1 | 仅诊断遥测，不属于进化证据 |
| L1 accepted | 1 | 只有 `BFCL/028` 的 `candidate_trigger/add_rule` 被接受 |
| L1 rejected | 5 | 主要为 no-effect 和 merge rejection |
| L2 audit | 22 | 14 consensus、6 boundary、2 hollow prosecution |
| L2 accepted | 0 | 当前 policy genome 最终仍为空 |
| L2 `skip_no_policy` | 20 | 没有可执行 planner 候选，不是 replay gate 拒绝 |
| L2 fitness reject | 2 | `verified_clean -> unresolved` 与 gold polarity 冲突 |
| detector calls | 60 | 包含 source/policy/canary 等 detector 调用 |
| mutation calls | 13 | 包含 L1 reflection、L2 selection 等 |
| total provider calls | 73 | `detector_calls + mutation_calls` |
| wall time | 16201 秒 | 约 4.5 小时 |

### 2.3 L2 失败不是单一“门控过严”

当前 `policy_reflection.py:53-173` 的 planner 只为以下形态产生候选：

- `hollow_complete_prosecution`；
- `hollow_complete_defense`；
- `all_candidates_quarantined`。

因此当前 22 条 L2 attribution 的实际漏斗是：

```text
22 L2 attributions
  -> 20 次 no_executable_policy_for_attribution
  -> 2 次进入 policy replay
  -> 2 次因为 gold polarity 反向被拒绝
  -> 0 次 accepted policy
```

两层根因，必须分别处理：

1. **动作面覆盖缺口（P1 修）**：`boundary_terminal_conflict` 与 `consensus_gate_blocked` 没有可执行候选，L2 层无法进化。必须补齐可执行动作面与注册词表，否则降低门控只会放行错误的 `verified_clean -> unresolved`。
2. **门控过严（P2 修）**：即使有候选，二值化门控（无 gold 即拒）也会卡死 label-free 批量。必须引入分级门控 + 动作级上限，这是导师 §3.3「放开门控」的直接落地。

### 2.4 L1 失败证据

50 例 L1 拒绝主要包括：

- `source_no_effect:intervention_not_executed`；
- `source_no_effect:no_measurable_target_local_change`；
- `cross_module_merge_required`。

这些结果证明 L1 需要收到两类反馈：

1. 规则是否合法、是否绑定正确输出；
2. L2 或下游裁决认为该规则为什么没有形成有效结果。

反馈必须模糊化，不能把完整 L2 判据和阈值暴露给 L1。

---

## 3. 目标架构和数据流

### 3.1 三层职责

| 层 | 负责内容 | 可以改变什么 | 不可以改变什么 |
|---|---|---|---|
| L1 | provider 侧候选、证书、证据字段生成 | prompt 注入的 slot-rule | proof court 规则、L2 词表语义、gold |
| L2 | court 侧 coverage/consensus/boundary 综合裁决 | policy genome（含注册词表）通过确定性 evaluator 的 terminal edge | provider prompt、occurrence ledger、任意自由 verdict |
| Meta | 进化质量、门控阈值经验、生成指导、总结效率、结果评估、注册词表使用指导 | L1/L2 候选的指导与排序、gate profile 切换、注册词条的引导 | detector verdict、硬门、ActionSpec 合法性、词条的执行语义 |

### 3.2 目标调用链

```text
detect
  -> deterministic failure classifier
  -> build FailureEpisode
  -> retrieve mechanism history
  -> retrieve Meta guidance and bounded gate feedback
  -> L1 reflection OR L2 precomputed policy candidates
  -> existing structural/closed-vocabulary validation
  -> candidate replay
  -> graded evidence assessment (strong/weak/conditional/reject)
  -> GateEvent and FailureAttempt
  -> outcome validation when independent evidence exists
  -> update EvolutionExperienceMemory.MetaSkillMemory + gate thresholds
  -> checkpoint
```

### 3.3 可进化注册词表（核心新增，对应「L2 更开放」设计 A）

L2 词表从冻结常量集升级为版本化注册表。**放开的是词表本身的可进化性，不放开的是一条策略的执行语义必须是确定性代码。**

#### 3.3.1 注册表结构

```text
src/sehd_v6/core/bccse/vocabulary_registry.py   （唯一所有者，新增）

VocabularyRegistry
├── schema_version: "sehd.l2_vocabulary_registry.v1"
├── predicates: dict[str, PredicateEntry]
│     name, facts_whitelist(字段白名单), dsl_source, compiled_function, registered_generation, status
├── actions: dict[str, ActionEntry]
│     name, operator(固定算子), params_schema, registered_generation, status, usage
└── audit: list[RegistryEdit]   （增/删/改全部审计）
```

- `predicates`：每个词条是 `AdjudicationFacts` **受审白名单字段**上编译出的纯函数。白名单即 `AdjudicationFacts` 的地址化字段全集（`v6_contracts.py:60-75`），不允许引入新字段、新数据结构或 provider 原始内容。
- `actions`：每个词条是**固定算子**的参数化实例。算子集为注册的可执行原语（见 P1 6.3），参数按 `params_schema` 校验，未知参数 fail-closed。
- `status`：`baseline` / `registered` / `disabled`。`disabled` 词条仍保留在审计中但不可被 policy item 引用。
- 增删改全部写入 `audit`，随 `policy_genome.json` 或 `htc_tes_experience_memory.json` 持久化，不新建第二个权威文件。

#### 3.3.2 新词条生命周期

```text
模型提议（DSL/动作参数化）
  -> 确定性编译/校验（白名单字段、算子集合、参数 schema）
  -> 注册为 status=registered，默认可用但分级门控上限 weak_accept/conditional
  -> 独立证据（probe / false-reject 统计 / 独立案例）支持后升级 strong_accept
  -> Meta 漂移遥测监控词表膨胀（新增词条率、未知词条 skip 率）
```

#### 3.3.3 与既有契约的关系

- `L2_PREDICATES`/`L2_ACTIONS` 的现有词条成为注册表的 `baseline` 词条，行为 byte-identical。
- `policy_evaluator.py` 的解析改为从注册表读取；未知词条继续 fail-closed 写入 `skipped`。
- 词表增删是**注册表操作**，不是模型自由改写 evaluator；执行语义只在 `vocabulary_registry` 一个地方声明。

### 3.4 Meta 的物理所有权

Meta 经验语义独立，但物理上继续嵌入：

```text
src/sehd_v6/core/bccse/evolution_experience_memory.py
```

唯一权威 artifact 继续是：

```text
htc_tes_experience_memory.json
```

不得再创建并行的 Meta memory 文件。这样可以复用当前：

- `persist_progress()` 的原子写入；
- `load_evolution_checkpoint()` 的恢复边界；
- `state_manifest.py` 对 experience memory 的 hash 绑定；
- `inherit_from` 的完整状态继承；
- probe 失败时的 `stable_memory_payload` 回滚。

---

## 4. 阶段总览和依赖关系

```text
P0 基线冻结
  -> P1 L2 可执行面 + 可进化注册词表
  -> P2 分级门控（放开门控，动作级上限）
  -> P3 GateEvent + L2->L1 模糊反馈
  -> P4 Meta memory/schema/checkpoint
  -> P5 Meta 自进化 + 生成指导 + 总结效率
  -> P6 后验评估 + false-reject 闭环
  -> P7 漂移遥测 + 同步回滚（含词表膨胀）
  -> P8 成本/调用/耗时审计
  -> P9 8-case shadow -> 新 50-case development -> 100+ continuous -> fair comparison
```

阶段不得合并提交。P1 未完成前不得改 acceptance gate 的硬门部分；P1 未完成前不得让 L2 新动作进入 deployed champion；P2 未完成前不得把新动作从 conditional/weak 升级；P4 未完成前不得让 Meta guidance 影响生成；P5 未完成前不得自动切换 gate profile；P6 未完成前不得自动改变 gate profile。

---

## 5. P0：固定现状，不改变生产行为

### 5.1 任务

新增匿名、离线、可重放 fixture，覆盖：

1. consensus blocked；
2. boundary clean 与 terminal unresolved；
3. hollow coverage；
4. L1 structural rejection；
5. L1 compiled/injected 但 no-effect；
6. L1 accepted local movement；
7. L2 policy replay 发生 gold polarity rejection；
8. detector runtime-invalid；
9. checkpoint 在 observation、proposal、attempt outcome 三个中断点恢复。

### 5.2 文件范围

建议新增：

```text
tests/fixtures/meta_skill_evolution.py
experiments/analyze_evolution_run.py
```

建议修改：

```text
tests/test_evolution_standalone.py
tests/test_evolution_hardening.py
```

### 5.3 验收

- fixture 不调用外部 API；
- fixture 不含完整原始轨迹、gold prompt 内容或真实 case ID；
- 能对账 `50 = 30 stable + 19 triggered + 1 runtime-invalid`；
- 能对账 L2 `14 consensus + 6 boundary + 2 hollow prosecution`；
- 能稳定重现 20 次 `no_executable_policy_for_attribution` 和 2 次 polarity reject；
- 生产代码行为不变；
- 运行完整 pytest，记录实际测试数量和通过数量。

### 5.4 命令

```powershell
python -m pytest -q
```

提交：

```text
test(evolution): freeze meta-evolution baselines
```

---

## 6. P1：L2 可执行面 + 可进化注册词表

这是 L2 自进化的地基：没有可执行动作面和可进化词表，L2 层无法进化，后续分级门控与 Meta 都缺载体。

### 6.1 设计原则

- 不能用 `downgrade_coverage` 假装修复所有 L2 问题。该动作只能降低 coverage，不可能恢复被 consensus 阻断的有效 error certificate，也不能消费 boundary 的 clean 结论。
- 新增动作必须是 court-local、确定性、可审计和可回滚的最小动作，不得增加通用 `rewrite_verdict`。
- 词表可进化，但每个词条编译为确定性代码；无自由文本语义。

### 6.2 新增 court-edge 动作（audit-gated）

#### A. 消费已验证 boundary clean

建议动作名：

```text
consume_verified_clean_boundary
```

建议前提：

```text
boundary_state_conflicts_with_terminal
```

只有同时满足以下条件才可以执行：

- `boundary_path_state == verified_clean`；
- 当前 terminal state 为 `unresolved`；
- 没有 surviving error certificate；
- boundary 结果来自既有确定性 boundary path；
- 不修改 occurrence ledger；
- 只改变 proof-court terminal edge；
- `policy_evaluation` 记录 before/after 和动作来源。

预期解决的基线形态：6 个 boundary conflict。

#### B. 恢复经过审计的 consensus-blocked error

建议动作名：

```text
restore_audited_consensus_blocked_errors
```

建议前提：

```text
consensus_gate_blocked_nonempty
```

恢复条件必须全部满足：

- certificate 已通过 `validate_error_certificate`；
- certificate ID 存在于 `consensus_gate_blocked_ids`；
- `benchmark_causal_status` 为 `causes_incorrect_result` 或 `propagates_to_incorrect_result`；
- `impact_kind != process_debt`；
- certificate 未被 dominating defense 击败；
- certificate 不是 quarantined raw provider output；
- 恢复后继续使用现有 earliest-origin/localization 逻辑；
- `policy_evaluation` 记录 restored certificate IDs。

proof court 必须向 policy hook 传递确定性内部审计结构，而不是仅传 count。该结构不得进入 reflection prompt。

**分级约束（对齐 v2 确认）**：该动作在 label-free 批量下最高 `weak_accept`；只有独立证据（probe / false-reject 统计 / 独立案例）支持后才可升级 `strong_accept`（见 P2）。

### 6.3 可进化注册词表实现

#### 6.3.1 谓词 DSL（前提放开）

- 模型可提议的新 predicate 必须是 `AdjudicationFacts` 白名单字段上的**约束表达式**：`field op value`（`op ∈ {==, !=, <, <=, >, >=, in}`）+ 布尔组合（`and` / `or` / `not`）。
- 编译目标：一个纯函数 `(AdjudicationFacts) -> bool`，编译失败（越权字段 / 非法语法 / 非布尔结果）即 fail-closed，不注册。
- 现有 6 个 predicate 成为 `baseline` 词条，行为不变。
- 禁止引用未列入白名单的字段，禁止引入 provider 原始内容、gold 或自由文本。

#### 6.3.2 动作注册（动作放开）

- 固定算子集（第一版）：

```text
lower_coverage            # 同 downgrade_coverage
invalidate_coverage       # 同 invalidate_coverage_claim
set_terminal_unresolved   # 终态置 unresolved（仅 court-local）
emit_telemetry            # 同 emit_telemetry_only
consume_boundary          # 消费已验证 boundary clean（6.2-A）
restore_audited_consensus # 恢复经过审计的 consensus-blocked（6.2-B）
```

- 新动作 = 上述算子的参数化实例（`side` / `target` / `to_state` 等按 `params_schema` 校验）。**算子集本身在 P1 冻结**，不随进化增加算子；进化只增加参数化实例。算子集的扩展是超出本任务的设计变更，需另行确认。
- 保留 `reclassify_candidate_state`、`require_route_replay` 为 reserved engine-level 动作，不注册、不可提议、不可执行。

#### 6.3.3 注册纪律

- 新词条 `status=registered`，默认分级门控上限 `weak_accept`/`conditional`（见 P2）；
- 注册、禁用、删除全部写入 `RegistryEdit` 审计；
- 注册表随 policy genome 参与 lineage/hash：`state_manifest.py` 需要覆盖注册表（见 P4）；
- 词表膨胀进入 P7 漂移遥测。

### 6.4 Planner 映射表

`plan_policy_candidates()` 必须明确处理所有当前 L2 reason，并能在满足安全闸时生成新动作候选：

| reason | 候选策略要求 |
|---|---|
| `hollow_complete_prosecution` | invalidate 或 downgrade prosecution |
| `hollow_complete_defense` | invalidate 或 downgrade defense |
| `all_candidates_quarantined` | 保持 prosecution incomplete/unresolved |
| `boundary_terminal_conflict` | `consume_boundary` 候选（安全闸满足）；不满足则 `emit_telemetry` 或结构化 no-candidate |
| `consensus_gate_blocked` | `restore_audited_consensus` 候选（安全闸满足）；不满足则 `emit_telemetry` 或结构化 no-candidate |

“没有候选”必须拆分为：

```text
no_registered_runtime_action
runtime_precondition_not_satisfied
candidate_blocked_by_protection
candidate_already_present
```

不得继续用一个 `no_executable_policy_for_attribution` 覆盖所有情况。

### 6.5 代码所有权

必须检查和修改：

```text
src/sehd_v6/core/bccse/v6_contracts.py
src/sehd_v6/core/bccse/vocabulary_registry.py   （新增，唯一所有者）
src/sehd_v6/core/bccse/policy_reflection.py
src/sehd_v6/core/paca_tcl/policy_evaluator.py
src/sehd_v6/core/paca_tcl/proof_contract.py     （仅 policy hook 扩展，不动 base 裁决逻辑）
src/sehd_v6/core/paca_tcl/proof_engine.py       （仅 policy 透传扩展）
```

只有在现有 output contract 缺少必要字段时，才允许最小修改 detector output projection。不得重写 proof court 的非 policy 基线逻辑（consensus gate、defense 校验、localization）。

测试必须覆盖：

```text
tests/test_policy_evaluator.py
tests/test_l2_registry.py    （新增）
```

### 6.6 验收标准

- 每个 L2 attribution 都返回结构化 route result；
- boundary 成功路径可实现 `unresolved -> verified_clean`；
- boundary 存在 surviving error 时 fail-closed；
- consensus 只恢复经过确定性审计的证书；
- quarantined/defeated/process-only certificate 永不恢复；
- 新 predicate DSL：合法表达式编译为纯函数；越权字段/非法语法 fail-closed 且不注册；
- 新动作：未知算子/参数 fail-closed，写入 `skipped`；
- 注册表增删改全审计；`baseline` 词条行为 byte-identical；
- empty policy 与 baseline byte-identical；
- telemetry-only 不改变 terminal；
- facts、items 和 evaluator 顺序相同则结果 byte-identical；
- 既有 L2 测试全部通过；
- P0 的 22 条 L2 fixture 不再全部落入相同的空 route 原因。

禁止验收条件：仅因为 L2 accept 数大于 0 就判定通过。

提交：

```text
feat(evolution): complete executable L2 policy routes with evolvable vocabulary registry
```

---

## 7. P2：分级门控（放开门控，对应导师 §3.3）

### 7.1 硬门和质量门必须分开

以下继续 binary hard reject：

- invalid JSON/schema；
- unknown vocabulary（未注册 predicate/action 或算子）；
- invalid parameter；
- ownership mismatch；
- ActionSpec invalid；
- label/case leakage；
- immutable baseline/protected item violation；
- detector authority invalid；
- replay runtime invalid；
- gold polarity regression；
- canary regression；
- unknown execution authority。

以下允许质量分级：

- legal but effect evidence weak；
- coverage-only movement；
- telemetry-only movement；
- label-free directional movement；
- local output moved but downstream edge unchanged；
- historical evidence supports generation shape；
- conflict signal unavailable；
- independent validation pending；
- **新注册词条首次使用（P1 注册表）**。

### 7.2 决策等级与动作级上限

```text
strong_accept
weak_accept
conditional
reject
```

| 等级 | 允许行为 | 判定来源 |
|---|---|---|
| `strong_accept` | 提交并获得 protection | 终态移动 + gold 确认 **或** 独立验证证据（probe / canary / 独立案例） |
| `weak_accept` | 进入可逆 working genome，不得获得最终正确性声明，仍可 modify/remove | 终态移动（label-free）或 coverage 移动，canary 保留 |
| `conditional` | 进入 pending validation 或 telemetry trial，不进入 deployed champion | telemetry-only fired / effect 不可得 / 新注册词条首次使用 |
| `reject` | 不提交，写入反馈和经验 | 仅硬负信号（§7.1） |

**动作级上限（放开的代价控制）**：

- `restore_audited_consensus_blocked_errors`：label-free 最高 `weak_accept`；仅独立证据升级。
- `consume_verified_clean_boundary`：label-free 最高 `weak_accept`；仅独立证据升级。
- `emit_telemetry` / telemetry-only：最高 `conditional`。
- coverage-only movement：最高 `weak_accept`。
- 无独立证据不得 `strong_accept`。

### 7.3 label-free 判定规则（对齐 v2 确认）

- `strong_accept` 不依赖 gold 单独可得；在 label-free 批量中，若存在独立验证证据（registered canary、development probe、后续独立案例）支持同一可执行 intervention，则可 `strong_accept`；否则最高 `weak_accept`。
- 决策等级由**确定性 evidence table** 计算，不使用单一浮点阈值。证据维度：

```text
structure
ownership
execution
local_effect
independent_regression
historical_support
historical_false_reject
cost_efficiency
```

每项只能是：

```text
positive / neutral / negative / unavailable
```

### 7.4 旧 decision 迁移

现有 decision 如：

```text
accept_batch
accept_policy
reject_fitness
reject_policy_fitness
reject_policy_no_effect
skip_no_policy
```

必须保留 audit 中的原始 decision 作为 `legacy_decision`，同时生成唯一的 `decision_level`。不得在两个模块中各自重新推导等级。

### 7.5 验收

- hard violation 永远 reject；
- gold regression 永远 reject；
- canary regression 永远 reject；
- telemetry-only 最大 conditional；
- coverage-only 最大 weak_accept；
- 无独立证据不得 strong_accept；
- 新动作 label-free 最高 weak_accept，独立证据后才升级；
- conditional 不进入 deployed champion/policy artifact；
- pending conditional 可 checkpoint/resume；
- Meta profile 无法覆盖 hard gate（P5 落地后验证）；
- 两个已知 hollow prosecution polarity reject 仍保持 reject。

提交：

```text
refactor(evolution): grade L2 gate decisions with action caps
```

---

## 8. P3：GateEvent 和 L2 到 L1 模糊反馈

### 8.1 新增文件

```text
src/sehd_v6/core/bccse/gate_feedback.py
tests/test_gate_feedback.py
```

### 8.2 GateEvent schema 与 event ID

```python
@dataclass(frozen=True)
class GateEvent:
    schema_version: str
    event_id: str
    episode_id: str            # 无提案场景允许 ""
    layer: str
    artifact: str
    proposal_signature: str    # 无提案场景为 ""
    operation: str
    stage: str
    decision_level: str        # strong_accept / weak_accept / conditional / reject / skip
    reason_codes: tuple[str, ...]
    feedback_codes: tuple[str, ...]
    execution_trace: dict[str, bool]
    observable_delta_class: str
    downstream_delta_class: str
    sequence: int
```

event ID 生成（确定性，排除事件自身字段与时间戳）：

```text
payload = canonical_json({
    "schema_version": "sehd.gate_event.v1",
    "episode_id": episode_id or "",
    "layer": layer,
    "artifact": artifact,
    "proposal_signature": proposal_signature,
    "operation": operation,
    "stage": stage,
    "decision_level": decision_level,
    "reason_codes": sorted(reason_codes),
    "feedback_codes": sorted(feedback_codes),
    "sequence": sequence,
})
event_id = sha256(payload).hexdigest()
```

canonical_json = `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`。

不得写入：

- raw case ID；
- raw rationale；
- 完整 rule text；
- gold；
- provider response；
- prompt；
- credential；
- URL/path。

### 8.3 与既有审计的关系（单一权威）

- **GateEvent 是 L1/L2 每次决策终态的统一门控记录**（含无提案场景）。
- `FailureAttempt`（`failure_episode.py:218-274`）继续作为 per-episode 的执行记录，保留 compiled/injected/applied/observable_delta/downstream_court_delta/no_effect_reason；每个带提案的 attempt 通过 `proposal_signature` 与唯一 GateEvent 对应。
- `step.gate_decision` / `l2_policy_audit` 保留为兼容视图；决策等级的权威推导只在 GateEvent 生成点（runner 内单一函数）进行，不得在 audit 视图重新推导。
- GateEvent 持久化：L1/L2 的 GateEvent 追加到 checkpoint 的 `gate_events` 字段；GateExperience 聚合进 memory（P4）。

### 8.4 模糊反馈闭词表

建议第一版固定为：

```text
insufficient_grounding
execution_not_established
independent_regression_risk
scope_or_ownership_mismatch
unsafe_or_unrepresentable
redundant_intervention
policy_route_unavailable
candidate_quality_inconclusive
```

内部精确 reason 与 prompt feedback 的映射必须集中在 `gate_feedback.py`，不能由 runner、memory、reflection 各自维护。

### 8.5 接线位置

L1 主 prompt 构造位于：

```text
evolution_runner.py:5035-5049
```

L2 selection prompt 构造位于：

```text
evolution_runner.py:3593-3597
policy_reflection.py:176-196
```

新增统一投影函数：

```python
project_gate_feedback_for_prompt(...)
```

该函数只输出 bounded、closed-vocabulary、generation-hint-only 内容。

### 8.6 验收

- 每个 L1/L2 决策终态（含 skip/no-proposal）都有一个 GateEvent；
- GateEvent resume 幂等；
- 所有旧 decision/reason 都能映射为 feedback code；
- unknown reason 映射为 `candidate_quality_inconclusive`；
- L1 prompt 最多注入固定数量 feedback，建议 5 条；
- L2 prompt 最多注入固定数量 feedback，建议 3 条；
- 不暴露 threshold、gold polarity、完整 L2 判据；
- 不增加任何 provider/detector/reflection/critic/replay 调用；
- owner route、ActionSpec、StructuralGate 行为与 P0 一致。

提交：

```text
feat(evolution): add bounded bidirectional gate feedback
```

---

## 9. P4：独立 Meta skill memory、schema、checkpoint

### 9.1 记忆所有权

修改：

```text
src/sehd_v6/core/bccse/evolution_experience_memory.py
src/sehd_v6/core/bccse/failure_episode.py
src/sehd_v6/core/bccse/evolution_runner.py
src/sehd_v6/evolution/state_manifest.py
src/sehd_v6/evolution/runner.py
src/sehd_v6/evolution/__main__.py
```

新增测试：

```text
tests/test_meta_skill_memory.py
```

### 9.2 schema 迁移

当前：

```text
sehd.evolution_experience_memory.v1
agenthallu.bccse_v5_checkpoint.v4
```

建议升级为：

```text
sehd.evolution_experience_memory.v2
agenthallu.bccse_v5_checkpoint.v5
```

迁移边界规则：

- memory v2 正常加载；v1 在单一 `from_dict()` 边界迁移为 v2（新增 Meta 子域为空、既有 type/memory 结构逐字段复制）；无 schema 但完全匹配旧 type-only 结构时执行一次 legacy migration；其他未知 schema 直接 `ValueError`；内存中的对象只有 v2 当前格式；不保留散落在 runner 中的 v1/v2 双轨构造分支。
- checkpoint v4→v5：加载时识别 v4，补齐新增字段默认值后迁移到 v5 内存格式，下一次持久化写 v5；`gate_events`、Meta 相关字段缺失时默认空。其他未知 schema 直接 `ValueError`（与现有 `load_evolution_checkpoint` 行为一致）。`evolution_result.json`/`records.json` 为运行产物，不参与迁移，仅由 `analyze_evolution_run.py` 向后兼容读取。

### 9.3 Meta memory 内容

#### GateExperience

```text
gate_event_id
mechanism_signature
layer
artifact
stage
decision_level
feedback_codes
execution_trace_class
follow_up_status
validation_source
sequence
```

#### GuidanceProfile

只允许闭词表 token（可被 P5 进化增删），例如：

```text
bind_action_to_owned_output
prefer_observable_discriminator
reuse_effective_action_shape
avoid_unexecuted_action_shape
avoid_downstream_invariant_surface
require_independent_anchor
prefer_less_destructive_policy
seek_distinct_candidate_direction
```

#### EfficiencyProfile

```text
attempts_to_accept
mutation_calls_to_accept
source_replays_to_accept
latency_to_accept
```

效率只做监控和指导，不定义单一自动放行阈值。

### 9.4 Prompt 投影

新增唯一函数：

```python
project_meta_skill_memory_for_prompt(...)
```

输出必须：

- bounded；
- canonical；
- closed vocabulary；
- 不含 raw trajectory；
- 不含 raw provider response；
- 不含 case/source/model identity；
- 不含 gold/evaluator fields；
- 固定最多历史代表数量；
- 明确 `authority="generation_hint_only"`。

### 9.5 checkpoint/inheritance/rollback

Meta 状态随 `htc_tes_experience_memory.json` 持久化，因此：

- `state_manifest.py` 的 experience memory hash 必须覆盖 Meta；
- checkpoint 的 `stable_memory_payload` 必须覆盖 Meta；
- failed probe 必须同步回滚 L1、L2、causal memory 和 Meta memory；
- resume 必须恢复 Meta sequence、dedup ledger、pending validation；
- inheritance 必须恢复完整 Meta memory，不能静默使用空 Meta memory；
- `gate_events`（P3）随 checkpoint 持久化并参与 resume 幂等。

### 9.6 验收

- v1 memory 可确定性迁移为 v2；v2 round-trip 规范化相等；
- v4 checkpoint 可迁移加载；unknown schema 拒绝；
- event/episode/attempt validation 幂等；
- Meta memory 不包含 identity、gold、trajectory、prompt、provider response；
- interrupted 与 uninterrupted run 规范化 Meta memory 相同；
- inheritance 后 memory hash 与 manifest 一致；
- probe rollback 后四类状态 hash 都恢复稳定版本；
- 不增加模型调用。

提交：

```text
feat(evolution): persist independent meta-skill experience
```

---

## 10. P5：Meta 自进化、生成指导与总结效率

这是导师 §3.2 的核心：门控判断本身纳入 Skill 经验自进化。Meta 不是静态规则表，而是有自己的进化闭环。

### 10.1 Meta 阈值自进化闭环（核心新增）

```text
GateExperience（P4）累计
  -> 确定性聚合：按 feedback_code / mechanism_signature 统计 误拒率、supported/unsupported
  -> 触发条件（预注册）：某类误拒率超过注册阈值 或 独立证据出现
  -> Meta 生成候选：
       1) GuidanceProfile token 增删候选
       2) gate profile 切换候选（离散档位，见 P5 10.5）
       3) 注册词表使用指导候选
  -> 确定性校验（闭词表、gold 隔离、不触 hard gate）
  -> 独立验证（probe / canary / 独立案例）
  -> 提交 / 回滚，回写 GateExperience
```

- 第一版只生成 guidance 与 profile audit，**不自动放宽 gate**；只有在独立证据达到实验协议预注册的要求后，才允许在离散 profile 之间切换。
- 所有切换写入 audit；`EfficiencyProfile` 跟踪「从归因到接受」的轮次与调用数，识别重复低效路径。

### 10.2 L1 接入

在 `evolution_runner.py:5035-5049` 中按以下顺序构造 prompt：

```text
FailureEpisode
  -> mechanism history
  -> bounded GateEvent feedback（P3）
  -> Meta GuidanceProfile
  -> existing causal reflection prompt
```

Meta guidance 只能作为 generation hint，不能替代：

- `allowed_modules`；
- module charter；
- ActionSpec；
- rule slots；
- StructuralGate。

### 10.3 L2 接入

L2 必须保持：

```text
planner 先枚举合法 PolicyEdit
  -> Meta 计算候选排序特征
  -> 模型只返回 candidate_index/no_proposal
  -> validate_policy_edit
  -> deterministic policy replay
```

Meta 不得扩大候选集合。

### 10.4 候选指导特征

建议使用闭词表特征：

```text
owned_output_match
action_shape_previously_effective
action_shape_previously_no_effect
observable_field_available
downstream_edge_reachable
independent_anchor_available
destructive_scope
duplicate_risk
```

### 10.5 Gate profile 离散档位（供 Meta 切换）

第一版预注册两档，后续由 Meta 按证据在档位间切换：

```text
conservative  # P2 验收档：新动作 label-free 最高 weak_accept，严格按 §7.2
steady        # 独立证据达到协议要求后：已获支持的动作可 strong_accept，误拒率高的反馈码可放宽
```

切换条件必须预注册；无证据不切换；Meta 无法覆盖 hard gate（§7.1）。

### 10.6 验收

- 无 Meta history 时现有流程正常；
- 有 history 时 prompt 包含 bounded guidance；
- L2 prompt 仍只有 candidate index 选择；
- 已确认 no-effect 的 action shape 不再被优先推荐；
- 历史 effective ActionSpec 可以被复用为生成假设；
- GuidanceProfile token 增删走候选-验证-提交闭环，全部审计；
- gate profile 切换无独立证据不生效；
- Meta guidance 不改变硬门结果；
- 不增加 candidate=1 的 critic 调用；
- 不增加总 provider 调用；
- 相同 fixture 下 duplicate proposal 不增加；
- source no-effect 不高于 P0 基线。

提交：

```text
feat(evolution): self-evolve meta gate thresholds and generation guidance
```

---

## 11. P6：后验评估和 false-reject 闭环

### 11.1 后验验证对象

新增 `OutcomeValidation`，只记录验证结论，不记录 gold 内容：

```text
gate_event_id
validation_source
outcome
validated_sequence
```

`outcome` 闭词表：

```text
supports_accept
contradicts_accept
supports_reject
false_reject
inconclusive
```

### 11.2 false reject 定义

只有以下情况可以标记 `false_reject`：

1. 同一机制在独立 validation case 上产生目标 observable effect 且没有 independent regression；
2. 后续独立 probe/holdout 支持同一可执行 intervention；
3. 经过授权的独立 evaluator 给出支持结论。

以下不能标记 false reject：

- 同 source 第二次模型采样；
- 同 prompt 的 retry；
- telemetry-only fired；
- 无 gold 的 terminal movement；
- 接受率低；
- 模型 rationale 声称规则正确。

### 11.3 Meta 更新纪律

Meta 可累计：

```text
supported_reject_count
false_reject_count
unsupported_accept_count
supported_accept_count
inconclusive_count
```

这些计数是 P5 阈值自进化闭环的输入；第一版不自动放宽 gate。

### 11.4 验收

- 无独立证据时只能是 pending/inconclusive；
- validation event 幂等；
- source replay 不能伪造 false reject；
- 后续独立案例可以支持前次 reject 的误拒标记；
- unsupported accept 会标记 profile risk；
- outcome 只能通过闭词表回写；
- Meta 结果不进入 detector verdict。

提交：

```text
feat(evolution): close meta outcome evaluation loop
```

---

## 12. P7：漂移遥测和同步回滚（含词表膨胀）

### 12.1 BatchDriftSnapshot

每批持久化：

```text
feedback_code_distribution
acceptance_rate_by_layer
conditional_rate
false_reject_count
unsupported_accept_count
runtime_invalid_rate
attempts_to_accept
mutation_calls_per_accept
tokens
latency
cost_status
registry_size              # 注册词表：baseline/registered/disabled 计数
new_registration_rate       # 新注册词条率
unknown_vocabulary_skip_rate # 未知词条 fail-closed skip 率
```

### 12.2 漂移维度

必须分开报告：

- semantic drift：guidance token 分布变化；
- structural drift：operation/field 分布变化；
- decision drift：四级决策分布变化；
- effect drift：supported/unsupported outcome 变化；
- cost drift：单位有效进化的调用、token 和耗时变化；
- vocabulary drift：注册词条增删速率、未知词条 skip 率、禁用词条复活。

综合 drift index 只能用于报告，不得单独作为不可解释的自动放行依据。

### 12.3 强制回滚条件

- gold polarity regression；
- canary regression；
- vocabulary/schema drift（未知词条异常上升）；
- unsupported strong_accept；
- gold/identity prompt leakage；
- Meta memory hash mismatch；
- checkpoint state mismatch；
- 注册表 audit 断裂（disabled 词条被引用等）。

统计预警默认只报警，除非实验协议提前预注册自动回滚条件。回滚必须同时恢复：

```text
L1 champion hash
L2 policy hash（含注册表版本）
experience memory hash
Meta profile/version
pending conditional queue
```

### 12.4 验收

- 每批都有闭词表 drift snapshot；
- snapshot 不含 case identity；
- forced breach 可以恢复上一稳定状态；
- resume 后与稳定运行规范化一致；
- drift 不参与 detector verdict；
- Meta 不可绕过 hard gate；
- 注册表增删可回溯到 RegistryEdit 审计。

提交：

```text
feat(evolution): monitor and rollback meta-skill and vocabulary drift
```

---

## 13. P8：成本、调用和耗时审计

### 13.1 当前缺口

当前真实运行已有：

- request hash；
- usage tokens；
- latency；
- cache hit；
- detector/mutation logical counters。

当前缺少：

- evolution run 统一 usage ledger；
- repair call 的完整 usage 汇总；
- USD price snapshot；
- evolution-specific 成本分析脚本；
- 每个 accepted/conditional outcome 的成本归属。

### 13.2 文件范围

建议新增：

```text
src/sehd_v6/core/provider_usage.py
experiments/analyze_evolution_costs.py
```

### 13.3 Usage ledger

每个 request 记录：

```text
request_hash
purpose
route/model
cache_hit
transport_attempts
prompt_tokens
completion_tokens
reasoning_tokens
latency_seconds
repair_used
logical_stage
```

按 `request_hash` 去重。logical call 和 fresh network request 分开报告。

### 13.4 Price table

价格必须是显式、版本化的实验输入，不得硬编码在 detector config：

```json
{
  "schema_version": "sehd.provider_pricing.v1",
  "currency": "USD",
  "effective_date": "2026-08-06",
  "models": {
    "model-name": {
      "input_per_million": 0,
      "output_per_million": 0
    }
  }
}
```

没有价格表时必须输出：

```text
cost_status=unavailable
```

不得用假 0 代表未知成本。

### 13.5 每次实验必报

```text
wall duration
mutation calls
source replays
policy replays
canary replays
probe replays
unique fresh requests
cache hits
prompt/completion/reasoning tokens
USD cost 或 unavailable
calls per accepted edit
tokens per accepted edit
calls per independently supported edit
```

### 13.6 验收

- request hash 去重正确；
- cache hit 不计 fresh transport；
- repair usage 不丢失；
- logical/network calls 可分离；
- 无价格时 cost status 明确 unavailable；
- 50 例可解释 60 detector + 13 mutation = 73 logical calls；
- 新版相对基线报告绝对增量和百分比增量。

提交：

```text
feat(experiments): audit evolution latency and API cost
```

---

## 14. P9：实验执行顺序和正式验收

### 14.1 E0 离线契约

输入：P0 fixture。

开启：新 L2 action、注册表、GateEvent、Meta memory、graded gate。

关闭：外部 API、自动 profile evolution、final claim。

验收：所有新增和既有测试通过，空 policy baseline 不变，gold firewall 不变。

### 14.2 E1 8 例 shadow 验证

必须重新选取 8 个全新案例，排除完整 used-case ledger。目标覆盖：

- consensus block；
- boundary conflict；
- hollow coverage；
- L1 provider artifact missing；
- L1 malformed certificate；
- stable clean；
- runtime-invalid 或不可执行 route；
- 同机制复用案例。

此阶段使用 shadow Meta：记录 Meta recommendation，但不能改变 deployed genome。

报告：

```text
L2 attribution 到 executable candidate 的覆盖率
first-attempt valid proposal rate
execution established rate
effect observed rate
decision_level 分布（strong/weak/conditional/reject/skip）
GateEvent 完整率
prompt leak count
registry 新增词条数与 skip 率
logical/fresh calls
wall time
cost status
```

通过条件（过程条件，不依赖模型行为）：

1. 8 例全部有结构化 route result；
2. 不再用统一空 route 原因覆盖不同 L2 机制；
3. 至少验证 boundary 和 consensus 的 deterministic replay contract；
4. Meta shadow 不改变 L1/L2 genome；
5. 无 hard gate violation 被放行；
6. 无 gold/identity leak；
7. 新注册词条默认等级 ≤ weak_accept/conditional（不进入 deployed champion）。

### 14.3 E2 新 50 例 development run

必须使用完全新案例，排除全部 ledger；不能直接复用 `evolve_50`。

设置：

- L2 新动作 + 注册表开启；
- 分级门控使用 conservative profile（P2）；
- L2->L1 模糊 feedback 开启；
- Meta guidance 开启（P5）；
- 自动 profile evolution 关闭；
- final champion claim 关闭；
- 独立 canary/probe 预注册。

核心指标（报告，不单独作为通过条件）：

```text
L1/L2 proposal rate
L2 executable route coverage
first-attempt structural-valid rate
execution-established rate
strong/weak/conditional/reject distribution
source no-effect rate
canary regressions
registry 新增词条率与 unknown skip 率
calls per supported edit
```

通过条件（过程条件）：

- L2 executable route coverage 高于旧版 2/22；
- source no-effect 不高于 P0 基线；
- gold regression 为 0；
- canary regression 为 0；
- 每个 decision 可追溯 GateEvent；
- cost/time 增量可解释；
- conditional 不进入 deployed champion；
- 新动作 label-free 未以 strong_accept 进入 champion；
- 无 gold/identity leak；
- resume 幂等（interrupted 与 uninterrupted 规范化一致）。

### 14.4 E3 100+ 连续进化

检查点建议为 25、50、75、100+。每个检查点输出：

```text
L1 hash
L2 hash（含注册表版本）
experience memory hash
Meta profile/version
decision distribution
feedback distribution
false-reject evidence
drift snapshot
registry snapshot
cost snapshot
```

通过条件（过程条件）：

- resume 与 uninterrupted run 规范化一致；
- memory 和 pending queue 有容量上限；
- 没有 identity/gold leakage；
- 没有 forced drift breach；
- 注册表增删全部可回溯 RegistryEdit；
- final holdout 仍封存。

不把「Meta guidance 被复用」「calls per supported edit 不恶化」作为通过门槛；它们作为趋势指标进入报告，供后续 fair comparison 分析。

### 14.5 E4 fair comparison

最终至少运行四组：

| 组 | L1 evolution | L2 evolution | Meta |
|---|---|---|---|
| A | off | off | off |
| B | on | off | off |
| C | on | on | off |
| D | on | on | on |

统一冻结：

- case order；
- provider route；
- temperature；
- token budget；
- detector config；
- call budget；
- canary/probe protocol；
- external dataset split。

最终报告必须区分：

```text
pre-policy L1 detection effect
post-policy final detection effect
L2 policy effect
Meta effect on evolution quality
Meta effect on convergence/cost
```

不能把 L2 改写后的 final state 直接宣称为 L1 效果。

---

## 15. 测试矩阵

### 15.1 Meta contract

```text
tests/test_gate_feedback.py
tests/test_meta_skill_memory.py
```

必须覆盖 stable ID、reason 映射、unknown reason、prompt firewall、schema migration（memory v1->v2、checkpoint v4->v5）、round-trip、resume、inheritance、rollback、cost dedup、GateEvent 与 FailureAttempt 对应关系。

### 15.2 L2 registry contract

```text
tests/test_l2_registry.py
tests/test_policy_evaluator.py
```

必须覆盖：

- DSL 合法表达式编译为纯函数；越权字段/非法语法 fail-closed；
- 新动作参数化实例校验；
- boundary consume 成功路径与 surviving error 拒绝；
- consensus audited restore 成功路径；
- quarantined/defeated/process-only 不恢复；
- occurrence ledger unchanged；
- planner 覆盖每个 reason；
- empty policy byte-identical；
- policy evaluator deterministic；
- baseline 词条行为不变。

### 15.3 Bidirectional feedback

在 `tests/test_feedback_loop.py` 增加：

1. L2 feedback 在后续 L1 reflection 前可见；
2. feedback 只包含抽象 code；
3. gold 不出现在 feedback；
4. precise same-source retry 与 cross-case fuzzy feedback 分离；
5. feedback 不改变 owner route；
6. feedback 不放宽 StructuralGate；
7. 无提案决策也产出 GateEvent。

### 15.4 Graded gate

新增 `tests/test_graded_gate.py`，覆盖：

- hard reject；
- strong accept（独立证据路径与 gold 路径）；
- weak accept（label-free 终态移动）；
- conditional；
- 新动作 label-free 最高 weak_accept；
- telemetry 最大 conditional；
- gold/canary regression hard reject；
- pending conditional checkpoint/resume；
- Meta profile 不能覆盖 hard gate。

---

## 16. Definition of Done

只有以下条件全部满足才算完成：

### 代码

- 所有当前 L2 reason 都有 route result；
- consensus/boundary 新语义由确定性 evaluator 执行且分级门控有动作级上限；
- 注册词表增删改全审计，baseline 词条行为不变；
- GateEvent 成为 L1/L2 统一门控记录（含无提案）；
- L2->L1 feedback 使用模糊闭词表；
- Meta memory 作为 experience artifact 的独立子域持久化；
- Meta 阈值自进化闭环（误拒率 → 候选 → 验证 → 提交）可运行；
- Meta guidance 有唯一 projection owner；
- hard gate 和 graded quality gate 分离；
- conditional 不进入 deployed champion；
- outcome validation 能记录 false reject/unsupported accept；
- drift snapshot 和同步回滚可运行（含词表膨胀）；
- 成本报告可对账。

### 安全

- 所有 gold firewall 测试通过；
- unknown vocabulary fail-closed；
- Meta 不能改变 verdict；
- Meta 不能创建任意 policy 或自由文本词条；
- Meta 不能绕过 ActionSpec、replay、canary、probe 或 polarity gate；
- 新注册词条默认 ≤ weak_accept/conditional；
- empty policy 保持 baseline 行为。

### 持久化

- memory/checkpoint schema 迁移明确（v1->v2、v4->v5）；
- resume 幂等；
- inheritance hash 完整；
- probe rollback 同步恢复 L1/L2/Meta；
- interrupted 与 uninterrupted 规范化一致。

### 实验

- P0 fixture、E1 shadow、E2 新 50、E3 100+ 连续运行全部有产物；
- 新实验排除全量 used-case ledger；
- 时间、调用、token、成本完整报告；
- gold regression 为 0；
- canary regression 为 0；
- 没有将内部自评当作最终 improvement claim；
- final holdout 只在最后打开。

---

## 17. DeepSeek 首轮执行指令

DeepSeek 首轮只执行以下内容，不得直接修改生产代码：

1. 阅读 `README.md:16-73`、本文档、`evolution_runner.py`、`policy_reflection.py`、`policy_evaluator.py`、`proof_contract.py`、`v6_contracts.py`、`evolution_experience_memory.py`。
2. 运行完整 pytest 并记录实际基线。
3. 检查当前工作区，只识别并保留非本任务已有修改。
4. 生成 P0 fixture 设计清单，列出每个 fixture 的输入、预期 attribution、预期 gate decision、预期调用数。
5. 单独回答 P1 可行性：
   a. `consume_verified_clean_boundary` 与 `restore_audited_consensus_blocked_errors` 是否能在现有 proof-court output contract 内完成；如果字段不足，列出缺失字段、生产位置和下游用途。
   b. 谓词 DSL 的最小语法与 `AdjudicationFacts` 白名单字段集，说明越权字段如何在编译期 fail-closed。
   c. 注册表如何融入 `policy_genome.json` / `htc_tes_experience_memory.json` 的 hash 与 lineage，不新建权威文件。
6. 不运行真实 provider 实验。
7. 不修改 P2-P9 代码。
8. 首轮只提交 P0 方案和测试基线，等待阶段确认。

首轮执行完成的判断标准：DeepSeek 能解释为什么 50 例的 20 次 L2 skip 不是 gate rejection，能指出 `policy_reflection.py:67-87` 的候选覆盖缺口，能说明 P1 新动作如何保持 occurrence ledger 与 gold firewall 不变，能说明新词条如何默认限制在 weak_accept/conditional，并能给出完整 pytest 的实际结果。
