# Eval: a3 on gemini-3.5-flash-lite

- Started: 2026-10-22T10:09:52+00:00
- Provider: gemini; judge: location-only; prompt: planner.v1, investigator.v3, agent_loop.v2, skeptic.v1
- Cases: 15 (split: test); samples per case: 3; location tolerance: ±3 lines
- Code: commit `c04ff9803d1a` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 83% |
| Recall | 64% |
| F0.5 | 79% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 33% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 72% |
| Findings (TP / partial / FP) | 30 (25 / 0 / 5) |
| Runs (failed / served from cache) | 45 (0 / 24) |
| Tokens (in / out) | 1896110 / 53426 |
| Review requests (from cache) | 459 (367) |
| Requests per run (mean / max) | 10.2 / 23 |
| Real provider requests (retries and judge included) | 92 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 58 (0) |
| Outcomes | confirmed 33, inconclusive 5, refuted 20 |
| Why loops stopped | submitted 80, turn_budget 9 |
| Turns / tool calls | 414 / 344 |
| Submissions rejected by the evidence check | 12 |
| Duplicate confirmations | 2 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-werkzeug-content-type-helper | clean | 0 | 0 | - | - | 3 (cached) |  |
| clean-werkzeug-content-type-helper | clean | 1 | 0 | - | - | 4 (cached) |  |
| clean-werkzeug-content-type-helper | clean | 2 | 0 | - | - | 4 (cached) |  |
| clean-werkzeug-range-to-header | clean | 0 | 0 | - | - | 5 (cached) |  |
| clean-werkzeug-range-to-header | clean | 1 | 0 | - | - | 5 (cached) |  |
| clean-werkzeug-range-to-header | clean | 2 | 0 | - | - | 4 |  |
| decoy-inventory-export-write | decoy | 0 | 0 | - | - | 10 (cached) |  |
| decoy-inventory-export-write | decoy | 1 | 1 | - | 1 | 14 |  |
| decoy-inventory-export-write | decoy | 2 | 0 | - | - | 9 (cached) |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 0 | 1 | - | 1 | 10 (cached) |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 1 | 0 | - | - | 10 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 2 | 1 | - | 1 | 11 |  |
| decoy-werkzeug-send-static | decoy | 0 | 0 | - | - | 7 |  |
| decoy-werkzeug-send-static | decoy | 1 | 0 | - | - | 13 (cached) |  |
| decoy-werkzeug-send-static | decoy | 2 | 0 | - | - | 4 (cached) |  |
| seeded-inventory-cancel-order | seeded | 0 | 2 | 1/2 | - | 19 (cached) |  |
| seeded-inventory-cancel-order | seeded | 1 | 2 | 2/2 | - | 18 |  |
| seeded-inventory-cancel-order | seeded | 2 | 1 | 1/2 | - | 12 |  |
| seeded-inventory-set-role | seeded | 0 | 1 | 1/2 | - | 21 (cached) |  |
| seeded-inventory-set-role | seeded | 1 | 1 | 1/2 | - | 17 |  |
| seeded-inventory-set-role | seeded | 2 | 1 | 1/2 | - | 12 |  |
| seeded-inventory-update-price-convention | seeded | 0 | 0 | 0/1 | - | 5 (cached) |  |
| seeded-inventory-update-price-convention | seeded | 1 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-inventory-update-price-convention | seeded | 2 | 0 | 0/1 | - | 5 (cached) |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 0 | 1 | 1/1 | - | 12 (cached) |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 1 | 1 | 1/1 | - | 12 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 2 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-werkzeug-debugger-pin-check | seeded | 0 | 1 | 1/1 | - | 12 (cached) |  |
| seeded-werkzeug-debugger-pin-check | seeded | 1 | 1 | 1/1 | - | 12 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 2 | 1 | 1/1 | - | 12 |  |
| seeded-werkzeug-number-converter-rename | seeded | 0 | 1 | 0/1 | - | 10 (cached) |  |
| seeded-werkzeug-number-converter-rename | seeded | 1 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-werkzeug-number-converter-rename | seeded | 2 | 1 | 1/1 | - | 12 |  |
| seeded-werkzeug-password-compare | seeded | 0 | 1 | 1/1 | - | 9 (cached) |  |
| seeded-werkzeug-password-compare | seeded | 1 | 1 | 1/1 | - | 10 |  |
| seeded-werkzeug-password-compare | seeded | 2 | 1 | 1/1 | - | 6 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 0 | 2 | 2/2 | - | 20 (cached) |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 1 | 2 | 2/2 | - | 23 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 2 | 1 | 1/2 | - | 11 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 0 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 1 | 1 | 1/1 | - | 9 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 2 | 0 | 0/1 | - | 6 (cached) |  |
| seeded-werkzeug-uuid-converter | seeded | 0 | 1 | 1/1 | - | 10 |  |
| seeded-werkzeug-uuid-converter | seeded | 1 | 1 | 1/1 | - | 12 |  |
| seeded-werkzeug-uuid-converter | seeded | 2 | 1 | 1/1 | - | 11 |  |
