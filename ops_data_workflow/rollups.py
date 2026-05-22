"""Build quarter/year rollup periods from existing normalized batches."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from pathlib import Path

import pandas as pd

from .periods import PERIOD_LEVEL_MONTH, PERIOD_LEVEL_QUARTER, PERIOD_LEVEL_WEEK, PERIOD_LEVEL_YEAR, ReviewPeriod, review_period_from_dates


def rollup_period_for(period_level: str, year: int, quarter: int | None = None) -> ReviewPeriod:
    if period_level == PERIOD_LEVEL_YEAR:
        return review_period_from_dates(date(year, 1, 1), date(year, 12, 31), PERIOD_LEVEL_YEAR)
    if period_level != PERIOD_LEVEL_QUARTER:
        raise ValueError("汇总复盘仅支持季度或年度。")
    if quarter not in {1, 2, 3, 4}:
        raise ValueError("季度必须为 1-4。")
    start_month = (int(quarter) - 1) * 3 + 1
    end_month = start_month + 2
    return review_period_from_dates(
        date(year, start_month, 1),
        date(year, end_month, monthrange(year, end_month)[1]),
        PERIOD_LEVEL_QUARTER,
    )


def select_rollup_component_batches(db_path: Path, period: ReviewPeriod) -> list[str]:
    from .dashboard import list_successful_dashboard_batches

    batches = list_successful_dashboard_batches(db_path)
    if batches.empty:
        return []
    candidates = batches[batches["source_type"].astype(str).ne("rollup")].copy()
    if candidates.empty:
        return []
    candidates["_start_dt"] = pd.to_datetime(candidates["period_start"], errors="coerce")
    candidates["_end_dt"] = pd.to_datetime(candidates["period_end"], errors="coerce")
    start = pd.Timestamp(period.period_start)
    end = pd.Timestamp(period.period_end)
    candidates = candidates[
        candidates["_start_dt"].notna()
        & candidates["_end_dt"].notna()
        & candidates["_start_dt"].ge(start)
        & candidates["_end_dt"].le(end)
    ].copy()
    if candidates.empty:
        return []

    selected: list[str] = []
    for month_start in _month_starts(start.date(), end.date()):
        month_end = date(month_start.year, month_start.month, monthrange(month_start.year, month_start.month)[1])
        month_rows = candidates[
            candidates["period_level"].eq(PERIOD_LEVEL_MONTH)
            & candidates["_start_dt"].dt.year.eq(month_start.year)
            & candidates["_start_dt"].dt.month.eq(month_start.month)
        ].sort_values("created_at", ascending=False)
        if not month_rows.empty:
            selected.append(str(month_rows.iloc[0]["batch_id"]))
            continue
        week_rows = candidates[
            candidates["period_level"].eq(PERIOD_LEVEL_WEEK)
            & candidates["_start_dt"].dt.year.eq(month_start.year)
            & candidates["_start_dt"].dt.month.eq(month_start.month)
        ].sort_values(["_start_dt", "created_at"], ascending=[True, False])
        selected.extend(str(batch_id) for batch_id in week_rows["batch_id"].tolist())
    return selected


def _month_starts(start: date, end: date) -> list[date]:
    months: list[date] = []
    current = date(start.year, start.month, 1)
    while current <= end:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months
