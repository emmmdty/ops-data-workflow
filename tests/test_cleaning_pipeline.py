from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.cleaning_pipeline import split_channel_total_rows
from ops_data_workflow.storage import init_db
from ops_data_workflow.storage import (
    list_content_performance_items,
    list_period_channel_totals,
    persist_content_performance_items,
    persist_workflow_result,
)


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

    def test_period_channel_totals_falls_back_to_total_summary_for_legacy_batches(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with sqlite3.connect(db_path) as conn:
                pd.DataFrame(
                    [
                        {
                            "batch_id": "legacy-batch",
                            "channel": "抖音商业化",
                            "platform": "抖音",
                            "spend": 1000,
                            "impressions": 20000,
                            "clicks": 0,
                            "activations": 20,
                            "first_pay_count": 5,
                            "activation_cost": 50,
                            "first_pay_cost": 200,
                        },
                        {
                            "batch_id": "legacy-batch",
                            "channel": "总计",
                            "platform": "",
                            "spend": 1000,
                            "impressions": 20000,
                            "clicks": 0,
                            "activations": 20,
                            "first_pay_count": 5,
                            "activation_cost": 50,
                            "first_pay_cost": 200,
                        },
                    ]
                ).to_sql("total_summary_items", conn, if_exists="append", index=False)

            totals = list_period_channel_totals(db_path, batch_id="legacy-batch")

            self.assertEqual(set(totals["channel"]), {"抖音商业化", "总计"})
            self.assertTrue(totals["is_channel_total"].astype(bool).all())

    def test_douyin_primary_without_secondaries_keeps_empty_performance_l2(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-05-01",
                        "period_end": "2026-05-31",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_id": "7626764911542516736",
                        "material_id": "7626764911542516736",
                        "content_url": "https://www.douyin.com/video/7626764911542516736",
                        "title": "今朝没酒喝凉水#一笑江湖 #翻唱",
                        "category_l1": "说唱",
                        "category_l2": "",
                        "matched_category_l1": "说唱",
                        "matched_category_l2": "",
                        "matched_content_type": "",
                        "content_type": "说唱",
                        "spend": 100,
                        "impressions": 1000,
                        "clicks": 10,
                        "activations": 2,
                        "first_pay_count": 1,
                    }
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            performance = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(performance.iloc[0]["category_l1"], "说唱")
            self.assertEqual(performance.iloc[0]["category_l2"], "")
            self.assertEqual(performance.iloc[0]["content_type"], "说唱")
            with sqlite3.connect(db_path) as conn:
                raw_l2 = conn.execute("select category_l2 from content_performance_items").fetchone()[0]
            self.assertEqual(raw_l2, "")


if __name__ == "__main__":
    unittest.main()
