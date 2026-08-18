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

当前结果：`191 passed`。

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
