from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.raw_sync import discover_raw_periods, sync_raw_periods


def _write_xiaohongshu_file(path: Path, note_id: str, spend: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "时间": "2026-05-08~2026-05-14",
                    "标题": f"小红书内容 {note_id}",
                    "笔记ID": note_id,
                    "发布作者": "同花顺理财",
                    "类型": "图文",
                    "内容分类": "热点行情",
                    "消费": spend,
                    "展现量": 6000,
                    "点击量": 300,
                    "激活数": 12,
                    "首次付费次数": 2,
                }
            ]
        ).to_excel(writer, sheet_name="02户", index=False)


class RawSyncTests(unittest.TestCase):
    def test_discover_raw_periods_scans_months_and_weeks_only(self):
        with TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            _write_xiaohongshu_file(data_root / "weeks" / "20260508-20260514" / "小红书.xlsx", "note-new", 10.0)
            _write_xiaohongshu_file(data_root / "months" / "202605" / "小红书.xlsx", "note-month", 20.0)
            _write_xiaohongshu_file(data_root / "weeks" / "202605w2" / "旧周目录.xlsx", "skip-week", 30.0)
            _write_xiaohongshu_file(data_root / "raw" / "20260508-20260514" / "旧路径.xlsx", "skip", 30.0)
            _write_xiaohongshu_file(data_root / "reference" / "原生内容投稿-20260527.xlsx", "reference", 40.0)
            (data_root / "not-a-period").mkdir()

            periods = discover_raw_periods(data_root)

            self.assertEqual([period.name for period in periods], ["202605", "20260508-20260514"])
            self.assertEqual([period.period_level for period in periods], ["month", "week"])
            self.assertEqual([period.period_key for period in periods], ["2026-05", "20260508-20260514"])

    def test_sync_raw_periods_generates_once_and_regenerates_after_file_change(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_root = tmp_path / "data"
            _write_xiaohongshu_file(data_root / "weeks" / "20260501-20260507" / "小红书账号投放数据.xlsx", "note-old", 20.0)
            _write_xiaohongshu_file(data_root / "weeks" / "20260508-20260514" / "小红书账号投放数据.xlsx", "note-new", 10.0)

            first = sync_raw_periods(
                data_root,
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                processed_root=tmp_path / "processed",
                output_root=tmp_path / "outputs",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )
            second = sync_raw_periods(
                data_root,
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                processed_root=tmp_path / "processed",
                output_root=tmp_path / "outputs",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )
            _write_xiaohongshu_file(data_root / "weeks" / "20260508-20260514" / "小红书账号投放数据.xlsx", "note-newer", 99.0)
            third = sync_raw_periods(
                data_root,
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                processed_root=tmp_path / "processed",
                output_root=tmp_path / "outputs",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual([item.status for item in first], ["generated", "generated"])
            self.assertEqual([item.status for item in second], ["skipped", "skipped"])
            self.assertEqual([item.status for item in third], ["skipped", "generated"])
            self.assertTrue(first[0].batch_id)
            self.assertTrue(third[1].batch_id)
            self.assertEqual(first[1].batch_id, third[1].batch_id)
            self.assertFalse((data_root / "weeks" / "20260508-20260514" / "cleaned.xlsx").exists())
            self.assertTrue((tmp_path / "processed" / "20260508-20260514" / third[1].batch_id / "cleaned.xlsx").exists())
            with closing(sqlite3.connect(tmp_path / ".runtime" / "workflow.sqlite3")) as conn:
                periods = conn.execute(
                    """
                    select period_start, period_end, period_level, source_type, count(*)
                    from upload_batches
                    group by period_start, period_end, period_level, source_type
                    order by period_start
                    """
                ).fetchall()
                latest_new_count = conn.execute(
                    """
                    select count(*)
                    from canonical_items
                    where batch_id = ?
                    """,
                    (third[1].batch_id,),
                ).fetchone()[0]
            self.assertEqual(
                periods,
                [
                    ("2026-05-01", "2026-05-07", "week", "upload", 1),
                    ("2026-05-08", "2026-05-14", "week", "upload", 1),
                ],
            )
            self.assertEqual(latest_new_count, 1)

    def test_sync_raw_periods_ignores_generated_clean_artifacts_in_signature(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_root = tmp_path / "data"
            period_dir = data_root / "weeks" / "20260508-20260514"
            _write_xiaohongshu_file(period_dir / "小红书账号投放数据.xlsx", "note-new", 10.0)

            first = sync_raw_periods(
                data_root,
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                processed_root=tmp_path / "processed",
                output_root=tmp_path / "outputs",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )
            second = sync_raw_periods(
                data_root,
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                processed_root=tmp_path / "processed",
                output_root=tmp_path / "outputs",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual([item.status for item in first], ["generated"])
            self.assertEqual([item.status for item in second], ["skipped"])


if __name__ == "__main__":
    unittest.main()
