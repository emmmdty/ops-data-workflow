"""Config-backed channel profiles for source detection and field aliases."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import pandas as pd
import yaml


DEFAULT_CHANNEL_PROFILES_PATH = Path(__file__).resolve().parents[1] / "config" / "channel_profiles.yml"
REQUIRED_PROFILE_KEYS = frozenset(
    {
        "channel",
        "platform",
        "group",
        "filename_keywords",
        "field_aliases",
        "account_filter_enabled",
        "active",
    }
)


@dataclass(frozen=True)
class ChannelProfile:
    channel: str
    platform: str
    group: str
    filename_keywords: tuple[str, ...]
    field_aliases: Mapping[str, tuple[str, ...]]
    account_filter_enabled: bool
    active: bool

    def matches_name(self, value: object) -> bool:
        return self.matching_keyword_length(value) > 0

    def matching_keyword_length(self, value: object) -> int:
        compact = compact_source_name(value)
        lower = compact.lower()
        match_length = 0
        for keyword in self.filename_keywords:
            compact_keyword = compact_source_name(keyword)
            if compact_keyword and (compact_keyword in compact or compact_keyword.lower() in lower):
                match_length = max(match_length, len(compact_keyword))
        return match_length


@dataclass(frozen=True)
class ChannelProfileConfig:
    profiles: tuple[ChannelProfile, ...]

    def active_profiles(self) -> list[ChannelProfile]:
        return [profile for profile in self.profiles if profile.active]

    def profile_for_channel(self, channel: object) -> ChannelProfile | None:
        text = str(channel or "").strip()
        for profile in self.profiles:
            if profile.channel == text:
                return profile
        return None

    def field_aliases_for_channel(self, channel: object) -> dict[str, list[str]]:
        profile = self.profile_for_channel(channel)
        if profile is None:
            return {}
        return {field: list(aliases) for field, aliases in profile.field_aliases.items()}

    def infer_channel_from_name(self, value: object) -> str:
        best_profile: ChannelProfile | None = None
        best_length = 0
        for profile in self.active_profiles():
            match_length = profile.matching_keyword_length(value)
            if match_length > best_length:
                best_profile = profile
                best_length = match_length
        return best_profile.channel if best_profile else ""

    def platform_from_name(self, value: object) -> str:
        compact = compact_source_name(value)
        lower = compact.lower()
        for profile in self.active_profiles():
            platform = compact_source_name(profile.platform)
            group = compact_source_name(profile.group)
            if platform and (platform in compact or platform.lower() in lower):
                return profile.platform
            if group and (group in compact or group.lower() in lower):
                return profile.platform
            if profile.matches_name(value):
                return profile.platform
        return ""

    def to_frame(self) -> pd.DataFrame:
        return render_channel_profiles_table(self)


def load_channel_profiles(path: Path | str | None = None) -> ChannelProfileConfig:
    config_path = Path(path) if path is not None else DEFAULT_CHANNEL_PROFILES_PATH
    return _load_channel_profiles_cached(str(config_path))


@lru_cache(maxsize=16)
def _load_channel_profiles_cached(path_text: str) -> ChannelProfileConfig:
    config_path = Path(path_text)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw_profiles = data.get("channels", [])
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("channel_profiles.yml must define a non-empty channels list")
    profiles = tuple(_profile_from_config(index, item) for index, item in enumerate(raw_profiles, start=1))
    channels = [profile.channel for profile in profiles]
    if len(channels) != len(set(channels)):
        raise ValueError("channel_profiles.yml contains duplicate channel names")
    return ChannelProfileConfig(profiles=profiles)


def render_channel_profiles_table(profiles: ChannelProfileConfig | None = None) -> pd.DataFrame:
    config = profiles or load_channel_profiles()
    rows = []
    for profile in config.profiles:
        rows.append(
            {
                "渠道": profile.channel,
                "平台": profile.platform,
                "文件名关键词": "、".join(profile.filename_keywords),
                "字段别名": _format_field_aliases(profile.field_aliases),
                "启用状态": "启用" if profile.active else "停用",
            }
        )
    return pd.DataFrame(rows, columns=["渠道", "平台", "文件名关键词", "字段别名", "启用状态"])


def compact_source_name(value: object) -> str:
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


def _profile_from_config(index: int, item: object) -> ChannelProfile:
    if not isinstance(item, dict):
        raise ValueError(f"channel_profiles.yml channel #{index} must be a mapping")
    missing = sorted(REQUIRED_PROFILE_KEYS - set(item))
    if missing:
        raise ValueError(f"channel_profiles.yml channel #{index} missing keys: {', '.join(missing)}")
    channel = _required_text(item["channel"], f"channel #{index} channel")
    platform = _required_text(item["platform"], f"{channel} platform")
    group = _required_text(item["group"], f"{channel} group")
    filename_keywords = tuple(_text_items(item["filename_keywords"], f"{channel} filename_keywords"))
    if not filename_keywords:
        raise ValueError(f"{channel} filename_keywords must not be empty")
    field_aliases = _field_aliases(item["field_aliases"], channel)
    return ChannelProfile(
        channel=channel,
        platform=platform,
        group=group,
        filename_keywords=filename_keywords,
        field_aliases=field_aliases,
        account_filter_enabled=bool(item["account_filter_enabled"]),
        active=bool(item["active"]),
    )


def _required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"channel_profiles.yml {label} must not be empty")
    return text


def _text_items(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"channel_profiles.yml {label} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _field_aliases(value: object, channel: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ValueError(f"channel_profiles.yml {channel} field_aliases must be a mapping")
    aliases: dict[str, tuple[str, ...]] = {}
    for field, raw_aliases in value.items():
        field_name = str(field or "").strip()
        if not field_name:
            continue
        aliases[field_name] = tuple(_text_items(raw_aliases or [], f"{channel} field_aliases.{field_name}"))
    return aliases


def _format_field_aliases(field_aliases: Mapping[str, tuple[str, ...]]) -> str:
    parts = []
    for field, aliases in field_aliases.items():
        if aliases:
            parts.append(f"{field}: {'/'.join(aliases)}")
    return "；".join(parts)
