"""Helpers for turning uploaded files into an analyzable raw directory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from typing import Iterable, Protocol

from .zip_input import extract_zip


SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".xls", ".xlsx", ".zip"}


class UploadedFileLike(Protocol):
    name: str

    def getvalue(self) -> bytes: ...


@dataclass(frozen=True)
class MaterializedUploads:
    raw_dir: Path
    original_files: list[Path]


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
) -> MaterializedUploads:
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    originals_dir = target_dir.parent / "uploaded_originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    original_files: list[Path] = []
    normalized_uploads = [(upload, _normalize_upload_relative_path(upload.name)) for upload in uploads]
    period_root = _common_period_root(normalized_uploads) if strip_common_period_root else ""

    for upload, relative_path in normalized_uploads:
        destination_relative_path = _strip_period_root(relative_path, period_root)
        safe_name = relative_path.name
        suffix = relative_path.suffix.lower()
        if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
            supported = "、".join(sorted(SUPPORTED_UPLOAD_SUFFIXES))
            raise ValueError(f"不支持的上传文件类型：{safe_name}。支持：{supported}")

        original_path = originals_dir / relative_path
        original_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_bytes(upload.getvalue())
        original_files.append(original_path)

        if suffix == ".zip":
            extract_zip(original_path, target_dir)
        else:
            destination = target_dir / destination_relative_path
            _validate_path_within_root(destination, target_dir, destination_relative_path.as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(original_path.read_bytes())

    return MaterializedUploads(raw_dir=target_dir, original_files=original_files)


def _normalize_upload_relative_path(upload_name: str) -> Path:
    normalized = str(upload_name).replace("\\", "/").strip()
    path = Path(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"上传文件名包含非法路径：{upload_name}")
    return path


def _validate_path_within_root(path: Path, root: Path, source_name: str) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not str(resolved).startswith(str(root_resolved)):
        raise ValueError(f"上传文件名包含非法路径：{source_name}")


def _common_period_root(normalized_uploads: list[tuple[UploadedFileLike, Path]]) -> str:
    roots = []
    for _, relative_path in normalized_uploads:
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
