"""Import an external raw-data tree into the app source layout."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import shutil
import re
from typing import Optional

import pandas as pd

from .generated_artifacts import is_generated_tabular_artifact
from .pipeline import TABULAR_SUFFIXES, _is_csv
from .periods import ReviewPeriod, infer_review_period_from_text, review_period_from_dates
from .raw_cleaning import reset_runtime_data
from .source_channels import infer_channel_from_path
from .source_storage import source_dir_for_period
from .storage import init_db


REFERENCE_FILE_TOKENS = ("原生内容投稿", "reference", "台账")
MONTH_DIR_TOKENS = {"month", "months", "mouth", "mouths", "月", "月份", "月度"}


@dataclass(frozen=True)
class SourceImportEntry:
    source_path: Path
    relative_path: str
    target_path: Path
    kind: str
    status: str
    message: str
    period_level: str = ""
    period_key: str = ""
    period_label: str = ""
    channel: str = ""
    sheet_names: Optional[list[str]] = None


@dataclass(frozen=True)
class SourceImportPlan:
    source_root: Path
    data_root: Path
    entries: list[SourceImportEntry]

    @property
    def copyable_entries(self) -> list[SourceImportEntry]:
        return [entry for entry in self.entries if entry.kind == "raw" and entry.status == "ready"]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "kind": entry.kind,
                    "status": entry.status,
                    "relative_path": entry.relative_path,
                    "target_path": entry.target_path.as_posix() if entry.target_path else "",
                    "period_key": entry.period_key,
                    "period_label": entry.period_label,
                    "channel": entry.channel,
                    "sheet_names": "；".join(entry.sheet_names or []),
                    "message": entry.message,
                }
                for entry in self.entries
            ]
        )


@dataclass(frozen=True)
class SourceImportResult:
    copied_count: int
    skipped_count: int
    copied_files: list[Path]


def build_source_import_plan(source_root: Path, data_root: Path, *, default_year: int) -> SourceImportPlan:
    source_root = Path(source_root)
    data_root = Path(data_root)
    if not source_root.exists():
        raise FileNotFoundError(f"未找到外部数据目录：{source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"外部数据路径不是目录：{source_root}")
    try:
        list(source_root.iterdir())
    except PermissionError as exc:
        raise PermissionError(f"无法读取外部数据目录：{source_root}") from exc

    entries: list[SourceImportEntry] = []
    for path in _iter_external_files(source_root):
        relative = path.relative_to(source_root).as_posix()
        sheet_names = _sheet_names(path)
        if _is_reference_file(path, relative):
            entries.append(
                SourceImportEntry(
                    source_path=path,
                    relative_path=relative,
                    target_path=Path(""),
                    kind="ignored",
                    status="local_reference_disabled",
                    message="内容台账以飞书实时读取为准，本地 reference 不再导入。",
                    sheet_names=sheet_names,
                )
            )
            continue

        period = _infer_import_period(relative, default_year)
        if period is None or period.period_level not in {"week", "month"}:
            entries.append(
                SourceImportEntry(
                    source_path=path,
                    relative_path=relative,
                    target_path=Path(""),
                    kind="ignored",
                    status="unrecognized_period",
                    message="无法从路径识别周度或月度复盘周期。",
                    sheet_names=sheet_names,
                )
            )
            continue

        target_dir = source_dir_for_period(data_root, period)
        entries.append(
            SourceImportEntry(
                source_path=path,
                relative_path=relative,
                target_path=_unique_target(target_dir, path.name, relative, entries),
                kind="raw",
                status="ready",
                message="识别为投放原始数据。",
                period_level=period.period_level,
                period_key=period.period_key,
                period_label=period.period_label,
                channel=infer_channel_from_path(relative),
                sheet_names=sheet_names,
            )
        )
    return SourceImportPlan(source_root=source_root, data_root=data_root, entries=entries)


def execute_source_import_plan(
    plan: SourceImportPlan,
    *,
    project_root: Path,
    replace_all: bool = False,
) -> SourceImportResult:
    project_root = Path(project_root)
    if replace_all:
        _clear_source_and_runtime(project_root, plan.data_root)

    copied: list[Path] = []
    skipped = 0
    for entry in plan.entries:
        if entry not in plan.copyable_entries:
            skipped += 1
            continue
        if entry.target_path.exists() and not replace_all:
            raise FileExistsError(f"目标文件已存在：{entry.target_path}")
        entry.target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.source_path, entry.target_path)
        copied.append(entry.target_path)
    if replace_all:
        init_db(project_root / ".runtime" / "workflow.sqlite3")
    return SourceImportResult(copied_count=len(copied), skipped_count=skipped, copied_files=copied)


def _iter_external_files(source_root: Path) -> list[Path]:
    return sorted(
        path
        for path in Path(source_root).rglob("*")
        if path.is_file()
        and path.suffix.lower() in TABULAR_SUFFIXES
        and not path.name.startswith("~$")
        and not path.name.startswith("._")
        and "__MACOSX" not in path.parts
        and not is_generated_tabular_artifact(path, source_root)
    )


def _sheet_names(path: Path) -> list[str]:
    if _is_csv(path):
        return ["CSV"]
    try:
        with pd.ExcelFile(path) as workbook:
            return list(workbook.sheet_names)
    except Exception as exc:
        return [f"无法打开：{type(exc).__name__}: {exc}"]


def _infer_import_period(relative: str, default_year: int) -> ReviewPeriod | None:
    inferred = infer_review_period_from_text(relative, default_year)
    if inferred is not None:
        return inferred
    parts = Path(relative).parts
    for index, part in enumerate(parts[:-1]):
        if part.lower() not in MONTH_DIR_TOKENS or index + 1 >= len(parts):
            continue
        month = _month_from_dir_token(parts[index + 1])
        if month is None:
            continue
        year, month_index = month
        start = date(year, month_index, 1)
        end = date(year, month_index, monthrange(year, month_index)[1])
        return review_period_from_dates(start, end, "month")
    return None


def _month_from_dir_token(token: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(20\d{2})[-_]?([01]\d)", str(token).strip())
    if not match:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return int(match.group(1)), month


def _is_reference_file(path: Path, relative: str) -> bool:
    text = f"{relative}/{path.name}".lower()
    return any(token.lower() in text for token in REFERENCE_FILE_TOKENS)


def _unique_target(target_dir: Path, file_name: str, relative: str, existing_entries: list[SourceImportEntry]) -> Path:
    candidate = Path(target_dir) / Path(file_name).name
    used = {entry.target_path for entry in existing_entries}
    if candidate not in used:
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    slug = "".join(char if char.isalnum() else "_" for char in relative).strip("_")[-36:] or "copy"
    counter = 1
    while True:
        counter += 1
        next_candidate = candidate.with_name(f"{stem}_{slug}_{counter}{suffix}")
        if next_candidate not in used:
            return next_candidate


def _clear_source_and_runtime(project_root: Path, data_root: Path) -> None:
    for target in [
        data_root / "months",
        data_root / "weeks",
        data_root / "raw",
        data_root / "file_backup",
        project_root / "processed",
        project_root / "outputs",
        project_root / "output" / "playwright",
    ]:
        if target.exists():
            shutil.rmtree(target)
    workflow_db = data_root / "workflow.sqlite3"
    if workflow_db.exists():
        workflow_db.unlink()
    reset_runtime_data(project_root)
