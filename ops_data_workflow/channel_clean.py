"""Write per-channel cleaned workbooks for human review and handoff."""

from __future__ import annotations

from pathlib import Path
import re

import pandas as pd

from .channel_profiles import load_channel_profiles
from .field_mapping import load_field_mapping


CHANNEL_CLEAN_DIR = "channel_clean"
CHANNEL_CLEAN_SHEET = "清理后明细"
UNIFIED_CHANNEL_CLEAN_WORKBOOK = "cleaned_channels.xlsx"
UNIFIED_CHANNEL_SYSTEM_SHEETS = ["导入日志", "重复内容", "冲突项", "补齐来源", "审核记录"]
CHANNEL_CLEAN_COLUMNS = [
    "周期",
    "渠道",
    "账号",
    "内容形式",
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
UNIFIED_CHANNEL_COLUMNS = [
    "周期",
    "平台",
    "渠道",
    "账号",
    "内容形式",
    "内容类型",
    "标题",
    "内容ID",
    "素材ID",
    "唯一标识",
    "内容链接",
    "发布时间",
    "消耗",
    "曝光量",
    "点击量",
    "激活数",
    "付费数",
    "激活成本",
    "付费成本",
    "补齐来源",
    "补齐置信度",
    "复核状态",
    "复核原因",
]


def write_unified_channel_clean_workbook(
    canonical: pd.DataFrame,
    output_dir: Path,
    *,
    period_label: str = "",
    period_start: str = "",
    period_end: str = "",
    batch_id: str = "",
    import_log: pd.DataFrame | None = None,
    duplicate_content: pd.DataFrame | None = None,
    conflicts: pd.DataFrame | None = None,
    fill_sources: pd.DataFrame | None = None,
    review_records: pd.DataFrame | None = None,
) -> Path:
    """Write one workbook with one business sheet per channel plus system sheets."""
    workbook_path = Path(output_dir) / UNIFIED_CHANNEL_CLEAN_WORKBOOK
    workbook_path.parent.mkdir(parents=True, exist_ok=True)
    used_sheet_names: set[str] = set()

    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        for channel in _ordered_channels(canonical):
            channel_frame = canonical[canonical["channel"].map(_clean_text).eq(channel)].copy()
            sheet_name = _unique_sheet_name(channel, used_sheet_names)
            _unified_channel_frame(
                channel_frame,
                period_label=period_label,
                period_start=period_start,
                period_end=period_end,
            ).to_excel(writer, sheet_name=sheet_name, index=False)

        _system_frame(import_log, ["source_file", "sheet_name", "status", "rows", "message"]).to_excel(
            writer,
            sheet_name="导入日志",
            index=False,
        )
        _system_frame(duplicate_content, ["dedupe_key", "content_id", "title", "rows", "note"]).to_excel(
            writer,
            sheet_name="重复内容",
            index=False,
        )
        _system_frame(conflicts, ["issue_type", "channel", "content_id", "title", "reason"]).to_excel(
            writer,
            sheet_name="冲突项",
            index=False,
        )
        _system_frame(
            fill_sources,
            [
                "batch_id",
                "channel",
                "content_id",
                "material_id",
                "title",
                "field_name",
                "old_value",
                "new_value",
                "source",
                "confidence",
                "status",
                "reason",
            ],
        ).to_excel(writer, sheet_name="补齐来源", index=False)
        _system_frame(
            review_records,
            ["batch_id", "channel", "content_id", "material_id", "title", "review_status", "review_reasons", "review_action"],
        ).to_excel(writer, sheet_name="审核记录", index=False)
    return workbook_path


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
    result["内容形式"] = _text_series(frame, "content_form")
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


def _unified_channel_frame(
    frame: pd.DataFrame,
    *,
    period_label: str,
    period_start: str,
    period_end: str,
) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    result["周期"] = _period_value(frame, period_label, period_start, period_end)
    result["平台"] = _first_text_series(frame, ["platform", "platform_group"])
    result["渠道"] = _text_series(frame, "channel")
    result["账号"] = _text_series(frame, "account")
    result["内容形式"] = _text_series(frame, "content_form")
    result["内容类型"] = _first_text_series(
        frame,
        ["content_category", "manual_category", "ledger_content_type", "category_l2", "category_display"],
    )
    result["标题"] = _text_series(frame, "title")
    result["内容ID"] = _text_series(frame, "content_id")
    result["素材ID"] = _text_series(frame, "material_id")
    result["唯一标识"] = frame.apply(_unified_identifier, axis=1)
    result["内容链接"] = _text_series(frame, "content_url")
    result["发布时间"] = _text_series(frame, "source_time")
    result["消耗"] = _numeric_series(frame, "spend")
    result["曝光量"] = _numeric_series(frame, "impressions")
    result["点击量"] = _numeric_series(frame, "clicks")
    result["激活数"] = _numeric_series(frame, "activations")
    result["付费数"] = _numeric_series(frame, "first_pay_count")
    result["激活成本"] = _numeric_series(frame, "activation_cost")
    result["付费成本"] = _numeric_series(frame, "first_pay_cost")
    result["补齐来源"] = _first_text_series(frame, ["metadata_source", "ledger_match_source", "manual_category_source"])
    result["补齐置信度"] = _first_text_series(frame, ["metadata_confidence", "category_confidence"])
    result["复核状态"] = _first_text_series(frame, ["review_status", "review_action", "category_status"])
    result["复核原因"] = _review_reason_series(frame)

    raw_extras = _raw_extra_frame(frame)
    result = result[UNIFIED_CHANNEL_COLUMNS].reset_index(drop=True)
    if raw_extras.empty:
        return result
    return pd.concat([result, raw_extras.reset_index(drop=True)], axis=1)


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


def _unified_identifier(row: pd.Series) -> str:
    for column in ["content_id", "material_id", "content_url", "title"]:
        value = _clean_text(row.get(column, ""))
        if value:
            return _strip_tags(value) if column == "title" else value
    return ""


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


def _ordered_channels(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "channel" not in frame.columns:
        return []
    channels: list[str] = []
    for value in frame["channel"].tolist():
        channel = _clean_text(value) or "未识别渠道"
        if channel not in channels:
            channels.append(channel)
    return channels


def _unique_sheet_name(name: str, used_names: set[str]) -> str:
    base = re.sub(r"[:\\/?*\[\]]+", "_", _clean_text(name)).strip() or "未识别渠道"
    base = base[:31]
    candidate = base
    counter = 2
    while candidate in used_names:
        suffix = f"_{counter}"
        candidate = f"{base[:31 - len(suffix)]}{suffix}"
        counter += 1
    used_names.add(candidate)
    return candidate


def _system_frame(frame: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    prepared = frame.copy()
    for column in columns:
        if column not in prepared.columns:
            prepared[column] = ""
    return prepared


def _raw_extra_frame(frame: pd.DataFrame) -> pd.DataFrame:
    raw_columns = [column for column in frame.columns if str(column).startswith("raw__")]
    if not raw_columns:
        return pd.DataFrame(index=frame.index)
    result = pd.DataFrame(index=frame.index)
    grouped_columns: dict[str, list[str]] = {}
    for column in raw_columns:
        if _is_mapped_raw_column(str(column)):
            continue
        display_name = _raw_display_name(str(column))
        if _is_ignored_raw_display_name(display_name):
            continue
        values = frame[column]
        if values.map(_clean_text).eq("").all():
            continue
        grouped_columns.setdefault(display_name, []).append(column)
    for display_name, columns in grouped_columns.items():
        result[display_name] = _first_nonblank_raw_series(frame, columns)
    return result


def _raw_display_name(column: str) -> str:
    parts = column.split("__")
    return (parts[-1] if len(parts) >= 2 else column).strip()


def _is_ignored_raw_display_name(display_name: str) -> bool:
    text = _clean_text(display_name)
    if not text or text.lower().startswith("unnamed:"):
        return True
    return _is_config_ignored_raw_field(text)


def _is_config_ignored_raw_field(raw_name: str) -> bool:
    for ignored in load_field_mapping().ignored_fields:
        if raw_name == ignored or raw_name.startswith(ignored):
            return True
    return False


def _first_nonblank_raw_series(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series([""] * len(frame), index=frame.index, dtype=object)
    for column in columns:
        values = frame[column]
        mask = result.map(_clean_text).eq("") & values.map(_clean_text).ne("")
        result.loc[mask] = values.loc[mask]
    return result


def _is_mapped_raw_column(column: str) -> bool:
    raw_name = _raw_display_name(column)
    mapping = load_field_mapping()
    if raw_name in _preserved_raw_source_columns(mapping):
        return False
    if raw_name in mapping.mapped_source_columns:
        return True
    for profile in load_channel_profiles().profiles:
        for aliases in profile.field_aliases.values():
            if raw_name in aliases:
                return True
    return False


def _preserved_raw_source_columns(mapping) -> set[str]:
    return {
        source_column
        for field in mapping.fields
        if field.role == "derived_metric_raw"
        for source_column in field.source_columns
    }


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
