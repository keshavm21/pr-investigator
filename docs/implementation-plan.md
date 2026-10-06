# Implementation Plan

> **Status (2026-10-06):** Phase 1 is complete: merged, B0 baseline committed, CI green. Phase 2's plan below is under review; implementation hasn't started. No phase starts until it is approved.

The order follows risk. The most uncertain question is whether agentic investigation produces precise, evidence-backed findings at acceptable cost, so it gets built and measured first. GitHub App plumbing and the UI are well-understood engineering and come later.

From Phase 1 onward, every phase ends with:

- green CI
- updated docs
- recorded eval results
- a short demo

## Decisions for Phase 1

From [decisions.md](decisions.md):

- **Accepted:** D1 (Python), D2 (configurable provider and model behind an internal LLM interface), D3 (free model tiers only, no paid routing; Gemini through its official SDK), D21 ($0 budget), D24 (our own thin LLM interface).
- **Decided:** D23, the project licence: MIT.

**Zero-spend rule for Phase 1:** no paid API calls. Tests use the fake and replay adapters. Live runs use a free-tier key from a project without billing, and paid adapters are disabled by configuration.

## Phase overview

| Phase | Theme | Essential? | Builds on |
|---|---|---|---|
| 1 | Foundations, PR ingestion, read-only tools, baseline reviewer, eval harness | Essential | — |
| 2 | Agentic investigation with the evidence ledger | Essential | 1 |
| 3 | Repository intelligence and retrieval | Essential (some parts optional) | 2 |
| 4 | Verification, aggregation and rendering | Essential | 3 |
| 5 | Human review UI and GitHub publishing | Essential | 4 |
| 6 | GitHub App service, automation, hardening and deployment | Essential (some parts optional) | 5 |
| 7 | Evaluation report and portfolio polish | Core essential, extensions optional | 6 |
| S1–S10 | Stretch goals | Optional | varies |

Phases 1–5 make up the **minimum credible version**: a CLI- and UI-driven reviewer that investigates, verifies and posts approved findings through a personal token. Phase 6 makes it a real GitHub App. Phase 7 produces the results that justify the design.

---

## Phase 1: Foundations and a baseline reviewer

**Goal:** an end-to-end skeleton. Review a PR (from GitHub or a local repository) with a deliberately simple baseline, and measure it.

**Changes made during implementation:**

- **Postgres moved to Phase 3,** where the code index first needs it. Phase 1 stores runs as JSON files under `.pri/`.
- **GitHub calls use plain httpx.** Phase 1 needs a single REST endpoint. githubkit can come with GitHub App auth.
- **Modules are flat** (`diff.py`, `workspace.py`, ...) until they grow.

**Essential**

- **Scaffolding.**
  - uv project, ruff, mypy (strict), pytest
  - GitHub Actions CI
  - settings
- **Domain models:** `ReviewTarget`, `Finding`, `Anchor` and the diff types. Hypotheses and verdicts arrive with Phase 2.
- **Review targets.**
  - public GitHub PRs, fetched through the REST API (httpx), with an optional `GITHUB_TOKEN`
  - local repositories (path plus base and head refs), which evals use so they never need the GitHub API
- **Workspace.** Hardened git fetch, a worktree at head, the merge-base diff, a hunk parser and the commentable-line map.
- **Read-only tools, version 1:** `get_diff`, `read_file`, `search_code` (ripgrep) and `list_directory`.
  - path confinement
  - output bounds
  - tests for traversal and symlink escape
- **Provider-neutral LLM layer** ([architecture.md](architecture.md) §7.1):
  - the `LLMClient` interface and normalized request and response types
  - validated structured output, with one repair retry when the provider can't enforce a schema
  - normalized stop reasons, including refusals
  - usage accounting and a client-side rate limiter
  - adapters: `fake` (scripted), `replay` (recorded responses and local cache), `gemini` (free tier)
  - the `PRI_ALLOW_PAID_PROVIDERS=false` guard
  - versioned prompt files
- **Baseline B0.** A single-call, diff-only review that returns structured findings.
- **Eval harness, version 0.**
  - the case manifest format and a runner for local targets
  - a matcher (location plus LLM judge), metrics and a markdown report
  - about 10 owned cases: mostly seeded, at least 2 decoys and 2 clean
- **CLI:** `pri review <pr-url | --local ...>` and `pri eval run`.

**Exit criteria**

- `pri review` prints baseline findings for a real PR.
- An eval report for B0 on the mini-suite is committed, produced on the free tier.
- CI is green and makes no live API calls.
- No money was spent on API calls.

**Main risks:** diff and line-mapping edge cases (renames, deletions, files without a trailing newline), getting the eval case format right early, and free-tier changes (limits or model availability shifting without notice; the provider is configuration, so switching is cheap).

---

## Phase 2: Agentic investigation with the evidence ledger

**Goal:** add repository-aware investigation with mechanically verified evidence, and measure it fairly against B0.

**Decisions:**

- **D4:** our own bounded loop, no agent framework.
- **D5:** the planner turns the PR into suspects, and each suspect gets an investigation. A1 is kept for comparison.
- **D6:** typed read-only tools.
- **D25:** at most 4 suspects per PR and 6 turns per suspect.

**Held constant for a fair comparison with B0:** the model `gemini-3.5-flash-lite`, medium effort on every call, the same 10 cases, 1 sample, and location-only matching (±3 lines).

**Essential**

- **Planner.** One call with structured output. From the PR metadata and the diff, it lists up to 4 suspects, each anchored on diff lines and stating the evidence that would confirm or refute it.
- **Agent loop,** shared by A1 and A2.
  - The conversation is append-only.
  - Each turn runs the requested read-only tools and returns their results: at most 3 tools per turn, each result capped at about 4,000 characters.
  - The loop stops on an accepted submission, the turn budget, a refusal, `max_tokens`, or an error.
  - Investigations run one at a time.
- **Evidence ledger.** Every tool result records exactly the line ranges it showed, under ids (O1, O2, ...). The diff hunks in the first message are recorded the same way.
- **Mechanical evidence check.**
  - A submission's evidence must cite existing observation ids, in the same file, with lines inside what was actually shown.
  - The defect site must be on lines the PR diff shows.
  - Snippets are re-extracted from the repository.
  - A failing submission gets the errors back while turns remain. Otherwise the investigation ends inconclusive.
- **Reviewers.**
  - **A2:** the planner, then one investigation per suspect, ending with the terminal tool `submit_verdict`.
  - **A1:** one loop over the whole PR with a 12-turn budget, ending with the terminal tool `report_findings`.

  Only confirmed, verified findings are reported.
- **Gemini adapter.**
  - tool declarations
  - conversion of function calls and function responses
  - verbatim replay of the model's earlier turns, which Gemini requires for thought signatures
  - automatic function calling disabled
- **Traces.** One JSONL trace per run or eval case, and `pri show` to read it.
- **Eval.** A `--reviewer b0|a1|a2` option, and summaries that record the git commit, the real request count and requests per case. A1 and A2 runs are compared with B0.
- **Folded in:** the three deferred Phase 1 items: commit and request count in summaries, the automatic-function-calling warning, and the original rate-limit message.

**Not in Phase 2:**

- risk-signal leads and the context pack (Phase 3)
- the independent verifier (Phase 4)
- parallel investigations
- prompt caching
- a database, UI or GitHub App work

The earlier plan listed regex risk signals as planner input; they move to Phase 3 with the other leads.

**Live runs.** Each needs your go-ahead, and all run at 10 requests/minute:

- one tool-calling check (at most 3 requests)
- one A2 smoke review on the Phase 1 smoke-test PR (at most 26 requests)
- one A2 eval run (at most 260 requests)
- one A1 eval run (at most 120 requests)

A2 and A1 can run on different days to stay inside 500 requests/day.

**Exit criteria**

- Every reported A1 or A2 finding has at least one mechanically verified evidence item. This is enforced in code and tested.
- Offline tests cover the loop, the evidence check and the adapter, including scripted end-to-end runs on real eval cases.
- The A1 and A2 results, and a comparison with B0, are committed.
- Requests and tokens per PR are measured.

**Main risks**

- **Free-tier limits.** Budgets, caps and the cache keep a run under the daily quota.
- **A small free model may call tools poorly or cite evidence wrongly.** The evidence check rejects bad citations, and results are reported as measured.
- **Gemini tool-calling details.** These get verified early with the live check.

---

## Phase 3: Repository intelligence and retrieval

**Goal:** give the planner and investigators structural and semantic knowledge of the repository, and measure whether it helps.

**Essential**

- **Indexing.**
  - tree-sitter: deep for Python, generic tags queries for other languages
  - content-addressed by blob SHA, with per-commit snapshots
  - symbols, imports and call sites
- **Tools:** `find_symbol`, `find_references` (heuristic, with results labelled as such), `get_symbol_context`, `find_tests` and `git_history`.
- **Context pack and repository profile** (deterministic): enclosing symbols, callers and callees, related tests and config.
- **Semantic retrieval.**
  - AST-aware chunking
  - embeddings stored in pgvector
  - hybrid search (full-text plus vector, merged with RRF)
  - the `semantic_search` tool
- **Leads.**
  - Semgrep/Opengrep alerts new in head (decide D12 after a short spike)
  - tree-sitter-based risk signals
  - convention-deviation leads
  - hidden-Unicode and agent-instruction-file signals
- **Eval.** B1 becomes available. Run A2 with and without the structural tools and semantic retrieval, and compare embedding neighbours with structural neighbours for convention leads.

**Optional in this phase:** the `structural_search` tool (ast-grep), and a small hand-labelled retrieval set for recall@k.

**Exit criteria**

- Re-indexing a PR head only processes changed blobs.
- Ablation numbers are committed.
- Semantic retrieval is kept, narrowed or dropped based on the data.

**Also in this phase (moved from Phase 1):** Docker Compose with Postgres (pgvector image) and Alembic migrations. Runs, findings and traces move from JSON files into the database.

**Main risks:** imprecise name-based references (labelled; S2 is the fallback), and the cost of the first index on large repositories (exclusions and caps).

---

## Phase 4: Verification, aggregation and rendering

**Goal:** turn candidate verdicts into a short, ranked, safe-to-post list.

**Essential**

- **Skeptic verifier.** A fresh context, a small read-only tool budget, instructions to refute, and grounded refuting evidence.
- **Analyzer corroboration.**
- **Aggregation.**
  - deduplication
  - ranking by severity × verification strength
  - the noise budget (D17)
  - confidence thresholds calibrated on the eval set
- **Anchor resolution.** Multi-line comments, the LEFT side for deleted lines, and the fallback to the review summary.
- **Comment rendering.**
  - the fixed template
  - the markdown sanitizer
  - the outgoing secret scanner
  - suggestion validation (replaces only the anchored lines; the patched file still parses)
- **Injection handling.** The injection-as-finding category and the injection fixture suite ([security.md](security.md) §6).
- **Eval.** A2 versus A3, including true positives removed by the verifier.

**Exit criteria**

- The full-pipeline (A3) results are committed.
- The sanitizer and injection tests are green.

**Main risks:** the verifier removing true positives (measured, and its prompt tuned), and dedupe merging distinct issues.

---

## Phase 5: Human review UI and GitHub publishing

**Goal:** a human can inspect, edit, approve or reject findings and post them to the PR.

**Essential**

- **Review UI** (D13; FastAPI with Jinja/HTMX recommended):
  - a list of runs
  - finding cards with verification badges
  - an evidence viewer with permalinks
  - a trace viewer
  - refuted and unposted findings
  - approve, edit and reject (with reason codes)
  - a publish preview showing the exact payload
- **Publisher.**
  - one review with `event: COMMENT` and the analyzed `commit_id`
  - multi-line anchors and suggestions
  - an idempotency marker
  - a 422 fallback that moves the finding into the summary body
  - stale-head detection
- **Review decisions** stored as labelled feedback.
- **CLI publish path:** `pri publish <run> --approve ...`.
- **Simple auth:** a single-user token for local or demo use.

**Exit criteria:** approving findings in the UI produces a review on a test PR with correct line anchors, and rejections are recorded with reasons.

**Main risks:** anchoring mismatches against GitHub's diff (covered by property tests and a real test PR).

---

## Phase 6: GitHub App service, automation, hardening and deployment

**Goal:** opening a PR on an installed repository triggers analysis automatically, with production-grade separation of credentials.

**Essential**

- **GitHub App.**
  - permissions as in [architecture.md](architecture.md) §8
  - JWT signing, then installation tokens down-scoped per job
  - installation lifecycle events
- **Webhook endpoint.** Signature verification, delivery dedupe and a fast acknowledgement.
- **Queue and worker** (procrastinate).
  - supersede the old run on each new push
  - admission policy: allowlist, size limits, drafts
  - per-installation budgets
- **Repository config** (`.pr-investigator.yml`), read from the base branch only.
- **Trust-zone separation** ([security.md](security.md) §4).
  - the fetcher and publisher run in the control plane
  - the worker has no GitHub credentials
  - separate DB roles
- **Reviewer login.** The GitHub OAuth flow with a repository permission check, plus CSRF protection, secure sessions and a CSP.
- **Deployment.**
  - a Dockerfile and a Compose setup for a VM
  - Caddy for TLS
  - secrets handling
  - a runbook
  - smee.io for local webhook development

**Optional in this phase:** a Check Run that links to the review UI, a `/investigate` PR comment command, and an egress allowlist proxy for the worker.

**Exit criteria:** on a test repository, opening a PR runs an analysis automatically, and a logged-in reviewer can approve and publish the findings. Security tests for webhooks and UI authorization are green.

**Main risks:** operational complexity (keep it to one VM and one database), and webhook edge cases (redeliveries, out-of-order events).

---

## Phase 7: Evaluation report and portfolio polish

**Goal:** credible, reproducible results and a project that explains itself.

**Essential**

- Grow the owned suite to 30–40 cases (more categories, decoys and clean PRs), with 3 repetitions and confidence intervals.
- A full ablation table (B0, B1, A1, A2, A3, A3−sem) with cost per true positive and latency.
- Security eval results: injection suppression rate and canary leakage, which must be zero.
- A README with an architecture diagram, results, limitations and a demo recording. Docs refreshed to match what was built.

**Optional:** historical real-bug cases (BugsInPy, CVEfixes Python subset), and an external benchmark subset (Qodo PR-Review-Bench, Martian Code Review Bench offline set, or SWR-Bench) to compare with published tools.

**Exit criteria:** results are committed under `evals/results/` and summarized in the README.

---

## Stretch goals (optional, in rough order of value)

| ID | Stretch | Notes |
|---|---|---|
| S1 | Execution-based verification | Generate a test that demonstrates the issue and run it in a sandbox against head and base. Sandbox choice is D19. Opt-in only, never automatic on fork PRs. |
| S2 | Precise references through language servers | multilspy with pyright, and typescript-language-server. Only for language servers that don't execute project code; others run in the sandbox only. Eval A3+LSP. |
| S3 | Deep support for a second language | TypeScript/JavaScript recommended (D18) |
| S4 | Feedback memory and regression memory | Retrieve this repository's past review decisions as precedents for the verifier and ranking (always visible, never silent suppression), and past fix commits as hypotheses |
| S5 | Repository threat model | Drafted once per repository by an LLM, edited by a human, then used as planner context |
| S6 | Validated fix suggestions | The patch applies, parses and passes tests in the sandbox |
| S7 | Online "addressed rate" metric | Did authors change the flagged lines before merge? |
| S8 | OpenTelemetry export | GenAI semantic conventions, exported to Langfuse or Phoenix |
| S9 | Planner self-consistency | Several planner passes with shuffled diff order, merged. Measure the gain against the extra cost. |
| S10 | MCP server for the repository-intelligence tools | Use the same tools interactively from Claude Code for debugging and demos |
