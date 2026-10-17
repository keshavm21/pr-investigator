# Eval: a2 on gemini-3.5-flash-lite

- Started: 2026-10-16T14:21:09+00:00
- Provider: gemini; judge: location-only; prompt: planner.v1, investigator.v2, agent_loop.v1
- Cases: 10; samples per case: 1; location tolerance: ±3 lines
- Code: commit `f8b55595d43c` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 100% |
| Recall | 83% |
| F0.5 | 96% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 5 (5 / 0 / 0) |
| Runs (failed / served from cache) | 10 (0 / 0) |
| Tokens (in / out) | 218315 / 9371 |
| Review requests (from cache) | 62 (0) |
| Requests per run (mean / max) | 6.2 / 11 |
| Real provider requests (retries and judge included) | 73 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 12 (0) |
| Outcomes | confirmed 6, inconclusive 1, refuted 5 |
| Why loops stopped | submitted 11, turn_budget 1 |
| Turns / tool calls | 52 / 35 |
| Submissions rejected by the evidence check | 7 |
| Duplicate confirmations | 1 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 4 |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 5 |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 3 |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 5 |  |
| seeded-broken-caller-signature | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-missing-authorization | seeded | 0 | 0 | 0/1 | - | 7 |  |
| seeded-none-dereference | seeded | 0 | 1 | 1/1 | - | 11 |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 6 |  |
| seeded-path-traversal-attachments | seeded | 0 | 1 | 1/1 | - | 8 |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - | 6 |  |
