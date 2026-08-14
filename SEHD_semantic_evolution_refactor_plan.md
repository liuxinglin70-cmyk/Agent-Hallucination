# SEHD 自进化语义空间重构 · 实施清单

> v2（2026-08-14）：基于对当前代码（commit `e30840a`）的全量核查修订，替代原方案文档。
> 核心原则不变：**Open Semantic Repair, Closed Contract Execution** —— 开放修复语义的学习空间，封闭 Runtime / Evidence / Certificate / Verdict 的执行权威。
> 本文档是执行清单：每项任务给出改动文件、任务口径、验收标准与对应测试；核查结论以代码为准。

---

## 0. 核查勘误（相对原方案的修正）

原方案的诊断经代码核查约 85% 成立，以下条目已按当前代码状态修正任务口径。

| 原论断 | 核查结论 | 修正后口径 |
|---|---|---|
| §2.1 Repairability 由 CapabilityRegistry 决定 | ✅ 成立 | `causal/repairability.py:278-328`：plan 编译走 `operations_for()` 的 failure_shape+direction+target_field 精确匹配；`capabilities.py` 模块头自述"capability 仅当 runtime 已有确定性 executor 才准入"。任务不变：解除该耦合 |
| §2.2 RepairPlan 是"操作计划"，需重构结构 | ⚠️ 部分过时 | `causal/plan.py` **已携带** target_causal_edge / owner / producer_route / output_object/field / observed/required / blocking_predicate / binding schema / grounding_query。真问题仅一个：**admission 与 `legal_operations` 非空耦合**。任务改为解耦，不重构协议 |
| §2.3 ExecutableCandidate 是 opcode-centric | ⚠️ 部分过时 | commit `2991fa6` 后模型只写 applicability，框架从唯一 plan 确定性回填 target_module/action/opcode（`planning/candidates.py:151-238`）。任务改为：**operation 变为可选**（有确定性后端时自动选中），新增 mechanism / guidance 语义字段 |
| §2.4 reflection 只选 opcode | ✅ 成立 | `planning/l1.py:282-288` prompt 原文："Select repair_edge_id and an operation from legal_operations. Do not invent target_module or action strings"。任务不变 |
| §2.5 Skill 进不了 provider route | ✅ 成立 | `detection/injection.py` 首行："never injects prose into model/provider prompts"；`l1_execution.py` 是纯 post-court 确定性执行器。**修正补充**：`detection/continuation.py` 的冻结下游重放（constructive path）是 route 重入的现成存在性证明；detector 已有 9 个命名 route（`detector.py:26-36`）。实施以这两个为落点，不新建执行引擎 |
| §2.6 CAPABILITY_GAP 终止经验形成 | ⚠️ 部分过时 | GAP 分支（`runner.py:1655-1712`）**已** upsert_observation（:1586）+ 落 RepairIntent / FailureObservation / fn_family 到 step log。缺的只是：不调 `record_attempt` → ExperienceMemory 的 representative 槽为空 → 同机制检索为空。任务量从"流程重排"降为"补一条可检索 attempt" |
| §2.7 L2 vocabulary evolution 接线断裂 | ✅ 成立（已实测） | `runner.py:2021-2026` 传入的 `attribution.reason_code` 是 cut 的 REASON_* 词表（`hollow_complete_prosecution` 等），而 `evolution.py:162` 要求 `== no_registered_runtime_action` 才开 planner。实测 3 种真实 reason 全部返回 0 候选；promotion 路径（`runner.py:2053-2065`）传 route_result 是对的，但注册恒空故 promotion 恒空。测试盲区坐实：`test_vocabulary_evolution.py` 用桩 reason 验 helper，从未验证真实 runner 组合 |
| §2.8 ConditionSpec 手工封闭词表 | ✅ 成立（升级优先级） | `skills/rule.py:340-362` `_FEATURE_CONTRACT` 20 特征手工维护、算子仅 contains/equals；:388 `_DOMAIN_FEATURES` 二层手工白名单。这是**当前 REPAIRABLE 案例的第一杀手**（`condition_not_matched` / `condition_value_unregistered` / `condition_feature_not_bindable_in_domain`），P1 → **P0** |
| §2.9 ExperienceMemory 不记负经验 | ⚠️ 部分过时 | `experience/memory.py:81` 已有 no_effect / structural_reject / no_proposal 负经验槽，检索已按机制签名分层回退。任务改为：补 GAP 槽位 + 把 CandidateArchive 的 binding/search 失败并入检索面，**不重建** |
| §14 实验数字 | 已修正 | 最新基线见 §1.3 |

---

## 1. 基线与执行顺序

### 1.1 执行顺序（按确定性收益/风险重排，替代原 §19）

```
Wave 0  确定性速胜（不动架构，天级）
        T0.1 L2 registry evolution 接线修复
        T0.2 CAPABILITY_GAP 写入可检索经验
Wave 1  ConditionSpec 开放（原 P1 → P0，直接提高 REPAIRABLE 案例的 skill yield）
        T1.1 特征词表单真源化
        T1.2 值域 schema 投影 + 最小算子扩展
Wave 2  结构性重构（前置设计 D1-D3 未过门不得进入）
        D1 prose-steering 决策和解   D2 RouteAddressability 权威   D3 漂移容忍归因
        T2.1 repairability 脱钩      T2.2 RepairPlan 解耦 admission
        T2.3 候选语义化              T2.4 route 注入
        T2.5 replay 泛化             T2.6 fitness 漂移防线
Wave 3  清理与收口
        T3.1 旧闭集路径删除 + 可达性审计
```

排序理由：Wave 0/1 是小改动、确定性收益，可能直接产出第一个 ACCEPTED（当前 REPAIRABLE 案例死在 condition 词表，不是死在能力缺口）；Wave 2 解开 FN 主力（construct_certificate 家族）的结构性不可学习，但引入 provider 介导执行，必须先过三份前置设计。

### 1.2 主矛盾（为什么必须做 Wave 2）

FN 覆盖矩阵实测（`capabilities.py:fn_capability_coverage_matrix`）：**11 个 FN 家族中 7 个 uncovered**（provider_side_capability）。最新 10b 实验：4/10 案例死在 `construct_certificate` GAP——gold-FN、candidate 已存在、证据/证书获取无 primitive。AgentHallu 的 FN 多为 evidence/verifier 失败；registry-primitive 架构下这类失败**永远**不可学习。route 注入的解法不违反权威分离：Skill 不写证据，provider 在 guidance 下自己重做验证，产出仍走原 validator + 原 proof court。

### 1.3 Phase 0：锁定基线（开跑前必须完成）

保存并归档：

- [ ] `experiments/runs/evolution_10/`、`evolution_10b/`、`evolution_50/`、`validation_15/`、`funnel_census_5/` 的完整输出（含 evolution_result.json / checkpoint）
- [ ] 每例 detector output、causal cut、repairability verdict、missing capability、search/execution/fitness 计数
- [ ] 合并去重 31 例的检测基线：决定性 P 0.667 / R 0.909 / F1 0.769 / acc 0.700；全体口径（强制二分类）macro-F1 0.485；unresolved 率 22.6% 是最大短板
- [ ] 基线漏斗口径统一为 §8 指标，作为重构前后唯一对照

---

## 2. Wave 0：确定性速胜

### T0.1 L2 registry evolution 接线修复 〔P0，天级，独立于重构〕

- **改动文件**：`sehd_rise/skills/vocabulary/evolution.py`、`sehd_rise/loop/runner.py`
- **内容**：
  1. `plan_registry_candidates(attribution, registry)` 增加显式参数 `route_result: str`，guard 改为 `route_result == REGISTRATION_REASON_NO_ACTION`；**删除**对 `attribution.reason_code` 的同名要求（route 决定权属于 policy planner，词表 planner 不得从另一个命名空间重新推导开闸条件）。
  2. `runner.py` 的 `_run_registry_reflection_impl` 调用点透传 `plan.route_result`（该调用点本身已被 `route_result == ROUTE_NO_REGISTERED_RUNTIME_ACTION` 门控，`runner.py:2337`）。
  3. `apply_registry_edit(..., reason_codes=(attribution.reason_code,))` 保留因果 reason 作为审计溯源，不改。
- **验收**：
  - [ ] runner 级集成测试：真实 cut reason（如 `hollow_complete_prosecution`）→ `plan_policy_candidates` 返回 NO_ACTION route → registry reflection 产出 ≥1 candidate → pending queue 出现 registry evolution entry。**禁止**再用桩 `reason_code=NO_ACTION` 直喂 helper 的测试替代此测试。
  - [ ] 修复后 promotion 路径（`runner.py:2053-2065`）可被独立观察计数激活，`l2_registry_registrations` 出现非空 outcome。

### T0.2 CAPABILITY_GAP 写入可检索经验 〔P0，时级〕

- **改动文件**：`sehd_rise/loop/runner.py`（GAP 分支）、`sehd_rise/experience/memory.py`、`sehd_rise/loop/ledger.py`
- **内容**：
  1. GAP 分支在现有 `record_step` 之后调用 `self.record_attempt(decision="semantic_unavailable", operation="capability_gap", structural_result="capability_gap")`，把 RepairIntent/missing_capability 带入 attempt 的 proposal_content 通道。
  2. `experience/memory.py`：`_SLOT_KEYS` 增加 `semantic_unavailable` 槽；`_slot_for` 映射该 decision；`from_dict` 边界兼容旧 schema（无该槽的旧 checkpoint 载入后槽为 None，不 fail）。
  3. 检索面：`_refs_for_entry` 把 `semantic_unavailable` 作为第二负经验参考（与 no_effect 同级），使 case B 的 reflection 能检索到 case A 的同机制 GAP。
- **验收**：
  - [ ] 单测：构造 CAPABILITY_GAP episode → 同机制第二案例 `retrieve()` 返回含 `semantic_unavailable` 代表的 reference。
  - [ ] 回归：旧 checkpoint（`sehd_rise.experience_memory.v1` schema）载入不报错，行为不变。

---

## 3. Wave 1：ConditionSpec schema-derived（原 §2.8，P1 → P0）

### T1.1 特征词表单真源化

- **改动文件**：`sehd_rise/skills/binding.py`（投影器）、`sehd_rise/skills/rule.py`
- **内容**：
  1. `_DOMAIN_FEATURES`（`rule.py:388`）目前"mirrors the feature set each branch of `project_binding_universe` actually emits"——把镜像关系变成**代码依赖**：binding.py 为每个 domain 声明其投影发射的特征集合（单一真源表），rule.py 的校验、`render_condition_spec_contract` 的 prompt 渲染、`validate_condition_spec_for_domain` 全部从该表派生。
  2. 删除 rule.py 内手工维护的 `_DOMAIN_FEATURES` 字面量表。全局 `_FEATURE_CONTRACT` 同理由该表 + 各特征的值域枚举联合派生。
  3. 污染过滤保留为投影后置层：gold / case id / dataset id / opaque identity / 不稳定哈希 / 不可泛化字面 id 不进入特征集合（现有 `EVALUATOR_ONLY_FIELDS` 防火墙 + `_ACTION_POISON_RE` 同族机制）。
- **验收**：
  - [ ] 单测：在 binding.py 投影器新增一个发射特征后，无需改 rule.py，`render_condition_spec_contract` 与 `validate_condition_spec_for_domain` 自动承认该特征。
  - [ ] 不变量测试：污染字段（gold/case_id）在任何 domain 投影中不可达。

### T1.2 值域 schema 投影 + 最小算子扩展

- **改动文件**：`sehd_rise/skills/rule.py`、`sehd_rise/skills/vocabulary/contracts.py`
- **内容**：
  1. 特征值域（`_CANDIDATE_KINDS` / `_COVERAGE_STATES` / `_MODULE_TAGS` 等）从其 schema 源（census schema、court contract 枚举）派生为单一表，替代 rule.py 内分散的 frozenset 字面量。
  2. 算子新增 `not_equals`（与 contains/equals 对称的最小扩展）。数值比较算子**暂不新增**——当前无数值型特征，等投影器发射数值特征时再加（YAGNI）。
  3. 10b 实证的第一杀手是域绑定失败（模型对 coverage 域写 `candidate_kind`）：prompt 渲染已域限定，增加**每特征一行反例**（"candidate_kind is NOT bindable in coverage domain"）降低模型错误率。
- **验收**：
  - [ ] `condition_value_unregistered` / `condition_feature_unregistered` 在 Phase 1 重跑的候选拒绝中占比显著下降（对照 §1.3 基线漏斗）。
  - [ ] 回归：全部现有 condition 相关测试通过。

---

## 4. Wave 2 前置设计（门控——未通过不得进入 Wave 2）

### D1 与 2026-08-13「删 prose steering」决策的和解

该决策（L1 生成性 opcode 全删、执行模型 prose 注入 → 确定性后处理）是当时从 degeneracy 里爬出来的根因修复：确定性后处理让效果归因无需 provider 自报。Wave 2 的 route guidance 注入等于反转该决策，**必须先书面回答**为什么这次不会退化回当时删掉的样子。可辩护的差异点（逐条落测试，否则不算过门）：

- [ ] guidance 从类型化 skill 契约（condition + guidance 字段）编译产出，不是自由 prose；
- [ ] 效果归因走确定性 court 重算 + claim 级内容指纹（`court/identity.py:certificate_claim_fingerprint`），不依赖 provider 自报；
- [ ] guidance 文本过编译期防火墙（复用并扩展 `_ACTION_POISON_RE` / `FINAL_VERDICT_RE` / verdict 白名单机制到注入面）；
- [ ] 接受仍然必须经过 replay + fitness + canary（安全边界不动）。

**若以上任何一条无法满足：不实施 Wave 2**，退回课程路径（curriculum 记录缺口，人工扩确定性 opcode）。

### D2 RouteAddressability 权威设计

删除 registry 作为 learning admission 后，"存在可 replay 的 intervention point"需要新的封闭权威，否则实施时会即兴再造一个闭集：

- [ ] 新表（落在 `causal/` 层）：`failure edge → producer_route → replay_scope → binding_domain → observable_postcondition`。`FailureEdge.producer_route` 字段已存在；detector 的 9 个命名 route（`detector.py:26-36`：candidate_fallback / construct_census / qualification / propagation / boundary / focused_prosecution / defense / appeal_prosecutor / appeal_defender）是 route 侧封闭词表。
- [ ] 不变量：cut.py 的每个可进化 owner 必须映射 ≥1 route，否则才是真 CAPABILITY_GAP。
- [ ] replay_scope 定义"从目标 route 起冻结重放哪段下游路径"——`continuation.py` 的冻结下游重放（verifier → prosecution → defense → court 重算）是现成机制，D2 决定它对每个 route 的泛化边界。

### D3 漂移容忍归因设计

实验已实证同一冻结检测器两次采样终态不同（BFCL_001）。route 注入后"target edge changed"可能是 guidance 起效也可能是采样漂移，§8 全部漏斗指标会被污染：

- [ ] edge 身份比较一律用 claim 级内容指纹（`court/identity.py`，fitness 已消费），禁用位置 id；
- [ ] terminal 状态变化作为接受证据时，需决定性复核（同配置重放 ≥2 次一致，或指纹级证据变化）；
- [ ] 预算：每次语义尝试 = 全程 provider 调用；funnel census 已证并发 >2 会死在 detector 层，进化实验串行或 ≤2 并发；warm replay cache（PACA_GLOBAL_CACHE_ROOT）只对 base replay 有效，**prompt 变更使 mutation cache 失效**——每个候选的 replay 按冻活调用预算。

---

## 5. Wave 2：结构性重构

### T2.1 Repairability 脱离 CapabilityRegistry 〔P0〕

- **改动文件**：`sehd_rise/causal/repairability.py`、`sehd_rise/causal/plan.py`、`sehd_rise/loop/runner.py`
- **内容**：
  1. `analyze()` 分支 4 改为：L1/L2 切割 + RouteAddressability 命中可定位干预点 → `REPAIRABLE`（**即使无 opcode**）。registry 查询结果降级为 evidence 里的 `available_deterministic_backends` 提示字段。
  2. `CAPABILITY_GAP` 语义收紧为：D2 表中该因果边无任何可 replay 干预点（真 framework/runtime gap）。
  3. `UNADDRESSABLE_FRAMEWORK_DEFECT` 不变量保留：声明可进化但 D2 映射断裂时仍触发。
- **验收**：
  - [ ] 原 §12.2 测试：failure 有 target route、无 deterministic opcode → `REPAIRABLE` 而非 `CAPABILITY_GAP`。
  - [ ] 10b 的 4 例 construct_certificate GAP 形状在新逻辑下进入 search（最终可失败在 SEARCH/PROVIDER_NO_EFFECT，见 §8）。

### T2.2 RepairPlan 解耦 admission 〔P0〕

- **改动文件**：`sehd_rise/causal/plan.py`
- **内容**：`build_repair_plan` 不再因 `legal_operations` 为空而跳过 edge（`plan.py:310-311`）；plan 结构保持现有字段，变化仅两点：
  1. `legal_operations` 允许为空（此时 `executor` 由 RouteAddressability 给出 `route_guidance` 路径）；
  2. 新增 `available_deterministic_backends`（可空，纯优化提示）。
- **验收**：每个 actionable failure 的 plan 能回答 where to intervene / what must change / how to verify change 三问（字段已存在，验证其非空性）。

### T2.3 候选语义化 〔P0〕

- **改动文件**：`sehd_rise/planning/candidates.py`、`sehd_rise/planning/l1.py`、`sehd_rise/learning/protocol.py`
- **内容**（在现有 `ExecutableCandidate` 上改职责，不新增平行类型）：
  1. reflection 输出改为四元组：failure_mechanism（为什么在该边失败）/ applicability（label-free 条件）/ local_guidance（目标 route 应遵守的处理原则）/ expected_local_effect；框架固定 edge / owner / target route / 输出契约 / 禁写字段（现状已固定，保持）。
  2. `operation` 变为可选：唯一确定性后端存在时框架自动选中（沿用 `auto_selected_operation` 机制），不存在时候选走 route 注入路径。
  3. `_prevalidate_candidate` 的 rejection 理由中删除一切"无 opcode"类原因；opcode 相关检查仅在 `operation` 非空时执行。
- **验收**：候选拒绝不再出现"没有 opcode"类 reason；`render_capability_contracts` 无能力时不再输出 "return capability_gap"（`capabilities.py:745`）。

### T2.4 Skill 接入 existing provider routes 〔P0〕

- **改动文件**：`sehd_rise/detection/injection.py`、provider route context builder（`detection/court/engine.py` 的 route 装配处）、`sehd_rise/detection/detector.py`
- **内容**：
  1. `detection/injection.py` 职责从"deterministic rule 编译"扩展为"skill → 执行面编译"：条件命中的 skill 编译为 route-local guidance 块；组装顺序 = base system instruction + route-local detector contract + matched skills + case context。
  2. matched skill 必须满足：condition 可绑定 / target route 匹配 / 无 gold 泄漏 / 无 case 身份 / 无直接 verdict、evidence、certificate 指令（D1 防火墙的编译期执行点）。
  3. **禁止新建 provider execution engine**——重放走 T2.5。
- **验收**（原 §12.3 测试）：
  - [ ] provider request context 中包含匹配 skill，**非目标 route 不包含**；
  - [ ] 防火墙测试（原 §12.4）：注入面无法携带 "Mark this example hallucinated" / "Create a certificate" / gold 字样——编译期拒绝并有封闭 reason。

### T2.5 Replay 泛化 〔P0〕

- **改动文件**：`sehd_rise/detection/continuation.py`、`sehd_rise/execution/replay.py`
- **内容**：把 constructive path 的冻结下游重放泛化为 D2 定义的 per-route replay scope：从目标 route 起、带 guidance、重走冻结下游路径，原 validator 校验、原 court 重算 terminal。court 结果差异经 D3 指纹比较归因。
- **验收**：runner 级集成测试（原 §12.1）：raw record → detector → cut → RepairIntent → semantic search → binding → route replay → court → fitness → memory 全链可达。

### T2.6 Fitness 漂移防线 〔P0〕

- **改动文件**：`sehd_rise/evaluation/fitness.py`、`sehd_rise/evaluation/effect.py`
- **内容**：D3 的执行层落地——干预效果归因全部切换到指纹比较；terminal 变化作接受证据时的决定性复核；canary 池在 route 注入模式下的重放预算单独核算。
- **验收**：注入回归测试——同一 guidance 在采样漂移下（模拟不同 certificate 枚举顺序）归因结论稳定。

---

## 6. Wave 3：清理与收口

### T3.1 旧闭集路径删除 + 可达性审计 〔P1〕

- [ ] 删除 learning admission 的 exact capability gate 残留（T2.1 完成后 registry 只余 operation → executor contract 后端目录）
- [ ] 删除 fixed opcode planner 分支、重复 reason→operation mapper、legacy fallback
- [ ] 全仓静态审计：registered operation / reason mapper / legacy status / helper 必须有 production 调用路径；无 caller 的 enum / opcode / mapper / adapter / wrapper / 旧 fixture 全删
- [ ] `tools/check_layers.py` + `python -m pytest` + `node ~/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise` 三闸全绿
- [ ] 禁止事项（约束，全程有效）：不批量新增 construct_* primitive；不新增 detector layer；Skill 不获得 evidence/certificate/verdict authority；LLM 不直接改 Python runtime；不为旧实验保留双路径；不建 v2/semantic2 平行目录——一律改原文件

---

## 7. 测试清单（原 §12 修订汇总）

- [ ] **Runner 级集成测试**（§12.1）：raw record → detector → cut → RepairIntent → search → binding → route replay → court → fitness → memory
- [ ] **L2 接线集成测试**（T0.1 新增，原方案未列）：causal attribution → L2 policy → NO_ACTION route → registry reflection → pending registry evolution → 独立观察 promotion
- [ ] **No-opcode repairability 测试**（§12.2）
- [ ] **真实 route 注入测试**（§12.3）：目标 route 收到 / 非目标 route 不收到
- [ ] **Authority 防火墙测试**（§12.4）：skill 无法写 gold / verdict / certificate / fabricated evidence / validator output / bypass court——在**注入面**重复现有 rule 层防火墙的强度
- [ ] **Cross-case transfer 测试**（§12.5）：case A 产生 skill S → 异 dataset case B 自动触发 → route 行为改变 → 指标改善；只改善 source case 的 skill 不得 promotion
- [ ] **漂移归因稳定性测试**（T2.6 新增）：枚举顺序扰动下归因不变
- [ ] **schema 兼容测试**（T0.2）：旧 experience checkpoint 载入兼容

---

## 8. 验收指标（原 §13 修订）

漏斗不变，两点修订：所有"变化"类比较必须指纹级（D3）；每次 attempt 记录 provider 调用数与延迟（成本列）。

| 指标 | 定义 | 第一阶段目标 |
|---|---|---|
| Evolution Addressability Rate | 进入 semantic search 的 actionable failures / 全部 actionable | ≈100%（真 framework/runtime gap 除外） |
| Semantic Hypothesis Yield | 产出 ≥1 schema-valid hypothesis / 进入 search | — |
| Binding Rate | 成功绑定 / schema-valid hypotheses | — |
| Route Execution Rate | 真正进入 target route / bound attempts | — |
| Target Edge Movement | target causal edge changed（指纹比较）/ executed | — |
| Blocker Clear Rate | 原 blocker 清除 / executed | — |
| Fitness Improvement | base vs evolved：同案 / held-out case / held-out dataset | — |
| Skill Transfer Rate | 非产生案例上的有效触发率 | 只改善 source case 不得 promotion |

---

## 9. 完成自进化的额外条件（新增考虑）

重构解开的只是"结构性不可学习"。以下条件共同决定自进化能否真正闭环，逐项与框架改造同等重要：

### 9.1 Provider 能力天花板

route 注入使 7 个 uncovered FN 家族**可尝试**，不保证**能成功**——这些家族本来就是 provider-side 缺口。正确姿态不是预期能填满，而是让 §8 漏斗把 `PROVIDER_NO_EFFECT` 与 `SEARCH_FAILURE` / `BINDING_FAILURE` 分开计量：某家族持续 no-effect 即为真 runtime primitive gap 证据，进入 P2 再评估（对应原 §19 P2"再判断是否真的存在 runtime primitive gap"）。**连续 no-effect 家族清单本身就是 Wave 2 的核心产出之一。**

### 9.2 采样漂移是接受决策的一等公民

实验已实证 provider 采样漂移（同案例两次终态不同）。单案例是弱证据；接受门必须建立在决定性复核 + 指纹级归因上（D3/T2.6），否则进化会把噪声固化为 skill。进化信号 = dev 集 gold 结局奖励（当前核心目标口径）；SOTA 对比另按 AgentHallu 官方 693 例协议 + 密封 holdout 单独跑，两者不混用。

### 9.3 成本预算先行

每次语义尝试 = 目标 route 起全程 provider 冻活调用；funnel census 实证并发 >2 死于 detector 层（V4 thinking 已关、渐进超时已修，但读窗仍是硬约束）。Wave 2 实验设计必须：串行或 ≤2 并发；warm cache 只覆盖 base replay；mutation cache 对 prompt 变更失效，每个候选按冷调用预算。在 §1.3 基线上先测单 attempt 均价，再定每轮实验的 candidate 预算。

### 9.4 分层采样实验设计

两轮 20 例中 candidate-missing FN（construct_candidate 唯一覆盖的 clean→error 形状）出现 **0 次**——0-accept 有相当部分是样本构成问题，不是框架不能进化。验证重构效果必须分层取样：① 可修复形状富集组（candidate-missing FN、gold-FP）验证 ACCEPTED 通路；② 自然分布组验证漏斗比例。不做分层，0-accept 无法归因。

### 9.5 判定纪律

- Wave 0/1 落地后先重跑 10b 同 manifest：condition 类拒绝占比与 GAP 出口数量是直接对照点；
- Wave 2 每落地一个 T，跑对应 §7 测试 + 10 例冒烟，不允许跨 T 攒批验证；
- 任一环节出现 `UNADDRESSABLE_FRAMEWORK_DEFECT` 上升，视为 D2 映射不完整，先补表再继续。

---

## 10. 完成判定（原 §20 修订）

同时满足才算框架语义空间问题解决：

- [ ] actionable failure 不再因缺 opcode 在 search 前终止（T2.1/T2.2）
- [ ] Semantic Skill 能真实改变已有 detector decision route 的行为（T2.4/T2.5 注入测试）
- [ ] Skill 无法绕过 evidence / certificate / proof authority（§12.4 防火墙，含注入面）
- [ ] rejected / no-effect / capability-gap attempts 全部可检索（T0.2 + 检索面合并）
- [ ] Condition feature 不再依赖手写字面量表（T1.1 单真源）
- [ ] capability registry 仅作 deterministic backend 目录（T3.1 审计证明）
- [ ] 真实 raw-case integration test 完整到达 fitness / experience（§12.1）
- [ ] 同一 skill 在 held-out case / dataset 自动匹配、执行并改善 detector（§12.5）
- [ ] L2 vocabulary evolution 有 runner 级集成测试且非恒空（T0.1）
- [ ] D1-D3 设计文档存在、其回归测试在 CI 中、漂移归因指标稳定（T2.6）
- [ ] 旧 capability-centric learning path 已删除（非 fallback 隐藏）；仓库无无 caller 的旧 opcode / mapper / wrapper（T3.1 三闸审计）

达成前不开启大规模自进化实验；达成后按 §9.4 分层设计开跑。
