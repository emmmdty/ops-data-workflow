from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.dashboard import (
    DashboardFilters,
    aggregate_dashboard,
    build_channel_top_topic_insights,
    build_overview_table_rows,
    build_period_comparison_between_batches,
    build_period_comparison_for_batch,
    build_content_recommendations,
    build_dashboard_summary,
    build_top_content_review_queue,
    compare_channel_topics,
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
    summarize_channel_category_comparison,
    summarize_channel_categories,
    summarize_channel_top_topics,
    summarize_channel_top_content_links,
    summarize_dimension_for_metric,
    summarize_period_metric_trends,
    summarize_topics_for_selection,
    summarize_content_type_trends,
    summarize_content_types,
    summarize_unique_content,
)
from ops_data_workflow.reporting import format_display_number, localize_and_sort_columns
from ops_data_workflow.periods import PERIOD_LEVEL_MONTH, PERIOD_LEVEL_WEEK, SOURCE_TYPE_ROLLUP, SOURCE_TYPE_UPLOAD
from ops_data_workflow.storage import init_db, load_manual_recap_report, persist_manual_recap_report, previous_successful_batch_id_for_period


def _append_frame(conn: sqlite3.Connection, table_name: str, batch_id: str, frame: pd.DataFrame) -> None:
    stored = frame.copy()
    stored.insert(0, "batch_id", batch_id)
    stored.to_sql(table_name, conn, if_exists="append", index=False)


def _insert_period_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    period_start: str,
    period_end: str,
    *,
    period_level: str = PERIOD_LEVEL_WEEK,
    period_key: str = "",
    source_type: str = SOURCE_TYPE_UPLOAD,
    created_at: str = "2026-05-13T00:00:00+00:00",
) -> None:
    if not period_key:
        period_key = period_start.replace("-", "") + "-" + period_end.replace("-", "")
    conn.execute(
        """
        insert into upload_batches (
            batch_id, period_start, period_end, created_at, archive_dir,
            output_dir, status, comparison_batch_id, comparison_note,
            period_level, period_key, period_label, data_start, data_end, source_type
        )
        values (?, ?, ?, ?, '', '', 'ok', '', '', ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            period_start,
            period_end,
            created_at,
            period_level,
            period_key,
            f"{period_level}:{period_key}",
            period_start,
            period_end,
            source_type,
        ),
    )


def _append_single_metric_row(
    conn: sqlite3.Connection,
    batch_id: str,
    period_start: str,
    period_end: str,
    spend: float,
) -> None:
    _append_frame(
        conn,
        "canonical_items",
        batch_id,
        pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "period_start": period_start,
                    "period_end": period_end,
                    "content_id": batch_id,
                    "spend": spend,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                }
            ]
        ),
    )


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
    def test_overview_platform_chart_bars_use_actual_metric_values(self):
        from app import _build_platform_chart_figure

        platform_summary = pd.DataFrame(
            [
                {"channel": "抖音商业化", "spend": 120.0, "activations": 12.0},
                {"channel": "小红书商业化", "spend": 80.0, "activations": 10.0},
            ]
        )
        channel_comparison = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend_previous": 60.0,
                    "spend_change_rate": 1.0,
                    "activations_previous": 6.0,
                    "activations_change_rate": 1.0,
                },
                {
                    "channel": "小红书商业化",
                    "spend_previous": 100.0,
                    "spend_change_rate": -0.2,
                    "activations_previous": 20.0,
                    "activations_change_rate": -0.5,
                },
            ]
        )

        fig = _build_platform_chart_figure(platform_summary, channel_comparison)

        self.assertIsNotNone(fig)
        current_spend = next(trace for trace in fig.data if trace.name == "总消耗 本期")
        previous_spend = next(trace for trace in fig.data if trace.name == "总消耗 上期")
        self.assertEqual(list(current_spend.y), [120.0, 80.0])
        self.assertEqual(list(previous_spend.y), [60.0, 100.0])
        self.assertNotIn("渠道内相对指数", str(fig.to_plotly_json()))
        self.assertEqual(fig.layout.yaxis.title.text, "总消耗")
        self.assertEqual(fig.layout.yaxis2.title.text, "环比")

    def test_manual_recap_report_persists_separately_from_upload_reports(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)

            persist_manual_recap_report(
                db_path,
                "batch-1",
                provider="deepseek",
                model="deepseek-chat",
                report={
                    "overview": {"summary": "整体承压", "cause": "行情波动", "action": "补齐图文"},
                    "channels": [{"channel": "抖音商业化", "summary": "达人下架", "cause": "审核", "action": "补授权"}],
                },
            )
            loaded = load_manual_recap_report(db_path, "batch-1")

            self.assertEqual(loaded["provider"], "deepseek")
            self.assertEqual(loaded["report"]["overview"]["summary"], "整体承压")
            self.assertEqual(loaded["report"]["channels"][0]["channel"], "抖音商业化")

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
            self.assertEqual(list(batches["period_label"]), ["周｜2026-04-08 至 2026-04-14", "周｜2026-04-01 至 2026-04-07"])
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

    def test_load_channel_comparison_for_batch_rebuilds_legacy_rows_without_impressions(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                _insert_period_batch(conn, "batch-old", "2026-05-08", "2026-05-14")
                _insert_period_batch(conn, "batch-new", "2026-05-15", "2026-05-21", created_at="2026-05-21T00:00:00+00:00")
                _append_frame(
                    conn,
                    "canonical_items",
                    "batch-old",
                    pd.DataFrame(
                        [
                            {
                                "platform": "抖音",
                                "channel": "抖音商业化",
                                "period_start": "2026-05-08",
                                "period_end": "2026-05-14",
                                "content_id": "old",
                                "spend": 100.0,
                                "impressions": 1000.0,
                                "activations": 10.0,
                                "first_pay_count": 2.0,
                            }
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
                                "period_start": "2026-05-15",
                                "period_end": "2026-05-21",
                                "content_id": "new",
                                "spend": 200.0,
                                "impressions": 2500.0,
                                "activations": 20.0,
                                "first_pay_count": 5.0,
                            }
                        ]
                    ),
                )
                _append_frame(
                    conn,
                    "channel_comparison_items",
                    "batch-new",
                    pd.DataFrame(
                        [
                            {
                                "channel": "总计",
                                "spend_current": 200.0,
                                "spend_previous": 100.0,
                                "spend_change_rate": 1.0,
                                "activations_current": 20.0,
                                "activations_previous": 10.0,
                                "activations_change_rate": 1.0,
                            }
                        ]
                    ),
                )
                conn.commit()

            comparison = load_channel_comparison_for_batch(db_path, "batch-new").set_index("channel")

            self.assertAlmostEqual(comparison.loc["总计", "impressions_current"], 2500.0)
            self.assertAlmostEqual(comparison.loc["总计", "impressions_previous"], 1000.0)
            self.assertAlmostEqual(comparison.loc["总计", "impressions_change_rate"], 1.5)

    def test_build_period_comparison_for_batch_uses_period_order_not_created_at(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = [
                    ("batch-current", "2026-05-15", "2026-05-21", "2026-05-01T00:00:00+00:00"),
                    ("batch-previous", "2026-05-08", "2026-05-14", "2026-05-10T00:00:00+00:00"),
                    ("batch-older", "2026-05-01", "2026-05-07", "2026-05-20T00:00:00+00:00"),
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
                                "period_start": "2026-05-15",
                                "period_end": "2026-05-21",
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
                                "period_start": "2026-05-08",
                                "period_end": "2026-05-14",
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
                                "period_start": "2026-05-01",
                                "period_end": "2026-05-07",
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

    def test_week_comparison_first_week_uses_previous_month_third_week_not_month(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = [
                    ("apr-w1", "2026-04-03", "2026-04-09", PERIOD_LEVEL_WEEK, "20260403-20260409", 10.0),
                    ("apr-w2", "2026-04-10", "2026-04-16", PERIOD_LEVEL_WEEK, "20260410-20260416", 20.0),
                    ("apr-w3", "2026-04-17", "2026-04-23", PERIOD_LEVEL_WEEK, "20260417-20260423", 30.0),
                    ("apr-month", "2026-04-01", "2026-04-30", PERIOD_LEVEL_MONTH, "2026-04", 999.0),
                    ("may-w1", "2026-05-08", "2026-05-14", PERIOD_LEVEL_WEEK, "20260508-20260514", 60.0),
                ]
                for batch_id, start, end, level, key, spend in rows:
                    _insert_period_batch(conn, batch_id, start, end, period_level=level, period_key=key)
                    _append_single_metric_row(conn, batch_id, start, end, spend)
                conn.commit()

            comparison = build_period_comparison_for_batch(db_path, "may-w1").set_index("channel")
            previous_batch_id = previous_successful_batch_id_for_period(
                db_path,
                "2026-05-08",
                PERIOD_LEVEL_WEEK,
                "20260508-20260514",
            )

            self.assertIn("总计", comparison.index)
            self.assertAlmostEqual(comparison.loc["总计", "spend_previous"], 30.0)
            self.assertAlmostEqual(comparison.loc["总计", "spend_change_rate"], 1.0)
            self.assertEqual(previous_batch_id, "apr-w3")

    def test_week_comparison_ignores_duplicate_reimport_batches_when_selecting_previous_month_week(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = [
                    ("apr-w1-old", "2026-04-03", "2026-04-09", "20260403-20260409", 10.0, "2026-05-13T00:00:00+00:00"),
                    ("apr-w1-new", "2026-04-03", "2026-04-09", "20260403-20260409", 11.0, "2026-05-13T01:00:00+00:00"),
                    ("apr-w2", "2026-04-10", "2026-04-16", "20260410-20260416", 20.0, "2026-05-13T02:00:00+00:00"),
                    ("apr-w3", "2026-04-17", "2026-04-23", "20260417-20260423", 30.0, "2026-05-13T03:00:00+00:00"),
                    ("may-w1", "2026-05-08", "2026-05-14", "20260508-20260514", 60.0, "2026-05-13T04:00:00+00:00"),
                ]
                for batch_id, start, end, key, spend, created_at in rows:
                    _insert_period_batch(
                        conn,
                        batch_id,
                        start,
                        end,
                        period_level=PERIOD_LEVEL_WEEK,
                        period_key=key,
                        created_at=created_at,
                    )
                    _append_single_metric_row(conn, batch_id, start, end, spend)
                conn.commit()

            comparison = build_period_comparison_for_batch(db_path, "may-w1").set_index("channel")
            previous_batch_id = previous_successful_batch_id_for_period(
                db_path,
                "2026-05-08",
                PERIOD_LEVEL_WEEK,
                "20260508-20260514",
            )

            self.assertIn("总计", comparison.index)
            self.assertAlmostEqual(comparison.loc["总计", "spend_previous"], 30.0)
            self.assertAlmostEqual(comparison.loc["总计", "spend_change_rate"], 1.0)
            self.assertEqual(previous_batch_id, "apr-w3")

    def test_first_week_without_previous_month_third_week_has_no_comparison(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                _insert_period_batch(
                    conn,
                    "apr-w1",
                    "2026-04-03",
                    "2026-04-09",
                    period_level=PERIOD_LEVEL_WEEK,
                    period_key="20260403-20260409",
                )
                _append_single_metric_row(conn, "apr-w1", "2026-04-03", "2026-04-09", 10.0)
                conn.commit()

            comparison = build_period_comparison_for_batch(db_path, "apr-w1")

            self.assertTrue(comparison.empty)

    def test_month_comparison_uses_previous_month_and_ignores_week_batches(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = [
                    ("mar-month", "2026-03-01", "2026-03-31", PERIOD_LEVEL_MONTH, "2026-03", 100.0),
                    ("apr-week", "2026-04-17", "2026-04-23", PERIOD_LEVEL_WEEK, "20260417-20260423", 900.0),
                    ("apr-month", "2026-04-01", "2026-04-30", PERIOD_LEVEL_MONTH, "2026-04", 200.0),
                    ("may-month", "2026-05-01", "2026-05-31", PERIOD_LEVEL_MONTH, "2026-05", 300.0),
                ]
                for batch_id, start, end, level, key, spend in rows:
                    _insert_period_batch(conn, batch_id, start, end, period_level=level, period_key=key)
                    _append_single_metric_row(conn, batch_id, start, end, spend)
                conn.commit()

            comparison = build_period_comparison_for_batch(db_path, "may-month").set_index("channel")

            self.assertIn("总计", comparison.index)
            self.assertAlmostEqual(comparison.loc["总计", "spend_previous"], 200.0)
            self.assertAlmostEqual(comparison.loc["总计", "spend_change_rate"], 0.5)

    def test_successful_batch_list_keeps_direct_and_rollup_sources_for_same_period(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                _insert_period_batch(
                    conn,
                    "q1-rollup",
                    "2026-01-01",
                    "2026-03-31",
                    period_level="quarter",
                    period_key="2026-Q1",
                    source_type=SOURCE_TYPE_ROLLUP,
                    created_at="2026-04-01T00:00:00+00:00",
                )
                _insert_period_batch(
                    conn,
                    "q1-upload",
                    "2026-01-01",
                    "2026-03-31",
                    period_level="quarter",
                    period_key="2026-Q1",
                    source_type=SOURCE_TYPE_UPLOAD,
                    created_at="2026-04-02T00:00:00+00:00",
                )
                conn.commit()

            batches = list_successful_dashboard_batches(db_path)

            self.assertIn("period_level", batches.columns)
            self.assertIn("period_key", batches.columns)
            self.assertIn("source_type", batches.columns)
            self.assertEqual(batches["batch_id"].tolist(), ["q1-upload", "q1-rollup"])
            self.assertEqual(batches["source_type"].tolist(), [SOURCE_TYPE_UPLOAD, SOURCE_TYPE_ROLLUP])

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

    def test_build_overview_table_rows_places_total_first_and_merges_growth(self):
        items = pd.DataFrame(
            [
                {"channel": "抖音商业化", "spend": 200.0, "impressions": 2000.0, "activations": 20.0, "first_pay_count": 5.0},
                {"channel": "B站", "spend": 50.0, "impressions": 500.0, "activations": 5.0, "first_pay_count": 1.0},
            ]
        )
        summary = build_dashboard_summary(items)
        platform_summary = aggregate_dashboard(items, ["channel"])
        channel_comparison = pd.DataFrame(
            [
                {
                    "channel": "总计",
                    "spend_change_rate": 0.5,
                    "impressions_change_rate": 0.25,
                    "activations_change_rate": -0.2,
                    "activation_cost_change_rate": 0.1,
                    "first_pay_count_change_rate": 0.0,
                    "first_pay_cost_change_rate": -0.3,
                },
                {
                    "channel": "抖音商业化",
                    "spend_change_rate": 1.0,
                    "impressions_change_rate": 0.4,
                    "activations_change_rate": 0.5,
                    "activation_cost_change_rate": -0.25,
                    "first_pay_count_change_rate": 0.25,
                    "first_pay_cost_change_rate": 0.6,
                },
            ]
        )

        rows = build_overview_table_rows(summary, platform_summary, channel_comparison)

        self.assertEqual(
            list(rows.columns),
            [
                "channel",
                "spend",
                "spend_change_rate",
                "impressions",
                "impressions_change_rate",
                "activations",
                "activations_change_rate",
                "activation_cost",
                "activation_cost_change_rate",
                "first_pay_count",
                "first_pay_count_change_rate",
                "first_pay_cost",
                "first_pay_cost_change_rate",
            ],
        )
        self.assertEqual(rows["channel"].tolist(), ["汇总", "抖音商业化", "B站市场部"])
        self.assertAlmostEqual(rows.iloc[0]["spend"], 250.0)
        self.assertAlmostEqual(summary.total_impressions, 2500.0)
        self.assertAlmostEqual(rows.iloc[0]["impressions"], 2500.0)
        self.assertAlmostEqual(rows.iloc[0]["impressions_change_rate"], 0.25)
        self.assertAlmostEqual(rows.iloc[0]["activation_cost"], 10.0)
        self.assertAlmostEqual(rows.iloc[0]["first_pay_cost"], 250.0 / 6.0)
        self.assertAlmostEqual(rows.iloc[0]["spend_change_rate"], 0.5)
        self.assertAlmostEqual(rows.iloc[1]["impressions_change_rate"], 0.4)
        self.assertAlmostEqual(rows.iloc[1]["activation_cost_change_rate"], -0.25)
        self.assertTrue(pd.isna(rows.iloc[2]["spend_change_rate"]))
        self.assertTrue(pd.isna(rows.iloc[2]["impressions_change_rate"]))
        self.assertTrue(pd.isna(rows.iloc[2]["first_pay_cost_change_rate"]))

    def test_build_overview_table_rows_keeps_values_without_comparison(self):
        items = pd.DataFrame(
            [
                {"channel": "小红书商业化", "spend": 120.0, "activations": 10.0, "first_pay_count": 2.0},
            ]
        )
        summary = build_dashboard_summary(items)
        platform_summary = aggregate_dashboard(items, ["channel"])

        rows = build_overview_table_rows(summary, platform_summary, pd.DataFrame())

        self.assertEqual(rows["channel"].tolist(), ["汇总", "小红书商业化"])
        self.assertAlmostEqual(rows.iloc[1]["spend"], 120.0)
        self.assertAlmostEqual(rows.iloc[1]["activation_cost"], 12.0)
        self.assertTrue(rows.filter(like="_change_rate").isna().all().all())

    def test_build_overview_table_rows_sorts_channels_by_business_priority(self):
        items = pd.DataFrame(
            [
                {"channel": "其他渠道", "spend": 400.0, "activations": 40.0, "first_pay_count": 8.0},
                {"channel": "B站", "spend": 300.0, "activations": 30.0, "first_pay_count": 6.0},
                {"channel": "小红书商业化", "spend": 200.0, "activations": 20.0, "first_pay_count": 4.0},
                {"channel": "抖音商业化", "spend": 100.0, "activations": 10.0, "first_pay_count": 2.0},
            ]
        )
        summary = build_dashboard_summary(items)
        platform_summary = pd.DataFrame(
            [
                {"channel": "其他渠道", "spend": 400.0, "activations": 40.0, "activation_cost": 10.0, "first_pay_count": 8.0, "first_pay_cost": 50.0},
                {"channel": "B站", "spend": 300.0, "activations": 30.0, "activation_cost": 10.0, "first_pay_count": 6.0, "first_pay_cost": 50.0},
                {"channel": "小红书商业化", "spend": 200.0, "activations": 20.0, "activation_cost": 10.0, "first_pay_count": 4.0, "first_pay_cost": 50.0},
                {"channel": "抖音商业化", "spend": 100.0, "activations": 10.0, "activation_cost": 10.0, "first_pay_count": 2.0, "first_pay_cost": 50.0},
            ]
        )

        rows = build_overview_table_rows(summary, platform_summary, pd.DataFrame())

        self.assertEqual(rows["channel"].tolist(), ["汇总", "抖音商业化", "小红书商业化", "B站市场部", "其他渠道"])

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

    def test_summarize_channel_categories_returns_all_nonblank_categories(self):
        frame = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "category_l2": f"栏目{i:02d}",
                    "content_id": f"dy-{i}",
                    "spend": float(i),
                    "activations": float(i % 3 + 1),
                    "first_pay_count": float(i % 2),
                }
                for i in range(1, 13)
            ]
            + [
                {"channel": "抖音商业化", "category_l2": "", "content_id": "blank", "spend": 999.0},
                {"channel": "B站", "category_l2": "栏目99", "content_id": "bv-1", "spend": 999.0},
            ]
        )

        result = summarize_channel_categories(frame, "抖音商业化")

        self.assertEqual(len(result), 12)
        self.assertEqual(result.iloc[0]["category_name"], "栏目12")
        self.assertNotIn("", set(result["category_name"]))
        self.assertNotIn("栏目99", set(result["category_name"]))
        self.assertIn("first_pay_rate", result.columns)

    def test_summarize_channel_category_comparison_limits_current_top5_and_adds_previous_spend(self):
        current = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "category_l2": f"栏目{i:02d}",
                    "content_id": f"dy-{i}",
                    "spend": float(i * 10),
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                }
                for i in range(1, 8)
            ]
        )
        previous = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "category_l2": "栏目07",
                    "content_id": "old-7",
                    "spend": 35.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
                {
                    "channel": "抖音商业化",
                    "category_l2": "栏目06",
                    "content_id": "old-6",
                    "spend": 60.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
                {
                    "channel": "B站",
                    "category_l2": "栏目07",
                    "content_id": "bv-7",
                    "spend": 999.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
            ]
        )

        result = summarize_channel_category_comparison(current, previous, "抖音商业化", top_n=5)

        self.assertEqual(result["category_name"].tolist(), ["栏目07", "栏目06", "栏目05", "栏目04", "栏目03"])
        self.assertAlmostEqual(result.iloc[0]["spend"], 70.0)
        self.assertAlmostEqual(result.iloc[0]["spend_previous"], 35.0)
        self.assertAlmostEqual(result.iloc[0]["spend_change_rate"], 1.0)
        self.assertAlmostEqual(result.iloc[1]["spend_change_rate"], 0.0)
        self.assertTrue(pd.isna(result.iloc[2]["spend_previous"]))

    def test_summarize_channel_top_topics_uses_top_20_spend_candidates_and_ai_labels(self):
        frame = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "title": f"标题{i:02d}",
                    "category_l2": "资讯",
                    "category_l3": f"原题材{i:02d}",
                    "content_id": f"dy-{i:02d}",
                    "spend": float(100 - i),
                    "activations": 1.0,
                    "first_pay_count": 1.0 if i < 5 else 0.0,
                }
                for i in range(25)
            ]
            + [
                {
                    "channel": "B站",
                    "title": "其他渠道",
                    "category_l2": "大佬采访",
                    "category_l3": "其他题材",
                    "content_id": "bv-1",
                    "spend": 500.0,
                    "activations": 5.0,
                    "first_pay_count": 1.0,
                }
            ]
        )

        result = summarize_channel_top_topics(
            frame,
            "抖音商业化",
            top_n=20,
            topic_labels={0: "趋势交易", 1: "趋势交易", 2: "芯片行情"},
        )

        self.assertLessEqual(len(result), 20)
        self.assertIn("趋势交易", set(result["topic_name"]))
        trend = result[result["topic_name"].eq("趋势交易")].iloc[0]
        self.assertAlmostEqual(trend["spend"], 199.0)
        self.assertNotIn("原题材20", set(result["topic_name"]))
        self.assertNotIn("其他题材", set(result["topic_name"]))

    def test_summarize_channel_top_topics_falls_back_to_algorithmic_categories_without_ai_labels(self):
        frame = pd.DataFrame(
            [
                {
                    "channel": "小红书商业化",
                    "title": "成王败寇 一念之差",
                    "category_l2": "达人内容",
                    "category_l3": "",
                    "content_id": "note-1",
                    "spend": 50.0,
                    "activations": 5.0,
                    "first_pay_count": 1.0,
                },
                {
                    "channel": "小红书商业化",
                    "title": "达人投流剧情",
                    "category_l2": "达人内容",
                    "category_l3": "很长很像标题的低消耗题材",
                    "content_id": "note-2",
                    "spend": 1.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
            ]
        )

        result = summarize_channel_top_topics(frame, "小红书商业化", top_n=2)

        self.assertEqual(list(result["topic_name"]), ["剧情达人"])
        self.assertAlmostEqual(result.iloc[0]["spend"], 51.0)
        self.assertNotIn("成王败寇 一念之差", set(result["topic_name"]))
        self.assertNotIn("很长很像标题的低消耗题材", set(result["topic_name"]))

    def test_summarize_channel_top_topics_rejects_ai_labels_that_copy_titles(self):
        frame = pd.DataFrame(
            [
                {
                    "channel": "小红书商业化",
                    "title": "成王败寇 一念之差",
                    "category_l2": "达人内容",
                    "category_l3": "",
                    "content_id": "note-1",
                    "spend": 50.0,
                    "activations": 5.0,
                    "first_pay_count": 1.0,
                }
            ]
        )

        result = summarize_channel_top_topics(
            frame,
            "小红书商业化",
            top_n=1,
            topic_labels={0: "成王败寇 一念之差"},
        )

        self.assertEqual(list(result["topic_name"]), ["剧情达人"])
        self.assertNotIn("成王败寇 一念之差", set(result["topic_name"]))

    def test_build_channel_top_topic_insights_summarizes_budget_activation_and_efficiency(self):
        topic_summary = pd.DataFrame(
            [
                {
                    "topic_name": "剧情达人",
                    "spend": 300.0,
                    "activations": 30.0,
                    "activation_cost": 10.0,
                    "first_pay_count": 9.0,
                    "first_pay_rate": 0.3,
                },
                {
                    "topic_name": "财商认知",
                    "spend": 100.0,
                    "activations": 20.0,
                    "activation_cost": 5.0,
                    "first_pay_count": 4.0,
                    "first_pay_rate": 0.2,
                },
            ]
        )

        markdown = build_channel_top_topic_insights(topic_summary)

        self.assertIn("重点题材分析结论", markdown)
        self.assertIn("剧情达人", markdown)
        self.assertIn("财商认知", markdown)
        self.assertIn("预算集中", markdown)
        self.assertIn("拉新", markdown)
        self.assertIn("效率", markdown)

    def test_compare_channel_topics_adds_previous_spend_by_topic_name(self):
        current = pd.DataFrame(
            [
                {"channel": "抖音商业化", "topic_name": "短线交易", "content_types": "股友说", "spend": 120.0, "activations": 12.0},
                {"channel": "抖音商业化", "topic_name": "芯片行情", "content_types": "资讯", "spend": 40.0, "activations": 4.0},
            ]
        )
        previous = pd.DataFrame(
            [
                {"channel": "抖音商业化", "topic_name": "短线交易", "content_types": "股友说", "spend": 80.0, "activations": 8.0},
                {"channel": "抖音商业化", "topic_name": "财商认知", "content_types": "资讯", "spend": 200.0, "activations": 20.0},
            ]
        )

        result = compare_channel_topics(current, previous)

        self.assertEqual(result["topic_name"].tolist(), ["短线交易", "芯片行情"])
        self.assertAlmostEqual(result.iloc[0]["spend_previous"], 80.0)
        self.assertAlmostEqual(result.iloc[0]["spend_change_rate"], 0.5)
        self.assertTrue(pd.isna(result.iloc[1]["spend_previous"]))

    def test_summarize_channel_top_content_links_uses_channel_limits_and_keeps_urls(self):
        frame = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "title": f"抖音标题{i:02d}",
                    "content_id": f"dy-{i:02d}",
                    "cover_url": f"https://img.example/douyin/{i:02d}.jpg",
                    "content_url": f"https://douyin.example/{i:02d}",
                    "spend": float(100 - i),
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                }
                for i in range(25)
            ]
            + [
                {
                    "channel": "小红书商业化",
                    "title": f"小红书标题{i:02d}",
                    "content_id": f"note-{i:02d}",
                    "cover_url": "",
                    "content_url": f"https://xhs.example/{i:02d}",
                    "spend": float(50 - i),
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                }
                for i in range(12)
            ]
            + [
                {
                    "channel": "B站",
                    "title": f"B站标题{i:02d}",
                    "content_id": f"bv-{i:02d}",
                    "cover_url": f"https://img.example/bilibili/{i:02d}.jpg",
                    "content_url": f"https://bilibili.example/{i:02d}",
                    "spend": float(20 - i),
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                }
                for i in range(7)
            ]
            + [
                {
                    "channel": "其他渠道",
                    "title": "其他标题",
                    "content_id": "other-1",
                    "cover_url": "",
                    "content_url": "https://other.example/1",
                    "spend": 999.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                }
            ]
        )

        douyin = summarize_channel_top_content_links(frame, "抖音商业化")
        xhs = summarize_channel_top_content_links(frame, "小红书商业化")
        bilibili = summarize_channel_top_content_links(frame, "B站")
        other = summarize_channel_top_content_links(frame, "其他渠道")

        self.assertEqual(len(douyin), 5)
        self.assertEqual(len(xhs), 5)
        self.assertEqual(len(bilibili), 5)
        self.assertEqual(len(other), 1)
        self.assertEqual(douyin.iloc[0]["title"], "抖音标题00")
        self.assertEqual(douyin.iloc[0]["content_url"], "https://douyin.example/00")
        self.assertEqual(douyin.iloc[0]["cover_url"], "https://img.example/douyin/00.jpg")
        self.assertIn("cover_url", xhs.columns)

    def test_build_top_content_review_queue_uses_period_channel_limits_and_flags_missing_links(self):
        rows = []
        for batch_id in ["upload:week:20260515-20260521", "upload:month:2026-05"]:
            for channel, total in [
                ("抖音商业化", 25),
                ("抖音市场部", 24),
                ("小红书商业化", 12),
                ("小红书市场部", 11),
                ("B站", 7),
                ("其他渠道", 12),
            ]:
                for index in range(total):
                    rows.append(
                        {
                            "batch_id": batch_id,
                            "period_start": "2026-05-15",
                            "period_end": "2026-05-21",
                            "batch_period_start": "2026-05-15",
                            "batch_period_end": "2026-05-21",
                            "platform_group": channel.replace("商业化", "").replace("市场部", ""),
                            "platform": channel,
                            "channel": channel,
                            "title": f"{channel}标题{index:02d}",
                            "content_id": f"{batch_id}:{channel}:{index}",
                            "material_id": "",
                            "content_url": "" if index == 0 else f"https://example.com/{batch_id}/{channel}/{index}",
                            "manual_category": "" if index == 1 else "资讯",
                            "content_category": "" if index == 2 else "资讯",
                            "spend": float(1000 - index),
                            "activations": 1.0,
                            "first_pay_count": 0.0,
                        }
                    )
        queue = build_top_content_review_queue(pd.DataFrame(rows), include_auto_passed=True)

        counts = queue.groupby(["batch_id", "channel"]).size().to_dict()
        for batch_id in ["upload:week:20260515-20260521", "upload:month:2026-05"]:
            self.assertEqual(counts[(batch_id, "抖音商业化")], 20)
            self.assertEqual(counts[(batch_id, "抖音市场部")], 20)
            self.assertEqual(counts[(batch_id, "小红书商业化")], 10)
            self.assertEqual(counts[(batch_id, "小红书市场部")], 10)
            self.assertEqual(counts[(batch_id, "B站市场部")], 5)
            self.assertEqual(counts[(batch_id, "其他渠道")], 10)

        missing_link = queue[queue["title"].eq("抖音商业化标题00")].iloc[0]
        self.assertTrue(bool(missing_link["missing_content_url"]))
        self.assertIn("缺链接", missing_link["audit_flags"])
        self.assertEqual(int(missing_link["rank_in_channel"]), 1)

    def test_build_top_content_review_queue_defaults_to_ai_review_exceptions(self):
        rows = [
            {
                "batch_id": "upload:week:20260515-20260521",
                "batch_period_start": "2026-05-15",
                "batch_period_end": "2026-05-21",
                "platform_group": "抖音",
                "platform": "抖音商业化",
                "channel": "抖音商业化",
                "title": "自动通过素材",
                "content_id": "auto-pass",
                "content_url": "https://example.com/auto",
                "content_category": "资讯",
                "category_l2": "资讯",
                "category_confidence": 0.95,
                "spend": 1000.0,
                "activations": 10.0,
            },
            {
                "batch_id": "upload:week:20260515-20260521",
                "batch_period_start": "2026-05-15",
                "batch_period_end": "2026-05-21",
                "platform_group": "抖音",
                "platform": "抖音商业化",
                "channel": "抖音商业化",
                "title": "低置信素材",
                "content_id": "low-confidence",
                "content_url": "https://example.com/low",
                "content_category": "资讯",
                "category_l2": "资讯",
                "category_confidence": 0.62,
                "spend": 900.0,
                "activations": 9.0,
            },
            {
                "batch_id": "upload:week:20260515-20260521",
                "batch_period_start": "2026-05-15",
                "batch_period_end": "2026-05-21",
                "platform_group": "抖音",
                "platform": "抖音商业化",
                "channel": "抖音商业化",
                "title": "缺链接素材",
                "content_id": "missing-url",
                "content_url": "",
                "content_category": "资讯",
                "category_l2": "资讯",
                "category_confidence": 0.95,
                "spend": 800.0,
                "activations": 8.0,
            },
            {
                "batch_id": "upload:week:20260515-20260521",
                "batch_period_start": "2026-05-15",
                "batch_period_end": "2026-05-21",
                "platform_group": "抖音",
                "platform": "抖音商业化",
                "channel": "抖音商业化",
                "title": "链接格式异常素材",
                "content_id": "invalid-url",
                "content_url": "not-a-url",
                "content_category": "资讯",
                "category_l2": "资讯",
                "category_confidence": 0.95,
                "spend": 700.0,
                "activations": 7.0,
            },
            {
                "batch_id": "upload:week:20260515-20260521",
                "batch_period_start": "2026-05-15",
                "batch_period_end": "2026-05-21",
                "platform_group": "抖音",
                "platform": "抖音商业化",
                "channel": "抖音商业化",
                "title": "类型冲突素材",
                "content_id": "type-risk",
                "content_url": "https://example.com/risk",
                "content_category": "资讯",
                "category_l2": "资讯",
                "category_confidence": 0.95,
                "match_risk_reason": "投稿台账内容类型不一致",
                "spend": 600.0,
                "activations": 6.0,
            },
        ]

        queue = build_top_content_review_queue(pd.DataFrame(rows))
        titles = set(queue["title"])

        self.assertNotIn("自动通过素材", titles)
        self.assertEqual(titles, {"低置信素材", "缺链接素材", "链接格式异常素材", "类型冲突素材"})
        self.assertTrue(queue["needs_review"].astype(bool).all())
        self.assertTrue(queue["ai_review_status"].eq("需人工确认").all())
        self.assertIn("低置信", queue[queue["title"].eq("低置信素材")]["ai_review_reason"].iloc[0])
        self.assertIn("缺链接", queue[queue["title"].eq("缺链接素材")]["audit_flags"].iloc[0])
        self.assertIn("链接格式异常", queue[queue["title"].eq("链接格式异常素材")]["audit_flags"].iloc[0])
        self.assertIn("类型/台账冲突", queue[queue["title"].eq("类型冲突素材")]["audit_flags"].iloc[0])

        all_top = build_top_content_review_queue(pd.DataFrame(rows), include_auto_passed=True)
        passed = all_top[all_top["title"].eq("自动通过素材")].iloc[0]
        self.assertEqual(passed["ai_review_status"], "自动通过")
        self.assertIn("置信度达标", passed["ai_review_reason"])

    def test_summarize_topics_for_selection_keeps_blank_bilibili_category_unmatched(self):
        frame = pd.DataFrame(
            [
                {"channel": "B站", "title": "标题A", "category_l2": "", "category_l3": "题材A", "spend": 100.0, "activations": 10.0, "first_pay_count": 2.0},
                {"channel": "B站", "title": "标题B", "category_l2": "", "category_l3": "题材B", "spend": 20.0, "activations": 2.0, "first_pay_count": 1.0},
                {"channel": "抖音商业化", "title": "标题C", "category_l2": "资讯", "category_l3": "热点行情", "spend": 50.0, "activations": 5.0, "first_pay_count": 1.0},
            ]
        )

        result = summarize_topics_for_selection(frame, "B站", None, "spend", top_n=5)

        self.assertEqual(list(result["topic_name"]), ["题材A", "题材B"])
        self.assertEqual(set(result["category_name"]), {"未匹配栏目"})

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
                "impressions",
            ]
        )

        result = localize_and_sort_columns(frame)

        self.assertEqual(
            list(result.columns)[:6],
            ["渠道", "标题", "消耗", "付费成本", "栏目", "分类来源"],
        )
        self.assertIn("曝光量", result.columns)
        self.assertNotIn("展示/曝光量", result.columns)

    def test_localized_sorted_columns_hide_internal_compatibility_fields(self):
        frame = pd.DataFrame(
            {
                "platform": ["抖音"],
                "platform_group": ["抖音"],
                "channel": ["抖音商业化"],
                "account": ["同花顺投资"],
                "account_raw": ["同花顺投资原始"],
                "spend": [100],
            }
        )

        result = localize_and_sort_columns(frame)

        self.assertIn("平台", result.columns)
        self.assertIn("渠道", result.columns)
        self.assertIn("账号", result.columns)
        self.assertNotIn("平台组", result.columns)
        self.assertNotIn("原始账号", result.columns)

    def test_display_numbers_trim_insignificant_trailing_zeroes(self):
        self.assertEqual(format_display_number(67.90), "67.9")
        self.assertEqual(format_display_number(79.0), "79")
        self.assertEqual(format_display_number(1000.0), "1,000")
        self.assertEqual(format_display_number(1000.0, 0), "1,000")
        self.assertEqual(format_display_number(0.16667), "0.17")
        self.assertEqual(format_display_number(0.125), "0.13")

        frame = pd.DataFrame({"channel": ["抖音商业化"], "spend": [67.90], "activation_cost": [79.0], "ctr": [0.16667]})
        result = localize_and_sort_columns(frame)

        self.assertEqual(result.iloc[0]["消耗"], "67.9")
        self.assertEqual(result.iloc[0]["激活成本"], "79")
        self.assertEqual(result.iloc[0]["点击率"], "0.17")

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

    def test_summarize_period_metric_trends_filters_level_limits_recent_and_recomputes_costs(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = [
                    ("week-1", "2026-04-03", "2026-04-09", PERIOD_LEVEL_WEEK, "20260403-20260409", 100.0, 10.0, 2.0),
                    ("week-2", "2026-04-10", "2026-04-16", PERIOD_LEVEL_WEEK, "20260410-20260416", 240.0, 12.0, 3.0),
                    ("week-3", "2026-04-17", "2026-04-23", PERIOD_LEVEL_WEEK, "20260417-20260423", 300.0, 15.0, 5.0),
                    ("month-1", "2026-04-01", "2026-04-30", PERIOD_LEVEL_MONTH, "2026-04", 999.0, 99.0, 9.0),
                ]
                for batch_id, start, end, level, key, spend, activations, first_pay_count in rows:
                    _insert_period_batch(conn, batch_id, start, end, period_level=level, period_key=key)
                    _append_frame(
                        conn,
                        "canonical_items",
                        batch_id,
                        pd.DataFrame(
                            [
                                {
                                    "platform": "抖音",
                                    "channel": "抖音商业化",
                                    "period_start": start,
                                    "period_end": end,
                                    "content_id": batch_id,
                                    "spend": spend,
                                    "impressions": spend * 10,
                                    "clicks": spend,
                                    "activations": activations,
                                    "first_pay_count": first_pay_count,
                                    "activation_cost": 999.0,
                                    "first_pay_cost": 999.0,
                                }
                            ]
                        ),
                    )
                conn.commit()

            items = load_all_dashboard_items(db_path)
            batches = list_successful_dashboard_batches(db_path)

            result = summarize_period_metric_trends(items, batches, PERIOD_LEVEL_WEEK, window_size=2)

            self.assertEqual(result["batch_id"].tolist(), ["week-2", "week-3"])
            self.assertEqual(result["trend_period"].tolist(), ["2026-04-10 至 2026-04-16", "2026-04-17 至 2026-04-23"])
            self.assertAlmostEqual(result.iloc[1]["spend"], 300.0)
            self.assertAlmostEqual(result.iloc[1]["activation_cost"], 20.0)
            self.assertAlmostEqual(result.iloc[1]["first_pay_cost"], 60.0)
            self.assertAlmostEqual(result.iloc[1]["first_pay_rate"], 5.0 / 15.0)

    def test_summarize_period_metric_trends_filters_channels_before_aggregating(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                _insert_period_batch(
                    conn,
                    "week-1",
                    "2026-04-03",
                    "2026-04-09",
                    period_level=PERIOD_LEVEL_WEEK,
                    period_key="20260403-20260409",
                )
                _append_frame(
                    conn,
                    "canonical_items",
                    "week-1",
                    pd.DataFrame(
                        [
                            {
                                "platform": "抖音",
                                "channel": "抖音商业化",
                                "period_start": "2026-04-03",
                                "period_end": "2026-04-09",
                                "content_id": "dy-1",
                                "spend": 100.0,
                                "impressions": 1000.0,
                                "clicks": 100.0,
                                "activations": 10.0,
                                "first_pay_count": 2.0,
                            },
                            {
                                "platform": "B站",
                                "channel": "B站",
                                "period_start": "2026-04-03",
                                "period_end": "2026-04-09",
                                "content_id": "bv-1",
                                "spend": 40.0,
                                "impressions": 400.0,
                                "clicks": 20.0,
                                "activations": 4.0,
                                "first_pay_count": 1.0,
                            },
                        ]
                    ),
                )
                conn.commit()

            items = load_all_dashboard_items(db_path)
            batches = list_successful_dashboard_batches(db_path)

            result = summarize_period_metric_trends(items, batches, PERIOD_LEVEL_WEEK, channels=("B站",))

            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(result.iloc[0]["spend"], 40.0)
            self.assertAlmostEqual(result.iloc[0]["activations"], 4.0)
            self.assertAlmostEqual(result.iloc[0]["activation_cost"], 10.0)

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

    def test_build_content_recommendations_leads_with_overall_and_channel_analysis(self):
        items = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 120.0,
                    "impressions": 2400.0,
                    "activations": 24.0,
                    "first_pay_count": 6.0,
                },
                {
                    "channel": "B站",
                    "spend": 80.0,
                    "impressions": 800.0,
                    "activations": 8.0,
                    "first_pay_count": 1.0,
                },
            ]
        )
        summary = build_dashboard_summary(items)
        platform_summary = aggregate_dashboard(items, ["channel"])
        content_type_summary = pd.DataFrame(
            [
                {
                    "category_display": "股友说",
                    "spend": 100.0,
                    "activations": 20.0,
                    "activation_cost": 5.0,
                    "first_pay_count": 4.0,
                    "first_pay_rate": 0.2,
                },
            ]
        )
        channel_comparison = pd.DataFrame(
            [
                {
                    "channel": "总计",
                    "spend_change_rate": 0.2,
                    "impressions_change_rate": -0.1,
                    "activations_change_rate": 0.5,
                    "activation_cost_change_rate": -0.2,
                    "first_pay_count_change_rate": 0.4,
                    "first_pay_cost_change_rate": -0.15,
                },
                {
                    "channel": "抖音商业化",
                    "spend_change_rate": 0.1,
                    "impressions_change_rate": 0.3,
                    "activations_change_rate": 0.2,
                    "activation_cost_change_rate": -0.08,
                    "first_pay_count_change_rate": 0.5,
                    "first_pay_cost_change_rate": -0.25,
                },
            ]
        )

        markdown = build_content_recommendations(
            summary,
            platform_summary,
            content_type_summary,
            channel_comparison=channel_comparison,
            external_context={
                "summary": "节假日：本周期含劳动节后恢复；行情：上证指数上涨1.2%；政策：证监会发布政策解读。"
            },
        )

        self.assertLess(markdown.index("## 总体分析"), markdown.index("## 分渠道分析"))
        self.assertIn("总曝光", markdown)
        self.assertIn("抖音商业化", markdown)
        self.assertIn("外部背景", markdown)
        self.assertIn("上证指数上涨1.2%", markdown)


if __name__ == "__main__":
    unittest.main()
