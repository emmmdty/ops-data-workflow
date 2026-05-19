"""End-to-end workflow orchestration."""

from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Optional
from uuid import uuid4

from .ai import generate_ai_summary
from .comparison import build_channel_comparison
from .models import WorkflowResult
from .pipeline import analyze_input_dir
from .reference_tables import parse_period_from_raw_dir
from .reporting import write_outputs
from .storage import (
    archive_input_files,
    load_category_mappings,
    persist_workflow_result,
    previous_successful_batch_id,
    read_total_summary,
)


def run_workflow(
    input_dir: Path,
    period_start: str,
    period_end: str,
    output_dir: Path,
    category_rules_path: Optional[Path] = None,
) -> WorkflowResult:
    if not period_start or not period_end:
        period_start, period_end = parse_period_from_raw_dir(input_dir)
    analysis = analyze_input_dir(
        Path(input_dir),
        period_start,
        period_end,
        category_rules_path,
        category_matcher=lambda items, category_library, env_path: {},
    )
    report_html, analysis_xlsx, canonical_csv, total_summary_xlsx = write_outputs(
        Path(output_dir),
        period_start,
        period_end,
        analysis.canonical,
        analysis.category_summary,
        analysis.channel_summary,
        analysis.platform_summary,
        analysis.platform_category_summary,
        analysis.total_summary,
        analysis.raw_category_stats,
        analysis.pending_categories,
        analysis.account_audit,
        analysis.top_content_items,
        analysis.cover_metrics,
        analysis.data_quality,
        analysis.review_queue,
        analysis.preprocessing_report,
        analysis.duplicate_merge_details,
        analysis.conflict_retention_details,
        analysis.missing_value_details,
        analysis.reference_tables.tables,
    )
    return WorkflowResult(
        batch_id="",
        canonical=analysis.canonical,
        category_summary=analysis.category_summary,
        channel_summary=analysis.channel_summary,
        platform_summary=analysis.platform_summary,
        platform_category_summary=analysis.platform_category_summary,
        total_summary=analysis.total_summary,
        raw_category_stats=analysis.raw_category_stats,
        pending_categories=analysis.pending_categories,
        account_audit=analysis.account_audit,
        top_content_items=analysis.top_content_items,
        cover_metrics=analysis.cover_metrics,
        data_quality=analysis.data_quality,
        review_queue=analysis.review_queue,
        preprocessing_report=analysis.preprocessing_report,
        duplicate_merge_details=analysis.duplicate_merge_details,
        conflict_retention_details=analysis.conflict_retention_details,
        missing_value_details=analysis.missing_value_details,
        reference_tables=dict(analysis.reference_tables.tables),
        channel_comparison=_empty_frame(),
        comparison_note="未启用数据库历史对比。",
        ai_summary="",
        report_html=report_html,
        analysis_xlsx=analysis_xlsx,
        canonical_csv=canonical_csv,
        total_summary_xlsx=total_summary_xlsx,
        archive_dir=Path(""),
    )


def run_archived_workflow(
    input_dir: Path,
    period_start: str,
    period_end: str,
    output_root: Path = Path("outputs"),
    archive_root: Path = Path("archive"),
    db_path: Path = Path("data/workflow.sqlite3"),
    category_rules_path: Optional[Path] = None,
    uploaded_zip_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    category_matcher=None,
) -> WorkflowResult:
    if not period_start or not period_end:
        period_start, period_end = parse_period_from_raw_dir(input_dir)
    batch_id = _new_batch_id()
    archive_dir = Path(archive_root) / f"{period_start}_{batch_id}"
    archived_files = archive_input_files(input_dir, archive_dir, uploaded_zip_path)
    archived_raw_dir = archive_dir / "raw"

    previous_batch_id = previous_successful_batch_id(db_path, period_start)
    previous_summary = read_total_summary(db_path, previous_batch_id) if previous_batch_id else _empty_frame()

    category_mappings = load_category_mappings(db_path)
    analysis = analyze_input_dir(
        archived_raw_dir,
        period_start,
        period_end,
        category_rules_path,
        env_path=env_path,
        category_matcher=category_matcher,
        category_mappings=category_mappings,
    )
    if previous_summary.empty:
        channel_comparison = _empty_frame()
        comparison_note = "无历史对比数据：数据库中没有早于当前周期的成功批次。"
    else:
        channel_comparison = build_channel_comparison(analysis.total_summary, previous_summary)
        comparison_note = ""

    ai_summary = generate_ai_summary(
        analysis.total_summary,
        analysis.category_summary,
        analysis.top_content_items,
        analysis.account_audit,
        channel_comparison,
        comparison_note,
        env_path=env_path,
        platform_summary=analysis.platform_summary,
        platform_category_summary=analysis.platform_category_summary,
    )

    output_dir = Path(output_root) / batch_id
    report_html, analysis_xlsx, canonical_csv, total_summary_xlsx = write_outputs(
        output_dir,
        period_start,
        period_end,
        analysis.canonical,
        analysis.category_summary,
        analysis.channel_summary,
        analysis.platform_summary,
        analysis.platform_category_summary,
        analysis.total_summary,
        analysis.raw_category_stats,
        analysis.pending_categories,
        analysis.account_audit,
        analysis.top_content_items,
        analysis.cover_metrics,
        analysis.data_quality,
        analysis.review_queue,
        analysis.preprocessing_report,
        analysis.duplicate_merge_details,
        analysis.conflict_retention_details,
        analysis.missing_value_details,
        analysis.reference_tables.tables,
        channel_comparison,
        comparison_note,
        ai_summary,
    )
    persist_workflow_result(
        db_path,
        batch_id,
        period_start,
        period_end,
        archive_dir,
        output_dir,
        archived_files,
        analysis.canonical,
        analysis.channel_summary,
        analysis.total_summary,
        analysis.platform_summary,
        analysis.platform_category_summary,
        analysis.category_summary,
        analysis.top_content_items,
        analysis.account_audit,
        analysis.cover_metrics,
        analysis.data_quality,
        analysis.review_queue,
        analysis.preprocessing_report,
        analysis.duplicate_merge_details,
        analysis.conflict_retention_details,
        analysis.missing_value_details,
        channel_comparison,
        ai_summary,
        previous_batch_id,
        comparison_note,
    )
    return WorkflowResult(
        batch_id=batch_id,
        canonical=analysis.canonical,
        category_summary=analysis.category_summary,
        channel_summary=analysis.channel_summary,
        platform_summary=analysis.platform_summary,
        platform_category_summary=analysis.platform_category_summary,
        total_summary=analysis.total_summary,
        raw_category_stats=analysis.raw_category_stats,
        pending_categories=analysis.pending_categories,
        account_audit=analysis.account_audit,
        top_content_items=analysis.top_content_items,
        cover_metrics=analysis.cover_metrics,
        data_quality=analysis.data_quality,
        review_queue=analysis.review_queue,
        preprocessing_report=analysis.preprocessing_report,
        duplicate_merge_details=analysis.duplicate_merge_details,
        conflict_retention_details=analysis.conflict_retention_details,
        missing_value_details=analysis.missing_value_details,
        reference_tables=dict(analysis.reference_tables.tables),
        channel_comparison=channel_comparison,
        comparison_note=comparison_note,
        ai_summary=ai_summary,
        report_html=report_html,
        analysis_xlsx=analysis_xlsx,
        canonical_csv=canonical_csv,
        total_summary_xlsx=total_summary_xlsx,
        archive_dir=archive_dir,
    )


def _new_batch_id() -> str:
    return f"{date.today().isoformat()}-{uuid4().hex[:8]}"


def _empty_frame():
    import pandas as pd

    return pd.DataFrame()
