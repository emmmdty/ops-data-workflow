from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.cleaning_pipeline import split_channel_total_rows
from ops_data_workflow.storage import list_content_performance_items, list_period_channel_totals, persist_workflow_result


class CleaningPipelineTests(unittest.TestCase):
    def test_single_row_channel_workbook_enters_totals_not_material_detail(self):
        canonical = pd.DataFrame(
            [
                {
                    "period_start": "2026-06-01",
                    "period_end": "2026-06-07",
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "",
                    "title": "",
                    "source_file": "B站总数据.xlsx",
                    "source_sheet": "Sheet1",
                    "source_row": 2,
                    "spend": 1000,
                    "impressions": 200000,
                    "clicks": 100,
                    "activations": 20,
                    "first_pay_count": 4,
                }
            ]
        )

        detail, totals = split_channel_total_rows(canonical)

        self.assertTrue(detail.empty)
        self.assertEqual(len(totals), 1)
        row = totals.iloc[0]
        self.assertEqual(row["channel"], "B站市场部")
        self.assertEqual(float(row["spend"]), 1000.0)
        self.assertTrue(bool(row["is_channel_total"]))

    def test_single_row_channel_total_ignores_synthetic_row_identity(self):
        canonical = pd.DataFrame(
            [
                {
                    "period_start": "2026-05-26",
                    "period_end": "2026-06-04",
                    "platform": "小红书",
                    "channel": "小红书商业化",
                    "content_id": "row:d926474c43af",
                    "material_id": "row:d926474c43af",
                    "title": "小红书（商业化） 第3行",
                    "source_file": "小红书（商业化）.xlsx",
                    "source_sheet": "Sheet1",
                    "source_row": 3,
                    "spend": 104792,
                    "impressions": 2020000,
                    "clicks": 0,
                    "activations": 2077,
                    "first_pay_count": 541,
                }
            ]
        )

        detail, totals = split_channel_total_rows(canonical)

        self.assertTrue(detail.empty)
        self.assertEqual(len(totals), 1)
        self.assertEqual(float(totals.iloc[0]["spend"]), 104792.0)

    def test_persist_workflow_result_stores_channel_totals_separately_from_performance(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "B站",
                        "channel": "B站市场部",
                        "content_id": "",
                        "title": "",
                        "source_file": "B站总数据.xlsx",
                        "source_sheet": "Sheet1",
                        "source_row": 2,
                        "spend": 1000,
                        "impressions": 200000,
                        "clicks": 100,
                        "activations": 20,
                        "first_pay_count": 4,
                    }
                ]
            )
            total_summary = pd.DataFrame(
                [
                    {
                        "channel": "B站市场部",
                        "spend": 1000,
                        "impressions": 200000,
                        "clicks": 100,
                        "activations": 20,
                        "first_pay_count": 4,
                    },
                    {
                        "channel": "总计",
                        "spend": 1000,
                        "impressions": 200000,
                        "clicks": 100,
                        "activations": 20,
                        "first_pay_count": 4,
                    },
                ]
            )

            persist_workflow_result(
                db_path=db_path,
                batch_id="batch-1",
                period_start="2026-06-01",
                period_end="2026-06-07",
                archive_dir=Path(tmp) / "processed",
                output_dir=Path(tmp) / "outputs",
                archived_files=[],
                canonical=canonical,
                channel_summary=pd.DataFrame(),
                total_summary=total_summary,
                platform_summary=pd.DataFrame(),
                platform_category_summary=pd.DataFrame(),
                category_summary=pd.DataFrame(),
                top_content_items=pd.DataFrame(),
                account_audit=pd.DataFrame(),
                cover_metrics=pd.DataFrame(),
                data_quality=pd.DataFrame(),
                preprocessing_report=pd.DataFrame(),
                duplicate_merge_details=pd.DataFrame(),
                conflict_retention_details=pd.DataFrame(),
                missing_value_details=pd.DataFrame(),
                channel_comparison=pd.DataFrame(),
                topic_label_items=pd.DataFrame(),
                cleaned_asset_table=pd.DataFrame(),
                content_recap_table=pd.DataFrame(),
                unanalyzable_summary=pd.DataFrame(),
                ai_summary="",
                comparison_batch_id=None,
                comparison_note="",
            )

            performance = list_content_performance_items(db_path, batch_id="batch-1")
            totals = list_period_channel_totals(db_path, batch_id="batch-1")

            self.assertTrue(performance.empty)
            self.assertEqual(len(totals), 2)
            self.assertIn("总计", set(totals["channel"]))


if __name__ == "__main__":
    unittest.main()
