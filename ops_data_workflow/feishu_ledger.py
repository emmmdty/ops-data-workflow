"""Load harvester Feishu content ledgers into the local ledger schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Mapping

import pandas as pd
import requests
from dotenv import dotenv_values

from .content_ledger import LEDGER_COLUMNS, parse_content_ledger_file


PLATFORM_SHEETS = {
    "douyin": ("抖音", "FEISHU_SHEET_DOUYIN"),
    "xhs": ("小红书", "FEISHU_SHEET_XHS"),
    "bilibili": ("B站", "FEISHU_SHEET_BILIBILI"),
}

PLATFORM_HEADERS = {
    "douyin": [
        "编号",
        "投稿时间",
        "内容链接",
        "账号",
        "内容类型",
        "是否投放成功",
        "是否为爆款",
        "供稿人",
        "备注",
        "作品ID",
        "作品类型",
        "标题",
        "tag词",
        "一级类型",
        "二级类型",
        "内容类型标签审核",
        "AI内容判断备注",
    ],
    "xhs": ["编号", "投稿时间", "内容链接", "笔记ID", "标题", "账号", "一级类型", "二级类型", "内容类型", "内容类型标签审核", "tag词"],
    "bilibili": ["编号", "投稿时间", "内容链接", "短链id", "账号", "标题", "tag词", "内容类型"],
}

READ_CHUNK_SIZE = 5000
READ_MIN_COLUMN_COUNT = 30


@dataclass(frozen=True)
class FeishuLedgerConfig:
    app_id: str
    app_secret: str
    spreadsheet_token: str
    wiki_token: str
    api_base_url: str
    sheets: Mapping[str, str]


def load_feishu_content_ledger(
    *,
    env: Mapping[str, str] | None = None,
    env_path: Path | None = None,
    session: object | None = None,
    default_year: int = 2026,
    today: date | None = None,
) -> pd.DataFrame:
    """Return harvester Feishu rows as content ledger records.

    Missing configuration intentionally returns an empty disabled ledger. Runtime
    Feishu errors are raised so callers can record a warning and fall back.
    """
    config, warnings = _load_config(env=env, env_path=env_path)
    if config is None:
        empty = _empty_ledger()
        empty.attrs["feishu_enabled"] = False
        empty.attrs["ledger_warnings"] = warnings
        staleness = build_feishu_staleness_summary(empty, today=today, default_year=default_year)
        empty.attrs["feishu_staleness"] = staleness
        empty.attrs["feishu_snapshot"] = _snapshot(empty, enabled=False, warnings=warnings, staleness=staleness)
        return empty

    client = _FeishuSheetsClient(config, session=session)
    records_by_platform: dict[str, pd.DataFrame] = {}
    source_files: set[str] = set()
    sheet_row_counts: dict[str, int] = {}
    for platform_id, (platform_name, _) in PLATFORM_SHEETS.items():
        rows = client.read_sheet_rows(platform_id, len(PLATFORM_HEADERS[platform_id]))
        sheet_row_counts[config.sheets[platform_id]] = max(0, len(rows) - 1)
        frame = _rows_to_frame(platform_id, rows)
        if frame.empty:
            continue
        records_by_platform[platform_name] = frame

    if not records_by_platform:
        empty = _empty_ledger()
        staleness = build_feishu_staleness_summary(empty, today=today, default_year=default_year)
        empty.attrs["feishu_enabled"] = True
        empty.attrs["ledger_warnings"] = warnings
        empty.attrs["feishu_staleness"] = staleness
        empty.attrs["feishu_snapshot"] = _snapshot(
            empty,
            enabled=True,
            warnings=warnings,
            sheet_row_counts=sheet_row_counts,
            staleness=staleness,
        )
        return empty

    with TemporaryDirectory() as tmp:
        workbook = Path(tmp) / "harvester_feishu.xlsx"
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            for platform_name, frame in records_by_platform.items():
                frame.to_excel(writer, sheet_name=platform_name, index=False)
        ledger = parse_content_ledger_file(workbook, root=Path(tmp), source_file="harvester_feishu")
        source_files.add(str(workbook.resolve()))

    if ledger.empty:
        result = _empty_ledger()
    else:
        result = ledger[LEDGER_COLUMNS].copy()
    result.attrs["source_files"] = source_files
    result.attrs["feishu_enabled"] = True
    result.attrs["ledger_warnings"] = warnings
    staleness = build_feishu_staleness_summary(result, today=today, default_year=default_year)
    result.attrs["feishu_staleness"] = staleness
    result.attrs["feishu_snapshot"] = _snapshot(
        result,
        enabled=True,
        warnings=warnings,
        sheet_row_counts=sheet_row_counts,
        staleness=staleness,
    )
    return result


def build_feishu_staleness_summary(
    ledger: pd.DataFrame,
    *,
    today: date | None = None,
    default_year: int = 2026,
    stale_after_days: int = 3,
) -> dict[str, object]:
    """Summarize whether each Feishu platform ledger appears stale."""
    today = today or datetime.now(timezone.utc).date()
    items: list[dict[str, object]] = []
    for platform_name, latest_date in _latest_published_dates_by_platform(ledger, default_year=default_year).items():
        days_since_latest = (today - latest_date).days if latest_date is not None else None
        needs_check = latest_date is None or (days_since_latest is not None and days_since_latest > stale_after_days)
        latest_text = latest_date.isoformat() if latest_date is not None else ""
        if latest_date is None:
            status = "missing_date"
            message = f"{platform_name}没有可识别的最新投稿时间，请检查飞书台账。"
        elif needs_check:
            status = "stale"
            message = f"{platform_name}最新投稿时间 {latest_text}，已 {days_since_latest} 天未更新，请检查飞书台账。"
        else:
            status = "fresh"
            message = f"{platform_name}最新投稿时间 {latest_text}，距今天 {days_since_latest} 天。"
        items.append(
            {
                "platform": platform_name,
                "latest_published_date": latest_text,
                "days_since_latest": days_since_latest,
                "needs_check": bool(needs_check),
                "status": status,
                "message": message,
            }
        )
    needs_check_platforms = [str(item["platform"]) for item in items if bool(item["needs_check"])]
    return {
        "checked_at": today.isoformat(),
        "stale_after_days": int(stale_after_days),
        "needs_check": bool(needs_check_platforms),
        "needs_check_platforms": needs_check_platforms,
        "items": items,
    }


def _snapshot(
    ledger: pd.DataFrame,
    *,
    enabled: bool,
    warnings: list[str],
    sheet_row_counts: dict[str, int] | None = None,
    staleness: dict[str, object] | None = None,
) -> dict[str, object]:
    total = int(len(ledger))
    platform_counts = ledger.get("platform", pd.Series(dtype=object)).value_counts(dropna=False).to_dict() if total else {}
    completeness: dict[str, float] = {}
    for column in [
        "content_id",
        "content_url",
        "title",
        "tags",
        "raw_content_type",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "content_type",
    ]:
        if column not in ledger.columns:
            completeness[column] = 0.0
            continue
        non_blank = ledger[column].fillna("").astype(str).str.strip().ne("").sum()
        completeness[column] = (int(non_blank) / total) if total else 0.0
    return {
        "enabled": bool(enabled),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": total,
        "platform_counts": {str(key): int(value) for key, value in platform_counts.items()},
        "sheet_row_counts": sheet_row_counts or {},
        "field_completeness": completeness,
        "warnings": list(warnings),
        "staleness": staleness or build_feishu_staleness_summary(ledger),
    }


def _latest_published_dates_by_platform(ledger: pd.DataFrame, *, default_year: int) -> dict[str, date | None]:
    result = {platform_name: None for platform_name, _ in PLATFORM_SHEETS.values()}
    if ledger is None or ledger.empty or "platform" not in ledger.columns or "published_date" not in ledger.columns:
        return result
    for platform_name in result:
        dates = [
            parsed
            for parsed in (
                _parse_published_date(value, default_year)
                for value in ledger.loc[ledger["platform"].astype(str).eq(platform_name), "published_date"].tolist()
            )
            if parsed is not None
        ]
        result[platform_name] = max(dates) if dates else None
    return result


def _parse_published_date(value: object, default_year: int) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
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


def _load_config(
    *,
    env: Mapping[str, str] | None,
    env_path: Path | None,
) -> tuple[FeishuLedgerConfig | None, list[str]]:
    values = _merged_env(env, env_path)
    missing = [
        key
        for key in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_SHEET_DOUYIN", "FEISHU_SHEET_XHS", "FEISHU_SHEET_BILIBILI"]
        if not _text(values.get(key))
    ]
    if not _text(values.get("FEISHU_SPREADSHEET_TOKEN")) and not _text(values.get("FEISHU_WIKI_TOKEN")):
        missing.append("FEISHU_SPREADSHEET_TOKEN 或 FEISHU_WIKI_TOKEN")
    if missing:
        return None, [f"缺少飞书配置：{', '.join(missing)}"]
    return (
        FeishuLedgerConfig(
            app_id=_text(values.get("FEISHU_APP_ID")),
            app_secret=_text(values.get("FEISHU_APP_SECRET")),
            spreadsheet_token=_text(values.get("FEISHU_SPREADSHEET_TOKEN")),
            wiki_token=_text(values.get("FEISHU_WIKI_TOKEN")),
            api_base_url=(_text(values.get("FEISHU_OPEN_BASE_URL")) or "https://open.feishu.cn").rstrip("/"),
            sheets={platform_id: _text(values.get(env_name)) for platform_id, (_, env_name) in PLATFORM_SHEETS.items()},
        ),
        [],
    )


def _merged_env(env: Mapping[str, str] | None, env_path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    candidates: list[Path] = []
    if env_path is not None:
        candidates.append(Path(env_path))
    candidates.extend([Path(".env"), Path(__file__).resolve().parents[1] / ".env"])
    for path in candidates:
        if path.exists():
            values.update({str(key): str(value or "") for key, value in dotenv_values(path).items()})
            break
    values.update({key: value for key, value in os.environ.items() if key.startswith("FEISHU_")})
    if env is not None:
        values.update({str(key): str(value or "") for key, value in env.items()})
    return values


class _FeishuSheetsClient:
    def __init__(self, config: FeishuLedgerConfig, *, session: object | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.tenant_access_token = ""
        self._spreadsheet_token = config.spreadsheet_token
        self._sheets_cache: list[dict] | None = None

    def read_sheet_rows(self, platform_id: str, column_count: int) -> list[list[object]]:
        sheet_id = self.config.sheets[platform_id]
        spreadsheet_token = self.spreadsheet_token()
        row_count = max(1, self.sheet_row_count(sheet_id))
        read_column_count = max(int(column_count), READ_MIN_COLUMN_COUNT)
        rows: list[list[object]] = []
        for row_start in range(1, row_count + 1, READ_CHUNK_SIZE):
            row_end = min(row_count, row_start + READ_CHUNK_SIZE - 1)
            column_end = _column_name(read_column_count)
            range_text = f"{sheet_id}!A{row_start}:{column_end}{row_end}"
            data = self.request_json(
                f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_text}"
            )
            rows.extend(data.get("valueRange", {}).get("values") or data.get("values") or [])
        return rows

    def spreadsheet_token(self) -> str:
        if self._spreadsheet_token:
            return self._spreadsheet_token
        data = self.request_json(f"/open-apis/wiki/v2/spaces/get_node?token={self.config.wiki_token}")
        node = data.get("node") or {}
        obj_type = _text(node.get("obj_type") or node.get("objType")).lower()
        obj_token = _text(node.get("obj_token") or node.get("objToken"))
        if obj_type and obj_type != "sheet":
            raise RuntimeError(f"Wiki 节点不是普通表格，当前类型：{obj_type}")
        if not obj_token:
            raise RuntimeError("Wiki 节点未返回普通表格 token")
        self._spreadsheet_token = obj_token
        return obj_token

    def sheet_row_count(self, sheet_id: str) -> int:
        sheets = self.list_sheets()
        for item in sheets:
            properties = item.get("properties") or item
            if properties.get("sheet_id") != sheet_id and properties.get("sheetId") != sheet_id and properties.get("id") != sheet_id:
                continue
            grid = properties.get("grid_properties") or properties.get("gridProperties") or {}
            return int(grid.get("row_count") or grid.get("rowCount") or properties.get("row_count") or properties.get("rowCount") or 200)
        return 200

    def list_sheets(self) -> list[dict]:
        if self._sheets_cache is not None:
            return self._sheets_cache
        token = self.spreadsheet_token()
        data = self.request_json(f"/open-apis/sheets/v3/spreadsheets/{token}/sheets/query")
        self._sheets_cache = data.get("sheets") or data.get("items") or []
        return self._sheets_cache

    def request_json(self, path: str, *, method: str = "GET", body: dict | None = None, auth: bool = True) -> dict:
        payload = self.request_raw(path, method=method, body=body, auth=auth)
        if payload.get("code", 0) == 0:
            return payload.get("data") or {}
        raise RuntimeError(f"飞书 API 调用失败：{payload.get('msg') or payload.get('message') or payload.get('code')}")

    def request_raw(self, path: str, *, method: str = "GET", body: dict | None = None, auth: bool = True) -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self.tenant_token()}"
        url = f"{self.config.api_base_url}{path if path.startswith('/') else f'/{path}'}"
        response = self.session.request(method, url, headers=headers, json=body, timeout=10)
        if not response.ok:
            raise RuntimeError(f"飞书 HTTP {response.status_code}：{response.text}")
        return response.json()

    def tenant_token(self) -> str:
        if self.tenant_access_token:
            return self.tenant_access_token
        payload = self.request_raw(
            "/open-apis/auth/v3/tenant_access_token/internal",
            method="POST",
            body={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            auth=False,
        )
        token = _text(payload.get("tenant_access_token") or payload.get("data", {}).get("tenant_access_token"))
        if not token:
            raise RuntimeError("飞书未返回 tenant_access_token")
        self.tenant_access_token = token
        return token


def _rows_to_frame(platform_id: str, rows: list[list[object]]) -> pd.DataFrame:
    headers = PLATFORM_HEADERS[platform_id]
    header_index = _detect_header_index(rows, headers)
    if header_index is None:
        return pd.DataFrame(columns=headers)
    source_headers = [_cell_text(value) for value in rows[header_index]]
    normalized_rows = []
    for raw in rows[header_index + 1 :]:
        values = [_cell_text(value) for value in list(raw)[: len(source_headers)]]
        values.extend([""] * (len(source_headers) - len(values)))
        by_header = {header: value for header, value in zip(source_headers, values) if header}
        row = [by_header.get(header, "") for header in headers]
        if any(_text(value) for value in row):
            normalized_rows.append(row)
    return pd.DataFrame(normalized_rows, columns=headers)


def _detect_header_index(rows: list[list[object]], headers: list[str]) -> int | None:
    expected = set(headers)
    best_index: int | None = None
    best_score = 0
    for index, row in enumerate(rows[:10]):
        values = {_cell_text(value) for value in row}
        score = len(values.intersection(expected))
        if score > best_score:
            best_index = index
            best_score = score
    return best_index if best_score >= 3 else None


def _cell_text(value: object) -> str:
    if isinstance(value, dict):
        if "link" in value:
            return _text(value.get("link"))
        if "text" in value:
            return _text(value.get("text"))
        if isinstance(value.get("values"), list):
            return " ".join(_text(item) for item in value["values"] if _text(item))
        if "value" in value:
            return _text(value.get("value"))
    if isinstance(value, list):
        return " ".join(_cell_text(item) for item in value if _cell_text(item))
    return _text(value)


def _column_name(index: int) -> str:
    value = int(index)
    result = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _empty_ledger() -> pd.DataFrame:
    empty = pd.DataFrame(columns=LEDGER_COLUMNS)
    empty.attrs["source_files"] = set()
    empty.attrs["ledger_warnings"] = []
    return empty


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()
