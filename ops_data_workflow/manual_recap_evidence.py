"""Structured evidence for manual AI recap generation."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

import pandas as pd


OVERVIEW_METRICS = (
    "spend",
    "impressions",
    "activations",
    "activation_cost",
    "first_pay_count",
    "first_pay_cost",
    "first_pay_rate",
)
COUNT_METRICS = ("spend", "impressions", "clicks", "activations", "first_pay_count")
UNMATCHED_LABELS = {"", "未匹配", "未匹配题材", "未命名题材", "无"}
CHANNEL_TOKEN_REPLACEMENTS = {
    "抖音": "douyin",
    "小红书": "xiaohongshu",
    "B站": "bzhan",
    "微信": "weixin",
    "商业化": "shangyehua",
    "市场部": "shichangbu",
}


def build_manual_recap_evidence(
    *,
    current_items: pd.DataFrame,
    previous_items: pd.DataFrame | None = None,
    channel_comparison: pd.DataFrame | None = None,
    current_topic_labels: pd.DataFrame | None = None,
    previous_topic_labels: pd.DataFrame | None = None,
    top_content_cases: pd.DataFrame | None = None,
    max_drivers: int = 5,
) -> dict[str, Any]:
    """Build deterministic cross-period evidence before asking the LLM to write."""
    current = _prepare_items(current_items)
    previous = _prepare_items(previous_items if previous_items is not None else pd.DataFrame())
    comparison = channel_comparison.copy() if channel_comparison is not None else pd.DataFrame()
    has_previous = _has_previous_data(previous, comparison)

    content_type_drivers = _build_dimension_drivers(
        current,
        previous,
        dimension="content_type",
        id_dimension="content_type",
        has_previous=has_previous,
        max_drivers=max_drivers,
    )
    topic_drivers = _build_topic_drivers(
        current_topic_labels,
        previous_topic_labels,
        has_previous=has_previous,
        max_drivers=max_drivers,
    )
    material_drivers = _build_material_drivers(current, previous, has_previous=has_previous, max_drivers=max_drivers)
    channel_gaps = _channel_data_gaps(current, top_content_cases)
    channels = _assemble_channels(content_type_drivers, topic_drivers, material_drivers, channel_gaps)

    return {
        "change_driver_summary": {
            "overview_metrics": _overview_metrics(current, previous, comparison, has_previous=has_previous),
            "contribution_sources": _contribution_sources(channels),
            "data_gaps": _overall_data_gaps(current, has_previous, channel_gaps),
        },
        "historical_content_context": {
            "channels": channels,
        },
    }


def _prepare_items(items: pd.DataFrame) -> pd.DataFrame:
    prepared = items.copy()
    prepared = _analyzable_items(prepared)
    for column in COUNT_METRICS:
        if column not in prepared.columns:
            prepared[column] = 0.0
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce").fillna(0.0)
    for column in [
        "channel",
        "title",
        "content_id",
        "material_id",
        "content_url",
        "content_type",
        "content_category",
        "category_l2",
        "category_l3",
        "manual_category",
        "ledger_content_type",
        "ai_category",
    ]:
        if column not in prepared.columns:
            prepared[column] = ""
        prepared[column] = prepared[column].fillna("").astype(str).str.strip()
    prepared["channel"] = prepared["channel"].replace("", "未知渠道")
    prepared["content_type"] = prepared.apply(_content_type_for_row, axis=1)
    prepared["topic_name"] = prepared.apply(_topic_for_row, axis=1)
    prepared = _add_rate_columns(prepared)
    return prepared


def _analyzable_items(items: pd.DataFrame) -> pd.DataFrame:
    if items.empty:
        return items.copy()
    if "analysis_status" in items.columns:
        return items[items["analysis_status"].fillna("").astype(str).eq("可分析")].copy()
    if "is_analyzable" in items.columns:
        values = items["is_analyzable"].fillna(False)
        mask = values.astype(str).str.lower().isin({"true", "1", "yes", "是"})
        return items[mask].copy()
    return items.copy()


def _add_rate_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["ctr"] = _safe_series_divide(result["clicks"], result["impressions"])
    result["activation_cost"] = _safe_series_divide(result["spend"], result["activations"])
    result["first_pay_cost"] = _safe_series_divide(result["spend"], result["first_pay_count"])
    result["first_pay_rate"] = _safe_series_divide(result["first_pay_count"], result["activations"])
    return result


def _safe_series_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    top = pd.to_numeric(numerator, errors="coerce").fillna(0.0)
    bottom = pd.to_numeric(denominator, errors="coerce").fillna(0.0)
    result = pd.Series(0.0, index=top.index, dtype="float64")
    mask = bottom.ne(0.0)
    result.loc[mask] = top.loc[mask] / bottom.loc[mask]
    return result


def _content_type_for_row(row: pd.Series) -> str:
    for column in ["content_type", "content_category", "category_l2", "manual_category", "ledger_content_type", "ai_category"]:
        value = str(row.get(column, "") or "").strip()
        if value:
            return value
    return "未匹配"


def _topic_for_row(row: pd.Series) -> str:
    for column in ["topic_name", "category_l3", "content_type"]:
        value = str(row.get(column, "") or "").strip()
        if value:
            return value
    return "未匹配题材"


def _has_previous_data(previous: pd.DataFrame, comparison: pd.DataFrame) -> bool:
    if not previous.empty:
        return True
    if comparison.empty:
        return False
    previous_columns = [column for column in comparison.columns if column.endswith("_previous")]
    if not previous_columns:
        return False
    return comparison[previous_columns].apply(pd.to_numeric, errors="coerce").notna().any().any()


def _overview_metrics(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    comparison: pd.DataFrame,
    *,
    has_previous: bool,
) -> dict[str, dict[str, Any]]:
    total_row = _comparison_total_row(comparison)
    current_totals = _aggregate_total(current)
    previous_totals = _aggregate_total(previous)
    result: dict[str, dict[str, Any]] = {}
    for metric in OVERVIEW_METRICS:
        current_value = _value_from_row(total_row, f"{metric}_current", current_totals.get(metric, 0.0))
        previous_default = previous_totals.get(metric, None if not has_previous else 0.0)
        previous_value = _value_from_row(total_row, f"{metric}_previous", previous_default)
        delta = None if previous_value is None else current_value - previous_value
        change_rate = _value_from_row(total_row, f"{metric}_change_rate", _change_rate(current_value, previous_value))
        result[metric] = {
            "evidence_id": f"overview.metric.{metric}",
            "current": current_value,
            "previous": previous_value,
            "delta": delta,
            "change_rate": change_rate,
            "direction": _metric_direction(metric, delta),
        }
    return result


def _comparison_total_row(comparison: pd.DataFrame) -> dict[str, Any]:
    if comparison.empty or "channel" not in comparison.columns:
        return {}
    normalized = comparison.copy()
    normalized["channel"] = normalized["channel"].fillna("").astype(str).str.strip()
    total = normalized[normalized["channel"].isin(["总计", "汇总"])]
    if total.empty:
        return {}
    return total.iloc[0].to_dict()


def _value_from_row(row: dict[str, Any], key: str, default: object) -> float | None:
    if key in row:
        value = _nullable_float(row.get(key))
        if value is not None:
            return value
    return _nullable_float(default)


def _aggregate_total(items: pd.DataFrame) -> dict[str, float]:
    spend = _sum(items, "spend")
    impressions = _sum(items, "impressions")
    clicks = _sum(items, "clicks")
    activations = _sum(items, "activations")
    first_pay_count = _sum(items, "first_pay_count")
    return {
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": _safe_divide(clicks, impressions),
        "activations": activations,
        "activation_cost": _safe_divide(spend, activations),
        "first_pay_count": first_pay_count,
        "first_pay_cost": _safe_divide(spend, first_pay_count),
        "first_pay_rate": _safe_divide(first_pay_count, activations),
    }


def _build_dimension_drivers(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    *,
    dimension: str,
    id_dimension: str,
    has_previous: bool,
    max_drivers: int,
) -> dict[str, list[dict[str, Any]]]:
    current_summary = _summarize_dimension(current, dimension)
    previous_summary = _summarize_dimension(previous, dimension)
    return _driver_rows_by_channel(
        current_summary,
        previous_summary,
        id_dimension=id_dimension,
        has_previous=has_previous,
        max_drivers=max_drivers,
    )


def _build_topic_drivers(
    current_topic_labels: pd.DataFrame | None,
    previous_topic_labels: pd.DataFrame | None,
    *,
    has_previous: bool,
    max_drivers: int,
) -> dict[str, list[dict[str, Any]]]:
    current = _prepare_topic_labels(current_topic_labels)
    previous = _prepare_topic_labels(previous_topic_labels)
    if current.empty and previous.empty:
        return {}
    current_summary = _summarize_dimension(current, "topic_name")
    previous_summary = _summarize_dimension(previous, "topic_name")
    return _driver_rows_by_channel(
        current_summary,
        previous_summary,
        id_dimension="topic",
        has_previous=has_previous,
        max_drivers=max_drivers,
    )


def _prepare_topic_labels(labels: pd.DataFrame | None) -> pd.DataFrame:
    if labels is None or labels.empty:
        return pd.DataFrame()
    prepared = labels.copy()
    for column in COUNT_METRICS:
        if column not in prepared.columns:
            prepared[column] = 0.0
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce").fillna(0.0)
    for column in ["channel", "topic_name", "content_type", "title", "content_id", "material_id"]:
        if column not in prepared.columns:
            prepared[column] = ""
        prepared[column] = prepared[column].fillna("").astype(str).str.strip()
    prepared["channel"] = prepared["channel"].replace("", "未知渠道")
    prepared["topic_name"] = prepared["topic_name"].replace("", "未匹配题材")
    prepared = _add_rate_columns(prepared)
    return prepared


def _summarize_dimension(items: pd.DataFrame, dimension: str) -> pd.DataFrame:
    columns = ["channel", "name", *OVERVIEW_METRICS]
    if items.empty:
        return pd.DataFrame(columns=columns)
    working = items.copy()
    if dimension not in working.columns:
        working[dimension] = "未匹配"
    working[dimension] = working[dimension].fillna("").astype(str).str.strip()
    working.loc[working[dimension].eq(""), dimension] = "未匹配"
    grouped = (
        working.groupby(["channel", dimension], dropna=False)
        .agg(
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
        )
        .reset_index()
        .rename(columns={dimension: "name"})
    )
    grouped["activation_cost"] = _safe_series_divide(grouped["spend"], grouped["activations"])
    grouped["first_pay_cost"] = _safe_series_divide(grouped["spend"], grouped["first_pay_count"])
    grouped["first_pay_rate"] = _safe_series_divide(grouped["first_pay_count"], grouped["activations"])
    return grouped[columns]


def _driver_rows_by_channel(
    current_summary: pd.DataFrame,
    previous_summary: pd.DataFrame,
    *,
    id_dimension: str,
    has_previous: bool,
    max_drivers: int,
) -> dict[str, list[dict[str, Any]]]:
    channels = _ordered_unique([*current_summary.get("channel", []), *previous_summary.get("channel", [])])
    result: dict[str, list[dict[str, Any]]] = {}
    for channel in channels:
        current_rows = current_summary[current_summary["channel"].eq(channel)].copy() if not current_summary.empty else pd.DataFrame()
        previous_rows = previous_summary[previous_summary["channel"].eq(channel)].copy() if not previous_summary.empty else pd.DataFrame()
        names = _ordered_unique([*current_rows.get("name", []), *previous_rows.get("name", [])])
        drivers = [
            _driver_record(channel, name, _first_row(current_rows, "name", name), _first_row(previous_rows, "name", name), id_dimension, has_previous=has_previous)
            for name in names
        ]
        drivers = sorted(
            drivers,
            key=lambda row: (
                abs(float(row.get("activations_delta") or 0.0)),
                float(row.get("current_spend") or 0.0),
            ),
            reverse=True,
        )[:max_drivers]
        if drivers:
            result[channel] = drivers
    return result


def _driver_record(
    channel: str,
    name: str,
    current_row: dict[str, Any],
    previous_row: dict[str, Any],
    id_dimension: str,
    *,
    has_previous: bool,
) -> dict[str, Any]:
    current_values = _metric_values(current_row)
    previous_values = _metric_values(previous_row) if has_previous else _empty_metric_values(previous_is_none=True)
    return {
        "evidence_id": f"channel.{_slug(channel)}.{id_dimension}.{_slug(name)}",
        "name": name,
        "driver_tag": _driver_tag(current_values, previous_values, has_previous=has_previous),
        "current_spend": current_values["spend"],
        "previous_spend": previous_values["spend"],
        "spend_delta": _delta(current_values["spend"], previous_values["spend"]),
        "spend_change_rate": _change_rate(current_values["spend"], previous_values["spend"]),
        "current_activations": current_values["activations"],
        "previous_activations": previous_values["activations"],
        "activations_delta": _delta(current_values["activations"], previous_values["activations"]),
        "activation_cost_current": current_values["activation_cost"],
        "activation_cost_previous": previous_values["activation_cost"],
        "activation_cost_delta": _delta(current_values["activation_cost"], previous_values["activation_cost"]),
        "current_first_pay_count": current_values["first_pay_count"],
        "previous_first_pay_count": previous_values["first_pay_count"],
        "first_pay_count_delta": _delta(current_values["first_pay_count"], previous_values["first_pay_count"]),
    }


def _build_material_drivers(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    *,
    has_previous: bool,
    max_drivers: int,
) -> dict[str, list[dict[str, Any]]]:
    if current.empty:
        return {}
    current_materials = _summarize_materials(current)
    previous_materials = _summarize_materials(previous)
    result: dict[str, list[dict[str, Any]]] = {}
    for channel in _ordered_unique(current_materials.get("channel", [])):
        scoped = current_materials[current_materials["channel"].eq(channel)].sort_values("spend", ascending=False).head(max_drivers)
        previous_scoped = previous_materials[previous_materials["channel"].eq(channel)] if not previous_materials.empty else pd.DataFrame()
        drivers = []
        for _, row in scoped.iterrows():
            previous_row = _first_row(previous_scoped, "material_key", str(row.get("material_key", "") or ""))
            record = _driver_record(channel, str(row.get("title", "") or row.get("material_key", "")), row.to_dict(), previous_row, "material", has_previous=has_previous)
            record["content_url"] = str(row.get("content_url", "") or "")
            record["content_type"] = str(row.get("content_type", "") or "")
            drivers.append(record)
        if drivers:
            result[channel] = drivers
    return result


def _summarize_materials(items: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "channel",
        "material_key",
        "title",
        "content_type",
        "content_url",
        "spend",
        "impressions",
        "clicks",
        "activations",
        "activation_cost",
        "first_pay_count",
        "first_pay_cost",
        "first_pay_rate",
    ]
    if items.empty:
        return pd.DataFrame(columns=columns)
    working = items.copy()
    working["material_key"] = working.apply(_material_key, axis=1)
    grouped = (
        working.groupby(["channel", "material_key"], dropna=False)
        .agg(
            title=("title", _first_non_blank),
            content_type=("content_type", _first_non_blank),
            content_url=("content_url", _first_non_blank),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
        )
        .reset_index()
    )
    grouped["activation_cost"] = _safe_series_divide(grouped["spend"], grouped["activations"])
    grouped["first_pay_cost"] = _safe_series_divide(grouped["spend"], grouped["first_pay_count"])
    grouped["first_pay_rate"] = _safe_series_divide(grouped["first_pay_count"], grouped["activations"])
    return grouped[columns]


def _material_key(row: pd.Series) -> str:
    for column in ["content_id", "material_id", "content_url", "title"]:
        value = str(row.get(column, "") or "").strip()
        if value:
            return value
    return hashlib.md5(str(row.to_dict()).encode("utf-8")).hexdigest()[:10]


def _driver_tag(current: dict[str, float], previous: dict[str, float | None], *, has_previous: bool) -> str:
    if current["spend"] > 0 and current["activations"] <= 0:
        return "高消耗低转化"
    if not has_previous or previous["spend"] is None:
        return "数据不足"
    spend_delta = current["spend"] - float(previous["spend"] or 0.0)
    activations_delta = current["activations"] - float(previous["activations"] or 0.0)
    previous_cost = float(previous["activation_cost"] or 0.0)
    current_cost = current["activation_cost"]
    if float(previous["spend"] or 0.0) <= 0 and current["activations"] > 0:
        return "新增增量"
    if spend_delta > 0 and activations_delta > 0 and (previous_cost <= 0 or current_cost <= previous_cost):
        return "放量有效"
    if spend_delta > 0 and (activations_delta <= 0 or (previous_cost > 0 and current_cost > previous_cost)):
        return "放量低效"
    if spend_delta < 0 and activations_delta < 0:
        return "收缩拖累"
    if activations_delta > 0:
        return "新增增量"
    return "数据不足"


def _channel_data_gaps(current: pd.DataFrame, top_content_cases: pd.DataFrame | None) -> dict[str, list[dict[str, Any]]]:
    if current.empty:
        return {}
    top_cases = top_content_cases.copy() if top_content_cases is not None else pd.DataFrame()
    gaps: dict[str, list[dict[str, Any]]] = {}
    for channel in _ordered_unique(current["channel"]):
        scoped = current[current["channel"].eq(channel)].copy()
        channel_gaps: list[dict[str, Any]] = []
        unmatched = scoped[scoped["content_type"].isin(UNMATCHED_LABELS)]
        unmatched_spend = _sum(unmatched, "spend")
        if unmatched_spend > 0:
            channel_gaps.append(_gap_record(channel, "内容类型未匹配", f"未匹配内容类型消耗 {unmatched_spend:.0f}", value=unmatched_spend))
        if _sum(scoped, "spend") > 0 and _sum(scoped, "impressions") <= 0:
            channel_gaps.append(_gap_record(channel, "曝光为0", "当前渠道有消耗但曝光合计为0", value=_sum(scoped, "spend")))
        material_scope = _material_gap_scope(scoped, top_cases, channel)
        if not material_scope.empty:
            missing_title = material_scope["title"].fillna("").astype(str).str.strip().eq("")
            if missing_title.any():
                channel_gaps.append(_gap_record(channel, "素材标题缺失", f"Top 素材中 {int(missing_title.sum())} 条缺标题", value=float(missing_title.sum())))
        if channel_gaps:
            gaps[channel] = channel_gaps
    return gaps


def _material_gap_scope(scoped: pd.DataFrame, top_cases: pd.DataFrame, channel: str) -> pd.DataFrame:
    if not top_cases.empty and {"channel", "title"}.issubset(top_cases.columns):
        cases = top_cases[top_cases["channel"].fillna("").astype(str).str.strip().eq(channel)].copy()
        if not cases.empty:
            return cases
    return scoped.sort_values("spend", ascending=False).head(5)


def _overall_data_gaps(current: pd.DataFrame, has_previous: bool, channel_gaps: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not has_previous:
        gaps.append({"evidence_id": "gap.previous_period", "type": "缺少可比周期", "message": "缺少上一同等级周期，不能归因环比变化。"})
    for channel in _ordered_unique(current.get("channel", [])):
        gaps.extend(channel_gaps.get(channel, []))
    return gaps


def _gap_record(channel: str, gap_type: str, message: str, *, value: float) -> dict[str, Any]:
    return {
        "evidence_id": f"gap.{_slug(channel)}.{_slug(gap_type)}",
        "channel": channel,
        "type": gap_type,
        "message": message,
        "value": value,
    }


def _assemble_channels(
    content_type_drivers: dict[str, list[dict[str, Any]]],
    topic_drivers: dict[str, list[dict[str, Any]]],
    material_drivers: dict[str, list[dict[str, Any]]],
    channel_gaps: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    channels = _ordered_unique([*content_type_drivers.keys(), *topic_drivers.keys(), *material_drivers.keys(), *channel_gaps.keys()])
    return [
        {
            "channel": channel,
            "content_type_drivers": content_type_drivers.get(channel, []),
            "topic_drivers": topic_drivers.get(channel, []),
            "material_drivers": material_drivers.get(channel, []),
            "data_gaps": channel_gaps.get(channel, []),
        }
        for channel in channels
    ]


def _contribution_sources(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    for channel in channels:
        for dimension_key in ["content_type_drivers", "topic_drivers", "material_drivers"]:
            for driver in channel.get(dimension_key, []):
                drivers.append(
                    {
                        "evidence_id": driver.get("evidence_id", ""),
                        "channel": channel.get("channel", ""),
                        "name": driver.get("name", ""),
                        "driver_tag": driver.get("driver_tag", ""),
                        "activations_delta": driver.get("activations_delta"),
                        "spend_delta": driver.get("spend_delta"),
                    }
                )
    return sorted(drivers, key=lambda item: abs(float(item.get("activations_delta") or 0.0)), reverse=True)[:12]


def _first_row(frame: pd.DataFrame, column: str, value: object) -> dict[str, Any]:
    if frame.empty or column not in frame.columns:
        return {}
    match = frame[frame[column].fillna("").astype(str).eq(str(value))]
    return {} if match.empty else match.iloc[0].to_dict()


def _metric_values(row: dict[str, Any]) -> dict[str, float]:
    return {
        "spend": _float(row.get("spend")),
        "impressions": _float(row.get("impressions")),
        "activations": _float(row.get("activations")),
        "activation_cost": _float(row.get("activation_cost")),
        "first_pay_count": _float(row.get("first_pay_count")),
        "first_pay_cost": _float(row.get("first_pay_cost")),
        "first_pay_rate": _float(row.get("first_pay_rate")),
    }


def _empty_metric_values(*, previous_is_none: bool = False) -> dict[str, float | None]:
    empty: dict[str, float | None] = {
        "spend": 0.0,
        "impressions": 0.0,
        "activations": 0.0,
        "activation_cost": 0.0,
        "first_pay_count": 0.0,
        "first_pay_cost": 0.0,
        "first_pay_rate": 0.0,
    }
    if previous_is_none:
        return {key: None for key in empty}
    return empty


def _delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return float(current) - float(previous)


def _change_rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or float(previous) == 0.0:
        return None
    return (float(current) - float(previous)) / float(previous)


def _metric_direction(metric: str, delta: float | None) -> str:
    if delta is None:
        return "无可比"
    if delta == 0:
        return "持平"
    if metric in {"activation_cost", "first_pay_cost"}:
        return "成本上升" if delta > 0 else "成本下降"
    return "提升" if delta > 0 else "下降"


def _nullable_float(value: object) -> float | None:
    if value is None:
        return None
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    return float(number)


def _float(value: object) -> float:
    number = _nullable_float(value)
    return 0.0 if number is None else float(number)


def _sum(items: pd.DataFrame, column: str) -> float:
    if items.empty or column not in items.columns:
        return 0.0
    return float(pd.to_numeric(items[column], errors="coerce").fillna(0.0).sum())


def _safe_divide(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if float(denominator) else 0.0


def _first_non_blank(values: Iterable[object]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _ordered_unique(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _slug(value: object) -> str:
    text = str(value or "").strip()
    for source, target in CHANNEL_TOKEN_REPLACEMENTS.items():
        text = text.replace(source, target)
    ascii_text = re.sub(r"[^A-Za-z0-9]+", "", text).lower()
    if ascii_text:
        return ascii_text[:48]
    digest = hashlib.md5(str(value or "").encode("utf-8")).hexdigest()[:10]
    return f"id{digest}"
