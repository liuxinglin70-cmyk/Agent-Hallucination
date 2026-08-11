# Provider 输出漂移导致 Skill 被误杀/假接受的根因诊断与修复报告（第二轮）

## 结论先行

第一轮修复（`DIAG_SELF_EVOLUTION_BLOCKERS.md`，P0–P4）解决了进化循环内的
信号分裂问题，之后暴露的剩余阻断是**自然语言规则对 provider 的影响是全局的**：
注入规则后 provider 输出整体重采样，证书普查整体漂移（e2e 实测
`appeal_error_*→initial_error_*`）。上一轮建议"用更窄的条件"收窄命中——
本报告论证这是错误方向：**漂移不是规则适用性的问题，而是框架把不稳定的位置
身份当作跨 run 语义身份来度量效应**。第一性原理修复是把"证书是什么"（内容）
与"证书排第几"（位置）分离：全部跨 run 比较改用 **claim 级内容指纹**。

修复后经离线合成复现 + 真实案例验证：
- 纯重命名漂移不再被误报为"规则已执行"（postcondition 失败关闭）；
- 真实的 targeted withhold 被 claim 指纹如实测出；
- 坏规则（FN 案例上削弱证据链的 withhold）以**真实量化理由**被拒
  （`module_local_score_regressed:3.300->1.300`，此前读作无效应）；
- FN 方向的 claim 级证据增量被如实计分（`3.300->4.300`
  local_improvement → 接受）；
- 既有接受面（L2 策略、稳定 canary）行为不变。

---

## 漂移机制（代码行级证据链）

规则注入 → genome 变化 → provider prompt body 变化 → 缓存 key 变化 →
provider 重新采样（temperature=0 也不逐字节稳定，且规则文本本身改变行为）。
关键问题在检测器内部：

1. **证书 ID 是位置性枚举产物。** `_merge_prosecutions`
   （`proof_engine_v3_4.py:354`）在 merge 时按枚举顺序重铸全部证书 ID：
   `row["certificate_id"] = f"{route_name}_error_{index:03d}"`。census 候选 ID
   同理（`_union_census`，`v3_candidate_{N}`）。**任何注入只要改变 provider
   各阶段产出的数量或顺序，所有 ID 全量漂移。**
2. e2e 实测（`probe_withhold_e2e`，BFCL/000）：baseline 产出
   `appeal_error_001..005`（存活 005）；注入 withhold 规则后产出
   `initial_error_001..002`（存活 002 或全量 withheld——两次采样两种形态），
   verdict 未变或变为 unresolved。

漂移无法靠"更窄条件"消除（LLM 对 prompt 变化整体敏感），也不应消除——
框架需要的是在漂移下如实度量。

## 三个被污染的判定通道（修复前）

### 通道 A — postcondition 恒真（假执行证据 → 死规则漏过）
`targeted_certificate_withheld`（action_spec.py）用 **ID 集合差**
判定"目标证书被 withheld"：`baseline_ids - replay_ids`。
漂移下两侧 ID 命名空间不同（appeal_* vs initial_*），差集**恒非空** →
恒报"已执行"。离线合成验证：纯重命名场景 satisfied=True（应为 False）。

### 通道 B — local score 的两头错（真效应读作无效应，噪声读作效应）
`module_local_score`（fitness.py）的 prosecution 项 =
`valid_error_certificate_count`（全表面计数）。实测合成：
- 纯 census 重组（存活 claim 不变、行数变化）→ score 移动 ±2（假信号）；
- verdict 载体 claim 被真 withheld 但替换 claim 顶上 → score 0 变化
  （真 effect 读作 no_effect）。
接受与否变成漂移方向的抽签。

### 通道 C — instrumentation 缺口当作未执行（好规则被降级）
`_attribute_intervention_fitness`（evolution_runner.py）在 postcondition
失败且 provider 未自报时降级为 `intervention_not_executed` → 拒绝。
但 postcondition 的 field 义务可能落在结果 schema 不可见的字段上
（如 `recovery_evidence_refs`——P4 已记录同类缺口，当时只对
gold task 改进放行）。过程级（claim 级）改进同样应被豁免时没有被豁免。

## 修复（第一性原理：内容身份与位置身份分离）

新增 `src/sehd_v6/core/bccse/certificate_identity.py`：

```python
CLAIM_IDENTITY_FIELDS = ("origin_event_id", "breach_type", "claim_kind",
                         "norm_source", "impact_kind")
certificate_claim_fingerprint(row) -> "fp:<sha256-16>"
```

- 全部字段为闭词汇值或确定性 packet 事件锚点 → 同一 claim 跨 run 指纹不变，
  不同 claim 不碰撞；
- adjudicative 字段（`benchmark_causal_status`/`recovery_state`）与 free-text
  刻意排除——它们是进化允许移动的输出，不是 claim 的身份；
- `surviving_carrier_fingerprints(result)`：把 court 的存活 ID 解析为 claim
  指纹集；不可解析（merge 32 行截断）回退不透明 id 并标记 `resolved=False`
  → 严格消费端失败关闭。

五个消费点切换：

| # | 位置 | 修复 |
|---|------|------|
| F1 | action_spec.py postcondition | `withhold_certificate` 新增 `baseline_result` 权威路径：claim 指纹集差判定；纯重命名 → `no_baseline_carrier_withheld`（失败关闭）；身份不可解析 → `carrier_identity_unresolved`（失败关闭）。legacy id 路径保留给无 baseline 的外部调用者 |
| F2 | action_spec.py `_OPERATION_EFFECT` / `_MODULE_OPERATION_EFFECT` | `withhold_certificate` 观测面改为 `surviving_certificates:decrease`；`emit_certificate` 改为 `surviving_certificates:increase`（claim 级，非全表面计数） |
| F3 | fitness.py `observable_process_delta` | 新增 `surviving_certificates:decreased/increased` 载体移动 token（仅 resolved 时发出）；`prosecution_certificates`/`defense_certificates` 计数器改为 claim 级 valid 集大小 |
| F4 | fitness.py `module_local_score` | prosecution 侧两模块（`candidate_to_verifier_route`/`atomic_verifier_instruction`）改为双轴 claim 级分：emitted valid claims + surviving carriers + coverage rank。FN 方向（emit/verify 产生 claim）与 FP 方向（withhold 移除载体）各有可测轴，纯重命名不变 |
| F5 | evolution_runner.py `_attribute_intervention_fitness` | instrumentation-gap rescue 扩展到过程级：postcondition 失败详情属于可观测性失败类（`field_obligation_absent`/`unobservable_in_result`/`unmapped_postcondition`/`carrier_identity_unresolved`）且 gold 极性校验的过程改进已通过时，不再降级为 `intervention_not_executed`。工具看得见且未满足的义务（如 `no_baseline_carrier_withheld`）仍然降级 |
| — | genome.py / module_charter.py / evolution_runner.py | `surviving_certificates` 加入 OBSERVABLE_FIELDS、atomic_verifier 的 MODULE_OBSERVABLE_FIELDS、契约 field_prefixes |

**不改动**：consensus gate 的 `startswith("appeal_")` 前缀语义——它是
**run 内**语义（同一次检测内 ID 稳定），不受漂移影响。

## 验证证据

**离线合成复现（`probe_drift_repro.py`）**：三个场景 × 三个通道全部通过——
纯重命名 postcondition=False、真 withheld=True、漂移+行数增加 score 不变。

**真实 e2e（`probe_fingerprint_verify.py`，BFCL/000 withhold）**:
baseline 存活 `appeal_error_005` 解析为 `fp:bdb3ce0e315155ba`
（event_007 contradiction/assertion/prior_trajectory_evidence/final_answer）;
evolved 全量 withheld → movement `withheld=[fp:...], emitted=[], resolved=True`;
postcondition 如实报 `targeted_certificate_withheld:1_claim(s)`;
local_score 2.0→1.0。

**真实环路负向 A/B（`probe_focus_fn.py`，缓存热控制变量）**:
同一 withhold 风格坏规则、同一 replay——
修复前 `no_measurable_target_local_change`（盲）→
修复后 `worse: module_local_score_regressed:3.300->1.300`（如实量化拒绝）。

**完整接受链正向（`probe_positive_accept.py`，BFCL/004 + require_evidence_field 机制）**:
claim 级 prosecution 增量 3.3→4.3 → `local_improvement`；
postcondition `field_obligation_absent`（schema 不可见）+ provider 未自报 →
instrumentation-gap rescue 生效 → **passed=True（可接受）**。
修复前同链路：降级为 `intervention_not_executed` → 拒绝。

**回归**：`probe_evolution.py`（4 案例）与 `probe_evolution2.py`（8 案例）
决策不变（L2 accept_policy 保持，稳定案例保持）。全包 65 模块导入通过。

## 边界与后续

1. **defense 侧 claim 计数未切换**（`clean_defense_boundary` 的 score 仍用
  court valid 计数）：FP 归属路由主要落在 prosecution 侧模块，defense 模块
  的漂移暴露面较小，留待有真实 FP defense 案例时再切。
2. 漂移本身是 LLM 固有属性；本修复保证框架在漂移下**如实度量**，不承诺消除
  drift 的副作用面（如 canary 终态翻转仍会被 canary 门拦截——那是忠实守卫）。
3. 接受率恢复后，达到 SOTA 的下一步在实验协议层：按 README 导师要求用独立
  holdout 测量 champion genome 的跨案例泛化增益。
