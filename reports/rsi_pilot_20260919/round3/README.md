# RSI 试点第 3 轮（仪器轮）：种子候选复筛未过，按协议停止

日期：2026-09-20；协议：[research-manifest-round3.json](../../output/rsi-planning/research-manifest-round3.json)（运行前冻结，提交 `b13c2b6`）；代码基线：`78f7c4c`（seed_candidate 能力 + 3 项离线测试，全库 417 passed）。

## 结论摘要

**第 3 轮在第一道门（种子复筛）按冻结门停止：无候选进入面板验证。** 停止原因是一个新的实质发现：种子 v002（哈希钉住，与第 2 轮 attempt 2 通过筛查与 3/3 复核的候选逐字节相同）在 `BFCL/020` 上的本轮读数为 **authoritative verified_error**——judge 判阴（medium）后召回触发，升级实质性通过「整理副本而非原文件」声明，**防御听证在 32768 包络下完整执行并实质性定罪**（step 6）。这与 attempt 1 的同例失败不同：那一次是 8192 截断（基础设施伪影），这一次是语义定罪。

## 执行过程

1. 基线 4 例现测（H0：002/153 漏判、020/053 clean，全 authoritative）。
2. 种子装载成功：`seeded_from` 记录含源运行、版本与钉住哈希；契约校验通过；proposer 零调用（预算 0）。
3. 种子复筛（4 case-eval）：002@6、153@14 修复（与历史一致），053 clean，**020 判 error** → 筛查门失败。
4. 按协议「任一阶段未过即停止」，面板验证（8 例 × 3 重复 × 2 臂）未启动。总消耗 8 次 case-evaluation + 0 次 proposer。

## 关键发现：020 的准确定级

跨全实验的 020 读数史：

| 测量 | 臂 | 读数 |
|---|---|---|
| P1 试点 | H0 | clean |
| R2 attempt 1 复核 ×3 | H0 | clean / clean / clean |
| R2 attempt 1 复核 ×3 | v001 | **error（8192 截断降级）** / clean / clean |
| R2 attempt 2 筛查 | v002 | clean |
| R2 attempt 2 复核 ×3 | H0 | clean / clean / clean |
| R2 attempt 2 复核 ×3 | v002 | clean / clean / clean |
| R3 基线 | H0 | clean |
| R3 种子复筛 | v002 | **error（authoritative，防御实质性定罪）** |

分级：H0 从未翻转（8/8 clean）；候选臂 8 次测量中 2 次 error（1 次截断伪影、1 次语义定罪）。**020 不是 024 型两臂噪声，而是一个低频的、与候选相关的召回路径 FP 风险**：候选指令在场时 judge 投票出现分歧（020 例 judge 为 medium 置信阴性），召回触发后升级+防御倾向于对「整理副本而非原文件」这类任务解释性声明定罪——该声明与候选机制（引用内容绑定）无关，却因召回触发而被连带定罪。

这正是 P2 报告预注册的触发条件：「若仍经召回→防御路径把邻居定罪，按方案 §9 触发消费位扩展的单独决定」。judge.system 的指令文本无法约束防御消费位——第 2、3 轮的证据（013 与 020）共同说明该消费位约束是剩余风险的结构性根源。

## 对下一轮的输入

1. **消费位扩展决定（§9，需单独冻结）**：将 `text_boundary` 的研究面从 `judge.system` 扩至已注册的 `defense.system`（`detector/interventions.py` 的 HOOKS/SYSTEM_HOOKS 已支持，`defense.py:431` 已消费 `prompt_block`；仅研究通道的第一轮限制 `ALLOWED_HOOK` 需按 manifest 放宽），使候选的 not_when 同时约束升级/防御对邻居的定罪。需要配套多入口联合候选支持。
2. 024 维持噪声案例定级（两臂 3/5 vs 3/5），面板门设计（round-3 manifest）可复用。
3. 002/153 的修复在全部测量中稳定（本轮再 +2：4/4 → 6/6 候选臂读数），机制收益本身未被任何一轮读数动摇。

## 预算与合规

- 本轮：8 次 case-evaluation（基线 4 + 复筛 4）+ 0 次 proposer 调用；模型包络未动；gold/门限/划分/种子未动。
- 试点累计（P1 至第 3 轮）：约 190 次 case-evaluation、10 次 proposer 调用。
