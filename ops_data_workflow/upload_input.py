"""Helpers for turning uploaded files into an analyzable raw directory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Iterable, Protocol
from zipfile import ZipFile

from .generated_artifacts import is_generated_tabular_artifact
from .source_channels import infer_channel_from_path
from .zip_input import extract_zip


SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".xls", ".xlsx", ".zip"}


class UploadedFileLike(Protocol):
    name: str

    def getvalue(self) -> bytes: ...


@dataclass(frozen=True)
class MaterializedUploads:
    raw_dir: Path
    original_files: list[Path]


@dataclass(frozen=True)
class UploadChannelConflict:
    channel: str
    existing_files: list[Path]
    incoming_files: list[str]


def infer_period_from_upload_names(
    uploads: Iterable[UploadedFileLike],
) -> tuple[date, date] | None:
    for upload in uploads:
        relative_path = _normalize_upload_relative_path(upload.name)
        parts = relative_path.parts
        if len(parts) < 2:
            continue
        inferred = _parse_period_folder_name(parts[0])
        if inferred is not None:
            return inferred
    return None


def materialize_uploaded_files(
    uploads: Iterable[UploadedFileLike],
    target_dir: Path,
    *,
    strip_common_period_root: bool = False,
    replace_same_channel: bool = False,
) -> MaterializedUploads:
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    uploads = list(uploads)
    normalized_sources = _normalized_upload_sources(uploads)
    normalized_inputs = _normalized_uploads_with_zip_members(uploads)
    period_root = _common_period_root(normalized_inputs) if strip_common_period_root else ""
    incoming_channels = _incoming_channels(normalized_inputs, period_root)
    conflicts = _channel_conflicts_for_channels(target_dir, incoming_channels)
    if conflicts and not replace_same_channel:
        channels = "、".join(conflict.channel for conflict in conflicts)
        raise FileExistsError(f"本地已存在渠道：{channels}。如需替换，请确认覆盖已存在渠道。")
    if replace_same_channel:
        _remove_channel_files(target_dir, incoming_channels)
    if incoming_channels:
        _invalidate_generated_period_artifacts(target_dir)

    materialized_files: list[Path] = []
    with TemporaryDirectory() as tmp:
        staging_dir = Path(tmp)
        for upload, relative_path in normalized_sources:
            destination_relative_path = _strip_period_root(relative_path, period_root)
            safe_name = relative_path.name
            suffix = relative_path.suffix.lower()
            if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
                supported = "、".join(sorted(SUPPORTED_UPLOAD_SUFFIXES))
                raise ValueError(f"不支持的上传文件类型：{safe_name}。支持：{supported}")
            if suffix == ".zip":
                staged_zip = staging_dir / relative_path.name
                staged_zip.write_bytes(upload.getvalue())
                extract_zip(staged_zip, target_dir, strip_root=period_root)
            else:
                destination = target_dir / destination_relative_path
                _validate_path_within_root(destination, target_dir, destination_relative_path.as_posix())
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(upload.getvalue())
                materialized_files.append(destination)

    return MaterializedUploads(raw_dir=target_dir, original_files=materialized_files)


def detect_upload_channel_conflicts(
    uploads: Iterable[UploadedFileLike],
    target_dir: Path,
    *,
    strip_common_period_root: bool = False,
) -> list[UploadChannelConflict]:
    normalized_uploads = _normalized_uploads_with_zip_members(uploads)
    period_root = _common_period_root(normalized_uploads) if strip_common_period_root else ""
    incoming_channels = _incoming_channels(normalized_uploads, period_root)
    return _channel_conflicts_for_channels(Path(target_dir), incoming_channels, normalized_uploads, period_root)


def _normalize_upload_relative_path(upload_name: str) -> Path:
    normalized = str(upload_name).replace("\\", "/").strip()
    path = Path(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"上传文件名包含非法路径：{upload_name}")
    return path


def _normalized_upload_sources(uploads: Iterable[UploadedFileLike]) -> list[tuple[UploadedFileLike, Path]]:
    return [(upload, _normalize_upload_relative_path(upload.name)) for upload in uploads]


def _normalized_uploads_with_zip_members(uploads: Iterable[UploadedFileLike]) -> list[tuple[UploadedFileLike, Path]]:
    normalized_uploads: list[tuple[UploadedFileLike, Path]] = []
    for upload, relative_path in _normalized_upload_sources(uploads):
        normalized_uploads.append((upload, relative_path))
        if relative_path.suffix.lower() != ".zip":
            continue
        with TemporaryDirectory() as tmp:
            staged_zip = Path(tmp) / relative_path.name
            staged_zip.write_bytes(upload.getvalue())
            with ZipFile(staged_zip) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    member_path = _normalize_upload_relative_path(member.filename)
                    if member_path.suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES - {".zip"}:
                        normalized_uploads.append((upload, member_path))
    return normalized_uploads


def _validate_path_within_root(path: Path, root: Path, source_name: str) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not str(resolved).startswith(str(root_resolved)):
        raise ValueError(f"上传文件名包含非法路径：{source_name}")


def _common_period_root(normalized_uploads: list[tuple[UploadedFileLike, Path]]) -> str:
    roots = []
    for _, relative_path in normalized_uploads:
        if relative_path.suffix.lower() == ".zip":
            continue
        if len(relative_path.parts) < 2:
            return ""
        root = relative_path.parts[0]
        if _parse_period_folder_name(root) is None:
            return ""
        roots.append(root)
    if not roots:
        return ""
    first = roots[0]
    return first if all(root == first for root in roots) else ""


def _strip_period_root(relative_path: Path, period_root: str) -> Path:
    if not period_root or not relative_path.parts or relative_path.parts[0] != period_root:
        return relative_path
    return Path(*relative_path.parts[1:])


def _incoming_channels(normalized_uploads: list[tuple[UploadedFileLike, Path]], period_root: str) -> set[str]:
    return {
        infer_channel_from_path(_strip_period_root(relative_path, period_root))
        for _, relative_path in normalized_uploads
        if relative_path.suffix.lower() != ".zip"
    }


def _channel_conflicts_for_channels(
    target_dir: Path,
    channels: set[str],
    normalized_uploads: list[tuple[UploadedFileLike, Path]] | None = None,
    period_root: str = "",
) -> list[UploadChannelConflict]:
    if not channels or not Path(target_dir).exists():
        return []
    incoming_by_channel: dict[str, list[str]] = {channel: [] for channel in channels}
    if normalized_uploads is not None:
        for _, relative_path in normalized_uploads:
            if relative_path.suffix.lower() == ".zip":
                continue
            channel = infer_channel_from_path(_strip_period_root(relative_path, period_root))
            if channel in incoming_by_channel:
                incoming_by_channel[channel].append(_strip_period_root(relative_path, period_root).as_posix())

    existing_by_channel: dict[str, list[Path]] = {channel: [] for channel in channels}
    for path in sorted(Path(target_dir).rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("~$") or is_generated_tabular_artifact(path, target_dir):
            continue
        if path.suffix.lower() not in SUPPORTED_UPLOAD_SUFFIXES - {".zip"}:
            continue
        channel = infer_channel_from_path(path.relative_to(target_dir))
        if channel in existing_by_channel:
            existing_by_channel[channel].append(path)
    return [
        UploadChannelConflict(
            channel=channel,
            existing_files=files,
            incoming_files=incoming_by_channel.get(channel, []),
        )
        for channel, files in sorted(existing_by_channel.items())
        if files
    ]


def _remove_channel_files(target_dir: Path, channels: set[str]) -> None:
    if not channels:
        return
    for path in sorted(Path(target_dir).rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("~$") or is_generated_tabular_artifact(path, target_dir):
            continue
        if path.suffix.lower() not in SUPPORTED_UPLOAD_SUFFIXES - {".zip"}:
            continue
        if infer_channel_from_path(path.relative_to(target_dir)) in channels:
            path.unlink()


def _invalidate_generated_period_artifacts(target_dir: Path) -> None:
    for name in ["cleaned.xlsx", "period_manifest.json"]:
        path = Path(target_dir) / name
        if path.exists():
            path.unlink()


def _parse_period_folder_name(folder_name: str) -> tuple[date, date] | None:
    patterns = [
        re.fullmatch(r"(\d{8})-(\d{8})", folder_name),
        re.fullmatch(r"(\d{4}-\d{2}-\d{2})[_-](\d{4}-\d{2}-\d{2})", folder_name),
    ]
    for match in patterns:
        if match is None:
            continue
        start = _parse_folder_date(match.group(1))
        end = _parse_folder_date(match.group(2))
        if start is not None and end is not None and start <= end:
            return start, end
    return None


def _parse_folder_date(value: str) -> date | None:
    try:
        if "-" in value:
            return date.fromisoformat(value)
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None
