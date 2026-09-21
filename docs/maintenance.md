# 命名与维护约定

## 稳定名称

- Python 包与命令固定为 `detector`、`evolution`；功能扩展在模块内完成，禁止复制成带研究版本号的新包。
- 文件按职责命名：`predicate_dsl.py`、`gates.py`、`registry.py` 等。配置按用途或供应商命名：`development.json`、`autodl.json`、`deepseek.json`。
- 准入函数为 `structural_gate`、`harmlessness_gate`、`behavior_gate`；边际判断为 `meets_m`。配置门限使用相应语义前缀。
- 日期、实验 ID 放入运行目录和报告元数据，不放入业务包名。
- schema 的 `.v1`/`.v2`、包发布版本、模型官方 ID 和供应商 `/v1` 路径是数据合同或外部接口版本，应保留；它们不是历史研究模块的命名方式。

## 文档布局

当前有效文档只有五份，均位于 `docs/` 顶层：[技术方案](technical-solution.md)（实现与运行合同）、[优化方案](optimization-plan.md)（后续改造）、[判错对象口径](error-scope.md)、[根因定位](localization.md)、本文。根 README 的「当前主文档」与「阅读顺序」是唯一入口。

过程性文档集中在 `docs/history/`，由[索引](history/README.md)说明每份的保留理由与适用基线：`progress.md`、`review.md`、`directions-assessment.md`、`real-verification-feasibility.md`、`group-meeting-2026-09-17.md`。[optimization-directions.md](optimization-directions.md) 属历史方案，因冻结的研究 manifest 逐字引用其章节而保留在原路径。

整理原则：描述现状的技术文档不按迭代版本并存——`architecture.md`、`interventions.md`、`evaluation.md` 曾分别描述同一实现的局部，2026-09-21 合并进 `technical-solution.md` 并删除；历史文档只标注状态、不改写正文，其相对链接随移动更新。

## 迁移与兼容边界

旧 `detector_v2`、`evolution_v3` 命令及导入已移除，不保留同功能别名。对应类改为 `LiveDetectorBackend`；环境变量前缀改为 `DETECTOR_`。旧 registry/schema 命名空间不作为当前运行输入；其哈希与代码身份不可混用。测试需要的两份 registry 已整理为 `detector/tests/fixtures/` 的专用数据，并按当前合同重建哈希。

根目录 `tests/` 只保留跨模块回归；底座与框架自身测试分别留在模块的 `tests/`。原依赖已移除旧框架的测试、旧实验脚本、下载网页和重复设计文档移出活动仓库。

旧缓存、活跃库、日志、实验输出与历史配置位于仓库外的 `../cleanup_quarantine/`，不会被导入、默认配置或测试发现。使用隔离目录是因为自动审批检查拒绝了批量删除；该目录用于可恢复清理，不是项目运行依赖。`.git`、本地 `.env` 和全部原始数据保留。

## 输出与复现

运行输出只写入 `artifacts/`，不混入实现包和原始数据目录。**`artifacts/` 是自动生成的运行时目录，不进入 Git，也不作为长期证据保存**；需要保留的实验结果归档到 `reports/` 下的实验目录，归档约定与证据等级见 [reports/README.md](../reports/README.md)。2026-09-21 完成过一次这样的整理：原始产物从 `artifacts/` 回收进 `reports/verification_20260917/`、`reports/evolution_real/run_20260916/`、`reports/rsi_pilot_20260919/h0_baseline/`，其余运行残留删除。报告中的 `artifacts/...` 路径若属运行当时的命令记录则保留原样，指向证据的路径改指归档副本。

`reports/latest/` 保留最近一次完整验证，包含 pytest XML、日志、能力清单、两个 smoke 报告、数据完整性报告和源码摘要。`python scripts/verify.py` 会重新生成结果；测试过程中若源码改变，总验证失败。

仓库整理前主线测试为 188 项通过。整理增加入口、配置、分区与依赖边界回归；最终数量以 `reports/latest/summary.json` 为准。仓库整理阶段未调用模型服务，也未将历史准确率当作最新代码结果。

后续验证更新（2026-09-16）：在整理完成后，另行执行了两例真实模型服务联调，记录于 `reports/live/`。新增本地 HTTP 命令链及失败退出码回归，修复了 `verify-ledger`、`smoke` 在检查失败时仍可能返回成功的问题。离线验证与真实联调各自保存时间、源码和证据范围，不相互替代。
