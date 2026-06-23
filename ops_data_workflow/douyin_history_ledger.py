"""Douyin historical work ledger collection and Feishu sheet writing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping, Sequence

from dotenv import dotenv_values
import requests

from .env_bridge import resolve_harvester_root


DOUYIN_HISTORY_SHEET_TITLE = "抖音历史台账"

DOUYIN_HISTORY_HEADERS = [
    "账号名称",
    "账号主页",
    "发布时间",
    "作品类型",
    "作品ID",
    "作品链接",
    "标题",
    "tag词",
    "内容类型",
    "内容类型标签审核",
    "采集状态",
    "采集时间",
    "失败原因",
    "来源",
]

DOUYIN_HISTORY_COLUMN_WIDTHS = {
    "账号名称": 120,
    "账号主页": 220,
    "发布时间": 110,
    "作品类型": 80,
    "作品ID": 150,
    "作品链接": 560,
    "标题": 420,
    "tag词": 360,
    "内容类型": 120,
    "内容类型标签审核": 120,
    "采集状态": 100,
    "采集时间": 160,
    "失败原因": 260,
    "来源": 100,
}

FEISHU_COPY_KEYS = [
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_WIKI_TOKEN",
    "FEISHU_SPREADSHEET_TOKEN",
    "FEISHU_OPEN_BASE_URL",
    "FEISHU_SHEET_DOUYIN",
    "FEISHU_SHEET_XHS",
    "FEISHU_SHEET_BILIBILI",
    "FEISHU_SHEET_STEP15_FILTERED",
]

READ_CHUNK_SIZE = 5000
DEFAULT_WRAP_ROW_LIMIT = 5000
@dataclass(frozen=True)
class EnvCopyResult:
    copied: list[str]
    kept: list[str]
    skipped_empty: list[str]
    target_path: Path


@dataclass(frozen=True)
class DouyinHistoryConfig:
    app_id: str
    app_secret: str
    spreadsheet_token: str
    wiki_token: str
    api_base_url: str
    history_sheet_id: str


@dataclass(frozen=True)
class SheetInitResult:
    spreadsheet_token: str
    sheet_id: str
    title: str
    created: bool


@dataclass(frozen=True)
class UpsertResult:
    spreadsheet_token: str
    sheet_id: str
    created: int
    updated: int
    skipped: int


@dataclass(frozen=True)
class HarvesterCrawlResult:
    command: list[str]
    json_path: Path
    records_path: Path
    record_count: int


def copy_harvester_feishu_env(source_env: Path, target_env: Path) -> EnvCopyResult:
    """Copy non-empty FEISHU_* values from harvester .env without overwriting local values."""
    source_env = Path(source_env)
    target_env = Path(target_env)
    source_values = {str(key): str(value or "") for key, value in dotenv_values(source_env).items()}
    target_values = {str(key): str(value or "") for key, value in dotenv_values(target_env).items()} if target_env.exists() else {}
    copied: list[str] = []
    kept: list[str] = []
    skipped_empty: list[str] = []

    lines = target_env.read_text(encoding="utf-8").splitlines() if target_env.exists() else []
    existing_line_by_key = _env_line_indexes(lines)

    for key in FEISHU_COPY_KEYS:
        value = _text(source_values.get(key))
        if not value:
            skipped_empty.append(key)
            continue
        if _text(target_values.get(key)):
            kept.append(key)
            continue
        line = f"{key}={_format_env_value(value)}"
        if key in existing_line_by_key:
            lines[existing_line_by_key[key]] = line
        else:
            lines.append(line)
        target_values[key] = value
        copied.append(key)

    target_env.parent.mkdir(parents=True, exist_ok=True)
    target_env.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return EnvCopyResult(copied=copied, kept=kept, skipped_empty=skipped_empty, target_path=target_env)


def init_douyin_history_sheet(
    *,
    env: Mapping[str, str] | None = None,
    env_path: Path | None = None,
    session: object | None = None,
    update_env_path: Path | None = None,
) -> SheetInitResult:
    """Create or initialize the Douyin history Feishu sheet and apply layout."""
    config = load_douyin_history_config(env=env, env_path=env_path)
    client = DouyinHistorySheetsClient(config, session=session)
    result = _init_douyin_history_sheet_with_client(client)
    if update_env_path is not None:
        write_history_sheet_id_to_env(update_env_path, result.sheet_id)
    return result


def upsert_douyin_history_records(
    records: Iterable[Mapping[str, object]],
    *,
    env: Mapping[str, str] | None = None,
    env_path: Path | None = None,
    session: object | None = None,
    batch_size: int = 100,
    collected_at: str | None = None,
    update_env_path: Path | None = None,
) -> UpsertResult:
    """Upsert Douyin history records into Feishu without duplicate work rows."""
    config = load_douyin_history_config(env=env, env_path=env_path)
    client = DouyinHistorySheetsClient(config, session=session)
    init_result = _init_douyin_history_sheet_with_client(client)
    if update_env_path is not None:
        write_history_sheet_id_to_env(update_env_path, init_result.sheet_id)

    sheet_id = init_result.sheet_id
    existing_values = client.read_rows(sheet_id)
    header_index = _detect_history_header_index(existing_values)
    data_rows = existing_values[header_index + 1 :] if header_index is not None else existing_values
    data_start_row_number = (header_index + 2) if header_index is not None else 1

    existing_by_key: dict[str, tuple[int, list[object]]] = {}
    for offset, raw_row in enumerate(data_rows):
        row = _plain_row(raw_row)
        if not any(_text(value) for value in row):
            continue
        for key in _dedupe_keys_from_row(row):
            if key not in existing_by_key:
                existing_by_key[key] = (data_start_row_number + offset, row)

    appended_keys: set[str] = set()
    rows_to_append: list[list[object]] = []
    created = 0
    updated = 0
    skipped = 0
    timestamp = collected_at or _current_timestamp()

    for record in records:
        new_row = history_record_to_row(record, collected_at=timestamp)
        keys = _dedupe_keys_from_row(new_row)
        if not keys:
            skipped += 1
            continue
        matched_key = next((key for key in keys if key in existing_by_key), "")
        if matched_key:
            row_number, existing_row = existing_by_key[matched_key]
            merged_row = _merge_history_row(existing_row, new_row)
            if merged_row != existing_row:
                client.write_rows(sheet_id, _row_range(sheet_id, row_number), [merged_row])
                for key in _dedupe_keys_from_row(merged_row):
                    existing_by_key[key] = (row_number, merged_row)
                updated += 1
            else:
                skipped += 1
            continue
        if any(key in appended_keys for key in keys):
            skipped += 1
            continue
        rows_to_append.append(new_row)
        appended_keys.update(keys)
        created += 1

    for chunk in _chunks(rows_to_append, max(1, int(batch_size))):
        client.append_rows(sheet_id, chunk)

    return UpsertResult(
        spreadsheet_token=client.spreadsheet_token(),
        sheet_id=sheet_id,
        created=created,
        updated=updated,
        skipped=skipped,
    )


def history_records_from_harvester_json(
    payload: Mapping[str, object] | Path | str,
    *,
    accounts: Sequence[Mapping[str, object]] | Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    source: str = "harvester-THS",
) -> list[dict[str, object]]:
    """Convert harvester Douyin JSON output into the history-ledger record shape."""
    if isinstance(payload, (str, Path)):
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    account_homepages = _account_homepages(accounts)
    items = payload.get("items") if isinstance(payload, Mapping) else []
    if not isinstance(items, list):
        return []

    records: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        account_name = _record_value(item, "account_name", "accountName", "账号名称")
        url = normalize_douyin_work_url(_record_value(item, "url", "itemUrl", "link", "作品链接"))
        work = extract_douyin_work(url)
        if not url and not _record_value(item, "failed", "failureReason", "失败原因"):
            continue
        records.append(
            {
                "account_name": account_name,
                "account_homepage": account_homepages.get(account_name, ""),
                "published_at": _record_value(item, "published_at", "publishedAt", "发布时间"),
                "work_type": work["type"] if work else _record_value(item, "work_type", "作品类型"),
                "work_id": work["id"] if work else _record_value(item, "work_id", "id", "作品ID"),
                "url": url,
                "title": _record_value(item, "title", "标题"),
                "tags": item.get("tags", item.get("tag词", "")),
                "content_type": _record_value(item, "content_type", "contentType", "内容类型"),
                "content_type_review": _record_value(item, "content_type_review", "contentTypeReview", "内容类型标签审核"),
                "status": _record_value(item, "status", "采集状态") or ("失败" if item.get("failed") else "已采集"),
                "failure_reason": _record_value(item, "failure_reason", "failureReason", "失败原因"),
                "source": source,
            }
        )
    return records


def history_record_to_row(record: Mapping[str, object], *, collected_at: str | None = None) -> list[object]:
    """Map one history record to Feishu values, keeping 作品链接 as a plain URL string."""
    url = normalize_douyin_work_url(_record_value(record, "url", "itemUrl", "link", "作品链接"))
    work = extract_douyin_work(url)
    work_id = _record_value(record, "work_id", "id", "作品ID") or (work["id"] if work else "")
    work_type = _record_value(record, "work_type", "作品类型") or (work["type"] if work else "")
    if not url and work_id:
        url = normalize_douyin_work_url(work_id, default_type=work_type or "video")
        work = extract_douyin_work(url)
        work_type = work_type or (work["type"] if work else "")

    published_at = _record_value(record, "published_at", "publishedAt", "发布时间")
    if not published_at and work_id:
        published_at = published_date_from_douyin_item_id(work_id)

    return [
        _record_value(record, "account_name", "accountName", "账号名称"),
        _record_value(record, "account_homepage", "accountHomepage", "账号主页"),
        published_at,
        work_type,
        work_id,
        url,
        _record_value(record, "title", "标题"),
        _tags_text(record.get("tags", record.get("tag词", ""))),
        _record_value(record, "content_type", "contentType", "内容类型"),
        _record_value(record, "content_type_review", "contentTypeReview", "内容类型标签审核"),
        _record_value(record, "status", "采集状态") or "已采集",
        collected_at or _record_value(record, "collected_at", "采集时间") or _current_timestamp(),
        _record_value(record, "failure_reason", "failureReason", "失败原因"),
        _record_value(record, "source", "来源") or "harvester-THS",
    ]


def load_douyin_history_config(
    *,
    env: Mapping[str, str] | None = None,
    env_path: Path | None = None,
) -> DouyinHistoryConfig:
    values = _merged_env(env=env, env_path=env_path)
    missing = [key for key in ["FEISHU_APP_ID", "FEISHU_APP_SECRET"] if not _text(values.get(key))]
    if not _text(values.get("FEISHU_SPREADSHEET_TOKEN")) and not _text(values.get("FEISHU_WIKI_TOKEN")):
        missing.append("FEISHU_SPREADSHEET_TOKEN 或 FEISHU_WIKI_TOKEN")
    if missing:
        raise RuntimeError(f"缺少飞书配置：{', '.join(missing)}")
    return DouyinHistoryConfig(
        app_id=_text(values.get("FEISHU_APP_ID")),
        app_secret=_text(values.get("FEISHU_APP_SECRET")),
        spreadsheet_token=_text(values.get("FEISHU_SPREADSHEET_TOKEN")),
        wiki_token=_text(values.get("FEISHU_WIKI_TOKEN")),
        api_base_url=(_text(values.get("FEISHU_OPEN_BASE_URL")) or "https://open.feishu.cn").rstrip("/"),
        history_sheet_id=_text(values.get("FEISHU_SHEET_DOUYIN_HISTORY")),
    )


def write_history_sheet_id_to_env(env_path: Path, sheet_id: str) -> None:
    env_path = Path(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    indexes = _env_line_indexes(lines)
    line = f"FEISHU_SHEET_DOUYIN_HISTORY={sheet_id}"
    if "FEISHU_SHEET_DOUYIN_HISTORY" in indexes:
        lines[indexes["FEISHU_SHEET_DOUYIN_HISTORY"]] = line
    else:
        lines.append(line)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def load_harvester_douyin_accounts(harvester_root: Path | None = None) -> list[dict[str, object]]:
    root = Path(harvester_root).expanduser() if harvester_root is not None else resolve_harvester_root()
    accounts_path = root / "platform-accounts.json"
    payload = json.loads(accounts_path.read_text(encoding="utf-8"))
    accounts = payload.get("douyin") if isinstance(payload, Mapping) else []
    return [dict(item) for item in accounts if isinstance(item, Mapping)]


def run_harvester_douyin_history_crawl(
    *,
    harvester_root: Path | None = None,
    since: str = "2000-01-01",
    until: str | None = None,
    max_scrolls: int = 500,
    max_detail_pages: int = 5000,
    max_items: int | None = None,
    skip_feishu: bool = False,
    env_path: Path | None = None,
    records_output_dir: Path = Path(".runtime/douyin-history"),
) -> HarvesterCrawlResult:
    """Run harvester-THS Douyin crawler with full-history limits, then optionally write Feishu."""
    harvester_root = Path(harvester_root).expanduser() if harvester_root is not None else resolve_harvester_root()
    until = until or _current_date()
    effective_detail_pages = max_items if max_items is not None else max_detail_pages
    env = os.environ.copy()
    env.update(
        {
            "MAX_SCROLLS_PER_ACCOUNT": str(max_scrolls),
            "MAX_DETAIL_PAGES": str(effective_detail_pages),
            "OLD_ITEM_STOP_AFTER": "999999",
            "MIN_CHECK_BEFORE_STOP": "999999",
        }
    )
    command = ["npm", "run", "crawl:douyin", "--", "--since", since, "--until", until, "--mode", "conservative"]
    subprocess.run(command, cwd=harvester_root, env=env, check=True)

    json_path = harvester_root / "output" / f"douyin_notes_{since}_to_{until}.json"
    accounts = load_harvester_douyin_accounts(harvester_root)
    records = history_records_from_harvester_json(json_path, accounts=accounts, source="harvester-THS")
    if max_items is not None:
        records = records[: max(0, max_items)]

    records_output_dir = Path(records_output_dir)
    records_output_dir.mkdir(parents=True, exist_ok=True)
    records_path = records_output_dir / f"douyin_history_{since}_to_{until}.json"
    records_path.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")

    if not skip_feishu:
        upsert_douyin_history_records(records, env_path=env_path)

    return HarvesterCrawlResult(
        command=command,
        json_path=json_path,
        records_path=records_path,
        record_count=len(records),
    )


class DouyinHistorySheetsClient:
    def __init__(self, config: DouyinHistoryConfig, *, session: object | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.tenant_access_token = ""
        self._spreadsheet_token = config.spreadsheet_token
        self._sheets_cache: list[dict] | None = None

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

    def list_sheets(self) -> list[dict]:
        if self._sheets_cache is not None:
            return self._sheets_cache
        token = self.spreadsheet_token()
        data = self.request_json(f"/open-apis/sheets/v3/spreadsheets/{token}/sheets/query")
        self._sheets_cache = data.get("sheets") or data.get("items") or []
        return self._sheets_cache

    def ensure_history_sheet(self) -> tuple[str, bool]:
        existing = self.find_history_sheet()
        if existing:
            return _sheet_id(existing), False

        data = self.operate_sheets(
            [
                {
                    "addSheet": {
                        "properties": {
                            "title": DOUYIN_HISTORY_SHEET_TITLE,
                        }
                    }
                }
            ]
        )
        self._sheets_cache = None
        sheet_id = _sheet_id_from_add_sheet_response(data) or _sheet_id(self.find_history_sheet() or {})
        if not sheet_id:
            raise RuntimeError("飞书创建抖音历史台账 Sheet 后未返回 sheet_id")
        return sheet_id, True

    def find_history_sheet(self) -> dict | None:
        sheet_id = self.config.history_sheet_id
        for sheet in self.list_sheets():
            if sheet_id and _sheet_id(sheet) == sheet_id:
                return sheet
            if _sheet_title(sheet) == DOUYIN_HISTORY_SHEET_TITLE:
                return sheet
        return None

    def read_rows(self, sheet_id: str) -> list[list[object]]:
        token = self.spreadsheet_token()
        row_count = max(1, self.sheet_row_count(sheet_id))
        rows: list[list[object]] = []
        column_end = _column_name(len(DOUYIN_HISTORY_HEADERS))
        for row_start in range(1, row_count + 1, READ_CHUNK_SIZE):
            row_end = min(row_count, row_start + READ_CHUNK_SIZE - 1)
            range_text = f"{sheet_id}!A{row_start}:{column_end}{row_end}"
            data = self.request_json(f"/open-apis/sheets/v2/spreadsheets/{token}/values/{range_text}")
            rows.extend(data.get("valueRange", {}).get("values") or data.get("values") or [])
        return rows

    def sheet_row_count(self, sheet_id: str) -> int:
        for item in self.list_sheets():
            if _sheet_id(item) != sheet_id:
                continue
            properties = item.get("properties") or item
            grid = properties.get("grid_properties") or properties.get("gridProperties") or {}
            return int(grid.get("row_count") or grid.get("rowCount") or properties.get("row_count") or properties.get("rowCount") or 200)
        return 200

    def write_rows(self, sheet_id: str, range_text: str, rows: list[list[object]]) -> dict:
        token = self.spreadsheet_token()
        return self.request_json(
            f"/open-apis/sheets/v2/spreadsheets/{token}/values",
            method="PUT",
            body={"valueRange": {"range": range_text, "values": rows}},
        )

    def append_rows(self, sheet_id: str, rows: list[list[object]]) -> dict | None:
        if not rows:
            return None
        current_rows = self.sheet_row_count(sheet_id)
        existing_values = self.read_rows(sheet_id)
        required_rows = max(current_rows, len(existing_values) + len(rows) + 1)
        self.ensure_sheet_rows(sheet_id, required_rows)
        token = self.spreadsheet_token()
        column_end = _column_name(len(DOUYIN_HISTORY_HEADERS))
        return self.request_json(
            f"/open-apis/sheets/v2/spreadsheets/{token}/values_append",
            method="POST",
            body={"valueRange": {"range": f"{sheet_id}!A1:{column_end}{len(rows)}", "values": rows}},
        )

    def ensure_sheet_rows(self, sheet_id: str, required_row_count: int) -> dict | None:
        required = int(required_row_count)
        current = self.sheet_row_count(sheet_id)
        missing = required - current
        if missing <= 0:
            return None
        token = self.spreadsheet_token()
        return self.request_json(
            f"/open-apis/sheets/v2/spreadsheets/{token}/dimension_range",
            method="POST",
            body={
                "dimension": {
                    "sheetId": sheet_id,
                    "majorDimension": "ROWS",
                    "length": missing,
                }
            },
        )

    def set_range_style(self, range_text: str, style: dict) -> dict:
        token = self.spreadsheet_token()
        return self.request_json(
            f"/open-apis/sheets/v2/spreadsheets/{token}/style",
            method="PUT",
            body={"appendStyle": {"range": range_text, "style": style}},
        )

    def update_column_width(self, sheet_id: str, column_number: int, width: int) -> dict:
        token = self.spreadsheet_token()
        return self.request_json(
            f"/open-apis/sheets/v2/spreadsheets/{token}/dimension_range",
            method="PUT",
            body={
                "dimension": {
                    "sheetId": sheet_id,
                    "majorDimension": "COLUMNS",
                    "startIndex": column_number,
                    "endIndex": column_number,
                },
                "dimensionProperties": {"fixedSize": int(width)},
            },
        )

    def operate_sheets(self, requests_body: list[dict]) -> dict:
        token = self.spreadsheet_token()
        return self.request_json(
            f"/open-apis/sheets/v2/spreadsheets/{token}/sheets_batch_update",
            method="POST",
            body={"requests": requests_body},
        )

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


def _init_douyin_history_sheet_with_client(client: DouyinHistorySheetsClient) -> SheetInitResult:
    sheet_id, created = client.ensure_history_sheet()
    column_end = _column_name(len(DOUYIN_HISTORY_HEADERS))
    client.write_rows(sheet_id, f"{sheet_id}!A1:{column_end}1", [DOUYIN_HISTORY_HEADERS])
    client.operate_sheets(
        [
            {
                "updateSheet": {
                    "properties": {
                        "sheetId": sheet_id,
                        "frozenRowCount": 1,
                    }
                }
            }
        ]
    )
    for index, header in enumerate(DOUYIN_HISTORY_HEADERS, start=1):
        client.update_column_width(sheet_id, index, DOUYIN_HISTORY_COLUMN_WIDTHS[header])
    client.set_range_style(
        f"{sheet_id}!A1:{column_end}1",
        {
            "font": {"bold": True},
            "backColor": "#F5F7FA",
        },
    )
    wrap_row_end = max(1, client.sheet_row_count(sheet_id))
    for header in ["作品链接", "标题", "tag词"]:
        column = _column_name(DOUYIN_HISTORY_HEADERS.index(header) + 1)
        client.set_range_style(
            f"{sheet_id}!{column}1:{column}{wrap_row_end}",
            {
                "textWrap": True,
            },
        )
    return SheetInitResult(
        spreadsheet_token=client.spreadsheet_token(),
        sheet_id=sheet_id,
        title=DOUYIN_HISTORY_SHEET_TITLE,
        created=created,
    )


def normalize_douyin_work_url(value: object, *, default_type: str = "video") -> str:
    text = _text(value)
    if not text:
        return ""
    work = extract_douyin_work(text)
    if work:
        return f"https://www.douyin.com/{work['type']}/{work['id']}"
    if re.fullmatch(r"\d{8,}", text):
        work_type = default_type if default_type in {"video", "note"} else "video"
        return f"https://www.douyin.com/{work_type}/{text}"
    return text


def extract_douyin_work(value: object) -> dict[str, str] | None:
    text = _text(value)
    match = re.search(r"(?:douyin\.com)?/(video|note)/([A-Za-z0-9_-]{8,})", text)
    if not match:
        match = re.search(r"\b(video|note)/([A-Za-z0-9_-]{8,})", text)
    if not match:
        return None
    return {"type": match.group(1), "id": match.group(2)}


def published_date_from_douyin_item_id(item_id: object) -> str:
    text = _text(item_id)
    if not re.fullmatch(r"\d{8,}", text):
        return ""
    seconds = int(text) >> 32
    if seconds < 1_000_000_000 or seconds > 2_200_000_000:
        return ""
    tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(seconds, tz=tz).strftime("%Y-%m-%d")


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


def _merge_history_row(existing_row: list[object], new_row: list[object]) -> list[object]:
    merged = _pad_row(existing_row)
    incoming = _pad_row(new_row)
    for index, header in enumerate(DOUYIN_HISTORY_HEADERS):
        current = _text(merged[index])
        value = _text(incoming[index])
        if not value:
            continue
        if header == "采集状态":
            merged[index] = value
        elif header == "失败原因" and (value or not current):
            merged[index] = value
        elif not current:
            merged[index] = value
    return merged


def _dedupe_key_from_row(row: Sequence[object]) -> str:
    keys = _dedupe_keys_from_row(row)
    return keys[0] if keys else ""


def _dedupe_keys_from_row(row: Sequence[object]) -> list[str]:
    padded = _pad_row(row)
    keys: list[str] = []
    work_id = _text(padded[DOUYIN_HISTORY_HEADERS.index("作品ID")])
    if work_id:
        keys.append(f"id:{work_id}")
    url = normalize_douyin_work_url(padded[DOUYIN_HISTORY_HEADERS.index("作品链接")])
    if url:
        keys.append(f"url:{url}")
    return keys


def _detect_history_header_index(rows: Sequence[Sequence[object]]) -> int | None:
    expected = set(DOUYIN_HISTORY_HEADERS)
    best_index: int | None = None
    best_score = 0
    for index, row in enumerate(rows[:10]):
        values = {_text(value) for value in row}
        score = len(values.intersection(expected))
        if score > best_score:
            best_index = index
            best_score = score
    return best_index if best_score >= 4 else None


def _row_range(sheet_id: str, row_number: int) -> str:
    column_end = _column_name(len(DOUYIN_HISTORY_HEADERS))
    return f"{sheet_id}!A{row_number}:{column_end}{row_number}"


def _pad_row(row: Sequence[object]) -> list[object]:
    values = list(row[: len(DOUYIN_HISTORY_HEADERS)])
    values.extend([""] * (len(DOUYIN_HISTORY_HEADERS) - len(values)))
    return values


def _plain_row(row: Sequence[object]) -> list[object]:
    return [_text(value) for value in _pad_row(row)]


def _chunks(rows: list[list[object]], size: int) -> Iterable[list[list[object]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _account_homepages(
    accounts: Sequence[Mapping[str, object]] | Mapping[str, Sequence[Mapping[str, object]]] | None,
) -> dict[str, str]:
    if accounts is None:
        return {}
    if isinstance(accounts, Mapping):
        accounts = accounts.get("douyin") or []
    return {
        _text(item.get("name")): _text(item.get("url"))
        for item in accounts
        if isinstance(item, Mapping) and _text(item.get("name"))
    }


def _record_value(record: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if _text(value):
            return _text(value)
    return ""


def _tags_text(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(item) for item in value if _text(item))
    return _text(value)


def _sheet_id(sheet: Mapping[str, object]) -> str:
    properties = sheet.get("properties") if isinstance(sheet, Mapping) else {}
    properties = properties if isinstance(properties, Mapping) else sheet
    return _text(properties.get("sheet_id") or properties.get("sheetId") or properties.get("id"))


def _sheet_title(sheet: Mapping[str, object]) -> str:
    properties = sheet.get("properties") if isinstance(sheet, Mapping) else {}
    properties = properties if isinstance(properties, Mapping) else sheet
    return _text(properties.get("title"))


def _sheet_id_from_add_sheet_response(data: Mapping[str, object]) -> str:
    replies = data.get("replies") if isinstance(data, Mapping) else None
    if not isinstance(replies, list):
        return ""
    for reply in replies:
        if not isinstance(reply, Mapping):
            continue
        add_sheet = reply.get("addSheet") or reply.get("add_sheet")
        if not isinstance(add_sheet, Mapping):
            continue
        properties = add_sheet.get("properties") if isinstance(add_sheet.get("properties"), Mapping) else add_sheet
        return _text(properties.get("sheet_id") or properties.get("sheetId") or properties.get("id"))
    return ""


def _column_name(index: int) -> str:
    value = int(index)
    result = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _env_line_indexes(lines: Sequence[str]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            indexes[match.group(1)] = index
    return indexes


def _format_env_value(value: str) -> str:
    if re.search(r"\s", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _current_timestamp() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _current_date() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        if "link" in value:
            return _text(value.get("link"))
        if "text" in value:
            return _text(value.get("text"))
        if isinstance(value.get("values"), list):
            return " ".join(_text(item) for item in value["values"] if _text(item))
        if "value" in value:
            return _text(value.get("value"))
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if _text(item))
    return str(value).strip()
