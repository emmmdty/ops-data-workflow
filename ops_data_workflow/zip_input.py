"""Safe zip extraction for uploaded raw data packages."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile


def extract_zip(uploaded_zip: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(uploaded_zip) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            destination = target_dir / member.filename
            resolved = destination.resolve()
            if not str(resolved).startswith(str(target_dir.resolve())):
                raise ValueError(f"Zip 文件包含非法路径: {member.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as handle:
                handle.write(source.read())
    return target_dir
