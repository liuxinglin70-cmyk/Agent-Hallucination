# 复核：批次缺陷并未决定本次终态（对 README 的更正）

日期：2026-09-21（同日晚）。只读复核，不调用模型。复核对象是本报告 README 的两处归因。

## 更正一：即使保护集构造正确，该候选也不会准入

README 记录了"保护集单极性 → 判定在冻结那一刻就注定"，并据此把本轮的 `promotion_unmeasurable` 主要归因于批次缺陷。**这个归因不完整，且会误导下一步（它让人以为换一批就能测出候选）。**

`output/promotion-planning/replay_gates_exhaustive.py` 对归档两臂做了穷举反事实：枚举全部能通过覆盖下限的保护集划分（保护集取控制臂判对的案例，收益集取其余），用**同一批纯函数门**重跑判定序列。

结果（2717 个可满足划分）：

| 停在 | 数量 | 唯一理由 |
|---|---|---|
| HARMLESSNESS | 1397 | `sentinel harmlessness violation: new_fn=1` |
| BEHAVIOR | 1320 | `target polarity mismatch: recall claim but no TP gain` |
| 准入 | **0** | — |

两种情形互斥且穷尽：`BFCL/032`（控制臂真阳、候选丢失）若落在保护集，HARMLESSNESS 因 `new_fn=1` 拒绝；若落在收益集，则保护集干净、HARMLESSNESS 通过，但 BEHAVIOR 的 recall 极性要求 `F->T > 0`——而本批全部 3 处两臂差异都是 `T->F`，**没有任何划分能造出 `F->T`**，BEHAVIOR 必然失败。

**结论：这个冻结候选在本批上无法准入，与保护集怎么建无关。** 批次缺陷让本轮失去的是 BEHAVIOR/M 的读数质量，不是终态本身。

对下一步的含义：**不要为了"验证桥接完整路径"重跑同一个候选**——它测不出别的东西，只会重复这两个失败。要验证完整路径，必须换一个在收益方向上确有增益的候选，或者把预算投到定位目标（见 `docs/optimization-round2.md`）。

## 更正二：候选的真实形状需要方向分列的读数才看得见

README 指出"消除的 2 个误报落在保护集上、不被任何门计量"，但当时没有给出可复核的读数。现已实现该读数（`evolution/metrics.py::protection_repair`，只记录不判分）。用它重读归档两臂：

```
保护集 (6 例):  repaired=2 (repaired_fp=2)   regressed=0    net=None
收益集 (14 例): repaired=0                   regressed=1 (regressed_fn=1)   net=None
```

即候选的真实形状是"**消除 2 个误报、丢失 1 个真阳**"，而门只读到 `margin_cases=-1`。这个读数现在会写进晋升报告的 `benefit.protection_repair` / `benefit.benefit_repair`，方向分列、不做加权合并（方案 §5.2 禁止加权抵消，故 `net` 恒为 `null`）。

口径细节：只有**决定性翻转**才算 repair/regression。把控制臂未决的案例判出结果属于校准，不计为修复（与 `gates._decisive_correction` 对 boundary 主张的纪律一致），单独记在 `unresolved_removed`。

## 复核脚本

```powershell
python output/promotion-planning/replay_gates_exhaustive.py   # 2717 个划分的判定序列
python output/promotion-planning/replay_gates.py              # 单个代表性划分的逐门细节
python -m pytest evolution/tests/test_protection_repair.py -q # 口径锁定（含归档读数断言）
```

以上脚本均只读：不调用模型、不写 registry、不修改任何已归档文件。
