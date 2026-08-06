# SEHD v6 优化执行总单 v3

> **主题**：L1↔L2 双向反馈、L2 双阶段裁决、可进化注册词表、分级晋升与完整 Meta Review 闭环  
> **仓库**：`liuxinglin70-cmyk/sehd-v6`  
> **规划基线**：以执行开始时的最新 `main` 为准；本计划审查时参考 `main@3a20ae7b296b6fcd61d7d23ff9d0591ff4070122`  
> **首要目标**：把优化后的框架端到端跑通，而不是复现或追逐旧实验中的接受数量  
> **文档版本**：v3  
> **日期**：2026-08-06

---

## 0. 文档定位

本文档是 SEHD v6 下一阶段的**代码实施、测试、联调、实验和验收总单**。

本轮不以旧 50 例运行中的接受率、拒绝率或 L2 attribution 数量为成功标准。旧结果只允许用于：

1. 解释历史行为；
2. 建立兼容性 fixture；
3. 防止已有正确行为回退；
4. 校验分析脚本的向后兼容性。

本轮真正的成功标准是：

```text
输入案例
  -> detector 正常运行
  -> 确定性归因
  -> L1 或 L2 生成合法候选
  -> 候选确定性 replay
  -> 统一 PromotionDecision
  -> working / deployable 状态正确更新
  -> GateEvent 与模糊反馈产生
  -> Meta Review 获取经验并形成指导或 profile 决策
  -> checkpoint / resume / inherit / rollback 一致
  -> 调用、耗时、token、成本可审计
```

只要上述闭环没有真正跑通，即使接受率提高，也不得判定本轮完成。

---

## 1. 已确认的架构决定

### 1.1 L2 采用 pre-ledger / post-ledger 双阶段架构

L2 仍然是一个 adjudication-policy 层，但内部明确分为两个执行阶段：

```text
L2-pre：pre-ledger policy
  - 位于 consensus disposition 之后、occurrence snapshot 之前
  - 可影响 surviving certificate set
  - 影响 occurrence ledger、earliest-origin、responsible event
  - 只能执行经过审计的最小确定性动作

L2-post：post-ledger policy
  - 位于 occurrence ledger 和 localization 完成之后
  - 只允许修改 coverage、boundary consumption 和 terminal edge
  - 不得修改 occurrence ledger
```

本轮主闭环先依赖 **L2-post** 跑通。

**L2-pre 不得从文档中删除或无限期搁置。** 本计划单独设置 `P9：pre-ledger consensus policy`，明确其插入位置、数据契约、测试和验收。P9 不阻塞第一轮优化框架 smoke run，但属于 v3 后续必做阶段。

### 1.2 可进化注册词表在本轮完成，拆成两个独立步骤

本轮必须完成注册词表改造，但拆成：

```text
P3A：版本化 baseline registry
P3B：在线可进化 DSL / RegistryEdit
```

P3A 先把当前冻结常量集迁移为单一权威、版本化、可序列化、可 hash 的 registry，并保持 baseline 行为完全一致。

P3B 再实现模型可提议的新 predicate DSL 和固定算子的参数化动作实例。P3B 不允许跳过 P3A，也不得把自由文本逻辑交给 runtime 执行。

### 1.3 Promotion Gate 使用双维状态

不得使用一个 `decision_level` 同时表达证据、激活范围和保护状态。

统一拆为：

```text
EvidenceGrade：
  strong / weak / conditional / reject / skip

ActivationState：
  pending / working / deployable / disabled
```

推荐映射：

| EvidenceGrade | ActivationState | 连续进化中生效 | 独立 detector 部署 | 可修改/删除 |
|---|---|---:|---:|---:|
| conditional | pending | 否，仅等待验证或 shadow | 否 | 是 |
| weak | working | 是 | 否 | 是 |
| strong | deployable | 是 | 是 | 默认否 |
| reject | 无 | 否 | 否 | 不适用 |
| skip | 无 | 否 | 否 | 不适用 |

### 1.4 Meta Review 在一个完整宏迭代中闭环交付

Meta Review 不拆成相隔很远的多个半成品阶段。

本计划设置一个完整的 `P7：Meta Review 完整闭环迭代`，内部允许多个小提交和 bugfix，但在整套机制通过前：

- 不把部分 Meta 行为宣称为完成；
- 不允许不完整 Meta profile 自动影响 production gate；
- 不允许只完成 memory 就结束；
- 不允许只加入几条 guidance token 就称为 Meta 自进化。

P7 必须一次性覆盖：

```text
MetaSkillState
MetaEdit
GateExperience
OutcomeValidation
生成指导
候选排序
效率经验
gate profile
漂移检测
同步回滚
checkpoint / resume / inheritance
```

### 1.5 GateEvent 与 prompt feedback 分离

```text
GateEvent
  - 精确内部审计
  - 包含完整结构化 reason、execution trace 和 decision
  - 不直接进入 prompt

GateFeedbackProjection
  - bounded
  - closed vocabulary
  - identity-free
  - gold-free
  - generation_hint_only
```

### 1.6 OutcomeValidation 和 rollback 完成后，Meta 才能自动切换 gate profile

在 P7 内部也必须遵循依赖顺序：

```text
GateExperience
  -> OutcomeValidation
  -> shadow Meta recommendation
  -> drift / rollback ready
  -> automatic profile switching
```

没有独立验证和可用回滚时，Meta 最多输出 recommendation，不得改变实际 gate profile。

---

## 2. 三层架构与通用晋升机制

### 2.1 目标结构

```text
┌────────────────────────────────────────────────────────────┐
│ Meta Review / Meta skill                                   │
│ - 生成指导                                                  │
│ - 候选排序                                                  │
│ - gate profile                                              │
│ - 误拒/误接后验评估                                         │
│ - 总结效率                                                  │
│ - 漂移、成本和回滚                                          │
│ authority = evolution_guidance_only                         │
└──────────────────────────┬─────────────────────────────────┘
                           │
              指导 L1/L2 如何进化，不直接裁决案例
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ Promotion Gate                                             │
│ - hard gate                                                │
│ - evidence table                                           │
│ - EvidenceGrade                                            │
│ - ActivationState                                          │
│ - pending / working / deployable                            │
└───────────────┬───────────────────────────────┬────────────┘
                │                               │
┌───────────────▼──────────────┐  ┌────────────▼──────────────┐
│ L1 provider-rule skill       │  │ L2 adjudication policy    │
│ - 8 slot-rule modules        │  │ - L2-pre                  │
│ - ActionSpec                 │  │ - L2-post                 │
│ - provider prompt injection  │  │ - deterministic evaluator │
└───────────────┬──────────────┘  └────────────┬──────────────┘
                └──────────── detection ────────┘
```

### 2.2 权限边界

#### L1 可以改变

- provider prompt 中的 slot-rule；
- 候选、证书、证据、边界相关 provider 输出；
- add / modify / remove rule。

#### L1 不可以改变

- proof court 基础语义；
- L2 registry 的执行语义；
- gold；
- Promotion Gate 硬门；
- Meta profile。

#### L2 可以改变

- 注册词表内合法 policy item；
- L2-pre 或 L2-post 对应的确定性裁决边；
- add / modify / remove policy item。

#### L2 不可以改变

- provider prompt；
- 任意自由 verdict；
- 未注册 operator；
- raw provider response；
- gold；
- Meta profile。

#### Meta Review 可以改变

- 闭词表 guidance token；
- 候选排序 preference；
- 离散 gate profile；
- 注册词表使用指导；
- 后验评估与回滚建议。

#### Meta Review 不可以改变

- detector verdict；
- occurrence ledger；
- ActionSpec 合法性；
- vocabulary operator 的执行语义；
- gold firewall；
- hard gate。

---

## 3. 全局实施纪律

### 3.1 必须遵守

- [ ] 每个阶段先读目标文件及现有测试，再改代码。
- [ ] 每个阶段保持可运行、可测试、可回滚。
- [ ] 每个阶段使用独立提交；禁止把多个机制改动塞进一个无法审查的提交。
- [ ] 所有 runtime semantics 必须由确定性代码拥有。
- [ ] 模型只能选择或提议受约束候选，不得执行 policy。
- [ ] gold 只能进入 polarity、fitness credit、development validation 和最终统计。
- [ ] gold、case identity、完整 evaluator rationale 不得进入 L1/L2/Meta prompt。
- [ ] unknown schema / predicate / action / operator / parameter 一律 fail-closed。
- [ ] 任何新 provider 调用必须说明 purpose、上限、计数和成本。
- [ ] 新实验排除 `experiments/protocols/used_case_ledger.txt` 中全部 case。
- [ ] final holdout 只读，不得写回 genome、memory、Meta、registry 或 profile。
- [ ] `python -m pytest -q tests` 作为默认 SEHD 测试入口；不得使用无约束全仓 collection 代替。
- [ ] 开始执行前重新读取最新 `main`，记录实际 HEAD；文档中的旧行号仅作导航，不作硬编码依据。

### 3.2 禁止事项

- [ ] 禁止因为接受率低而直接降低硬门。
- [ ] 禁止用旧 50 例的接受数量作为阶段通过标准。
- [ ] 禁止通过修改单一浮点阈值制造“改进”。
- [ ] 禁止给 L2 增加通用 `rewrite_verdict`。
- [ ] 禁止把 Meta 注册为第九个 L1 module。
- [ ] 禁止把 Meta 变成 detector 裁决器。
- [ ] 禁止持久化 Python function 对象。
- [ ] 禁止让 registry 同时以两份权威内容存在于 policy artifact 和 experience memory。
- [ ] 禁止把 conditional 项静默加入 active runtime policy。
- [ ] 禁止用 canary 单独证明 correctness。
- [ ] 禁止用 final holdout 结果更新 Meta Review。
- [ ] 禁止在 P9 前从 post-ledger hook 恢复 consensus-blocked certificate。

---

## 4. 阶段依赖总览

```text
P0  冻结代码与闭环基线
  -> P1 统一 Promotion Contract
  -> P2 安全 L2-post 执行面
  -> P3A 版本化 baseline registry
  -> P3B 在线可进化 DSL / RegistryEdit
  -> P4 分级晋升与 working/deployable 视图
  -> P5 GateEvent 与模糊反馈
  -> P6 Meta Review 前置持久化准备
  -> P7 Meta Review 完整闭环迭代
  -> P8 优化框架端到端跑通
  -> P9 L2-pre consensus policy
  -> P10 连续运行与正式评测
```

阶段约束：

- P1 未完成前不得修改 gate 的实际行为。
- P2 未完成前不得把 boundary action 加入 policy planner。
- P3A 未完成前不得在线注册新 DSL。
- P3B 未完成前不得声称 L2 vocabulary 可进化。
- P4 未完成前不得让 weak/conditional 项进入不明确的部署状态。
- P5 未完成前不得向 L1 注入 gate reason。
- P6 未完成前不得让 Meta state 参与 checkpoint。
- P7 未完整通过前不得自动切 gate profile。
- P8 未跑通前不得启动规模实验。
- P9 未完成前 consensus restore 只能记录为 engine-level route unavailable。
- P10 final holdout 始终最后执行。

---

# 5. P0：冻结当前代码和闭环基线

## 5.1 目标

不关注旧实验指标，建立优化框架实施前的可重放代码基线。

## 5.2 任务清单

- [ ] 获取执行时最新 `main` HEAD。
- [ ] 记录 Python 版本、OS、detector config、provider route config。
- [ ] 运行：
  ```powershell
  python -m pytest -q tests
  ```
- [ ] 记录实际测试数量和失败详情，不硬编码“542”。
- [ ] 识别工作区已有非本任务修改，不覆盖、不回滚。
- [ ] 新增匿名离线 fixture：
  - [ ] true consensus blocked + terminal unresolved；
  - [ ] consensus blocked 但已有终态，必须不归因到 L2；
  - [ ] boundary verified_clean + terminal unresolved；
  - [ ] boundary conflict + surviving error；
  - [ ] hollow prosecution；
  - [ ] hollow defense；
  - [ ] L1 structural reject；
  - [ ] L1 compiled/injected/applied 但 no-effect；
  - [ ] L1 accepted local movement；
  - [ ] runtime-invalid；
  - [ ] checkpoint 在 observation/proposal/outcome 三个位置中断。
- [ ] 新增端到端 mocked provider fixture，能够模拟：
  - L1 proposal；
  - L2 proposal；
  - replay；
  - canary；
  - checkpoint/resume。
- [ ] 建立最小 usage ledger 字段，即使成本汇总后续再完善。

## 5.3 建议文件

```text
tests/fixtures/meta_skill_evolution.py
tests/test_failure_classifier.py
tests/test_evolution_hardening.py
tests/test_evolution_standalone.py
experiments/analyze_evolution_run.py
src/sehd_v6/core/provider_usage.py
```

## 5.4 验收

- [ ] 所有 fixture 不调用外部 API。
- [ ] fixture 不含真实 case ID、完整原始轨迹或 gold prompt。
- [ ] 当前 production behavior 不变。
- [ ] 当前 failure attribution 正反案例可稳定复现。
- [ ] interrupted 与 uninterrupted fixture 有可比较产物。
- [ ] usage ledger 能区分 logical call 和 transport attempt。
- [ ] 完整 SEHD 测试通过。

## 5.5 提交

```text
test(evolution): freeze optimized-loop contracts
```

---

# 6. P1：统一 Promotion Contract

## 6.1 目标

建立 L1/L2 共用的晋升数据模型，但保持当前行为兼容。

## 6.2 新增文件

```text
src/sehd_v6/core/bccse/promotion_contract.py
tests/test_promotion_contract.py
```

## 6.3 数据结构

```python
EvidenceStatus = positive | neutral | negative | unavailable

EvidenceDimension:
    structure
    ownership
    execution
    local_effect
    terminal_effect
    gold_polarity
    independent_effect
    non_regression
    historical_support
    cost_efficiency

EvidenceGrade:
    strong
    weak
    conditional
    reject
    skip

ActivationState:
    pending
    working
    deployable
    disabled

PromotionDecision:
    schema_version
    layer
    artifact
    legacy_decision
    evidence_table
    evidence_grade
    activation_state
    hard_reject_codes
    quality_reason_codes
    decision_authority
```

## 6.4 hard gate

以下始终 reject：

- [ ] invalid schema；
- [ ] unknown vocabulary/operator；
- [ ] invalid parameter；
- [ ] ownership mismatch；
- [ ] ActionSpec invalid；
- [ ] gold/case/prompt leakage；
- [ ] detector authority invalid；
- [ ] replay runtime invalid；
- [ ] gold polarity regression；
- [ ] canary regression；
- [ ] protected baseline violation；
- [ ] unknown execution authority。

## 6.5 兼容映射

- [ ] 当前 L1 decision 映射到 PromotionDecision。
- [ ] 当前 L2 decision 映射到 PromotionDecision。
- [ ] `legacy_decision` 原样保留。
- [ ] 映射逻辑只有一个 owner。
- [ ] audit、memory、runner 不得二次推导 grade。

## 6.6 验收

- [ ] P0 fixture 下输出行为 byte-identical。
- [ ] 一个 legacy decision 只有一个确定映射。
- [ ] hard reject 不可被 profile 覆盖。
- [ ] 不增加 provider call。
- [ ] 完整测试通过。

## 6.7 提交

```text
refactor(evolution): unify promotion evidence and activation contracts
```

---

# 7. P2：安全补齐 L2-post 执行面

## 7.1 目标

先完成可安全落地的 post-ledger correction，让优化框架具备真实 L2 terminal-edge 修复能力。

## 7.2 第一版动作

```text
consume_verified_clean_boundary
```

前提必须同时满足：

- [ ] `boundary_path_state == verified_clean`；
- [ ] `boundary_authority_valid == true`；
- [ ] `terminal_state == unresolved`；
- [ ] `surviving_error_count == 0`；
- [ ] boundary 来源是既有确定性 path；
- [ ] action 不修改 occurrence ledger；
- [ ] action 记录 before/after、policy item、authority。

## 7.3 新增/扩展 facts

```text
boundary_path_state
boundary_authority_valid
terminal_state
surviving_error_count
policy_stage
```

## 7.4 consensus 当前处理

P9 前：

```text
consensus_gate_blocked + terminal unresolved
  -> route_result = engine_level_intervention_required
  -> feedback_code = policy_route_unavailable
  -> 不生成 post-ledger restore action
```

## 7.5 route result taxonomy

替换统一的 `no_executable_policy_for_attribution`：

```text
no_registered_runtime_action
runtime_precondition_not_satisfied
candidate_blocked_by_protection
candidate_already_present
engine_level_intervention_required
```

## 7.6 修改文件

```text
src/sehd_v6/core/bccse/v6_contracts.py
src/sehd_v6/core/bccse/policy_reflection.py
src/sehd_v6/core/paca_tcl/policy_evaluator.py
src/sehd_v6/core/paca_tcl/proof_contract.py
tests/test_policy_evaluator.py
tests/test_proof_court_policy_hook.py
tests/test_policy_reflection.py
```

`proof_engine.py` 只有在实际调用链确认需要透传时才修改，不得为了匹配旧计划而无意义改动。

## 7.7 验收

- [ ] empty policy 保持 baseline 行为。
- [ ] boundary action 可实现合法 `unresolved -> verified_clean`。
- [ ] surviving error 非空时 fail-closed。
- [ ] occurrence ledger byte-identical。
- [ ] telemetry-only 不改变 terminal。
- [ ] consensus 不被 post hook 恢复。
- [ ] 每个 L2 attribution 都有结构化 route result。
- [ ] 不以 accept 数量作为验收条件。

## 7.8 提交

```text
feat(evolution): add safe post-ledger boundary policy
```

---

# 8. P3A：版本化 baseline registry

## 8.1 目标

将当前 `L2_PREDICATES`、`L2_ACTIONS`、`ACTION_PARAMS` 从散落常量升级为单一权威 registry，同时保持行为完全一致。

## 8.2 新增文件

```text
src/sehd_v6/core/bccse/vocabulary_registry.py
tests/test_l2_registry.py
```

## 8.3 Registry 数据模型

```text
VocabularyRegistry
├── schema_version
├── facts_schema_version
├── compiler_version
├── predicates
├── action_operators
├── action_instances
└── audit
```

### PredicateEntry

```text
name
canonical_ast
canonical_dsl
facts_whitelist
compiled_digest
registered_generation
status = baseline / registered / disabled
```

### ActionOperator

```text
operator
params_schema
policy_stage = pre_ledger / post_ledger
runtime_owner
```

### ActionInstance

```text
name
operator
params
registered_generation
status
```

### RegistryEdit

```text
edit_id
operation
target
before_hash
after_hash
reason_codes
sequence
```

## 8.4 baseline 迁移

- [x] 现有 predicate 成为 baseline entries。
- [x] 现有 action 成为 baseline operator/instances。
- [x] planner、genome validator、evaluator 都从 registry 读取。
- [x] unknown entry 继续 fail-closed。
- [x] baseline entries 不可删除。
- [x] disabled entry 不可被 policy item 引用。

## 8.5 权威所有权

Registry 唯一权威随 `PolicyGenome` 持久化：

```text
policy_genome.json
├── items
├── registry
└── registry_audit
```

Experience memory 只保存：

```text
registry usage experience
registration outcome
unknown skip statistics
```

不得保存第二份权威 registry。

## 8.6 hash

`PolicyGenome.content_hash` 必须覆盖：

- [x] policy items 的 canonical 语义；
- [x] registry active canonical state；
- [x] registry status；
- [x] compiler version；
- [x] facts schema version。

不得把运行统计、usage count 或时间戳纳入 content hash。（已实现：hash 仅覆盖 items + registry 序列化状态，registry.to_dict 含 compiler/facts schema version 锚。）

## 8.7 验收

- [x] baseline evaluator 行为 byte-identical。
- [x] empty policy byte-identical。
- [x] registry round-trip canonical equality。
- [x] registry hash 稳定。
- [x] unknown/disabled entry fail-closed。
- [x] manifest/inheritance 能绑定含 registry 的 policy hash。
- [x] 不新增 provider call。（P3A 未新增；P3B 引入 registry_reflection 复用 mutation route。）

## 8.8 提交

```text
refactor(evolution): centralize versioned L2 vocabulary registry
```

---

# 9. P3B：在线可进化 DSL 与 RegistryEdit

## 9.1 目标

在本轮完成可进化词表，而不是停留在静态 registry。

## 9.2 Predicate DSL 最小语法

允许：

```text
comparison:
  field == value
  field != value
  field < value
  field <= value
  field > value
  field >= value
  field in [value, ...]

boolean:
  and
  or
  not
  parentheses
```

禁止（全部由 AST 白名单在编译期强制，见 `vocabulary_registry._validate_dsl_node`）：

- [x] 函数调用；
- [x] 属性穿透；
- [x] 任意 Python expression；
- [x] provider raw text；
- [x] gold；
- [x] identity；
- [x] URL/path；
- [x] 动态 import；
- [x] regex 第一版；
- [x] 新数据源。

## 9.3 编译

```text
DSL source
  -> parse
  -> canonical AST
  -> whitelist validation
  -> type validation
  -> pure predicate compiler
  -> compiled digest
```

artifact 只保存 canonical AST/DSL 和 digest；加载时重新确定性编译。

## 9.4 固定 action operator

本轮 operator 集合冻结：

```text
lower_coverage
invalidate_coverage
set_terminal_unresolved
emit_telemetry
consume_boundary
```

`restore_audited_consensus` 只在 P9 的 pre-ledger operator 中注册。

模型只能注册固定 operator 的参数化实例，不能创建新 operator 语义。

## 9.5 RegistryEdit 生成路径

新增独立、受约束的 registration planner：

```text
FailureAttribution
  -> 当前 registry coverage
  -> deterministic registration candidate templates
  -> 模型选择 candidate_index 或 no_proposal
  -> RegistryEdit validation
  -> shadow compile
  -> replay
  -> PromotionDecision
```

第一版不允许模型直接返回自由 DSL 文本并立即执行。模型提议必须经过：

1. 受约束 schema；
2. canonical parser；
3. whitelist；
4. shadow replay；
5. Promotion Gate。

## 9.6 首次注册状态

新 registry entry：

```text
EvidenceGrade = conditional
ActivationState = pending
```

只有通过 replay 和独立 development validation 后，才能：

```text
working
```

只有强证据后才能：

```text
deployable
```

## 9.7 provider 调用

- [ ] 明确 `purpose=registry_reflection`。
- [ ] 记录 logical call。
- [ ] 设置每个 case 最大注册提议次数。
- [ ] candidate 数为 1 时不额外启用 critic。
- [ ] 可复用现有 mutation route，但使用独立 cache namespace。

## 9.8 建议文件

```text
src/sehd_v6/core/bccse/vocabulary_registry.py
src/sehd_v6/core/bccse/registry_reflection.py
src/sehd_v6/core/bccse/policy_genome.py
src/sehd_v6/core/bccse/evolution_runner.py
tests/test_l2_registry.py
tests/test_registry_reflection.py
tests/test_registry_evolution.py
```

## 9.9 验收

- [x] 合法 DSL 编译为 pure function。
- [x] 越权字段在编译期 fail-closed。
- [x] 非法语法不注册。
- [x] unknown operator 不注册。
- [x] 参数 schema 严格校验。
- [x] 新 entry 首次只能 pending。
- [x] pending entry 不进入 active evaluator。
- [x] shadow replay 可 checkpoint/resume。（registry 随 policy_genome 持久化于 checkpoint，resume 经 from_dict 重编译 DSL 恢复。）
- [x] 注册、禁用、恢复全部有 RegistryEdit audit。（apply_registry_edit 每次操作追加 RegistryEdit，hash 链 before/after 可验证。）
- [x] registry lineage/hash 正确。
- [x] 不允许自由文本裁决逻辑。（模型仅能选择预计算注册候选或弃权；DSL 经 AST 白名单编译。）

## 9.10 提交

```text
feat(evolution): evolve audited L2 predicate and action vocabulary
```

---

# 10. P4：分级晋升与 working/deployable 视图

## 10.1 目标

让分级门控真正改变 runtime scope，而不是只多一个标签。

## 10.2 PolicyGenome 改造

新增：

```python
items_for_scope("working")
items_for_scope("deployable")
```

修改保护语义：

```text
baseline -> protected
strong + deployable -> protected
weak + working -> 可 modify/remove
conditional + pending -> 不在 items 中
```

## 10.3 pending queue

```text
PendingValidationItem
├── subject_id
├── layer
├── proposal
├── registry dependencies
├── source evidence
├── required validation
├── created_sequence
└── status
```

物理上随 checkpoint 和 experience memory 持久化，但不参与 detector runtime。

## 10.4 决策规则

### strong / deployable

满足：

- [ ] hard gate 全通过；
- [ ] execution established；
- [ ] 正向独立效果或 gold polarity improvement；
- [ ] 无 canary regression；
- [ ] 无 independent regression。

### weak / working

允许：

- [ ] coverage-only movement；
- [ ] label-free、可解释的安全 terminal movement；
- [ ] 新 policy 有确定性 local effect；
- [ ] canary 保留；
- [ ] 尚无足够独立 correctness evidence。

### conditional / pending

- [ ] telemetry-only；
- [ ] 新 registry entry 首次使用；
- [ ] effect 暂不可观测；
- [ ] 需要后续独立验证；
- [ ] shadow Meta/registry candidate。

### reject

仅硬负信号或确定性无效。

## 10.5 Canary 纪律

Canary 只可作为：

```text
non_regression evidence
hard veto
```

不得单独把 weak 升级为 strong。

## 10.6 修改文件

```text
src/sehd_v6/core/bccse/policy_genome.py
src/sehd_v6/core/bccse/evolution_runner.py
src/sehd_v6/core/paca_tcl/detector.py
src/sehd_v6/evolution/runner.py
tests/test_graded_gate.py
tests/test_policy_genome.py
```

## 10.7 验收

- [ ] weak 可修改/删除。
- [ ] strong 默认不可修改。
- [ ] conditional 不进入 active items。
- [ ] evolution 使用 working view。
- [ ] standalone detector 默认使用 deployable view。
- [ ] pending queue resume 幂等。
- [ ] registry dependencies 在 scope projection 时校验。
- [ ] hard violation 永远 reject。
- [ ] 完整测试通过。

## 10.8 提交

```text
refactor(evolution): separate evidence grade from policy activation
```

---

# 11. P5：GateEvent 与双向模糊反馈

## 11.1 目标

建立 L2→L1 和 gate→generation 的统一反馈通道，同时防止 L1 hack gate。

## 11.2 新增文件

```text
src/sehd_v6/core/bccse/gate_feedback.py
tests/test_gate_feedback.py
```

## 11.3 GateEvent

```text
schema_version
event_id
subject_id
episode_id
attempt_id
layer
artifact
operation
stage
legacy_decision
evidence_grade
activation_state
hard_reject_codes
quality_reason_codes
execution_trace_class
observable_delta_class
downstream_delta_class
sequence
```

## 11.4 Event ID

优先身份：

```text
有 proposal -> attempt_id
无 proposal -> episode_id + decision kind
runtime event -> deterministic observation_id
```

`sequence` 只排序，不参与稳定身份。

## 11.5 模糊反馈闭词表

第一版：

```text
insufficient_grounding
execution_not_established
independent_regression_risk
scope_or_ownership_mismatch
unsafe_or_unrepresentable
redundant_intervention
policy_route_unavailable
candidate_quality_inconclusive
registry_entry_pending
downstream_effect_not_established
```

## 11.6 两种投影

### same-source retry

可包含较精确的闭词表状态：

```text
compiled_not_applied
applied_without_local_effect
local_effect_without_downstream_effect
```

### cross-case guidance

只包含模糊抽象 code，不暴露阈值、gold polarity 或完整判据。

## 11.7 接线

L1：

```text
FailureEpisode
  -> mechanism history
  -> bounded GateFeedbackProjection
  -> causal reflection
```

L2：

```text
planner candidates
  -> bounded GateFeedbackProjection
  -> candidate index selection
```

## 11.8 验收

- [ ] 每个 terminal gate decision 都有 GateEvent。
- [ ] no-proposal/skip 也有 GateEvent。
- [ ] resume 不重复写同一 event。
- [ ] feedback 无 raw identity、gold、prompt、provider response。
- [ ] feedback 不改变 owner route。
- [ ] feedback 不放宽 ActionSpec/StructuralGate。
- [ ] same-source 和 cross-case 投影严格分离。
- [ ] 不增加 provider call。

## 11.9 提交

```text
feat(evolution): add audited gate events and bounded feedback
```

---

# 12. P6：Meta Review 前置持久化准备

## 12.1 目标

在 P7 一次性完善 Meta Review 前，先完成不会改变行为的 schema 和 checkpoint 基础，避免在 Meta 迭代中同时处理底层持久化 bug。

## 12.2 memory schema

```text
sehd.evolution_experience_memory.v1 -> v2
checkpoint v4 -> v5
```

## 12.3 memory v2 预留子域

```text
meta_review
├── gate_experiences
├── outcome_validations
├── meta_state
├── pending_validation_queue
├── efficiency_profile
├── drift_snapshots
└── dedup_ledgers
```

初始全部为空，P6 不影响 generation 或 gate。

## 12.4 修改文件

```text
src/sehd_v6/core/bccse/evolution_experience_memory.py
src/sehd_v6/core/bccse/failure_episode.py
src/sehd_v6/core/bccse/evolution_runner.py
src/sehd_v6/evolution/state_manifest.py
src/sehd_v6/evolution/runner.py
src/sehd_v6/evolution/__main__.py
tests/test_meta_skill_memory.py
```

## 12.5 迁移纪律

- [ ] v1 只在 `from_dict()` 单一边界迁移为 v2。
- [ ] legacy type-only 只允许显式受支持的迁移。
- [ ] unknown schema `ValueError`。
- [ ] checkpoint v4 加默认空字段后转为 v5 内存格式。
- [ ] 下一次持久化只写 v5。
- [ ] runner 不保留散落的 v1/v2 双轨分支。

## 12.6 验收

- [ ] v1→v2 round-trip canonical equality。
- [ ] checkpoint v4→v5 可加载。
- [ ] unknown schema 拒绝。
- [ ] interrupted/uninterrupted memory 规范化一致。
- [ ] inherit_from 恢复空 Meta 子域。
- [ ] stable rollback payload 覆盖 Meta 子域。
- [ ] 不增加模型调用。
- [ ] production behavior 不变。

## 12.7 提交

```text
refactor(evolution): prepare persistent meta-review state
```

---

# 13. P7：Meta Review 完整闭环迭代

> 本阶段作为一个完整宏迭代交付。允许内部小提交和 bugfix，但不得在子机制不完整时宣称 Meta Review 已完成。

## 13.1 目标

一次性完善“门控如何自我审查、如何指导更好 skill、如何提高总结效率、如何后验纠错、如何漂移回滚”的完整机制。

## 13.2 新增核心文件

```text
src/sehd_v6/core/bccse/meta_review.py
src/sehd_v6/core/bccse/meta_skill.py
tests/test_meta_review.py
tests/test_meta_skill.py
```

## 13.3 MetaSkillState

```text
schema_version
guidance_tokens
ranking_preferences
gate_profile
generation
parent_hash
content_hash
edits_applied
audit
```

## 13.4 MetaEdit

第一版操作：

```text
add_guidance_token
remove_guidance_token
enable_ranking_preference
disable_ranking_preference
switch_gate_profile
```

禁止：

- 自由文本 gate rule；
- 任意数值阈值；
- 修改 hard gate；
- 修改 detector verdict；
- 创建新 operator；
- 直接创建任意 PolicyItem。

## 13.5 GuidanceProfile 闭词表

```text
bind_action_to_owned_output
prefer_observable_discriminator
reuse_effective_action_shape
avoid_unexecuted_action_shape
avoid_downstream_invariant_surface
require_independent_anchor
prefer_less_destructive_policy
seek_distinct_candidate_direction
prefer_registered_runtime_route
avoid_pending_registry_dependency
```

## 13.6 RankingPreference 闭词表

```text
owned_output_match
action_shape_previously_effective
action_shape_previously_no_effect
observable_field_available
downstream_edge_reachable
independent_anchor_available
destructive_scope
duplicate_risk
registry_stability
cost_efficiency
```

## 13.7 GateExperience

```text
gate_event_id
mechanism_signature
layer
artifact
stage
evidence_grade
activation_state
feedback_codes
execution_trace_class
follow_up_status
validation_source
sequence
```

## 13.8 OutcomeValidation

```text
supports_accept
contradicts_accept
supports_reject
exact_false_reject
gate_class_counterevidence
inconclusive
```

### exact_false_reject

必须满足：

- [ ] 同一 canonical intervention；
- [ ] 相容的 condition discriminator；
- [ ] 独立 development validation case/probe；
- [ ] positive effect；
- [ ] 无 regression；
- [ ] validation event 幂等。

### gate_class_counterevidence

同机制但不同 intervention，只能表明某类 gate 可能过严，不能证明原提案被误拒。

### 禁止作为 false reject 证据

- 同 source retry；
- 同 prompt 再采样；
- telemetry-only；
- 接受率低；
- 模型 rationale；
- final holdout 回写。

## 13.9 Meta 候选生成

第一版优先使用确定性候选枚举：

```text
GateExperience + OutcomeValidation
  -> deterministic MetaEdit candidates
  -> shadow recommendation
  -> validation
  -> commit / rollback
```

如使用模型，只允许从预计算 MetaEdit candidate index 中选择，并单独记录 `purpose=meta_review_selection`。

## 13.10 Gate profile

第一版：

```text
conservative
steady
```

### conservative

- 新 registry entry 首次只能 pending；
- label-free safe policy 最大 weak；
- hard gate 固定；
- canary veto。

### steady

只有满足预注册证据后：

- 已获独立支持的 intervention 可 strong；
- 有 exact_false_reject 证据的特定 gate class 可放宽质量 gate；
- hard gate 不变。

## 13.11 自动切换顺序

同一 P7 内部严格按以下子步骤：

```text
P7.1 GateExperience 聚合
P7.2 OutcomeValidation
P7.3 shadow Meta recommendation
P7.4 prompt guidance / candidate ranking
P7.5 drift snapshot
P7.6 synchronous rollback
P7.7 automatic profile switching
```

P7.7 前，实际 profile 不改变。

## 13.12 Prompt projection

唯一函数：

```python
project_meta_review_for_prompt(...)
```

要求：

- bounded；
- canonical；
- closed vocabulary；
- identity-free；
- gold-free；
- raw trajectory-free；
- provider response-free；
- `authority="evolution_guidance_only"`。

## 13.13 L1 接入

```text
FailureEpisode
  -> mechanism history
  -> GateFeedbackProjection
  -> Meta guidance
  -> existing reflection prompt
```

Meta guidance 不替代：

- allowed_modules；
- module charter；
- ActionSpec；
- rule slots；
- StructuralGate。

## 13.14 L2 接入

```text
planner 枚举 PolicyEdit / RegistryEdit
  -> Meta ranking features
  -> 模型 candidate_index / no_proposal
  -> validation
  -> replay
  -> PromotionDecision
```

Meta 不扩大候选集合。

## 13.15 总结效率

记录：

```text
attempts_to_accept
mutation_calls_to_accept
source_replays_to_accept
policy_replays_to_accept
latency_to_accept
tokens_to_accept
cost_to_accept
```

效率用于排序和报告，不作为单一自动放行阈值。

## 13.16 DriftSnapshot

```text
feedback_code_distribution
evidence_grade_distribution
activation_state_distribution
false_reject_count
unsupported_accept_count
runtime_invalid_rate
attempts_to_accept
mutation_calls_per_supported_edit
tokens
latency
cost_status
registry_size
new_registration_rate
unknown_vocabulary_skip_rate
```

综合 drift index 只用于报告，不可作为黑箱放行依据。

## 13.17 同步回滚

回滚必须同时恢复：

```text
L1 working/deployable hashes
L2 working/deployable hashes
registry hash
experience memory hash
MetaSkillState hash/profile
pending validation queue
dedup ledger
```

## 13.18 成本审计

每个 request：

```text
logical_call_id
request_hash
transport_attempt_id
purpose
route/model
cache_hit
transport_attempts
prompt_tokens
completion_tokens
reasoning_tokens
latency_seconds
repair_used
provider_billed_usage
```

实际成本按 transport/billed usage 计算，不能只按 request hash 去重。

## 13.19 P7 验收

### 功能

- [ ] MetaSkillState 有稳定 content hash 和 lineage。
- [ ] MetaEdit 可增删 guidance 和 ranking preference。
- [ ] GateExperience 与 OutcomeValidation 闭环。
- [ ] exact false reject 和 class counterevidence 分离。
- [ ] Meta guidance 可注入 L1/L2。
- [ ] Meta 排序不扩大候选集合。
- [ ] efficiency profile 可记录。
- [ ] drift snapshot 可生成。
- [ ] profile 可在满足证据后切换。
- [ ] profile 无证据不切换。
- [ ] rollback 同步完整。

### 安全

- [ ] Meta 不修改 verdict。
- [ ] Meta 不绕过 hard gate。
- [ ] Meta 不修改 ActionSpec。
- [ ] Meta 不创建任意 policy。
- [ ] Meta 不创建 operator。
- [ ] prompt 无 identity/gold 泄漏。
- [ ] final holdout 不写回。

### 持久化

- [ ] resume 幂等。
- [ ] inheritance hash 完整。
- [ ] interrupted/uninterrupted normalized equality。
- [ ] rollback 后所有 state hash 恢复。
- [ ] bugfix 后全量测试继续通过。

### 成本

- [ ] 新增调用都有 purpose 和上限。
- [ ] logical/fresh transport 分离。
- [ ] cache 和 retry 正确计数。
- [ ] 无价格表时 `cost_status=unavailable`。
- [ ] 不用假 0 代表未知成本。

## 13.20 提交建议

本阶段可使用多个内部提交：

```text
feat(meta): add meta-review state and edits
feat(meta): close outcome-validation loop
feat(meta): guide and rank L1/L2 evolution
feat(meta): add drift, cost and synchronized rollback
fix(meta): repair integrated meta-review findings
```

只有所有 P7 验收通过后，才标记宏阶段完成。

---

# 14. P8：优化框架端到端跑通

## 14.1 原则

不先跑大规模，不比较旧接受率。先证明优化后框架每条关键链路都能走完。

## 14.2 E0：离线契约闭环

使用 mocked provider 和匿名 fixture。

必须覆盖：

- [ ] L1 proposal -> replay -> weak/strong。
- [ ] L2-post boundary proposal -> replay -> weak。
- [ ] registry DSL proposal -> pending -> validation。
- [ ] GateEvent -> feedback -> 下一次 reflection 可见。
- [ ] Meta Review recommendation。
- [ ] OutcomeValidation。
- [ ] profile shadow。
- [ ] rollback。
- [ ] checkpoint/resume。
- [ ] inherit_from。
- [ ] deployable view 过滤 working/pending。

通过条件：

- [ ] 所有链路完成，无未捕获异常。
- [ ] 所有 artifact 可写入、读取、恢复。
- [ ] gold firewall 通过。
- [ ] empty policy baseline 不变。
- [ ] 不调用外部 API。

## 14.3 E1：全新少量 provider smoke run

使用全新 4–8 个案例，排除 used-case ledger。

目标不是覆盖数据分布，而是验证真实 provider 链路：

- [ ] 至少一条 L1 evolution path。
- [ ] 至少一条 L2-post path。
- [ ] 至少一条 registry proposal path。
- [ ] 至少一个 pending validation。
- [ ] 至少一个 GateEvent feedback 被后续消费。
- [ ] 至少一个 Meta Review audit。
- [ ] 正常 checkpoint。
- [ ] 人工中断后 resume。
- [ ] usage ledger 完整。

如果自然案例未触发某条路径，允许使用预注册的 targeted development case，但必须报告，不得伪装成自然分布结果。

通过条件：

- [ ] run 正常结束。
- [ ] detector/mutation/replay 调用都可对账。
- [ ] 无 gold/identity leak。
- [ ] no unknown schema。
- [ ] no silent conditional activation。
- [ ] no manifest mismatch。
- [ ] cost/time 可解释。

## 14.4 E2：新开发批次

在 E1 跑通后，使用全新 20–50 例 development batch。

开启：

```text
L1 evolution
L2-post
versioned registry
online RegistryEdit
graded promotion
feedback
Meta Review guidance
Meta Review shadow/validated profile
```

关闭：

```text
L2-pre consensus restore
final champion claim
final holdout
```

过程指标：

```text
run completion rate
runtime-invalid rate
route result completeness
proposal structural-valid rate
execution-established rate
working/deployable/pending distribution
registry compile/validation rate
GateEvent completeness
feedback consumption
Meta recommendation/action distribution
resume consistency
calls/tokens/cost per supported edit
```

通过条件：

- [ ] 完整跑完，不因 Meta/registry/schema 崩溃。
- [ ] 所有 decision 可追溯。
- [ ] pending 不进入 active runtime。
- [ ] deployable view 正确。
- [ ] checkpoint/resume 一致。
- [ ] rollback 可触发并恢复。
- [ ] gold/canary regression 为 0。
- [ ] 没有将内部 Meta 自评当成 detector improvement claim。
- [ ] 不要求接受数量超过旧实验。

## 14.5 P8 完成定义

只有 E0、E1、E2 均完成，才可声明：

```text
优化后的 SEHD v6 自进化框架已跑通
```

该声明只表示工程闭环完成，不表示最终检测性能提升。

---

# 15. P9：L2-pre consensus policy

> 本阶段是 A1 的强制后续设计，不得从 v3 文档中删除。

## 15.1 目标

在正确的 pre-ledger 位置实现经过审计的 consensus correction，避免 post-ledger 恢复造成 occurrence/localization 不一致。

## 15.2 插入位置

目标调用顺序：

```text
validate / defense / materiality
  -> apply_consensus_gate
  -> build PreLedgerPolicyFacts
  -> evaluate_pre_ledger_policies
  -> final surviving set
  -> occurrence_errors snapshot
  -> earliest-origin localization
  -> occurrence ledger
  -> base terminal
  -> L2-post
```

## 15.3 PreLedgerPolicyFacts

不得把 raw certificate 直接暴露给模型。确定性 runtime facts 至少包含：

```text
consensus_gate_applied
blocked_certificate_ids
blocked_certificate_audit_status
blocked_certificate_causal_status
blocked_certificate_impact_kind
blocked_certificate_defense_status
blocked_certificate_quarantine_status
blocked_certificate_origin_refs
terminal_without_restore
```

Reflection prompt 只获得摘要和预计算 candidate，不获得内部完整审计结构。

## 15.4 operator

```text
restore_audited_consensus
```

必须全部满足：

- [ ] certificate 通过 schema validation；
- [ ] ID 存在于 blocked set；
- [ ] causal status 是 causes/propagates incorrect result；
- [ ] impact 非 process-only；
- [ ] 未被 dominating defense 击败；
- [ ] 非 quarantined raw output；
- [ ] authority valid；
- [ ] action 进入 pre-ledger evaluator；
- [ ] 恢复后正常参与 localization；
- [ ] occurrence ledger 由最终 surviving set 自然产生。

## 15.5 注册

在 P3 registry 中注册：

```text
operator = restore_audited_consensus
policy_stage = pre_ledger
```

首次使用：

```text
conditional / pending
```

独立验证后：

```text
weak / working
```

强证据后：

```text
strong / deployable
```

## 15.6 代码文件

```text
src/sehd_v6/core/bccse/v6_contracts.py
src/sehd_v6/core/bccse/vocabulary_registry.py
src/sehd_v6/core/bccse/policy_reflection.py
src/sehd_v6/core/paca_tcl/policy_evaluator.py
src/sehd_v6/core/paca_tcl/proof_contract.py
tests/test_pre_ledger_consensus_policy.py
tests/test_proof_court_policy_hook.py
```

## 15.7 验收

- [ ] empty pre-policy byte-identical。
- [ ] 合法 restore 进入 surviving set。
- [ ] occurrence ledger 与 surviving set 一致。
- [ ] responsible event/localization 一致。
- [ ] defeated/quarantined/process-only 永不恢复。
- [ ] post-ledger evaluator 不执行 pre operator。
- [ ] pre-ledger evaluator 不执行 post operator。
- [ ] unknown stage fail-closed。
- [ ] gold firewall 保持。
- [ ] P8 端到端闭环加入一条 pre-ledger fixture。
- [ ] 新 provider smoke 中至少验证一次 targeted pre-ledger 路径后再启用自然 batch。

## 15.8 提交

```text
feat(evolution): add audited pre-ledger consensus policy
```

---

# 16. P10：连续运行与正式评测

P8/P9 完成后再启动。

## 16.1 E3：100+ 连续进化

检查点：

```text
25 / 50 / 75 / 100+
```

每次输出：

```text
L1 working/deployable hash
L2 working/deployable hash
registry hash
experience memory hash
Meta state/profile hash
pending queue size
decision distribution
OutcomeValidation distribution
drift snapshot
cost snapshot
```

通过条件：

- [ ] 无未捕获异常。
- [ ] resume 与 uninterrupted 一致。
- [ ] memory/queue 有容量上限。
- [ ] registry 无审计断裂。
- [ ] 无 gold/identity leak。
- [ ] forced rollback 可恢复。
- [ ] final holdout 封存。

## 16.2 E4：fair comparison

至少四组：

| 组 | L1 | L2 | Meta Review |
|---|---:|---:|---:|
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
- validation protocol；
- dataset split；
- registry baseline；
- profile baseline。

必须分开报告：

```text
pre-policy detector result
post-policy final result
L2 marginal effect
Meta effect on proposal quality
Meta effect on convergence
Meta effect on cost
```

不能把 L2 修改后的 final state 直接称为 L1 效果。

## 16.3 E5：sealed final holdout

打开前冻结全部组和 artifact。

Final holdout：

- [ ] read-only；
- [ ] 不继续 evolution；
- [ ] 不更新 memory；
- [ ] 不更新 Meta；
- [ ] 不切 profile；
- [ ] 不注册 vocabulary；
- [ ] 不回写 false reject；
- [ ] 不重试到满意结果；
- [ ] 只产生最终评测 artifact。

---

# 17. 测试矩阵

## 17.1 Promotion

```text
tests/test_promotion_contract.py
tests/test_graded_gate.py
```

覆盖：

- hard reject；
- conditional/pending；
- weak/working；
- strong/deployable；
- canary veto；
- gold regression；
- deployable projection；
- weak 可修改；
- strong protection。

## 17.2 Registry

```text
tests/test_l2_registry.py
tests/test_registry_reflection.py
tests/test_registry_evolution.py
```

覆盖：

- baseline byte-identical；
- canonical AST；
- whitelist；
- type validation；
- invalid DSL；
- operator/params；
- RegistryEdit audit；
- pending first use；
- hash/lineage；
- disabled entry；
- stage ownership。

## 17.3 L2-post

```text
tests/test_policy_evaluator.py
tests/test_proof_court_policy_hook.py
tests/test_policy_reflection.py
```

覆盖：

- boundary consume；
- surviving error reject；
- occurrence unchanged；
- no-route taxonomy；
- deterministic order；
- empty policy。

## 17.4 Feedback

```text
tests/test_gate_feedback.py
tests/test_feedback_loop.py
```

覆盖：

- stable event ID；
- no-proposal event；
- same-source/cross-case；
- prompt firewall；
- feedback consumption；
- owner/gate 不变。

## 17.5 Meta Review

```text
tests/test_meta_review.py
tests/test_meta_skill.py
tests/test_meta_skill_memory.py
```

覆盖：

- MetaSkillState；
- MetaEdit；
- GateExperience；
- OutcomeValidation；
- exact false reject；
- class counterevidence；
- shadow recommendation；
- guidance/ranking；
- profile switch；
- drift；
- rollback；
- cost；
- schema migration；
- resume/inherit。

## 17.6 L2-pre

```text
tests/test_pre_ledger_consensus_policy.py
```

覆盖：

- valid restore；
- defeated reject；
- quarantine reject；
- process-only reject；
- occurrence/localization consistency；
- stage separation；
- empty pre-policy。

## 17.7 端到端

```text
tests/test_evolution_standalone.py
tests/test_evolution_hardening.py
tests/test_framework_hardening_r1.py
```

覆盖：

- L1/L2/registry/Meta 完整闭环；
- pending/working/deployable；
- checkpoint/resume；
- inherit；
- rollback；
- manifest；
- usage ledger。

---

# 18. Definition of Done

只有以下全部满足，v3 才算完成。

## 18.1 框架跑通

- [ ] E0 离线完整闭环通过。
- [ ] E1 全新少量 provider smoke run 完成。
- [ ] E2 新 development batch 完成。
- [ ] L1 evolution path 跑通。
- [ ] L2-post path 跑通。
- [ ] RegistryEdit path 跑通。
- [ ] PromotionDecision 跑通。
- [ ] GateEvent/feedback 跑通。
- [ ] Meta Review 完整闭环跑通。
- [ ] checkpoint/resume/inherit/rollback 跑通。
- [ ] 调用/耗时/token/成本可对账。

## 18.2 架构

- [ ] L2-pre/post stage 明确。
- [ ] post 不修改 occurrence ledger。
- [ ] pre 设计和 P9 实施路径完整保留。
- [ ] Promotion Gate 不被误称为 L2 或 Meta。
- [ ] Meta 不进入 detector verdict。
- [ ] Registry 是单一权威。

## 18.3 安全

- [ ] gold firewall 全部通过。
- [ ] unknown schema/vocabulary/operator fail-closed。
- [ ] free-text runtime semantics 不存在。
- [ ] pending 不激活。
- [ ] working 不默认部署。
- [ ] canary 不作为 correctness authority。
- [ ] Meta 不绕过 hard gate。
- [ ] final holdout 只读。

## 18.4 持久化

- [ ] memory v1→v2。
- [ ] checkpoint v4→v5。
- [ ] registry hash/lineage 完整。
- [ ] Meta hash/lineage 完整。
- [ ] interrupted/uninterrupted 一致。
- [ ] rollback 同步恢复全部状态。

## 18.5 测试

- [ ] `python -m pytest -q tests` 全部通过。
- [ ] 无外部/归档测试树误计入 SEHD 基线。
- [ ] 每个新增 contract 有独立单元测试。
- [ ] 每个阶段有端到端 fixture。
- [ ] bugfix 必须附回归测试。

## 18.6 实验纪律

- [ ] 不以旧接受率作为通过门槛。
- [ ] 不复用 used-case ledger 案例做新开发结论。
- [ ] 不把框架内部自评分数当 detector improvement。
- [ ] 正式效果声明只来自独立评测。
- [ ] final holdout 最后打开。

---

# 19. DeepSeek 执行指令

## 19.1 首轮

DeepSeek 首轮只执行 P0：

1. 读取最新 `main` 和本计划；
2. 检查仓库状态；
3. 运行 SEHD-only tests；
4. 建立 fixture 和测试基线；
5. 输出当前代码与计划文件路径的差异；
6. 不运行真实 provider；
7. 不修改 P1 以后 production code；
8. 提交 P0。

## 19.2 后续阶段

每阶段必须输出：

```text
1. 修改文件
2. 新增/修改 contract
3. 测试命令和结果
4. 行为变化
5. 未变化的不变量
6. 新增 provider calls
7. 成本影响
8. checkpoint/schema 影响
9. 已知风险
10. 下一阶段前置条件
```

## 19.3 停止条件

出现以下任一情况必须停止进入下一阶段：

- 测试失败；
- baseline 行为非预期变化；
- gold/identity leak；
- occurrence ledger 不一致；
- unknown schema 被静默接受；
- conditional 被激活；
- Meta 改变 hard gate；
- registry 双权威；
- manifest/hash 不一致；
- rollback 不完整；
- 调用或成本无法对账。

---

# 20. 建议提交顺序

```text
test(evolution): freeze optimized-loop contracts
refactor(evolution): unify promotion evidence and activation contracts
feat(evolution): add safe post-ledger boundary policy
refactor(evolution): centralize versioned L2 vocabulary registry
feat(evolution): evolve audited L2 predicate and action vocabulary
refactor(evolution): separate evidence grade from policy activation
feat(evolution): add audited gate events and bounded feedback
refactor(evolution): prepare persistent meta-review state
feat(meta): add meta-review state and edits
feat(meta): close outcome-validation loop
feat(meta): guide and rank L1/L2 evolution
feat(meta): add drift, cost and synchronized rollback
fix(meta): repair integrated meta-review findings
test(evolution): validate optimized framework end to end
feat(evolution): add audited pre-ledger consensus policy
test(evolution): validate continuous self-evolution
```

---

# 21. 最终优先级

```text
最高优先级：
  优化后的框架能完整跑通

第二优先级：
  每条进化路径可审计、可恢复、可回滚

第三优先级：
  Meta Review 真正形成完整闭环

第四优先级：
  L2 vocabulary 在本轮实现可进化

第五优先级：
  pre-ledger consensus 作为明确后续阶段完成

最后：
  规模化效果评测和 final holdout
```

不得为了提前得到“更高接受率”打乱以上顺序。
