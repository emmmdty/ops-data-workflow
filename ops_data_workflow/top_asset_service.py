"""Top content pool and asset-analysis queue inputs."""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

import pandas as pd

from .title_matching import normalized_title_key


HIGH_SPEND_ABSOLUTE_THRESHOLD = 2000.0
HIGH_IMPRESSIONS_ABSOLUTE_THRESHOLD = 100000.0
TARGET_TOP_PLATFORMS = {"抖音", "小红书", "B站"}
DEFAULT_NUMERIC_COLUMNS = ["spend", "impressions", "clicks", "activations", "first_pay_count"]
DEFAULT_STANDARD_COLUMNS = [
    "platform",
    "platform_group",
    "channel",
    "period_start",
    "period_end",
    "content_id",
    "material_id",
    "title",
    "account",
    "cover_url",
    "content_url",
    "source_time",
    "category_l1",
    "category_l2",
    "category_l3",
    "manual_category",
    "ai_category",
    "content_category",
    "spend",
    "impressions",
    "clicks",
    "activations",
    "first_pay_count",
    "activation_cost",
    "first_pay_cost",
    "ctr",
    "merged_row_count",
]


def build_high_spend_content_pool(
    canonical: pd.DataFrame,
    *,
    standard_columns: list[str] | None = None,
    numeric_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Build per-period/channel high-spend content after identity aggregation."""
    standard_columns = list(standard_columns or DEFAULT_STANDARD_COLUMNS)
    numeric_columns = list(numeric_columns or DEFAULT_NUMERIC_COLUMNS)
    if canonical.empty:
        return _empty_high_spend_pool(standard_columns)
    frame = canonical.copy()
    for column in [
        "period_start",
        "period_end",
        "platform",
        "platform_group",
        "channel",
        "content_id",
        "material_id",
        "title",
        "account",
        "content_url",
    ]:
        if column not in frame.columns:
            frame[column] = ""
    for column in numeric_columns + ["activation_cost", "first_pay_cost", "ctr"]:
        if column not in frame.columns:
            frame[column] = 0.0
    frame["spend"] = pd.to_numeric(frame["spend"], errors="coerce").fillna(0.0)
    frame["content_identity_key"] = frame.apply(high_spend_content_identity_key, axis=1)
    grouped_rows: list[dict[str, object]] = []
    group_columns = ["period_start", "period_end", "channel", "content_identity_key"]
    for _, group in frame.groupby(group_columns, dropna=False, sort=False):
        sorted_group = group.sort_values("spend", ascending=False)
        lead = sorted_group.iloc[0]
        row: dict[str, object] = {}
        for column in frame.columns:
            if column in numeric_columns:
                row[column] = _sum_or_zero(group[column])
            elif column in {"activation_cost", "first_pay_cost", "ctr"}:
                row[column] = lead.get(column, pd.NA)
            elif column == "merged_row_count":
                row[column] = int(
                    pd.to_numeric(group.get(column, pd.Series([], dtype=float)), errors="coerce")
                    .fillna(1)
                    .sum()
                )
            else:
                row[column] = _first_non_blank_from_series(sorted_group[column]) if column in sorted_group.columns else ""
        row["merged_row_count"] = max(int(row.get("merged_row_count") or 0), int(len(group)))
        row["content_identity_key"] = lead.get("content_identity_key", "")
        grouped_rows.append(row)
    aggregated = pd.DataFrame(grouped_rows)
    if aggregated.empty:
        return _empty_high_spend_pool(standard_columns)
    aggregated["_top_platform_label"] = aggregated.apply(_platform_label, axis=1)
    aggregated = aggregated[aggregated["_top_platform_label"].isin(TARGET_TOP_PLATFORMS)].copy()
    if aggregated.empty:
        return _empty_high_spend_pool(standard_columns)
    aggregated["spend"] = pd.to_numeric(aggregated["spend"], errors="coerce").fillna(0.0)
    aggregated["rank_in_channel"] = (
        aggregated.groupby(["period_start", "period_end", "channel"], dropna=False)["spend"]
        .rank(method="first", ascending=False)
        .astype("Int64")
    )
    aggregated["impressions"] = pd.to_numeric(aggregated["impressions"], errors="coerce").fillna(0.0)
    aggregated["impressions_rank_in_channel"] = (
        aggregated.groupby(["period_start", "period_end", "channel"], dropna=False)["impressions"]
        .rank(method="first", ascending=False)
        .astype("Int64")
    )
    aggregated["channel_top_limit"] = aggregated["channel"].map(_spend_top_limit).astype("int64")
    aggregated["channel_exposure_top_limit"] = aggregated["channel"].map(_exposure_top_limit).astype("int64")
    top_mask = aggregated["rank_in_channel"].le(aggregated["channel_top_limit"])
    exposure_top_mask = aggregated["impressions_rank_in_channel"].le(aggregated["channel_exposure_top_limit"])
    spend_threshold_mask = aggregated["spend"].gt(HIGH_SPEND_ABSOLUTE_THRESHOLD)
    impressions_threshold_mask = aggregated["impressions"].gt(HIGH_IMPRESSIONS_ABSOLUTE_THRESHOLD)
    result = aggregated[top_mask | exposure_top_mask | spend_threshold_mask | impressions_threshold_mask].copy()
    if result.empty:
        return _empty_high_spend_pool(standard_columns)
    result["high_spend_reason"] = [
        _high_spend_reason(rank, spend_limit, exposure_rank, exposure_limit, spend, impressions)
        for rank, spend_limit, exposure_rank, exposure_limit, spend, impressions in zip(
            result["rank_in_channel"],
            result["channel_top_limit"],
            result["impressions_rank_in_channel"],
            result["channel_exposure_top_limit"],
            result["spend"],
            result["impressions"],
        )
    ]
    result["missing_high_spend_link"] = result["content_url"].map(_is_blank)
    result = result.sort_values(
        ["period_end", "period_start", "channel", "rank_in_channel", "spend"],
        ascending=[False, False, True, True, False],
    ).drop(columns=["_top_platform_label"], errors="ignore").reset_index(drop=True)
    return result


def build_executable_top_content_pool(
    canonical: pd.DataFrame,
    *,
    standard_columns: list[str] | None = None,
    numeric_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Build the Top pool that is safe to send into asset capture or analysis."""
    display_pool = build_high_spend_content_pool(
        canonical,
        standard_columns=standard_columns,
        numeric_columns=numeric_columns,
    )
    has_analysis_status = "analysis_status" in display_pool.columns
    has_match_status = "match_status" in display_pool.columns
    if not has_analysis_status and not has_match_status:
        return display_pool.iloc[0:0].copy()

    executable_mask = pd.Series(False, index=display_pool.index)
    if has_analysis_status:
        executable_mask |= display_pool["analysis_status"].fillna("").astype(str).str.strip().eq("可分析")
    if has_match_status:
        executable_mask |= display_pool["match_status"].fillna("").astype(str).str.strip().eq("已匹配")
    return display_pool[executable_mask].copy().reset_index(drop=True)


def high_spend_content_identity_key(row: pd.Series) -> str:
    """Return the stable content identity key used by the Top asset pool."""
    channel = _clean_identity_part(row.get("channel", ""))
    platform = _platform_label(row)
    content_id = _clean_identifier(row.get("content_id", ""))
    material_id = _clean_identifier(row.get("material_id", ""))
    content_url = _normalize_identity_url(row.get("content_url", ""))
    title_key = normalized_title_key(row.get("title", ""))
    account = _clean_identity_part(row.get("account", ""))
    if platform == "小红书":
        note_id = _extract_xhs_identity_id(content_id or material_id or content_url)
        if note_id:
            return f"{channel}::小红书::id::{note_id}"
    if platform == "B站":
        bvid = _extract_bilibili_identity_id(content_id or material_id or content_url)
        if bvid:
            return f"{channel}::B站::id::{bvid}"
    if platform == "抖音":
        work_id = _clean_identifier(row.get("work_id", ""))
        work_url = _normalize_identity_url(row.get("work_url", "")) or content_url
        standard_title_key = normalized_title_key(row.get("standard_title", "")) or title_key
        douyin_id = _extract_douyin_identity_id(work_id or work_url)
        if douyin_id:
            return f"{channel}::抖音::id::{douyin_id}"
        if work_url:
            return f"{channel}::抖音::url::{work_url}"
        if standard_title_key:
            return f"{channel}::抖音::title_account::{account}::{standard_title_key}"
    if content_id:
        return f"{channel}::{platform}::id::{content_id}"
    if content_url:
        return f"{channel}::{platform}::url::{content_url}"
    if title_key:
        return f"{channel}::{platform}::title_account::{account}::{title_key}"
    return f"{channel}::{platform}::row::{row.name}"


def _empty_high_spend_pool(standard_columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        columns=standard_columns
        + [
            "content_identity_key",
            "rank_in_channel",
            "impressions_rank_in_channel",
            "channel_top_limit",
            "channel_exposure_top_limit",
            "high_spend_reason",
            "missing_high_spend_link",
        ]
    )


def _platform_label(row: pd.Series) -> str:
    text = " ".join(_clean_identifier(row.get(column, "")) for column in ["platform", "platform_group", "channel"])
    if "小红书" in text or "xiaohongshu" in text.lower() or "xhs" in text.lower():
        return "小红书"
    if "B站" in text or "bilibili" in text.lower():
        return "B站"
    if "抖音" in text or "douyin" in text.lower():
        return "抖音"
    return _clean_identifier(row.get("platform_group", "")) or _clean_identifier(row.get("platform", ""))


def _spend_top_limit(channel: object) -> int:
    name = _clean_identifier(channel)
    if "抖音" in name:
        return 20
    if "小红书" in name or "B站" in name:
        return 10
    return 10


def _exposure_top_limit(channel: object) -> int:
    name = _clean_identifier(channel)
    if "抖音" in name:
        return 20
    if "小红书" in name or "B站" in name:
        return 10
    return 10


def _high_spend_reason(rank: object, limit: object, exposure_rank: object, exposure_limit: object, spend: object, impressions: object) -> str:
    reasons: list[str] = []
    try:
        rank_value = int(rank)
        limit_value = int(limit)
    except Exception:
        rank_value = 0
        limit_value = 0
    try:
        exposure_rank_value = int(exposure_rank)
        exposure_limit_value = int(exposure_limit)
    except Exception:
        exposure_rank_value = 0
        exposure_limit_value = 0
    spend_value = _parse_number(spend)
    impressions_value = _parse_number(impressions)
    if rank_value and limit_value and rank_value <= limit_value:
        reasons.append(f"分渠道消耗Top{limit_value}")
    if exposure_rank_value and exposure_limit_value and exposure_rank_value <= exposure_limit_value:
        reasons.append(f"分渠道曝光Top{exposure_limit_value}")
    if not pd.isna(spend_value) and spend_value > HIGH_SPEND_ABSOLUTE_THRESHOLD:
        reasons.append("单条消耗>2000元")
    if not pd.isna(impressions_value) and impressions_value > HIGH_IMPRESSIONS_ABSOLUTE_THRESHOLD:
        reasons.append("单条曝光>100000")
    return "；".join(reasons)


def _first_non_blank_from_series(series: pd.Series) -> object:
    for value in series:
        if not _is_blank(value):
            return value
    return ""


def _sum_or_zero(series: pd.Series) -> float:
    return float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum())


def _parse_number(value: object) -> float:
    try:
        return float(pd.to_numeric(value, errors="coerce"))
    except Exception:
        return float("nan")


def _clean_identity_part(value: object) -> str:
    text = _clean_identifier(value)
    return text or "未知渠道"


def _clean_identifier(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _normalize_identity_url(value: object) -> str:
    text = _clean_identifier(value)
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return text
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def _extract_xhs_identity_id(value: object) -> str:
    text = _clean_identifier(value)
    match = re.search(r"/(?:item|explore)/([^?/#\s]+)", text)
    if match:
        return match.group(1)
    return text.strip() if text.strip() and "/" not in text else ""


def _extract_bilibili_identity_id(value: object) -> str:
    match = re.search(r"(BV[0-9A-Za-z]+)", _clean_identifier(value))
    return match.group(1) if match else ""


def _extract_douyin_identity_id(value: object) -> str:
    text = _clean_identifier(value)
    match = re.search(r"(?<!\d)(\d{16,20})(?!\d)", text)
    return match.group(1) if match else ""
