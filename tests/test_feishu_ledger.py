from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from ops_data_workflow.feishu_ledger import load_feishu_content_ledger
from ops_data_workflow.periods import period_metadata_from_dates
from ops_data_workflow.raw_cleaning import clean_raw_period_dir, load_cleaning_ledger


def _write_xlsx(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = "" if payload is None else __import__("json").dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, value_rows_by_sheet: dict[str, list[list[object]]]) -> None:
        self.calls: list[tuple[str, str]] = []
        self.value_rows_by_sheet = value_rows_by_sheet

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append((method, url))
        if url.endswith("/open-apis/auth/v3/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        if "/open-apis/wiki/v2/spaces/get_node" in url:
            return FakeResponse({"code": 0, "data": {"node": {"obj_type": "sheet", "obj_token": "wiki-sheet-token"}}})
        if "/open-apis/sheets/v3/spreadsheets/" in url and url.endswith("/sheets/query"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "sheets": [
                            {"properties": {"sheet_id": "dySheet", "grid_properties": {"row_count": 2}}},
                            {"properties": {"sheet_id": "xhsSheet", "grid_properties": {"row_count": 3}}},
                            {"properties": {"sheet_id": "biliSheet", "grid_properties": {"row_count": 2}}},
                        ]
                    },
                }
            )
        if "/values/" in url:
            for sheet_id, rows in self.value_rows_by_sheet.items():
                if f"/values/{sheet_id}!" in url:
                    return FakeResponse({"code": 0, "data": {"valueRange": {"values": self._slice_rows(url, rows)}}})
        return FakeResponse({"code": 999, "msg": f"unexpected url {url}"})

    def _slice_rows(self, url: str, rows: list[list[object]]) -> list[list[object]]:
        match = re.search(r"!A\d+:([A-Z]+)\d+", url)
        if not match:
            return rows
        column_count = 0
        for char in match.group(1):
            column_count = column_count * 26 + ord(char) - 64
        return [row[:column_count] for row in rows]


class FeishuLedgerTests(unittest.TestCase):
    def test_missing_feishu_config_returns_empty_disabled_ledger(self):
        with TemporaryDirectory() as tmp:
            empty_env = Path(tmp) / ".env"
            empty_env.write_text("", encoding="utf-8")
            ledger = load_feishu_content_ledger(
                env={
                    "FEISHU_APP_ID": "",
                    "FEISHU_APP_SECRET": "",
                    "FEISHU_WIKI_TOKEN": "",
                    "FEISHU_SPREADSHEET_TOKEN": "",
                    "FEISHU_SHEET_DOUYIN": "",
                    "FEISHU_SHEET_XHS": "",
                    "FEISHU_SHEET_BILIBILI": "",
                },
                env_path=empty_env,
            )

        self.assertTrue(ledger.empty)
        self.assertFalse(ledger.attrs["feishu_enabled"])
        self.assertIn("缺少飞书配置", "；".join(ledger.attrs["ledger_warnings"]))

    def test_load_feishu_content_ledger_reads_wiki_sheet_rows_and_normalizes_cells(self):
        session = FakeSession(
            {
                "dySheet": [["编号", "投稿时间", "内容链接", "标题", "tag词", "账号", "内容类型"]],
                "xhsSheet": [
                    ["2026目标  5个爆款/月"],
                    ["编号", "投稿时间", "内容链接", "笔记ID", "标题", "账号", "内容类型", "内容类型标签审核", "tag词"],
                    [
                        "1",
                        "05 19",
                        {
                            "type": "url",
                            "text": "打开链接",
                            "link": "https://www.xiaohongshu.com/explore/note-1?xsec_token=secret",
                        },
                        "note-1",
                        "小红书飞书标题",
                        {"type": "multipleValue", "values": ["投资号"]},
                        {"type": "multipleValue", "values": ["图文"]},
                        "通过",
                        "#投教",
                    ],
                ],
                "biliSheet": [["编号", "投稿时间", "内容链接", "短链id", "账号", "标题", "tag词"]],
            }
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_WIKI_TOKEN": "wiki-token",
            "FEISHU_SHEET_DOUYIN": "dySheet",
            "FEISHU_SHEET_XHS": "xhsSheet",
            "FEISHU_SHEET_BILIBILI": "biliSheet",
            "FEISHU_OPEN_BASE_URL": "https://open.feishu.cn",
        }

        ledger = load_feishu_content_ledger(env=env, session=session)

        self.assertEqual(len(ledger), 1)
        row = ledger.iloc[0]
        self.assertEqual(row["platform"], "小红书")
        self.assertEqual(row["content_id"], "note-1")
        self.assertEqual(row["title"], "小红书飞书标题")
        self.assertEqual(row["account"], "投资号")
        self.assertEqual(row["content_type"], "图文")
        self.assertEqual(row["content_url"], "https://www.xiaohongshu.com/explore/note-1?xsec_token=secret")
        self.assertEqual(row["source_file"], "harvester_feishu")
        self.assertEqual(row["source_sheet"], "小红书")
        self.assertTrue(any("/open-apis/wiki/v2/spaces/get_node" in url for _, url in session.calls))

    def test_load_feishu_content_ledger_uses_spreadsheet_token_without_wiki_lookup(self):
        session = FakeSession(
            {
                "dySheet": [
                    ["编号", "投稿时间", "内容链接", "标题", "tag词", "筛选状态", "简短理由", "账号", "内容类型", "内容类型标签审核", "本地素材目录"],
                    ["1", "05 19", "https://v.douyin.com/abc/", "抖音标题", "#投教", "", "", "投资号", "资讯", "通过", ""],
                ],
                "xhsSheet": [["编号", "投稿时间", "内容链接", "笔记ID", "标题", "账号", "内容类型", "内容类型标签审核", "tag词"]],
                "biliSheet": [["编号", "投稿时间", "内容链接", "短链id", "账号", "标题", "tag词"]],
            }
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_SPREADSHEET_TOKEN": "spreadsheet-token",
            "FEISHU_WIKI_TOKEN": "wiki-token",
            "FEISHU_SHEET_DOUYIN": "dySheet",
            "FEISHU_SHEET_XHS": "xhsSheet",
            "FEISHU_SHEET_BILIBILI": "biliSheet",
        }

        ledger = load_feishu_content_ledger(env=env, session=session)

        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger.iloc[0]["platform"], "抖音")
        self.assertEqual(ledger.iloc[0]["title"], "抖音标题")
        self.assertTrue(any("/open-apis/sheets/v3/spreadsheets/spreadsheet-token/sheets/query" in url for _, url in session.calls))
        self.assertFalse(any("/open-apis/wiki/v2/spaces/get_node" in url for _, url in session.calls))

    def test_load_feishu_content_ledger_reads_current_douyin_title_column(self):
        session = FakeSession(
            {
                "dySheet": [
                    ["2026目标", "", ""],
                    ["投稿规则", "1、明显不符合广告平台规则"],
                    ["", "2、投稿账号连着2周过审率低于30%"],
                    [
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
                    ["", "2026年投稿"],
                    ["", "0611 投稿视频"],
                    [
                        "1",
                        "2026-06-11",
                        {"type": "url", "text": "打开链接", "link": "https://www.douyin.com/video/7594830477777751338"},
                        "投资号",
                        "资讯",
                        "",
                        "",
                        "",
                        "",
                        "7594830477777751338",
                        "视频",
                        "当妈妈问你钱都在哪儿？我be like..",
                        "#同花顺 #财经",
                        "资讯",
                        "",
                        "通过",
                        "",
                    ],
                ],
                "xhsSheet": [["编号", "投稿时间", "内容链接", "笔记ID", "标题", "账号", "内容类型", "内容类型标签审核", "tag词"]],
                "biliSheet": [["编号", "投稿时间", "内容链接", "短链id", "账号", "标题", "tag词"]],
            }
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_SPREADSHEET_TOKEN": "spreadsheet-token",
            "FEISHU_SHEET_DOUYIN": "dySheet",
            "FEISHU_SHEET_XHS": "xhsSheet",
            "FEISHU_SHEET_BILIBILI": "biliSheet",
        }

        ledger = load_feishu_content_ledger(env=env, session=session)

        self.assertEqual(len(ledger), 1)
        row = ledger.iloc[0]
        self.assertEqual(row["platform"], "抖音")
        self.assertEqual(row["title"], "当妈妈问你钱都在哪儿？我be like..")
        self.assertEqual(row["account"], "投资号")
        self.assertEqual(row["content_type"], "资讯")
        self.assertEqual(row["content_id"], "7594830477777751338")
        self.assertEqual(row["category_l1"], "资讯")
        self.assertEqual(row["category_l2"], "")
        self.assertEqual(row["raw_content_type"], "资讯")

        snapshot = ledger.attrs["feishu_snapshot"]
        self.assertTrue(snapshot["enabled"])
        self.assertEqual(snapshot["total_rows"], 1)
        self.assertEqual(snapshot["platform_counts"]["抖音"], 1)
        self.assertIn("dySheet", snapshot["sheet_row_counts"])
        self.assertIn("title", snapshot["field_completeness"])
        self.assertEqual(snapshot["field_completeness"]["content_id"], 1.0)

    def test_feishu_ledger_preserves_platform_specific_type_fields(self):
        session = FakeSession(
            {
                "dySheet": [
                    [
                        "编号",
                        "投稿时间",
                        "内容链接",
                        "账号",
                        "内容类型",
                        "作品ID",
                        "标题",
                        "tag词",
                        "一级类型",
                        "二级类型",
                    ],
                    [
                        "1",
                        "2026-06-11",
                        "https://www.douyin.com/video/7594830477777751338",
                        "投资号",
                        "旧内容类型",
                        "7594830477777751338",
                        "抖音标题",
                        "#财经",
                        "投教",
                        "方法论",
                    ],
                ],
                "xhsSheet": [
                    ["编号", "投稿时间", "内容链接", "笔记ID", "标题", "账号", "一级类型", "二级类型", "内容类型", "tag词"],
                    [
                        "1",
                        "2026-06-12",
                        "https://www.xiaohongshu.com/explore/note-1",
                        "note-1",
                        "小红书标题",
                        "投资号",
                        "投教",
                        "图文教程",
                        "旧图文",
                        "#投教",
                    ],
                ],
                "biliSheet": [
                    ["编号", "投稿时间", "内容链接", "短链id", "账号", "标题", "tag词", "内容类型"],
                    [
                        "1",
                        "2026-06-13",
                        "https://www.bilibili.com/video/BV1abcde2345/",
                        "BV1abcde2345",
                        "投资号",
                        "B站标题",
                        "投教",
                        "长视频测评",
                    ],
                ],
            }
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_SPREADSHEET_TOKEN": "spreadsheet-token",
            "FEISHU_SHEET_DOUYIN": "dySheet",
            "FEISHU_SHEET_XHS": "xhsSheet",
            "FEISHU_SHEET_BILIBILI": "biliSheet",
        }

        ledger = load_feishu_content_ledger(env=env, session=session)

        by_platform = {row["platform"]: row for _, row in ledger.iterrows()}
        self.assertEqual(by_platform["抖音"]["category_l1"], "投教")
        self.assertEqual(by_platform["抖音"]["category_l2"], "方法论")
        self.assertEqual(by_platform["抖音"]["content_type"], "方法论")
        self.assertEqual(by_platform["小红书"]["category_l1"], "投教")
        self.assertEqual(by_platform["小红书"]["category_l2"], "图文教程")
        self.assertEqual(by_platform["小红书"]["content_type"], "图文教程")
        self.assertEqual(by_platform["B站"]["bilibili_content_type"], "长视频测评")
        self.assertEqual(by_platform["B站"]["content_type"], "长视频测评")

    def test_load_feishu_content_ledger_reads_wide_current_channel_tables_and_skips_separator_rows(self):
        session = FakeSession(
            {
                "dySheet": [
                    ["2026目标", "", ""],
                    [
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
                    ["", "2026年投稿"],
                    ["", "0612 投稿视频"],
                    [
                        "1",
                        "2026-06-12",
                        "https://www.douyin.com/video/7650436177517989139",
                        "投资号",
                        "股友说",
                        "",
                        "",
                        "",
                        "",
                        "7650436177517989139",
                        "视频",
                        "修炼交易的心酸",
                        "#同花顺股友说",
                        "股友说",
                        "股民教学",
                        "通过",
                        "使用minimax",
                    ],
                ],
                "xhsSheet": [
                    ["2026目标", "", ""],
                    [
                        "编号",
                        "投稿时间",
                        "内容链接",
                        "笔记ID",
                        "账号",
                        "内容类型",
                        "是否投放成功",
                        "是否为爆款",
                        "供稿人",
                        "备注",
                        "标题",
                        "tag词",
                        "一级类型",
                        "二级类型",
                        "内容类型标签审核",
                        "AI内容判断备注",
                    ],
                    ["", "2026年投稿"],
                    ["", "0612 投稿视频"],
                    [
                        "1",
                        "2026-06-12",
                        "https://www.xiaohongshu.com/discovery/item/6a2bce78000000002202af22",
                        "6a2bce78000000002202af22",
                        "投资号",
                        "股友说",
                        "",
                        "",
                        "",
                        "",
                        "修炼交易的心酸",
                        "#同花顺股友说",
                        "视频",
                        "股友说",
                        "通过",
                        "使用minimax",
                    ],
                ],
                "biliSheet": [
                    ["2026目标", "", ""],
                    [
                        "编号",
                        "投稿时间",
                        "内容链接",
                        "短链id",
                        "是否投放成功",
                        "是否为爆款",
                        "供稿人",
                        "备注",
                        "账号",
                        "作品类型",
                        "标题",
                        "tag词",
                        "内容类型",
                        "内容类型标签审核",
                        "AI内容判断备注",
                    ],
                    ["", "2026年投稿"],
                    ["", "0612 投稿视频"],
                    [
                        "1",
                        "2026-06-12",
                        "https://www.bilibili.com/video/BV1sdEv64EB3/",
                        "BV1sdEv64EB3",
                        "",
                        "",
                        "",
                        "",
                        "投资号",
                        "视频",
                        "你有没有轻松赚到别人一个月工资的兴奋？",
                        "#财经 #股票",
                        "短视频",
                        "通过",
                        "使用minimax",
                    ],
                ],
            }
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_SPREADSHEET_TOKEN": "spreadsheet-token",
            "FEISHU_SHEET_DOUYIN": "dySheet",
            "FEISHU_SHEET_XHS": "xhsSheet",
            "FEISHU_SHEET_BILIBILI": "biliSheet",
        }

        ledger = load_feishu_content_ledger(env=env, session=session)

        self.assertEqual(len(ledger), 3)
        self.assertNotIn("2026年投稿", set(ledger["published_date"]))
        by_platform = {row["platform"]: row for _, row in ledger.iterrows()}
        self.assertEqual(by_platform["抖音"]["category_l1"], "股友说")
        self.assertEqual(by_platform["抖音"]["category_l2"], "股民教学")
        self.assertEqual(by_platform["小红书"]["category_l1"], "视频")
        self.assertEqual(by_platform["小红书"]["category_l2"], "股友说")
        self.assertEqual(by_platform["B站"]["title"], "你有没有轻松赚到别人一个月工资的兴奋？")
        self.assertEqual(by_platform["B站"]["bilibili_content_type"], "短视频")

    def test_load_feishu_content_ledger_maps_legacy_xhs_rows_by_header_name(self):
        session = FakeSession(
            {
                "dySheet": [["编号", "投稿时间", "内容链接", "标题", "tag词", "筛选状态", "简短理由", "账号", "内容类型", "内容类型标签审核", "本地素材目录"]],
                "xhsSheet": [
                    ["编号", "投稿时间", "内容链接", "笔记ID", "账号", "内容类型", "内容类型标签审核", "tag词"],
                    [
                        "1",
                        "05 19",
                        "https://www.xiaohongshu.com/explore/note-legacy",
                        "note-legacy",
                        {"type": "multipleValue", "values": ["投资号"]},
                        {"type": "multipleValue", "values": ["图文"]},
                        "通过",
                        "#投教",
                    ],
                ],
                "biliSheet": [["编号", "投稿时间", "内容链接", "短链id", "账号", "标题", "tag词"]],
            }
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_SPREADSHEET_TOKEN": "spreadsheet-token",
            "FEISHU_SHEET_DOUYIN": "dySheet",
            "FEISHU_SHEET_XHS": "xhsSheet",
            "FEISHU_SHEET_BILIBILI": "biliSheet",
        }

        ledger = load_feishu_content_ledger(env=env, session=session)

        row = ledger.iloc[0]
        self.assertEqual(row["content_id"], "note-legacy")
        self.assertEqual(row["title"], "")
        self.assertEqual(row["account"], "投资号")
        self.assertEqual(row["content_type"], "图文")
        self.assertEqual(row["content_type_review"], "通过")
        self.assertEqual(row["tags"], "#投教")

    def test_cleaning_ledger_does_not_fallback_to_local_reference_when_feishu_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference"
            _write_xlsx(
                reference / "原生内容投稿.xlsx",
                {
                    "小红书渠道": pd.DataFrame(
                        [
                            {
                                "编号": 1,
                                "投稿时间": "05 19",
                                "内容链接": "https://www.xiaohongshu.com/explore/note-local",
                                "笔记ID": "note-local",
                                "标题": "本地台账标题",
                                "账号": "投资号",
                                "内容类型": "图文",
                            }
                        ]
                    )
                },
            )

            with patch("ops_data_workflow.raw_cleaning.load_feishu_content_ledger", side_effect=RuntimeError("boom")):
                ledger = load_cleaning_ledger(root / "raw", default_year=2026, reference_root=reference)

            self.assertEqual(len(ledger), 0)
            self.assertIn("飞书台账读取失败", "；".join(ledger.attrs["ledger_warnings"]))

    def test_clean_raw_period_dir_records_feishu_fallback_warning_in_outputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "0527-0602 数据"
            _write_xlsx(
                raw_dir / "抖音市场部数据.xlsx",
                {
                    "Sheet1": pd.DataFrame(
                        [
                            {
                                "视频标题": "有效投放内容",
                                "视频id": "dy-1",
                                "素材ID": "mat-1",
                                "消耗": 10.0,
                                "展示数": 100,
                                "激活数": 2,
                                "付费次数": 1,
                            }
                        ]
                    )
                },
            )

            with patch("ops_data_workflow.raw_cleaning.load_feishu_content_ledger", side_effect=RuntimeError("boom")):
                bucket = clean_raw_period_dir(
                    raw_dir,
                    period_metadata_from_dates("2026-05-27", "2026-06-02"),
                    output_dir=root / "clean",
                    default_year=2026,
                )

            import_log = pd.read_excel(bucket.cleaned_workbook, sheet_name="导入日志")
            warning_rows = import_log[import_log["status"].astype(str).eq("warning")]
            self.assertIn("飞书台账读取失败：boom", "；".join(warning_rows["message"].astype(str)))
            manifest = pd.read_json(bucket.manifest_path, typ="series").to_dict()
            self.assertIn("飞书台账读取失败：boom", manifest["ledger_warnings"])


if __name__ == "__main__":
    unittest.main()
