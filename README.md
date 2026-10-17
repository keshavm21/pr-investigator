# PR Investigator

An evidence-driven AI reviewer for GitHub pull requests. Instead of sending a diff to an LLM and posting whatever comes back, it forms hypotheses, investigates them with read-only tools across the repository, verifies what it finds, and lets a human approve what gets posted.

> **Status: Phase 2 of 7, built and evaluated.** Phase 1 laid the foundation: PR ingestion, read-only repository tools, a one-call baseline reviewer (B0) to measure later phases against, and the evaluation harness. Phase 2 adds agentic investigation: a planner lists suspected problems, a bounded agent investigates each one with the read-only tools, and every reported finding must cite lines the tools actually showed, checked mechanically against the analyzed commit. On the evaluation cases, both agentic reviewers find issues the baseline misses because they need code outside the diff, with every finding backed by verified evidence ([results](evals/results/README.md#phase-2-agentic-reviewers-a2-and-a1)). See the [implementation plan](docs/implementation-plan.md).

## Quick start

Requirements: git, [ripgrep](https://github.com/BurntSushi/ripgrep) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env      # add GEMINI_API_KEY; use an AI Studio key from a project without billing
uv run pri doctor         # checks the setup without calling any API

uv run pri review https://github.com/OWNER/REPO/pull/123     # review a public PR (baseline)
uv run pri review https://github.com/OWNER/REPO/pull/123 --reviewer a2   # agentic review
uv run pri show RUN_ID    # what an agentic review did: plan, investigations, evidence
uv run pri eval validate  # check the evaluation cases (no LLM calls)
uv run pri eval run       # score the baseline on the evaluation cases
```

Reviewers: `b0` makes one call on the diff; `a2` runs a planner, then one bounded investigation per suspect (up to about 26 requests per PR); `a1` runs one agent loop over the whole PR (up to 12 requests). `b0` stays the default until the decision on which agentic reviewer to keep (D5 in the [decision register](docs/decisions.md)).

It runs on Gemini's free tier. `.env.example` sets `PRI_LLM_MODEL=gemini-3.5-flash-lite`, the model the evaluation baseline uses (the code default is `gemini-3.8-flash`). Free-tier inputs may be used for training, so PR Investigator refuses to send a private repository's code to it.

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
