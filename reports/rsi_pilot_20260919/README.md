# RSI 试点 P1：机会筛查与训练机制冻结

日期：2026-09-19；代码基线：`8c6dcde`（P0 修复后）；方案：[docs/optimization-directions.md](../../docs/optimization-directions.md) 第 6–8 节。

## 结论摘要

**P1 通过。** 按冻结协议完成 19 次 case-evaluation（上限 24），找到 **两条同机制、无口径争议、H0 实际漏判的真实失败**，训练机制冻结为：

> **`quoted_content_vs_delivered`（任务引用原文与交付内容绑定）**
> 修复操作：先在任务原文中定位被引号明确引用的必需内容/正文，再查最终实际交付参数（含专用 tags/mentions 参数），检查交付是否偏离引用原文或把单独要求的元素嵌入正文。not_when：任务未以引号给出确切内容时，摘要、改写、重编号、引用包装等呈现行为不定罪；任务未要求逐字时不得触发。

- 训练失败（FN）：`BFCL/002`、`BFCL/153`
- 同族已判对（回归样本）：`BFCL/008`、`BFCL/024`
- 正常近邻（TN）：`BFCL/020`、`BFCL/053`
- 边界对照（过度触发风险）：`BFCL/013`（H0 的 FP 恰来自同族机制的严格化——把合法重编号呈现判为错误）

P2 按方案第 8 节获准启动，干预面限定为现有 `text_boundary@judge.system`。

## 执行过程

### 阶段一：首轮 12 例（冻结清单 [pilot-cases.frozen.json](../../output/rsi-planning/pilot-cases.frozen.json)）

逐例人工核查（[scope_review.json](../../output/rsi-planning/scope_review.json)）先行并提交（`c6fbac1`），基线后行。H0 = 裸底座（空 registry）、`detector/configs/autodl.json`（GLM-5.3-flash，judge/defense 各 3 票）、无外部证据、fresh 全检测、2 workers。

| 案例 | gold | scope_review | H0 | 与 gold | 备注 |
|---|---|---|---|---|---|
| 003 | true | 同意（有张力） | verified_error | 判对 | 目标候选转回归样本 |
| 024 | true | 同意 | verified_error | 判对 | 转回归样本 |
| 042 | true | 同意 | verified_error | 判对 | 转回归样本 |
| 054 | true | 有限同意 | verified_error | 判对 | 转回归样本；gold 参数 30 依赖隐藏油量，不作训练目标 |
| 013 | false | clean_usable | verified_error | **FP** | 单例，不成族；机制见下节 |
| 020 | false | clean_usable | verified_clean | 判对 | 正常近邻 |
| 018 | false | clean_usable | verified_clean | 判对 | 正常近邻 |
| 053 | false | clean_usable | verified_clean | 判对 | 正常近邻 |
| 017 | true | scope_dispute | verified_clean | FN | 口径争议，排除修复奖励 |
| 028 | true | scope_dispute | verified_clean | FN | 口径争议（"多余内容"在观察内），排除 |
| 041 | true | scope_dispute | verified_clean | FN | ES-3 已知 FN，排除 |
| 062 | true | scope_dispute | verified_clean | FN | ES-3，排除 |

首轮读数：TP 4、FP 1、FN 4、TN 3，全部 authoritative、零未决。**无两条同机制无争议失败**，且 FN 恰好全部是预排除的争议例——与方案 6.1 的预判一致。

### 阶段二：唯一一次扩展筛查（冻结清单 [expansion-cases.frozen.json](../../output/rsi-planning/expansion-cases.frozen.json)，提交 `a4508f5`）

按 case ID 顺序审查全部 31 个 BFCL Incorrect Tool Arguments 候选（排除试点 12 例；BFCL 无 validation 案例），**31 例逐一记录入选/落选原因**，入选 7 例（002/008/082/118/129/148/153）。累计测量 12+7=19 ≤ 24。

| 案例 | 机制组 | H0 | 与 gold | 备注 |
|---|---|---|---|---|
| 002 | 引用内容-交付绑定 | verified_clean | **FN** | 任务引用确切行，实际写入模板+虚构署名 |
| 008 | 引用内容-交付绑定 | verified_error | 判对 | 回归样本 |
| 082 | 观察值-参数绑定 | verified_error | 判对 | 回归样本 |
| 118 | 动作集绑定 | verified_clean | **FN** | 单例家族，仅记录 |
| 129 | 观察值-参数绑定 | verified_clean | **FN** | 单例家族，仅记录 |
| 148 | 观察值-参数绑定 | verified_error | 判对 | 回归样本 |
| 153 | 引用内容-交付绑定 | verified_clean | **FN** | 引用正文被嵌入 tags/mention |

### 训练机制的两条失败为何同机制

两条失败可由**同一个明确操作**修复（方案 6.2 的拆分标准）：

- `BFCL/002`：T2 逐字引用要写入文件的行 `'Collaboration leads to success. Innovation ignites growth.'`；step 6 实际写入完整模板、`Simona:` 虚构署名前缀与占位文本。
- `BFCL/153`：T6 引用推文正文 `'Postponing my plans.'` 并单独要求 hashtags 与 mention；step 14 把 `#Travel #PlansChanged @AirlineSupport` 嵌入正文，`tags=[]`、`mentions=[]`。

同族对照显示现有检测器的缺口：`008`（hashtag 嵌入正文但"only the file names"约束更强）与 `024`（明确 exactly as it is）已被判对，而 002/153 的引用原文偏离被漏判——现有失败族归纳按 judge/recall 状态分族，从未把"任务引用原文 vs 交付参数"作为可计分的绑定方向。

### 013 的 FP 是机制的边界而非噪声

H0 在 013 上的定罪理由是"剥离 `Line N:` 前缀并重新编号，交付内容与观察不一致"——同一"内容绑定"方向的过度触发。这验证了方案 3.5/8.2 的预警：修复候选的 not_when 必须排除"任务未引用确切原文"的情形，否则会以新增 FP 换取 FN。013 进入 P2 的对照集。

## 预算与合规

- case-evaluation：19 次（筛查上限 24）；全部 authoritative、零 unresolved、零重试换样。
- proposer/诊断调用：0（P1 为纯基线测量）。
- 墙钟：两轮 batch 各约 35–60 分钟（2 workers），远低于 4 小时阶段上限。
- 模型：`detector/configs/autodl.json` 包络未动（GLM-5.3-flash，temperature 0，票数/超时原样）。
- 标签与阈值：未改任何 gold、门限、划分、种子；全部 31+12 例的取舍原因先于基线冻结并提交。
- 样本口径：全部为开发暴露（development_exposed），结论只覆盖"训练可修性"，不构成对外性能结论。

## 下一步（P2，按方案第 8 节）

1. `evolution/research.py` 最小研究入口（离线 mock 测试先行）。
2. B/C 两路线各最多一稿一修订：B = 修好反馈转发的现有 proposer（P0 产物）；C = 完整轨迹诊断 + 同一 hook。仅 `text_boundary@judge.system`。
3. 筛查通过（两条训练失败修复原目标、两个正常近邻不退化）才进入冻结配对复核与候选未用开发验证；未通过则报告原因并停止该分支。
