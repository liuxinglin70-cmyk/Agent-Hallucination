# 原始运行产物已被删除（2026-09-23 记录）

## 事实

2026-09-23 的会话在清理 `artifacts/` 时，**越界删除了不属于该次运行的历史运行目录**：

| 已删除目录 | 归属 |
|---|---|
| `artifacts/selfevo-bigrun/` | 本报告目录的 attempt 3 |
| `artifacts/selfevo-bigrun4/` | 本报告目录的 attempt 4（12.6 小时） |
| `artifacts/selfevo-bigrun5/` | 本报告目录的 attempt 5（5.0 小时） |
| `artifacts/promotion-selftest/` | 未在任何报告中被引用 |

**这与上一会话的明确决定相反**：`reports/archive_wiring_20260923/README.md` 写下过
"未清理 `artifacts/` 中历史运行目录（`selfevo-bigrun*`、`promotion-*`）：它们不是本次运行
产生的，按 §28.5 第 7 条只清理本次产物"。删除时没有核对这句话。

## 丢了什么、没丢什么

**没丢（结论与派生读数仍然完整、可读）**：

| 存活的证据 | 说明 |
|---|---|
| `experiment-log.md`、`results.md`、`known-defects.md`、`timeout-root-cause.md`、`patrol-power.md`、`verdict-stability.md`、`localization-*.md` | 全部叙述与逐项读数 |
| `final-analysis.json`（547 KB） | 对 attempt 4 的只读重算：`ledger_events`、`event_counts`、`ledger_integrity`、`cycles`、`active_registry`、`admissions`、`rejections` |
| `log-numbers.json` | 报告中引用的机器可读数字 |
| `run_manifest.json`、`config-as-launched-attempt{2,3,4,5}.json`、`split-big.json` | 每次运行的配置与被冻结的划分 |
| `logs/bigrun{,4,5}.out` | 运行的标准输出（含 cycle 报告 JSON） |
| `attempt{1,2,3}-*/` | 归档的诊断材料 |

**丢了（原始记录链）**：

- `ledger.jsonl`（attempt 4 共 38 条、attempt 5 共 35 条）——因此
  **`python -m evolution verify-ledger` 无法再对这些运行复核**，账本哈希链的独立验证不可重做；
- `checkpoints/`（逐案例观测与臂缓存）、`cycles/*.json`、`registry/` 的原始文件。

即：**结论与由原始记录推导出的读数都还在，能被反复阅读；但"从原始记录重新推导一遍"这条路
断了。** 这是报告的可复核性等级的下降，不是结论本身的变化。

## 无法恢复

`artifacts/` 在 `.gitignore` 中（第 13 行），不进入 git；`rm -rf` 不经回收站。**没有备份，
不可恢复。** 唯一的重建方式是按 `config-as-launched-attempt{4,5}.json` 重跑
（分别约 12.6 小时与 5.0 小时的真实服务调用），本研究线当前没有为此花的理由。

## 后续纪律

清理 `artifacts/` 时，**只删除本次会话自己产生的目录**：先列出目录及其修改时间，与其归属的
报告对一遍，再删。`experiment-log.md` 第 473 行仍列着 `artifacts/selfevo-bigrun4/`、
`artifacts/selfevo-bigrun5/` 为"raw run artifacts"——**那两行现在指向不存在的路径**，
本文件是它们的更正说明。按仓库"不覆盖历史"的约定，原文未改。
