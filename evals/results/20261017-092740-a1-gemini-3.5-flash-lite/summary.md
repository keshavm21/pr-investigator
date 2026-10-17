# Eval: a1 on gemini-3.5-flash-lite

- Started: 2026-10-17T09:27:40+00:00
- Provider: gemini; judge: location-only; prompt: single_agent.v3, agent_loop.v2
- Cases: 10; samples per case: 2; location tolerance: ±3 lines
- Code: commit `009d2123bcbc` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 100% |
| Recall | 100% |
| F0.5 | 100% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 12 (12 / 0 / 0) |
| Runs (failed / served from cache) | 20 (0 / 10) |
| Tokens (in / out) | 362568 / 7511 |
| Review requests (from cache) | 87 (39) |
| Requests per run (mean / max) | 4.3 / 10 |
| Real provider requests (retries and judge included) | 48 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 0 (0) |
| Outcomes | none |
| Why loops stopped | submitted 20 |
| Turns / tool calls | 87 / 67 |
| Submissions rejected by the evidence check | 3 |
| Duplicate confirmations | 0 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 4 (cached) |  |
| clean-extract-rows-helper | clean | 1 | 0 | - | - | 4 |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 4 (cached) |  |
| clean-health-endpoint | clean | 1 | 0 | - | - | 4 |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 3 (cached) |  |
| decoy-admin-router-auth | decoy | 1 | 0 | - | - | 10 |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 3 (cached) |  |
| decoy-allowlisted-sort | decoy | 1 | 0 | - | - | 3 |  |
| seeded-broken-caller-signature | seeded | 0 | 1 | 1/1 | - | 7 (cached) |  |
| seeded-broken-caller-signature | seeded | 1 | 1 | 1/1 | - | 8 |  |
| seeded-missing-authorization | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-missing-authorization | seeded | 1 | 1 | 1/1 | - | 4 |  |
| seeded-none-dereference | seeded | 0 | 1 | 1/1 | - | 2 (cached) |  |
| seeded-none-dereference | seeded | 1 | 1 | 1/1 | - | 3 |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-off-by-one-pagination | seeded | 1 | 1 | 1/1 | - | 3 |  |
| seeded-path-traversal-attachments | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-path-traversal-attachments | seeded | 1 | 1 | 1/1 | - | 4 |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-sql-injection-search | seeded | 1 | 1 | 1/1 | - | 5 |  |
