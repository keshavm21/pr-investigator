# Evaluation results

Each subdirectory is the unmodified output of one `pri eval run`. Don't edit or regenerate a published baseline; add a new run instead.

## Official baselines

### Phase 1 B0

- **Directory:** `20261004-205135-b0-gemini-3.5-flash-lite/`
- **Produced from:** commit `cc74214` (`d0186a0` before the repository history was rewritten on 2026-10-16), run on 2026-10-04 (UTC)
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

**Comparing against it.** Use the same model, cases, sample count and matching mode. The Phase 1 and Phase 2 runs cover these 10 cases; eval suite v2 (Phase 3) adds 20 more, so name the 10 cases explicitly with `--case` to compare. With 6 seeded issues, one finding moves recall by about 17 points, so small differences are noise.

## Phase 2: agentic reviewers A2 and A1

Same setup as B0: `gemini-3.5-flash-lite` on the free tier, the same 10 cases, medium effort, location-only matching (±3 lines) with no LLM judge. Budgets per D25: at most 4 suspects per PR and 6 turns per investigation for A2, 12 turns for A1, 3 tool calls per turn, about 4,000 characters per tool result. Every run ran at 10 requests per minute.

### Final comparison

These are the two runs on the final Phase 2 code, with 2 samples per case (20 reviews per reviewer):

| | B0 | A2 (planner + investigations) | A1 (one agent loop) |
|---|---|---|---|
| Precision | 100% (4 of 4) | 86% (12 of 14) | **100% (12 of 12)** |
| Recall | 67% (4 of 6) | **100% (12 of 12)** | **100% (12 of 12)** |
| False positives on clean PRs / decoys flagged | 0 / 0 | 0 / 0 | 0 / 0 |
| Findings with mechanically verified evidence | n/a | 100% | 100% |
| Requests per PR (mean / max) | 1 / 1 | 7.3 / 16 | 4.3 / 10 |
| Input tokens per PR (mean) | about 1.1K | about 26.8K | about 18.1K |
| Submissions rejected by the evidence check | n/a | 3 | 3 |

- **Directories:** `20261017-091837-a2-gemini-3.5-flash-lite/` and `20261017-092740-a1-gemini-3.5-flash-lite/`, with one trace per case and sample in `traces/` (`pri show` renders them).
- **Prompts:** A2 `planner.v1`, `investigator.v3`, `agent_loop.v2`; A1 `single_agent.v3`, `agent_loop.v2`.
- **Code:** commit `009d212` plus the submission fixes described below, which were uncommitted during the runs (so both summaries say "with uncommitted changes") and are committed together with this section.
- **Commands:** `PRI_LLM_REQUESTS_PER_MINUTE=10 uv run pri eval run --reviewer a2 --no-judge --samples 2 --max-requests 200`, and the same with `--reviewer a1 --max-requests 130`.

**What the agentic reviewers add.** Both find the two issues B0 misses because they need code outside the diff: the caller in `app/routes/orders.py` that a signature change breaks, and the None dereference whose cause is in `users.py`. Neither flagged a decoy or a clean PR, and every reported finding cites lines the tools actually showed, re-read from the analyzed commit.

**A2's two false positives** are the same finding in both samples: the new attachment endpoint reads the whole file into memory before sending it. That is a real but minor performance point that the case's ground truth doesn't label, so the rules count it as a false positive until a human adjudicates it.

**A1 against A2 (ablation for D5).** On this suite, the single agent loop matched A2's recall with higher precision, about 40% fewer requests and about a third fewer tokens. The suite favours it: each seeded case has one issue in one area, so the planner's strengths (separate, visible investigations for several suspects) can't show. Decision (D5, 2026-10-18): A1 is the default for `pri review`; A2 stays available, and the planner has to show its value on harder cases with several issues per PR ([decisions.md](../../docs/decisions.md)).

### How we got here: earlier Phase 2 runs

| Run | Directory | Prompts (investigator / single agent) | Precision | Recall | Requests (real) |
|---|---|---|---|---|---|
| A2 run 1 | `20261016-142109-a2-gemini-3.5-flash-lite/` | `investigator.v2` | 100% | 83% | 62 (73) |
| A2 run 2 | `20261017-072439-a2-gemini-3.5-flash-lite/` | `investigator.v3` | 80% | 67% | 70 (60) |
| A1 run 1 | `20261017-073103-a1-gemini-3.5-flash-lite/` | `single_agent.v3` | 100% | 100% | 39 (39) |

Every A2 miss in runs 1 and 2 was a formatting failure after a confirmed investigation, not a judgment failure. Between the two runs, the investigator confirmed all 6 seeded issues. The model left `start_line` out of its first submission almost every time (6 of 7 rejections in run 1, 13 of 17 in run 2), even after a prompt example (v2) and a schema that marks the keys required (v3; Gemini doesn't enforce function-call schemas). Because A2's investigators submit late (turn 4–6), a second mistake left no turn to fix it.

The fixes for the final runs, all generic rather than tuned to these cases:

- Line evidence with only one end of its range cites that single line, the narrowest reading. It is still checked against what the tools showed and re-read from the repository.
- Enum values (roles, statuses, categories) match case-insensitively; unknown values are still rejected.
- A "two turns left" note (`agent_loop.v2`), so a rejected submission can still be fixed.
- A clearer error for a cited range outside what was shown.

Rejected submissions in A2 fell from 17 in 10 reviews to 3 in 20.

### Caveats

- **The suite is small:** 10 cases, 6 seeded issues. One finding moves recall by about 8 points with 2 samples, and two single-sample A2 runs on almost identical code differed by 17 points.
- **Samples aren't fully independent across runs.** Responses are cached by request, so whenever a conversation is byte-identical to an earlier run's (same prompts, same tool results), sample 0 replays the recorded response. The final A1 sample 0 is identical to A1 run 1, and the final A2 sample 0 partly replays A2 run 2, including all of its planner calls. Sample 1 is new in both.
- **Location-only matching** counts a finding as correct if it lands within 3 lines of the seeded issue. All matched findings were also checked against the traces and describe the seeded issue.

