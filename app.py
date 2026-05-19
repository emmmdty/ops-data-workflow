from __future__ import annotations

import base64
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from ops_data_workflow.ai import group_topic_labels, resolve_deepseek_settings
from ops_data_workflow.dashboard import (
    DashboardFilters,
    aggregate_dashboard,
    build_period_comparison_between_batches,
    build_period_comparison_for_batch,
    build_content_recommendations,
    build_dashboard_summary,
    dashboard_detail_items,
    detect_high_metric_anomalies,
    filter_dashboard_items,
    format_beijing_datetime,
    load_all_dashboard_items,
    load_data_quality_for_batch,
    load_dashboard_items_for_batch,
    load_latest_data_quality,
    load_latest_dashboard_items,
    load_latest_review_queue,
    load_review_queue_for_batch,
    list_successful_dashboard_batches,
    metric_sort_ascending,
    summarize_dimension_for_metric,
    summarize_topics_for_selection,
    summarize_content_type_trends,
    summarize_content_types,
    summarize_unique_content,
)
from ops_data_workflow.reporting import localize_columns, localize_and_sort_columns
from ops_data_workflow.reference_tables import load_reference_tables
from ops_data_workflow.raw_sync import sync_raw_periods
from ops_data_workflow.storage import (
    delete_batch_permanently,
    list_file_backups,
    list_recent_batches,
    move_batch_to_file_backup,
    read_batch_record,
    restore_file_backup,
    upsert_category_mappings,
)
from ops_data_workflow.upload_input import infer_period_from_upload_names, materialize_uploaded_files
from ops_data_workflow.workflow import run_archived_workflow


APP_DB = Path("data/workflow.sqlite3")
APP_RAW = Path("data/raw")
APP_FILE_BACKUP = Path("data/file_backup")
APP_ARCHIVE = Path("archive")
APP_OUTPUTS = Path("outputs")
CATEGORY_RULES = Path("config/category_rules.yml")
ENV_PATH = Path(".env")
TREND_QUICK_RANGES = ["全部", "一周", "两周", "一个月"]
TREND_RANGE_DAYS = {"一周": 7, "两周": 14, "一个月": 30}
CHART_METRICS = {
    "消耗": ("spend", "activations", "消耗", "激活数"),
    "激活数": ("activations", "activation_cost", "激活数", "激活成本"),
    "付费数": ("first_pay_count", "first_pay_cost", "付费数", "付费成本"),
    "激活成本": ("activation_cost", "activations", "激活成本", "激活数"),
    "付费成本": ("first_pay_cost", "first_pay_count", "付费成本", "付费数"),
    "付费率": ("first_pay_rate", "activations", "付费率", "激活数"),
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
            border: 0;
            min-height: 46px;
            background: linear-gradient(180deg, #3da2ff 0%, #0a84ff 100%);
            color: #ffffff;
            font-weight: 650;
            box-shadow: 0 14px 30px rgba(10, 132, 255, 0.28);
        }
        .stButton > button:hover,
        [data-testid="stDownloadButton"] > button:hover {
            background: linear-gradient(180deg, #2496ff 0%, #0071e3 100%);
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
    st.session_state["channel_comparison"] = result.channel_comparison
    st.session_state["comparison_note"] = result.comparison_note
    st.session_state["ai_summary"] = result.ai_summary


def _ensure_generate_period_defaults() -> None:
    today = date.today()
    st.session_state.setdefault("generate_period_start", today)
    st.session_state.setdefault("generate_period_end", st.session_state["generate_period_start"] + timedelta(days=6))
    st.session_state.setdefault("generate_period_end_touched", False)
    st.session_state.setdefault("generate_period_source", "")


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


@st.fragment(run_every="30s")
def _sync_raw_data_fragment() -> None:
    results = _run_raw_sync()
    if any(item.status == "generated" for item in results):
        st.rerun()


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

    batch_ids = [str(value) for value in batches["batch_id"]]
    label_by_id = dict(zip(batch_ids, batches["period_label"].astype(str)))
    created_by_id = {
        str(row["batch_id"]): format_beijing_datetime(row.get("created_at", ""))
        for _, row in batches.iterrows()
    }

    latest_batch_id = str(batches.iloc[0]["batch_id"])
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

    return selected_batch_id, batches


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
        fallback = normalized[normalized["batch_id"].ne(current_batch_id)]
        return "" if fallback.empty else str(fallback.iloc[0]["batch_id"])
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


def _page_overview() -> None:
    _render_section_shell("􀍟", "总览", "最新成功批次的核心指标、渠道表现和内容建议。")
    st.title("总览")
    st.caption("选择一个周期后会联动到其他分析页；raw 目录有新增或变更时会自动生成并刷新。")
    notice = st.session_state.pop("raw_sync_notice", "")
    if notice:
        st.info(notice)

    selected_batch_id, batches = _get_common_period_selector("overview")
    if not selected_batch_id:
        st.info("还没有成功批次。请先到“生成报告”上传数据并生成存档。")
        return

    if st.button("刷新报告和数据", width="stretch"):
        results = _run_raw_sync()
        if any(item.status == "generated" for item in results):
            st.rerun()
        if any(item.status == "error" for item in results):
            st.error(st.session_state.get("raw_sync_notice", "raw 自动刷新遇到错误。"))
        else:
            st.success("已检查 raw 目录，当前没有需要生成的新内容。")

    items = load_dashboard_items_for_batch(APP_DB, selected_batch_id)
    if items.empty:
        st.info("当前周期没有可展示的数据。")
        return

    comparison_batch_id = _get_comparison_period_selector("overview", selected_batch_id, batches)
    summary = build_dashboard_summary(items)
    platform_summary = aggregate_dashboard(items, ["channel"])
    channel_comparison = build_period_comparison_between_batches(APP_DB, selected_batch_id, comparison_batch_id)
    content_type_summary = summarize_content_types(items)
    recommendations = build_content_recommendations(summary, platform_summary, content_type_summary)

    st.subheader("总体核心指标")
    _render_kpis(summary)

    st.subheader("环比增长")
    _render_growth_overview(channel_comparison)

    st.subheader("分平台核心结果")
    _render_platform_kpis(platform_summary, channel_comparison)

    st.subheader("直接建议")
    st.markdown(recommendations)

    st.subheader("分渠道表现")
    selected_metric_name = st.selectbox(
        "选择图表指标",
        list(CHART_METRICS.keys()),
        key="overview_chart_metric",
        width="stretch",
    )
    y_metric, color_metric, y_label, color_label = CHART_METRICS[selected_metric_name]
    _render_platform_chart(platform_summary, y_metric, color_metric, y_label, color_label)

    st.subheader("渠道栏目分析")
    available_channels = sorted(platform_summary["channel"].dropna().unique()) if not platform_summary.empty else []
    selected_channel = st.selectbox(
        "选择渠道查看二级栏目数据",
        [""] + available_channels,
        key="overview_drill_channel",
        format_func=lambda x: "请选择渠道" if x == "" else x,
        width="stretch",
    )

    if selected_channel:
        channel_items = items[items["channel"] == selected_channel]
        category_summary = summarize_dimension_for_metric(channel_items, "category_l2", y_metric, top_n=15)
        if category_summary.empty:
            st.info("当前渠道没有可展示的栏目数据。")
        else:
            _render_vertical_bar_chart(
                category_summary.sort_values(y_metric, ascending=metric_sort_ascending(y_metric)),
                "category_name",
                y_metric,
                "二级栏目",
                y_label,
                f"{selected_channel} 二级栏目{selected_metric_name} Top 15",
                color_metric,
                color_label,
            )


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
            _apply_inferred_generate_period(uploaded)
        st.caption(resolve_deepseek_settings(ENV_PATH).public_status)
        c1, c2 = st.columns(2)
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
            st.caption("已根据上传文件夹名自动回填周期，仍可手动调整。")
        generate = st.button("生成并存档", type="primary", width="stretch")

    if generate:
        if not uploaded:
            st.error("请先上传文件夹，或上传 CSV、Excel、zip 数据包。")
        elif period_start > period_end:
            st.error("周期开始日期不能晚于结束日期。")
        else:
            try:
                with st.spinner("正在读取上传文件、校验、入库并生成报告..."):
                    period_dir = f"{period_start:%Y%m%d}-{period_end:%Y%m%d}"
                    materialized = materialize_uploaded_files(
                        uploaded,
                        APP_RAW / period_dir,
                        strip_common_period_root=True,
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
                    )
                    _store_artifacts(result)
            except Exception as exc:
                st.error(f"生成失败：{exc}")

    if "total_summary" in st.session_state:
        _display_generation_results()
    else:
        st.info("可点击选择目录，或把多个 CSV / Excel / ZIP 拖入上传区；目录上传会保留子目录结构。")

    _render_historical_reports()


def _render_historical_reports() -> None:
    st.subheader("历史报告")
    notice = st.session_state.pop("historical_report_notice", "")
    if notice:
        st.success(notice)

    history = list_recent_batches(APP_DB, limit=80)
    if history.empty:
        st.info("还没有历史报告。生成第一份报告后，这里会展示可查看和更新的报告。")
        return

    history = history[history["status"].astype(str).eq("ok")].copy()
    if history.empty:
        st.info("还没有成功生成的历史报告。")
        return

    batch_ids = [str(value) for value in history["batch_id"]]
    label_by_id = {
        str(row["batch_id"]): (
            f"{row.get('period_start', '')} 至 {row.get('period_end', '')}"
            f"｜{format_beijing_datetime(row.get('created_at', ''))}"
        )
        for _, row in history.iterrows()
    }
    selected_batch_id = st.selectbox(
        "选择历史报告",
        batch_ids,
        key="generate_history_batch_id",
        format_func=lambda batch_id: label_by_id.get(batch_id, batch_id),
        width="stretch",
    )
    record = read_batch_record(APP_DB, selected_batch_id)
    if not record:
        st.warning("未找到所选历史报告记录。")
        return

    output_dir = Path(record.get("output_dir", ""))
    archive_raw_dir = Path(record.get("archive_dir", "")) / "raw"
    st.caption(
        f"批次：{record.get('batch_id', '')}｜周期：{record.get('period_start', '')} 至 {record.get('period_end', '')}"
    )
    if record.get("comparison_note"):
        st.caption(record["comparison_note"])

    view_tab, download_tab, update_tab = st.tabs(["查看历史报告", "下载历史报告", "更新报告"])
    with view_tab:
        report_path = output_dir / "report.html"
        if report_path.exists():
            components.html(report_path.read_text(encoding="utf-8"), height=720, scrolling=True)
        else:
            st.warning("历史 HTML 报告文件不存在，可能被手动移动或删除。")

    with download_tab:
        _render_historical_report_downloads(output_dir, selected_batch_id)

    with update_tab:
        st.caption("更新会基于该批次归档的原始数据重新生成一个新批次；旧报告会继续保留。")
        can_update = archive_raw_dir.exists()
        if not can_update:
            st.warning("归档原始数据不存在，无法更新该报告。")
        if st.button("更新所选报告", type="primary", width="stretch", disabled=not can_update):
            try:
                with st.spinner("正在基于历史原始数据重新生成报告..."):
                    result = run_archived_workflow(
                        archive_raw_dir,
                        record["period_start"],
                        record["period_end"],
                        output_root=APP_OUTPUTS,
                        archive_root=APP_ARCHIVE,
                        db_path=APP_DB,
                        category_rules_path=CATEGORY_RULES,
                        env_path=ENV_PATH,
                    )
                    _store_artifacts(result)
                st.session_state["historical_report_notice"] = f"已更新并生成新批次：{result.batch_id}"
                st.rerun()
            except Exception as exc:
                st.error(f"更新失败：{exc}")


def _render_historical_report_downloads(output_dir: Path, batch_id: str) -> None:
    files = [
        ("下载 HTML 报告", "report.html", "text/html"),
        ("下载 Excel 明细", "analysis.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("下载标准 CSV", "canonical.csv", "text/csv"),
        ("下载总结果表", "total_summary.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ]
    cols = st.columns(4)
    for col, (label, filename, mime_type) in zip(cols, files):
        path = output_dir / filename
        if path.exists():
            col.download_button(
                label,
                path.read_bytes(),
                f"{batch_id}_{filename}",
                mime_type,
                width="stretch",
            )
        else:
            col.button(label, disabled=True, width="stretch")


def _page_content_types() -> None:
    _render_section_shell("", "内容类型统计", "查看指定周期批次的内容分布、消耗与转化效率。")
    st.title("内容类型统计")
    st.caption("统计指定周期批次中的所有内容类型，并展示素材数、唯一视频数和转化效率。")

    # 添加周期选择器
    selected_batch_id, batches = _get_common_period_selector("content_types")
    if not selected_batch_id:
        st.info("还没有可统计的成功批次。")
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
    _render_section_shell("􀑪", "历史趋势", "用快捷日期窗口和渠道筛选追踪批次变化。")
    st.title("历史趋势")
    st.caption("读取所有成功批次，按批次周期展示内容类型历史变化。")
    items = load_all_dashboard_items(APP_DB)
    if items.empty:
        st.info("还没有历史批次。")
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
    _render_section_shell("", "内容明细", "快速切换批次和全历史，查看唯一视频与原始投放行。")
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
    st.caption("展示指定周期批次的字段缺失、分类缺失和异常指标，先处理高风险项再进入正式复盘。")

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


def _page_reference_tables() -> None:
    _render_section_shell("􀉉", "维护台账", "查看本地映射表和处理规则，确认账号与字段口径。")
    st.title("维护台账")
    st.caption("查看本地 reference_tables.xlsx 中的字段映射、账号映射和处理规则；B站 MID 到实际账号名在这里维护。")
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
        category_l2 = st.text_input("二级栏目", value=current_l2, key=f"{review_key_prefix}_category_l2")
        category_l3 = st.text_input("三级题材", value=current_l3, key=f"{review_key_prefix}_category_l3")
        st.caption("只需要确认这条内容最终应归到哪个二级栏目、三级题材。")

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
                st.error("请先填写二级栏目，再保存当前审核。")
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


def _page_history() -> None:
    _render_section_shell("􀐫", "历史批次", "查看每次归档的周期、时间与对比说明。")
    st.title("历史批次")
    history = list_recent_batches(APP_DB)
    if history.empty:
        st.info("还没有历史批次。生成第一份报告后，这里会展示存档记录。")
    else:
        st.caption("历史批次按周期结束时间倒序展示；上一周期比较只会取时间上最近的更早成功批次。")
        st.dataframe(localize_and_sort_columns(history), width="stretch", hide_index=True)


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
            "输入批次号确认",
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
            st.warning("直接删除会清理当前批次记录、输出和归档文件，且不可从备份页恢复。")
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


def _page_drill_down_analysis() -> None:
    """内容分析页面，支持按渠道、栏目和题材逐级查看表现。"""
    _render_section_shell("", "内容分析", "按渠道、栏目和题材逐级查看内容表现。")
    st.title("内容分析")
    st.caption("先定位渠道，再定位二级栏目，最后查看题材 Top N。B站统一归入 B站全部，直接进入题材分析。")

    selected_batch_id, batches = _get_common_period_selector("drill_down")
    if not selected_batch_id:
        st.info("还没有可分析的成功批次。")
        return

    items = load_dashboard_items_for_batch(APP_DB, selected_batch_id)
    if items.empty:
        st.info("当前周期没有可分析的数据。")
        return

    available_channels = sorted(items["channel"].dropna().unique())
    if not available_channels:
        st.info("当前数据没有渠道信息。")
        return

    col1, col2, col3 = st.columns([2, 2, 1])
    selected_channel = col1.selectbox(
        "选择渠道",
        available_channels,
        key="drill_channel",
        width="stretch",
    )
    sort_metric_name = col2.selectbox(
        "排序指标",
        list(CHART_METRICS.keys()),
        key="drill_sort_metric",
        width="stretch",
    )
    top_n = col3.number_input(
        "显示 Top N",
        min_value=5,
        max_value=50,
        value=15,
        key="drill_top_n",
        width="stretch",
    )

    sort_metric, color_metric, sort_label, color_label = CHART_METRICS[sort_metric_name]
    channel_items = items[items["channel"] == selected_channel]

    if channel_items.empty:
        st.info(f"渠道 {selected_channel} 没有数据。")
        return

    st.subheader(f"{selected_channel} 渠道总览")
    channel_summary = aggregate_dashboard(channel_items, ["channel"])
    if not channel_summary.empty:
        _render_channel_summary_metrics(channel_summary)

    is_bilibili = selected_channel == "B站"
    category_summary = summarize_dimension_for_metric(channel_items, "category_l2", sort_metric, top_n=int(top_n))

    if is_bilibili:
        st.info("B站不区分栏目层级，本页统一按 B站全部 分析题材。")
        selected_category_l2 = BILIBILI_CATEGORY
    else:
        if category_summary.empty:
            st.warning("当前渠道没有二级栏目数据，请先补充分类。")
            return
        else:
            selected_category_l2 = st.selectbox(
                "选择二级栏目",
                list(category_summary["category_name"]),
                key="drill_category_l2",
                width="stretch",
            )

    if not category_summary.empty:
        st.subheader(f"{selected_channel} 二级栏目分布")
        _render_vertical_bar_chart(
            category_summary.sort_values(sort_metric, ascending=metric_sort_ascending(sort_metric)),
            "category_name",
            sort_metric,
            "二级栏目",
            sort_label,
            f"{selected_channel} 二级栏目{sort_metric_name} Top {top_n}",
            color_metric,
            color_label,
        )

    st.subheader("题材分析")
    if is_bilibili:
        topic_items = channel_items
    elif selected_category_l2:
        topic_items = channel_items[channel_items["category_l2"] == selected_category_l2]
    else:
        topic_items = pd.DataFrame()

    if topic_items.empty:
        st.info("请先选择栏目查看题材数据。")
        return

    topic_cache_key = f"topic_labels_{selected_batch_id}_{selected_channel}_{selected_category_l2}"
    if topic_cache_key not in st.session_state:
        st.session_state[topic_cache_key] = group_topic_labels(topic_items, env_path=ENV_PATH)
    topic_labels = st.session_state.get(topic_cache_key, {})
    if topic_labels:
        st.caption("题材分组：DeepSeek 归纳。")
    else:
        st.caption("题材分组：本地规则兜底。")

    topic_summary = summarize_topics_for_selection(
        topic_items,
        selected_channel,
        selected_category_l2,
        sort_metric,
        top_n=int(top_n),
        topic_labels=topic_labels,
    )
    if topic_summary.empty:
        st.warning("当前数据没有可归纳的题材信息。")
        return

    _render_vertical_bar_chart(
        topic_summary.sort_values(sort_metric, ascending=metric_sort_ascending(sort_metric)),
        "topic_name",
        sort_metric,
        "题材",
        sort_label,
        f"题材{sort_metric_name} Top {top_n}",
        color_metric,
        color_label,
    )
    st.subheader("题材明细")
    st.dataframe(localize_and_sort_columns(topic_summary), width="stretch", hide_index=True)

    st.subheader("异常数据检测")
    _detect_and_display_anomalies(topic_items, topic_summary, sort_metric, int(top_n))


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
            f"二级栏目缺失但{metric_label}较高",
            "未发现二级栏目缺失但数据高的记录",
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


def _display_generation_results() -> None:
    total_summary: pd.DataFrame = st.session_state["total_summary"]
    st.success(f"报告生成并入库完成，批次号：{st.session_state['batch_id']}")
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
        ("总消耗", f"{summary.total_spend:,.0f}"),
        ("激活数", f"{summary.activations:,.0f}"),
        ("激活成本", f"{summary.activation_cost:,.1f}"),
        ("付费数", f"{summary.first_pay_count:,.0f}"),
        ("付费成本", f"{summary.first_pay_cost:,.1f}"),
        ("付费率", f"{summary.first_pay_rate:.1%}"),
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
    return f"{float(numeric):,.{digits}f}"


def _fmt_percent(value: object) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "-"
    return f"{float(numeric):.1%}"


def _fmt_growth_delta(value: object) -> str | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    return f"{float(numeric):+.1%}"


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
        text=y_column,
        labels=labels,
        title=title,
        custom_data=[x_column],
        **color_kwargs,
    )
    fig.update_traces(
        texttemplate=_bar_text_template(y_column),
        textposition="outside",
        cliponaxis=False,
        marker_line_width=0.8,
        marker_line_color="rgba(255,255,255,0.72)",
        hovertemplate=f"{x_label}：%{{customdata[0]}}<br>{y_label}：%{{y}}<extra></extra>",
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
    y_metric: str = "activations",
    color_metric: str = "activation_cost",
    y_label: str = "激活数",
    color_label: str = "激活成本",
) -> None:
    if platform_summary.empty:
        return
    chart = platform_summary.sort_values(
        y_metric,
        ascending=metric_sort_ascending(y_metric) if y_metric in platform_summary.columns else False,
    )
    _render_vertical_bar_chart(
        chart,
        "channel",
        y_metric,
        "渠道",
        y_label,
        f"分渠道{y_label}",
        color_metric,
        color_label,
    )


def _content_filter_panel(items: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        platforms = c1.multiselect("渠道", _dashboard_options(items, "channel"), key=f"{key_prefix}_platforms")
        content_categories = c2.multiselect(
            "二级栏目",
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


_inject_theme()

page = st.navigation(
    [
        st.Page(_page_overview, title="总览", default=True),
        st.Page(_page_generate, title="生成报告"),
        st.Page(_page_drill_down_analysis, title="内容分析"),
        st.Page(_page_content_types, title="内容类型统计"),
        st.Page(_page_trends, title="历史趋势"),
        st.Page(_page_content_details, title="内容明细"),
        st.Page(_page_data_quality, title="数据质量"),
        st.Page(_page_reference_tables, title="维护台账"),
        st.Page(_page_category_review, title="分类审核"),
        st.Page(_page_file_backup, title="文件备份"),
        st.Page(_page_history, title="历史批次"),
    ],
    position="sidebar",
    expanded=True,
)
page.run()
_sync_raw_data_fragment()
