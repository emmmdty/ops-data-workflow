import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch

import pandas as pd

from app import (
    _analysis_jobs_display,
    _asset_cache_jobs_display,
    _asset_cache_records_display,
    _asset_cache_status_summary,
    _batch_caption,
    _batch_option_label,
    _batch_options_for_level,
    _batch_period_value_label,
    _channel_top_link_card_rows,
    _channel_top_link_card_html,
    _channel_totals_table_html,
    _channel_totals_for_display,
    _classification_quality_issues,
    _cached_manifest_from_entry,
    _display_manifests_with_reusable_cache,
    _content_performance_display,
    _evidence_status_summary,
    _evidence_status_table,
    _image_data_uri,
    _image_data_uri_cached,
    _local_content_assets_display,
    _local_recap_metric_html,
    _local_recap_metric_items,
    _metric_delta_text,
    _metric_delta_color,
    _feishu_staleness_needs_confirmation,
    _feishu_staleness_status_lines,
    _sync_feishu_ledger_to_local,
    _metric_row_chunks,
    _overview_metrics,
    _overview_status_metrics,
    _overview_items_for_batch,
    _overview_period_level_options,
    _comparison_caption,
    _render_channel_totals_table,
    _report_pool_with_manual_supplements,
    _report_section_view_model,
    _report_status_copy,
    _rollup_components_display,
    _render_metric_row,
    _recap_tier_analysis_purpose,
    _recap_tier_status_summary,
    _run_recap_tier_pipeline,
    _run_upload_cleaning,
    _split_type_recap_tables,
    _show_frame,
    _sync_inferred_upload_period,
    _top_asset_cache_entries_display,
    _top_pool_with_value,
    _quality_items_with_manual_supplements,
    _trend_display_frame,
)
from ops_data_workflow.periods import PERIOD_LEVEL_MONTH, PERIOD_LEVEL_QUARTER, PERIOD_LEVEL_WEEK, PERIOD_LEVEL_YEAR
from ops_data_workflow.reporting import localize_columns
from ops_data_workflow.top_asset_service import RECAP_TIER_1_SPEND_TOP


class AppOverviewTests(unittest.TestCase):
    def test_sync_inferred_upload_period_sets_new_upload_dates_once(self):
        session_state = {}
        uploads = [
            type("Upload", (), {"name": "20260515-20260521/B站.xlsx"})(),
            type("Upload", (), {"name": "20260515-20260521/抖音商业化.xlsx"})(),
        ]

        _sync_inferred_upload_period(
            uploads,
            (date(2026, 5, 15), date(2026, 5, 21)),
            session_state=session_state,
        )

        self.assertEqual(session_state["upload_data_start_year"], 2026)
        self.assertEqual(session_state["upload_data_start_month"], 5)
        self.assertEqual(session_state["upload_data_start_day"], 15)
        self.assertEqual(session_state["upload_data_end_year"], 2026)
        self.assertEqual(session_state["upload_data_end_month"], 5)
        self.assertEqual(session_state["upload_data_end_day"], 21)

        session_state["upload_data_start_day"] = 16
        _sync_inferred_upload_period(
            uploads,
            (date(2026, 5, 15), date(2026, 5, 21)),
            session_state=session_state,
        )
        self.assertEqual(session_state["upload_data_start_day"], 16)

        _sync_inferred_upload_period(
            [type("Upload", (), {"name": "20260605-20260611/B站.xlsx"})()],
            (date(2026, 6, 5), date(2026, 6, 11)),
            session_state=session_state,
        )
        self.assertEqual(session_state["upload_data_start_month"], 6)
        self.assertEqual(session_state["upload_data_start_day"], 5)
        self.assertEqual(session_state["upload_data_end_day"], 11)

    def test_feishu_staleness_helpers_require_confirmation_for_stale_channels(self):
        staleness = {
            "needs_check": True,
            "items": [
                {
                    "platform": "抖音",
                    "latest_published_date": "2026-06-20",
                    "days_since_latest": 3,
                    "needs_check": False,
                    "status": "fresh",
                    "message": "抖音最新投稿时间 2026-06-20，距今天 3 天。",
                },
                {
                    "platform": "小红书",
                    "latest_published_date": "2026-06-18",
                    "days_since_latest": 5,
                    "needs_check": True,
                    "status": "stale",
                    "message": "小红书最新投稿时间 2026-06-18，已 5 天未更新，请检查飞书台账。",
                },
            ],
        }

        self.assertTrue(_feishu_staleness_needs_confirmation(staleness))
        self.assertEqual(
            _feishu_staleness_status_lines(staleness),
            [
                "抖音：2026-06-20，距今天 3 天",
                "小红书：2026-06-18，距今天 5 天，需要检查",
            ],
        )
        self.assertFalse(_feishu_staleness_needs_confirmation({"needs_check": False, "items": []}))

    def test_sync_feishu_ledger_to_local_blocks_before_write_when_staleness_is_unconfirmed(self):
        ledger = pd.DataFrame([{"platform": "小红书", "content_id": "note-1", "title": "标题"}])
        ledger.attrs["feishu_staleness"] = {
            "needs_check": True,
            "needs_check_platforms": ["小红书"],
            "items": [
                {
                    "platform": "小红书",
                    "latest_published_date": "2026-06-18",
                    "days_since_latest": 5,
                    "needs_check": True,
                    "status": "stale",
                    "message": "小红书最新投稿时间 2026-06-18，已 5 天未更新，请检查飞书台账。",
                }
            ],
        }
        ledger.attrs["feishu_snapshot"] = {"enabled": True, "staleness": ledger.attrs["feishu_staleness"]}

        class Status:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def write(self, message):
                pass

            def update(self, **kwargs):
                pass

        with patch("app.copy_missing_runtime_env"), patch("app.st.status", return_value=Status()), patch(
            "app.load_feishu_content_ledger", return_value=ledger
        ), patch("app.upsert_content_assets_from_feishu") as upsert, patch("app.build_feishu_content_asset_diff") as diff, patch(
            "app.persist_feishu_ledger_snapshot"
        ) as persist, patch("app.st.warning") as warning:
            _sync_feishu_ledger_to_local("manual:feishu", confirm_stale=False)

        upsert.assert_not_called()
        diff.assert_not_called()
        persist.assert_not_called()
        self.assertIn("需要人工确认", warning.call_args.args[0])

    def test_sync_feishu_ledger_to_local_writes_after_staleness_confirmation(self):
        ledger = pd.DataFrame([{"platform": "小红书", "content_id": "note-1", "title": "标题"}])
        ledger.attrs["feishu_staleness"] = {"needs_check": True, "items": []}
        ledger.attrs["feishu_snapshot"] = {"enabled": True, "staleness": ledger.attrs["feishu_staleness"]}
        diff_result = Mock(summary={"新增": 1, "修改": 0, "无变化": 0})

        class Status:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def write(self, message):
                pass

            def update(self, **kwargs):
                pass

        with patch("app.copy_missing_runtime_env"), patch("app.st.status", return_value=Status()), patch(
            "app.build_feishu_content_asset_diff", return_value=diff_result
        ) as diff, patch("app.upsert_content_assets_from_feishu", return_value=1) as upsert, patch(
            "app.persist_feishu_ledger_snapshot"
        ) as persist, patch("app.st.success"):
            _sync_feishu_ledger_to_local("manual:feishu", ledger=ledger, confirm_stale=True)

        diff.assert_called_once()
        upsert.assert_called_once()
        persist.assert_called_once_with(ANY, "manual:feishu", ledger.attrs["feishu_snapshot"])

    def test_run_upload_cleaning_passes_preloaded_feishu_ledger_to_workflow(self):
        ledger = pd.DataFrame([{"platform": "抖音"}])
        result = Mock(batch_id="batch-1", canonical=pd.DataFrame(), core_recap_xlsx=None, feishu_staleness={})

        class Status:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def write(self, message):
                pass

            def update(self, **kwargs):
                pass

        period = Mock(
            period_start="2026-06-01",
            period_end="2026-06-07",
            period_level="week",
            period_key="20260601-20260607",
            period_label="2026-06-01 至 2026-06-07",
            data_start="2026-06-01",
            data_end="2026-06-07",
            source_type="upload",
        )
        materialized = Mock(raw_dir=Path("/tmp/raw"))
        with patch("app.copy_missing_runtime_env"), patch("app.st.status", return_value=Status()), patch(
            "app.materialize_uploaded_files", return_value=materialized
        ), patch("app.run_archived_workflow", return_value=result) as workflow, patch("app.st.success"):
            _run_upload_cleaning([], Path("/tmp/target"), period, False, feishu_ledger=ledger)

        self.assertIs(workflow.call_args.kwargs["preloaded_feishu_ledger"], ledger)

    def test_overview_metrics_do_not_double_count_summary_total_row(self):
        totals = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "小红书商业化",
                    "spend": 50.0,
                    "impressions": 500.0,
                    "activations": 5.0,
                    "first_pay_count": 1.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "总计",
                    "spend": 150.0,
                    "impressions": 1500.0,
                    "activations": 15.0,
                    "first_pay_count": 3.0,
                    "is_channel_total": True,
                },
            ]
        )

        metrics = _overview_metrics(totals, pd.DataFrame())
        channels = _channel_totals_for_display(totals, pd.DataFrame())

        self.assertEqual(metrics["消耗"], 150.0)
        self.assertEqual(metrics["曝光"], 1500.0)
        self.assertEqual(metrics["激活数"], 15.0)
        self.assertEqual(metrics["付费数"], 3.0)
        self.assertEqual(set(channels["channel"]), {"抖音商业化", "小红书商业化"})

    def test_overview_metrics_and_channel_totals_include_weighted_value(self):
        totals = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "总计",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                },
            ]
        )

        metrics = _overview_metrics(totals, pd.DataFrame(), activation_weight=2.0, first_pay_weight=10.0)
        channels = _channel_totals_for_display(
            totals,
            pd.DataFrame(),
            activation_weight=2.0,
            first_pay_weight=10.0,
        )

        self.assertEqual(metrics["价值"], 40.0)
        self.assertEqual(float(channels.iloc[0]["value"]), 40.0)

    def test_overview_merges_partial_channel_totals_with_detail_channels(self):
        totals = pd.DataFrame(
            [
                {
                    "channel": "小红书商业化",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                }
            ]
        )
        items = pd.DataFrame(
            [
                {
                    "channel": "小红书商业化",
                    "spend": 70.0,
                    "impressions": 700.0,
                    "activations": 7.0,
                    "first_pay_count": 1.0,
                },
                {
                    "channel": "抖音商业化",
                    "spend": 200.0,
                    "impressions": 2000.0,
                    "activations": 20.0,
                    "first_pay_count": 4.0,
                },
                {
                    "channel": "B站市场部",
                    "spend": 30.0,
                    "impressions": 0.0,
                    "activations": 3.0,
                    "first_pay_count": 1.0,
                },
            ]
        )

        metrics = _overview_metrics(totals, items)
        channels = _channel_totals_for_display(totals, items)

        by_channel = channels.set_index("channel")
        self.assertEqual(set(by_channel.index), {"小红书商业化", "抖音商业化", "B站市场部"})
        self.assertEqual(float(by_channel.loc["小红书商业化", "spend"]), 100.0)
        self.assertEqual(float(by_channel.loc["抖音商业化", "spend"]), 200.0)
        self.assertEqual(float(by_channel.loc["B站市场部", "spend"]), 30.0)
        self.assertEqual(metrics["消耗"], 330.0)
        self.assertEqual(metrics["曝光"], 3000.0)
        self.assertEqual(metrics["激活数"], 33.0)
        self.assertEqual(metrics["付费数"], 7.0)

    def test_channel_totals_display_attaches_previous_period_delta_metadata(self):
        totals = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 120.0,
                    "impressions": 800.0,
                    "activations": 9.0,
                    "first_pay_count": 4.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "小红书商业化",
                    "spend": 100.0,
                    "impressions": 600.0,
                    "activations": 5.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "B站市场部",
                    "spend": 50.0,
                    "impressions": 400.0,
                    "activations": 4.0,
                    "first_pay_count": 0.0,
                    "is_channel_total": True,
                },
            ]
        )
        previous_totals = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "小红书商业化",
                    "spend": 100.0,
                    "impressions": 0.0,
                    "activations": 5.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                },
            ]
        )

        channels = _channel_totals_for_display(
            totals,
            pd.DataFrame(),
            previous_totals=previous_totals,
            previous_items=pd.DataFrame(),
        )

        deltas = channels.attrs["metric_deltas"]
        self.assertEqual(deltas["抖音商业化"]["spend"]["text"], "+20.0%")
        self.assertEqual(deltas["抖音商业化"]["spend"]["class"], "channel-delta-good")
        self.assertEqual(deltas["抖音商业化"]["impressions"]["text"], "-20.0%")
        self.assertEqual(deltas["抖音商业化"]["impressions"]["class"], "channel-delta-bad")
        self.assertEqual(deltas["抖音商业化"]["first_pay_count"]["text"], "+100.0%")
        self.assertEqual(deltas["抖音商业化"]["first_pay_count"]["class"], "channel-delta-good")
        self.assertEqual(deltas["抖音商业化"]["activation_cost"]["text"], "+33.3%")
        self.assertEqual(deltas["抖音商业化"]["activation_cost"]["class"], "channel-delta-bad")
        self.assertEqual(deltas["抖音商业化"]["first_pay_cost"]["text"], "-40.0%")
        self.assertEqual(deltas["抖音商业化"]["first_pay_cost"]["class"], "channel-delta-good")
        self.assertEqual(deltas["抖音商业化"]["value"]["text"], "+8.3%")
        self.assertEqual(deltas["抖音商业化"]["value"]["class"], "channel-delta-good")
        self.assertNotIn("spend", deltas["小红书商业化"])
        self.assertNotIn("impressions", deltas["小红书商业化"])
        self.assertNotIn("B站市场部", deltas)

    def test_channel_totals_table_html_colors_only_delta_brackets(self):
        channels = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 120.0,
                    "impressions": 800.0,
                    "activations": 9.0,
                    "first_pay_count": 3.0,
                    "activation_cost": 13.3333,
                    "first_pay_cost": 40.0,
                    "value": 12.0,
                }
            ]
        )
        channels.attrs["metric_deltas"] = {
            "抖音商业化": {
                "spend": {"text": "+20.0%", "class": "channel-delta-good"},
                "impressions": {"text": "-20.0%", "class": "channel-delta-bad"},
                "activation_cost": {"text": "+33.3%", "class": "channel-delta-bad"},
                "value": {"text": "+18.2%", "class": "channel-delta-good"},
            }
        }

        html = _channel_totals_table_html(channels)

        self.assertIn('<span class="channel-value">120</span><span class="channel-delta channel-delta-good">（+20.0%）</span>', html)
        self.assertIn('<span class="channel-value">800</span><span class="channel-delta channel-delta-bad">（-20.0%）</span>', html)
        self.assertIn('<span class="channel-value">13.33</span><span class="channel-delta channel-delta-bad">（+33.3%）</span>', html)
        self.assertIn('<span class="channel-value">12</span><span class="channel-delta channel-delta-good">（+18.2%）</span>', html)

    def test_channel_totals_display_excludes_blank_channel_rows(self):
        totals = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "",
                    "spend": 30.0,
                    "impressions": 300.0,
                    "activations": 3.0,
                    "first_pay_count": 1.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "   ",
                    "spend": 20.0,
                    "impressions": 200.0,
                    "activations": 2.0,
                    "first_pay_count": 1.0,
                    "is_channel_total": True,
                },
                {
                    "channel": None,
                    "spend": 10.0,
                    "impressions": 100.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "小红书商业化",
                    "spend": 50.0,
                    "impressions": 500.0,
                    "activations": 5.0,
                    "first_pay_count": 1.0,
                    "is_channel_total": True,
                },
                {
                    "channel": "总计",
                    "spend": 210.0,
                    "impressions": 2100.0,
                    "activations": 21.0,
                    "first_pay_count": 5.0,
                    "is_channel_total": True,
                },
            ]
        )

        channels = _channel_totals_for_display(totals, pd.DataFrame())

        self.assertEqual(set(channels["channel"]), {"抖音商业化", "小红书商业化"})
        self.assertEqual(len(channels), 2)

    def test_metric_delta_text_formats_period_over_period_change(self):
        self.assertEqual(_metric_delta_text(120, 100), "+20.0%")
        self.assertEqual(_metric_delta_text(90, 100), "-10.0%")
        self.assertEqual(_metric_delta_text(0, 0), "")
        self.assertEqual(_metric_delta_text(10, 0), "上一周期为 0")
        self.assertEqual(_metric_delta_text(10, None), "暂无上一周期")

    def test_metric_delta_color_uses_business_direction(self):
        self.assertEqual(_metric_delta_color("激活数", 120, 100), "inverse")
        self.assertEqual(_metric_delta_color("付费数", 90, 100), "inverse")
        self.assertEqual(_metric_delta_color("价值", 120, 100), "inverse")
        self.assertEqual(_metric_delta_color("激活成本", 90, 100), "normal")
        self.assertEqual(_metric_delta_color("付费成本", 120, 100), "normal")
        self.assertEqual(_metric_delta_color("消耗", 100, 100), "off")
        self.assertEqual(_metric_delta_color("曝光", 100, None), "off")

    def test_metric_row_passes_business_delta_color_to_cards(self):
        columns = [Mock(), Mock()]

        with patch("app.st.columns", return_value=columns):
            _render_metric_row({"激活数": 120, "激活成本": 90}, {"激活数": 100, "激活成本": 100})

        self.assertEqual(columns[0].metric.call_args.kwargs["delta_color"], "inverse")
        self.assertEqual(columns[1].metric.call_args.kwargs["delta_color"], "normal")

    def test_metric_row_chunks_wrap_before_cards_become_too_narrow(self):
        metrics = {f"指标{i}": float(i) for i in range(7)}

        chunks = _metric_row_chunks(metrics)

        self.assertEqual([len(chunk) for chunk in chunks], [4, 3])

    def test_overview_items_falls_back_to_canonical_dashboard_rows_for_legacy_batches(self):
        canonical = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                }
            ]
        )

        with patch("app.list_content_performance_items", return_value=pd.DataFrame()) as performance, patch(
            "app.load_dashboard_items_for_batch", return_value=canonical
        ) as canonical_loader:
            result = _overview_items_for_batch("batch-legacy")

        performance.assert_called_once()
        canonical_loader.assert_called_once()
        self.assertEqual(float(result["spend"].sum()), 100.0)

    def test_overview_status_metrics_count_channels_from_detail_rows(self):
        items = pd.DataFrame(
            [
                {"channel": "抖音市场部", "content_id": "dy-1"},
                {"channel": "抖音市场部", "content_id": "dy-2"},
                {"channel": "B站市场部", "content_id": "bv-1"},
                {"channel": "", "content_id": "blank"},
            ]
        )
        totals = pd.DataFrame()
        manifests = pd.DataFrame([{"status": "succeeded"}])
        recap_items = pd.DataFrame()

        metrics = _overview_status_metrics(items, totals, manifests, recap_items)

        self.assertEqual(metrics["素材明细"], 4)
        self.assertEqual(metrics["覆盖渠道"], 2)
        self.assertNotIn("渠道总数据", metrics)

    def test_overview_period_selector_groups_batches_and_uses_compact_labels(self):
        batches = pd.DataFrame(
            [
                {
                    "batch_id": "week-2",
                    "period_level": PERIOD_LEVEL_WEEK,
                    "period_key": "20260605-20260611",
                    "period_start": "2026-06-05",
                    "period_end": "2026-06-11",
                    "period_label": "周｜2026-06-05 至 2026-06-11",
                    "created_at": "2026-06-15T00:00:00+00:00",
                    "source_type": "upload",
                },
                {
                    "batch_id": "month-5",
                    "period_level": PERIOD_LEVEL_MONTH,
                    "period_key": "2026-05",
                    "period_start": "2026-05-01",
                    "period_end": "2026-05-31",
                    "period_label": "月｜2026年05月",
                    "created_at": "2026-06-14T00:00:00+00:00",
                    "source_type": "upload",
                },
                {
                    "batch_id": "quarter-1",
                    "period_level": PERIOD_LEVEL_QUARTER,
                    "period_key": "2026-Q1",
                    "period_start": "2026-01-01",
                    "period_end": "2026-03-31",
                    "period_label": "季度｜2026年第1季度",
                    "created_at": "2026-06-13T00:00:00+00:00",
                    "source_type": "rollup",
                },
                {
                    "batch_id": "year-2026",
                    "period_level": PERIOD_LEVEL_YEAR,
                    "period_key": "2026",
                    "period_start": "2026-01-01",
                    "period_end": "2026-12-31",
                    "period_label": "年度｜2026年",
                    "created_at": "2026-06-12T00:00:00+00:00",
                    "source_type": "rollup",
                },
            ]
        )

        self.assertEqual(_overview_period_level_options(batches), ["周", "月", "季度", "年度"])
        self.assertEqual(_batch_options_for_level(batches, PERIOD_LEVEL_WEEK)[0][0], "20260605-20260611")
        self.assertEqual(_batch_options_for_level(batches, PERIOD_LEVEL_MONTH)[0][0], "202605")
        self.assertEqual(_batch_options_for_level(batches, PERIOD_LEVEL_QUARTER)[0][0], "2026Q1")
        self.assertEqual(_batch_options_for_level(batches, PERIOD_LEVEL_YEAR)[0][0], "2026")

    def test_period_level_options_always_show_all_supported_levels(self):
        batches = pd.DataFrame(
            [
                {"batch_id": "week-2", "period_level": PERIOD_LEVEL_WEEK},
                {"batch_id": "month-5", "period_level": PERIOD_LEVEL_MONTH},
            ]
        )

        self.assertEqual(_overview_period_level_options(batches), ["周", "月", "季度", "年度"])

    def test_batch_option_label_uses_compact_period_format(self):
        self.assertEqual(
            _batch_period_value_label(
                pd.Series(
                    {
                        "period_level": PERIOD_LEVEL_WEEK,
                        "period_key": "20260605-20260611",
                        "period_start": "2026-06-05",
                        "period_end": "2026-06-11",
                    }
                )
            ),
            "20260605-20260611",
        )
        self.assertEqual(_batch_period_value_label(pd.Series({"period_level": PERIOD_LEVEL_MONTH, "period_key": "2026-05"})), "202605")
        self.assertEqual(_batch_period_value_label(pd.Series({"period_level": PERIOD_LEVEL_QUARTER, "period_key": "2026-Q1"})), "2026Q1")
        self.assertEqual(_batch_period_value_label(pd.Series({"period_level": PERIOD_LEVEL_YEAR, "period_key": "2026"})), "2026")
        self.assertEqual(
            _batch_option_label(pd.Series({"period_level": PERIOD_LEVEL_WEEK, "period_key": "20260605-20260611"})),
            "20260605-20260611",
        )

    def test_batch_caption_uses_compact_period_without_duplicate_level(self):
        caption = _batch_caption(
            {
                "period_level": PERIOD_LEVEL_WEEK,
                "period_key": "20260605-20260611",
                "period_label": "周｜2026-06-05 至 2026-06-11",
                "period_start": "2026-06-05",
                "period_end": "2026-06-11",
                "data_start": "2026-06-05",
                "data_end": "2026-06-11",
                "source_type": "upload",
            }
        )

        self.assertEqual(caption, "20260605-20260611｜上传")

    def test_comparison_caption_names_current_and_previous_periods(self):
        current = {
            "period_level": PERIOD_LEVEL_WEEK,
            "period_key": "20260605-20260611",
            "period_start": "2026-06-05",
            "period_end": "2026-06-11",
        }
        previous = {
            "period_level": PERIOD_LEVEL_WEEK,
            "period_key": "20260515-20260521",
            "period_start": "2026-05-15",
            "period_end": "2026-05-21",
        }

        self.assertEqual(
            _comparison_caption(current, previous),
            "环比：本周期 20260605-20260611，对比周期 20260515-20260521。",
        )

    def test_rollup_component_display_uses_period_labels_not_internal_batch_ids(self):
        records = {
            "batch-week": {
                "period_level": PERIOD_LEVEL_WEEK,
                "period_key": "20260605-20260611",
                "period_start": "2026-06-05",
                "period_end": "2026-06-11",
            },
            "batch-month": {
                "period_level": PERIOD_LEVEL_MONTH,
                "period_key": "2026-05",
                "period_start": "2026-05-01",
                "period_end": "2026-05-31",
            },
        }

        with patch("app.read_batch_record", side_effect=lambda _db, batch_id: records.get(batch_id, {})):
            display = _rollup_components_display(["batch-week", "batch-month", "missing-in-db"])

        self.assertEqual(list(display.columns), ["周期"])
        self.assertEqual(display["周期"].tolist(), ["20260605-20260611", "202605", "可用周期 3"])
        self.assertNotIn("batch-week", "\n".join(display["周期"].tolist()))

    def test_trend_display_frame_hides_internal_period_fields(self):
        trend = pd.DataFrame(
            [
                {
                    "trend_period": "20260605-20260611",
                    "period_level": PERIOD_LEVEL_WEEK,
                    "period_key": "20260605-20260611",
                    "period_label": "周｜2026-06-05 至 2026-06-11",
                    "batch_id": "internal-batch",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 2.0,
                    "activation_cost": 10.0,
                    "first_pay_cost": 50.0,
                }
            ]
        )

        display = _trend_display_frame(trend)

        self.assertEqual(
            list(display.columns),
            ["trend_period", "spend", "impressions", "activations", "first_pay_count", "activation_cost", "first_pay_cost"],
        )
        for internal_column in ["period_level", "period_key", "period_label", "batch_id"]:
            self.assertNotIn(internal_column, display.columns)

    def test_top_pool_value_and_channel_top_link_cards(self):
        top_pool = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "抖音第一",
                    "account": "账号A",
                    "content_url": "https://example.com/dy-1",
                    "spend": 300.0,
                    "impressions": 3000.0,
                    "activations": 3.0,
                    "first_pay_count": 1.0,
                },
                {
                    "channel": "抖音商业化",
                    "content_id": "dy-2",
                    "title": "抖音第二",
                    "account": "账号B",
                    "content_url": "",
                    "spend": 200.0,
                    "impressions": 2000.0,
                    "activations": 2.0,
                    "first_pay_count": 0.0,
                },
                {
                    "channel": "抖音商业化",
                    "content_id": "dy-3",
                    "title": "抖音第三",
                    "account": "账号C",
                    "content_url": "https://example.com/dy-3",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
                {
                    "channel": "B站商业化",
                    "content_id": "BV1",
                    "title": "B站第一",
                    "account": "账号D",
                    "content_url": "https://example.com/bv1",
                    "spend": 400.0,
                    "impressions": 4000.0,
                    "activations": 4.0,
                    "first_pay_count": 2.0,
                },
            ]
        )

        valued = _top_pool_with_value(top_pool, activation_weight=2.0, first_pay_weight=10.0)
        cards = _channel_top_link_card_rows(valued, limit=2)

        self.assertEqual(float(valued.loc[valued["content_id"].eq("BV1"), "value"].iloc[0]), 28.0)
        self.assertEqual(cards["channel"].value_counts().to_dict(), {"抖音商业化": 2, "B站商业化": 1})
        self.assertEqual(cards.iloc[0]["content_id"], "BV1")
        self.assertEqual(cards[cards["channel"].eq("抖音商业化")].iloc[0]["content_id"], "dy-1")

    def test_channel_top_link_card_prefers_douyin_work_id_for_link_and_cover(self):
        row = pd.Series(
            {
                "platform": "抖音",
                "channel": "抖音商业化",
                "content_id": "wrong-material-id",
                "work_id": "real-video-id",
                "work_url": "https://www.douyin.com/video/real-video-id",
                "material_id": "wrong-material-id",
                "asset_key": "抖音::id::wrong-material-id",
                "title": "投放素材",
                "account": "投资号",
                "content_url": "https://www.douyin.com/video/wrong-material-id",
                "spend": 100.0,
                "impressions": 1000.0,
                "activations": 10.0,
                "first_pay_count": 2.0,
            }
        )
        cover_lookup = {
            "抖音::id::real-video-id": "/tmp/real-cover.jpg",
        }

        def fake_image_data_uri(path: str) -> str:
            return "data:image/jpeg;base64,cover" if path == "/tmp/real-cover.jpg" else ""

        with patch("app._image_data_uri", side_effect=fake_image_data_uri):
            html = _channel_top_link_card_html(row, cover_lookup, rank=1)

        self.assertIn("<img", html)
        self.assertNotIn("暂无封面", html)
        self.assertIn('href="https://www.douyin.com/video/real-video-id"', html)
        self.assertNotIn('href="https://www.douyin.com/video/wrong-material-id"', html)

    def test_channel_top_link_card_does_not_open_douyin_material_id_as_video(self):
        row = pd.Series(
            {
                "platform": "抖音",
                "channel": "抖音商业化",
                "content_id": "7623721481431780662",
                "material_id": "7623721481431780662",
                "asset_key": "抖音::material::7623721481431780662",
                "title": "巨量素材",
                "account": "投资号",
                "content_url": "",
                "work_id": "",
                "work_url": "",
                "spend": 100.0,
                "impressions": 1000.0,
                "activations": 10.0,
                "first_pay_count": 2.0,
            }
        )
        cover_lookup = {
            "抖音::id::7623721481431780662": "/tmp/wrong-cover.jpg",
        }

        with patch("app._image_data_uri", return_value="data:image/jpeg;base64,cover"):
            html = _channel_top_link_card_html(row, cover_lookup, rank=1)

        self.assertNotIn("<img", html)
        self.assertIn("暂无封面", html)
        self.assertIn("暂无可打开链接", html)
        self.assertNotIn("https://www.douyin.com/video/7623721481431780662", html)

    def test_channel_top_link_card_uses_douyin_giant_asset_url_as_evidence_link(self):
        row = pd.Series(
            {
                "platform": "抖音",
                "channel": "抖音商业化",
                "content_id": "",
                "material_id": "7623721481431780662",
                "asset_key": "抖音::material::7623721481431780662",
                "title": "巨量素材",
                "account": "投资号",
                "content_url": "",
                "work_id": "",
                "work_url": "",
                "ad_material_url": "https://巨量.example/video.mp4",
                "ad_cover_url": "https://巨量.example/cover.jpg",
                "spend": 100.0,
                "impressions": 1000.0,
                "activations": 10.0,
                "first_pay_count": 2.0,
            }
        )

        html = _channel_top_link_card_html(row, {}, rank=1)

        self.assertIn('href="https://巨量.example/video.mp4"', html)
        self.assertIn("打开巨量证据", html)

    def test_image_data_uri_reuses_cached_file_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "cover.jpg"
            image_path.write_bytes(b"fake image bytes")
            _image_data_uri_cached.clear()
            original_read_bytes = Path.read_bytes

            def counted_read_bytes(path: Path) -> bytes:
                return original_read_bytes(path)

            with patch.object(Path, "read_bytes", autospec=True, side_effect=counted_read_bytes) as read_bytes:
                first = _image_data_uri(str(image_path))
                second = _image_data_uri(str(image_path))

        self.assertEqual(first, second)
        self.assertEqual(read_bytes.call_count, 1)

    def test_display_manifests_adds_reusable_cache_cover_without_current_batch_manifest(self):
        top_pool = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "BV1same",
                    "asset_key": "",
                    "title": "历史缓存素材",
                    "spend": 100.0,
                }
            ]
        )
        current_manifests = pd.DataFrame(
            columns=["job_id", "batch_id", "status", "platform", "asset_key", "asset_dir", "cover_path"]
        )
        cached_manifests = pd.DataFrame(
            [
                {
                    "job_id": "",
                    "batch_id": "_cache",
                    "status": "succeeded",
                    "platform": "B站",
                    "asset_key": "B站::id::BV1same",
                    "asset_dir": "/tmp/cache/BV1same",
                    "cover_path": "/tmp/cache/BV1same/cover.jpg",
                }
            ]
        )

        display_manifests = _display_manifests_with_reusable_cache(top_pool, current_manifests, cached_manifests)

        self.assertEqual(len(display_manifests), 1)
        self.assertEqual(display_manifests.iloc[0]["asset_key"], "B站::id::BV1same")
        self.assertEqual(display_manifests.iloc[0]["cover_path"], "/tmp/cache/BV1same/cover.jpg")

    def test_cached_manifest_uses_local_image_when_manifest_cover_is_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = Path(tmp)
            cover_path = asset_dir / "images" / "001.webp"
            cover_path.parent.mkdir(parents=True)
            cover_path.write_bytes(b"fake image")
            (asset_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "status": "succeeded",
                                "platform": "抖音",
                                "asset_key": "抖音::id::7634503668024298793",
                                "asset_dir": str(asset_dir),
                                "cover_path": "",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest = _cached_manifest_from_entry(
                pd.Series(
                    {
                        "asset_key": "抖音::id::7634503668024298793",
                        "asset_dir": str(asset_dir),
                        "platform": "抖音",
                    }
                )
            )

        self.assertEqual(manifest["cover_path"], str(cover_path))

    def test_cached_manifest_rejects_douyin_screenshot_only_fallback_as_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = Path(tmp)
            screenshot_path = asset_dir / "screenshots" / "001.jpg"
            screenshot_path.parent.mkdir(parents=True)
            screenshot_path.write_bytes(b"fake browser screenshot")
            (asset_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "status": "succeeded",
                                "platform": "抖音",
                                "asset_key": "抖音::id::7634503668024298793",
                                "asset_dir": str(asset_dir),
                                "cover_path": str(screenshot_path),
                                "video_path": "",
                                "screenshots": [str(screenshot_path)],
                                "frames": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest = _cached_manifest_from_entry(
                pd.Series(
                    {
                        "asset_key": "抖音::id::7634503668024298793",
                        "asset_dir": str(asset_dir),
                        "platform": "抖音",
                    }
                )
            )

        self.assertEqual(manifest, {})

    def test_type_recap_tables_are_split_by_required_platform_type_levels(self):
        type_recap = pd.DataFrame(
            [
                {"platform": "抖音", "type_level": "douyin_l1", "content_type": "图文", "value": 10},
                {"platform": "抖音", "type_level": "douyin_l2", "content_type": "投资知识", "value": 8},
                {"platform": "小红书", "type_level": "xhs_l1", "content_type": "图文", "value": 6},
                {"platform": "小红书", "type_level": "xhs_l2", "content_type": "理财方法", "value": 4},
                {"platform": "B站", "type_level": "bilibili", "content_type": "采访内容", "value": 2},
            ]
        )

        tables = _split_type_recap_tables(type_recap)

        self.assertEqual(
            list(tables),
            ["抖音一级类型", "抖音二级类型", "小红书一级类型", "小红书二级类型", "B站内容类型"],
        )
        self.assertEqual(tables["抖音一级类型"].iloc[0]["抖音一级类型"], "图文")
        self.assertEqual(tables["B站内容类型"].iloc[0]["B站内容类型"], "采访内容")
        self.assertNotIn("content_type", tables["抖音一级类型"].columns)
        self.assertNotIn("content_type", tables["B站内容类型"].columns)
        self.assertNotIn("type_level", tables["抖音一级类型"].columns)
        self.assertNotIn("platform", tables["B站内容类型"].columns)

    def test_main_display_tables_hide_internal_fields_and_localize_columns(self):
        performance = pd.DataFrame(
            [
                {
                    "performance_key": "internal-performance",
                    "asset_key": "internal-asset",
                    "source_rows_json": "[]",
                    "period_start": "2026-05-26",
                    "period_end": "2026-06-04",
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "BV1abcde2345",
                    "title": "真实B站标题",
                    "account": "投资号",
                    "tags": "投教",
                    "bilibili_content_type": "采访内容",
                    "content_url": "https://www.bilibili.com/video/BV1abcde2345/",
                    "spend": 100,
                    "impressions": 1000,
                    "activations": 10,
                    "first_pay_count": 2,
                    "value": 30,
                    "share": 1,
                }
            ]
        )
        assets = pd.DataFrame(
            [
                {
                    "asset_key": "internal-asset",
                    "title_key": "internal-title-key",
                    "raw_result_json": "{}",
                    "platform": "B站",
                    "content_id": "BV1abcde2345",
                    "title": "真实B站标题",
                    "account": "投资号",
                    "bilibili_content_type": "采访内容",
                    "content_url": "https://www.bilibili.com/video/BV1abcde2345/",
                    "updated_at": "2026-06-16T00:00:00+00:00",
                }
            ]
        )
        jobs = pd.DataFrame(
            [
                {
                    "job_id": "job-internal",
                    "batch_id": "batch-1",
                    "job_type": "top_multimodal_content",
                    "status": "succeeded",
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_identity_key": "internal-identity",
                    "title": "真实B站标题",
                    "content_url": "https://www.bilibili.com/video/BV1abcde2345/",
                    "payload_json": "{}",
                    "result_json": "{}",
                    "error_message": "",
                    "attempts": 1,
                    "updated_at": "2026-06-16T00:00:00+00:00",
                }
            ]
        )
        manifests = pd.DataFrame(
            [
                {
                    "job_id": "job-internal",
                    "batch_id": "batch-1",
                    "status": "succeeded",
                    "platform": "B站",
                    "asset_dir": "/tmp/internal",
                    "cover_path": "/tmp/internal/cover.jpg",
                    "video_path": "/tmp/internal/video.mp4",
                    "screenshots_json": "[]",
                    "frames_json": "[]",
                    "metadata_json": "{}",
                    "updated_at": "2026-06-16T00:00:00+00:00",
                }
            ]
        )
        capture_jobs = pd.DataFrame(
            [
                {
                    "job_id": "job-internal",
                    "batch_id": "batch-1",
                    "status": "succeeded",
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "BV1abcde2345",
                    "title": "真实B站标题",
                    "metrics_json": "{}",
                    "harvester_root": "/tmp/harvester",
                    "jobs_path": "/tmp/jobs.jsonl",
                    "manifest_path": "/tmp/manifest.json",
                    "updated_at": "2026-06-16T00:00:00+00:00",
                }
            ]
        )

        performance_display = localize_columns(_content_performance_display(performance))
        assets_display = localize_columns(_local_content_assets_display(assets))
        jobs_display = localize_columns(_analysis_jobs_display(jobs))
        manifests_display = localize_columns(_asset_cache_records_display(manifests))
        capture_jobs_display = localize_columns(_asset_cache_jobs_display(capture_jobs))

        hidden_columns = [
            "performance_key",
            "asset_key",
            "source_rows_json",
            "title_key",
            "raw_result_json",
            "payload_json",
            "result_json",
            "screenshots_json",
            "frames_json",
            "metadata_json",
            "metrics_json",
            "harvester_root",
            "jobs_path",
            "manifest_path",
            "content_identity_key",
        ]
        for english_column in hidden_columns:
            for display in [performance_display, assets_display, jobs_display, manifests_display, capture_jobs_display]:
                self.assertNotIn(english_column, display.columns)
        for expected_column in ["周期开始", "周期结束", "平台", "渠道", "平台编号", "标题", "价值", "价值占比"]:
            self.assertIn(expected_column, performance_display.columns)
        self.assertIn("B站内容类型", assets_display.columns)
        self.assertIn("状态", jobs_display.columns)
        self.assertIn("素材来源", manifests_display.columns)
        self.assertNotIn("素材目录", manifests_display.columns)
        self.assertIn("平台编号", capture_jobs_display.columns)

    def test_asset_cache_status_summary_counts_reuse_pending_and_analysis(self):
        top_pool = pd.DataFrame(
            [
                {"content_identity_key": "one"},
                {"content_identity_key": "two"},
                {"content_identity_key": "three"},
            ]
        )
        capture_pool = top_pool.iloc[:2].copy()
        manifests = pd.DataFrame(
            [
                {"status": "succeeded", "asset_key": "asset-1"},
                {"status": "failed", "error_message": "登录状态失效"},
            ]
        )
        jobs = pd.DataFrame(
            [
                {"status": "succeeded"},
                {"status": "failed", "error_message": "MiniMax 配置缺失"},
            ]
        )

        summary = _asset_cache_status_summary(top_pool, capture_pool, manifests, jobs)

        self.assertEqual(summary["高价值池"], 3)
        self.assertEqual(summary["可复盘素材"], 2)
        self.assertEqual(summary["已复用缓存"], 1)
        self.assertEqual(summary["待补采"], 1)
        self.assertEqual(summary["已完成多模态"], 1)
        self.assertIn("登录状态失效", summary["失败原因"])

    def test_recap_tier_status_summary_counts_scope_jobs_and_report(self):
        top_pool = pd.DataFrame(
            [
                {"content_identity_key": "one", "channel": "抖音商业化"},
                {"content_identity_key": "two", "channel": "抖音商业化"},
            ]
        )
        manifests = pd.DataFrame(
            [
                {"status": "succeeded", "content_identity_key": "one"},
                {"status": "succeeded", "content_identity_key": "outside"},
                {"status": "failed", "content_identity_key": "two"},
            ]
        )
        jobs = pd.DataFrame(
            [
                {"analysis_purpose": "strategy_recap:tier1_spend_top", "status": "succeeded"},
                {"analysis_purpose": "strategy_recap:tier1_spend_top", "status": "queued"},
                {"analysis_purpose": "strategy_recap:tier2_exposure_top", "status": "succeeded"},
            ]
        )

        summary = _recap_tier_status_summary(
            "tier1_spend_top",
            top_pool,
            manifests,
            jobs,
            {"has_report": True},
        )

        self.assertEqual(_recap_tier_analysis_purpose("tier1_spend_top"), "strategy_recap:tier1_spend_top")
        self.assertEqual(summary["范围素材"], 2)
        self.assertEqual(summary["已缓存素材"], 1)
        self.assertEqual(summary["已完成多模态"], 1)
        self.assertEqual(summary["待分析"], 1)
        self.assertEqual(summary["LLM报告"], "已生成")

    def test_recap_tier_status_summary_matches_manifest_by_job_id_when_identity_missing(self):
        top_pool = pd.DataFrame(
            [
                {"content_identity_key": "one", "job_id": "job-one"},
                {"content_identity_key": "two", "job_id": "job-two"},
            ]
        )
        manifests = pd.DataFrame(
            [
                {"status": "succeeded", "job_id": "job-one"},
                {"status": "succeeded", "job_id": "job-outside"},
            ]
        )

        summary = _recap_tier_status_summary(
            "tier1_spend_top",
            top_pool,
            manifests,
            pd.DataFrame(),
            {"has_report": False},
        )

        self.assertEqual(summary["已缓存素材"], 1)
        self.assertEqual(summary["LLM报告"], "未生成")

    def test_recap_tier_pipeline_writes_partial_report_when_capture_partially_fails(self):
        class CaptureResult:
            ok = False
            job_count = 2
            succeeded_count = 1
            failed_count = 1
            message = "补采失败"

        class Status:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def write(self, message):
                pass

            def update(self, **kwargs):
                pass

        items = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "高消耗素材",
                    "account": "投放号",
                    "content_url": "https://www.douyin.com/video/1",
                    "spend": 3000.0,
                    "impressions": 1000.0,
                    "analysis_status": "可分析",
                }
            ]
        )

        persisted = Mock(item_count=1, strategy_count=1)
        with patch("app.copy_missing_runtime_env"), patch("app.st.status", return_value=Status()), patch("app.st.warning") as warning, patch(
            "app.run_harvester_asset_capture", return_value=CaptureResult()
        ), patch("app.reset_top_multimodal_jobs"), patch("app.run_top_multimodal_analysis_from_manifests", return_value=1), patch(
            "app.persist_multimodal_recap", return_value=persisted
        ), patch("app._successful_analysis_identities", return_value={"抖音商业化::抖音::title_account::投放号::高消耗素材"}), patch(
            "app.generate_range_recap_report", return_value={"overview": {"report": "部分报告"}}
        ) as generate_report, patch("app.persist_range_recap_report") as persist_report, patch(
            "app.list_period_channel_totals", return_value=pd.DataFrame()
        ), patch("app.read_batch_record", return_value={}):
            ok = _run_recap_tier_pipeline("batch-1", items, RECAP_TIER_1_SPEND_TOP)

        self.assertFalse(ok)
        self.assertTrue(generate_report.called)
        self.assertTrue(persist_report.called)
        report = persist_report.call_args.kwargs["report"]
        self.assertEqual(report["range_execution_status"], "partial")
        self.assertIn("部分完成", warning.call_args.args[0])

    def test_report_status_copy_only_states_whether_conclusion_uses_current_data(self):
        self.assertEqual(
            _report_status_copy({"has_report": True}),
            "页面汇报结论已基于当前选择周期的数据；上传清洗、类型复盘、页面汇报是分步完成态。",
        )
        self.assertEqual(
            _report_status_copy({"has_report": False}),
            "页面汇报结论待更新：上传清洗完成后，还需要生成类型复盘并点击生成/更新口头汇报结论。",
        )

    def test_report_section_view_model_uses_weekly_meeting_order_and_text_fallbacks(self):
        top_pool = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "达人观点素材",
                    "spend": 300.0,
                    "impressions": 3000.0,
                    "activations": 30.0,
                    "first_pay_count": 3.0,
                    "content_url": "https://example.com/dy-1",
                },
                {
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "BV1",
                    "title": "旧内容同步",
                    "spend": 200.0,
                    "impressions": 2000.0,
                    "activations": 8.0,
                    "first_pay_count": 1.0,
                    "content_url": "",
                },
            ]
        )

        model = _report_section_view_model(top_pool, {"has_report": False}, {})

        self.assertEqual(
            list(model),
            [
                "一、整体数据",
                "二、抖音商业化内容分析",
                "三、抖音市场部内容分析",
                "四、小红书商业化内容分析",
                "五、小红书市场部内容分析",
                "六、B站数据",
                "下周重点策略",
            ],
        )
        self.assertNotIn("下周待办", model)
        self.assertIn("数据结论", model["一、整体数据"]["title"])
        self.assertIn("结论待更新", "\n".join(model["一、整体数据"]["items"]))
        self.assertIn("抖音商业化", "\n".join(model["二、抖音商业化内容分析"]["items"]))
        self.assertIn("当前数据未提供足够证据", "\n".join(model["三、抖音市场部内容分析"]["items"]))
        self.assertIn("B站", "\n".join(model["六、B站数据"]["items"]))

    def test_report_section_view_model_names_top_l1_l2_types_and_common_patterns(self):
        top_pool = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "普通人如何建立交易体系",
                    "category_l1": "图文",
                    "category_l2": "投资认知理财方法",
                    "spend": 500.0,
                    "impressions": 5000.0,
                    "activations": 50.0,
                    "first_pay_count": 5.0,
                },
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-2",
                    "title": "普通人存钱复利方法",
                    "category_l1": "图文",
                    "category_l2": "投资认知理财方法",
                    "spend": 300.0,
                    "impressions": 3000.0,
                    "activations": 30.0,
                    "first_pay_count": 3.0,
                },
                {
                    "platform": "小红书",
                    "channel": "小红书市场部",
                    "content_id": "xhs-1",
                    "title": "K线形态入门清单",
                    "category_l1": "图文",
                    "category_l2": "理财方法",
                    "spend": 200.0,
                    "impressions": 2000.0,
                    "activations": 20.0,
                    "first_pay_count": 2.0,
                },
            ]
        )

        model = _report_section_view_model(top_pool, {"has_report": True}, {})
        overview = "\n".join(model["一、整体数据"]["items"])
        douyin = "\n".join(model["二、抖音商业化内容分析"]["items"])

        self.assertIn("一级类型表现", overview)
        self.assertIn("二级类型表现", overview)
        self.assertIn("图文", overview)
        self.assertIn("投资认知理财方法", overview)
        self.assertIn("高价值内容共性", overview)
        self.assertIn("普通人", overview)
        self.assertIn("一级类型表现", douyin)
        self.assertIn("二级类型表现", douyin)
        self.assertIn("投资认知理财方法", douyin)

    def test_report_section_view_model_does_not_duplicate_generated_overview_summary(self):
        top_pool = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "普通人交易方法",
                    "category_l1": "图文",
                    "category_l2": "投资认知理财方法",
                    "spend": 500.0,
                    "impressions": 5000.0,
                    "activations": 50.0,
                    "first_pay_count": 5.0,
                }
            ]
        )
        generated_report = {
            "overview": {
                "summary": "结论已基于当前选择周期的数据。；本周期高价值池共 1 条素材。",
                "sections": [
                    {
                        "title": "一、整体数据",
                        "items": ["本周期高价值池共 1 条素材。"],
                    }
                ],
            }
        }

        model = _report_section_view_model(top_pool, {"has_report": True}, generated_report)
        overview = "\n".join(model["一、整体数据"]["items"])

        self.assertNotIn("；本周期高价值池", overview)

    def test_evidence_status_groups_hidden_links_missing_assets_and_reportable_items(self):
        top_pool = pd.DataFrame(
            [
                {"asset_key": "asset-1", "content_id": "dy-1", "title": "有缓存", "content_url": "https://example.com/1"},
                {"asset_key": "asset-2", "content_id": "dy-2", "title": "链接可能删除", "content_url": "https://example.com/2"},
                {"asset_key": "asset-3", "content_id": "dy-3", "title": "无素材", "content_url": ""},
            ]
        )
        manifests = pd.DataFrame(
            [
                {"asset_key": "asset-1", "status": "succeeded", "error_message": ""},
                {"asset_key": "asset-2", "status": "failed", "error_message": "作品已删除或隐藏"},
            ]
        )

        summary = _evidence_status_summary(top_pool, manifests)
        table = _evidence_status_table(top_pool, manifests)

        self.assertEqual(summary["可汇报"], 1)
        self.assertEqual(summary["链接不可访问"], 1)
        self.assertEqual(summary["有数据无素材"], 1)
        self.assertEqual(set(table["证据状态"]), {"可汇报", "链接不可访问", "有数据无素材"})
        self.assertIn("作品已删除或隐藏", table.set_index("平台编号").loc["dy-2", "原因"])

    def test_evidence_status_marks_douyin_giant_asset_without_work_link_as_analyzable(self):
        top_pool = pd.DataFrame(
            [
                {
                    "asset_key": "抖音::title_account::账号::高价值标题",
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "material_id": "7626286546770968627",
                    "title": "高价值标题",
                    "content_url": "",
                    "category_l1": "股友说",
                    "category_l2": "股民教学",
                    "ad_material_url": "https://巨量.example/video.mp4",
                    "ad_cover_url": "https://巨量.example/cover.jpg",
                    "match_status": "已匹配",
                    "match_source": "标准标题",
                }
            ]
        )

        summary = _evidence_status_summary(top_pool, pd.DataFrame())
        table = _evidence_status_table(top_pool, pd.DataFrame())

        self.assertEqual(summary["可分析但作品链接缺失"], 1)
        row = table.iloc[0]
        self.assertEqual(row["证据状态"], "可分析但作品链接缺失")
        self.assertIn("巨量视频/封面", row["原因"])

    def test_evidence_status_marks_douyin_work_identity_with_giant_cover_as_reportable(self):
        top_pool = pd.DataFrame(
            [
                {
                    "asset_key": "抖音::id::7594830477777751338",
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "7594830477777751338",
                    "work_id": "7594830477777751338",
                    "work_url": "https://www.douyin.com/video/7594830477777751338",
                    "title": "已有作品身份",
                    "category_l1": "社区话题",
                    "category_l2": "股市段子互动",
                    "ad_cover_url": "https://巨量.example/cover.jpg",
                    "match_status": "已匹配",
                }
            ]
        )

        summary = _evidence_status_summary(top_pool, pd.DataFrame())
        table = _evidence_status_table(top_pool, pd.DataFrame())

        self.assertEqual(summary["可汇报"], 1)
        row = table.iloc[0]
        self.assertEqual(row["证据状态"], "可汇报")
        self.assertIn("巨量视频/封面", row["原因"])

    def test_evidence_status_marks_unmatched_douyin_giant_cover_as_unmatched_local_library(self):
        top_pool = pd.DataFrame(
            [
                {
                    "asset_key": "抖音::title_account::账号::未匹配标题",
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "material_id": "7626286546770968627",
                    "title": "未匹配标题",
                    "content_url": "",
                    "ad_cover_url": "https://巨量.example/cover.jpg",
                    "match_status": "未匹配",
                    "analysis_status": "不可分析",
                }
            ]
        )

        summary = _evidence_status_summary(top_pool, pd.DataFrame())
        table = _evidence_status_table(top_pool, pd.DataFrame())

        self.assertEqual(summary["未匹配本地库"], 1)
        self.assertEqual(table.iloc[0]["证据状态"], "未匹配本地库")

    def test_manual_supplements_join_report_pool_and_evidence_status(self):
        top_pool = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "原高价值素材",
                    "spend": 100.0,
                }
            ]
        )
        supplements = pd.DataFrame(
            [
                {
                    "supplement_id": "manual-1",
                    "platform": "小红书",
                    "channel": "小红书市场部",
                    "content_id": "note-1",
                    "content_url": "https://example.com/note-1",
                    "title": "人工补充素材",
                    "category_l1": "图文",
                    "category_l2": "理财方法",
                    "evidence_path": "/tmp/note-1.png",
                    "reason": "这条影响小红书结论。",
                }
            ]
        )

        report_pool = _report_pool_with_manual_supplements(top_pool, supplements)
        model = _report_section_view_model(report_pool, {"has_report": False}, {})
        evidence = _evidence_status_table(report_pool, pd.DataFrame())

        self.assertEqual(len(report_pool), 2)
        self.assertIn("已纳入 1 条人工补充", "\n".join(model["一、整体数据"]["items"]))
        self.assertIn("人工补充素材", "\n".join(model["五、小红书市场部内容分析"]["items"]))
        self.assertEqual(evidence.set_index("平台编号").loc["note-1", "证据状态"], "人工新增")
        self.assertIn("/tmp/note-1.png", evidence.set_index("平台编号").loc["note-1", "原因"])

    def test_classification_quality_issues_follow_harvester_taxonomy_boundaries(self):
        items = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-ok",
                    "category_l1": "股友说",
                    "category_l2": "股民教学",
                    "bilibili_content_type": "",
                },
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-bad",
                    "category_l1": "资讯",
                    "category_l2": "泛财经",
                    "bilibili_content_type": "",
                },
                {
                    "platform": "小红书",
                    "channel": "小红书市场部",
                    "content_id": "xhs-bad",
                    "category_l1": "图文",
                    "category_l2": "股友说",
                    "bilibili_content_type": "",
                },
                {
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "bv-bad",
                    "category_l1": "采访内容",
                    "category_l2": "二级不应存在",
                    "bilibili_content_type": "",
                },
            ]
        )

        issues = _classification_quality_issues(items)

        self.assertEqual(set(issues["平台编号"]), {"dy-bad", "xhs-bad", "bv-bad"})
        self.assertIn("抖音一级类型", issues.set_index("平台编号").loc["dy-bad", "问题"])
        self.assertIn("小红书图文二级类型", issues.set_index("平台编号").loc["xhs-bad", "问题"])
        self.assertIn("B站只使用单级内容类型", issues.set_index("平台编号").loc["bv-bad", "问题"])

    def test_quality_items_are_scoped_to_report_pool_plus_manual_supplements(self):
        top_pool = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-top",
                    "category_l1": "股友说",
                    "category_l2": "股民教学",
                }
            ]
        )
        non_top_items = pd.DataFrame(
            [
                *top_pool.to_dict("records"),
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-non-top",
                    "category_l1": "资讯",
                    "category_l2": "泛财经",
                },
            ]
        )
        supplements = pd.DataFrame(
            [
                {
                    "supplement_id": "manual-bad",
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "bv-manual",
                    "category_l1": "采访内容",
                    "category_l2": "不应存在",
                    "bilibili_content_type": "",
                }
            ]
        )

        scoped = _quality_items_with_manual_supplements(top_pool, supplements)
        unscoped_issues = _classification_quality_issues(non_top_items)
        scoped_issues = _classification_quality_issues(scoped)

        self.assertIn("dy-non-top", set(unscoped_issues["平台编号"]))
        self.assertNotIn("dy-non-top", set(scoped_issues["平台编号"]))
        self.assertEqual(set(scoped_issues["平台编号"]), {"bv-manual"})

    def test_local_recap_metric_items_add_total_share_and_scope_notes(self):
        row = pd.Series(
            {
                "高价值素材数": 3,
                "可复盘素材数": 2,
                "待补齐素材数": 1,
                "高价值消耗": 50.0,
                "高价值曝光": 200.0,
                "高价值价值": 75.0,
            }
        )
        total_metrics = {"消耗": 100.0, "曝光": 800.0, "价值": 300.0}

        items = _local_recap_metric_items(row, total_metrics)

        by_label = {item["label"]: item for item in items}
        self.assertEqual(by_label["高价值素材"]["scope"], "高价值素材池总数")
        self.assertEqual(by_label["可复盘素材"]["scope"], "可进入复盘的高价值素材")
        self.assertEqual(by_label["待补齐素材"]["scope"], "高价值池内待补齐素材")
        self.assertEqual(by_label["高价值消耗"]["share"], "占总量 50.0%")
        self.assertEqual(by_label["高价值曝光"]["share"], "占总量 25.0%")
        self.assertEqual(by_label["高价值价值"]["share"], "占总量 25.0%")
        self.assertEqual(by_label["高价值消耗"]["scope"], "高价值素材池 / 当前周期总消耗")

    def test_local_recap_metric_html_stays_inline_for_streamlit_markdown(self):
        html = _local_recap_metric_html(
            {
                "label": "高价值素材",
                "value": "97",
                "share": "",
                "scope": "高价值素材池总数",
            }
        )

        self.assertNotIn("\n", html)
        self.assertIn('class="local-recap-metric"', html)
        self.assertIn('class="local-recap-note">高价值素材池总数</div>', html)

    def test_top_asset_cache_entries_display_hides_paths_and_localizes_size(self):
        entries = pd.DataFrame(
            [
                {
                    "asset_key": "抖音::id::1",
                    "platform": "抖音",
                    "content_id": "1",
                    "source": "harvester_daily_cache",
                    "asset_dir": "/tmp/internal/path",
                    "size_bytes": 2048,
                    "ref_count": 2,
                    "last_used_batch_id": "batch-1",
                    "updated_at": "2026-06-17T00:00:00+00:00",
                }
            ]
        )

        display = _top_asset_cache_entries_display(entries)

        self.assertIn("素材来源", display.columns)
        self.assertIn("缓存体积", display.columns)
        self.assertNotIn("asset_dir", display.columns)
        self.assertNotIn("/tmp/internal/path", display.to_string())

    def test_display_tables_use_platform_specific_type_columns(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "抖音标题",
                    "category_l1": "股友说",
                    "category_l2": "股民教学",
                    "bilibili_content_type": "不应展示",
                    "content_type": "旧兼容字段",
                },
                {
                    "platform": "小红书",
                    "channel": "小红书商业化",
                    "content_id": "xhs-1",
                    "title": "小红书标题",
                    "category_l1": "图文",
                    "category_l2": "理财方法",
                    "bilibili_content_type": "不应展示",
                    "content_type": "旧兼容字段",
                },
                {
                    "platform": "B站",
                    "channel": "B站市场部",
                    "content_id": "BV1abcde2345",
                    "title": "B站标题",
                    "category_l1": "不应展示",
                    "category_l2": "不应展示",
                    "bilibili_content_type": "采访内容",
                    "content_type": "旧兼容字段",
                },
            ]
        )

        performance_display = localize_columns(_content_performance_display(frame))
        assets_display = localize_columns(_local_content_assets_display(frame))

        for display in [performance_display, assets_display]:
            self.assertIn("一级类型", display.columns)
            self.assertIn("二级类型", display.columns)
            self.assertIn("B站内容类型", display.columns)
            self.assertNotIn("栏目", display.columns)
            self.assertNotIn("内容类型", display.columns)

            by_platform = display.set_index("平台")
            self.assertEqual(by_platform.loc["抖音", "一级类型"], "股友说")
            self.assertEqual(by_platform.loc["抖音", "二级类型"], "股民教学")
            self.assertEqual(by_platform.loc["抖音", "B站内容类型"], "")
            self.assertEqual(by_platform.loc["小红书", "一级类型"], "图文")
            self.assertEqual(by_platform.loc["小红书", "二级类型"], "理财方法")
            self.assertEqual(by_platform.loc["小红书", "B站内容类型"], "")
            self.assertEqual(by_platform.loc["B站", "一级类型"], "")
            self.assertEqual(by_platform.loc["B站", "二级类型"], "")
            self.assertEqual(by_platform.loc["B站", "B站内容类型"], "采访内容")

    def test_local_assets_display_hides_period_batch_fields_and_deduplicates_assets(self):
        assets = pd.DataFrame(
            [
                {
                    "asset_key": "小红书::id::note-1",
                    "platform": "小红书",
                    "content_id": "note-1",
                    "account": "示例账号",
                    "title": "旧标题",
                    "category_l1": "图文",
                    "category_l2": "理财方法",
                    "content_url": "https://www.xiaohongshu.com/explore/note-1",
                    "first_seen_batch_id": "batch-old",
                    "last_seen_batch_id": "batch-old",
                    "updated_at": "2026-06-01T00:00:00+00:00",
                },
                {
                    "asset_key": "小红书::id::note-1",
                    "platform": "小红书",
                    "content_id": "note-1",
                    "account": "示例账号",
                    "title": "新标题",
                    "category_l1": "图文",
                    "category_l2": "理财方法",
                    "content_url": "https://www.xiaohongshu.com/explore/note-1",
                    "first_seen_batch_id": "batch-old",
                    "last_seen_batch_id": "batch-new",
                    "updated_at": "2026-06-02T00:00:00+00:00",
                },
            ]
        )

        display = localize_columns(_local_content_assets_display(assets))

        self.assertEqual(len(display), 1)
        self.assertEqual(display.iloc[0]["标题"], "新标题")
        self.assertIn("更新时间", display.columns)
        self.assertNotIn("最近批次", display.columns)
        self.assertNotIn("首次批次", display.columns)

    def test_local_assets_display_shows_single_work_link_with_compat_fallback(self):
        assets = pd.DataFrame(
            [
                {
                    "asset_key": "小红书::id::note-1",
                    "platform": "小红书",
                    "content_id": "note-1",
                    "account": "示例账号",
                    "title": "只有兼容链接",
                    "content_url": "https://www.xiaohongshu.com/explore/note-1",
                    "work_url": "",
                }
            ]
        )

        display = localize_columns(_local_content_assets_display(assets))

        self.assertIn("作品链接", display.columns)
        self.assertNotIn("作品链接(兼容)", display.columns)
        self.assertEqual(display.iloc[0]["作品链接"], "https://www.xiaohongshu.com/explore/note-1")

    def test_local_assets_display_hides_empty_ad_evidence_columns(self):
        assets = pd.DataFrame(
            [
                {
                    "asset_key": "小红书::id::note-1",
                    "platform": "小红书",
                    "content_id": "note-1",
                    "title": "无巨量证据",
                    "content_url": "https://www.xiaohongshu.com/explore/note-1",
                    "ad_material_url": "",
                    "ad_cover_url": "",
                }
            ]
        )

        display = localize_columns(_local_content_assets_display(assets))

        self.assertNotIn("巨量链接", display.columns)
        self.assertNotIn("巨量封面链接", display.columns)

    def test_local_assets_display_never_shows_period_ad_evidence_columns(self):
        assets = pd.DataFrame(
            [
                {
                    "asset_key": "抖音::id::7594830477777751338",
                    "platform": "抖音",
                    "content_id": "7594830477777751338",
                    "title": "有巨量证据",
                    "work_url": "https://www.douyin.com/video/7594830477777751338",
                    "content_url": "https://www.douyin.com/video/7594830477777751338",
                    "ad_material_url": "https://巨量.example/video.mp4",
                    "ad_cover_url": "https://巨量.example/cover.jpg",
                }
            ]
        )

        display = localize_columns(_local_content_assets_display(assets))

        self.assertNotIn("巨量链接", display.columns)
        self.assertNotIn("巨量封面链接", display.columns)

    def test_content_performance_table_keeps_metrics_numeric_and_cleans_title_tags(self):
        frame = pd.DataFrame(
            [
                {
                    "period_start": "2026-06-05",
                    "period_end": "2026-06-11",
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "title": "为什么说股市就是看清自己最好的地方？ #财经 #同花顺资讯 #股市",
                    "tags": "#财经 #同花顺资讯 #股市",
                    "category_l1": "股友说",
                    "category_l2": "股民教学",
                    "spend": "1000.5",
                    "impressions": "10000",
                    "activations": "10",
                    "first_pay_count": "2",
                    "activation_cost": "100.05",
                    "first_pay_cost": "500.25",
                }
            ]
        )

        with patch("app.st.dataframe") as dataframe:
            _show_frame(_content_performance_display(frame))

        displayed = dataframe.call_args.args[0]
        self.assertEqual(displayed.iloc[0]["标题"], "为什么说股市就是看清自己最好的地方？")
        self.assertEqual(displayed.iloc[0]["tag词"], "#财经 #同花顺资讯 #股市")
        for column in ["消耗", "曝光量", "激活数", "付费数", "激活成本", "付费成本"]:
            self.assertTrue(pd.api.types.is_numeric_dtype(displayed[column]), column)
        self.assertEqual(float(displayed.iloc[0]["消耗"]), 1000.5)

    def test_show_frame_uses_content_height_for_short_tables(self):
        frame = pd.DataFrame([{"channel": "抖音商业化", "spend": 100.0}])

        with patch("app.st.dataframe") as dataframe:
            _show_frame(frame, height=320)

        rendered_height = dataframe.call_args.kwargs["height"]
        self.assertLess(rendered_height, 160)

    def test_show_frame_caps_tall_tables_at_requested_height(self):
        frame = pd.DataFrame([{"channel": f"渠道{i}", "spend": i} for i in range(20)])

        with patch("app.st.dataframe") as dataframe:
            _show_frame(frame, height=220)

        self.assertEqual(dataframe.call_args.kwargs["height"], 220)

    def test_show_frame_can_expand_to_show_all_rows(self):
        frame = pd.DataFrame([{"channel": f"渠道{i}", "spend": i} for i in range(20)])

        with patch("app.st.dataframe") as dataframe:
            _show_frame(frame, height=220, fit_all_rows=True)

        self.assertGreater(dataframe.call_args.kwargs["height"], 220)

    def test_user_facing_status_values_are_localized(self):
        jobs = pd.DataFrame(
            [
                {
                    "status": "succeeded",
                    "trigger": "manual_recap",
                    "platform": "B站",
                    "title": "真实B站标题",
                    "attempts": 1,
                    "max_attempts": 3,
                    "updated_at": "2026-06-16T00:00:00+00:00",
                }
            ]
        )
        manifests = pd.DataFrame(
            [
                {
                    "status": "failed",
                    "platform": "B站",
                    "asset_dir": "/tmp/internal",
                    "updated_at": "2026-06-16T00:00:00+00:00",
                }
            ]
        )

        job_display = _analysis_jobs_display(jobs)
        manifest_display = _asset_cache_records_display(manifests)

        self.assertEqual(job_display.iloc[0]["status"], "已完成")
        self.assertEqual(job_display.iloc[0]["trigger"], "手动复盘")
        self.assertEqual(manifest_display.iloc[0]["status"], "失败")


if __name__ == "__main__":
    unittest.main()
