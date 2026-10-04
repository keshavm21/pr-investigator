# Inventory service (evaluation fixture)

A small, deliberately simple web service used as the base project for PR Investigator's
evaluation cases. Each case in `evals/cases/` describes a pull request against this code.
The code is never executed by the evaluation harness; it only needs to be realistic to review.

Some cases intentionally introduce vulnerabilities. Don't copy code from the cases.
