"""Attribution and coverage tables for matched and unmatched content."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class AttributionTables:
    coverage_summary: pd.DataFrame
    matched_breakdown: pd.DataFrame
    unmatched_breakdown: pd.DataFrame


COVERAGE_COLUMNS = [
    "scope",
    "item_count",
    "spend",
    "impressions",
    "item_share_of_total",
    "spend_share_of_total",
    "denominator",
]

MATCHED_BREAKDOWN_COLUMNS = [
    "platform",
    "channel",
    "account",
    "primary_type",
    "secondary_type",
    "item_count",
    "spend",
    "impressions",
    "activations",
    "first_pay_count",
    "spend_share_of_total",
    "spend_share_of_matched",
]

UNMATCHED_BREAKDOWN_COLUMNS = [
    "platform",
    "channel",
    "unmatched_reason",
    "field_gap",
    "attribution_type",
    "item_count",
    "spend",
    "impressions",
    "spend_share_of_total",
    "spend_share_of_unmatched",
]


def build_attribution_tables(canonical: pd.DataFrame) -> AttributionTables:
    frame = _prepare(canonical)
    total_count = len(frame)
    total_spend = float(frame["spend"].sum())
    total_impressions = float(frame["impressions"].sum())
    matched_mask = _matched_mask(frame)
    pending_mask = frame["analysis_status"].eq("待补全")
    unmatched_mask = (~matched_mask) & (~pending_mask)
    coverage = pd.DataFrame(
        [
            _coverage_row("全量投放", frame, total_count, total_spend, denominator="分母=全量投放"),
            _coverage_row("飞书已匹配", frame[matched_mask], total_count, total_spend, denominator="分母=全量投放"),
            _coverage_row("飞书未匹配", frame[unmatched_mask], total_count, total_spend, denominator="分母=全量投放"),
            _coverage_row("待补齐", frame[pending_mask], total_count, total_spend, denominator="分母=全量投放"),
        ],
        columns=COVERAGE_COLUMNS,
    )
    return AttributionTables(
        coverage_summary=coverage,
        matched_breakdown=_matched_breakdown(frame[matched_mask], total_spend),
        unmatched_breakdown=_unmatched_breakdown(frame[~matched_mask], total_spend),
    )


def _prepare(canonical: pd.DataFrame) -> pd.DataFrame:
    frame = canonical.copy()
    for column in [
        "platform",
        "channel",
        "account",
        "matched_account",
        "match_status",
        "analysis_status",
        "unanalyzable_reason",
        "category_l1",
        "category_l2",
        "matched_content_type",
        "content_category",
        "manual_category",
        "metadata_content_type_candidate",
        "metadata_tags",
        "title",
        "content_url",
        "content_id",
        "work_id",
    ]:
        if column not in frame.columns:
            frame[column] = ""
        frame[column] = frame[column].fillna("").astype(str)
    for column in ["spend", "impressions", "activations", "first_pay_count"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return frame


def _matched_mask(frame: pd.DataFrame) -> pd.Series:
    return frame["match_status"].eq("已匹配") | frame["analysis_status"].eq("可分析")


def _coverage_row(
    scope: str,
    rows: pd.DataFrame,
    total_count: int,
    total_spend: float,
    *,
    denominator: str,
) -> dict[str, object]:
    spend = float(rows["spend"].sum()) if not rows.empty else 0.0
    return {
        "scope": scope,
        "item_count": int(len(rows)),
        "spend": spend,
        "impressions": float(rows["impressions"].sum()) if not rows.empty else 0.0,
        "item_share_of_total": _ratio(len(rows), total_count),
        "spend_share_of_total": _ratio(spend, total_spend),
        "denominator": denominator,
    }


def _matched_breakdown(rows: pd.DataFrame, total_spend: float) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=MATCHED_BREAKDOWN_COLUMNS)
    scoped = rows.copy()
    scoped["account_display"] = _first_nonblank_columns(scoped, ["matched_account", "account"])
    scoped["primary_type"] = _first_nonblank_columns(scoped, ["category_l1", "content_category", "manual_category"])
    scoped["secondary_type"] = _first_nonblank_columns(scoped, ["category_l2", "matched_content_type", "content_category", "manual_category"])
    matched_spend = float(scoped["spend"].sum())
    grouped = (
        scoped.groupby(["platform", "channel", "account_display", "primary_type", "secondary_type"], dropna=False, sort=False)
        .agg(
            item_count=("platform", "size"),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
        )
        .reset_index()
        .rename(columns={"account_display": "account"})
    )
    grouped["spend_share_of_total"] = grouped["spend"].map(lambda value: _ratio(value, total_spend))
    grouped["spend_share_of_matched"] = grouped["spend"].map(lambda value: _ratio(value, matched_spend))
    return grouped[MATCHED_BREAKDOWN_COLUMNS]


def _unmatched_breakdown(rows: pd.DataFrame, total_spend: float) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=UNMATCHED_BREAKDOWN_COLUMNS)
    scoped = rows.copy()
    scoped["unmatched_reason"] = scoped["unanalyzable_reason"].where(scoped["unanalyzable_reason"].str.strip().ne(""), "未匹配飞书自有内容")
    scoped["field_gap"] = scoped.apply(_field_gap, axis=1)
    scoped["attribution_type"] = scoped.apply(_attribution_type, axis=1)
    unmatched_spend = float(scoped["spend"].sum())
    grouped = (
        scoped.groupby(["platform", "channel", "unmatched_reason", "field_gap", "attribution_type"], dropna=False, sort=False)
        .agg(
            item_count=("platform", "size"),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
        )
        .reset_index()
    )
    grouped["spend_share_of_total"] = grouped["spend"].map(lambda value: _ratio(value, total_spend))
    grouped["spend_share_of_unmatched"] = grouped["spend"].map(lambda value: _ratio(value, unmatched_spend))
    return grouped[UNMATCHED_BREAKDOWN_COLUMNS]


def _first_nonblank_columns(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series([""] * len(frame), index=frame.index, dtype=object)
    for column in columns:
        values = frame[column].fillna("").astype(str).str.strip()
        mask = result.astype(str).str.strip().eq("") & values.ne("")
        result.loc[mask] = values.loc[mask]
    return result


def _field_gap(row: pd.Series) -> str:
    gaps: list[str] = []
    if not _text(row.get("title")):
        gaps.append("缺标题")
    if not (_text(row.get("content_url")) or _text(row.get("work_url"))):
        gaps.append("缺作品链接")
    if not (_text(row.get("content_id")) or _text(row.get("work_id"))):
        gaps.append("缺作品ID")
    if not _text(row.get("account")):
        gaps.append("缺账号")
    return "、".join(gaps) if gaps else "字段完整"


def _attribution_type(row: pd.Series) -> str:
    explicit = _text(row.get("metadata_content_type_candidate")) or _text(row.get("content_category")) or _text(row.get("manual_category"))
    if explicit:
        return explicit
    text = " ".join(
        _text(row.get(column))
        for column in ["title", "metadata_tags", "category_l1", "category_l2"]
    )
    if "二创" in text or "混剪" in text or "再创作" in text:
        return "二创"
    if "代理" in text or "达人" in text or "代投" in text:
        return "代理"
    if "盘点" in text:
        return "盘点"
    if "图文" in text:
        return "图文"
    return "待归因"


def _ratio(numerator: object, denominator: object) -> float:
    try:
        den = float(denominator)
        if den == 0:
            return 0.0
        return float(numerator) / den
    except Exception:
        return 0.0


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
