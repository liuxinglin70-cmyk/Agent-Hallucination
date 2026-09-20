# Research run rsi_round4_20260920 — screening_complete

- mechanism: quoted_content_vs_delivered
- evaluation mode: fresh full detection, no judge cache
- budget: {"caps": {"baseline_case_evaluations": 4, "screening_case_evaluations": 8, "proposer_calls_per_route": 2, "max_versions": 2}, "used": {"baseline_case_evaluations": 4, "screening_case_evaluations": 4, "proposer_calls": {"C": 1}, "versions_created": 1}}

## v001 (route C, draft) — PASS
- reasons: []
- training: {"BFCL/002.json": {"repaired": true, "state": "verified_error", "responsible_step": 6, "target_step": 6, "target_aligned": true}, "BFCL/153.json": {"repaired": true, "state": "verified_error", "responsible_step": 14, "target_step": 14, "target_aligned": true}}
- neighbors: {"BFCL/020.json": {"clean": true, "state": "verified_clean"}, "BFCL/053.json": {"clean": true, "state": "verified_clean"}}
