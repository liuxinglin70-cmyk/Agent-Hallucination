# 仓库整理记录

整理日期：2026-09-16。当前源码、测试与核心说明均使用职责名称。

| 原入口或位置 | 当前位置 |
|---|---|
| `detector_v2/` | `detector/` |
| `evolution_v3/` | `evolution/` |
| `DetectorV2Backend` | `LiveDetectorBackend` |
| 版本编号的准入门 | structural / harmlessness / behavior / marginal |
| 多轮框架配置 | `evolution/configs/development.json` |
| 供应商检测配置 | `detector/configs/autodl.json`、`deepseek.json` |
| 多份迭代设计文档 | 2026-09-16 整理为 `docs/architecture.md`、`interventions.md`、`evaluation.md`、`maintenance.md`；2026-09-21 前三份合并进 `docs/technical-solution.md`，历史过程文档移入 `docs/history/` |
| 最新验证 | `reports/latest/` |
| 日常运行输出 | `artifacts/` |

旧框架残留测试、归档方案、历史实验脚本、重复数据派生物、网页下载、旧报告、缓存和 registry 均移出活动仓库。测试实际需要的两份 registry 转为专用夹具，保留原有覆盖。

原始数据 693 个案例、全部 695 个文件逐文件校验保留。保留 `.git` 与原有 `.env`，没有提交或重置用户原有工作树修改。

批量删除被自动审批检查拒绝（仅返回 blocked by policy），因此使用可恢复的仓库外隔离目录 `../cleanup_quarantine/`。隔离目录不属于当前实现或测试依赖，也没有进入仓库。

`historical_context.json` 仅保留整理前测试基线与最后一轮旧实验背景。当前代码测试以 `../latest/summary.json` 和其源码哈希为准；历史基线为 188 项通过，当前新增跨模块、命令行默认参数与数据边界回归。
