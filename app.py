from __future__ import annotations

import base64
import html
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components

from ops_data_workflow.ai import resolve_deepseek_settings
from ops_data_workflow.dashboard import (
    DashboardFilters,
    aggregate_dashboard,
    build_channel_top_topic_insights,
    build_overview_table_rows,
    build_period_comparison_for_batch,
    build_content_recommendations,
    build_dashboard_summary,
    dashboard_detail_items,
    detect_high_metric_anomalies,
    filter_dashboard_items,
    format_beijing_datetime,
    load_all_dashboard_items,
    load_channel_comparison_for_batch,
    load_data_quality_for_batch,
    load_dashboard_items_for_batch,
    load_latest_data_quality,
    load_latest_dashboard_items,
    load_latest_review_queue,
    load_review_queue_for_batch,
    list_successful_dashboard_batches,
    metric_sort_ascending,
    summarize_channel_categories,
    summarize_topics_for_selection,
    summarize_content_type_trends,
    summarize_content_types,
    summarize_unique_content,
)
from ops_data_workflow.reporting import format_display_number, localize_columns, localize_and_sort_columns
from ops_data_workflow.account_filters import load_account_filter_config
from ops_data_workflow.external_context import fetch_external_context
from ops_data_workflow.reference_tables import load_reference_tables
from ops_data_workflow.raw_sync import sync_raw_periods
from ops_data_workflow.periods import PERIOD_LEVEL_LABELS, PERIOD_LEVELS, PERIOD_LEVEL_WEEK, SOURCE_TYPE_UPLOAD, review_period_from_dates
from ops_data_workflow.raw_cleaning import clean_raw_period_dir
from ops_data_workflow.raw_normalization import (
    detect_normalized_upload_channel_conflicts,
    normalize_uploaded_periods,
    preview_uploaded_period_buckets,
    preview_uploaded_periods,
)
from ops_data_workflow.recap import build_recap_summary
from ops_data_workflow.review_resolutions import (
    REVIEW_ACTIONS,
    apply_review_resolutions_and_regenerate,
    load_data_review_items,
    save_review_resolutions,
)
from ops_data_workflow.rollups import rollup_period_for, select_rollup_component_batches
from ops_data_workflow.storage import (
    delete_batch_permanently,
    list_file_backups,
    load_topic_labels_for_batch,
    move_batch_to_file_backup,
    restore_file_backup,
    upsert_category_mappings,
)
from ops_data_workflow.topic_analysis import channel_topic_limit, summarize_persisted_topic_labels
from ops_data_workflow.upload_input import detect_upload_channel_conflicts, infer_period_from_upload_names, materialize_uploaded_files
from ops_data_workflow.workflow import run_archived_workflow, run_rollup_workflow


APP_DB = Path("data/workflow.sqlite3")
APP_RAW = Path("data/raw")
APP_FILE_BACKUP = Path("data/file_backup")
APP_ARCHIVE = Path("archive")
APP_OUTPUTS = Path("outputs")
OVERVIEW_CACHE_VERSION = 2
CATEGORY_RULES = Path("config/category_rules.yml")
ENV_PATH = Path(".env")
TREND_QUICK_RANGES = ["全部", "一周", "两周", "一个月"]
TREND_RANGE_DAYS = {"一周": 7, "两周": 14, "一个月": 30}
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
    "正在整理 raw 周期目录",
    "正在归档原始文件",
    "正在读取渠道数据并标准化",
    "正在校验数据质量与题材分类",
    "正在固化重点题材",
    "正在写入周期库并生成当前下载文件",
    "报告生成完成",
)
GENERATION_PROGRESS_VALUES = {
    "正在识别上传文件和复盘周期": 5,
    "正在整理 raw 周期目录": 15,
    "正在归档原始文件": 25,
    "正在读取渠道数据并标准化": 45,
    "正在校验数据质量与题材分类": 70,
    "正在固化重点题材": 82,
    "正在写入周期库并生成当前下载文件": 90,
    "报告生成完成": 95,
}
GROWTH_METRICS = {
    "消耗": ("spend_current", "spend_change_rate", 0, "normal"),
    "激活数": ("activations_current", "activations_change_rate", 0, "normal"),
    "付费数": ("first_pay_count_current", "first_pay_count_change_rate", 0, "normal"),
    "激活成本": ("activation_cost_current", "activation_cost_change_rate", 1, "inverse"),
    "付费成本": ("first_pay_cost_current", "first_pay_cost_change_rate", 1, "inverse"),
}
BILIBILI_CATEGORY = "B站全部"
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
    st.session_state["report_html"] = result.report_html.read_bytes()
    st.session_state["analysis_xlsx"] = result.analysis_xlsx.read_bytes()
    st.session_state["canonical_csv"] = result.canonical_csv.read_bytes()
    st.session_state["total_summary_xlsx"] = result.total_summary_xlsx.read_bytes()
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


def _trend_window_from_quick_range(
    quick_range: str,
    default_start: date,
    default_end: date,
) -> tuple[date, date]:
    if quick_range not in TREND_RANGE_DAYS:
        return default_start, default_end
    days = TREND_RANGE_DAYS[quick_range]
    return default_end - timedelta(days=days - 1), default_end


def _init_trend_filters(default_start: date, default_end: date) -> None:
    if "trend_quick_range" not in st.session_state:
        st.session_state["trend_quick_range"] = "全部"
    if "trend_period_start" not in st.session_state:
        st.session_state["trend_period_start"] = default_start
    if "trend_period_end" not in st.session_state:
        st.session_state["trend_period_end"] = default_end


def _on_trend_quick_range_change() -> None:
    default_start = st.session_state.get("trend_default_start", date.today())
    default_end = st.session_state.get("trend_default_end", date.today())
    selected = st.session_state.get("trend_quick_range", "全部")
    start, end = _trend_window_from_quick_range(selected, default_start, default_end)
    st.session_state["trend_period_start"] = start
    st.session_state["trend_period_end"] = end


def _run_raw_sync() -> list:
    if st.session_state.get("raw_sync_running"):
        return []
    st.session_state["raw_sync_running"] = True
    try:
        results = sync_raw_periods(
            APP_RAW,
            db_path=APP_DB,
            output_root=APP_OUTPUTS,
            archive_root=APP_ARCHIVE,
            category_rules_path=CATEGORY_RULES,
            env_path=ENV_PATH,
        )
    finally:
        st.session_state["raw_sync_running"] = False

    generated = [item for item in results if item.status == "generated"]
    errors = [item for item in results if item.status == "error"]
    if generated:
        st.session_state["raw_sync_notice"] = f"已自动刷新 {len(generated)} 个 raw 周期。"
    elif errors:
        st.session_state["raw_sync_notice"] = "raw 自动刷新遇到错误：" + "；".join(
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
        buckets = normalize_uploaded_periods(
            uploaded,
            APP_RAW,
            default_year=date.today().year,
            replace_same_channel=replace_same_channel,
        )
        return [
            clean_raw_period_dir(bucket.raw_dir, bucket.review_period, default_year=date.today().year)
            for bucket in buckets
        ]
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
    st.caption("系统已按路径识别复盘周期；生成时会写入 period_manifest.json，可在生成前核对复盘层级和数据时间。")
    st.dataframe(preview, width="stretch", hide_index=True)


def _generate_upload_conflict_labels(uploaded, preview_buckets: list, period_start: date, period_end: date) -> list[str]:
    if not uploaded:
        return []
    try:
        if preview_buckets:
            conflicts = detect_normalized_upload_channel_conflicts(uploaded, APP_RAW, default_year=date.today().year)
            return [
                f"{conflict.review_period.period_label}：{conflict.channel}"
                for conflict in conflicts
            ]
        period_dir = f"{period_start:%Y%m%d}-{period_end:%Y%m%d}"
        conflicts = detect_upload_channel_conflicts(
            uploaded,
            APP_RAW / period_dir,
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


def _update_bucket_manifest(bucket, period) -> None:
    try:
        payload = json.loads(bucket.manifest_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload.update(
        {
            "period_level": period.period_level,
            "period_key": period.period_key,
            "period_label": period.period_label,
            "period_start": period.period_start,
            "period_end": period.period_end,
            "data_start": period.data_start,
            "data_end": period.data_end,
            "source_type": period.source_type,
        }
    )
    try:
        bucket.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


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
    period_start, period_end = _items_date_range(items)
    external_context = fetch_external_context(period_start, period_end)
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
    st.title("总览")
    notice = st.session_state.pop("raw_sync_notice", "")
    if notice:
        st.info(notice)

    selected_batch_id, _ = _get_overview_period_selector("overview")
    if not selected_batch_id:
        st.info("还没有成功周期。请先到“生成报告”上传数据并生成。")
        return

    if st.button("刷新报告和数据", width="stretch"):
        _load_cached_overview_data.clear()
        results = _run_raw_sync()
        if any(item.status == "generated" for item in results):
            st.rerun()
        if any(item.status == "error" for item in results):
            st.error(st.session_state.get("raw_sync_notice", "raw 自动刷新遇到错误。"))
        else:
            st.success("已检查 raw 目录，当前没有需要生成的新内容。")

    overview_data = _load_overview_data(APP_DB, selected_batch_id)
    items = overview_data["items"]
    if items.empty:
        st.info("当前周期没有可展示的数据。")
        return

    summary = overview_data["summary"]
    platform_summary = overview_data["platform_summary"]
    channel_comparison = overview_data["channel_comparison"]
    recommendations = overview_data["recommendations"]

    st.subheader("本周期数据总览")
    _render_overview_summary_table(summary, platform_summary, channel_comparison)

    st.subheader("内容题材建议摘要")
    st.markdown(recommendations)

    st.subheader("分渠道图")
    _render_platform_chart(platform_summary, channel_comparison)


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
    _render_section_shell("􀈟", "生成报告", "支持上传整个文件夹或多文件包，自动归档并生成下载结果。")
    st.title("生成报告")
    st.caption("上传 Excel、CSV 或 zip，系统会保存到 raw 周期目录、标准化明细、补齐栏目题材、写入历史库并生成下载文件。")

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
        generate = st.button("生成并存档", type="primary", width="stretch")

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
                )
            except Exception as exc:
                st.error(f"生成失败：{exc}")

    _render_rollup_generator()

    if "total_summary" in st.session_state:
        _display_generation_results()
    else:
        st.info("可点击选择目录，或把多个 CSV / Excel / ZIP 拖入上传区；目录上传会保留子目录结构。")

def _run_with_generation_progress(
    uploaded,
    period_start: date,
    period_end: date,
    *,
    overwrite_existing_channels: bool = False,
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
            progress_callback("正在整理 raw 周期目录")
            if normalized_buckets:
                result = None
                for bucket in normalized_buckets:
                    period = _period_for_generate_bucket(bucket, len(normalized_buckets), period_start, period_end)
                    _update_bucket_manifest(bucket, period)
                    result = run_archived_workflow(
                        bucket.raw_dir,
                        period.period_start,
                        period.period_end,
                        output_root=APP_OUTPUTS,
                        archive_root=APP_ARCHIVE,
                        db_path=APP_DB,
                        category_rules_path=CATEGORY_RULES,
                        env_path=ENV_PATH,
                        period_level=period.period_level,
                        period_key=period.period_key,
                        period_label=period.period_label,
                        data_start=period.data_start,
                        data_end=period.data_end,
                        source_type=period.source_type,
                        progress_callback=progress_callback,
                    )
                assert result is not None
            else:
                period_dir = f"{period_start:%Y%m%d}-{period_end:%Y%m%d}"
                materialized = materialize_uploaded_files(
                    uploaded,
                    APP_RAW / period_dir,
                    strip_common_period_root=True,
                    replace_same_channel=overwrite_existing_channels,
                )
                result = run_archived_workflow(
                    materialized.raw_dir,
                    period_start.isoformat(),
                    period_end.isoformat(),
                    output_root=APP_OUTPUTS,
                    archive_root=APP_ARCHIVE,
                    db_path=APP_DB,
                    category_rules_path=CATEGORY_RULES,
                    env_path=ENV_PATH,
                    period_level=st.session_state.get("generate_period_level", PERIOD_LEVEL_WEEK),
                    source_type=SOURCE_TYPE_UPLOAD,
                    progress_callback=progress_callback,
                )
            _store_artifacts(result)
            progress_bar.progress(100, text="报告生成完成")
            status.update(label="报告生成完成", state="complete", expanded=False)
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
                    archive_root=APP_ARCHIVE,
                    category_rules_path=CATEGORY_RULES,
                    env_path=ENV_PATH,
                )
                _store_artifacts(result)
                st.success(f"已生成汇总复盘：{period.period_label}")
            except Exception as exc:
                st.error(f"汇总生成失败：{exc}")


def _page_content_types() -> None:
    _render_section_shell("", "内容类型统计", "查看指定周期的内容分布、消耗与转化效率。")
    st.title("内容类型统计")
    st.caption("统计指定周期中的所有内容类型，并展示素材数、唯一视频数和转化效率。")

    # 添加周期选择器
    selected_batch_id, batches = _get_common_period_selector("content_types")
    if not selected_batch_id:
        st.info("还没有可统计的成功周期。")
        return

    items = load_dashboard_items_for_batch(APP_DB, selected_batch_id)
    if items.empty:
        st.info("当前周期没有可统计的数据。")
        return

    filtered = _content_filter_panel(items, "type")
    summary = summarize_content_types(filtered)
    if summary.empty:
        st.info("当前筛选条件下没有内容类型数据。")
        return

    st.subheader("内容类型分布")
    top = summary.head(15)
    _render_vertical_bar_chart(
        top.sort_values("spend", ascending=False),
        "category_display",
        "spend",
        "内容类型",
        "消耗",
        "内容类型消耗 Top 15",
        "activations",
        "激活数",
    )

    st.subheader("全部内容类型")
    st.dataframe(localize_and_sort_columns(summary), width="stretch", hide_index=True)


def _page_trends() -> None:
    _render_section_shell("􀑪", "历史趋势", "用快捷日期窗口和渠道筛选追踪周期变化。")
    st.title("历史趋势")
    st.caption("读取保留周期，按周期展示内容类型变化。")
    items = load_all_dashboard_items(APP_DB)
    if items.empty:
        st.info("还没有可展示的周期数据。")
        return

    period_start, period_end = _dashboard_period_bounds(items)
    _init_trend_filters(period_start, period_end)
    st.session_state["trend_default_start"] = period_start
    st.session_state["trend_default_end"] = period_end
    with st.container(border=True):
        quick_range = st.segmented_control(
            "统计日期",
            TREND_QUICK_RANGES,
            key="trend_quick_range",
            width="stretch",
            on_change=_on_trend_quick_range_change,
        )
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1.15])
        selected_start = c1.date_input(
            "时间线开始",
            key="trend_period_start",
        )
        selected_end = c2.date_input(
            "时间线结束",
            key="trend_period_end",
        )
        metric = c3.selectbox(
            "趋势指标",
            ["spend", "activations", "first_pay_count", "activation_cost", "first_pay_rate", "unique_content_count"],
            format_func=lambda value: localize_columns(pd.DataFrame(columns=[value])).columns[0],
        )
        platforms = c4.multiselect("渠道", _dashboard_options(items, "channel"), key="trend_platforms")

        c5, c6 = st.columns(2)
        categories = c5.multiselect("最终内容类别", _dashboard_options(items, "content_category"), key="trend_categories")
        top_n = c6.slider("图中展示 Top 内容类型数", 3, 20, 8, key="trend_top_n")
    if quick_range == "全部":
        st.caption("当前按完整历史区间统计。")
    else:
        st.caption(f"当前快捷区间：{quick_range}。可继续手动微调开始和结束日期。")

    if selected_start > selected_end:
        st.error("时间线开始日期不能晚于结束日期。")
        return

    trend_items = filter_dashboard_items(
        items,
        DashboardFilters(
            channels=tuple(platforms),
            content_categories=tuple(categories),
        ),
    )
    trends = summarize_content_type_trends(
        trend_items,
        selected_start.isoformat(),
        selected_end.isoformat(),
    )
    if trends.empty:
        st.info("当前时间线和筛选条件下没有趋势数据。")
        return

    plotted = _top_trend_rows(trends, metric, top_n)
    fig = px.line(
        plotted,
        x="trend_period",
        y=metric,
        color="category_display",
        markers=True,
        labels={
            "trend_period": "趋势周期",
            metric: localize_columns(pd.DataFrame(columns=[metric])).columns[0],
            "category_display": "内容类型",
        },
    )
    st.plotly_chart(fig)

    st.subheader("趋势明细")
    st.dataframe(localize_and_sort_columns(trends), width="stretch", hide_index=True)


def _page_content_details() -> None:
    _render_section_shell("", "内容明细", "快速切换周期和保留数据，查看唯一视频与原始投放行。")
    st.title("内容明细")
    st.caption("原始投放行会保留不同渠道/素材记录；唯一视频视图用于快速判断同一视频的合计表现。")

    # 添加周期选择器
    selected_batch_id, batches = _get_common_period_selector("content_details")

    scope = st.radio("数据范围", ["指定周期", "全部历史"], horizontal=True)

    if scope == "指定周期":
        if not selected_batch_id:
            st.info("还没有可查看的内容明细。")
            return
        items = load_dashboard_items_for_batch(APP_DB, selected_batch_id)
    else:
        items = load_all_dashboard_items(APP_DB)

    if items.empty:
        st.info("当前数据范围没有可查看的内容明细。")
        return

    filtered = _content_filter_panel(items, "detail")

    unique_content = summarize_unique_content(filtered)
    detail = dashboard_detail_items(filtered)

    st.subheader("唯一视频汇总")
    st.dataframe(localize_and_sort_columns(unique_content), width="stretch", hide_index=True)

    st.subheader("原始投放明细")
    st.dataframe(localize_and_sort_columns(detail), width="stretch", hide_index=True)


def _page_data_quality() -> None:
    _render_section_shell("􀇿", "数据质量", "优先处理缺失字段、分类风险和异常指标。")
    st.title("数据质量")
    st.caption("展示指定周期的字段缺失、分类缺失和异常指标，先处理高风险项再进入正式复盘。")

    # 添加周期选择器
    selected_batch_id, batches = _get_common_period_selector("data_quality")
    if not selected_batch_id:
        st.info("还没有数据质量报告。请先到「生成报告」上传数据并生成存档。")
        return

    quality = load_data_quality_for_batch(APP_DB, selected_batch_id)
    if quality.empty:
        st.info("当前周期没有数据质量报告。")
        return

    status_counts = quality["status"].value_counts() if "status" in quality.columns else pd.Series(dtype=int)
    c1, c2 = st.columns(2)
    c1.metric("需处理项", int(status_counts.get("需处理", 0)))
    c2.metric("通过项", int(status_counts.get("通过", 0)))
    st.dataframe(localize_and_sort_columns(quality), width="stretch", hide_index=True)


def _page_data_review() -> None:
    _render_section_shell("􀬚", "数据审核", "审核重复文件、标题冲突、数值冲突和缺失关键字段，并实时同步 cleaned.xlsx。")
    st.title("数据审核")
    st.caption("保存审核后会写入 review_resolutions，原子回写 cleaned.xlsx，并立即重新生成当前周期的分析数据。")

    selected_batch_id, batches = _get_common_period_selector("data_review")
    if not selected_batch_id:
        st.info("还没有可审核的数据。请先到「生成报告」导入并生成存档。")
        return

    review_items = load_data_review_items(APP_DB, selected_batch_id)
    if review_items.empty:
        st.info("当前周期没有重复、冲突或缺失字段审核项。")
        return

    st.caption(f"当前周期：{selected_batch_id}；审核项 {len(review_items)} 条。")
    _render_conflict_priority_review(review_items)
    editable_columns = [
        "issue_id",
        "issue_type",
        "review_action",
        "channel",
        "title",
        "content_id",
        "material_id",
        "dedupe_key",
        "duplicate_group_id",
        "conflict_details",
        "ledger_match_source",
        "ledger_content_type",
        "ledger_source_file",
        "ledger_source_sheet",
        "ledger_source_row",
        "match_risk_level",
        "match_risk_reason",
        "source_file",
        "source_sheet",
        "field_name",
        "new_value",
        "merge_target_content_id",
    ]
    for column in editable_columns:
        if column not in review_items.columns:
            review_items[column] = ""
    edited = st.data_editor(
        review_items[editable_columns],
        width="stretch",
        hide_index=True,
        disabled=[
            "issue_id",
            "issue_type",
            "channel",
            "title",
            "content_id",
            "material_id",
            "dedupe_key",
            "duplicate_group_id",
            "conflict_details",
            "ledger_match_source",
            "ledger_content_type",
            "ledger_source_file",
            "ledger_source_sheet",
            "ledger_source_row",
            "match_risk_level",
            "match_risk_reason",
            "source_file",
            "source_sheet",
        ],
        column_config={
            "review_action": st.column_config.SelectboxColumn("审核动作", options=REVIEW_ACTIONS, required=True),
            "issue_id": st.column_config.TextColumn("审核ID", disabled=True),
            "issue_type": st.column_config.TextColumn("问题类型", disabled=True),
            "field_name": st.column_config.TextColumn("改字段名"),
            "new_value": st.column_config.TextColumn("新值"),
            "merge_target_content_id": st.column_config.TextColumn("合并到主ID"),
        },
        key=f"data_review_editor_{selected_batch_id}",
    )
    if st.button("保存审核并同步 Excel", type="primary", width="stretch"):
        try:
            saved = save_review_resolutions(APP_DB, selected_batch_id, edited)
            with st.spinner("正在同步 cleaned.xlsx 并重新生成分析数据..."):
                result = apply_review_resolutions_and_regenerate(
                    APP_DB,
                    selected_batch_id,
                    output_root=APP_OUTPUTS,
                    archive_root=APP_ARCHIVE,
                    category_rules_path=CATEGORY_RULES,
                    env_path=ENV_PATH,
                )
                _store_artifacts(result)
            st.success(f"已保存 {saved} 条审核结果，并更新当前周期：{result.batch_id}")
            st.rerun()
        except Exception as exc:
            st.error(f"同步失败：{exc}")


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
    st.caption("查看本地 reference_tables.xlsx 和 account_filters.yml 中的字段、账号映射和统计过滤口径。")
    account_filters = load_account_filter_config(Path("config/account_filters.yml"))
    with st.expander("账号过滤配置", expanded=True):
        st.dataframe(localize_columns(account_filters.to_frame()), width="stretch", hide_index=True)
    references = load_reference_tables(Path("config/reference_tables.xlsx"))
    for sheet_name, frame in references.tables.items():
        with st.expander(sheet_name, expanded=sheet_name == "账号映射表"):
            st.dataframe(localize_columns(frame), width="stretch", hide_index=True)


def _page_category_review() -> None:
    _render_section_shell("", "分类审核", "对低置信度或缺失分类的内容做人工确认并沉淀历史映射。")
    st.title("分类审核")
    st.caption("只审核缺失或低置信度分类。保存后会沉淀为历史映射，下次上传同内容ID、素材ID或标题时自动复用。")

    # 添加周期选择器
    selected_batch_id, batches = _get_common_period_selector("category_review")
    if not selected_batch_id:
        st.info("当前没有待审核或待复核分类。")
        return

    queue = load_review_queue_for_batch(APP_DB, selected_batch_id)
    if queue.empty:
        st.info("当前周期没有待审核或待复核分类。")
        return

    queue = queue.reset_index(drop=True).copy()
    review_columns = [
        "review_status",
        "needs_manual_review",
        "review_reasons",
        "channel",
        "title",
        "account_id",
        "account_raw",
        "account",
        "account_mapping_source",
        "content_id",
        "material_id",
        "dedupe_key",
        "merged_row_count",
        "conflict_details",
        "category_l2",
        "category_l3",
        "category_source",
        "category_confidence",
        "spend",
        "activations",
        "activation_cost",
        "source_file",
    ]
    for column in review_columns:
        if column not in queue.columns:
            queue[column] = ""

    if "category_review_index" not in st.session_state:
        st.session_state["category_review_index"] = 0
    st.session_state["category_review_index"] = min(
        st.session_state["category_review_index"],
        max(len(queue) - 1, 0),
    )
    index = st.session_state["category_review_index"]
    current = queue.iloc[index]

    nav1, nav2, nav3 = st.columns([1, 1.2, 1])
    if nav1.button("上一条", width="stretch", disabled=index == 0):
        st.session_state["category_review_index"] = max(index - 1, 0)
        st.rerun()
    nav2.markdown(f"**当前待审核：{index + 1} / {len(queue)}**")
    if nav3.button("下一条", width="stretch", disabled=index >= len(queue) - 1):
        st.session_state["category_review_index"] = min(index + 1, len(queue) - 1)
        st.rerun()

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("审核原因")
        st.write(str(current.get("review_reasons", "")) or "未提供原因")
        st.caption(
            f"状态：{current.get('review_status', '') or '-'} | "
            f"分类来源：{current.get('category_source', '') or '-'} | "
            f"置信度：{_fmt_metric_number(current.get('category_confidence', ''), 2)}"
        )
        if str(current.get("conflict_details", "")).strip():
            st.text_area("冲突详情", str(current.get("conflict_details", "")), height=120, disabled=True)

        detail_fields = [
            ("渠道", current.get("channel", "")),
            ("标题", current.get("title", "")),
            ("视频/笔记id", current.get("content_id", "")),
            ("素材ID", current.get("material_id", "")),
            ("账号", current.get("account", "")),
            ("原始账号", current.get("account_raw", "")),
            ("账号映射来源", current.get("account_mapping_source", "")),
            ("消耗", _fmt_metric_number(current.get("spend", 0), 0)),
            ("激活数", _fmt_metric_number(current.get("activations", 0), 0)),
            ("激活成本", _fmt_metric_number(current.get("activation_cost", 0), 1)),
            ("来源文件", current.get("source_file", "")),
        ]
        for label, value in detail_fields:
            st.markdown(f"**{label}**")
            st.write(value if str(value).strip() else "-")

    with right:
        st.subheader("单条审核")
        current_l2 = str(current.get("category_l2", "") or "")
        current_l3 = str(current.get("category_l3", "") or "")
        review_key_prefix = f"review_item_{index}"
        category_l2 = st.text_input("栏目", value=current_l2, key=f"{review_key_prefix}_category_l2")
        category_l3 = st.text_input("三级题材", value=current_l3, key=f"{review_key_prefix}_category_l3")
        st.caption("只需要确认这条内容最终应归到哪个栏目、三级题材。")

        if st.button("确认并保存当前审核", type="primary", width="stretch"):
            payload = pd.DataFrame(
                [
                    {
                        "platform": current.get("channel", ""),
                        "platform_group": current.get("channel", ""),
                        "channel": current.get("channel", ""),
                        "content_id": current.get("content_id", ""),
                        "material_id": current.get("material_id", ""),
                        "title": current.get("title", ""),
                        "category_l2": category_l2,
                        "category_l3": category_l3,
                    }
                ]
            )
            saved = upsert_category_mappings(APP_DB, payload)
            if saved == 0:
                st.error("请先填写栏目，再保存当前审核。")
            else:
                st.success("当前条目已保存到历史映射。重新上传或重新生成后会自动复用。")

    with st.expander("全部待审核条目预览"):
        preview_columns = [
            "review_status",
            "review_reasons",
            "channel",
            "title",
            "content_id",
            "material_id",
            "category_l2",
            "category_l3",
            "category_source",
            "category_confidence",
        ]
        st.dataframe(localize_columns(queue[preview_columns]), width="stretch", hide_index=True)


def _page_file_backup() -> None:
    _render_section_shell("􀈽", "文件备份", "把周期移到备份区，或从备份区恢复回分析列表。")
    st.title("文件备份")
    st.caption("移动后周期会从常规选择器隐藏；只有在这里恢复后，才会重新出现在各分析页。")

    notice = st.session_state.pop("file_backup_notice", "")
    if notice:
        st.success(notice)

    active_batches = list_successful_dashboard_batches(APP_DB)
    if active_batches.empty:
        st.info("当前没有可操作的成功周期。")
    else:
        st.subheader("周期删除与备份")
        batch_ids = [str(value) for value in active_batches["batch_id"]]
        label_by_id = {
            str(row["batch_id"]): (
                f"{row.get('period_start', '')} 至 {row.get('period_end', '')}"
                f"｜{format_beijing_datetime(row.get('created_at', ''))}"
            )
            for _, row in active_batches.iterrows()
        }
        selected_batch_id = st.selectbox(
            "选择周期",
            batch_ids,
            key="file_backup_batch_id",
            format_func=lambda batch_id: label_by_id.get(batch_id, batch_id),
            width="stretch",
        )
        action = st.radio(
            "操作类型",
            ["移动到文件备份", "直接删除"],
            horizontal=True,
            key="file_backup_action",
        )
        confirmation_text = st.text_input(
            "输入周期标识确认",
            key="file_backup_confirmation",
            placeholder=selected_batch_id,
        )
        can_execute = confirmation_text.strip() == selected_batch_id
        if action == "移动到文件备份":
            if st.button("移动到文件备份", type="primary", width="stretch", disabled=not can_execute):
                try:
                    move_batch_to_file_backup(APP_DB, selected_batch_id, APP_RAW, APP_FILE_BACKUP)
                    st.session_state["global_batch_id"] = ""
                    st.session_state["file_backup_notice"] = f"已移动到文件备份：{selected_batch_id}"
                    st.rerun()
                except Exception as exc:
                    st.error(f"移动失败：{exc}")
        else:
            st.warning("直接删除会清理当前周期记录、输出和归档文件，且不可从备份页恢复。")
            if st.button("直接删除", width="stretch", disabled=not can_execute):
                try:
                    delete_batch_permanently(APP_DB, selected_batch_id, APP_RAW, APP_FILE_BACKUP)
                    st.session_state["global_batch_id"] = ""
                    st.session_state["file_backup_notice"] = f"已永久删除：{selected_batch_id}"
                    st.rerun()
                except Exception as exc:
                    st.error(f"删除失败：{exc}")

    st.subheader("已备份周期")
    backups = list_file_backups(APP_DB)
    if backups.empty:
        st.info("暂无文件备份。")
        return

    st.dataframe(localize_and_sort_columns(backups), width="stretch", hide_index=True)
    backup_ids = [str(value) for value in backups["batch_id"]]
    backup_label_by_id = {
        str(row["batch_id"]): (
            f"{row.get('period_start', '')} 至 {row.get('period_end', '')}"
            f"｜{format_beijing_datetime(row.get('backed_up_at', ''))}"
        )
        for _, row in backups.iterrows()
    }
    selected_backup_id = st.selectbox(
        "选择备份",
        backup_ids,
        key="file_backup_restore_id",
        format_func=lambda batch_id: backup_label_by_id.get(batch_id, batch_id),
        width="stretch",
    )
    selected_backup = backups[backups["batch_id"].astype(str).eq(str(selected_backup_id))].iloc[0]
    st.caption(f"备份目录：{selected_backup.get('backup_dir', '')}")
    can_restore = Path(str(selected_backup.get("backup_dir", ""))).exists()
    if not can_restore:
        st.warning("备份目录不存在，无法恢复。")
    if st.button("恢复到周期", width="stretch", disabled=not can_restore):
        try:
            restore_file_backup(APP_DB, selected_backup_id, APP_RAW, APP_FILE_BACKUP)
            st.session_state["global_batch_id"] = selected_backup_id
            st.session_state["file_backup_notice"] = f"已恢复周期：{selected_backup_id}"
            st.rerun()
        except Exception as exc:
            st.error(f"恢复失败：{exc}")


def _render_channel_page(channel_name: str) -> None:
    topic_limit = channel_topic_limit(channel_name)
    topic_scope = f"消耗 Top {topic_limit} 重点题材" if topic_limit else "达人数据暂不做题材分析"
    _render_section_shell("", channel_name, f"按当前总览周期展示该渠道全部栏目数据和{topic_scope}。")
    st.title(channel_name)

    selected_batch_id, batches = _selected_or_latest_batch_id()
    if not selected_batch_id:
        st.info("还没有可分析的成功周期。请先到“生成报告”上传数据并生成。")
        return

    period_caption = _period_caption_for_batch(batches, selected_batch_id)
    if period_caption:
        st.caption(f"当前周期：{period_caption}。周期跟随“总览”页全局选择。")

    items = load_dashboard_items_for_batch(APP_DB, selected_batch_id)
    if items.empty:
        st.info("当前周期没有可分析的数据。")
        return

    channel_items = items[items["channel"].eq(channel_name)].copy()
    if channel_items.empty:
        st.info(f"当前周期没有 {channel_name} 数据。")
        return

    st.subheader("渠道核心指标")
    channel_summary = aggregate_dashboard(channel_items, ["channel"])
    if not channel_summary.empty:
        _render_channel_summary_metrics(channel_summary)

    category_summary = summarize_channel_categories(items, channel_name)
    st.subheader("栏目汇总")
    if category_summary.empty:
        st.info("当前渠道没有可展示的栏目数据。")
    else:
        _render_vertical_bar_chart(
            category_summary,
            "category_name",
            "spend",
            "栏目",
            "消耗",
            f"{channel_name} 栏目消耗",
            "activations",
            "激活数",
        )
        st.dataframe(
            localize_columns(_category_table_display(category_summary)),
            width="stretch",
            hide_index=True,
        )

    st.subheader("重点题材分析")
    if topic_limit <= 0:
        st.info("达人数据暂不做题材分析，后台明细仍会保留。")
    else:
        topic_labels = load_topic_labels_for_batch(APP_DB, selected_batch_id)
        channel_topic_labels = topic_labels[topic_labels["channel"].astype(str).eq(str(channel_name))].copy() if not topic_labels.empty else pd.DataFrame()
        topic_summary = summarize_persisted_topic_labels(topic_labels, channel_name)
        if topic_summary.empty:
            st.warning("当前周期还没有固化的重点题材。请重新生成该周期，系统会在生成报告时完成 AI 题材固化。")
        else:
            st.caption(f"展示范围：{channel_name} 消耗 Top {topic_limit} 内容；AI 结果已随周期固化，页面只读取入库题材。")
            topic_insights = build_channel_top_topic_insights(topic_summary)
            if "重点题材分析结论" not in topic_insights:
                topic_insights = f"#### 重点题材分析结论\n{topic_insights}"
            st.markdown(topic_insights)
            _render_vertical_bar_chart(
                topic_summary,
                "topic_name",
                "spend",
                "题材",
                "消耗",
                f"{channel_name} 重点题材消耗",
                "activations",
                "激活数",
            )
            _render_short_table_blocks(_topic_table_display(topic_summary), "重点题材明细")
            with st.expander("查看题材对应素材", expanded=False):
                st.dataframe(
                    localize_and_sort_columns(_topic_material_detail(channel_topic_labels)),
                    width="stretch",
                    hide_index=True,
                )

    st.subheader("异常数据检测")
    _detect_and_display_anomalies(channel_items, pd.DataFrame(), "activation_cost", 20)


def _render_channel_summary_metrics(channel_summary: pd.DataFrame) -> None:
    """渲染渠道汇总指标"""
    if channel_summary.empty:
        return
    row = channel_summary.iloc[0]
    _render_metric_grid(
        [
            ("消耗", _fmt_metric_number(row.get("spend", 0), 0)),
            ("激活数", _fmt_metric_number(row.get("activations", 0), 0)),
            ("激活成本", _fmt_metric_number(row.get("activation_cost", 0), 1)),
            ("付费数", _fmt_metric_number(row.get("first_pay_count", 0), 0)),
            ("付费成本", _fmt_metric_number(row.get("first_pay_cost", 0), 1)),
            ("付费率", _fmt_percent(row.get("first_pay_rate", 0))),
        ],
        columns=3,
    )


def _detect_and_display_anomalies(
    topic_items: pd.DataFrame,
    topic_summary: pd.DataFrame,
    sort_metric: str,
    top_n: int,
) -> None:
    """检测并显示异常数据：标题/栏目缺失但指标高，或成本异常高。"""
    anomalies = detect_high_metric_anomalies(topic_items, sort_metric, top_n=top_n)
    metric_label = localize_columns(pd.DataFrame(columns=[sort_metric])).columns[0]
    col1, col2, col3 = st.columns(3)

    with col1:
        _render_anomaly_block(
            anomalies.get("missing_title", pd.DataFrame()),
            f"标题缺失但{metric_label}较高",
            "未发现标题缺失但数据高的记录",
        )
    with col2:
        _render_anomaly_block(
            anomalies.get("missing_category_l2", pd.DataFrame()),
            f"栏目缺失但{metric_label}较高",
            "未发现栏目缺失但数据高的记录",
        )
    with col3:
        _render_anomaly_block(
            anomalies.get("high_cost", pd.DataFrame()),
            "成本异常高",
            "未发现成本异常高的记录",
        )


def _render_anomaly_block(rows: pd.DataFrame, warning_label: str, empty_label: str) -> None:
    if rows is None or rows.empty:
        st.success(empty_label)
        return
    st.warning(f"发现 {len(rows)} 条{warning_label}记录")
    with st.expander("查看详情"):
        display_cols = [
            "channel",
            "title",
            "content_id",
            "material_id",
            "category_l2",
            "category_l3",
            "spend",
            "activations",
            "activation_cost",
            "first_pay_count",
            "first_pay_cost",
        ]
        display_cols = [column for column in display_cols if column in rows.columns]
        st.dataframe(localize_and_sort_columns(rows[display_cols]), width="stretch", hide_index=True)


def _selected_or_latest_batch_id() -> tuple[str, pd.DataFrame]:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return "", batches

    batch_ids = [str(value) for value in batches["batch_id"]]
    latest_batch_id = batch_ids[0]
    selected_batch_id = str(st.session_state.get("global_batch_id", latest_batch_id))
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
        "topic_name",
        "content_types",
        "item_count",
        "material_count",
        "spend_share",
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
    display = topic_summary[[column for column in columns if column in topic_summary.columns]].copy()
    return display


def _topic_material_detail(topic_labels: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "rank_position",
        "topic_name",
        "content_type",
        "title",
        "content_id",
        "material_id",
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


def _render_short_table_blocks(frame: pd.DataFrame, label: str, rows_per_block: int = 8) -> None:
    if frame.empty:
        return
    total_rows = len(frame)
    for start in range(0, total_rows, rows_per_block):
        block = frame.iloc[start : start + rows_per_block].copy()
        if total_rows > rows_per_block:
            st.caption(f"{label} {start + 1}-{start + len(block)} / {total_rows}")
        display = localize_and_sort_columns(block).reset_index(drop=True)
        st.table(display.style.hide(axis="index"))


def _display_generation_results() -> None:
    total_summary: pd.DataFrame = st.session_state["total_summary"]
    st.success(f"报告生成并入库完成，周期标识：{st.session_state['batch_id']}")
    st.subheader("总体核心指标")
    summary = build_dashboard_summary(st.session_state["platform_summary"])
    _render_kpis(summary)

    st.subheader("分平台核心结果")
    _render_platform_kpis(st.session_state["platform_summary"])

    with st.expander("AI 数据结论", expanded=True):
        st.markdown(st.session_state["ai_summary"])
    with st.expander("账号覆盖校验"):
        st.dataframe(localize_columns(st.session_state["account_audit"]), width="stretch", hide_index=True)
    with st.expander("数据质量报告"):
        st.dataframe(localize_columns(st.session_state["data_quality"]), width="stretch", hide_index=True)
    with st.expander("分类审核队列"):
        st.dataframe(localize_columns(st.session_state["review_queue"]), width="stretch", hide_index=True)
    with st.expander("分渠道总数据"):
        st.dataframe(localize_columns(st.session_state["platform_summary"]), width="stretch", hide_index=True)

    st.subheader("下载")
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("下载 HTML 报告", st.session_state["report_html"], "report.html", "text/html", width="stretch")
    c2.download_button(
        "下载 Excel 明细",
        st.session_state["analysis_xlsx"],
        "analysis.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    c3.download_button("下载标准 CSV", st.session_state["canonical_csv"], "canonical.csv", "text/csv", width="stretch")
    c4.download_button(
        "下载总结果表",
        st.session_state["total_summary_xlsx"],
        "total_summary.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
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
        chart["__bar_scale"] = pd.concat(
            [chart[y_metric].abs(), chart[previous_column].abs()],
            axis=1,
        ).max(axis=1, skipna=True)
        chart["__bar_scale"] = chart["__bar_scale"].where(chart["__bar_scale"].gt(0), pd.NA)
        chart["__current_index"] = (chart[y_metric] / chart["__bar_scale"] * 100).fillna(0)
        chart["__previous_index"] = chart[previous_column] / chart["__bar_scale"] * 100
        chart["__growth_sort"] = chart[change_column]
        chart = chart.sort_values(
            ["__growth_sort", y_metric],
            ascending=[is_cost, metric_sort_ascending(y_metric)],
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
                y=chart["__current_index"],
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
                hovertemplate=f"渠道：%{{customdata[0]}}<br>本期实际{y_label}：%{{customdata[1]}}<br>渠道内相对指数：%{{y:.0f}}<extra></extra>",
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Bar(
                x=chart["__axis_label"],
                y=chart["__previous_index"],
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
                hovertemplate=f"渠道：%{{customdata[0]}}<br>上期实际{y_label}：%{{customdata[1]}}<br>渠道内相对指数：%{{y:.0f}}<extra></extra>",
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
                "title": f"分渠道{y_label}环比视角",
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
                        "yaxis.title.text": "渠道内相对指数（本渠道最大=100）",
                        "yaxis.tickformat": ".0f",
                        "yaxis.range": [0, 115],
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
        title="渠道内相对指数（本渠道最大=100）",
        tickformat=".0f",
        range=[0, 115],
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
        c1, c2, c3 = st.columns(3)
        platforms = c1.multiselect("渠道", _dashboard_options(items, "channel"), key=f"{key_prefix}_platforms")
        content_categories = c2.multiselect(
            "栏目",
            _dashboard_options(items, "category_l2"),
            key=f"{key_prefix}_content_categories",
        )
        category_l3 = c3.multiselect("三级题材", _dashboard_options(items, "category_l3"), key=f"{key_prefix}_category_l3")
        text_query = st.text_input("标题 / 内容ID / 素材ID / 账号ID / 账号 / 作者", key=f"{key_prefix}_query")
    return filter_dashboard_items(
        items,
        DashboardFilters(
            channels=tuple(platforms),
            content_categories=tuple(content_categories),
            category_l3=tuple(category_l3),
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


def _dashboard_period_bounds(items: pd.DataFrame) -> tuple[date, date]:
    starts = pd.to_datetime(items["batch_period_start"], errors="coerce").dropna()
    ends = pd.to_datetime(items["batch_period_end"], errors="coerce").dropna()
    today = date.today()
    start = starts.min().date() if not starts.empty else today
    end = ends.max().date() if not ends.empty else today
    return start, end


def _dashboard_options(items: pd.DataFrame, column: str) -> list[str]:
    if column not in items.columns:
        return []
    values = items[column].fillna("").astype(str).str.strip()
    return sorted(value for value in values.unique() if value)


def _top_trend_rows(trends: pd.DataFrame, metric: str, top_n: int) -> pd.DataFrame:
    metric_totals = (
        trends.groupby("category_display", as_index=False)[metric]
        .sum(numeric_only=True)
        .sort_values(metric, ascending=False)
        .head(top_n)
    )
    keep = set(metric_totals["category_display"])
    return trends[trends["category_display"].isin(keep)].copy()


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
        "category_l3",
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

    safe_name = "".join(char if char.isascii() and char.isalnum() else "_" for char in channel).strip("_")
    _page_channel.__name__ = f"_page_channel_{safe_name or abs(hash(channel))}"
    return _page_channel


def _build_navigation_pages() -> list:
    pages = [
        st.Page(_page_overview, title="总览", default=True),
        st.Page(_page_generate, title="生成报告"),
    ]
    for channel in _available_channels_for_current_period():
        pages.append(st.Page(_make_channel_page(channel), title=channel))
    pages.extend(
        [
            st.Page(_page_content_types, title="内容类型统计"),
            st.Page(_page_trends, title="历史趋势"),
            st.Page(_page_content_details, title="内容明细"),
            st.Page(_page_data_quality, title="数据质量"),
            st.Page(_page_data_review, title="数据审核"),
            st.Page(_page_reference_tables, title="维护台账"),
            st.Page(_page_category_review, title="分类审核"),
            st.Page(_page_file_backup, title="文件备份"),
        ]
    )
    return pages


_inject_theme()

page = st.navigation(_build_navigation_pages(), position="sidebar", expanded=True)
page.run()
