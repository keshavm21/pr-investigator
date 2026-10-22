# Demo walkthrough

A five-minute tour that needs no API key and no GitHub account, using committed traces and offline commands. To record it, run these in a terminal capture tool of your choice (for example `asciinema rec`).

1. **Setup check.** `uv run pri doctor` (calls no API).
2. **An investigation, turn by turn.** `uv run pri show evals/results/20261017-092740-a1-gemini-3.5-flash-lite/traces/seeded-broken-caller-signature-s1.jsonl`. Point out the search, the read of a caller outside the diff, the two rejected submissions and the accepted one; the quoted code is re-read by the system.
3. **A planner run.** `uv run pri show` on a trace in `evals/results/20261022-a2-test3/traces/` (one suspect, one bounded investigation each).
4. **The numbers.** `uv run pri eval report B0=evals/results/20261022-b0-test3 B1=evals/results/20261022-b1-test3 A1=evals/results/20261022-a1-test3 A2=evals/results/20261022-a2-test3 A3=evals/results/20261022-a3-test3`.
5. **The review UI.** `uv run pri serve`, sign in with the printed token, open a run: finding cards, evidence viewer, trace, approve or reject with a reason, and the publish preview (nothing posts without the confirm button).
6. **The guarantees.** `uv run pytest tests/unit/test_comments.py tests/unit/test_reviewers.py -k "inject or compliant"`: a model that obeys every injection still cannot get a link, image, mention or secret into a comment.
