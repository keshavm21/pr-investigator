# Evaluation results

Each subdirectory is the unmodified output of one `pri eval run`. Don't edit or regenerate a published baseline; add a new run instead.

## Official baselines

### Phase 1 B0

- **Directory:** `20261004-205135-b0-gemini-3.5-flash-lite/`
- **Produced from:** commit `d0186a0`, run on 2026-10-04 (UTC)
- **Model:** `gemini-3.5-flash-lite` (Gemini API free tier)
- **Prompt:** `baseline_review.v1`
- **Matching:** location only, ±3 lines, no LLM judge
- **Command:** `PRI_LLM_MODEL=gemini-3.5-flash-lite uv run pri eval run --samples 1 --no-judge --max-requests 15`

**Results.** All 10 cases ran (6 seeded, 2 decoy, 2 clean) with 1 sample each, in 10 provider requests with no errors or retries.

| Metric | Value |
|---|---|
| Precision | 100% (4 of 4 findings) |
| Recall | 67% (4 of 6 seeded issues) |
| F0.5 | 91% |
| False positives on clean PRs | 0 |
| Decoys flagged | 0 of 2 |

All four matched findings were checked by hand and describe the seeded issue: SQL injection, path traversal, missing authorization, off-by-one.

**Misses.** Both need context outside the diff, which is what Phase 2's repository-aware investigation is meant to supply:

- `seeded-broken-caller-signature`: the broken caller is in a file the diff doesn't touch.
- `seeded-none-dereference`: the cause is only visible in `users.py`.

**Comparing against it.** Use the same model, cases, sample count and matching mode. With 6 seeded issues, one finding moves recall by about 17 points, so small differences are noise.
