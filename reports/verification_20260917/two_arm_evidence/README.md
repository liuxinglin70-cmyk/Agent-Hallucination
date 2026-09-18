# 可审计外部证据路径：开发诊断记录

本目录记录“外部证据如何进入检测流程”这一修复的开发诊断。所有门阈值保持不变。

## 修复内容

检测器新增 `evidence` 输入：一份可审计的外部证据快照，作为独立对象随记录进入，不写进 `question/history`，不伪装成原代理的工具观察。

- **快照 schema（`detector/evidence.py`）**：每条来源必须携带 URL、标题、发布方、获取时间、原文片段、内容 sha256、所核验的实体/属性/值、适用语境。片段哈希不匹配、字段缺失或出现未知字段即 fail-closed 拒绝。
- **同一快照到达三个阶段**：召回扫描、因果升级、防御都收到同一份渲染文本，输出通过证据 ID 引用。
- **缓存键**：provider 的 prompt cache key 加入快照摘要，证据不同就不会复用缓存回复。
- **可追溯**：`provenance.evidence_snapshot_digest` 记录摘要，`proof.external_evidence` 保存快照本身。
- **复核不再依赖记忆**：升级行与防御判定若引用带冲突的证据，只有快照记录的 `resolutions` 指定了适用来源时才被接受；未记录的冲突仍拒绝作为决定性依据。

## 轮次记录

| 轮次 | 目录 | 代码状态 | 结果 |
|---|---|---|---|
| 1 | `two_arm_evidence_round1_pre_fixes/`（原始运行保留） | 无相关性门、无冲突裁定记录 | 暴露两个缺陷（见下） |
| 2 | 原始运行已删除 | 加入相关性门 | 目标案例两次运行结论相反，暴露缺陷二 |
| 3 | `two_arm_evidence_round3_pre_upgrade_law/`（原始运行保留） | 加入 `resolutions`，升级提示尚未含证据法 | 目标两次纠正、对照干净 |
| 4 | 本目录（当前协议） | 最终版本（升级提示补齐证据法） | 与第 3 轮一致 |

第 2 轮的原始运行在重置目录时被删除，其结论不复引用为原始证据；同一现象在第 1 轮保留的运行中同样可见（见下）。

## 第一轮暴露的两个缺陷

**缺陷一：只要附加快照，召回扫描就会为所有案例重新打开。**

`Magentic_One/036.json` 是一例 gold=false 的正常轨迹（与 Lisette 毫无关系）。在带 Lisette 快照的那一臂里，它被扫描、被升级为 c1+c2、最终以 `degraded_judge_only` 判成 `verified_error`（见归档运行）。原因不在快照内容，而在当时那条规则：快照存在就强制重开扫描。它让一份与本案无关的检索记录变成了定罪依据。

**修复（`touches_packet`）**：只有当快照的实体或值确实出现在 question 或轨迹文本中，快照才生效。判定是确定性的、不读取标签，并且把原因写进 `provenance.evidence_applied` 与 `evidence_application_reason`。

**缺陷二：同一固定输入，两次运行给出了相反的冲突裁定。**

同一份快照、同一段提示，目标案例的运行 1 按语境采纳 E1 并判错，运行 2 以“冲突未决”为由拒绝（两份原始运行均在归档目录中）。同样的字节、不同的裁决——把冲突留给每次调用来解释，结果不可复现。

**修复（`resolutions`）**：冲突裁定随快照记录，指明适用来源与可核查理由；三个阶段都消费同一份已裁定输入。未记录的冲突仍然不得支撑决定性结论。

修复后的离线核对：

| 案例 | 是否适用 | 原因 |
|---|---|---|
| `OpenDeepSearch/086.json`（目标） | 是 | 快照实体 `lisette` 出现在问题/轨迹中 |
| `Magentic_One/036.json`（正常对照） | 否 | 快照实体或值均未出现 |
| `OpenDeepSearch/002.json`（正常对照） | 否 | 同上 |


## 开发诊断结果（第四轮，当前协议）

固定上游：两臂的 judge 结果都从已保存基线冻结，唯一差别是是否附加快照。目标 `OpenDeepSearch/086.json`（gold=true，标注错误在第 2 步），对照 `Magentic_One/036.json` 与 `OpenDeepSearch/002.json`（均 gold=false）。

| 案例 | 臂 | 终态 | 权威 | 责任步 | 结果 |
|---|---|---|---|---|---|
| 086（目标，2 次） | A 无证据 | verified_clean | authoritative | — | 复现原漏报 |
| 086（目标，2 次） | B 有证据 | verified_error | authoritative | **2**（=标注步） | **纠正** |
| 036（对照） | A / B | verified_clean | authoritative | — | 无误伤，B 臂 0 次调用 |
| 002（对照） | A / B | verified_clean | authoritative | — | 无误伤，B 臂 0 次调用 |

证据臂两次运行的链路一致且可审计：

- **触发**：相关性门确认快照实体 `lisette` 出现在问题/轨迹中，召回扫描启动。
- **新增有效证据**：扫描提出 c1，引用 `[E1, E2]`；升级门的 `conflict_note` 写明按快照记录的裁定，以 E1 为适用来源。
- **纠错**：升级判定 `content_error=true, causal=true`；防御以 `substantiated` 确认，引用 `[E1]`；定位步 2 与数据集标注一致。
- c2（未做交叉验证）两次都被正确拒绝：那只是代理自设的计划，不构成交付物违约。

关键的可复现性来自第一轮的教训：同一份输入、两次运行曾给出相反的冲突裁定。把裁定作为快照的 `resolutions` 记录下来之后，第 3、4 两轮各两次运行结果一致。**冲突由记录裁定，不由每次调用重新解释。**

第 3 轮与第 4 轮的差别只在升级阶段的提示补齐了证据法条文；两轮结论相同，说明该补全没有改变行为方向。

## 边界与未决事项

- **运行与归档的字节差异（已核实，无语义变化）**：诊断运行时，四个文件（`detector/api.py`、`detector/evidence.py`、`detector/recaller.py`、`detector/defense.py`）是 CRLF 行尾；归档前已统一为 LF，因此 `protocol.json` 中的 `source_digest=fa50a04aeff4b9ce` 与当前代码的摘要不同。已用临时副本还原当时的字节并复算，得到完全相同的摘要，差异仅为行尾。`snapshot_digest=8131b0ff9e164ec719641b5cb0cced13770d2b2cb597ab01e9bdb561ef0bcf37` 与当前一致。
- 快照来源核查是**开发期人工完成的，不是盲测检索成功**。系统尚未具备按案例自动检索并抓取证据的能力；`touches_packet` 只是确认固定快照是否适用于本案，不是检索器。
- 相关性门是词面匹配。若任务用别的名称指代同一实体（如船名缩写），它会拒绝一份本可适用的快照；这是尚未解决的召回缺口，不是本次修复的目标。
- 单案例、单快照、四轮运行（其中 3 轮有原始记录）的开发诊断，不足以证明普遍纠错能力。它证明的是：链路可用、纠错来自可核验证据而非门限调整、正常对照未被误伤。
- 防御阶段在部分运行中因 900 秒案例时限未完成投票（见归档运行），此时权威降级为 `degraded_judge_only` 而不是被当作纠错或正常。这是运行健康问题，不是机制结论；当前协议下目标案例的两次运行都在时限内完成。
- 下一份快照若包含更多来源，冲突与独立性需要同样的记录方式；没有任何来源相互独立时，结论应记为未决。

## 快照来源

`evidence_snapshot.json` 由两份已人工核验的来源构成，片段取自实际抓取的文本：

- **E1**：[Q-Ships and Their Story](https://www.gutenberg.org/cache/epub/54338/pg54338.txt)（E. Keble Chatterton, 1922），第七章。原文含 “This was the 116-ton topsail schooner Lisette, which had formerly belonged to the Duke of Sutherland.” 与建造年 1873，覆盖题目全部四项描述符。
- **E2**：[Heritage Images 照片目录](https://www.heritage-images.com/preview/the-76-ton-schooner-lisette-under-sail-creator-kirk-sons-of-cowes-2696189)，题名使用 76 吨，只给出船名与 1873 年，不含 Duke of Sutherland 与诱饵船语境，也未引任何吨位测量或船籍记录。

两份来源对 (Lisette, tonnage) 给出不同值，快照记录 `resolutions` 指定 E1 为适用来源并写明理由。裁定依据是题目自身的四项描述符，不是数字多数，也不是把 76 换成 116。

