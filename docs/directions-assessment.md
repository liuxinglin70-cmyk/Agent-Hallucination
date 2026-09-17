# 方向对照评估：文档主张与代码现状

更新日期：2026-09-17。配套资料：[架构](architecture.md)、[当前进展](progress.md)、[优化方向](optimization-directions.md)、[代码 review](review.md)、[真实进化报告](../reports/evolution_real/README.md)。

本文把 `optimization-directions.md` 的设计主张逐条比到 `detector/` 与 `evolution/` 的当前实现，判断哪些已经具备、哪些缺失、以及下一步真正该动哪里。所有结论都给出代码位置；设计建议与已实现事实分开陈述。

## 0. 一句话结论

当前系统是「在固定检测器旁边寻找干预规则」，这一点文档判断正确；但**第一优先级不是长出手（improver），而是修尺子**——真实运行已经出现「做出了有效修改却读数为 0」的实例，评估集没有覆盖被修改的机制。本文之后的两项优化（边际集极性保证、patrol 对准已部署版本）已落地并带回归测试。

## 1. 当前闭环的真实形状

```text
patrol ─┬─▶ 失败族 ─▶ 诊断 ─┬─▶ actionable ─▶ proposer ─▶ 四门 ─▶ registry
        │   worksheet.py    │   engine.py    只输出 JSON   容差全为 0
        │   (裸底座)         └─▶ capability_gap ─▶ 🗑 丢弃
        └── 见第 5 节：现已改为「已部署版本 + 裸底座对照」
```

| 组件 | 现状 | 代码位置 |
|---|---|---|
| 巡检来源 | 每轮抽 `patrol_pool`，种子 `seed + cycle*7919` | `evolution/splits.py` |
| 巡检版本 | 优化前恒为 `empty_registry()`；现已改为已部署版本并保留裸底座对照 | `engine.py::_patrol_arms` |
| 诊断 | 失败族 → 机制签名 → 7 条治疗路由 → 类型化样本 | `worksheet.py::diagnose_failure_families` |
| 不可执行族 | `capability_gap` 不进 proposer，只进报告 | `engine.py::split_actionable_families` |
| 提案 | JSON：4 类干预 × 10 个 hook，零文件 IO、零执行 | `proposer.py` |
| 评审 | structural / harmlessness / behavior / M，全为纯函数 | `gates.py` |
| 准入 | 终结式：任一门失败即拒绝，不搜索修复 | `gates.py` 模块 docstring |
| 持久化 | ledger（事件）+ registry（marginal 历史）+ checkpoint（逐例观测） | `ledger.py`、`registry.py`、`checkpoint.py` |

### 1.1 能力边界由代码自己声明

`evolution/capability_surface.py` 给出了机器可读的自评，可直接引用而无需猜测：

```python
"open_ended_self_evolution": False,
"generative_freedom": {
    "new_predicate_program": True,
    "new_executor": True,
    "new_hook": False,
    "new_guard_action": False,
    "execution_graph_rewrite": False,
    "sandboxed_code_generation": False,
    "model_weight_update": False,
},
"required_for_open_ended_evolution": [
    "typed declarative predicate programs",   # 已内联实现
    "typed control-flow policy artifacts",    # 已内联实现
    "typed execution-graph artifacts",        # 缺
    "immutable capability artifact store",    # 缺
    "sandboxed new executors with property and metamorphic tests",  # 缺
],
```

即：唯一的能力创建通道是 proposer 内联声明新的 typed-DSL 谓词程序，由 `detector/predicate_dsl.py` 固定解释器执行。它能扩展**观测词汇与控制流策略**，不能创造新执行器、新 hook、图结构或权重。

### 1.2 检测器侧的可修改面

`detector/api.py::_detect` 是一条硬编码单趟流水线：validate → packet → judge →（条件）recall scanner → causal upgrade → tracer → defense → 确定性裁决。跨阶段主载荷是显式 dict，但**编排状态是闭包局部变量**（`api.py:382` 的 `nonlocal`），没有统一的 State / StateDelta 对象，也没有 `run(state, tools, budget) -> state_delta` 接口。

干预点共 10 个，全部是模块级字符串键（`detector/interventions.py:47-58`），类型为闭集四种（`interventions.py:43-45`）。因此文档 §1.3「固定执行流程限制了改进类型」成立。

## 2. 文档主张逐条核对

结论标记：**成立**（代码证实）、**部分**（方向对但机制描述需修正）、**未实现**（设计建议，代码无对应）。

| 文档主张 | 判定 | 依据 |
|---|---|---|
| §1.1 学习任务来自裸底座而非能力前沿 | **成立** | 优化前 `preview_cycle` 与正式 cycle 均传 `empty_registry()` |
| §1.2 proposer 是格式受限的提案器 | **成立** | `proposer.py` 全文件无 `open/read_text/subprocess/ast/exec` |
| §1.3 固定流程限制改进类型 | **成立** | 10 个固定 hook、4 类闭集干预，无图改写 |
| §1.4 反馈被当成终结理由 | **成立** | `echo_of_rejected` 永久拉黑同款提案（`proposer.py:546`） |
| §2 需要可编辑、可装载的 Harness | **部分** | registry 可装载已实现；harness manifest 不存在 |
| §3.1 给 improver 可操作观测 | **部分** | worksheet 诊断很细，但没有结构化执行轨迹对象 |
| §3.2 用诊断干预找修改点 | **未实现** | 无局部试验（surgical probe）机制 |
| §4.2 从成功过程编译技能 | **未实现** | 无 skill/tool 数据结构 |
| §4.3 三类经验持久化 | **未实现** | 无程序性记忆，只有事件流与 marginal 历史 |
| §5 工作流节点化 | **未实现** | `execution_graph_rewrite: False` |
| §6.2 存档保存可继续工作的分支 | **部分** | registry 有 snapshots 与 rollback，但无「研究分支存档」 |
| §6.4 自举 | **未实现** | 检测侧工具不向 improver 暴露，且 improver 不存在 |
| §7 改进器本身如何改善 | **未实现** | `meta_eval.py` 声明只读，无任何回灌 |

### 2.1 需要修正的两处文档判断

**其一：JSON 提案协议并非「表达不了修复」。** 文档 §1.2 认为仅靠 JSON 无法表达真实修复过程。但真实第 1 轮的 `IV-1-002` 是一条 `recall_trigger` JSON 提案，它**确实通过了行为门、确实触发了一次、并在 sentinel 上修好了 1 个漏报**。失手之处不在表达力，而在评估集无处承认它（见第 3 节）。

**其二：文档 §1.1 描述的风险目前尚不可观测。** 「某错误已被上一代修好，却继续主导下一轮提案」需要一个前提——曾经成功准入过干预。历史真实准入为 0，从未有改动被部署，因此该风险是**潜伏**而非已观测。优化前的设计是刻意护栏（注释自称 VaG contamination guard），不是疏忽；改动时必须保留裸底座对照，把不可观测的保证变成可测的对照（第 5 节即如此实现）。

## 3. 关键实证发现：做出了有效修改，尺子却看不见

`reports/evolution_real/README.md` 记录的第 1 轮三个候选：

| 候选 | 类型 | 结构门 | 无害门 | 行为门 | 边际门 | 结果 |
|---|---|---|---|---|---|---|
| IV-1-001 | recall_trigger | 通过 | 通过 | **失败** | — | 拒绝（0 触发） |
| IV-1-002 | recall_trigger | 通过 | 通过 | 通过 | **失败** | 拒绝（`marginal_cases=0 < 1`） |
| IV-1-003 | text_boundary | 通过 | 通过 | **失败** | — | 拒绝（方向不符） |

报告自己指出：gain 集 G 的构成为 **2 TP + 1 TN + 1 FP，完全没有 FN**（`paired_n=4`）。而 IV-1-002 是一条专门提升召回的候选。

根因在 `evolution/metrics.py:119`：

> `marginal_cases` is the number of all-case correct predictions gained

`marginal_cases = active_correct - base_correct`（`metrics.py:205`）。一个漏报被修好，只有在 G 里存在「gold=error 且底座判错」的案例时才计分。G 里没有这种案例，所以 IV-1-002 注定读出 0。

### 3.1 引擎其实知道，但没有据此改变结论

`engine.py:422-425` 已经在计算机会覆盖：

```python
opportunity = {
    "recall": sum(r.get("gold") is True and r.get("prediction") is False for r in control_g),
    "precision": sum(r.get("gold") is False and r.get("prediction") is True for r in control_g),
}
```

当某极性机会为 0 且 M 未通过时，`engine.py:456-458` 会标注：

```python
m.update(evaluable=False, status="not_evaluable",
         reason=f"no {polarity} repair opportunities in gain; admission remains blocked")
```

这个标记会写进 rejection feedback（`engine.py:976-980`）并传给 proposer，proposer 侧也理解它（`proposer.py:408`："not_evaluable means missing evaluation coverage, not a falsified mechanism"）。

但**没有任何地方据此改变本轮结论**：准入仍然显示为失败，不会触发重新抽样或换评估集。于是「不可评估」被读成了「无收益」。这比文档的判断更精确：不是反馈缺失，而是反馈被收集、被传递、却不作用于决策。

### 3.2 这不只是一个公信力问题

一个 recall 候选只有两种命运：被拒，或偶然通过。若以「通过率」为目标优化，搜索会系统性地偏向**容易被当前 G 计分的机制**（在 G 上刚好有对应错误的那类），从而扭曲研究结论。这是规格博弈，与朴素刷榜同源，只是发生在「正确性规格」层。

## 4. 已完成优化一：边际集极性保证

### 4.1 改动

`evolution/splits.py` 新增 `_stratified_gain_sample`，在既有的来源配额抽样之上加一层极性下限：

```python
gain = _stratified_gain_sample(
    remaining, gain_size, gold,
    min_positives=int(split_cfg.get("mini_gain_min_positives", default_floor)),
    min_negatives=int(split_cfg.get("mini_gain_min_negatives", default_floor)),
    rng=rng,
)
```

- 开发协议默认每极性至少 1 例；正式协议默认 `max(1, gain_size // 4)`。
- 下限无法满足时**报错而非静默弱化**（`ValueError`，带 `gain_min_negatives` 等关键词）。
- `split_manifest_report` 新增 `gain_polarity` / `sentinel_polarity`，使 G 的构成可读。

### 4.2 效果

改动前开发协议 G 的 error/clean 为 2/2（碰巧），但这是来源配额抽样的偶然结果，不受控。改动后成为**构造成立**，并由 `tests/test_repository.py` 的两个测试锁定（含下限不可满足时 fail-closed）。

### 4.3 边界（必须明确）

这是一个**必要不充分**条件。G 里有 error 案例 ≠ 该案例是召回机会；机会取决于底座在该案例上的行为。例如优化后开发协议 G 含 2 个 error 案例，但若底座把这两个都答对，召回类候选仍然无分可拿。

因此本改动解决的是「G 连一个该极性的案例都没有」这一类，不解决「有案例但无机会」。后者由下一节处理——把机会覆盖作为**每轮可读的诊断量**，而不是假装它已被保证。

## 5. 已完成优化二：patrol 对准已部署版本

### 5.1 改动

原本注释写着「Patrol always runs the bare base」，且以 VaG 污染为由禁止使用已部署 registry。现改为：

- 新增配置 `patrol_registry`，取值 `active`（默认）或 `bare_base`（恢复旧行为，转义舱口）。
- `engine.py::_patrol_arms` 返回 `(patrol_rows, bare_rows, contrast)`：巡检臂 = 已部署版本，参照臂 = 裸底座。
- registry 为空时（冷启动）两臂是**同一次评估**，因此冷启动成本与改动前一致；只有真正部署了干预才会多出第二次 sweep。
- 新增 `_failure_contrast`，把失败分成三类：

```text
new_failures        巡检版本判错、裸底座判对  → 被准入干预引入的退化
persisting_failures 两臂都判错              → 常驻残余
resolved_by_active  巡检版本判对、裸底座判错  → 被更新修好的
```

- 对比结果写入 `worksheet["failure_contrast"]` 与 `worksheet["patrol_registry"]`，随 cycle 报告与 ledger 落盘。
- `preview_cycle` 与正式 cycle 共用同一套臂，避免「预览与实跑测的不是同一个版本」。
- 未解析与非权威行计为失败（`_row_is_failure`）：从底座视角，没判出来就是没解决。

### 5.2 为什么必须保留裸底座臂

去掉裸底座，`resolved_by_active` 与 `new_failures` 都无法计算——这两个概念本身是相对量。保留它使文档 §1.1 想要的「面向当前版本残余错误」与原有「VaG 保证」**同时成立**，且后者从断言变成可核对的数据。

### 5.3 为什么现在做这一步是安全的

文档 §1.1 的动机是「旧毛病反复主导提案」。该风险需要曾经成功部署过干预才会显形，而历史准入为 0。因此这次改动在冷启动阶段行为不变（单臂），部署后才生效——不会在无证据时改变任何既有读数。

### 5.4 验证

`evolution/tests/test_capability_evolution_gain.py::test_next_cycle_patrols_the_deployed_version_and_sees_the_capability` 做端到端两轮验证：第 1 轮空 registry 准入能力并修好巡检失败；第 2 轮巡检已部署版本，这些失败出现在 `resolved_by_active`，裸底座仍报坏。该测试在旧协议下确认失败（`resolved_by_active: []`），因此不是平凡通过。

另有 `test_patrol_runs_the_deployed_version_with_a_bare_base_reference`、`test_patrol_registry_bare_base_escape_hatch_skips_the_second_arm`、`test_patrol_registry_rejects_unknown_mode` 覆盖三态。

## 6. 顺带清理的悬空与冗余

| 项 | 问题 | 处理 |
|---|---|---|
| `gates.py` 的 `UNFREEZING_HOOKS` | 在 `__all__` 中导出但全仓库无定义，形似已实现的解冻开关 | 从 `__all__` 移除 |
| `budget.sentinel_cases` | 无任何代码读取；真正的控制是 `split.sentinel_positives/negatives` | 从默认配置与两份出厂配置删除 |
| `budget.marginal_cases` | 无任何代码读取；真正的控制是 `split.mini_gain_size`。名字强烈误导 | 同上删除 |

`engine._evaluate` 仍有一处真实调用（governance 路径），保留。

## 7. 尚未处理，且不建议现在处理

以下为文档主张但本轮**有意不做**的项，附理由：

- **建最小 improver（文档 §10 第 2 项）。** 在评估集无法计分某类改进时先长手，只会得到一台更快生产「无法判定之物」的机器。应先让尺子能读出改进，再长出更多改进手段。
- **capability_gap 转工单（当前被丢弃）。** 这是把 #3 之后的自然下一步：每轮 gap 已带 `reason` 与 `routes`，可直接成为「待建能力清单」。但它依赖评估集先能计分新能力，否则建成也无法判定。
- **三类经验持久化、harness manifest、工作流节点化。** 都是 §2/§4/§5 的真实缺口，但属于能力形成阶段，应在评估闭合后推进。
- **断点续跑缺口。** 真实第 2 轮被中断后，`cycle_started` 已写入导致同 ledger 无法重跑，且该轮账本只存候选 ID 与 notes、无完整候选正文，无法精确恢复。这是一个独立且高价值的工程项：闭环要跑几十轮时它会持续消耗时间。建议单独立项，不与评估改动混做。

## 8. 下一步建议顺序

| 顺序 | 动作 | 为什么在这个位置 | 可核对的结果 |
|---|---|---|---|
| 1 | 让 `not_evaluable` 作用于决策：某极性无机会时，触发重抽或改用机会覆盖满足的评估集，而非直接判负 | 反馈已存在却未被消费，是当前最便宜的结构性缺口 | 机会为 0 时不再产出「0 收益」的伪拒绝 |
| 2 | 把机会覆盖做成每轮可读量并入报告/账本 | 需求方从猜测变为显式契约 | cycle 报告可直接读出「本轮可计分哪类机制」 |
| 3 | 补断点续跑 | 独立工程项，长闭环的必需设施 | 中断后可同 ledger 续跑 |
| 4 | capability_gap → 待建能力清单 | 让「无法执行」从垃圾桶变成工单箱 | gap 不再静默流失 |
| 5 | 最小 improver：读源码、局部执行、形成可装载改动 | 前四项就位后，才有能力判断新长的「手」是否真的有用 | 一次真实可定位的 harness 瓶颈被自主修好 |

第 1 项是针对第 3 节实证的直接修复；第 2 项把它从一次性补丁升为协议属性。两者都不放宽任何门限。

## 9. 一句话区分「优化」与「累积自改进」

只有执行过程变化发生时，应称为一次 harness 优化；程序性能力积累、学习任务推进、改进策略复用**持续发生**，才有理由讨论累积自改进。当前项目处于「首次优化已可发生，但尚不能稳定计分」的阶段——这正是先修尺子的理由。
