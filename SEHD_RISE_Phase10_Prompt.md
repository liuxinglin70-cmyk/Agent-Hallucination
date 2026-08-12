# 交接任务卡：SEHD-RISE Phase 10 — api/cli 门面 + 端到端 parity 验收（终态）

> 用法：本卡由用户在新会话手动粘贴启动。本卡 + 仓库 = 全部所需上下文；
> 9b 的结论不在对话里，而在 9b 提交（`refactor(sehd_rise): phase 9b …`）与
> `SEHD_RISE_Refactor_Execution_Plan.md`「Phase 9b 实施记录」小节里。
> 本卡为最后一阶段，提交后仅剩全包验收报告，不生成下一卡。

## 0. 前置校验（30 秒；任一失败即停，不实施）
- [ ] `git status --short` 恰好只含未跟踪的 `SEHD_RISE_Phase10_Prompt.md`（9b 已提交，工作区干净；若 9b 未提交则先回 9b 卡）
- [ ] `python -c "import sehd_rise.loop.runner"` 通过
- [ ] `python tools/parity/test_loop_machine.py` 全过（10/10）
- [ ] `python -m tools.parity.e2e_loop` 4 场景全绿
- [ ] `node ~/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise/loop` → 0 命中
- [ ] `python tools/parity/check_layers.py` 绿（76 文件/13 层）

## 1. 已完成（9b，已提交）
- `loop/runner.py`（4633 行）：C6 合流 L1/L2 为一条编排主路径；C2/C9/C10 信用链；C8 终态 `gate_decision` 写 Outcome 值；funnel resume 重建（steps 投影，检查点等价）。
- `evaluation/promotion.py`：`promote()` 扩决策集识别 Outcome 字符串（[W1]）。
- `loop/state.py` + `loop/probes.py`：`meta_skill_state` → `strategy_state`（[W3]）。
- `tools/parity/e2e_loop.py`（284 行）：4 场景 e2e（C2 unaddressable / C9 INFRA_FAILURE / C9 BUDGET_EXHAUSTED / 检查点 resume 等价；真实 provider）。
- `tools/parity/compare_detection.py`：移除 warm-cache replay，以 offline 契约（74 checks）为检测层 parity 准则。
- 9b 验证证据见计划文档「Phase 9b 实施记录」§4。

## 2. 迁移映射（计划文档 §Phase 10）

| 源 | 目标 | 方式 | 备注 |
|---|---|---|---|
| `evolution/runner.py`（381 行，`run_evolution`/记录加载/继承校验） | `api/evolution.py` | 平移 + 接口重接 | `resume_from`/`inherit_from`/记录加载语义保持；重接 `loop.runner.EvolutionLoop` |
| `evolution/state_manifest.py` | `api/manifests.py` | 平移 | 状态清单哈希绑定 |
| `detector/__init__.py` 的 facade 职责 | `api/detection.py` | 平移 | `detect_record`/批量/副本聚合的公共出口 |
| `evolution/__main__.py` | `cli/evolve.py` | 平移 | 参数面保持一致 |
| `detector/__main__.py` + `paca_tcl/detector.py` 的 `main()` | `cli/detect.py` | 合并 | |
| 新增 | `cli/__main__.py` | 新建 | `python -m sehd_rise <evolve\|detect>` 分发 |

## 3. 关键裁决（9b 已定，Phase 10 延续）
1. **真实 provider 端到端 parity**（9b [R2 更新] 延续）：暖缓存命中非自进化目标——自进化改 skill rule / mutation 使 provider 请求必然变化，缓存命中证明与自进化正确性无关。Phase 10 `compare_run.py` 用真实 provider 跑端到端比对，不依赖暖缓存。计划文档 §Phase 10「同一夹具案例流 + 暖缓存」表述需回写为「真实 provider」。
2. **C1-C10 差异解释**：parity 比对允许且必须解释因 C1-C10 产生的 outcome 分类与统计口径差异，逐条对照语义变更点编号注释；除 C1-C10 外行为等价。
3. **`src/sehd_v6/` 冻结铁律**：旧包一字不改；文档（README / TECHNICAL_DESIGN）可改框架指向。
4. **api/cli 分层规则**：`api` 门面层可 import `loop.runner`（编排入口）与各层公共出口；`cli` 只经 `api`，不 import loop 内部。维持 S1 与 `check_layers` 规则。

## 4. 未完成清单（按序执行，逐行勾销后提交）
- [ ] `api/evolution.py`：平移 `evolution/runner.py` 的 `run_evolution`/记录加载/继承校验，接口重接 `loop.runner.EvolutionLoop`。
- [ ] `api/manifests.py`：平移 `evolution/state_manifest.py`（状态清单哈希绑定）。
- [ ] `api/detection.py`：平移 `detector/__init__.py` 的 facade 职责（`detect_record`/批量/副本聚合公共出口）。
- [ ] `cli/evolve.py`：平移 `evolution/__main__.py`（参数面一致）。
- [ ] `cli/detect.py`：合并 `detector/__main__.py` + `paca_tcl/detector.py` 的 `main()`。
- [ ] `cli/__main__.py`：新建 `python -m sehd_rise <evolve|detect>` 分发。
- [ ] `tools/parity/compare_run.py`：同一夹具案例流，分别经 `src.sehd_v6.evolution.run_evolution` 与 `sehd_rise.api.evolution.run_evolution` 运行（真实 provider）；比对 accepted 编辑的 target module/operation 集合、经验记忆机制签名计数、artifact schema（`evolution_result.json`/`champion_genome.json`/`htc_tes_experience_memory.json`/`evolution_state_manifest.json` 字段齐全）；C1-C10 差异逐条注释。
- [ ] 回写计划文档 §Phase 10「暖缓存」→「真实 provider」。
- [ ] 防丢失勾销：api/cli 不在 src 留副本；门面层不 import loop 内部模块（仅 `loop.runner`）；无别名/死代码残留。

## 5. 验证（全绿才可提交）
1. `python tools/parity/compare_run.py`：端到端 parity 全绿（真实 provider；C1-C10 差异逐条解释）。
2. CLI 冒烟：`python -m sehd_rise evolve --help`、`python -m sehd_rise detect` 夹具单条跑通。
3. `python tools/parity/check_layers.py` 绿（门面层加入后层数更新、无违规）。
4. 全包冒烟：`python -c "import sehd_rise.api.evolution, sehd_rise.api.detection, sehd_rise.api.manifests, sehd_rise.cli.evolve, sehd_rise.cli.detect, sehd_rise.cli"`。
5. S1 维持：`grep -rn "from sehd_rise.loop" sehd_rise/{api,cli}` → 仅 `api/evolution.py` 可 import `loop.runner`（门面接线），cli 不 import loop 内部。
6. `node ~/.zcode/skills/pristine/scripts/pristine-scan.js sehd_rise/api sehd_rise/cli` → 0 命中。
7. 更新 `README.md` 与 `docs/design/TECHNICAL_DESIGN.md` 框架指向（`src/sehd_v6/` 代码不动）。

## 6. 提交 + 回写
1. `git commit`，格式：
   `refactor(sehd_rise): phase 10 — public api/cli, end-to-end parity acceptance, docs`
   正文：门面迁移映射、端到端 parity 证据（compare_run 真实 provider / C1-C10 差异解释）、CLI 冒烟、结构/pristine/S1 验证、文档更新。
2. 回写计划文档「Phase 10 实施记录」小节（模式照抄 9a/9b 条目）。
3. 更新 zcode 记忆条目 `sehd-rise-refactor-plan` 状态行（Phase 10 已提交 + 哈希；全包验收完成）。

## 7. 交接协议（用户手动递推）
Phase 10 为终态，不生成下一卡。提交后最终回复含全包验收报告：
1. 完成声明：各验证项证据摘要 + 命令原文。
2. 提交哈希 + 勾销后的防丢失清单。
3. 裁决/降级记录（§3 各条 + 9b [R2 更新] 延续）。
4. 全包 parity 报告（C1-C10 差异逐条对照）。
5. 文档更新清单。

故障停止：前置校验失败 / 验证未全绿 → 不提交，报告证据，由用户介入。
