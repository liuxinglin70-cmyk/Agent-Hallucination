# Jev / System One 类模型与本项目适配性技术调研

检索与核验日期：2026-09-21。核验对象：TypeSafe AI 的 Jev（2026-09-15 发布）及其同类"类型化决策模型"，包括开源复现、编码器判定头、校准方法论与 agent 轨迹判定文献。

用途：判断这类模型能否提高本项目的检测底座与自进化框架，给出可执行的嵌入位置、前置条件与证伪条件。不作论文新颖性裁决，不引用跨论文收益数字为本项目设目标。

证据标注约定（全文强制）：**〔厂商〕** = 厂商自陈、无第三方复现；**〔实测〕** = 第三方独立测量、有可复现产物；**〔争议〕** = 存在矛盾证据或来源不可靠；**〔未证〕** = 未能证实。本项目的代码与数据读数为直接核验（给出 `file:line`），与外部来源分开计。未复现任何外部实验。

---

## 0. 结论先行

**第一，Jev 这个产品不该进我们的判定路径，但 Jev 这个模型类值得进；而它真正能立刻提高的东西不是判定能力，是测量方式。**

三条判断，按可立即执行的顺序排列：

1. **现在就能做、且攻击的是真实瓶颈**：把我们的 judge 已有的 `confidence`（`high|medium|low`）变成**经过校准审计的概率**，让门从"数翻转"升级为"配对正误评分"。这条完全不依赖任何外部模型，只用归档数据，是本文推荐的第一件事（第 8 节 P0）。
2. **值得建、但还没有需求证据**：给框架加第二个 `runner`——一个冻结的本地类型化判定头。这正是 `capability_surface.py` 自己点名还缺的东西，也是 `evolution/gaps.py` 未来非空的原因。但它的前置件（工件不可变存储、校准门）现在不存在，必须先补（第 8 节 P1）。
3. **明确不做**：把托管 Jev 放进判定路径；以及用学习模型预测配对评估结果来跳过评估——后者会破坏框架"LLM 输出永不作为晋升分数"的根本约定（`evolution/gates.py:1-10`），并正是奖励模型过优化的教科书形态。

关键反直觉事实，两条：

- **Jev 的 `confidence` 不是正确率概率**，而是"领先答案高出均匀分布多少"的离散度 `(K·p_max − 1)/(K − 1)`；`Noul`（布尔）题型干脆不返回 `confidence`〔厂商源码内嵌 + 独立复核〕。也就是说，我们最想从这类模型拿到的那样东西，恰好是它没有的那样东西。它的概率分布 `probabilities` 才是原始信息。
- **框架的约束是测量，不是搜索。** 生成/验证成本比极大（一次 proposer 调用 vs 一次配对评估 62 分钟、其中约 50 分钟耗在第 1 例），所以"廉价模型加速搜索"这一直觉在本项目里杠杆很小（第 6.2 节的推导）。会立刻改善的是"每次测量能带出多少信息"。

三类嵌入位置的总表：

| 位置 | 提高什么 | 代价 | 前置条件 | 判断 |
|---|---|---|---|---|
| ① 新 `runner`（学习型判定器进检测底座） | 补齐 DSL 表达不了的语义判定；这是能力扩展，是项目目标 2 的正题 | 工件存储 + 校准门 + 独立数据 | 见 6.1 | **方向对，条件未到** |
| ② 搜索过滤器（生成多→廉价筛→验证少） | 名义上提高候选吞吐 | 破坏 doctrine；且杠杆小 | 无 | **不做** |
| ③ 把校准引入测量 | 把 1 bit 读数变成连续量，改善统计功效 | 一份只读审计脚本 | 无 | **P0，立刻做** |

---

## 1. Jev：事实清单

### 1.1 接口与三种题型

端点 `POST https://api.typesafe.ai/v1/systemone`，`Authorization: Bearer`，请求体为 `state`（字符串/对象/数组）+ `model`（`"jev-latest"`）+ `questions`（键值映射；**键不参与推理**，仅用于回传对应）。〔厂商〕`https://docs.typesafe.ai/api`

| 题型 | 语义 | 返回 | 基数 |
|---|---|---|---|
| Choice | 从你定义的选项集中选一个 | `choice`、完整 `probabilities`、`confidence` | 上限 255 |
| Score | 按你定义的有序等级评分 | `score`（**概率加权值，可落在两级之间**，例 `1.05`）、`legend`、`probabilities`、`confidence` | 2–10 级 |
| Noul | 是/否 | `noul`（0–1），**无 `confidence`** | 二元 |

三种题型可混在一次调用里，**问题并行且相互隔离地评估**，"增加问题几乎不改变响应时间"〔厂商〕。`Score` 的"可落在两级之间"是厂商明确写的，同时厂商警告"不要用 score 输出计算两级之间的精确数值幅度，`jev-1.13` 的 score 等级在数值校准上很弱"〔厂商〕。

### 1.2 上下文、价格、可用性

- 上下文：**每请求 64k；其中 `state` 加最长的那一个问题上限 32k**〔厂商 `https://docs.typesafe.ai/models`〕。第三方独立确认：Vercel AI Gateway 目录里 `typesafe-ai/jev` 的 `context_window` 为 32000，`type` 标注为 `evaluation` 而非 chat model〔实测〕。挪威语实测补充：其分词器约 2.06 字符/token，所以 32k 对挪威语只有约 64k 字符，不是英文语境下会误算的 12 万〔实测〕。
- 纯文本输入，无图像/音频/视频〔厂商〕。
- 价格：输入 $42/十亿 token（即 $0.042/MTok），**输出计费为零**；限流 250,000 token/秒、1,200 请求/分，且厂商声明"限流在动态调整，可在不通知的情况下变化"〔厂商〕。厂商自陈"我们无法证明定价未被补贴"〔厂商〕。
- 可用性：**early access + 等待名单**；无权重、无自托管、无本地部署选项；不在 OpenRouter（446 个模型的目录里零命中，实测）；在 Vercel AI Gateway 与 LangSmith Gateway 上可用；状态页显示 `api.typesafe.ai` 可用性 99.841%〔厂商〕。非 OpenAI 兼容（无 `chat/completions`），需要专门客户端或网关〔实测〕。
- **无论文、无专利公开、无架构披露、无参数量、无基座、无分词器公开**。arXiv 全文检索 `"System One Model"` 返回 5 条全不相关、`"Reinforcement Learning for Calibrated Decisions"` 返回 0 条〔实测，我方复核〕。

### 1.3 RLCD 只有名字

厂商对训练方法的全部公开描述是三行卡片：RLHF 训练"人偏好的回答"、RLVR 训练"可程序化验证的输出"、**RLCD 训练"返回决策与校准概率而非生成文本"**〔厂商〕。没有目标函数、没有损失、没有算法名、没有奖励定义、没有消融、没有数据构成、没有规模。唯一量化陈述是校准的**目标**而非训练目标："被赋予 0.2 概率的结果应约 20% 发生"〔厂商〕。

把它类比于严格正误评分规则（Brier / log score）是**社区推断**，最严谨的独立分析自己声明："这解释了此类训练想达到什么，并不确立 TypeSafe 用哪种损失、其流程在狭义算法意义上是否是强化学习、或是否更新了全部骨干权重。"〔实测，作者自述为推断〕开源同类模型的卡里倒是直接写了用 CE+Brier 或 KL，但那是复现者的选择，不能反推厂商。

### 1.4 它的"置信"不是正确率概率 —— 对我们是决定性事实

`confidence` 的计算在厂商文档的 React 演示源码里可直接读出，页面文字也确认："本演示用 `(3 × 最大概率 − 1) / 2` 来近似三选项的 confidence"〔厂商〕。独立研究者在其官方 Python 适配器里读到同一公式：对 K 个选项，`c = (p_max − 1/K) / (1 − 1/K)`，"它衡量领先答案高出均匀分布多少，**不是另一个学出来的'该答案正确'的估计**"〔实测〕。

后果，直接关系到我们的设计：

- 我们需要的量是 `P(这个判定是对的)`。Jev 给的 `confidence` 是**答案分布的集中度**，两者只有在"模型校准良好且选项空间完备"时才近似相等。
- `Noul` 题型没有 `confidence`，只有 `noul` 概率。所以要用它的概率，只能用 `probabilities` 原始分布，自己定义并自行校准。
- 厂商自己也没把它当唯一正解："我们提供 `confidence` 作为适配多数用途的便利度量，但你从不被锁定在我们的定义里……正因如此我们给你完整的 `probabilities`。"〔厂商〕

**这一条独立地就否掉了"用 hosted Jev 当判定器并读它的 confidence 来驱动我们 policy"的路线。**

### 1.5 厂商自己承认的九类失效

厂商有一页 `model-jaggedness`，自陈 `jev-1.13` 的已知锯齿〔厂商〕：(1) 字面阅读，"回答你写的问题，不是你意图的问题"；(2) **不算数**——"Jev 不是计算器"，"不计数"，涵盖单词字符数、段落中词出现次数、长列表项数；(3) 日期当文本读、不可靠；(4) 双重否定与复杂间接表达不可靠；(5) **state 越大精度越低，"Jev 存在 context rot"**；(6) **state 是数据、默认不被当作敌意输入，写来对抗性操纵的内容"可以移动答案"**；(7) 指令/标准互相矛盾时不可靠；(8) 结构不变式不成立——同一单据上 Noul 与 yes/no Choice 给出不一致结论，且 Noul 与其自身否定之和为 `0.72 + 0.47 = 1.19`；(9) 不能生成文本，"强行用选项链生成会很慢且很糟"。

第 6 条对我们尤其重要：我们的轨迹正文里包含工具观测、用户消息、检索到的网页内容——全部是 state，且全部**不可信**。我们的检测问题本身就是在问"这些内容里有没有不可信的主张"，把它整段喂给一个默认不设防的判定器，是把攻击面直接交出去。

### 1.6 独立实测读数

| 测试 | 设计 | 读数 | 标注 |
|---|---|---|---|
| LangChain 判定器对比 | 5 例×100 次×4 判定器（Jev / GPT-5.6 Luna / Terra / Claude Sonnet 4.6），人工判据为 oracle | 与 oracle 一致 **500/500**；单例方差 **0.0000149**（Luna 高 433×、Terra 913×、Claude 92×）；0.44 s；$0.00035/次（Claude 同批 $28.17）；作者自陈"5 例很小、低方差 ≠ 正确、便宜会以同样速度放大错误反馈" | 〔实测〕 |
| Archestra 真实工具调用 | 100 条 Claude Code 工具调用、400 决策 | 0-shot 93%、9-shot 95%；**多数类基线 79%**（"79% 的工具调用是无害的，75% 准确率不如一行永远返回 benign 的代码"）；同一 payload 重复只有 **35–39% 的概率逐位相同**；3 类标签下换选项键顺序 **5–7/100 翻转**；减去基线噪声后约 **4/100 因选项顺序改变**；有一例把自信的 `internal` 0.83 翻成 `public` 0.48 | 〔实测〕 |
| JevBench v1.2.11 | 第三方基准，534 决策/系统，hard 档 220 项（111 公开 + 109 留出）冻结并哈希 | Jev 排 **38 行中的第 1，分数 75.4**；hard 档准确率 **74.09%**；`ece_hard` **0.0606**；$0.0399/**千次决策**（注意单位不是千 token）；作者自陈"是试点不是普查、英语、不构成污染证明"，且其自身 fast 档就是 Jev 的另一家服务被移出榜单 | 〔实测〕 |
| archerhume 探针 | 1,029 条插桩探针 + 6,800 条基准记录 | 问题隔离成立（兄弟问题里的秘密无法影响本问题，概率 0.00）；**选项是 listwise 处理而非独立**（追加一个无关选项会把两个既有选项的对数几率从 +0.38 压到 +0.11）；`output_tokens` 是计费构造不是生成量；分词器与 192 个公开分词器都不匹配；**"不应假设 API 级确定性"** | 〔实测〕 |
| lindfors 挪威语 | 24 份听证文件，DeepSeek V4.1 Flash 对照 | 立场判断 20/24 双方持平；Score 精确等级 19/24 vs 14/24；千文件成本 $0.22 vs $1.31/$3.08；中位时延 0.32 s vs 2.7/26 s；**校准 ECE：草稿题面 0.040，更谨慎更长的题面反而 0.116**（"更长的指令把概率推向中间"） | 〔实测〕 |
| Good Start Labs | 6,003 条 rubric 检查 | 与 Claude Fable 5.1 同判 **91.5%**；每百万评分 $160 vs $33,000；结论句"格式是被保证的，判断仍然可以是错的" | 〔实测〕 |

**厂商自评的边界必须一起记住**：首页的 193.6×/444.6× 来自四工作流平均，其参照标签是 GPT-6 Astra 与 Claude Fable 5.1 的**平均**而非人工真值；厂商自己写明"工作流由我们能力团队的成员制作，可能存在偏差"、"参照答案偏向 OpenAI 和 Anthropic 的模型"、"0% 类型错误率不是实测，是 schema 匹配保证"、"首页演示用了简化查询与短输入，**对我们模型有利**"〔厂商〕。信息流里流传的"单次前向传播"是社区改写，**厂商从未这样说过**〔争议〕；"JevBench 75.3"是误引（75.3 是第二名字段 SemIf，Jev 是 75.4）〔实测，我方复核〕。

### 1.7 架构：只有推断，且开源生态已收敛

厂商只说"新模型架构，parallel sampler"与"Jev 并行输出所有概率，而不是按 token 自回归生成"，高基数 Choice 用"两阶段：先独立打分再显式选择"（**未给出阈值**）〔厂商〕。其余全是推断，其中最严谨的一支做了插桩实验并明确标注推断：因果 Transformer（可能含稀疏 MoE）+ 共享 state 编码 + 隔离的问题分支 + 直接概率读出〔实测〕。Sebastian Raschka 的推断是"小编码器模型（如 ModernBERT）+ 类似 calibration reward 的训练"，并强调"秘密更可能在数据里而不在算法里"。

更可靠的信号来自**复现生态**：launch 后一周内出现的开源模型**独立收敛到双向/prefill-only 编码器**形状（DeBERTa-v3-large、ModernBERT、DiffusionGemma 预填充），且复现者把"与 TypeSafe System One API 兼容"当成既定目标。

---

## 2. 同类开源模型

### 2.1 真正可用的复现与替代

| 模型 | 许可 | 基座/规模 | 输出 | 校准 | 上下文 | 读数 | 训练成本 |
|---|---|---|---|---|---|---|---|
| `com-kotobalabs/open-jev-deberta-v3-large` | Apache-2.0 | DeBERTa-v3-large 434M | choice/score/noul + 分布 | **是**，ECE 0.022 域内 / 0.035 OOD | 512（state 截到 256） | 域内 acc 0.854、Brier 0.213；域外 acc 0.690 | **1×H100，229 秒，≈$0.25** |
| `convaiinnovations/laya` | Apache-2.0 | ModernBERT-large 421M | 同上 | RLCD 训练；**原始过度自信**，按题型重构温度后 ECE 0.466→0.081 | 512 | typed-decisions 0.766 acc / 0.062 Brier / ECE 0.213；**含 agent-trace observability 工作流 0.730** | 未公布 |
| `heman10x/rlcd-modernbert-151m` | Apache-2.0 | GLiClass ModernBERT 151M | 24 选项 + **1 个显式弃权槽** | 是，CE+Brier，温度 T=1.0716 | — | 留出 top-1 95.00%、ECE 3.35%、越界弃权召回 97.50%、p50 35.58 ms | 未公布 |
| `mobarmg/jev-schema-scorer-deberta-v3-large` | MIT | DeBERTa-v3-large 435M | 分组 softmax | **否**，卡里自陈"概率常常极尖，当作排序而非校准置信" | 384/512 | v2 choice acc 0.841 | 合成数据 |
| `vagmi/jev-lite` | gemma（**含非商用训练数据**） | gemma-4-E4B-it，适配器 34.9M | choice/score/noul | 是，ECE 0.019 | — | acc 0.816；门控带宽 ≥0.80 时 62% 覆盖 / 93% 准确 | 1×RTX 4090，1h40m |

`com-kotobalabs` 的复现仓还给了三骨干消融，**这是本节最有价值的单个数据点**：ModernBERT-base 149M（acc 0.717、ECE 0.013、68 ms、**训练 $0.16**）、DeBERTa-v3-large 435M（acc 0.855、ECE 0.014、42 ms、$0.26）、LLaDA-MoE-7B-A1B LoRA（acc 0.835、ECE 0.028、676 ms、$2.29）。

**结论：训练一个带校准的类型化判定头，成本量级是"几毛到几美元 + 一次几小时的 GPU"，不是障碍。** 障碍在数据、在留出集、在准入门——不在算力。这条改变了我上一轮对可行性的判断。

另有一条架构经验值得记：该复现仓记录了负面结果——把新 `[OPT]` 标记 token 当打分位"学不出来"，只有对选项文本做 span pooling 才work。

### 2.2 关键负结果：窄数据上的 RLCD 会落到未训练基线之下

`Astro-Han/decision-head-rlcd` 是我在此次调研中找到的**唯一**报告"RLCD 在哪里有害"的产物：Qwen3.5-4B + 4.9M LoRA，32,000 条决策、3 个种子、按与训练数据的关系分组留出。结论是泛化严格跟随训练覆盖，且在 `long_policy` 上训练后落到 **0.32，未训练基线 0.47，Jev 0.63**——"在它不泛化的地方，RL 不只是没帮上忙，它落到未训练起点之下"。

**这是我们最重要的风险参考文献。** 它把"一个自信而错误的确定性组件比噪声组件更危险"从直觉变成实测：噪声会被一致性要求挡在门外，而稳定偏差会被准入并积累。配合理论侧的 Kalai & Vempala（校准的语言模型必然幻觉）与分组损失论文（下节），构成完整的反对理由链。

### 2.3 名同实异与卫生问题

- **RLCD 缩写撞车**：`RLCD: Reinforcement Learning from Contrastive Distillation`（arXiv:2307.12950, 2023）是完全不同的方法。检索时不可混用。
- 多个仓库是**字节相同的重复上传**（`rAVEUK/open-jev-*` 与 `Raymond1122/metask-jev-*` 各为其对应仓库的镜像），引用时须溯源到原始仓库。
- 两个模型卡的代码路径**当天已 404**（`AndeyTait/JevForge-0.8B` 指向的 `jevforge.predict.Predictor`、`kushalpatil/jevify-*` 指向的 `kushalpatil07/jevify`），即"有卡无路"；一个卡把 Laya 的 RLCD 引用成了无关的 arXiv:2510.01237。
- 一个"击败 Jev"的分数（acc 72.70 / Brier 0.1480 / ECE 0.1440）其脚注写明来自**竞争对手的评测套件**，不是本人测量〔争议〕；同一仓库在第三方基准上排第 4（72.5），低于 Jev（75.4）。这是本项目一贯的证据纪律要防的那类数字。

### 2.4 文献里没有的东西

没有出版物报告**轨迹级判定器的校准**。AgentHallu（arXiv:2601.06818，正是本项目使用的数据集）、TRAIL、AgentRewardBench、Who&When 全部只报 accuracy/F1/精确率召回率。JudgeBench、RewardBench、RM-Bench 亦只测判定准确率，不测校准。保形风险控制用于 LLM 判定只出现在 2026 年一篇针对开放对话成对判定的工作。

同时，步级归因在文献里仍未解决：Who&When 的最佳方法是 53.5% 的"责任 agent"准确率但只有 **14.2% 的"责任步"准确率**，部分方法低于随机。这与我们 `docs/optimization-round2.md` 记录的实测（37/135 定位错误几乎全部来自标注歧义）互相印证：**步级读数的噪声主要来自标注口径，不来自判定能力**，换更强的判定器不解决它。

---

## 3. 校准方法论：我们必须先补的课

这一节全部是外部方法论，但每一条都对着我们代码里的一个具体空缺。

### 3.1 我们用错了 "calibration" 这个词

在本库里，`calibration` 指的是一件完全不同的事：`evolution/metrics.py:305-308` 与 `evolution/gates.py:95-97` 用它表示"把未决案例变成已决"——即"未决→已决是记账变化，不算修复"，边界极性明确拒绝"仅靠 calibration 就能满足的声明"。这与概率校准（说 0.8 就该对 80%）毫无关系。

全库检索 `calibrat|ECE|Brier|reliability`：**零条概率校准审计代码**。`confidence` 的三个消费点全是离散分支：`detector/api.py:56` 的"非 high 就跑召回网"、`detector/api.py:666` 写入 guard facts、`detector/interventions.py:88` 的 `judge_confidence` 事实词表。**没有任何门消费置信的数值，也没有任何地方检验过 high/medium/low 是否真的对应不同准确率。**

### 3.2 ECE 的已知缺陷（决定我们该怎么报数）

- Guo et al. 2017（arXiv:1706.04599）引入 ECE 与可靠性图，并给出温度缩放——单参数、事后、出奇有效。
- **ECE 有分箱依赖与有限样本下偏**：Roelofs et al.（arXiv:2012.08668）给出 de-biased 估计量；Kumar et al.（arXiv:1909.10155）逐字指出"流行的重校准方法（Platt、温度缩放）比报告的更不校准，而且**现有技术无法估计它们有多不校准**"——因为它改变的是映射而不改变排序，ECE 无法自证。推论：**我们不能只靠 ECE 审计一个做了温度缩放的组件**，样本量不足时必须用直方图分箱或合适的估计量。
- 因此任何跨来源的 ECE 数值都不可直接比较——本次调研中 0.013 / 0.019 / 0.022 / 3.35% / 0.0606 / 0.116 各用不同分箱、不同划分、不同估计量。这也解释了为什么厂商从不在任何页面上给 ECE。

### 3.3 分组损失：完美 ECE 也可以是废模型

Gruber & Buettner 2022（arXiv:2210.16315）"Beyond calibration: estimating the grouping loss"：**完美 ECE 可以与一个无用模型共存**，因为同一个置信度在不同子群里可以对应完全相反的准确率。

对我们的直接推论：我们的 693 例来自 7 个来源、5 个领域、14 个子类。一个判定器的总体 ECE 可能很好，而在某个来源上系统性反向——这与 README 记录的"候选在同一批上双向改变判定"（消除 2 个保护集误报的同时丢失 1 个收益集真阳）是同一现象。**所以按来源分组报分组损失，比报总体 ECE 重要得多。**

同时必须区分**校准**与**判别力**：ECE 是 `p → Pr(正确)` 映射的性质，AUROC 是排序的性质。一个重校准过的多数类预测器可以完美校准且几乎无用。两者都要报。

### 3.4 选择性预测与保形风险控制

- 选择性分类（Geifman & El-Yaniv, arXiv:1705.08500；SelectiveNet arXiv:1901.09192）：指标是 **accuracy-at-coverage**，即在"允许弃权"下的准确率—覆盖率曲线。本次调研中只有 Laya 的 `eval/results.md` 报了这一对（in-task "accuracy at 50% coverage 0.947"，zero-shot 0.818）。
- 保形风险控制（Angelopoulos et al., arXiv:2208.02814）把覆盖率换成**任意单调损失**，因此可以对"漏报率"这类量给出分布无关保证——正好对应我们"漏报不能离开分母"的口径。
- **但要注意非交换性警告**（Barber et al., arXiv:2202.13415）：保形保证依赖可交换性，而 agent 轨迹跨框架、跨步骤并不可交换。可用，但必须按来源分别校准，不能当全局保证。
- 学习延迟/转交（learning to defer，arXiv:2006.01862 等）是"不确定就交给更慢的通道"的形式化，与我们 `unresolved` 语义同构。

### 3.5 蒸馏 judge 的偏差继承

这是把学习模型接进 RSI 回路最需要防的一条，且已有可引用证据：

- **教师自一致上限**：Laya 公开其教师自一致上限 **0.735**——教师在同一套件上跟自己一致都不超过 73.5%，学生的天花板由教师噪声决定，不由学生容量决定。
- **教师与 gold 不一致**：`vagmi/jev-lite` 实测"教师与 gold 不一致 **24.9%**"，其处理是把这些行按 0.5 混向 gold 而不是丢弃。
- 偏差类型学：位置偏差（arXiv:2305.17926）、无内容偏差（arXiv:2102.09690 "Calibrate Before Use"）、自我偏好（arXiv:2404.13076）、**面板相关性**（arXiv:2605.29800 "Nine Judges, Two Effective Votes"——多个 judge 的有效票数远少于名义票数）。最后一条直接质疑我们 `judge_route.votes = 3` 的独立性假设：三个同模型同提示的样本，其错误是相关的。
- 反向的实证：`Astro-Han/decision-head-rlcd` 显示窄覆盖训练会落到基线之下（见 2.2）。

---

## 4. 本项目接口事实（决定能不能接、接哪里）

### 4.1 `runner` 槽位与派发路径

`runner` **不是词表闭集**，而是靠 `if/elif/else` 结构性闭合。`detector/interventions.py:441-470`（`evidence_check`）与 `:490-517`（`recall_trigger`）结构相同：

- `predicate in PREDICATES`（六个内建）→ 只接受 `runner == "deterministic"`，且禁止携带 `predicate_definition`（`:445-454`）；
- `runner == PROGRAM_RUNNER`（`"typed_dsl"`，`detector/predicate_dsl.py:36`）→ 必须有通过 `validate_predicate_program` 的 `predicate_definition`，且程序名必须等于 `body.predicate`（`:456-464`）；
- 其它任何字符串 → `:465` 的 `else` 抛 "has no registered executor"。

运行时派发 `InterventionPlan.evidence_for`（`:902-908`）：

```python
runner = str(body.get("runner") or "")
if runner == PROGRAM_RUNNER:
    result = run_predicate_program(body.get("predicate_definition") or {}, packet, context)
else:
    result = run_predicate(predicate, packet, context)
```

**`else` 默认假设是内建确定性 runner。** 因此一个未处理的第三个 runner 值不会优雅降级，而是掉进 `run_predicate` 并抛 `predicate ... has no deterministic executor`（`detector/predicates.py:515-517`）。`recall_triggered` 有同构分支（`:1076-1084`）。

另有两处会在计划构造期先拦下来：`inline_predicate_programs`（`:244-284`）对"runner 不是 `typed_dsl` 却带 `predicate_definition`"直接抛（`:262-268`）；`evidence_for` 末尾还要求 `result["verdict"]` 落在 `output_verdicts` 里，否则抛"undeclared verdict"（`:909-914`）。

`structured_key` 在两处（`evolution/contracts.py:158-178` `evidence_check`、`:192-218` `recall_trigger`）用 `if runner == PROGRAM_RUNNER` 决定是否计算 `program_hash`，**非 `typed_dsl` runner 的 `program_hash` 恒为空串**。后果见 7.3。

### 4.2 26 字段类型化视图 = 已经写好的状态压缩

`detector/predicate_dsl.py:56-84` 的 `FIELD_TYPES` 共 26 个字段（22 个 packet/context 视图 + 4 个 aggregate）。关键性质：

- `_extract_view(field, packet, context)`（`:519`）**是模块级函数、可独立调用**，只需要一个 `packet` 和一个普通 `context` 映射——**不需要 program，不需要 hook 名，不需要 LLM 调用**。`typed_field_profile(packet, context=None)`（`:657`）会遍历全部 26 个字段给出截断版本，并明确注明"无 case id、无 gold 标签"。
- 26 个字段里，**22 个纯由 packet 计算**（question/last_*/event_*/tool_*/interaction_*/packet_features/*_count）；只有 4 个 `context_judge_*` 依赖 judge 的解析输出（`:594-635`）。
- hook 可用性由 `detector/predicate_context.py:25-31` 的 `HOOK_CONTEXT_FIELDS` 强制：`judge.post` 与 `recall.trigger` 拿到全部 8 个 context 字段，`defense.pre` 拿到 4 个（`_HEARING_FIELDS`），**`upgrade.pre` 一个都没有**。`validate_program_hook`（`:34-45`）在注册时就把不可用字段拒掉，而不是静默当成空值。

**推论：一个吃 `_extract_view` 输出的本地判定头，边际 LLM 成本近似为零，且能在 STRUCTURAL 的离线家族执行里跑。** 这解决了上一轮我担心的"32k 上下文 vs 我们 40 万字符视图"——不需要把视图喂给判定器，只需要喂 26 个类型化字段。这是本项目自带的、可审计的、去标识的状态压缩层。

代价也要说清：类型化视图是**有损**的（scalar 是字符串、list 是字符串列表、aggregate 是整数），DSL 只能做词法/结构匹配正是因为这个损失。学习型判定头能在同一视图上做语义泛化，这是它相对 DSL 的**唯一真实优势**——但它的输入上限也被同一个损失封住。

### 4.3 STRUCTURAL 对执行器的要求

`_run_program_on_family`（`evolution/gates.py:160-189`）逐成员加载 label-free packet、执行、收集 verdict 集合，**要求集合大小为 1**（"heterogeneous verdicts" 即拒），且该 verdict 必须落在 `conviction_verdicts` 内（`:351-360`）。缺 `dataset_root` 直接拒（`:341-345`）；家族回放输入缺失时返回 `_policy_fail(..., evaluable=False, status="not_evaluated")`（`:211-219`）——即**不可测量而非证伪**，这个区分是框架的既有优点。

对学习型 runner，这条意味着它必须：在**家族全体成员**上给出**同一个**定罪级 verdict。这不是"平均准确率够高"，是**全体一致**。用 2.2 的负结果对照，这是最容易被击穿的一关。

同时也说明：`_run_program_on_family` 直接调用 `run_predicate_program`（`:182`），所以学习型 executor 必须在函数内部分派，或在 `structural_gate` 里新增分支。

### 4.4 完全没有的两样东西

**(a) 工件不可变存储。** `source_digest()`（`evolution/backends.py:73-85`）只做**非递归** `glob("*.py")`，覆盖 `detector/` 与 `evolution/` 顶层的 py 文件——不含 `detector/configs/`、不含数据、不含任何二进制。`harness.py` 的组件摘要走 `fingerprint`（`:88-90`，JSON 规范化 + sha256 前 12 位），只哈希可 JSON 序列化的 payload；`_digest_payload`（`:225-239`）只覆盖版本与摘要，二进制无法自然进入。`file_digest`（`:64-70`，读原始字节、缺失返回 `"missing"`）**存在但只在别处使用**，没有被 harness 或晋升协议采用。

**(b) 校准门。** 检索确认：**不存在任何消费 predicate 自身正确率、精确率、召回率、与 gold 一致率或校准的门**。最接近的 `evidence_increment_rows`（`evolution/metrics.py:533-557`）逻辑是——"候选臂的证据行里出现了这个 intervention_id，且对照臂没有"就 +1。**它只数出现，不查 verdict 对不对。** 因此一个永远输出同一个错 verdict 的 predicate 可以在 BEHAVIOR 门上得满分。

把这两条放在一起：一个学习型 runner 今天若被塞进注册表，**只能靠"家族全体一致"过关，没有任何机制检查它是否准**。这正是 2.2 负结果描述的失效形态。

### 4.5 成本包线与统计功效现状

`evolution/config.py` 的默认包线：`split.seed 20260817`、`patrol_size 120`、`sentinel_size 20`（正负各 10）；`budget {patrol_cases 120, stable_failure_rerun_cases 40, llm_calls 4, max_proposals 3, max_admits 2}`；`gates {structural_jaccard_threshold 0.72, harmlessness_new_fn/fp/unresolved_allowed 全 0, harmlessness_min_protected_tp/tn 各 2, m_min_marginal_cases 1, m_new_errors_allowed 0}`；`library {max_width 6, max_text_boundary_per_hook 1, max_decision_guards 4, retire_after_cycles_marginal_le0 2, rollback_after_cycles_net_negative 2}`。`evolution/configs/development.json` 实际把 patrol 降到 20、`llm_calls` 2、`max_proposals` 4、`cycle_timeout_seconds` 7200、workers 8。检测器侧：`case_timeout_seconds 10800`、`judge_route.votes 3 + vote_topup 2`、`defense_route.votes 3 + vote_topup 2`。

统计工具现状：**只有两处**推断——`evolution/engine.py:3151` 的 `_mcnemar_improvement_p`（单侧精确二项，用于最终验证判决，`p_value < 0.05` 才 pass），与 `output/rsi-planning/run_round.py` 的 Fisher 精确检验（用于 `no_significant_degradation` 规则）。**没有 bootstrap、没有置信区间、没有方差估计**；`evolution/metrics.py` 全部是点估计。仓库里 "bootstrap" 一词指的是**巡逻重采样以发现失败族**，不是统计重采样。

框架自己已经写下了结论，`evolution/engine.py:3108-3114` 逐字：

> "with n=300 and a symmetric paired test, one-sided p<0.05 requires at least 5 net improving flips and zero worsening flips; a small true effect can be real yet underpowered, which is a property of the pre-registered criterion, not of the gate machinery"

而开发池远不到 n=300。`docs/history/real-verification-feasibility.md:237` 与 `docs/history/progress.md:160,217` 逐字记录："统计功效不足……这不是配置调参问题"、"评估信号太贵且太弱（4 例 gain 集、0 容错，统计功效≈0）"、"**限制不再是代码或门限，而是评估集的统计功效**"。

---

## 5. 数据与工件盘点

### 5.1 693 例，以及"全部暴露"

`datasets/raw/agenthallu/AgentHallu/` 共 **693** 个案例 JSON，7 个来源：BFCL 164、Camel 93、Magentic_One 94、Octotools 47、OpenDeepSearch 100、OpenManus 104、SmolAgents 91。**gold 为真 443 例（全部带 `hallucination_step`）、为假 250 例。** 标签以字符串形式存放（`is_hallucination = 'true'`、`hallucination_step = '1'`），由 `evolution/splits.py:23-34` 的 `bool_gold` 归一化并在非法值上抛错。

**决定性的约束**：`datasets/protocols/development_exposure.json` 的 `cases` 列表包含**全部 693 例**，其 `policy` 逐字为"Conservative exclusion: the original corpus cannot certify a new independent holdout."。`reports/README.md:48` 逐字："没有任何一份报告提供独立泛化结论：原始 693 例全部在 `datasets/protocols/development_exposure.json` 的暴露范围内。要宣称泛化，必须先补齐独立终审数据。"

协议文件只有三份：`development.json`（30 例演化 + 10 例验证）、`development_exposure.json`（693）、`real_path_check.json`（6 例、2 次重复）。**sentinel / gain / patrol 划分不是协议文件**，而是 `evolution/splits.py` 按配置种子在运行时派生的。

### 5.2 已归档的判定输出（可用于离线审计）

- **59 个结果信封**（`<run>/cases/<safe_id>/result.json`）：`reports/verification_20260917/detector_dev40/` 40 个 + `reports/rsi_pilot_20260919/h0_baseline/` 19 个。仓库中**不存在**任何 `prediction.json`（输出走 `result.json`，见 `detector/runner.py:84-100`）。
- **4 个 `evaluation.json`**，其中 dev40 的 `metrics` 是可直接引用的基线读数：decisive 40、unresolved 0、`decisive_accuracy 0.725`、tp 17 / fp 4 / fn 7 / tn 12、`step_localization {n: 24, hits: 10, accuracy: 0.4167}`。
- **192 个 arm 案例文件**（`reports/**/checkpoints/**/arms/` 下，均含 `proof.evidence` 证据行，其中可见 `runner: "typed_dsl"`）。
- **518 个 rsi_pilot JSON**（含 `frozen_recheck/<arm>/repNN/{identity.json, results/}` 的逐次重复归档）。
- `reports/` 全目录 **982 个 JSON**（目录内文件总数为 1,037，差额为日志等非 JSON 文件）。

这些归档的 `proof.judge` 里带着 judge 的 `confidence` 与 `is_hallucination`，**这就是 P0 审计的数据来源**。

### 5.3 经验存储几乎全空

| 存储 | 磁盘记录数 |
|---|---|
| `episodic.jsonl` | 8（全部在 `reports/verification_20260917/harness_memory/`） |
| `episode_corrections.jsonl` | 8 |
| `procedural.jsonl` | **0（不存在）** |
| `improvement.jsonl` | **0（不存在）** |
| `gaps.jsonl` | **0（不存在；两份 `gaps_summary.json` 均为全零）** |
| `recall.json` | 不存在 |
| 全部账本合计 | 25 行 |

**意义**：`gaps.py`——"没有任何路线能处理这个机制"的能力缺口后备清单——目前**没有任何一条记录**。也就是说，**"手写模式匹配表达不了某类判定"这一需求，现在还没有累积出证据**。这直接决定了第 6.1 节对"新 runner"优先级的判断：方向对，需求侧证据尚未形成。

---

## 6. 深入判断：三类嵌入位置

先把一个直觉纠正掉，因为它决定后面所有排序。

### 6.0 框架的约束是测量，不是搜索

生成/验证成本比在数量级上失衡：候选生成是**一次 LLM 调用产出 ≤4 个候选**（`proposer.py:494`，budget `llm_calls 2`），而验证一个候选是**一次配对评估，实测 62 分钟、其中约 50 分钟耗在一例上**（`reports/promotion_20260921/README.md`；`evolution/backends.py:193-198` 的注释逐字记录了这个长尾）。

所以"廉价模型让我们搜得更多"这条链路在本项目里是断的：搜索的瓶颈不是生成候选的成本，是**评估一个候选的成本**。任何只降低生成/筛选成本的改造，收益上限都很低。真正高的杠杆在**每次测量能带出多少信息**。

### 6.1 位置一：新 `runner`（学习型判定器进底座）

**为什么方向是对的。** 这是 `capability_surface.py` 自己点名的缺口：它声明 `new_executor_runtime_registration: True`、`hybrid_proposer_typed_dsl_fallback: True`，同时诚实列出 `new_hook: False`、`sandboxed_code_generation: False`、`model_weight_update: False`，并在 `required_for_open_ended_evolution` 里逐条列出还缺什么，其中就有 **"immutable capability artifact store"** 与 **"sandboxed new executors with property and metamorphic tests"**。DSL 的能力边界是词法/结构匹配；家族机制一旦是"judge 声明的契约没有覆盖实际交付物"这类语义判断，DSL 就表达不了，于是变成 `ACTIONABILITY["capability_gap"]`（`evolution/worksheet.py:685-688`）。学习型判定头正好补这一格——而且**它吃的是同一个 26 字段视图**，所以比较是干净的：同一输入、同一注册机制、同一门，手写程序 vs 学习函数。

**为什么现在不行。** 三个硬前置：

1. **需求证据为零**：`gaps.jsonl` 不存在，能力缺口计数为 0。我们并不知道哪些失败族真的落在 DSL 表达力之外——`classify_family_actionability` 只在每条路线都无法处理时才记为 gap（`worksheet.py:807-809`），而 `evolved_predicate` 这个兜底路线几乎总是"存在"。
2. **准入门不存在**（4.4b）：今天一个学习型 runner 只需"家族全体一致"就可定罪，`evidence_increment_rows` 只数出现不查对错。
3. **没有可用的留出集**（5.1）：全部 693 例暴露。在暴露池上训练再在暴露池上验证，等于用被优化的对象给自己打分——这正是框架在别处极力避免的（`gates.py:1-10` "LLM 输出永不作为晋升分数"）。

**要付的代价**，按依赖顺序：工件不可变存储 → 校准门 → 独立留出集 → 才谈训练。

### 6.2 位置二：搜索过滤器（生成多 → 廉价筛 → 验证少）

**杠杆比直觉小得多，而且有 doctrine 风险。**

- 候选只有 4 个/轮，`max_proposals` 是**策略上限而非成本上限**。要 40 个候选才能体现筛选价值，但把候选从 4 提到 40 的瓶颈是**验证 40 个候选**，不是生成 40 个。廉价过滤器不解决它要解决的问题。
- 更严重的是：如果"廉价筛"的目的是**预测配对评估的结果以跳过评估**，那就直接违反 `evolution/gates.py:1-10`（"LLM output is never a promotion score"）与整个骨架的设计前提，同时是奖励模型过优化的标准形态（Gao et al. 2022, arXiv:2210.10760；Skalse et al. 2022, arXiv:2209.13085）——优化一个代理分数会让真实目标退化。
- 允许的形态只剩"只重排序、永不进判定路径"，而重排序 4 个候选的期望收益≈0。

**结论：不做。** 唯一值得保留的残余形态是 L0 级分诊，等候选规模真的上去之后再评估。

### 6.3 位置三：把校准引入测量（真正的瓶颈所在）

这是本文的核心判断。推理链：

1. 我们唯一的权威读数是 **gold 上的二元终态**（`detector/result.py:54` 的 `prediction_state(..., require_authoritative=True)`）。因此每次判定携带的信息量是 **1 bit**。
2. 1 bit 读数只能"数翻转"。所以门不得不写成"净增 ≥1 且 0 新错"这种零容差阈值。在 n=20、4 例增益集的规模下，`m_min_marginal_cases = 1` 与采样波动**同量级**——这正是文档自陈的"统计功效≈0"。
3. **一个校准概率把 1 bit 变成连续量。** 于是可以用**配对正误评分**（Brier / log loss 的配对差）做检验，同样的 n 下能检出远小于"1 例净翻转"的真实效应。这不是放宽判据，是**换一个信息量更大的统计量**。
4. 而且框架里**已经有这个先例**：`_mcnemar_improvement_p`（`evolution/engine.py:3151`）已经在最终验证判决里消费一个配对统计量。在门的读数里加一条配对正误评分，是**扩展既有模式，不是引入外来物**。
5. 数据也已经在了：归档的 `proof.judge` 带 `confidence` 与 `is_hallucination`，gold 带标签。**这一步完全离线、零新模型、零外部依赖。**

**注意这条与 `protection_repair` 的关系。** `evolution/metrics.py:280-330` 已经确立了正确纪律："只认决定性翻转为修复，`unresolved→correct` 属校准、单独计数、两个方向永不相加、`net` 恒为 `null`"。那条纪律**禁止**把异质效应加权抵消，是对的。而"单一效应的统计不确定性"是**另一个问题**——框架目前是用零容差来回避它。正确的回应是**修仪器**（把一条 1 bit 读数换成带不确定性的连续读数，并如实报 CI），**不是放宽判据**。

**而这才是 Jev 类模型对我们真正的启发：不是"再放一个判定器"，而是"输出必须带可检验的概率"这条工艺。** 把这条工艺施加到我们已有的 judge 上，收益立刻可得——不需要 Jev 可用，不需要自托管，不需要训练任何东西。

### 6.4 优先级

| 序 | 项目 | 依赖 | 判断依据 |
|---|---|---|---|
| **P0** | 校准审计 + 配对正误评分读数 | 无 | 直接攻击唯一被文档承认的瓶颈；纯离线；数据已在 |
| **P1** | 工件不可变存储 + 校准门 + `structured_key` 修复 | 无 | 任何学习型/非确定性 verdict 源的前置件；本身是正确性修复 |
| **P2** | 新 runner（本地冻结判定头） | P1 + 独立 holdout | 方向对，但需求证据（`gaps.jsonl`）与数据都未到 |
| P3 | 搜索过滤器 | — | 杠杆小 + doctrine 风险，不做 |

注意 P2 与 `docs/optimization-round2.md` 的关系：本轮方案已经确认"定位机制收益当前不具备开展条件（按可修复案例计只有 1 例）"。**学习型 runner 不应该被塞进这一轮**，它与该轮的核心结论无关。

---

## 7. 负面作用与风险清单

按严重性排序。前三条是**具体的代码级后果**，不是一般性担忧。

### 7.1 非确定性会静默破坏冻结与复现

实测事实：托管 Jev 同一 payload 重复只有 35–39% 的概率逐位相同〔实测〕；3 类标签下换选项顺序约 4/100 翻转；厂商提醒"若你按某版本调了置信阈值，就固定那个版本 ID 而不是别名"，独立探针的结论是"不应假设 API 级确定性"。

落进我们代码的后果：

- `evolution/research.py:1204-1210` 的身份绑定会抛错："`{stage}/{arm}/rep{rep} exists with a different identity; repetitions are frozen and must not be silently re-run`"。即重复实验的冻结语义被破坏。
- `evolution/backends.py` 的 `CachingBackend` 只缓存 `decision_authority == "authoritative"` 的结果；非确定性源会让同一 arm 不可复现。
- **最危险的形态是静默的**：账本的哈希链仍然逐条验证通过（`evolution/ledger.py:76`），因为它只保证记录未被篡改，不保证记录可再现。也就是说，我们会得到一份**校验通过但内容不可复制**的证据链。

**推论：这类能力若进本项目，必须以"冻结的本地工件 + 摘要"形式进入，不能以托管服务形式进入。** 一个 eval 模式下的本地编码器头是确定性的（模跨硬件浮点差异），一个托管 API 不是。

### 7.2 权重不进 `source_digest`：晋升基线会静默失真

`freeze_protocol` 把 `code_baseline = source_digest()` 写进协议（`promotion.py:454`），`load_protocol` 在加载时**重新校验**它并在不等时抛"detector/evolution source changed after the promotion protocol was frozen; a frozen protocol is evaluated on the machinery it froze"（`:576-580`）。

但 `source_digest()` 只覆盖非递归的 `detector/*.py` + `evolution/*.py`（4.4a）。**一个权重文件在它之外。** 后果：一个已晋升的学习型 runner 在权重被替换后，协议校验**仍然通过**，于是"我们评价的机制"与"正在运行的机制"静默地不再是同一个东西。

这比 7.1 更严重：7.1 会让运行失败或不可复现（可见），7.2 会让评价**在错误的前提下**得出结论（不可见）。修复不难（`file_digest` 已存在，接入 harness 的 T 组件与协议即可），但不修则会污染每一次带学习型组件的晋升。

### 7.3 `structured_key` 碰撞

`evolution/contracts.py:158-178` / `:192-218`：非 `typed_dsl` runner 的 `program_hash` 恒为 `""`。因此两个**不同**的学习模型，只要 predicate 名、hook、`packet_features` 相同，就产生**相同**的 `structured_key`。后果双向：

- STRUCTURAL 的冗余检查会把第二个判为"与活跃项冗余"而**拒绝一个真正不同的候选**（假阴性）；
- 或者反过来把"换了模型"当成"同一候选"的重复出现，掩盖真实差异。

同时 `proposer.py:326-362` 给 proposer 看的活跃库摘要里，`program_hash` 对非 DSL runner 也静默为空串——proposer 因此失去区分能力。

### 7.4 judge 偏差继承是**结构性**的，不是偶然的

失败族的构造源出检测器自身的输出路径：`evolution/worksheet.py:171` 的 `_mechanism_signature` 以 `judge_positive`、各阶段 state、`recall_path`、`upgrade_positive`、`defense_verdict`、`positive_source` 为键。因此在**检测器输出的标签**上训练的打分器，会继承检测器的盲区，并且**恰好在检测器系统性出错的地方最自信**——因为那里正是它的训练信号最一致的地方。

外部证据已在 3.5 列出（Laya 教师自一致上限 0.735；vagmi 教师与 gold 不一致 24.9%）。理论侧：Kalai & Vempala（校准的模型必然幻觉）说明校准不解决"这个主张是否可核查"。实证侧：`decision-head-rlcd` 的窄覆盖 RLCD 落到基线之下。

**这条与 7.1 合起来给出一个反转结论**：确定性的、置信度高的、稳定的错误组件，比一个摇摆的组件**对 RSI 更危险**——噪声会被一致性/多数要求挡在门外（我们 `required_quorum`、`majority_vote` 机制正是干这个的），稳定偏差会被准入然后被跨轮积累。

### 7.5 能力面审计与 doctrine 漂移

`capability_surface.py` 是一份**诚实声明**：它明确报告 `new_hook: False`、`new_guard_action: False`、`sandboxed_code_generation: False`、`model_weight_update: False`、`execution_graph_rewrite: False`。引入学习型 runner 会改变这份声明的内容，而 `required_for_open_ended_evolution` 要求的是**"带性质与蜕变测试的沙箱执行器"**——只加 runner 不加测试体系是**半满足**，而半满足的形态正是本项目自己的文献检索里记下的 Phantom Guardrails 警告："正常模式可诱发虚构失败；受控实验中无根据 guard 可混入有收益组合。"

也就是说：**加一个能定罪的学习型执行器而不加对应的测试与校准门，恰好是本项目已经识别为危险的形态。**

### 7.6 外部依赖与跨时间可比性

early access + 等待名单 + "限流动态调整、可不通知变更" + 厂商自陈无法证明定价未补贴 + 99.841% 可用性。基础设施故障本身有处理（`promotion_unresolved`、任一臂有基础设施失败即不判分不写 registry，`promotion.py:1032-1074`），但**"可比性"不是基础设施问题**：模型版本漂移会让两次晋升运行在不可比的判定器上比较，而账本里不会留下痕迹——因为故障没有发生。

---

## 8. 建议的最小工作包

以下三个工作包都不依赖 Jev 可用性。P0 与 P1 也不依赖任何外部模型。

### P0 —— 把现有 `confidence` 变成可检验量（纯离线，今天可做）

**步骤 1（只读脚本）。** 从 `reports/**` 归档的 `proof.judge` 抽出 `(case, source, gold, confidence, is_hallucination, state, authority)`，产出：

- 三档的准确率、覆盖率、**ECE（用 de-biased 或 adaptive 估计量，并报自助置信区间，不要只报点估计）**；
- **按 7 个来源分组的准确率与分组损失**（3.3 的理由：总体 ECE 好而在某来源上反向，是我们已经见过两次的现象）；
- 校准与判别力**两者都报**（ECE 与 AUROC），因为完美校准可以与无用模型共存。

**步骤 2（判据）。**

- 若三档单调可分且 ECE 在可接受范围 → `confidence → p` 的映射可用于门的统计量，进入步骤 3；
- 若不单调、或 CI 覆盖零差异、或分组损失大 → **结论是"现有 confidence 不能用于门"**。这同样是有效读数，且省掉后续全部投入。

**步骤 3（加读数，不改门）。** 在 `meets_m` 之外增加一条**独立的**配对正误评分读数（配对 Brier 差或 log 差 + CI），沿用 `protection_repair` 已确立的"分开记录、永不相加、`net` 恒为 `null`"纪律。**不动既有的零容差门**——先用一条真实运行证明新读数比旧读数信息量更大，再谈要不要让门消费它。

**证伪条件**：若三档在现有 693 例上不可分（例如 high 与 medium 的准确率差异落在自助 CI 内），则此路不通，须回到二元读数并另找功效来源（例如扩大独立数据、或改用配对设计的重复测量）。

**为什么这是最高优先级**：它是唯一一条直接攻击"评估集统计功效"这个被文档承认的瓶颈、同时零外部依赖、零新模型的路径。而且它产出的读数**本身就是文献缺口**（2.4：没有任何工作报告轨迹级判定器的校准，而我们有 693 例带 binary + 责任步 + 因果解释标注的数据）——把审计做成可复现读数并归档，成本只是报告格式。

### P1 —— 工件不可变存储、校准门、`structured_key` 修复

**P1-A 工件摘要覆盖。** 用现有 `file_digest`（`evolution/backends.py:64-70`）把二进制工件纳入：`source_digest()` 增加对工件目录的递归覆盖，`harness.py` 的 T 组件（或新增组件）带上工件摘要，`freeze_protocol` 的 `code_baseline` 或新增字段带上工件身份。判据：**交换两个内容不同的权重文件必须让协议加载失败**——这是可以写成回归测试的。

**P1-B 校准门。** 在 `structural_gate` 增加分支：任何**非确定性或学习型** verdict 源，除既有的"家族全体一致 + 落在 `conviction_verdicts` 内"之外，**必须附一份留出集上的可靠性读数**（分组准确率 + ECE + CI），缺失即拒。同时把 `evidence_increment_rows` 的语义补一份独立读数：**"证据行出现且 verdict 正确"**，与现有的"仅出现"分开记（不是替换——替换会改变 BEHAVIOR 门的既有含义，属于 doctrine 变更）。

**P1-C `structured_key` 修复。** 非 `typed_dsl` runner 必须把**工件身份**纳入 key（或新增一个 tuple 槽位）。判据：两个不同工件、相同 predicate 名与 hook，必须产生不同的 `structured_key`。

**为什么 P1 应该在 P2 之前**：这三条都是**独立的正确性修复**，即使最终不引入任何学习型 runner，7.2 与 7.3 描述的隐患在今天的代码里已经存在（一旦有任何非 `.py` 的执行语义来源）。

### P2 —— 训练/引入一个窄判定头（仅在独立 holdout 建成之后）

**门槛**：独立留出集存在且 ≥100 例未暴露数据（`evolution/splits.py:183` 的 `FinalHoldout` 已经是只读一次的机制，缺的是数据），且 `gaps.jsonl` 至少有一条真实的能力缺口记录证明该判定确实是 DSL 表达力之外的。

**成本不是障碍**：kotoba 的三骨干消融给出 $0.16 / $0.26 / $2.29 的训练成本，open-jev 是 229 秒 / $0.25。**障碍是数据、留出集与准入门。**

**建议的起始形态**（若进入）：

- 只做 `evidence_check`——`capability_surface.py` 明确写着 `evidence_check_changes_terminal_alone: False`，即架构上**它不可能单独改变终态**，这是天然的第一落点；
- 带**显式 abstention verdict**（参照 `heman10x/rlcd-modernbert-151m` 的"24 选项 + 1 弃权槽"形态），并申报为 `conviction_verdicts` 之外；
- **温度缩放**在留出集上拟合，并**如实报告 ECE 与该估计量在样本量下的可信度**（Kumar 2019：ECE 无法自证温度缩放）；
- 必须通过 `_run_program_on_family` 的**家族全体一致**且附校准报告；
- 输入只用 26 字段类型化视图（4.2），不吃原始轨迹——这既省上下文，又避免 1.5 第 6 条（对抗性 state 移动答案）的攻击面。

### P3 —— 明确不做

- 把托管 Jev 作为判定路径的一部分（7.1、7.6）；
- 用学习模型预测配对评估结果以跳过评估（6.2，违反 `gates.py:1-10`）；
- 在 conviction 路径放置无理由组件（厂商自陈"不写解释"；我们的账本是哈希链 + 证据引用校验，无法容纳一个不能解释自己的组件）；
- 在检测器输出标签上蒸馏打分器并声称泛化（7.4）。

---

## 9. 什么会推翻本文

- **独立留出集建成且 ≥100 例未暴露数据**：那时位置一的可行性判断需要重做，P2 从"条件未到"变成"可开展"。
- **出现自托管、携带可验证校准报告、且摘要可冻结的同形模型**：那时 7.1 与 7.6 的主要反对理由消失。目前的可自托管选项（kotoba / laya / rlcd-modernbert）已满足自托管与许可条件，缺的正是**校准报告**与**留出集**。
- **P0 的读数为正**（三档单调可分、ECE 可接受、分组损失小）：那时位置三的门改造路径更短，且会反过来改变位置一的收益估计（因为门有了更强的统计量，学习型 runner 的效果才可能被测出来）。
- **我们的成绩单显示判定不再受测量限制**：例如门已能稳定区分 1 例边际、或长尾单例成本下降到不再是瓶颈。这时本文的整个排序（测量优先于能力）需要重估。

本文的判断建立在两个可复核的前提上：**框架的权威读数是 1 bit 的二元终态**（`detector/result.py:54`），以及**全部 693 例已暴露**（`datasets/protocols/development_exposure.json`）。这两个前提任一改变，结论都要重算。

---

## 附：检索与核验记录

**外部检索。** 主要查询：`Jev System One Model TypeSafe`、`RLCD Reinforcement Learning for Calibrated Decisions`、`JevBench results`、`open-jev / jevify / laya / metask-jev`、`calibration ECE proper scoring rule temperature scaling`、`conformal risk control`、`selective classification accuracy at coverage`、`learning to defer`、`agent trajectory failure attribution`、`distilling LLM judge bias`、`process reward model`。来源分层：**厂商一手**（`typesafe.ai` 博客/首页/FAQ、`docs.typesafe.ai` 全站、状态页）、**独立实测**（LangChain 博客与代码仓、Archestra、Good Start Labs、lindfors、archerhume 探针、JevBench、Browser Use）、**注册表一手**（PyPI / npm / HuggingFace API / Vercel AI Gateway API / OpenRouter API）、**论文**（arXiv 与 ACL 链接，均为摘要级或方法级，未复现）。二手聚合站只用于发现原文，未作为技术依据。

**本项目核验。** 直接读取并逐条核对的文件：`detector/interventions.py`（runner 闭合与派发）、`detector/predicate_dsl.py`（FIELD_TYPES / _extract_view / run_predicate_program / hash）、`detector/predicate_context.py`（hook 上下文可用性）、`detector/semantics.py`（judge 契约）、`detector/api.py`（召回触发规则）、`evolution/gates.py`（四门与家族执行）、`evolution/metrics.py`（`evidence_increment_rows` / `protection_repair`）、`evolution/contracts.py`（`structured_key`）、`evolution/harness.py`（清单与组件摘要）、`evolution/promotion.py`（冻结协议与校验）、`evolution/backends.py`（`source_digest` / `file_digest` / `fingerprint`）、`evolution/config.py`（包线）、`evolution/engine.py`（McNemar 与统计功效注记）、`evolution/worksheet.py`（家族构造与路线分类）、`evolution/capability_surface.py`、`evolution/memory.py`、`evolution/gaps.py`、`evolution/splits.py`、`reports/README.md`、`docs/{technical-solution,optimization-plan,optimization-round2,maintenance,error-scope,localization}.md`、`docs/history/{progress,review,directions-assessment,real-verification-feasibility}.md`、`datasets/protocols/*.json`、`datasets/integrity.json`。计数（693 / 443 / 250 / 7 源、59 信封、192 arm 案例文件、518 rsi_pilot JSON、982 报告 JSON、经验存储与账本行数）为直接统计结果。

**本次未能证实或存在缺口的项，逐条列出**：(1) Jev 的参数量、基座、分词器、架构、RLCD 的形式化定义——厂商未披露，全部为推断；(2) 厂商从不给任何 ECE/Brier/可靠性图，所有校准数字均为第三方；(3) `jev-1.13` 两阶段 score-then-choose 的触发阈值未公开；(4) 是否在 AWS Bedrock 上（403，无法枚举）；(5) Semantic Scholar 检索被限流（429），学术引用覆盖不完整；(6) 专利数据库未检索，"无专利"属未证实而非确认否定；(7) 厂商的 $40M 种子轮与估值传闻未由厂商官网证实，未采用；(8) Every 的"25× 快、580× 便宜"仅见二手引用，一手来源未取到，未采用；(9) 若干第三方"击败 Jev"的分数来源为竞争对手套件或自评，已在 2.3 标注为争议。

**未做的事**：未复现任何外部实验；未调用任何外部模型服务；未修改本项目任何代码或数据；未用本项目代码、私有案例或内部报告文本向外发送（检索只使用公开方法名与通用词）。
