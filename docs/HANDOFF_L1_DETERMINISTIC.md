# HANDOFF — Skill Semantic Closure（Phase 1）

> 2026-08-13：本文已替换旧的 L1 deterministic Phase 2 交接。旧文中
> 旧文把可学习 FN 当作稳定结果、把 L1 当作模型行为执行器，均不再有效。
> 当前设计权威见 `docs/design/TECHNICAL_DESIGN.md` 与
> `sehd_rise/skills/capabilities.py`。

## 已落实的语义契约

- `SkillCapabilityRegistry` 是 Skill 能力的唯一真源。每项能力同时声明 layer、
  owner module、operation、reads、writes、failure shapes、direction、binding、
  local/terminal effect、executor、fitness postcondition 与 `does_not_support`。
- L1 executor 是 `l1_court_transform`。它只对 proof-court 已生成的结果执行七个
  确定性抑制/选择操作；不创建 candidate、evidence、certificate，不重跑 verifier，
  不改变 provider prompt 或 reasoning。
- L2 executor 是 `l2_adjudication_policy`。只有注册表中有可观测检测效果的动作
  可以作为 Skill repair 被计划；纯 telemetry / escalation 名称不能进入 Skill 提案。
- 因果切点不会因为当前没有可执行操作而被降级或改写。可学习但语言不可表达的
  失败进入 `CAPABILITY_GAP`，记录：

  ```json
  {
    "failure": "missing_error_candidate",
    "owner": "candidate_trigger",
    "desired_transition": "clean -> error",
    "missing_capability": "construct_candidate"
  }
  ```

- `NON_EVOLVABLE` 只用于非学习目标或稳定结果；不得用于掩盖缺失执行原语。
- 如果注册表声称存在精确能力但 RepairPlan 仍无法生成，状态是
  `UNADDRESSABLE_FRAMEWORK_DEFECT`，表示实现闭环断裂，而不是能力缺口。

## 三条机械验收链

1. FP：`atomic_verifier_instruction / withhold_certificate` 能编译并在实际 L1
   executor 中命中，目标证书消失，terminal 从 error 变 clean。
2. FN clean + missing candidate：保持 `candidate_trigger` 因果属主，返回结构化
   `CAPABILITY_GAP`；不得伪造 suppress 操作或降级为 `NON_EVOLVABLE`。
3. 对注册表逐项检查 executor operation 与 fitness postcondition 均存在；任何一端
   缺失都由 `loop/semantic_closure.py::validate_semantic_closure()` 报错。

对应测试：`tests/test_skill_semantic_closure.py`、
`tests/test_causal_gold_polarity.py`、`tests/test_loop_machine.py`。

## 下一阶段边界

Phase 1 只证明“Skill 语言—执行器—fitness”的机械闭环，不证明新 Skill 已在真实
数据上产生泛化收益。进入下一阶段后，仍须用新鲜 child genome、实际 provider 调用、
source edge 变化、下游 blocker 清除、独立 stable canary 与 sealed holdout 建立效果证据；
不得用编译成功、结构验收、返回码 0 或汇总计数替代。
