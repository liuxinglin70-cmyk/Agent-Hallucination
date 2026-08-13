# SEHD-RISE 自进化链路审查报告与第三轮修复计划

> 状态：审查完成，结论已逐条回读代码复核；修复尚未开始。
> 基准：`8b750d3`（origin/main）。本文件是下一轮修复的唯一任务清单来源。

## 1. 审查结论（一句话）

主骨架（检测 → 因果切割 → 可修复性 → RepairPlan → 反思规划 → 预验证 → 结构门 →
注入/执行 → 源回放 → effect/fitness → shadow 批次 → champion 提交）**是通的、能产出
skill**；但四类机制被设计或接线错误切断，导致"自进化"只在很窄的配置下成立
（supervised 模式下的 FP/抑制类修复，或 label-free+canary 下的 malformed/quarantine
修复）。三层跨案例学习器（meta / typing / promotion）全部退化为 prompt 文本或遥测，
没有任何确定性控制效果。

## 2. 已复核的缺陷清单（按严重度）

每条均已在代码中核实（含生产者与消费者双侧行号）。

### P0 —— 整条自进化通路被切断

**P0-1　L2 词表进化两头断链，注册=纯遥测。**
`skills/vocabulary/evolution.py:371` 的 `enqueue_registry_validation` 全仓库零调用者；
`loop/runner.py:526-538` 在 `apply_registry_edit` 后只追加 audit 行，从不入队。因此
`promote_pending_registry_entries` 的触发条件（runner.py:2056-2063）永远为假，
`promoted_intent_candidates` 要求的 `PENDING_STATUS_VALIDATED` 永远不可达。注册条目
以 `STATUS_PENDING` 落库（`skills/vocabulary/registry.py:1022/1040`）且被所有 active
视图过滤（registry.py:129/299）——每个注册的谓词/动作都是永不生效的影子条目。
第二头：`register_action` 生成 `<operator>_instance` 别名（evolution.py:211），但
`action_names()` 按 `SKILL_CAPABILITY_REGISTRY.contains(name)` 过滤
（registry.py:317-326），能力注册表只含基线操作名，导致 `_validate_item_contract`
报 `unknown_action`（`skills/policy.py:419-420`）。从入队到使用双侧被切断。

**P0-2　默认 label-free 模式下 FN / 构造性召回完全不可达。**
`has_gold = use_gold and raw_has_gold`（`causal/signals.py:1466`）；
`REASON_GOLD_FALSE_NEGATIVE_CLEAN` 只在 `signal.has_gold` 时产生
（`causal/cut.py:616/642-684`）；`project_provider_artifacts` 的 FN+omission 分支还要求
`candidate_kind_hint == "omission"`（signals.py:1007-1011），而 runner/records 调用点
从不传该参数（`loop/records.py:41`）。结果：默认配置下 gold-error 被误判 clean 的案例
无 artifact → `no_failure_shape` → `NON_EVOLVABLE`。`construct_candidate`（构造性召回）
只在 gold 监督模式且 `candidate_count == 0` 时可达。另：`provider_artifact_missing`
不匹配任何 capability 的 `applicable_failure_shapes`（上一轮 P0-2 的"不借用"修复的
副作用），missing-artifact 案例全部落 `CAPABILITY_GAP` 只进课程——设计选择，但收窄了
label-free 可学面。

### P1 —— 某个修复家族 / 信号被静默杀死

**P1-1　localization 修复家族双重死亡（select_earliest_origin / suppress_later_origin）。**
(a) L1 变换不回写触发源：`skills/l1_execution.py:320-334` 只写
`surviving_error_certificate_ids / state / responsible_event_id`，不回写
`proof["localization_status"]`；切割器重推导 trigger 读的正是它
（`detection/policy.py:170`；`causal/cut.py:960`）→ 修复后该字段仍为
`failed_temporal_fallback` → `_triggered` 恒 True → "trigger closed" 通道永不触发。
(b) 效应契约方向词表不匹配：能力声明 `("responsible_step","shift")`
（`skills/capabilities.py:454`），但 `observable_process_delta` 产出
`responsible_step:added`（`evaluation/fitness.py:496-501`），
`effect.py:486-494` 的 `shift` 只认 `("shift","->")` → 契约恒为
`field_observed_direction_mismatch` → 回放判 no_effect → REJECT。同类错配：
`keep_coverage_incomplete` 声明 `("prosecution_coverage","opened")`，delta 产出
`prosecution_coverage:decreased`（capabilities.py:342 vs fitness.py:424-432），该 op 的
契约信用路径同样被切断（仅 trigger-closed 通道可救它）。

**P1-2　stable-canary 池被死守卫毒化，忠实编辑被永久误杀。**
`loop/shadow.py:512` 的 `not bool(_source_after.get("should_evolve"))` 恒真
（`DetectionSignal.to_dict()` 无此键，C10 已把 trigger 收进可修复性权威）→ 任何决定性
回放（含仍 REPAIRABLE 的）都被收进 canary 池；随后 `canary_preserved` 要求
`not parent_triggered and not child_triggered`（`evaluation/canary.py:110-115`）→
忠实编辑被误判 `stable_canary_regression` → 进 `rejected_buffer`（append-only，无重试/
放宽路径）。对照正确写法：runner.py:1226-1233 与 1955 两处均用 `not _triggered(...)`。

**P1-3　convergence 记账自相矛盾。**
(a) `loop/runner.py:4478-4479`：`record_no_edit()` 后立即
`revoke_structural_accept(cert, ...)`，后者把 streak 恢复为 accept 前快照 → 本 case 的
no-edit 被自我擦除；`loop/shadow.py:665-669` 是 revoke→record 的正确顺序，两条拒绝
路径语义不一致。(b) 计数按"被拒编辑"而非"case"（`evaluation/gate.py:580`），20 个
窗口在重试密集的少数 case 内即可打满，runner.py:1487 随后跳过剩余队列记 "halted"。
(c) 反向：`NO_HYPOTHESIS` / `INVALID_PROPOSAL` 终端从不调用 `record_no_edit`
（runner.py:3595 一带），模型持续 no_edit 的 run 永不收敛、烧满预算。

**P1-4　meta 五域策略没有任何确定性消费者。**
`StrategyState` 只被 `project_strategy_for_prompt` 渲染成
`<meta_guidance authority="evolution_guidance_only">` 文本
（`planning/l1.py:795-811`；runner.py:2811-2822）。`planning/search.py`（contrast 邻居
选择、极性池）、重试上限、候选生成、门控、canary 没有一处读它。上一轮接的
archive→search-policy 路由与 `meta_update_every=5` 默认值，实际效果只是"每 5 个 case
给 LLM 追加一段可被忽略的提示词"；五域改变零个确定性决策。

**P1-5　HTC-TES 类型 fitness 是纯咨询层。**
`assess_edit` 的 `global_fitness / DACR / decisive_accuracy` 只写进 batch/fitness audit
遥测（`loop/shadow.py:445-504`、runner.py:4438-4474）；`_shadow_commit_eligible`
（shadow.py:170-199）从不读它；读回路径只剩 prompt 文本（`get_reflection_summary`）。

**P1-6　appeal 后证书↔审计索引错位，绑定特征静默错误。**
`_merge_prosecutions` 重编号为 `initial_error_NNN` / `appeal_error_NNN` 并重排序
（`detection/court/engine.py:702-748`），而 `skills/binding.py:166-179` 用
`result["prosecution"]`（focused 子集）按索引 zip `error_audits`（属于 combined 集，
contract.py:1670）→ `audits.get(certificate_id)` 取到错误/空审计 → 证书/候选域的
`missing_field / failed_invariant / evidence_verified` 静默错（`evidence_verified` 还查
combined 的 `surviving_error_certificate_ids`，focused 证书恒读 False）。在这些特征上
诱导出的 L1 规则会在错误实体上接地。

**P1-7　构造性续跑第 2 个候选重复 certificate id，整案失败。**
`contract.py:987-991`（`_validate_top`）对重复 certificate id 直接 raise；
`detection/continuation.py:248-292` 不重编号 provider 返回的 `focused_error_001`
字面 id（focused 提示词的 schema 示例即该字面值，engine.py:97），并入 prosecution 后
调用 `adjudicate_proof_court` 无守卫。`max_matches_per_rule=8` 下第二个 bound anchor
必撞 id → ValueError 上抛，case 记 INFRA_FAILURE 而非跳过一个候选。

### P2 —— 误导性遥测 / 不可达分支

- **boundary 绑定域读不存在的键**：`binding.py:351` 读 `clean_boundary`，法庭发的是
  `boundary_path` / `case_boundary_finding`（engine.py:349/1178）→ 该域恒 0 绑定。
- **`positive_candidate_ids` / `clean_covered_candidate_ids` 无人产出**
  （`causal/episode.py:687/1424` 消费，court 结果不产出）→ `court_balance` 恒空，
  `_select_candidate` 静默回退 `census[0]`。
- **`evaluation/promotion.py::promote` 只是 ledger 打分器**：唯一调用是
  `loop/ledger.py:517` 事后评级；champion 晋升走独立谓词 `_shadow_commit_eligible`，
  从不咨询 `PromotionDecision`。
- **`all_candidates_quarantined` 规划模板不可达**：`planning/l2.py:146` 的 reason 没有
  任何切割器产出（对照 `causal/cut.py` 的 L2 reason 词表）。
- **`FEEDBACK_REGISTRY_ENTRY_PENDING` 无生产者**（`meta/review.py:69`）；
  结构门最重要的 `label_or_case_leakage` 拒绝类别进不了任何 feedback 码
  （`evaluation/promotion.py:219-252` 无匹配前缀）。
- **meta 提交门槛与持久化矛盾**：`MIN_COMMIT_EVIDENCE=3`（review.py:314）+ 无跨 run
  shadow 缓冲 → 每次 `_run_meta_update` 重新推导、重新丢弃同一批候选。
- **L2 step 记 L1 genome hash**（runner.py:2323-2324），步骤谱系看不到 L2 演化。
- **`causal/signals.py:1488`** `_ = _check_step_correct(...)` 结果丢弃的无效调用。
- **默认 label-free 无 canary 时，前几个全触发 case 上所有编辑被拒**：
  `_shadow_commit_eligible`（shadow.py:191-199）要求 label-free directional 带非空且
  全 preserved 的 canary_audits，而 canary 池在遇到第一个稳定 case 前为空；即便提交，
  label-free skill 也是 `fitness_status=none` 的 provisional 影子（永不 protected /
  promoted，`claim_learning=False`）。这是设计语义，不是接线错，但必须文档化口径。

## 3. 未破坏的部分（回归测试须保持）

- 触发一致性：`project_detection → build_causal_cut → build_causal_episode →
  analyze_repairability` 在主循环 / canary 守卫 / `_triggered` 三处逐字一致。
- 门控→回放→提交链：`apply_edit` → `compile_genome_rules`（5 元组）→
  `execute_l1_rules` 实体级绑定求值（与预验证同语义）→ shadow 按编辑独立提交/回滚 →
  `state.champion_genome` 更新。
- DSL 闭合性：action/condition 词表由能力注册表派生；L2 policy 的 plan→apply→
  full-set replay→commit 是真实闭环，提交项确实进入后续法庭求值。
- 标签防火墙：gold 只在 `use_gold` 显式 opt-in 后 join；detector 记录剥离 evaluator
  字段；canary 种子对全触发池 fail-fast（runner.py:1243-1249）。
- 法庭输出 schema 基本一致（census/candidate_id、surviving 列表类型、evidence_health
  键名均核对无错位）。

## 4. 修复批次（建议顺序，每批一个 commit）

每批验收 = 该批缺陷对应的新增回归测试 + 全量 pytest 全绿 + `check_layers.py` 无违规
+ 改动目录 pristine scan 0 hits。设计决策类批次不写代码，只改文档并在 commit message
里注明。

| 批次 | 内容 | 关键改动点 | 验收 |
|---|---|---|---|
| B1 | P1-7 构造续跑重复 id 打崩案 | `continuation.py` 对 provider 返回的 cert 重编号（`constructed_error_NNN`）或去重；对单个候选的续跑子调用加 try/except 记 `CONSTRUCTED_CANDIDATE_NOT_VERIFIED` 而非上抛 | 新测试：one_or_many 规则绑 2+ 锚，续跑完成或跳过，不抛异常、不废案 |
| B2 | P1-6 audit 索引错位 | `binding.py` 按 `combined_prosecution` 行与 `error_audits` 以 `certificate_id` 建索引（不再按位置 zip）；`evidence_verified` 用 combined id 查 surviving | 新测试：带 appeal 合并（initial+appeal 双集）的 result，证书域特征与 audit 逐 id 对齐 |
| B3 | P1-3 convergence 记账 | runner.py:4478 改为 revoke→record（对齐 shadow）；gate 的 no-edit 改为按 case 计（同 case 多次拒绝只计一次）；NO_HYPOTHESIS/INVALID_PROPOSAL 终端各计一次 | 新测试：单 case 多拒绝不把 streak 打满；NO_HYPOTHESIS 计入窗口；窗口满后剩余 case 记 halted |
| B4 | P1-2 canary 死守卫 | shadow.py:512 改为与 runner 两处同源的 `not _triggered(...)`（携带 use_gold/gold_label 上下文） | 新测试：决定性但仍 REPAIRABLE 的回放不入 stable_canaries 池 |
| B5 | P1-1 localization 家族 | `effect.py` direction_tokens 对齐 registry 词表（`opened↔decreased`、`shift↔added/removed`，建单一映射表防再漂移）；`l1_execution.py` 变换后回写 `proof["localization_status"]` 与 `responsible_step` 相关事实 | 新测试：select_earliest_origin 修复后 `_triggered` 变 False；契约方向 `shift`/`opened` 能被 observed |
| B6 | P0-1 L2 词表进化断链 | `_run_registry_reflection_impl` 在 apply 后调用 `enqueue_registry_validation`（携带 motivating facts + intended_edit）；`registry.action_names()` 按 registry 自身实例表过滤而非能力注册表 | 新测试：注册→入队→(模拟重复观察)→promote→promoted_intent_candidates 产出可验证的 PolicyEdit；instance 名可通过 validate |
| B7 | P1-4 meta 确定性消费者 | 给至少一个域真实效果：`search_prior` 参与 contrast 邻居选择或候选排序（保持封闭词表、不碰属主/门控）；其余域在文档里如实降级为 prompt 引导 | 新测试：提交后的 strategy token 改变 `_select_contrast_neighbor` 或候选排序的确定性输出 |
| B8 | P2 杂项 | boundary 键对齐 `boundary_path`；补产 `positive_candidate_ids/clean_covered_candidate_ids`；leakage 拒绝类别进 feedback 码；删不可达模板/无效调用；L2 step 记 policy hash | 新测试逐项对应；删死路径后全量测试不变 |
| B9 | 设计口径（只文档） | label-free FN 语义（框架不自证漏检，FN 仅 gold 模式）、默认无 canary 时前 N 案例拒绝编辑的口径、provisional skill 不宣称学习成功 | README.md + TECHNICAL_DESIGN.md 同步，无"as of"注记 |

## 5. 不修、只记录的事项

- HTC-TES（P1-5）与 `promotion.py::promote`（P2）：本轮明确它们是 advisory/记账层，
  若 B7 后仍无权威消费场景，就把架构文档里"fitness 门控"的措辞改成"遥测"。
- P0-2 的 FN 信号源：任何让 label-free 产生 FN 的新信号都涉及检测语义扩展，属于机制
  设计变更，必须在 B9 口径明确后单独立项，不在本清单内顺手实现。

## 6. 验证门（每批提交前必跑）

```bash
cd "C:\Users\liuxinglin\Desktop\agent hallucination"
$env:PYTHONPATH="$PWD"
python -m pytest tests/ -q          # 当前 124 个全绿；每批只增不减
python tools/check_layers.py        # no violations
node C:/Users/liuxinglin/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise/<改动目录>
```
