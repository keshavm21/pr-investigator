# Eval: b0 on gemini-3.5-flash-lite

- Started: 2026-10-04T20:50:25+00:00
- Provider: gemini; judge: location-only; prompt: baseline_review.v1
- Cases: 10; samples per case: 1; location tolerance: ±3 lines

| Metric | Value |
|---|---|
| Precision | 100% |
| Recall | 67% |
| F0.5 | 91% |
| False positives per clean run | 0.00 |
| Decoy trigger rate | 0% |
| Anchors on diff lines | 100% |
| Matches at the exact lines | 100% |
| Findings (TP / partial / FP) | 4 (4 / 0 / 0) |
| Runs (failed / served from cache) | 10 (0 / 0) |
| Tokens (in / out) | 11066 / 1122 |

TP counts include partial matches. Unmatched findings count as false positives until a human adjudicates them.

## Per case

| Case | Kind | Sample | Findings | Matched | Decoy hits | Error |
|---|---|---|---|---|---|---|
| clean-extract-rows-helper | clean | 0 | 0 | - | - |  |
| clean-health-endpoint | clean | 0 | 0 | - | - |  |
| decoy-admin-router-auth | decoy | 0 | 0 | - | - |  |
| decoy-allowlisted-sort | decoy | 0 | 0 | - | - |  |
| seeded-broken-caller-signature | seeded | 0 | 0 | 0/1 | - |  |
| seeded-missing-authorization | seeded | 0 | 1 | 1/1 | - |  |
| seeded-none-dereference | seeded | 0 | 0 | 0/1 | - |  |
| seeded-off-by-one-pagination | seeded | 0 | 1 | 1/1 | - |  |
| seeded-path-traversal-attachments | seeded | 0 | 1 | 1/1 | - |  |
| seeded-sql-injection-search | seeded | 0 | 1 | 1/1 | - |  |
