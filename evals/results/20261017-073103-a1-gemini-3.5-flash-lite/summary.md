# Eval: a1 on gemini-3.5-flash-lite

- Started: 2026-10-17T07:31:03+00:00
- Provider: gemini; judge: location-only; prompt: single_agent.v3, agent_loop.v1
- Cases: 10; samples per case: 1; location tolerance: ±3 lines
- Code: commit `cb2f4ab3845b` with uncommitted changes
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
| Findings (TP / partial / FP) | 6 (6 / 0 / 0) |
| Runs (failed / served from cache) | 10 (0 / 0) |
| Tokens (in / out) | 158098 / 3437 |
| Review requests (from cache) | 39 (0) |
| Requests per run (mean / max) | 3.9 / 7 |
| Real provider requests (retries and judge included) | 39 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 0 (0) |
| Outcomes | none |
| Why loops stopped | submitted 10 |
| Turns / tool calls | 39 / 29 |
| Submissions rejected by the evidence check | 1 |
| Duplicate confirmations | 0 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 4 |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 4 |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 3 |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 3 |  |
| seeded-broken-caller-signature | seeded | 0 | 1 | 1/1 | - | 7 |  |
| seeded-missing-authorization | seeded | 0 | 1 | 1/1 | - | 4 |  |
| seeded-none-dereference | seeded | 0 | 1 | 1/1 | - | 2 |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 4 |  |
| seeded-path-traversal-attachments | seeded | 0 | 1 | 1/1 | - | 4 |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - | 4 |  |
