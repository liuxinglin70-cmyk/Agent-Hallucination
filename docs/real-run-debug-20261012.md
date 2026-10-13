# 真实运行前代码排查（检测器 + 自进化框架）

- 排查日期：2026-10-12
- 初次排查基线：`main` @ `6cec2d2`（当时工作区干净）；当前为该 HEAD 加用户修复和本次续修的未提交工作区，源码身份见附八。
- 排查范围：`detector/` + `evolution/` 的重点运行路径；原轮未完成逐文件深审，实际覆盖见附三。续审另覆盖实验入口、冻结恢复、开发预算、组合准入、治理和真实运行预检。
- 目标真实运行：`evolution/configs/full693_g0_evolution_20260928.json`（cycles=3、workers=8、development 开启、archive=cycle_end）配合 `detector/configs/autodl.json`
- 本文件性质：保留原轮只读审查，附八记录用户授权后的代码续修与验证。各轮证据分别标注；原轮声称已复现但未保留脚本的内容，不能自动视为本轮独立复现。
- **阅读入口：附十一是本轮统一口径与风险机制的实施/验收；附九、十保留先前五例结果与分析，附八保留续修验收**。原条目中已撤销的 B3、B7、C9、C11、C13 不进入修复清单；A1、A2、A4、A5、B6、B12、C14、C17 的表述已收窄。附五的 D0 是修复前阻断项，当前预检已通过校准资格检查。

---

## 0. 结论速览

### 0.1 目标配置的条件风险（可达不等于必然触发）

| 编号 | 位置 | 一句话 | 严重度 | 置信度 | 已复现 |
|---|---|---|---|---|---|
| **A1** | `evolution/engine.py:2473`（+`2458`、`2904`） | 缺陷注入审计的"排除不可测案例后重新判定"是**死代码**，出现不可测案例时，排除后重新判定的路径被跳过，可冻结审计并提前终止实验 | 高 | 高 | 否（调用点分析确证） |
| **A2** | `detector/adjudication.py:119-125`（+`detector/defense.py:345-349`） | `substantiated_upgraded` 判决**永远无法定罪**，被静默改判为 `invalid`，走"辩护未定"分支并污染定位读数 | 高 | 高 | 是 |
| **A3** | `evolution/research.py:75/97/151` | 冻结清单里的 `max_versions` 预算上限**从未被强制执行**（仓库自己的日志里已有超限记录） | 高 | 高 | 否（grep 确证） |
| **A4** | `detector/runner.py:527`（+`detector/api.py:174`） | **降级为兜底风险**：若该异常逃出才会中断批次；当前可选预算生产入口有局部捕获，自然逃逸尚未证实 | 中 | 异常传播高、自然可达性待证 | 合成传播；本轮正常路径反证 |
| **A5** | `evolution/gates.py:149` | 任何 `prediction is None` 的行都会把整条臂判为"不可测"，使声明的 resolution 目标永远无法被评分，并阻断单元准入 | 高 | 高 | 是 |

### 0.2 会削弱防线 / 影响读数口径的

| 编号 | 位置 | 一句话 | 严重度 | 置信度 | 已复现 |
|---|---|---|---|---|---|
| B1 | `evolution/engine.py:4290-4305` | 超出 `max_admits` 的合格单元被静默丢弃，并被记忆写成"已证伪"（`gate=None, reason=""`） | 中 | 高 | 否 |
| B2 | `detector/evidence.py:511` | 未决冲突的说明用裸子串匹配，被争值里的一个数字即可放行（fail-open） | 中 | 高 | 是 |
| B3 | `detector/replay.py:223` | **撤销**：当前代码正确比较 `ok` 与 `state == "ok"`，本轮反例实际抛错 | 非缺陷 | 高 | 本轮反证 |
| B4 | `evolution/gaps.py:207` | 已解决的 gap 被再次观测后仍永久关闭，能力缺口从积压中消失 | 中 | 高 | 是 |
| B5 | `evolution/engine.py:867`、`:1639` | 读取 `actionability["polarity"]`，而生产者从不返回该键 → 永远存 `""`，记忆检索的极性项永不命中 | 中 | 高 | 是 |
| B6 | `evolution/engine.py:2407` | 缺陷审计用**宽松参数**调用 `structural_gate`，与真实周期校验不对等 | 中 | 高 | 是 |
| B7 | `detector/runner.py:460,502,597` | **撤销**：invocation / 最近完成测量 / lifetime 是三个有意区分的成本口径 | 非缺陷 | 高 | 合同与测试反证 |
| B8 | `evolution/research.py:291` | baseline 预算耗尽时从 `execute()` 抛异常，绕过文档承诺的 `partial` 报告（不落任何报告文件） | 中 | 高 | 否 |
| B9 | `evolution/engine.py:4798` 附近 | `ActiveRegistry.retire` 的 `ValueError` 无调用方捕获；公开的 `check_retire_dependencies` 零调用 | 中 | 中（可达性） | 否 |
| B10 | `detector/predicates.py:247-251` | `required_action` 名称抽取会把冠词 `the` 当成工具名，制造"任务要求未执行"的伪违规（对当前语料不可达） | 中 | 高 | 是 |
| B12 | `evolution/promotion.py:1688-1697` | 晋升的"本 claim 是否可测"判定与框架自己的 opportunity 定义不一致（`boundary`/`resolution` 被误判为不可测） | 中 | 高 | 是 |
| B13 | `evolution/meta_eval.py:81` | 未启用审计时 `all(())` 恒真，`defect_injection_audit.verdict` 空过为 `pass`，而它是最终 `overall` 的 AND 项 | 中 | 高（可达性中） | 是 |
| B14 | `evolution/promotion.py:1646-1650` | 晋升门禁拿不到稳定性标注，`meets_m` 会在"仪器读两次不一样"的案例上按原始增益计分 | 中 | 高 | 否 |

### 0.3 低危 / 契约与可读性

| 编号 | 位置 | 一句话 |
|---|---|---|
| C1 | `detector/judge.py:187-189` vs `:282-316` | 文档承诺"平票时阶段降级"，实现把平票当 `ok` 且可判 `authoritative`（`api.py:72` 的强制复扫是部分缓解） |
| C2 | `detector/adjudication.py:507,523` | `decisive_facts.defense_reason` 取自 `reasons[-1]`，recall 降级时填的是 recall 的 error_type |
| C3 | `detector/adjudication.py:236` | 非首听证 tracer 分歧一律标成 `..._TRACER_EARLIER`，tracer 更晚时标签为假 |
| C4 | `detector/adjudication.py:543`、`:619-628`、`:477-479` | 死赋值 / 只写不读的 `working_positive["evidence_refs"]` / 恒真的 guard 条件 |
| C5 | `detector/defense.py:392` | docstring "Never raises" 与 `raise_if_terminal` 实际会抛相矛盾 |
| C6 | `detector/step_uncertainty.py:124` | `state` 的承重语义（`!= "selected"` 会让 `closure_state` 降级）缺少文档 |
| C7 | `detector/packet.py:429` | `has_subsequent_activity` 的切片语义未说明（经核对**无** off-by-one） |
| C8 | `detector/packet.py:465` vs `:491` | `_answer_call` 认可 `finish` 为投递，`answer_tool_is_delivery` 按子串排除 `finish` |
| C9 | `detector/measurement.py:292` | **撤销**：未知总请求数返回 `null`，避免把已知部分冒充总量 |
| C10 | `evolution/ledger.py:139,103` | 撕裂的 ledger 行抛 `JSONDecodeError`，而非文档承诺的 `{"valid": False}` |
| C11 | `detector/runtime.py:35-49` | **撤销**：共享校验不存在“只能一个点、必须三位数字”的约束 |
| C12 | `detector/interventions.py:2033` | 同 family 同名 predicate 的全局 fact 被后者覆盖，且不记冲突 |
| C13 | `detector/evaluation.py:45` | **撤销为缺陷**：全案例准确率把未决计为未答对；报告应结合覆盖率读取 |
| C14 | `evolution/splits.py:212` | `except Exception: pass` 吞掉 marker 解析失败；`read(commit=False)` 分支是死代码 |
| C15 | `evolution/capability_planning.py:373`、`evolution/backends.py:738` | 永不触发的守卫；被接受但完全不使用的 `fired_always` 参数 |
| C16 | `evolution/backends.py:851` | `base_predictions` 中显式 `None` 被静默替换为 gold，"注入 unresolved"不可达 |
| C17 | `detector/adjudication.py:224-227` | `overridden` 的列表推导里同一个条件写了两遍 |
| C18 | `evolution/promotion.py:2093-2098` | **提升为高风险**：竞争分支只校验 `protocol_hash`，可接受不同候选/输出目录消费同一协议；本轮已复现 |
| C19 | `evolution/metrics.py:520` | `new_errors = new_fn + new_fp` 不含 docstring 承诺的"gold=false 且正确→unresolved"转变（被 `unresolved_added` 守卫掩盖） |

**阻断性判断**：按 `full693_g0_evolution_20260928.json` + `detector/configs/autodl.json` 的当前组合：

- **`A4` 的触发前提未启用**——`causal_audit.mode` 默认 `off`（`detector/config.py:71`），且全仓库没有任何生产配置把它设为 `shadow`/`tracer_context`（只出现在 `detector/tests/`）。所以它**不阻断**本次运行；当前正常预算耗尽路径有局部保护，不能据合成抛错便断言整个机制确定失效。
- **`A1` 与 `A2` 的触发路径在目标配置中启用**，但没有真实模型运行证据证明本次一定出现超时或分裂票：
  - `A1`：审计一旦遇到任一不可测的 S+G 案例，`state` 直接变成 `frozen_after_defect_injection_audit`，`run_experiment` 在 `evolution/engine.py:4507-4508` 跳出、`run_full_self_evolution` 抛错，整轮 3-cycle 实验结束。而专门为修这个问题写的 ~100 行代码永远不会被执行。
  - `A2`：`defense_route.votes=3, vote_topup=2`（`detector/configs/autodl.json:24-29`），只要 3 张辩护票出现 `[substantiated, refuted, inconclusive]` 这类分裂，判决即被改判，定位读数被污染——而 `localization_min_joint_cases` / `localization_joint_broken_allowed` 正是本次运行要读的门禁。
- **`A3` 不在本次路径上**：`research.py` 只被 `scripts/run_research_round.py` 使用，evolution 主流程不走它；它的价值在于说明"预算上限写了但没接线"这类缺陷在本仓库确实发生过，且会造成实际超支（日志已记录两次）。
- **`A5` 与 `A1` 共享不可测性判定问题**，是否触发取决于实际行状态，不能写成本次必然命中：
  - `A5`（太严）：控制臂或候选臂里存在"**权威但未决**"（`prediction is None`、`authority=authoritative`）的案例时，整条臂被判为不可测，该单元永远无法 `qualified`。该状态合法且是设计产物——`judge` 在观察冲突（MATERIALITY grounding rule 6）下会给出"未决"，`adjudication` 会落成 `unresolved`。因此它把一个诚实读出的"语义未决"当成"基础设施失败"处理，直接阻断准入。
  - `A1`（太宽）：它需要的是"黄金标签缺失 / 非权威 / 运行时无效 / 阶段失败"这类**真正**不可测的行（因为审计路径拿到的是丢弃了 `result` 的摘要，见 A1 证据 2），一旦这样的行出现在审计的 S+G 里，整轮实验终止。24 个案例的 S+G 在 3 小时单案例上限、且实测慢案例长达 108 分钟（`artifacts/cost_model_693.json`）的条件下，这个条件是可达到的。

**核验后的启动结论**：当前目标配置的只读预检返回 `NOT READY`，已发现校准源码指纹不匹配（D0）。需先解决 D0，并处理 A2、A1/A5、组合定位 D1；A3、A4、传统晋升 C18 属独立入口风险。实际修复顺序与验收标准以附七为准。

---

## A1（高）审计遇到不可测行时跳过排除后重判，可能提前冻结实验

### 结论

`_defect_rejection_after_exclusions`（`evolution/engine.py:394-493`）只有一个生产调用点。**本轮已反证“全局不可达”**：健康 Mock 周期中该函数被调用 1 次；在缺陷臂注入一个不可测 sentinel 后，调用数为 0，周期冻结。准确的问题是：需要排除不可测行的失败路径被前置的 `evaluable=False` 检查跳过。另一个独立问题是候选行摘要丢失 `result`，使辅助函数漏看运行时/阶段健康信息；并非所有输入下都恒为空。

### 证据（三重）

**(1) 唯一调用点，在不可测行参与的失败路径上被前置条件屏蔽。**

`evolution/engine.py:2438-2479`：

```python
                if not report["audit_passed"]:
                    ...
                    unmeasured = any(
                        verdict.get("passed") is False
                        and verdict.get("evaluable") is False
                        for verdict in (
                            unit.get("harmlessness") or {},
                            unit.get("behavior") or {},
                            unit.get("m") or {},
                        )
                    )
                    if unmeasured:
                        infrastructure = (
                            "defect evaluation has insufficient measurement "
                            "coverage"
                        )
                    else:
                        infrastructure = _defect_rejection_after_exclusions(
```

而 `_run_unit` 在返回前对三个判定统一施加了标记（`evolution/engine.py:2915-2921`）：

```python
            "harmlessness": _mark_unstable(_mark_unmeasured(harmlessness)),
            "behavior": _mark_unmeasured(behavior),
            ...
            "m": _mark_unstable(_mark_unmeasured(m)),
```

`_mark_unmeasured`（`evolution/engine.py:2836-2843`）在 `unmeasured` 非空时会把**每一个** `passed=False` 的判定标成 `evaluable=False`：

```python
            if not unmeasured or not isinstance(verdict, dict):
                return verdict
            if verdict.get("passed") is not False or verdict.get("evaluable") is False:
                return verdict
            marked = dict(verdict)
            marked["infrastructure_failure"] = True
            marked["evaluable"] = False
```

`unmeasured` 的来源是 `evolution/engine.py:2720`：

```python
        unmeasured = sorted(_unmeasurable_cases([*control, *candidate_rows]))
```

它把**对照臂与候选臂合起来**扫描。于是逻辑闭合：只要存在不可测行（无论落在哪一臂），任何失败的判定都带 `evaluable=False` → `2458` 行的 `unmeasured` 为真 → `2473` 行的 else 分支永远不会走。若三个判定都没失败，则 `audit_passed=True`，跳过整块。若失败来自正常业务门拒绝且无不可测行，else 则可以调用该函数，故不能称为全局死代码。

**(2) 摘要丢失运行时/阶段信息，但不是所有不可测性信息。**

`evolution/engine.py:2904` 返回给调用方的是**有损摘要**：

```python
            "candidate_rows": _summarise_rows(candidate_rows),
```

`_summarise_rows`（`evolution/engine.py:143-153`）只保留 `case/gold/prediction/state/authority`，**丢弃 `result`**。而 `arm_has_infrastructure_failure`（`evolution/gates.py:156-166`）判断不可测要靠 `result`：

```python
        result = row.get("result") or {}
        if result and not bool(result.get("runtime_valid", True)):
            return f"runtime-invalid row for case {row.get('case')}"
        for status in (result.get("stage_status") or {}).values():
```

`result` 缺失 → `result` 为空 dict → 两个检查都不触发。`_defect_rejection_after_exclusions` 的开头（`evolution/engine.py:419-424`）正是：

```python
    candidate_rows = list(unit.get("candidate_rows") or ())
    unusable = _unmeasurable_cases([*baseline_rows, *candidate_rows])
    if not unusable:
        # Nothing was excluded, so the verdict already rests on measurable
        # rows only.
        return ""
```

注意 `prediction is None` 与 `authority != "authoritative"` 在摘要里仍保留，所以只有"**权威但运行时无效/阶段失败**"这类不可测行会被漏掉——而 `_mark_unmeasured` 用的是完整行，比摘要**更敏感**。结论：辅助函数的判定范围是 `_mark_unmeasured` 的真子集，构不成"重新派生"。

**(3) 测试覆盖不到。**

`_defect_rejection_after_exclusions` 的生产调用点只有 `evolution/engine.py:2473` 一处（全仓库 grep 确认）。`evolution/tests/test_audit_inconclusive_scope.py`（5 个用例）与 `evolution/tests/test_measurement_integrity.py`（2 处）都**直接调用该函数**，因此它们全部通过，却看不到引擎从不调用它。`evolution/tests/test_engine_smoke.py:1445` 甚至手工把 `unit["candidate_rows"] = unresolved_rows` 覆盖掉摘要，进一步说明摘要形状与函数期望不匹配。

### 影响与触发条件

- 触发：缺陷审计的候选臂（或对照臂）的 S+G 中**任意一个案例**不可测（provider 故障、案例超时、非权威终态、阶段失败）。
- 后果链：`_audit` → `verdict["frozen"]=True` → `_run_cycle`（`evolution/engine.py:3392-3398`）返回 `{"state": "frozen_after_defect_injection_audit"}` → `run_experiment`（`:4507-4508`）`if result.get("state") != "completed": break` → 整个实验停止。
- `evolution/engine.py:3377-3390` 的有界重试（`audit.max_attempts`，默认 2）**无法清除**它：重试会经 `_ArmRunner.run` → `CycleJournal.evaluate`（`evolution/checkpoint.py:202-213`）直接回放同一份已持久化的 arm 观测，失败行原样返回。
- 注释 `evolution/engine.py:2449-2457` 明确写着这个整批扫描"已被替换"，并给出了 2026 年那次三周期运行因一个 sentinel 超时而冻结的复盘——即**已发生过的真实故障模式**。

### 本轮加强复现：排除后覆盖足够

采用 24 例合成数据、8 个 sentinel（4 TP/4 TN），最低 protected TP/TN 均设为 2。缺陷臂仅第一个 sentinel 注入 None，其余行健康：

```text
healthy remaining sentinel: TP=3, TN=4（均满足最低 2）
production helper_calls=0
cycle state=frozen_after_defect_injection_audit
manual _defect_rejection_after_exclusions(same unit/baseline): infrastructure reason=""
```

因此这次冻结不能仅以排除后覆盖不足解释：现有辅助函数自己会认定其余行足以承载拒绝，但生产链没有调用它。原 4-sentinel 反例只证明跳过分支；本反例进一步排除了保护覆盖不足这一解释。这里仍保持协议最低覆盖，不建议无条件丢弃故障行并宣称通过。

### 建议核验方式

1. 在 `evolution/engine.py:2473` 加一次计数（或 monkeypatch 计数），跑一个 S+G 中含一个不可测案例的 mock cycle，断言计数为 0。
2. 修复方向二选一：让 `_audit` 用**完整行**而不是摘要参与判定（把 `candidate_rows` 与 `_summarise_rows(candidate_rows)` 分开保存），并在 `2458` 行之前先用辅助函数重新派生；或直接删除死代码并承认"整批扫描"是有意保留的策略，同步修正注释。
3. 增加一个**引擎级**测试（而非直接调用辅助函数），断言"一个无关 sentinel 不可测时审计仍然通过"。

---

## A2（高）`substantiated_upgraded` 判决永远无法定罪，被静默改判为 `invalid`

### 结论

`CONVICTING_VERDICTS` 包含 `substantiated_upgraded`（`detector/adjudication.py:53`），但唯一能产生该判决的地方**必然**把 `confirmed_step` 置为 `None`（`detector/defense.py:345-349`）；而 `detector/adjudication.py:119-125` 要求 `confirmed_step == alleged_step`，于是它被判为 `invalid`，`_convicted_hearing` 返回 `None`，流程落入"辩护未能定论"分支，检测权威性降为 `degraded_judge_only`。

### 证据

`detector/adjudication.py:53` 与 `:108-137`：

```python
CONVICTING_VERDICTS = frozenset({"substantiated", "substantiated_upgraded"})
...
def _hearing_verdict(
    hearing: Mapping[str, Any], packet: Mapping[str, Any] | None = None,
) -> str:
    parsed = (hearing.get("defense_result") or {}).get("parsed")
    parsed = parsed if isinstance(parsed, Mapping) else {}
    verdict = str(parsed.get("verdict") or "none")
    if verdict not in CONVICTING_VERDICTS or packet is None:
        return verdict
    alleged_step = hearing.get("step")
    confirmed_step = parsed.get("confirmed_step")
    refs = parsed.get("evidence_refs") or []
    if (
        not isinstance(alleged_step, int)
        or confirmed_step != alleged_step
        or not evidence_refs_support_step(packet, refs, alleged_step)
    ):
        return "invalid"
    return verdict
```

`detector/defense.py:335-349`（唯一生产者）：

```python
    if collapsed == UPGRADED_SUBSTANTIATED:
        anchor = next(
            (b for b in ballots if b.get("verdict") == "substantiated"),
            ballots[0],
        )
        ...
        parsed = {
            **anchor,
            "verdict": UPGRADED_SUBSTANTIATED,
            "confirmed_step": None,
        }
```

`_vote_verdict`（`detector/defense.py:117-140`）只在**没有**严格多数 `substantiated` 时才返回 `substantiated_upgraded`：

```python
    winner, _ = majority_vote(list(verdicts), on_tie="")
    if winner == "substantiated":
        return "substantiated", False
    if winner:
        return winner, False
    if any(v == "substantiated" for v in verdicts):
        return UPGRADED_SUBSTANTIATED, True
    return "inconclusive", True
```

因此产生 `substantiated_upgraded` 的投票多重集里，`substantiated` 必然不占多数。再叠加 `_validate_defense_evidence`（`detector/defense.py:99-110`）对 `substantiated` 强制 `confirmed_step == alleged_step`，可以推出：只要判决是 `substantiated_upgraded`，`confirmed_step` 就一定是 `None`。**没有任何生产者能产出 `substantiated_upgraded` + 非空 `confirmed_step` 的组合。**

### 复现（本次实跑）

```
CONVICTING_VERDICTS = ['substantiated', 'substantiated_upgraded']
['substantiated', 'refuted', 'inconclusive'] -> ('substantiated_upgraded', True)
['substantiated', 'substantiated', 'refuted'] -> ('substantiated', False)
['substantiated', 'refuted', 'refuted']       -> ('refuted', False)
_hearing_verdict(substantiated_upgraded, step=1) = 'invalid'
```

### 影响

- **终态的二元值不变，但测量权威性改变**：仍为 `verified_error`，却变成 `degraded_judge_only`。下游只计权威行时，会影响覆盖率和可测性，而不只是定位步号。
- **定位（localization）读数被污染**：`_convicted_hearing` 返回 `None` 后，`responsible_step` 取自 `positive.get("step")`（`detector/adjudication.py:569`），即**未经 tracer 校正的指控步号**；而正常定罪路径会用 tracer 的步号（`:619-628`）。在"首听证被 refuted、后续 recall 听证升级"的情形下，行里会写 `positive_source="judge"` 与**被 refuted 的那次听证**的步号，且 `localization_authority` 仍是 `"authoritative"`。
- **死代码**：`detector/adjudication.py:631` 的 `degraded = verdict == "substantiated_upgraded"` 与 `:657-660` 描述该情形的注释都不可达。
- 消费方：`result.py:34-39` 把 `responsible_step` 写进信封；`evolution/metrics.py` 的 `row_responsible_step` / `paired_localization_delta` / `localization_metrics` 都是按它计分。而 `full693` 配置里 `localization_min_joint_cases: 1`、`localization_joint_broken_allowed: 0` 正是本次运行要读的门禁。
- 现有测试为何没抓到：`detector/tests/test_voting_hearings.py:270-277` 的 `_hearing` 辅助函数把 `confirmed_step` 设为 `step if verdict.startswith("substantiated")`，于是 `substantiated_upgraded` 也拿到非空步号——**这是任何生产者都产不出的形状**。本次实跑 `detector/tests/test_voting_hearings.py` + `evolution/tests/test_localization_conflict_and_split.py`：35 passed，其中采用 `_hearing` 构造升级 verdict 的裁决用例没有覆盖真实 emitted 形状；不能说这 35 个测试全部从错误分支通过。

### 建议核验方式

- 单元级：`_hearing_verdict` 对 `{"verdict": "substantiated_upgraded", "confirmed_step": None, "evidence_refs": [...]}` 应返回定罪，而不是 `"invalid"`。
- 集成级：把 `test_voting_hearings.py` 的 fixture 改成真实 emitted 形状（`confirmed_step=None`），观察现有断言是否失败。
- 修复方向二选一并在代码注释中固定契约：要么在 `_hearing_verdict` 里把"`substantiated_upgraded` + null `confirmed_step`"视为"无确认步号"而非矛盾；要么停止在 `defense.py` 里清空该字段并同步更新两处注释。

---

## A3（高）research 冻结清单的 `max_versions` 上限从未被强制执行

### 结论

`ResearchBudget.max_versions` 在 `evolution/research.py` 中出现 3 次（定义、`as_dict` 报告、清单解析），**没有任何比较**。版本上限在该模块内不存在，只有部署侧 `development.py:661-662` 有一个同类上限。

### 证据

`evolution/research.py:75`、`:97`、`:151`：

```python
    max_versions: int = 4
...
                "max_versions": self.max_versions,
...
            max_versions=int(budget.get("max_versions", 4)),
```

`grep -n max_versions evolution/research.py` 只返回这三行（外加 `versions_created` 的若干赋值）。与之矛盾的文档承诺，`evolution/research.py:23-25`：

```
- Budget: per-stage case-evaluation caps, per-route proposer-call caps and a
  version cap are counted; an exhausted budget stops the run as ``partial``
  instead of padding results.
```

`evolution/research.py:891`、`:906` 会把 `caps` 与 `used` 并排写进报告，读者会以为上限被遵守。

### 触发条件与实测记录

每条 route 都会创建 draft + revision（`evolution/research.py:1045-1046`），resume 时还会重新起草（`:1041-1045`），全部经 `_new_version_id()`（`:697-700`）且无任何检查；每个版本要花 `len(cases)` 次全量检测评估（`:589-596`）。仓库自己的实验日志已经记录过两次超限：

- `docs/experiment-log.md:17890`：`"caps": {... "max_versions": 1}` 而 `"used": {... "versions_created": 2}`
- `docs/experiment-log.md:18399`：`"max_versions": 4` 而 `"versions_created": 5`

### 与本次运行的关系

`max_versions` 只在 research 通道（`scripts/run_research_round.py:315`）生效，evolution 主流程不走该路径。所以它**不阻断**本次 `full693` 运行，但它是一处已实际发生过的预算失控。

### 建议核验方式

在 `_new_version_id`（`evolution/research.py:697`）内断言 `versions_created <= max_versions` 并跑现有测试套件；或在 `--dry-run` 清单里设 `max_versions=1` + `routes: ["B","C"]`，数 `run/v*` 目录数（预期 > 1）。

---

## A4（中，降级）AddedBudgetExceeded 顶层兜底缺口，自然生产逃逸未证实

`Detector.detect` 顶层只捕获 ProviderError / CaseDeadlineExceeded；如果 AddedBudgetExceeded 真从 `_detect` 逃出，runner 不会将它规范化为案例结果，bounded_map 会传播异常。原 FakeDetector 强制抛错与直接手动进入 added_request_scope 的探针只能证明这一传播链，不能证明“开启因果审计后两个普通请求必然炸批次”。

本轮追踪了全部受预算的常规入口：

- causal_audit 的 added_request_scope/provider_call 位于 AddedBudgetExceeded handler 内；
- build_causal_diagnostics 的 begin、causal_recall_decision 的 check 均局部捕获；
- negative review 的 start_negative_review / added_request_scope / 递归 _detect 整块被 API 捕获并保留基础判定；
- Tracer 的 reserve_context 捕获耗尽并去掉 context；普通 Tracer provider_call 不因因果模式开启就自动进入 added_request_scope。

本轮直接以显式 MonkeyPatch 驱动已有反证测试，三项断言通过：

```text
test_optional_parallel_budget_exhaustion_preserves_run: PASS
test_budget_exhausted_review_keeps_base_result_and_actual_cost: PASS
test_context_budget_failure_keeps_base_tracer_and_records_fallback: PASS
```

因此没有独立证据确认当前正常入口的自然逃逸。目标配置 causal_audit.mode=off，更不应列为当前高危启动阻断。顶层补一层预期案例异常保护可以作为 P2 方案，但应先找真实调用边界的反例，保留 base_detection_result 和实际成本，不吞 fatal/cancel/programming error。EXHAUSTED_ERRORS 的分类需要在这种异常被正式规范化为 envelope 时再接入；当前已处理的可选预算耗尽不应把仍有效的基础判定降为 infrastructure_failure。

---

## B1（中）超出 `max_admits` 的合格单元被静默丢弃并被记忆写成"已证伪"

`evolution/engine.py:4290-4305`：

```python
        selected_units: list[Mapping[str, Any]] = []
        selected_entry_count = 0
        for unit in units:
            size = len([*unit["partners"], unit["candidate"]])
            if selected_entry_count + size > max_admits:
                continue
            selected_units.append(unit)
            selected_entry_count += size
        units = selected_units
```

与 docstring `evolution/engine.py:4215-4220`（"Admit every qualified unit; … the rest are rejected"）不符：被 `continue` 丢掉的单元**没有**原因、**没有** `dropped_entries`（只有 M 失败分支会设，`:4383`）、**没有**进 `report["combination"]`（`:4140-4145`）、也**没有** `rejected` 账本事件（`:3949-3986` 只在 `not verdict["passed"]` 时触发，而合格单元四道门全过）。

随后 `_record_cycle_experience`（`evolution/engine.py:2283-2290`）走 `or {}` 兜底：

```python
                rejection = rejected_by_id.get(intervention_id) or {}
                outcome = "rejected"
                gate = str(rejection.get("gate") or "") or None
                reason = str(rejection.get("reason") or "")
                unevaluable = bool(rejection.get("not_evaluable"))
```

而 `evolution/memory.py:210-216` 把它判为已证伪：

```python
            "falsified": (
                record.get("outcome") == "rejected"
                and not bool(record.get("unevaluable"))
            ),
```

结果：本次配置是 `max_proposals: 6` / `max_admits: 2`，一个**通过全部门禁**的候选既不被准入，又被记成"已证伪的机制"（`gate=None, reason=""`），与该方法 docstring "falsified is True only when a gate rejected for a substantive reason" 直接冲突。现有测试只覆盖 2 个单元 / `max_admits=2` 的情形（`evolution/tests/test_engine_smoke.py:574-677`）。

---

## B2（中）`citation_error` 的冲突说明用裸子串匹配，短值即可绕过（fail-open）

`detector/evidence.py:507-519`：

```python
        note = " ".join(str(conflict_note or "").split())
        for conflict in unresolved:
            disputed = set(conflict["evidence_ids"])
            values = {str(value) for value in conflict["values"]}
            addressed = any(token in note for token in disputed | values)
            if not addressed:
                return (
                    f"{where} grounds a decisive finding on unresolved "
                    ...
```

`values` 是任意短字符串。当被争值是数字（数值型任务里是常态）时，任何含该字符的说明都会放行。该检查**承重**：`detector/recaller.py:252-257` 用 `require_settled=bool(content_error and causal)` 决定一个候选能否升级为正式指控。

### 复现（本次实跑）

构造含未决冲突（`widget/count` 有 `4` 与 `7`）的快照：

```
no note             -> w grounds a decisive finding on unresolved conflicts ['E1','E2'] (values ['4','7']) without resolving them ...
note 'irrelevant 4' -> None        <-- 通过了
note 'checked'      -> w grounds a decisive finding on unresolved conflicts ...
```

`"I reviewed 4 unrelated things"` 里那个孤立的 `4` 就足以放行。

修复取向：不要把“提到冲突”当成“冲突已解决”。仅增加词边界仍会放行 `I reviewed 4 unrelated things`；应让决定性结论引用已验证的 resolution 或显式返回未决，普通 `conflict_note` 只作解释，不授予定罪/升级权。补“无关短数字”和“提及 E1 但未解决冲突”负例。

---

## B3（撤销）冻结阶段的一致性校验有效

本轮读取 `detector/replay.py:223` 并直接调用 `_frozen_stage`：

```text
ok=True + state=provider_error -> ValueError: frozen judge result/status authority mismatch
```

实现比较的是 `result_copy["ok"] != (status_copy.get("state") == "ok")`。原报告展示的代码片段本身已是正确条件，但文字论证却改成读取 `status_copy.get("ok")`，与源码不符；原“正常返回”记录未能独立复现，应撤销。没有必要修改该守卫。

---

## B4（中）已解决的 gap 被再次观测后仍永久关闭

`evolution/gaps.py:207-211`：

```python
    for record in load_gap_resolutions(path):
        identity = str(record.get("identity") or "")
        if identity in state:
            state[identity]["resolved_cycle"] = int(record["cycle"])
```

`open_gaps` 的过滤条件 `evolution/gaps.py:253`：

```python
        if record.get("resolved_cycle") is None
```

resolution 是作为**对折叠后状态的末次覆盖**应用的，从不比较 `record["cycle"]` 与 `last_seen_cycle`。于是"cycle 1 观测 → cycle 2 解决 → cycle 4 再次观测"会让缺口永久消失。

### 复现（本次实跑）

```
after cycle1 open: 1
after resolve   : 0
after re-observe: 0 | summary: {'open': 0, 'resolved': 1}
```

这与该存储自己的文档冲突（`evolution/gaps.py:120-134` 明确说"Re-seeing a standing gap in a later round is itself the information the backlog exists to carry"，且 `occurrences >= 2` 是引擎用来判断"能力缺失才是瓶颈"的依据）。同类 reducer 在 `evolution/route_failures.py:285-287` 就是按追加顺序处理的（`state.pop(key, None)`），观测会重新建条目。

消费方：`evolution/engine.py:879` `open_gaps` → `_proposal_memory_context` → 起草提示的 `open_capability_gaps` / `recurring_capability_gaps`。`resolve_gap` 目前**没有生产调用方**（只有 `evolution/tests/test_gap_backlog.py:211`），因此当前影响限于使用该公开 API（`gaps.py:305` 导出）的运维/脚本：积压会静默少报。

---

## B5（中）`actionability["polarity"]` 键不存在，记忆极性项永不命中

唯一生产者 `evolution/worksheet.py:799-811`（另两处 `:748-755`、`:800-806` 同形）只返回三个键：

```python
    return {
        "classification": "actionable",
        "reason": ACTIONABILITY["actionable"],
        "routes": routes,
    }
```

而两个消费方读取 `polarity`：

- `evolution/engine.py:866-868`（`recall_experience` 的参数）
- `evolution/engine.py:1638-1639`（development 路径）

```python
        actionability = family.get("actionability") or {}
        polarity = str(actionability.get("polarity") or "")
```

`.get` 不会抛错，所以静默得到 `""`。后果：development 路径的 L1 失败通过 `record_episode`（`evolution/engine.py:1659-1676`）持久化 `polarity: ""`（`evolution/memory.py:140` 原样存储），而门禁路径存的是 claim 的真实极性（`evolution/engine.py:2276-2295`）——同一个 store 出现两套语义；`evolution/memory.py:345` 的 `if polarity and str(record.get("polarity") or "") == polarity: points += 2` 因此对 development 条目永不命中。正确推导就在同文件 `evolution/engine.py:1327-1328`，而 :870 行读的 `routes` 键是真实存在的，可见是键名写错。

### 复现（本次实跑）

```
actionability keys: ['classification', 'reason', 'routes'] | .get('polarity') -> None
```

---

## B6（中）缺陷注入审计用宽松参数调用 `structural_gate`

真实周期 `evolution/engine.py:3814-3824` 传入 9 个参数（含 `allowed_failure_cases`、`diagnosed_families`、`dataset_root`、`batch_candidates`、`patrol_rows`、`joint_groups`）；审计 `evolution/engine.py:2407-2409` 只传 3 个。

被跳过的检查（`evolution/gates.py:295-322, 344-351, 374-393, 458-470`）包括：provenance 的 case 必须落在本周期 patrol 内、family 必须是本周期诊断出的、DSL 演化谓词必须能在 family 全成员上执行、`upgrade.system` 钩子的可达性。

### 复现（本次实跑）

```
IV-DEFECT-001 audit-style passed= False | case id leak in directive/body/claim: BFCL/001.json
IV-DEFECT-002 audit-style passed= True  |
```

两个缺陷都硬编码 `failure_cases=["BFCL/001.json", "BFCL/002.json"]`（`evolution/gates.py:1296,1320`）；这两个 id 在语料里存在，但**存在不等于在当前 patrol 内**，不能据此断言真实门不会额外拒绝——审计未传 `allowed_failure_cases` 只能证明该检查未演练；若真实 patrol 恰好包括这些案例，它也不会因这一项被拒，不能把该参数遗漏说成唯一通过原因。

审计的**方向性结论**仍然成立（`IV-DEFECT-001` 在两条路径下都被同一个 `candidate_contains_case_id` 拒绝）；被削弱的是"完整规则集被演练"这一主张。修复取向：让 `_audit` 使用与 `_run_cycle` 相同的参数集（这需要把 patrol 提前到审计之前），或在审计报告中显式标注 `structural_parameters="reduced"`。

---

## B7（撤销）缓存耗时与三种成本口径符合当前合同

`docs/technical-solution.md:134` 明确定义：`invocation_cost` 统计本次执行，`case_attempt_cost` 统计各案例最近一次完成测量，`run_lifetime_cost` 统计全部持久化尝试。缓存行本次耗时为 0，最近完成测量保留历史 execution 耗时，两者应当不同；重测后 lifetime 与最近测量也应当不同。

`detector/tests/test_run_cost.py` 已覆盖缓存、resume、失败尝试和重测的区别。原建议改成只读 `row["elapsed_seconds"]` 会抹掉最近测量耗时，因此不应采纳。若需要优化，只补成本字段的展示说明。

---

## B8（中）research baseline 预算耗尽时抛异常，绕过 `partial` 报告

`evolution/research.py:291-296` 在设置 `self.stopped = "baseline_budget_exhausted"` 后 `raise RuntimeError(...)`；而 `resolve_baseline()` 在 `:999` 被调用，**在** `:1011` 的 `try:` 之外，`:1047-1048` 的处理器才会把 `status` 置为 `"partial"`。触发情形：清单引用的 baseline 运行不再覆盖全部 case（运行目录被删、结果不再是 `authoritative`、或清单新增了 case）且预算已耗尽（归档清单正是 4 个 case 配 4 的上限，`docs/experiment-log.md:17754`）。后果：不写 `report.json`/`report.md`/`selection.json`，`:292` 的赋值成为死代码；调用方 `scripts/run_research_round.py:315` 没有 try，`main()` 直接带 traceback 退出。

---

## B9（中，可达性中等）`ActiveRegistry.retire` 的 `ValueError` 无捕获方

`evolution/registry.py:231-233` 在存在孤儿依赖时抛 `ValueError`；`evolution/engine.py:4775-4786` 的 `_apply_retirement` 按每个条目自身的 marginal 历史逐条 retire，从未调用公开的前置检查 `check_retire_dependencies`（`evolution/registry.py:224`，全仓库零调用）；`run_governance`（`:4724`）与 `cli.cmd_governance`（`evolution/cli.py:136-139`）都没有 try。一个"evidence_check + decision_guard"联合单元若两个成员在 2 个周期里的 leave-one-out marginal 走向分化，就会中断整个治理阶段（归因已算完，治理报告/账本事件/回滚检查全部丢失）。注册表字节不会被破坏（`_mutation` 在 `_publish` 之前回滚）。建议核验：用 `evolution/tests/test_ledger_registry_metrics.py:240-283` 的那对条目驱动 `_apply_retirement`，并给它两次 ≤0 的 marginal。

---

## B10（中）`required_action` 名称抽取会把冠词 `the` 当成工具名，制造伪违规

### 结论

`detector/predicates.py:220-263` 的机械义务抽取在"标记词 + 工具语义对象"结构里取 `_NAME_SHAPE_RE.match(phrase)` 作为动作名。当标记词是 `must use` 而短语是 `the tool named X` 时，`phrase` 恰好是 `"the"`，形状匹配成功，于是动作名被登记为 `"the"`。

### 证据

`detector/predicates.py:243-256`：

```python
            tail = question[pos + len(marker): pos + len(marker) + 80]
            obj = _TOOL_OBJECT_RE.search(tail)
            if obj is None or obj.start() > _TOOL_OBJECT_WINDOW:
                continue
            phrase = _LEAD_IN_RE.sub("", tail[:obj.start()].strip(" \t:-"))
            shape = _NAME_SHAPE_RE.match(phrase)
            if shape is None:
                continue
            name = " ".join(shape.group(0).split()).strip(" .-").lower()
            if not name or name in {"tool", "function", "api", "command", "endpoint"}:
                continue
```

排除集合（`:252`）只有 `{"tool","function","api","command","endpoint"}`，**不含冠词**。

### 复现（本次实跑，含内部追踪）

```
marker='must use' pos=4 action_is_required=True
   tail=' the tool named lookup_customer.'
   obj='tool'
   phrase='the'
   name_shape='the'

run_predicate on a packet:
{'predicate': 'required_action_present',
 'verdict': 'violated',
 'detail': "required actions missing from tool calls: ['the']",
 'runner': 'deterministic'}
```

即一个**完全合格**的问题（明确要求调用名为 `lookup_customer` 的工具）被判定为"要求的动作未出现在工具调用中"，因为"要求的动作"是单词 `the`。

### 影响面（对本次运行不可达，已实测）

该谓词是 `convict_mechanical_violation` 的机械证据来源，并被写入风险画像的 `task_signals`（`detector/step_uncertainty.py:77-82`）与任务证书（`detector/causal_task.py`）。但本次语料完全不触发这条路径——脚本实测全部 693 个案例：

```
cases=693  cases with any detected required action=0
cases whose detected action is junk (article/pronoun/short)=0
'must use': 0 case(s)   'use the tool': 0 case(s)   'tool named': 0 case(s)
'required_action': 0 case(s)   'must call': 0 case(s)   'should use': 0 case(s)
```

因此这是**机制层面的真实缺陷、对本次运行零影响**：一旦语料换成含 "You must use the tool named X" 这类表述的任务，它会把无辜案例推向 `verified_error`。

### 附带发现（同一次探测）

结构化拼写 `required_action: "X"` 的识别要求它位于**子句开头**（`_action_is_required` 的 `_EXECUTION_PREFIX_RE.fullmatch`，`detector/predicates.py:386-401`）：

```
'required_action: "lookup_customer"'                       -> ['lookup_customer']
'Task: required_action: "lookup_customer"'                 -> []      <-- 漏检
'Please carry out the required_action: "lookup_customer"'  -> []      <-- 漏检
'[turn 1] required_action: "lookup_customer"'              -> []      <-- 多轮任务漏检
```

考虑到多轮问题的扁平化格式是 `[turn N] 文本`（`detector/packet.py:106-108`、`detector/predicates.py:172-190`），第二轮及以后的结构化拼写**永远无法**被识别。这两种行为（伪违规 + 漏检）方向相反，建议一并确认设计意图——`cac6d31`/`6c241be` 两个提交正是在这条规则上做过修正，改动前需先确认它当前想表达什么。

### 建议核验方式

1. 负例测试：`"You must use the tool named lookup_customer."` 的 `_detect_required_actions` 应为 `{"lookup_customer"}` 或空集，绝不可是 `{"the"}`。
2. 正例测试：多轮问题中第二轮的 `required_action: "X"` 应被识别（或明确记录为设计上的不支持）。

---

## A5（高）任何 `prediction is None` 的行都会把整条臂判为"不可测"，使 resolution 目标永久不可评分

### 结论

`arm_has_infrastructure_failure` 把"该案例没有决断"（`prediction is None`）一律当成"这条臂没能被诚实测量"。但同一个判定层在别处明确把这种行叫作**健康的**测量。两套定义冲突，后果是：声明要支持的 `resolution` 目标在结构上不可能被评分。

### 证据

`evolution/gates.py:144-150`：

```python
    if not rows:
        return "arm produced no rows"
    for row in rows:
        if row.get("gold") is not True and row.get("gold") is not False:
            return f"missing boolean gold for case {row.get('case')}"
        if row.get("prediction") is None:
            return f"unresolved row for case {row.get('case')}"
```

同一层的其他定义（都把它当作健康读数）：

- `detector/measurement.py:79` —— 该行的 `measurement_status` 是 `"semantic_unresolved"`，**不是** `"infrastructure_failure"`。
- `evolution/metrics.py:1089-1096` `_row_health` —— authority 为 `authoritative` 且 `runtime_valid` 为真时返回 `True`。
- `evolution/metrics.py:54-73` `_bool_prediction` 的注释 —— "``None`` is a legitimate value here -- it is how an unresolved row reads, and the paired deltas are built around it"。
- `evolution/metrics.py` 的 `EvaluationState.SEMANTIC_UNRESOLVED_HEALTHY` 就是这个状态的名字。

### 复现（本次实跑）

构造一行权威但未决的合法信封（`state=unresolved`、`decision_authority=authoritative`、`runtime_valid=True`）：

```
measurement_status          : semantic_unresolved
arm_has_infrastructure_fail : 'unresolved row for case A/1.json'
paired_delta flips          : {'U->T': 1} | marginal: 1
meets_resolution            : (True, [])
```

即：`paired_delta` 能正确算出 `U->T`，`meets_resolution` 也给出通过，但**这条臂在到达门禁之前就已经被判为不可测**。

### 为什么 resolution 目标因此失效

resolution 的定义就是"控制臂未决 → 候选臂决断"（`U->T` / `U->F`）。要评分它，控制臂**必然**含 `prediction is None` 的行。于是 `arm_has_infrastructure_failure` 必然先命中：

- `evolution/promotion.py:1574-1576` → `:1597-1613` 在任何门禁运行之前就把状态写成 `promotion_unresolved`；
- `evolution/engine.py:2720` 的 `unmeasured` → `:3882-3891` 的 `and not unit.get("unmeasured_cases")` 让该单元永远无法 `qualified`。

这说明正常生产入口的 resolution 支持不完整，不能由此断言所有辅助函数本身不可调用。涉及的路径包括 `evolution/gates.py:835`（behavior_gate 的 resolution 分支）、`:954`（`resolution_behavior_gate`）、`:1238`（`meets_resolution`）、`evolution/promotion_alignment.py:45` 的 `"resolution_failed"`、`evolution/promotion.py:1725-1733`/`:1758-1759`、以及配置键 `resolution_min_resolved_cases`（`evolution/gates.py:1251`）存在前置门禁或目标分派阻断；直接调用的单元测试仍可到达这些辅助函数。

### 建议核验方式

二者只能留一个：要么 `arm_has_infrastructure_failure` 把"权威但未决"视为可测（把不可测限定为 provider 失败/非权威/运行时无效/阶段失败），要么 resolution 目标族整体下线并同步修改文档与配置项。补一个"控制臂含权威未决行 + 候选臂修复该案例"的测试，断言 resolution 门禁真的被调用过。

---

## B12（中）晋升的"本 claim 是否可测"判定与框架自己的 opportunity 定义不一致

`evolution/promotion.py:1688-1697`：

```python
        if target_kind == "detection":
            scored_direction = any(
                opportunity.get(
                    str((artifact.get("claim") or {}).get("target_polarity") or "")
                )
                for artifact in artifacts
            )
            unmeasurable_claim = not scored_direction
        else:
            unmeasurable_claim = localization_op["improvable"] <= 0
```

`opportunity = gain_opportunity(control_benefit)`（`:1680`）**只返回 `recall` / `precision` 两个键**（`evolution/metrics.py:801-810`）。因此：

- 合法的 `target_polarity="boundary"` 检测 claim（`detector/interventions.py` 允许该取值）会得到 `opportunity.get("boundary") is None` → `unmeasurable_claim=True`，即使增益集里确实有可修复案例。
- `target_kind="resolution"` 走 `else` 分支，用 **localization** 的机会数判断可测性，同样错位。

### 复现（本次实跑）

```
gain_opportunity keys        : ['precision', 'recall']
target_opportunity boundary  : 1        <-- 框架自己的定义说可测
localization_opportunity     : {'improvable': 0, 'preserved': 0, 'unannotated_positive': 1}
resolution 真实未决机会反例见 D5：U->T=1，但 target_opportunity=0
```

周期路径对 boundary 使用统一 `target_opportunity(...)`，但不能据此认定 resolution 正确：该函数没有按 `target_kind="resolution"` 读取 unresolved 机会；详见 D5。此条 主要 指传统晋升路径，不代表机制对齐路径的所有机会计算。

### 影响

`unmeasurable_claim=True` → `evolution/promotion.py:1703-1713` 给**每个**成员写 `{"passed": False, "evaluable": False, "status": "not_evaluated"}` → `gates["behavior"]` 以 `evaluable=False` 失败 → `:1755` 永不算 `gates["m"]` → `:1790-1805` 记为 `state="promotion_unmeasurable"`。即一个本可测量的 claim 被记为"未测量"。

---

## B13（中）未启用审计时 `all(())` 恒真，`defect_injection_audit` 空过为 `pass`

`evolution/meta_eval.py:76-84`：

```python
    audits = [
        report.get("audit") or {}
        for report in cycle_reports
        if report.get("audit") is not None
    ]
    audit_passed = all(
        bool(audit.get("passed")) for audit in audits
        if audit.get("enabled", True)
    )
```

`cycles` 统计的是全部 `audits`，而 `passed` 是在**过滤后的子集**上取的 `all`。子集为空时 `all(())` 为 `True`。触发条件：

- 配置 `"audit": {"enabled": false}` —— 这是受支持的设置（`evolution/engine.py:2332-2333` 每轮返回 `{"enabled": False, "passed": True, "frozen": False, "reports": []}`；`:3362` 的注释也说明"即使审计关闭也要跑覆盖检查"）；
- 或报告里根本没有 `audit` 键（被 `:76-80` 的推导式排除），例如 `evolution/cli.py:157-174` 的 `cmd_meta` 从磁盘读取任意 `work_root/cycles/cycle*.json`。

而 `evolution/meta_eval.py:136-141` 会据此写出：

```python
        "defect_injection_audit": {
            "cycles": len(audits),
            "passed": audit_passed,
            ...
            "verdict": "pass" if audit_passed else "freeze",
        },
```

这个布尔值是**最终结论的 AND 项**，不是装饰。`evolution/engine.py:5347-5354`：

```python
        nonempty = bool(engine.registry.entries)
        overall = (
            "pass"
            if nonempty and final_report["verdict"]["overall"] == "pass"
            and bool(meta_report["defect_injection_audit"]["passed"])
            and bool(ledger_status["valid"])
            else "fail"
        )
```

而 `evolution/cli.py:154` 按 `report["overall"]` 决定退出码（pass → 0）。于是"根本没审计过"的运行会被记为并通过为 `pass`。（`nonempty` 守卫要求注册表非空，所以完全空跑的运行不会误报 pass。）

建议核验：`audit.enabled=false` 跑一轮，断言 `defect_injection_audit.verdict != "pass"`（或改为 `audits and all(...)` / 显式区分 `not_applicable`）。

---

## B14（中）晋升门禁拿不到稳定性标注

`evolution/promotion.py:1646-1650`：

```python
        detection = paired_delta(list(control_benefit), list(candidate_benefit))
        localization = paired_localization_delta(
            list(control_benefit), list(candidate_benefit),
            gold_steps=self.protocol.get("gold_steps") or {},
        )
```

未传 `unstable_cases` → `stability_applicable` 为 `False`（`evolution/metrics.py:550-558`）→ `meets_m` 改为对 `delta["marginal_cases"]` 而非 `marginal_cases_on_stable` 计分（`evolution/gates.py:1213-1215`）。周期路径是传的（`evolution/engine.py:2702-2705`）。

`evolution/metrics.py:407-409` 给出的理由（"promotion's cached arms"）在 `evolution/promotion.py:1546-1548` 的 `fresh = self.require_fresh or requires_fresh_evaluation(...)` 为真时不成立：此时两条臂都是现场运行，控制臂是一次新的 roll，于是候选可能仅凭"落在仪器自身抖动案例上"的增益被准入。仓库自己的实验日志也把它列为未修项（`docs/experiment-log.md:12048`："未修晋升路径的噪声底（`promotion.py` 未接 noise floor）"，另见 `:26703`）。

---

## C 组：低危项（逐条已读源码确认）

- **C1** `detector/judge.py:187-189` 的 docstring 承诺"tie … the stage degrades"，但 `:282-295` 之后 `:314-316` 无条件返回 `"state": "ok"`。偶数票（2 票，或 3 票中 1 票弃权）产生的平票会经 `detector/adjudication.py:370-403` 落成 `verified_clean` + `authoritative`。`detector/api.py:55-72` 的 `_judge_negative_needs_recall` 会因 `vote.tie` 强制复扫，是部分缓解，但该事实不会进入行的 authority。**本次配置 `judge_route.votes=3, vote_topup=2`，属活路径。**
- **C2** `detector/adjudication.py:507` + `:523`：`defense_reason` 取自 `reasons[-1]`，而 recall 原因行在所有听证的 defense 行之后追加（`:463-476`），因此 recall 降级时该字段填的是 recall 的 `error_type`；同分支 `:500` 用的是 `defense_parsed.get("reason")`，两处对同一字段的含义不一致。无 Python 消费方，但会被持久化进每个 case 信封。
- **C3** `detector/adjudication.py:236-237`：`elif overridden:` → `LOCALIZATION_AGREEMENT_TRACER_EARLIER`，tracer 给出**更晚**步号时标签为假，与函数自身 docstring（`:198-204`）矛盾。只影响报告文字。
- **C4** 三处死状态：`detector/adjudication.py:543` 的 `degraded = True` 在该分支从不被读（返回处硬编码 `AUTHORITY_DEGRADED_JUDGE_ONLY`）；`:619-628` 的 `working_positive["evidence_refs"]` 只写不读；`:477-479` 的 `judge_positive or positive.get("source") == "recall_upgrade"` 恒真。
- **C5** `detector/defense.py:392` docstring "Never raises" 与 `:175`/`:202` 的 `raise_if_terminal(exc)` 矛盾（终态 `ProviderError` 会抛，被 `detector/api.py:174` 接成 unresolved 信封）。对比 `detector/judge.py:184-185` 的表述是正确的。
- **C6** `detector/step_uncertainty.py:118-125`：语义方向正确（选全 → `selected`，被截断/有未知 → `unknown`），但该字段**承重**——`detector/causal_graph.py:243` 用它决定 `closure_state`，进而 `detector/causal_audit.py:243` 与 `detector/causal_task.py:79` 会让因果上下文与任务证书整条降级。缺 docstring，且只有 `detector/tests/test_causal_diagnostics.py:110` 固定它。
- **C7** `detector/packet.py:429-432`：`events[anchor_pos:]` 的 1-based 下标恰好跳过 anchor。**经核对无 off-by-one，非缺陷**，仅记录语义未文档化。
- **C8** `detector/packet.py:34-37` 把 `finish` 列为答案工具提示词（`_answer_call` 因此认它），但 `:489-491` 的 `answer_tool_is_delivery` 按子串排除含 `finish` 的名字，`delivered_result()`（`:522-530`）会把它降级为 `last_status_tool`。该锚点经 `detector/adjudication.py:66-75` 影响 `convict_mechanical_violation` 的 `responsible_event_id`。
- **C9（撤销）** 未知请求次数令总量为 `null`，符合“未知计数不补零”的当前合同。`usage_missing_attempts` 的已知计数与 `usage_unknown_cases` 一同报告，不能用二者表达形式不同证明缺陷。
- **C10** `evolution/ledger.py:139-147`（`read()`）与 `:103`（`last_hash()`）调用裸 `json.loads`；撕裂行会抛 `JSONDecodeError` 而不是 `verify()` 承诺的 `{"valid": False, ...}`，使引擎的 `"ledger integrity broken"` 拒绝变成解码错误（仍然 fail-closed，但诊断错误；`cycle_interrupted` 的恢复追加也会从 `last_hash()` 再抛一次）。
- **C11（撤销）** `validate_path_id` 真源在 `detector/runtime.py:35`，runner 是导入消费者。不存在“单点号/三位数字”限制，本轮 `validate_path_id("Source/a.b.c.json")` 成功。`evolution/contracts.py` 的特定语料泄漏正则覆盖范围可单独记录，但不能说路径校验拒绝新语料形状。
- **C12** `detector/interventions.py:2028-2035`：同 hook 上同 family、同 predicate 名的两条 `evidence_check`，后者的 verdict 会覆盖全局 fact；family 命名空间无法区分同 family 内的两条证据行，且不记 `policy_conflict`（对比 guard action 冲突会记）。
- **C13（撤销为缺陷）** 全案例准确率把未决计为没有答对，是有测试固定的指标定义。可用 coverage/decisive metrics 区分全未决与全错；不建议把该字段改成 null 而破坏既有分母。
- **C14** `evolution/splits.py:206-213` 的 `except Exception: pass` 让损坏的 marker 在报错信息里显示成 `previous_read=None`（仍会 raise，只是丢失原因）；`commit=False` 分支明确抛错，防止先读标签后不消费一次性资格。本轮已实际复现拒绝，因此它是防御分支，不能认定为死代码。
- **C15** `evolution/capability_planning.py:370-377` 的 `if classification == "capability_gap" and not evidence:` 永不触发（到达该分类的每条路径都已 append 过 evidence），即"点名缺失能力"的那行文字不可达。`evolution/backends.py:733-739` 的 `fired_always: bool = True` 被接受但全无引用（无调用方传入，无实际影响）。
- **C16** `evolution/backends.py:851-853`：`prediction = self.base_predictions.get(case_id); if prediction is None: prediction = gold` 让显式 `None` 与"缺条目"等价，而 docstring（`:725-726`）说的是"缺条目"才用 gold；于是 `authority_failure_reasons` 的 `code: "injected_unresolved"`（`:915-918`）只在 gold 本身为 None 时触发。`overrides` 支持 `None`（`:862`），因此这个约定很容易被误用。建议改为 `if case_id not in self.base_predictions:`。
- **C17** `detector/adjudication.py:224-227` 的列表推导包含两个语义等价的条件（`adopted` 就由 `step == adopted_step` 派生），不是同一字面条件复制两遍，无害但会误导读者。
- **C18** `evolution/promotion.py:2076-2098`：一次性协议的**非竞争分支**比对完整 identity（`:2082` `if recorded != identity: raise`），`_claim_aligned_protocol` 的对应分支也比对完整 identity（`:2108-2110`），但 `_claim_protocol` 的**竞争分支**（`except FileExistsError:`，`:2093-2098`）只校验 `protocol_hash`。因此两个并发的 `PromotionRun` 争抢同一协议路径时，若输掉 `open("x")` 竞争的那一方 `candidate_content_hash`/`output_dir` 不同，它既不会报错、也不会写下自己的 claim，而是继续测量并可能走到 `_publish`。调用方 `evolution/promotion.py:1515-1523` 只把抛出的 `ValueError/KeyError/OSError` 转成 `promotion_blocked_preflight`，此处不抛即无拦截。建议把竞争分支改成与非竞争分支一致的全量比对。
- **C19** `evolution/metrics.py:520` 的 `"new_errors": new_fn + new_fp` 与 `evolution/metrics.py:391-392` 的 docstring（"``new_errors`` is correct-or-decisive -> wrong-or-unresolved transitions"）不完全一致：`new_fn`（`:432-441`）只覆盖 gold=true，`new_fp = fp_added`（`:427-431`、`:442`）要求 `active_prediction is True`，因此"gold=false 且原本正确（`False`）→ 变成未决（`None`）"这一转变贡献 0。本身不可被利用，因为 `meets_m`/`meets_resolution` 各自独立要求 `unresolved_added == 0`（`evolution/gates.py:1231-1232`、`:1261-1262`），但 docstring 与实现应当对齐。

---

## 附一：已核对但**不构成**缺陷的点（避免重复排查）

1. `detector/runner.py:443-458` 缓存复用前的身份校验链完整：`case_id`、`execution_identity`、`validate_case_commit`（`commit_digest`/`result_digest`/`input_digest` 三重摘要）、`input_digest` 四道检查，任一不符即抛错而非静默复用。
2. `detector/runner.py:98-120` 拒绝复用非本 schema 的 `result.json`；`retry_in_run`/`needs_remeasurement` 与 `measurement_status` 的分类自洽。
3. `detector/runtime.py:337-375` `bounded_map` 不预提交整个语料，且 `finally: pool.shutdown(wait=True, cancel_futures=True)` 保证异常时不泄漏线程。
4. `detector/adjudication.py:364-403`：judge 负且 recall 臂不可用时返回 `verified_clean` + `degraded_judge_only`，不把 outage 洗成 `authoritative` clean（两条分支的 authority 取值已实跑确认）。
5. `detector/defense.py:117-140` `_vote_verdict` 的 tie 律：只有严格多数 `substantiated` 才给 `substantiated`，否则升级/`inconclusive`，永不把平局变成 `refuted`。
6. `detector/recaller.py:536-554` upgrade 的 quorum：`cast = len(samples)` 且 `len(samples) >= required_quorum(votes)`，每候选的严格多数是在足额样本上计算的。
7. `detector/provider.py:198-206` `required_quorum` 用**配置票数**做多数下限，正确防止弃权稀释法定人数。
8. `detector/evidence.py:369-398` 的并查集 `find` 是路径对折（`parent[node] = parent[parent[node]]`），正确。
9. `detector/evidence.py:401-446` `conflict_report` 只在同一 `(entity, attribute)` 出现两个不同 `value` 时报冲突，并用 `resolved_by` 是否在被引集合内做二次校验，正确。
10. `detector/step_uncertainty.py:23` 的 `event_limit = 16 * 8 = 128` 会在超长轨迹上截断，但**本次语料实测最长仅 43 个事件**（`Magentic_One/061.json`），>64 的 case 为 0 个，无风险。
11. `evolution/engine.py:2495-2525` 审计缓存：只有**已决**判定才落盘，inconclusive 不缓存，避免一次 provider 故障变成 work_root 的永久属性。
12. `evolution/gates.py:626-627` 用 `unstable_cases is not None` 区分"没重掷"与"重掷了但没动"，与 `metrics.noise_floor` 三种 status 一致。
13. `evolution/splits.py:176-180` `patrol_sample` 的 RNG 由 `seed + cycle*7919` 派生，同一 cycle 重跑得到同一 patrol 样本。
14. `evolution/splits.py:189-290` `FinalHoldout` 用 `O_CREAT|O_EXCL` 原子创建 marker，跨进程拒绝二次读取。
15. `detector/measurement.py:142-165` `measurement_sources` 对 `execution.v1`/`v2` 的 lineage 校验传递且严格。
16. `evolution/backends.py:1020-1054` 缓存项校验含 `identity` 全等、`result_digest` 自洽、`decision_authority == "authoritative"`、`case_id` 一致四项，且只有 authoritative 结果才写盘。
17. `evolution/checkpoint.py` 的可恢复性承诺成立：`registry_before` 写入并按同一语义读回、累计 `used_seconds` 在两处 deadline 计算中一致、completion 记录含 event/contract/report 三重摘要校验、per-arm 缓存键为 `digest({"registry","fresh"})` 且信封重新校验。
18. `evolution/contracts.py` 的身份辅助函数与 `evolution/config.py` 的深合并/路径解析均正确（顺序无关摘要、加载时解析全部路径）。
19. `evolution/cli.py` 的每个开关都被消费（`--workers` → `config["backend"]["workers"]`，在 `engine.py:4393/4558/4644/4907` 读取；`--challenge` 在 v1 路径被 `promotion.py:510` 明确拒绝而非忽略），退出码读取的键在引擎报告里都存在。
20. `evolution/archive.py`、`evolution/harness.py`、`evolution/memory.py`、`evolution/smoke.py`、`evolution/capability_surface.py`：未发现可报告缺陷（摘要顺序稳定依赖 `detector/identity.py` 的 `sort_keys=True`；`record_episode_correction` 的嵌套 `exclusive_writer` 按 `detector/runtime.py:396-431` 是可重入的，非死锁）。

---

## 附二：原轮建议（已由附七替代，不直接执行）

1. **A2**：`detector/adjudication.py:119-125` 与 `detector/defense.py:345-349` 的契约不一致，一处条件即可修；它污染的是本次运行明确要读的定位目标。修后需重跑 `detector/tests/test_voting_hearings.py` 与 `evolution/tests/test_localization_conflict_and_split.py`，并先把 fixture 改成真实 emitted 形状（否则测试仍从错误分支通过）。
2. **A1**：让 `_audit` 用完整行参与判定，或明确定义并记录"整批扫描"策略；补一个**引擎级**测试。这一条决定"一个无关案例不可测是否会终止整轮实验"。
3. **A5**：与 A1 同源（"未决 ≠ 不可测"的判定冲突），但方向相反——A1 是"太宽"把无关案例算进来，A5 是"太严"把健康案例算出去。两条应一起定契约，否则修好一条会暴露另一条。
4. **A4**：把 `AddedBudgetExceeded` 纳入 `Detector.detect` 的兜底（注意别吃掉 `api.py:677` 的内部捕获）或 `run_batch` 的逐案例兜底，并把 code 加入 `measurement.EXHAUSTED_ERRORS`。
5. **A3**：在 `_new_version_id` 内强制执行 `max_versions`，并让超限走 `partial`。
6. **B1/B2/B4/B5/B10**：分别修"静默丢弃 + 误记证伪"、"短值子串放行"、"解决后重现"、"键名写错"、"冠词被当成工具名"。
7. **B3/B6/B7/B8/B9/B12/B13/B14**：审计参数对齐（或标注）、baseline 预算走 partial（B3/B7 已撤销）、retire 依赖前置检查、晋升可测性判定改用 `target_opportunity`、`audit_passed` 区分 `not_applicable`、晋升接上稳定性标注。
8. **C 组**：按可读性与契约一致性批量处理；其中 **C8/C11/C16/C18** 建议写成显式契约（答案投递判定收敛到单一函数；case id 形状写进两侧 docstring；`base_predictions` 改用 `in` 判断；协议争夺分支与非竞争分支同样做全量比对）。

---

## 附三：原轮方法与覆盖记录（本轮验证见附六）

- 原轮方法记录：用符号级检索核对跨模块消费者，并记录最小探针结果。原文“全部源文件逐文件阅读/每条可执行结论均复现”的主张与后文未深审清单及多项“未复现”条目矛盾，故撤销全量覆盖声明；本轮实际覆盖和独立复现见附六。
- 复现方式：本次排查共运行 16 个最小复现/探针（预算异常跨批传播、预算门越界点、冻结阶段校验、缺陷审计参数、缺陷候选 STRUCTURAL、gap 解决后重现、actionability 键、citation 子串放行、`_hearing_verdict` 改判、`arm_has_infrastructure_failure` vs `paired_delta`、opportunity 键集合、`required_action` 抽取、语料轨迹长度统计、语料短语普查等），全部在临时脚本中执行，未改动仓库源码；临时脚本与全部中间产物已清理，`git status` 仅剩本文件。
- 语料实测（用于判断"是否对本次运行可达"）：693 个案例最长轨迹 43 个事件（`Magentic_One/061.json`），事件数 >64 的为 0；case id 全部匹配 `Source/NNN.json`（泄漏正则 0 漏配）；`must use` / `use the tool` / `tool named` / `required_action` / `must call` / `should use` 全部 0 命中。
- 排除方式：对每个可疑点先看 docstring/注释是否已说明为有意行为；已被文档或测试固定为设计意图的（如 `withhold_conviction` → clean + authoritative、不可判定 → fail-closed、`unstable_cases=None` 与 `[]` 的区别、`has_subsequent_activity` 的切片语义）不作为缺陷上报。
- 已逐行读过并明确**无**可报告缺陷的文件（除上文已列条目外）：`evolution/hearing.py`、`evolution/checkpoint.py`、`evolution/config.py`、`evolution/contracts.py`、`evolution/calibration.py`、`evolution/promotion_alignment.py`、`evolution/archive.py`、`evolution/harness.py`、`evolution/memory.py`、`evolution/smoke.py`、`evolution/capability_surface.py`、`evolution/cli.py`。
- **原轮尚未逐行深挖**（续审的实际新增覆盖见附六，仍不声称全库逐行完成）：`detector/predicate_dsl.py`、`detector/conviction_calibration.py`、`detector/causal_contracts.py`、`detector/causal_task.py`、`detector/retrieval.py`、`detector/identity.py`、`detector/dataset.py`、`detector/cli.py`、`evolution/sandbox.py`、`evolution/mechanisms.py`、`evolution/development.py`、`evolution/metamorphic.py`、`evolution/proposer.py`（部分读过）、`evolution/candidate_versions.py`、`evolution/route_failures.py`、`evolution/gates.py`（部分读过）、`evolution/engine.py`（约 6 成读过）、`evolution/promotion.py`（关键分支读过，未逐行）。这些文件已由并行审计部分覆盖，但审计进程在收尾前失败，其结论**未经本轮独立复核，故未收录**。
- 未做的验证：未运行任何真实 provider 调用、未跑完整测试套件（只跑了 `detector/tests/test_voting_hearings.py` 与 `evolution/tests/test_localization_conflict_and_split.py`，35 passed）、未做基准测量与调参。


---

## 附四：2026-10-12 独立核验结果（本轮有效结论）

### 4.1 总体判断

**本轮离线验收未全通过（2270 passed、2 failed），真实启动预检也为 NOT READY。** Mock smoke/CLI 可运行，不能据此认定全部运行合同正确。 当前源码基线仍为 `main@6cec2d2`，本轮没有修改源码、配置、测试、原始数据、冻结协议、校准记录或历史 ledger。真实运行预检已实际返回退出码 1、`NOT READY`，原因是 D0。除此之外，A2 的真实生产形状、A1 的引擎级失败路径，以及新增 D1–D5 说明，预算、目标分派和持久身份之间仍有未被现有测试覆盖的组合问题。

原报告不能整体视为已验证。它包含源码事实正确但因果结论扩大、把设计定义误报为缺陷、以及源码与文字推理不一致的条目。原轮临时探针已经删除，故本轮只将实际重跑的行为标为“本轮复现”；没有根据原文的“是”直接继承复现资格。

### 4.2 原条目逐项核验

“源码确认”只表示读到实现和消费者，不表示已执行整条生产链，也不表示目标配置必然触发。低危的说明/冗余问题不与运行阻断同级。

| ID | 本轮结论 | 证据与必要修正 |
|---|---|---|
| A1 | 根因成立，撤销“全局死代码”表述；引擎级复现 | 健康 Mock cycle：helper=1、completed；缺陷臂一个 sentinel 注入未决：helper=0、frozen。加强反例：8 sentinel 排除故障行后健康 TP=3/TN=4，均≥2；生产仍 helper=0/frozen，手动 helper 重判返回空 infrastructure reason。摘要丢失 result 是另一信息损失。 |
| A2 | 成立；生产者→裁决者复现 | 真 `run_defense` 接收三张合法分裂票，输出 upgraded/null；真裁决将其视为 invalid，并落为 `degraded_judge_only`。不只是定位变化，权威覆盖也变化。无需将少数票升级为完全权威票。 |
| A3 | 成立；本轮完整 Fake research 复现 | max_versions=1，却生成 v001/v002，used=2，仍报告 screening_complete。历史日志两条超限读数也存在。独立 research 入口，不是 full-self-evolution 的预算。 |
| A4 | **降级为兜底风险；自然生产逃逸未证实** | API 顶层缺少该异常保护，但实际 added_request_scope 均有局部 handler，Tracer context reserve 也捕获；本轮三项正常路径预算耗尽测试通过。强制 FakeDetector 抛错不是开启 causal 模式后实际逃逸的证明。目标配置 off。 |
| A5 | 成立，但不是 resolution 不工作唯一根因 | 权威、runtime-valid 的 prediction=None 行仍被当成 infrastructure。周期 qualification 明确拒绝 unmeasured_cases；resolution 另有 D5 的机会和目标分派断层。不能声称所有辅助函数不可被直接调用。 |
| B1 | 源码链确认 | max_admits 过滤不登记预算淘汰理由；经验兜底 outcome=rejected、unevaluable=False，可写 falsified。尚未本轮驱动完整周期的三候选记忆落盘。应记预算未选中，而非证伪。 |
| B2 | 成立；本轮复现 | 同一冲突，空 note 被拒，`I reviewed 4 unrelated things` 放行。原“加词边界”方案不能排除此反例，需把冲突解释与已验证解决分开。 |
| B3 | **撤销** | 当前条件正确读取 state；本轮负例抛 authority mismatch。原文字引用的 status.ok 不存在于该实现。 |
| B4 | 成立；本轮复现，生产影响有限 | observe cycle1→resolve cycle2→observe cycle4 后 open=0。当前 resolve_gap 无生产调用方，不扩大为每个真实周期都会发生。 |
| B5 | 源码确认 | worksheet 生产 actionability 不含 polarity；engine 两处读该键得到空值。应统一从目标/claim 规则派生。 |
| B6 | 缩减审计覆盖的风险成立 | 审计与周期 structural 参数不同；案例存在不证明属于本轮 patrol。人工缺陷需要独立审计语境，不应简单把生产 family 参数硬套到合成 AUDIT family。 |
| B7 | **撤销** | 当前合同明确定义 invocation、最近完成测量、lifetime 三种口径；缓存耗时处理与测试一致。不修改统计使三者强行相等。 |
| B8 | 成立；本轮 Fake research 复现 | baseline cap=0、无可复用 baseline 时 RuntimeError，report/selection 均未写。baseline 初始化在 partial handler 外。 |
| B9 | 成立；本轮实际 registry→retirement 复现 | evidence 连续两周期 marginal=0、guard=1，_apply_retirement 抛依赖 ValueError；两条 registry 均保留。治理报告没有正常完成。独立 governance 入口。 |
| B10 | 成立；词法反例和 693 例只读普查复现 | must use/call the tool named lookup_customer 均抽出 the；693 个当前案例 detected_actions=0，相关六类短语均为 0。不要直接断言一定定罪，还取决于校准和 guard。 |
| B12 | 传统晋升分派缺陷成立 | boundary 查不存在的 opportunity 键；resolution 用 localization opportunity。target_opportunity 对 resolution 本身也不完整，原“统一函数证明正确”的论证无效。 |
| B13 | 报告语义问题成立，条件风险 | enabled audit 的集合为空时 all=True；应显式区分 not_run/disabled/pass。默认 audit=true，不能直接判目标运行将未审计冒充通过。是否允许 disabled overall pass 应由最终资格合同确定。 |
| B14 | 传统晋升缺少稳定性证据的风险成立 | paired_delta 未传 unstable_cases。无稳定性标注是“未测”，不等于确定发生抖动，也不应自动扣除增益；需按 fresh/cached 和协议要求补测或阻断。 |
| C1 | 文档与平票状态定义不一致 | Judge 平票可为 ok，API vote.tie 强制 Recall。没有验证 Recall 后最终一定错误/一定权威，因此保留为合同澄清与边界测试项。 |
| C2 | 源码确认，报告字段问题 | last_reason 可来自 Recall，defense_reason 不能稳定代表 Defense 原因。 |
| C3 | 源码确认，标签问题 | 非首 tracer 分歧统一标为 tracer_earlier，不能覆盖更晚步号。 |
| C4 | 低危冗余/只写状态 | 不作为启动阻断；清理前保留权限与来源规则。 |
| C5 | docstring 不准确 | terminal ProviderError 可以抛出；修说明而非吞掉终态。 |
| C6 | 文档覆盖项 | state→closure 的消费者存在，未发现本条本身运行错误。 |
| C7 | 非缺陷 | 1-based anchor 与切片匹配；补语义说明即可。 |
| C8 | 投递语义需明确 | finish 进入答案候选，但 delivery 检查将其视为状态。应基于实际工具合同决定是否统一，不能只因两层规则不同便认定错误。 |
| C9 | **撤销** | 未知次数使总量 null 符合“不把缺测补零”；已知缺失数另有 unknown_cases 配套。 |
| C10 | 源码确认，恢复诊断问题 | ledger 破碎 JSON 抛解码错误而非返回 valid=False；仍拒绝损坏证据，不能自行截断 ledger 修复。 |
| C11 | **撤销** | 真源 runtime.py 无单点/三位数约束；Source/a.b.c.json 本轮通过。 |
| C12 | 源码确认，fact 冲突风险 | 同 family/predicate 覆盖可让 guard 只读后一条；正式组合应检查绑定和冲突，需后续整体 fixture。 |
| C13 | **撤销为缺陷** | all_case_accuracy 用全案例分母计未决为未答对，有测试固定；正确读法结合覆盖率。 |
| C14 | 部分成立 | marker 解析错误说明被吞；commit=False 是明确拒绝的防御路径，本轮已复现，撤销“死代码”。 |
| C15 | 低危清理项，源码逻辑确认 | fired_always 不消费；无候选路线会追加 evidence，每个 insufficient 路线必有 reason 或 semantic_relation 证据，因此 capability_gap 下空 evidence guard 冗余。清理不改变 admission。 |
| C16 | 源码确认，Mock 夹具风险 | base_predictions 显式 None 回落 gold；override None 可实现未决。应改缺键判断，减少假健康夹具。 |
| C17 | 低危等价条件冗余 | adopted 由 step 等于 adopted_step 派生，两条件语义重复，不是相同字面条件复制。 |
| C18 | **成立且升级为高风险；本轮竞争分支复现** | 同 protocol_hash、不同 candidate/output，在“exists 检查时不存在、open x 时已存在”的受控竞争中被接受。突破一次性协议的候选身份约束；不属于一般可读性项。 |
| C19 | docstring 与计数边界不一致 | False→None 不进入 new_errors，但 unresolved_added 独立阻断；不因此放宽门。 |

### 4.3 目标配置下的可达性

目标是 `full693_g0_evolution_20260928.json` 配合 autodl。默认 audit.enabled=true、causal_audit.mode=off；full-self-evolution 本身没有自动调用 `run_governance`，因此 B9/D4 是治理 CLI 的后续风险。research 与传统 PromotionRun 也不在该主入口的直接调用链上。

D0 是本次实际预检阻断。A1/A2/A5 与 D1 在主流程有触发条件，当前没有真实运行证明这些条件必然出现。D2 的预算 admission 机制有缺口，但**该配置每个机制最多 3 版本，LLM 修复次数每周期最多 1，单机制起草请求上界为 4，低于 development.max_provider_calls=5**；不能把其 max=1 反例写成当前配置一定超支。D3 需要恢复工件内容漂移或共享对象被修改，D5 需要 resolution 候选，均不能无条件断言当前三周期一定触发。

---

## 附五：深层代码 review 新增发现

### D0（高，当前实际阻断）校准记录与当前谓词源码指纹不一致

证据：本轮在 loopback-only 环境运行仓库自己的只读入口：

```powershell
python -B scripts/preflight_real_run.py --config evolution/configs/full693_g0_evolution_20260928.json --phase evolution --json artifacts/code_quality/039f705adb825d2b7e178b7072060beae72ee35b6f7c54eb400e21174eb41b43/43cca25847de4e2fbafee8672e53d52f/review_preflight.json
```

结果：exit=1、blockers=1、warnings=1、`NOT READY`。`final_delivery_present`、`required_action_present`、`verbatim_fidelity` 三个谓词均为 `source_fingerprint_mismatch`。693 例完整性、划分、sentinel 数量、runner admission、路径长度和超时配置检查通过。

根因边界：预检证明**当前环境的校准身份不适用**，尚未证明应归因于哪一个历史代码改动或环境差异。它不是网络故障，也不是凭此认定检测器无法启动；运行时会保持 fail-closed，却无法让这些机械谓词授权定罪，真实能力口径因此变化。

优化方案：先对比记录的 source fingerprint、当前 `_runner_source_fingerprint` 及其依赖链，查清代码/平台变化。若记录确实属于旧规则，保留原记录，在另行授权的证据验证阶段产出当前规则适用的新记录，随后重跑预检。不能只改哈希、降低校准要求或绕过 admission 来获得绿色结果。本轮未改校准文件，也未生成新准确率/基准结论。

### D1（高，定位候选组合活路径）单元按定位评分，组合又退回二元 M

位置：`EvolutionEngine._check_combination`，`evolution/engine.py:4330-4342`；对比 `_run_unit` 的 `candidate_target_kind` / `meets_localization` 分派。

两个已经 qualified 的定位单元在单元门里使用定位增益，但组合门只计算 `paired_delta`，直接 `meets_m`。组合纠正了责任步而检测真值保持正确时，二元增益为 0，尾部单元被记为组合失败。后续 gate/经验反馈因此可能把互不损伤的定位候选记为失败。

本轮受控函数级复现使用两个不同 hook（tracer.system / upgrade.system）、不同 directive 的候选，真实 registry 构造、配对指标和 `_check_combination`；已合格单元与 arm rows 为合成输入，仅 sentinel harmlessness 判定替换为 pass，以隔离组合目标分派：

```text
gold steps: A=3, B=4
baseline:  prediction=True, responsible_step=1（两例）
combined:  prediction=True, responsible_step=3/4
joint_improved=2，binary marginal_cases=0
selected=[IV-LOC-001]，dropped=[IV-LOC-002]
reason=combination arm failed HARMLESSNESS/M (marginal_cases=0 < 1)
```

这不是完整周期生成两条候选的证明；它证明组合消费者对 qualified localization units 问错了指标。目标配置 max_admits=2，允许进入多单元组合分支，是否出现取决于候选。

优化方案：组合保留每个成员的 target_kind/polarity，按目标读 detection/localization/resolution 增益与各自保护损伤，不能把所有目标压为二元 M，也不能加权相抵。增加“纯定位二单元”“检测+定位混合”“组合新定位损伤”正式周期测试。另审查治理的 `record_marginal`：目前 `run_governance` 同样只记录二元 marginal，不能把纯定位价值为 0 的历史当作没有贡献而自动退休。

### D2（中，配置敏感）开发请求预算只后记账，内部 repair 可以越界

位置：`evolution/development.py:137-145,663,749-761`；`evolution/proposer.py:865-904`；`evolution/development_adapter.py:35,104`。

`_exhausted` 只在调用 propose 之前检查“还剩至少一次”。propose 可内部递归修复并实际发多次请求；返回后按 notes 累加，`spend_provider` 无上限检查。LLMProposer 只知道全周期 `budget.llm_calls`，不知道本机制还剩的开发预算。

本轮用公开 ProposalBatch 模拟一次起草含一次 repair 的真实返回形状，无网络请求：

```text
DevelopmentBudget(max_provider_calls=1, max_versions_per_mechanism=1)
notes=[provider_called=True, provider_called=True/repair=True]
result: used.provider_calls=2，版本仍被记录
```

证明是 API 边界预算反例，不是实测两次收费。当前目标 max_provider_calls=5、max_versions=3、全周期 repair<=1，无该超限触发；其他合法配置可触发。

优化方案：起草与 repair 共享调用前 reservation，由 proposer/provider 实际请求入口检查剩余额度；notes 仅报告成本，不控制 admission。失败请求仍记账，preflight 拒绝不记账；预算不足返回明确 stopped/partial。只在返回后抛错无法收回已发生的请求。

### D3（高，恢复身份边界）候选内容漂移后仍沿用旧 L1 资格

位置：`evolution/candidate_versions.py:90-105,176-219` 的 load/record；`evolution/development.py:958-1000` 的 freeze；`evolution/engine.py:1921-1957` 的 recovery。

load_existing 接受 candidate.json 内的 candidate 与 candidate_hash，但不比对；freeze 主要检查 sidecar.status，然后重新计算当前候选哈希，并未验证它是否还是当初过 L0/L1 的版本。恢复直接拿旧 candidate_hash 与当前 candidate 配对，也不核验。record 对候选仅浅拷贝，其嵌套 body/claim 还可能与调用者共享。

本轮先用真实 CandidateDeveloper + 合成 L1 双臂生成 ready_for_freeze，再仅在临时目录修改已落盘候选的 directive，保留旧 candidate_hash/L1 sidecar；新建 developer 后分别 recovery 和 freeze：

```text
old candidate_hash = 724b1ca87bc834de...
actual changed hash = 6c18d4a161b00a1...
recovery: 接受 changed candidate，仍声称旧 hash
freeze: 接受 changed candidate，重新计算新 hash，沿用旧 ready_for_freeze/L1
```

这是持久身份漂移的确定复现，未修改仓库候选或真实 registry。**后续正式门仍会检查候选**，本条不等于直接绕过全部准入或已发生生产激活；被突破的是“进入正式门的版本已在指定邻居上通过开发验证”这一前提。

优化方案：record 深拷贝持有独立快照；load/freeze/recover 必须验证 candidate 内容摘要、候选内 content_hash（如存在）与记录一致，L0/L1/sidecar 同时绑定候选、机制、目标+邻居案例、当前输入/配置/运行身份。漂移拒绝并要求新版本重新验证，不能重新算哈希替旧证据补身份。增加“磁盘内容漂移”和“外部嵌套对象变更”两个入口反例。

### D4（中，治理入口）为统计 fired_rate 额外执行一遍不受治理 deadline 约束的 active arm

位置：`evolution/engine.py:4707-4721` 的 `run_governance` 与 `:4391` 附近 `_evaluate`。

治理开始计算一个 stage deadline，由 `_ArmRunner` 管理 active/leave-one-out arm；但每个已测 attribution 写 marginal 时，为获取 fired_rate 又调用 `_evaluate(registry, gain)`。这个调用没有传递治理 deadline，且不复用刚读到的 active_rows。多个 entry 会重复同一整批调用；对 stochastic backend，fired_rate 也不再来自本条 attribution 的那次测量。

本轮使用真实 engine/registry 与合成行，将 ArmRunner.run 和 `_evaluate` 设为可计数离线 fixture：一个 entry 的治理中 ArmRunner 调用 4 次，另外 `_evaluate` 调用 1 次、无 kwargs/deadline。这只证明额外调用和边界绕开，未实测真实耗时或费用。

优化方案：fired_rate 直接读取归因的 active_rows，移除这次重复执行。如确需独立统计，明确标为独立 instrument arm，走同一 deadline/预算与报告身份。验收“额外 active evaluation 为 0”和“stage deadline 已耗尽不能因统计重新启动测量”。本入口由独立 governance CLI 调用，不是 full-self-evolution 当前主入口自动执行。

### D5（高，resolution 契约）即使修 A5，机会计算和行为分派仍不完整

位置：`evolution/metrics.py:253-272` target_opportunity、`evolution/engine.py:2594-2667,2745-2769`，`detector/interventions.py:82,90`，`evolution/gates.py:835`。

schema 接受 `target_kind="resolution"`，但 polarity 只接受 recall/precision/boundary。`target_opportunity` 没有 resolution 特例，仍查 detection 的极性；gain_opportunity 只统计 False→True 可修的 FN / True→False 可修的 FP，不统计健康 None 的解决机会。engine 对非 localization/joint 的成员仍使用普通 behavior_gate/M，未按 resolution dispatch 到专用门；普通 behavior_gate 的 `polarity=="resolution"` 分支又无法通过 schema。

本轮真实 registry 校验与指标复现：

```text
schema accepts target_kind=resolution, polarity=recall
baseline: gold=True, prediction=None, authority=authoritative, runtime_valid=True
candidate: prediction=True
paired_delta.flips={U->T:1}
target_opportunity(... gain_opportunity(baseline))=0
```

周期在 unavailable 判断即可认定没有机会，尚未花候选臂；因此单改不可测判定不构成 resolution 修复。传统晋升的 B12 与机制成员健康判定 `mechanism_row_healthy` 要求 bool prediction，也需一起核对。

优化方案：以 target_kind 决定目标族，以 polarity 决定检测方向；resolution 的机会和增益只针对健康未决→权威正确，专用 behavior/M 在周期、组合、传统/机制对齐晋升、目标 outcome、治理中按统一合同消费。不要新增非法 polarity 来绕 schema。增加全入口健康 U→正确正例、U→错误负例、基础设施 U 不可认作健康机会的反例。

---

## 附六：本轮验证与覆盖边界

### 6.1 已执行的独立验证

本轮环境：Windows、Python 3.13.7、Asia/Shanghai 日期 2026-10-12；源码 HEAD 为 `6cec2d2`。没有调用真实 provider。pytest 自动继承 `scripts/offline_guard/sitecustomize.py`，其网络审计只允许 loopback；临时复现也显式导入同一 guard。真实配置预检仅读取 credential 是否存在，不输出凭证值，不发请求。

完整验证命令：

```powershell
python scripts/verify.py --scope all
```

本轮报告目录：

`artifacts/code_quality/039f705adb825d2b7e178b7072060beae72ee35b6f7c54eb400e21174eb41b43/43cca25847de4e2fbafee8672e53d52f/`

本轮完整验证已结束，`summary.json` 总状态 **FAIL**；2026-10-12 13:06:39–13:45:00（Asia/Shanghai）。全部 2272 个测试中 **2270 通过、2 失败、0 error、0 skipped**，code_only/local_data/platform 三个范围均执行。另执行的 CLI/能力/sandbox/smoke 检查均通过，共 18 个检查项中仅 pytest 的退出码非零。

| 检查 | 本轮结果 |
|---|---|
| AST/依赖审计 | PASS；80 个生产模块，无报告的重复定义、依赖方向违规/import cycle |
| 原始数据完整性 | PASS；693 例与 integrity.json 一致 |
| pytest 全量 | FAIL；2270 passed / 2 failed，失败项详见 6.3 |
| detector/evolution/experiments CLI 与命令注册 | PASS；安装态/source 用例也纳入全量 pytest |
| capability-audit / admission / metamorphic / sandbox | PASS |
| Mock smoke / capability-smoke | PASS；准入与 audit 断言、single_shot 路径均通过 |
| 验证前后源码/配置/测试/协议清单 | PASS；source_unchanged_during_checks 的 exit_code=0，SHA-256 始终为 `039f705adb825d2b7e178b7072060beae72ee35b6f7c54eb400e21174eb41b43` |

证据：[全量 summary](../artifacts/code_quality/039f705adb825d2b7e178b7072060beae72ee35b6f7c54eb400e21174eb41b43/43cca25847de4e2fbafee8672e53d52f/summary.json)、[原始 pytest 日志](../artifacts/code_quality/039f705adb825d2b7e178b7072060beae72ee35b6f7c54eb400e21174eb41b43/43cca25847de4e2fbafee8672e53d52f/pytest.log)、[只读 preflight](../artifacts/code_quality/039f705adb825d2b7e178b7072060beae72ee35b6f7c54eb400e21174eb41b43/43cca25847de4e2fbafee8672e53d52f/review_preflight.json)。这些均为本轮新工件，不覆盖既有验证结果。

额外执行的确定性反例/反证：A1 引擎健康/不可测两组及排除后覆盖足够的加强反例；A2 真实 Defense 投票→裁决；A3 完整 Fake research 版本超限；A5 健康未决 arm；B2 冲突 note 两组；B3 frozen mismatch；B4 gap 重现；B8 baseline 预算无报告；B9 registry 依赖退休；B10 词法反例和 693 例只读任务普查；C11 合法多点 ID；C14 commit=False；C18 受控协议竞争；另直接执行三项 A4 正常可选预算反证测试；D1 组合目标；D2 repair 计数；D3 持久候选漂移；D4 治理额外 arm；D5 resolution 机会。复现代码通过 stdin 在内存执行，临时案例和工件位于系统临时目录，不新增仓库测试文件。其中第一次 A1 探针未关闭 engine 就清理目录，触发 Windows 文件锁错误；后续正确关闭 engine 的重跑才作为 A1 证据。遗留临时目录 `C:/Users/NUAA/AppData/Local/Temp/tmp82nxerqz` 的删除被工具自动审批拒绝（blocked by policy，未返回具体原因），已保留；其内容不作为审查证据。

### 6.2 覆盖方式与限制

按调用链重点审查了 detector 的 Defense→Adjudication、API→request budget→batch、frozen replay、measurement、路径/数据投影、证据冲突，以及 predicate DSL 的进程资源终止；审查了 evolution 的 audit→unit→combination、development→version store→freeze/recovery、research 初始化/预算/报告、promotion 的机会/稳定性/协议 claim、governance→attribution→retire/rollback。对 identity、causal_contracts/task、retrieval、sandbox、mechanisms、metamorphic 读取入口和相关边界，结合全量已有测试验证；**未逐行证明每个源文件正确**。

对每个发现寻找相邻合同和反证：因此撤销 B3/B7/C9/C11/C13，收窄 A1/A4/B6/C14 等。无意证明 provider 模型行为、终端网络、真实吞吐、预算估算准确率或三周期进化收益。只读预检里的时间/成本读数来自历史 cost model，不是本轮新增测量。

D1/D2/D4 使用显式 fixture 隔离边界，D3/C18 使用临时工件内容漂移/竞争注入。它们证明指定合同在这些输入下失败，不证明现有历史报告已受污染。本轮另只读遍历 693 例，确认最大 normalized events=43（Magentic_One/061.json）、>64 为 0、detected required actions 为 0，原列六种短语均 0；这一部分现有独立证据。其他未保留且未重跑的原轮探针/并行审计结论不升级成新证据。


### 6.3 两个失败项的归因与局部重跑

两个失败节点局部重跑仍为 **2 failed**（2.55s），不是全量执行顺序导致的一次性失败。重跑 JUnit：[review_failure_recheck.xml](../artifacts/code_quality/039f705adb825d2b7e178b7072060beae72ee35b6f7c54eb400e21174eb41b43/43cca25847de4e2fbafee8672e53d52f/review_failure_recheck.xml)。本轮没有为了通过测试改动源码或夹具。

| 失败节点 | 实际行为 | 归因与优化 |
|---|---|---|
| `tests/test_preflight_real_run.py::test_calibration_state_is_reported`（:259） | 测试期望无 blocker；当前三谓词 source_fingerprint_mismatch，预检正确阻断。 | 与 D0 是同一个当前校准资格问题，不重复登记源码缺陷。解决校准适用性后才允许真实预检通过；纯机制测试可用显式 fixture，真实数据 readiness 测试不能 Mock 掉阻断。 |
| `tests/test_baseline_693_dryrun.py::test_usage_lower_bound_counts_attempts_that_reported_nothing`（:477） | `attempts_without_usage=12`、缺测案例=1 的断言通过；`total_is_lower_bound` 实际 None，测试期望 True。 | **T1：夹具与当前来源合同漂移**。该手写 execution 只有次数与 usage，无 usage_source/requests lineage；`measurement._execution_usage_source` 因无观察证据返回 unknown，`run_baseline_693.py:262` 按合同不声称已证下界。技术文档 :132 与 test_usage_source 同样要求 unknown→None。应该补可校验的真实请求观察夹具来验证“有来源但部分缺测→下界”，另保留“无来源→未知”断言，不能放宽生产读者把未知数字直接认证为下界。 |

T1 是离线验收可靠性问题，不是当前成本读者的已确认运行错误；它也说明仅引用旧的“全绿”记录不足以描述当前完整 scope=all。离线全量仍为失败，不能把扣除这两个失败后的数量当作验收通过。

---

## 附七：优化方案与下一轮验收顺序（仅规划，不执行代码修改）

### 7.1 工作包

| 顺序 | 范围与根因 | 交付要求 | 验收门 |
|---|---|---|---|
| P0：真实启动资格 | D0 校准身份；源码/工件真源核对 | 解释 mismatch 来源；保留旧证据，明确适用校准记录的产生与验证方式。不要直接改 fingerprint。 | 同一当前源码/配置只读 preflight 无 calibration BLOCKER；记录仍绑定真实规则与原始 corpus。若需新增校准测量，先明确另行工作范围。 |
| P1a：目标与测量合同 | A1/A5/D5、D1、B12/B14 | 定义健康未决、基础设施失败、分裂票与稳定性；按 target_kind 接通单元→组合→晋升→治理；审计保留完整行，明确减去不可测行后的最低覆盖。 | 引擎级部分 sentinel 故障有可解释结果；resolution 健康 U→正确走专用门；两定位单元都可保留；混合组合不新增损伤；fresh 晋升有协议要求的稳定性证据。不能以减少样本消掉必要覆盖。 |
| P1b：裁决与证据权限 | A2/B2/C18 | upgraded/null 与裁决合同一致，但仍保持 degraded 权威；冲突说明不等于 resolution；一次性协议所有竞争/非竞争分支全 identity 校验。 | live emitted ballots 与 frozen 同形；首听证 refuted、后续 upgraded 来源准确；无关数值/仅提到 E1 不升级冲突结论；两个不同 candidate/output 只能一个消费协议。 |
| P1c：持久资格与生命周期 | D3/B1/B4/B9/C10/C12 | L1 资格绑定候选和输入身份，深拷贝；未被预算选中≠证伪；gap 新 epoch 重开；退休先求依赖闭包/可解释拒绝；ledger 损坏诊断；fact 冲突明确归属。 | 候选内容/嵌套对象漂移不能 freeze/recover；三合格单元只准入二个时第三个保留预算未选中状态；依赖单元退休不抛出未处理异常、不留下孤儿；损坏证据不自动截断；两同名证据不能静默授错权。 |
| P2：预算与结果可解释性 | D2/D4/A3/A4/B5/B6/B8、T1 与剩余 C 项 | 请求前 reservation；复用归因 active_rows；所有预期预算终态完整落报告；默认/可选路径异常范围明确；校正成本来源夹具 T1；审计报告列出实际演练规则；只清除已核销冗余。 | 请求 cap=1 不发生第二次 repair；多案例一个预算终态不终止其他案例；research max_versions=1 不落第二版本；baseline exhaustion 有 partial/report；治理不为 fired_rate 增加 arm；guard/CLI/恢复合同不回退。 |

P1 内先明确合同再修改消费者，避免只改一处判断后暴露相反方向的问题。P0 可以先做只读身份诊断；P1/P2 的代码实施必须在下一轮授权修改代码后推进，本轮未执行这些修复。

### 7.2 应维持的正确行为

保留 B3 的 ok/state mismatch 拒绝；保留 B7 三种成本口径；保留未知请求总数为 null；保留 commit=False 拒绝标签偷读；保留原始 ledger/校准/标签和冻结身份；保留 terminal/fatal/cancel 传播边界。修 A2 不把升级的少数定罪票写成完全权威；修 A5 不把 provider failure 未决当成健康机会；修 A1 不因某次坏行就无条件宣称审计通过。

### 7.3 最终交付状态

本轮仅更新本文件；全量离线结果为 2270 passed / 2 failed，另完成局部重跑与只读 preflight（NOT READY），并生成独立新工件。未改源码、配置、测试、原始数据或历史证据，未做真实请求、基准或调参。后续先按 P0 确认校准身份，再按 P1 的全入口反例推进修复；不能用“旧套件全绿”替代新增反例和真实启动资格。


---

## 附八：用户修复后的续审与遗漏修复（2026-10-12）

本次范围由用户授权升级为“继续 review 并修复遗漏 bug，进行真实启动预检”。入口为 HEAD `6cec2d2` 加用户已有的未提交修复，未重置或覆盖其他工作区改动。初始源码清单保存于 `artifacts/code_quality/review-followup-20261012-e3470609/initial_source_manifest.json`。旧章节的 NOT READY、2270/2 和“未改源码”描述的是修复前那轮，不是当前工作区状态。

### 8.1 用户修复的复核

当前工作区已接通升级 Defense 的 null 步号合同、冲突 resolution、健康未决/resolution 目标、组合目标分派、晋升稳定性重掷和完整协议竞争身份、版本内容快照、research 上限与 partial 报告、gap 重新开放、治理依赖闭包/目标归因及成本夹具。初始实际 evolution 预检返回 READY，0 blocker、1 cost warning；三个当前校准谓词均 admitted。当前工作区的验收证据将在 8.3 定稿，不以旧 HEAD 的通过记录代替。

### 8.2 已复现并修复的遗漏

| ID | 根因与实际反例 | 修复与验证 |
|---|---|---|
| R1 | D3 只核验 candidate.json 自己的哈希。修改内容并重算记录哈希、或删除记录哈希，recovery 仍与旧 ready_for_freeze sidecar 配对；load 后再改磁盘也不能只信内存。 | CandidateVersionGraph.read_version 是 load/freeze/recovery 的共同真源；目录版本、非空内容摘要及认证 sidecar 必须一致，恢复再次读取磁盘；证据为两个内容漂移反例先失败后通过。 |
| R2 | D2 新增额度 setter 没有作用域恢复：一次开发草稿用完额度后，后续普通周期仍被留存的 0 拒绝；没有可绑定 family 时，也会提前扣掉一次未发请求的额度。 | setter 返回先前额度，drafter 在 finally 恢复；额度预留移到实际 provider_call 前，内部 repair 仍共享本次额度。None/已有额度两个作用域反例及 preflight 拒绝反例先失败后通过。 |
| R3 | B1 只修了多单元过滤。零预算、单联合单元超限、去重后单联合单元的提前返回都没有 budget_unselected_entries；周期经验还只用单位主 ID，遗漏 partner。 | 所有这些返回使用共同预算排除结果；经验消费全体 entry ID。三种提前返回反例先失败后通过；max_admits=0/1/2 的完整 Mock 周期证明两个成员都得到 budget_unselected 或 admitted，均不会被错误记为 falsified。 |
| R4 | arm 门禁只读首听证顶层镜像，遗漏后续听证失败；内部或规范化行显式 invalid/空 authority 的未决仍被计为健康机会，定位读者又未消费健康否定信息。 | failed_stage_state 统一读取顶层和全听证；内部权威性一致检查拒绝 invalid/空权限；定位收益消费健康判定。新增后续 Tracer/Defense、resolution 权限和定位信用反例。规范化的最小指标行仍可使用既有接口，显式运行元数据不能授予假权威。原 evaluation_state 测试用 vocabulary 外的 authority=unresolved 构造健康行，已改为 authoritative；provider 故障夹具则用 invalid，仍断言 infrastructure 分类。 |

新增测试位于 `evolution/tests/test_followup_review.py`：首次 8 个反例全部失败；后续听证两反例及定位信用反例也实际失败，invalid authority 机会通过直接探针复现。修复后新增回归共 18 项（含 3 项完整周期经验验证），相关定位、开发、adapter、proposer、research、resolution/health、measurement 回归 184 项通过（58.85s）；JUnit 见 `artifacts/code_quality/review-followup-20261012-e3470609/related_regressions.xml`。所有 provider 均为明确 Fake/Mock，未发真实模型请求。

### 8.3 完整验收和最终启动预检

完整离线命令 `python scripts/verify.py --scope all` 已结束，总状态 **PASS**；2026-10-12 18:02:11–18:43:50（Asia/Shanghai）。**2321 passed、0 failed、0 error、0 skipped**，code_only/local_data/platform 全部执行，网络护栏为 loopback_only。18 项检查全部通过，包括源码/依赖审计、693 例数据完整性、三 CLI 注册、能力准入、metamorphic、sandbox、Mock smoke 的审计/准入及 single_shot 断言。验证前后源码/配置/测试/协议清单一致，SHA-256 为 `42e41a9444ae771427c2e371e7d756f857cc8a57bb6895190e99f7a053a270cc`。

证据：[全量 summary](../artifacts/code_quality/42e41a9444ae771427c2e371e7d756f857cc8a57bb6895190e99f7a053a270cc/3783f48fbc6248bd8698a2c6469d2549/summary.json)、[pytest 日志](../artifacts/code_quality/42e41a9444ae771427c2e371e7d756f857cc8a57bb6895190e99f7a053a270cc/3783f48fbc6248bd8698a2c6469d2549/pytest.log)、[JUnit](../artifacts/code_quality/42e41a9444ae771427c2e371e7d756f857cc8a57bb6895190e99f7a053a270cc/3783f48fbc6248bd8698a2c6469d2549/pytest.xml)、[源码清单](../artifacts/code_quality/42e41a9444ae771427c2e371e7d756f857cc8a57bb6895190e99f7a053a270cc/3783f48fbc6248bd8698a2c6469d2549/source_manifest.json)。此前因新增反例中断的验证已标为 interrupted_for_source_update，不计入验收。本轮 R1–R4 的已复现遗漏经修复、相关回归及全量检查通过，关闭；修复前附六的两项失败未在当前源码重现。

冻结源码的 baseline/evolution 两阶段只读预检均 **READY，0 blocker、各 1 cost warning**：[baseline](../artifacts/code_quality/review-followup-20261012-e3470609/verified_preflight_baseline.json)、[evolution](../artifacts/code_quality/review-followup-20261012-e3470609/verified_preflight_evolution.json)。预检生产源码摘要为 `b095d1fb028a31cc2ee65ebb9621219bde57535760eef1e2c5130f506fa48ecd`；它与全量验证清单的文件范围不同，两者分别绑定本次相同工作区。693 例标签及 split、超时/周期预算、凭证存在性、无 DETECTOR 环境覆盖、干净工作目录、路径余量、三个定罪谓词校准和四 runner 准入均通过。

唯一警告来自历史 cost model：workers=8 时，全量一遍工作下限 21.3h、模拟 p50 22.2h、效率调整 28.0h、长尾下限 12.5h，约 18,628,941 tokens。这是历史模型估算，不是本次真实测量。READY 表示本地启动资格满足，不证明远端 endpoint、模型、实时配额或真实吞吐。续修范围与证据汇总见 [完成核验](../artifacts/code_quality/review-followup-20261012-e3470609/followup_completion_audit.json)。

本轮未修改原始案例/标签、既有冻结协议或历史 ledger；没有替用户发起真实基准或进化轮次。所有最终结果均绑定当前工作区源码。

### 8.4 后续优化与运行边界

本次新增修复限于七个 evolution 生产文件、一个既有测试夹具和一个新增回归模块；初始清单对比可区分用户修复与续修。R1–R4 的确定性合同已由相邻消费者和完整 Mock 周期验证。对原章节条目保留原证据及撤销/收窄说明，不把本次检查解释为逐行证明所有代码、所有外部输入均正确。

| 顺序 | 下一步 | 验收依据 |
|---|---|---|
| 运行前 | 固定本次通过验收的源码、配置与校准记录，保存实际启动命令及启动时 preflight 工件；工作区再变化则重新核验相关合同和身份。 | 验证清单与实际启动身份一致；不复用中断报告或旧源码通过记录。 |
| 获得真实运行授权后 | 先检查实际 endpoint/模型、超时与 usage 来源，再按真实配置运行；记录逐请求预算、取消和恢复行为。 | 真实响应与实际 usage 的来源链可追溯；离线 READY 不替代远端可用性证据。 |
| 取得真实耗时后 | 对比历史估算和实际耗时/请求量，调整并发、超时、周期预算与缓存复用；优化以相同样本覆盖、权限和准入门为约束。 | 报告实际分位数、缺失 usage 和预算终态；不能以减少健康覆盖或放松门禁换取通过。 |

当前仅有历史成本估算警告，没有远端连接、真实吞吐或进化收益证据。候选认证、预算作用域和健康状态继续由共同读取合同消费；新增入口应复用这些合同并验证异常分支，避免相邻入口重新出现同一遗漏。


---

## 附九：五例真实检测（2026-10-12）

用户后续明确授权“选择五条真实数据，五并发跑一次真实幻觉检测，不跑自进化”，覆盖前轮仅离线/预检范围。本次通过正式 detector batch 入口运行，未修改生产源码、配置或标签，未传 intervention。使用 autodl 的 GLM-5.3-flash 和现有多阶段/投票/超时策略；五条案例一次批处理，内部请求和合同允许的 retry/topup 不等于额外案例。

固定随机种子 20261012，从五个不同来源各随机抽一条；选择不读取 gold，五个 raw 文件摘要均与 integrity.json 一致。样本为 Octotools/035.json、BFCL/135.json、OpenManus/085.json、OpenDeepSearch/008.json、SmolAgents/028.json。run_id `live5_20261012_185056`，workers=5。原始样本未复制或改写；请求清单与源码/配置身份见 `artifacts/detector/live_checks/live5_20261012_185056/request_manifest.json`，结果写入同目录 runs。

真实检测已完成：2026-10-12 18:51:33–19:01:50（Asia/Shanghai），墙钟 **616.3s**，退出码 **0**，5/5 实际测量、5/5 authoritative、无 unresolved/降级/缺失/stop。峰值 provider 请求并发 **5**，provider attempts **25**；真实 usage 为 prompt=67,355、completion=195,057、total=**262,412 tokens**，cached=14,592、reasoning=183,588。usage 来源 request_observations，usage_missing_attempts=0。summary 的 elapsed_seconds=1744.0 是案例耗时之和，不能误当约 29 分钟墙钟。

| 案例 | 检测 | gold | 责任步号 / gold 步号 | 结果 |
|---|---|---|---|---|
| Octotools/035.json | 幻觉 | 幻觉 | 3 / 2（content_step） | 检测正确，步号不同 |
| BFCL/135.json | 无幻觉 | 无幻觉 | — / — | 正确 |
| OpenManus/085.json | 幻觉 | 幻觉 | 2 / 1（action_step） | 检测正确，步号不同 |
| OpenDeepSearch/008.json | 无幻觉 | 无幻觉 | — / — | 正确 |
| SmolAgents/028.json | 无幻觉 | 幻觉 | — / 1 | 漏检 |

本地 evaluate：检测 4/5 与标签一致，TP=2、TN=2、FP=0、FN=1；正例召回 2/3，精确率 2/2。严格责任步号命中 0/3，需分别看 content/action 目标；这五例不能代表全量准确率。OpenManus/085 的 Judge 记录引用来源位置不匹配的合同警告，最终状态 ok 且权威，原始警告已保留。漏检与两例步号差异是需后续复核的效果观察，尚未归因为实现缺陷；本轮未改代码/提示词、未重跑。

证据：[五例报告](../artifacts/detector/live_checks/live5_20261012_185056/report.md)、[完整 evaluate](../artifacts/detector/live_checks/live5_20261012_185056/runs/live5_20261012_185056/evaluation.json)、[运行 summary](../artifacts/detector/live_checks/live5_20261012_185056/runs/live5_20261012_185056/summary.json)、[请求清单](../artifacts/detector/live_checks/live5_20261012_185056/request_manifest.json)、[日志](../artifacts/detector/live_checks/live5_20261012_185056/batch.log)。运行前后源码清单及五个 raw 案例摘要均一致。未执行自进化。


---

## 附十：定位差异与疑惑度机制复核

针对本轮两条正例和一个 FN 的详细分析见 [定位分析](../artifacts/detector/live_checks/live5_20261012_185056/localization_analysis.md)。Octotools gold=2/pred=3、OpenManus gold=1/pred=2 都是外部错误来源位置与助手首次采纳位置不同：当前 LOCALIZATION_LAW 明确排除 tool observation，本轮 Judge/Tracer/Defense 一致采用助手位置，没有编号平移或裁决覆盖。SmolAgents 未执行 Tracer；三票高置信 clean 使召回 not_needed，且 gold 的几何说明存在需要独立复核的内部矛盾，不能据此直接认证模型答案 27。

本轮 causal_audit.mode=off、negative_feedback=false；五例无 causal_diagnostics，两个正例 causal_context=null，25 次请求没有因果审计调用。因此疑惑度机制未参与这次真实检测。当前 semantic_proxy 是离散证据风险排序，真实 teacher-forced PPL 后端仍 unsupported。另在网络护栏下零 provider 调用重建画像/正式诊断，发现 Octotools/SmolAgents 缺 role 导致信号跳过，SmolAgents 代码事件还超过 6000 字符预算；OpenManus 无可用局部算术证书。两条正例即使使用现有 tracer_context 诊断函数仍 context=None，单开开关不能证明收益。

后续先定义来源/采纳/交付三位置和明确评分目标，再做数据格式与长事件有界适配、域内核验和真实 PPL 后端选择，最后才进行冻结样本配对消融。未新增真实请求、未改源码/配置/标注或原运行证据；原 4/5 检测与 0/3 严格步号指标保留。


---

## 附十一：数据集首错口径、风险机制启用与十例验证

本轮用户授权统一口径并优先数据集标注、接通独立检测与后续自进化的风险机制，离线验收后随机十例十并发真实检测；用户已选择现有语义风险代理，不要求本轮接入 token 级 PPL。未运行真实自进化。

### 11.1 已实施合同

- 定位目标统一为 dataset_first_error_event.v1：包括工具观察/人类回复在内的完整事件首错，须同一材料性错误传播到交付；Judge/Recall/Tracer/Defense 和进化提案均使用该口径。不是对所有预测减一，不向检测器透露 gold。
- 结果 localization 记录事件位置、原始 source_step_id 与引用通道；评分优先唯一原始编号→一基事件位置，歧义/无映射明确报告而不猜。693 例核对：443 个正例都可唯一映射，250 个负例无步骤标注；32 个非顺序原始编号事件不再直接混用坐标。独立 evaluate 和 evolution gold loader 共用规则，报告记录目标，显式不同目标不计命中。
- 共享配置和 autodl 默认启用 tracer_context/semantic_proxy.v2。缺 role 保留未知但仍消费可见通道；长事件保留风险候选与截断说明，不整条跳过。独立 API、进化 LiveDetectorBackend、缓存/冻结身份与提案消费者已接通。
- 听证优先强局部证书，不能绑定时使用通用 risk_context：有界候选/引用/片段不具备定罪或定位权限；生成/实际消费摘要可核对。packet、policy、完整 target 均绑定；重封摘要后的内容篡改仍在 provider 前拒绝；共享输入预算不足时回退并记录，repair 不重置预算。
- 局部算术证书分别记录错误观察来源和助手采纳；进化只在窄合同的材料性/明确复制链/符号修复及更早偏差排除成立时按来源对齐。通用风险仅作为 hypothesis 进入提案，不能伪造自动因果修复资格。真正 teacher-forced PPL 仍 unsupported；阴性扩展保留生产校准门，不因开风险代理绕过。

技术合同见 technical-solution.md 的“数据集首错定位与风险上下文合同”。测试中的基础管线 fixture 显式 off；更改的定位/评分夹具反映新目标或提供真实可映射历史，不改原始案例和标签。初始四项反例全部失败，修复后相关 343 项回归、扩展 1133 项通过，新增 22 项边界测试。证据目录 artifacts/code_quality/localization-risk-20261012，含 related.xml、expanded.xml、初始源码清单和 mapping_census.json。

### 11.2 完整验收与真实运行

初次冻结源码 SHA-256 cdebe8cd3f4c145622fc47d118bab0f5b0640cf7f2196fa4c366d8b95d0af9df 的全量 verify --scope all 已结束，2339 passed/3 failed；失败及修复后的最终验收见下文。初次 source audit 与原始数据完整性通过；两阶段只读 preflight READY，各 0 blocker/1 历史成本估算 warning，证据为 localization-risk-20261012/preflight_verified_baseline.json 与 preflight_verified_evolution.json。

十例已固定在 artifacts/detector/live_checks/live10_20261012_riskv2/cases.json；seed=20261012，在冻结 554 例开发池均匀无放回抽取，排除上一轮五例，不读取 gold 进行选择，不使用独立 validation 子集。源码、配置及 raw case 摘要绑定 request_manifest；只有离线完整验收通过后才启动正式 batch，workers=10，autodl 风险机制开启，无 intervention/自进化。

最终完整验收 **PASS**，源码 SHA-256 `45fa48b37fee0dc9ced12b761985ce78d05f9b45f89646c9f6fa6d5064fb484e`。2026-10-12 20:30:24–20:46:46（Asia/Shanghai），**2343 passed、0 failed、0 error、0 skipped**，18 项检查全部通过，source_unchanged_during_checks=0。通过标准库调度器按测试文件划分四组，调用原 scripts.verify 的全部检查；完整收集/执行数量一致，实际命令与分组证据保留，不声称是单一 pytest 进程。

证据：[最终 summary](../artifacts/code_quality/45fa48b37fee0dc9ced12b761985ce78d05f9b45f89646c9f6fa6d5064fb484e/f657f1cab7bd44e68116e2a81859714d/summary.json)、[实际分组命令/数量](../artifacts/code_quality/45fa48b37fee0dc9ced12b761985ce78d05f9b45f89646c9f6fa6d5064fb484e/f657f1cab7bd44e68116e2a81859714d/pytest_execution.json)、[收集清单](../artifacts/code_quality/45fa48b37fee0dc9ced12b761985ce78d05f9b45f89646c9f6fa6d5064fb484e/f657f1cab7bd44e68116e2a81859714d/collection.log)、[合并 JUnit](../artifacts/code_quality/45fa48b37fee0dc9ced12b761985ce78d05f9b45f89646c9f6fa6d5064fb484e/f657f1cab7bd44e68116e2a81859714d/pytest.xml)、[调度脚本](../artifacts/code_quality/localization-risk-20261012/parallel_verification.py)。初次串行验收为 2339 passed/3 failed：新 localization_record 对不含 position 的公开简化 packet 读取过严已修复；实验测试改为明确的 off fixture 与 shadow 差异，51 项复核通过，新增简化 packet 的未知位置反例。初次并行尝试因为 basetemp 父目录不存在产生 fixture setup 错误，其 FAIL 工件和环境归因保留，不作为通过证据；最终使用独立系统临时目录四组全部通过。

最终 [baseline 预检](../artifacts/code_quality/localization-risk-20261012/preflight_final_baseline.json)、[evolution 预检](../artifacts/code_quality/localization-risk-20261012/preflight_final_evolution.json) 均 READY，0 blocker、各 1 历史成本 warning。真实十例已于验收通过后启动，run_id=live10_20261012_riskv2，workers=10；用户已中止两条长尾，八条完整结果及后续修复见下节。

### 11.3 十例真实检测中止统计与长尾复核

#### 1. 中止范围与统计口径

按用户指示于 **2026-10-12 21:58:17（Asia/Shanghai）**终止 live10_20261012_riskv2 的剩余两条运行。已核验命令行 run_id 后仅停止本批主进程 18392 及当时所属 provider 子进程 656、3936；未停止其他批次。批处理退出码 -1 是强制中止，不能写成成功结束。八份 canonical result、原始 attempt、输入与历史源码身份全部保留；不补写两条假结果、不自动重跑、不启动自进化。

开始时间 20:47:24，截止中止墙钟 **4253.0s / 70.88 分钟**。最后一个完整结果于 21:02:39 结束，随后 **55.63 分钟**没有新 canonical result。完成的单案例耗时范围 90.0–915.1s；这些耗时不能相加当作墙钟。

十条来自原先冻结的开发池，随机种子 20261012，无放回并排除上一批五例；不根据标签挑选，未动独立验证集。workers=10 表示案例并发上限；运行控制的 provider 名额上限也是 10，多票请求共享这十个名额。中止丢失峰值统计，不能声称实测峰值正好为 10。

**已完成八条**：8/8 authoritative，6/8 标签一致（75%），TP=4、TN=2、FN=2、FP=0；正例召回 4/6（66.7%）、精确率 4/4。数据集首错完整事件目标下定位命中 **1/6（16.7%）**；在成功检出的四个阳性中是 1/4。**原请求十条**：完成率 8/10；正确且完成 6/10，定位命中 1/7 个带首错标注的正例。这些覆盖读数包含两条未完成样本，不能称两条检测错误，更不能将中止当阴性。小样本且发生中止，不据此估计全量收益。

| 案例 | gold 标签 | 实际检测 | 预测责任事件 / gold 事件 | 案例耗时 | 结果 |
|---|---|---|---|---|---|
| OpenManus/032.json | 幻觉 | 幻觉 | 4 / 3 | 915.1s | 标签一致 |
| BFCL/009.json | 无幻觉 | 中止/缺失 | — / — | 截止中止约 4253s，未完成 | 未测完 |
| BFCL/069.json | 幻觉 | 幻觉 | 5 / 4 | 609.1s | 标签一致 |
| BFCL/002.json | 幻觉 | 无幻觉 | — / 6 | 90.0s | 漏检 |
| BFCL/059.json | 幻觉 | 幻觉 | 7 / 6 | 434.2s | 标签一致 |
| SmolAgents/082.json | 无幻觉 | 无幻觉 | — / — | 242.4s | 标签一致 |
| BFCL/091.json | 幻觉 | 无幻觉 | — / 6 | 729.9s | 漏检 |
| Magentic_One/031.json | 幻觉 | 中止/缺失 | — / 3 | 截止中止约 4253s，未完成 | 未测完 |
| OpenManus/016.json | 无幻觉 | 无幻觉 | — / — | 342.3s | 标签一致 |
| Camel/057.json | 幻觉 | 幻觉 | 3 / 3 | 535.1s | 标签一致 |

#### 2. 请求与成本证据

八条已完成案例保留下来的 provider 尝试 **57** 次，全部 HTTP 200，无已记录 transport_error，usage 缺失 0；观测 total tokens **463,066**。它是全批费用/用量的**下界**：中止两条的已完成 HTTP 请求未写出 case attempt，其用量无法从本地还原；未结束的远端请求是否继续计费也没有本地证据。

阶段请求：Judge 30、Judge repair 2、Recall scanner 3、Recall repair 2、causal upgrade 3、Tracer 4、Tracer repair 1、Defense 12。最长单次请求观测耗时 **411.9s**，其中包含排队；最长名额排队 **305.1s**。例如 OpenManus/016 的一次 Judge 排队 305.1s、整个请求 339.5s，主体耗时来自本地并发排队，而非模型推理本身。

#### 3. 为什么会出现长尾

##### 3.1 配置允许的长等待与分阶段叠加

真实配置 case_timeout_seconds=10800（3 小时）；基础单次 HTTP 超时 900s，transport_retries=2，timeout_growth_factor=1.5，provider_call 的累计预算 2400s（40 分钟）。重试共用该累计预算，不能把它写成三次各 40 分钟，但不同阶段/投票/repair/听证可以继续调用，各阶段再共用 3 小时案例截止。因此 70.9 分钟仍在允许预算内，并不自动说明死循环。

Judge/Defense 都有 3 票与 2 次 topup；合同不合法还会 repair。Recall→upgrade→逐个阳性 hearing 有串行依赖，每个 hearing 再跑 Tracer 和多票 Defense。defense/recall 的 max_tokens=32768，允许较大的生成量；它是输出上限，不是必须生成量。已有八例的 3–12 次请求以及 BFCL/091 的 6 次 Judge + 2 次 repair，证明分支和合同修复已实际放大请求数。

##### 3.2 案例并发与投票请求的名额竞争

十案例可以同时发起多票，初始最多约 30 个 Judge 任务争用 10 个 provider 名额；名额限制正确约束 HTTP 并发，但队列也会增加案例墙钟。完成样本已经记录 2–5 分钟排队。不能简单把请求并发增大到 30 来“修复”，那可能增加上游限流和费用。

**已复现的实现漏洞 LT1**：provider 累计 deadline 建立在排队前，RunControl.request_slot 原先却只检查 case/added deadline。provider 40 分钟窗口到期仍可能等待名额，直至 3 小时案例截止；获得名额后还可能发出超时 0.001s 的过期请求。离线确定性反例：总窗口 3s、容量在 4s 才释放，原实现仍触发 HTTP；修复后 3s 即退出且不发送，不把容量超时错记为全局取消。

此漏洞可以扩大长尾，但**没有旧请求级日志，不能证明两条未完成样本实际走到了这个漏洞**。停止时仍有两个所属 provider_worker，支持当时存在活动传输，不能把最后 55.6 分钟全部归因为排队。

##### 3.3 两个未完成输入并非都长

BFCL/009：6 个事件、history 序列化约 1511 UTF-8 bytes；Magentic_One/031：14 个事件、约 18680 bytes。对照已完成 BFCL/002：22 个事件、6912 bytes、90.0s；OpenManus/016：14 个事件、9394 bytes、342.3s。轨迹文本量可能影响模型耗时，但它不能解释短 BFCL/009 也进入长尾。

两条没有完成的 attempt.json，旧实现把请求观测收集在内存，整次 detector.detect 返回或抛异常后才落盘，强制终止会丢失这部分记录。**无法核实**两条最后的具体阶段、此前请求数、HTTP 状态、topup 次数、累计用量或上游超时/限流情况；不能将这些猜测写成既成事实。已完成八条 HTTP 200 也不能用于证明未完成两条没有服务问题。

#### 4. 风险代理到底发挥了什么作用

八条都有 causal_diagnostics 分支记录，但只有 **6 条成功生成 profile/audit**；OpenManus/032 与 BFCL/091 的诊断为 budget_exhausted/time。四个阳性听证中，**BFCL/059、Camel/057 的最终有效 Tracer 实际消费了语义风险上下文**，保留具体 digest；Camel 定位命中，BFCL/059 仍比 gold 晚一步。这证明真实输入链已接通，不能证明风险机制普遍改善定位。

BFCL/069 初始 Tracer 使用了风险上下文，但引用合同失败后进入 repair；repair 记录 causal_context_budget:time，最后有效 Tracer 的 context_digest=null。即使结果里保存了原 context 对象，也不能将它统计为最终调用消费。OpenManus/032 在诊断时就超时，没有有效风险上下文。

**已确认并修复的 LT2**：新增机制的 30s 时间预算使用从诊断开始的墙钟，包含基础模型请求及本地容量排队。基础 Tracer 等待超过 30s 后，repair 会失去上下文；先前阶段慢也会令之后诊断失效。它把原流程耗时错误记为机制新增耗时。修复在统一 provider 包装层扣除基础请求等待的并集；多个重叠基础请求只扣一次，新增审计/阴性复审请求仍计时，真实 case 截止不延长，输入/输出/请求次数预算不变。独立 Detector 与 evolution backend 共用此包装层。

BFCL/002、BFCL/091 两个漏检都没有阳性 hearing。当前风险上下文用于已进入 hearing 的 Tracer，negative_feedback_enabled=false；画像生成本身没有裁决权，也不会自动重开高置信阴性。因此不能指望仅启用 Tracer 上下文就修复全部 FN。真实 token 级 PPL 仍为 unsupported，本轮所用的是语义风险代理。

四个检出的阳性里，OpenManus/032、BFCL/059、BFCL/069 都落在 gold 的后一事件。评分合同现在统一为 dataset_first_error_event.v1，但模型仍可能定位到后续陈述/采纳事件。现有结果不支持用统一 -1 或直接读取 gold 修正预测；首源和后续采纳还需独立证据核验。

#### 5. 本轮修复与后续优化

**LT1 已修复**：provider 总窗口覆盖名额排队，过期不再发送，案例取消/截止仍优先传递。**LT2 已修复**：新增时间预算排除基础 provider 等待，repair 不因基础慢请求丢失上下文。**LT3 已修复**：正式 batch 每个案例 attempt 新增 requests.jsonl，实时记录 queued/started/finished、唯一 request_id、时间、阶段、排队、超时和已观测 usage，flush+fsync；不写 API key、完整 prompt 或响应正文，不将日志当判定结果。中止后可区分排队与已发送阶段，并读取已完成请求的用量；不能追溯补回此次旧运行丢失的信息。直接调用可通过 request_observer_scope 接入同一公共观测合同。

先做上述确定性修复，保持本次原始配置和原始结果。下一次获授权的真实验证应保留请求日志，并依据阶段耗时分布制定单案例/单阶段快检预算，限制 retry/topup 与大输出带来的尾部放大；预算耗尽输出 unresolved，不伪造 clean。仅缩小超时会缩短等待，也可能降低完成率，需要同时记录覆盖率。继续观察 origin 与 adoption 的证据分离；阴性扩展需独立校准和许可，不能为了两条 FN 跳过现有校准门。当前不新增真实检测或自进化运行。

#### 6. 离线验收与身份边界

长尾修复后 **242 项相关回归通过**（12 个 detector/evolution 测试文件，含 8 项新增用例），三个 CLI help 入口通过、git diff --check 通过。覆盖 provider 排队超时、保留 case deadline、基础/新增请求时间预算、重叠等待、上下文 repair、请求级持久化、中止无假 canonical result、观测写盘失败不伪装成传输重试及已有 backend/反馈合同。新增时间预算约束本地新增工作和新增 provider 调用，不能从一次基础调用的等待时间分离出上下文引起的推理时间增量；该增量继续由上下文输入预算和案例总截止约束，不声称拥有可测的精确增量延迟上限。

十例运行前的全量验收 2343 项/18 检查通过，对应当时源码；本轮修复后未重新执行全套，不能把前次结果冒充当前全量验收。真实运行 manifest 的 detector source_identity 为 `de5965704277b73979c9db015b05fd5debfc2083c0aa0a635034c5550c8ce50c`；当前修复后的 detector source_identity 为 `1ad3326b109297fccb02cb57913af085f8be0cd1d187f7966a80a19f23a8c1f8`。新修复不会改变八条已测结果，也未做真实重跑证明长尾已消失。

证据：[中止记录](../artifacts/detector/live_checks/live10_20261012_riskv2/stop_record.json)、[已完成八例评分](../artifacts/detector/live_checks/live10_20261012_riskv2/completed_evaluation.json)、[原十例覆盖评分](../artifacts/detector/live_checks/live10_20261012_riskv2/requested_evaluation.json)、[请求清单](../artifacts/detector/live_checks/live10_20261012_riskv2/request_manifest.json)、[中止汇总](../artifacts/detector/live_checks/live10_20261012_riskv2/interrupted_summary.json)、[详细执行/诊断记录](../artifacts/detector/live_checks/live10_20261012_riskv2/interrupted_details.json)、[长尾回归 JUnit](../artifacts/detector/live_checks/live10_20261012_riskv2/long_tail_regression.xml)、[修复验收身份](../artifacts/detector/live_checks/live10_20261012_riskv2/long_tail_verification.json)、[原批处理日志](../artifacts/detector/live_checks/live10_20261012_riskv2/batch.log)。

