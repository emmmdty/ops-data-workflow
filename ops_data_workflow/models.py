"""Workflow result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class WorkflowResult:
    batch_id: str
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
    reference_tables: dict[str, pd.DataFrame]
    channel_comparison: pd.DataFrame
    comparison_note: str
    ai_summary: str
    report_html: Optional[Path]
    analysis_xlsx: Optional[Path]
    canonical_csv: Optional[Path]
    total_summary_xlsx: Optional[Path]
    archive_dir: Path
    channel_clean_workbooks: list[Path] = field(default_factory=list)
    account_filter_rules: pd.DataFrame = field(default_factory=pd.DataFrame)
    account_filter_details: pd.DataFrame = field(default_factory=pd.DataFrame)
