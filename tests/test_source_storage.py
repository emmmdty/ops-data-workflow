from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.periods import PERIOD_LEVEL_MONTH, PERIOD_LEVEL_WEEK, review_period_from_dates
from ops_data_workflow.source_storage import (
    discover_source_period_dirs,
    latest_reference_workbook,
    migrate_legacy_raw_to_source_layout,
    source_dir_for_period,
    source_period_from_path,
)


def _write_source_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{"标题": "样本", "消费": 1}]).to_excel(writer, index=False)


class SourceStorageTests(unittest.TestCase):
    def test_source_dir_for_period_uses_months_and_weeks(self):
        with TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            month = review_period_from_dates(date(2026, 5, 1), date(2026, 5, 25), PERIOD_LEVEL_MONTH)
            week = review_period_from_dates(date(2026, 4, 3), date(2026, 4, 9), PERIOD_LEVEL_WEEK)

            self.assertEqual(source_dir_for_period(data_root, month), data_root / "months" / "202605")
            self.assertEqual(source_dir_for_period(data_root, week), data_root / "weeks" / "20260403-20260409")

    def test_source_period_from_path_parses_internal_layout_without_raw_compatibility(self):
        with TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            month_dir = data_root / "months" / "202605"
            week_dir = data_root / "weeks" / "20260403-20260409"
            legacy_week_dir = data_root / "weeks" / "202604w1"
            raw_dir = data_root / "raw" / "20260403-20260409"

            month = source_period_from_path(month_dir)
            week = source_period_from_path(week_dir)

            self.assertEqual(month.period_level, PERIOD_LEVEL_MONTH)
            self.assertEqual(month.period_key, "2026-05")
            self.assertEqual(month.period_start, "2026-05-01")
            self.assertEqual(month.period_end, "2026-05-31")
            self.assertEqual(week.period_level, PERIOD_LEVEL_WEEK)
            self.assertEqual(week.period_key, "20260403-20260409")
            self.assertEqual(week.period_start, "2026-04-03")
            self.assertEqual(week.period_end, "2026-04-09")
            with self.assertRaises(ValueError):
                source_period_from_path(legacy_week_dir)
            with self.assertRaises(ValueError):
                source_period_from_path(raw_dir)

    def test_discover_source_period_dirs_only_scans_raw_input_layout(self):
        with TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            _write_source_file(data_root / "months" / "202605" / "小红书.xlsx")
            _write_source_file(data_root / "weeks" / "20260403-20260409" / "B站.xlsx")
            _write_source_file(data_root / "weeks" / "202604w1" / "旧周目录.xlsx")
            _write_source_file(data_root / "raw" / "20260403-20260409" / "旧路径.xlsx")
            _write_source_file(data_root / "reference" / "原生内容投稿-20260527.xlsx")

            periods = discover_source_period_dirs(data_root)

            self.assertEqual([period.path for period in periods], [
                data_root / "weeks" / "20260403-20260409",
                data_root / "months" / "202605",
            ])

    def test_latest_reference_workbook_ignores_backups_and_temp_files(self):
        with TemporaryDirectory() as tmp:
            reference_dir = Path(tmp) / "data" / "reference"
            _write_source_file(reference_dir / "原生内容投稿-20260520.xlsx")
            _write_source_file(reference_dir / "原生内容投稿-20260527.backup-before-dedupe.xlsx")
            _write_source_file(reference_dir / "~$原生内容投稿-20260528.xlsx")
            latest = reference_dir / "原生内容投稿-20260527.xlsx"
            _write_source_file(latest)

            self.assertEqual(latest_reference_workbook(reference_dir), latest)

    def test_migrate_legacy_raw_only_copies_source_tabular_files(self):
        with TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            legacy_dir = data_root / "raw" / "20260501-20260525"
            _write_source_file(legacy_dir / "B站.xlsx")
            _write_source_file(legacy_dir / "cleaned.xlsx")
            _write_source_file(legacy_dir / "channel_clean" / "B站_clean.xlsx")
            (legacy_dir / "period_manifest.json").write_text(
                '{"period_start":"2026-05-01","period_end":"2026-05-31","period_level":"month","period_key":"2026-05","data_start":"2026-05-01","data_end":"2026-05-25"}',
                encoding="utf-8",
            )

            results = migrate_legacy_raw_to_source_layout(data_root)

            self.assertEqual(len(results), 1)
            target = data_root / "months" / "202605"
            self.assertTrue((target / "B站.xlsx").exists())
            self.assertFalse((target / "cleaned.xlsx").exists())
            self.assertFalse((target / "B站_clean.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
