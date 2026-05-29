"""Local account filtering rules for channel-specific reporting scopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml


DEFAULT_ACCOUNT_FILTERS = {
    "platforms": {
        "xiaohongshu": {
            "display_name": "小红书",
            "match_channels": ["小红书"],
            "filter_enabled": True,
            "include_accounts": ["同花顺投资", "同顺股民社区", "同花顺理财", "同顺财经", "问财", "喵懂投资"],
            "aliases": {
                "投资号": "同花顺投资",
                "同花顺投资": "同花顺投资",
                "股民社区": "同顺股民社区",
                "同顺股民社区": "同顺股民社区",
                "同花顺股民社区": "同顺股民社区",
                "理财": "同花顺理财",
                "同花顺理财": "同花顺理财",
                "财经号": "同顺财经",
                "同顺财经": "同顺财经",
                "同花顺财经": "同顺财经",
                "问财": "问财",
                "喵懂投资": "喵懂投资",
            },
            "exclude_blank": True,
        },
        "douyin": {
            "display_name": "抖音",
            "match_channels": ["抖音"],
            "filter_enabled": False,
            "include_accounts": [
                "同花顺投资",
                "同花顺股民社区",
                "同花顺财富",
                "同花顺财经",
                "同花顺问财",
                "喵懂投资",
                "同花顺期货通",
            ],
            "aliases": {
                "投资号": "同花顺投资",
                "同花顺投资": "同花顺投资",
                "股民社区": "同花顺股民社区",
                "同顺股民社区": "同花顺股民社区",
                "同花顺股民社区": "同花顺股民社区",
                "财富": "同花顺财富",
                "同花顺财富": "同花顺财富",
                "财经号": "同花顺财经",
                "财经": "同花顺财经",
                "同花顺财经": "同花顺财经",
                "问财": "同花顺问财",
                "同花顺问财": "同花顺问财",
                "喵懂投资": "喵懂投资",
                "期货通": "同花顺期货通",
                "同花顺期货通": "同花顺期货通",
            },
            "id_aliases": {},
            "exclude_blank": False,
        },
        "bilibili": {
            "display_name": "B站",
            "match_channels": ["B站"],
            "filter_enabled": False,
            "include_accounts": ["同花顺投资"],
            "aliases": {"投资号": "同花顺投资", "同花顺投资": "同花顺投资"},
            "id_aliases": {"1622777305": "同花顺投资"},
            "exclude_blank": False,
        },
    }
}

ACCOUNT_FILTER_DETAIL_COLUMNS = [
    "platform",
    "platform_group",
    "channel",
    "source_file",
    "source_sheet",
    "source_row",
    "content_id",
    "material_id",
    "title",
    "account_raw",
    "account",
    "normalized_account",
    "account_filter_status",
    "filter_reason",
    "spend",
    "activations",
    "first_pay_count",
]


@dataclass(frozen=True)
class AccountFilterDecision:
    scoped: bool
    included: bool
    original_account: str
    normalized_account: str
    platform: str = ""
    filter_enabled: bool = False
    reason: str = ""


@dataclass(frozen=True)
class PlatformAccountFilterRule:
    key: str
    platform: str
    match_channels: tuple[str, ...]
    include_accounts: tuple[str, ...]
    aliases: Mapping[str, str]
    id_aliases: Mapping[str, str]
    exclude_blank: bool
    filter_enabled: bool

    @property
    def include_set(self) -> set[str]:
        return set(self.include_accounts)

    def matches(self, channel: object) -> bool:
        text = clean_account_text(channel)
        return any(token and token in text for token in self.match_channels)

    def evaluate(self, account: object, account_id: object = "") -> AccountFilterDecision:
        original = clean_account_text(account)
        normalized = self.aliases.get(original, original)
        account_id_text = clean_identifier_text(account_id)
        if not original and account_id_text:
            normalized = self.id_aliases.get(account_id_text, account_id_text)
            original = account_id_text
        if not original:
            return AccountFilterDecision(
                scoped=False,
                included=True,
                original_account=original,
                normalized_account=normalized,
                platform=self.platform,
                filter_enabled=self.filter_enabled,
            )
        if not self.filter_enabled:
            return AccountFilterDecision(
                scoped=False,
                included=True,
                original_account=original,
                normalized_account=original,
                platform=self.platform,
                filter_enabled=False,
            )
        if normalized not in self.include_set:
            return AccountFilterDecision(
                scoped=True,
                included=False,
                original_account=original,
                normalized_account=normalized,
                platform=self.platform,
                filter_enabled=True,
                reason=f"不在{self.platform}账号白名单",
            )
        return AccountFilterDecision(
            scoped=True,
            included=True,
            original_account=original,
            normalized_account=normalized,
            platform=self.platform,
            filter_enabled=True,
        )


@dataclass(frozen=True)
class AccountFilterConfig:
    rules: tuple[PlatformAccountFilterRule, ...]
    source_path: Path
    source_name: str

    @property
    def include_accounts(self) -> tuple[str, ...]:
        rule = self.rule_for_platform("小红书")
        return rule.include_accounts if rule else ()

    @property
    def aliases(self) -> Mapping[str, str]:
        rule = self.rule_for_platform("小红书")
        return rule.aliases if rule else {}

    @property
    def exclude_blank(self) -> bool:
        rule = self.rule_for_platform("小红书")
        return bool(rule.exclude_blank) if rule else True

    def rule_for_platform(self, platform: str) -> Optional[PlatformAccountFilterRule]:
        platform_name = clean_account_text(platform)
        for rule in self.rules:
            if rule.platform == platform_name:
                return rule
        return None

    def evaluate(self, channel: object, account: object, account_id: object = "") -> AccountFilterDecision:
        for rule in self.rules:
            if rule.matches(channel):
                return rule.evaluate(account, account_id)
        original = clean_account_text(account)
        return AccountFilterDecision(
            scoped=False,
            included=True,
            original_account=original,
            normalized_account=original,
        )

    def expected_accounts_by_platform(self) -> dict[str, list[str]]:
        return {rule.platform: list(rule.include_accounts) for rule in self.rules if rule.include_accounts}

    def to_frame(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for rule in self.rules:
            rows.extend(self._rule_rows(rule))
        return pd.DataFrame(
            rows,
            columns=[
                "platform",
                "rule_type",
                "source_account",
                "normalized_account",
                "included",
                "filter_enabled",
                "status",
                "config_source",
                "config_path",
                "note",
            ],
        )

    def _rule_rows(self, rule: PlatformAccountFilterRule) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for account in rule.include_accounts:
            rows.append(
                {
                    "platform": rule.platform,
                    "rule_type": "白名单",
                    "source_account": account,
                    "normalized_account": account,
                    "included": True,
                    "filter_enabled": rule.filter_enabled,
                    "status": "参与统计" if rule.filter_enabled else "配置留存",
                    "config_source": self.source_name,
                    "config_path": str(self.source_path),
                    "note": "启用过滤的平台只统计白名单账号；未启用过滤的平台仅用于口径留存和覆盖校验。",
                }
            )
        for source_account, normalized_account in rule.aliases.items():
            included = normalized_account in rule.include_set
            rows.append(
                {
                    "platform": rule.platform,
                    "rule_type": "别名映射",
                    "source_account": source_account,
                    "normalized_account": normalized_account,
                    "included": included,
                    "filter_enabled": rule.filter_enabled,
                    "status": "参与统计" if rule.filter_enabled and included else "配置留存",
                    "config_source": self.source_name,
                    "config_path": str(self.source_path),
                    "note": "原始账号先归一到业务账号，再按平台过滤开关判断是否进入汇总。",
                }
            )
        for source_id, normalized_account in rule.id_aliases.items():
            included = normalized_account in rule.include_set
            rows.append(
                {
                    "platform": rule.platform,
                    "rule_type": "ID映射",
                    "source_account": source_id,
                    "normalized_account": normalized_account,
                    "included": included,
                    "filter_enabled": rule.filter_enabled,
                    "status": "参与统计" if rule.filter_enabled and included else "配置留存",
                    "config_source": self.source_name,
                    "config_path": str(self.source_path),
                    "note": "账号名为空时，可用平台账号ID映射到白名单账号。",
                }
            )
        rows.append(
            {
                "platform": rule.platform,
                "rule_type": "空账号策略",
                "source_account": "",
                "normalized_account": "",
                "included": True,
                "filter_enabled": rule.filter_enabled,
                "status": "默认记录",
                "config_source": self.source_name,
                "config_path": str(self.source_path),
                "note": "没有账号的数据默认进入统计；只有存在账号或账号ID时才进行白名单匹配。",
            }
        )
        return rows


def load_account_filter_config(path: Optional[Path] = None) -> AccountFilterConfig:
    config_path = Path(path or "config/account_filters.yml")
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        source_name = "本地配置"
    else:
        raw = DEFAULT_ACCOUNT_FILTERS
        source_name = "内置默认配置"
    rules = tuple(_build_rule(key, section, config_path) for key, section in _platform_sections(raw).items())
    if not rules:
        raise ValueError(f"{config_path} 未配置账号过滤平台规则。")
    return AccountFilterConfig(rules=rules, source_path=config_path, source_name=source_name)


def apply_account_filters(
    canonical: pd.DataFrame,
    config: AccountFilterConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if canonical.empty:
        filtered = canonical.copy()
        for column in ["account_filter_status", "account_filter_reason", "account_normalized"]:
            if column not in filtered.columns:
                filtered[column] = ""
        return filtered, pd.DataFrame(columns=ACCOUNT_FILTER_DETAIL_COLUMNS)

    prepared = canonical.copy()
    has_account_raw_column = "account_raw" in prepared.columns
    for column in [
        "platform",
        "platform_group",
        "channel",
        "account_raw",
        "account_id",
        "account",
        "author",
        "account_mapping_source",
        "account_filter_status",
        "account_filter_reason",
        "account_normalized",
    ]:
        if column not in prepared.columns:
            prepared[column] = ""

    keep_mask = pd.Series(True, index=prepared.index)
    detail_rows: list[dict[str, object]] = []
    for index, row in prepared.iterrows():
        channel = _channel_scope(row)
        original_account = _original_account(row, require_raw=has_account_raw_column)
        decision = config.evaluate(channel, original_account, row.get("account_id", ""))
        if not decision.scoped:
            continue
        prepared.at[index, "account_normalized"] = decision.normalized_account
        if decision.included:
            prepared.at[index, "account"] = decision.normalized_account
            prepared.at[index, "author"] = decision.normalized_account
            prepared.at[index, "account_filter_status"] = "已统计"
            prepared.at[index, "account_filter_reason"] = ""
            prepared.at[index, "account_mapping_source"] = _append_source(
                row.get("account_mapping_source", ""),
                "账号过滤配置",
            )
            continue
        keep_mask.at[index] = False
        prepared.at[index, "account_filter_status"] = "已排除"
        prepared.at[index, "account_filter_reason"] = decision.reason
        detail_rows.append(_detail_row(row, decision))

    details = pd.DataFrame(detail_rows, columns=ACCOUNT_FILTER_DETAIL_COLUMNS)
    return prepared.loc[keep_mask].reset_index(drop=True), details.reset_index(drop=True)


def is_xiaohongshu_channel(value: object) -> bool:
    return "小红书" in clean_account_text(value)


def clean_account_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def clean_identifier_text(value: object) -> str:
    text = clean_account_text(value)
    if not text:
        return ""
    try:
        numeric = pd.to_numeric(pd.Series([text]), errors="coerce").iloc[0]
    except Exception:
        numeric = pd.NA
    if not pd.isna(numeric) and float(numeric).is_integer():
        return str(int(float(numeric)))
    return text.removesuffix(".0")


def _platform_sections(raw: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    if "platforms" in raw and isinstance(raw["platforms"], Mapping):
        return raw["platforms"]  # type: ignore[return-value]
    if "account_filters" in raw and isinstance(raw["account_filters"], Mapping):
        filters = raw["account_filters"]
        if "platforms" in filters and isinstance(filters["platforms"], Mapping):
            return filters["platforms"]  # type: ignore[return-value]
        if "xiaohongshu" in filters and isinstance(filters["xiaohongshu"], Mapping):
            return {"xiaohongshu": filters["xiaohongshu"]}  # type: ignore[return-value]
    if "xiaohongshu" in raw and isinstance(raw["xiaohongshu"], Mapping):
        return {"xiaohongshu": raw["xiaohongshu"]}  # type: ignore[return-value]
    if "小红书" in raw and isinstance(raw["小红书"], Mapping):
        return {"xiaohongshu": raw["小红书"]}  # type: ignore[return-value]
    return DEFAULT_ACCOUNT_FILTERS["platforms"]


def _build_rule(key: object, section: Mapping[str, object], config_path: Path) -> PlatformAccountFilterRule:
    rule_key = clean_account_text(key)
    platform = clean_account_text(section.get("display_name")) or _default_platform_name(rule_key)
    match_channels = tuple(_clean_list(section.get("match_channels", [platform])))
    include_accounts = tuple(_clean_list(section.get("include_accounts", [])))
    aliases = _clean_mapping(section.get("aliases", {}), platform)
    id_aliases = _clean_identifier_mapping(section.get("id_aliases", {}), platform)
    filter_enabled = bool(section.get("filter_enabled", platform == "小红书"))
    exclude_blank = bool(section.get("exclude_blank", True))
    if not include_accounts:
        raise ValueError(f"{config_path} 未配置 {platform} 账号白名单 include_accounts。")
    missing_targets = sorted(
        {target for target in [*aliases.values(), *id_aliases.values()] if target not in set(include_accounts)}
    )
    if missing_targets:
        raise ValueError(f"{config_path} 的 {platform} 存在未进入白名单的账号别名目标：{', '.join(missing_targets)}")
    return PlatformAccountFilterRule(
        key=rule_key,
        platform=platform,
        match_channels=match_channels,
        include_accounts=include_accounts,
        aliases=aliases,
        id_aliases=id_aliases,
        exclude_blank=exclude_blank,
        filter_enabled=filter_enabled,
    )


def _default_platform_name(key: str) -> str:
    normalized = key.lower()
    if normalized in {"xiaohongshu", "xhs"}:
        return "小红书"
    if normalized in {"douyin", "dy"}:
        return "抖音"
    if normalized in {"bilibili", "b站"}:
        return "B站"
    return key


def _clean_list(values: object) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("账号白名单 include_accounts 和 match_channels 必须是列表。")
    return [text for text in (clean_account_text(value) for value in values) if text]


def _clean_mapping(values: object, platform: str = "") -> dict[str, str]:
    if not isinstance(values, Mapping):
        label = f"{platform} " if platform else ""
        raise ValueError(f"{label}账号别名 aliases 必须是键值映射。")
    mapping: dict[str, str] = {}
    for key, value in values.items():
        source = clean_account_text(key)
        target = clean_account_text(value)
        if source and target:
            mapping[source] = target
    return mapping


def _clean_identifier_mapping(values: object, platform: str = "") -> dict[str, str]:
    if not isinstance(values, Mapping):
        label = f"{platform} " if platform else ""
        raise ValueError(f"{label}账号ID别名 id_aliases 必须是键值映射。")
    mapping: dict[str, str] = {}
    for key, value in values.items():
        source = clean_identifier_text(key)
        target = clean_account_text(value)
        if source and target:
            mapping[source] = target
    return mapping


def _channel_scope(row: pd.Series) -> str:
    values = []
    for column in ["channel", "platform", "platform_group"]:
        value = clean_account_text(row.get(column, ""))
        if value:
            values.append(value)
    return " ".join(dict.fromkeys(values))


def _original_account(row: pd.Series, *, require_raw: bool = False) -> str:
    account_raw = clean_account_text(row.get("account_raw", ""))
    if require_raw:
        return account_raw
    return account_raw or clean_account_text(row.get("account", ""))


def _append_source(current: object, source: str) -> str:
    values = [clean_account_text(current), source]
    return "；".join(dict.fromkeys(value for value in values if value))


def _detail_row(row: pd.Series, decision: AccountFilterDecision) -> dict[str, object]:
    item = {column: row.get(column, "") for column in ACCOUNT_FILTER_DETAIL_COLUMNS}
    item["account_raw"] = decision.original_account
    item["account"] = clean_account_text(row.get("account", ""))
    item["normalized_account"] = decision.normalized_account
    item["account_filter_status"] = "已排除"
    item["filter_reason"] = decision.reason
    return item
