"""Optional public metadata enrichment for content rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Callable
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

from .pipeline import build_high_spend_content_pool, high_spend_content_identity_key
from .title_matching import clean_douyin_share_title


METADATA_COLUMNS = [
    "metadata_source",
    "metadata_confidence",
    "metadata_fetched_at",
    "metadata_error",
    "metadata_review_reason",
    "metadata_tags",
    "metadata_content_type_candidate",
    "link_openability",
    "link_source",
    "xhs_placeholder_url",
]

SUPPLEMENT_RECORD_COLUMNS = [
    "batch",
    "channel",
    "content_id",
    "material_id",
    "title",
    "field_name",
    "old_value",
    "new_value",
    "source",
    "confidence",
    "status",
    "reason",
]

DEFAULT_MODE = "off"
SAFE_PUBLIC_MODE = "safe_public"
REQUEST_TIMEOUT_SECONDS = 5.0
HIGH_SPEND_REVIEW_THRESHOLD = 2000.0
DEFAULT_HARVESTER_ROOT = Path("/Users/tjk/Documents/Codex/harvester-THS")
BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
}


@dataclass(frozen=True)
class MetadataCandidate:
    platform: str
    content_id: str = ""
    content_url: str = ""
    placeholder_url: str = ""
    title: str = ""
    tags: str = ""
    published_at: str = ""
    account: str = ""
    content_type_candidate: str = ""
    link_openability: str = ""
    link_source: str = ""
    source: str = ""
    confidence: float = 0.0
    error: str = ""
    review_reason: str = ""
    force_review: bool = False
    cache_hit: bool = False


def enrich_content_metadata(
    canonical: pd.DataFrame,
    *,
    mode: str = DEFAULT_MODE,
    cache_dir: Path | None = None,
    harvester_root: Path | None = None,
    fetch_bilibili: Callable[[str], dict | None] | None = None,
    fetch_douyin_detail: Callable[[str], dict | None] | None = None,
    fetch_xhs_detail: Callable[[str, str], dict | None] | None = None,
    allow_public_api: bool = True,
    resolve_douyin_shortlink: Callable[[str], str] | None = None,
    fetched_at: str | None = None,
    batch_id: str = "",
    return_records: bool = False,
) -> tuple[pd.DataFrame, dict[str, int]] | tuple[pd.DataFrame, dict[str, int], pd.DataFrame]:
    """Backfill safe public content metadata without overwriting Excel values."""
    enriched = canonical.copy()
    _ensure_metadata_columns(enriched)
    original_review_flags = enriched["needs_manual_review"].fillna(False).map(_is_truthy)
    enriched["needs_manual_review"] = original_review_flags.astype(bool)
    stats = {
        "processed_rows": 0,
        "filled_rows": 0,
        "hint_rows": 0,
        "conflict_rows": 0,
        "review_rows": 0,
        "error_rows": 0,
        "cache_hits": 0,
    }
    supplement_records: list[dict[str, object]] = []
    if mode != SAFE_PUBLIC_MODE or enriched.empty:
        if return_records:
            return enriched, stats, pd.DataFrame(columns=SUPPLEMENT_RECORD_COLUMNS)
        return enriched, stats

    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    cache = MetadataCache(cache_dir) if cache_dir is not None else None
    harvester_cache = HarvesterMetadataCache(harvester_root or DEFAULT_HARVESTER_ROOT)
    high_spend_keys = _high_spend_metadata_keys(enriched)
    row_identity_keys = enriched.apply(high_spend_content_identity_key, axis=1)
    for index, row in enriched.iterrows():
        platform = _platform_name(row)
        is_high_spend_row = _clean_text(row_identity_keys.loc[index]) in high_spend_keys
        is_douyin_share_row = platform == "douyin" and _douyin_row_has_share_text(row)
        if not is_high_spend_row and not is_douyin_share_row:
            continue
        candidate: MetadataCandidate | None = None
        if platform == "bilibili":
            candidate = _bilibili_candidate(row, cache, harvester_cache, fetch_bilibili, allow_public_api)
        elif platform == "douyin":
            candidate = _douyin_candidate(row, harvester_cache, resolve_douyin_shortlink, fetch_douyin_detail)
        elif platform == "xhs":
            candidate = _xhs_candidate(row, cache, harvester_cache, fetch_xhs_detail)
        if candidate is None:
            continue
        stats["processed_rows"] += 1
        changed, conflicted, review, hinted, records = _apply_candidate(
            enriched,
            index,
            candidate,
            fetched_at,
            batch_id=batch_id,
        )
        supplement_records.extend(records)
        if changed:
            stats["filled_rows"] += 1
        if hinted:
            stats["hint_rows"] += 1
        if conflicted:
            stats["conflict_rows"] += 1
        if review and not bool(original_review_flags.loc[index]):
            stats["review_rows"] += 1
        if candidate.error:
            stats["error_rows"] += 1
        if candidate.cache_hit:
            stats["cache_hits"] += 1
    if return_records:
        return enriched, stats, pd.DataFrame(supplement_records, columns=SUPPLEMENT_RECORD_COLUMNS)
    return enriched, stats


def _high_spend_metadata_keys(frame: pd.DataFrame) -> set[str]:
    pool = build_high_spend_content_pool(frame)
    if pool.empty or "content_identity_key" not in pool.columns:
        return set()
    return {
        _clean_text(value)
        for value in pool["content_identity_key"].dropna().tolist()
        if _clean_text(value)
    }


def _douyin_row_has_share_text(row: pd.Series) -> bool:
    text = " ".join(
        _clean_text(row.get(column))
        for column in ["content_id", "material_id", "content_url", "title"]
        if column in row.index
    )
    return _looks_like_douyin_share_text(text)


class MetadataCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    def get(self, platform: str, content_id: str) -> dict | None:
        path = self._path(platform, content_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def set(self, platform: str, content_id: str, payload: dict) -> None:
        if not platform or not content_id:
            return
        path = self._path(platform, content_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _path(self, platform: str, content_id: str) -> Path:
        safe_id = re.sub(r"[^0-9A-Za-z_-]+", "_", str(content_id or "").strip())
        return self.cache_dir / platform / f"{safe_id}.json"


class HarvesterMetadataCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._output_metadata: dict[str, dict] | None = None
        self._douyin_output_metadata: dict[str, dict] | None = None
        self._xhs_output_metadata: dict[str, dict] | None = None

    def get_douyin(self, item_id: str) -> dict | None:
        if not item_id:
            return None
        cached = self._read_douyin_detail_cache(item_id)
        if cached:
            return cached
        return self._read_douyin_output_metadata().get(item_id)

    def get_bilibili(self, bvid: str) -> dict | None:
        if not bvid:
            return None
        cached = self._read_detail_cache(bvid)
        if cached:
            return cached
        return self._read_output_metadata().get(bvid)

    def get_xhs(self, note_id: str) -> dict | None:
        if not note_id:
            return None
        cached = self._read_xhs_detail_cache(note_id)
        if cached:
            return cached
        return self._read_xhs_output_metadata().get(note_id)

    def _read_douyin_detail_cache(self, item_id: str) -> dict | None:
        path = self.root / ".runtime" / "detail-cache" / "douyin" / f"{item_id}.json"
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return _normalize_harvester_douyin_payload(item_id, parsed)

    def _read_detail_cache(self, bvid: str) -> dict | None:
        path = self.root / ".runtime" / "detail-cache" / "bilibili" / f"{bvid}.json"
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return _normalize_harvester_bilibili_payload(bvid, parsed)

    def _read_xhs_detail_cache(self, note_id: str) -> dict | None:
        path = self.root / ".runtime" / "detail-cache" / "xhs" / f"{note_id}.json"
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return _normalize_harvester_xhs_payload(note_id, parsed)

    def _read_output_metadata(self) -> dict[str, dict]:
        if self._output_metadata is not None:
            return self._output_metadata
        result: dict[str, dict] = {}
        output_dir = self.root / "output"
        for path in sorted(output_dir.glob("*bilibili*.json")):
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = parsed.get("items") if isinstance(parsed, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                bvid = _clean_text(item.get("bvid") or item.get("id")) or _extract_bilibili_bv(item.get("link") or item.get("videoUrl"))
                if not bvid or bvid in result:
                    continue
                result[bvid] = _normalize_harvester_bilibili_payload(bvid, item)
        self._output_metadata = result
        return result

    def _read_douyin_output_metadata(self) -> dict[str, dict]:
        if self._douyin_output_metadata is not None:
            return self._douyin_output_metadata
        result: dict[str, dict] = {}
        output_dir = self.root / "output"
        for path in sorted(output_dir.glob("*douyin*.json")):
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = parsed.get("items") if isinstance(parsed, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = _extract_douyin_item_id(item.get("id") or item.get("itemUrl") or item.get("link") or item.get("content_url"))
                if not item_id or item_id in result:
                    continue
                result[item_id] = _normalize_harvester_douyin_payload(item_id, item)
        self._douyin_output_metadata = result
        return result

    def _read_xhs_output_metadata(self) -> dict[str, dict]:
        if self._xhs_output_metadata is not None:
            return self._xhs_output_metadata
        result: dict[str, dict] = {}
        output_dir = self.root / "output"
        for path in sorted(output_dir.glob("*xhs*.json")):
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = parsed.get("items") if isinstance(parsed, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                note_id = _extract_xhs_note_id(item.get("id") or item.get("link") or item.get("noteUrl"))
                if not note_id or note_id in result:
                    continue
                result[note_id] = _normalize_harvester_xhs_payload(note_id, item)
        self._xhs_output_metadata = result
        return result


def fetch_bilibili_metadata(bvid: str, *, request_get: Callable | None = None) -> dict | None:
    if not bvid:
        return None
    getter = request_get or requests.get
    view_url = "https://api.bilibili.com/x/web-interface/view"
    tag_url = "https://api.bilibili.com/x/tag/archive/tags"
    headers = {**BILIBILI_HEADERS, "Referer": _normalize_bilibili_url(bvid)}
    view = getter(view_url, params={"bvid": bvid}, timeout=REQUEST_TIMEOUT_SECONDS, headers=headers)
    view.raise_for_status()
    view_payload = view.json()
    if view_payload.get("code") != 0 or not isinstance(view_payload.get("data"), dict):
        return None
    tag_names: list[str] = []
    try:
        tags = getter(tag_url, params={"bvid": bvid}, timeout=REQUEST_TIMEOUT_SECONDS, headers=headers)
        tags.raise_for_status()
        tag_payload = tags.json()
        if isinstance(tag_payload.get("data"), list):
            tag_names = [
                _clean_text(item.get("tag_name"))
                for item in tag_payload["data"]
                if isinstance(item, dict) and _clean_text(item.get("tag_name"))
            ]
    except Exception:
        tag_names = []
    data = view_payload["data"]
    return {
        "id": bvid,
        "link": _normalize_bilibili_url(bvid),
        "title": _clean_text(data.get("title")),
        "tags": ",".join(tag_names),
        "published_at": _date_from_epoch(data.get("pubdate") or data.get("ctime") or data.get("created")),
    }


def fetch_xhs_downloader_detail(
    note_id: str,
    link: str = "",
    *,
    base_url: str = "",
    request_post: Callable | None = None,
) -> dict | None:
    """Fetch optional XHS detail from an external sidecar service."""
    service_url = _clean_text(base_url).rstrip("/")
    if not note_id or not service_url:
        return None
    poster = request_post or requests.post
    response = poster(
        f"{service_url}/xhs/detail",
        json={"id": note_id, "url": _clean_text(link)},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def resolve_douyin_shortlink(link: str) -> str:
    text = _clean_text(link)
    if not text or not _is_douyin_shortlink(text):
        return ""
    current = text
    for _ in range(5):
        try:
            response = requests.get(
                current,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        except Exception:
            return ""
        location = response.headers.get("location")
        if location:
            current = urljoin(current, location)
            normalized = _normalize_douyin_url(current)
            if _extract_douyin_item_id(normalized):
                return normalized
            continue
        normalized = _normalize_douyin_url(response.url or current)
        return normalized if _extract_douyin_item_id(normalized) else ""
    return ""


def fetch_douyin_detail_from_harvester(value: str, *, harvester_root: Path | None = None) -> dict | None:
    """Resolve one Douyin copied link through the sibling harvester project."""
    text = _clean_text(value)
    root = Path(harvester_root or DEFAULT_HARVESTER_ROOT)
    if not text or not root.exists():
        return None
    script = root / "src" / "resolve-douyin-share.mjs"
    if not script.exists():
        resolved = resolve_douyin_shortlink(text)
        item_id = _extract_douyin_item_id(resolved)
        return {"id": item_id, "link": resolved} if item_id else None
    command = ["node", str(script), "--json", "--input", text]
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads((completed.stdout or "").strip() or "{}")
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return data if isinstance(data, dict) else None


def _ensure_metadata_columns(frame: pd.DataFrame) -> None:
    for column in METADATA_COLUMNS:
        if column not in frame.columns:
            frame[column] = "" if column != "metadata_confidence" else 0.0
    if "needs_manual_review" not in frame.columns:
        frame["needs_manual_review"] = False
    if "review_reasons" not in frame.columns:
        frame["review_reasons"] = ""


def _platform_name(row: pd.Series) -> str:
    text = " ".join(
        _clean_text(row.get(column))
        for column in ["platform", "platform_group", "channel"]
        if column in row.index
    ).lower()
    if "bilibili" in text or "b站" in text or "哔哩" in text:
        return "bilibili"
    if "douyin" in text or "抖音" in text:
        return "douyin"
    if "xiaohongshu" in text or "xhs" in text or "小红书" in text:
        return "xhs"
    return ""


def _bilibili_candidate(
    row: pd.Series,
    cache: MetadataCache | None,
    harvester_cache: HarvesterMetadataCache,
    fetcher: Callable[[str], dict | None] | None,
    allow_public_api: bool = True,
) -> MetadataCandidate | None:
    bvid = _extract_bilibili_bv(_first_non_blank(row, ["content_id", "material_id", "content_url", "dedupe_key"]))
    if not bvid:
        return None
    cached = cache.get("bilibili", bvid) if cache is not None else None
    if cached:
        return _candidate_from_payload("bilibili", cached, "metadata_cache", 0.85, cache_hit=True)
    harvester_payload = harvester_cache.get_bilibili(bvid)
    if harvester_payload:
        if cache is not None:
            cache.set("bilibili", bvid, harvester_payload)
        return _candidate_from_payload("bilibili", harvester_payload, "harvester_cache", 0.88, cache_hit=True)
    if not allow_public_api:
        return _bilibili_failure_candidate(row, bvid, "B站公开接口跳过：历史批量刷新仅使用本地缓存")
    fetcher = fetcher or fetch_bilibili_metadata
    try:
        payload = fetcher(bvid) or {}
    except Exception as exc:
        return _bilibili_failure_candidate(row, bvid, f"B站公开接口失败：{exc}")
    if not payload:
        return _bilibili_failure_candidate(row, bvid, "B站公开接口未返回内容")
    normalized = {
        "id": _clean_text(payload.get("id")) or bvid,
        "link": _clean_text(payload.get("link")) or _normalize_bilibili_url(bvid),
        "title": _clean_text(payload.get("title")),
        "tags": _clean_text(payload.get("tags")),
        "published_at": _clean_text(payload.get("published_at") or payload.get("publishedAt")),
    }
    if cache is not None:
        cache.set("bilibili", bvid, normalized)
    return _candidate_from_payload("bilibili", normalized, "bilibili_public_api", 0.9)


def _bilibili_failure_candidate(row: pd.Series, bvid: str, error: str) -> MetadataCandidate:
    review_reason = ""
    if _row_spend(row) >= HIGH_SPEND_REVIEW_THRESHOLD:
        review_reason = "高消耗B站公开接口失败，可登录态补抓"
    return MetadataCandidate(
        platform="bilibili",
        content_id=bvid,
        source="bilibili_public_api",
        error=error,
        review_reason=review_reason,
    )


def _douyin_candidate(
    row: pd.Series,
    harvester_cache: HarvesterMetadataCache,
    shortlink_resolver: Callable[[str], str] | None,
    detail_fetcher: Callable[[str], dict | None] | None,
) -> MetadataCandidate | None:
    detail_source = _clean_text(row.get("content_url")) or _clean_text(row.get("title"))
    original_link = _first_url_from_text(detail_source) or _clean_text(row.get("content_url")) or _first_url_from_text(row.get("title"))
    raw_content_id = _clean_text(row.get("content_id"))
    content_id = _extract_douyin_item_id(_first_non_blank(row, ["content_id", "material_id", "content_url", "dedupe_key"]))
    url_item_id = content_id or _extract_douyin_numeric_id(_first_non_blank(row, ["material_id", "dedupe_key"]))
    link = _normalize_douyin_url(original_link) if url_item_id else ""
    error = ""
    if not url_item_id and original_link and _is_douyin_shortlink(original_link):
        detail = _fetch_douyin_detail(detail_source or original_link, detail_fetcher, harvester_cache)
        if detail:
            candidate = _candidate_from_payload("douyin", detail, "harvester_douyin_detail", 0.9, cache_hit=bool(detail.get("cache_hit")))
            if candidate.content_id or candidate.content_url:
                return candidate
        resolver = shortlink_resolver or resolve_douyin_shortlink
        resolved = resolver(original_link)
        if resolved:
            content_id = _extract_douyin_item_id(resolved)
            url_item_id = content_id
            link = _normalize_douyin_url(resolved)
            cached = harvester_cache.get_douyin(content_id)
            if cached:
                return _candidate_from_payload("douyin", cached, "harvester_cache", 0.88, cache_hit=True)
        else:
            error = "抖音短链未解析"
    elif url_item_id:
        cached = harvester_cache.get_douyin(url_item_id)
        if cached:
            return _candidate_from_payload("douyin", cached, "harvester_cache", 0.88, cache_hit=True)
    if not content_id and not url_item_id:
        return MetadataCandidate(platform="douyin", source="douyin_id_derived", error=error) if error else None
    if not link:
        link = _normalize_douyin_url(url_item_id)
    return MetadataCandidate(
        platform="douyin",
        content_id=content_id or raw_content_id,
        content_url=link,
        published_at=_published_date_from_douyin_item_id(url_item_id),
        source="douyin_id_derived",
        confidence=0.65,
        error=error,
        review_reason="抖音公开补全需复核",
    )


def _fetch_douyin_detail(
    value: str,
    detail_fetcher: Callable[[str], dict | None] | None,
    harvester_cache: HarvesterMetadataCache,
) -> dict | None:
    text = _clean_text(value)
    if not text:
        return None
    fetcher = detail_fetcher or (lambda raw: fetch_douyin_detail_from_harvester(raw, harvester_root=harvester_cache.root))
    try:
        payload = fetcher(text) or {}
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload:
        return None
    item_id = _extract_douyin_item_id(payload.get("id") or payload.get("content_id") or payload.get("link") or payload.get("itemUrl"))
    return _normalize_harvester_douyin_payload(item_id, payload)


def _xhs_candidate(
    row: pd.Series,
    cache: MetadataCache | None,
    harvester_cache: HarvesterMetadataCache,
    fetch_xhs_detail: Callable[[str, str], dict | None] | None,
) -> MetadataCandidate | None:
    original_link = _clean_text(row.get("content_url"))
    text = _first_non_blank(row, ["content_id", "material_id", "content_url", "dedupe_key"])
    note_id = _extract_xhs_note_id(text) or _clean_text(row.get("content_id"))
    if not note_id:
        return MetadataCandidate(
            platform="xhs",
            source="xhs_id_derived",
            error="小红书公开字段缺少笔记ID或可解析链接",
            link_openability="missing",
        )
    cached = cache.get("xhs", note_id) if cache is not None else None
    if cached:
        return _candidate_from_payload("xhs", cached, "metadata_cache", 0.86, cache_hit=True)
    harvester_payload = harvester_cache.get_xhs(note_id)
    if harvester_payload:
        if cache is not None:
            cache.set("xhs", note_id, harvester_payload)
        return _candidate_from_payload("xhs", harvester_payload, "harvester_cache", 0.88, cache_hit=True)
    if fetch_xhs_detail is not None:
        try:
            payload = fetch_xhs_detail(note_id, original_link) or {}
        except Exception as exc:
            return MetadataCandidate(
                platform="xhs",
                content_id=note_id,
                placeholder_url=_normalize_xhs_url(note_id),
                published_at=_published_date_from_xhs_note_id(note_id),
                source="xhs_downloader",
                link_openability="failed",
                link_source="xhs_downloader",
                error=f"XHS-Downloader 详情补齐失败：{exc}",
                review_reason="小红书外部补齐失败，可登录态补抓",
            )
        if payload:
            normalized = _normalize_harvester_xhs_payload(note_id, {**payload, "link_source": "xhs_downloader"})
            normalized["link_source"] = "xhs_downloader"
            if cache is not None:
                cache.set("xhs", note_id, normalized)
            return _candidate_from_payload("xhs", normalized, "xhs_downloader", 0.8)
    openable_link = original_link if _is_openable_xhs_url(original_link) else ""
    link_source = "original_excel" if openable_link else "derived_placeholder"
    return MetadataCandidate(
        platform="xhs",
        content_id=note_id,
        content_url=openable_link,
        placeholder_url=_normalize_xhs_url(note_id),
        published_at=_published_date_from_xhs_note_id(note_id),
        source="xhs_id_derived",
        link_openability="openable" if openable_link else "placeholder_only",
        link_source=link_source,
        confidence=0.7,
        review_reason="小红书公开补全需复核",
    )


def _candidate_from_payload(platform: str, payload: dict, source: str, confidence: float, *, cache_hit: bool = False) -> MetadataCandidate:
    content_url = _clean_text(payload.get("openable_url") or payload.get("link") or payload.get("content_url") or payload.get("noteUrl"))
    content_id = _clean_text(payload.get("id") or payload.get("content_id")) or (
        _extract_xhs_note_id(content_url) if platform == "xhs" else ""
    )
    if platform == "douyin" and not content_id:
        content_id = _extract_douyin_item_id(content_url)
    link_openability = _clean_text(payload.get("link_openability"))
    if platform == "xhs" and not link_openability:
        link_openability = "openable" if _is_openable_xhs_url(content_url) else ("placeholder_only" if content_id else "")
    link_source = _clean_text(payload.get("link_source")) or source
    return MetadataCandidate(
        platform=platform,
        content_id=content_id,
        content_url=content_url if platform != "xhs" or _is_openable_xhs_url(content_url) else "",
        placeholder_url=_clean_text(payload.get("placeholder_url")) or (_normalize_xhs_url(content_id) if platform == "xhs" and content_id else ""),
        title=_clean_text(payload.get("title")),
        tags=_normalize_tags(_clean_text(payload.get("tags"))) if platform == "xhs" else _clean_text(payload.get("tags")),
        published_at=_clean_text(payload.get("published_at") or payload.get("publishedAt")),
        account=_clean_text(payload.get("account") or payload.get("accountName")),
        content_type_candidate=_clean_text(payload.get("content_type") or payload.get("contentType")),
        link_openability=link_openability,
        link_source=link_source,
        source=source,
        confidence=confidence,
        cache_hit=cache_hit,
    )


def _apply_candidate(
    frame: pd.DataFrame,
    index: object,
    candidate: MetadataCandidate,
    fetched_at: str,
    *,
    batch_id: str = "",
) -> tuple[bool, bool, bool, bool, list[dict[str, object]]]:
    changed = False
    conflicts: list[str] = []
    records: list[dict[str, object]] = []
    for column, value in [
        ("content_url", candidate.content_url),
        ("content_id", candidate.content_id),
        ("title", candidate.title),
        ("source_time", candidate.published_at),
        ("account", candidate.account),
        ("author", candidate.account),
    ]:
        if column not in frame.columns or not value:
            continue
        current = _clean_text(frame.at[index, column])
        if not current:
            frame.at[index, column] = value
            changed = True
            records.append(_supplement_record(frame.loc[index], batch_id, column, current, value, candidate, "filled", "公开信息补齐"))
        elif column == "title" and candidate.platform == "douyin" and _can_replace_douyin_title(frame.loc[index], current, value, candidate):
            frame.at[index, column] = value
            changed = True
            records.append(_supplement_record(frame.loc[index], batch_id, column, current, value, candidate, "normalized", "抖音复制分享文本标题回填"))
        elif column == "content_id" and _can_replace_douyin_content_id(current, value, candidate):
            frame.at[index, column] = value
            changed = True
            records.append(_supplement_record(frame.loc[index], batch_id, column, current, value, candidate, "normalized", "抖音短链ID规范化"))
        elif _can_replace_with_normalized_url(column, current, value):
            frame.at[index, column] = value
            changed = True
            records.append(_supplement_record(frame.loc[index], batch_id, column, current, value, candidate, "normalized", "公开链接规范化"))
        elif _conflicts(column, current, value):
            conflicts.append(column)
            records.append(_supplement_record(frame.loc[index], batch_id, column, current, value, candidate, "conflict", "公开信息与Excel字段冲突"))
    if candidate.placeholder_url and candidate.platform == "xhs":
        if not _clean_text(frame.at[index, "xhs_placeholder_url"]):
            frame.at[index, "xhs_placeholder_url"] = candidate.placeholder_url
            changed = True
            records.append(
                _supplement_record(
                    frame.loc[index],
                    batch_id,
                    "xhs_placeholder_url",
                    "",
                    candidate.placeholder_url,
                    candidate,
                    "hint",
                    "小红书ID占位链接",
                )
            )
    if candidate.link_openability:
        frame.at[index, "link_openability"] = candidate.link_openability
    if candidate.link_source:
        frame.at[index, "link_source"] = candidate.link_source
    if candidate.tags:
        if not _clean_text(frame.at[index, "metadata_tags"]):
            frame.at[index, "metadata_tags"] = candidate.tags
            changed = True
            records.append(
                _supplement_record(
                    frame.loc[index],
                    batch_id,
                    "metadata_tags",
                    "",
                    candidate.tags,
                    candidate,
                    "filled",
                    "公开标签补齐",
                )
            )
    if candidate.content_type_candidate:
        if not _clean_text(frame.at[index, "metadata_content_type_candidate"]):
            frame.at[index, "metadata_content_type_candidate"] = candidate.content_type_candidate
            changed = True
            records.append(
                _supplement_record(
                    frame.loc[index],
                    batch_id,
                    "metadata_content_type_candidate",
                    "",
                    candidate.content_type_candidate,
                    candidate,
                    "filled",
                    "公开内容类型候选",
                )
            )

    if candidate.source:
        frame.at[index, "metadata_source"] = candidate.source
    frame.at[index, "metadata_confidence"] = candidate.confidence
    frame.at[index, "metadata_fetched_at"] = fetched_at
    if candidate.error:
        frame.at[index, "metadata_error"] = _append_text(frame.at[index, "metadata_error"], candidate.error)
        records.append(_supplement_record(frame.loc[index], batch_id, "public_metadata", "", "", candidate, "failed", candidate.error))
    reasons: list[str] = []
    if conflicts:
        reasons.append("公开信息与Excel字段冲突")
    if candidate.review_reason:
        reasons.append(candidate.review_reason)
    review_reason = _manual_review_reason(frame.loc[index], conflicts, candidate)
    review = bool(review_reason)
    if review_reason:
        reasons.append(review_reason)
    if reasons:
        reason_text = "；".join(reasons)
        frame.at[index, "metadata_review_reason"] = _append_text(frame.at[index, "metadata_review_reason"], reason_text)
        if review:
            frame.at[index, "review_reasons"] = _append_text(frame.at[index, "review_reasons"], review_reason)
    if review:
        frame.at[index, "needs_manual_review"] = True
    return changed, bool(conflicts), review, bool(reasons), records


def _supplement_record(
    row: pd.Series,
    batch_id: str,
    field_name: str,
    old_value: object,
    new_value: object,
    candidate: MetadataCandidate,
    status: str,
    reason: str,
) -> dict[str, object]:
    return {
        "batch": batch_id,
        "channel": _clean_text(row.get("channel")) or _clean_text(row.get("platform")),
        "content_id": _clean_text(row.get("content_id")) or candidate.content_id,
        "material_id": _clean_text(row.get("material_id")),
        "title": _clean_text(row.get("title")) or candidate.title,
        "field_name": field_name,
        "old_value": _clean_text(old_value),
        "new_value": _clean_text(new_value),
        "source": candidate.source,
        "confidence": candidate.confidence,
        "status": status,
        "reason": reason,
    }


def _manual_review_reason(row: pd.Series, conflicts: list[str], candidate: MetadataCandidate) -> str:
    if "content_id" in conflicts:
        return "内容ID冲突"
    if _has_high_value_conflict(conflicts, candidate) and _row_spend(row) >= HIGH_SPEND_REVIEW_THRESHOLD:
        return "高消耗公开信息冲突"
    if candidate.force_review:
        return candidate.review_reason or "公开补充需人工复核"
    return ""


def _has_high_value_conflict(conflicts: list[str], candidate: MetadataCandidate) -> bool:
    if not conflicts:
        return False
    if candidate.platform in {"douyin", "xhs"}:
        return False
    return any(column in {"title", "source_time", "account", "author"} for column in conflicts)


def _row_spend(row: pd.Series) -> float:
    try:
        return float(row.get("spend") or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_harvester_bilibili_payload(bvid: str, payload: dict) -> dict:
    tags = _clean_text(payload.get("tags"))
    return {
        "id": _clean_text(payload.get("bvid") or payload.get("id")) or bvid,
        "link": _clean_text(payload.get("videoUrl") or payload.get("link")) or _normalize_bilibili_url(bvid),
        "title": _clean_text(payload.get("title")),
        "tags": _normalize_tags(tags),
        "published_at": _clean_text(payload.get("publishedAt") or payload.get("published_at")),
    }


def _normalize_harvester_douyin_payload(item_id: str, payload: dict) -> dict:
    raw_url = _clean_text(
        payload.get("itemUrl")
        or payload.get("link")
        or payload.get("content_url")
        or payload.get("videoUrl")
        or payload.get("url")
    )
    resolved_id = _extract_douyin_item_id(payload.get("id") or payload.get("content_id") or raw_url) or item_id
    return {
        "id": resolved_id,
        "link": _normalize_douyin_url(resolved_id),
        "openable_url": _clean_text(
            payload.get("itemUrl")
            or payload.get("link")
            or payload.get("content_url")
            or payload.get("videoUrl")
            or payload.get("url")
        ),
        "title": _clean_text(payload.get("title")),
        "tags": _clean_text(payload.get("tags")),
        "account": _clean_text(payload.get("account") or payload.get("accountName") or payload.get("authorName")),
        "published_at": _clean_text(payload.get("published_at") or payload.get("publishedAt")) or _published_date_from_douyin_item_id(resolved_id),
        "content_type": _clean_text(payload.get("content_type") or payload.get("contentType")),
        "cache_hit": bool(payload.get("cache_hit")),
    }


def _normalize_harvester_xhs_payload(note_id: str, payload: dict) -> dict:
    raw_url = _clean_text(payload.get("openable_url") or payload.get("noteUrl") or payload.get("link") or payload.get("content_url"))
    resolved_id = _extract_xhs_note_id(raw_url) or _clean_text(payload.get("id") or payload.get("content_id")) or note_id
    openable_url = raw_url if _is_openable_xhs_url(raw_url) else ""
    return {
        "id": resolved_id,
        "openable_url": openable_url,
        "raw_url": raw_url,
        "placeholder_url": _normalize_xhs_url(resolved_id),
        "title": _clean_text(payload.get("title")),
        "tags": _normalize_tags(_clean_text(payload.get("tags"))),
        "account": _clean_text(payload.get("account") or payload.get("accountName") or payload.get("authorName")),
        "content_type": _clean_text(payload.get("content_type") or payload.get("contentType")),
        "published_at": _clean_text(payload.get("published_at") or payload.get("publishedAt")) or _published_date_from_xhs_note_id(resolved_id),
        "link_openability": "openable" if openable_url else "placeholder_only",
        "link_source": _clean_text(payload.get("link_source")) or "harvester_cache",
        "xsec_token_present": _has_xhs_xsec_token(raw_url),
    }


def _normalize_tags(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parts = [part.strip().lstrip("#") for part in re.split(r"[,\s，、#]+", text) if part.strip().lstrip("#")]
    return ",".join(dict.fromkeys(parts))


def _conflicts(column: str, current: str, incoming: str) -> bool:
    if not current or not incoming:
        return False
    if column in {"content_url"}:
        return not _is_same_content_url(current, incoming)
    return current.strip() != incoming.strip()


def _is_same_content_url(current: str, incoming: str) -> bool:
    if _canonical_url(current) == _canonical_url(incoming):
        return True
    return any(
        extractor(current) and extractor(current) == extractor(incoming)
        for extractor in [_extract_bilibili_bv, _extract_douyin_item_id, _extract_xhs_note_id]
    )


def _can_replace_with_normalized_url(column: str, current: str, incoming: str) -> bool:
    if column != "content_url":
        return False
    incoming_id = _extract_douyin_item_id(incoming)
    if not incoming_id:
        return False
    if _is_douyin_shortlink(current):
        return True
    if _is_douyin_shortlink(_first_url_from_text(current)):
        return True
    current_id = _extract_douyin_item_id(current)
    if current_id and current_id == incoming_id and _clean_text(current) != _clean_text(incoming):
        return True
    return False


def _can_replace_douyin_content_id(current: str, incoming: str, candidate: MetadataCandidate) -> bool:
    if candidate.platform != "douyin":
        return False
    if not _extract_douyin_item_id(incoming):
        return False
    current_text = _clean_text(current)
    if _is_douyin_shortlink(current_text):
        return True
    if _is_douyin_shortlink(_first_url_from_text(current_text)):
        return True
    if "douyin.com" in current_text.lower() or "iesdouyin.com" in current_text.lower():
        return True
    return False


def _can_replace_douyin_title(row: pd.Series, current: str, incoming: str, candidate: MetadataCandidate) -> bool:
    if candidate.platform != "douyin":
        return False
    current_text = _clean_text(current)
    incoming_text = _clean_text(incoming)
    if not current_text or not incoming_text or current_text == incoming_text:
        return False
    row_text = " ".join(
        _clean_text(row.get(column))
        for column in ["title", "content_url", "source_time"]
        if column in row.index
    )
    share_text = " ".join([current_text, row_text])
    if _looks_like_douyin_share_text(share_text):
        return True
    if clean_douyin_share_title(current_text) != current_text:
        return True
    if _is_douyin_shortlink(_first_url_from_text(row_text)):
        return True
    return False


def _first_non_blank(row: pd.Series, columns: list[str]) -> str:
    for column in columns:
        if column in row.index:
            value = _clean_text(row.get(column))
            if value:
                return value
    return ""


def _first_url_from_text(value: object) -> str:
    text = _clean_text(value)
    match = re.search(r"https?://[^\s]+", text)
    return match.group(0).rstrip("，,。.;；)") if match else ""


def _looks_like_douyin_share_text(value: object) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    return bool(
        _is_douyin_shortlink(_first_url_from_text(text))
        or re.search(r"复制此链接|打开Dou音搜索|打开抖音搜索|直接观看视频", text, flags=re.I)
        or re.search(r"^[\d.]+\s+[A-Za-z0-9]{1,12}\s*[:：/]", text)
    )


def _extract_bilibili_bv(value: object) -> str:
    match = re.search(r"\b(BV[0-9A-Za-z]{8,})\b", _clean_text(value))
    return match.group(1) if match else ""


def _extract_douyin_item_id(value: object) -> str:
    text = _clean_text(value)
    match = re.search(r"(?:douyin\.com|iesdouyin\.com)/(?:video|note)/(\d+)", text)
    if match:
        return match.group(1)
    match = re.search(r"/(?:video|note)/(\d+)", text)
    if match:
        return match.group(1)
    return text if re.fullmatch(r"\d{8,}", text) else ""


def _extract_douyin_numeric_id(value: object) -> str:
    text = _clean_text(value)
    if re.fullmatch(r"\d{8,}", text):
        return text
    if re.fullmatch(r"\d+(?:\.\d+)?e\+\d+", text, flags=re.IGNORECASE):
        try:
            return str(int(float(text)))
        except (OverflowError, ValueError):
            return ""
    return ""


def _extract_xhs_note_id(value: object) -> str:
    text = _clean_text(value)
    match = re.search(r"xiaohongshu\.com/(?:explore|discovery/item)/([^/?#\s]+)", text)
    if match:
        return match.group(1)
    match = re.search(r"/(?:explore|discovery/item)/([^/?#\s]+)", text)
    if match:
        return match.group(1)
    return text if re.fullmatch(r"[0-9a-fA-F]{8,}", text) else ""


def _normalize_bilibili_url(value: object) -> str:
    bvid = _extract_bilibili_bv(value)
    return f"https://www.bilibili.com/video/{bvid}/" if bvid else ""


def _normalize_douyin_url(value: object) -> str:
    text = _clean_text(value)
    item_id = _extract_douyin_item_id(text)
    if not item_id:
        return text
    item_type = "note" if "/note/" in text else "video"
    return f"https://www.douyin.com/{item_type}/{item_id}"


def _normalize_xhs_url(note_id: str) -> str:
    return f"https://www.xiaohongshu.com/discovery/item/{note_id}" if note_id else ""


def _is_openable_xhs_url(value: object) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    host = urlparse(text).netloc.lower()
    if host.endswith("xhslink.com"):
        return True
    if "xiaohongshu.com" not in host:
        return False
    note_id = _extract_xhs_note_id(text)
    if not note_id:
        return False
    return _has_xhs_xsec_token(text) or "xsec_source=" in text or "/explore/" in text and "source=webshare" in text


def _has_xhs_xsec_token(value: object) -> bool:
    return "xsec_token=" in _clean_text(value)


def _is_douyin_shortlink(value: object) -> bool:
    text = _clean_text(value)
    host = urlparse(text).netloc.lower()
    return host in {"v.douyin.com", "www.iesdouyin.com"}


def _published_date_from_xhs_note_id(note_id: str) -> str:
    match = re.match(r"^([0-9a-fA-F]{8})", _clean_text(note_id))
    if not match:
        return ""
    return _date_from_epoch(int(match.group(1), 16))


def _published_date_from_douyin_item_id(item_id: str) -> str:
    text = _clean_text(item_id)
    if not re.fullmatch(r"\d{8,}", text):
        return ""
    seconds = int(text) >> 32
    if seconds < 1_000_000_000 or seconds > 2_200_000_000:
        return ""
    return _date_from_epoch(seconds)


def _date_from_epoch(value: object) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    beijing = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(beijing).strftime("%Y-%m-%d")


def _canonical_url(value: str) -> str:
    return _clean_text(value).rstrip("/")


def _append_text(current: object, addition: str) -> str:
    left = _clean_text(current)
    right = _clean_text(addition)
    if not right:
        return left
    if not left:
        return right
    parts = [part.strip() for part in left.split("；") if part.strip()]
    if right in parts:
        return left
    parts.append(right)
    return "；".join(parts)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _is_truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "待审核", "待复核", "需复核"}
    return bool(value)
