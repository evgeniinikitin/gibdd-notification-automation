"""Direct Gemini multimodal provider for native PDF trip extraction."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from .exceptions import GeminiError, GeminiResponseError
from .extraction import (
    SYSTEM_PROMPT,
    TripExtractionContextDTO,
    TripExtractionDTO,
    TripExtractionProvider,
)
from .schemas import (
    ImageOCRDTO,
    LLMUsageDTO,
    StrictDTO,
    StructuredLLMResultDTO,
)


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
SleepCallable = Callable[[float], Awaitable[None]]


class GeminiConfig(StrictDTO):
    api_key: str = Field(min_length=1)
    api_keys: tuple[str, ...] = ()
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    model: str = "gemini-3.5-flash-lite"
    proxy_url: str | None = None
    timeout_seconds: float = Field(default=90, gt=0)
    max_attempts: int = Field(default=2, ge=1, le=5)
    retry_backoff_seconds: tuple[float, ...] = (1.0, 3.0)

    def resolved_api_keys(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                key.strip()
                for key in (self.api_key, *self.api_keys)
                if key.strip()
            )
        )


class GeminiProvider:
    """Gemini client with native PDF input and strict DTO validation."""

    supports_native_pdf = True

    def __init__(
        self,
        config: GeminiConfig,
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
            timeout=httpx.Timeout(config.timeout_seconds),
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def extract_trip(
        self,
        context: TripExtractionContextDTO,
    ) -> StructuredLLMResultDTO[TripExtractionDTO]:
        document_parts: list[dict[str, Any]] = []
        document_manifest: list[dict[str, Any]] = []
        for document in context.documents:
            document_manifest.append(
                {
                    "file_id": document.file_id,
                    "name": document.name,
                    "media_type": document.media_type,
                    "text": document.text,
                    "warnings": document.warnings,
                }
            )
            if document.inline_data_base64:
                document_parts.extend(
                    [
                        {"text": f"Документ: {document.name}"},
                        {
                            "inlineData": {
                                "mimeType": document.media_type,
                                "data": document.inline_data_base64,
                            }
                        },
                    ]
                )

        prompt_context = {
            "current_fields": context.current_fields,
            "carrier_profile_identity": context.carrier_profile_identity,
            "latest_user_message": context.latest_user_message,
            "recent_user_messages": context.recent_user_messages,
            "documents": document_manifest,
        }
        parts = [
            *document_parts,
            {
                "text": (
                    "Извлеки данные из приложенных документов и контекста. "
                    "Если PDF приложен нативно, считай его визуальное содержимое "
                    "главным источником.\nКонтекст заявки:\n"
                    + json.dumps(
                        prompt_context,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            },
        ]
        result = await self._request_structured(
            parts=parts,
            response_model=TripExtractionDTO,
            system_prompt=SYSTEM_PROMPT,
        )
        document_characters = sum(len(item.text) for item in context.documents)
        has_native_visual = any(
            item.inline_data_base64
            and (
                item.media_type == "application/pdf"
                or item.media_type.startswith("image/")
            )
            for item in context.documents
        )
        if (
            not context.current_fields
            and (document_characters >= 200 or has_native_visual)
            and self._trip_extraction_score(result.data) <= 1
        ):
            retry_parts = [*parts]
            retry_parts[-1] = {
                "text": (
                    str(parts[-1]["text"])
                    + "\n\nКОНТРОЛЬ КАЧЕСТВА: предыдущая попытка не извлекла "
                    "факты из содержательного документа. Перечитай его внимательно и "
                    "обязательно извлеки заказчика, дату, время, маршрут и людей. Не возвращай "
                    "пустую структуру, если эти данные есть в тексте."
                )
            }
            retry = await self._request_structured(
                parts=retry_parts,
                response_model=TripExtractionDTO,
                system_prompt=SYSTEM_PROMPT,
            )
            retry.attempt_count += result.attempt_count
            if self._trip_extraction_score(retry.data) > self._trip_extraction_score(
                result.data
            ):
                return retry
        return result

    @staticmethod
    def _trip_extraction_score(extraction: TripExtractionDTO) -> int:
        route = extraction.route
        return sum(
            bool(value)
            for value in (
                extraction.customer and extraction.customer.name,
                extraction.trip_date,
                extraction.start_time,
                route and route.text,
                route and route.address_start,
                route and route.destination,
                extraction.children,
                extraction.children_count,
                extraction.companions,
                extraction.companions_count,
            )
        )

    async def generate_structured_parts(
        self,
        *,
        parts: list[dict[str, Any]],
        response_model: type[StructuredModel],
        system_prompt: str | None = None,
    ) -> StructuredLLMResultDTO[StructuredModel]:
        """Generate a typed response from arbitrary multimodal parts."""

        return await self._request_structured(
            parts=parts,
            response_model=response_model,
            system_prompt=system_prompt,
        )

    async def extract_image_text(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> StructuredLLMResultDTO[ImageOCRDTO]:
        if not mime_type.startswith("image/"):
            raise GeminiError("Image MIME type must be image/*")
        return await self._request_structured(
            parts=[
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                },
                {
                    "text": (
                        "Извлеки весь читаемый текст без домыслов. Особое правило "
                        "для таблиц: восстанови каждую логическую строку таблицы "
                        "на одной строке текста, а колонки раздели символом табуляции. "
                        "Не перечисляй ячейки одного столбца вертикально. Сохрани "
                        "заголовки, даты, номера и пустые ячейки. Верни text и "
                        "confidence от 0 до 1."
                    )
                },
            ],
            response_model=ImageOCRDTO,
        )

    async def _request_structured(
        self,
        *,
        parts: list[dict[str, Any]],
        response_model: type[StructuredModel],
        system_prompt: str | None = None,
    ) -> StructuredLLMResultDTO[StructuredModel]:
        schema = response_model.model_json_schema()
        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        }
        request_body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        }
        if system_prompt:
            request_body["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        schema_enabled = True
        last_error: Exception | None = None
        api_keys = self.config.resolved_api_keys()
        # One additional attempt leaves room for Google's occasional JSON
        # Schema rejection after an earlier key had already hit its quota.
        max_attempts = max(self.config.max_attempts, len(api_keys) + 1)
        key_index = 0
        for attempt in range(1, max_attempts + 1):
            api_key = api_keys[key_index]
            try:
                response = await self._client.post(
                    f"models/{self.config.model}:generateContent",
                    headers={
                        "x-goog-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    key_index = (key_index + 1) % len(api_keys)
                    raise GeminiResponseError(
                        f"Gemini transient HTTP {response.status_code}"
                    )
                if response.status_code >= 400:
                    detail = response.text[:500].replace("\n", " ")
                    detail_lower = detail.lower()
                    if response.status_code in {400, 403} and any(
                        marker in detail_lower
                        for marker in (
                            "api_key_invalid",
                            "api key not valid",
                            "quota",
                            "resource_exhausted",
                            "rate limit",
                        )
                    ):
                        key_index = (key_index + 1) % len(api_keys)
                        raise GeminiResponseError(
                            f"Gemini key unavailable, HTTP {response.status_code}"
                        )
                    if response.status_code in {400, 422} and schema_enabled:
                        generation_config.pop("responseJsonSchema", None)
                        schema_enabled = False
                        request_body["systemInstruction"] = {
                            "parts": [
                                {
                                    "text": (
                                        (system_prompt + "\n" if system_prompt else "")
                                        + "Верни только JSON по схеме: "
                                        + json.dumps(
                                            schema,
                                            ensure_ascii=False,
                                            separators=(",", ":"),
                                        )
                                    )
                                }
                            ]
                        }
                        raise GeminiResponseError(
                            "Gemini rejected JSON Schema mode"
                        )
                    raise GeminiError(
                        f"Gemini HTTP {response.status_code}: {detail}"
                    )
                body = response.json()
                content = self._response_text(body)
                data = response_model.model_validate(
                    self._decode_json_content(content)
                )
                return StructuredLLMResultDTO[response_model](  # type: ignore[valid-type]
                    data=data,
                    provider="google-gemini",
                    requested_model=self.config.model,
                    actual_model=str(
                        body.get("modelVersion") or self.config.model
                    ),
                    response_id=body.get("responseId"),
                    usage=self._usage(body.get("usageMetadata")),
                    attempt_count=attempt,
                )
            except GeminiError as exc:
                last_error = exc
                if not isinstance(exc, GeminiResponseError):
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
            if attempt < max_attempts:
                await self._sleep(
                    self.config.retry_backoff_seconds[
                        min(
                            attempt - 1,
                            len(self.config.retry_backoff_seconds) - 1,
                        )
                    ]
                )
        raise GeminiResponseError(
            f"Gemini did not return valid structured JSON after "
            f"{max_attempts} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _response_text(body: dict[str, Any]) -> str:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiResponseError("Gemini response has no candidates")
        content = candidates[0].get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise GeminiResponseError("Gemini response has no content parts")
        text = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict)
        )
        if not text.strip():
            raise GeminiResponseError("Gemini response content is empty")
        return text

    @staticmethod
    def _decode_json_content(content: str) -> Any:
        stripped = content.strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            stripped,
            flags=re.DOTALL | re.I,
        )
        return json.loads(fenced.group(1) if fenced else stripped)

    @staticmethod
    def _usage(raw: Any) -> LLMUsageDTO | None:
        if not isinstance(raw, dict):
            return None
        return LLMUsageDTO(
            prompt_tokens=raw.get("promptTokenCount"),
            completion_tokens=raw.get("candidatesTokenCount"),
            total_tokens=raw.get("totalTokenCount"),
        )


class GeminiFallbackProvider:
    """Use direct Gemini first and a configured provider after Gemini errors."""

    supports_native_pdf = True

    def __init__(
        self,
        primary: GeminiProvider,
        fallback: TripExtractionProvider | None = None,
        fallback_image_extractor: Any | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.fallback_image_extractor = fallback_image_extractor

    async def extract_trip(
        self,
        context: TripExtractionContextDTO,
    ) -> StructuredLLMResultDTO[TripExtractionDTO]:
        try:
            return await self.primary.extract_trip(context)
        except GeminiError:
            if self.fallback is None:
                raise
            return await self.fallback.extract_trip(context)

    async def extract_image_text(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> StructuredLLMResultDTO[ImageOCRDTO]:
        try:
            return await self.primary.extract_image_text(
                image_bytes,
                mime_type,
            )
        except GeminiError:
            if self.fallback_image_extractor is None:
                raise
            return await self.fallback_image_extractor.extract_image_text(
                image_bytes,
                mime_type,
            )
