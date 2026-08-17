# evolution_v3：detector_v2 的干预式自进化框架

按仓库根目录文档 **`SELF_EVOLUTION_V3_DESIGN_FOR_DETECTOR_V2.md`（v3.1）** 实现的
SEHD-RISE3 自进化框架。本目录与 `detector_v2/` 平级，独立运行；运行时会通过
`detector_v2` 公开 API 加载和执行干预 registry。

核心定义：**自进化 = 受控干预的准入问题**。检测器有有限个 LLM 决策点；进化
只向这些决策点提交三类闭词表干预，每个干预在写 registry 前必须通过
V1→V2→V3→M 四道 gold 评审。LLM 永远不做晋升评分。

---

## 1. 已实现内容与文档里程碑对应

| 文档要求 | 实现位置 |
|---|---|
| 9 个 hooks 注册 + 干预加载（fail-closed） | `detector_v2/interventions.py` |
| `proof.deployment` 落盘 | `detector_v2/api.py`（每次检测都写 `proof.deployment`） |
| frozen-judge 配对重放（降低 V2/M 的 LLM 噪声） | `detector_v2/api.py`、`evolution_v3/backends.py` |
| 升级器逐候选覆盖校验：缺行补问 → 仍缺显式 degraded | `detector_v2/recaller.py`、`detector_v2/semantics.py` |
| 信息不对称修复：upgrade 正例的 defense/tracer 不接收 Judge 的 `correct_result/is_hallucination` | `detector_v2/api.py`、`detector_v2/adjudication.py` |
| 首批 5 个 deterministic predicates | `detector_v2/predicates.py` |
| V1 结构批评（deterministic 候选按 executable key 判冗余） | `evolution_v3/gates.py::v1_structural` |
| V2 sentinel 无害性 | `evolution_v3/gates.py::v2_harmlessness` |
| V3 claim vs 行为差分 | `evolution_v3/gates.py::v3_semantics` |
| M 边际增益 + greedy subset selection | `evolution_v3/gates.py::marginal_selection` |
| Append-only hash-chained ledger | `evolution_v3/ledger.py`（`datasets/evolution_v2/ledger.jsonl`） |
| 库上限 width=5、同 hook 文本边界 ≤1、guard ≤3（cycle 2 联合准入后调整） | `evolution_v3/registry.py::check_limits` |
| outcome-driven retirement / 机械回滚 | `evolution_v3/engine.py` |
| Defect-injection audit（每 cycle 2 个固定缺陷） | `evolution_v3/gates.py::audit_defects` |
| L1/L2 写权隔离、事实词表、冲突即基线 | `detector_v2/interventions.py`、`detector_v2/adjudication.py` |
| M3 L1+L2 联合候选（各自过 V1-V3，只按联合配置过 M） | `evolution_v3/proposer.py`、`evolution_v3/gates.py::marginal_selection` |
| Meta-Evaluation | `evolution_v3/meta_eval.py` |
| 393/300 数据布局与 read-once 终审 | `evolution_v3/splits.py`、`evolution_v3/engine.py::run_final` |

## 2. 目录结构

```text
evolution_v3/
  configs/framework.json      # 预算、门阈值、库上限、协议配置
  contracts.py                # 干预合同（复用 detector_v2 的校验/哈希）
  splits.py                   # S / G / P / 终审300 read-once；mini tuning100 切分
  backends.py                 # detector_v2 真实后端（frozen-judge + frozen_stages 重放）+ cached run 后端
  calibration.py              # 空库双跑噪声地板预注册
  worksheet.py                # 机械失败族归因 + predicate 诊断（case id 不进候选文本）
  proposer.py                 # 多模板起草器（机械诊断接地）+ LLM 起草器（≤3 候选）
  gates.py                    # V1/V2/V3/M + 缺陷注入审计
  registry.py                 # pre-commit active registry、快照、回滚
  ledger.py                   # append-only hash 链
  engine.py                   # cycle 编排、退休/回滚、终审
  meta_eval.py                # 进化机制自身指标
  cli.py / __main__.py
  tests/                      # 离线单元与端到端 gate 测试
  experiments/                # 实验协议说明
```

与检测器的交互点（必需内容）：

```text
evolution_v3.contracts  ──验证──>  detector_v2.interventions
evolution_v3.backends   ──调用──>  detector_v2.api.Detector.detect(..., interventions=registry)
evolution_v3.engine     ──读写──>  detector_v2.packet / dataset / evaluation
detector_v2 运行结果    ──回传──>  proof.deployment / proof.evidence / causal_upgrade_coverage
```

## 3. 快速验证（不消耗 LLM 预算）

```bash
# 单元/集成/回归测试全绿（当前 156 个）
python -m pytest detector_v2/tests evolution_v3/tests tests -q

# 离线完整 cycle：玩具检测器注入 2 个稳定 FN 失败族，
# 候选 IV-1-001 过 V1→V2→V3→M 并被 pre-commit 写入，
# 2 个缺陷候选（case-id 泄漏 / 零边际噪声）必须被 gate 拒绝。
python -m evolution_v3 smoke
```

`smoke` 使用隔离工作区 `evolution_v3/work/smoke/`，不会污染正式
`evolution_v3/registry/active.json` 与 `datasets/evolution_v2/ledger.jsonl`。

### 3.1 防止“门槛太严导致空结果”的源头措施

门槛本身不放松（V2 仍零新增错误、M 仍要求净增益且不新增错误），但产出质量
从源头提高：

1. **巡逻补采（bootstrap）**：初始 120 例巡逻若没有 ≥2 同机制失败，自动在
   `stable_failure_rerun_cases` 预算内补采，避免随机巡逻恰好漏掉失败族。
2. **机械谓词接地**：`worksheet.diagnose_failure_families` 对失败族跑 5 个
   deterministic predicates，把每个族的 `violations/applicable` 信号喂给
   proposer，模板优先选择真正命中的机制。
3. **一源多模板 + 跨族多样性**：同一失败族最多生成 3 个不同干预
   （required_action / verbatim / observation fidelity / guard / evidence），
   并且先保证每个失败族至少一个候选，避免大族饿死其他族。
4. **优先 non-judge hooks**：默认模板不生成 `judge.system`，因此候选可以走
   frozen-judge 配对重放；这是当前最强的配对噪声控制。
5. **空结果显式化**：cycle 报告含 `proposal_summary.empty_proposal_reason`，
   meta 报告含 `bootstrap_used_per_cycle` 与 `empty_proposal_reasons`。没有
   候选收入是有效结果，而不是静默失败。
6. **预算前预演**：`python -m evolution_v3 preview --cycle 1 ...` 只跑巡逻与
   提案，不花 V2/M 检测预算，先看本 cycle 会不会空手。
7. **结构化冗余判定**：`evidence_check` / `decision_guard` 按
   `(hook, predicate/action, facts)` 判重，不同确定性执行器不会被相似 claim
   文案误杀。
8. **rare-family 提案注入**：`EvolutionEngine.run_cycle(..., proposal_batch=...)`
   允许把 100 例中只出现一次的清晰机制族作为 pre-commit 联合单元提交，
   避免巡逻切分恰好吃掉第二例导致空进化。

### 3.2 已执行的 100+50 mini 实验

当前阶段实验用冻结的 `runs_v2/v2_100_20w`（100 例调优）与
`evolution_v3/work/valruns/evo3_val_base`（50 例验证基线），不新增数据集：

```bash
python evolution_v3/experiments/run_tuning100.py cycle \
  --cycle 1 --max-proposals 2 --m-min-marginal 1 --no-audit
python evolution_v3/experiments/run_tuning100.py cycle \
  --cycle 2 --max-proposals 3 --m-min-marginal 2 --no-audit
python evolution_v3/experiments/run_tuning100.py validate --no-noise
```

结果与完整证据索引：
**`experiments/tuning100_validation50_experiment_report.md`**。

摘要：5 条干预通过 V1/V2/V3/M 并写入
`evolution_v3/registry/active.json`；100 例 G 集 22→25 correct；50 例验证集
78%→84%（+6pp），FP 4→3，`new_fn=0/new_fp=0`。

## 4. 正式实验协议（393/300，尚未执行）

```bash
# 0. 检查数据与门状态（终审 300 仍为 read-once）
python -m evolution_v3 init
# 0.5 预算前预演：只巡逻 + 提案，看候选数量与失败族诊断
python -m evolution_v3 preview --cycle 1 --backend detector_v2
# 0.6 预注册同案例空库双跑的噪声地板（M4 前校准，不做默认容忍度假设）
python -m evolution_v3 calibrate-noise --backend detector_v2

# 1. M2：跑一个完整 cycle（无候选收入也是有效结果）
python -m evolution_v3 cycle --cycle 1 --backend detector_v2

# 2. M4：3 个 cycle 后，恰读一次终审 300（再次读取会报 read-once violation）
python -m evolution_v3 experiment --cycles 3 --backend detector_v2
python -m evolution_v3 final --noise-reruns 2

# 3. 进化机制自身评估（只读）
python -m evolution_v3 meta
python -m evolution_v3 verify-ledger
```

预算与阈值见 `configs/framework.json`，终审判据见
`evolution_v3/work/final/final_report.json`：

- 主判据：active vs 空库在 300 例上的配对净翻转 ≥ 同案例噪声重跑正净翻转；
- 次判据：单侧 McNemar p<0.05、step localization 不下降、unresolved 不增加；
- 不通过即框架判负，不调参续命。

## 5. 三类干预的闭词表

```json
{
  "type": "evidence_check | text_boundary | decision_guard",
  "hook": "judge.system|judge.post|scanner.system|upgrade.pre|upgrade.system|tracer.system|defense.pre|defense.system|adjudicate.guard"
}
```

- `evidence_check` 只允许已注册的 deterministic predicate：
  `required_action_present` / `verbatim_fidelity` / `evidence_refs_resolve` /
  `contract_coverage` / `delivered_anchor_consistency`；runner 必须为
  `deterministic`，没有 executor 的 runner 在加载时 fail-closed。
- `decision_guard` 动作闭词表：
  `withhold_conviction` / `restore_judge_conviction` /
  `require_full_view_defense`；多 guard 动作冲突时全部失效并回退 baseline，
  `proof.deployment.policy_conflict=true`。
- `text_boundary` 必须同时有 `directive` 和 `not_when` 双边界。

## 6. 数据协议

- 进化池：`datasets/eval_splits/phase2_v1/manifest.json` 的 393 例。
- 当前已跑的 mini 实验：`datasets/eval_splits/evolution_v3_tuning100.json`
  （S=20 / P=40 / G=40，基线 `runs_v2/v2_100_20w`）与
  `datasets/eval_splits/evolution_v3_validation50.json`
  （pool 去重后 50 例，基线 `evolution_v3/work/valruns/evo3_val_base`）。
- 正式协议：SENTINEL 20 例（gold 正负各 10，source 分层）→ V2；
  G 80 例 → M 边际增益；P 其余 293 例中每 cycle 随机抽 120 例巡逻。
- 终审：300 例，`FinalHoldout` 强制 read-once；M4 前零读取（当前仍未读取）。
- 已知污染：100 例 tuning 集与 `final_verification` 有 44 例重叠；正式终审
  必须改用剩余 256 例或重建全新冻结终审集。
- Provider：默认 AutoDL（`detector_v2/configs/detector_v2_autodl.json`）。

## 7. 已知边界与明确不做

- mock 后端是 oracle 玩具，只用于离线验证 gate 机械和实验协议；报告中
  `backend=mock`，**不是检测器能力读数**。
- 不训练权重、不引入多代理框架、不让 LLM 评分。
- 已部署干预的输出永不进入后续候选的 reference。
