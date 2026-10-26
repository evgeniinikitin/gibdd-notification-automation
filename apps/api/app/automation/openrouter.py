"""OpenRouter Free Models Router provider with strict structured responses."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from .exceptions import OpenRouterError, OpenRouterResponseError
from .schemas import (
    CaptchaRecognitionDTO,
    ImageOCRDTO,
    LLMUsageDTO,
    StrictDTO,
    StructuredLLMResultDTO,
)


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
SleepCallable = Callable[[float], Awaitable[None]]


class OpenRouterConfig(StrictDTO):
    api_key: str = Field(min_length=1)
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "openrouter/free"
    captcha_model: str = "openrouter/free"
    http_referer: str | None = None
    app_title: str = "Buscript"
    proxy_url: str | None = None
    timeout_seconds: float = Field(default=60, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: tuple[float, ...] = (0.25, 1.0, 2.0)

    @classmethod
    def from_env(cls) -> OpenRouterConfig:
        return cls(
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            model=os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            captcha_model=os.environ.get(
                "OPENROUTER_CAPTCHA_MODEL", "openrouter/free"
            ),
            http_referer=os.environ.get("OPENROUTER_HTTP_REFERER") or None,
            app_title=os.environ.get("OPENROUTER_APP_TITLE", "Buscript"),
            proxy_url=os.environ.get("OPENROUTER_PROXY_URL") or None,
        )


class OpenRouterProvider:
    """An isolated HTTP client; global proxy environment variables are ignored."""

    def __init__(
        self,
        config: OpenRouterConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: SleepCallable = asyncio.sleep,
    ) -> None:
        self.config = config
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{config.base_url.rstrip('/')}/",
            proxy=config.proxy_url,
            timeout=config.timeout_seconds,
            trust_env=False,
        )

    async def __aenter__(self) -> OpenRouterProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[StructuredModel],
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> StructuredLLMResultDTO[StructuredModel]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return await self._request_structured(
            messages=messages,
            response_model=response_model,
            requested_model=model or self.config.model,
        )

    async def recognize_captcha(
        self, image_bytes: bytes, mime_type: str
    ) -> StructuredLLMResultDTO[CaptchaRecognitionDTO]:
        if not mime_type.startswith("image/"):
            raise OpenRouterError("Captcha MIME type must be image/*")
        prompt = (
            "Ты OCR-модуль для простой текстовой капчи. Распознай только "
            "символы на изображении. Верни JSON с полями text и confidence "
            "(число от 0 до 1). Не добавляй пояснений."
        )
        return await self._request_structured(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": self._data_url(image_bytes, mime_type)
                            },
                        },
                    ],
                }
            ],
            response_model=CaptchaRecognitionDTO,
            requested_model=self.config.captcha_model,
        )

    async def extract_image_text(
        self, image_bytes: bytes, mime_type: str
    ) -> StructuredLLMResultDTO[ImageOCRDTO]:
        if not mime_type.startswith("image/"):
            raise OpenRouterError("Image MIME type must be image/*")
        return await self._request_structured(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Извлеки весь читаемый текст с изображения без "
                                "домыслов. Сохрани строки. Верни JSON с полями "
                                "text и confidence."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": self._data_url(image_bytes, mime_type)
                            },
                        },
                    ],
                }
            ],
            response_model=ImageOCRDTO,
            requested_model=self.config.model,
        )

    async def _request_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type[StructuredModel],
        requested_model: str,
    ) -> StructuredLLMResultDTO[StructuredModel]:
        response_schema = response_model.model_json_schema()
        request_body = {
            "model": requested_model,
            "messages": messages,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": self._schema_name(response_model.__name__),
                    "strict": True,
                    "schema": response_schema,
                },
            },
        }
        response_mode = "json_schema"

        last_error: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                response = await self._client.post(
                    "chat/completions",
                    headers=self._headers(),
                    json=request_body,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise OpenRouterResponseError(
                        f"OpenRouter transient HTTP {response.status_code}"
                    )
                if response.status_code >= 400:
                    body = response.text[:500].replace("\n", " ")
                    if (
                        response.status_code in {400, 404, 422}
                        and response_mode != "plain_json"
                    ):
                        # The free router may select a model/provider that does
                        # not implement strict JSON Schema. Keep DTO validation
                        # authoritative while progressively relaxing only the
                        # transport hint.
                        if response_mode == "json_schema":
                            request_body["response_format"] = {
                                "type": "json_object"
                            }
                            response_mode = "json_object"
                        else:
                            request_body.pop("response_format", None)
                            schema_instruction = {
                                "role": "system",
                                "content": (
                                    "Верни только JSON, совместимый со схемой: "
                                    + json.dumps(
                                        response_schema,
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    )
                                ),
                            }
                            request_body["messages"] = [
                                schema_instruction,
                                *request_body["messages"],
                            ]
                            response_mode = "plain_json"
                        raise OpenRouterResponseError(
                            "Selected OpenRouter endpoint rejected structured "
                            f"output mode: HTTP {response.status_code}"
                        )
                    raise OpenRouterError(
                        f"OpenRouter HTTP {response.status_code}: {body}"
                    )
                body = response.json()
                content = self._message_content(body)
                structured = response_model.model_validate(
                    self._decode_json_content(content)
                )
                actual_model = str(body.get("model") or requested_model)
                usage = self._usage(body.get("usage"))
                return StructuredLLMResultDTO[response_model](  # type: ignore[valid-type]
                    data=structured,
                    provider=str(body.get("provider") or "openrouter"),
                    requested_model=requested_model,
                    actual_model=actual_model,
                    response_id=body.get("id"),
                    usage=usage,
                    attempt_count=attempt,
                )
            except OpenRouterError as exc:
                last_error = exc
                # Authentication and other explicit 4xx errors are raised
                # directly above and must not be retried.
                if not isinstance(exc, OpenRouterResponseError):
                    raise
            except (
                httpx.TransportError,
                json.JSONDecodeError,
                ValidationError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc

            if attempt < self.config.max_attempts:
                await self._sleep(self._backoff(attempt))

        raise OpenRouterResponseError(
            f"OpenRouter did not return valid structured JSON after "
            f"{self.config.max_attempts} attempts: {last_error}"
        ) from last_error

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.config.app_title,
        }
        if self.config.http_referer:
            headers["HTTP-Referer"] = self.config.http_referer
        return headers

    def _backoff(self, attempt: int) -> float:
        index = min(attempt - 1, len(self.config.retry_backoff_seconds) - 1)
        if index < 0:
            return 0
        return self.config.retry_backoff_seconds[index]

    @staticmethod
    def _data_url(image_bytes: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _schema_name(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", name)[:64] or "response"

    @staticmethod
    def _message_content(body: dict[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterResponseError("OpenRouter response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise OpenRouterResponseError("OpenRouter response has no message")
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if texts:
                return "".join(texts)
        raise OpenRouterResponseError("OpenRouter response content is not text")

    @staticmethod
    def _decode_json_content(content: str) -> Any:
        stripped = content.strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.I
        )
        if fenced:
            stripped = fenced.group(1)
        return json.loads(stripped)

    @staticmethod
    def _usage(raw: Any) -> LLMUsageDTO | None:
        if not isinstance(raw, dict):
            return None
        return LLMUsageDTO(
            prompt_tokens=raw.get("prompt_tokens"),
            completion_tokens=raw.get("completion_tokens"),
            total_tokens=raw.get("total_tokens"),
        )
