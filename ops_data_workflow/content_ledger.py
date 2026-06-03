"""Parse harvester Feishu exports and enrich ad rows with content metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
import os
from pathlib import Path
import re
from typing import Iterable

import pandas as pd
import yaml

from .categories import category_from_tags
from .field_mapping import load_field_mapping, standardize_content_form
from .source_storage import latest_reference_workbook
from .title_matching import extract_historical_title, normalized_title_key


TABULAR_SUFFIXES = {".csv", ".xls", ".xlsx"}

LEDGER_COLUMNS = [
    "platform",
    "published_date",
    "content_url",
    "content_id",
    "account",
    "title",
    "tags",
    "content_type",
    "content_type_review",
    "filter_status",
    "source_file",
    "source_sheet",
    "source_row",
    "source_path",
    "title_key",
    "title_key_no_tags",
]


@dataclass(frozen=True)
class LedgerMatch:
    row: pd.Series
    source: str
    key: str
    duplicate_count: int
    fill_allowed: bool = True
    risk_reason: str = ""


def load_content_ledger(
    input_dir: Path,
    *,
    default_year: int = 2026,
    config_path: Path | None = None,
) -> pd.DataFrame:
    """Load content-ledger rows from harvester Feishu export workbooks."""
    input_dir = Path(input_dir)
    frames: list[pd.DataFrame] = []
    source_files: set[str] = set()
    specs: list[tuple[Path, Path | None, str | None]] = [
        (path, input_dir, None)
        for path in _iter_tabular_files(input_dir)
    ]
    specs.extend(_configured_ledger_specs(config_path))
    seen_paths: set[Path] = set()
    for path, root, source_file in specs:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        frame = parse_content_ledger_file(path, root=root, source_file=source_file)
        if frame.empty:
            continue
        frames.append(frame)
        source_files.update(str(value) for value in frame["source_path"].dropna().unique())
    if not frames:
        empty = pd.DataFrame(columns=LEDGER_COLUMNS)
        empty.attrs["source_files"] = set()
        return empty
    ledger = _dedupe_ledger_by_earliest_date(pd.concat(frames, ignore_index=True), default_year=default_year)
    source_file_set = {str(Path(value).resolve()) for value in source_files}
    result = ledger[LEDGER_COLUMNS].copy()
    result.attrs["source_files"] = source_file_set
    return result


def load_latest_reference_ledger(reference_dir: Path, *, default_year: int = 2026) -> pd.DataFrame:
    """Load only the newest human-maintained content ledger from data/reference."""
    workbook = latest_reference_workbook(reference_dir)
    if workbook is None:
        empty = pd.DataFrame(columns=LEDGER_COLUMNS)
        empty.attrs["source_files"] = set()
        return empty
    frame = parse_content_ledger_file(workbook, root=Path(reference_dir))
    if frame.empty:
        frame.attrs["source_files"] = set()
        return frame
    ledger = _dedupe_ledger_by_earliest_date(frame, default_year=default_year)
    result = ledger[LEDGER_COLUMNS].copy()
    result.attrs["source_files"] = {str(workbook.resolve())}
    return result


def parse_content_ledger_file(
    path: Path,
    *,
    root: Path | None = None,
    source_file: str | None = None,
) -> pd.DataFrame:
    path = Path(path)
    rows: list[dict[str, object]] = []
    source_file_label = source_file or _source_file_label(path, root)
    for sheet_name, frame, header_row in _ledger_sheets(path):
        platform = _platform_from_sheet(sheet_name)
        if not platform:
            continue
        for offset, item in frame.iterrows():
            record = _ledger_record(
                item,
                platform,
                source_file=source_file_label,
                source_sheet=sheet_name,
                source_path=str(path.resolve()),
                source_row=int(header_row) + 2 + int(offset),
            )
            if record:
                rows.append(record)
    if not rows:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    return pd.DataFrame(rows, columns=LEDGER_COLUMNS)


def apply_content_ledger(
    canonical: pd.DataFrame,
    ledger: pd.DataFrame,
    *,
    douyin_id_bridge: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Backfill category/link/title fields from content-ledger rows."""
    enriched = canonical.copy()
    for column in [
        "ledger_match_source",
        "ledger_match_key",
        "ledger_content_type",
        "ledger_content_type_review",
        "ledger_filter_status",
        "ledger_source_file",
        "ledger_source_sheet",
        "ledger_source_row",
        "match_risk_level",
        "match_risk_reason",
        "manual_category_source",
        "review_reasons",
    ]:
        if column not in enriched.columns:
            enriched[column] = ""
    if "needs_manual_review" not in enriched.columns:
        enriched["needs_manual_review"] = False
    if "content_form" not in enriched.columns:
        enriched["content_form"] = ""

    if ledger.empty and (douyin_id_bridge is None or douyin_id_bridge.empty):
        return enriched

    field_mapping = load_field_mapping()
    lookup = _build_lookup(ledger)
    fuzzy_rows = _fuzzy_ledger_rows(ledger)
    bridge_lookup = _build_douyin_bridge_lookup(douyin_id_bridge)
    for index, row in enriched.iterrows():
        match = _match_row(row, lookup, fuzzy_rows, bridge_lookup)
        if match is None:
            continue
        item = match.row
        _record_match_metadata(enriched, index, match, item)
        if not match.fill_allowed:
            _mark_match_risk(enriched, index, match.risk_reason)
            continue
        if match.duplicate_count > 1:
            _mark_match_risk(enriched, index, f"投稿台账存在 {match.duplicate_count} 条同键记录")

        _fill_blank(enriched, index, "content_url", item.get("content_url", ""))
        _fill_blank(enriched, index, "content_id", item.get("content_id", ""))
        _fill_title(enriched, index, item.get("title", ""))
        _fill_blank(enriched, index, "account", item.get("account", ""))
        _fill_blank(enriched, index, "author", item.get("account", ""))

        content_type = _clean_text(item.get("content_type", ""))
        if content_type:
            current = _clean_text(enriched.at[index, "manual_category"] if "manual_category" in enriched.columns else "")
            if not current:
                enriched.at[index, "manual_category"] = content_type
                enriched.at[index, "manual_category_source"] = "投稿台账补全"
            elif current != content_type:
                _mark_match_risk(enriched, index, f"原始内容类型 {current} 与投稿台账 {content_type} 不一致")
                enriched.at[index, "review_reasons"] = _append_text(
                    enriched.at[index, "review_reasons"] if "review_reasons" in enriched.columns else "",
                    "投稿台账分类冲突",
                )
                enriched.at[index, "needs_manual_review"] = True
            _fill_content_form_from_type(enriched, index, content_type, field_mapping)
        if match.risk_reason:
            _mark_match_risk(enriched, index, match.risk_reason)
    return enriched


def account_match_label(platform: object, account: object) -> str:
    text = _clean_text(account)
    if not text:
        return ""
    platform_name = _platform_name(platform)
    if platform_name == "抖音":
        if re.search(r"达人|达人内容", text):
            return "达人内容"
        if re.search(r"福利官|新手福利", text):
            return "福利官"
        if re.search(r"(同花顺|同顺)?问财", text):
            return "问财"
        if re.search(r"同花顺投资|^投资号$", text):
            return "投资号"
        if re.search(r"(同花顺|同顺)财经|^财经号$", text):
            return "财经号"
        if re.search(r"同花顺财富|同花顺理财|^理财$", text):
            return "理财"
        if re.search(r"(同花顺|同顺)股民社区|^股民社区$", text):
            return "股民社区"
        if re.search(r"同花顺期货通|^期货通$", text):
            return "期货通"
    if platform_name == "小红书":
        if re.search(r"研习社", text):
            return "研习社"
        if re.search(r"同花顺投资|^投资号$", text):
            return "投资号"
        if re.search(r"(同花顺|同顺)股民社区|^股民社区$", text):
            return "股民社区"
        if re.search(r"同花顺财富|同花顺理财|^理财$", text):
            return "理财"
        if re.search(r"(同花顺|同顺)财经|^财经号$", text):
            return "财经号"
        if re.search(r"(同花顺|同顺)?问财", text):
            return "问财"
        if re.search(r"喵懂投资", text):
            return "喵懂投资"
    if platform_name == "B站" and re.search(r"同花顺投资|^投资号$", text):
        return "投资号"
    return text


def platform_match_label(row: pd.Series | object) -> str:
    if isinstance(row, pd.Series):
        return _platform_name(" ".join(str(row.get(column, "")) for column in ["platform_group", "platform", "channel"]))
    return _platform_name(row)


def _ledger_sheets(path: Path) -> list[tuple[str, pd.DataFrame, int]]:
    if _is_csv(path):
        try:
            frame = _read_table(path)
        except Exception:
            return []
        return [("CSV", frame, 0)] if _is_ledger_frame(frame) else []

    sheets: list[tuple[str, pd.DataFrame, int]] = []
    try:
        with pd.ExcelFile(path) as workbook:
            sheet_names = list(workbook.sheet_names)
    except Exception:
        return []
    for sheet_name in sheet_names:
        best: tuple[pd.DataFrame, int, int] | None = None
        for header in range(0, 5):
            try:
                frame = _read_table(path, sheet_name=sheet_name, header=header)
            except Exception:
                continue
            frame = frame.dropna(axis=1, how="all")
            score = _ledger_score(frame)
            if best is None or score > best[2]:
                best = (frame, header, score)
        if best and best[2] >= 2 and _is_ledger_frame(best[0]):
            sheets.append((str(sheet_name), best[0], best[1]))
    return sheets


def _is_ledger_frame(frame: pd.DataFrame) -> bool:
    columns = {str(column).strip() for column in frame.columns}
    if "内容链接" not in columns:
        return False
    if columns.intersection({"花费", "总花费", "消费", "消耗", "展示量", "展示数", "应用激活数"}):
        return False
    return bool(columns.intersection({"编号", "投稿时间", "笔记ID", "短链id", "tag词"}))


def _ledger_score(frame: pd.DataFrame) -> int:
    columns = {str(column).strip() for column in frame.columns}
    tokens = {"编号", "投稿时间", "内容链接", "账号", "内容类型", "笔记ID", "短链id", "标题", "tag词"}
    return len(columns.intersection(tokens))


def _ledger_record(
    row: pd.Series,
    platform: str,
    *,
    source_file: str,
    source_sheet: str,
    source_path: str,
    source_row: int,
) -> dict[str, object] | None:
    link_text = _first_value(row, ["内容链接", "作品链接", "笔记链接", "视频链接"])
    content_url = _extract_url(link_text)
    title = _first_value(row, ["标题", "视频标题", "内容标题"])
    if not title:
        title = _title_from_link_text(link_text)
    tags = _first_value(row, ["tag词", "TAG词", "标签", "话题"])
    if not tags:
        tags = _tags_from_text(link_text)
    content_type = _first_value(row, ["内容类型", "内容分类", "栏目"])
    if not content_type:
        content_type = category_from_tags(f"{tags} {link_text}")
    content_id = _first_value(row, ["笔记ID", "短链id", "视频/笔记id", "内容ID"])
    if not content_id:
        content_id = _extract_content_id(platform, content_url or link_text)
    account = _first_value(row, ["账号", "账号名称", "发布作者"])
    if not any([content_url, content_id, title]):
        return None
    if _looks_like_separator(row):
        return None
    return {
        "platform": platform,
        "published_date": _first_value(row, ["投稿时间", "时间", "发布日期"]),
        "content_url": content_url,
        "content_id": content_id,
        "account": account,
        "title": title,
        "tags": tags,
        "content_type": content_type,
        "content_type_review": _first_value(row, ["内容类型标签审核"]),
        "filter_status": _first_value(row, ["筛选状态", "是否投放成功"]),
        "source_file": source_file,
        "source_sheet": source_sheet,
        "source_row": source_row,
        "source_path": source_path,
        "title_key": normalized_title_key(title),
        "title_key_no_tags": _normalized_tagless_title_key(title or link_text),
    }


def _build_lookup(ledger: pd.DataFrame) -> dict[tuple[str, str, str, str], list[pd.Series]]:
    lookup: dict[tuple[str, str, str, str], list[pd.Series]] = {}
    if ledger.empty:
        return lookup
    for _, row in ledger.iterrows():
        platform = platform_match_label(row.get("platform", ""))
        account = account_match_label(platform, row.get("account", ""))
        content_id = _clean_text(row.get("content_id", ""))
        title_key = _clean_text(row.get("title_key", "")) or normalized_title_key(row.get("title", ""))
        title_key_no_tags = _clean_text(row.get("title_key_no_tags", "")) or _normalized_tagless_title_key(
            row.get("title", "")
        )
        if content_id:
            lookup.setdefault((platform, account, "id", content_id), []).append(row)
            if platform in {"小红书", "B站"} and account:
                lookup.setdefault((platform, "", "id", content_id), []).append(row)
        if platform == "抖音" and title_key_no_tags:
            lookup.setdefault((platform, account, "title_no_tags", title_key_no_tags), []).append(row)
            if account:
                lookup.setdefault((platform, "", "title_no_tags", title_key_no_tags), []).append(row)
        elif platform == "抖音" and title_key:
            lookup.setdefault((platform, account, "title_no_tags", title_key), []).append(row)
            if account:
                lookup.setdefault((platform, "", "title_no_tags", title_key), []).append(row)
    return lookup


def _build_douyin_bridge_lookup(
    bridge: pd.DataFrame | None,
) -> dict[tuple[str, str], list[pd.Series]]:
    lookup: dict[tuple[str, str], list[pd.Series]] = {}
    if bridge is None or bridge.empty:
        return lookup
    for _, row in bridge.iterrows():
        id_type = _clean_text(row.get("id_type", ""))
        id_value = _clean_text(row.get("id_value", ""))
        if id_type and id_value:
            lookup.setdefault((id_type, id_value), []).append(row)
    return lookup


def _fuzzy_ledger_rows(ledger: pd.DataFrame) -> list[pd.Series]:
    if ledger.empty:
        return []
    rows: list[pd.Series] = []
    for _, row in ledger.iterrows():
        if platform_match_label(row.get("platform", "")) != "抖音":
            continue
        title_key = _clean_text(row.get("title_key_no_tags", "")) or _normalized_tagless_title_key(row.get("title", ""))
        if len(title_key) >= 6:
            rows.append(row)
    return rows


def _match_row(
    row: pd.Series,
    lookup: dict[tuple[str, str, str, str], list[pd.Series]],
    fuzzy_rows: list[pd.Series],
    bridge_lookup: dict[tuple[str, str], list[pd.Series]],
) -> LedgerMatch | None:
    platform = platform_match_label(row)
    account = account_match_label(platform, row.get("account", ""))
    if platform == "抖音":
        bridge_match = _match_douyin_bridge(row, bridge_lookup)
        if bridge_match is not None:
            return bridge_match

    keys: list[tuple[str, str, str, str, str]] = []
    content_id = _clean_text(row.get("content_id", ""))
    if content_id:
        keys.append(("id", platform, account, "id", content_id))
        if platform in {"小红书", "B站"} and not account:
            keys.append(("id", platform, "", "id", content_id))
    title_key = _normalized_tagless_title_key(row.get("title", ""))
    if platform == "抖音" and title_key and account:
        keys.append(("账号+标题", platform, account, "title_no_tags", title_key))
    for source, key_platform, key_account, key_type, key_value in keys:
        matches = lookup.get((key_platform, key_account, key_type, key_value))
        if matches:
            if key_type == "title_no_tags":
                return _select_title_match(row, matches, source, f"{key_platform}:{key_account}:{key_type}:{key_value}")
            reason = _ambiguous_match_reason(matches, "同键")
            if reason:
                return LedgerMatch(
                    matches[0],
                    source,
                    f"{key_platform}:{key_account}:{key_type}:{key_value}",
                    len(matches),
                    fill_allowed=False,
                    risk_reason=reason,
                )
            return LedgerMatch(matches[0], source, f"{key_platform}:{key_account}:{key_type}:{key_value}", len(matches))
    if platform == "抖音" and title_key:
        if not account:
            matches = lookup.get((platform, "", "title_no_tags", title_key))
            if matches:
                key = f"{platform}::title_no_tags::{title_key}"
                return _select_title_match(row, matches, "唯一标题", key)
        fuzzy_match = _match_fuzzy_title(row, fuzzy_rows)
        if fuzzy_match is not None:
            return fuzzy_match
    return None


def _match_douyin_bridge(
    row: pd.Series,
    bridge_lookup: dict[tuple[str, str], list[pd.Series]],
) -> LedgerMatch | None:
    for id_type, id_value in _douyin_feedback_keys(row):
        matches = bridge_lookup.get((id_type, id_value), [])
        if not matches:
            continue
        key = f"抖音::{id_type}::{id_value}"
        reason = _ambiguous_bridge_reason(matches)
        if reason:
            return LedgerMatch(matches[0], "反馈ID桥表", key, len(matches), fill_allowed=False, risk_reason=reason)
        return LedgerMatch(matches[0], "反馈ID桥表", key, len(matches))
    return None


def _douyin_feedback_keys(row: pd.Series) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for column in ["content_id", "material_id"]:
        value = _clean_text(row.get(column, ""))
        if value and not value.startswith("row:") and (column, value) not in keys:
            keys.append((column, value))
    url_key = _douyin_url_key(row.get("content_url", ""))
    if url_key and ("content_url", url_key) not in keys:
        keys.append(("content_url", url_key))
    return keys


def _douyin_url_key(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    token_match = re.search(r"[?&]token=([^&#\s]+)", text)
    if token_match:
        return f"token:{token_match.group(1)}"
    return text


def _select_title_match(row: pd.Series, matches: list[pd.Series], source: str, key: str) -> LedgerMatch:
    if len(matches) <= 1:
        return LedgerMatch(matches[0], source, key, len(matches))
    selected = _choose_title_match_by_date(row, matches)
    reason = "同标题多链接按日期选择"
    return LedgerMatch(selected, source, key, len(matches), fill_allowed=True, risk_reason=reason)


def _choose_title_match_by_date(row: pd.Series, matches: list[pd.Series]) -> pd.Series:
    period_end = _parse_period_end(row)
    dated: list[tuple[date, pd.Series]] = []
    for candidate in matches:
        parsed = _parse_published_date(candidate.get("published_date", ""), 2026)
        if parsed is not None:
            dated.append((parsed, candidate))
    if not dated:
        return matches[0]
    if period_end is not None:
        eligible = [(parsed, candidate) for parsed, candidate in dated if parsed <= period_end]
        if eligible:
            latest = max(parsed for parsed, _ in eligible)
            return next(candidate for parsed, candidate in eligible if parsed == latest)
    earliest = min(parsed for parsed, _ in dated)
    return next(candidate for parsed, candidate in dated if parsed == earliest)


def _parse_period_end(row: pd.Series) -> date | None:
    for column in ["period_end", "batch_period_end", "data_end"]:
        parsed = _parse_published_date(row.get(column, ""), 2026)
        if parsed is not None:
            return parsed
    return None


def _match_fuzzy_title(row: pd.Series, ledger_rows: list[pd.Series]) -> LedgerMatch | None:
    title_key = _normalized_tagless_title_key(row.get("title", ""))
    if len(title_key) < 6:
        return None
    account = account_match_label("抖音", row.get("account", ""))
    matches: list[tuple[float, pd.Series]] = []
    for candidate in ledger_rows:
        candidate_account = account_match_label("抖音", candidate.get("account", ""))
        if account and candidate_account and account != candidate_account:
            continue
        candidate_key = _clean_text(candidate.get("title_key_no_tags", "")) or _normalized_tagless_title_key(
            candidate.get("title", "")
        )
        if not candidate_key or candidate_key == title_key:
            continue
        score = _fuzzy_title_score(title_key, candidate_key)
        if score >= 0.86:
            matches.append((score, candidate))
    if not matches:
        return None
    best_score = max(score for score, _ in matches)
    best_matches = [candidate for score, candidate in matches if score == best_score]
    candidate = _choose_title_match_by_date(row, best_matches)
    candidate_key = _clean_text(candidate.get("title_key_no_tags", "")) or _normalized_tagless_title_key(
        candidate.get("title", "")
    )
    reason = "标题近似匹配，需确认"
    if len(best_matches) > 1:
        reason = _append_text(reason, "同标题多链接按日期选择")
    return LedgerMatch(
        candidate,
        "模糊标题",
        f"抖音::fuzzy_title::{title_key}->{candidate_key}",
        len(best_matches),
        fill_allowed=True,
        risk_reason=reason,
    )


def _is_fuzzy_title_match(left: str, right: str) -> bool:
    return _fuzzy_title_score(left, right) >= 0.86


def _fuzzy_title_score(left: str, right: str) -> float:
    shorter, longer = sorted([left, right], key=len)
    if len(shorter) >= 6 and shorter in longer:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _dedupe_ledger_by_earliest_date(ledger: pd.DataFrame, *, default_year: int) -> pd.DataFrame:
    if ledger.empty:
        return ledger
    grouped: dict[tuple[str, str, str], list[int]] = {}
    passthrough: list[int] = []
    for index, row in ledger.iterrows():
        key = _ledger_duplicate_key(row)
        if key is None:
            passthrough.append(index)
            continue
        grouped.setdefault(key, []).append(index)

    keep: set[int] = set(passthrough)
    for indices in grouped.values():
        if len(indices) == 1:
            keep.add(indices[0])
            continue
        dated = [
            (index, parsed)
            for index in indices
            if (parsed := _parse_published_date(ledger.at[index, "published_date"], default_year)) is not None
        ]
        if not dated:
            keep.update(indices)
            continue
        earliest = min(parsed for _, parsed in dated)
        earliest_indices = [index for index, parsed in dated if parsed == earliest]
        keep.update(earliest_indices if len(earliest_indices) > 1 else [earliest_indices[0]])
    return ledger.loc[[index for index in ledger.index if index in keep]].reset_index(drop=True)


def _ledger_duplicate_key(row: pd.Series) -> tuple[str, str, str] | None:
    platform = platform_match_label(row.get("platform", ""))
    content_id = _clean_text(row.get("content_id", ""))
    content_url = _clean_text(row.get("content_url", ""))
    if platform == "抖音" and content_url:
        return platform, "url", content_url
    if content_id:
        return platform, "id", content_id
    if content_url:
        return platform, "url", content_url
    if platform == "抖音":
        title_key = _clean_text(row.get("title_key_no_tags", "")) or _normalized_tagless_title_key(row.get("title", ""))
        if title_key:
            return platform, "title_no_tags", title_key
    return None


def _parse_published_date(value: object, default_year: int) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean_text(value)
    if not text:
        return None

    year_match = re.search(r"(?<!\d)(20\d{2})[./年\-\s]*(\d{1,2})[./月\-\s]*(\d{1,2})日?(?!\d)", text)
    if year_match:
        return _date_or_none(int(year_match.group(1)), int(year_match.group(2)), int(year_match.group(3)))

    month_day_match = re.search(r"(?<!\d)(\d{1,2})[./月\-\s]+(\d{1,2})日?(?!\d)", text)
    if month_day_match:
        return _date_or_none(default_year, int(month_day_match.group(1)), int(month_day_match.group(2)))

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        return parsed.date()
    return None


def _date_or_none(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _configured_ledger_specs(config_path: Path | None) -> list[tuple[Path, Path | None, str | None]]:
    if config_path is None:
        return []
    config_path = Path(config_path)
    if not config_path.exists():
        return []
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("content_ledgers") or []
    if not isinstance(entries, list):
        return []

    specs: list[tuple[Path, Path | None, str | None]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source_type = _clean_text(entry.get("type", ""))
        if source_type not in {"local_excel", "feishu_sheet_export"}:
            continue
        for path_text in _entry_paths(entry):
            source_path = Path(path_text)
            if not source_path.is_absolute():
                source_path = config_path.parent / source_path
            if source_path.exists() and source_path.suffix.lower() in TABULAR_SUFFIXES:
                specs.append((source_path, config_path.parent, path_text))
    return specs


def _entry_paths(entry: dict[str, object]) -> list[str]:
    value = entry.get("path") or entry.get("file")
    values = entry.get("paths") or entry.get("files")
    candidates: list[object] = []
    if value:
        candidates.append(value)
    if isinstance(values, list):
        candidates.extend(values)
    return [_clean_text(item) for item in candidates if _clean_text(item)]


def _source_file_label(path: Path, root: Path | None) -> str:
    path = Path(path)
    if root is None:
        return path.name
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(os.path.relpath(path.resolve(), Path(root).resolve())).as_posix()


def _normalized_tagless_title_key(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"复制此链接.*$", "", text, flags=re.I)
    text = re.sub(r"打开Dou音搜索.*$", "", text, flags=re.I)
    text = re.sub(r"打开抖音搜索.*$", "", text)
    text = re.sub(r"[#＃]\s*[^#＃]+", "", text)
    return normalized_title_key(text)


def _ambiguous_match_reason(matches: list[pd.Series], key_label: str) -> str:
    if len(matches) <= 1:
        return ""
    accounts = {
        account_match_label(row.get("platform", ""), row.get("account", ""))
        for row in matches
        if _clean_text(row.get("account", ""))
    }
    content_types = {
        _clean_text(row.get("content_type", ""))
        for row in matches
        if _clean_text(row.get("content_type", ""))
    }
    if len(accounts) > 1 or len(content_types) > 1:
        return f"投稿台账存在 {len(matches)} 条{key_label}记录"
    return ""


def _ambiguous_bridge_reason(matches: list[pd.Series]) -> str:
    if len(matches) <= 1:
        return ""
    accounts = {
        account_match_label("抖音", row.get("account", ""))
        for row in matches
        if _clean_text(row.get("account", ""))
    }
    content_types = {
        _clean_text(row.get("content_type", ""))
        for row in matches
        if _clean_text(row.get("content_type", ""))
    }
    if len(accounts) > 1 or len(content_types) > 1:
        return f"抖音ID桥表存在 {len(matches)} 条同ID记录"
    return ""


def _record_match_metadata(frame: pd.DataFrame, index: object, match: LedgerMatch, item: pd.Series) -> None:
    frame.at[index, "ledger_match_source"] = match.source
    frame.at[index, "ledger_match_key"] = match.key
    frame.at[index, "ledger_content_type"] = item.get("content_type", "")
    frame.at[index, "ledger_content_type_review"] = item.get("content_type_review", "")
    frame.at[index, "ledger_filter_status"] = item.get("filter_status", "")
    frame.at[index, "ledger_source_file"] = item.get("source_file", "")
    frame.at[index, "ledger_source_sheet"] = item.get("source_sheet", "")
    frame.at[index, "ledger_source_row"] = _clean_text(item.get("source_row", ""))


def _mark_match_risk(frame: pd.DataFrame, index: object, reason: str) -> None:
    frame.at[index, "match_risk_level"] = "需复核"
    frame.at[index, "match_risk_reason"] = _append_text(frame.at[index, "match_risk_reason"], reason)
    frame.at[index, "review_reasons"] = _append_text(frame.at[index, "review_reasons"], reason)
    frame.at[index, "needs_manual_review"] = True


def _iter_tabular_files(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in Path(input_dir).rglob("*")
        if path.is_file()
        and path.suffix.lower() in TABULAR_SUFFIXES
        and not path.name.startswith("~$")
        and not _is_generated_channel_clean_file(path)
        and "channel_clean" not in path.parts
    )


def _is_csv(path: Path) -> bool:
    return Path(path).suffix.lower() == ".csv"


def _is_generated_channel_clean_file(path: Path) -> bool:
    return Path(path).stem.lower().endswith("_clean")


def _read_table(
    path: Path,
    sheet_name: object = 0,
    header: object = 0,
    nrows: int | None = None,
) -> pd.DataFrame:
    path = Path(path)
    if _is_csv(path):
        last_error: Exception | None = None
        for encoding in ["utf-8-sig", "utf-8", "gbk"]:
            try:
                return pd.read_csv(path, header=header, nrows=nrows, encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error:
            raise last_error
        return pd.read_csv(path, header=header, nrows=nrows)
    return pd.read_excel(path, sheet_name=sheet_name, header=header, nrows=nrows)


def _first_value(row: pd.Series, columns: Iterable[str]) -> str:
    for column in columns:
        if column not in row.index:
            continue
        value = _clean_text(row.get(column, ""))
        if value:
            return value
    return ""


def _extract_url(value: object) -> str:
    match = re.search(r"https?://[^\s\])）>]+", str(value or ""))
    return match.group(0) if match else ""


def _extract_content_id(platform: str, value: object) -> str:
    text = str(value or "")
    if platform == "小红书":
        match = re.search(r"/(?:item|explore)/([^?/#\s]+)", text)
        return match.group(1) if match else ""
    if platform == "B站":
        match = re.search(r"(BV[0-9A-Za-z]+)", text)
        return match.group(1) if match else ""
    return ""


def _tags_from_text(value: object) -> str:
    tags = []
    for match in re.finditer(r"[#＃]\s*([^#＃\s]+)", str(value or "")):
        tag = match.group(1).strip()
        if tag and f"#{tag}" not in tags:
            tags.append(f"#{tag}")
    return " ".join(tags)


def _title_from_link_text(value: object) -> str:
    title = extract_historical_title(value)
    title = re.split(r"[#＃]", title, maxsplit=1)[0]
    return " ".join(title.split()).strip()


def _platform_from_sheet(sheet_name: str) -> str:
    return _platform_name(sheet_name)


def _platform_name(value: object) -> str:
    text = str(value or "")
    if "抖音" in text:
        return "抖音"
    if "小红书" in text:
        return "小红书"
    if "B站" in text or "bilibili" in text.lower():
        return "B站"
    return ""


def _fill_blank(frame: pd.DataFrame, index: object, column: str, value: object) -> None:
    if column not in frame.columns:
        frame[column] = ""
    if not _clean_text(frame.at[index, column]):
        frame.at[index, column] = _clean_text(value)


def _fill_title(frame: pd.DataFrame, index: object, value: object) -> None:
    if "title" not in frame.columns:
        frame["title"] = ""
    current = _clean_text(frame.at[index, "title"])
    replacement = _clean_text(value)
    if replacement and (not current or _is_url_only(current)):
        frame.at[index, "title"] = replacement


def _fill_content_form_from_type(frame: pd.DataFrame, index: object, content_type: str, field_mapping) -> None:
    if "content_form" not in frame.columns:
        frame["content_form"] = ""
    form = standardize_content_form(
        pd.Series({"manual_category": content_type}),
        channel=_clean_text(frame.at[index, "channel"] if "channel" in frame.columns else ""),
        mapping=field_mapping,
    )
    if not form:
        return
    current = _clean_text(frame.at[index, "content_form"])
    if not current or form == "图文":
        frame.at[index, "content_form"] = form


def _is_url_only(value: object) -> bool:
    text = _clean_text(value)
    return bool(re.fullmatch(r"https?://\S+", text))


def _append_text(current: object, addition: str) -> str:
    values = [part for part in str(current or "").split("；") if part]
    if addition and addition not in values:
        values.append(addition)
    return "；".join(values)


def _looks_like_separator(row: pd.Series) -> bool:
    text = " ".join(_clean_text(value) for value in row.tolist())
    return "投稿视频" in text and not _extract_url(text)


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text
