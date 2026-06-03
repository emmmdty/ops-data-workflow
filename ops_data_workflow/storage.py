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

from .title_matching import normalized_title_key
from .periods import (
    PERIOD_LEVEL_MONTH,
    PERIOD_LEVEL_WEEK,
    SOURCE_TYPE_UPLOAD,
    ReviewPeriod,
    period_metadata_from_dates,
)


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
    "douyin_id_bridge",
    "period_file_states",
    "file_backups",
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
    "review_queue_items",
    "preprocessing_report_items",
    "duplicate_merge_items",
    "conflict_retention_items",
    "missing_value_items",
    "channel_comparison_items",
    "topic_label_items",
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
            create table if not exists douyin_id_bridge (
                bridge_key text not null,
                id_type text not null,
                id_value text not null,
                account text not null default '',
                content_type text not null default '',
                content_url text not null default '',
                title text not null default '',
                title_key_no_tags text not null default '',
                source_file text not null default '',
                source_sheet text not null default '',
                source_row text not null default '',
                match_source text not null default '',
                batch_id text not null default '',
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

    if level == PERIOD_LEVEL_WEEK:
        same_month = scoped[
            scoped["_start_dt"].dt.year.eq(start.year)
            & scoped["_start_dt"].dt.month.eq(start.month)
        ].sort_values(["_start_dt", "created_at"], ascending=[False, False])
        if not same_month.empty:
            return str(same_month.iloc[0]["batch_id"])

        previous_month_start = (start.replace(day=1) - pd.Timedelta(days=1)).replace(day=1)
        previous_month = scoped[
            scoped["_start_dt"].dt.year.eq(previous_month_start.year)
            & scoped["_start_dt"].dt.month.eq(previous_month_start.month)
        ].sort_values(["_start_dt", "created_at"], ascending=[True, False])
        return str(previous_month.iloc[2]["batch_id"]) if len(previous_month) >= 3 else ""

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


DOUYIN_ID_BRIDGE_COLUMNS = [
    "bridge_key",
    "id_type",
    "id_value",
    "account",
    "content_type",
    "content_url",
    "title",
    "title_key_no_tags",
    "source_file",
    "source_sheet",
    "source_row",
    "match_source",
    "batch_id",
    "updated_at",
]


def load_douyin_id_bridge(db_path: Path) -> pd.DataFrame:
    db_path = Path(db_path)
    if not db_path.exists():
        return pd.DataFrame(columns=DOUYIN_ID_BRIDGE_COLUMNS)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                "select * from douyin_id_bridge order by updated_at asc",
                conn,
            )
        except Exception:
            return pd.DataFrame(columns=DOUYIN_ID_BRIDGE_COLUMNS)


def persist_douyin_id_bridge(db_path: Path, batch_id: str, canonical: pd.DataFrame) -> int:
    if canonical.empty:
        return 0
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    with closing(sqlite3.connect(db_path)) as conn:
        for row in _douyin_bridge_rows(batch_id, canonical, now):
            existing = conn.execute(
                """
                select rowid from douyin_id_bridge
                where bridge_key = ?
                    and account = ?
                    and content_type = ?
                    and content_url = ?
                    and source_file = ?
                    and source_sheet = ?
                    and source_row = ?
                limit 1
                """,
                (
                    row["bridge_key"],
                    row["account"],
                    row["content_type"],
                    row["content_url"],
                    row["source_file"],
                    row["source_sheet"],
                    row["source_row"],
                ),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    update douyin_id_bridge
                    set title = ?, title_key_no_tags = ?, match_source = ?, batch_id = ?, updated_at = ?
                    where rowid = ?
                    """,
                    (
                        row["title"],
                        row["title_key_no_tags"],
                        row["match_source"],
                        row["batch_id"],
                        row["updated_at"],
                        existing[0],
                    ),
                )
            else:
                conn.execute(
                    """
                    insert into douyin_id_bridge (
                        bridge_key, id_type, id_value, account, content_type, content_url,
                        title, title_key_no_tags, source_file, source_sheet, source_row,
                        match_source, batch_id, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(row[column] for column in DOUYIN_ID_BRIDGE_COLUMNS),
                )
            written += 1
        conn.commit()
    return written


def _douyin_bridge_rows(batch_id: str, canonical: pd.DataFrame, updated_at: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for _, item in canonical.iterrows():
        if not _is_douyin_item(item) or not _is_safe_douyin_bridge_source(item):
            continue
        account = _clean_text(item.get("account", ""))
        content_type = _clean_text(item.get("ledger_content_type", "")) or _clean_text(item.get("manual_category", ""))
        if not account or not content_type:
            continue
        base = {
            "account": account,
            "content_type": content_type,
            "content_url": _clean_text(item.get("content_url", "")),
            "title": _clean_text(item.get("title", "")),
            "title_key_no_tags": normalized_title_key(item.get("title", "")),
            "source_file": _clean_text(item.get("ledger_source_file", "")) or _clean_text(item.get("source_file", "")),
            "source_sheet": _clean_text(item.get("ledger_source_sheet", "")) or _clean_text(item.get("source_sheet", "")),
            "source_row": _clean_text(item.get("ledger_source_row", "")) or _clean_text(item.get("source_row", "")),
            "match_source": _clean_text(item.get("ledger_match_source", "")),
            "batch_id": _clean_text(batch_id),
            "updated_at": updated_at,
        }
        for id_type, id_value in _douyin_bridge_keys(item):
            row = {
                "bridge_key": f"{id_type}:{id_value}",
                "id_type": id_type,
                "id_value": id_value,
                **base,
            }
            rows.append(row)
    return rows


def _is_douyin_item(row: pd.Series) -> bool:
    text = " ".join(_clean_text(row.get(column, "")) for column in ["platform_group", "platform", "channel"])
    return "抖音" in text


def _is_safe_douyin_bridge_source(row: pd.Series) -> bool:
    return _clean_text(row.get("ledger_match_source", "")) in {"id", "账号+标题", "唯一标题"}


def _douyin_bridge_keys(row: pd.Series) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for column in ["content_id", "material_id"]:
        value = _clean_text(row.get(column, ""))
        if value and not value.startswith("row:") and (column, value) not in keys:
            keys.append((column, value))
    url_key = _douyin_url_key(row.get("content_url", ""))
    if url_key and ("content_url", url_key) not in keys:
        keys.append(("content_url", url_key))
    return keys


def _douyin_url_key(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    match = re.search(r"[?&]token=([^&#\s]+)", text)
    if match:
        return f"token:{match.group(1)}"
    return text


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
    review_queue: pd.DataFrame,
    preprocessing_report: pd.DataFrame,
    duplicate_merge_details: pd.DataFrame,
    conflict_retention_details: pd.DataFrame,
    missing_value_details: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    topic_label_items: Optional[pd.DataFrame],
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
        _append_frame(conn, "review_queue_items", batch_id, review_queue)
        _append_frame(conn, "preprocessing_report_items", batch_id, preprocessing_report)
        _append_frame(conn, "duplicate_merge_items", batch_id, duplicate_merge_details)
        _append_frame(conn, "conflict_retention_items", batch_id, conflict_retention_details)
        _append_frame(conn, "missing_value_items", batch_id, missing_value_details)
        _append_frame(conn, "channel_comparison_items", batch_id, channel_comparison)
        _append_frame(conn, "topic_label_items", batch_id, topic_label_items if topic_label_items is not None else pd.DataFrame())
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
        "review_queue_items",
        "preprocessing_report_items",
        "duplicate_merge_items",
        "conflict_retention_items",
        "missing_value_items",
        "channel_comparison_items",
        "topic_label_items",
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
        columns = {row[1] for row in conn.execute('pragma table_info("ai_reports")').fetchall()}
        if "report_type" in columns:
            conn.execute(
                "delete from ai_reports where batch_id = ? and coalesce(report_type, 'auto_summary') != 'manual_recap'",
                (batch_id,),
            )
        else:
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
    if frame.empty:
        return
    stored = frame.copy()
    stored.insert(0, "batch_id", batch_id)
    _ensure_table_columns(conn, table_name, stored)
    stored.to_sql(table_name, conn, if_exists="append", index=False)


def _ensure_table_columns(conn: sqlite3.Connection, table_name: str, frame: pd.DataFrame) -> None:
    exists = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).fetchone()
    if not exists:
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
