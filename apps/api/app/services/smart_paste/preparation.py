"""Conservative text preparation for Smart Paste."""

from __future__ import annotations

import unicodedata


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def prepare_paste_text(original: str) -> tuple[str, list[str]]:
    """Return cleaned working text and warnings."""
    warnings: list[str] = []
    text = normalize_unicode(original)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    # Remove exact duplicate consecutive lines
    deduped: list[str] = []
    for ln in lines:
        if not deduped or deduped[-1] != ln:
            deduped.append(ln)

    if len(deduped) < len(lines):
        warnings.append(f"Removed {len(lines) - len(deduped)} duplicate lines")

    cleaned = "\n".join(deduped)

    if len(cleaned) > 50000:
        cleaned = cleaned[:50000]
        warnings.append("Text truncated to 50,000 characters for processing")

    if len(cleaned) < 30:
        warnings.append("Paste is very short — extraction may be incomplete")

    return cleaned, warnings
