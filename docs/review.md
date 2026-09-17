# 检测器底座与自进化框架：整体机制梳理与代码 Review

Review 日期：2026-09-17。范围：`detector/`、`evolution/` 全部源码与 `docs/`。
方法：逐文件通读 + 对关键结论做最小复现（下文中标「本机复现」的条目均由我实际运行代码确认）。
离线测试基线：`python -m pytest -q` → **220 passed**（`reports/latest` 记录的 204 项已增至 220）。

> **2026-09-17 更新：第 5 节的 A/B 修复已全部实施完成，测试增至 244 项并通过全部 7 项离线校验。**
> 逐条修复与新增回归测试见 [第 8 节 修复记录](#8-修复记录2026-09-17-实施)；跳出当前循环的后续方向见
> [optimization-directions.md](optimization-directions.md)。

---

## 0. 一句话结论

架构设计是**认真且自洽**的：职责边界、fail-closed 门控、gold 隔离、哈希账本都是真的。
但实现层存在若干**高影响缺陷**，其中至少三条直接决定"结论是否可信"：
一个让真实 out-of-sample 度量**必然崩溃**，一个让**无证据也能机械定罪**，一个让**辩护投票结果依赖采样顺序**。
因此当前最优先不是继续跑第 2 轮进化，而是先修这些"会污染结论"的 bug，再谈收益。

---

## 1. 系统全景

依赖方向单向：`evolution → detector`。底座可独立运行，进化框架只能通过 `detector.interventions` 的 registry 合同影响检测。

```
                          ┌──────────────────────────────────────────────────────────┐
                          │                        evolution                          │
                          │  失败诊断 → 提案 → 四门 → 组合选择 → registry/账本/治理     │
                          └───────────────┬──────────────────────────────────────────┘
                                          │ 只经 registry 合同注入（新增 typed 谓词、
                                          │ recall 触发、guard、prompt 边界）
                                          ▼
  原始记录 ──project_record──▶ ┌───────────────────────────────────────────────────────┐
  (question/history)          │                     detector                          │
                              │                                                       │
                              │  packet 构建(事件/工具/交付锚点)                        │
                              │        │                                              │
                              │        ▼                                              │
                              │  ┌─ Outcome Judge (多票) ─┐                            │
                              │  │  阳性 ──▶ 听证/辩护     │                            │
                              │  │  阴性+分歧 ──▶ 召回网   │  ← recall_trigger 可强制    │
                              │  └──────────┬────────────┘                            │
                              │             ▼                                         │
                              │  Recall Scanner → Causal Upgrade (多票, 只提案不定罪)   │
                              │             │                                         │
                              │             ▼                                         │
                              │  每场听证: Step Tracer → Defense (多票, 辩护可翻案)     │
                              │             │                                         │
                              │             ▼                                         │
                              │  确定性 adjudication(机械 guard + 封闭状态)            │
                              └─────────────┬─────────────────────────────────────────┘
                                            ▼
                    verified_error / verified_clean / unresolved
                    × authoritative / degraded_judge_only / invalid
```

关键闭合性：**终态只在 `adjudication.adjudicate` 一处产生**，任何干预都无法直接写 `state`/`decision_authority`。这是这套设计最扎实的一点，review 中未发现反例。

---

## 2. 检测器机制设计

### 2.1 主流程（`detector/api.py` `Detector.detect` → `_detect`）

```
validate_record → build_packet   ── 输入防火墙：拒绝 gold 字段；只暴露 question/history
        │
        ├─ 编译干预计划 InterventionPlan.from_registry（哈希校验、hook/写权校验）
        │
        ├─ Judge（多票）──┬─ 阳性 ──────────────▶ 听证队列
        │                 ├─ 阴性&需召回 ───────▶ run_recall_net()
        │                 └─ 阴性&一致 ─────────▶ 直接定 verified_clean（权威）
        │
        ├─ judge.post / upgrade.pre 机械证据（内置或 typed DSL 谓词）
        │
        ├─ 若有阳性：run_hearing = Step Tracer → Defense
        │        辩护 refuted ⇒ judge_overturned ⇒ 再走召回网
        │
        ├─ recall 阳性候选 → Causal Upgrade（要求"内容错误 ∧ 结果因果 ∧ coverage=answered"）
        │        → 每个通过候选各开一场听证
        │
        ├─ guard 事实汇总 → plan.guard_actions()（冲突则回退底座并记录 policy_conflict）
        │
        └─ adjudicate(...) → proof 组装 → build_detection_output
```

### 2.2 裁决优先级（`detector/adjudication.py`）

```
1. Judge 阶段 ok? 否 ─────────────────────────▶ unresolved / invalid（唯一可产生 unresolved 的阶段）
2. convict_mechanical_violation + 有效机械证据 ─▶ verified_error / authoritative
3. 无前置阳性（judge 与 upgrade 都无） ────────▶ verified_clean（recall 未跑完则降级 degraded）
4. withhold_conviction guard（需 defense ok）─▶ verified_clean / authoritative
5. 全部听证 refuted ──────────────────────────▶ verified_clean / authoritative
     └ 除非 restore_judge_conviction 恢复定罪
6. 首个定罪听证 ──────────────────────────────▶ verified_error（substantiated_upgraded 则降级）
7. 无定罪且非全 refuted（未决）───────────────▶ verified_error / degraded_judge_only
```

三个正交维度：**终态**（error/clean/unresolved）、**权威度**（authoritative/degraded/invalid）、
**运行时有效性**（runtime_valid）。评估侧 `prediction_state(require_authoritative=True)` 只认权威终态，
其余返回 `None`，这是进化门控实际消费的信号。

### 2.3 投票与不对称性（`detector/provider.py::majority_vote`）

- 聚合规则：`None` 视为弃权；严格多数 `count*2 > cast`；平票/无多数走 `on_tie`。
- **Judge**：平票/无多数 → 阴性（`on_tie=False`）。非凡例的举证责任放在"阳性"上，方向正确。
- **Defense**：平票/无多数 → 不翻案（`substantiated` 升格为 `substantiated_upgraded` 并降级权威）。意图正确，**实现有 bug（见 5.3）**。
- **Causal Upgrade**：逐候选多数决；无 topup。
- **Step Tracer**：刻意单票，不参与投票。

设计意图是"阳性需要证据、翻案需要可证明"，这是对的。问题在于**quorum 收缩没有下限**（见 5.4）。

### 2.4 机械化 guard 与证据

- `evidence_check` 只能"追加证据与 fact"，**不能写终态**；返回的 verdict 会被强制校验是否在 `body.output_verdicts` 内。
- `convict_mechanical_violation` 要求存在一条 `action + intervention_id + evidence_intervention_id + failure_family + verdict∈predicate_conviction_verdicts[predicate]` 全部匹配的证据行。
- 机械定罪锚定在最后一条 prose 事件，且**先于**正向/辩护逻辑执行。
- 冲突处理：多个 guard 动作同时命中 → 记录 `policy_conflict` 并返回空集（回退底座）。

这套"写权分离 + 冲突回退"是有工程含量的。但 `default_verdict` 校验漏了 conviction 子集，导致"零命中即定罪"（见 5.2）。

---

## 3. 自进化机制设计

### 3.1 一轮 cycle（`evolution/engine.py` `run_cycle` → `_run_cycle`）

```
预检 ─ ledger.verify() ─ cycle_started 去重 ─ 构造 CycleJournal(contract)
        │
        ├─ 缺陷注入审计 _audit：把已知缺陷候选灌进四门，必须被拒 → 否则冻结
        │     （缓存在 reports/defect_audit_verdict.json，按 代码/config/gold/registry 指纹）
        │
        ├─ Patrol：已部署版本（H_t）在 patrol 集上跑，同时保留裸底座为参照臂
        │     （两臂对比分出 new_failures / persisting_failures / resolved_by_active；
        │      registry 为空时两臂是同一次评估。见 directions-assessment.md 第 5 节）
        │
        ├─ 失败族诊断 worksheet：机制签名聚类 + typed 谓词命中 + 共同字面量 → 可执行路由
        │
        ├─ Proposer（LLM 草拟 + 受约束模板；注入历史拒绝反馈抑制原样重提）
        │
        ├─ ▲ structural_gate   合同/去重/防案例 ID 泄漏/DSL 在失败族成员上真实命中
        ├─ ▲ harmlessness_gate  sentinel 配对：不允许新增 FN/FP/unresolved/健康回退
        ├─ ▲ behavior_gate      干预真的触发且与声明行为一致
        ├─ ▲ meets_m             gain 集净收益 ≥ 阈值，新错误=0
        │
        ├─ 组合选择：贪心装入(max_admits) → 组合臂复检 harmlessness+meets_m → 失败则 survivors=units[:1]
        │
        ├─ registry.check_limits → registry.write(预提交) → ledger("admitted"/"registry_written")
        │
        └─ cycle 报告 + ledger("cycle_completed")
        （cycle 后的 run_governance：leave-one-out 归因、淘汰、回滚 —— 不参与准入）
```

四门按 `结构 → 无害 → 行为 → 边际` 顺序，且**后门以前门通过为前提**。这是正确的漏斗式设计。

### 3.2 数据划分的角色

| 集合 | 角色 | 谁用 |
|---|---|---|
| S sentinel | 无害性保护集 | harmlessness_gate |
| G gain | 边际收益集 | meets_m（**同时是准入依据**） |
| P patrol | 巡检已部署版本，裸底座为对照 | 失败族诊断 |
| validation (mini) | 准留出效果检查 | `_evaluate_mini_effect` → **当前崩溃** |
| final holdout | 正式 read-once 终审 | `run_final`（默认关闭） |

### 3.3 持久状态

```
Ledger：append-only 哈希链 {event,payload,timestamp,prev_hash,entry_hash,event_id}
         verify() 逐条校验 prev_hash 连续性与 entry_hash 重算
Registry：active.json + 时间戳快照(prev_hash) + marginal/net 历史
          check_limits(max_width/max_text_boundary_per_hook/max_decision_guards)
          rollback_to_snapshot(cycle) 恢复一致状态
Checkpoint：checkpoints/cycleNNN/{checkpoint.json, arms/<arm>/<case>.json(row_hash)}
```

设计是完整的。但"写入 registry 后才写账本"这一顺序、以及**正式路径完全不给 resume**，是可用性硬伤（见 5.9）。

---

## 4. 做得好的地方（review 中确认成立）

- **输入防火墙有效**：`dataset.project_record` 只保留 `{question, history}`；`packet.validate_record` 递归拒绝 `EVALUATOR_ONLY_FIELDS`嵌套键；`runner` 显式丢弃 gold（`record, _gold = load_case(...)`）。gold 标签**确实**不进入 packet/prompt/provider 调用。
- **终态单一出口**：只有 `adjudicate` 能写终态，registry 合同把干预限制在"追加证据/触发/受限裁决动作"。
- **DSL 非图灵完备且无 eval/import/属性遍历**：字段与操作符是显式白名单，程序结构受限（≤16 规则/≤6 verdict/≤4 条件）。
- **fail-closed 姿态一致**：未知类型/hook/谓词、哈希不符、guard 缺排除事实、证据未声明 verdict —— 一律拒绝加载或执行。
- **机械证据与终态解耦**："机械定罪"要求 evidence 行同时匹配 predicate 与 failure_family，且 `predicates.PREDICATE_CONVICTION_VERDICTS` 只授权三种谓词的特定 verdict。
- **审计与账本**：缺陷注入审计 + 哈希链账本 + `verify-ledger` 退出码反映链有效性，方向正确。
- **配对重放的意图正确**：对改变模型调用/召回控制流的干预强制 fresh 对照，避免把缓存与重采样混为一谈。

---

## 5. Review 发现的问题（按严重度排序）

> 标注「本机复现」的条目我实际运行过；其余来自逐行代码审查，附 `file:line`。

### P0 — 会直接让结论不可信

**5.1 【本机复现】mini 留出效果评估必然崩溃，唯一的样本外读数等于没有。**
`evolution/engine.py:1418-1437`：先以 `self.gold_map` 评估 `split.validation`，之后才 `update` 该 map。
但 `gold_map` 只由 `split.pool` 构建（`engine.py:213-216`），而 mini 的 validation 与 pool **被显式要求不相交**（`splits.py:301-302`）。
所以 validation 行 gold 全为 `None` → `paired_delta`→`confusion_rows` 抛 `"paired metrics baseline gold is not boolean"`（`evolution/metrics.py:29-33`）。
本机复现：
```
pool size 30  validation size 10
overlap set()
validation cases missing from gold_map: 10 ['BFCL/012.json', 'BFCL/068.json', 'BFCL/106.json']
```
后果：文档承诺的 `python -m evolution experiment --mini` 在耗尽 provider 预算后抛异常；
`run_capability_smoke` 同样受影响。测试全绿是因为测试与 smoke 构造的 split 里 `validation ⊆ pool`，恰好绕开了真实划分。
**影响**：所有"收益"只能在 G（准入集本身）上衡量，没有可用的样本外读数。

**5.2 【本机复现】DSL 的 `default_verdict` 可以是定罪 verdict ⇒ 无任何规则命中也能机械定罪。**
`predicate_dsl.py:327-332` 只校验 `default_verdict ∈ verdicts`，未排除 `conviction_verdicts`；
`run_predicate_program` 在无规则命中时直接返回 `default_verdict`（`predicate_dsl.py:699-705`）。
本机复现：
```
VALIDATION: PASSED (no error)
RUNTIME VERDICT: violated
DETAIL: no rule matched; default verdict violated
```
该 verdict 经 `evidence_for` 进入 `convict_mechanical_violation` guard → `adjudicate` 产生真实 authoritative 定罪。
**影响**：击穿"定罪必须有证据"的前提——"证据"变成了"什么都没匹配上"。这是最危险的一条。

**5.3 【本机复现】辩护投票在"无多数"时按采样顺序任取一票，同一输入可得到相反终态。**
`defense.py:96-110` 无多数且无 `substantiated` 时返回 `("", True)`；
`defense.py:255-259` 随后 `next((b for b in ballots if b.verdict == collapsed), ballots[0])`，
而 `collapsed==""` 无任何 ballot 匹配 ⇒ **永远取 `ballots[0]`**。
本机复现：
```
['refuted','inconclusive'] -> ('', True)   ⇒ 解析为 refuted      ⇒ 全 refuted ⇒ verified_clean/authoritative
['inconclusive','refuted'] -> ('', True)   ⇒ 解析为 inconclusive ⇒ 未决      ⇒ verified_error/degraded
```
同一票集，仅顺序不同，终态相反。`adjudication._hearing_verdict` 只看 `parsed["verdict"]`，从不检查 `vote.tie`。
**影响**：定罪与否取决于采样顺序，不可复现。

**5.4 【本机复现】投票 quorum 收缩无下限，"单票"即可构成严格多数。**
`provider.py:198-218` 只对已投票计数，`majority_vote([True], on_tie=False) == (True, 1)` 是"严格多数"。
本机复现：`majority_vote([True, None, None], on_tie=False) → (True, 1)`。
Judge 有 topup 但**有上限**，放弃后仍用收缩后的 quorum；**Causal Upgrade 完全没有 topup**（`recaller.py:379-397`），
只要有一个样本存活即可升级为定罪。
**影响**：重复 contract/传输失败的案例，可能由**一张**幸存票定罪或判清白；文档宣称的"传输抖动不能改变结论"不成立。

### P1 — 稳健性与可追溯性

**5.5 registry 内容哈希是"可选"的，手改 registry 可静默加载。**
`interventions.py:684-686`、`704-706` 均为 `if persisted and persisted != ...`：
缺 `content_hash` 直接通过，随后 `api.py:658-660` 记录 `interventions_hash: ""`。
`build_intervention_registry` 总会写哈希，所以这**恰好只放过**哈希本要防的场景（手写/手改的 registry）。
与 `docs/interventions.md` "registry 具备内容哈希"的表述不符。

**5.6 judge 的 `basis` 类型未防护，可能中断整个 batch。**
`judge.py:110-112`：`basis not in JUDGE_BASIS_VALUES` 对不可哈希值（如模型返回 list）抛 `TypeError`；
`_cast_vote` 只在 `try` 内捕获 `ProviderError`，`runner.py:182` 的 `future.result()` 无 try，
⇒ 单例异常中断整批，而不是产出一条 `unresolved`。其余闭集字段（confidence/kind/verdict）都先 `str()`，`basis` 是漏网者。

**5.7 非 judge 阶段的故障被掩埋为权威终态。**
`adjudication.py:322-389` 的"全 refuted"分支返回 `verified_clean/authoritative/runtime_valid=True`，**不检查 `recall_ok`**。
⇒ recall scanner 传输失败（provider_error）时，仍得到一个"权威 clean"标签，`authority_failure_reasons` 里看不到这次故障。
对称地，judge 阳性 + defense 传输失败 → `verified_error/degraded`，而评估侧 `require_authoritative=False` 会把它当预测计入，
⇒ 基础设施故障可被统计成 true positive。只有 judge 能产生 `unresolved`，下游阶段故障既不重试也不暴露。

**5.8 `upgrade.pre` 证据钩子每个案例执行两次。**
`api.py:366-370` 无条件调用一次，`recaller.py:347-351` 在召回网触发时又以不同 context 调用一次。
⇒ `plan.evidence` 多出一行、`deployment.fired` 多出一条 `evidence_appended`，
且 `plan.facts[predicate]` 被后一次覆盖——与"append-only 忠实轨迹"的承诺冲突。

**5.9 【代码确认】中断的 cycle 无法在同一 ledger 续跑，正式路径尤为彻底。**
`engine.py:610-619` 与 `558-561` 拒绝重入已写 `cycle_started` 的轮次；
`run_full_self_evolution` 更硬：`if started - completed: raise`（`engine.py:1958-1962`），且**从不传 `resume=True`**；
`checkpoint.py:34-35` 又对无 checkpoint 的历史中断直接拒绝重建。
⇒ 这正是 `docs/progress.md` 所说"第 2 轮被中断就要整轮重来"的根因。检查点机制已具备，只是正式路径没接线。

**5.10 默认配置下"无害性"与"收益"的保护都很薄。**
- `harmlessness_min_protected_tp/tn` 默认 **1**（`gates.py:455-456`），开发 sentinel 仅 6 例 ⇒ 1 TP + 1 TN 即通过，其余 sentinel 案例即使回退也不受保护。
- G 集仅 `mini_gain_size=4`，而 `m_min_marginal_cases` 默认 1 ⇒ 4 例上 +1 即准入。
- `metrics.py:122-132` 的 `marginal_cases` 把 **unresolved→正确** 也计为收益；`new_fp` 只统计 →T，
  所以"把未决的干净案例强行判为阴性"可拿到 `marginal_cases+1`、`new_errors=0`、`unresolved_added=0`，
  而 `boundary` 极性只要求 `rate>0`（`gates.py:599-604`）——**该极性约束实际是空约束**。
  ⇒ 校准改变可伪装成错误修复。

**5.11 缺陷审计可能空过并被缓存。**
`_audit` 把"任一注入缺陷被拒"当作 `passed=True`，但候选臂在超期时 fail-closed 成 unresolved 行，
无害门会因 `new_unresolved` 拒掉——**即便门逻辑根本没被真正检验**。
该 `passed=True` 还会按不含 provider 响应的指纹持久化复用（`engine.py:242-287`）。
另有 `audit.enabled=false → passed=True`（`engine.py:240-241`）。审计只证明"两个特定缺陷被拒"，不能证明"坏候选无法通过"。

### P2 — 一般性缺陷

- **DSL 正则无超时**（`predicate_dsl.py:583-585`）：`(a+)+$` 型回溯在 4096 字符 haystack 上可近乎永不返回；
  案例 deadline 只在 `provider_call` 内检查（`provider.py:291-299`），**谓词执行不受任何预算约束**，可挂死整个 detect/线程池。
- **DSL 校验与运行不一致**：`equals/not_equals` 的 threshold 在运行时才 `int()`（`predicate_dsl.py:620`），
  但校验只覆盖比较类操作符（`189-199`）⇒ `{"operator":"equals","value":"abc"}` 通过校验、运行时 `ValueError`，同样中断 batch。
- **DSL `value=0/False` 不可表达**（`predicate_dsl.py:107` 用 `str(value or "")`）；`matches` 大小写敏感而其他操作符不敏感（`583-585` vs `561-562`）。
- **L1/L2 层分离仅是文档**：`interventions.py:15-16` 声称层写权分离，但代码无 `layer` 字段/校验，
  一个"L1 text_boundary"可改写 L2 的 `defense.system` 提示词。
- **`UNFREEZING_HOOKS` 死代码**（`gates.py` 仅在 `__all__` 导出，无任何引用）——**已修复**：从 `__all__` 移除；同批删除两个无代码读取且误导的配置键 `budget.sentinel_cases` 与 `budget.marginal_cases`。
- **`adjudicate` 存在未使用参数**（`recall_status`/`upgrade_status`/`tracer_result`/`tracer_status`/`hearing_facts`），`api.py:579-590` 构造 `hearing_facts` 后从未使用。
- **`run_defense` 里 guard 检查有副作用**（`defense.py:312-320` 调 `plan.guard_actions`，会追加 `guard_fired` 与机械证据），
  与 `api.py:635` 的终态调用叠加 ⇒ `deployment.fired` 可能重复记录同一 guard。
- **配置别名共享可变嵌套默认**（`config.py:68` 浅拷贝 `_PROVIDER_DEFAULTS`）：三个路由共享同一 `context_cache` dict 对象。
- **`deepseek.json` 缺 `case_timeout_seconds`** ⇒ 整案 deadline 为 `None`（无上限），与 `autodl.json:3` 的 900 不一致。
- **`invalid_json` 被归类为传输错误**（`runner.py:54-62`）⇒ 纯模型不守格式也会触发最多 3 次整案重跑，抬高成本并引入重掷噪声。
- **`.env` 发现会向上走出仓库**（`env.py:27-28` 沿 `parents` 上溯），且 `load_local_env` 返回最远的命中文件，与"最近仓库 .env"的文档相反。
- **`ledger.append` 无 fsync 且仅进程内锁**：同一 work_root 并发多进程可交错破坏哈希链（README 只说"不要并发"，未强制）。
- **`_evaluate_mini_effect` 崩溃与 5.1 同源**，另注意 `read-once` holdout 的 `started` 标记先于评估提交（`engine.py:1687`），评估异常会永久烧掉终审集。

---

## 6. 下一步该干什么

`docs/progress.md` 的计划（跑完第 2 轮 → 拿独立数据 → 证明收益）方向没错，但**顺序要调整**：
在 5.1–5.4 修好之前，第 2 轮要么直接崩溃，要么产出的"收益"不可信，等于白烧预算。

### 阶段 A：修正确性（先于任何真实实验，1 周内）
1. **修 5.1**：`_evaluate_mini_effect` 在评估前补齐 validation 的 gold（把 `update` 提到 `evaluate` 之前），并加一条"validation ⊆ gold_map"的断言。
2. **修 5.2**：`validate_predicate_program` 增加 `default_verdict ∉ conviction_verdicts`，并在 batch 校验层复核。
3. **修 5.3**：`_vote_verdict` 无多数时返回 `inconclusive`（而非 `""` 后任取首票），并让 `adjudication` 显式读取 `vote.tie`。
4. **修 5.4**：给三个投票阶段加**最小 quorum**（如 `cast >= ceil(votes/2)`，否则 `no_quorum` 降级）；
   为 causal upgrade 补 topup 或同等保护。
5. **修 5.5/5.6**：registry 从磁盘加载时**强制**要求 `content_hash`；`basis` 先做 `isinstance(str)`。
6. 每项都补**回归测试**——这 6 条当前全无测试覆盖，正是它们能存活到今天的原因。

### 阶段 B：修有效性（在跑真实前完成，决定结论是否站得住）
7. **修 5.7**：让下游阶段故障产生 `degraded`/显式 reason，而不是被包装成 authoritative 终态；评估侧区分"基础设施未决"与"判定"。
8. **收紧门限默认值（5.10）**：无害性保护下限提到 ≥2 TP + ≥2 TN；`boundary` 极性必须绑定真实纠错（禁止 U→F 计为收益）；
   G 集样本量与 `m_min_marginal_cases` 拉开差距。
9. **修 5.11**：审计须校验"候选确被门逻辑（而非超期 fail-closed）拒绝"，且缓存指纹纳入 provider 模型版本。
10. **接上 resume（5.9）**：让 `run_full_self_evolution` 在存在有效 checkpoint 时传 `resume=True`，把"中断即整轮重来"消掉。

### 阶段 C：再做原计划（阶段 A/B 通过后）
11. 完成真实两轮进化（原计划第 2 项）——此时第 2 轮既能续跑，收益也建立在可复现、有样本外读数的基础上。
12. 获取独立评估数据并冻结协议（原计划第 3 项）；报告要带定位一致性/命中率（当前 5/6、约 1/6，是薄弱环节）。
13. 消融与成本（原计划第 4/5 项）：裸底座 / 固定干预 / 完整进化 / 去 DSL / 去 recall_trigger 的对照，报告 FP/FN、unresolved、调用成本与重复运行方差。

### 一句话优先级
**先让"测出来的东西是对的"（A），再让"测出来的东西有意义"（B），最后才去测"有没有收益"（C）。**
现在直接跳到 C，是本仓库目前最大的方法论风险。

---

## 7. 复现方式

```bash
# 离线回归（本机 220 passed）
python -m pytest -q

# 5.1 复现：mini split 的 validation 不在 gold_map 来源中
python -c "from evolution.config import load_config; from evolution.splits import build_split; \
cfg=load_config('evolution/configs/development.json'); \
sp=build_split(config=cfg, dataset_root='datasets/raw/agenthallu/AgentHallu', mini=True); \
print(len(sp.pool), len(sp.validation), set(sp.pool)&set(sp.validation))"

# 5.2 复现：default_verdict 为定罪 verdict 时仍通过校验
python -c "from detector.predicate_dsl import validate_predicate_program, run_predicate_program; \
p={'schema_version':'detector.predicate_program.v1','name':'evo_probe','description':'d', \
'verdicts':['violated','ok','not_applicable'],'conviction_verdicts':['violated'], \
'default_verdict':'violated','rules':[{'conditions':[{'field':'question_text','operator':'contains','value':'zzz'}],'verdict':'ok','detail':'x'}]}; \
validate_predicate_program(p); print(run_predicate_program(p, {'question_text':'hi','events':[]})['verdict'])"

# 5.3/5.4 复现：辩护无多数按顺序取首票；单票即严格多数
python -c "from detector.defense import _vote_verdict; from detector.provider import majority_vote; \
print(_vote_verdict(['refuted','inconclusive'], cast=2), _vote_verdict(['inconclusive','refuted'], cast=2)); \
print(majority_vote([True, None, None], on_tie=False))"
```

---

## 8. 修复记录（2026-09-17 实施）

A/B 阶段全部完成。**`python -m pytest -q` → 244 passed**（原 220），`scripts/verify.py` 七项校验全 PASS。
每项修复都配了回归测试——这些缺陷此前**零测试覆盖**，正是它们能存活至今的原因。

### A 阶段（正确性）

| # | 缺陷 | 修复 | 回归测试 |
|---|---|---|---|
| A1 | mini 留出评估用 `gold=None` 评估 → `paired_delta` 崩溃，唯一样本外读数失效 | `engine.py:_evaluate_mini_effect` 在评估**前**补齐 validation gold，并加 gold 缺失断言 | `test_mini_effect_runs_when_validation_is_disjoint_from_pool` |
| A2 | DSL `default_verdict` 可为定罪 verdict ⇒ 零命中即机械定罪 | `predicate_dsl.py` 校验 `default_verdict ∉ conviction_verdicts` | `test_default_verdict_may_not_be_a_conviction_verdict` |
| A3 | 辩护无多数时按采样顺序取首票 ⇒ 同一票集终态相反 | `_vote_verdict` 无多数统一落到 `inconclusive`（降级不翻案），不再回退 `ballots[0]` | `test_defense_no_majority_never_overturns_and_is_order_independent` |
| A4 | 投票 quorum 无下限，单票即成"严格多数"（upgrade 更无 topup） | 新增 `provider.required_quorum(votes)`；judge/defense 低于 quorum 即失败或降级，causal upgrade 报 `no_quorum` | `test_judge_quorum_*`、`test_defense_quorum_*`、`test_upgrade_quorum_*` |
| A5 | registry 内容哈希"可选"，手改 registry 可静默加载 | `load_intervention_registry` 从磁盘加载时**强制**要求 `content_hash`（内存路径不受影响，`build_*` 在校验后计算哈希） | `test_loader_requires_content_hash`、`test_loader_rejects_tampered_hash` |
| A6 | judge `basis` 非字符串触发未捕获 `TypeError`，中断整批 | 闭集判定前先 `isinstance(basis, str)` | `test_unhashable_basis_is_a_contract_failure_not_a_crash` |
| A7 | DSL 校验/运行不一致（`equals` 阈值不校验整数）、`value=0/False` 不可表达、正则 ReDoS、`matches` 大小写不对称 | 全部聚合算子统一整数校验；`_require_literal` 保留 `0`/`False`；拒绝反向引用与"量词嵌套歧义组"；`matches` 统一 `IGNORECASE` | 4 条 DSL 回归测试 |
| A8 | `run_defense` 的 guard 资格探测带副作用 ⇒ `deployment.fired` 重复记录同一 guard | `guard_actions(..., record=False)` 只读探测；终态才写轨迹 | `test_guard_eligibility_probe_records_nothing` |
| A9 | `upgrade.pre` 证据钩子每案执行两次（证据行与轨迹重复、fact 被覆盖） | 移除 `api.py` 的提前调用，改由 causal upgrade 阶段执行一次 | 既有 `test_api` 全绿 |
| A10 | 冻结听证永不命中（比较了 `positive` 未投影的 `contracts`/`confidence`） ⇒ "冻结"实为实时重跑，重引入 provider 噪声 | `frozen_hearing` 只比较投影出的 7 字段 | `test_frozen_hearing_is_reused_instead_of_rerunning_tracer_and_defense` |

### B 阶段（有效性）

| # | 缺陷 | 修复 | 回归测试 |
|---|---|---|---|
| B1 | "全 refuted"分支不查 `recall_ok` ⇒ 下游故障被洗成 authoritative clean | 该分支在 recall 未完成时降级为 `degraded_judge_only` 并记 `recall_unavailable_after_refutation` | `test_refuted_with_failed_recall_degrades_*`、`test_refuted_with_successful_recall_stays_authoritative` |
| B2 | 无害门默认只保护 1 TP + 1 TN；`boundary` 极性是空约束（U→F 也算收益） | 默认下限提到 **2 TP + 2 TN**；`boundary` 改为必须产生**决胜纠错**（`_decisive_correction`，未决→正确不算） | `test_harmlessness_floor_blocks_thin_sentinels_by_default`、`test_boundary_polarity_requires_a_decisive_correction` |
| B3 | 缺陷审计可"空过"（超期 fail-closed 被当成门逻辑拒绝）并被缓存 | 新增 `arm_has_infrastructure_failure`；不可评分的审计臂标记 `audit_inconclusive` 并**冻结**而非通过 | `test_audit_does_not_certify_a_fail_closed_arm`、`test_audit_freezes_when_defect_arm_cannot_be_scored` |
| B4 | 正式路径从不传 `resume=True` ⇒ 中断即整轮重来 | `run_full_self_evolution` 检查 checkpoint 存在性，存在则以 `resume=True` 续跑，不存在则明确拒绝 | `test_full_self_evolution_resumes_interrupted_cycle_with_checkpoint`、`test_interrupted_cycle_without_checkpoint_is_refused` |

### 未在本次处理（见 optimization-directions.md）

- 大规模成本/方差评估、独立终审数据与正式协议冻结（原计划第 3–5 项）——属于实验设计问题，不是代码缺陷；
- 定位一致性与命中率偏低——需单独度量，已记录在 progress.md。

### 后续实施（2026-09-17，同日）

本节最初列的「未在本次处理」中的两项结构缺口已落地，并新增 H_t 基座：

- **评估读数**：边际集极性下限 + 巡检对准已部署版本（测试 244 → 251）；依据与边界见 [方向对照评估](directions-assessment.md) 第 4、5 节。
- **H_t 基座**：`harness.py`（五组件清单与代际差异）、`gaps.py`（能力缺口后备清单）、`memory.py`（三类经验存储与有界检索），测试 251 → **292 passed**。三者均为记录与记忆设施，不参与门判定；经验尚未接入候选生成。
- 新增持久化一度导致测试写入仓库默认 `artifacts/evolution/`，已在测试与 smoke 配置中补齐隔离路径。

**方法论提醒**：以上修复让"测出来的东西是对的、且有统计意义"成为前提；但**它们本身不产生收益**。
真正的收益问题见 [optimization-directions.md](optimization-directions.md)——当前自进化仍处在无效循环中；
H_t 基座提供了描述、记忆与工单能力，但能力增长仍取决于后续把记忆接入改进过程。
