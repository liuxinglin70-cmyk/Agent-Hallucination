# Agent Hallucination：检测器底座与自进化框架

本仓库维护两条相互连接的主线：`detector` 独立判断 Agent 轨迹中的结果性幻觉；`evolution` 从稳定失效中提出干预，经程序化评审后部署到底座。模块、命令和配置按职责命名，不按研究迭代版本命名。

## 两个核心目标

1. **检测器能够正确执行幻觉检测任务，并提供可靠的责任步骤定位。** 当前检测器基本有效，作为后续工作的基座持续维护；具体正确率、定位表现和适用范围以实际测量为准。
2. **自进化或自我改进框架能够正确生效，提高基座检测器的幻觉检测和定位能力。** 框架应自动发现改进机会、形成可执行修改、验证收益、部署有效版本，并在后续轮次继承和积累能力；包括但不限于通过设计 RSI 架构实现。

当前工作重点是第二个目标。提示、DSL、上下文、工具、局部代码及流程策略都是可选改进手段，根据实际任务选择；不把某一种检测子方法预设为整个项目的新主线。

后续开发、分析和验证必须服务上述目标：分析做到足以推动实现，实验用于检验改进是否有效；以实际检测与定位收益衡量进展，不能以增加报告、通过工程测试或候选数量替代目标。已有底座保持可用，必要改动随可执行改进推进。

## 当前主文档

- [框架技术方案](docs/technical-solution.md)：只描述当前已实现的模块、接口、执行逻辑和运行方式。
- [自我改进框架优化方案](docs/optimization-plan.md)：后续如何增强改进循环、定位优化、可修改能力、版本继承和自动发布；验证作为配套。

其余长期有效的约定与口径：

- [判错对象口径决定](docs/error-scope.md)：检测器被要求定罪什么（ES-1…ES-6），含与数据集标签的已知分歧目录。
- [根因定位与答案提交步](docs/localization.md)：责任步骤的共享口径。
- [命名与维护约定](docs/maintenance.md)：稳定名称、迁移边界与输出规范。

历史文档集中在 [docs/history/](docs/history/README.md)：代码 review、真实验证可行性评估、方向对照评估、组会汇报与逐轮进展。其中的结论只对当时代码基线有效，不作为现状依据。[早期 RSI 优化方向与试点背景](docs/optimization-directions.md) 同样属历史方案，保留是因为冻结的研究 manifest 逐字引用其章节。

未来工作方向以本节核心目标和上述优化方案为准。下方历史报告与早期方案保留原始上下文，不覆盖当前目标和实施顺序。

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
docs/                      技术方案、优化方案、口径与维护约定
docs/history/              历史过程文档（review、可行性评估、组会、逐轮进展）
tests/                     跨模块和仓库入口回归测试
scripts/verify.py           可复现的离线验证入口
scripts/live_smoke.py       两个合成案例的真实服务联调入口
scripts/real_path_check.py  真实数据集上的小规模路径与重复运行检查
reports/                   实验证据归档（结构、归档约定与证据等级见 reports/README.md）
artifacts/                 运行时输出（自动生成，不入 Git，不长期保存）
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

先读当前有效文档，再按需查阅历史记录：

1. [框架技术方案](docs/technical-solution.md)——当前实现、接口与运行方式
2. [自我改进框架优化方案](docs/optimization-plan.md)——后续改造路线与实施顺序
3. [判错对象口径决定](docs/error-scope.md)、[根因定位与答案提交步](docs/localization.md)——判定与定位口径
4. [命名与维护约定](docs/maintenance.md)——命名、迁移与输出规范
5. [最新验证结果](reports/latest/README.md)——当前源码的离线验证状态

历史记录按主题查阅（结论只对当时代码有效）：

6. [实验报告归档说明](reports/README.md)——目录结构、归档约定与各报告的证据等级
7. [历史文档索引](docs/history/README.md)——review、可行性评估、方向对照、组会汇报、逐轮进展
8. [真实服务联调结果](reports/live/README.md)、[真实数据集小规模路径检查](reports/real/README.md)、[真实两轮自进化实验结果](reports/evolution_real/README.md)
9. [证据覆盖优化与预检记录](reports/optimization_20260918/README.md)、[判错对象口径与固定预检复跑记录](reports/error_scope_20260918/README.md)
10. [RSI 试点 P1/P2 与第 2–6 轮记录](reports/rsi_pilot_20260919/README.md)

`artifacts/` 是自动生成的运行时目录（缓存、registry、账本、检查点），不进入 Git 也不长期保存；**需要保留的实验结果一律归档到 `reports/`**，归档约定见 [reports/README.md](reports/README.md)。

原始数据共 693 个案例、7 个来源。原始目录 695 个文件的摘要保存在 `datasets/integrity.json`。当前默认协议为开发复测；没有预先配置可宣称独立泛化的终审集。离线 smoke 使用 mock 后端，证明机制闭环，不代表真实模型准确率。

## 历史进展摘要

以下按日期记录已完成的工作与当时的结论边界。**这些是历史读数，不代表当前代码状态**；逐轮细节见[历史文档索引](docs/history/README.md)，实验证据见对应 `reports/`。

2026-09-16 已完成离线回归、真实服务联调、真实数据集小规模路径检查，以及首次真实两轮自进化实验。真实服务使用当前配置的 `GLM-5.3-flash`：两个合成算术案例跑通 `batch → evaluate`；真实数据集上冻结底座后两次运行 6 例，判定 6/6 一致、0 unresolved、12/12 authoritative，并另以单票强制触发验证了召回升级分支。自进化第 1 轮完整跑通真实 `proposer → detector 配对评估 → 四门准入`：产出 3 个合法候选但**全部被门限拒绝，准入 0，真实收益未成立**；第 2 轮被环境中断未完成。这些结果都不是准确率或泛化结论，具体证据边界见相应报告。

2026-09-17 完成代码 review 与修复：13 项会污染结论的缺陷全部修复并配回归测试，测试由 220 增至 244 passed，`scripts/verify.py` 七项校验全 PASS。同时确认真实自进化收益未成立的结构性原因（搜索空间冻结、评估无统计功效、准入终结式无存档、框架自身只由人手优化），并给出面向 RSI 的优化方向。详见 [代码 Review](docs/history/review.md) 与 [优化方向](docs/optimization-directions.md)。

同日完成两项评估侧优化，测试由 244 增至 **251 passed**：边际集（G）加极性下限，使两种 claim 方向都可被计分；巡检改测已部署版本并保留裸底座对照，把新引入退化、常驻残余与已修复分离为可读字段。依据是真实第 1 轮出现的「做出了有效修改却读数为 0」——G 中没有被修改机制的案例。逐条对照与理由见 [方向对照评估](docs/history/directions-assessment.md)。

2026-09-17 完成实验框架的结构性重构，测试增至 **312 passed**，离线验证九项全 PASS：`evolution/harness.py` 把 H_t = (G, C, T, M, R) 做成可装载、双层哈希可核验的清单与代际差异；`evolution/gaps.py` 把不可执行的稳定失败族从「当轮丢弃」变为去重后备清单；`evolution/memory.py` 提供三类经验存储。在此基础上，cycle 报告新增 `instrument` 块按轮报告「本轮能计分哪类机制」（`gain_opportunity` / `unmeasurable_polarities`），终态区分为 `admitted` / `rejected` / `not_evaluated` / `unresolved`（「未测量」不再等于「已证伪」），已准入的程序性经验与开放缺口作为草拟输入交给 proposer（记入账本 `proposal_memory`），覆盖不足时按 `coverage_response` 策略改用替代评估集或跳过（默认 `strict`，行为与改动前一致），并修复了断点续跑：契约去除 registry 哈希，已提交本轮准入后中断也能在同一账本继续，观察按 `(arm, case)` 复用。清单、缺口与经验不参与门判定；记忆「被供给」已由测试证明，**替代评估集模式实测可改变准入，故默认关闭**。详见 [H_t 基座说明](evolution/README.md#h_t-基座) 与 [当前进展](docs/history/progress.md)。

2026-09-18 完成证据覆盖优化与固定划分预检，测试由 312 增至 **385 passed**。语料补入四项来源记录，固定 S/G 的可用证据覆盖由 0/10 变为 1/10，其余缺口逐例保留、没有放宽匹配；修复 corpus 后端未自动读取数据集、缓存未指纹化来源正文两处接线缺陷；五个定位阶段统一到同一份答案提交步口径（[根因定位](docs/localization.md)）。真实预检为 2 TP + 1 TN，未达 2 TP + 2 TN，按协议停止，未启动两轮实验，也未重抽种子、降门限或切换诊断集。详见 [本轮报告](reports/optimization_20260918/README.md)。

2026-09-18 完成判错对象口径决定与固定预检复跑，测试由 385 增至 **392 passed**。上一轮预检被 `protected_tn=1 < 2` 阻断，逐例复核发现三例哨兵的判定取决于「检测器被要求定罪什么」，因此先把它写成版本化决定（[判错对象口径](docs/error-scope.md)），再做唯一一处定罪相关改动——观测互相冲突时不得用世界知识挑边定罪。据此冻结新协议复跑同一预检，真实复跑又暴露两个运行缺陷（召回扫描器输出在 8192 上限被截断；证据标签把缓存文件名推到 268 字符、越过 Windows 路径上限），第三个运行限制由实测确定（`SmolAgents/019` 端到端需 1480 秒而原案例时限为 900 秒）。四次尝试逐次冻结、逐次复跑：预检由 2 TP + 1 TN 变为 **2 TP + 2 TN 通过**，缺陷注入审计通过且两个注入缺陷均为实质性拒绝，随后在预检第二道检查按协议停止——增益集 4 例全部判对，诊断出的召回族无任何可计分机会。**本轮未产生候选或准入，门限、划分、种子与标签一律未动**；完整记录与独立验收见 [本轮报告](reports/error_scope_20260918/README.md)。

2026-09-19 启动 RSI 试点并完成 P0/P1/P2（[早期优化方案](docs/optimization-directions.md) §6–8）：P0 修复 HybridProposer 未转发 active_entries 与拒绝反馈的缺口；P1 以 19 次 case-evaluation 冻结训练机制 `quoted_content_vs_delivered`（两条无口径争议的真实漏判 `BFCL/002`、`BFCL/153`）；P2 的最小研究入口（`evolution/research.py`，离线 15 项测试先行）跑通「草拟→修订→筛查→冻结配对复核→候选未用开发验证」：两条路线修订后均修复训练失败，但未用开发验证未通过（v002 把边界例 013 的合法重编号定罪——增益与代价同走 judge→defense 链；v005 第 2 轮丢失 024 既有检出——temperature 0 下边界案例轮间方差），部署保持不变。测试增至 **414 passed**。同日冻结第 2 轮（v006）协议：机制监督带 P2 两条失败反馈下修 not_when，单路线 C，复核与未用验证各 3 次重复，预算另立。详见 [RSI 试点报告](reports/rsi_pilot_20260919/README.md)。
