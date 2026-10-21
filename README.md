# PR Investigator

An evidence-driven AI reviewer for GitHub pull requests. Instead of sending a diff to an LLM and posting whatever comes back, it forms hypotheses, investigates them with read-only tools across the repository, verifies what it finds, and lets a human approve what gets posted.

> **Status: Phase 3 of 7 under way; Phases 1 and 2 complete.** Phase 1 laid the foundation: PR ingestion, read-only repository tools, a one-call baseline reviewer (B0) to measure later phases against, and the evaluation harness. Phase 2 adds agentic investigation: a planner lists suspected problems, a bounded agent investigates each one with the read-only tools, and every reported finding must cite lines the tools actually showed, checked mechanically against the analyzed commit. On the evaluation cases, both agentic reviewers find issues the baseline misses because they need code outside the diff, with every finding backed by verified evidence ([results](evals/results/README.md#phase-2-agentic-reviewers-a2-and-a1)). Phase 3 adds structural repository knowledge, measured on a harder suite with a held-out test split. See the [implementation plan](docs/implementation-plan.md).

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

Reviewers: `a2` (the default) runs a planner, then one bounded investigation per suspect (up to about 26 requests per PR); `a1` runs one bounded agent loop over the whole PR (up to 12 requests); `b0` makes one call on the diff, the Phase 1 baseline. `b1` adds a context pack (callers, callees and tests of the changed Python functions) and mechanical leads to that one call: on the held-out test split it had 100% precision with no extra request, and `--context` gives the same pack to `a1`. A2 is the default because it had the highest recall on the held-out test split of eval suite v2, at about 40% more requests per PR than A1 (D5 in the [decision register](docs/decisions.md)). `pri eval run` still scores `b0` unless you pass `--reviewer`. `pri serve` opens a local review UI (sign-in token printed at start): inspect findings and evidence, approve, edit or reject them with reasons, and preview the exact GitHub review. `pri publish RUN --approve F1` does the same from the command line. Both only preview until you confirm (`--post` / the confirm button), post one `COMMENT` review on the analyzed commit as the owner of `GITHUB_TOKEN`, and refuse if the PR has new commits. `a3` adds an independent skeptic that tries to refute each `a2` finding; on the held-out test split it removed no false positive (and cost about 38% more requests), so it stays opt-in. Every agentic review ranks its findings and marks the ones inside the noise budget (at most 7, severity medium or higher), and `review.md` shows the sanitized comments that would be posted; nothing is posted to GitHub (Phase 5). `--tools structural` gives `a1` and `a2` five more tools backed by a tree-sitter index of the repository's Python code (`find_symbol`, `find_references`, `get_symbol_context`, `find_tests`, `git_history`); the default, `--tools base`, keeps the Phase 1 tools until the Phase 3 ablations show whether the new ones help.

It runs on Gemini's free tier. `.env.example` sets `PRI_LLM_MODEL=gemini-3.5-flash-lite`, the model the evaluation baseline uses (the code default is `gemini-3.8-flash`). Free-tier inputs may be used for training, so PR Investigator refuses to send a private repository's code to it.

## Demo

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
