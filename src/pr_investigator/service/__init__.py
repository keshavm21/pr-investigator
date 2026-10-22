"""The hosted service (Phase 6): GitHub App auth, webhooks, a job queue and two worker roles.

The control plane (web app, fetcher, publisher) holds GitHub credentials. The analysis worker
holds only the LLM key and never sees a GitHub token (docs/security.md §4).
"""
