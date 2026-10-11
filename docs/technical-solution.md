# 核心技术文档

更新：2026-10-11。本文描述当前实现及约束；历史实验、旧方案和数值统一见 [实验日志](experiment-log.md)。

> **研究方向与验收目标**：详见 [疑惑度引导的双向因果轨迹审计技术设计](uncertainty-guided-causal-audit-design-20261008.md)，简明版见 [2026-10-08 组会汇报](../doc/group-meetings/group-meeting-uncertainty-causal-feedback-20261008.md)。

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

Judge 的阳性 root_cause.quote 和 Tracer 的 quote 必须是非空字符串；Scanner 的 quote 可省略，但提供时也须通过核验。`packet.packet_quote_error` 将引用文本逐通道匹配至对应步骤：事件引用覆盖该事件内容及工具交互，交互引用仅覆盖指定工具的名称、参数和观察，不能借用同事件中未引用的工具，也不能跨字段拼接。quote 采用去除首尾空白后的原文子串匹配，展示标签不是证据。live 与 frozen parser 消费同一规则；错误输出沿既有 repair/弃权流程处理，冻结合同错误拒绝回放。此核验只证明文本出处，不证明偏差或因果关系成立。

`replay.validate_frozen_stages` 独立拥有输入信封的嵌套快照，集中调用各阶段解析器并从有效升级行重建派生候选；API只负责阶段编排。LiveDetectorBackend生成冻结信封时也复制完整嵌套结果，信封、规范化结果与原基线之间的修改不能相互传播。旧私有API验证入口已删除，调用方直接消费replay合同。

`hearing.run_hearing` 执行单次 Tracer/Defense 听证；API 保留诊断准备、Judge 翻案及 Recall 重开顺序。`replay.match_frozen_hearing` 只在来源、候选身份和完整目标同时匹配时选择已验证听证，Tracer 另核对上下文摘要；复用的结果和状态独立复制。听证队列是阶段结果的唯一来源。无论首听证来自Judge还是Recall，顶层proof、阶段状态与provider镜像均取首听证；实际责任步及决定性事实取被采纳的定罪听证，后续Recall不继承被推翻Judge的答案字段。决定性事实记录其candidate_id，Judge听证为None。首镜像与采纳步的分歧继续进入localization_sources/conflict，不改变检测裁决权威。无听证不调用Tracer/Defense，也不生成镜像。

冻结阶段的ok必须为布尔值，并与state=ok一致。失败阶段的parsed必须为空或缺失，不能将成功裁决混入失败信封；合法失败状态及错误信息可以复用，避免通过重抽模型请求掩盖失败。此校验与所有成功阶段的原解析器一起执行。

`replay.build_replay_inputs` 生成 `detector.replay_inputs.v1`，绑定每个模型阶段的源码、packet、有效路由、基础 prompt及共享hook政策指定的阶段干预条目；Scanner、Upgrade、Defense另绑定适用快照，Tracer绑定审计策略和声明的scorer工件，启用阴性复审时Judge也绑定复审策略。

Detector在请求前取得此身份并写入provenance，backend将其复制进冻结信封。新运行拒绝缺身份、字段不闭合、摘要损坏或Judge输入漂移的信封；下游输入漂移清除对应冻结结果，Scanner或Upgrade漂移同时清除依赖它的Recall听证。Judge只读轨迹，单独冻结Judge可以配合新快照。

当前活动干预的mask在移除输入失效结果前核验，防止失效选择清掉待拒绝的阶段；不影响模型输入的judge.post仍执行后处理。听证完整目标和实际因果上下文继续独立核验。纯规范化函数未传 `expected_inputs` 时只检查阶段合同，可用于只读诊断，不能授权新Detector运行复用。完整回放包与独立复审尚未验收，状态见实施台账。

Judge保留每张无效票的结构化失败。全部无效票为同一失败类型时，阶段保留该类型（包括contract_error及provider的具体超时类型）；不足quorum或混合失败类型报告no_quorum。一次合同repair与有限vote_topup继续受原预算约束，fatal与取消继续传播。Defense的初次票与补票调用同一汇总规则，证词与权限不变。归档审计只读检查历史记录的原始步号、结构类别与计数，不将旧模型输出重新包装成当前合同的验证结果。

`api.Detector` 编排阶段；`judge.py`、`recaller.py`、`step_tracer.py`、`defense.py` 各自校验模型输出。Judge 阳性和召回升级候选进入 hearing 队列；`adjudication.py` 校验证据引用、指控和确认步骤，计算终态。内置机械谓词的定罪能力受 `conviction_calibration.py` 和校准记录约束；证据型组件不能自行获得定罪权。 已验证typed DSL按程序/guard的声明合同确定权限，模型typed semantic仍无定罪权。

终态交付谓词仅把精确success状态、JSON对象success=true、字面success或完整固定完成banner识别为成功；失败/unknown、false、unsuccessful及普通查询字符串不触发成功交付义务，过深或非法JSON保持不适用。动作谓词仍会消费Judge合同的动作文本；离线反例显示其可能给真实任务补出不存在的启动义务，登记为台账B40，正式入口影响及源头修复待完成，未证明最终定罪。

`interventions.py` 负责统一 schema、内容哈希、hook 和执行计划；`predicate_dsl.py`、`predicate_context.py` 定义可执行谓词及可用上下文。冻结阶段回放必须匹配证据身份，并拒绝覆盖待测干预的目标阶段。配置文件与 `Detector(config=...)` 共用 `config.validate_config`，解析有效 route 并保留默认 off 的审计配置，未知字段、非法数值与缺失声明工件直接报错；`require_credentials=False` 不读取仓库 .env。审计默认关闭；shadow 与 tracer_context 执行分支已接通，完整模块验收状态见实施台账。

Packet 的 `packet_reference_index` 统一事件和实际工具交互的引用归属，canonical position 与原始 source_step_id 分开；局部窗口 v2 覆盖 anchor/target 双端，携带 packet/内容摘要、遗漏 ID、截断原因与独立数据，后续活动不标为恢复。

Recall/Upgrade 的 live 与 frozen 解析使用同一适用证据快照；`recaller.assemble_upgraded_candidates` 从已校验候选及升级行生成按步骤排序的完整听证列表，单数项由该列表派生，回放不信任传入的派生字段。

`detector.identity` 提供规范有限 JSON 摘要、源码/声明工件摘要和 `execution_identity.v2`。Detector 的 provenance 与批次 `run_manifest.v3` 使用同一规则；清单绑定实际有效配置和逐例 record/packet/registry/工件，恢复时验证源码与逐例输入。演化 backend 复用同一输入构造规则，绑定 detector/evolution 源码、有效配置、record/packet、适用 evidence 与实际 registry/replay。内存基线核验 backend_execution_identity，并以独立副本返回。缓存 entry.v2 核验完整身份和结果摘要，目录由 registry 内容生成；缺身份的旧缓存重测，历史证据不回写。缓存中的 Mock gold 只属于测试 oracle 身份，不进入 Detector 输入。源码摘要排除测试与文档。

使用内置机械定罪的 registry 还绑定实际校准视图：读取的校准记录、corpus 核验、可选重测和所用谓词权限状态。视图返回独立副本，已解析的权限与身份共同固定；不消费机械定罪的 registry 不加载此身份。逐例缓存随视图变化失效，CycleJournal 在同一固定 arm 的校准变化后拒绝复用或重新观测。整个周期对尚未生成候选的隐式能力工件冻结仍待完成。

`backend_runtime_identity` 绑定已构造 backend 的有效配置、声明工件、Mock 参数及选定案例的实际证据快照；harness.v2、cycle_checkpoint.v3 和缺陷审计消费此身份并使用规范完整摘要。完成报告和中断检查点的恢复均经 CycleJournal.load_state 验证。cycle_checkpoint.v3保存显式authority字段，以首次消费绑定校准视图并在同一视图上扩展谓词；新候选臂、所有后续测量与恢复均核验已消费身份，未消费时为None并不加载校准。输入漂移在写入前拒绝，旧 checkpoint/harness 不作为新运行复用。Live/Caching fresh 评估沿用原 backend 的逐例证据快照，同时重跑全部阶段、不使用冻结 baseline。阶段回放策略唯一来源为detector.replay：hook reach、直接冻结失效和fresh要求分别描述；judge.post仍执行确定性后处理。full-view guard与未知hook要求全阶段重跑，边界摘要绑定实际阶段、fresh、未知hook与策略schema，使用完整SHA-256。Live/Caching构造消费完整干预条目的fresh规则；所有非None冻结信封必须含judge_result，仅含hearings或空信封在模型请求前拒绝；Detector拒绝冻结信封时把完整plan.entries传入共享mask，包括full-view action，fresh规则拒绝信封内所有已冻结模型阶段，拒绝前不发模型请求。judge.system同样不能复用下游冻结听证；直接失效仍是独立的窄规则。engine、promotion和development直接引用共享规则，旧graph/mask/fresh定义已删除。preflight的JSON source_identity检查项保存共享source_identity(detector,evolution)的完整摘要及有效配置声明的detector_config文件路径/完整工件摘要；缺声明配置为BLOCKER，截断值只用于终端显示。晋升普通协议v3与机制协议v4使用共享source_identity、规范完整batch/config/roll摘要以及artifact_digest；工件manifest.v2即使为空也必须有合法schema/闭合字段/映射及自身digest，验证后才访问所声明工件。冻结写者和加载读者共用非空字符串路径标签约束；写者在任何所声明artifact IO之前验证全部标签，空路径不会被解释为当前目录或写成不能加载的协议。旧协议v1/v2不作为新运行复用，加载拒绝且不回写历史字节。外层protocol_hash校验仍先执行；内层反例重封外层用于隔离manifest合同。旧backend source_digest/fingerprint/artifact_digest/manifest及alignment重复digest已删除。默认校准factory以缓存锁保证同key单一对象，ConvictionCalibration以实例锁串行解析并发布status/inputs；解析完成后同一视图不再重读文件。CycleJournal绑定时用同一次取得的视图核验已消费身份和新请求，观察落盘前再次校验；未消费校准时不读视图。真实冷加载竞争及两项可替换factory条件反例已正式失败后修，相关61通过，a85edf6固定独立14+相关42通过。空工件路径和明确offline的backend合同夹具经9efeb41固定回归132+独立11通过，固定811662f完整code_only1568及platform57通过；local_data未运行。这些离线结果不证明真实测量收益。

`packet_reference_error` 统一 Judge、Recall、Tracer、Defense、引用谓词和 scoped semantic 的轨迹引用类型、存在性及范围验证；外部 snapshot citation 仍由 evidence.citation_error 验证。scoped semantic 的 supported/contradicted 必须带生产者提供的非空引用，abstained 可明确提供空列表，运行时不填造窗口引用。工具引用按实际所属 event 核验窗口范围。semantic executor 接收 packet/context 的独立副本，校验使用未被 executor 修改的投影；artifact/vocabulary 校验只有一个执行路径，证据仍不获得定罪权限。

**证据与权限边界**：DSL 正则在可终止子进程中执行，单次预算 0.5 秒、同一程序共享 1 秒，并受 case deadline 和 run cancellation 约束。资源耗尽输出 `not_applicable` 和 `execution_status=resource_exhausted`，不生成事实、不触发 Recall、不授权定罪；整例期限及取消继续传播。决定性引用逐一检查相关冲突说明，snapshot 的 resolution 来源绑定对应规范实体/属性组；packet 优先保留实质 answer 工具交付，closing status 保留为无实质交付时的锚点。deterministic 及无 runner 的公开裁决输入只可绑定内置定罪词汇并通过校准，typed DSL 保留显式程序权限。交付识别修改使旧校准源码指纹失效，历史记录保持原始字节，运行时拒绝复用其权限。DSL规范化保留 `when.packet_features`，`when.facts` 编译入现有 typed conditions；任意上下文字段扫描路径已删除，所有条件共享字段白名单、hook权限及合并条件数量上限；规则字段闭合，旧派生条件及未知字段明确拒绝，避免静默丢失约束。B15–B21 当前为工作区修复待审查；完整验收以实施台账为准。

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

依赖图的 `declared_literal_consumption` 边表示结构化字段通过明确的工具交互 `source_ref` 声明采用了可重算算术观察的数值。观察和字段必须数值相同，字段不能额外声明单位或时间；来源必须唯一且在消费之前。后续字段传播复用 `declared_field_consumption`，继续核对实体、属性、单位、时间和数值。`dependency_graph.v2` 的复制边另记录实体/属性/单位/时间绑定、值与精确来源/目标字段路径，同事件的不同字段不能通过节点相交拼成一条消费链。新边与原边共用节点/边预算、遗漏及闭包规则；引用错误、来源歧义、后见观察或未核验标量保持未知。正确的新观察不会替换后续声明仍引用的旧来源。该图证据从Detector诊断入口可达，并已接入当前听证的声明消费路径证书；它不证明任务材料性、语义首次引入或反事实效应，也不授予定罪/定位权限。

`shadow` 在基础裁决后使用剩余新增预算生成诊断。`tracer_context` 在听证前准备诊断。context.v4只接收可唯一绑定的局部算术结果：packet引用限定实际交互，quote与delivered_result必须是相同有界数值，correct_result与重算表达式一致，且观察确有偏差；多工具事件必须有明确交互引用。缺值、自然语言值、歧义或已一致的结果回退基础Tracer；Recall hearing不借用Judge的correct_result。局部claim证书绑定packet和完整目标摘要，候选仅保留匹配交互的证书。Tracer在provider前从packet重算证书并核验实际结果输入，重新封装的错证书/角色/权限被拒绝。same_local_result绑定被引用算术结果及从该实际来源出发的字段复制链；`observed_lineage` 按节点、精确字段路径和字段绑定追踪，独立记录观察到的assistant采用点、交付锚点以及后续同表达式的正确观察。首个观察采用点不等于首次因果引入；正确观察不证明旧值已被替换。结构路径证书的材料性、语义首次引入和纠正效果保持unknown，证书没有定罪或定位权限。

`task_certificate.v1` 仅核验完整指令为 Calculate/Compute/Evaluate 有界字面算术、assistant交付规范result/value记录且没有额外指令角色的单一结果任务：实际正确交付反驳当前数值材料性；错误值须匹配绑定claim并处于完整交付链才核验材料性。首次引入只指规范结果声明，要求其前缀无未核验文本或工具动作，并核验该声明的精确字段来源；多错误、额外任务或不支持的交付合同保留未知。`verified_symbolic` 修复从首个声明沿同一字段路径传播到交付，只在固定声明复制链接和单一算术义务假设下成立，修复值是精确有限十进制；直接复用原错误观察的独立交付分支不能据此认定已修复。它不执行工具或验证状态变化，不能替代裁决或通用语义因果定位。

Tracer在provider前从当前packet和策略重算任务证书与完整路径，拒绝重新封装的错边、字段路径、角色/权限或未知上下文字段；上下文生成也核验审计策略身份。字段遍历按规范字典键顺序进行，列表顺序保留，以防仅键顺序变化使图和上下文身份漂移。

`detector.hearing`统一Judge/Recall目标构造与证明投影，回放比较完整目标字段（含任务contracts、confidence和外部引用），缺字段或字段变化不复用旧听证。Recall目标只接收候选与允许任务合同，candidate中注入的Judge答案、confidence或判定锚点不会进入目标；输入嵌套容器独立复制。 Scanner、Upgrade和Defense直接调用唯一provider_call，初次和repair均传递完整snapshot_digest；无snapshot时传空tag。Fake调用方使用相同的provider公共签名。上下文的实际证据摘要不含审计耗时，冻结 Tracer 仅在上下文身份相同后复用；配置变化或此前无上下文时重新执行。

新增上下文按 UTF-8 内容字节保守计入输入预算；repair 单独准入，耗尽时回到基础 Tracer。新增阴性复审的 Scanner、Upgrade 多票、Tracer、Defense 及 repair 都由 provider 的 `added_request_scope` 共享同一案例请求、输入、输出和 deadline 预算，transport 不重新领预算。预算持有者记录复审启动，整例重试不能再次启动可选阴性复审；后续新案例独立获得预算。内部 continuation 绑定 case、packet、配置、registry 和证据，复用同次已执行的 Judge；不借用外部冻结回放入口。高置信阴性的审计在基础证明形成后执行。预算耗尽保留已形成的基础证明与实际请求成本；策略资格回调结束立即核验预算与整例期限/取消，允许和拒绝分支共用；整个可选诊断和反馈阶段共享异常证明保留边界，fatal、整例超时和取消都保留基础证明。

阴性反馈独立开关默认为 false，仅在 tracer_context 模式、基础 clean 且原召回未运行时检查已核验异常。生产配置缺少真实校准时记录 calibration_unavailable，不启动扩展。`OfflineFeedbackPolicy` 是无凭据 API 的显式离线策略注入点，不能从 JSON 校准证书装载；它允许复审后仍必须经过完整召回、升级、独立听证和裁决。Scanner 只接收有界异常与来源，不接收 Judge 的 correct_result；仅凭高风险或模型意见不会重开。当前离线闭环不证明真实收益，完整同 claim 首次引入证书及演化消费仍在实施。

### 测量、故障与成本

`measurement.py` 统一健康分类与重测策略。error_type 和 code 都可能携带致命错误，必须同时检查。凭据、权限、请求、配额等致命故障不在同轮重试，修复环境后可在后续运行重测。暂时性服务错误允许同轮重试；显式 retryable=false 及耗尽案例或批次时限后不能重新申请完整预算。各模型阶段共用 provider.raise_if_terminal，终止错误保持分类并传播，不能变成弃权或继续 topup。ProviderError 和 AddedBudgetExceeded 属于 runtime.CaseExecutionError；并行调度传播案例失败但不取消其他案例，运行中止和程序错误仍停止调度。附加请求作用域保留整例 deadline，request_deadline 只计算请求的有效最短期限；check_request_deadline 区分整例超时与附加预算耗尽；AddedBudget.check 在本地审计同样优先传播整例 CaseDeadlineExceeded，Detector 单例边界统一输出未决并保留已完成的基础证明和请求成本。容量等待和 transport 均遵守这两个界限；附加预算耗尽保留完成的基础裁决，不取消其他案例。因果模型审计与阴性复审共用该作用域，provider_call 为请求预算的单次预留入口。

`runner.py` 统一结果落盘、尝试记录、断点复用和调度；`runtime.py` 提供运行控制、deadline、原子写入和单写者保护。整例重试共享案例 deadline。`measurement.request_execution` 汇总单次 Detector 的真实请求，`case_execution` 生成 execution.v2，保留每次整例尝试的结果、错误和请求成本；嵌套 usage scope 向父层归并一次。批次每次尝试结束即原子保存 attempt.json，程序错误和中断仍传播，汇总保留已发生的成本而不补造结果行。未知请求统计用 usage_unknown_cases 表示，旧 providers/hearings 统计仅为下界。

`measurement.cost_summary` 是运行/报告成本汇总真源。批次 measurement 的 `invocation_cost` 只统计本次实际执行（含中断请求），缓存命中不收取历史请求；`case_attempt_cost` 汇总选中案例最近一次完成测量，`run_lifetime_cost` 从持久化整例尝试 journal 累计所有实际请求，失败与重测保留且不重复。顶层 usage/elapsed 和实验报告 cost 使用 invocation 口径，独立列出历史成本；请求数未知不写零。provider 并发峰值在运行控制退出前后由同一实例显式交付。

完成案例的 `result.json` 携带 `case_commit.v1`：输入快照/摘要、run/case/attempt ID、耗时、结果摘要和完整提交摘要一次原子写入。immutable attempt 先提交，canonical 同内容替换；替换中断时旧结果与旧耗时共同保留，已完成 attempt 不能覆盖。新运行缓存要求合法提交、执行身份及实际输入摘要；当前报告和评估必须验证提交，删除提交不能降级成历史格式。整例 attempt journal 独立封存内容摘要，累计成本前验证；未知计数字段不默认补零。分离的 input.json/elapsed_seconds.json 不再生产或用于计时；计时和 usage 脚本分别共用 case_elapsed/result_usage。

单案例计时使用 `python -m detector detect --record RECORD --case-id CASE --config CONFIG --output-dir UNIQUE_OUTPUT` 的 `prediction.json`：`execution` 保存每次请求耗时、排队时间、用量、失败状态及案例总耗时。运行使用配置中的实际预算；不会为诊断隐式扩大预算。固定 SmolAgents/019 计时脚本已删除：它在阶段导入后替换 provider，无法采集既有调用绑定。2026-09-18 的测量数值和相关预算依据保留，旧脚本可从 Git `a6318f4` 读取。

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

路线状态按 JSONL 追加顺序归约：resolution 清除对应 mechanism/route 的当前观察期，后续独立测量重新累计，历史字节保留。`route_observations.build_route_measurement` 在门控丢弃原始 result 前，从实际配对收益集合投影案例、输入摘要、注册表、候选/伙伴、原始调用身份和冻结阶段；输入与预测、配置、两臂案例或候选注册表不一致、目标阶段冻结、来源缺失时不生成有效测量信封。冻结范围复用 detector.replay.HOOK_POLICIES，未另定义 hook 政策。

信封以 evolution.route_measurement.v1 保存，拥有独立快照。`measurement_for_candidate` 核对信封确实属于报告中的候选或伙伴；正式周期的两条成员报告路径传递同一单位信封。route_failure_record 记录其来源；reducer 和能力规划按当前 backend_runtime_identity 校验适用性，配置含实际离线反馈策略。没有当前范围、无有效信封、部分或全部候选调用来源复用时仅增加 diagnostic_observations，不认定路线能力不足。两次独立候选测量达到原阈值才排除路线；可共享配对控制基线，但不能复用候选调用来源。解决记录也不会让旧来源变成新测量。同周期可保留多份独立测量；相同事件重放幂等，内容冲突拒绝。数字 revisions_observed 只作诊断元数据，不能单独赋予路线排除权限。旧 JSONL 缺来源的记录继续可读，原始字节不改，不补造身份。

`measurement.request_execution` 在每次实际调用完成时生成 `measurement_id`，输入或时钟读数相同仍得到不同标识；缓存、归档和复制保留原标识。`case_execution` 从有序原始尝试标识派生整例标识，重新汇总相同尝试不会制造独立测量；任一尝试缺少有效标识时整例标识为 `None`。这些标识只描述测量来源，不赋予结果权威，也不证明阶段未使用冻结回放。路线来源校验使用 measurement_sources 验证原始尝试与整例派生摘要，拒绝缺失、重复或篡改的尝试；标识本身不证明语义因果关系。 实际Mock调用也产生调用标识，并以measurement_kind=synthetic及backend=mock隔离其来源；有效packet才能生成输入身份。Mock使用的gold仅在评估器/Fake oracle边界，以摘要绑定，不作为真实测量或真实后端的路线排除证据。

### 模块职责

| 模块组 | 文件 | 职责 |
| --- | --- | --- |
| 循环 | engine.py、cycle_preparation.py、config.py、splits.py | 阶段顺序、预算、划分、审计、巡检、准入、治理 |
| 失败建模 | worksheet.py、causal_failures.py、mechanisms.py、route_observations.py、route_failures.py | 稳定失败族、机制机会、定位错/缺、路径缺口 |
| 开发 | proposer.py、development.py、development_adapter.py、capability_planning.py | L0/L1 检查、修订、预算和反馈 |
| 研究入口 | research.py、scripts/run_research_round.py | 冻结机制研究、重复验证，不替代生产准入 |
| 晋升 | promotion.py、promotion_alignment.py、candidate_versions.py | 统一候选、协议冻结、自哈希、对照身份、晋升终态 |
| 门和指标 | gates.py、metrics.py、calibration.py | 结构、保护、行为、收益、定位、稳定性 |
| 后端 | backends.py、sandbox.py、capability_surface.py | 真实/模拟评估、缓存身份、受限执行 |
| 版本恢复 | registry.py、checkpoint.py、ledger.py | 部署、快照、断点 journal、追加哈希链 |
| 经验能力 | harness.py、memory.py、archive.py、gaps.py | 系统身份、经验、组件、开放缺口，不越过门判定 |
| 验证 | meta_eval.py、metamorphic.py、smoke.py | 归因、变形检查、离线闭环 |

记忆写入由memory._append_record统一负责：在模块线程锁和现有进程writer租约内读取、校验逻辑键/JSON类型、比对完整内容并追加。同键同内容返回实际持久化副本，同键内容变化拒绝；未知结构、键类型不符或重复历史不被修补或扩写。新记录和recall输出拥有独立JSON内容，非有限值拒绝；写入前后中断传播原异常，重放按实际落盘判断，不返回未落盘的内容。record_episode_correction在同一写入边界内验证原episode摘要，独立追加复核记录，允许后续有新证据的重新分类；原episode字节不变。write_recall只写目录内JSON快照，不覆盖JSONL历史或越界。

CycleJournal的save与权威绑定共用独立JSON发布：原子替换成功后才更新内存；写入异常后核对实际落盘是否为旧提交或本次提交，无法确认则拒绝状态读取、保存和新观察，要求重新打开。state返回独立快照，合同、起始注册表和更新输入不保留外部可变引用；非有限JSON拒绝。Engine.run_cycle将恢复准备和执行纳入同一清理作用域；原始取消、fatal或业务异常保持对象身份传播，checkpoint与ledger中断记录分别尝试，记录故障以cycle_recording_errors中的(stage, exception)元组附在原异常对象上，兼容Python 3.10。周期完成由统一入口将evolution.cycle_completion.v1记录与status=completed一起原子写入checkpoint，记录绑定合同摘要、部署摘要、完整报告/摘要、cycle_completed事件及记录摘要；正常、预检阻断、审计冻结共用。提交完成后checkpoint不可改写或新增观察，恢复不更新时间/重提案/重测/重部署，仅校验并补齐report文件和带幂等键的完成事件。报告或事件的完整内容/JSON类型冲突、重复完成事件、记录摘要/语义或合同漂移拒绝且不覆盖。未提交完成而既有终态输出也拒绝；待补输出时部署必须仍对应提交，输出完整的历史周期可复用历史报告。full_self_evolution通过同一run_cycle恢复完成周期，不直接信任散落报告或跳过检查点核验。该完成记录不证明此前部署、全部记忆与其他周期事件已经形成一个原子提交。

EvolutionEngine周期历史由私有_cycle_reports拥有，公开cycle_reports在操作锁内返回独立深副本，没有替换入口；run_cycle返回、内部历史和checkpoint记录互不共享可变内容。run_experiment只聚合本次请求中实际读取的提交报告，完成计数、提前停止、停止原因和验证集评估都使用同一范围；其他周期不混入零/前缀请求，也不压制本次范围的验证。零周期返回空聚合且验证集status=not_run。公开快照的嵌套修改、追加或清空不能污染内部历史及恢复结果。

### 核心不变量

1. STRUCTURAL 验证候选合同、执行边界、失败族和目标类型；结构失败不消耗真实候选测量。
2. HARMLESSNESS 检查保护集及覆盖，BEHAVIOR 要求实际触发且符合声明，M 衡量冻结收益集的决定性修复。收益不能抵消保护损伤。
3. detection、localization、joint 分别计量。定位分母包含漏检的 gold 阳性；只改标签不能伪装为定位修复。
4. unresolved、基础设施故障、覆盖不足、无可修机会分开记录。不可测不应写成机制被证伪，不进入永久负反馈。
5. 对照和候选使用冻结案例与角色，身份包含源码、配置、候选、证据和协议；不能通过换目录、裁门或改角色重新抽签。
6. 后续轮次读取新部署版本。缓存和断点核对身份，历史摘要不能作为新代码的结果复用。
7. 注册表输入与读取不能共享可变嵌套对象；发布拥有独立快照。同周期回滚选最后可用记录，同秒发布不能覆盖证据。 活动存储的 `active.json` 是唯一 `detector.registry_commit.v1` 原子提交，绑定部署registry、治理state、提交ID与整体摘要；快照先写入，提交前失败清理未引用的新快照。替换后异常重新读取实际提交并传播原异常，内存与重开保持一致。每次变更持有线程锁及进程租约，并拒绝陈旧提交ID。Detector与预检共用加载器验证完整提交，只提取部署内容；公开独立registry与快照仍使用原部署格式。旧分离active/state目录拒绝作为新活动存储，不迁移或改写历史字节。
8. 晋升以注册表提交中的publication记录为终态真源：部署、版本、完整通过报告和事件载荷一起提交，外部文件及账本是可恢复输出。提交前失败不部署；提交后中断传播原异常，重开按已提交终态补齐输出，不重新测量或再次部署。Ledger采用哈希链、跨进程写锁及可选幂等键；同键同内容返回既有记录，同键冲突拒绝，历史行不改写。

两种晋升协议都使用同一发布与恢复入口。candidate_status在终态生成处确定，公共promote入口直接返回同一报告，不在持久化后追加字段。恢复身份绑定候选、协议、配置、门集合、输出目录和源码；恢复同时核对候选身份、四门通过、版本部署摘要及事件/报告一致性。已有输出冲突保留原字节并拒绝；没有提交记录的既有终态文件不能作为部署授权。清空部署或回滚保留历史publication记录，恢复历史输出不会重新部署历史版本。Detector加载提交后只接收纯部署registry，不接收治理状态或晋升评分。

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

**定位与复审合同**：tracer_context 已向当前听证的 Tracer 传入有界局部核验证据，并校验上下文身份；完整同 claim 证书仍在实施。阴性复审的当前权限与生产校准限制见本文因果诊断合同，不把拟议能力写成已实现行为。

**开发侧因果失败记录**：Tracer结果保存独立复制的完整causal_context及摘要。`evolution/causal_failures.py` 从当前无标签轨迹和实际backend策略重算听证证书，核对目标、上下文摘要、有效Tracer/Defense、终局定位权限和Tracer步与结果步的一致性。漏检、误报、运行不可测、证据缺失、多指控歧义分别记录；只有单个成立听证中的材料性、首个规范结果声明及符号修复均核验通过，才能分类同claim的early/late/aligned。责任步与首次位置比较使用packet.position，source_step_id只作为原始来源标识保留。

`localization_repair_viability` 接受结构不可能，或证书独立指出与注释一致的首次声明；证书与注释不一致、重封伪证书、输入/策略漂移、定位冲突及多错误均保持needs_review。开发侧标签只判断失败类型或核对机会，不作为目标答案回传。Engine的preview/cycle将实际backend策略传给家族筛选和诊断；`cycle_preparation.prepare_patrol`统一采样与确定性补样；`prepare_families`统一诊断、机制分组、正常邻居、可行路线与能力规划，engine的preview/cycle消费同一结果。准备职责只调用入口提供的测量回调，不写账本或注册表；preview保留报告写入，正式cycle保留journal与准入边界。补样两臂使用相同case集合与已有seed公式，初始没有家族才补样。

诊断的显式proposer投影只保留失败类型、证据状态、作用范围和修复操作，不含case、步号、packet/target摘要或正确数值。真实member_summaries进入LLM提案prompt，机制以同claim偏差及repair_localization_source分组，能力规划复用已有定位输入与能力需求。Hybrid定位提案允许有同claim首次引入证据时前移，排除无证据、另一错误、已纠正和未知修复效应。正常邻居与M15/M16完整生命周期集成继续按实施台账验收；现有L0/L1与四门不因该证书放宽。

**开发修订边界**：`development_adapter.make_development_drafter` 持有家族快照，每次调用提供独立的家族、机制、父程序及诊断反馈；返回候选、联合组和notes也独立复制。为当前机制指定唯一intervention_id，并在后续规范化/冻结前移除旧content_hash。engine实际开发入口使用该适配器，反馈delta投影只有一个定义。

临时反馈通过try/finally恢复原有快照，provider异常、取消及反馈setter失败也执行恢复。LLM反馈setter独立拥有传入对象；Hybrid提供当前反馈快照的读取与写入合同，修订结束保留先前正式反馈及其拒绝状态。父程序投影保留type/hook/trigger/body/claim的完整可执行语义，排除case provenance与旧hash；父版本关系仍由CandidateDeveloper保存。

L1逐例反证只从已生成配对delta.rows及已有target/normal回退、改善和触发集合投影，保留别名、实际两臂prediction及原评分结论，不重复定义收益或回退规则。金标、正确答案和原case身份不进入该投影。不可测修订标记not_evaluable及unmeasurable，不成为永久拒绝指纹；L0未运行或失败与L1实际测量状态区分。正式L1覆盖、健康、邻居保护和四门维持原规则。

现有 `detector/provider.py` 没有对原 Agent 步骤计算teacher-forced PPL的接口，当前疑惑度表示语义风险代理。

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

engine smoke 的共享合成配置明确选择 `proposer.mode=offline`，模块级 autouse fixture 禁止构造 LLMProposer；显式 Scripted/Recording 注入对象继续由 Engine 使用，生产默认配置保持原语义。固定 7d04878 独立复核完整模块 72 项及 4 项注入/配置隔离检查通过，29 次默认工厂调用全部返回 OfflineProposer。最近全量验证对应固定 811662f；其后实现提交使用各自相关回归，当前文档收尾没有重新运行全套，也没有新增实验结果。

新增实验先冻结输入、源码/配置身份、角色、预算和判据；结束后将日期、分母、结果、失败、中断、更正和成本追加到唯一实验日志。运行缓存不作为永久文档提交。算法文件按职责命名，日期放运行 ID；夹具只服务具体测试断言，不存放任意实验备份。
