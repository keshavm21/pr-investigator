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

## Phase 3: suite v2 baselines (held-out test split)

Eval suite v2 (D26) adds 20 cases, including a Werkzeug 3.1.9 fixture and PRs with two issues each. These baselines use the 15 held-out **test** cases (13 seeded issues, 3 decoys, 2 clean), 1 sample each, with the same model and matching as before.

| | B0 | A1 |
|---|---|---|
| Precision | 86% (6 of 7) | 75% (3 of 4) |
| Recall | **46% (6 of 13)** | 23% (3 of 13) |
| False positives on clean PRs / decoys flagged | 1 / 0 | 0 / 0 |
| Findings with verified evidence | n/a | 100% |
| Requests per PR (mean / max) | 1 / 1 | 5.5 / 11 |
| Input tokens per PR (mean) | about 1K | about 26K |

- **Directories:** `20261018-112200-b0-gemini-3.5-flash-lite/` and `20261018-112312-a1-gemini-3.5-flash-lite/` (A1 with traces).
- **Code:** commit `757faed`. The A1 summary says "with uncommitted changes", but only the B0 results folder was uncommitted at the time: the check meant to ignore result folders didn't work under the hardened git settings, fixed in the next commit.
- **Commands:** `PRI_LLM_REQUESTS_PER_MINUTE=10 uv run pri eval run --split test --reviewer b0 --no-judge --samples 1 --max-requests 20`, and the same with `--reviewer a1 --max-requests 180`. A1 needed 9 retries after Gemini 503 errors (92 real requests).

**Per case.** Both found the SQL injection in the order cancellation, the unchecked user in the role change, and the timing-unsafe password comparison. B0 also found the safe_join traversal, the UUID regex and the ProxyFix IPv6 split; A1 found none of those three. Neither found: the cancellation IDOR, the unvalidated role, the ProxyFix spoofing, the Content-Range off-by-one, the removed debugger PIN check, the converter keyword that rule strings still use, or the anonymous-user crash in the price update.

**False positives.** B0 claimed a helper function was used before its definition in a clean refactor (Python resolves the name at call time). A1 noted that two converter docstrings still say `signed`; that is true but minor and not in the ground truth.

**Why A1 did worse.** In the traces, A1 read the right code (and often the tests) and then accepted the PR description's claim instead of checking it. Its summaries repeat the author: normpath "already collapses `..`", the first X-Forwarded-For entry "is the original client", the Content-Range change "correctly fixes" the off-by-one. Each description is deliberately plausible but wrong, as in real buggy PRs. B0 sees the same descriptions but has to judge the diff on its own; A1's reading of unchanged tests that don't cover the bug seems to reinforce the author's claim. This is a finding about the agent's judgment, not about missing context, and it matters for Phase 3: on these cases, the bottleneck is checking claims, not finding code.

**The suite does its job.** Unlike the 10 Phase 1 cases, where A1 scored 100%, these cases separate the configurations, leaving room for every Phase 3 component to show its value.

**Protocol note.** These failures were analyzed on the held-out split. Under D26, any change motivated by them (for example, telling the agent to verify the author's claims) must be developed against dev cases and then measured once on the test split, with the observation's origin stated.

## Claim-checking experiment (dev split)

The test baselines above showed A1 accepting misleading PR descriptions. Under D26, test failures are only analyzed, so the idea was developed on dev cases instead:

- **Four new dev cases.** Three PRs justify a broken change with a false claim: header newlines are "already rejected by WSGI servers", redirect escaping is "redundant after iri_to_uri", the page-size cap is "redundant because of LIMIT". One decoy makes a true claim (a route that only uses `require_role`, which does reject anonymous requests).
- **One prompt change** (`single_agent.v4`): the title and description are the author's account and can be wrong; treat each claim as a hypothesis to check against the code, judge the change as if an unconfirmed claim hadn't been made, and don't rely on unchanged tests.

| Dev split (19 cases, 11 issues) | A1 `single_agent.v3` | A1 `single_agent.v4` |
|---|---|---|
| Precision | 88% | 100% |
| Recall | **64% (7 of 11)** | 45% (5 of 11) |
| Decoys flagged | 1 of 4 (the true-claim decoy) | 0 of 4 |
| Misleading-claim cases found | 1 of 4 | 1 of 4 |

- **Directories:** `20261018-193625-a1-gemini-3.5-flash-lite-dev-v3/` and `20261018-194040-a1-gemini-3.5-flash-lite-dev-v4/` (dev runs; summaries say "with uncommitted changes" because the new dev cases and prompt weren't committed yet). The v3 run replayed the 10 Phase 1 cases from the cache; v4's are fresh, so part of the difference on those cases is run-to-run variation.
- **Result: no gain, so v4 was not run on the test split**, and the reviewers stay on v3. v4 removed the over-skeptical false positive but still accepted the false claims: its traces restate the author ("removes the newline check since WSGI servers already validate header values") even for a claim that can't be checked inside the repository. On `gemini-3.5-flash-lite`, an instruction alone doesn't make the agent check claims.

## Phase 3: A2 baseline on the test split

Same setup as the B0 and A1 test-split baselines (15 held-out cases, 13 issues, 1 sample, `planner.v1`, `investigator.v3`, `agent_loop.v2`).

| Test split | B0 | A1 | A2 (raw) | A2 (adjudicated) |
|---|---|---|---|---|
| Precision | 86% (6 of 7) | 75% (3 of 4) | 73% (8 of 11) | 91% (10 of 11) |
| Recall | 46% (6 of 13) | 23% (3 of 13) | **62% (8 of 13)** | **77% (10 of 13)** |
| Decoys flagged | 0 of 3 | 0 of 3 | 1 of 3 | 1 of 3 |
| Requests per PR (mean / max) | 1 / 1 | 5.5 / 11 | 7.6 / 18 | |
| Input tokens per PR (mean) | about 1K | about 26K | about 31K | |

- **Directory:** `20261018-194957-a2-gemini-3.5-flash-lite/`, with traces. 114 requests, no retries. The summary says "with uncommitted changes": the four new dev cases and the `v4` prompt files were uncommitted; the reviewer code matched commit `b0479ec`.
- **Adjudication** (by hand, from the findings and traces; the raw numbers follow the eval rules). Two unmatched findings describe expected issues correctly but sit where location-only matching can't credit them:
  - the cancellation IDOR is anchored on the `UPDATE` line, 8 lines below the expected lookup line (the tolerance is 3);
  - the converter rename is anchored on the deleted `signed` parameter (the base side), while the ground truth marks only the head side.

  The remaining false positive is real: on the ProxyFix logging decoy, a low-severity "logs an unvalidated header value" finding next to the must-not-flag line. B0's and A1's unmatched findings were checked the same way; none describes an expected issue.

**What A2 found that neither B0 nor A1 did:** the removed debugger PIN check, ProxyFix's spoofable client address, and the Content-Range off-by-one. The planner turns each PR into explicit suspects with a "what would confirm or refute it" field, and an investigation then checks that specific suspicion. On these cases, that structure got past the misleading descriptions that A1, given only an instruction to be skeptical, accepted.

**Missed by all three:** the anonymous-user crash in the price update. **Missed by A2 only:** the safe_join traversal (B0 found it) and the unchecked user in the role change.

**For D5.** D5 kept A2 available on condition that the planner earns its place on harder cases with several issues per PR. On this split it does: A2 has the highest recall, at about 40% more requests per PR than A1. Changing the default reviewer is a decision for D5.


## Structural tools on the dev split

Phase 3 step 2 adds five tools backed by a tree-sitter index (`--tools structural`: `find_symbol`, `find_references`, `get_symbol_context`, `find_tests`, `git_history`), with prompts `single_agent.v5` and `investigator.v5` (v3 plus a sentence naming the tools). Before any test-split ablation (step 5), each reviewer ran once on the 19 dev cases (11 issues) with each toolset. 1 sample each, `--no-judge`, the same model and matching as before.

| Dev split | A1 base (`v3`) | A1 structural (`v5`) | A2 base (`v3`) | A2 structural (`v5`) |
|---|---|---|---|---|
| Precision | 88% (7 of 8) | 90% (9 of 10) | 89% (8 of 9) | 73% (8 of 11); 82% adjudicated |
| Recall | 64% (7 of 11) | 82% (9 of 11) | 73% (8 of 11) | 73% (8 of 11); 82% adjudicated |
| Decoys flagged | 1 of 4 | 1 of 4 | 0 of 4 | 1 of 4 |
| Requests per PR (mean / max) | 4.4 / 7 | 5.5 / 11 | 5.7 / 11 | 5.7 / 10 |
| Input tokens per PR (mean) | about 19K | about 31K | about 20K | about 27K |
| Structural tool calls | n/a | 8 of 84 | n/a | 10 of 71 |

- **Directories:** `20261018-193625-a1-gemini-3.5-flash-lite-dev-v3/` (from the claim-checking experiment), `20261019-a1-structural-gemini-3.5-flash-lite-dev/`, `20261019-a2-gemini-3.5-flash-lite-dev/` and `20261019-a2-structural-gemini-3.5-flash-lite-dev/`, all with traces. Summaries say "with uncommitted changes": the step 2 code was uncommitted.
- **Cache replays.** The base runs replayed earlier responses for most Phase 1 cases (A1 v3: 10 cases; A2 base: 12 runs, from the Phase 2 eval), so those rows repeat earlier samples. The structural runs send different tool lists, so they are fresh. The first A1 structural run stopped on its last case: `find_tests` crashed sorting module-level uses next to test functions (fixed, with a test). The rerun replayed the 18 finished cases from the cache.
- **Adjudication** (A2 structural only; the other runs' unmatched findings describe no expected issue): the broken-caller finding describes the expected issue (the new required `discount` parameter, with the unchanged caller) but is anchored on the range check 6 lines below the signature (the tolerance is 3). The other two are real false positives: an unchecked `UPDATE` in the require-role decoy, and an email sent before the order commits.

**The agents rarely use the new tools.** Across both structural runs, `find_tests` was called 13 times, `find_references` 5 times, and `find_symbol`, `get_symbol_context` and `git_history` never; `read_file` and `search_code` made up most calls (A1: 35 and 33; A2: 24 and 25).

**The differences are run-to-run variation, not the tools.** In the five cases where the two A2 runs disagree (the three misleading-claim Werkzeug cases found only with structural tools; missing authorization and path traversal found only with base tools), the two runs made nearly the same tool calls; a structural tool appears in one of those investigations (`find_tests`, header newlines). The verdicts flip between confirmed and inconclusive on the same kind of evidence. A1's two extra finds each used one structural call, but the evidence they cite came from `read_file`. With 11 issues and 1 sample, a swing of two or three cases is within noise.

**Result.** No evidence on dev that the structural tools help, mostly because the agents don't use them. The tools stay opt-in (`--tools base` remains the default). For step 5: a one-sample ablation can't detect a two-case effect on this suite, so ablations need at least 2 samples per configuration. Structure that the agent doesn't have to ask for (the step 3 context pack) may be the better way to use the index.

## Phase 3 ablation on the test split

The step 5 ablation: five configurations on the 15 held-out test cases (13 issues, so 26 issue-samples), 2 samples each, `--no-judge`, `gemini-3.5-flash-lite`, 464 real requests in one session. Nothing was tuned on these cases: the context pack, leads and tools were built and checked on dev cases and unit tests only (D26).

| Test split, 2 samples | B0 | B1 | A1 base | A1 + context | A1 + structural tools |
|---|---|---|---|---|---|
| Precision | 79% (11 of 14) | **100% (12 of 12)** | 75% (6 of 8) | 86% (6 of 7) | 88% (7 of 8) |
| Recall | 42% (11 of 26) | **46% (12 of 26)** | 23% (6 of 26) | 23% (6 of 26) | 27% (7 of 26) |
| False positives per clean run | 0.50 | 0.00 | 0.00 | 0.00 | 0.00 |
| Decoys flagged | 0 of 6 | 0 of 6 | 1 of 6 | 0 of 6 | 0 of 6 |
| Requests per run (mean / max) | 1 / 1 | 1 / 1 | 5.7 / 11 | 4.7 / 9 | 5.7 / 12 |
| Input tokens per run (mean) | about 1K | about 1.3K | about 27K | about 22K | about 35K |
| Findings with verified evidence | n/a | n/a | 100% | 100% | 100% |

- **Directories:** `20261020-b0/`, `-b1/`, `-a1/`, `-a1-ctx/`, `-a1-structural/` (agentic runs have traces). **Commands:** `PRI_LLM_REQUESTS_PER_MINUTE=10 uv run pri eval run --split test --no-judge --samples 2 --reviewer {b0|b1|a1}`, plus `--context` or `--tools structural` for the last two.
- **Cache.** Sample 0 of B0 and of A1 base replayed the earlier single-sample baselines from the cache (15 runs each); sample 1 is new. So the B0 and A1 base columns are one old and one new sample, and their numbers differ from the single-sample baselines above. The other three columns are fresh.
- **One A1 base run failed** (Gemini 503, on the ProxyFix logging decoy) and counts as no findings.
- **Leads were not ablated separately.** A1 + context and B1 get the pack and the leads together.
- **Samples are few.** 26 issue-samples per column: a gap of 1 or 2 issues (4–8 points of recall) is within run-to-run noise. Only the differences in false positives and in cost are large enough to read.

**Findings.**
1. **The context pack and leads help B0 clearly, in precision.** B1 reports 12 correct findings and no wrong ones; B0 has 3 wrong ones (two on a clean PR that only moves a helper, one on the safe-join case). Recall is about the same (46% vs 42%): B1 found the debugger PIN removal in one sample, which B0 never did, and lost the safe-join traversal in both. It costs no extra request, only about 300 more input tokens.
2. **For A1, the pack makes the agent cheaper and slightly more precise, not better at finding issues.** Recall is unchanged (23%), the decoy it flagged without the pack is gone, and it needs 18% fewer requests (4.7 vs 5.7) and 19% fewer tokens, since its first questions (who calls this, is it tested) are already answered.
3. **The structural tools add little.** A1 with them found 7 issues instead of 6 (one more sample of the converter-rename case), within noise, for 30% more tokens. Across the 30 runs they made 18 structural calls (`find_tests` 16, `find_symbol` 2) against 110 `read_file` and `search_code` calls; `find_references`, `get_symbol_context` and `git_history` were never called. This repeats the dev result.
4. **Agentic recall is still below the single call's.** A1 (23%) trails B0 (42%) and B1 (46%) in every configuration. The traces from the baselines show why: A1 reads the right code and then accepts the PR description's claim (ProxyFix `X-Forwarded-For`, Content-Range, safe_join, UUID regex). More context doesn't fix that. A2's planner did better on the same cases (62% in its single-sample baseline, not re-run here).

**Decisions** (also in `docs/decisions.md` and the plan):

| Component | Decision | Why |
|---|---|---|
| Context pack and leads (B1; `--context` for A1) | **Keep** | The only addition with a clear gain: B1 precision 79% to 100% at no extra request; A1 about 18% cheaper with the decoy gone |
| Structural tools (`--tools structural`) | **Keep as opt-in, not default** | No measurable gain; three of five tools were never called. Their code stays small and tested; revisit pruning if a later phase gives the agent more reason to use them |
| Deterministic leads | **Keep, inside the pack** | Cheap and mechanical; their separate value wasn't measured |
| Semantic retrieval and embeddings (D8, D9), Semgrep spike (D12), `structural_search` | **Not built** | The misses are judgment failures, not missing context: the agents read the right code and accept the author's claim. Retrieval can't fix that |
| A2 with the context pack | **Not done** | Left out for time and quota; the next candidate when the planner is revisited |

**Reading for the project.** The agentic loop's value on this suite comes from its structure (A2's planner and verification of evidence), not from extra context. The cheapest strong configuration is B1: one request, 100% precision. Phase 4 (verification) targets exactly the weak spot: an independent check that does not share the author's framing.
