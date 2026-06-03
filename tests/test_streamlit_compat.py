from pathlib import Path
import unittest


class StreamlitCompatibilityTests(unittest.TestCase):
    def test_app_uses_current_width_parameter(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("use_container_width", app_source)

    def test_app_uses_navigation_pages_without_standalone_recommendations(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("st.navigation", app_source)
        self.assertIn("st.Page", app_source)
        self.assertIn("title=\"总览\"", app_source)
        self.assertNotIn("总览与推荐", app_source)
        self.assertIn("历史趋势", app_source)
        self.assertNotIn("st.markdown(recommendations", app_source)

    def test_app_supports_directory_upload_and_segmented_controls(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn('accept_multiple_files="directory"', app_source)
        self.assertIn("st.segmented_control", app_source)
        self.assertIn("最近 8 周", app_source)
        self.assertIn("最近 12 个月", app_source)

    def test_generate_page_uses_single_upload_control(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _render_rollup_generator")]

        self.assertNotIn("GENERATE_UPLOAD_MODES", app_source)
        self.assertNotIn("generate_upload_mode", generate_source)
        self.assertNotIn("上传方式", generate_source)
        self.assertNotIn("文件夹上传", generate_source)
        self.assertNotIn("多文件或 zip 上传", generate_source)
        self.assertEqual(generate_source.count("st.file_uploader("), 1)
        self.assertIn('accept_multiple_files="directory"', generate_source)

    def test_generate_page_exposes_review_period_normalization_ui(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _render_rollup_generator")]

        self.assertIn("normalize_uploaded_periods", app_source)
        self.assertIn("preview_uploaded_periods", app_source)
        self.assertIn("复盘层级", generate_source)
        self.assertIn("数据时间", generate_source)
        self.assertIn("文件数", app_source)
        self.assertIn("来源路径", app_source)
        self.assertIn("来源类型", generate_source)
        self.assertIn("data/months", app_source)
        self.assertIn("data/weeks", app_source)
        self.assertIn("processed", app_source)

    def test_generate_page_exposes_stepwise_progress_status(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _render_rollup_generator")]

        self.assertIn("st.status", generate_source)
        self.assertIn("_run_with_generation_progress", app_source)
        self.assertIn("progress_callback", app_source)
        self.assertIn("正在识别上传文件和复盘周期", app_source)
        self.assertIn("正在整理源文件周期目录", app_source)
        self.assertIn("正在整理清洗产物", app_source)
        self.assertIn("正在读取渠道数据并标准化", app_source)
        self.assertIn("正在校验字段完整性与内容类型", app_source)
        self.assertIn("正在写入周期库", app_source)

    def test_app_no_longer_hardcodes_legacy_generate_period_defaults(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("value=date(2026, 4, 1)", app_source)
        self.assertNotIn("value=date(2026, 4, 27)", app_source)
        self.assertIn("timedelta(days=6)", app_source)
        self.assertIn("generate_period_end_touched", app_source)

    def test_app_moves_generation_parameters_out_of_sidebar_without_startup_raw_sync(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("with st.sidebar:", app_source)
        self.assertNotIn("@st.fragment(run_every=", app_source)
        self.assertNotIn("_sync_raw_data_fragment()", app_source)
        self.assertIn("sync_raw_periods", app_source)
        self.assertIn("strip_common_period_root=True", app_source)
        self.assertIn("手动同步源文件", app_source)
        self.assertNotIn("已自动刷新", app_source)

    def test_generate_page_no_longer_exposes_history_reports(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _page_trends")]

        self.assertNotIn("_render_historical_reports()", generate_source)
        self.assertNotIn("历史报告", app_source)
        self.assertNotIn("查看历史报告", app_source)
        self.assertNotIn("下载历史报告", app_source)
        self.assertNotIn("更新所选报告", app_source)

    def test_generate_page_warns_before_overwriting_existing_channels(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        generate_source = app_source[app_source.index("def _page_generate") : app_source.index("def _run_with_generation_progress")]

        self.assertIn("detect_upload_channel_conflicts", app_source)
        self.assertIn("本地已存在渠道", generate_source)
        self.assertIn("覆盖已存在渠道", generate_source)
        self.assertIn("overwrite_existing_channels", app_source)

    def test_app_does_not_run_raw_sync_after_navigation_render(self):
        app_source = Path("app.py").read_text(encoding="utf-8")

        navigation_tail = app_source[app_source.index("page.run()") :]
        self.assertNotIn("_sync_raw_data_fragment", navigation_tail)
        self.assertNotIn("_run_raw_sync()", navigation_tail)

    def test_app_renders_total_and_platform_metric_sections(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertIn("本周期数据总览", overview_source)
        self.assertIn("_render_overview_summary_table(summary, platform_summary, channel_comparison)", overview_source)
        self.assertNotIn("_render_kpis(summary)", overview_source)
        self.assertNotIn('st.subheader("复盘统一字段")', overview_source)
        self.assertNotIn('st.subheader("环比变化")', overview_source)
        self.assertIn("_render_overview_summary_table(summary, platform_summary, channel_comparison)", overview_source)
        self.assertNotIn('st.subheader("总体核心指标")', overview_source)
        self.assertNotIn('st.subheader("分平台核心结果")', overview_source)
        self.assertNotIn("_render_growth_overview(channel_comparison)", overview_source)
        self.assertNotIn("_render_platform_kpis(platform_summary, channel_comparison)", overview_source)

    def test_overview_always_renders_period_comparison_table(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertNotIn("暂无上一同等级周期可用于环比。", overview_source)
        self.assertIn("_render_overview_summary_table(summary, platform_summary, channel_comparison)", overview_source)
        self.assertIn('return "（-）"', app_source)

    def test_overview_renders_unified_recap_fields(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _page_generate")]

        self.assertIn("build_recap_summary", app_source)
        self.assertNotIn('st.subheader("复盘统一字段")', overview_source)
        self.assertNotIn("st.table(recap_summary", overview_source)

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
        self.assertIn("_render_platform_chart(platform_summary, channel_comparison)", overview_source)
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

    def test_overview_cache_version_invalidates_comparison_schema_changes(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        load_source = app_source[app_source.index("def _load_overview_data") : app_source.index("def _items_period_level")]

        self.assertIn("OVERVIEW_CACHE_VERSION", app_source)
        self.assertIn("OVERVIEW_CACHE_VERSION", load_source)
        self.assertIn("cache_version", load_source)

    def test_overview_platform_chart_uses_current_previous_bars_and_growth_axis(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        platform_chart_source = app_source[
            app_source.index("def _build_platform_chart_figure") : app_source.index("def _content_filter_panel")
        ]

        self.assertIn("channel_comparison", platform_chart_source)
        self.assertIn('"本期"', platform_chart_source)
        self.assertIn('"上期"', platform_chart_source)
        self.assertIn('"环比"', platform_chart_source)
        self.assertIn("secondary_y=True", platform_chart_source)
        self.assertIn("yaxis2", platform_chart_source)

    def test_overview_platform_chart_uses_actual_metric_bars_without_relative_index(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        platform_chart_source = app_source[
            app_source.index("def _build_platform_chart_figure") : app_source.index("def _content_filter_panel")
        ]

        self.assertNotIn("__bar_scale", platform_chart_source)
        self.assertNotIn("__current_index", platform_chart_source)
        self.assertNotIn("__previous_index", platform_chart_source)
        self.assertNotIn("渠道内相对指数", platform_chart_source)
        self.assertIn("分渠道{y_label}对比", platform_chart_source)
        self.assertIn("本期实际{y_label}", platform_chart_source)
        self.assertIn("上期实际{y_label}", platform_chart_source)
        self.assertIn("yaxis.title.text", platform_chart_source)
        self.assertIn("str(meta[\"y_label\"])", platform_chart_source)
        self.assertIn("sort_values(", platform_chart_source)
        self.assertIn("[y_metric]", platform_chart_source)
        self.assertIn("本期实际", platform_chart_source)
        self.assertIn("上期实际", platform_chart_source)

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
        self.assertNotIn("文件备份", app_source)
        self.assertNotIn("渠道下钻分析", app_source)
        self.assertNotIn("B站全部", app_source)
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
        self.assertIn("重点内容类型贡献", channel_source)
        self.assertIn("重点内容类型贡献结论", channel_source)
        self.assertIn("st.markdown(topic_insights", channel_source)
        self.assertIn("页面只读取入库结果", channel_source)
        self.assertNotIn("重点题材消耗", channel_source)
        self.assertNotIn('st.subheader("重点题材分析")', channel_source)
        self.assertNotIn('"页面只读取入库题材"', channel_source)
        self.assertNotIn("group_topic_labels", channel_source)
        self.assertIn('st.subheader("栏目汇总")', channel_source)
        self.assertNotIn('f"{channel_name} 栏目消耗"', channel_source)
        self.assertNotIn('st.subheader("二级栏目汇总")', channel_source)
        self.assertNotIn("异常数据检测", channel_source)
        self.assertNotIn("_detect_and_display_anomalies", app_source)

    def test_channel_category_summary_is_single_table_with_category_first(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        channel_source = app_source[app_source.index("def _render_channel_page") : app_source.index("def _render_channel_summary_metrics")]
        category_display_source = app_source[
            app_source.index("def _category_table_display") : app_source.index("def _topic_table_display")
        ]

        self.assertIn("localize_columns(_category_table_display(category_summary))", channel_source)
        self.assertNotIn("_render_short_table_blocks(_category_table_display(category_summary)", channel_source)
        self.assertLess(category_display_source.index('"category_name"'), category_display_source.index('"item_count"'))

    def test_channel_pages_use_previous_period_comparison_for_metrics_categories_and_topics(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        channel_source = app_source[app_source.index("def _render_channel_page") : app_source.index("def _render_channel_summary_metrics")]

        self.assertIn("previous_batch_from_rows", app_source)
        self.assertIn("previous_batch_id", channel_source)
        self.assertIn("previous_items", channel_source)
        self.assertIn("channel_comparison", channel_source)
        self.assertIn("_render_channel_summary_metrics(channel_summary, channel_growth_row)", channel_source)
        self.assertIn("_render_period_comparison_bar_chart(", channel_source)
        self.assertIn("PERIOD_COMPARISON_CHART_HEIGHT", app_source)
        self.assertIn('__current_index"] = 100.0', app_source)
        self.assertIn('__previous_index"', app_source)
        self.assertIn("周期对比指数（本期=100）", app_source)
        self.assertIn("环比 ", app_source)
        self.assertIn("uniformtext", app_source)
        self.assertNotIn('"activations",\n            "激活数"', channel_source)
        self.assertIn("compare_channel_topics", channel_source)
        self.assertIn('"content_type"', channel_source)

    def test_channel_pages_show_top_content_links_only_for_supported_channels(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        dashboard_source = Path("ops_data_workflow/dashboard.py").read_text(encoding="utf-8")
        channel_source = app_source[app_source.index("def _render_channel_page") : app_source.index("def _render_channel_summary_metrics")]

        self.assertIn("summarize_channel_top_content_links", app_source)
        self.assertIn('st.subheader("消耗 Top5 素材案例")', channel_source)
        self.assertIn("_render_material_case_cards(top_content_links, channel_name)", channel_source)
        self.assertIn("笔记/视频链接", app_source)
        self.assertIn("封面/素材链接", app_source)
        self.assertIn("抖音", dashboard_source)
        self.assertIn("小红书", dashboard_source)
        self.assertIn("B站", dashboard_source)

    def test_topic_table_keeps_topic_and_content_type_first_without_rank_column(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        topic_display_source = app_source[
            app_source.index("def _topic_table_display") : app_source.index("def _topic_material_detail")
        ]
        detail_source = app_source[
            app_source.index("def _topic_material_detail") : app_source.index("def _render_short_table_blocks")
        ]
        short_table_source = app_source[
            app_source.index("def _render_short_table_blocks") : app_source.index("def _display_generation_results")
        ]

        self.assertLess(topic_display_source.index('"content_type"'), topic_display_source.index('"spend_share"'))
        for column in [
            '"topic_name"',
            '"content_types"',
            '"item_count"',
            '"material_count"',
            '"spend_previous"',
            '"clicks"',
            '"ctr"',
            '"material_id"',
        ]:
            self.assertNotIn(column, topic_display_source)
        self.assertNotIn('"rank_position"', topic_display_source)
        self.assertNotIn('"material_id"', detail_source)
        self.assertIn("hide_index=True", short_table_source)
        self.assertIn("preserve_order=True", app_source)

    def test_conflict_priority_review_helper_remains_available_for_review_flows(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        review_helper_source = app_source[
            app_source.index("def _render_conflict_priority_review") : app_source.index("def _page_reference_tables")
        ]

        self.assertIn('st.subheader("冲突优先审核")', review_helper_source)
        self.assertIn('"影响消耗"', review_helper_source)
        self.assertIn("def _review_issue_priority", review_helper_source)

    def test_navigation_uses_dynamic_channel_page_factory(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        navigation_source = app_source[app_source.index("def _build_navigation_pages") : app_source.rindex("_inject_theme()")]

        self.assertIn("def _overview_page()", app_source)
        self.assertIn("st.Page(_page_overview, title=\"总览\", default=True)", app_source)
        self.assertIn("def _generate_page()", app_source)
        self.assertIn("st.Page(_page_generate, title=\"生成页面数据\")", app_source)
        self.assertIn("_make_channel_page(channel)", app_source)
        self.assertIn("title=channel", app_source)
        self.assertIn("_channel_pages_for_current_period()", navigation_source)
        self.assertIn("page = st.navigation(_build_navigation_pages()", app_source)

    def test_overview_channel_links_use_current_page_navigation(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        links_source = app_source[app_source.index("def _render_channel_links") : app_source.index("def _top_content_cases_for_report")]

        self.assertIn("st.page_link", links_source)
        self.assertIn("_channel_pages_for_current_period()", links_source)
        self.assertNotIn("<a href=", links_source)
        self.assertNotIn("_channel_page_href(channel, batch_id)", links_source)

    def test_short_table_blocks_hide_dataframe_index(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        table_source = app_source[
            app_source.index("def _render_short_table_blocks") : app_source.index("def _display_generation_results")
        ]

        self.assertIn("st.dataframe", table_source)
        self.assertIn("hide_index=True", table_source)
        self.assertNotIn("st.table", table_source)

    def test_generate_page_uses_ui_only_mode_without_download_exports(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        run_source = app_source[app_source.index("def _run_with_generation_progress") : app_source.index("def _render_rollup_generator")]
        display_source = app_source[app_source.index("def _display_generation_results") : app_source.index("def _render_kpis")]
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _render_overview_summary_table")]

        self.assertIn('output_mode="ui_only"', run_source)
        self.assertIn("enable_deepseek=True", run_source)
        self.assertIn("enable_external_context=False", run_source)
        self.assertIn("AI 初审", app_source)
        self.assertIn("数据清洗并入库完成", display_source)
        self.assertIn("AI 复盘报告请到“总览”页手动生成", display_source)
        self.assertNotIn('st.markdown(st.session_state["ai_summary"])', display_source)
        self.assertIn("手动生成/更新 AI 复盘", overview_source)
        self.assertIn("load_manual_recap_report", app_source)
        self.assertIn("persist_manual_recap_report", app_source)
        self.assertNotIn("下载 HTML 报告", display_source)
        self.assertNotIn("下载 Excel 明细", display_source)
        self.assertNotIn("下载标准 CSV", display_source)
        self.assertNotIn("下载总结果表", display_source)

    def test_manual_ai_recap_generation_has_stepwise_progress(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        recap_source = app_source[app_source.index("def _render_manual_recap_controls") : app_source.index("def _render_manual_recap_overview")]
        progress_source = app_source[
            app_source.index("MANUAL_RECAP_PROGRESS_STEPS") : app_source.index("def _render_manual_recap_overview")
        ]

        self.assertIn("MANUAL_RECAP_PROGRESS_STEPS", app_source)
        self.assertIn("MANUAL_RECAP_PROGRESS_VALUES", app_source)
        self.assertIn("_run_manual_recap_generation_with_progress(", recap_source)
        self.assertIn("st.status", progress_source)
        self.assertIn("st.progress", progress_source)
        self.assertIn("正在整理复盘证据", progress_source)
        self.assertIn("正在请求 AI 生成结构化复盘", progress_source)
        self.assertIn("正在保存 AI 复盘报告", progress_source)
        self.assertIn("AI 复盘报告生成完成", progress_source)

    def test_overview_keeps_data_evidence_before_single_manual_recap(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _render_manual_recap_controls")]
        recap_source = app_source[app_source.index("def _render_manual_recap_controls") : app_source.index("def _render_channel_links")]

        self.assertIn('st.subheader("本周期数据总览")', overview_source)
        self.assertIn("_render_overview_summary_table(summary, platform_summary, channel_comparison)", overview_source)
        self.assertIn('st.subheader("分渠道图")', overview_source)
        self.assertIn("_render_platform_chart(platform_summary, channel_comparison)", overview_source)
        self.assertIn("_render_manual_recap_controls(", overview_source)
        self.assertLess(overview_source.index('st.subheader("本周期数据总览")'), overview_source.index("_render_manual_recap_controls("))
        self.assertLess(overview_source.index('st.subheader("分渠道图")'), overview_source.index("_render_manual_recap_controls("))
        self.assertNotIn('st.subheader("内容题材建议摘要")', overview_source)
        self.assertNotIn("st.markdown(recommendations)", overview_source)
        self.assertIn("下周期总体方向", recap_source)
        self.assertIn("_render_manual_recap_overview(saved.get(\"report\", {}))", recap_source)
        self.assertIn("_render_manual_recap_sections(overview.get(\"sections\", []))", recap_source)
        self.assertIn("整体结论", recap_source)
        self.assertIn("_manual_recap_should_render_direction(overview.get(\"sections\", []))", recap_source)

    def test_manual_recap_uses_structured_section_renderer_with_legacy_fallback(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _render_manual_recap_overview") : app_source.index("def _render_channel_links")]
        channel_source = app_source[app_source.index("def _render_manual_recap_channel") : app_source.index("def _render_material_case_cards")]
        helper_source = app_source[
            app_source.index("def _render_manual_recap_sections") : app_source.index("def _manual_recap_paragraph_html")
        ]
        direction_helper_source = app_source[
            app_source.index("def _manual_recap_should_render_direction") : app_source.index("def _render_channel_links")
        ]

        self.assertIn("_render_manual_recap_sections(overview.get(\"sections\", []))", overview_source)
        self.assertIn("_manual_recap_paragraph_html(\"整体结论\"", overview_source)
        self.assertIn("_render_manual_recap_sections(match.get(\"sections\", []))", channel_source)
        self.assertIn("_manual_recap_paragraph_html(\"表现判断 / 有效素材 / 原因判断\"", channel_source)
        self.assertIn("_manual_recap_should_render_direction(match.get(\"sections\", []))", channel_source)
        self.assertIn("manual-recap-sections", helper_source)
        self.assertIn("manual-recap-section", helper_source)
        self.assertIn("<ul>", helper_source)
        self.assertIn("<li>", helper_source)
        self.assertIn("html.escape", helper_source)
        self.assertIn("下周期动作", direction_helper_source)
        self.assertIn("下一周期执行动作", direction_helper_source)
        self.assertIn("return False", direction_helper_source)

    def test_channel_pages_render_clickable_top5_material_cards(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        channel_source = app_source[app_source.index("def _render_channel_page") : app_source.index("def _render_channel_summary_metrics")]
        cards_source = app_source[app_source.index("def _render_material_case_cards") : app_source.index("def _topic_material_detail")]

        self.assertIn("返回总览", channel_source)
        self.assertIn("消耗 Top5 素材案例", channel_source)
        self.assertIn("_render_material_case_cards(top_content_links, channel_name)", channel_source)
        self.assertIn("_render_channel_links(platform_summary)", app_source)
        self.assertIn("st.query_params.get(\"batch_id\"", app_source)
        self.assertIn("quote(str(batch_id), safe='')", app_source)
        self.assertNotIn('href="#channel-', app_source)
        self.assertIn('target="_blank"', cards_source)
        self.assertIn("cover-toggle", app_source)
        self.assertIn("cover-preview-backdrop", app_source)
        self.assertIn("cover-preview-dialog", app_source)

    def test_manual_recap_receives_topic_context_as_evidence_input(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        overview_source = app_source[app_source.index("def _page_overview") : app_source.index("def _render_manual_recap_controls")]
        recap_source = app_source[app_source.index("def _render_manual_recap_controls") : app_source.index("def _render_manual_recap_overview")]

        self.assertIn("_channel_topic_context_for_report(selected_batch_id, platform_summary)", overview_source)
        self.assertIn("recommendations", recap_source)
        self.assertIn("channel_topic_context", recap_source)
        self.assertIn("overview_recommendations=recommendations", recap_source)
        self.assertIn("channel_topic_context=channel_topic_context", recap_source)
        self.assertIn("period_level=", recap_source)

    def test_channel_topic_context_is_evidence_not_overview_copy(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        context_source = app_source[
            app_source.index("def _channel_topic_context_for_report") : app_source.index("def _top_content_cases_for_report")
        ]

        self.assertIn("topic_insights", context_source)
        self.assertIn("top_topics", context_source)
        self.assertNotIn("overview_topic_line", context_source)
        self.assertNotIn("重点题材联动", context_source)

    def test_channel_pages_show_ai_analysis_before_evidence_sections(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        channel_source = app_source[app_source.index("def _render_channel_page") : app_source.index("def _render_channel_summary_metrics")]
        recap_channel_source = app_source[app_source.index("def _render_manual_recap_channel") : app_source.index("def _render_material_case_cards")]

        self.assertLess(channel_source.index("_render_manual_recap_channel"), channel_source.index('st.subheader("渠道核心指标")'))
        self.assertIn("AI 渠道复盘建议", recap_channel_source)
        self.assertNotIn("渠道深度分析", recap_channel_source)
        self.assertIn("表现判断 / 有效素材 / 原因判断", recap_channel_source)
        self.assertNotIn("素材表现 / 题材表现 / 内容类型表现 / 归因分析", recap_channel_source)
        self.assertIn("_render_manual_recap_sections(match.get(\"sections\", []))", recap_channel_source)
        self.assertIn("下一周期执行方向", recap_channel_source)

    def test_channel_ai_recap_filters_legacy_topic_sections_before_rendering(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        helper_source = app_source[
            app_source.index("def _render_manual_recap_sections") : app_source.index("def _manual_recap_paragraph_html")
        ]

        self.assertIn("_manual_recap_visible_section", helper_source)
        self.assertIn("题材/内容类型", helper_source)
        self.assertIn("return False", helper_source)

    def test_app_imports_batch_scoped_auxiliary_loaders(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        dashboard_imports = app_source[
            app_source.index("from ops_data_workflow.dashboard import (") : app_source.index("from ops_data_workflow.reporting")
        ]

        self.assertNotIn("load_data_quality_for_batch", dashboard_imports)
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
        trend_source = app_source[app_source.index("def _page_trends") : app_source.index("def _render_conflict_priority_review")]

        self.assertNotIn('value=st.session_state["generate_period_start"]', generate_source)
        self.assertNotIn('value=st.session_state["generate_period_end"]', generate_source)
        self.assertNotIn('value=st.session_state["trend_period_start"]', trend_source)
        self.assertNotIn('value=st.session_state["trend_period_end"]', trend_source)
        self.assertNotIn('default=st.session_state.get("trend_quick_range"', trend_source)

    def test_historical_trends_render_period_metric_grid_instead_of_content_type_trends(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        trend_source = app_source[app_source.index("def _page_trends") : app_source.index("def _render_conflict_priority_review")]

        self.assertIn("summarize_period_metric_trends", app_source)
        self.assertNotIn("summarize_content_type_trends", app_source)
        self.assertNotIn("最终内容类别", trend_source)
        self.assertNotIn("图中展示 Top 内容类型数", trend_source)
        self.assertIn("周/月", trend_source)
        self.assertIn("最近 8 周", app_source)
        self.assertIn("最近 12 个月", app_source)
        self.assertIn("_render_period_metric_trend_grid", trend_source)
        self.assertIn("_render_period_metric_chart", app_source)
        for label in ["总消耗", "总曝光", "激活数", "激活成本", "付费数", "付费成本"]:
            self.assertIn(label, app_source)

    def test_historical_trends_use_selected_period_level_and_compact_axis_codes(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        trend_source = app_source[app_source.index("def _page_trends") : app_source.index("def _render_conflict_priority_review")]
        axis_source = app_source[
            app_source.index("def _trend_axis_label") : app_source.index("def _trend_available_period_count")
        ]

        self.assertIn("summarize_period_metric_trends(", trend_source)
        self.assertIn("selected_level,", trend_source)
        self.assertNotIn("week_storage_key", app_source)
        self.assertIn('strftime("%Y%m")', axis_source)
        self.assertNotIn('strftime("%Y-%m")', axis_source)
        self.assertIn('strftime("%Y%m%d")', axis_source)

    def test_app_renders_single_item_category_review_flow(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        review_source = app_source[app_source.index("def _page_category_review") : app_source.index("def _render_channel_page")]

        self.assertIn("内容审核", app_source)
        self.assertIn("build_top_content_review_queue", app_source)
        self.assertIn("AI 初审", review_source)
        self.assertIn("人工异常队列", review_source)
        self.assertIn("AI 已通过", review_source)
        self.assertIn("缺链接", app_source)
        self.assertIn("内容链接", app_source)
        self.assertIn("打开校验", review_source)
        self.assertIn("快捷内容类型", review_source)
        self.assertIn("保存并下一条", review_source)
        self.assertIn("apply_review_resolutions_and_regenerate", app_source)
        self.assertIn("category_confidence", app_source)
        self.assertNotIn('("账号"', review_source)
        self.assertNotIn("三级题材", review_source)
        self.assertNotIn('"category_l3"', review_source)

    def test_app_hides_removed_pages_and_tertiary_topic_ui(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        reporting_source = Path("ops_data_workflow/reporting.py").read_text(encoding="utf-8")
        navigation_source = app_source[app_source.index("def _build_navigation_pages") : app_source.rindex("_inject_theme()")]

        self.assertNotIn("内容类型统计", navigation_source)
        self.assertNotIn("数据审核", navigation_source)
        self.assertNotIn("内容明细", navigation_source)
        self.assertNotIn("数据质量", navigation_source)
        self.assertNotIn("_page_content_types", navigation_source)
        self.assertNotIn("_page_data_review", navigation_source)
        self.assertNotIn("_page_content_details", navigation_source)
        self.assertNotIn("_page_data_quality", navigation_source)
        self.assertNotIn("def _page_content_types", app_source)
        self.assertNotIn("def _page_data_review", app_source)
        self.assertNotIn("三级题材", app_source)
        self.assertNotIn("三级题材", reporting_source)

    def test_app_keeps_data_review_backend_sync_available_without_page(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        navigation_source = app_source[app_source.index("def _build_navigation_pages") : app_source.rindex("_inject_theme()")]

        self.assertIn("review_resolutions", app_source)
        self.assertIn("apply_review_resolutions_and_regenerate", app_source)
        self.assertIn("save_review_resolutions", app_source)
        self.assertNotIn("数据审核", navigation_source)
        self.assertNotIn('st.Page(_page_data_review, title="数据审核")', navigation_source)


if __name__ == "__main__":
    unittest.main()
