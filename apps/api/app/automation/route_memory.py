"""Conservative enrichment from routes verified in historical notifications."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from .extraction import TripExtractionDTO


def _normal(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"\bж\s*/\s*д\b", "железнодорожный", value)
    value = re.sub(r"\bжелезнодорожн\w*\s+вокзал\w*\b", "вокзал", value)
    value = re.sub(r"\bвокзальн\w*\b", "вокзал", value)
    value = re.sub(r"[\u00ab\u00bb\"'()]", " ", value)
    value = re.sub(
        r"\b(?:россия|рф|город|г|пос(?:елок)?|д(?:еревня)?|"
        r"ленинградская\s+область|санкт[ -]петербург|спб)\b\.?,?",
        " ",
        value,
    )
    value = re.sub(
        r"\b(?:улица|ул|проспект|пр|шоссе|ш|переулок|пер)\b\.?,?",
        " ",
        value,
    )
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    value = re.sub(r"(?<=\d)(?=[a-zа-я])|(?<=[a-zа-я])(?=\d)", " ", value)
    return " ".join(value.split())


def _score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens, right_tokens = set(left.split()), set(right.split())
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    overlap = len(left_tokens & right_tokens) / min(
        len(left_tokens), len(right_tokens)
    )
    return max(jaccard, overlap, SequenceMatcher(None, left, right).ratio())


@lru_cache(maxsize=1)
def _records() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("data") / "route_memory.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _looks_roundtrip(extraction: TripExtractionDTO) -> bool:
    route = extraction.route
    if route is None:
        return False
    text = f"{route.text or ''}\n{route.roads or ''}"
    if route.address_start and route.destination and _score(
        _normal(route.address_start), _normal(route.destination)
    ) >= 0.9:
        return True
    if re.search(
        r"\bи\s+обратно\b|\bобратно\s*[-–—:]?\s*уточн",
        text,
        re.IGNORECASE,
    ):
        return True
    if route.stops and route.address_start:
        return _score(
            _normal(route.stops[-1].address),
            _normal(route.address_start),
        ) >= 0.9
    return False


def enrich_extracted_route_from_memory(
    extraction: TripExtractionDTO,
    *,
    threshold: float = 0.82,
) -> dict[str, Any] | None:
    """Replace estimates only for a high-confidence historical route match."""

    route = extraction.route
    if route is None or not route.address_start or not route.destination:
        return None
    start_key = _normal(route.address_start)
    destination_key = _normal(route.destination)
    roundtrip = _looks_roundtrip(extraction)
    if roundtrip and _score(start_key, destination_key) >= 0.9:
        for stop in route.stops:
            candidate = _normal(stop.address)
            if candidate and _score(start_key, candidate) < 0.9:
                route.destination = stop.address
                destination_key = candidate
                break
    best: tuple[float, dict[str, Any]] | None = None
    for record in _records():
        if bool(record["roundtrip"]) != roundtrip:
            continue
        score = min(
            _score(start_key, record["start_key"]),
            _score(destination_key, record["destination_key"]),
        )
        if best is None or score > best[0]:
            best = (score, record)
    if best is None or best[0] < threshold:
        return None
    record = best[1]
    minutes = int(record["duration_minutes"])
    route.distance = f"{record['distance_km']} км"
    route.duration = f"{minutes // 60:02d}:{minutes % 60:02d}"
    route.roads = record["roads"]
    route.approximate = False
    return {
        "score": round(best[0], 3),
        "examples": record["examples"],
        "start": record["start"],
        "destination": record["destination"],
        "roundtrip": record["roundtrip"],
    }
