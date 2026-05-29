"""Normalize uploaded raw files into review-period buckets."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import json
import re
import shutil
from tempfile import TemporaryDirectory
from typing import Iterable, Protocol
from zipfile import ZipFile

from .periods import ReviewPeriod, infer_review_period_from_text, period_raw_dir_name, review_period_from_dates
from .pipeline import TABULAR_SUFFIXES
from .source_channels import infer_channel_from_path


class UploadedFileLike(Protocol):
    name: str

    def getvalue(self) -> bytes: ...


@dataclass(frozen=True)
class NormalizedPeriodBucket:
    review_period: ReviewPeriod
    raw_dir: Path
    manifest_path: Path
    files: list[Path]
    source_paths: list[str]
    ignored_file_count: int = 0


@dataclass(frozen=True)
class PreviewPeriodBucket:
    review_period: ReviewPeriod
    file_count: int
    source_paths: list[str]
    ignored_file_count: int = 0


@dataclass(frozen=True)
class NormalizedUploadChannelConflict:
    review_period: ReviewPeriod
    channel: str
    existing_files: list[Path]
    incoming_files: list[str]


def normalize_uploaded_periods(
    uploads: Iterable[UploadedFileLike],
    raw_root: Path,
    *,
    default_year: int,
    replace_same_channel: bool = False,
) -> list[NormalizedPeriodBucket]:
    """Materialize uploaded files into normalized raw dirs grouped by review period."""
    raw_root = Path(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp:
        staging_dir = Path(tmp) / f"staging_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        for upload in uploads:
            relative_path = _safe_relative_path(upload.name)
            data = upload.getvalue()
            original_path = staging_dir / relative_path
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_path.write_bytes(data)
            if relative_path.suffix.lower() == ".zip":
                _extract_zip_bytes(data, staging_dir / relative_path.stem)

        tabular_by_key: dict[tuple[str, str], list[tuple[ReviewPeriod, Path, str]]] = defaultdict(list)
        ignored_by_key: dict[tuple[str, str], int] = defaultdict(int)

        for path in sorted(staging_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".zip":
                continue
            source_name = path.relative_to(staging_dir).as_posix()
            inferred = infer_review_period_from_text(source_name, default_year)
            if _is_ignored_path(path) or path.suffix.lower() not in TABULAR_SUFFIXES:
                if inferred is not None:
                    ignored_by_key[(inferred.period_level, inferred.period_key)] += 1
                continue
            if inferred is None:
                raise ValueError(f"无法从上传路径识别复盘周期：{source_name}")
            tabular_by_key[(inferred.period_level, inferred.period_key)].append((inferred, path, source_name))

        buckets: list[NormalizedPeriodBucket] = []
        for key, entries in sorted(tabular_by_key.items(), key=lambda item: (item[1][0][0].period_start, item[0][0], item[0][1])):
            period = _combine_periods([entry[0] for entry in entries])
            raw_dir = raw_root / period_raw_dir_name(period)
            raw_dir.mkdir(parents=True, exist_ok=True)
            incoming_channels = {infer_channel_from_path(entry[2]) for entry in entries}
            conflicts = _existing_channels(raw_dir, incoming_channels)
            if conflicts and not replace_same_channel:
                channels = "、".join(sorted(conflicts))
                raise FileExistsError(f"本地已存在渠道：{channels}。如需替换，请确认覆盖已存在渠道。")
            if replace_same_channel:
                _remove_existing_channels(raw_dir, incoming_channels)
            _invalidate_generated_period_artifacts(raw_dir)
            copied: list[Path] = []
            source_paths: list[str] = []
            for _, path, source_name in entries:
                destination = _unique_destination(raw_dir, path.name, source_name)
                shutil.copy2(path, destination)
                copied.append(destination)
                source_paths.append(source_name)

            manifest_path = raw_dir / "period_manifest.json"
            manifest = {
                "period_level": period.period_level,
                "period_key": period.period_key,
                "period_label": period.period_label,
                "period_start": period.period_start,
                "period_end": period.period_end,
                "data_start": period.data_start,
                "data_end": period.data_end,
                "source_type": period.source_type,
                "file_count": len(copied),
                "ignored_file_count": int(ignored_by_key.get(key, 0)),
                "source_paths": source_paths,
                "files": [path.relative_to(raw_dir).as_posix() for path in copied],
            }
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            buckets.append(
                NormalizedPeriodBucket(
                    review_period=period,
                    raw_dir=raw_dir,
                    manifest_path=manifest_path,
                    files=copied,
                    source_paths=source_paths,
                    ignored_file_count=int(ignored_by_key.get(key, 0)),
                )
            )
        return buckets


def preview_uploaded_periods(
    uploads: Iterable[UploadedFileLike],
    *,
    default_year: int,
) -> list[ReviewPeriod]:
    """Preview normalized review periods without writing into the app raw root."""
    return [bucket.review_period for bucket in preview_uploaded_period_buckets(uploads, default_year=default_year)]


def preview_uploaded_period_buckets(
    uploads: Iterable[UploadedFileLike],
    *,
    default_year: int,
) -> list[PreviewPeriodBucket]:
    """Preview normalized review-period buckets and source counts."""
    with TemporaryDirectory() as tmp:
        buckets = normalize_uploaded_periods(uploads, Path(tmp) / "raw", default_year=default_year)
        return [
            PreviewPeriodBucket(
                review_period=bucket.review_period,
                file_count=len(bucket.files),
                source_paths=bucket.source_paths,
                ignored_file_count=bucket.ignored_file_count,
            )
            for bucket in buckets
        ]


def detect_normalized_upload_channel_conflicts(
    uploads: Iterable[UploadedFileLike],
    raw_root: Path,
    *,
    default_year: int,
) -> list[NormalizedUploadChannelConflict]:
    conflicts: list[NormalizedUploadChannelConflict] = []
    for bucket in preview_uploaded_period_buckets(uploads, default_year=default_year):
        raw_dir = Path(raw_root) / period_raw_dir_name(bucket.review_period)
        incoming_by_channel: dict[str, list[str]] = defaultdict(list)
        for source_path in bucket.source_paths:
            path = Path(source_path)
            if path.suffix.lower() not in TABULAR_SUFFIXES or _is_ignored_path(path):
                continue
            incoming_by_channel[infer_channel_from_path(source_path)].append(source_path)
        for channel in sorted(_existing_channels(raw_dir, set(incoming_by_channel))):
            conflicts.append(
                NormalizedUploadChannelConflict(
                    review_period=bucket.review_period,
                    channel=channel,
                    existing_files=_existing_files_for_channel(raw_dir, channel),
                    incoming_files=incoming_by_channel.get(channel, []),
                )
            )
    return conflicts


def _combine_periods(periods: list[ReviewPeriod]) -> ReviewPeriod:
    first = periods[0]
    data_start = min(period.data_start for period in periods)
    data_end = max(period.data_end for period in periods)
    return review_period_from_dates(
        datetime.fromisoformat(data_start).date(),
        datetime.fromisoformat(data_end).date(),
        first.period_level,
        logic_start=datetime.fromisoformat(first.period_start).date(),
        logic_end=datetime.fromisoformat(first.period_end).date(),
        source_type=first.source_type,
    )


def _safe_relative_path(name: str) -> Path:
    normalized = str(name or "").replace("\\", "/").strip()
    path = Path(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"上传文件名包含非法路径：{name}")
    return path


def _extract_zip_bytes(data: bytes, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for member_name, payload in _zip_members(data):
        relative_path = _safe_relative_path(member_name)
        if _is_ignored_path(relative_path):
            continue
        destination = target_dir / relative_path
        resolved = destination.resolve()
        if not str(resolved).startswith(str(target_dir.resolve())):
            raise ValueError(f"Zip 文件包含非法路径: {member_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def _zip_members(data: bytes) -> list[tuple[str, bytes]]:
    members = _read_zip_members(data)
    if any(_looks_mojibake(name) for name, _ in members):
        try:
            gbk_members = _read_zip_members(data, metadata_encoding="gbk")
            if sum(_looks_mojibake(name) for name, _ in gbk_members) < sum(_looks_mojibake(name) for name, _ in members):
                return gbk_members
        except Exception:
            pass
    return members


def _read_zip_members(data: bytes, metadata_encoding: str | None = None) -> list[tuple[str, bytes]]:
    kwargs = {"metadata_encoding": metadata_encoding} if metadata_encoding else {}
    members: list[tuple[str, bytes]] = []
    with ZipFile(BytesIO(data), **kwargs) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            members.append((member.filename, archive.read(member)))
    return members


def _looks_mojibake(text: str) -> bool:
    return bool(re.search(r"[╩╛╒╨╬╢�]", text))


def _is_ignored_path(path: Path) -> bool:
    parts = Path(path).parts
    return (
        Path(path).name == ".DS_Store"
        or Path(path).name.startswith("~$")
        or Path(path).name.startswith("._")
        or "__MACOSX" in parts
    )


def _unique_destination(raw_dir: Path, file_name: str, source_name: str) -> Path:
    safe_name = Path(file_name).name
    destination = raw_dir / safe_name
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    source_slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", source_name).strip("_")[-36:] or "copy"
    candidate = raw_dir / f"{stem}_{source_slug}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = raw_dir / f"{stem}_{source_slug}_{counter}{suffix}"
        counter += 1
    return candidate


def _remove_existing_channels(raw_dir: Path, channels: set[str]) -> None:
    if not raw_dir.exists() or not channels:
        return
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.name == "cleaned.xlsx" or path.name == "period_manifest.json":
            continue
        if path.suffix.lower() not in TABULAR_SUFFIXES:
            continue
        if infer_channel_from_path(path.relative_to(raw_dir)) in channels:
            path.unlink()


def _existing_channels(raw_dir: Path, channels: set[str]) -> set[str]:
    if not raw_dir.exists() or not channels:
        return set()
    existing: set[str] = set()
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.name == "cleaned.xlsx" or path.name == "period_manifest.json":
            continue
        if path.suffix.lower() not in TABULAR_SUFFIXES:
            continue
        channel = infer_channel_from_path(path.relative_to(raw_dir))
        if channel in channels:
            existing.add(channel)
    return existing


def _existing_files_for_channel(raw_dir: Path, channel: str) -> list[Path]:
    if not raw_dir.exists() or not channel:
        return []
    files: list[Path] = []
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.name == "cleaned.xlsx" or path.name == "period_manifest.json":
            continue
        if path.suffix.lower() not in TABULAR_SUFFIXES:
            continue
        if infer_channel_from_path(path.relative_to(raw_dir)) == channel:
            files.append(path)
    return files


def _invalidate_generated_period_artifacts(raw_dir: Path) -> None:
    for path in [Path(raw_dir) / "cleaned.xlsx", Path(raw_dir) / "period_manifest.json"]:
        if path.exists():
            path.unlink()
