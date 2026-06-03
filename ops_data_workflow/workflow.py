"""End-to-end workflow orchestration."""

from __future__ import annotations

from pathlib import Path
from datetime import date
import hashlib
import json
import shutil
from typing import Callable, Optional
from uuid import uuid4

import pandas as pd

from .ai import generate_ai_summary, generate_local_summary, resolve_deepseek_settings
from .channel_clean import write_channel_clean_workbooks
from .comparison import build_channel_comparison
from .external_context import fetch_external_context
from .models import WorkflowResult
from .pipeline import TABULAR_SUFFIXES, analyze_canonical_frame, analyze_input_dir
from .periods import SOURCE_TYPE_ROLLUP, SOURCE_TYPE_UPLOAD, ReviewPeriod, period_metadata_from_dates, period_result_id
from .raw_cleaning import clean_raw_period_dir, cleaned_workbook_in_dir, load_cleaned_canonical
from .reference_tables import parse_period_from_raw_dir
from .source_storage import source_storage_key
from .reporting import write_outputs
from .storage import (
    ArchivedFile,
    load_category_mappings,
    load_douyin_id_bridge,
    persist_workflow_result,
    persist_douyin_id_bridge,
    previous_successful_batch_id_for_period,
    read_total_summary,
)
from .topic_analysis import build_topic_label_frame
from .source_storage import discover_source_period_dirs


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
        cleaned_output_dir=Path(output_dir),
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
        account_filter_rules=analysis.account_filter_rules,
        account_filter_details=analysis.account_filter_details,
    )
    channel_clean_workbooks = write_channel_clean_workbooks(
        analysis.canonical,
        Path(output_dir),
        period_start=period_start,
        period_end=period_end,
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
        channel_clean_workbooks=channel_clean_workbooks,
        account_filter_rules=analysis.account_filter_rules,
        account_filter_details=analysis.account_filter_details,
    )


def run_archived_workflow(
    input_dir: Path,
    period_start: str,
    period_end: str,
    output_root: Path = Path("outputs"),
    processed_root: Path = Path("processed"),
    archive_root: Path | None = None,
    db_path: Path = Path(".runtime/workflow.sqlite3"),
    category_rules_path: Optional[Path] = None,
    uploaded_zip_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    reference_root: Path | None = None,
    category_matcher=None,
    period_level: str = "",
    period_key: str = "",
    period_label: str = "",
    data_start: str = "",
    data_end: str = "",
    source_type: str = SOURCE_TYPE_UPLOAD,
    progress_callback: Optional[Callable[[str], None]] = None,
    output_mode: str = "full",
    enable_deepseek: bool = True,
    enable_external_context: bool = True,
    write_channel_clean: bool = True,
    metadata_enrichment_mode: str = "off",
    metadata_cache_dir: Path | None = None,
    force_reclean: bool = False,
    allow_public_api_metadata: bool = True,
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
    batch_id = period_result_id(period)
    ui_only = output_mode == "ui_only"
    input_dir = Path(input_dir)
    processed_dir = Path(processed_root) / source_storage_key(period) / batch_id
    progress("正在整理清洗产物")
    existing_cleaned = None if force_reclean else cleaned_workbook_in_dir(input_dir)
    if existing_cleaned is not None:
        processed_dir = input_dir
        cleaned_workbook = existing_cleaned
        archived_files = _source_file_records(input_dir)
    else:
        _replace_directory(processed_dir)
        cleaned_bucket = clean_raw_period_dir(
            input_dir,
            period,
            default_year=date.fromisoformat(period.data_start).year,
            output_dir=processed_dir,
            reference_root=reference_root,
            write_channel_clean=write_channel_clean and not ui_only,
            metadata_enrichment_mode=metadata_enrichment_mode,
            metadata_cache_dir=metadata_cache_dir,
            allow_public_api_metadata=allow_public_api_metadata,
        )
        cleaned_workbook = cleaned_bucket.cleaned_workbook
        archived_files = _source_file_records(input_dir)

    previous_batch_id = previous_successful_batch_id_for_period(
        db_path,
        period.period_start,
        period.period_level,
        period.period_key,
    )
    previous_summary = read_total_summary(db_path, previous_batch_id) if previous_batch_id else _empty_frame()

    category_mappings = load_category_mappings(db_path)
    douyin_id_bridge = load_douyin_id_bridge(db_path)
    progress("正在读取渠道数据并标准化")
    if cleaned_workbook is None:
        raise FileNotFoundError("未找到 cleaned.xlsx，请先完成原始 Excel 清洗。")
    analysis = analyze_canonical_frame(
        load_cleaned_canonical(cleaned_workbook),
        period_start,
        period_end,
        category_rules_path,
        env_path=env_path,
        category_matcher=category_matcher if enable_deepseek else _no_category_matches,
        category_mappings=category_mappings,
        douyin_id_bridge=douyin_id_bridge,
    )
    progress("正在校验字段完整性与内容类型")
    if previous_summary.empty:
        channel_comparison = _empty_frame()
        comparison_note = "无历史对比数据：数据库中没有早于当前周期的成功周期。"
    else:
        channel_comparison = build_channel_comparison(analysis.total_summary, previous_summary)
        comparison_note = ""

    settings = resolve_deepseek_settings(env_path)
    external_context = (
        fetch_external_context(period.data_start, period.data_end)
        if enable_deepseek and enable_external_context and settings.configured
        else None
    )
    if ui_only:
        ai_summary = ""
    elif enable_deepseek:
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
            external_context=external_context,
        )
    else:
        ai_summary = generate_local_summary(
            analysis.total_summary,
            analysis.platform_summary,
            channel_comparison,
            external_context,
        )
    ai_provider = "deepseek" if enable_deepseek and settings.configured and not ui_only else "local"
    ai_model = settings.model if ai_provider == "deepseek" else ""
    progress("正在固化重点题材")
    topic_label_items = build_topic_label_frame(
        analysis.canonical,
        env_path=env_path,
        topic_labeler=_no_topic_labels if ui_only or not enable_deepseek else None,
    )

    output_dir = Path(output_root) / batch_id
    if ui_only:
        progress("正在写入周期库")
        report_html = analysis_xlsx = canonical_csv = total_summary_xlsx = None
        channel_clean_workbooks = []
    else:
        _replace_directory(output_dir)
        progress("正在写入周期库")
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
            account_filter_rules=analysis.account_filter_rules,
            account_filter_details=analysis.account_filter_details,
        )
        channel_clean_workbooks = write_channel_clean_workbooks(
            analysis.canonical,
            output_dir,
            period_label=period.period_label,
            period_start=period.period_start,
            period_end=period.period_end,
        )
    persist_workflow_result(
        db_path,
        batch_id,
        period_start,
        period_end,
        processed_dir,
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
        ai_provider=ai_provider,
        ai_model=ai_model,
        period_level=period.period_level,
        period_key=period.period_key,
        period_label=period.period_label,
        data_start=period.data_start,
        data_end=period.data_end,
        source_type=period.source_type,
    )
    persist_douyin_id_bridge(db_path, batch_id, analysis.canonical)
    progress("页面数据生成完成")
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
        archive_dir=processed_dir,
        channel_clean_workbooks=channel_clean_workbooks,
        account_filter_rules=analysis.account_filter_rules,
        account_filter_details=analysis.account_filter_details,
    )


def refresh_historical_source_periods(
    *,
    data_root: Path = Path("data"),
    processed_root: Path = Path("processed"),
    output_root: Path = Path("outputs"),
    db_path: Path = Path(".runtime/workflow.sqlite3"),
    metadata_cache_dir: Path | None = None,
    env_path: Optional[Path] = None,
    reference_root: Path | None = None,
) -> list[WorkflowResult]:
    """Rebuild all discovered source periods from raw Excel/CSV files."""
    results: list[WorkflowResult] = []
    for source_period in discover_source_period_dirs(data_root):
        result = run_archived_workflow(
            source_period.path,
            source_period.period.period_start,
            source_period.period.period_end,
            output_root=output_root,
            processed_root=processed_root,
            db_path=db_path,
            env_path=env_path,
            reference_root=reference_root,
            period_level=source_period.period.period_level,
            period_key=source_period.period.period_key,
            period_label=source_period.period.period_label,
            data_start=source_period.period.data_start,
            data_end=source_period.period.data_end,
            source_type=source_period.period.source_type,
            output_mode="ui_only",
            enable_deepseek=False,
            enable_external_context=False,
            write_channel_clean=False,
            metadata_enrichment_mode="safe_public",
            metadata_cache_dir=metadata_cache_dir,
            force_reclean=True,
            allow_public_api_metadata=False,
        )
        results.append(result)
    return results


def run_rollup_workflow(
    db_path: Path,
    component_batch_ids: list[str],
    period: ReviewPeriod,
    output_root: Path = Path("outputs"),
    processed_root: Path = Path("processed"),
    archive_root: Path | None = None,
    category_rules_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    category_matcher=None,
    output_mode: str = "full",
    enable_deepseek: bool = True,
    enable_external_context: bool = True,
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

    batch_id = period_result_id(period)
    ui_only = output_mode == "ui_only"
    processed_dir = Path(processed_root) / source_storage_key(period) / batch_id
    _replace_directory(processed_dir)
    (processed_dir / "rollup_manifest.json").write_text(
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
    douyin_id_bridge = load_douyin_id_bridge(db_path)
    analysis = analyze_canonical_frame(
        canonical,
        period.period_start,
        period.period_end,
        category_rules_path,
        env_path=env_path,
        category_matcher=category_matcher if enable_deepseek else _no_category_matches,
        category_mappings=category_mappings,
        douyin_id_bridge=douyin_id_bridge,
    )
    if previous_summary.empty:
        channel_comparison = _empty_frame()
        comparison_note = "无历史对比数据：数据库中没有同层级上一周期的成功周期。"
    else:
        channel_comparison = build_channel_comparison(analysis.total_summary, previous_summary)
        comparison_note = ""

    settings = resolve_deepseek_settings(env_path)
    external_context = (
        fetch_external_context(period.data_start, period.data_end)
        if enable_deepseek and enable_external_context and settings.configured
        else None
    )
    if enable_deepseek:
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
            external_context=external_context,
        )
    else:
        ai_summary = generate_local_summary(
            analysis.total_summary,
            analysis.platform_summary,
            channel_comparison,
            external_context,
        )
    ai_provider = "deepseek" if enable_deepseek and settings.configured else "local"
    ai_model = settings.model if ai_provider == "deepseek" else ""
    topic_label_items = build_topic_label_frame(
        analysis.canonical,
        env_path=env_path,
        topic_labeler=_no_topic_labels if not enable_deepseek else None,
    )
    output_dir = Path(output_root) / batch_id
    if ui_only:
        report_html = analysis_xlsx = canonical_csv = total_summary_xlsx = None
        channel_clean_workbooks = []
    else:
        _replace_directory(output_dir)
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
            account_filter_rules=analysis.account_filter_rules,
            account_filter_details=analysis.account_filter_details,
        )
        channel_clean_workbooks = write_channel_clean_workbooks(
            analysis.canonical,
            output_dir,
            period_label=period.period_label,
            period_start=period.period_start,
            period_end=period.period_end,
        )
    persist_workflow_result(
        db_path,
        batch_id,
        period.period_start,
        period.period_end,
        processed_dir,
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
        ai_provider=ai_provider,
        ai_model=ai_model,
        period_level=period.period_level,
        period_key=period.period_key,
        period_label=period.period_label,
        data_start=period.data_start,
        data_end=period.data_end,
        source_type=SOURCE_TYPE_ROLLUP,
    )
    persist_douyin_id_bridge(db_path, batch_id, analysis.canonical)
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
        archive_dir=processed_dir,
        channel_clean_workbooks=channel_clean_workbooks,
        account_filter_rules=analysis.account_filter_rules,
        account_filter_details=analysis.account_filter_details,
    )


def _new_batch_id() -> str:
    return f"{date.today().isoformat()}-{uuid4().hex[:8]}"


def _source_file_records(raw_dir: Path) -> list[ArchivedFile]:
    records: list[ArchivedFile] = []
    raw_dir = Path(raw_dir)
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TABULAR_SUFFIXES or _is_generated_artifact(path, raw_dir):
            continue
        records.append(
            ArchivedFile(
                source_file=path.relative_to(raw_dir).as_posix(),
                archive_path=path,
                sha256=_sha256(path),
                size_bytes=path.stat().st_size,
            )
        )
    return records


def _is_generated_artifact(path: Path, root: Path) -> bool:
    item = Path(path)
    try:
        relative = item.relative_to(root)
    except ValueError:
        relative = item
    if item.name in {"cleaned.xlsx", "period_manifest.json"}:
        return True
    if item.stem.lower().endswith("_clean"):
        return True
    return "channel_clean" in relative.parts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_directory(path: Path) -> None:
    path = Path(path)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _no_category_matches(items, category_library, env_path):
    return {}


def _no_topic_labels(items, env_path):
    return {}


def _empty_frame():
    import pandas as pd

    return pd.DataFrame()
