# SEHD-RISE 重构执行计划（实施文档）

> **上游依据：** `SEHD_RISE_Architecture_Refactor_Guideline_Outline.md`（架构纲领，定义"为什么"与"目标形态"）。
>
> **本文档定位：** 纲领的唯一实施落地方案，定义"按什么顺序、搬什么、怎么验证、在哪提交"。实施过程中与纲领冲突时，以纲领为准并回写本文档。
>
> **执行约束（不可违反）：**
>
> 1. `src/sehd_v6/` 全程冻结——不修改、不删除、不格式化。它是迁移期的行为基准（golden reference）。
> 2. 新框架落位于 `sehd_rise/`，按本文档的阶段顺序逐步生长；**每完成一个阶段立即 git commit**。
> 3. 全部工作由主会话直接执行，**不分派 subagent**。
> 4. 除 §3.2「语义变更点清单」明确列出的行为变化外，一切迁移必须保持行为等价（pristine：重构是结构修复，不是行为变更）。
> 5. 新包内**禁止**出现 `sehd_v6` 的任何 import；新旧交互只允许发生在 `tools/parity/` 校验脚本中。

---

# 1. 现状诊断

## 1.1 代码地图（迁移基线，已逐一核实）

`src/sehd_v6/` 共 68 个 Python 文件、约 40,358 行。调用方式为仓库根目录下的命名空间包（`python -m src.sehd_v6.evolution` / `python -m src.sehd_v6.detector`）。

| 区域 | 文件 | 行数 | 职责 |
|---|---|---|---|
| 入口门面 | `evolution/runner.py`、`evolution/__main__.py`、`evolution/state_manifest.py` | 668 | `run_evolution` 公共 API、CLI、状态清单 |
| 入口门面 | `detector/__init__.py`、`detector/__main__.py`、`detector/batch.py`、`detector/replica_aggregation.py` | 642 | 检测器批量入口、副本聚合 |
| 基础设施 | `env_loader.py`、`provider_routes.py`、`core/provider_models.py`、`core/provider_usage.py`、`utils/gold_label.py`、`utils/ocse_packet.py` | 788 | env、路由、usage 账本、gold 规整、轨迹打包 |
| 检测层 | `core/paca_tcl/`（15 个模块） | ≈9,500 | Proof-Court 检测器（含 v1/v2/v3/v3.4/v3.5 五代版本链） |
| 进化控制面 | `core/bccse/`（30 个模块） | ≈26,000 | L1/L2/Meta 自进化全部编排与数据模型 |
| 类型层 | `core/htc_tes/`（3 个模块） | ≈2,000 | HTC-TES 类型分类/适应度/路由 |

### 臃肿震中：`core/bccse/evolution_runner.py`（10,165 行，约 100 个模块级函数 + `EvolutionLoop` 类）

一个文件混合了至少 12 类职责：案例队列主循环（`EvolutionLoop.run`，约 2,400 行）、L2 案例流（`_run_l2_policy_case_impl`，约 700 行）、shadow 批量提交（`_flush_shadow_batch_impl`，约 600 行）、检查点存取（`load_evolution_checkpoint` / `_restore_checkpoint_state`，约 550 行）、进度持久化（`_persist_progress` / `_save_evolution_results`）、门事件投影（`_project_gate_event` / `_build_gate_event_ledger`）、因果效应审计（`_expected_effect_audit` / `_verify_edge_level_counterfactual` / `_attribute_intervention_fitness`）、canary 管理（`_selected_canaries` / `_canary_preserved` / `_evict_stable_canaries`）、拒绝归因与重试反馈（`_build_rejection_summary` / `_build_mutation_retry_feedback` 等 10 个函数）、候选预校验（`_prevalidate_candidate` 等）、开发探针（`_run_development_probe_impl`）、检测重放（`_run_detection_with_genome` 含缓存种子管理）。

### paca_tcl 版本链（遗产叠加）

```text
contract_v1 ──► layered_contract(v2) ──► proof_contract_v3 ──► proof_contract_v3_4 ──► proof_contract(v3.5)
layered_engine(v2) ──► proof_engine_v3_4 ──► proof_engine(v3.5)
```

v3.5 契约/引擎直接从 v3、v3.4、v1 导入辅助函数与基类，五代代码必须共存才能运行——这是"复制臃肿"在检测层的具象。

### 已核实的结构性缺陷

| # | 缺陷 | 证据位置 |
|---|---|---|
| S1 | bccse ↔ htc_tes 包级循环导入，靠 PEP 562 惰性 `__getattr__` 掩盖 | `core/bccse/__init__.py:6-14` 自述 |
| S2 | core → app 反向依赖：`core/paca_tcl/detector.py` import `sehd_v6.detector.batch` | 静态依赖图 |
| S3 | 检测层 → 进化层反向依赖：`paca_tcl/evolution_contract.py` import `bccse.signal_adapter`；`paca_tcl/detector.py` import `bccse.genome` / `bccse.policy_genome` | 静态依赖图 |
| S4 | paca_tcl ↔ bccse 词汇循环：`bccse/genome.py`、`condition_spec.py`、`module_charter.py` import `paca_tcl/audit_contract`，同时 `skill_integration.py` import `bccse.genome` | 静态依赖图 |
| S5 | L1/L2 两套独立编排：`EvolutionLoop.run` 内联 L1 流 vs `_run_l2_policy_case_impl` + `_flush_shadow_batch_impl` | `evolution_runner.py:7404` / `6059` / `5461` |
| S6 | skip 语义混合：`gate_decision` 实际取值含 `skip_stable`、`skip_no_edit`、`skip_runtime_invalid`、`skip_unaddressable_failure`、`skip_mutation_budget_exhausted`、`skip_invalid_mutation`、`skip_no_policy`、`halted`、`reject_policy_replay_error` | `evolution_runner.py` 全文 |

## 1.2 纲领问题 → 代码位置对照表

| 纲领条目 | 问题 | 当前代码位置 | 重构落点 |
|---|---|---|---|
| §1.1 | Trigger 与 Repairability 分裂 | `step_attributor.AttributionSignal.should_evolve`（触发于 `evolution_runner.py:7606`）→ `failure_classifier` → `failure_episode` → `UnaddressableFailure`（`7807`） | Phase 4：单一 Repairability Authority |
| §1.2 | Unaddressable 语义层级错误 | `skip_unaddressable_failure` 被当作普通 gate_decision 落账 | Phase 4：定义为 `UNADDRESSABLE_FRAMEWORK_DEFECT`（框架不变量违反），Phase 9：独立漏斗统计 |
| §1.3 | 根因判断多权威竞争 | `step_attributor`、`failure_classifier`、`failure_episode`、`htc_tes/type_conditioned_evolution`、`reflection_engine`、`policy_reflection`、`meta_review` 各自重释 failure | Phase 4：CausalFailureEpisode 唯一因果权威 |
| §1.4 | Detector/Skill 边界可被打穿 | `failure_classifier` 的 malformed-origin 分类已有雏形，但 framework defect 不进入独立真源 | Phase 4：检测缺陷归 `FRAMEWORK_DEFECT`，永不进入 Skill 进化 |
| §1.5 | Attribution ≠ Terminal Causal Cut | `failure_classifier.py` 按"异常 artifact"直接定 owner（规则 1-5 优先级链） | Phase 4：`causal/cut.py` 以"阻止正确终态的最小可修复因果切面"构造 owner |
| §1.6 | HTC-TES 兼任类型理解与路由 | `type_conditioned_evolution.py` 的类型路由可覆盖/过滤 module | Phase 5：降级为 Advisory Type Intelligence |
| §1.7 | L1/L2 编排重复 | S5 + `genome.py` vs `policy_genome.py`、`reflection_engine.py` vs `policy_reflection.py` | Phase 4 定义共享 RepairPlan 协议，Phase 9 统一编排 |
| §1.8 | Meta 复杂度大于直接纠错能力 | `meta_skill.py`（state/commit/rollback/persistence）+ `meta_review.py` | Phase 8：重定义为 Cross-Case Strategy Learner |
| §1.9 | skip 掩盖故障漏斗 | S6 | Phase 9：§2.9 四层 outcome 分类 |
| §1.10 | Convergence 混入非学习事件 | 收敛窗口消费所有 gate_decision（含 skip/budget/runtime） | Phase 9：收敛只消费 complete repair attempts |
| §1.11 | 同一语义多结构重复表达 | Detector Result → `signal_adapter` → `AttributionSignal` → `FailureAttribution` → `FailureEpisode` → Repair Frontier → `ActionSpec` → `GateEvent` → Experience → Meta | 全阶段：§2.11 权威表落地为模块归属（§2.3 本文档） |
| §1.12 | 系统集成错误为主 | S1–S5 | 分层依赖规则（§2.2 本文档）从结构上排除 |

---

# 2. 目标架构：`sehd_rise/`

## 2.1 目录结构

```text
sehd_rise/
├── __init__.py                  # 仅包说明；不做任何 re-export（从结构上杜绝包级循环）
│
├── platform/                    # 与研究机制无关的基础设施（最底层）
│   ├── env.py                   #   .env 加载            ← env_loader.py
│   ├── routes.py                #   provider 路由解析     ← provider_routes.py
│   ├── models.py                #   canonical model name  ← core/provider_models.py
│   ├── usage.py                 #   ProviderUsageLedger   ← core/provider_usage.py
│   ├── gold.py                  #   gold label 规整（仅评估侧使用）← utils/gold_label.py
│   └── jsonio.py                #   canonical_json / stable_hash / atomic_write_json
│                                #   ← utils/ocse_packet.py 前段 + evolution_runner 的 _atomic_write_json/_payload_hash
│
├── skills/                      # 可进化表面的数据模型与闭词汇（纯数据，无任何流程逻辑）
│   ├── genome.py                #   L1 SkillGenome        ← bccse/genome.py
│   ├── policy.py                #   L2 PolicyGenome       ← bccse/policy_genome.py
│   ├── rule.py                  #   规则 slot 契约 + 条件  ← bccse/rule_contract.py + condition_spec.py
│   └── vocabulary/
│       ├── contracts.py         #   闭词汇、层/谓词常量、共享校验 ← bccse/v6_contracts.py + vocabulary.py
│       ├── registry.py          #   L2_REGISTRY 版本化注册表       ← bccse/vocabulary_registry.py
│       ├── charters.py          #   八个 L1 模块章程               ← bccse/module_charter.py
│       └── evolution.py         #   词汇扩展规划 + pending→registered 晋升
│                                #   ← bccse/registry_reflection.py + registry_promotion.py
│
├── detection/                   # Proof-Court 检测层（复杂但自包含；纲领 Principle 1）
│   ├── packet.py                #   轨迹打包/校验          ← utils/ocse_packet.py 后段
│   ├── transport.py             #   provider 调用、census、计数 ← paca_tcl/basic_runner.py
│   ├── court/
│   │   ├── contract.py          #   v3.5 证明契约（合并 v1/v2/v3/v3.4 五代辅助链）
│   │   │                        #   ← proof_contract + proof_contract_v3_4 + proof_contract_v3
│   │   │                        #     + contract_v1 + layered_contract
│   │   ├── engine.py            #   v3.5 证明法庭引擎（合并 v2/v3.4 引擎链）
│   │   │                        #   ← proof_engine + proof_engine_v3_4 + layered_engine
│   │   ├── audit.py             #   证书审计翻译           ← paca_tcl/audit_contract.py
│   │   └── identity.py          #   证书内容指纹           ← bccse/certificate_identity.py
│   ├── policy.py                #   L2 政策的确定性执行器 + adjudication facts
│   │                            #   ← paca_tcl/policy_evaluator.py + v6_contracts 的
│   │                            #     build_adjudication_facts / validate_policy_params
│   ├── injection.py             #   L1 规则注入 provider 边界 ← paca_tcl/skill_integration.py
│   ├── detector.py              #   detect_record 公共入口      ← paca_tcl/detector.py
│   ├── replicas.py              #   副本聚合                    ← detector/replica_aggregation.py
│   └── batch.py                 #   批量检测                    ← detector/batch.py
│
├── causal/                      # ★ 因果修复层（新增核心；唯一因果权威）
│   ├── signals.py               #   检测输出 → 进化侧唯一投影（合并两套投影）
│   │                            #   ← bccse/signal_adapter.py + paca_tcl/evolution_contract.py
│   ├── cut.py                   #   Terminal Causal Cut + 失败形状分类
│   │                            #   ← bccse/step_attributor.py + failure_classifier.py
│   ├── episode.py               #   CausalFailureEpisode（纲领 §2.3 统一因果对象）
│   │                            #   ← bccse/failure_episode.py + v6_contracts.FailureAttribution
│   ├── repairability.py         #   ★ 单一 Repairability Authority（纲领 §2.1/§2.2，新增）
│   └── plan.py                  #   ★ Canonical RepairPlan 协议（纲领 §2.7，新增）
│
├── experience/                  # 机制级经验记忆（单一经验真源）
│   └── memory.py                #   ← bccse/evolution_experience_memory.py + types.py
│
├── typing/                      # HTC-TES：Advisory Type Intelligence（无因果否决权）
│   ├── classifier.py            #   ← htc_tes/type_classifier.py
│   ├── guidance.py              #   类型条件化检索/反思先验（删除 owner 覆盖能力）
│   │                            #   ← htc_tes/type_conditioned_evolution.py 的先验部分
│   └── slices.py                #   类型化评估切片         ← htc_tes/type_aware_fitness.py
│
├── planning/                    # 假设搜索：由 RepairPlan 驱动的生成侧（L1/L2 共享协议，不同执行器）
│   ├── l1.py                    #   L1 LLM 因果反思        ← bccse/reflection_engine.py
│   ├── l2.py                    #   L2 闭词汇政策规划       ← bccse/policy_reflection.py
│   ├── retrieval.py             #   经验检索 + 负反馈分析   ← bccse/experience_retriever.py
│   │                            #   + negative_feedback_analyzer.py
│   └── feedback.py              #   重试反馈构造（prompt-safe）← evolution_runner 的
│                                #   _build_mutation_retry_feedback/_polarity_retry_hint 等 10 函数
│
├── execution/                   # 修复执行与证据（Execution Truth）
│   ├── action.py                #   ActionSpec 可执行干预   ← bccse/action_spec.py
│   ├── executor.py              #   按 RepairPlan.executor 分派：provider 行为执行 / detection.policy
│   ├── replay.py                #   带基因组检测重放 + 缓存种子管理
│   │                            #   ← evolution_runner 的 _run_detection_with_genome/_run_mutation_call 等
│   └── trace.py                 #   ExecutionTrace + 规则轨迹收集
│                                #   ← evolution_runner 的 _project_execution_trace/_collect_*_trace 族
│
├── evaluation/                  # 因果效应 → 适应度 → 晋升（Fitness Truth）
│   ├── effect.py                #   边级反事实、阻塞谓词闭合、干预归因
│   │                            #   ← evolution_runner 的 _verify_edge_level_counterfactual/
│   │                            #     _attribute_intervention_fitness/_expected_effect_audit 族
│   ├── fitness.py               #   source 改善评估         ← bccse/fitness.py
│   ├── canary.py                #   canary 选取/保持/驱逐   ← evolution_runner 的 _selected_canaries 等
│   ├── gate.py                  #   结构门 + LLM 无冲突门   ← bccse/safety_gate.py + _llm_no_conflict_gate
│   └── promotion.py             #   PromotionDecision/证据等级/MetricSnapshot
│                                #   ← bccse/promotion_contract.py + promotion_gates.py
│
├── meta/                        # Meta Skill：Cross-Case Strategy Learner（只学搜索策略）
│   ├── strategy.py              #   ← bccse/meta_skill.py
│   └── review.py                #   ← bccse/meta_review.py
│
├── loop/                        # 统一编排（纲领 §7 状态机的实现；替代 evolution_runner 全部流程职责）
│   ├── machine.py               #   状态机 + §2.9 outcome 漏斗分类（新增）
│   ├── runner.py                #   案例队列、预算、halt、收敛（替代 EvolutionLoop.run）
│   ├── shadow.py                #   shadow 批量提交         ← _flush_shadow_batch_impl 等
│   ├── probes.py                #   开发探针               ← _run_development_probe_impl 等
│   ├── ledger.py                #   GateEvent 台账与投影    ← bccse/gate_feedback.py + runner 门事件函数族
│   ├── persistence.py           #   检查点/恢复/结果落盘    ← runner 持久化函数族（约 550 行）
│   ├── records.py               #   案例记录准备/参数校验  ← _attribution_for_result/_validate_evolution_params（_detector_record→detection/detector.py、_evaluator_gold→platform/gold.py、_authority_failure_details→execution/replay.py，见 Phase 7 归属迁移）
│   └── result.py                #   EvolutionStep / OnlineEvolutionResult
│
├── api/                         # 对外门面（唯一允许"组装"的层）
│   ├── evolution.py             #   run_evolution          ← evolution/runner.py
│   ├── detection.py             #   检测公共 API           ← detector/__init__.py 的 facade 部分
│   └── manifests.py             #   状态清单               ← evolution/state_manifest.py
│
└── cli/
    ├── __main__.py              #   python -m sehd_rise <evolve|detect>
    ├── evolve.py                #   ← evolution/__main__.py
    └── detect.py                #   ← detector/__main__.py + paca_tcl/detector.py 的 main()
```

迁移期校验脚本（不属于任何包，唯一允许同时 import 新旧两包的位置）：

```text
tools/parity/
├── fixtures.py              # 从 datasets/raw/agenthallu 构建固定夹具案例集
├── compare_detection.py     # 新旧检测器逐字段比对（暖缓存回放）
├── roundtrip_skills.py      # 基因组/词汇/政策序列化往返一致性
└── compare_run.py           # 端到端小样本运行漏斗统计比对
```

## 2.2 分层依赖规则（无环，机械可检查）

依赖方向只允许向下（`A → B` 表示 A 可以 import B）：

```text
platform        ← 一切层可依赖；自身不依赖任何 sehd_rise 层
skills          → platform
detection       → platform, skills                 # 仅消费 skills 的数据模型与词汇常量
causal          → platform, skills, detection
experience      → platform, skills, causal
typing          → platform, skills, causal, experience
planning        → platform, skills, causal, experience, typing
execution       → platform, skills, detection, causal
evaluation      → platform, skills, causal, execution
meta            → platform, causal, experience, evaluation
loop            → 除 api/cli 外的所有层
api / cli       → loop, detection, evaluation, planning
```

**反向 import 一律禁止。** 该规则从结构上排除现状缺陷 S1–S4：

- S1（bccse↔htc_tes）：`typing/` 在 `experience/` 之上，下方各层永不 import typing，循环不可能成立；不再需要任何惰性 `__getattr__`。
- S2/S3（core↔app 反向）：`detection/` 不 import `api/`、`cli/`、`loop/`；批量入口 `batch.py` 收编进 detection 内部。
- S4（词汇循环）：全部闭词汇集中于 `skills/vocabulary/`，`detection/court/` 只读消费；检测层与技能层之间只有单向数据依赖。

**共享词汇归属裁决：** 纲领 §2.11 要求一个事实一个权威。证明法庭审计词汇（`audit_contract`）、L2 谓词/动作注册表、模块章程，既是法庭报告失败的语言、也是进化路由修复的语言——在新架构中唯一真源是 `skills/vocabulary/`，检测层作为下游消费者 import。这反转了现状中 `bccse → paca_tcl.audit_contract` 的方向，从而打开死结。

## 2.3 纲领 §2.11 权威关系 → 模块归属表

| 纲领权威 | 唯一真源模块 | 其余一切结构只能是其投影 |
|---|---|---|
| Detection Truth | `detection/court/` 输出（terminal basis） | `causal/signals.py` 是唯一合法投影点 |
| Failure Truth | `causal/episode.py`（CausalFailureEpisode） | classifier/attributor/episode 三处重释合并为一处 |
| Repair Truth | `causal/plan.py`（RepairPlan） | planning/execution 不得重造 repair 语义 |
| Execution Truth | `execution/trace.py`（ExecutionTrace） | runner 内联的轨迹收集函数族全部收编 |
| Effect Truth | `evaluation/effect.py`（CausalEffectRecord） | 边级反事实证据只在此处判定 |
| Fitness Truth | `evaluation/promotion.py`（PromotionDecision） | gate/promotion 双文件统一决策模型 |
| Experience Truth | `experience/memory.py` | typing 只做切片视图，不另存副本 |
| Meta Truth | `meta/strategy.py` | meta 状态唯一驻地 |
| 触发/可修复权威 | `causal/repairability.py` | 取代 `should_evolve` 与 `skip_unaddressable_failure` 的双权威 |
| Gate 事件台账 | `loop/ledger.py`（GateEvent） | 维持"GateEvent 单一投影权威"已有成果 |

## 2.4 导入与命名规范

1. 包内一律使用**绝对导入**：`from sehd_rise.causal.episode import CausalFailureEpisode`。禁止相对导入链（现状中 `from ...utils import ...` 跨三层难以审计）。
2. 每个子包的 `__init__.py` 只做同层符号的显式 re-export；`sehd_rise/__init__.py` 保持空白（仅 docstring）。禁止 PEP 562 惰性导出——分层规则保证无循环，惰性导出失去存在理由。
3. 模块命名用名词单数（`episode.py`、`plan.py`），流程入口用动词性命名（`cut.py` 的 `build_causal_cut()`）。
4. 移植即重命名：符号在新包中的名字即最终名字，不留 `_old`/`_new`、版本号后缀（`proof_contract_v3_4` 这类命名在新包中不存在）、兼容别名（pristine 上线初稿法则）。
5. 注释只写 why。从旧代码搬运时，解释历史补丁的"what"注释随代码重写一并消化，不照搬。

---

# 3. 迁移总策略

## 3.1 冻结与平行生长

- `src/sehd_v6/` 冻结为行为基准。任何"发现旧代码有 bug"的情况：记录进本文档 §5 风险登记，**不在旧包修**，在新包对应阶段按纲领语义实现正确行为，并在该阶段提交信息中注明偏差。
- `sehd_rise/` 按 Phase 0→10 顺序逐层生长。每个阶段结束时新包必须整体可 import、该层验证脚本全绿，然后立即提交。
- 阶段顺序按依赖方向自底向上排列（platform → skills → detection → causal → …），保证任何阶段结束时不存在"引用了尚未迁移模块"的悬空 import。

## 3.2 语义变更点清单（仅此处允许行为变化，其余一律等价）

| # | 纲领依据 | 变更内容 | 落地阶段 |
|---|---|---|---|
| C1 | §2.1/§2.2 | 单一 Repairability Authority：`should_evolve` 不再是独立触发权威；只有 `REPAIRABLE` 进入进化；`NON_EVOLVABLE` 与 `FRAMEWORK_DEFECT` 分流 | Phase 4 |
| C2 | §2.2 | Unaddressable 重新定义为 `UNADDRESSABLE_FRAMEWORK_DEFECT`（框架不变量违反），从 no-edit 路径中剥离 | Phase 4（定义）/ Phase 9（漏斗统计） |
| C3 | §2.3/§2.4 | 统一 CausalFailureEpisode + Terminal Causal Cut：`step_attributor`/`failure_classifier`/`failure_episode` 三处归因合并为一处，owner 由"最小可修复因果切面"产出 | Phase 4 |
| C4 | §2.5 | Detector/Evolution 所有权边界硬隔离：validator/transport/route/config 缺陷永久路由到框架侧，不生成 Skill | Phase 4 |
| C5 | §2.6 | HTC-TES 降级为 Advisory：类型信息只服务检索/先验/切片，删除对 causal owner 的覆盖与 module 过滤能力 | Phase 5 |
| C6 | §2.7 | L1/L2 共享 Canonical RepairPlan 协议（failure identity / layer / owner / target edge / executor / legal operations / effect contract / evaluation contract） | Phase 4（协议定义）/ Phase 9（编排消费） |
| C7 | §2.8 | Meta 重定义为 Cross-Case Strategy Learner：只产出 search prior / ranking / retry / diversity 策略；删除对 owner、cut、repairability、hard gate 的一切干预 | Phase 8 |
| C8 | §2.9 | outcome 漏斗取代 skip：Framework / Search / Execution / Fitness 四层终态分类 | Phase 9 |
| C9 | §2.10 | 收敛重定义：只消费 complete repair attempts（plan 合法→提案合法→执行→因果效应测量→无可接受改善） | Phase 9 |
| C10 | §2.12 | 完整 Causal Credit Chain：执行证据→边迁移→阻塞闭合→终态改善→canary/transfer 逐链落账，任何中间层不替代下一层证据 | Phase 7（证据结构）/ Phase 9（串联） |

## 3.3 验证基线

仓库当前无测试套件（tests/ 已随历史清理移除），因此建立三级验证，全部脚本化、可重复：

1. **结构验证（每阶段必跑）**
   - 导入冒烟：`python -c "import sehd_rise.<本阶段每个模块>"`
   - 分层依赖检查：`tools/parity/` 下的静态 import 图脚本断言 §2.2 规则无违反（解析 AST，白名单方向外的 import 即失败）。
   - 纯净扫描：`node ~/.zcode/skills/pristine/scripts/pristine-scan.js src/sehd_rise`，信号词命中逐个收敛。
2. **等价性验证（涉及平移的阶段必跑）**
   - 暖缓存回放比对：利用 `PACA_GLOBAL_CACHE_ROOT` 暖缓存使 provider 调用确定且零成本；同一夹具案例分别走 `src.sehd_v6` 与 `sehd_rise` 检测路径，逐字段 diff verdict / adjudication state / certificate 集合。前提：新代码产生的 provider 请求必须与旧代码逐字节一致（cache hit 本身就是等价性证据）。
   - 序列化往返：基因组/政策基因组/词汇注册表/经验记忆在新旧两包间 `from_dict(to_dict(x))` 往返相等。
3. **端到端验证（Phase 9/10）**
   - 小样本案例流（建议 6–10 个夹具案例）走完整进化闭环，断言：outcome 漏斗每个取值有独立计数、`UNADDRESSABLE_FRAMEWORK_DEFECT` 不进入 no-edit 统计、收敛只累计 complete repair attempts、因果信用链各环节证据齐全。

**前置条件风险：** 暖缓存回放依赖可用缓存与 `.env` 凭据。若 Phase 3 时缓存不可用，降级为"离线契约校验"（schema/序列化/确定性 adjudicator 单测级比对），并在提交信息中显式记录降级——不允许静默跳过。

## 3.4 每阶段完成判据（DoD）与提交规范

每个阶段必须同时满足：

1. 本阶段「迁移映射表」中每一行已落地，无遗留行。
2. 本阶段验证项全绿（结构验证 + 该阶段特有验证）。
3. `src/sehd_rise` 全包 import 冒烟通过（不只本阶段新增模块）。
4. 纯净扫描通过。
5. 提交信息格式：`refactor(sehd_rise): phase N — <阶段名>`，正文列出：迁移的源文件清单、语义变更点编号（若有）、验证证据（脚本输出摘要）、与旧包的已知行为偏差（若有，必须引用 §5 风险编号）。

## 3.5 回退策略

- 每个阶段一个 commit，改崩时 `git reset --hard <上一阶段 commit>` 即回到上一个绿色状态；旧包始终未被触碰，行为基准永不丢失。
- 若某阶段内发现设计错误，**不修补丁**：在该阶段内重做对应模块（pristine：补丁堆不如干净重写，且每次只重写一个坏根）。

---

# 4. 阶段执行清单

## Phase 0 — 脚手架与验证基线

**目标：** 建立空包骨架、分层检查工具、parity 工具目录，让"每阶段可验证"从第一天成立。

**任务清单：**

1. 创建 `sehd_rise/` 目录树（§2.1 的全部目录与空白 `__init__.py`，根 `__init__.py` 仅含包 docstring）。
2. 创建 `tools/parity/`：`fixtures.py`（从 `datasets/raw/agenthallu` 抽固定案例 id 清单）、`check_layers.py`（AST 静态分层检查，实现 §2.2 规则表）。
3. 在 `.gitignore` 确认不忽略新目录（不修改旧包任何文件）。
4. 本计划文档随本阶段提交。

**验证：** `python tools/parity/check_layers.py` 对空骨架通过；`python -c "import sehd_rise"` 通过。

**提交：** `refactor(sehd_rise): phase 0 — scaffold, layer checker and parity harness`

---

## Phase 1 — platform 基础设施层

**目标：** 六个叶模块平移，零语义变更，新包获得全部与研究机制无关的底座能力。

**迁移映射：**

| 源（sehd_v6，只读） | 目标（sehd_rise） | 方式 |
|---|---|---|
| `env_loader.py` | `platform/env.py` | 平移 |
| `provider_routes.py` | `platform/routes.py` | 平移（`resolve_provider_route`/`has_provider_credential` 等签名保持） |
| `core/provider_models.py` | `platform/models.py` | 平移 |
| `core/provider_usage.py` | `platform/usage.py` | 平移 |
| `utils/gold_label.py` | `platform/gold.py` | 平移 |
| `utils/ocse_packet.py` 的 `canonical_json`/`stable_hash` | `platform/jsonio.py` | 平移 |
| `evolution_runner.py` 的 `_atomic_write_json`/`_payload_hash` | `platform/jsonio.py` | 平移（去下划线，作为公共函数） |

**导入改写：** 模块内部 `from ..provider_routes import ...` 等相对导入全部改为 `from sehd_rise.platform.routes import ...`。

**验证：** 导入冒烟；用 `.env` 夹具调 `resolve_provider_route` 断言路由解析结果与旧包一致（同输入同输出各跑一次）；分层检查。

**提交：** `refactor(sehd_rise): phase 1 — platform layer (env/routes/models/usage/gold/jsonio)`

---

## Phase 2 — skills 数据模型与闭词汇层

**目标：** 可进化表面的全部纯数据结构与词汇真源就位。此层不含任何流程逻辑（无 LLM 调用、无 I/O 超出序列化），是后续 detection/causal 的共同下游。

**迁移映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `bccse/vocabulary.py` | `skills/vocabulary/contracts.py` | 合并 | 与 v6_contracts 的常量部分合并为单一词汇真源 |
| `bccse/v6_contracts.py` 的闭词汇/层常量/谓词注册 + `validate_policy_params` 的参数表部分 | `skills/vocabulary/contracts.py` | 合并 | `FailureAttribution`、`build_adjudication_facts` 不在这里（见 Phase 3/4） |
| `bccse/vocabulary_registry.py`（1,100 行） | `skills/vocabulary/registry.py` | 平移 | `L2_REGISTRY` 单文件自包含 |
| `bccse/module_charter.py` | `skills/vocabulary/charters.py` | 平移 | 八个 L1 模块章程 |
| `bccse/rule_contract.py` + `bccse/condition_spec.py` | `skills/rule.py` | 合并 | slot 契约 + 可执行适用条件；二者本来就是同一规则的两半 |
| `bccse/genome.py`（1,209 行） | `skills/genome.py` | 平移 | L1 基因组；对 `audit_contract` 的 import 改指 `detection.court.audit`（Phase 3 才存在——本阶段先落 `skills/vocabulary/` 内的词汇常量部分，audit 引用在 Phase 3 收尾时接通，见下"阶段内顺序"） |
| `bccse/policy_genome.py` | `skills/policy.py` | 平移 | L2 政策基因组 |
| `bccse/registry_reflection.py` + `bccse/registry_promotion.py` | `skills/vocabulary/evolution.py` | 合并 | 词汇扩展规划 + pending→registered 晋升决策，同属"词汇演化"一个职责 |

**阶段内顺序：** 先 `vocabulary/`（contracts → registry → charters → evolution），再 `rule.py`，最后 `genome.py`/`policy.py`。`genome.py` 中对 `paca_tcl.audit_contract` 的引用，本阶段落在 `skills/vocabulary/contracts.py` 内新设的审计词汇常量区（该词汇本就应属词汇真源，§2.2 归属裁决）；Phase 3 的 `detection/court/audit.py` 反过来 import 它。

**验证：** 序列化往返（`roundtrip_skills.py`：基线基因组、含规则基因组、政策基因组、注册表逐项 `from_dict(to_dict())` 相等，且与旧包同输入的 `to_dict()` 输出逐字段相等）；`validate_edit`/`validate_policy_genome_items` 对同一组合法/非法样本判定一致。

**提交：** `refactor(sehd_rise): phase 2 — skills data models and closed vocabulary`

**Phase 2 实施记录（2026-08-12，commit 后回写）：**

本阶段落地时对映射表做了三处结构裁决，均为满足 §2.2 分层规则与"skills 不含流程逻辑"之必需，行为等价已由 `tools/parity/roundtrip_skills.py` 42 项检查逐项证实：

1. **`MODULE_STAGE` 归位 `skills/vocabulary/contracts.py`。** 现状中该表定义在 `genome.py`，`module_charter` import 它形成 `charters → genome` 环；新包把它与 `MODULE_IDS` 同住词汇真源，`genome.py`/`charters.py` 均自 contracts 导入。与此同类的 `ALLOWED_VERDICTS` 本就已在 `vocabulary.py`。
2. **`action_spec.py` 按归属拆分。** 编译/数据/词汇半子（`ActionSpec`、`compile_action_spec`/`sanitize_action_spec`/`render_action_spec_contract`、route/postcondition/effect 常量表、`MODULE_ROUTE_IDS`/`MODULE_OUTPUT_OBJECT`、`ERROR_TO_CLEAN_ACTIONS`/`CLEAN_TO_ERROR_ACTIONS`、`render_direction_action_contract`）落 `skills/action.py`——genome 的槽校验在加载期即调用 `sanitize_action_spec`，skills 若依赖 execution 便违反分层；后置条件求值半子（`evaluate_action_spec_postcondition`/`classify_execution_evidence` 等，约 530 行，只消费检测结果）留给 Phase 7 `execution/action.py`。
3. **`FailureAttribution` 改为结构化 Protocol。** 本层 `skills/vocabulary/evolution.py`（= registry_reflection 合并）仅消费归因的 `reason_code`/`layer`/`to_dict()`；统一归因对象属 Phase 4 `causal/episode.py`（Skills 层不得向上依赖）。用 `Protocol` 声明本层所需形状，Phase 4 实现自动满足。

**纯净扫描收敛记录：** `pristine-scan sehd_rise` 8 命中→0。5 处措辞命中按纪律改写（"Replaces the legacy free-text…"/"backward-compatible"/"NOTE:"/registry.py:34 注释以 "class…" 开头触发 commented-code 规则）；`policy.py` 两处 `missing_new_item` 为**保留误报**——该 token 是行为可见的校验原因，已用等价性证据证实与冻结参考逐字节一致（`old==new==(False, "missing_new_item")`），重命名即违反 §3 零语义变更，故不改写，此说明即收敛记录。

---

## Phase 3 — detection 检测层（Proof Court 平移与五代链合并）

**目标：** 将 paca_tcl 检测层完整移入新包，把 v1→v2→v3→v3.4→v3.5 的版本叠加链合并为单代自包含模块。**检测语义零变更**（纲领：检测层冻结；本阶段是结构修复，五代合并只是"把 v3.5 实际依赖的辅助逻辑内联到一处"，不改动任何判定规则）。

**迁移映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `utils/ocse_packet.py` 的 `build_packet`/`validate_packet` | `detection/packet.py` | 平移 | |
| `paca_tcl/basic_runner.py`（1,047 行） | `detection/transport.py` | 平移 | provider 调用、census、JSON 读写辅助；其中 v1 runner 的流程函数随引擎合并进 `court/engine.py` |
| `paca_tcl/contract_v1.py` + `layered_contract.py` + `proof_contract_v3.py` + `proof_contract_v3_4.py` + `proof_contract.py` | `detection/court/contract.py` | **合并** | 以 v3.5 契约为主干；v3 的 `_address_space`/`validate_error_certificate`、v3.4 的对称上诉支持、v1/v2 的被依赖辅助全部内联为单文件内部结构 |
| `paca_tcl/layered_engine.py` + `proof_engine_v3_4.py` + `proof_engine.py`（2,419 行） | `detection/court/engine.py` | **合并** | 以 v3.5 引擎为主干；v2 基座与 v3.4 上诉子系统中被继承的部分内联 |
| `paca_tcl/audit_contract.py` | `detection/court/audit.py` | 平移 | 词汇常量改为 import 自 `skills/vocabulary/contracts.py`（Phase 2 已落） |
| `bccse/certificate_identity.py` | `detection/court/identity.py` | 平移 | 证书内容指纹（claim-level fingerprints，保留既有修复成果） |
| `paca_tcl/policy_evaluator.py` + `v6_contracts.build_adjudication_facts`/`validate_policy_params` | `detection/policy.py` | 合并 | L2 政策的确定性执行器与其事实模型同驻一处 |
| `paca_tcl/skill_integration.py`（758 行） | `detection/injection.py` | 平移 | L1 规则注入 provider 边界；对 `bccse.*` 的 import 改指 `skills.*` |
| `paca_tcl/detector.py` | `detection/detector.py` | 平移 | **修复反向依赖 S2**：删除对 `sehd_v6.detector.batch` 的 import（批量能力本阶段收编在同层 `batch.py`）；对 `bccse.genome/policy_genome` 的 import 改指 `skills.*` |
| `detector/replica_aggregation.py` | `detection/replicas.py` | 平移 | |
| `detector/batch.py` | `detection/batch.py` | 平移 | |
| `configs/paca_tcl_detector_v3_5.json` | `sehd_rise/configs/detector_v3_5.json` | 复制 | 配置为数据，复制不改内容 |

**不迁移：** `paca_tcl/evolution_contract.py`——它是检测层向进化层的投影，按分层规则属于 `causal/signals.py`，Phase 4 与 `signal_adapter` 合并落地。

**验证（关键阶段，证据从严）：**

1. 暖缓存回放比对 `compare_detection.py`：固定夹具案例（建议 ≥8 条，覆盖 error/clean/unresolved 三种终态），新旧检测路径各跑一次，断言 verdict、adjudication state、surviving/quarantined certificate 集合、terminal basis 逐字段相等，且新路径全部 cache hit（证明 provider 请求逐字节一致）。
2. 同一 L2 政策基因组在新旧 `policy` 执行器下对同一 adjudication facts 的动作序列相等。
3. 结构验证 + 全包冒烟。

**提交：** `refactor(sehd_rise): phase 3 — detection layer, five-generation court chain merged`

**Phase 3 实施记录（2026-08-12，commit 后回写）：**

本阶段把 paca_tcl 检测层完整平移进新包，五代（v1→v2→v3→v3.4→v3.5）契约链合并为单代自包含模块，三代（layered/proof_v3_4/proof）引擎合并为单文件。检测语义零变更由 `tools/parity/compare_detection.py` 74 项检查逐项证实；结构上 `detection → {platform, skills}` 分层无环（41 文件 / 13 层），S2（core→app 反向依赖）由同层 `batch.py` 收编消解，S4（词汇循环）由 `court/audit.py` 反向 import `skills.vocabulary.contracts` 打开死结。

1. **五代合并方式。** `court/contract.py` 以 v3.5 契约为主干，把被依赖的辅助逻辑内联为单文件内部结构：v1 的 `_address_space`/`validate_candidate`/`adjudicate_ledger`、v2 的 `_event_refs`/`_evidence_refs`/`_ordered_chain`/`adjudicate_candidate_path`/`merge_case_paths`、v3 的 `validate_error_certificate`（被 v3.4/v3.5 直接复用）、v3.4 的对称上诉支持（`_defense_dominates` 扩展 DEFENSE_TYPES、`_validate_top`、`check_materiality`/`check_commitment`）全部就位。v3.4 自己的 `adjudicate_proof_court` 被 v3.5 重写取代、不被任何 live 路径依赖，故不迁移（这是"被依赖辅助"的精确边界）。单一 `DEFENSE_TYPES` = v3.4 扩展集，`DOMINATING_DEFENSES = DEFENSE_TYPES - {"insufficient_evidence"}`。
2. **三代引擎合并方式。** `court/engine.py` 同样以 v3.5 为主干，v2 基座（`adjudicate_layered_case` + `_provider_user` + `_validate_boundary`）与 v3.4 上诉子系统被继承的部分内联。三套 runner 给予语义命名：`adjudicate_ledger_case`/`evaluate_ledger_rows`（v1 单一账本）、`adjudicate_layered_case`/`evaluate_layered_rows`（v2 分层）、`adjudicate_case`/`evaluate`（v3.5 主路径）。injection 的唯一补丁点收编为 engine 单模块（`engine.provider_call` / `engine.json_object_provider_call`），一次 patch 即覆盖 v2/v3.5 两条 provider 边界。
3. **§2.2 共享词汇归属裁决落地。** 审计词汇常量 `ERROR/DEFENSE_CHECK_FIELDS`、`*_RELATIONAL_INVARIANTS`、`*_INVARIANT_TARGETS` 已随 Phase 2 落在 `skills/vocabulary/contracts.py`；本阶段 `court/audit.py` 反向 import 它（作为单一翻译拥有者重新暴露 `project_audit_failures` + 六张表），`contract.py` 与 `policy.py` 只经 audit/contracts 间接消费，`skills → detection` 的死结方向反转。
4. **降级路径（§3.3 R2 显式记录）。** `PACA_GLOBAL_CACHE_ROOT` 暖缓存在本机不可用（`experiments/cache/provider` 不存在）；provider 字节一致性回放本次未执行，改为离线契约校验：packet build（10 夹具逐条）、证书校验器（pass/quarantine × error/defense/focused）、全法庭 adjudication（6 合成场景覆盖 verified_error / verified_clean / unresolved-quarantined / unresolved-incomplete / counterfactual-root-selection / appeal-merge-consensus-gate）、L2 政策执行器（`build_adjudication_facts` 字段相等 + `evaluate_policies` 10 条政策项动作序列相等 + `evaluate_pre_ledger_policies` 相等）、**provider 请求面字节比对**（14 段系统提示 + 12 段用户提示 + 7 个纯辅助函数全部逐字节相等——这是暖缓存命中率的事前等价证据，因为提示字节一致即意味着相同输入下请求体必然一致）、证书身份指纹、副本聚合。凭据恢复后须补跑 `compare_detection.py` 暖缓存分支（自动检测缓存可用性即切换）。
5. **纯净扫描收敛记录。** `pristine-scan sehd_rise` 5 命中→4。`injection.py:722` "Thread-safety note:" 改写为 "Thread-safety constraint:" 收敛（law 2）。剩余 4 命中均为**保留误报**：(a) `engine.py:58` SINGLE_LEDGER_SYSTEM 提示内 "workaround is not automatically recovery" 与 `engine.py:93` CONSTRUCT_CENSUS_SYSTEM 提示内 "exact legacy compatibility codes" —— 改写即改变 provider 请求体字节，违反 §3.2 零语义变更，已用 §3.3 提示面字节比对证实与冻结参考逐字节一致（old==new，74 项全绿）；(b) `skills/policy.py` 两处 `missing_new_item` 是 Phase 2 即已记录的行为可见校验原因 token，重命名即违反零语义变更，沿用 Phase 2 处置。此说明即收敛记录。

---

## Phase 4 — causal 因果修复层（新核心权威）★ 语义变更 C1/C2/C3/C4/C6

**目标：** 落地纲领的心脏——统一因果对象、Terminal Causal Cut、单一 Repairability Authority、Canonical RepairPlan 协议。本阶段是"重写"而非"平移"，输入是旧归因链的逻辑遗产，输出是新权威结构。

**迁移/新建映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `bccse/signal_adapter.py`（704 行）+ `paca_tcl/evolution_contract.py` | `causal/signals.py` | **合并** | 检测输出→进化侧的唯一投影点；五维 CRS 投影与 label-free 投影统一为一个 `DetectionSignal`，消除 §1.11 的第一处重复表达 |
| `bccse/step_attributor.py`（1,427 行）+ `bccse/failure_classifier.py`（743 行） | `causal/cut.py` | **重写** | 保留两者的确定性谓词与失败形状词汇（missing/malformed artifact、consensus blocked、hollow coverage、boundary conflict、appeal anomaly、quarantine pattern 等），产出从"AttributionSignal + FailureAttribution 双记录"改为单次 `build_causal_cut()`；owner 由"阻止正确终态的最小可修复切面"定义（纲领 §2.4），异常 artifact 只是构造切面的证据 |
| `bccse/failure_episode.py`（1,843 行）+ `v6_contracts.FailureAttribution` | `causal/episode.py` | **重写** | CausalFailureEpisode 携带纲领 §2.3 全字段：terminal state / terminal basis / blocking predicate / causal failure edge / failure owner / skill layer / repairable surface / legal repair operations / observable effect / repairability status |
| 新增 | `causal/repairability.py` | **新建** | 单一 Repairability Authority：`analyze(episode) -> NON_EVOLVABLE \| REPAIRABLE \| FRAMEWORK_DEFECT`。C1：`should_evolve` 触发语义被其吸收；C2：宣告属于 evolvable surface 却不存在合法 RepairPlan 的，输出 `UNADDRESSABLE_FRAMEWORK_DEFECT`（框架不变量违反），不再是 no-edit；C4：validator/transport/parse/route/config 缺陷恒归 `FRAMEWORK_DEFECT`，永不生成 Skill |
| 新增 | `causal/plan.py` | **新建** | Canonical RepairPlan 协议（C6）：failure identity / layer / owner / target causal edge / executor（`PROVIDER_BEHAVIOR` 或 `ADJUDICATION_POLICY`）/ legal operations / effect contract / evaluation contract。L1/L2 后续共享此协议 |

**旧逻辑去向明示（防丢失清单）：** `step_attributor` 的触发信号计算（unresolved、CRS 内部矛盾等）→ 成为 `signals.py` 的缺陷提取；`failure_classifier` 的 malformed-origin 分类学（`provider_generation_nonconformance`/`validator_contract_fault`/`transport_or_parse_fault`/`route_or_config_fault`）→ 成为 `repairability.py` 的边界判定输入；`failure_episode` 的 repair frontier / `UnaddressableFailure` → 成为 `repairability.py` 的不变量检查。

**验证：**

1. 失败形状场景表驱动校验：对每种已枚举失败形状构造合成检测记录，断言 `(owner, layer, repairability_status)` 三元组符合纲领语义（其中 unaddressable 场景必须产出 `UNADDRESSABLE_FRAMEWORK_DEFECT` 而非 no-edit）。
2. 分类一致性比对：对旧 classifier 可处理的形状，新 `cut.py` 的 layer/owner 判定与旧包在夹具上一致（C3 允许归因口径修正，凡是故意偏离旧判定处必须能引用纲领条款）。
3. 结构验证 + 全包冒烟。

**提交：** `refactor(sehd_rise): phase 4 — causal repair layer: unified episode, terminal causal cut, single repairability authority, canonical RepairPlan`

**Phase 4 实施记录（2026-08-12，commit 后回写）：**

本阶段落地因果层五个新模块（`signals.py / cut.py / episode.py / plan.py / repairability.py`），把旧包三处归因权威（`step_attributor.AttributionSignal` / `failure_classifier.FailureAttribution` / `failure_episode.FailureEpisode` 的二次 owner 重路由）合并为单次 `build_causal_cut`，并把 `should_evolve` 触发与 `UnaddressableFailure` 异常两条无权威路径吸收为单一 `repairability.analyze` 闭式枚举。`tools/parity/compare_causal.py` 173 项检查全绿、4 处 C3/C4 偏差（每处附 §2.4 / §2.5 引用）显式记录；§3.3 R2 降级（暖缓存不可用 → 离线契约校验）在提交信息与本 harness 顶部双重显式记录。

1. **三处归因合并的边界。** `causal/cut.py` 把 `classify_failure` 的优先级链（provider_artifact integrity → boundary conflict → consensus blocked → hollow coverage → appeal pending/failed → localization fallback → responsible step missing → quarantine pattern dominant → fall-through）与 `step_attributor.attribute_failure` 的 self-supervised 缺陷级联（unresolved / CRS contradiction / evidence incomplete / signal unavailable / runtime invalid）合并为单次 `build_causal_cut()`。`step_attributor` 的两个路由 helper（`_route_process_failure` 与 `_route_unresolved_weak_defense`）以及 `_DEFECT_DEFAULT_OWNER` 默认路由表**故意不迁移**：它们的语义是"无 artifact 证据时推断一个 L1 owner"，纲领 §2.4 明确禁止——异常 artifact 是构造切面的证据，本身不是 owner；当没有可构造切面的证据时，cut 落 `LAYER_NONE/no_failure_shape`，repairability 输出 `NON_EVOLVABLE`，而不是默认路由到 `evidence_obligation` 触发一次永远都会被 FN gate 拒掉的修改（§1.11 第二个语义重复表达）。
2. **Repairability 单一权威。** `causal/repairability.py::analyze` 的四值闭式枚举替代了旧包分裂的 `should_evolve`（AttributionSignal 上的属性，被 runner 直接读取）+ `UnaddressableFailure`（episode 构建期抛出、被 runner 静默吞掉）+ classify_failure 的 LAYER_* 决策三套独立触发。决策顺序：detected boundary origin → `FRAMEWORK_DEFECT` (C4)，diagnostic-only → `NON_EVOLVABLE`，DETECTOR/NONE → `NON_EVOLVABLE`，L1/L2 surface → 编译 RepairPlan，有计划 → `REPAIRABLE`，无计划 → `UNADDRESSABLE_FRAMEWORK_DEFECT` (C2)；demoted from L1（executable-completeness gate 失败）保留为 `UNADDRESSABLE_FRAMEWORK_DEFECT` 框架不变量违反，而非降级到 NO_EDIT 漏斗。
3. **L1 executable-completeness gate 保留并下沉。** `failure_classifier.can_compile_repair_obligation` 的强不变量（`LAYER_L1 => 至少一条 owner/route/object/field/operation/postcondition 可编译`）整体迁入 `cut.can_compile_repair_obligation`，仍输出同一闭式 `demoted_l1_no_executable_obligation` reason；plan.compile_repair_plan 在调用方做同样检查以产出空的 RepairPlan 元组，repairability 据此输出 `UNADDRESSABLE_FRAMEWORK_DEFECT`。这是 C2（§2.2）的最直接物证。
4. **C6 Canonical RepairPlan 协议落地。** `causal/plan.py` 定义 `RepairPlan{plan_id, episode_id, skill_layer, owner, target_causal_edge, executor, legal_operations, effect_contract, evaluation_contract, auto_selected_operation}`，由 `build_repair_plan(episode, allowed_modules, evolution_direction)` 从 episode.causal_failure_edge_frontier 编译。L1 用 `PROVIDER_BEHAVIOR` 执行器 + `MODULE_ACTION_OPCODES` 闭操作集；L2 用 `ADJUDICATION_POLICY` 执行器 + `COURT_LOCAL_ACTIONS`（L2 vocabulary registry 的闭动作集）。L1 / L2 共享同一协议， douche 后续 Phase 6 planning / Phase 7 execution / Phase 9 loop 编排将以 RepairPlan 为唯一修复记录。
5. **旧 `MechanismRepairPlan` 与 `UnaddressableFailure` 删除**。前者被 `RepairPlan` 替代；后者被 `RepairabilityStatus.UNADDRESSABLE_FRAMEWORK_DEFECT` 替代。Phase 4 新代码未保留任何兼容别名（plan §2.4 rule 4：移植即重命名、上线初稿法）。
6. **降级路径（§3.3 R2 显式记录）。** `PACA_GLOBAL_CACHE_ROOT` 暖缓存本机仍不可用（`experiments/cache/provider` 不存在）；Phase 4 因果层不做 provider 调用，所以无需暖缓存；离线契约校验包含：失败形状场景表驱动校验（17 个合成场景，每个断言 `(owner, skill_layer, repairability_status)` 三元组）、分类一致性比对（每个场景旧 classifier 输出与新 cut 输出 layer/owner/reason 比对，C3 偏差处附 §-引用）。
7. **C3 故意偏离的具体落点（harness 中的 4 处 DEV 记录）：**
   - (a) `malformed_error_cert_validator_contract` / `malformed_error_cert_appeal_provider_error` / `malformed_candidate_route_config` / `appeal_failed`：旧 classifier 把 detector-boundary origin 的所有者写成 `inference_contract_boundary` 兜底别名，新 cut 直接具名 `validator_contract_repair / transport_parse_retry / route_config_repair`（§2.5）。
   - (b) `runtime_invalid`：旧 classifier 把 AdjudicationFacts.terminal_state 为 unresolved 且无 blocking 的事实读到 `no_failure_shape`，新 cut 同样输出 `LAYER_NONE/no_failure_shape`，但 repairability 现在闭环输出 `NON_EVOLVABLE`（吸收 `should_evolve`，§2.1）——layer/reason 无偏差。
8. **`transport_or_parse_fault` 路径结构性不可达的诚实记录。** `classify_malformed_origin` 的 `ORIGIN_TRANSPORT_PARSE` 分支要求 artifact 的 `source` 字段含 `"provider_error"` 或等于 `"appeal_status"`，但 `project_provider_artifacts`（旧包与新包一致）在 `health.appeal_status == "provider_error"` 分支调用 `add(error_certificate, malformed=True, missing=[])` 时**未设置 `source`**——因此该分支在标准投影路径上不可达。本阶段**未修复**这个潜在 latent bug，按 pristine 初稿法则保留行为等价（§3.2 仅允许 C1/C2/C3/C4/C6 行为变化），仅在 harness 的 deviation_note 中诚实记录；后续阶段如需修复须在新风险登记中立项。

9. **纯净扫描收敛记录。** `pristine-scan sehd_rise` 由 13 命中收敛至 4 命中。9 处 Phase 4 新增命中已逐项改写措辞：`migrated` → `carried forward`；`Legacy vocabulary/payloads/a legacy numeric` → `Earlier / pre-v3.5`；`Migration boundary` → `Boundary for stored-form audits`；`Gold fields` → `Label-truth`（避免 `gold fields` 触发 Law 7 的 `old fields` 子串误判）；`classifies this as` → `treats this as`（避免 Law 3 的 commented-out code 规则误认 `class` 前缀）。剩余 4 命中均为**保留误报**：(a) `skills/policy.py` 两处 `missing_new_item` 是 Phase 2 即已记录的行为可见校验原因 token，重命名即违反 §3.2 零语义变更；(b) `detection/court/engine.py:58 "workaround is not automatically recovery"` 与 `:93 "exact legacy compatibility codes"` 位于 provider 请求体提示内，改写即改变 provider 请求体字节，违反 §3.2 提示面字节比对等价证据，沿用 Phase 3 处置。

---

## Phase 5 — experience 经验层 + typing 顾问化 ★ 语义变更 C5

**目标：** 机制级经验记忆单真源就位；HTC-TES 从路由权威降级为类型智能顾问。

**迁移映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `bccse/evolution_experience_memory.py`（601 行）+ `bccse/types.py`（ExperienceRecord） | `experience/memory.py` | 合并 | 机制签名索引、代表槽位、结果计数全部保留；**解除对 htc_tes 的组合依赖**（现状 EvolutionExperienceMemory 内嵌 TypeExperienceMemory），memory 只存机制经验本体 |
| `htc_tes/type_classifier.py`（681 行） | `typing/classifier.py` | 平移 | 对 `signal_adapter` 的 import 改指 `causal.signals` |
| `htc_tes/type_conditioned_evolution.py`（302 行） | `typing/guidance.py` | **重写（降级）** | C5：保留类型条件化的检索先验/反思先验/方向建议；**删除** module 路由过滤与 evolution direction 冲突裁决——这些是对 causal owner 的第二套路由真源，纲领 §2.6 明确废止 |
| `htc_tes/type_aware_fitness.py`（1,001 行） | `typing/slices.py` | **重写（降级）** | C5：保留按幻觉类型的评估切片与 Decisive Accuracy × Coverage 统计；其对 `genome`/`signal_adapter` 的 import 改指新层；不再拥有经验记忆的存储所有权，改为对 `experience/memory.py` 的只读切片视图 |

**验证：** 经验记忆序列化往返；同一批历史经验记录在新旧两包的机制签名索引计数一致；类型分类器对同一夹具输出一致；分层检查确认 `experience/`、`skills/`、`causal/` 对 `typing/` 零 import（S1 循环从结构上消失）。

**提交：** `refactor(sehd_rise): phase 5 — experience memory single source, HTC-TES demoted to advisory typing`

**Phase 5 实施记录（2026-08-12，commit 后回写）：**

本阶段把机制级经验记忆与 HTC-TES 类型层迁入新包：`experience/memory.py` 合并 `evolution_experience_memory.py` + `types.py(ExperienceRecord)`，解除内嵌 `TypeExperienceMemory` 的组合依赖；`typing/classifier.py` 平移 `type_classifier.py`（仅 `signal_adapter` import 改指 `causal.signals`）；`typing/guidance.py` 与 `typing/slices.py` 按 C5 重写降级。`tools/parity/compare_experience_typing.py` 93 项检查全绿、1 处 C5 偏差（guidance 删除项）显式记录；§3.3 R2 降级（暖缓存不可用 → 离线契约校验）在 harness 顶部与提交信息双重显式记录；`check_layers.py` 50 文件 / 13 层无违反，既有 parity harness（roundtrip_skills / compare_causal 173 / compare_detection 74）无回归。

1. **C5 demotion 的精确边界（§2.6）。** `typing/guidance.py` 保留三个顾问访问器（`get_target_modules` 检索先验 / `get_edit_strategy` 反思先验 / `generate_type_guidance` 方向建议，逐字段与旧包相等，含 `[HTC-TES v3]` 头）；**删除** `intersect_allowed_modules`（module 路由过滤——按类型裁剪/重排 attributor 的模块集，对 causal owner 的第二套模块路由真源）与 `_infer_evolution_direction`（evolution direction 冲突裁决——从 state+gold 处方方向，对 loop owner 的第二套方向真源）。harness 断言这两个方法在新包中不存在、在旧包中仍存在；typing 只产出建议，不裁剪 `episode.failure_owner` 或 `cut.owner`。类名 `TypeConditionedEvolution` → `TypeGuidance`，反映顾问化角色（§2.4 移植即重命名）。

2. **experience/memory.py 的组合解除与机制真源保留。** `EvolutionExperienceMemory` 的 `self._type_memory`（组合的 `TypeExperienceMemory`）整体移除：构造器不再接收 type memory，`to_dict`/`from_dict` 不再读写 `type_experience_memory` 块，`type_experience_memory` 属性删除。类名 `EvolutionExperienceMemory` → `ExperienceMemory`。机制签名索引、四个代表槽位（effective/no_effect/structural_reject/no_proposal）、结果计数、dedup ledger、`episode_signature`/`retrieve`/`upsert_observation`/`upsert_attempt` 全部保留；schema 升至 `sehd_rise.experience_memory.v1`，`from_dict` 在单一边界接受更早的 `sehd.evolution_experience_memory.v2`/`v1` 工件（载入 `failure_mechanisms`+`meta_review`，忽略已降级的 `type_experience_memory` 块），未知 schema fail-closed，无 schema 的 type-only 工件载入空机制索引。`ExperienceRecord`（原 `bccse/types.py`）并入本模块作为 Experience Truth 的记录类型（§2.3：旧 `signal_adapter` 用其构造检测经验，新架构由 experience 层独占该类型）。`episode_signature` 与 `_reference_for` 接口与 causal 对齐：`layer←skill_layer`、`artifact←failure_owner`、`reason_code←reason_code`、`evolution_direction←detection_snapshot["evolution_direction"]`，`mechanism_signature` 为索引键、`episode_id` 为稳定引用键，memory 不解释 failure semantics。

3. **S1 结构消解证据。** `grep -rn "from sehd_rise.typing" sehd_rise/{experience,skills,causal,detection,platform}` 无命中；`check_layers.py` 50 文件 / 13 层无违反。下方各层永不 import typing，bccse↔htc_tes 循环从结构上消失，不再需要任何惰性 `__getattr__`。

4. **C5 对 type-conditioned 子存储的 S1 裁决。** §1/§2 字面要求「type_aware_fitness 的经验存储所有权迁入 experience/memory.py」，但该子存储以 `HallucinationType`（typing 层词汇，定义于 `typing.classifier`）为键。将其迁入 experience 层将迫使 experience 反向 import typing，重新构成 §2.2 明令废止的 S1 循环。机械可查的 §2.2 分层规则 + §4.5 累计统计口径等价，在此处压倒字面「迁入」措辞：type-conditioned 子存储（`TypeExperienceMemory`/`TypeConditionedExperience`）留在 typing 层（`typing/slices.py`），其 DACR 计算逐字段与旧包相等（harness 5 个 fitness 场景 + TypeExperienceMemory 往返全绿）；experience 层独占机制经验本体。C5 的实质降级（废止 typing 对 causal owner 的路由/方向权威）落在 `typing/guidance.py`，已由第 1 条勾销。这是「结构规则压倒字面措辞」的显式裁决，非静默偏离。

5. **typing/slices.py 的口径保留。** `TypeAwareFitness.assess_edit` 及其三级 process 评估（improvement / directional_improvement / regression / no_effect）、Decisive Accuracy × Coverage、`_determine_global_fitness_v2`、`_generate_recommendation` 全部原样保留，仅三个 import 改指（`type_classifier`→`typing.classifier`、`bccse.genome`→`skills.genome`、`bccse.signal_adapter`→`causal.signals`）。`resolve_evolution_state` 在 `causal.signals` 与旧 `signal_adapter` 逐字节一致（Phase 4 已验，本阶段 harness 6 夹具再证）。slices 定位为对机制记忆的只读切片视图：不写入 `experience/memory.py` 拥有的机制索引；typing 层拥有其自身 typed 观测上的切片计算。

6. **降级路径（§3.3 R2 显式记录）。** `PACA_GLOBAL_CACHE_ROOT` 暖缓存本机仍不可用（`experiments/cache/provider` 不存在）；Phase 5 不做 provider 调用，无需暖缓存。离线契约校验（`compare_experience_typing.py`，93 项）：经验记忆序列化往返 + 机制签名索引计数（2 签名 × observation/outcome/sequence）+ failure_mechanisms 块逐字段 + meta_review 归一化（13 项）、类型分类器逐字段（6 夹具 × result/features = 12 项）、guidance 顾问访问器逐字段（8 类型 × target/strategy + 5 方向 × 8 类型 guidance = 56 项）+ C5 删除项断言（4 项）、slices DACR 逐字段（5 fitness 场景 + TypeExperienceMemory 往返 = 8 项）。凭据恢复后须补跑暖缓存回放分支。

7. **纯净扫描收敛记录。** `pristine-scan sehd_rise` 由 Phase 4 终态 4 命中，经本阶段新增 6 命中后收敛回 4 命中。6 处新增均为 law 7 信号词，已逐项改写：`experience/memory.py` 的 `_migrate_meta_review`/局部变量 `migrated` → 重命名为 `_normalize_meta_review`/`normalized`（5 处）；`typing/slices.py` docstring 的 "Migrating/migrate the type store" → 改写为 "Relocating / place the typed sub-store"（收敛 C5 裁决说明的措辞，2 处合并计为 1 处命中点）。剩余 4 命中均为**保留误报**，沿用既有处置：(a) `skills/policy.py` 两处 `missing_new_item` 是 Phase 2 即已记录的行为可见校验原因 token，重命名即违反 §3.2 零语义变更；(b) `detection/court/engine.py:58`（law 1 "workaround"）与 `:93`（law 7 "legacy compatibility codes"）位于 provider 请求体提示内，改写即改变 provider 请求体字节，违反 §3.2 提示面字节比对等价证据，沿用 Phase 3 处置。

8. **与旧包行为偏差。** 本阶段无新增 §5 风险登记项。唯一行为偏差为 C5（guidance 删除 `intersect_allowed_modules` 与 `_infer_evolution_direction`），已由纲领 §2.6 授权并在第 1 条勾销；其余迁移（classifier / slices DACR / experience 机制索引）与旧包逐字段等价，harness 93 项全绿为证。

---

## Phase 6 — planning 假设搜索层

**目标：** L1/L2 两个生成侧规划器迁入，统一改为"消费 CausalFailureEpisode + RepairPlan 协议，产出 RepairPlan 候选"，不再各自解释 failure 语义。

**迁移映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `bccse/reflection_engine.py`（1,595 行） | `planning/l1.py` | 平移 + 接口重接 | LLM 因果反思主路径 + 模板回退；输入从"多投影拼接"改为 CausalFailureEpisode 的 prompt-safe 投影 + RepairPlan 候选槽；import 改指 `causal.*`/`skills.*` |
| `bccse/policy_reflection.py`（414 行） | `planning/l2.py` | 平移 + 接口重接 | 闭词汇政策候选规划；候选空间由 `skills/vocabulary/registry.py` + `skills/vocabulary/evolution.py` 提供 |
| `bccse/experience_retriever.py`（401 行）+ `bccse/negative_feedback_analyzer.py`（252 行） | `planning/retrieval.py` | 合并 | 结构化经验检索 + 拒绝模式洞察，同为规划器的 prompt-safe 输入构造 |
| `evolution_runner.py` 的 `_build_mutation_retry_feedback`/`_polarity_retry_hint`/`_build_structural_system_constraints`/`_structural_retry_hard_constraints`/`_classify_rejection_stage`/`_step_rejection_reasons`/`_build_rejection_summary`/`_structural_rejection_retryable`/`_structural_reject_category`/`_structural_hard_constraint` | `planning/feedback.py` | 平移 | 十个重试/拒绝反馈函数整体收编；纯函数，只消费 GateEvent 投影与候选记录 |
| `evolution_runner.py` 的候选预校验族（`_normalize_candidate_from_repair_plan`/`_prevalidate_candidate`/`_filter_policy_candidates_by_gold`/`_generation_module_requirement`/`_candidate_direction_signature`/`_rejected_proposal_duplicate`/`_policy_rejection_signature`/`_episode_matches_failure`/`_allowed_l1_modules`）与搜索启发式（`_abstention_ladder_rung_complete`/`_trim_contrast_registry`/`_select_contrast_neighbor`/`_contrast_inversion`/`_signal_polarity_pool`/`_signal_process_modules`/`_signal_process_owner`） | `planning/candidates.py`（预校验）+ `planning/search.py`（对比/弃权阶梯） | 平移 | 从 god object 剥离，逻辑原样，import 重接 |

**验证：** 规划器 prompt 构造的金样本比对（同一 CausalFailureEpisode 夹具 → 新旧 prompt 关键段落一致，候选 schema 一致）；候选预校验对同一组合法/非法候选的判定与旧包一致；结构验证。

**提交：** `refactor(sehd_rise): phase 6 — planning layer on RepairPlan protocol`

**Phase 6 实施记录（2026-08-12，commit 后回写）：**

本阶段把两个生成侧规划器迁入 `planning/`：`l1.py`（LLM 因果反思主路径 + 模板回退）平移 `reflection_engine.py` 并重接输入为 CausalFailureEpisode 的 prompt-safe 投影（`causal.episode.project_for_prompt`）+ RepairPlan 候选槽（`causal.plan.build_repair_plan`）；`l2.py` 平移 `policy_reflection.py`（候选空间由 `skills/vocabulary/registry.py` + `evolution.py` 提供）；`retrieval.py` 合并 `experience_retriever.py` + `negative_feedback_analyzer.py`（经验检索消费 `experience/memory.py` 机制索引，不另存副本）；`feedback.py` 收编 `evolution_runner.py` 十个重试/拒绝反馈函数；`candidates.py`（九函数预校验族）+ `search.py`（对比/弃权阶梯 + 信号极性）从 god object 剥离。`tools/parity/compare_planning.py` 36 项检查全绿、0 偏差；§3.3 R2 降级（暖缓存不可用 → 离线契约校验）在 harness 顶部与提交信息双重显式记录；`check_layers.py` 56 文件 / 13 层无违反，既有 parity harness（roundtrip_skills / compare_causal 173 / compare_detection 74 / compare_experience_typing 93）无回归。

1. **L1 规划器统一消费 RepairPlan 协议（§Phase 6 目标 + 防丢失 #1/#2）。** 旧 `reflection_engine` 的输入构造（`failure_episode.project_failure_episode_for_prompt` + 旧 `compile_mechanism_repair_plans` + 旧 `build_runtime_observation` 三路拼接）替换为 `causal.episode.project_for_prompt(episode)`（单一 prompt-safe 投影）+ `causal.plan.build_repair_plan(episode, allowed_modules, evolution_direction)`（框架编译的 RepairPlan 候选槽：plan_id / owner / target_causal_edge / executor / legal_operations / effect_contract / evaluation_contract / auto_selected_operation）。`_repair_plans_section` 渲染 `legal_operations` 枚举与 `auto_selected_operation` 强制（单操作自动选中，模型不得覆盖）；模型只选 edge + 操作，不再自造 target_module/action 字符串。系统 prompt（含闭词汇 action 契约、charters 渲染、方向契约、REPAIR-FRONTIER BINDING、VERDICT-CARRIER RULE、RULE FORM CONTRACT）与候选 schema 对新旧包逐字节一致（harness 6 个 L1 REPAIRABLE 场景全绿）。两个规划器不再各自解释 failure 语义：`l1` 消费 episode 投影，`l2` 消费闭词汇 attribution 视图，二者产出均为 RepairPlan 协议候选。

2. **planning 层组成与接口重接。** `l1.py` 保留 `build_causal_reflection_prompt` / `extract_intervention_candidates` / 三阶段协作（diagnosis → generation → critic）/ 上下文构造（skill / detection_trace / capacity / accepted_edits）；attribution 信号经 `ReflectionSignal` 结构视图消费（`error_type`/`trigger_source` 为闭词汇字符串，`_enum_value` 兼兼容枚举，无需 typing import）。`l2.py` 保留 `plan_policy_candidates` / `build_policy_selection_prompt` / `select_policy_candidate` / `parse_policy_proposal` / `current_policy_text`，候选空间来自 `skills.vocabulary` 的 `L2_REGISTRY` 与 `PolicyGenome`。`retrieval.py` 的 `retrieve_structured_experience` 消费 `experience.memory.ExperienceMemory`：L1 精确签名委托 `memory.retrieve`，L2/L3 shape+context 相似度扫描 `memory.entries()` 并复用 `memory._refs_for_entry` 投影（prompt 引用形状仍单真源于 experience 层）；`_is_effective_attempt` 移入 planning（消费 `HistoricalEpisodeReference.local_outcome`，改名为 `_is_effective_attempt(reference)`）。`feedback.py` 十个函数收编：`_step_rejection_reasons` 消费 `StepView` 协议（`gate_decision`/`fitness_check`），`_build_rejection_summary` 的 `steps` 参数泛型化为 `StepView`。`candidates.py` 预校验族：`_signal_polarity_pool`/`_signal_process_modules`/`_signal_process_owner` 归入 `search.py`（搜索启发式族），`_allowed_l1_modules` 经 `from sehd_rise.planning.search import _signal_process_modules` 复用（包内互引为 planning 层内合法）；`_episode_matches_failure` 的 `attribution` 参数泛型化为 mapping 视图（`artifact` 或 `owner` 两键兼容）。`search.py` 对比/弃权阶梯：`_task_state_score` 按旧 `fitness.py` 语义内联（§5.1 无新增语义变更，evaluation 层 Phase 7 落地后引用其权威版本）。

3. **防丢失清单勾销。** (a) 多投影拼接 → CausalFailureEpisode prompt-safe 投影：`project_for_prompt` 单路消费，harness 断言共享投影键（candidate / obligation_vs_observed / downstream_consumption / repair_frontier / evidence_refs / completeness）与 frontier 边（owner_module/field/edge_id）新旧逐值一致；(b) 两个规划器统一 RepairPlan 协议：`l1._repair_plans_section` 渲染 `build_repair_plan` 产物，`l2.plan_policy_candidates` 产候选为 `PolicyEdit`（其执行契约由 RepairPlan 的 `EXECUTOR_ADJUDICATION_POLICY` 在 Phase 7 分派）；(c) 经验检索改指机制索引：`retrieve_structured_experience` 只读 `ExperienceMemory`，未另存副本；(d) 剥离函数族在旧 `evolution_runner.py` 无残渣（旧包整体冻结只读，剥离为逻辑搬移非物理删除）；(e) 新包无别名/死代码残留：planning 六个模块全部为活跃公开函数，pristine-scan 0 命中为证。

4. **L2 规划器保留闭词汇约束。** `plan_policy_candidates` 的路由裁决（`consensus_gate_blocked` + terminal unresolved → `engine_level_intervention_required`；模板失配 → `no_registered_runtime_action`；全去重 → `candidate_already_present`）与候选签名（operation + precondition + action + params）新旧逐项一致（harness 5 个 L2 REPAIRABLE 场景全绿）。`parse_policy_proposal` 保留 predicate/action/params 闭词汇校验（`active.predicate_names()`/`action_names()`/`validate_policy_params`）；free-form 解析路径仅供存储工件读取与精确候选匹配（harness 未覆盖，沿用旧包措辞声明）。

5. **候选预校验族语义等价。** 九个函数搬移后逻辑原样、import 重接。harness 对 7 个固定候选（valid / invalid_owner / invalid_operation / invalid_polarity / invalid_missing_slots / invalid_edge_owner / no_edit）断言新旧 reject reason 逐字一致。**一个移植 bug 由 harness 捕获并修复**：`_prevalidate_candidate` 的 polarity 检查旧版按目标模块集合过滤（`ERROR_TO_CLEAN_ACTIONS.get(target_module)`），初版新实现误用全局极性池（`_polarity_pool` 返回的模块集合），导致 valid 候选被误报 `action_polarity_mismatch`；已改为按目标模块取方向操作集合，修复后 36/36 全绿。

6. **降级路径（§3.3 R2 显式记录）。** `PACA_GLOBAL_CACHE_ROOT` 暖缓存本机仍不可用（`experiments/cache/provider` 不存在）；Phase 6 不做 provider 调用，无需暖缓存。离线契约校验（`compare_planning.py`，36 项）：L1 prompt 金样本（6 场景 × system 逐字节 + repair-plans 段落逐字节 + 共享投影键 + frontier 边 = 24 项）、L2 规划器（5 场景 × route + 候选签名 = 5 项）、候选预校验（7 项）。观察段（`<structured_runtime_observation>`）结构性差异为新包按 §Phase 6 明示的投影面升级（episode 投影替换 raw detection/attribution 快照）：新侧持有 skill_layer / failure_owner / repairable_surface / legal_repair_operations / observable_effect / repairability 判定，旧侧持有 action_envelope / attribution / detection；两包共享键逐值一致，差异已在 harness docstring 记录为文档化偏差（DEV 0）。凭据恢复后须补跑暖缓存回放分支。

7. **纯净扫描收敛记录。** `pristine-scan sehd_rise/planning` 初扫 10 命中（全部 law 7 信号词），已逐项改写收敛：`candidates.py` "legacy record shape" → "plain attribution dict"、"legacy full form" → "full-form candidate"；`feedback.py` "unchanged for backward compatibility" → "preserved for existing batch reports"；`l1.py` "legacy mutation output" → "single-edition mutation output"、"legacy full form" → "full form"；`l2.py` "The legacy free-form prompt/parser remain" → "The free-form prompt/parser path remains"、变量 `legacy`/`legacy_signature` → `parsed`/`parsed_signature`、"Legacy add-only responses" → "Add-only responses"。收敛后 0 命中（planning 层零信号词，低于 Phase 4/5 终态的全包 4 命中基线）。

8. **与旧包行为偏差。** 本阶段无新增 §5 风险登记项，无 C 语义变更（C6 协议定义在 Phase 4，本阶段为下游消费）。唯一行为面差异为 L1 观察段的投影面升级（第 6 条，§Phase 6 明示的迁移本身）与 `_episode_matches_failure` 的 attribution 参数视图泛化（闭词汇键兼容，无判定变化）；其余迁移（L1 prompt 逐字节 / L2 planner 逐项 / 预校验逐字）与旧包等价，harness 36 项全绿为证。

---

## Phase 7 — execution 执行层 + evaluation 评估层 ★ 语义变更 C10（证据结构）

**目标：** 修复执行、证据链、因果效应判定、适应度与晋升决策全部就位——纲领 §2.12 因果信用链的物质载体。

**迁移映射（execution）：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `bccse/action_spec.py`（896 行） | `execution/action.py` | 平移 | 可执行干预规范；对 `module_charter`/`certificate_identity` 的 import 改指 `skills.vocabulary.charters`/`detection.court.identity` |
| `evolution_runner.py` 的 `_run_detection_with_genome`（含缓存种子指纹/刷新）、`_run_mutation_call`、`_run_counted_detection`、`_record_detector_provider_usage` | `execution/replay.py` | 平移 | 带基因组检测重放与预算计数；usage 记账改指 `platform/usage.py` |
| 新增（编排逻辑来自 runner 内联执行段） | `execution/executor.py` | 新建 | 按 `RepairPlan.executor` 分派：`PROVIDER_BEHAVIOR` → 规则注入重放；`ADJUDICATION_POLICY` → `detection/policy.py` 确定性执行。这是纲领 §2.7"两层 Skill 不同执行器"的唯一分派点 |
| `evolution_runner.py` 的 `_project_execution_trace`、`_collect_rule_trace`/`_collect_injected_rule_trace`/`_collect_applied_rule_trace`/`_collect_condition_matched_rule_trace`/`_collect_condition_not_matched_rule_trace`/`_collect_candidate_rule_trace`/`_collect_rule_effects_trace`/`_collect_condition_feature_trace`（含内部 walk 函数）、`_rule_in_trace`、`_injected_applied_flags` | `execution/trace.py` | 平移 | ExecutionTrace 单一执行真源；九个轨迹收集函数整体收编 |

**迁移映射（evaluation）：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `evolution_runner.py` 的 `_expected_effect_audit`/`_enforce_expected_effect_observation`/`_classify_replay_no_effect`/`_no_effect_reason_for`/`_verify_edge_level_counterfactual`/`_attribute_intervention_fitness`/`_baseline_surviving_error_certificate_ids`/`_field_binds_output_field`/`_experience_intervention_trace`/`_cross_case_no_effect_duplicate` | `evaluation/effect.py` | 平移 | C10：边级反事实、阻塞谓词闭合、干预归因构成 CausalEffectRecord；no-effect 分类学原样保留 |
| `bccse/fitness.py`（842 行） | `evaluation/fitness.py` | 平移 | source 改善评估；import 改指 |
| `evolution_runner.py` 的 `_selected_canaries`/`_canary_preserved`/`_evict_stable_canaries`/`_fitness_gold_label` | `evaluation/canary.py` | 平移 | canary 选取/保持/驱逐 |
| `bccse/safety_gate.py`（672 行）+ `evolution_runner.py` 的 `_llm_no_conflict_gate` | `evaluation/gate.py` | 合并 | 结构门 + LLM 无冲突门 |
| `bccse/promotion_contract.py`（437 行）+ `bccse/promotion_gates.py`（72 行） | `evaluation/promotion.py` | 合并 | PromotionDecision/EvidenceGrade/ActivationState/MetricSnapshot/ReplayEvidence 统一决策模型（维持"单一晋升契约"已有成果） |

**验证：** 因果效应判定的夹具比对（同一对 baseline/replay 检测结果 → 新旧边迁移与阻塞闭合判定一致）；结构门对同一组合法/非法编辑的判定与旧包一致；PromotionDecision 映射表全枚举比对；结构验证。

**提交：** `refactor(sehd_rise): phase 7 — execution evidence chain and evaluation (effect/fitness/canary/gate/promotion)`

**Phase 7 实施记录（2026-08-12，commit 后回写）：**

本阶段落地执行证据链与评估层共 9 个模块。执行层 4 模块（上一会话已落地验证未提交）+ 评估层 5 模块（本会话落地）+ 归属迁移 3 项 + Phase 6 l1 残渣修复 + C10 语义重接。`tools/parity/compare_effect.py` 24 项检查全绿、0 失败、16 偏差（全部 C10 trigger bridge DEV，引用 §2.12）；结构门判定 5 组合法/非法编辑 0 不一致；PromotionDecision 全枚举 768 组合（32 决策 × 2 权威 × 2 profile × 2 证据状态 × 3 理由集）0 不一致；`check_layers.py` 65 文件 / 13 层无违反；S1 结构消解维持（下方各层零 import execution/evaluation）；既有 parity harness（roundtrip_skills / compare_causal 173 / compare_detection 74 / compare_experience_typing 93 / compare_planning 36）无回归。

1. **C10 语义重接（§2.12 因果信用链，本阶段唯一语义变更）。** 旧 `AttributionSignal.should_evolve` 已被 Phase 4 C1/C3 删除（吸收进 `repairability.analyze` + `DetectionSignal`）。effect/fitness/canary 的信号参数全部重接 `DetectionSignal`（`causal.signals.project_detection` 产出）。`should_evolve` 的三处消费点重接方式：(a) `observable_process_delta` 的 `trigger:closed/opened` token —— 旧版读 `signal.should_evolve`，新版改为 `before_trigger`/`after_trigger` 参数（repairability verdict），由调用方（loop）线程传入；(b) `assess_source_fitness` 的 trigger 闭合/开启判定 —— 同样改读 `before_trigger`/`after_trigger`；(c) `_canary_preserved` 的稳定性判定 —— 旧版调 `attribute_failure(payload).should_evolve`，新版调 `project_detection` → `build_causal_cut` → `build_causal_episode` → `repairability.analyze`，trigger = `status == REPAIRABLE`。**`_attribute_intervention_fitness` 不读 `should_evolve`**（它纯从 postcondition + edge counterfactual 判信用），逐字平移。**`CausalEffectRecord`** 新定义（边级反事实 `_verify_edge_level_counterfactual` + 阻塞谓词闭合 + 干预归因 `_attribute_intervention_fitness` 三证据承载为单一 record），由 `build_causal_effect_record` 组装。no-effect 分类学（`_classify_replay_no_effect`/`_no_effect_reason_for`）原样保留。C10 偏差在 `compare_effect.py` 记 16 DEV（trigger bridge：旧 over-trigger 在 C4 framework-defect / NON_EVOLVABLE 场景被新包正确降级为 non-trigger —— 即 §2.12 的语义修正本身）。

2. **评估层组成与接口重接。** `evaluation/fitness.py` ← `bccse/fitness.py`（842 行）平移：`FitnessAssessment`/`observable_process_delta`/`module_local_score`/`task_state_score`/`_coverage_rank`/`_valid_claim_fingerprints`/`_prosecution_strength_score`/`_localization_quality` 等；signal 参数重接 `DetectionSignal`（`signal.crs.complete` 替 `signal.crs_complete`；`signal.prosecution_coverage.score/.state` 替 `signal.prosecution_coverage`/`_state`；`signal.diagnostics["localization_status"]` 替 `signal.localization_status`；`_gold_step_correct` 从 `signal.gold_steps`+`responsible_step` 重算替 `signal.responsible_step_correct`）；旧 `replace(signal, state=resolve_evolution_state(result))` 内部重解析删除（signal 即单一投影，state 已匹配）。`evaluation/effect.py` ← runner 10 函数平移 + `CausalEffectRecord` 新定义 + `build_causal_effect_record` 组装器。`evaluation/canary.py` ← runner `_selected_canaries`/`_canary_preserved`/`_evict_stable_canaries`/`_fitness_gold_label`；`_canary_preserved` C10 重接（见第 1 条）；`fitness_gold_label` 消费已迁 `platform.gold._evaluator_gold`。`evaluation/gate.py` ← `bccse/safety_gate.py`（672）+ runner `_llm_no_conflict_gate` 合并；`StructuralGate`/`GateCertificate`/`GateConfig`/`create_structural_gate` 原样；`llm_no_conflict_gate` 的 provider transport 经 `execution.replay._run_conflict_gate_call` 路由（evaluation 不可 import detection）。`evaluation/promotion.py` ← `bccse/promotion_contract.py`（437）+ `bccse/promotion_gates.py`（72）合并；`PromotionDecision`/`PromotionEvidence`/`promote`/`MetricSnapshot`/`ReplayEvidence` 原样；schema 字段 `legacy_decision` → `gate_decision` 重命名（pristine law 7：pre-launch 无 installed base，schema 即代码所言；parity harness 同步更新 kw）。

3. **归属迁移（落地裁决：计划文档 Phase 9 loop/records.py 列 `_detector_record`/`_evaluator_gold`/`_authority_failure_details`，但 execution.replay / evaluation.canary 需要它们且 execution 禁 import loop——迁到下层单一真源）。** `_detector_record` → `detection/detector.py`（检测防火墙本就是 detection 关切；消费 `detection.packet.EVALUATOR_ONLY_FIELDS`）；`_evaluator_gold` → `platform/gold.py`（与 `coerce_gold_label` 同住；evaluation/canary 与 loop 均可 import）；`_authority_failure_details` → `execution/replay.py`（读检测结果诊断，execution 关切）。**计划文档 Phase 9 loop/records.py 映射表已回写**（删此三项，注其已迁下层）。

4. **task_state_score 层裁决（Phase 6 遗留）。** Phase 6 实施记录称 `planning/search._task_state_score` 将「Phase 7 引用 evaluation/fitness 权威版本」——但 LAYER_RULES 禁 planning→evaluation，不可行。裁决：planning 保留内联 7 行纯函数（与 `evaluation/fitness.task_state_score` 逐字节同，harness 覆盖防漂移）；`evaluation/fitness.task_state_score` 为权威版本，docstring 注明 planning 内联副本的存在与层规则原因。本会话已落地此裁决（fitness.py docstring 已注）。

5. **execution 层 4 模块（上一会话已落地，本会话复验未回归）。** `execution/action.py`（D1 后置条件求值）+ `execution/trace.py`（9 轨迹收集）+ `execution/replay.py`（计数重放 + mutation call + 本会话新增 `_run_conflict_gate_call`）+ `execution/executor.py`（§2.7 单一分派点 `execute_repair_plan`）。本会话为 gate.py 的 LLM 无冲突门新增 `_run_conflict_gate_call`（execution 是 evaluation 的 provider transport 边界，evaluation 不可 import detection）。

6. **Phase 6 审计修复（残渣）。** `planning/l1.py` 的 `build_runtime_observation` 死别名（旧 reflection_engine 同名 wrapper 的镜像，新包无调用方）已删函数 + `__all__` 条目。

7. **降级路径（§3.3 R2 显式记录）。** `PACA_GLOBAL_CACHE_ROOT` 暖缓存本机仍不可用（`experiments/cache/provider` 不存在）；Phase 7 effect/fitness/canary 检查在确定性合成行上运行，不调用 provider（gate 的 LLM 无冲突门在无凭据时 skip_no_live_route）。`compare_effect.py` 顶部 DEGRADED 横幅 + 提交信息双重显式记录降级。凭据恢复后须补跑暖缓存回放分支（provider 级 byte-identity replay）。

8. **纯净扫描收敛记录。** `pristine-scan sehd_rise` 初扫 15 命中（本阶段新增 11，既有 4）。逐项改写收敛：`promotion.py` 字段 `legacy_decision` → `gate_decision`、注释 "Legacy decision classes" → "Closed decision-class catalogue"、"legacy gate outcome" → "gate outcome"；`effect.py` 变量 `legacy_status` → `free_text_status`、"legacy edits" → "pre-contract edits"、"legacy id-list fallback" → "fallback id list"、"legacy free-text path" → "free-text fallback path"、"classify as action_spec_absent" → "land in the action_spec_absent class"（scan 的 commented-code regex 误命中 `# class`ify 前缀）；`fitness.py` "legacy count fallback" → "count fallback"、"legacy court count" → "court count"、"legacy no_effect contract" → "no_effect contract"；`gate.py` "semantically dead" → "semantically inert"。收敛后 4 命中（全部既有保留：`detection/court/engine.py` 2 + `skills/policy.py` 2，Phase 7 范围外，与 Phase 6 终态基线一致）。

9. **与旧包行为偏差。** C10（§2.12）为本阶段唯一语义变更：`should_evolve` → repairability verdict 的 trigger 重接，导致旧包 over-trigger 的 C4 framework-defect / NON_EVOLVABLE 场景在新包正确降级为 non-trigger（compare_effect 16 DEV 即此）。`legacy_decision` → `gate_decision` 为 schema 字段重命名（pre-launch，parity harness 同步）。其余迁移（fitness/effect/canary/gate/promotion 逻辑）与旧包等价，harness 24 项 + 结构门 5 组 + PromotionDecision 768 组合全绿为证。

---

## Phase 8 — meta 策略层 ★ 语义变更 C7

**目标：** Meta Skill 重定义为 Cross-Case Strategy Learner（纲领 §2.8）。

**迁移映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `bccse/meta_skill.py`（303 行） | `meta/strategy.py` | **重写（收窄）** | C7：状态/提交/回滚/持久化保留，但 MetaEdit 的可操作面收窄为：search prior、operation preference、mechanism ranking、retry strategy、candidate diversity；删除对 gate profile 的硬切换能力之外一切触及 owner/cut/repairability/hard gate 的通道（现状本就 boxed-in，本阶段把"只学策略不学真相"落成类型级约束） |
| `bccse/meta_review.py`（617 行） | `meta/review.py` | 平移 + 接口重接 | 输入改为 `loop/ledger.py` 的 GateEvent 投影 + `experience/memory.py` 的已验证 RepairPlan outcome；输出仅为策略建议 |

**验证：** MetaEdit 合法性枚举校验（闭词汇外操作全部拒绝）；同一 GateEvent 序列在新旧两包产生的候选建议可比（允许因 C7 收窄产生的差异，逐条注明）；结构验证。

**提交：** `refactor(sehd_rise): phase 8 — meta skill as cross-case strategy learner`

**Phase 8 实施记录（2026-08-12，commit 后回写）：**

本阶段把 Meta 层重定义为 Cross-Case Strategy Learner（C7 / §2.8）：`meta/strategy.py` 重写收窄 `bccse/meta_skill.py`（303 行），`meta/review.py` 平移 + 接口重接 `bccse/meta_review.py`（617 行）。MetaEdit 可操作面收窄为五域闭词汇（search prior / operation preference / mechanism ranking / retry strategy / candidate diversity），owner / cut / repairability / hard-gate 通道全部类型级删除。`tools/parity/compare_meta.py` 43 项检查全绿、0 失败、6 偏差（全部 C7 DEV，引用 §2.8），其中自进化回路（证据门提交 → lineage 推进 → shadow 拒收 → 手工保留）5 项断言覆盖；`check_layers.py` 67 文件 / 13 层无违反；S1 维持（下方各层零 import meta）；既有 parity harness（roundtrip_skills / compare_causal 173 / compare_detection 74 / compare_experience_typing 93 / compare_planning 36 / compare_effect 24）无回归。

1. **C7 语义重接（§2.8 Meta 重定义，本阶段唯一语义变更）。** Meta Skill → Cross-Case Strategy Learner，只学策略不学真相。五域闭词汇落地为 `DOMAIN_TOKENS`（域 → 令牌集，每令牌恰属一域，共 22 枚存活令牌）。删除令牌（§2.8 "Meta 不负责"）：owner 三枚（`bind_action_to_owned_output` / `avoid_unowned_output_target` / `avoid_scope_or_ownership_mismatch`）、cut 二枚（`avoid_downstream_invariant_surface` / `downstream_edge_reachable`）、ranking-owner 一枚（`owned_output_match`）、hard-gate（gate profile `conservative`/`steady` + `switch_gate_profile` 操作）。旧操作集 `add_guidance_token` / `remove_guidance_token` / `enable_ranking_preference` / `disable_ranking_preference` / `switch_gate_profile` 废止，统一为 `OP_ENABLE` / `OP_DISABLE`。类型级约束：`StrategyEdit` 必须声明 `domain` + `target`，target 不属该域即拒（`target_not_in_domain`）；被删令牌无任何域可归 → 永不可进入状态。`compare_meta` 合法性枚举：5 域 enable 全过、5 个 v6 操作全拒、6 个删除令牌在 5 域全拒、2 个 gate profile 全拒、跨域 / 重复 enable / disable 缺位全拒。

2. **模块组成与接口重接。** `meta/strategy.py`：`StrategyState`（每域 `enabled` 令牌 + `generation`/`parent_hash`/`edits_applied`/`audit` + `content_hash`，schema `sehd_rise.strategy_state.v1`）保留状态 / 提交 / 回滚 / 持久化机制（`clone`/`from_dict`/`to_dict`/`apply_strategy_edit` 不可变 lineage）；`state_id` 由 `content_hash` 派生（确定性，替旧 uuid，replay 可复现）。`meta/review.py`：`GateExperience` 从旧 `meta_review` 平移为 meta 自有的 GateEvent 投影 DTO（`from_dict` 闭词汇过滤 `feedback_codes`）；GateEvent→GateExperience 投影函数（旧 `aggregate_gate_experience`）删除——GateEvent 类型在 `loop/ledger.py`（Phase 9），meta 不可 import loop，投影归 loop 单一权威。输入重接：验证 RepairPlan outcome 从 `experience/memory.MechanismEntry`（每域 representative slot：`effective`/`no_effect`/`structural_reject`/`no_proposal`）读取，替旧 `OutcomeValidation` 跟进分类（`classify_outcome` 删除）。输出重接：`project_strategy_for_prompt` 仅产 `{authority:"evolution_guidance_only", strategy:{域:[令牌]}}`，`gate_profile` 字段删除。`FUZZY_FEEDBACK_CODES` 落 meta（消费方 fail-closed 校验；`loop/ledger` Phase 9 import 同集，单一真源）。

3. **防丢失清单勾销。** (a) MetaEdit 可操作面收窄为五域闭词汇：`DOMAIN_TOKENS` 闭集 + `validate_strategy_edit` 域/令牌双校验，闭词汇外操作全拒（harness §4.3 全枚举）；(b) Meta 不再触及 owner/cut/repairability/hard-gate：删除令牌无域可归，类型级不可表达（harness 6 令牌 × 5 域全拒 + gate profile 全拒）；(c) meta_review 输入重接 GateEvent 投影（`GateExperience` 自有 DTO）+ 已验证 RepairPlan outcome（`experience/memory.MechanismEntry`），不读 raw detection/attribution（meta→experience 层规则内）；(d) meta_review 输出仅为策略建议：`generate_strategy_suggestions` 仅产 `StrategyEdit`，不产 RepairPlan/edit/owner 裁决；(e) 状态/提交/回滚/持久化保留：`StrategyState` clone/apply/lineage/content_hash 机制不收窄，只收窄可操作面；(f) 剥离逻辑在旧 god object 无残渣（旧包冻结只读，逻辑搬移非物理删除）；(g) 新包无别名/死代码：meta 两模块全为活跃公开函数，`pristine-scan sehd_rise/meta` 0 命中为证。

4. **GateEvent 序列候选建议比对（§4.4）。** 同一 GateEvent 序列喂两包：旧 `generate_meta_edit_candidates`（feedback + `OutcomeValidation`）vs 新 `generate_strategy_suggestions`（feedback + `MechanismEntry` verified outcome）。三共享令牌（`prefer_registered_runtime_route` / `avoid_pending_registry_dependency` / `action_shape_previously_effective`）触发条件下两包同产。C7 偏差（6 DEV，引用 §2.8）：证据源重接——`action_shape_previously_effective` 旧由 `exact_false_reject` `OutcomeValidation` 背书，新由 verified `effective` representative 背书（§2.8 "从多次已验证的 RepairPlan outcome 中学习"）；C7 新增——`avoid_redundant_intervention`（feedback-backed，旧 `COMMIT_EVIDENCE_SOURCES` 无映射）、`action_shape_previously_no_effect` / `reuse_effective_action_shape` / `avoid_action_shape_previously_failed`（outcome-backed，旧无 no-effect/structural_reject 证据路径）。无令牌被删后仍被旧包生成（旧三令牌均存活），故无"移除候选"偏差；删除通道的封闭性由合法性枚举（§4.3）证明。

5. **自进化回路验证（§4.6）。** meta 层是确定性层（§2.8：「Everything here is deterministic and prompt-safe」），**不做任何 provider 调用**——暖缓存可用性不影响 Phase 8 验证，离线契约校验（合法性枚举 / 候选比对 / 序列化往返 / 自进化回路）即**完整验证**而非 R2 降级。自进化回路 5 项断言：`effective x3`（3 个同型机制各带 verified effective representative）→ `action_shape_previously_effective` 过证据门（observed=3 ≥ MIN_COMMIT_EVIDENCE=3）提交、generation 推进（0→2，两条独立候选均过门）、content_hash 变、令牌启用；`effective x1` → 不足阈值 shadow 拒收（`insufficient_evidence`）且状态零变；`route x3` 同机制 feedback → 过门提交；disable → 永不自动提交（`operation_requires_manual_review`，lineage 可见的策略决策）。离线契约：`StrategyState`/`StrategyEdit`/`GateExperience` `from_dict(to_dict(x))` 往返 content_hash 与字段稳定；确定性生成——同输入两次 `generate_strategy_suggestions` 候选 `(域,令牌)` 序列与 `edit_id` 序列逐字一致；prompt projection authority 固定 `evolution_guidance_only` 且 `gate_profile` 字段缺位。

6. **纯净扫描收敛记录。** `pristine-scan sehd_rise/meta` 初扫 0 命中（`strategy.py` / `review.py` 零信号词，law 1/2/3/7 全清）。全包 `pristine-scan sehd_rise` 4 命中（全部既有保留：`detection/court/engine.py` 2 + `skills/policy.py` 2，Phase 8 范围外，与 Phase 6/7 终态基线一致）。本阶段新增代码零命中为证。

7. **与旧包行为偏差。** C7（§2.8）为本阶段唯一语义变更：Meta 可操作面收窄（owner/cut/repairability/hard-gate 通道类型级删除）+ 输入重接（`OutcomeValidation` → experience-memory verified outcome）+ 输出收窄（`gate_profile` 字段删除，仅产策略建议）。旧包 v6 接受 owner 指导令牌（`add_guidance bind_action_to_owned_output`）与 gate profile 切换（`switch_gate_profile steady`），新包结构拒绝（compare_meta v6-contrast 2 DEV）。`state_id` 由 `content_hash` 派生替旧 uuid（pre-launch 无 installed base，replay 可复现）。其余保留逻辑（状态 lineage / commit 证据门 / drift 报告）与旧包语义等价，harness 38 项全绿为证。

---

## Phase 9 — loop 统一编排层 ★ 语义变更 C2（统计）/C6（编排）/C8/C9/C10（串联）

**目标：** 拆解 10,165 行 god object，实现纲领 §7 状态机与 §2.9 故障漏斗；L1/L2 在 Canonical RepairPlan 协议上合流为一条编排路径。本阶段是新框架"减臃"的主要兑现点。

**迁移映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `evolution_runner.py` 的 `EvolutionStep`/`OnlineEvolutionResult`/`ProvisionalEdit`/`_build_evolution_result` | `loop/result.py` | 平移 | 步骤与结果记录 |
| `evolution_runner.py` 的 `_PersistenceContext`/`_persist_progress`/`load_evolution_checkpoint`/`_restore_state_from_checkpoint`/`_restore_checkpoint_state`/`_save_evolution_results`/`_last_audited_step` | `loop/persistence.py` | 平移 | 检查点/恢复/落盘约 550 行整体收编 |
| `evolution_runner.py` 的 `_attribution_for_result`/`_validate_evolution_params` | `loop/records.py` | 平移 | 案例记录准备与参数校验（`_detector_record` 已迁 `detection/detector.py`、`_evaluator_gold` 已迁 `platform/gold.py`、`_authority_failure_details` 已迁 `execution/replay.py`——见 Phase 7 实施记录归属迁移裁决） |
| `bccse/gate_feedback.py`（338 行）+ `evolution_runner.py` 的 `_project_gate_event`/`_project_delta_class`/`_build_gate_event_ledger`/`_record_gate_event`/`_record_attempt` | `loop/ledger.py` | 合并 | GateEvent 台账与全部投影（维持单一投影权威） |
| `evolution_runner.py` 的 `_shadow_commit_eligible`/`_flush_shadow_batch_impl`（约 600 行） | `loop/shadow.py` | 平移 + 接口重接 | shadow 批量提交；消费 RepairPlan outcome |
| `evolution_runner.py` 的 `PendingProbeCommit`/`_probe_case_delta`/`_probe_certification`/`_probe_certification_label`/`_run_development_probe_impl` | `loop/probes.py` | 平移 | 开发探针 |
| `evolution_runner.py` 的 `_RunState`/`_initialize_state`/`_l2_promotion_evidence` | `loop/state.py` | 平移 | 运行态聚合 |
| **新增** | `loop/machine.py` | **新建** | 纲领 §7 状态机：`DETECTED → CAUSAL_FAILURE_ANALYZED → {NON_EVOLVABLE \| FRAMEWORK_DEFECT \| REPAIRABLE} → REPAIR_PLANNED → HYPOTHESIS_SEARCH → PROPOSAL_VALIDATED → EXECUTED → TARGET_EDGE_CHANGED → BLOCKER_CLEARED → FITNESS_EVALUATED → {REJECTED \| ACCEPTED} → EXPERIENCE_LEARNED → META_UPDATED`；每次迁移携带 §2.9 四层 outcome 终态分类（C8）：Framework 层 `NON_EVOLVABLE/UNADDRESSABLE/ROUTING_FAILURE/SCHEMA_FAILURE/INFRA_FAILURE/BUDGET_EXHAUSTED`，Search 层 `NO_HYPOTHESIS/INVALID_PROPOSAL/DUPLICATE_PROPOSAL`，Execution 层 `ACTION_NOT_DISPATCHED/PROVIDER_NOT_COMPLIED/TARGET_EDGE_UNCHANGED/BLOCKER_NOT_CLEARED`，Fitness 层 `SOURCE_NOT_IMPROVED/CANARY_REGRESSION/TRANSFER_REGRESSION/ACCEPTED` |
| `EvolutionLoop.run`（约 2,400 行）+ `_run_l2_policy_case_impl`（约 700 行）+ `run_online_evolution` | `loop/runner.py` | **重写（合流）** | C6：L1/L2 两套编排合并为一条 `execute_repair_plan` 主路径——案例队列 → 检测 → `causal.repairability` 分流 → RepairPlan → planning → execution → evaluation → ledger → experience → meta。C2：`UNADDRESSABLE_FRAMEWORK_DEFECT` 独立计数，不进入 no-edit/收敛统计。C9：收敛窗口只消费 complete repair attempts（plan 合法→提案合法→已执行→已测效应→无可接受改善）；budget exhausted / infra failure / provider failure 等不再是收敛证据 |

**skip 词汇废止对照（C8 落地核对表）：** `skip_stable`→`NON_EVOLVABLE`；`skip_unaddressable_failure`→`UNADDRESSABLE`；`skip_runtime_invalid`→`INFRA_FAILURE`；`skip_mutation_budget_exhausted`→`BUDGET_EXHAUSTED`；`skip_invalid_mutation`→`INVALID_PROPOSAL`；`skip_no_policy` 按原因细分（无路由→`INFRA_FAILURE`，无候选→`NO_HYPOTHESIS`，提案非法→`INVALID_PROPOSAL`，运行时前置不满足→`ROUTING_FAILURE`）；`skip_no_edit` 按阶段细分为 Search/Execution/Fitness 层对应终态。旧词汇在新包中不存在。

**验证：**

1. 状态机单测级校验：全部合法迁移路径可达、非法迁移拒绝、每个 outcome 终态有独立计数。
2. 端到端小样本：固定夹具案例流（含人工构造的 unaddressable / provider 故障 / 预算耗尽场景各一），断言：C2 unaddressable 不进 no-edit；C9 非学习事件不进收敛计数；C10 被接受编辑具备完整信用链（dispatch→edge moved→blocker cleared→terminal improved→canary preserved）逐环证据；C8 结果 JSON 按四层漏斗统计。
3. 检查点恢复等价：同一运行在中断点恢复后与连续运行的队列位置/预算/台账一致。
4. 结构验证 + 全包冒烟。

**提交：** `refactor(sehd_rise): phase 9 — unified loop orchestration on RepairPlan, state machine and failure funnel`

**Phase 9a 实施记录（2026-08-12，commit 后回写）：**

本阶段落地 loop 统一编排层前半（§2.1 拆分：9a = 7 平移模块 + machine.py 新建 + 状态机单测 + 提交；9b = `runner.py` 重写合流 / e2e 小样本 / 检查点恢复等价 / Phase 10 卡）。9a 不接 runner，故 parity / 7 harness / S1 天然不受影响。实际提交信息为 `refactor(sehd_rise): phase 9a — loop state machine, §2.9 outcome funnel and migrated records (machine/result/records/state/persistence/ledger/probes/shadow)`。新增 8 模块全部冒烟通过、`pristine-scan sehd_rise/loop` 0 命中、`check_layers` 75 文件 / 13 层无违反、状态机单测 10/10、既有 7 harness 全绿、S1 维持（下方各层零 import loop）、C8 词汇核对（`skip_` / `legacy` / `backward` 全无命中）。

1. **落地裁决（§2 各条）。** (a) C8 漏斗词汇 = `machine.Outcome` 闭集（17 枚 / 4 层）；`skip_*` 旧词在 loop 不存在。(b) `EvolutionStep.gate_decision` 承载 Outcome 值或中间标记；`_build_evolution_result` 计数唯一权威：`edits_accepted=funnel.accepted()`、`edits_rejected=SOURCE_NOT_IMPROVED+CANARY_REGRESSION+TRANSFER_REGRESSION`、`stable_cases=NON_EVOLVABLE`、`runtime_invalid_cases=INFRA_FAILURE`。(c) `l2_promotion_evidence` 的 `gold_polarity = terminal_effect and step.gate_decision == "ACCEPTED"`（源 `"accept_policy"` 已并入 ACCEPTED）。(d) GateEvent 字段 `legacy_decision` → `gate_decision`（law 7 pre-launch；`promote()` 已用 `gate_decision` 形参，重命名安全）。(e) `FUZZY_FEEDBACK_CODES` + `FEEDBACK_*` 从 `meta.review` 导入（Phase 8 单一真源，禁重定义）；`ACTIVATION_STATES`/`EVIDENCE_GRADES` 从 `evaluation.promotion` 导入。(f) `_initialize_state` 主体属 runner 上下文 → 随 9b `runner.py` 迁移；`state.py` 只收 `RunState` + `l2_promotion_evidence`。(g) `_project_execution_trace` 随 `_build_gate_event_ledger` 迁入 ledger.py（同投影族，防丢失）。(h) persistence 落盘用 `platform.jsonio.atomic_write_json`/`payload_hash`/`stable_hash`；`_detector_record`/`_evaluator_gold`/`_authority_failure_details` 已在下层单一真源（detection/platform/execution），persistence 不留副本。(i) 跨层重命名对账：`FailureEpisode`→`CausalFailureEpisode`、`MetaSkillState`/`create_baseline_meta_skill`→`StrategyState`/`create_baseline_strategy`（Phase 8）、`EvolutionExperienceMemory`→`ExperienceMemory`。

2. **pristine 驱动的收窄（law 7 / §2.10 / §4.6-7）。** (a) persistence 删除 v4→v5 迁移块与 `_CHECKPOINT_LEGACY_SCHEMA_VERSION`（pre-launch 无 installed base，schema 仅 v5，不符即拒，禁迁移层）；schema 版本单一定义为 `CHECKPOINT_SCHEMA_VERSION` 并在写/读两侧共用。(b) `_project_execution_trace` 的 `skip_runtime_invalid`（含 `skip_`，违 §4.7 C8 grep）换为 `Outcome.INFRA_FAILURE.value`（§2.4 runtime_invalid = INFRA_FAILURE），`reject_policy_replay_error` 同归 INFRA_FAILURE。(c) shadow 按 §3「换 Outcome 词」：`accept_batch`→`Outcome.ACCEPTED.value`、`rollback_batch`→`Outcome.SOURCE_NOT_IMPROVED.value`；probes `rollback_probe`→`Outcome.CANARY_REGRESSION.value`（§2.4 edits_rejected 构成）。(d) shadow 一处「old behavior」why 注释改写去信号词；`outcome_class_for`/`_structured_outcome`/`_evict_stable_canaries` + 其闭词汇常量（`OUTCOME_STAGES`/`OUTCOME_CLASSES`/`_ASSESSMENT_OUTCOME_CLASS`/`_PROMOTABLE_OUTCOME_CLASSES`）随 shadow 落地为唯一 sehd_rise 真源（src 只读不迁，新包内无重复定义）。

3. **防丢失清单勾销。** (a) GateEvent 台账与投影单一权威在 ledger.py（`_build_gate_event_ledger` 供 checkpoint 写与 finalize 读两侧共用，纯步骤函数 → resume 幂等）；(b) `FUZZY_FEEDBACK_CODES`/`GateExperience` 单一真源为 `meta.review.py`（loop import 同集）；(c) 剥离函数族在 src 只读不迁，新包内无重复定义；(d) `_initialize_state` 归属 9b runner（§2.6），9a 不迁；(e) 旧 skip 词汇 / 旧位置参数无别名残留（C8 grep 为证）。

4. **验证摘要。** 状态机单测 `python tools/parity/test_loop_machine.py` 10/10（33 迁移逐条可达 + 非法跨层跳 / 非法 outcome / 终态再迁移全拒 + funnel 每 outcome 独立计数 + `to_dict` 四层结构 + C2 unaddressable 独立键且无 `no_edit` 键 + outcome/layer 划分完备不相交）；全模块冒烟 `python -c "import sehd_rise.loop.{machine,result,records,state,persistence,ledger,probes,shadow}"` 8 个 OK；`check_layers.py` 75 文件 / 13 层无违反；既有 7 harness（roundtrip_skills / compare_causal / compare_detection / compare_experience_typing / compare_planning / compare_effect / compare_meta）全绿；S1 维持（experience/skills/causal/detection/platform/planning/typing/execution/evaluation/meta 零 `from sehd_rise.loop`）；`pristine-scan sehd_rise/loop` 0 命中；C8 词汇核对 `grep skip_` 与 `grep legacy|backward` 全无命中。

5. **R2 降级说明。** 9a 无 provider 调用、纯离线校验即完整验证（mode 照 Phase 8：确定性层离线契约校验即完整验证，非 R2 降级）。

6. **9b 前置接线表（§6.1）。** §2 关键裁决随 `SEHD_RISE_Phase9b_Prompt.md` 递推为 9b 接线表：gate_decision Outcome 词汇与 `promote()` `LEGACY_DECISIONS` 的对账（ACCEPTED 等需被 promote 识别为 accept，否则 GateEvent grade 误降 skip）、persistence/probes 残留 legacy 词汇（`halted`/`provisional`/`retry_`/`rollback_resume`/`record_attempt(decision=...)`）的 Outcome 重接、`meta_skill_state` 字段名与 `StrategyState` 对账、`causal_memory.meta_review.get("meta_state")` 键对账——均 9b runner 接线时裁决，9a 纯离线不触及。

---

## Phase 10 — api/cli 门面 + 端到端 parity 验收

**目标：** 对外门面就位，新旧框架端到端比对，文档收尾。

**迁移映射：**

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `evolution/runner.py`（381 行，`run_evolution`/记录加载/继承校验） | `api/evolution.py` | 平移 + 接口重接 | `resume_from`/`inherit_from`/记录加载语义保持 |
| `evolution/state_manifest.py` | `api/manifests.py` | 平移 | 状态清单哈希绑定 |
| `detector/__init__.py` 的 facade 职责 | `api/detection.py` | 平移 | `detect_record`/批量/副本聚合的公共出口 |
| `evolution/__main__.py` | `cli/evolve.py` | 平移 | 参数面保持一致 |
| `detector/__main__.py` + `paca_tcl/detector.py` 的 `main()` | `cli/detect.py` | 合并 | |
| 新增 | `cli/__main__.py` | 新建 | `python -m sehd_rise <evolve\|detect>` 分发 |

**端到端 parity 验收：**

1. `compare_run.py`：同一夹具案例流 + 暖缓存，分别经 `src.sehd_v6.evolution.run_evolution` 与 `sehd_rise.api.evolution.run_evolution` 运行；比对项：accepted 编辑的 target module/operation 集合、经验记忆的机制签名计数、artifact schema（`evolution_result.json`/`champion_genome.json`/`htc_tes_experience_memory.json`/`evolution_state_manifest.json` 字段齐全）。**允许且必须解释的差异**：因 C1–C10 产生的 outcome 分类与统计口径差异，逐条对照语义变更点编号注释。
2. CLI 冒烟：`python -m sehd_rise evolve --help`、`python -m sehd_rise detect` 夹具单条跑通。
3. 纯净扫描终验 + 全包冒烟 + 分层检查。
4. 更新 `README.md` 与 `docs/design/TECHNICAL_DESIGN.md` 中的框架指向（文档可改；`src/sehd_v6/` 代码仍不动）。

**提交：** `refactor(sehd_rise): phase 10 — public api/cli, end-to-end parity acceptance, docs`

---

# 5. 风险登记与注意事项

| # | 风险 | 缓解 |
|---|---|---|
| R1 | Phase 3 五代契约/引擎链合并引入判定偏差 | 暖缓存逐字段比对为硬性门槛；不通过则阶段不提交。偏差只能来自合并错误（语义本阶段零变更），发现即回退重做 |
| R2 | 暖缓存/凭据不可用导致等价验证降级 | §3.3 已定义降级路径（离线契约校验），提交信息必须显式记录降级，并在凭据恢复后补跑 |
| R3 | Phase 4/9 为重写型阶段，旧逻辑遗漏 | 每阶段附"旧逻辑去向明示/废止对照"核对表（见 Phase 4 与 Phase 9），逐行勾销后方可提交 |
| R4 | 迁移期发现旧包 bug | 冻结铁律：旧包不修，记入本表并在新包对应阶段按纲领语义实现，提交信息引用本条 |
| R5 | 会话成本：本重构横跨多个会话 | 每阶段提交即持久化点；新会话从本文档当前未完成阶段继续，无需重读全部代码 |
| R6 | outcome 口径变化导致历史实验数据不可比 | C1–C10 清单即差异解释依据；Phase 10 parity 报告逐条绑定变更编号 |
| R7 | `datasets/` 夹具代表性不足 | 夹具选取必须覆盖 error/clean/unresolved 三终态与至少一种 L2 政策触发场景，选取清单写入 `tools/parity/fixtures.py` |

**纪律重申：** 全程不分派 subagent；`src/sehd_v6/` 一字不改；每阶段一提交；除 C1–C10 外行为等价；验证降级必须显式记录。

---

# 附：阶段-提交一览

| Phase | 主题 | 语义变更 | 验证门槛 |
|---|---|---|---|
| 0 | 脚手架 + 分层检查 + parity 工具 | — | 分层检查过空骨架 |
| 1 | platform 基础设施 | — | 路由解析同输入同输出 |
| 2 | skills 数据模型与闭词汇 | — | 序列化往返相等 |
| 3 | detection 检测层（五代链合并） | — | 暖缓存逐字段比对 |
| 4 | causal 因果修复层 ★ | C1/C2/C3/C4/C6 | 失败形状场景表 + 分类比对 |
| 5 | experience + typing 顾问化 ★ | C5 | 机制索引一致 + 无环断言 |
| 6 | planning 假设搜索层 | — | prompt 金样本 + 预校验一致 |
| 7 | execution + evaluation ★ | C10（证据结构） | 效应/门/晋升判定比对 |
| 8 | meta 策略层 ★ | C7 | MetaEdit 合法性枚举 |
| 9 | loop 统一编排 ★ | C2/C6/C8/C9/C10 | 状态机 + 端到端漏斗断言 |
| 10 | api/cli + parity 验收 | — | 端到端比对 + CLI 冒烟 |
