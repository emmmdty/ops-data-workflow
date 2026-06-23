"""SQLite-backed background analysis job queue."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterable, Mapping

import pandas as pd

from .storage import list_harvester_asset_jobs, list_harvester_asset_manifests


JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_RETRY = "retry"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_SUCCEEDED = "succeeded"
TOP_MULTIMODAL_JOB_TYPE = "top_multimodal_content"
ANALYSIS_PURPOSE_FILL_MISSING_TYPE = "fill_missing_type"
ANALYSIS_PURPOSE_STRATEGY_RECAP = "strategy_recap"
MULTIMODAL_RESULT_FIELDS = [
    "内容形态",
    "一级内容类型",
    "二级内容类型",
    "B站内容类型",
    "标题钩子",
    "视觉结构",
    "信息密度",
    "转化路径",
    "可复用点",
    "不建议复用点",
    "下周期策略建议",
    "共性总结",
]


JOB_COLUMNS = [
    "job_id",
    "batch_id",
    "job_type",
    "analysis_purpose",
    "status",
    "trigger",
    "platform",
    "channel",
    "content_identity_key",
    "title",
    "content_url",
    "prompt_hint",
    "payload_json",
    "result_json",
    "error_message",
    "attempts",
    "max_attempts",
    "visible_alert",
    "created_at",
    "updated_at",
]


def init_analysis_jobs(db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            create table if not exists analysis_jobs (
                job_id text primary key,
                batch_id text not null,
                job_type text not null,
                analysis_purpose text not null default 'strategy_recap',
                status text not null,
                trigger text not null default '',
                platform text not null default '',
                channel text not null default '',
                content_identity_key text not null default '',
                title text not null default '',
                content_url text not null default '',
                prompt_hint text not null default '',
                payload_json text not null default '',
                result_json text not null default '',
                error_message text not null default '',
                attempts integer not null default 0,
                max_attempts integer not null default 2,
                visible_alert integer not null default 0,
                created_at text not null,
                updated_at text not null
            )
            """
        )
        _ensure_analysis_job_columns(conn)
        conn.commit()


def enqueue_top_multimodal_jobs(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    trigger: str,
    prompt_hint: str = "",
    max_attempts: int = 2,
    analysis_purpose: str = ANALYSIS_PURPOSE_STRATEGY_RECAP,
) -> list[str]:
    init_analysis_jobs(db_path)
    if top_content.empty:
        return []
    created: list[str] = []
    now = _now()
    with closing(sqlite3.connect(db_path)) as conn:
        for _, row in top_content.iterrows():
            payload = _job_payload(row)
            purpose = _analysis_purpose(analysis_purpose)
            payload["analysis_purpose"] = purpose
            job_id = _job_id(batch_id, TOP_MULTIMODAL_JOB_TYPE, purpose, payload.get("content_identity_key", ""))
            exists = conn.execute("select 1 from analysis_jobs where job_id = ?", (job_id,)).fetchone()
            if exists:
                continue
            conn.execute(
                """
                insert into analysis_jobs (
                    job_id, batch_id, job_type, analysis_purpose, status, trigger, platform, channel,
                    content_identity_key, title, content_url, prompt_hint, payload_json,
                    attempts, max_attempts, visible_alert, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
                """,
                (
                    job_id,
                    batch_id,
                    TOP_MULTIMODAL_JOB_TYPE,
                    purpose,
                    JOB_STATUS_QUEUED,
                    trigger,
                    payload.get("platform", ""),
                    payload.get("channel", ""),
                    payload.get("content_identity_key", ""),
                    payload.get("title", ""),
                    payload.get("content_url", ""),
                    prompt_hint,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    int(max_attempts),
                    now,
                    now,
                ),
            )
            created.append(job_id)
        conn.commit()
    return created


def reset_top_multimodal_jobs(
    db_path: Path,
    batch_id: str,
    top_content: pd.DataFrame,
    *,
    trigger: str,
    prompt_hint: str = "",
    max_attempts: int = 2,
    analysis_purpose: str = ANALYSIS_PURPOSE_STRATEGY_RECAP,
) -> list[str]:
    """Queue or requeue Top multimodal jobs for a manual analysis pass."""
    init_analysis_jobs(db_path)
    if top_content.empty:
        return []
    job_ids: list[str] = []
    now = _now()
    purpose = _analysis_purpose(analysis_purpose)
    with closing(sqlite3.connect(db_path)) as conn:
        current_job_ids: set[str] = set()
        for _, row in top_content.iterrows():
            payload = _job_payload(row)
            payload["analysis_purpose"] = purpose
            job_id = _job_id(batch_id, TOP_MULTIMODAL_JOB_TYPE, purpose, payload.get("content_identity_key", ""))
            current_job_ids.add(job_id)
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            exists = conn.execute("select 1 from analysis_jobs where job_id = ?", (job_id,)).fetchone()
            if exists:
                conn.execute(
                    """
                    update analysis_jobs
                    set analysis_purpose = ?, status = ?, trigger = ?, platform = ?, channel = ?,
                        content_identity_key = ?, title = ?, content_url = ?,
                        prompt_hint = ?, payload_json = ?, result_json = '',
                        error_message = '', attempts = 0, max_attempts = ?,
                        visible_alert = 0, updated_at = ?
                    where job_id = ?
                    """,
                    (
                        purpose,
                        JOB_STATUS_QUEUED,
                        trigger,
                        payload.get("platform", ""),
                        payload.get("channel", ""),
                        payload.get("content_identity_key", ""),
                        payload.get("title", ""),
                        payload.get("content_url", ""),
                        prompt_hint,
                        payload_json,
                        int(max_attempts),
                        now,
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    insert into analysis_jobs (
                        job_id, batch_id, job_type, analysis_purpose, status, trigger, platform, channel,
                        content_identity_key, title, content_url, prompt_hint, payload_json,
                        attempts, max_attempts, visible_alert, created_at, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
                    """,
                    (
                        job_id,
                        batch_id,
                        TOP_MULTIMODAL_JOB_TYPE,
                        purpose,
                        JOB_STATUS_QUEUED,
                        trigger,
                        payload.get("platform", ""),
                        payload.get("channel", ""),
                        payload.get("content_identity_key", ""),
                        payload.get("title", ""),
                        payload.get("content_url", ""),
                        prompt_hint,
                        payload_json,
                        int(max_attempts),
                        now,
                        now,
                    ),
                )
            job_ids.append(job_id)
        if current_job_ids:
            placeholders = ",".join("?" for _ in current_job_ids)
            conn.execute(
                f"""
                delete from analysis_jobs
                where batch_id = ?
                  and job_type = ?
                  and analysis_purpose = ?
                  and job_id not in ({placeholders})
                """,
                [batch_id, TOP_MULTIMODAL_JOB_TYPE, purpose, *sorted(current_job_ids)],
            )
        conn.commit()
    return job_ids


def record_job_failure(db_path: Path, job_id: str, error_message: str, *, max_attempts: int | None = None) -> None:
    init_analysis_jobs(db_path)
    now = _now()
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "select attempts, max_attempts from analysis_jobs where job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return
        attempts = int(row[0] or 0) + 1
        allowed = int(max_attempts if max_attempts is not None else row[1] or 2)
        status = JOB_STATUS_FAILED if attempts >= allowed else JOB_STATUS_RETRY
        visible_alert = 1 if status == JOB_STATUS_FAILED else 0
        conn.execute(
            """
            update analysis_jobs
            set attempts = ?, max_attempts = ?, status = ?, error_message = ?,
                visible_alert = ?, updated_at = ?
            where job_id = ?
            """,
            (attempts, allowed, status, str(error_message), visible_alert, now, job_id),
        )
        conn.commit()


def record_job_success(db_path: Path, job_id: str, result: Mapping[str, object]) -> None:
    init_analysis_jobs(db_path)
    now = _now()
    normalized = {field: _text(result.get(field)) for field in MULTIMODAL_RESULT_FIELDS}
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            update analysis_jobs
            set status = ?, result_json = ?, error_message = '',
                visible_alert = 0, updated_at = ?
            where job_id = ?
            """,
            (
                JOB_STATUS_SUCCEEDED,
                json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                now,
                job_id,
            ),
        )
        conn.commit()


def run_top_multimodal_analysis_from_manifests(
    db_path: Path,
    batch_id: str,
    *,
    analyzer: Callable[[Mapping[str, object], Mapping[str, object]], Mapping[str, object]] | None = None,
    analysis_purpose: str = "",
) -> int:
    jobs = list_analysis_jobs(db_path, batch_id=batch_id)
    if jobs.empty:
        return 0
    manifests = list_harvester_asset_manifests(db_path, batch_id=batch_id)
    asset_jobs = list_harvester_asset_jobs(db_path, batch_id=batch_id)
    identity_by_harvester_job = _harvester_job_identity_map(asset_jobs)
    manifest_by_job = {}
    manifest_by_identity = {}
    if not manifests.empty:
        for _, item in manifests.iterrows():
            if _text(item.get("status")) != "succeeded":
                continue
            if not _text(item.get("asset_dir")):
                continue
            manifest = item.to_dict()
            harvester_job_id = _text(item.get("job_id"))
            manifest_by_job[harvester_job_id] = manifest
            identity = identity_by_harvester_job.get(harvester_job_id)
            if identity:
                manifest_by_identity[identity] = manifest
    updated = 0
    purpose_filter = _text(analysis_purpose)
    for _, row in jobs.iterrows():
        if _text(row.get("job_type")) != TOP_MULTIMODAL_JOB_TYPE:
            continue
        if purpose_filter and _text(row.get("analysis_purpose")) != purpose_filter:
            continue
        if _text(row.get("status")) not in {JOB_STATUS_QUEUED, JOB_STATUS_RETRY, JOB_STATUS_RUNNING}:
            continue
        job_id = _text(row.get("job_id"))
        manifest = manifest_by_job.get(job_id) or manifest_by_identity.get(_text(row.get("content_identity_key")))
        if manifest is None:
            manifest = _remote_evidence_manifest(row.to_dict())
        if manifest is None:
            continue
        try:
            manifest = dict(manifest)
            manifest["metadata"] = _metadata_dict(manifest.get("metadata_json") or manifest.get("metadata"))
            result = (analyzer or _default_multimodal_analyzer)(row.to_dict(), manifest)
            record_job_success(db_path, job_id, result)
        except Exception as exc:
            record_job_failure(db_path, job_id, f"多模态分析失败：{exc}", max_attempts=1)
        updated += 1
    return updated


def list_analysis_jobs(db_path: Path, *, batch_id: str = "") -> pd.DataFrame:
    init_analysis_jobs(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        sql = "select * from analysis_jobs"
        params: list[object] = []
        if batch_id:
            sql += " where batch_id = ?"
            params.append(batch_id)
        sql += " order by created_at asc, job_id asc"
        rows = conn.execute(sql, params).fetchall()
        columns = [item[0] for item in conn.execute("select * from analysis_jobs limit 0").description]
    if not rows:
        return pd.DataFrame(columns=JOB_COLUMNS)
    frame = pd.DataFrame(rows, columns=columns)
    if "visible_alert" in frame.columns:
        frame["visible_alert"] = frame["visible_alert"].astype(bool)
    if "analysis_purpose" not in frame.columns:
        frame["analysis_purpose"] = ANALYSIS_PURPOSE_STRATEGY_RECAP
    frame["analysis_purpose"] = frame["analysis_purpose"].fillna("").astype(str).replace("", ANALYSIS_PURPOSE_STRATEGY_RECAP)
    return frame[[column for column in JOB_COLUMNS if column in frame.columns]]


def _job_payload(row: pd.Series) -> dict[str, str]:
    return {
        "platform": _text(row.get("platform") or row.get("platform_group")),
        "channel": _text(row.get("channel")),
        "content_identity_key": _text(row.get("content_identity_key")) or _fallback_identity(row),
        "content_id": _text(row.get("content_id")),
        "title": _text(row.get("title")),
        "account": _text(row.get("account")),
        "content_url": _text(row.get("content_url") or row.get("work_url")),
        "work_url": _text(row.get("work_url")),
        "ad_material_id": _text(row.get("ad_material_id") or row.get("material_id")),
        "ad_material_url": _text(row.get("ad_material_url")),
        "ad_cover_url": _text(row.get("ad_cover_url")),
        "spend": _text(row.get("spend")),
        "impressions": _text(row.get("impressions")),
        "activations": _text(row.get("activations")),
        "first_pay_count": _text(row.get("first_pay_count")),
        "activation_cost": _text(row.get("activation_cost")),
        "first_pay_cost": _text(row.get("first_pay_cost")),
        "category_l1": _text(row.get("category_l1")),
        "category_l2": _text(row.get("category_l2")),
        "bilibili_content_type": _text(row.get("bilibili_content_type")),
        "content_type": _text(row.get("content_type")),
        "tags": _text(row.get("tags")),
    }


def _default_multimodal_analyzer(job: Mapping[str, object], manifest: Mapping[str, object]) -> dict[str, str]:
    metadata = _metadata_dict(manifest.get("metadata_json") or manifest.get("metadata"))
    platform = _text(job.get("platform")) or _text(manifest.get("platform"))
    has_video = bool(_text(manifest.get("video_path")))
    has_images = bool(_text(manifest.get("cover_path"))) or bool(_text(manifest.get("screenshots_json"))) or bool(_text(manifest.get("frames_json")))
    content_form = "视频" if has_video else ("图文" if has_images else "未知")
    category_l1 = _text(metadata.get("category_l1") or metadata.get("一级内容类型") or metadata.get("内容一级类型"))
    category_l2 = _text(metadata.get("category_l2") or metadata.get("二级内容类型") or metadata.get("内容二级类型"))
    bilibili_type = _text(metadata.get("bilibili_content_type") or metadata.get("B站内容类型") or metadata.get("content_type"))
    if platform != "B站":
        bilibili_type = ""
    title = _text(job.get("title"))
    asset_note = "已基于 harvester 素材资产生成，建议接入视觉模型后复核细节。"
    return {
        "内容形态": content_form,
        "一级内容类型": category_l1,
        "二级内容类型": category_l2,
        "B站内容类型": bilibili_type,
        "标题钩子": _title_hook(title),
        "视觉结构": "视频素材" if has_video else ("图片/截图素材" if has_images else "素材结构待补全"),
        "信息密度": "中",
        "转化路径": "标题/封面吸引点击，内容承接卖点或投教信息，再引导激活/付费。",
        "可复用点": asset_note,
        "不建议复用点": "不要直接复用未验证的夸张承诺、低清素材或缺少转化承接的表达。",
        "下周期策略建议": "优先复用已采集 Top 素材的标题结构、内容形态和转化承接，并结合真实消耗/曝光复盘迭代。",
    }


def _metadata_dict(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _harvester_job_identity_map(jobs: pd.DataFrame) -> dict[str, str]:
    if jobs.empty or "job_id" not in jobs.columns:
        return {}
    mapping: dict[str, str] = {}
    for _, row in jobs.iterrows():
        job_id = _text(row.get("job_id"))
        identity = _text(row.get("content_identity_key"))
        if job_id and identity:
            mapping[job_id] = identity
    return mapping


def _title_hook(title: str) -> str:
    text = _text(title)
    if not text:
        return "标题钩子待补全"
    if "?" in text or "？" in text:
        return "问题式钩子"
    if any(token in text for token in ["为什么", "如何", "怎么", "一招", "避坑", "机会"]):
        return "利益/问题导向钩子"
    return "主题直给钩子"


def _fallback_identity(row: pd.Series) -> str:
    joined = "|".join(_text(row.get(column)) for column in ["channel", "content_id", "material_id", "title", "content_url"])
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _job_id(batch_id: str, job_type: str, analysis_purpose: str, identity: str) -> str:
    raw = f"{batch_id}|{job_type}|{_analysis_purpose(analysis_purpose)}|{identity}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _analysis_purpose(value: object) -> str:
    text = _text(value)
    if text == ANALYSIS_PURPOSE_FILL_MISSING_TYPE or text.startswith(f"{ANALYSIS_PURPOSE_FILL_MISSING_TYPE}:"):
        return ANALYSIS_PURPOSE_FILL_MISSING_TYPE
    if text.startswith(f"{ANALYSIS_PURPOSE_STRATEGY_RECAP}:"):
        return text
    return text or ANALYSIS_PURPOSE_STRATEGY_RECAP


def _remote_evidence_manifest(job: Mapping[str, object]) -> dict[str, object] | None:
    payload = _metadata_dict(job.get("payload_json"))
    platform = _text(job.get("platform")) or _text(payload.get("platform"))
    ad_material_url = _text(job.get("ad_material_url")) or _text(payload.get("ad_material_url"))
    ad_cover_url = _text(job.get("ad_cover_url")) or _text(payload.get("ad_cover_url"))
    if platform != "抖音" or not (ad_material_url or ad_cover_url):
        return None
    remote_media_urls = [url for url in [ad_cover_url, ad_material_url] if url]
    return {
        "job_id": _text(job.get("job_id")),
        "status": "succeeded",
        "platform": platform,
        "asset_key": _text(job.get("content_identity_key")),
        "asset_dir": "",
        "cover_path": "",
        "video_path": "",
        "screenshots_json": "[]",
        "frames_json": "[]",
        "remote_media_urls": remote_media_urls,
        "metadata": {
            "evidence_source": "douyin_ad_material",
            "ad_material_url": ad_material_url,
            "ad_cover_url": ad_cover_url,
        },
        "error_message": "",
    }


def _ensure_analysis_job_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute('pragma table_info("analysis_jobs")').fetchall()}
    if "analysis_purpose" not in existing:
        conn.execute(
            'alter table "analysis_jobs" add column "analysis_purpose" text not null default "strategy_recap"'
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
