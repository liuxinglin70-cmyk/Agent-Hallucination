# SEHD v6 本地代码修复、运行验证与复审 Agent Prompt

> 用法：将本文档完整交给在开发机上运行、能够访问本地文件系统和 Git 远端的代码 Agent。该 Agent 应直接读取本地仓库、执行命令、修改文件、安装或下载必要依赖、运行测试、提交并推送修复。不要只复制其中某一节。

---

## 你的角色与执行权限

你是一名在本地开发环境中运行的资深软件工程 Agent，同时承担研究型 AI 检测框架的实验方法审计和代码审查职责。你的任务不是“让测试变绿”或“让五例看起来有提升”，而是直接修复 SEHD v6 中会导致检测运行失效、标签污染、自确认式进化、错误经验积累或实验结论失真的机制问题，并用本地可复现测试和真实 Git 提交证明修复有效。

你具备并应主动使用以下能力：

- 读取本地仓库中的全部受版本控制文件，以及与本任务有关的本地实验产物；
- 使用 shell、Python、测试框架、lint/type-check 工具和 Git；
- 从配置好的 Git 远端执行 `fetch`、`pull`/下载、读取远端分支和提交；
- 在需要时按仓库既有方式安装依赖、初始化 submodule、拉取 Git LFS 文件或下载公开测试资源；
- 直接编辑生产代码、测试、文档和实验脚本；
- 创建分支、提交 commits，并将修复分支推送到配置好的远端；
- 若本机有 `gh` 或等价工具，创建 Draft PR；若没有，则仍须完成分支推送并给出创建 PR 所需的信息。

**本地文件系统中的实际仓库内容是第一事实来源。** 不要只根据本 prompt、旧审计摘要或远端网页推断代码。你必须打开真实文件、搜索调用链、执行测试并检查生成产物。

目标仓库：

- 远端参考：`https://github.com/liuxinglin70-cmyk/sehd-v6`
- 静态审计基线提交：`3ee7a882fbaa9b320c58c150d763a7637b5c05c7`
- 建议修复分支：`fix/trial5-evolution-hardening`

工作开始时必须先确定本地仓库根目录和真实状态，不要假设当前目录、分支或 HEAD：

```bash
pwd
git rev-parse --show-toplevel
git status --short --branch
git remote -v
git log -1 --decorate --oneline
git submodule status --recursive || true
```

如果当前目录不是仓库：

1. 先在当前工作目录及合理的父目录中定位已有本地副本；
2. 只有确认不存在本地副本后，才从已配置远端或上述 URL clone；
3. clone 后检查 submodule、Git LFS 和项目安装说明。

然后执行：

```bash
git fetch --all --prune --tags
```

读取当前默认分支和远端 HEAD，列出当前工作基线相对 `3ee7a882fbaa9b320c58c150d763a7637b5c05c7` 的提交与关键文件变化。不要假设仓库仍停留在审计基线，也不要为了匹配基线而回退现有代码。

### 脏工作区保护协议

在任何修改、切换分支、pull、rebase 或依赖生成动作之前检查工作区：

- 不得执行 `git reset --hard`、`git clean -fd[x]`、强制 checkout、覆盖式 restore 或删除用户未提交工作；
- 不得未经审查把已有未提交改动混入本任务 commit；
- 如果原工作区是干净的，在当前 HEAD 上创建修复分支；
- 如果原工作区有未提交改动，优先从当前 HEAD 创建独立 `git worktree` 和修复分支，在新 worktree 中工作，保持原工作区不动；
- 记录原始 `git status`、当前分支、HEAD 和已存在改动的文件名，但不要把敏感内容复制进报告；
- 发现与本任务直接冲突的本地改动时，先做最小化兼容处理并在最终报告中说明，绝不能静默覆盖。

推荐命令示例：

```bash
# 干净工作区
git switch -c fix/trial5-evolution-hardening

# 脏工作区：在仓库同级目录创建隔离 worktree
git worktree add ../sehd-v6-agent-fix -b fix/trial5-evolution-hardening HEAD
```

### 下载与依赖规则

你可以下载本任务所需内容，但必须遵守：

- 优先使用仓库锁文件和既有安装命令，不擅自升级大版本依赖；
- 先检查 `pyproject.toml`、lockfile、README、CI workflow 和测试脚本，再选择安装命令；
- 下载的 fixture、模型输出或外部数据必须记录来源、用途和是否应提交；
- 不把大型缓存、虚拟环境、provider 原始敏感响应、API key 或临时下载物提交进仓库；
- 不执行来源不明的远程脚本，例如未经审查的 `curl ... | sh`；
- 安装或下载失败时保留原始错误并采用仓库支持的替代路径，不伪造测试通过。

### Git 写入与推送规则

你必须实际完成代码修改和 Git 交付，而不是只输出 diff 建议：

- commits 按机制问题拆分，保持可审查；
- 每个 commit 前检查 `git diff --check`，并确认没有 secrets、运行缓存或无关文件；
- 不改写、不 force-push 默认分支；
- 修复完成且验收通过后，将修复分支推送到配置好的远端；
- 推送后用 `git ls-remote` 或远端查询验证分支确实存在，不能只根据 push 命令退出码声称成功；
- 若可用，创建 Draft PR；若无法创建 PR，提供远端分支、commit SHA、目标基线和建议 PR 标题/正文；
- 除非仓库权限或明确保护规则阻止，否则“仅本地 commit、未推送”不算完成。

---

## 背景与已知实验事实

一次真实五例严格自监督运行 `trial_5_clean` 已完成，主要配置与结果如下：

```text
supervision_mode=self_supervised
use_gold=False
fitness_use_gold=False
canary_limit=2
validation_probe_every=0
fitness_batch_size=1
max_mutation_attempts_per_case=2
```

核心结果：

```text
total_cases=5
triggered_cases=2
stable_cases=1
runtime_invalid_cases=2
edits_accepted=0
edits_rejected=1
edits_no_edit=1
source_replays=1
canary_cases=0
probe_cases=0
champion_generation=0
champion_edits=[]
```

逐案结果：

1. `BFCL/000`：触发 `focused_quarantine`，生成一条 `evidence_obligation/add_rule`，source replay 后 `defense_coverage: 1.0 -> 0.0`，局部分数 `0.3 -> 0.0`，正确判为 `worse` 并拒绝。
2. `BFCL/001`：`verified_error`，`skip_stable`，但它出现在 BFCL/000 之后，因此不能作为 BFCL/000 当时的 canary。
3. `Camel/000`：`skip_runtime_invalid`，根因是 `appeal/required_appeal_incomplete`，decision authority 失效。
4. `Magentic_One/000`：触发 unresolved，mutation 返回 `no_edit`。
5. `Octotools/000`：与 Camel/000 相同的 `appeal/required_appeal_incomplete`。

本次无 mutation cache 命中，因此上述现象不能归因于旧 mutation 缓存复用。

实验还暴露出一项新的经验记录不一致：BFCL/000 在 BCCSE source assessment 中是 `worse/regressed`，但 HTC-TES 经验 metadata 同时记录：

```text
process_state=no_effect
outcome=regressed
outcome_decision=worse
```

这会使后续 reflection 同时看到“无效果”和“发生回归”的矛盾信号。

注意：日志中“gold 不泄漏”的结论只由 `has_gold=False`、`gold_enhanced=False` 推导，不能证明 `hallucination_category`、`hallucination_subcategory` 或嵌套 evaluator 字段没有进入 reflection prompt。必须通过代码级 prompt capture 测试重新验证。

---

## 总目标

完成一轮面向真实闭环的修复，使框架满足以下定义：

1. 检测器在 provider 返回可修复的不完整 appeal 输出时能进行有界、可审计的 schema repair/retry；仍失败时继续 fail closed，绝不能伪造 authority。
2. detector、evolution、reflection、experience memory 的所有输入边界使用统一且递归的 evaluator 字段防火墙。
3. 严格自监督模式不能依赖 source 自己确认正确性；公开 API/CLI 必须支持预注册、与 evolution source 隔离的 canary/probe。
4. mutation 或 appeal 的重试必须真正改变请求，并携带上次失败的结构化反馈；不能命中完全相同的缓存结果后重复失败。
5. BCCSE 与 HTC-TES 对同一次 replay 的 improvement/no_effect/regression 结论必须一致，或者明确拆分为不同语义字段，不能出现互相矛盾的单一 `process_state`。
6. 当前五例只能作为 wiring/stability test，代码、文档和结果摘要不得把它描述为准确率提升、baseline 充分覆盖或 champion 证据。

---

## 不可违反的约束

- 不得通过放宽 `decision_authority`、把 incomplete 当 complete、把 runtime failure 当 clean、把 unresolved 当正确来降低 `runtime_invalid`。
- 不得把 gold、人工类别、case ID、split role、outer feedback 或人工解释送入 detector/runtime/mutation prompt，除非是显式 gold-enhanced 实验中经过注册的 evaluator-only方向信息；默认自监督必须完全隔离。
- 不得用当前五例或 final holdout 反复调参并声称泛化。
- 不得删除 fail-closed 逻辑来“提高完成率”。
- 不得硬编码 `Camel/000`、`Octotools/000`、`BFCL/000` 或任何 AgentHallu case 的专用修复。
- 不得写入、打印或提交 API key、token、私有 endpoint credential。
- 不得仅修改文档或仅增加宽松 mock；必须修改实际生产路径并添加能失败于旧实现、通过于新实现的测试。
- 不得在完成离线与 mock 测试前启动昂贵的 50 例 provider 实验。

---

# 工作包 A：先复现和建立故障矩阵

## A1. 仓库与运行产物盘点

先在本地运行全仓库结构盘点，至少执行或等价完成：

```bash
git ls-files > /tmp/sehd_v6_tracked_files.txt
find . -maxdepth 4 -type f | sort > /tmp/sehd_v6_local_files.txt
rg -n "required_appeal_incomplete|EVALUATOR_ONLY_FIELDS|canary_case_ids|validation_probe_every|process_state|expected_effect" src tests docs experiments || true
```

`rg` 没有命中可以正常返回，但测试、构建、lint 命令不得用 `|| true` 隐藏失败。对调用链的结论必须来自本地文件和运行结果。

读取并关联至少以下文件：

```text
src/sehd_v6/evolution/runner.py
src/sehd_v6/evolution/__main__.py
src/sehd_v6/core/bccse/evolution_runner.py
src/sehd_v6/core/bccse/reflection_engine.py
src/sehd_v6/core/bccse/step_attributor.py
src/sehd_v6/core/bccse/fitness.py
src/sehd_v6/core/bccse/genome.py
src/sehd_v6/core/bccse/safety_gate.py
src/sehd_v6/core/htc_tes/type_classifier.py
src/sehd_v6/core/htc_tes/type_conditioned_evolution.py
src/sehd_v6/core/htc_tes/type_aware_fitness.py
src/sehd_v6/core/paca_tcl/detector.py
src/sehd_v6/core/paca_tcl/basic_runner.py
src/sehd_v6/core/paca_tcl/proof_engine.py
src/sehd_v6/core/paca_tcl/proof_contract.py
src/sehd_v6/core/paca_tcl/skill_integration.py
src/sehd_v6/utils/ocse_packet.py
docs/design/TECHNICAL_DESIGN.md
docs/technical/EXPERIMENT_FRAMEWORK_GUIDE.md
tests/test_evolution_standalone.py
tests/test_evolution_hardening.py
tests/test_detector_hardening.py
```

检查本地仓库中的真实运行产物。先用 `find`/`git ls-files`/`rg` 定位，不要仅依赖下面给出的路径；若路径存在则读取原始 JSON，而不是只使用汇总日志：

```text
experiments/protocols/trial_5_output/evolution_result.json
experiments/protocols/trial_5_output/evolution_checkpoint.json
experiments/protocols/trial_5_output/champion_genome.json
experiments/protocols/trial_5_output/htc_tes_experience_memory.json
experiments/protocols/trial_5_output/htc_tes_coverage.json
experiments/protocols/trial_5_output/runtime_failures/0002.json
experiments/protocols/trial_5_output/runtime_failures/0004.json
```

若产物未被 Git 跟踪但存在于本地，也可以读取用于审计，但先确认其中不含 credential 或不应外传的 provider 原始数据，并且不要默认提交这些产物。若本地完全没有原始产物，不要猜测内容；使用本 prompt 中的已知事实继续工作，并在最终报告中注明未能直接读取原始 JSON。

## A2. 输出根因矩阵

在修改代码前建立一份表，至少包括：

| ID | 现象 | 实际根因 | 涉及文件/函数 | 是否机制错误 | 修复策略 | 所需测试 |
|---|---|---|---|---|---|---|

不要把 provider “不稳定”当作最终根因。需要进一步解释为什么现有 schema、prompt、validator、retry、cache 或 authority 转换没有正确处理该响应。

---

# 工作包 B（P0）：统一、递归、可证明的标签防火墙

## B1. 单一字段来源

以 `src/sehd_v6/utils/ocse_packet.py` 中统一定义的 evaluator-only 集合为唯一来源，至少覆盖：

```text
is_hallucination
hallucination_step
hallucination_reason
hallucination_category
hallucination_subcategory
gold_label
gold_steps
gold_explanation
outer_feedback
```

同时审查并合理处理：

```text
case_id
source_id
source
source_dir
split role / partition role
agent_type
model_id
```

区分数据集身份字段与真实 tool payload 中同名业务字段，不能粗暴删除工具参数里的合法 `source`。

删除或重构以下重复且不一致的防火墙定义：

- `bccse.evolution_runner._EVALUATOR_ONLY_FIELDS`
- `bccse.reflection_engine._EVALUATOR_TRAJECTORY_KEYS`
- 各 provider prompt builder 中手写的 forbidden 字段 tuple/set

应提供共享函数，例如：

```python
strip_evaluator_fields(value, *, boundary: str) -> sanitized_copy
find_evaluator_field_hits(value, *, boundary: str) -> list[path]
assert_label_free(value, *, boundary: str) -> None
```

要求递归处理 Mapping、list、tuple，并返回字段路径用于审计，例如：

```text
events[0].gold_label
history[1].metadata.hallucination_category
messages[0].gold_steps
```

## B2. 所有真实边界都必须调用

至少覆盖：

1. standalone detector 输入；
2. evolution detector 输入；
3. OCSE packet 构建前；
4. reflection trajectory context；
5. mutation prompt 最终字符串生成前；
6. experience memory 中未来会重新进入 prompt 的 feature/metadata；
7. focused prosecution、defense、appeal、counterfactual 等 provider user prompt。

禁止只依靠最终 prompt 中搜索几个字符串，因为标签值可能存在但字段名被改写。需要在结构化序列化之前清洗，并在序列化后做最后的 fail-closed 审计。

## B3. 必需测试

新增测试必须证明旧实现会失败，新实现通过：

- 顶层 `hallucination_category` / `hallucination_subcategory` 被移除；
- `history[*]`、`events[*]`、`messages[*]`、`task` 内嵌套字段被递归移除；
- 字段的敏感值也没有出现在最终 reflection user prompt；
- 合法 tool arguments 中名为 `source` 的业务字段仍保留；
- 默认自监督 prompt 不包含 case ID、人工标签、人工类别或 gold steps；
- gold-enhanced 模式仅通过专门、受控且对称的 diagnostic reference 输入，不把原始 gold explanation/category 透传。

建议使用 capture stub 截获传给 `json_object_provider_call` 的 `system` 和 `user`，不要只测试中间 helper。

---

# 工作包 C（P0）：修复 appeal prosecutor incomplete 导致的 detector authority 失效

## C1. 精确定位失败层

追踪：

```text
_appeal_trigger_calibrator_v35
_appeal_prosecutor_user_v35
provider_call / provider_call_with_schema
appeal response validation
_merge_prosecutions
proof_contract.adjudicate_proof_court
_resolve_incomplete_appeal
_run_detection_with_genome
```

确定 `required appeal prosecution is incomplete` 的具体条件：

- provider 返回 `coverage_state=incomplete/unresolved`；
- 缺失 `coverage_state`；
- JSON/schema 不完整；
- candidate ID 超出集合；
- 应覆盖的 candidate 未被明确处理；
- 空证书是否被错误理解为未覆盖；
- max_tokens 截断；
- prompt 与 validator 对“每个候选必须返回一行”还是“仅返回成立证书”定义不一致；
- appeal prosecutor 与 defender 的 coverage 合同是否不对称。

## C2. 合同必须明确

为 appeal prosecutor 定义并实现唯一合同。推荐语义：

- `coverage_state=complete` 表示模型审查了所有 `APPEAL_CANDIDATES`；
- `candidates=[]` 可以表示全部审查后没有可成立的补充起诉，不等于 incomplete；
- 若返回证书，每条 `source_candidate_id` 必须属于允许集合；
- 响应必须明确记录 `reviewed_candidate_ids`，或以每候选 disposition 结构证明覆盖；
- 不得仅根据自然语言推断 coverage complete；
- 不能把缺字段自动补成 complete。

若引入 `reviewed_candidate_ids` 或 disposition，请版本化 schema，并保持旧 artifact 的只读兼容或明确拒绝旧 schema。

## C3. 有界 repair/retry

对可修复的 appeal schema 失败增加最多一次或两次有界重试：

- retry prompt 必须包含稳定的结构化失败类别，例如：

```text
missing_coverage_state
unreviewed_candidate_ids
foreign_candidate_id
invalid_certificate_schema
response_truncated
```

- retry 必须使用相同候选和证据，但请求 body 必须不同；
- cache key 必须因此不同，并在 audit 中记录 parent request hash、retry request hash、failure category；
- 不要把自由文本 provider 输出整段回灌 prompt；只回灌清洗后的结构化错误；
- retry 失败后保持原有 fail-closed：无完整初审本地 authority 时返回 unresolved/invalid；不能为了完成率保留不具 authority 的 decisive state。

检查 `max_tokens` 是否足以覆盖最坏情况下 8 个 appeal candidates；如需动态预算，设硬上限并记录实际预算，避免无限扩张。

## C4. 必需测试

至少覆盖：

1. `coverage_state=complete, candidates=[]` 是合法的“完整审查、无补充证书”；
2. 首次缺 coverage、第二次修复成功；
3. 首次遗漏 reviewed candidate、第二次补齐；
4. 返回 foreign candidate 时拒绝；
5. 两次都失败时保持 unresolved/invalid，不产生 decisive 结果；
6. 初审已经具完整 local positive authority 时，appeal infrastructure 失败的保留行为与文档一致；
7. Camel/Octotools 的离线 fixture 可以复现原错误，并在修复后不再因相同 schema 条件失效。

不能只把异常 catch 掉。测试应断言 authority、runtime_valid、state、appeal_status、failure audit 都正确。

---

# 工作包 D（P0）：让公开 API/CLI 支持真正独立的 canary/probe

## D1. 修复当前入口缺陷

当前公开 `run_evolution()` 把全部 `records` 的键都作为 `evolution_case_ids`，同时固定：

```text
canary_case_ids=()
probe_ids=()
validation_probe_every=0
probe_limit=0
```

因此调用者无法在同一个 records 集合中提供“可读取但不参与 mutation source”的独立 canary/probe。

重构公开 API，使调用者可以显式提供：

```python
records                  # evolution + canary + probe 的记录总表，或分别传映射
evolution_case_ids
canary_case_ids
probe_ids
validation_probe_every
validation_probe_trigger_threshold
probe_limit
```

保持简单旧调用的兼容，但旧调用在严格自监督模式下必须明确标记为：

```text
unanchored_diagnostic_only
```

或直接 fail fast。推荐默认 fail fast，只有显式 `allow_unanchored_diagnostic=True` 才允许运行，且该模式：

- 不能提交 label-free provisional edit；
- 输出中明确 `promotion_disabled_reason=no_independent_canary`；
- 不能使用“champion”措辞，只能叫 baseline/working genome。

## D2. CLI 与 manifest

为 CLI 增加清晰参数，建议支持：

```text
--evolution-ids <json/jsonl>
--canary-ids <json/jsonl>
--probe-ids <json/jsonl>
--validation-probe-every N
--validation-probe-trigger-threshold N
--probe-limit N
--allow-unanchored-diagnostic
--sequence-seed N
```

所有角色必须：

- 去重；
- 互斥；
- records 可加载；
- role、顺序和哈希写入 checkpoint/result；
- final holdout 永远不能出现在此 evolution API 中。

## D3. 预先建立 canary baseline

不要等到 evolution 序列里先遇到 stable case 后才有 canary。预注册 canary 必须在任何 mutation source 之前用 baseline/current parent genome执行并登记。

严格自监督 promotion 至少需要一个独立、初始稳定、child replay 仍稳定的 canary。`canary_limit` 只是采样上限，不是 canary 的来源。

## D4. 必需测试

- 公开 API 传入 1 source + 1 canary，source 产生 directional evidence，canary preserved 后 working genome generation 变为 1；
- 相同 source 没有 canary 时默认 fail fast；
- 显式 diagnostic-only 模式可以运行但 generation 必须保持 0；
- evolution/canary/probe 重叠时拒绝；
- canary 初始不稳定时自监督运行拒绝启动；
- probe 仅 veto/rollback，不能把 provisional label-free edit 升级为 accuracy evidence；
- final holdout ID 被传入 evolution role 时拒绝。

---

# 工作包 E（P1）：修复无效 mutation 重试与缓存重放

## E1. 当前问题

当 mutation 返回可解析 JSON，但无法组成合法 `SkillEdit` 时，runner 可能重新排队同一 source；如果 system/user 未改变，`json_object_provider_call` 会命中相同 request hash，返回相同无效响应，所谓重试没有搜索价值。

## E2. 修复要求

每次 retry 必须增加结构化上下文：

```text
attempt_index
previous_failure_stage
previous_failure_categories
allowed_operations
allowed_modules
schema_version
```

只传类别，不传可能包含 case ID、规则全文或 evaluator 信息的原始异常。

request body 必须改变，audit 中记录：

```text
request_hash_before
request_hash_retry
cache_hit
retry_feedback_categories
```

若第一次是 `operation=no_edit`，默认不要强迫模型第二次编辑；`no_edit` 是合法决策。只有 invalid schema、repairable structural reject 或 source no-effect 才进入有限 retry。

## E3. 必需测试

- 第一次 mutation 返回无效 operation，第二次在反馈后返回有效 edit；两个 request hash 不同；
- 第二次 prompt 包含稳定失败类别，不包含原始 case-specific 内容；
- 第一次 `no_edit` 不重试；
- transport error 与 semantic invalid 分开计数；
- retry 预算耗尽后只记录一次 source-level no-edit convergence 结果，避免 attempt 级重复增加 convergence streak。

---

# 工作包 F（P1）：统一 BCCSE 与 HTC-TES 的 fitness 语义

## F1. 修复日志中的矛盾

BFCL/000 已出现：

```text
BCCSE assessment.decision=worse
local_score=0.3 -> 0.0
outcome=regressed
HTC-TES process_state=no_effect
```

这是错误的语义组合。顶层 state 未变不代表模块局部过程没有回归。

## F2. 推荐设计

选择以下一种明确设计，并贯穿代码、artifact 和文档：

### 方案一：一个权威 source fitness outcome

`TypeAwareFitness.assess_edit()` 接收完整 `FitnessAssessment`，直接把：

```text
strong_improvement/local_improvement -> improvement
directional_evidence -> directional_improvement
no_effect -> no_effect
worse -> regression
```

作为 `process_state`。

### 方案二：拆分两个字段

```text
verdict_transition_state = improvement/no_effect/regression
source_fitness_outcome = improved/directional/no_effect/regressed
```

不得继续让一个字段说 no_effect、另一个字段说 regressed 而没有语义说明。

`override_success` 只能控制 experience 是否记为 success，不能把独立观察到的 regression 变成 no_effect。

## F3. 必需测试

- state 不变但 module local score 下降，process 必须是 regression；
- state 不变且所有局部输出不变，才是 no_effect；
- label-free unresolved -> decisive 是 directional，不是 correctness improvement；
- regression 永远不能因 override_success=True 被记为成功；
- experience summary 对同一 entry 只暴露一致的 outcome。

---

# 工作包 G（P1）：加强 expected-effect 的方向契约

BFCL/000 的 edit 目标是修复 prosecution evidence incomplete，但 mutation 的 `expected_effect` 却是：

```text
Prosecution evidence coverage becomes incomplete...
```

现有 `_expected_effect_audit` 仅凭关键词把它判为 `target_aligned=True`，没有发现它与 `unresolved_to_decisive` 和当前 prosecution deficiency 方向相反，直到昂贵 source replay 后才发现回归。

实现结构化 expected effect，至少包含：

```json
{
  "target_signal": "prosecution_coverage",
  "expected_direction": "increase|decrease|preserve|become_complete|become_incomplete",
  "expected_state_transition": "optional",
  "rationale_category": "..."
}
```

在 structural gate 之后、source replay 之前检查：

- expected effect 是否属于目标模块可观察输出；
- 是否与 attribution deficiency 相容；
- 是否与 evolution direction 相容；
- 是否企图通过降低另一侧 coverage 来制造“变化”；
- 是否要求运行时不可观察条件。

不满足时进入 repair prompt 或拒绝，不要消耗完整 detector source replay。

添加 BFCL/000 风格测试：当 prosecution 已 incomplete 且目标是 unresolved_to_decisive 时，`expected_direction=become_incomplete` 必须被判为 `direction_conflict`。

---

# 工作包 H（P1）：降低顺序依赖并完善实验 provenance

## H1. 顺序治理

- 所有运行记录明确的 sequence manifest、seed、sequence hash；
- 默认从注册 manifest 读取，而不是依赖 Python mapping 插入顺序；
- canary 在 source 前预执行，因此 source/canary 在输入文件中的排列不影响可用性；
- 不要把尚未出现的 evolution source 偷看成 canary；角色必须预注册。

## H2. 顺序测试

构造相同 source/canary 角色的两种 records 排列，保证：

- baseline canary audit 相同；
- source 的 promotion 决策相同；
- champion/working genome content hash 相同；
- budget audit 除时间戳外一致。

若 provider 非确定性阻碍完全 hash 一致，使用固定 mock provider验证机制，并在真实运行中报告 stochastic variance。

---

# 工作包 I（P1）：修正文档和实验结论边界

更新至少：

```text
docs/design/TECHNICAL_DESIGN.md
docs/technical/EXPERIMENT_FRAMEWORK_GUIDE.md
相关 README / small-scale validation plan
```

必须与当前代码一致地说明：

- 类型路由无交集时当前到底是 skip 还是回退到 attribution route；只能保留一种真实描述；
- appeal 失败时哪些初审 positive 可以保留 local authority，哪些必须 unresolved/invalid；
- standalone evolution 与正式 development/probe/final-holdout orchestration 的边界；
- self-supervised directional evidence 需要独立 canary 才能进入 reversible working candidate；
- 五例运行只证明 plumbing、fail-closed、回滚与审计链路，不证明 baseline 充分覆盖、准确率提高或 generalization；
- `generation=0` 表示未发生自进化，不应描述成“自进化成功”；
- `runtime_invalid=40%` 时不能据 1 个 stable case 声称 baseline 具备合理初始覆盖；
- `has_gold=False` 不能单独证明所有 evaluator metadata 未进入 prompt，必须由 firewall/prompt capture 测试证明。

修正代码注释中已经过时的架构描述，例如 safety gate 仍称 typed DSL 或已被替代的 canary 设计。

---

# 测试与执行顺序

按以下顺序工作，不要直接跑大实验：

## 第 1 阶段：静态与单元测试

1. 从本地 `pyproject.toml`、CI workflow、Makefile/任务脚本和 README 确认仓库官方测试入口；记录环境和依赖版本。
2. 运行仓库已有的 SEHD 专用测试命令；不要无差别收集外部/归档测试树。
3. 新增上述所有回归测试。
4. 运行 lint/type check（以仓库现有工具为准）。
5. 运行只使用 mock provider 的完整两例闭环：1 source + 1 canary。
6. 保存关键命令的退出码和简洁结果；不得删掉失败测试或用 `|| true` 隐藏失败。

## 第 2 阶段：离线五例重放

若已有缓存/fixture，重放：

```text
BFCL/000
BFCL/001
Camel/000
Magentic_One/000
Octotools/000
```

验证 schema、authority、角色隔离和 artifact，不调用真实 API。

## 第 3 阶段：真实 provider 五例试运行

只有前两阶段全部通过后才能运行。要求使用：

- 新输出目录；
- 新 run_id；
- 新 mutation/appeal cache namespace；
- 明确 evolution IDs；
- 至少 1–2 个预注册且与 source 隔离的 canary；
- probe 可继续禁用，但必须明确记录；
- 不加载被旧标签防火墙可能污染的 experience memory；
- 不复用本次 `trial_5_clean` 的 mutation experience 作为正式学习输入。

建议 run_id：

```text
trial_5_after_hardening
```

## 第 4 阶段：顺序稳健性试验

在固定 mock 或可复用 detector cache 下，至少跑两种 source 顺序，证明 role-prebaseline 后结果不再取决于 stable case 是否先出现。

在五例稳定运行前不要启动 50 例实验。50 例也只能是 development evidence，不是 final champion 证据。

---

# 强制验收标准

以下全部满足才能宣称本轮修复完成：

## 标签隔离

- [ ] evaluator-only 字段只有一个权威定义；
- [ ] detector 与 reflection 都递归清洗；
- [ ] `hallucination_category`、`hallucination_subcategory`、`gold_steps` 的顶层和嵌套泄漏测试通过；
- [ ] capture 到的自监督 mutation prompt 不含任何人工标签、类别、case ID 或对应敏感值；
- [ ] 合法 tool payload 未被误删。

## Appeal 稳定性

- [ ] complete + empty appeal prosecution 合法；
- [ ] 可修复 incomplete 会触发不同 request hash 的有界 retry；
- [ ] retry 失败仍 fail closed；
- [ ] 不通过放宽 authority 降低 runtime_invalid；
- [ ] Camel/Octotools 对应 fixture 不再产生原 schema 错误；
- [ ] 真实五例中若仍出现同类 runtime invalid，必须阻止进入 50 例并给出新的原始响应/validator 根因。

## 自监督进化

- [ ] 公开 API/CLI 可传独立 canary/probe；
- [ ] source/canary/probe 角色互斥并写入 provenance；
- [ ] 无 canary 的严格自监督默认拒绝或明确 diagnostic-only 且 generation 保持 0；
- [ ] mock 闭环中至少有一个合法 directional edit 经独立 canary 后进入 working genome；
- [ ] label-free edit 不获得 strong/weak correctness fitness，也不产生 accuracy/champion claim。

## Fitness 与经验

- [ ] `worse` 不再与 `process_state=no_effect` 同时出现；
- [ ] expected-effect 方向冲突在 replay 前被发现；
- [ ] regression 不能被 override 成成功；
- [ ] experience memory 中的 negative feedback 是稳定类别，不含 case-specific prompt poison。

## 重试与缓存

- [ ] semantic invalid retry 的请求内容与 hash 发生变化；
- [ ] retry prompt 含结构化失败类别；
- [ ] no_edit 不被强制重试；
- [ ] retry/convergence 计数按 source case 而不是 attempt 重复累计。

## 文档与结论

- [ ] 代码、CLI help、技术设计和实验指南一致；
- [ ] 五例报告不再把 generation 0 称作有效自进化；
- [ ] 不再以 1 stable + 2 runtime invalid 推导 baseline 覆盖充分；
- [ ] 说明 development/probe/final-holdout 的真实边界。

---

# 最终交付物

完成后必须提供：

1. **Root Cause Report**
   - 每个问题的触发条件、代码路径、为何旧测试未发现、修复原理。

2. **Code Changes**
   - 修改文件列表；
   - 每个关键函数的行为变化；
   - schema/version 兼容策略。

3. **Test Evidence**
   - 执行的完整命令；
   - 通过/失败数；
   - 新增测试名称与覆盖的不变式；
   - 不得只写“all tests passed”。

4. **Before/After Trial Table**

   至少比较：

   ```text
   runtime_invalid_cases
   appeal schema retries
   detector_calls
   mutation_calls
   mutation_cache_hits
   canary_cases
   probe_cases
   triggered_cases
   edits_accepted
   edits_rejected
   champion/working generation
   champion/working content hash
   firewall violations
   experience outcome consistency
   ```

5. **Artifact Audit**
   - 检查 result/checkpoint/genome/experience/coverage/runtime_failure 文件相互一致；
   - 验证计数不变式；
   - 验证 role manifest/hash；
   - 验证无 secret 和 evaluator metadata。

6. **Remaining Risks**
   - 明确哪些只是工程修复，哪些仍需统计实验；
   - 不得把 provider-backed 5 例当准确率证据；
   - 列出进入 50 例前仍需满足的 gate。

7. **Git Deliverables**
   - 本地仓库根目录、起始分支和起始 HEAD；
   - 修复分支名；
   - 每个 commit 的 SHA 与主题；
   - 推送的 remote 名和远端分支；
   - `git ls-remote` 或等价远端校验结果；
   - Draft PR 链接（本机工具支持时必须创建）；
   - 若 PR 创建工具不可用，给出建议 PR 标题和可直接使用的 PR 描述；
   - PR 描述中包含迁移、兼容性、回滚和实验重跑说明。

---

# 本地执行证据要求

最终报告必须让另一名工程师能够在同一仓库复现你的工作。至少包含：

```text
repository_root
starting_branch
starting_head
remote_default_branch
working_branch
python/runtime version
installation command
unit/integration test commands and exit codes
lint/type-check commands and exit codes
provider-backed commands actually run
files changed
git diff --stat
commit SHAs
pushed remote ref
PR URL or PR creation material
```

所有“已修复”“已推送”“测试通过”“没有标签泄漏”等结论都必须有本地命令或测试证据。没有执行的真实 provider 试验必须明确写为“未执行”，不能用 mock 结果代替。

# 最终报告结论格式

最终只允许从以下结论中选择，并给证据：

```text
A. 修复完成，可进入受控的 50 例 development run；仍不能做 champion 声明。
B. 核心代码已修复，但真实五例仍有阻断性 runtime/authority 问题，不得进入 50 例。
C. 发现更深层机制错误，当前框架仍不能可信自进化；列出最小下一步。
```

禁止使用“基本没问题”“应该可以”“看起来有效”这类模糊结论。

---

## 立即开始

不要先回复一份计划后停止。直接在本地执行以下动作：

1. 定位仓库根目录，检查 `git status`、remote、HEAD 和未提交改动；
2. `git fetch --all --prune --tags`，读取当前远端状态；
3. 按脏工作区保护协议创建独立分支或 worktree；
4. 读取五例原始产物和相关代码，输出并保存根因矩阵；
5. 按 P0 顺序实际修改代码：

```text
B 标签防火墙
C appeal authority/schema
D 独立 canary/probe 公开入口
```

6. P0 有测试证据后继续 E–I；
7. 完成全部可执行验收，检查 diff 和 secrets，提交原子 commits；
8. 推送修复分支并验证远端分支存在；
9. 创建 Draft PR，或在工具不可用时输出可直接创建 PR 的完整材料；
10. 最终报告必须引用真实文件、函数、命令、测试结果、commit SHA 和远端分支。

不要先重跑 50 例，不要通过弱化 fail-closed 获得更漂亮的结果，也不要只生成补丁文本而不落盘、测试、提交和推送。
