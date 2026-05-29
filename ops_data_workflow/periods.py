"""Review-period inference and normalization helpers."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re


PERIOD_LEVEL_WEEK = "week"
PERIOD_LEVEL_MONTH = "month"
PERIOD_LEVEL_QUARTER = "quarter"
PERIOD_LEVEL_YEAR = "year"
PERIOD_LEVELS = [PERIOD_LEVEL_WEEK, PERIOD_LEVEL_MONTH, PERIOD_LEVEL_QUARTER, PERIOD_LEVEL_YEAR]
PERIOD_LEVEL_LABELS = {
    PERIOD_LEVEL_WEEK: "周",
    PERIOD_LEVEL_MONTH: "月",
    PERIOD_LEVEL_QUARTER: "季度",
    PERIOD_LEVEL_YEAR: "年度",
}
SOURCE_TYPE_UPLOAD = "upload"
SOURCE_TYPE_ROLLUP = "rollup"


@dataclass(frozen=True)
class ReviewPeriod:
    period_level: str
    period_key: str
    period_label: str
    period_start: str
    period_end: str
    data_start: str
    data_end: str
    source_type: str = SOURCE_TYPE_UPLOAD


def infer_review_period_from_text(text: str, default_year: int) -> ReviewPeriod | None:
    """Infer a review period from upload paths or raw directory names."""
    normalized = _normalize_text(text)
    if not normalized:
        return None

    ranges = _extract_date_ranges(normalized, default_year)
    month_context = _extract_month_context(normalized, default_year)
    quarter_context = _extract_quarter_context(normalized, default_year)
    year_context = _extract_year_context(normalized, default_year)

    if year_context and _has_year_signal(normalized):
        start = date(year_context, 1, 1)
        end = date(year_context, 12, 31)
        data_start, data_end = ranges[0] if ranges else (start, end)
        return review_period_from_dates(
            data_start,
            data_end,
            PERIOD_LEVEL_YEAR,
            logic_start=start,
            logic_end=end,
        )

    if quarter_context is not None:
        year, quarter = quarter_context
        start_month = (quarter - 1) * 3 + 1
        start = date(year, start_month, 1)
        end_month = start_month + 2
        end = date(year, end_month, monthrange(year, end_month)[1])
        data_start, data_end = ranges[0] if ranges else (start, end)
        return review_period_from_dates(
            data_start,
            data_end,
            PERIOD_LEVEL_QUARTER,
            logic_start=start,
            logic_end=end,
        )

    explicit_range = ranges[0] if ranges else None
    if _has_monthly_signal(normalized):
        if month_context:
            year, month = month_context
            logic_start = date(year, month, 1)
            logic_end = date(year, month, monthrange(year, month)[1])
            data_start, data_end = explicit_range if explicit_range else (logic_start, logic_end)
        elif explicit_range:
            data_start, data_end = explicit_range
            logic_start = date(data_end.year, data_end.month, 1)
            logic_end = date(data_end.year, data_end.month, monthrange(data_end.year, data_end.month)[1])
        else:
            return None
        return review_period_from_dates(
            data_start,
            data_end,
            PERIOD_LEVEL_MONTH,
            logic_start=logic_start,
            logic_end=logic_end,
        )

    if explicit_range is None:
        if month_context:
            year, month = month_context
            logic_start = date(year, month, 1)
            logic_end = date(year, month, monthrange(year, month)[1])
            return review_period_from_dates(logic_start, logic_end, PERIOD_LEVEL_MONTH)
        return None

    data_start, data_end = explicit_range
    span_days = (data_end - data_start).days + 1
    if span_days >= 21:
        return review_period_from_dates(data_start, data_end, PERIOD_LEVEL_MONTH)
    return review_period_from_dates(data_start, data_end, PERIOD_LEVEL_WEEK)


def review_period_from_dates(
    data_start: date,
    data_end: date,
    period_level: str,
    *,
    logic_start: date | None = None,
    logic_end: date | None = None,
    source_type: str = SOURCE_TYPE_UPLOAD,
) -> ReviewPeriod:
    if data_end < data_start:
        raise ValueError("数据时间开始日期不能晚于结束日期。")
    level = period_level if period_level in PERIOD_LEVELS else PERIOD_LEVEL_WEEK

    if level == PERIOD_LEVEL_MONTH:
        base = logic_start or date(data_start.year, data_start.month, 1)
        start = date(base.year, base.month, 1)
        end = logic_end or date(base.year, base.month, monthrange(base.year, base.month)[1])
        key = f"{start.year:04d}-{start.month:02d}"
        label_core = f"{start.year:04d}年{start.month:02d}月"
    elif level == PERIOD_LEVEL_QUARTER:
        base = logic_start or data_start
        quarter = (base.month - 1) // 3 + 1
        start_month = (quarter - 1) * 3 + 1
        start = date(base.year, start_month, 1)
        end_month = start_month + 2
        end = logic_end or date(base.year, end_month, monthrange(base.year, end_month)[1])
        key = f"{base.year:04d}-Q{quarter}"
        label_core = f"{base.year:04d}年第{quarter}季度"
    elif level == PERIOD_LEVEL_YEAR:
        base = logic_start or data_start
        start = date(base.year, 1, 1)
        end = logic_end or date(base.year, 12, 31)
        key = f"{base.year:04d}"
        label_core = f"{base.year:04d}年"
    else:
        start = logic_start or data_start
        end = logic_end or data_end
        key = f"{start:%Y%m%d}-{end:%Y%m%d}"
        label_core = f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d}"

    label = f"{PERIOD_LEVEL_LABELS[level]}｜{label_core}"
    if start != data_start or end != data_end:
        label = f"{label}（数据时间：{data_start:%Y-%m-%d} 至 {data_end:%Y-%m-%d}）"
    return ReviewPeriod(
        period_level=level,
        period_key=key,
        period_label=label,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        data_start=data_start.isoformat(),
        data_end=data_end.isoformat(),
        source_type=source_type,
    )


def period_raw_dir_name(period: ReviewPeriod) -> str:
    return f"{period.period_start.replace('-', '')}-{period.period_end.replace('-', '')}"


def period_result_id(period: ReviewPeriod) -> str:
    return f"{period.source_type}:{period.period_level}:{period.period_key}"


def period_metadata_from_dates(
    period_start: str,
    period_end: str,
    period_level: str = "",
    period_key: str = "",
    period_label: str = "",
    data_start: str = "",
    data_end: str = "",
    source_type: str = SOURCE_TYPE_UPLOAD,
) -> ReviewPeriod:
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    explicit_level = bool(str(period_level or "").strip())
    level = period_level or (PERIOD_LEVEL_MONTH if (end - start).days + 1 >= 21 else PERIOD_LEVEL_WEEK)
    period = review_period_from_dates(
        date.fromisoformat(data_start) if data_start else start,
        date.fromisoformat(data_end) if data_end else end,
        level,
        logic_start=start if explicit_level else None,
        logic_end=end if explicit_level else None,
        source_type=source_type or SOURCE_TYPE_UPLOAD,
    )
    return ReviewPeriod(
        period_level=period.period_level,
        period_key=period_key or period.period_key,
        period_label=period_label or period.period_label,
        period_start=period.period_start,
        period_end=period.period_end,
        data_start=period.data_start,
        data_end=period.data_end,
        source_type=period.source_type,
    )


def _normalize_text(text: str) -> str:
    return str(text or "").replace("\\", "/").strip()


def _extract_date_ranges(text: str, default_year: int) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    patterns = [
        re.compile(r"(?<!\d)(20\d{2})[-./年]?(\d{1,2})[-./月]?(\d{1,2})日?\D{0,6}(20\d{2})[-./年]?(\d{1,2})[-./月]?(\d{1,2})日?(?!\d)"),
        re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})\D{0,6}(\d{2})(\d{2})(?!\d)"),
        re.compile(r"(?<!\d)(\d{2})(\d{2})\D{0,4}(\d{2})(\d{2})(?!\d)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            try:
                if len(match.groups()) == 6:
                    start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
                elif len(match.groups()) == 5:
                    start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    end = date(start.year, int(match.group(4)), int(match.group(5)))
                    if end < start:
                        end = date(start.year + 1, end.month, end.day)
                else:
                    start = date(default_year, int(match.group(1)), int(match.group(2)))
                    end = date(default_year, int(match.group(3)), int(match.group(4)))
                    if end < start:
                        end = date(default_year + 1, end.month, end.day)
            except ValueError:
                continue
            ranges.append((start, end))
    return ranges


def _extract_month_context(text: str, default_year: int) -> tuple[int, int] | None:
    for match in re.finditer(r"(?:(20\d{2})年?)?(\d{1,2})月", text):
        month = int(match.group(2))
        if 1 <= month <= 12:
            return int(match.group(1) or default_year), month
    return None


def _extract_quarter_context(text: str, default_year: int) -> tuple[int, int] | None:
    match = re.search(r"(?:(20\d{2})年?)?[Qq]([1-4])", text)
    if match:
        return int(match.group(1) or default_year), int(match.group(2))
    match = re.search(r"(?:(20\d{2})年?)?第?([一二三四1234])季度", text)
    if not match:
        return None
    value = match.group(2)
    quarter = {"一": 1, "二": 2, "三": 3, "四": 4}.get(value, int(value) if value.isdigit() else 0)
    return (int(match.group(1) or default_year), quarter) if quarter else None


def _extract_year_context(text: str, default_year: int) -> int | None:
    match = re.search(r"(20\d{2})年", text)
    if match:
        return int(match.group(1))
    return default_year if _has_year_signal(text) else None


def _has_monthly_signal(text: str) -> bool:
    return any(token in text for token in ["总数据", "月数据", "月度", "整月"])


def _has_year_signal(text: str) -> bool:
    return any(token in text for token in ["年度", "全年", "年总"])
