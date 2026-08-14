# Wave 2 前置设计：语义执行的三份门控设计

> 门控文档（SEHD_semantic_evolution_refactor_plan.md §4）。D1-D3 全部过门才允许进入
> Wave 2 实施（T2.1-T2.6）；每条过门条件给出将落地为回归测试的机械验证点。
> 核查基准：commit `e30840a` + Wave 0/1 改动（T0.1/T0.2/T1.1/T1.2）。

---

## D1 与 2026-08-13「删 prose steering」决策的和解

### 当时删的是什么

2026-08-13 决策（L1 生成性 opcode 全删、执行模型 prose 注入 → 确定性后处理）删除的
退化形态是：**模型直接书写运行时语义**——自由 prose 规则被注入 provider prompt，
效果归因依赖 provider 自报（"我应用了该规则"），无法验证、无法复现、无法归因。
确定性后处理修复了归因：效果由 court 重算，无需 provider 自报。

### Wave 2 要引入的是什么

route guidance 注入：一个 condition 命中的 skill 被编译为**目标 provider route 的
局部指导块**，provider 在 guidance 下重做自己的验证工作，产出仍走原 validator +
原 proof court 重算终局。这与被删除的形态有四条结构性差异，每条落一个回归测试：

1. **guidance 从类型化契约编译，不是自由 prose。**
   skill 的语义字段是四元组（failure_mechanism / applicability / local_guidance /
   expected_local_effect），applicability 是封闭词表的 ConditionSpec（T1.1 单真源
   表），local_guidance 是面向目标 route 的处理原则文本，经过 §3 防火墙编译。
   不存在"模型写任意文本进 prompt"的通道。
   → 测试：`tests/...::test_injection_payload_carries_only_typed_contract_fields`
   （T2.4 落地，断言注入块可由 ConditionSpec + 封闭字段序列化重造，无自由文本键）。

2. **效果归因走确定性 court 重算 + claim 级内容指纹，不依赖 provider 自报。**
   replay 产出的证书/候选/覆盖状态由 `court/identity.py` 的
   `certificate_claim_fingerprint` / `surviving_carrier_fingerprints` /
   `certificate_carrier_movement` 比较（fitness 已消费这些指纹），target edge 是否
   移动由指纹差异裁决，provider 从未被询问"你是否应用了 guidance"。
   → 测试：T2.6 漂移归因稳定性测试（枚举顺序扰动下归因结论不变）。

3. **guidance 文本过编译期防火墙，与被删形态同强度。**
   注入面复用并扩展现有机制：`_ACTION_POISON_RE`（skills/rule.py）、
   `FINAL_VERDICT_RE` / `FORBIDDEN_CONTENT_MARKERS` / verdict 白名单
   （skills/vocabulary/contracts.py）。编译期拒绝一切携带 verdict 指令、
   evidence/certificate 构造指令、gold 字样、case 身份的 guidance，
   拒绝理由是封闭词表。
   → 测试：§12.4 防火墙测试的注入面副本（"Mark this example hallucinated" /
   "Create a certificate" / gold 字样全部编译期拒绝）。

4. **接受门不动：replay + fitness + canary 一个不少。**
   guidance 路径的产物与确定性 opcode 路径走同一个 shadow 批次、同一个 fitness
   评估、同一个 canary 池；route 注入只改变"候选如何被执行"，不改变
   "编辑如何被接受"。
   → 测试：§12.1 runner 级集成测试（全链到达 fitness/memory）+ 现有
   canary/极性门回归套件保持不变。

**若以上任何一条无法满足：不实施 Wave 2**，退回课程路径（curriculum 记录缺口，
人工扩确定性 opcode）。

---

## D2 RouteAddressability 权威设计

### 问题

删除 registry 作为 learning admission 后（T2.1），"存在可 replay 的干预点"需要
一个封闭权威表，否则实施时会即兴再造一个闭集。

### 权威表（落在 `causal/` 层，T2.1 实施时建立）

表的键是 failure edge 的 owner（cut.py 的可进化 owner 词表 = 6 个 L1 module），
行内容：

| owner | producer_route | replay_scope（冻结重放链） | binding_domain | observable_postcondition |
|---|---|---|---|---|
| candidate_trigger | construct_census_route | census → qualification → propagation → focused_prosecution → defense → court 重算 | claim_anchor / candidate | census 候选集变化（指纹比较） |
| candidate_to_verifier_route | qualification_route | qualification → propagation → focused_prosecution → defense → court 重算 | candidate | 目标候选 qualification_state 变化 |
| causal_link_test | propagation_route | propagation → focused_prosecution → defense → court 重算 | candidate / origin | 传播覆盖与依赖边变化 |
| atomic_verifier_instruction | focused_prosecution_route | focused_prosecution → defense → court 重算 | certificate | 目标证书 audit decision/字段变化 |
| evidence_obligation | focused_prosecution_route | focused_prosecution → defense → court 重算 | certificate / coverage | error_audits / 证书指纹变化 |
| earliest_origin_tiebreak | boundary_route | boundary → court 重算 | origin / boundary | boundary_path state / responsible_event_id 变化 |

设计依据：

- **route 侧封闭词表**是 detector 的 9 个命名 route
  （`detector.py:26-36` REQUIRED_ROUTES）。上表只用其中 6 个；
  `candidate_fallback_route` / `appeal_prosecutor_route` /
  `appeal_defender_route` 不承接任何 L1 owner（appeal 机制的 cut owner 是 L2
  artifact，走 T0.1 修复后的 registry 进化路径，不是 provider route 注入）。
- **冻结重放链的存在性证明**是 `detection/continuation.py` 的 constructive path：
  它已经从"候选已构建"点起冻结重放 qualification → propagation →
  focused_prosecution → defense → court 重算。D2 把同一机制泛化为按 route 的
  进入点选择（T2.5），不新建执行引擎。
- **observable_postcondition** 是 D3 指纹比较的断言对象：replay 后该后条件必须
  可由指纹/封闭状态词表判定，不接受 provider 自报。

### 不变量（落地为测试）

1. cut.py 的每个可进化 owner（MODULE_IDS 六模块）必须映射 ≥1 route；
   映射断裂而 owner 被声明为可进化 → `UNADDRESSABLE_FRAMEWORK_DEFECT`
   （框架缺陷，不是能力缺口）。
2. 某因果边既无 route 映射又无确定性后端 → 才是真 `CAPABILITY_GAP`
   （T2.1 的收紧语义）。
3. L2 artifact owner 不进入此表：它们的修复面是 adjudication vocabulary，
   由 registry 进化承接；混入 route 注入会模糊两层权威。

---

## D3 漂移容忍归因设计

### 问题

实验已实证同一冻结检测器两次采样终态不同（BFCL_001：15 例并发判
verified_error、50 例单跑判 unresolved）。route 注入后"target edge changed"
可能是 guidance 起效，也可能是采样漂移。没有漂移防线，§8 全部漏斗指标被污染，
进化会把噪声固化为 skill。

### 三条规则（T2.6 实施）

1. **edge 身份一律用 claim 级内容指纹比较，禁用位置 id。**
   `court/identity.py` 的 `certificate_claim_fingerprint`（声明内容哈希，与
   证书枚举顺序无关）已在 fitness 消费；replay 前后 target edge 的"变化"断言
   只对指纹集合做，位置 id / 枚举顺序不参与任何比较。
   → 测试：同一结果集两种枚举顺序下，movement 判定逐元素一致。

2. **terminal 状态变化作为接受证据时必须决定性复核。**
   候选 replay 终局与 base 不同 → 接受前同配置重放 ≥2 次且结论一致，
   或存在指纹级证据变化（新证书指纹出现 / 旧指纹消失且语义匹配 target
   edge）。单次 terminal 翻转永不单独构成接受证据。
   → 测试：模拟"第一次 replay 翻转、第二次回到 base"的漂移序列，
   接受门必须拒绝。

3. **预算按冷调用核算。**
   每次语义尝试 = 目标 route 起全程 provider 冻活调用；funnel census 实证
   并发 >2 死于 detector 层 → 进化实验串行或 ≤2 并发。
   `PACA_GLOBAL_CACHE_ROOT` 的 warm replay cache 只对 base replay 有效；
   guidance 改变 prompt → mutation cache 对每个候选失效 → 每个候选按
   冷调用预算。在 §1.3 基线上先测单 attempt 均价，再定每轮实验的
   candidate 预算（§9.3）。

---

## 过门检查单

- [ ] D1 四条差异各有回归测试并在 CI 通过（T2.4/T2.6 落地后勾验）
- [ ] D2 权威表建成，不变量 1-3 各有测试
- [ ] D3 三条规则各有测试；指纹比较在 fitness/effect 全面取代位置 id
- [ ] 任一环节 `UNADDRESSABLE_FRAMEWORK_DEFECT` 上升视为 D2 表不完整，
      先补表再继续（§9.5 判定纪律）
