# Eval: a1 on gemini-3.5-flash-lite

- Started: 2026-10-20T18:51:41+00:00
- Provider: gemini; judge: location-only; prompt: single_agent.v5, agent_loop.v2
- Cases: 15 (split: test); samples per case: 2; location tolerance: ±3 lines
- Tools: structural; context pack and leads: off
- Code: commit `decfa6e0c6e7` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 88% |
| Recall | 27% |
| F0.5 | 60% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 86% |
| Findings (TP / partial / FP) | 8 (7 / 0 / 1) |
| Runs (failed / served from cache) | 30 (0 / 0) |
| Tokens (in / out) | 1043704 / 9340 |
| Review requests (from cache) | 171 (0) |
| Requests per run (mean / max) | 5.7 / 12 |
| Real provider requests (retries and judge included) | 179 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 0 (0) |
| Outcomes | none |
| Why loops stopped | submitted 30 |
| Turns / tool calls | 171 / 139 |
| Submissions rejected by the evidence check | 2 |
| Duplicate confirmations | 0 |
| Provider retries | 8 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-werkzeug-content-type-helper | clean | 0 | 0 | - | - | 5 |  |
| clean-werkzeug-content-type-helper | clean | 1 | 0 | - | - | 5 |  |
| clean-werkzeug-range-to-header | clean | 0 | 0 | - | - | 6 |  |
| clean-werkzeug-range-to-header | clean | 1 | 0 | - | - | 4 |  |
| decoy-inventory-export-write | decoy | 0 | 0 | - | - | 7 |  |
| decoy-inventory-export-write | decoy | 1 | 0 | - | - | 7 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 0 | 0 | - | - | 5 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 1 | 0 | - | - | 5 |  |
| decoy-werkzeug-send-static | decoy | 0 | 0 | - | - | 4 |  |
| decoy-werkzeug-send-static | decoy | 1 | 0 | - | - | 4 |  |
| seeded-inventory-cancel-order | seeded | 0 | 1 | 1/2 | - | 4 |  |
| seeded-inventory-cancel-order | seeded | 1 | 1 | 1/2 | - | 6 |  |
| seeded-inventory-set-role | seeded | 0 | 1 | 1/2 | - | 5 |  |
| seeded-inventory-set-role | seeded | 1 | 1 | 1/2 | - | 5 |  |
| seeded-inventory-update-price-convention | seeded | 0 | 0 | 0/1 | - | 5 |  |
| seeded-inventory-update-price-convention | seeded | 1 | 0 | 0/1 | - | 5 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 0 | 0 | 0/1 | - | 12 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 1 | 0 | 0/1 | - | 11 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 0 | 0 | 0/1 | - | 7 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 1 | 0 | 0/1 | - | 10 |  |
| seeded-werkzeug-number-converter-rename | seeded | 0 | 1 | 0/1 | - | 7 |  |
| seeded-werkzeug-number-converter-rename | seeded | 1 | 1 | 1/1 | - | 4 |  |
| seeded-werkzeug-password-compare | seeded | 0 | 1 | 1/1 | - | 3 |  |
| seeded-werkzeug-password-compare | seeded | 1 | 1 | 1/1 | - | 2 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 0 | 0 | 0/2 | - | 5 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 1 | 0 | 0/2 | - | 6 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 0 | 0 | 0/1 | - | 6 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 1 | 0 | 0/1 | - | 6 |  |
| seeded-werkzeug-uuid-converter | seeded | 0 | 0 | 0/1 | - | 5 |  |
| seeded-werkzeug-uuid-converter | seeded | 1 | 0 | 0/1 | - | 5 |  |
