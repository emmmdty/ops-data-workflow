"""Infer canonical channel names from uploaded raw file paths."""

from __future__ import annotations

from pathlib import Path


SOCIAL_PLATFORM_GROUP = "微信"
SOCIAL_MARKET_CHANNEL = "微信市场部"
SOCIAL_COMMERCIAL_CHANNEL = "微信商业化"
SOCIAL_PLATFORM_KEYWORDS = ("视频号", "微信", "腾讯")


def infer_channel_from_path(path: str | Path) -> str:
    """Best-effort channel key used for same-period file replacement."""
    name = Path(str(path)).stem
    compact = _compact_source_name(name)
    social_channel = social_channel_from_name(compact)
    if social_channel:
        return social_channel
    if "B站" in compact or "bilibili" in compact.lower():
        return "B站"
    if "小红书" in compact:
        return "小红书市场部" if "市场部" in compact else "小红书商业化"
    if "抖音" in compact:
        if "达人" in compact:
            return "达人数据"
        if "市场部" in compact:
            return "抖音市场部"
        if "期货" in compact:
            return "抖音期货通"
        return "抖音商业化"
    return name


def normalize_channel_name(value: object) -> str:
    text = "" if value is None else str(value).strip()
    social_channel = social_channel_from_name(text)
    return social_channel or text


def social_channel_from_name(value: object) -> str:
    compact = _compact_source_name(value)
    if not social_platform_from_name(compact):
        return ""
    if "商业化" in compact:
        return SOCIAL_COMMERCIAL_CHANNEL
    return SOCIAL_MARKET_CHANNEL


def social_platform_from_name(value: object) -> str:
    compact = _compact_source_name(value)
    for platform in SOCIAL_PLATFORM_KEYWORDS:
        if platform in compact:
            return platform
    return ""


def _compact_source_name(value: object) -> str:
    return (
        str(value or "")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .strip()
    )
