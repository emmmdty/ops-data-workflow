from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from ops_data_workflow.douyin_history_ledger import (
    DOUYIN_HISTORY_COLUMN_WIDTHS,
    DOUYIN_HISTORY_HEADERS,
    DOUYIN_HISTORY_SHEET_TITLE,
    copy_harvester_feishu_env,
    history_record_to_row,
    history_records_from_harvester_json,
    init_douyin_history_sheet,
    upsert_douyin_history_records,
)


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = "" if payload is None else json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


class FakeHistorySession:
    def __init__(self, *, sheets: list[dict] | None = None, values: list[list[object]] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.sheets = sheets or []
        self.values = values or []

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        body = kwargs.get("json")
        self.calls.append({"method": method, "url": url, "body": body})
        if url.endswith("/open-apis/auth/v3/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        if "/open-apis/wiki/v2/spaces/get_node" in url:
            return FakeResponse({"code": 0, "data": {"node": {"obj_type": "sheet", "obj_token": "wiki-sheet-token"}}})
        if "/open-apis/sheets/v3/spreadsheets/" in url and url.endswith("/sheets/query"):
            return FakeResponse({"code": 0, "data": {"sheets": self.sheets}})
        if url.endswith("/sheets_batch_update") and body:
            requests = body.get("requests") or []
            if requests and "addSheet" in requests[0]:
                sheet_id = "histSheet"
                title = requests[0]["addSheet"]["properties"]["title"]
                self.sheets.append(
                    {
                        "properties": {
                            "sheet_id": sheet_id,
                            "title": title,
                            "grid_properties": {"row_count": 200, "column_count": 20},
                        }
                    }
                )
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "replies": [
                                {
                                    "addSheet": {
                                        "properties": {
                                            "sheetId": sheet_id,
                                            "title": title,
                                        }
                                    }
                                }
                            ]
                        },
                    }
                )
            return FakeResponse({"code": 0, "data": {}})
        if "/values/" in url:
            return FakeResponse({"code": 0, "data": {"valueRange": {"values": self.values}}})
        if url.endswith("/values"):
            return FakeResponse({"code": 0, "data": {"updatedRange": body.get("valueRange", {}).get("range", "")}})
        if url.endswith("/values_append"):
            return FakeResponse({"code": 0, "data": {"updates": {"updatedRows": len(body.get("valueRange", {}).get("values", []))}}})
        if url.endswith("/style") or url.endswith("/dimension_range"):
            return FakeResponse({"code": 0, "data": {}})
        return FakeResponse({"code": 999, "msg": f"unexpected url {url}"})


class DouyinHistoryLedgerTests(unittest.TestCase):
    def test_copy_harvester_feishu_env_copies_only_missing_feishu_keys(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "harvester.env"
            target = root / ".env"
            source.write_text(
                "\n".join(
                    [
                        "FEISHU_APP_ID=harvester-app",
                        "FEISHU_APP_SECRET=harvester-secret",
                        "FEISHU_WIKI_TOKEN=wiki-token",
                        "FEISHU_SPREADSHEET_TOKEN=",
                        "FEISHU_SHEET_DOUYIN=dySheet",
                        "FEISHU_OPEN_BASE_URL=https://open.feishu.cn",
                        "UNRELATED=value",
                    ]
                ),
                encoding="utf-8",
            )
            target.write_text(
                "DEEPSEEK_API_KEY=keep\nFEISHU_APP_ID=ops-app\nFEISHU_SHEET_DOUYIN_HISTORY=histSheet\n",
                encoding="utf-8",
            )

            result = copy_harvester_feishu_env(source, target)

            values = target.read_text(encoding="utf-8")
            self.assertIn("DEEPSEEK_API_KEY=keep", values)
            self.assertIn("FEISHU_APP_ID=ops-app", values)
            self.assertIn("FEISHU_APP_SECRET=harvester-secret", values)
            self.assertIn("FEISHU_WIKI_TOKEN=wiki-token", values)
            self.assertIn("FEISHU_SHEET_DOUYIN=dySheet", values)
            self.assertIn("FEISHU_SHEET_DOUYIN_HISTORY=histSheet", values)
            self.assertNotIn("UNRELATED=value", values)
            self.assertIn("FEISHU_APP_SECRET", result.copied)
            self.assertIn("FEISHU_APP_ID", result.kept)

    def test_history_record_to_row_keeps_work_link_as_plain_full_url_string(self):
        record = {
            "account_name": "同花顺投资",
            "account_homepage": "https://www.douyin.com/user/user-id",
            "published_at": "2026-06-08",
            "url": "https://www.douyin.com/video/7645231187513871670?previous_page=app_code_link",
            "title": "市场波动怎么看",
            "tags": ["#股票", "#投资"],
            "content_type": "投教",
            "content_type_review": "需复核",
            "source": "harvester-THS",
        }

        row = history_record_to_row(record, collected_at="2026-06-08 12:00:00")

        self.assertEqual(row[DOUYIN_HISTORY_HEADERS.index("作品链接")], "https://www.douyin.com/video/7645231187513871670")
        self.assertIsInstance(row[DOUYIN_HISTORY_HEADERS.index("作品链接")], str)
        self.assertEqual(row[DOUYIN_HISTORY_HEADERS.index("作品ID")], "7645231187513871670")
        self.assertEqual(row[DOUYIN_HISTORY_HEADERS.index("作品类型")], "video")
        self.assertEqual(row[DOUYIN_HISTORY_HEADERS.index("tag词")], "#股票 #投资")

    def test_history_records_from_harvester_json_maps_accounts_and_ids(self):
        payload = {
            "items": [
                {
                    "accountName": "同花顺投资",
                    "publishedAt": "2026-06-08",
                    "itemUrl": "https://www.douyin.com/note/7645231187513871670",
                    "title": "图文内容",
                    "tags": "#投教",
                    "contentType": "投教",
                    "contentTypeReview": "通过",
                }
            ]
        }
        accounts = [{"name": "同花顺投资", "url": "https://www.douyin.com/user/user-id"}]

        records = history_records_from_harvester_json(payload, accounts=accounts, source="harvester-test")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["account_homepage"], "https://www.douyin.com/user/user-id")
        self.assertEqual(records[0]["work_id"], "7645231187513871670")
        self.assertEqual(records[0]["work_type"], "note")
        self.assertEqual(records[0]["source"], "harvester-test")

    def test_init_douyin_history_sheet_creates_sheet_and_applies_header_layout(self):
        session = FakeHistorySession(sheets=[])
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_WIKI_TOKEN": "wiki-token",
        }

        result = init_douyin_history_sheet(env=env, session=session)

        self.assertTrue(result.created)
        self.assertEqual(result.sheet_id, "histSheet")
        add_calls = [call for call in session.calls if call["url"].endswith("/sheets_batch_update")]
        self.assertTrue(any((call["body"].get("requests") or [{}])[0].get("addSheet", {}).get("properties", {}).get("title") == DOUYIN_HISTORY_SHEET_TITLE for call in add_calls))
        self.assertTrue(any((call["body"].get("requests") or [{}])[0].get("updateSheet", {}).get("properties", {}).get("frozenRowCount") == 1 for call in add_calls))
        value_calls = [call for call in session.calls if call["url"].endswith("/values")]
        self.assertEqual(value_calls[0]["body"]["valueRange"]["values"], [DOUYIN_HISTORY_HEADERS])
        width_calls = [call for call in session.calls if call["url"].endswith("/dimension_range")]
        link_width = DOUYIN_HISTORY_COLUMN_WIDTHS["作品链接"]
        self.assertTrue(any(call["body"]["dimensionProperties"].get("fixedSize") == link_width for call in width_calls))
        style_calls = [call for call in session.calls if call["url"].endswith("/style")]
        self.assertTrue(any("font" in call["body"]["appendStyle"]["style"] for call in style_calls))
        header_styles = [call["body"]["appendStyle"]["style"] for call in style_calls if "font" in call["body"]["appendStyle"]["style"]]
        self.assertTrue(all("hAlign" not in style for style in header_styles))
        self.assertTrue(any(call["body"]["appendStyle"]["style"].get("textWrap") is True for call in style_calls))
        self.assertFalse(any("5000" in call["body"]["appendStyle"]["range"] for call in style_calls))

    def test_upsert_douyin_history_records_updates_existing_blanks_and_appends_new_rows(self):
        existing = [
            DOUYIN_HISTORY_HEADERS,
            [
                "同花顺投资",
                "https://www.douyin.com/user/user-id",
                "2026-06-01",
                "video",
                "7645231187513871670",
                "https://www.douyin.com/video/7645231187513871670",
                "",
                "",
                "",
                "",
                "待补充",
                "2026-06-01 00:00:00",
                "",
                "old",
            ],
        ]
        session = FakeHistorySession(
            sheets=[
                {
                    "properties": {
                        "sheet_id": "histSheet",
                        "title": DOUYIN_HISTORY_SHEET_TITLE,
                        "grid_properties": {"row_count": 2, "column_count": 20},
                    }
                }
            ],
            values=existing,
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_SPREADSHEET_TOKEN": "spreadsheet-token",
            "FEISHU_SHEET_DOUYIN_HISTORY": "histSheet",
        }
        records = [
            {
                "account_name": "同花顺投资",
                "account_homepage": "https://www.douyin.com/user/user-id",
                "published_at": "2026-06-01",
                "url": "https://www.douyin.com/video/7645231187513871670",
                "title": "补齐标题",
                "tags": "#投教",
                "content_type": "投教",
                "content_type_review": "通过",
                "status": "已采集",
                "source": "harvester-THS",
            },
            {
                "account_name": "同花顺投资",
                "account_homepage": "https://www.douyin.com/user/user-id",
                "published_at": "2026-06-02",
                "url": "https://www.douyin.com/video/7645231187513871671",
                "title": "新增标题",
                "tags": "#股票",
                "content_type": "资讯",
                "content_type_review": "需复核",
                "status": "已采集",
                "source": "harvester-THS",
            },
        ]

        result = upsert_douyin_history_records(records, env=env, session=session, collected_at="2026-06-08 12:00:00")

        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 1)
        update_calls = [
            call
            for call in session.calls
            if call["url"].endswith("/values") and call["body"]["valueRange"]["range"].endswith("A2:N2")
        ]
        self.assertEqual(len(update_calls), 1)
        updated_row = update_calls[0]["body"]["valueRange"]["values"][0]
        self.assertEqual(updated_row[DOUYIN_HISTORY_HEADERS.index("标题")], "补齐标题")
        self.assertEqual(updated_row[DOUYIN_HISTORY_HEADERS.index("采集状态")], "已采集")
        self.assertIsInstance(updated_row[DOUYIN_HISTORY_HEADERS.index("作品链接")], str)
        append_calls = [call for call in session.calls if call["url"].endswith("/values_append")]
        self.assertEqual(len(append_calls), 1)
        appended_row = append_calls[0]["body"]["valueRange"]["values"][0]
        self.assertEqual(appended_row[DOUYIN_HISTORY_HEADERS.index("作品链接")], "https://www.douyin.com/video/7645231187513871671")
        self.assertIsInstance(appended_row[DOUYIN_HISTORY_HEADERS.index("作品链接")], str)

    def test_upsert_douyin_history_records_dedupes_auto_link_cells_by_url_text(self):
        existing = [
            DOUYIN_HISTORY_HEADERS,
            [
                "同花顺投资",
                "https://www.douyin.com/user/user-id",
                "2026-06-01",
                "video",
                "",
                [
                    {
                        "type": "url",
                        "text": "https://www.douyin.com/video/7645231187513871670",
                        "link": "https://www.douyin.com/video/7645231187513871670",
                    }
                ],
                "",
                "",
                "",
                "",
                "待补充",
                "2026-06-01 00:00:00",
                "",
                "old",
            ],
        ]
        session = FakeHistorySession(
            sheets=[
                {
                    "properties": {
                        "sheet_id": "histSheet",
                        "title": DOUYIN_HISTORY_SHEET_TITLE,
                        "grid_properties": {"row_count": 2, "column_count": 20},
                    }
                }
            ],
            values=existing,
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_SPREADSHEET_TOKEN": "spreadsheet-token",
            "FEISHU_SHEET_DOUYIN_HISTORY": "histSheet",
        }

        result = upsert_douyin_history_records(
            [
                {
                    "account_name": "同花顺投资",
                    "account_homepage": "https://www.douyin.com/user/user-id",
                    "published_at": "2026-06-01",
                    "url": "https://www.douyin.com/video/7645231187513871670",
                    "title": "补齐标题",
                    "tags": "#投教",
                    "status": "已采集",
                }
            ],
            env=env,
            session=session,
            collected_at="2026-06-08 12:00:00",
        )

        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertFalse(any(call["url"].endswith("/values_append") for call in session.calls))
        update_calls = [call for call in session.calls if call["url"].endswith("/values")]
        updated_row = update_calls[-1]["body"]["valueRange"]["values"][0]
        self.assertEqual(updated_row[DOUYIN_HISTORY_HEADERS.index("作品链接")], "https://www.douyin.com/video/7645231187513871670")
        self.assertIsInstance(updated_row[DOUYIN_HISTORY_HEADERS.index("作品链接")], str)


if __name__ == "__main__":
    unittest.main()
