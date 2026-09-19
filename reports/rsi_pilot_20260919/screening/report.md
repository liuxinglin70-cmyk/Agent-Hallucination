# Research run rsi_pilot_screening_20260919 — screening_complete

- mechanism: quoted_content_vs_delivered
- evaluation mode: fresh full detection, no judge cache
- budget: {"caps": {"baseline_case_evaluations": 0, "screening_case_evaluations": 16, "proposer_calls_per_route": 3, "max_versions": 4}, "used": {"baseline_case_evaluations": 0, "screening_case_evaluations": 8, "proposer_calls": {"B": 2, "C": 1}, "versions_created": 3}}

## v001 (route B, draft) — fail / no candidate
- reasons: ["training failure BFCL/002.json not repaired (state='verified_error', step=6)", "training failure BFCL/153.json not repaired (state='verified_clean', step=None)"]
- training: {"BFCL/002.json": {"repaired": false, "state": "verified_error", "responsible_step": 6, "target_step": 6, "target_aligned": true}, "BFCL/153.json": {"repaired": false, "state": "verified_clean", "responsible_step": null, "target_step": 14, "target_aligned": true}}
- neighbors: {"BFCL/020.json": {"clean": true, "state": "verified_clean"}, "BFCL/053.json": {"clean": true, "state": "verified_clean"}}

## v002 (route B, revision) — PASS
- reasons: []
- training: {"BFCL/002.json": {"repaired": true, "state": "verified_error", "responsible_step": 6, "target_step": 6, "target_aligned": true}, "BFCL/153.json": {"repaired": true, "state": "verified_error", "responsible_step": 14, "target_step": 14, "target_aligned": true}}
- neighbors: {"BFCL/020.json": {"clean": true, "state": "verified_clean"}, "BFCL/053.json": {"clean": true, "state": "verified_clean"}}

## v003 (route C, draft) — fail / no candidate
