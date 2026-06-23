from __future__ import annotations

import base64
import calendar
from datetime import date, timedelta
import html
import json
import os
from pathlib import Path
import re
import textwrap
import time

import pandas as pd
import streamlit as st

from ops_data_workflow.analysis_jobs import (
    ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
    ANALYSIS_PURPOSE_STRATEGY_RECAP,
    list_analysis_jobs,
    reset_top_multimodal_jobs,
    run_top_multimodal_analysis_from_manifests,
)
from ops_data_workflow.dashboard import (
    format_beijing_datetime,
    list_successful_dashboard_batches,
    load_all_dashboard_items,
    load_dashboard_items_for_batch,
    summarize_period_metric_trends,
)
from ops_data_workflow.env_bridge import copy_missing_runtime_env, resolve_harvester_env_path
from ops_data_workflow.feishu_ledger import load_feishu_content_ledger
from ops_data_workflow.harvester_bridge import (
    cache_existing_harvester_assets_for_batch,
    resolve_harvester_root,
    run_harvester_asset_capture,
)
from ops_data_workflow.minimax_recap import analyze_top_content_with_minimax
from ops_data_workflow.multimodal_recap import (
    build_type_recap_items,
    persist_multimodal_recap,
    persist_type_recap_from_top_content,
)
from ops_data_workflow.periods import (
    PERIOD_LEVEL_LABELS,
    PERIOD_LEVEL_MONTH,
    PERIOD_LEVEL_QUARTER,
    PERIOD_LEVEL_WEEK,
    PERIOD_LEVEL_YEAR,
    PERIOD_LEVELS,
    review_period_from_dates,
)
from ops_data_workflow.platform_taxonomy import (
    BILIBILI_CONTENT_TYPES,
    DOUYIN_TAXONOMY,
    XHS_TAXONOMY,
    normalize_platform_classification,
)
from ops_data_workflow.recap_settings import get_recap_settings, update_recap_settings
from ops_data_workflow.range_recap_report import generate_range_recap_report
from ops_data_workflow.reporting import DISPLAY_NUMERIC_COLUMNS, format_display_number, localize_columns
from ops_data_workflow.rollups import rollup_period_for, select_rollup_component_batches
from ops_data_workflow.source_storage import source_dir_for_period, source_storage_key
from ops_data_workflow.storage import (
    build_feishu_content_asset_diff,
    get_top_asset_cache_summary,
    list_content_performance_items,
    list_harvester_asset_jobs,
    list_harvester_asset_manifests,
    list_local_content_assets,
    clear_manual_recap_report,
    list_manual_high_value_supplements,
    load_manual_recap_report,
    load_manual_recap_status,
    load_range_recap_report,
    load_range_recap_status,
    list_multimodal_recap_items,
    list_period_channel_totals,
    list_strategy_recap_items,
    list_top_asset_cache_entries,
    list_type_recap_items,
    persist_feishu_ledger_snapshot,
    persist_range_recap_report,
    persist_oral_recap_report,
    previous_successful_batch_id_for_period,
    read_batch_record,
    upsert_manual_high_value_supplement,
    upsert_content_assets_from_feishu,
)
from ops_data_workflow.top_asset_service import (
    RECAP_TIER_1_SPEND_TOP,
    RECAP_TIER_2_EXPOSURE_TOP,
    RECAP_TIER_3_THRESHOLD,
    RECAP_TIER_LABELS,
    build_executable_top_content_pool,
    build_high_spend_content_pool,
    build_recap_tier_pool,
    filter_executable_top_content_pool,
)
from ops_data_workflow.top_asset_cache import cleanup_top_asset_cache
from ops_data_workflow.upload_input import (
    detect_upload_channel_conflicts,
    infer_period_from_upload_names,
    materialize_uploaded_files,
)
from ops_data_workflow.workflow import run_archived_workflow, run_rollup_workflow


def _app_path_from_env(name: str, default: str) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else Path(default)


APP_DATA_ROOT = _app_path_from_env("OPS_DATA_ROOT", "data")
APP_PROCESSED = _app_path_from_env("OPS_PROCESSED_ROOT", "processed")
APP_DB = _app_path_from_env("OPS_WORKFLOW_DB", ".runtime/workflow.sqlite3")
APP_OUTPUTS = _app_path_from_env("OPS_OUTPUTS_ROOT", "outputs")
CATEGORY_RULES = Path("config/category_rules.yml")
ENV_PATH = Path(".env")
HARVESTER_ENV_PATH = resolve_harvester_env_path(project_root=Path.cwd())

NUMERIC_COLUMNS = ["spend", "impressions", "clicks", "activations", "first_pay_count"]
INTEGER_DISPLAY_COLUMNS = {
    "impressions",
    "clicks",
    "activations",
    "first_pay_count",
    "item_count",
    "unique_content_count",
    "material_count",
    "channel_count",
    "merged_row_count",
    "rank_position",
    "pending_item_count",
    "secondary_category_count",
    "observed_count",
    "count",
    "total",
}
PERCENT_DISPLAY_COLUMNS = {
    column
    for column in DISPLAY_NUMERIC_COLUMNS
    if column.endswith("_share") or column.endswith("_rate")
} | {"ctr", "share"}
TABLE_NUMERIC_COLUMNS = DISPLAY_NUMERIC_COLUMNS | {"share"}
LOCALIZED_TABLE_NUMERIC_COLUMNS = {
    localize_columns(pd.DataFrame(columns=[column])).columns[0]: column
    for column in TABLE_NUMERIC_COLUMNS
}
REPORT_CHANNEL_SECTIONS = [
    ("二、抖音商业化内容分析", "抖音商业化"),
    ("三、抖音市场部内容分析", "抖音市场部"),
    ("四、小红书商业化内容分析", "小红书商业化"),
    ("五、小红书市场部内容分析", "小红书市场部"),
    ("六、B站数据", "B站"),
]
RECAP_TIER_DEFINITIONS = {
    RECAP_TIER_1_SPEND_TOP: "抖音按消耗取分渠道 Top20，其他平台按消耗取分渠道 Top10。",
    RECAP_TIER_2_EXPOSURE_TOP: "抖音按曝光取分渠道 Top20，其他平台按曝光取分渠道 Top10。",
    RECAP_TIER_3_THRESHOLD: "纳入单条消耗大于 2000 元或单条曝光大于 100000 的素材。",
}


st.set_page_config(page_title="原生内容投放分析工作台", layout="wide", initial_sidebar_state="collapsed")


def _inject_theme() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background: #f5f7fb;
            color: #152238;
        }
        .block-container {
            max-width: 1420px;
            padding-top: 1.4rem;
            padding-bottom: 3rem;
        }
        [data-testid="stSidebar"] {
            background: #eef3f8;
            border-right: 1px solid #d8e1ec;
        }
        [data-testid="stExpandSidebarButton"] {
            width: 36px;
            height: 36px;
            margin: 12px 0 0 12px;
            border: 1px solid #d0dae7;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 6px 16px rgba(25, 40, 68, 0.12);
        }
        [data-testid="stExpandSidebarButton"]:hover,
        [data-testid="stExpandSidebarButton"]:focus {
            border-color: #7aa7df;
            background: #f5f9ff;
        }
        h1, h2, h3, label, [data-testid="stMarkdownContainer"] {
            letter-spacing: 0;
        }
        div[data-testid="stMetric"] {
            min-height: 104px;
            padding: 14px 16px;
            border: 1px solid #dce4ef;
            border-radius: 8px;
            background: #ffffff;
        }
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] > div,
        div[data-testid="stMetricValue"] p {
            font-variant-numeric: tabular-nums;
            line-height: 1.15;
            max-width: 100%;
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        div[data-testid="stDataFrame"], div[data-testid="stFileUploader"], [data-testid="stExpander"] details {
            border-radius: 8px;
        }
        .stButton > button, [data-testid="stDownloadButton"] > button {
            border-radius: 8px;
            min-height: 42px;
            font-weight: 600;
        }
        .weight-button-spacer {
            height: 1.7rem;
        }
        [data-testid="stDecoration"],
        [data-testid="stHeaderActionElements"],
        [data-testid="stAppDeployButton"],
        [data-testid="stMainMenu"],
        #MainMenu,
        [data-testid="stDataFrame"] button[title],
        [data-testid="stDataFrame"] [aria-label="Show/hide columns"],
        [data-testid="stDataFrame"] [aria-label="Download as CSV"],
        [data-testid="stDataFrame"] [aria-label="Search"],
        [data-testid="stDataFrame"] [aria-label="Fullscreen"] {
            display: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <style>
        [data-testid="stFileUploaderDropzoneInstructions"] > div {
            visibility: hidden;
            position: relative;
            min-width: 220px;
            min-height: 44px;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] > div::before {
            content: "拖拽文件夹到这里";
            visibility: visible;
            position: absolute;
            left: 0;
            top: 0;
            color: #152238;
            font-size: 0.95rem;
            font-weight: 600;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] > div::after {
            content: "支持单个或多个渠道数据文件";
            visibility: visible;
            position: absolute;
            left: 0;
            top: 24px;
            color: #617089;
            font-size: 0.82rem;
        }
        [data-testid="stFileUploaderDropzone"] [data-testid="stBaseButton-secondary"] {
            font-size: 0;
        }
        [data-testid="stFileUploaderDropzone"] [data-testid="stBaseButton-secondary"]::after {
            content: "选择文件夹";
            font-size: 0.9rem;
        }
        .top-link-card {
            display: grid;
            grid-template-columns: 162px minmax(0, 1fr);
            min-height: 148px;
            margin: 0 0 0.7rem;
            overflow: hidden;
            border: 1px solid #dce4ef;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 8px 20px rgba(25, 40, 68, 0.05);
        }
        .top-link-cover {
            position: relative;
            display: block;
            min-height: 148px;
            overflow: hidden;
            background: #edf2f7;
            border-right: 1px solid #edf1f6;
            text-decoration: none;
            cursor: zoom-in;
        }
        .top-link-cover img {
            width: 100%;
            height: 100%;
            min-height: 148px;
            object-fit: cover;
            object-position: center 28%;
            display: block;
            transition: transform 140ms ease;
        }
        .top-link-cover:hover img {
            transform: scale(1.04);
        }
        .top-link-cover.empty {
            display: grid;
            place-items: center;
            color: #78869b;
            font-size: 0.82rem;
            cursor: default;
            background: repeating-linear-gradient(135deg, #edf2f7, #edf2f7 10px, #e7edf5 10px, #e7edf5 20px);
        }
        .top-link-cover-label {
            position: absolute;
            right: 8px;
            bottom: 8px;
            padding: 3px 6px;
            border-radius: 6px;
            background: rgba(17, 31, 52, 0.72);
            color: #ffffff;
            font-size: 0.72rem;
            font-weight: 700;
        }
        .top-link-body {
            min-width: 0;
            padding: 14px 16px;
            display: grid;
            gap: 9px;
            align-content: center;
        }
        .top-link-topline {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .top-link-rank {
            color: #77849a;
            font-size: 0.78rem;
            font-weight: 800;
            font-variant-numeric: tabular-nums;
        }
        .top-link-channel {
            color: #1f6fd1;
            font-size: 0.78rem;
            font-weight: 800;
        }
        .top-link-title {
            color: #162033;
            font-size: 1rem;
            font-weight: 750;
            line-height: 1.35;
            overflow-wrap: anywhere;
        }
        .top-link-detail {
            min-height: 1rem;
            color: #66748a;
            font-size: 0.78rem;
            overflow-wrap: anywhere;
        }
        .top-link-metrics {
            display: grid;
            grid-template-columns: 1.1fr 1fr 1fr 1fr;
            gap: 8px;
        }
        .top-link-metric {
            min-width: 0;
            padding: 8px 10px;
            border: 1px solid #e0e7f0;
            border-radius: 8px;
            background: #fbfcfe;
        }
        .top-link-metric span {
            display: block;
            margin-bottom: 3px;
            color: #77849a;
            font-size: 0.72rem;
        }
        .top-link-metric strong {
            display: block;
            color: #152238;
            font-size: 0.86rem;
            line-height: 1.15;
            font-variant-numeric: tabular-nums;
            overflow-wrap: anywhere;
        }
        .top-link-metric-main {
            border-color: #abd0ff;
            background: linear-gradient(180deg, #f1f7ff 0%, #ffffff 100%);
        }
        .top-link-metric-main strong {
            color: #0d4f9f;
            font-size: 1.08rem;
        }
        .local-recap-metric {
            min-height: 118px;
            padding: 14px 16px;
            border: 1px solid #dce4ef;
            border-radius: 8px;
            background: #ffffff;
        }
        .local-recap-label {
            color: #2d3748;
            font-size: 0.9rem;
            font-weight: 500;
            line-height: 1.25;
            margin-bottom: 6px;
        }
        .local-recap-value-line {
            align-items: baseline;
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            line-height: 1.1;
            margin-bottom: 8px;
        }
        .local-recap-value {
            color: #2b3141;
            font-size: 2rem;
            font-variant-numeric: tabular-nums;
            font-weight: 500;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .local-recap-share {
            color: #49637f;
            font-size: 0.82rem;
            font-weight: 600;
            white-space: nowrap;
        }
        .local-recap-note {
            color: #6b778c;
            font-size: 0.78rem;
            line-height: 1.35;
        }
        .channel-overview-table-wrap {
            width: 100%;
            overflow-x: auto;
            border: 1px solid #dce4ef;
            border-radius: 8px;
            background: #ffffff;
        }
        .channel-overview-table {
            width: 100%;
            border-collapse: collapse;
            background: #ffffff;
            color: #1f2937;
            font-size: 0.86rem;
        }
        .channel-overview-table th,
        .channel-overview-table td {
            border-right: 1px solid #e5ebf3;
            border-bottom: 1px solid #e5ebf3;
            padding: 8px 10px;
            text-align: right;
            vertical-align: middle;
            white-space: nowrap;
        }
        .channel-overview-table th:first-child,
        .channel-overview-table td:first-child {
            text-align: left;
        }
        .channel-overview-table th {
            background: #f7f9fc;
            color: #6b7280;
            font-weight: 400;
        }
        .channel-overview-table tr:last-child td {
            border-bottom: 0;
        }
        .channel-overview-table th:last-child,
        .channel-overview-table td:last-child {
            border-right: 0;
        }
        .channel-value {
            color: #1f2937;
            font-weight: 400;
        }
        .channel-delta {
            margin-left: 4px;
            font-weight: 600;
        }
        .channel-delta-good {
            color: #c0392b;
        }
        .channel-delta-bad {
            color: #1f8a4c;
        }
        .top-link-open {
            color: #1d67c5;
            text-decoration: none;
            font-size: 0.78rem;
            font-weight: 800;
        }
        .top-link-open.muted {
            color: #77849a;
        }
        .top-link-cover-modal {
            display: none;
            position: fixed;
            inset: 0;
            z-index: 999999;
            padding: 28px;
            background: rgba(10, 18, 31, 0.72);
            align-items: center;
            justify-content: center;
        }
        .top-link-cover-modal:target {
            display: flex;
        }
        .top-link-cover-dialog {
            width: min(920px, 94vw);
            max-height: 92vh;
            overflow: hidden;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 24px 80px rgba(0, 0, 0, 0.28);
        }
        .top-link-cover-dialog-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 12px 14px;
            border-bottom: 1px solid #edf1f6;
        }
        .top-link-cover-dialog-head strong {
            font-size: 0.9rem;
        }
        .top-link-cover-close {
            color: #1d67c5;
            text-decoration: none;
            font-size: 0.82rem;
            font-weight: 800;
        }
        .top-link-cover-large {
            max-height: calc(92vh - 52px);
            display: grid;
            place-items: center;
            background: #0f1726;
        }
        .top-link-cover-large img {
            max-width: 100%;
            max-height: calc(92vh - 52px);
            display: block;
            object-fit: contain;
        }
        @media (max-width: 760px) {
            .top-link-card {
                grid-template-columns: 118px minmax(0, 1fr);
            }
            .top-link-metrics {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _page_overview() -> None:
    st.title("总览")
    batch_id = _select_overview_batch("overview")
    if not batch_id:
        st.info("还没有成功周期。请先在上传清洗页上传一个新周期。")
        return

    record = read_batch_record(APP_DB, batch_id)
    totals = list_period_channel_totals(APP_DB, batch_id=batch_id)
    items = _overview_items_for_batch(batch_id)
    recap_items = list_type_recap_items(APP_DB, batch_id=batch_id)
    manifests = _manifests_with_job_context(
        list_harvester_asset_manifests(APP_DB, batch_id=batch_id),
        list_harvester_asset_jobs(APP_DB, batch_id=batch_id),
    )
    settings = get_recap_settings(APP_DB)
    previous_batch_id = _previous_batch_id_for_record(record)
    previous_totals = list_period_channel_totals(APP_DB, batch_id=previous_batch_id) if previous_batch_id else pd.DataFrame()
    previous_items = _overview_items_for_batch(previous_batch_id) if previous_batch_id else pd.DataFrame()

    st.caption(_batch_caption(record))
    channel_totals = _channel_totals_for_display(
        totals,
        items,
        activation_weight=settings.activation_weight,
        first_pay_weight=settings.first_pay_weight,
        previous_totals=previous_totals,
        previous_items=previous_items,
    )

    _render_recap_weight_settings(settings, key_prefix="overview")
    metrics = _overview_metrics(
        totals,
        items,
        activation_weight=settings.activation_weight,
        first_pay_weight=settings.first_pay_weight,
    )
    previous_metrics = (
        _overview_metrics(
            previous_totals,
            previous_items,
            activation_weight=settings.activation_weight,
            first_pay_weight=settings.first_pay_weight,
        )
        if previous_batch_id
        else None
    )
    _render_metric_row(metrics, previous_metrics)
    previous_record = read_batch_record(APP_DB, previous_batch_id) if previous_batch_id else {}
    st.caption(_comparison_caption(record, previous_record))

    st.subheader("分渠道总览")
    _render_channel_totals_table(channel_totals)

    status_metrics = _overview_status_metrics(items, totals, manifests, recap_items)
    status_columns = st.columns(len(status_metrics))
    for column, (label, value) in zip(status_columns, status_metrics.items()):
        column.metric(label, format_display_number(value, 0))

    st.subheader("清洗状态")
    status_rows = pd.DataFrame(
        [
            {"环节": "本周期明细", "状态": "已生成" if not items.empty else "无明细", "数量": len(items)},
            {
                "环节": "渠道汇总数据",
                "状态": "已进入总览" if _channel_total_count(totals) else "无",
                "数量": _channel_total_count(totals),
            },
            {
                "环节": "素材缓存",
                "状态": "已有缓存" if _succeeded_manifest_count(manifests) else "待缓存",
                "数量": _succeeded_manifest_count(manifests),
            },
            {"环节": "高价值复盘", "状态": "已生成" if not recap_items.empty else "待生成", "数量": len(recap_items)},
        ]
    )
    _show_frame(status_rows, height=220)


def _page_upload_cleaning() -> None:
    st.title("上传清洗")
    st.caption("上传新周期数据，完成字段标准化、渠道内去重、飞书台账回填，并更新本地数据。")

    with st.container(border=True):
        uploads = st.file_uploader(
            "上传渠道数据文件或文件夹",
            type=["xlsx", "xls", "csv", "zip"],
            accept_multiple_files="directory",
            key="upload_cleaning_files",
        )
        inferred = infer_period_from_upload_names(uploads or [])
        default_start = inferred[0] if inferred else date.today() - timedelta(days=6)
        default_end = inferred[1] if inferred else date.today()
        _sync_inferred_upload_period(uploads or [], inferred)
        level_label = st.segmented_control(
            "周期维度",
            [PERIOD_LEVEL_LABELS[level] for level in PERIOD_LEVELS],
            default=PERIOD_LEVEL_LABELS[PERIOD_LEVEL_WEEK],
            key="upload_period_level_label",
        )
        period_level = _period_level_from_label(level_label)
        start_col, end_col = st.columns(2)
        data_start = _chinese_date_input(start_col, "数据开始", default_start, key_prefix="upload_data_start")
        data_end = _chinese_date_input(end_col, "数据结束", default_end, key_prefix="upload_data_end")
        if inferred:
            st.info(f"已识别数据时间：{inferred[0].isoformat()} 至 {inferred[1].isoformat()}")

        period = review_period_from_dates(data_start, data_end, period_level)
        target_dir = _raw_dir_for_period(period)
        conflicts = detect_upload_channel_conflicts(
            uploads or [],
            target_dir,
            strip_common_period_root=True,
        )
        overwrite_existing_channels = False
        if conflicts:
            channels = "、".join(conflict.channel for conflict in conflicts)
            st.warning(f"该周期已有渠道数据：{channels}。勾选后会用本次上传替换对应渠道。")
            overwrite_existing_channels = st.checkbox("替换对应渠道", key="overwrite_existing_channels")
        auto_tier1_recap = st.checkbox(
            "清洗完成后自动补采并分析一级素材",
            value=False,
            key="auto_tier1_recap_after_upload",
            help="一级范围为抖音消耗Top20、其他平台消耗Top10。清洗成功和复盘任务分开提示；复盘失败不会回滚清洗入库结果。",
        )
        st.caption("二级曝光范围和三级阈值范围在高价值内容复盘页手动触发，避免上传等待时间过长。")

        if st.button("开始标准化清洗", type="primary", disabled=not uploads, width="stretch"):
            if data_end < data_start:
                st.error("数据开始日期不能晚于结束日期。")
            else:
                ledger = _load_feishu_ledger_for_check()
                st.session_state["pending_upload_feishu_ledger"] = ledger
                if _feishu_staleness_needs_confirmation(_ledger_staleness(ledger)):
                    _render_feishu_staleness_prompt(
                        _ledger_staleness(ledger),
                        action_label="继续清洗前请先确认飞书台账是否需要补充。",
                    )
                else:
                    _run_upload_cleaning(
                        uploads or [],
                        target_dir,
                        period,
                        overwrite_existing_channels,
                        auto_tier1_recap,
                        feishu_ledger=ledger,
                    )
        pending_upload_ledger = st.session_state.get("pending_upload_feishu_ledger")
        if pending_upload_ledger is not None and _feishu_staleness_needs_confirmation(_ledger_staleness(pending_upload_ledger)):
            if st.button("已检查飞书台账，继续清洗", width="stretch", key="confirm_stale_feishu_upload"):
                _run_upload_cleaning(
                    uploads or [],
                    target_dir,
                    period,
                    overwrite_existing_channels,
                    auto_tier1_recap,
                    feishu_ledger=pending_upload_ledger,
                )
                st.session_state.pop("pending_upload_feishu_ledger", None)

    with st.container(border=True):
        st.subheader("生成季度/年度汇总")
        c1, c2, c3 = st.columns(3)
        rollup_label = c1.segmented_control("汇总维度", ["季度", "年度"], default="季度", key="rollup_level_label")
        rollup_level = PERIOD_LEVEL_QUARTER if rollup_label == "季度" else PERIOD_LEVEL_YEAR
        year = int(c2.number_input("年份", min_value=2020, max_value=2100, value=date.today().year, step=1))
        quarter = None
        if rollup_level == PERIOD_LEVEL_QUARTER:
            quarter = int(c3.selectbox("季度", [1, 2, 3, 4], index=((date.today().month - 1) // 3)))
        period = rollup_period_for(rollup_level, year, quarter)
        components = select_rollup_component_batches(APP_DB, period)
        period_label = _batch_period_value_label(
            pd.Series(
                {
                    "period_level": period.period_level,
                    "period_key": period.period_key,
                    "period_start": period.period_start,
                    "period_end": period.period_end,
                }
            )
        )
        st.caption(f"{period_label}，可用于汇总的月度周期：{len(components)} 个。")
        if components:
            _show_frame(_rollup_components_display(components), height=180)
        if st.button("生成汇总数据", disabled=not components, width="stretch"):
            _run_rollup(period, components)


def _upload_batch_signature(uploads: list[object]) -> tuple[str, ...]:
    return tuple(sorted(str(getattr(upload, "name", "")) for upload in uploads))


def _sync_inferred_upload_period(
    uploads: list[object],
    inferred: tuple[date, date] | None,
    *,
    session_state=None,
) -> None:
    if not uploads or inferred is None:
        return
    state = st.session_state if session_state is None else session_state
    signature = _upload_batch_signature(uploads)
    signature_key = "upload_inferred_period_signature"
    if state.get(signature_key) == signature:
        return

    state[signature_key] = signature
    start_date, end_date = inferred
    for key_prefix, value in (
        ("upload_data_start", start_date),
        ("upload_data_end", end_date),
    ):
        state[f"{key_prefix}_year"] = value.year
        state[f"{key_prefix}_month"] = value.month
        state[f"{key_prefix}_day"] = value.day


def _page_high_value_recap() -> None:
    st.title("高价值内容复盘")
    batch_id = _select_batch("recap")
    if not batch_id:
        st.info("还没有可复盘的成功周期。")
        return

    items = list_content_performance_items(APP_DB, batch_id=batch_id)
    if items.empty:
        st.warning("当前周期没有可复盘的素材明细；渠道汇总数据只用于总览，不进入高价值复盘。")
        return

    settings = get_recap_settings(APP_DB)
    _render_recap_weight_settings(settings, key_prefix="recap")

    settings = get_recap_settings(APP_DB)
    top_pool = _top_pool_with_value(
        build_high_spend_content_pool(items),
        activation_weight=settings.activation_weight,
        first_pay_weight=settings.first_pay_weight,
    )
    executable_top_pool = filter_executable_top_content_pool(top_pool)
    asset_jobs = list_harvester_asset_jobs(APP_DB, batch_id=batch_id)
    manifests = _manifests_with_job_context(
        list_harvester_asset_manifests(APP_DB, batch_id=batch_id),
        asset_jobs,
    )
    display_manifests = _display_manifests_with_reusable_cache(
        top_pool,
        manifests,
        _reusable_cache_manifests_for_top_pool(top_pool, list_top_asset_cache_entries(APP_DB)),
    )
    recap_tables = _build_local_recap_tables(APP_DB, batch_id, top_pool)
    totals = list_period_channel_totals(APP_DB, batch_id=batch_id)
    total_metrics = _overview_metrics(
        totals,
        items,
        activation_weight=settings.activation_weight,
        first_pay_weight=settings.first_pay_weight,
    )
    analysis_jobs = list_analysis_jobs(APP_DB, batch_id=batch_id)
    report_status = load_manual_recap_status(APP_DB, batch_id)
    loaded_report = load_manual_recap_report(APP_DB, batch_id)
    supplements = list_manual_high_value_supplements(APP_DB, batch_id=batch_id)
    report_pool = _report_pool_with_manual_supplements(top_pool, supplements)
    quality_items = _quality_items_with_manual_supplements(top_pool, supplements)
    report_tab, evidence_tab, quality_tab = st.tabs(["高价值汇报报告", "素材证据与人工补齐", "分类与数据质量"])
    with report_tab:
        _render_high_value_report_tab(
            report_pool,
            report_status=report_status,
            manual_report=loaded_report.get("report", {}),
            recap_tables=recap_tables,
            total_metrics=total_metrics,
            manifests=display_manifests,
        )
    with evidence_tab:
        _render_recap_tier_panel(items, manifests, analysis_jobs, batch_id=batch_id)
        _render_high_value_evidence_tab(report_pool, executable_top_pool, manifests, analysis_jobs, batch_id=batch_id)
    with quality_tab:
        _render_high_value_quality_tab(quality_items, report_pool, batch_id=batch_id)


def _render_high_value_report_tab(
    top_pool: pd.DataFrame,
    *,
    report_status: dict[str, object],
    manual_report: object,
    recap_tables: dict[str, pd.DataFrame],
    total_metrics: dict[str, float],
    manifests: pd.DataFrame,
) -> None:
    status_copy = _report_status_copy(report_status)
    if bool(report_status.get("has_report")):
        st.success(status_copy)
    else:
        st.warning(status_copy)
        st.caption("当前内容为即时参考草稿，适合先检查方向；点击生成后才会固化为可汇报结论。")
    st.subheader("渠道消耗前 5")
    _render_channel_top_link_cards(top_pool, manifests=manifests)
    sections = _report_section_view_model(top_pool, report_status, manual_report)
    for title, section in sections.items():
        st.subheader(title)
        section_title = _text(section.get("title"))
        if section_title:
            st.markdown(f"**{section_title}**")
        for item in section.get("items", []):
            st.markdown(f"- {_text(item)}")
    if st.button("生成/更新口头汇报结论", type="primary", width="stretch", disabled=top_pool.empty):
        report = _local_oral_report_payload(top_pool)
        persist_oral_recap_report(
            APP_DB,
            _text(report_status.get("batch_id")),
            provider="local",
            model="oral-report-v1",
            report=report,
        )
        st.success("已根据当前选择周期的数据更新口头汇报结论。")
        st.rerun()
    with st.expander("数据支撑附录", expanded=False):
        _render_local_recap_tables(recap_tables, total_metrics=total_metrics)
        st.markdown("#### 高价值素材明细")
        _show_frame(_top_pool_display(top_pool), height=420)


def _render_high_value_evidence_tab(
    top_pool: pd.DataFrame,
    capture_pool: pd.DataFrame,
    manifests: pd.DataFrame,
    analysis_jobs: pd.DataFrame,
    *,
    batch_id: str,
) -> None:
    st.subheader("素材证据状态")
    summary = _evidence_status_summary(top_pool, manifests)
    columns = st.columns(len(summary))
    for column, (label, value) in zip(columns, summary.items()):
        column.metric(label, format_display_number(value, 0))
    _show_frame(_evidence_status_table(top_pool, manifests), height=320)

    capture_pool = capture_pool if capture_pool is not None else pd.DataFrame()
    if capture_pool.empty:
        st.warning("当前高价值素材还没有可执行的匹配状态，请先完成清洗匹配。")

    _render_asset_cache_status(
        _asset_cache_status_summary(top_pool, capture_pool, manifests, analysis_jobs),
        get_top_asset_cache_summary(APP_DB),
    )

    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        if c1.button("复用每日采集素材", disabled=capture_pool.empty, width="stretch"):
            reused = cache_existing_harvester_assets_for_batch(
                APP_DB,
                batch_id,
                capture_pool,
                harvester_root=resolve_harvester_root(),
            )
            st.success(f"已复用 {reused} 个素材缓存。")
        if c2.button("补采缺失重点素材", disabled=capture_pool.empty, width="stretch"):
            progress_placeholder = st.empty()
            progress_started_at = time.monotonic()

            def progress(event) -> None:
                total = int(getattr(event, "total", 0) or 0)
                completed = int(getattr(event, "completed", 0) or 0)
                text = _harvester_progress_text(event, progress_started_at)
                if total:
                    progress_placeholder.progress(
                        min(completed / total, 1.0),
                        text=text,
                    )
                else:
                    progress_placeholder.info(text)

            with st.status("正在补采缺失重点素材", expanded=True) as status:
                status.write("正在复用已有缓存并排除已下载素材。")
                status.write("补采在后台静默运行；只有遇到登录失效或风控验证时才需要人工处理。")
                result = run_harvester_asset_capture(APP_DB, batch_id, capture_pool, progress_callback=progress)
                if result.job_count:
                    status.write(f"本次实际补采 {result.job_count} 个，完成 {result.succeeded_count} 个，失败 {result.failed_count} 个。")
                else:
                    status.write("没有缺失素材需要补采。")
            if result.ok:
                progress_placeholder.empty()
                st.success(result.message)
            else:
                st.error(result.message)
        if c3.button("生成/更新类型复盘", disabled=top_pool.empty, width="stretch"):
            written = persist_type_recap_from_top_content(APP_DB, batch_id, top_pool)
            st.success(f"已基于高价值池写入 {written} 条类型复盘；不依赖素材补采或多模态。")
            st.rerun()
        missing_type_pool = _missing_type_pool(capture_pool)
        if c4.button("多模态补缺失类型", disabled=missing_type_pool.empty, width="stretch"):
            if _run_manual_multimodal_recap(
                batch_id,
                missing_type_pool,
                status_label="正在执行多模态补缺失类型",
                start_message="正在执行多模态素材分析，仅填充当前缺失的类型字段。",
                trigger="manual_fill_missing_type",
                analysis_purpose=ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
                pool_name="补类型任务",
                success_message=lambda updated_jobs, persisted: (
                    f"已更新 {updated_jobs} 个补类型任务，写入 {persisted.item_count} 条审计记录；仅填充空类型。"
                ),
                failure_next_step="请检查 MiniMax 配置、素材缓存和网络状态后重试。",
            ):
                st.rerun()
        if c5.button("生成/更新策略复盘", disabled=capture_pool.empty, width="stretch"):
            if _run_manual_multimodal_recap(
                batch_id,
                capture_pool,
                status_label="正在生成策略复盘",
                start_message="正在生成策略素材分析，并更新素材复盘和渠道类型策略。",
                trigger="manual_strategy_recap",
                analysis_purpose=ANALYSIS_PURPOSE_STRATEGY_RECAP,
                pool_name="策略任务",
                success_message=lambda updated_jobs, persisted: (
                    f"已更新 {updated_jobs} 个策略任务，写入 {persisted.item_count} 条素材复盘和 {persisted.strategy_count} 条渠道类型策略。"
                ),
                failure_next_step="清洗入库结果不受影响，可稍后重试策略复盘；请检查 MiniMax 配置、素材缓存和网络状态后重试。",
            ):
                st.rerun()

    _render_manual_supplement_form(batch_id)
    supplements = list_manual_high_value_supplements(APP_DB, batch_id=batch_id)
    with st.expander("人工补充记录", expanded=not supplements.empty):
        _show_frame(_manual_supplements_display(supplements), height=220)

    with st.expander("缓存占用与清理", expanded=False):
        cache_summary = get_top_asset_cache_summary(APP_DB)
        st.caption(
            f"当前缓存占用：{_format_bytes(cache_summary.get('size_bytes', 0))}，"
            f"素材数：{cache_summary.get('entry_count', 0)}，引用数：{cache_summary.get('ref_count', 0)}。"
        )
        if st.button("清理缓存", width="stretch", key=f"cleanup_cache_{batch_id}"):
            cleanup = cleanup_top_asset_cache(
                APP_DB,
                keep_batch_ids=_recent_successful_batch_ids(limit=8),
            )
            st.success(f"已清理 {cleanup.deleted_count} 个素材，释放 {_format_bytes(cleanup.deleted_bytes)}。")
            st.rerun()
        _show_frame(_top_asset_cache_entries_display(list_top_asset_cache_entries(APP_DB)), height=220)

    with st.expander("素材缓存记录与复盘任务", expanded=False):
        _show_frame(_asset_cache_records_display(manifests), height=260)
        _show_frame(_analysis_jobs_display(analysis_jobs), height=260)


def _render_recap_tier_panel(
    items: pd.DataFrame,
    manifests: pd.DataFrame,
    analysis_jobs: pd.DataFrame,
    *,
    batch_id: str,
) -> None:
    st.subheader("分级复盘任务")
    st.caption("一级可在上传清洗后自动触发；二级曝光范围和三级阈值范围在这里手动触发。每个范围会生成独立 LLM 报告，不覆盖其他范围。")
    tabs = st.tabs([RECAP_TIER_LABELS[key] for key in [RECAP_TIER_1_SPEND_TOP, RECAP_TIER_2_EXPOSURE_TOP, RECAP_TIER_3_THRESHOLD]])
    for tab, tier_key in zip(tabs, [RECAP_TIER_1_SPEND_TOP, RECAP_TIER_2_EXPOSURE_TOP, RECAP_TIER_3_THRESHOLD]):
        with tab:
            label = RECAP_TIER_LABELS[tier_key]
            definition = RECAP_TIER_DEFINITIONS[tier_key]
            tier_pool = _top_pool_with_value(
                build_recap_tier_pool(items, tier_key),
                activation_weight=get_recap_settings(APP_DB).activation_weight,
                first_pay_weight=get_recap_settings(APP_DB).first_pay_weight,
            )
            report_status = load_range_recap_status(APP_DB, batch_id, tier_key)
            status = _recap_tier_status_summary(tier_key, tier_pool, manifests, analysis_jobs, report_status)
            st.caption(definition)
            columns = st.columns(len(status))
            for column, (name, value) in zip(columns, status.items()):
                column.metric(name, value if isinstance(value, str) else format_display_number(value, 0))
            if tier_pool.empty:
                st.warning("当前范围没有可执行素材。请先确认清洗匹配状态，或补充本地内容库后重跑清洗。")
            else:
                action_label = "生成/更新一级 LLM 复盘" if tier_key == RECAP_TIER_1_SPEND_TOP else f"生成/更新{label} LLM 复盘"
                if st.button(action_label, width="stretch", key=f"run_{tier_key}_{batch_id}"):
                    if _run_recap_tier_pipeline(batch_id, items, tier_key):
                        st.rerun()
                report = load_range_recap_report(APP_DB, batch_id, tier_key)
                if report:
                    _render_range_recap_report(report.get("report", {}))
                with st.expander("查看该范围素材", expanded=False):
                    _show_frame(_top_pool_display(tier_pool), height=360)


def _render_range_recap_report(report: object) -> None:
    if not isinstance(report, dict):
        return
    overview = report.get("overview") if isinstance(report.get("overview"), dict) else {}
    if overview:
        st.markdown("#### LLM 复盘报告")
        main_text = _text(overview.get("report") or overview.get("summary"))
        if main_text:
            st.markdown(main_text)
        direction = _text(overview.get("next_cycle_direction"))
        if direction:
            st.markdown(f"**下周期方向**：{direction}")
        for section in overview.get("sections", []) if isinstance(overview.get("sections"), list) else []:
            if not isinstance(section, dict):
                continue
            title = _text(section.get("title"))
            if title:
                st.markdown(f"**{title}**")
            for item in section.get("items", []) if isinstance(section.get("items"), list) else []:
                st.markdown(f"- {_text(item)}")
    channels = report.get("channels") if isinstance(report.get("channels"), list) else []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        name = _text(channel.get("channel")) or "未命名渠道"
        with st.expander(name, expanded=False):
            analysis = _text(channel.get("analysis"))
            if analysis:
                st.markdown(analysis)
            direction = _text(channel.get("next_cycle_direction"))
            if direction:
                st.markdown(f"**执行方向**：{direction}")
            for section in channel.get("sections", []) if isinstance(channel.get("sections"), list) else []:
                if not isinstance(section, dict):
                    continue
                title = _text(section.get("title"))
                if title:
                    st.markdown(f"**{title}**")
                for item in section.get("items", []) if isinstance(section.get("items"), list) else []:
                    st.markdown(f"- {_text(item)}")


def _render_high_value_quality_tab(items: pd.DataFrame, top_pool: pd.DataFrame, *, batch_id: str) -> None:
    st.subheader("分类与数据质量")
    issues = _classification_quality_issues(items)
    if issues.empty:
        st.success("抖音、小红书、B站分类口径已通过当前校验。")
    else:
        st.warning("存在分类口径不一致或缺失项，以下素材不应直接进入口头结论。")
        _show_frame(issues, height=320)
    st.subheader("类型复盘")
    _render_type_recap_result_tables(list_type_recap_items(APP_DB, batch_id=batch_id))
    st.subheader("素材复盘")
    _show_frame(_multimodal_recap_display(list_multimodal_recap_items(APP_DB, batch_id=batch_id)), height=360)
    st.subheader("渠道类型策略")
    _show_frame(_strategy_recap_display(list_strategy_recap_items(APP_DB, batch_id=batch_id)), height=360)
    with st.expander("高价值素材明细", expanded=False):
        _show_frame(_top_pool_display(top_pool), height=420)


def _report_pool_with_manual_supplements(top_pool: pd.DataFrame, supplements: pd.DataFrame) -> pd.DataFrame:
    frame = top_pool.copy() if top_pool is not None else pd.DataFrame()
    manual_rows = _manual_supplements_as_report_rows(supplements)
    if manual_rows.empty:
        return frame
    if frame.empty:
        return manual_rows
    return pd.concat([frame, manual_rows], ignore_index=True, sort=False)


def _quality_items_with_manual_supplements(items: pd.DataFrame, supplements: pd.DataFrame) -> pd.DataFrame:
    frame = items.copy() if items is not None else pd.DataFrame()
    manual_rows = _manual_supplements_as_report_rows(supplements)
    if manual_rows.empty:
        return frame
    if frame.empty:
        return manual_rows
    return pd.concat([frame, manual_rows], ignore_index=True, sort=False)


def _manual_supplements_as_report_rows(supplements: pd.DataFrame) -> pd.DataFrame:
    if supplements is None or supplements.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for _, row in supplements.iterrows():
        platform = _text(row.get("platform"))
        channel = _text(row.get("channel")) or platform
        content_id = _text(row.get("content_id"))
        content_url = _text(row.get("content_url"))
        title = _text(row.get("title"))
        identity = _text(row.get("supplement_id")) or content_id or content_url or title
        rows.append(
            {
                "asset_key": f"manual:{identity}",
                "platform": platform,
                "channel": channel,
                "content_id": content_id,
                "content_url": content_url,
                "title": title,
                "account": _text(row.get("account")),
                "category_l1": _text(row.get("category_l1")),
                "category_l2": _text(row.get("category_l2")),
                "bilibili_content_type": _text(row.get("bilibili_content_type")),
                "content_type": _text(row.get("bilibili_content_type"))
                or _text(row.get("category_l2"))
                or _text(row.get("category_l1")),
                "evidence_path": _text(row.get("evidence_path")),
                "manual_reason": _text(row.get("reason")),
                "source_kind": "manual",
                "spend": 0.0,
                "impressions": 0.0,
                "activations": 0.0,
                "first_pay_count": 0.0,
                "value": 0.0,
                "high_spend_reason": "人工新增",
            }
        )
    return pd.DataFrame(rows)


def _render_manual_supplement_form(batch_id: str) -> None:
    with st.form(f"manual_supplement_{batch_id}", border=True):
        st.subheader("人工补充高价值素材")
        c1, c2, c3 = st.columns(3)
        platform = c1.selectbox("平台", ["抖音", "小红书", "B站"], key=f"supplement_platform_{batch_id}")
        channel = c2.text_input("渠道", key=f"supplement_channel_{batch_id}")
        content_id = c3.text_input("平台编号", key=f"supplement_content_id_{batch_id}")
        title = st.text_input("标题", key=f"supplement_title_{batch_id}")
        content_url = st.text_input("作品链接", key=f"supplement_url_{batch_id}")
        d1, d2, d3 = st.columns(3)
        category_l1 = d1.text_input("一级类型", key=f"supplement_l1_{batch_id}")
        category_l2 = d2.text_input("二级类型", key=f"supplement_l2_{batch_id}")
        bilibili_content_type = d3.text_input("B站内容类型", key=f"supplement_bili_type_{batch_id}")
        evidence_path = st.text_input("截图/封面/缓存路径", key=f"supplement_evidence_path_{batch_id}")
        reason = st.text_area("为什么影响结论", key=f"supplement_reason_{batch_id}", height=90)
        submitted = st.form_submit_button("保存补充并标记结论待更新", type="primary", width="stretch")
        if submitted:
            if not (_text(content_id) or _text(content_url) or _text(title)):
                st.error("至少填写平台编号、作品链接或标题中的一项。")
                return
            upsert_manual_high_value_supplement(
                APP_DB,
                batch_id,
                {
                    "platform": platform,
                    "channel": channel,
                    "content_id": content_id,
                    "content_url": content_url,
                    "title": title,
                    "category_l1": category_l1,
                    "category_l2": category_l2,
                    "bilibili_content_type": bilibili_content_type,
                    "evidence_path": evidence_path,
                    "reason": reason,
                },
            )
            clear_manual_recap_report(APP_DB, batch_id)
            st.success("已保存补充素材。当前口头汇报结论已标记为待更新。")
            st.rerun()


def _report_status_copy(status: dict[str, object]) -> str:
    if bool(status.get("has_report")):
        return "页面汇报结论已基于当前选择周期的数据；上传清洗、类型复盘、页面汇报是分步完成态。"
    return "页面汇报结论待更新：上传清洗完成后，还需要生成类型复盘并点击生成/更新口头汇报结论。"


def _render_user_recovery_hint(message: str, exc: Exception | None = None) -> None:
    detail = f"错误信息：{exc}" if exc else ""
    if detail:
        st.info(f"{message}\n\n{detail}")
    else:
        st.info(message)


def _run_manual_multimodal_recap(
    batch_id: str,
    pool: pd.DataFrame,
    *,
    status_label: str,
    start_message: str,
    trigger: str,
    analysis_purpose: str,
    pool_name: str,
    success_message,
    failure_next_step: str,
) -> bool:
    try:
        with st.status(status_label, expanded=True) as status:
            total = len(pool) if pool is not None else 0
            status.write(f"{start_message} 本次待处理 {total} 个素材。")
            status.write("正在重置本轮任务状态。")
            reset_top_multimodal_jobs(
                APP_DB,
                batch_id,
                pool,
                trigger=trigger,
                analysis_purpose=analysis_purpose,
            )
            status.write("正在确认 MiniMax 与素材分析环境。")
            copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
            status.write("正在执行多模态素材分析。")
            updated_jobs = run_top_multimodal_analysis_from_manifests(
                APP_DB,
                batch_id,
                analyzer=lambda job, manifest: analyze_top_content_with_minimax(job, manifest, env_path=ENV_PATH),
                analysis_purpose=analysis_purpose,
            )
            status.write(f"{pool_name}已完成 {updated_jobs}/{total} 个素材分析任务，正在写入复盘结果。")
            persisted = persist_multimodal_recap(
                APP_DB,
                batch_id,
                pool,
                analysis_purpose=analysis_purpose,
                analyzer=_analysis_job_result_analyzer(batch_id, analysis_purpose),
            )
            status.update(label=f"{status_label}完成", state="complete")
        st.success(success_message(updated_jobs, persisted))
        return True
    except Exception as exc:
        st.warning(f"{status_label}未完成：{exc}")
        _render_user_recovery_hint(failure_next_step, exc)
        return False


def _report_section_view_model(
    top_pool: pd.DataFrame,
    report_status: dict[str, object],
    manual_report: object,
) -> dict[str, dict[str, list[str] | str]]:
    frame = _prepare_report_pool(top_pool)
    sections: dict[str, dict[str, list[str] | str]] = {
        "一、整体数据": {
            "title": "数据结论",
            "items": _overall_report_items(frame, report_status, manual_report),
        }
    }
    for title, channel_key in REPORT_CHANNEL_SECTIONS:
        channel_frame = _channel_report_frame(frame, channel_key)
        sections[title] = {
            "title": f"{channel_key}内容分析",
            "items": _channel_report_items(channel_frame, channel_key),
        }
    sections["下周重点策略"] = {"title": "下周重点策略", "items": _next_strategy_items(frame)}
    return sections


def _local_oral_report_payload(top_pool: pd.DataFrame) -> dict[str, object]:
    sections = _report_section_view_model(top_pool, {"has_report": True}, {})
    return {
        "overview": {
            "summary": "；".join(sections["一、整体数据"]["items"]),
            "sections": [
                {"title": title, "items": section["items"]}
                for title, section in sections.items()
            ],
        },
        "channels": [
            {"channel": channel_key, "analysis": "；".join(sections[title]["items"])}
            for title, channel_key in REPORT_CHANNEL_SECTIONS
        ],
    }


def _prepare_report_pool(top_pool: pd.DataFrame) -> pd.DataFrame:
    frame = top_pool.copy() if top_pool is not None else pd.DataFrame()
    for column in [
        "platform",
        "channel",
        "content_id",
        "title",
        "content_url",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "content_type",
        "evidence_path",
        "manual_reason",
        "source_kind",
    ]:
        if column not in frame.columns:
            frame[column] = ""
        frame[column] = frame[column].fillna("").astype(str)
    for column in ["spend", "impressions", "activations", "first_pay_count", "value"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return frame


def _overall_report_items(
    frame: pd.DataFrame,
    report_status: dict[str, object],
    manual_report: object,
) -> list[str]:
    status_line = _report_status_copy(report_status)
    if frame.empty:
        return [status_line, "当前周期没有进入高价值池的素材，暂不做过多结论。"]
    channel_count = int(frame["channel"].replace("", pd.NA).dropna().nunique())
    spend = _sum(frame, "spend")
    activations = _sum(frame, "activations")
    top_channel = _top_dimension_name(frame, "channel")
    items = [
        status_line,
        f"本周期高价值池共 {len(frame)} 条素材，覆盖 {channel_count} 个渠道，高价值消耗 {format_display_number(spend, 0)}，激活 {format_display_number(activations, 0)}。",
    ]
    manual_count = int(frame.get("source_kind", pd.Series(dtype=object)).fillna("").astype(str).eq("manual").sum())
    if manual_count:
        items.append(f"已纳入 {manual_count} 条人工补充的高价值素材，结论更新时会一起参与口头汇报判断。")
    if top_channel:
        items.append(f"当前高价值消耗主要集中在{top_channel}，口头汇报应优先说明该渠道的有效素材和可复用方向。")
    type_summary = _type_performance_summary(frame)
    if type_summary.get("primary"):
        items.append(f"一级类型表现：{type_summary['primary']}。")
    if type_summary.get("secondary"):
        items.append(f"二级类型表现：{type_summary['secondary']}。")
    commonality = _high_value_commonality(frame)
    if commonality:
        items.append(f"高价值内容共性：{commonality}。")
    report_items = _manual_report_overview_items(manual_report)
    return items + report_items[:2]


def _manual_report_overview_items(manual_report: object) -> list[str]:
    if not isinstance(manual_report, dict):
        return []
    overview = manual_report.get("overview", {})
    if not isinstance(overview, dict):
        return []
    sections = overview.get("sections")
    if isinstance(sections, list) and any(
        isinstance(section, dict) and _text(section.get("title")) == "一、整体数据"
        for section in sections
    ):
        return []
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        if _text(section.get("title")) in {"核心结论", "下周期动作"}:
            return [_text(item) for item in section.get("items", []) if _text(item)]
    return [
        _text(overview.get(key))
        for key in ["summary", "report", "next_cycle_direction"]
        if _text(overview.get(key))
    ]


def _channel_report_frame(frame: pd.DataFrame, channel_key: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    channel = frame["channel"].fillna("").astype(str)
    platform = frame["platform"].fillna("").astype(str)
    if channel_key == "B站":
        return frame[channel.str.contains("B站", na=False) | platform.eq("B站")].copy()
    return frame[channel.str.contains(channel_key, na=False)].copy()


def _channel_report_items(frame: pd.DataFrame, channel_key: str) -> list[str]:
    if frame.empty:
        return [f"{channel_key}当前数据未提供足够证据，暂不做过多结论。"]
    spend = _sum(frame, "spend")
    activations = _sum(frame, "activations")
    first_pay = _sum(frame, "first_pay_count")
    top_title = _top_title(frame)
    type_summary = _type_performance_summary(frame)
    type_name = _channel_type_signal(frame)
    commonality = _high_value_commonality(frame)
    items = [
        f"ps：{channel_key}数据取自当前高价值池内的可报告素材，共 {len(frame)} 条，高价值消耗 {format_display_number(spend, 0)}，激活 {format_display_number(activations, 0)}，付费 {format_display_number(first_pay, 0)}。",
    ]
    if top_title:
        items.append(f"表现最突出的素材是「{top_title}」，建议口头汇报时结合封面或截图说明它为什么值得复用。")
    if type_summary.get("primary"):
        items.append(f"一级类型表现：{type_summary['primary']}。")
    if type_summary.get("secondary"):
        items.append(f"二级类型表现：{type_summary['secondary']}。")
    if type_name:
        items.append(f"内容类型上，{type_name}是当前最值得优先复盘的方向，后续应围绕同类素材继续补充样本。")
    if commonality:
        items.append(f"高价值内容共性：{commonality}。")
    return items


def _next_strategy_items(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return ["先补齐高价值素材证据，再判断下周策略。"]
    top_channel = _top_dimension_name(frame, "channel")
    type_name = _channel_type_signal(frame)
    items = []
    if top_channel:
        items.append(f"优先复用{top_channel}中已验证的高价值素材方向，先扩大同类内容样本。")
    if type_name:
        items.append(f"继续测试{type_name}，并对低证据素材补截图、封面或缓存。")
    items.append("链接隐藏或删除的作品不直接丢弃，先用缓存或人工截图确认后再纳入结论。")
    return items


def _top_dimension_name(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    grouped = frame.groupby(column, dropna=False)["spend"].sum().sort_values(ascending=False)
    for name in grouped.index.tolist():
        text = _text(name)
        if text:
            return text
    return ""


def _top_title(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    sorted_frame = frame.sort_values("spend", ascending=False)
    for _, row in sorted_frame.iterrows():
        title = _text(row.get("title")) or _text(row.get("content_id"))
        if title:
            return title
    return ""


def _channel_type_signal(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    candidates: list[str] = []
    for _, row in frame.iterrows():
        classification = _row_classification(row)
        if classification.platform == "B站":
            candidates.append(classification.bilibili_type)
        else:
            candidates.append(classification.secondary_type or classification.primary_type)
    series = pd.Series([item for item in candidates if item])
    if series.empty:
        return ""
    return _text(series.value_counts().index[0])


def _type_performance_summary(frame: pd.DataFrame) -> dict[str, str]:
    if frame.empty:
        return {"primary": "", "secondary": ""}
    primary_rows: list[dict[str, object]] = []
    secondary_rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        classification = _row_classification(row)
        if classification.platform == "B站":
            if classification.bilibili_type:
                primary_rows.append({"类型": classification.bilibili_type, "spend": _number(row.get("spend")), "value": _number(row.get("value"))})
            continue
        if classification.primary_type:
            primary_rows.append({"类型": classification.primary_type, "spend": _number(row.get("spend")), "value": _number(row.get("value"))})
        if classification.secondary_type:
            secondary_rows.append({"类型": classification.secondary_type, "spend": _number(row.get("spend")), "value": _number(row.get("value"))})
    return {
        "primary": _top_type_sentence(pd.DataFrame(primary_rows)),
        "secondary": _top_type_sentence(pd.DataFrame(secondary_rows)),
    }


def _top_type_sentence(rows: pd.DataFrame) -> str:
    if rows.empty:
        return ""
    grouped = rows.groupby("类型", dropna=False).agg(spend=("spend", "sum"), value=("value", "sum"), item_count=("类型", "size"))
    grouped = grouped.sort_values(["value", "spend"], ascending=[False, False])
    parts = []
    for type_name, row in grouped.head(3).iterrows():
        label = _text(type_name)
        if not label:
            continue
        parts.append(
            f"{label}（{int(row['item_count'])}条，消耗{format_display_number(row['spend'], 0)}，价值{format_display_number(row['value'], 0)}）"
        )
    return "、".join(parts)


def _high_value_commonality(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    titles = " ".join(_text(value) for value in frame.get("title", pd.Series(dtype=object)).tolist())
    signals = [
        ("普通人", "围绕普通人处境、财富焦虑或可执行方法展开"),
        ("股民", "直接击中股民身份、情绪和交易心态"),
        ("K线", "用K线、指标或图表结构降低理解门槛"),
        ("复利", "用复利、存钱、财务自由等长期收益叙事承接转化"),
        ("涨停", "用涨停、资金、板块等明确市场结果制造即时关注"),
        ("财富", "用财富层级、财富曲线或人物故事强化传播钩子"),
    ]
    matched = [description for token, description in signals if token in titles]
    if not matched:
        type_name = _channel_type_signal(frame)
        return f"集中在{type_name}等已验证类型，标题通常先给出明确问题或强情绪场景" if type_name else ""
    return "；".join(matched[:3])


def _row_classification(row: pd.Series):
    platform_text = " ".join(_text(row.get(column)) for column in ["platform", "platform_group", "channel"])
    return normalize_platform_classification(
        platform_text,
        category_l1=row.get("category_l1"),
        category_l2=row.get("category_l2"),
        bilibili_content_type=row.get("bilibili_content_type"),
        content_type=row.get("content_type"),
    )


def _evidence_status_summary(top_pool: pd.DataFrame, manifests: pd.DataFrame) -> dict[str, int]:
    table = _evidence_status_table(top_pool, manifests)
    counts = table["证据状态"].value_counts().to_dict() if not table.empty else {}
    return {
        "可汇报": int(counts.get("可汇报", 0)),
        "待补齐": int(counts.get("待补齐", 0)),
        "待补素材": int(counts.get("待补素材", counts.get("待补齐", 0))),
        "链接不可访问": int(counts.get("链接不可访问", 0)),
        "可分析但作品链接缺失": int(counts.get("可分析但作品链接缺失", 0)),
        "未匹配本地库": int(counts.get("未匹配本地库", 0)),
        "有数据无素材": int(counts.get("有数据无素材", 0)),
        "人工新增": int(counts.get("人工新增", 0)),
    }


def _evidence_status_table(top_pool: pd.DataFrame, manifests: pd.DataFrame) -> pd.DataFrame:
    frame = top_pool.copy() if top_pool is not None else pd.DataFrame()
    for column in [
        "asset_key",
        "platform",
        "channel",
        "content_id",
        "title",
        "content_url",
        "work_id",
        "work_url",
        "material_id",
        "ad_material_id",
        "ad_material_url",
        "ad_cover_url",
        "match_status",
        "analysis_status",
        "source_kind",
        "evidence_path",
        "manual_reason",
    ]:
        if column not in frame.columns:
            frame[column] = ""
    manifest_lookup = _manifest_status_lookup(manifests)
    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        manifest = _manifest_for_row(row, manifest_lookup)
        status = _text(manifest.get("status"))
        error = _text(manifest.get("error_message"))
        url = _text(row.get("content_url"))
        work_url = _text(row.get("work_url"))
        source_kind = _text(row.get("source_kind"))
        if source_kind == "manual":
            evidence_status = "人工新增"
            reason_parts = ["人工补充素材"]
            manual_reason = _text(row.get("manual_reason"))
            evidence_path = _text(row.get("evidence_path"))
            if manual_reason:
                reason_parts.append(manual_reason)
            if evidence_path:
                reason_parts.append(f"证据：{evidence_path}")
            reason = "；".join(reason_parts)
        elif status == "succeeded":
            evidence_status = "可汇报"
            reason = "已有素材缓存或封面证据"
        elif _has_douyin_ad_material_evidence(row) and _has_work_identity(row):
            evidence_status = "可汇报"
            reason = "已有作品身份，并有巨量视频/封面素材证据"
        elif _is_inaccessible_reason(error):
            evidence_status = "链接不可访问"
            reason = error
        elif _has_douyin_ad_material_evidence(row) and not _has_work_identity(row):
            evidence_status = "可分析但作品链接缺失"
            reason = "已有巨量视频/封面证据，可做素材分析；缺少作品链接，不能生成作品打开链接或作品ID。"
        elif _is_unmatched_local_content(row):
            evidence_status = "未匹配本地库"
            reason = "作品ID/作品链接/标题都未匹配到本地内容库"
        elif not url:
            evidence_status = "有数据无素材"
            reason = "缺少作品链接，需要人工补充截图、封面或缓存路径"
        else:
            evidence_status = "待补齐"
            reason = error or "尚未取得可报告素材证据"
        rows.append(
            {
                "证据状态": evidence_status,
                "平台": _text(row.get("platform")),
                "渠道": _text(row.get("channel")),
                "平台编号": _text(row.get("content_id")),
                "标题": _text(row.get("title")),
                "作品链接": url or work_url,
                "巨量链接": _text(row.get("ad_material_url")),
                "巨量封面链接": _text(row.get("ad_cover_url")),
                "原因": reason,
            }
        )
    return pd.DataFrame(rows, columns=["证据状态", "平台", "渠道", "平台编号", "标题", "作品链接", "巨量链接", "巨量封面链接", "原因"])


def _manifest_for_row(row: pd.Series, manifest_lookup: dict[str, dict[str, str]]) -> dict[str, str]:
    for asset_key in _asset_key_candidates_for_row(row):
        manifest = manifest_lookup.get(asset_key)
        if manifest:
            return manifest
    return {}


def _has_douyin_ad_material_evidence(row: pd.Series) -> bool:
    platform = _text(row.get("platform"))
    if platform != "抖音":
        return False
    has_ad_asset = bool(_text(row.get("ad_material_url")) or _text(row.get("ad_cover_url")))
    if not has_ad_asset:
        return False
    matched = _text(row.get("match_status")) == "已匹配" or _text(row.get("analysis_status")) == "可分析"
    return matched or _has_work_identity(row) or _has_content_type(row)


def _has_work_identity(row: pd.Series) -> bool:
    return bool(
        _text(row.get("content_url"))
        or _text(row.get("work_url"))
        or _text(row.get("content_id"))
        or _text(row.get("work_id"))
    )


def _is_unmatched_local_content(row: pd.Series) -> bool:
    status = _text(row.get("match_status"))
    analysis_status = _text(row.get("analysis_status"))
    has_type = _has_content_type(row)
    return status == "未匹配" or (analysis_status == "不可分析" and not has_type and not _has_work_identity(row))


def _has_content_type(row: pd.Series) -> bool:
    return bool(
        _text(row.get("category_l1"))
        or _text(row.get("category_l2"))
        or _text(row.get("content_type"))
        or _text(row.get("bilibili_content_type"))
    )


def _manifest_status_lookup(manifests: pd.DataFrame | None) -> dict[str, dict[str, str]]:
    if manifests is None or manifests.empty:
        return {}
    lookup: dict[str, dict[str, str]] = {}
    frame = manifests.copy()
    for column in ["asset_key", "status", "error_message"]:
        if column not in frame.columns:
            frame[column] = ""
    for _, row in frame.iterrows():
        asset_key = _text(row.get("asset_key"))
        if asset_key and asset_key not in lookup:
            lookup[asset_key] = {
                "status": _text(row.get("status")),
                "error_message": _text(row.get("error_message")),
            }
    return lookup


def _is_inaccessible_reason(reason: object) -> bool:
    text = _text(reason)
    return any(token in text for token in ["删除", "隐藏", "不可访问", "404", "下架", "不存在"])


def _classification_quality_issues(items: pd.DataFrame) -> pd.DataFrame:
    frame = items.copy() if items is not None else pd.DataFrame()
    for column in ["platform", "channel", "content_id", "title", "category_l1", "category_l2", "bilibili_content_type"]:
        if column not in frame.columns:
            frame[column] = ""
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    rows: list[dict[str, str]] = []
    for _, row in frame.iterrows():
        problem = _classification_problem(row)
        if not problem:
            continue
        rows.append(
            {
                "平台": _text(row.get("platform")),
                "渠道": _text(row.get("channel")),
                "平台编号": _text(row.get("content_id")),
                "标题": _text(row.get("title")),
                "一级类型": _text(row.get("category_l1")),
                "二级类型": _text(row.get("category_l2")),
                "B站内容类型": _text(row.get("bilibili_content_type")),
                "问题": problem,
            }
        )
    return pd.DataFrame(rows, columns=["平台", "渠道", "平台编号", "标题", "一级类型", "二级类型", "B站内容类型", "问题"])


def _classification_problem(row: pd.Series) -> str:
    platform = _text(row.get("platform"))
    l1 = _text(row.get("category_l1"))
    l2 = _text(row.get("category_l2"))
    bilibili_type = _text(row.get("bilibili_content_type"))
    if platform == "抖音":
        if l1 not in DOUYIN_TAXONOMY:
            return f"抖音一级类型只能是{'、'.join(DOUYIN_TAXONOMY)}。"
        allowed = DOUYIN_TAXONOMY[l1]
        if allowed and l2 not in allowed:
            return f"抖音{l1}二级类型只能是{'、'.join(sorted(allowed))}。"
        if not allowed and l2:
            return f"抖音{l1}二级类型必须为空。"
    if platform == "小红书":
        if l1 not in XHS_TAXONOMY:
            return "小红书一级类型只能是图文或视频。"
        allowed = XHS_TAXONOMY[l1]
        if l2 not in allowed:
            return f"小红书{l1}二级类型只能是{'、'.join(sorted(allowed))}。"
    if platform == "B站":
        effective_type = bilibili_type or l1
        if l2:
            return "B站只使用单级内容类型，二级类型必须为空。"
        if effective_type not in BILIBILI_CONTENT_TYPES:
            return f"B站内容类型只能是{'、'.join(sorted(BILIBILI_CONTENT_TYPES))}。"
    return ""


def _manual_supplements_display(supplements: pd.DataFrame) -> pd.DataFrame:
    if supplements is None or supplements.empty:
        return pd.DataFrame(columns=["平台", "渠道", "平台编号", "标题", "作品链接", "一级类型", "二级类型", "B站内容类型", "证据路径", "影响结论原因"])
    columns = [
        "platform",
        "channel",
        "content_id",
        "title",
        "content_url",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "evidence_path",
        "reason",
    ]
    display = supplements[[column for column in columns if column in supplements.columns]].copy()
    return localize_columns(display.rename(columns={"evidence_path": "证据路径", "reason": "影响结论原因"}))


def _page_local_assets() -> None:
    st.title("本地总表")
    st.caption("全量本地素材库，按平台和素材 ID 去重；线上飞书只读，同步动作只更新本地总表和飞书快照。")

    with st.container(border=True):
        st.subheader("本地总素材表")
        if st.button("从线上飞书读取并更新本地总表", width="stretch"):
            _sync_feishu_ledger_to_local("manual:feishu", confirm_stale=False)
        pending_manual_ledger = st.session_state.get("pending_manual_feishu_ledger")
        if pending_manual_ledger is not None and _feishu_staleness_needs_confirmation(_ledger_staleness(pending_manual_ledger)):
            if st.button("已检查飞书台账，继续更新本地总表", width="stretch", key="confirm_stale_feishu_manual"):
                _sync_feishu_ledger_to_local("manual:feishu", ledger=pending_manual_ledger, confirm_stale=True)
                st.session_state.pop("pending_manual_feishu_ledger", None)
        _render_feishu_sync_diff()
        _show_frame(_local_content_assets_display(list_local_content_assets(APP_DB)), height=420)


def _page_asset_cache() -> None:
    st.title("周期数据")
    st.caption("按周期查看清洗明细、渠道总数据、素材缓存记录和复盘结果。")
    batch_id = _select_batch("assets_cache", allow_empty=True)
    selected_batch_id = batch_id or _latest_batch_id()

    with st.expander("本周期清洗明细", expanded=True):
        _show_frame(_content_performance_display(list_content_performance_items(APP_DB, batch_id=selected_batch_id or "")), height=360)
    with st.expander("渠道总数据", expanded=False):
        _show_frame(list_period_channel_totals(APP_DB, batch_id=selected_batch_id or ""), height=260)
    with st.expander("素材缓存记录", expanded=False):
        _show_frame(_asset_cache_jobs_display(list_harvester_asset_jobs(APP_DB, batch_id=selected_batch_id or "")), height=240)
        _show_frame(_asset_cache_records_display(list_harvester_asset_manifests(APP_DB, batch_id=selected_batch_id or "")), height=300)
    with st.expander("复盘结果", expanded=False):
        _show_frame(_multimodal_recap_display(list_multimodal_recap_items(APP_DB, batch_id=selected_batch_id or "")), height=260)
        _render_type_recap_result_tables(list_type_recap_items(APP_DB, batch_id=selected_batch_id or ""))


def _page_trends() -> None:
    st.title("历史趋势")
    st.caption("查看不同周期下核心投放指标的变化。")
    metric_labels = ["消耗", "曝光", "激活数", "付费数", "激活成本", "付费成本"]
    metric_columns = {
        "消耗": "spend",
        "曝光": "impressions",
        "激活数": "activations",
        "付费数": "first_pay_count",
        "激活成本": "activation_cost",
        "付费成本": "first_pay_cost",
    }
    c1, c2, c3 = st.columns([1, 1, 1])
    level_label = c1.segmented_control(
        "周期维度",
        [PERIOD_LEVEL_LABELS[level] for level in PERIOD_LEVELS],
        default=PERIOD_LEVEL_LABELS[PERIOD_LEVEL_WEEK],
        key="trend_period_level_label",
    )
    period_level = _period_level_from_label(level_label)
    window = int(c2.number_input("展示周期数", min_value=3, max_value=36, value=12, step=1))
    metric_label = c3.selectbox("指标", metric_labels, index=0)

    trend = _build_trend_frame(period_level, window)
    if trend.empty:
        st.info("暂无可展示的历史趋势。")
        return
    y_column = metric_columns[metric_label]
    chart = trend[["trend_period", y_column]].copy()
    chart = chart.set_index("trend_period")
    st.line_chart(chart, height=360)
    st.subheader("趋势明细")
    _show_frame(_trend_display_frame(trend), height=420)


PAGES = [
    st.Page(_page_overview, title="总览", default=True),
    st.Page(_page_upload_cleaning, title="上传清洗"),
    st.Page(_page_high_value_recap, title="高价值内容复盘"),
    st.Page(_page_local_assets, title="本地总表"),
    st.Page(_page_asset_cache, title="周期数据"),
    st.Page(_page_trends, title="历史趋势"),
]


def _chinese_date_input(parent, label: str, value: date, *, key_prefix: str) -> date:
    parent.markdown(f"**{label}**")
    year_col, month_col, day_col = parent.columns([1.2, 1, 1])
    year = int(
        year_col.number_input(
            "年",
            min_value=2020,
            max_value=2100,
            value=int(value.year),
            step=1,
            key=f"{key_prefix}_year",
        )
    )
    month = int(
        month_col.selectbox(
            "月",
            list(range(1, 13)),
            index=max(0, min(11, int(value.month) - 1)),
            format_func=lambda month_value: f"{month_value}月",
            key=f"{key_prefix}_month",
        )
    )
    max_day = calendar.monthrange(year, month)[1]
    day = int(
        day_col.selectbox(
            "日",
            list(range(1, max_day + 1)),
            index=max(0, min(max_day - 1, int(value.day) - 1)),
            format_func=lambda day_value: f"{day_value}日",
            key=f"{key_prefix}_day",
        )
    )
    return date(year, month, day)


def _run_upload_cleaning(
    uploads: list[object],
    target_dir: Path,
    period,
    overwrite_existing_channels: bool,
    auto_tier1_recap: bool = False,
    feishu_ledger: pd.DataFrame | None = None,
) -> None:
    copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
    try:
        with st.status("正在标准化清洗", expanded=True) as status:
            status.write("已接收上传数据，正在保存到本周期目录。")
            materialized = materialize_uploaded_files(
                uploads,
                target_dir,
                strip_common_period_root=True,
                replace_same_channel=overwrite_existing_channels,
            )
            status.write("文件已保存，正在进入字段标准化、去重和飞书台账回填流程。")

            def progress(message: str) -> None:
                status.write(message)

            result = run_archived_workflow(
                materialized.raw_dir,
                period.period_start,
                period.period_end,
                output_root=APP_OUTPUTS,
                processed_root=APP_PROCESSED,
                db_path=APP_DB,
                category_rules_path=CATEGORY_RULES,
                env_path=ENV_PATH,
                period_level=period.period_level,
                period_key=period.period_key,
                period_label=period.period_label,
                data_start=period.data_start,
                data_end=period.data_end,
                source_type=period.source_type,
                progress_callback=progress,
                output_mode="ui_only",
                enable_deepseek=False,
                enable_external_context=False,
                metadata_enrichment_mode="safe_public",
                force_reclean=True,
                enqueue_background_analysis=False,
                preloaded_feishu_ledger=feishu_ledger,
            )
            status.update(label="数据清理结束", state="complete")
    except Exception as exc:
        st.warning(f"清洗未完成：{exc}")
        _render_user_recovery_hint("请检查上传文件格式、飞书台账和字段映射后重试。", exc)
        return
    st.success("清洗完成，本周期数据已更新。")
    _render_feishu_staleness_summary(result.feishu_staleness)
    if result.core_recap_xlsx:
        st.caption("已生成本周期核验结果。")
    if auto_tier1_recap:
        st.caption("已完成的清洗入库结果不会被复盘失败回滚；一级复盘可失败后在高价值内容复盘页重试。")
        _run_recap_tier_pipeline(
            result.batch_id,
            result.canonical,
            RECAP_TIER_1_SPEND_TOP,
            auto_trigger=True,
        )


def _run_recap_tier_pipeline(
    batch_id: str,
    items: pd.DataFrame,
    tier_key: str,
    *,
    auto_trigger: bool = False,
) -> bool:
    label = RECAP_TIER_LABELS.get(tier_key, tier_key)
    definition = RECAP_TIER_DEFINITIONS.get(tier_key, "")
    settings = get_recap_settings(APP_DB)
    tier_pool = _top_pool_with_value(
        build_recap_tier_pool(items, tier_key),
        activation_weight=settings.activation_weight,
        first_pay_weight=settings.first_pay_weight,
    )
    if tier_pool.empty:
        st.warning(f"{label}没有可执行素材，未启动补采或报告生成。")
        return False
    copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
    trigger = "upload_auto_tier1" if auto_trigger else f"manual_{tier_key}"
    purpose = _recap_tier_analysis_purpose(tier_key)
    progress_placeholder = st.empty()
    progress_started_at = time.monotonic()

    def progress(event) -> None:
        total = int(getattr(event, "total", 0) or 0)
        completed = int(getattr(event, "completed", 0) or 0)
        text = _harvester_progress_text(event, progress_started_at)
        if total:
            progress_placeholder.progress(min(completed / total, 1.0), text=text)
        else:
            progress_placeholder.info(text)

    try:
        with st.status(f"正在处理{label}", expanded=True) as status:
            status.write(f"范围：{definition}")
            status.write("正在复用已有素材缓存并补采缺失素材。")
            status.write("补采在后台静默运行；只有遇到登录失效或风控验证时才需要人工处理。")
            capture_result = run_harvester_asset_capture(APP_DB, batch_id, tier_pool, progress_callback=progress)
            if capture_result.job_count:
                status.write(f"补采任务 {capture_result.job_count} 个，成功 {capture_result.succeeded_count} 个，失败 {capture_result.failed_count} 个。")
            else:
                status.write("没有新增素材需要补采，直接进入多模态分析。")
            partial_capture = (not capture_result.ok) or int(capture_result.failed_count or 0) > 0
            if partial_capture:
                status.write("补采未全部完成；继续分析已有素材证据，并将报告标记为部分完成。")
            status.write("正在执行多模态素材分析。")
            reset_top_multimodal_jobs(
                APP_DB,
                batch_id,
                tier_pool,
                trigger=trigger,
                analysis_purpose=purpose,
            )
            copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
            updated_jobs = run_top_multimodal_analysis_from_manifests(
                APP_DB,
                batch_id,
                analyzer=lambda job, manifest: analyze_top_content_with_minimax(job, manifest, env_path=ENV_PATH),
                analysis_purpose=purpose,
            )
            persisted = persist_multimodal_recap(
                APP_DB,
                batch_id,
                tier_pool,
                analysis_purpose=purpose,
                analyzer=_analysis_job_result_analyzer(batch_id, purpose),
            )
            status.write(f"多模态完成 {updated_jobs} 个任务，写入 {persisted.item_count} 条素材复盘和 {persisted.strategy_count} 条策略聚合。")
            successful_identities = _successful_analysis_identities(batch_id, purpose)
            report_pool = _filter_pool_by_identities(tier_pool, successful_identities) if successful_identities else tier_pool.iloc[0:0].copy()
            partial_analysis = len(report_pool) < len(tier_pool)
            execution_status = "partial" if partial_capture or partial_analysis else "complete"
            if report_pool.empty:
                status.update(label=f"{label}复盘未完成", state="error")
                st.warning(f"{label}没有完成可用于 LLM 报告的素材分析；请稍后重试缺失素材。")
                progress_placeholder.empty()
                return False
            status.write("正在生成该范围的 LLM 复盘报告。")
            report = generate_range_recap_report(
                batch_id=batch_id,
                range_key=tier_key,
                range_label=label,
                range_definition=definition,
                top_pool=report_pool,
                period_totals=list_period_channel_totals(APP_DB, batch_id=batch_id),
                period_level=_batch_period_level(read_batch_record(APP_DB, batch_id)),
                env_path=ENV_PATH,
            )
            report["range_execution_status"] = execution_status
            report["range_total_items"] = int(len(tier_pool))
            report["range_report_items"] = int(len(report_pool))
            report["capture_failed_count"] = int(capture_result.failed_count or 0)
            persist_range_recap_report(
                APP_DB,
                batch_id,
                tier_key,
                provider="llm",
                model="manual-recap-report",
                report=report,
            )
            status.update(label=f"{label}复盘已生成", state="complete")
        progress_placeholder.empty()
        if execution_status == "complete":
            st.success(f"{label}已完成：素材分析和 LLM 报告已更新。")
            return True
        st.warning(
            f"{label}部分完成：已基于 {len(report_pool)}/{len(tier_pool)} 条完成分析的素材生成 LLM 报告；"
            "缺失素材可稍后重试后更新报告。"
        )
        return False
    except Exception as exc:
        progress_placeholder.empty()
        st.warning(f"{label}复盘未完成：{exc}。清洗入库结果不受影响，可稍后在高价值内容复盘页重试。")
        _render_user_recovery_hint("请检查素材采集项目登录状态、素材缓存和 MiniMax 配置后重试。", exc)
        return False


def _run_rollup(period, component_batch_ids: list[str]) -> None:
    with st.status("正在生成汇总数据", expanded=True) as status:
        result = run_rollup_workflow(
            APP_DB,
            component_batch_ids,
            period,
            output_root=APP_OUTPUTS,
            processed_root=APP_PROCESSED,
            category_rules_path=CATEGORY_RULES,
            env_path=ENV_PATH,
            output_mode="ui_only",
            enable_deepseek=False,
            enable_external_context=False,
        )
        status.update(label="汇总数据已生成", state="complete")
    st.success("汇总完成，对应周期数据已更新。")


def _sync_feishu_ledger_to_local(
    batch_id: str,
    *,
    ledger: pd.DataFrame | None = None,
    confirm_stale: bool = True,
) -> None:
    copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
    with st.status("正在读取线上飞书台账", expanded=True) as status:
        ledger = ledger if ledger is not None else load_feishu_content_ledger(env_path=ENV_PATH)
        staleness = _ledger_staleness(ledger)
        if _feishu_staleness_needs_confirmation(staleness) and not confirm_stale:
            st.session_state["pending_manual_feishu_ledger"] = ledger
            status.update(label="飞书台账需要人工确认", state="error")
            _render_feishu_staleness_prompt(
                staleness,
                action_label="需要人工确认飞书台账后，才能更新本地总表。",
            )
            return
        diff = build_feishu_content_asset_diff(APP_DB, batch_id, ledger)
        written = upsert_content_assets_from_feishu(APP_DB, batch_id, ledger)
        snapshot = ledger.attrs.get("feishu_snapshot")
        if isinstance(snapshot, dict):
            persist_feishu_ledger_snapshot(APP_DB, batch_id, snapshot)
        status.update(label="本地总表已更新", state="complete")
    st.session_state["feishu_sync_diff"] = diff
    if diff.summary.get("新增", 0) or diff.summary.get("修改", 0):
        st.success(
            f"已读取 {written} 条飞书记录：新增 {diff.summary.get('新增', 0)} 条，"
            f"修改 {diff.summary.get('修改', 0)} 条，无变化 {diff.summary.get('无变化', 0)} 条。"
        )
    else:
        st.warning("本次飞书没有更新内容，请先在飞书补充或修正后再同步。")
    _render_feishu_staleness_summary(_ledger_staleness(ledger))


def _load_feishu_ledger_for_check() -> pd.DataFrame:
    copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
    with st.status("正在读取线上飞书台账并检查更新状态", expanded=True) as status:
        ledger = load_feishu_content_ledger(env_path=ENV_PATH)
        staleness = _ledger_staleness(ledger)
        if _feishu_staleness_needs_confirmation(staleness):
            status.update(label="飞书台账需要人工确认", state="error")
        else:
            status.update(label="飞书台账更新状态正常", state="complete")
    _render_feishu_staleness_summary(_ledger_staleness(ledger))
    return ledger


def _ledger_staleness(ledger: pd.DataFrame | None) -> dict[str, object]:
    if ledger is None:
        return {}
    staleness = getattr(ledger, "attrs", {}).get("feishu_staleness")
    return staleness if isinstance(staleness, dict) else {}


def _feishu_staleness_needs_confirmation(staleness: dict[str, object] | None) -> bool:
    return bool(isinstance(staleness, dict) and staleness.get("needs_check"))


def _feishu_staleness_status_lines(staleness: dict[str, object] | None) -> list[str]:
    if not isinstance(staleness, dict):
        return []
    lines: list[str] = []
    for item in staleness.get("items") or []:
        if not isinstance(item, dict):
            continue
        platform = _text(item.get("platform"))
        latest = _text(item.get("latest_published_date"))
        days = item.get("days_since_latest")
        needs_check = bool(item.get("needs_check"))
        if not platform:
            continue
        if latest:
            if days is None:
                line = f"{platform}：{latest}"
            else:
                line = f"{platform}：{latest}，距今天 {int(days)} 天"
        else:
            line = f"{platform}：无有效投稿时间"
        if needs_check:
            line = f"{line}，需要检查"
        lines.append(line)
    return lines


def _render_feishu_staleness_summary(staleness: dict[str, object] | None) -> None:
    lines = _feishu_staleness_status_lines(staleness)
    if not lines:
        return
    if _feishu_staleness_needs_confirmation(staleness):
        st.warning("飞书台账更新状态需要人工确认检查：\n\n" + "\n".join(f"- {line}" for line in lines))
    else:
        st.caption("飞书台账更新状态：" + "；".join(lines))


def _render_feishu_staleness_prompt(staleness: dict[str, object] | None, *, action_label: str) -> None:
    st.warning("飞书台账超过 3 天未更新或缺少有效投稿时间，需要人工确认后继续。")
    _render_feishu_staleness_summary(staleness)
    st.info(action_label)


def _render_feishu_sync_diff() -> None:
    diff = st.session_state.get("feishu_sync_diff")
    if diff is None:
        return
    summary = getattr(diff, "summary", {}) or {}
    st.caption(
        f"最近一次飞书同步：新增 {int(summary.get('新增', 0))} 条 / "
        f"修改 {int(summary.get('修改', 0))} 条 / 无变化 {int(summary.get('无变化', 0))} 条"
    )
    added = getattr(diff, "added", pd.DataFrame())
    changed = getattr(diff, "changed", pd.DataFrame())
    if int(summary.get("新增", 0)) == 0 and int(summary.get("修改", 0)) == 0:
        st.warning("本次飞书没有更新内容，请先在飞书补充或修正后再同步。")
        return
    if added is not None and not added.empty:
        with st.expander("查看飞书新增内容", expanded=True):
            _show_frame(_feishu_added_display(added), height=260)
    if changed is not None and not changed.empty:
        with st.expander("查看飞书修改内容", expanded=True):
            _show_frame(_feishu_changed_display(changed), height=260)


def _display_batch_id(batch_id: object) -> str:
    text = _text(batch_id)
    return text or "当前周期"


def _feishu_added_display(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "platform",
        "title",
        "content_id",
        "content_url",
        "category_l1",
        "category_l2",
        "source_sheet",
        "source_row",
    ]
    display = _select_display_columns(frame, columns)
    return localize_columns(display)


def _feishu_changed_display(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["platform", "content_id", "content_url", "title", "field", "old_value", "new_value"]
    display = _select_display_columns(frame, columns)
    return localize_columns(
        display.rename(
            columns={
                "field": "修改字段",
                "old_value": "旧值",
                "new_value": "新值",
            }
        )
    )


def _select_batch(key_prefix: str, *, allow_empty: bool = False) -> str:
    return _select_period_batch(key_prefix, allow_empty=allow_empty)


def _select_period_batch(key_prefix: str, *, allow_empty: bool = False) -> str:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return ""
    batches = batches.copy()
    if "period_level" not in batches.columns:
        batches["period_level"] = PERIOD_LEVEL_WEEK
    level_labels = _overview_period_level_options(batches)
    if not level_labels:
        return ""
    c1, c2 = st.columns([0.8, 1.8])
    selected_label = c1.segmented_control(
        "周期类型",
        level_labels,
        default=level_labels[0],
        key=f"{key_prefix}_period_level_selector",
    )
    selected_level = _period_level_from_label(selected_label)
    options = _batch_options_for_level(batches, selected_level)
    if allow_empty:
        options = [("全部周期", "")] + options
    if not options:
        c2.selectbox("具体周期", ["暂无该类型周期"], disabled=True, key=f"{key_prefix}_batch_selector")
        return ""
    labels = [label for label, _ in options]
    selected = c2.selectbox("具体周期", labels, key=f"{key_prefix}_batch_selector")
    return dict(options).get(str(selected), "")


def _select_overview_batch(key_prefix: str) -> str:
    return _select_period_batch(key_prefix)


def _overview_period_level_options(batches: pd.DataFrame) -> list[str]:
    return [PERIOD_LEVEL_LABELS[level] for level in PERIOD_LEVELS]


def _batch_options_for_level(batches: pd.DataFrame, period_level: str) -> list[tuple[str, str]]:
    if batches is None or batches.empty:
        return []
    if "period_level" not in batches.columns:
        return []
    frame = batches[batches["period_level"].astype(str).str.strip().eq(str(period_level))].copy()
    if frame.empty:
        return []
    return [(_batch_option_label(row), str(row.get("batch_id", ""))) for _, row in frame.iterrows()]


def _latest_batch_id() -> str:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return ""
    return str(batches.iloc[0]["batch_id"])


def _batch_option_label(row: pd.Series) -> str:
    return _batch_period_value_label(row)


def _batch_period_value_label(row: pd.Series) -> str:
    level = str(row.get("period_level", "") or "").strip()
    key = str(row.get("period_key", "") or "").strip()
    start = str(row.get("period_start", "") or "").replace("-", "")
    end = str(row.get("period_end", "") or "").replace("-", "")
    if level == PERIOD_LEVEL_WEEK:
        compact = key.replace("-", "") if key else f"{start}-{end}"
        if len(compact) == 16 and "-" not in compact:
            compact = f"{compact[:8]}-{compact[8:]}"
        return compact
    if level == PERIOD_LEVEL_MONTH:
        compact = key.replace("-", "") if key else start[:6]
        return compact
    if level == PERIOD_LEVEL_QUARTER:
        compact = key.replace("-", "")
        return compact
    if level == PERIOD_LEVEL_YEAR:
        compact = key or start[:4]
        return compact
    label = str(row.get("period_label", "") or row.get("period_key", "") or row.get("batch_id", ""))
    return label


def _batch_period_level(record: pd.Series | dict[str, object]) -> str:
    level = _text(record.get("period_level"))
    return level or PERIOD_LEVEL_WEEK


def _batch_caption(record: dict[str, str]) -> str:
    source_type = "上传" if record.get("source_type") == "upload" else "本地汇总"
    label = _batch_option_label(pd.Series(record))
    data_start = str(record.get("data_start", "") or "")
    data_end = str(record.get("data_end", "") or "")
    period_start = str(record.get("period_start", "") or "")
    period_end = str(record.get("period_end", "") or "")
    data_suffix = ""
    if data_start and data_end and (data_start != period_start or data_end != period_end):
        data_suffix = f"｜数据时间：{data_start} 至 {data_end}"
    return f"{label}｜{source_type}{data_suffix}"


def _raw_dir_for_period(period) -> Path:
    if period.period_level in {PERIOD_LEVEL_WEEK, PERIOD_LEVEL_MONTH}:
        return source_dir_for_period(APP_DATA_ROOT, period)
    if period.period_level == PERIOD_LEVEL_QUARTER:
        return APP_DATA_ROOT / "quarters" / source_storage_key(period)
    if period.period_level == PERIOD_LEVEL_YEAR:
        return APP_DATA_ROOT / "years" / source_storage_key(period)
    return APP_DATA_ROOT / "periods" / source_storage_key(period)


def _period_level_from_label(label: object) -> str:
    reverse = {value: key for key, value in PERIOD_LEVEL_LABELS.items()}
    return reverse.get(str(label), PERIOD_LEVEL_WEEK)


def _previous_batch_id_for_record(record: dict[str, str]) -> str:
    return (
        previous_successful_batch_id_for_period(
            APP_DB,
            str(record.get("period_start", "") or ""),
            str(record.get("period_level", "") or ""),
            str(record.get("period_key", "") or ""),
        )
        or ""
    )


def _comparison_caption(current_record: dict[str, str], previous_record: dict[str, str]) -> str:
    current_label = _batch_option_label(pd.Series(current_record)) if current_record else "当前周期"
    if not previous_record:
        return "环比：暂无上一周期。"
    previous_label = _batch_option_label(pd.Series(previous_record))
    return f"环比：本周期 {current_label}，对比周期 {previous_label}。"


def _rollup_components_display(component_batch_ids: list[str]) -> pd.DataFrame:
    rows = []
    for index, batch_id in enumerate(component_batch_ids, start=1):
        record = read_batch_record(APP_DB, batch_id)
        label = _batch_period_value_label(pd.Series(record)) if record else ""
        rows.append({"周期": label or f"可用周期 {index}"})
    return pd.DataFrame(rows, columns=["周期"])


def _overview_items_for_batch(batch_id: str) -> pd.DataFrame:
    if not batch_id:
        return pd.DataFrame()
    items = list_content_performance_items(APP_DB, batch_id=batch_id)
    if not items.empty:
        return items
    return load_dashboard_items_for_batch(APP_DB, batch_id)


def _render_recap_weight_settings(settings, *, key_prefix: str) -> None:
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 1, 1])
        activation_weight = c1.number_input(
            "激活权重",
            value=float(settings.activation_weight),
            min_value=0.0,
            step=0.1,
            key=f"{key_prefix}_activation_weight",
        )
        first_pay_weight = c2.number_input(
            "付费权重",
            value=float(settings.first_pay_weight),
            min_value=0.0,
            step=0.1,
            key=f"{key_prefix}_first_pay_weight",
        )
        c3.markdown('<div class="weight-button-spacer"></div>', unsafe_allow_html=True)
        if c3.button("保存权重", width="stretch", key=f"{key_prefix}_save_weights"):
            update_recap_settings(
                APP_DB,
                activation_weight=float(activation_weight),
                first_pay_weight=float(first_pay_weight),
            )
            st.success("权重已保存。")
            st.rerun()
        if settings.updated_at:
            st.caption(_recap_weight_updated_at_caption(settings.updated_at))


def _recap_weight_updated_at_caption(updated_at: object) -> str:
    return f"当前默认权重更新时间：{format_beijing_datetime(updated_at)}"


def _overview_metrics(
    totals: pd.DataFrame,
    items: pd.DataFrame,
    *,
    activation_weight: float = 1.0,
    first_pay_weight: float = 1.0,
) -> dict[str, float]:
    source = _total_metric_source_frame(totals, items)
    spend = _sum(source, "spend")
    impressions = _sum(source, "impressions")
    activations = _sum(source, "activations")
    first_pay = _sum(source, "first_pay_count")
    return {
        "消耗": spend,
        "曝光": impressions,
        "激活数": activations,
        "付费数": first_pay,
        "激活成本": spend / activations if activations else 0.0,
        "付费成本": spend / first_pay if first_pay else 0.0,
        "价值": activations * float(activation_weight) + first_pay * float(first_pay_weight),
    }


def _overview_status_metrics(
    items: pd.DataFrame,
    totals: pd.DataFrame,
    manifests: pd.DataFrame,
    recap_items: pd.DataFrame,
) -> dict[str, int]:
    return {
        "素材明细": len(items) if items is not None else 0,
        "覆盖渠道": _covered_channel_count(items, totals),
        "已缓存素材": _succeeded_manifest_count(manifests),
        "类型复盘": len(recap_items) if recap_items is not None else 0,
    }


def _render_metric_row(metrics: dict[str, float], previous_metrics: dict[str, float] | None = None) -> None:
    for chunk in _metric_row_chunks(metrics):
        columns = st.columns(len(chunk))
        for column, (label, value) in zip(columns, chunk):
            decimals = 2 if ("成本" in label or label == "价值") else 0
            previous_value = previous_metrics.get(label) if previous_metrics else None
            column.metric(
                label,
                format_display_number(value, decimals),
                delta=_metric_delta_text(value, previous_value),
                delta_color=_metric_delta_color(label, value, previous_value),
            )


def _metric_row_chunks(metrics: dict[str, float], *, max_columns: int = 4) -> list[list[tuple[str, float]]]:
    items = list(metrics.items())
    return [items[start : start + max_columns] for start in range(0, len(items), max_columns)]


def _metric_delta_text(current: object, previous: object) -> str:
    if previous is None:
        return "暂无上一周期"
    current_value = _number(current)
    previous_value = _number(previous)
    if current_value == previous_value:
        return ""
    if previous_value == 0:
        return "上一周期为 0"
    change = (current_value - previous_value) / previous_value
    return f"{change:+.1%}"


def _metric_delta_color(label: str, current: object, previous: object) -> str:
    if previous is None:
        return "off"
    current_value = _number(current)
    previous_value = _number(previous)
    if current_value == previous_value:
        return "off"
    return "normal" if "成本" in label else "inverse"


def _metric_source_frame(totals: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    explicit_totals = totals.copy() if totals is not None else pd.DataFrame()
    if not explicit_totals.empty and "is_channel_total" in explicit_totals.columns:
        mask = explicit_totals["is_channel_total"].astype(str).str.lower().isin({"1", "true", "yes"})
        explicit_totals = explicit_totals[mask]
    detail = items.copy() if items is not None else pd.DataFrame()
    if explicit_totals.empty:
        return detail
    if detail.empty or "channel" not in detail.columns or "channel" not in explicit_totals.columns:
        return explicit_totals
    total_channels = {
        _text(value)
        for value in explicit_totals["channel"].tolist()
        if _text(value) and _text(value) != "总计"
    }
    if not total_channels:
        return explicit_totals
    detail = detail.copy()
    detail["channel"] = detail["channel"].map(_text)
    missing_detail = detail[~detail["channel"].isin(total_channels) & detail["channel"].ne("")]
    if missing_detail.empty:
        return explicit_totals
    return pd.concat([explicit_totals, missing_detail], ignore_index=True, sort=False)


def _total_metric_source_frame(totals: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    source = _metric_source_frame(totals, items)
    if source.empty or "channel" not in source.columns:
        return source
    total_rows = source[source["channel"].astype(str).str.strip().eq("总计")]
    return total_rows if not total_rows.empty else _without_total_rows(source)


def _channel_totals_for_display(
    totals: pd.DataFrame,
    items: pd.DataFrame,
    *,
    activation_weight: float = 1.0,
    first_pay_weight: float = 1.0,
    previous_totals: pd.DataFrame | None = None,
    previous_items: pd.DataFrame | None = None,
) -> pd.DataFrame:
    empty_columns = ["channel", "spend", "impressions", "activations", "first_pay_count", "activation_cost", "first_pay_cost", "value"]
    source = _without_total_rows(_metric_source_frame(totals, items))
    if source.empty:
        return pd.DataFrame(columns=empty_columns)
    grouped = _summarize_channel_totals_for_display(
        source,
        activation_weight=activation_weight,
        first_pay_weight=first_pay_weight,
        empty_columns=empty_columns,
    )
    if previous_totals is not None or previous_items is not None:
        previous_source = _without_total_rows(
            _metric_source_frame(
                previous_totals if previous_totals is not None else pd.DataFrame(),
                previous_items if previous_items is not None else pd.DataFrame(),
            )
        )
        previous_grouped = _summarize_channel_totals_for_display(
            previous_source,
            activation_weight=activation_weight,
            first_pay_weight=first_pay_weight,
            empty_columns=empty_columns,
        )
        grouped.attrs["metric_deltas"] = _channel_metric_delta_metadata(grouped, previous_grouped)
    return grouped


def _summarize_channel_totals_for_display(
    source: pd.DataFrame,
    *,
    activation_weight: float,
    first_pay_weight: float,
    empty_columns: list[str],
) -> pd.DataFrame:
    if source.empty:
        return pd.DataFrame(columns=empty_columns)
    frame = source.copy()
    for column in ["channel", *NUMERIC_COLUMNS]:
        if column not in frame.columns:
            frame[column] = 0 if column in NUMERIC_COLUMNS else ""
    frame["channel"] = frame["channel"].map(_text)
    frame = frame[frame["channel"].ne("")].copy()
    if frame.empty:
        return pd.DataFrame(columns=empty_columns)
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    grouped = (
        frame.groupby("channel", dropna=False)
        .agg(
            spend=("spend", _sum_series),
            impressions=("impressions", _sum_series),
            activations=("activations", _sum_series),
            first_pay_count=("first_pay_count", _sum_series),
        )
        .reset_index()
    )
    grouped["activation_cost"] = grouped.apply(lambda row: _safe_ratio(row["spend"], row["activations"]), axis=1)
    grouped["first_pay_cost"] = grouped.apply(lambda row: _safe_ratio(row["spend"], row["first_pay_count"]), axis=1)
    grouped["value"] = (
        grouped["activations"].astype(float) * float(activation_weight)
        + grouped["first_pay_count"].astype(float) * float(first_pay_weight)
    )
    return grouped.sort_values("spend", ascending=False).reset_index(drop=True)


CHANNEL_TOTAL_DISPLAY_COLUMNS = [
    ("channel", "渠道", 0),
    ("spend", "消耗", 2),
    ("impressions", "曝光量", 0),
    ("activations", "激活数", 0),
    ("first_pay_count", "付费数", 0),
    ("activation_cost", "激活成本", 2),
    ("first_pay_cost", "付费成本", 2),
    ("value", "价值", 0),
]


def _channel_metric_delta_metadata(current: pd.DataFrame, previous: pd.DataFrame) -> dict[str, dict[str, dict[str, str]]]:
    if current.empty or previous.empty or "channel" not in current.columns or "channel" not in previous.columns:
        return {}
    previous_by_channel = previous.set_index("channel", drop=False)
    result: dict[str, dict[str, dict[str, str]]] = {}
    for _, current_row in current.iterrows():
        channel = _text(current_row.get("channel"))
        if not channel or channel not in previous_by_channel.index:
            continue
        previous_row = previous_by_channel.loc[channel]
        if isinstance(previous_row, pd.DataFrame):
            previous_row = previous_row.iloc[0]
        channel_deltas: dict[str, dict[str, str]] = {}
        for metric, label, _decimals in CHANNEL_TOTAL_DISPLAY_COLUMNS:
            if metric == "channel":
                continue
            delta_text = _metric_delta_text(current_row.get(metric), previous_row.get(metric))
            if not delta_text.startswith(("+", "-")):
                continue
            delta_class = _channel_delta_css_class(label, current_row.get(metric), previous_row.get(metric))
            if not delta_class:
                continue
            channel_deltas[metric] = {"text": delta_text, "class": delta_class}
        result[channel] = channel_deltas
    return result


def _channel_delta_css_class(label: str, current: object, previous: object) -> str:
    delta_color = _metric_delta_color(label, current, previous)
    if delta_color == "inverse":
        return "channel-delta-good" if _number(current) > _number(previous) else "channel-delta-bad"
    if delta_color == "normal":
        return "channel-delta-good" if _number(current) < _number(previous) else "channel-delta-bad"
    return ""


def _render_channel_totals_table(frame: pd.DataFrame) -> None:
    if frame is None or frame.empty:
        st.info("暂无数据。")
        return
    st.markdown(_channel_totals_table_html(frame), unsafe_allow_html=True)


def _channel_totals_table_html(frame: pd.DataFrame) -> str:
    headers = "".join(f"<th>{_safe_html(label)}</th>" for _column, label, _decimals in CHANNEL_TOTAL_DISPLAY_COLUMNS)
    delta_metadata = frame.attrs.get("metric_deltas", {}) if hasattr(frame, "attrs") else {}
    rows = []
    for _, row in frame.iterrows():
        channel = _text(row.get("channel"))
        cells = []
        for column, _label, decimals in CHANNEL_TOTAL_DISPLAY_COLUMNS:
            if column == "channel":
                cells.append(f"<td>{_safe_html(channel)}</td>")
                continue
            value = format_display_number(row.get(column), decimals)
            delta = delta_metadata.get(channel, {}).get(column, {})
            delta_html = ""
            if delta.get("text") and delta.get("class"):
                delta_html = (
                    f'<span class="channel-delta {_safe_html(delta.get("class"))}">'
                    f'（{_safe_html(delta.get("text"))}）'
                    "</span>"
                )
            cells.append(f'<td><span class="channel-value">{_safe_html(value)}</span>{delta_html}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<div class="channel-overview-table-wrap">'
        f'<table class="channel-overview-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
        "</div>"
    )


def _without_total_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "channel" not in frame.columns:
        return frame
    return frame[~frame["channel"].astype(str).str.strip().eq("总计")].copy()


def _top_pool_display(top_pool: pd.DataFrame) -> pd.DataFrame:
    if top_pool.empty:
        return top_pool
    columns = [
        "channel",
        "content_id",
        "title",
        "account",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "work_url",
        "content_url",
        "ad_material_url",
        "ad_cover_url",
        "spend",
        "impressions",
        "activations",
        "first_pay_count",
        "activation_cost",
        "first_pay_cost",
        "value",
        "high_spend_reason",
    ]
    return _display_value_columns(_platform_type_display_columns(top_pool[[column for column in columns if column in top_pool.columns]].copy()))


def _content_performance_display(items: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "period_start",
        "period_end",
        "platform",
        "channel",
        "content_id",
        "account",
        "title",
        "tags",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "work_url",
        "content_url",
        "ad_material_url",
        "ad_cover_url",
        "spend",
        "impressions",
        "activations",
        "first_pay_count",
        "activation_cost",
        "first_pay_cost",
        "value",
        "share",
    ]
    display = _platform_type_display_columns(_select_display_columns(items, columns))
    return _coerce_display_numeric_columns(_clean_display_title_tags(display))


def _local_content_assets_display(assets: pd.DataFrame) -> pd.DataFrame:
    display = _dedupe_local_content_assets(assets)
    if display is not None and not display.empty:
        display = display.copy()
        if "work_url" not in display.columns:
            display["work_url"] = ""
        if "content_url" in display.columns:
            display["work_url"] = display.apply(
                lambda row: _text(row.get("work_url")) or _text(row.get("content_url")),
                axis=1,
            )
    columns = [
        "platform",
        "content_id",
        "work_url",
        "account",
        "title",
        "tags",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "published_date",
        "updated_at",
    ]
    return _format_display_time_columns(_platform_type_display_columns(_select_display_columns(display, columns)))


def _dedupe_local_content_assets(assets: pd.DataFrame) -> pd.DataFrame:
    if assets is None or assets.empty:
        return assets
    display = assets.copy()
    for column in ["asset_key", "platform", "content_id", "content_url", "title", "updated_at"]:
        if column not in display.columns:
            display[column] = ""
    display["_dedupe_key"] = display.apply(_local_asset_dedupe_key, axis=1)
    display["_updated_sort"] = pd.to_datetime(display["updated_at"], errors="coerce")
    display = (
        display.sort_values(["_dedupe_key", "_updated_sort", "updated_at"], ascending=[True, False, False])
        .drop_duplicates(subset=["_dedupe_key"], keep="first")
        .drop(columns=["_dedupe_key", "_updated_sort"], errors="ignore")
    )
    return display.reset_index(drop=True)


def _local_asset_dedupe_key(row: pd.Series) -> str:
    asset_key = _text(row.get("asset_key"))
    if asset_key:
        return asset_key
    platform = _text(row.get("platform"))
    for column in ["content_id", "content_url", "title"]:
        value = _text(row.get(column))
        if value:
            return f"{platform}::{column}::{value}"
    return f"{platform}::row::{row.name}"


def _multimodal_recap_display(items: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "analysis_purpose",
        "evidence_source",
        "classification_write_status",
        "classification_write_reason",
        "platform",
        "channel",
        "content_id",
        "account",
        "title",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "content_form",
        "title_hook",
        "visual_structure",
        "information_density",
        "conversion_path",
        "reuse_points",
        "avoid_points",
        "next_period_strategy",
        "summary",
        "updated_at",
    ]
    return _clean_display_title_tags(_format_display_time_columns(_platform_type_display_columns(_select_display_columns(items, columns))))


def _strategy_recap_display(items: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "analysis_purpose",
        "channel",
        "platform",
        "type_level",
        "content_type",
        "item_count",
        "metrics",
        "common_patterns",
        "reuse_points",
        "avoid_points",
        "next_period_actions",
        "supporting_content_identity_keys",
        "updated_at",
    ]
    return _format_display_time_columns(_select_display_columns(items, columns))


def _asset_cache_jobs_display(jobs: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "status",
        "platform",
        "channel",
        "content_id",
        "title",
        "content_url",
        "spend",
        "impressions",
        "activations",
        "first_pay_count",
        "error_message",
        "updated_at",
    ]
    return _localize_display_values(_select_display_columns(jobs, columns))


def _asset_cache_records_display(manifests: pd.DataFrame) -> pd.DataFrame:
    if manifests is None or manifests.empty:
        return pd.DataFrame(columns=["status", "platform", "asset_source", "has_cover", "has_video", "error_message", "updated_at"])
    display = manifests.copy()
    for column in ["cover_path", "video_path", "metadata_json"]:
        if column not in display.columns:
            display[column] = ""
    display["asset_source"] = display["metadata_json"].map(_asset_source_label)
    display["has_cover"] = display["cover_path"].map(lambda value: "有" if _text(value) else "无")
    display["has_video"] = display["video_path"].map(lambda value: "有" if _text(value) else "无")
    columns = ["status", "platform", "asset_source", "has_cover", "has_video", "error_message", "updated_at"]
    return _localize_display_values(_select_display_columns(display, columns))


def _harvester_asset_jobs_display(jobs: pd.DataFrame) -> pd.DataFrame:
    return _asset_cache_jobs_display(jobs)


def _harvester_asset_manifests_display(manifests: pd.DataFrame) -> pd.DataFrame:
    return _asset_cache_records_display(manifests)


def _manifests_with_job_context(manifests: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if manifests is None or manifests.empty or jobs is None or jobs.empty:
        return manifests
    if "job_id" not in manifests.columns or "job_id" not in jobs.columns:
        return manifests
    job_columns = [column for column in ["job_id", "channel", "content_identity_key", "content_id", "content_url"] if column in jobs.columns]
    if len(job_columns) <= 1:
        return manifests
    context = jobs[job_columns].drop_duplicates("job_id")
    return manifests.merge(context, on="job_id", how="left", suffixes=("", "_job"))


def _analysis_jobs_display(jobs: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "status",
        "trigger",
        "analysis_purpose",
        "platform",
        "channel",
        "title",
        "content_url",
        "attempts",
        "max_attempts",
        "error_message",
        "updated_at",
    ]
    return _localize_display_values(_select_display_columns(jobs, columns))


def _missing_type_pool(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    prepared = frame.copy()
    for column in ["platform", "platform_group", "category_l1", "category_l2", "bilibili_content_type", "content_type"]:
        if column not in prepared.columns:
            prepared[column] = ""
        prepared[column] = prepared[column].fillna("").astype(str).str.strip()
    mask = prepared.apply(_row_missing_platform_type, axis=1)
    return prepared[mask].copy().reset_index(drop=True)


def _row_missing_platform_type(row: pd.Series) -> bool:
    platform = _platform_from_row(row)
    if platform in {"抖音", "小红书"}:
        classification = normalize_platform_classification(
            platform,
            category_l1=row.get("category_l1"),
            category_l2=row.get("category_l2"),
        )
        if not classification.primary_valid:
            return True
        allowed = DOUYIN_TAXONOMY.get(classification.primary_type) if platform == "抖音" else XHS_TAXONOMY.get(classification.primary_type)
        return bool(allowed) and not classification.secondary_valid
    if platform == "B站":
        classification = normalize_platform_classification(
            platform,
            bilibili_content_type=row.get("bilibili_content_type"),
            content_type=row.get("content_type"),
        )
        return not classification.bilibili_valid
    return False


def _asset_cache_status_summary(
    top_pool: pd.DataFrame,
    capture_pool: pd.DataFrame,
    manifests: pd.DataFrame,
    jobs: pd.DataFrame,
) -> dict[str, object]:
    succeeded_manifests = _succeeded_manifest_count(manifests)
    failed_reasons = []
    for frame in [manifests, jobs]:
        if frame is None or frame.empty or "error_message" not in frame.columns:
            continue
        failed_reasons.extend(
            _text(value)
            for value in frame["error_message"].fillna("").tolist()
            if _text(value)
        )
    completed_jobs = 0
    if jobs is not None and not jobs.empty and "status" in jobs.columns:
        completed_jobs = int(jobs["status"].fillna("").astype(str).eq("succeeded").sum())
    capture_count = len(capture_pool) if capture_pool is not None else 0
    return {
        "高价值池": len(top_pool) if top_pool is not None else 0,
        "可复盘素材": capture_count,
        "已复用缓存": succeeded_manifests,
        "待补采": max(capture_count - succeeded_manifests, 0),
        "已完成多模态": completed_jobs,
        "失败原因": "；".join(dict.fromkeys(failed_reasons[:3])),
    }


def _render_asset_cache_status(summary: dict[str, object], cache_summary: dict[str, int]) -> None:
    with st.container(border=True):
        columns = st.columns(6)
        for column, label in zip(columns, ["高价值池", "可复盘素材", "已复用缓存", "待补采", "已完成多模态"]):
            column.metric(label, format_display_number(summary.get(label, 0), 0))
        columns[-1].metric("缓存占用", _format_bytes(cache_summary.get("size_bytes", 0)))
        failure = _text(summary.get("失败原因"))
        if failure:
            st.warning(f"失败原因：{failure}")


def _recap_tier_analysis_purpose(tier_key: str) -> str:
    return f"{ANALYSIS_PURPOSE_STRATEGY_RECAP}:{_text(tier_key)}"


def _recap_tier_status_summary(
    tier_key: str,
    tier_pool: pd.DataFrame,
    manifests: pd.DataFrame,
    jobs: pd.DataFrame,
    report_status: dict[str, object],
) -> dict[str, object]:
    purpose = _recap_tier_analysis_purpose(tier_key)
    tier_count = len(tier_pool) if tier_pool is not None else 0
    succeeded_manifests = _scoped_succeeded_manifest_count(manifests, tier_pool)
    scoped_jobs = jobs.copy() if jobs is not None else pd.DataFrame()
    if not scoped_jobs.empty and "analysis_purpose" in scoped_jobs.columns:
        scoped_jobs = scoped_jobs[scoped_jobs["analysis_purpose"].map(_text).eq(purpose)]
    completed_jobs = 0
    if not scoped_jobs.empty and "status" in scoped_jobs.columns:
        completed_jobs = int(scoped_jobs["status"].map(_text).eq("succeeded").sum())
    return {
        "范围素材": tier_count,
        "已缓存素材": succeeded_manifests,
        "已完成多模态": completed_jobs,
        "待分析": max(tier_count - completed_jobs, 0),
        "LLM报告": "已生成" if bool(report_status.get("has_report")) else "未生成",
    }


def _top_asset_cache_entries_display(entries: pd.DataFrame) -> pd.DataFrame:
    if entries is None or entries.empty:
        return localize_columns(
            pd.DataFrame(columns=["platform", "content_id", "asset_source", "cache_size", "ref_count", "last_used_batch_id", "updated_at"])
        )
    display = entries.copy()
    display["asset_source"] = display.get("source", pd.Series(dtype=object)).map(_asset_source_label)
    display["cache_size"] = display.get("size_bytes", pd.Series(dtype=object)).map(_format_bytes)
    columns = ["platform", "content_id", "asset_source", "cache_size", "ref_count", "last_used_batch_id", "updated_at"]
    return localize_columns(_localize_display_values(_select_display_columns(display, columns)))


def _select_display_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[column for column in columns if frame is None or column in getattr(frame, "columns", [])])
    return frame[[column for column in columns if column in frame.columns]].copy()


def _clean_display_title_tags(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "title" not in frame.columns:
        return frame
    display = frame.copy()
    display["title"] = display["title"].map(_title_without_hashtags)
    return display


def _coerce_display_numeric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    display = frame.copy()
    for column in ["spend", "impressions", "activations", "first_pay_count", "activation_cost", "first_pay_cost", "value", "share"]:
        if column in display.columns:
            display[column] = pd.to_numeric(display[column], errors="coerce").fillna(0.0)
    return display


def _title_without_hashtags(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    text = re.sub(r"(?<!\\w)[#＃][^#＃\\s]+", "", text)
    text = re.sub(r"\\s+", " ", text)
    return text.strip()


def _platform_type_display_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "platform" not in frame.columns:
        return frame
    display = frame.copy()
    for column in ["category_l1", "category_l2", "bilibili_content_type"]:
        if column not in display.columns:
            display[column] = ""
    platform = display["platform"].map(_text)
    bilibili_mask = platform.eq("B站")
    display.loc[bilibili_mask, ["category_l1", "category_l2"]] = ""
    display.loc[~bilibili_mask, "bilibili_content_type"] = ""
    return display


def _localize_display_values(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    localized = frame.copy()
    localized = _format_display_time_columns(localized)
    value_maps = {
        "status": {
            "succeeded": "已完成",
            "success": "已完成",
            "ok": "已完成",
            "failed": "失败",
            "error": "失败",
            "pending": "待处理",
            "queued": "排队中",
            "running": "处理中",
            "skipped": "已跳过",
        },
        "trigger": {
            "manual_recap": "手动复盘",
            "manual": "手动触发",
            "upload": "上传后生成",
            "scheduled": "定时任务",
            "retry": "重试",
        },
    }
    for column, mapping in value_maps.items():
        if column in localized.columns:
            localized[column] = localized[column].map(lambda value: mapping.get(_text(value), _text(value)))
    return localized


def _format_display_time_columns(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    for column in display.columns:
        if _is_time_display_column(column):
            display[column] = display[column].map(format_beijing_datetime)
    return display


def _is_time_display_column(column: object) -> bool:
    text = str(column)
    return text.endswith("_at") or text in {"created_at", "updated_at", "fetched_at", "metadata_fetched_at"}


def _top_pool_with_value(
    top_pool: pd.DataFrame,
    *,
    activation_weight: float = 1.0,
    first_pay_weight: float = 1.0,
) -> pd.DataFrame:
    if top_pool is None or top_pool.empty:
        return top_pool.copy() if top_pool is not None else pd.DataFrame()
    frame = top_pool.copy()
    for column in ["activations", "first_pay_count"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["value"] = frame["activations"] * float(activation_weight) + frame["first_pay_count"] * float(first_pay_weight)
    return frame


def _channel_top_link_card_rows(top_pool: pd.DataFrame, *, limit: int = 5) -> pd.DataFrame:
    if top_pool is None or top_pool.empty:
        return pd.DataFrame()
    frame = top_pool.copy()
    for column in ["channel", "content_id", "title", "account", "content_url"]:
        if column not in frame.columns:
            frame[column] = ""
    for column in ["spend", "impressions", "activations", "first_pay_count", "value"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame = frame.sort_values(["channel", "spend"], ascending=[True, False])
    selected = frame.groupby("channel", dropna=False, sort=False).head(limit).copy()
    return selected.sort_values("spend", ascending=False).reset_index(drop=True)


def _render_channel_top_link_cards(top_pool: pd.DataFrame, *, manifests: pd.DataFrame | None = None) -> None:
    cards = _channel_top_link_card_rows(top_pool, limit=5)
    if cards.empty:
        st.info("当前周期没有可展示的高价值素材。")
        return
    cover_lookup = _top_cover_lookup(manifests)
    for channel_index, (channel, group) in enumerate(cards.groupby("channel", sort=False, dropna=False)):
        channel_name = _text(channel) or "未命名渠道"
        with st.expander(
            f"{channel_name}｜前 {len(group)} 个高价值内容",
            expanded=channel_index == 0,
        ):
            for index, (_, row) in enumerate(group.iterrows(), start=1):
                st.markdown(
                    _channel_top_link_card_html(row, cover_lookup, rank=index),
                    unsafe_allow_html=True,
                )


def _display_manifests_with_reusable_cache(
    top_pool: pd.DataFrame,
    manifests: pd.DataFrame | None,
    cached_manifests: pd.DataFrame | None,
) -> pd.DataFrame:
    base = manifests.copy() if manifests is not None else pd.DataFrame()
    cache = cached_manifests.copy() if cached_manifests is not None else pd.DataFrame()
    if cache.empty:
        return base
    needed_keys = set(_asset_key_candidates_for_top_pool(top_pool))
    if not needed_keys:
        return base
    for column in ["asset_key", "status", "cover_path"]:
        if column not in cache.columns:
            cache[column] = ""
    cache = cache[
        cache["asset_key"].fillna("").astype(str).isin(needed_keys)
        & cache["status"].fillna("").astype(str).eq("succeeded")
        & cache["cover_path"].fillna("").astype(str).str.strip().ne("")
    ].copy()
    if cache.empty:
        return base
    if base.empty:
        return cache.reset_index(drop=True)
    if "asset_key" not in base.columns:
        base["asset_key"] = ""
    existing_keys = set(base["asset_key"].fillna("").astype(str))
    missing_cache = cache[~cache["asset_key"].fillna("").astype(str).isin(existing_keys)].copy()
    if missing_cache.empty:
        return base.reset_index(drop=True)
    return pd.concat([base, missing_cache], ignore_index=True, sort=False)


def _asset_key_candidates_for_top_pool(top_pool: pd.DataFrame) -> list[str]:
    if top_pool is None or top_pool.empty:
        return []
    candidates: list[str] = []
    for _, row in top_pool.iterrows():
        candidates.extend(_asset_key_candidates_for_row(row))
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _reusable_cache_manifests_for_top_pool(top_pool: pd.DataFrame, entries: pd.DataFrame | None) -> pd.DataFrame:
    if entries is None or entries.empty:
        return pd.DataFrame()
    needed_keys = set(_asset_key_candidates_for_top_pool(top_pool))
    if not needed_keys:
        return pd.DataFrame()
    frame = entries.copy()
    for column in ["asset_key", "asset_dir", "platform", "content_id"]:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[frame["asset_key"].fillna("").astype(str).isin(needed_keys)].copy()
    if frame.empty:
        return pd.DataFrame()
    manifests: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        manifest = _cached_manifest_from_entry(row)
        if manifest:
            manifests.append(manifest)
    return pd.DataFrame(manifests)


def _cached_manifest_from_entry(row: pd.Series) -> dict[str, object]:
    asset_key = _text(row.get("asset_key"))
    asset_dir = Path(_text(row.get("asset_dir"))).expanduser()
    if not asset_key or not asset_dir.exists() or not asset_dir.is_dir():
        return {}
    platform = _text(row.get("platform")) or _asset_key_platform(asset_key)
    fallback_cover_path = _first_local_image_in_dir(asset_dir, platform=platform)
    manifest = _read_cached_asset_manifest(asset_dir / "manifest.json", asset_key=asset_key)
    if not manifest:
        manifest = {
            "status": "succeeded",
            "platform": platform,
            "asset_key": asset_key,
            "asset_dir": str(asset_dir),
            "cover_path": fallback_cover_path,
            "video_path": "",
            "screenshots_json": "[]",
            "frames_json": "[]",
            "metadata_json": "{}",
            "error_message": "",
        }
    if _is_douyin_screenshot_only_manifest(manifest, asset_dir):
        return {}
    cover_path = _text(manifest.get("cover_path"))
    if not cover_path or not Path(cover_path).expanduser().is_file():
        cover_path = fallback_cover_path
    if not cover_path or not Path(cover_path).expanduser().is_file():
        return {}
    manifest["job_id"] = _text(manifest.get("job_id"))
    manifest["batch_id"] = _text(manifest.get("batch_id")) or "_reusable_cache"
    manifest["status"] = "succeeded"
    manifest["platform"] = _text(manifest.get("platform")) or platform
    manifest["asset_key"] = asset_key
    manifest["asset_dir"] = _text(manifest.get("asset_dir")) or str(asset_dir)
    manifest["cover_path"] = cover_path
    manifest["video_path"] = _text(manifest.get("video_path"))
    manifest["screenshots_json"] = _manifest_list_json(manifest.get("screenshots_json") or manifest.get("screenshots"))
    manifest["frames_json"] = _manifest_list_json(manifest.get("frames_json") or manifest.get("frames"))
    manifest["metadata_json"] = _manifest_mapping_json(manifest.get("metadata_json") or manifest.get("metadata"))
    manifest["error_message"] = ""
    return manifest


def _read_cached_asset_manifest(path: Path, *, asset_key: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if _text(item.get("asset_key")) != asset_key:
            continue
        if _text(item.get("status")) != "succeeded":
            continue
        return dict(item)
    return {}


def _first_local_image_in_dir(asset_dir: Path, *, platform: str = "") -> str:
    suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    image_paths = [path for path in sorted(asset_dir.rglob("*")) if path.is_file() and path.suffix.lower() in suffixes]
    if _text(platform) == "抖音":
        preferred = [
            path
            for path in image_paths
            if path.parent.name in {"images", "frames"} or path.parent == asset_dir
        ]
        image_paths = preferred
    for path in image_paths:
        if path.is_file() and path.suffix.lower() in suffixes:
            return str(path)
    return ""


def _asset_key_platform(asset_key: str) -> str:
    return _text(asset_key).split("::", 1)[0] if "::" in _text(asset_key) else ""


def _is_douyin_screenshot_only_manifest(manifest: dict[str, object], asset_dir: Path) -> bool:
    platform = _text(manifest.get("platform")) or _asset_key_platform(_text(manifest.get("asset_key")))
    if platform != "抖音":
        return False
    if _text(manifest.get("video_path")):
        return False
    if _manifest_list(manifest.get("frames_json") or manifest.get("frames")):
        return False
    cover_path = Path(_text(manifest.get("cover_path"))).expanduser()
    screenshots = [Path(path).expanduser() for path in _manifest_list(manifest.get("screenshots_json") or manifest.get("screenshots"))]
    if cover_path and str(cover_path) != "." and cover_path.parent.name not in {"screenshots", ""}:
        return False
    image_paths = [
        path
        for path in asset_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ]
    if any(path.parent.name in {"images", "frames"} or path.parent == asset_dir for path in image_paths):
        return False
    return bool(screenshots or image_paths)


def _manifest_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return [text]
    if isinstance(parsed, list):
        return [_text(item) for item in parsed if _text(item)]
    return []


def _manifest_list_json(value: object) -> str:
    if isinstance(value, str):
        text = _text(value)
        return text if text.startswith("[") else "[]"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return "[]"


def _manifest_mapping_json(value: object) -> str:
    if isinstance(value, str):
        text = _text(value)
        return text if text.startswith("{") else "{}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "{}"


def _channel_top_link_card_html(row: pd.Series, cover_lookup: dict[str, str], *, rank: int) -> str:
    title = _text(row.get("title")) or _text(row.get("content_id")) or "未命名素材"
    account = _text(row.get("account"))
    content_id = _text(row.get("content_id"))
    asset_key = _text(row.get("asset_key"))
    cover_path = _cover_path_for_row(row, cover_lookup)
    cover_uri = _image_data_uri(cover_path) if cover_path else ""
    url, open_label = _open_link_for_row(row)
    activation_cost = _safe_ratio(row.get("spend"), row.get("activations"))
    first_pay_cost = _safe_ratio(row.get("spend"), row.get("first_pay_count"))
    details = "｜".join(part for part in [account, content_id] if part)
    cover_id = f"top-cover-{abs(hash(asset_key or content_id or title))}"
    open_link = (
        f'<a class="top-link-open" href="{_safe_html(url)}" target="_blank" rel="noopener noreferrer">{_safe_html(open_label)}</a>'
        if url
        else '<span class="top-link-open muted">暂无可打开链接</span>'
    )
    if cover_uri:
        cover_html = textwrap.dedent(f"""
        <a class="top-link-cover" href="#{cover_id}" aria-label="点击放大封面">
            <img src="{cover_uri}" alt="封面">
            <span class="top-link-cover-label">点击放大</span>
        </a>
        <div id="{cover_id}" class="top-link-cover-modal">
            <div class="top-link-cover-dialog">
                <div class="top-link-cover-dialog-head">
                    <strong>封面大图预览</strong>
                    <a href="#" class="top-link-cover-close">关闭</a>
                </div>
                <div class="top-link-cover-large"><img src="{cover_uri}" alt="封面大图"></div>
            </div>
        </div>
        """).strip()
    else:
        cover_html = '<div class="top-link-cover empty"><span>暂无封面</span></div>'
    return textwrap.dedent(f"""
    <div class="top-link-card">
        {cover_html}
        <div class="top-link-body">
            <div class="top-link-topline">
                <span class="top-link-rank">#{rank}</span>
                <span class="top-link-channel">{_safe_html(_text(row.get("channel")) or "未命名渠道")}</span>
            </div>
            <div class="top-link-title">{_safe_html(title)}</div>
            <div class="top-link-detail">{_safe_html(details)}</div>
            <div class="top-link-metrics">
                <div class="top-link-metric top-link-metric-main"><span>消耗</span><strong>{format_display_number(row.get("spend"), 0)}</strong></div>
                <div class="top-link-metric"><span>曝光</span><strong>{format_display_number(row.get("impressions"), 0)}</strong></div>
                <div class="top-link-metric"><span>激活 / 成本</span><strong>{format_display_number(row.get("activations"), 0)} / {format_display_number(activation_cost, 2)}</strong></div>
                <div class="top-link-metric"><span>付费 / 成本</span><strong>{format_display_number(row.get("first_pay_count"), 0)} / {format_display_number(first_pay_cost, 2)}</strong></div>
            </div>
            {open_link}
        </div>
    </div>
    """).strip()


def _cover_path_for_row(row: pd.Series, cover_lookup: dict[str, str]) -> str:
    for key in _asset_key_candidates_for_row(row):
        channel_key = _cover_lookup_key(_text(row.get("channel")), key)
        cover_path = cover_lookup.get(channel_key, "")
        if cover_path:
            return cover_path
        cover_path = cover_lookup.get(key, "")
        if cover_path:
            return cover_path
    return ""


def _asset_key_candidates_for_row(row: pd.Series) -> list[str]:
    platform = _platform_from_row(row)
    candidates: list[str] = []
    explicit = _text(row.get("asset_key"))
    if explicit and not (platform == "抖音" and "::material::" in explicit):
        candidates.append(explicit)
    if platform == "抖音":
        for content_id in _douyin_work_id_candidates_for_row(row):
            candidates.append(f"抖音::id::{content_id}")
        return list(dict.fromkeys(candidate for candidate in candidates if candidate))
    for content_id in _content_id_candidates_for_row(row):
        candidates.append(f"{platform}::id::{content_id}" if platform else f"id::{content_id}")
    url = _text(row.get("content_url"))
    if url:
        candidates.append(f"{platform}::url::{url}" if platform else f"url::{url}")
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _open_url_for_row(row: pd.Series) -> str:
    return _open_link_for_row(row)[0]


def _open_link_for_row(row: pd.Series) -> tuple[str, str]:
    platform = _platform_from_row(row)
    if platform == "抖音":
        douyin_id = _preferred_douyin_work_id(row)
        if douyin_id:
            return f"https://www.douyin.com/video/{douyin_id}", "打开作品"
        douyin_url = _douyin_url_from_row(row)
        if douyin_url:
            return douyin_url, "打开作品"
        evidence_url = _text(row.get("ad_material_url")) or _text(row.get("ad_cover_url"))
        if evidence_url:
            return evidence_url, "打开巨量证据"
        return "", ""
    url = _text(row.get("content_url")) or _text(row.get("work_url"))
    return (url, "打开作品") if url else ("", "")


def _platform_from_row(row: pd.Series) -> str:
    text = " ".join(_text(row.get(column)) for column in ["platform", "platform_group", "channel"])
    lowered = text.lower()
    if "抖音" in text or "douyin" in lowered:
        return "抖音"
    if "小红书" in text or "xhs" in lowered:
        return "小红书"
    if "B站" in text or "b站" in text or "bilibili" in lowered:
        return "B站"
    return _text(row.get("platform"))


def _content_id_candidates_for_row(row: pd.Series) -> list[str]:
    return [
        value
        for value in [
            _text(row.get("content_id")),
            _text(row.get("material_id")),
            _text(row.get("work_id")),
            _douyin_id_from_text(row.get("content_url")),
            _douyin_id_from_text(row.get("asset_key")),
            _douyin_id_from_text(row.get("content_identity_key")),
        ]
        if value
    ]


def _douyin_id_candidates_for_row(row: pd.Series) -> list[str]:
    values = [
        _text(row.get("content_id")),
        _text(row.get("work_id")),
        _douyin_id_from_text(row.get("content_url")),
        _text(row.get("material_id")),
        _douyin_id_from_text(row.get("asset_key")),
        _douyin_id_from_text(row.get("content_identity_key")),
    ]
    return list(dict.fromkeys(value for value in values if value))


def _douyin_work_id_candidates_for_row(row: pd.Series) -> list[str]:
    material_id = _text(row.get("ad_material_id")) or _text(row.get("material_id"))
    trusted = _has_trusted_douyin_link_source(row)
    values = []
    for value in [
        _text(row.get("work_id")),
        _douyin_id_from_text(row.get("work_url")),
        _douyin_id_from_text(row.get("content_url")),
        _douyin_id_from_text(row.get("content_identity_key")),
    ]:
        if not value:
            continue
        if value == material_id and not trusted:
            continue
        values.append(value)
    return list(dict.fromkeys(value for value in values if value))


def _preferred_douyin_work_id(row: pd.Series) -> str:
    for value in _douyin_work_id_candidates_for_row(row):
        if value:
            return value
    return ""


def _douyin_url_from_row(row: pd.Series) -> str:
    for value in [_text(row.get("work_url")), _text(row.get("content_url"))]:
        item_id = _douyin_id_from_text(value)
        material_id = _text(row.get("ad_material_id")) or _text(row.get("material_id"))
        if "douyin.com/" in value and item_id and (item_id != material_id or _has_trusted_douyin_link_source(row)):
            return value
    return ""


def _has_trusted_douyin_link_source(row: pd.Series) -> bool:
    source = " ".join(_text(row.get(column)) for column in ["link_source", "metadata_source", "match_source"]).lower()
    return any(
        token in source
        for token in ["harvester_douyin_detail", "harvester_cache", "metadata_cache", "original_excel", "作品id"]
    )


def _douyin_id_from_text(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    match = re.search(r"(?:douyin\.com/video/|抖音::id::|::抖音::id::)([A-Za-z0-9_-]+)", text)
    if match:
        return match.group(1)
    return ""


def _top_cover_lookup(manifests: pd.DataFrame | None) -> dict[str, str]:
    if manifests is None or manifests.empty:
        return {}
    lookup: dict[str, str] = {}
    frame = manifests.copy()
    for column in ["asset_key", "cover_path", "status", "channel"]:
        if column not in frame.columns:
            frame[column] = ""
    for _, row in frame.iterrows():
        if _text(row.get("status")) != "succeeded":
            continue
        asset_key = _text(row.get("asset_key"))
        cover_path = _text(row.get("cover_path"))
        channel = _text(row.get("channel"))
        if channel and asset_key and cover_path:
            lookup.setdefault(_cover_lookup_key(channel, asset_key), cover_path)
        if asset_key and cover_path and asset_key not in lookup:
            lookup[asset_key] = cover_path
    return lookup


def _cover_lookup_key(channel: str, asset_key: str) -> str:
    return f"{_text(channel)}::{_text(asset_key)}"


def _build_local_recap_tables(db_path: Path, batch_id: str, top_pool: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if top_pool is None or top_pool.empty:
        return {
            "summary": pd.DataFrame(),
            "channel": pd.DataFrame(),
            "type": pd.DataFrame(),
        }
    frame = top_pool.copy()
    for column in ["channel", "content_url", "match_status", "analysis_status"]:
        if column not in frame.columns:
            frame[column] = ""
    for column in ["spend", "impressions", "activations", "first_pay_count", "value"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    executable_mask = frame["content_url"].map(_text).ne("")
    if "match_status" in frame.columns:
        executable_mask |= frame["match_status"].astype(str).str.strip().eq("已匹配")
    if "analysis_status" in frame.columns:
        executable_mask |= frame["analysis_status"].astype(str).str.strip().eq("可分析")
    summary = pd.DataFrame(
        [
            {
                "高价值素材数": int(len(frame)),
                "可复盘素材数": int(executable_mask.sum()),
                "待补齐素材数": int(len(frame) - executable_mask.sum()),
                "高价值消耗": _sum(frame, "spend"),
                "高价值曝光": _sum(frame, "impressions"),
                "高价值价值": _sum(frame, "value"),
            }
        ]
    )
    channel = (
        frame.groupby("channel", dropna=False)
        .agg(
            item_count=("content_id", "size"),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
            value=("value", "sum"),
        )
        .reset_index()
    )
    channel["activation_cost"] = channel.apply(lambda row: _safe_ratio(row["spend"], row["activations"]), axis=1)
    channel["first_pay_cost"] = channel.apply(lambda row: _safe_ratio(row["spend"], row["first_pay_count"]), axis=1)
    channel = channel.sort_values(["spend", "value"], ascending=[False, False]).reset_index(drop=True)
    type_recap = build_type_recap_items(db_path, batch_id, frame)
    return {
        "summary": summary,
        "channel": _display_channel_recap_columns(channel),
        "type": type_recap,
        "type_tables": _split_type_recap_tables(type_recap),
    }


def _render_local_recap_tables(tables: dict[str, pd.DataFrame], *, total_metrics: dict[str, float] | None = None) -> None:
    summary = tables.get("summary", pd.DataFrame())
    st.subheader("本地数据复盘")
    if summary.empty:
        st.info("暂无可复盘的本地高价值素材。")
        return
    row = summary.iloc[0]
    metrics = _local_recap_metric_items(row, total_metrics or {})
    metric_values = {item["label"]: item for item in metrics}
    for chunk in _metric_row_chunks(metric_values, max_columns=3):
        columns = st.columns(len(chunk))
        for column, (_, item) in zip(columns, chunk):
            column.markdown(_local_recap_metric_html(item), unsafe_allow_html=True)
    channel_tab, type_tab = st.tabs(["渠道复盘", "内容类型复盘"])
    with channel_tab:
        _show_frame(tables.get("channel", pd.DataFrame()), height=260)
    with type_tab:
        type_tables = tables.get("type_tables", {})
        if not type_tables:
            _show_frame(pd.DataFrame(), height=320)
        else:
            for label, frame in type_tables.items():
                st.markdown(f"#### {label}")
                _show_frame(frame, height=220)


def _local_recap_metric_items(row: pd.Series, total_metrics: dict[str, float] | None = None) -> list[dict[str, str]]:
    total_metrics = total_metrics or {}
    definitions = [
        ("高价值素材", row.get("高价值素材数"), 0, "", "高价值素材池总数"),
        ("可复盘素材", row.get("可复盘素材数"), 0, "", "可进入复盘的高价值素材"),
        ("待补齐素材", row.get("待补齐素材数"), 0, "", "高价值池内待补齐素材"),
        ("高价值消耗", row.get("高价值消耗"), 0, "消耗", "高价值素材池 / 当前周期总消耗"),
        ("高价值曝光", row.get("高价值曝光"), 0, "曝光", "高价值素材池 / 当前周期总曝光"),
        ("高价值价值", row.get("高价值价值"), 0, "价值", "高价值素材池 / 当前周期总价值"),
    ]
    items: list[dict[str, str]] = []
    for label, value, decimals, total_key, scope in definitions:
        item = {
            "label": label,
            "value": format_display_number(value, decimals),
            "share": _local_recap_share_text(value, total_metrics.get(total_key)) if total_key else "",
            "scope": scope,
        }
        items.append(item)
    return items


def _local_recap_share_text(value: object, total: object) -> str:
    total_value = _number(total)
    if not total_value:
        return ""
    return f"占总量 {_number(value) / total_value:.1%}"


def _local_recap_metric_html(item: dict[str, str]) -> str:
    share = _text(item.get("share"))
    share_html = f'<span class="local-recap-share">{_safe_html(share)}</span>' if share else ""
    return (
        '<div class="local-recap-metric">'
        f'<div class="local-recap-label">{_safe_html(item.get("label"))}</div>'
        '<div class="local-recap-value-line">'
        f'<span class="local-recap-value">{_safe_html(item.get("value"))}</span>'
        f"{share_html}"
        "</div>"
        f'<div class="local-recap-note">{_safe_html(item.get("scope"))}</div>'
        "</div>"
    )


def _render_type_recap_result_tables(type_recap: pd.DataFrame) -> None:
    for label, frame in _split_type_recap_tables(type_recap).items():
        st.markdown(f"#### {label}")
        _show_frame(frame, height=220)


def _display_type_recap_table(frame: pd.DataFrame, type_column_label: str = "类型") -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    columns = [
        "content_type",
        "item_count",
        "spend",
        "impressions",
        "activations",
        "first_pay_count",
        "activation_cost",
        "first_pay_cost",
        "value",
        "share",
    ]
    display = frame[[column for column in columns if column in frame.columns]].copy()
    if "content_type" in display.columns:
        display = display.rename(columns={"content_type": type_column_label})
    sort_columns = [column for column in ["value", "spend"] if column in display.columns]
    if sort_columns:
        return display.sort_values(sort_columns, ascending=[False] * len(sort_columns))
    return display


def _split_type_recap_tables(type_recap: pd.DataFrame) -> dict[str, pd.DataFrame]:
    labels = [
        ("抖音一级类型", "抖音", "douyin_l1"),
        ("抖音二级类型", "抖音", "douyin_l2"),
        ("小红书一级类型", "小红书", "xhs_l1"),
        ("小红书二级类型", "小红书", "xhs_l2"),
        ("B站内容类型", "B站", "bilibili"),
    ]
    if type_recap is None or type_recap.empty:
        return {label: pd.DataFrame() for label, _, _ in labels}
    frame = type_recap.copy()
    for column in ["platform", "type_level"]:
        if column not in frame.columns:
            frame[column] = ""
    return {
        label: _display_type_recap_table(
            frame[
                frame["platform"].astype(str).str.strip().eq(platform)
                & frame["type_level"].astype(str).str.strip().eq(type_level)
            ].copy(),
            label,
        )
        for label, platform, type_level in labels
    }


def _display_channel_recap_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    columns = [
        "channel",
        "item_count",
        "spend",
        "impressions",
        "activations",
        "first_pay_count",
        "activation_cost",
        "first_pay_cost",
        "value",
    ]
    return frame[[column for column in columns if column in frame.columns]].copy()


def _display_value_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame


def _analysis_job_result_analyzer(batch_id: str, analysis_purpose: str = ANALYSIS_PURPOSE_STRATEGY_RECAP):
    jobs = list_analysis_jobs(APP_DB, batch_id=batch_id)
    results: dict[str, dict[str, object]] = {}
    if not jobs.empty:
        for _, row in jobs.iterrows():
            if _text(row.get("analysis_purpose")) != analysis_purpose:
                continue
            identity = _text(row.get("content_identity_key"))
            result_json = _text(row.get("result_json"))
            if not identity or not result_json:
                continue
            try:
                parsed = json.loads(result_json)
            except Exception:
                parsed = {}
            if isinstance(parsed, dict):
                results[identity] = parsed

    def analyzer(row: pd.Series) -> dict[str, object]:
        return results.get(_text(row.get("content_identity_key")), {})

    return analyzer


def _successful_analysis_identities(batch_id: str, analysis_purpose: str) -> set[str]:
    jobs = list_analysis_jobs(APP_DB, batch_id=batch_id)
    if jobs.empty:
        return set()
    scoped = jobs[
        jobs.get("analysis_purpose", pd.Series(dtype=object)).map(_text).eq(analysis_purpose)
        & jobs.get("status", pd.Series(dtype=object)).map(_text).eq("succeeded")
    ].copy()
    if scoped.empty or "content_identity_key" not in scoped.columns:
        return set()
    return {value for value in scoped["content_identity_key"].map(_text).tolist() if value}


def _filter_pool_by_identities(pool: pd.DataFrame, identities: set[str]) -> pd.DataFrame:
    if pool is None or pool.empty or not identities or "content_identity_key" not in pool.columns:
        return pool.iloc[0:0].copy() if pool is not None else pd.DataFrame()
    return pool[pool["content_identity_key"].map(_text).isin(identities)].copy()


def _asset_source_label(value: object) -> str:
    text = _text(value)
    if not text:
        return "本地缓存"
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            text = " ".join(_text(parsed.get(key)) for key in ["ops_cache_source", "ops_cache_note", "source"])
    except Exception:
        pass
    if "harvester_daily_cache" in text or "每日缓存" in text:
        return "每日采集复用"
    if "topn" in text.lower() or "补采" in text:
        return "重点素材补采"
    if "复用" in text or "已采集" in text:
        return "本地缓存复用"
    return "本地缓存"


def _harvester_phase_label(stage: object, phase: object) -> str:
    key = (_text(stage), _text(phase))
    labels = {
        ("material", "start"): "素材缓存开始",
        ("material", "prepare"): "素材准备中",
        ("material", "fallback"): "正在使用浏览器兜底采集",
        ("material", "fallback-extract"): "正在提取页面媒体",
        ("material", "manifest"): "正在写入素材记录",
        ("material", "done"): "素材缓存完成",
    }
    return labels.get(key) or "素材处理中"


def _harvester_progress_text(event: object, started_at: float) -> str:
    total = int(getattr(event, "total", 0) or 0)
    completed = int(getattr(event, "completed", 0) or 0)
    remaining = int(getattr(event, "remaining_count", 0) or 0)
    platform = _text(getattr(event, "platform", "")) or "素材"
    action = _text(getattr(event, "action", "")) or _harvester_phase_label(
        getattr(event, "stage", ""),
        getattr(event, "phase", ""),
    )
    if not total:
        return f"{platform}：{action}，后台静默运行中。"
    elapsed = max(time.monotonic() - started_at, 0.0)
    eta_text = ""
    if completed > 0 and remaining > 0 and elapsed > 0:
        seconds = int(round((elapsed / completed) * remaining))
        eta_text = f"，预计还需 {_format_duration(seconds)}"
    return f"{platform}：{action}，后台静默运行中，已完成 {completed}/{total}，剩余 {remaining} 个{eta_text}。"


def _format_duration(seconds: int) -> str:
    value = max(int(seconds), 0)
    if value < 60:
        return f"{value} 秒"
    minutes = value // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    rest_minutes = minutes % 60
    return f"{hours} 小时 {rest_minutes} 分钟" if rest_minutes else f"{hours} 小时"


def _format_bytes(value: object) -> str:
    size = _number(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    decimals = 0 if index == 0 else 1
    return f"{size:.{decimals}f}{units[index]}"


def _image_data_uri(path: object) -> str:
    image_path_text = _text(path)
    if not image_path_text:
        return ""
    image_path = Path(image_path_text).expanduser()
    if not image_path.exists() or not image_path.is_file():
        return ""
    suffix = image_path.suffix.lower()
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix)
    if not mime_type:
        return ""
    try:
        stat = image_path.stat()
    except OSError:
        return ""
    return _image_data_uri_cached(str(image_path), mime_type, int(stat.st_size), int(stat.st_mtime_ns))


@st.cache_data(show_spinner=False, max_entries=256)
def _image_data_uri_cached(path: str, mime_type: str, size_bytes: int, modified_ns: int) -> str:
    del size_bytes, modified_ns
    try:
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:{mime_type};base64,{encoded}"


def _safe_html(value: object) -> str:
    return html.escape(_text(value), quote=True)


def _recent_successful_batch_ids(*, limit: int = 8) -> list[str]:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty or "batch_id" not in batches.columns:
        return []
    return [_text(value) for value in batches["batch_id"].head(limit).tolist() if _text(value)]


def _build_trend_frame(period_level: str, window_size: int) -> pd.DataFrame:
    batches = list_successful_dashboard_batches(APP_DB)
    if batches.empty:
        return pd.DataFrame()
    items = load_all_dashboard_items(APP_DB)
    if period_level in {PERIOD_LEVEL_WEEK, PERIOD_LEVEL_MONTH}:
        trend = summarize_period_metric_trends(items, batches, period_level, window_size=window_size)
        return _normalize_trend_frame(trend)
    return _build_rollup_trend_frame(items, batches, period_level, window_size)


def _build_rollup_trend_frame(items: pd.DataFrame, batches: pd.DataFrame, period_level: str, window_size: int) -> pd.DataFrame:
    selected = batches[batches["period_level"].astype(str).eq(period_level)].copy()
    if selected.empty:
        return pd.DataFrame()
    selected["_period_end_dt"] = pd.to_datetime(selected["period_end"], errors="coerce")
    selected = selected.sort_values(["_period_end_dt", "created_at"], ascending=[False, False]).head(window_size)
    if items.empty:
        grouped = pd.DataFrame(columns=["batch_id", *NUMERIC_COLUMNS])
    else:
        frame = items[items["batch_id"].isin(set(selected["batch_id"].astype(str)))].copy()
        grouped = (
            frame.groupby("batch_id", dropna=False)
            .agg(
                spend=("spend", _sum_series),
                impressions=("impressions", _sum_series),
                clicks=("clicks", _sum_series),
                activations=("activations", _sum_series),
                first_pay_count=("first_pay_count", _sum_series),
            )
            .reset_index()
            if not frame.empty
            else pd.DataFrame(columns=["batch_id", *NUMERIC_COLUMNS])
        )
    trend = selected.merge(grouped, on="batch_id", how="left")
    return _normalize_trend_frame(trend.sort_values("period_start"))


def _normalize_trend_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in NUMERIC_COLUMNS:
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)
    if "trend_period" not in result.columns:
        result["trend_period"] = result["period_start"].astype(str) + " 至 " + result["period_end"].astype(str)
    result["activation_cost"] = result.apply(lambda row: _safe_ratio(row["spend"], row["activations"]), axis=1)
    result["first_pay_cost"] = result.apply(lambda row: _safe_ratio(row["spend"], row["first_pay_count"]), axis=1)
    columns = [
        "trend_period",
        "period_level",
        "period_key",
        "period_label",
        "spend",
        "impressions",
        "activations",
        "first_pay_count",
        "activation_cost",
        "first_pay_cost",
    ]
    for column in columns:
        if column not in result.columns:
            result[column] = ""
    return result[columns].reset_index(drop=True)


def _trend_display_frame(trend: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "trend_period",
        "spend",
        "impressions",
        "activations",
        "first_pay_count",
        "activation_cost",
        "first_pay_cost",
    ]
    return _select_display_columns(trend, columns)


def _frame_height_for_rows(frame: pd.DataFrame, requested_height: int, *, fit_all_rows: bool = False) -> int:
    header_height = 42
    row_height = 36
    frame_padding = 16
    content_height = header_height + max(len(frame), 1) * row_height + frame_padding
    fitted_height = max(96, content_height)
    if fit_all_rows:
        return fitted_height
    return min(requested_height, fitted_height)


def _localized_column_label(column: str) -> str:
    return localize_columns(pd.DataFrame(columns=[column])).columns[0]


def _table_numeric_source_column(column: object) -> str:
    text = str(column)
    if text in TABLE_NUMERIC_COLUMNS:
        return text
    return LOCALIZED_TABLE_NUMERIC_COLUMNS.get(text, "")


def _prepare_table_display(frame: pd.DataFrame) -> pd.DataFrame:
    display = localize_columns(_format_display_time_columns(frame))
    for raw_column in frame.columns:
        source_column = _table_numeric_source_column(raw_column)
        localized_column = _localized_column_label(raw_column)
        if not source_column or localized_column not in display.columns:
            continue
        numeric = pd.to_numeric(display[localized_column], errors="coerce")
        if source_column in PERCENT_DISPLAY_COLUMNS:
            display[localized_column] = (numeric * 100).round(1)
        elif source_column in INTEGER_DISPLAY_COLUMNS:
            display[localized_column] = numeric.round().astype("Int64")
        else:
            display[localized_column] = numeric.round(2)
    return display


def _table_column_config(frame: pd.DataFrame) -> dict[str, object]:
    column_config = {}
    for raw_column in frame.columns:
        source_column = _table_numeric_source_column(raw_column)
        if not source_column:
            continue
        localized_column = _localized_column_label(raw_column)
        if source_column in PERCENT_DISPLAY_COLUMNS:
            column_config[localized_column] = st.column_config.NumberColumn(localized_column, format="%g%%")
        else:
            column_config[localized_column] = st.column_config.NumberColumn(localized_column, format="localized")
    return column_config


def _show_frame(frame: pd.DataFrame, *, height: int = 300, fit_all_rows: bool = False) -> None:
    if frame is None or frame.empty:
        st.info("暂无数据。")
        return
    st.dataframe(
        _prepare_table_display(frame),
        width="stretch",
        hide_index=True,
        height=_frame_height_for_rows(frame, height, fit_all_rows=fit_all_rows),
        column_config=_table_column_config(frame),
    )


def _channel_total_count(totals: pd.DataFrame) -> int:
    if totals.empty or "is_channel_total" not in totals.columns:
        return 0
    mask = totals["is_channel_total"].astype(str).str.lower().isin({"1", "true", "yes"})
    return int(mask.sum())


def _covered_channel_count(items: pd.DataFrame, totals: pd.DataFrame) -> int:
    source = items if items is not None and not items.empty else _without_total_rows(totals if totals is not None else pd.DataFrame())
    if source is None or source.empty or "channel" not in source.columns:
        return 0
    channels = source["channel"].map(_text)
    return int(channels[channels.ne("")].nunique())


def _succeeded_manifest_count(manifests: pd.DataFrame) -> int:
    if manifests.empty or "status" not in manifests.columns:
        return 0
    return int(manifests["status"].astype(str).eq("succeeded").sum())


def _scoped_succeeded_manifest_count(manifests: pd.DataFrame, scope: pd.DataFrame) -> int:
    if manifests is None or manifests.empty or scope is None or scope.empty or "status" not in manifests.columns:
        return 0
    succeeded = manifests[manifests["status"].map(_text).eq("succeeded")].copy()
    if succeeded.empty:
        return 0
    scope_identities = _non_blank_set(scope, "content_identity_key")
    if scope_identities and "content_identity_key" in succeeded.columns:
        matched = succeeded[succeeded["content_identity_key"].map(_text).isin(scope_identities)]
        return int(len(matched))
    scope_job_ids = _non_blank_set(scope, "job_id")
    if scope_job_ids and "job_id" in succeeded.columns:
        matched = succeeded[succeeded["job_id"].map(_text).isin(scope_job_ids)]
        return int(len(matched))
    return 0


def _non_blank_set(frame: pd.DataFrame, column: str) -> set[str]:
    if frame is None or frame.empty or column not in frame.columns:
        return set()
    return {value for value in frame[column].map(_text).tolist() if value}


def _sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


def _sum_series(values: pd.Series) -> float:
    return float(pd.to_numeric(values, errors="coerce").fillna(0.0).sum())


def _safe_ratio(numerator: object, denominator: object) -> float:
    denominator_value = _number(denominator)
    return _number(numerator) / denominator_value if denominator_value else 0.0


def _number(value: object) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


_inject_theme()
page = st.navigation(PAGES, position="sidebar", expanded=True)
page.run()
