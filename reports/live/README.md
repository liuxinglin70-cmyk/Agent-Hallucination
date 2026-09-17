# 真实服务联调结果

执行日期：2026-09-16。结果：**PASS**。

从当前检测配置加载真实 AutoDL 路由，Judge/Defense/Recall 三个路由均使用 `GLM-5.3-flash`（`thinking.type = low`）。执行真实 `batch` 和 `evaluate` 子进程，不替换检测器或 provider。

| 合成案例 | 预期 | 实际 | Authority | 定位 |
|---|---|---|---|---|
| 2 + 2 回答 4 | clean | verified_clean | authoritative | 不适用 |
| 2 + 2 回答 5 | error | verified_error | authoritative | 第 1 步，符合预期 |

2/2 得到预期终态；0 unresolved、0 degraded。批量命令和评估命令退出码均为 0。clean 案例只执行了 Judge；error 案例执行了 Judge、Step Tracer 与 Defense，其中 Step Tracer 返回 `provider_error` 后由 Defense 维持了正确终态。本组合成数据未触发 Recall 与 Causal Upgrade，因此这两个阶段的真实服务路径不能由这两例确认。

保留配置票数；2 路并发；请求超时 45 秒，单案例超时 180 秒；传输重试与 vote top-up 设为 0。报告中的 2/2 仅表示两个简单合成案例通过，不是数据集准确率，更不是自进化收益。真实数据集上的小规模路径与重复运行检查另见 [../real/README.md](../real/README.md)。

- [机器摘要、路由与源码记录](summary.json)
- [两例评估结果](evaluation.json)
- [批量日志](batch.log)
- [评估日志](evaluate.log)

复现：在仓库根目录运行 `python scripts/live_smoke.py`。该命令调用真实模型服务并生成新时间戳的运行目录；凭据通过配置引用的环境变量读取。离线 `scripts/verify.py` 不会重新执行此检查。
