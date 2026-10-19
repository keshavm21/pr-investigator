# Eval: a1 on gemini-3.5-flash-lite

- Started: 2026-10-18T19:36:25+00:00
- Provider: gemini; judge: location-only; prompt: single_agent.v3, agent_loop.v2
- Cases: 19 (split: dev); samples per case: 1; location tolerance: ±3 lines
- Code: commit `b0479ecf49c1` with uncommitted changes
- Budget: 4 suspects, 6 turns per investigation, 12 turns for A1, 3 tool calls per turn, 4000 characters per tool result

| Metric | Value |
|---|---|
| Precision | 88% |
| Recall | 64% |
| F0.5 | 81% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 25% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 8 (7 / 0 / 1) |
| Runs (failed / served from cache) | 19 (0 / 10) |
| Tokens (in / out) | 355017 / 5625 |
| Review requests (from cache) | 84 (39) |
| Requests per run (mean / max) | 4.4 / 7 |
| Real provider requests (retries and judge included) | 45 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Investigations

| Metric | Value |
|---|---|
| Reported findings with verified evidence | 100% |
| Suspects investigated (dropped) | 0 (0) |
| Outcomes | none |
| Why loops stopped | submitted 19 |
| Turns / tool calls | 84 / 67 |
| Submissions rejected by the evidence check | 1 |
| Duplicate confirmations | 0 |
| Provider retries | 0 |

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |
|---|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - | 4 (cached) |  |
| clean-health-endpoint | clean | 0 | 0 | - | - | 4 (cached) |  |
| clean-inventory-shared-int-parsing | clean | 0 | 0 | - | - | 6 |  |
| clean-werkzeug-plain-int-docs | clean | 0 | 0 | - | - | 2 |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - | 3 (cached) |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - | 3 (cached) |  |
| decoy-inventory-require-role-only | decoy | 0 | 1 | - | 1 | 7 |  |
| decoy-werkzeug-debugger-resource-cache | decoy | 0 | 0 | - | - | 4 |  |
| seeded-broken-caller-signature | seeded | 0 | 1 | 1/1 | - | 7 (cached) |  |
| seeded-inventory-uncapped-limit | seeded | 0 | 0 | 0/1 | - | 4 |  |
| seeded-missing-authorization | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-none-dereference | seeded | 0 | 1 | 1/1 | - | 2 (cached) |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-path-traversal-attachments | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - | 4 (cached) |  |
| seeded-werkzeug-gen-salt-random | seeded | 0 | 1 | 1/1 | - | 4 |  |
| seeded-werkzeug-header-newlines | seeded | 0 | 0 | 0/1 | - | 5 |  |
| seeded-werkzeug-input-stream-fallback | seeded | 0 | 0 | 0/1 | - | 7 |  |
| seeded-werkzeug-redirect-escape | seeded | 0 | 0 | 0/1 | - | 6 |  |
