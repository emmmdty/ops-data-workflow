"""Optional public metadata enrichment for content rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Callable
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests


METADATA_COLUMNS = [
    "metadata_source",
    "metadata_confidence",
    "metadata_fetched_at",
    "metadata_error",
    "metadata_review_reason",
    "metadata_tags",
    "metadata_content_type_candidate",
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
    title: str = ""
    tags: str = ""
    published_at: str = ""
    account: str = ""
    content_type_candidate: str = ""
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
    for index, row in enriched.iterrows():
        platform = _platform_name(row)
        candidate: MetadataCandidate | None = None
        if platform == "bilibili":
            candidate = _bilibili_candidate(row, cache, harvester_cache, fetch_bilibili, allow_public_api)
        elif platform == "douyin":
            candidate = _douyin_candidate(row, resolve_douyin_shortlink)
        elif platform == "xhs":
            candidate = _xhs_candidate(row)
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

    def get_bilibili(self, bvid: str) -> dict | None:
        if not bvid:
            return None
        cached = self._read_detail_cache(bvid)
        if cached:
            return cached
        return self._read_output_metadata().get(bvid)

    def _read_detail_cache(self, bvid: str) -> dict | None:
        path = self.root / ".runtime" / "detail-cache" / "bilibili" / f"{bvid}.json"
        if not path.exists():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return _normalize_harvester_bilibili_payload(bvid, parsed)

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
    shortlink_resolver: Callable[[str], str] | None,
) -> MetadataCandidate | None:
    original_link = _clean_text(row.get("content_url"))
    raw_content_id = _clean_text(row.get("content_id"))
    content_id = _extract_douyin_item_id(_first_non_blank(row, ["content_id", "material_id", "content_url", "dedupe_key"]))
    url_item_id = content_id or _extract_douyin_numeric_id(_first_non_blank(row, ["material_id", "dedupe_key"]))
    link = _normalize_douyin_url(original_link) if url_item_id else ""
    error = ""
    if not url_item_id and original_link and _is_douyin_shortlink(original_link):
        resolver = shortlink_resolver or resolve_douyin_shortlink
        resolved = resolver(original_link)
        if resolved:
            content_id = _extract_douyin_item_id(resolved)
            url_item_id = content_id
            link = _normalize_douyin_url(resolved)
        else:
            error = "抖音短链未解析"
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


def _xhs_candidate(row: pd.Series) -> MetadataCandidate | None:
    text = _first_non_blank(row, ["content_id", "material_id", "content_url", "dedupe_key"])
    note_id = _extract_xhs_note_id(text) or _clean_text(row.get("content_id"))
    if not note_id:
        return MetadataCandidate(
            platform="xhs",
            source="xhs_id_derived",
            error="小红书公开字段缺少笔记ID或可解析链接",
        )
    return MetadataCandidate(
        platform="xhs",
        content_id=note_id,
        content_url=_normalize_xhs_url(note_id),
        published_at=_published_date_from_xhs_note_id(note_id),
        source="xhs_id_derived",
        confidence=0.7,
        review_reason="小红书公开补全需复核",
    )


def _candidate_from_payload(platform: str, payload: dict, source: str, confidence: float, *, cache_hit: bool = False) -> MetadataCandidate:
    return MetadataCandidate(
        platform=platform,
        content_id=_clean_text(payload.get("id") or payload.get("content_id")),
        content_url=_clean_text(payload.get("link") or payload.get("content_url")),
        title=_clean_text(payload.get("title")),
        tags=_clean_text(payload.get("tags")),
        published_at=_clean_text(payload.get("published_at") or payload.get("publishedAt")),
        account=_clean_text(payload.get("account") or payload.get("accountName")),
        content_type_candidate=_clean_text(payload.get("content_type") or payload.get("contentType")),
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
        elif _can_replace_with_normalized_url(column, current, value):
            frame.at[index, column] = value
            changed = True
            records.append(_supplement_record(frame.loc[index], batch_id, column, current, value, candidate, "normalized", "公开链接规范化"))
        elif _conflicts(column, current, value):
            conflicts.append(column)
            records.append(_supplement_record(frame.loc[index], batch_id, column, current, value, candidate, "conflict", "公开信息与Excel字段冲突"))
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
    if _is_douyin_shortlink(current) and _extract_douyin_item_id(incoming):
        return True
    return False


def _first_non_blank(row: pd.Series, columns: list[str]) -> str:
    for column in columns:
        if column in row.index:
            value = _clean_text(row.get(column))
            if value:
                return value
    return ""


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


def _is_douyin_shortlink(value: object) -> bool:
    host = urlparse(_clean_text(value)).netloc.lower()
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
