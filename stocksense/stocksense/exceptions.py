"""Typed domain exceptions.

Every exception carries a ``user_message`` that is safe to show in the UI.
Internal details (if any) go to ``detail`` and are only written to the log.
"""
from __future__ import annotations


class StockSenseError(Exception):
    """Base class for all expected application errors."""

    default_message = "The operation could not be completed."

    def __init__(self, user_message: str | None = None, *, detail: str | None = None):
        self.user_message = user_message or self.default_message
        self.detail = detail
        super().__init__(self.user_message)


class ConfigurationError(StockSenseError):
    default_message = "The application configuration is invalid. Check your .env file."


class DatabaseError(StockSenseError):
    default_message = "A database error occurred. Your change was not saved; please try again."


class NotFoundError(StockSenseError):
    default_message = "The requested record does not exist."


class DuplicateRecordError(StockSenseError):
    default_message = "A record with the same identifier already exists."


class DomainValidationError(StockSenseError):
    """Input data failed a business rule."""

    default_message = "The submitted data is not valid."

    def __init__(self, user_message: str | None = None, *, errors: list[str] | None = None,
                 detail: str | None = None):
        super().__init__(user_message, detail=detail)
        self.errors = errors or []


class AuthenticationError(StockSenseError):
    default_message = "Invalid username or password."


class SessionExpiredError(AuthenticationError):
    default_message = "Your session has expired. Please sign in again."


class PermissionDeniedError(StockSenseError):
    default_message = "You do not have permission to perform this action."


class InvalidTransitionError(StockSenseError):
    default_message = "This action is not allowed in the proposal's current status."


class StaleVersionError(StockSenseError):
    default_message = ("This proposal was changed after you opened it. "
                       "Reload the latest version and review it again.")


class AlreadyDecidedError(InvalidTransitionError):
    default_message = "A decision has already been recorded for this proposal version."


class ApprovalBlockedError(StockSenseError):
    default_message = "Approval was blocked because the proposal failed validation."

    def __init__(self, user_message: str | None = None, *, errors: list[str] | None = None,
                 detail: str | None = None):
        super().__init__(user_message, detail=detail)
        self.errors = errors or []


class AuditWriteError(StockSenseError):
    default_message = ("The audit record could not be written, so the operation was cancelled. "
                       "Nothing was changed.")


class ImportValidationError(StockSenseError):
    default_message = "The file could not be imported. Fix the listed problems and try again."


class AIServiceError(StockSenseError):
    """Base class for AI-review failures. Never fatal to core features."""

    default_message = "The AI review is unavailable right now. Core features continue to work."
    status = "ERROR"
    retryable = False


class AINotConfiguredError(AIServiceError):
    default_message = ("AI review is not configured. Add GEMINI_API_KEY to your .env file "
                       "and restart the application.")
    status = "NOT_CONFIGURED"


class AIAuthError(AIServiceError):
    default_message = "The Gemini API key was rejected. Check GEMINI_API_KEY in your .env file."
    status = "AUTH_ERROR"


class AIModelError(AIServiceError):
    default_message = "The configured Gemini model is not available. Check GEMINI_MODEL."
    status = "MODEL_ERROR"


class AIRateLimitError(AIServiceError):
    default_message = "Gemini rate limit or quota reached. Wait a while and retry."
    status = "RATE_LIMITED"
    retryable = True


class AITimeoutError(AIServiceError):
    default_message = "Gemini did not respond in time. Retry later."
    status = "TIMEOUT"
    retryable = True


class AIProviderError(AIServiceError):
    default_message = "Gemini returned a temporary error. Retry later."
    status = "PROVIDER_ERROR"
    retryable = True


class AIResponseError(AIServiceError):
    default_message = "Gemini returned a response that could not be validated, so it was discarded."
    status = "INVALID_RESPONSE"


class AIBlockedError(AIServiceError):
    default_message = "Gemini declined to produce a review for this content."
    status = "BLOCKED"
