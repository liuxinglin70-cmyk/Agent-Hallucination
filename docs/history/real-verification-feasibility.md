# 真实验证可行性评估：检测器底座与自进化框架

> **历史文档（2026-09-17）。** 其中「没有独立终审集」的阻断项仍然成立，但代码基线与测试数量已过时；当前口径见 [技术方案](../technical-solution.md) 第 9 节。保留原因见 [历史文档索引](README.md)。

评估日期：2026-09-17。范围：`detector/`、`evolution/` 全部源码、`datasets/`、`reports/` 与 `artifacts/` 中的真实运行记录。
方法：通读源码与文档 + 本机实测（真实 provider 调用、离线回归、划分复算、成本复算）。下文标「实测」的条目均由本次实际运行确认。

本文件回答一个问题：**现在能不能对这套系统做「真实验证」，以及哪些结论能做、哪些做不了。**

---

## 0. 结论

**技术上可以跑，科学上现在做不了「真实验证」。**

三句话说清：

1. **基础设施是通的。** 真实服务实测可用（`GLM-5.3-flash`，2.85s 返回）；离线 312 tests passed、`scripts/verify.py` 9 项校验全 PASS；检测器与进化的真实入口都能加载并构造（实测）。
2. **但「真实验证」的三种含义要分开。** 路径连通性验证 **可以且已经做过**；开发集上的诊断读数 **可以但只是开发集读数**；泛化/独立性验证与「自进化收益」验证 **现在做不了**——不是因为代码跑不起来，而是因为**没有独立数据**（693/693 全部在暴露清单内）和**评估没有统计功效**（边际集 G 只有 4 例、失败族只有 2 例）。
3. **而且历史真实结果已经失效。** 2026-09-16 的真实运行记录（live/real/evolution_real）产生于修复之前的代码，之后有 13 项会改变语义的修复，且边际集抽样因新增极性下限而改变（实测：G 已从记录的 `Camel/048, OpenDeepSearch/002, Magentic_One/093, Octotools/005` 变为 `OpenDeepSearch/080, Octotools/005, Magentic_One/050, OpenDeepSearch/002`）。**当前代码上没有任何一份有效的真实结果**，要读数必须新跑。

因此：可以做 **开发集上的端到端真实运行**（用于验证机制闭环与诊断读数），不能做 **可声称泛化的真实验证**。若目标是后者，必须先补独立终审数据与统计功效，这属于实验设计问题，不是代码缺陷。

---

## 1. 检测器底座：现状梳理

### 1.1 阶段链与编排

规范阶段顺序在 `evolution/harness.py:47-56` 显式声明，并由入口交叉校验（`harness.py:92`）：

```text
validate → packet → judge → recall → upgrade → tracer → defense → adjudicate
```

公开入口 `Detector.detect` / `_detect`（`detector/api.py:267/297`）。执行逻辑：

- `validate_record` → `build_packet` → `compile_runtime_plan`（`api.py:306-331`）。加载 fail-closed：任何无执行器的 hook/type/runner 在首次 LLM 调用前抛错（`api.py:280-286`）。
- **Judge** 读完整轨迹，输出 `is_hallucination`（`api.py:350-354`），跑 `judge.post` 机械证据钩子。
- **召回触发**：只有 Judge `state=="ok"`、判阴性、且 `plan.recall_triggered(...)` 才强制进入 Scanner（`api.py:413-421`）。一致高置信阴性直接结束（`recall_scanner.state="not_needed"`）。
- **听证队列**：Judge 阳性先入队（`api.py:488-489`）；每场听证独立跑 Tracer + Defense（`run_hearing`，`api.py:523-550`）。Judge 阳性被 `refuted` 驳回后转召回网。
- **确定性裁决**：`adjudicate(...)`（`api.py:646-663`）汇合听证、机械证据、DSL 定罪。
- **`proof`** 汇总各阶段 parsed、听证、证据、部署触发记录（`api.py:691-731`）。

**唯一的模型调用点**是 `detector/provider.py::provider_call`（`provider.py:239`），用标准库 `urllib` 直连 OpenAI 兼容 `/chat/completions`。Windows 下由 `provider_worker.py` 子进程执行单次 HTTP 以强制 kill 超时（`provider_worker.py:1-3`）。

### 1.2 判断语义与输出

`RESULT_SCHEMA = "detector.detection_result.v1"`（`detector/result.py:7`）。三个终态（`result.py:8`）：

| state | 含义 | authority |
|---|---|---|
| `verified_error` | 有有效证据的定罪 | 正常 `authoritative`；`substantiated_upgraded`/未决 → `degraded_judge_only` |
| `verified_clean` | 无阳性且召回正常 | 召回不可用 → `degraded_judge_only` |
| `unresolved` | Judge 失败/quorum 不足 | `invalid`，`runtime_valid=False` |

下游 `prediction_state` 默认要求 `decision_authority=="authoritative"` 且 `runtime_valid is True`（`result.py:54-72`）——即**降级判断与未决都不算数**。

几处决定结论可信度的设计：

- **阳性是「extraordinary claim」**：多票严格多数才判阳，平票/无 quorum 保持阴性并降级（`judge.py:204-212, 307-331`）。Judge 失败产生 unresolved 而非阴性。
- **召回只提候选不定罪**（`recaller.py:91`）；**因果升级**要求候选同时「内容错误 + 结果因果」，缺行报 degraded，绝不静默当作 `content_error=false`（`recaller.py:167-175, 457-462`）。
- **辩护只有可证明的 `refuted` 才能翻案**（`adjudication.py:5-7`）；无多数统一落 `inconclusive`（降级不翻案，`defense.py:101-124`）。
- **裁决确定性**：多 guard 动作冲突则全部禁用、回退底座（`adjudication.py:182-184`）。
- **评估口径**：unresolved 计为错（`detector/evaluation.py:47-57`）。

### 1.3 可修改面（这是自进化动作空间的边界）

干预是唯一注入通道，四类闭集（`detector/interventions.py:43-45`）：

| 类型 | 作用 | 绑定 hook |
|---|---|---|
| `text_boundary` | 追加带适用/排除边界的提示文本 | 5 个 `SYSTEM_HOOKS` |
| `evidence_check` | 执行内置或 typed DSL 谓词，追加机械证据/事实 | `judge.post`/`upgrade.pre`/`defense.pre` |
| `decision_guard` | 依事实执行受限裁决动作 | `adjudicate.guard` |
| `recall_trigger` | Judge 阴性时触发已有召回网 | `recall.trigger` |

Hook 共 10 个（`interventions.py:47-58`）；guard 动作闭集 4 个（`interventions.py:68-73`）；内置谓词 6 个（`interventions.py:89-106`）；DSL 上限 `MAX_RULES=16`、`MAX_VERDICTS=6`、`MAX_LITERAL_CHARS=400`、`MAX_REGEX_CHARS=300`、`MAX_CONDITIONS=4`（`predicate_dsl.py:39-43`）。

即：**能扩展「观测词汇与控制流策略」，不能创造新 hook、新执行器、新图结构或权重。** 这一点由代码自己机器可读地声明（`evolution/capability_surface.py:47,95,99-101`，并在 `reports/latest/capability_audit.json` 中实测确认）：

```json
"open_ended_self_evolution": false,
"new_hook": false,
"execution_graph_rewrite": false,
"sandboxed_code_generation": false,
"model_weight_update": false
```

---

## 2. 自进化框架：现状梳理

### 2.1 一轮 cycle 的顺序（`evolution/engine.py:1-19`）

```text
defect-injection 审计 → patrol(H_t 已部署版本 + 裸底座参照) → 稳定失败族诊断
→ propose → 配对评估 + 四门 → 组合臂选择 → registry 预提交写 + 账本
→ record_harness(H_t 清单与代际差异)
```

- **patrol 双臂**：巡检臂 = 已部署版本，参照臂 = 裸底座；registry 为空时两臂是同一次评估（冷启动成本不变）。失败分为 `new_failures` / `persisting_failures` / `resolved_by_active`（`engine.py:129-169`）。
- **诊断**：机械签名 → 7 条治疗路由 → 类型化样本；不可执行的失败族归为 `capability_gap`（`worksheet` + `engine.py:258-284`）。
- **propose**：`HybridProposer` = LLM 草拟 + 受约束模板，历史拒绝反馈抑制原样重提（`architecture.md:43`）。
- **四门**：任一失败即 terminal，不搜索修复（准入是终结式的）。

### 2.2 四门与当前阈值（`evolution/configs/development_real_run.json`）

| 门 | 判据 | 阈值 |
|---|---|---|
| STRUCTURAL | 合同/执行器校验、禁案例 ID 泄漏、`failure_cases>=2`、族与极性一致、机械定罪一致命中、`upgrade.system` 可达性、去重 | 文本 Jaccard `0.72` |
| HARMLESSNESS | sentinel 配对；protected = 基线 authoritative 且判对的 TP/TN | 新增 FN/FP/unresolved 各 `0`；保护下限 `2 TP + 2 TN` |
| BEHAVIOR | 干预实际触发且行为与声明一致 | `require_fired=true`（fired_rate>0） |
| M（marginal） | gain 集净增正确案例、新错误 | `min_marginal_cases=1`，`new_errors_allowed=0` |

`m` 是 marginal 的数学简称，不是版本号（`architecture.md:51`）。

### 2.3 评估覆盖：当前最脆弱的一环

`marginal_cases = active_correct - base_correct`（`evolution/metrics.py:205`），**只统计「全对案例净增」**。因此某方向的 claim 只有在控制臂于该方向**判错**的案例上才可能得分——这是**仪器属性**（G 的构成 + 底座行为），不是候选属性。

真实第 1 轮的典型失效正是如此：`IV-1-002` 是一条提升召回的候选，行为门通过、在 sentinel 修好 1 个漏报，但原 G 集是 `2 TP + 1 TN + 1 FP`、**完全没有 FN**，于是 `marginal_cases=0` 被读出并拒绝（`reports/evolution_real/README.md:66-68`）。

已做的两层补救：

1. **极性下限**（`splits.py:426-434`）：mini 协议每极性至少 1 例；正式协议 `max(1, gain_size//4)`；不满足直接 `ValueError` 而非静默弱化（`splits.py:110-122`）。**实测**：当前 mini G = `{error: 2, clean: 2}`。注意这只是**必要不充分**——G 里有 error 案例 ≠ 该案例是召回机会。
2. **覆盖可读 + 终态可辨 + 响应策略**：
   - `instrument` 块给出 `gain_opportunity`（每极性可修复案例数）、`unmeasurable_polarities`、`terminal_classification`（`admitted`/`rejected`/`not_evaluated`/`unresolved`）、`terminal_counts`、`instrument_limited`（`engine.py:172-252, 579-642`）。
   - **「未曾被测量」不再等于「已被证伪」**（`architecture.md:63`）。
   - `coverage_response` 三模式：`strict`（默认，记未测量并阻断，与改动前逐字一致）/ `replacement`（在替代评估集上重新测量整个判定单元）/ `replacement_or_skip`（无匹配集则跳过）（`engine.py:255, 517-577`）。替代集**只驻留内存**，未安装时如实报 `none_installed`。

**这一项对可行性的影响**：默认 `strict` 下，G 无机会的正确候选仍会被读成「0 收益」并拒绝。所以现在跑真实进化，仍可能产出「做出了有效修改但读数为 0」的伪结论——除非显式开启替代评估集（但替代集尚未持久化，需在进程内安装）。

### 2.4 H_t = (G, C, T, M, R) 基座

`evolution/harness.py` 把每代做成可装载、双层哈希可核验的清单：G=工作流阶段与实际入口点；C=packet 视图预算与渲染器实测值；T=活跃 registry；M=记忆三存储统计；R=配置中的 patrol/budget/gates/library/split/proposer。

两条性质比完整性更重要：**组件是推导而非断言**（阶段入口被重命名、上下文预算消失都会使构建失败）；**事后可核验**（装载时重新推导摘要，被编辑过的清单会被发现）。清单与差异写入 cycle 报告与账本事件 `harness`，追加到 `harness_history.jsonl`。**清单与缺口不参与任何门判定**（`harness.py:28`）。

### 2.5 缺口与三类经验

- `evolution/gaps.py`：不可执行的稳定失败族落为去重后备清单，身份 `(failure_family, classification)`（剥掉 worksheet 的 `-P<hash>` 画像后缀，`gaps.py:37-40`），保留 `reason`/`routes`/`occurrences`；`resolve_gap` 记为历史而非删除。
- `evolution/memory.py`：**情景**（某轮尝试与各门结论）、**程序性**（只有通过门控的路线才写入，`memory.py:18`）、**改进**（哪类瓶颈对哪类补丁有反应）三类存储，检索按机制过滤并设上限；显式区分 `falsified_intervention_ids` 与 `unevaluable_intervention_ids`。
- 已接入草拟：`_proposal_memory_context` → `set_memory_context` → proposer 提示，账本记 `proposal_memory`/`proposal_memory_used`。**「被供给」已由测试证明，「是否改善草拟质量」尚未证明**（需带/不带记忆的对照）。

### 2.6 断点续跑

`CycleJournal` 以 `(arm, case)` 持久化逐例观察，续跑不重抽已完成观察。契约**不含 registry 哈希**，改为记录本轮**起始** registry；续跑若发现 live registry 已含本轮自己的准入，先 `ActiveRegistry.restore` 回退再评估（否则门控会拿候选与自己比较），动作记 `resume_registry_restored`。提案未落盘就中断仍拒绝重抽（有意，错误信息指向换新 `work_root`）。

---

## 3. 已有的真实运行证据与它们的边界

### 3.1 离线（当前源码，本次实测）

- `python -m pytest -q` → **312 passed**（24.2s）。
- `python scripts/verify.py` → **9 项校验全 PASS**（`dataset_integrity`、`pytest`、`detector_cli`、`evolution_cli`、`capability_audit`、`smoke`、`smoke_admission_and_audit`、`capability_smoke`、`source_unchanged_during_checks`）。
- 原始数据 695 文件 / 693 案例完整性通过（BFCL 164、Camel 93、Magentic_One 94、Octotools 47、OpenDeepSearch 100、OpenManus 104、SmolAgents 91）。
- 边界：**mock 后端只做机械回归，不是真实模型能力**。

> 说明：本次执行 `verify.py` 会按设计重新生成 `reports/latest/`。已核对 `git diff`——只有时间戳与耗时字段变化，无实质内容改动。

### 3.2 真实服务联调（`reports/live/`，2026-09-16）

2 个合成算术案例（非原始 693 案例），真实 `GLM-5.3-flash`：decisive=2、**unresolved=0**、accuracy=1.0、**authoritative=2**、degraded=0、步骤定位 1/1。clean 例只跑 Judge；error 例 Judge → Tracer 返回 `provider_error` → Defense 维持定罪。

边界（报告自述）：**未触发 Recall 与 Causal Upgrade**，这两阶段的真实路径不能由这两例确认；"2/2 不是数据集准确率，更不是自进化收益"。

### 3.3 真实数据集小规模检查（`reports/real/`，2026-09-16）

6 例 × 2 重复，真实 `GLM-5.3-flash`（judge/defense votes=3、topup=2）：

- 两次均：decisive=6、**unresolved=0**、decisive_accuracy=1.0、tp=3/fp=0/fn=0/tn=3、**authoritative=6**、degraded=0。
- **终态 6/6 一致；计入定位步则 5/6**（OpenDeepSearch/078 两次定位到第 4 步 vs 第 1 步）。
- 定位命中率：两个正例批次分别 0/3、1/3，均值约 **1/6**。
- 成本：202,426 token / 12 次，均值 16,868.8 token/案例；单例 32.3–379.6s（均值 109.6s）。
- 另以**单票强制**触发召回分支（3 例）：tp=0/fp=1/fn=0/tn=2，BFCL/012 被判 `verified_error`（FP）。

边界：案例来自**已被开发使用的复测集**，**不能解读为准确率**；6 例 0 unresolved 不代表大规模稳定性；召回分支是**构造条件**，不代表正常触发率。

### 3.4 真实两轮自进化（`reports/evolution_real/`，2026-09-16）

**第 1 轮完整跑通，结论是收益不成立、准入 0：**

| 阶段 | 实测结果 |
|---|---|
| 审计（缺陷注入） | 通过；`IV-DEFECT-001` 因 directive 泄漏案例 ID 被结构门拦下；`IV-DEFECT-002` 被 M 门拒（`marginal_cases=0<1`） |
| patrol（裸底座 20 例） | 标准口径 **4 FN / 1 FP / 3 U**（原记录「5 FN、3 FP」混入了未决） |
| 稳定失败族 | **1 个**，2 例同机制（BFCL/017、OpenDeepSearch/086），actionable |
| 提案（真实 GLM） | 3 个候选：2×`recall_trigger` + 1×`text_boundary(upgrade.system)` |
| 四门 | **3/3 全部拒绝**：2 个 BEHAVIOR 失败（`recall_triggered_cases=0` / 方向不符），1 个 M 失败（`marginal_cases=0<1`） |
| 准入 | **0**；`active_after_cycle=[]`；registry 仍为空（`content_hash=1a4fe1d7…`，无静默污染） |
| 账本 | valid，哈希链完整 |

**第 2 轮**：audit（cache hit）→ patrol（与第 1 轮相同 20 例）→ proposed（先给错 `failure_family` 键被拒，repair 后产出 3 个合法候选），随后**进程中断**。`artifacts/evolution_real_stdout.log` **为 0 字节**，无 traceback、无退出码，**中断原因未证实**；且该轮账本只保留候选 ID 与 notes、**没有完整候选正文**，无法精确恢复。

**成本（本次复算）**：cycle 1 的 40 个缓存结果中记录了 **133 次调用 / 948,380 token**；proposer 3 次调用 / 42,589 token；**第 1 轮墙钟约 90 分钟**。gate 的 fresh 重跑不写入缓存，**实际消耗高于此数**。

边界：失败族正例只有 2 个、patrol 仅 20 例、边际评估 `paired_n=4`，`marginal_cases=0` 的置信度有限；0 准入是**门限拒绝**的结果，不是没有产出候选。

### 3.5 关键：这些记录在修复后的代码上已失效

2026-09-17 修复了 13 项会污染结论的缺陷（`docs/review.md` 第 8 节），其中多项**直接改变真实运行的终态**：

- B1：`refuted` 且 recall 未完成时，从「authoritative clean」改为 `degraded_judge_only`。
- A2：DSL `default_verdict` 不得为定罪 verdict（原可零命中即机械定罪）。
- A3：辩护无多数统一落 `inconclusive`（原按采样顺序取首票，同票集可得到相反终态）。
- A4：新增 `required_quorum`，低票不再算「严格多数」。
- A9/A10：`upgrade.pre` 证据钩子由每案两次改为一次；冻结听证只比较投影的 7 字段（原「冻结」实为实时重跑）。

同时**划分本身变了**（实测）：新增极性下限后，mini G 由记录的 `Camel/048, OpenDeepSearch/002, Magentic_One/093, Octotools/005` 变为 `OpenDeepSearch/080, Octotools/005, Magentic_One/050, OpenDeepSearch/002`。

结论：**§3.2–3.4 的数字是修复前代码 + 修复前划分的读数，当前代码上没有任何一份有效的真实结果。**

---

## 4. 可行性判断（分维度）

| 维度 | 能否真实验证 | 依据与限制 |
|---|---|---|
| **服务连通性 / 端到端路径** | ✅ **可以，且已做** | 实测真实 provider 返回正常（2.85s）；`reports/live`、`reports/real` 已证明 batch→evaluate 链路通，含 0 unresolved 与全部 authoritative |
| **检测器在开发集上的诊断读数** | ✅ 可以 | 案例来自开发复测集，读数有效但**只是开发集读数**；含 unresolved/degraded 率可测 |
| **自进化闭环可端到端跑通** | ✅ 可以 | cycle 1 已完整跑通（审计→patrol→诊断→提案→四门→账本）；断点续跑已修复 |
| **自进化「收益」** | ⚠️ **能测，但当前功效不足** | G=4 例、max_new_errors=0、失败族=2 例；provider 采样波动与效应量同阶（报告自述「不能排除候选臂的 provider 波动」）。只能得出「本轮在 G 上是否计分」 |
| **「未测量」不被读成「无收益」** | ❌ 默认做不到 | `coverage_response=strict` 下无机会方向仍阻断准入；替代集未持久化 |
| **泛化 / 独立性** | ❌ **做不到** | 实测：693 个案例**全部**在 `datasets/protocols/development_exposure.json` 内；**不存在任何正式 `final` manifest**；`final` 与 `full-self-evolution` 无 manifest 时直接拒绝 |
| **累积自改进 / RSI** | ❌ 做不到 | `open_ended_self_evolution=false`；动作空间是封闭 DSL 干预搜索，框架自身只由人手优化 |
| **复现既有真实结果** | ❌ 做不到 | 划分变更 + 语义修复，旧读数已失效 |

---

## 5. 阻断「真实验证」的具体项

按可操作性排序：

1. **没有独立终审集（最硬的阻断）。** 693/693 案例都在暴露清单内，保守策略认定原始语料不能认证为新的独立终审范围；删缓存或换 case ID 都不消除此边界。要谈泛化，必须有外部新数据或人工标注的独立集，并按 `docs/evaluation.md:27` 冻结协议（`evolution_pool.cases` + `final_verification.records`，含唯一性、无交集、read-once 校验）。
2. **统计功效不足。** G=4（极性下限仅保证「每极性至少 1 例」）、失败族=2 例、`m_min_marginal_cases=1`。这不是配置调参问题——放大也受限于开发池只有 30 例。可行方向是改用配对统计与置信区间，而不是硬阈值 1。
3. **评估仪器仍会把「未测量」读成「0 收益」。** `instrument` 块已让这一点**可读**，但默认 `strict` 下不改变准入。要在真实运行中受益，需显式开启 `replacement`/`replacement_or_skip` 并先解决替代评估集的持久化与暴露保证。
4. **历史结果失效，必须重跑。** 修复 + 划分变更后，`reports/evolution_real` 的 0 准入不能作为当前代码的结论。
5. **成本与中断预算。** 单轮约 90 分钟、~95 万 token（且实际更高，fresh 重跑不入缓存）；第 2 轮曾以 0 字节 stdout 中断且原因未证实；patrol 20 例中曾出现 3 例 unresolved（15%），来源是超时与 provider 错误。真实运行必须用**新的 `work_root`/`registry_dir`/`ledger_path`**，且同一工作目录不能并发启动多个进化进程。
6. **检测器侧还有未度量的失效形态。** 定位一致性弱（5/6）与命中率低（约 1/6）已被记录，应作为「判定正确但定位漂移」的独立失效形态继续度量，不能被终态一致率掩盖。

---

## 6. 现在就能做的、最小而诚实的真实验证

### 层级 A：连通性与路径（分钟级，几乎零成本）

```bash
# 单例真实检测（会调用模型服务）
python -m detector detect --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json \
  --case-id BFCL/001.json --output-dir artifacts/detector/single

# 两例合成联调
python scripts/live_smoke.py
```

可声称：endpoint/凭据/模型/检测流程可接通。
不可声称：准确率、复杂轨迹表现、自进化收益。

### 层级 B：开发集诊断读数（小时级）

```bash
python scripts/real_path_check.py     # 6 例 ×2 重复，冻结配置与案例清单
```

可声称：当前代码在开发复测集上的诊断读数（含 unresolved/degraded/authority 分布、定位一致性、成本）。
不可声称：准确率、泛化、稳定性。

### 层级 C：自进化闭环可跑通（半天级，~1M token/轮）

```bash
# 必须先建新 work_root 的配置（勿复用 artifacts/evolution_real，其 cycle 2 已写 cycle_started）
python -m evolution experiment --mini --cycles 2 \
  --config evolution/configs/development_real_run.json
python -m evolution verify-ledger
```

可声称：真实 LLM proposer → 真实 detector 配对评估 → 四门 → 账本，全链路可跑通；本轮准入结论。
不可声称：收益、能力增长、泛化。默认 `strict` 下仍可能产出伪「0 收益」。

### 层级 D：收益验证（**前置条件未满足**）

需要先完成：独立终审集 + 功效设计 + 替代评估集持久化 + 在修复后代码上重跑。在此之前，任何「自进化有效/无效」都是开发集上的单轮观察，不构成结论。

### 明确不能宣称的

- 检测器准确率、F1、泛化能力（无独立集）。
- 自进化收益、能力增长、累积自改进（功效不足 + 封闭动作空间）。
- 「召回/升级阶段在正常配置下的触发率」（历史证据是单票强制构造）。
- 降级与未决不计入的「高准确率」（`prediction_state` 要求 authoritative，unresolved 计为错）。

---

## 7. 建议

**短期（可直接执行）**：跑层级 A + B，在**当前代码**上重新取得一份有效的开发集读数基线；随后跑层级 C 一轮，用途是验证机制闭环与「本轮可计分哪类机制」，而不是宣布收益。跑前在配置里显式记录 `coverage_response` 取值，避免事后无法区分「未测量」与「无收益」。

**中期（决定能否谈真实验证）**：解决独立数据。这是唯一能把「开发集诊断」升级为「真实验证」的路径，且无法靠代码或调参绕过。

**同时修功效**：把 `m_min_marginal_cases=1` 这类硬阈值换成配对统计与置信区间；扩大失败族与 G 的样本量受限于开发池，须与独立数据方案一并设计。

**措辞纪律**：目前的证据只支持「机制闭环可跑通」与「开发集诊断读数」两类陈述。在独立数据与功效问题解决之前，不要用「验证了」「证明了」描述自进化收益。

---

## 附：本次评估的实际执行记录

| 动作 | 结果 |
|---|---|
| 真实 provider 探活（`GLM-5.3-flash`，60s 超时） | HTTP 200，2.85s，返回 `OK`；usage 49 token（含 29 reasoning） |
| `python -m pytest -q` | 312 passed，0 failed，24.2s |
| `python scripts/verify.py` | 9 项校验全 PASS，`passed=true`；副作用：仅重写 `reports/latest/` 的时间戳与耗时字段（已核对 `git diff`） |
| 划分复算 `build_split(..., mini=True)` | pool 30、sentinel 6 `{error:3, clean:3}`、gain 4 `{error:2, clean:2}`、patrol 20、validation 10 |
| `LiveDetectorBackend(development_real_run)` 构造 | OK，无 provider 调用 |
| `development_exposure.json` | 693 案例，7 个来源，与整合语料逐一对应 |
| 正式 `final` manifest 检索 | 全仓库无 `final_verification` 记录 |
| cycle 1 成本复算 | 40 个缓存结果 / 133 次调用 / 948,380 token |

