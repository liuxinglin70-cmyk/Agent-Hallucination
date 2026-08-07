# 自进化框架因果经验闭环：代码实施契约

## 0. 文档用途与执行权威

本文档是本轮自进化框架优化的唯一实施规范，用于启动新的代码执行对话。执行模型必须先完整读取：

1. .agents/skills/pristine/SKILL.md
2. 本文档
3. 本文档“代码所有权”列出的现有实现

执行模型不得根据旧实验日志、历史设计草稿或临时观察改变本文档的目标、非目标和验收边界。实验日志只提供证据，不拥有架构规则。

本文档使用以下规范词：

- **必须**：实现和验收不可省略。
- **禁止**：触发即视为偏离任务。
- **应当**：没有代码证据支持时不得偏离。
- **可以**：不改变边界时允许采用的实现选择。

实施遵循 First-Time Principle：

- 从失败信息丢失的根因修复，不调整阈值掩盖问题。
- 每条规则只有一个所有者，不保留长期双轨实现。
- 代码命名解释“是什么”，注释只解释不可见的“为什么”。
- 不保留 feature flag、兼容分支散落、注释代码、备份文件或待清理实现。
- 旧格式兼容只能存在于明确的 I/O 迁移边界；内存中的核心模型只有一种当前格式。
- 每个阶段完成后代码都必须是可运行、可测试、可提交的完整状态。

---

## 1. 任务目标

本任务的目标是打通以下真实闭环：

~~~text
当前案例检测
  -> 确定性失败归因
  -> 结构化失败案例
  -> 检索之前案例的同机制经验
  -> 在现有一次反思调用中生成 skill
  -> 编译并执行现有 ActionSpec
  -> 记录本地执行结果
  -> 更新 genome 与持久化经验
  -> 下一案例使用更新后的 genome 和经验
~~~

完成后，框架必须能够回答：

1. 当前案例为什么需要进化。
2. 失败发生在哪个真实产物、字段和下游消费边上。
3. 该断裂由哪个模块拥有，哪些动作在当前运行时合法。
4. 之前相同机制的案例尝试过什么，产生了什么本地执行结果。
5. 当前提案是否真正 compiled、injected、applied，并移动了目标 observable。
6. 下一案例是否确实读取了前一案例保存的经验。

本任务不以“产生更多文本规则”为完成标准。只有进入现有可执行链路并留下因果执行证据的规则才是 skill。

---

## 2. 明确非目标

以下内容不属于本任务，执行模型禁止顺手实施：

1. 不调整 skill 准入阈值、fitness 阈值、置信度阈值或候选数量阈值。
2. 不通过放宽 structural gate、replay gate 或 acceptance gate 提高产出率。
3. 不新增跨案例迁移验证，不用迁移效果定义 skill 是否有效。
4. 不增加 provider、detector、reflection、critic 或 validation 模型调用。
5. 不增加离线聚类任务，不要求先收集一批案例再归纳。
6. 第一版不引入 embedding、向量数据库、学习式相似度或相似度阈值。
7. 不围绕“十个单案例并发实验”设计共享状态；该实验只验证独立流程。
8. 不在本任务中重写 detector、proof court 或完整 L1/L2 action vocabulary。
9. 不以单批 accepted skill 数量作为代码验收阈值。
10. 不硬编码 API 地址、模型密钥或实验凭据；继续从环境配置读取，日志不得输出密钥。
11. 不修改与 src/sehd_v6 自进化链路无关的模块。
12. 不推送远端、创建 PR 或部署，除非用户另行授权。

如果正确实现需要突破任一非目标，执行模型必须停止当前阶段，给出文件、函数、运行结果和为什么无法在现有边界内完成的证据，不得自行扩大任务。

---

## 3. 当前能力边界

### 3.1 已有且必须保留的能力

现有代码已经提供：

- src/sehd_v6/core/bccse/evolution_runner.py：逐案例在线运行、checkpoint、resume、source replay、contrast replay、genome 更新。
- src/sehd_v6/core/bccse/failure_classifier.py：确定性 layer、artifact、reason 和 owner 补充归因。
- src/sehd_v6/core/bccse/action_spec.py：把模块动作编译为 route、output object、field、operation、postcondition 和 observable。
- src/sehd_v6/core/bccse/module_charter.py：模块权限、动作 opcode、合法字段和 observable 的权威来源。
- src/sehd_v6/core/bccse/reflection_engine.py：现有反思调用和候选 schema。
- src/sehd_v6/core/htc_tes/type_aware_fitness.py：类型经验统计。
- src/sehd_v6/evolution/runner.py：公开 evolution API，已经支持 initial L1 genome、initial L2 policy genome 和 experience memory。
- src/sehd_v6/evolution/__main__.py：CLI 入口。

以下运行语义必须保持：

- 当前 genome 和 policy genome 进入下一条案例。
- evaluator-only 字段不会泄漏到 detector/provider。
- runtime-invalid 不参与 mutation 和 fitness。
- proposal 先作用于 candidate genome，不直接污染 working champion。
- skill 仍由现有 source replay 和本地 observable movement 判断。
- checkpoint 和 resume 保持幂等。

### 3.2 当前根因

当前 skill 提取的主要信息损失发生在归因与反思之间：

- 归因输出能说明 artifact/reason/owner，但没有表示完整的生产者—字段—消费者断裂。
- 反思输入包含截断后的宽泛原始轨迹，关键事件可能被无关内容挤出。
- runtime observation、detection context 和 experience summary 重复描述相同事实，但没有统一对象身份。
- 经验记忆偏向类型和结果统计，不能表达历史动作为什么有效或无效。
- 当前 comparison case 只包含 state/artifact/reason 等粗粒度信息，不能支持真实因果归纳。

本任务必须修复这个信息结构，不能把问题改写为“提高 acceptance rate”。

---

## 4. 目标架构与代码所有权

### 4.1 目标数据流

~~~text
DetectionResult
  -> FailureAttribution
  -> FailureEpisodeBuilder
       -> current FailureEpisode
       -> prompt-safe causal projection
       -> mechanism signature
  -> EvolutionExperienceMemory.retrieve(previous compatible episodes)
  -> ReflectionEngine(existing single call)
  -> existing candidate parser
  -> existing ActionSpec compiler
  -> existing structural and replay gates
  -> FailureAttempt outcome
  -> EvolutionExperienceMemory.upsert
  -> checkpoint
~~~

### 4.2 唯一所有权

| 规则或数据 | 唯一所有者 | 禁止事项 |
| --- | --- | --- |
| layer/artifact/reason/owner | failure_classifier.py 和现有最终 attribution contract | builder 不得重新猜 owner |
| 候选、证明、court 原始产物 | detector result | memory 不得创建另一份真值 |
| 因果切片、failure edge、机制签名 | 新 failure_episode.py | runner/reflection 不得各自重建 |
| 模块权限和合法动作 | module_charter.py + action_spec.py | LLM 不得创造新 opcode 或字段 |
| 历史机制经验持久化与检索 | 新 EvolutionExperienceMemory | contrast registry 不再为生成提供历史 |
| prompt 编排和候选解析 | reflection_engine.py | prompt 不得再次接收完整未筛选轨迹 |
| 流程编排 | evolution_runner.py | runner 不得拥有领域投影逻辑 |
| 类型统计 | TypeExperienceMemory | 不得复制完整因果案例 |
| skill 接受决定 | 现有 gate 和 source replay | 历史案例不得成为新 validation gate |

### 4.3 现有同名类型的处理

src/sehd_v6/core/bccse/types.py 当前的 FailureEpisode 明确只表示 paired replay 输入，字段也只有 error type 和匿名引用。把在线因果闭环字段直接追加到这个类型会制造一个具有两套不相干语义的对象。

Phase 1 必须在同一个提交中完成以下重命名：

- 将现有 paired replay 类型重命名为 ReplayEpisode；
- 更新 proof evaluator、导出和所有测试调用点；
- 不保留 FailureEpisode 别名、弃用 wrapper 或新旧构造分支；
- 将 FailureEpisode 作为本文档定义的因果失败案例唯一名称。

ReplayEpisode 与 FailureEpisode 的职责不同，不是两条竞争的进化工作流：前者只是 paired replay 的匿名评估输入，后者是在线 skill 产生和经验记忆的权威结构。

新的 FailureEpisode 允许使用清晰的嵌套值类型，例如：

- FailureEdge
- CausalSlice
- FailureAttempt
- HistoricalEpisodeReference

这些类型必须服务于 FailureEpisode，不能形成第二条工作流。

---

## 5. FailureEpisode 当前格式

### 5.1 Schema

目标 schema version：

~~~text
sehd.failure_episode.v1
~~~

建议的规范化结构：

~~~text
FailureEpisode
├── schema_version
├── episode_id
├── record_key
├── detection
│   ├── state
│   ├── evolution_direction
│   └── trigger_source
├── attribution
│   ├── layer
│   ├── artifact
│   ├── reason_code
│   ├── owner_module
│   └── fallback_owner_module
├── causal_slice
│   ├── candidate
│   │   ├── candidate_id
│   │   ├── candidate_kind
│   │   ├── origin_refs
│   │   └── dependency_refs
│   ├── obligation
│   │   ├── required_fields
│   │   └── required_evidence_refs
│   ├── provider_output
│   │   ├── output_object
│   │   ├── present_fields
│   │   ├── missing_fields
│   │   └── malformed_fields
│   ├── downstream_consumption
│   │   ├── consumer
│   │   ├── qualification_state
│   │   ├── certificate_state
│   │   ├── quarantine_state
│   │   ├── coverage_state
│   │   └── court_effect
│   ├── failure_edge
│   │   ├── producer_route
│   │   ├── output_object
│   │   ├── field
│   │   ├── consumer
│   │   ├── observed
│   │   └── required
│   ├── evidence_refs
│   └── completeness
├── action_envelope
│   ├── legal_modules
│   ├── legal_operations
│   ├── legal_output_fields
│   └── legal_observables
└── attempts
    └── FailureAttempt[]
~~~

### 5.2 字段不变量

- schema_version 必须显式存在。
- episode_id 是规范化 observation 的稳定哈希。
- record_key 只用于内部审计、幂等和 artifact 关联，不进入反思 prompt。
- evolution_direction 使用现有 controller 已经确定的方向，不重新推导 gold。
- attribution 必须来自现有权威归因结果。
- missing_fields、malformed_fields 和所有引用必须排序并去重。
- failure_edge 只能指向现有 ActionSpec 能影响的第一个可修复断裂。
- 无法建立完整引用链时，completeness=partial；禁止补造事实。
- action_envelope 必须由 module charter 和 ActionSpec registry 确定性生成。
- attempts 不进入 episode_id；观察身份不能随尝试结果变化。
- evaluator/gold 原始字段不得出现在 schema 的 prompt projection 中。

### 5.3 稳定身份

episode_id 建议按以下方式产生：

~~~text
sha256(canonical_json(
  schema_version
  + record_key
  + detection_without_evaluator_fields
  + attribution
  + causal_slice
  + action_envelope
))
~~~

规范化要求：

- map key 使用稳定顺序；
- 集合转为排序后的 list；
- 保留具有流程语义的 event 顺序；
- 不包含时间戳、随机数、provider request id 或 attempts；
- 同一输入重复运行必须得到相同哈希。

mechanism_signature 与 episode_id 分离。前者用于跨案例检索，禁止包含 record key、case id、自然语言解释和时间信息。

### 5.4 FailureAttempt

每次提案的 outcome 使用结构化记录：

~~~text
FailureAttempt
├── attempt_id
├── proposal_signature
├── operation
├── target_module
├── action_spec
├── structural_result
├── compiled
├── injected
├── applied
├── observable_delta
├── downstream_court_delta
├── no_effect_reason
└── decision
~~~

attempt_id 必须由 episode_id + proposal_signature + logical_attempt_index 稳定产生。checkpoint resume 使用 upsert，不允许重复 append。

no_effect_reason 只能根据现有执行证据分类。第一版至少区分：

- condition_not_matched
- provider_did_not_apply_action
- output_changed_then_removed_or_quarantined
- provider_output_changed_but_downstream_invariant
- expected_delta_observed
- unresolved_no_effect

证据不足时必须使用 unresolved_no_effect，不能根据最终 state 猜测中间原因。

---

## 6. FailureEpisodeBuilder

### 6.1 文件和 API

新增：

~~~text
src/sehd_v6/core/bccse/failure_episode.py
~~~

建议公开边界：

~~~python
build_failure_episode(...)
build_mechanism_signature(...)
project_failure_episode_for_prompt(...)
validate_failure_episode(...)
~~~

实际参数应复用现有 typed result，不得把整个 runner local namespace 传入 builder。

### 6.2 确定性构建步骤

1. 接收 authoritative detection result、现有 attribution、现有 evolution direction。
2. 根据 attribution 中的 artifact/reason/owner 选择当前责任链。
3. 选择责任候选，优先顺序为：
   - attribution 或 court 明确引用的 candidate；
   - unresolved、quarantined 或 verified_error terminal edge 引用的 candidate；
   - provider artifact 明确包含的 candidate；
   - 最早进入当前 artifact 失败链的 candidate。
4. 按稳定对象 ID 串联：
   - candidate census；
   - candidate qualification；
   - evidence obligation；
   - certificate；
   - quarantine/coverage；
   - proof court。
5. 对比 required 和 observed：
   - 必需字段缺失；
   - 字段存在但为空；
   - 字段格式错误；
   - provider 输出存在但未被下游消费；
   - 下游消费发生但没有移动目标 court edge。
6. 在当前 attribution 对应的流程拓扑内选择第一个可由合法 ActionSpec 修复的断裂作为 failure_edge。
7. 从真实对象提取最小 evidence slice。每个 snippet 必须由 event ref 或 artifact ref 定位。
8. 从 module charter 和 ActionSpec registry 生成 action envelope。
9. 校验引用、所有权、字段和合法动作。
10. 生成 episode id、机制签名和 prompt-safe projection。

### 6.3 多候选和不完整轨迹

- 多个候选同时失败时，builder 必须按 court/attribution 的责任引用选择主候选。
- 其他候选只作为 bounded supporting refs，不得把多个失败混成一个 edge。
- 无法通过 ID 连接 provider output 和下游消费时，episode 仍可产生，但标记 partial。
- partial 不是 runtime-invalid，也不是自动拒绝 skill 的新 gate。
- 如果连 artifact、owner 或可修复 edge 都无法确定，记录结构化 unaddressable 原因并沿用现有 no-actionable 行为；禁止把它路由到随意模块。

### 6.4 反思投影

prompt projection 只包含：

- detection state 和 evolution direction；
- attribution；
- 当前 candidate 的结构化身份；
- obligation 与 observed 的差异；
- primary failure edge；
- 最小 evidence refs/snippets；
- legal action envelope；
- 历史参考的结构化摘要。

必须删除：

- record key、case id、dataset/source identity；
- gold label、hallucination step、evaluator explanation；
- provider 原始 response metadata；
- 与 primary failure edge 无关的整段 trajectory；
- 任意可由模型误认为指令的未界定历史文本。

### 6.5 消除重复投影

reflection_engine.build_runtime_observation() 和 runner 中现有 provider artifact projection 与新 builder 重叠。

最终状态必须是：

- 结构化 artifact/court 投影只在 failure_episode.py 定义；
- 迁移阶段需要旧函数名时，只能委托给新实现；
- Phase 3 完成后，reflection 直接消费 FailureEpisode，删除无意义 wrapper；
- 不允许 runner、reflection 和 builder 各自维护字段列表。

---

## 7. 在线跨轨迹经验

### 7.1 不采用离线聚类

真实运行按单案例顺序更新。第一条案例没有历史，后续案例只读取当时已经完成的历史。

在线行为：

~~~text
build current episode
  -> retrieve prior compatible references, excluding current episode
  -> upsert current observation for crash-safe checkpoint
  -> generate/execute proposal
  -> upsert attempt outcome
  -> next case
~~~

这里的“结构索引”只是一种确定性查找，不是预先聚类，也不要求批量数据。

### 7.2 持久化模型

新增一个清晰的组合根：

~~~text
EvolutionExperienceMemory
├── schema_version
├── type_experience_memory
└── failure_mechanisms
~~~

- TypeExperienceMemory 继续负责类型统计。
- failure_mechanisms 负责结构化因果经验。
- 两者写入现有 experience memory artifact，避免第二个 CLI 配置。
- 旧 artifact 的读取由一个边界迁移函数完成；内存中立即转为当前 schema。
- 当前版本只写新 schema，不在核心流程传播 old/new 判断。

目标 schema version：

~~~text
sehd.evolution_experience_memory.v1
~~~

### 7.3 结构索引键

第一版机制签名：

~~~text
(
  layer,
  artifact,
  reason_code,
  evolution_direction,
  candidate_kind,
  failure_edge.consumer,
  normalized_missing_or_malformed_fields
)
~~~

规则：

- 所有值来自封闭或规范化字段。
- 字段集合排序去重。
- 不包含 case identity、自然语言 rationale、模型名称或时间。
- 签名算法属于 failure_episode.py，memory 只消费，不复制实现。

### 7.4 检索顺序

检索按离散结构逐级回退：

1. 完整机制签名一致；
2. layer + artifact + direction + consumer 一致；
3. layer + artifact + direction 一致；
4. 无兼容历史。

禁止：

- 学习式相似度；
- embedding；
- 相似度阈值；
- 通过 hallucination type/module 映射代替 artifact 机制；
- 因“历史结果好”而放宽当前 ActionSpec 合法性。

### 7.5 持久化内容和容量

不缓存完整原始轨迹。每个结构索引项保存：

- observation count；
- outcome counts；
- 最近一条本地执行有效代表；
- 最近一条 executed-no-effect 代表；
- 最近一条 structural reject 代表；
- 最近一条 no-proposal/unaddressable 代表。

代表选择是确定性的固定 outcome slot，不使用分数阈值。相同 slot 的替换顺序：

1. complete 优先于 partial；
2. outcome 证据字段更完整者优先；
3. 相同时使用逻辑处理序号；
4. 再相同时使用 episode id 作为稳定 tie-break。

每个 slot 保存结构化 causal slice、ActionSpec 和 outcome，不保存完整 trajectory。持久化代表可以保存不透明 episode id，但不得保存 raw record key、case id 或 dataset identity。

完整 episode 审计记录属于当前 run 的 evolution_result；跨运行 memory 只保存代表和累计计数。不要同时持久化一份无界 episode 列表和一份可漂移索引。

### 7.6 供反思使用的历史参考

每次最多向现有反思调用提供：

- 一条本地有效代表；
- 一条 no-effect 或 rejected 代表。

如果没有本地有效代表，提供最相关的 no-effect/rejected 和 no-proposal 代表。检索为空时保持当前案例独立运行。

历史投影必须包含：

- 与当前案例共同的结构事实；
- 当前案例独有的事实；
- 历史案例独有的事实；
- 历史 ActionSpec；
- 历史本地执行 outcome 或明确 no-effect 原因。

历史案例只帮助产生 proposal，不参与当前 skill 的 acceptance。

---

## 8. Reflection 集成

### 8.1 调用预算

接入前后，对同一个案例和相同重试配置：

- detector 调用次数不增加；
- reflection 调用次数不增加；
- critic 调用次数不增加；
- source replay 次数不增加；
- contrast replay 次数不增加；
- 不新增迁移或独立 validation 调用。

如果实现导致调用次数增加，该阶段直接失败。

### 8.2 输入职责

新的 reflection input 必须由以下内容组成：

1. 当前 episode prompt projection；
2. 最多两个历史参考；
3. 当前 genome 中可能重复或冲突的 skill；
4. module charter 生成的合法动作范围；
5. 现有输出 JSON schema。

模型只负责：

- 从可观察结构事实中形成 condition；
- 在合法 action envelope 内选择 operation/field；
- 给出短的因果 rationale。

模型不负责：

- 重做 failure attribution；
- 猜测 owner；
- 猜测未出现的 candidate 或字段；
- 创造 opcode；
- 判断 gold；
- 重新阅读完整轨迹并自行关联对象；
- 决定当前 skill 是否通过 gate。

### 8.3 输出兼容

第一版保持现有 reflection candidate schema 和 ActionSpec 编译器接口。不得同时引入新的候选 schema、修改动作 vocabulary 和重写 gate。

如果当前 candidate schema 无法表达 action envelope 中已经存在的合法字段，应当在本阶段一次性修正 schema 所有者，并删除旧字段；不得增加 optional legacy 字段长期兼容。

### 8.4 现有 contrast registry

现有 registry 最终只负责已有的 contrast replay 安全保障，建议在代码中使用表达该职责的名称，例如 contrast_replay_registry。

Phase 3 完成后必须删除其作为 reflection generation context 的用途。历史生成参考只来自 EvolutionExperienceMemory。

---

## 9. Outcome 回写与幂等

### 9.1 更新时机

每个案例按以下顺序持久化：

1. detection 和 attribution 完成；
2. build episode；
3. 检索历史，明确排除当前 episode id；
4. 将当前 observation upsert 到 checkpoint；
5. 执行现有 reflection、compile 和 replay；
6. 生成 FailureAttempt；
7. upsert attempt outcome；
8. 更新代表 slot 和统计；
9. 写入最终 checkpoint/result；
10. 进入下一案例。

### 9.2 Resume

必须为以下中断点编写集成测试：

- observation 已写入、reflection 尚未调用；
- proposal 已生成、source replay 尚未完成；
- attempt outcome 已写入、case finalization 尚未完成。

恢复后必须满足：

- 同一 episode 不重复计数；
- 同一 attempt 不重复 append；
- 历史检索不把当前未完成 episode 当作前序案例；
- provider 调用计数符合现有 resume contract；
- 最终 memory 与无中断运行规范化相同。

---

## 10. 跨运行继承

### 10.1 问题

同一 run_online_evolution 内已经逐案例继承当前 genome 和 policy genome。跨完整运行时，API 与 CLI 的输入边界不对称，容易只继承 L1 或只加载 memory，形成不完整闭环。

### 10.2 目标

新增完整状态清单：

~~~text
evolution_state_manifest.json
├── schema_version
├── provider_rule_genome
│   ├── path
│   └── sha256
├── adjudication_policy_genome
│   ├── path
│   └── sha256
├── experience_memory
│   ├── path
│   └── sha256
├── source_run_id
└── lineage
~~~

目标 schema：

~~~text
sehd.evolution_state.v1
~~~

公开入口新增：

~~~text
--inherit-from <completed_output_dir>
~~~

### 10.3 语义

- --resume-from：恢复同一输入队列，继续中断运行，必须校验输入/config hash。
- --inherit-from：开始新的案例流，继承上一轮完整 genome、policy 和 memory。

必须：

- 原子加载三个 artifact；
- 校验清单路径和 hash；
- 拒绝显式参数与 manifest 冲突；
- 保持现有 resume 语义不变；
- CLI 和 public API 能力一致。

不得：

- 用 resume 模拟新数据继承；
- 缺失 policy 时静默使用空 policy；
- 缺失 memory 时静默开始空历史；
- 写出 manifest 后继续允许未绑定 hash 的 artifact 替换。

---

## 11. 文件级实施范围

### 11.1 必须新增

~~~text
src/sehd_v6/core/bccse/failure_episode.py
tests/test_failure_episode.py
tests/test_evolution_experience_memory.py
~~~

如组合 memory 需要独立所有者，可新增：

~~~text
src/sehd_v6/core/bccse/evolution_experience_memory.py
~~~

不要把新的因果经验实现继续塞进 type_aware_fitness.py。该模块保留类型统计职责。

### 11.2 必须修改

~~~text
src/sehd_v6/core/bccse/types.py
src/sehd_v6/core/bccse/evolution_runner.py
src/sehd_v6/core/bccse/reflection_engine.py
src/sehd_v6/core/htc_tes/type_aware_fitness.py
src/sehd_v6/evolution/runner.py
src/sehd_v6/evolution/__main__.py
tests/test_evolution_hardening.py
tests/test_evolution_standalone.py
~~~

根据现有导出方式同步修改 src/sehd_v6/core/bccse/__init__.py。

### 11.3 原则上不修改

~~~text
src/sehd_v6/core/paca_tcl/
src/sehd_v6/detector/
实验数据原文件
skill 准入和 fitness 阈值配置
~~~

如果 builder 缺少必要的真实 artifact ref，只允许对 detector 输出 contract 做最小、明确、可测试的结构补全；不得改变 detector 判决逻辑。发生这种情况时，必须先报告缺失字段、生产模块和下游用途。

---

## 12. 分阶段实施与 Git 边界

每个阶段必须独立完成测试和 review 后提交。前一阶段未通过，不得开始下一阶段。

### Phase 0：固定行为基线

工作：

- 运行当前完整测试并记录实际测试数，不在文档中假设固定数量。
- 从已有实验 artifact 建立代表性 fixture：
  - 正确稳定；
  - 错误 owner/field；
  - invalid mutation；
  - compiled 但 no-effect；
  - accepted local movement；
  - L2 policy movement。
- 固定 detector result、attribution、ActionSpec、调用次数和 label firewall 行为。

验收：

- 生产代码行为零变化。
- fixture 可在无外部 API 的测试中稳定重放。
- 当前测试全通过。

提交：

~~~text
test(evolution): freeze causal evolution baselines
~~~

### Phase 1：建立唯一 FailureEpisode

工作：

- 扩展现有类型。
- 实现 builder、validator、canonical serializer、prompt projection。
- runner 生成 episode 并写入审计结果，但不改变 reflection proposal。
- 将重复 artifact/runtime projection 委托到新唯一实现。

验收：

- 所有 runtime-valid 且 should_evolve 的 fixture 产生合法 episode，或产生明确的 unaddressable 结构原因。
- 同一输入重复运行得到相同 episode id。
- 所有 evidence ref 可解析；无法解析时为 partial。
- prompt projection 无 label/identity 泄漏。
- detector state、genome decision、gate decision 和调用次数与基线一致。
- ReplayEpisode 的 proof evaluator 行为与基线一致。

提交：

~~~text
refactor(evolution): model causal failure episodes
~~~

### Phase 2：持久化在线经验

工作：

- 实现 EvolutionExperienceMemory。
- 把旧 type memory 作为组合成员保留。
- 实现旧 artifact 的单边界迁移。
- 实现结构签名、代表 slot、确定性检索和 checkpoint 恢复。

验收：

- 旧 memory 无损加载并写为唯一新 schema。
- round-trip 后规范化内容一致。
- exact match 优先，fallback 顺序稳定。
- memory 不包含完整原始轨迹、gold 或 evaluator 字段。
- resume 不重复计数。
- 外部调用次数不变。

提交：

~~~text
feat(evolution): persist online causal episode memory
~~~

### Phase 3：反思使用因果历史

工作：

- reflection 接收当前 episode 和历史参考。
- 替换宽泛、重复的 generation context。
- 删除 contrast registry 的生成用途。
- 保持 candidate schema、ActionSpec 和现有 gate。

验收：

- 无历史时流程正常完成。
- 有历史时 prompt 包含当前 edge、共同/差异事实、历史 action 和本地 outcome。
- prompt 不包含 raw case identity、gold 或完整轨迹。
- 不增加任何模型或 replay 调用。
- 现有合法 candidate 仍可编译。
- runner 不再拥有结构投影规则。

提交：

~~~text
refactor(reflection): ground proposals in causal history
~~~

### Phase 4：闭合执行反馈

工作：

- 将 compiled/injected/applied/observable/downstream outcome 写回 FailureAttempt。
- 结构化 no-effect 原因。
- 跨案例复用现有 semantic duplicate 检查。
- 完成两案例顺序集成测试和多中断点 resume 测试。

验收：

- accepted、rejected、runtime-invalid、executed-no-effect 可区分。
- 后续同机制案例不会原样重复已确认 no-effect 的语义 proposal。
- 两案例测试证明 A 的 outcome 在 B 的 reflection 前可见。
- 中断恢复结果与无中断运行一致。
- TypeExperienceMemory 不复制完整 episode。

提交：

~~~text
feat(evolution): close the online causal feedback loop
~~~

### Phase 5：完整跨运行继承

工作：

- 实现 state manifest。
- 实现 public API 和 CLI 的 inherit-from。
- 校验 genome、policy、memory 的 hash 和 lineage。

验收：

- 新数据运行的初始三个 artifact hash 与上一轮 manifest 完全一致。
- 冲突输入明确失败。
- resume 行为和测试不变。
- standalone/public API 能读取完整状态或明确拒绝不支持的使用方式。

提交：

~~~text
feat(evolution): inherit complete evolution state
~~~

### Phase 6：真实 gold 小批次验证

工作：

- 选择未使用的十条 gold 数据。
- 每个进程只运行一条，十个并发。
- 输出目录和 memory 隔离。
- 实时监控直到全部终态。
- 对每条结果做结构化案例分析。

验收：

- 十个任务全部达到明确终态，无静默挂起。
- 每个触发进化的 runtime-valid 案例有可审计 episode。
- accepted skill 有完整执行证据。
- 没有 skill 的案例能够定位到具体漏斗阶段。
- 发现代码问题并修复后，必须重跑同一批十条。
- 同批满足预期后才能标记完成并提交。

提交：

~~~text
test(evolution): validate causal skill loop on gold batch <batch-id>
~~~

下一批必须更换十条数据，并更新 used-case ledger。

---

## 13. 测试矩阵

### 13.1 Builder 单元测试

至少覆盖：

- 单候选完整生产—消费链；
- 多候选但 court 指定责任候选；
- missing field；
- malformed field；
- provider output 存在但下游未消费；
- provider output 改变但 court invariant；
- 引用断裂产生 partial；
- 无合法 action 产生 unaddressable；
- canonical hash 稳定；
- mechanism signature 不受 case identity 影响；
- prompt firewall。

### 13.2 Memory 单元测试

至少覆盖：

- 旧 type-only memory 迁移；
- 新 schema round-trip；
- exact 和三级 fallback；
- 当前 episode 排除；
- outcome slot 替换顺序；
- 统计累加；
- resume upsert 幂等；
- memory 无 raw trajectory 和 evaluator 字段。

### 13.3 Reflection 单元测试

至少覆盖：

- 无历史；
- effective + no-effect 历史；
- 只有 rejected 历史；
- partial 当前 episode；
- action envelope 与 candidate schema 对齐；
- prompt 长度不超过现有输入上限；
- provider 调用数量与基线一致。

### 13.4 顺序集成测试

建立两条相同机制、不同 case identity 的确定性 fixture：

~~~text
case A
  -> episode A
  -> proposal A
  -> no-effect 或 accepted local outcome
  -> persisted memory

case B
  -> compatible episode B
  -> retrieve A
  -> reflection input contains A outcome
  -> proposal is compiled and replayed
~~~

该测试只证明在线历史进入生成闭环，不以 B 必须改善为验收条件。

### 13.5 调用预算测试

对相同 fixture 比较 Phase 0 和 Phase 3：

| 调用类型 | 允许变化 |
| --- | --- |
| detector/provider | 0 |
| reflection | 0 |
| critic | 0 |
| source replay | 0 |
| contrast replay | 0 |
| migration validation | 必须为 0 |

---

## 14. 真实实验协议

### 14.1 批次规则

- 使用 gold 标签数据。
- 每批十个不同案例。
- 十个实验并发。
- 每个实验只包含一条数据。
- 同一批的进程不得共享工作 genome、policy memory 或写入文件。
- 不同批次不得复用案例。
- used-case ledger 是案例唯一性来源。
- 十个并发实验不用于验证在线跨案例继承。

### 14.2 修复规则

如果一批中发现框架问题：

1. 保存原始失败 artifact。
2. 识别损失漏斗阶段。
3. 定位唯一所有者模块。
4. 按 pristine 修复根因。
5. 运行相关单元和集成测试。
6. 重新运行同一批十条。
7. 只有重跑结果符合预期，批次才完成。

禁止：

- 修复后直接更换新批次；
- 删除失败案例；
- 因 content/problem 难处理而调整准入阈值；
- 用单案例专用 condition、case id 或原文片段形成 skill；
- 为提高通过数增加额外验证。

### 14.3 实时监控

执行模型必须持续监控：

- 每个子任务状态；
- 当前处理阶段；
- provider error、timeout、content filter；
- checkpoint 更新时间；
- 最终 artifact 是否完整。

监控等待应使用可中断的短轮询，不使用超过 60 秒的阻塞 sleep。状态没有变化时无需反复输出相同内容。

### 14.4 每案例分析模板

~~~text
case_id:
gold:
baseline detection:
evolution trigger:
attribution:
failure edge:
episode completeness:
history retrieval:
proposal:
ActionSpec:
compiled / injected / applied:
observable delta:
downstream court delta:
decision:
no-effect reason:
framework defect or expected behavior:
~~~

---

## 15. 验收漏斗和解释边界

必须记录以下计数：

~~~text
should_evolve
  -> episode_created
  -> failure_edge_complete
  -> history_retrieved
  -> proposal_generated
  -> action_compiled
  -> action_injected
  -> action_applied
  -> observable_changed
  -> local_source_improved
  -> skill_accepted
~~~

这些计数用于定位问题，不是新的 acceptance threshold。

解释规则：

- should_evolve -> episode_created 大量损失：结构化构建或归因 contract 有问题。
- episode_created -> proposal_generated 大量损失：skill 提取能力不足。
- proposal_generated -> action_compiled 大量损失：prompt/action envelope/schema 不一致。
- action_compiled -> action_applied 大量损失：condition 或 provider 执行问题。
- action_applied -> observable_changed 大量损失：动作字段与真实 owned output 不一致。
- observable_changed -> local_source_improved 大量损失：下游 court 对该变化不敏感或方向错误。
- 单纯 accepted rate 低不能直接证明 detector 底座不可用。

本任务完成可以证明：

- 框架能从失败轨迹产生结构化、可追踪的 skill 生成依据；
- 历史经验能逐案例进入后续生成；
- skill 能进入真实执行和本地反馈链路。

本任务不能单独证明：

- 独立数据集整体检测准确率提高；
- skill 具备普遍迁移效果；
- detector 已达到最终科研性能。

---

## 16. 硬验收边界

以下全部满足才可宣称代码任务完成：

1. 所有当前测试通过。
2. 所有新增单元和集成测试通过。
3. runtime-valid 且触发进化的案例都产生合法 episode 或明确 unaddressable 原因。
4. 所有 evidence ref 可解析，或 episode 明确标记 partial。
5. prompt 中没有 gold、evaluator 和 case identity 泄漏。
6. 外部模型和 replay 调用次数没有增加。
7. skill 阈值和 acceptance gate 没有变化。
8. 相同输入产生确定的 episode id、检索顺序和 prompt projection。
9. checkpoint resume 不产生重复 episode、attempt 或统计。
10. accepted skill 有 compiled、injected、applied 和 observable 证据。
11. 同机制已确认 no-effect 的 proposal 不会被原样重复。
12. 两案例顺序测试证明历史 outcome 进入后续 reflection。
13. 完整状态可以通过 manifest 跨运行继承。
14. 十个 gold 单案例并发实验保持隔离语义。
15. 每次实验修复后重跑同批并通过。
16. 每个完成阶段和每个完成批次都有独立 Git commit。
17. Git diff 不包含无关用户文件、密钥、临时输出、备份或注释代码。
18. 最终代码中没有长期双轨 projection、旧/new schema 分支散落或未使用 wrapper。

---

## 17. Review 清单

每次提交前执行模型必须检查：

### 架构

- 是否只有一个 failure episode builder？
- attribution、ActionSpec、memory、reflection 的所有权是否清晰？
- runner 是否只做编排？
- 是否把新逻辑错误地放进 TypeExperienceMemory？
- 是否存在同一事实的两份持久化来源？

### pristine

- 是否从根因修复，而不是增加 case-specific 分支？
- 是否新增 feature flag、兼容开关或“暂时”路径？
- 是否有注释解释代码“做什么”而不是“为什么”？
- 是否有 TODO、dead code、备份文件或注释掉的实现？
- 被新实现替代的旧逻辑是否已经删除？

### 安全

- 是否读取但未打印环境密钥？
- prompt 是否经过 evaluator/gold firewall？
- artifact 是否包含不必要的原始轨迹？
- manifest hash 是否在加载时验证？

### 实验

- 是否使用未出现过的数据？
- 十个进程是否完全隔离？
- 修复后是否重跑同一批？
- 结论是否基于实际 artifact，而不是只看实验 summary？

---

## 18. 停止条件

出现以下情况必须停止当前阶段并报告，不得用 workaround 继续：

- 需要调整阈值才能让测试通过；
- 需要增加模型调用才能构建 episode；
- 现有 detector result 没有必要 artifact，且无法在不改变判决语义的情况下补全；
- 旧 checkpoint 无法无损迁移；
- prompt firewall 无法证明；
- Phase 0 基线本身不稳定；
- 工作树存在与本任务重叠的用户修改；
- API 连续失败且证据表明是外部服务问题；
- 必须扩大到 detector/proof court 重写才能继续；
- 一个阶段无法形成无临时双轨的完整提交。

报告必须包含：

- 阻塞文件和函数；
- 最小复现；
- 预期与实际；
- 已排除的原因；
- 继续所需的用户决策或新授权。

---

## 19. 下一对话启动指令

在新的对话中发送：

~~~text
请执行自进化框架因果经验闭环的代码优化。

开始前必须完整读取：
1. C:\Users\liuxinglin\Desktop\agent hallucination\.agents\skills\pristine\SKILL.md
2. C:\Users\liuxinglin\Desktop\agent hallucination\docs\design\SELF_EVOLUTION_FRAMEWORK_REVIEW.md

把第二份文档视为本任务唯一实施契约，不要重新发散设计，也不要使用历史实验日志覆盖其中的边界。

从 Phase 0 开始，先核验当前工作树、测试基线和代码真实状态，再逐阶段实施。每个阶段必须完成对应测试、review 和 Git commit 后才能进入下一阶段。不得调整 skill 准入阈值，不得增加迁移验证或模型调用，不得围绕十个单案例并发实验设计核心架构。任何修复必须遵守 pristine，从唯一规则所有者修复根因并删除被替代逻辑。

持续执行到当前阶段的验收条件满足；发现超出实施契约的阻塞时，提供代码和运行证据后停止，不要自行扩大范围。
~~~

---

## 20. 研究参考边界

以下工作只用于理解历史经验、在线更新和可执行 skill 的已有思路，不是可直接复制的目标架构：

- Reflexion: https://arxiv.org/abs/2303.11366
- ExpeL: https://arxiv.org/abs/2308.10144
- CLIN: https://arxiv.org/abs/2310.10134
- Agent Workflow Memory: https://arxiv.org/abs/2409.07429
- Voyager: https://arxiv.org/abs/2305.16291
- AgentHallu: https://arxiv.org/abs/2601.06818

实现优先级是先跑通本文档定义的 detector-specific 因果经验闭环。不得为了对齐论文术语增加抽象层，也不得在闭环完成前声称研究 novelty。
