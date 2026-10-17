# Eval: a2 on gemini-3.5-flash-lite

- Started: 2026-10-17T07:24:39+00:00
- Provider: gemini; judge: location-only; prompt: planner.v1, investigator.v3, agent_loop.v1
- Cases: 10; samples per case: 1; location tolerance: ±3 lines
- Code: commit `e73e28b6d09d` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 80% |
| Recall | 67% |
| F0.5 | 77% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 5 (4 / 0 / 1) |
| Runs (failed / served from cache) | 10 (0 / 0) |
| Tokens (in / out) | 258325 / 9605 |
| Review requests (from cache) | 70 (10) |
| Requests per run (mean / max) | 7.0 / 11 |
| Real provider requests (retries and judge included) | 60 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 12 (0) |
| Outcomes | confirmed 5, inconclusive 3, refuted 4 |
| Why loops stopped | submitted 9, turn_budget 3 |
| Turns / tool calls | 60 / 41 |
| Submissions rejected by the evidence check | 10 |
| Duplicate confirmations | 0 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 7 |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 5 |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 5 |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 5 |  |
| seeded-broken-caller-signature | seeded | 0 | 0 | 0/1 | - | 7 |  |
| seeded-missing-authorization | seeded | 0 | 1 | 1/1 | - | 6 |  |
| seeded-none-dereference | seeded | 0 | 1 | 1/1 | - | 11 |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 6 |  |
| seeded-path-traversal-attachments | seeded | 0 | 2 | 1/1 | - | 11 |  |
| seeded-sql-injection-search | seeded | 0 | 0 | 0/1 | - | 7 |  |
