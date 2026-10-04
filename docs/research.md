# Research Notes

> Research for the design proposal, gathered 2026-10-03. Some figures come from vendor posts or press coverage rather than peer-reviewed work; those are marked "reported". Re-check anything time-sensitive before relying on it.

## 1. AI code review tools today

**Anthropic Code Review for Claude Code** (launched March 2026)

- **Design.** Dispatches several agents in parallel, scaled to PR size. Each looks for a class of problem: logic errors, security, edge cases, regressions. A verification pass filters low-confidence findings before anything is posted.
- **Output.** One summary comment plus inline comments. It never approves PRs.
- **Reported numbers.**
  - about 20 minutes per review, costing $15–25
  - substantive review comments on 54% of PRs internally, up from 16%
  - fewer than 1% of findings marked incorrect by engineers

Sources: [InfoQ](https://infoq.com/news/2026/04/claude-code-review), [Tessl](https://tessl.io/blog/anthropic-launches-ai-code-review-agents-that-scan-pull-requests-for-bugs).

**Cursor Bugbot**

- **First design.** Several bug-finding passes in parallel over *shuffled* diffs, with "majority voting" to keep only issues that appeared across passes.
- **Metric.** Cursor measures quality with a "resolution rate": an AI judge checks at merge time which reported issues actually got fixed. About 40 experiments raised it from roughly 52% to over 70% (reported).
- **The agentic move.** The biggest gain came from moving to a fully agentic loop that reasons over the diff, calls tools and decides where to dig deeper. With that loop, the model was *encouraged* to investigate aggressively, and validation kept quality up.

Sources: [Cursor blog](https://cursor.com/blog/building-bugbot), [forum summary](https://forum.cursor.com/t/building-a-better-bugbot/149040).

**CodeRabbit**

- Clones each repository into a sandbox (microVMs on Cloud Run).
- Builds a cross-file code graph.
- Runs dozens of linters and SAST tools.
- Lets its agent investigate by writing shell commands (`cat`, `grep`, `ast-grep`) instead of calling predefined tools.
- Generates verification scripts in the sandbox to filter out low-value or hallucinated feedback.

Sources: [Google Cloud blog](https://cloud.google.com/blog/products/ai-machine-learning/how-coderabbit-built-its-ai-code-review-agent-with-google-cloud-run), [CodeRabbit on context engineering](https://coderabbit.ai/blog/context-engineering-ai-code-reviews), [The AI Engineer](https://theaiengineer.substack.com/p/how-coderabbit-actually-works).

**The pattern across tools.** Parallel analysis, then a verification step that checks candidates against actual code behaviour, then ranking. Deterministic static analysis serves as a high-precision base layer. False positives are widely described as the failure mode that kills adoption.

Sources: [Tembo](https://www.tembo.io/blog/ai-agents-for-code-review), [DeepSource](https://deepsource.com/resources/ai-code-review-tools).

**Takeaways for this project**

- Verification before posting is standard. It isn't a differentiator by itself.
- The differentiators open to this project are:
  - evidence that is *mechanically grounded* in what the agent observed
  - transparent traces that include refuted hypotheses
  - decoy-based evaluation of false positives
  - a security architecture shaped by the 2026 incidents (§6)
- Measure quality as "did humans act on it", following Cursor's resolution rate and Martian's online benchmark.
- Prefer agentic investigation with strong validation over prompting the model to be timid.

## 2. Agentic vulnerability research

**Google Project Naptime / Big Sleep**

- **Principles:** give the model room to reason, an interactive environment, specialized tools (code browser, Python sandbox, debugger, reporter), *automatic verification with certainty*, and sampling across multiple independent trajectories.
- **Result:** large gains on CyberSecEval2 tasks.

Sources: [The Hacker News](https://thehackernews.com/2024/06/google-introduces-project-naptime-for.html), [Security Boulevard](https://securityboulevard.com/2024/06/googles-project-naptime-aims-for-ai-based-vulnerability-research).

**OpenAI Aardvark**

- **Pipeline:** builds a threat model of the repository, scans commits against it, reproduces candidates in a sandbox to confirm exploitability and cut false positives, then proposes patches for human review.
- **Reported:** 92% recall on golden repositories with known or synthetic vulnerabilities.

Sources: [OpenAI](https://openai.com/index/introducing-aardvark), [VentureBeat](https://venturebeat.com/security/meet-aardvark-openais-in-house-security-agent-for-code-analysis-and-patching).

**Takeaways for this project**

- Separate hypothesis generation from investigation, and investigation from verification.
- Execution-based verification is the gold standard but needs a real sandbox, so it is stretch S1.
- A per-repository threat model is useful context, so it is stretch S5.

## 3. LLMs combined with static analysis

**IRIS** (ICLR 2025)

- Uses an LLM to infer project-specific taint sources and sinks for CodeQL.
- Uses the LLM again to triage the resulting alerts.
- Detected roughly twice as many vulnerabilities as CodeQL alone.

Source: [paper](https://proceedings.iclr.cc/paper_files/paper/2025/file/582d4e27fa24168f3af1f4582655034b-Paper-Conference.pdf).

**"Sifting the Noise"** (2026)

- Agent frameworks (Aider, OpenHands, SWE-agent) were used to filter false positives from SAST alerts.
- Agentic setups far outperformed plain prompting with strong models: for example, 95.5% of false positives identified at 95.5% precision, versus 36.4% for vanilla prompting.
- Gains were limited or inconsistent with weaker models.
- Aggressive false-positive filtering can suppress real vulnerabilities.

Source: [arXiv 2601.22952](https://arxiv.org/abs/2601.22952).

**Memoir** (2026)

- Turns historical false-positive alerts into verified, evolving "false-positive memories".
- Retrieves them when triaging new alerts.
- Reports very high F1 on CWE-Bench-Java.

Source: [arXiv 2608.09181](https://arxiv.org/abs/2608.09181).

**Takeaways for this project**

- Static analyzers are good *lead generators* and corroborators; LLM agents are good *triagers*.
- A verifier must be measured for the true positives it kills, not only the false positives.
- Retrieval over past decisions is a meaningful RAG use, so it is stretch S4.

## 4. Code retrieval: grep, embeddings and structure

**"Is Grep All You Need?"** (2026)

- Across several agent harnesses, grep-style search wrapped in a good harness generally matched or beat vector retrieval.
- Results depended strongly on the harness and the tool-calling style.

Source: [arXiv 2605.15184](https://arxiv.org/abs/2605.15184).

**CORE-Bench** (EMNLP 2026)

- Embedding models drop sharply when moving from classic code search to repository-level retrieval for agentic coding.

Source: [arXiv 2606.11864](https://arxiv.org/abs/2606.11864).

**Agentic search in practice.** Practitioner write-ups note that agentic search avoids stale embeddings, and that Claude Code relies on agentic search rather than a vector index.

Sources: [Sara Zan](https://www.zansara.dev/posts/2026-03-15-vector-dbs-vs-grep/), [Morph](https://www.morphllm.com/agentic-search).

**cAST** (EMNLP 2025 Findings)

- AST-based chunking (split large nodes, merge small siblings) beats line-based chunking.
- +4.3 Recall@5 on RepoEval and +2.67 Pass@1 on SWE-bench.
- Code: [astchunk](https://github.com/yilinjz/astchunk).

Source: [arXiv 2506.15655](https://arxiv.org/abs/2506.15655).

**Embedding models (2026)**

- Voyage's code model is the leading managed option for code.
- Qwen3-Embedding and Jina code embeddings are strong open-weight options.

Source: [Prem AI overview](https://www.premai.io/blog/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting/).

**Hybrid search in Postgres.** Combine full-text (or BM25 via ParadeDB) with pgvector using Reciprocal Rank Fusion, because scores from the two aren't comparable.

Source: [ParadeDB](https://paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual).

**Takeaways for this project**

- Default to lexical and structural tools.
- Use embeddings only for "find by behaviour" questions and convention exemplars, and prove their value by ablation.
- Chunk by AST.
- Content-address the index so it is never stale.

## 5. Code intelligence and analyzers

- **tree-sitter.** py-tree-sitter (0.26) plus `tree-sitter-language-pack`, prebuilt parsers for well over 100 languages, with tags queries for definitions and references. Sources: [language pack](https://openapps.pro/packages/tree-sitter-language-pack), [py-tree-sitter](https://tessl.io/registry/tessl/pypi-tree-sitter).
- **multilspy** (Microsoft). A Python client wrapping language servers for definitions and references. Serena builds on it for agent tools. Sources: [multilspy](https://docsearch.algolia.com/mcp/docs/repo/microsoft/multilspy), [Serena](https://mcpservers.org/es/servers/oraios/serena).
- **SCIP.** Precise, index-based navigation, now under open governance. Source: [Sourcegraph](https://sourcegraph.com/blog/announcing-scip).
- **Semgrep CE and Opengrep.**
  - Semgrep CE taint analysis is single-file and mostly intra-procedural. Cross-file analysis is commercial.
  - Opengrep (LGPL-2.1, January 2025) is a community fork that restores inter-procedural taint analysis and other removed features.
  - Semgrep's registry rules are licensed for internal, non-competing, non-SaaS use.

  Sources: [Semgrep taint docs](https://docs.semgrep.dev/writing-rules/data-flow/taint-mode), [Opengrep vs Semgrep](https://appsecsanta.com/opengrep-vs-semgrep), [Semgrep licensing](https://semgrep.dev/docs/licensing).

**Takeaways for this project**

- tree-sitter for breadth and robustness.
- Language servers only where they don't execute project code.
- Pick the analyzer after a spike (D12), with rule licensing in mind.

## 6. Security incidents and research (2026)

**"Comment and Control"** (April 2026)

- Prompt injection in PR titles, issue bodies and comments hijacked AI agents including Claude Code Security Review, Gemini CLI Action and GitHub Copilot Agent into exfiltrating secrets.
- In one report, a reviewer action posted its own API key as a comment.

Sources: [Cloud Security Alliance](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/04/CSA_research_note_comment_control_github_prompt_injection_20260417-csa-styled.pdf), [VentureBeat](https://venturebeat.com/security/ai-agent-runtime-security-system-card-audit-comment-and-control-2026).

**The hackerbot-claw campaign** (February 2026). An autonomous bot exploited GitHub Actions workflows and replaced repositories' `CLAUDE.md` files with social-engineering instructions.

Source: [Cloud Security Alliance](https://labs.cloudsecurityalliance.org/research/csa-research-note-claude-code-github-action-prompt-injection/).

**Ghostcommit**

1. An `AGENTS.md` file referenced an image with hidden instructions.
2. Images are excluded from review by default, so the instructions passed AI review.
3. A coding agent later followed them and committed the `.env` secrets encoded as integer constants.

Source: [BleepingComputer](https://www.bleepingcomputer.com/news/security/ghostcommit-hides-prompt-injection-in-images-to-fool-ai-agents-steal-secrets/).

**GitInject** (2026)

- Eleven attack classes against CI/CD agents: config-file injection, credential exfiltration, judgment manipulation and availability.
- Every tested vendor was vulnerable to at least one class in its default configuration.
- The most critical weaknesses are structural: how credentials and configuration files are handled.

Source: [arXiv 2606.09935](https://arxiv.org/abs/2606.09935).

**Sandboxing.** gVisor intercepts syscalls in a userspace kernel: fast, with stronger isolation than plain containers. Firecracker microVMs give a hardware boundary with sub-second boot.

Sources: [Fly.io](https://fly.io/learn/firecracker-vs-gvisor/), [Tembo](https://www.tembo.io/blog/ai-agent-sandbox).

**Takeaways for this project**

- No credentials or network in the agent's reach.
- Configuration from the base branch only.
- Never load repository instruction files as instructions.
- Text-only inputs.
- A sanitized output path behind human approval.
- An explicit injection test suite ([security.md](security.md)).

## 7. Benchmarks for AI code review

| Benchmark | What it is | Source |
|---|---|---|
| Martian Code Review Bench (Feb 2026) | Open source. Offline: 50 curated PRs with human-verified issues and a gold set. Online: tracks which bot comments developers actually act on. Uses F-beta scoring. | [Kilo summary](https://blog.kilo.ai/p/martians-independent-benchmark-tested), [CodeAnt](https://www.codeant.ai/blogs/ai-code-review-benchmark-results-from-200-000-real-pull-requests) |
| Qodo PR-Review-Bench | 100 real merged PRs with 580 injected issues (best-practice violations and functional bugs), 7 languages, MIT licence | [Hugging Face](https://huggingface.co/datasets/Qodo/PR-Review-Bench/blob/main/README.md) |
| SWR-Bench (FSE 2026) | 1,000 manually verified PRs with full project context. An LLM-based evaluation agrees with humans about 90% of the time. | [arXiv 2509.01494](https://arxiv.org/abs/2509.01494) |
| ContextCRBench | 67,910 context-enriched entries for hunk-level quality, line-level localization and comment generation | [arXiv 2511.07017](https://arxiv.org/html/2511.07017v1) |
| c-CRAB | Tests generated from human reviews. Existing agents solve about 40%. | [arXiv 2603.23448](https://arxiv.org/abs/2603.23448) |
| MacroscopeBench | 12,000+ real bugs from 1,500+ repositories. Two LLM judges score recall and precision. | [Macroscope](https://macroscope.com/content/ai-code-review-benchmark-best-models) |
| Tenki | 122 real bugs across 50 PRs from large open-source projects | [Tenki](https://tenki.cloud/benchmarks/code-reviewer) |

Datasets for historical bugs: [BugsInPy](https://2020.esec-fse.org/details/esecfse-2020-tool-demos/26/BugsInPy-A-Database-of-Existing-Bugs-in-Python-Programs-to-Enable-Controlled-Testing), [CVEfixes](https://arxiv.org/pdf/2107.08760), [MoreFixes](https://conf.researchr.org/details/promise-2024/promise-2024-papers/1/MoreFixes-A-Large-Scale-Dataset-of-CVE-Fix-Commits-Mined-through-Enhanced-Repository).

**Takeaways for this project**

- Precision and recall pull against each other, so report both, plus F-beta.
- Validate any LLM judge against human labels.
- Owned, post-cutoff cases avoid contamination.
- Decoys test the "evidence first" claim directly.

## 8. Platform facts the design relies on

**GitHub**

- **Installation tokens** expire after 1 hour and can be scoped to specific repositories and permissions at creation. Source: [docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app).
- **Webhooks** must be answered with a 2xx within 10 seconds. Verify signatures. `X-GitHub-Delivery` is unique per delivery and reused on redelivery. Source: [docs](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks).
- **Reviews.** "Create a review" takes `commit_id`, `event` (`APPROVE`, `REQUEST_CHANGES`, `COMMENT`) and `comments[]` with `path`, `line`, `side`, `start_line` and `start_side`. The endpoint triggers notifications and can hit secondary rate limits. Source: [docs](https://docs.github.com/en/rest/pulls/reviews).

**Claude API** (from Anthropic's current API reference; relevant once the project can use a paid provider)

- Structured outputs (`output_config.format`) and strict tools.
- Adaptive thinking with an `effort` setting. Thinking can't be disabled on Opus 5.5, and its default effort is `medium`.
- No forced `tool_choice` and no assistant prefill on current models.
- Prompt caching uses prefix matching. In a fan-out, a cache entry becomes readable only after the first response starts streaming.
- Task budgets (beta).
- Mid-conversation system messages.
- A `refusal` stop reason, with server-side fallback.
- Thinking blocks are valid only in unedited conversations, so history must be append-only.
- The Batch API costs 50% less.
- Pricing (per million tokens): Opus 5.5 is $4 input and $20 output, with cache reads at $0.20. Sonnet 5.5 is $2 and $10. Haiku 4.5 is $1 and $5.

## 9. Free model tiers (October 2026)

Free-tier terms change often, and most figures below come from third-party summaries rather than official docs. Google's own rate-limit page says to check AI Studio for current values. Treat these as approximate.

| Provider | Free allowance (reported) | Fit for code review | Data use |
|---|---|---|---|
| **Gemini API** (AI Studio key) | Google's docs (checked 2026-10-04) list free tiers for stable models `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash` and `gemini-3.5-flash-lite`. Gemini 2.5 is restricted to existing users. Exact limits aren't published: each project sees its own in AI Studio. Third-party reports range from about 20 to 1,500 requests/day depending on model and account. 1M-token context. | **Best fit.** A diff plus context fits in one request. Native SDK supports JSON-schema output and function calling. Reports say the OpenAI-compatible layer handles schemas less reliably, so use the native SDK. | Free-tier data may be used to improve Google's products. Use only with public code. |
| **Groq** | About 30 requests/min, 1,000 requests/day, but only about 8k tokens/min and 200k tokens/day on the main models (gpt-oss-120b/20b, Qwen). | Very fast, OpenAI-compatible, with tool use and structured outputs on supported models. The per-minute token cap is smaller than one review prompt with a real diff, though small calls like an eval judge fit. | See Groq's terms. |
| **OpenRouter `:free` models** | 20 requests/min. 50 requests/day without purchased credits; 1,000/day only after buying $10 of credits (excluded). | 50 requests a day is too few for evals. Model availability changes often. | Varies by upstream provider. Some free routes log prompts. |
| **GitHub Models** | Roughly 50–150 requests/day depending on model tier, with about 8k input and 4k output tokens per request. | The per-request input cap is too small for diffs plus context. | GitHub's terms |
| **Local models (Ollama)** | Unlimited, with no API key. | Private and free to run as often as needed. Quality depends on hardware: models that fit a laptop are noticeably weaker at multi-file reasoning. A good no-quota fallback for development and tests, behind the `openai_compatible` adapter. | Stays on your machine |

Sources: [Gemini rate limits (official)](https://ai.google.dev/gemini-api/docs/rate-limits), [Gemini free-tier summary](https://www.memetik.ai/guides/gemini-api-free-tier-limits), [Gemini free-tier guide](https://www.aifreeapi.com/en/posts/gemini-api-free-tier-rate-limits), [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai?hl=en), [Gemini schema issue via the compatibility layer](https://discuss.ai.google.dev/t/structured-output-not-working-via-the-openai-compatible-layer/108341), [Groq free-tier limits](https://klymentiev.com/blog/groq-pricing), [Groq structured outputs](https://console.groq.com/docs/structured-outputs), [OpenRouter free tier](https://klymentiev.com/blog/openrouter-free-tier), [OpenRouter privacy notes](https://www.llmrumors.com/news/openrouter-free-model-limits-privacy-fallbacks), [GitHub Models free tier](https://freellm.net/providers/github-models).

**Takeaways for this project**

- Gemini Flash on the free tier is the Phase 1 default.
- An OpenAI-compatible adapter later adds Groq (for a judge from a different model family) and local Ollama (for unlimited development runs) at almost no cost in code.
- The free tiers' data-use terms rule out private repositories.
