# 仓库整理与提交核对（2026-10-19）

当前入口由根 README 导航：技术合同、研究方向、实验日志、结果索引、组会材料、实施台账与脚本清单各自维护。历史方案保存在 docs/archive，现有脚本路径、原始数据、冻结标准和原始运行证据保留。

本次新增[脚本入口](../../../scripts/README.md)，覆盖 scripts 根目录全部28个Python文件及回环保护模块；pytest插件与独立命令分开说明，移除技术文档中失效的固定数量清单。容量原件复制到[版本化证据](../api-capacity-20261019/report.md)，根 README 与台账链接改指可推送路径。此前未提交的历史归档、检测器修复、测试和并行验证入口一并提交，未重新启动真实服务。

## 验证

- [本次定向回归](targeted-pytest.xml)：200项通过、无失败/错误/跳过，42.05秒，覆盖配置、action forecast、API、定罪听证来源、P1/P2修复、并行验证入口、成本和调用分析。
- [整理核对](validation.json)：420个本地链接均存在；脚本入口无遗漏；34份新增结果复制件与本地来源SHA-256一致；待删除文档均有历史归档；成本校准块原字节保留；92个生产模块无重复定义、边界违规或导入环。
- [原完整验收摘要](prior-full-summary.json)及[原源码清单](prior-source-manifest.json)：2748项通过、19项门禁通过，对应 f953e0932b2bb7ad4f2a35c62ae4cd1a1960d4d403911d75edd3f1a86f4f918d。与当前源码相比，只有 detector/config.py、detector/configs/autodl.json 改变及 scripts/probe_api_capacity.py 新增，另有 tests/test_p2_2_multi_deliverable.py 只移除末尾空行，未删除源码文件。此前功能修复和离线验证实现与完整验收时一致。新增证据目录设置 Git -text，避免换行转换破坏清单摘要。

本次没有重跑全套2748项，不能称当前模型配置已经通过全套验收；模型容量记录也不证明检测准确率。完整历史测试日志仍在本地 artifacts/code_quality。新文档及本清单不改写原报告。

[逐文件来源与摘要](manifest.json)。凭据、原始数据及本地缓存不纳入提交。
