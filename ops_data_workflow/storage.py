"""SQLite archive and batch persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import hashlib
import re
import shutil
import sqlite3
from typing import Iterable, Optional

import pandas as pd

from .platform_normalizers.bilibili import extract_bvid
from .title_matching import normalized_title_key
from .periods import (
    PERIOD_LEVEL_MONTH,
    PERIOD_LEVEL_WEEK,
    SOURCE_TYPE_UPLOAD,
    ReviewPeriod,
    period_metadata_from_dates,
)
from .cleaning_pipeline import PERIOD_CHANNEL_TOTAL_COLUMNS, split_channel_total_rows


SUCCESS_BATCH_ORDER = "period_end desc, period_start desc, created_at desc"
SUCCESS_BATCH_ORDER_ASC = "period_end asc, period_start asc, created_at asc"
PERIOD_STATE_ACTIVE = "active"
PERIOD_STATE_BACKED_UP = "backed_up"
PERIOD_STATE_DELETED = "deleted"
PERSISTED_RESULT_TABLES = [
    "upload_batches",
    "uploaded_files",
    "ai_reports",
    "category_mappings",
    "period_file_states",
    "file_backups",
    "canonical_items",
    "channel_summary_items",
    "total_summary_items",
    "platform_summary_items",
    "platform_category_summary_items",
    "category_summary_items",
    "top_content_items",
    "content_assets",
    "content_performance_items",
    "period_channel_totals",
    "account_audit_items",
    "cover_metric_items",
    "data_quality_items",
    "preprocessing_report_items",
    "duplicate_merge_items",
    "conflict_retention_items",
    "missing_value_items",
    "channel_comparison_items",
    "topic_label_items",
    "cleaned_asset_items",
    "content_recap_items",
    "unanalyzable_summary_items",
    "attribution_coverage_items",
    "matched_attribution_items",
    "unmatched_attribution_items",
    "feishu_ledger_snapshots",
    "asset_match_results",
    "harvester_asset_jobs",
    "harvester_asset_manifests",
    "top_asset_cache_entries",
    "top_asset_cache_refs",
    "multimodal_recap_items",
    "type_recap_items",
    "recap_settings",
]
CONTENT_ASSET_COLUMNS = [
    "asset_key",
    "platform",
    "content_id",
    "content_url",
    "title",
    "account",
    "tags",
    "raw_content_type",
    "category_l1",
    "category_l2",
    "bilibili_content_type",
    "content_type",
    "content_type_review",
    "filter_status",
    "published_date",
    "source_file",
    "source_sheet",
    "source_row",
    "title_key",
    "title_key_no_tags",
    "first_seen_batch_id",
    "last_seen_batch_id",
    "created_at",
    "updated_at",
]
CONTENT_PERFORMANCE_COLUMNS = [
    "performance_key",
    "batch_id",
    "period_start",
    "period_end",
    "platform",
    "channel",
    "content_identity_key",
    "asset_key",
    "content_id",
    "material_id",
    "content_url",
    "title",
    "account",
    "tags",
    "category_l1",
    "category_l2",
    "bilibili_content_type",
    "content_type",
    "match_status",
    "match_source",
    "match_key",
    "match_confidence",
    "match_reason",
    "spend",
    "impressions",
    "clicks",
    "activations",
    "first_pay_count",
    "ctr",
    "activation_cost",
    "first_pay_cost",
    "first_pay_rate",
    "merged_row_count",
    "source_rows_json",
    "updated_at",
]
MULTIMODAL_RECAP_COLUMNS = [
    "content_identity_key",
    "platform",
    "channel",
    "content_id",
    "title",
    "category_l1",
    "category_l2",
    "bilibili_content_type",
    "content_form",
    "title_hook",
    "visual_structure",
    "information_density",
    "conversion_path",
    "reuse_points",
    "avoid_points",
    "next_period_strategy",
    "summary",
    "raw_result_json",
    "updated_at",
]
TYPE_RECAP_COLUMNS = [
    "batch_id",
    "platform",
    "type_level",
    "content_type",
    "item_count",
    "spend",
    "impressions",
    "activations",
    "first_pay_count",
    "activation_cost",
    "first_pay_cost",
    "value",
    "share",
]
TOP_ASSET_CACHE_ENTRY_COLUMNS = [
    "asset_key",
    "content_id",
    "platform",
    "source",
    "asset_dir",
    "size_bytes",
    "last_used_batch_id",
    "ref_count",
    "created_at",
    "updated_at",
]
TOP_ASSET_CACHE_REF_COLUMNS = [
    "batch_id",
    "job_id",
    "content_identity_key",
    "asset_key",
    "used_at",
    "retained",
]


@dataclass(frozen=True)
class ArchivedFile:
    source_file: str
    archive_path: Path
    sha256: str
    size_bytes: int


def init_db(db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            create table if not exists upload_batches (
                batch_id text primary key,
                period_start text not null,
                period_end text not null,
                created_at text not null,
                archive_dir text not null,
                output_dir text not null,
                status text not null,
                comparison_batch_id text,
                comparison_note text,
                period_level text not null default '',
                period_key text not null default '',
                period_label text not null default '',
                data_start text not null default '',
                data_end text not null default '',
                source_type text not null default ''
            )
            """
        )
        _ensure_upload_batch_metadata_columns(conn)
        conn.execute(
            """
            create table if not exists uploaded_files (
                batch_id text not null,
                source_file text not null,
                archive_path text not null,
                sha256 text not null,
                size_bytes integer not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists ai_reports (
                batch_id text not null,
                provider text not null,
                model text not null,
                summary text not null,
                created_at text not null,
                report_type text not null default 'auto_summary',
                report_json text not null default '',
                primary key (batch_id, report_type)
            )
            """
        )
        _ensure_ai_report_columns(conn)
        conn.execute(
            """
            create table if not exists category_mappings (
                mapping_key text primary key,
                platform text not null,
                platform_group text not null,
                channel text not null default '',
                content_id text not null,
                material_id text not null,
                title text not null,
                title_key text not null default '',
                category_l1 text not null,
                category_l2 text not null,
                category_l3 text not null,
                updated_at text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists period_file_states (
                period_key text primary key,
                period_start text not null,
                period_end text not null,
                status text not null,
                batch_id text not null default '',
                raw_dir text not null default '',
                backup_dir text not null default '',
                updated_at text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists file_backups (
                batch_id text primary key,
                period_key text not null,
                period_start text not null,
                period_end text not null,
                raw_dir text not null default '',
                backup_dir text not null default '',
                backed_up_at text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists topic_label_items (
                batch_id text not null,
                channel text not null default '',
                content_id text not null default '',
                material_id text not null default '',
                title text not null default '',
                content_type text not null default '',
                topic_name text not null default '',
                rank_metric text not null default '',
                rank_value real not null default 0,
                rank_position integer not null default 0,
                source text not null default '',
                provider text not null default '',
                model text not null default '',
                input_hash text not null default '',
                created_at text not null default '',
                spend real not null default 0,
                impressions real not null default 0,
                clicks real not null default 0,
                ctr real not null default 0,
                activations real not null default 0,
                activation_cost real not null default 0,
                first_pay_count real not null default 0,
                first_pay_cost real not null default 0,
                first_pay_rate real not null default 0
            )
            """
        )
        _ensure_table_columns(
            conn,
            "category_mappings",
            pd.DataFrame(
                columns=[
                    "mapping_key",
                    "platform",
                    "platform_group",
                    "channel",
                    "content_id",
                    "material_id",
                    "title",
                    "title_key",
                    "category_l1",
                    "category_l2",
                    "category_l3",
                    "updated_at",
                ]
            ),
        )
        _init_lightweight_middle_platform_tables(conn)
        conn.commit()


def archive_input_files(
    input_dir: Path,
    archive_dir: Path,
    uploaded_zip_path: Optional[Path] = None,
) -> list[ArchivedFile]:
    input_dir = Path(input_dir)
    archive_dir = Path(archive_dir)
    raw_dir = archive_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    archived: list[ArchivedFile] = []

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(input_dir)
        target = raw_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        archived.append(_file_record(target, relative.as_posix()))

    if uploaded_zip_path and Path(uploaded_zip_path).exists():
        target = archive_dir / "uploaded.zip"
        shutil.copy2(uploaded_zip_path, target)
        archived.append(_file_record(target, "uploaded.zip"))
    return archived


def find_previous_batch(db_path: Path, period_start: str) -> Optional[str]:
    return previous_successful_batch_id(db_path, period_start)


def previous_successful_batch_id(db_path: Path, period_start: str) -> Optional[str]:
    return previous_successful_batch_id_for_period(
        db_path,
        period_start,
        "",
        "",
    )


def previous_successful_batch_id_for_period(
    db_path: Path,
    period_start: str,
    period_level: str = "",
    period_key: str = "",
) -> Optional[str]:
    if not Path(db_path).exists():
        return None
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            batches = pd.read_sql_query(
                """
                select batch_id, period_start, period_end, created_at, status,
                       period_level, period_key, period_label, data_start, data_end, source_type
                from upload_batches
                where status = 'ok'
                order by period_end desc, period_start desc, created_at desc
                """,
                conn,
            )
        except Exception:
            row = conn.execute(
                """
                select batch_id
                from upload_batches
                where status = 'ok' and period_end < ?
                order by period_end desc, period_start desc, created_at desc
                limit 1
                """,
                (period_start,),
            ).fetchone()
            return str(row[0]) if row else None
    previous = previous_batch_from_rows(batches, period_start, period_level, period_key)
    return previous or None


def previous_batch_from_rows(
    batches: pd.DataFrame,
    period_start: str,
    period_level: str = "",
    period_key: str = "",
) -> str:
    if batches.empty or not period_start:
        return ""
    normalized = normalize_batch_metadata(batches)
    if normalized.empty:
        return ""
    start = pd.to_datetime(period_start, errors="coerce")
    if pd.isna(start):
        return ""
    level = str(period_level or "").strip()
    if not level:
        current = normalized[normalized["period_start"].astype(str).eq(str(period_start))]
        level = str(current.iloc[0].get("period_level", "")) if not current.empty else PERIOD_LEVEL_WEEK
    level = level or PERIOD_LEVEL_WEEK
    scoped = normalized[normalized["period_level"].eq(level)].copy()
    scoped["_start_dt"] = pd.to_datetime(scoped["period_start"], errors="coerce")
    scoped["_end_dt"] = pd.to_datetime(scoped["period_end"], errors="coerce")
    scoped = scoped[scoped["_start_dt"].notna() & scoped["_end_dt"].notna() & scoped["_start_dt"].lt(start)]
    scoped = _latest_batch_rows_per_period(scoped)
    if scoped.empty:
        return ""

    previous = scoped.sort_values(["_end_dt", "_start_dt", "created_at"], ascending=[False, False, False])
    return str(previous.iloc[0]["batch_id"]) if not previous.empty else ""


def _latest_batch_rows_per_period(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    prepared = rows.copy()
    key_columns = ["period_level", "period_key", "period_start", "period_end"]
    for column in key_columns:
        if column not in prepared.columns:
            prepared[column] = ""
    prepared["_created_dt"] = pd.to_datetime(prepared.get("created_at", ""), errors="coerce", utc=True)
    return (
        prepared.sort_values(key_columns + ["_created_dt"], ascending=[True, True, True, True, False])
        .drop_duplicates(subset=key_columns, keep="first")
        .drop(columns=["_created_dt"], errors="ignore")
    )


def normalize_batch_metadata(batches: pd.DataFrame) -> pd.DataFrame:
    if batches.empty:
        return batches.copy()
    normalized = batches.copy()
    for column in ["period_level", "period_key", "period_label", "data_start", "data_end", "source_type"]:
        if column not in normalized.columns:
            normalized[column] = ""
    for index, row in normalized.iterrows():
        try:
            period = period_metadata_from_dates(
                str(row.get("period_start", "")),
                str(row.get("period_end", "")),
                str(row.get("period_level", "") or ""),
                str(row.get("period_key", "") or ""),
                str(row.get("period_label", "") or ""),
                str(row.get("data_start", "") or ""),
                str(row.get("data_end", "") or ""),
                str(row.get("source_type", "") or SOURCE_TYPE_UPLOAD),
            )
        except Exception:
            continue
        normalized.at[index, "period_level"] = period.period_level
        normalized.at[index, "period_key"] = period.period_key
        normalized.at[index, "period_label"] = period.period_label
        normalized.at[index, "data_start"] = period.data_start
        normalized.at[index, "data_end"] = period.data_end
        normalized.at[index, "source_type"] = period.source_type
    return normalized


def _legacy_period_level(period_start: str, period_end: str) -> str:
    try:
        start = pd.Timestamp(period_start)
        end = pd.Timestamp(period_end)
    except Exception:
        return PERIOD_LEVEL_WEEK
    return PERIOD_LEVEL_MONTH if (end - start).days + 1 >= 21 else PERIOD_LEVEL_WEEK


def _legacy_previous_successful_batch_id(db_path: Path, period_start: str) -> Optional[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select batch_id
            from upload_batches
            where status = 'ok' and period_end < ?
            order by period_end desc, period_start desc, created_at desc
            limit 1
            """,
            (period_start,),
        ).fetchone()
    return str(row[0]) if row else None


def latest_successful_batch_id(db_path: Path) -> Optional[str]:
    if not Path(db_path).exists():
        return None
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            f"""
            select batch_id
            from upload_batches
            where status = 'ok'
            order by {SUCCESS_BATCH_ORDER}
            limit 1
            """
        ).fetchone()
    return str(row[0]) if row else None


def read_total_summary(db_path: Path, batch_id: str) -> pd.DataFrame:
    if not batch_id or not Path(db_path).exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                "select * from total_summary_items where batch_id = ?",
                conn,
                params=(batch_id,),
            ).drop(columns=["batch_id"], errors="ignore")
        except Exception:
            return pd.DataFrame()


def persist_manual_recap_report(
    db_path: Path,
    batch_id: str,
    *,
    provider: str,
    model: str,
    report: dict[str, object],
) -> None:
    init_db(db_path)
    created_at = datetime.now(timezone.utc).isoformat()
    report_json = json.dumps(report, ensure_ascii=False)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            insert or replace into ai_reports (
                batch_id, provider, model, summary, created_at, report_type, report_json
            )
            values (?, ?, ?, ?, ?, 'manual_recap', ?)
            """,
            (batch_id, provider, model, report_json, created_at, report_json),
        )
        conn.commit()


def load_manual_recap_report(db_path: Path, batch_id: str) -> dict[str, object]:
    if not batch_id or not Path(db_path).exists():
        return {}
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select provider, model, summary, report_json, created_at
            from ai_reports
            where batch_id = ? and report_type = 'manual_recap'
            """,
            (batch_id,),
        ).fetchone()
    if row is None:
        return {}
    provider, model, summary, report_json, created_at = row
    raw = str(report_json or summary or "{}")
    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        report = {"overview": {"summary": str(summary or ""), "cause": "", "action": ""}, "channels": []}
    return {
        "provider": str(provider or ""),
        "model": str(model or ""),
        "created_at": str(created_at or ""),
        "report": report if isinstance(report, dict) else {},
    }


def list_recent_batches(db_path: Path, limit: int = 20) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(db_path)) as conn:
        return pd.read_sql_query(
            """
            select batch_id, period_start, period_end, created_at, status, archive_dir, output_dir, comparison_note
            from upload_batches
            order by period_end desc, period_start desc, created_at desc
            limit ?
            """,
            conn,
            params=(limit,),
        )


def read_batch_record(db_path: Path, batch_id: str) -> dict[str, str]:
    if not batch_id or not Path(db_path).exists():
        return {}
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select batch_id, period_start, period_end, created_at, status, archive_dir, output_dir,
                   comparison_batch_id, comparison_note, period_level, period_key, period_label,
                   data_start, data_end, source_type
            from upload_batches
            where batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
    if row is None:
        return {}
    columns = [
        "batch_id",
        "period_start",
        "period_end",
        "created_at",
        "status",
        "archive_dir",
        "output_dir",
        "comparison_batch_id",
        "comparison_note",
        "period_level",
        "period_key",
        "period_label",
        "data_start",
        "data_end",
        "source_type",
    ]
    return {column: "" if value is None else str(value) for column, value in zip(columns, row)}


def is_period_active(db_path: Path, period_start: str, period_end: str) -> bool:
    """Return whether a period is allowed to appear in selectors and raw sync."""
    db_path = Path(db_path)
    if not period_start or not period_end:
        return True
    if not db_path.exists():
        return True
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select status
            from period_file_states
            where period_key = ?
            """,
            (_period_key(period_start, period_end),),
        ).fetchone()
    if row is None:
        return True
    return str(row[0] or PERIOD_STATE_ACTIVE) == PERIOD_STATE_ACTIVE


def list_file_backups(db_path: Path) -> pd.DataFrame:
    """List backed-up periods that can be restored from the app."""
    columns = ["batch_id", "period_start", "period_end", "raw_dir", "backup_dir", "backed_up_at"]
    db_path = Path(db_path)
    if not db_path.exists():
        return pd.DataFrame(columns=columns)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            backups = pd.read_sql_query(
                """
                select batch_id, period_start, period_end, raw_dir, backup_dir, backed_up_at
                from file_backups
                order by backed_up_at desc, period_end desc, period_start desc
                """,
                conn,
            )
        except Exception:
            return pd.DataFrame(columns=columns)
    return backups[columns] if not backups.empty else pd.DataFrame(columns=columns)


def move_batch_to_file_backup(
    db_path: Path,
    batch_id: str,
    raw_root: Path,
    file_backup_root: Path,
) -> dict[str, str]:
    """Hide a batch's raw period files and mark the period as backed up."""
    record = read_batch_record(db_path, batch_id)
    if not record:
        raise ValueError(f"未找到周期：{batch_id}")

    period_start = record["period_start"]
    period_end = record["period_end"]
    period_dir_name = _period_dir_name(period_start, period_end)
    raw_dir = Path(raw_root) / period_dir_name
    backup_dir = Path(file_backup_root) / f"{period_dir_name}_{batch_id}"
    backup_raw_dir = backup_dir / "raw"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if raw_dir.exists() and not backup_raw_dir.exists():
        shutil.move(str(raw_dir), str(backup_raw_dir))
    elif raw_dir.exists() and backup_raw_dir.exists():
        shutil.rmtree(backup_raw_dir)
        shutil.move(str(raw_dir), str(backup_raw_dir))

    now = datetime.now(timezone.utc).isoformat()
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        _upsert_period_state(
            conn,
            period_start,
            period_end,
            PERIOD_STATE_BACKED_UP,
            batch_id,
            raw_dir,
            backup_dir,
            now,
        )
        conn.execute(
            """
            insert into file_backups (
                batch_id, period_key, period_start, period_end, raw_dir, backup_dir, backed_up_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(batch_id) do update set
                period_key = excluded.period_key,
                period_start = excluded.period_start,
                period_end = excluded.period_end,
                raw_dir = excluded.raw_dir,
                backup_dir = excluded.backup_dir,
                backed_up_at = excluded.backed_up_at
            """,
            (
                batch_id,
                _period_key(period_start, period_end),
                period_start,
                period_end,
                str(raw_dir),
                str(backup_dir),
                now,
            ),
        )
        conn.commit()
    return {
        "batch_id": batch_id,
        "period_start": period_start,
        "period_end": period_end,
        "raw_dir": str(raw_dir),
        "backup_dir": str(backup_dir),
    }


def restore_file_backup(
    db_path: Path,
    batch_id: str,
    raw_root: Path,
    file_backup_root: Path,
) -> dict[str, str]:
    """Restore a backed-up raw period so the period appears in selectors again."""
    db_path = Path(db_path)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            """
            select batch_id, period_start, period_end, raw_dir, backup_dir
            from file_backups
            where batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"未找到备份：{batch_id}")
        _, period_start, period_end, stored_raw_dir, stored_backup_dir = row

    period_dir_name = _period_dir_name(str(period_start), str(period_end))
    raw_dir = Path(stored_raw_dir or "") if stored_raw_dir else Path(raw_root) / period_dir_name
    backup_dir = Path(stored_backup_dir or "") if stored_backup_dir else Path(file_backup_root) / f"{period_dir_name}_{batch_id}"
    backup_raw_dir = backup_dir / "raw"
    if raw_dir.exists() and backup_raw_dir.exists():
        raise FileExistsError(f"raw 周期目录已存在，无法恢复：{raw_dir}")
    if backup_raw_dir.exists():
        raw_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(backup_raw_dir), str(raw_dir))
    if backup_dir.exists() and not any(backup_dir.iterdir()):
        backup_dir.rmdir()

    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("delete from file_backups where batch_id = ?", (batch_id,))
        conn.execute("delete from period_file_states where period_key = ?", (_period_key(str(period_start), str(period_end)),))
        conn.commit()
    return {
        "batch_id": batch_id,
        "period_start": str(period_start),
        "period_end": str(period_end),
        "raw_dir": str(raw_dir),
        "backup_dir": str(backup_dir),
    }


def delete_batch_permanently(
    db_path: Path,
    batch_id: str,
    raw_root: Path,
    file_backup_root: Path,
) -> dict[str, str]:
    """Delete one batch's persisted rows and artifacts while preserving reusable mappings."""
    record = read_batch_record(db_path, batch_id)
    if not record:
        raise ValueError(f"未找到周期：{batch_id}")
    period_start = record["period_start"]
    period_end = record["period_end"]
    period_dir_name = _period_dir_name(period_start, period_end)
    raw_dir = Path(raw_root) / period_dir_name
    output_dir = Path(record.get("output_dir", ""))
    archive_dir = Path(record.get("archive_dir", ""))

    for path in [raw_dir, output_dir, archive_dir]:
        _remove_path_if_exists(path)

    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path)) as conn:
        backup_row = conn.execute(
            "select backup_dir from file_backups where batch_id = ?",
            (batch_id,),
        ).fetchone()
        if backup_row and backup_row[0]:
            _remove_path_if_exists(Path(str(backup_row[0])))
        conn.execute("delete from file_backups where batch_id = ?", (batch_id,))
        _delete_batch_scoped_rows(conn, batch_id)
        _upsert_period_state(
            conn,
            period_start,
            period_end,
            PERIOD_STATE_DELETED,
            batch_id,
            raw_dir,
            "",
            now,
        )
        conn.commit()
    return {
        "batch_id": batch_id,
        "period_start": period_start,
        "period_end": period_end,
        "raw_dir": str(raw_dir),
        "output_dir": str(output_dir),
        "archive_dir": str(archive_dir),
    }


def load_category_mappings(db_path: Path) -> dict[str, dict[str, str]]:
    db_path = Path(db_path)
    if not db_path.exists():
        return {}
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            rows = pd.read_sql_query("select * from category_mappings", conn)
        except Exception:
            return {}
    mappings: dict[str, dict[str, str]] = {}
    for _, row in rows.iterrows():
        mapping = {
            "platform": str(row.get("platform", "")),
            "platform_group": str(row.get("platform_group", "")),
            "channel": str(row.get("channel", "")),
            "content_id": str(row.get("content_id", "")),
            "material_id": str(row.get("material_id", "")),
            "title": str(row.get("title", "")),
            "title_key": str(row.get("title_key", "")),
            "category_l1": str(row.get("category_l1", "")),
            "category_l2": str(row.get("category_l2", "")),
            "category_l3": str(row.get("category_l3", "")),
        }
        for key in _mapping_keys(mapping):
            mappings[key] = mapping
    return mappings


def upsert_category_mappings(db_path: Path, mappings: pd.DataFrame) -> int:
    if mappings.empty:
        return 0
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    with closing(sqlite3.connect(db_path)) as conn:
        for _, row in mappings.iterrows():
            mapping = {
                "platform": _clean_text(row.get("platform", "")),
                "platform_group": _clean_text(row.get("platform_group", "")),
                "channel": _clean_text(row.get("channel", "")),
                "content_id": _clean_text(row.get("content_id", "")),
                "material_id": _clean_text(row.get("material_id", "")),
                "title": _clean_text(row.get("title", "")),
                "title_key": _clean_text(row.get("title_key", "")),
                "category_l1": _clean_text(row.get("category_l1", "")),
                "category_l2": _clean_text(row.get("category_l2", "")),
                "category_l3": _clean_text(row.get("category_l3", "")),
            }
            if not mapping["title_key"]:
                mapping["title_key"] = normalized_title_key(mapping["title"])
            if not mapping["category_l2"]:
                continue
            for mapping_key in _mapping_keys(mapping):
                conn.execute(
                    """
                    insert into category_mappings (
                        mapping_key, platform, platform_group, channel, content_id, material_id,
                        title, title_key, category_l1, category_l2, category_l3, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(mapping_key) do update set
                        platform = excluded.platform,
                        platform_group = excluded.platform_group,
                        channel = excluded.channel,
                        content_id = excluded.content_id,
                        material_id = excluded.material_id,
                        title = excluded.title,
                        title_key = excluded.title_key,
                        category_l1 = excluded.category_l1,
                        category_l2 = excluded.category_l2,
                        category_l3 = excluded.category_l3,
                        updated_at = excluded.updated_at
                    """,
                    (
                        mapping_key,
                        mapping["platform"],
                        mapping["platform_group"],
                        mapping["channel"],
                        mapping["content_id"],
                        mapping["material_id"],
                        mapping["title"],
                        mapping["title_key"],
                        mapping["category_l1"],
                        mapping["category_l2"],
                        mapping["category_l3"],
                        now,
                    ),
                )
                written += 1
        conn.commit()
    return written


def persist_topic_labels(db_path: Path, batch_id: str, topic_labels: pd.DataFrame) -> int:
    """Replace persisted focused topic labels for one batch."""
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("delete from topic_label_items where batch_id = ?", (batch_id,))
        if topic_labels.empty:
            conn.commit()
            return 0
        _append_frame(conn, "topic_label_items", batch_id, topic_labels)
        conn.commit()
        return int(len(topic_labels))


def load_topic_labels_for_batch(db_path: Path, batch_id: str) -> pd.DataFrame:
    if not batch_id or not Path(db_path).exists():
        return pd.DataFrame()
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                """
                select *
                from topic_label_items
                where batch_id = ?
                order by channel, rank_position, rowid
                """,
                conn,
                params=(batch_id,),
            ).drop(columns=["batch_id"], errors="ignore")
        except Exception:
            return pd.DataFrame()


def delete_topic_labels_for_batch(db_path: Path, batch_id: str) -> None:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("delete from topic_label_items where batch_id = ?", (batch_id,))
        conn.commit()


def upsert_content_assets_from_feishu(db_path: Path, batch_id: str, ledger: pd.DataFrame) -> int:
    init_db(db_path)
    if ledger is None or ledger.empty:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with closing(sqlite3.connect(db_path)) as conn:
        for _, row in ledger.iterrows():
            asset_key = _content_asset_key(row)
            if not asset_key:
                continue
            incoming = _content_asset_record(asset_key, batch_id, row, now)
            existing_row = conn.execute(
                "select * from content_assets where asset_key = ?",
                (asset_key,),
            ).fetchone()
            if existing_row is not None:
                columns = [item[1] for item in conn.execute("pragma table_info(content_assets)").fetchall()]
                existing = {column: existing_row[index] for index, column in enumerate(columns)}
                incoming = _merge_non_blank(existing, incoming)
                incoming["created_at"] = existing.get("created_at") or now
                incoming["updated_at"] = now
                incoming["last_seen_batch_id"] = batch_id
            conn.execute(
                """
                insert into content_assets (
                    asset_key, platform, content_id, content_url, title, account,
                    tags, raw_content_type, category_l1, category_l2,
                    bilibili_content_type, content_type, content_type_review,
                    filter_status, published_date, source_file, source_sheet,
                    source_row, title_key, title_key_no_tags, first_seen_batch_id,
                    last_seen_batch_id, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(asset_key) do update set
                    platform = excluded.platform,
                    content_id = excluded.content_id,
                    content_url = excluded.content_url,
                    title = excluded.title,
                    account = excluded.account,
                    tags = excluded.tags,
                    raw_content_type = excluded.raw_content_type,
                    category_l1 = excluded.category_l1,
                    category_l2 = excluded.category_l2,
                    bilibili_content_type = excluded.bilibili_content_type,
                    content_type = excluded.content_type,
                    content_type_review = excluded.content_type_review,
                    filter_status = excluded.filter_status,
                    published_date = excluded.published_date,
                    source_file = excluded.source_file,
                    source_sheet = excluded.source_sheet,
                    source_row = excluded.source_row,
                    title_key = excluded.title_key,
                    title_key_no_tags = excluded.title_key_no_tags,
                    first_seen_batch_id = excluded.first_seen_batch_id,
                    last_seen_batch_id = excluded.last_seen_batch_id,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                tuple(incoming[column] for column in CONTENT_ASSET_COLUMNS),
            )
            count += 1
        conn.commit()
    return count


def list_local_content_assets(db_path: Path) -> pd.DataFrame:
    columns = CONTENT_ASSET_COLUMNS
    if not Path(db_path).exists():
        return pd.DataFrame(columns=columns)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        frame = pd.read_sql_query("select * from content_assets order by platform, content_id, title", conn)
    return _normalize_platform_type_frame(frame, columns)


def persist_content_performance_items(db_path: Path, batch_id: str, canonical: pd.DataFrame) -> int:
    init_db(db_path)
    detail, totals = split_channel_total_rows(canonical)
    frame = _content_performance_frame(batch_id, detail)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("delete from content_performance_items where batch_id = ?", (batch_id,))
        if not frame.empty:
            _ensure_table_columns(conn, "content_performance_items", frame)
            frame.to_sql("content_performance_items", conn, if_exists="append", index=False)
        summary_totals = _period_totals_from_summary(batch_id, canonical, pd.DataFrame())
        combined_totals = _combine_period_totals(totals, summary_totals)
        conn.execute("delete from period_channel_totals where batch_id = ?", (batch_id,))
        _append_frame(conn, "period_channel_totals", batch_id, combined_totals)
        conn.commit()
    return int(len(frame))


def list_content_performance_items(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    columns = CONTENT_PERFORMANCE_COLUMNS
    if not Path(db_path).exists():
        return pd.DataFrame(columns=columns)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        sql = "select * from content_performance_items"
        params: list[object] = []
        if batch_id:
            sql += " where batch_id = ?"
            params.append(batch_id)
        sql += " order by period_end desc, channel, spend desc"
        frame = pd.read_sql_query(sql, conn, params=params)
        frame = _backfill_performance_fields_with_conn(conn, frame, batch_id=batch_id)
    return _coerce_content_performance_numbers(_normalize_performance_title_tags(_normalize_platform_type_frame(frame, columns)))


def list_period_channel_totals(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    columns = ["batch_id", *PERIOD_CHANNEL_TOTAL_COLUMNS]
    if not Path(db_path).exists():
        return pd.DataFrame(columns=columns)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        sql = "select * from period_channel_totals"
        params: list[object] = []
        if batch_id:
            sql += " where batch_id = ?"
            params.append(batch_id)
        sql += " order by period_end desc, channel"
        try:
            frame = pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame[columns]


def get_recap_setting_values(db_path: Path) -> dict[str, object]:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("select setting_key, setting_value, updated_at from recap_settings").fetchall()
    values: dict[str, object] = {}
    updated_at = ""
    for key, raw_value, row_updated_at in rows:
        text = _clean_text(raw_value)
        try:
            values[str(key)] = float(text)
        except Exception:
            values[str(key)] = text
        if _clean_text(row_updated_at) > updated_at:
            updated_at = _clean_text(row_updated_at)
    if updated_at:
        values["updated_at"] = updated_at
    return values


def upsert_recap_setting_values(db_path: Path, values: dict[str, object]) -> None:
    init_db(db_path)
    updated_at = _clean_text(values.get("updated_at")) or datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path)) as conn:
        for key, value in values.items():
            if key == "updated_at":
                continue
            conn.execute(
                """
                insert into recap_settings (setting_key, setting_value, updated_at)
                values (?, ?, ?)
                on conflict(setting_key) do update set
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (_clean_text(key), _clean_text(value), updated_at),
            )
        conn.commit()


def persist_multimodal_recap_items(db_path: Path, batch_id: str, items: pd.DataFrame) -> int:
    init_db(db_path)
    frame = items.copy() if items is not None else pd.DataFrame(columns=MULTIMODAL_RECAP_COLUMNS)
    for column in MULTIMODAL_RECAP_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = _normalize_platform_type_frame(frame[MULTIMODAL_RECAP_COLUMNS], MULTIMODAL_RECAP_COLUMNS)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("delete from multimodal_recap_items where batch_id = ?", (batch_id,))
        _append_frame(conn, "multimodal_recap_items", batch_id, frame)
        conn.commit()
    return int(len(frame))


def list_multimodal_recap_items(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    return _normalize_platform_type_frame(
        _read_batch_table(db_path, "multimodal_recap_items", ["batch_id", *MULTIMODAL_RECAP_COLUMNS], batch_id=batch_id),
        ["batch_id", *MULTIMODAL_RECAP_COLUMNS],
    )


def persist_type_recap_items(db_path: Path, batch_id: str, items: pd.DataFrame) -> int:
    init_db(db_path)
    frame = items.copy() if items is not None else pd.DataFrame(columns=TYPE_RECAP_COLUMNS)
    for column in TYPE_RECAP_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[TYPE_RECAP_COLUMNS].drop(columns=["batch_id"], errors="ignore")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("delete from type_recap_items where batch_id = ?", (batch_id,))
        _append_frame(conn, "type_recap_items", batch_id, frame)
        conn.commit()
    return int(len(frame))


def list_type_recap_items(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    return _read_batch_table(db_path, "type_recap_items", TYPE_RECAP_COLUMNS, batch_id=batch_id)


def upsert_top_asset_cache_entry(db_path: Path, entry: dict[str, object]) -> None:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    asset_key = _clean_text(entry.get("asset_key"))
    if not asset_key:
        return
    with closing(sqlite3.connect(db_path)) as conn:
        existing = conn.execute(
            "select created_at from top_asset_cache_entries where asset_key = ?",
            (asset_key,),
        ).fetchone()
        created_at = _clean_text(existing[0]) if existing else now
        conn.execute(
            """
            insert into top_asset_cache_entries (
                asset_key, content_id, platform, source, asset_dir, size_bytes,
                last_used_batch_id, ref_count, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(asset_key) do update set
                content_id = excluded.content_id,
                platform = excluded.platform,
                source = excluded.source,
                asset_dir = excluded.asset_dir,
                size_bytes = excluded.size_bytes,
                last_used_batch_id = excluded.last_used_batch_id,
                updated_at = excluded.updated_at
            """,
            (
                asset_key,
                _clean_text(entry.get("content_id")),
                _clean_text(entry.get("platform")),
                _clean_text(entry.get("source")),
                _clean_text(entry.get("asset_dir")),
                int(entry.get("size_bytes") or 0),
                _clean_text(entry.get("last_used_batch_id")),
                int(entry.get("ref_count") or 0),
                created_at,
                now,
            ),
        )
        _refresh_top_asset_ref_count(conn, asset_key)
        conn.commit()


def upsert_top_asset_cache_ref(
    db_path: Path,
    *,
    batch_id: str,
    job_id: str,
    content_identity_key: str,
    asset_key: str,
    retained: bool = True,
) -> None:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            insert into top_asset_cache_refs (
                batch_id, job_id, content_identity_key, asset_key, used_at, retained
            )
            values (?, ?, ?, ?, ?, ?)
            on conflict(batch_id, job_id) do update set
                content_identity_key = excluded.content_identity_key,
                asset_key = excluded.asset_key,
                used_at = excluded.used_at,
                retained = excluded.retained
            """,
            (
                _clean_text(batch_id),
                _clean_text(job_id),
                _clean_text(content_identity_key),
                _clean_text(asset_key),
                now,
                1 if retained else 0,
            ),
        )
        _refresh_top_asset_ref_count(conn, _clean_text(asset_key))
        conn.commit()


def list_top_asset_cache_entries(db_path: Path) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame(columns=TOP_ASSET_CACHE_ENTRY_COLUMNS)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        frame = pd.read_sql_query(
            "select * from top_asset_cache_entries order by updated_at desc, asset_key",
            conn,
        )
    for column in TOP_ASSET_CACHE_ENTRY_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0 if column in {"size_bytes", "ref_count"} else ""
    return frame[TOP_ASSET_CACHE_ENTRY_COLUMNS]


def list_top_asset_cache_refs(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame(columns=TOP_ASSET_CACHE_REF_COLUMNS)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        sql = "select * from top_asset_cache_refs"
        params: list[object] = []
        if batch_id:
            sql += " where batch_id = ?"
            params.append(batch_id)
        sql += " order by used_at desc, batch_id, job_id"
        frame = pd.read_sql_query(sql, conn, params=params)
    for column in TOP_ASSET_CACHE_REF_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0 if column == "retained" else ""
    return frame[TOP_ASSET_CACHE_REF_COLUMNS]


def get_top_asset_cache_summary(db_path: Path) -> dict[str, int]:
    entries = list_top_asset_cache_entries(db_path)
    if entries.empty:
        return {"entry_count": 0, "size_bytes": 0, "ref_count": 0}
    return {
        "entry_count": int(len(entries)),
        "size_bytes": int(pd.to_numeric(entries["size_bytes"], errors="coerce").fillna(0).sum()),
        "ref_count": int(pd.to_numeric(entries["ref_count"], errors="coerce").fillna(0).sum()),
    }


def remove_top_asset_cache_entry(db_path: Path, asset_key: str) -> None:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("delete from top_asset_cache_entries where asset_key = ?", (_clean_text(asset_key),))
        conn.commit()


def mark_top_asset_cache_ref_retained(db_path: Path, asset_key: str, *, retained: bool) -> None:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "update top_asset_cache_refs set retained = ? where asset_key = ?",
            (1 if retained else 0, _clean_text(asset_key)),
        )
        conn.commit()


def _refresh_top_asset_ref_count(conn: sqlite3.Connection, asset_key: str) -> None:
    key = _clean_text(asset_key)
    if not key:
        return
    row = conn.execute(
        "select count(*), max(batch_id) from top_asset_cache_refs where asset_key = ?",
        (key,),
    ).fetchone()
    ref_count = int(row[0] or 0) if row else 0
    last_batch = _clean_text(row[1]) if row else ""
    conn.execute(
        """
        update top_asset_cache_entries
        set ref_count = ?, last_used_batch_id = coalesce(nullif(?, ''), last_used_batch_id)
        where asset_key = ?
        """,
        (ref_count, last_batch, key),
    )


def persist_feishu_ledger_snapshot(db_path: Path, batch_id: str, snapshot: dict[str, object]) -> str:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        snapshot_id = _persist_feishu_ledger_snapshot_with_conn(conn, batch_id, snapshot)
        conn.commit()
    return snapshot_id


def persist_harvester_asset_jobs(
    db_path: Path,
    batch_id: str,
    jobs: Iterable[dict[str, object]],
    *,
    status: str,
    harvester_root: Path,
    jobs_path: Path,
    manifest_path: Path,
    error_message: str = "",
) -> int:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with closing(sqlite3.connect(db_path)) as conn:
        for job in jobs:
            job_status = _clean_text(job.get("status")) or status
            job_error = _clean_text(job.get("error_message")) or error_message
            conn.execute(
                """
                insert into harvester_asset_jobs (
                    job_id, batch_id, status, platform, channel, content_identity_key,
                    content_id, content_url, title, account, period_start, period_end,
                    metrics_json, harvester_root, jobs_path, manifest_path, error_message,
                    created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(batch_id, job_id) do update set
                    status = excluded.status,
                    platform = excluded.platform,
                    channel = excluded.channel,
                    content_identity_key = excluded.content_identity_key,
                    content_id = excluded.content_id,
                    content_url = excluded.content_url,
                    title = excluded.title,
                    account = excluded.account,
                    period_start = excluded.period_start,
                    period_end = excluded.period_end,
                    metrics_json = excluded.metrics_json,
                    harvester_root = excluded.harvester_root,
                    jobs_path = excluded.jobs_path,
                    manifest_path = excluded.manifest_path,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    _clean_text(job.get("job_id")),
                    batch_id,
                    job_status,
                    _clean_text(job.get("platform")),
                    _clean_text(job.get("channel")),
                    _clean_text(job.get("content_identity_key")),
                    _clean_text(job.get("content_id")),
                    _clean_text(job.get("content_url")),
                    _clean_text(job.get("title")),
                    _clean_text(job.get("account")),
                    _clean_text(job.get("period_start")),
                    _clean_text(job.get("period_end")),
                    json.dumps(job.get("metrics") or {}, ensure_ascii=False, sort_keys=True),
                    str(harvester_root),
                    str(jobs_path),
                    str(manifest_path),
                    job_error,
                    now,
                    now,
                ),
            )
            count += 1
        conn.commit()
    return count


def persist_harvester_asset_manifests(
    db_path: Path,
    batch_id: str,
    manifests: Iterable[dict[str, object]],
) -> int:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with closing(sqlite3.connect(db_path)) as conn:
        for item in manifests:
            conn.execute(
                """
                insert into harvester_asset_manifests (
                    job_id, batch_id, status, platform, asset_key, asset_dir, cover_path,
                    video_path, screenshots_json, frames_json, metadata_json,
                    error_message, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(batch_id, job_id) do update set
                    status = excluded.status,
                    platform = excluded.platform,
                    asset_key = excluded.asset_key,
                    asset_dir = excluded.asset_dir,
                    cover_path = excluded.cover_path,
                    video_path = excluded.video_path,
                    screenshots_json = excluded.screenshots_json,
                    frames_json = excluded.frames_json,
                    metadata_json = excluded.metadata_json,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    _clean_text(item.get("job_id")),
                    batch_id,
                    _clean_text(item.get("status")),
                    _clean_text(item.get("platform")),
                    _clean_text(item.get("asset_key")),
                    _clean_text(item.get("asset_dir")),
                    _clean_text(item.get("cover_path")),
                    _clean_text(item.get("video_path")),
                    json.dumps(item.get("screenshots") or [], ensure_ascii=False),
                    json.dumps(item.get("frames") or [], ensure_ascii=False),
                    json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                    _clean_text(item.get("error_message")),
                    now,
                    now,
                ),
            )
            count += 1
        conn.commit()
    return count


def list_harvester_asset_jobs(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    columns = [
        "job_id",
        "batch_id",
        "status",
        "platform",
        "channel",
        "content_identity_key",
        "content_id",
        "content_url",
        "title",
        "account",
        "period_start",
        "period_end",
        "metrics_json",
        "harvester_root",
        "jobs_path",
        "manifest_path",
        "error_message",
        "created_at",
        "updated_at",
    ]
    return _read_harvester_table(db_path, "harvester_asset_jobs", columns, batch_id=batch_id)


def list_harvester_asset_manifests(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    columns = [
        "job_id",
        "batch_id",
        "status",
        "platform",
        "asset_key",
        "asset_dir",
        "cover_path",
        "video_path",
        "screenshots_json",
        "frames_json",
        "metadata_json",
        "error_message",
        "created_at",
        "updated_at",
    ]
    return _read_harvester_table(db_path, "harvester_asset_manifests", columns, batch_id=batch_id)


def persist_workflow_result(
    db_path: Path,
    batch_id: str,
    period_start: str,
    period_end: str,
    archive_dir: Path,
    output_dir: Path,
    archived_files: Iterable[ArchivedFile],
    canonical: pd.DataFrame,
    channel_summary: pd.DataFrame,
    total_summary: pd.DataFrame,
    platform_summary: pd.DataFrame,
    platform_category_summary: pd.DataFrame,
    category_summary: pd.DataFrame,
    top_content_items: pd.DataFrame,
    account_audit: pd.DataFrame,
    cover_metrics: pd.DataFrame,
    data_quality: pd.DataFrame,
    preprocessing_report: pd.DataFrame,
    duplicate_merge_details: pd.DataFrame,
    conflict_retention_details: pd.DataFrame,
    missing_value_details: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    topic_label_items: Optional[pd.DataFrame],
    cleaned_asset_table: Optional[pd.DataFrame],
    content_recap_table: Optional[pd.DataFrame],
    unanalyzable_summary: Optional[pd.DataFrame],
    ai_summary: str,
    comparison_batch_id: Optional[str],
    comparison_note: str,
    ai_provider: str = "deepseek",
    ai_model: str = "",
    period_level: str = "",
    period_key: str = "",
    period_label: str = "",
    data_start: str = "",
    data_end: str = "",
    source_type: str = SOURCE_TYPE_UPLOAD,
    attribution_coverage: Optional[pd.DataFrame] = None,
    matched_attribution: Optional[pd.DataFrame] = None,
    unmatched_attribution: Optional[pd.DataFrame] = None,
    feishu_snapshot: Optional[dict[str, object]] = None,
) -> None:
    init_db(db_path)
    created_at = datetime.now(timezone.utc).isoformat()
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
    with closing(sqlite3.connect(db_path)) as conn:
        _delete_period_scoped_rows(conn, period, include_batch_id=batch_id)
        conn.execute(
            """
            insert into upload_batches (
                batch_id, period_start, period_end, created_at, archive_dir,
                output_dir, status, comparison_batch_id, comparison_note,
                period_level, period_key, period_label, data_start, data_end, source_type
            )
            values (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                period.period_start,
                period.period_end,
                created_at,
                str(archive_dir),
                str(output_dir),
                comparison_batch_id or "",
                comparison_note,
                period.period_level,
                period.period_key,
                period.period_label,
                period.data_start,
                period.data_end,
                period.source_type,
            ),
        )
        conn.execute(
            "delete from period_file_states where period_key = ?",
            (_period_key(period.period_start, period.period_end),),
        )
        for item in archived_files:
            conn.execute(
                """
                insert into uploaded_files (batch_id, source_file, archive_path, sha256, size_bytes)
                values (?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    item.source_file,
                    str(item.archive_path),
                    item.sha256,
                    item.size_bytes,
                ),
            )
        _append_frame(conn, "canonical_items", batch_id, canonical)
        _append_frame(conn, "channel_summary_items", batch_id, channel_summary)
        _append_frame(conn, "total_summary_items", batch_id, total_summary)
        _append_frame(conn, "platform_summary_items", batch_id, platform_summary)
        _append_frame(conn, "platform_category_summary_items", batch_id, platform_category_summary)
        _append_frame(conn, "category_summary_items", batch_id, category_summary)
        _append_frame(conn, "top_content_items", batch_id, top_content_items)
        _append_frame(conn, "account_audit_items", batch_id, account_audit)
        _append_frame(conn, "cover_metric_items", batch_id, cover_metrics)
        _append_frame(conn, "data_quality_items", batch_id, data_quality)
        _append_frame(conn, "preprocessing_report_items", batch_id, preprocessing_report)
        _append_frame(conn, "duplicate_merge_items", batch_id, duplicate_merge_details)
        _append_frame(conn, "conflict_retention_items", batch_id, conflict_retention_details)
        _append_frame(conn, "missing_value_items", batch_id, missing_value_details)
        _append_frame(conn, "channel_comparison_items", batch_id, channel_comparison)
        _append_frame(conn, "topic_label_items", batch_id, topic_label_items if topic_label_items is not None else pd.DataFrame())
        _append_frame(conn, "cleaned_asset_items", batch_id, cleaned_asset_table if cleaned_asset_table is not None else pd.DataFrame())
        _append_frame(conn, "content_recap_items", batch_id, content_recap_table if content_recap_table is not None else pd.DataFrame())
        _append_frame(conn, "unanalyzable_summary_items", batch_id, unanalyzable_summary if unanalyzable_summary is not None else pd.DataFrame())
        _append_frame(conn, "attribution_coverage_items", batch_id, attribution_coverage if attribution_coverage is not None else pd.DataFrame())
        _append_frame(conn, "matched_attribution_items", batch_id, matched_attribution if matched_attribution is not None else pd.DataFrame())
        _append_frame(conn, "unmatched_attribution_items", batch_id, unmatched_attribution if unmatched_attribution is not None else pd.DataFrame())
        _append_frame(conn, "asset_match_results", batch_id, _asset_match_result_frame(canonical))
        detail_canonical, explicit_channel_totals = split_channel_total_rows(canonical)
        summary_channel_totals = _period_totals_from_summary(batch_id, canonical, total_summary)
        period_channel_totals = _combine_period_totals(explicit_channel_totals, summary_channel_totals)
        conn.execute("delete from period_channel_totals where batch_id = ?", (batch_id,))
        _append_frame(conn, "period_channel_totals", batch_id, period_channel_totals)
        performance = _content_performance_frame(batch_id, detail_canonical)
        conn.execute("delete from content_performance_items where batch_id = ?", (batch_id,))
        if not performance.empty:
            _ensure_table_columns(conn, "content_performance_items", performance)
            performance.to_sql("content_performance_items", conn, if_exists="append", index=False)
        if feishu_snapshot is not None:
            _persist_feishu_ledger_snapshot_with_conn(conn, batch_id, feishu_snapshot)
        if str(ai_summary or "").strip():
            conn.execute(
                """
                insert or replace into ai_reports (batch_id, provider, model, summary, created_at, report_type, report_json)
                values (?, ?, ?, ?, ?, 'auto_summary', '')
                """,
                (batch_id, ai_provider, ai_model, ai_summary, created_at),
            )
        conn.commit()


def purge_history_state(
    db_path: Path,
    output_root: Optional[Path] = None,
    archive_root: Optional[Path] = None,
) -> None:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        existing_tables = {
            row[0]
            for row in conn.execute("select name from sqlite_master where type = 'table'").fetchall()
        }
        for table_name in PERSISTED_RESULT_TABLES:
            if table_name not in existing_tables:
                continue
            conn.execute(f'delete from "{_sqlite_identifier(table_name)}"')
        conn.commit()
    for root in [output_root, archive_root]:
        if root is None:
            continue
        _clear_directory(Path(root))


def _clear_directory(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _period_key(period_start: str, period_end: str) -> str:
    return f"{period_start}|{period_end}"


def _period_dir_name(period_start: str, period_end: str) -> str:
    return f"{str(period_start).replace('-', '')}-{str(period_end).replace('-', '')}"


def _upsert_period_state(
    conn: sqlite3.Connection,
    period_start: str,
    period_end: str,
    status: str,
    batch_id: str,
    raw_dir: Path | str,
    backup_dir: Path | str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        insert into period_file_states (
            period_key, period_start, period_end, status, batch_id, raw_dir, backup_dir, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(period_key) do update set
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            status = excluded.status,
            batch_id = excluded.batch_id,
            raw_dir = excluded.raw_dir,
            backup_dir = excluded.backup_dir,
            updated_at = excluded.updated_at
        """,
        (
            _period_key(period_start, period_end),
            period_start,
            period_end,
            status,
            batch_id,
            str(raw_dir),
            str(backup_dir),
            updated_at,
        ),
    )


def _delete_batch_scoped_rows(conn: sqlite3.Connection, batch_id: str) -> None:
    for table_name in [
        "uploaded_files",
        "canonical_items",
        "channel_summary_items",
        "total_summary_items",
        "platform_summary_items",
        "platform_category_summary_items",
        "category_summary_items",
        "top_content_items",
        "account_audit_items",
        "cover_metric_items",
        "data_quality_items",
        "preprocessing_report_items",
        "duplicate_merge_items",
        "conflict_retention_items",
        "missing_value_items",
        "channel_comparison_items",
        "topic_label_items",
        "cleaned_asset_items",
        "content_recap_items",
        "unanalyzable_summary_items",
        "attribution_coverage_items",
        "matched_attribution_items",
        "unmatched_attribution_items",
        "asset_match_results",
        "content_performance_items",
        "period_channel_totals",
        "feishu_ledger_snapshots",
        "harvester_asset_jobs",
        "harvester_asset_manifests",
        "multimodal_recap_items",
        "type_recap_items",
        "upload_batches",
    ]:
        if not conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?",
            (table_name,),
        ).fetchone():
            continue
        columns = {
            row[1]
            for row in conn.execute(f'pragma table_info("{_sqlite_identifier(table_name)}")').fetchall()
        }
        if "batch_id" not in columns:
            continue
        conn.execute(f'delete from "{_sqlite_identifier(table_name)}" where batch_id = ?', (batch_id,))

    if conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'ai_reports'",
    ).fetchone():
        conn.execute("delete from ai_reports where batch_id = ?", (batch_id,))


def _delete_period_scoped_rows(
    conn: sqlite3.Connection,
    period: ReviewPeriod,
    *,
    include_batch_id: str = "",
) -> None:
    existing_ids: set[str] = set()
    if include_batch_id:
        existing_ids.add(str(include_batch_id))
    try:
        rows = conn.execute(
            """
            select batch_id
            from upload_batches
            where period_level = ?
              and period_key = ?
              and source_type = ?
            """,
            (period.period_level, period.period_key, period.source_type),
        ).fetchall()
        existing_ids.update(str(row[0]) for row in rows if row and row[0])
    except Exception:
        pass
    try:
        rows = conn.execute(
            """
            select batch_id
            from upload_batches
            where period_start = ?
              and period_end = ?
              and coalesce(source_type, '') in (?, '')
            """,
            (period.period_start, period.period_end, period.source_type),
        ).fetchall()
        existing_ids.update(str(row[0]) for row in rows if row and row[0])
    except Exception:
        pass
    for batch_id in sorted(existing_ids):
        _delete_batch_scoped_rows(conn, batch_id)


def _remove_path_if_exists(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _append_frame(conn: sqlite3.Connection, table_name: str, batch_id: str, frame: pd.DataFrame) -> None:
    if frame.empty and len(frame.columns) == 0:
        return
    stored = frame.copy()
    stored.insert(0, "batch_id", batch_id)
    _ensure_table_columns(conn, table_name, stored)
    if stored.empty:
        return
    stored.to_sql(table_name, conn, if_exists="append", index=False)


def _ensure_table_columns(conn: sqlite3.Connection, table_name: str, frame: pd.DataFrame) -> None:
    exists = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).fetchone()
    if not exists:
        columns_sql = ", ".join(
            f'"{_sqlite_identifier(str(column))}" {_sqlite_type(frame[column])}'
            for column in frame.columns
        )
        conn.execute(f'create table if not exists "{_sqlite_identifier(table_name)}" ({columns_sql})')
        return

    existing = {
        row[1]
        for row in conn.execute(f'pragma table_info("{_sqlite_identifier(table_name)}")').fetchall()
    }
    for column in frame.columns:
        if column in existing:
            continue
        conn.execute(
            f'alter table "{_sqlite_identifier(table_name)}" add column "{_sqlite_identifier(str(column))}" {_sqlite_type(frame[column])}'
        )


def _ensure_upload_batch_metadata_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute('pragma table_info("upload_batches")').fetchall()
    }
    for column in ["period_level", "period_key", "period_label", "data_start", "data_end", "source_type"]:
        if column not in existing:
            conn.execute(f'alter table "upload_batches" add column "{column}" text not null default ""')


def _ensure_ai_report_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute('pragma table_info("ai_reports")').fetchall()
    }
    for column in ["report_type", "report_json"]:
        if column not in existing:
            conn.execute(f'alter table "ai_reports" add column "{column}" text not null default ""')
    conn.execute(
        "update ai_reports set report_type = 'auto_summary' where coalesce(report_type, '') = ''"
    )


def _init_lightweight_middle_platform_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists feishu_ledger_snapshots (
            snapshot_id text primary key,
            batch_id text not null default '',
            enabled integer not null default 0,
            fetched_at text not null default '',
            total_rows integer not null default 0,
            platform_counts_json text not null default '{}',
            sheet_row_counts_json text not null default '{}',
            field_completeness_json text not null default '{}',
            warnings_json text not null default '[]',
            created_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists content_assets (
            asset_key text primary key,
            platform text not null default '',
            content_id text not null default '',
            content_url text not null default '',
            title text not null default '',
            account text not null default '',
            tags text not null default '',
            raw_content_type text not null default '',
            category_l1 text not null default '',
            category_l2 text not null default '',
            bilibili_content_type text not null default '',
            content_type text not null default '',
            content_type_review text not null default '',
            filter_status text not null default '',
            published_date text not null default '',
            source_file text not null default '',
            source_sheet text not null default '',
            source_row integer not null default 0,
            title_key text not null default '',
            title_key_no_tags text not null default '',
            first_seen_batch_id text not null default '',
            last_seen_batch_id text not null default '',
            created_at text not null default '',
            updated_at text not null default ''
        )
        """
    )
    _ensure_table_columns(conn, "content_performance_items", pd.DataFrame(columns=CONTENT_PERFORMANCE_COLUMNS))
    _ensure_table_columns(conn, "period_channel_totals", pd.DataFrame(columns=["batch_id", *PERIOD_CHANNEL_TOTAL_COLUMNS]))
    conn.execute(
        """
        create table if not exists asset_match_results (
            batch_id text not null,
            period_start text not null default '',
            period_end text not null default '',
            platform text not null default '',
            channel text not null default '',
            content_identity_key text not null default '',
            content_id text not null default '',
            material_id text not null default '',
            title text not null default '',
            matched_ledger_title text not null default '',
            content_url text not null default '',
            match_status text not null default '',
            match_source text not null default '',
            match_key text not null default '',
            match_confidence real not null default 0,
            match_reason text not null default '',
            matched_category_l1 text not null default '',
            matched_category_l2 text not null default '',
            matched_bilibili_content_type text not null default '',
            matched_content_type text not null default '',
            matched_account text not null default ''
        )
        """
    )
    conn.execute(
        """
        create table if not exists harvester_asset_jobs (
            job_id text not null default '',
            batch_id text not null default '',
            status text not null default '',
            platform text not null default '',
            channel text not null default '',
            content_identity_key text not null default '',
            content_id text not null default '',
            content_url text not null default '',
            title text not null default '',
            account text not null default '',
            period_start text not null default '',
            period_end text not null default '',
            metrics_json text not null default '{}',
            harvester_root text not null default '',
            jobs_path text not null default '',
            manifest_path text not null default '',
            error_message text not null default '',
            created_at text not null default '',
            updated_at text not null default '',
            primary key (batch_id, job_id)
        )
        """
    )
    conn.execute(
        """
        create table if not exists harvester_asset_manifests (
            job_id text not null default '',
            batch_id text not null default '',
            status text not null default '',
            platform text not null default '',
            asset_key text not null default '',
            asset_dir text not null default '',
            cover_path text not null default '',
            video_path text not null default '',
            screenshots_json text not null default '[]',
            frames_json text not null default '[]',
            metadata_json text not null default '{}',
            error_message text not null default '',
            created_at text not null default '',
            updated_at text not null default '',
            primary key (batch_id, job_id)
        )
        """
    )
    conn.execute(
        """
        create table if not exists top_asset_cache_entries (
            asset_key text primary key,
            content_id text not null default '',
            platform text not null default '',
            source text not null default '',
            asset_dir text not null default '',
            size_bytes integer not null default 0,
            last_used_batch_id text not null default '',
            ref_count integer not null default 0,
            created_at text not null default '',
            updated_at text not null default ''
        )
        """
    )
    conn.execute(
        """
        create table if not exists top_asset_cache_refs (
            batch_id text not null default '',
            job_id text not null default '',
            content_identity_key text not null default '',
            asset_key text not null default '',
            used_at text not null default '',
            retained integer not null default 1,
            primary key (batch_id, job_id)
        )
        """
    )
    conn.execute(
        """
        create table if not exists recap_settings (
            setting_key text primary key,
            setting_value text not null default '',
            updated_at text not null default ''
        )
        """
    )
    _ensure_table_columns(
        conn,
        "multimodal_recap_items",
        pd.DataFrame(columns=["batch_id", *MULTIMODAL_RECAP_COLUMNS]),
    )
    _ensure_table_columns(
        conn,
        "type_recap_items",
        pd.DataFrame(columns=TYPE_RECAP_COLUMNS),
    )
    _ensure_table_columns(
        conn,
        "top_asset_cache_entries",
        pd.DataFrame(columns=TOP_ASSET_CACHE_ENTRY_COLUMNS),
    )
    _ensure_table_columns(
        conn,
        "top_asset_cache_refs",
        pd.DataFrame(columns=TOP_ASSET_CACHE_REF_COLUMNS),
    )
    _migrate_harvester_tables_to_batch_scoped_primary_key(conn)
    _ensure_table_columns(
        conn,
        "harvester_asset_jobs",
        pd.DataFrame(
            columns=[
                "job_id",
                "batch_id",
                "status",
                "platform",
                "channel",
                "content_identity_key",
                "content_id",
                "content_url",
                "title",
                "account",
                "period_start",
                "period_end",
                "metrics_json",
                "harvester_root",
                "jobs_path",
                "manifest_path",
                "error_message",
                "created_at",
                "updated_at",
            ]
        ),
    )
    _ensure_table_columns(
        conn,
        "harvester_asset_manifests",
        pd.DataFrame(
            columns=[
                "job_id",
                "batch_id",
                "status",
                "platform",
                "asset_key",
                "asset_dir",
                "cover_path",
                "video_path",
                "screenshots_json",
                "frames_json",
                "metadata_json",
                "error_message",
                "created_at",
                "updated_at",
            ]
        ),
    )
    _normalize_platform_type_tables(conn)


def _migrate_harvester_tables_to_batch_scoped_primary_key(conn: sqlite3.Connection) -> None:
    _migrate_harvester_table_to_batch_scoped_primary_key(
        conn,
        "harvester_asset_jobs",
        [
            "job_id",
            "batch_id",
            "status",
            "platform",
            "channel",
            "content_identity_key",
            "content_id",
            "content_url",
            "title",
            "account",
            "period_start",
            "period_end",
            "metrics_json",
            "harvester_root",
            "jobs_path",
            "manifest_path",
            "error_message",
            "created_at",
            "updated_at",
        ],
    )
    _migrate_harvester_table_to_batch_scoped_primary_key(
        conn,
        "harvester_asset_manifests",
        [
            "job_id",
            "batch_id",
            "status",
            "platform",
            "asset_key",
            "asset_dir",
            "cover_path",
            "video_path",
            "screenshots_json",
            "frames_json",
            "metadata_json",
            "error_message",
            "created_at",
            "updated_at",
        ],
    )


def _migrate_harvester_table_to_batch_scoped_primary_key(
    conn: sqlite3.Connection,
    table_name: str,
    columns: list[str],
) -> None:
    row = conn.execute(
        "select sql from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).fetchone()
    create_sql = str(row[0] if row else "")
    if "primary key (batch_id, job_id)" in create_sql.lower():
        return
    backup_name = f"{table_name}__legacy_job_pk"
    conn.execute(f'alter table "{_sqlite_identifier(table_name)}" rename to "{_sqlite_identifier(backup_name)}"')
    if table_name == "harvester_asset_jobs":
        conn.execute(
            """
            create table harvester_asset_jobs (
                job_id text not null default '',
                batch_id text not null default '',
                status text not null default '',
                platform text not null default '',
                channel text not null default '',
                content_identity_key text not null default '',
                content_id text not null default '',
                content_url text not null default '',
                title text not null default '',
                account text not null default '',
                period_start text not null default '',
                period_end text not null default '',
                metrics_json text not null default '{}',
                harvester_root text not null default '',
                jobs_path text not null default '',
                manifest_path text not null default '',
                error_message text not null default '',
                created_at text not null default '',
                updated_at text not null default '',
                primary key (batch_id, job_id)
            )
            """
        )
    else:
        conn.execute(
            """
            create table harvester_asset_manifests (
                job_id text not null default '',
                batch_id text not null default '',
                status text not null default '',
                platform text not null default '',
                asset_key text not null default '',
                asset_dir text not null default '',
                cover_path text not null default '',
                video_path text not null default '',
                screenshots_json text not null default '[]',
                frames_json text not null default '[]',
                metadata_json text not null default '{}',
                error_message text not null default '',
                created_at text not null default '',
                updated_at text not null default '',
                primary key (batch_id, job_id)
            )
            """
        )
    existing = {
        item[1]
        for item in conn.execute(f'pragma table_info("{_sqlite_identifier(backup_name)}")').fetchall()
    }
    selected = [column for column in columns if column in existing]
    if selected:
        column_sql = ", ".join(f'"{_sqlite_identifier(column)}"' for column in selected)
        conn.execute(
            f'insert or replace into "{_sqlite_identifier(table_name)}" ({column_sql}) '
            f'select {column_sql} from "{_sqlite_identifier(backup_name)}"'
        )
    conn.execute(f'drop table "{_sqlite_identifier(backup_name)}"')


def _persist_feishu_ledger_snapshot_with_conn(
    conn: sqlite3.Connection,
    batch_id: str,
    snapshot: dict[str, object],
) -> str:
    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = hashlib.sha1(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        + str(batch_id).encode("utf-8")
    ).hexdigest()
    conn.execute(
        """
        insert or replace into feishu_ledger_snapshots (
            snapshot_id, batch_id, enabled, fetched_at, total_rows,
            platform_counts_json, sheet_row_counts_json, field_completeness_json,
            warnings_json, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            batch_id,
            1 if bool(snapshot.get("enabled")) else 0,
            _clean_text(snapshot.get("fetched_at")),
            int(snapshot.get("total_rows") or 0),
            json.dumps(snapshot.get("platform_counts") or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(snapshot.get("sheet_row_counts") or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(snapshot.get("field_completeness") or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(snapshot.get("warnings") or [], ensure_ascii=False),
            created_at,
        ),
    )
    return snapshot_id


def _read_harvester_table(db_path: Path, table_name: str, columns: list[str], *, batch_id: str = "") -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame(columns=columns)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        sql = f'select * from "{_sqlite_identifier(table_name)}"'
        params: list[object] = []
        if batch_id:
            sql += " where batch_id = ?"
            params.append(batch_id)
        sql += " order by updated_at desc, job_id"
        try:
            frame = pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame[columns]


def _read_batch_table(db_path: Path, table_name: str, columns: list[str], *, batch_id: str = "") -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame(columns=columns)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        sql = f'select * from "{_sqlite_identifier(table_name)}"'
        params: list[object] = []
        if batch_id:
            sql += " where batch_id = ?"
            params.append(batch_id)
        try:
            frame = pd.read_sql_query(sql, conn, params=params)
        except Exception:
            return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame[columns]


def _normalize_platform_type_record(record: dict[str, object]) -> dict[str, object]:
    platform = _clean_text(record.get("platform"))
    if platform == "B站":
        bilibili_type = (
            _clean_text(record.get("bilibili_content_type"))
            or _clean_text(record.get("content_type"))
            or _clean_text(record.get("category_l2"))
            or _clean_text(record.get("category_l1"))
            or _clean_text(record.get("raw_content_type"))
        )
        record["category_l1"] = ""
        record["category_l2"] = ""
        record["bilibili_content_type"] = bilibili_type
        if "content_type" in record:
            record["content_type"] = bilibili_type
    elif platform:
        l1 = _clean_text(record.get("category_l1"))
        l2 = _clean_text(record.get("category_l2")) or _clean_text(record.get("content_type"))
        record["category_l1"] = l1
        record["category_l2"] = l2
        record["bilibili_content_type"] = ""
        if "content_type" in record:
            record["content_type"] = l2 or l1 or _clean_text(record.get("raw_content_type"))
    return record


def _normalize_platform_type_frame(frame: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame(columns=columns or [])
    normalized = frame.copy()
    if normalized.empty and columns:
        for column in columns:
            if column not in normalized.columns:
                normalized[column] = ""
        return normalized[columns]
    if "platform" not in normalized.columns:
        if columns:
            for column in columns:
                if column not in normalized.columns:
                    normalized[column] = ""
            return normalized[columns]
        return normalized
    for column in ["category_l1", "category_l2", "bilibili_content_type", "content_type", "raw_content_type"]:
        if column not in normalized.columns:
            normalized[column] = ""
    for index, row in normalized.iterrows():
        record = {column: row.get(column, "") for column in normalized.columns}
        _normalize_platform_type_record(record)
        for column in ["category_l1", "category_l2", "bilibili_content_type", "content_type"]:
            if column in normalized.columns:
                normalized.at[index, column] = record.get(column, "")
    if columns:
        for column in columns:
            if column not in normalized.columns:
                normalized[column] = ""
        return normalized[columns]
    return normalized


def _coerce_content_performance_numbers(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    coerced = frame.copy()
    for column in [
        "spend",
        "impressions",
        "clicks",
        "activations",
        "first_pay_count",
        "ctr",
        "activation_cost",
        "first_pay_cost",
        "first_pay_rate",
        "match_confidence",
        "merged_row_count",
        "value",
        "share",
    ]:
        if column in coerced.columns:
            coerced[column] = pd.to_numeric(coerced[column], errors="coerce").fillna(0.0)
    return coerced


def _normalize_performance_title_tags(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "title" not in frame.columns:
        return frame
    normalized = frame.copy()
    if "tags" not in normalized.columns:
        normalized["tags"] = ""
    for index, row in normalized.iterrows():
        clean_title, extracted_tags = _split_title_and_tags(row.get("title"))
        existing_tags = _clean_text(row.get("tags"))
        normalized.at[index, "title"] = clean_title
        normalized.at[index, "tags"] = existing_tags or extracted_tags
    return normalized


def _backfill_performance_fields_with_conn(conn: sqlite3.Connection, frame: pd.DataFrame, *, batch_id: str = "") -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    filled = frame.copy()
    for column in [
        "asset_key",
        "content_identity_key",
        "platform",
        "content_id",
        "content_url",
        "title",
        "account",
        "tags",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "content_type",
    ]:
        if column not in filled.columns:
            filled[column] = ""
    asset_rows = _load_content_asset_backfill_rows(conn)
    match_by_identity, match_by_platform_id = _load_asset_match_backfill_rows(conn, batch_id=batch_id)
    for index, row in filled.iterrows():
        asset = asset_rows.get(_clean_text(row.get("asset_key")), {})
        match = match_by_identity.get(_clean_text(row.get("content_identity_key")), {})
        if not match:
            match = match_by_platform_id.get(_platform_id_backfill_key(row), {})
        if not asset and not match:
            if _is_identity_placeholder_title(row, _clean_text(row.get("title"))):
                filled.at[index, "title"] = ""
            continue
        platform = _clean_text(row.get("platform")) or _clean_text(asset.get("platform")) or _clean_text(match.get("platform"))
        candidate_title = _first_displayable_title(row, [asset.get("title"), match.get("title")])
        current_title = _clean_text(row.get("title"))
        if candidate_title and _is_identity_placeholder_title(row, current_title):
            filled.at[index, "title"] = candidate_title
        elif not candidate_title and _is_identity_placeholder_title(row, current_title):
            filled.at[index, "title"] = ""
        if _clean_text(asset.get("account")) and not _clean_text(row.get("account")):
            filled.at[index, "account"] = _clean_text(asset.get("account"))
        for column in ["tags", "category_l1", "category_l2", "content_type"]:
            candidate = _clean_text(asset.get(column)) or _clean_text(match.get(column))
            if candidate and not _clean_text(row.get(column)):
                filled.at[index, column] = candidate
        bilibili_type = _clean_text(asset.get("bilibili_content_type")) or _clean_text(match.get("bilibili_content_type"))
        if bilibili_type and not _clean_text(row.get("bilibili_content_type")):
            filled.at[index, "bilibili_content_type"] = bilibili_type
        content_url = _clean_text(asset.get("content_url")) or _clean_text(match.get("content_url"))
        if content_url and not _clean_text(row.get("content_url")):
            filled.at[index, "content_url"] = content_url
        if platform and not _clean_text(row.get("platform")):
            filled.at[index, "platform"] = platform
    return filled


def _load_content_asset_backfill_rows(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    try:
        assets = pd.read_sql_query(
            """
            select asset_key, platform, content_id, content_url, title, account, tags,
                   category_l1, category_l2, bilibili_content_type, content_type
            from content_assets
            """,
            conn,
        )
    except Exception:
        return {}
    rows: dict[str, dict[str, str]] = {}
    for _, row in assets.iterrows():
        asset_key = _clean_text(row.get("asset_key"))
        if not asset_key:
            continue
        rows[asset_key] = {
            "platform": _clean_text(row.get("platform")),
            "content_id": _clean_text(row.get("content_id")),
            "content_url": _clean_text(row.get("content_url")),
            "title": _clean_text(row.get("title")),
            "account": _clean_text(row.get("account")),
            "tags": _clean_text(row.get("tags")),
            "category_l1": _clean_text(row.get("category_l1")),
            "category_l2": _clean_text(row.get("category_l2")),
            "bilibili_content_type": _clean_text(row.get("bilibili_content_type")),
            "content_type": _clean_text(row.get("content_type")),
        }
    return rows


def _load_asset_match_backfill_rows(
    conn: sqlite3.Connection,
    *,
    batch_id: str = "",
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    try:
        existing = {
            row[1]
            for row in conn.execute('pragma table_info("asset_match_results")').fetchall()
        }
    except Exception:
        return {}, {}
    if not existing:
        return {}, {}
    select_columns = [
        "content_identity_key",
        "platform",
        "content_id",
        "content_url",
        "title",
        "matched_ledger_title",
        "matched_category_l1",
        "matched_category_l2",
        "matched_bilibili_content_type",
        "matched_content_type",
        "matched_account",
    ]
    expressions = [
        column if column in existing else f"'' as {column}"
        for column in select_columns
    ]
    sql = f"select {', '.join(expressions)} from asset_match_results where match_status = '已匹配'"
    params: list[object] = []
    if batch_id:
        sql += " and batch_id = ?"
        params.append(batch_id)
    try:
        matches = pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return {}, {}
    by_identity: dict[str, dict[str, str]] = {}
    by_platform_id: dict[str, dict[str, str]] = {}
    for _, row in matches.iterrows():
        identity = _clean_text(row.get("content_identity_key"))
        record = {
            "platform": _clean_text(row.get("platform")),
            "content_id": _clean_text(row.get("content_id")),
            "content_url": _clean_text(row.get("content_url")),
            "title": _clean_text(row.get("matched_ledger_title")) or _clean_text(row.get("title")),
            "account": _clean_text(row.get("matched_account")),
            "tags": "",
            "category_l1": _clean_text(row.get("matched_category_l1")),
            "category_l2": _clean_text(row.get("matched_category_l2")),
            "bilibili_content_type": _clean_text(row.get("matched_bilibili_content_type")),
            "content_type": _clean_text(row.get("matched_content_type")) or _clean_text(row.get("matched_category_l2")),
        }
        if identity:
            by_identity[identity] = record
        platform_id_key = _platform_id_backfill_key(row)
        if platform_id_key:
            by_platform_id[platform_id_key] = record
    return by_identity, by_platform_id


def _platform_id_backfill_key(row: object) -> str:
    platform = _clean_text(row.get("platform") if hasattr(row, "get") else "")
    content_id = _clean_text(row.get("content_id") if hasattr(row, "get") else "")
    return f"{platform}::id::{content_id}" if platform and content_id else ""


def _first_displayable_title(row: pd.Series, values: list[object]) -> str:
    for value in values:
        title = _clean_text(value)
        if title and not _is_undisplayable_title(row, title):
            return title
    return ""


def _split_title_and_tags(value: object) -> tuple[str, str]:
    text = _clean_text(value)
    if not text:
        return "", ""
    tags = re.findall(r"[#＃][^#＃\s]+", text)
    title = re.sub(r"[#＃][^#＃\s]+", "", text)
    title = " ".join(title.split()).strip()
    return title, " ".join(dict.fromkeys(tags))


def _normalize_platform_type_tables(conn: sqlite3.Connection) -> None:
    table_columns = {
        "content_assets": CONTENT_ASSET_COLUMNS,
        "content_performance_items": CONTENT_PERFORMANCE_COLUMNS,
        "multimodal_recap_items": ["batch_id", *MULTIMODAL_RECAP_COLUMNS],
    }
    for table_name, columns in table_columns.items():
        exists = conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?",
            (table_name,),
        ).fetchone()
        if not exists:
            continue
        existing = {
            row[1]
            for row in conn.execute(f'pragma table_info("{_sqlite_identifier(table_name)}")').fetchall()
        }
        required = {"platform", "category_l1", "category_l2", "bilibili_content_type"}
        if not required.issubset(existing):
            continue
        _ensure_table_columns(conn, table_name, pd.DataFrame(columns=columns))
        existing = {
            row[1]
            for row in conn.execute(f'pragma table_info("{_sqlite_identifier(table_name)}")').fetchall()
        }
        content_type_expr = "coalesce(content_type, '')" if "content_type" in existing else "''"
        raw_type_expr = "coalesce(raw_content_type, '')" if "raw_content_type" in existing else "''"
        set_content_type = (
            f", content_type = coalesce(nullif(bilibili_content_type, ''), nullif(content_type, ''), "
            f"nullif(category_l2, ''), nullif(category_l1, ''), nullif({raw_type_expr}, ''), '')"
            if "content_type" in existing
            else ""
        )
        conn.execute(
            f"""
            update "{_sqlite_identifier(table_name)}"
            set
                bilibili_content_type = coalesce(nullif(bilibili_content_type, ''), nullif({content_type_expr}, ''), nullif(category_l2, ''), nullif(category_l1, ''), nullif({raw_type_expr}, ''), ''),
                category_l1 = '',
                category_l2 = ''
                {set_content_type}
            where platform = 'B站'
            """
        )
        if "content_type" in existing:
            conn.execute(
                f"""
                update "{_sqlite_identifier(table_name)}"
                set
                    category_l2 = coalesce(nullif(category_l2, ''), nullif(content_type, ''), nullif({raw_type_expr}, ''), ''),
                    bilibili_content_type = '',
                    content_type = coalesce(nullif(category_l2, ''), nullif(content_type, ''), nullif(category_l1, ''), nullif({raw_type_expr}, ''), '')
                where platform <> 'B站'
                """
            )
        else:
            conn.execute(
                f"""
                update "{_sqlite_identifier(table_name)}"
                set bilibili_content_type = ''
                where platform <> 'B站'
                """
            )


def _content_asset_key(row: pd.Series) -> str:
    platform = _clean_text(row.get("platform"))
    content_id = _clean_text(row.get("content_id"))
    content_url = _clean_text(row.get("content_url"))
    title_key = _clean_text(row.get("title_key")) or normalized_title_key(_clean_text(row.get("title")))
    account = _clean_text(row.get("account"))
    if content_id:
        return f"{platform}::id::{content_id}"
    if content_url:
        return f"{platform}::url::{content_url}"
    if title_key:
        return f"{platform}::title_account::{account}::{title_key}"
    return ""


def _content_asset_record(asset_key: str, batch_id: str, row: pd.Series, now: str) -> dict[str, object]:
    published_date = _standard_date_text(row.get("published_date"))
    record = {
        "asset_key": asset_key,
        "platform": _clean_text(row.get("platform")),
        "content_id": _clean_text(row.get("content_id")),
        "content_url": _clean_text(row.get("content_url")),
        "title": _clean_text(row.get("title")),
        "account": _clean_text(row.get("account")),
        "tags": _clean_text(row.get("tags")),
        "raw_content_type": _clean_text(row.get("raw_content_type")),
        "category_l1": _clean_text(row.get("category_l1")),
        "category_l2": _clean_text(row.get("category_l2")),
        "bilibili_content_type": _clean_text(row.get("bilibili_content_type")),
        "content_type": _clean_text(row.get("content_type")),
        "content_type_review": _clean_text(row.get("content_type_review")),
        "filter_status": _clean_text(row.get("filter_status")),
        "published_date": published_date,
        "source_file": _clean_text(row.get("source_file")),
        "source_sheet": _clean_text(row.get("source_sheet")),
        "source_row": int(_number(row.get("source_row"))),
        "title_key": _clean_text(row.get("title_key")) or normalized_title_key(_clean_text(row.get("title"))),
        "title_key_no_tags": _clean_text(row.get("title_key_no_tags")),
        "first_seen_batch_id": batch_id,
        "last_seen_batch_id": batch_id,
        "created_at": now,
        "updated_at": now,
    }
    _normalize_platform_type_record(record)
    return record


def _merge_non_blank(existing: dict[str, object], incoming: dict[str, object]) -> dict[str, object]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in {"asset_key", "first_seen_batch_id", "created_at"}:
            merged[key] = existing.get(key) or value
            continue
        if isinstance(value, (int, float)) and key == "source_row":
            if value:
                merged[key] = value
            continue
        text = _clean_text(value)
        if text:
            merged[key] = value
        elif key not in merged:
            merged[key] = value
    result = {column: merged.get(column, "") for column in CONTENT_ASSET_COLUMNS}
    _normalize_platform_type_record(result)
    return result


def _content_performance_frame(batch_id: str, canonical: pd.DataFrame) -> pd.DataFrame:
    if canonical is None or canonical.empty:
        return pd.DataFrame(columns=CONTENT_PERFORMANCE_COLUMNS)
    frame = canonical.copy()
    for column in [
        "period_start",
        "period_end",
        "platform",
        "channel",
        "content_identity_key",
        "content_id",
        "material_id",
        "content_url",
        "work_url",
        "title",
        "account",
        "matched_account",
        "metadata_tags",
        "tags",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "content_type",
        "content_category",
        "matched_category_l1",
        "matched_category_l2",
        "matched_bilibili_content_type",
        "matched_content_type",
        "match_status",
        "match_source",
        "match_key",
        "match_confidence",
        "match_reason",
        "source_file",
        "source_sheet",
        "source_row",
    ]:
        if column not in frame.columns:
            frame[column] = ""
    for column in ["spend", "impressions", "clicks", "activations", "first_pay_count", "ctr", "activation_cost", "first_pay_cost", "first_pay_rate"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["content_identity_key"] = frame.apply(_performance_identity_key, axis=1)
    grouped_rows: list[dict[str, object]] = []
    for _, group in frame.groupby(["period_start", "period_end", "channel", "content_identity_key"], dropna=False, sort=False):
        lead = group.sort_values("spend", ascending=False).iloc[0]
        spend = float(group["spend"].sum())
        impressions = float(group["impressions"].sum())
        clicks = float(group["clicks"].sum())
        activations = float(group["activations"].sum())
        first_pay = float(group["first_pay_count"].sum())
        display_title, extracted_tags = _split_title_and_tags(_performance_display_title(lead))
        display_tags = _clean_text(lead.get("metadata_tags")) or _clean_text(lead.get("tags")) or extracted_tags
        row = {
            "performance_key": _performance_key(batch_id, lead),
            "batch_id": batch_id,
            "period_start": _clean_text(lead.get("period_start")),
            "period_end": _clean_text(lead.get("period_end")),
            "platform": _clean_text(lead.get("platform")),
            "channel": _clean_text(lead.get("channel")),
            "content_identity_key": _clean_text(lead.get("content_identity_key")),
            "asset_key": _performance_asset_key(lead),
            "content_id": _clean_text(lead.get("content_id")),
            "material_id": _clean_text(lead.get("material_id")),
            "content_url": _clean_text(lead.get("content_url")) or _clean_text(lead.get("work_url")),
            "title": display_title,
            "account": _clean_text(lead.get("account")),
            "tags": display_tags,
            "category_l1": _clean_text(lead.get("matched_category_l1")) or _clean_text(lead.get("category_l1")),
            "category_l2": _clean_text(lead.get("matched_category_l2")) or _clean_text(lead.get("category_l2")) or _clean_text(lead.get("content_category")),
            "bilibili_content_type": _clean_text(lead.get("matched_bilibili_content_type")) or _clean_text(lead.get("bilibili_content_type")),
            "content_type": _clean_text(lead.get("matched_content_type")) or _clean_text(lead.get("content_type")) or _clean_text(lead.get("content_category")),
            "match_status": _clean_text(lead.get("match_status")),
            "match_source": _clean_text(lead.get("match_source")),
            "match_key": _clean_text(lead.get("match_key")),
            "match_confidence": float(_number(lead.get("match_confidence"))),
            "match_reason": _clean_text(lead.get("match_reason")),
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
            "activations": activations,
            "first_pay_count": first_pay,
            "ctr": clicks / impressions if impressions else 0.0,
            "activation_cost": spend / activations if activations else 0.0,
            "first_pay_cost": spend / first_pay if first_pay else 0.0,
            "first_pay_rate": first_pay / activations if activations else 0.0,
            "merged_row_count": int(len(group)),
            "source_rows_json": _source_rows_json(group),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _normalize_platform_type_record(row)
        grouped_rows.append(row)
    if not grouped_rows:
        return pd.DataFrame(columns=CONTENT_PERFORMANCE_COLUMNS)
    return pd.DataFrame(grouped_rows, columns=CONTENT_PERFORMANCE_COLUMNS)


def _period_totals_from_summary(batch_id: str, canonical: pd.DataFrame, total_summary: pd.DataFrame) -> pd.DataFrame:
    if total_summary is None or total_summary.empty:
        return pd.DataFrame(columns=PERIOD_CHANNEL_TOTAL_COLUMNS)
    period_start = _first_non_blank_column(canonical, "period_start")
    period_end = _first_non_blank_column(canonical, "period_end")
    rows: list[dict[str, object]] = []
    for _, source in total_summary.iterrows():
        channel = _clean_text(source.get("channel"))
        if not channel:
            continue
        spend = _number(source.get("spend"))
        activations = _number(source.get("activations"))
        first_pay = _number(source.get("first_pay_count"))
        raw = "|".join([batch_id, period_start, period_end, channel, "summary"])
        rows.append(
            {
                "period_total_key": hashlib.sha1(raw.encode("utf-8")).hexdigest(),
                "period_start": period_start,
                "period_end": period_end,
                "channel": channel,
                "platform": _clean_text(source.get("platform")),
                "source_file": "summary",
                "source_sheet": "",
                "source_row": 0,
                "spend": spend,
                "impressions": _number(source.get("impressions")),
                "clicks": _number(source.get("clicks")),
                "activations": activations,
                "first_pay_count": first_pay,
                "activation_cost": spend / activations if activations else _number(source.get("activation_cost")),
                "first_pay_cost": spend / first_pay if first_pay else _number(source.get("first_pay_cost")),
                "is_channel_total": True,
            }
        )
    return pd.DataFrame(rows, columns=PERIOD_CHANNEL_TOTAL_COLUMNS)


def _combine_period_totals(explicit_totals: pd.DataFrame, summary_totals: pd.DataFrame) -> pd.DataFrame:
    frames = [
        frame.copy()
        for frame in [explicit_totals, summary_totals]
        if frame is not None and not frame.empty
    ]
    if not frames:
        return pd.DataFrame(columns=PERIOD_CHANNEL_TOTAL_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    for column in PERIOD_CHANNEL_TOTAL_COLUMNS:
        if column not in combined.columns:
            combined[column] = ""
    combined["_priority"] = combined["source_file"].astype(str).map(lambda value: 1 if value == "summary" else 0)
    combined = (
        combined.sort_values(["period_start", "period_end", "channel", "_priority"], ascending=[True, True, True, True])
        .drop_duplicates(subset=["period_start", "period_end", "channel"], keep="first")
        .drop(columns=["_priority"], errors="ignore")
    )
    return combined[PERIOD_CHANNEL_TOTAL_COLUMNS].reset_index(drop=True)


def _first_non_blank_column(frame: pd.DataFrame, column: str) -> str:
    if frame is None or frame.empty or column not in frame.columns:
        return ""
    for value in frame[column].tolist():
        text = _clean_text(value)
        if text:
            return text
    return ""


def _asset_match_result_frame(canonical: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "period_start",
        "period_end",
        "platform",
        "channel",
        "content_identity_key",
        "content_id",
        "material_id",
        "title",
        "matched_ledger_title",
        "content_url",
        "match_status",
        "match_source",
        "match_key",
        "match_confidence",
        "match_reason",
        "matched_category_l1",
        "matched_category_l2",
        "matched_bilibili_content_type",
        "matched_content_type",
        "matched_account",
    ]
    if canonical is None or canonical.empty:
        return pd.DataFrame(columns=columns)
    frame = canonical.copy()
    for column in columns:
        if column not in frame.columns:
            frame[column] = "" if column != "match_confidence" else 0.0
    return frame[columns].copy()


def _performance_identity_key(row: pd.Series) -> str:
    current = _clean_text(row.get("content_identity_key"))
    if current:
        return current
    raw = "|".join(_clean_text(row.get(column)) for column in ["channel", "content_id", "material_id", "title", "content_url"])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _performance_asset_key(row: pd.Series) -> str:
    platform = _clean_text(row.get("platform"))
    content_id = _clean_text(row.get("content_id"))
    content_url = _clean_text(row.get("content_url")) or _clean_text(row.get("work_url"))
    title_key = normalized_title_key(_clean_text(row.get("title")))
    account = _clean_text(row.get("account"))
    if content_id:
        return f"{platform}::id::{content_id}"
    if content_url:
        return f"{platform}::url::{content_url}"
    if title_key:
        return f"{platform}::title_account::{account}::{title_key}"
    return _clean_text(row.get("content_identity_key"))


def _performance_display_title(row: pd.Series) -> str:
    title = _clean_text(row.get("title"))
    matched_title = _clean_text(row.get("matched_ledger_title"))
    if matched_title and _is_identity_placeholder_title(row, title):
        return matched_title
    return title or matched_title


def _is_identity_placeholder_title(row: pd.Series, title: str) -> bool:
    if _is_undisplayable_title(row, title):
        return True
    if not title:
        return True
    identity_values = {
        _clean_text(row.get(column))
        for column in ["content_id", "material_id", "work_id", "match_key"]
        if _clean_text(row.get(column))
    }
    if title in identity_values:
        return True
    platform = _clean_text(row.get("platform"))
    if platform == "B站":
        bvid = extract_bvid(title)
        return bool(bvid and bvid == title)
    if _looks_like_content_url_title(platform, title):
        return True
    return False


def _is_undisplayable_title(row: pd.Series, title: str) -> bool:
    text = _clean_text(title)
    if not text:
        return True
    if text in {"/", "-", "--", "无", "未知", "nan", "None", "null"}:
        return True
    platform = _clean_text(row.get("platform")) if hasattr(row, "get") else ""
    return _looks_like_content_url_title(platform, text)


def _looks_like_content_url_title(platform: str, title: str) -> bool:
    lowered = title.lower()
    if not re.match(r"^https?://", lowered):
        return False
    if platform == "小红书":
        return "xiaohongshu.com" in lowered or "xhslink.com" in lowered
    if platform == "抖音":
        return "douyin.com" in lowered or "iesdouyin.com" in lowered
    if platform == "B站":
        return "bilibili.com" in lowered or "b23.tv" in lowered
    return True


def _performance_key(batch_id: str, row: pd.Series) -> str:
    raw = "|".join(
        [
            batch_id,
            _clean_text(row.get("period_start")),
            _clean_text(row.get("period_end")),
            _clean_text(row.get("channel")),
            _clean_text(row.get("content_identity_key")),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _source_rows_json(group: pd.DataFrame) -> str:
    rows: list[dict[str, object]] = []
    for _, row in group.iterrows():
        rows.append(
            {
                "source_file": _clean_text(row.get("source_file")),
                "source_sheet": _clean_text(row.get("source_sheet")),
                "source_row": int(_number(row.get("source_row"))),
            }
        )
    return json.dumps(rows, ensure_ascii=False, sort_keys=True)


def _standard_date_text(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return text
    return parsed.strftime("%Y-%m-%d")


def _number(value: object) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _sqlite_identifier(value: str) -> str:
    return value.replace('"', '""')


def _sqlite_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "integer"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_numeric_dtype(series):
        return "real"
    return "text"


def _file_record(path: Path, source_file: str) -> ArchivedFile:
    return ArchivedFile(
        source_file=source_file,
        archive_path=path,
        sha256=_sha256(path),
        size_bytes=path.stat().st_size,
    )


def _mapping_keys(mapping: dict[str, str]) -> list[str]:
    keys = []
    for column in ["content_id", "material_id", "title"]:
        value = mapping.get(column, "").strip()
        if value:
            keys.append(f"{column}:{value}")
    title_key = mapping.get("title_key", "").strip() or normalized_title_key(mapping.get("title", ""))
    if title_key:
        keys.append(f"title_key:{title_key}")
    return keys


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
