"""Consolidate reusable Top asset material caches into one project library."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Iterable, Mapping

from .storage import init_db, upsert_top_asset_cache_entry
from .top_asset_cache import directory_size, safe_path_segment


PLATFORM_DIR_TO_LABEL = {
    "douyin": "抖音",
    "xhs": "小红书",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
}
PLATFORM_LABEL_TO_DIR = {
    "抖音": "douyin",
    "小红书": "xhs",
    "B站": "bilibili",
}


@dataclass
class TopAssetLibraryConsolidationResult:
    scanned_manifests: int = 0
    copied_count: int = 0
    updated_count: int = 0
    skipped_no_real_id: int = 0
    skipped_giant_only: int = 0
    skipped_missing_dir: int = 0
    copied_by_platform: Counter[str] = field(default_factory=Counter)
    scanned_by_source: Counter[str] = field(default_factory=Counter)
    copied_by_source: Counter[str] = field(default_factory=Counter)
    updated_by_source: Counter[str] = field(default_factory=Counter)
    skipped_by_reason: Counter[str] = field(default_factory=Counter)
    skip_samples: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _SourceManifest:
    path: Path
    item: dict[str, object]
    source_dir: Path
    source: str


@dataclass(frozen=True)
class _ReusableIdentity:
    platform_label: str
    platform_dir: str
    content_id: str
    asset_key: str


def consolidate_top_asset_library(
    *,
    db_path: Path,
    cache_root: Path = Path(".runtime/top-assets"),
    harvester_root: Path | None = None,
    ops_runtime_root: Path = Path(".runtime/harvester"),
    harvester_runtime_assets_root: Path | None = None,
    dry_run: bool = False,
) -> TopAssetLibraryConsolidationResult:
    """Copy reusable historical assets into ``cache_root/<platform>/<real-id>``.

    Only real platform work IDs are eligible. Douyin giant-engine material-only
    captures are intentionally skipped so they can be recaptured per run.
    """

    cache_root = Path(cache_root).expanduser().resolve()
    hroot = Path(harvester_root).expanduser().resolve() if harvester_root is not None else None
    ops_runtime_root = Path(ops_runtime_root).expanduser().resolve()
    runtime_assets_root = (
        Path(harvester_runtime_assets_root).expanduser().resolve()
        if harvester_runtime_assets_root is not None
        else (hroot / ".runtime" / "douyin-channel-type-classifier" / "assets" if hroot is not None else None)
    )
    result = TopAssetLibraryConsolidationResult()
    for source in _iter_manifest_sources(
        cache_root=cache_root,
        harvester_root=hroot,
        ops_runtime_root=ops_runtime_root,
        harvester_runtime_assets_root=runtime_assets_root,
    ):
        result.scanned_manifests += 1
        result.scanned_by_source[source.source] += 1
        identity = _reusable_identity_from_manifest(source)
        if identity is None:
            if _is_douyin_giant_only(source.item, source.path):
                result.skipped_giant_only += 1
                _record_skip(result, "giant_only", source.path)
            else:
                result.skipped_no_real_id += 1
                _record_skip(result, "no_real_id", source.path)
            continue
        if not source.source_dir.exists() or not source.source_dir.is_dir():
            result.skipped_missing_dir += 1
            _record_skip(result, "missing_dir", source.path)
            continue
        if _is_step15_asset_path(source.path) and not _has_local_media_file(source.item, source.source_dir):
            result.skipped_no_real_id += 1
            _record_skip(result, "no_media", source.path)
            continue
        target_dir = cache_root / identity.platform_dir / safe_path_segment(identity.content_id)
        if target_dir.exists():
            result.updated_count += 1
            result.updated_by_source[source.source] += 1
            if not dry_run:
                if target_dir.resolve() == source.source_dir.resolve():
                    _write_normalized_manifest(target_dir / "manifest.json", source, identity, target_dir)
                _record_entry(db_path, identity, target_dir, source.source)
                _migrate_legacy_cache_keys(db_path, identity)
            continue
        result.copied_count += 1
        result.copied_by_platform[identity.platform_dir] += 1
        result.copied_by_source[source.source] += 1
        if dry_run:
            continue
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source.source_dir, target_dir, dirs_exist_ok=True)
        _write_normalized_manifest(target_dir / "manifest.json", source, identity, target_dir)
        _record_entry(db_path, identity, target_dir, source.source)
        _migrate_legacy_cache_keys(db_path, identity)
    return result


def _iter_manifest_sources(
    *,
    cache_root: Path,
    harvester_root: Path | None,
    ops_runtime_root: Path,
    harvester_runtime_assets_root: Path | None,
) -> Iterable[_SourceManifest]:
    seen: set[Path] = set()
    roots = [cache_root, ops_runtime_root]
    if harvester_root is not None:
        roots.append(harvester_root / "output")
    if harvester_runtime_assets_root is not None:
        roots.append(harvester_runtime_assets_root)
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("**/manifest.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            for item in _manifest_items(path):
                source_dir = _source_dir_for_item(path, item)
                yield _SourceManifest(
                    path=path,
                    item=item,
                    source_dir=source_dir,
                    source=_source_name(
                        path,
                        cache_root=cache_root,
                        harvester_root=harvester_root,
                        ops_runtime_root=ops_runtime_root,
                        harvester_runtime_assets_root=harvester_runtime_assets_root,
                    ),
                )


def _manifest_items(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, Mapping):
        raw_items = payload.get("items") or payload.get("manifests") or [payload]
    else:
        raw_items = []
    return [dict(item) for item in raw_items if isinstance(item, Mapping)]


def _source_dir_for_item(path: Path, item: Mapping[str, object]) -> Path:
    raw = _text(item.get("asset_dir") or item.get("assetDir") or item.get("dir"))
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        return candidate.resolve()
    return path.parent.resolve()


def _reusable_identity_from_manifest(source: _SourceManifest) -> _ReusableIdentity | None:
    item = source.item
    platform_label = _platform_label(item.get("platform") or item.get("platformId") or item.get("platform_id") or _platform_hint_from_path(source.path))
    if platform_label == "抖音" and _is_douyin_giant_only_without_real_work_id(item):
        return None
    content_id = _content_id_for_platform(platform_label, item, source.path)
    if not platform_label or not content_id:
        asset_key_platform, asset_key_id = _asset_key_parts(item.get("asset_key"))
        platform_label = platform_label or asset_key_platform
        content_id = content_id or asset_key_id
    if not platform_label or not content_id:
        return None
    platform_dir = PLATFORM_LABEL_TO_DIR.get(platform_label)
    if not platform_dir:
        return None
    asset_key = f"{platform_label}::id::{content_id}"
    return _ReusableIdentity(platform_label=platform_label, platform_dir=platform_dir, content_id=content_id, asset_key=asset_key)


def _content_id_for_platform(platform_label: str, item: Mapping[str, object], path: Path) -> str:
    values = [
        item.get("content_id"),
        item.get("work_id"),
        item.get("id"),
        item.get("awemeId"),
        item.get("aweme_id"),
        item.get("noteId"),
        item.get("note_id"),
        item.get("bvid"),
        item.get("itemId"),
        item.get("item_id"),
        item.get("link"),
        item.get("content_url"),
        item.get("url"),
        item.get("asset_key"),
    ]
    if not _is_step15_asset_path(path):
        values.append(path.parent.name)
    if platform_label == "抖音":
        return _douyin_work_id(values)
    if platform_label == "小红书":
        return _xhs_note_id(values)
    if platform_label == "B站":
        return _bilibili_bvid(values)
    return ""


def _douyin_work_id(values: Iterable[object]) -> str:
    for value in values:
        text = _text(value)
        if not text:
            continue
        if re.fullmatch(r"\d{10,24}", text):
            return text
        for pattern in [
            r"/(?:video|note)/(\d{10,24})",
            r"(?:aweme_id|item_id|modal_id)=(\d{10,24})",
            r"(?:^|::)抖音::id::(\d{10,24})",
            r"(?:抖音|douyin)_id_(\d{10,24})",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
    return ""


def _xhs_note_id(values: Iterable[object]) -> str:
    for value in values:
        text = _text(value)
        if not text:
            continue
        match = re.search(r"::小红书::id::([0-9A-Za-z_-]{6,64})", text)
        if match:
            return match.group(1)
        match = re.search(r"(?:小红书|xhs)_id_([0-9A-Za-z_-]{6,64})", text, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"/explore/([0-9A-Za-z_-]{6,64})", text)
        if match:
            return match.group(1)
        if re.fullmatch(r"(?:[0-9a-f]{20,32}|note-[0-9A-Za-z_-]+)", text, re.IGNORECASE):
            return text
    return ""


def _bilibili_bvid(values: Iterable[object]) -> str:
    for value in values:
        text = _text(value)
        if not text:
            continue
        match = re.search(r"::B站::id::(BV[0-9A-Za-z]+)", text)
        if match:
            return match.group(1)
        match = re.search(r"(?:B站|bilibili)_id_(BV[0-9A-Za-z]+)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"/video/(BV[0-9A-Za-z]+)", text)
        if match:
            return match.group(1)
        if re.fullmatch(r"BV[0-9A-Za-z]+", text):
            return text
    return ""


def _asset_key_parts(value: object) -> tuple[str, str]:
    text = _text(value)
    match = re.match(r"^(抖音|小红书|B站)::id::(.+)$", text)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _is_douyin_giant_only(item: Mapping[str, object], path: Path) -> bool:
    platform = _platform_label(item.get("platform") or item.get("platformId") or _platform_hint_from_path(path))
    if platform != "抖音":
        return False
    if _is_douyin_giant_only_without_real_work_id(item):
        return True
    if _douyin_work_id(
        [
            item.get("asset_key"),
            item.get("content_id"),
            item.get("work_id"),
            item.get("id"),
            item.get("awemeId"),
            item.get("aweme_id"),
            item.get("link"),
            item.get("content_url"),
        ]
    ):
        return False
    text = "\n".join(
        _text(value)
        for value in [
            item.get("ad_material_id"),
            item.get("material_id"),
            item.get("ad_material_url"),
            item.get("ad_cover_url"),
            item.get("asset_dir"),
            item.get("video_path"),
            item.get("cover_path"),
            json.dumps(item.get("metadata") or {}, ensure_ascii=False),
        ]
    )
    return bool(re.search(r"\d{10,24}", text) or "giant" in text.lower() or "巨量" in text)


def _is_douyin_giant_only_without_real_work_id(item: Mapping[str, object]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    metadata_source = _text(metadata.get("source")).lower()
    explicit_real_id = _douyin_work_id(
        [
            item.get("asset_key"),
            item.get("content_id"),
            item.get("work_id"),
            item.get("id"),
            item.get("awemeId"),
            item.get("aweme_id"),
            item.get("itemId"),
            item.get("item_id"),
            item.get("link"),
            item.get("content_url"),
            item.get("url"),
        ]
    )
    if explicit_real_id:
        return False
    if metadata_source == "giant_asset":
        return True
    text = "\n".join(
        _text(value)
        for value in [
            item.get("ad_material_id"),
            item.get("material_id"),
            item.get("ad_material_url"),
            item.get("ad_cover_url"),
            json.dumps(item.get("metadata") or {}, ensure_ascii=False),
        ]
    )
    return bool(text and (re.search(r"\d{10,24}", text) or "giant" in text.lower() or "巨量" in text))


def _write_normalized_manifest(
    manifest_path: Path,
    source: _SourceManifest,
    identity: _ReusableIdentity,
    target_dir: Path,
) -> None:
    source_dir = source.source_dir.resolve()
    target_dir = target_dir.resolve()
    item = dict(source.item)
    item["status"] = _text(item.get("status")) or ("succeeded" if item.get("ok") else "succeeded")
    item["platform"] = identity.platform_label
    item["asset_key"] = identity.asset_key
    item["asset_dir"] = str(target_dir)
    item["cover_path"] = _remap_path(
        item.get("cover_path")
        or item.get("coverPath")
        or _first_path(item.get("imagePaths") or item.get("image_paths"))
        or _first_image_path(item),
        source_dir,
        target_dir,
    )
    item["video_path"] = _remap_path(item.get("video_path") or item.get("videoPath"), source_dir, target_dir)
    item["screenshots"] = [_remap_path(path, source_dir, target_dir) for path in _list_paths(item.get("screenshots") or item.get("screenshots_json") or item.get("imagePaths") or item.get("image_paths"))]
    item["frames"] = [_remap_path(path, source_dir, target_dir) for path in _list_paths(item.get("frames") or item.get("frames_json") or item.get("framePaths") or item.get("frame_paths"))]
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    item["metadata"] = {
        **dict(metadata),
        "ops_cache_source": source.source,
        "ops_cache_source_asset_dir": str(source_dir),
        "ops_cache_note": "历史缓存已归并到本项目素材库",
    }
    item["error_message"] = _text(item.get("error_message") or item.get("error") or item.get("errorMessage"))
    for key in [
        "ok",
        "dir",
        "assetDir",
        "coverPath",
        "videoPath",
        "imagePaths",
        "framePaths",
        "screenshots_json",
        "frames_json",
        "stdout",
        "stderr",
    ]:
        item.pop(key, None)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"items": [item]}, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_entry(db_path: Path, identity: _ReusableIdentity, target_dir: Path, source: str) -> None:
    upsert_top_asset_cache_entry(
        db_path,
        {
            "asset_key": identity.asset_key,
            "content_id": identity.content_id,
            "platform": identity.platform_label,
            "source": source,
            "asset_dir": str(target_dir.resolve()),
            "size_bytes": directory_size(target_dir),
            "last_used_batch_id": "",
        },
    )


def _migrate_legacy_cache_keys(db_path: Path, identity: _ReusableIdentity) -> None:
    legacy_keys = _legacy_cache_keys(identity)
    if not legacy_keys:
        return
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        for legacy_key in legacy_keys:
            conn.execute(
                "update top_asset_cache_refs set asset_key = ? where asset_key = ?",
                (identity.asset_key, legacy_key),
            )
            conn.execute("delete from top_asset_cache_entries where asset_key = ?", (legacy_key,))
        conn.commit()


def _legacy_cache_keys(identity: _ReusableIdentity) -> list[str]:
    if identity.platform_label == "抖音":
        return [f"douyin:work:{identity.content_id}"]
    return []


def _source_name(
    path: Path,
    *,
    cache_root: Path,
    harvester_root: Path | None,
    ops_runtime_root: Path,
    harvester_runtime_assets_root: Path | None,
) -> str:
    try:
        path.resolve().relative_to(cache_root.resolve())
        return "ops_top_assets_history"
    except ValueError:
        pass
    try:
        path.resolve().relative_to(ops_runtime_root.resolve())
        return "ops_harvester_manifest_history"
    except ValueError:
        pass
    if harvester_runtime_assets_root is not None:
        try:
            path.resolve().relative_to(harvester_runtime_assets_root.resolve())
            return "harvester_runtime_classifier_history"
        except ValueError:
            pass
    if harvester_root is not None:
        try:
            path.resolve().relative_to((harvester_root / "output" / "step15-assets").resolve())
            return "harvester_step15_assets_history"
        except ValueError:
            pass
        try:
            path.resolve().relative_to((harvester_root / "output").resolve())
            return "harvester_output_history"
        except ValueError:
            pass
    return "ops_harvester_manifest_history"


def _platform_hint_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        label = PLATFORM_DIR_TO_LABEL.get(part.lower())
        if label:
            return label
    return ""


def _record_skip(result: TopAssetLibraryConsolidationResult, reason: str, path: Path) -> None:
    result.skipped_by_reason[reason] += 1
    samples = result.skip_samples.setdefault(reason, [])
    if len(samples) < 10:
        samples.append(str(path))


def _is_step15_asset_path(path: Path) -> bool:
    return "step15-assets" in path.parts


def _has_local_media_file(item: Mapping[str, object], source_dir: Path) -> bool:
    paths = [
        item.get("video_path"),
        item.get("videoPath"),
        item.get("cover_path"),
        item.get("coverPath"),
        *_list_paths(item.get("imagePaths") or item.get("image_paths")),
        *_list_paths(item.get("screenshots") or item.get("screenshots_json")),
        *_list_paths(item.get("framePaths") or item.get("frame_paths") or item.get("frames") or item.get("frames_json")),
    ]
    assets = item.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, Mapping):
                continue
            kind = _text(asset.get("kind") or asset.get("type")).lower()
            if kind in {"video", "image", "screenshot", "cover", "thumbnail", "frame"}:
                paths.append(asset.get("path") or asset.get("url"))
    for value in paths:
        text = _text(value)
        if not text or re.match(r"^https?://", text, re.IGNORECASE):
            continue
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = source_dir / path
        try:
            if path.exists() and path.is_file() and path.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def _platform_label(value: object) -> str:
    text = _text(value)
    lowered = text.lower()
    if text in {"抖音", "douyin"} or "douyin" in lowered:
        return "抖音"
    if text in {"小红书", "xhs", "xiaohongshu"} or "xhs" in lowered or "xiaohongshu" in lowered:
        return "小红书"
    if text in {"B站", "bilibili"} or "bilibili" in lowered or "哔哩" in text:
        return "B站"
    return ""


def _first_image_path(item: Mapping[str, object]) -> str:
    assets = item.get("assets")
    if not isinstance(assets, list):
        return ""
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        kind = _text(asset.get("kind") or asset.get("type")).lower()
        if kind in {"image", "screenshot", "cover", "thumbnail"}:
            return _text(asset.get("path") or asset.get("url"))
    return ""


def _first_path(value: object) -> str:
    paths = _list_paths(value)
    return paths[0] if paths else ""


def _list_paths(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except Exception:
            return [value] if value else []
        return _list_paths(loaded)
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return []


def _remap_path(value: object, source_dir: Path, target_dir: Path) -> str:
    text = _text(value)
    if not text:
        return ""
    path = Path(text).expanduser()
    try:
        relative = path.resolve().relative_to(source_dir.resolve())
    except Exception:
        return text
    return str((target_dir / relative).resolve())


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
