# HANDOFF — Entity-level Skill Grounding（Phase 2A）

> 2026-08-13：Phase 2A 已把 `ConditionSpec -> Executor` 从聚合布尔匹配改为
> 强类型实体绑定；Phase 2B 在此基础上只加入一个受限 `construct_candidate` harness。
> `emit_certificate`、`attach_evidence` 与 verifier override 仍被禁止。

## 已落实的运行时契约

- `skills/binding.py` 是 L1 grounding 的唯一实现：atom 返回实体集合，`all_of`
  对同一实体求交，`any_of` 去重合并，`not` 在当前 domain 的 universe 上做 anti-join。
- Binding domain 为 `claim_anchor / candidate / certificate / origin / coverage / boundary / event`；
  每个 binding 携带实体标识、父标识、局部 features、evidence refs 与实际命中的字段。
- `SkillCapabilityRegistry` 为每个 capability 声明 `input_binding_domain`、
  `required_binding_fields` 和 `cardinality`。`ActionSpec` 只从该注册表编译这些字段。
- `RepairPlan` 携带 `target_domain / grounding_query / required_binding_schema`，因此计划
  在进入反思前已经回答“修哪个类型的对象、如何找到、必须具备哪些标识”。
- add/modify 提案必须先在 source artifact 上产生 `BOUND` proof；proof 随 `SkillEdit`
  和规则 metadata 持久化。部署到新案例时仍重新动态绑定，不复用 source entity id。
- executor 只接收通过 capability contract 的 Binding；不存在 first-match 或把
  `output_field` 当 target id 的回退路径。

## 可观测失败与统计边界

状态链已拆为：

```text
PROPOSAL_VALIDATED -> BOUND -> EXECUTED -> TARGET_EDGE_CHANGED
```

绑定失败在 fitness 前终止：

- `BINDING_NOT_FOUND`：查询在 artifact 中没有合法实体；execution 层。
- `BINDING_AMBIGUOUS`：`exactly_one` capability 得到多个实体；execution 层。
- `BINDING_DOMAIN_MISMATCH`：domain 或必需标识字段违反注册表；framework 层。

三者均不会计为 `SOURCE_NOT_IMPROVED`，也不会污染 Skill 泛化或收敛统计。
每次检测的 `l1_execution` 同时输出 `bound / fired / skipped`，可恢复
`bound_rate | compiled` 与 `executed_rate | bound`。

## 机械验收

`tests/test_skill_grounding.py` 覆盖：

1. 两个不同 candidate 分别满足 A/B 时，`A all_of B` 不产生 binding；同一实体满足时绑定。
2. `not` 是实体 domain anti-join。
3. `withhold_certificate + CandidateBinding` 在 mutation 前以 domain mismatch 失败。
4. source certificate 经 condition → binding → executor 后消失并清除 terminal edge。
5. transfer artifact 无合法 target 时得到 `BINDING_NOT_FOUND` 且不执行。
6. `exactly_one` 多命中得到 `BINDING_AMBIGUOUS`。
7. source 预验证把 binding proof 写入候选，随后进入 `SkillEdit` 持久化链。

## Phase 2B 已完成的机械闭环

- `skills/anchors.py` 在反思前从 label-free trajectory 确定性投影固定 `ClaimAnchor`
  universe；稳定身份绑定 record/event/span/hash，递归 gold firewall 拒绝 evaluator 字段。
- `construct_candidate` 输入是 `claim_anchor`、基数为 `exactly_one`，只产出中性 candidate；
  Skill 无权自由选 span、写 evidence/certificate/verdict 或覆盖 verifier。
- `detection/continuation.py` 从冻结 baseline artifact 只续跑 qualification、propagation、
  focused prosecution、defense 与 proof-court recomputation；不重跑 census。
- `ConstructiveExecutionProof` 逐层记录 anchor、constructor、candidate、qualification、
  verifier、certificate 与 terminal edge；六种 construction outcome 不再压入
  `SOURCE_NOT_IMPROVED`。
- `tests/test_constructive_recall.py` 覆盖确定性、gold firewall、exactly-one binding、
  candidate/anchor identity、局部续跑保留 unrelated census、有效 FN、clean verifier 与幂等。

机械闭环通过不等于真实数据上的 effectiveness 或 generalization；下一判断点是仅选择
missing-candidate FN 的小规模真实 provider batch，并逐级审计 anchor → bound → constructed
→ qualified → verified → certificate → terminal。独立 stable canary 与 sealed holdout 仍是
后续 generalization / promotion 证据，不能由结构验收替代。
