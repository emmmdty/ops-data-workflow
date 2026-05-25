from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.historical_content import (
    build_historical_category_mappings,
    parse_historical_content_workbook,
)
from ops_data_workflow.storage import init_db, upsert_category_mappings
from ops_data_workflow.title_matching import normalized_title_key
from ops_data_workflow.workflow import run_archived_workflow


class HistoricalContentTests(unittest.TestCase):
    def test_title_key_ignores_tags_punctuation_and_whitespace(self):
        self.assertEqual(
            normalized_title_key("你会是那百分之几的股民？#财经 #股市 #同花顺投资 #投资理"),
            normalized_title_key(" 你会是那百分之几的股民? "),
        )

    def test_parse_historical_workbook_detects_real_headers(self):
        with TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "原生内容投稿.xlsx"
            self._write_history_workbook(workbook)

            rows = parse_historical_content_workbook(workbook)

            self.assertIn("note-ok", set(rows["content_id"]))
            self.assertIn("股友说", set(rows["content_type"]))
            douyin = rows[rows["platform_group"].eq("抖音")].iloc[0]
            self.assertEqual(douyin["content_type"], "股友说")
            self.assertIn("你会是那百分之几的股民", douyin["title"])

    def test_build_mappings_skips_conflicting_ids_and_title_keys(self):
        with TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "原生内容投稿.xlsx"
            self._write_history_workbook(workbook)

            result = build_historical_category_mappings(workbook)

            keys = set(result.mappings["mapping_key"])
            self.assertIn("content_id:note-ok", keys)
            self.assertNotIn("content_id:note-conflict", keys)
            title_key = normalized_title_key("你会是那百分之几的股民？")
            self.assertIn(f"title_key:{title_key}", keys)
            conflict_keys = set(result.conflicts["mapping_key"])
            self.assertIn("content_id:note-conflict", conflict_keys)
            self.assertIn(f"title_key:{normalized_title_key('冲突标题')}", conflict_keys)

    def test_workflow_reuses_title_key_mapping_and_overrides_ai_category(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "你会是那百分之几的股民？#财经 #股市 #同花顺投资",
                        "视频id": "dy-title-key",
                        "素材ID": "mat-title-key",
                        "消耗": 100.0,
                        "展示数": 1000,
                        "点击数": 100,
                        "激活数": 10,
                        "付费次数": 2,
                        "内容类型": "旧分类",
                    }
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")
            init_db(db_path)
            upsert_category_mappings(
                db_path,
                pd.DataFrame(
                    [
                        {
                            "platform": "抖音",
                            "platform_group": "抖音",
                            "channel": "抖音商业化",
                            "title_key": normalized_title_key("你会是那百分之几的股民？"),
                            "category_l2": "股友说",
                            "category_l3": "你会是那百分之几的股民？",
                        }
                    ]
                ),
            )

            result = run_archived_workflow(
                raw_dir,
                "2026-05-15",
                "2026-05-21",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            row = result.canonical.iloc[0]
            self.assertEqual(row["category_l2"], "股友说")
            self.assertEqual(row["category_l3"], "你会是那百分之几的股民？")
            self.assertEqual(row["category_source"], "历史审核映射")
            self.assertEqual(row["review_status"], "已确认")

    @staticmethod
    def _write_history_workbook(path: Path) -> None:
        xhs_rows = [
            ["2026目标", None, None, None, None, None],
            ["编号", "投稿时间", "内容链接", "笔记ID", "账号", "内容类型"],
            [1, "05 21", "【有效标题 - 同顺股民社区 | 小红书】 https://xhs.example/item/note-ok", "note-ok", "投资号", "股友说"],
            [2, "05 21", "https://xhs.example/item/note-conflict", "note-conflict", "投资号", "资讯"],
            [3, "05 21", "https://xhs.example/item/note-conflict", "note-conflict", "投资号", "图文"],
        ]
        douyin_rows = [
            ["投稿规则", None, None, None, None, None],
            ["编号", "投稿时间", "内容链接", "账号", "内容类型", "备注"],
            [
                1,
                "05 21",
                "3.20 A@b.c :9pm 你会是那百分之几的股民？#财经 #股市 #同花顺投资 https://v.douyin.com/abc/ 复制此链接",
                "投资号",
                "股友说",
                "",
            ],
            [2, "05 21", "冲突标题 #财经 https://v.douyin.com/one/", "投资号", "资讯", ""],
            [3, "05 21", "冲突标题 #股市 https://v.douyin.com/two/", "投资号", "图文", ""],
        ]
        bilibili_rows = [
            ["编号", "投稿时间", "内容链接", "短链id", "是否投放成功", "是否为爆款"],
            [1, "05 21", "https://www.bilibili.com/video/BV1abc/", "BV1abc", "", ""],
        ]
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(douyin_rows).to_excel(writer, sheet_name="抖音渠道", header=False, index=False)
            pd.DataFrame(xhs_rows).to_excel(writer, sheet_name="小红书渠道", header=False, index=False)
            pd.DataFrame(bilibili_rows).to_excel(writer, sheet_name="B站渠道", header=False, index=False)


if __name__ == "__main__":
    unittest.main()
