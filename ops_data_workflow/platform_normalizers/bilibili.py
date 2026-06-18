"""Bilibili work identity normalization."""

from __future__ import annotations

import re

import pandas as pd

from .common import PlatformIdentity, first_text, normalize_url, strip_title_noise, text_value


def normalize_bilibili_row(row: pd.Series) -> dict[str, str]:
    original_title = text_value(row.get("original_title", "")) or text_value(row.get("title", ""))
    url = normalize_url(first_text(row, ["work_url", "content_url", "title"]))
    url_bvid = extract_bvid(url)
    raw_identity = first_text(row, ["work_id", "content_id", "content_id_fallback", "material_id"])
    bvid = url_bvid or extract_bvid(raw_identity) or extract_bvid(row.to_string())
    work_url = normalize_bilibili_url(url_bvid or bvid) if (url_bvid or bvid) else url
    status = "ok" if bvid else "missing_identity"
    reason = "" if bvid else "缺少作品ID或链接"
    return PlatformIdentity(
        platform="B站",
        original_title=original_title,
        standard_title=strip_title_noise(original_title),
        work_url=work_url,
        work_id=bvid,
        ad_material_id=text_value(row.get("material_id", "")),
        normalization_status=status,
        normalization_reason=reason,
    ).as_dict()


def extract_bvid(value: object) -> str:
    text = text_value(value)
    match = re.search(r"(BV[0-9A-Za-z]{6,})", text)
    return match.group(1) if match else ""


def normalize_bilibili_url(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}/" if bvid else ""
