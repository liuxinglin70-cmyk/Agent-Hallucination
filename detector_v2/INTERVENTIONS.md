# detector_v2 自进化干预接口（V3 M0-M3 检测器侧改动）

本文件是 `SELF_EVOLUTION_V3_DESIGN_FOR_DETECTOR_V2.md` 在 detector_v2 侧的实现
说明。所有干预执行器都在 `detector_v2/interventions.py` 与
`detector_v2/predicates.py` 中显式注册；没有 executor 的 hook/type/runner 会在
加载 registry 时抛错（fail-closed）。

## Hooks

| hook | 层 | 写权 |
|---|---|---|
| `judge.system` | L1 | 追加 system 文本边界 |
| `judge.post` | L1 | 仅追加 `proof.evidence` |
| `scanner.system` | L1 | 追加 system 文本边界 |
| `upgrade.pre` | L1 | 仅追加 `proof.evidence`（事实注入升级 prompt） |
| `upgrade.system` | L1 | 追加 system 文本边界 |
| `tracer.system` | L1 | 追加 system 文本边界 |
| `defense.pre` | L2 | 仅追加 `proof.evidence` |
| `defense.system` | L2 | 追加 system 文本边界 |
| `adjudicate.guard` | L2 | 闭词表动作 |

## Registry

```json
{
  "schema_version": "detector_v2.intervention_registry.v1",
  "interventions": [ { "schema_version": "detector_v2.intervention.v1", "...": "..." } ],
  "content_hash": "<sha256>"
}
```

调用方式：

```bash
python -m detector_v2 batch \
  --dataset-root datasets/raw/agenthallu/AgentHallu \
  --cases-file datasets/eval_splits/v3_evo50/evolution_cases.json \
  --interventions evolution_v3/registry/active.json
```

Python API：`Detector.detect(record, case_id=..., interventions=registry)`。

配对重放：

- `Detector.detect(..., frozen_judge={"judge_result": ..., "judge_status": ...})`
  会跳过 Judge 的 LLM 调用，只重放基线 Judge 输出；当 registry 中存在
  `judge.system` 干预时 fail-closed 拒绝重放。
- `Detector.detect(..., frozen_stages={"judge_result": ..., "recall_result": ...,
  "upgrade_result": ..., "tracer_result": ..., "defense_result": ...})`
  进一步冻结未被干预 hook 覆盖的 recall / upgrade / tracer / defense 基线
  阶段。`judge.system` 命中时同样拒绝冻结 Judge。
- 进化框架用这两层 replay 消除 V2/M 配对比较中的 LLM 噪声；`result.provenance`
  会记录 `frozen_judge_replay` 与 `frozen_stages` 列表，报告可审计。

每次检测结果都携带：

```json
"proof": {
  "evidence": [...],
  "deployment": {
    "intervention_registry_hash": "...",
    "active_interventions": [...],
    "fired": [...],
    "evidence": [...],
    "guard_actions": [...],
    "policy_conflict": false
  }
}
```

当前 pre-commit 部署 registry 为 `evolution_v3/registry/active.json`，共 5 条
（2026-08-17）：

```text
IV-1-001  evidence_check @ judge.post        required_action_present
IV-1-002  decision_guard @ adjudicate.guard  restore recall-upgrade conviction
IV-2-001  decision_guard @ adjudicate.guard  restore high-confidence judge conviction
IV-2-002  evidence_check @ judge.post        delivered_anchor_consistency
IV-2-003  decision_guard @ adjudicate.guard  withhold consistent-anchor recall-upgrade
```

100+50 实验结论见
`evolution_v3/experiments/tuning100_validation50_experiment_report.md`。

## Deterministic predicates

`required_action_present`、`verbatim_fidelity`、`evidence_refs_resolve`、
`contract_coverage`、`delivered_anchor_consistency`。每个谓词都有封闭 verdict
词表，输出只写 `proof.evidence`。

## M0 行为修复

1. **Upgrade 输出契约**：逐候选覆盖校验。初次响应缺行时补问一次；补问后仍缺，
   `causal_upgrade` 状态为 `degraded_missing_rows`，终态 clean 但 authority 降为
   `degraded_judge_only`。缺行绝不静默解释成 `content_error=false`。
2. **信息不对称**：recall-upgrade 正例进入 defense/tracer 时不再携带 Judge 的
   `correct_result / is_hallucination`。
3. **Guard 冲突即基线**：多个 `decision_guard` 动作冲突时全部失效，回退 baseline，
   并落盘 `proof.deployment.policy_conflict=true`。
