from __future__ import annotations

import base64
import html
import json
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components

from ops_data_workflow.ai import generate_manual_recap_report, resolve_deepseek_settings
from ops_data_workflow.dashboard import (
    AI_REVIEW_AUTO_PASS_THRESHOLD,
    DashboardFilters,
    aggregate_dashboard,
    build_channel_top_topic_insights,
    build_overview_table_rows,
    build_period_comparison_for_batch,
    build_content_recommendations,
    build_dashboard_summary,
    build_top_content_review_queue,
    compare_channel_topics,
    filter_dashboard_items,
    format_beijing_datetime,
    load_all_dashboard_items,
    load_channel_comparison_for_batch,
    load_dashboard_items_for_batch,
    load_latest_data_quality,
    load_latest_dashboard_items,
    load_latest_review_queue,
    load_review_queue_for_batch,
    list_successful_dashboard_batches,
    metric_sort_ascending,
    summarize_period_metric_trends,
    summarize_channel_category_comparison,
    summarize_channel_categories,
    summarize_channel_top_content_links,
    summarize_topics_for_selection,
    summarize_content_types,
    summarize_unique_content,
)
from ops_data_workflow.reporting import format_display_number, localize_columns, localize_and_sort_columns
from ops_data_workflow.account_filters import load_account_filter_config
from ops_data_workflow.channel_profiles import load_channel_profiles, render_channel_profiles_table
from ops_data_workflow.reference_tables import load_reference_tables
from ops_data_workflow.raw_sync import sync_raw_periods
from ops_data_workflow.periods import PERIOD_LEVEL_LABELS, PERIOD_LEVELS, PERIOD_LEVEL_MONTH, PERIOD_LEVEL_WEEK, SOURCE_TYPE_UPLOAD, review_period_from_dates
from ops_data_workflow.raw_normalization import (
    detect_normalized_upload_channel_conflicts,
    normalize_uploaded_periods,
    preview_uploaded_period_buckets,
    preview_uploaded_periods,
)
from ops_data_workflow.recap import build_recap_summary
from ops_data_workflow.review_resolutions import (
    apply_review_resolutions_and_regenerate,
    save_review_resolutions,
)
from ops_data_workflow.rollups import rollup_period_for, select_rollup_component_batches
from ops_data_workflow.storage import (
    load_manual_recap_report,
    load_topic_labels_for_batch,
    persist_manual_recap_report,
    previous_batch_from_rows,
    upsert_category_mappings,
)
from ops_data_workflow.source_storage import source_dir_for_period
from ops_data_workflow.topic_analysis import (
    channel_topic_limit,
    summarize_persisted_content_types,
    summarize_persisted_topic_labels,
)
from ops_data_workflow.upload_input import detect_upload_channel_conflicts, infer_period_from_upload_names, materialize_uploaded_files
from ops_data_workflow.workflow import refresh_historical_source_periods, run_archived_workflow, run_rollup_workflow


def _app_path_from_env(name: str, default: str) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else Path(default)


APP_DATA_ROOT = _app_path_from_env("OPS_DATA_ROOT", "data")
APP_PROCESSED = _app_path_from_env("OPS_PROCESSED_ROOT", "processed")
APP_DB = _app_path_from_env("OPS_WORKFLOW_DB", ".runtime/workflow.sqlite3")
APP_OUTPUTS = _app_path_from_env("OPS_OUTPUTS_ROOT", "outputs")
OVERVIEW_CACHE_VERSION = 2
CATEGORY_RULES = Path("config/category_rules.yml")
ENV_PATH = Path(".env")
TREND_PERIOD_LEVELS = [PERIOD_LEVEL_WEEK, PERIOD_LEVEL_MONTH]
TREND_WINDOW_OPTIONS = {
    PERIOD_LEVEL_WEEK: [("最近 8 周", 8), ("最近 6 周", 6), ("全部", None)],
    PERIOD_LEVEL_MONTH: [("最近 12 个月", 12), ("最近 6 个月", 6), ("全部", None)],
}
CHART_METRICS = {
    "总消耗": ("spend", "总消耗", False),
    "总曝光": ("impressions", "总曝光", False),
    "激活数": ("activations", "激活数", False),
    "激活成本": ("activation_cost", "激活成本", True),
    "付费数": ("first_pay_count", "付费数", False),
    "付费成本": ("first_pay_cost", "付费成本", True),
}
GENERATION_PROGRESS_STEPS = (
    "正在识别上传文件和复盘周期",
    "正在整理源文件周期目录",
    "正在整理清洗产物",
    "正在读取渠道数据并标准化",
    "正在校验字段完整性与内容类型",
    "正在固化重点题材",
    "正在写入周期库",
    "页面数据生成完成",
)
GENERATION_PROGRESS_VALUES = {
    "正在识别上传文件和复盘周期": 5,
    "正在整理源文件周期目录": 15,
    "正在整理清洗产物": 25,
    "正在读取渠道数据并标准化": 45,
    "正在校验字段完整性与内容类型": 70,
    "正在固化重点题材": 82,
    "正在写入周期库": 90,
    "页面数据生成完成": 95,
}
MANUAL_RECAP_PROGRESS_STEPS = (
    "正在整理复盘证据",
    "正在请求 AI 生成结构化复盘",
    "正在保存 AI 复盘报告",
    "AI 复盘报告生成完成",
)
MANUAL_RECAP_PROGRESS_VALUES = {
    "正在整理复盘证据": 15,
    "正在请求 AI 生成结构化复盘": 45,
    "正在保存 AI 复盘报告": 85,
    "AI 复盘报告生成完成": 100,
}
GROWTH_METRICS = {
    "消耗": ("spend_current", "spend_change_rate", 0, "normal"),
    "激活数": ("activations_current", "activations_change_rate", 0, "normal"),
    "付费数": ("first_pay_count_current", "first_pay_count_change_rate", 0, "normal"),
    "激活成本": ("activation_cost_current", "activation_cost_change_rate", 1, "inverse"),
    "付费成本": ("first_pay_cost_current", "first_pay_cost_change_rate", 1, "inverse"),
}
BAR_COLOR_SEQUENCE = [
    "#0A84FF",
    "#30B0C7",
    "#34C759",
    "#FF9F0A",
    "#FF453A",
    "#BF5AF2",
    "#64D2FF",
    "#FFD60A",
    "#5E5CE6",
    "#FF375F",
]
BAR_CONTINUOUS_SCALE = ["#D7ECFF", "#64D2FF", "#0A84FF", "#0B3D91"]
RATE_METRIC_COLUMNS = {"ctr", "first_pay_rate", "spend_change_rate", "activations_change_rate", "first_pay_count_change_rate", "activation_cost_change_rate", "first_pay_cost_change_rate"}
SOURCE_TYPE_LABELS = {SOURCE_TYPE_UPLOAD: "上传原始包", "rollup": "系统汇总"}
PERIOD_COMPARISON_CHART_HEIGHT = 420


def _get_logo_base64() -> str:
    logo_path = Path(__file__).parent / "brand_logo.png"
    if logo_path.exists():
        with open(logo_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""


_logo_base64 = _get_logo_base64()
st.set_page_config(page_title="原生内容投放分析工作台", layout="wide")


def _inject_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --app-bg: radial-gradient(circle at top left, #f4f7fb 0%, #edf2f9 45%, #e8eef6 100%);
            --surface: rgba(255, 255, 255, 0.78);
            --surface-strong: rgba(255, 255, 255, 0.92);
            --surface-border: rgba(255, 255, 255, 0.52);
            --surface-shadow: 0 18px 48px rgba(68, 89, 126, 0.14);
            --text-main: #10233f;
            --text-muted: #5f7394;
            --accent: #0a84ff;
            --accent-soft: rgba(10, 132, 255, 0.12);
            --line-soft: rgba(113, 135, 168, 0.18);
            --success-soft: rgba(52, 199, 89, 0.14);
        }
        [data-testid="stAppViewContainer"] {
            background: var(--app-bg);
            color: var(--text-main);
        }
        .block-container {
            padding-top: 1.6rem;
            padding-bottom: 3.4rem;
            max-width: 1440px;
        }
        h1, h2, h3, label, [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"] {
            letter-spacing: 0;
            color: var(--text-main);
        }
        h1 {
            font-weight: 760;
        }
        [data-testid="stSidebar"] {
            background: rgba(244, 247, 251, 0.72);
            backdrop-filter: blur(24px);
            border-right: 1px solid rgba(255, 255, 255, 0.58);
        }
        [data-testid="stSidebar"] > div:first-child {
            background: transparent;
        }
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: 0.9rem;
        }
        [data-testid="stVerticalBlockBorderWrapper"],
        [data-testid="stExpander"] details,
        div[data-testid="stDataFrame"],
        div[data-testid="stFileUploader"],
        div[data-testid="stMarkdownContainer"] blockquote {
            background: var(--surface);
            border: 1px solid var(--surface-border);
            border-radius: 20px;
            box-shadow: var(--surface-shadow);
            backdrop-filter: blur(18px);
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            padding: 0.2rem;
        }
        div[data-testid="stMetric"] {
            border: 1px solid var(--surface-border);
            border-radius: 22px;
            padding: 18px 18px;
            background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(245,249,255,0.78));
            box-shadow: var(--surface-shadow);
            min-height: 108px;
        }
        div[data-testid="stMetricLabel"] {
            color: var(--text-muted);
            font-weight: 600;
        }
        div[data-testid="stMetricValue"] {
            color: var(--text-main);
        }
        .stButton > button,
        [data-testid="stDownloadButton"] > button {
            border-radius: 16px;
            min-height: 46px;
            font-weight: 650;
        }
        .stButton > button[kind="primary"],
        [data-testid="stDownloadButton"] > button {
            border: 0;
            background: linear-gradient(180deg, #3da2ff 0%, #0a84ff 100%);
            color: #ffffff;
            box-shadow: 0 14px 30px rgba(10, 132, 255, 0.28);
        }
        .stButton > button[kind="primary"]:hover,
        [data-testid="stDownloadButton"] > button:hover {
            background: linear-gradient(180deg, #2496ff 0%, #0071e3 100%);
        }
        .stButton > button[kind="secondary"] {
            border: 1px solid rgba(182, 198, 224, 0.68);
            background: rgba(255, 255, 255, 0.92);
            color: var(--text-main);
            box-shadow: 0 8px 18px rgba(68, 89, 126, 0.08);
        }
        .stButton > button[kind="secondary"]:hover {
            border-color: rgba(10, 132, 255, 0.45);
            color: #0a84ff;
            background: rgba(247, 251, 255, 0.98);
        }
        .stSegmentedControl [role="radiogroup"] {
            background: rgba(229, 236, 247, 0.9);
            border-radius: 16px;
            padding: 0.22rem;
            border: 1px solid rgba(182, 198, 224, 0.55);
        }
        .stSegmentedControl [role="radio"] {
            border-radius: 13px;
        }
        .stSegmentedControl [aria-checked="true"] {
            background: #ffffff;
            box-shadow: 0 8px 20px rgba(74, 98, 138, 0.12);
        }
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        div[data-baseweb="popover"] input,
        [data-testid="stDateInputField"] {
            border-radius: 16px;
        }
        div[data-testid="stFileUploader"] {
            padding: 0.85rem;
        }
        [data-testid="stFileUploaderDropzone"] {
            border-radius: 18px;
            border: 1.5px dashed rgba(10, 132, 255, 0.28);
            background: linear-gradient(180deg, rgba(255,255,255,0.92), rgba(236,243,252,0.8));
            padding: 1.25rem 1rem;
        }
        .dataframe th {
            background: rgba(240, 245, 252, 0.92);
        }
        .dataframe td {
            background: rgba(255, 255, 255, 0.72);
        }
        .overview-summary-table-wrap {
            overflow-x: auto;
            margin: 0.2rem 0 1.1rem;
            border: 1px solid rgba(113, 135, 168, 0.2);
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.92);
            box-shadow: 0 10px 24px rgba(68, 89, 126, 0.08);
        }
        .overview-summary-table {
            width: 100%;
            min-width: 760px;
            border-collapse: collapse;
            color: var(--text-main);
            font-variant-numeric: tabular-nums;
        }
        .overview-summary-table th,
        .overview-summary-table td {
            padding: 0.48rem 0.65rem;
            border-bottom: 1px solid rgba(113, 135, 168, 0.14);
            text-align: center;
            white-space: nowrap;
            line-height: 1.25;
        }
        .overview-summary-table th {
            background: rgba(240, 245, 252, 0.82);
            color: var(--text-muted);
            font-size: 0.86rem;
            font-weight: 700;
        }
        .overview-summary-table td:first-child {
            text-align: left;
            font-weight: 700;
        }
        .overview-summary-table tr:first-child td {
            background: rgba(248, 251, 255, 0.95);
            font-weight: 700;
        }
        .overview-summary-table .overview-delta {
            display: inline-block;
            margin-left: 0.25rem;
            font-size: 0.84rem;
        }
        .overview-summary-table .overview-delta-red {
            color: #d92d20;
        }
        .overview-summary-table .overview-delta-green {
            color: #079455;
        }
        .overview-summary-table .overview-delta-neutral {
            color: var(--text-muted);
        }
        .manual-recap-card,
        .material-cases-grid .material-case-card,
        .channel-link-grid a {
            background: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(113, 135, 168, 0.18);
            border-radius: 12px;
            box-shadow: 0 8px 18px rgba(68, 89, 126, 0.08);
        }
        .manual-recap-card {
            padding: 0.85rem 1rem;
            margin: 0.35rem 0 1rem;
        }
        .manual-recap-card h4,
        .material-case-card h4 {
            margin: 0 0 0.35rem;
            font-size: 0.98rem;
            color: var(--text-main);
        }
        .manual-recap-card p {
            margin: 0.25rem 0;
            color: var(--text-muted);
            line-height: 1.55;
        }
        .manual-recap-sections {
            display: grid;
            gap: 0.65rem;
            margin: 0.35rem 0 0.55rem;
        }
        .manual-recap-section {
            border-top: 1px solid rgba(113, 135, 168, 0.14);
            padding-top: 0.55rem;
        }
        .manual-recap-section:first-child {
            border-top: 0;
            padding-top: 0;
        }
        .manual-recap-section h5 {
            margin: 0 0 0.25rem;
            font-size: 0.9rem;
            color: var(--text-main);
        }
        .manual-recap-section ul {
            margin: 0;
            padding-left: 1.1rem;
            color: var(--text-muted);
            line-height: 1.55;
        }
        .manual-recap-section li {
            margin: 0.16rem 0;
        }
        .channel-link-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin: 0.2rem 0 1rem;
        }
        .channel-link-grid a {
            display: inline-flex;
            align-items: center;
            min-height: 34px;
            padding: 0 0.75rem;
            color: #0a84ff;
            text-decoration: none;
            font-weight: 650;
        }
        .material-cases-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
            gap: 0.7rem;
            margin: 0.25rem 0 1rem;
        }
        .material-case-card {
            overflow: hidden;
            min-width: 0;
        }
        .material-case-card .thumb {
            height: 74px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #e7f1ff, #f4f8ff);
            color: #5f7394;
            font-size: 0.78rem;
        }
        .material-case-card label.thumb {
            cursor: zoom-in;
        }
        .material-case-card .cover-toggle {
            display: none;
        }
        .material-case-card img {
            width: 100%;
            height: 74px;
            object-fit: cover;
            display: block;
        }
        .material-case-card .body {
            padding: 0.52rem 0.6rem 0.62rem;
        }
        .material-case-card .title {
            min-height: 2.55em;
            color: var(--text-main);
            font-size: 0.82rem;
            line-height: 1.28;
            overflow: hidden;
        }
        .material-case-card .meta,
        .material-case-card a {
            font-size: 0.78rem;
            line-height: 1.45;
        }
        .material-case-card a {
            color: #0a84ff;
            text-decoration: none;
            font-weight: 650;
        }
        .cover-preview-backdrop {
            position: fixed;
            inset: 0;
            z-index: 9999;
            background: rgba(16, 35, 63, 0.56);
            display: none;
            align-items: center;
            justify-content: center;
            padding: 1.4rem;
        }
        .material-case-card .cover-toggle:checked ~ .cover-preview-backdrop {
            display: flex;
        }
        .cover-preview-dialog {
            width: min(680px, 92vw);
            max-height: 88vh;
            overflow: auto;
            border-radius: 16px;
            background: rgba(255,255,255,0.98);
            box-shadow: 0 28px 80px rgba(16,35,63,0.34);
            padding: 0.8rem;
            text-align: center;
        }
        .cover-preview-dialog img {
            max-width: 100%;
            max-height: 68vh;
            object-fit: contain;
            border-radius: 12px;
            display: block;
            margin: 0 auto 0.65rem;
        }
        .cover-preview-close {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 34px;
            padding: 0 0.8rem;
            border-radius: 10px;
            background: #0a84ff;
            color: #fff;
            cursor: pointer;
            font-weight: 650;
        }
        [data-testid="stExpander"] details summary {
            padding: 0.35rem 0.2rem;
            color: var(--text-main);
            font-weight: 650;
        }
        [data-testid="stInfo"],
        [data-testid="stSuccess"] {
            border-radius: 18px;
            border: 1px solid var(--surface-border);
            background: var(--surface-strong);
            box-shadow: var(--surface-shadow);
        }
        [data-testid="stSuccess"] {
            background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(237, 252, 241, 0.88));
            border-color: rgba(52, 199, 89, 0.2);
        }
        [data-testid="stAlertContentInfo"] p,
        [data-testid="stAlertContentSuccess"] p,
        [data-testid="stCaptionContainer"] p {
            color: var(--text-muted);
        }
        .stPlotlyChart {
            background: var(--surface);
            border: 1px solid var(--surface-border);
            border-radius: 24px;
            box-shadow: var(--surface-shadow);
            padding: 0.55rem;
        }
        /* 文件上传组件中文化 */
        [data-testid="stFileUploaderDropzone"] {
            position: relative;
            min-height: 84px;
            overflow: hidden;
            align-items: center;
        }
        [data-testid="stFileUploaderDropzone"]::before {
            content: "拖拽文件夹或文件到此处，或点击选择\A 单个文件最大 200MB · 支持 CSV、XLS、XLSX、ZIP";
            position: absolute;
            top: 50%;
            left: 26px;
            right: 180px;
            transform: translateY(-50%);
            color: var(--text-main);
            font-size: 0.95rem;
            font-weight: 650;
            line-height: 1.45;
            white-space: pre-line;
            pointer-events: none;
            z-index: 2;
        }
        [data-testid="stFileUploaderDropzone"]::after {
            content: "浏览文件/文件夹";
            position: absolute;
            top: 50%;
            right: 16px;
            min-width: 132px;
            transform: translateY(-50%);
            padding: 0.55rem 0.8rem;
            border: 1px solid rgba(143, 163, 196, 0.45);
            border-radius: 12px;
            background: #ffffff;
            color: var(--text-main);
            font-size: 0.92rem;
            font-weight: 650;
            line-height: 1.2;
            text-align: center;
            pointer-events: none;
            z-index: 2;
        }
        [data-testid="stFileUploaderDropzone"] > * {
            opacity: 0 !important;
        }
        @media (max-width: 640px) {
            [data-testid="stFileUploaderDropzone"] {
                min-height: 116px;
            }
            [data-testid="stFileUploaderDropzone"]::before {
                top: 20px;
                left: 18px;
                right: 18px;
                transform: none;
                font-size: 0.9rem;
            }
            [data-testid="stFileUploaderDropzone"]::after {
                top: auto;
                right: 18px;
                bottom: 16px;
                left: 18px;
                min-width: 0;
                transform: none;
            }
        }
        /* 上传文件列表样式 */
        div[data-testid="stFileUploader"] details summary {
            font-size: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _store_artifacts(result) -> None:
    st.session_state["batch_id"] = result.batch_id
    if result.batch_id:
        st.session_state["global_batch_id"] = result.batch_id
        st.session_state["overview_batch_user_selected"] = True
    st.session_state["report_html"] = result.report_html.read_bytes() if result.report_html is not None else None
    st.session_state["analysis_xlsx"] = result.analysis_xlsx.read_bytes() if result.analysis_xlsx is not None else None
    st.session_state["canonical_csv"] = result.canonical_csv.read_bytes() if result.canonical_csv is not None else None
    st.session_state["total_summary_xlsx"] = result.total_summary_xlsx.read_bytes() if result.total_summary_xlsx is not None else None
    st.session_state["total_summary"] = result.total_summary
    st.session_state["channel_summary"] = result.channel_summary
    st.session_state["platform_summary"] = result.platform_summary
    st.session_state["platform_category_summary"] = result.platform_category_summary
    st.session_state["category_summary"] = result.category_summary
    st.session_state["top_content_items"] = result.top_content_items
    st.session_state["account_audit"] = result.account_audit
    st.session_state["cover_metrics"] = result.cover_metrics
    st.session_state["data_quality"] = result.data_quality
    st.session_state["review_queue"] = result.review_queue
    st.session_state["account_filter_rules"] = result.account_filter_rules
    st.session_state["account_filter_details"] = result.account_filter_details
    st.session_state["channel_comparison"] = result.channel_comparison
    st.session_state["comparison_note"] = result.comparison_note
    st.session_state["ai_summary"] = result.ai_summary
    st.session_state["metadata_enrichment_summary"] = _load_metadata_enrichment_summary(result.archive_dir)


def _load_metadata_enrichment_summary(archive_dir: Path) -> dict:
    manifest_path = Path(archive_dir) / "period_manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    summary = payload.get("metadata_enrichment")
    return summary if isinstance(summary, dict) else {}


def _ensure_generate_period_defaults() -> None:
    today = date.today()
    st.session_state.setdefault("generate_period_start", today)
    st.session_state.setdefault("generate_period_end", st.session_state["generate_period_start"] + timedelta(days=6))
    st.session_state.setdefault("generate_period_end_touched", False)
    st.session_state.setdefault("generate_period_source", "")
    st.session_state.setdefault("generate_period_level", PERIOD_LEVEL_WEEK)


def _sync_generate_period_end() -> None:
    if "generate_period_start" not in st.session_state:
        return
    if st.session_state.get("generate_period_end_touched"):
        return
    st.session_state["generate_period_end"] = st.session_state["generate_period_start"] + timedelta(days=6)


def _mark_generate_period_end_touched() -> None:
    st.session_state["generate_period_end_touched"] = True


def _apply_inferred_generate_period(uploads) -> None:
    inferred = infer_period_from_upload_names(uploads)
    if inferred is None:
        st.session_state["generate_period_source"] = ""
        return
    signature = "|".join(sorted(upload.name for upload in uploads))
    if st.session_state.get("generate_period_source") == signature:
        return
    start, end = inferred
    st.session_state["generate_period_start"] = start
    st.session_state["generate_period_end"] = end
    st.session_state["generate_period_end_touched"] = False
    st.session_state["generate_period_source"] = signature


def _apply_previewed_generate_period(periods, uploads) -> None:
    if len(periods) != 1:
        return
    signature = "preview|" + "|".join(sorted(upload.name for upload in uploads))
    if st.session_state.get("generate_period_source") == signature:
        return
    period = periods[0]
    st.session_state["generate_period_start"] = pd.to_datetime(period.period_start).date()
    st.session_state["generate_period_end"] = pd.to_datetime(period.period_end).date()
    st.session_state["generate_period_level"] = period.period_level
    st.session_state["generate_period_end_touched"] = False
    st.session_state["generate_period_source"] = signature


def _run_raw_sync() -> list:
    if st.session_state.get("raw_sync_running"):
        return []
    st.session_state["raw_sync_running"] = True
    try:
        results = sync_raw_periods(
            APP_DATA_ROOT,
            db_path=APP_DB,
            output_root=APP_OUTPUTS,
            processed_root=APP_PROCESSED,
            category_rules_path=CATEGORY_RULES,
            env_path=ENV_PATH,
            reference_root=APP_DATA_ROOT / "reference",
            output_mode="ui_only",
            enable_deepseek=True,
            enable_external_context=False,
        )
    finally:
        st.session_state["raw_sync_running"] = False

    generated = [item for item in results if item.status == "generated"]
    errors = [item for item in results if item.status == "error"]
    if generated:
        st.session_state["raw_sync_notice"] = f"已手动同步 {len(generated)} 个源文件周期。"
    elif errors:
        st.session_state["raw_sync_notice"] = "源文件手动同步遇到错误：" + "；".join(
            f"{item.period_name}: {item.message}" for item in errors[:3]
        )
    return results


def _mark_overview_period_manual() -> None:
    selected = st.session_state.get("overview_batch_id", "")
    latest = st.session_state.get("overview_latest_batch_id", "")
    st.session_state["overview_batch_user_selected"] = bool(selected and selected != latest)


def _set_global_period_selection(session_key: str) -> None:
    selected = st.session_state.get(session_key, "")
    if selected:
        st.session_state["global_batch_id"] = selected


def _get_common_period_selector(key_prefix: str) -> tuple[str, pd.DataFrame]:
    """统一的周期选择器，返回 (batch_id, batches_df)。

    使用 session_state['global_batch_id'] 实现跨页面联动。
    """
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return "", batches

    available_levels = [level for level in PERIOD_LEVELS if level in set(batches["period_level"].astype(str))]
    if not available_levels:
        available_levels = [PERIOD_LEVEL_WEEK]
    level_key = f"{key_prefix}_period_level"
    current_level = st.session_state.get(level_key, st.session_state.get("global_period_level", available_levels[0]))
    if current_level not in available_levels:
        current_level = available_levels[0]
    selected_level = st.selectbox(
        "选择复盘层级",
        available_levels,
        index=available_levels.index(current_level),
        key=level_key,
        format_func=lambda level: PERIOD_LEVEL_LABELS.get(level, level),
        width="stretch",
    )
    st.session_state["global_period_level"] = selected_level

    level_batches = batches[batches["period_level"].astype(str).eq(str(selected_level))].copy()
    if level_batches.empty:
        return "", level_batches

    batch_ids = [str(value) for value in level_batches["batch_id"]]
    label_by_id = {
        str(row["batch_id"]): _period_selector_label(row)
        for _, row in level_batches.iterrows()
    }
    created_by_id = {
        str(row["batch_id"]): format_beijing_datetime(row.get("created_at", ""))
        for _, row in level_batches.iterrows()
    }

    latest_batch_id = str(level_batches.iloc[0]["batch_id"])
    session_key = f"{key_prefix}_batch_id"
    current_batch_id = st.session_state.get(session_key, latest_batch_id)
    if "global_batch_id" in st.session_state and st.session_state["global_batch_id"] in batch_ids:
        if st.session_state.get(f"{key_prefix}_sync_global", True):
            current_batch_id = st.session_state["global_batch_id"]
    if current_batch_id not in batch_ids:
        current_batch_id = latest_batch_id
    if session_key in st.session_state and st.session_state[session_key] != current_batch_id:
        del st.session_state[session_key]

    selected_batch_id = st.selectbox(
        "选择周期",
        batch_ids,
        index=batch_ids.index(current_batch_id),
        key=session_key,
        format_func=lambda batch_id: f"{label_by_id.get(batch_id, batch_id)}｜{created_by_id.get(batch_id, '')}",
        on_change=_set_global_period_selection,
        args=(session_key,),
        width="stretch",
    )

    # 同步到全局
    st.session_state["global_batch_id"] = selected_batch_id

    return selected_batch_id, level_batches


def _get_compact_period_selector(key_prefix: str, level_col, period_col) -> tuple[str, pd.DataFrame]:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return "", batches

    available_levels = [level for level in PERIOD_LEVELS if level in set(batches["period_level"].astype(str))]
    if not available_levels:
        available_levels = [PERIOD_LEVEL_WEEK]
    level_key = f"{key_prefix}_period_level"
    current_level = st.session_state.get(level_key, st.session_state.get("global_period_level", available_levels[0]))
    if current_level not in available_levels:
        current_level = available_levels[0]
    with level_col:
        selected_level = st.selectbox(
            "复盘层级",
            available_levels,
            index=available_levels.index(current_level),
            key=level_key,
            format_func=lambda level: PERIOD_LEVEL_LABELS.get(level, level),
            width="stretch",
        )
    st.session_state["global_period_level"] = selected_level

    level_batches = batches[batches["period_level"].astype(str).eq(str(selected_level))].copy()
    if level_batches.empty:
        return "", level_batches

    batch_ids = [str(value) for value in level_batches["batch_id"]]
    label_by_id = {
        str(row["batch_id"]): _period_selector_label(row)
        for _, row in level_batches.iterrows()
    }
    created_by_id = {
        str(row["batch_id"]): format_beijing_datetime(row.get("created_at", ""))
        for _, row in level_batches.iterrows()
    }

    latest_batch_id = str(level_batches.iloc[0]["batch_id"])
    session_key = f"{key_prefix}_batch_id"
    current_batch_id = st.session_state.get(session_key, latest_batch_id)
    if "global_batch_id" in st.session_state and st.session_state["global_batch_id"] in batch_ids:
        if st.session_state.get(f"{key_prefix}_sync_global", True):
            current_batch_id = st.session_state["global_batch_id"]
    if current_batch_id not in batch_ids:
        current_batch_id = latest_batch_id
    if session_key in st.session_state and st.session_state[session_key] != current_batch_id:
        del st.session_state[session_key]

    with period_col:
        selected_batch_id = st.selectbox(
            "周期",
            batch_ids,
            index=batch_ids.index(current_batch_id),
            key=session_key,
            format_func=lambda batch_id: f"{label_by_id.get(batch_id, batch_id)}｜{created_by_id.get(batch_id, '')}",
            on_change=_set_global_period_selection,
            args=(session_key,),
            width="stretch",
        )

    st.session_state["global_batch_id"] = selected_batch_id
    return selected_batch_id, level_batches


def _get_overview_period_selector(key_prefix: str) -> tuple[str, pd.DataFrame]:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return "", batches

    selected_level = _render_review_level_cards(key_prefix, batches)
    level_batches = batches[batches["period_level"].astype(str).eq(str(selected_level))].copy()
    if level_batches.empty:
        return "", level_batches

    batch_ids = [str(value) for value in level_batches["batch_id"]]
    label_by_id = {
        str(row["batch_id"]): _period_selector_label(row)
        for _, row in level_batches.iterrows()
    }
    created_by_id = {
        str(row["batch_id"]): format_beijing_datetime(row.get("created_at", ""))
        for _, row in level_batches.iterrows()
    }

    latest_batch_id = str(level_batches.iloc[0]["batch_id"])
    session_key = f"{key_prefix}_batch_id"
    current_batch_id = st.session_state.get(session_key, latest_batch_id)
    if "global_batch_id" in st.session_state and st.session_state["global_batch_id"] in batch_ids:
        if st.session_state.get(f"{key_prefix}_sync_global", True):
            current_batch_id = st.session_state["global_batch_id"]
    if current_batch_id not in batch_ids:
        current_batch_id = latest_batch_id
    if session_key in st.session_state and st.session_state[session_key] != current_batch_id:
        del st.session_state[session_key]

    selected_batch_id = st.selectbox(
        "选择周期",
        batch_ids,
        index=batch_ids.index(current_batch_id),
        key=session_key,
        format_func=lambda batch_id: f"{label_by_id.get(batch_id, batch_id)}｜{created_by_id.get(batch_id, '')}",
        on_change=_set_global_period_selection,
        args=(session_key,),
        width="stretch",
    )
    st.session_state["global_batch_id"] = selected_batch_id
    return selected_batch_id, level_batches


def _render_review_level_cards(key_prefix: str, batches: pd.DataFrame) -> str:
    available_levels = {str(level) for level in batches["period_level"].dropna().astype(str)}
    ordered_available = [level for level in PERIOD_LEVELS if level in available_levels]
    if not ordered_available:
        ordered_available = [PERIOD_LEVEL_WEEK]

    default_level = PERIOD_LEVEL_WEEK if PERIOD_LEVEL_WEEK in ordered_available else ordered_available[0]
    level_key = f"{key_prefix}_period_level"
    current_level = st.session_state.get(level_key, default_level)
    if current_level not in ordered_available:
        current_level = default_level
    st.session_state[level_key] = current_level
    st.session_state["global_period_level"] = current_level

    cols = st.columns(4)
    for col, level in zip(cols, PERIOD_LEVELS):
        is_available = level in available_levels
        if col.button(
            PERIOD_LEVEL_LABELS.get(level, level),
            key=f"{key_prefix}_review_level_{level}",
            type="primary" if current_level == level else "secondary",
            disabled=not is_available,
            width="stretch",
        ):
            st.session_state[level_key] = level
            st.session_state["global_period_level"] = level
            batch_session_key = f"{key_prefix}_batch_id"
            if batch_session_key in st.session_state:
                del st.session_state[batch_session_key]
            st.rerun()
    return current_level


def _period_selector_label(row: pd.Series) -> str:
    label = str(row.get("period_label", "") or "").strip()
    data_start = str(row.get("data_start", "") or "").strip()
    data_end = str(row.get("data_end", "") or "").strip()
    source_type = str(row.get("source_type", "") or SOURCE_TYPE_UPLOAD).strip()
    source_label = SOURCE_TYPE_LABELS.get(source_type, source_type or "上传原始包")
    data_label = f"数据时间 {data_start} 至 {data_end}" if data_start and data_end else ""
    parts = [label, f"来源类型 {source_label}", data_label]
    return "｜".join(part for part in parts if part)


def _get_comparison_period_selector(
    key_prefix: str,
    current_batch_id: str,
    batches: pd.DataFrame,
) -> str:
    if not current_batch_id or batches.empty:
        return ""

    batch_ids = [str(value) for value in batches["batch_id"]]
    options = [""] + [batch_id for batch_id in batch_ids if batch_id != current_batch_id]
    if len(options) <= 1:
        return ""

    label_by_id = dict(zip(batch_ids, batches["period_label"].astype(str)))
    created_by_id = {
        str(row["batch_id"]): format_beijing_datetime(row.get("created_at", ""))
        for _, row in batches.iterrows()
    }
    default_batch_id = _default_comparison_batch_id(current_batch_id, batches)
    session_key = f"{key_prefix}_comparison_batch_id"
    current_value = st.session_state.get(session_key, default_batch_id)
    if current_value not in options:
        current_value = default_batch_id if default_batch_id in options else ""
    if session_key in st.session_state and st.session_state[session_key] != current_value:
        del st.session_state[session_key]

    return st.selectbox(
        "选择对比周期",
        options,
        index=options.index(current_value),
        key=session_key,
        format_func=lambda batch_id: (
            "不对比"
            if batch_id == ""
            else f"{label_by_id.get(batch_id, batch_id)}｜{created_by_id.get(batch_id, '')}"
        ),
        width="stretch",
    )


def _default_comparison_batch_id(current_batch_id: str, batches: pd.DataFrame) -> str:
    if batches.empty or current_batch_id not in set(batches["batch_id"].astype(str)):
        return ""
    normalized = batches.copy()
    normalized["batch_id"] = normalized["batch_id"].astype(str)
    current = normalized[normalized["batch_id"].eq(current_batch_id)]
    if current.empty:
        return ""
    current_start = pd.to_datetime(current.iloc[0].get("period_start", ""), errors="coerce")
    normalized["_period_end_dt"] = pd.to_datetime(normalized["period_end"], errors="coerce")
    earlier = normalized[
        normalized["batch_id"].ne(current_batch_id)
        & normalized["_period_end_dt"].notna()
        & normalized["_period_end_dt"].lt(current_start)
    ]
    if earlier.empty:
        return ""
    earlier = earlier.sort_values(["_period_end_dt", "period_start", "created_at"], ascending=[False, False, False])
    return str(earlier.iloc[0]["batch_id"])


def _sync_period_from_global(key_prefix: str) -> None:
    """从全局 batch_id 同步到指定页面的 batch_id"""
    if "global_batch_id" in st.session_state:
        session_key = f"{key_prefix}_batch_id"
        if session_key in st.session_state and st.session_state[session_key] != st.session_state["global_batch_id"]:
            del st.session_state[session_key]


def _render_section_shell(icon: str, title: str, description: str) -> None:
    # 使用同花顺 logo 作为图标
    logo_html = ""
    if _logo_base64:
        logo_html = f'<img src="data:image/png;base64,{_logo_base64}" style="width:2rem; height:2rem; object-fit:contain; border-radius:8px;">'
    else:
        logo_html = f'<div style="width:2.5rem; height:2.5rem; border-radius:16px; display:flex; align-items:center; justify-content:center; background:rgba(10,132,255,0.12); color:#0a84ff; font-size:1.15rem;">{icon}</div>'

    st.markdown(
        f"""
        <div style="padding:1rem 1.1rem 1.15rem; margin-bottom:0.8rem; border-radius:26px;
             background:linear-gradient(180deg, rgba(255,255,255,0.88), rgba(245,249,255,0.76));
             border:1px solid rgba(255,255,255,0.58); box-shadow:0 18px 44px rgba(68,89,126,0.12);">
          <div style="display:flex; align-items:center; gap:0.8rem;">
            {logo_html}
            <div>
              <div style="font-size:1.32rem; font-weight:760; color:#10233f;">{title}</div>
              <div style="font-size:0.95rem; color:#5f7394;">{description}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _preview_generate_periods(uploaded) -> list:
    try:
        return preview_uploaded_period_buckets(uploaded, default_year=date.today().year)
    except Exception:
        return []


def _normalize_generate_uploads(uploaded, *, replace_same_channel: bool = False) -> list:
    try:
        return normalize_uploaded_periods(
            uploaded,
            APP_DATA_ROOT,
            default_year=date.today().year,
            replace_same_channel=replace_same_channel,
        )
    except ValueError:
        return []


def _render_generate_period_preview(buckets: list) -> None:
    if not buckets:
        return
    preview = pd.DataFrame(
        [
            {
                "复盘层级": PERIOD_LEVEL_LABELS.get(bucket.review_period.period_level, bucket.review_period.period_level),
                "复盘周期": bucket.review_period.period_label,
                "数据时间": f"{bucket.review_period.data_start} 至 {bucket.review_period.data_end}",
                "文件数": bucket.file_count,
                "来源路径": "；".join(bucket.source_paths[:3]) + ("；..." if len(bucket.source_paths) > 3 else ""),
                "来源类型": SOURCE_TYPE_LABELS.get(bucket.review_period.source_type, bucket.review_period.source_type),
                "period_key": bucket.review_period.period_key,
            }
            for bucket in buckets
        ]
    )
    st.caption("系统已按路径识别复盘周期；生成时会把源文件落到 data/months 或 data/weeks，并在 processed 生成清洗产物。")
    st.dataframe(preview, width="stretch", hide_index=True)


def _generate_upload_conflict_labels(uploaded, preview_buckets: list, period_start: date, period_end: date) -> list[str]:
    if not uploaded:
        return []
    try:
        if preview_buckets:
            conflicts = detect_normalized_upload_channel_conflicts(uploaded, APP_DATA_ROOT, default_year=date.today().year)
            return [
                f"{conflict.review_period.period_label}：{conflict.channel}"
                for conflict in conflicts
            ]
        fallback_period = review_period_from_dates(
            period_start,
            period_end,
            st.session_state.get("generate_period_level", PERIOD_LEVEL_WEEK),
            source_type=SOURCE_TYPE_UPLOAD,
        )
        conflicts = detect_upload_channel_conflicts(
            uploaded,
            source_dir_for_period(APP_DATA_ROOT, fallback_period),
            strip_common_period_root=True,
        )
        return [conflict.channel for conflict in conflicts]
    except Exception:
        return []


def _period_for_generate_bucket(bucket, bucket_count: int, period_start: date, period_end: date):
    period = bucket.review_period
    if bucket_count != 1:
        return period
    level = st.session_state.get("generate_period_level", period.period_level)
    return review_period_from_dates(
        pd.to_datetime(period.data_start).date(),
        pd.to_datetime(period.data_end).date(),
        level,
        logic_start=period_start,
        logic_end=period_end,
        source_type=period.source_type,
    )


def _db_file_signature(db_path: Path) -> tuple[int, int]:
    path = Path(db_path)
    if not path.exists():
        return (0, 0)
    stat = path.stat()
    return (int(stat.st_mtime_ns), int(stat.st_size))


def _load_overview_data(db_path: Path, batch_id: str) -> dict[str, object]:
    if Path(db_path) == APP_DB:
        db_signature = _db_file_signature(APP_DB)
    else:
        db_signature = _db_file_signature(db_path)
    return _load_cached_overview_data(str(db_path), batch_id, db_signature, OVERVIEW_CACHE_VERSION)


@st.cache_data(show_spinner=False)
def _load_cached_overview_data(
    db_path_text: str,
    batch_id: str,
    db_signature: tuple[int, int],
    cache_version: int,
) -> dict[str, object]:
    del db_signature, cache_version
    db_path = Path(db_path_text)
    items = load_dashboard_items_for_batch(db_path, batch_id)
    summary = build_dashboard_summary(items)
    platform_summary = aggregate_dashboard(items, ["channel"])
    channel_comparison = load_channel_comparison_for_batch(db_path, batch_id)
    if channel_comparison.empty:
        channel_comparison = build_period_comparison_for_batch(db_path, batch_id)
    content_type_summary = summarize_content_types(items)
    external_context = None
    recommendations = build_content_recommendations(
        summary,
        platform_summary,
        content_type_summary,
        channel_comparison=channel_comparison,
        external_context=external_context,
    )
    return {
        "items": items,
        "summary": summary,
        "platform_summary": platform_summary,
        "channel_comparison": channel_comparison,
        "content_type_summary": content_type_summary,
        "external_context": external_context,
        "recommendations": recommendations,
    }


def _items_period_level(items: pd.DataFrame) -> str:
    if items.empty or "batch_period_level" not in items.columns:
        return PERIOD_LEVEL_WEEK
    value = str(items["batch_period_level"].dropna().astype(str).head(1).iloc[0] if not items["batch_period_level"].dropna().empty else "")
    return value if value in PERIOD_LEVELS else PERIOD_LEVEL_WEEK


def _items_date_range(items: pd.DataFrame) -> tuple[str, str]:
    if items.empty:
        return "", ""
    start_candidates = ["batch_data_start", "batch_period_start", "period_start"]
    end_candidates = ["batch_data_end", "batch_period_end", "period_end"]
    return _first_nonblank_column_value(items, start_candidates), _first_nonblank_column_value(items, end_candidates)


def _first_nonblank_column_value(items: pd.DataFrame, columns: list[str]) -> str:
    for column in columns:
        if column not in items.columns:
            continue
        values = items[column].dropna().astype(str).str.strip()
        values = values[values.ne("")]
        if not values.empty:
            return str(values.iloc[0])
    return ""


def _page_overview() -> None:
    st.markdown('<div id="top"></div>', unsafe_allow_html=True)
    st.title("总览")
    notice = st.session_state.pop("raw_sync_notice", "")
    if notice:
        st.info(notice)

    selected_batch_id, _ = _get_overview_period_selector("overview")
    if not selected_batch_id:
        st.info("还没有成功周期。请先到“生成页面数据”上传数据并生成。")
        return

    if st.button("手动同步源文件", width="stretch"):
        _load_cached_overview_data.clear()
        results = _run_raw_sync()
        if any(item.status == "generated" for item in results):
            st.rerun()
        if any(item.status == "error" for item in results):
            st.error(st.session_state.get("raw_sync_notice", "源文件手动同步遇到错误。"))
        else:
            st.success("已检查源文件目录，当前没有需要生成的新内容。")

    overview_data = _load_overview_data(APP_DB, selected_batch_id)
    items = overview_data["items"]
    if items.empty:
        st.info("当前周期没有可展示的数据。")
        return

    summary = overview_data["summary"]
    platform_summary = overview_data["platform_summary"]
    channel_comparison = overview_data["channel_comparison"]
    channel_topic_context = _channel_topic_context_for_report(selected_batch_id, platform_summary)
    recommendations = str(overview_data["recommendations"] or "")

    st.subheader("本周期数据总览")
    _render_overview_summary_table(summary, platform_summary, channel_comparison)

    st.subheader("分渠道图")
    _render_platform_chart(platform_summary, channel_comparison)

    _render_manual_recap_controls(
        selected_batch_id,
        summary,
        platform_summary,
        channel_comparison,
        items,
        recommendations,
        channel_topic_context,
    )
    _render_channel_links(platform_summary)


def _render_manual_recap_controls(
    batch_id: str,
    summary,
    platform_summary: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    items: pd.DataFrame,
    recommendations: str = "",
    channel_topic_context: pd.DataFrame | None = None,
) -> None:
    left, right = st.columns([2.2, 1])
    with left:
        st.subheader("手动 AI 复盘报告")
        st.caption("上传后只做数据清洗和页面展示；AI 复盘只在点击按钮时生成，并固定到下次手动更新。")
    with right:
        generate = st.button("手动生成/更新 AI 复盘", type="primary", width="stretch")

    if generate:
        try:
            _run_manual_recap_generation_with_progress(
                batch_id,
                summary,
                platform_summary,
                channel_comparison,
                items,
                recommendations,
                channel_topic_context,
            )
            st.success("AI 复盘报告已手动更新。")
        except Exception as exc:
            st.error(f"手动生成 AI 复盘失败：{exc}")

    saved = load_manual_recap_report(APP_DB, batch_id)
    if not saved:
        st.info("当前周期还没有手动 AI 复盘报告。页面数据已可查看，复盘结论需点击按钮手动生成。")
        return
    created_at = format_beijing_datetime(saved.get("created_at", ""))
    if created_at:
        st.caption(f"上次手动更新时间：{created_at}")
    _render_manual_recap_overview(saved.get("report", {}))


def _run_manual_recap_generation_with_progress(
    batch_id: str,
    summary,
    platform_summary: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    items: pd.DataFrame,
    recommendations: str = "",
    channel_topic_context: pd.DataFrame | None = None,
) -> None:
    with st.status(MANUAL_RECAP_PROGRESS_STEPS[0], expanded=True) as status:
        progress_bar = st.progress(0, text=MANUAL_RECAP_PROGRESS_STEPS[0])

        def progress_callback(message: str) -> None:
            percent = MANUAL_RECAP_PROGRESS_VALUES.get(message, 50)
            status.update(label=message)
            progress_bar.progress(percent, text=message)

        progress_callback("正在整理复盘证据")
        top_cases = _top_content_cases_for_report(items, platform_summary)
        total_summary = build_overview_table_rows(summary, platform_summary, channel_comparison)

        progress_callback("正在请求 AI 生成结构化复盘")
        report = generate_manual_recap_report(
            total_summary=total_summary,
            platform_summary=platform_summary,
            channel_comparison=channel_comparison,
            top_content_cases=top_cases,
            overview_recommendations=recommendations,
            channel_topic_context=channel_topic_context,
            period_level=_items_period_level(items),
            env_path=ENV_PATH,
        )

        progress_callback("正在保存 AI 复盘报告")
        settings = resolve_deepseek_settings(ENV_PATH)
        persist_manual_recap_report(
            APP_DB,
            batch_id,
            provider="deepseek",
            model=settings.model,
            report=report,
        )

        progress_bar.progress(100, text="AI 复盘报告生成完成")
        status.update(label="AI 复盘报告生成完成", state="complete", expanded=False)


def _render_manual_recap_overview(report: object) -> None:
    data = report if isinstance(report, dict) else {}
    overview = data.get("overview", {}) if isinstance(data.get("overview", {}), dict) else {}
    report_text = str(
        overview.get("report", "")
        or "\n\n".join(
            part
            for part in [
                str(overview.get("summary", "") or "").strip(),
                str(overview.get("cause", "") or "").strip(),
            ]
            if part
        )
        or "暂无总体复盘。"
    ).strip()
    direction_text = str(overview.get("next_cycle_direction", "") or overview.get("action", "") or "暂无下周期总体方向。").strip()
    sections_html = _render_manual_recap_sections(overview.get("sections", []))
    body_html = sections_html or _manual_recap_paragraph_html("整体结论", report_text)
    direction_html = (
        _manual_recap_paragraph_html("下周期总体方向", direction_text)
        if _manual_recap_should_render_direction(overview.get("sections", []))
        else ""
    )
    st.markdown(
        f"""
        <div class="manual-recap-card">
          <h4>本周期整体复盘与下周期意见</h4>
          {body_html}
          {direction_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_manual_recap_sections(sections: object) -> str:
    if not isinstance(sections, list):
        return ""
    blocks = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title", "") or "").strip()
        items = section.get("items", [])
        if not _manual_recap_visible_section(title):
            continue
        if not title or not isinstance(items, list):
            continue
        item_html = "".join(f"<li>{html.escape(str(item or '').strip())}</li>" for item in items if str(item or "").strip())
        if not item_html:
            continue
        blocks.append(
            '<div class="manual-recap-section">'
            f"<h5>{html.escape(title)}</h5>"
            f"<ul>{item_html}</ul>"
            "</div>"
        )
    if not blocks:
        return ""
    return f'<div class="manual-recap-sections">{"".join(blocks)}</div>'


def _manual_recap_visible_section(title: str) -> bool:
    if title in {"题材/内容类型"}:
        return False
    return True


def _manual_recap_paragraph_html(label: str, text: str) -> str:
    return f"<p><strong>{html.escape(label)}：</strong>{html.escape(text).replace(chr(10), '<br>')}</p>"


def _manual_recap_should_render_direction(sections: object) -> bool:
    if not isinstance(sections, list):
        return True
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title", "") or "").strip()
        if title in {"下周期动作", "下一周期执行动作"}:
            return False
    return True


def _render_channel_links(platform_summary: pd.DataFrame) -> None:
    if platform_summary.empty or "channel" not in platform_summary.columns:
        return
    pages_by_channel = _channel_pages_for_current_period()
    channels = []
    for channel in platform_summary["channel"].fillna("").astype(str).str.strip().tolist():
        if not channel:
            continue
        if channel not in pages_by_channel:
            continue
        channels.append(channel)
    if not channels:
        return
    st.markdown('<div class="channel-link-grid">', unsafe_allow_html=True)
    columns = st.columns(min(len(channels), 4))
    for index, channel in enumerate(channels):
        with columns[index % len(columns)]:
            st.page_link(pages_by_channel[channel], label=channel, icon=":material/arrow_forward:", width="stretch")
    st.markdown("</div>", unsafe_allow_html=True)


def _channel_topic_context_for_report(batch_id: str, platform_summary: pd.DataFrame) -> pd.DataFrame:
    columns = ["channel", "topic_insights", "top_topics"]
    if not batch_id or platform_summary.empty or "channel" not in platform_summary.columns:
        return pd.DataFrame(columns=columns)
    topic_labels = load_topic_labels_for_batch(APP_DB, batch_id)
    if topic_labels.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for channel in platform_summary["channel"].fillna("").astype(str).str.strip().tolist():
        if not channel:
            continue
        topic_summary = summarize_persisted_topic_labels(topic_labels, channel)
        if topic_summary.empty:
            continue
        rows.append(
            {
                "channel": channel,
                "topic_insights": build_channel_top_topic_insights(topic_summary),
                "top_topics": _topic_context_records(topic_summary),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _topic_context_records(topic_summary: pd.DataFrame) -> list[dict[str, object]]:
    if topic_summary.empty:
        return []
    columns = [
        column
        for column in ["topic_name", "content_types", "spend", "activations", "activation_cost", "first_pay_count", "first_pay_rate"]
        if column in topic_summary.columns
    ]
    if not columns:
        return []
    clean = topic_summary[columns].head(5).copy().astype(object)
    clean = clean.where(pd.notna(clean), "")
    return clean.to_dict(orient="records")


def _top_content_cases_for_report(items: pd.DataFrame, platform_summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if platform_summary.empty or "channel" not in platform_summary.columns:
        return pd.DataFrame()
    for channel in platform_summary["channel"].fillna("").astype(str).str.strip():
        if not channel:
            continue
        top = summarize_channel_top_content_links(items, channel)
        if top.empty:
            continue
        top = top.copy()
        top.insert(0, "channel", channel)
        rows.append(top)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _html_anchor_id(value: object) -> str:
    text = str(value or "").strip()
    safe = "".join(char if char.isascii() and char.isalnum() else "-" for char in text).strip("-")
    return safe or _stable_url_token(text)


def _stable_url_token(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _channel_page_path(channel: object) -> str:
    return f"channel-{_html_anchor_id(channel)}"


def _channel_page_href(channel: object, batch_id: str = "") -> str:
    path = f"/{_channel_page_path(channel)}"
    return f"{path}?batch_id={quote(str(batch_id), safe='')}" if batch_id else path


def _render_overview_summary_table(
    summary,
    platform_summary: pd.DataFrame,
    channel_comparison: pd.DataFrame | None = None,
) -> None:
    rows = build_overview_table_rows(summary, platform_summary, channel_comparison)
    if rows.empty:
        st.info("当前没有可展示的环比数据。")
        return

    metrics = [
        ("总消耗", "spend", "spend_change_rate", 0, False),
        ("总曝光", "impressions", "impressions_change_rate", 0, False),
        ("激活数", "activations", "activations_change_rate", 0, False),
        ("激活成本", "activation_cost", "activation_cost_change_rate", 2, True),
        ("付费数", "first_pay_count", "first_pay_count_change_rate", 0, False),
        ("付费成本", "first_pay_cost", "first_pay_cost_change_rate", 2, True),
    ]
    headers = "".join(f"<th>{html.escape(label)}</th>" for label, *_ in metrics)
    body_rows = []
    for _, row in rows.iterrows():
        channel = str(row.get("channel", "") or "").strip() or "-"
        cells = [f"<td>{html.escape(channel)}</td>"]
        for _, value_column, rate_column, digits, is_cost in metrics:
            cells.append(
                "<td>"
                + _overview_metric_cell(row.get(value_column), row.get(rate_column), digits, is_cost)
                + "</td>"
            )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    table_html = f"""
    <div class="overview-summary-table-wrap">
      <table class="overview-summary-table">
        <thead>
          <tr><th>渠道</th>{headers}</tr>
        </thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)


def _overview_metric_cell(value: object, rate: object, digits: int, is_cost: bool) -> str:
    formatted_value = html.escape(_fmt_overview_value(value, digits))
    formatted_rate = html.escape(_fmt_overview_growth(rate))
    delta_class = _overview_growth_class(rate, is_cost)
    return f'{formatted_value}<span class="overview-delta {delta_class}">{formatted_rate}</span>'


def _fmt_overview_value(value: object, digits: int) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "-"
    return format_display_number(numeric, max_decimals=max(digits, 0))


def _fmt_overview_growth(value: object) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "（-）"
    sign = "+" if float(numeric) > 0 else ""
    return f"（{sign}{format_display_number(float(numeric) * 100, max_decimals=1)}%）"


def _overview_growth_class(value: object, is_cost: bool) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric) or float(numeric) == 0.0:
        return "overview-delta-neutral"
    if float(numeric) > 0:
        return "overview-delta-green" if is_cost else "overview-delta-red"
    return "overview-delta-red" if is_cost else "overview-delta-green"


def _compact_recommendations(markdown: str, max_items: int = 4) -> str:
    bullets = []
    for line in str(markdown or "").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if item.startswith("- "):
            bullets.append(item)
        if len(bullets) >= max_items:
            break
    return "\n".join(bullets) if bullets else str(markdown or "").strip()


def _page_generate() -> None:
    _ensure_generate_period_defaults()
    _render_section_shell("􀈟", "生成页面数据", "支持上传整个文件夹或多文件包，更新本周期源文件并刷新页面数据。")
    st.title("生成页面数据")
    st.caption("上传 Excel、CSV 或 zip，系统会保存到 data/months 或 data/weeks，清洗产物写入 processed，并更新当前周期数据。")

    with st.container(border=True):
        st.subheader("生成参数")
        uploaded = st.file_uploader(
            "上传原始数据",
            type=["csv", "xls", "xlsx", "zip"],
            key="generate_uploads",
            accept_multiple_files="directory",
        )
        if uploaded:
            preview_buckets = _preview_generate_periods(uploaded)
            preview_periods = [bucket.review_period for bucket in preview_buckets]
            if preview_buckets:
                _apply_previewed_generate_period(preview_periods, uploaded)
                _render_generate_period_preview(preview_buckets)
            else:
                _apply_inferred_generate_period(uploaded)
        else:
            preview_buckets = []
        st.caption(resolve_deepseek_settings(ENV_PATH).public_status)
        c0, c1, c2 = st.columns([1, 1, 1])
        c0.selectbox(
            "复盘层级",
            PERIOD_LEVELS,
            key="generate_period_level",
            format_func=lambda level: PERIOD_LEVEL_LABELS.get(level, level),
            width="stretch",
        )
        period_start = c1.date_input(
            "周期开始",
            key="generate_period_start",
            on_change=_sync_generate_period_end,
        )
        period_end = c2.date_input(
            "周期结束",
            key="generate_period_end",
            on_change=_mark_generate_period_end_touched,
        )
        if uploaded and st.session_state.get("generate_period_source"):
            st.caption("已根据上传文件夹名自动回填周期，仍可手动调整复盘层级、周期日期；识别结果会展示数据时间和来源类型。")
        conflict_labels = _generate_upload_conflict_labels(uploaded, preview_buckets, period_start, period_end)
        overwrite_existing_channels = False
        if conflict_labels:
            st.warning("本地已存在渠道：" + "、".join(conflict_labels))
            overwrite_existing_channels = st.checkbox(
                "覆盖已存在渠道",
                value=False,
                key="overwrite_existing_channels",
            )
        else:
            st.session_state["overwrite_existing_channels"] = False
        enable_metadata_enrichment = st.checkbox(
            "尝试自动补充公开信息",
            value=False,
            key="enable_metadata_enrichment",
            help="仅使用低风险公开信息：B站缓存/公开接口；抖音/小红书链接和ID推导。不会覆盖 Excel 已有字段，仅高消耗冲突或内容ID冲突进入人工复核。",
        )
        generate = st.button("生成本周期数据", type="primary", width="stretch")
        refresh_history = st.button("重算历史批次并补充公开信息", width="stretch")

    if generate:
        if not uploaded:
            st.error("请先上传文件夹，或上传 CSV、Excel、zip 数据包。")
        elif period_start > period_end:
            st.error("周期开始日期不能晚于结束日期。")
        elif conflict_labels and not overwrite_existing_channels:
            st.error("本地已存在渠道。默认不覆盖，请勾选“覆盖已存在渠道”后再生成。")
        else:
            try:
                _run_with_generation_progress(
                    uploaded,
                    period_start,
                    period_end,
                    overwrite_existing_channels=overwrite_existing_channels,
                    metadata_enrichment_mode="safe_public" if enable_metadata_enrichment else "off",
                )
            except Exception as exc:
                st.error(f"生成失败：{exc}")

    if refresh_history:
        try:
            _run_historical_refresh_with_progress()
        except Exception as exc:
            st.error(f"历史批次重算失败：{exc}")

    _render_rollup_generator()

    if "total_summary" in st.session_state:
        _display_generation_results()
    else:
        st.info("可点击选择目录，或把多个 CSV / Excel / ZIP 拖入上传区；目录上传会保留子目录结构。")


def _run_historical_refresh_with_progress():
    with st.status("正在重算历史批次", expanded=True) as status:
        results = refresh_historical_source_periods(
            data_root=APP_DATA_ROOT,
            processed_root=APP_PROCESSED,
            output_root=APP_OUTPUTS,
            db_path=APP_DB,
            metadata_cache_dir=APP_DATA_ROOT / "metadata_cache",
            env_path=ENV_PATH,
            reference_root=APP_DATA_ROOT / "reference",
        )
        if not results:
            st.session_state["historical_refresh_summary"] = "未找到可重算的历史原始数据目录。"
            status.update(label="未找到可重算的历史原始数据目录", state="complete", expanded=False)
            return []
        latest = results[-1]
        _store_artifacts(latest)
        st.session_state["historical_refresh_summary"] = f"已重算 {len(results)} 个历史批次，并启用公开信息补充。"
        status.update(label=st.session_state["historical_refresh_summary"], state="complete", expanded=False)
        return results

def _run_with_generation_progress(
    uploaded,
    period_start: date,
    period_end: date,
    *,
    overwrite_existing_channels: bool = False,
    metadata_enrichment_mode: str = "off",
):
    with st.status(GENERATION_PROGRESS_STEPS[0], expanded=True) as status:
        progress_bar = st.progress(0, text=GENERATION_PROGRESS_STEPS[0])
        detail = st.empty()
        messages: list[str] = []

        def progress_callback(message: str) -> None:
            message = str(message)
            if message not in messages:
                messages.append(message)
            percent = GENERATION_PROGRESS_VALUES.get(message, min(95, max(5, len(messages) * 12)))
            progress_bar.progress(percent, text=message)
            detail.markdown("\n".join(f"- {item}" for item in messages))
            status.update(label=message, state="running", expanded=True)

        try:
            progress_callback("正在识别上传文件和复盘周期")
            normalized_buckets = _normalize_generate_uploads(
                uploaded,
                replace_same_channel=overwrite_existing_channels,
            )
            progress_callback("正在整理源文件周期目录")
            if normalized_buckets:
                result = None
                for bucket in normalized_buckets:
                    period = _period_for_generate_bucket(bucket, len(normalized_buckets), period_start, period_end)
                    result = run_archived_workflow(
                        bucket.raw_dir,
                        period.period_start,
                        period.period_end,
                        output_root=APP_OUTPUTS,
                        processed_root=APP_PROCESSED,
                        db_path=APP_DB,
                        category_rules_path=CATEGORY_RULES,
                        env_path=ENV_PATH,
                        reference_root=APP_DATA_ROOT / "reference",
                        period_level=period.period_level,
                        period_key=period.period_key,
                        period_label=period.period_label,
                        data_start=period.data_start,
                        data_end=period.data_end,
                        source_type=period.source_type,
                        progress_callback=progress_callback,
                        output_mode="ui_only",
                        enable_deepseek=True,
                        enable_external_context=False,
                        metadata_enrichment_mode=metadata_enrichment_mode,
                        metadata_cache_dir=APP_DATA_ROOT / "metadata_cache",
                    )
                assert result is not None
            else:
                fallback_period = review_period_from_dates(
                    period_start,
                    period_end,
                    st.session_state.get("generate_period_level", PERIOD_LEVEL_WEEK),
                    source_type=SOURCE_TYPE_UPLOAD,
                )
                materialized = materialize_uploaded_files(
                    uploaded,
                    source_dir_for_period(APP_DATA_ROOT, fallback_period),
                    strip_common_period_root=True,
                    replace_same_channel=overwrite_existing_channels,
                )
                result = run_archived_workflow(
                    materialized.raw_dir,
                    fallback_period.period_start,
                    fallback_period.period_end,
                    output_root=APP_OUTPUTS,
                    processed_root=APP_PROCESSED,
                    db_path=APP_DB,
                    category_rules_path=CATEGORY_RULES,
                    env_path=ENV_PATH,
                    reference_root=APP_DATA_ROOT / "reference",
                    period_level=fallback_period.period_level,
                    period_key=fallback_period.period_key,
                    period_label=fallback_period.period_label,
                    data_start=fallback_period.data_start,
                    data_end=fallback_period.data_end,
                    source_type=SOURCE_TYPE_UPLOAD,
                    progress_callback=progress_callback,
                    output_mode="ui_only",
                    enable_deepseek=True,
                    enable_external_context=False,
                    metadata_enrichment_mode=metadata_enrichment_mode,
                    metadata_cache_dir=APP_DATA_ROOT / "metadata_cache",
                )
            _store_artifacts(result)
            progress_bar.progress(100, text="页面数据生成完成")
            status.update(label="页面数据生成完成", state="complete", expanded=False)
            return result
        except Exception:
            status.update(label="生成失败", state="error", expanded=True)
            raise


def _render_rollup_generator() -> None:
    with st.container(border=True):
        st.subheader("季度/年度汇总复盘")
        st.caption("季度、年度可直接上传原始包生成，也可以由已入库周期汇总生成；汇总规则为月度优先、周级补缺。")
        c1, c2, c3 = st.columns(3)
        level = c1.selectbox(
            "汇总复盘层级",
            ["quarter", "year"],
            format_func=lambda value: PERIOD_LEVEL_LABELS.get(value, value),
            key="rollup_period_level",
            width="stretch",
        )
        year = int(c2.number_input("汇总年份", min_value=2020, max_value=2100, value=date.today().year, step=1))
        quarter = 1
        if level == "quarter":
            quarter = int(c3.selectbox("汇总季度", [1, 2, 3, 4], format_func=lambda value: f"第{value}季度", key="rollup_quarter"))
        else:
            c3.caption("年度汇总覆盖 1 月 1 日至 12 月 31 日。")

        try:
            period = rollup_period_for(level, year, quarter if level == "quarter" else None)
            components = select_rollup_component_batches(APP_DB, period)
        except Exception as exc:
            st.warning(f"暂无法计算汇总周期：{exc}")
            return
        st.caption(f"将生成：{period.period_label}；来源类型：系统汇总；可用组成周期 {len(components)} 个。")
        if st.button("生成季度/年度汇总", width="stretch", disabled=not components):
            try:
                result = run_rollup_workflow(
                    APP_DB,
                    components,
                    period,
                    output_root=APP_OUTPUTS,
                    processed_root=APP_PROCESSED,
                    category_rules_path=CATEGORY_RULES,
                    env_path=ENV_PATH,
                    output_mode="ui_only",
                    enable_deepseek=True,
                    enable_external_context=False,
                )
                _store_artifacts(result)
                st.success(f"已生成汇总复盘：{period.period_label}")
            except Exception as exc:
                st.error(f"汇总生成失败：{exc}")


def _page_trends() -> None:
    _render_section_shell("􀑪", "历史趋势", "按周/月复盘周期追踪关键指标变化。")
    st.title("历史趋势")
    st.caption("切换周/月和展示窗口，查看全部关键指标趋势；不再按内容分类拆线。")
    items = load_all_dashboard_items(APP_DB)
    batches = list_successful_dashboard_batches(APP_DB)
    if items.empty or batches.empty:
        st.info("还没有可展示的周期数据。")
        return

    available_levels = [level for level in TREND_PERIOD_LEVELS if level in set(batches["period_level"].astype(str))]
    if not available_levels:
        st.info("当前没有周或月维度的历史周期。")
        return

    level_key = "trend_period_level"
    if level_key in st.session_state and st.session_state[level_key] not in available_levels:
        del st.session_state[level_key]
    with st.container(border=True):
        c1, c2, c3 = st.columns([0.9, 1.2, 1.8])
        with c1:
            selected_level = st.segmented_control(
                "周期粒度",
                available_levels,
                format_func=lambda value: PERIOD_LEVEL_LABELS.get(value, value),
                key=level_key,
                width="stretch",
            )
        selected_level = selected_level or available_levels[0]
        window_options = TREND_WINDOW_OPTIONS.get(selected_level, TREND_WINDOW_OPTIONS[PERIOD_LEVEL_WEEK])
        window_labels = [label for label, _ in window_options]
        with c2:
            selected_window_label = st.segmented_control(
                "展示窗口",
                window_labels,
                key=f"trend_window_{selected_level}",
                width="stretch",
            )
        selected_window_label = selected_window_label or window_labels[0]
        platforms = c3.multiselect("渠道", _dashboard_options(items, "channel"), key="trend_platforms")

    window_size = dict(window_options).get(selected_window_label)
    trends = summarize_period_metric_trends(
        items,
        batches,
        selected_level,
        window_size=window_size,
        channels=tuple(platforms),
    )
    if trends.empty:
        st.info("当前周期粒度和筛选条件下没有趋势数据。")
        return

    available_count = _trend_available_period_count(batches, selected_level)
    if window_size and available_count < window_size:
        st.caption(f"当前只有 {available_count} 个{PERIOD_LEVEL_LABELS.get(selected_level, selected_level)}周期，已展示全部可用周期。")
    else:
        st.caption(f"当前展示：{PERIOD_LEVEL_LABELS.get(selected_level, selected_level)} · {selected_window_label}。")

    _render_period_metric_trend_grid(trends)

    with st.expander("查看周期级明细", expanded=False):
        st.dataframe(localize_and_sort_columns(trends), width="stretch", hide_index=True)


def _render_period_metric_trend_grid(trends: pd.DataFrame) -> None:
    metric_items = list(CHART_METRICS.items())
    for start in range(0, len(metric_items), 3):
        cols = st.columns(3)
        for offset, (col, (metric_name, (metric, metric_label, is_cost))) in enumerate(
            zip(cols, metric_items[start : start + 3])
        ):
            color = BAR_COLOR_SEQUENCE[(start + offset) % len(BAR_COLOR_SEQUENCE)]
            with col:
                with st.container(border=True):
                    _render_period_metric_chart(trends, metric_name, metric, metric_label, is_cost, color)


def _render_period_metric_chart(
    trends: pd.DataFrame,
    metric_name: str,
    metric: str,
    metric_label: str,
    is_cost: bool,
    color: str,
) -> None:
    if metric not in trends.columns:
        return
    chart = trends.copy()
    chart[metric] = pd.to_numeric(chart[metric], errors="coerce")
    value_candidates = chart[metric].dropna()
    current_value = value_candidates.iloc[-1] if not value_candidates.empty else pd.NA
    current_text = _format_chart_value(current_value, metric) or "暂无"
    delta = _period_metric_change_rate(chart[metric])
    delta_text = _fmt_growth_delta(delta) or "（-）"
    delta_color = _trend_delta_color(delta, is_cost=is_cost)
    trend_note = "成本越低越好" if is_cost else "提升为正向"
    st.markdown(
        f"""
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:0.75rem;margin-bottom:0.35rem;">
          <div>
            <div style="color:#5f6f86;font-size:0.9rem;font-weight:650;">{html.escape(metric_name)}</div>
            <div style="color:#10233f;font-size:1.7rem;font-weight:760;line-height:1.25;">{html.escape(current_text)}</div>
          </div>
          <div style="color:{delta_color};background:rgba(248,250,252,0.9);border-radius:999px;padding:0.25rem 0.55rem;font-size:0.86rem;font-weight:720;white-space:nowrap;">
            较上期 {html.escape(delta_text)}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if chart[metric].dropna().empty:
        st.info(f"{metric_name}暂无可绘制数据。")
        return

    chart["__axis_label"] = chart.apply(_trend_axis_label, axis=1)
    chart["__value_text"] = chart[metric].map(lambda value: _format_chart_value(value, metric) or "暂无")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=chart["__axis_label"],
            y=chart[metric],
            customdata=chart[["trend_period", "__value_text"]].to_numpy(),
            mode="lines+markers",
            line={"color": color, "width": 3},
            marker={"color": "#ffffff", "line": {"color": color, "width": 2}, "size": 8},
            hovertemplate=f"周期：%{{customdata[0]}}<br>{metric_label}：%{{customdata[1]}}<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=245,
        margin=dict(l=12, r=12, t=10, b=48),
        showlegend=False,
        plot_bgcolor="rgba(255,255,255,0.96)",
        paper_bgcolor="rgba(255,255,255,0)",
        font=dict(color="#10233f"),
    )
    fig.update_xaxes(title="", tickangle=-22, automargin=True, gridcolor="rgba(255,255,255,0)")
    fig.update_yaxes(title="", tickformat=_axis_tick_format(metric), gridcolor="rgba(95,115,148,0.14)")
    st.plotly_chart(fig, config={"displayModeBar": False})
    st.caption(trend_note)


def _period_metric_change_rate(values: pd.Series) -> object:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 2:
        return pd.NA
    previous = float(numeric.iloc[-2])
    current = float(numeric.iloc[-1])
    if previous == 0:
        return pd.NA
    return (current - previous) / previous


def _trend_delta_color(value: object, *, is_cost: bool) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric) or float(numeric) == 0.0:
        return "#667085"
    if float(numeric) > 0:
        return "#079455" if is_cost else "#d92d20"
    return "#d92d20" if is_cost else "#079455"


def _trend_axis_label(row: pd.Series) -> str:
    start = pd.to_datetime(row.get("period_start", ""), errors="coerce")
    if pd.isna(start):
        key = str(row.get("period_key", "") or row.get("trend_period", "")).strip()
        return key.replace("-", "")
    if str(row.get("period_level", "")) == PERIOD_LEVEL_MONTH:
        return start.strftime("%Y%m")
    if str(row.get("period_level", "")) == PERIOD_LEVEL_WEEK:
        end = pd.to_datetime(row.get("period_end", ""), errors="coerce")
        if pd.isna(end):
            return start.strftime("%Y%m%d")
        return f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    key = str(row.get("period_key", "") or row.get("trend_period", "")).strip()
    return key.replace("-", "")


def _trend_available_period_count(batches: pd.DataFrame, period_level: str) -> int:
    if batches.empty or "period_level" not in batches.columns:
        return 0
    scoped = batches[batches["period_level"].fillna("").astype(str).eq(str(period_level))]
    if {"period_level", "period_key"}.issubset(scoped.columns):
        scoped = scoped.drop_duplicates(subset=["period_level", "period_key"])
    return int(len(scoped))


def _render_conflict_priority_review(review_items: pd.DataFrame) -> None:
    st.subheader("冲突优先审核")
    if review_items.empty:
        return
    priority = review_items.copy()
    for column in ["spend", "activations", "activation_cost"]:
        if column not in priority.columns:
            priority[column] = 0
        priority[column] = pd.to_numeric(priority[column], errors="coerce").fillna(0.0)
    priority["__risk_rank"] = priority["issue_type"].fillna("").astype(str).map(_review_issue_priority)
    priority = priority.sort_values(["__risk_rank", "spend"], ascending=[True, False]).head(20)
    display = pd.DataFrame(
        {
            "问题类型": priority["issue_type"].fillna("").astype(str),
            "建议动作": priority["review_action"].fillna("").astype(str),
            "渠道": priority["channel"].fillna("").astype(str),
            "标题": priority["title"].fillna("").astype(str),
            "内容ID": priority["content_id"].fillna("").astype(str),
            "冲突点": priority["conflict_details"].fillna("").astype(str),
            "影响消耗": priority["spend"],
            "影响激活": priority["activations"],
            "来源文件": priority["source_file"].fillna("").astype(str),
            "来源Sheet": priority["source_sheet"].fillna("").astype(str),
        }
    )
    st.table(display)


def _review_issue_priority(value: object) -> int:
    text = str(value or "")
    if "冲突" in text or "重复" in text:
        return 0
    if "缺失" in text:
        return 1
    if "复核" in text or "审核" in text:
        return 2
    return 3


def _page_reference_tables() -> None:
    _render_section_shell("􀉉", "维护台账", "查看本地映射表和处理规则，确认账号与字段口径。")
    st.title("维护台账")
    st.caption("查看本地 reference_tables.xlsx、channel_profiles.yml 和 account_filters.yml 中的字段、账号映射和统计过滤口径。")
    channel_profiles = load_channel_profiles(Path("config/channel_profiles.yml"))
    with st.expander("渠道配置说明", expanded=True):
        st.markdown("渠道识别来自 config/channel_profiles.yml；文件名关键词用于识别上传渠道，字段别名优先于通用字段映射。")
        st.dataframe(render_channel_profiles_table(channel_profiles), width="stretch", hide_index=True)
    account_filters = load_account_filter_config(Path("config/account_filters.yml"))
    with st.expander("账号过滤配置", expanded=True):
        st.dataframe(localize_columns(account_filters.to_frame()), width="stretch", hide_index=True)
    references = load_reference_tables(Path("config/reference_tables.xlsx"))
    for sheet_name, frame in references.tables.items():
        with st.expander(sheet_name, expanded=sheet_name == "账号映射表"):
            st.dataframe(localize_columns(frame), width="stretch", hide_index=True)


def _content_review_type_options(queue: pd.DataFrame, current: pd.Series) -> list[str]:
    values: list[str] = []
    for column in ["content_category", "category_l2", "manual_category", "ai_category", "ledger_content_type"]:
        value = str(current.get(column, "") or "").strip()
        if value and value not in values:
            values.append(value)
    for column in ["content_category", "category_l2", "manual_category", "ai_category", "ledger_content_type"]:
        if column not in queue.columns:
            continue
        for value in queue[column].fillna("").astype(str).str.strip().tolist():
            if value and value not in values:
                values.append(value)
            if len(values) >= 8:
                return values
    return values or ["资讯", "股友说", "盘点", "大佬采访", "问财问句"]


def _page_category_review() -> None:
    st.markdown(
        f"""
        <div style="margin:0 0 .8rem 0;">
            <div style="font-size:.86rem;font-weight:700;color:#5f7394;margin-bottom:.2rem;">内容审核</div>
            <div style="font-size:2.05rem;line-height:1.1;font-weight:760;color:#10233f;">仅需审核重点内容</div>
            <div style="margin-top:.35rem;color:#5f7394;">
                队列默认收录每渠道 Top 20、单条消耗 2000 元以上、冲突和关键字段补齐失败内容；普通低风险 AI 分类结果不会全量进入审核队列。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    filter_cols = st.columns([0.18, 0.58, 0.24], gap="small")
    selected_batch_id, batches = _get_compact_period_selector("category_review", filter_cols[0], filter_cols[1])
    if not selected_batch_id:
        st.info("当前没有可审核内容。")
        return

    all_review_items = load_review_queue_for_batch(APP_DB, selected_batch_id)
    if all_review_items.empty:
        st.info("当前周期没有需要人工审核的重点内容。")
        return

    channel_options = ["全部渠道"] + sorted(value for value in all_review_items["channel"].fillna("").astype(str).unique() if value)
    with filter_cols[2]:
        selected_channel = st.selectbox("渠道", channel_options, key=f"content_review_channel_{selected_batch_id}", width="stretch")
    if selected_channel != "全部渠道":
        all_review_items = all_review_items[all_review_items["channel"].astype(str).eq(selected_channel)].copy()

    review_columns = [
        "ai_review_status",
        "ai_review_reason",
        "review_status",
        "needs_review",
        "audit_flags",
        "rank_in_channel",
        "review_reasons",
        "channel",
        "title",
        "content_url",
        "content_id",
        "material_id",
        "manual_category",
        "ai_category",
        "content_category",
        "category_l2",
        "category_source",
        "category_confidence",
        "ledger_match_source",
        "ledger_content_type",
        "match_risk_level",
        "match_risk_reason",
        "spend",
        "activations",
        "activation_cost",
        "source_file",
        "source_sheet",
    ]
    for column in review_columns:
        if column not in all_review_items.columns:
            all_review_items[column] = ""

    all_review_items = all_review_items.reset_index(drop=True).copy()
    all_review_items["needs_review"] = True
    all_review_items["ai_review_status"] = "需人工审核"
    all_review_items["audit_flags"] = all_review_items["review_reasons"].fillna("").astype(str)
    all_review_items["ai_review_reason"] = all_review_items["review_reasons"].fillna("").astype(str)
    all_review_items["rank_in_channel"] = pd.to_numeric(all_review_items["rank_in_channel"], errors="coerce")
    total_count = len(all_review_items)
    manual_count = int(all_review_items["needs_review"].astype(bool).sum())
    auto_count = int(total_count - manual_count)

    st.subheader("AI 初审")
    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("AI 已审素材", total_count)
    metric2.metric("自动通过", auto_count)
    metric3.metric("需人工确认", manual_count)
    metric4.metric("通过阈值", f"{AI_REVIEW_AUTO_PASS_THRESHOLD:.2f}")

    queue = all_review_items[all_review_items["needs_review"].astype(bool)].copy()
    queue = queue.sort_values(["spend", "rank_in_channel"], ascending=[False, True]).reset_index(drop=True)
    if queue.empty:
        st.success("当前筛选条件下没有需要人工确认的内容。")
        with st.expander("AI 已通过 / 全部 Top 素材预览"):
            preview_columns = [
                "ai_review_status",
                "ai_review_reason",
                "rank_in_channel",
                "channel",
                "title",
                "content_url",
                "content_category",
                "category_confidence",
                "spend",
            ]
            st.dataframe(localize_columns(all_review_items[preview_columns]), width="stretch", hide_index=True)
        return

    if "category_review_index" not in st.session_state:
        st.session_state["category_review_index"] = 0
    st.session_state["category_review_index"] = min(
        st.session_state["category_review_index"],
        max(len(queue) - 1, 0),
    )
    index = st.session_state["category_review_index"]
    current = queue.iloc[index]
    content_url = str(current.get("content_url", "") or "").strip()
    current_l2 = str(current.get("category_l2", "") or current.get("content_category", "") or current.get("manual_category", "") or current.get("ai_category", "") or "")

    queue_col, content_col, action_col = st.columns([0.95, 1.5, 1.05])
    with queue_col:
        st.subheader("人工异常队列")
        st.caption("仅展示高消耗、分渠道 Top、冲突或关键字段补齐失败内容。")
        for row_index, row in queue.head(10).iterrows():
            active = row_index == index
            marker = "▶ " if active else ""
            with st.container(border=True):
                st.markdown(f"**{marker}{row.get('channel', '-') or '-'} · #{row.get('rank_in_channel', '-') or '-'}**")
                st.write(str(row.get("title", "") or "-"))
                st.caption(
                    f"{row.get('audit_flags', '') or row.get('ai_review_reason', '') or '-'} | "
                    f"消耗 {_fmt_metric_number(row.get('spend', 0), 0)}"
                )

    with content_col:
        st.subheader("当前素材判断")
        st.markdown(f"**人工原因：{current.get('ai_review_reason', '') or current.get('audit_flags', '') or '-'}**")
        st.write(str(current.get("title", "") or "-"))
        st.caption(
            f"分渠道排名：{current.get('rank_in_channel', '') or '-'} | "
            f"分类来源：{current.get('category_source', '') or '-'} | "
            f"置信度：{_fmt_metric_number(current.get('category_confidence', ''), 2)}"
        )
        focus1, focus2 = st.columns(2)
        with focus1:
            st.markdown("**内容链接**")
            if content_url:
                st.write(content_url)
            else:
                st.error("缺链接，需要补齐")
        with focus2:
            st.markdown("**AI 内容类型建议**")
            st.write(current_l2 or "未匹配")
            st.caption(f"置信度 {_fmt_metric_number(current.get('category_confidence', ''), 2)}")
        st.caption(
            f"消耗 {_fmt_metric_number(current.get('spend', 0), 0)} | "
            f"激活数 {_fmt_metric_number(current.get('activations', 0), 0)} | "
            f"渠道排序 #{current.get('rank_in_channel', '-') or '-'}"
        )
        if str(current.get("match_risk_reason", "")).strip():
            st.text_area("类型/台账冲突", str(current.get("match_risk_reason", "")), height=90, disabled=True)
        with st.expander("低频排查字段"):
            st.write(
                {
                    "content_id": current.get("content_id", ""),
                    "material_id": current.get("material_id", ""),
                    "ledger_match_source": current.get("ledger_match_source", ""),
                    "ledger_content_type": current.get("ledger_content_type", ""),
                    "source_file": current.get("source_file", ""),
                    "source_sheet": current.get("source_sheet", ""),
                }
            )

    with action_col:
        st.subheader("确认结果")
        review_key_prefix = f"review_item_{index}"
        confirmed_url = st.text_input("内容链接", value=content_url, key=f"{review_key_prefix}_content_url")
        if confirmed_url.strip().lower().startswith(("http://", "https://")):
            st.link_button("打开校验", confirmed_url.strip(), width="stretch")
        else:
            st.caption("链接为空或格式异常时不会自动通过。")

        type_options = _content_review_type_options(all_review_items, current)
        selected_type = st.pills(
            "快捷内容类型",
            type_options,
            default=current_l2 if current_l2 in type_options else None,
            key=f"{review_key_prefix}_type_pills",
            width="stretch",
        )
        category_default = str(selected_type or current_l2 or "")
        category_l2 = st.text_input(
            "也可输入新类型",
            value=category_default,
            key=f"{review_key_prefix}_category_l2_{category_default}",
        )
        st.caption("人工确认会覆盖 AI 建议，并沉淀为历史映射。")

        if st.button("保存并下一条", type="primary", width="stretch"):
            payload = pd.DataFrame(
                [
                    {
                        "platform": current.get("channel", ""),
                        "platform_group": current.get("platform_group", ""),
                        "channel": current.get("channel", ""),
                        "content_id": current.get("content_id", ""),
                        "material_id": current.get("material_id", ""),
                        "title": current.get("title", ""),
                        "category_l2": category_l2,
                    }
                ]
            )
            saved = upsert_category_mappings(APP_DB, payload)
            url_saved = 0
            if confirmed_url.strip() and confirmed_url.strip() != content_url:
                url_saved = save_review_resolutions(
                    APP_DB,
                    selected_batch_id,
                    pd.DataFrame(
                        [
                            {
                                "issue_id": f"content-url:{index}:{current.get('content_id', '')}:{current.get('material_id', '')}",
                                "issue_type": "Top内容审核",
                                "review_action": "改字段",
                                "channel": current.get("channel", ""),
                                "title": current.get("title", ""),
                                "content_id": current.get("content_id", ""),
                                "material_id": current.get("material_id", ""),
                                "dedupe_key": current.get("dedupe_key", ""),
                                "duplicate_group_id": current.get("duplicate_group_id", ""),
                                "field_name": "content_url",
                                "new_value": confirmed_url.strip(),
                                "merge_target_content_id": "",
                            }
                        ]
                    ),
                )
            if saved == 0 and url_saved == 0:
                st.error("请先填写内容类型或新的内容链接，再保存当前审核。")
            else:
                st.session_state["category_review_index"] = min(index + 1, max(len(queue) - 1, 0))
                with st.spinner("正在同步当前周期数据..."):
                    result = apply_review_resolutions_and_regenerate(
                        APP_DB,
                        selected_batch_id,
                        output_root=APP_OUTPUTS,
                        processed_root=APP_PROCESSED,
                        category_rules_path=CATEGORY_RULES,
                        env_path=ENV_PATH,
                        output_mode="ui_only",
                        enable_deepseek=True,
                        enable_external_context=False,
                    )
                    _store_artifacts(result)
                st.success("当前条目已保存并同步到最终数据、图表和手动 AI 复盘输入。")
                st.rerun()

    with st.expander("AI 已通过 / 重点审核队列预览"):
        preview_columns = [
            "ai_review_status",
            "ai_review_reason",
            "rank_in_channel",
            "audit_flags",
            "channel",
            "title",
            "content_url",
            "content_id",
            "material_id",
            "content_category",
            "category_l2",
            "category_source",
            "category_confidence",
        ]
        st.dataframe(localize_columns(all_review_items[preview_columns]), width="stretch", hide_index=True)


def _render_channel_page(channel_name: str) -> None:
    topic_limit = channel_topic_limit(channel_name)
    topic_scope = f"消耗 Top {topic_limit} 重点内容类型" if topic_limit else "重点内容类型"
    _render_section_shell("", channel_name, f"按当前总览周期展示该渠道全部栏目数据和{topic_scope}。")
    st.markdown(f'<div id="channel-{html.escape(_html_anchor_id(channel_name))}"></div>', unsafe_allow_html=True)
    st.title(channel_name)
    st.page_link(_overview_page(), label="返回总览", icon=":material/arrow_back:", width="content")

    selected_batch_id, batches = _selected_or_latest_batch_id()
    if not selected_batch_id:
        st.info("还没有可分析的成功周期。请先到“生成页面数据”上传数据并生成。")
        return

    period_caption = _period_caption_for_batch(batches, selected_batch_id)
    if period_caption:
        st.caption(f"当前周期：{period_caption}。周期跟随“总览”页全局选择。")
    saved_report = load_manual_recap_report(APP_DB, selected_batch_id)
    _render_manual_recap_channel(saved_report.get("report", {}), channel_name)

    items = load_dashboard_items_for_batch(APP_DB, selected_batch_id)
    if items.empty:
        st.info("当前周期没有可分析的数据。")
        return

    previous_batch_id = _previous_batch_id_for_channel_page(batches, selected_batch_id)
    previous_items = load_dashboard_items_for_batch(APP_DB, previous_batch_id) if previous_batch_id else pd.DataFrame()
    channel_comparison = load_channel_comparison_for_batch(APP_DB, selected_batch_id)
    if channel_comparison.empty:
        channel_comparison = build_period_comparison_for_batch(APP_DB, selected_batch_id)
    channel_growth_row = _chart_comparison_by_channel(channel_comparison).get(channel_name)

    channel_items = items[items["channel"].eq(channel_name)].copy()
    if channel_items.empty:
        st.info(f"当前周期没有 {channel_name} 数据。")
        return

    st.subheader("渠道核心指标")
    channel_summary = aggregate_dashboard(channel_items, ["channel"])
    if not channel_summary.empty:
        _render_channel_summary_metrics(channel_summary, channel_growth_row)

    category_summary = summarize_channel_categories(items, channel_name)
    st.subheader("栏目汇总")
    if category_summary.empty:
        st.info("当前渠道没有可展示的栏目数据。")
    else:
        st.caption("栏目汇总仅保留表格，消耗对比以“重点内容类型贡献”图为主，避免重复展示同一口径。")
        st.dataframe(
            localize_columns(_category_table_display(category_summary)),
            width="stretch",
            hide_index=True,
        )

    st.subheader("重点内容类型贡献")
    if topic_limit <= 0:
        st.info("当前渠道没有足够数据生成重点内容类型分析。")
    else:
        topic_labels = load_topic_labels_for_batch(APP_DB, selected_batch_id)
        previous_topic_labels = load_topic_labels_for_batch(APP_DB, previous_batch_id) if previous_batch_id else pd.DataFrame()
        channel_topic_labels = topic_labels[topic_labels["channel"].astype(str).eq(str(channel_name))].copy() if not topic_labels.empty else pd.DataFrame()
        topic_summary = summarize_persisted_topic_labels(topic_labels, channel_name)
        content_type_summary = summarize_persisted_content_types(topic_labels, channel_name)
        if topic_summary.empty or content_type_summary.empty:
            st.warning("当前周期还没有固化的重点内容类型。请重新生成该周期，系统会在生成页面数据时完成内容类型固化。")
        else:
            previous_content_type_summary = summarize_persisted_content_types(previous_topic_labels, channel_name)
            content_type_comparison = compare_channel_topics(
                content_type_summary.rename(columns={"content_type": "topic_name"}),
                previous_content_type_summary.rename(columns={"content_type": "topic_name"}),
            ).rename(columns={"topic_name": "content_type"})
            st.caption(f"展示范围：{channel_name} 消耗 Top {topic_limit} 内容；内容类型已随周期固化，页面只读取入库结果。")
            topic_insights = build_channel_top_topic_insights(
                content_type_summary.rename(columns={"content_type": "topic_name"})
            ).replace("题材", "内容类型")
            if "重点内容类型贡献结论" not in topic_insights:
                topic_insights = f"#### 重点内容类型贡献结论\n{topic_insights}"
            st.markdown(topic_insights)
            _render_period_comparison_bar_chart(
                content_type_comparison,
                "content_type",
                "内容类型",
                f"{channel_name} 重点内容类型消耗",
            )
            _render_short_table_blocks(_topic_table_display(content_type_comparison), "重点内容类型明细", preserve_order=True)
            with st.expander("查看内容类型对应素材", expanded=False):
                st.dataframe(
                    localize_and_sort_columns(_topic_material_detail(channel_topic_labels)),
                    width="stretch",
                    hide_index=True,
                )

    top_content_links = summarize_channel_top_content_links(items, channel_name)
    if not top_content_links.empty:
        st.subheader("消耗 Top5 素材案例")
        _render_material_case_cards(top_content_links, channel_name)


def _render_channel_summary_metrics(channel_summary: pd.DataFrame, channel_growth_row: dict[str, object] | pd.Series | None = None) -> None:
    """渲染渠道汇总指标"""
    if channel_summary.empty:
        return
    row = channel_summary.iloc[0]
    metrics = [
        ("消耗", _fmt_metric_number(row.get("spend", 0), 0), "spend_change_rate", False),
        ("激活数", _fmt_metric_number(row.get("activations", 0), 0), "activations_change_rate", False),
        ("激活成本", _fmt_metric_number(row.get("activation_cost", 0), 1), "activation_cost_change_rate", True),
        ("付费数", _fmt_metric_number(row.get("first_pay_count", 0), 0), "first_pay_count_change_rate", False),
        ("付费成本", _fmt_metric_number(row.get("first_pay_cost", 0), 1), "first_pay_cost_change_rate", True),
        ("付费率", _fmt_percent(row.get("first_pay_rate", 0)), "first_pay_rate_change_rate", False),
    ]
    _render_metric_grid_with_deltas(
        [
            (
                label,
                value,
                _fmt_growth_delta(_growth_value(channel_growth_row, rate_column)) or "（-）",
                "normal" if is_cost else "inverse",
            )
            for label, value, rate_column, is_cost in metrics
        ],
        columns=3,
    )


def _selected_or_latest_batch_id() -> tuple[str, pd.DataFrame]:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return "", batches

    batch_ids = [str(value) for value in batches["batch_id"]]
    latest_batch_id = batch_ids[0]
    requested_batch_id = str(st.query_params.get("batch_id", "") or "")
    selected_batch_id = requested_batch_id if requested_batch_id in batch_ids else str(st.session_state.get("global_batch_id", latest_batch_id))
    if selected_batch_id not in batch_ids:
        selected_batch_id = latest_batch_id
    st.session_state["global_batch_id"] = selected_batch_id
    return selected_batch_id, batches


def _period_caption_for_batch(batches: pd.DataFrame, batch_id: str) -> str:
    if batches.empty or not batch_id:
        return ""
    match = batches[batches["batch_id"].astype(str).eq(str(batch_id))]
    if match.empty:
        return ""
    row = match.iloc[0]
    label = str(row.get("period_label", "") or "").strip()
    created_at = format_beijing_datetime(row.get("created_at", ""))
    return f"{label}｜生成时间 {created_at}" if created_at else label


def _previous_batch_id_for_channel_page(batches: pd.DataFrame, batch_id: str) -> str:
    if batches.empty or not batch_id:
        return ""
    match = batches[batches["batch_id"].astype(str).eq(str(batch_id))]
    if match.empty:
        return ""
    row = match.iloc[0]
    return previous_batch_from_rows(
        batches,
        str(row.get("period_start", "") or ""),
        str(row.get("period_level", "") or ""),
        str(row.get("period_key", "") or ""),
    )


def _channel_top_topic_candidates(items: pd.DataFrame, channel_name: str, top_n: int = 20) -> pd.DataFrame:
    if items.empty or "channel" not in items.columns:
        return pd.DataFrame()
    scoped = items[items["channel"].eq(channel_name)].copy()
    if scoped.empty or "spend" not in scoped.columns:
        return pd.DataFrame()
    scoped["spend"] = pd.to_numeric(scoped["spend"], errors="coerce")
    scoped = scoped.dropna(subset=["spend"])
    return scoped.sort_values("spend", ascending=False).head(int(top_n)).copy()


def _category_table_display(category_summary: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "category_name",
        "item_count",
        "spend",
        "impressions",
        "clicks",
        "ctr",
        "activations",
        "activation_cost",
        "first_pay_count",
        "first_pay_cost",
        "first_pay_rate",
    ]
    display = category_summary[[column for column in columns if column in category_summary.columns]].copy()
    return display.rename(columns={"category_name": "category_l2"})


def _topic_table_display(topic_summary: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "content_type",
        "spend_share",
        "spend",
        "spend_change_rate",
        "impressions",
        "activations",
        "activation_cost",
        "first_pay_count",
        "first_pay_cost",
        "first_pay_rate",
    ]
    display = topic_summary[[column for column in columns if column in topic_summary.columns]].copy()
    return display


def _top_content_links_display(top_content_links: pd.DataFrame) -> pd.DataFrame:
    columns = ["title", "spend", "cover_url", "content_url"]
    display = top_content_links[[column for column in columns if column in top_content_links.columns]].copy()
    for column in columns:
        if column not in display.columns:
            display[column] = ""
    display["title"] = display["title"].fillna("").astype(str).str.strip()
    display.loc[display["title"].eq(""), "title"] = "-"
    display["content_url"] = display["content_url"].fillna("").astype(str).str.strip()
    display.loc[display["content_url"].eq(""), "content_url"] = "-"
    display["spend"] = display["spend"].map(lambda value: _fmt_metric_number(value, 0))
    return display.rename(
        columns={
            "title": "标题",
            "spend": "消耗",
            "cover_url": "封面/素材链接",
            "content_url": "笔记/视频链接",
        }
    )


def _render_manual_recap_channel(report: object, channel_name: str) -> None:
    data = report if isinstance(report, dict) else {}
    channels = data.get("channels", []) if isinstance(data.get("channels", []), list) else []
    match = None
    for item in channels:
        if not isinstance(item, dict):
            continue
        if str(item.get("channel", "") or "").strip() == channel_name:
            match = item
            break
    if not match:
        st.info("当前渠道还没有手动 AI 复盘结论。可回到总览页点击“手动生成/更新 AI 复盘”。")
        return
    analysis_text = str(
        match.get("analysis", "")
        or "\n\n".join(
            part
            for part in [
                str(match.get("summary", "") or "").strip(),
                str(match.get("cause", "") or "").strip(),
            ]
            if part
        )
        or "暂无 AI 渠道复盘建议。"
    ).strip()
    direction_text = str(match.get("next_cycle_direction", "") or match.get("action", "") or "暂无下一周期执行方向。").strip()
    sections_html = _render_manual_recap_sections(match.get("sections", []))
    body_html = sections_html or _manual_recap_paragraph_html("表现判断 / 有效素材 / 原因判断", analysis_text)
    direction_html = (
        _manual_recap_paragraph_html("下一周期执行方向", direction_text)
        if _manual_recap_should_render_direction(match.get("sections", []))
        else ""
    )
    st.markdown(
        f"""
        <div class="manual-recap-card">
          <h4>{html.escape(channel_name)} AI 渠道复盘建议</h4>
          {body_html}
          {direction_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_material_case_cards(top_content_links: pd.DataFrame, channel_name: str) -> None:
    if top_content_links.empty:
        return
    cards = []
    for index, row in top_content_links.head(5).reset_index(drop=True).iterrows():
        title = str(row.get("title", "") or "").strip() or "-"
        cover_url = str(row.get("cover_url", "") or "").strip()
        content_url = str(row.get("content_url", "") or "").strip()
        spend = _fmt_metric_number(row.get("spend", 0), 0)
        toggle_id = f"cover-{_html_anchor_id(channel_name)}-{index}"
        thumb = (
            f'<label class="thumb" for="{html.escape(toggle_id)}"><img src="{html.escape(cover_url)}" alt="{html.escape(title)}"></label>'
            if cover_url
            else '<div class="thumb">无封面</div>'
        )
        link_html = (
            f'<a href="{html.escape(content_url)}" target="_blank" rel="noopener noreferrer">打开链接</a>'
            if content_url.startswith(("http://", "https://"))
            else '<span class="meta">无链接</span>'
        )
        preview_html = (
            '<input class="cover-toggle" '
            f'id="{html.escape(toggle_id)}" type="checkbox">'
            '<div class="cover-preview-backdrop">'
            '<div class="cover-preview-dialog">'
            f'<img src="{html.escape(cover_url)}" alt="{html.escape(title)}">'
            f'<div style="font-weight:650;color:#10233f;margin-bottom:0.65rem;">{html.escape(title)}</div>'
            f'<label class="cover-preview-close" for="{html.escape(toggle_id)}">关闭</label>'
            "</div>"
            "</div>"
            if cover_url
            else ""
        )
        cards.append(
            '<div class="material-case-card">'
            f"{preview_html}"
            f"{thumb}"
            '<div class="body">'
            f'<div class="title">{html.escape(title)}</div>'
            f'<div class="meta">消耗 {html.escape(spend)}</div>'
            f"<div>{link_html}</div>"
            "</div>"
            "</div>"
        )
    st.markdown(f'<div class="material-cases-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def _topic_material_detail(topic_labels: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "rank_position",
        "topic_name",
        "content_type",
        "title",
        "content_id",
        "spend",
        "activations",
        "activation_cost",
        "first_pay_count",
        "first_pay_cost",
        "first_pay_rate",
        "source",
    ]
    if topic_labels.empty:
        return pd.DataFrame(columns=columns)
    return topic_labels[[column for column in columns if column in topic_labels.columns]].copy()


def _render_short_table_blocks(frame: pd.DataFrame, label: str, rows_per_block: int = 8, *, preserve_order: bool = False) -> None:
    if frame.empty:
        return
    total_rows = len(frame)
    for start in range(0, total_rows, rows_per_block):
        block = frame.iloc[start : start + rows_per_block].copy()
        if total_rows > rows_per_block:
            st.caption(f"{label} {start + 1}-{start + len(block)} / {total_rows}")
        display = (localize_columns(block) if preserve_order else localize_and_sort_columns(block)).reset_index(drop=True)
        st.dataframe(display, width="stretch", hide_index=True)


def _display_generation_results() -> None:
    total_summary: pd.DataFrame = st.session_state["total_summary"]
    st.success(f"数据清洗并入库完成，周期标识：{st.session_state['batch_id']}")
    if st.session_state.get("historical_refresh_summary"):
        st.success(str(st.session_state["historical_refresh_summary"]))
    _render_metadata_enrichment_summary(st.session_state.get("metadata_enrichment_summary", {}))
    st.subheader("总体核心指标")
    summary = build_dashboard_summary(st.session_state["platform_summary"])
    _render_kpis(summary)

    st.subheader("分平台核心结果")
    _render_platform_kpis(st.session_state["platform_summary"])

    st.info("上传后仅执行数据清洗和页面展示。AI 复盘报告请到“总览”页手动生成，生成后会固定到下次手动更新。")
    with st.expander("账号覆盖校验"):
        st.dataframe(localize_columns(st.session_state["account_audit"]), width="stretch", hide_index=True)
    with st.expander("字段完整性报告"):
        st.dataframe(localize_columns(st.session_state["data_quality"]), width="stretch", hide_index=True)
    with st.expander("重点审核队列"):
        st.dataframe(localize_columns(st.session_state["review_queue"]), width="stretch", hide_index=True)
    with st.expander("分渠道总数据"):
        st.dataframe(localize_columns(st.session_state["platform_summary"]), width="stretch", hide_index=True)

    st.info("当前为页面数据模式：已保留清洗核验文件和页面展示数据，未生成下载产物或 AI 报告。")


def _render_metadata_enrichment_summary(summary: dict) -> None:
    if not summary or summary.get("mode") != "safe_public":
        return
    hint_rows = int(summary.get("hint_rows") or summary.get("conflict_rows") or 0)
    metrics = [
        ("自动补全行数", int(summary.get("filled_rows") or 0)),
        ("记录提示行数", hint_rows),
        ("高消耗需复核行数", int(summary.get("review_rows") or 0)),
        ("公开接口失败行数", int(summary.get("error_rows") or 0)),
    ]
    st.caption(
        "公开信息补充："
        + "；".join(f"{label} {value}" for label, value in metrics)
        + f"；缓存命中 {int(summary.get('cache_hits') or 0)}"
    )


def _render_kpis(summary) -> None:
    metrics = [
        ("总消耗", format_display_number(summary.total_spend, 0)),
        ("激活数", format_display_number(summary.activations, 0)),
        ("激活成本", format_display_number(summary.activation_cost, 1)),
        ("付费数", format_display_number(summary.first_pay_count, 0)),
        ("付费成本", format_display_number(summary.first_pay_cost, 1)),
        ("付费率", f"{format_display_number(summary.first_pay_rate * 100, 1)}%"),
    ]
    _render_metric_grid(metrics, columns=3)


def _render_growth_overview(channel_comparison: pd.DataFrame) -> None:
    if channel_comparison.empty:
        st.info("暂无可用环比数据：需要至少两个成功周期。")
        return

    comparison = channel_comparison.copy()
    total_rows = comparison[comparison["channel"].fillna("").astype(str).eq("总计")]
    if total_rows.empty:
        st.info("暂无总计环比数据。")
        return

    total = total_rows.iloc[0]
    metric_items = list(GROWTH_METRICS.items())
    for start in range(0, len(metric_items), 3):
        batch = metric_items[start : start + 3]
        cols = st.columns(len(batch))
        for col, (label, (value_column, rate_column, digits, delta_color)) in zip(cols, batch):
            col.metric(
                label,
                _fmt_metric_number(total.get(value_column, 0), digits),
                delta=_fmt_growth_delta(total.get(rate_column)),
                delta_color=delta_color,
            )

    channel_rows = comparison[~comparison["channel"].fillna("").astype(str).eq("总计")].copy()
    if channel_rows.empty:
        return

    metric_name = st.selectbox(
        "选择环比指标",
        list(GROWTH_METRICS.keys()),
        key="overview_growth_metric",
        width="stretch",
    )
    _, rate_column, _, _ = GROWTH_METRICS[metric_name]
    channel_rows[rate_column] = pd.to_numeric(channel_rows[rate_column], errors="coerce")
    channel_rows = channel_rows.dropna(subset=[rate_column])
    if channel_rows.empty:
        st.info("当前指标没有可计算的分渠道环比。")
        return

    _render_vertical_bar_chart(
        channel_rows.sort_values(rate_column, ascending=metric_sort_ascending(rate_column)),
        "channel",
        rate_column,
        "渠道",
        f"{metric_name}环比",
        f"分渠道{metric_name}环比增长",
        rate_column,
        f"{metric_name}环比",
    )


def _render_platform_kpis(platform_summary: pd.DataFrame, channel_comparison: pd.DataFrame | None = None) -> None:
    if platform_summary.empty:
        st.info("当前没有可展示的平台汇总数据。")
        return

    display = platform_summary.copy()
    growth_by_channel = _platform_growth_by_channel(channel_comparison)
    name_column = "channel" if "channel" in display.columns else display.columns[0]
    spend_column = "spend"
    activation_column = "activations"
    activation_cost_column = "activation_cost"
    first_pay_column = "first_pay_count"
    first_pay_cost_column = "first_pay_cost"
    first_pay_rate_column = "first_pay_rate"

    for start in range(0, len(display), 2):
        batch = display.iloc[start : start + 2]
        cols = st.columns(len(batch))
        for col, (_, row) in zip(cols, batch.iterrows()):
            with col:
                platform_name = str(row.get(name_column, "")).strip()
                growth_row = growth_by_channel.get(platform_name)
                st.markdown(f"**{platform_name}**")
                metrics = [
                    ("消耗", _fmt_metric_number(row.get(spend_column, 0), 0), "spend_change_rate"),
                    ("激活数", _fmt_metric_number(row.get(activation_column, 0), 0), "activations_change_rate"),
                    ("激活成本", _fmt_metric_number(row.get(activation_cost_column, 0), 1), "activation_cost_change_rate"),
                    ("付费数", _fmt_metric_number(row.get(first_pay_column, 0), 0), "first_pay_count_change_rate"),
                    ("付费成本", _fmt_metric_number(row.get(first_pay_cost_column, 0), 1), "first_pay_cost_change_rate"),
                    ("付费率", _fmt_percent(row.get(first_pay_rate_column, 0)), "first_pay_rate_change_rate"),
                ]
                for metric_start in range(0, len(metrics), 2):
                    metric_batch = metrics[metric_start : metric_start + 2]
                    metric_cols = st.columns(len(metric_batch))
                    for metric_col, (label, value, rate_column) in zip(metric_cols, metric_batch):
                        metric_col.metric(
                            label,
                            value,
                            delta=_platform_growth_delta(growth_row, rate_column),
                            delta_color=_platform_growth_delta_color(rate_column),
                        )


def _platform_growth_by_channel(channel_comparison: pd.DataFrame | None) -> dict[str, pd.Series]:
    if channel_comparison is None or channel_comparison.empty or "channel" not in channel_comparison.columns:
        return {}
    comparison = channel_comparison.copy()
    comparison["channel"] = comparison["channel"].fillna("").astype(str).str.strip()
    comparison = comparison[comparison["channel"].ne("") & comparison["channel"].ne("总计")]
    return {str(row["channel"]): row for _, row in comparison.iterrows()}


def _platform_growth_delta(growth_row: pd.Series | None, rate_column: str) -> str | None:
    if growth_row is None or rate_column not in growth_row:
        return None
    return _fmt_growth_delta(growth_row.get(rate_column))


def _platform_growth_delta_color(rate_column: str) -> str:
    if rate_column in {"activation_cost_change_rate", "first_pay_cost_change_rate"}:
        return "inverse"
    return "normal"


def _render_metric_grid(metrics: list[tuple[str, str]], columns: int) -> None:
    for start in range(0, len(metrics), columns):
        batch = metrics[start : start + columns]
        cols = st.columns(len(batch))
        for col, (label, value) in zip(cols, batch):
            col.metric(label, value)


def _render_metric_grid_with_deltas(metrics: list[tuple[str, str, str, str]], columns: int) -> None:
    for start in range(0, len(metrics), columns):
        batch = metrics[start : start + columns]
        cols = st.columns(len(batch))
        for col, (label, value, delta, delta_color) in zip(cols, batch):
            col.metric(label, value, delta=delta, delta_color=delta_color)


def _growth_value(growth_row: dict[str, object] | pd.Series | None, rate_column: str) -> object:
    if growth_row is None:
        return pd.NA
    if isinstance(growth_row, pd.Series):
        return growth_row.get(rate_column, pd.NA)
    return growth_row.get(rate_column, pd.NA)


def _fmt_metric_number(value: object, digits: int) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "-"
    return format_display_number(numeric, max_decimals=max(digits, 0))


def _fmt_percent(value: object) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "-"
    return f"{format_display_number(float(numeric) * 100, max_decimals=1)}%"


def _fmt_growth_delta(value: object) -> str | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    sign = "+" if float(numeric) > 0 else ""
    return f"{sign}{format_display_number(float(numeric) * 100, max_decimals=1)}%"


def _render_vertical_bar_chart(
    frame: pd.DataFrame,
    x_column: str,
    y_column: str,
    x_label: str,
    y_label: str,
    title: str,
    color_column: str | None = None,
    color_label: str = "",
) -> None:
    if frame.empty or x_column not in frame.columns or y_column not in frame.columns:
        return

    chart = frame.copy()
    chart[x_column] = chart[x_column].fillna("").astype(str).str.strip()
    chart = chart[chart[x_column].ne("")]
    chart[y_column] = pd.to_numeric(chart[y_column], errors="coerce")
    chart = chart.dropna(subset=[y_column])
    if chart.empty:
        return

    chart["__bar_text"] = chart[y_column].map(lambda value: _format_chart_value(value, y_column))
    chart["__axis_label"] = chart[x_column].map(_compact_axis_label)
    usable_color_column = _usable_color_column(chart, color_column)
    color_target = usable_color_column or "__axis_label"
    labels = {
        "__axis_label": x_label,
        x_column: x_label,
        y_column: y_label,
        color_target: color_label or x_label,
    }
    color_kwargs = (
        {"color_continuous_scale": BAR_CONTINUOUS_SCALE}
        if usable_color_column
        else {"color_discrete_sequence": BAR_COLOR_SEQUENCE}
    )
    fig = px.bar(
        chart,
        x="__axis_label",
        y=y_column,
        color=color_target,
        text="__bar_text",
        labels=labels,
        title=title,
        custom_data=[x_column, "__bar_text"],
        **color_kwargs,
    )
    fig.update_traces(
        texttemplate="%{text}",
        textposition="outside",
        cliponaxis=False,
        marker_line_width=0.8,
        marker_line_color="rgba(255,255,255,0.72)",
        hovertemplate=f"{x_label}：%{{customdata[0]}}<br>{y_label}：%{{customdata[1]}}<extra></extra>",
    )
    fig.update_layout(
        template="plotly_white",
        height=max(360, min(680, 300 + len(chart) * 22)),
        margin=dict(l=20, r=20, t=68, b=92),
        bargap=0.28,
        showlegend=False,
        plot_bgcolor="rgba(255,255,255,0.96)",
        paper_bgcolor="rgba(255,255,255,0)",
        font=dict(color="#10233f"),
        title=dict(font=dict(size=18, color="#10233f")),
    )
    fig.update_xaxes(title=x_label, tickangle=-25, automargin=True)
    fig.update_yaxes(title=y_label, tickformat=_axis_tick_format(y_column), gridcolor="rgba(95,115,148,0.16)")
    st.plotly_chart(fig)


def _compact_axis_label(value: object, limit: int = 12) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _usable_color_column(frame: pd.DataFrame, color_column: str | None) -> str:
    if not color_column or color_column not in frame.columns:
        return ""
    values = pd.to_numeric(frame[color_column], errors="coerce")
    if values.notna().any():
        frame[color_column] = values
        return color_column
    return ""


def _format_chart_value(value: object, metric: str) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return ""
    if metric in RATE_METRIC_COLUMNS:
        return f"{format_display_number(float(numeric) * 100, 1)}%"
    return format_display_number(numeric, 1 if metric in {"activation_cost", "first_pay_cost"} else 0)


def _bar_text_template(metric: str) -> str:
    if metric in RATE_METRIC_COLUMNS:
        return "%{text:.1%}"
    if metric in {"activation_cost", "first_pay_cost"}:
        return "%{text:,.1f}"
    return "%{text:,.0f}"


def _axis_tick_format(metric: str) -> str:
    if metric in RATE_METRIC_COLUMNS:
        return ".0%"
    if metric in {"activation_cost", "first_pay_cost"}:
        return ",.1f"
    return ",.0f"


def _render_period_comparison_bar_chart(
    frame: pd.DataFrame,
    label_column: str,
    x_label: str,
    title: str,
) -> None:
    if frame.empty or label_column not in frame.columns or "spend" not in frame.columns:
        return
    chart = frame.copy()
    chart[label_column] = chart[label_column].fillna("").astype(str).str.strip()
    chart = chart[chart[label_column].ne("")]
    chart["spend"] = pd.to_numeric(chart["spend"], errors="coerce")
    chart["spend_previous"] = pd.to_numeric(chart.get("spend_previous", pd.NA), errors="coerce")
    chart["spend_change_rate"] = pd.to_numeric(chart.get("spend_change_rate", pd.NA), errors="coerce")
    chart = chart.dropna(subset=["spend"])
    if chart.empty:
        return

    chart["__axis_label"] = chart[label_column].map(_compact_axis_label)
    chart["__current_text"] = chart["spend"].map(lambda value: _format_chart_value(value, "spend"))
    chart["__previous_text"] = chart["spend_previous"].map(lambda value: _format_chart_value(value, "spend") or "-")
    chart["__growth_text"] = chart["spend_change_rate"].map(lambda value: _fmt_growth_delta(value) or "（-）")
    chart["__current_bar_text"] = chart["__current_text"] + "<br>环比 " + chart["__growth_text"]
    chart["__current_index"] = 100.0
    chart["__previous_index"] = (
        chart["spend_previous"] / chart["spend"].where(chart["spend"].gt(0)) * 100
    )
    chart["__previous_index_plot"] = chart["__previous_index"].fillna(0.0)
    customdata = chart[[label_column, "__current_text", "__previous_text", "__growth_text"]].to_numpy()
    y_max = pd.concat([chart["__current_index"], chart["__previous_index_plot"]], ignore_index=True).max(skipna=True)
    y_range = [0, max(118.0, float(y_max) * 1.18)] if pd.notna(y_max) and float(y_max) > 0 else [0, 118]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=chart["__axis_label"],
            y=chart["__current_index"],
            text=chart["__current_bar_text"],
            customdata=customdata,
            marker={"color": "#0A84FF", "line": {"width": 0.8, "color": "rgba(255,255,255,0.72)"}},
            name="本期消耗",
            offsetgroup="本期",
            texttemplate="%{text}",
            textposition="outside",
            cliponaxis=False,
            hovertemplate=f"{x_label}：%{{customdata[0]}}<br>本期消耗：%{{customdata[1]}}<br>环比：%{{customdata[3]}}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=chart["__axis_label"],
            y=chart["__previous_index_plot"],
            text=chart["__previous_text"],
            customdata=customdata,
            marker={"color": "#8E9AAF", "line": {"width": 0.8, "color": "rgba(255,255,255,0.72)"}},
            name="上期消耗",
            offsetgroup="上期",
            texttemplate="%{text}",
            textposition="outside",
            cliponaxis=False,
            hovertemplate=f"{x_label}：%{{customdata[0]}}<br>上期消耗：%{{customdata[2]}}<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=PERIOD_COMPARISON_CHART_HEIGHT,
        margin=dict(l=20, r=20, t=68, b=92),
        bargap=0.24,
        bargroupgap=0.08,
        showlegend=True,
        uniformtext=dict(mode="show", minsize=10),
        plot_bgcolor="rgba(255,255,255,0.96)",
        paper_bgcolor="rgba(255,255,255,0)",
        font=dict(color="#10233f"),
        title=dict(text=title, font=dict(size=18, color="#10233f")),
        legend=dict(yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(title=x_label, tickangle=-25, automargin=True)
    fig.update_yaxes(title="周期对比指数（本期=100）", tickformat=",.0f", range=y_range, gridcolor="rgba(95,115,148,0.16)")
    st.plotly_chart(fig, config={"displayModeBar": False})


def _render_platform_chart(
    platform_summary: pd.DataFrame,
    channel_comparison: pd.DataFrame | None = None,
) -> None:
    fig = _build_platform_chart_figure(platform_summary, channel_comparison)
    if fig is None:
        return
    st.plotly_chart(
        fig,
        config={"displayModeBar": False},
    )


def _build_platform_chart_figure(
    platform_summary: pd.DataFrame,
    channel_comparison: pd.DataFrame | None = None,
) -> go.Figure | None:
    if platform_summary.empty:
        return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    trace_meta: list[dict[str, object]] = []
    comparison_by_channel = _chart_comparison_by_channel(channel_comparison)
    for metric_name, (y_metric, y_label, is_cost) in CHART_METRICS.items():
        if "channel" not in platform_summary.columns or y_metric not in platform_summary.columns:
            continue
        chart = platform_summary.copy()
        chart["channel"] = chart["channel"].fillna("").astype(str).str.strip()
        chart = chart[chart["channel"].ne("")]
        chart[y_metric] = pd.to_numeric(chart[y_metric], errors="coerce")
        chart = chart.dropna(subset=[y_metric])
        if chart.empty:
            continue
        previous_column = f"{y_metric}_previous"
        change_column = f"{y_metric}_change_rate"
        chart[previous_column] = chart["channel"].map(
            lambda channel: comparison_by_channel.get(str(channel), {}).get(previous_column, pd.NA)
        )
        chart[change_column] = chart["channel"].map(
            lambda channel: comparison_by_channel.get(str(channel), {}).get(change_column, pd.NA)
        )
        chart[previous_column] = pd.to_numeric(chart[previous_column], errors="coerce")
        chart[change_column] = pd.to_numeric(chart[change_column], errors="coerce")
        chart = chart.sort_values(
            [y_metric],
            ascending=[metric_sort_ascending(y_metric)],
            na_position="last",
        )
        chart["__bar_text"] = chart[y_metric].map(lambda value: _format_chart_value(value, y_metric))
        chart["__previous_text"] = chart[previous_column].map(lambda value: _format_chart_value(value, y_metric))
        chart["__growth_text"] = chart[change_column].map(_fmt_growth_delta)
        chart["__axis_label"] = chart["channel"].map(_compact_axis_label)
        trace_index = len(trace_meta) * 3
        visible = len(trace_meta) == 0
        fig.add_trace(
            go.Bar(
                x=chart["__axis_label"],
                y=chart[y_metric],
                text=chart["__bar_text"],
                customdata=chart[["channel", "__bar_text"]].to_numpy(),
                marker={"color": "#0A84FF", "line": {"width": 0.8, "color": "rgba(255,255,255,0.72)"}},
                name=f"{metric_name} 本期",
                legendgroup=metric_name,
                offsetgroup="本期",
                visible=visible,
                texttemplate="%{text}",
                textposition="outside",
                cliponaxis=False,
                hovertemplate=f"渠道：%{{customdata[0]}}<br>本期实际{y_label}：%{{customdata[1]}}<extra></extra>",
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Bar(
                x=chart["__axis_label"],
                y=chart[previous_column],
                text=chart["__previous_text"],
                customdata=chart[["channel", "__previous_text"]].to_numpy(),
                marker={"color": "#8E9AAF", "line": {"width": 0.8, "color": "rgba(255,255,255,0.72)"}},
                name=f"{metric_name} 上期",
                legendgroup=metric_name,
                offsetgroup="上期",
                visible=visible,
                texttemplate="%{text}",
                textposition="outside",
                cliponaxis=False,
                hovertemplate=f"渠道：%{{customdata[0]}}<br>上期实际{y_label}：%{{customdata[1]}}<extra></extra>",
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=chart["__axis_label"],
                y=chart[change_column] * 100,
                text=chart["__growth_text"],
                customdata=chart[["channel", "__growth_text"]].to_numpy(),
                mode="lines+markers+text",
                marker={"color": "#FF9F0A" if not is_cost else "#BF5AF2", "size": 9, "symbol": "diamond" if is_cost else "circle"},
                line={"color": "#FF9F0A" if not is_cost else "#BF5AF2", "width": 2},
                name=f"{metric_name} 环比",
                legendgroup=metric_name,
                visible=visible,
                texttemplate="%{text}",
                textposition="top center",
                hovertemplate=f"渠道：%{{customdata[0]}}<br>{y_label}环比：%{{customdata[1]}}<extra></extra>",
            ),
            secondary_y=True,
        )
        trace_meta.append(
            {
                "metric_name": metric_name,
                "y_metric": y_metric,
                "y_label": y_label,
                "title": f"分渠道{y_label}对比",
                "height": max(360, min(680, 300 + len(chart) * 22)),
                "trace_start": trace_index,
            }
        )

    if not trace_meta:
        return None

    buttons = []
    for button_index, meta in enumerate(trace_meta):
        visible = [False] * (len(trace_meta) * 3)
        start = int(meta["trace_start"])
        for index in range(start, start + 3):
            visible[index] = True
        buttons.append(
            {
                "label": str(meta["metric_name"]),
                "method": "update",
                "args": [
                    {"visible": visible},
                    {
                        "title.text": str(meta["title"]),
                        "yaxis.title.text": str(meta["y_label"]),
                        "yaxis.tickformat": ",.0f",
                        "yaxis.range": None,
                        "yaxis2.title.text": "环比",
                        "yaxis2.tickformat": ".1f",
                        "height": int(meta["height"]),
                    },
                ],
            }
        )

    initial = trace_meta[0]
    fig.update_layout(
        template="plotly_white",
        height=int(initial["height"]),
        margin=dict(l=20, r=20, t=104, b=92),
        bargap=0.28,
        showlegend=True,
        plot_bgcolor="rgba(255,255,255,0.96)",
        paper_bgcolor="rgba(255,255,255,0)",
        font=dict(color="#10233f"),
        title=dict(text=str(initial["title"]), font=dict(size=18, color="#10233f")),
        legend=dict(yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis2=dict(title="环比", ticksuffix="%", gridcolor="rgba(255,255,255,0)", zeroline=True),
        updatemenus=[
            {
                "type": "buttons",
                "direction": "right",
                "active": 0,
                "x": 0,
                "y": 1.2,
                "xanchor": "left",
                "yanchor": "top",
                "pad": {"r": 8, "t": 0},
                "buttons": buttons,
            }
        ],
    )
    fig.update_xaxes(title="渠道", tickangle=-25, automargin=True)
    fig.update_yaxes(
        title=str(initial["y_label"]),
        tickformat=",.0f",
        gridcolor="rgba(95,115,148,0.16)",
        secondary_y=False,
    )
    fig.update_yaxes(
        title="环比",
        ticksuffix="%",
        gridcolor="rgba(255,255,255,0)",
        zeroline=True,
        secondary_y=True,
    )
    return fig


def _chart_comparison_by_channel(channel_comparison: pd.DataFrame | None) -> dict[str, dict[str, object]]:
    if channel_comparison is None or channel_comparison.empty or "channel" not in channel_comparison.columns:
        return {}
    comparison = channel_comparison.copy()
    comparison["channel"] = comparison["channel"].fillna("").astype(str).str.strip()
    comparison = comparison[comparison["channel"].ne("")]
    return {str(row["channel"]): row.to_dict() for _, row in comparison.iterrows()}


def _content_filter_panel(items: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    with st.container(border=True):
        c1, c2 = st.columns(2)
        platforms = c1.multiselect("渠道", _dashboard_options(items, "channel"), key=f"{key_prefix}_platforms")
        content_categories = c2.multiselect(
            "栏目",
            _dashboard_options(items, "category_l2"),
            key=f"{key_prefix}_content_categories",
        )
        text_query = st.text_input("标题 / 内容ID / 素材ID / 账号ID / 账号 / 作者", key=f"{key_prefix}_query")
    return filter_dashboard_items(
        items,
        DashboardFilters(
            channels=tuple(platforms),
            content_categories=tuple(content_categories),
            text_query=text_query,
        ),
    )


def _metric_value(summary: pd.DataFrame, channel: str, column: str) -> float:
    if summary.empty:
        return 0.0
    row = summary[summary["channel"].eq(channel)]
    if row.empty or column not in row.columns:
        return 0.0
    value = pd.to_numeric(row[column], errors="coerce").iloc[0]
    return 0.0 if pd.isna(value) else float(value)


def _dashboard_options(items: pd.DataFrame, column: str) -> list[str]:
    if column not in items.columns:
        return []
    values = items[column].fillna("").astype(str).str.strip()
    return sorted(value for value in values.unique() if value)


def _localized_label_map() -> dict[str, str]:
    return {column: localize_columns(pd.DataFrame(columns=[column])).columns[0] for column in [
        "channel",
        "title",
        "account_id",
        "account_raw",
        "account",
        "account_mapping_source",
        "content_id",
        "material_id",
        "category_l2",
        "review_status",
        "needs_manual_review",
        "review_reasons",
        "spend",
        "activations",
        "source_file",
    ]}


def _available_channels_for_current_period() -> list[str]:
    selected_batch_id, _ = _selected_or_latest_batch_id()
    if not selected_batch_id:
        return []
    items = load_dashboard_items_for_batch(APP_DB, selected_batch_id)
    return _dashboard_options(items, "channel")


def _make_channel_page(channel: str):
    def _page_channel() -> None:
        _render_channel_page(channel)

    safe_name = _html_anchor_id(channel).replace("-", "_")
    _page_channel.__name__ = f"_page_channel_{safe_name}"
    return _page_channel


def _overview_page():
    return st.Page(_page_overview, title="总览", default=True)


def _generate_page():
    return st.Page(_page_generate, title="生成页面数据")


def _channel_pages_for_current_period() -> dict[str, object]:
    return {
        channel: st.Page(_make_channel_page(channel), title=channel, url_path=_channel_page_path(channel))
        for channel in _available_channels_for_current_period()
    }


def _build_navigation_pages() -> list:
    pages = [
        _overview_page(),
        _generate_page(),
    ]
    pages.extend(_channel_pages_for_current_period().values())
    pages.extend(
        [
            st.Page(_page_trends, title="历史趋势"),
            st.Page(_page_reference_tables, title="维护台账"),
            st.Page(_page_category_review, title="内容审核"),
        ]
    )
    return pages


_inject_theme()

page = st.navigation(_build_navigation_pages(), position="sidebar", expanded=True)
page.run()
