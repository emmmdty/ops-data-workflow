from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from zipfile import ZipFile

import pandas as pd

from ops_data_workflow.periods import (
    PERIOD_LEVEL_MONTH,
    PERIOD_LEVEL_WEEK,
    infer_review_period_from_text,
)
from ops_data_workflow.raw_normalization import normalize_uploaded_periods


class FakeUpload:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def _workbook_bytes() -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "视频BVID": "bv1",
                    "视频标题": "样本标题",
                    "花费": 1,
                    "展示量": 10,
                    "点击量": 2,
                    "应用激活数": 1,
                }
            ]
        ).to_excel(writer, sheet_name="sheet1", index=False)
    return buffer.getvalue()


class PeriodInferenceTests(unittest.TestCase):
    def test_monthly_total_data_uses_calendar_month_and_keeps_actual_data_dates(self):
        period = infer_review_period_from_text("3月总数据/0301-0323 B站数据.xlsx", default_year=2026)

        self.assertIsNotNone(period)
        assert period is not None
        self.assertEqual(period.period_level, PERIOD_LEVEL_MONTH)
        self.assertEqual(period.period_key, "2026-03")
        self.assertEqual(period.period_start, "2026-03-01")
        self.assertEqual(period.period_end, "2026-03-31")
        self.assertEqual(period.data_start, "2026-03-01")
        self.assertEqual(period.data_end, "2026-03-23")
        self.assertIn("数据时间：2026-03-01 至 2026-03-23", period.period_label)

    def test_weekly_range_without_year_uses_default_year_and_cross_year_correction(self):
        period = infer_review_period_from_text("1228-0103数据/B站.xlsx", default_year=2026)

        self.assertIsNotNone(period)
        assert period is not None
        self.assertEqual(period.period_level, PERIOD_LEVEL_WEEK)
        self.assertEqual(period.period_start, "2026-12-28")
        self.assertEqual(period.period_end, "2027-01-03")
        self.assertEqual(period.period_key, "20261228-20270103")

    def test_normalize_uploaded_periods_splits_single_week_zip_into_period_buckets(self):
        workbook = _workbook_bytes()
        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, "w") as archive:
            archive.writestr("4月单周数据/0403-0409数据/B站.xlsx", workbook)
            archive.writestr("4月单周数据/0410-0416数据/B站.xlsx", workbook)
            archive.writestr("4月单周数据/0417-0423数据/B站.xlsx", workbook)
            archive.writestr("4月单周数据/0417-0423数据/长视频0424.mp4", b"ignored")
            archive.writestr("__MACOSX/4月单周数据/0417-0423数据/._B站.xlsx", b"ignored")

        with TemporaryDirectory() as tmp:
            result = normalize_uploaded_periods(
                [FakeUpload("4月单周数据.zip", zip_buffer.getvalue())],
                Path(tmp) / "raw",
                default_year=2026,
            )

            self.assertEqual([bucket.review_period.period_key for bucket in result], [
                "20260403-20260409",
                "20260410-20260416",
                "20260417-20260423",
            ])
            self.assertTrue(all(bucket.review_period.period_level == PERIOD_LEVEL_WEEK for bucket in result))
            self.assertEqual([len(bucket.files) for bucket in result], [1, 1, 1])
            manifest = json.loads(result[2].manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["file_count"], 1)
            self.assertEqual(manifest["ignored_file_count"], 1)

    def test_normalize_uploaded_periods_keeps_same_name_files_without_overwrite(self):
        uploads = [
            FakeUpload("0508-0514 数据/B站数据.xlsx", _workbook_bytes()),
            FakeUpload("补充/0508-0514 数据/B站数据.xlsx", _workbook_bytes()),
        ]

        with TemporaryDirectory() as tmp:
            result = normalize_uploaded_periods(uploads, Path(tmp) / "raw", default_year=2026)

            self.assertEqual(len(result), 1)
            bucket = result[0]
            self.assertEqual(bucket.review_period.period_key, "20260508-20260514")
            names = sorted(path.name for path in bucket.files)
            self.assertEqual(len(names), 2)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(all(path.exists() for path in bucket.files))


if __name__ == "__main__":
    unittest.main()
