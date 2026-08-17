# detector_v2.1 100 例实验报告

- 日期：2026-08-17
- 组件：detector_v2.1（Judge + Contract + Recall Scanner/Causal Upgrade + Step Tracer + Defense）
- 配置：`configs/detector_v2_autodl.json`
- 增跑并发：20
- 案例构造：
  - 前 20 例 = `v2_pilot20_cases.json`（来自 v3_evo50 前 20，冻结）；
  - 后 80 例 = `v2_extra80_cases.json`（全量 693 中排除前 20 与 ODS/015，`random.Random(20260817).sample`，冻结 seed）；
  - 合并 = `v2_100_cases.json`，无重叠。
- 运行目录：`runs_v2/v2_100_20w/`（由 `v2_pilot20_10w_v21` 与 `v2_extra80_20w` 合并）

## 1. 总体指标

| 指标 | 100 例合并 | 80 例增跑 | 20 例 pilot | 官方 GPT-5（693 全量） |
|---|---|---|---|---|
| completion | 100/100 = 100% | 80/80 = 100% | 20/20 = 100% | 100% |
| accuracy | 72.0% | 68.75% | 85.0% | 70.6% |
| TP / FP / FN / TN | 44 / 11 / 17 / 28 | 36 / 10 / 15 / 19 | 8 / 1 / 2 / 9 | — |
| positive precision | 80.0% | 78.3% | 88.9% | — |
| positive recall | 72.1% | 70.6% | 80.0% | 73.2% |
| positive F1 | 75.9% | 74.2% | 84.2% | — |
| macro-F1 | 71.3% | 67.3% | 85.0% | 70.2% |
| step localization | 47.5% | 49.0% | 40.0% | 32.7% |

**口径说明**：前 20 例曾用于 V2.1 开发期的机制调试（不是规则特判，但仍是同批开发读数），因此 100 例合并数偏乐观；80 例增跑是独立于开发调试的读数，更有代表性。

## 2. 80 例独立读数的分源表现

| 来源 | n | decisive | acc | unresolved |
|---|---|---|---|---|
| BFCL | 30 | 30 | 60.0% | 0 |
| Camel | 12 | 12 | 75.0% | 0 |
| Magentic_One | 5 | 5 | 80.0% | 0 |
| Octotools | 3 | 3 | 33.3% | 0 |
| OpenDeepSearch | 8 | 8 | 87.5% | 0 |
| OpenManus | 12 | 12 | 75.0% | 0 |
| SmolAgents | 10 | 10 | 80.0% | 0 |

（上表是 100 例合并中分源，非仅 80；BFCL 仍是主要难点，与官方结论一致。）

## 3. 成本

| 子集 | token | attempts | 平均 token/case | 墙钟 |
|---|---|---|---|---|
| 20 pilot | 580,501 | 81 | 29,025 | 约 3.8 min（10 并发） |
| 80 增跑 | 2,901,416 | 298 | 36,268 | 约 70.3 min（20 并发） |
| 合计 | 3,481,917 | 379 | 34,819 | — |

## 4. 结论

1. **机制稳定**：100/100 decisive，0 unresolved；Defense/Scanner/Tracer 的故障与降级没有把任何案例变成 unresolved。
2. **与官方同口径可比较**：80 例独立增跑 macro-F1 67.3% vs 官方 GPT-5 全量 70.2%，acc 68.75% vs 70.6%，处于同一水平线附近；step localization 49.0% 显著高于官方 32.7%。
3. **主要短板在 BFCL 型 tool-use 精确契约**：BFCL 分源 acc 最低，FP 集中在“显式契约解释过严”，FN 集中在“gold 的精确形式语义与通用语义冲突”（如 `2.0000` vs `2.0`）。
4. **下一步**（不在本报告范围内）：对 exact-form/exact-value 契约做独立 comparator，并给 world-knowledge 型错误增加独立事实核验通道。

## 5. 文件

- 案例：`detector_v2/experiments/v2_100_cases.json`、`v2_extra80_cases.json`
- 运行：`runs_v2/v2_100_20w/`、`runs_v2/v2_extra80_20w/`
- 评估：`runs_v2/v2_100_20w/evaluation.json`
