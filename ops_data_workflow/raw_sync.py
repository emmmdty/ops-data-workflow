"""Synchronize raw source folders into workflow batches."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import hashlib
import sqlite3
from typing import Callable, Optional

import pandas as pd

from .pipeline import TABULAR_SUFFIXES
from .source_storage import discover_source_period_dirs
from .workflow import run_archived_workflow


@dataclass(frozen=True)
class RawPeriod:
    name: str
    path: Path
    period_start: str
    period_end: str
    period_level: str
    period_key: str
    period_label: str
    data_start: str
    data_end: str
    source_type: str


@dataclass(frozen=True)
class RawSyncResult:
    period_name: str
    period_start: str
    period_end: str
    status: str
    batch_id: str = ""
    message: str = ""


FileSignature = tuple[str, str, int]


def discover_raw_periods(data_root: Path) -> list[RawPeriod]:
    data_root = Path(data_root)
    if not data_root.exists():
        return []

    periods: list[RawPeriod] = []
    for source in discover_source_period_dirs(data_root):
        metadata = source.period
        periods.append(
            RawPeriod(
                name=source.name,
                path=source.path,
                period_start=metadata.period_start,
                period_end=metadata.period_end,
                period_level=metadata.period_level,
                period_key=metadata.period_key,
                period_label=metadata.period_label,
                data_start=metadata.data_start,
                data_end=metadata.data_end,
                source_type=metadata.source_type,
            )
        )
    return periods


def sync_raw_periods(
    data_root: Path,
    *,
    db_path: Path,
    output_root: Path,
    processed_root: Path,
    category_rules_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    reference_root: Optional[Path] = None,
    category_matcher: Optional[Callable] = None,
) -> list[RawSyncResult]:
    results: list[RawSyncResult] = []
    for period in _canonical_raw_periods(discover_raw_periods(data_root)):
        try:
            current_signature = _raw_signature(period.path)
            latest_batch_id = _latest_successful_batch_for_period(db_path, period)
            if latest_batch_id and current_signature == _stored_signature(db_path, latest_batch_id):
                results.append(
                    RawSyncResult(
                        period.name,
                        period.period_start,
                        period.period_end,
                        "skipped",
                        latest_batch_id,
                        "源文件未变化",
                    )
                )
                continue

            workflow_result = run_archived_workflow(
                period.path,
                period.period_start,
                period.period_end,
                output_root=output_root,
                processed_root=processed_root,
                db_path=db_path,
                category_rules_path=category_rules_path,
                env_path=env_path,
                reference_root=reference_root,
                category_matcher=category_matcher,
                period_level=period.period_level,
                period_key=period.period_key,
                period_label=period.period_label,
                data_start=period.data_start,
                data_end=period.data_end,
                source_type=period.source_type,
            )
            results.append(
                RawSyncResult(
                    period.name,
                    period.period_start,
                    period.period_end,
                    "generated",
                    workflow_result.batch_id,
                    "已根据源文件生成当前周期",
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
        if path.is_file()
        and path.suffix.lower() in TABULAR_SUFFIXES
        and not path.name.startswith("~$")
        and not _is_generated_raw_artifact(path, period_dir)
    )


def _canonical_raw_periods(periods: list[RawPeriod]) -> list[RawPeriod]:
    grouped: dict[tuple[str, str, str], list[RawPeriod]] = {}
    for period in periods:
        grouped.setdefault(_period_group_key(period), []).append(period)
    return [
        _canonical_period_for_group(group)
        for _, group in sorted(
            grouped.items(),
            key=lambda item: (
                item[1][0].period_end,
                item[1][0].period_start,
                item[1][0].period_level,
                item[1][0].period_key,
            ),
        )
    ]


def _period_group_key(period: RawPeriod) -> tuple[str, str, str]:
    return (period.source_type, period.period_level, period.period_key)


def _canonical_period_for_group(periods: list[RawPeriod]) -> RawPeriod:
    return sorted(
        periods,
        key=lambda period: (
            period.data_end,
            period.period_end,
            period.name,
        ),
        reverse=True,
    )[0]


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
        if Path(str(source_file)).suffix.lower() in TABULAR_SUFFIXES
        and not Path(str(source_file)).name.startswith("~$")
        and not _is_generated_raw_artifact(Path(str(source_file)))
    )


def _latest_successful_batch_for_period(db_path: Path, period: RawPeriod) -> str:
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
                    and period_level = ?
                    and period_key = ?
                    and source_type = ?
                order by created_at desc
                limit 1
                """,
                (period.period_level, period.period_key, period.source_type),
            ).fetchone()
        except Exception:
            row = None
        if row:
            return str(row[0])
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
                (period.period_start, period.period_end),
            ).fetchone()
        except Exception:
            return ""
    return str(row[0]) if row else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_generated_raw_artifact(path: Path, root: Path | None = None) -> bool:
    item = Path(path)
    relative = item
    if root is not None:
        try:
            relative = item.relative_to(root)
        except ValueError:
            relative = item
    if item.name == "cleaned.xlsx":
        return True
    if item.stem.lower().endswith("_clean"):
        return True
    return "channel_clean" in relative.parts
