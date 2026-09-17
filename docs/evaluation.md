# 数据与评估协议

## 原始数据完整性

原始目录 `datasets/raw/` 保持不变，包含 695 个文件，其中 `agenthallu/AgentHallu/` 有 693 个 JSON 案例：

| 来源 | 案例数 |
|---|---:|
| BFCL | 164 |
| Camel | 93 |
| Magentic_One | 94 |
| Octotools | 47 |
| OpenDeepSearch | 100 |
| OpenManus | 104 |
| SmolAgents | 91 |

`datasets/integrity.json` 保存整理前的逐文件 SHA-256。`python scripts/verify.py` 比较文件集合及全部摘要，任何新增、缺失或内容变化都使完整性检查失败。该检查证明与整理前本地原始目录一致，不替代上游发布方校验。

## 标签边界与开发协议

检测器只接收 `question/history`；`dataset.project_record` 投影原始记录。gold 由评估和进化门控侧读取，LLM 不承担晋升评分。`datasets/protocols/development.json` 仅保存案例 ID，默认开发池 30 例、复测集 10 例。sentinel/gain/patrol 按配置和种子拆分；它们已经参与历史开发，复测不是独立泛化验证。

`development_exposure.json` 保守地将原始 693 个案例列为不能直接认证为新独立终审的范围。这不表示每个案例都调用过模型，而是保留既有开发、协议选择和历史污染的不确定性。删除缓存或运行目录不能消除此边界。

## 正式评估入口

默认 `split_manifest=null`。`final` 与 `full-self-evolution` 不会自动取旧划分；调用方必须提供独立审计的正式 manifest、适配的数据根目录与新的运行目录。正式 manifest 包含 `evolution_pool.cases` 和 `final_verification.records`（case_id/gold）。程序检查唯一性、开发/终审无交集、历史开发记录重叠及 read-once 标记。

显式正式配置还需设置 `final_evaluation.enabled=true`。全流程入口需设置 `experiment.mode=full_self_evolution`，并可指定 `expected_evolution_cases`、`expected_evaluation_cases`、`expected_dataset_cases` 校验数量。现有实现从 manifest 读取终审记录；正式配置属于评估受信任输入，不能据此宣称实现了外部标签服务隔离。

原始语料不能因换 case ID 而被视为独立数据。自定义正式协议必须同时保留准确的数据使用边界；配置引用的历史记录缺失会报错，而不会静默绕过。

## 证据等级

| 结果 | 能说明什么 |
|---|---|
| pytest | 输入合同、检测路径、投票、门控、缓存、账本、DSL 等离线回归 |
| 本地 HTTP 命令链测试 | 真实子进程执行 detect/batch/evaluate、HTTP 传输、落盘与标签隔离；响应由本地固定服务提供 |
| smoke | mock 条件下提案准入、缺陷拒绝与状态闭环 |
| capability-smoke | 合成数据上新谓词经 registry 执行并产生可观测增益 |
| 真实服务合成案例联调 | 当前 endpoint、凭据、模型输出与检测流程可接通；不能估计复杂轨迹准确率 |
| 开发集真实模型结果 | 当前已使用案例上的诊断读数 |
| 独立正式评估 | 满足数据隔离与真实后端测量后才可讨论泛化 |

最新代码结果统一写入 `reports/latest/`。历史结果摘要只在 `reports/maintenance/` 中作为背景保留，不与当前回归混合，也不沿用旧文档的通过数量或准确率。

真实服务联调独立写入 `reports/live/`，避免把离线总检查通过误认为外部服务也被重新测过。2026-09-16 的两例联调均产生正确的 authoritative 终态，无 unresolved、无 degraded，错误案例定位到第 1 步。两例不来自原始 693 案例，没有读取或重新划分终审数据。

下一阶段先在已知开发数据上检查真实提案与各门表现，再使用独立新增的数据评估泛化。开发复测和独立评估必须分别报告，详见 [进展与计划](progress.md)。
