"""Raw source storage helpers for the lightweight data layout."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import json
import re
import shutil

from .generated_artifacts import is_generated_tabular_artifact
from .periods import (
    PERIOD_LEVEL_MONTH,
    PERIOD_LEVEL_WEEK,
    ReviewPeriod,
    period_metadata_from_dates,
    review_period_from_dates,
)
from .reference_tables import parse_period_from_raw_dir


TABULAR_SUFFIXES = {".csv", ".xls", ".xlsx"}
REFERENCE_DIR_NAME = "reference"
MONTHS_DIR_NAME = "months"
WEEKS_DIR_NAME = "weeks"


@dataclass(frozen=True)
class SourcePeriodDir:
    name: str
    path: Path
    period: ReviewPeriod


@dataclass(frozen=True)
class LegacyMigrationResult:
    source_path: Path
    target_path: Path
    file_count: int


def source_dir_for_period(data_root: Path, period: ReviewPeriod) -> Path:
    """Return the canonical raw-source directory for a review period."""
    data_root = Path(data_root)
    if period.period_level == PERIOD_LEVEL_MONTH:
        start = date.fromisoformat(period.period_start)
        return data_root / MONTHS_DIR_NAME / f"{start.year:04d}{start.month:02d}"
    if period.period_level == PERIOD_LEVEL_WEEK:
        return data_root / WEEKS_DIR_NAME / week_date_storage_key(
            date.fromisoformat(period.period_start),
            date.fromisoformat(period.period_end),
        )
    raise ValueError(f"原始数据只支持月度或周度目录：{period.period_level}")


def source_storage_key(period: ReviewPeriod) -> str:
    if period.period_level in {PERIOD_LEVEL_MONTH, PERIOD_LEVEL_WEEK}:
        return source_dir_for_period(Path("."), period).name
    key = str(period.period_key or period.period_start or "period").strip()
    return re.sub(r"[^0-9A-Za-z_-]+", "", key.replace(":", "_")) or "period"


def source_period_from_path(path: Path) -> ReviewPeriod:
    """Parse a canonical source period path.

    Runtime code intentionally recognizes only data/months and data/weeks, not
    legacy data/raw directories.
    """
    path = Path(path)
    parent = path.parent.name
    name = path.name
    if parent == MONTHS_DIR_NAME:
        match = re.fullmatch(r"(20\d{2})(0[1-9]|1[0-2])", name)
        if not match:
            raise ValueError(f"无法识别月度原始数据目录：{path}")
        year = int(match.group(1))
        month = int(match.group(2))
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        return review_period_from_dates(start, end, PERIOD_LEVEL_MONTH)
    if parent == WEEKS_DIR_NAME:
        return week_period_from_key(name)
    raise ValueError(f"不是有效的原始数据目录：{path}")


def discover_source_period_dirs(data_root: Path) -> list[SourcePeriodDir]:
    """List raw-source period directories under data/months and data/weeks."""
    data_root = Path(data_root)
    periods: list[SourcePeriodDir] = []
    for root_name in [WEEKS_DIR_NAME, MONTHS_DIR_NAME]:
        root = data_root / root_name
        if not root.exists():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or not _raw_tabular_files(child):
                continue
            try:
                period = source_period_from_path(child)
            except ValueError:
                continue
            periods.append(SourcePeriodDir(name=child.name, path=child, period=period))
    return sorted(periods, key=lambda item: (item.period.period_start, item.period.period_end, item.name))


def latest_reference_workbook(reference_dir: Path) -> Path | None:
    """Return the newest human-maintained reference workbook."""
    reference_dir = Path(reference_dir)
    if not reference_dir.exists():
        return None
    candidates: list[tuple[str, float, str, Path]] = []
    for path in reference_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".xlsx", ".xls"}:
            continue
        name = path.name
        lowered = name.lower()
        if name.startswith("~$") or "backup" in lowered or "备份" in lowered:
            continue
        date_token = _latest_date_token(name)
        candidates.append((date_token, path.stat().st_mtime, name, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[:3])[3]


def week_date_storage_key(start: date, end: date) -> str:
    return f"{start:%Y%m%d}-{end:%Y%m%d}"


def week_period_from_key(key: str) -> ReviewPeriod:
    match = re.fullmatch(r"(20\d{2})(0[1-9]|1[0-2])([0-3]\d)-(20\d{2})(0[1-9]|1[0-2])([0-3]\d)", str(key).strip())
    if not match:
        raise ValueError(f"无法识别周度原始数据目录：{key}")
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    end_year = int(match.group(4))
    end_month = int(match.group(5))
    end_day = int(match.group(6))
    try:
        start = date(year, month, day)
        end = date(end_year, end_month, end_day)
    except ValueError as exc:
        raise ValueError(f"无法识别周度原始数据目录：{key}") from exc
    if end < start:
        raise ValueError(f"无法识别周度原始数据目录：{key}")
    return review_period_from_dates(start, end, PERIOD_LEVEL_WEEK)


def migrate_legacy_raw_to_source_layout(data_root: Path, *, move: bool = False) -> list[LegacyMigrationResult]:
    """One-time legacy data/raw migration.

    Only original tabular source files are migrated. Generated workbooks,
    manifests, and non-tabular artifacts are ignored.
    """
    data_root = Path(data_root)
    raw_root = data_root / "raw"
    if not raw_root.exists():
        return []
    results: list[LegacyMigrationResult] = []
    for legacy_dir in sorted(raw_root.iterdir(), key=lambda item: item.name):
        if not legacy_dir.is_dir():
            continue
        try:
            period = _legacy_period(legacy_dir)
        except ValueError:
            continue
        target = source_dir_for_period(data_root, period)
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        for source in _raw_tabular_files(legacy_dir):
            destination = _unique_destination(target, source.name)
            if move:
                shutil.move(str(source), destination)
            else:
                shutil.copy2(source, destination)
            copied += 1
        if copied:
            results.append(LegacyMigrationResult(legacy_dir, target, copied))
    return results


def _legacy_period(period_dir: Path) -> ReviewPeriod:
    manifest = Path(period_dir) / "period_manifest.json"
    if manifest.exists():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return period_metadata_from_dates(
            str(payload.get("period_start", "")),
            str(payload.get("period_end", "")),
            str(payload.get("period_level", "")),
            str(payload.get("period_key", "")),
            str(payload.get("period_label", "")),
            str(payload.get("data_start", "")),
            str(payload.get("data_end", "")),
            str(payload.get("source_type", "")),
        )
    period_start, period_end = parse_period_from_raw_dir(period_dir)
    return period_metadata_from_dates(period_start, period_end)


def _raw_tabular_files(period_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in Path(period_dir).rglob("*")
        if path.is_file()
        and path.suffix.lower() in TABULAR_SUFFIXES
        and not path.name.startswith("~$")
        and not _is_generated_artifact(path, period_dir)
    )


def _is_generated_artifact(path: Path, root: Path | None = None) -> bool:
    return is_generated_tabular_artifact(Path(path), root)


def _latest_date_token(name: str) -> str:
    tokens = re.findall(r"(20\d{2})[-_年.]?([01]\d)[-_月.]?([0-3]\d)", name)
    if not tokens:
        return ""
    return max(f"{year}{month}{day}" for year, month, day in tokens)


def _unique_destination(directory: Path, file_name: str) -> Path:
    destination = Path(directory) / Path(file_name).name
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    counter = 2
    while True:
        candidate = destination.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
