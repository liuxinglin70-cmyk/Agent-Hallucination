# 干预与运行时合同

所有部署通过 `detector/interventions.py`。未知类型、hook、runner、fact 或不满足执行约束的 registry 均拒绝加载。registry 具备内容哈希，不应手改已生成 registry 后继续复用原哈希。

## 四类干预

| 类型 | 作用 | 可写范围 |
|---|---|---|
| `text_boundary` | 增加有适用/排除边界的提示文本 | 已注册 system hook |
| `evidence_check` | 执行内置或 typed DSL 谓词 | 追加机械证据与事实，不直接写终态 |
| `decision_guard` | 根据事实执行受限裁决动作 | `adjudicate.guard` |
| `recall_trigger` | 在 Judge 阴性时触发已有召回网 | `recall.trigger`，不直接定罪 |

## Hook 和写权

| Hook | 层 | 能力 |
|---|---|---|
| `judge.system` | L1 | Judge 提示边界 |
| `judge.post` | L1 | Judge 后机械证据 |
| `recall.trigger` | L1 | 阴性案例召回触发 |
| `scanner.system` | L1 | Scanner 提示边界 |
| `upgrade.pre` | L1 | 升级前机械证据 |
| `upgrade.system` | L1 | 升级提示边界 |
| `tracer.system` | L1 | 定位提示边界 |
| `defense.pre` | L2 | 辩护前机械证据 |
| `defense.system` | L2 | 辩护提示边界 |
| `adjudicate.guard` | L2 | 有证据约束的裁决动作 |

Guard 动作为 `withhold_conviction`、`restore_judge_conviction`、`require_full_view_defense`、`convict_mechanical_violation`。必须提供适用事实与非空排除事实；多 guard 动作冲突时回退底座并记录冲突。机械定罪要求实际的可授权机械证据。

内置谓词：`required_action_present`、`verbatim_fidelity`、`evidence_refs_resolve`、`contract_coverage`、`delivered_anchor_consistency`、`final_delivery_present`。

## 新观察能力：typed DSL

`evidence_check` 和 `recall_trigger` 可以携带 `body.predicate_definition`，runner 为 `typed_dsl`，schema 为 `detector.predicate_program.v1`。运行时注册新的 `evo_...` 谓词、verdict 与 fact；同名不同定义会被拒绝。

程序只能访问 typed 字段和操作符白名单，不执行 eval、import、循环或任意属性访问。最多 16 条规则、6 个 verdict，每条规则最多 4 个 AND 条件；规则按顺序首命中，多条规则可表达 OR。单条件 `field/operator/value` 与 `conditions` 形式不能混写。精确字段与操作符可通过 `python -m evolution capability-audit` 查询。

```json
{
  "conditions": [
    {"field": "question_text", "operator": "contains", "value": "use search"},
    {"field": "tool_names", "operator": "none_contains", "value": "search"}
  ],
  "verdict": "violated",
  "detail": "required search action is absent"
}
```

这是规则片段，不是完整 registry。完整程序须声明 verdict 集合、默认 verdict 和 conviction verdict 子集。结构门会在提案所属的每个失败族成员上检查真实执行结果。

`recall_trigger` 仅在 Judge 成功且阴性时判断触发条件；开启召回后仍要经过因果升级、定位和辩护。它属于需要 fresh 配对评估的干预，不能通过冻结旧 recall 路径掩盖效果。

## API 和追溯

`Detector.detect(record, interventions=registry)` 加载注册干预。`frozen_judge` 与 `frozen_stages` 是评估用重放接口，受干预的阶段不允许被冻结。`proof.deployment` 记录加载、触发、执行证据和冲突；`proof.evidence` 提供机械证据。

当前能力边界是“可注册新 typed 谓词和召回控制策略的受控进化”：不能任意新增 hook、裁决动作、重写执行图、生成任意可执行代码或更新模型权重。
