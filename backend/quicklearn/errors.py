"""Error taxonomy.

PRD §11: "Fail loudly on provider errors with actionable messages. 'Invalid API key for
OpenAI — check Settings' not '500 Internal Server Error.'"

Two properties carry that through the whole stack:

* ``message`` is written for the person reading it in the UI, naming the provider and the fix.
* ``retryable`` decides whether the job queue burns another attempt. Retrying a bad API key
  three times just delays an error the user has to act on regardless.
"""

from __future__ import annotations

from typing import Any


class QuicklearnError(Exception):
    """Base class. ``retryable`` defaults to False — failing fast is the safer default."""

    status_code = 500
    retryable = False

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.__class__.__name__, "message": self.message, **self.detail}


class ConfigurationError(QuicklearnError):
    """A required provider or setting is missing. Surfaced at config time where possible."""

    status_code = 400


class NotFoundError(QuicklearnError):
    status_code = 404


class ValidationError(QuicklearnError):
    status_code = 422


class ConflictError(QuicklearnError):
    """Local patch: the write is based on stale state, e.g. an autosave of a regenerated note."""

    status_code = 409


class ProviderError(QuicklearnError):
    """Base for anything that went wrong talking to an inference provider."""

    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail={**(detail or {}), "provider": provider})
        self.provider = provider


class ProviderAuthError(ProviderError):
    """Bad or missing credentials. Never retried — the key will not fix itself."""

    status_code = 401
    retryable = False


class ProviderRateLimitError(ProviderError):
    """Rate limited. Retryable, and the queue backs off between attempts."""

    status_code = 429
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        retry_after: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider=provider, detail=detail)
        self.retry_after = retry_after


class ProviderQuotaError(ProviderError):
    """Out of credit. Retrying cannot help."""

    status_code = 402
    retryable = False


class ProviderUnavailableError(ProviderError):
    """Transient upstream failure — 5xx, timeout, connection reset."""

    status_code = 503
    retryable = True


class ProviderResponseError(ProviderError):
    """The provider answered, but not in a usable shape.

    Retryable because a single malformed structured-output response is usually a sampling
    accident that a second attempt clears.
    """

    status_code = 502
    retryable = True


class UnsupportedOperationError(QuicklearnError):
    """A provider does not implement a capability, e.g. Anthropic has no speech-to-text."""

    status_code = 400


class PipelineError(QuicklearnError):
    """A pipeline stage failed for a reason that isn't the provider's fault."""

    status_code = 500


class MediaError(PipelineError):
    """ffmpeg or a decoder rejected the input file."""

    status_code = 422


class CancelledError(QuicklearnError):
    """Raised inside a stage when the user cancels the job. Not an error condition."""

    status_code = 409
    retryable = False


class DependencyMissingError(QuicklearnError):
    """An optional extra is not installed. The message must name the install command."""

    status_code = 501
