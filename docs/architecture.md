# 代码架构与执行逻辑

## 模块边界

依赖方向为 `evolution → detector`。底座独立运行，不反向导入进化框架。数据集只在加载与评估层出现，原始标签不进入检测器输入。进化生成的候选只能经 `detector.interventions` 的 registry 合同进入检测路径。

| 职责 | 文件 | 主要输入与输出 |
|---|---|---|
| 检测公开入口与编排 | `detector/api.py` | question/history、registry → 检测结果 |
| 数据投影和证据包 | `detector/dataset.py`、`packet.py` | 原始轨迹 → 无标签输入、事件/工具/交付锚点 |
| 共享判断语义 | `detector/semantics.py` | MATERIALITY 法则及各阶段提示词 |
| 结果判断 | `detector/judge.py` | 完整轨迹 → 结果性幻觉判断 |
| 候选召回与因果升级 | `detector/recaller.py` | 阴性/被驳回案例 → 候选及因果升级判断 |
| 定位与辩护 | `detector/step_tracer.py`、`defense.py` | 听证候选 → 最早因果步、辩护结论 |
| 裁决与结果合同 | `detector/adjudication.py`、`result.py` | 各阶段证据 → 终态与 authority |
| 扩展执行 | `detector/interventions.py`、`predicates.py`、`predicate_dsl.py` | registry → 有界干预与执行证据 |
| 服务调用 | `detector/provider.py`、`provider_worker.py`、`config.py`、`env.py` | 路由、超时、重试、多票、调用审计 |
| 批量和评估 | `detector/runner.py`、`evaluation.py`、`cli.py` | 案例清单 → 运行目录与评估指标 |
| 进化编排 | `evolution/engine.py` | split、backend、proposer → cycle 报告 |
| 机制诊断与候选 | `evolution/worksheet.py`、`proposer.py` | 重复失效 → 失败族、DSL 接地信息与提案 |
| 门控与指标 | `evolution/gates.py`、`metrics.py` | 配对结果 → 准入证据 |
| 持久状态 | `evolution/registry.py`、`ledger.py` | 预提交、快照、哈希链、淘汰与回滚 |
| 数据与执行后端 | `evolution/splits.py`、`backends.py` | 开发/正式划分、live/mock/cached 执行 |
| 元评估与能力面 | `evolution/meta_eval.py`、`capability_surface.py` | 门控行为、缺陷审计、可执行能力清单 |

## 检测流程

1. `Detector.detect` 校验输入、构建 packet、加载并编译干预计划。未提供 registry 时使用空计划或显式配置的 registry。
2. Outcome Judge 读取完整轨迹，按共享 MATERIALITY 法则判断交付结果。Judge、Causal Upgrade 和 Defense 支持独立多票，具体路由和票数由配置控制。
3. Judge 阳性先进入听证；阴性若存在分歧、弃权或低置信则进入 Recall Scanner。一致高置信阴性通常直接结束，但匹配的 `recall_trigger` 可以开启召回。
4. Scanner 只提出候选，不直接定罪。Causal Upgrade 要求候选同时具备内容错误和结果因果性；通过的候选按最早步骤排序。
5. 每场听证独立执行 Step Tracer 和 Defense。Judge 阳性被有证据驳回后，可转召回网继续审理。某场有效听证确认错误即可形成阳性终态。
6. 确定性 adjudication 根据封闭状态和机械 guard 生成终态。辩护未决或故障不会自动翻案，而会降低 authority；Judge 失败会产生 unresolved。
7. `proof` 保留 Judge、召回、升级、各场听证、机械证据与部署触发记录；结果评估区分终态、降级判断和基础设施未决。

## 一轮进化

1. `EvolutionEngine` 加载配置、分区、活跃库、账本与后端，检查来源和缓存身份。
2. 在 patrol 上运行裸底座，必要时复测与补采，寻找重复的机制性失效。`worksheet` 区分可修复的检测失效与基础设施问题。
3. 失败族诊断提供内置谓词命中情况、typed views、共同字面量、可行干预路由。`HybridProposer` 结合 LLM 草拟与受约束模板；历史拒绝反馈用于抑制原样重提。
4. `structural_gate` 校验合同、去重、禁止案例 ID 泄漏；新 DSL 必须在失败族成员上实际执行并一致命中所声明的能力。
5. `harmlessness_gate` 在 sentinel 配对比较，不允许超出配置的新 FN、FP 或 unresolved；只用程序化结果与标签评审。
6. `behavior_gate` 验证干预实际触发且产生与声明一致的行为证据。
7. 边际门 `meets_m` 和 engine 中的组合选择在 gain 集计算净收益及新错误，处理联合候选、贪心子集和 leave-one-out 归因。
8. 合格组合经 registry 预提交与限额检查后写入活跃库；账本记录决策。后续治理按贡献淘汰与回滚，恢复一致的 registry/state。

报告中的门字段为 `structural`、`harmlessness`、`behavior`、`m`；`m` 是 marginal 的数学简称，不是软件版本。

## 配对重放和 fresh 对照

`LiveDetectorBackend` 对可机械重放的候选冻结未受影响的阶段，听证按 `(source, candidate_id)` 匹配。缺失或被干预遮蔽的冻结条目会被拒绝。对改变模型调用或召回控制流的干预，`requires_fresh_evaluation` 选择 fresh 对照与 fresh 候选臂，避免把缓存结果和重新采样结果直接当成因果收益。

缓存身份包括代码摘要、配置摘要、输入与 registry 身份。mock 后端仅用于机械回归；真实模型能力应来自真实后端的独立测量。

## 运行与验证分层

`scripts/verify.py` 运行全部 pytest、本地 HTTP 子进程集成测试、命令帮助、能力面审计以及两个 mock smoke。测试数据、registry、账本和临时配置使用隔离目录；不覆盖日常真实运行状态。结果按源码逐文件摘要绑定到 `reports/latest/`，验证期间代码变化会使总检查失败。

`scripts/live_smoke.py` 独立使用真实 provider，生成两个小型合成案例，经公开 `batch` 和 `evaluate` 命令检查完整结果链。报告记录实际模型路由、超时、案例终态、authority、阶段状态、代码摘要及运行目录；不写入凭据。它没有运行 LLM proposer 或真实进化准入，不能证明自进化收益。

命令级结果也属于执行合同：`verify-ledger` 的退出码反映哈希链有效性；`smoke` 的退出码反映实际准入、审计、cycle 完成和账本有效性，而不是仅表示 Python 进程运行结束。
