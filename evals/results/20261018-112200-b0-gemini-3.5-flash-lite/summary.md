# Eval: b0 on gemini-3.5-flash-lite

- Started: 2026-10-18T11:22:00+00:00
- Provider: gemini; judge: location-only; prompt: baseline_review.v1
- Cases: 15 (split: test); samples per case: 1; location tolerance: ±3 lines
- Code: commit `757faed0d81a`

| Metric | Value |
|---|---|
| Precision | 86% |
| Recall | 46% |
| F0.5 | 73% |
| False positives per clean run | 0.50 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 7 (6 / 0 / 1) |
| Runs (failed / served from cache) | 15 (0 / 0) |
| Tokens (in / out) | 14823 / 2131 |
| Review requests (from cache) | 15 (0) |
| Requests per run (mean / max) | 1.0 / 1 |
| Real provider requests (retries and judge included) | 15 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-werkzeug-content-type-helper | clean | 0 | 1 | - | - | 1 |  |
| clean-werkzeug-range-to-header | clean | 0 | 0 | - | - | 1 |  |
| decoy-inventory-export-write | decoy | 0 | 0 | - | - | 1 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 0 | 0 | - | - | 1 |  |
| decoy-werkzeug-send-static | decoy | 0 | 0 | - | - | 1 |  |
| seeded-inventory-cancel-order | seeded | 0 | 1 | 1/2 | - | 1 |  |
| seeded-inventory-set-role | seeded | 0 | 1 | 1/2 | - | 1 |  |
| seeded-inventory-update-price-convention | seeded | 0 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 0 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 0 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-number-converter-rename | seeded | 0 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-password-compare | seeded | 0 | 1 | 1/1 | - | 1 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 0 | 1 | 1/2 | - | 1 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 0 | 1 | 1/1 | - | 1 |  |
| seeded-werkzeug-uuid-converter | seeded | 0 | 1 | 1/1 | - | 1 |  |
