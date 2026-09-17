# 真实两轮自进化实验结果（开发 30+10 协议）

执行日期：2026-09-16。状态：**未完成（第 2 轮被中断）**，但第 1 轮是完整且可验证的真实闭环。

> **2026-09-16 深度复核更正**：下文保留原实验记录，但部分早期归因不准确，以[复核分析与优化方案](analysis.md)及[可复算结果](recheck.json)为准。patrol标准口径为 **4 FN、1 FP、3 U**，原“5 FN、3 FP”混入未决；IV-1-002在sentinel修正了1例漏报，IV-1-003在sentinel修正了1例误报，均未形成G收益；IV-1-001在原失败族两例上均能触发，在S+G上未触发。原G没有FN。无traceback不能单独证明中断来自环境。新增真实诊断单独存于`recheck_live/`，不属于原两轮准入结果。

这是首次在整理后的代码上，用真实 `GLM-5.3-flash` 跑通 LLM proposer → 真实 detector 配对评估 → 四门准入的真实进化流程。**结论是：协议要求的真实收益未成立，有效准入为 0。** 两个候选在sentinel上各出现一次局部改善，但没有候选满足行为方向及gain收益要求；门限执行与收益集覆盖是否充分，应分开判断。

## 运行配置

| 项 | 值 |
|---|---|
| 配置 | `evolution/configs/development_real_run.json` |
| 数据划分 | 开发 30+10（sentinel 6、gain 4、patrol 20；validation 10） |
| 轮数 | 2（`--cycles 2 --mini`） |
| 模型 | `GLM-5.3-flash`，proposer 输出预算 32768 |
| 工作目录 | `artifacts/evolution_real`（独立 work_root/registry/ledger，起始 registry 为空） |
| 初始 registry | 空（`content_hash=1a4fe1d7…`，未加载任何历史干预） |

## 时间线

| 时刻（本地） | 事件 |
|---|---|
| 18:03 | 启动，初始 registry 为空 |
| 18:26 | 缺陷注入审计完成（通过） |
| 19:33 | **第 1 轮完成**（约 90 分钟） |
| 19:37 | 第 2 轮完成 patrol 与提案，进入 gate 评估 |
| 19:37 之后 | 进程被中断；stdout 无输出、无退出码 |

第 2 轮在提案后未完成后续结果落盘。已有记录无 traceback、无退出码，不能据此单独确认中断原因；环境侧中断是原记录中的解释，尚缺运行器或系统日志验证。

## 第 1 轮：完整闭环

### patrol（裸底座，20 例）

- 20 例中 **12例正确、4 FN、1 FP、3 U**。原worksheet的“5 FN、3 FP”将3个未决/降级案例按gold方向混入错误统计，不能作为标准混淆矩阵。
- 稳定失败族 **1 个**：`FAM-fn-judge_false_no_recall_candidates-…`，2 例同机制（BFCL/017、OpenDeepSearch/086），诊断完整，判定为 actionable。
- 未触发 bootstrap 重跑（首次 patrol 即找到族）。

### 审计（缺陷注入）

防御性审计**通过**：两个故意注入的缺陷候选都被正确拒绝，其中 `IV-DEFECT-001` 因 directive 中泄漏案例 ID（`BFCL/001.json`）被结构门拦下。这证明结构门确实在拦截标签泄漏。

### 提案（真实 GLM）

真实 LLM proposer 一次调用即产出 **3 个候选**，全部针对该失败族：

| 候选 | 类型 | 声明方向 |
|---|---|---|
| IV-1-001 | recall_trigger | 把“有交付结果但工具输出为 None”的 judge 负例送进召回网 |
| IV-1-002 | recall_trigger | 把“交付结果无成功工具观测锚定”的 judge 负例送进召回网 |
| IV-1-003 | text_boundary（upgrade.system） | 让因果升级复核未锚定/失败的交付 |

### 四门结果：3 个候选全部拒绝，准入 0

| 候选 | structural | harmlessness | behavior | 边际门 | 结果 |
|---|---|---|---|---|---|
| IV-1-001 | 通过 | 通过 | **失败** | — | 拒绝 |
| IV-1-002 | 通过 | 通过 | 通过 | **失败** | 拒绝 |
| IV-1-003 | 通过 | 通过 | **失败** | — | 拒绝 |

拒绝原因：

- IV-1-001：`claim behavior mismatch: recall_triggered_cases=0` —— 候选声称会触发召回，但实测 0 次触发，行为与声明不符。
- IV-1-003：`target polarity mismatch: recall claim but no TP gain or precision was harmed` —— 声明要提升召回，实测无TP增益，而是在sentinel移除了1个FP；不是精确率受损。
- IV-1-002：`marginal_cases=0 < 1` —— 行为门通过，但配对评估在 gain 集上净增正确案例为 0，未达最小边际收益。

关键点：IV-1-001在S+G上没有真正触发召回；IV-1-002触发1例且在sentinel修复1个FN，因G收益为0被拒绝；IV-1-003发生注入并在sentinel修复1个FP，但与声明方向不符。原G只有2TP、1TN、1FP，完全没有FN。现有门限按规则执行，不等于这组划分充分检验了召回能力。

### 配对评估的噪声控制

- 已有 registry 为空，`active_effect_on_g`：paired_n=4，base 与 active 全对一致（3 正确 + 1 一致错误），`marginal_cases=0`，无新增错误。
- `fresh_control.reroll_vs_cached_baseline`：`marginal_cases=0, new_errors=0, unresolved_added=0` —— 同一registry的一次重掷未见终态差分，但不能排除候选臂的provider波动，也不能证明中间阶段完全一致。
- 第 1 轮结束时 active registry **仍为空**，`active_after_cycle=[]`。

## 第 2 轮：部分结果

- patrol 与提案完成。真实 GLM proposer 在**第一次调用时给错了 failure_family 键**（漏掉 `-Pa007b166787d` 后缀），两个候选被 `unknown_failure_family` 拒绝。
- **repair 路径生效**：proposer 的第二次（repair）调用修正了 family 键，产出 3 个合法候选 `IV-2-001/002/003`，均标记 `frozen_judge_replay_compatible=true`。
- 随后进程中断，**gate 评估无结果**，第 2 轮未完成。

## 成本

| 项 | 值 |
|---|---|
| proposer 调用 | 3 次，42589 token |
| detector 侧已缓存结果 | 40 次结果落盘，541318 token |
| 第 1 轮墙钟 | 约 90 分钟 |

detector 侧 token 只统计了落盘缓存的部分；gate 的 fresh 重跑不写入该缓存，因此实际消耗高于此数。

## 校验

- `verify-ledger`：**valid=true，12 条记录，哈希链完整**。
- 原始数据 695 文件 SHA-256 未变。
- active registry 为空且 `content_hash` 与初始一致，未发生静默污染。

## 证据边界

- **"真实收益未成立"只对本次轮次成立。** 第 2 轮未完成，两轮 protocol 的完整结论尚未取得；不能由第 1 轮单独推断该框架在真实数据上永无收益。
- 门限在 `m_min_marginal_cases=1`、`m_new_errors_allowed=0` 下运行，未放宽。0 准入是**门限拒绝**的结果，不是没有产出候选——3 个候选都真实通过了结构门与无害门。
- 失败族的正例成员只有 2 个，patrol 仅 20 例；边际评估的 paired_n=4，样本很小，`marginal_cases=0` 的置信度有限。
- 第2轮具体中断原因未证实。当前缺少该轮的断点续跑能力：`cycle_started`已写入但没有完成记录，现有引擎具有防重用保护。诊断还发现第二轮账本只保留候选ID及notes，没有完整候选正文，无法据此恢复精确提案。

## 复现

```bash
# 新一轮真实进化（必须用新的 work_root/registry_dir/ledger_path）
python -m evolution experiment --mini --cycles 2 \
  --config evolution/configs/development_real_run.json
```

注意：`artifacts/evolution_real` 中第 2 轮已写 `cycle_started`，直接重跑会因防重用保护而 `raise`。复现需另建配置指向新的工作目录，或清理该目录后重跑。`python scripts/verify.py` 不执行真实进化。
