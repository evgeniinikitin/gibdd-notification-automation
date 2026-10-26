"""Domain-specific exceptions for the isolated automation package."""

from __future__ import annotations

from typing import Any


class AutomationError(RuntimeError):
    """Base class for operational automation failures."""


class BusinessRuleViolation(AutomationError):
    def __init__(self, message: str, *, issues: list[Any] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class PayloadValidationError(AutomationError):
    pass


class OpenRouterError(AutomationError):
    pass


class OpenRouterResponseError(OpenRouterError):
    pass


class GeminiError(AutomationError):
    pass


class GeminiResponseError(GeminiError):
    pass


class DocumentGenerationError(AutomationError):
    pass


class DocumentParseError(AutomationError):
    pass


class ReceiptParseError(AutomationError):
    pass


class UnsafeSubmissionError(AutomationError):
    """Raised when an irreversible portal action is attempted in dry-run."""


class SubmissionError(AutomationError):
    pass


class CaptchaResolutionError(AutomationError):
    pass
