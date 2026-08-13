# HANDOFF — L1 确定性执行重构（Phase 2）

> 本文档是跨会话交接的单一真源。新会话启动时先读本文件，再读
> `docs/design/TECHNICAL_DESIGN.md` 与 `README.md` 的「核心目标与设计纪律」。
> 完成 Phase 2 后按 pristine 法则 3 更新或删除本文件（不留残渣）。

## 0. 工作树状态（2026-08-13）

- 分支：`fix/reflection-source-wiring`
- 已提交：`70bdb57` — P0–P3 四项修复（reflection 接线 + 词表扩展 + binding 收窄 + meta 投影接线）
- 未提交（已完成、待 commit）：
  - `README.md` — 核心目标改写（见下）
  - `sehd_rise/meta/review.py` — 删除死代码 `rank_candidates`（无调用方）
- 未跟踪（实验产物，非代码）：`agenthallu_arxiv.html` 等 5 个文件，忽略即可

## 1. 目标与决策记录（用户已裁决，勿再讨论）

| # | 决策 | 状态 |
|---|---|---|
| 1 | 核心目标 = **造一个会自己越改越准的幻觉检测器，自进化效果达到 SOTA** | README 已改 |
| 2 | **不考虑 holdout 数据集** | 裁决 |
| 3 | 进化信号 = **来源案例终态朝 gold 移动**（修复当前错误即可选择性接受），不追求跨案例泛化 | 裁决 |
| 4 | L1 **生成性 opcode 全部删除**（不留），仅 9 个确定性 opcode 迁框架确定性执行 | 裁决 |
| 5 | 删除 L1 的 prose steering + `rule_effects` self-report 链 | 裁决 |
| 6 | `require_evidence_field` / `enforce_certificate_invariant` 是「court 验收」而非「框架 transform」，一并删除；`clean_defense_boundary` / `abstention_rule` 空模块一并删除（MODULE_IDS 8→6） | 已裁决 |
| 7 | FN-clean 方向删完归零：接受 **FN 暂时 NON_EVOLVABLE**（FP 是当前优先级），Phase 3 再为 FN 重设计确定性动作 | 已裁决 |

## 2. 根因（为什么做 Phase 2）

L1 与 L2 是两种执行模型，L1 走错了边：

| | 触发 | 效果 | 谁执行 |
|---|---|---|---|
| L2（正确） | 闭词表 predicate → `AdjudicationFacts` | `evaluate_policies` 确定性改 coverage/terminal | 框架 `detection/policy.py` |
| L1（错误） | `condition_spec` 门 + prose | `injection.py` 注入 `"When X, Y."` 求 provider 遵守 | **provider self-report `rule_effects`** |

**结论**：自由度低（Q2）与加载方式差（Q3）是同一根因——**执行模型是 prose steering**，
不是闭词表太小。L1 的确定性机制已存在（`skills/action.py` 的
`compile_action_spec` + `_OPERATION_POSTCONDITION`），只是没接到执行路径。

**修法（研究背书，VOYAGER/Code-as-Policies/DSPy）**：模糊触发 + 确定性效果。
skill 的 action 必须是框架能派发、能查 postcondition 的命名操作，绝不靠 LLM 读 prose 自觉遵守。

## 3. opcode 精确分类（Phase 2 的改造清单）

来源：`sehd_rise/skills/vocabulary/charters.py:312-360`（MODULE_ACTION_OPCODES）
+ `sehd_rise/skills/action.py:70-101`（_OPERATION_POSTCONDITION）。

### 3a. 保留并迁移框架（7 个确定性 opcode，2026-08-13 终态）

`require_evidence_field` / `enforce_certificate_invariant` 已被删除（它们的
postcondition 只是读 court audit 验收，从不执行 transform，属「court owns validation
truth」，不是 L1 可执行的抑制/选择动作）。连同空的 `clean_defense_boundary` /
`abstention_rule` 模块，L1 现在是 6 模块 7 opcode：

| opcode | 模块 | 确定性动作 |
|---|---|---|
| `suppress_candidate` | candidate_trigger | 从 census 删候选 |
| `keep_coverage_incomplete` | evidence_obligation | 降 prosecution coverage 状态 |
| `hold_candidate_qualification` | candidate_to_verifier_route | 候选资格保持不通过 |
| `withhold_certificate` | atomic_verifier_instruction | 删除指定证书（fingerprint 定向，非整面） |
| `reject_non_causal_origin` | causal_link_test | 从 origin 集删非因果 origin |
| `suppress_later_origin` | earliest_origin_tiebreak | 删较晚 origin |
| `select_earliest_origin` | earliest_origin_tiebreak | 排序选最早 origin |

### 3b. 删除（生成性 opcode，本质是「教冻结检测器重新生成内容」，框架无法确定性执行）

- `emit_candidate` / `emit_origin_census`（candidate_trigger）
- `populate_evidence_field` / `emit_error_certificate`（evidence_obligation）
- `route_candidate_to_verifier` / `qualify_candidate`（candidate_to_verifier_route）
- `verify_violation` / `emit_certificate`（atomic_verifier_instruction）
- `emit_defense_certificate` / `emit_boundary_finding`（clean_defense_boundary）
- `test_causal_link` / `populate_impact_chain`（causal_link_test）
- `emit_boundary_finding`（abstention_rule）

### 3c. 删除的直接后果（必须处理，不是 bug）

1. **abstention_rule / clean_defense_boundary 模块删除**（两者 opcode 全删空）。
   弃权（unresolved）修复现在只能走 L2 或检测器本身；Phase 3 若需要可为弃权重新
   设计一个确定性动作（改证据标准状态，而非生成 boundary finding 文本）。已裁决删除。
2. **CLEAN_TO_ERROR_ACTIONS 只剩 earliest_origin_tiebreak 的 select_earliest_origin**
   （仅挪 origin，不能把 clean 翻成 error）。FN（漏报）方向的可进化表达面归零：gold
   FN-clean 切点被 executable gate 降级为 `LAYER_NONE`（NON_EVOLVABLE）。已裁决接受
   「FN 暂时 NON_EVOLVABLE」，Phase 3 再为 FN 重设计确定性动作。
3. 已同步清理：`action.py` 的 `_MODULE_EFFECT` / `_OPERATION_EFFECT` /
   `_MODULE_OPERATION_EFFECT`（整表删除）/ `ERROR_TO_CLEAN_ACTIONS` / `CLEAN_TO_ERROR_ACTIONS`；
   `charters.py` 的 `MODULE_ACTION_OPCODES` / `MODULE_OPERATION_TARGETS` /
   `MODULE_OBSERVABLE_FIELDS` / `MODULE_FORBIDDEN_ACTION_TERMS` / 两个 charter；
   `contracts.py` 的 `MODULE_IDS`（8→6）/ `MODULE_STAGE` / `ALLOWED_VERDICTS`（清空）；
   `genome.py` 的 display/capacity/baseline/OBSERVABLE_FIELDS；injection/detector/gate/
   fitness/effect/typing/planning/loop 的模块表项；弃权阶梯全链路删除。

## 4. Phase 2 实施蓝图

### 4.1 前置分析（Phase 2 第一步，勿跳）

L1 的 9 个确定性 opcode 作用在检测 pipeline 的**不同阶段**的中间产物，而 L2 是
post-ledger 单一 hook。必须先做依赖链分析：

- census 候选 → 证书（verification）→ origin/localization → coverage → terminal 的**级联关系**；
- 删一个 census 候选是否级联删其证书、重算 coverage 和 terminal；
- `withhold_certificate` 删证书后，`surviving_error_certificate_ids`、coverage、terminal 如何重算。

产出：一张「确定性 opcode → 作用阶段 → 作用产物 → 级联重算」的映射表。
这张表是后续所有实现的单一真源。

**4.1 已定稿（2026-08-13）。依赖链映射表（7 个 opcode 终态）：**

| opcode | 模块 | 作用面 | 作用产物 | 级联重算 |
|---|---|---|---|---|
| `suppress_candidate` | candidate_trigger | census | 删 `candidate_id` | census → 其证书（`source_candidate_id`）从 surviving 删 → terminal+responsible |
| `withhold_certificate` | atomic_verifier_instruction | surviving | 删 1 个 `certificate_id` | surviving → terminal+responsible |
| `keep_coverage_incomplete` | evidence_obligation | prosecution_coverage | complete→incomplete | coverage → terminal（clean 不可保持） |
| `hold_candidate_qualification` | candidate_to_verifier_route | surviving | 删该候选全部证书 | surviving → terminal+responsible |
| `reject_non_causal_origin` | causal_link_test | surviving | 删该 origin 全部证书 | surviving → terminal+responsible |
| `select_earliest_origin` | earliest_origin_tiebreak | responsible_event_id | 重选最早 origin | 无（surviving 不变） |
| `suppress_later_origin` | earliest_origin_tiebreak | responsible_event_id | 重选最早 origin | 无 |

**terminal 重算规则**（与 `contract._resolve_terminal_state` 可达分支一致）：
`surviving 非空 → verified_error + 最早 surviving origin`；
`surviving 空 且 coverage_complete 且 candidate_terminal → verified_clean`；否则 `unresolved`。

**关键结构约束**：
1. `error_audits` 只与 `result["prosecution"]`（focused 初始）按 index 对齐，**不对齐**
   `combined_prosecution`（appeal 合并）。`surviving_error_certificate_ids` 可能含
   `appeal_error_*`，其审计不在 `error_audits` —— 实例解析只走初始检控面，appeal 存活
   证书的审计缺失要 fail-closed。
2. 全部 7 个 transform 单调抑制/选择，**无一个能建立 coverage_complete 或生成证书**。
   terminal 只能从 error 降到 clean（删光存活 + basis 已 clean）或 unresolved，**永远
   不能从 clean 升 error** —— 与「FN-clean 拍板 NON_EVOLVABLE」自洽。
3. 三段链 `candidate_id`(census) ↔ `source_candidate_id`(certificate) ↔ `certificate_id`
   完整，`suppress_candidate`/`hold_candidate_qualification` 依赖它级联。

**condition → target 实例解析方案（4.3.3 接线前需落地的缺口）**：

- 缺口本质：`condition_spec` 是**路由级**布尔谓词（`project_route_condition_features`
  投影 provider 输入拍平成 set），执行器需要的是**实例级** target（哪个具体
  candidate/certificate/origin）。`resolve_condition_instance` 是 result 侧平行投影。
- 特征 → 实例源映射：
  | feature | 粒度 | result 侧实例源 |
  |---|---|---|
  | `candidate_kind`/`candidate_axis`/`module_tag` | candidate | `result["census"]` → `candidate_id` |
  | `missing_field`/`failed_invariant` | certificate | `result["prosecution"].candidates` + `error_audits`（index 对齐）→ `certificate_id` |
  | `coverage_state`/`event_role`/`has_tool_*` | 无 | 只喂 field-level opcode，不解析 |
- 歧义策略（已裁决）：多实例命中**确定性取第一**（census/audit 顺序，同
  `select_earliest_origin_temporal` idiom）；零命中 fail-closed。理由：`withhold_certificate`
  语义本就是「删一个匹配证书」，取第一与现有 postcondition 语义一致。
- **已落地**（本会话）：`execution/l1.py::resolve_condition_instance` + 4 个实例级
  opcode 自动解析（无显式 `target` 时先解析，仍无则 fail-closed）。`rule.py` 的
  `_normalize_candidate_kind`/`_normalize_module_tag` 改为公开 `normalize_*` 以复用
  归一化单一真源。`tests/test_l1_executor.py` 10 测试覆盖解析/多命中/零命中/fail-closed。

### 4.2 执行模型选择（两个方向，分析后二选一）

- **方案 A（阶段内 hook）**：在每个检测阶段加确定性 hook，消费该阶段产物快照执行 transform。
  更贴近检测语义，但侵入 pipeline、工程量大。
- **方案 B（后处理层）**：检测跑完产出完整 court result，一个确定性「策略层」对结果做
  suppress/withhold/reject transform，再按依赖链重算 coverage/terminal/localization。
  同构 L2 的 `evaluate_policies`，更简单、可复用 L2 的 `AdjudicationFacts` 模式。

倾向先做**方案 B**（复用 `detection/policy.py` 的确定性执行模式 + `build_adjudication_facts`
的「从 court result 派生事实快照」模式），除非依赖链分析证明后处理会破坏语义。

### 4.3 改动清单（顺序）

1. **删除生成性 opcode**（3b）及其在 `charters.py` / `action.py` 的所有表项（3c.3）。
2. **新建 L1 确定性执行器**：消费 court result 派生的事实快照，对 9 个确定性 opcode
   执行 transform，返回 fired/skipped + postcondition 结果（同构 `evaluate_policies`）。
3. **重写 `execution/executor.py` 的 PROVIDER_BEHAVIOR 分支**：从「注入 genome 回放」
   改为「跑 baseline 检测 + 确定性执行 L1 规则」。
4. **删 prose steering**：`injection.py` 的 `make_skill_aware_provider` prose 注入、
   `rule_effects` self-report 收集、`bccse_applied_rule_ids` 等自报字段；`execution/trace.py`
   的 `_collect_rule_effects_trace` / `_collect_applied_rule_trace`；`effect.py` 的
   `provider_complied`（advisory）路径。
5. **重接 effect/fitness**：`effect.py` 的 `_attribute_intervention_fitness` 从「查 injected/applied
   trace」改为「查确定性执行器的 fired + postcondition」；`intervention_not_executed` 降级
   改为「postcondition 未满足」。
6. **abstention_rule 空模块裁决**（3c.1）：删除、保留空、还是重新设计确定性动作。

### 4.4 Phase 1（验证，可先做，不改代码）

Phase 1 = **选择性接受已实现**（`fitness.py:656-667` 的 gold task score + `shadow.py:189-190`
的 `_shadow_commit_eligible`），被 P0 的 `condition_not_matched` 挡住从未触发。Phase 2 动工前，
先重跑 `experiments/run_single_case.py` 跑 BFCL_002/005/006 + Camel_001（这 4 条是此前
`retry_fitness_no_effect` 的案例），确认 P0–P3 后链路是否走通、以及反思引擎在回放前被拒后
能否改产出可匹配规则。**这条证据决定 Phase 3（condition 语义化）是否如判断那样是下一个瓶颈。**

## 5. 关键代码锚点

| 文件 | 锚点 | 用途 |
|---|---|---|
| `sehd_rise/detection/policy.py:199-382` | `evaluate_policies` | L2 确定性执行模板 |
| `sehd_rise/detection/policy.py:51-174` | `build_adjudication_facts` | 事实快照派生模板 |
| `sehd_rise/detection/injection.py` | `make_skill_aware_provider` | 待删的 prose steering |
| `sehd_rise/skills/action.py:70-176` | postcondition + 方向表 | 待迁移/清理 |
| `sehd_rise/skills/vocabulary/charters.py:312-433` | opcode + 字段表 | 待清理 |
| `sehd_rise/execution/executor.py:39-80` | `execute_repair_plan` | 分派待重写 |
| `sehd_rise/execution/replay.py:84-170` | `_run_detection_with_genome` | 回放待改 |
| `sehd_rise/evaluation/effect.py` | `_attribute_intervention_fitness` | 归因待重接 |
| `sehd_rise/evaluation/fitness.py:612-731` | `assess_source_fitness` | 选择性接受判据 |
| `sehd_rise/loop/shadow.py:170-199` | `_shadow_commit_eligible` | 选择性接受 commit |
| `sehd_rise/planning/l1.py:1085` | `build_failure_diagnosis_prompt` | Phase 4 诊断主路径 |

## 6. 验证标准

- `python -m pytest tests/ -q` 全绿（当前 46 passed）
- `python tools/check_layers.py` 无环层纪律通过
- `node ~/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise tests` 无新增命中
- 重跑 4 条 no_effect 案例，观察「回放前拒绝」而非「回放后 no_effect」

## 7. 风险与开放问题

1. **生成性 opcode 全删后，L1 表达面大幅收窄**（只剩抑制/拒绝/验证），FN 方向尤其受限。
   这是用户裁决，但 Phase 2 完成后需用 Phase 1 的真实证据回头验证「表达力是否够 SOTA」。
2. **abstention_rule 空模块**如何影响弃权修复（3c.1）未定。
3. **后处理 vs 阶段内 hook** 二选一（4.2）未定，依赖 4.1 的依赖链分析。
