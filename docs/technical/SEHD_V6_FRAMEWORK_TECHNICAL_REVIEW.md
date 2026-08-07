# SEHD v6 自进化框架技术文档（V3 重构后全景审查）

> **文档性质**：对 V3 优化执行计划（`SEHD_V6_OPTIMIZED_EXECUTION_PLAN_V3.md`，P0–P10）全部代码重构完成后的**整体框架梳理、实现一致性核查、深层 review 结论与机制有效性评估**。
> **代码基线**：`main@cb4edd6` + 本轮 review 修复（见 §8）。
> **测试基线**：`python -m pytest -q tests` → **738 passed, 0 failed**（修复前后均通过）。
> **审查日期**：2026-08-07

---

## 1. 系统总览

SEHD v6 是一个**幻觉检测器自进化框架**。它对一个多阶段 LLM 检测流水线（PACA-TCL v3.5 proof court）进行在线进化，进化对象是两类受约束 artifact，而不是自由文本：

```text
原始轨迹
  └─ PACA-TCL v3.5 检测器（proof court 裁决）
       ├─ step_attributor   自监督触发信号（无 gold）
       ├─ failure_classifier 确定性归因（L1 / L2 / detector / none）
       │
       ├─ L1 路径：reflection_engine 反思 → SkillEdit（8 个模块的 slot-rule）
       │     → StructuralGate → 源 replay + canary + contrast → shadow batch commit
       │
       ├─ L2 路径：policy_reflection 从闭词汇候选中选择 → PolicyEdit
       │     → policy_evaluator 确定性 replay → gold 极性纪律 → working genome
       │
       ├─ Registry 路径：registry_reflection 注册新 predicate/action 实例
       │     → DSL 白名单编译 → pending（影子态，不可执行）
       │
       ├─ 所有终局 gate 决策 → GateEventLedger（P5）
       │     → GateFeedbackProjection（same-source 精确 / cross-case 模糊）
       │
       └─ 运行结束 → Meta Review 聚合（P7）
             → GateExperience / shadow MetaEdit / DriftSnapshot
             → 经验记忆持久化（meta_review 子域，单一权威）
```

**权限边界**（V3 §2.2，代码中实际强制）：

| 层 | 能改变 | 不能改变 |
|---|---|---|
| L1 | 8 个 provider-rule 模块的 slot-rule（condition/action/evidence_anchor） | proof court 语义、L2 registry、gold、hard gate、Meta profile |
| L2 | registry 闭词汇内的合法 policy item（add/modify/remove） | provider prompt、自由 verdict、未注册 operator、gold、Meta profile |
| Meta | 闭词汇 guidance token、ranking preference、离散 gate profile | detector verdict、occurrence ledger、ActionSpec 合法性、operator 语义、hard gate |

模型在每一层都只做**受约束选择**（candidate_index / no_proposal），所有 runtime 语义由确定性代码拥有——这是全框架最核心的不变量，已在代码中全面落实。

---

## 2. 代码结构与模块职责

### 2.1 包布局

```text
src/sehd_v6/
├── detector/                 # 独立部署入口（单轨检测 + batch）
│   ├── detector? -> core/paca_tcl/detector.py   detect_record()
│   ├── batch.py              # 批量检测、runtime_invalid 重试语义
│   └── replica_aggregation.py
├── evolution/                # 自进化公开 API
│   ├── runner.py             # run_evolution()：records→detector→evolution 闭环
│   ├── state_manifest.py     # 跨 run 继承的 sha256 绑定 manifest
│   └── __main__.py           # CLI
└── core/
    ├── paca_tcl/             # 检测引擎（proof court）
    │   ├── proof_engine.py       (1753 行) 编排：候选发现→控辩→consensus→定位→L2 hook
    │   ├── proof_contract.py     (790 行)  纯裁决：surviving set / occurrence / L2-pre+L2-post hook
    │   ├── policy_evaluator.py   (348 行)  L2 确定性执行器（post + pre 双阶段）
    │   ├── detector.py           detect_record / deployable scope 投影
    │   ├── skill_integration.py  L1 genome → provider prompt 注入
    │   ├── basic_runner.py       provider 传输、缓存、evaluate
    │   └── proof_evaluator.py    metric_snapshot / mutation 传输
    ├── bccse/                  # 自进化引擎
    │   ├── evolution_runner.py   (7179 行) 主编排 run_online_evolution()
    │   ├── genome.py             L1 SkillGenome / SkillEdit / fitness_status
    │   ├── policy_genome.py      L2 PolicyGenome / scope 投影 / pending_queue
    │   ├── vocabulary_registry.py 版本化闭词汇 registry + DSL 编译器
    │   ├── reflection_engine.py  L1 反思 prompt / 候选解析
    │   ├── policy_reflection.py  L2 确定性 planner + 选择 prompt
    │   ├── registry_reflection.py RegistryEdit 注册 planner
    │   ├── failure_episode.py    因果 episode 构建与 prompt 投影
    │   ├── failure_classifier.py 确定性失败归因（L1/L2/detector/none）
    │   ├── step_attributor.py    自监督触发信号
    │   ├── fitness.py            FitnessAssessment / task_state_score
    │   ├── safety_gate.py        StructuralGate（cooldown/convergence/certificates）
    │   ├── promotion_contract.py ★ PromotionDecision 单一推导权威
    │   ├── gate_feedback.py      ★ GateEvent / GateEventLedger / 模糊反馈投影
    │   ├── meta_skill.py         ★ MetaSkillState / MetaEdit（闭词汇）
    │   ├── meta_review.py        ★ GateExperience / OutcomeValidation / DriftSnapshot
    │   ├── evolution_experience_memory.py 经验记忆 v2（meta_review 子域）
    │   ├── module_charter.py / action_spec.py / rule_contract.py  L1 合约
    │   └── signal_adapter.py / step_attributor.py / types.py / curriculum.py …
    ├── htc_tes/                # 类型条件化进化（类型分类、类型经验、类型 fitness）
    └── provider_usage.py       # usage ledger（见 §7 差距 G5）
```

### 2.2 调用链关键节点（文件:行号）

| 环节 | 位置 |
|---|---|
| 主循环入口 | `evolution_runner.py:2420 run_online_evolution` |
| 每 case 检测 | `evolution_runner.py:4896`（`run_detection`，L3123） |
| 自监督触发 | `step_attributor.attribute_failure` → `signal.should_evolve` |
| 确定性归因 | `evolution_runner.py:5004 _attribute_failure` → `failure_classifier.classify_failure` |
| L2 分支路由 | `evolution_runner.py:5034–5129`（L2 attribution 独立开环，不再落到 L1） |
| L2 提案/replay | `evolution_runner.py:3598–4074 run_l2_policy_case` |
| Registry 注册 | `evolution_runner.py:3521–3596 _run_registry_reflection` |
| L1 反思 | `evolution_runner.py:5321–6000`（候选生成/critic/diagnosis/targeted） |
| L1 replay+canary+contrast | `evolution_runner.py:6397–6674` |
| shadow batch commit | `evolution_runner.py:4264–4851 flush_shadow_batch` |
| development probe | `evolution_runner.py:4076–4262 run_development_probe` |
| GateEvent 记录 | `evolution_runner.py:3379–3454 _record_gate_event` |
| Meta Review 聚合 | `evolution_runner.py:7046–7083`（run 末尾） |
| 同步回滚 | `evolution_runner.py:4170–4209`（probe 失败时） |
| checkpoint 持久化 | `evolution_runner.py:3257–3377 persist_progress` |
| checkpoint 恢复 | `evolution_runner.py:1993 load_evolution_checkpoint` / `2169 _restore_state_from_checkpoint` |
| L2-post court hook | `proof_contract.py:748–792` |
| L2-pre court hook | `proof_contract.py:507–570` |
| deployable scope | `paca_tcl/detector.py:201`（standalone 只取 deployable） |

---

## 3. V3 计划逐阶段一致性核查

| 阶段 | 设计目标 | 实现状态 | 备注 |
|---|---|---|---|
| **P0** 冻结基线 | 匿名 fixture + mocked provider + usage ledger | ✅ 测试/fixture 齐；⚠️ usage ledger 未接线（G5） | `tests/fixtures/meta_skill_evolution.py` |
| **P1** 统一 Promotion Contract | EvidenceGrade + ActivationState 双维 | ✅ 完整 | `promotion_contract.py`，单一推导点 `promote()` |
| **P2** 安全 L2-post | boundary consume + route taxonomy | ✅ 完整 | `policy_evaluator.evaluate_policies` + court hook |
| **P3A** 版本化 registry | 单一权威、hash、版本锚 | ✅ 完整 | `vocabulary_registry.L2_REGISTRY` |
| **P3B** 在线可进化 DSL | DSL 白名单编译 + RegistryEdit | ✅ 编译/注册/审计完整；⚠️ 无晋升路径（G3） | `registry_reflection.py` |
| **P4** 分级晋升 | working/deployable 双视图 | ✅ 完整 | `policy_genome.items_for_scope` |
| **P5** GateEvent + 模糊反馈 | 审计账本 + 两种投影 | ⚠️ 账本完整；**反馈未注入 prompt**（G2） | `gate_feedback.py` |
| **P6** Meta 持久化准备 | memory v1→v2、checkpoint v4→v5 | ✅ 完整 | `evolution_experience_memory.py` |
| **P7** Meta Review 闭环 | 完整闭环 | ⚠️ **核心断链**：MetaEdit 从不提交（G1） | `meta_review.py` / `meta_skill.py` |
| **P8** 端到端跑通 | E0 离线闭环 | ✅ E0 通过；E1/E2 按计划不执行 | `tests/test_e2e_optimized_framework.py` |
| **P9** L2-pre consensus | pre-ledger restore | ⚠️ evaluator 完整；**engine 未接线**（G4） | `policy_evaluator.evaluate_pre_ledger_policies` |
| **P10** 连续运行+正式评测 | — | ⬜ 未启动（依赖 E1/E2） | — |

**结论**：P1/P2/P3A/P4/P6/P8 的设计与实现高度一致，质量高；P3B/P5/P7/P9 存在**机制性断链**（详见 §7）。

---

## 4. 核心机制详解

### 4.1 Promotion Contract（P1）——单一推导权威

`promotion_contract.promote()` 是全框架**唯一**把 legacy 决策映射为 `PromotionDecision` 的地方：

```text
legacy_decision ∈ {accept_batch, provisional, structural_reject, skip_stable, ...}（穷举闭集）
  + reasons（hard 前缀表 → 12 个 hard reject code，未匹配项保留为 quality reason）
  + PromotionEvidence（10 维三态证据）
    │
    ├─ hard 非空            → reject / 无激活（任何 profile 不可覆盖）
    ├─ skip 类              → skip / 无激活
    ├─ reject 类            → reject / 无激活
    ├─ provisional 类       → conditional / pending
    └─ accept 类
         ├─ execution∧terminal_effect∧(gold_polarity∨independent_effect)∧non_regression≠False
         │     → strong / deployable
         └─ 否则 → weak / working
```

设计正确性要点：
- hard gate 不可被 profile 覆盖（§13.10 纪律在 `promote()` 中硬性成立）；
- canary 的 `non_regression` 是 veto 而非 correctness authority，永远不会单独把 weak 提升为 strong；
- `from_dict` 对 grade/activation/hard code/evidence dimension 全部 fail-closed 校验。

### 4.2 版本化 Registry + DSL 编译器（P3A/P3B）

`vocabulary_registry.py` 是 L2 词表的**单一权威**：

- **Registry 组成**：`predicates` / `action_operators` / `action_instances` / `audit`（RegistryEdit 链），版本锚 `schema_version` / `facts_schema_version` / `compiler_version` 全部进入 `content_hash`。
- **DSL 白名单**（`_validate_dsl_node`）：只允许 `and/or/not`、6 种比较 + `in`、括号、`AdjudicationFacts` 字段、闭集字符串字面量、整数字面量。**禁止**：函数调用、属性穿透、下标、推导式、动态 import、regex、gold/identity、URL/path。编译产物是纯闭包，`__builtins__={}`。
- **状态机**：`baseline`（不可删）→ `pending`（首次注册，影子态）→ `registered`（working，可执行）→ `disabled`。只有 `baseline + registered` 进入 active view。
- **不可变 lineage**：`apply_registry_edit` 返回新 registry，`before_hash`/`after_hash` 链可重放审计。

设计正确性要点：
- baseline predicate 也由同一个 DSL 编译器编译，**没有第二份手写语义**可漂移；
- 序列化只存 canonical DSL + digest，加载时确定性重编译（drift 即编译失败，fail-closed）；
- registry 随 `PolicyGenome` 持久化（`policy_genome.json` 内含 `registry` + `registry_audit`），经验记忆**不存第二份权威**。

### 4.3 分级晋升与双视图（P4）

`PolicyGenome.items_for_scope(scope)`：

```text
item.is_baseline                 → 两个 scope 都在
fitness_status == strong         → working + deployable
fitness_status == weak           → 仅 working
fitness_status == none/conditional → 都不在
+ _dependencies_active()：item 引用的 predicate/action 必须在 registry active view
```

- `conditional` artifact 物理上**不在 `items`**，而在 `pending_queue`（随 checkpoint 持久化，不参与 content_hash，不进 runtime）。
- `is_item_protected`：baseline 永远保护；strong 默认保护；weak 可改可删。
- 进化 loop 用 `working` 视图；standalone detector 用 `deployable` 视图（`detector.py:201`）。

### 4.4 GateEvent 与反馈投影（P5）

- `GateEventLedger.record()` 幂等（按稳定 `event_id` 去重），resume 不重复写。
- 身份优先级（§11.4）：attempt_id > episode_id+decision_kind > 确定性 observation_id。runner 用 `f"{case_id}:{case_index}:{len(steps)}"` 作 observation_id，**故意排除 gate_decision**，因为 provisional step 会被 `flush_shadow_batch` 原地改写为 accept_batch——若按 decision 键控会把同一逻辑 step 记两次。
- 两种投影严格分离：`project_same_source_retry`（精确 retry_state）/ `project_cross_case_guidance`（10 个模糊闭词汇 code）。两者都不含阈值、gold、prompt、provider 原文、identity。

### 4.5 Meta Review（P7）

- `MetaSkillState`：guidance_tokens / ranking_preferences / gate_profile，`content_hash` 只覆盖配置（audit/时间戳不进 hash）。
- `MetaEdit` 只允许 5 种操作（add/remove guidance、enable/disable ranking、switch profile），全部闭词汇校验（`validate_meta_edit` fail-closed）。
- `classify_outcome`：`exact_false_reject` 需要 6 个条件同时成立（同 canonical intervention ∧ 相容 discriminator ∧ 独立验证 ∧ positive effect ∧ 无回归 ∧ 幂等 validation_event_id），且 `forbidden_source` 一票否决。
- `project_meta_review_for_prompt`：bounded、闭词汇、`authority="evolution_guidance_only"`。
- 同步回滚：probe 失败时同时恢复 L1 genome、L2 policy genome、registry、经验记忆、meta state、canaries（`evolution_runner.py:4170–4209`）。

**但 Meta 闭环存在关键断链，见 §7 G1。**

---

## 5. 检测引擎中的 L2 双阶段插入点

### 5.1 调用顺序（proof_contract.adjudicate_proof_court）

```text
validate / defense dominance / materiality / commitment
  → apply_consensus_gate           (L485–505)
  → PreLedgerPolicyFacts + evaluate_pre_ledger_policies   (L507–570)   ★ P9
  → surviving set（含合法 restore）
  → occurrence_errors snapshot / earliest-origin localization (L576–627)
  → occurrence ledger
  → base terminal state
  → evaluate_policies（L2-post，coverage + terminal edge only） (L745–789)
```

两个阶段的 stage 隔离由 registry 保证：`restore_audited_consensus` 注册为 `policy_stage="pre_ledger"`，**不是** post-ledger 可提案 action；post evaluator 遇到 pre operator 会 fail-closed（`unknown_action`），pre evaluator 遇到 post operator 会 fail-closed（`unknown_stage_for_pre_ledger`）。

### 5.2 L2-post 的 fail-closed 守卫

`consume_verified_clean_boundary` 同时要求：`boundary_path_state=="verified_clean"` ∧ `boundary_authority_valid` ∧ `terminal_state=="unresolved"` ∧ `surviving_error_count==0`，且**不修改 occurrence ledger**（terminal edge only）。court hook 在 `surviving` 非空时不会把 `verified_error` 翻成 `verified_clean`（`proof_contract.py:777–787`）。

### 5.3 L2-pre 的 fail-closed 守卫

`_restore_guards` 要求 8 个条件同时成立：consensus 已应用、ID 在 blocked 集、schema=pass、causal∈{causes/propagates}、impact 非 process-only/process_debt、未被 dominating defense 击败、非 quarantined。被恢复的证书会**从 `consensus_blocked` 移除**（本轮 review 补齐的注释明确这一点），保证 occurrence ledger 的单桶一致性。

---

## 6. 持久化与可恢复性

| 状态 | 持久化位置 | hash 校验 | 恢复行为 |
|---|---|---|---|
| champion genome | `champion_checkpoint.json` + checkpoint payload | ✅ content_hash | 恢复 |
| shadow genome | `shadow_checkpoint.json` | ✅（但**故意不恢复**） | 回退到 champion（不复活未提交 shadow） |
| stable genome | checkpoint `stable_genome_payload` | ⚠️ 无（缺口，见 §9 建议） | 恢复，缺失时 clone champion |
| policy genome | checkpoint `policy_genome` | ✅ content_hash + 逐项 `_validate_item_contract` | fail-closed |
| stable policy genome | checkpoint `stable_policy_genome_payload` | ⚠️ 无 | 恢复，缺失时 clone policy |
| registry | 随 policy_genome 内嵌 | ✅（经 policy hash） | from_dict 重编译 DSL |
| gate events | checkpoint `gate_events` | schema 校验 | 幂等重建 ledger |
| meta state | 经验记忆 `meta_review.meta_state` | ⚠️ 无 | 恢复，ValueError 时回退 baseline |
| 经验记忆 | `htc_tes_experience_memory.json` | v1→v2 迁移边界 | 单边界迁移，未知 schema 拒绝 |
| 跨 run 继承 | `evolution_state_manifest.json` | ✅ sha256 + 路径逃逸防护 | 严格绑定三 artifact |

resume 安全性要点：case_ids 逐一比对 + `input_content_hash` + `config_hash` 强校验；trailing halted/skipped 占位 step 会回卷重审；provisional step 统一改写为 `rollback_resume`。

---

## 7. 机制有效性评估：五个关键缺口

按对"自进化是否真的闭环"的影响排序：

### G1（最严重）Meta Review 不闭环：MetaEdit 从不提交

> **状态：已修复（第三轮，commit `cfc4c4f`）** — 证据门提交见 `EVOLUTION_FIXES_SUMMARY.md` G1 一节。

**位置**：`evolution_runner.py:7064–7091`

run 末尾生成 `meta_candidates` 后只写入 `meta_review_summary["shadow_candidates"]` 做审计，**从不调用 `apply_meta_edit`**。后果链：

```text
MetaEdit 永不提交
  → meta_skill_state.guidance_tokens 永远为空
  → evolution_runner.py:3863–3868 的注入条件（guidance_tokens or ranking_preferences 非空）永假
  → meta_guidance 永远为 None
  → L2 policy prompt 永远收不到 Meta guidance
  → gate_profile 永远停在 conservative
  → "Meta 指导 L1/L2 进化" 在运行时不存在
```

这意味着 **P7 的核心价值（Meta 指导更好 skill、候选排序、profile 切换）在当前代码里全部不生效**，尽管 `meta_review.py` 的每个单元机制（GateExperience、OutcomeValidation、classify_outcome、rank_candidates、project_meta_review_for_prompt）都实现且有测试。

**连带问题**：
- `classify_outcome` / `OutcomeValidation` 在 production 路径**无任何调用方**（只被单测调用）；`evolution_runner.py:7066` 永远传空 list，注释说"由 development probe 产生（P7.2）"但 probe 代码从不产生。
- `meta_review.py:rank_candidates` 在 production 无调用方——Meta 排序未接入 L2 候选选择。
- `gate_profile` 读侧零消费：`promotion_contract.promote()` 的 `profile` 参数所有调用方都用默认值；`promotion_gates.py` 无 profile 逻辑。即使 profile 切到 steady 也不改变任何行为。
- P7.7 自动 profile 切换未实现。

### G2（次严重）反馈闭环断裂：GateEvent 投影从不注入任何 prompt

> **状态：已修复（第三轮，commit `44ea0f4`）** — L1/L2 双 prompt 注入见 `EVOLUTION_FIXES_SUMMARY.md` G2 一节。

`gate_feedback.project_cross_case_guidance` 的唯一 production 调用方是 `meta_review.aggregate_gate_experience`（用于提取 feedback_codes）；`project_same_source_retry` **零调用方**。没有任何 prompt builder 接收 GateFeedbackProjection。

后果：V3 §11.7 设计的"L2→L1 和 gate→generation 统一反馈通道"在运行时不存在——GateEvent 是只写审计账本，feedback 永远到不了下一次 reflection。`_build_mutation_retry_feedback` 走的是另一套（`mutation_retry_feedback` + `rejected_source_proposals`），与 GateEvent 无关。

### G3 Registry 注册是死胡同：pending → working 无晋升路径

> **状态：已修复（第三轮，commit `378147e`）** — 证据晋升 + 别名分派修复见 `EVOLUTION_FIXES_SUMMARY.md` G3 一节。

`_run_registry_reflection` 把新 entry 注册为 `pending`（影子态），此后**无任何代码路径**把 pending entry 提升为 `registered`（working）：

- `pending_queue` 只被写入（`policy_genome.enqueue_pending`）和序列化，**无消费方**；
- registry entry 也不入 pending_queue（`_run_registry_reflection` 只改 `policy_genome.registry`，不 enqueue）；
- P3B §9.6 设计的"replay + 独立 development validation → working → strong → deployable"晋升链没有实现。

后果：在线词表进化**注册了但永远不可用**，P3B 的进化价值只到"审计"为止。测试 `test_registry_evolution.py` 也验证了 pending 不进 active view，但没有测试任何晋升。

### G4 P9 L2-pre 未接入引擎：`pre_ledger_policy_items` 无调用方

> **状态：已修复（第三轮，commit `209a2c5`）** — stage 路由见 `EVOLUTION_FIXES_SUMMARY.md` G4 一节。

`proof_contract.adjudicate_proof_court` 的 `pre_ledger_policy_items` 参数（L347）在**全仓库无任何调用方传入**：

- `proof_engine.py` 只在 L1644 传 `policy_items`（post），不传 `pre_ledger_policy_items`；
- `paca_tcl/detector.py` 只传 `policy_items`；
- `policy_reflection.plan_policy_candidates` 对 `consensus_gate_blocked + terminal_unresolved` 仍返回 `engine_level_intervention_required`（**不产生** pre-ledger 候选）。

后果：P9 的 evaluator、guards、stage 隔离、单测全部正确，但 proof-court 集成块（`proof_contract.py:507–570`）是**死代码**——pre-ledger restore 永远不会在真实运行中发生。P9 §15.7 的"合法 restore 进入 surviving set / occurrence ledger 一致"验收在 engine 层面未达成（只到 evaluator 单测层面）。

### G5 ProviderUsageLedger 未接线

> **状态：已修复（第三轮，commit `a47a007`）** — mutation 路径记账见 `EVOLUTION_FIXES_SUMMARY.md` G5 一节。

`provider_usage.py` 的 `ProviderUsageLedger`（logical call / transport attempt / token / cost 审计）**无任何 production 调用方**。P0 §5.2 的"最小 usage ledger"只到模块 + 单测为止，§13.18 的逐请求成本审计未接入 runner。成本追踪目前只有 `budget_usage` 计数器（mutation_calls / detector_calls / tokens 无）。

---

## 8. 本轮 Review 修复的代码缺陷（3 处，已落地，738 测试全过）

### F1 `proof_contract.py:566` — P9 恢复桶一致性注释补齐
恢复证书时已正确从 `consensus_blocked` 移除（行为本身对），但缺少"单桶一致性"的不变量说明。补齐注释，防止后续维护把 restore 写成"双桶共存"导致 occurrence ledger 重复计数。

### F2 `evolution_runner.py:6614` — contrast neighbor 键访问崩溃风险（真 bug）
```python
neighbor_id = str(neighbor["case_id"])        # 改前：KeyError 风险
neighbor_before_state = str(neighbor["state"])
```
`contrast_replay_registry` 的行可能来自 checkpoint 恢复（恢复路径只做 `isinstance(row, Mapping)` 过滤，不校验键），且该访问在 try 块**之外**，损坏的 checkpoint 行会直接炸掉主循环。已改为 `.get()` 防御式读取 + 空值 fail-closed 跳过。

### F3 `evolution_runner.py:4564` — `source_audits[idx]["source_after"]` 链式下标
改为 `.get()` + `isinstance(Mapping)` 守卫，消除 KeyError 风险。

### 评估后决定**不改**的项
- `load_evolution_checkpoint` 对 stable genome / stable policy / experience memory 缺 content_hash 校验：属加固建议而非 bug（champion/shadow/working-policy 已有校验），改动会改变 checkpoint 兼容行为，列入 §9 建议。
- `evolution_runner.py:6397` 的 `if True:` 恒真分支：是 `fitness_check` 开关语义废除后的死代码味道，但公共 API 强制 `fitness_check=True`，无行为风险，保留以免无谓 churn。
- `except Exception` 共 19 处：逐一评估后 17 处为合理 fail-closed/有意降级；604/4717 两处会吞异常但都有审计落盘，风险可接受。

---

## 9. 优化建议（按优先级）

> **状态：G1-G5 已全部落地（第三轮），本节保留为历史建议；新的遗留项见 `EVOLUTION_FIXES_SUMMARY.md` 第三轮小节与 D1。**

### 第一优先级：闭合 Meta Review 循环（G1）
这是"自进化框架是否成立"的决定性缺口。最小落地路径：
1. **MetaEdit 提交**：run 末尾对 `meta_candidates` 做 shadow 验证后 `apply_meta_edit` 提交（先仅 guidance token，profile 切换仍走证据门）。这是单行机制改动，但让 G1 的整条后果链打通。
2. **OutcomeValidation 生产**：在 development probe 完成后，把 probe 结果分类为 OutcomeValidation（`classify_outcome` 已就绪），写入 `meta_review["outcome_validations"]`。
3. **rank_candidates 接入**：在 `run_l2_policy_case` 的候选选择前用 Meta ranking 重排（只重排不扩大候选集，机制已就绪）。

### 第二优先级：接通反馈通道（G2）
把 `project_cross_case_guidance` 的模糊 code 注入 L1 reflection prompt 和 L2 policy prompt（prompt builder 已有 `meta_guidance` 参数位），让 GateEvent 从"只写账本"变成"可读反馈"。注意保持 same-source / cross-case 投影严格分离的现有纪律。

### 第三优先级：补上 pending → working 晋升（G3）
为 registry pending entry 和 pending_queue 实现晋升裁决：一次 replay 证明确定性 local effect → `registered`（working）；独立 development validation → 可入 deployable。否则 P3B 的在线词表进化永远停在影子态。

### 第四优先级：接通 P9 L2-pre（G4）
1. `proof_engine.py` 在调 `adjudicate_proof_court` 时把 pre-ledger policy items 传入（registry 中 `policy_stage=="pre_ledger"` 且 active 的 item）；
2. `plan_policy_candidates` 为 `consensus_gate_blocked + terminal_unresolved` 生成 pre-ledger restore 候选（替换现在的纯 `engine_level_intervention_required` 终态）；
3. 补一条 engine 级集成测试（当前只有 evaluator 单测）。

### 第五优先级：基础设施
- 接线 `ProviderUsageLedger` 到 `run_mutation_call` / `run_detection`（G5）；
- 补 stable genome / stable policy / experience memory 的 checkpoint hash 校验；
- `evolution_runner.py` 已达 7179 行，建议按职责拆分（L1 循环 / L2 循环 / probe / batch commit / 持久化），降低单文件认知负担。

---

## 10. 总结

**做到位的**（设计与实现一致、质量高）：统一晋升契约（P1）、安全 L2-post（P2）、版本化 registry 与 DSL 白名单编译（P3A/P3B 的编译侧）、分级双视图（P4）、持久化准备（P6）、端到端离线闭环（P8 E0）、gold 防火墙、unknown fail-closed、registry 单一权威、同步回滚、checkpoint/resume 身份绑定。

**没闭环的**（机制断链，框架"自进化"名不副实的根源）：Meta Review 不提交（G1）、反馈不进 prompt（G2）、registry 注册无晋升（G3）、L2-pre 未接引擎（G4）、usage ledger 未接线（G5）。

**一句话判断**：当前框架的**晋升门控、词表治理、持久化、安全纪律**已经达到 V3 设计；但**"Meta 指导进化"和"反馈驱动进化"这两条自进化的主动脉在运行时不工作**——框架现在是一个"可审计、可回滚、分级门控完善的受约束进化系统"，但还不是一个"Meta 闭环驱动、反馈闭环驱动的自进化系统"。补 G1+G2 之前，不宜启动 P10 规模化评测，否则测出的只是 L1/L2 局部进化，而非完整的自进化框架。
