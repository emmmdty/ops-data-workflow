"""Excel ingestion, normalization, category completion, and scoring."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
import re
from typing import Callable, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse, urlunparse

import pandas as pd

from .account_filters import apply_account_filters, load_account_filter_config
from .categories import HIGH_SPEND_CATEGORY_RULES, category_from_tags, load_category_rules, suggest_category
from .content_ledger import account_match_label, apply_content_ledger, load_content_ledger
from .field_mapping import load_field_mapping, standardize_content_form, standardize_content_type
from .reference_tables import (
    ReferenceTables,
    account_mapping_lookup,
    load_reference_tables,
)
from .title_matching import normalized_title_key
from .source_channels import SOCIAL_PLATFORM_GROUP, normalize_channel_name, platform_from_channel_or_name, social_platform_from_name


TABULAR_SUFFIXES = {".csv", ".xls", ".xlsx"}

STANDARD_COLUMNS = [
    "platform",
    "platform_group",
    "channel",
    "period_start",
    "period_end",
    "content_id",
    "content_id_fallback",
    "material_id",
    "title",
    "account_raw",
    "account_id",
    "account",
    "account_mapping_source",
    "account_normalized",
    "account_filter_status",
    "account_filter_reason",
    "author",
    "cover_url",
    "content_url",
    "source_time",
    "duration",
    "category_l1",
    "category_l2",
    "category_l3",
    "category_source",
    "category_l2_source",
    "category_confidence",
    "review_status",
    "content_form",
    "primary_category",
    "manual_category",
    "ai_category",
    "content_category",
    "category_status",
    "spend",
    "impressions",
    "clicks",
    "activations",
    "first_pay_count",
    "activation_cost",
    "first_pay_cost",
    "ctr",
    "activation_rate",
    "first_pay_rate",
    "activation_cost_raw",
    "first_pay_cost_raw",
    "ctr_raw",
    "activation_rate_raw",
    "first_pay_rate_raw",
    "likes",
    "comments",
    "favorites",
    "follows",
    "dedupe_key",
    "merged_row_count",
    "conflict_details",
    "needs_manual_review",
    "review_reasons",
    "source_file",
    "source_sheet",
    "source_row",
    "source_file_hash",
    "duplicate_group_id",
    "review_action",
]

INTERNAL_COMPAT_COLUMNS = {"platform", "platform_group"}
NUMERIC_COLUMNS = ["spend", "impressions", "clicks", "activations", "first_pay_count"]
REVIEW_QUEUE_TOP_N = 20
REVIEW_QUEUE_HIGH_SPEND_THRESHOLD = 2000.0
REVIEW_QUEUE_CRITICAL_FIELDS = [
    ("content_url", "内容链接"),
    ("content_id", "内容ID"),
    ("title", "标题"),
    ("content_category", "内容类型"),
]
REVIEW_QUEUE_HIGH_RISK_PATTERNS = [
    "同ID标题冲突",
    "标题冲突",
    "同标题多链接",
    "投稿台账多候选",
    "内容类型冲突",
    "类型/台账冲突",
    "内容ID冲突",
    "高风险",
]


@dataclass(frozen=True)
class AnalysisData:
    canonical: pd.DataFrame
    category_summary: pd.DataFrame
    channel_summary: pd.DataFrame
    platform_summary: pd.DataFrame
    platform_category_summary: pd.DataFrame
    total_summary: pd.DataFrame
    raw_category_stats: pd.DataFrame
    pending_categories: pd.DataFrame
    account_audit: pd.DataFrame
    top_content_items: pd.DataFrame
    cover_metrics: pd.DataFrame
    data_quality: pd.DataFrame
    review_queue: pd.DataFrame
    preprocessing_report: pd.DataFrame
    duplicate_merge_details: pd.DataFrame
    conflict_retention_details: pd.DataFrame
    missing_value_details: pd.DataFrame
    account_filter_rules: pd.DataFrame
    account_filter_details: pd.DataFrame
    reference_tables: ReferenceTables


EXPECTED_ACCOUNTS: Dict[str, List[str]] = {
    "小红书": ["同花顺投资", "同顺股民社区", "同花顺理财", "同顺财经", "问财", "喵懂投资"],
    "抖音": [
        "同花顺投资",
        "同花顺股民社区",
        "同花顺财富",
        "同花顺财经",
        "同花顺问财",
        "喵懂投资",
        "同花顺期货通",
    ],
    "B站": ["同花顺投资"],
}

CategoryMatcher = Callable[[pd.DataFrame, list[str], Optional[Path]], Mapping[int, object]]
CategoryMappings = Mapping[str, Mapping[str, str]]


def analyze_input_dir(
    input_dir: Path,
    period_start: str,
    period_end: str,
    category_rules_path: Optional[Path] = None,
    *,
    env_path: Optional[Path] = None,
    category_matcher: Optional[CategoryMatcher] = None,
    category_mappings: Optional[CategoryMappings] = None,
    reference_tables_path: Optional[Path] = None,
    account_filters_path: Optional[Path] = None,
    douyin_id_bridge: Optional[pd.DataFrame] = None,
    cleaned_output_dir: Optional[Path] = None,
    reference_root: Optional[Path] = None,
) -> AnalysisData:
    input_dir = Path(input_dir)
    raw_category_stats = collect_raw_category_stats(input_dir)
    from .periods import period_metadata_from_dates
    from .raw_cleaning import clean_raw_period_dir, cleaned_workbook_in_dir, load_cleaned_canonical

    cleaned_dir = Path(cleaned_output_dir) if cleaned_output_dir is not None else input_dir
    cleaned_workbook = cleaned_workbook_in_dir(cleaned_dir)
    if cleaned_workbook is None:
        period = period_metadata_from_dates(period_start, period_end)
        clean_raw_period_dir(
            input_dir,
            period,
            default_year=_default_year_from_period(period_start, period_end),
            output_dir=cleaned_dir,
            reference_root=reference_root,
        )
        cleaned_workbook = cleaned_workbook_in_dir(cleaned_dir)
    if cleaned_workbook is None:
        raise FileNotFoundError("未找到可识别的渠道数据文件，请上传 Excel、CSV 或 zip。")

    analysis = analyze_canonical_frame(
        load_cleaned_canonical(cleaned_workbook),
        period_start,
        period_end,
        category_rules_path,
        env_path=env_path,
        category_matcher=category_matcher,
        category_mappings=category_mappings or {},
        reference_tables_path=reference_tables_path,
        account_filters_path=account_filters_path,
        douyin_id_bridge=douyin_id_bridge,
    )
    return replace(analysis, raw_category_stats=raw_category_stats)


def analyze_canonical_frame(
    canonical: pd.DataFrame,
    period_start: str,
    period_end: str,
    category_rules_path: Optional[Path] = None,
    *,
    env_path: Optional[Path] = None,
    category_matcher: Optional[CategoryMatcher] = None,
    category_mappings: Optional[CategoryMappings] = None,
    reference_tables_path: Optional[Path] = None,
    account_filters_path: Optional[Path] = None,
    douyin_id_bridge: Optional[pd.DataFrame] = None,
) -> AnalysisData:
    rules = load_category_rules(category_rules_path)
    references = load_reference_tables(reference_tables_path or Path("config/reference_tables.xlsx"))
    account_filters = load_account_filter_config(account_filters_path or Path("config/account_filters.yml"))
    prepared = canonical.copy()
    had_account_raw = "account_raw" in prepared.columns
    for column in STANDARD_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = ""
    if not had_account_raw:
        prepared["account_raw"] = prepared["account"]
    prepared = _normalize_replayed_canonical_columns(prepared)
    prepared = _backfill_core_metric_aliases(prepared)
    for column in NUMERIC_COLUMNS:
        prepared[column] = prepared[column].map(parse_number)
    prepared["period_start"] = period_start
    prepared["period_end"] = period_end
    prepared = _normalize_social_dimensions(prepared)
    prepared = _apply_account_mappings(prepared, references)
    if douyin_id_bridge is not None and not douyin_id_bridge.empty:
        prepared = apply_content_ledger(prepared, pd.DataFrame(), douyin_id_bridge=douyin_id_bridge)
    prepared, account_filter_details = apply_account_filters(prepared, account_filters)
    preprocessing = _preprocess_canonical(prepared)
    prepared = preprocessing["canonical"]
    prepared = _complete_categories(
        prepared,
        rules,
        references=references,
        env_path=env_path,
        category_matcher=category_matcher,
        category_mappings=category_mappings or {},
    )
    prepared = _derive_metrics(prepared)
    data_quality = _build_data_quality_report(prepared)
    preprocessing_report = _build_preprocessing_report(prepared, preprocessing, data_quality, account_filter_details)
    review_queue = _build_review_queue(prepared)
    channel_summary = _summarize_channels(prepared)
    platform_summary = _summarize_platforms(prepared)
    platform_category_summary = _summarize_platform_categories(prepared)
    total_summary = _make_total_summary(prepared)
    category_summary = _summarize_categories(prepared)
    pending = prepared[prepared["content_category"].map(_is_blank)].copy()
    account_audit = _build_account_audit(prepared, expected_accounts=account_filters.expected_accounts_by_platform())
    top_content_items = _summarize_top_content(prepared)
    cover_metrics = _summarize_cover_metrics(prepared)
    return AnalysisData(
        canonical=prepared,
        category_summary=category_summary,
        channel_summary=channel_summary,
        platform_summary=platform_summary,
        platform_category_summary=platform_category_summary,
        total_summary=total_summary,
        raw_category_stats=pd.DataFrame(columns=["source_file", "sheet", "raw_field", "value", "count"]),
        pending_categories=pending,
        account_audit=account_audit,
        top_content_items=top_content_items,
        cover_metrics=cover_metrics,
        data_quality=data_quality,
        review_queue=review_queue,
        preprocessing_report=preprocessing_report,
        duplicate_merge_details=preprocessing["duplicate_merge_details"],
        conflict_retention_details=preprocessing["conflict_retention_details"],
        missing_value_details=preprocessing["missing_value_details"],
        account_filter_rules=account_filters.to_frame(),
        account_filter_details=account_filter_details,
        reference_tables=references,
    )


def _normalize_replayed_canonical_columns(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    identifier_columns = {"content_id", "material_id", "account_id"}
    text_columns = [
        "platform",
        "platform_group",
        "channel",
        "period_start",
        "period_end",
        "content_id",
        "content_id_fallback",
        "material_id",
        "title",
        "account_raw",
        "account_id",
        "account",
        "account_mapping_source",
        "account_normalized",
        "account_filter_status",
        "account_filter_reason",
        "author",
        "cover_url",
        "content_url",
        "source_time",
        "duration",
        "category_l1",
        "category_l2",
        "category_l3",
        "category_source",
        "category_l2_source",
        "review_status",
        "content_form",
        "primary_category",
        "manual_category",
        "ai_category",
        "content_category",
        "category_status",
        "dedupe_key",
        "conflict_details",
        "review_reasons",
        "source_file",
        "source_sheet",
        "source_file_hash",
        "duplicate_group_id",
        "review_action",
        "ledger_match_source",
        "ledger_match_key",
        "ledger_content_type",
        "ledger_content_type_review",
        "ledger_filter_status",
        "ledger_source_file",
        "ledger_source_sheet",
        "ledger_source_row",
        "match_risk_level",
        "match_risk_reason",
        "manual_category_source",
    ]
    for column in text_columns:
        if column not in normalized.columns:
            continue
        if column in identifier_columns:
            normalized[column] = normalized[column].map(_clean_identifier)
        else:
            normalized[column] = normalized[column].map(lambda value: "" if _is_blank(value) else str(value).strip())
    return normalized


def _find_file(input_dir: Path, tokens: Iterable[str]) -> Path:
    candidates = []
    for path in _iter_tabular_files(input_dir):
        name = path.name
        if all(token in name for token in tokens):
            candidates.append(path)
    if not candidates:
        joined = " / ".join(tokens)
        raise FileNotFoundError(f"未找到包含 {joined} 的平台数据文件")
    return sorted(candidates)[0]


def _standardize_douyin(raw: pd.DataFrame, source_file: str, channel: str) -> pd.DataFrame:
    field_mapping = load_field_mapping()
    return _standardize(
        raw,
        platform=channel,
        platform_group="抖音",
        channel=channel,
        source_file=source_file,
        fields=field_mapping.fields_for_source(f"douyin:{channel}"),
    )


def _social_market_channel(stem: str) -> str:
    return normalize_channel_name(stem)


def _social_market_platform(stem: str) -> str:
    return social_platform_from_name(stem)


def _standardize(
    raw: pd.DataFrame,
    platform: str,
    platform_group: str,
    channel: str,
    source_file: str,
    fields: Mapping[str, List[str]],
) -> pd.DataFrame:
    field_mapping = load_field_mapping()
    normalized = pd.DataFrame(index=raw.index)
    normalized["platform"] = platform
    normalized["platform_group"] = platform_group
    normalized["channel"] = channel
    normalized["period_start"] = ""
    normalized["period_end"] = ""
    normalized["source_file"] = source_file

    expanded_fields = _expanded_field_candidates(raw, fields)
    for output, candidates in expanded_fields.items():
        normalized[output] = _first_non_blank(raw, candidates)

    if "manual_category" not in normalized.columns and "content_category" in normalized.columns:
        normalized["manual_category"] = normalized["content_category"]
    normalized["manual_category"] = raw.apply(lambda row: standardize_content_type(row, field_mapping), axis=1)
    normalized["content_form"] = raw.apply(
        lambda row: standardize_content_form(row, channel=channel, mapping=field_mapping),
        axis=1,
    )

    for column in STANDARD_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    fallback_mask = normalized["content_id"].map(_is_blank) & ~normalized["content_id_fallback"].map(_is_blank)
    normalized.loc[fallback_mask, "content_id"] = normalized.loc[fallback_mask, "content_id_fallback"]
    normalized["account_raw"] = normalized["account"].map(lambda value: "" if _is_blank(value) else str(value).strip())
    normalized["author"] = normalized["author"].where(~normalized["author"].map(_is_blank), normalized["account"])

    for column in NUMERIC_COLUMNS:
        normalized[column] = normalized[column].map(parse_number)

    normalized["content_id"] = normalized["content_id"].fillna("").astype(str)
    normalized["material_id"] = normalized["material_id"].fillna("").astype(str)
    normalized["title"] = normalized["title"].fillna("").astype(str)
    normalized["account_id"] = normalized["account_id"].map(_clean_identifier)
    normalized["account"] = normalized["account"].map(lambda value: "" if _is_blank(value) else str(value))
    normalized["account_mapping_source"] = normalized["account"].map(
        lambda value: "原始账号字段" if not _is_blank(value) else ""
    )
    normalized["author"] = normalized["author"].map(lambda value: "" if _is_blank(value) else str(value))
    normalized["author"] = normalized["author"].where(~normalized["author"].map(_is_blank), normalized["account"])
    normalized = normalized[normalized[["title", "content_id", "material_id", "content_url"]].ne("").any(axis=1)]
    raw_extra = _raw_extra_columns(raw, expanded_fields, source_file)
    if not raw_extra.empty:
        normalized = pd.concat([normalized.reset_index(drop=True), raw_extra.loc[normalized.index].reset_index(drop=True)], axis=1)
    return _ordered_columns(normalized)


def _expanded_field_candidates(raw: pd.DataFrame, fields: Mapping[str, List[str]]) -> dict[str, list[str]]:
    expanded: dict[str, list[str]] = {}
    for output, candidates in fields.items():
        expanded[output] = [column for column in candidates if column in raw.columns]
    return expanded


def _backfill_core_metric_aliases(frame: pd.DataFrame) -> pd.DataFrame:
    backfilled = frame.copy()
    field_mapping = load_field_mapping()
    for output in [field.internal for field in field_mapping.fields if field.internal in set(STANDARD_COLUMNS)]:
        if output not in backfilled.columns:
            backfilled[output] = pd.NA
        backfilled[output] = backfilled[output].astype("object")
        aliases = _field_alias_columns(backfilled, output)
        if output == "content_form":
            aliases = aliases + [column for column in _field_alias_columns(backfilled, "manual_category") if column not in aliases]
        if not aliases:
            continue
        if output == "manual_category":
            alias_values = backfilled.apply(lambda row: _first_valid_content_alias(row, aliases, field_mapping), axis=1)
        elif output == "content_form":
            alias_values = backfilled.apply(
                lambda row: _content_form_from_aliases(row, aliases, field_mapping),
                axis=1,
            )
        else:
            alias_values = _first_non_blank(backfilled, aliases)
        mask = backfilled[output].map(_is_blank) & ~alias_values.map(_is_blank)
        backfilled.loc[mask, output] = alias_values.loc[mask]
    if {"content_id", "content_id_fallback"}.issubset(backfilled.columns):
        fallback_mask = backfilled["content_id"].map(_is_blank) & ~backfilled["content_id_fallback"].map(_is_blank)
        backfilled.loc[fallback_mask, "content_id"] = backfilled.loc[fallback_mask, "content_id_fallback"]
    return backfilled


def _matches_configured_source_alias(output: str, column: object) -> bool:
    try:
        source_columns = set(load_field_mapping().source_columns_for(output))
    except KeyError:
        return False
    return _source_column_name(column) in source_columns


def _field_alias_columns(frame: pd.DataFrame, output: str) -> list[str]:
    source_columns = load_field_mapping().source_columns_for(output)
    aliases: list[str] = []
    for source_column in source_columns:
        for column in frame.columns:
            if column == output:
                continue
            if _source_column_name(column) == source_column and column not in aliases:
                aliases.append(column)
    return aliases


def _source_column_name(column: object) -> str:
    text = str(column or "").strip()
    if text.startswith("raw__"):
        parts = text.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return text


def _first_valid_content_alias(row: pd.Series, aliases: list[str], field_mapping) -> str:
    for column in aliases:
        value = standardize_content_type(pd.Series({_source_column_name(column): row.get(column, "")}), field_mapping)
        if value:
            return value
    return ""


def _content_form_from_aliases(row: pd.Series, aliases: list[str], field_mapping) -> str:
    values = {_source_column_name(column): row.get(column, "") for column in aliases}
    values["manual_category"] = row.get("manual_category", "")
    return standardize_content_form(pd.Series(values), channel=str(row.get("channel", "")), mapping=field_mapping)


def _normalize_social_dimensions(canonical: pd.DataFrame) -> pd.DataFrame:
    normalized = canonical.copy()
    for column in ["platform", "platform_group", "channel"]:
        if column not in normalized.columns:
            normalized[column] = ""
        normalized[column] = normalized[column].fillna("").astype(str)

    normalized["channel"] = normalized["channel"].map(normalize_channel_name)
    platform_from_platform = normalized["platform"].map(lambda value: platform_from_channel_or_name(value, default=""))
    platform_from_channel = normalized["channel"].map(platform_from_channel_or_name)
    social_platform = platform_from_platform.where(platform_from_platform.ne(""), platform_from_channel)
    social_mask = social_platform.ne("")
    if social_mask.any():
        normalized.loc[social_mask, "platform"] = social_platform[social_mask]
        normalized.loc[social_mask, "platform_group"] = social_platform[social_mask]
    return normalized


def _raw_extra_columns(raw: pd.DataFrame, fields: Mapping[str, List[str]], source_file: str) -> pd.DataFrame:
    mapped_columns: set[str] = set()
    for candidates in fields.values():
        mapped_columns.update(candidates)
    extras = raw[[column for column in raw.columns if column not in mapped_columns]].copy()
    if extras.empty:
        return extras
    renamed = {}
    for column in extras.columns:
        renamed[column] = f"raw__{Path(source_file).stem}__{column}"
    return extras.rename(columns=renamed)


def _ordered_columns(frame: pd.DataFrame) -> pd.DataFrame:
    standard = [column for column in STANDARD_COLUMNS if column in frame.columns]
    extras = [column for column in frame.columns if column not in standard]
    return frame[standard + extras]


def _apply_account_mappings(canonical: pd.DataFrame, references: ReferenceTables) -> pd.DataFrame:
    canonical = canonical.copy()
    lookup = account_mapping_lookup(references.account_mapping)
    for column in ["account_raw", "account_mapping_source", "account_id", "account", "author"]:
        if column not in canonical.columns:
            canonical[column] = ""
        canonical[column] = canonical[column].astype(object)

    for index, row in canonical.iterrows():
        account = "" if _is_blank(row.get("account")) else str(row.get("account")).strip()
        account_id = _clean_identifier(row.get("account_id"))
        channel = "" if _is_blank(row.get("channel")) else str(row.get("channel")).strip()
        if account:
            canonical.at[index, "account"] = account
            canonical.at[index, "account_mapping_source"] = canonical.at[index, "account_mapping_source"] or "原始账号字段"
            canonical.at[index, "author"] = row.get("author") if not _is_blank(row.get("author")) else account
            continue
        mapped = lookup.get((channel, account_id))
        if mapped:
            canonical.at[index, "account"] = mapped["account"]
            canonical.at[index, "author"] = mapped["account"]
            canonical.at[index, "account_mapping_source"] = mapped["mapping_source"]
        elif _is_bilibili_row(row) and account_id:
            canonical.at[index, "account_mapping_source"] = "未匹配"
    return canonical


def _preprocess_canonical(canonical: pd.DataFrame) -> dict[str, pd.DataFrame]:
    canonical = canonical.copy()
    for column in ["dedupe_key", "merged_row_count", "conflict_details", "needs_manual_review", "review_reasons"]:
        if column not in canonical.columns:
            canonical[column] = "" if column not in {"merged_row_count", "needs_manual_review"} else 0
    canonical["dedupe_key"] = canonical.apply(_dedupe_key, axis=1)
    canonical["merged_row_count"] = pd.to_numeric(canonical["merged_row_count"], errors="coerce").fillna(0).astype(int)
    canonical["merged_row_count"] = canonical["merged_row_count"].where(canonical["merged_row_count"].gt(0), 1)
    canonical["conflict_details"] = canonical["conflict_details"].fillna("").astype(str)
    canonical["needs_manual_review"] = canonical["needs_manual_review"].fillna(False).astype(bool)
    canonical["review_reasons"] = canonical["review_reasons"].fillna("").astype(str)

    rows: list[pd.Series] = []
    duplicate_rows: list[dict[str, object]] = []
    conflict_rows: list[dict[str, object]] = []

    dedupeable = canonical["dedupe_key"].astype(str).str.strip().ne("")
    for _, group in canonical[dedupeable].groupby("dedupe_key", sort=False, dropna=False):
        merged, conflicts = _merge_duplicate_group(group)
        rows.append(merged)
        if len(group) > 1:
            duplicate_rows.append(
                {
                    "dedupe_key": merged["dedupe_key"],
                    "channel": merged.get("channel", ""),
                    "content_id": merged.get("content_id", ""),
                    "merged_row_count": int(len(group)),
                    "source_files": _join_unique_nonblank(group["source_file"]),
                    "material_ids": _join_unique_nonblank(group["material_id"]),
                }
            )
        conflict_rows.extend(conflicts)

    for _, row in canonical[~dedupeable].iterrows():
        rows.append(row)

    if rows:
        result = pd.DataFrame(rows).reset_index(drop=True)
    else:
        result = canonical
    result = _mark_manual_review_reasons(result)
    missing_details = _build_missing_value_details(result)
    return {
        "canonical": _ordered_columns(result),
        "duplicate_merge_details": pd.DataFrame(
            duplicate_rows,
            columns=["dedupe_key", "channel", "content_id", "merged_row_count", "source_files", "material_ids"],
        ),
        "conflict_retention_details": pd.DataFrame(
            conflict_rows,
            columns=[
                "dedupe_key",
                "channel",
                "content_id",
                "column",
                "values",
                "action",
                "relative_difference",
                "issue_type",
            ],
        ),
        "missing_value_details": missing_details,
    }


def _dedupe_key(row: pd.Series) -> str:
    channel = "" if _is_blank(row.get("channel")) else str(row.get("channel")).strip()
    content_id = "" if _is_blank(row.get("content_id")) else str(row.get("content_id")).strip()
    if not channel:
        return ""
    if content_id:
        return f"{channel}::id::{content_id}"
    content_url = _normalized_content_url_key(row.get("content_url", ""))
    if content_url:
        return f"{channel}::url::{content_url}"
    title = normalized_title_key(row.get("title", ""))
    if not title:
        return ""
    return f"{channel}::title::{title}"


def _merge_duplicate_group(group: pd.DataFrame) -> tuple[pd.Series, list[dict[str, object]]]:
    merged = group.iloc[0].copy()
    conflicts: list[dict[str, object]] = []
    existing_count = pd.to_numeric(group.get("merged_row_count", pd.Series(dtype=float)), errors="coerce").max()
    existing_count = 0 if pd.isna(existing_count) else int(existing_count)
    merged["merged_row_count"] = max(int(len(group)), existing_count)
    if len(group) == 1:
        return merged, conflicts

    dedupe_kind = _dedupe_kind(str(merged.get("dedupe_key", "")))
    exact_cross_sheet_duplicate = _is_exact_cross_sheet_duplicate(group)
    review_conflict_columns: list[str] = []
    for column in NUMERIC_COLUMNS:
        values = pd.to_numeric(group[column], errors="coerce").dropna()
        if values.empty:
            merged[column] = float("nan")
            continue
        if exact_cross_sheet_duplicate:
            merged[column] = float(values.iloc[0])
            continue
        merged[column] = float(values.sum())
        unique_values = list(dict.fromkeys(float(value) for value in values))
        if len(unique_values) <= 1:
            continue
        relative_difference = _relative_difference(unique_values)
        if relative_difference > 0.05:
            action = "sum"
        else:
            action = "first_non_blank"
            review_conflict_columns.append(column)
        conflicts.append(
            {
                "dedupe_key": merged.get("dedupe_key", ""),
                "channel": merged.get("channel", ""),
                "content_id": merged.get("content_id", ""),
                "column": column,
                "values": " | ".join(_format_number(value) for value in unique_values),
                "action": action,
                "relative_difference": relative_difference,
            }
        )

    for column in group.columns:
        if column in NUMERIC_COLUMNS or column in {"merged_row_count", "conflict_details", "needs_manual_review", "review_reasons"}:
            continue
        if column == "dedupe_key":
            merged[column] = group[column].iloc[0]
        elif column == "title":
            merged[column] = _preferred_content_title(group[column])
        else:
            merged[column] = _first_non_blank_value(group[column])
    existing_reasons: list[str] = []
    for value in group.get("review_reasons", pd.Series(dtype=object)).tolist():
        existing_reasons.extend(_split_reasons(value))
    existing_conflicts = [
        str(value).strip()
        for value in group.get("conflict_details", pd.Series(dtype=object)).tolist()
        if not _is_blank(value)
    ]
    text_conflicts = _duplicate_text_conflicts(group, dedupe_kind)
    if text_conflicts:
        existing_conflicts.extend(text_conflicts["details"])
        existing_reasons.extend(text_conflicts["reasons"])
        merged["needs_manual_review"] = True
        for detail, reason in zip(text_conflicts["details"], text_conflicts["reasons"]):
            column, _, values = detail.partition("=")
            conflicts.append(
                {
                    "dedupe_key": merged.get("dedupe_key", ""),
                    "channel": merged.get("channel", ""),
                    "content_id": merged.get("content_id", ""),
                    "column": column,
                    "values": values,
                    "action": "manual_review",
                    "relative_difference": "",
                    "issue_type": reason,
                }
            )
    if dedupe_kind == "id" and _has_different_base_titles(group.get("title", pd.Series(dtype=object))):
        existing_reasons.append("同ID标题不一致")
    if review_conflict_columns:
        existing_conflicts.extend(f"{item['column']}={item['values']}->{item['action']}" for item in conflicts)
        merged["needs_manual_review"] = True
        existing_reasons.append("数值相近重复待审核")
        existing_reasons.append("数值冲突")
    elif conflicts:
        existing_conflicts.extend(f"{item['column']}={item['values']}->{item['action']}" for item in conflicts)
    elif bool(group.get("needs_manual_review", pd.Series(dtype=bool)).astype(bool).any()):
        merged["needs_manual_review"] = True
    merged["conflict_details"] = "; ".join(dict.fromkeys(existing_conflicts))
    merged["review_reasons"] = "；".join(dict.fromkeys(reason for reason in existing_reasons if reason))
    return merged, conflicts


def _dedupe_kind(dedupe_key: str) -> str:
    if "::id::" in dedupe_key:
        return "id"
    if "::url::" in dedupe_key:
        return "url"
    if "::title::" in dedupe_key:
        return "title"
    return ""


def _normalized_content_url_key(value: object) -> str:
    text = "" if _is_blank(value) else str(value).strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.netloc:
        return text.rstrip("/")
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def _duplicate_text_conflicts(group: pd.DataFrame, dedupe_kind: str) -> dict[str, list[str]]:
    details: list[str] = []
    reasons: list[str] = []
    title_values = _distinct_text_values(group.get("title", pd.Series(dtype=object)), key_func=normalized_title_key)
    if dedupe_kind == "id" and len(title_values) > 1:
        details.append(f"title={' | '.join(title_values)}->preferred")
        reasons.append("同ID标题不一致")
    url_values = _distinct_text_values(group.get("content_url", pd.Series(dtype=object)), key_func=_normalized_content_url_key)
    if dedupe_kind == "title" and len(url_values) > 1:
        details.append(f"content_url={' | '.join(url_values)}->first_non_blank")
        reasons.append("同标题多链接")
    content_type_values: list[str] = []
    for column in ["manual_category", "content_form", "content_category"]:
        if column not in group.columns:
            continue
        values = _distinct_text_values(group[column])
        if len(values) > 1:
            details.append(f"{column}={' | '.join(values)}->first_non_blank")
            content_type_values.extend(values)
    if content_type_values:
        reasons.append("内容类型冲突")
    return {
        "details": list(dict.fromkeys(details)),
        "reasons": list(dict.fromkeys(reasons)),
    }


def _distinct_text_values(series: pd.Series, *, key_func: Callable[[object], str] | None = None) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for value in series.tolist():
        if _is_blank(value):
            continue
        text = str(value).strip()
        key = key_func(value) if key_func else text
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(text)
    return values


def _is_exact_cross_sheet_duplicate(group: pd.DataFrame) -> bool:
    if len(group) < 2:
        return False
    source_files = _unique_nonblank_values(group.get("source_file", pd.Series(dtype=object)))
    source_sheets = _unique_nonblank_values(group.get("source_sheet", pd.Series(dtype=object)))
    if len(source_files) != 1 or len(source_sheets) <= 1:
        return False
    for column in NUMERIC_COLUMNS:
        if column not in group.columns:
            continue
        values = pd.to_numeric(group[column], errors="coerce")
        non_blank = values.dropna()
        if non_blank.empty:
            continue
        if non_blank.nunique(dropna=True) > 1:
            return False
    return True


def _unique_nonblank_values(series: pd.Series) -> list[str]:
    values: list[str] = []
    for value in series.tolist():
        if _is_blank(value):
            continue
        text = str(value).strip()
        if text and text not in values:
            values.append(text)
    return values


def _uses_content_id_only_dedupe(row: pd.Series) -> bool:
    text = " ".join(
        str(row.get(column, "") or "")
        for column in ["platform_group", "platform", "channel"]
    )
    normalized = text.lower()
    return "小红书" in text or "B站" in text or "bilibili" in normalized


def _is_bilibili_row(row: pd.Series) -> bool:
    text = " ".join(
        str(row.get(column, "") or "")
        for column in ["platform_group", "platform", "channel"]
    )
    normalized = text.lower()
    return "B站" in text or "bilibili" in normalized or "哔哩哔哩" in text


def _bilibili_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool, index=frame.index)
    parts = []
    for column in ["platform_group", "platform", "channel"]:
        if column in frame.columns:
            parts.append(frame[column].fillna("").astype(str))
        else:
            parts.append(pd.Series("", index=frame.index, dtype=object))
    text = parts[0].str.cat(parts[1:], sep=" ")
    return text.str.contains("B站|哔哩哔哩", na=False) | text.str.lower().str.contains("bilibili", na=False)


def _preferred_content_title(series: pd.Series) -> str:
    values = [str(value).strip() for value in series.tolist() if not _is_blank(value)]
    if not values:
        return ""
    return sorted(values, key=lambda value: (_has_tag(value), len(value)), reverse=True)[0]


def _has_tag(value: object) -> bool:
    return bool(re.search(r"[#＃]\S+", str(value or "")))


def _has_different_base_titles(series: pd.Series) -> bool:
    keys = [
        normalized_title_key(value)
        for value in series.tolist()
        if not _is_blank(value) and normalized_title_key(value)
    ]
    return len(dict.fromkeys(keys)) > 1


def _relative_difference(values: list[float]) -> float:
    positives = [abs(value) for value in values if value != 0]
    if not positives:
        return 0.0
    return (max(values) - min(values)) / min(positives)


def _mark_manual_review_reasons(canonical: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical.copy()
    for index, row in canonical.iterrows():
        reasons = _split_reasons(row.get("review_reasons", ""))
        if _is_blank(row.get("content_id")):
            reasons.append("内容ID缺失")
        if _is_blank(row.get("account")) and _is_bilibili_row(row) and not _is_blank(row.get("account_id")):
            reasons.append("账号映射缺失")
        if _has_manual_conflict(row.get("conflict_details")):
            reasons.append("数值相近重复待审核")
            reasons.append("数值冲突")
        unique_reasons = []
        for reason in reasons:
            if reason and reason not in unique_reasons:
                unique_reasons.append(reason)
        canonical.at[index, "review_reasons"] = "；".join(unique_reasons)
        canonical.at[index, "needs_manual_review"] = bool(unique_reasons)
    return canonical


def _has_manual_conflict(value: object) -> bool:
    if _is_blank(value):
        return False
    details = str(value)
    if "manual_review" in details or "first_non_blank" in details:
        return True
    return False


def _split_reasons(value: object) -> list[str]:
    if _is_blank(value):
        return []
    return [token.strip() for token in re.split(r"[;；]", str(value)) if token.strip()]


def _first_non_blank_value(series: pd.Series) -> str:
    for value in series:
        if not _is_blank(value):
            return str(value).strip()
    return ""


def _build_missing_value_details(canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    key_columns = ["content_id", "material_id", "title", "account", "category_l1", "category_l2"]
    for column in key_columns:
        if column not in canonical.columns:
            continue
        mask = canonical[column].map(_is_blank)
        for _, row in canonical[mask].iterrows():
            rows.append(
                {
                    "channel": row.get("channel", ""),
                    "content_id": row.get("content_id", ""),
                    "material_id": row.get("material_id", ""),
                    "title": row.get("title", ""),
                    "missing_column": column,
                    "action": "保留为空并进入质量扫描",
                }
            )
    return pd.DataFrame(
        rows,
        columns=["channel", "content_id", "material_id", "title", "missing_column", "action"],
    )


def _build_preprocessing_report(
    canonical: pd.DataFrame,
    preprocessing: Mapping[str, pd.DataFrame],
    data_quality: pd.DataFrame,
    account_filter_details: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    duplicate_details = preprocessing["duplicate_merge_details"]
    conflict_details = preprocessing["conflict_retention_details"]
    missing_details = preprocessing["missing_value_details"]
    account_filter_details = account_filter_details if account_filter_details is not None else pd.DataFrame()
    filtered_count = int(len(account_filter_details))
    filtered_spend = (
        pd.to_numeric(account_filter_details.get("spend", pd.Series(dtype=float)), errors="coerce")
        .fillna(0.0)
        .sum()
    )
    total_before_filter = int(len(canonical) + filtered_count)
    rows = [
        {
            "metric": "标准化后行数",
            "value": int(len(canonical)),
            "count": int(len(canonical)),
            "total": int(len(canonical)),
            "status": "完成",
            "note": "标准化并完成渠道内去重后的 canonical 行数。",
        },
        {
            "metric": "重复合并组数",
            "value": int(len(duplicate_details)),
            "count": int(len(duplicate_details)),
            "total": int(len(canonical)),
            "status": "需复核" if not duplicate_details.empty else "通过",
            "note": "按 channel + content_id 合并，content_id 为空不合并。",
        },
        {
            "metric": "冲突保留字段数",
            "value": int(len(conflict_details)),
            "count": int(len(conflict_details)),
            "total": int(len(canonical)),
            "status": "需复核" if not conflict_details.empty else "通过",
            "note": "数值冲突相对差异大于 5% 时求和，否则首个非空值，全部冲突值保留。",
        },
        {
            "metric": "缺失值明细数",
            "value": int(len(missing_details)),
            "count": int(len(missing_details)),
            "total": int(len(canonical)),
            "status": "需处理" if not missing_details.empty else "通过",
            "note": "关键字段缺失保留为空，并进入人工审核或质量扫描。",
        },
        {
            "metric": "小红书账号过滤排除行数",
            "value": filtered_count,
            "count": filtered_count,
            "total": total_before_filter,
            "status": "需关注" if filtered_count else "通过",
            "note": "只有存在账号或账号ID但未命中白名单的小红书行不进入汇总，空账号行默认记录。",
        },
        {
            "metric": "小红书账号过滤排除消耗",
            "value": float(filtered_spend),
            "count": filtered_count,
            "total": total_before_filter,
            "status": "需关注" if filtered_count else "通过",
            "note": "被过滤账号的消耗仅用于审计，不计入小红书汇总。",
        },
    ]
    if not data_quality.empty:
        rows.extend(data_quality.to_dict(orient="records"))
    return pd.DataFrame(rows, columns=["metric", "value", "count", "total", "status", "note"])


def _format_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _first_non_blank(raw: pd.DataFrame, candidates: Iterable[str]) -> pd.Series:
    result = pd.Series([pd.NA] * len(raw), index=raw.index, dtype="object")
    for column in candidates:
        if column not in raw.columns:
            continue
        values = raw[column].astype("object")
        mask = result.map(_is_blank) & ~values.map(_is_blank)
        result.loc[mask] = values.loc[mask]
    return result


def _xiaohongshu_category_status(row: pd.Series) -> str:
    if not _is_blank(row.get("内容分类")):
        return "人工标记"
    if not _is_blank(row.get("内容类型")):
        return "人工标记"
    if not _is_blank(row.get("内容类型_映射")):
        return "人工标记"
    return ""


def _complete_categories(
    canonical: pd.DataFrame,
    rules: Mapping[str, Iterable[str]],
    *,
    references: ReferenceTables,
    env_path: Optional[Path] = None,
    category_matcher: Optional[CategoryMatcher] = None,
    category_mappings: Optional[CategoryMappings] = None,
) -> pd.DataFrame:
    canonical = canonical.copy()
    for column in [
        "category_l1",
        "category_l2",
        "category_l3",
        "category_source",
        "review_status",
        "primary_category",
        "manual_category",
        "ai_category",
        "content_category",
        "category_status",
        "category_l2_source",
        "review_reasons",
    ]:
        if column not in canonical.columns:
            canonical[column] = ""
        canonical[column] = canonical[column].astype(object)
    raw_category = canonical["manual_category"].where(~canonical["manual_category"].map(_is_blank), "")
    canonical["primary_category"] = ""
    canonical["category_confidence"] = 0.0

    canonical["manual_category"] = ""
    canonical["ai_category"] = ""
    canonical["content_category"] = ""
    canonical["category_l2_source"] = ""
    _apply_fixed_channel_categories(canonical)
    has_category = raw_category.astype(str).str.strip().ne("")
    writable_manual = has_category & canonical["content_category"].map(_is_blank)
    canonical.loc[writable_manual, "manual_category"] = raw_category.loc[writable_manual].astype(str).str.strip()
    canonical.loc[writable_manual, "content_category"] = raw_category.loc[writable_manual].astype(str).str.strip()
    manual_sources = (
        canonical["manual_category_source"].fillna("").astype(str).str.strip()
        if "manual_category_source" in canonical.columns
        else pd.Series("", index=canonical.index)
    )
    canonical.loc[writable_manual, "category_status"] = manual_sources.loc[writable_manual].where(
        manual_sources.loc[writable_manual].ne(""),
        "人工标记",
    )
    canonical.loc[writable_manual, "category_confidence"] = 1.0

    _apply_account_content_type_mappings(canonical, references.account_content_type)

    tag_category = canonical.apply(lambda row: category_from_tags(_tag_match_text_for_row(row)), axis=1)
    has_tag_category = tag_category.astype(str).str.strip().ne("")
    _apply_tag_categories(canonical, tag_category, has_tag_category)

    keyword_category = canonical["title"].map(lambda title: suggest_category(title, rules))
    has_keyword_category = keyword_category.astype(str).str.strip().ne("") & canonical["content_category"].map(_is_blank)
    canonical.loc[has_keyword_category, "ai_category"] = keyword_category.loc[has_keyword_category]
    canonical.loc[has_keyword_category, "content_category"] = keyword_category.loc[has_keyword_category]
    canonical.loc[has_keyword_category, "category_status"] = "标题关键词匹配"
    canonical.loc[has_keyword_category, "category_confidence"] = 0.75

    category_library = _build_category_library(raw_category, keyword_category, canonical["content_category"])
    pending = canonical[canonical["content_category"].map(_is_blank)]
    if category_library and not pending.empty:
        matcher = category_matcher or _default_category_matcher
        for channel, group in pending.groupby("channel", dropna=False):
            scoped_library = _category_library_for_channel(canonical, str(channel), category_library)
            matched = matcher(group.copy(), scoped_library, env_path)
            for index, match in matched.items():
                if index not in canonical.index:
                    continue
                normalized, confidence = _category_match_payload(match)
                if normalized not in scoped_library:
                    continue
                canonical.at[index, "ai_category"] = normalized
                canonical.at[index, "content_category"] = normalized
                canonical.at[index, "category_status"] = "DeepSeek匹配"
                canonical.at[index, "category_confidence"] = confidence

    _fill_missing_secondary_categories(canonical)

    if category_mappings:
        _apply_category_mappings(canonical, category_mappings)

    _apply_high_spend_category_rules(canonical)

    still_missing = canonical["content_category"].map(_is_blank)
    canonical.loc[still_missing, "category_status"] = "未匹配"
    canonical.loc[~still_missing & canonical["category_status"].map(_is_blank), "category_status"] = "人工标记"
    canonical.loc[~still_missing & canonical["category_confidence"].eq(0.0), "category_confidence"] = 1.0
    canonical["primary_category"] = ""
    canonical["category_l1"] = ""
    canonical["category_l2"] = canonical["content_category"].fillna("").astype(str)
    canonical["category_l3"] = canonical["category_l3"].where(~canonical["category_l3"].map(_is_blank), canonical["title"])
    canonical["category_status"] = canonical["category_status"].fillna("").astype(str).astype(object)
    canonical["category_source"] = canonical["category_status"].fillna("").astype(str).astype(object)
    missing_l2_source = canonical["category_l2_source"].map(_is_blank)
    canonical.loc[missing_l2_source, "category_l2_source"] = canonical.loc[missing_l2_source, "category_status"]
    canonical["review_status"] = canonical.apply(_review_status, axis=1)
    canonical = _mark_category_review_reasons(canonical)
    return canonical


def _apply_fixed_channel_categories(canonical: pd.DataFrame) -> None:
    bilibili = _bilibili_mask(canonical)
    if not bilibili.any():
        return
    if "content_form" in canonical.columns:
        canonical.loc[bilibili & canonical["content_form"].map(_is_blank), "content_form"] = "视频"


def _category_match_payload(value: object) -> tuple[str, float]:
    if isinstance(value, Mapping):
        category = str(value.get("category", "") or "").strip()
        confidence = parse_number(value.get("confidence"))
        if pd.isna(confidence):
            confidence = 0.65
        return category, float(max(0.0, min(1.0, confidence)))
    return str(value or "").strip(), 0.65


def _fill_missing_secondary_categories(canonical: pd.DataFrame) -> None:
    missing = canonical["content_category"].map(_is_blank)
    non_bilibili = ~_bilibili_mask(canonical)
    candidates = canonical[missing & non_bilibili]
    if candidates.empty:
        return

    known = canonical[~canonical["content_category"].map(_is_blank) & non_bilibili].copy()
    if known.empty:
        return

    account_lookup = _majority_category_lookup(known, ["channel", "account"])
    topic_lookup = _majority_category_lookup(known, ["channel", "category_l3"])
    channel_lookup = _single_category_lookup(known, ["channel"])

    for index, row in candidates.iterrows():
        category = ""
        source = ""
        account_key = _lookup_key(row, ["channel", "account"])
        if account_key in account_lookup:
            category = account_lookup[account_key]
            source = "同账号栏目补全"
        if not category:
            topic_key = _lookup_key(row, ["channel", "category_l3"])
            if topic_key in topic_lookup:
                category = topic_lookup[topic_key]
                source = "同题材栏目补全"
        if not category:
            channel_key = _lookup_key(row, ["channel"])
            if channel_key in channel_lookup:
                category = channel_lookup[channel_key]
                source = "同渠道栏目补全"
        if not category:
            continue
        canonical.at[index, "ai_category"] = category
        canonical.at[index, "content_category"] = category
        canonical.at[index, "category_status"] = source
        canonical.at[index, "category_l2_source"] = source
        canonical.at[index, "category_confidence"] = 0.55


def _majority_category_lookup(known: pd.DataFrame, key_columns: list[str]) -> dict[tuple[str, ...], str]:
    lookup: dict[tuple[str, ...], str] = {}
    for key, group in known.groupby(key_columns, dropna=False):
        normalized_key = key if isinstance(key, tuple) else (key,)
        clean_key = tuple("" if pd.isna(value) else str(value).strip() for value in normalized_key)
        if any(not value for value in clean_key):
            continue
        counts = group["content_category"].fillna("").astype(str).str.strip().value_counts()
        counts = counts[counts.index != ""]
        if counts.empty:
            continue
        if len(counts) == 1 or counts.iloc[0] > counts.iloc[1]:
            lookup[clean_key] = str(counts.index[0])
    return lookup


def _single_category_lookup(known: pd.DataFrame, key_columns: list[str]) -> dict[tuple[str, ...], str]:
    lookup: dict[tuple[str, ...], str] = {}
    for key, group in known.groupby(key_columns, dropna=False):
        normalized_key = key if isinstance(key, tuple) else (key,)
        clean_key = tuple("" if pd.isna(value) else str(value).strip() for value in normalized_key)
        if any(not value for value in clean_key):
            continue
        values = group["content_category"].fillna("").astype(str).str.strip()
        unique = [value for value in values.unique() if value]
        if len(unique) == 1:
            lookup[clean_key] = unique[0]
    return lookup


def _lookup_key(row: pd.Series, columns: list[str]) -> tuple[str, ...]:
    return tuple("" if pd.isna(row.get(column, "")) else str(row.get(column, "")).strip() for column in columns)


def _apply_tag_categories(canonical: pd.DataFrame, tag_category: pd.Series, has_tag_category: pd.Series) -> None:
    channel = canonical["channel"].fillna("").astype(str)
    tag_scoped = has_tag_category & (
        channel.str.contains("小红书", na=False) | channel.str.contains("抖音", na=False)
    ) & canonical["content_category"].map(_is_blank)
    if not tag_scoped.any():
        return

    canonical.loc[tag_scoped, "ai_category"] = tag_category.loc[tag_scoped].astype(str).str.strip()
    canonical.loc[tag_scoped, "content_category"] = tag_category.loc[tag_scoped].astype(str).str.strip()
    canonical.loc[tag_scoped, "category_status"] = "TAG匹配"
    canonical.loc[tag_scoped, "category_confidence"] = 0.95


def _apply_high_spend_category_rules(canonical: pd.DataFrame) -> None:
    if canonical.empty or "channel" not in canonical.columns:
        return
    channel = canonical["channel"].fillna("").astype(str)
    douyin = channel.str.contains("抖音", na=False)
    missing = canonical["content_category"].map(_is_blank)
    spend = pd.to_numeric(canonical.get("spend", pd.Series(0.0, index=canonical.index)), errors="coerce").fillna(0.0)
    top_indexes: set[object] = set()
    for _, group in canonical[douyin].groupby("channel", dropna=False, sort=False):
        top_indexes.update(group.sort_values("spend", ascending=False).head(20).index)
    high_spend = spend.ge(2000.0) | canonical.index.isin(top_indexes)
    scoped = canonical[douyin & missing & high_spend]
    if scoped.empty:
        return
    for index, row in scoped.iterrows():
        text = " ".join(
            str(row.get(column, "") or "")
            for column in ["title", "metadata_tags", "category_l3", "manual_category", "ai_category"]
        )
        category = suggest_category(text, HIGH_SPEND_CATEGORY_RULES)
        if not category:
            continue
        canonical.at[index, "ai_category"] = category
        canonical.at[index, "content_category"] = category
        canonical.at[index, "category_status"] = "高消耗规则匹配"
        canonical.at[index, "category_confidence"] = 0.8


def _tag_match_text_for_row(row: pd.Series) -> str:
    values: list[str] = []
    for column, value in row.items():
        column_text = str(column)
        if column_text == "title" or column_text in {"tag词", "TAG词", "标签", "话题"}:
            if not _is_blank(value):
                values.append(str(value))
            continue
        if not column_text.startswith("raw__"):
            continue
        raw_name = column_text.rsplit("__", 1)[-1]
        if raw_name in {"tag词", "TAG词", "标签", "话题"} and not _is_blank(value):
            values.append(str(value))
    return " ".join(values)


def _apply_account_content_type_mappings(canonical: pd.DataFrame, account_content_type: pd.DataFrame) -> None:
    if account_content_type.empty:
        return
    required = {"channel", "account", "category_l2"}
    if not required.issubset(set(account_content_type.columns)):
        return
    mapping: dict[tuple[str, str], Mapping[str, str]] = {}
    for _, row in account_content_type.fillna("").iterrows():
        channel = str(row.get("channel", "")).strip()
        account = str(row.get("account", "")).strip()
        category_l2 = str(row.get("category_l2", "")).strip()
        if not channel or not account or not category_l2:
            continue
        mapping[(channel, account)] = {
            "category_l1": str(row.get("category_l1", "")).strip(),
            "category_l2": category_l2,
            "category_l3": str(row.get("category_l3", "")).strip(),
        }
    if not mapping:
        return

    for index, row in canonical.iterrows():
        if not _is_blank(row.get("content_category")):
            continue
        key = (str(row.get("channel", "")).strip(), str(row.get("account", "")).strip())
        item = mapping.get(key)
        if not item:
            continue
        canonical.at[index, "manual_category"] = item["category_l2"]
        canonical.at[index, "content_category"] = item["category_l2"]
        canonical.at[index, "category_status"] = "账号内容类型对照"
        canonical.at[index, "category_confidence"] = 1.0
        if item["category_l3"]:
            canonical.at[index, "category_l3"] = item["category_l3"]


def _mark_category_review_reasons(canonical: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical.copy()
    for index, row in canonical.iterrows():
        reasons = _split_reasons(row.get("review_reasons", ""))
        if row.get("review_status") in {"待审核", "待复核"}:
            reasons.append("分类待复核")
        unique_reasons = []
        for reason in reasons:
            if reason and reason not in unique_reasons:
                unique_reasons.append(reason)
        canonical.at[index, "review_reasons"] = "；".join(unique_reasons)
        canonical.at[index, "needs_manual_review"] = bool(unique_reasons)
    return canonical


def _apply_category_mappings(canonical: pd.DataFrame, category_mappings: CategoryMappings) -> None:
    for index, row in canonical.iterrows():
        mapping = _lookup_category_mapping(row, category_mappings)
        if not mapping:
            continue
        l2 = str(mapping.get("category_l2", "")).strip()
        l3 = str(mapping.get("category_l3", "")).strip()
        if l2:
            canonical.at[index, "manual_category"] = l2
            canonical.at[index, "content_category"] = l2
            canonical.at[index, "category_status"] = "历史审核映射"
            canonical.at[index, "category_l2_source"] = "历史审核映射"
            canonical.at[index, "category_confidence"] = 1.0
        if l3:
            canonical.at[index, "category_l3"] = l3


def _lookup_category_mapping(
    row: pd.Series,
    category_mappings: CategoryMappings,
) -> Optional[Mapping[str, str]]:
    for key in _category_mapping_keys(row):
        mapping = category_mappings.get(key)
        if mapping:
            return mapping
    return None


def _category_mapping_keys(row: pd.Series) -> list[str]:
    keys: list[str] = []
    for column in ["content_id", "material_id", "title"]:
        value = "" if pd.isna(row.get(column, "")) else str(row.get(column, "")).strip()
        if value:
            keys.append(f"{column}:{value}")
    title_key = normalized_title_key(row.get("title", ""))
    if title_key:
        keys.append(f"title_key:{title_key}")
    return keys


def _review_status(row: pd.Series) -> str:
    if _is_blank(row.get("category_l2")):
        return "待审核"
    source = str(row.get("category_source", "")).strip()
    if source in {"人工标记", "历史审核映射"}:
        return "已确认"
    confidence = parse_number(row.get("category_confidence"))
    if not pd.isna(confidence) and confidence >= 0.9:
        return "已确认"
    return "待复核"


def _default_category_matcher(
    items: pd.DataFrame,
    category_library: list[str],
    env_path: Optional[Path],
) -> Mapping[int, str]:
    from .ai import match_missing_categories

    return match_missing_categories(items, category_library, env_path)


def _build_category_library(*series_list: pd.Series) -> list[str]:
    values: list[str] = []
    for series in series_list:
        clean = series.where(~series.map(_is_blank), "").astype(str).str.strip()
        for value in clean:
            if value and value not in values:
                values.append(value)
    return values


def _category_library_for_channel(canonical: pd.DataFrame, channel: str, fallback: list[str]) -> list[str]:
    channel_name = str(channel).strip()
    scoped = canonical[canonical["channel"].fillna("").astype(str).str.strip().eq(channel_name)]
    library = _build_category_library(scoped["content_category"]) if not scoped.empty else []
    return library or fallback


def _derive_metrics(canonical: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical.copy()
    canonical["ctr"] = _safe_divide(canonical["clicks"], canonical["impressions"])
    canonical["activation_rate"] = _safe_divide(canonical["activations"], canonical["clicks"])
    canonical["first_pay_rate"] = _safe_divide(canonical["first_pay_count"], canonical["activations"])
    canonical["activation_cost"] = _safe_divide(canonical["spend"], canonical["activations"])
    canonical["first_pay_cost"] = _safe_divide(canonical["spend"], canonical["first_pay_count"])
    return canonical


def _read_channel_totals(path: Path) -> pd.DataFrame:
    preview = _read_table(path, sheet_name=0, header=None, nrows=20)
    header_row = None
    for idx, row in preview.iterrows():
        if row.astype(str).str.strip().eq("渠道").any():
            header_row = int(idx)
            break
    if header_row is None:
        raise ValueError(f"{path.name} 中未找到渠道汇总表头")

    raw = _read_table(path, sheet_name=0, header=header_row)
    raw = raw.dropna(axis=1, how="all")
    if "渠道" not in raw.columns:
        channel_col = next((col for col in raw.columns if str(col).strip() == "渠道"), None)
        if channel_col is None:
            raise ValueError(f"{path.name} 中未找到渠道列")
        raw = raw.rename(columns={channel_col: "渠道"})
    keep = [column for column in ["渠道", "消耗", "激活", "付费"] if column in raw.columns]
    totals = raw[keep].copy()
    totals = totals[~totals["渠道"].map(_is_blank)]
    totals = totals.rename(
        columns={
            "渠道": "channel",
            "消耗": "spend_total",
            "激活": "activations_total",
            "付费": "first_pay_count_total",
        }
    )
    for column in ["spend_total", "activations_total", "first_pay_count_total"]:
        if column in totals.columns:
            totals[column] = totals[column].map(parse_number)
    return totals


def _summarize_channels(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby("channel", as_index=False)
        .agg(
            platform=("platform", "first"),
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values("spend", ascending=False)
    )
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    return summary.reset_index(drop=True)


def _summarize_platforms(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby("channel", as_index=False)
        .agg(
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values("spend", ascending=False)
    )
    total_spend = _sum_or_zero(summary["spend"])
    total_activations = _sum_or_zero(summary["activations"])
    total_first_pay = _sum_or_zero(summary["first_pay_count"])
    summary["spend_share"] = summary["spend"] / total_spend if total_spend else 0.0
    summary["activation_share"] = summary["activations"] / total_activations if total_activations else 0.0
    summary["first_pay_share"] = summary["first_pay_count"] / total_first_pay if total_first_pay else 0.0
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_rate"] = _safe_divide(summary["activations"], summary["clicks"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    return summary.reset_index(drop=True)


def _summarize_platform_categories(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby(["channel", "account", "content_category", "category_l3"], as_index=False)
        .agg(
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values(["channel", "activations"], ascending=[True, False])
    )
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_rate"] = _safe_divide(summary["activations"], summary["clicks"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    summary["category_display"] = summary["content_category"].fillna("").astype(str)
    summary = _add_scoring_columns(summary)
    return summary.sort_values(["channel", "overall_score"], ascending=[True, False]).reset_index(drop=True)


def _summarize_categories(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby(["content_category", "category_l3"], as_index=False)
        .agg(
            channel_count=("channel", "nunique"),
            account_count=("account", lambda values: values.replace("", pd.NA).dropna().nunique()),
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values("activations", ascending=False)
    )
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    summary["category_display"] = summary["content_category"].fillna("").astype(str)
    summary = _add_scoring_columns(summary)
    return summary.sort_values("overall_score", ascending=False).reset_index(drop=True)


def _add_scoring_columns(summary: pd.DataFrame) -> pd.DataFrame:
    summary = summary.copy()
    summary["heat_score"] = (
        0.45 * _rank_pct(summary["impressions"])
        + 0.35 * _rank_pct(summary["clicks"])
        + 0.20 * _rank_pct(summary["ctr"])
    )
    inverse_activation_cost = 1 - _rank_pct(summary["activation_cost"].replace([float("inf")], pd.NA))
    summary["acquisition_score"] = (
        0.35 * _rank_pct(summary["activations"])
        + 0.25 * _rank_pct(summary["first_pay_count"])
        + 0.15 * inverse_activation_cost
        + 0.15 * _rank_pct(summary["first_pay_rate"])
        + 0.10 * summary["heat_score"]
    )
    summary["overall_score"] = (summary["acquisition_score"] * 100).round(2)
    return summary


def _make_total_summary(canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    total_spend = _sum_or_zero(canonical["spend"])
    total_activations = _sum_or_zero(canonical["activations"])
    total_first_pay = _sum_or_zero(canonical["first_pay_count"])

    for channel, group in canonical.groupby("channel", sort=False):
        rows.append(_total_row(channel, group, total_spend, total_activations, total_first_pay))
    rows.append(_total_row("总计", canonical, total_spend, total_activations, total_first_pay))
    return pd.DataFrame(rows)


def _total_row(
    channel: str,
    group: pd.DataFrame,
    total_spend: float,
    total_activations: float,
    total_first_pay: float,
) -> Dict[str, float]:
    spend = _sum_or_zero(group["spend"])
    activations = _sum_or_zero(group["activations"])
    first_pay = _sum_or_zero(group["first_pay_count"])
    impressions = _sum_or_zero(group["impressions"])
    clicks = _sum_or_zero(group["clicks"])
    pending = group[group["content_category"].map(_is_blank).astype(bool)]
    pending_spend = _sum_or_zero(pending["spend"])
    return {
        "channel": channel,
        "spend": spend,
        "spend_share": spend / total_spend if total_spend else 0.0,
        "impressions": impressions,
        "clicks": _sum_or_blank(group["clicks"]),
        "ctr": clicks / impressions if impressions and not pd.isna(clicks) else float("nan"),
        "activations": activations,
        "activation_share": activations / total_activations if total_activations else 0.0,
        "activation_cost": spend / activations if activations else 0.0,
        "first_pay_count": first_pay,
        "first_pay_share": first_pay / total_first_pay if total_first_pay else 0.0,
        "first_pay_cost": spend / first_pay if first_pay else 0.0,
        "first_pay_rate": first_pay / activations if activations else 0.0,
        "item_count": float(len(group)),
        "pending_item_count": float(len(pending)),
        "pending_spend": pending_spend,
        "pending_spend_share": pending_spend / spend if spend else 0.0,
        "secondary_category_count": float(group["content_category"].replace("", pd.NA).dropna().nunique()),
    }


def collect_raw_category_stats(input_dir: Path) -> pd.DataFrame:
    specs = [
        ("B站.xlsx", "sheet1", 0),
        ("小红书商业化.xlsx", "kos账户投放数据", 0),
        ("小红书商业化.xlsx", "内容表格", 1),
        ("抖音商业化.xlsx", "Sheet2", 0),
        ("抖音商业化.xlsx", "Sheet1", 3),
        ("抖音市场部.xlsx", "Sheet2", 0),
    ]
    rows = []
    for file_name, sheet_name, header in specs:
        try:
            path = _find_file(Path(input_dir), [Path(file_name).stem])
        except FileNotFoundError:
            continue
        try:
            frame = _read_table(path, sheet_name=sheet_name, header=header)
        except Exception:
            continue
        for raw_field in ["类型", "内容分类", "内容类型"]:
            if raw_field not in frame.columns:
                continue
            values = frame[raw_field].dropna().astype(str).str.strip()
            values = values[values.ne("") & values.ne("0") & values.str.lower().ne("nan")]
            for value, count in values.value_counts().items():
                rows.append(
                    {
                        "source_file": path.name,
                        "sheet": sheet_name,
                        "raw_field": raw_field,
                        "value": value,
                        "count": int(count),
                    }
                )
    return pd.DataFrame(rows, columns=["source_file", "sheet", "raw_field", "value", "count"])


def _build_data_quality_report(canonical: pd.DataFrame) -> pd.DataFrame:
    total = int(len(canonical))
    rows = []

    def add_rate(metric: str, mask: pd.Series, note: str) -> None:
        count = int(mask.sum())
        rows.append(
            {
                "metric": metric,
                "value": count / total if total else 0.0,
                "count": count,
                "total": total,
                "status": "需处理" if count else "通过",
                "note": note,
            }
        )

    add_rate("二级分类缺失率", canonical["content_category"].map(_is_blank), "二级分类对应当前内容类型，是推荐分析的主分类。")
    add_rate("素材ID缺失率", canonical["material_id"].map(_is_blank), "素材ID缺失会影响按素材追踪。")
    add_rate("内容ID缺失率", canonical["content_id"].map(_is_blank), "内容ID缺失会影响去重和历史复用。")
    add_rate("标题缺失率", canonical["title"].map(_is_blank), "标题缺失会影响AI分类和三级题材暂代。")
    add_rate("账号/作者缺失率", canonical["account"].map(_is_blank), "账号缺失会影响账号维度筛选。")

    impressions = pd.to_numeric(canonical["impressions"], errors="coerce").fillna(0.0)
    clicks = pd.to_numeric(canonical["clicks"], errors="coerce").fillna(0.0)
    spend = pd.to_numeric(canonical["spend"], errors="coerce").fillna(0.0)
    activations = pd.to_numeric(canonical["activations"], errors="coerce").fillna(0.0)
    anomalies = [
        ("展示为0但点击大于0", impressions.eq(0.0) & clicks.gt(0.0), "展示为0时点击率无法解释，需要回查原表。"),
        ("消耗为0但激活大于0", spend.eq(0.0) & activations.gt(0.0), "消耗为0时成本类指标无法解释，需要回查原表。"),
        ("消耗小于0", spend.lt(0.0), "负消耗通常来自退款或导出异常，需人工确认。"),
    ]
    for metric, mask, note in anomalies:
        count = int(mask.sum())
        rows.append(
            {
                "metric": metric,
                "value": float(count),
                "count": count,
                "total": total,
                "status": "需处理" if count else "通过",
                "note": note,
            }
        )
    return pd.DataFrame(rows, columns=["metric", "value", "count", "total", "status", "note"])


def _build_review_queue(canonical: pd.DataFrame) -> pd.DataFrame:
    prepared = canonical.copy()
    for column in [
        "period_start",
        "period_end",
        "channel",
        "spend",
        "activations",
        "needs_manual_review",
        "review_reasons",
        "conflict_details",
        "match_risk_level",
        "match_risk_reason",
        *[field for field, _ in REVIEW_QUEUE_CRITICAL_FIELDS],
    ]:
        if column not in prepared.columns:
            prepared[column] = False if column == "needs_manual_review" else ""

    spend = pd.to_numeric(prepared["spend"], errors="coerce").fillna(0.0)
    prepared["__review_rank_in_channel"] = _review_queue_rank_in_channel(prepared, spend)
    top_content = prepared["__review_rank_in_channel"].le(REVIEW_QUEUE_TOP_N)
    high_spend = spend.ge(REVIEW_QUEUE_HIGH_SPEND_THRESHOLD)
    high_risk = _review_queue_high_risk_mask(prepared)
    missing_critical = _review_queue_missing_critical_mask(prepared)
    queue = prepared[top_content | high_spend | high_risk | missing_critical].copy()
    if queue.empty:
        return pd.DataFrame(
            columns=[
                "review_status",
                "rank_in_channel",
                "needs_manual_review",
                "review_reasons",
                "channel",
                "title",
                "content_url",
                "account_id",
                "account_raw",
                "account",
                "account_mapping_source",
                "content_id",
                "material_id",
                "dedupe_key",
                "merged_row_count",
                "conflict_details",
                "manual_category",
                "ai_category",
                "content_category",
                "category_l2",
                "category_l3",
                "category_source",
                "category_confidence",
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
                "source_row",
                "source_file_hash",
                "duplicate_group_id",
                "review_action",
            ]
        )
    queue["rank_in_channel"] = queue["__review_rank_in_channel"].astype("Int64")
    queue["review_reasons"] = queue.apply(_review_queue_reasons, axis=1)
    queue["needs_manual_review"] = True
    columns = [
        "review_status",
        "rank_in_channel",
        "needs_manual_review",
        "review_reasons",
        "channel",
        "title",
        "content_url",
        "account_id",
        "account_raw",
        "account",
        "account_mapping_source",
        "content_id",
        "material_id",
        "dedupe_key",
        "merged_row_count",
        "conflict_details",
        "manual_category",
        "ai_category",
        "content_category",
        "category_l2",
        "category_l3",
        "category_source",
        "category_confidence",
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
        "source_row",
        "source_file_hash",
        "duplicate_group_id",
        "review_action",
    ]
    for column in columns:
        if column not in queue.columns:
            queue[column] = ""
    return queue.sort_values(["spend", "rank_in_channel", "activations"], ascending=[False, True, False])[columns].reset_index(drop=True)


def _review_queue_rank_in_channel(canonical: pd.DataFrame, spend: pd.Series) -> pd.Series:
    frame = canonical[["period_start", "period_end", "channel"]].copy()
    frame["__spend"] = spend
    return (
        frame.groupby(["period_start", "period_end", "channel"], dropna=False)["__spend"]
        .rank(method="first", ascending=False)
        .astype("Int64")
    )


def _review_queue_high_risk_mask(canonical: pd.DataFrame) -> pd.Series:
    text = pd.Series("", index=canonical.index, dtype=object)
    for column in ["review_reasons", "conflict_details", "match_risk_level", "match_risk_reason"]:
        if column in canonical.columns:
            text = text.str.cat(canonical[column].fillna("").astype(str), sep="；")
    matched = pd.Series(False, index=canonical.index)
    for pattern in REVIEW_QUEUE_HIGH_RISK_PATTERNS:
        matched = matched | text.str.contains(pattern, na=False)
    manual_flag = canonical.get("needs_manual_review", pd.Series(False, index=canonical.index)).map(_review_queue_truthy)
    return matched | manual_flag


def _review_queue_missing_critical_mask(canonical: pd.DataFrame) -> pd.Series:
    mask = pd.Series(False, index=canonical.index)
    for column, _ in REVIEW_QUEUE_CRITICAL_FIELDS:
        if column in canonical.columns:
            mask = mask | canonical[column].map(_is_blank)
    return mask


def _review_queue_reasons(row: pd.Series) -> str:
    reasons = _split_reasons(row.get("review_reasons", ""))
    rank = parse_number(row.get("rank_in_channel"))
    if not pd.isna(rank) and rank <= REVIEW_QUEUE_TOP_N:
        reasons.append("分渠道消耗Top20")
    spend = parse_number(row.get("spend"))
    if not pd.isna(spend) and spend >= REVIEW_QUEUE_HIGH_SPEND_THRESHOLD:
        reasons.append("单条消耗>=2000元")
    for column, label in REVIEW_QUEUE_CRITICAL_FIELDS:
        if _is_blank(row.get(column)):
            reasons.append(f"{label}补齐失败")
    if _review_row_has_high_risk_conflict(row):
        reasons.append("高风险冲突")

    unique_reasons = []
    for reason in reasons:
        text = str(reason or "").strip()
        if text and text not in unique_reasons:
            unique_reasons.append(text)
    return "；".join(unique_reasons)


def _review_row_has_high_risk_conflict(row: pd.Series) -> bool:
    text = "；".join(
        str(row.get(column, "") or "")
        for column in ["review_reasons", "conflict_details", "match_risk_level", "match_risk_reason"]
    )
    if _review_queue_truthy(row.get("needs_manual_review", False)):
        return True
    return any(pattern in text for pattern in REVIEW_QUEUE_HIGH_RISK_PATTERNS)


def _review_queue_truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是", "需复核", "待审核"}
    return bool(value)


def _build_account_audit(
    canonical: pd.DataFrame,
    *,
    expected_accounts: Optional[Mapping[str, Iterable[str]]] = None,
) -> pd.DataFrame:
    rows = []
    canonical = canonical.copy()
    canonical["account"] = canonical["account"].fillna("").astype(str).str.strip()
    expected_by_platform = dict(EXPECTED_ACCOUNTS)
    if expected_accounts is not None:
        for platform, accounts in expected_accounts.items():
            expected_by_platform[str(platform).strip()] = [
                str(account).strip() for account in accounts if str(account).strip()
            ]
    for platform, expected_accounts in expected_by_platform.items():
        if platform == "抖音":
            platform_items = canonical[canonical["channel"].astype(str).str.contains("抖音", na=False)]
        elif platform == "小红书":
            platform_items = canonical[canonical["channel"].astype(str).str.contains("小红书", na=False)]
        else:
            platform_items = canonical[canonical["channel"].eq(platform)]
        observed = platform_items["account"].replace("", pd.NA).dropna()
        observed_set = set(observed.astype(str))
        for expected_account in expected_accounts:
            count = int(platform_items["account"].eq(expected_account).sum())
            rows.append(
                {
                    "channel": platform,
                    "expected_account": expected_account,
                    "status": "已覆盖" if count else "缺失",
                    "observed_count": count,
                    "matched_account": expected_account if count else "",
                }
            )
        for account in sorted(observed_set - set(expected_accounts)):
            rows.append(
                {
                    "channel": platform,
                    "expected_account": "",
                    "status": "异常账号",
                    "observed_count": int(platform_items["account"].eq(account).sum()),
                    "matched_account": account,
                }
            )
    return pd.DataFrame(
        rows,
        columns=["channel", "expected_account", "status", "observed_count", "matched_account"],
    )


def _summarize_top_content(canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for channel, group in canonical.groupby("channel", sort=False):
        ranked = group.sort_values("spend", ascending=False).head(15).copy()
        spend_threshold = pd.to_numeric(group["spend"], errors="coerce").quantile(0.75)
        activation_median = pd.to_numeric(group["activations"], errors="coerce").median()
        cost_median = pd.to_numeric(group["activation_cost"], errors="coerce").median()
        for _, item in ranked.iterrows():
            rows.append(
                {
                    "channel": channel,
                    "content_id": item.get("content_id", ""),
                    "material_id": item.get("material_id", ""),
                    "title": item.get("title", ""),
                    "account_id": item.get("account_id", ""),
                    "account": item.get("account", ""),
                    "manual_category": item.get("manual_category", ""),
                    "ai_category": item.get("ai_category", ""),
                    "content_category": item.get("content_category", ""),
                    "spend": item.get("spend", 0.0),
                    "impressions": item.get("impressions", 0.0),
                    "clicks": item.get("clicks", 0.0),
                    "activations": item.get("activations", 0.0),
                    "first_pay_count": item.get("first_pay_count", 0.0),
                    "activation_cost": item.get("activation_cost", pd.NA),
                    "first_pay_cost": item.get("first_pay_cost", pd.NA),
                    "ctr": item.get("ctr", pd.NA),
                    "cover_url": item.get("cover_url", ""),
                    "content_url": item.get("content_url", ""),
                    "source_time": item.get("source_time", ""),
                    "metadata_source": item.get("metadata_source", ""),
                    "metadata_confidence": item.get("metadata_confidence", pd.NA),
                    "metadata_fetched_at": item.get("metadata_fetched_at", ""),
                    "metadata_error": item.get("metadata_error", ""),
                    "metadata_review_reason": item.get("metadata_review_reason", ""),
                    "metadata_tags": item.get("metadata_tags", ""),
                    "metadata_content_type_candidate": item.get("metadata_content_type_candidate", ""),
                    "performance_flag": _content_performance_flag(
                        item, spend_threshold, activation_median, cost_median
                    ),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "channel",
            "content_id",
            "material_id",
            "title",
            "account_id",
            "account",
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
            "cover_url",
            "content_url",
            "source_time",
            "metadata_source",
            "metadata_confidence",
            "metadata_fetched_at",
            "metadata_error",
            "metadata_review_reason",
            "metadata_tags",
            "metadata_content_type_candidate",
            "performance_flag",
        ],
    )


def _content_performance_flag(
    item: pd.Series,
    spend_threshold: float,
    activation_median: float,
    cost_median: float,
) -> str:
    spend = parse_number(item.get("spend"))
    activations = parse_number(item.get("activations"))
    first_pay = parse_number(item.get("first_pay_count"))
    activation_cost = parse_number(item.get("activation_cost"))
    if first_pay > 0 and spend >= spend_threshold:
        return "爆款候选"
    if activations > 0 and not pd.isna(activation_cost) and activation_cost <= cost_median:
        return "高转化低成本"
    if spend >= spend_threshold and activations <= activation_median:
        return "高消耗低转化"
    return "常规观察"


def _summarize_cover_metrics(canonical: pd.DataFrame) -> pd.DataFrame:
    scoped = canonical[
        _bilibili_mask(canonical) | canonical["channel"].astype(str).str.contains("小红书", na=False)
    ].copy()
    if scoped.empty:
        return pd.DataFrame(
            columns=[
                "channel",
                "title",
                "account_id",
                "account",
                "manual_category",
                "ai_category",
                "content_category",
                "cover_url",
                "content_url",
                "spend",
                "impressions",
                "clicks",
                "ctr",
                "activations",
                "activation_cost",
            ]
        )
    columns = [
        "channel",
        "title",
        "account_id",
        "account",
        "manual_category",
        "ai_category",
        "content_category",
        "cover_url",
        "content_url",
        "spend",
        "impressions",
        "clicks",
        "ctr",
        "activations",
        "activation_cost",
    ]
    return scoped.sort_values("spend", ascending=False)[columns].head(50).reset_index(drop=True)


def _rank_pct(series: pd.Series) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if len(clean) == 0:
        return clean
    return clean.rank(pct=True, method="average")


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce").astype(float)
    denominator = pd.to_numeric(denominator, errors="coerce").astype(float)
    result = pd.Series(pd.NA, index=numerator.index, dtype="Float64")
    mask = denominator.ne(0.0)
    result.loc[mask] = numerator.loc[mask] / denominator.loc[mask]
    return result


def _sum_or_blank(series: pd.Series) -> float:
    total = pd.to_numeric(series, errors="coerce").sum(min_count=1)
    return float(total) if not pd.isna(total) else float("nan")


def _sum_or_zero(series: pd.Series) -> float:
    total = pd.to_numeric(series, errors="coerce").sum(min_count=1)
    return float(total) if not pd.isna(total) else 0.0


def _join_non_blank(values: Iterable[object]) -> str:
    tokens = [str(value).strip() for value in values if not _is_blank(value)]
    return " / ".join(tokens)


def _join_unique_nonblank(series: pd.Series) -> str:
    values: list[str] = []
    for value in series:
        if _is_blank(value):
            continue
        text = str(value).strip()
        if text and text not in values:
            values.append(text)
    return "、".join(values)


def _clean_identifier(value: object) -> str:
    if _is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _is_blank(value: object) -> bool:
    if value is None or pd.isna(value):
        return True
    return str(value).strip() == ""


def _iter_tabular_files(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in Path(input_dir).rglob("*")
        if path.is_file()
        and path.suffix.lower() in TABULAR_SUFFIXES
        and not path.name.startswith("~$")
        and not _is_generated_channel_clean_file(path)
        and "channel_clean" not in path.parts
    )


def _is_csv(path: Path) -> bool:
    return Path(path).suffix.lower() == ".csv"


def _is_generated_channel_clean_file(path: Path) -> bool:
    return Path(path).stem.lower().endswith("_clean")


def _default_content_ledger_config() -> Path | None:
    candidate = Path("config/feishu_sources.yml")
    return candidate if candidate.exists() else None


def _default_year_from_period(period_start: str, period_end: str) -> int:
    for value in [period_start, period_end]:
        try:
            return date.fromisoformat(str(value)).year
        except ValueError:
            continue
    return date.today().year


def _read_table(
    path: Path,
    sheet_name: object = 0,
    header: object = 0,
    nrows: Optional[int] = None,
) -> pd.DataFrame:
    path = Path(path)
    if _is_csv(path):
        last_error: Optional[Exception] = None
        for encoding in ["utf-8-sig", "utf-8", "gbk"]:
            try:
                return pd.read_csv(path, header=header, nrows=nrows, encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error:
            raise last_error
        return pd.read_csv(path, header=header, nrows=nrows)
    return pd.read_excel(path, sheet_name=sheet_name, header=header, nrows=nrows)


def parse_number(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "/":
        return float("nan")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return float("nan")
    number = float(match.group(0))
    suffix = text[match.end() : match.end() + 1].lower()
    if suffix in {"w", "万"}:
        number *= 10000
    elif suffix in {"y", "亿"}:
        number *= 100000000
    return number
