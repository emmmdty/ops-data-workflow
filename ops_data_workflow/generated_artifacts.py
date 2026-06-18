"""Identify workflow-generated files that must not be re-ingested as sources."""

from __future__ import annotations

from pathlib import Path


GENERATED_FILE_NAMES = {
    "cleaned.xlsx",
    "period_manifest.json",
    "content_recap_core.xlsx",
    "xhs_enrichment_report.xlsx",
}
GENERATED_DIRECTORY_NAMES = {"channel_clean"}


def is_generated_artifact(path: Path, root: Path | None = None) -> bool:
    item = Path(path)
    if item.name in GENERATED_FILE_NAMES:
        return True
    if item.suffix.lower() in {".xlsx", ".xls", ".csv"}:
        if item.name.startswith("pending_") or item.name.endswith("_clean.xlsx"):
            return True
    parts = _relative_parts(item, root)
    return any(part in GENERATED_DIRECTORY_NAMES for part in parts)


def is_generated_tabular_artifact(path: Path, root: Path | None = None) -> bool:
    item = Path(path)
    if item.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
        return False
    return is_generated_artifact(item, root)


def _relative_parts(path: Path, root: Path | None) -> tuple[str, ...]:
    if root is None:
        return path.parts
    try:
        return path.resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        return path.parts
