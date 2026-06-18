"""Content-addressed cache helpers for high-value recap assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Iterable, Mapping

import pandas as pd

from .storage import (
    list_top_asset_cache_entries,
    list_top_asset_cache_refs,
    mark_top_asset_cache_ref_retained,
    remove_top_asset_cache_entry,
)


DEFAULT_MAX_CACHE_SIZE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_KEEP_RECENT_PERIODS = 8


@dataclass(frozen=True)
class CacheCleanupResult:
    deleted_count: int
    deleted_bytes: int
    remaining_bytes: int


def asset_key_for_job(job: Mapping[str, object]) -> str:
    platform = _platform_label(job.get("platform") or job.get("channel"))
    content_id = _text(job.get("content_id"))
    content_url = _text(job.get("content_url"))
    identity = _text(job.get("content_identity_key"))
    if content_id:
        return f"{platform}::id::{content_id}"
    if content_url:
        return f"{platform}::url::{_normalize_url(content_url)}"
    if identity:
        return f"{platform}::identity::{identity}"
    title = _text(job.get("title"))
    account = _text(job.get("account"))
    return f"{platform}::title_account::{account}::{title}" if title else f"{platform}::unknown"


def asset_cache_path(cache_root: Path, job: Mapping[str, object]) -> Path:
    platform = _platform_cache_name(job.get("platform") or job.get("channel"))
    return Path(cache_root).expanduser().resolve() / platform / safe_path_segment(asset_key_for_job(job))


def directory_size(path: Path) -> int:
    root = Path(path)
    if not root.exists():
        return 0
    if root.is_file():
        return int(root.stat().st_size)
    total = 0
    for child in root.rglob("*"):
        if child.is_file():
            try:
                total += int(child.stat().st_size)
            except OSError:
                pass
    return total


def cleanup_top_asset_cache(
    db_path: Path,
    *,
    cache_root: Path = Path(".runtime/top-assets"),
    keep_batch_ids: Iterable[str] | None = None,
    max_size_bytes: int = DEFAULT_MAX_CACHE_SIZE_BYTES,
) -> CacheCleanupResult:
    entries = list_top_asset_cache_entries(db_path)
    refs = list_top_asset_cache_refs(db_path)
    keep = {str(batch_id) for batch_id in (keep_batch_ids or []) if str(batch_id)}
    cache_root_resolved = Path(cache_root).expanduser().resolve()
    if entries.empty:
        return CacheCleanupResult(deleted_count=0, deleted_bytes=0, remaining_bytes=0)

    protected_asset_keys = set()
    if not refs.empty and keep:
        scoped = refs[refs.get("batch_id", pd.Series(dtype=object)).fillna("").astype(str).isin(keep)]
        protected_asset_keys = set(scoped.get("asset_key", pd.Series(dtype=object)).fillna("").astype(str))
    if not refs.empty:
        retained = refs[refs.get("retained", pd.Series(dtype=object)).astype(str).isin({"1", "True", "true"})]
        retained = retained[retained.get("batch_id", pd.Series(dtype=object)).fillna("").astype(str).isin(keep)]
        protected_asset_keys.update(retained.get("asset_key", pd.Series(dtype=object)).fillna("").astype(str))

    entries = entries.copy()
    entries["_size"] = entries["asset_dir"].map(
        lambda value: directory_size(Path(_text(value))) if _is_under(Path(_text(value)).expanduser(), cache_root_resolved) else 0
    )
    total_size = int(entries["_size"].sum())
    deleted_count = 0
    deleted_bytes = 0
    if total_size <= int(max_size_bytes):
        return CacheCleanupResult(deleted_count=0, deleted_bytes=0, remaining_bytes=total_size)

    candidates = entries[~entries["asset_key"].astype(str).isin(protected_asset_keys)].copy()
    candidates = candidates.sort_values(["updated_at", "created_at", "asset_key"], ascending=[True, True, True])
    for _, row in candidates.iterrows():
        if total_size <= int(max_size_bytes):
            break
        asset_dir = Path(_text(row.get("asset_dir"))).expanduser()
        try:
            resolved = asset_dir.resolve()
        except OSError:
            resolved = asset_dir
        if not _is_under(resolved, cache_root_resolved):
            continue
        size = directory_size(resolved)
        if resolved.exists():
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
        remove_top_asset_cache_entry(db_path, _text(row.get("asset_key")))
        mark_top_asset_cache_ref_retained(db_path, _text(row.get("asset_key")), retained=False)
        deleted_count += 1
        deleted_bytes += size
        total_size -= size
    return CacheCleanupResult(deleted_count=deleted_count, deleted_bytes=deleted_bytes, remaining_bytes=max(total_size, 0))


def safe_path_segment(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", _text(value)).strip("_")[:160] or "unknown"


def _platform_cache_name(value: object) -> str:
    platform = _platform_label(value)
    if platform == "抖音":
        return "douyin"
    if platform == "小红书":
        return "xhs"
    if platform == "B站":
        return "bilibili"
    return safe_path_segment(platform or "unknown")


def _platform_label(value: object) -> str:
    text = _text(value)
    lowered = text.lower()
    if "抖音" in text or "douyin" in lowered:
        return "抖音"
    if "小红书" in text or "xhs" in lowered or "xiaohongshu" in lowered:
        return "小红书"
    if "B站" in text or "哔哩" in text or "bilibili" in lowered:
        return "B站"
    return text


def _normalize_url(value: str) -> str:
    return _text(value).rstrip("/")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()
