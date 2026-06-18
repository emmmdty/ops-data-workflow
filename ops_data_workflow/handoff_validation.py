"""Handoff validation helpers for the controlled local periods."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from .analysis_jobs import list_analysis_jobs
from .controlled_backfill import CONTROLLED_BACKFILL_BATCH_IDS
from .pipeline import build_high_spend_content_pool
from .storage import (
    list_content_performance_items,
    list_harvester_asset_manifests,
)


@dataclass(frozen=True)
class HandoffPeriodStatus:
    batch_id: str
    performance_count: int
    asset_match_count: int
    top_pool_count: int
    harvester_manifest_count: int
    harvester_succeeded_count: int
    harvester_failed_count: int
    multimodal_succeeded_count: int
    multimodal_failed_count: int
    cache_path_count: int
    failure_reason: str

    @property
    def ok(self) -> bool:
        return (
            self.performance_count > 0
            and self.asset_match_count > 0
            and self.top_pool_count > 0
            and self.harvester_succeeded_count > 0
            and self.multimodal_succeeded_count > 0
            and not self.failure_reason
        )


def build_handoff_validation_report(
    db_path: Path,
    *,
    batch_ids: Iterable[str] = CONTROLLED_BACKFILL_BATCH_IDS,
) -> list[HandoffPeriodStatus]:
    db_path = Path(db_path)
    rows = _read_asset_match_counts(db_path, batch_ids)
    statuses: list[HandoffPeriodStatus] = []
    for batch_id in batch_ids:
        performance = list_content_performance_items(db_path, batch_id=batch_id)
        top_pool = build_high_spend_content_pool(performance)
        manifests = list_harvester_asset_manifests(db_path, batch_id=batch_id)
        analysis_jobs = list_analysis_jobs(db_path, batch_id=batch_id)
        failure_reason = _failure_reason(performance, top_pool, manifests, analysis_jobs)
        statuses.append(
            HandoffPeriodStatus(
                batch_id=batch_id,
                performance_count=int(len(performance)),
                asset_match_count=int(rows.get(batch_id, 0)),
                top_pool_count=int(len(top_pool)),
                harvester_manifest_count=int(len(manifests)),
                harvester_succeeded_count=_status_count(manifests, "succeeded"),
                harvester_failed_count=_status_count(manifests, "failed"),
                multimodal_succeeded_count=_status_count(analysis_jobs, "succeeded"),
                multimodal_failed_count=_status_count(analysis_jobs, "failed"),
                cache_path_count=_cache_path_count(manifests),
                failure_reason=failure_reason,
            )
        )
    return statuses


def handoff_report_to_frame(statuses: Iterable[HandoffPeriodStatus]) -> pd.DataFrame:
    return pd.DataFrame([status.__dict__ | {"ok": status.ok} for status in statuses])


def format_handoff_report(statuses: Iterable[HandoffPeriodStatus]) -> str:
    frame = handoff_report_to_frame(statuses)
    if frame.empty:
        return "没有可校验周期。"
    return frame.to_string(index=False)


def _read_asset_match_counts(db_path: Path, batch_ids: Iterable[str]) -> dict[str, int]:
    import sqlite3
    from contextlib import closing

    batch_ids = list(batch_ids)
    if not db_path.exists() or not batch_ids:
        return {}
    placeholders = ",".join("?" for _ in batch_ids)
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            rows = conn.execute(
                f"select batch_id, count(*) from asset_match_results where batch_id in ({placeholders}) group by batch_id",
                batch_ids,
            ).fetchall()
        except sqlite3.Error:
            return {}
    return {str(batch_id): int(count or 0) for batch_id, count in rows}


def _status_count(frame: pd.DataFrame, status: str) -> int:
    if frame.empty or "status" not in frame.columns:
        return 0
    return int(frame["status"].fillna("").astype(str).eq(status).sum())


def _cache_path_count(manifests: pd.DataFrame) -> int:
    if manifests.empty or "asset_dir" not in manifests.columns:
        return 0
    return int(manifests["asset_dir"].fillna("").astype(str).str.contains(".runtime/top-assets", regex=False).sum())


def _failure_reason(
    performance: pd.DataFrame,
    top_pool: pd.DataFrame,
    manifests: pd.DataFrame,
    analysis_jobs: pd.DataFrame,
) -> str:
    if performance.empty:
        return "content_performance_items 为空"
    if top_pool.empty:
        return "TopN 高价值池为空"
    if manifests.empty:
        return "harvester manifest 为空"
    if _status_count(manifests, "succeeded") == 0:
        return _first_non_blank(manifests, "error_message") or "harvester 未成功采集/复用素材"
    if _cache_path_count(manifests) == 0:
        return "harvester 素材未写入本项目 .runtime/top-assets 缓存"
    if _status_count(analysis_jobs, "succeeded") == 0:
        return _first_non_blank(analysis_jobs, "error_message") or "多模态分析尚未成功"
    return ""


def _first_non_blank(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    for value in frame[column]:
        text = str(value or "").strip()
        if text:
            return text
    return ""
