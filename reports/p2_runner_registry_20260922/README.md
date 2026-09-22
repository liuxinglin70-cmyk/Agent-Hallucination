# P2.1：显式 runner registry 与 typed_semantic

日期：2026-09-22
范围：`detector/interventions.py`、`detector/api.py`、`evolution/capability_surface.py`、
`evolution/contracts.py`、`evolution/proposer.py`
证据等级：**diagnostic-only**（离线；`runner_registry.json` 是契约执行演示，不含检测质量结论）

## 修掉的缺口

runner 空间此前是**隐式 if/else**：除 `typed_dsl` 外的一切都落到内置谓词表。这种形状有一个
特定失效模式——**未注册的 runner 名会静默执行另一个执行器**，而"没有声明"与"用默认"无法区分。
两处派发点（`evidence_for` 与 `recall_triggered`）各写一遍同样的分支。

## 落地

**`RUNNER_REGISTRY`**：每个执行器声明 supported hooks / intervention types / input schema /
deterministic / learned / requires_artifact / **terminal_authority**。

- 两处 if/else 派发改查表，且**未注册 runner 是错误而不是回退**；
- 声明被强制：runner 的 hooks 与 intervention types 在 `validate_intervention` 中校验；
- `RunnerSpec` 构造即拒绝"learned 且持终局权威"的组合。

**`typed_semantic`**（新增，`evidence_only`）：

- 仅 `evidence_check`，仅 `judge.post` / `upgrade.pre` / `defense.pre`；
  **刻意排除 `recall.trigger`**——"不得定罪"的执行器也不得决定何时启动召回网络；
- 裁决词表 `supported` / `contradicted` / `abstained`，**弃权是一等结局**；
- 必须绑定 `artifact{id,digest}`，且**运行时会与执行器实际加载的 artifact 比对**。
  只声明不比对的话，摘要就是装饰品：加载器可以递上模型 B 而条目写着模型 A，
  下游一切检查仍会通过，因为它们只看到声明值。

**`SemanticExecutor`**：执行器由调用方**注入**，框架不自带模型。
`Detector(..., semantic_executor=...)` → `compile_runtime_plan` → `InterventionPlan`。
无执行器时**fail closed**，不允许用确定性默认值冒充语义判断。

## 五条保证（`verify_runner_registry.py` 逐条演示）

| 保证 | 拒绝理由 |
|---|---|
| 未注册 runner | `runner 'no_such_runner' is not registered; known runners are [...]` |
| 身份不匹配 | `the executor loaded artifact 'classifier-B' but the entry pins 'classifier-a'` |
| 无执行器 | `typed_semantic needs an executor; none was supplied to this detection call` |
| 越界裁决 | `semantic verdict 'convicted' is outside the declared vocabulary` |
| 语义证据定罪 | `unknown trigger fact 'claim_supported'` |

最后一条值得说明：语义裁决**被记录**（L1 行为门与人都能看到），但它的谓词
**不进入封闭 fact 词表**，所以任何 guard 都无法消费它。这不是巧合而是结构性的——
`convict_mechanical_violation` 只接受内置或 typed_dsl 谓词的定罪裁决。因此
"evidence-only" 同时由声明与结构保证：**声明说明意图，结构使其无法绕开**。

## 附带修正

- `evidence_check` 分支在 runner 已注册但谓词未定义时，原报"no registered executor"
  会误导调用方；现在按实际原因分派消息（proposer 的修复循环会读这段文本）。
- `make_intervention(..., artifact=...)`：构造器此前无法表达带 artifact 的条目，
  而它构造时即校验，所以 P2.1 的 runner 会**无法从草拟路径到达**。
- `capability_surface_report()` 新增 `executable_inventory.runners`，并区分
  `name_a_learned_executor`（真）与 `generate_a_learned_executor`（假）——
  框架能**绑定**一个注入的执行器，但它**不自带**，把后者报成真会声称一个不存在的能力。

## 验证

- `pytest -q`：**730 passed**（§26 结束时 715，本轮 +15，全部在 `test_runner_registry.py`）；
- `scripts/verify.py`：七项全 PASS；
- `python -m evolution capability-audit` 现列出三个 runner 及其权威级别；
- **未运行真实服务，未创建 holdout。**

## 未做 / 仍需注意

1. **没有任何真实语义执行器存在**：`typed_semantic` 是一个**可绑定的契约**，
   不是一个可用的能力。要真正使用它，需要实现一个 `SemanticExecutor` 子类
   并提供其 artifact 摘要；本包只保证"一旦绑上，约束会被执行"。
2. **proposer 的 prompt 现在描述该 schema**，但 current offline/hybrid 路径不会主动
   产出它（它需要外部执行器才有意义）。这是有意的：不该鼓励为不存在的执行器起草条目。
3. **P2.3 变形测试 gate 未做**：§15.2 的六类变形尚未固化为新 runner 的准入条件。
   在真实执行器接入前，这一步是必需的。
4. **P2.2 Level B 沙箱未做**。
5. 检查点 A 仍未运行；独立 holdout（§8.3）未创建。
