# 机械定罪谓词：校准护栏与误报修复（2026-09-23）

底层审查（`reports/deep_audit_20260923/`）发现一条**零 LLM、不过 defense、直接授权终局定罪**的通路：
`detector/predicates.py` 的谓词给出 convicting verdict → `decision_guard` 绑定该证据 →
`detector/adjudication.py` 记 `verified_error / authoritative`。这条通路**从不测量谓词是否正确**。
本轮做两件事：给这条路加一道"定罪权必须由校准证明"的护栏，并真正修掉谓词自身的误报。

## 一、实测数字（693 例 AgentHallu，250 gold-clean / 443 gold-error）

只读数据集，纯谓词离线求值（无 provider、无 judge、无 defense、gold 不进 packet）。
原始逐例结果：`sweep_before.json` / `sweep_after.json`；汇总：`summary_before.json` / `summary_after.json`。

| 谓词 | clean 误报（修前 → 修后） | error 命中（修前 → 修后） | 校准结论 |
|---|---|---|---|
| `required_action_present` | **7 → 0** | 32 → 3 | 修前会被校准拒绝，修后通过 |
| `final_delivery_present` | **3 → 0** | 24 → 0 | 修前会被校准拒绝，修后通过 |
| `verbatim_fidelity` | 0 → 0 | 0 → 0 | 一直通过（未改动） |

### 7 例误报的成因（`required_action_present`）

| 案例 | 修前抽出的"义务" | 真实成因 |
|---|---|---|
| Camel/023 | `complete name` | `"_ACTION_MARKERS"` 里的 `use the` 命中回答格式要求 "Use the complete name with article if any." |
| BFCL/092 | `details. it would be useful if you could retrieve specifics of my last` | `call the`/`call` 从自然语言里抓出句子片段 |
| BFCL/128 | `same credit card` | `use the` 命中许可语 "You are welcome to use the same credit card" |
| BFCL/019 | `close the ticket` | movement 分支把 `proceed to` 当移动标记，把动词短语当目的地 |
| BFCL/022 | `start the engine confidently` | 同上 |
| BFCL/038 | `display only the last 10 lines once you've done this` | 同上 |
| BFCL/152 | `delete that one` | 同上 |

### 3 例误报的成因（`final_delivery_present`）

OpenManus/048、062、069：轨迹以 `terminate(status=success)` 结束，答案**在工具 observation 里**
（如 `Extracted from page: {'text': 'The 2018 Jerlov Award winner is Dr. Raymond C. Smith.'}`）。
该谓词只知道两条交付通道（末条散文、answer 工具），把这种"答案在观测里"的交付判成"没有交付"——
这是对**框架形态**的误判，不是对轨迹的判定。

## 二、修法（每条都用全语料实测选定，不是针对个例打补丁）

**`required_action_present` 义务抽取 → 双条件**
`_TOOL_OBJECT_RE` 要求标记之后必须出现显式工具语义对象（tool/function/api/command/endpoint），
且对象之前的名字必须是标识符形状（≤3 词、无句末标点）。"Use the complete name with article if any"
两条都不满足：没有工具词，`complete name with article if any` 是句子不是名字。
实测：clean 误报 7 → 4，error 命中 32 → 25（这一步只砍误报，不动真阳性结构）。

**movement 分支 → 目的地标记**
`proceed to` 不是移动标记：它接不定式动词短语（"proceed to close the ticket"）。
保留真正接目的地的标记（go to / visit / drive to …），并要求目标不以功能词开头
（"plan a visit **and need** to know" 不是地点），且不在疑问句/条件从句里
（"perhaps we could swing by the nearest tire shop?" 是询问不是要求）。
满足判定也修了：旧实现要求**工具名含移动动词**，等于规定"移动必须由导航工具完成"，
于是被 `find_nearest_tire_shop` 完成的"去最近的轮胎店"被判成从未到达。

**pending engine start → 必须是轨迹的最后一事件，且该事件无工具调用**
旧实现只要轨迹里**出现过**许可问句就判 violated，detail 写的是"trajectory ends asking permission"——
对 8 个案例这是**事实错误的陈述**（问句出现在中间，之后还继续动作）。
另加 `_unquoted`：引号内的问句（agent 发出去的推文内容 "Should I take any action?"）不算 agent 在问用户。

**`final_delivery_present` → 三条交付通道**
末条散文 / answer 工具 / **内容型工具观测**。观测去掉运行时外壳
（`Observed output of cmd \`x\` executed:`）与固定完成横幅后仍 ≥20 字符即算一条通道。
阈值故意松：该谓词只判"通道是否存在"，不判答案对不对；它的定罪权由校准记录发放，
而校准会在"松规则仍产生 clean 误报"时直接吊销。

### error 命中变化必须如实报告

`required_action_present` 在 error 集上 **32 → 3**，即丢失 29 例。逐例审计（按 gold subcategory
与谓词自己写出的 detail 是否为真）结论如下：

* 修前 32 例中只有 **15 例**属于该谓词声称的族（`Missing Required Call`）；另 17 例落在
  `Unnecessary Tool Call`(4) / `Incorrect Tool Arguments`(3) / `Parallel Call Conflict`(2) /
  `General Reasoning`(4) / `Science Reasoning`(1) / `Summarize Misalign`(1) /
  `Human-Interaction`(1) / `Query Misalign`(1) —— 对错误轨迹也是"错的族"。
* 丢失的 29 例中，**0 例**同时具备"detail 事实成立"与"属于所声称的失效族"。具体地：
  * **8 例**（BFCL 039/041/044/047/052/056/078/085）detail 写 "trajectory **ends** asking permission"，
    而轨迹**并不终止**在许可问句上（问句在中间，之后还继续调用工具）。这是**事实错误的陈述**。
  * **3 例**（BFCL 063/059/074 等一类）同理，或问句出现在 agent 自己发出的推文引号内。
  * **13 例** movement 分支的目标根本不是地点：`close the ticket`、`start the engine confidently`、
    `start the car engine`、`juxtapose it`、`and need`、`book the flight using the secured credit card …`
    等，都是动词短语或连词片段，不是"未到达的目的地"。
  * 其余为目标确为任务元素但族不符或已被完成者：如 BFCL/066 的 `nearest tire shop`
    实际由 `find_nearest_tire_shop` 完成了（gold 为 `Unnecessary Tool Call`），
    BFCL/081 的引擎最终 `engineState: running` 已启动（gold 为 `Unnecessary Tool Call`），
    Magentic_One/020 的真错因是把车站映射错了（gold 为 `General Reasoning`）。
* 修后保留的 3 例（BFCL/057、062、077）**全部**属于 `Missing Required Call`，
  且都是该谓词真正实现的分支：轨迹确实**终止**在"Would you like me to lock all doors and then
  start the engine?"这类许可问句上。
* **必须承认的边界**：`Missing Required Call` 族共 32 例，修后只覆盖 3 例（修前 15 例）。
  该谓词现在的读法是**窄而真**的；它在 error 集上的 3 个命中全部成立，但覆盖很小。
  要覆盖"要求调用 X 但没调用"这一整族，需要新的机械谓词（显式工具名抽取 + 调用缺失判定），
  本轮未做。**不建议**据此认为该谓词已被"修好到可用"——它只是不再说谎。

`final_delivery_present` 在 error 集上 **24 → 0**。这 24 例的 gold subcategory 是
`Summarize Misalign`(13) / `Human-Interaction`(10) / `Query Misalign`(1)，
**没有一例的 gold reason 描述"没有交付"**；逐例检查它们的观测都带实质内容
（答案在里面，只是内容错）。所以这 24 例从来不是该谓词的真阳性，而是"恰好也没写散文"的巧合；
0 不是召回损失，是巧合被消除。修后该谓词在本语料上**不再有任何 error 命中**——
它的价值只剩"确认交付通道存在"，而不是"发现交付缺失"。这是它在本语料上的真实能力上限。

## 三、校准护栏的设计与落点

**记录（可复现、随指纹失效）**：`detector/calibration/conviction_predicates.json`
由 `scripts/calibrate_conviction_predicates.py --write` 生成，内容是
`谓词名 → clean 误报数 / error 命中数 / clean 语料指纹 / 谓词源码指纹 / 定罪词表`，
外层带 `content_digest`（记录被手改即失效）。`--verify` 重测并逐字段比对，不一致即退出非零。

**源码指纹是导出的依赖指纹，不是整文件哈希**：`source_fingerprint()` 从
`predicates._RUNNERS[pred]` 出发，遍历可达函数引用的模块级名字（含嵌套 code object，
所以 comprehension/lambda 里的调用也算），递归收集并对每个值取规范形式的哈希。
效果：改 `final_delivery_present` 读的常量会移动它的指纹；改
`_movement_requirements` 读的 pattern 只移动前者的指纹——既不会漏依赖（改动不改指纹），
也不会因为同文件无关改动而失效（那样读者会学会"无脑重跑校准"）。跨模块依赖也被跟进
（`final_delivery_present → detector.packet.delivered_result`，见测试）。

**运行期检查（四个入口，都在证据绑定点）**：

1. `detector/interventions.py::conviction_verdict_table` — 内建谓词的表项逐条过校准；
   未通过的谓词在该表里**不持有任何定罪 verdict**，所以 guard 绑定根本找不到可用行。
2. `detector/interventions.py::conviction_verdicts_for` — 读取时再过滤一次。
   这一层专门防"绕过"：调用方即使直接传入 `PREDICATE_CONVICTION_VERDICTS` 这张设计表，
   未校准的谓词仍然拿不到授权（有测试钉住）。
3. `detector/interventions.py::InterventionPlan.guard_actions` — 绑定失败时区分两种沉默：
   未校准（记录原因）vs 绑定不到行；`deployment_summary()["uncalibrated_convictions"]`
   把"这条定罪路线被吊销"如实写进报告，而不是让读者以为"它什么都没发现"。
4. `detector/adjudication.py::adjudicate` — 经 `conviction_verdicts_for` 二次核验，
   手搓 mechanical evidence 行也无法复活被吊销的授权。

**与既有门控协同而不重复**：`terminal_authority` 仍然先判 runner 是否允许定罪
（`typed_semantic` / `sandboxed_component` 恒为 `evidence_only`），校准只对
**内建 deterministic 谓词**追加"测量前提"。`typed_dsl` 程序不受影响（它的准入由变形测试门负责），
有测试钉住"legacy 定罪路线不被削弱"。

**未校准的后果是降级而非定罪**：谓词照常产出 evidence 行（可观测性保留、可指导提案），
只是不能授权终局定罪；guard 被抑制，案件回落到 judge/defense 路径。
方向是安全的：拒绝 → 走普通路径，而不是拒绝 → 错误定罪。

**fail-closed 的边界**：记录缺失/损坏/被改（`artifact_unavailable`、`artifact_malformed`、
`content_digest_mismatch`）、schema 不符、谓词缺条目、定罪词表不符、clean 误报非 0、
clean 样本数 < 50、语料指纹不符、源码指纹不符、实时重测计数不符 —— 全部拒绝授权。
**唯一不拒绝的情况**：`datasets/raw` 不存在（.gitignore 里没有它）。
此时记录自带的溯源检查（schema/digest/源码指纹/词表/已记录计数）仍全部生效，
只有"实时重测"不可用，而这一点被写成 `warnings()` / `summary()["unavailable"]`
显式报告，不静默当作"已验证"。

**成本**：`default_calibration()` 进程内缓存，首次解析约 1.2–1.4s（哈希 21MB 语料 + 693×3 谓词求值），
之后每次调用是字典查找。`pytest` 全套由 76s 增至 94s（含 39 个新测试自身开销）。

## 四、测试与验证命令

```bash
python -m pytest -q                                   # 996 passed（本轮新增 39 项）
python scripts/verify.py                              # 15 项全 PASS（996 tests, 0 failures）
python scripts/calibrate_conviction_predicates.py --verify   # VERIFY OK
python scripts/predicate_corpus_probe.py --out ...     # 复现 clean FP / error 命中
```

规则选择过程的离线探测脚本（都通过真实 `run_predicate` 求值，只读语料）：

* `probe_variants2.py` — 义务抽取/movement/许可三处规则的分项与组合 2×2；
* `probe_movement_matrix.py` — movement 的"抽取 vs 满足判定"矩阵（M1/M2/M3）；
* `probe_final_rules.py` — 最终三项规则的 A/B/C 组合；
* `probe_delivery.py`、`probe_delivery_simple.py` — 交付通道假设的测量（阈值 20/40/60/80）。

* `detector/tests/test_conviction_calibration.py`（19 项）：护栏本身。真实小语料 + 真实测量，
  不用 mock 掉判定。覆盖：通过时仍有定罪权、`clean_set_false_positive` /
  `insufficient_clean_cases` / `source_fingerprint_mismatch` / `corpus_fingerprint_mismatch` /
  `clean_fingerprint_mismatch` / `conviction_verdicts_mismatch` / `predicate_not_recorded` /
  `content_digest_mismatch` 各自拒绝、篡改记录不能买回授权、观测不到语料时报告而非静默、
  指纹跟着可达依赖走（模板级：读的常量动则动、不读的动也不动、跨模块依赖被跟进）、
  guard 抑制并在 summary 里说明原因、直接调 `adjudicate` 也拦得住、legacy 整表不能绕过、
  `typed_dsl` 路线不被削弱、随仓库发布的记录覆盖每个 convicting 谓词且源码指纹不过期。
* `detector/tests/test_predicate_obligation_extraction.py`（20 项）：谓词抽取本身。
  每个误报机制一条（回答格式要求、许可语、不定式指令、疑问/条件从句、功能词开头、
  非移动工具完成的到达、非终止的许可问句、引号内问句、带工具调用的事件、观测交付）。

**回退验证**（每条修复单独回退 → 新测试失败 → 恢复 → 通过）：

| 回退内容 | 失败测试数 |
|---|---|
| `detector/predicates.py` 回退到 HEAD | 12 failed / 8 passed（抽取测试）+ 13 failed（校准测试，语料测量真的变了） |
| 去掉 `conviction_verdicts_for` 里的校准过滤 | 1 failed（legacy 整表绕过） |
| 去掉 `conviction_verdict_table` 里的校准过滤 | 2 failed（guard 与 adjudication 两条都不再拒绝） |

## 五、未做的事

* **未修改 `datasets/` 下任何文件**；语料只读（`scripts/predicate_corpus_probe.py`、
  `scripts/calibrate_conviction_predicates.py` 都只读）。
* **未调用任何真实模型服务**，未运行 `evolution cycle`/smoke 的真实 provider 路径；
  上一节全部数字来自纯离线谓词求值与 `pytest`/`verify.py`。
* **未放开任何门限，未删除既有检查项**。校准是新增前提，不改任何既有阈值。
* **未改动被划为禁区的区域**：`evolution/engine.py`（`_run_cycle`/`_run_unit`/`_instrument_report`/`_audit`/`run_governance`）、
  `evolution/gates.py`（`localization_behavior_gate`/`behavior_gate`）、`detector/adjudication.py` 的
  `responsible_step` 选择逻辑。`detector/` 改动集中在 `predicates.py`、`interventions.py`
  的定罪权相关函数、以及新增 `conviction_calibration.py`。
* **未把"要求调用某工具但没调用"这一族的召回补回来**：`required_action_present` 修后
  在 `Missing Required Call` 上只覆盖 3/32。要提升需要新的机械谓词（显式工具名抽取 + 调用缺失），
  这属于口径扩张，未在本轮做。
* **未把校准接到 `evolution` 的准入/晋升路径**：`evolution/calibration.py`（judge confidence 可靠性表）
  与本模块仍是两个不同主体，本轮没有把二者合成一个"统一校准层"。
* **未更新计划文档 §23.10 进度表与新增完成记录章节**（其他代理正在改 `docs/` 与 `evolution/`，
  避免并发冲突）；本 README 即本轮归档。
* **未测真实 provider 上的效果**：本轮的结论限于"机械谓词在 693 例标注语料上的读数与授权边界"，
  不说明检测器整体准确率、定位准确率或自进化收益。

## 六、证据等级

**说明**：机械定罪谓词在本语料上的 clean 误报为 0（实测，可复现），
且未通过校准的谓词在运行期**无法**授权终局定罪（有可执行复现）。
**不说明**：谓词在未标注数据上的误报率、检测器整体指标、以及任何"自进化收益"。
