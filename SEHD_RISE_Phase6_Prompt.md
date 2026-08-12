# 阶段任务卡：SEHD-RISE Phase 6 — planning 假设搜索层

> 用法：本卡由定时任务（CronCreate, delayMinutes=2）自动加载执行。本卡 + 仓库 = 全部所需上下文；
> 上一会话的结论不在对话里，而在 git 提交与计划文档实施记录中（§3.4 R5）。本卡是
> SEHD_RISE_Refactor_Execution_Plan.md §Phase 6 的渲染，与文档冲突时以文档为准并回写文档。

## 0. 前置校验（30 秒；任一失败即停，不实施、不生成下一阶段任务卡）
- [ ] `git log --oneline -7` 确认 Phase 0–5 已提交：5b88634、50fb9d7、0f144a9、f9b0883、f34495d，
      且 HEAD 为 Phase 5 提交（`refactor(sehd_rise): phase 5 — experience memory single source, HTC-TES demoted to advisory typing`）
- [ ] `git status` 干净（无未提交的 Phase 6 之前产物）
- [ ] `sehd_rise/planning/` 只有空白 `__init__.py`（Phase 0 骨架，本阶段从零生长）
- [ ] 计划文档含 Phase 2/3/4/5 实施记录小节（证明接续链正常、记录模式可循）
- [ ] 源文件行数与计划一致：reflection_engine.py=1,595、policy_reflection.py=414、
      experience_retriever.py=401、negative_feedback_analyzer.py=252（另从 evolution_runner.py 剥离
      feedback/candidates/search 函数族）
- [ ] 工作目录 = 仓库根目录；`python tools/parity/check_layers.py` 可运行且当前通过

## 1. 迁移映射（源文件只读，目标 sehd_rise/）
| 源（src/sehd_v6/core/，只读） | 目标 | 方式 |
|---|---|---|
| `bccse/reflection_engine.py`（1,595 行） | `planning/l1.py` | 平移 + 接口重接 |
| `bccse/policy_reflection.py`（414 行） | `planning/l2.py` | 平移 + 接口重接 |
| `bccse/experience_retriever.py`（401 行）+ `bccse/negative_feedback_analyzer.py`（252 行） | `planning/retrieval.py` | 合并 |
| `evolution_runner.py` 的 `_build_mutation_retry_feedback`/`_polarity_retry_hint`/`_build_structural_system_constraints`/`_structural_retry_hard_constraints`/`_classify_rejection_stage`/`_step_rejection_reasons`/`_build_rejection_summary`/`_structural_rejection_retryable`/`_structural_reject_category`/`_structural_hard_constraint` | `planning/feedback.py` | 平移 |
| `evolution_runner.py` 的候选预校验族（`_normalize_candidate_from_repair_plan`/`_prevalidate_candidate`/`_filter_policy_candidates_by_gold`/`_generation_module_requirement`/`_candidate_direction_signature`/`_rejected_proposal_duplicate`/`_policy_rejection_signature`/`_episode_matches_failure`/`_allowed_l1_modules`）与搜索启发式（`_abstention_ladder_rung_complete`/`_trim_contrast_registry`/`_select_contrast_neighbor`/`_contrast_inversion`/`_signal_polarity_pool`/`_signal_process_modules`/`_signal_process_owner`） | `planning/candidates.py`（预校验）+ `planning/search.py`（对比/弃权阶梯） | 平移 |

- planning/l1.py：LLM 因果反思主路径 + 模板回退；输入从"多投影拼接"改为 CausalFailureEpisode 的
  prompt-safe 投影（`causal.episode.project_for_prompt`）+ RepairPlan 候选槽；import 改指
  `causal.*`/`skills.*`。
- planning/l2.py：闭词汇政策候选规划；候选空间由 `skills/vocabulary/registry.py` +
  `skills/vocabulary/evolution.py` 提供。
- planning/retrieval.py：结构化经验检索（消费 `experience/memory.py` 的机制索引 `retrieve`）+
  拒绝模式洞察，同为规划器的 prompt-safe 输入构造。
- planning/feedback.py：十个重试/拒绝反馈函数整体收编；纯函数，只消费 GateEvent 投影与候选记录。
- planning/candidates.py + planning/search.py：从 god object 剥离，逻辑原样，import 重接。

## 2. 防丢失清单（逐行勾销后方可提交）
- [ ] reflection_engine 的"多投影拼接"输入构造 → 改为消费 CausalFailureEpisode 的 prompt-safe 投影
- [ ] 两个规划器不再各自解释 failure 语义（统一消费 RepairPlan 协议，产出 RepairPlan 候选）
- [ ] experience_retriever 的经验检索 → 改指 `experience/memory.py` 的机制索引（不另存副本）
- [ ] 从 evolution_runner.py 剥离的函数族不得在旧 god object 留残渣副本（pristine：不留残渣）
- [ ] 删除项不得以任何别名/死代码残留于新包

## 3. 纪律约束（不可违反）
- 不修改 src/sehd_v6/ 任何文件；不分派 subagent；全程主会话直接执行。
- 包内一律绝对导入：planning 层
  `from sehd_rise.planning.x import ... / from sehd_rise.platform... / from sehd_rise.skills...
  / from sehd_rise.causal... / from sehd_rise.detection... / from sehd_rise.experience...
  / from sehd_rise.typing...`。
- 分层依赖：planning → platform, skills, causal, experience, typing（planning 在 typing 之上；
  下方各层永不 import planning）。
- 本阶段无语义变更（C5 已在 Phase 5 落地；planning 统一消费 RepairPlan 协议是 C6 协议定义
  在 Phase 4 的下游消费，非新语义变更）；凡故意偏离旧判定处必须能引用纲领条款。
- 移植即重命名：无 _old/_new、无版本号后缀、无兼容别名（pristine 上线初稿）。
- 注释只写 why；pristine-scan 信号词命中逐个收敛，保留误报必须写收敛记录（模式见 Phase 2/3/4/5）。

## 4. 验证（全绿才可提交）
1. 结构：`python tools/parity/check_layers.py`；全包 import 冒烟 `python -c "import sehd_rise"`
   （不只本阶段新增模块，§3.4 DoD）。
2. S1 维持证据：
   `grep -rn "from sehd_rise.typing\|from sehd_rise.planning" sehd_rise/experience sehd_rise/skills
   sehd_rise/causal sehd_rise/detection sehd_rise/platform` → 无命中。
3. 规划器 prompt 构造的金样本比对：同一 CausalFailureEpisode 夹具 → 新旧 prompt 关键段落一致、
   候选 schema 一致 —— 新建 `tools/parity/compare_planning.py`（沿用 compare_causal.py 的
   同进程双包夹具驱动模式）。
4. 候选预校验：对同一组合法/非法候选的判定与旧包一致（compare_planning.py 内）。
5. `node ~/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise` 收敛。
6. 暖缓存回放不可用（§3.3 R2）→ 降级为离线契约校验（schema/序列化/确定性 prompt 比对），
   提交信息与实施记录双重显式记录降级，不允许静默跳过。

## 5. 提交 + 回写（顺序执行）
1. git commit，格式：
   `refactor(sehd_rise): phase 6 — planning layer on RepairPlan protocol`
   正文含：迁移/新建源文件清单、旧逻辑去向勾销表、验证证据（prompt 金样本比对 / 候选预校验一致 /
   S1 维持 / 降级说明）、与旧包行为偏差（引用 §5 风险编号）。
2. 提交后回写计划文档「Phase 6 实施记录」小节（模式严格照抄 Phase 2/3/4/5 已有条目）：
   落地裁决、验证证据摘要、降级说明、纯净扫描收敛记录。回写内容并入本阶段提交
   （沿用 f34495d / Phase 5 做法：文档随阶段提交入库）。
3. 更新 zcode 记忆条目 sehd-rise-refactor-plan 的「下一阶段」状态行（一行索引，状态本体在仓库）。

## 6. 交接协议（用户手动递推，不用定时任务）
本卡由用户在新会话手动粘贴启动（Phase 6）。完成第 5 节后按下列步骤为 Phase 7 生成下一阶段任务卡，
由用户手动粘贴递推；Phase 10 完成后停止。

### 6.1 生成下一阶段任务卡
以 `SEHD_RISE_Refactor_Execution_Plan.md` §Phase(N+1) 为唯一内容源，套用本卡第 0–7 节
结构生成 `SEHD_RISE_Phase(N+1)_Prompt.md`（仓库根目录，随本阶段提交入库）。变更点：
- 第 0 节 commit 清单追加本阶段哈希
- 第 1/2/3/4 节按文档对应小节填充
- 第 5 节提交信息用文档原文
- 第 6 节递推到 N+2；Phase 10 改为「不生成下一阶段任务卡，第 7 节输出最终验收汇总」
- 第 7 节第 4 项改为「下一阶段任务卡文件名 + 全文已置于代码块供用户复制」

### 6.2 交付下一阶段启动 prompt（用户手动粘贴）
任务卡文件生成并提交后，**不调用 CronCreate**（用户拍板：手动递推，不用定时任务）。在最终回复（第 7 节）中：
1. 报告 `SEHD_RISE_Phase{N+1}_Prompt.md` 已生成并随本阶段提交入库；
2. 将该任务卡全文置于一个代码块内供用户复制（外层用 4 反引号包裹，使内层 3 反引号不破坏可复制性）；
3. 用户在新会话粘贴该全文启动 Phase {N+1}。

### 6.3 故障停止（不递推坏状态）
- 第 0 节前置校验失败 → 停止，不提交，报告故障项，由用户介入。
- 第 4 节验证未全绿 → 不提交，报告失败项与证据。
- 任务卡文件未生成或未提交 → 报告错误（下一会话读不到文件）。

### 6.4 终止条件
Phase 10 完成后不生成下一阶段任务卡；第 7 节输出最终验收汇总（各阶段哈希、C1–C10 语义变更落地清单、
parity 报告、纯净扫描终验），重构结束。

## 7. 最终回复格式（严格）
1. 完成声明：各验证项证据摘要 + 证据输出文件/命令原文。
2. 提交哈希 + 勾销后的防丢失清单。
3. 降级记录（若有）。
4. 下一阶段任务卡交付确认：`SEHD_RISE_Phase7_Prompt.md` 已生成并入库。
5. `SEHD_RISE_Phase7_Prompt.md` 全文（置于代码块内，供用户复制；下一会话由用户手动粘贴启动）。
