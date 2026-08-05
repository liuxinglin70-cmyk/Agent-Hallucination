# SEHD v6 检测器-自进化框架排查报告

本报告定位 50 例实验（`evolve_50_20260806`）暴露的三个现象：champion 与
baseline 差异极小、检测底座失效、覆盖率损失。每条结论都绑定源码位置与
实验日志计数；无法由证据支持的假设写在末节"已证伪的假设"，以免下一轮
会话在错误前提上继续推理。

## 1. 证据基线

证据来源：`experiments/results/evolve_50/run/evolution_result.json`（该运行由
修复前的旧代码产生，产物已随仓库清理删除；本报告与
`EVOLUTION_FIXES_SUMMARY.md` 保留全部关键计数与分布，作为唯一事实来源）：

```text
total=50  triggered=36  accepted=1  rejected=16  stable=8  runtime_invalid=6
steps=63  duration=21816s
```

63 个 step 的门控决策分布：

| 决策 | 次数 | 决策 | 次数 |
|---|---|---|---|
| accept_policy | 22 | reject_conflict | 4 |
| retry_invalid_mutation | 8 | reject_fitness | 4 |
| skip_runtime_invalid | 6 | retry_fitness_no_effect | 4 |
| skip_stable | 5 | skip_invalid_mutation | 4 |
| 其余 7 类 | 各 1 | | |

最终产物：L1 champion 仅 1 条进化规则（`accept_batch`），L2 policy genome
2 条 item，且两条 item 的 action 都是 `downgrade_coverage`。

## 2. 缺陷一：L2 接受后直接跳过 L1，占据 61% 的触发预算

`evolution_runner.py:4319`：

```python
l2_accepted = run_l2_policy_case(...)
if l2_accepted or l2_stable_source:
    continue
```

`run_l2_policy_case` 的契约（同文件 3120-3125 行）只承诺**被拒绝**的 L2
提案回落 L1；被接受时 `continue` 直接进入下一案例。

实验计数证实该路径吞掉了绝大部分预算：22 个 `accept_policy` 案例中，
**同一案例再出现 L1 步骤的数量为 0**。36 个触发案例里 22 个（61%）自始至终
没有产生任何 L1 规则提案。

这是 champion 与 baseline 差异极小的**直接机制**：L1 genome 在这 22 例上
从未被写入的机会。

### 为什么这是设计缺陷而非取舍

L2 policy 的动作词表（`v6_contracts.L2_ACTIONS`）只有
`downgrade_coverage`、`invalidate_coverage_claim`、`emit_telemetry_only`，
且 `policy_evaluator._lower_coverage` 保证 coverage 单调下降、永不上升。
即 L2 层在设计上**只能削弱裁决主张，不能补充证据**。用一个只会做减法的
层去消费"证据不足"类失败的预算，失败必然复发——而复发时案例仍被归因到
L2，形成闭环。

## 3. 缺陷二：L2 单向降级叠加 L1 空补充，构成覆盖率损失

最终 policy genome 的两条 item：

```text
consensus_gate_blocked_nonempty        -> downgrade_coverage(prosecution -> incomplete)
boundary_state_conflicts_with_terminal -> downgrade_coverage(defense    -> incomplete)
```

两条覆盖控辩双方，且都是降级。结合缺陷一（L1 无补充），运行期表现为：
coverage 被持续调低，而调低后暴露的证据缺口没有任何一层去填。日志中
`prosecution_coverage=0.30 / prosecution_coverage_state=incomplete` 在多数
案例的 `source_before` 与 `source_after` 中同时出现，即降级已成为稳态。

`_recompute_terminal_state`（`policy_evaluator.py:61-73`）要求
`prosecution == "complete" and defense == "complete"` 才可能给出
`verified_clean`。任一侧被降级后，该分支永久不可达，终局只能是
`verified_error` 或 `unresolved`——这正是 50 例中 unresolved 高发的结构性
原因。

## 4. 缺陷三：module_local_score 与模块输出面不对齐（存在但非 no_effect 根因）

`fitness.py:272-350` 的 `module_local_score` 是接受门控的唯一局部信号。
将其与 `module_charter.MODULE_OBSERVABLE_FIELDS`（模块自有输出面的单一
事实来源）逐模块比对，确实存在失配——但审查后判定这些失配**不是**
`evolve_50` 中 5 次 no_effect 的根因（见下文"审查修正"）。

| 模块 | charter 声明的输出面 | module_local_score 实际读取 | 对齐 |
|---|---|---|---|
| candidate_trigger | candidate_count | candidate_count>0 的布尔 | 部分（丢失增量） |
| evidence_obligation | prosecution_coverage, prosecution_certificates, causal_field_coverage | min(prosecution, defense) | **否** |
| candidate_to_verifier_route | prosecution_coverage, prosecution_certificates | valid_error_cert + prosecution | 是 |
| atomic_verifier_instruction | quarantined_certificates, causal_field_coverage | valid_error_cert + prosecution | **否** |
| clean_defense_boundary | defense_coverage, defense_certificates | defense + valid_defense_cert | 是 |
| causal_link_test | causal_field_coverage, localization_status | causal_field + localization | 是 |
| earliest_origin_tiebreak | responsible_step, localization_status | localization + causal_field | 是 |
| abstention_rule | state, trigger | crs_complete + consensus_gate + coverage 均值 | **否** |

三处失配各自可复现日志中的具体失败：

**abstention_rule**（4 次 no_effect 中占 3 次）。charter 声明其输出面是
boundary finding（经 `state` / `trigger` 观测），但 unresolved 分支
（`fitness.py:326-349`）只读 `crs_complete`(0.4)、`consensus_gate_applied`
(0.4) 与 coverage 均值——**没有一项属于该模块自己的输出面**。三项在编辑
前后都不受该模块影响，所以分数恒定：

```text
0.4 + 0.4 + 0.4 x (0.3 + 1.0) / 2 = 1.06
```

日志中 BFCL/073、OpenManus/095、SmolAgents/014 三例的
`local_score_before = local_score_after = 1.06` 与此完全吻合。同一份
`fitness_check` 里 `observable_deltas` 记录了
`prosecution_certificates:decreased`，即规则确实改变了 provider 输出，但
打分函数对该变化结构性失明。

**evidence_obligation**。`min(prosecution, defense)` 的初衷（避免单边取胜）
是正确的，但 charter 中该模块的三个输出面**全在 prosecution 侧**，它无权
影响 defense。当 defense 为弱侧时（日志中 prosecution=0.3 / defense=1.0，
弱侧恒为 prosecution，尚未触发；但反向组合下）分数被一个该模块无法触及的
量钳住。BFCL/024 的 `before=after=0.3` 即弱侧钳制的结果。

**atomic_verifier_instruction**。charter 的核心输出面是
`quarantined_certificates`（隔离证书），而打分读的是
`valid_error_certificate_count + prosecution`。隔离一张伪证书会让
valid 计数**下降**，被 `_MODULE_SCORE_GOLD_DIRECTION` 判为反向；虽然
gold-clean 案例上有补偿分支，gold-error 案例上这仍是纯负信号——而隔离
伪证书恰恰是该模块的主要正确行为。

### 为什么这不是阈值问题（审查修正）

5 次 no_effect 的 delta 全部**精确为 0.0**（0.3→0.3、1.06→1.06、
12.0→12.0），不是"略低于 epsilon=0.05"。调整 epsilon 不会改变任何一次
判定。信号本身没有产生，不是信号太弱。

但审查进一步发现：观测到的 delta 大多在 `prosecution_certificates`
（abstention_rule charter 禁止触碰的字段），`BFCL/024` 与
`SmolAgents/014` 的 `observable_deltas` 为空。即这些编辑要么没有产生任何
可观测变化（no_effect 判定正确），要么改变的是**错误模块**的输出面。
因此问题指向**反思路由质量**（为何编辑落在本模块禁触的字段上），而非
打分失明。据此，改动 `module_local_score` 的方案已回退（见第 6 节与
EVOLUTION_FIXES_SUMMARY.md 的 R2）。

## 5. 缺陷四：mutation 产出损耗

12 次 invalid_mutation 的原因分布：

| 原因 | 次数 |
|---|---|
| provider_empty_content: finish_reason=length | 6 |
| rule_spec: condition_overload | 3 |
| expected_effect_contract_invalid_direction: decreased | 3 |

三类性质不同：

- **6 次 finish_reason=length**：反思调用的输出 token 预算不足，模型在
  reasoning 阶段耗尽额度、正文为空。这是配置问题，不是模型能力问题。
- **3 次 condition_overload**：结构门要求条件至多一个连接词
  （`safety_gate` 的 `condition_overload` 检查），而 prompt 虽已声明该约束，
  模型仍在复合条件上重复失败。
- **3 次 invalid_direction:decreased**：模型填写的方向值 `decreased` 不在
  `EXPECTED_DIRECTIONS` 词表内（合法值为 `decrease`）。这是词表与
  prompt 示例的**字面不一致**导致的纯机械损耗。

## 6. 修复状态与审查结论

实施状态、回退记录与最终落地的修复见
[EVOLUTION_FIXES_SUMMARY.md](EVOLUTION_FIXES_SUMMARY.md)。本节只记录审查
修正后的结论。

| 编号 | 改动 | 状态 | 结论 |
|---|---|---|---|
| F1 | expected_direction 词表归一 | 落地 | 修 3 次机械损耗 |
| F2 | 空正文走修复路径 | 落地 | 修 6 次 finish_reason=length |
| F3 | condition_overload 判定单一来源 | 落地 | 行为不变的重构 |
| F4 | gold task score 先于诊断守卫判定 | 落地 | 修 BFCL/088 误拒 |
| F5 | action target 绑定 provider schema | 落地 | 修 3/5 no_effect 的动作无落点 |
| F6 | LLM conflict gate 降级为 advisory | 落地 | 修 4/17 gate 前误杀 |
| F7 | artifact owner 路由首位 + 起诉证据缺口投影 | 落地 | 破 unresolved→abstention 默认 |
| F8 | error cert 畸形→verifier / 缺失→obligation | 落地 | 证书质量 vs 证据义务面拆分 |
| F9 | rule_trace 拆分 compiled/injected | 落地 | no_effect 归因可分辨注入与否 |
| F10 | slot-contract 拒绝重试归一 | 落地 | rule_spec_contract 可重试且有约束文本 |
| F11 | 防御侧证据缺口投影 | 落地 | incomplete defense→clean_defense_boundary |
| F12 | applied_rule_ids 执行闭环 | 落地 | 三层 rule trace：compiled/injected/applied |
| F13 | rule_effects 字段级绑定 | 落地 | 四层 rule trace + 字段级影响归因 |
| R1 | L2 accept 后回落 L1 | 回退 | L1 已在 20 例上跑过，瓶颈是接受率非预算 |
| R2 | module_local_score 对齐输出面 | 回退 | 破坏 13 条契约测试，且未修好观测案例 |
| R3 | L2 降级需 L1 支撑 | 回退 | 阻断合法的 hollow-defense 纠正 |

**审查修正**：缺陷一"L2 吞预算"经复核不成立。L1 路径在另外 20 个触发
案例上实际运行（retry_invalid 8 + reject_fitness 4 + retry_fitness_no_effect
4 + reject_conflict 4），仅产出 1 条接受。真正的瓶颈是 **L1 接受率低
（1/20）**，根因在反思质量与 fitness 判定，不在 L2 预算占用。

**缺陷三修正**：5 个 no_effect 案例的观测 delta 多在
`prosecution_certificates`（abstention_rule charter 禁止触碰的字段），
`BFCL/024` 与 `SmolAgents/014` 的 delta 为空。这些 no_effect 判定本身
正确 —— 编辑要么无效果，要么改变的是错误模块的输出。

**深层根因（后续审查已定位并修复）**：
- 3 个 no_effect（`BFCL/073`、`OpenManus/095`、`SmolAgents/014`）都指向
  `abstention_rule/shape_decisive_standard` —— 该 opcode 在 boundary
  route 的 provider schema 中**没有输出字段落点**，规则注入后无可执行
  动作，属 F5 修复范围。
- `BFCL/088` 的 `verified_error → unresolved` 纠偏被 `responsible_step_lost`
  误拒，属 F4 修复范围（gold task score 判定顺序）。
- 4 个被 `reject_conflict` 丢弃的提案多为"一般规则的更具体细化"，在
  source replay 之前被弱 LLM 误杀，属 F6 修复范围。

## 7. 已证伪的假设

以下假设曾出现在早期分析中，经源码核验为**不成立**，记录于此以免重复
推导：

- **"baseline 是空规则集"**：`genome.create_baseline_genome()`
  （genome.py:1036 起）八个模块全部有 immutable baseline 规则
  （clean_defense_boundary 4 条，candidate_trigger / evidence_obligation /
  atomic_verifier_instruction / causal_link_test 各 2 条等）。日志
  `rule_trace` 中的空列表记录的是**进化新增**规则，与 baseline 无关。注入
  "种子规则"会与既有 baseline 重复定义。
- **"L1/L2 层级判定在 step_attributor 中"**：层级由独立的确定性分类器
  `failure_classifier.classify_failure()` 决定（规则 2-4 判 L2），
  `evolution_runner.py:1005` 调用。`step_attributor` 产出的
  `target_module` 仅是 L1 内部模块假设，不参与层级决策。
- **"epsilon=0.05 阈值过严"**：全部 no_effect 的 delta 精确为 0.0，
  与阈值无关。
