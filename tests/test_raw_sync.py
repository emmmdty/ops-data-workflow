from contextlib import closing
from pathlib import Path
import sqlite3
import json
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.raw_sync import discover_raw_periods, sync_raw_periods
from ops_data_workflow.storage import init_db


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
    def test_discover_raw_periods_ignores_uploaded_originals_and_invalid_dirs(self):
        with TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "data" / "raw"
            _write_xiaohongshu_file(raw_root / "20260508-20260514" / "小红书.xlsx", "note-new", 10.0)
            _write_xiaohongshu_file(raw_root / "20260501-20260507" / "小红书.xlsx", "note-old", 20.0)
            _write_xiaohongshu_file(raw_root / "uploaded_originals" / "20260401-20260407" / "小红书.xlsx", "skip", 30.0)
            (raw_root / "not-a-period").mkdir()

            periods = discover_raw_periods(raw_root)

            self.assertEqual([period.name for period in periods], ["20260501-20260507", "20260508-20260514"])

    def test_discover_raw_periods_reads_manifest_period_metadata(self):
        with TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "data" / "raw"
            period_dir = raw_root / "20260301-20260331"
            _write_xiaohongshu_file(period_dir / "小红书账号投放数据.xlsx", "note-mar", 10.0)
            (period_dir / "period_manifest.json").write_text(
                json.dumps(
                    {
                        "period_level": "month",
                        "period_key": "2026-03",
                        "period_label": "月｜2026年03月（数据时间：2026-03-01 至 2026-03-23）",
                        "period_start": "2026-03-01",
                        "period_end": "2026-03-31",
                        "data_start": "2026-03-01",
                        "data_end": "2026-03-23",
                        "source_type": "upload",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            periods = discover_raw_periods(raw_root)

            self.assertEqual(len(periods), 1)
            self.assertEqual(periods[0].period_level, "month")
            self.assertEqual(periods[0].period_key, "2026-03")
            self.assertEqual(periods[0].period_end, "2026-03-31")
            self.assertEqual(periods[0].data_end, "2026-03-23")

    def test_discover_raw_periods_treats_legacy_long_range_as_month(self):
        with TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "data" / "raw"
            _write_xiaohongshu_file(raw_root / "20260401-20260427" / "小红书账号投放数据.xlsx", "note-apr", 10.0)

            periods = discover_raw_periods(raw_root)

            self.assertEqual(len(periods), 1)
            self.assertEqual(periods[0].period_level, "month")
            self.assertEqual(periods[0].period_key, "2026-04")
            self.assertEqual(periods[0].period_start, "2026-04-01")
            self.assertEqual(periods[0].period_end, "2026-04-30")
            self.assertEqual(periods[0].data_end, "2026-04-27")

    def test_sync_raw_periods_generates_once_and_regenerates_after_file_change(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_root = tmp_path / "data" / "raw"
            _write_xiaohongshu_file(raw_root / "20260501-20260507" / "小红书账号投放数据.xlsx", "note-old", 20.0)
            _write_xiaohongshu_file(raw_root / "20260508-20260514" / "小红书账号投放数据.xlsx", "note-new", 10.0)

            first = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )
            second = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )
            _write_xiaohongshu_file(raw_root / "20260508-20260514" / "小红书账号投放数据.xlsx", "note-newer", 99.0)
            third = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual([item.status for item in first], ["generated", "generated"])
            self.assertEqual([item.status for item in second], ["skipped", "skipped"])
            self.assertEqual([item.status for item in third], ["skipped", "generated"])
            self.assertTrue(first[0].batch_id)
            self.assertTrue(third[1].batch_id)
            self.assertEqual(first[1].batch_id, third[1].batch_id)
            with closing(sqlite3.connect(tmp_path / "workflow.sqlite3")) as conn:
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
            raw_root = tmp_path / "data" / "raw"
            period_dir = raw_root / "20260508-20260514"
            _write_xiaohongshu_file(period_dir / "小红书账号投放数据.xlsx", "note-new", 10.0)

            first = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )
            (period_dir / "channel_clean").mkdir(exist_ok=True)
            _write_xiaohongshu_file(period_dir / "channel_clean" / "小红书账号投放数据_clean.xlsx", "note-clean", 999.0)
            second = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual([item.status for item in first], ["generated"])
            self.assertEqual([item.status for item in second], ["skipped"])

    def test_sync_raw_periods_uses_one_canonical_dir_for_duplicate_logical_period(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_root = tmp_path / "data" / "raw"
            duplicate_dir = raw_root / "20260401-20260427"
            canonical_dir = raw_root / "20260401-20260430"
            _write_xiaohongshu_file(duplicate_dir / "小红书账号投放数据.xlsx", "note-dup", 10.0)
            _write_xiaohongshu_file(canonical_dir / "小红书账号投放数据.xlsx", "note-canonical", 20.0)
            (canonical_dir / "period_manifest.json").write_text(
                json.dumps(
                    {
                        "period_level": "month",
                        "period_key": "2026-04",
                        "period_label": "月｜2026年04月（数据时间：2026-04-01 至 2026-04-27）",
                        "period_start": "2026-04-01",
                        "period_end": "2026-04-30",
                        "data_start": "2026-04-01",
                        "data_end": "2026-04-27",
                        "source_type": "upload",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            first = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )
            second = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual([item.period_name for item in first], ["20260401-20260430"])
            self.assertEqual([item.status for item in first], ["generated"])
            self.assertEqual([item.status for item in second], ["skipped"])
            with closing(sqlite3.connect(tmp_path / "workflow.sqlite3")) as conn:
                batch_count = conn.execute("select count(*) from upload_batches").fetchone()[0]
                spend = conn.execute("select sum(spend) from canonical_items").fetchone()[0]
            self.assertEqual(batch_count, 1)
            self.assertEqual(spend, 20.0)

    def test_sync_raw_periods_skips_backed_up_periods(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_root = tmp_path / "data" / "raw"
            period_dir = raw_root / "20260508-20260514"
            _write_xiaohongshu_file(period_dir / "小红书账号投放数据.xlsx", "note-new", 10.0)
            init_db(tmp_path / "workflow.sqlite3")
            with closing(sqlite3.connect(tmp_path / "workflow.sqlite3")) as conn:
                conn.execute(
                    """
                    insert into period_file_states (
                        period_key, period_start, period_end, status, batch_id,
                        raw_dir, backup_dir, updated_at
                    )
                    values ('2026-05-08|2026-05-14', '2026-05-08', '2026-05-14',
                            'backed_up', 'batch-archived', '', '', '2026-05-19T00:00:00+00:00')
                    """
                )
                conn.commit()

            results = sync_raw_periods(
                raw_root,
                db_path=tmp_path / "workflow.sqlite3",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                env_path=tmp_path / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual([item.status for item in results], ["skipped"])
            self.assertEqual(results[0].message, "周期已备份或删除")


if __name__ == "__main__":
    unittest.main()
