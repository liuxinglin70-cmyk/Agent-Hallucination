# P2.2 Level B 沙箱组件（2026-09-23）

**结论：工程闭环通过，且资源上限是"被证明"的而非"被声明"的。** 三个上限各由一个真实
子进程越限验证；27 条逃逸路径全部被静态拒绝；新 runner 从 evolution 后端可执行且
digest 校验在真实路径上生效。本记录**不构成**检测能力提升的证据：沙箱产出
`evidence_only`，永不单独定罪。

## 做了什么

| 交付物 | 文件 | 内容 |
|---|---|---|
| 沙箱组件 | `evolution/sandbox.py`（新增，~1500 行） | 静态验证 + 子进程执行 + 上限探测 |
| runner | `detector/interventions.py` | `sandboxed_component`（第 4 个 runner） |
| 单元/属性/变形测试 | `evolution/tests/test_sandbox.py`（41 项） | 见"测试为何这样组织" |
| 准入主体 | `evolution/metamorphic.py` | 该 runner 的 §15.2 五类主体 |
| 可达性接线 | `evolution/backends.py`、`detector/api.py` | 组件从 registry 自动构建执行器 |
| 审计面 | `evolution/capability_surface.py` | `executable_inventory.sandbox` |
| 命令 | `python -m evolution sandbox-check [--probe]` | 退出码即裁决 |
| 固定验证 | `scripts/verify.py` 第 10 项 | `sandbox_check` |

### §10.2 的六条要求与落点

| 要求 | 落地方式 | 强制点 |
|---|---|---|
| 纯函数 | 模块级只允许 import / 字面量常量 / 函数定义；不允许类、循环、`with`、装饰器、模块级计算 | `_check_module_level` |
| 输入 schema 固定 | 参数名与类型在源码读入**之前**声明，`parameters` 必须是非空列表、类型取自封闭集合 | `validate_component` |
| 禁网 | `socket`/`urllib`/`http`/`requests` 均不在导入白名单；子进程内 `__import__` 也被替换为查白名单的版本 | 白名单 + 子进程双锁 |
| 禁任意文件访问 | `open`/`io`/`pathlib`/`os` 不在白名单；`open` 等既不在子进程 builtins 里，也在 AST 层被拒 | `BUILTIN_WHITELIST` + `visit_Call` |
| 导入白名单 | 16 个模块（`re`/`json`/`math`/…），且**源码实际 import 的集合必须等于声明的集合** | `IMPORT_WHITELIST` |
| CPU/时间/内存上限 | Windows Job Object（内存 + CPU 时间）+ 父进程墙钟 + 输出/输入字节上限 | `_apply_windows_limits`、`_run_child` |
| property + metamorphic 测试 | 见下 | `test_sandbox.py` |

## 读数

复现：`python reports/p2_sandbox_20260923/verify_sandbox.py`（只读、离线、零 provider），
完整输出见 [verify_output.txt](verify_output.txt)；`--probe` 版本见
[sandbox_check_probe.json](sandbox_check_probe.json)。

### 1. 资源上限（实测，非声明）

| 上限 | 平台机制 | 探测方式 | 读数 |
|---|---|---|---|
| 墙钟 | 父进程 `communicate(timeout=)` | 子进程 `sleep(300)`，预算 2 s | **enforced**：2.02 s 停止 |
| 内存 | Job Object `PROCESS_MEMORY` | 子进程申请 1 GiB，预算 256 MiB | **enforced**：`MemoryError` |
| CPU 时间 | Job Object `JOB_TIME` | 子进程死循环，预算 1 s | **enforced**：停止 |

平台：`win32`，机制 `windows_job_object`。`admission_blockers` 为空——**若某平台缺任一
上限，`admission_blockers` 会返回理由，`execute_component` 直接拒绝运行**，因为一个
无法约束子进程的沙箱不该放行子进程。这是 `test_admission_is_refused_when_a_limit_cannot_be_enforced`
所钉的行为。

### 2. 逃逸路径

**27 条路径，27 条被拒，0 条泄漏**（`run_forbidden_sources()`）：

| 类别 | 路径（示例） |
|---|---|
| 文件系统 | `open('/etc/passwd')`、`open('out.txt','w')`、`with open(...)` |
| 内省逃逸 | `f.__globals__`、`().__class__.__bases__`、`getattr(f,'x',None)` |
| 危险导入 | `os`、`subprocess`、`socket`、相对导入、未声明导入 |
| 动态执行 | `eval`、`exec`、`__import__`、`f.system('ls')` |
| 结构 | `class`、`global`、装饰器、`async def`、`await`、`yield`、walrus、`*args` |
| 契约 | 元数不符、默认参数、`**kwargs`、模块级计算、未知名字 |

### 3. runner 契约与准入

| 字段 | 值 |
|---|---|
| `supported_hooks` | `judge.post`、`upgrade.pre`、`defense.pre` |
| `intervention_types` | `evidence_check`（**不含** `recall_trigger`） |
| `terminal_authority` | **`evidence_only`**（永不单独定罪） |
| `requires_artifact` | `True`（源码即 artifact，须钉 digest） |
| `deterministic` | `True`（纯函数；非 learned） |
| `metamorphic_classes` | 5 类（§15.2 的 1、2、3、4、6） |

四个 runner 全部准入：`deterministic`、`sandboxed_component`、`typed_dsl`、`typed_semantic`。

### 4. 可达性（本包最容易出错的一点）

`verify_sandbox.py` 第 5 节证明组件**真的能跑**：从 registry 自动构建执行器
（`['evo_sentence_match']`）→ 编译 → 执行 → 产出 `runner="sandboxed_component"` 的
evidence 行；同时篡改 digest 后**被拒**（`refusing to attribute results to an artifact
that did not produce them`）。

这一节存在的原因：前三个工作包各修过一次"新能力到不了运行路径"的缺陷，所以本包在
`evolution/backends.py` 与 `detector/api.py` 都接了线，并为此写了 5 项专项测试。

## 过程中修掉的两个真实缺陷

**1. 子进程编码错误（活缺陷）。** 子进程用 `-I` 启动，而 `-I` 隐含 `-E`：环境变量被
忽略，`PYTHONIOENCODING` 失效，stdin/stdout 回落到 locale 编码。在非 UTF-8 locale 上，
**任何非 ASCII 文本都会在管道上被损坏**——而这些组件的用途正是比较任务文本，中文/中文
标点/弯引号都是常态。表现为 `JSONDecodeError`，看起来像组件或 payload 的错。
修复：在 driver 内显式 `reconfigure(encoding="utf-8")`。有专项回归测试
（`test_non_ascii_input_survives_the_child_boundary`，用"同文同判、异文异判"两句钉住）。

**2. 参考组件未实现自己声明的等价类。** 门的第 5 类（语义等价）声明忽略引号风格，
而参考组件只折叠空白与句末句点，弯引号与直引号会判为不同——**门自己发现了这个不一致**
（`semantic_equivalence: rewriting expected ... changed the judgment`）。修复：把引号
映射加进组件，并让判定器的规范化集合与 `semantic_equivalents()` 声明的集合**逐项对齐**
（不多也不少）。

另外顺手删掉了 `contracts.DETERMINISTIC_RUNNERS` 的手工清单，改为从 `RUNNER_REGISTRY`
派生——它上一包就已被记录为潜伏漂移点，而本包新增 runner 会让它当场失配。

## 测试为何这样组织

单独测"`open` 被拒"不能说明沙箱不透——`f.__globals__` 一样能出去。因此：

- **属性**：量化在整个面上——白名单内每个模块都**真的能绑定**（否则白名单条目会变成
  运行期错误，与"组件写错了"无法区分）；每条禁止路径都被拒且**计数被钉住**（`>= 20`，
  因为对空列表断言"全部被拒"会平凡通过）；上限超顶**拒绝而非截断**（截断等于用另一个
  预算运行未经测试的组件）。
- **变形**：规范化改写不改判定（第 5 类）；参数**按名绑定**，改动声明顺序改变 digest
  但不得静默转置取值；同一输入重复调用结果一致。
- **可证伪**：`test_declared_capabilities_match_the_actual_probe` 要求"声明的能力"必须
  被"探测"复现。平台谎报一个它没有的内存上限，比声明"没有上限"更糟，因为审计引用的是
  声明。

## 关于 §23.7 的前置条件（本包动手前按要求核对）

NEXT_TASK 说 P2.2"动手前先读 §23.7 的前置条件并与我讨论：该能力与'允许生成任意代码'相邻"。
我读了 §23.7，但**无法与用户对话**（本轮无交互），因此**没有自行扩大范围**，而是把边界
写死并留证：

`§23.7` 列的五项前置（graph schema、allowed mutation set、static validation、
execution trace diff、rollback）是 **P3 开放式演化**的门槛——新 hook 生成、图改写、
权重更新、学习型策略路由。**P2.2 不做这些**：

| §23.7 前置 | P2.2 是否触及 | 说明 |
|---|---|---|
| graph schema | **否** | 不生成、不改写执行图 |
| allowed mutation set | **否** | 组件不能改 hook、guard 动作或路由 |
| static validation | **是（已实现）** | AST 白名单 + 导入白名单 + 固定 schema，27 条逃逸路径被拒 |
| execution trace diff | **否** | 组件不产生新执行路径，只在既有 `evidence_check` 钩子上报告 |
| rollback | **不适用** | 组件无法写入 registry；回滚语义属于晋升路径，非本通道 |

即：**"允许生成任意代码"这件事本身没有被打开**。本通道允许的是"生成一个**纯函数**，
其参数 schema 在源码读入前固定、导入来自封闭白名单、无文件系统与网络、由 OS 级
CPU/时间/内存上限约束、且**永不单独定罪**"。`capability_surface` 的自述也按此措辞写成
`True` + 范围说明，而不是无条件 `True`。

`open_ended_self_evolution` 仍为 **`False`**，`open_ended_evolution_blockers` 报出尚未满足的
两项（typed execution-graph artifacts、immutable capability artifact store）。这是本包
**刻意保留**的边界，不是遗漏。

## 未做

- **未接入真实生成器**：本轮没有"由 LLM 生成组件源码"的路径。沙箱、准入、执行、
  可达性全部就绪，但 `proposer` 不会产出 `sandboxed_component` 候选——这需要先决定由
  谁在何时生成，属于与本包独立的设计决策（§28.6 同类）。当前组件只能由调用方显式提供。
- **未改任何门限**：`sandboxed_component` 是**准入 + 执行**通道，四门与定位门逐字未动；
  沙箱裁决标 `admission-only`。
- **未做进程级隔离之外的加固**：沙箱不是对抗"被允许运行任意 Python 的攻击者"的安全
  边界。进程边界、受限 builtins、封闭导入与 OS 级上限是真实的；同进程内的 Python 限制
  原则上可绕过，所以准入的前提是**静态验证通过**，沙箱限制的是"仍然越界"时的伤害。
- **未覆盖 `filesystem_bytes` 上限**：POSIX 上设了 `RLIMIT_FSIZE`，Windows Job Object
  没有等价项，因此该字段声明为 `False`。可执行组件本来就无法打开文件（无 `open`、
  无 `io`、无 `os`），这一项是纵深防御而非主防线。
- **未在 POSIX 上实测**：本机为 `win32`，POSIX 分支（`RLIMIT_CPU`/`RLIMIT_AS`/
  `RLIMIT_FSIZE`）有代码但**未在本轮被验证**；报告不声称它成立。
- **未运行真实服务**：本包全程离线。

## 复现

```bash
python -m pytest evolution/tests/test_sandbox.py -q          # 41 passed
python -m evolution sandbox-check                            # exit 0
python -m evolution sandbox-check --probe                    # 三条上限各被越限验证
python -m evolution metamorphic-gate                         # 四个 runner 全准入
python reports/p2_sandbox_20260923/verify_sandbox.py         # 五节读数
```
