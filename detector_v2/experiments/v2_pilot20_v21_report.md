# detector_v2.1 Pilot-20 验证报告

- 日期：2026-08-17
- 代码：`detector_v2/` V2.1（Judge + Recall Scanner/Causal Upgrade + Step Tracer + Defense）
- 配置：`configs/detector_v2_autodl.json`
- 案例：`datasets/eval_splits/v3_evo50/evolution_cases.json` 前 20 例（冻结）
- run id：`v2_pilot20_10w_v21`，10 并发
- 输出：`runs_v2/v2_pilot20_10w_v21/`

## 1. 总体指标

| 指标 | V2.1 | V2.0 pilot | 旧检测器同 20 例（decisive） | 官方 GPT-5（全量 693） |
|---|---|---|---|---|
| completion | 20/20 = 100% | 100% | 95% | 100%（官方必出二元） |
| accuracy | **85.0%** | 65.0% | 68.4% | 70.6% |
| TP / FP / FN / TN | 8 / 1 / 2 / 9 | 5 / 2 / 5 / 8 | 8 / 5 / 1 / 5 | — |
| positive precision | 88.9% | 71.4% | 61.5% | — |
| positive recall | **80.0%** | 50.0% | 88.9% | 73.2% |
| positive F1 | 84.2% | 58.8% | — | — |
| macro-F1 | **85.0%** | 64.2% | 67.6% | 70.2% |
| step localization | **40.0%** | 20.0% | 30.0% | 32.7% |

注：20 例是小样本，与全量 GPT-5 只能做量级对照。

## 2. 修复后翻转账本（V2.0 → V2.1）

V2.1 新修复的案例：
- V2.0 FN → V2.1 TP：`BFCL/006`、`BFCL/024`、`BFCL/158`；
- V2.0 FP → V2.1 TN：`BFCL/001`；
- V2.0 FN → V2.1 TP（Defense 不再误翻案）：`BFCL/081`。

仍残留 3 个错误：
- `BFCL/025`：gold 要求 `2.0` 而轨迹写 `2.0000`；当前 Judge 按“precision up to four decimals”判 clean，Scanner 也未出候选。这是任务契约与 gold 语义冲突的硬例。
- `Camel/038`：gold 正确物种是 Rockhopper penguin，但 Judge/Scanner/Upgrade 都沿用了工具观察里的 King Penguin；属于 world-knowledge 召回问题。
- `BFCL/147`：Judge 把“总费用必须 $850”当显式契约并维持定罪，gold=False；这是契约解释仍偏严的 FP。

## 3. Step localization 明细（V2.1）

命中 4/10：

| case | resp | gold | 命中 |
|---|---|---|---|
| BFCL/006 | 6 | 6 | ✓ |
| BFCL/024 | 5 | 5 | ✓ |
| BFCL/051 | 4 | 3 | ✗ |
| BFCL/081 | 8 | 7 | ✗ |
| BFCL/111 | 4 | 7 | ✗ |
| BFCL/117 | 8 | 8 | ✓ |
| BFCL/158 | 14 | 11 | ✗ |
| Camel/011 | 3 | 3 | ✓ |

Step Tracer 作为独立阶段后，V2.0 的 20% 提升到 40%，已高于旧检测器 30% 和官方 GPT-5 全量 32.7%（小样本）。

## 4. 成本

- 总 token：580,501；
- 平均 token/case：29,025；
- provider attempts：81；
- 10 并发，20 例累计 CPU/IO 时间 6,933 秒（并发后墙钟明显更低，最后一次补跑 10 例约 10 分钟）。

成本上升来自：每个 Judge=false 案例多跑 Scanner + Upgrade，每个阳性案例多跑 Tracer + Defense。对 20 例规模完全可接受。

## 5. 结论

V2.1 的机制修复按预期生效：

1. Recall 不再被单次 Judge 锁死：Judge=false 的 `BFCL/006/024/158` 由 Recall Scanner + Causal Upgrade 找回；
2. Causal Upgrade 的“缺行不升级”语义修复后，召回路径不再因模型少返回候选而整路失败；
3. Defense 增加“显式任务契约不得当格式差异翻案”后，`BFCL/024/081` 不再被误翻案；
4. Step Tracer 优先于 Defense confirmed_step，step localization 从 20% → 40%。

残余问题均为语义边界或世界知识，不再由架构缺陷主导。
