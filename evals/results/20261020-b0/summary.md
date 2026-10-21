# Eval: b0 on gemini-3.5-flash-lite

- Started: 2026-10-20T17:54:04+00:00
- Provider: gemini; judge: location-only; prompt: baseline_review.v1
- Cases: 15 (split: test); samples per case: 2; location tolerance: ±3 lines
- Code: commit `decfa6e0c6e7` with uncommitted changes

| Metric | Value |
|---|---|
| Precision | 79% |
| Recall | 42% |
| F0.5 | 67% |
| False positives per clean run | 0.50 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 14 (11 / 0 / 3) |
| Runs (failed / served from cache) | 30 (0 / 15) |
| Tokens (in / out) | 29646 / 4207 |
| Review requests (from cache) | 30 (15) |
| Requests per run (mean / max) | 1.0 / 1 |
| Real provider requests (retries and judge included) | 16 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-werkzeug-content-type-helper | clean | 0 | 1 | - | - | 1 (cached) |  |
| clean-werkzeug-content-type-helper | clean | 1 | 1 | - | - | 1 |  |
| clean-werkzeug-range-to-header | clean | 0 | 0 | - | - | 1 (cached) |  |
| clean-werkzeug-range-to-header | clean | 1 | 0 | - | - | 1 |  |
| decoy-inventory-export-write | decoy | 0 | 0 | - | - | 1 (cached) |  |
| decoy-inventory-export-write | decoy | 1 | 0 | - | - | 1 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 0 | 0 | - | - | 1 (cached) |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 1 | 0 | - | - | 1 |  |
| decoy-werkzeug-send-static | decoy | 0 | 0 | - | - | 1 (cached) |  |
| decoy-werkzeug-send-static | decoy | 1 | 0 | - | - | 1 |  |
| seeded-inventory-cancel-order | seeded | 0 | 1 | 1/2 | - | 1 (cached) |  |
| seeded-inventory-cancel-order | seeded | 1 | 1 | 1/2 | - | 1 |  |
| seeded-inventory-set-role | seeded | 0 | 1 | 1/2 | - | 1 (cached) |  |
| seeded-inventory-set-role | seeded | 1 | 1 | 1/2 | - | 1 |  |
| seeded-inventory-update-price-convention | seeded | 0 | 0 | 0/1 | - | 1 (cached) |  |
| seeded-inventory-update-price-convention | seeded | 1 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 0 | 0 | 0/1 | - | 1 (cached) |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 1 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 0 | 0 | 0/1 | - | 1 (cached) |  |
| seeded-werkzeug-debugger-pin-check | seeded | 1 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-number-converter-rename | seeded | 0 | 0 | 0/1 | - | 1 (cached) |  |
| seeded-werkzeug-number-converter-rename | seeded | 1 | 0 | 0/1 | - | 1 |  |
| seeded-werkzeug-password-compare | seeded | 0 | 1 | 1/1 | - | 1 (cached) |  |
| seeded-werkzeug-password-compare | seeded | 1 | 1 | 1/1 | - | 1 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 0 | 1 | 1/2 | - | 1 (cached) |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 1 | 1 | 1/2 | - | 1 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 0 | 1 | 1/1 | - | 1 (cached) |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 1 | 1 | 0/1 | - | 1 |  |
| seeded-werkzeug-uuid-converter | seeded | 0 | 1 | 1/1 | - | 1 (cached) |  |
| seeded-werkzeug-uuid-converter | seeded | 1 | 1 | 1/1 | - | 1 |  |
