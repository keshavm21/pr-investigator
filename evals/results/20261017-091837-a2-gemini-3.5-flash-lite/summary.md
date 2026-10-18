# Eval: a2 on gemini-3.5-flash-lite

- Started: 2026-10-17T09:18:37+00:00
- Provider: gemini; judge: location-only; prompt: planner.v1, investigator.v3, agent_loop.v2
- Cases: 10; samples per case: 2; location tolerance: ±3 lines
- Code: commit `009d2123bcbc` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 86% |
| Recall | 100% |
| F0.5 | 88% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 83% |
| Findings (TP / partial / FP) | 14 (12 / 0 / 2) |
| Runs (failed / served from cache) | 20 (0 / 4) |
| Tokens (in / out) | 535954 / 16931 |
| Review requests (from cache) | 147 (56) |
| Requests per run (mean / max) | 7.3 / 16 |
| Real provider requests (retries and judge included) | 91 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 27 (0) |
| Outcomes | confirmed 14, inconclusive 2, refuted 11 |
| Why loops stopped | submitted 25, turn_budget 2 |
| Turns / tool calls | 127 / 100 |
| Submissions rejected by the evidence check | 3 |
| Duplicate confirmations | 0 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 5 (cached) |  |
| clean-extract-rows-helper | clean | 1 | 0 | - | - | 5 |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 5 (cached) |  |
| clean-health-endpoint | clean | 1 | 0 | - | - | 11 |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 4 (cached) |  |
| decoy-admin-router-auth | decoy | 1 | 0 | - | - | 5 |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 4 (cached) |  |
| decoy-allowlisted-sort | decoy | 1 | 0 | - | - | 4 |  |
| seeded-broken-caller-signature | seeded | 0 | 1 | 1/1 | - | 6 |  |
| seeded-broken-caller-signature | seeded | 1 | 1 | 1/1 | - | 5 |  |
| seeded-missing-authorization | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-missing-authorization | seeded | 1 | 1 | 1/1 | - | 16 |  |
| seeded-none-dereference | seeded | 0 | 1 | 1/1 | - | 11 |  |
| seeded-none-dereference | seeded | 1 | 1 | 1/1 | - | 13 |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 6 |  |
| seeded-off-by-one-pagination | seeded | 1 | 1 | 1/1 | - | 7 |  |
| seeded-path-traversal-attachments | seeded | 0 | 2 | 1/1 | - | 10 |  |
| seeded-path-traversal-attachments | seeded | 1 | 2 | 1/1 | - | 10 |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - | 6 |  |
| seeded-sql-injection-search | seeded | 1 | 1 | 1/1 | - | 7 |  |
