# Eval: a1 on gemini-3.5-flash-lite

- Started: 2026-10-18T11:23:12+00:00
- Provider: gemini; judge: location-only; prompt: single_agent.v3, agent_loop.v2
- Cases: 15 (split: test); samples per case: 1; location tolerance: ±3 lines
- Code: commit `757faed0d81a` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 75% |
| Recall | 23% |
| F0.5 | 52% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 4 (3 / 0 / 1) |
| Runs (failed / served from cache) | 15 (0 / 0) |
| Tokens (in / out) | 396378 / 5026 |
| Review requests (from cache) | 83 (0) |
| Requests per run (mean / max) | 5.5 / 11 |
| Real provider requests (retries and judge included) | 92 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 0 (0) |
| Outcomes | none |
| Why loops stopped | submitted 15 |
| Turns / tool calls | 83 / 68 |
| Submissions rejected by the evidence check | 2 |
| Duplicate confirmations | 0 |
| Provider retries | 9 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-werkzeug-content-type-helper | clean | 0 | 0 | - | - | 3 |  |
| clean-werkzeug-range-to-header | clean | 0 | 0 | - | - | 5 |  |
| decoy-inventory-export-write | decoy | 0 | 0 | - | - | 6 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 0 | 0 | - | - | 4 |  |
| decoy-werkzeug-send-static | decoy | 0 | 0 | - | - | 5 |  |
| seeded-inventory-cancel-order | seeded | 0 | 1 | 1/2 | - | 4 |  |
| seeded-inventory-set-role | seeded | 0 | 1 | 1/2 | - | 7 |  |
| seeded-inventory-update-price-convention | seeded | 0 | 0 | 0/1 | - | 5 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 0 | 0 | 0/1 | - | 11 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 0 | 0 | 0/1 | - | 7 |  |
| seeded-werkzeug-number-converter-rename | seeded | 0 | 1 | 0/1 | - | 6 |  |
| seeded-werkzeug-password-compare | seeded | 0 | 1 | 1/1 | - | 3 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 0 | 0 | 0/2 | - | 5 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 0 | 0 | 0/1 | - | 5 |  |
| seeded-werkzeug-uuid-converter | seeded | 0 | 0 | 0/1 | - | 7 |  |
