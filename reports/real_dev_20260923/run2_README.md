# 真实运行 2（2026-09-23）：开发阶段在真实数据上首次跑通

**结论：开发循环第一次在真实数据上完整运行——起草、L0、L1、定向修订、再次 L1——两个版本
都被 L1 证伪，四门因此一个候选都没看到。** 这正是这个阶段存在的理由：那类候选此前会直接进入
62 分钟的双臂晋升运行（§30 实测），现在在 15.5 分钟内被本地廉价检查拦下。**本轮不是自进化
成功，也不是检测能力提升**——没有任何版本通过，准入依然为 0。

原始产物：[run2_archive/](run2_archive/) · 机器可读：[readings_run2.json](readings_run2.json)

## 运行身份

| 项 | 值 |
|---|---|
| 运行目录 | `artifacts/selfevo-real-dev2-20260923/` |
| 配置 | `evolution/configs/selfevo_real_dev2_20260923.json`，SHA `0ec2667bb966b204` |
| 源码摘要（启动前冻结） | `97bd8580208abd7b`，git head `0ae0d0d` |
| 检测配置摘要 | `da3131e671c5d981`（`detector/configs/autodl.json`） |
| 模型 / 端点 | `GLM-5.3-flash` / `https://www.autodl.art/api/v1` |
| 并发 | 8 workers，**全程只有一个 cycle 进程**（启动后即刻核对） |
| 开发配置 | `enabled=true`、每轮机制上限 1、每机制版本上限 **2**、L1 上限 40 例评估 |
| 墙钟预算 | `cycle_timeout_seconds=14400`（240 分钟）——**唯一相对运行 1 的改动**，见下 |
| 划分 | mini、种子 20260923：sentinel 6（3/3）、gain 6（4 error / 2 clean）、patrol_pool 18 |
| holdout | `final_size=0`、`final_reads=0`——**未创建也未读取** |
| 墙钟 | 总计 **186.8 分钟**（审计 118.4 + 巡逻 52.9 + **开发 15.5** + 其余 <1） |
| 账本 | 11 条，哈希链 `valid=true` |
| 终态 | `completed`，`active_after_cycle=[]` |

**运行期间未再改动任何源码**：运行 1 的教训（`SOURCE_DRIFT_DURING_RUN.md`）已落实，本次
冻结摘要与运行所依据的源码一致。

## 相对运行 1 的唯一改动：墙钟预算

运行 1 证明审计与巡逻共用一份墙钟，审计单独吃掉 89 分钟（配额 120 分钟的 74%），随后整批
bootstrap fail-closed。运行 2 把 `cycle_timeout_seconds` 从 7200 提到 14400。

- **这是基础设施预算，不是门限**：`gates` 整块逐字未改（`prerun_state_run2.json` 里存了整份
  配置可供核对）；
- 运行 2 的审计实际用了 **118.4 分钟**（比运行 1 更久），若仍用 120 分钟配额，巡逻会再次被饿死。
审计变慢的主因可定位到单例：`Magentic_One/049.json` 逐例 `elapsed_seconds` 实测运行 1 用 36.0 分钟、
运行 2 用 **93.8 分钟**（同一案例、同一模型、同一配置）；
- 即便如此，巡逻只用了 52.9 分钟就完成 8 例且 `unresolved=0`——**说明运行 1 的 10 例未决确实
  是预算排队，不是检测困难**。

## 读数

### 1. 巡逻：8 例全部真跑，无未决

| tp | tn | fn | fp | unresolved | 规模 |
|---|---|---|---|---|---|
| 3 | 3 | 1 | 1 | **0** | 8 |

诊断出 **1 个机制**，为**定位族**（`FAM-loc-mislocated-117fce1c070d`，
机制 `M-c393f57ff12f`）。`bootstrap_count=0` 且 `family_found=true`——初始巡逻就找到了家族，
没有触发补抽（运行 1 的 `family_found=false` 其实是"补抽整批没测"，见 run1_README）。

### 2. 开发阶段：**真实数据上的首次完整运行**（本轮核心读数）

```json
{"round_path": "development", "targets": 1, "candidate_count": 0,
 "versions_failed_l0": 0, "versions_failed_l1": 2, "versions_passed": 0}
```

| 版本 | 类型 | L0 | L1 | 父本 | 证伪理由 |
|---|---|---|---|---|---|
| `v001` | draft | **6/6 通过** | **否** | — | `no target case improved on the development replay` |
| `v002` | **revision** | **6/6 通过** | **否** | `v001` | 同上 |

**L0 六项全过**：`schema`、`no_case_id`、`metamorphic_runner`、`structured_identity`、
`targets_mechanism`（hook `tracer.system` 能到达机制的失败阶段 `step_tracer`）、
`localization_annotation`（目标案例带 gold 步号）。即：**这个候选在形式上是合法的、
不泄漏、且指向正确阶段的**——它被拒不是因为格式问题。

**L1 在 6 例上配对测量**（目标 2 例 + 正常近邻 4 例，两臂）：

```json
{"paired_n": 6, "base_all_case_correct": 6, "active_all_case_correct": 6,
 "marginal_cases": 0, "new_errors": 0, "unresolved_added": 0, "health_regressed": 0,
 "flips": {"T->T": 3, "F->F": 3}}
```

读法：候选**确实触发了**（`fired_cases` 4 例，覆盖两个目标案例与两个近邻），但**判定与步号
一个都没变**——6 例全部 `T->T` / `F->F`。它无害、也确实到达了运行期，只是**没有修好它声称
要修的东西**。这正是 `check_l1` 要区分的"未触发"与"触发了但无效"：前者是没有测量，
后者是**测量到了失败**。本轮是后者。

### 3. 定向修订确实发生了，并且带上了证伪理由

`v002` 是对 `v001` 的修订（`kind=revision`、`parent_hash=afa597ba1139` 即 v001 的哈希），
其 `diagnosis.json` 记录的理由正是 `"no target case improved on the development replay"`。

两版措辞确有差别，说明修订不是换皮：

| | `v001` | `v002` |
|---|---|---|
| directive 措辞 | "name only a step that can host the error: its own event text, tool arguments, or prose must contain…" | "**before naming** the responsible step, **verify** that the specific erroneous value… actually appears in that step's own tool arguments…" |
| not_when | "Do not move the attribution when the currently named step's own text already contains…" | "Do not move or withhold the localization when… already contain the erroneous value…; **never**…" |
| 候选哈希 | `afa597ba1139` | `68a9f3f82ecd`（**不同**） |

从"只命名能承载错误的步"改成"命名前先验证"，是针对同一失败方向的合理修订路径。
**但它仍然没修好**——两版在 6 例上的读数是同一个形状（`marginal_cases=0`）。

### 4. 四门：一个候选都没看到（本阶段的目的）

| 项 | 读数 |
|---|---|
| `versions_falsified` | 2 |
| `candidates_reaching_the_gates` | **0** |
| `gate_entries` | `[]` |
| `proposals_in_cycle` | 0 |
| `admitted` / `active_after_cycle` | `[]` / `[]` |

**`gates_saw_nothing_when_nothing_passed=true`**：两个版本被 L1 证伪，于是没有任何候选进入
四门。对比 §30 的那一轮——同一个机制族的候选（`IV-1-001`，`text_boundary` / `tracer.system`
／目标 `localization`）**直接进入并消耗了一次完整的晋升运行**。本阶段的作用就是把这一步提前到
15.5 分钟、零晋升预算。

### 5. `cycle_end` 归档：**首次在真实运行上写入**

```json
{"archived": true, "policy": "cycle_end", "recorded": 2}
```

归档内容（`candidates/`）：

| 项 | 值 |
|---|---|
| `candidates.total` | 2，`by_status = {"failed_l1": 2}` |
| `components.total` | **0**（`by_kind = {}`） |
| `metric_role` | `diagnostic-only` |

`components=0` 是**正确行为**，不是缺陷：只有通过 L1 的版本才登记组件，两个版本都没通过，
所以没有组件——这与"存储没接线"是两件事，本轮证明了该判断（运行 1 无可归档内容→目录不存在；
本轮有内容→目录存在且写入 2 条）。

这同时**关掉了 §28.6 的第一处未接线**（候选存档无运行路径写入）：`archive.on=cycle_end`
在真实运行上确实写盘了。

### 6. 仍未写入的存储

| 存储 | 状态 | 为什么 |
|---|---|---|
| `route_failures.jsonl` | **不存在** | 本轮无提案进四门，没有"触发但无收益"的路由可记（自报 `observed=0, appended=0`） |
| `gaps.jsonl` | **不存在** | 能力规划无缺口（自报 `recorded=0, open=0`） |
| `memory/episodic.jsonl` | **不存在** | **本轮没有拒绝**——开发阶段的证伪走的是 `development.json`，不是四门拒绝；H_t 的 M 组件自报 `episodic/records=0` |

前两处的真实写入**仍未验证**（与 §26.5 同一类风险，本轮依然没有消除）。第三处值得单独说清：
上一轮（§30）的记忆写入来自**四门的拒绝**；本轮的候选根本没到四门，所以没有那条记录。
**开发阶段的证伪不写情景记忆**——这是一个可以被追问的设计点，本轮只如实记录现象。

## 这一轮证明了什么、没证明什么

**证明了（真实数据）**：
1. **开发阶段真的能拦下坏候选**：2 个版本在 L1 被证伪，四门一个都没看到；
2. **L0 是可用的廉价筛**：6 项检查全过，说明"拦下"不是因为格式/泄漏/阶段不匹配；
3. **L1 能区分"未触发"与"触发但无效"**：候选 `fired` 4 例却零变化，读数是
   "no target case improved"而**不是**"候选从未到达运行期"；
4. **定向修订闭环成立**：v002 带 v001 的证伪理由生成，措辞与哈希都不同；
5. **`cycle_end` 归档在真实运行上写入**，且只登记通过 L1 的组件（0 个）。

**没有证明**：
1. **没有任何版本通过 L1**，所以**没有测到"开发阶段能产出可用候选"**——只测到了它能拒绝；
2. **不是自进化成功**：0 准入，`G0 < G1 < G2` 无从谈起；
3. **没有检测/定位能力提升的读数**；巡逻读数（3/3/1/1）是**裸底座**的，不是候选效果；
4. **没有泛化**：693 例全在开发暴露范围内，未创建 holdout；
5. **没有跨代继承**：单轮、0 准入；
6. `route_failures` / `gaps` 的运行期写入仍未验证。

## 与运行 1 的对照（同一阶段、两个不同结局）

| | 运行 1 | 运行 2 |
|---|---|---|
| 可开发机制 | **0**（巡逻 10/16 未决） | **1**（巡逻 8 例无未决） |
| 开发目标数 | 0 | 1 |
| 开发版本数 | 0 | **2**（含 1 次修订） |
| `round_path` | `development`（**错报**，实际单轮） | `development`（正确，账本有事件） |
| 账本 `development` 事件 | 无 | **有** |
| 归档写入 | 无目录 | 2 条候选 |
| 墙钟 | 197.4 min（配额 120，**超支**） | 186.8 min（配额 240，未超支） |

**第二行是第一行的原因**：运行 1 的巡逻被墙钟饿死到无法诊断任何机制，所以开发阶段没有对象。
把预算调够之后，同一份代码立刻产出了可开发机制——**这支持"运行 1 的空结果来自基础设施，
而不是来自数据里没有可利用的机制"**。

## 关于本轮的边界（不可放宽）

- 20 例以内的所有读数都在 `datasets/protocols/development_exposure.json` 暴露范围内，
  **不是独立终审集**，任何读数都不构成泛化结论。
- 门限、划分、种子、检测包络、gold **一律未动**；唯一改动是墙钟预算，理由与证据见上。
- **本轮未产生任何准入**，active registry 仍为空。
- 开发阶段在 L1 之后**不再有第二次机会**：`max_versions_per_mechanism=2` 用尽即 `discarded`。
  本轮两个版本都没过，因此终止；**这不是"预算不够"的结论**，是"两版都没修好"的结论。

## 复现

```bash
python reports/real_dev_20260923/extract_readings.py artifacts/selfevo-real-dev2-20260923
python -m evolution verify-ledger --config evolution/configs/selfevo_real_dev2_20260923.json
```
