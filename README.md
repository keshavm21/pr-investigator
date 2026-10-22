# PR Investigator

An evidence-driven AI reviewer for GitHub pull requests. Instead of sending a diff to an LLM and posting whatever comes back, it forms hypotheses, investigates them with read-only tools across the repository, verifies what it finds, and lets a human approve what gets posted.

> **Status: Phases 1–6 built, Phase 7 (evaluation report) done.** Ingestion, read-only repository tools, baseline and agentic reviewers, an evidence ledger that mechanically checks every finding, a review UI, a gated publisher and a GitHub App service. Two exit checks need real GitHub accounts and are with the owner: a real test PR (Phase 5) and a deployed App (Phase 6). See the [implementation plan](docs/implementation-plan.md).

## Results

Held-out test split (15 cases, 3 samples each, `gemini-3.5-flash-lite` on the free tier; 95% bootstrap intervals over cases). Full table, caveats and per-component decisions: [evals/results/README.md](evals/results/README.md).

| | B0 (one call) | B1 (+context pack) | A1 (one agent loop) | A2 (planner + investigations, default) | A3 (A2 + skeptic) |
|---|---|---|---|---|---|
| Precision | 81% | **100%** | 75% | 81% | 83% |
| Recall | 44% | 44% | 23% | **64%** (45–81%) | 64% |
| Requests per true positive | 2.6 | 2.6 | 28.3 | 13.0 | 18.4 |

- Agentic investigation (A2) finds more issues that need code outside the diff; the one-call reviewer with a deterministic context pack (B1) is the precise, cheap option.
- The skeptic verifier and the tree-sitter tools did not earn their place on this model, so they are opt-in.
- Security: 0 canary leaks and 0 surviving injections through the output layer against a scripted worst-case model ([security.md](evals/results/security.md)).

**Limitations.** 15 test cases: the intervals are wide. Location-only scoring undercounts correct findings. A2 flags decoys more often than B0/B1. The free-tier model is small, so the verifier result may not transfer to stronger models. Python only. The real-model injection resistance is not measured. Rate limits are in-process and per peer address.

## Quick start

Requirements: git, [ripgrep](https://github.com/BurntSushi/ripgrep) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env      # add GEMINI_API_KEY; use an AI Studio key from a project without billing
uv run pri doctor         # checks the setup without calling any API

uv run pri review https://github.com/OWNER/REPO/pull/123     # review a public PR (agentic, A1)
uv run pri review https://github.com/OWNER/REPO/pull/123 --reviewer a2   # planner + investigations
uv run pri show RUN_ID    # what an agentic review did: its turns, evidence and findings
uv run pri eval validate  # check the evaluation cases (no LLM calls)
uv run pri eval run       # score the baseline on the evaluation cases
```

Reviewers: `a2` (the default) runs a planner, then one bounded investigation per suspect (up to about 26 requests per PR); `a1` runs one bounded agent loop over the whole PR (up to 12 requests); `b0` makes one call on the diff, the Phase 1 baseline. `b1` adds a context pack (callers, callees and tests of the changed Python functions) and mechanical leads to that one call: on the held-out test split it had 100% precision with no extra request, and `--context` gives the same pack to `a1`. A2 is the default because it had the highest recall on the held-out test split of eval suite v2, at about 40% more requests per PR than A1 (D5 in the [decision register](docs/decisions.md)). `pri eval run` still scores `b0` unless you pass `--reviewer`. The hosted version (`pri service`, `pri worker`, `compose.yaml`) runs as a GitHub App: opening a PR on an allowlisted repository queues an analysis, and reviewers with write access sign in with GitHub, approve and post; see [the runbook](docs/runbook.md). `pri serve` opens a local review UI (sign-in token printed at start): inspect findings and evidence, approve, edit or reject them with reasons, and preview the exact GitHub review. `pri publish RUN --approve F1` does the same from the command line. Both only preview until you confirm (`--post` / the confirm button), post one `COMMENT` review on the analyzed commit as the owner of `GITHUB_TOKEN`, and refuse if the PR has new commits. `a3` adds an independent skeptic that tries to refute each `a2` finding; on the held-out test split it removed no false positive (and cost about 38% more requests), so it stays opt-in. Every agentic review ranks its findings and marks the ones inside the noise budget (at most 7, severity medium or higher), and `review.md` shows the sanitized comments that would be posted; nothing is posted to GitHub (Phase 5). `--tools structural` gives `a1` and `a2` five more tools backed by a tree-sitter index of the repository's Python code (`find_symbol`, `find_references`, `get_symbol_context`, `find_tests`, `git_history`); the default, `--tools base`, keeps the Phase 1 tools until the Phase 3 ablations show whether the new ones help.

It runs on Gemini's free tier. `.env.example` sets `PRI_LLM_MODEL=gemini-3.5-flash-lite`, the model the evaluation baseline uses (the code default is `gemini-3.8-flash`). Free-tier inputs may be used for training, so PR Investigator refuses to send a private repository's code to it.

## Demo

A scripted five-minute tour is in [docs/demo.md](docs/demo.md).

Every agentic run writes a trace, and `pri show` turns it into a timeline. The committed eval traces need no API key:

```bash
uv run pri show evals/results/20261017-092740-a1-gemini-3.5-flash-lite/traces/seeded-broken-caller-signature-s1.jsonl
```

In that case, a PR adds a required `discount` parameter to `calculate_total` but leaves a caller unchanged. The agent searches for callers, reads the one in `app/routes/orders.py`, and has two reports rejected by the evidence check (a missing field, then a defect site outside the diff) before its report is accepted:

```text
  turn 1: search_code(pattern='calculate_total')
    -> [Observations you can cite: O5 = search literal 'calculate_total' (7 matches); ...]
  turn 2: read_file(path='app/routes/orders.py', start_line=1, end_line=45, ref='head')
  ...
  turn 6: report_findings(1 findings)
    submission rejected:
      - finding 1: defect site app/routes/orders.py:32-32 (head) isn't on lines the PR diff shows; ...
  turn 8: report_findings(1 findings)
    submission accepted

F1 [high] calculate_total signature change is not backward compatible and breaks existing callers ...
  app/pricing.py:12 · correctness · confidence high
  defect_site app/pricing.py:12-12 (head): calculate_total now requires 3 arguments including discount, ...
    | def calculate_total(lines: list[OrderLine], tax_rate: Decimal, discount: Decimal) -> Decimal:
  context app/routes/orders.py:32-32 (head): app/routes/orders.py calls calculate_total with 2 arguments, ...
    |     total = calculate_total(lines, TAX_RATE)
```

The quoted code lines are re-read from the repository by the system, never copied from model output.

## Development

```bash
uv run pytest             # offline; no test calls a live API
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Troubleshooting

### `ModuleNotFoundError: No module named 'pr_investigator'` on macOS

Inside an iCloud-synced folder (Desktop or Documents), iCloud Drive sets the macOS `hidden` flag on everything in dot-folders such as `.venv`. Python 3.13+ skips hidden `.pth` files, and the editable install depends on one, so the package can't be imported. The test suite stops with this explanation when it detects the problem.

- **Best fix:** keep the checkout outside synced folders (for example in `~/Developer`), then recreate the environment, since a virtualenv stores absolute paths: `rm -rf .venv && uv sync`.
- **If the checkout must stay in a synced folder:** keep the environment in `venv.nosync/` (no leading dot, so it isn't synced or hidden) and make `.venv` a symlink to it:

  ```bash
  rm -rf .venv
  UV_PROJECT_ENVIRONMENT=venv.nosync uv sync
  ln -s venv.nosync .venv
  ```

## Documentation

- [Architecture](docs/architecture.md)
- [Security and threat model](docs/security.md)
- [Testing and evaluation](docs/evaluation.md)
- [Implementation plan](docs/implementation-plan.md)
- [Decision register](docs/decisions.md)
- [Research notes](docs/research.md)

## License

MIT. See [LICENSE](LICENSE).
