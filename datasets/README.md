# Raw Datasets

Place raw dataset files in this directory.  They are never tracked by git.

Expected layout:

```text
datasets/
├── README.md            # this file
└── raw/                 # actual dataset files (git-ignored)
    └── <dataset>/
        ├── cases/       # raw trajectory records
        └── manifest.jsonl
```

## Record format

Each raw trajectory is a JSON object consumed by the detector:

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

- `history` must be a non-empty list.
- Evaluator-only fields (`gold_label`, `is_hallucination`,
  `hallucination_step`, ...) may be present; the detector strips them before
  every provider call, and the evolution framework may use them only under an
  explicit gold-enhanced opt-in.

The evolution CLI accepts this directory's records as a JSON mapping or a
JSONL sequence, see `src/sehd_v6/evolution/README.md`.
