# 历史文档索引

本目录保存已被当前文档取代、但结论仍被后续报告引用的过程性文档。**这里的结论只对当时对应的代码基线与运行有效，不代表当前实现**，不得作为现状依据引用。

当前有效文档共五份，都在 `docs/` 顶层，由根目录 [README](../../README.md) 索引：[技术方案](../technical-solution.md)、[优化方案](../optimization-plan.md)、[判错对象口径](../error-scope.md)、[根因定位](../localization.md)、[命名与维护约定](../maintenance.md)。

| 文档 | 日期 | 内容 | 为什么保留在这里 |
| --- | --- | --- | --- |
| [progress.md](progress.md) | 2026-09-17 至 09-20 | 逐轮进展、框架重构四段、测试数量演进、下一步顺序表 | 与根 README 的时间线重复，但包含 H_t 重构与断点续跑的完整过程记录 |
| [review.md](review.md) | 2026-09-17 | 代码 review：13 项缺陷与 A/B 阶段修复清单 | 缺陷编号（A1–A10、B1–B4）被 `reports/verification_20260917/` 引用 |
| [directions-assessment.md](directions-assessment.md) | 2026-09-17 | 优化方向主张与代码现状的逐条核对 | 「先修尺子」裁定的依据，含边际集极性下限与 patrol 改动的理由 |
| [real-verification-feasibility.md](real-verification-feasibility.md) | 2026-09-17 | 真实验证可行性评估：能做什么、不能做什么 | 明确「没有独立终审集」这一阻断项及其论证 |
| [group-meeting-2026-09-17.md](group-meeting-2026-09-17.md) | 2026-09-17 | 组会汇报：转向 RSI 的理由与目标形态 | 面向外部汇报的叙述版本，与 progress/review 内容重叠 |
| [deck-2026-09-17/](deck-2026-09-17/) | 2026-09-17 | 组会汇报 PPT：生成脚本与成稿 PDF（13 页） | 该汇报的交付物，2026-09-21 由 `artifacts/ppt_build/` 归档至此；渲染中间图已删除 |

实验结果的归档位置见 [reports/README.md](../../reports/README.md)。本目录只放过程性**文档**，不放运行产物。

## 阅读建议

只想了解项目现状时，不要读本目录：根 README 的「当前主文档」与「阅读顺序」已经给出有效入口。需要追溯某个结论的来源、或核实早期缺陷编号与实验背景时，再按上表定位。

已被合并或删除、不再保留副本的文档：

| 原文档 | 处置 |
| --- | --- |
| `docs/architecture.md` | 合并进 [技术方案](../technical-solution.md) 第 1、3、5、6、8 节 |
| `docs/interventions.md` | 合并进 [技术方案](../technical-solution.md) 第 5 节 |
| `docs/evaluation.md` | 合并进 [技术方案](../technical-solution.md) 第 9 节 |
