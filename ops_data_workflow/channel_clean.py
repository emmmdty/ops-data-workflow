"""Write per-channel cleaned workbooks for human review and handoff."""

from __future__ import annotations

from pathlib import Path
import re

import pandas as pd


CHANNEL_CLEAN_DIR = "channel_clean"
CHANNEL_CLEAN_SHEET = "清理后明细"
CHANNEL_CLEAN_COLUMNS = [
    "周期",
    "渠道",
    "账号",
    "内容类型",
    "内容分类",
    "标题",
    "id/BV或者唯一标识",
    "内容链接",
    "消耗",
    "曝光量",
    "激活数",
    "激活成本",
    "付费",
    "付费成本",
    "匹配来源",
    "复核原因",
]


def write_channel_clean_workbooks(
    canonical: pd.DataFrame,
    output_dir: Path,
    *,
    period_label: str = "",
    period_start: str = "",
    period_end: str = "",
) -> list[Path]:
    """Write one compact clean workbook per source file/channel."""
    if canonical.empty:
        return []

    output_root = Path(output_dir) / CHANNEL_CLEAN_DIR
    output_root.mkdir(parents=True, exist_ok=True)
    workbook_paths: list[Path] = []
    used_names: set[str] = set()
    prepared = canonical.copy()
    prepared["_channel_clean_source"] = prepared.apply(_source_key, axis=1)

    for source, group in prepared.groupby("_channel_clean_source", sort=False, dropna=False):
        filename = _unique_clean_filename(str(source), used_names)
        workbook_path = output_root / filename
        frame = _channel_clean_frame(
            group.drop(columns=["_channel_clean_source"], errors="ignore"),
            period_label=period_label,
            period_start=period_start,
            period_end=period_end,
        )
        with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name=CHANNEL_CLEAN_SHEET, index=False)
        workbook_paths.append(workbook_path)
    return workbook_paths


def _channel_clean_frame(
    frame: pd.DataFrame,
    *,
    period_label: str,
    period_start: str,
    period_end: str,
) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    result["周期"] = _period_value(frame, period_label, period_start, period_end)
    result["渠道"] = _text_series(frame, "channel")
    result["账号"] = _text_series(frame, "account")
    result["内容类型"] = _first_text_series(frame, ["manual_category", "ledger_content_type"])
    result["内容分类"] = _first_text_series(
        frame,
        ["content_category", "category_l2", "category_display", "manual_category", "ledger_content_type"],
    )
    result["标题"] = _text_series(frame, "title")
    result["id/BV或者唯一标识"] = frame.apply(_unique_identifier, axis=1)
    result["内容链接"] = _text_series(frame, "content_url")
    result["消耗"] = _numeric_series(frame, "spend")
    result["曝光量"] = _numeric_series(frame, "impressions")
    result["激活数"] = _numeric_series(frame, "activations")
    result["激活成本"] = _numeric_series(frame, "activation_cost")
    result["付费"] = _numeric_series(frame, "first_pay_count")
    result["付费成本"] = _numeric_series(frame, "first_pay_cost")
    result["匹配来源"] = _text_series(frame, "ledger_match_source")
    result["复核原因"] = _review_reason_series(frame)
    return result[CHANNEL_CLEAN_COLUMNS].reset_index(drop=True)


def _source_key(row: pd.Series) -> str:
    source_file = _clean_text(row.get("source_file", ""))
    if source_file:
        return Path(source_file).name
    channel = _clean_text(row.get("channel", ""))
    return f"{channel or '未命名渠道'}.xlsx"


def _unique_clean_filename(source: str, used_names: set[str]) -> str:
    stem = Path(source).stem or source or "未命名渠道"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem).strip() or "未命名渠道"
    base = f"{stem}_clean.xlsx"
    candidate = base
    counter = 2
    while candidate in used_names:
        candidate = f"{stem}_{counter}_clean.xlsx"
        counter += 1
    used_names.add(candidate)
    return candidate


def _period_value(frame: pd.DataFrame, period_label: str, period_start: str, period_end: str) -> str:
    if period_label:
        return period_label
    if period_start and period_end:
        return f"{period_start} 至 {period_end}"
    start = _first_column_text(frame, "period_start")
    end = _first_column_text(frame, "period_end")
    if start and end:
        return f"{start} 至 {end}"
    return ""


def _unique_identifier(row: pd.Series) -> str:
    content_id = _clean_text(row.get("content_id", ""))
    if content_id:
        return content_id
    title = _clean_text(row.get("title", ""))
    if _is_douyin(row) and title:
        return _strip_tags(title)
    material_id = _clean_text(row.get("material_id", ""))
    if material_id:
        return material_id
    return _strip_tags(title)


def _is_douyin(row: pd.Series) -> bool:
    text = " ".join(
        _clean_text(row.get(column, ""))
        for column in ["platform_group", "platform", "channel"]
    )
    return "抖音" in text


def _strip_tags(value: object) -> str:
    text = _clean_text(value)
    text = re.sub(r"[#＃]\s*[^#＃\s]+", "", text)
    return " ".join(text.split()).strip()


def _first_text_series(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series([""] * len(frame), index=frame.index, dtype=object)
    for column in columns:
        if column not in frame.columns:
            continue
        values = frame[column].map(_clean_text)
        mask = result.eq("") & values.ne("")
        result.loc[mask] = values.loc[mask]
    return result


def _text_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype=object)
    return frame[column].map(_clean_text)


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([pd.NA] * len(frame), index=frame.index, dtype=object)
    numeric = pd.to_numeric(frame[column], errors="coerce")
    return numeric.where(numeric.notna(), pd.NA)


def _review_reason_series(frame: pd.DataFrame) -> pd.Series:
    result = _text_series(frame, "match_risk_reason")
    if "review_reasons" not in frame.columns:
        return result
    fallback = frame["review_reasons"].map(_clean_review_reason)
    mask = result.eq("") & fallback.ne("")
    result.loc[mask] = fallback.loc[mask]
    return result


def _clean_review_reason(value: object) -> str:
    reasons = [
        reason
        for reason in _clean_text(value).split("；")
        if reason and reason != "内容ID缺失"
    ]
    return "；".join(reasons)


def _first_column_text(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    for value in frame[column].tolist():
        text = _clean_text(value)
        if text:
            return text
    return ""


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text
