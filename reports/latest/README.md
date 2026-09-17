# 最新代码验证结果

生成时间（UTC）：2026-09-17T09:06:14.892053+00:00

总状态：**PASS**。pytest：312 通过，0 失败，0 错误，0 跳过。

原始数据：695 个文件，693 个案例；完整性核对通过。

| 检查 | 结果 |
|---|---|
| dataset_integrity | PASS |
| pytest | PASS |
| detector_cli | PASS |
| evolution_cli | PASS |
| capability_audit | PASS |
| smoke | PASS |
| smoke_admission_and_audit | PASS |
| capability_smoke | PASS |
| source_unchanged_during_checks | PASS |

这些结果包括本地 HTTP 命令链回归与 mock 机制验证，不是真实模型准确率或独立泛化结论。

另行执行的真实服务检查见 [../live/README.md](../live/README.md)（两例合成联调）与 [../real/README.md](../real/README.md)（真实数据集小规模路径与重复运行）；两者都有独立时间与源码记录，本命令不会重新调用模型服务。

源码 SHA-256：`e26093fa38df91151ca8882c9dae77ae91bca9c71728dbda93f432e11f33290f`。逐文件摘要见 [source_manifest.json](source_manifest.json)。

复现：在仓库根目录运行 `python scripts/verify.py`。机器摘要见 [summary.json](summary.json)，测试详情见 [pytest.xml](pytest.xml) 和 [pytest.log](pytest.log)。
