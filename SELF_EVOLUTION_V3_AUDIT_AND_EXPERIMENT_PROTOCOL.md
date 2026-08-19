# SEHD-RISE3 系统审查与真实实验协议

状态：2026-08-19 审查后版本。本文是当前实现状态、已知限制和真实实验准入条件的权威说明，优先于旧实验计划和历史结果摘要。

## 1. 结论摘要

`detector_v2` 与 `evolution_v3` 已经具备可运行的检测和干预式自进化原型，但当前不能直接把历史 393/300 资产作为确认性终审证据。

当前判断：

- 可以运行真实 provider 的工程性和探索性实验。
- 不能直接运行并宣称对未见数据具有确认性泛化提升。
- 旧 `tuning100` 与 `phase2 final_verification` 重叠 44 例，代码现在会主动阻断该 formal split。
- 当前 runtime registry 为空；`final` 明确拒绝空 active registry，避免空库对空库的伪验证。
- 50 例历史验证主要是 frozen-stage replay，不能替代 fresh end-to-end efficacy 实验。
- 当前历史结果中的 `78% -> 84%` 只能作为探索性机制证据，不能作为独立外部泛化结论。

## 2. 本轮已修复的问题

### 检测执行层

- 严格校验 gold 标签、result schema、`runtime_valid`、`occurrence_state` 与 authority 一致性。
- 单 case 异常转换为明确的 invalid/unresolved 结果，不再中止整个 batch。
- upgrade candidate 禁止重复 row、越界 step、非升级 row 携带 step。
- frozen replay 经过与 live path 相同的 contract 校验，并拒绝冻结正在被 intervention 修改的阶段。
- 同时传入 `frozen_judge` 和 `frozen_stages` 会直接报错。
- tracer 的责任 step 必须由对应事件或工具交互 evidence ref 支持。
- guard 的 executable exclusion facts 必须存在且非空。
- 缓存结果采用原子写；缓存 namespace 包含 detector/evolution 源码指纹。

### 自进化层

- 联合候选采用最终配置上的 leave-one-out 归因，不再把组合总收益复制给每个成员。
- proposal batch 校验 cycle、patrol、failure family、唯一 ID 和 joint group 契约。
- proposer LLM 调用预算实际递减。
- rollback 同步恢复 active registry、intervention marginal history 和 net contribution history。
- active registry 与 state 维护 hash 一致性检查。
- final holdout 只有在评估成功提交后才消费 read-once marker。
- paired metrics 拒绝重复或缺失 case。

### 实验与产物层

- formal split 检查历史 development manifest 与 final 的交集。
- batch run 写入 manifest，禁止同一 run ID 在配置、case 或 intervention 变化后复用旧结果。
- batch case ID 的落盘路径碰撞会被拒绝。
- 所有关键 JSON 产物采用原子写。

## 3. 验证结果

```powershell
python -m pytest -q
```

当前结果：`189 passed`。

```powershell
python -m evolution_v3 smoke
```

离线 smoke 通过。它使用 mock/oracle toy backend，只能验证门控、registry、ledger 和状态机制，不能作为 detector 能力指标。

当前历史 active registry 可按 executable contract 加载，共 5 条干预。默认 runtime registry 仍为空，这是当前 workspace 的运行状态，不代表历史 active registry 已部署到 runtime path。

## 4. 当前实验资产状态

| 资产 | 数量 | 用途 | 当前状态 |
|---|---:|---|---|
| phase2 evolution pool | 393 | evolution development | 可用于开发，但不能与历史触碰集合混合宣称独立验证 |
| phase2 final verification | 300 | 原计划终审 | 与 tuning100 重叠 44 例，已污染 |
| tuning100 | 100 | 历史调优 | 已被反复用于候选选择/门控 |
| validation50 | 50 | 历史验证 | 与 tuning100 不重叠，但 active 臂主要是 frozen replay |

代码默认配置包含历史 development manifest：

- `datasets/eval_splits/evolution_v3_tuning100.json`
- `datasets/eval_splits/evolution_v3_validation50.json`

因此 `build_split()` 发现 final 与历史 development case 重叠时会失败。这是故意的研究安全阀。

## 5. 真实实验前置条件

### 5.1 重新冻结数据边界

必须新建版本化实验 manifest，例如 `real_eval_v2`。新 final holdout 必须：

- 不出现在任何历史 tuning、validation、patrol、proposal provenance、缓存或运行产物中。
- 按 question/task signature 做 group-aware split，而不是只按 case ID 去重。
- 按 source/framework 分层抽样。
- 保存每个 raw case 的 SHA-256。
- 在第一次 provider 调用前冻结 case list、gold、抽样 seed 和标签分布。
- 将 holdout gold 放入受控存储，不能仅依赖仓库内公开 manifest。

由于当前 final300 已有 44 例污染，不能继续把它写成干净的 300 例确认集。只能重新构建 holdout，或明确降级为剩余样本的探索性分析。

### 5.2 固定运行配置

正式实验必须预先固定并记录：

- git commit、Python 版本、依赖锁。
- provider endpoint、实际返回 model、route mapping。
- temperature、max tokens、thinking 设置、timeout、retry 和并发度。
- detector/evolution config、prompt/system prompt hash。
- registry hash、manifest hash、raw input hash。
- cache namespace、失败类型、重试原因、token usage 和 cost。

provider 没有可靠随机 seed 时，至少做一次主 fresh run 和一次独立 fresh stability run，并报告 case-level churn。

### 5.3 固定比较 arms

至少执行：

1. empty registry baseline。
2. 最终 active registry。
3. 每条 intervention 的 leave-one-out。
4. no-recall、no-defense 等机制消融。
5. 简单 deterministic baseline。

baseline 与 active 的 primary efficacy arm 必须都是 fresh end-to-end。frozen-stage replay 只能作为机制归因和噪声控制分析。

### 5.4 固定统计协议

建议预注册 primary estimand：

```text
active_correct - baseline_correct
```

必须同时报告 accuracy、precision、recall、F1、macro-F1、FP、FN、unresolved、provider failure rate、step localization、paired improvement/worsening flips、exact one-sided McNemar p-value、95% confidence interval、按 question group 聚类的 bootstrap CI、source/framework subgroup 结果，以及每 case/arm 的 token、重试和 cost。

多个 candidate、cycle 或 intervention 比较必须预先规定 Holm 校正，或只把最终冻结 registry 的一次比较作为 confirmatory test。

## 6. 推荐运行顺序

新建干净 manifest 和独立 work root 后：

```powershell
python -m evolution_v3 init --config path/to/real_eval_v2.json
python -m evolution_v3 verify-ledger --config path/to/real_eval_v2.json
python -m evolution_v3 preview --cycle 1 --backend detector_v2 --config path/to/real_eval_v2.json
python -m evolution_v3 experiment --cycles 3 --backend detector_v2 --config path/to/real_eval_v2.json
python -m evolution_v3 final --config path/to/real_eval_v2.json
python -m evolution_v3 meta --config path/to/real_eval_v2.json
```

在执行 `final` 前必须确认 active registry 非空且 hash 已冻结、baseline/active 两臂 case 数一致、无 missing/duplicate/invalid result、primary arms 不是 cached/frozen-only replay、final holdout 与所有 development 资产不重叠。

## 7. 结果表述边界

当前历史 100+50 结果可以表述为：

> 在开发相关、provider 混合且 active 臂主要采用 frozen-stage replay 的配对实验中，active interventions 产生了局部正确翻转；该观察未构成独立外部泛化证据。

只有完成新的 clean holdout、fresh baseline/active 双臂、预注册统计和完整产物归档后，才可以表述“确认性提升”或“真实泛化提升”。

## 8. Round-4 模型切换验证序列（Qwen3.5 判决 + GPT-5.6 复核）

2026-08-19 起部署模型切换为 AutoDL 网关：judge/step_tracer/causal_upgrade/evolution_proposer
= Qwen3.5-397B-A17B（thinking disabled），recall_scanner/defense = gpt-5.6-luna（max_tokens
8192）。配置指纹已随 `detector_v2/configs/detector_v2_autodl.json` 变化，历史基线不可比。
切换前已修复：AgentHallu 多轮 question（list 形态）在 `build_packet` 统一拍平为带轮次编号
的字符串（4d3d7d5 引入的字符串门曾使原始数据全部拒载）。

全量实验前必须按下列顺序完成两级小规模验证；任一门失败即停止，不得进入下一级。

### 8.1 阶段 A：检测器验证（pilot20 双跑）

复用 `runs_v2/v2_pilot20_10w` 的 20 例清单（10 阳性/10 阴性），同配置连跑两次
（run-id 仅后缀 `_a1`/`_a2`）：

```bash
python -m detector_v2 batch   --dataset-root datasets/raw/agenthallu/AgentHallu \
  --cases-file <pilot20 清单> --run-id v4_qg_pilot20_a1 --workers 20
python -m detector_v2 evaluate --dataset-root datasets/raw/agenthallu/AgentHallu \
  --cases-file <pilot20 清单> --run-id v4_qg_pilot20_a1
# a2 同理
```

预注册通过门（两级全部满足）：

| 门 | 阈值 | 失败处置 |
| --- | --- | --- |
| unresolved（provider 侧） | ≤ 1/20 | 查超时/重试日志，必要时 judge 升级 qwen3.6-plus 重跑 |
| accuracy（a1） | ≥ 0.60 | 按 FP/FN 构成定位；FP 高查 defense 路，FN 高查 recall 路 |
| a1 vs a2 判决一致率 | ≥ 18/20 | 一致性不足禁止进入进化实验 |
| provider 失败率 | < 10% | 网关不稳则切回 DeepSeek 直连配置 |

本轮修复后的 frozen pilot20 实测结果：`v4_qg_pilot20_fixed20_a1/a2` 均为
20/20 完成、accuracy `0.80`、真正 `state=unresolved` 为 `0/20`，provider
stage failure 为 0；两次终态判决一致率为 `20/20`。A1 有 1 条
`degraded_judge_only`，但它仍有合法的 `verified_error` 终态，不计入
provider-unresolved；A2 为 0 条 degraded。该结果满足 A 阶段四道门。

本轮同时修复了三个回归来源：多票 Judge 的 3/3 高置信阴性不再被单次
Recall Scanner 推翻；Step Tracer 不再误继承 Judge 的 `votes=3`；评估器将
合法 degraded terminal 与真正 unresolved 分开统计。

### 8.2 阶段 B：自进化机制验证（mini，无验证集）

v3_evo50 的 validation_set 为空，本阶段只验证机制闭环，不产生任何效果结论。
本轮使用独立配置 `evolution_v3/configs/framework_stageB_mini50_workers20.json`：

```text
mini pool: 50
sentinel: 20（gold 正负各 10）
gain: 16
patrol: 14
validation/final: 0
backend workers: 20
```

```bash
python -m evolution_v3 preview --cycle 1 --backend detector_v2 --mini \
  --config evolution_v3/configs/framework_stageB_mini50_workers20.json
python -m evolution_v3 experiment --cycles 3 --backend detector_v2 --mini \
  --config evolution_v3/configs/framework_stageB_mini50_workers20.json
python -m evolution_v3 verify-ledger \
  --config evolution_v3/configs/framework_stageB_mini50_workers20.json
```

预注册通过门：

- 每个 cycle 完整结束，ledger hash 链 `verify-ledger` 通过；
- Qwen3.5 提案器在 `llm_calls ≤ 4/cycle` 预算内产出至少一个契约合法提案；
- G1/G2/G3 门对每个提案给出明确 admit/reject，配对重放语义无 crash；
- 巡逻/边际臂 unresolved 率不高于阶段 A 观测值的两倍。

并发安全要求：case 评估通过 `ThreadPoolExecutor(max_workers=20)` 并行，缓存
结果使用短临时文件名和原子替换；cycle、registry、ledger 写入保持主流程串行。
曾发现 Windows 长路径下临时文件前缀重复完整 cache 文件名会造成并发落盘失败，
现已修复并加入专项回归测试。

本轮 B 运行结果：3 个 cycle 均完成，ledger 共 12 条记录且 hash 链有效；14 条
patrol 没有形成至少两个同机制失败案例，因此三轮 proposal 均为空。这证明了
cycle 编排、审计拒绝和 ledger 闭环，但没有触发 proposer 的合法提案和准入路径，
所以不能宣称 B 的提案/干预有效性门已通过。故障注入审计中的 active arm 出现
大量 unresolved 属于被测坏候选的拒绝信号，不计入正常 patrol/gain 健康度；审计
代码和回归测试已修复为 V1/V2/V3/M 任一失败均明确记录
`audit_rejected=true`；该修复从下一次 B 运行开始生效，历史 `v2` 产物不被改写。

### 8.3 阶段 C：全量实验（100+50）

A、B 全部门通过后，按第 6 节顺序对 tuning100 + validation50 跑 3 cycles + final +
meta，统计判据沿用第 5 节预注册要求。预算参考：阶段 A 约 ¥5，阶段 B 约 ¥10-15，
阶段 C 含重放与双臂在 ¥200 内（member 价，以 result.json usage 实算为准）。
