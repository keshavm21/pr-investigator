# Testing and Evaluation

> **Status: proposed, under review (2026-10-03).**

This document covers two questions:

- **Testing:** does the software do what it should? Tests are deterministic, free and run on every commit.
- **Evaluation:** how good are the reviews? Evals are statistical, consume API quota and are run deliberately.

## 1. Testing

**CI never calls live LLM or GitHub APIs.**

| Layer | What it covers | How |
|---|---|---|
| Unit | Diff parsing and the commentable-line map; path confinement; tool output formatting and truncation; chunking; symbol extraction; evidence grounding; anchoring; rendering and sanitizing; config loading (base branch only); budget logic | pytest. Hypothesis property tests for diff and anchor mapping. Golden files for chunks and symbols. |
| Integration | Postgres repositories and migrations; git workspace operations; the GitHub client; the flow from webhook to job to run to publish | A Postgres service container; fixture repositories generated in temporary directories; mocked HTTP (respx) |
| Agent orchestration | Loop mechanics, parallel tool calls, budget cut-offs, a missing verdict, `refusal` and `max_tokens` stop reasons, rejection of invalid evidence | A scripted fake LLM that returns predetermined tool calls |
| Record/replay | Prompt rendering and response parsing against real model output, at no cost | Recorded API responses keyed by a request hash. Re-recorded deliberately when prompts change. |
| Security | Injection fixtures (output assertions), path traversal, the sanitizer, webhook signatures, UI authorization | See [security.md](security.md) §6 |
| End-to-end (manual or nightly) | A real GitHub test repository and real APIs, posting to a sandbox PR | Manual dispatch only |

## 2. Evaluation

### 2.1 Metrics

Each reported finding is one of:

- **TP:** it matches a ground-truth issue.
- **FP:** it has no match, on a case whose ground truth is complete.
- **Unlabeled:** it needs human adjudication (§2.3).

| Metric | Definition | Why it matters |
|---|---|---|
| Precision | TP / (TP + FP) | The headline metric. False positives destroy trust. |
| Recall | Matched ground-truth issues / all ground-truth issues | Catches a reviewer that stays silent to stay precise |
| F0.5 | F-score weighted toward precision | A single summary number |
| FP per clean PR | False positives on PRs with no known issues | Noise in normal use |
| Decoy trigger rate | Share of decoys flagged | Directly tests "investigate before claiming" |
| Localization | Anchor within the ground-truth range (±3 lines) | Comments must land in the right place |
| Evidence validity | Share of findings whose evidence supports the claim (LLM judge, spot-checked by hand) | Tests the evidence model, not only the verdict |
| Verifier effect | FPs removed versus TPs removed | The verifier's real cost |
| Cost and latency | Cost per PR, cost per true positive, p50/p95 latency, tool calls per investigation | Feasibility |
| Reliability | Refusal rate, failed-run rate | Operational health |

**Online metrics** come from real usage:

- approval rate, edit rate and rejection reasons from the review UI
- **addressed rate**: whether the flagged code changed before merge

Addressed rate is the same signal behind Cursor's "resolution rate" and Martian's online benchmark ([research.md](research.md) §7).

### 2.2 Datasets

1. **Owned seeded suite (essential).** Built on small-to-medium real open-source Python services, kept in a dedicated eval repository. Keeping that repository private avoids future training contamination. Each case is a base commit plus a PR branch, in one of three kinds:
   - **Seeded:** one or more realistic injected issues across categories:
     - SQL or command injection, path traversal, SSRF
     - missing authorization or IDOR, unsafe deserialization
     - a caller broken by a signature change
     - None or edge-case handling, off-by-one errors
     - swallowed exceptions, resource leaks, races, wrong defaults
   - **Decoy:** code that looks dangerous but is safe for a reason visible only beyond the diff. Examples: input validated upstream, a query parameterized through a helper, unreachable code, framework protection.
   - **Clean:** ordinary refactors and features with no known issue.

   The target is about 10 cases in Phase 1 and 30–40 by Phase 7, roughly 60% seeded, 20% decoy and 20% clean. The cases are written after the models' training cutoff, so they can't be memorized.
2. **Historical real bugs (optional).** Python bugs from BugsInPy and Python CVE fixes from CVEfixes or MoreFixes. Each becomes a PR that reintroduces the bug (by reverting the fix on top of the fixed version), or uses the original bug-introducing commit. These bugs are real but may be memorized by models; results are reported separately.
3. **External benchmarks (optional).** For comparison with published tools, one of:
   - a Python subset of Qodo PR-Review-Bench (100 PRs, 580 injected issues, MIT licence)
   - Martian's Code Review Bench offline set (50 PRs with human-verified issues, open source)
   - SWR-Bench (1,000 PRs)

   Check each dataset's licence and format before use.

Example case manifest:

```yaml
id: sqli-search-endpoint-001
kind: seeded            # seeded | decoy | clean | historical | external
repo: eval-fixtures/inventory-service
base: 3f2c9a1
head: 9b7e044
language: python
expected:
  - id: E1
    category: security/sql-injection
    severity: high
    location: { path: app/search.py, lines: [42, 44] }
    description: >
      The `q` query parameter is interpolated into SQL with an f-string;
      no parameter binding or validation on any path from the route.
must_not_flag:
  - location: { path: app/reports.py, lines: [10, 18] }
    reason: Looks like string building, but values are bound by db.query() placeholders.
```

### 2.3 Matching findings to ground truth

1. **Candidate pairs:** a finding and a ground-truth issue in the same file with overlapping lines (±3).
2. **Judge:** an LLM decides whether the finding describes the same issue (match, partial or no), with a structured rationale.
3. **Assignment:** findings and issues are paired one-to-one. A partial match counts toward recall but is reported separately.
4. **Adjudication:** unmatched findings on seeded and clean cases count as FPs by default, but a sample goes to a human. Real bugs we didn't seed become "unlabeled TP" and are added to the ground truth.

The judge is validated against 50–100 hand-labelled pairs. Every result is reported with the judge's agreement score (Cohen's κ).

The judge runs on the configured free model. A judge from a different model family would reduce the risk of a model favouring its own outputs. That becomes an option once a second free adapter exists.

### 2.4 Configurations and ablations

| ID | Configuration | Question it answers |
|---|---|---|
| B0 | Single call, diff only | Baseline |
| B1 | Single call, diff plus the deterministic context pack | Value of non-agentic context |
| A1 | One agent loop with all tools, no planner | Value of the planner |
| A2 | Planner plus parallel investigators, no verifier | Value of agentic investigation over B1 |
| A3 | A2 plus verifier (the full pipeline) | Value of verification |
| A3−sem | A3 without semantic search and convention leads | Value of semantic retrieval |
| A3+LSP | A3 with precise references (S2) | Value of precise code intelligence |

Effort levels and, if D3 allows, cheaper models per stage are tested as further variants.

### 2.5 Statistics

- **Repetition.** Model outputs vary from run to run, so the baseline runs at least 3 times per case. On free quotas, agentic configurations may only afford 1–2 repetitions; reports state the count. Results are reported as means with bootstrap 95% confidence intervals over cases.
- **Paired comparisons.** Ablations run on the same cases and report per-case wins and losses, not only averages.
- **Honesty about size.** The suites are small, and the reports say so instead of over-claiming.

### 2.6 Running evals

- `pri eval run --suite core --config A3 --reps 3` writes per-run JSONL and a `summary.md` under `evals/results/`.
- During development, use a subset of 5–10 cases with 1 repetition.
- Responses are cached by a hash of the request, so re-scoring results or regenerating a report never spends quota again.
- Every eval run records the git SHA, configuration, provider, exact model ID, prompt versions, and request and token counts. A request cap aborts the run when reached.
- A client-side rate limiter keeps requests within the provider's per-minute quotas. Runs pause and resume on HTTP 429 errors rather than failing.
- Live evals consume free-tier quota, and would cost money on a paid provider. Run them on purpose, never automatically in CI and never for fork PRs.

### 2.7 Eval budget: free quotas

The budget is $0 (D21), so free-tier quotas are the constraint ([architecture.md](architecture.md) §7.4).

| Configuration | Requests per case | 10 cases × 3 repetitions |
|---|---|---|
| B0 / B1 baseline | 1, plus judge calls | About 30–60 requests; minutes of quota |
| A1–A3 agentic | Roughly 80–120 | About 2,400–3,600 requests; two to three days of a ~1,500-request daily quota per configuration |

Consequences:

- Phase 1 evaluates only the baseline, which fits easily.
- From Phase 2, agentic configurations run on a development subset (5–10 cases) with 1–2 repetitions. The full table in Phase 7 is spread over several days of quota.
- Recorded responses make every reported number reproducible without new calls.
- If a budget appears later, the same harness runs unchanged on a paid provider. At Claude Opus 5.5 high effort, the full ablation table would cost roughly $1,000–2,000.
