# Security and Threat Model

> **Status: proposed, under review (2026-10-03).** Read this before changing agent tools, prompts, GitHub authentication, rendering or publishing.

## 1. Why this matters here

An AI pull-request reviewer sits where untrusted input meets privileged credentials. Several incidents from 2026 show what goes wrong (details and sources in [research.md](research.md) §6):

- **"Comment and Control."** Prompt injection in PR titles, issue bodies and comments got popular AI reviewers and agents to leak repository and API secrets. In one report, a reviewer action posted its own API key as a PR comment.
- **The "hackerbot-claw" campaign.** An autonomous bot exploited CI workflows and replaced repositories' agent instruction files with social-engineering instructions.
- **"Ghostcommit."** Instructions hidden in an image, referenced from an `AGENTS.md` file, got past AI reviewers. A coding agent later followed them and smuggled secrets out as innocent-looking constants.
- **GitInject.** This systematic study concluded that the most critical weaknesses are *structural*: they come from how credentials and configuration files are handled, not from any particular model.

The design follows that conclusion. **Structural controls come first. Prompt-level defenses are defense in depth.**

## 2. Assets

- The GitHub App private key and installation tokens, which grant write access to pull requests in every installed repository.
- The LLM and embedding API keys, which carry a spend risk.
- The source code of installed repositories (possibly private), and data derived from it: the index, embeddings and traces.
- The integrity of what gets posted under the App's name, including `suggestion` blocks a maintainer can apply with one click.
- Reviewer trust. A manipulated "all clear" is a harm in itself, not just a missed bug.
- Service availability and the API budget.

## 3. Adversaries and entry points

- **PR authors**, including anyone who can open a fork PR on a public repository. They control:
  - the title, body and commit messages
  - every changed file
  - new files: docs, images, agent instruction files, config, `.gitattributes`
  - file names
- **Existing repository content.** It may already contain injected text, such as Ghostcommit-style payloads merged earlier.
- **A malicious installer** targeting our infrastructure through resource exhaustion, parser exploits or cross-tenant access.
- **Network attackers** sending forged webhooks or attempting CSRF or XSS against the review UI.

## 4. Trust zones and capabilities

| Component | Holds | Can reach | Sees untrusted content | Executes repository code |
|---|---|---|---|---|
| Web app (control plane) | App private key, webhook secret, OAuth client secret, session key | GitHub API, DB | Renders it (escaped) | No |
| Fetcher job (control plane) | One-hour installation token: one repository, `contents:read` | GitHub git endpoints | Writes it to the workspace volume | No (hardened git) |
| Analysis worker | LLM and embedding API keys; a DB role without access to auth or session data | LLM and embedding APIs, DB | Yes, this is its job | No |
| Publisher job (control plane) | One-hour installation token: one repository, `pull_requests:write` | GitHub API | Only approved, sanitized findings | No |
| Sandbox (stretch S1) | Nothing | No network | Yes | Yes, isolated |

In local development (Phases 1–5) a single process uses a personal access token. That mode is weaker, and only for developing against your own repositories. The separation above is required for the hosted service (Phase 6).

**The lethal trifecta.** An agent that (a) reads untrusted content, (b) can access private data or secrets, and (c) can communicate externally can be steered into exfiltrating data. Our analysis agent has (a) by necessity, so the design removes the other two as far as possible:

- **No external communication (c).** The agent has no network tools. Its only output is structured data that deterministic code validates and a human must approve before anything is posted.
- **Minimal private data (b).** There are no credentials in anything the agent's tools can reach, and no tool can read outside the run's worktree.

## 5. Threats and mitigations

### 5.1 Prompt injection

An attacker might use injection to:

- suppress true findings
- create misleading findings
- produce harmful comment content (phishing links, malicious suggestions)
- exfiltrate data
- trigger actions

Mitigations:

1. **Nothing to hijack.** The agent's tools only read. There is no shell, network or write access. Publishing is a separate component behind human approval.
2. **One instruction channel.** Instructions come only from our system prompt and from mid-conversation system messages. Repository content arrives inside tool results, delimited and labelled untrusted.

   Agent instruction files in the target repository (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, Copilot instructions and similar) are never loaded as instructions. When a PR changes one of them, it is reviewed as code and flagged.
3. **Behaviour-changing configuration comes from the base branch only** (D16). This covers `.pr-investigator.yml`, ignore paths and `.gitattributes`-based exclusions.
4. **Deterministic signals don't depend on the LLM.** Analyzer leads and risk signals feed the planner, which must address or explicitly dismiss each one. Dismissals are visible in the UI. This limits silent suppression.
5. **The verifier gets an independent context.** It sees the claim and system-extracted evidence, not the investigator's narrative.
6. **Injection is reported as a finding.** The human sees a finding when the system detects any of these:
   - instruction-like text aimed at AI reviewers
   - hidden Unicode (bidirectional controls, zero-width characters)
   - edits to agent instruction files in the PR
7. **Text only.** Images and binaries are never passed to the model, which closes the image vector Ghostcommit used against this system. New binary files referenced from instruction files are flagged.
8. **Output constraints** (§5.4).

**Residual risk.** Injected text can still bias the agent toward missing an issue. The human gate and the visible list of refuted hypotheses reduce this risk but don't eliminate it. The security eval suite measures how often it happens (§6).

### 5.2 Code execution

- **The worker never runs project code.** It never installs dependencies, builds, runs tests, or starts language servers that execute project code. For example, some language servers run build scripts or proc-macros. Those may only run inside the sandbox.
- **Hardened git.**
  - hooks disabled
  - no submodule initialization
  - LFS smudge disabled
  - restricted protocols
  - clone size limits

  Config that could launch programs lives in `.git/config`, which a fetch doesn't transfer, and repository content is never used as git configuration.
- **Parsers and analyzers run constrained.** tree-sitter, Semgrep/Opengrep and ripgrep treat files as data, but they are complex native code. They run as non-root, in a container with a read-only root filesystem, with CPU, memory and time limits, and with no extra capabilities.
- **Execution-based verification (S1) only in an isolated sandbox:**
  - gVisor or a microVM
  - no network and no secrets
  - an ephemeral filesystem and resource limits
  - opt-in per repository
  - never automatic for fork PRs or first-time contributors

### 5.3 Filesystem confinement

- Every tool path is canonicalized and must resolve inside the worktree.
- Symlinks are resolved and refused if the target is outside the worktree.
- Absolute paths and `..` segments are rejected.
- Special files, binary files and files over the size limit are refused.
- The test suite includes path-traversal and symlink-escape fixtures.

### 5.4 Output integrity

Everything the LLM writes is untrusted output.

- **Fixed template.** Comments are rendered from structured fields through a fixed template.
- **Sanitized markdown:**
  - raw HTML is removed
  - images are removed (data can leak through image URLs that GitHub's proxy fetches)
  - links are allowed only to permalinks in the same repository
  - user and team @-mentions are neutralized
  - length is capped
- **Secret scanning.** Outgoing text is scanned (known patterns plus entropy) and blocked or redacted on a match. A committed secret becomes its own finding, with the value redacted.
- **Suggestion blocks** may only replace the anchored lines, and the patched file must still parse. The UI shows the suggestion as a diff, with a warning, before approval.
- **The review UI** escapes or sanitizes all repository text and LLM output to prevent XSS.

### 5.5 Credentials

- **The App private key stays in the control plane.** Installation tokens are minted per job, down-scoped to one repository and the minimum permissions, and expire after one hour. They are never logged, and never passed to the LLM or the analysis worker.
- **The worker's environment** holds only the LLM and embedding API keys and credentials for a DB role with no access to auth or session tables. An egress allowlist proxy for the worker is optional defense in depth, in case a parser exploit leads to code execution.
- **Local personal access tokens** never appear in prompts or traces.

### 5.6 Webhooks and the review UI

- **Webhooks.** HMAC signature verification happens before parsing. Deliveries are deduped by ID. Events for unknown or disabled installations are ignored, and the endpoint is rate-limited.
- **UI login and access.** Reviewers sign in with GitHub, and the UI checks write permission on each repository.
- **Session protection.** Cookies are signed, `HttpOnly`, `Secure` and `SameSite`. State-changing requests carry CSRF tokens, and a strict Content Security Policy applies.

### 5.7 Resource exhaustion, cost abuse and coverage manipulation

- **Limits** apply to:
  - repository size, file count and file size (for both parsing and reading)
  - diff size (larger PRs get a partial review)
  - tokens and cost per run
  - daily budget per installation
  - concurrency
  - subprocess run time
- **Safe regexes.** ripgrep's default regex engine matches in linear time, so agent-supplied patterns can't cause catastrophic backtracking. PCRE2 mode stays disabled.
- **Hiding a change in a huge PR** triggers a partial review, and the review summary carries an explicit "not reviewed" list. Signals that reduce coverage are read from the base branch only.

### 5.8 Tenant isolation and data handling

- **Scoped queries.** Every query is scoped by installation.
- **Per-installation caches.** Index and embedding caches are kept per installation. A global content-addressed cache could reveal to one tenant that certain code exists in another tenant's repositories.
- **Third-party processing.** Private code is sent to the LLM and embedding providers. This is documented, and each repository must opt in.
- **Free tiers and training.** Some free tiers, including Gemini's, may use submitted data to improve the provider's products. Each adapter declares this as a capability. The pipeline refuses to send a private repository's content to such a provider unless an explicit override is set. In practice, free tiers are used only on public repositories and our own fixtures.
- **Retention.** Worktrees are deleted after each run, traces expire, and uninstalling the App deletes the installation's data.

### 5.9 The project's own supply chain

- dependencies locked (`uv.lock`) and updated automatically
- a minimal base image
- CI never exposes secrets to fork PRs (no `pull_request_target` workflow that checks out PR code)
- security scanners run on this repository

## 6. Security testing

- **Injection fixture suite.** It covers these injection vectors:
  - PR title and body
  - code comments and docs
  - head-branch config and agent instruction files
  - hidden Unicode
  - text formatted to look like tool output
  - explicit requests to exfiltrate data

  And it asserts that:
  - planted canary secrets never appear in any output
  - no external links or images appear in comments
  - head-branch configuration is ignored
  - the injection is reported
  - seeded true bugs are still found (the suppression rate)
- **Other tests** cover confinement, the sanitizer, webhook signatures and UI authorization.
- **A manual red-team pass** happens before any public demo.

## 7. Accepted residual risks

- LLM judgment can be biased by injected text. Humans make the final call.
- A precision-first policy means some real issues won't be reported.
- LLM and embedding providers process repository code. Users must accept this per repository.
