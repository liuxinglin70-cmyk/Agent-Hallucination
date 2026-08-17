# detector_v2.1 优化路线：recall、最早步定位与整体检测器

状态：设计稿，未实施。
依据：`detector_v2/experiments/v2_pilot20_report.md` 的 20 例实验结果与逐错误输出。

---

## 0. 当前瓶颈的事实

20 例 pilot 中：

- 5 个 FN **全部是 Judge=false**，Defense 从未运行。Judge 单点决定 recall 上界。
- 旧检测器的扫描式 census 在同 20 例上 positive recall 88.9%（8/9 decisive + 1 unresolved），说明“扫描发现偏差”本身召回强，旧框架的问题是它把偏差直接当成定罪，没有因果/结果过滤器。
- 2 个 FP 中：
  - `BFCL/001`：Judge 与 Defense 都确认了“报告子串 vs 工具返回整行”的伪事实错误；
  - `BFCL/147`：Judge 定罪，但 Defense 的 ±2 证据窗口看不到后置 invoice，只能 inconclusive。
- step localization 2/10：Judge 的单次“找最早步”不稳，例如 BFCL/051 判 4（gold 3）、BFCL/081 判 8（gold 7）、BFCL/111 判 4（gold 7）。
- 几个 FN 的 Judge 输出显示：BFCL/006/024/025/158 的问题不是模型没看到偏差，而是**比较标准错了**（把“任务显式要求的 verbatim/exact-output 偏差”当成无差异）；Camel/038 是 Judge 的 world knowledge 错误。

结论：下一步不是加阈值，也不是继续在单一 Judge prompt 上堆规则，而是把“召回”和“定罪”两个职责拆开，并给 localization 单独一个阶段。

---

## 1. V2.1 架构

```text
{question, history}
      │
      ▼
[0] Contract Extractor       只提取“任务要求交付什么 + 显式形式/动作契约”
      │
      ▼
[1] Outcome Judge            二元判断 + confidence + evidence_refs + root_cause
      │
      ├─ true ─────────────────────────────────────┐
      │                                            ▼
      │                              [3] Step Tracer（最早因果步）
      │                                            │
      │                                            ▼
      │                              [4] Defense（按 evidence_refs 审查，可升级全视图）
      │
      └─ false 或 confidence=low ─────────────────────────┐
                                            ▼
                              [2] Recall Scanner（只扫描，不定罪）
                                            │ 候选偏差
                                            ▼
                              [2b] Causal Upgrade（修正该候选是否改变交付结果）
                                            │ yes ──► error（step=候选 locus）
                                            │ no  ──► clean
```

核心变化：**Judge 不再独自承担召回**。Judge 负责 precision 方向的主判决；recall 由独立的 Scanner + Causal Upgrade 兜底。

---

## 2. 各阶段设计

### 2.0 Contract Extractor（可并入 Judge，推荐独立小调用）

输入：question（+ 可选 history）。
输出：

```json
{
  "required_result": "task wants ...",
  "explicit_contracts": [
    {"kind": "verbatim_content|exact_form|exact_value|required_action",
     "description": "post exactly the diff content", "source": "question turn 2"}
  ],
  "presentation_freedom": "none|semantic|verbatim"
}
```

作用：把“格式差异不算幻觉”的规则改准确：

> 默认语义比较；**仅当任务显式固定了输出内容/形式/数值/动作**时，违反显式契约才算结果错误。

这直接针对 BFCL/006/024/025/158 这类“任务是 verbatim/exact-output，但 Judge 判为 clean”的 FN；它不是 BFCL 特判，是任何任务都存在的“任务契约”概念。为防止把 `write only "Guava"` 之类的风格要求过度定罪，契约项还必须通过 Causal Upgrade 的“是否改变交付结果”测试。

### 2.1 Outcome Judge（V2.1）

在现有 Judge 基础上增加三个字段，prompt 保持通用：

```json
{
  "is_hallucination": true,
  "hallucination_step": 6,
  "hallucination_reason": "...",
  "delivered_result": "...",
  "correct_result": "...",
  "basis": "observation|derivation|world_knowledge|task_requirement",
  "confidence": "high|medium|low",
  "evidence_refs": ["event_006"],
  "root_cause": {"step": 6, "quote": "the exact offending text", "referent": "the required/correct text"}
}
```

- `confidence` 是类别，不是数值阈值；
- `evidence_refs` 供 Defense 精确取证据，不再只看 ±2；
- `root_cause.quote/referent` 是 step localization 的结构化基础。

### 2.2 Recall Scanner + Causal Upgrade

这是本方案最大的改变。

Scanner prompt 只问：

> 找出轨迹中所有可能偏离“任务契约、工具观察或真实世界事实”的具体点，每条给出 locus、quote、evidence_refs。**不判断是否幻觉。**

对每个候选，Causal Upgrade 只问两个问题：

1. 该候选内容是否真实错误？
2. 修正它是否会让轨迹交付结果变成正确结果？

升级规则（封闭、无阈值）：

- 任一候选通过两个问题 → `verified_error`，responsible step = 最早通过的候选 locus；
- 全部候选被否 → 维持 Judge 的 `verified_clean`。

这样：
- 旧扫描器的高召回被保留；
- 旧框架“过程偏差即定罪”的 FP 被 Causal Upgrade 过滤；
- 不需要从旧目录迁移代码，只在 `detector_v2` 新写一个干净的 `recaller.py`。

成本：只对 Judge=false 或 confidence=low 的案例运行，pilot 中约 65% 案例需要，平均多 1 次 Pro 调用。

### 2.3 Step Tracer（定位）

只在终态为 error 时运行，输入：

- question；
- 交付结果与正确结果（Judge 或 Causal Upgrade 给出）；
- 候选 root_cause 集合。

两种实现可选：

- **A. 后向因果定位（推荐先做）**：要求模型从错误交付结果出发，逐步问“这个错误值/动作最早由哪一步引入”，输出最早 step + quote。这与 AgentHallu 的 counterfactual attribution 定义一致。
- **B. 官方 step-by-step 前向扫描**：逐前缀判断，首错即停。官方论文显示该方法把 attribution 从 32.7% 提到 42.7%；但 token 成本高。

建议先做 A，并把 A 的输出与 Judge.root_cause / Causal Upgrade.locus 三者取“最早一致步”；不一致时取被 evidence_refs 支持的步。

### 2.4 Defense V2.1

- 视图从“定罪步 ±2”改为“Judge/Upgrade 给出的 `evidence_refs` 全集 + 交付结果块”；
- 若 `inconclusive` 且存在未渲染的引用，升级为一次全量视图复查；
- 翻案仍需 `refuted` 的积极证据，规则不变。

### 2.5 测试时扩展（低优先级，预算允许时）

- Judge confidence=low 的 false 案例强制走 Recall Scanner；
- Judge confidence=low 的 true 案例强制走全量 Defense；
- Judge 可做 k=3 多数，但只在 low 时启用；不做全量 3x。

---

## 3. 对 pilot 失败模式的可解释预期

| 失败 | V2.1 机制 |
|---|---|
| BFCL/006/024/025/158 FN（显式任务契约） | Contract Extractor 让 Judge 按 verbatim/exact contract 比较 |
| Camel/038 FN（world knowledge 错） | Recall Scanner 发现“用户提示与真实物种冲突”候选；Causal Upgrade 判断修正物种会改结果 → 升级 |
| BFCL/001 FP（报告子串伪错误） | 无显式 verbatim 契约；Causal Upgrade 会问“修正引用整行是否改变任务结果” → 通常否 → 不升级 |
| BFCL/147 FP | Judge 必须给 evidence_refs；Defense 按 refs 看到 invoice，可能 refuted |
| step localization 差 | Step Tracer 独立定位，不再依赖 Judge 单步猜测 |

注意：这些是机制预期的方向，不是对 20 例的规则特判；每个新组件都用同一套通用定义，并在新冻结验证集上读数。

---

## 4. 实施顺序

1. `detector_v2/semantics.py`：增加 Contract Extractor / Recall Scanner / Causal Upgrade / Step Tracer 的通用 prompt；
2. `detector_v2/contract.py`（新）：契约提取与解析；
3. `detector_v2/judge.py`：输出增加 confidence/evidence_refs/root_cause，契约块注入；
4. `detector_v2/recaller.py`（新）：Scanner + Causal Upgrade；
5. `detector_v2/step_tracer.py`（新）；
6. `detector_v2/defense.py`：evidence_refs 视图 + 全量升级；
7. `detector_v2/adjudication.py`：新状态机与 responsible-step 归并；
8. 单测 + 20 例重跑（只作为开发读数，最终以冻结验证集为准）。

## 5. 明确不做

- 不把任何 case id 写入 prompt/规则；
- 不根据 BFCL 名称路由；
- 不引入数值阈值；
- 不让 Recall Scanner 直接定罪（必须过 Causal Upgrade）。
