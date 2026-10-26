"""Conservative customer identity lookup from verified notifications."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any


def _tokens(value: object) -> set[str]:
    normalized = str(value or "").casefold().replace("ё", "е")
    return set(re.findall(r"[a-zа-я0-9]+", normalized))


def customer_name_similarity(left: object, right: object) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens) / min(
        len(left_tokens), len(right_tokens)
    )
    left_text = " ".join(sorted(left_tokens))
    right_text = " ".join(sorted(right_tokens))
    return max(overlap, SequenceMatcher(None, left_text, right_text).ratio())


@lru_cache(maxsize=1)
def _records() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("data") / "customer_memory.json"
    return json.loads(path.read_text()) if path.exists() else []


def enrich_customer_from_memory(
    customer: dict[str, Any],
    *,
    threshold: float = 0.74,
) -> dict[str, Any] | None:
    name = str(customer.get("name") or "").strip()
    inn = re.sub(r"\D", "", str(customer.get("inn") or ""))
    if not name and len(inn) not in {10, 12}:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for record in _records():
        record_inn = re.sub(r"\D", "", str(record.get("inn") or ""))
        if inn and record_inn and inn == record_inn:
            best = (1.0, record)
            break
        score = max(
            customer_name_similarity(name, alias)
            for alias in record.get("aliases") or [record["name"]]
        )
        if best is None or score > best[0]:
            best = (score, record)
    if best is None or best[0] < threshold:
        return None
    score, record = best
    customer["type"] = record.get("type") or customer.get("type")
    customer["name"] = record["name"]
    for field in ("inn", "address", "phone", "email"):
        if not customer.get(field) and record.get(field):
            customer[field] = record[field]
    return {
        "score": round(score, 3),
        "name": record["name"],
        "examples": record["examples"],
    }
