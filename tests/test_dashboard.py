from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.dashboard import (
    DashboardFilters,
    aggregate_dashboard,
    build_period_comparison_between_batches,
    build_period_comparison_for_batch,
    build_content_recommendations,
    build_dashboard_summary,
    detect_high_metric_anomalies,
    filter_dashboard_items,
    format_beijing_datetime,
    load_all_dashboard_items,
    load_channel_comparison_for_batch,
    load_dashboard_items,
    load_dashboard_items_for_batch,
    load_latest_dashboard_items,
    list_successful_dashboard_batches,
    metric_sort_ascending,
    summarize_dimension_for_metric,
    summarize_topics_for_selection,
    summarize_content_type_trends,
    summarize_content_types,
    summarize_unique_content,
)
from ops_data_workflow.reporting import localize_and_sort_columns
from ops_data_workflow.storage import init_db


def _append_frame(conn: sqlite3.Connection, table_name: str, batch_id: str, frame: pd.DataFrame) -> None:
    stored = frame.copy()
    stored.insert(0, "batch_id", batch_id)
    stored.to_sql(table_name, conn, if_exists="append", index=False)


def _seed_dashboard_db(db_path: Path) -> None:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        batches = [
            ("batch-old", "2026-04-01", "2026-04-07", "ok"),
            ("batch-new", "2026-04-08", "2026-04-14", "ok"),
            ("batch-failed", "2026-04-15", "2026-04-21", "failed"),
        ]
        for index, (batch_id, period_start, period_end, status) in enumerate(batches, start=1):
            conn.execute(
                """
                insert into upload_batches (
                    batch_id, period_start, period_end, created_at, archive_dir,
                    output_dir, status, comparison_batch_id, comparison_note
                )
                values (?, ?, ?, ?, '', '', ?, '', '')
                """,
                (batch_id, period_start, period_end, f"2026-05-13T00:0{index}:00+00:00", status),
            )

        _append_frame(
            conn,
            "canonical_items",
            "batch-old",
            pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "period_start": "2026-04-01",
                        "period_end": "2026-04-07",
                        "content_id": "dy-1",
                        "material_id": "mat-1",
                        "title": "短线交易高手",
                        "account": "同花顺投资",
                        "primary_category": "",
                        "category_l1": "",
                        "category_l2": "股友说",
                        "category_l3": "短线交易",
                        "content_category": "股友说",
                        "spend": 100.0,
                        "impressions": 1000.0,
                        "clicks": 100.0,
                        "activations": 10.0,
                        "first_pay_count": 2.0,
                        "source_file": "抖音商业化.xlsx",
                    },
                    {
                        "platform": "B站",
                        "channel": "B站",
                        "period_start": "2026-04-01",
                        "period_end": "2026-04-07",
                        "content_id": "bv-1",
                        "material_id": "mat-b",
                        "title": "长视频深度财经",
                        "account": "12345",
                        "primary_category": "",
                        "category_l1": "",
                        "category_l2": "采访",
                        "category_l3": "新手教学",
                        "content_category": "采访",
                        "spend": 200.0,
                        "impressions": 2000.0,
                        "clicks": 100.0,
                        "activations": 5.0,
                        "first_pay_count": 1.0,
                        "source_file": "B站.xlsx",
                    },
                ]
            ),
        )
        _append_frame(
            conn,
            "canonical_items",
            "batch-new",
            pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "period_start": "2026-04-08",
                        "period_end": "2026-04-14",
                        "content_id": "dy-2",
                        "material_id": "mat-2",
                        "title": "热点行情复盘",
                        "account": "同花顺投资",
                        "primary_category": "",
                        "category_l1": "",
                        "category_l2": "资讯",
                        "category_l3": "热点行情",
                        "content_category": "资讯",
                        "spend": 50.0,
                        "impressions": 500.0,
                        "clicks": 25.0,
                        "activations": 5.0,
                        "first_pay_count": 0.0,
                        "source_file": "抖音商业化.xlsx",
                    }
                ]
            ),
        )
        _append_frame(
            conn,
            "canonical_items",
            "batch-failed",
            pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "period_start": "2026-04-15",
                        "period_end": "2026-04-21",
                        "content_id": "note-failed",
                        "material_id": "note-failed",
                        "title": "失败批次内容",
                        "account": "同花顺投资",
                        "primary_category": "",
                        "category_l1": "",
                        "category_l2": "资讯",
                        "category_l3": "热点行情",
                        "content_category": "资讯",
                        "spend": 999.0,
                        "impressions": 999.0,
                        "clicks": 999.0,
                        "activations": 999.0,
                        "first_pay_count": 999.0,
                        "source_file": "小红书商业化.xlsx",
                    }
                ]
            ),
        )
        conn.commit()


class DashboardTests(unittest.TestCase):
    def test_load_dashboard_items_reads_only_successful_batches(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            _seed_dashboard_db(db_path)

            items = load_dashboard_items(db_path)

            self.assertEqual(set(items["batch_id"]), {"batch-old", "batch-new"})
            self.assertNotIn("note-failed", set(items["content_id"]))
            self.assertIn("batch_period_start", items.columns)
            self.assertIn("batch_period_end", items.columns)

    def test_load_latest_dashboard_items_reads_only_latest_successful_batch(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            _seed_dashboard_db(db_path)

            items = load_latest_dashboard_items(db_path)

            self.assertEqual(set(items["batch_id"]), {"batch-new"})
            self.assertEqual(list(items["content_id"]), ["dy-2"])

    def test_list_successful_dashboard_batches_and_load_specific_batch(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            _seed_dashboard_db(db_path)

            batches = list_successful_dashboard_batches(db_path)
            items = load_dashboard_items_for_batch(db_path, "batch-old")

            self.assertEqual(list(batches["batch_id"]), ["batch-new", "batch-old"])
            self.assertEqual(list(batches["period_label"]), ["2026-04-08 至 2026-04-14", "2026-04-01 至 2026-04-07"])
            self.assertEqual(set(items["batch_id"]), {"batch-old"})
            self.assertEqual(set(items["content_id"]), {"dy-1", "bv-1"})

    def test_load_channel_comparison_for_batch_reads_persisted_growth(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    insert into upload_batches (
                        batch_id, period_start, period_end, created_at, archive_dir,
                        output_dir, status, comparison_batch_id, comparison_note
                    )
                    values ('batch-new', '2026-04-08', '2026-04-14', '2026-05-13T00:00:00+00:00', '', '', 'ok', 'batch-old', '')
                    """
                )
                _append_frame(
                    conn,
                    "channel_comparison_items",
                    "batch-new",
                    pd.DataFrame(
                        [
                            {
                                "channel": "抖音商业化",
                                "spend_current": 120.0,
                                "spend_previous": 100.0,
                                "spend_change_rate": 0.2,
                                "activations_current": 12.0,
                                "activations_previous": 10.0,
                                "activations_change_rate": 0.2,
                                "first_pay_count_current": 6.0,
                                "first_pay_count_previous": 3.0,
                                "first_pay_count_change_rate": 1.0,
                            },
                            {
                                "channel": "总计",
                                "spend_current": 120.0,
                                "spend_previous": 100.0,
                                "spend_change_rate": 0.2,
                                "activations_current": 12.0,
                                "activations_previous": 10.0,
                                "activations_change_rate": 0.2,
                                "first_pay_count_current": 6.0,
                                "first_pay_count_previous": 3.0,
                                "first_pay_count_change_rate": 1.0,
                            },
                        ]
                    ),
                )
                conn.commit()

            comparison = load_channel_comparison_for_batch(db_path, "batch-new")

            self.assertEqual(list(comparison["channel"]), ["抖音商业化", "总计"])
            total = comparison[comparison["channel"].eq("总计")].iloc[0]
            self.assertAlmostEqual(total["first_pay_count_change_rate"], 1.0)

    def test_build_period_comparison_for_batch_uses_period_order_not_created_at(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = [
                    ("batch-current", "2026-05-01", "2026-05-07", "2026-05-01T00:00:00+00:00"),
                    ("batch-previous", "2026-04-24", "2026-04-30", "2026-05-10T00:00:00+00:00"),
                    ("batch-older", "2026-04-10", "2026-04-16", "2026-05-20T00:00:00+00:00"),
                ]
                for batch_id, period_start, period_end, created_at in rows:
                    conn.execute(
                        """
                        insert into upload_batches (
                            batch_id, period_start, period_end, created_at, archive_dir,
                            output_dir, status, comparison_batch_id, comparison_note
                        )
                        values (?, ?, ?, ?, '', '', 'ok', '', '')
                        """,
                        (batch_id, period_start, period_end, created_at),
                    )
                _append_frame(
                    conn,
                    "canonical_items",
                    "batch-current",
                    pd.DataFrame(
                        [
                            {
                                "platform": "抖音",
                                "channel": "抖音商业化",
                                "period_start": "2026-05-01",
                                "period_end": "2026-05-07",
                                "content_id": "current",
                                "spend": 200.0,
                                "activations": 20.0,
                                "first_pay_count": 10.0,
                            }
                        ]
                    ),
                )
                _append_frame(
                    conn,
                    "canonical_items",
                    "batch-previous",
                    pd.DataFrame(
                        [
                            {
                                "platform": "抖音",
                                "channel": "抖音商业化",
                                "period_start": "2026-04-24",
                                "period_end": "2026-04-30",
                                "content_id": "previous",
                                "spend": 100.0,
                                "activations": 10.0,
                                "first_pay_count": 5.0,
                            }
                        ]
                    ),
                )
                _append_frame(
                    conn,
                    "canonical_items",
                    "batch-older",
                    pd.DataFrame(
                        [
                            {
                                "platform": "抖音",
                                "channel": "抖音商业化",
                                "period_start": "2026-04-10",
                                "period_end": "2026-04-16",
                                "content_id": "older",
                                "spend": 20.0,
                                "activations": 2.0,
                                "first_pay_count": 1.0,
                            }
                        ]
                    ),
                )
                conn.commit()

            comparison = build_period_comparison_for_batch(db_path, "batch-current").set_index("channel")

            self.assertIn("总计", comparison.index)
            self.assertAlmostEqual(comparison.loc["总计", "spend_previous"], 100.0)
            self.assertAlmostEqual(comparison.loc["总计", "spend_change_rate"], 1.0)
            self.assertAlmostEqual(comparison.loc["抖音商业化", "first_pay_count_change_rate"], 1.0)
            self.assertAlmostEqual(comparison.loc["抖音商业化", "first_pay_rate_current"], 0.5)
            self.assertAlmostEqual(comparison.loc["抖音商业化", "first_pay_rate_previous"], 0.5)
            self.assertAlmostEqual(comparison.loc["抖音商业化", "first_pay_rate_change_rate"], 0.0)

    def test_build_period_comparison_between_batches_uses_selected_batch(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                for batch_id, period_start, period_end in [
                    ("batch-current", "2026-05-01", "2026-05-07"),
                    ("batch-previous", "2026-04-24", "2026-04-30"),
                    ("batch-older", "2026-04-10", "2026-04-16"),
                ]:
                    conn.execute(
                        """
                        insert into upload_batches (
                            batch_id, period_start, period_end, created_at, archive_dir,
                            output_dir, status, comparison_batch_id, comparison_note
                        )
                        values (?, ?, ?, '2026-05-13T00:00:00+00:00', '', '', 'ok', '', '')
                        """,
                        (batch_id, period_start, period_end),
                    )
                for batch_id, spend, activations, first_pay_count in [
                    ("batch-current", 200.0, 20.0, 10.0),
                    ("batch-previous", 100.0, 10.0, 5.0),
                    ("batch-older", 50.0, 5.0, 1.0),
                ]:
                    _append_frame(
                        conn,
                        "canonical_items",
                        batch_id,
                        pd.DataFrame(
                            [
                                {
                                    "platform": "抖音",
                                    "channel": "抖音商业化",
                                    "period_start": "2026-05-01",
                                    "period_end": "2026-05-07",
                                    "content_id": batch_id,
                                    "spend": spend,
                                    "activations": activations,
                                    "first_pay_count": first_pay_count,
                                }
                            ]
                        ),
                    )
                conn.commit()

            comparison = build_period_comparison_between_batches(
                db_path,
                "batch-current",
                "batch-older",
            ).set_index("channel")

            self.assertIn("总计", comparison.index)
            self.assertAlmostEqual(comparison.loc["总计", "spend_previous"], 50.0)
            self.assertAlmostEqual(comparison.loc["总计", "spend_change_rate"], 3.0)
            self.assertAlmostEqual(comparison.loc["抖音商业化", "first_pay_count_change_rate"], 9.0)

    def test_format_beijing_datetime_converts_utc_created_at_for_selector(self):
        result = format_beijing_datetime("2026-05-19T01:02:03+00:00")

        self.assertEqual(result, "2026年05月19日  09:02:03")

    def test_list_successful_dashboard_batches_keeps_latest_batch_per_period(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                for batch_id, created_at in [
                    ("batch-early", "2026-05-10T00:00:00+00:00"),
                    ("batch-late", "2026-05-11T00:00:00+00:00"),
                ]:
                    conn.execute(
                        """
                        insert into upload_batches (
                            batch_id, period_start, period_end, created_at, archive_dir,
                            output_dir, status, comparison_batch_id, comparison_note
                        )
                        values (?, '2026-04-08', '2026-04-14', ?, '', '', 'ok', '', '')
                        """,
                        (batch_id, created_at),
                    )
                _append_frame(
                    conn,
                    "canonical_items",
                    "batch-late",
                    pd.DataFrame([{"platform": "抖音", "channel": "抖音商业化", "content_id": "late-item"}]),
                )
                conn.commit()

            batches = list_successful_dashboard_batches(db_path)
            latest = load_latest_dashboard_items(db_path)

            self.assertEqual(list(batches["batch_id"]), ["batch-late"])
    def test_list_successful_dashboard_batches_hides_backed_up_periods(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            _seed_dashboard_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    insert into period_file_states (
                        period_key, period_start, period_end, status, batch_id,
                        raw_dir, backup_dir, updated_at
                    )
                    values ('2026-04-08|2026-04-14', '2026-04-08', '2026-04-14',
                            'backed_up', 'batch-new', '', '', '2026-05-19T00:00:00+00:00')
                    """
                )
                conn.commit()

            batches = list_successful_dashboard_batches(db_path)
            items = load_dashboard_items(db_path)

            self.assertEqual(list(batches["batch_id"]), ["batch-old"])
            self.assertEqual(set(items["batch_id"]), {"batch-old"})

    def test_load_latest_dashboard_items_prefers_latest_period_not_latest_import(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    insert into upload_batches (
                        batch_id, period_start, period_end, created_at, archive_dir,
                        output_dir, status, comparison_batch_id, comparison_note
                    )
                    values ('batch-april', '2026-04-01', '2026-04-30', '2026-05-10T00:00:00+00:00', '', '', 'ok', '', '')
                    """
                )
                conn.execute(
                    """
                    insert into upload_batches (
                        batch_id, period_start, period_end, created_at, archive_dir,
                        output_dir, status, comparison_batch_id, comparison_note
                    )
                    values ('batch-march', '2026-03-01', '2026-03-31', '2026-05-12T00:00:00+00:00', '', '', 'ok', '', '')
                    """
                )
                _append_frame(
                    conn,
                    "canonical_items",
                    "batch-april",
                    pd.DataFrame([{"platform": "抖音", "channel": "抖音商业化", "content_id": "april-item"}]),
                )
                _append_frame(
                    conn,
                    "canonical_items",
                    "batch-march",
                    pd.DataFrame([{"platform": "抖音", "channel": "抖音商业化", "content_id": "march-item"}]),
                )
                conn.commit()

            items = load_latest_dashboard_items(db_path)

            self.assertEqual(set(items["batch_id"]), {"batch-april"})
            self.assertEqual(list(items["content_id"]), ["april-item"])

    def test_load_all_dashboard_items_aliases_successful_history(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            _seed_dashboard_db(db_path)

            items = load_all_dashboard_items(db_path)

            self.assertEqual(set(items["batch_id"]), {"batch-old", "batch-new"})
            self.assertNotIn("batch-failed", set(items["batch_id"]))

    def test_filter_dashboard_items_uses_overlapping_period_and_combined_filters(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            _seed_dashboard_db(db_path)
            items = load_dashboard_items(db_path)

            filtered = filter_dashboard_items(
                items,
                DashboardFilters(
                    period_start="2026-04-07",
                    period_end="2026-04-09",
                    platforms=("抖音",),
                    content_categories=("资讯",),
                    text_query="热点",
                ),
            )

            self.assertEqual(list(filtered["content_id"]), ["dy-2"])

    def test_filter_dashboard_items_supports_l3_author_and_account_id_queries(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "category_l1": "",
                    "category_l2": "采访",
                    "category_l3": "新手教学",
                    "primary_category": "",
                    "content_category": "采访",
                    "content_id": "bv-1",
                    "material_id": "mat-b",
                    "title": "长视频深度财经",
                    "account_id": "1622777305",
                    "account": "",
                    "author": "",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "clicks": 100.0,
                    "activations": 10.0,
                    "first_pay_count": 1.0,
                },
                {
                    "platform": "小红书商业化",
                    "category_l1": "",
                    "category_l2": "热点行情",
                    "category_l3": "芯片主题",
                    "primary_category": "",
                    "content_category": "热点行情",
                    "content_id": "note-1",
                    "material_id": "note-1",
                    "title": "芯片行情",
                    "account_id": "",
                    "account": "同花顺理财",
                    "author": "同花顺理财",
                    "spend": 50.0,
                    "impressions": 500.0,
                    "clicks": 50.0,
                    "activations": 5.0,
                    "first_pay_count": 0.0,
                },
            ]
        )

        by_l3 = filter_dashboard_items(frame, DashboardFilters(category_l3=("新手教学",)))
        by_account_id = filter_dashboard_items(frame, DashboardFilters(text_query="1622777305"))
        by_author = filter_dashboard_items(frame, DashboardFilters(text_query="同花顺理财"))

        self.assertEqual(list(by_l3["content_id"]), ["bv-1"])
        self.assertEqual(list(by_account_id["content_id"]), ["bv-1"])
        self.assertEqual(list(by_author["content_id"]), ["note-1"])

    def test_aggregate_dashboard_recalculates_rates_from_sums(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "primary_category": "",
                    "content_category": "股友说",
                    "content_id": "dy-1",
                    "title": "内容1",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "clicks": 100.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "activation_cost": 1.0,
                },
                {
                    "platform": "抖音",
                    "primary_category": "",
                    "content_category": "股友说",
                    "content_id": "dy-2",
                    "title": "内容2",
                    "spend": 300.0,
                    "impressions": 1000.0,
                    "clicks": 50.0,
                    "activations": 10.0,
                    "first_pay_count": 8.0,
                    "activation_cost": 999.0,
                },
            ]
        )

        result = aggregate_dashboard(frame, ["platform", "content_category"])
        row = result.iloc[0]

        self.assertEqual(row["item_count"], 2)
        self.assertAlmostEqual(row["spend"], 400.0)
        self.assertAlmostEqual(row["ctr"], 150.0 / 2000.0)
        self.assertAlmostEqual(row["activation_cost"], 20.0)
        self.assertAlmostEqual(row["first_pay_cost"], 40.0)
        self.assertAlmostEqual(row["first_pay_rate"], 0.5)

    def test_metric_sorting_treats_costs_as_ascending_and_volume_as_descending(self):
        self.assertFalse(metric_sort_ascending("spend"))
        self.assertFalse(metric_sort_ascending("activations"))
        self.assertTrue(metric_sort_ascending("activation_cost"))
        self.assertTrue(metric_sort_ascending("first_pay_cost"))

    def test_summarize_dimension_for_metric_handles_top_n_and_cost_direction(self):
        frame = pd.DataFrame(
            [
                {"channel": "抖音商业化", "category_l2": "资讯", "spend": 100.0, "activations": 10.0, "first_pay_count": 2.0},
                {"channel": "抖音商业化", "category_l2": "股友说", "spend": 20.0, "activations": 10.0, "first_pay_count": 1.0},
                {"channel": "抖音商业化", "category_l2": "盘点", "spend": 50.0, "activations": 5.0, "first_pay_count": 0.0},
            ]
        )

        volume_rows = summarize_dimension_for_metric(frame, "category_l2", "spend", top_n=2)
        cost_rows = summarize_dimension_for_metric(frame, "category_l2", "activation_cost", top_n=2)

        self.assertEqual(list(volume_rows["category_name"]), ["资讯", "盘点"])
        self.assertEqual(list(cost_rows["category_name"]), ["股友说", "盘点"])
        self.assertAlmostEqual(cost_rows.iloc[0]["activation_cost"], 2.0)

    def test_summarize_topics_for_selection_uses_single_bilibili_bucket(self):
        frame = pd.DataFrame(
            [
                {"channel": "B站", "title": "标题A", "category_l2": "B站全部", "category_l3": "题材A", "spend": 100.0, "activations": 10.0, "first_pay_count": 2.0},
                {"channel": "B站", "title": "标题B", "category_l2": "B站全部", "category_l3": "题材B", "spend": 20.0, "activations": 2.0, "first_pay_count": 1.0},
                {"channel": "抖音商业化", "title": "标题C", "category_l2": "资讯", "category_l3": "热点行情", "spend": 50.0, "activations": 5.0, "first_pay_count": 1.0},
            ]
        )

        result = summarize_topics_for_selection(frame, "B站", None, "spend", top_n=5)

        self.assertEqual(list(result["topic_name"]), ["题材A", "题材B"])
        self.assertEqual(set(result["category_name"]), {"B站全部"})

    def test_summarize_topics_for_selection_accepts_ai_topic_labels(self):
        frame = pd.DataFrame(
            [
                {"channel": "抖音商业化", "title": "短线交易高手", "category_l2": "股友说", "category_l3": "", "spend": 100.0, "activations": 10.0, "first_pay_count": 2.0},
                {"channel": "抖音商业化", "title": "短线交易心法", "category_l2": "股友说", "category_l3": "", "spend": 80.0, "activations": 8.0, "first_pay_count": 1.0},
                {"channel": "抖音商业化", "title": "芯片行情复盘", "category_l2": "股友说", "category_l3": "", "spend": 30.0, "activations": 3.0, "first_pay_count": 0.0},
            ]
        )

        result = summarize_topics_for_selection(
            frame,
            "抖音商业化",
            "股友说",
            "spend",
            top_n=5,
            topic_labels={0: "短线交易", 1: "短线交易", 2: "芯片行情"},
        )

        self.assertEqual(list(result["topic_name"]), ["短线交易", "芯片行情"])
        self.assertAlmostEqual(result.iloc[0]["spend"], 180.0)

    def test_summarize_topics_for_selection_falls_back_to_title_and_ids(self):
        frame = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "title": "短线交易高手",
                    "content_id": "content-1",
                    "material_id": "material-1",
                    "category_l2": "股友说",
                    "category_l3": "",
                    "spend": 100.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                },
                {
                    "channel": "抖音商业化",
                    "title": "",
                    "content_id": "content-2",
                    "material_id": "material-2",
                    "category_l2": "股友说",
                    "category_l3": "",
                    "spend": 80.0,
                    "activations": 8.0,
                    "first_pay_count": 1.0,
                },
            ]
        )

        result = summarize_topics_for_selection(frame, "抖音商业化", "股友说", "spend", top_n=5)

        self.assertEqual(list(result["topic_name"]), ["短线交易高手", "未命名题材-content-2"])

    def test_detect_high_metric_anomalies_flags_missing_titles_and_categories(self):
        frame = pd.DataFrame(
            [
                {
                    "title": "",
                    "category_l2": "",
                    "category_l3": "题材A",
                    "content_id": "v1",
                    "material_id": "m1",
                    "channel": "抖音商业化",
                    "spend": 100.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                },
                {
                    "title": "正常标题",
                    "category_l2": "资讯",
                    "category_l3": "题材B",
                    "content_id": "v2",
                    "material_id": "m2",
                    "channel": "抖音商业化",
                    "spend": 10.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
            ]
        )

        anomalies = detect_high_metric_anomalies(frame, "spend")

        self.assertEqual(list(anomalies["missing_title"]["content_id"]), ["v1"])
        self.assertEqual(list(anomalies["missing_category_l2"]["content_id"]), ["v1"])

    def test_detect_high_metric_anomalies_flags_high_cost_outliers(self):
        frame = pd.DataFrame(
            [
                {"title": "成本异常", "category_l2": "资讯", "content_id": "v1", "material_id": "m1", "channel": "抖音商业化", "spend": 200.0, "activations": 1.0, "first_pay_count": 0.0},
                {"title": "正常成本", "category_l2": "资讯", "content_id": "v2", "material_id": "m2", "channel": "抖音商业化", "spend": 20.0, "activations": 10.0, "first_pay_count": 1.0},
            ]
        )

        anomalies = detect_high_metric_anomalies(frame, "activation_cost")

        self.assertEqual(list(anomalies["high_cost"]["content_id"]), ["v1"])

    def test_build_dashboard_summary_uses_filtered_rows(self):
        frame = pd.DataFrame(
            [
                {"spend": 120.0, "activations": 6.0, "first_pay_count": 3.0},
                {"spend": 80.0, "activations": 4.0, "first_pay_count": 1.0},
            ]
        )

        summary = build_dashboard_summary(frame)

        self.assertAlmostEqual(summary.total_spend, 200.0)
        self.assertAlmostEqual(summary.activations, 10.0)
        self.assertAlmostEqual(summary.activation_cost, 20.0)
        self.assertAlmostEqual(summary.first_pay_count, 4.0)
        self.assertAlmostEqual(summary.first_pay_cost, 50.0)
        self.assertAlmostEqual(summary.first_pay_rate, 0.4)

    def test_localized_sorted_columns_keep_important_chinese_fields_first(self):
        frame = pd.DataFrame(
            columns=[
                "source_file",
                "review_reasons",
                "first_pay_cost",
                "title",
                "channel",
                "spend",
                "category_l2",
                "category_source",
            ]
        )

        result = localize_and_sort_columns(frame)

        self.assertEqual(
            list(result.columns)[:6],
            ["渠道", "标题", "消耗", "首次付费成本", "二级栏目", "分类来源"],
        )

    def test_summarize_content_types_counts_rows_unique_content_and_missing_share(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "primary_category": "",
                    "content_category": "股友说",
                    "content_id": "dy-1",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "clicks": 100.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                },
                {
                    "platform": "抖音",
                    "primary_category": "",
                    "content_category": "股友说",
                    "content_id": "dy-1",
                    "spend": 50.0,
                    "impressions": 500.0,
                    "clicks": 25.0,
                    "activations": 5.0,
                    "first_pay_count": 1.0,
                },
                {
                    "platform": "小红书",
                    "primary_category": "",
                    "content_category": "",
                    "content_id": "note-1",
                    "spend": 25.0,
                    "impressions": 200.0,
                    "clicks": 10.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
            ]
        )

        result = summarize_content_types(frame)
        row = result[result["content_category"].eq("股友说")].iloc[0]
        missing = result[result["category_display"].eq("未匹配")].iloc[0]

        self.assertEqual(row["item_count"], 2)
        self.assertEqual(row["unique_content_count"], 1)
        self.assertAlmostEqual(row["spend"], 150.0)
        self.assertAlmostEqual(row["activation_cost"], 10.0)
        self.assertAlmostEqual(row["first_pay_rate"], 3.0 / 15.0)
        self.assertAlmostEqual(missing["missing_spend_share"], 25.0 / 175.0)

    def test_summarize_unique_content_aggregates_duplicate_video_rows(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "material_id": "mat-a",
                    "title": "同一个视频",
                    "primary_category": "",
                    "content_category": "股友说",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "clicks": 100.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                },
                {
                    "platform": "抖音",
                    "channel": "抖音市场部",
                    "content_id": "dy-1",
                    "material_id": "mat-b",
                    "title": "同一个视频",
                    "primary_category": "",
                    "content_category": "股友说",
                    "spend": 40.0,
                    "impressions": 500.0,
                    "clicks": 50.0,
                    "activations": 5.0,
                    "first_pay_count": 1.0,
                },
            ]
        )

        result = summarize_unique_content(frame)
        row = result.iloc[0]

        self.assertEqual(len(result), 1)
        self.assertEqual(row["channel_count"], 2)
        self.assertEqual(row["material_count"], 2)
        self.assertEqual(row["item_count"], 2)
        self.assertAlmostEqual(row["spend"], 140.0)
        self.assertAlmostEqual(row["activation_cost"], 140.0 / 15.0)

    def test_summarize_content_type_trends_filters_timeline_and_groups_by_batch_period(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            _seed_dashboard_db(db_path)
            items = load_all_dashboard_items(db_path)

            result = summarize_content_type_trends(items, "2026-04-05", "2026-04-10")

            self.assertEqual(set(result["batch_id"]), {"batch-old", "batch-new"})
            self.assertIn("trend_period", result.columns)
            self.assertIn("unique_content_count", result.columns)
            old_row = result[result["batch_id"].eq("batch-old") & result["content_category"].eq("股友说")].iloc[0]
            self.assertEqual(old_row["trend_period"], "2026-04-01 至 2026-04-07")
            self.assertAlmostEqual(old_row["spend"], 100.0)

    def test_build_content_recommendations_returns_renderable_markdown(self):
        summary = build_dashboard_summary(pd.DataFrame([{"spend": 100.0, "activations": 10.0, "first_pay_count": 2.0}]))
        platform_summary = pd.DataFrame(
            [
                {"platform": "抖音", "spend": 80.0, "activations": 8.0, "first_pay_count": 2.0, "activation_cost": 10.0},
                {"platform": "B站", "spend": 20.0, "activations": 1.0, "first_pay_count": 0.0, "activation_cost": 20.0},
            ]
        )
        content_type_summary = pd.DataFrame(
            [
                {
                    "category_display": "股友说",
                    "item_count": 3,
                    "spend": 80.0,
                    "activations": 8.0,
                    "first_pay_count": 2.0,
                    "activation_cost": 10.0,
                    "first_pay_rate": 0.25,
                },
                {
                    "category_display": "未匹配",
                    "item_count": 1,
                    "spend": 20.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                    "activation_cost": 20.0,
                    "first_pay_rate": 0.0,
                },
            ]
        )

        markdown = build_content_recommendations(summary, platform_summary, content_type_summary)

        self.assertIn("## 内容题材推荐", markdown)
        self.assertIn("股友说", markdown)
        self.assertIn("- ", markdown)


if __name__ == "__main__":
    unittest.main()
