"""Clean noisy raw workbooks into one canonical workbook per review period."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import hashlib
import json
import re
import shutil
from typing import Iterable, Optional

import pandas as pd
from dotenv import dotenv_values

from .content_ledger import load_content_ledger
from .content_metadata import enrich_content_metadata, fetch_douyin_detail_from_harvester, fetch_xhs_downloader_detail
from .feishu_ledger import load_feishu_content_ledger
from .field_mapping import load_field_mapping
from .generated_artifacts import is_generated_tabular_artifact
from .periods import ReviewPeriod, infer_review_period_from_text, period_raw_dir_name
from .pipeline import (
    NUMERIC_COLUMNS,
    STANDARD_COLUMNS,
    TABULAR_SUFFIXES,
    _is_csv,
    _matches_configured_source_alias,
    _preprocess_canonical,
    _read_table,
    _social_market_channel,
    _social_market_platform,
    _standardize,
    _standardize_douyin,
    parse_number,
)
from .source_channels import platform_from_channel_or_name, normalize_channel_name
from .storage import init_db


CLEANED_WORKBOOK_NAME = "cleaned.xlsx"
CLEANED_DETAIL_SHEET = "清洗后明细"
DUPLICATE_FILES_SHEET = "重复文件"
DUPLICATE_CONTENT_SHEET = "重复内容"
CONFLICTS_SHEET = "冲突项"
IGNORED_SHEETS_SHEET = "忽略sheet"
IMPORT_LOG_SHEET = "导入日志"
XHS_ENRICHMENT_REPORT_NAME = "xhs_enrichment_report.xlsx"
REVIEW_ACTION_KEEP = "保留"
REVIEW_ACTION_PENDING = "待审核"
SYNTHETIC_ROW_ID_COLUMN = "__清洗行ID"
SYNTHETIC_ROW_TITLE_COLUMN = "__清洗行标题"
GROUPED_CONTENT_TYPE_COLUMN = "__分组内容类型"
GROUPED_CONTENT_TYPE_LABELS = {"图文", "视频", "直播", "短视频"}
FIELD_MAPPING = load_field_mapping()

EXTRA_CANONICAL_COLUMNS = [
    "source_sheet",
    "source_row",
    "source_file_hash",
    "duplicate_group_id",
    "review_action",
]

HEADER_TOKENS = FIELD_MAPPING.mapped_source_columns
ADDITIVE_METRIC_TOKENS = FIELD_MAPPING.additive_metric_columns
METRIC_TOKENS = FIELD_MAPPING.metric_columns
IDENTITY_TOKENS = FIELD_MAPPING.identity_columns
SUMMARY_TOKENS = {"合计", "总计", "汇总", "小计", "总和", "Total", "TOTAL"}


@dataclass(frozen=True)
class CleanedPeriodBucket:
    review_period: ReviewPeriod
    raw_dir: Path
    cleaned_workbook: Path
    manifest_path: Path
    source_paths: list[str]
    ignored_sheet_count: int = 0
    duplicate_file_count: int = 0


@dataclass(frozen=True)
class SheetCandidate:
    source_path: Path
    relative_source: str
    sheet_name: str
    header_row: int
    frame: pd.DataFrame
    score: int


def clean_source_directory(
    source_root: Path,
    raw_root: Path,
    *,
    default_year: int,
    import_id: Optional[str] = None,
) -> list[CleanedPeriodBucket]:
    """Clean a directory tree of exported workbooks into period raw dirs."""
    source_root = Path(source_root)
    raw_root = Path(raw_root)
    import_id = import_id or f"import_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    original_root = raw_root / "uploaded_originals" / import_id
    original_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    ledger = load_content_ledger(source_root, default_year=default_year)
    ledger_source_files = {Path(path).resolve() for path in ledger.attrs.get("source_files", set())}

    grouped: dict[tuple[str, str], list[tuple[ReviewPeriod, Path, str, str]]] = defaultdict(list)
    duplicate_files: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    first_by_hash: dict[str, tuple[ReviewPeriod, Path, str]] = {}

    for path in _iter_tabular_files(source_root):
        if path.resolve() in ledger_source_files:
            continue
        relative = path.relative_to(source_root).as_posix()
        period = infer_review_period_from_text(relative, default_year)
        if period is None:
            continue
        digest = _sha256(path)
        original_path = original_root / relative
        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, original_path)

        key = (period.period_level, period.period_key)
        if digest in first_by_hash:
            original_period, original_file, original_relative = first_by_hash[digest]
            duplicate_files[key].append(
                {
                    "original_file": original_relative,
                    "duplicate_file": relative,
                    "sha256": digest,
                    "period_key": period.period_key,
                    "note": f"与 {original_file.name} 完全一致，清洗时只保留首份。",
                }
            )
            continue
        first_by_hash[digest] = (period, path, relative)
        grouped[key].append((period, path, relative, digest))

    buckets: list[CleanedPeriodBucket] = []
    for key, entries in sorted(grouped.items(), key=lambda item: (item[1][0][0].period_start, item[0])):
        period = _combine_period_metadata([entry[0] for entry in entries])
        raw_dir = raw_root / period_raw_dir_name(period)
        _reset_period_raw_dir(raw_dir)
        source_paths = [entry[2] for entry in entries]
        frames: list[pd.DataFrame] = []
        ignored_rows: list[dict[str, object]] = []
        import_log_rows: list[dict[str, object]] = []

        for _, path, relative, digest in entries:
            parsed_frames, ignored, logs = _clean_one_workbook(path, relative, digest, period, default_year)
            frames.extend(parsed_frames)
            ignored_rows.extend(ignored)
            import_log_rows.extend(logs)

        if frames:
            canonical = pd.concat(frames, ignore_index=True)
        else:
            canonical = pd.DataFrame(columns=STANDARD_COLUMNS + EXTRA_CANONICAL_COLUMNS)
        canonical["period_start"] = period.period_start
        canonical["period_end"] = period.period_end
        canonical = _ensure_cleaning_columns(canonical)
        preprocessing = _preprocess_canonical(canonical)
        cleaned = _mark_title_conflicts(preprocessing["canonical"])
        metadata_stats = _empty_metadata_stats()
        cleaned = _ensure_cleaning_columns(cleaned)
        cleaned["review_action"] = cleaned["needs_manual_review"].map(
            lambda value: REVIEW_ACTION_PENDING if bool(value) else REVIEW_ACTION_KEEP
        )
        xhs_enrichment_report = _build_xhs_enrichment_report(cleaned)
        _write_xhs_enrichment_artifacts(
            xhs_enrichment_report,
            clean_dir=raw_dir,
            enrichment_queue_root=None,
        )
        duplicate_content = _build_duplicate_content_sheet(
            preprocessing["duplicate_merge_details"],
            cleaned,
        )
        conflicts = _build_conflict_sheet(
            preprocessing["conflict_retention_details"],
            cleaned,
        )
        duplicate_file_frame = pd.DataFrame(
            duplicate_files.get(key, []),
            columns=["original_file", "duplicate_file", "sha256", "period_key", "note"],
        )
        ignored_frame = pd.DataFrame(
            ignored_rows,
            columns=["source_file", "sheet_name", "reason", "rows", "columns", "header_row"],
        )
        import_log = pd.DataFrame(
            import_log_rows,
            columns=["source_file", "sheet_name", "status", "rows", "message"],
        )

        cleaned_workbook = raw_dir / CLEANED_WORKBOOK_NAME
        write_cleaned_workbook(
            cleaned_workbook,
            cleaned,
            duplicate_file_frame,
            duplicate_content,
            conflicts,
            ignored_frame,
            import_log,
        )
        manifest_path = raw_dir / "period_manifest.json"
        _write_manifest(
            manifest_path,
            period,
            cleaned_workbook,
            source_paths,
            ignored_frame,
            duplicate_file_frame,
            metadata_enrichment=metadata_stats,
        )
        buckets.append(
            CleanedPeriodBucket(
                review_period=period,
                raw_dir=raw_dir,
                cleaned_workbook=cleaned_workbook,
                manifest_path=manifest_path,
                source_paths=source_paths,
                ignored_sheet_count=int(len(ignored_frame)),
                duplicate_file_count=int(len(duplicate_file_frame)),
            )
        )
    return buckets


def clean_raw_period_dir(
    raw_dir: Path,
    period: ReviewPeriod,
    *,
    default_year: int,
    output_dir: Path | None = None,
    reference_root: Path | None = None,
    metadata_enrichment_mode: str = "off",
    metadata_cache_dir: Path | None = None,
    enrichment_queue_root: Path | None = None,
    env_path: Path | None = None,
    fetch_bilibili_metadata=None,
    allow_public_api_metadata: bool = True,
    resolve_douyin_shortlink=None,
    preloaded_feishu_ledger: pd.DataFrame | None = None,
) -> CleanedPeriodBucket:
    """Clean an already-materialized raw source directory into an output dir."""
    raw_dir = Path(raw_dir)
    clean_dir = Path(output_dir) if output_dir is not None else raw_dir
    clean_dir.mkdir(parents=True, exist_ok=True)
    ledger = load_cleaning_ledger(
        raw_dir,
        default_year=default_year,
        reference_root=reference_root,
        env_path=env_path,
        preloaded_feishu_ledger=preloaded_feishu_ledger,
    )
    ledger_warnings = [str(value) for value in ledger.attrs.get("ledger_warnings", []) if str(value).strip()]
    ledger_source_files = {Path(path).resolve() for path in ledger.attrs.get("source_files", set())}
    source_paths = [
        path.relative_to(raw_dir).as_posix()
        for path in _iter_tabular_files(raw_dir)
        if path.name != CLEANED_WORKBOOK_NAME
        and path.resolve() not in ledger_source_files
    ]
    frames: list[pd.DataFrame] = []
    ignored_rows: list[dict[str, object]] = []
    import_log_rows: list[dict[str, object]] = []
    duplicate_files: list[dict[str, object]] = []
    first_by_hash: dict[str, str] = {}
    for path in _iter_tabular_files(raw_dir):
        if path.name == CLEANED_WORKBOOK_NAME or path.resolve() in ledger_source_files:
            continue
        relative = path.relative_to(raw_dir).as_posix()
        digest = _sha256(path)
        if digest in first_by_hash:
            duplicate_files.append(
                {
                    "original_file": first_by_hash[digest],
                    "duplicate_file": relative,
                    "sha256": digest,
                    "period_key": period.period_key,
                    "note": "与同周期内其他文件完全一致，清洗时只保留首份。",
                }
            )
            continue
        first_by_hash[digest] = relative
        parsed_frames, ignored, logs = _clean_one_workbook(path, relative, digest, period, default_year)
        frames.extend(parsed_frames)
        ignored_rows.extend(ignored)
        import_log_rows.extend(logs)
    canonical = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STANDARD_COLUMNS)
    canonical["period_start"] = period.period_start
    canonical["period_end"] = period.period_end
    canonical = _ensure_cleaning_columns(canonical)
    metadata_cache_dir = metadata_cache_dir or (clean_dir / ".metadata_cache")
    canonical, metadata_stats = enrich_content_metadata(
        canonical,
        mode=metadata_enrichment_mode,
        cache_dir=metadata_cache_dir,
        fetch_bilibili=fetch_bilibili_metadata,
        fetch_douyin_detail=fetch_douyin_detail_from_harvester,
        fetch_xhs_detail=_xhs_downloader_fetcher(env_path),
        allow_public_api=allow_public_api_metadata,
        resolve_douyin_shortlink=resolve_douyin_shortlink,
    )
    metadata_stats = {"mode": metadata_enrichment_mode, **metadata_stats}
    preprocessing = _preprocess_canonical(canonical)
    cleaned = _mark_title_conflicts(preprocessing["canonical"])
    cleaned = _ensure_cleaning_columns(cleaned)
    cleaned["review_action"] = cleaned["needs_manual_review"].map(
        lambda value: REVIEW_ACTION_PENDING if bool(value) else REVIEW_ACTION_KEEP
    )
    xhs_enrichment_report = _build_xhs_enrichment_report(cleaned)
    _write_xhs_enrichment_artifacts(
        xhs_enrichment_report,
        clean_dir=clean_dir,
        enrichment_queue_root=enrichment_queue_root,
    )
    duplicate_file_frame = pd.DataFrame(
        duplicate_files,
        columns=["original_file", "duplicate_file", "sha256", "period_key", "note"],
    )
    ignored_frame = pd.DataFrame(
        ignored_rows,
        columns=["source_file", "sheet_name", "reason", "rows", "columns", "header_row"],
    )
    import_log = pd.DataFrame(
        [*_ledger_warning_log_rows(ledger_warnings), *import_log_rows],
        columns=["source_file", "sheet_name", "status", "rows", "message"],
    )
    cleaned_workbook = clean_dir / CLEANED_WORKBOOK_NAME
    write_cleaned_workbook(
        cleaned_workbook,
        cleaned,
        duplicate_file_frame,
        _build_duplicate_content_sheet(preprocessing["duplicate_merge_details"], cleaned),
        _build_conflict_sheet(preprocessing["conflict_retention_details"], cleaned),
        ignored_frame,
        import_log,
    )
    manifest_path = clean_dir / "period_manifest.json"
    _write_manifest(
        manifest_path,
        period,
        cleaned_workbook,
        source_paths,
        ignored_frame,
        duplicate_file_frame,
        metadata_enrichment=metadata_stats,
        ledger_warnings=ledger_warnings,
    )
    return CleanedPeriodBucket(
        review_period=period,
        raw_dir=raw_dir,
        cleaned_workbook=cleaned_workbook,
        manifest_path=manifest_path,
        source_paths=source_paths,
        ignored_sheet_count=int(len(ignored_frame)),
        duplicate_file_count=int(len(duplicate_file_frame)),
    )


def load_cleaned_canonical(cleaned_workbook: Path) -> pd.DataFrame:
    cleaned_workbook = Path(cleaned_workbook)
    return pd.read_excel(
        cleaned_workbook,
        sheet_name=CLEANED_DETAIL_SHEET,
        converters={
            "content_id": _excel_text,
            "content_id_fallback": _excel_text,
            "material_id": _excel_text,
            "account_id": _excel_text,
            "duplicate_group_id": _excel_text,
        },
    )


def write_cleaned_workbook(
    cleaned_workbook: Path,
    canonical: pd.DataFrame,
    duplicate_files: pd.DataFrame,
    duplicate_content: pd.DataFrame,
    conflicts: pd.DataFrame,
    ignored_sheets: pd.DataFrame,
    import_log: pd.DataFrame,
) -> None:
    cleaned_workbook = Path(cleaned_workbook)
    cleaned_workbook.parent.mkdir(parents=True, exist_ok=True)
    canonical = _excel_text_columns(canonical)
    with pd.ExcelWriter(cleaned_workbook, engine="openpyxl") as writer:
        canonical.to_excel(writer, sheet_name=CLEANED_DETAIL_SHEET, index=False)
        duplicate_files.to_excel(writer, sheet_name=DUPLICATE_FILES_SHEET, index=False)
        duplicate_content.to_excel(writer, sheet_name=DUPLICATE_CONTENT_SHEET, index=False)
        conflicts.to_excel(writer, sheet_name=CONFLICTS_SHEET, index=False)
        ignored_sheets.to_excel(writer, sheet_name=IGNORED_SHEETS_SHEET, index=False)
        import_log.to_excel(writer, sheet_name=IMPORT_LOG_SHEET, index=False)


def rewrite_cleaned_canonical(cleaned_workbook: Path, canonical: pd.DataFrame) -> None:
    cleaned_workbook = Path(cleaned_workbook)
    sheets = pd.read_excel(cleaned_workbook, sheet_name=None)
    sheets[CLEANED_DETAIL_SHEET] = _excel_text_columns(canonical)
    with pd.ExcelWriter(cleaned_workbook, engine="openpyxl") as writer:
        for sheet_name in [
            CLEANED_DETAIL_SHEET,
            DUPLICATE_FILES_SHEET,
            DUPLICATE_CONTENT_SHEET,
            CONFLICTS_SHEET,
            IGNORED_SHEETS_SHEET,
            IMPORT_LOG_SHEET,
        ]:
            frame = sheets.get(sheet_name, pd.DataFrame())
            frame.to_excel(writer, sheet_name=sheet_name, index=False)


def cleaned_workbook_in_dir(input_dir: Path) -> Path | None:
    candidate = Path(input_dir) / CLEANED_WORKBOOK_NAME
    if candidate.exists():
        return candidate
    return None


def _excel_text_columns(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    for column in ["content_id", "content_id_fallback", "material_id", "account_id", "duplicate_group_id"]:
        if column in prepared.columns:
            prepared[column] = prepared[column].map(_excel_text)
    return prepared


def _excel_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def reset_runtime_data(project_root: Path = Path(".")) -> None:
    """Clear generated runtime data while preserving source data and configs."""
    project_root = Path(project_root)
    for relative in [
        "data/file_backup",
        "archive",
        "processed",
        "outputs",
        "output/playwright",
    ]:
        target = project_root / relative
        if target.exists():
            shutil.rmtree(target)
    db_path = project_root / ".runtime" / "workflow.sqlite3"
    if db_path.exists():
        db_path.unlink()
    for relative in ["data/months", "data/weeks", "processed", "outputs", "output/playwright", ".runtime"]:
        (project_root / relative).mkdir(parents=True, exist_ok=True)
    init_db(db_path)


def _clean_one_workbook(
    path: Path,
    relative: str,
    digest: str,
    period: ReviewPeriod,
    default_year: int,
) -> tuple[list[pd.DataFrame], list[dict[str, object]], list[dict[str, object]]]:
    candidates, ignored, mapping_frames = _sheet_candidates(path, relative, period, default_year)
    selected, additionally_ignored = _select_period_matching_sheets(candidates, period, default_year)
    ignored.extend(additionally_ignored)
    content_map = _combine_content_maps(mapping_frames)
    frames: list[pd.DataFrame] = []
    logs: list[dict[str, object]] = []
    for candidate in selected:
        raw = _drop_noise_rows(candidate.frame)
        raw, row_ignored = _remove_statistical_rows(candidate, raw)
        ignored.extend(row_ignored)
        raw = _add_synthetic_identity_rows(candidate, raw)
        standardized = _standardize_candidate(candidate, raw, content_map)
        standardized, no_metric_ignored = _drop_standardized_rows_without_metrics(candidate, standardized)
        ignored.extend(no_metric_ignored)
        if standardized.empty:
            ignored.append(_ignored_row(relative, candidate.sheet_name, "未识别到内容ID、素材ID或标题", raw, candidate.header_row))
            continue
        standardized["source_file"] = relative
        standardized["source_sheet"] = candidate.sheet_name
        standardized["source_row"] = [
            candidate.header_row + 2 + int(index)
            for index in standardized.index
        ]
        standardized["source_file_hash"] = digest
        standardized["duplicate_group_id"] = ""
        standardized["review_action"] = REVIEW_ACTION_KEEP
        standardized = standardized.reset_index(drop=True)
        frames.append(standardized)
        logs.append(
            {
                "source_file": relative,
                "sheet_name": candidate.sheet_name,
                "status": "已导入",
                "rows": int(len(standardized)),
                "message": "已标准化为清洗后明细。",
            }
        )
    if not selected:
        logs.append(
            {
                "source_file": relative,
                "sheet_name": "",
                "status": "未导入",
                "rows": 0,
                "message": "未找到可识别的原始数据 sheet。",
            }
        )
    return frames, ignored, logs


def _sheet_candidates(
    path: Path,
    relative: str,
    period: ReviewPeriod,
    default_year: int,
) -> tuple[list[SheetCandidate], list[dict[str, object]], list[pd.DataFrame]]:
    candidates: list[SheetCandidate] = []
    ignored: list[dict[str, object]] = []
    mapping_frames: list[pd.DataFrame] = []
    if _is_csv(path):
        frame = _read_table(path)
        score = _header_score(frame.columns)
        if _is_mapping_sheet(frame):
            mapping_frames.append(frame)
            ignored.append(_ignored_row(relative, "CSV", "映射表，仅用于补充内容类型", frame, 0))
        elif _is_raw_candidate(frame, score, path):
            candidates.append(SheetCandidate(path, relative, "CSV", 0, frame, score))
        else:
            ignored.append(_ignored_row(relative, "CSV", "未达到原始数据字段识别阈值", frame, 0))
        return candidates, ignored, mapping_frames

    try:
        with pd.ExcelFile(path) as workbook:
            sheet_names = list(workbook.sheet_names)
    except Exception as exc:
        return [], [_ignored_row(relative, "", f"工作簿无法打开：{exc}", pd.DataFrame(), 0)], []

    for sheet_name in sheet_names:
        candidate = _best_header_candidate(path, relative, sheet_name)
        if candidate is None or candidate.frame.dropna(how="all").empty:
            ignored.append(_ignored_row(relative, sheet_name, "空 sheet", pd.DataFrame(), 0))
            continue
        if _is_mapping_sheet(candidate.frame):
            mapping_frames.append(candidate.frame)
            ignored.append(_ignored_row(relative, sheet_name, "映射表，仅用于补充内容类型", candidate.frame, candidate.header_row))
            continue
        if not _is_raw_candidate(candidate.frame, candidate.score, path):
            ignored.append(_ignored_row(relative, sheet_name, "未达到原始数据字段识别阈值", candidate.frame, candidate.header_row))
            continue
        candidates.append(candidate)
    return candidates, ignored, mapping_frames


def _best_header_candidate(path: Path, relative: str, sheet_name: str) -> SheetCandidate | None:
    best: SheetCandidate | None = None
    for header in range(0, 5):
        try:
            frame = _read_table(path, sheet_name=sheet_name, header=header)
        except Exception:
            continue
        frame = frame.dropna(axis=1, how="all")
        score = _header_score(frame.columns)
        if best is None or score > best.score:
            best = SheetCandidate(path, relative, sheet_name, header, frame, score)
    return best


def _select_period_matching_sheets(
    candidates: list[SheetCandidate],
    period: ReviewPeriod,
    default_year: int,
) -> tuple[list[SheetCandidate], list[dict[str, object]]]:
    exact = [
        candidate
        for candidate in candidates
        if _sheet_date_range(candidate.sheet_name, default_year) == (period.data_start, period.data_end)
    ]
    if not exact:
        return candidates, []
    selected: list[SheetCandidate] = []
    ignored: list[dict[str, object]] = []
    for candidate in candidates:
        detected_range = _sheet_date_range(candidate.sheet_name, default_year)
        if candidate in exact or detected_range is None:
            selected.append(candidate)
        else:
            ignored.append(
                _ignored_row(
                    candidate.relative_source,
                    candidate.sheet_name,
                    "宽周期 sheet，存在与导入周期更匹配的 sheet",
                    candidate.frame,
                    candidate.header_row,
                )
            )
    return selected, ignored


def _sheet_date_range(sheet_name: str, default_year: int) -> tuple[str, str] | None:
    text = str(sheet_name or "")
    match = re.search(r"(?<!\d)(\d{1,2})[./-](\d{1,2})\D{0,4}(\d{1,2})[./-](\d{1,2})(?!\d)", text)
    if match:
        try:
            start = date(default_year, int(match.group(1)), int(match.group(2)))
            end = date(default_year, int(match.group(3)), int(match.group(4)))
        except ValueError:
            return None
        if end < start:
            end = date(default_year + 1, end.month, end.day)
        return start.isoformat(), end.isoformat()
    month_span = re.search(r"(?<!\d)(\d{1,2})\s*[-至到]\s*(\d{1,2})月", text)
    if month_span:
        start_month = int(month_span.group(1))
        end_month = int(month_span.group(2))
        start = date(default_year, start_month, 1)
        end = date(default_year, end_month, 28)
        return start.isoformat(), end.isoformat()
    return None


def _standardize_candidate(
    candidate: SheetCandidate,
    raw: pd.DataFrame,
    content_map: pd.DataFrame,
) -> pd.DataFrame:
    kind = _source_kind(candidate.source_path, raw.columns)
    if kind == "bilibili":
        channel = normalize_channel_name(candidate.source_path.stem)
        return _standardize(
            raw,
            platform="B站",
            platform_group="B站",
            channel=channel,
            source_file=candidate.relative_source,
            fields=FIELD_MAPPING.fields_for_source(kind),
        )
    if kind == "xhs_market":
        return _standardize_xiaohongshu_market(raw, candidate.relative_source)
    if kind == "xhs_commercial":
        prepared = _merge_xiaohongshu_content_map(raw, content_map)
        return _standardize_xiaohongshu_commercial(prepared, candidate.relative_source)
    if kind.startswith("douyin:"):
        return _standardize_douyin_cleaning(raw, candidate.relative_source, kind.split(":", 1)[1])
    if kind == "social":
        channel = _social_market_channel(candidate.source_path.stem)
        platform = _social_market_platform(candidate.source_path.stem)
        return _standardize_social(raw, candidate.relative_source, channel, platform=platform)
    return _standardize_generic(raw, candidate.relative_source, candidate.source_path.stem)


def _standardize_xiaohongshu_commercial(raw: pd.DataFrame, source_file: str) -> pd.DataFrame:
    return _standardize(
        raw,
        platform="小红书商业化",
        platform_group="小红书",
        channel="小红书商业化",
        source_file=source_file,
        fields=_xiaohongshu_fields("xhs_commercial"),
    )


def _standardize_xiaohongshu_market(raw: pd.DataFrame, source_file: str) -> pd.DataFrame:
    return _standardize(
        raw,
        platform="小红书市场部",
        platform_group="小红书",
        channel="小红书市场部",
        source_file=source_file,
        fields=_xiaohongshu_fields("xhs_market"),
    )


def _xiaohongshu_fields(source_kind: str) -> dict[str, list[str]]:
    fields = FIELD_MAPPING.fields_for_source(source_kind)
    fields["title"] = [
        column
        for column in fields.get("title", [])
        if column not in {"链接", "笔记链接", "笔记/素材链接", "内容链接"}
    ]
    return fields


def _standardize_social(raw: pd.DataFrame, source_file: str, channel: str, *, platform: str = "") -> pd.DataFrame:
    return _standardize(
        raw,
        platform=platform or channel,
        platform_group="微信" if platform else channel.replace("市场部", ""),
        channel=channel,
        source_file=source_file,
        fields=FIELD_MAPPING.fields_for_source("social"),
    )


def _standardize_douyin_cleaning(raw: pd.DataFrame, source_file: str, channel: str) -> pd.DataFrame:
    prepared = _add_grouped_content_type(raw)
    return _standardize(
        prepared,
        platform=channel,
        platform_group="抖音",
        channel=channel,
        source_file=source_file,
        fields=FIELD_MAPPING.fields_for_source(f"douyin:{channel}"),
    )


def _add_grouped_content_type(raw: pd.DataFrame) -> pd.DataFrame:
    prepared = raw.copy()
    if GROUPED_CONTENT_TYPE_COLUMN in prepared.columns:
        return prepared
    if "创建时间" not in prepared.columns:
        prepared[GROUPED_CONTENT_TYPE_COLUMN] = ""
        return prepared

    grouped_values: list[str] = []
    current_group = ""
    for _, row in prepared.iterrows():
        label = _grouped_content_type_label(row.get("创建时间"))
        if label:
            current_group = label
        elif not _blank(row.get("创建时间")):
            current_group = ""
        grouped_values.append(current_group)
    prepared[GROUPED_CONTENT_TYPE_COLUMN] = grouped_values
    return prepared


def _grouped_content_type_label(value: object) -> str:
    text = "" if _blank(value) else str(value).strip()
    return text if text in GROUPED_CONTENT_TYPE_LABELS else ""


def _standardize_generic(raw: pd.DataFrame, source_file: str, channel: str) -> pd.DataFrame:
    normalized_channel = normalize_channel_name(channel)
    platform = platform_from_channel_or_name(normalized_channel)
    return _standardize(
        raw,
        platform=platform,
        platform_group=platform,
        channel=normalized_channel,
        source_file=source_file,
        fields=FIELD_MAPPING.fields_for_source("generic"),
    )


def _source_kind(path: Path, columns: Iterable[object]) -> str:
    name = path.name
    column_set = {str(column).strip() for column in columns}
    if "B站" in name or {"视频BVID", "视频bvid", "视频AVID"}.intersection(column_set):
        return "bilibili"
    if "小红书" in name or {"笔记ID", "笔记/素材ID"}.intersection(column_set):
        return "xhs_market" if "市场部" in name else "xhs_commercial"
    if "抖音" in name or {"视频id", "视频标题"}.issubset(column_set):
        if "市场部" in name:
            channel = "抖音市场部"
        elif "商业化" in name or "商增" in name or "达人" in name or "期货" in name:
            channel = "抖音商业化"
        else:
            channel = path.stem
        return f"douyin:{channel}"
    if "微信" in name or "腾讯" in name or "视频号" in name:
        return "social"
    return "generic"


def _uses_content_id_only_cleaning_source(candidate: SheetCandidate, frame: pd.DataFrame) -> bool:
    return _source_kind(candidate.source_path, frame.columns) in {"bilibili", "xhs_commercial", "xhs_market"}


def _merge_xiaohongshu_content_map(raw: pd.DataFrame, content_map: pd.DataFrame) -> pd.DataFrame:
    if content_map.empty or "笔记ID" not in raw.columns:
        prepared = raw.copy()
        prepared["内容类别_解析"] = _first_existing_value(prepared, ["内容分类", "内容类型"])
        prepared["类别来源_解析"] = ""
        return prepared
    mapping = (
        content_map[["笔记ID", "内容类型"]]
        .dropna(subset=["笔记ID"])
        .drop_duplicates(subset=["笔记ID"], keep="first")
        .rename(columns={"内容类型": "内容类型_映射"})
    )
    prepared = raw.merge(mapping, on="笔记ID", how="left")
    prepared["内容类别_解析"] = _first_existing_value(prepared, ["内容分类", "内容类型", "内容类型_映射"])
    prepared["类别来源_解析"] = prepared["内容类型_映射"].map(lambda value: "内容表格映射" if not _blank(value) else "")
    return prepared


def _combine_content_maps(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if {"笔记ID", "内容类型"}.issubset(frame.columns)]
    if not usable:
        return pd.DataFrame()
    return pd.concat(usable, ignore_index=True)


def _first_existing_value(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series([""] * len(frame), index=frame.index, dtype=object)
    for column in columns:
        if column not in frame.columns:
            continue
        values = frame[column]
        mask = result.map(_blank) & ~values.map(_blank)
        result = result.where(~mask, values)
    return result


def _drop_noise_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    cleaned = frame.dropna(how="all").copy()
    if cleaned.empty:
        return cleaned
    column_names = {str(column).strip() for column in cleaned.columns}

    def is_noise(row: pd.Series) -> bool:
        values = [str(value).strip() for value in row.tolist() if not _blank(value)]
        if not values:
            return True
        header_hits = sum(1 for value in values if value in column_names)
        return header_hits >= 2 and header_hits >= max(2, len(values) // 2)

    return cleaned[~cleaned.apply(is_noise, axis=1)].copy()


def _remove_statistical_rows(
    candidate: SheetCandidate,
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    if frame.empty:
        return frame, []
    prepared = frame.copy()
    metric_columns = _additive_metric_columns(prepared)
    if not metric_columns:
        return prepared, []

    ignored: list[dict[str, object]] = []
    keep_mask = pd.Series(True, index=prepared.index)
    rows_to_ignore: dict[int, str] = {}
    keep_identified_id_duplicates = _uses_content_id_only_cleaning_source(candidate, prepared)

    for index, row in prepared.iterrows():
        if not _row_has_metric(row, metric_columns):
            continue
        text = _row_text(row)
        is_text_summary = any(token in text for token in SUMMARY_TOKENS)
        if (
            _metrics_equal_previous_sum(prepared, index, metric_columns)
            and not (keep_identified_id_duplicates and not _is_identity_blank(row, prepared) and not is_text_summary)
        ):
            rows_to_ignore[int(index)] = "汇总行：指标等于前面明细合计，已记录但不进入统计。"
        elif is_text_summary and _identity_columns_present(prepared):
            rows_to_ignore[int(index)] = "汇总行：包含合计/汇总标识，已记录但不进入统计。"

    detail = prepared.drop(index=list(rows_to_ignore.keys()), errors="ignore")
    for index, row in detail.iterrows():
        if not _is_identity_blank(row, detail):
            continue
        match_column, match_value = _matching_group_subtotal(detail, index, metric_columns)
        if match_column:
            rows_to_ignore[int(index)] = f"分组小计行：指标等于 {match_column}={match_value} 的明细合计，已记录但不进入统计。"

    if rows_to_ignore:
        keep_mask.loc[list(rows_to_ignore.keys())] = False
        for index, reason in sorted(rows_to_ignore.items()):
            ignored.append(
                _ignored_row(
                    candidate.relative_source,
                    candidate.sheet_name,
                    f"{reason} 原始行号 {candidate.header_row + 2 + int(index)}。",
                    prepared.loc[[index]],
                    candidate.header_row,
                )
            )
    return prepared[keep_mask].copy(), ignored


def _add_synthetic_identity_rows(candidate: SheetCandidate, frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    prepared = frame.copy()
    metric_columns = _additive_metric_columns(prepared)
    if not metric_columns:
        return prepared
    if SYNTHETIC_ROW_ID_COLUMN not in prepared.columns:
        prepared[SYNTHETIC_ROW_ID_COLUMN] = ""
    if SYNTHETIC_ROW_TITLE_COLUMN not in prepared.columns:
        prepared[SYNTHETIC_ROW_TITLE_COLUMN] = ""
    for index, row in prepared.iterrows():
        if not _row_has_metric(row, metric_columns) or not _is_identity_blank(row, prepared):
            continue
        raw_row = candidate.header_row + 2 + int(index)
        synthetic_id = _synthetic_row_id(candidate.relative_source, candidate.sheet_name, raw_row)
        prepared.at[index, SYNTHETIC_ROW_ID_COLUMN] = synthetic_id
        prepared.at[index, SYNTHETIC_ROW_TITLE_COLUMN] = f"{candidate.source_path.stem} 第{raw_row}行"
    return prepared


def _drop_standardized_rows_without_metrics(
    candidate: SheetCandidate,
    standardized: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    if standardized.empty:
        return standardized, []
    metric_columns = [column for column in ["spend", "impressions", "clicks", "activations", "first_pay_count"] if column in standardized.columns]
    if not metric_columns:
        return standardized, []
    metric_values = standardized[metric_columns].apply(pd.to_numeric, errors="coerce")
    has_any_metric = metric_values.fillna(0).abs().gt(1e-9).any(axis=1)
    synthetic_content_id = (
        standardized.get("content_id", pd.Series("", index=standardized.index))
        .fillna("")
        .astype(str)
        .str.startswith("row:")
    )
    present_metric_count = metric_values.notna().sum(axis=1)
    has_spend = (
        pd.to_numeric(standardized["spend"], errors="coerce").notna()
        if "spend" in standardized.columns
        else pd.Series(False, index=standardized.index)
    )
    conversion_columns = [column for column in ["activations", "first_pay_count"] if column in standardized.columns]
    has_conversion_metric = (
        metric_values[conversion_columns].notna().any(axis=1)
        if conversion_columns
        else pd.Series(False, index=standardized.index)
    )
    weak_synthetic_metric = synthetic_content_id & ~has_spend & (
        present_metric_count.le(1) | ~has_conversion_metric
    )
    keep_mask = has_any_metric & ~weak_synthetic_metric
    ignored: list[dict[str, object]] = []
    for index in standardized.index[~keep_mask]:
        reason = "无统计指标行：仅有内容字段或指标为空，已记录但不进入统计。"
        if bool(weak_synthetic_metric.loc[index]):
            reason = "无可追溯标识且统计指标不足：已记录但不进入统计。"
        ignored.append(
            _ignored_row(
                candidate.relative_source,
                candidate.sheet_name,
                f"{reason} 原始行号 {candidate.header_row + 2 + int(index)}。",
                standardized.loc[[index]],
                candidate.header_row,
            )
        )
    return standardized[keep_mask].copy(), ignored


def _is_raw_candidate(frame: pd.DataFrame, score: int, path: Path | None = None) -> bool:
    columns = {str(column).strip() for column in frame.columns}
    has_metric = bool(_additive_metric_columns(frame))
    has_identity = bool(columns.intersection(IDENTITY_TOKENS))
    known_channel_file = path is not None and _source_kind(Path(path), columns) != "generic"
    return score >= 2 and has_metric and (has_identity or known_channel_file)


def _additive_metric_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        text = str(column).strip()
        if (
            text in ADDITIVE_METRIC_TOKENS
            or any(_matches_configured_source_alias(metric, column) for metric in NUMERIC_COLUMNS)
        ):
            columns.append(column)
    return columns


def _identity_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame.columns if str(column).strip() in IDENTITY_TOKENS]


def _identity_columns_present(frame: pd.DataFrame) -> bool:
    return bool(_identity_columns(frame))


def _is_identity_blank(row: pd.Series, frame: pd.DataFrame) -> bool:
    columns = _identity_columns(frame)
    if not columns:
        return True
    return all(_blank(row.get(column)) for column in columns)


def _row_has_metric(row: pd.Series, metric_columns: list[object]) -> bool:
    for column in metric_columns:
        value = parse_number(row.get(column))
        if not pd.isna(value) and abs(float(value)) > 1e-9:
            return True
    return False


def _metrics_equal_previous_sum(frame: pd.DataFrame, index: object, metric_columns: list[object]) -> bool:
    position = list(frame.index).index(index)
    if position <= 0:
        return False
    checked = 0
    matched = 0
    for column in metric_columns:
        current = parse_number(frame.at[index, column])
        if pd.isna(current) or abs(float(current)) <= 1e-9:
            continue
        previous_values = frame.iloc[:position][column].map(parse_number)
        previous_sum = previous_values.sum(skipna=True)
        checked += 1
        if _numbers_close(float(current), float(previous_sum)):
            matched += 1
    return checked >= 2 and matched == checked


def _matching_group_subtotal(
    frame: pd.DataFrame,
    index: object,
    metric_columns: list[object],
) -> tuple[str, str]:
    row = frame.loc[index]
    detail = frame.drop(index=index)
    detail = detail[~detail.apply(lambda item: _is_identity_blank(item, frame), axis=1)]
    if detail.empty:
        return "", ""
    for column in _groupable_columns(detail, metric_columns):
        grouped = detail.groupby(column, dropna=True, sort=False)
        for group_value, group in grouped:
            if _blank(group_value):
                continue
            checked = 0
            matched = 0
            for metric in metric_columns:
                current = parse_number(row.get(metric))
                if pd.isna(current) or abs(float(current)) <= 1e-9:
                    continue
                group_sum = group[metric].map(parse_number).sum(skipna=True)
                checked += 1
                if _numbers_close(float(current), float(group_sum)):
                    matched += 1
            if checked >= 2 and matched == checked:
                return str(column), str(group_value)
    return "", ""


def _groupable_columns(frame: pd.DataFrame, metric_columns: list[object]) -> list[object]:
    excluded = set(metric_columns) | set(_identity_columns(frame)) | {
        "创建时间",
        "时间",
        "日期",
        "视频链接",
        "笔记链接",
        "链接",
        "内容链接",
        "落地页",
    }
    candidates: list[object] = []
    for column in frame.columns:
        if column in excluded or str(column).startswith(SYNTHETIC_ROW_ID_COLUMN):
            continue
        values = frame[column].dropna().astype(str).str.strip()
        values = values[values.ne("") & values.str.lower().ne("nan")]
        unique_count = values.nunique()
        if 1 < unique_count <= max(20, len(frame) // 2):
            candidates.append(column)
    priority = {"内容类型": 0, "内容分类": 1, "类型": 2, "账号": 3, "账号名称": 4}
    return sorted(candidates, key=lambda column: priority.get(str(column), 99))


def _row_text(row: pd.Series) -> str:
    values = [str(value).strip() for value in row.tolist() if not _blank(value)]
    return " | ".join(values)


def _numbers_close(current: float, expected: float) -> bool:
    return abs(current - expected) <= max(1e-6, abs(expected) * 0.001)


def _synthetic_row_id(source_file: str, sheet_name: str, row_number: int) -> str:
    raw = f"{source_file}|{sheet_name}|{row_number}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"row:{digest}"


def _is_mapping_sheet(frame: pd.DataFrame) -> bool:
    columns = {str(column).strip() for column in frame.columns}
    if {"笔记ID", "内容类型"}.issubset(columns) and not columns.intersection(METRIC_TOKENS):
        return True
    return False


def _header_score(columns: Iterable[object]) -> int:
    score = 0
    for column in columns:
        text = str(column).strip()
        if text in HEADER_TOKENS:
            score += 1
            continue
        if any(token in text for token in HEADER_TOKENS):
            score += 1
    return score


def _mark_title_conflicts(canonical: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical.copy()
    canonical = _ensure_cleaning_columns(canonical)
    title_keys = canonical["title"].map(_normalized_title)
    canonical["_title_key"] = title_keys
    for (period_start, period_end, channel, title_key), group in canonical[
        canonical["_title_key"].astype(str).str.strip().ne("")
    ].groupby(["period_start", "period_end", "channel", "_title_key"], dropna=False, sort=False):
        ids = sorted({str(value).strip() for value in group["content_id"].tolist() if str(value).strip()})
        if len(group) <= 1 or len(ids) <= 1:
            continue
        group_id = f"title:{period_start}:{period_end}:{channel}:{hashlib.sha1(title_key.encode('utf-8')).hexdigest()[:12]}"
        for index in group.index:
            canonical.at[index, "duplicate_group_id"] = group_id
            canonical.at[index, "needs_manual_review"] = True
            canonical.at[index, "review_reasons"] = _append_reason(
                canonical.at[index, "review_reasons"],
                "标题重复但ID不同",
            )
    return canonical.drop(columns=["_title_key"], errors="ignore")


def _build_duplicate_content_sheet(duplicate_details: pd.DataFrame, canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if not duplicate_details.empty:
        for _, row in duplicate_details.iterrows():
            rows.append({**row.to_dict(), "issue_type": "ID重复自动合并"})
    marked = canonical[canonical["duplicate_group_id"].astype(str).str.startswith("title:", na=False)]
    for group_id, group in marked.groupby("duplicate_group_id", sort=False):
        rows.append(
            {
                "dedupe_key": group_id,
                "channel": group["channel"].iloc[0],
                "content_id": " | ".join(sorted({str(value) for value in group["content_id"].tolist() if str(value).strip()})),
                "merged_row_count": int(len(group)),
                "source_files": " | ".join(dict.fromkeys(group["source_file"].astype(str).tolist())),
                "material_ids": " | ".join(sorted({str(value) for value in group["material_id"].tolist() if str(value).strip()})),
                "issue_type": "标题重复但ID不同",
            }
        )
    return pd.DataFrame(
        rows,
        columns=["dedupe_key", "channel", "content_id", "merged_row_count", "source_files", "material_ids", "issue_type"],
    )


def _build_conflict_sheet(conflict_details: pd.DataFrame, canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if not conflict_details.empty:
        for _, row in conflict_details.iterrows():
            payload = row.to_dict()
            if _blank(payload.get("issue_type")):
                payload["issue_type"] = "数值冲突"
            rows.append(payload)
    marked = canonical[canonical["duplicate_group_id"].astype(str).str.startswith("title:", na=False)]
    for group_id, group in marked.groupby("duplicate_group_id", sort=False):
        rows.append(
            {
                "dedupe_key": group_id,
                "channel": group["channel"].iloc[0],
                "content_id": " | ".join(sorted({str(value) for value in group["content_id"].tolist() if str(value).strip()})),
                "column": "title",
                "values": str(group["title"].iloc[0]),
                "action": "manual_review",
                "relative_difference": "",
                "issue_type": "标题重复但ID不同",
            }
        )
    return pd.DataFrame(
        rows,
        columns=["dedupe_key", "channel", "content_id", "column", "values", "action", "relative_difference", "issue_type"],
    )


def _ensure_cleaning_columns(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    for column in dict.fromkeys(STANDARD_COLUMNS + EXTRA_CANONICAL_COLUMNS):
        if column not in prepared.columns:
            if column in {"merged_row_count", "source_row"}:
                prepared[column] = 0
            elif column == "needs_manual_review":
                prepared[column] = False
            else:
                prepared[column] = ""
    return prepared


def _ignored_row(
    source_file: str,
    sheet_name: str,
    reason: str,
    frame: pd.DataFrame,
    header_row: int,
) -> dict[str, object]:
    return {
        "source_file": source_file,
        "sheet_name": sheet_name,
        "reason": reason,
        "rows": int(len(frame)) if frame is not None else 0,
        "columns": int(len(frame.columns)) if frame is not None and not frame.empty else 0,
        "header_row": int(header_row) + 1 if header_row is not None else 0,
    }


def _write_manifest(
    manifest_path: Path,
    period: ReviewPeriod,
    cleaned_workbook: Path,
    source_paths: list[str],
    ignored_sheets: pd.DataFrame,
    duplicate_files: pd.DataFrame,
    metadata_enrichment: dict[str, int] | None = None,
    ledger_warnings: list[str] | None = None,
) -> None:
    payload = {
        "period_level": period.period_level,
        "period_key": period.period_key,
        "period_label": period.period_label,
        "period_start": period.period_start,
        "period_end": period.period_end,
        "data_start": period.data_start,
        "data_end": period.data_end,
        "source_type": period.source_type,
        "file_count": len(source_paths),
        "ignored_sheet_count": int(len(ignored_sheets)),
        "duplicate_file_count": int(len(duplicate_files)),
        "source_paths": source_paths,
        "files": [cleaned_workbook.name],
        "cleaned_workbook": cleaned_workbook.name,
        "metadata_enrichment": metadata_enrichment or _empty_metadata_stats(),
        "ledger_warnings": ledger_warnings or [],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ledger_warning_log_rows(warnings: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for warning in warnings:
        if not str(warning).strip():
            continue
        rows.append(
            {
                "source_file": "飞书台账",
                "sheet_name": "",
                "status": "warning",
                "rows": 0,
                "message": str(warning),
            }
        )
    return rows


def _build_xhs_enrichment_report(canonical: pd.DataFrame) -> pd.DataFrame:
    if canonical.empty:
        return pd.DataFrame(columns=_xhs_enrichment_report_columns())
    rows: list[dict[str, object]] = []
    for _, row in canonical.iterrows():
        if not _is_xhs_row(row):
            continue
        note_id = _clean_text(row.get("content_id", ""))
        content_url = _clean_text(row.get("content_url", ""))
        title = _clean_text(row.get("title", ""))
        tags = _clean_text(row.get("metadata_tags", ""))
        account = _clean_text(row.get("account", "")) or _clean_text(row.get("author", ""))
        link_openability = _clean_text(row.get("link_openability", "")) or ("openable" if content_url else "missing")
        reasons = _xhs_enrichment_reasons(row)
        rows.append(
            {
                "渠道": _clean_text(row.get("channel", "")),
                "笔记ID": note_id,
                "标题": title,
                "账号": account,
                "tag词": tags,
                "作品链接": content_url,
                "占位链接": _clean_text(row.get("xhs_placeholder_url", "")),
                "链接状态": link_openability,
                "链接来源": _clean_text(row.get("link_source", "")),
                "补齐来源": _clean_text(row.get("metadata_source", "")) or _clean_text(row.get("ledger_match_source", "")),
                "补齐置信度": row.get("metadata_confidence", ""),
                "消耗": _numeric_value(row.get("spend", 0)),
                "待处理原因": "；".join(reasons),
                "复核原因": _clean_text(row.get("metadata_review_reason", "")) or _clean_text(row.get("review_reasons", "")),
            }
        )
    return pd.DataFrame(rows, columns=_xhs_enrichment_report_columns())


def _write_xhs_enrichment_artifacts(
    report: pd.DataFrame,
    *,
    clean_dir: Path,
    enrichment_queue_root: Path | None,
) -> None:
    if report.empty:
        return
    report_path = Path(clean_dir) / XHS_ENRICHMENT_REPORT_NAME
    report.to_excel(report_path, index=False)
    if enrichment_queue_root is None:
        return
    pending = report[report["待处理原因"].map(_clean_text).astype(bool)].copy()
    if pending.empty:
        return
    pending = pending.sort_values("消耗", ascending=False, kind="mergesort")
    queue_dir = Path(enrichment_queue_root) / "xhs"
    queue_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    pending.to_excel(queue_dir / f"pending_{today}.xlsx", index=False)


def _xhs_enrichment_report_columns() -> list[str]:
    return [
        "渠道",
        "笔记ID",
        "标题",
        "账号",
        "tag词",
        "作品链接",
        "占位链接",
        "链接状态",
        "链接来源",
        "补齐来源",
        "补齐置信度",
        "消耗",
        "待处理原因",
        "复核原因",
    ]


def _xhs_enrichment_reasons(row: pd.Series) -> list[str]:
    reasons: list[str] = []
    if not _clean_text(row.get("content_id", "")):
        reasons.append("笔记ID缺失")
    if not _clean_text(row.get("title", "")):
        reasons.append("标题缺失")
    if not _clean_text(row.get("account", "")) and not _clean_text(row.get("author", "")):
        reasons.append("账号缺失")
    if not _clean_text(row.get("metadata_tags", "")):
        reasons.append("tag词缺失")
    link_status = _clean_text(row.get("link_openability", ""))
    if not _clean_text(row.get("content_url", "")) or link_status in {"placeholder_only", "missing", "failed"}:
        reasons.append("可打开链接缺失")
    return reasons


def _is_xhs_row(row: pd.Series) -> bool:
    text = " ".join(_clean_text(row.get(column, "")) for column in ["platform", "platform_group", "channel"])
    lowered = text.lower()
    return "小红书" in text or "xiaohongshu" in lowered or "xhs" in lowered


def _numeric_value(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _empty_metadata_stats() -> dict[str, int | str]:
    return {
        "mode": "off",
        "processed_rows": 0,
        "filled_rows": 0,
        "hint_rows": 0,
        "conflict_rows": 0,
        "review_rows": 0,
        "error_rows": 0,
        "cache_hits": 0,
    }


def _combine_period_metadata(periods: list[ReviewPeriod]) -> ReviewPeriod:
    first = periods[0]
    data_start = max(first.period_start, min(period.data_start for period in periods))
    data_end = min(first.period_end, max(period.data_end for period in periods))
    return ReviewPeriod(
        period_level=first.period_level,
        period_key=first.period_key,
        period_label=first.period_label,
        period_start=first.period_start,
        period_end=first.period_end,
        data_start=data_start,
        data_end=data_end,
        source_type=first.source_type,
    )


def _iter_tabular_files(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in Path(root).rglob("*")
            if path.is_file()
            and path.suffix.lower() in TABULAR_SUFFIXES
            and not path.name.startswith("~$")
            and not is_generated_tabular_artifact(path, root)
        ),
        key=lambda path: (_looks_like_duplicate_copy(path.name), path.as_posix()),
    )


def load_cleaning_ledger(
    raw_dir: Path,
    *,
    default_year: int,
    reference_root: Path | None,
    env_path: Path | None = None,
    preloaded_feishu_ledger: pd.DataFrame | None = None,
) -> pd.DataFrame:
    warnings: list[str] = []
    frames: list[pd.DataFrame] = []
    source_files: set[str] = set()
    feishu_snapshot: dict[str, object] | None = None
    feishu_staleness: dict[str, object] | None = None

    try:
        feishu_ledger = (
            preloaded_feishu_ledger
            if preloaded_feishu_ledger is not None
            else load_feishu_content_ledger(default_year=default_year, env_path=env_path)
        )
        raw_snapshot = feishu_ledger.attrs.get("feishu_snapshot")
        if isinstance(raw_snapshot, dict):
            feishu_snapshot = raw_snapshot
        raw_staleness = feishu_ledger.attrs.get("feishu_staleness")
        if isinstance(raw_staleness, dict):
            feishu_staleness = raw_staleness
        if bool(feishu_ledger.attrs.get("feishu_enabled", True)):
            warnings.extend(str(value) for value in feishu_ledger.attrs.get("ledger_warnings", []))
        if not feishu_ledger.empty:
            frames.append(feishu_ledger)
            source_files.update(str(value) for value in feishu_ledger.attrs.get("source_files", set()))
    except Exception as exc:
        warnings.append(f"飞书台账读取失败：{exc}")

    if frames:
        ledger = pd.concat(frames, ignore_index=True)
    else:
        from .content_ledger import LEDGER_COLUMNS

        ledger = pd.DataFrame(columns=LEDGER_COLUMNS)
    ledger.attrs["source_files"] = source_files
    ledger.attrs["ledger_warnings"] = warnings
    if feishu_snapshot is not None:
        ledger.attrs["feishu_snapshot"] = feishu_snapshot
    if feishu_staleness is not None:
        ledger.attrs["feishu_staleness"] = feishu_staleness
    return ledger


def _xhs_downloader_fetcher(env_path: Path | None):
    base_url = _configured_xhs_downloader_base_url(env_path)
    if not base_url:
        return None
    return lambda note_id, link: fetch_xhs_downloader_detail(note_id, link, base_url=base_url)


def _configured_xhs_downloader_base_url(env_path: Path | None) -> str:
    values: dict[str, str] = {}
    candidates: list[Path] = []
    if env_path is not None:
        candidates.append(Path(env_path))
    candidates.extend([Path(".env"), Path(__file__).resolve().parents[1] / ".env"])
    for path in candidates:
        if path.exists():
            values.update({str(key): str(value or "") for key, value in dotenv_values(path).items()})
            break
    env_value = os.environ.get("XHS_DOWNLOADER_BASE_URL")
    if env_value is not None:
        values["XHS_DOWNLOADER_BASE_URL"] = env_value
    return _clean_text(values.get("XHS_DOWNLOADER_BASE_URL", "")).rstrip("/")


def _reset_period_raw_dir(raw_dir: Path) -> None:
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_title(value: object) -> str:
    if _blank(value):
        return ""
    return re.sub(r"\s+", "", str(value).strip()).lower()


def _append_reason(existing: object, reason: str) -> str:
    parts = [part for part in str(existing or "").split("；") if part]
    if reason not in parts:
        parts.append(reason)
    return "；".join(parts)


def _blank(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none"}


def _looks_like_duplicate_copy(name: str) -> int:
    text = str(name or "").lower()
    return 1 if any(token in text for token in ["副本", "copy", "复制"]) else 0
