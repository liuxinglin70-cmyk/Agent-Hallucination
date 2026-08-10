# 交接文档：batch01-10 深度审计 → 修复任务（Handoff）

> 目标读者：接手本任务的新对话。先读本文件全部内容，再按 §5 实施计划执行。
> 本文件是唯一权威交接物：审计结论、修复设计、测试计划、验证命令都在这里。

## 0. 任务背景与当前状态

- **已完成（只读）**：对 `experiments/results/batch01`-`batch10` 全部可审计案例做了深度审计，产出 4 个框架逻辑 bug 与 1 个 harness 缺陷，均已定位到源码与证据路径（见 §3）。
- **未完成**：所有修复均未实施，未新增任何测试，`git status` 工作树干净。上一轮尝试写入的 `docs/AUDIT_BATCH01_10.md` 已删除，不保留。
- **本任务**：按 §3 问题清单逐步修复，为每个修复补回归测试，跑全量测试，并用 §6 脚本复核审计问题消失。
- 前提约束：**不要改动 R17/R18 已修复的行为**（见 §4），不要做范围外重构，遵守 `.agents/skills/pristine` 技能（无补丁式 hack、单一事实源、注释只写 why）。

## 1. 仓库与环境事实

| 项 | 值 |
|---|---|
| 仓库 | `C:\Users\liuxinglin\Desktop\agent hallucination` |
| 分支 | `main`，HEAD=`b01d6ff`（R18 文档提交），工作树干净 |
| 系统 | win32，PowerShell 5.1 |
| Python | 3.12.4（`python --version` 验证过） |
| 可用工具 | 无 `jq`、无 `rg`（Grep/Glob 用内置工具，文件枚举用 `git ls-files`） |
| 测试命令 | `python -m pytest tests/ -x -q`（当前 966 全绿） |
| 单文件测试 | `python -m pytest tests/test_<name>.py -x -q` |
| 临时目录 | `C:\Users\LIUXIN~1\AppData\Local\Temp\opencode`（仓外 scratch，勿写进仓库） |
| 技能 | 触发 `pristine`（实施/重构/修 bug 前必读） |

JSON 聚合建议用 here-string 管道给 `python -`，不要写脚本文件到仓库：

```powershell
@'
import json, pathlib, collections
...
'@ | python -
```

## 2. 审计结论（已确认，新对话勿重新发现）

### 2.1 覆盖范围

- 完整可审计结果 = **90 例**：`batch01`-`batch08` 与 `batch10` 各 10 例。
- `batch09` **不是完成的批次**：只有 `batch_manifest.json`（10 case），没有任何 `output/evolution_result.json`，没有 `batch_report.json`。不得当作已完成批次。
- `batch01`-`batch05` **无 `batch_manifest.json`**：有完整输出与 `batch_report.json`，case id 在 `experiments/protocols/used_case_ledger.txt`，但 gold 极性无法从 per-batch manifest 独立复核。
- `batch04_pre_fix` 与 `experiments/results/_invalid` 是被替代/无效结果，排除。

### 2.2 聚合数字（已验证）

| Batch | 完整结果 | Steps | Triggered | Stable | Runtime invalid | L2 accept | L2 reject | L2 skip |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| batch01 | 10 | 12 | 5 | 5 | 0 | 1 | 0 | 0 |
| batch02 | 10 | 12 | 5 | 5 | 0 | 2 | 1 | 1 |
| batch03 | 10 | 11 | 5 | 5 | 0 | 2 | 2 | 1 |
| batch04 | 10 | 10 | 6 | 4 | 0 | 2 | 0 | 0 |
| batch05 | 10 | 13 | 7 | 3 | 0 | 1 | 1 | 0 |
| batch06 | 10 | 11 | 5 | 5 | 0 | 1 | 2 | 0 |
| batch07 | 10 | 12 | 6 | 4 | 0 | 0 | 0 | 1 |
| batch08 | 10 | 13 | 5 | 5 | 0 | 1 | 1 | 0 |
| batch09 | 0 | 0 | - | - | - | - | - | - |
| batch10 | 10 | 11 | 7 | 3 | 0 | 1 | 5 | 0 |
| **合计** | **90** | **105** | **51** | **39** | **0** | **11** | **12** | **3** |

不变量（90/90 满足）：`total_cases = triggered_cases + stable_cases + runtime_invalid_cases`。

L2 audit 归因分布（26 条）：`boundary_terminal_conflict`=20，`consensus_gate_blocked`=3，`hollow_complete_prosecution`=3。

L2 decision：`accept_policy`=11，`reject_policy_fitness`=9，`reject_policy_no_effect`=3，`skip_no_policy`=3。

12 条 L2 reject 真实 reason：`state_change_against_gold_polarity`=5，`proposed_item_did_not_fire`=3，`stable_canary_regression:*`=2，`replay_runtime_invalid:RuntimeError`=2（后两者属历史环境/供应商噪声，R18 已处理，见 §4）。

Provider 账本：全量 `policy_replays`=23 = 11 accept + 12 reject。真实 replay 的 purpose 是 `detector:policy:*`（不是名称含 "replay" 的 key）。两次旧版 batch10 runtime failure 在 dispatch 前已扣减 budget，但异常发生在 provider meta 返回前，因此无 `detector:policy:*` 记录——与 R18 结论一致。

## 3. 待修复问题清单（按严重度排序）

### P1-A：最终 L1 commit 没有更新 GateEvent

- **现象**：6 个 L1 编辑最终 `steps[].gate_decision=accept_batch`，对应 GateEvent 仍是 `provisional`/`conditional`/`pending`。
- **案例**：`batch01/OpenManus/084`、`batch04/OpenManus/067`、`batch06/BFCL/121`、`batch06/SmolAgents/075`、`batch07/Camel/068`、`batch07/OpenDeepSearch/059`。
- **证据**：`experiments/results/batch01/OpenManus__084/output/evolution_checkpoint.json:153`（`accept_batch`）与 `:8617-8619`（`provisional`/`conditional`/`pending`）。
- **根因**：
  - `_record_gate_event()`（`src/sehd_v6/core/bccse/evolution_runner.py:4014-4134`）在 step 初次记录时写事件；`flush_shadow_batch` commit 只改 step（`evolution_runner.py:4939-4946`），不替换/重评级事件。
  - Meta Review 直接消费 ledger（`evolution_runner.py:8900-8903`），把已接受编辑学习成 pending。
  - identity 缺陷：`_record_gate_event` 已提取 `attempt_id`/`episode_id`，但调 `gate_event_id()` 时显式传空（`evolution_runner.py:4113-4117`），事件退化为 observation-slot identity，无法按 proposal identity 对账。
- **已定修复设计**：不要在 commit 处打补丁。改为**单一投影权威**：新增 `_build_gate_event_ledger(steps)`，从最终 `steps`（含 final attempt 的 `compiled/injected/applied/observable_delta/downstream_court_delta` 与 `fitness_check`）确定性重建 ledger；`persist_progress` 与 `_finalize` 的 Meta Review 都从该重建结果读取。identity：有 proposal 时用 `attempt_id`/`episode_id`，否则退回 observation-slot。resume 路径 `evolution_runner.py:3044/3090` 的 `GateEventLedger.from_list`/`drop_event` 行为必须保持幂等。
- **验收**：对 6 个案例的最终 checkpoint，`step.gate_decision == gate_event.legacy_decision`；`accept_batch` 事件的 grade/activation 不再是 `conditional`/`pending`。

### P1-B：GateEvent 的 execution/delta 字段全是假默认值

- **现象**：90 例全部 **105/105** GateEvent 都是 `execution_trace_class=no_proposal`、`observable_delta_class=none`、`downstream_delta_class=none`，包括 11 条 `accept_policy`。
- **证据**：`experiments/results/batch10/Magentic_One__004/output/evolution_checkpoint.json:1052-1059` 同时记录 `accept_policy`/`strong`/`deployable` 与 `no_proposal`/`none`/`none`；相邻 L2 audit 证明候选 item fired 且终态 `unresolved -> verified_clean`。同记录内部矛盾。
- **根因**：`GateEvent` 定义三个字段（`gate_feedback.py:97-99`），Meta Review 持久化 `execution_trace_class`（`meta_review.py:148-178`），但 `_record_gate_event` 构造事件时从不传入（`evolution_runner.py:4111-4131`），全部落到 dataclass 默认值。
- **已定修复设计**：随 P1-A 的重建函数一起做。用现有 closed vocabulary（`gate_feedback.py:61-66` 的 `TRACE_CLASSES`/`DELTA_CLASSES`），从最终 attempt 的 `compiled/injected/applied`、`observable_delta`（state change→terminal、coverage→local）与 L2 `policy_evaluation.fired/skipped` 做确定性投影。
- **验收**：任何 `accept_policy` 事件不得是 `no_proposal`；有 terminal state 变化的事件不得全部 delta=`none`。

### P1-C：L2 reject 的 `policy_genome_hash` 不是实际 replay 的候选 hash

- **现象**：全部 12 个 L2 reject 的 audit hash == working genome hash，0 个 == 实际 replay 的 candidate genome hash（用 `PolicyGenome.from_dict`+`PolicyEdit.from_dict`+`apply_policy_edit` 逐条重建验证：12/12 working、0/12 candidate）。
- **案例**：`batch02/Magentic_One/067`、`batch03/BFCL/047`、`batch03/Camel/002`、`batch05/BFCL/069`、`batch06/BFCL/106`、`batch06/Magentic_One/039`、`batch08/BFCL/134`、`batch10/BFCL/012`、`batch10/BFCL/135`、`batch10/Magentic_One/063`、`batch10/OpenDeepSearch/071`、`batch10/OpenManus/011`。
- **证据**：`experiments/results/batch10/OpenManus__011/output/evolution_checkpoint.json:36`（proposal `policy_e828771f`）、`:111`（hash `8f5d1568...`，empty baseline）、`:121-135`（fired 含 `policy_e828771f`）、`:146`（replay `verified_clean`）。
- **根因**：`finish()` 写 `state.policy_genome.content_hash`（`evolution_runner.py:5391`）；候选在 `:5696` 创建、`:5698-5711` replay；accept 路径 `:5916` 先赋值再 finish，属于偶然正确；reject 路径永不赋值。
- **已定修复设计**：把 `candidate_genome.content_hash` 显式传入 `finish()` 写入 audit；建议同时保留 `working_policy_genome_hash` 与新增 `candidate_policy_genome_hash` 两个字段（向后兼容）。
- **验收**：12 个 reject 案例的 audit `candidate_policy_genome_hash == apply_policy_edit(working, proposal).content_hash`。

### P1-D：`rejection_summary` 丢失全部 L2 reason/stage

- **现象**：12 个 L2 reject 都在 `rejection_summary.by_decision`，但 `by_reason={}`、`by_stage={}`。
- **证据**：`experiments/results/batch10/BFCL__135/output/evolution_result.json:364-421`（`reject_policy_no_effect`/`proposed_item_did_not_fire`）与 `:845-848`（decision=1，reason/stage 空）。
- **根因**：`_build_rejection_summary()`（`evolution_runner.py:765-799`）的 `by_reason`/`by_stage` 只遍历 StructuralGate certificates；`by_decision` 遍历所有 steps。L2 reason 存在 `steps[].fitness_check.l2_policy`/`l2_policy_audit`，从不进入。R18 新增的 `reject_policy_replay_error` 也会同样被吞。
- **已定修复设计**：`_build_rejection_summary` 对每个 reject-class step 同时从 `fitness_check` 聚合 reason（`reject_reasons`/`retry_reason`/`assessment.reasons`/`l2_policy.reason`/`l2_policy.feedback_code`），并沿用现有 stage 分类逻辑。`by_decision` 语义保持不变（兼容现有测试与 batch report）。
- **验收**：12 个 reject 案例的 `by_reason` 覆盖真实 reason（`state_change_against_gold_polarity` 等），`by_stage` 非空。

### P2-E：`batch_report.json` 缺 L2/runtime/budget 汇总

- **现象**：`summarize_case()`（`experiments/run_batch.py:200-210`）只复制 `triggered_cases/edits_accepted/edits_rejected/stable_cases/halted/halt_reason`；遗漏 `total_cases`、`runtime_invalid_cases`、`l2_policy_accepts`、L2 reject 计数、`rejection_summary`、budget。
- **根因**：report builder 只投影少量顶层字段。
- **已定修复设计**：在 `summarize_case()` 增加上述字段；batch 顶层加合计与计数不变量断言（`total = triggered + stable + runtime_invalid`）。
- **验收**：batch10 report 能只读 report 就得到 1 L2 accept / 5 L2 reject 与预算合计。

## 4. 非 bug 与边界（不要改）

- **R17/R18 已修行为**：`replay_runtime_invalid` 可诊断化（`8e2a1f3`）、`reject_policy_replay_error` 独立判决类、确定性后置条件权威、撤销 `quarantine_certificate` 修复面。**不要改动**。
- **`policy_evaluation.terminal_state` 与 `facts.terminal_state` 不一致**（`batch06/BFCL/106`、`batch08/BFCL/134`、`batch10/BFCL/135`）：evaluator 的 `_recompute_terminal_state` 是简化重算（`policy_evaluator.py:71-83`），proof court 以全量规则为准（`proof_contract.py:725-776`），权威 replay state 来自 `proof_adjudication.state`。这是易误导的 telemetry，**不是 fitness bug**。
- **`edits_accepted` 混计 L2 accept**：R16 显式实现（`evolution_runner.py:5441-5449`，`test_evolution_hardening.py` 冻结）。属文档契约口径冲突，**不是本次要改的逻辑**。可选：修订 `docs/SEHD_V6_ACADEMIC_DOCUMENT.md`/`docs/technical/DETECTION_EVOLUTION_FLOW.md` 的字段定义，使其覆盖 L2 强接受；不得改运行行为。
- **batch09 未完成**、**batch01-05 缺 manifest**：harness 追溯缺口，可补 batch 状态/强制 manifest，但不属于框架判定 bug。
- **历史 batch10 `replay_runtime_invalid:RuntimeError`**：供应商传输/限速噪声，R18 已处理，勿重复诊断。

## 5. 实施计划（顺序）

1. **Step 1 — GateEvent 修复**（P1-A + P1-B，同一边界）：
   - 在 `evolution_runner.py` 新增确定性投影函数（attempt 字段 + L2 `policy_evaluation` → `TRACE_CLASSES`/`DELTA_CLASSES`）。
   - 新增 `_build_gate_event_ledger(steps)` 单一投影权威；`persist_progress` 写 checkpoint 前与 `_finalize` Meta Review 前都使用它。
   - identity 用 `attempt_id`/`episode_id`（有 proposal 时），无 proposal 才回退 observation-slot。
   - 保持 resume 幂等：`_restore_run_state` 的 `from_list`/`drop_event`（`evolution_runner.py:3044/3090`）逻辑不变。
2. **Step 2 — L2 candidate hash**（P1-C）：`_run_l2_policy_case_impl` 在 `finish()` 增加 `candidate_genome_hash` 参数；audit 记录 `candidate_policy_genome_hash` 并保留 `policy_genome_hash`。
3. **Step 3 — rejection_summary**（P1-D）：`_build_rejection_summary` 聚合 L2 reason/stage。
4. **Step 4 — batch_report**（P2-E）：`run_batch.py::summarize_case` 增加字段，builder 加合计与不变量。
5. **Step 5 — 回归测试**（每步一个）：
   - GateEvent：构造 accept_batch/provisional 场景，断言最终 ledger 的 decision/grade/trace/delta 与 step 一致（扩展 `tests/test_gate_feedback.py` 或 `tests/test_e2e_optimized_framework.py`）。
   - L2 hash：复用 `tests/test_evolution_hardening.py` 的 L2 policy_evaluation fixtures（~6167-7386），断言 reject audit 的 candidate hash 可重建。
   - rejection_summary：扩展现有 `test_rejection_summary_*`（`test_evolution_hardening.py:1388/3457`），断言 L2 reason 进入 by_reason/by_stage。
   - batch_report：对现有 batch01/10 的 report 做字段存在性断言。
6. **Step 6 — 验证**：`python -m pytest tests/ -x -q` 全绿（966+新增）；用 §6 复核脚本确认 4 个 bug 计数归零。

## 6. 验证方法

```powershell
# 1) 改动前后各跑一次
python -m pytest tests/ -x -q
# 2) 单文件快速迭代
python -m pytest tests/test_gate_feedback.py tests/test_evolution_hardening.py -x -q
```

复核脚本（改完后跑，目标：下述计数全部消失）：

```powershell
@'
import collections, json, pathlib
root = pathlib.Path("experiments/results")
trace_default = 0
provisional_accept = 0
hash_mismatch = 0
by_reason_missing_l2 = 0
for p in sorted(root.glob("batch??/*/output/evolution_result.json")):
    out = p.parent
    result = json.loads(p.read_text(encoding="utf-8"))
    ck = json.loads((out / "evolution_checkpoint.json").read_text(encoding="utf-8"))
    steps = {s.get("case_id") + ":" + str(s.get("case_index")): s for s in result.get("steps", [])}
    for e in ck.get("gate_events", []):
        if (e.get("execution_trace_class"), e.get("observable_delta_class"), e.get("downstream_delta_class")) == ("no_proposal", "none", "none"):
            trace_default += 1
        key = e.get("subject_id") + ":" + str(e.get("case_index")) if "case_index" in e else ""
        if str(e.get("legacy_decision", "")).startswith("accept"):
            if str(e.get("execution_trace_class")) == "no_proposal":
                trace_default += 1
    rs = result.get("evolution_audit", {}).get("rejection_summary", {})
    for a in result.get("evolution_audit", {}).get("l2_policy_audit", []):
        if str(a.get("decision", "")).startswith("reject"):
            if not rs.get("by_reason"):
                by_reason_missing_l2 += 1
            # candidate hash present?
            if "candidate_policy_genome_hash" not in a:
                hash_mismatch += 1
print("trace_default", trace_default, "provisional_accept", provisional_accept,
      "l2_hash_missing", hash_mismatch, "by_reason_missing_l2", by_reason_missing_l2)
'@ | python -
```

期望：`trace_default=0`、`l2_hash_missing=0`、`by_reason_missing_l2=0`。P1-A 用 §3 的 6 案例分别断言 `step.gate_decision == gate_event.legacy_decision`。

## 7. 复制粘贴的启动 prompt（新对话）

````markdown
# 任务：修复 SEHD v6 batch01-10 审计发现的框架 bug（延续上一个对话）

仓库：`C:\Users\liuxinglin\Desktop\agent hallucination`（win32 / PowerShell 5.1 / Python 3.12.4）。
分支 `main`，HEAD=`b01d6ff`，工作树干净。测试基线：`python -m pytest tests/ -x -q` 966 全绿。

## 第一步（必做）
读取 `docs/HANDOFF_BATCH01_10_FIX.md` 全文，并触发 `.agents/skills/pristine` 技能。
该文件是唯一权威交接物，包含：审计结论与聚合数字（§2）、4 个高严重度 bug + 1 个中严重度 harness 缺陷（§3，含证据路径、源码定位、已定修复设计）、非 bug 边界（§4，R17/R18 已修行为不得改动）、实施计划（§5）、验证命令（§6）。

## 任务范围
按 §5 顺序逐步实施，每步一个回归测试：
1. GateEvent 最终决策回写 + execution/delta 投影（单一投影权威，勿打补丁）。
2. L2 reject 的 candidate policy genome hash 写入 audit（向后兼容）。
3. `rejection_summary` 纳入 L2 reason/stage。
4. `batch_report.json` 增加 L2/runtime/budget 合计与计数不变量。
5. 补回归测试后跑 `python -m pytest tests/ -x -q` 全量（966+新增全绿）。
6. 用 §6 复核脚本确认：`trace_default=0`、`l2_hash_missing=0`、`by_reason_missing_l2=0`，且 6 个 accept_batch 案例的 GateEvent 与 step 一致。

## 硬约束
- 只改 §3 列出的问题。不得改动 R17/R18 已修行为（replay_runtime_invalid 诊断化、reject_policy_replay_error、确定性后置条件、quarantine 修复面撤销）。
- `edits_accepted` 混计 L2 属于 R16 显式行为，是文档契约问题，不是本次逻辑改动；最多修订两个 docs 字段定义，不改运行行为。
- 遵守 pristine：修复根因而非打补丁；注释只写 why；不留死代码/备份；唯一事实源。
- 无 jq/rg：文件枚举用 `git ls-files`，JSON 聚合用 `@'...'@ | python -`。
- 不要提交、不推送、不跑实验；除非用户明确要求。
## 交付
1. 每个问题的改动文件与关键行号。
2. 新增测试的清单与断言语义。
3. 全量测试结果输出。
4. 复核脚本输出（bug 计数归零）。
5. 收尾时输出一份 `docs/FIX_BATCH01_10_RESULT.md` 记录改动与验证结果。
````

## 8. 相关文件索引

**源码（修复目标）**
- `src/sehd_v6/core/bccse/evolution_runner.py`：`_record_gate_event` 4014-4134；`_l2_promotion_evidence` ~3955-4011；`_build_rejection_summary` 765-799；`_build_evolution_result` 3453-3545；`_persist_progress` 3788+（gate_events 3888）；`record_step` 6420-6425；flush commit 4939-4946；`_run_l2_policy_case_impl` 5221-5919（finish 5382-5410，candidate 5696，replay 5698-5711，assign 5916）；resume 3044/3090；`_finalize` Meta Review 8900-8903。
- `src/sehd_v6/core/bccse/gate_feedback.py`：GateEvent 73-181（默认值 97-99，TRACE/DELTA 词汇 61-66）；ledger record 290-300。
- `src/sehd_v6/core/bccse/meta_review.py`：GateExperience 86-145；aggregate_gate_experience 148-179。
- `src/sehd_v6/core/bccse/policy_genome.py`：`content_hash` 225-245；`apply_policy_edit` 502+。
- `src/sehd_v6/core/bccse/promotion_contract.py`：`promote` 367-437；`classify_reason_codes` 216-238。
- `experiments/run_batch.py`：`summarize_case` 200-279。

**测试**
- `tests/test_gate_feedback.py`（GateEvent schema/hard codes/meta）。
- `tests/test_evolution_hardening.py`（L2 policy_evaluation fixtures ~6167-7386；rejection_summary 1388/3457-3472；L2 accept/reject 计数）。
- `tests/test_e2e_optimized_framework.py`（gate event 存在性 69-75）。

**证据路径（§3 引用）**
- `experiments/results/batch01/OpenManus__084/output/evolution_checkpoint.json:153,8617-8619`
- `experiments/results/batch10/Magentic_One__004/output/evolution_checkpoint.json:1052-1059`
- `experiments/results/batch10/OpenManus__011/output/evolution_checkpoint.json:36,111,121-135,146`
- `experiments/results/batch10/BFCL__135/output/evolution_result.json:364-421,845-848`

**协议/台账**
- `experiments/protocols/used_case_ledger.txt`；`experiments/results/batch{N}/batch_manifest.json`（batch01-05 缺失）；`batch_report.json`。
