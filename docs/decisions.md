# Decision Register

> **Status values:**
> - **Proposed:** recommended, waiting for sign-off.
> - **Open:** needs input; the recommendation, if any, is shown.
> - **Accepted:** agreed.
> - **Deferred:** decided later, at the phase noted.
>
> When a decision changes, update its row and the affected docs.
>
> Last updated: 2026-10-06. **There is no budget for paid API calls:** development and evaluation use free tiers only.

## Accepted

| ID | Decision | Notes |
|---|---|---|
| D1 | **Implementation language: Python 3.13** | TypeScript was the alternative: better GitHub App tooling, weaker retrieval and eval ecosystem. |
| D2 | **The LLM provider and model are configuration, not code.** The pipeline talks only to a thin internal LLM interface ([architecture.md](architecture.md) §7.1). Nothing hard-codes a vendor or model. | Switching to Claude, OpenAI or another provider means adding an adapter. The review pipeline doesn't change. |
| D3 | **Free model tiers for development and evaluation, with no paid model routing.** Phase 1 uses **the Gemini API free tier** through the official `google-genai` SDK. **The evaluation model is `gemini-3.5-flash-lite`** (stable, free tier). The official Phase 1 B0 baseline used it, and Phase 2 must use it too so the comparison stays fair; `gemini-3.8-flash` returned 503 "high demand" and 429 errors on the free tier. The code default is still `gemini-3.8-flash`, so set `PRI_LLM_MODEL=gemini-3.5-flash-lite`. See §9 of [research.md](research.md). | Groq, OpenRouter's free models, GitHub Models and local Ollama were considered. Paid providers stay disabled by configuration until a budget exists. |
| D21 | **Evaluation budget is $0.** Evals are sized to free-tier quotas, not dollars. | Response caching, request caps, a small suite and few repetitions ([evaluation.md](evaluation.md) §2.7). Revisit if a budget appears. |
| D23 | **Project licence: MIT** (`LICENSE`, declared in `pyproject.toml`) | Apache-2.0 was the alternative: it adds an explicit patent grant. MIT is simpler. |
| D4 | **Our own small, bounded agent loop**, written against the internal `LLMClient`. No LangGraph, LangChain or other agent framework. | The investigation workflow is small enough to implement clearly as a bounded stateful loop, and the agentic behaviour itself, not framework usage, is the focus of the project. Revisit only if the workflow becomes complex enough to justify a framework. |
| D5 | **A planner turns the PR into suspected problems, then one bounded investigation runs per suspect (reviewer A2).** The single-agent version (A1) stays available for comparison. An independent verifier follows in Phase 4. | A single agent loop is simpler and cheaper. The eval compares the two on the same cases and model. |
| D6 | **Typed, read-only tools with no shell.** | A shell is more flexible but weaker for security and auditability, and it can't support the evidence ledger. |
| D24 | **Our own small LLM interface**: `LLMClient`, normalized request and response types, per-adapter capability flags, plain-module adapters. No LiteLLM. | LiteLLM and PydanticAI add more than they save for one or two providers, and PydanticAI would also take over orchestration (D4). |
| D25 | **Investigation budget: at most 4 suspects per PR and 6 model turns per suspect.** | Sized to the free tier of `gemini-3.5-flash-lite`, which AI Studio shows for this project as 15 requests/min, 250K tokens/min and 500 requests/day (checked 2026-10-06). The worst case is about 26 requests per PR, so one 10-case eval run stays well under the daily limit. |

## Architecture

| ID | Decision | Status | Recommendation | Alternatives and tradeoffs |
|---|---|---|---|---|
| D7 | Code intelligence | Proposed | **tree-sitter with heuristic references now; language servers later (S2)** | SCIP indexers (precise, but heavy offline indexing). Language servers that run build scripts are allowed only inside the sandbox. |
| D8 | Retrieval strategy | Proposed | **Lexical, structural and semantic retrieval (hybrid, merged with RRF).** Semantic retrieval only where names are unknown, plus convention-deviation leads, and it must justify itself in ablations. | Grep and structural only (simpler; recent research supports it for agents). |
| D9 | Embedding model | Open (decide in Phase 3) | **A local open-weight embedding model** (free, and code stays on your machine) | A free embedding API, such as Gemini's free tier, is less setup but has quotas and may use data for training. Voyage's code model is paid. |
| D10 | Datastore | Proposed | **Postgres with pgvector, as the single store**, from Phase 3. Phase 1 stores runs as JSON files. | SQLite with sqlite-vec (simplest locally, weaker for a concurrent web app plus worker). A dedicated vector database (more infrastructure). |
| D11 | Job queue | Proposed | **procrastinate** (Postgres-backed) | arq or RQ (need Redis), Celery (heavy), Temporal or DBOS (more concepts than needed). |
| D12 | Static analyzer | Open (decide in Phase 3) | Spike both **Semgrep CE** and **Opengrep** | Semgrep CE taint analysis is single-file and mostly intra-procedural. Opengrep is an LGPL fork that restores inter-procedural taint. Semgrep's registry rules are licensed for internal, non-SaaS use. |
| D13 | Review UI technology | Open | **FastAPI with Jinja2 and HTMX** | A React SPA shows off more frontend skill, at the cost of a second language and a build pipeline. |
| D14 | GitHub integration | Proposed | **GitHub App**, with a personal token for local development only | A GitHub Action needs no server, but human review needs a UI anyway, and fork-PR secrets are risky. |
| D15 | Identity of posted comments | Open | **Post as the App**, with "approved by @reviewer" in the footer | Posting as the approving user gives the human ownership but obscures that the comment was AI-generated. |
| D16 | Where repository config is read from | Proposed | **Base branch only**, for every setting that changes review behaviour or coverage | Reading from head would let a PR author redirect the review of their own PR. |
| D17 | Comment volume policy | Proposed | **At most 7 comments, severity medium or higher, upheld by the verifier**; everything else visible in the UI | Higher limits raise recall and noise. |
| D18 | Target languages | Proposed / Open | **Python deep support first.** Second language open; TypeScript recommended. | Broad but shallow multi-language support dilutes quality and makes evals harder. |
| D19 | Execution verification and sandbox | Deferred (S1) | Decide if S1 starts | Self-hosted Docker with gVisor (free, needs a VM); managed microVM sandboxes (paid); a provider's code-execution tool (paid, isolated snippets only). |
| D20 | Hosting | Open | **Run locally until there's a budget.** Later: a single VM with Docker Compose and Caddy. | Free PaaS tiers are tight on memory for Postgres plus a worker, and free tiers change often. |
| D22 | Python tooling and libraries | Proposed (minor) | In use since Phase 1: uv, ruff, mypy (strict), pytest with Hypothesis, Typer, httpx, google-genai. Planned: githubkit (with GitHub App auth), SQLAlchemy 2 with Alembic (Phase 3), structlog. | pyright or ty instead of mypy; PyGithub instead of githubkit. |
