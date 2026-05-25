"""End-to-end workflow orchestration."""

from __future__ import annotations

from pathlib import Path
from datetime import date
import json
from typing import Callable, Optional
from uuid import uuid4

import pandas as pd

from .ai import generate_ai_summary
from .comparison import build_channel_comparison
from .models import WorkflowResult
from .pipeline import analyze_canonical_frame, analyze_input_dir
from .periods import SOURCE_TYPE_ROLLUP, SOURCE_TYPE_UPLOAD, ReviewPeriod, period_metadata_from_dates
from .raw_cleaning import cleaned_workbook_in_dir, load_cleaned_canonical
from .reference_tables import parse_period_from_raw_dir
from .reporting import write_outputs
from .storage import (
    archive_input_files,
    load_category_mappings,
    persist_workflow_result,
    previous_successful_batch_id_for_period,
    read_total_summary,
)
from .topic_analysis import build_topic_label_frame


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
    period_level: str = "",
    period_key: str = "",
    period_label: str = "",
    data_start: str = "",
    data_end: str = "",
    source_type: str = SOURCE_TYPE_UPLOAD,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> WorkflowResult:
    def progress(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    if not period_start or not period_end:
        period_start, period_end = parse_period_from_raw_dir(input_dir)
    period = period_metadata_from_dates(
        period_start,
        period_end,
        period_level,
        period_key,
        period_label,
        data_start,
        data_end,
        source_type,
    )
    period_start = period.period_start
    period_end = period.period_end
    batch_id = _new_batch_id()
    archive_dir = Path(archive_root) / f"{period_start}_{batch_id}"
    progress("正在归档原始文件")
    archived_files = archive_input_files(input_dir, archive_dir, uploaded_zip_path)
    archived_raw_dir = archive_dir / "raw"

    previous_batch_id = previous_successful_batch_id_for_period(
        db_path,
        period.period_start,
        period.period_level,
        period.period_key,
    )
    previous_summary = read_total_summary(db_path, previous_batch_id) if previous_batch_id else _empty_frame()

    category_mappings = load_category_mappings(db_path)
    progress("正在读取渠道数据并标准化")
    cleaned_workbook = cleaned_workbook_in_dir(archived_raw_dir)
    if cleaned_workbook is not None:
        analysis = analyze_canonical_frame(
            load_cleaned_canonical(cleaned_workbook),
            period_start,
            period_end,
            category_rules_path,
            env_path=env_path,
            category_matcher=category_matcher,
            category_mappings=category_mappings,
        )
    else:
        analysis = analyze_input_dir(
            archived_raw_dir,
            period_start,
            period_end,
            category_rules_path,
            env_path=env_path,
            category_matcher=category_matcher,
            category_mappings=category_mappings,
        )
    progress("正在校验数据质量与题材分类")
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
    progress("正在固化重点题材")
    topic_label_items = build_topic_label_frame(analysis.canonical, env_path=env_path)

    output_dir = Path(output_root) / batch_id
    progress("正在写入历史库并生成下载文件")
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
        topic_label_items,
        ai_summary,
        previous_batch_id,
        comparison_note,
        period_level=period.period_level,
        period_key=period.period_key,
        period_label=period.period_label,
        data_start=period.data_start,
        data_end=period.data_end,
        source_type=period.source_type,
    )
    progress("报告生成完成")
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


def run_rollup_workflow(
    db_path: Path,
    component_batch_ids: list[str],
    period: ReviewPeriod,
    output_root: Path = Path("outputs"),
    archive_root: Path = Path("archive"),
    category_rules_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    category_matcher=None,
) -> WorkflowResult:
    from .dashboard import load_dashboard_items_for_batch

    if not component_batch_ids:
        raise ValueError("没有可用于生成汇总复盘的已入库周期。")
    frames = [load_dashboard_items_for_batch(db_path, batch_id) for batch_id in component_batch_ids]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise ValueError("所选周期没有可汇总的 canonical 数据。")
    canonical = pd.concat(frames, ignore_index=True)
    canonical = canonical.drop(columns=[column for column in canonical.columns if column.startswith("batch_")], errors="ignore")

    batch_id = _new_batch_id()
    archive_dir = Path(archive_root) / f"{period.period_start}_{batch_id}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "rollup_manifest.json").write_text(
        json.dumps(
            {
                "period_level": period.period_level,
                "period_key": period.period_key,
                "component_batch_ids": component_batch_ids,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    previous_batch_id = previous_successful_batch_id_for_period(
        db_path,
        period.period_start,
        period.period_level,
        period.period_key,
    )
    previous_summary = read_total_summary(db_path, previous_batch_id) if previous_batch_id else _empty_frame()
    category_mappings = load_category_mappings(db_path)
    analysis = analyze_canonical_frame(
        canonical,
        period.period_start,
        period.period_end,
        category_rules_path,
        env_path=env_path,
        category_matcher=category_matcher,
        category_mappings=category_mappings,
    )
    if previous_summary.empty:
        channel_comparison = _empty_frame()
        comparison_note = "无历史对比数据：数据库中没有同层级上一周期的成功批次。"
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
    topic_label_items = build_topic_label_frame(analysis.canonical, env_path=env_path)
    output_dir = Path(output_root) / batch_id
    report_html, analysis_xlsx, canonical_csv, total_summary_xlsx = write_outputs(
        output_dir,
        period.period_start,
        period.period_end,
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
        period.period_start,
        period.period_end,
        archive_dir,
        output_dir,
        [],
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
        topic_label_items,
        ai_summary,
        previous_batch_id,
        comparison_note,
        period_level=period.period_level,
        period_key=period.period_key,
        period_label=period.period_label,
        data_start=period.data_start,
        data_end=period.data_end,
        source_type=SOURCE_TYPE_ROLLUP,
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
