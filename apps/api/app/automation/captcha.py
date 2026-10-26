"""Pluggable CAPTCHA resolution with LLM OCR and assisted fallback."""

from __future__ import annotations

import os
from typing import Protocol

from pydantic import Field

from .exceptions import CaptchaResolutionError
from .schemas import (
    CaptchaAttemptDTO,
    CaptchaInputDTO,
    CaptchaMode,
    CaptchaRecognitionDTO,
    CaptchaResultDTO,
    StrictDTO,
    StructuredLLMResultDTO,
)


class CaptchaResolverConfig(StrictDTO):
    max_attempts: int = Field(default=5, ge=1, le=20)
    confidence_threshold: float = Field(default=0.8, ge=0, le=1)
    allowed_chars: str = (
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    )
    min_length: int = Field(default=4, ge=1)
    max_length: int = Field(default=8, ge=1)

    @classmethod
    def from_env(cls) -> CaptchaResolverConfig:
        return cls(
            max_attempts=int(os.environ.get("CAPTCHA_MAX_ATTEMPTS", "5")),
            confidence_threshold=float(
                os.environ.get("CAPTCHA_CONFIDENCE_THRESHOLD", "0.80")
            ),
            allowed_chars=os.environ.get(
                "CAPTCHA_ALLOWED_CHARS",
                "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            ),
            min_length=int(os.environ.get("CAPTCHA_MIN_LENGTH", "4")),
            max_length=int(os.environ.get("CAPTCHA_MAX_LENGTH", "8")),
        )


class CaptchaVisionProvider(Protocol):
    async def recognize_captcha(
        self, image_bytes: bytes, mime_type: str
    ) -> StructuredLLMResultDTO[CaptchaRecognitionDTO]: ...


class AssistedCaptchaBroker(Protocol):
    async def request_answer(
        self, request: CaptchaInputDTO, attempts: list[CaptchaAttemptDTO]
    ) -> str: ...


class CaptchaResolver(Protocol):
    async def resolve(self, request: CaptchaInputDTO) -> CaptchaResultDTO: ...


class LLMThenAssistedCaptchaResolver:
    """Try isolated vision OCR, then keep the browser session for a user answer."""

    def __init__(
        self,
        provider: CaptchaVisionProvider,
        *,
        assisted_broker: AssistedCaptchaBroker | None,
        config: CaptchaResolverConfig | None = None,
    ) -> None:
        self._provider = provider
        self._assisted_broker = assisted_broker
        self.config = config or CaptchaResolverConfig()

    async def resolve(self, request: CaptchaInputDTO) -> CaptchaResultDTO:
        attempts: list[CaptchaAttemptDTO] = []
        for attempt_number in range(1, self.config.max_attempts + 1):
            try:
                response = await self._provider.recognize_captcha(
                    request.image_bytes, request.mime_type
                )
                normalized = self.normalize(response.data.text)
                problem = self._problem(normalized, response.data.confidence)
                attempts.append(
                    CaptchaAttemptDTO(
                        attempt_number=attempt_number,
                        mode=CaptchaMode.LLM_OCR,
                        result=problem or "accepted",
                        confidence=response.data.confidence,
                        provider=response.provider,
                        model=response.actual_model,
                    )
                )
                if problem is None:
                    return CaptchaResultDTO(
                        text=normalized,
                        confidence=response.data.confidence,
                        mode=CaptchaMode.LLM_OCR,
                        provider=response.provider,
                        model=response.actual_model,
                        artifact_id=request.artifact_id,
                        attempts=attempts,
                    )
            except Exception as exc:  # provider errors are audit data, then fallback
                attempts.append(
                    CaptchaAttemptDTO(
                        attempt_number=attempt_number,
                        mode=CaptchaMode.LLM_OCR,
                        result=f"provider_error:{type(exc).__name__}",
                    )
                )

        if self._assisted_broker is None:
            raise CaptchaResolutionError(
                "LLM OCR exhausted and assisted CAPTCHA broker is unavailable"
            )

        answer = self.normalize(
            await self._assisted_broker.request_answer(request, attempts)
        )
        problem = self._problem(answer, 1.0)
        attempts.append(
            CaptchaAttemptDTO(
                attempt_number=len(attempts) + 1,
                mode=CaptchaMode.ASSISTED,
                result=problem or "accepted",
                confidence=1.0,
            )
        )
        if problem is not None:
            raise CaptchaResolutionError(
                f"Assisted CAPTCHA answer is invalid: {problem}"
            )
        return CaptchaResultDTO(
            text=answer,
            confidence=1.0,
            mode=CaptchaMode.ASSISTED,
            artifact_id=request.artifact_id,
            attempts=attempts,
        )

    def normalize(self, value: str) -> str:
        return "".join(
            character
            for character in value.strip()
            if not character.isspace() and character in self.config.allowed_chars
        )

    def _problem(self, value: str, confidence: float) -> str | None:
        if not value:
            return "empty"
        if len(value) < self.config.min_length:
            return "too_short"
        if len(value) > self.config.max_length:
            return "too_long"
        if any(character not in self.config.allowed_chars for character in value):
            return "invalid_characters"
        if confidence < self.config.confidence_threshold:
            return "low_confidence"
        return None


class AssistedOnlyCaptchaResolver:
    def __init__(
        self,
        broker: AssistedCaptchaBroker,
        *,
        config: CaptchaResolverConfig | None = None,
    ) -> None:
        self._broker = broker
        self._normalizer = LLMThenAssistedCaptchaResolver(
            provider=_UnusedVisionProvider(),
            assisted_broker=broker,
            config=config,
        )

    async def resolve(self, request: CaptchaInputDTO) -> CaptchaResultDTO:
        answer = self._normalizer.normalize(
            await self._broker.request_answer(request, [])
        )
        problem = self._normalizer._problem(answer, 1.0)
        if problem:
            raise CaptchaResolutionError(
                f"Assisted CAPTCHA answer is invalid: {problem}"
            )
        return CaptchaResultDTO(
            text=answer,
            confidence=1,
            mode=CaptchaMode.ASSISTED,
            artifact_id=request.artifact_id,
            attempts=[
                CaptchaAttemptDTO(
                    attempt_number=1,
                    mode=CaptchaMode.ASSISTED,
                    result="accepted",
                    confidence=1,
                )
            ],
        )


class _UnusedVisionProvider:
    async def recognize_captcha(
        self, image_bytes: bytes, mime_type: str
    ) -> StructuredLLMResultDTO[CaptchaRecognitionDTO]:
        raise AssertionError("vision provider is not used in assisted-only mode")
