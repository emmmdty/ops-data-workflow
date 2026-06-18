"""High-value content multimodal recap persistence and type aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from .recap_settings import get_recap_settings
from .storage import persist_multimodal_recap_items, persist_type_recap_items


MULTIMODAL_RECAP_COLUMNS = [
    "content_identity_key",
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


@dataclass(frozen=True)
class PersistedMultimodalRecap:
    item_count: int
    type_count: int


def persist_multimodal_recap(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    analyzer: Callable[[pd.Series], Mapping[str, object]] | None = None,
) -> PersistedMultimodalRecap:
    items = build_multimodal_recap_items(top_content, analyzer=analyzer)
    type_recap = build_type_recap_items(db_path, batch_id, top_content, multimodal_results=items)
    persist_multimodal_recap_items(db_path, batch_id, items)
    persist_type_recap_items(db_path, batch_id, type_recap)
    return PersistedMultimodalRecap(item_count=int(len(items)), type_count=int(len(type_recap)))


def build_multimodal_recap_items(
    top_content: pd.DataFrame,
    *,
    analyzer: Callable[[pd.Series], Mapping[str, object]] | None = None,
) -> pd.DataFrame:
    if top_content is None or top_content.empty:
        return pd.DataFrame(columns=MULTIMODAL_RECAP_COLUMNS)
    rows: list[dict[str, object]] = []
    now = datetime.now(timezone.utc).isoformat()
    for _, source in top_content.iterrows():
        result = dict(analyzer(source) if analyzer else {})
        category_l1 = _first_text(result, "一级内容类型", "category_l1") or _text(source.get("category_l1"))
        category_l2 = _first_text(result, "二级内容类型", "category_l2") or _text(source.get("category_l2")) or _text(source.get("content_type"))
        platform = _platform(source)
        if platform == "抖音" and not category_l2 and category_l1 in {"长视频", "说唱"}:
            category_l2 = category_l1
        bilibili_type = _first_text(result, "B站内容类型", "bilibili_content_type") or _text(source.get("bilibili_content_type"))
        if platform == "B站" and not bilibili_type:
            bilibili_type = category_l2 or _text(source.get("content_type"))
        rows.append(
            {
                "content_identity_key": _text(source.get("content_identity_key")),
                "platform": platform,
                "channel": _text(source.get("channel")),
                "content_id": _text(source.get("content_id")),
                "title": _text(source.get("title")),
                "category_l1": category_l1,
                "category_l2": category_l2,
                "bilibili_content_type": bilibili_type,
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
        category_l1 = _result_text(result, "一级内容类型", "category_l1") or _text(row.get("category_l1"))
        category_l2 = (
            _result_text(result, "二级内容类型", "category_l2")
            or _text(row.get("category_l2"))
            or _text(row.get("content_type"))
        )
        if platform == "抖音" and not category_l2 and category_l1 in {"长视频", "说唱"}:
            category_l2 = category_l1
        bilibili_type = (
            _result_text(result, "B站内容类型", "bilibili_content_type")
            or _text(row.get("bilibili_content_type"))
            or _text(row.get("content_type"))
            or category_l2
        )
        if platform == "抖音":
            records.append(_metric_record(batch_id, platform, "douyin_l1", category_l1, row, settings))
            records.append(_metric_record(batch_id, platform, "douyin_l2", category_l2, row, settings))
        elif platform == "小红书":
            records.append(_metric_record(batch_id, platform, "xhs_l1", category_l1, row, settings))
            records.append(_metric_record(batch_id, platform, "xhs_l2", category_l2, row, settings))
        elif platform == "B站":
            records.append(_metric_record(batch_id, platform, "bilibili", bilibili_type, row, settings))
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
