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

    def test_generate_page_exposes_review_period_normalization_ui(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _render_historical_reports")]

        self.assertIn("normalize_uploaded_periods", app_source)
        self.assertIn("preview_uploaded_periods", app_source)
        self.assertIn("复盘层级", generate_source)
        self.assertIn("数据时间", generate_source)
        self.assertIn("文件数", app_source)
        self.assertIn("来源路径", app_source)
        self.assertIn("来源类型", generate_source)
        self.assertIn("period_manifest.json", app_source)

    def test_generate_page_exposes_stepwise_progress_status(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _render_historical_reports")]

        self.assertIn("st.status", generate_source)
        self.assertIn("_run_with_generation_progress", app_source)
        self.assertIn("progress_callback", app_source)
        self.assertIn("正在识别上传文件和复盘周期", app_source)
        self.assertIn("正在整理 raw 周期目录", app_source)
        self.assertIn("正在读取渠道数据并标准化", app_source)
        self.assertIn("正在校验数据质量与题材分类", app_source)
        self.assertIn("正在写入历史库并生成下载文件", app_source)

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
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertIn("本周期数据总览", overview_source)
        self.assertIn("_render_kpis(summary)", overview_source)
        self.assertIn("环比变化", overview_source)
        self.assertIn("_render_overview_summary_table(summary, platform_summary, channel_comparison)", overview_source)
        self.assertNotIn('st.subheader("总体核心指标")', overview_source)
        self.assertNotIn('st.subheader("分平台核心结果")', overview_source)
        self.assertNotIn("_render_growth_overview(channel_comparison)", overview_source)
        self.assertNotIn("_render_platform_kpis(platform_summary, channel_comparison)", overview_source)

    def test_overview_always_renders_period_comparison_table(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertNotIn("暂无上一同等级周期可用于环比。", overview_source)
        self.assertIn('st.subheader("环比变化")\n    _render_overview_summary_table(summary, platform_summary, channel_comparison)', overview_source)
        self.assertIn('return "（-）"', app_source)

    def test_overview_delta_colors_match_business_direction(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        growth_class_source = app_source[
            app_source.index("def _overview_growth_class") : app_source.index("def _compact_recommendations")
        ]

        self.assertIn('return "overview-delta-green" if is_cost else "overview-delta-red"', growth_class_source)
        self.assertIn('return "overview-delta-red" if is_cost else "overview-delta-green"', growth_class_source)
        self.assertNotIn("improved =", growth_class_source)

    def test_overview_uses_review_level_cards_and_client_side_metric_switcher(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertIn('_get_overview_period_selector("overview")', overview_source)
        self.assertIn("_render_review_level_cards", app_source)
        self.assertIn("PERIOD_LEVEL_WEEK", app_source)
        self.assertIn("st.columns(4)", app_source)
        self.assertIn("_render_platform_chart(platform_summary)", overview_source)
        self.assertIn("CHART_METRICS.items()", app_source)
        self.assertIn("updatemenus=[", app_source)
        self.assertIn('"method": "update"', app_source)
        self.assertNotIn("overview_metric_button_", app_source)
        self.assertNotIn('"选择复盘层级"', overview_source)
        self.assertNotIn('"选择图表指标"', overview_source)

    def test_overview_uses_auto_comparison_and_no_dataframes(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertIn('_get_overview_period_selector("overview")', overview_source)
        self.assertIn("_load_overview_data(APP_DB, selected_batch_id)", overview_source)
        self.assertIn("load_channel_comparison_for_batch", app_source)
        self.assertIn("build_period_comparison_for_batch(db_path, batch_id)", app_source)
        self.assertIn("build_overview_table_rows(summary, platform_summary, channel_comparison)", app_source)
        self.assertIn("global_batch_id", app_source)
        self.assertNotIn("_get_comparison_period_selector", overview_source)
        self.assertNotIn("build_period_comparison_between_batches", overview_source)
        self.assertNotIn("st.dataframe", overview_source)
        self.assertNotIn("渠道栏目分析", overview_source)
        self.assertNotIn("overview_drill_channel", overview_source)

    def test_overview_data_is_cached_and_chart_switcher_does_not_force_rerun(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        platform_chart_source = app_source[
            app_source.index("def _build_platform_chart_figure") : app_source.index("def _content_filter_panel")
        ]

        self.assertIn("@st.cache_data", app_source)
        self.assertIn("def _load_overview_data", app_source)
        self.assertIn("_db_file_signature(APP_DB)", app_source)
        self.assertNotIn("st.button(", platform_chart_source)
        self.assertNotIn("st.rerun()", platform_chart_source)

    def test_app_has_chinese_upload_prompt_and_content_analysis_page(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("拖拽文件夹或文件到此处，或点击选择", app_source)
        self.assertIn("单个文件最大 200MB", app_source)
        self.assertIn("浏览文件/文件夹", app_source)
        self.assertIn('[data-testid="stFileUploaderDropzone"] > *', app_source)
        self.assertNotIn('div[data-testid="stFileUploaderDropzone"]', app_source)
        self.assertIn("_render_channel_page", app_source)
        self.assertIn("_make_channel_page", app_source)
        self.assertIn("_build_navigation_pages", app_source)
        self.assertNotIn("topic_labels_channel_", app_source)
        self.assertIn("load_topic_labels_for_batch", app_source)
        self.assertIn("summarize_persisted_topic_labels", app_source)
        self.assertIn("summarize_channel_categories", app_source)
        self.assertIn("build_channel_top_topic_insights", app_source)
        self.assertIn("文件备份", app_source)
        self.assertNotIn("渠道下钻分析", app_source)
        self.assertIn("B站全部", app_source)
        self.assertIn("选择对比周期", app_source)
        self.assertNotIn('orientation="h"', app_source)

    def test_channel_pages_do_not_require_analysis_choices(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        channel_source = app_source[app_source.index("def _render_channel_page") : app_source.index("def _render_channel_summary_metrics")]

        self.assertNotIn("选择渠道", channel_source)
        self.assertNotIn("排序指标", channel_source)
        self.assertNotIn("显示 Top N", channel_source)
        self.assertNotIn("选择二级栏目", channel_source)
        self.assertNotIn("selectbox", channel_source)
        self.assertNotIn("number_input", channel_source)
        self.assertIn("_selected_or_latest_batch_id()", channel_source)
        self.assertNotIn("_get_common_period_selector", channel_source)
        self.assertIn("重点题材分析", channel_source)
        self.assertIn("重点题材分析结论", channel_source)
        self.assertIn("st.markdown(topic_insights", channel_source)
        self.assertIn("页面只读取入库题材", channel_source)
        self.assertNotIn("group_topic_labels", channel_source)
        self.assertIn('st.subheader("栏目汇总")', channel_source)
        self.assertNotIn('st.subheader("二级栏目汇总")', channel_source)

    def test_channel_category_summary_is_single_table_with_category_first(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        channel_source = app_source[app_source.index("def _render_channel_page") : app_source.index("def _render_channel_summary_metrics")]
        category_display_source = app_source[
            app_source.index("def _category_table_display") : app_source.index("def _topic_table_display")
        ]

        self.assertIn("localize_columns(_category_table_display(category_summary))", channel_source)
        self.assertNotIn("_render_short_table_blocks(_category_table_display(category_summary)", channel_source)
        self.assertLess(category_display_source.index('"category_name"'), category_display_source.index('"item_count"'))

    def test_navigation_uses_dynamic_channel_page_factory(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        navigation_source = app_source[app_source.index("def _build_navigation_pages") : app_source.rindex("_inject_theme()")]

        self.assertIn("st.Page(_page_overview, title=\"总览\", default=True)", navigation_source)
        self.assertIn("st.Page(_page_generate, title=\"生成报告\")", navigation_source)
        self.assertIn("_make_channel_page(channel)", navigation_source)
        self.assertIn("title=channel", navigation_source)
        self.assertIn("page = st.navigation(_build_navigation_pages()", app_source)

    def test_short_table_blocks_use_static_tables_for_screenshots(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        table_source = app_source[
            app_source.index("def _render_short_table_blocks") : app_source.index("def _display_generation_results")
        ]

        self.assertIn("st.table", table_source)
        self.assertNotIn("st.dataframe", table_source)

    def test_app_imports_batch_scoped_auxiliary_loaders(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        dashboard_imports = app_source[
            app_source.index("from ops_data_workflow.dashboard import (") : app_source.index("from ops_data_workflow.reporting")
        ]

        self.assertIn("load_data_quality_for_batch", dashboard_imports)
        self.assertIn("load_review_queue_for_batch", dashboard_imports)
        self.assertIn("build_period_comparison_for_batch", dashboard_imports)
        self.assertIn("build_overview_table_rows", dashboard_imports)

    def test_period_selector_does_not_prewrite_widget_session_key(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        selector_source = app_source[
            app_source.index("def _get_common_period_selector") : app_source.index("def _sync_period_from_global")
        ]

        self.assertNotIn("st.session_state[session_key] =", selector_source)
        self.assertIn("del st.session_state[session_key]", selector_source)
        self.assertNotIn('st.session_state["overview_batch_id"] = result.batch_id', app_source)

    def test_period_selector_filters_batches_by_review_level(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        selector_source = app_source[
            app_source.index("def _get_common_period_selector") : app_source.index("def _get_comparison_period_selector")
        ]

        self.assertIn("选择复盘层级", selector_source)
        self.assertIn("PERIOD_LEVEL_LABELS", app_source)
        self.assertIn("period_level", selector_source)
        self.assertIn("source_type", selector_source)
        self.assertIn("数据时间", selector_source)

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

    def test_app_exposes_data_review_page_with_realtime_cleaned_excel_sync(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        navigation_source = app_source[app_source.index("def _build_navigation_pages") : app_source.rindex("_inject_theme()")]

        self.assertIn("review_resolutions", app_source)
        self.assertIn("数据审核", app_source)
        self.assertIn("st.data_editor", app_source)
        self.assertIn("保存审核并同步 Excel", app_source)
        self.assertIn("apply_review_resolutions_and_regenerate", app_source)
        self.assertIn('st.Page(_page_data_review, title="数据审核")', navigation_source)


if __name__ == "__main__":
    unittest.main()
