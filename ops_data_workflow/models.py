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
    preprocessing_report: pd.DataFrame
    duplicate_merge_details: pd.DataFrame
    conflict_retention_details: pd.DataFrame
    missing_value_details: pd.DataFrame
    reference_tables: dict[str, pd.DataFrame]
    channel_comparison: pd.DataFrame
    comparison_note: str
    ai_summary: str
    archive_dir: Path
    core_recap_xlsx: Optional[Path] = None
    cleaned_asset_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    content_recap_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    unanalyzable_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    attribution_coverage: pd.DataFrame = field(default_factory=pd.DataFrame)
    matched_attribution: pd.DataFrame = field(default_factory=pd.DataFrame)
    unmatched_attribution: pd.DataFrame = field(default_factory=pd.DataFrame)
    account_filter_rules: pd.DataFrame = field(default_factory=pd.DataFrame)
    account_filter_details: pd.DataFrame = field(default_factory=pd.DataFrame)
    feishu_staleness: dict[str, object] = field(default_factory=dict)
