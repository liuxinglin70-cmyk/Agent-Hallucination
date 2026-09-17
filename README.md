# Agent Hallucination：检测器底座与自进化框架

本仓库维护两条相互连接的主线：`detector` 独立判断 Agent 轨迹中的结果性幻觉；`evolution` 从稳定失效中提出干预，经程序化评审后部署到底座。模块、命令和配置按职责命名，不按研究迭代版本命名。

## 项目结构

```text
detector/                  检测器底座、配置、单元测试与测试夹具
evolution/                 自进化框架、开发配置、离线闭环、回归测试
datasets/raw/              原始完整数据集，保持原文件不变
datasets/protocols/        开发划分与保守的数据使用边界
datasets/integrity.json    原始目录逐文件 SHA-256
docs/                      架构、干预契约、数据评估协议、维护说明、代码 review 与优化方向
tests/                     跨模块和仓库入口回归测试
scripts/verify.py           可复现的离线验证入口
scripts/live_smoke.py       两个合成案例的真实服务联调入口
scripts/real_path_check.py  真实数据集上的小规模路径与重复运行检查
reports/latest/            最新源码对应的验证报告
reports/live/              独立记录的真实服务联调结果
reports/real/              真实数据集路径与重复运行结果
reports/evolution_real/    真实两轮自进化实验结果
reports/maintenance/       精简的整理记录与历史背景
artifacts/                 运行时输出（自动生成，不入 Git）
pyproject.toml             Python 项目元数据与统一测试配置
```

## 使用入口

Python 3.10 及以上；运行时使用标准库，测试依赖 pytest。建议在仓库根目录使用虚拟环境：

```bash
python -m pip install -e ".[test]"
python -m detector --help
python -m evolution --help
python scripts/verify.py
```

检测单个原始案例（会调用模型服务）：

```bash
python -m detector detect --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json --case-id BFCL/001.json --output-dir artifacts/detector/single
```

开发协议上的进化（会调用模型服务，数据已用于开发）：

```bash
python -m evolution preview --cycle 1 --mini
python -m evolution experiment --mini
python -m evolution registry
python -m evolution verify-ledger
```

默认检测配置为 `detector/configs/autodl.json`，路由模型为 `GLM-5.3-flash`，通过 `AUTODL_API_KEY` 获取凭据，直连 `https://www.autodl.art/api/v1`。`detector/configs/deepseek.json` 是显式可选配置。模型、票数与思考档位只由配置文件决定；仓库 `.env` 保留且不入 Git，只存放凭据，进程环境可覆盖配置中的同名变量。`GLM-5.3-flash` 始终思考，且对任何显式 `thinking` 字段会间歇性返回 400，因此 GLM 路由不发送该字段。

## 阅读顺序

1. [代码架构与执行逻辑](docs/architecture.md)
2. [干预、DSL 与部署契约](docs/interventions.md)
3. [数据与评估协议](docs/evaluation.md)
4. [命名与维护约定](docs/maintenance.md)
5. [代码 Review：机制梳理与缺陷清单](docs/review.md)
6. [跳出无效优化循环：面向 RSI 的优化方向](docs/optimization-directions.md)
7. [最新验证结果](reports/latest/README.md)
8. [当前进展与下一步计划](docs/progress.md)
9. [真实服务联调结果](reports/live/README.md)
10. [真实数据集小规模路径与重复运行结果](reports/real/README.md)
11. [真实两轮自进化实验结果](reports/evolution_real/README.md)

原始数据共 693 个案例、7 个来源。原始目录 695 个文件的摘要保存在 `datasets/integrity.json`。当前默认协议为开发复测；没有预先配置可宣称独立泛化的终审集。离线 smoke 使用 mock 后端，证明机制闭环，不代表真实模型准确率。

2026-09-16 已完成离线回归、真实服务联调、真实数据集小规模路径检查，以及首次真实两轮自进化实验。真实服务使用当前配置的 `GLM-5.3-flash`：两个合成算术案例跑通 `batch → evaluate`；真实数据集上冻结底座后两次运行 6 例，判定 6/6 一致、0 unresolved、12/12 authoritative，并另以单票强制触发验证了召回升级分支。自进化第 1 轮完整跑通真实 `proposer → detector 配对评估 → 四门准入`：产出 3 个合法候选但**全部被门限拒绝，准入 0，真实收益未成立**；第 2 轮被环境中断未完成。这些结果都不是准确率或泛化结论，具体证据边界见相应报告。

2026-09-17 完成代码 review 与修复：13 项会污染结论的缺陷全部修复并配回归测试，测试由 220 增至 244 passed，`scripts/verify.py` 七项校验全 PASS。同时确认真实自进化收益未成立的结构性原因（搜索空间冻结、评估无统计功效、准入终结式无存档、框架自身只由人手优化），并给出面向 RSI 的优化方向。详见 [代码 Review](docs/review.md) 与 [优化方向](docs/optimization-directions.md)。
