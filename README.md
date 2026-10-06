# PR Investigator

An evidence-driven AI reviewer for GitHub pull requests. Instead of sending a diff to an LLM and posting whatever comes back, it forms hypotheses, investigates them with read-only tools across the repository, verifies what it finds, and lets a human approve what gets posted.

> **Status: Phase 1 of 7.** What exists today is the foundation: PR ingestion, read-only repository tools, a one-call baseline reviewer (B0) to measure later phases against, and the evaluation harness. Agentic investigation arrives in Phase 2. See the [implementation plan](docs/implementation-plan.md).

## Quick start

Requirements: git, [ripgrep](https://github.com/BurntSushi/ripgrep) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env      # add GEMINI_API_KEY; use an AI Studio key from a project without billing
uv run pri doctor         # checks the setup without calling any API

uv run pri review https://github.com/OWNER/REPO/pull/123     # review a public PR
uv run pri eval validate  # check the evaluation cases (no LLM calls)
uv run pri eval run       # score the baseline on the evaluation cases
```

It runs on Gemini's free tier. `.env.example` sets `PRI_LLM_MODEL=gemini-3.5-flash-lite`, the model the evaluation baseline uses (the code default is `gemini-3.8-flash`). Free-tier inputs may be used for training, so PR Investigator refuses to send a private repository's code to it.

## Development

```bash
uv run pytest             # offline; no test calls a live API
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'pr_investigator'` on macOS.** If the repository is inside an iCloud-synced folder (Desktop or Documents), iCloud marks files in `.venv` as hidden, and Python 3.13+ ignores hidden `.pth` files, which breaks the editable install. Move the repository elsewhere, or keep the environment in `venv.nosync/` with `.venv` as a symlink to it (see "macOS and iCloud" in [CLAUDE.md](CLAUDE.md)).

## Documentation

- [Architecture](docs/architecture.md)
- [Security and threat model](docs/security.md)
- [Testing and evaluation](docs/evaluation.md)
- [Implementation plan](docs/implementation-plan.md)
- [Decision register](docs/decisions.md)
- [Research notes](docs/research.md)

## License

MIT. See [LICENSE](LICENSE).
