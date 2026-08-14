# evolution/ — 自进化框架落点与接口契约

本目录预留给重建的自进化框架。第一阶段只承诺接口与目录，不实现任何
进化权威。

## 允许的依赖方向（由 `tools/check_layers.py` 机械强制）

- `evolution/` 可以 import detector 的任意子层（runtime / skills / court /
  pipeline / 根接口模块）。
- `detector/` 永不 import `evolution/`（反向依赖直接判违规）。
- 新旧包之间禁止任何 import（旧库 `sehd_rise` 保持原样不动）。

## 第一阶段已提供的接口（detector 侧，冻结）

| 用途 | 接口 |
|---|---|
| 能力加载（加载 skill） | `evolution_api.load_skill_genome` / `load_policy_genome` / `create_baseline_genome` |
| 部署执行 | `api.detect(record, genome=..., policy_genome=...)`（无框架级全局状态） |
| 候选重放 | `replay.run_replay` / `run_constructive_replay`（`policy_scope` / `base_result` 语义与旧库一致） |
| 数据供给 | `evolution_api.project_for_evolution(result)`（Detection Truth 只读投影） |
| 终态读取 | `authority.resolve_evolution_state` 等 fail-closed 读取 |
| gold 隔离 | `FORBIDDEN_INPUT_FIELDS` / `EVALUATOR_ONLY_FIELDS` 双面同一防火墙 |

## 进化框架必须自己拥有的权威（不得下沉到 detector）

- 因果投影与 causal cut（对应旧库 `causal/`）
- 可修复性分诊（REPAIRABLE / CAPABILITY_GAP / UNADDRESSABLE_FRAMEWORK_DEFECT
  / FRAMEWORK_DEFECT / NON_EVOLVABLE 五态，单一判定真源）
- 假设搜索、结构门、fitness / canary / contrast 评估
- 经验记忆与跨案例学习、调用预算与账本

## 重建时的内部层级建议（沿用旧库纪律）

```text
evolution/
├── causal/        # 投影 -> cut -> episode -> repairability（单一权威）
├── experience/    # 机制级经验记忆
├── learning/      # 类型化投影协议（RepairIntent / FailureObservation / ...）
├── planning/      # 假设搜索与候选
├── execution/     # 动作执行与重放编排
├── evaluation/    # 效果 / fitness / canary / 门
├── meta/          # 跨案例策略学习器
└── loop/          # 统一编排
```

每层只依赖更低层 + detector；判定逻辑只写一次（单一真源），拒绝重复
推导、拒绝手调阈值。
