# detector_v2

独立运行的结果导向幻觉检测器。不 import、不依赖旧 `sehd_rise2` 任何代码。

自进化框架设计见仓库根目录
**[SELF_EVOLUTION_V3_DESIGN_FOR_DETECTOR_V2.md](../SELF_EVOLUTION_V3_DESIGN_FOR_DETECTOR_V2.md)**。

## 当前架构（已实现）

```text
{question, history}
      │ strict firewall
      ▼
packet（事件 + 工具交互 + 交付结果锚点）
      │
      ▼
[1] Outcome Judge        全轨迹端到端判断（3 票多数决）
      │ true ────────────────────────────────────────┐
      │ uncertain false（分歧/弃权/低置信）              │
      ▼                                               │
[2] Recall Scanner       只找候选偏差，永远不定罪        │
      │                                               │
      ▼                                               │
[3] Causal Upgrade       候选必须 content_error ∧ causal
      │ （逐候选 3 票多数决；全部正候选按最早步排序）      │
      ▼                                               ▼
[4] 听证队列（hearings）  Judge 阳性先听证；被 Defense 驳倒
      │                  或 Judge 阴性时，每个正候选依次听证
      ▼
[5] Step Tracer          最早因果步（每场听证各跑一次）
      │
      ▼
[6] Defense Verifier     只审查定罪，只允许“有证据的翻案”（3 票多数决）
      │ substantiated ────────► verified_error
      │ substantiated_upgraded（平票）─► verified_error（degraded）
      │ 全部听证 refuted ────► verified_clean
      │ 未决/故障 ──────────► verified_error（degraded_judge_only）
      ▼
deterministic adjudication（封闭词表，无阈值）
```

关键语义：

- 幻觉 = 轨迹交付的结果错误，且最早某一步的修正是因果充分的；
- **MATERIALITY 法则**（`semantics.py`）是唯一的语义权威，Judge / Causal
  Upgrade / Defense 三段共享同一文本：判定单位 = 任务要求的全部交付物；
  物质性 = 结果错误 ∨ 交付物内容违约 ∨ 虚假完成；工具调用即动作、任务固定
  常量自接地、人类消息是断言非环境事实、已纠正的中间错误不算错；
- 工具观察是环境事实；公开事实错误可用世界知识定罪；
- 格式差异、已纠正的中间错误、纯过程/风格偏差、无虚假完成声明的未完成，
  不是幻觉；
- Judge 阳性被 Defense 驳倒后转召回网再审，任何一个正候选听证
  `substantiated` 即定罪；
- 多票 Judge 的一致高置信阴性直接结束；Recall Scanner 只处理阴性票分歧、
  弃权或低置信度，避免单次高召回扫描推翻 3/3 clean 共识；
- Defense 的 `inconclusive`、平票（`substantiated_upgraded`）和基础设施故障
  不翻案，只降 authority；
- 只有 Judge 失败才 `unresolved`。

### 并发与评估口径

- `detector_v2 batch --workers N` 使用线程池并行 case；Stage A 验证使用
  `--workers 20`。
- `verified_error` / `verified_clean` 即使 authority 为
  `degraded_judge_only` 仍是合法 terminal outcome；评估会把它们作为可预测
  终态统计，同时单独报告 `degraded` 和 `provider_unresolved`。
- evolution gate 仍只接受 authoritative prediction；Stage A 的 provider
  unresolved 只统计真正 `state=unresolved` 或缺失结果。

### 方差层（多数决投票）

Judge、Causal Upgrade、Defense 三个决定位支持 `votes: N` 路由配置（当前
部署 3）。每次投票是独立顺序采样；契约违反或传输失败的一票弃权，其余票
继续裁决。平票处置写成法则而非调参：Judge 平票保持阴性（定罪是非常规
主张）；Upgrade 逐候选平票保持不升级；Defense 平票将 `substantiated`
升级为 `substantiated_upgraded`（维持定罪、降 authority，绝不翻案）。
Scanner 和 Tracer 不投票。投票详情记录在各阶段 `provider.vote`。

## 运行

```bash
# 单条
python -m detector_v2 detect \
  --record datasets/raw/agenthallu/AgentHallu/BFCL/001.json \
  --case-id BFCL/001.json --output-dir out_v2

# 批量（cases-file 是 JSON 数组）
python -m detector_v2 batch \
  --dataset-root datasets/raw/agenthallu/AgentHallu \
  --cases-file datasets/eval_splits/v3_evo50/evolution_cases.json \
  --run-id my_v2_run --workers 8

# 评估（gold 只出现在这一步）
python -m detector_v2 evaluate \
  --dataset-root datasets/raw/agenthallu/AgentHallu \
  --cases-file datasets/eval_splits/v3_evo50/evolution_cases.json \
  --run-id my_v2_run
```

## 配置

默认 `configs/detector_v2_autodl.json`：`https://www.autodl.art/api/v1`，
模型 `deepseek-v4-pro` / `deepseek-v4-flash`（小写），凭据 `AUTODL_API_KEY`
（由仓库 `.env` 或进程环境注入）。

`detector_v2.config.default_config_path()` 已指向 AutoDL 配置；不带
`--config` 的 CLI、`Detector`、`evolution_v3` 后端都会默认使用 AutoDL。
官方 DeepSeek 配置保留在 `configs/detector_v2.json`，仅作显式回滚路径：

```bash
python -m detector_v2 detect \
  --record FILE --config detector_v2/configs/detector_v2.json ...
```

环境变量覆盖（进程环境优先，其次仓库 `.env`）：

```text
DETECTOR_V2_ENDPOINT
DETECTOR_V2_API_KEY_ENV
DETECTOR_V2_JUDGE_MODEL
DETECTOR_V2_DEFENSE_MODEL
DETECTOR_V2_MODEL            # 两个阶段的兜底模型
DETECTOR_V2_TIMEOUT_SECONDS
DETECTOR_V2_TRANSPORT_RETRIES
```

### 上下文缓存

两份默认配置都开启 `context_cache`（`enabled: true`、`mode: "automatic"`）：

- `automatic`：DeepSeek 兼容类供应商自动缓存重复 prompt 前缀，请求体不额外
  带字段，避免被严格网关拒绝；
- `prompt_cache_key`：对需要显式 cache key 的供应商，发送
  `prompt_cache_key` 与 `prompt_cache_retention`：

```json
"context_cache": {
  "enabled": true,
  "mode": "prompt_cache_key",
  "key_prefix": "detector-v2",
  "retention": "24h"
}
```

每次调用的 usage 会统一记录 `prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens` / `prompt_cache_read_tokens` /
`prompt_cache_write_tokens`，供成本统计使用。

### 思考模式

两份默认配置的 `provider.extra_body` 已关闭 DeepSeek 思考模式
（`thinking.type: disabled`），避免检测阶段产生过长的推理 token。需要
推理链时可移除该字段。

### 可选模型分层

`configs/model_profiles.json` 记录各可选模型的分层路由建议与价格
（￥/M token），不改动默认 endpoint。当前推荐：

| 角色 | 模型 | 输入 | 输出 |
|---|---|---:|---:|
| Judge / Defense / Causal Upgrade | `Qwen3.5-397B-A17B` | 0.72 | 4.32 |
| Recall / Step Tracer | `hy3` | 1.0 | 4.0 |
| 主流程备选 | `gpt-5.6-luna` | 0.812 | 4.872 |
| Benchmark candidate | `MiniMax-H3` | 待定 | 待定 |

接入另一供应商只需替换路由的 `endpoint` / `api_key_env` / `model`，无需
改动检测器业务代码。

## 输入契约

检测器只接受：

```json
{"question": ..., "history": [...]}
```

顶层出现任何其他字段（包括 `true_answer`、`explanation`、元数据）都会被
拒绝。`dataset.project_record` 负责从原始 benchmark 记录投影出该形状。

## 自进化接口

- `interventions.py`：唯一部署通道。已验证的
  `detector_v2.intervention_registry.v1` registry，三类干预
  （evidence_check / text_boundary / decision_guard）只通过注册 hook 执行，
  运行时按机械 trigger 接合并落盘 `proof.deployment`。
- 结果中的结构化字段：
  `proof.judge.delivered_result / correct_result / basis / confidence /
  evidence_refs / root_cause / contracts`
  `proof.recall_candidates / causal_upgrade_rows / causal_upgrades /
  step_tracer / hearings`
  `proof.defense.verdict / confirmed_step / evidence_refs`
  `proof.evidence / proof.deployment`
  `decision_authority`（三态）——供进化层读取。
- `api.detect` 支持：
  - `frozen_judge={"judge_result": ..., "judge_status": ...}`：冻结 Judge，
    供不触碰 `judge.system` 的候选做配对重放；
  - `frozen_stages`：进一步冻结 recall / upgrade / tracer / defense 的基线
    阶段输出。已结算（`ok` / provider_error / contract_error）阶段一律可
    冻结；tracer/defense 按听证持久化在 `proof.hearings` 每行，多听证案例
    按 `(source, candidate_id)` 匹配重放，冻结流中缺少某个听证的冻结条目
    即 fail-closed。只有被干预 hook 覆盖的阶段重新执行，公共 API 对任何
    被干预 hook 覆盖的阶段一律拒绝冻结（遮蔽即 fail-closed）。
- 干预执行器、事实词表、guard 动作与落盘格式见
  `detector_v2/INTERVENTIONS.md`。

## 实验记录

- 20 例 pilot：`experiments/v2_pilot20_report.md`（V2.0）与
  `experiments/v2_pilot20_v21_report.md`（V2.1）。
- 100 例冻结基线：`runs_v2/v2_100_20w`，报告
  `experiments/v2_100_report.md`。
- 自进化 100+50 完整实验记录（cycle 1/2、五条准入干预、50 例验证结果）：
  `../evolution_v3/experiments/tuning100_validation50_experiment_report.md`。
- 20 例 / 100 例是开发读数；原计划冻结 300 例 `final_verification` 虽未读取，
  但与历史 tuning100 重叠 44 例，不能再作为干净确认集。正式实验必须重建
  holdout，详见 `../SELF_EVOLUTION_V3_AUDIT_AND_EXPERIMENT_PROTOCOL.md`。

本轮 Round-4 Stage A：`v4_qg_pilot20_fixed20_a1/a2`，20 路并发，两个 run
均 accuracy `0.80`、0 unresolved，终态一致 `20/20`。B 阶段使用
`evolution_v3/configs/framework_stageB_mini50_workers20.json` 的 mini50
划分（S20/G16/P14）和 `workers=20`；B 结果及边界以根目录实验协议为准。

## 测试

```bash
python -m pytest detector_v2/tests -q
```
