"""Infer canonical channel names from uploaded raw file paths."""

from __future__ import annotations

from pathlib import Path

from .channel_profiles import ChannelProfileConfig, compact_source_name, load_channel_profiles


SOCIAL_PLATFORM_GROUP = "微信"
SOCIAL_MARKET_CHANNEL = "微信市场部"
SOCIAL_COMMERCIAL_CHANNEL = "微信商业化"
SOCIAL_PLATFORM_KEYWORDS = ("视频号", "微信", "腾讯")
OTHER_PLATFORM = "其他"


def infer_channel_from_path(
    path: str | Path,
    *,
    config_path: str | Path | None = None,
    profiles: ChannelProfileConfig | None = None,
) -> str:
    """Best-effort channel key used for same-period file replacement."""
    name = Path(str(path)).stem
    compact = _compact_source_name(name)
    explicit = _explicit_business_channel_from_compact_name(compact)
    if explicit:
        return explicit
    configured = (profiles or load_channel_profiles(config_path)).infer_channel_from_name(compact)
    if configured:
        return configured
    legacy_channel = _legacy_channel_from_compact_name(compact)
    if legacy_channel:
        return legacy_channel
    return name


def normalize_channel_name(value: object, *, profiles: ChannelProfileConfig | None = None) -> str:
    text = "" if value is None else str(value).strip()
    explicit = _explicit_business_channel_from_compact_name(_compact_source_name(text))
    if explicit:
        return explicit
    configured = (profiles or load_channel_profiles()).infer_channel_from_name(text)
    if configured:
        return configured
    return _legacy_channel_from_compact_name(_compact_source_name(text)) or text


def social_channel_from_name(value: object, *, profiles: ChannelProfileConfig | None = None) -> str:
    compact = _compact_source_name(value)
    explicit = _explicit_business_channel_from_compact_name(compact)
    if explicit in {SOCIAL_MARKET_CHANNEL, SOCIAL_COMMERCIAL_CHANNEL}:
        return explicit
    configured = (profiles or load_channel_profiles()).infer_channel_from_name(compact)
    if configured in {SOCIAL_MARKET_CHANNEL, SOCIAL_COMMERCIAL_CHANNEL}:
        return configured
    legacy = _legacy_social_channel_from_name(compact)
    return legacy


def social_platform_from_name(value: object, *, profiles: ChannelProfileConfig | None = None) -> str:
    compact = _compact_source_name(value)
    platform = platform_from_channel_or_name(compact, profiles=profiles, default="")
    if platform:
        return platform
    return ""


def platform_from_channel_or_name(
    value: object,
    *,
    profiles: ChannelProfileConfig | None = None,
    default: str = OTHER_PLATFORM,
) -> str:
    compact = _compact_source_name(value)
    if any(token in compact for token in SOCIAL_PLATFORM_KEYWORDS):
        return SOCIAL_PLATFORM_GROUP
    if "B站" in compact or "bilibili" in compact.lower() or "哔哩哔哩" in compact:
        return "B站"
    if "小红书" in compact:
        return "小红书"
    if "抖音" in compact:
        return "抖音"
    configured_channel = (profiles or load_channel_profiles()).infer_channel_from_name(compact)
    profile = (profiles or load_channel_profiles()).profile_for_channel(configured_channel)
    if profile is not None:
        return profile.platform
    return default


def _compact_source_name(value: object) -> str:
    return compact_source_name(value)


def _legacy_channel_from_compact_name(compact: str) -> str:
    explicit = _explicit_business_channel_from_compact_name(compact)
    if explicit:
        return explicit
    social_channel = _legacy_social_channel_from_name(compact)
    if social_channel:
        return social_channel
    if "B站" in compact or "bilibili" in compact.lower():
        return "B站市场部"
    if "小红书" in compact:
        return "小红书市场部" if "市场部" in compact else "小红书商业化"
    if "抖音" in compact:
        if "市场部" in compact:
            return "抖音市场部"
        return "抖音商业化"
    return ""


def _legacy_override_channel_from_compact_name(compact: str) -> str:
    return ""


def _legacy_social_channel_from_name(value: object) -> str:
    compact = _compact_source_name(value)
    if not any(platform in compact for platform in SOCIAL_PLATFORM_KEYWORDS):
        return ""
    if _is_commercial_name(compact):
        return SOCIAL_COMMERCIAL_CHANNEL
    return SOCIAL_MARKET_CHANNEL


def _explicit_business_channel_from_compact_name(compact: str) -> str:
    if any(platform in compact for platform in SOCIAL_PLATFORM_KEYWORDS):
        return SOCIAL_COMMERCIAL_CHANNEL if _is_commercial_name(compact) else SOCIAL_MARKET_CHANNEL
    if "B站" in compact or "bilibili" in compact.lower() or "哔哩哔哩" in compact:
        return "B站商业化" if _is_commercial_name(compact) else "B站市场部"
    if "抖音" in compact:
        return "抖音市场部" if "市场部" in compact else "抖音商业化"
    return ""


def _is_commercial_name(compact: str) -> bool:
    return "商业化" in compact or "商增" in compact
