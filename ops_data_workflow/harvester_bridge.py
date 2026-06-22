"""Bridge ops-data-workflow Top assets to sibling harvester-THS CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Callable, Mapping, Sequence

import pandas as pd

from .storage import (
    list_harvester_asset_manifests,
    list_top_asset_cache_entries,
    persist_harvester_asset_jobs,
    persist_harvester_asset_manifests,
    upsert_top_asset_cache_entry,
    upsert_top_asset_cache_ref,
)
from .top_asset_cache import (
    asset_key_for_job,
    directory_size,
    has_reusable_asset_identity,
    reusable_asset_cache_path,
    reusable_asset_key_for_job,
)


HARVESTER_NPM_SCRIPT = "materials:cache-topn"
PLATFORM_LOGIN_SCRIPTS = {
    "小红书": "src/login-xhs.mjs",
    "xhs": "src/login-xhs.mjs",
    "抖音": "src/login-douyin.mjs",
    "douyin": "src/login-douyin.mjs",
    "B站": "src/login-bilibili.mjs",
    "bilibili": "src/login-bilibili.mjs",
}
METRIC_COLUMNS = ["spend", "impressions", "clicks", "activations", "first_pay_count"]
PROGRESS_LOG_PREFIX = "__HARVESTER_PROGRESS__"
PLATFORM_PROFILE_DIRS = [".douyin-profile", ".xhs-profile", ".bilibili-profile"]


@dataclass(frozen=True)
class HarvesterRunResult:
    ok: bool
    message: str
    jobs_path: Path
    manifest_path: Path
    job_count: int
    succeeded_count: int = 0
    failed_count: int = 0
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class HarvesterProgressEvent:
    platform: str
    stage: str
    phase: str
    item_id: str
    completed: int
    total: int
    remaining_count: int
    action: str
    updated_at: str


def resolve_harvester_root(
    *,
    workspace_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    values = env if env is not None else os.environ
    configured = str(values.get("HARVESTER_ROOT", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path(workspace_root) if workspace_root is not None else Path.cwd()
    return (root.resolve().parent / "harvester-THS").resolve()


def build_asset_jobs(
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    douyin_resolver: Callable[[str], Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    if top_content is None or top_content.empty:
        return []
    jobs: list[dict[str, object]] = []
    for _, row in top_content.iterrows():
        identity = _text(row.get("content_identity_key")) or _fallback_identity(row)
        platform = _text(row.get("platform") or row.get("platform_group")) or _platform_from_channel(row.get("channel"))
        platform_cache_name = _platform_cache_name(platform)
        if platform_cache_name == "douyin":
            content_id = _douyin_work_id_from_row(row)
        else:
            content_id = _text(row.get("content_id") or row.get("work_id"))
            content_id = content_id or _text(row.get("material_id"))
        content_url = _text(row.get("work_url") or row.get("content_url"))
        job = {
            "job_id": _job_id(identity),
            "batch_id": _text(batch_id),
            "platform": platform,
            "channel": _text(row.get("channel")),
            "content_identity_key": identity,
            "content_id": content_id,
            "content_url": content_url,
            "title": _text(row.get("title")),
            "account": _text(row.get("account")),
            "ad_material_id": _text(row.get("ad_material_id") or row.get("material_id")) if platform_cache_name == "douyin" else "",
            "ad_material_url": _text(row.get("ad_material_url")) if platform_cache_name == "douyin" else "",
            "ad_cover_url": _text(row.get("ad_cover_url")) if platform_cache_name == "douyin" else "",
            "period_start": _text(row.get("period_start")),
            "period_end": _text(row.get("period_end")),
            "metrics": {column: _float(row.get(column)) for column in METRIC_COLUMNS},
        }
        if platform_cache_name == "douyin":
            job = _normalize_douyin_identity(job)
            job = _resolve_douyin_job(job, douyin_resolver)
        jobs.append(job)
    return jobs


def build_asset_jobs_to_capture(db_path: Path, batch_id: str, top_content: pd.DataFrame) -> list[dict[str, object]]:
    jobs = build_asset_jobs(batch_id, top_content)
    return _build_asset_jobs_to_capture_from_jobs(db_path, jobs)


def _build_asset_jobs_to_capture_from_jobs(db_path: Path, jobs: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    if not jobs:
        return []
    manifests = list_harvester_asset_manifests(db_path)
    captured_ids = set()
    if not manifests.empty:
        scoped = manifests[
            manifests.get("status", pd.Series(dtype=object)).fillna("").astype(str).eq("succeeded")
            & manifests.get("asset_dir", pd.Series(dtype=object)).fillna("").astype(str).str.strip().ne("")
        ]
        captured_ids = set(scoped.get("job_id", pd.Series(dtype=object)).fillna("").astype(str))
    cached_asset_keys = _cached_asset_keys(db_path)
    pending: list[dict[str, object]] = []
    for job in jobs:
        if not has_reusable_asset_identity(job):
            pending.append(dict(job))
            continue
        if str(job.get("job_id", "")) in captured_ids:
            continue
        if reusable_asset_key_for_job(job) in cached_asset_keys:
            continue
        pending.append(dict(job))
    return pending


def cache_existing_harvester_assets_for_batch(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    cache_root: Path | None = None,
    harvester_root: Path | None = None,
    jobs_path: Path | None = None,
    manifest_path: Path | None = None,
    douyin_resolver: Callable[[str], Mapping[str, object]] | None = None,
) -> int:
    """Copy already captured harvester assets into ops runtime and record them for this batch."""
    jobs = build_asset_jobs(batch_id, top_content, douyin_resolver=douyin_resolver)
    if not jobs:
        return 0
    root = Path(cache_root) if cache_root is not None else Path(".runtime/top-assets")
    root = root.expanduser().resolve()
    hroot = Path(harvester_root) if harvester_root is not None else resolve_harvester_root()
    job_file = Path(jobs_path) if jobs_path is not None else Path(".runtime/harvester") / str(batch_id) / "jobs.jsonl"
    manifest_file = Path(manifest_path) if manifest_path is not None else Path(".runtime/harvester") / str(batch_id) / "manifest.json"
    daily_manifests = _daily_harvester_manifests_for_jobs(jobs, hroot)
    previous_capture_manifests = _previous_ops_capture_manifests_for_jobs(jobs, root)
    reused_jobs: list[dict[str, object]] = []
    reused_manifests: list[dict[str, object]] = []
    local_manifests = _local_cached_manifests_for_jobs(db_path, jobs)
    for job in jobs:
        job_id = _text(job.get("job_id"))
        manifest = daily_manifests.get(job_id) or previous_capture_manifests.get(job_id) or local_manifests.get(job_id)
        if not manifest:
            continue
        cached_manifest = _copy_manifest_assets_to_ops_cache(manifest, job, root)
        if not cached_manifest:
            continue
        cached_manifest["metadata"] = {
            **_metadata_from_manifest(cached_manifest),
            "ops_cache_note": "复用harvester每日缓存",
            "ops_cache_source": "harvester_daily_cache",
        }
        reused = dict(job)
        reused["status"] = "succeeded"
        reused["error_message"] = ""
        reused_jobs.append(reused)
        reused_manifests.append(cached_manifest)

    manifests = list_harvester_asset_manifests(db_path)
    if not manifests.empty:
        succeeded = manifests[
            manifests.get("status", pd.Series(dtype=object)).fillna("").astype(str).eq("succeeded")
            & manifests.get("asset_dir", pd.Series(dtype=object)).fillna("").astype(str).str.strip().ne("")
        ].copy()
    else:
        succeeded = pd.DataFrame()
    if not succeeded.empty:
        existing_ids = {_text(item.get("job_id")) for item in reused_manifests}
        manifest_by_job = {
            _text(row.get("job_id")): row.to_dict()
            for _, row in succeeded.iterrows()
            if _text(row.get("job_id")) and _text(row.get("job_id")) not in existing_ids
        }
        for job in jobs:
            if not has_reusable_asset_identity(job):
                continue
            job_id = _text(job.get("job_id"))
            existing = manifest_by_job.get(job_id)
            if not existing:
                continue
            cached_manifest = _copy_manifest_assets_to_ops_cache(existing, job, root)
            if not cached_manifest:
                continue
            reused = dict(job)
            reused["status"] = "succeeded"
            reused["error_message"] = ""
            reused_jobs.append(reused)
            reused_manifests.append(cached_manifest)
    if not reused_jobs:
        return 0
    persist_harvester_asset_jobs(
        db_path,
        batch_id,
        reused_jobs,
        status="succeeded",
        harvester_root=hroot,
        jobs_path=job_file,
        manifest_path=manifest_file,
    )
    persist_harvester_asset_manifests(db_path, batch_id, reused_manifests)
    _record_cache_entries(db_path, batch_id, reused_jobs, reused_manifests)
    _write_manifest_json(manifest_file, _manifest_records_for_batch(db_path, batch_id))
    return len(reused_jobs)


def write_jobs_jsonl(jobs: Sequence[Mapping[str, object]], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for job in jobs:
            handle.write(json.dumps(dict(job), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return path


def load_asset_manifests(path: Path) -> list[dict[str, object]]:
    path = Path(path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    if isinstance(payload, list):
        items = payload
    else:
        items = payload.get("items") or payload.get("manifests") or []
    return [_normalize_manifest_item(item) for item in items if isinstance(item, Mapping)]


def harvester_cli_available(harvester_root: Path) -> bool:
    package_json = Path(harvester_root) / "package.json"
    if not package_json.exists():
        return False
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8") or "{}")
    except Exception:
        return False
    scripts = payload.get("scripts")
    return isinstance(scripts, Mapping) and HARVESTER_NPM_SCRIPT in scripts


def run_harvester_asset_capture(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    harvester_root: Path | None = None,
    runtime_root: Path | None = None,
    cache_root: Path | None = None,
    npm_command: str = "npm",
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    progress_callback: Callable[[HarvesterProgressEvent], None] | None = None,
) -> HarvesterRunResult:
    root = Path(harvester_root) if harvester_root is not None else resolve_harvester_root()
    root = root.expanduser().resolve()
    runtime_dir = Path(runtime_root) if runtime_root is not None else Path(".runtime/harvester")
    runtime_dir = runtime_dir.expanduser().resolve()
    stable_cache_root = Path(cache_root) if cache_root is not None else Path(".runtime/top-assets")
    stable_cache_root = stable_cache_root.expanduser().resolve()
    run_id = _new_run_id()
    capture_root = _ops_capture_root(stable_cache_root, batch_id, run_id=run_id)
    batch_runtime = runtime_dir / str(batch_id)
    run_runtime = batch_runtime / run_id
    jobs_path = run_runtime / "jobs.jsonl"
    run_manifest_path = run_runtime / "manifest.json"
    batch_manifest_path = batch_runtime / "manifest.json"
    douyin_resolver = lambda value: resolve_douyin_share_with_harvester(value, harvester_root=root)
    cache_existing_harvester_assets_for_batch(
        db_path,
        batch_id,
        top_content,
        cache_root=stable_cache_root,
        harvester_root=root,
        jobs_path=batch_runtime / "jobs.jsonl",
        manifest_path=batch_manifest_path,
        douyin_resolver=douyin_resolver,
    )
    resolved_jobs = build_asset_jobs(batch_id, top_content, douyin_resolver=douyin_resolver)
    jobs = _build_asset_jobs_to_capture_from_jobs(db_path, resolved_jobs)
    write_jobs_jsonl(jobs, jobs_path)
    persist_harvester_asset_jobs(
        db_path,
        batch_id,
        jobs,
        status="queued",
        harvester_root=root,
        jobs_path=jobs_path,
        manifest_path=run_manifest_path,
    )

    if not jobs:
        return HarvesterRunResult(
            ok=True,
            message="当前周期没有需要抓取的 Top 素材。",
            jobs_path=jobs_path,
            manifest_path=batch_manifest_path,
            job_count=0,
        )

    if not harvester_cli_available(root):
        message = f"未找到 harvester TopN 素材采集 CLI：npm run {HARVESTER_NPM_SCRIPT}"
        persist_harvester_asset_jobs(
            db_path,
            batch_id,
            jobs,
            status="failed",
            harvester_root=root,
            jobs_path=jobs_path,
            manifest_path=run_manifest_path,
            error_message=message,
        )
        return HarvesterRunResult(False, message, jobs_path, batch_manifest_path, len(jobs), failed_count=len(jobs))

    command = [
        npm_command,
        "run",
        HARVESTER_NPM_SCRIPT,
        "--",
        "--input",
        str(jobs_path),
        "--out",
        str(run_manifest_path),
        "--root",
        str(capture_root),
    ]
    target_date = _target_date_from_jobs(jobs)
    if target_date:
        command.extend(["--target-date", target_date])
    run = runner or subprocess.run
    _prepare_ops_capture_root(capture_root, root)
    env = _harvester_capture_env(root)
    if runner is None:
        completed = _run_command_with_progress(command, cwd=root, env=env, progress_callback=progress_callback)
    else:
        completed = run(command, cwd=str(root), text=True, capture_output=True, env=env)
        _emit_progress_from_text(str(completed.stdout or ""), progress_callback)
        _emit_progress_from_text(str(completed.stderr or ""), progress_callback)
    if completed.returncode != 0:
        raw_message = (completed.stderr or completed.stdout or "harvester CLI 执行失败").strip()
        message = _explain_harvester_failure(raw_message, jobs)
        persist_harvester_asset_jobs(
            db_path,
            batch_id,
            jobs,
            status="failed",
            harvester_root=root,
            jobs_path=jobs_path,
            manifest_path=run_manifest_path,
            error_message=message,
        )
        return HarvesterRunResult(
            False,
            message,
            jobs_path,
            batch_manifest_path,
            len(jobs),
            failed_count=len(jobs),
            returncode=int(completed.returncode),
            stdout=str(completed.stdout or ""),
            stderr=str(completed.stderr or ""),
        )

    manifests = _cache_cli_manifests(
        load_asset_manifests(run_manifest_path),
        jobs,
        stable_cache_root,
    )
    persist_harvester_asset_manifests(db_path, batch_id, manifests)
    _write_manifest_json(batch_manifest_path, _manifest_records_for_batch(db_path, batch_id))
    manifest_by_id = {str(item.get("job_id", "")): item for item in manifests}
    refreshed_jobs: list[dict[str, object]] = []
    for job in jobs:
        item = manifest_by_id.get(str(job.get("job_id", "")))
        refreshed = dict(job)
        if item is None:
            refreshed["status"] = "failed"
            refreshed["error_message"] = "harvester manifest 未返回该素材，请检查 TopN CLI 输入解析或重新采集。"
        else:
            refreshed["status"] = _text(item.get("status")) or "failed"
            refreshed["error_message"] = _text(item.get("error_message"))
        refreshed_jobs.append(refreshed)
    persist_harvester_asset_jobs(
        db_path,
        batch_id,
        refreshed_jobs,
        status="succeeded",
        harvester_root=root,
        jobs_path=jobs_path,
        manifest_path=run_manifest_path,
    )
    _record_cache_entries(db_path, batch_id, refreshed_jobs, manifests)
    failed_count = sum(1 for item in refreshed_jobs if _text(item.get("status")) != "succeeded")
    succeeded_count = len(refreshed_jobs) - failed_count
    return HarvesterRunResult(
        ok=failed_count == 0,
        message="harvester 素材抓取完成。" if failed_count == 0 else f"harvester 素材抓取完成，失败 {failed_count} 条。",
        jobs_path=jobs_path,
        manifest_path=batch_manifest_path,
        job_count=len(jobs),
        succeeded_count=succeeded_count,
        failed_count=failed_count,
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
    )


def login_command_for_platform(platform: str, *, harvester_root: Path | None = None, node_command: str = "node") -> list[str]:
    root = Path(harvester_root) if harvester_root is not None else resolve_harvester_root()
    script = PLATFORM_LOGIN_SCRIPTS.get(_text(platform))
    if not script:
        raise ValueError(f"不支持登录的平台：{platform}")
    return [node_command, str(root / script)]


def resolve_douyin_share_with_harvester(
    value: str,
    *,
    harvester_root: Path | None = None,
    node_command: str = "node",
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    text = _text(value)
    if not text:
        return {}
    root = Path(harvester_root) if harvester_root is not None else resolve_harvester_root()
    script = root / "src" / "resolve-douyin-share.mjs"
    if not script.exists():
        return {}
    command = [node_command, str(script), "--json", "--input", text]
    run = runner or subprocess.run
    completed = run(command, cwd=str(root), text=True, capture_output=True)
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(str(completed.stdout or "{}"))
    except Exception:
        return {}
    data = payload.get("data") if isinstance(payload, Mapping) else {}
    return dict(data) if isinstance(data, Mapping) else {}


def _daily_harvester_manifests_for_jobs(
    jobs: Sequence[Mapping[str, object]],
    harvester_root: Path,
) -> dict[str, dict[str, object]]:
    output_root = Path(harvester_root) / "output"
    if not output_root.exists():
        return {}
    by_job: dict[str, dict[str, object]] = {}
    manifest_paths = list(output_root.glob("*/*/*/manifest.json"))
    for job in jobs:
        if not has_reusable_asset_identity(job):
            continue
        job_id = _text(job.get("job_id"))
        platform_dir = _platform_cache_name(_text(job.get("platform")))
        content_id = _text(job.get("content_id"))
        date_hint = _text(job.get("period_end"))
        preferred = []
        if date_hint and platform_dir and content_id:
            preferred.append(output_root / date_hint / platform_dir / _safe_path_segment(content_id) / "manifest.json")
        candidates = [path for path in preferred if path.exists()]
        candidates.extend(path for path in manifest_paths if path not in candidates)
        for path in candidates:
            item = _load_daily_manifest(path)
            if not item or _text(item.get("status")) != "succeeded":
                continue
            if not _manifest_is_usable_for_job(item, job, path):
                continue
            if not _daily_manifest_matches_job(item, job, path):
                continue
            by_job[job_id] = item
            break
    return by_job


def _previous_ops_capture_manifests_for_jobs(
    jobs: Sequence[Mapping[str, object]],
    cache_root: Path,
) -> dict[str, dict[str, object]]:
    output_root = Path(cache_root) / "_capture-runs"
    if not output_root.exists():
        return {}
    manifest_paths = list(output_root.glob("**/output/*/*/*/manifest.json"))
    by_job: dict[str, dict[str, object]] = {}
    for job in jobs:
        if not has_reusable_asset_identity(job):
            continue
        job_id = _text(job.get("job_id"))
        platform_dir = _platform_cache_name(_text(job.get("platform")))
        content_id = _safe_path_segment(_text(job.get("content_id")))
        if not job_id or not platform_dir or not content_id or content_id == "unknown":
            continue
        preferred = [
            path
            for path in manifest_paths
            if path.parent.name == content_id and path.parent.parent.name == platform_dir
        ]
        candidates = preferred + [path for path in manifest_paths if path not in preferred]
        for path in candidates:
            item = _load_daily_manifest(path)
            if not item or _text(item.get("status")) != "succeeded":
                continue
            if not _manifest_is_usable_for_job(item, job, path):
                continue
            if not _daily_manifest_matches_job(item, job, path):
                continue
            by_job[job_id] = item
            break
    return by_job


def _load_daily_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    platform = _platform_from_daily_platform_id(_text(payload.get("platformId") or payload.get("platform")))
    asset_dir = _text(payload.get("asset_dir") or payload.get("assetDir") or payload.get("dir") or Path(path).parent)
    screenshots = _json_or_list(payload.get("screenshots") or payload.get("screenshots_json"))
    image_paths = _json_or_list(payload.get("imagePaths") or payload.get("image_paths"))
    frame_paths = _json_or_list(payload.get("framePaths") or payload.get("frames") or payload.get("frames_json"))
    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    screenshots = screenshots + image_paths + _asset_paths(assets, {"image", "screenshot", "cover", "thumbnail"})
    frames = frame_paths + _asset_paths(assets, {"frame"})
    video_path = _text(payload.get("video_path") or payload.get("videoPath")) or _first_asset_path(assets, {"video"})
    cover_path = _text(payload.get("cover_path") or payload.get("coverPath")) or (screenshots[0] if screenshots else "")
    ok = bool(payload.get("ok")) or _text(payload.get("status")) == "succeeded"
    return {
        "job_id": _text(payload.get("job_id")),
        "status": "succeeded" if ok else "failed",
        "platform": platform,
        "content_id": _text(payload.get("id") or payload.get("content_id") or payload.get("noteId") or payload.get("bvid") or payload.get("itemId")),
        "content_url": _text(payload.get("link") or payload.get("content_url")),
        "title": _text(payload.get("title")),
        "asset_dir": asset_dir,
        "cover_path": cover_path,
        "video_path": video_path,
        "screenshots": screenshots,
        "frames": frames,
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {},
        "error_message": _text(payload.get("error") or payload.get("error_message") or payload.get("errorMessage")),
    }


def _daily_manifest_matches_job(manifest: Mapping[str, object], job: Mapping[str, object], path: Path) -> bool:
    manifest_platform = _platform_cache_name(_text(manifest.get("platform")))
    job_platform = _platform_cache_name(_text(job.get("platform")))
    if manifest_platform and job_platform and manifest_platform != job_platform:
        return False
    job_id = _text(job.get("job_id"))
    if _text(manifest.get("job_id")) and _text(manifest.get("job_id")) == job_id:
        return True
    content_id = _safe_path_segment(_text(job.get("content_id")))
    if content_id and content_id != "unknown" and path.parent.name == content_id:
        return True
    manifest_id = _safe_path_segment(_text(manifest.get("content_id")))
    if content_id and manifest_id and content_id == manifest_id:
        return True
    manifest_url_id = _safe_path_segment(_extract_douyin_work_id(manifest.get("content_url")))
    job_url_id = _safe_path_segment(_extract_douyin_work_id(job.get("content_url")))
    if content_id and manifest_url_id and content_id == manifest_url_id:
        return True
    return bool(job_url_id and manifest_url_id and job_url_id == manifest_url_id)


def _manifest_is_usable_for_job(manifest: Mapping[str, object], job: Mapping[str, object], path: Path) -> bool:
    if _manifest_has_invalid_visual_fallback(manifest):
        return False
    if _platform_cache_name(_text(job.get("platform"))) != "douyin":
        return True
    job_id = _text(job.get("content_id"))
    if not job_id:
        return False
    manifest_id = _text(manifest.get("content_id"))
    manifest_url_id = _extract_douyin_work_id(manifest.get("content_url"))
    path_name = path.parent.name if path.name == "manifest.json" else path.name
    path_id = _extract_douyin_work_id(path_name) or path_name
    ids = {value for value in [manifest_id, manifest_url_id, path_id] if value}
    if manifest_id and manifest_url_id and manifest_id != manifest_url_id:
        return False
    return job_id in ids


def _manifest_has_invalid_visual_fallback(manifest: Mapping[str, object]) -> bool:
    text = "\n".join(
        _text(value)
        for value in [
            manifest.get("error_message"),
            manifest.get("error"),
            manifest.get("fallbackReason"),
            manifest.get("source"),
            json.dumps(manifest.get("metadata") or {}, ensure_ascii=False),
        ]
    )
    fallback = manifest.get("fallback")
    if isinstance(fallback, Mapping):
        text = f"{text}\n{json.dumps(dict(fallback), ensure_ascii=False)}"
        fallback_kind = _text(fallback.get("kind"))
        extracted_media = bool(fallback.get("extractedMedia") or fallback.get("extracted_media"))
        if fallback_kind == "douyin-note-visual" and not extracted_media and "yt-dlp" in text:
            return True
    return any(token in text for token in ["视频不存在", "观看的视频不存在", "内容不存在", "页面不存在", "404"])


def _platform_from_daily_platform_id(value: str) -> str:
    text = _text(value).lower()
    if text in {"douyin", "抖音"}:
        return "抖音"
    if text in {"xhs", "xiaohongshu", "小红书"}:
        return "小红书"
    if text in {"bilibili", "b站", "哔哩哔哩"}:
        return "B站"
    return _text(value)


def _asset_paths(assets: object, kinds: set[str]) -> list[str]:
    if not isinstance(assets, list):
        return []
    paths: list[str] = []
    for item in assets:
        if not isinstance(item, Mapping):
            continue
        kind = _text(item.get("kind") or item.get("type")).lower()
        if kind not in kinds:
            continue
        path = _text(item.get("path") or item.get("url"))
        if path:
            paths.append(path)
    return paths


def _first_asset_path(assets: object, kinds: set[str]) -> str:
    paths = _asset_paths(assets, kinds)
    return paths[0] if paths else ""


def _normalize_manifest_item(item: Mapping[str, object]) -> dict[str, object]:
    platform = _text(item.get("platform"))
    error_message = _text(item.get("error_message"))
    normalized = {
        "job_id": _text(item.get("job_id")),
        "status": _text(item.get("status")) or "unknown",
        "platform": platform,
        "asset_key": _text(item.get("asset_key")),
        "asset_dir": _text(item.get("asset_dir")),
        "cover_path": _text(item.get("cover_path")),
        "video_path": _text(item.get("video_path")),
        "screenshots": list(item.get("screenshots") or []),
        "frames": list(item.get("frames") or []),
        "metadata": item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {},
        "error_message": _explain_harvester_failure(error_message, [{"platform": platform}]) if error_message else "",
    }
    if normalized["status"] == "succeeded" and not normalized["asset_dir"]:
        normalized["status"] = "failed"
        normalized["error_message"] = "harvester manifest 缺少素材目录，请重新补采该素材。"
    return normalized


def _copy_manifest_assets_to_ops_cache(
    manifest: Mapping[str, object],
    job: Mapping[str, object],
    cache_root: Path,
) -> dict[str, object]:
    if not has_reusable_asset_identity(job):
        return {}
    if not _manifest_is_usable_for_job(manifest, job, Path(_text(manifest.get("asset_dir")) or ".")):
        return {}
    source_dir = Path(_text(manifest.get("asset_dir")))
    if not source_dir.exists() or not source_dir.is_dir():
        return {}
    asset_key = reusable_asset_key_for_job(job)
    source_resolved = source_dir.resolve()
    target_dir = reusable_asset_cache_path(cache_root, job)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if source_resolved != target_dir.resolve():
        shutil.copytree(source_resolved, target_dir, dirs_exist_ok=True)
    path_map = _copied_path_map(source_resolved, target_dir.resolve())
    metadata = _metadata_from_manifest(manifest)
    metadata.setdefault("ops_cache_source_asset_dir", str(source_resolved))
    metadata["ops_cache_note"] = "已复制到本项目缓存"
    cached_manifest = {
        "job_id": _text(job.get("job_id")) or _text(manifest.get("job_id")),
        "status": "succeeded",
        "platform": _text(job.get("platform")) or _text(manifest.get("platform")),
        "asset_key": asset_key,
        "asset_dir": str(target_dir.resolve()),
        "cover_path": _remap_asset_path(manifest.get("cover_path"), path_map),
        "video_path": _remap_asset_path(manifest.get("video_path"), path_map),
        "screenshots": [
            _remap_asset_path(path, path_map)
            for path in _json_or_list(manifest.get("screenshots_json") if "screenshots_json" in manifest else manifest.get("screenshots"))
        ],
        "frames": [
            _remap_asset_path(path, path_map)
            for path in _json_or_list(manifest.get("frames_json") if "frames_json" in manifest else manifest.get("frames"))
        ],
        "metadata": metadata,
        "error_message": "",
    }
    _write_manifest_json(target_dir / "manifest.json", [cached_manifest])
    return cached_manifest


def _cache_cli_manifests(
    manifests: Sequence[Mapping[str, object]],
    jobs: Sequence[Mapping[str, object]],
    cache_root: Path,
    *,
    cleanup_source_under: Path | None = None,
) -> list[dict[str, object]]:
    job_by_id = {_text(job.get("job_id")): job for job in jobs if _text(job.get("job_id"))}
    cached: list[dict[str, object]] = []
    for item in manifests:
        if _text(item.get("status")) != "succeeded" or not _text(item.get("asset_dir")):
            cached.append(dict(item))
            continue
        job = job_by_id.get(_text(item.get("job_id")), {})
        cached_item = _copy_manifest_assets_to_ops_cache(item, job, cache_root)
        if cached_item and cleanup_source_under is not None:
            _remove_source_dir_if_under(item.get("asset_dir"), cleanup_source_under, cached_item.get("asset_dir"))
        cached.append(cached_item or dict(item))
    return cached


def _write_manifest_json(path: Path, manifests: Sequence[Mapping[str, object]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"items": [_manifest_json_item(item) for item in manifests]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _manifest_records_for_batch(db_path: Path, batch_id: str) -> list[dict[str, object]]:
    records = list_harvester_asset_manifests(db_path, batch_id=batch_id)
    if records.empty:
        return []
    return [row.to_dict() for _, row in records.iterrows()]


def _manifest_json_item(item: Mapping[str, object]) -> dict[str, object]:
    metadata = _metadata_from_manifest(item)
    metadata.pop("ops_cache_source_asset_dir", None)
    return {
        "job_id": _text(item.get("job_id")),
        "status": _text(item.get("status")) or "unknown",
        "platform": _text(item.get("platform")),
        "asset_key": _text(item.get("asset_key")),
        "asset_dir": _text(item.get("asset_dir")),
        "cover_path": _text(item.get("cover_path")),
        "video_path": _text(item.get("video_path")),
        "screenshots": _json_or_list(item.get("screenshots_json") if "screenshots_json" in item else item.get("screenshots")),
        "frames": _json_or_list(item.get("frames_json") if "frames_json" in item else item.get("frames")),
        "metadata": metadata,
        "error_message": _text(item.get("error_message")),
    }


def _cached_asset_keys(db_path: Path) -> set[str]:
    entries = list_top_asset_cache_entries(db_path)
    if entries.empty:
        return set()
    usable = entries[
        entries.get("asset_dir", pd.Series(dtype=object)).fillna("").astype(str).str.strip().ne("")
    ].copy()
    keys = set()
    for _, row in usable.iterrows():
        asset_dir = Path(_text(row.get("asset_dir")))
        if asset_dir.exists():
            keys.add(_text(row.get("asset_key")))
    return {key for key in keys if key}


def _local_cached_manifests_for_jobs(
    db_path: Path,
    jobs: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    entries = list_top_asset_cache_entries(db_path)
    if entries.empty:
        return {}
    entry_by_key = {
        _text(row.get("asset_key")): row.to_dict()
        for _, row in entries.iterrows()
        if _text(row.get("asset_key")) and _text(row.get("asset_dir"))
    }
    by_job: dict[str, dict[str, object]] = {}
    for job in jobs:
        if not has_reusable_asset_identity(job):
            continue
        key = reusable_asset_key_for_job(job)
        entry = entry_by_key.get(key)
        if not entry:
            continue
        asset_dir = Path(_text(entry.get("asset_dir")))
        if not asset_dir.exists():
            continue
        manifest_path = asset_dir / "manifest.json"
        loaded = load_asset_manifests(manifest_path)
        if loaded:
            manifest = loaded[0]
        else:
            manifest = {
                "job_id": _text(job.get("job_id")),
                "status": "succeeded",
                "platform": _text(job.get("platform")),
                "asset_key": key,
                "asset_dir": str(asset_dir),
                "cover_path": "",
                "video_path": "",
                "screenshots": [],
                "frames": [],
                "metadata": {},
                "error_message": "",
            }
        if not _manifest_is_usable_for_job(manifest, job, asset_dir / "manifest.json"):
            continue
        manifest["job_id"] = _text(job.get("job_id"))
        manifest["asset_key"] = key
        by_job[_text(job.get("job_id"))] = manifest
    return by_job


def _record_cache_entries(
    db_path: Path,
    batch_id: str,
    jobs: Sequence[Mapping[str, object]],
    manifests: Sequence[Mapping[str, object]],
) -> None:
    job_by_id = {_text(job.get("job_id")): job for job in jobs}
    for manifest in manifests:
        if _text(manifest.get("status")) != "succeeded":
            continue
        asset_dir = Path(_text(manifest.get("asset_dir")))
        if not asset_dir.exists():
            continue
        job_id = _text(manifest.get("job_id"))
        job = job_by_id.get(job_id, {})
        if not job or not has_reusable_asset_identity(job):
            continue
        asset_key = _text(manifest.get("asset_key")) or reusable_asset_key_for_job(job)
        if not asset_key:
            continue
        upsert_top_asset_cache_entry(
            db_path,
            {
                "asset_key": asset_key,
                "content_id": _text(job.get("content_id")),
                "platform": _text(job.get("platform")) or _text(manifest.get("platform")),
                "source": _cache_source(manifest),
                "asset_dir": str(asset_dir.resolve()),
                "size_bytes": 0 if _is_external_harvester_asset(manifest) else directory_size(asset_dir),
                "last_used_batch_id": _text(batch_id),
            },
        )
        upsert_top_asset_cache_ref(
            db_path,
            batch_id=batch_id,
            job_id=job_id,
            content_identity_key=_text(job.get("content_identity_key")),
            asset_key=asset_key,
            retained=True,
        )


def _cache_source(manifest: Mapping[str, object]) -> str:
    metadata = _metadata_from_manifest(manifest)
    return _text(metadata.get("ops_cache_source")) or _text(metadata.get("ops_cache_note")) or "topn_capture"


def _is_external_harvester_asset(manifest: Mapping[str, object]) -> bool:
    metadata = _metadata_from_manifest(manifest)
    source_dir = _text(metadata.get("ops_cache_source_asset_dir"))
    return bool(source_dir and source_dir == _text(manifest.get("asset_dir")))


def _ops_capture_root(cache_root: Path, batch_id: str, *, run_id: str | None = None) -> Path:
    run_id = run_id or _new_run_id()
    return Path(cache_root).expanduser().resolve() / "_capture-runs" / _safe_path_segment(batch_id) / run_id


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _prepare_ops_capture_root(capture_root: Path, harvester_root: Path) -> None:
    capture_root.mkdir(parents=True, exist_ok=True)
    output_dir = capture_root / "output"
    if output_dir.exists() and not any(output_dir.iterdir()):
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in PLATFORM_PROFILE_DIRS:
        target = capture_root / name
        source = Path(harvester_root) / name
        if target.exists() or target.is_symlink():
            continue
        if not source.exists():
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            target.symlink_to(source, target_is_directory=True)
        except OSError:
            shutil.copytree(source, target, dirs_exist_ok=True)


def _harvester_capture_env(harvester_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HARVESTER_ROOT"] = str(Path(harvester_root).resolve())
    env["HARVESTER_PROGRESS_LOGS"] = "1"
    env["CRAWL_BROWSER_HEADLESS"] = "1"
    env["MATERIAL_BROWSER_FALLBACK_HEADLESS"] = "1"
    env["PLAYWRIGHT_HEADLESS"] = "1"
    env["LOGIN_CHECK_HEADLESS"] = "1"
    return env


def _run_command_with_progress(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    progress_callback: Callable[[HarvesterProgressEvent], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    callback_errors: list[BaseException] = []

    def consume(stream: object, parts: list[str]) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                parts.append(line)
                _emit_progress_from_text(line, progress_callback)
        except BaseException as exc:  # pragma: no cover - defensive callback propagation
            callback_errors.append(exc)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    stdout_thread = threading.Thread(target=consume, args=(process.stdout, stdout_parts), daemon=True)
    stderr_thread = threading.Thread(target=consume, args=(process.stderr, stderr_parts), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    returncode = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    if callback_errors:
        raise callback_errors[0]
    return subprocess.CompletedProcess(
        list(command),
        int(returncode or 0),
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def _emit_progress_from_text(
    text: str,
    progress_callback: Callable[[HarvesterProgressEvent], None] | None,
) -> None:
    if progress_callback is None:
        return
    for line in str(text or "").splitlines():
        event = _parse_progress_line(line)
        if event is not None:
            progress_callback(event)


def _parse_progress_line(line: str) -> HarvesterProgressEvent | None:
    text = _text(line)
    if not text.startswith(PROGRESS_LOG_PREFIX):
        return None
    try:
        payload = json.loads(text[len(PROGRESS_LOG_PREFIX) :])
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None
    total = max(_int(payload.get("total")), 0)
    completed = max(_int(payload.get("completed")), 0)
    if total:
        completed = min(completed, total)
    remaining = max(total - completed, 0) if total else 0
    return HarvesterProgressEvent(
        platform=_platform_from_daily_platform_id(_text(payload.get("platformId") or payload.get("platform"))),
        stage=_text(payload.get("stage")),
        phase=_text(payload.get("phase")),
        item_id=_text(payload.get("itemId") or payload.get("id")),
        completed=completed,
        total=total,
        remaining_count=remaining,
        action=_text(payload.get("action")),
        updated_at=_text(payload.get("updatedAt")) or datetime.now(timezone.utc).isoformat(),
    )


def _remove_source_dir_if_under(source_dir: object, root: Path, target_dir: object) -> None:
    source_text = _text(source_dir)
    target_text = _text(target_dir)
    if not source_text:
        return
    try:
        source = Path(source_text).resolve()
        root_resolved = Path(root).resolve()
        target = Path(target_text).resolve() if target_text else None
        source.relative_to(root_resolved)
    except Exception:
        return
    if target is not None and source == target:
        return
    shutil.rmtree(source, ignore_errors=True)


def _copied_path_map(source_dir: Path, target_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in target_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(target_dir)
        mapping[str(source_dir / relative)] = str(path)
    return mapping


def _remap_asset_path(value: object, path_map: Mapping[str, str]) -> str:
    text = _text(value)
    if not text:
        return ""
    if text in path_map:
        return path_map[text]
    try:
        resolved = str(Path(text).resolve())
    except Exception:
        resolved = ""
    return path_map.get(resolved, text)


def _metadata_from_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    raw = manifest.get("metadata_json") or manifest.get("metadata") or {}
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_or_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return [text]
    if isinstance(parsed, list):
        return [_text(item) for item in parsed if _text(item)]
    return []


def _platform_cache_name(value: str) -> str:
    platform = _text(value)
    if platform == "抖音":
        return "douyin"
    if platform == "小红书":
        return "xhs"
    if platform == "B站":
        return "bilibili"
    return _safe_path_segment(platform or "unknown")


def _safe_path_segment(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", _text(value)).strip("_")[:120] or "unknown"


def _explain_harvester_failure(message: str, jobs: Sequence[Mapping[str, object]]) -> str:
    text = _text(message) or "harvester CLI 执行失败"
    platform = _platform_from_error_text(text) or _platform_from_jobs(jobs)
    if _looks_like_login_failure(text):
        command = _login_npm_script_for_platform(platform)
        if platform and command:
            return f"{platform}登录状态失效或触发风控，请在 harvester-THS 中运行 {command} 后重试。原始错误：{text}"
        if platform:
            return f"{platform}登录状态失效或触发风控，请先完成对应平台登录后重试。原始错误：{text}"
        return f"harvester 登录状态失效或触发风控，请先完成对应平台登录后重试。原始错误：{text}"
    return text


def _looks_like_login_failure(text: str) -> bool:
    return bool(
        re.search(
            r"登录状态|登录态|重新登录|请先登录|扫码登录|验证码登录|手机号登录|登录后查看|风控|安全验证|访问过于频繁|风险",
            text,
        )
    )


def _platform_from_error_text(text: str) -> str:
    if "小红书" in text or "xhs" in text.lower():
        return "小红书"
    if "抖音" in text or "douyin" in text.lower():
        return "抖音"
    if "B站" in text or "bilibili" in text.lower():
        return "B站"
    return ""


def _platform_from_jobs(jobs: Sequence[Mapping[str, object]]) -> str:
    platforms = {_text(job.get("platform")) or _platform_from_channel(job.get("channel")) for job in jobs}
    platforms = {platform for platform in platforms if platform}
    return platforms.pop() if len(platforms) == 1 else ""


def _login_npm_script_for_platform(platform: str) -> str:
    if platform == "抖音":
        return "npm run login:douyin"
    if platform == "小红书":
        return "npm run login"
    if platform == "B站":
        return "npm run login:bilibili"
    return ""


def _target_date_from_jobs(jobs: Sequence[Mapping[str, object]]) -> str:
    for job in jobs:
        text = _text(job.get("period_end"))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return text
    return ""


def _normalize_douyin_identity(job: dict[str, object]) -> dict[str, object]:
    content_url = _text(job.get("content_url"))
    extracted_id = _extract_douyin_work_id(_text(job.get("content_id")))
    if extracted_id and not _trusted_douyin_job_work_id(job, extracted_id):
        extracted_id = ""
    if not extracted_id and _looks_like_douyin_identity_key(job.get("content_identity_key")):
        extracted_id = _extract_douyin_work_id(_text(job.get("content_identity_key")))
        if extracted_id and not _trusted_douyin_job_work_id(job, extracted_id):
            extracted_id = ""
    if not extracted_id:
        extracted_id = _extract_douyin_work_id(content_url)
        if extracted_id and not _trusted_douyin_job_work_id(job, extracted_id):
            extracted_id = ""
    if not extracted_id:
        if _extract_douyin_work_id(content_url):
            job["content_url"] = ""
        job["content_id"] = ""
        return job
    job["content_id"] = extracted_id
    job["content_url"] = f"https://www.douyin.com/video/{extracted_id}"
    job["content_identity_key"] = f"{_text(job.get('channel'))}::抖音::id::{extracted_id}"
    job["job_id"] = _job_id(_text(job.get("content_identity_key")))
    return job


def _resolve_douyin_job(
    job: dict[str, object],
    resolver: Callable[[str], Mapping[str, object]] | None,
) -> dict[str, object]:
    content_url = _text(job.get("content_url"))
    content_id = _text(job.get("content_id"))
    if content_id and "douyin.com/video/" in content_url:
        return job
    if resolver is None or not content_url:
        return job
    try:
        resolved = resolver(content_url)
    except Exception:
        return job
    resolved_id = _text(resolved.get("id") or resolved.get("content_id") or resolved.get("item_id"))
    resolved_link = _text(resolved.get("link") or resolved.get("content_url") or resolved.get("url"))
    if resolved_id:
        job["content_id"] = resolved_id
        job["content_identity_key"] = f"{_text(job.get('channel'))}::抖音::id::{resolved_id}"
        job["job_id"] = _job_id(_text(job.get("content_identity_key")))
    if resolved_link:
        job["content_url"] = resolved_link
    job = _normalize_douyin_identity(job)
    return job


def _extract_douyin_work_id(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d{10,24}", text):
        return text
    match = re.search(r"/(?:video|note)/(\d{10,24})", text)
    if match:
        return match.group(1)
    match = re.search(r"(?:aweme_id|item_id|modal_id)=(\d{10,24})", text)
    if match:
        return match.group(1)
    match = re.search(r"::抖音::id::(\d{10,24})", text)
    if match:
        return match.group(1)
    match = re.search(r"(?:抖音|douyin)_id_(\d{10,24})", text, re.IGNORECASE)
    return match.group(1) if match else ""


def _douyin_work_id_from_row(row: pd.Series) -> str:
    material_id = _douyin_material_id_from_row(row)
    trusted_source = _has_trusted_douyin_link_source(row)
    for value in [
        row.get("work_id"),
        row.get("work_url"),
        row.get("content_url"),
    ]:
        extracted = _extract_douyin_work_id(value)
        if extracted and (not _same_douyin_material_id(extracted, material_id) or trusted_source):
            return extracted
    identity = _text(row.get("content_identity_key"))
    if _looks_like_douyin_identity_key(identity):
        extracted = _extract_douyin_work_id(identity)
        if extracted and (not _same_douyin_material_id(extracted, material_id) or trusted_source):
            return extracted
    return ""


def _douyin_material_id_from_row(row: pd.Series) -> str:
    return _normalized_douyin_numeric_id(row.get("ad_material_id")) or _normalized_douyin_numeric_id(row.get("material_id"))


def _trusted_douyin_job_work_id(job: Mapping[str, object], item_id: str) -> bool:
    material_id = _normalized_douyin_numeric_id(job.get("ad_material_id")) or _normalized_douyin_numeric_id(job.get("material_id"))
    if not material_id:
        return True
    return not _same_douyin_material_id(item_id, material_id) or _has_trusted_douyin_link_source(job)


def _same_douyin_material_id(item_id: str, material_id: str) -> bool:
    item = _normalized_douyin_numeric_id(item_id)
    material = _normalized_douyin_numeric_id(material_id)
    if not item or not material:
        return False
    if item == material:
        return True
    return len(item) == len(material) and item[:15] == material[:15]


def _normalized_douyin_numeric_id(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d{10,24}", text):
        return text
    if re.fullmatch(r"\d+(?:\.\d+)?e\+\d+", text, flags=re.IGNORECASE):
        try:
            return str(int(float(text)))
        except (OverflowError, ValueError):
            return ""
    match = re.search(r"(?<!\d)(\d{10,24})(?!\d)", text)
    return match.group(1) if match else ""


def _has_trusted_douyin_link_source(row: Mapping[str, object]) -> bool:
    source = " ".join(
        _text(row.get(column))
        for column in ["link_source", "metadata_source", "match_source"]
    ).lower()
    return any(
        token in source
        for token in ["harvester_douyin_detail", "harvester_cache", "metadata_cache", "original_excel", "作品id"]
    )


def _looks_like_douyin_identity_key(value: object) -> bool:
    text = _text(value)
    return bool(re.search(r"::抖音::id::\d{10,24}$", text))


def _job_id(identity: str) -> str:
    raw = f"harvester_asset|{identity}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _fallback_identity(row: pd.Series) -> str:
    if _platform_cache_name(_text(row.get("platform") or row.get("channel"))) == "douyin":
        raw = "|".join(_text(row.get(column)) for column in ["channel", "work_id", "work_url", "content_url", "title", "account"])
    else:
        raw = "|".join(_text(row.get(column)) for column in ["channel", "content_id", "material_id", "title", "content_url"])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _platform_from_channel(value: object) -> str:
    text = _text(value)
    if "小红书" in text:
        return "小红书"
    if "抖音" in text:
        return "抖音"
    if "B站" in text or "bilibili" in text.lower():
        return "B站"
    return ""


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _float(value: object) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _int(value: object) -> int:
    try:
        if value is None or pd.isna(value):
            return 0
        return int(float(value))
    except Exception:
        return 0
