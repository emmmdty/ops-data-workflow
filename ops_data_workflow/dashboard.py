"""Read-only dashboard queries and aggregations."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Mapping, Optional, Sequence

import pandas as pd

from .comparison import COMPARISON_METRICS, build_channel_comparison
from .source_channels import normalize_channel_name, social_platform_from_name
from .storage import init_db, normalize_batch_metadata, previous_batch_from_rows


METRIC_COLUMNS = [
    "item_count",
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "activations",
    "activation_cost",
    "first_pay_count",
    "first_pay_cost",
    "first_pay_rate",
]

PERIOD_METRIC_TREND_COLUMNS = [
    "batch_id",
    "period_level",
    "period_key",
    "period_label",
    "period_start",
    "period_end",
    "trend_period",
    "source_type",
    "item_count",
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "activations",
    "activation_cost",
    "first_pay_count",
    "first_pay_cost",
    "first_pay_rate",
]

CHANNEL_COMPARISON_COLUMNS = [
    "channel",
    "spend_current",
    "spend_previous",
    "spend_change_rate",
    "impressions_current",
    "impressions_previous",
    "impressions_change_rate",
    "activations_current",
    "activations_previous",
    "activations_change_rate",
    "activation_cost_current",
    "activation_cost_previous",
    "activation_cost_change_rate",
    "first_pay_count_current",
    "first_pay_count_previous",
    "first_pay_count_change_rate",
    "first_pay_cost_current",
    "first_pay_cost_previous",
    "first_pay_cost_change_rate",
    "first_pay_rate_current",
    "first_pay_rate_previous",
    "first_pay_rate_change_rate",
]
OVERVIEW_TABLE_COLUMNS = [
    "channel",
    "spend",
    "spend_change_rate",
    "impressions",
    "impressions_change_rate",
    "activations",
    "activations_change_rate",
    "activation_cost",
    "activation_cost_change_rate",
    "first_pay_count",
    "first_pay_count_change_rate",
    "first_pay_cost",
    "first_pay_cost_change_rate",
]

COST_METRICS = {"activation_cost", "first_pay_cost"}
AI_REVIEW_AUTO_PASS_THRESHOLD = 0.80
BILIBILI_CHANNEL = "B站市场部"
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
BATCH_COLUMNS = [
    "batch_id",
    "period_start",
    "period_end",
    "created_at",
    "status",
    "period_level",
    "period_key",
    "period_label",
    "data_start",
    "data_end",
    "source_type",
]
CHANNEL_TOPIC_KEYWORD_RULES = [
    ("品牌歌曲", ("同花顺进行曲", "真英雄", "bgm", "BGM", "唱", "歌", "伴奏", "合唱")),
    ("新股民教育", ("新股民", "忠告", "K线", "入门", "小白", "启蒙")),
    ("股民心智", ("股民", "家族", "公平", "竞争", "心态", "亏损", "赚钱", "悟道")),
    ("财商认知", ("财商", "投资理财", "实践", "总结", "执行力", "聪明", "认知", "选择", "痛苦", "享受")),
    ("剧情达人", ("达人", "成王败寇", "天才", "证明", "误闯", "剧情")),
    ("行情资讯", ("资讯", "热点", "行情", "芯片", "复盘")),
    ("问财问句", ("问财", "问句")),
    ("社区互动", ("社区", "话题")),
    ("大佬采访", ("采访", "大佬")),
]

DETAIL_COLUMNS = [
    "batch_id",
    "batch_period_start",
    "batch_period_end",
    "platform",
    "channel",
    "title",
    "account_id",
    "content_id",
    "material_id",
    "account",
    "author",
    "content_url",
    "category_l2",
    "category_source",
    "review_status",
    "content_category",
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "activations",
    "activation_cost",
    "first_pay_count",
    "first_pay_cost",
    "first_pay_rate",
    "source_file",
    "source_sheet",
    "source_row",
    "source_file_hash",
    "duplicate_group_id",
    "review_action",
]

TOP_CONTENT_REVIEW_COLUMNS = [
    "batch_id",
    "batch_period_start",
    "batch_period_end",
    "platform_group",
    "platform",
    "channel",
    "rank_in_channel",
    "ai_review_status",
    "ai_review_reason",
    "audit_flags",
    "needs_review",
    "missing_content_url",
    "invalid_content_url",
    "missing_manual_category",
    "missing_content_category",
    "low_category_confidence",
    "type_conflict",
    "title",
    "content_id",
    "material_id",
    "account",
    "content_url",
    "manual_category",
    "ai_category",
    "content_category",
    "category_l2",
    "category_source",
    "category_confidence",
    "review_status",
    "review_reasons",
    "ledger_match_source",
    "ledger_content_type",
    "ledger_source_file",
    "ledger_source_sheet",
    "ledger_source_row",
    "match_risk_level",
    "match_risk_reason",
    "spend",
    "activations",
    "activation_cost",
    "source_file",
    "source_sheet",
]

NUMERIC_SOURCE_COLUMNS = [
    "spend",
    "impressions",
    "clicks",
    "activations",
    "first_pay_count",
]


@dataclass(frozen=True)
class DashboardFilters:
    period_start: str = ""
    period_end: str = ""
    platforms: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    content_categories: tuple[str, ...] = ()
    category_l3: tuple[str, ...] = ()
    text_query: str = ""


@dataclass(frozen=True)
class DashboardSummary:
    total_spend: float
    total_impressions: float
    activations: float
    activation_cost: float
    first_pay_count: float
    first_pay_cost: float
    first_pay_rate: float


def load_dashboard_items(db_path: Path) -> pd.DataFrame:
    """Load canonical rows from successful archived batches."""
    db_path = Path(db_path)
    if not db_path.exists():
        return _empty_items()
    init_db(db_path)

    with closing(sqlite3.connect(db_path)) as conn:
        try:
            items = pd.read_sql_query(
                """
                select
                    canonical_items.*,
                    upload_batches.period_start as batch_period_start,
                    upload_batches.period_end as batch_period_end,
                    upload_batches.created_at as batch_created_at,
                    upload_batches.period_level as batch_period_level,
                    upload_batches.period_key as batch_period_key,
                    upload_batches.period_label as batch_period_label,
                    upload_batches.data_start as batch_data_start,
                    upload_batches.data_end as batch_data_end,
                    upload_batches.source_type as batch_source_type
                from canonical_items
                join upload_batches
                    on canonical_items.batch_id = upload_batches.batch_id
                left join period_file_states
                    on period_file_states.period_key = upload_batches.period_start || '|' || upload_batches.period_end
                where upload_batches.status = 'ok'
                    and coalesce(period_file_states.status, 'active') = 'active'
                order by upload_batches.period_end, upload_batches.period_start, upload_batches.created_at, canonical_items.rowid
                """,
                conn,
            )
        except Exception:
            return _empty_items()
    return _normalize_items(items)


def load_all_dashboard_items(db_path: Path) -> pd.DataFrame:
    """Load all successful historical rows for trend pages."""
    return load_dashboard_items(db_path)


def list_successful_dashboard_batches(db_path: Path) -> pd.DataFrame:
    db_path = Path(db_path)
    if not db_path.exists():
        return _empty_batches()
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            batches = pd.read_sql_query(
                """
                select upload_batches.batch_id, upload_batches.period_start, upload_batches.period_end,
                       upload_batches.created_at, upload_batches.status,
                       upload_batches.period_level, upload_batches.period_key, upload_batches.period_label,
                       upload_batches.data_start, upload_batches.data_end, upload_batches.source_type
                from upload_batches
                left join period_file_states
                    on period_file_states.period_key = upload_batches.period_start || '|' || upload_batches.period_end
                where upload_batches.status = 'ok'
                    and coalesce(period_file_states.status, 'active') = 'active'
                order by upload_batches.period_end desc, upload_batches.period_start desc, upload_batches.created_at desc
                """,
                conn,
            )
        except Exception:
            return _empty_batches()
    if batches.empty:
        return _empty_batches()
    batches = normalize_batch_metadata(batches)
    batches["_source_rank"] = batches["source_type"].map({"upload": 0}).fillna(1).astype(int)
    batches = batches.sort_values(
        ["period_end", "period_start", "_source_rank", "created_at"],
        ascending=[False, False, True, False],
    )
    batches = batches.drop_duplicates(subset=["period_level", "period_key", "source_type"], keep="first")
    return batches[BATCH_COLUMNS].reset_index(drop=True)


def format_beijing_datetime(value: object) -> str:
    """Format stored batch timestamps as Beijing time for selectors."""
    if value is None or pd.isna(value):
        return ""
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return str(value)
    return timestamp.tz_convert(BEIJING_TIMEZONE).strftime("%Y年%m月%d日  %H:%M:%S")


def load_latest_dashboard_items(db_path: Path) -> pd.DataFrame:
    """Load rows from the most recently created successful batch."""
    batches = list_successful_dashboard_batches(db_path)
    if batches.empty:
        return _empty_items()
    return load_dashboard_items_for_batch(db_path, str(batches.iloc[0]["batch_id"]))


def load_dashboard_items_for_batch(db_path: Path, batch_id: str) -> pd.DataFrame:
    db_path = Path(db_path)
    if not db_path.exists():
        return _empty_items()
    init_db(db_path)

    with closing(sqlite3.connect(db_path)) as conn:
        try:
            items = pd.read_sql_query(
                """
                select
                    canonical_items.*,
                    upload_batches.period_start as batch_period_start,
                    upload_batches.period_end as batch_period_end,
                    upload_batches.created_at as batch_created_at,
                    upload_batches.period_level as batch_period_level,
                    upload_batches.period_key as batch_period_key,
                    upload_batches.period_label as batch_period_label,
                    upload_batches.data_start as batch_data_start,
                    upload_batches.data_end as batch_data_end,
                    upload_batches.source_type as batch_source_type
                from canonical_items
                join upload_batches
                    on canonical_items.batch_id = upload_batches.batch_id
                left join period_file_states
                    on period_file_states.period_key = upload_batches.period_start || '|' || upload_batches.period_end
                where upload_batches.status = 'ok'
                    and canonical_items.batch_id = ?
                    and coalesce(period_file_states.status, 'active') = 'active'
                order by canonical_items.rowid
                """,
                conn,
                params=(batch_id,),
            )
        except Exception:
            return _empty_items()
    return _normalize_items(items)


def load_latest_data_quality(db_path: Path) -> pd.DataFrame:
    return _load_latest_auxiliary_table(db_path, "data_quality_items")


def load_latest_review_queue(db_path: Path) -> pd.DataFrame:
    return _load_latest_auxiliary_table(db_path, "review_queue_items")


def load_data_quality_for_batch(db_path: Path, batch_id: str) -> pd.DataFrame:
    return _load_auxiliary_table_for_batch(db_path, "data_quality_items", batch_id)


def load_review_queue_for_batch(db_path: Path, batch_id: str) -> pd.DataFrame:
    return _load_auxiliary_table_for_batch(db_path, "review_queue_items", batch_id)


def load_channel_comparison_for_batch(db_path: Path, batch_id: str) -> pd.DataFrame:
    """Load persisted channel period-over-period comparison for a batch."""
    db_path = Path(db_path)
    if not batch_id or not db_path.exists():
        return _empty_channel_comparison()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            comparison = pd.read_sql_query(
                """
                select *
                from channel_comparison_items
                where batch_id = ?
                order by rowid
                """,
                conn,
                params=(batch_id,),
            ).drop(columns=["batch_id"], errors="ignore")
        except Exception:
            return _empty_channel_comparison()
    normalized = _normalize_channel_comparison(comparison)
    if _has_missing_comparison_metric(normalized):
        rebuilt = build_period_comparison_for_batch(db_path, batch_id)
        if not rebuilt.empty and not _has_missing_comparison_metric(rebuilt):
            return rebuilt
    return normalized


def build_period_comparison_for_batch(db_path: Path, batch_id: str) -> pd.DataFrame:
    """Build period-over-period comparison by period order, not import time."""
    db_path = Path(db_path)
    if not batch_id or not db_path.exists():
        return _empty_channel_comparison()
    current = _batch_metadata(db_path, batch_id)
    current_period_start = str(current.get("period_start", "") or "")
    if not current_period_start:
        return _empty_channel_comparison()
    batches = list_successful_dashboard_batches(db_path)
    previous_batch_id = previous_batch_from_rows(
        batches,
        current_period_start,
        str(current.get("period_level", "") or ""),
        str(current.get("period_key", "") or ""),
    )
    if not previous_batch_id:
        return _empty_channel_comparison()

    current_items = load_dashboard_items_for_batch(db_path, batch_id)
    previous_items = load_dashboard_items_for_batch(db_path, previous_batch_id)
    if current_items.empty or previous_items.empty:
        return _empty_channel_comparison()

    comparison = build_channel_comparison(
        _comparison_summary(current_items),
        _comparison_summary(previous_items),
    )
    return _normalize_channel_comparison(comparison)


def _batch_metadata(db_path: Path, batch_id: str) -> dict[str, str]:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select batch_id, period_start, period_end, created_at, status,
                   period_level, period_key, period_label, data_start, data_end, source_type
            from upload_batches
            where batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
    if row is None:
        return {}
    return {column: "" if value is None else str(value) for column, value in zip(BATCH_COLUMNS, row)}


def build_period_comparison_between_batches(
    db_path: Path,
    current_batch_id: str,
    comparison_batch_id: str,
) -> pd.DataFrame:
    """Build period-over-period comparison for an explicitly selected pair of batches."""
    db_path = Path(db_path)
    if not current_batch_id or not comparison_batch_id or current_batch_id == comparison_batch_id:
        return _empty_channel_comparison()
    if not db_path.exists():
        return _empty_channel_comparison()
    current_items = load_dashboard_items_for_batch(db_path, current_batch_id)
    previous_items = load_dashboard_items_for_batch(db_path, comparison_batch_id)
    if current_items.empty or previous_items.empty:
        return _empty_channel_comparison()
    comparison = build_channel_comparison(
        _comparison_summary(current_items),
        _comparison_summary(previous_items),
    )
    return _normalize_channel_comparison(comparison)


def _load_latest_auxiliary_table(db_path: Path, table_name: str) -> pd.DataFrame:
    batches = list_successful_dashboard_batches(db_path)
    if batches.empty:
        return pd.DataFrame()
    return _load_auxiliary_table_for_batch(db_path, table_name, str(batches.iloc[0]["batch_id"]))


def _load_auxiliary_table_for_batch(db_path: Path, table_name: str, batch_id: str) -> pd.DataFrame:
    db_path = Path(db_path)
    if not db_path.exists():
        return pd.DataFrame()
    allowed = {"data_quality_items", "review_queue_items"}
    if table_name not in allowed:
        return pd.DataFrame()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                f"""
                select {table_name}.*
                from {table_name}
                where {table_name}.batch_id = ?
                order by {table_name}.rowid
                """,
                conn,
                params=(batch_id,),
            ).drop(columns=["batch_id"], errors="ignore")
        except Exception:
            return pd.DataFrame()


def filter_dashboard_items(items: pd.DataFrame, filters: DashboardFilters) -> pd.DataFrame:
    if items.empty:
        return _normalize_items(items)

    filtered = _normalize_items(items)
    if filters.period_start:
        start = pd.to_datetime(filters.period_start, errors="coerce")
        if not pd.isna(start):
            batch_end = pd.to_datetime(filtered["batch_period_end"], errors="coerce")
            filtered = filtered[batch_end.ge(start)]
    if filters.period_end:
        end = pd.to_datetime(filters.period_end, errors="coerce")
        if not pd.isna(end):
            batch_start = pd.to_datetime(filtered["batch_period_start"], errors="coerce")
            filtered = filtered[batch_start.le(end)]

    filtered = _filter_in(filtered, "platform", filters.platforms)
    filtered = _filter_in(filtered, "channel", filters.channels)
    filtered = _filter_in(filtered, "content_category", filters.content_categories)
    filtered = _filter_in(filtered, "category_l3", filters.category_l3)

    query = filters.text_query.strip()
    if query:
        search_columns = ["title", "content_id", "material_id", "account", "account_id", "author", "category_l3"]
        mask = pd.Series(False, index=filtered.index)
        for column in search_columns:
            if column not in filtered.columns:
                continue
            mask = mask | filtered[column].fillna("").astype(str).str.contains(query, case=False, regex=False)
        filtered = filtered[mask]
    return filtered.reset_index(drop=True)


def aggregate_dashboard(items: pd.DataFrame, dimensions: Sequence[str]) -> pd.DataFrame:
    dimensions = [dimension for dimension in dimensions if dimension]
    items = _normalize_items(items)
    for dimension in dimensions:
        if dimension not in items.columns:
            items[dimension] = ""

    columns = [*dimensions, *METRIC_COLUMNS]
    if items.empty:
        return pd.DataFrame(columns=columns)

    if dimensions:
        grouped = (
            items.groupby(dimensions, dropna=False)
            .agg(
                item_count=("content_id", "size"),
                spend=("spend", _sum_or_zero),
                impressions=("impressions", _sum_or_zero),
                clicks=("clicks", _sum_or_zero),
                activations=("activations", _sum_or_zero),
                first_pay_count=("first_pay_count", _sum_or_zero),
            )
            .reset_index()
        )
    else:
        grouped = pd.DataFrame(
            [
                {
                    "item_count": float(len(items)),
                    "spend": _sum_or_zero(items["spend"]),
                    "impressions": _sum_or_zero(items["impressions"]),
                    "clicks": _sum_or_zero(items["clicks"]),
                    "activations": _sum_or_zero(items["activations"]),
                    "first_pay_count": _sum_or_zero(items["first_pay_count"]),
                }
            ]
        )

    grouped = _add_rate_columns(grouped)
    sort_columns = [column for column in ["spend", "activations"] if column in grouped.columns]
    if sort_columns:
        grouped = grouped.sort_values(sort_columns, ascending=[False] * len(sort_columns))
    return grouped[columns].reset_index(drop=True)


def summarize_content_types(items: pd.DataFrame) -> pd.DataFrame:
    """Summarize all content types for the selected rows."""
    normalized = _with_category_display(_normalize_items(items))
    columns = [
        "content_category",
        "category_display",
        "item_count",
        "unique_content_count",
        "spend",
        "impressions",
        "clicks",
        "ctr",
        "activations",
        "activation_cost",
        "first_pay_count",
        "first_pay_cost",
        "first_pay_rate",
        "missing_spend_share",
    ]
    if normalized.empty:
        return pd.DataFrame(columns=columns)

    grouped = (
        normalized.groupby(["content_category", "category_display"], dropna=False)
        .agg(
            item_count=("content_id", "size"),
            unique_content_count=("content_id", _nunique_nonblank),
            spend=("spend", _sum_or_zero),
            impressions=("impressions", _sum_or_zero),
            clicks=("clicks", _sum_or_zero),
            activations=("activations", _sum_or_zero),
            first_pay_count=("first_pay_count", _sum_or_zero),
        )
        .reset_index()
    )
    grouped = _add_rate_columns(grouped)
    total_spend = _sum_or_zero(grouped["spend"])
    grouped["missing_spend_share"] = 0.0
    missing = grouped["category_display"].eq("未匹配")
    if total_spend:
        grouped.loc[missing, "missing_spend_share"] = grouped.loc[missing, "spend"] / total_spend
    return grouped.sort_values(["spend", "activations"], ascending=[False, False])[columns].reset_index(drop=True)


def summarize_unique_content(items: pd.DataFrame) -> pd.DataFrame:
    """Aggregate duplicate platform/video rows into a unique content reading view."""
    normalized = _with_category_display(_normalize_items(items))
    columns = [
        "platform",
        "content_id",
        "title",
        "account",
        "account_id",
        "author",
        "content_category",
        "category_display",
        "channels",
        "channel_count",
        "material_count",
        "item_count",
        "spend",
        "impressions",
        "clicks",
        "ctr",
        "activations",
        "activation_cost",
        "first_pay_count",
        "first_pay_cost",
        "first_pay_rate",
    ]
    if normalized.empty:
        return pd.DataFrame(columns=columns)

    normalized["content_key"] = normalized.apply(_content_key, axis=1)
    grouped = (
        normalized.groupby(["platform", "content_key"], dropna=False)
        .agg(
            content_id=("content_id", _first_non_blank),
            title=("title", _first_non_blank),
            account=("account", _first_non_blank),
            account_id=("account_id", _first_non_blank),
            author=("author", _first_non_blank),
            content_category=("content_category", _first_non_blank),
            category_display=("category_display", _first_non_blank),
            channels=("channel", _join_unique_nonblank),
            channel_count=("channel", _nunique_nonblank),
            material_count=("material_id", _nunique_nonblank),
            item_count=("content_id", "size"),
            spend=("spend", _sum_or_zero),
            impressions=("impressions", _sum_or_zero),
            clicks=("clicks", _sum_or_zero),
            activations=("activations", _sum_or_zero),
            first_pay_count=("first_pay_count", _sum_or_zero),
        )
        .reset_index()
        .drop(columns=["content_key"])
    )
    grouped = _add_rate_columns(grouped)
    return grouped.sort_values(["spend", "activations"], ascending=[False, False])[columns].reset_index(drop=True)


def summarize_content_type_trends(items: pd.DataFrame, period_start: str = "", period_end: str = "") -> pd.DataFrame:
    """Build batch-period trend rows for every content type."""
    filtered = filter_dashboard_items(
        items,
        DashboardFilters(period_start=period_start or "", period_end=period_end or ""),
    )
    normalized = _with_category_display(_normalize_items(filtered))
    columns = [
        "batch_id",
        "batch_period_start",
        "batch_period_end",
        "trend_period",
        "channel",
        "content_category",
        "category_display",
        "item_count",
        "unique_content_count",
        "spend",
        "impressions",
        "clicks",
        "ctr",
        "activations",
        "activation_cost",
        "first_pay_count",
        "first_pay_cost",
        "first_pay_rate",
    ]
    if normalized.empty:
        return pd.DataFrame(columns=columns)

    grouped = (
        normalized.groupby(
            [
                "batch_id",
                "batch_period_start",
                "batch_period_end",
                "channel",
                "content_category",
                "category_display",
            ],
            dropna=False,
        )
        .agg(
            item_count=("content_id", "size"),
            unique_content_count=("content_id", _nunique_nonblank),
            spend=("spend", _sum_or_zero),
            impressions=("impressions", _sum_or_zero),
            clicks=("clicks", _sum_or_zero),
            activations=("activations", _sum_or_zero),
            first_pay_count=("first_pay_count", _sum_or_zero),
        )
        .reset_index()
    )
    grouped = _add_rate_columns(grouped)
    grouped["trend_period"] = grouped["batch_period_start"].astype(str) + " 至 " + grouped["batch_period_end"].astype(str)
    grouped["_sort_period"] = pd.to_datetime(grouped["batch_period_start"], errors="coerce")
    grouped = grouped.sort_values(["_sort_period", "channel", "spend"], ascending=[True, True, False])
    return grouped[columns].reset_index(drop=True)


def summarize_period_metric_trends(
    items: pd.DataFrame,
    batches: pd.DataFrame,
    period_level: str,
    *,
    window_size: int | None = None,
    channels: Sequence[str] = (),
) -> pd.DataFrame:
    """Build one trend row per retained week/month period for key metrics."""
    selected_level = str(period_level or "").strip()
    if selected_level not in {"week", "month"}:
        return _empty_period_metric_trends()
    if batches.empty:
        return _empty_period_metric_trends()

    trend_batches = normalize_batch_metadata(batches)
    if "status" in trend_batches.columns:
        trend_batches = trend_batches[trend_batches["status"].fillna("").astype(str).eq("ok")]
    trend_batches = trend_batches[trend_batches["period_level"].fillna("").astype(str).eq(selected_level)].copy()
    if trend_batches.empty:
        return _empty_period_metric_trends()

    trend_batches["_period_start_dt"] = pd.to_datetime(trend_batches["period_start"], errors="coerce")
    trend_batches["_period_end_dt"] = pd.to_datetime(trend_batches["period_end"], errors="coerce")
    trend_batches = trend_batches[trend_batches["_period_start_dt"].notna() & trend_batches["_period_end_dt"].notna()]
    if trend_batches.empty:
        return _empty_period_metric_trends()

    trend_batches["_source_rank"] = trend_batches["source_type"].map({"upload": 0}).fillna(1).astype(int)
    trend_batches["_created_dt"] = pd.to_datetime(trend_batches.get("created_at", ""), errors="coerce", utc=True)
    trend_batches = trend_batches.sort_values(
        ["_period_end_dt", "_period_start_dt", "_source_rank", "_created_dt"],
        ascending=[False, False, True, False],
    ).drop_duplicates(subset=["period_level", "period_key"], keep="first")
    if window_size is not None and int(window_size) > 0:
        trend_batches = trend_batches.head(int(window_size))
    trend_batches = trend_batches.sort_values(["_period_start_dt", "_period_end_dt", "_created_dt"]).copy()

    normalized = _normalize_items(items)
    normalized = normalized[normalized["batch_id"].isin(set(trend_batches["batch_id"].astype(str)))].copy()
    normalized = _filter_in(normalized, "channel", channels)
    if normalized.empty:
        grouped = pd.DataFrame(columns=["batch_id", "item_count", "spend", "impressions", "clicks", "activations", "first_pay_count"])
    else:
        grouped = (
            normalized.groupby("batch_id", dropna=False)
            .agg(
                item_count=("content_id", "size"),
                spend=("spend", _sum_or_zero),
                impressions=("impressions", _sum_or_zero),
                clicks=("clicks", _sum_or_zero),
                activations=("activations", _sum_or_zero),
                first_pay_count=("first_pay_count", _sum_or_zero),
            )
            .reset_index()
        )

    result = trend_batches[
        ["batch_id", "period_level", "period_key", "period_label", "period_start", "period_end", "source_type"]
    ].merge(grouped, on="batch_id", how="left")
    for column in ["item_count", "spend", "impressions", "clicks", "activations", "first_pay_count"]:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)
    result["trend_period"] = result["period_start"].astype(str) + " 至 " + result["period_end"].astype(str)
    result = _add_rate_columns(result)
    return result[PERIOD_METRIC_TREND_COLUMNS].reset_index(drop=True)


def metric_sort_ascending(metric: str) -> bool:
    """Return True for metrics where lower values are better in rankings."""
    return metric in COST_METRICS


def summarize_dimension_for_metric(
    items: pd.DataFrame,
    dimension: str,
    metric: str,
    top_n: int = 15,
) -> pd.DataFrame:
    """Aggregate one dimension and return the Top N rows for the selected metric."""
    normalized = _normalize_items(items)
    if not dimension or metric not in METRIC_COLUMNS:
        return pd.DataFrame(columns=["category_name", *METRIC_COLUMNS])
    if dimension not in normalized.columns:
        normalized[dimension] = ""

    summary = aggregate_dashboard(normalized, [dimension])
    if summary.empty or metric not in summary.columns:
        return pd.DataFrame(columns=["category_name", *METRIC_COLUMNS])

    summary = summary.rename(columns={dimension: "category_name"})
    summary["category_name"] = summary["category_name"].fillna("").astype(str).str.strip()
    summary = summary[summary["category_name"].ne("")]
    if summary.empty:
        return pd.DataFrame(columns=["category_name", *METRIC_COLUMNS])

    return _sort_metric_summary(summary, metric).head(int(top_n)).reset_index(drop=True)


def summarize_channel_categories(items: pd.DataFrame, channel: str) -> pd.DataFrame:
    """Summarize every nonblank secondary category for one channel."""
    normalized = _normalize_items(items)
    channel_name = _normalize_channel_value(channel)
    columns = ["category_name", *METRIC_COLUMNS]
    if normalized.empty or not channel_name:
        return pd.DataFrame(columns=columns)

    scoped = normalized[normalized["channel"].eq(channel_name)].copy()
    if scoped.empty:
        return pd.DataFrame(columns=columns)

    summary = aggregate_dashboard(scoped, ["category_l2"])
    if summary.empty:
        return pd.DataFrame(columns=columns)
    summary = summary.rename(columns={"category_l2": "category_name"})
    summary["category_name"] = summary["category_name"].fillna("").astype(str).str.strip()
    summary = summary[summary["category_name"].ne("")]
    if summary.empty:
        return pd.DataFrame(columns=columns)
    return _sort_metric_summary(summary, "spend")[columns].reset_index(drop=True)


def summarize_channel_category_comparison(
    current_items: pd.DataFrame,
    previous_items: pd.DataFrame,
    channel: str,
    *,
    top_n: int = 5,
) -> pd.DataFrame:
    """Compare a channel's current Top categories against the previous period."""
    current = summarize_channel_categories(current_items, channel).head(int(top_n)).copy()
    previous = summarize_channel_categories(previous_items, channel)
    return _merge_spend_comparison(current, previous, "category_name")


def compare_channel_topics(current_topics: pd.DataFrame, previous_topics: pd.DataFrame) -> pd.DataFrame:
    """Attach previous-period spend and spend growth to current topic rows."""
    current = current_topics.copy()
    previous = previous_topics.copy()
    if "topic_name" not in current.columns:
        current["topic_name"] = ""
    if "topic_name" not in previous.columns:
        previous["topic_name"] = ""
    return _merge_spend_comparison(current, previous, "topic_name")


def summarize_channel_top_content_links(items: pd.DataFrame, channel: str) -> pd.DataFrame:
    """Return channel-specific spend Top content rows with visible content links."""
    channel_name = _normalize_channel_value(channel)
    limit = 5 if channel_name else 0
    columns = ["title", "spend", "cover_url", "content_url"]
    if limit <= 0:
        return pd.DataFrame(columns=columns)
    normalized = _normalize_items(items)
    if normalized.empty or "channel" not in normalized.columns:
        return pd.DataFrame(columns=columns)
    scoped = normalized[normalized["channel"].eq(channel_name)].copy()
    if scoped.empty:
        return pd.DataFrame(columns=columns)
    scoped["spend"] = pd.to_numeric(scoped["spend"], errors="coerce")
    scoped = scoped.dropna(subset=["spend"])
    if scoped.empty:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in scoped.columns:
            scoped[column] = ""
    return (
        scoped.sort_values("spend", ascending=False, na_position="last")
        .head(limit)[columns]
        .reset_index(drop=True)
    )


def build_top_content_review_queue(
    items: pd.DataFrame,
    *,
    include_auto_passed: bool = False,
    confidence_threshold: float = AI_REVIEW_AUTO_PASS_THRESHOLD,
) -> pd.DataFrame:
    """Return per-period, per-channel Top rows that need human content review."""
    if items.empty:
        return pd.DataFrame(columns=TOP_CONTENT_REVIEW_COLUMNS)
    normalized = _normalize_items(items)
    for column in TOP_CONTENT_REVIEW_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized["spend"] = pd.to_numeric(normalized["spend"], errors="coerce").fillna(0.0)
    normalized["category_confidence"] = pd.to_numeric(normalized["category_confidence"], errors="coerce").fillna(0.0)
    group_columns = ["batch_id", "batch_period_start", "batch_period_end", "platform_group", "channel"]
    groups: list[pd.DataFrame] = []
    for _, group in normalized.groupby(group_columns, dropna=False, sort=True):
        channel = str(group["channel"].iloc[0] or "")
        limit = _channel_top_content_limit(channel)
        if limit <= 0:
            continue
        top = group.sort_values("spend", ascending=False, na_position="last").head(limit).copy()
        top["rank_in_channel"] = range(1, len(top) + 1)
        top["missing_content_url"] = top["content_url"].map(_is_blank_text)
        top["invalid_content_url"] = ~top["missing_content_url"] & ~top["content_url"].map(_is_valid_content_url)
        top["missing_manual_category"] = top["manual_category"].map(_is_blank_text)
        top["missing_content_category"] = top["content_category"].map(_is_blank_text)
        top["low_category_confidence"] = ~top["missing_content_category"] & top["category_confidence"].lt(confidence_threshold)
        top["type_conflict"] = top["match_risk_level"].astype(str).str.strip().ne("") | top["match_risk_reason"].astype(str).str.strip().ne("")
        top["needs_review"] = (
            top["missing_content_url"]
            | top["invalid_content_url"]
            | top["missing_content_category"]
            | top["low_category_confidence"]
            | top["type_conflict"]
        )
        top["ai_review_status"] = top["needs_review"].map(lambda value: "需人工确认" if bool(value) else "自动通过")
        top["audit_flags"] = top.apply(_content_review_flags, axis=1)
        top["ai_review_reason"] = top.apply(_ai_review_reason, axis=1)
        if not include_auto_passed:
            top = top[top["needs_review"]].copy()
        if top.empty:
            continue
        groups.append(top)
    if not groups:
        return pd.DataFrame(columns=TOP_CONTENT_REVIEW_COLUMNS)
    result = pd.concat(groups, ignore_index=True)
    result = result.sort_values(
        ["batch_period_end", "batch_period_start", "platform_group", "channel", "rank_in_channel"],
        ascending=[False, False, True, True, True],
    )
    return result[TOP_CONTENT_REVIEW_COLUMNS].reset_index(drop=True)


def summarize_channel_top_topics(
    items: pd.DataFrame,
    channel: str,
    top_n: int = 20,
    metric: str = "spend",
    topic_labels: Mapping[int, str] | None = None,
) -> pd.DataFrame:
    """Summarize AI/fallback topics from the channel's Top N rows for one metric."""
    normalized = _normalize_items(items)
    channel_name = _normalize_channel_value(channel)
    columns = ["category_name", "topic_name", *METRIC_COLUMNS]
    if normalized.empty or not channel_name or metric not in METRIC_COLUMNS:
        return pd.DataFrame(columns=columns)

    scoped = normalized[normalized["channel"].eq(channel_name)].copy()
    if scoped.empty:
        return pd.DataFrame(columns=columns)

    scoped[metric] = pd.to_numeric(scoped[metric], errors="coerce")
    scoped = scoped.dropna(subset=[metric])
    if scoped.empty:
        return pd.DataFrame(columns=columns)

    top_rows = scoped.sort_values(
        metric,
        ascending=metric_sort_ascending(metric),
        na_position="last",
    ).head(int(top_n)).copy()
    if top_rows.empty:
        return pd.DataFrame(columns=columns)

    top_rows["category_name"] = top_rows["category_l2"].fillna("").astype(str).str.strip()
    top_rows.loc[top_rows["category_name"].eq(""), "category_name"] = "未匹配栏目"
    labels = topic_labels or {}
    top_rows["topic_name"] = [
        _channel_topic_label_for_row(index, row, labels)
        for index, row in top_rows.iterrows()
    ]

    summary = aggregate_dashboard(top_rows, ["category_name", "topic_name"])
    if summary.empty:
        return pd.DataFrame(columns=columns)
    summary["topic_name"] = summary["topic_name"].fillna("").astype(str).str.strip()
    summary.loc[summary["topic_name"].eq(""), "topic_name"] = "未命名题材"
    return _sort_metric_summary(summary, metric).head(int(top_n))[columns].reset_index(drop=True)


def build_channel_top_topic_insights(topic_summary: pd.DataFrame) -> str:
    """Build concise business analysis for a channel's focused topic summary."""
    summary = topic_summary.copy()
    if summary.empty:
        return "#### 重点题材分析结论\n- 当前没有可用于分析的重点题材数据。"

    for column in ["spend", "activations", "activation_cost", "first_pay_count", "first_pay_rate"]:
        if column not in summary.columns:
            summary[column] = 0.0
        summary[column] = pd.to_numeric(summary[column], errors="coerce")
    if "topic_name" not in summary.columns:
        summary["topic_name"] = ""
    summary["topic_name"] = summary["topic_name"].fillna("").astype(str).str.strip()
    summary.loc[summary["topic_name"].eq(""), "topic_name"] = "未命名题材"

    total_spend = _sum_or_zero(summary["spend"])
    top_spend = summary.sort_values("spend", ascending=False).iloc[0]
    top3_spend = _sum_or_zero(summary.sort_values("spend", ascending=False).head(3)["spend"])
    top_activation = summary.sort_values(["activations", "spend"], ascending=[False, False]).iloc[0]
    efficient_rows = summary[summary["activations"].gt(0) & summary["activation_cost"].notna()]
    efficient = efficient_rows.sort_values(["activation_cost", "spend"], ascending=[True, False]).iloc[0] if not efficient_rows.empty else top_activation
    pay_rows = summary[summary["activations"].gt(0) & summary["first_pay_rate"].notna()]
    pay_topic = pay_rows.sort_values(["first_pay_rate", "first_pay_count", "spend"], ascending=[False, False, False]).iloc[0] if not pay_rows.empty else top_activation

    top_share = _safe_ratio(float(top_spend.get("spend", 0) or 0), total_spend)
    top3_share = _safe_ratio(top3_spend, total_spend)
    return "\n".join(
        [
            "#### 重点题材分析结论",
            (
                f"- 预算集中在 **{top_spend['topic_name']}**：消耗 {_fmt_number(top_spend.get('spend'), 0)}，"
                f"占重点内容消耗 {_fmt_percent_text(top_share)}；Top 3 题材合计占 {_fmt_percent_text(top3_share)}。"
            ),
            (
                f"- 拉新贡献最高的是 **{top_activation['topic_name']}**：激活 {_fmt_number(top_activation.get('activations'), 0)}，"
                f"激活成本 {_fmt_number(top_activation.get('activation_cost'), 1)}。"
            ),
            (
                f"- 效率最优的是 **{efficient['topic_name']}**：激活成本 {_fmt_number(efficient.get('activation_cost'), 1)}，"
                f"可作为低成本扩量候选。"
            ),
            (
                f"- 付费转化优先关注 **{pay_topic['topic_name']}**：付费率 {_fmt_percent_text(pay_topic.get('first_pay_rate'))}，"
                f"付费 {_fmt_number(pay_topic.get('first_pay_count'), 0)}。"
            ),
            "建议：下一轮素材不要逐条按标题复投，优先围绕高消耗且能带来拉新的题材扩展相邻选题，同时用低成本题材做小预算测试。",
        ]
    )


def summarize_topics_for_selection(
    items: pd.DataFrame,
    channel: str,
    category_l2: Optional[str],
    metric: str,
    top_n: int = 15,
    topic_labels: Mapping[int, str] | None = None,
) -> pd.DataFrame:
    """Summarize topics after a channel and optional secondary category are selected."""
    normalized = _normalize_items(items)
    if metric not in METRIC_COLUMNS or not channel:
        return pd.DataFrame(columns=["category_name", "topic_name", *METRIC_COLUMNS])

    channel_name = _normalize_channel_value(channel)
    scoped = normalized[normalized["channel"].eq(channel_name)].copy()
    if category_l2:
        scoped = scoped[scoped["category_l2"].eq(str(category_l2).strip())].copy()
    if scoped.empty:
        return pd.DataFrame(columns=["category_name", "topic_name", *METRIC_COLUMNS])

    labels = topic_labels or {}
    scoped["category_name"] = scoped["category_l2"].fillna("").astype(str).str.strip()
    scoped.loc[scoped["category_name"].eq(""), "category_name"] = str(category_l2 or "").strip() or "未匹配栏目"
    scoped["topic_name"] = [
        _topic_label_for_row(index, row, labels)
        for index, row in scoped.iterrows()
    ]

    summary = aggregate_dashboard(scoped, ["category_name", "topic_name"])
    if summary.empty:
        return pd.DataFrame(columns=["category_name", "topic_name", *METRIC_COLUMNS])
    summary["topic_name"] = summary["topic_name"].fillna("").astype(str).str.strip()
    summary.loc[summary["topic_name"].eq(""), "topic_name"] = "未命名题材"
    return _sort_metric_summary(summary, metric).head(int(top_n)).reset_index(drop=True)


def detect_high_metric_anomalies(
    items: pd.DataFrame,
    metric: str,
    top_n: int = 15,
) -> dict[str, pd.DataFrame]:
    """Flag high-impact rows with missing labels or unusually high cost."""
    normalized = _normalize_items(items)
    empty = dashboard_detail_items(normalized.head(0))
    result = {
        "missing_title": empty.copy(),
        "missing_category_l2": empty.copy(),
        "high_cost": empty.copy(),
    }
    if normalized.empty or metric not in normalized.columns:
        return result

    values = pd.to_numeric(normalized[metric], errors="coerce")
    valid_values = values.dropna()
    if valid_values.empty:
        return result

    threshold = valid_values.quantile(0.8)
    high_metric = normalized[values.ge(threshold)].copy()
    if high_metric.empty:
        return result

    title_missing = high_metric["title"].fillna("").astype(str).str.strip().eq("")
    category_missing = high_metric["category_l2"].fillna("").astype(str).str.strip().eq("")
    result["missing_title"] = _sort_anomaly_rows(high_metric[title_missing], metric, top_n)
    result["missing_category_l2"] = _sort_anomaly_rows(high_metric[category_missing], metric, top_n)
    if metric in COST_METRICS:
        result["high_cost"] = _sort_anomaly_rows(high_metric, metric, top_n)
    return result


def build_content_recommendations(
    summary: DashboardSummary,
    platform_summary: pd.DataFrame,
    content_type_summary: pd.DataFrame,
    channel_comparison: Optional[pd.DataFrame] = None,
    external_context: Optional[object] = None,
) -> str:
    """Create direct Markdown recommendations from current KPI and context tables."""
    overview_rows = build_overview_table_rows(summary, platform_summary, channel_comparison)
    total_row = overview_rows[overview_rows["channel"].eq("汇总")].iloc[0] if not overview_rows.empty else pd.Series(dtype=object)
    lines = ["## 内容题材推荐", "## 总体分析"]
    lines.append(
        "- 本周期总览："
        f"总消耗 {_fmt_number(summary.total_spend, 0)}、总曝光 {_fmt_number(summary.total_impressions, 0)}、"
        f"激活数 {_fmt_number(summary.activations, 0)}、激活成本 {_fmt_number(summary.activation_cost, 1)}、"
        f"付费数 {_fmt_number(summary.first_pay_count, 0)}、付费成本 {_fmt_number(summary.first_pay_cost, 1)}。"
    )
    lines.append(
        "- 环比趋势："
        + "，".join(
            [
                _metric_change_clause("总消耗", total_row.get("spend_change_rate"), is_cost=False),
                _metric_change_clause("总曝光", total_row.get("impressions_change_rate"), is_cost=False),
                _metric_change_clause("激活数", total_row.get("activations_change_rate"), is_cost=False),
                _metric_change_clause("激活成本", total_row.get("activation_cost_change_rate"), is_cost=True),
                _metric_change_clause("付费数", total_row.get("first_pay_count_change_rate"), is_cost=False),
                _metric_change_clause("付费成本", total_row.get("first_pay_cost_change_rate"), is_cost=True),
            ]
        )
        + "。"
    )
    lines.append(f"- 原因判断：{_overall_reason(total_row)}")

    external_summary = _external_context_summary(external_context)
    lines.append(f"- 外部背景：{external_summary}")

    clean_types = content_type_summary.copy()
    if "category_display" not in clean_types.columns:
        clean_types["category_display"] = ""
    clean_types = clean_types[~clean_types["category_display"].eq("未匹配")]
    if not clean_types.empty:
        for column in ["spend", "activations", "activation_cost", "first_pay_count", "first_pay_rate"]:
            if column not in clean_types.columns:
                clean_types[column] = 0.0
            clean_types[column] = pd.to_numeric(clean_types[column], errors="coerce")
        top_activation = clean_types.sort_values(["activations", "first_pay_count", "spend"], ascending=[False, False, False]).iloc[0]
        efficient = clean_types[clean_types["activations"].gt(0)].sort_values(
            ["activation_cost", "first_pay_rate"], ascending=[True, False], na_position="last"
        )
        efficient_row = efficient.iloc[0] if not efficient.empty else top_activation
        lines.append(
            f"- 题材侧：**{top_activation['category_display']}** 贡献激活 {_fmt_number(top_activation['activations'], 0)}、"
            f"付费 {_fmt_number(top_activation['first_pay_count'], 0)}；**{efficient_row['category_display']}** "
            f"激活成本 {_fmt_number(efficient_row['activation_cost'], 1)}，适合做低成本扩量测试。"
        )
    else:
        lines.append("- 题材侧：当前有效内容分类不足，先补齐标题、TAG 或人工内容类型后再判断题材扩量。")

    if "category_display" in content_type_summary.columns:
        missing = content_type_summary[content_type_summary["category_display"].eq("未匹配")]
    else:
        missing = pd.DataFrame()
    if not missing.empty:
        missing_spend = _sum_or_zero(missing["spend"])
        lines.append(f"- 数据侧：仍有消耗 {_fmt_number(missing_spend, 0)} 未匹配分类，会影响题材判断。")

    lines.append("## 分渠道分析")
    channel_rows = overview_rows[~overview_rows["channel"].eq("汇总")].copy()
    if channel_rows.empty:
        lines.append("- 当前没有可用于分渠道分析的数据。")
    else:
        for _, row in channel_rows.iterrows():
            lines.append(_channel_recommendation_line(row, clean_types))
    return "\n".join(lines)


def _metric_change_clause(label: str, value: object, *, is_cost: bool) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return f"{label}暂无环比"
    direction = "提升" if float(number) > 0 else "降低" if float(number) < 0 else "持平"
    quality = ""
    if float(number) != 0:
        quality = "（成本承压）" if is_cost and float(number) > 0 else "（效率改善）" if is_cost else ""
    return f"{label}{direction}{_fmt_percent_text(abs(float(number)))}{quality}"


def _overall_reason(total_row: pd.Series) -> str:
    spend = _numeric_change(total_row.get("spend_change_rate"))
    impressions = _numeric_change(total_row.get("impressions_change_rate"))
    activations = _numeric_change(total_row.get("activations_change_rate"))
    activation_cost = _numeric_change(total_row.get("activation_cost_change_rate"))
    first_pay = _numeric_change(total_row.get("first_pay_count_change_rate"))
    first_pay_cost = _numeric_change(total_row.get("first_pay_cost_change_rate"))
    if spend is None and impressions is None and activations is None:
        return "缺少上一周期对比，先以本期各渠道绝对量和成本水平判断。"
    reasons: list[str] = []
    if spend is not None and impressions is not None:
        if spend > 0 and impressions > 0:
            reasons.append("投放规模和曝光同步放大")
        elif spend > 0 and impressions <= 0:
            reasons.append("预算增加但曝光没有同步增长，需检查流量价格或素材覆盖")
        elif spend < 0:
            reasons.append("预算收缩带动整体流量变化")
    if activations is not None:
        if activations > 0:
            reasons.append("激活增长说明承接效率有改善")
        elif activations < 0:
            reasons.append("激活下滑说明素材或落地页转化偏弱")
    if activation_cost is not None:
        reasons.append("激活成本下降" if activation_cost < 0 else "激活成本上升需要控量提效" if activation_cost > 0 else "激活成本稳定")
    if first_pay is not None and first_pay_cost is not None:
        if first_pay > 0 and first_pay_cost <= 0:
            reasons.append("付费数增长且付费成本受控")
        elif first_pay <= 0 and first_pay_cost > 0:
            reasons.append("付费转化不足推高付费成本")
    return "；".join(reasons) + "。" if reasons else "本周期变化较平稳，建议继续观察渠道结构。"


def _channel_recommendation_line(row: pd.Series, clean_types: pd.DataFrame) -> str:
    channel = str(row.get("channel", "") or "").strip() or "未知渠道"
    metric_text = "，".join(
        [
            _metric_change_clause("消耗", row.get("spend_change_rate"), is_cost=False),
            _metric_change_clause("曝光", row.get("impressions_change_rate"), is_cost=False),
            _metric_change_clause("激活", row.get("activations_change_rate"), is_cost=False),
            _metric_change_clause("激活成本", row.get("activation_cost_change_rate"), is_cost=True),
            _metric_change_clause("付费", row.get("first_pay_count_change_rate"), is_cost=False),
            _metric_change_clause("付费成本", row.get("first_pay_cost_change_rate"), is_cost=True),
        ]
    )
    action = "继续看激活成本和付费成本，优先保留低成本且能带来付费的题材。"
    if not clean_types.empty:
        top_topic = clean_types.sort_values(["activations", "spend"], ascending=[False, False]).iloc[0]
        action = f"重点围绕 **{top_topic['category_display']}** 扩展相邻题材，同时压低高成本素材。"
    return f"- **{channel}**：本期消耗 {_fmt_number(row.get('spend'), 0)}、曝光 {_fmt_number(row.get('impressions'), 0)}、激活 {_fmt_number(row.get('activations'), 0)}、付费 {_fmt_number(row.get('first_pay_count'), 0)}；{metric_text}。建议：{action}"


def _external_context_summary(external_context: Optional[object]) -> str:
    if external_context is None:
        return "未取到外部背景，以下判断仅基于站内投放数据。"
    if isinstance(external_context, Mapping):
        summary = str(external_context.get("summary", "") or "").strip()
    else:
        summary = str(getattr(external_context, "summary", "") or "").strip()
    return summary or "未取到外部背景，以下判断仅基于站内投放数据。"


def _numeric_change(value: object) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    return float(number)


def build_overview_table_rows(
    summary: DashboardSummary,
    platform_summary: pd.DataFrame,
    channel_comparison: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Build total-first overview rows with current values and optional period growth."""
    growth_by_channel = _overview_growth_by_channel(channel_comparison)
    rows = [
        _overview_table_row(
            "汇总",
            {
                "spend": summary.total_spend,
                "impressions": summary.total_impressions,
                "activations": summary.activations,
                "activation_cost": summary.activation_cost,
                "first_pay_count": summary.first_pay_count,
                "first_pay_cost": summary.first_pay_cost,
            },
            growth_by_channel.get("总计"),
        )
    ]

    if not platform_summary.empty:
        display = platform_summary.copy()
        name_column = "channel" if "channel" in display.columns else display.columns[0]
        if name_column == "channel":
            display[name_column] = display[name_column].map(normalize_channel_name)
        display["_overview_channel_priority"] = display[name_column].map(_overview_channel_priority)
        display["_overview_channel_order"] = range(len(display))
        display = display.sort_values(
            ["_overview_channel_priority", "_overview_channel_order"],
            kind="stable",
        )
        for _, row in display.iterrows():
            channel = str(row.get(name_column, "")).strip()
            rows.append(
                _overview_table_row(
                    channel,
                    {
                        "spend": row.get("spend", pd.NA),
                        "impressions": row.get("impressions", pd.NA),
                        "activations": row.get("activations", pd.NA),
                        "activation_cost": row.get("activation_cost", pd.NA),
                        "first_pay_count": row.get("first_pay_count", pd.NA),
                        "first_pay_cost": row.get("first_pay_cost", pd.NA),
                    },
                    growth_by_channel.get(channel),
                )
            )

    result = pd.DataFrame(rows)
    for column in OVERVIEW_TABLE_COLUMNS:
        if column not in result.columns:
            result[column] = "" if column == "channel" else pd.NA
    for column in OVERVIEW_TABLE_COLUMNS:
        if column != "channel":
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result[OVERVIEW_TABLE_COLUMNS].reset_index(drop=True)


def _overview_channel_priority(channel: object) -> int:
    name = str(channel or "").strip()
    if "抖音" in name:
        return 0
    if "小红书" in name:
        return 1
    if "B站" in name:
        return 2
    return 3


def build_dashboard_summary(items: pd.DataFrame) -> DashboardSummary:
    items = _normalize_items(items)
    spend = _sum_or_zero(items.get("spend", pd.Series(dtype=float)))
    impressions = _sum_or_zero(items.get("impressions", pd.Series(dtype=float)))
    activations = _sum_or_zero(items.get("activations", pd.Series(dtype=float)))
    first_pay_count = _sum_or_zero(items.get("first_pay_count", pd.Series(dtype=float)))
    return DashboardSummary(
        total_spend=spend,
        total_impressions=impressions,
        activations=activations,
        activation_cost=_safe_ratio(spend, activations),
        first_pay_count=first_pay_count,
        first_pay_cost=_safe_ratio(spend, first_pay_count),
        first_pay_rate=_safe_ratio(first_pay_count, activations),
    )


def dashboard_detail_items(items: pd.DataFrame) -> pd.DataFrame:
    normalized = _normalize_items(items)
    for column in DETAIL_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = _add_rate_columns(normalized)
    return normalized[DETAIL_COLUMNS].sort_values("spend", ascending=False).reset_index(drop=True)


def _normalize_items(items: pd.DataFrame) -> pd.DataFrame:
    normalized = items.copy()
    for column in _empty_items().columns:
        if column not in normalized.columns:
            normalized[column] = ""
    for column in NUMERIC_SOURCE_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0.0)
    for column in [
        "platform",
        "platform_group",
        "channel",
        "content_category",
        "category_l2",
        "category_l3",
        "category_source",
        "review_status",
        "title",
        "content_id",
        "material_id",
        "account",
        "account_id",
        "author",
        "content_url",
        "manual_category",
        "ai_category",
        "review_reasons",
        "ledger_match_source",
        "ledger_content_type",
        "ledger_source_file",
        "ledger_source_sheet",
        "ledger_source_row",
        "match_risk_level",
        "match_risk_reason",
    ]:
        if column not in normalized.columns:
            normalized[column] = ""
        normalized[column] = normalized[column].fillna("").astype(str)
    if "channel" in normalized.columns:
        missing_channel = normalized["channel"].str.strip().eq("")
        normalized.loc[missing_channel, "channel"] = normalized.loc[missing_channel, "platform"]
    if "platform" in normalized.columns:
        missing_platform = normalized["platform"].str.strip().eq("")
        normalized.loc[missing_platform, "platform"] = normalized.loc[missing_platform, "channel"]
    normalized["channel"] = normalized["channel"].map(normalize_channel_name)
    _normalize_social_display_dimensions(normalized)
    normalized["batch_period_start"] = normalized["batch_period_start"].fillna(normalized["period_start"]).astype(str)
    normalized["batch_period_end"] = normalized["batch_period_end"].fillna(normalized["period_end"]).astype(str)
    normalized = _add_rate_columns(normalized)
    return normalized


def _normalize_channel_comparison(comparison: pd.DataFrame) -> pd.DataFrame:
    normalized = comparison.copy()
    for column in CHANNEL_COMPARISON_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = "" if column == "channel" else pd.NA
    normalized["channel"] = normalized["channel"].fillna("").astype(str)
    normalized["channel"] = normalized["channel"].map(normalize_channel_name)
    for column in CHANNEL_COMPARISON_COLUMNS:
        if column == "channel":
            continue
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    return normalized[CHANNEL_COMPARISON_COLUMNS].reset_index(drop=True)


def _has_missing_comparison_metric(comparison: pd.DataFrame) -> bool:
    if comparison.empty:
        return False
    for metric in COMPARISON_METRICS:
        metric_columns = [
            f"{metric}_current",
            f"{metric}_previous",
            f"{metric}_change_rate",
        ]
        if all(pd.to_numeric(comparison.get(column), errors="coerce").isna().all() for column in metric_columns):
            return True
    return False


def _overview_growth_by_channel(channel_comparison: Optional[pd.DataFrame]) -> dict[str, pd.Series]:
    if channel_comparison is None or channel_comparison.empty or "channel" not in channel_comparison.columns:
        return {}
    comparison = channel_comparison.copy()
    comparison["channel"] = comparison["channel"].fillna("").astype(str).map(normalize_channel_name)
    comparison = comparison[comparison["channel"].ne("")]
    return {str(row["channel"]): row for _, row in comparison.iterrows()}


def _overview_table_row(
    channel: str,
    values: Mapping[str, object],
    growth_row: Optional[pd.Series],
) -> dict[str, object]:
    row: dict[str, object] = {"channel": channel}
    for metric in ["spend", "impressions", "activations", "activation_cost", "first_pay_count", "first_pay_cost"]:
        row[metric] = values.get(metric, pd.NA)
        rate_column = f"{metric}_change_rate"
        row[rate_column] = pd.NA if growth_row is None else growth_row.get(rate_column, pd.NA)
    return row


def _batch_period_start(db_path: Path, batch_id: str) -> str:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select period_start
            from upload_batches
            where batch_id = ? and status = 'ok'
            """,
            (batch_id,),
        ).fetchone()
    return "" if row is None or row[0] is None else str(row[0])


def _previous_batch_id_for_period(db_path: Path, period_start: str) -> str:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select upload_batches.batch_id
            from upload_batches
            left join period_file_states
                on period_file_states.period_key = upload_batches.period_start || '|' || upload_batches.period_end
            where upload_batches.status = 'ok'
                and coalesce(period_file_states.status, 'active') = 'active'
                and upload_batches.period_end < ?
            order by upload_batches.period_end desc, upload_batches.period_start desc, upload_batches.created_at desc
            limit 1
            """,
            (period_start,),
        ).fetchone()
    return "" if row is None or row[0] is None else str(row[0])


def _comparison_summary(items: pd.DataFrame) -> pd.DataFrame:
    by_channel = aggregate_dashboard(items, ["channel"])
    total = aggregate_dashboard(items, [])
    if total.empty:
        return by_channel
    total = total.copy()
    total.insert(0, "channel", "总计")
    return pd.concat([by_channel, total], ignore_index=True, sort=False)


def _normalize_social_display_dimensions(items: pd.DataFrame) -> None:
    platform_from_platform = items["platform"].map(social_platform_from_name)
    platform_from_channel = items["channel"].map(social_platform_from_name)
    social_platform = platform_from_platform.where(platform_from_platform.ne(""), platform_from_channel)
    social_mask = social_platform.ne("")
    if not social_mask.any():
        return
    items.loc[social_mask, "platform"] = social_platform[social_mask]
    items.loc[social_mask, "platform_group"] = social_platform[social_mask]
    channel_source = items["channel"].where(items["channel"].str.strip().ne(""), items["platform"])
    items.loc[social_mask, "channel"] = channel_source.loc[social_mask].map(normalize_channel_name)


def _filter_in(items: pd.DataFrame, column: str, values: Iterable[str]) -> pd.DataFrame:
    selected = [_normalize_channel_value(value) if column == "channel" else value for value in values if value]
    if not selected:
        return items
    return items[items[column].isin(selected)]


def _normalize_channel_value(value: object) -> str:
    return normalize_channel_name(value)


def _add_rate_columns(frame: pd.DataFrame) -> pd.DataFrame:
    with_rates = frame.copy()
    with_rates["ctr"] = _safe_divide(with_rates["clicks"], with_rates["impressions"])
    with_rates["activation_cost"] = _safe_divide(with_rates["spend"], with_rates["activations"])
    with_rates["first_pay_cost"] = _safe_divide(with_rates["spend"], with_rates["first_pay_count"])
    with_rates["first_pay_rate"] = _safe_divide(with_rates["first_pay_count"], with_rates["activations"])
    return with_rates


def _topic_label_for_row(index: object, row: pd.Series, topic_labels: Mapping[int, str]) -> str:
    try:
        normalized_index = int(index)
    except (TypeError, ValueError):
        normalized_index = index
    label = str(topic_labels.get(normalized_index, "")).strip()
    if label:
        return _clean_topic_label(label)
    return _fallback_topic_label(row)


def _channel_topic_label_for_row(index: object, row: pd.Series, topic_labels: Mapping[int, str]) -> str:
    try:
        normalized_index = int(index)
    except (TypeError, ValueError):
        normalized_index = index
    label = str(topic_labels.get(normalized_index, "")).strip()
    if label and not _looks_like_single_title(label, row):
        return _clean_topic_label(label)
    return _algorithmic_channel_topic_label(row)


def _algorithmic_channel_topic_label(row: pd.Series) -> str:
    search_text = " ".join(
        str(row.get(column, "") or "").strip()
        for column in ["category_l2", "category_l3", "content_category", "title"]
        if str(row.get(column, "") or "").strip()
    )
    for topic_name, keywords in CHANNEL_TOPIC_KEYWORD_RULES:
        if any(keyword and keyword in search_text for keyword in keywords):
            return topic_name

    for column in ["category_l2", "content_category", "category_l3"]:
        label = str(row.get(column, "") or "").strip()
        if label and not _looks_like_single_title(label, row):
            return _clean_topic_label(label)
    return "未归类题材"


def _looks_like_single_title(label: str, row: pd.Series) -> bool:
    clean = _clean_topic_label(label)
    compact = re.sub(r"\s+", "", clean)
    if not compact or clean == "未命名题材":
        return True
    if len(compact) > 12:
        return True
    title = _compact_text(row.get("title", ""))
    category_l3 = _compact_text(row.get("category_l3", ""))
    if compact and title and compact == title:
        return True
    if compact and category_l3 and compact == category_l3:
        return len(compact) >= 8 or clean != compact or bool(re.search(r"[，,。！？?：:/|《》【】（）()]", clean))
    return False


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _fallback_topic_label(row: pd.Series) -> str:
    category_l3 = str(row.get("category_l3", "") or "").strip()
    if category_l3:
        return _clean_topic_label(category_l3)
    title = str(row.get("title", "") or "").strip()
    if title:
        return _clean_topic_label(title)
    for column in ["content_id", "material_id"]:
        value = str(row.get(column, "") or "").strip()
        if value:
            return f"未命名题材-{value}"
    return "未命名题材"


def _clean_topic_label(label: str) -> str:
    clean = re.sub(r"\s+", " ", str(label).strip())
    clean = re.sub(r"#[^\s#]+", "", clean).strip()
    if not clean:
        return "未命名题材"
    return clean[:28]


def _sort_anomaly_rows(rows: pd.DataFrame, metric: str, top_n: int) -> pd.DataFrame:
    if rows.empty:
        return dashboard_detail_items(rows)
    sorted_rows = rows.copy()
    sorted_rows[metric] = pd.to_numeric(sorted_rows[metric], errors="coerce")
    return dashboard_detail_items(
        sorted_rows.sort_values(metric, ascending=False, na_position="last").head(int(top_n))
    )


def _sort_metric_summary(summary: pd.DataFrame, metric: str) -> pd.DataFrame:
    sort_columns = [metric]
    ascending = [metric_sort_ascending(metric)]
    if metric_sort_ascending(metric):
        for column, direction in [("spend", True), ("activations", False)]:
            if column in summary.columns and column != metric:
                sort_columns.append(column)
                ascending.append(direction)
    else:
        for column in ["activations", "spend"]:
            if column in summary.columns and column != metric:
                sort_columns.append(column)
                ascending.append(False)
    return summary.sort_values(sort_columns, ascending=ascending, na_position="last")


def _merge_spend_comparison(current: pd.DataFrame, previous: pd.DataFrame, key_column: str) -> pd.DataFrame:
    result = current.copy()
    if key_column not in result.columns:
        result[key_column] = ""
    if result.empty:
        result["spend_previous"] = pd.Series(dtype="Float64")
        result["spend_change_rate"] = pd.Series(dtype="Float64")
        return result
    if previous.empty or key_column not in previous.columns or "spend" not in previous.columns:
        result["spend_previous"] = pd.NA
    else:
        previous_spend = previous[[key_column, "spend"]].copy()
        previous_spend[key_column] = previous_spend[key_column].fillna("").astype(str).str.strip()
        previous_spend["spend_previous"] = pd.to_numeric(previous_spend["spend"], errors="coerce")
        previous_spend = previous_spend[[key_column, "spend_previous"]].drop_duplicates(
            subset=[key_column],
            keep="first",
        )
        result[key_column] = result[key_column].fillna("").astype(str).str.strip()
        result = result.merge(previous_spend, on=key_column, how="left")
    result["spend"] = pd.to_numeric(result.get("spend"), errors="coerce")
    result["spend_previous"] = pd.to_numeric(result["spend_previous"], errors="coerce")
    result["spend_change_rate"] = _change_rate(result["spend"], result["spend_previous"])
    return result


def _change_rate(current: pd.Series, previous: pd.Series) -> pd.Series:
    current_values = pd.to_numeric(current, errors="coerce").astype(float)
    previous_values = pd.to_numeric(previous, errors="coerce").astype(float)
    result = pd.Series(pd.NA, index=current.index, dtype="Float64")
    mask = previous_values.notna() & previous_values.ne(0.0)
    result.loc[mask] = (current_values.loc[mask] - previous_values.loc[mask]) / previous_values.loc[mask]
    return result


def _channel_top_content_limit(channel: str) -> int:
    name = str(channel or "").strip()
    if "抖音" in name:
        return 20
    if "小红书" in name:
        return 10
    if "B站" in name:
        return 5
    return 10


def _is_blank_text(value: object) -> bool:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    return text.lower() in {"", "nan", "none", "null", "<na>"}


def _is_valid_content_url(value: object) -> bool:
    text = "" if value is None or pd.isna(value) else str(value).strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def _content_review_flags(row: pd.Series) -> str:
    flags = []
    if bool(row.get("missing_content_url", False)):
        flags.append("缺链接")
    if bool(row.get("invalid_content_url", False)):
        flags.append("链接格式异常")
    if bool(row.get("missing_content_category", False)):
        flags.append("缺内容类型")
    if bool(row.get("low_category_confidence", False)):
        flags.append("低置信")
    if bool(row.get("type_conflict", False)):
        flags.append("类型/台账冲突")
    return "；".join(flags) if flags else "AI自动通过"


def _ai_review_reason(row: pd.Series) -> str:
    flags = str(row.get("audit_flags", "") or "").strip()
    if flags and flags != "AI自动通过":
        return flags
    confidence = pd.to_numeric(pd.Series([row.get("category_confidence", 0.0)]), errors="coerce").iloc[0]
    if pd.isna(confidence):
        confidence = 0.0
    return f"置信度达标（{float(confidence):.2f}）且链接完整"


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce").astype(float)
    denominator = pd.to_numeric(denominator, errors="coerce").astype(float)
    result = pd.Series(pd.NA, index=numerator.index, dtype="Float64")
    mask = denominator.ne(0.0)
    result.loc[mask] = numerator.loc[mask] / denominator.loc[mask]
    return result


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _sum_or_zero(series: pd.Series) -> float:
    total = pd.to_numeric(series, errors="coerce").sum(min_count=1)
    return 0.0 if pd.isna(total) else float(total)


def _with_category_display(items: pd.DataFrame) -> pd.DataFrame:
    with_display = items.copy()
    with_display["category_display"] = with_display["content_category"].fillna("").astype(str).str.strip()
    with_display.loc[with_display["category_display"].eq(""), "category_display"] = "未匹配"
    return with_display


def _content_key(row: pd.Series) -> str:
    for column in ["content_id", "material_id", "title"]:
        value = str(row.get(column, "")).strip()
        if value:
            return value
    return f"row-{row.name}"


def _first_non_blank(series: pd.Series) -> str:
    for value in series:
        text = "" if pd.isna(value) else str(value).strip()
        if text:
            return text
    return ""


def _join_unique_nonblank(series: pd.Series) -> str:
    values: list[str] = []
    for value in series:
        text = "" if pd.isna(value) else str(value).strip()
        if text and text not in values:
            values.append(text)
    return "、".join(values)


def _nunique_nonblank(series: pd.Series) -> int:
    values = series.fillna("").astype(str).str.strip()
    return int(values[values.ne("")].nunique())


def _join_non_blank(values: Iterable[object]) -> str:
    tokens = []
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            tokens.append(text)
    return " / ".join(tokens)


def _fmt_number(value: object, decimals: int) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "暂无"
    if decimals <= 0:
        return f"{float(number):,.0f}"
    text = f"{float(number):,.{decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_percent_text(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "暂无"
    text = f"{float(number) * 100:.1f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _empty_items() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "batch_id",
            "period_start",
            "period_end",
            "batch_period_start",
            "batch_period_end",
            "batch_created_at",
            "platform",
            "platform_group",
            "channel",
            "content_id",
            "material_id",
            "title",
            "account_id",
            "account",
            "author",
            "content_url",
            "manual_category",
            "ai_category",
            "category_l2",
            "category_source",
            "category_confidence",
            "review_status",
            "review_reasons",
            "content_category",
            "ledger_match_source",
            "ledger_content_type",
            "ledger_source_file",
            "ledger_source_sheet",
            "ledger_source_row",
            "match_risk_level",
            "match_risk_reason",
            "spend",
            "impressions",
            "clicks",
            "activations",
            "first_pay_count",
            "source_file",
        ]
    )


def _empty_period_metric_trends() -> pd.DataFrame:
    return pd.DataFrame(columns=PERIOD_METRIC_TREND_COLUMNS)


def _empty_batches() -> pd.DataFrame:
    return pd.DataFrame(columns=BATCH_COLUMNS)


def _empty_channel_comparison() -> pd.DataFrame:
    return pd.DataFrame(columns=CHANNEL_COMPARISON_COLUMNS)
