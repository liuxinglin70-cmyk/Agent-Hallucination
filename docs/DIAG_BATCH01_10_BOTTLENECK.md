# batch01-10 预算黑洞诊断与修复：cleanward 加法面饱和 + L2 反 gold 候选

> 交接物：`docs/HANDOFF_BATCH01_10_FIX.md`（P1-A..P2-E 审计修复，已实施）。
> 本文件记录**后续**深度诊断：两个把 mutation call 预算烧在无效果面上的根因（P0-1 / P1），对应修复 F1a / F3a。
> 基线：979 全绿 → 修复后 986 全绿（+7 回归测试）。

## 1. 三个问题的根因诊断

### P0-1（F1a）：`clean_defense_boundary` 在 defense-complete 上进入 allowed 集合

**现象（batch01-10 聚合）**：11 次 `clean_defense_boundary` mutation call 全部无效果——其中 9 例 defense-coverage 已 complete，模型仍生成防御侧"添加"操作，provider 不应用（`applied_rule_trace={}`）或应用后 regress。

**源码路径**：

- `_allowed_l1_modules`（`evolution_runner.py:1211-1222`）只做极性过滤：`_signal_process_modules`（`:1183-1199`）把 `target_module`/`secondary_module` 限定在 `_signal_polarity_pool`（`:1164-1180`）内。
- FP 的极性池 `_FP_POLARITY_MODULES = frozenset(ERROR_TO_CLEAN_ACTIONS)`（`:1150`）**含** `clean_defense_boundary`（`action_spec.py:120-123`）——所以 defense 已完整的 FP 上，cdb 仍进入 allowed，模型生成防御侧"添加"。
- 防御侧已完整时，cdb 再发射防御记录无法移动任何可观测面（11/11 实证），且其动作在 `ERROR_TO_CLEAN_ACTIONS` 池内合法，`_prevalidate_candidate`（`:1272-1372`）的 polarity/action_spec 检查拦不住——饱和不是"非法"，是"无移动面"。

**修复（F1a）**：`defense_coverage_state == "complete"` 时从 allowed 剔除 `clean_defense_boundary`。饱和度点收敛到 `abstention_rule`（complete+clean 时唯一还能移动判面的模块）。用 `getattr(..., None)`——coverage 字段缺失（测试 SimpleNamespace）不触发剔除，现有 4 个 `_allowed_l1_modules` 测试全部不受影响。

### P1（F3a）：L2 selection prompt gold-free，反 gold 候选在 selection 阶段不被约束

**现象（batch01-10 聚合）**：12 条 L2 reject 中 5 条 `state_change_against_gold_polarity`（`HANDOFF_BATCH01_10_FIX.md:67`）——模型在 gold=error 案例上选择推向 clean 的候选，烧掉 proposal+replay。

**源码路径**：

- `build_policy_selection_prompt`（`policy_reflection.py:272-286`）完全无 gold；reject 是 replay 后的事后检查（`_run_l2_policy_case_impl` `:6015-6039` 的 `state_change_against_gold_polarity`）。
- 反 gold 候选来自模板规划 `plan_policy_candidates`（`policy_reflection.py:112-145`）：
  - `boundary_terminal_conflict` → `consume_verified_clean_boundary`（`v6_contracts.py:225`）→ 求值器强制 `terminal_after="verified_clean"`（`policy_evaluator.py:196`）——error 案例上推进 verified_clean，反 gold；
  - `hollow_complete_prosecution` → `invalidate_coverage_claim:side=prosecution`（`policy_reflection.py:115-117`）→ 求值器把控方覆盖置 unresolved（`policy_evaluator.py:213-217`），推向 clean，反 gold。

**修复（F3a）**：selection prompt **之前**确定性过滤——新增 `_filter_policy_candidates_by_gold`（`evolution_runner.py:1397-1418`，模块级纯函数），在 `:5853` skip 块之后、`build_policy_selection_prompt`（`:5880`）之前调用，用 `_fitness_gold_label` 提前取 gold。**仅 error-gold 方向过滤**（实证唯一有浪费的方向：5/5 reject 全在 error-gold）；clean-gold 方向无浪费证据，保留在 replay fitness gate（不改 prompt、不泄 gold，L2 保持 label-free 设计）。

**关键插入点**：在 `:5853` **之后**——若插在 `plan_policy_candidates` 后（`:5818`），反 gold 模板候选本就非空，会跳过 `:5819-5853` 的空候选 skip；且 `:5825` 的 `promoted_intent_candidates` fallback 会把被过滤的候选回灌。`:5853` 后插入同时覆盖 `plan.candidates` 与 promoted 路径；过滤后空集走新增的显式 skip（`all_candidates_filtered_anti_gold`，`:5865`）。

### P0-2：不新增运行时防线

任务设想的"静态预检"已存在：`_prevalidate_candidate`（`:1280-1372`：owner/operation/polarity/action_spec）+ admission contract 校验（`:7791-7820`：observable_field ∈ `MODULE_OBSERVABLE_FIELDS` + direction/scope/trigger）。聚合 21 例 no_effect 的构成：

| 构成 | 例数 | 处置 |
|---|---|---|
| R17 已撤回的 `quarantine_certificate` 旧数据 | 10 | 不处理 |
| `clean_defense_boundary`（defense-complete 无移动面） | 5 | **F1a 覆盖** |
| L2 反 gold 候选（proposal+replay 烧掉） | 3 | **F3a 覆盖** |
| replay 层（provider 未应用/执行但无 delta，不可静态拦截） | 3 | 承认，不可静态拦截 |

`blocked_action_surfaces`（`:7298-7308`）已阻止 retry 重复。**不新增代码**——两道新防线是 F1a/F3a，不是预检。

## 2. 改动清单

### `src/sehd_v6/core/bccse/evolution_runner.py`

| 改动 | 位置 | 语义 |
|---|---|---|
| 导入 `ACTION_CONSUME_VERIFIED_CLEAN_BOUNDARY` / `ACTION_DOWNGRADE_COVERAGE` / `ACTION_INVALIDATE_COVERAGE_CLAIM` | `:112` `v6_contracts` 导入块 | F3a 常量表 |
| F1a：`_allowed_l1_modules` 剔除饱和 cdb | `:1211-1222`（原 1208-1214） | `defense_coverage_state == "complete"` 时移除 `clean_defense_boundary`；getattr 兼容无 coverage 字段的信号 |
| F3a：`_ANTI_GOLD_ACTIONS_ON_ERROR_GOLD` + `_filter_policy_candidates_by_gold` | `:1390-1418` | error-gold 下剔除 `consume_verified_clean_boundary` / `invalidate_coverage_claim:prosecution` / `downgrade_coverage:prosecution` 候选；`gold_label is not True` 原样返回 |
| F3a 调用点 + 显式 skip | `:5856-5871`（`:5865` skip） | 覆盖 `plan.candidates` + promoted fallback；空集走 `all_candidates_filtered_anti_gold` |

### `tests/test_evolution_hardening.py`（+7）

| 测试 | 断言语义 |
|---|---|
| `test_allowed_l1_modules_drops_saturated_clean_defense_boundary` | FP + `defense_coverage_state="complete"` → allowed 不含 cdb |
| `test_allowed_l1_modules_keeps_unsaturated_clean_defense_boundary` | coverage=`"incomplete"` → 保留 cdb |
| `test_allowed_l1_modules_keeps_secondary_when_coverage_unknown` | 无 coverage 字段 → 保留 cdb（getattr 兼容） |
| `test_filter_policy_candidates_by_gold_removes_clean_boundary_on_error_gold` | gold=True 剔除 `consume_verified_clean_boundary`；gold=None 原样返回（prompt 保持 label-free） |
| `test_filter_policy_candidates_by_gold_keeps_clean_boundary_on_clean_gold` | gold=False 保留 `consume_verified_clean_boundary` |
| `test_filter_policy_candidates_by_gold_removes_prosecution_weakening_on_error_gold` | gold=True 剔除 `invalidate:prosecution`；gold=False 原样（含 `side=defense` 反 clean 候选——replay gate 兜底，刻意收窄） |
| `test_prevalidate_candidate_rejects_unowned_observable_field` | P0-2 防线回归保护：`remove_rule` + 非模块拥有 field → `field_not_owned` 拒绝 |

### `docs/DIAG_BATCH01_10_BOTTLENECK.md`（本文件）

## 3. 预算节省估算（基于 batch01-10 聚合）

- **P0-1**：11 次 cdb mutation call → 9/11 不再生成（9 例 defense-complete 被 F1a 剔除，Camel__017 s1 secondary 同步剔除）。余 2 例为 coverage 非 complete 的 cdb 调用，仍可能生成——但那是真正可移动的防御面，属于合法探索。该模块预算黑洞归零。
- **P1**：5 次 `state_change_against_gold_polarity` replay → 反 gold 模板候选（error-gold 的 `consume_verified_clean_boundary` / `invalidate:prosecution`）在 selection 前被过滤，≥3/5 不再产生 proposal+replay。剩余不确定来自非模板动作/promoted intent 路径（F3a 过滤的是模板动作 + promoted fallback 经同一过滤点，覆盖面已最大）。

## 4. 验证

```
python -m pytest tests/ -x -q
986 passed in 29.28s   （979 基线 + 7 新增）
```

未触碰 R17/R18/R16 已修行为（`replay_runtime_invalid` 诊断化、确定性后置条件、`edits_accepted` 混计、quarantine 撤回）；未提交、未推送、未跑实验。
