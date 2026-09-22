# P2.3 变形测试 gate（2026-09-23）

**结论：工程闭环通过。** 六类变形关系已固化为**新 runner 的准入判据**，三个已注册
runner 全部通过，三个"确实坏掉"的对照主体全部被拒。本记录**不构成**检测能力或定位
能力提升的证据——gate 不产生任何分数。

## 做了什么

新增 `evolution/metamorphic.py`（§15.2 六类）与 `evolution/tests/test_metamorphic.py`
（42 项），并把判据接入两条准入路径：

| 接入点 | 文件 | 作用 |
|---|---|---|
| `RunnerSpec.metamorphic_classes` | `detector/interventions.py:126` 起 | runner **声明**必须存活的变形类；learned runner 由构造函数强制加入 `semantic_equivalence` |
| `structural_gate` | `evolution/gates.py:256` 起 | schema 检查之后立即拒绝无准入主体的 runner |
| `check_l0` | `evolution/development.py:154` 起 | 开发期同样拦截，避免坏 runner 先花掉一次 L1 重放 |
| `capability_surface_report` | `evolution/capability_surface.py` | `executable_inventory.runner_admission` 报告**执行后**的裁决 |
| `python -m evolution metamorphic-gate` | `evolution/cli.py` | 离线命令，退出码即裁决 |
| `scripts/verify.py` | `metamorphic_gate` 项 | 纳入固定验证流程 |

## 六类与判据

| # | 类 | 变换 | 判据 |
|---|---|---|---|
| 1 | `case_id_rename` | 改案例 id 与成员名 | 机制身份（`mechanism_id`/`group`）逐项相同 |
| 2 | `source_rename` | 改来源前缀 | 同上，且**改名必须真的落地**（报出新旧来源） |
| 3 | `irrelevant_tool_call` | 轨迹中插入无关工具调用 | 特定 checker 裁决不变 |
| 4 | `neighbor_surface_token` | 正常近邻注入规则自身词面 | 规则不得触发 |
| 5 | `semantic_equivalence` | expected/actual 语义等价改写 | typed semantic 裁决不变 |
| 6 | `localization_event_move` | 移动无关 event | 根因关系不变（见下） |

## 读数

`python reports/p2_metamorphic_20260923/verify_metamorphic.py`（只读、零 provider 调用），
完整输出见 [verify_output.txt](verify_output.txt)：

| 项 | 读数 |
|---|---|
| 注册 runner 数 | 3（`deterministic`、`typed_dsl`、`typed_semantic`） |
| 每个 runner 声明的类数 | 5、5、6（`typed_semantic` 多 `semantic_equivalence`） |
| 全部通过 | `all_admitted=True`，`failed=[]` |
| 每类 `mutation_effective` | 全部 `True` |
| 负对照被拒 | 3/3 |
| 裁决权威 | `admission-only` |

**负对照**（"确实坏掉"的主体必须被拒，否则 gate 是空的）：

| 对照 | 结果 | 失败类与理由 |
|---|---|---|
| 关键词匹配器（见到自己的词就触发） | **被拒** | `neighbor_surface_token`：注入规则自身词面后误触发 |
| 逐字相等的语义判定器 | **被拒** | `semantic_equivalence`：改写引号即可翻转裁决 |
| 机制 key 读 case id | **被拒** | `case_id_rename`：机制身份随改名改变 |

## 三条设计要点

**1. 变异必须"有效"，否则算失败。** 每类都报 `mutation_effective`；一个什么都没改的
生成器不能因为"结果没变"而通过。这条不是装饰：`_with_members` 实测在返回基类时会
把被测主体换成基类实现，于是用**未受检的代码**判定通过——已修，并有专项测试
（`test_rename_preserves_the_subject_under_test`）。

**2. 非中性变异一律拒绝，不静默施加。** "无关"是定义出来的，不是假设的：插入文本
经 `_is_inert_text` 检查（不得含 `success`/动作动词/许可句式/数字等任何机械信号），
插入后必须**锚点不变、路由特征集不变**，否则拒绝并报理由。规则自身词面含数字时降级
到惰性子集，降级后无字母数字则拒绝——注入 `", "` 什么也没测却会报通过。

**3. 第 6 类必须给出位置置换。** 移动 event 后"根因在第 N 步"不再是同一个坐标，
所以 `move_irrelevant_event` 返回 `old→new` 置换，判据在**同侧施加同一置换**下比较。
只有第一层读数（记录的 responsible step 不随布局重算）而缺置换，该类是空的。

## 未做

- **未接入真实执行器**：`typed_semantic` 的准入主体是参考实现（规范化比较），不是
  真实学习模型。真实执行器接入时须用**它自己**作为主体跑 `run_gate("typed_semantic", subject)`，
  报告会带上该 artifact 的 digest；这是把 digest 写进 report 的意义所在。当前**没有任何
  真实 learned executor 存在**（`generate_a_learned_executor=False`）。
- **未改任何门限**：`structural_gate` 新增的是**准入判据**，不是门限替代；四门与定位门
  逐字未动，`admission-only` 已写进报告。
- **未运行真实服务**：本包全程离线。
- **未覆盖第 6 类的行级细节**：判据在 `localization_state`（hit/mislocated/…）层面，
  未逐 event 比对 tracer 输出。
- **`contracts.DETERMINISTIC_RUNNERS` 的手工清单未清理**：`evolution/contracts.py:35-38`
  的注释说"等 P2.1 落地后改用查表"，P2.1 已落地而清单仍在。这是**潜伏缺陷**（当前三个
  runner 与清单一致，无活缺陷表现），本包未动它以免与既有缓存键语义纠缠；已记入
  §28.6 类的待接线清单。

## 复现

```bash
python -m pytest evolution/tests/test_metamorphic.py -q   # 42 passed
python -m evolution metamorphic-gate                      # exit 0
python reports/p2_metamorphic_20260923/verify_metamorphic.py
```
