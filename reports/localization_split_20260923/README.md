# 定位口径两分与责任步冲突降级（2026-09-23）

## 这一轮做了什么

修 §32.7 记录的两条检测器基座口径问题中**可以只改证据强度、不改判定**的那部分：

1. **责任步冲突静默取其一**：一行同时报告两个不同责任步，却仍记 `authoritative`。
2. **动作步与内容步的命中率不可分**：142 个定罪行中 117 行的责任步指向自身散文为空、只有工具调用的事件，
   而指标把两类混在一个数字里。

**未运行任何真实模型服务**；未改任何门限数值；未删除任何既有检查项；未改任何既有权重的含义。
所有读数来自**已归档的真实运行记录**（`reports/rsi_pilot_20260919/**/results/*.json`，2026-09-19 的
GLM-5.3-flash 真实运行）与离线 mock。

## 一、冲突处理：选了"记录冲突 + 降级定位权威"，并对冲突类别做了实测区分

### 选的路与理由

采纳规则写成常量 `LOCALIZATION_STEP_PRIORITY`（`detector/adjudication.py`）并写入每一行的
`localization_sources.adopted_rule`：**被判罪 hearing 的 step tracer 优先；tracer 失败时才回落到该
hearing 的 defense `confirmed_step`；再回落到指控自身的步号**。这是实现原本就有的规则，现在它成为
行内可复现的事实，而不是只活在一个 `if` 分支里。

**降级只作用于新增的 `localization_authority` 轴，不动 `decision_authority`。**

### 为什么不动 `decision_authority`

先读了代码再定：`detection.result.prediction_state` 要求
`decision_authority == "authoritative"`；`evolution/gates.arm_has_infrastructure_failure`、
`metrics._row_health`/`mechanism_row_health`、`localization_behavior_gate`、
`localization_opportunity`、`mechanism_opportunity` 全部读这一项或由它派生的 `prediction`。
把它降级会让**已经成立的真实定罪**从权威集里消失，等于用证据强度问题去改判定——而任务书明确要求
"不影响既有判定而只影响证据强度"。因此新增一条并列的轴，所有下游契约逐字不变（有测试锁定）。

### 实测发现的冲突类别：两类，含义相反

我没有预先假定"tracer 与 defense 不一致就是冲突"，而是先在归档上量了两件事：

| 测量 | 结果 |
|---|---|
| 所有 `substantiated` hearing 是否满足 `confirmed_step == 指控步` | **284/284 全部满足**（契约强制，`detector/defense.py:136`） |
| tracer 与 `confirmed_step` 不一致时的方向 | **earlier 25 次，later 0 次，equal 261 次** |

所以 defense 的 `confirmed_step` **不是**一个独立的定位判断，它是把指控步重述一遍；tracer 与它不一致
时，tracer 必然报**更早**的步，这正是 `LOCALIZATION_LAW` 要它做的事（"find the EARLIEST step"）。此时
采纳的就是 tracer 的步，行内自洽，**不构成矛盾**。把它当作冲突降级，会让这个信号的含义变成"tracer 正常工作了"。

真正的矛盾是另一类：**envelope 的 `proof.step_tracer` 镜像的是首个 hearing 的 tracer，而采纳的是另一个
步**——这才是"同一行两个责任步"。142 例中恰好 3 例：

| 案例 | `proof.step_tracer` 报告 | 采纳 | 冲突类别 |
|---|---|---|---|
| `BFCL/153.json` | 14 | 10 | `first_hearing_tracer_vs_adopted` |
| `BFCL/008.json`（2 次运行） | 3 | 1 | `first_hearing_tracer_vs_adopted` |

因此最终语义是：

- `localization_conflict`：**只**放真正的自相矛盾（上述一类），非空即把 `localization_authority`
  降为 `degraded_localization_conflict`。
- `localization_sources`：**总是**写，包含每一条声明（被判罪 hearing 的 tracer / 其 defense
  confirmed / 指控步 / 首个 hearing 的 tracer）、每条的步号与 `adopted` 标志、被覆盖的来源、以及
  决定规则与 `resolution`（`all_sources_agree` / `tracer_earlier_than_attested_step` /
  `first_hearing_tracer_vs_adopted`）。

任务书提到的 `BFCL/153` / `BFCL/008` 这两处"tracer 说 X、final 说 Y"正是第二类，已全部标出并降级。
归档里的 `Camel/054`、`Octotools/030`、`OpenManus/064` 等 tracer<defense 的情况属于第一类，
记录在 `localization_sources` 但**不**降级——理由如上，并有测试锁住这个区分。

### 对下游契约的影响

**零影响**，且是设计目标而非巧合：`decision_authority`、`prediction`、`responsible_step`、
`responsible_step_kind` 全部与修复前逐字相同。替换验证：把 142 行归档记录重新喂进修好的
`adjudicate()`，**`decision_authority` 变化 0 行，采纳步变化 0 行**（见下表）。新增的
`test_conflict_degradation_leaves_the_localization_gates_untouched` 直接跑真实的
`localization_behavior_gate`，证明带冲突标记的行与不带标记的行得到相同的 `passed` / `joint_delta` /
`joint_improved` / `joint_broken`。因此**不需要替代方案**：`localization_behavior_gate` 不依赖
`localization_authority`，降级不会让它少拿任何行。

## 二、动作步 / 内容步：判定规则与分列读数设计

### 机械判定规则（`detector/packet.py::step_kind`）

在 packet 构建后**只读事件自身的结构**：

| 条件 | 判定 |
|---|---|
| `content` 非空（去空白后） | `content_step` |
| `content` 为空且 `tool_interactions` 非空 | `action_step` |
| 两者都空 | `empty_step` |
| 步号不在 `1..len(events)` | `None`（不可分类，绝不猜） |

既有散文又有工具调用的事件记为**内容步**（该步有可就地检查的散文）。规则不读 gold、不读任务文本、
不做长度或关键词启发、不看任何数字。

**载荷**：定罪的行在 `detector/adjudication.py` 里写入 `responsible_step_kind`（无法定位该步时为
`null`），`detector/result.py` 把它放进 detection envelope。对**早于该字段**归档的行，
`evolution/engine.py::_step_kind_from_record` 用同一份 `{question, history}` 投影重建 packet 后调用
**同一个** `step_kind()`——同样的机械规则、同样的字节。两者都不可得才记 `unclassified`，宁可留未分类
也不把猜测写进分解。

### 分列读数（不改既有权重）

| 位置 | 新增 | 既有 |
|---|---|---|
| `metrics.localization_metrics` | `step_kinds`（4 类 × `scorable`/`hits`/`accuracy`）、`localization_conflict_rows` | 五个头部计数**逐字未变** |
| `metrics.paired_localization_delta` | `baseline_step_kinds`、`active_step_kinds`、`localization_conflict_rows` | `base_joint`/`active_joint`/`joint_delta`/`joint_improved`/`joint_broken` **逐字未变** |
| `engine._step_localization_check` | `step_kind_split`、`baseline_classified`、`active_classified`、`note` | `n`/`errors`/`baseline_hits`/`active_hits`/`not_regressed` **逐字未变**（判据只读原计数） |

**"既有口径不得放宽或收紧"**：命中定义仍是"采纳的 `responsible_step` == 标注 `hallucination_step`"，
不因步的性质加权或折扣。72.6% 这个数字的含义没有变。

**冲突行在分解里被排除**（不按任何一个步记为命中或未命中——两个都上报了，按哪个记都是行本身无法
支撑的断言），由 `localization_conflict_rows` 单独计数。

**`docs/localization.md` 已写明**：两种步都算命中；报告必须分列；动作步与内容步的命中率**不可混读**
（前者答"找到了正确的工具调用"，后者答"找到了正确的那句话"）。

## 三、实测数字（693 例语料，只读，零 provider 调用）

语料定位：`reports/rsi_pilot_20260919/**/results/*.json` 恰好 **142 个 `verified_error`**，与 §32.7
的底审数字一致（该目录含 GLM-5.3-flash 真实运行记录，71 个案例文件带 gold、可评定位）。
复算脚本：`reports/localization_split_20260923/measure_corpus.py`，读数落盘
`reports/localization_split_20260923/corpus_readings.json`。

| 量 | 修复前 | 修复后 |
|---|---|---|
| 定罪行总数 | 142 | 142（未变） |
| —— 采纳步为**动作步** | 117（隐性） | **117（显式 `action_step`）** |
| —— 采纳步为**内容步** | 25（隐性） | **25（显式 `content_step`）** |
| —— 未分类 | 不适用（无此字段） | **0** |
| 整体 gold 命中 | 98/142 = **69.0%** | 98/142 = **69.0%**（未变） |
| 动作步命中率 | 不可分 | **98/117 = 83.8%** |
| 内容步命中率 | 不可分 | **0/25 = 0.0%** |
| 冲突行数 | 3（**全部仍记 `authoritative`**） | **3（全部标出并降级 `localization_authority`）** |
| —— 其中记为权威定位的行 | 142 | **139** |
| `decision_authority` 被本次修复改变的行 | — | **0** |
| 采纳步被本次修复改变的行 | — | **0** |

**两个命中率口径的分母区别（必须明说）**：

- **98/135 = 72.6%** 是 §32 引用的数字，分母**只含 gold 步可定位的行**（135 行，另 7 行 gold 步号缺失）。
  这与 `evolution/metrics.localization_metrics` 的口径（`annotated_positive` 只数有标注的正例）一致。
- **98/142 = 69.0%** 是**本报告新算的、分母含全部定罪行**的版本。
- 两者都不是新口径：本次修复**没有**改动任何一个分母或分子，只是把同一批命中拆成两栏。

**分列读数揭示的实质**（这是分解的价值所在）：整体 69.0% 由动作步撑起——动作步 83.8%（98/117），
**内容步 0/25**。也就是说，在这批数据上，检测器从未在"某句 assistant 散文本身是根因"的行上定位对过一次。
混在一起的平均数掩盖了这一点；分列之后它是可读的。gold 侧的结构也支持"动作步是多数"：
443 个有标注的案例里 gold 步为 `tool_only` 248、`prose_only` 115、`prose+tool` 80。

## 四、测试名与验证命令

新增文件 `evolution/tests/test_localization_conflict_and_split.py`（**17 项**）：

任务 1（冲突记录与降级）：
- `test_tracer_naming_an_earlier_step_is_recorded_not_charged` —— tracer 比 defense 重述的指控更早时
  **只记录不降级**（实测 284/284、25/25 支撑的分类）
- `test_first_hearing_tracer_disagreeing_with_adopted_step_is_flagged` —— 真正的自相矛盾被标出并降级
- `test_agreeing_sources_record_no_conflict`
- `test_tracer_earlier_than_the_allegation_is_not_a_conflict`
- `test_defense_fallback_step_is_adopted_when_the_tracer_failed`
- `test_upgraded_defense_verdict_is_not_read_as_a_conflicting_step`
- `test_conflict_degradation_leaves_the_localization_gates_untouched` —— 跑真实 gate 证明下游零影响
- `test_conviction_envelope_records_the_step_kind_and_conflict`

任务 2（机械判定与分列读数）：
- `test_step_kind_reads_event_structure_not_text`
- `test_step_kind_is_content_for_an_event_that_both_speaks_and_acts`
- `test_step_kind_reports_empty_when_the_event_has_neither`
- `test_split_metrics_exclude_conflicted_rows_and_never_mix_kinds`
- `test_paired_delta_decomposes_each_arm_by_its_own_kind`
- `test_rows_without_the_field_are_unclassified_not_guessed`
- `test_engine_split_classifies_legacy_rows_from_the_trajectory`
- `test_engine_split_verdict_does_not_read_the_decomposition`

任务 3（语料）：
- `test_archived_corpus_has_no_authoritative_row_with_two_responsible_steps` —— 只读重放全部 142 行，
  断言 3 例冲突全部被标出、117/25 的步结构、以及 `decision_authority` 与采纳步未变

**"回退该修复 → 测试失败"逐项验证**（不是碰巧通过）：

| 回退的改动 | 失败测试数 |
|---|---|
| 冲突检测恒返回 `None` | **3** |
| `step_kind` 恒返回 `None` | **6** |
| `localization_metrics` 去掉 `step_kinds`/`localization_conflict_rows` | **1** |
| `_step_localization_check` 去掉 `step_kind_split` | **2** |
| `paired_localization_delta` 的 `localization_conflict_rows` 恒为 0 | **1** |

复现：

```bash
python -m pytest -q                                              # 951 passed
python -m pytest evolution/tests/test_localization_conflict_and_split.py -q   # 17 passed
python scripts/verify.py                                         # 全项 PASS（见下）
python reports/localization_split_20260923/measure_corpus.py      # 重算本报告的读数
```

**基线对照**

| 项 | 值 |
|---|---|
| 本任务开工前 pytest 基线 | 917 passed |
| 期间另一代理并行落地的工作 | +18（`test_noise_floor.py` 等）|
| 本任务新增 | **+17** |
| 当前 pytest | **951 passed, 0 failed** |
| `scripts/verify.py` | 全部检查项 PASS |

## 五、未做与不确定的部分

- **未修** §32.7 的另两条：机械定罪谓词在 250 个 gold-clean 案例上 2.8% 误报且不经 defense；
  `BFCL/008`/`024` 单元级判定 8 次重复 6/2 分裂。这两条改变的是**判定**而非证据强度，需要先定口径。
  本次刻意只碰"不改判定"的部分。
- **未运行任何真实服务**。语料全部来自已归档运行；重放只走纯函数 `adjudicate()`。
  `evolution/tests/conftest.py` 的 HTTP 守卫全程生效，且本次没有写任何 `python -c` 探测脚本发请求。
- **本报告的命中率是"归档运行在该代码版本下的读数"，不是当前检测器准确率**：142 行由 2026-09-19
  的运行产出，其间代码有变动（含 §32 的 19 项修复）。重放证明的是**定位记录语义**的变化，不是检测能力。
- **内容步 0/25 的样本量小**（25 行），0% 是描述不是估计；它说明"混读会掩盖什么"，不足以断言内容步定位
  的总体能力。要下这个结论需要新的独立运行。
- **`responsible_step_kind` 对既有归档行不可得**，`_step_kind_from_record` 是**重建**（同一规则、
  同一字节），但若某行对应的数据集文件缺失或被改动，该行会落进 `unclassified` 而不是被猜。本次语料
  0 例落空（142/142 全部可分类）。
- **未在 `evolution/engine.py` 的 `_run_cycle`/`_run_unit`/`_instrument_report`/`_audit`/
  `run_governance` 与 `evolution/gates.py` 的 `behavior_gate`/`localization_behavior_gate` 内改动
  任何逻辑**（按任务书的并行边界）；只改了模块级的 `_step_localization_check` / `_step_kind_from_record`
  与 `detector/adjudication.py` 的责任步选取那一段。
- **`detector/interventions.py` 未改动**（另一代理的工作区域）。`conviction_verdicts_for` /
  `terminal_authority` 逻辑本次未触碰。
- **本任务不能宣称**：定位准确率提升、内容步定位可用、冲突率在更大语料上仍为 2.1%（3/142）。
  它只说明：冲突不再静默、两类步的读数不再混在一起且都可复算。
