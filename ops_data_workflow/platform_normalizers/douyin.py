"""Douyin identity normalization with ad-id demotion."""

from __future__ import annotations

import re

import pandas as pd

from .common import PlatformIdentity, append_reason, first_text, normalize_url, strip_title_noise, text_value


SUSPECT_TITLE_PATTERNS = [
    re.compile(r"推送|客供|混剪|脚本|制作|实拍|口播|剪辑|跑量|二剪|素材"),
    re.compile(r"(?:^|[【_\-\s])[AB]组[-_]?|A/B|AB测试", re.I),
    re.compile(r"\.(?:mp4|mov|m4v|avi)\b|(?:^|[_\-\s])(?:va|vb|vd)\d", re.I),
    re.compile(r"素材编号|计划|单元|广告组|巨量|千川"),
]


def normalize_douyin_row(row: pd.Series) -> dict[str, str]:
    original_title = text_value(row.get("original_title", "")) or text_value(row.get("title", ""))
    raw_url = first_text(row, ["work_url", "content_url", "title", "material_id", "content_id"])
    extracted_url = normalize_url(raw_url)
    is_douyin_url = _is_douyin_url(extracted_url)
    item_id = extract_douyin_item_id(extracted_url) if is_douyin_url else ""
    material_id = first_text(row, ["ad_material_id", "material_id", "content_id"])
    standard_title = strip_title_noise(original_title)
    suspect_reason = _suspect_title_reason(original_title, standard_title)
    url_reason = "抖音URL解析失败" if is_douyin_url and not item_id else ""
    status = "ok" if item_id or (standard_title and not suspect_reason) else "missing_identity"
    if url_reason:
        status = "pending_enrichment"
    reason = append_reason(url_reason, suspect_reason)
    return PlatformIdentity(
        platform="抖音",
        original_title=original_title,
        standard_title=standard_title,
        work_url=_normalize_douyin_work_url(item_id) if item_id else (extracted_url if is_douyin_url else ""),
        work_id=item_id,
        ad_material_id=material_id,
        normalization_status=status,
        normalization_reason=reason,
    ).as_dict()


def extract_douyin_item_id(value: object) -> str:
    text = text_value(value)
    if not text:
        return ""
    if re.fullmatch(r"\d{10,24}", text):
        return text
    match = re.search(r"/(?:video|note)/(\d{10,24})", text)
    if match:
        return match.group(1)
    match = re.search(r"(?:aweme_id|item_id|modal_id)=(\d{10,24})", text)
    if match:
        return match.group(1)
    return ""


def _normalize_douyin_work_url(item_id: str) -> str:
    return f"https://www.douyin.com/video/{item_id}" if item_id else ""


def _is_douyin_url(value: str) -> bool:
    lowered = str(value or "").lower()
    return "douyin.com" in lowered or "iesdouyin.com" in lowered


def _suspect_title_reason(original_title: str, standard_title: str) -> str:
    text = original_title or ""
    if not standard_title:
        return "抖音标题非真实作品标题"
    for pattern in SUSPECT_TITLE_PATTERNS:
        if pattern.search(text):
            return "抖音标题非真实作品标题"
    if len(standard_title) <= 4 and re.search(r"\d", text):
        return "抖音标题非真实作品标题"
    return ""
