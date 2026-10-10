# 核心技术文档

更新：2026-10-09。本文描述当前实现及约束；历史实验、旧方案和数值统一见 [实验日志](experiment-log.md)。

> **研究方向与验收目标**：详见 [疑惑度引导的双向因果轨迹审计技术设计](uncertainty-guided-causal-audit-design-20261008.md)，简明版见 [2026-10-08 组会汇报](group-meeting-uncertainty-causal-feedback-20261008.md)。

代码重构、缺陷修复及新功能的当前实施状态统一见根目录 [实施台账](../IMPLEMENTATION_PLAN.md)。本期采用离线代码验收，不开展真实模型实验；本文件仍只描述已实现合同。

## 🧭 仓库边界

依赖方向为 `experiments → detector`、`evolution → detector`。检测器不导入进化框架；进化只能通过经过验证的 registry 干预检测。实验编排负责冻结执行计划、收集结果和评估，不能替代准入门。

| 目录 | 内容 | 约束 |
| --- | --- | --- |
| detector/ | 检测、模型调用、证据、干预、结果、批处理 | 独立运行，不依赖 evolution |
| evolution/ | 失败诊断、候选开发、四门、发布、记忆、治理 | 候选经过评估才能进入部署版本 |
| experiments/ | 计划、运行、报告 | 使用统一检测结果信封 |
| scripts/ | 验证、预检、成本、研究轮和实验工具 | 新运行产物写 artifacts |
| datasets/protocols/ | 开发划分、测量协议、暴露记录 | 执行输入，不能作为旧日志删除 |
| datasets/raw/ | 本地原始数据 | 不修改，由 integrity.json 校验 |
| 各包 tests/、根 tests/ | 单元、跨包回归、必要夹具 | 不依赖旧 reports/output |
| docs/ | 本文、研究设计和唯一实验日志 | README 是入口；代码实施进度只写根目录 IMPLEMENTATION_PLAN.md |
| artifacts/ | 可生成的运行产物 | 不入 Git，旧缓存不能直接用于新代码 |

## 🔎 检测器模型

令轨迹为 X、证据快照为 E、部署干预为 R、配置为 C。检测输出 D(X,E,R,C) 包含终态、判定权威性、责任步骤及定位权威性、测量健康、成本和执行来源。判定、定位与服务可用性是不同维度。

```mermaid
flowchart TD
    accTitle: Detector execution and adjudication
    accDescr: Validated input produces allegations, evidence review and a result envelope with separate detection and localization authority.
    input[轨迹与任务] --> packet[输入投影和事件 packet]
    registry[已验证 registry 与证据] --> packet
    packet --> judge[Judge 多票判断]
    judge --> recall[按需召回和因果升级]
    judge --> hearing[指控队列]
    recall --> hearing
    hearing --> review[Tracer 定位与 Defense 复核]
    review --> verdict[确定性裁决]
    packet --> predicate[校准机械谓词]
    predicate --> verdict
    verdict --> result[结果信封和测量健康]
```

### 输入与判错口径

`dataset.py` 分离任务输入和标签；`packet.py` 建立事件 ID、位置、工具观察、交付物及答案锚点。gold 只用于评估，不能参与判断或责任步骤映射。`semantics.py` 是各模型阶段共享的判错与定位规则。

可定罪对象包括结果错误、任务固定的交付动作/参数/次序/渠道违约、虚假完成、伪造归因与伪造验证。合法探索和未改变交付物的风格偏差不构成幻觉。观测冲突时，不得用模型世界知识挑边定罪，需要适用的独立证据快照裁决。诚实报告过程失败不自动等于结果性幻觉，例如 BFCL/041 与数据集标签有已知口径分歧。不得为通过预检改标签、种子或门限。

责任步骤是最早引入实质错误的 assistant 内容或动作。提交工具调用首次引入错误时可以是根因，仅重复已有错误时应追溯早先步骤；工具观察不是 assistant 本身的幻觉。内容步和动作步都可命中，但指标必须分列，不能将动作步命中解释为语义根因追踪成功。

### 执行与终态

`api.Detector` 编排阶段；`judge.py`、`recaller.py`、`step_tracer.py`、`defense.py` 各自校验模型输出。Judge 阳性和召回升级候选进入 hearing 队列；`adjudication.py` 校验证据引用、指控和确认步骤，计算终态。机械谓词的定罪能力受 `conviction_calibration.py` 和校准记录约束；证据型组件不能自行获得定罪权。

`interventions.py` 负责统一 schema、内容哈希、hook 和执行计划；`predicate_dsl.py`、`predicate_context.py` 定义可执行谓词及可用上下文。冻结阶段回放必须匹配证据身份，并拒绝覆盖待测干预的目标阶段。配置文件与 `Detector(config=...)` 共用 `config.validate_config`，解析有效 route 并保留默认 off 的审计配置，未知字段、非法数值与缺失声明工件直接报错；`require_credentials=False` 不读取仓库 .env。审计默认关闭；shadow 与 tracer_context 执行分支已接通，完整模块验收状态见实施台账。

Packet 的 `packet_reference_index` 统一事件和实际工具交互的引用归属，canonical position 与原始 source_step_id 分开；局部窗口 v2 覆盖 anchor/target 双端，携带 packet/内容摘要、遗漏 ID、截断原因与独立数据，后续活动不标为恢复。

Recall/Upgrade 的 live 与 frozen 解析使用同一适用证据快照；`recaller.assemble_upgraded_candidates` 从已校验候选及升级行生成按步骤排序的完整听证列表，单数项由该列表派生，回放不信任传入的派生字段。

`detector.identity` 提供规范有限 JSON 摘要、源码/声明工件摘要和 `execution_identity.v2`。Detector 的 provenance 与批次 `run_manifest.v3` 使用同一规则；清单绑定实际有效配置和逐例 record/packet/registry/工件，恢复时验证源码与逐例输入。演化 backend 复用同一输入构造规则，绑定 detector/evolution 源码、有效配置、record/packet、适用 evidence 与实际 registry/replay。内存基线核验 backend_execution_identity，并以独立副本返回。缓存 entry.v2 核验完整身份和结果摘要，目录由 registry 内容生成；缺身份的旧缓存重测，历史证据不回写。缓存中的 Mock gold 只属于测试 oracle 身份，不进入 Detector 输入。源码摘要排除测试与文档。

使用内置机械定罪的 registry 还绑定实际校准视图：读取的校准记录、corpus 核验、可选重测和所用谓词权限状态。视图返回独立副本，已解析的权限与身份共同固定；不消费机械定罪的 registry 不加载此身份。逐例缓存随视图变化失效，CycleJournal 在同一固定 arm 的校准变化后拒绝复用或重新观测。整个周期对尚未生成候选的隐式能力工件冻结仍待完成。

`backend_runtime_identity` 绑定已构造 backend 的有效配置、声明工件、Mock 参数及选定案例的实际证据快照；harness.v2、cycle_checkpoint.v3 和缺陷审计消费此身份并使用规范完整摘要。完成报告和中断检查点的恢复均经 CycleJournal.load_state 验证。cycle_checkpoint.v3保存显式authority字段，以首次消费绑定校准视图并在同一视图上扩展谓词；新候选臂、所有后续测量与恢复均核验已消费身份，未消费时为None并不加载校准。输入漂移在写入前拒绝，旧 checkpoint/harness 不作为新运行复用。Live/Caching fresh 评估沿用原 backend 的逐例证据快照，同时重跑全部阶段、不使用冻结 baseline。阶段回放策略唯一来源为detector.replay：hook reach、直接冻结失效和fresh要求分别描述；judge.post仍执行确定性后处理。full-view guard与未知hook要求全阶段重跑，边界摘要绑定实际阶段、fresh、未知hook与策略schema，使用完整SHA-256。Live/Caching构造消费完整干预条目的fresh规则；所有非None冻结信封必须含judge_result，仅含hearings或空信封在模型请求前拒绝；Detector拒绝冻结信封时把完整plan.entries传入共享mask，包括full-view action，fresh规则拒绝信封内所有已冻结模型阶段，拒绝前不发模型请求。judge.system同样不能复用下游冻结听证；直接失效仍是独立的窄规则。engine、promotion和development直接引用共享规则，旧graph/mask/fresh定义已删除。preflight的JSON source_identity检查项保存共享source_identity(detector,evolution)的完整摘要及有效配置声明的detector_config文件路径/完整工件摘要；缺声明配置为BLOCKER，截断值只用于终端显示。晋升普通协议v3与机制协议v4使用共享source_identity、规范完整batch/config/roll摘要以及artifact_digest；工件manifest.v2即使为空也必须有合法schema/闭合字段/映射及自身digest，验证后才访问所声明工件。旧协议v1/v2不作为新运行复用，加载拒绝且不回写历史字节。外层protocol_hash校验仍先执行；内层反例重封外层用于隔离manifest合同。旧backend source_digest/fingerprint/artifact_digest/manifest及alignment重复digest已删除。周期首次消费身份冻结的稳定路径已验证；独立审查发现冷未解析校准视图并发读取漂移输入时的P2，线程安全加载仍待修复。

`packet_reference_error` 统一 Judge、Recall、Tracer、Defense、引用谓词和 scoped semantic 的轨迹引用类型、存在性及范围验证；外部 snapshot citation 仍由 evidence.citation_error 验证。scoped semantic 的 supported/contradicted 必须带生产者提供的非空引用，abstained 可明确提供空列表，运行时不填造窗口引用。工具引用按实际所属 event 核验窗口范围。semantic executor 接收 packet/context 的独立副本，校验使用未被 executor 修改的投影；artifact/vocabulary 校验只有一个执行路径，证据仍不获得定罪权限。

批次 `run_summary.v3` 分别记录 requested/returned/not_started/missing，completed 只统计实际返回的非 not_started 行。工作线程登记完成结果，调度器中断后仍保留已提交案例；没有返回行的已开始案例记为 missing，不写成阴性或未开始。重复及额外案例行拒绝进入汇总。

案例 ID 由 `runtime.validate_path_id` 统一验证为正斜杠分隔的相对路径，run/attempt ID 限定为单个路径段；拒绝 drive/UNC、遍历、Windows 保留名、控制字符及尾随点或空格。`contained_path` 核验解析后的目标仍在声明根目录内，包含目录链接；批次和 measurement 同时拒绝大小写或扁平化输出路径碰撞。

实验 arm 的角色、registry selector 和可选 `detector_policy.causal_audit` 由计划解析。空 registry 显式传入，防止意外继承配置中的活动 registry；未知 arm 或与空臂冲突的 registry 参数拒绝。measurement.v1 清单核验 schema、顺序摘要、案例/排除集合和计数；选择子集保持冻结顺序并生成自己的摘要，保留父清单摘要，缺逐例标签时不继承全量正负计数。wheel 使用调用方提供的数据及 `--measurement`/`--plan` 协议路径；三个包的 help 与回环功能入口均在仓库外安装验证。批次日志写 stderr，实验 stdout 是 JSON。

| 维度 | 合同 | 解释 |
| --- | --- | --- |
| 终态 | verified_error / verified_clean / unresolved | 未决不等于 clean |
| 判定权威性 | authoritative / degraded_judge_only / invalid | 只有可信终态进入正式预测 |
| 定位权威性 | authoritative / degraded_localization_conflict | 独立于检测结论计量 |
| 未开始 | 批处理占位 not_started | 不是检测终态或阴性 |
| 版本 | detector.detection_result.v1 | 不匹配的缓存拒绝复用 |

`result.prediction_state` 同时检查 schema、authority、runtime_valid、state 与 occurrence_state，正式预测只返回 True、False 或 None。定位优先使用被定罪 hearing 的 tracer，其次是可用的 defense 确认，再次是指控步骤。tracer 比确认步骤更早本身不构成冲突；首 hearing 的镜像 tracer 与最终采纳步矛盾则降低定位权威性。旧定位文档曾混淆这些不同种类的不一致，这里按当前代码明确区分。

### 因果诊断与定位上下文

`causal_contracts.py`、`step_uncertainty.py` 和 `causal_graph.py` 生成只读风险画像、确定性候选和实际依赖投影；未知语义关系保留未知，teacher-forced PPL 返回 unsupported。`causal_audit.py` 可验证局部字面算术偏差；模型解释只记 hypothesis，局部错误不证明材料性、最早责任步或最终交付违约。

`shadow` 在基础裁决后使用剩余新增预算生成诊断。`tracer_context` 在听证前准备诊断，`context_for_hearing` 只投影当前听证所引用事件的核验记录，并绑定完整目标摘要；事件重叠仍不证明 claim 等价。Tracer 可以拒绝不相关上下文，原引用解析和 Adjudication 权限保持有效。上下文的实际证据摘要不含审计耗时，冻结 Tracer 仅在上下文身份相同后复用；配置变化或此前无上下文时重新执行。

新增上下文按 UTF-8 内容字节保守计入输入预算；repair 单独准入，耗尽时回到基础 Tracer。新增阴性复审的 Scanner、Upgrade 多票、Tracer、Defense 及 repair 都由 provider 的 `added_request_scope` 共享同一案例请求、输入、输出和 deadline 预算，transport 不重新领预算。预算持有者记录复审启动，整例重试不能再次启动可选阴性复审；后续新案例独立获得预算。内部 continuation 绑定 case、packet、配置、registry 和证据，复用同次已执行的 Judge；不借用外部冻结回放入口。高置信阴性的审计在基础证明形成后执行。预算耗尽保留已形成的基础证明与实际请求成本；策略资格回调结束立即核验预算与整例期限/取消，允许和拒绝分支共用；整个可选诊断和反馈阶段共享异常证明保留边界，fatal、整例超时和取消都保留基础证明。

阴性反馈独立开关默认为 false，仅在 tracer_context 模式、基础 clean 且原召回未运行时检查已核验异常。生产配置缺少真实校准时记录 calibration_unavailable，不启动扩展。`OfflineFeedbackPolicy` 是无凭据 API 的显式离线策略注入点，不能从 JSON 校准证书装载；它允许复审后仍必须经过完整召回、升级、独立听证和裁决。Scanner 只接收有界异常与来源，不接收 Judge 的 correct_result；仅凭高风险或模型意见不会重开。当前离线闭环不证明真实收益，完整同 claim 首次引入证书及演化消费仍在实施。

### 测量、故障与成本

`measurement.py` 统一健康分类与重测策略。error_type 和 code 都可能携带致命错误，必须同时检查。凭据、权限、请求、配额等致命故障不在同轮重试，修复环境后可在后续运行重测。暂时性服务错误允许同轮重试；显式 retryable=false 及耗尽案例或批次时限后不能重新申请完整预算。各模型阶段共用 provider.raise_if_terminal，终止错误保持分类并传播，不能变成弃权或继续 topup。ProviderError 和 AddedBudgetExceeded 属于 runtime.CaseExecutionError；并行调度传播案例失败但不取消其他案例，运行中止和程序错误仍停止调度。附加请求作用域保留整例 deadline，request_deadline 只计算请求的有效最短期限；check_request_deadline 区分整例超时与附加预算耗尽；AddedBudget.check 在本地审计同样优先传播整例 CaseDeadlineExceeded，Detector 单例边界统一输出未决并保留已完成的基础证明和请求成本。容量等待和 transport 均遵守这两个界限；附加预算耗尽保留完成的基础裁决，不取消其他案例。因果模型审计与阴性复审共用该作用域，provider_call 为请求预算的单次预留入口。

`runner.py` 统一结果落盘、尝试记录、断点复用和调度；`runtime.py` 提供运行控制、deadline、原子写入和单写者保护。整例重试共享案例 deadline。`measurement.request_execution` 汇总单次 Detector 的真实请求，`case_execution` 生成 execution.v2，保留每次整例尝试的结果、错误和请求成本；嵌套 usage scope 向父层归并一次。批次每次尝试结束即原子保存 attempt.json，程序错误和中断仍传播，汇总保留已发生的成本而不补造结果行。未知请求统计用 usage_unknown_cases 表示，旧 providers/hearings 统计仅为下界。

`measurement.cost_summary` 是运行/报告成本汇总真源。批次 measurement 的 `invocation_cost` 只统计本次实际执行（含中断请求），缓存命中不收取历史请求；`case_attempt_cost` 汇总选中案例最近一次完成测量，`run_lifetime_cost` 从持久化整例尝试 journal 累计所有实际请求，失败与重测保留且不重复。顶层 usage/elapsed 和实验报告 cost 使用 invocation 口径，独立列出历史成本；请求数未知不写零。provider 并发峰值在运行控制退出前后由同一实例显式交付。

完成案例的 `result.json` 携带 `case_commit.v1`：输入快照/摘要、run/case/attempt ID、耗时、结果摘要和完整提交摘要一次原子写入。immutable attempt 先提交，canonical 同内容替换；替换中断时旧结果与旧耗时共同保留，已完成 attempt 不能覆盖。新运行缓存要求合法提交、执行身份及实际输入摘要；当前报告和评估必须验证提交，删除提交不能降级成历史格式。整例 attempt journal 独立封存内容摘要，累计成本前验证；未知计数字段不默认补零。分离的 input.json/elapsed_seconds.json 不再生产或用于计时；计时和 usage 脚本分别共用 case_elapsed/result_usage。

路径 ID 统一拒绝 Windows 设备名，包括扩展名前空格与 COM/LPT 上标¹²³别名；不通过设备 I/O 验证非法名字。run writer lease 通过 ContextVar 在同一执行上下文中传递，bounded_map 显式复制上下文给批次 worker；独立线程/进程没有该租约。公开 record_case 取得 run 和 case 租约，独立 evaluate_run 和 baseline 报告取得 run 租约；批次到报告的同次调用保持租约，防止中间恢复运行替换结果。实验阶段在同一 run 租约内执行、读取和评估，报告从固定副本构造，缓存行本次耗时为零。evaluate_run 将当前投影任务与 case_commit.input_digest 对照，拒绝用改变后的任务给旧结果评分；gold仍由评估侧读取，不进入该任务摘要。baseline CLI 直接输出完整报告，stop 非空时退出码为2；源码与安装 wheel 的回环认证失败、缺measurement提示已验证。experiments.snapshot.run_snapshot在读取前按路径顺序取得所有选中既有run租约，采样、实际身份对照和评分共用；缺失root捕获为缺失，不创建新run。paired、status与scripts/paired_endpoint共用，旧_load_rows/_shared_registry与独立manifest读树路径已删除。snapshot只接受run_manifest.v3完整逐例execution_identity与case_commit绑定，缺身份历史输出不进入当前测量复用；实际source/record/packet不一致拒绝配对，配置/registry/evidence/replay/authority/artifacts决定是否同配置噪声。 paired --baseline-roll 指定独立同输入的基线复测；相同目录拒绝作为独立roll。snapshot将已校验result与评分行按case合并，保留阶段健康证据；experiments.reading为两入口效果读数真源，先排除prediction/state/健康状态改变的不稳定样本；缺复测或没有稳定样本结论未知。默认要求稳定样本决定性修正多于回退且单侧精确McNemar p<0.05，全量读数独立保留；演化终止报告共用metrics.mcnemar_improvement_p。

脚本usage/timing、noise roll、real path metrics、前缀和status共用run_snapshot；完整metrics与多臂前缀读取保持共同租约。缺身份旧树不作为当前测量，外来commit与叶链接越界拒绝。前缀和StageOutcome共用plan.measured_prefix，仅权威测量延长前缀；失败文件存在不算已测。raw单例elapsed/usage读者已删除，报告从同一snapshot副本计算。freeze_measured_prefix以load_measurement读取冻结顺序，由MeasurementSet.to_manifest导出；两入口共用_measurement验证，不生成缺digest/计数的伪manifest。导出从指定corpus读取所选案例标签计数，父manifest摘要与当前文件内容摘要分别保留，mini与未标注排除角色不混合；未知计数或空样本不能导出。输出在独立父目录租约内原子写入，保留输出叶entry并拒绝既有文件及悬空链接，不写入选中run根或raw corpus。

EvolutionEngine 使用独立 Context 持有 work root 租约，实例不能借用另一引擎的租约；close 在持有上下文内释放，初始化失败立即释放，finalize 为跨线程或 GC 释放提供保障。实例公开操作和 close 由同一 RLock 串行；close 拒绝新操作或重新进入，活跃操作和外层 with 全部退出后才释放租约，同线程回调关闭也不能提前交出 work root；生产入口显式关闭，实验最终报告在租约释放前写入。init/reset 同样取得 work root 租约。两个 smoke 入口通过 create_smoke_root 原子创建独立目录，已有目录直接拒绝，不清空或复用；capability smoke 的配置使用独立深拷贝，不能修改调用方生产配置。

## 🧬 自进化模型

部署版本与改进系统分离：部署状态 R_t 由 registry 表示；harness 清单记录门、能力、工具、记忆与运行编排的身份。候选在冻结的对照条件下评估，全部门通过才更新部署版本；拒绝或不可测时保留原版本。

```mermaid
flowchart TD
    accTitle: Controlled self evolution cycle
    accDescr: Audit and diagnose failures, develop candidates and publish only after four gates; other outcomes preserve deployment.
    audit[缺陷注入审计与覆盖预检] --> patrol[部署巡检与裸底座对照]
    patrol --> families[稳定检测或定位失败族]
    families --> develop[提案与有预算的开发]
    develop --> structural[STRUCTURAL]
    structural --> paired[冻结配对测量]
    paired --> gates[HARMLESSNESS → BEHAVIOR → M]
    gates --> outcome{可测且全通过}
    outcome -->|是| publish[注册表发布与快照]
    outcome -->|否| retain[保留版本并记录终态]
    publish --> next[下一轮继承与治理]
    retain --> next
```

L1 开发回放要求两臂恰好覆盖预定 target∪neighbor，每例各一行；配对指标模块统一验证覆盖和布尔 gold/prediction。覆盖缺失、非法读值和不健康运行归为未测，delta 留空，版本不能冻结。已知 gold step 时，定位回退只计基线原本精确命中而候选失去命中的案例；wrong→wrong 保持未改善。

普通单位提前检查、替换集合的 coverage 和路线反馈共用 target_opportunity：定位使用可纠正定位数，联合使用检测机会与定位机会之和。替换按 localization 方向的显式策略执行，并以实际替换集合报告机会。联合收益门保留“至少一个目标改善、两个目标均不回退”的既有语义。

指标模块 target_outcome 统一读取 detection/localization/joint/resolution 的收益与损伤，并携带目标类型和 delta_basis。周期入口按实际 benefit set 为每个候选成员产生 outcome；即使行为门未通过，读值仍保留。缺少相应指标归为未测；joint_broken、检测损伤及健康退化归为损伤，路线反馈只记录可测且未改善、未损伤的结果。定位机会来自基线可纠正的 annotated positive，不能用检测 FN/FP 机会替代。

路线状态按 JSONL 追加顺序归约：resolution 清除对应 mechanism/route 的当前观察期，后续 observation 从零累计，历史字节保留。相同 cycle/route 事件重放不重复累计，冲突内容拒绝；解决记录写入幂等。当前仍需接入候选、measurement 和上下文身份，不能把跨 cycle 次数当成已证明独立的测量证据。

### 模块职责

| 模块组 | 文件 | 职责 |
| --- | --- | --- |
| 循环 | engine.py、config.py、splits.py | 阶段顺序、预算、划分、审计、巡检、准入、治理 |
| 失败建模 | worksheet.py、mechanisms.py、route_failures.py | 稳定失败族、机制机会、定位错/缺、路径缺口 |
| 开发 | proposer.py、development.py、capability_planning.py | L0/L1 检查、修订、预算和反馈 |
| 研究入口 | research.py、scripts/run_research_round.py | 冻结机制研究、重复验证，不替代生产准入 |
| 晋升 | promotion.py、promotion_alignment.py、candidate_versions.py | 统一候选、协议冻结、自哈希、对照身份、晋升终态 |
| 门和指标 | gates.py、metrics.py、calibration.py | 结构、保护、行为、收益、定位、稳定性 |
| 后端 | backends.py、sandbox.py、capability_surface.py | 真实/模拟评估、缓存身份、受限执行 |
| 版本恢复 | registry.py、checkpoint.py、ledger.py | 部署、快照、断点 journal、追加哈希链 |
| 经验能力 | harness.py、memory.py、archive.py、gaps.py | 系统身份、经验、组件、开放缺口，不越过门判定 |
| 验证 | meta_eval.py、metamorphic.py、smoke.py | 归因、变形检查、离线闭环 |

### 核心不变量

1. STRUCTURAL 验证候选合同、执行边界、失败族和目标类型；结构失败不消耗真实候选测量。
2. HARMLESSNESS 检查保护集及覆盖，BEHAVIOR 要求实际触发且符合声明，M 衡量冻结收益集的决定性修复。收益不能抵消保护损伤。
3. detection、localization、joint 分别计量。定位分母包含漏检的 gold 阳性；只改标签不能伪装为定位修复。
4. unresolved、基础设施故障、覆盖不足、无可修机会分开记录。不可测不应写成机制被证伪，不进入永久负反馈。
5. 对照和候选使用冻结案例与角色，身份包含源码、配置、候选、证据和协议；不能通过换目录、裁门或改角色重新抽签。
6. 后续轮次读取新部署版本。缓存和断点核对身份，历史摘要不能作为新代码的结果复用。
7. 注册表输入与读取不能共享可变嵌套对象；发布拥有独立快照。同周期回滚选最后可用记录，同秒发布不能覆盖证据。
8. Ledger 采用哈希链和跨进程写锁，引擎单运行者锁是并发边界。ActiveRegistry 多文件不是数据库事务；active/state 不一致时拒绝加载，不静默接受。

### 审查结论与剩余边界

审查沿输入投影、API 编排、裁决/结果、测量、开发/门控、晋升、注册表/恢复展开，以完整离线测试及新增回归验证执行约束。本次未进行真实模型测量，不宣称形式化证明。

| 发现 | 风险 | 修复和证据 |
| --- | --- | --- |
| 致命错误只读优先字段 | code 中致命错误被遮蔽 | 两字段均识别，参数化信封测试 |
| 同轮重试包含 fatal_provider | 无效凭据重复消耗预算 | 禁止同轮重试，保留跨运行重测 |
| 注册表浅复制 | 外部修改 body/snapshots 绕过发布 | 输入、结果、状态深复制；内存/磁盘哈希测试 |
| 快照精确到秒且同周期回滚取最早 | 覆盖快照或恢复错误版本 | 唯一快照名；最后可用记录；固定时钟回归 |
| 验证指纹遗漏 experiments | 实验编排变更未进入验证源码身份 | 纳入源码清单；修改实验模块须改变哈希的回归 |
| 工具/测试依赖历史产物目录 | 清理文档破坏执行 | 驱动迁 scripts，划分迁 protocols，回放迁 fixtures |
| 旧文档与代码不同步 | 定位冲突口径、现状与计划混淆 | 当前合同集中本文，旧内容标为历史 |

静态结构检查覆盖 66 个顶层 Python 模块（detector 28、evolution 33、experiments 5），没有 detector 反向依赖 evolution 的导入。engine（5353 行）、promotion（2325 行）和 interventions（2247 行）仍是主要维护热点。

一次冗余梳理移除了没有生产调用方的代码：RepairUnit 声明与编译器、统计评估器、注册表单笔事务、注册表自愈、单元漂移记录、`event_source_mapping` 与 `resolution_opportunity`。它们此前只被各自的测试引用，`promotion.py` 中通向其中的分支在任何候选上都无法到达。保留的 `retire()` 反向依赖检查是实时的：它仍会拒绝单独退役被现役 Guard 消费的证据项。

engine、backends、promotion、interventions 仍承担较多职责，后续物理拆包需逐接口迁移。本次保持公开 API 和冻结身份合同，先统一职责边界及输出入口。真实模型重复波动、定位标注歧义、机械谓词误报风险仍需实测与语义审计，不能用测试数量替代。

## 🧪 待研究：疑惑度定位如何反哺幻觉检测（2026-10-08）

**现有代码事实**：`api.py::_judge_negative_needs_recall` 根据多票一致性和置信度决定部分阴性是否需要 Recall；`Detector._detect` 的 `negative_needs_recall` 还接受现有部署干预与适用证据快照触发；一旦进入 Recall，仍需 `run_causal_upgrade`、独立听证的 Tracer/Defense 和 `adjudicate`。阳性 Tracer 已支持输入单项指控，但尚无逐步困惑度或后续反事实推演的标准输入。

`causal_contracts.py`、`step_uncertainty.py`、`causal_graph.py` 和 `causal_audit.py` 实现只读诊断及 shadow 审计。Detector 在非 off 模式于基础终态生成后输出 `proof.causal_diagnostics`：StepRiskProfile、Top-K 与 mandatory 候选、DependencyGraph、CausalProjection 和 CausalAudit。默认 off 不计算诊断；默认 deterministic_only 不增加 provider 调用。

风险排序优先采用 calculator 字面算术的局部偏差、相同 entity/attribute/unit/time 的显式字段冲突候选，以及未核验内容。机械缺失动作保留轨迹级 null locus。字段冲突是 hypothesis，工具观察不自动归责；缺失/未知 role 不记为零风险，teacher-forced PPL 明确 unsupported。算术核验仅支持可精确表示的字面四则运算；数值转换前限制十进制数量级，运算中限制分子/分母位长，超限保持未知。不执行工具代码，不把局部结果当作任务级违规证明。

依赖图确认 packet 内同一交互的 call/result 结构，以及显式 source_ref、绑定字段和值匹配且来源在前的声明消费。该验证证明记录声明的字段复制关系，不证明来源事实或材料性。没有来源的自然语言、仅文字相似、未解析/歧义引用和同次参数引用后续观察均不能取得因果验证。投影纳入远距离祖先、真实后缀和交付锚点；节点/边/深度/字符预算或依赖缺失使 closure_state 保持 unknown，记录遗漏并返回独立内容副本。

`run_causal_audit` 重算局部算术证书及机械缺失动作。supported/refuted 仅描述证书的明确作用范围；材料性和因果角色未证明时保持 unknown，缺失动作不分摊给事件。局部算术修正不声称工具已重放或状态已改变，审计没有定罪权。开启 model_hypothesis 时通过现有 provider 执行一次有预算的慢审；输出要求闭合字段、枚举、可见 packet 引用及长度上限，合法模型解释仍只记为 hypothesis，不能升级确定性证书。

`runtime.AddedBudget` 是每例新增预算的唯一持有者，Detector 和整例重试共用；嵌套调用不能重领或更换策略。诊断启动后时间额度与现有 case/run deadline 取更短者。请求前累计预留请求数、UTF-8 输入内容/声明选项的字节数和输出上限；没有 tokenizer 时这是保守估计，不承诺精确 token hard cap。慢审关闭内部 transport retry，实际请求/usage 仍由统一 usage_scope 记录，并在审计及整例 execution 中分别呈现范围，汇总只读取整例计数。

可恢复 provider/合同失败或预算不足只改变诊断 health，保留基础终态和必需阶段健康。致命 provider 失败继续返回原有 invalid/unresolved 信封并附 `base_detection_result` 和停止原因；KeyboardInterrupt/RunStopped 等传播原异常，整例 attempt 记录保留已生成的基础证明和实际成本。RunStopped 属于运行控制中止，继承 BaseException，provider 不转换为普通 provider 错误，backend 的案例失败处理也不能吞掉它。等待 provider 容量时耗尽单例 deadline 则抛 CaseDeadlineExceeded，由 provider 转为不可同轮重试的 case_deadline_exceeded，保留单例失败与全运行取消的区别。

**待接通**：阳性听证的同 claim Tracer 上下文及受校准策略约束的阴性复审。当前 tracer_context 配置产生审计诊断，尚不改变 Tracer 输入。阴性复审仍须经过 Recall/Upgrade/Defense/Adjudication。异常分数不能定罪，模型反事实不能充当工具回放；强一致阴性的默认路径保持现有行为。

**双任务反馈**：`evolution/worksheet.py`、`mechanisms.py` 和 `proposer.py` 可在开发侧消费不含部署标签泄漏的因果诊断，将漏检、误报、首次步骤晚判、证据缺失、已纠正及多错误歧义区分处理；保留原始 L0/L1、四门准入、双指标配对与不可测状态。现有 `detector/provider.py` 没有对原 Agent 步骤计算 teacher-forced PPL 的接口，首期“疑惑度”应明确写作语义风险代理。

**实施顺序与验收**：先旁路影子审计（不改变判决）；然后配对验证 Tracer 定位收益；再独立验证阴性复核对 FN/FP 的影响；最后研究结构化反馈的跨轮自进化净收益。所有新增 prompt、scorer、依赖投影及后端工件须纳入缓存身份和冻结回放一致性校验。完整风险、分阶段文件改动、消融与预算见[技术优化设计](uncertainty-guided-causal-audit-design-20261008.md)。

## 🛠️ 运行和维护

Python ≥3.10，运行时标准库，测试依赖 pytest。默认配置为 detector/configs/autodl.json，凭据从本地 .env 或进程环境读取，不写入日志。默认开发划分不是独立终审，历史暴露记录必须保留。

```bash
python -m pytest -q
python scripts/verify.py --scope code_only
python -m detector --help
python -m evolution --help
python -m experiments --help
python scripts/run_research_round.py --help
python scripts/cost_model_693.py --json artifacts/cost_model_693.json
```

verify.py 默认执行 code_only（合成输入与临时夹具），可显式选择 platform（回环 HTTP、worker、锁、沙箱）、local_data（本地数据和证据完整性）或 all。所有测试禁公网，Python 子进程继承同一回环边界；code_only 拒绝访问原始数据及历史 artifacts。验证报告按源码摘要及独立运行 ID 写入 artifacts/code_quality，不覆盖历史报告；源码 AST 检查覆盖重复定义、包依赖边界和循环依赖。成本工具从实验日志的命名 JSON 块读取冻结样本并保留 original_source；旧样本有选择偏差，不代表完整语料的最新成本。

新增实验先冻结输入、源码/配置身份、角色、预算和判据；结束后将日期、分母、结果、失败、中断、更正和成本追加到唯一实验日志。运行缓存不作为永久文档提交。算法文件按职责命名，日期放运行 ID；夹具只服务具体测试断言，不存放任意实验备份。
