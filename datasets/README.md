# 原始数据集

把原始数据集文件放在本目录。它们从不被 git 跟踪。

预期布局：

```text
datasets/
├── README.md            # 本文件
└── raw/                 # 实际数据集文件（被 git 忽略）
    └── <dataset>/
        ├── cases/       # 原始轨迹记录
        └── manifest.jsonl
```

## 记录格式

每条原始轨迹是供检测器消费的一个 JSON 对象：

```json
{
  "case_id": "optional-external-id",
  "question": "optional task text",
  "history": [
    {
      "step": 1,
      "role": "assistant",
      "content": "agent message",
      "tool_calls": [],
      "tool_responses": []
    }
  ]
}
```

- `history` 必须是非空列表。
- 仅求值器使用的字段（`gold_label`、`is_hallucination`、`hallucination_step`，……）
  可以存在；检测器在每次 provider 调用前剥离它们，进化框架只可在显式启用
  gold 增强时使用它们。

进化 CLI 接受本目录的记录，可以是 JSON 映射，也可以是 JSONL 序列；见
`sehd_rise/detection/detector.py` 与 `sehd_rise/api/evolution.py` 中的记录加载入口。
