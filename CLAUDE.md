# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PR Investigator is an evidence-driven AI reviewer for GitHub pull requests. It forms hypotheses about a PR, investigates them with read-only tools over the repository, verifies what it finds, and shows the findings to a human, who decides what gets posted as a GitHub review.

**Status:** Phase 1 is implemented: PR ingestion, read-only repository tools, the one-call baseline reviewer (B0) and the eval harness. Phase 2 (agentic investigation) hasn't started. Work goes phase by phase, following `docs/implementation-plan.md`. Don't start a phase until the user approves it.

## Where things are documented

- `docs/architecture.md`: the system design and the source of truth for components, the pipeline, tools, retrieval, the data model and the stack.
- `docs/security.md`: the threat model. Read it before touching agent tools, prompts, GitHub auth, comment rendering or publishing.
- `docs/evaluation.md`: testing layers, eval datasets, metrics and ablations.
- `docs/implementation-plan.md`: phases, scope and exit criteria.
- `docs/decisions.md`: the decision register. Treat Proposed and Open items as unsettled until the user accepts them, and update the register when a decision changes.
- `docs/research.md`: prior art and sources.

## Architectural invariants

Don't break these without an explicit decision from the user.

1. **Evidence before claims.** A finding is reportable only if every evidence item refers to a line range that a tool actually returned during that investigation (the observation ledger), checked deterministically against the analyzed commit. Snippets shown to humans are extracted by the system, never copied from model output.
2. **All repository and PR content is untrusted.** This includes code comments, docs, PR title and body, commit messages, file names, and agent instruction files in the target repository (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`). It is data, never instructions.
3. **The analysis agent is read-only and capability-poor.** It uses typed tools confined to the run's worktree. It has no shell, network, write access or credentials, and it never executes repository code. Any execution happens only in an isolated sandbox (stretch goal).
4. **Publishing is separate and gated.** Only the publisher component posts to GitHub, and only findings a human approved. It uses a fixed template, sanitized output, and `event: COMMENT`.
5. **Behaviour-changing configuration comes from the base branch,** never from the PR head. This covers `.pr-investigator.yml`, ignore paths and generated-file exclusions.
6. **Precision over recall.** Volume is capped. "Inconclusive" beats a speculative finding.
7. **Every run is reproducible and traced.** Record model IDs, effort, prompt versions and config. Persist every LLM call and tool call.
8. **Agentic and retrieval components must earn their place** through eval ablations.

## Working conventions

- Python 3.13 is accepted. The rest of the stack in `docs/architecture.md` §12 (Postgres + pgvector, tree-sitter, ripgrep and so on) is proposed until accepted in `docs/decisions.md`.
- **No paid API calls.** Development and evaluation use free model tiers only (Gemini free tier by default). Paid-provider adapters stay disabled unless the user explicitly says a budget exists.
- **The pipeline is provider-neutral.** Review code talks only to the internal `LLMClient` interface (`docs/architecture.md` §7.1), never to a vendor SDK directly. Provider and model are configuration. Don't add routing layers, provider fallbacks or infrastructure for multi-provider support. Pipeline logic must not depend on provider extras like prompt caching or forced tool choice.
- Agent conversations are append-only: never edit earlier turns.
- Prompts are versioned files. Changing a prompt changes the prompt version recorded with each run.
- Tests and CI must never call live LLM or GitHub APIs. Use the `fake` and `replay` LLM adapters and mocked HTTP.
- Live reviews and evals consume free-tier quota (roughly 80–120 requests per PR once the agentic pipeline exists). Ask before running them, and never send private-repository content to a free tier that may train on it.

## Commands

```bash
uv sync                                    # install deps; uv manages Python 3.13
uv run pytest                              # all tests, offline (the live test is skipped)
uv run pytest tests/unit/test_diff.py::test_commentable_lines_and_same_hunk_rule   # one test
uv run ruff check . && uv run ruff format --check .
uv run mypy                                # strict, src/ only
uv run pri doctor                          # check setup; calls no API
uv run pri review https://github.com/OWNER/REPO/pull/N        # public PRs only on the free tier
uv run pri review PATH --base main --head feature --data-class public
uv run pri eval validate                   # check every case end to end; no LLM calls
uv run pri eval run --case ID --samples 1  # uses free-tier quota; responses are cached
PRI_LIVE_TESTS=1 uv run pytest tests/live -m live            # one real Gemini call
```

## Code notes

- Code layout: `docs/architecture.md` §13. Run data, git mirrors and the LLM response cache live in `.pri/` (gitignored).
- All LLM calls go through `llm.factory.build_client()`, which returns an `LLMClient`. Only `llm/gemini.py` imports the Gemini SDK. Every `LLMRequest` carries a `data_class`, and `LLMClient` refuses private content for providers that may train on inputs.
- Eval cases (`evals/cases/*.yaml`) are edits to a fixture in `evals/fixtures/`. Ground-truth locations are text matches in the head file. Run `pri eval validate` after changing a case or a fixture.
- An autouse fixture (`tests/conftest.py`) strips API keys, selects the fake provider and runs each test in an empty directory. Keep it that way, so tests can't spend quota even when `.env` holds a real key.
- Tests import the installed (editable) package, never `src/` via `sys.path`. `tests/integration/test_packaging.py` checks the install and the `pri` entry point from a fresh interpreter.

## macOS and iCloud

Keep the checkout outside iCloud-synced folders such as Desktop and Documents. It lives in `~/Developer`, which isn't synced.

Inside a synced folder, iCloud Drive sets the macOS `hidden` flag on everything in dot-folders, and Python 3.13+ skips hidden `.pth` files. That breaks the editable install in `.venv`: `ModuleNotFoundError: No module named 'pr_investigator'`. If a checkout must live in a synced folder, keep the environment in `venv.nosync/` (no leading dot, not synced) with `.venv` as a symlink to it. `tests/conftest.py` stops the test run with this explanation if the problem appears.

A virtualenv stores absolute paths, so after moving the checkout, recreate it with `rm -rf .venv && uv sync`.
