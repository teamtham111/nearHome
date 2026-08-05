"""Utility helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def normalize_address(address: str) -> str:
    cleaned = re.sub(r"\s+", " ", address.strip().upper())
    return cleaned


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inputs_hash(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sqft_to_sqm(sqft: float) -> float:
    return round(sqft * 0.092903, 2)
