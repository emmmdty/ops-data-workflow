"""Parse harvester Feishu exports into owned content ledger rows."""

from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
import re
from typing import Iterable

import pandas as pd
import yaml

from .categories import category_from_tags
from .generated_artifacts import is_generated_tabular_artifact
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
    "raw_content_type",
    "category_l1",
    "category_l2",
    "bilibili_content_type",
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
    """Load only the newest human-maintained content ledger from an explicit directory."""
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
    raw_content_type = _first_value(row, ["内容类型", "内容分类", "栏目"])
    category_l1 = _first_value(row, ["一级类型", "一级内容类型", "一级分类"])
    category_l2 = _first_value(row, ["二级类型", "二级内容类型", "二级分类"])
    bilibili_content_type = raw_content_type if platform == "B站" else ""
    content_type = _platform_content_type(
        platform,
        raw_content_type=raw_content_type,
        category_l1=category_l1,
        category_l2=category_l2,
        bilibili_content_type=bilibili_content_type,
        tags=tags,
        link_text=link_text,
    )
    content_id = _extract_content_id(platform, content_url or link_text) if platform == "抖音" else ""
    if not content_id:
        content_id = _first_value(row, ["作品ID", "笔记ID", "短链id", "视频/笔记id", "内容ID"])
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
        "raw_content_type": raw_content_type,
        "category_l1": category_l1,
        "category_l2": category_l2,
        "bilibili_content_type": bilibili_content_type,
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


def _platform_content_type(
    platform: str,
    *,
    raw_content_type: str,
    category_l1: str,
    category_l2: str,
    bilibili_content_type: str,
    tags: str,
    link_text: object,
) -> str:
    if platform in {"抖音", "小红书"}:
        return category_l2 or category_l1 or raw_content_type or category_from_tags(f"{tags} {link_text}")
    if platform == "B站":
        return bilibili_content_type or raw_content_type or category_from_tags(f"{tags} {link_text}")
    return raw_content_type or category_from_tags(f"{tags} {link_text}")


def _ledger_duplicate_key(row: pd.Series) -> tuple[str, str, str] | None:
    platform = _platform_name(row.get("platform", ""))
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
            if source_path.is_dir():
                for child in _iter_tabular_files(source_path):
                    specs.append((child, config_path.parent, path_text))
            elif source_path.exists() and source_path.suffix.lower() in TABULAR_SUFFIXES:
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


def _iter_tabular_files(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in Path(input_dir).rglob("*")
        if path.is_file()
        and path.suffix.lower() in TABULAR_SUFFIXES
        and not path.name.startswith("~$")
        and not is_generated_tabular_artifact(path, input_dir)
    )


def _is_csv(path: Path) -> bool:
    return Path(path).suffix.lower() == ".csv"


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
    if platform == "抖音":
        match = re.search(r"/(?:video|note)/(\d{10,24})", text)
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


def _looks_like_separator(row: pd.Series) -> bool:
    text = " ".join(_clean_text(value) for value in row.tolist())
    if _extract_url(text):
        return False
    return bool(re.search(r"(?:20\d{2}年投稿|\d{3,4}\s*投稿(?:视频|图文)?|投稿视频|投稿图文)", text))


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text
