"""Platform-specific identity normalization for content recap."""

from __future__ import annotations

import pandas as pd

from .bilibili import normalize_bilibili_row
from .common import NORMALIZED_IDENTITY_COLUMNS, platform_label
from .douyin import normalize_douyin_row
from .xhs import normalize_xhs_row


def normalize_platform_identities(frame: pd.DataFrame) -> pd.DataFrame:
    """Append stable work identity fields without using ad ids as content ids."""
    normalized = frame.copy()
    for column in NORMALIZED_IDENTITY_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""

    for index, row in normalized.iterrows():
        platform = platform_label(row)
        if platform == "抖音":
            identity = normalize_douyin_row(row)
        elif platform == "小红书":
            identity = normalize_xhs_row(row)
        elif platform == "B站":
            identity = normalize_bilibili_row(row)
        else:
            identity = {
                "platform": platform,
                "original_title": _text(row.get("title", "")),
                "standard_title": _text(row.get("title", "")),
                "work_url": _text(row.get("content_url", "")),
                "work_id": "",
                "ad_material_id": _first_text(row, ["material_id", "content_id"]),
                "normalization_status": "unsupported_platform",
                "normalization_reason": "平台不在复盘范围",
            }
        for column, value in identity.items():
            normalized.at[index, column] = value
        work_id = _text(identity.get("work_id", ""))
        if platform == "抖音":
            normalized.at[index, "content_id"] = work_id
            if "material_id" in normalized.columns and _text(identity.get("ad_material_id", "")):
                normalized.at[index, "material_id"] = _text(identity.get("ad_material_id", ""))
        elif platform in {"小红书", "B站"} and work_id:
            normalized.at[index, "content_id"] = work_id
    return normalized


def _first_text(row: pd.Series, columns: list[str]) -> str:
    for column in columns:
        value = _text(row.get(column, ""))
        if value:
            return value
    return ""


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text
