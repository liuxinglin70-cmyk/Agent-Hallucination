# §28.6 未接线修复：候选存档与组件库（2026-09-23）

**结论：工程闭环通过。** 两个"存储可用但无运行路径写入"的存储已接线，写入时机是一个
**显式声明的策略**而不是隐式行为，且**默认为关闭**，因此接线本身不改变任何既有运行的
记录内容。

## 本轮必须首先报告的一件事：我违反了"不运行真实服务"的红线

**事实**：本包的测试初稿未设置 `proposer.mode=offline`。`preview_cycle` 会调用
proposer，而默认模式是 `llm`，因此测试在无人批准的情况下**向真实服务发了 4 次调用
（约 50,085 tokens）**，全部成功返回（`model: glm-5.3-flash`）。

**证据**：`reports/latest/` 之外的三份 preview 报告保留了完整计量，例如
`pytest-current/archive_wiringcurrent/work/reports/proposal_preview_cycle001.json`：
`{"provider_called": true, "provider_ok": true, "model": "glm-5.3-flash", "usage": {"total_tokens": ...}}`。
去重后为 4 次（同一测试被跑了 3 轮，报告内容不同，故非重复计数）。

**为什么会发生**：mock backend 不等于 mock proposer。`EvolutionEngine` 的
`proposer.mode` 默认为 `llm`，既有测试（如 `test_engine_smoke.py`）都是**显式注入**
`OfflineProposer()` 或显式设置 `mode=offline`，我的初稿漏了这一步，而漏掉之后没有任何
东西会报错——它只是变慢（每次预览 ~40-230 秒），慢被误读为"patrol 慢"。

**已做的修复**（三层，逐层收紧）：

1. 测试内显式设 `config["proposer"]["mode"] = "offline"`。该文件用时从 **312 秒降到
   0.43 秒**——这个比值本身证明此前 5 分钟全部是真实服务延迟。
2. 新增 `evolution/tests/conftest.py`：autouse fixture 拦截
   `detector.provider._isolated_transport`、`provider_call` 与 `urllib.request.urlopen`，
   **任何**测试内的真实 HTTP 交换都直接以 `AssertionError` 失败并给出修复提示。
   已用探针脚本验证该守卫确实拦得住（2/2 通过）。
3. 守卫的 escape hatch 为 `@pytest.mark.allow_live_provider`，仓库内**无人使用**：
   真正需要服务的是一次"运行"，不是一次"测试"，它属于 `reports/` 下的脚本与记录。

**未做**：未删除那 4 次调用的报告（它们是这次违规的证据）；未向用户申请豁免。
**本轮随后未再发生真实调用**：全套测试耗时回到 52 秒量级。

## 做了什么

| 交付物 | 文件 | 内容 |
|---|---|---|
| 策略声明 | `evolution/config.py` | `archive.on`：`off` / `development_end` / `cycle_end`，**默认 `off`** |
| 写入方法 | `evolution/engine.py` | `archive_development_run()`：逐版本归档 + 组件登记 |
| 接线 | `evolution/cli.py` | `develop` 结束时调用（caller 拥有 run 边界） |
| 命令 | `python -m evolution archive [--ingest]` | 查看两个存储 / 归档一次开发运行 |
| 测试 | `evolution/tests/test_archive_wiring.py`（11 项） | 见下 |
| 离线守卫 | `evolution/tests/conftest.py` | 禁止测试触网 |

### 写入时机：为什么是"开发循环末"而不是"cycle 末"

NEXT_TASK 要求"接线前先与我确认由谁在何时归档"。我无法与用户对话，因此**没有自行
决定**，而是把这个问题变成**配置项**：调用方声明 `on`，代码不替它选。

选择的语义写进了 `config.DEFAULTS` 的注释：

| 值 | 含义 |
|---|---|
| `off` | 无运行路径写入（默认：既有运行行为不变） |
| `development_end` | 一次开发运行结束时归档它的**全部版本**（通过与否） |
| `cycle_end` | 每个 cycle 归档一次，使一轮里开发过的多个机制集中在一处 |

`develop` 走的是 `development_end` 语义，落点在 `cli.cmd_develop`：把写入放在 CLI 而
不是 `CandidateDeveloper.develop()` 里，因为**调用方拥有 run 边界**——同一个机制可以被
反复 develop，若每次 `develop` 都写一次，一个机制的实验会被记成多个 run。

### 归档内容的判据

- **归档全部版本，通过与否**（§9.1 明确要求含失败版本），失败版本的
  `failure_reason` + `reason_codes` 一并记录——那正是"我们试过这条路"要回答的问题。
- **组件只来自通过 L1 的版本**：`freeze()` 只能从 `ready_for_freeze` 进入并把状态改写为
  `frozen`，所以接受这两个状态正好是"循环认证过的版本"，而 `failed_l0` / `failed_l1`
  两者都不是。局部有用性是组件所声称的全部内容，而 L1 是本框架唯一能确立它的读数。
- **组件 = 内联 typed-DSL 程序**（沿用 §26.3 的定义）：唯一同时具备稳定摘要、封闭词表、
  无自由代码、且有两个消费者的对象。内建确定性断言是框架代码而非本次运行的产物；
  prose directive 没有摘要。

## 读数

复现：`python reports/archive_wiring_20260923/verify_archive_wiring.py`（离线、零 provider、
临时目录），完整输出见 [verify_output.txt](verify_output.txt)。

| 项 | 读数 |
|---|---|
| 默认策略 | `archive.on = 'off'`，`archive.root = ''` |
| 策略为 off 时 | `archived=False`，`reason` 说明如何开启；两个存储均为空 |
| 通过版本 | 1 个候选（`status='frozen'`，机制 `M-17aad6d46f7a`） |
| 其组件 | `evo_delivery_probe`，`kind=parser`，`capability='control_flow'`，digest 已生成 |
| 失败版本 | 1 个候选（`status='failed_l1'`），**组件 0 个** |
| 检索往返 | `retrieve_components(capability='control_flow')` → 命中该组件 |
| 报告权威性 | `metric_role='diagnostic-only'` |
| 存储 → registry 通路 | 不存在（源码级断言） |

## 过程中修掉的三个真实缺陷

**1. `freeze()` 之后 `ready_for_freeze` 不复存在（活缺陷）。** 组件推导初版只接受
`ready_for_freeze`，但 `freeze()` 把它改写成 `frozen`——于是**已冻结（即已通过 L1）的
版本一个组件都登记不上**，而冻结正是正常路径。测试与复现脚本都以 `components: 0` 直接
暴露了这一点。修复：接受 `{"ready_for_freeze", "frozen"}`，并把"为什么正好是这两个"写进
docstring。

**2. 组件的 capability 键恒为空（活缺陷）。** `_component_capability` 从
`self.cycle_reports` 里查机制，而 `develop` 路径**从不填充该属性**，所以每次查询都返回
`""`。后果不是报错，而是**静默降级**：组件被登记为未分类，在
`retrieve_components` 的排序中被 capability 匹配（+3 分）压过，长期运行中会逐渐沉到
prompt 上限之外。修复：改为 `component_capability(mechanism)` 静态方法，直接接收机制；
落点注释记录了旧写法为何错。

**3. 测试触网（活缺陷，见上）。**

## 测试为何这样组织

- **一个 patrol 服务整个模块**：patrol 是这个 harness 里最贵的一步，且**不依赖** mock 的
  per-candidate override（override 只改变开发重放的读数）。初版每个测试跑一次 patrol，
  11 个测试要 5 分钟；改为 module-scoped fixture 后 0.47 秒。
- **每个 run 有自己的 registry 与 store 根**：共享注册表会让第二个 run 的候选在
  STRUCTURAL 身份检查下与第一个重复；共享 store 根会让"这次运行归档了什么"读到兄弟
  测试的写入——该缺陷在开发过程中真的出现了（`test_a_failing_run_...` 单独跑通过、
  在模块内失败）。
- **结构性断言而非文字断言**：`archive` 模块的源码里不得出现 `ActiveRegistry` 或
  `from evolution.registry`，`__all__` 里不得出现 `promote` / `admit` / `make_registry`。
  三层分离是可执行属性，不是文档句子。
- **幂等性**：同一 run 归档两次，候选与组件计数都不变（`archive_candidate` 按
  `(mechanism_id, candidate_hash)` 去重）。

## 未做

- **未自行决定归档时机**：如上述，时机是配置项，`off` 为默认。用户若要固定策略，只需在
  配置里写一行；代码不替用户选。
- **未把 `cycle_end` 接进 `cycle`**：策略值已声明并被校验，但 `_run_cycle` 尚未调用
  `archive_development_run`——cycle 侧当前不产生开发 run 目录，接线需要先定义"cycle 末
  归档哪些 run_dirs"，那又是一个设计决策。**因此 `cycle_end` 目前是已声明但未接线的值**，
  这是本包明确留下的缺口，不是遗漏。
- **未生成组件**：本包登记的是开发循环**已经产出**的 typed-DSL 程序。没有"由 LLM 生成
  组件源码"的路径（与 P2.2 的说明一致）。
- **未改任何门限**、未运行真实服务（除上述意外并已修复）。
- **未清理 `artifacts/` 中历史运行目录**（`selfevo-bigrun*`、`promotion-*`）：它们不是本
  次运行产生的，按 §28.5 第 7 条只清理本次产物。

## 复现

```bash
python -m pytest evolution/tests/test_archive_wiring.py -q   # 11 passed, 0.5s
python -m evolution archive --root artifacts/evolution/candidates
python reports/archive_wiring_20260923/verify_archive_wiring.py
```
