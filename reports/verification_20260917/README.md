# 真实验证运行结果（2026-09-17）

本次在仓库整理后的代码上，用真实模型服务把两条主线各独立跑了一遍：

- **检测器底座**：开发协议 40 例（30 演化池 + 10 复测集）真实检测；
- **自进化框架**：开发协议（30+10）真实两轮，`proposer → detector 配对评估 → 四门`。

服务：`GLM-5.3-flash`，全路由同一模型（judge/defense/recall 均 3 票），端点为 `https://www.autodl.art/api/v1`；进化的 proposer 继承 judge 路由。

**结论先行：**

1. **检测器侧跑出了可用读数。** 40/40 完成，0 unresolved，终态准确率 **0.725**，TP 17 / FP 4 / FN 7 / TN 12，macro-F1 **0.721**，authoritative 38、degraded 2。这是开发集上的真实读数，不是准确率或泛化结论。
2. **自进化侧两轮都跑完，但准入 0——而且不是候选不够好。** 第 1、2 轮各产出 4 个候选，**8 个候选全部被拒**。其中 **5 个死于同一条不可能被满足的门**：无害门要求 sentinel 至少 `2 TP + 2 TN` 基线正确，而本协议的 sentinel 只有 3 正 + 3 负，实测 protected 只有 `2 TP + 1 TN`。**只要基线在这 6 例里有 2 例判错，该门就永久失败，与候选无关。**
3. **因此本次实验没有验证到「自进化收益」，也没有验证到「自进化无效」。** 它验证到的是更基础的一件事：**当前开发协议与无害门下限尺寸失配，准入通道被整体堵死。** 这是框架自身的一个结构性问题，必须先修，才能谈收益。

---

## 1. 运行范围与合规性

| 项 | 值 |
|---|---|
| 检测器配置 | `detector/configs/autodl.json` |
| 进化配置 | `evolution/configs/development_real_run.json` |
| 进化协议 | `datasets/protocols/development.json`（仓库既有，未新增/未修改） |
| 检测器案例清单 | `artifacts/_dev40_cases.json`（由既有协议的两个集合拼接，40 例） |
| 进化工作目录 | `artifacts/evolution_real/` |
| 原始数据 | 未改动；`datasets/protocols/` 下仅既有 3 个文件 |
| 模型调用 | 真实服务，无 mock |

说明：本次未新建任何协议文件。检测器用的 40 例就是既有开发协议的 `evolution_set` + `validation_set`，进化用的划分完全由 `development.json` 经仓库现有 `build_split` 生成（seed 20260824）。

### 1.1 两条主线的独立性（本次实测确认）

| 检查 | 结果 |
|---|---|
| `detector/` 是否 import `evolution` | **否**，零处引用 |
| `evolution/` 是否 import `detector` | 是，仅经公开契约（`interventions`、`predicate_dsl`、`packets`、`predicates`） |
| 独立入口 | `detector/__main__.py`、`evolution/__main__.py` 各自存在 |
| 独立运行 | 本次检测器用 `python -m detector batch` 单独跑完；进化用 `python -m evolution experiment` 单独跑完 |

依赖方向单向 `evolution → detector` 在本次运行中得到确认，两条主线可各自独立自主运行。

---

## 2. 检测器：开发协议 40 例真实结果

命令：`python -m detector batch --dataset-root datasets/raw/agenthallu/AgentHallu --cases-file artifacts/_dev40_cases.json --run-id dev40 --runs-root artifacts/detector_dev40 --workers 8 --config detector/configs/autodl.json`，随后 `evaluate`。

### 2.1 指标

| 指标 | 值 |
|---|---|
| 请求 / 落盘 | 40 / **40**，missing 0 |
| decisive / unresolved | 40 / **0**（completion 1.0） |
| 终态准确率 | **0.725** |
| TP / FP / FN / TN | 17 / 4 / 7 / 12 |
| positive precision / recall | 0.810 / 0.708 |
| positive F1 | 0.756 |
| macro F1 | **0.721** |
| authoritative / degraded | 38 / **2** |

`degraded 2` 是重要读数：这两例不是未决，而是**降级判断**（`degraded_judge_only`），即结论没有达到 authoritative 门槛。按 `prediction_state` 的口径，降级判断在严格意义上不算数；上表按仓库 `evaluate` 的既有口径统计，两者已分别列出，未混算。

### 2.2 逐例分布

- **TP（17）**：Camel/018、Camel/054、Magentic_One/093、Octotools/005、Octotools/023、Octotools/030、OpenDeepSearch/067、OpenDeepSearch/080、OpenDeepSearch/088、OpenDeepSearch/095、OpenManus/064、OpenManus/073、SmolAgents/037、OpenDeepSearch/076、OpenDeepSearch/078、SmolAgents/022、SmolAgents/036
- **TN（12）**：BFCL/107、Magentic_One/036、Magentic_One/085、Octotools/040、OpenDeepSearch/002、OpenDeepSearch/084、BFCL/012、BFCL/068、BFCL/106、Camel/028、Magentic_One/067、Magentic_One/049
- **FN（7）**：BFCL/017、BFCL/041、BFCL/062、OpenDeepSearch/035、OpenDeepSearch/063、OpenDeepSearch/086、Magentic_One/074
- **FP（4）**：Camel/048、Magentic_One/050、OpenDeepSearch/077、SmolAgents/019

### 2.3 步骤定位

| 项 | 值 |
|---|---|
| 参与定位的案例 | 24 |
| 命中（responsible_step == gold_step） | **10** |
| 准确率 | **0.417** |

这是检测器最弱的一环，且与终态准确率分离：判定对了但定位漂移。仓库既有记录（`reports/real/`）也报告过同类现象（约 1/6）。本次 0.417 高于该小样本观察，仍不足以支撑定位能力结论。

### 2.4 成本与耗时

| 项 | 值 |
|---|---|
| 逐例耗时（40 例） | 合计 10,230.6s，均值 **255.8s**，最大 **2700.1s** |
| 墙钟（8 并发） | 约 **1 小时 15 分** |

---

## 3. 自进化：真实两轮结果

命令：`python -m evolution experiment --mini --cycles 2 --config evolution/configs/development_real_run.json`，工作目录 `artifacts/evolution_real/`（起始 registry 为空）。

### 3.1 两轮都完整完成

| 项 | 第 1 轮 | 第 2 轮 |
|---|---|---|
| 状态 | **completed** | **completed** |
| 耗时 | 3698s（约 62 分） | 2207s（约 37 分） |
| 缺陷注入审计 | passed，cache miss | passed，cache miss |
| patrol 规模 | 20 | 20 |
| patrol 结果 | TP 7 / FP 2 / FN 5 / TN 3 / **unresolved 3** | TP 8 / FP 2 / FN 5 / TN 3 / **unresolved 2** |
| 稳定失败族 | 3 | 3 |
| 产出候选 | 4 | 4 |
| 准入 | **0** | **0** |
| active_after_cycle | 空 | 空 |

与上一份记录（2026-09-16）不同，本次**第 2 轮完整跑完**，两轮都有 `cycle_completed` 落盘。断点续跑未被动用（无中断）。

### 3.2 稳定失败族（两轮相同）

| 失败族 | 机制 | 成员 |
|---|---|---|
| `FAM-fn-judge_false_no_recall_candidates-a4f141369d93-P70fe7f80fd90` | judge 判阴、召回未触发 | BFCL/017、OpenDeepSearch/086 |
| `FAM-fn-judge_false_no_recall_candidates-a4f141369d93-Pf96f7bc87dcb` | 同上，另一画像 | OpenDeepSearch/035、OpenDeepSearch/063 |
| `FAM-fp-judge_conviction-117fce1c070d` | judge 阳性误报 | BFCL/107、Camel/048 |

3 个族全部为 `actionable`，**能力缺口清单为空**（`recorded: []`），即没有发生「稳定失败但动作词表无法处理」的情况。

### 3.3 候选与四门逐条结果

第 1 轮（真实 GLM 一次调用产出 4 个候选，25,662 token）：

| 候选 | 类型 / hook | structural | harmlessness | 终态 |
|---|---|---|---|---|
| IV-1-001 | `recall_trigger` / `recall.trigger` | **失败**：谓词在族成员上未一致命中（`not_applicable`） | — | rejected |
| IV-1-002 | `recall_trigger` / `recall.trigger` | 通过（Jaccard 0.0895） | **失败**：`protected_tn=1 < 2` | rejected |
| IV-1-003 | `evidence_check` / `defense.pre` | 通过（Jaccard 0.0423） | **失败**：`protected_tn=1 < 2` | rejected |
| IV-1-004 | `decision_guard` / `adjudicate.guard` | 通过（Jaccard 0.0769） | **失败**：`protected_tn=1 < 2` | rejected |

第 2 轮（第一次调用 4 个候选因 `value must be non-empty text` 与未知 trigger fact 被合同层拒绝，repair 后产出 4 个合法候选；30,791 + 30,892 token）：

| 候选 | 类型 / hook | structural | harmlessness | 终态 |
|---|---|---|---|---|
| IV-2-001 | `evidence_check` / `defense.pre` | 通过 | **失败**：`protected_tn=1 < 2` | rejected |
| IV-2-002 | `decision_guard` / `adjudicate.guard` | 通过 | **失败**：`protected_tn=1 < 2` | rejected |
| IV-2-003 | `recall_trigger` / `recall.trigger` | **失败**：谓词在族成员上未一致命中 | — | rejected |
| IV-2-004 | `recall_trigger` / `recall.trigger` | **失败**：同上 | — | rejected |

BEHAVIOR 与 M 门在两轮中**从未被执行到**——所有候选都在更早的门上被拦截。

### 3.4 决定性发现：无害门在开发协议上不可满足

无害门（`evolution/gates.py:437`）的 protected 集合**完全由基线臂决定，与候选无关**：

```python
protected_baseline = [
    row for row in baseline_rows
    if row.get("gold") in (True, False)
    and row.get("prediction") is row.get("gold")          # 基线判对
    and str(row.get("authority") or "") == "authoritative"
]
```

门下限来自 `harmlessness_min_protected_tp/tn`，配置未显式设置，取默认 **2 / 2**（`gates.py:517`）。

本次两轮的 sentinel 完全相同（6 例：3 正 + 3 负），实测构成：

| sentinel 案例 | gold | 基线预测 | 是否计入 protected |
|---|---|---|---|
| Octotools/023 | error | correct | protected TP |
| OpenDeepSearch/088 | error | correct | protected TP |
| BFCL/041 | error | **wrong** | 排除（baseline_incorrect） |
| Magentic_One/036 | clean | correct | protected TN |
| OpenDeepSearch/077 | clean | **wrong** | 排除（baseline_incorrect） |
| SmolAgents/019 | clean | **wrong** | 排除（baseline_incorrect） |

得到 `protected_tp=2`、`protected_tn=1`。下限要求 `2 TP + 2 TN`，**负例侧最多只能拿到 1**。

这不是偶发：`sentinel_negatives=3`，要满足下限必须 **3 个负例里至少 2 个基线判对**，而实测只有 1 个判对。**只要基线在这 3 个负例里判错 2 个（本次正是如此），任何候选都会被拒绝，无论它多有效。** 该状态在第 1、2 轮、以及 2026-09-16 那次运行中完全一致。

### 3.5 与修复前记录的对照，说明这是新引入的失配

| 运行 | 代码状态 | sentinel 构成 | protected | 无害门默认下限 | 无害门结果 |
|---|---|---|---|---|---|
| 2026-09-16 | 修复前 | 同（2 TP / 1 TN） | 2 / 1 | **1 / 1** | **通过** |
| 2026-09-17（本次） | 修复后 | 同（2 TP / 1 TN） | 2 / 1 | **2 / 2** | **失败** |

2026-09-16 修复记录中的 B2 项把默认下限从 `1+1` 提到 `2+2`（`docs/review.md` 第 8 节，理由是「默认只保护 1 TP + 1 TN」保护太薄）。**但开发协议的 sentinel 尺寸没有同步调整**：`split.sentinel_positives=3`、`sentinel_negatives=3`，对 `2+2` 的下限不留任何余量。修复前那次之所以能跑到行为门和边际门，正是因为下限是 1+1。

**这是本次运行暴露的最重要事实：无害门的加强在开发协议上把准入通道整体关闭了。**

### 3.6 一个需要区分开的细节：两个候选本身确实有害

无害门因覆盖不足而提前返回（`delta=None`），所以它**没有真正评估候选造成的破坏**。我另外直接从检查点的候选臂观察到了破坏：

- `IV-1-003`（`evidence_check` / `defense.pre`）把 sentinel 里的 **Octotools/023、OpenDeepSearch/088 从 TP 翻成 FN**（gold=error，基线判对，候选判错）。
- `IV-1-004`（`decision_guard` / `adjudicate.guard`）造成**同样的两例翻转**。

即这两个 precision 方向候选**确实会打坏两个既有正例**，即使下限被放开，它们也会被无害门的 `new_fn=2` 拦下。所以它们的拒绝结论是**正确的**，只是理由被覆盖不足提前覆盖了——门报的是「测不了」，真相是「测了会坏」。这正对应仓库已经记录的 `not_evaluated`（未曾被测量）与 `rejected`（已被实质证伪）不可互相替代的区别。

`IV-1-002` 则相反：它**没有造成任何翻转**（3 个 protected sentinel 全部保持），是一条干净的候选，纯粹死于覆盖不足。

### 3.7 评估覆盖读数（instrument）

两轮一致：

| 项 | 值 |
|---|---|
| `gain_opportunity` | `{recall: 0, precision: 1}` |
| `unmeasurable_polarities` | `["recall"]` |
| `terminal_counts` | `{rejected: 4}` |
| `instrument_limited` | false |

**召回方向可计分案例为 0**——gain 集里没有「gold=error 且基线判错」的案例。这与 2026-09-16 第 1 轮的失效机制相同：4 个候选里有 2~3 个是召回方向，而该方向在 G 上无分可得。默认 `strict` 策略下这仍记为「未测量」并阻断准入。

### 3.8 审计、账本、成本

| 项 | 值 |
|---|---|
| 缺陷注入审计 | 两轮均 **passed**；`IV-DEFECT-001` 因泄漏案例 ID 被 structural 拒绝，`IV-DEFECT-002` 被 harmlessness 拒绝 |
| 账本 | **26 条记录，哈希链 valid=true，broken_at=null** |
| registry | 全程为空，`content_hash` 保持 `1a4fe1d7…`，无静默污染 |
| H_t 清单 | 两轮各一条，digest 链式 `a6b59863… → 6e51a1ab…`，registry_hash 未变 |
| 情景经验 | 8 条，outcome 均为 `rejected` |
| 累计 token（检测器侧缓存） | 78 个缓存文件 / 252 次调用 / **1,689,457 token** |
| proposer token | 25,662 + 30,791 + 30,892 |

注：检测器侧 token 只统计落盘缓存，四门的 fresh 重跑不写入该缓存，**实际消耗高于此数**。

---

## 4. 本次运行可以支持与不能支持的结论

### 可以支持

1. **检测器底座可独立、真实、端到端运行**，40/40 完成、0 unresolved，产出可复算的混淆矩阵与定位读数。
2. **自进化框架可独立、真实、端到端运行两轮且两轮都完成**，包含真实 LLM proposer、真实检测器配对评估、四门、账本与 H_t 记录。
3. **框架的机械保护在真实运行中有效**：缺陷注入审计拦下了标签泄漏；结构门拦下了谓词不命中的候选；账本哈希链完整；registry 无污染。
4. **准入阻断的根因已定位到具体机制，而非笼统的「收益未成立」**：无害门的 `2+2` 下限与开发协议 `3+3` sentinel 尺寸失配，使 protected_tn 上限为 1，任何候选都无法通过。

### 不能支持

1. **不能说明自进化有收益**——准入 0。
2. **不能说明自进化无收益**——8 个候选里 5 个从未被真正测量（含 1 个干净候选 IV-1-002），BEHAVIOR 与 M 门从未执行。这是「未测量」，不是「已证伪」。
3. **不能作为检测器准确率或泛化结论**——40 例全部在开发协议内，且 693 例整体都在 `development_exposure.json` 暴露清单内；无独立终审集。
4. **不能说明定位能力**——24 例样本，且与既有小样本观察差异较大。
5. 本次运行**没有**修改任何源码、配置或协议；未放宽任何门限（诊断用的放宽下限运行已中止并清理，未计入本报告）。

---

## 5. 下一步（按证据优先级）

1. **修尺寸失配。** 无害门下限与 sentinel 规模必须联立决定。可选：把 `sentinel_positives/negatives` 提到能覆盖 `2+2` 且有余量的规模（如 6+6），或让下限随 sentinel 规模自适应，或在协议构建时**校验下限可满足性并 fail-closed 报错**——后者最符合仓库既有风格（极性下限已是「不可满足即报错」）。
2. **修完后重跑同一协议两轮**，才能第一次真正测到「候选能否通过无害门、能否在 G 上得分」。在此之前任何收益陈述都无效。
3. **召回方向无计分案例**（`gain_opportunity.recall=0`）需单独处理：或调整 G 的构造使召回机会存在，或显式启用 `coverage_response` 的替代评估集（该机制目前只驻留内存、未持久化）。
4. **定位是独立的失效面**（0.417），应与终态准确率分开度量与改进。

---

## 6. 产物位置

| 内容 | 路径 |
|---|---|
| 检测器 40 例结果 | `artifacts/detector_dev40/dev40/`（`cases/*/result.json`、`evaluation.json`） |
| 案例清单（本次拼接） | `artifacts/_dev40_cases.json` |
| 进化 cycle 报告 | `artifacts/evolution_real/cycles/cycle001.json`、`cycle002.json` |
| 进化账本 | `artifacts/evolution_real/ledger.jsonl` |
| 逐例观测检查点 | `artifacts/evolution_real/checkpoints/cycle00{1,2}/` |
| 缺陷审计判定 | `artifacts/evolution_real/reports/defect_audit_verdict.json` |
| H_t 清单与历史 | `artifacts/evolution/harness/harness.json`、`harness_history.jsonl` |
| 情景经验 | `artifacts/evolution/memory/episodic.jsonl` |
| 上一份（修复前）运行记录 | `artifacts/evolution_real_20260916_stale/`、`reports/evolution_real/` |
