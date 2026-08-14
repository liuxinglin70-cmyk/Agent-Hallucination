# evolution/ — 自进化框架落点与接口契约

本目录预留给重建的自进化框架。第一阶段只承诺接口与目录，不实现任何
进化权威。

## 允许的依赖方向（由 `tools/check_layers.py` 机械强制）

- `evolution/` 可以 import `detector`（单向）。
- `detector/` 永不 import `evolution/`（反向依赖直接判违规）。
- 新旧包之间禁止任何 import（旧库 `sehd_rise` 保持原样不动）。

## 第一阶段已提供的接口（detector 侧，冻结）

| 用途 | 接口 |
|---|---|
| 能力加载（加载 skill） | `evolution_api.load_skill_genome` / `load_policy_genome`（不透明载荷 + hash 校验） |
| 部署执行 | `api.detect(record, genome=..., policy_genome=...)`（显式参数，无全局状态） |
| 数据供给 | `evolution_api.project_for_evolution(result)`（Detection Truth 只读投影） |
| 终态读取 | `result.resolve_evolution_state` 等 fail-closed 读取 |
| gold 隔离 | `packet.EVALUATOR_ONLY_FIELDS` 输入防火墙 |

## 最小检测器的冻结输出契约（`sehd2.detection_result.v1`）

- `state` / `decision_authority` / `runtime_valid`：fail-closed 权威三元组；
- `proof_adjudication`：terminal_basis、prosecution/defense coverage、
  policy_evaluation（预留 L2 表面）、证书聚合事实；
- `providers` / `authority_failure_reasons`：机器可读失败诊断；
- `provenance`：本次运行部署的 genome 指纹。

## 进化框架必须自己拥有的权威（不得下沉到 detector）

- 因果投影与 causal cut
- 可修复性分诊
- 假设搜索（census/verification/defense 之外的技能生成与选择）
- 基因组变异、fitness / canary / contrast 评估、promotion
- 经验记忆与跨案例学习、调用预算与账本

## 重建时的内部层级建议

```text
evolution/
├── causal/        # 投影 -> cut -> episode -> repairability（单一权威）
├── experience/    # 机制级经验记忆
├── planning/      # 假设搜索与候选
├── execution/     # 动作执行与重放编排（重放 = detect(genome=candidate)）
├── evaluation/    # 效果 / fitness / canary / 门
├── meta/          # 跨案例策略学习器
└── loop/          # 统一编排
```

每层只依赖更低层 + detector；判定逻辑只写一次（单一真源），拒绝重复
推导、拒绝手调阈值。
