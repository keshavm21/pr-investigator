# Eval: a3 on gemini-3.5-flash-lite

- Started: 2026-10-21T06:56:55+00:00
- Provider: gemini; judge: location-only; prompt: planner.v1, investigator.v3, agent_loop.v2, skeptic.v1
- Cases: 19 (split: dev); samples per case: 1; location tolerance: ±3 lines
- Code: commit `f881682fb701` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 90% |
| Recall | 82% |
| F0.5 | 88% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 89% |
| Findings (TP / partial / FP) | 10 (9 / 0 / 1) |
| Runs (failed / served from cache) | 19 (0 / 10) |
| Tokens (in / out) | 535591 / 15153 |
| Review requests (from cache) | 149 (105) |
| Requests per run (mean / max) | 7.8 / 19 |
| Real provider requests (retries and judge included) | 44 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 19 (0) |
| Outcomes | confirmed 10, inconclusive 3, refuted 6 |
| Why loops stopped | submitted 26, turn_budget 3 |
| Turns / tool calls | 130 / 103 |
| Submissions rejected by the evidence check | 4 |
| Duplicate confirmations | 0 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 5 (cached) |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 5 (cached) |  |
| clean-inventory-shared-int-parsing | clean | 0 | 0 | - | - | 3 (cached) |  |
| clean-werkzeug-plain-int-docs | clean | 0 | 0 | - | - | 1 (cached) |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 4 (cached) |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 4 (cached) |  |
| decoy-inventory-require-role-only | decoy | 0 | 0 | - | - | 7 (cached) |  |
| decoy-werkzeug-debugger-resource-cache | decoy | 0 | 0 | - | - | 1 (cached) |  |
| seeded-broken-caller-signature | seeded | 0 | 1 | 1/1 | - | 10 |  |
| seeded-inventory-uncapped-limit | seeded | 0 | 1 | 1/1 | - | 10 |  |
| seeded-missing-authorization | seeded | 0 | 1 | 1/1 | - | 11 |  |
| seeded-none-dereference | seeded | 0 | 1 | 1/1 | - | 15 |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 11 |  |
| seeded-path-traversal-attachments | seeded | 0 | 2 | 1/1 | - | 19 |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - | 9 |  |
| seeded-werkzeug-gen-salt-random | seeded | 0 | 1 | 1/1 | - | 8 |  |
| seeded-werkzeug-header-newlines | seeded | 0 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-werkzeug-input-stream-fallback | seeded | 0 | 0 | 0/1 | - | 7 (cached) |  |
| seeded-werkzeug-redirect-escape | seeded | 0 | 1 | 1/1 | - | 12 |  |
