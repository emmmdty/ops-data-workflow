from pathlib import Path
import ast
import re
import unittest


class StreamlitCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_source = Path("app.py").read_text(encoding="utf-8")

    def test_app_uses_current_width_parameter(self):
        self.assertNotIn("use_container_width", self.app_source)

    def test_app_runtime_paths_can_be_overridden_for_isolated_demo(self):
        for token in [
            "def _app_path_from_env",
            '"OPS_DATA_ROOT"',
            '"OPS_PROCESSED_ROOT"',
            '"OPS_WORKFLOW_DB"',
            '"OPS_OUTPUTS_ROOT"',
        ]:
            self.assertIn(token, self.app_source)

    def test_builtin_english_table_toolbar_is_hidden(self):
        theme_source = self._function_source("_inject_theme", "def _page_overview")

        for token in ["stHeaderActionElements", "stAppDeployButton", "Show/hide columns", "Download as CSV", "Search", "Fullscreen"]:
            self.assertIn(token, theme_source)
        self.assertIn("display: none !important", theme_source)

    def test_upload_widget_default_english_copy_is_masked(self):
        theme_source = self._function_source("_inject_theme", "def _page_overview")

        for token in [
            "stFileUploaderDropzoneInstructions",
            "拖拽文件夹到这里",
            "支持单个或多个渠道数据文件",
            "选择文件夹",
        ]:
            self.assertIn(token, theme_source)

    def test_navigation_is_limited_to_current_product_pages(self):
        titles = re.findall(r"st\.Page\([^)]*title=\"([^\"]+)\"", self.app_source)

        self.assertEqual(
            titles,
            ["总览", "上传清洗", "高价值内容复盘", "本地总表", "周期数据", "历史趋势"],
        )
        self.assertIn("st.navigation", self.app_source)
        self.assertIn("st.Page", self.app_source)

    def test_sidebar_navigation_is_not_hidden_by_header_css(self):
        theme_source = self._function_source("_inject_theme", "def _page_overview")

        self.assertIn('initial_sidebar_state="collapsed"', self.app_source)
        self.assertIn('position="sidebar"', self.app_source)
        self.assertNotRegex(theme_source, r'(?m)^\s*\[data-testid="stToolbar"\],\s*$')
        self.assertNotIn('header [data-testid="stToolbar"]', theme_source)
        self.assertNotRegex(theme_source, r'\[data-testid="stSidebar"\]\s*\{[^}]*display:\s*none')
        self.assertNotRegex(theme_source, r'\[data-testid="stSidebar"\]\s*\{[^}]*visibility:\s*hidden')
        self.assertNotIn("collapsedControl", theme_source)
        self.assertIn("stExpandSidebarButton", theme_source)
        self.assertNotRegex(theme_source, r'\[data-testid="stExpandSidebarButton"\]\s*\{[^}]*display:\s*none')
        self.assertNotRegex(theme_source, r'\[data-testid="stExpandSidebarButton"\]\s*\{[^}]*visibility:\s*hidden')
        self.assertNotIn("header button[kind]", theme_source)
        self.assertNotIn("header button[title]", theme_source)

    def test_removed_legacy_product_surfaces_are_not_exposed(self):
        removed_labels = [
            "数据分析/归因",
            "维护台账",
            "手动生成/更新 AI 复盘",
            "DeepSeek",
            "重算历史批次",
            "渠道栏目分析",
            "总览与推荐",
        ]
        for label in removed_labels:
            self.assertNotIn(label, self.app_source)

        for legacy_helper in [
            "generate_manual_recap_report",
            "resolve_deepseek_settings",
            "persist_manual_recap_report",
            "refresh_historical_source_periods",
            "build_attribution_tables",
            "_render_channel_page",
            "_make_channel_page",
        ]:
            self.assertNotIn(legacy_helper, self.app_source)

    def test_upload_cleaning_page_supports_directory_upload_and_all_period_levels(self):
        upload_source = self._function_source("_page_upload_cleaning", "_page_high_value_recap")

        self.assertIn('accept_multiple_files="directory"', upload_source)
        self.assertIn("_chinese_date_input", upload_source)
        self.assertIn("月", self._function_source("_chinese_date_input", "def _run_upload_cleaning"))
        self.assertNotIn(".date_input(", upload_source)
        self.assertIn("materialize_uploaded_files", self.app_source)
        self.assertIn("run_archived_workflow", self.app_source)
        self.assertIn("detect_upload_channel_conflicts", self.app_source)
        self.assertIn("PERIOD_LEVELS", self.app_source)
        for label in ["周", "月", "季度", "年度"]:
            self.assertIn(label, self.app_source)

    def test_quarter_and_year_rollups_are_generated_inside_upload_cleaning(self):
        upload_source = self._function_source("_page_upload_cleaning", "_page_high_value_recap")

        self.assertIn("rollup_period_for", self.app_source)
        self.assertIn("select_rollup_component_batches", self.app_source)
        self.assertIn("run_rollup_workflow", self.app_source)
        self.assertIn("生成季度/年度汇总", upload_source)
        self.assertIn("_rollup_components_display", upload_source)
        self.assertNotIn('{"batch_id": components}', upload_source)
        self.assertNotIn('title="季度/年度汇总"', self.app_source)

    def test_high_value_recap_persists_weights_and_uses_top_asset_cache_flow(self):
        recap_source = self._function_source("_page_high_value_recap", "_page_local_assets")

        self.assertIn("get_recap_settings", self.app_source)
        self.assertIn("update_recap_settings", self.app_source)
        self.assertIn("渠道消耗前 5", recap_source)
        self.assertIn("_render_channel_top_link_cards", recap_source)
        self.assertIn("_build_local_recap_tables", recap_source)
        self.assertIn("activation_weight", recap_source)
        self.assertIn("first_pay_weight", recap_source)
        self.assertIn("build_executable_top_content_pool", self.app_source)
        self.assertIn("cache_existing_harvester_assets_for_batch", self.app_source)
        self.assertIn("run_harvester_asset_capture", self.app_source)
        self.assertIn("run_top_multimodal_analysis_from_manifests", self.app_source)
        self.assertIn("persist_multimodal_recap", self.app_source)
        self.assertIn("persist_type_recap_from_top_content", self.app_source)
        self.assertIn("ANALYSIS_PURPOSE_FILL_MISSING_TYPE", self.app_source)
        self.assertIn("ANALYSIS_PURPOSE_STRATEGY_RECAP", self.app_source)
        self.assertIn("生成/更新类型复盘", recap_source)
        self.assertIn("多模态补缺失类型", recap_source)
        self.assertIn("生成/更新策略复盘", recap_source)
        self.assertIn("list_strategy_recap_items", self.app_source)
        self.assertIn("缓存占用", recap_source)
        self.assertIn("清理缓存", recap_source)
        self.assertIn("_asset_cache_status_summary", self.app_source)
        self.assertIn("cleanup_top_asset_cache", self.app_source)

    def test_upload_page_exposes_tier1_auto_recap_as_separate_state(self):
        upload_source = self._function_source("_page_upload_cleaning", "_page_high_value_recap")

        self.assertIn("清洗完成后自动补采并分析一级素材", upload_source)
        self.assertIn("清洗成功和复盘任务分开提示", upload_source)
        self.assertIn("复盘失败不会回滚清洗入库结果", upload_source)
        self.assertIn("二级曝光范围和三级阈值范围", upload_source)
        self.assertIn("auto_tier1_recap_after_upload", upload_source)

    def test_recap_tier_ui_runs_scoped_llm_reports_without_legacy_imports(self):
        recap_source = self._function_source("_render_recap_tier_panel", "def _render_range_recap_report")
        report_source = self._function_source("_render_range_recap_report", "def _render_high_value_quality_tab")
        pipeline_source = self._function_source("_run_recap_tier_pipeline", "def _run_rollup")

        for token in [
            "分级复盘任务",
            "一级可在上传清洗后自动触发",
            "每个范围会生成独立 LLM 报告",
            "RECAP_TIER_1_SPEND_TOP",
            "RECAP_TIER_2_EXPOSURE_TOP",
            "RECAP_TIER_3_THRESHOLD",
            "generate_range_recap_report",
            "persist_range_recap_report",
        ]:
            self.assertIn(token, self.app_source)
        self.assertIn("load_range_recap_report", recap_source)
        self.assertNotIn("analysis_jobs", report_source)
        self.assertIn("analysis_purpose=purpose", pipeline_source)
        self.assertIn("return True", pipeline_source)
        self.assertIn("return False", pipeline_source)
        self.assertIn("if _run_recap_tier_pipeline(batch_id, items, tier_key):", recap_source)
        self.assertNotIn("_run_recap_tier_pipeline(batch_id, items, tier_key)\n                    st.rerun()", recap_source)

    def test_missing_type_multimodal_uses_fill_missing_scope(self):
        recap_source = self._function_source("_render_high_value_evidence_tab", "def _render_recap_tier_panel")
        missing_type_source = recap_source[
            recap_source.index('if c4.button("多模态补缺失类型"') : recap_source.index('if c5.button("生成/更新策略复盘"')
        ]

        self.assertIn("ANALYSIS_PURPOSE_FILL_MISSING_TYPE", missing_type_source)
        self.assertIn("analysis_purpose=ANALYSIS_PURPOSE_FILL_MISSING_TYPE", missing_type_source)
        self.assertNotIn("analysis_purpose=purpose", missing_type_source)

    def test_strategy_multimodal_uses_default_strategy_scope(self):
        recap_source = self._function_source("_render_high_value_evidence_tab", "def _render_recap_tier_panel")
        strategy_source = recap_source[recap_source.index('if c5.button("生成/更新策略复盘"') :]

        self.assertIn("ANALYSIS_PURPOSE_STRATEGY_RECAP", strategy_source)
        self.assertIn("analysis_purpose=ANALYSIS_PURPOSE_STRATEGY_RECAP", strategy_source)

    def test_high_value_capture_progress_shows_eta_and_background_status(self):
        recap_source = self._function_source("_page_high_value_recap", "_page_local_assets")

        self.assertIn("_harvester_progress_text", recap_source)
        self.assertIn("预计还需", self.app_source)
        self.assertIn("后台静默", self.app_source)
        self.assertIn("剩余", self.app_source)
        self.assertIn("time.monotonic", recap_source)

    def test_channel_top5_cards_use_b2_cover_metric_layout(self):
        card_source = self._function_source("_render_channel_top_link_cards", "def _build_local_recap_tables")

        for token in [
            "top-link-card",
            "top-link-cover",
            "top-link-metrics",
            "top-link-metric-main",
            "with st.expander",
            "expanded=channel_index == 0",
            "点击放大",
            "封面大图预览",
            "_top_cover_lookup",
            "_image_data_uri",
            "_safe_html",
            "激活 / 成本",
            "付费 / 成本",
        ]:
            self.assertIn(token, self.app_source)
        self.assertIn("list_harvester_asset_manifests(APP_DB, batch_id=batch_id)", self.app_source)
        self.assertIn("asset_key", card_source)
        self.assertNotIn("st.columns(2)", card_source)
        self.assertNotIn("metric(\"价值\"", card_source)

    def test_channel_top5_lives_in_report_tab_with_explicit_manifests(self):
        report_source = self._function_source("_render_high_value_report_tab", "def _render_high_value_evidence_tab")
        evidence_source = self._function_source("_render_high_value_evidence_tab", "def _render_high_value_quality_tab")

        self.assertIn("manifests: pd.DataFrame", report_source)
        self.assertIn('st.subheader("渠道消耗前 5")', report_source)
        self.assertIn("_render_channel_top_link_cards(top_pool, manifests=manifests)", report_source)
        self.assertNotIn("list_harvester_asset_manifests", report_source)
        self.assertNotIn("渠道消耗前 5", evidence_source)
        self.assertNotIn("_render_channel_top_link_cards", evidence_source)

    def test_overview_and_local_tables_use_new_cleaning_recap_tables(self):
        for token in [
            "list_period_channel_totals",
            "list_content_performance_items",
            "list_local_content_assets",
            "list_harvester_asset_manifests",
            "list_multimodal_recap_items",
            "list_type_recap_items",
        ]:
            self.assertIn(token, self.app_source)

    def test_local_recap_metrics_wrap_to_two_rows(self):
        recap_source = self._function_source("_render_local_recap_tables", "def _display_channel_recap_columns")
        page_source = self._function_source("_page_high_value_recap", "_page_local_assets")

        self.assertIn("_metric_row_chunks", recap_source)
        self.assertIn("max_columns=3", recap_source)
        self.assertNotIn("st.columns(6)", recap_source)
        self.assertIn("local-recap-metric", recap_source)
        self.assertIn("local-recap-share", recap_source)
        self.assertIn("local-recap-note", recap_source)
        self.assertIn("_overview_metrics(", page_source)
        self.assertIn("total_metrics=", page_source)

    def test_overview_channel_section_renders_table_without_metric_cards(self):
        overview_source = self._function_source("_page_overview", "_page_upload_cleaning")

        self.assertIn("分渠道总览", overview_source)
        self.assertIn("previous_totals=previous_totals", overview_source)
        self.assertIn("previous_items=previous_items", overview_source)
        self.assertIn("_render_channel_totals_table(channel_totals)", overview_source)
        self.assertGreater(overview_source.index('st.subheader("分渠道总览")'), overview_source.index("_render_recap_weight_settings"))
        self.assertGreater(overview_source.index('st.subheader("分渠道总览")'), overview_source.index("_render_metric_row(metrics, previous_metrics)"))
        self.assertLess(overview_source.index('st.subheader("分渠道总览")'), overview_source.index("status_metrics = _overview_status_metrics"))
        self.assertNotIn("_render_channel_total_cards", overview_source)
        self.assertNotIn("分渠道明细表", overview_source)
        self.assertIn("channel-overview-table", self.app_source)
        self.assertIn("channel-value", self.app_source)
        self.assertIn("channel-delta", self.app_source)

    def test_trends_remain_core_metrics_only(self):
        trend_source = self._function_source("_page_trends", "PAGES =")

        self.assertIn("summarize_period_metric_trends", self.app_source)
        for label in ["消耗", "曝光", "激活数", "付费数", "激活成本", "付费成本"]:
            self.assertIn(label, trend_source)
        self.assertNotIn("Topic", trend_source)

    def test_user_facing_copy_hides_internal_table_and_cache_terms(self):
        visible_strings = [
            value
            for value in _string_literals(self.app_source)
            if any(ch >= "\u4e00" and ch <= "\u9fff" for ch in value)
        ]
        visible_copy = "\n".join(visible_strings)

        for forbidden in [
            "content_assets",
            "manifest",
            "harvester",
            "workbook",
            "batch_id",
            "raw_dir",
            "原始文件目录",
            "上传路径",
            "TopN",
            "CSV",
            "ZIP",
            "Excel",
            "单行渠道总数据",
        ]:
            self.assertNotIn(forbidden, visible_copy)
        for expected in ["本地总表", "飞书快照", "素材缓存记录", "清洗完成", "本周期"]:
            self.assertIn(expected, visible_copy)

    def _function_source(self, start: str, end: str) -> str:
        return self.app_source[self.app_source.index(f"def {start}") : self.app_source.index(end)]


def _string_literals(source: str) -> list[str]:
    module = ast.parse(source)
    return [node.value for node in ast.walk(module) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
