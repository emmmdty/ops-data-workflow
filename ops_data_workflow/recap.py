"""Unified weekly/monthly recap metric tables."""

from __future__ import annotations

import pandas as pd

from .source_channels import normalize_channel_name


WEEKLY_COLUMNS = ["渠道", "消耗", "曝光量", "激活数", "激活成本", "付费", "付费成本"]
MONTHLY_EXTRA_COLUMNS = ["大盘付费数据", "大盘付费成本", "原生内容曝光数", "消耗占比"]


def build_recap_summary(items: pd.DataFrame, *, period_level: str = "week") -> pd.DataFrame:
    """Build the unified display fields for weekly and monthly recaps."""
    prepared = items.copy()
    for column in ["channel", "spend", "impressions", "activations", "first_pay_count"]:
        if column not in prepared.columns:
            prepared[column] = "" if column == "channel" else 0.0
    for column in ["spend", "impressions", "activations", "first_pay_count"]:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce").fillna(0.0)
    prepared["channel"] = prepared["channel"].fillna("").astype(str).map(normalize_channel_name)

    rows = [_summary_row("汇总", prepared, prepared["spend"].sum())]
    for channel, group in _sorted_channel_groups(prepared):
        rows.append(_summary_row(str(channel), group, prepared["spend"].sum()))

    result = pd.DataFrame(rows)
    if period_level == "month":
        return result[WEEKLY_COLUMNS + MONTHLY_EXTRA_COLUMNS]
    return result[WEEKLY_COLUMNS]


def _summary_row(channel: str, group: pd.DataFrame, total_spend: float) -> dict[str, object]:
    spend = float(group["spend"].sum())
    impressions = float(group["impressions"].sum())
    activations = float(group["activations"].sum())
    first_pay = float(group["first_pay_count"].sum())
    return {
        "渠道": channel,
        "消耗": spend,
        "曝光量": impressions,
        "激活数": activations,
        "激活成本": spend / activations if activations else 0.0,
        "付费": first_pay,
        "付费成本": spend / first_pay if first_pay else 0.0,
        "大盘付费数据": "占位",
        "大盘付费成本": "占位",
        "原生内容曝光数": impressions,
        "消耗占比": spend / total_spend if total_spend else 0.0,
    }


def _sorted_channel_groups(items: pd.DataFrame):
    if items.empty:
        return []
    grouped = list(items.groupby("channel", dropna=False, sort=False))
    return sorted(grouped, key=lambda item: (_channel_priority(item[0]), -float(item[1]["spend"].sum())))


def _channel_priority(channel: object) -> int:
    text = str(channel or "")
    if "抖音" in text:
        return 0
    if "小红书" in text:
        return 1
    if "B站" in text:
        return 2
    return 3
