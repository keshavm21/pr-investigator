# Runbook: running the hosted service

One VM, Docker Compose, four containers: `web` (UI and webhooks), `fetcher`, `worker` and `caddy` (TLS). State is one SQLite file and the run files on one volume. If you only want to review PRs from your laptop, you don't need any of this: use `pri review` and `pri serve`.

## What holds which secret

| Container | Holds | Never holds |
|---|---|---|
| `web` | App private key (file), webhook secret, OAuth client secret | LLM key |
| `fetcher` | App private key (file) | LLM key |
| `worker` | LLM key | any GitHub credential (it refuses to start if it sees one) |

The two env files, `deploy/control.env` and `deploy/worker.env`, are what separate the zones. Don't merge them.

## 1. Create the GitHub App

GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.

- **Webhook URL:** `https://YOUR-DOMAIN/webhooks/github`. **Webhook secret:** a long random string; the same value goes in `GITHUB_WEBHOOK_SECRET`.
- **Callback URL** (for sign-in): `https://YOUR-DOMAIN/oauth/callback`. Tick "Expire user authorization tokens". Leave "Request user authorization during installation" off.
- **Repository permissions:** Metadata: read. Contents: read. Pull requests: read and write. Nothing else.
- **Subscribe to events:** Pull request. (Installation events arrive without subscribing.)
- **Where can it be installed:** only on this account.
- After creating it: note the **App ID**, generate a **client secret** (→ `GITHUB_OAUTH_CLIENT_SECRET`, with the **Client ID** → `GITHUB_OAUTH_CLIENT_ID`), and **generate a private key**. Save the downloaded file as `deploy/github-app.pem`.
- Install the App on the repositories you want analyzed.

## 2. Configure

```bash
cp deploy/control.env.example deploy/control.env   # App id, secrets, PRI_BASE_URL, PRI_ALLOWED_REPOS
cp deploy/worker.env.example  deploy/worker.env    # the LLM key
chmod 600 deploy/control.env deploy/worker.env deploy/github-app.pem
export PRI_DOMAIN=pri.example.com                  # DNS A record pointing at the VM
```

The service is **closed by default**: only repositories listed in `PRI_ALLOWED_REPOS` (`owner/name`, comma-separated) are analyzed. Private repositories are refused unless `PRI_ALLOW_PRIVATE=true`; with the Gemini free tier they would be refused at analysis anyway, because that tier may train on inputs.

## 3. Start

```bash
docker compose up -d --build
docker compose ps                      # web should become healthy
curl -s https://$PRI_DOMAIN/healthz    # {"status":"ok"}
```

Open a pull request on an allowlisted repository. In the App's "Advanced" page, a recent delivery should show `202`. Within a minute or so the run appears at `https://$PRI_DOMAIN`: sign in with GitHub (you need write access to the repository), approve findings, check the preview, post.

## 4. What happens to a PR

1. GitHub sends `pull_request` (opened, reopened, synchronize, ready_for_review). The web app verifies the HMAC signature **before parsing**, drops redeliveries, applies the admission policy (allowlist, draft, size, daily budget) and queues a job, then answers `202`.
2. A new push **supersedes** the PR's older active jobs. Closing the PR stops them.
3. The fetcher mints a one-hour token for that one repository (`contents: read`), confirms the PR's head is still the job's head, fetches, and reads `.pr-investigator.yml` **from the base branch**.
4. The analysis worker reviews the local workspace and saves the run.
5. A person with write access signs in, decides, and posts. The review is posted by the App with a one-hour `pull_requests: write` token for that repository, and each comment says who approved it.

## Repository config (`.pr-investigator.yml`, on the base branch)

```yaml
enabled: true            # false: skip this repository
ignore_paths:            # globs, left out of the review and listed as not reviewed
  - "docs/**"
  - "**/generated/*.py"
reviewer: a2             # a1, a2 or a3 (default: PRI_SERVICE_REVIEWER)
max_comments: 5          # at most 7
```

A change to this file in a pull request has no effect on that pull request. Unknown keys make the whole file invalid (defaults apply, and the run's summary says so).

## Local webhook development

```bash
npx smee-client --url https://smee.io/YOUR-CHANNEL --target http://127.0.0.1:8000/webhooks/github
```

Use the smee URL as the App's webhook URL while developing, and run `pri service` and `pri worker` from the shell with the same env files loaded.

## Operations

| Task | Command |
|---|---|
| Logs | `docker compose logs -f web fetcher worker` |
| Stop analysis now | remove the repository from `PRI_ALLOWED_REPOS`, `docker compose up -d` (queued jobs still run; uninstall the App to stop everything) |
| Back up | stop the stack and copy the `data` volume (`service.sqlite`, `review.sqlite`, `runs/`) |
| Upgrade | `git pull && docker compose up -d --build` |
| Rotate the private key | generate a new key in the App settings, replace `deploy/github-app.pem`, `docker compose restart web fetcher`, then delete the old key in GitHub |
| Rotate the webhook secret | change it in the App settings and in `control.env`, `docker compose restart web` |
| Inspect the queue | `docker compose exec web sqlite3 /data/service.sqlite 'select id,repo,pr_number,state,error from job order by id desc limit 20'` |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Deliveries show `401` | the webhook secret in the App and in `control.env` differ |
| Deliveries show `202` but no job appears | the repository isn't in `PRI_ALLOWED_REPOS`, it's a draft, over the size limit, or the daily budget is used (the response body says which) |
| Job state `failed`, "fetch failed" | the App isn't installed on that repository, or lacks Contents: read |
| Job state `skipped` | private repository with `PRI_ALLOW_PRIVATE=false`, or `enabled: false` in the base branch's config |
| Job state `superseded` | a newer push arrived; this is normal |
| Worker exits at start | a GitHub credential is in `worker.env` |
| Sign-in loops back to the login page | `PRI_BASE_URL` doesn't match the URL in the browser, or the OAuth callback URL in the App is wrong |
| 403 on a run | you don't have write access to that repository |

## Known limits

- One analysis at a time per worker; scale by starting more `worker` containers (they share the queue).
- SQLite on a single host. Moving to several hosts means moving the queue to Postgres (design in architecture.md §9).
- The analysis worker can reach the internet (it needs the LLM API). An egress allowlist proxy is an optional hardening step.
