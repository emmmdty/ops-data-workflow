"""Shared helpers for platform identity normalization."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse, urlunparse

import pandas as pd

from ops_data_workflow.title_matching import clean_douyin_share_title


NORMALIZED_IDENTITY_COLUMNS = [
    "original_title",
    "standard_title",
    "work_url",
    "work_id",
    "ad_material_id",
    "ad_material_url",
    "ad_cover_url",
    "normalization_status",
    "normalization_reason",
]


@dataclass(frozen=True)
class PlatformIdentity:
    platform: str
    original_title: str = ""
    standard_title: str = ""
    work_url: str = ""
    work_id: str = ""
    ad_material_id: str = ""
    ad_material_url: str = ""
    ad_cover_url: str = ""
    normalization_status: str = ""
    normalization_reason: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "platform": self.platform,
            "original_title": self.original_title,
            "standard_title": self.standard_title,
            "work_url": self.work_url,
            "work_id": self.work_id,
            "ad_material_id": self.ad_material_id,
            "ad_material_url": self.ad_material_url,
            "ad_cover_url": self.ad_cover_url,
            "normalization_status": self.normalization_status,
            "normalization_reason": self.normalization_reason,
        }


def platform_label(row: pd.Series | object) -> str:
    if isinstance(row, pd.Series):
        text = " ".join(text_value(row.get(column, "")) for column in ["platform", "platform_group", "channel"])
    else:
        text = text_value(row)
    lowered = text.lower()
    if "抖音" in text or "douyin" in lowered:
        return "抖音"
    if "小红书" in text or "xiaohongshu" in lowered or "xhs" in lowered:
        return "小红书"
    if "B站" in text or "哔哩" in text or "bilibili" in lowered:
        return "B站"
    return text_value(row.get("platform", "")) if isinstance(row, pd.Series) else ""


def text_value(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.endswith(".0") and re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return "" if text.lower() in {"", "nan", "none", "null", "<na>", "nat"} else text


def first_text(row: pd.Series, columns: list[str]) -> str:
    for column in columns:
        value = text_value(row.get(column, ""))
        if value:
            return value
    return ""


def normalize_url(value: object) -> str:
    text = text_value(value)
    if not text:
        return ""
    match = re.search(r"https?://[^\s\])）>，,]+", text)
    if match:
        text = match.group(0)
    parsed = urlparse(text)
    if not parsed.netloc:
        return text.rstrip("/")
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def strip_title_noise(value: object) -> str:
    text = clean_douyin_share_title(text_value(value))
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"复制此链接.*$", " ", text, flags=re.I)
    text = re.sub(r"打开Dou音搜索.*$", " ", text, flags=re.I)
    text = re.sub(r"打开抖音搜索.*$", " ", text)
    text = re.split(r"[#＃]", text, maxsplit=1)[0]
    return " ".join(text.split()).strip()


def append_reason(*reasons: str) -> str:
    values: list[str] = []
    for reason in reasons:
        for part in re.split(r"[;；]", str(reason or "")):
            text = part.strip()
            if text and text not in values:
                values.append(text)
    return "；".join(values)
