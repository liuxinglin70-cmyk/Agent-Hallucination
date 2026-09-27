# 自进化结果分析报告：三轮运行、0 准入

**日期**：2026-09-27
**运行**：`full693-20260924T143024Z` / work_root `work/evolution`，3 cycle，`mini` 划分（开发池 30、validation 10）
**原始证据**：`work/evolution/reports/experiment.json`、`work/evolution/ledger.jsonl`（37 条，校验有效）、`work/evolution/cycles/cycle00{1,2,3}.json`、`work/evolution/development/`、`work/evolution/memory/episodic.jsonl`
**权威叙述**：本报告是分析性派生文，与 [实验日志](experiment-log.md) §34.5 同源。两者不一致时以日志与原始文件为准。

---

## 1. 终态：一次结构完整、结果为零的运行

| 项 | 值 | 判读 |
|---|---|---|
| cycles | 请求 3、完成 3 | 没有提前中断 |
| `stopped_early` / `stop_reason` | `false` / `""` | 框架自身认为跑完了 |
| ledger | 校验有效，37 条，`broken_at=null` | 哈希链完整 |
| **`active_registry`** | **空**（`interventions: []`） | **0 准入** |
| validation 迷你评估 | n=10，`passed=false` | 边际 0、新错误 0、未决 0 |
| 配对翻转 | `F->F 5 / T->T 5` | **完全无变化** |
| g1 批次 | 未启动 | 无 registry 变化，按计划正确跳过 |

**这个运行在工程意义上是成功的**（框架跑完整闭环、账目自洽、拒绝理由可复现），**在科学意义上是零结果**（没有任何干预被采纳，因此没有"自进化是否有效"的证据可言）。这两句话必须同时说，否则容易把"跑通了"读成"有效果"。

---

## 2. 逐轮记录

### Cycle 1

| 候选 | 类型 / hook | 结构门 | 无害门 | 行为门 | 终态 |
|---|---|---|---|---|---|
| `IV-1-D001` | `recall_trigger` @ `recall.trigger` | 过（jaccard 0.0846） | 过（n=4，0 损伤） | **否**：`never fired on S union G` + 1 例不可测（`Magentic_One/049`） | `not_evaluated` |
| `IV-1-D002` | `text_boundary` @ `upgrade.system` | 过（jaccard 0.1085） | 过（n=4） | **否**：`target polarity mismatch`（声称 recall，实际 `T->F 1`） | `rejected` |

开发阶段（L0/L1）先行过滤：`M-17084a0707a4` v001 因 `Camel/048` + `Magentic_One/050` 邻居退化与 2 个新错误被 L1 拒；修订版 v002 通过并冻结。`M-dcd34940c9d8` v001 一次通过。

### Cycle 2

| 候选 | 结构门 | 无害门 | 行为门 | 终态 |
|---|---|---|---|---|
| `IV-2-D001` | 过（0.0861） | 过 | **否**：`never fired` + `049` 不可测 | `not_evaluated` |
| `IV-2-D002` | 过（0.1458） | 过 | **否**：`runtime health regressed on 1 case`（`T->U 1`）+ `049` 不可测 | `not_evaluated` |

开发阶段再次过滤：`M-17084a0707a4` v001 这次连目标都没改进（`no target case improved on the development replay`）；v002 通过。`M-dcd34940c9d8` v001 通过。

### Cycle 3

| 机制 | 路由 | 状态 | 停止 |
|---|---|---|---|
| `M-17084a0707a4` | `recall_trigger` | `discarded` | `no_candidates`（LLM 调用成功，32,857 token，未产出合法候选） |
| `M-dcd34940c9d8` | `evolved_predicate` | `discarded` | `no_candidates` + `budget_exhausted`（其 v001 因 `Camel/048` 退化在 L1 被拒） |

`empty_proposal_reason`：`stable failure families exist but the proposer drafted no valid candidate`。

**三轮汇总**：7 个开发版本，3 个 `failed_l1`，4 个 `ready_for_freeze`（C1: 2、C2: 2、C3: 0）。冻结的 4 个版本全部在正式晋升的行为门被否。

---

## 3. 六个结构性发现（区别于"模型不行"的归因）

以下每条都指向**框架设计**，而不是模型能力。

### 3.1 `Magentic_One/049`：一个测不出来的样本挡住了所有候选

它是干净样本（gold=false，10 步），但**每一轮测量都失败**（`authority=degraded_judge_only`）。它同时是三处集合的成员：sentinel/gain 评估集、`gain_opportunity` 的 precision 收益点、以及唯一的不可测行。

后果：C1/C2 的 D001 候选在 `never fired` 之外还附加"1 例无法诚实测量，拒绝不可归因于候选"——**候选不是因为它自己不好被否的**。C1 的 D002 分到了另一份 12 例配对（那次 `049` 是权威 `verified_clean`），才拿到可读的 delta。

这是**测量能力问题伪装成候选质量问题**的典型形态。在修复它之前，后续任何 cycle 都会重复同一失败形态。

### 3.2 起草集与评估集不相交：2 例 → 12 例的泛化鸿沟

候选谓词从 patrol 失败族的**成员案例**起草（D001 的取证来自 `BFCL/062` + `OpenDeepSearch/086`），而行为门要求它在 **S ∪ G 的 12 例**上触发。两个集合按划分设计**完全不相交**（pool 30 = sentinel 6 + gain 6 + patrol 18）。

不把评估集喂给提案器是**正确的防泄漏设计**，但结构后果是：**候选必须把从 2 例拟合的触发条件泛化到 12 例未见案例**，否则 `fired_rate=0` 直接否决。这正是 C1/C2 的 D001 的形态。

**修法是扩大并多样化起草证据，不是暴露评估集。**

### 3.3 收益点异质：6 例跨 4 类 + 2 干净样本

| gain 案例 | gold | 类别 |
|---|---|---|
| `OpenDeepSearch/088` | true | Planning / Fact Derive |
| `Magentic_One/049` | **false** | — |
| `OpenManus/064` | true | Human-Interaction |
| `Octotools/030` | true | Reasoning |
| `OpenDeepSearch/002` | **false** | — |
| `OpenDeepSearch/063` | true | Retrieval / Summarize Misalign |

要求**单个**谓词在这 6 例上同时产生正向边际，等于要求一个谓词覆盖 4 类失败 + 2 个干净样本。这与失败族"按机制聚类"的粒度**不匹配**。`gain_opportunity` 把不同族的机会合并成一个考场，是独立于 3.2 的第二个错配。

### 3.4 12 例面上的门控带宽过低

门控同时要求：结构 jaccard ≤ 0.72、无害门 0 新 FN / 0 新 FP / 0 新未决 + 保护下限 2 TP + 2 TN、行为门 `require_fired`、边际门 ≥1 边际案例且 0 新错误。可用评估面只有 12 例（patrol 18 例不参与门控）。

在 n=12 上要求"≥1 边际改善且 0 新错误"，等于要求候选在小样本上做到零噪声命中。**C1 的 D002 达成 `correctness_improved 1 / correctness_worsened 0 / new_errors 0 / decisive_fixed 1`，仍被 polarity 判据否决**——因为它声称 recall 却只产生了一个 `T->F`（precision 方向）的修正。

判据本身**自洽**（防止"改口径冒充收益"），但结果表明：在 12 例面上，**一个真做了正确修正的候选也可能因方向声明不符而被拒**。这是"判据正确但带宽过窄"的组合。

### 3.5 `Camel/048` 作为事实上的否决器

三个 cycle 共 3 次记为邻居退化（C1 与 `Magentic_One/050` 并列、C2、C3）。它是**两个机制的 8 例共同邻居集成员**（每轮同一批）。6 个机制-轮次组合中有 3 次的 v001 因它被拒。

`require_normal_neighbors=2` 的设计意图合理（防止候选损伤正常案例），但若 `Camel/048` 本身处于判定边界，它就充当了**一票否决器**。需要先做边界诊断，再决定它是否应继续留在邻居保护集里。

### 3.6 三轮是同一次搜索的重复，且定位方向从未被开发

**失败族逐字相同**：C1/C2/C3 的 `stable_families` 是同一批 4 个族（2 detection + 2 localization）。
**patrol 案例逐字相同**：三轮 18 例完全一致。
**status 逐字相同**：三轮 `gain_opportunity` 都是 `{recall:1, precision:1}`。
**检测器行为无变化**：registry 三轮为空；`harness` digest 变化（`17f69102d5a3` → `bef3d26500a5` → `6003bc56907e`）只是 M/C 组件版本记录，不影响判定。

因此**三轮不是三次独立探索，而是同一状态上重复了三次同样的搜索**，得到同样的族、同样的候选方向、同样的拒绝。

**为什么拒绝记忆没能改变下一轮（精确机制）**：`memory/episodic.jsonl` 确实落盘了 7 条拒绝证据，抑制路径也确实接线（`engine.py:3648` 在起草前调用 `set_rejection_feedback`）。但 `proposer.py::_proposal_fingerprint`（988 行）对候选按下述内容做指纹：结构化谓词键、或 `text_boundary` 的 **hook + packet_features + directive 全文**。于是：

- **3 条拒绝被标为 `not_evaluable=true`**（C1-D001、C2-D001、C2-D002），`set_rejection_feedback` 的过滤器**故意不把它们计入 `rejected_keys`**——设计意图正确（"没测成"不等于"被证伪"），但后果是：只要 `Magentic_One/049` 的测量阻塞存在，同一机制就可以永远重试。本轮 4 条拒绝里 3 条走的是这条路。
- **唯一 1 条真证伪（C1-D002）确实进了 `rejected_keys`**，但指纹含 directive 全文；C2 的 LLM 起草了**措辞不同的新 directive**，指纹不匹配 → 逃过抑制，得到同类拒绝（polarity mismatch）。

所以三轮重复的根因不是"记忆没接线"，而是**`not_evaluable` 占主导 + 指纹按文本全文匹配（改写即逃逸）**。前者把可重试面撑大到几乎所有拒绝，后者让唯一的真证伪也拦不住改写版。修法：对"因测量阻塞而不可评估"的机制设置**重试次数上限或阻塞标记**（而非无限重试），并考虑对同类极性失配引入更粗粒度的机制级抑制（而不是全文指纹）。

**定位方向被连续跳过 6 次**：patrol 每轮同时产出 2 个 detection 族与 2 个 localization 族，但 `_all_families` 先返回 detection、后返回 localization（`engine.py:2991`），而 `max_mechanisms_per_cycle=2` 按顺序取前两个，于是两个 localization 族在三个 cycle 里**连续 6 次被同一理由跳过**（`max_mechanisms_per_cycle=2 was already reached by earlier mechanisms this cycle`）。

这不是随机遗漏，而是**固定排序 + 上限**的结构效应。考虑到基线数据显示定位是最大短板（责任步精确匹配 0.2721，Human-Interaction 仅 0.0423），**检测器这一维度的改进通道实际处于关闭状态**。

### 3.7 Cycle 3 的预算耗尽（修正：不是"从未尝试"）

C3 的 `llm_calls` 预算为 3 次/cycle，实际消耗：第一个机制 1 次（调用成功但无合法候选），第二个机制 2 次（产出 v001，L1 被拒）。预算恰好用尽后，第二个机制的进一步起草被拒，记录为 `provider_called=false` + `reason: "llm_calls budget exhausted for this cycle"`。

准确的表述是：**C3 的 0 候选是预算在尝试之前耗尽**——第二个机制只跑了 1 个版本（对比 C1/C2 都产出了 v001+v002），没有获得修复机会。

注意 `no_candidates` 的字面含义容易与"模型尝试后失败"混淆；读日志须结合 `stopped_note`。

---

## 4. 这一轮证明了什么、没证明什么

### 证明了

1. **L0/L1 开发循环接线成功且真实生效**。7 个版本中 3 个在 L1 被拒，这些候选**没有**进入昂贵的双臂晋升测量。对比 §30 记录的"毛坯候选直接冲门、62 分钟实测后被拒"，门前过滤确实工作。
2. **四门判据可执行，且拒绝理由可归因**。每条 rejection 都带 delta 与具体案例名。
3. **失败机制聚类稳定**。三轮都识别出同样的 2+2 族。
4. **测量完整性防线有效**。不可测行未被视为"已证伪"，而是显式标 `not_evaluated` 并注明"拒绝不可归因于候选"。
5. **缺陷注入审计两次都按预期拦截**：结构缺陷被 STRUCTURAL 拦（case id 泄漏）、极性缺陷被 BEHAVIOR 拦。

### 没有证明

1. **没有任何证据表明自进化能提升检测能力**——0 准入，registry 为空，g1 未启动，validation 10 例翻转完全为 0。
2. **候选生成质量未达标**——两个走到行为门的候选均未通过；C3 连候选都没产出。
3. **registry 长期治理能力未被检验**——没有准入，`net_contribution_history`、快照链、漂移监控全部空跑。
4. **定位方向的改进能力完全未检验**——两个 localization 族被连续跳过（3.6）。

---

## 5. 修复建议（按证据强度排序）

| 优先级 | 动作 | 依据 |
|---|---|---|
| **P0** | 处理 `Magentic_One/049`：配置重试或放宽其单例预算，或将其从 gain 机会点移出并记录原因 | 3.1，唯一同时污染可测性与评估集的案例；它直接制造了 3 条 `not_evaluable` |
| **P0** | 给"因测量阻塞不可评估"的机制设**重试上限或阻塞标记**，替代当前的无条件重试 | 3.6，3/4 拒绝为 `not_evaluable`，导致同一机制可无限重试 |
| **P0** | 放宽 `max_mechanisms_per_cycle` 或让 detection/localization 族**交替优先**，确保定位方向至少被开发一次 | 3.6，定位是最大短板却 6 次被跳过 |
| **P1** | 抑制键从"文本全文指纹"改为**机制级/极性级**更粗粒度 | 3.6，改写措辞即逃逸，C1-D002 的抑制实际未拦住 C2 同类候选 |
| **P1** | 扩大并多样化起草证据（更多同族案例），而非暴露评估集 | 3.2，2 例→12 例的泛化鸿沟 |
| **P1** | 让 gain 机会点**按失败族**筛选，而非固定 6 例混合 4 类 | 3.3，考场与靶子粒度不匹配 |
| **P2** | 重新标定 12 例面门控（或扩大 gain 面）；报告中区分"候选被否"与"候选未被测成" | 3.4，真实修正被判据否决 |
| **P2** | 给 `Camel/048` 做边界诊断，区分"边界样本"与"正常邻居" | 3.5，事实上的否决器 |
| **P3** | C3 预算不足时明确记录"未尝试"，避免与 `no_candidates` 混淆 | 3.7 |
| **P3** | `memory.py:388-389` 的 `falsified_intervention_ids` / `unevaluable_intervention_ids` 已计算但**无生产消费方**（仅自身模块与测试引用）；要么接入抑制决策，要么按 §21.3 的标准删除 | 与优化方案已记录的"无调用方"问题同类 |

---

## 6. 成本

自进化阶段的 token 成本被分散记录在 `checkpoints/`、`cache/`、`cycles/`、`reports/` 四处，且每轮的 checkpoint 会复制上一轮产物，**逐文件求和会重复计数**（原始求和 78,007,747，包含重复）。可确认的单点读数只有 proposer 侧：C3 第一次调用 32,857 token（其中 reasoning 23,516）。**要给出可信的自进化总成本，需要先把 checkpoint 复制去重或改为增量记录**——这本身是一项待办。

对照而言，测量阶段（653 例）的成本是明确的：报告口径 41,050,704 token（下界），含 8 轮重试累计约 53.5M。**自进化 3 轮的开销远小于测量，但这只是量级判断，不是精确账目。**
