# P1.1 + P1.3：重放边界与 artifact 身份

日期：2026-09-22
范围：`evolution/backends.py`、`evolution/development.py`、`evolution/promotion.py`、`evolution/cli.py`
证据等级：**diagnostic-only**（离线、脚本化，不含任何检测质量或泛化结论）

## P1.1 部分重放边界

**修掉的缺口**：`partial_replay_plan` 是死代码（除归档脚本外零调用者），而重放边界实际由
`LiveDetectorBackend._frozen_stages` 内部的硬编码 hook 名决定。两张表各写一遍，谁也不会因
为对方改动而失败。

**落地**：

- `REPLAY_STAGES`：阶段顺序的唯一拼写。
- `replay_boundary(hooks)`：给定 hook 集合返回 `rerun_stages` 与 `boundary_key`。
  **未知 hook 保守取全流程**——未声明的 hook 无法证明其可达范围为空，假设为零会冻结
  它其实能影响的阶段。
- 结果缓存文件名带上 `boundary_key`：边界逻辑一旦扩展（P1 正是扩展它的地方），旧缓存
  不得为新的边界作答。仅在 registry key 非内容派生时附加，因为内容派生的 key 已经
  蕴含 hook 集合，无条件附加只会挤占文件名预算（Windows 路径上限有专项测试）。
- 开发循环报告 `replay_boundary`：使"开发比重跑整臂便宜"成为可核对的事实。

**没有改门限**：边界只影响"重跑多少"，`requires_fresh_evaluation` 与三张 masking 表的
fail-closed 语义逐字保留。

## P1.3 artifact 身份

**落地**：

- `artifact_digest(path)`：文件或目录的内容摘要，路径不存在时**拒绝**而不是返回
  `"missing"` 哨兵——哨兵会和真值一样参与哈希，"权重被替换成空"正是这样通过检查的。
- `artifact_manifest(*paths)`：`schema_version` + 每件 artifact 的摘要 + 覆盖两者的
  manifest 摘要。空 manifest 合法，表示"该 runner 不声明外部 artifact"，与"声明了但
  读不到"是两句不同的话。
- `source_digest()` 由 `glob("*.py")` 改 `rglob("*.py")` 覆盖子包，并排除 `tests/`：
  测试文件不参与任何一次检测，让它失效所有缓存会把摘要的权威花在它并不描述的东西上。
- `freeze_protocol(..., artifact_paths=[...])` 冻结 manifest；`load_protocol` 与
  `_run_aligned` 逐字节复核。
- CLI `freeze-protocol --artifact`（可重复）。

**§12.1 要求的回归已落地**（`test_promotion.py`）：

- 替换权重 → 协议拒绝加载（`learned artifact changed`）；
- 删除权重 → 协议拒绝加载（`missing`）；
- 不声明 artifact → manifest 为 `{}` 且摘要非空，是记录在案的事实。

## 验证

- `pytest -q`：**680 passed**（本轮新增 5 项：3 项边界、2 项 artifact）；
- `scripts/verify.py`：七项全 PASS；
- 端到端离线复现：CLI `--artifact` 写入的 manifest 含该文件路径与摘要，协议正常冻结。

## 未做

- 未运行真实服务；未创建 holdout；
- P1.2（候选存档 / 组件库）、P1.4（`structured_key` 扩展）、P1.5（校准审计）未开工；
- `boundary_key` 只在有内容派生 registry key 时进入缓存名，因此该分支的缓存隔离
  依赖 registry key 本身；`_content_derived_key` 对此有显式判定与注释。
