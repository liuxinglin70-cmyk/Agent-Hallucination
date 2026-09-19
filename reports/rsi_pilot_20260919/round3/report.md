# Research run rsi_round3_20260920 — screening_complete

- mechanism: quoted_content_vs_delivered
- evaluation mode: fresh full detection, no judge cache
- budget: {"caps": {"baseline_case_evaluations": 4, "screening_case_evaluations": 4, "proposer_calls_per_route": 0, "max_versions": 1}, "used": {"baseline_case_evaluations": 4, "screening_case_evaluations": 4, "proposer_calls": {}, "versions_created": 2}}

## v001 (route C, draft) — fail / no candidate
- reasons: ["normal neighbor BFCL/020.json degraded to 'verified_error'"]
- training: {"BFCL/002.json": {"repaired": true, "state": "verified_error", "responsible_step": 6, "target_step": 6, "target_aligned": true}, "BFCL/153.json": {"repaired": true, "state": "verified_error", "responsible_step": 14, "target_step": 14, "target_aligned": true}}
- neighbors: {"BFCL/020.json": {"clean": false, "state": "verified_error"}, "BFCL/053.json": {"clean": true, "state": "verified_clean"}}

## v002 (route C, draft) — fail / no candidate
