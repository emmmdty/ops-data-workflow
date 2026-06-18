"""Match ad feedback assets to owned Feishu content ledgers."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

import pandas as pd

from .platform_normalizers.bilibili import extract_bvid, normalize_bilibili_url
from .platform_normalizers.common import normalize_url, platform_label, strip_title_noise, text_value
from .platform_normalizers.xhs import extract_xhs_id, normalize_xhs_url
from .title_matching import normalized_title_key


MATCH_COLUMNS = [
    "match_status",
    "match_source",
    "match_key",
    "matched_ledger_title",
    "matched_content_type",
    "matched_category_l1",
    "matched_category_l2",
    "matched_bilibili_content_type",
    "matched_account",
    "match_confidence",
    "match_reason",
]


@dataclass(frozen=True)
class LedgerIndexes:
    by_id: dict[tuple[str, str], pd.Series]
    by_url: dict[tuple[str, str], pd.Series]
    douyin_by_title: dict[str, list[pd.Series]]
    has_platform_rows: set[str]


class XhsIdMatcher:
    def match(self, row: pd.Series, indexes: LedgerIndexes):
        work_id = text_value(row.get("work_id", ""))
        if work_id and ("小红书", work_id) in indexes.by_id:
            return indexes.by_id[("小红书", work_id)], "作品ID", work_id, 1.0
        return None


class BilibiliBvMatcher:
    def match(self, row: pd.Series, indexes: LedgerIndexes):
        work_id = text_value(row.get("work_id", ""))
        if work_id and ("B站", work_id) in indexes.by_id:
            return indexes.by_id[("B站", work_id)], "BV号", work_id, 1.0
        return None


class DouyinIdMatcher:
    def match(self, row: pd.Series, indexes: LedgerIndexes):
        for work_id in _douyin_row_work_ids(row):
            if ("抖音", work_id) in indexes.by_id:
                return indexes.by_id[("抖音", work_id)], "作品ID", work_id, 1.0
        return None


class DouyinTitleMatcher:
    threshold = 0.9

    def match(self, row: pd.Series, indexes: LedgerIndexes):
        if text_value(row.get("normalization_reason", "")).find("抖音标题非真实作品标题") >= 0 and not _allow_suspect_douyin_title_match(row):
            return None
        requires_welfare = _requires_douyin_welfare_account(row)
        best: tuple[pd.Series, str, float] | None = None
        row_keys = _row_title_keys(row)
        for row_key in row_keys:
            for ledger_key, items in indexes.douyin_by_title.items():
                score = _title_similarity(row_key, ledger_key)
                if score < self.threshold:
                    continue
                for item in items:
                    if requires_welfare and not _ledger_has_douyin_welfare_account(item):
                        continue
                    if best is None or score > best[2]:
                        best = (item, row_key, score)
        if best is None:
            return None
        item, key, score = best
        return item, "标准标题", key, score


def match_assets_to_ledger(canonical: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    matched = canonical.copy()
    for column in MATCH_COLUMNS:
        if column not in matched.columns:
            matched[column] = "" if column != "match_confidence" else 0.0
    if matched.empty:
        return matched
    indexes = _build_ledger_indexes(ledger)
    for index, row in matched.iterrows():
        platform = platform_label(row)
        if platform not in {"抖音", "小红书", "B站"}:
            _set_unmatched(matched, index, "平台不在复盘范围")
            continue
        candidate = _match_row(row, platform, indexes)
        if candidate is None:
            reason = "飞书台账缺失候选" if platform not in indexes.has_platform_rows else "未匹配飞书自有内容"
            _set_unmatched(matched, index, reason)
            continue
        item, source, key, confidence = candidate
        matched.at[index, "match_status"] = "已匹配"
        matched.at[index, "match_source"] = source
        matched.at[index, "match_key"] = key
        matched.at[index, "matched_ledger_title"] = text_value(item.get("title", ""))
        matched.at[index, "matched_content_type"] = text_value(item.get("content_type", ""))
        matched.at[index, "matched_category_l1"] = text_value(item.get("category_l1", ""))
        matched.at[index, "matched_category_l2"] = text_value(item.get("category_l2", ""))
        matched.at[index, "matched_bilibili_content_type"] = text_value(item.get("bilibili_content_type", ""))
        matched.at[index, "matched_account"] = text_value(item.get("account", ""))
        matched.at[index, "match_confidence"] = confidence
        matched.at[index, "match_reason"] = ""
        matched_work_id = _ledger_work_id(platform, item)
        matched_work_url = _ledger_work_url(platform, item, matched_work_id)
        _fill_if_blank(matched, index, "content_category", item.get("content_type", ""))
        _fill_if_blank(matched, index, "manual_category", item.get("content_type", ""))
        _fill_if_blank(matched, index, "category_l1", item.get("category_l1", ""))
        _fill_if_blank(matched, index, "category_l2", item.get("category_l2", "") or item.get("content_type", ""))
        _fill_if_blank(matched, index, "account", item.get("account", ""))
        _fill_if_blank(matched, index, "tags", item.get("tags", ""))
        _fill_if_blank(matched, index, "work_id", matched_work_id)
        _fill_if_blank(matched, index, "work_url", matched_work_url)
        _fill_if_blank(matched, index, "content_url", matched_work_url or item.get("content_url", ""))
        if matched_work_id:
            matched.at[index, "content_id"] = matched_work_id
        _fill_title_if_blank_or_identity(matched, index, platform, item.get("title", ""))
    return matched


def _build_ledger_indexes(ledger: pd.DataFrame) -> LedgerIndexes:
    by_id: dict[tuple[str, str], pd.Series] = {}
    by_url: dict[tuple[str, str], pd.Series] = {}
    douyin_by_title: dict[str, list[pd.Series]] = {}
    has_platform_rows: set[str] = set()
    if ledger is None or ledger.empty:
        return LedgerIndexes(by_id, by_url, douyin_by_title, has_platform_rows)
    for _, row in ledger.iterrows():
        platform = platform_label(row.get("platform", ""))
        if platform not in {"抖音", "小红书", "B站"}:
            continue
        has_platform_rows.add(platform)
        work_id = _ledger_work_id(platform, row)
        work_url = _ledger_work_url(platform, row, work_id)
        if work_id and (platform, work_id) not in by_id:
            by_id[(platform, work_id)] = row
        if work_url and (platform, work_url) not in by_url:
            by_url[(platform, work_url)] = row
        if platform == "抖音":
            for title_key in _ledger_title_keys(row):
                if title_key:
                    douyin_by_title.setdefault(title_key, []).append(row)
    return LedgerIndexes(by_id, by_url, douyin_by_title, has_platform_rows)


def _match_row(row: pd.Series, platform: str, indexes: LedgerIndexes):
    if platform == "小红书":
        return XhsIdMatcher().match(row, indexes)
    if platform == "B站":
        return BilibiliBvMatcher().match(row, indexes)
    if platform == "抖音":
        return DouyinIdMatcher().match(row, indexes) or DouyinTitleMatcher().match(row, indexes)
    return None


def _douyin_row_work_ids(row: pd.Series) -> list[str]:
    from .platform_normalizers.douyin import extract_douyin_item_id

    values = []
    for column in ["work_id", "content_id", "content_url", "work_url"]:
        values.append(extract_douyin_item_id(row.get(column, "")))
    return _unique_nonblank(values)


def _ledger_work_id(platform: str, row: pd.Series) -> str:
    text = text_value(row.get("content_id", ""))
    url = text_value(row.get("content_url", ""))
    if platform == "小红书":
        return extract_xhs_id(text) or extract_xhs_id(url)
    if platform == "B站":
        return extract_bvid(text) or extract_bvid(url)
    if platform == "抖音":
        from .platform_normalizers.douyin import extract_douyin_item_id

        return extract_douyin_item_id(text) or extract_douyin_item_id(url)
    return text


def _ledger_work_url(platform: str, row: pd.Series, work_id: str) -> str:
    return _normalized_work_url(platform, row.get("content_url", "")) or (
        normalize_xhs_url(work_id) if platform == "小红书" else normalize_bilibili_url(work_id) if platform == "B站" else ""
    )


def _ledger_title_keys(row: pd.Series) -> list[str]:
    keys: list[str] = []
    for column in ["title_key_no_tags", "title_key", "title"]:
        keys.extend(_title_key_variants(row.get(column, "")))
    return _unique_nonblank(keys)


def _row_title_keys(row: pd.Series) -> list[str]:
    keys: list[str] = []
    for column in ["standard_title", "title"]:
        keys.extend(_title_key_variants(row.get(column, "")))
    return _unique_nonblank(keys)


def _title_key_variants(value: object) -> list[str]:
    text = text_value(value)
    text_variants = [text]
    stripped = _strip_douyin_title_prefix_noise(text)
    if stripped != text:
        text_variants.append(stripped)
    for candidate in list(text_variants):
        text_variants.extend(_hashtag_mixed_title_variants(candidate))

    variants: list[str] = []
    for candidate in _unique_nonblank(text_variants):
        key = normalized_title_key(candidate)
        variants.append(key)
        if key:
            compact = _strip_compact_date_prefix(key)
            if compact != key:
                variants.append(compact)
    return _unique_nonblank(variants)


def _hashtag_mixed_title_variants(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    variants: list[str] = []
    leading_removed = re.sub(r"^\s*(?:[#＃]\s*[\w\u4e00-\u9fff]{1,16}\s+)+", "", text)
    if leading_removed != text:
        variants.append(leading_removed)
    marker_label_removed = re.sub(r"[#＃]\s*[\w\u4e00-\u9fff]{1,16}[，,、]\s*", "，", text)
    if marker_label_removed != text:
        variants.append(marker_label_removed)
    return variants


def _allow_suspect_douyin_title_match(row: pd.Series) -> bool:
    for column in ["title", "original_title"]:
        text = text_value(row.get(column, ""))
        if not re.match(r"^\s*[#＃]", text):
            continue
        for variant in _hashtag_mixed_title_variants(text):
            if normalized_title_key(variant):
                return True
    return False


def _requires_douyin_welfare_account(row: pd.Series) -> bool:
    return _row_text_contains(row, ["standard_title", "title", "original_title", "metadata_tags"], "同花顺福利官")


def _ledger_has_douyin_welfare_account(row: pd.Series) -> bool:
    return _row_text_contains(row, ["account", "tags", "title", "content_type", "content_type_review"], "同花顺福利官")


def _row_text_contains(row: pd.Series, columns: list[str], needle: str) -> bool:
    return any(needle in text_value(row.get(column, "")) for column in columns)


def _strip_douyin_title_prefix_noise(value: str) -> str:
    text = str(value or "").strip()
    for _ in range(3):
        previous = text
        text = re.sub(r"^\s*(?:20\d{2}[-/.年]\s*)?\d{1,2}[-/.月]\s*\d{1,2}日?\s*", "", text)
        text = re.sub(r"^\s*\d{1,2}\s*(?:am|pm)\s*", "", text, flags=re.I)
        text = re.sub(r"^\s*[:：]\s*", "", text)
        if text == previous:
            break
    return text.strip()


def _strip_compact_date_prefix(key: str) -> str:
    text = str(key or "")
    match = re.match(r"^(0?[1-9]|1[0-2])(0?[1-9]|[12]\d|3[01])(.+)$", text)
    if not match:
        return text
    return match.group(3)


def _unique_nonblank(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _normalized_work_url(platform: str, value: object) -> str:
    raw = normalize_url(value)
    if platform == "小红书":
        note_id = extract_xhs_id(raw)
        return normalize_xhs_url(note_id) if note_id else raw
    if platform == "B站":
        bvid = extract_bvid(raw)
        return normalize_bilibili_url(bvid) if bvid else raw
    if platform == "抖音":
        from .platform_normalizers.douyin import extract_douyin_item_id

        item_id = extract_douyin_item_id(raw)
        return f"https://www.douyin.com/video/{item_id}" if item_id else raw
    return raw


def _set_unmatched(frame: pd.DataFrame, index: object, reason: str) -> None:
    frame.at[index, "match_status"] = "未匹配"
    frame.at[index, "match_source"] = ""
    frame.at[index, "match_key"] = ""
    frame.at[index, "matched_ledger_title"] = ""
    frame.at[index, "matched_content_type"] = ""
    frame.at[index, "matched_category_l1"] = ""
    frame.at[index, "matched_category_l2"] = ""
    frame.at[index, "matched_bilibili_content_type"] = ""
    frame.at[index, "matched_account"] = ""
    frame.at[index, "match_confidence"] = 0.0
    frame.at[index, "match_reason"] = reason


def _fill_if_blank(frame: pd.DataFrame, index: object, column: str, value: object) -> None:
    if column not in frame.columns:
        frame[column] = ""
    if not text_value(frame.at[index, column]) and text_value(value):
        frame.at[index, column] = text_value(value)


def _fill_title_if_blank_or_identity(frame: pd.DataFrame, index: object, platform: str, value: object) -> None:
    column_missing = "title" not in frame.columns
    if column_missing:
        frame["title"] = ""
    title = text_value(value)
    if not title:
        return
    current = text_value(frame.at[index, "title"])
    if column_missing or not current or _is_identity_placeholder_title(platform, current, frame.loc[index]):
        frame.at[index, "title"] = title


def _is_identity_placeholder_title(platform: str, title: str, row: pd.Series) -> bool:
    current = text_value(title)
    if not current:
        return True
    identity_values = {
        text_value(row.get(column, ""))
        for column in ["work_id", "content_id", "material_id", "match_key"]
        if text_value(row.get(column, ""))
    }
    if current in identity_values:
        return True
    if platform == "B站":
        bvid = extract_bvid(current)
        return bool(bvid and bvid == current)
    if platform == "小红书":
        note_id = extract_xhs_id(current)
        return bool(note_id and note_id == text_value(row.get("work_id", "")))
    return False


def _title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()
