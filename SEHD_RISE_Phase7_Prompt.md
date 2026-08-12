# 阶段任务卡：SEHD-RISE Phase 7 — execution 执行层 + evaluation 评估层

> 用法：本卡由用户在新会话手动粘贴启动（手动递推，不用定时任务）。本卡 + 仓库 = 全部所需上下文；
> 上一会话的结论不在对话里，而在 git 提交与计划文档实施记录中（§3.4 R5）。本卡是
> SEHD_RISE_Refactor_Execution_Plan.md §Phase 7 的渲染，与文档冲突时以文档为准并回写文档。

## 0. 前置校验（30 秒；任一失败即停，不实施、不生成下一阶段任务卡）
- [ ] `git log --oneline -8` 确认 Phase 0–6 已提交：5b88634、50fb9d7、0f144a9、f9b0883、f34495d、
      93b3334，且 Phase 6 提交为 `refactor(sehd_rise): phase 6 — planning layer on RepairPlan protocol`
      （HEAD 为其后随卡提交或工作区干净）
- [ ] `git status` 干净（无未提交的 Phase 7 之前产物）
- [ ] `sehd_rise/execution/` 与 `sehd_rise/evaluation/` 只有空白 `__init__.py`（Phase 0 骨架，本阶段从零生长）
- [ ] 计划文档含 Phase 2/3/4/5/6 实施记录小节（证明接续链正常、记录模式可循）
- [ ] 源文件行数与计划一致：action_spec.py=896、fitness.py=842、safety_gate.py=672、
      promotion_contract.py=437、promotion_gates.py=72（另从 evolution_runner.py 剥离
      replay/effect/canary/gate/trace 函数族）
- [ ] 工作目录 = 仓库根目录；`python tools/parity/check_layers.py` 可运行且当前通过

## 1. 迁移映射（源文件只读，目标 sehd_rise/）
**execution 层：**

| 源（src/sehd_v6/core/bccse/，只读） | 目标 | 方式 |
|---|---|---|
| `bccse/action_spec.py`（896 行） | `execution/action.py` | 平移 |
| `evolution_runner.py` 的 `_run_detection_with_genome`（含缓存种子指纹/刷新）、`_run_mutation_call`、`_run_counted_detection`、`_record_detector_provider_usage` | `execution/replay.py` | 平移 |
| 新增（编排逻辑来自 runner 内联执行段） | `execution/executor.py` | 新建 |
| `evolution_runner.py` 的 `_project_execution_trace`、`_collect_rule_trace`/`_collect_injected_rule_trace`/`_collect_applied_rule_trace`/`_collect_condition_matched_rule_trace`/`_collect_condition_not_matched_rule_trace`/`_collect_candidate_rule_trace`/`_collect_rule_effects_trace`/`_collect_condition_feature_trace`（含内部 walk 函数）、`_rule_in_trace`、`_injected_applied_flags` | `execution/trace.py` | 平移 |

**evaluation 层：**

| 源（src/sehd_v6/core/bccse/，只读） | 目标 | 方式 |
|---|---|---|
| `evolution_runner.py` 的 `_expected_effect_audit`/`_enforce_expected_effect_observation`/`_classify_replay_no_effect`/`_no_effect_reason_for`/`_verify_edge_level_counterfactual`/`_attribute_intervention_fitness`/`_baseline_surviving_error_certificate_ids`/`_field_binds_output_field`/`_experience_intervention_trace`/`_cross_case_no_effect_duplicate` | `evaluation/effect.py` | 平移 |
| `bccse/fitness.py`（842 行） | `evaluation/fitness.py` | 平移 |
| `evolution_runner.py` 的 `_selected_canaries`/`_canary_preserved`/`_evict_stable_canaries`/`_fitness_gold_label` | `evaluation/canary.py` | 平移 |
| `bccse/safety_gate.py`（672 行）+ `evolution_runner.py` 的 `_llm_no_conflict_gate` | `evaluation/gate.py` | 合并 |
| `bccse/promotion_contract.py`（437 行）+ `bccse/promotion_gates.py`（72 行） | `evaluation/promotion.py` | 合并 |

- execution/action.py：可执行干预规范；对 `module_charter`/`certificate_identity` 的 import 改指
  `skills.vocabulary.charters`/`detection.court.identity`。
- execution/replay.py：带基因组检测重放与预算计数；usage 记账改指 `platform/usage.py`。
- execution/executor.py：按 `RepairPlan.executor` 分派：`PROVIDER_BEHAVIOR` → 规则注入重放；
  `ADJUDICATION_POLICY` → `detection/policy.py` 确定性执行。这是纲领 §2.7"两层 Skill 不同执行器"的唯一分派点。
- execution/trace.py：ExecutionTrace 单一执行真源；九个轨迹收集函数整体收编。
- evaluation/effect.py：C10——边级反事实、阻塞谓词闭合、干预归因构成 CausalEffectRecord；no-effect 分类学原样保留。
- evaluation/fitness.py：source 改善评估；import 改指。
- evaluation/canary.py：canary 选取/保持/驱逐。
- evaluation/gate.py：结构门 + LLM 无冲突门（合并）。
- evaluation/promotion.py：PromotionDecision/EvidenceGrade/ActivationState/MetricSnapshot/ReplayEvidence
  统一决策模型（维持"单一晋升契约"已有成果）。

## 2. 防丢失清单（逐行勾销后方可提交）
- [ ] 九个轨迹收集函数整体收编进 execution/trace.py，无函数散落旧 god object
- [ ] 因果效应判定（边级反事实/阻塞谓词闭合/干预归因）构成 CausalEffectRecord 协议（C10 证据结构）
- [ ] 结构门（safety_gate）+ LLM 无冲突门合并为单一 gate.py，判定口径与旧包逐项一致
- [ ] 晋升契约（promotion_contract + promotion_gates）合并为统一决策模型，映射表全枚举比对
- [ ] 从 evolution_runner.py 剥离的函数族不得在旧 god object 留残渣副本（pristine：不留残渣）
- [ ] 删除项不得以任何别名/死代码残留于新包

## 3. 纪律约束（不可违反）
- 不修改 src/sehd_v6/ 任何文件；不分派 subagent；全程主会话直接执行。
- 包内一律绝对导入：execution/evaluation 层
  `from sehd_rise.execution.x import ... / from sehd_rise.platform... / from sehd_rise.skills...
  / from sehd_rise.causal... / from sehd_rise.detection... / from sehd_rise.evaluation...`。
- 分层依赖：execution → platform, skills, detection, causal；evaluation → platform, skills,
  causal, execution；下方各层永不 import execution/evaluation。
- 本阶段含**语义变更 C10（证据结构）**：因果效应判定由效果记录协议（CausalEffectRecord）承载；
  凡故意偏离旧判定处必须能引用纲领条款（§2.12 因果信用链）。
- 移植即重命名：无 _old/_new、无版本号后缀、无兼容别名（pristine 上线初稿）。
- 注释只写 why；pristine-scan 信号词命中逐个收敛，保留误报必须写收敛记录（模式见 Phase 2/3/4/5/6）。

## 4. 验证（全绿才可提交）
1. 结构：`python tools/parity/check_layers.py`；全包 import 冒烟 `python -c "import sehd_rise"`
   （不只本阶段新增模块，§3.4 DoD）。
2. S1 维持证据：
   `grep -rn "from sehd_rise.execution\|from sehd_rise.evaluation" sehd_rise/experience sehd_rise/skills
   sehd_rise/causal sehd_rise/detection sehd_rise/platform sehd_rise/planning sehd_rise/typing` → 无命中。
3. 因果效应判定金样本比对：同一对 baseline/replay 检测结果 → 新旧边迁移与阻塞闭合判定一致 ——
   新建 `tools/parity/compare_effect.py`（沿用 compare_causal/compare_planning 的同进程双包夹具驱动模式）。
4. 结构门判定比对：对同一组合法/非法编辑的判定与旧包一致（compare_effect.py 内或单独 gate 比对）。
5. PromotionDecision 映射表全枚举比对（promotion_contract 各字段 → 新 promotion.py 逐项相等）。
6. 暖缓存回放不可用（§3.3 R2）→ 降级为离线契约校验（schema/序列化/确定性判定比对），
   提交信息与实施记录双重显式记录降级，不允许静默跳过。

## 5. 提交 + 回写（顺序执行）
1. git commit，格式：
   `refactor(sehd_rise): phase 7 — execution evidence chain and evaluation (effect/fitness/canary/gate/promotion)`
   正文含：迁移/新建源文件清单、旧逻辑去向勾销表、验证证据（因果效应比对 / 结构门判定一致 /
   PromotionDecision 映射表全枚举 / S1 维持 / 降级说明）、与旧包行为偏差（引用 §5 风险编号）。
2. 提交后回写计划文档「Phase 7 实施记录」小节（模式严格照抄 Phase 2/3/4/5/6 已有条目）：
   落地裁决、验证证据摘要、降级说明、纯净扫描收敛记录。回写内容并入本阶段提交
   （沿用 f34495d / Phase 5/6 做法：文档随阶段提交入库）。
3. 更新 zcode 记忆条目 sehd-rise-refactor-plan 的「下一阶段」状态行（一行索引，状态本体在仓库）。

## 6. 交接协议（用户手动递推，不用定时任务）
本卡由用户在新会话手动粘贴启动（Phase 7）。完成第 5 节后按下列步骤为 Phase 8 生成下一阶段任务卡，
由用户手动粘贴递推；Phase 10 完成后停止。

### 6.1 生成下一阶段任务卡
以 `SEHD_RISE_Refactor_Execution_Plan.md` §Phase(N+1) 为唯一内容源，套用本卡第 0–7 节
结构生成 `SEHD_RISE_Phase(N+1)_Prompt.md`（仓库根目录，随本阶段提交入库）。变更点：
- 第 0 节 commit 清单追加本阶段哈希
- 第 1/2/3/4 节按文档对应小节填充
- 第 5 节提交信息用文档原文
- 第 6 节递推到 N+2；Phase 10 改为「不生成下一阶段任务卡，第 7 节输出最终验收汇总」
- 第 7 节第 4 项改为「下一阶段任务卡文件名 + 全文已置于代码块供用户复制」

### 6.2 交付下一阶段启动 prompt（用户手动粘贴）
任务卡文件生成并提交后，**不调用 CronCreate**（用户拍板：手动递推，不用定时任务）。在最终回复（第 7 节）中：
1. 报告 `SEHD_RISE_Phase{N+1}_Prompt.md` 已生成并随本阶段提交入库；
2. 将该任务卡全文置于一个代码块内供用户复制（外层用 4 反引号包裹，使内层 3 反引号不破坏可复制性）；
3. 用户在新会话粘贴该全文启动 Phase {N+1}。

### 6.3 故障停止（不递推坏状态）
- 第 0 节前置校验失败 → 停止，不提交，报告故障项，由用户介入。
- 第 4 节验证未全绿 → 不提交，报告失败项与证据。
- 任务卡文件未生成或未提交 → 报告错误（下一会话读不到文件）。

### 6.4 终止条件
Phase 10 完成后不生成下一阶段任务卡；第 7 节输出最终验收汇总（各阶段哈希、C1–C10 语义变更落地清单、
parity 报告、纯净扫描终验），重构结束。

## 7. 最终回复格式（严格）
1. 完成声明：各验证项证据摘要 + 证据输出文件/命令原文。
2. 提交哈希 + 勾销后的防丢失清单。
3. 降级记录（若有）。
4. 下一阶段任务卡交付确认：`SEHD_RISE_Phase8_Prompt.md` 已生成并入库。
5. `SEHD_RISE_Phase8_Prompt.md` 全文（置于代码块内，供用户复制；下一会话由用户手动粘贴启动）。
