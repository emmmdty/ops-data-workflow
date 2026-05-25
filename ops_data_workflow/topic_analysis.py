"""Build and summarize focused channel topic labels."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping, Optional

import pandas as pd

from .ai import group_topic_labels, resolve_deepseek_settings
from .categories import DEFAULT_CATEGORY_RULES, suggest_category


TOPIC_LABEL_COLUMNS = [
    "channel",
    "content_id",
    "material_id",
    "title",
    "content_type",
    "topic_name",
    "rank_metric",
    "rank_value",
    "rank_position",
    "source",
    "provider",
    "model",
    "input_hash",
    "created_at",
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

PERSISTED_TOPIC_SUMMARY_COLUMNS = [
    "channel",
    "topic_name",
    "content_types",
    "item_count",
    "material_count",
    "spend",
    "spend_share",
    "impressions",
    "clicks",
    "ctr",
    "activations",
    "activation_cost",
    "first_pay_count",
    "first_pay_cost",
    "first_pay_rate",
]

TopicLabeler = Callable[[pd.DataFrame, Optional[Path]], Mapping[int, str]]

INVALID_TEXT_VALUES = {"", "nan", "none", "null", "nat", "<na>"}
UNMATCHED_TOPIC_NAME = "未匹配题材"
UNMATCHED_CONTENT_TYPE = "未匹配"

LOCAL_TOPIC_KEYWORDS = [
    ("同花顺进行曲", ["同花顺进行曲", "真英雄", "唱这首歌", "专属bgm", "bgm", "伴奏", "合唱"]),
    ("交易心法", ["打开k线图", "悟道", "交易心法", "稳定盈利", "实践与总结"]),
    ("股友说", ["股友说", "股民交流", "同花顺社区", "交易高手", "炒股之路", "股民", "炒股"]),
    ("热点行情", ["板块", "a股", "大涨", "涨停", "芯片", "脑机接口", "主力", "指数", "产业链"]),
    ("盘点", ["盘点", "前十", "top", "排行", " vs ", "VS"]),
    ("财商动画", ["财商", "贫穷的人", "富有的人", "先享受", "理财"]),
    ("问财", ["问财", "问财skill", "问句"]),
    ("投资入门", ["小白", "选股法", "做t", "etf", "基金", "ppi"]),
    ("大佬采访", ["采访", "冠军", "孙辉", "陈小群", "鑫多多"]),
]


def channel_topic_limit(channel: object) -> int:
    text = str(channel or "").strip()
    if not text or "达人" in text:
        return 0
    if "抖音" in text:
        return 20
    if "小红书" in text:
        return 10
    if "B站" in text:
        return 10
    return 10


def select_topic_candidates(
    items: pd.DataFrame,
    channel: object,
    *,
    metric: str = "spend",
) -> pd.DataFrame:
    channel_name = str(channel or "").strip()
    limit = channel_topic_limit(channel_name)
    if items.empty or not channel_name or limit <= 0 or metric not in items.columns:
        return pd.DataFrame()
    scoped = items[items.get("channel", pd.Series(dtype=object)).astype(str).eq(channel_name)].copy()
    if scoped.empty:
        return pd.DataFrame()
    scoped[metric] = pd.to_numeric(scoped[metric], errors="coerce")
    scoped = scoped[scoped[metric].notna()].copy()
    if scoped.empty:
        return pd.DataFrame()
    candidates = scoped.sort_values(metric, ascending=False).head(limit).copy()
    candidates["rank_metric"] = metric
    candidates["rank_value"] = candidates[metric].astype(float)
    candidates["rank_position"] = range(1, len(candidates) + 1)
    candidates["content_type"] = candidates.apply(_content_type_for_row, axis=1)
    return candidates


def build_topic_label_frame(
    items: pd.DataFrame,
    *,
    env_path: Optional[Path] = None,
    topic_labeler: Optional[TopicLabeler] = None,
) -> pd.DataFrame:
    if items.empty or "channel" not in items.columns:
        return pd.DataFrame(columns=TOPIC_LABEL_COLUMNS)

    settings = resolve_deepseek_settings(env_path)
    labeler = topic_labeler or group_topic_labels
    created_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, object]] = []
    for channel in _ordered_channels(items):
        candidates = select_topic_candidates(items, channel)
        if candidates.empty:
            continue
        ai_labels = labeler(candidates, env_path) if settings.configured or topic_labeler is not None else {}
        for index, row in candidates.iterrows():
            raw_label = str(ai_labels.get(int(index), "")).strip()
            label = _valid_ai_label(raw_label, row)
            if label:
                topic_name = label
                source = "ai"
            else:
                topic_name, source = _local_topic_label(row)
            rows.append(
                {
                    "channel": str(row.get("channel", "") or ""),
                    "content_id": str(row.get("content_id", "") or ""),
                    "material_id": str(row.get("material_id", "") or ""),
                    "title": str(row.get("title", "") or ""),
                    "content_type": str(row.get("content_type", "") or ""),
                    "topic_name": topic_name,
                    "rank_metric": str(row.get("rank_metric", "spend") or "spend"),
                    "rank_value": _number(row.get("rank_value")),
                    "rank_position": int(row.get("rank_position", 0) or 0),
                    "source": source,
                    "provider": "deepseek" if source == "ai" else "local",
                    "model": settings.model if source == "ai" else "",
                    "input_hash": _input_hash(row),
                    "created_at": created_at,
                    "spend": _number(row.get("spend")),
                    "impressions": _number(row.get("impressions")),
                    "clicks": _number(row.get("clicks")),
                    "ctr": _number(row.get("ctr")),
                    "activations": _number(row.get("activations")),
                    "activation_cost": _number(row.get("activation_cost")),
                    "first_pay_count": _number(row.get("first_pay_count")),
                    "first_pay_cost": _number(row.get("first_pay_cost")),
                    "first_pay_rate": _number(row.get("first_pay_rate")),
                }
            )
    return pd.DataFrame(rows, columns=TOPIC_LABEL_COLUMNS)


def summarize_persisted_topic_labels(topic_labels: pd.DataFrame, channel: object) -> pd.DataFrame:
    channel_name = str(channel or "").strip()
    if topic_labels.empty or not channel_name:
        return pd.DataFrame(columns=PERSISTED_TOPIC_SUMMARY_COLUMNS)
    labels = topic_labels[topic_labels["channel"].astype(str).eq(channel_name)].copy()
    if labels.empty:
        return pd.DataFrame(columns=PERSISTED_TOPIC_SUMMARY_COLUMNS)
    for column in ["spend", "impressions", "clicks", "activations", "first_pay_count"]:
        if column not in labels.columns:
            labels[column] = 0.0
        labels[column] = pd.to_numeric(labels[column], errors="coerce").fillna(0.0)
    for column in ["topic_name", "content_type", "material_id"]:
        if column not in labels.columns:
            labels[column] = ""
        labels[column] = labels[column].fillna("").astype(str).str.strip()
    labels.loc[labels["topic_name"].eq(""), "topic_name"] = "未命名题材"

    grouped = (
        labels.groupby(["channel", "topic_name"], dropna=False)
        .agg(
            content_types=("content_type", _join_unique),
            item_count=("topic_name", "size"),
            material_count=("material_id", _nunique_nonblank),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
        )
        .reset_index()
    )
    total_spend = float(grouped["spend"].sum()) if not grouped.empty else 0.0
    grouped["spend_share"] = grouped["spend"].map(lambda value: float(value) / total_spend if total_spend else 0.0)
    grouped["ctr"] = _safe_divide(grouped["clicks"], grouped["impressions"])
    grouped["activation_cost"] = _safe_divide(grouped["spend"], grouped["activations"])
    grouped["first_pay_cost"] = _safe_divide(grouped["spend"], grouped["first_pay_count"])
    grouped["first_pay_rate"] = _safe_divide(grouped["first_pay_count"], grouped["activations"])
    return grouped.sort_values(["spend", "activations"], ascending=[False, False])[
        PERSISTED_TOPIC_SUMMARY_COLUMNS
    ].reset_index(drop=True)


def _ordered_channels(items: pd.DataFrame) -> list[str]:
    values: list[str] = []
    for value in items["channel"].fillna("").astype(str):
        text = value.strip()
        if text and text not in values:
            values.append(text)
    return values


def _content_type_for_row(row: pd.Series) -> str:
    for column in ["content_category", "category_l2", "manual_category", "ai_category"]:
        value = _safe_text(row.get(column, ""))
        if value:
            return value
    return UNMATCHED_CONTENT_TYPE


def _valid_ai_label(label: str, row: pd.Series) -> str:
    clean = _clean_topic_label(label)
    if not clean or clean == "未命名题材":
        return ""
    if _matches_raw_identifier(clean, row):
        return ""
    return clean


def _local_topic_label(row: pd.Series) -> tuple[str, str]:
    for column in ["category_l3", "content_type"]:
        value = _clean_topic_label(row.get(column, ""))
        if _is_usable_topic(value) and _is_concise_rule_topic(value, row):
            return value, "local_rules"

    content_type = _content_type_for_row(row)
    if _is_usable_topic(content_type):
        return _clean_topic_label(content_type), "local_rules"

    title = _safe_text(row.get("title", ""))
    keyword_topic = _topic_from_keywords(title)
    if keyword_topic:
        return keyword_topic, "local_rules"

    suggested = suggest_category(title, DEFAULT_CATEGORY_RULES)
    suggested = _clean_topic_label(suggested)
    if _is_usable_topic(suggested):
        return suggested, "local_rules"

    return UNMATCHED_TOPIC_NAME, "local_unmatched"


def _clean_topic_label(label: object) -> str:
    text = _safe_text(label)
    if not text:
        return "未命名题材"
    for token in ["#", "【", "】", "《", "》", "「", "」"]:
        text = text.replace(token, "")
    text = " ".join(text.split())
    return text[:40] if text else "未命名题材"


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in INVALID_TEXT_VALUES else text


def _is_usable_topic(value: object) -> bool:
    text = _safe_text(value)
    return bool(text) and text not in {UNMATCHED_CONTENT_TYPE, "未命名题材", UNMATCHED_TOPIC_NAME}


def _is_concise_rule_topic(value: object, row: pd.Series) -> bool:
    text = _safe_text(value)
    if not _is_usable_topic(text) or _matches_raw_identifier(text, row):
        return False
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")) or any(suffix in lowered for suffix in [".mp4", ".mov"]):
        return False
    return len(text) <= 18 and len(text.split()) <= 2


def _matches_raw_identifier(label: str, row: pd.Series) -> bool:
    compact = _compact(label)
    if not compact:
        return False
    for column in ["title", "content_id", "material_id"]:
        raw = _compact(_safe_text(row.get(column, "")))
        if raw and compact == raw:
            return True
    return False


def _topic_from_keywords(title: object) -> str:
    text = _safe_text(title)
    if not text:
        return ""
    lower_text = text.lower()
    padded_text = f" {text} "
    for topic, keywords in LOCAL_TOPIC_KEYWORDS:
        for keyword in keywords:
            token = str(keyword).strip()
            if not token:
                continue
            if token.isupper() and token in text:
                return topic
            if token.startswith(" ") and token.endswith(" ") and token in padded_text:
                return topic
            if token.lower() in lower_text:
                return topic
    return ""


def _compact(value: object) -> str:
    text = _safe_text(value)
    for token in ["#", "【", "】", "《", "》", "「", "」", "，", ",", "。", "？", "?", "！", "!", "：", ":", "-", "_"]:
        text = text.replace(token, "")
    return "".join(text.split()).lower()


def _input_hash(row: pd.Series) -> str:
    payload = {
        "channel": str(row.get("channel", "") or ""),
        "title": str(row.get("title", "") or ""),
        "content_id": str(row.get("content_id", "") or ""),
        "material_id": str(row.get("material_id", "") or ""),
        "content_type": str(row.get("content_type", "") or ""),
        "category_l3": str(row.get("category_l3", "") or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _number(value: object) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(numeric) else float(numeric)


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce").astype(float)
    denominator = pd.to_numeric(denominator, errors="coerce").astype(float)
    result = pd.Series(0.0, index=numerator.index, dtype=float)
    mask = denominator.ne(0.0)
    result.loc[mask] = numerator.loc[mask] / denominator.loc[mask]
    return result


def _join_unique(series: pd.Series) -> str:
    values: list[str] = []
    for value in series:
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)
    return "、".join(values)


def _nunique_nonblank(series: pd.Series) -> int:
    values = series.fillna("").astype(str).str.strip()
    return int(values[values.ne("")].nunique())
