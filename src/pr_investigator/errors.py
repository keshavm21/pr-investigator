"""Exception hierarchy. Every error raised on purpose derives from PRIError."""


class PRIError(Exception):
    """Base class for expected, user-facing errors."""


class ConfigError(PRIError):
    """Missing or invalid configuration (for example, no API key)."""


class GitError(PRIError):
    """A git command failed."""


class DiffParseError(PRIError):
    """`git diff` output didn't have the expected shape."""


class GitHubError(PRIError):
    """A GitHub API request failed or returned something unexpected."""


class GitHubValidationError(GitHubError):
    """GitHub answered 422: it rejected the request's content (for example, a comment line)."""


class PublishError(PRIError):
    """A review can't be published: nothing approved, a stale head, no pull request, ..."""


class DataPolicyError(PRIError):
    """The configured LLM provider may not receive this repository's content."""


class PaidProviderBlockedError(PRIError):
    """A paid provider was configured while paid providers are disabled."""


class EvalCaseError(PRIError):
    """An evaluation case manifest is invalid or can't be materialized."""
