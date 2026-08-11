# SEHD v6 自进化无法产出有效 Skill 的根因诊断与修复报告

## 结论先行

通过对 `evolution_runner.py`（9570 行）、`fitness.py`、`signal_adapter.py`、
`reflection_engine.py`、`failure_episode.py`、`proof_contract.py` 的代码行级审查，
加上 **3 轮真实小规模实验**（含 gold 模式、8 案例混合集、unresolved-FN 聚焦集），
找到 **5 个相互叠加的根因**。它们不是"skill 质量不够"或"反思引擎架构不行"，
而是**进化循环内部的信号一致性与权威层级断裂**：检测器（PACA-TCL v3.5）的
verdict 机制是健全的（`proof_contract.py:576-588` 证实 surviving 证书集驱动终态），
反思 prompt 也是信息充分的（VERDICT-CARRIER RULE 已写入 system），但**进化框架
在 5 个关键判定点上自相矛盾，把正确的 skill 全部误杀**。

修复后实测：**L1 skill 与 L2 策略首次被接受，且跨案例泛化**。

---

## 实验证据链（修复前 baseline：0 编辑被接受）

**probe_run1**（gold 模式，4 案例：2 正确错误 + 2 clean）：
- BFCL/000、002（gold=true, verified_error 正确）→ STABLE，注册为 canary
- BFCL/001、007（gold=false）→ L2 归因 `boundary_terminal_conflict`，提出正确策略
  `consume_verified_clean_boundary`（unresolved→clean 匹配 gold=false）
- **但全部被 `reject_policy_fitness (stable_canary_regression:BFCL/000,BFCL/002)` 误杀**——
  canary 状态字节级不变（verified_error→verified_error）却判 `preserved=False`

---

## 5 个根因（按严重度排序）

### P0 — canary 信号分裂（全部编辑被系统性误杀）
`_canary_preserved`（evolution_runner.py:3793）硬编码 `attribute_failure(use_gold=False)`，
而主循环注册 stable canary 用 `use_gold=self.use_gold`（`_attribution_for_result`，
line 7249 分支）。gold 模式下 BFCL/000 是"正确稳定"案例（should_evolve=False），
但无 gold 上下文下显示 `EVIDENCE_INCOMPLETE → should_evolve=True`。

**第一性原理**：注册与检查的归因信号源必须一致——"注册为稳定锚"与"检查时不稳定"
是直接矛盾。**实测影响：L1 与 L2 全部编辑被拒。**

### P3 — 状态解析错位（unresolved-FN 静默不可见）
`resolve_evolution_state`（signal_adapter.py）非 policy 路径回退到
`resolve_authoritative_state`（occurrence-ledger 优先）。Camel/005 顶层
state=unresolved（court 裁决）但 occurrence_ledger=verified_error →
进化读到 verified_error==gold → CORRECT → 永不触发。

**第一性原理**：进化循环消费的是 **court 的 terminal 裁决**（gold 极性、触发、
fitness 全对着 terminal），而 occurrence ledger 记录"错误事实上是否发生"，两者可分离。
**实测影响：gold=true 的 unresolved-FN（BFCL/004、006 等）全部静默跳过。**

### P4 — gold 权威被 instrumentation 缺口架空（真改进被拒）
`_attribute_intervention_fitness`（evolution_runner.py:2083）在 gold 验证的 task
改进（task_score 1→2）上仍因 postcondition 不满足（`field_obligation_absent`——
recovery_evidence_refs 不在结果 schema）+ provider 未自报 applied_rule_ids
而降级为 `intervention_not_executed`。BFCL/004 的 require_evidence_field skill
使 unresolved→verified_error（gold=true 正确），raw assessment=strong_improvement，
却被拒绝。

**第一性原理**：`_enforce_expected_effect_observation` 明确声明 "A gold-verified
task improvement is never downgraded"，但它运行在降级之后——gold 权威被
instrumentation 缺口架空，与文档自相矛盾。

### P1 — VERDICT-CARRIER RULE 要求 LLM 看到它看不到的数据
主反思 prompt（`build_causal_reflection_prompt`）的 system 里 VERDICT-CARRIER RULE
要求 LLM 针对 `court_balance.verdict_carriers` 选择证书，但主路径注入的是
`episode_projection`（**不含 carriers**）——含 carriers 的 `runtime_observation`
只在 critic/diagnosis 辅助路径使用。LLM 被要求遵守一个它看不到的列表 → 瞎猜
证书或直接 no_edit。

### P2 — 无 gold 模式 fitness 误杀 FP 方向修复
`assess_source_fitness`（fitness.py）对 direction 非 None 的模块（如
atomic_verifier_instruction）local_score 下降硬判 `worse`。合成验证：同一 withhold
修复在 gold=false 下是 `local_improvement`，无 gold 下是 `worse`。

**第一性原理**：direction 注解只在 gold 下有意义（gold 告诉往哪走）；无 gold 时
分数下降既可能是修复（FP 撤证）也可能是回归，硬判 worse 会丢弃所有无 gold FP 修复。

---

## 修复（全部第一性原理，非补丁）

| # | 文件 | 修复 |
|---|------|------|
| P0 | evolution_runner.py | `_canary_preserved` 新增 `use_gold`/`gold_label` 参数，L1/L2 两条 canary 路径都传注册上下文 |
| P3 | signal_adapter.py | `resolve_evolution_state` 改为 terminal state 优先（row.state/proof.state），occurrence-first 仅作历史回退 |
| P4 | evolution_runner.py | gold task 改进（task_after>task_before）时保留评估，不被 instrumentation 缺口降级 |
| P1 | reflection_engine.py | `build_causal_reflection_prompt` 新增 `runtime_observation` 参数，合并 court_balance 进主 prompt |
| P2 | fitness.py | 无 gold 时 direction 模块的 local_score 下降降级为 `no_effect`（可审计）而非 `worse`（否决） |

---

## 修复验证（端到端）

**1. canary 修复（P0）**：probe_run2 中 BFCL/001 的 L2 策略从 `reject_policy_fitness`
变为 `accept_policy`，canary audit 显示 `ctx=gold, preserved=True`；**且策略泛化到
未见过的 BFCL/007、010（unresolved→verified_clean，gold=false 正确）**。

**2. 状态解析修复（P3）**：Camel/005 从 CORRECT（永不触发）变为
`unresolved_should_error` 正常触发；BFCL/001 行为不变；L2 策略 replay 的 terminal
状态解析正确。

**3. gold 权威修复（P4）**：probe_focus 中 BFCL/004 的
`atomic_verifier_instruction/add_rule` skill 从 `reject_fitness_no_effect` 变为
**`accept_batch（strong_improvement，task_score 1→2）`** —— 审查中第一个被接受的
L1 skill，内容：当候选引用证据只含"agent 在影响前已确认并修复"的事件时，
要求 `recovery_evidence_refs` 字段。

**4. prompt 修复（P1）**：主 prompt 实测包含 verdict_carriers 列表。

**5. fitness 修复（P2）**：无 gold 时 FP 修复降级为 `no_effect`（不再 worse），
gold 模式行为不变。

---

## 回答用户的核心问题

> 到底是自进化框架、底层的检测器或者说反思引擎的架构设计不行，
> 还是代码框架存在 bug？

**答案：都不是架构方向错误，而是代码框架中存在 5 个信号一致性与权威层级断裂的 bug。**
检测器的 surviving-set→verdict 机制是健全的（e2e 验证：withhold 规则确实执行、
幸存集收缩、court 正确重判）；反思 prompt 的约束是充分的；L1/L2 双层可进化面
设计是合理的。真正的问题是：**canary 注册/检查信号分裂、状态解析错位、
gold 权威被 instrumentation 架空、prompt 要求 LLM 看它看不到的数据、
无 gold fitness 方向误判**——这些叠加起来使得**每一个正确 skill 都在某个环节
被误杀**，从而表现为"产不出有效 skill"。

**修复后，进化首次产出被 gold 验证的 L1 skill 和可泛化的 L2 策略。**

---

## 下一步建议（未做，属于实验协议层）

1. **无 gold 模式专项验证**：P2/P4 修复主要影响 gold 模式；无 gold 的
   `directional_evidence` 路径仍需 canary 池充足——建议跑无 gold 批验证。
2. **更大规模 holdout 验证**：本报告验证了"skill 能被接受且单案例有效"，
   跨案例泛化仅 3 案例演示；需按 README 的独立 holdout 协议测量泛化增益。
3. **provider 执行语义审计**：e2e 发现规则注入后 provider 输出整体漂移
   （appeal_error_*→initial_error_*）——自然语言规则对 provider 的影响是全局的，
   建议用更窄的条件 + 更明确的输出字段约束提升命中精度。
