class RepositoryError(Exception):
    code = "repository_error"
    retryable = False


class TransientRepositoryError(RepositoryError):
    code = "github_temporarily_unavailable"
    retryable = True


class RepositoryConfigurationError(RepositoryError):
    code = "github_auth_unconfigured"


class RepositoryAccessError(RepositoryError):
    code = "repository_access_denied"


class RepositoryRevisionError(RepositoryError):
    code = "revision_not_found"


class RepositoryLimitError(RepositoryError):
    code = "repository_limit_exceeded"


class RepositoryArchiveError(RepositoryError):
    code = "repository_archive_invalid"
