"""High-value content multimodal recap persistence and type aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from .platform_taxonomy import normalize_platform_classification
from .recap_settings import get_recap_settings
from .storage import (
    fill_missing_content_performance_types,
    persist_multimodal_recap_items,
    persist_strategy_recap_items,
    persist_type_recap_items,
)


ANALYSIS_PURPOSE_FILL_MISSING_TYPE = "fill_missing_type"
ANALYSIS_PURPOSE_STRATEGY_RECAP = "strategy_recap"
CLASSIFICATION_STATUS_FILLED = "filled"
CLASSIFICATION_STATUS_SKIPPED_EXISTING = "skipped_existing"
CLASSIFICATION_STATUS_REJECTED_INVALID_TAXONOMY = "rejected_invalid_taxonomy"
CLASSIFICATION_STATUS_NO_CLASSIFICATION = "no_classification"


MULTIMODAL_RECAP_COLUMNS = [
    "content_identity_key",
    "analysis_purpose",
    "evidence_source",
    "classification_write_status",
    "classification_write_reason",
    "platform",
    "channel",
    "content_id",
    "title",
    "category_l1",
    "category_l2",
    "bilibili_content_type",
    "content_form",
    "title_hook",
    "visual_structure",
    "information_density",
    "conversion_path",
    "reuse_points",
    "avoid_points",
    "next_period_strategy",
    "summary",
    "raw_result_json",
    "updated_at",
]
TYPE_RECAP_COLUMNS = [
    "batch_id",
    "platform",
    "type_level",
    "content_type",
    "item_count",
    "spend",
    "impressions",
    "activations",
    "first_pay_count",
    "activation_cost",
    "first_pay_cost",
    "value",
    "share",
]
STRATEGY_RECAP_COLUMNS = [
    "batch_id",
    "channel",
    "platform",
    "type_level",
    "content_type",
    "item_count",
    "metrics",
    "common_patterns",
    "reuse_points",
    "avoid_points",
    "next_period_actions",
    "supporting_content_identity_keys",
    "updated_at",
]


@dataclass(frozen=True)
class PersistedMultimodalRecap:
    item_count: int
    type_count: int
    strategy_count: int = 0


def persist_multimodal_recap(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    analysis_purpose: str = ANALYSIS_PURPOSE_STRATEGY_RECAP,
    analyzer: Callable[[pd.Series], Mapping[str, object]] | None = None,
) -> PersistedMultimodalRecap:
    items = build_multimodal_recap_items(
        top_content,
        analysis_purpose=analysis_purpose,
        analyzer=analyzer,
    )
    persist_multimodal_recap_items(db_path, batch_id, items)
    type_count = 0
    strategy_count = 0
    if analysis_purpose == ANALYSIS_PURPOSE_FILL_MISSING_TYPE:
        updates = _classification_updates(items)
        fill_missing_content_performance_types(db_path, batch_id, updates)
    else:
        strategy_recap = build_strategy_recap_items(db_path, batch_id, top_content, multimodal_results=items)
        strategy_count = persist_strategy_recap_items(db_path, batch_id, strategy_recap)
    return PersistedMultimodalRecap(item_count=int(len(items)), type_count=type_count, strategy_count=int(strategy_count))


def persist_type_recap_from_top_content(db_path: Path, batch_id: str, top_content: pd.DataFrame) -> int:
    type_recap = build_type_recap_items(db_path, batch_id, top_content)
    return persist_type_recap_items(db_path, batch_id, type_recap)


def build_multimodal_recap_items(
    top_content: pd.DataFrame,
    *,
    analysis_purpose: str = ANALYSIS_PURPOSE_STRATEGY_RECAP,
    analyzer: Callable[[pd.Series], Mapping[str, object]] | None = None,
) -> pd.DataFrame:
    if top_content is None or top_content.empty:
        return pd.DataFrame(columns=MULTIMODAL_RECAP_COLUMNS)
    rows: list[dict[str, object]] = []
    now = datetime.now(timezone.utc).isoformat()
    for _, source in top_content.iterrows():
        result = dict(analyzer(source) if analyzer else {})
        platform = _platform(source)
        source_classification = _source_classification(source, platform)
        ai_classification = _ai_classification(result, platform)
        source_complete = _classification_complete_for_platform(platform, source_classification)
        ai_complete = _classification_complete_for_platform(platform, ai_classification)
        classification = source_classification if source_complete else (ai_classification if ai_complete else source_classification)
        status, reason = _classification_write_status(
            analysis_purpose,
            result,
            platform,
            source_classification,
            ai_classification,
        )
        rows.append(
            {
                "content_identity_key": _text(source.get("content_identity_key")),
                "analysis_purpose": analysis_purpose,
                "evidence_source": _evidence_source(source),
                "classification_write_status": status,
                "classification_write_reason": reason,
                "platform": platform,
                "channel": _text(source.get("channel")),
                "content_id": _text(source.get("content_id")),
                "title": _text(source.get("title")),
                "category_l1": classification.primary_type,
                "category_l2": classification.secondary_type,
                "bilibili_content_type": classification.bilibili_type,
                "content_form": _first_text(result, "内容形态", "content_form") or _text(source.get("content_form")),
                "title_hook": _first_text(result, "标题钩子", "title_hook"),
                "visual_structure": _first_text(result, "视觉结构", "visual_structure"),
                "information_density": _first_text(result, "信息密度", "information_density"),
                "conversion_path": _first_text(result, "转化路径", "conversion_path"),
                "reuse_points": _first_text(result, "可复用点", "reuse_points"),
                "avoid_points": _first_text(result, "不建议复用点", "avoid_points"),
                "next_period_strategy": _first_text(result, "下周期策略建议", "next_period_strategy"),
                "summary": _first_text(result, "共性总结", "可复用点", "summary"),
                "raw_result_json": json.dumps(result, ensure_ascii=False, sort_keys=True),
                "updated_at": now,
            }
        )
    return pd.DataFrame(rows, columns=MULTIMODAL_RECAP_COLUMNS)


def build_type_recap_items(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    multimodal_results: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if top_content is None or top_content.empty:
        return pd.DataFrame(columns=TYPE_RECAP_COLUMNS)
    settings = get_recap_settings(db_path)
    frame = top_content.copy()
    results_by_identity = _results_by_identity(multimodal_results)
    for column in ["platform", "platform_group", "channel", "content_identity_key", "category_l1", "category_l2", "bilibili_content_type", "content_type"]:
        if column not in frame.columns:
            frame[column] = ""
    for column in ["spend", "impressions", "activations", "first_pay_count"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

    records: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        platform = _platform(row)
        if platform not in {"抖音", "小红书", "B站"}:
            continue
        result = results_by_identity.get(_text(row.get("content_identity_key")), {})
        classification = _resolved_classification(row, result, platform)
        if platform == "抖音":
            if classification.primary_valid:
                records.append(_metric_record(batch_id, platform, "douyin_l1", classification.primary_type, row, settings))
            if classification.secondary_valid:
                records.append(_metric_record(batch_id, platform, "douyin_l2", classification.secondary_type, row, settings))
        elif platform == "小红书":
            if classification.primary_valid:
                records.append(_metric_record(batch_id, platform, "xhs_l1", classification.primary_type, row, settings))
            if classification.secondary_valid:
                records.append(_metric_record(batch_id, platform, "xhs_l2", classification.secondary_type, row, settings))
        elif platform == "B站":
            if classification.bilibili_valid:
                records.append(_metric_record(batch_id, platform, "bilibili", classification.bilibili_type, row, settings))
    raw = pd.DataFrame(records)
    if raw.empty:
        return pd.DataFrame(columns=TYPE_RECAP_COLUMNS)
    grouped = (
        raw.groupby(["batch_id", "platform", "type_level", "content_type"], as_index=False, dropna=False)
        .agg(
            item_count=("item_count", "sum"),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
            value=("value", "sum"),
        )
    )
    grouped["activation_cost"] = grouped.apply(lambda row: _ratio(row["spend"], row["activations"]), axis=1)
    grouped["first_pay_cost"] = grouped.apply(lambda row: _ratio(row["spend"], row["first_pay_count"]), axis=1)
    totals = grouped.groupby(["platform", "type_level"], dropna=False)["value"].transform("sum")
    grouped["share"] = grouped.apply(lambda row: _ratio(row["value"], totals.loc[row.name]), axis=1)
    grouped = grouped.sort_values(["platform", "type_level", "value", "spend"], ascending=[True, True, False, False])
    return grouped[TYPE_RECAP_COLUMNS].reset_index(drop=True)


def build_strategy_recap_items(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    multimodal_results: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if top_content is None or top_content.empty:
        return pd.DataFrame(columns=STRATEGY_RECAP_COLUMNS)
    settings = get_recap_settings(db_path)
    frame = top_content.copy()
    results_by_identity = _results_by_identity(multimodal_results)
    for column in ["platform", "platform_group", "channel", "content_identity_key", "category_l1", "category_l2", "bilibili_content_type", "content_type"]:
        if column not in frame.columns:
            frame[column] = ""
    for column in ["spend", "impressions", "activations", "first_pay_count"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

    records: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        platform = _platform(row)
        result = results_by_identity.get(_text(row.get("content_identity_key")), {})
        classification = _resolved_classification(row, result, platform)
        for type_level, content_type in _strategy_type_entries(platform, classification):
            records.append(
                {
                    "batch_id": batch_id,
                    "channel": _text(row.get("channel")),
                    "platform": platform,
                    "type_level": type_level,
                    "content_type": content_type,
                    "content_identity_key": _text(row.get("content_identity_key")),
                    "spend": _number(row.get("spend")),
                    "impressions": _number(row.get("impressions")),
                    "activations": _number(row.get("activations")),
                    "first_pay_count": _number(row.get("first_pay_count")),
                    "value": _number(row.get("activations")) * settings.activation_weight
                    + _number(row.get("first_pay_count")) * settings.first_pay_weight,
                    "summary": _first_text(result, "summary", "共性总结"),
                    "reuse_points": _first_text(result, "reuse_points", "可复用点"),
                    "avoid_points": _first_text(result, "avoid_points", "不建议复用点"),
                    "next_period_strategy": _first_text(result, "next_period_strategy", "下周期策略建议"),
                }
            )
    raw = pd.DataFrame(records)
    if raw.empty:
        return pd.DataFrame(columns=STRATEGY_RECAP_COLUMNS)
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, object]] = []
    for keys, group in raw.groupby(["batch_id", "channel", "platform", "type_level", "content_type"], dropna=False, sort=False):
        group = group.copy()
        metrics = {
            "spend": float(group["spend"].sum()),
            "impressions": float(group["impressions"].sum()),
            "activations": float(group["activations"].sum()),
            "first_pay_count": float(group["first_pay_count"].sum()),
            "value": float(group["value"].sum()),
        }
        rows.append(
            {
                "batch_id": keys[0],
                "channel": keys[1],
                "platform": keys[2],
                "type_level": keys[3],
                "content_type": keys[4],
                "item_count": int(len(group)),
                "metrics": json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                "common_patterns": _join_unique(group["summary"]),
                "reuse_points": _join_unique(group["reuse_points"]),
                "avoid_points": _join_unique(group["avoid_points"]),
                "next_period_actions": _join_unique(group["next_period_strategy"]),
                "supporting_content_identity_keys": json.dumps(
                    [_text(value) for value in group["content_identity_key"].tolist() if _text(value)],
                    ensure_ascii=False,
                ),
                "updated_at": now,
            }
        )
    return pd.DataFrame(rows, columns=STRATEGY_RECAP_COLUMNS).sort_values(
        ["channel", "platform", "type_level", "content_type"],
        ascending=[True, True, True, True],
    ).reset_index(drop=True)


def _metric_record(batch_id: str, platform: str, type_level: str, content_type: str, row: pd.Series, settings) -> dict[str, object]:
    activations = _number(row.get("activations"))
    first_pay = _number(row.get("first_pay_count"))
    spend = _number(row.get("spend"))
    return {
        "batch_id": batch_id,
        "platform": platform,
        "type_level": type_level,
        "content_type": content_type or "未识别",
        "item_count": 1,
        "spend": spend,
        "impressions": _number(row.get("impressions")),
        "activations": activations,
        "first_pay_count": first_pay,
        "value": activations * settings.activation_weight + first_pay * settings.first_pay_weight,
    }


def _resolved_classification(
    source: pd.Series,
    result: Mapping[str, object],
    platform: str,
):
    ai_classification = _ai_classification(result, platform)
    if _classification_complete_for_platform(platform, ai_classification):
        return ai_classification
    return _source_classification(source, platform)


def _source_classification(source: pd.Series, platform: str):
    return normalize_platform_classification(
        platform,
        category_l1=_text(source.get("category_l1")),
        category_l2=_text(source.get("category_l2")),
        bilibili_content_type=_text(source.get("bilibili_content_type")),
        content_type=_text(source.get("content_type")),
    )


def _ai_classification(result: Mapping[str, object], platform: str):
    return normalize_platform_classification(
        platform,
        category_l1=_result_text(result, "一级内容类型", "category_l1"),
        category_l2=_result_text(result, "二级内容类型", "category_l2"),
        bilibili_content_type=_result_text(result, "B站内容类型", "bilibili_content_type"),
        content_type=_result_text(result, "内容类型", "content_type"),
    )


def _classification_write_status(
    analysis_purpose: str,
    result: Mapping[str, object],
    platform: str,
    source_classification,
    ai_classification,
) -> tuple[str, str]:
    raw_values = [
        _result_text(result, "一级内容类型", "category_l1"),
        _result_text(result, "二级内容类型", "category_l2"),
        _result_text(result, "B站内容类型", "bilibili_content_type"),
        _result_text(result, "内容类型", "content_type"),
    ]
    has_raw = any(_text(value) for value in raw_values)
    source_complete = _classification_complete_for_platform(platform, source_classification)
    ai_complete = _classification_complete_for_platform(platform, ai_classification)
    if analysis_purpose != ANALYSIS_PURPOSE_FILL_MISSING_TYPE:
        if source_complete:
            return CLASSIFICATION_STATUS_SKIPPED_EXISTING, "策略复盘不执行类型写入；已有内容类型仅用于聚合。"
        if ai_complete:
            return CLASSIFICATION_STATUS_NO_CLASSIFICATION, "策略复盘不执行类型写入；AI 分类仅用于渠道类型聚合。"
        if has_raw:
            return CLASSIFICATION_STATUS_REJECTED_INVALID_TAXONOMY, f"AI 输出不在 harvester taxonomy 内，策略复盘不采用该分类：{' / '.join(_text(value) for value in raw_values if _text(value))}"
        return CLASSIFICATION_STATUS_NO_CLASSIFICATION, "策略复盘未输出可用于聚合的内容类型。"
    if source_complete:
        return CLASSIFICATION_STATUS_SKIPPED_EXISTING, "已有内容类型，仅保留原值。"
    if ai_complete:
        return CLASSIFICATION_STATUS_FILLED, "AI 输出通过 harvester taxonomy 校验，已允许填充空值。"
    if has_raw:
        return CLASSIFICATION_STATUS_REJECTED_INVALID_TAXONOMY, f"AI 输出不在 harvester taxonomy 内：{' / '.join(_text(value) for value in raw_values if _text(value))}"
    return CLASSIFICATION_STATUS_NO_CLASSIFICATION, "AI 未输出可用内容类型。"


def _classification_complete_for_platform(platform: str, classification) -> bool:
    if platform in {"抖音", "小红书"}:
        if not classification.primary_valid:
            return False
        allowed_secondary_required = bool(classification.secondary_type) or _requires_secondary(platform, classification.primary_type)
        return classification.secondary_valid if allowed_secondary_required else True
    if platform == "B站":
        return bool(classification.bilibili_valid)
    return classification.has_any_valid_type


def _requires_secondary(platform: str, primary_type: str) -> bool:
    if platform == "抖音":
        from .platform_taxonomy import DOUYIN_TAXONOMY

        return bool(DOUYIN_TAXONOMY.get(primary_type))
    if platform == "小红书":
        from .platform_taxonomy import XHS_TAXONOMY

        return bool(XHS_TAXONOMY.get(primary_type))
    return False


def _classification_updates(items: pd.DataFrame) -> list[dict[str, object]]:
    if items is None or items.empty:
        return []
    updates: list[dict[str, object]] = []
    for _, row in items.iterrows():
        if _text(row.get("classification_write_status")) != CLASSIFICATION_STATUS_FILLED:
            continue
        platform = _text(row.get("platform"))
        update = {
            "content_identity_key": _text(row.get("content_identity_key")),
            "category_l1": _text(row.get("category_l1")) if platform != "B站" else "",
            "category_l2": _text(row.get("category_l2")) if platform != "B站" else "",
            "bilibili_content_type": _text(row.get("bilibili_content_type")) if platform == "B站" else "",
            "content_type": _text(row.get("bilibili_content_type")) if platform == "B站" else _text(row.get("category_l2")) or _text(row.get("category_l1")),
        }
        updates.append(update)
    return updates


def _strategy_type_entries(platform: str, classification) -> list[tuple[str, str]]:
    if platform == "抖音":
        entries = []
        if classification.primary_valid:
            entries.append(("douyin_l1", classification.primary_type))
        if classification.secondary_valid:
            entries.append(("douyin_l2", classification.secondary_type))
        return entries
    if platform == "小红书":
        entries = []
        if classification.primary_valid:
            entries.append(("xhs_l1", classification.primary_type))
        if classification.secondary_valid:
            entries.append(("xhs_l2", classification.secondary_type))
        return entries
    if platform == "B站" and classification.bilibili_valid:
        return [("bilibili", classification.bilibili_type)]
    return []


def _evidence_source(source: pd.Series) -> str:
    source_kind = _text(source.get("source_kind"))
    if source_kind == "manual" or _text(source.get("evidence_path")):
        return "manual"
    if _text(source.get("ad_material_url")) or _text(source.get("ad_cover_url")) or _text(source.get("ad_material_id")):
        return "ad_material"
    return "work_asset"


def _join_unique(values: object) -> str:
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return "；".join(result)


def _results_by_identity(frame: pd.DataFrame | None) -> dict[str, Mapping[str, object]]:
    if frame is None or frame.empty or "content_identity_key" not in frame.columns:
        return {}
    return {
        _text(row.get("content_identity_key")): row.to_dict()
        for _, row in frame.iterrows()
        if _text(row.get("content_identity_key"))
    }


def _platform(row: pd.Series) -> str:
    text = " ".join(_text(row.get(column)) for column in ["platform", "platform_group", "channel"])
    lowered = text.lower()
    if "抖音" in text or "douyin" in lowered:
        return "抖音"
    if "小红书" in text or "xhs" in lowered or "xiaohongshu" in lowered:
        return "小红书"
    if "B站" in text or "哔哩" in text or "bilibili" in lowered:
        return "B站"
    return _text(row.get("platform"))


def _first_text(mapping: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _result_text(mapping: Mapping[str, object], *keys: str) -> str:
    return _first_text(mapping, *keys)


def _ratio(numerator: object, denominator: object) -> float:
    denominator_value = _number(denominator)
    return 0.0 if denominator_value == 0 else _number(numerator) / denominator_value


def _number(value: object) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text
