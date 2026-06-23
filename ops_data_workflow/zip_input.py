"""Safe zip extraction for uploaded raw data packages."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile


def extract_zip(uploaded_zip: Path, target_dir: Path, *, strip_root: str = "") -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(uploaded_zip) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            member_path = Path(str(member.filename).replace("\\", "/").strip())
            if strip_root and member_path.parts and member_path.parts[0] == strip_root:
                member_path = Path(*member_path.parts[1:])
            destination = target_dir / member_path
            resolved = destination.resolve()
            try:
                resolved.relative_to(target_dir.resolve())
            except ValueError:
                raise ValueError(f"Zip 文件包含非法路径: {member.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as handle:
                handle.write(source.read())
    return target_dir
