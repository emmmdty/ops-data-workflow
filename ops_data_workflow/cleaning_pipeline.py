"""Cleaning pipeline helpers with explicit channel-total separation."""

from __future__ import annotations

import hashlib
import re

import pandas as pd


METRIC_COLUMNS = ["spend", "impressions", "clicks", "activations", "first_pay_count"]
IDENTITY_COLUMNS = ["content_id", "material_id", "work_id", "content_url", "work_url", "title"]
PERIOD_CHANNEL_TOTAL_COLUMNS = [
    "period_total_key",
    "period_start",
    "period_end",
    "channel",
    "platform",
    "source_file",
    "source_sheet",
    "source_row",
    "spend",
    "impressions",
    "clicks",
    "activations",
    "first_pay_count",
    "activation_cost",
    "first_pay_cost",
    "is_channel_total",
]


def split_channel_total_rows(canonical: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return material detail rows and single-row channel totals separately."""
    if canonical is None or canonical.empty:
        return pd.DataFrame(), pd.DataFrame(columns=PERIOD_CHANNEL_TOTAL_COLUMNS)
    frame = canonical.copy()
    for column in ["source_file", "source_sheet", "period_start", "period_end", "channel", "platform", *IDENTITY_COLUMNS]:
        if column not in frame.columns:
            frame[column] = ""
    for column in [*METRIC_COLUMNS, "source_row"]:
        if column not in frame.columns:
            frame[column] = 0
    source_group_columns = ["period_start", "period_end", "channel", "source_file", "source_sheet"]
    total_mask = pd.Series(False, index=frame.index)
    for _, group in frame.groupby(source_group_columns, dropna=False, sort=False):
        if len(group) != 1:
            continue
        row = group.iloc[0]
        if not _has_metric(row):
            continue
        if _has_identity(row):
            continue
        total_mask.loc[group.index] = True
    totals = _period_channel_totals_frame(frame[total_mask])
    detail = frame[~total_mask].copy().reset_index(drop=True)
    return detail, totals


def _period_channel_totals_frame(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=PERIOD_CHANNEL_TOTAL_COLUMNS)
    records: list[dict[str, object]] = []
    for _, row in rows.iterrows():
        spend = _number(row.get("spend"))
        activations = _number(row.get("activations"))
        first_pay = _number(row.get("first_pay_count"))
        record = {
            "period_total_key": _period_total_key(row),
            "period_start": _text(row.get("period_start")),
            "period_end": _text(row.get("period_end")),
            "channel": _text(row.get("channel")),
            "platform": _text(row.get("platform")),
            "source_file": _text(row.get("source_file")),
            "source_sheet": _text(row.get("source_sheet")),
            "source_row": int(_number(row.get("source_row"))),
            "spend": spend,
            "impressions": _number(row.get("impressions")),
            "clicks": _number(row.get("clicks")),
            "activations": activations,
            "first_pay_count": first_pay,
            "activation_cost": spend / activations if activations else 0.0,
            "first_pay_cost": spend / first_pay if first_pay else 0.0,
            "is_channel_total": True,
        }
        records.append(record)
    return pd.DataFrame(records, columns=PERIOD_CHANNEL_TOTAL_COLUMNS)


def _period_total_key(row: pd.Series) -> str:
    raw = "|".join(
        _text(row.get(column))
        for column in ["period_start", "period_end", "channel", "source_file", "source_sheet", "source_row"]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _has_identity(row: pd.Series) -> bool:
    source_file_stem = _source_file_stem(row.get("source_file"))
    for column in IDENTITY_COLUMNS:
        value = _text(row.get(column))
        if not value:
            continue
        if _is_synthetic_row_identity(value):
            continue
        if column == "title" and _is_synthetic_source_row_title(value, source_file_stem):
            continue
        return True
    return False


def _has_metric(row: pd.Series) -> bool:
    return any(abs(_number(row.get(column))) > 1e-9 for column in METRIC_COLUMNS)


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


def _is_synthetic_row_identity(value: str) -> bool:
    return bool(re.fullmatch(r"row:[0-9a-f]{8,40}", _text(value), flags=re.I))


def _is_synthetic_source_row_title(value: str, source_file_stem: str) -> bool:
    text = _text(value)
    if not text or not source_file_stem:
        return False
    return bool(re.fullmatch(rf"{re.escape(source_file_stem)}\s+第\d+行", text))


def _source_file_stem(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    return re.sub(r"\.[A-Za-z0-9]+$", "", text)
