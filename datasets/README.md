# 数据目录

`raw/` 是原始完整数据，保持原文件与原目录结构；不进入 Git。`integrity.json` 保存全部 695 个文件的 SHA-256，包含 693 个 AgentHallu 案例。

`protocols/development.json` 保存当前开发与复测案例 ID；`protocols/development_exposure.json` 保存保守的数据使用边界。旧派生学习案例、策略候选和重复 split 已移出活动仓库。

检测器输入严格只有 `question/history`，原始标签通过 `detector.dataset.project_record` 隔离。完整协议见 [框架技术方案](../docs/technical-solution.md) 第 9 节。
