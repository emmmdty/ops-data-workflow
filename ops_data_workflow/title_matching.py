"""Shared title normalization for historical content matching."""

from __future__ import annotations

import re
import unicodedata


INVALID_TITLE_KEY_VALUES = {"", "nan", "none", "null", "nat", "<na>"}


def normalized_title_key(value: object) -> str:
    """Return a stable key for matching the same title with different tags."""
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(r"https?://\S+", "", text)
    text = re.split(r"[#＃]", text, maxsplit=1)[0]
    text = re.sub(r"复制此链接.*$", "", text, flags=re.I)
    text = re.sub(r"打开Dou音搜索.*$", "", text, flags=re.I)
    text = re.sub(r"打开抖音搜索.*$", "", text)
    text = "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return text.lower()


def extract_historical_title(value: object) -> str:
    """Extract the human title from copied post text in the history workbook."""
    text = _clean_text(value)
    if not text:
        return ""

    xhs_match = re.search(r"【(.+?)(?:\s+-\s+[^】|]+)?\s*\|\s*小红书", text)
    if xhs_match:
        return _squash_spaces(xhs_match.group(1))

    text = re.sub(r"https?://\S+.*$", "", text).strip()
    text = re.sub(r"复制此链接.*$", "", text, flags=re.I).strip()
    if ":/" in text:
        text = text.rsplit(":/", 1)[-1]
    for _ in range(4):
        text = re.sub(r"^\s*\d+(?:\.\d+)?\s*", "", text)
        text = re.sub(r"^\s*[A-Za-z]@[A-Za-z.]+\s*", "", text)
        text = re.sub(r"^\s*\d{1,2}/\d{1,2}\s*", "", text)
        text = re.sub(r"^\s*:?\d{1,2}\s*(?:am|pm)\s*", "", text, flags=re.I)
        text = re.sub(r"^\s*[A-Za-z]{1,8}\s*", "", text)
    return _squash_spaces(text)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in INVALID_TITLE_KEY_VALUES:
        return ""
    return unicodedata.normalize("NFKC", text)


def _squash_spaces(value: str) -> str:
    return " ".join(str(value or "").split()).strip()
