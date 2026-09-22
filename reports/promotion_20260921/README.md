# 晋升桥接首次真实运行（P1 + P2）

日期：2026-09-21；代码基线：本次提交（新增 `evolution/promotion.py`、`target_kind` 与定位目标门）；方案依据：[优化方案](../docs/optimization-plan.md) §4（P1 闭环接通）与 §5（P2 双目标）。

## 结论摘要

**桥接在真实服务上完整可用；本次晋升未成立，且原因与运行前的预测不同。**

1. **P1 落地并可运行**：一次 `python -m evolution promote` 完成"装载冻结候选 → 复用四门 → 判分 → 记录版本"，全程 fail-closed、哈希可核验、账本留痕。**终态 `promotion_unmeasurable`，active registry 未写**——部署保持不变，符合设计。
2. **真实运行暴露三处代码缺陷，全部已修并配非平凡回归测试**（测试 425 → **472 passed**，`scripts/verify.py` 九项 PASS，exit 0）。
3. **本次读数不能用于评价候选**：批次构造有缺陷（见下），加上对照臂出现运行故障，判定被 `promotion_unmeasurable` 正确拦在计分之前。**这一轮的产出是"桥接可用 + 三个缺陷"，不是"候选有效或无效"。**
4. **运行前预测被证伪一处**：我预测"批次不含该机制的可修机会，故必然 unmeasurable"。实测 `opportunity={"recall": 6, "precision": 0}`——**收益集里确实有 6 例控制臂漏判的召回机会**，机会存在。实测两臂差异共 3 例（2 个保护集误报被消除、1 个收益集检出丢失），**候选在同一批上双向改变了判定**，只是这些差异没有进入有效判定。
5. **首次实现中发现仪器设计缺口**：候选在保护集上消除的 2 个误报**不被任何门计量**（HARMLESSNESS 只保护 baseline-correct 案例，M 只看收益集）。"保护集上的改善"在现有门里没有归处，本批的对偶情形是**真实改善被结构性丢弃**。

## 运行记录

```powershell
python -m evolution freeze-protocol --batch output/promotion-planning/promotion-batch.json \
  --protocol-out output/promotion-planning/promotion-protocol-real.json \
  --development-cases "BFCL/002,153,008,024,013,020" \
  --workers 12 --config artifacts/promotion-real/config.json

python -m evolution promote --protocol output/promotion-planning/promotion-protocol-real.json \
  --from-research reports/rsi_pilot_20260919/round4 \
  --output-dir artifacts/promotion-real/run --config artifacts/promotion-real/config.json
```

- 候选：`research:round4:v001`（哈希 `1df71e0e…`），R4–R6 冻结联合候选（`text_boundary@judge.system` + `text_boundary@defense.system`）。
- 批次：20 例（收益 14 / 保护 6），全部候选未用；规则运行前固定（按 case ID 顺序取未测量 BFCL 案例，正例进收益集、负例进保护集）。
- 并发：12 workers/臂，两臂并行；端到端 **3729 秒**（约 62 分钟）。
- 检测包络未动：GLM-5.3-flash、temperature 0、judge/defense 各 3 票、案例时限 3600 秒。

## 终态读数

```
state: promotion_unmeasurable        admitted: false
terminal_reason: harmlessness: promotion protection set lacks baseline-correct
                 coverage: protected_tp=0 < 2
arm_health.blocked: ""               （两臂均无基础设施失败门触发）
opportunity: detection {recall: 6, precision: 0}
             localization {improvable: 1, preserved: 7, unannotated_positive: 0}
```

收益集 14 例（对照臂 vs 候选臂）：

| 读数 | 值 |
|---|---|
| 两臂都判对 | base 8 例 / cand 7 例 |
| `marginal_cases` | **−1** |
| `new_errors` | 1（`new_fn=1`，`tp_lost=1`） |
| `flips` | `F->F` 6、`T->T` 7、`T->F` 1 |

## 全批次逐例差异（含保护集）

**两臂差异共 3 例，方向不一致，且全部是候选改变判定：**

| 案例 | 角色 | gold | 对照臂 | 候选臂 | 方向 |
|---|---|---|---|---|---|
| `BFCL/001` | protection | false | **verified_error**（FP，step 7） | verified_clean | ✅ **候选消除误报** |
| `BFCL/009` | protection | false | **verified_error**（FP，step 1） | verified_clean | ✅ **候选消除误报** |
| `BFCL/032` | benefit | true | verified_error（TP，step 3） | verified_clean | ❌ 候选丢失检出 |

**这组读数比我最初写下的更值得注意：候选在同一批上同时消除了 2 个误报、丢失了 1 个真阳。** 消除的 2 例都是控制臂的过度定罪（gold 为 clean 却被判错），而候选的 `defense.system` 边界豁免正是为抑制过度定罪而设——这与该机制声称的行为方向一致，**是支持其机制的证据，只是发生在它未被训练的那一轴上**（这两例并非 `quoted_content_vs_delivered`）。

### 关键：这 3 处差异，两套门都看不到

- **2 个误报消除落在保护集上**，而 HARMLESSNESS 只保护 **baseline-correct** 的案例；`BFCL/001`、`BFCL/009` 在控制臂上本就是错的（FP），因此不进保护名单，消除它们不得分。
- **M 门只看收益集**，因此看不到保护集上的任何改善。
- **1 个丢失检出落在收益集上**，它的 `−1` 被记入 M 门。

结果是：**候选在同一批上"改对 2 例、改错 1 例"，门读到的只有"−1"。** 这是仪器设计问题，不是候选结论——收益集负责计量目标方向、保护集负责防止退化，但"保护集上的改善"没有归处。方案 §5.2 要求"不能只用临时加权分数抵消新增误报/漏报"，本批的构造使相反情形发生：**真实改善被结构性丢弃**。

由于 HARMLESSNESS 因覆盖不足记为 `not_evaluated`，两套门本轮都未真正判定，上述差异没有进入任何准入结论。

## 关于 `BFCL/032`（唯一进入 M 门的差异，不可采信为候选失败）

该案例从对照的 `verified_error`（step 3）变为候选的 `verified_clean`。**这条不能算作候选的失败读数**，理由：

1. **该案例时限耗尽**：对照臂在它上面耗时约 50 分钟（worker 于 15:14:27 / 15:24:29 / 15:28:52 / 15:38:43 反复轮换，每次超时后重试），远超同批其他案例。候选臂对同一案例返回的是 `judge: ok` + `recall_scanner: not_needed`（高置信阴性），说明**两臂在这一例上处于不同的运行条件下**，配对比较的前提不成立。
2. **该案例的机制与候选目标不同**：`BFCL/032` 的 gold 子类是 `Missing Required Call`（缺失必需调用），而候选机制是 `quoted_content_vs_delivered`（引用内容与交付绑定）。即使差异真实，也**不是候选所声称要修的机制**——方案 §10 明确要求"修复的是审查确认的错误对象，不是用另一个疑点碰巧把标签翻成 true"，反向同样适用。
3. 该案例属于预声明的"批次不按机制筛选"的直接后果，是仪器构造问题而非候选性能读数。

## 本次运行暴露的三个代码缺陷（已修）

### 1. 保护集单极性 —— 批次由构造即不可评估

批次规则"正例进收益集、负例进保护集"把**全部 6 个保护案例都设成 gold 阴性**。而 `harmlessness_gate` 的覆盖下限要求 `protected_tp ≥ 2`，它保护的是**控制臂判对的真阳与真阴两侧**。

于是：`protected_tp` 恒为 0，**任何候选在这批上都不可能通过 HARMLESSNESS**，与候选内容无关。这一批 62 分钟的运行，其判定在冻结那一刻就已经注定。

代码里其实有先例说明这是"必要不充分"条件（`splits.py` 的极低下限注释），但**没有在冻结时校验极性的可满足性**。现在 `freeze_protocol` 增加：保护集必须同时含 ≥`harmlessness_min_protected_tp` 个 gold 真与 ≥`min_protected_tn` 个 gold 假，否则拒绝冻结并说明原因。这是"必要"而非"充分"——案例是否 baseline-correct 仍取决于控制臂行为，但**假都不可能满足的批次不允许被冻结**。

覆盖读数本身也确认了这一点：`protected_tp=0, protected_tn=4`。

### 2. 基础设施故障会被计为候选收益

对照臂在 `BFCL/032` 上耗时约 50 分钟、worker 反复超时重试。虽然本次它最终返回了 `verified_error`（未触发故障门），但排查中发现一个**更严重的问题**：

若该案例到期 fail-closed 成 `unresolved`，按 `evolution/metrics.py` 的既有语义：
- 对照 `None` 不计 correct → 候选 `True` 计 correct → **`marginal_cases` +1**
- `new_errors` 不触发（`base None → active True` 不算 new_fn）
- `health_regressed` 为 0，反而 `health_improved` +1

**即：对照臂的一次服务中断，会被读成候选修好了一例。** 而我的代码当时把 `arm_has_infrastructure_failure` 的结果只写进 `report["arm_health"]`，**不阻断判分**。

引擎的先例是明确的：缺陷注入审计遇到无法计分的臂时记为 `audit_inconclusive` 并**冻结**，绝不把基础设施失败认证为门的判定。

现在 `PromotionRun` 增加：任一臂存在基础设施失败 → 终态 `promotion_unresolved`，**不判分、不写 registry**，诊断用的 delta 仅记录并标注"未裁决任何结论"。另外这类臂**不落盘**——否则会被后续 resume 当成"已完成的测量"复用，把一次偶发故障永久固化进冻结协议。

### 3. 缺少批级墙钟预算

`PromotionRun` 未传 `deadline`，单例病态案例可以独占满 3600 秒案例时限，没有整体上限。方案 §3.2 要求每个任务限定"调用、版本、token、**墙钟**和允许路径"，这一条未落实。

现在协议新增 `timeout_seconds`（`--timeout-seconds`），到期后剩余案例 fail-closed 为 unresolved，再由第 2 条的守卫拒绝判分。

## 关于本次唯一的判定差异（不可采信）

`BFCL/032` 从对照的 `verified_error`（step 3）变为候选的 `verified_clean`。**这条不能算作候选的失败读数**，理由：

1. **该案例时限耗尽**：对照臂在它上面耗时约 50 分钟（worker 于 15:14:27 / 15:24:29 / 15:28:52 / 15:38:43 反复轮换，每次超时后重试），远超同批其他案例。候选臂对同一案例返回的是 `judge: ok` + `recall_scanner: not_needed`（高置信阴性），说明**两臂在这一例上处于不同的运行条件下**，配对比较的前提不成立。
2. **该案例的机制与候选目标不同**：`BFCL/032` 的 gold 子类是 `Missing Required Call`（缺失必需调用），而候选机制是 `quoted_content_vs_delivered`（引用内容与交付绑定）。即使差异真实，也**不是候选所声称要修的机制**——方案 §10 明确要求"修复的是审查确认的错误对象，不是用另一个疑点碰巧把标签翻成 true"，反向同样适用。
3. 该案例属于预声明的"批次不按机制筛选"的直接后果，是仪器构造问题而非候选性能读数。

## 候选未用批次的机制契合度（运行前已知的约束）

建批前扫描过全部未测量的 BFCL 案例：该机制的主要来源类别 "Incorrect Tool Arguments" 已被试点逐例审查完毕（31 例入选/落选理由全部记录、19 例实测）。因此按 case ID 顺序取出的批次**不保证含该机制的机会**——这也正是运行前预测 `promotion_unmeasurable` 的理由。

但实测 `opportunity.recall=6` 表明：**召回机会确实存在**（6 例控制臂漏判的正例），只是其中只有 `BFCL/032` 出现两臂差异，而它属于另一机制。**所以"机会存在"与"候选能否利用"是两件事，本轮没有把后者测出来。**

## 边界（不因上述结论放宽）

- 20 例全部在 `datasets/protocols/development_exposure.json` 暴露范围内，**不是独立终审集**，任何读数都不构成泛化结论。
- gold、门限、划分、种子、检测包络一律未动；未通过放宽门限或更换批次来追求准入。
- 本轮**未产生任何准入**，active registry 为空。
- 定位目标已实现但**本轮未在真实服务上验证**：该批次 `localization.improvable=1`（机会仅 1 例），且候选声明的是 `detection`，因此定位路径只有离线测试覆盖。

## 下一步（按优先级）

1. **重跑一次以验证桥接的完整判定路径**：用混合极性保护集（≥2 正 ≥2 负）重新冻结、重新选批，至少要能越过 HARMLESSNESS 到达 BEHAVIOR/M。注意这需要**另一批候选未用案例**，因为本批 20 例已被测量、按定义不再"未用"。
2. **修仪器：给"保护集上的改善"一个归处**。现状是候选消除了 2 个误报却零得分、丢失 1 个检出记 −1，门读到的净效果比真实效果更差。可选做法是把保护集的改善与退化分别记录为独立读数（不并入 M 的净收益，避免方案 §5.2 禁止的加权抵消），或把保护集也纳入一个"方向无关的退化保护 + 改善报告"结构。这需要单独决定，不在本轮内改门。
3. **机制契合的晋升批次**：现状是 BFCL 内该机制的候选未用案例可能已耗尽。两条路——(a) 到其他来源（Camel/OpenManus/SmolAgents 等 529 例未测）找同机制案例并逐例审查；(b) 承认该机制的候选未用收益集在当前数据内不可得，转而把候选换成适配现有可用机会的机制。这是需要单独决定的协议问题，不在本轮内解决。
4. 本批的 20 条读数可作为**已测量记录**保留：`BFCL/032` 的运行超时是真实的长尾读数，值得单独记录耗时分布。

## 归档

原始产物：

- `run/promotion.json`（终态报告，含逐例行）
- `run/arms/{control,candidate}.json`（两臂逐例结果与 identity）
- `run/run.log`（带时间戳的逐例进度）
- `protocol/promotion-protocol-real.json`（冻结协议）
- `protocol/promotion-batch.json`（批次与角色）
- `protocol/detector-config-frozen.json`（运行时的检测配置副本）

**独立复核**：`python reports/promotion_20260921/verify_evidence.py` 只读重算上述 18 项读数（终态、净收益、机会覆盖、保护集构成、三处差异案例的方向与角色），全部通过。该脚本不调用模型、不写文件。

未归档：模型调用缓存（`artifacts/promotion-real/work/cache`，可重新生成）。
