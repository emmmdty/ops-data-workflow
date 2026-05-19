"""Synchronize period raw folders into archived workflow batches."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import hashlib
import sqlite3
from typing import Callable, Optional

import pandas as pd

from .pipeline import TABULAR_SUFFIXES
from .reference_tables import parse_period_from_raw_dir
from .storage import is_period_active
from .workflow import run_archived_workflow


@dataclass(frozen=True)
class RawPeriod:
    name: str
    path: Path
    period_start: str
    period_end: str


@dataclass(frozen=True)
class RawSyncResult:
    period_name: str
    period_start: str
    period_end: str
    status: str
    batch_id: str = ""
    message: str = ""


FileSignature = tuple[str, str, int]


def discover_raw_periods(raw_root: Path) -> list[RawPeriod]:
    raw_root = Path(raw_root)
    if not raw_root.exists():
        return []

    periods: list[RawPeriod] = []
    for child in sorted(raw_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name == "uploaded_originals":
            continue
        try:
            period_start, period_end = parse_period_from_raw_dir(child)
        except ValueError:
            continue
        if not _raw_tabular_files(child):
            continue
        periods.append(
            RawPeriod(
                name=child.name,
                path=child,
                period_start=period_start,
                period_end=period_end,
            )
        )
    return periods


def sync_raw_periods(
    raw_root: Path,
    *,
    db_path: Path,
    output_root: Path,
    archive_root: Path,
    category_rules_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    category_matcher: Optional[Callable] = None,
) -> list[RawSyncResult]:
    results: list[RawSyncResult] = []
    for period in discover_raw_periods(raw_root):
        try:
            if not is_period_active(db_path, period.period_start, period.period_end):
                results.append(
                    RawSyncResult(
                        period.name,
                        period.period_start,
                        period.period_end,
                        "skipped",
                        "",
                        "周期已备份或删除",
                    )
                )
                continue
            current_signature = _raw_signature(period.path)
            latest_batch_id = _latest_successful_batch_for_period(db_path, period.period_start, period.period_end)
            if latest_batch_id and current_signature == _stored_signature(db_path, latest_batch_id):
                missing_channel = _missing_bilibili_channel(db_path, latest_batch_id, period.path)
                if not missing_channel:
                    results.append(
                        RawSyncResult(
                            period.name,
                            period.period_start,
                            period.period_end,
                            "skipped",
                            latest_batch_id,
                            "raw 文件未变化",
                        )
                    )
                    continue

            workflow_result = run_archived_workflow(
                period.path,
                period.period_start,
                period.period_end,
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                category_rules_path=category_rules_path,
                env_path=env_path,
                category_matcher=category_matcher,
            )
            results.append(
                RawSyncResult(
                    period.name,
                    period.period_start,
                    period.period_end,
                    "generated",
                    workflow_result.batch_id,
                    "已根据 raw 文件生成最新批次",
                )
            )
        except Exception as exc:
            results.append(
                RawSyncResult(
                    period.name,
                    period.period_start,
                    period.period_end,
                    "error",
                    "",
                    str(exc),
                )
            )
    return results


def _raw_tabular_files(period_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in Path(period_dir).rglob("*")
        if path.is_file() and path.suffix.lower() in TABULAR_SUFFIXES and not path.name.startswith("~$")
    )


def _raw_signature(period_dir: Path) -> list[FileSignature]:
    return [
        (path.relative_to(period_dir).as_posix(), _sha256(path), path.stat().st_size)
        for path in _raw_tabular_files(period_dir)
    ]


def _stored_signature(db_path: Path, batch_id: str) -> list[FileSignature]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            rows = conn.execute(
                """
                select source_file, sha256, size_bytes
                from uploaded_files
                where batch_id = ?
                order by source_file
                """,
                (batch_id,),
            ).fetchall()
        except Exception:
            return []
    return sorted(
        (str(source_file), str(sha256), int(size_bytes))
        for source_file, sha256, size_bytes in rows
        if Path(str(source_file)).suffix.lower() in TABULAR_SUFFIXES and not Path(str(source_file)).name.startswith("~$")
    )


def _latest_successful_batch_for_period(db_path: Path, period_start: str, period_end: str) -> str:
    db_path = Path(db_path)
    if not db_path.exists():
        return ""
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute(
                """
                select batch_id
                from upload_batches
                where status = 'ok'
                    and period_start = ?
                    and period_end = ?
                order by created_at desc
                limit 1
                """,
                (period_start, period_end),
            ).fetchone()
        except Exception:
            return ""
    return str(row[0]) if row else ""


def _missing_bilibili_channel(db_path: Path, batch_id: str, period_dir: Path) -> bool:
    has_bilibili_file = any("B站" in path.name for path in _raw_tabular_files(period_dir))
    if not has_bilibili_file:
        return False
    db_path = Path(db_path)
    if not db_path.exists():
        return True
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            channel_count = pd.read_sql_query(
                """
                select count(*) as count
                from canonical_items
                where batch_id = ?
                    and channel = 'B站'
                """,
                conn,
                params=(batch_id,),
            )["count"].iloc[0]
        except Exception:
            return True
    return int(channel_count) == 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
