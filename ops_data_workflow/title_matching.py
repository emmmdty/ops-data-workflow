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
    text = clean_douyin_share_title(text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.split(r"[#＃]", text, maxsplit=1)[0]
    text = re.sub(r"复制此链接.*$", "", text, flags=re.I)
    text = re.sub(r"打开Dou音搜索.*$", "", text, flags=re.I)
    text = re.sub(r"打开抖音搜索.*$", "", text)
    text = "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return _dedupe_repeated_key(text.lower())


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
    text = clean_douyin_share_title(text)
    return _squash_spaces(text)


def clean_douyin_share_title(value: object) -> str:
    """Remove copy-command prefixes without stripping meaningful leading numbers."""
    text = _clean_text(value)
    if not text:
        return ""
    if ":/" in text:
        prefix, suffix = text.rsplit(":/", 1)
        if re.search(r"(?:^|\s)\d+(?:\.\d+)?\s+[A-Za-z0-9]{1,12}$", prefix.strip()):
            text = suffix.strip()

    for _ in range(4):
        previous = text
        text = re.sub(
            r"^\s*/?\d{1,3}(?:\.\d+)?\s+[A-Za-z0-9]{1,12}[@.][A-Za-z0-9.]+\s*(?::?\s*\d{1,2}\s*(?:am|pm)?)?\s*",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"^\s*@?[A-Za-z][A-Za-z0-9]{0,11}[@.][A-Za-z0-9.]+\s*",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(r"^\s*[:：]?\d{1,2}\s*(?:am|pm)\s*", "", text, flags=re.I)
        if text == previous:
            break
    return _dedupe_repeated_phrase(_squash_spaces(text))


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in INVALID_TITLE_KEY_VALUES:
        return ""
    return unicodedata.normalize("NFKC", text)


def _squash_spaces(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe_repeated_phrase(value: str) -> str:
    text = _squash_spaces(value)
    if not text:
        return ""
    parts = text.split()
    if len(parts) % 2 == 0 and len(parts) >= 2:
        half = len(parts) // 2
        if parts[:half] == parts[half:]:
            return " ".join(parts[:half])
    return text


def _dedupe_repeated_key(value: str) -> str:
    key = str(value or "")
    if len(key) < 12:
        return key
    for unit_size in range(6, (len(key) // 2) + 1):
        if len(key) % unit_size:
            continue
        unit = key[:unit_size]
        if unit and unit * (len(key) // unit_size) == key:
            return unit
    return key
