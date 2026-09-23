# 底层深度审查：测量完整性缺陷与修复（2026-09-23）

## 这一轮做了什么

对实验框架做了一次底层代码审查，重点是**"框架记下来的东西是否等于它实际测到的东西"**，而不是功能是否存在。
审查覆盖五条路径：cycle 主流程、开发循环、晋升协议、检测器决策机制、执行管线与三条落盘路径。

审查方式：五路并行只读审计 → 每条高危结论**独立复现**（脚本在仓库外，全部离线）→ 修复 → 补非平凡回归测试 →
复跑原复现脚本确认修复生效。**未运行任何真实模型服务**，未改任何门限数值。

**结论**：发现并修复 **19 个活缺陷**（其中 8 个会直接污染大规模实验的结论）。修复后 `917 passed`（基线 843），
`scripts/verify.py` 15 项全 PASS（基线 12 项）。

**基线对照**

| 项 | 修复前 | 修复后 |
|---|---|---|
| pytest | 843 passed | **917 passed** |
| verify.py | 12 项全 PASS | **15 项全 PASS** |
| 新增回归测试 | — | 74 项（4 个新文件 + 既有文件扩充） |

## 修复清单

### A. 账本与协议的完整性（证据不可伪造）

| # | 缺陷 | 复现读数 | 修法 |
|---|---|---|---|
| A1 | **账本跨进程追加非原子**：`last_hash()` 与写入之间无文件锁，两个进程从同一 `prev_hash` 往后写，丢记录并断链。而 `_run_aligned` 与 `run_cycle` 都以 `ledger.verify()["valid"]` 为前置，断链后**所有后续运行拒绝启动** | 4 进程 × 150 次：期望 600 条，落盘 **549 条，丢 51 条**，链在 index 3 断裂 | `evolution/ledger.py`：追加时取 `fcntl`/`msvcrt` 文件锁（含超时与专属错误），写入后 `flush`+`fsync`。复跑：**600/600 条，链完整** |
| A2 | **v1 冻结协议可原地改写**：v1 无 `protocol_hash`（只有 v2 有），`load_protocol` 无法校验协议自身。控制臂行为已知后移动一个角色字符串即可改变判定 | 同一候选、同一批次、同一路径，仅把 `roles["BFCL/000.json"]` 从 `protection` 改为 `benefit`：`promotion_unmeasurable` → **`promoted` 并写入 registry** | `protocol_self_hash()` + v1 冻结时写入自哈希，`load_protocol` 无条件重算比对。复跑：编辑被拒（"changed after it was frozen"） |
| A3 | **v1 不钉轨迹内容**：只钉批次文件哈希（案例 id 列表），改案例文件后仍可计分 | 替换 6 个案例的 `history` 后 `load_protocol` **接受** | v1 冻结时写入 `case_digests`；`load_protocol` 逐个比对；无 digest 的合成冻结在运行期报 `trajectory_pinned=false` 并拒绝 |
| A4 | **门可以被 `--stages` 裁掉**：`gates` 把跳过的门默认成 `{"passed": True, "skipped": True}`，`failing` 为空即准入，且 registry 理由串硬编码宣称四门全过 | `--stages structural,harmlessness,behavior` → 同一候选从 `promotion_rejected`（`new_fp=1`）变为 **`promoted`，registry 写入 1**，理由写"STRUCTURAL/HARMLESSNESS/BEHAVIOR/M passed" | v1 与 v2 一致要求四门齐全；理由串改为按实际评估过的门生成。复跑：被拒，`gates evaluated: []` |
| A5 | **被拒候选可重掷到过**：attempt claim 只服务 v2；v1 换一个输出目录即重新实测两臂 | 同一协议、同一候选：attempt1 provider 中断 → `promotion_unresolved`；attempt2 新目录 → **`promoted`** | `_claim_protocol()` 覆盖 v1（按协议自哈希认领）；同目录幂等、新目录拒绝 |
| A6 | **v1 不冻结控制臂身份**：冻结时的推理基于当时的 active registry，运行期控制臂取运行当时的 registry，期间任何一次准入都会静默改变对照组 | 空 registry → `promoted`；期间准入一条 → 控制臂变为 `['IV-DEPLOYED-EARLIER']`，报告无任何字段记录对照身份 | v1 冻结时写入 `baseline_registry_hash`，运行前比对 |
| A7 | **冻结的 `coverage_floor` 运行期从未被读取**：实际下限来自运行时的 config，放宽门限即可重新计分同一冻结批次 | 协议冻结 `min_protected_tp=2`，以 `-5` 运行 → **`promoted`** | `load_protocol(config=...)` 比对冻结 floor 与运行阈值。复跑：被拒 |
| A8 | **`workers`/`timeout_seconds` 被 `or` 静默改写**：`workers: 0` → 默认 8/臂；`timeout_seconds: 0` → 无批级墙钟 | 冻结文件写 `workers=0` → 解析为 8（**16 路并发**） | 冻结参数校验：`workers>=1`、`timeout>0` 或显式 `null`，不合法即拒绝而非修正 |

### B. 基础设施失败 vs 候选被证伪（最贵的一类误读）

| # | 缺陷 | 复现读数 | 修法 |
|---|---|---|---|
| B1 | **整臂 fail-closed 被写成"候选已证伪"**：候选臂因预算/停摆全部 `unresolved` 时，HARMLESSNESS 把它读成丢失真阳性（`new_fn>0`），返回 `evaluable=True`，终态分类为 `rejected`，**写入情景记忆 `falsified: true`**，并进 `rejected_keys` 永久禁提案 | 候选臂 **0/12 例被测量**，HARMLESSNESS 返回 `new_fn=2, new_unresolved=4`，终态 `rejected`，记忆 `{"falsified": true, "unevaluable": false}` | `_run_unit` 用 `arm_has_infrastructure_failure` 标记不可测行；`_mark_unmeasured` 把该 unit 的失败裁决改写为 `not_evaluated`；`gate_rejection_is_unevaluable` 优先读该标记。复跑：终态 **`not_evaluated`**，记忆 `falsified: false, unevaluable: true` |
| B2 | **`gain_measured` 是硬编码常量 `True`**：gain 臂整批 fail-closed 时机会读数两侧均为 0，与"真的没有可修复方向"完全同形，账本因此记下 `{"recall": 0, "precision": 0}` | `gain_measured: true` 而 8/8 gain 行全部 `batch_deadline_exceeded` | `_instrument_report` 按不可测行数计算 `gain_measured`，新增 `unmeasured_gain_cases`；`_measurable_families` 接收该标志，不可测时给出的阻塞理由是"仪器没读到"而非"动作空间没机会" |
| B3 | **审计在空比较上认证**：排除不可测行的空检查在 sentinel/gain 切分**之前**，排除项全落在 gain 时，`paired_delta([], [])` 读成 `marginal_cases=0 < 1` = "门拒绝了缺陷"，审计认证通过 | sentinel 全测、gain 全 fail-closed → `_defect_rejection_after_exclusions` 返回 `''`（=审计成立），裁决 `certified` | 补 gain 侧非空检查，为空即 inconclusive。复跑：返回 "no measurable gain case left"，裁决 **`frozen`** |
| B4 | **审计裁决跨阈值复用**：指纹含源码/配置/数据/registry/env，**不含 `gates` 与 `budget`**，而裁决完全由门限产生 | 改门限后第二次审计报 `cache: "hit"`，复用旧裁决 | 指纹纳入 `gates` 与 `budget`。复跑：第二次 `cache: "miss"` |
| B5 | **fail-closed 行污染 frozen-replay 基线**：恢复的行无条件写入 `_base_results`，空 envelope 使该案例在本轮剩余所有部署臂上抛异常，只在远处表现为一行"未决" | 污染后 `_frozen_stages` 返回 `{}`，`run_case` 抛 "frozen replay envelope is empty" | 只写入权威行 |
| B6 | **治理被截断后反向读**：截断的 active 臂把 unresolved 计为"不正确"，于是空臂对它有"增益"，`attribution_marginal` 变**正**；同一份有害注册表在机时充足时 `net=-3` 两次触发回滚，机时不足时 `marginal=+5`、永不回滚 | `attribution_marginal: [[5]]` 而真实读数为 `[[-3]]` | 归属与净贡献在任一臂不可测时**不记录**（新增 `measured`/`attribution_measured` 字段）；回滚改为只在可测时记账。退役与回滚窗口改为按**不同 cycle** 计数（原按行切片，一次测量可填满整个窗口） |
| B7 | **回滚报告"已触发"但什么都没恢复**：目标 cycle 无快照时 `rollback_to_snapshot` 返回 `None`，调用方忽略返回值仍写 `triggered: True` 与 `rolled_back` 账本 | 记录 `triggered: True`、`restored_snapshot: ""`、entries 未变 | 判空 → `triggered: False` + 专属原因；`registry.rollback_to_snapshot` 的空文件名不再解析成快照目录本身（原会抛 `PermissionError`） |

### C. 统计与终审口径

| # | 缺陷 | 复现读数 | 修法 |
|---|---|---|---|
| C1 | **McNemar 口径与门口径不一致**：终审用 `correctness_improved`（把 `unresolved→correct` 计为改善），而 `gates._decisive_correction` 明确拒绝把校准算作修正 | 只靠"敢多判"、零真实修复的 registry：`mcnemar_p = 0.0157`（< 0.05）；真实修复但 provider 别处 abstain：`p = 1.0` | `paired_delta` 新增 `decisive_fixed`/`decisive_broken`；`_mcnemar_improvement_p` 只数决定性翻转。复跑：多判型 `overall = fail` |
| C2 | **终审可对"零真实修复"的 registry 判 pass**：判据全读宽口径计数 | 5 例 `unresolved→correct`、无任何错误决定被修正 → `overall = "pass"` | `main_pass` 增加"至少一例决定性修正"且 `decisive_broken == 0`；`effect` 与 `verdict` 均报出该计数 |
| C3 | **`mini_effect` 静默跳过**：唯一 out-of-sample 读数仅在"所有 cycle completed"时计算，键缺失在 CLI 合同上等于通过（退出码 0） | 报告无 `mini_effect` 键，退出码 **0** | 未运行时显式写 `{"status": "not_run", "passed": null}` 并给出原因 |

### D. 开发循环

| # | 缺陷 | 修法 |
|---|---|---|
| D1 | **L1 允许目标案例被破坏**：只对 `neighbor_cases` 做回归判定，**对 `target_cases` 从不做**——修好 A/1 的步号同时让 A/2 真阳性变漏检仍判 `passed`。目标族通常只有 2 例，一次漏检吞掉一半 | 对 `target_cases` 也做回归判定，新增独立 `target_regressed` 字段；`delta.new_errors>0`/`tp_lost` 入拒绝理由 |
| D2 | **开发阶段证伪不写情景记忆**（只遍历四门报告，开发失败版本从不到四门），下一轮看不到"这条机制已试过并失败" | 新增 `_record_development_episodes`；`check_l1` 生产 `reason_codes`（归档原恒为 `[]`） |
| D3 | **组件库写/读根目录不一致**：归档写 `archive_root`，检索读 `component_root or work_root/components` → **没有任何组件进入过下一轮起草提示** | 归档写入检索实际读的路径；两者不一致时构造期给出显式警告字段 |
| D4 | **`NameError: identifier`**：恢复分支引用调用处的关键字参数 | 改为局部名并补测试覆盖该分支 |
| D5 | **开发回放不受 cycle 墙钟约束**，且开发数据守卫漏掉 `split.validation`（mini 协议下唯一的 out-of-sample 集） | 接入 deadline；守卫补齐 `validation` 与 `final` |
| D6 | 基础设施抖动被写成 `failed_l1` | 新增 `unmeasured` 状态，与"已证伪"分离 |

### E. 执行管线与准入面

| # | 缺陷 | 修法 |
|---|---|---|
| E1 | **结果缓存键不含模型/端点/温度**：`DETECTOR_MODEL` 等 env 覆盖会被 `resolve_route` 读取但不进缓存键，同 `work_root` 换模型直接命中旧模型的行 | 抽出共享常量 `DETECTOR_ENV_KEYS` 与 `detector_env_fingerprint()`（一处定义），并入缓存 namespace、cycle contract 与审计指纹。并修正旧键表：`DETECTOR_UPGRADE_MODEL`/`DETECTOR_TRACER_MODEL` 是死键（upgrade/tracer 走 judge route），已从表中移除 |
| E2 | **`terminal_authority="evidence_only"` 运行期无人读取**：声明"只报告、永不判罪"的 runner（`sandboxed_component`/`typed_semantic`）可以授权 `convict_mechanical_violation`，产出 `authoritative` 定罪 | 机械定罪的证据绑定改为按 `(predicate, runner)` 查表并先按 runner 的 `terminal_authority` 门控；`typed_dsl`（conviction）路径逐字不变 |
| E3 | **`evaluate_run` 把降级/未决读数计为真实预测**：recall 网从未跑通的 clean 案例记 `tn=1`、`decisive_accuracy=1.0` | 主指标只统计 `authoritative`；宽松口径完整保留在 `terminal_metrics`；新增 `degraded`/`degraded_disagreeing` 字段 |
| E4 | **preflight 早退丢掉本轮刚发现的 capability gaps**（报告无 `capability_backlog`，不写 `gaps.jsonl`） | 早退处补做记账并重存报告 |
| E5 | **gap `occurrences` 永不累加**：只在内容变化时追加观测，`cycle` 不参与比较，10 轮后仍为 1，`occurrences>=2` 的"反复出现"信号**永不触发** | 每次观测到即追加，identity 去重 |
| E6 | **`capability-audit` 是"永远 PASS"项**：`cmd_capability_audit` 无条件 `return 0`，产物内容从不被断言 | 退出码改为报告自身裁决；`verify.py` 增加内容断言 `capability_audit_admissible`；两个 `--help` 项补"命令注册"断言 |
| E7 | **localization 声明结构性写不出 `route_failures.jsonl`**（两次真实运行的目标都是它）：① `gain_opportunities` 只有 `recall`/`precision` 两键，`boundary` 恒取 0；② `localization_behavior_gate` 的返回体不带 `fired_rate`/`behavior_events` | ① boundary 取两侧机会之和；② 抽取共享 `deployment_events()`，两个 BEHAVIOR 门读同一份触发计数。复跑：localization 路径 `observed=1`（原恒为 0） |
| E8 | `archive.root` 是唯一未被解析的路径，按调用方 cwd 解释，多会话下归档分裂 | 纳入 `load_config` 的解析列表 |
| E9 | 非布尔 prediction（`1`/`0`）被静默降级为 `None`（=未决），控制臂的真实决定被读成弃权，候选"白赚"增益 | 严格化：非 `True`/`False`/`None` 抛错，与 `detector.result.prediction_state` 对齐 |

## 修复后仍存在的已知边界（如实记录）

以下是**本轮未修**或**本质上不可由代码修复**的部分，大规模实验前必须知道：

1. **holdout 未创建**：`datasets/protocols/final_holdout_v1.json` 仍不存在，693 例全部在开发暴露范围内。
   这属于用户决策（红线），本轮未动。
2. **并发无锁仍是运维约束**：账本追加现在有跨进程文件锁（记录不再丢），但同一 `work_root` 跑两个 run 仍会
   互相覆盖 registry、检查点与缓存。框架不阻止，也没有 work_root 独占声明。**这是本轮的修复边界**。
3. **校准审计不在任何运行路径上**：`evolution/calibration.py` 只在测试与手工脚本 `scripts/audit_calibration.py` 中被调用，
   `verify.py` 也不含它。计划 P1.5 声称"落地"，实际是离线工具。
4. **`typed_semantic` 是结构性死通道**：prompt 允许 LLM 使用它，STRUCTURAL 会放行，但运行期从不注入 `semantic_executor`，
   必然 fail-closed，且失败会被记为"候选有害"。本轮未从 prompt 移除该 schema。
5. **变形测试准入测的是参考实现而非候选自身**：`admission_error` 只检查"runner 已注册 + 有 reference subject"，
   候选自己的程序/组件不会被作为被测主体跑一次。
6. **检测器基座的三条结论**（本轮审计发现，未修，因为它们改变的是检测口径而非框架正确性）：
   - 机械定罪谓词的误报率在 250 个 gold-clean 案例上为 **7 例（2.8%）**，且这条通路**不经过 defense**。
     该 guard 尚未被部署，故对已发生读数污染为 0，但它是"已上膛"的。
   - `BFCL/008` 与 `BFCL/024` 在同一配置下的 8 次重复中判定分裂为 6/2，即**单元级读数不可作为"已修复"的证据**。
   - 142 个定罪行中 117 行的"责任步"指向自身散文为空、只有工具调用的事件；3/142 与 `step_tracer` 自身给出的步冲突却仍记 `authoritative`。
7. **`Magentic_One/049.json` 是稳定的长尾案例**（同一配置下实测 36.0 / 93.8 分钟），审计集由它主导；
   本轮未改审计集排期，也未给长尾单独排预算。

## 复现

```bash
python -m pytest -q                      # 917 passed
python scripts/verify.py                 # 15 项全 PASS
```

本轮新增的回归测试（全部离线，`conftest.py` 的 HTTP 守卫全程生效）：

- `evolution/tests/test_measurement_integrity.py`（10 项）：B1/B2/B3/B4/B5/C1/C2
- `evolution/tests/test_promotion_integrity.py`（17 项）：A2–A8
- `evolution/tests/test_development_loop_defects.py`（17 项）：D1–D6
- `evolution/tests/test_terminal_authority.py`（7 项）：E2
- `evolution/tests/test_detector_env_identity.py`（8 项）：E1
- `evolution/tests/test_capability_audit_exit.py`（4 项）：E6
- `evolution/tests/test_preflight_gap_accounting.py`（3 项）：E4

每条测试都经过"单独回退该修复 → 测试失败 → 恢复 → 测试通过"的验证，不是碰巧通过。

## 证据等级

**本报告说明**：框架的测量机制在离线与 mock 下不再产出自相矛盾的读数，且每一处修复都有可执行复现支撑。

**本报告不说明**：检测器准确率、定位准确率、自进化的收益或泛化。本轮**未运行任何真实模型服务**，
未改变任何门限数值，未创建 holdout。所有读数来自离线测试与 mock 后端。

## 未做的事（汇总）

- **未运行真实服务**（除离线 mock）。未调用任何 provider。
- **未创建 holdout**，未改划分，未重抽种子。
- **未改任何门限数值**（新增的是准入判据与完整性校验，不是门限替代）。
- **未接入真实组件生成器**，未从 prompt 移除 `typed_semantic`。
- **未修检测器基座的三条口径问题**（误报率、单元级不稳定、动作步/内容步混淆）——它们改变检测口径，
  需要单独决定口径后再动。
- **未测真实 provider 限流下的并发行为**：`workers` 与限流的耦合只能由真实运行标定。
