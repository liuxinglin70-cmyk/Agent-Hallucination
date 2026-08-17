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
[1] Outcome Judge        全轨迹端到端判断（主召回来源）
      │ true ────────────────────────────────────────┐
      │ false（一律运行召回安全网）                    │
      ▼                                               │
[2] Recall Scanner       只找候选偏差，永远不定罪       │
      │                                               │
      ▼                                               │
[3] Causal Upgrade       候选必须 content_error ∧ causal
      │ 升级成功 ─────────────────────────────────────┤
      ▼                                               ▼
[4] Step Tracer          最早因果步（只在阳性后运行）
      │
      ▼
[5] Defense Verifier     只审查定罪，只允许“有证据的翻案”
      │ substantiated ────────► verified_error
      │ refuted ──────────────► verified_clean
      │ inconclusive/故障 ────► verified_error（degraded_judge_only）
      ▼
deterministic adjudication（封闭词表，无阈值）
```

关键语义：

- 幻觉 = 轨迹交付的结果错误，且最早某一步的修正是因果充分的；
- 工具观察是环境事实；公开事实错误可用世界知识定罪；
- 格式差异、已纠正的中间错误、纯过程/风格偏差、无虚假完成声明的未完成，不是幻觉；
- Defense 的 `inconclusive` 和基础设施故障不翻案，只降 authority；
- 只有 Judge 失败才 `unresolved`。

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

## 输入契约

检测器只接受：

```json
{"question": ..., "history": [...]}
```

顶层出现任何其他字段（包括 `true_answer`、`explanation`、元数据）都会被
拒绝。`dataset.project_record` 负责从原始 benchmark 记录投影出该形状。

## 自进化接口

- `artifacts.py`：已验证的 `prompt_directive` registry，运行时按机械特征
  路由注入阶段 prompt。
- 结果中的结构化字段：
  `proof.judge.delivered_result / correct_result / basis / confidence /
  evidence_refs / root_cause / contracts`
  `proof.recall_candidates / causal_upgrade_rows / step_tracer`
  `proof.defense.verdict / confirmed_step / evidence_refs`
  `proof.evidence / proof.deployment`
  `decision_authority`（三态）——供进化层读取。
- `api.detect` 支持：
  - `frozen_judge={"judge_result": ..., "judge_status": ...}`：冻结 Judge，
    供不触碰 `judge.system` 的候选做配对重放；
  - `frozen_stages`：进一步冻结 recall / upgrade / tracer / defense 的基线
    阶段输出，只有被干预 hook 覆盖的阶段重新执行。registry 含
    `judge.system` 时拒绝冻结 Judge（fail-closed）。
- 干预执行器、事实词表、guard 动作与落盘格式见
  `detector_v2/INTERVENTIONS.md`。

## 实验记录

- 20 例 pilot：`experiments/v2_pilot20_report.md`（V2.0）与
  `experiments/v2_pilot20_v21_report.md`（V2.1）。
- 100 例冻结基线：`runs_v2/v2_100_20w`，报告
  `experiments/v2_100_report.md`。
- 自进化 100+50 完整实验记录（cycle 1/2、五条准入干预、50 例验证结果）：
  `../evolution_v3/experiments/tuning100_validation50_experiment_report.md`。
- 20 例 / 100 例是开发读数；冻结 300 例 `final_verification` 仍未读取，
  正式终审保持 read-once。

## 测试

```bash
python -m pytest detector_v2/tests -q
```
