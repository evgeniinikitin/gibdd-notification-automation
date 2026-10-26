"""Pure helpers used by the asynchronous receipt download worker."""

from __future__ import annotations

import io
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from pypdf import PdfReader

from .exceptions import ReceiptParseError
from .schemas import ReceiptParseResultDTO


class ReceiptWorkerHelper:
    WATCH_URL = (
        "http://xn--80aebkobnwfcnsfk1e0h.xn--p1ai/"
        "transportation/watch?token={token}&ticket=1"
    )
    SYSTEM_ID_PATTERNS = (
        re.compile(
            r"Системный\s+идентификатор\s*(?:\n|\s)*"
            r"уведомления\s*(?:\n|\s)*([0-9]{4,})",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"уведомления\s*(?:№|N)?\s*([0-9]{6,})",
            flags=re.IGNORECASE,
        ),
    )

    @classmethod
    def build_receipt_url(cls, token: str) -> str:
        normalized = token.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ReceiptParseError("Submission token is empty or contains whitespace")
        return cls.WATCH_URL.format(token=quote(normalized, safe=""))

    @staticmethod
    def token_from_url(value: str) -> str | None:
        query = parse_qs(urlparse(value.strip()).query)
        tokens = query.get("token")
        if not tokens or not tokens[0].strip():
            return None
        return tokens[0].strip()

    @classmethod
    def extract_system_id(cls, text: str) -> str | None:
        normalized = text.replace("\u00a0", " ")
        for pattern in cls.SYSTEM_ID_PATTERNS:
            match = pattern.search(normalized)
            if match:
                return match.group(1)
        return None

    @classmethod
    def parse_pdf(
        cls, source: bytes | Path
    ) -> ReceiptParseResultDTO:
        raw: bytes
        if isinstance(source, Path):
            if not source.is_file():
                raise ReceiptParseError(f"Receipt PDF not found: {source}")
            raw = source.read_bytes()
        else:
            raw = source
        if not raw.startswith(b"%PDF"):
            raise ReceiptParseError("Receipt response does not have a PDF signature")
        try:
            reader = PdfReader(io.BytesIO(raw), strict=False)
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise ReceiptParseError(f"Could not read receipt PDF: {exc}") from exc
        system_id = cls.extract_system_id(text)
        return ReceiptParseResultDTO(
            system_id=system_id,
            text=text,
            page_count=len(reader.pages),
            needs_review=system_id is None,
        )

    @staticmethod
    def receipt_filename(system_id: str, trip_label: str) -> str:
        if not re.fullmatch(r"\d{4,}", system_id.strip()):
            raise ReceiptParseError("System ID must contain at least four digits")
        label = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", trip_label)
        label = re.sub(r"\s+", " ", label).strip(" .")
        if not label:
            raise ReceiptParseError("Trip label is empty after sanitization")
        return f"{system_id.strip()} {label[:120]}.pdf"
