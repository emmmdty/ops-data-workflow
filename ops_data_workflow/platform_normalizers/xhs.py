"""Xiaohongshu work identity normalization."""

from __future__ import annotations

import re

import pandas as pd

from .common import PlatformIdentity, first_text, normalize_url, strip_title_noise, text_value


def normalize_xhs_row(row: pd.Series) -> dict[str, str]:
    original_title = text_value(row.get("original_title", "")) or text_value(row.get("title", ""))
    raw_identity = first_text(row, ["work_id", "content_id", "content_id_fallback", "material_id", "content_url", "title"])
    url = normalize_url(first_text(row, ["work_url", "content_url", "material_id", "content_id", "title"]))
    note_id = extract_xhs_id(raw_identity) or extract_xhs_id(url)
    work_url = normalize_xhs_url(note_id) if note_id else (url if "xiaohongshu.com" in url else "")
    status = "ok" if note_id else "missing_identity"
    reason = "" if note_id else "缺少作品ID或链接"
    return PlatformIdentity(
        platform="小红书",
        original_title=original_title,
        standard_title=strip_title_noise(original_title),
        work_url=work_url,
        work_id=note_id,
        ad_material_id=text_value(row.get("material_id", "")),
        normalization_status=status,
        normalization_reason=reason,
    ).as_dict()


def extract_xhs_id(value: object) -> str:
    text = text_value(value)
    if not text:
        return ""
    match = re.search(r"/(?:explore|discovery/item|item)/([^?/#\s]+)", text)
    if match:
        return _clean_id(match.group(1))
    if re.fullmatch(r"[0-9a-fA-F]{12,32}", text):
        return text
    return ""


def normalize_xhs_url(note_id: str) -> str:
    clean = _clean_id(note_id)
    return f"https://www.xiaohongshu.com/explore/{clean}" if clean else ""


def _clean_id(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "", str(value or "").strip())
