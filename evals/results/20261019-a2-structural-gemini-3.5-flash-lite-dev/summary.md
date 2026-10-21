# Eval: a2 on gemini-3.5-flash-lite

- Started: 2026-10-19T08:28:40+00:00
- Provider: gemini; judge: location-only; prompt: planner.v1, investigator.v5, agent_loop.v2
- Cases: 19 (split: dev); samples per case: 1; location tolerance: ±3 lines
- Tools: structural
- Code: commit `decfa6e0c6e7` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 73% |
| Recall | 73% |
| F0.5 | 73% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 25% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 88% |
| Findings (TP / partial / FP) | 11 (8 / 0 / 3) |
| Runs (failed / served from cache) | 19 (0 / 0) |
| Tokens (in / out) | 520593 / 13691 |
| Review requests (from cache) | 108 (10) |
| Requests per run (mean / max) | 5.7 / 10 |
| Real provider requests (retries and judge included) | 98 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 19 (0) |
| Outcomes | confirmed 11, inconclusive 2, refuted 6 |
| Why loops stopped | submitted 17, turn_budget 2 |
| Turns / tool calls | 89 / 71 |
| Submissions rejected by the evidence check | 5 |
| Duplicate confirmations | 0 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 4 |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 6 |  |
| clean-inventory-shared-int-parsing | clean | 0 | 0 | - | - | 6 |  |
| clean-werkzeug-plain-int-docs | clean | 0 | 0 | - | - | 1 |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 3 |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 4 |  |
| decoy-inventory-require-role-only | decoy | 0 | 1 | - | 1 | 6 |  |
| decoy-werkzeug-debugger-resource-cache | decoy | 0 | 0 | - | - | 1 |  |
| seeded-broken-caller-signature | seeded | 0 | 1 | 0/1 | - | 6 |  |
| seeded-inventory-uncapped-limit | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-missing-authorization | seeded | 0 | 0 | 0/1 | - | 7 |  |
| seeded-none-dereference | seeded | 0 | 2 | 1/1 | - | 9 |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-path-traversal-attachments | seeded | 0 | 0 | 0/1 | - | 10 |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - | 6 |  |
| seeded-werkzeug-gen-salt-random | seeded | 0 | 1 | 1/1 | - | 4 |  |
| seeded-werkzeug-header-newlines | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-werkzeug-input-stream-fallback | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-werkzeug-redirect-escape | seeded | 0 | 1 | 1/1 | - | 7 |  |
