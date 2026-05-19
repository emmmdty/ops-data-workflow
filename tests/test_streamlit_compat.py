from pathlib import Path
import unittest


class StreamlitCompatibilityTests(unittest.TestCase):
    def test_app_uses_current_width_parameter(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("use_container_width", app_source)

    def test_app_uses_navigation_pages_and_markdown_recommendations(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("st.navigation", app_source)
        self.assertIn("st.Page", app_source)
        self.assertIn("title=\"总览\"", app_source)
        self.assertNotIn("总览与推荐", app_source)
        self.assertIn("内容类型统计", app_source)
        self.assertIn("历史趋势", app_source)
        self.assertIn("st.markdown(recommendations", app_source)

    def test_app_supports_directory_upload_and_segmented_controls(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn('accept_multiple_files="directory"', app_source)
        self.assertIn("st.segmented_control", app_source)
        self.assertIn("一周", app_source)
        self.assertIn("两周", app_source)
        self.assertIn("一个月", app_source)

    def test_generate_page_uses_single_upload_control(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _render_historical_reports")]

        self.assertNotIn("GENERATE_UPLOAD_MODES", app_source)
        self.assertNotIn("generate_upload_mode", generate_source)
        self.assertNotIn("上传方式", generate_source)
        self.assertNotIn("文件夹上传", generate_source)
        self.assertNotIn("多文件或 zip 上传", generate_source)
        self.assertEqual(generate_source.count("st.file_uploader("), 1)
        self.assertIn('accept_multiple_files="directory"', generate_source)

    def test_app_no_longer_hardcodes_legacy_generate_period_defaults(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("value=date(2026, 4, 1)", app_source)
        self.assertNotIn("value=date(2026, 4, 27)", app_source)
        self.assertIn("timedelta(days=6)", app_source)
        self.assertIn("generate_period_end_touched", app_source)

    def test_app_moves_generation_parameters_out_of_sidebar_and_syncs_raw(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("with st.sidebar:", app_source)
        self.assertIn("@st.fragment", app_source)
        self.assertIn("run_every=", app_source)
        self.assertIn("sync_raw_periods", app_source)
        self.assertIn("strip_common_period_root=True", app_source)

    def test_generate_page_can_view_download_and_update_history_reports(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _page_content_types")]

        self.assertIn("_render_historical_reports()", generate_source)
        self.assertIn("历史报告", app_source)
        self.assertIn("查看历史报告", app_source)
        self.assertIn("下载历史报告", app_source)
        self.assertIn("更新所选报告", app_source)
        self.assertIn("read_batch_record", app_source)
        self.assertIn("report.html", app_source)
        self.assertIn("components.html", app_source)

    def test_app_renders_navigation_before_background_raw_sync(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        raw_sync_call = app_source.rindex("\n_sync_raw_data_fragment()")
        self.assertLess(app_source.index("page.run()"), raw_sync_call)

    def test_app_renders_total_and_platform_metric_sections(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("总体核心指标", app_source)
        self.assertIn("环比增长", app_source)
        self.assertIn("分平台核心结果", app_source)
        self.assertIn("_render_platform_kpis", app_source)
        self.assertIn("_render_growth_overview", app_source)
        self.assertNotIn("st.columns(6)", app_source)

    def test_platform_kpis_read_raw_metric_columns_before_display(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]
        function_source = app_source[
            app_source.index("def _render_platform_kpis") : app_source.index("def _render_metric_grid")
        ]

        self.assertNotIn("localized = localize_columns(platform_summary.copy())", function_source)
        self.assertIn("_render_platform_kpis(platform_summary, channel_comparison)", overview_source)
        self.assertIn("channel_comparison: pd.DataFrame | None = None", function_source)
        self.assertIn("delta=_platform_growth_delta", function_source)
        self.assertIn("delta_color=_platform_growth_delta_color", function_source)
        self.assertIn('first_pay_column = "first_pay_count"', function_source)
        self.assertIn('first_pay_cost_column = "first_pay_cost"', function_source)
        self.assertIn('first_pay_rate_column = "first_pay_rate"', function_source)
        self.assertIn('first_pay_rate_change_rate', function_source)

    def test_overview_uses_global_period_selector_and_no_tables(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertIn('_get_common_period_selector("overview")', overview_source)
        self.assertIn("build_period_comparison_between_batches", overview_source)
        self.assertIn("_get_comparison_period_selector", overview_source)
        self.assertIn("global_batch_id", app_source)
        self.assertNotIn("st.dataframe", overview_source)

    def test_app_has_chinese_upload_prompt_and_content_analysis_page(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("拖拽文件夹或文件到此处，或点击选择", app_source)
        self.assertIn("单个文件最大 200MB", app_source)
        self.assertIn("浏览文件/文件夹", app_source)
        self.assertIn('[data-testid="stFileUploaderDropzone"] > *', app_source)
        self.assertNotIn('div[data-testid="stFileUploaderDropzone"]', app_source)
        self.assertIn("内容分析", app_source)
        self.assertIn("文件备份", app_source)
        self.assertNotIn("渠道下钻分析", app_source)
        self.assertIn("B站全部", app_source)
        self.assertIn("metric_sort_ascending", app_source)
        self.assertIn("选择对比周期", app_source)
        self.assertNotIn('orientation="h"', app_source)

    def test_app_imports_batch_scoped_auxiliary_loaders(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        dashboard_imports = app_source[
            app_source.index("from ops_data_workflow.dashboard import (") : app_source.index("from ops_data_workflow.reporting")
        ]

        self.assertIn("load_data_quality_for_batch", dashboard_imports)
        self.assertIn("load_review_queue_for_batch", dashboard_imports)
        self.assertIn("build_period_comparison_for_batch", dashboard_imports)
        self.assertIn("build_period_comparison_between_batches", dashboard_imports)

    def test_period_selector_does_not_prewrite_widget_session_key(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        selector_source = app_source[
            app_source.index("def _get_common_period_selector") : app_source.index("def _sync_period_from_global")
        ]

        self.assertNotIn("st.session_state[session_key] =", selector_source)
        self.assertIn("del st.session_state[session_key]", selector_source)
        self.assertNotIn('st.session_state["overview_batch_id"] = result.batch_id', app_source)

    def test_session_state_widgets_do_not_pass_duplicate_defaults(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _page_trends")]
        trend_source = app_source[app_source.index("def _page_trends") : app_source.index("def _page_content_details")]

        self.assertNotIn('value=st.session_state["generate_period_start"]', generate_source)
        self.assertNotIn('value=st.session_state["generate_period_end"]', generate_source)
        self.assertNotIn('value=st.session_state["trend_period_start"]', trend_source)
        self.assertNotIn('value=st.session_state["trend_period_end"]', trend_source)
        self.assertNotIn('default=st.session_state.get("trend_quick_range"', trend_source)

    def test_app_renders_single_item_category_review_flow(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("当前待审核", app_source)
        self.assertIn("审核原因", app_source)
        self.assertIn("上一条", app_source)
        self.assertIn("下一条", app_source)
        self.assertIn("确认并保存当前审核", app_source)
        self.assertIn("category_confidence", app_source)


if __name__ == "__main__":
    unittest.main()
