# Agent Hallucination：检测器底座与自进化框架

本仓库维护两条相互连接的主线：`detector` 独立判断 Agent 轨迹中的结果性幻觉；`evolution` 从稳定失效中提出干预，经程序化评审后部署到底座。模块、命令和配置按职责命名，不按研究迭代版本命名。

## 项目结构

```text
detector/                  检测器底座、配置、单元测试与测试夹具
evolution/                 自进化框架、开发配置、离线闭环、回归测试
  harness.py               H_t = (G, C, T, M, R) 清单、哈希与代际差异
  gaps.py                  能力缺口后备清单（不可执行失败族）
  memory.py                三类经验存储与有界检索（情景/程序性/改进）
datasets/raw/              原始完整数据集，保持原文件不变
datasets/protocols/        开发划分与保守的数据使用边界
datasets/integrity.json    原始目录逐文件 SHA-256
docs/                      架构、干预契约、数据评估协议、维护说明、代码 review、优化方向与方向对照评估
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
7. [方向对照评估：文档主张与代码现状](docs/directions-assessment.md)
8. [真实验证可行性评估](docs/real-verification-feasibility.md)
9. [最新验证结果](reports/latest/README.md)
10. [当前进展与下一步计划](docs/progress.md)
11. [真实服务联调结果](reports/live/README.md)
12. [真实数据集小规模路径与重复运行结果](reports/real/README.md)
13. [真实两轮自进化实验结果](reports/evolution_real/README.md)
14. [根因定位与答案提交步](docs/localization.md)
15. [证据覆盖优化与预检记录](reports/optimization_20260918/README.md)
16. [判错对象口径决定](docs/error-scope.md)
17. [口径决定与固定预检复跑记录](reports/error_scope_20260918/README.md)

原始数据共 693 个案例、7 个来源。原始目录 695 个文件的摘要保存在 `datasets/integrity.json`。当前默认协议为开发复测；没有预先配置可宣称独立泛化的终审集。离线 smoke 使用 mock 后端，证明机制闭环，不代表真实模型准确率。

2026-09-16 已完成离线回归、真实服务联调、真实数据集小规模路径检查，以及首次真实两轮自进化实验。真实服务使用当前配置的 `GLM-5.3-flash`：两个合成算术案例跑通 `batch → evaluate`；真实数据集上冻结底座后两次运行 6 例，判定 6/6 一致、0 unresolved、12/12 authoritative，并另以单票强制触发验证了召回升级分支。自进化第 1 轮完整跑通真实 `proposer → detector 配对评估 → 四门准入`：产出 3 个合法候选但**全部被门限拒绝，准入 0，真实收益未成立**；第 2 轮被环境中断未完成。这些结果都不是准确率或泛化结论，具体证据边界见相应报告。

2026-09-17 完成代码 review 与修复：13 项会污染结论的缺陷全部修复并配回归测试，测试由 220 增至 244 passed，`scripts/verify.py` 七项校验全 PASS。同时确认真实自进化收益未成立的结构性原因（搜索空间冻结、评估无统计功效、准入终结式无存档、框架自身只由人手优化），并给出面向 RSI 的优化方向。详见 [代码 Review](docs/review.md) 与 [优化方向](docs/optimization-directions.md)。

同日完成两项评估侧优化，测试由 244 增至 **251 passed**：边际集（G）加极性下限，使两种 claim 方向都可被计分；巡检改测已部署版本并保留裸底座对照，把新引入退化、常驻残余与已修复分离为可读字段。依据是真实第 1 轮出现的「做出了有效修改却读数为 0」——G 中没有被修改机制的案例。逐条对照与理由见 [方向对照评估](docs/directions-assessment.md)。

2026-09-17 完成实验框架的结构性重构，测试增至 **312 passed**，离线验证九项全 PASS：`evolution/harness.py` 把 H_t = (G, C, T, M, R) 做成可装载、双层哈希可核验的清单与代际差异；`evolution/gaps.py` 把不可执行的稳定失败族从「当轮丢弃」变为去重后备清单；`evolution/memory.py` 提供三类经验存储。在此基础上，cycle 报告新增 `instrument` 块按轮报告「本轮能计分哪类机制」（`gain_opportunity` / `unmeasurable_polarities`），终态区分为 `admitted` / `rejected` / `not_evaluated` / `unresolved`（「未测量」不再等于「已证伪」），已准入的程序性经验与开放缺口作为草拟输入交给 proposer（记入账本 `proposal_memory`），覆盖不足时按 `coverage_response` 策略改用替代评估集或跳过（默认 `strict`，行为与改动前一致），并修复了断点续跑：契约去除 registry 哈希，已提交本轮准入后中断也能在同一账本继续，观察按 `(arm, case)` 复用。清单、缺口与经验不参与门判定；记忆「被供给」已由测试证明，**替代评估集模式实测可改变准入，故默认关闭**。详见 [H_t 基座说明](evolution/README.md#h_t-基座) 与 [当前进展](docs/progress.md)。

2026-09-18 完成证据覆盖优化与固定划分预检，测试由 312 增至 **385 passed**。语料补入四项来源记录，固定 S/G 的可用证据覆盖由 0/10 变为 1/10，其余缺口逐例保留、没有放宽匹配；修复 corpus 后端未自动读取数据集、缓存未指纹化来源正文两处接线缺陷；五个定位阶段统一到同一份答案提交步口径（[根因定位](docs/localization.md)）。真实预检为 2 TP + 1 TN，未达 2 TP + 2 TN，按协议停止，未启动两轮实验，也未重抽种子、降门限或切换诊断集。详见 [本轮报告](reports/optimization_20260918/README.md)。

2026-09-18 完成判错对象口径决定与固定预检复跑，测试由 385 增至 **392 passed**。上一轮预检被 `protected_tn=1 < 2` 阻断，逐例复核发现三例哨兵的判定取决于「检测器被要求定罪什么」，因此先把它写成版本化决定（[判错对象口径](docs/error-scope.md)），再做唯一一处定罪相关改动——观测互相冲突时不得用世界知识挑边定罪。据此冻结新协议复跑同一预检，真实复跑又暴露两个运行缺陷（召回扫描器输出在 8192 上限被截断；证据标签把缓存文件名推到 268 字符、越过 Windows 路径上限），第三个运行限制由实测确定（`SmolAgents/019` 端到端需 1480 秒而原案例时限为 900 秒）。四次尝试逐次冻结、逐次复跑：预检由 2 TP + 1 TN 变为 **2 TP + 2 TN 通过**，缺陷注入审计通过且两个注入缺陷均为实质性拒绝，随后在预检第二道检查按协议停止——增益集 4 例全部判对，诊断出的召回族无任何可计分机会。**本轮未产生候选或准入，门限、划分、种子与标签一律未动**；完整记录与独立验收见 [本轮报告](reports/error_scope_20260918/README.md)。
