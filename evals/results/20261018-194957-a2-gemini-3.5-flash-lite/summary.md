# Eval: a2 on gemini-3.5-flash-lite

- Started: 2026-10-18T19:49:57+00:00
- Provider: gemini; judge: location-only; prompt: planner.v1, investigator.v3, agent_loop.v2
- Cases: 15 (split: test); samples per case: 1; location tolerance: ±3 lines
- Code: commit `b0479ecf49c1` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 73% |
| Recall | 62% |
| F0.5 | 70% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 33% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 88% |
| Findings (TP / partial / FP) | 11 (8 / 0 / 3) |
| Runs (failed / served from cache) | 15 (0 / 0) |
| Tokens (in / out) | 462997 / 14512 |
| Review requests (from cache) | 114 (0) |
| Requests per run (mean / max) | 7.6 / 18 |
| Real provider requests (retries and judge included) | 114 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 20 (0) |
| Outcomes | confirmed 12, inconclusive 1, refuted 7 |
| Why loops stopped | submitted 19, turn_budget 1 |
| Turns / tool calls | 99 / 78 |
| Submissions rejected by the evidence check | 3 |
| Duplicate confirmations | 1 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-werkzeug-content-type-helper | clean | 0 | 0 | - | - | 3 |  |
| clean-werkzeug-range-to-header | clean | 0 | 0 | - | - | 5 |  |
| decoy-inventory-export-write | decoy | 0 | 0 | - | - | 10 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 0 | 1 | - | 1 | 5 |  |
| decoy-werkzeug-send-static | decoy | 0 | 0 | - | - | 7 |  |
| seeded-inventory-cancel-order | seeded | 0 | 2 | 1/2 | - | 11 |  |
| seeded-inventory-set-role | seeded | 0 | 1 | 1/2 | - | 18 |  |
| seeded-inventory-update-price-convention | seeded | 0 | 0 | 0/1 | - | 5 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-werkzeug-number-converter-rename | seeded | 0 | 1 | 0/1 | - | 6 |  |
| seeded-werkzeug-password-compare | seeded | 0 | 1 | 1/1 | - | 5 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 0 | 2 | 2/2 | - | 12 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 0 | 0 | 0/1 | - | 7 |  |
| seeded-werkzeug-uuid-converter | seeded | 0 | 1 | 1/1 | - | 6 |  |
