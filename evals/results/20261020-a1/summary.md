# Eval: a1 on gemini-3.5-flash-lite

- Started: 2026-10-20T18:04:01+00:00
- Provider: gemini; judge: location-only; prompt: single_agent.v3, agent_loop.v2
- Cases: 15 (split: test); samples per case: 2; location tolerance: ±3 lines
- Tools: base; context pack and leads: off
- Code: commit `decfa6e0c6e7` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 75% |
| Recall | 23% |
| F0.5 | 52% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 20% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 8 (6 / 0 / 2) |
| Runs (failed / served from cache) | 30 (1 / 15) |
| Tokens (in / out) | 792711 / 9712 |
| Review requests (from cache) | 164 (83) |
| Requests per run (mean / max) | 5.7 / 11 |
| Real provider requests (retries and judge included) | 101 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 0 (0) |
| Outcomes | none |
| Why loops stopped | submitted 29 |
| Turns / tool calls | 164 / 136 |
| Submissions rejected by the evidence check | 4 |
| Duplicate confirmations | 0 |
| Provider retries | 11 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-werkzeug-content-type-helper | clean | 0 | 0 | - | - | 3 (cached) |  |
| clean-werkzeug-content-type-helper | clean | 1 | 0 | - | - | 3 |  |
| clean-werkzeug-range-to-header | clean | 0 | 0 | - | - | 5 (cached) |  |
| clean-werkzeug-range-to-header | clean | 1 | 0 | - | - | 5 |  |
| decoy-inventory-export-write | decoy | 0 | 0 | - | - | 6 (cached) |  |
| decoy-inventory-export-write | decoy | 1 | 1 | - | 1 | 6 |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 0 | 0 | - | - | 4 (cached) |  |
| decoy-werkzeug-proxy-fix-logging | decoy | 1 | 0 | - | - | 0 | LLMTransientError: Gemini server error (503): This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later. |
| decoy-werkzeug-send-static | decoy | 0 | 0 | - | - | 5 (cached) |  |
| decoy-werkzeug-send-static | decoy | 1 | 0 | - | - | 4 |  |
| seeded-inventory-cancel-order | seeded | 0 | 1 | 1/2 | - | 4 (cached) |  |
| seeded-inventory-cancel-order | seeded | 1 | 1 | 1/2 | - | 4 |  |
| seeded-inventory-set-role | seeded | 0 | 1 | 1/2 | - | 7 (cached) |  |
| seeded-inventory-set-role | seeded | 1 | 1 | 1/2 | - | 5 |  |
| seeded-inventory-update-price-convention | seeded | 0 | 0 | 0/1 | - | 5 (cached) |  |
| seeded-inventory-update-price-convention | seeded | 1 | 0 | 0/1 | - | 4 |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 0 | 0 | 0/1 | - | 11 (cached) |  |
| seeded-werkzeug-content-range-off-by-one | seeded | 1 | 0 | 0/1 | - | 10 |  |
| seeded-werkzeug-debugger-pin-check | seeded | 0 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-werkzeug-debugger-pin-check | seeded | 1 | 0 | 0/1 | - | 7 |  |
| seeded-werkzeug-number-converter-rename | seeded | 0 | 1 | 0/1 | - | 6 (cached) |  |
| seeded-werkzeug-number-converter-rename | seeded | 1 | 0 | 0/1 | - | 11 |  |
| seeded-werkzeug-password-compare | seeded | 0 | 1 | 1/1 | - | 3 (cached) |  |
| seeded-werkzeug-password-compare | seeded | 1 | 1 | 1/1 | - | 3 |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 0 | 0 | 0/2 | - | 5 (cached) |  |
| seeded-werkzeug-proxy-fix-client-ip | seeded | 1 | 0 | 0/2 | - | 5 |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 0 | 0 | 0/1 | - | 5 (cached) |  |
| seeded-werkzeug-safe-join-dotdot | seeded | 1 | 0 | 0/1 | - | 5 |  |
| seeded-werkzeug-uuid-converter | seeded | 0 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-werkzeug-uuid-converter | seeded | 1 | 0 | 0/1 | - | 9 |  |
