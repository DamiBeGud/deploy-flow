class RepositoryError(Exception):
    code = "repository_error"
    retryable = False

    def __init__(self, detail: str | None = None):
        super().__init__(detail or self.code)
        self.detail = detail

    @property
    def user_message(self) -> str:
        return self.detail or "Repository access could not be verified."


class TransientRepositoryError(RepositoryError):
    code = "github_temporarily_unavailable"
    retryable = True


class RepositoryConfigurationError(RepositoryError):
    code = "github_auth_unconfigured"

    @property
    def user_message(self) -> str:
        return "GitHub App credentials are not configured for repository analysis."


class RepositoryAccessError(RepositoryError):
    code = "repository_access_denied"

    @property
    def user_message(self) -> str:
        return self.detail or "The GitHub App cannot access this repository. Check its installation and permissions."


class GitHubTokenRequestError(TransientRepositoryError):
    code = "github_token_request_failed"

    @property
    def user_message(self) -> str:
        return self.detail or "GitHub temporarily rejected the App token request; please retry."


class RepositoryRevisionError(RepositoryError):
    code = "revision_not_found"

    @property
    def user_message(self) -> str:
        return self.detail or "The requested repository revision was not found."


class RepositoryLimitError(RepositoryError):
    code = "repository_limit_exceeded"


class RepositoryArchiveError(RepositoryError):
    code = "repository_archive_invalid"
