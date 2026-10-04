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


class DataPolicyError(PRIError):
    """The configured LLM provider may not receive this repository's content."""


class PaidProviderBlockedError(PRIError):
    """A paid provider was configured while paid providers are disabled."""


class EvalCaseError(PRIError):
    """An evaluation case manifest is invalid or can't be materialized."""
