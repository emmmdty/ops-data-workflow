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
from ops_data_workflow.env_bridge import copy_missing_runtime_env
from ops_data_workflow.feishu_ledger import load_feishu_content_ledger
from ops_data_workflow.harvester_bridge import (
    cache_existing_harvester_assets_for_batch,
    resolve_harvester_root,
    run_harvester_asset_capture,
)
from ops_data_workflow.minimax_recap import analyze_top_content_with_minimax
from ops_data_workflow.multimodal_recap import build_type_recap_items, persist_multimodal_recap
from ops_data_workflow.periods import (
    PERIOD_LEVEL_LABELS,
    PERIOD_LEVEL_MONTH,
    PERIOD_LEVEL_QUARTER,
    PERIOD_LEVEL_WEEK,
    PERIOD_LEVEL_YEAR,
    PERIOD_LEVELS,
    review_period_from_dates,
)
from ops_data_workflow.recap_settings import get_recap_settings, update_recap_settings
from ops_data_workflow.reporting import DISPLAY_NUMERIC_COLUMNS, format_display_number, localize_columns
from ops_data_workflow.rollups import rollup_period_for, select_rollup_component_batches
from ops_data_workflow.source_storage import source_dir_for_period, source_storage_key
from ops_data_workflow.storage import (
    get_top_asset_cache_summary,
    list_content_performance_items,
    list_harvester_asset_jobs,
    list_harvester_asset_manifests,
    list_local_content_assets,
    list_multimodal_recap_items,
    list_period_channel_totals,
    list_top_asset_cache_entries,
    list_type_recap_items,
    persist_feishu_ledger_snapshot,
    previous_successful_batch_id_for_period,
    read_batch_record,
    upsert_content_assets_from_feishu,
)
from ops_data_workflow.top_asset_service import build_executable_top_content_pool, build_high_spend_content_pool
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
HARVESTER_ENV_PATH = Path("/Users/tjk/Documents/Codex/harvester-THS/.env")

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
    manifests = list_harvester_asset_manifests(APP_DB, batch_id=batch_id)
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

        if st.button("开始标准化清洗", type="primary", disabled=not uploads, width="stretch"):
            if data_end < data_start:
                st.error("数据开始日期不能晚于结束日期。")
            else:
                _run_upload_cleaning(uploads or [], target_dir, period, overwrite_existing_channels)

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
    executable_top_pool = _top_pool_with_value(
        build_executable_top_content_pool(items),
        activation_weight=settings.activation_weight,
        first_pay_weight=settings.first_pay_weight,
    )
    st.subheader("高价值素材池")
    st.subheader("渠道消耗前 5")
    manifests = list_harvester_asset_manifests(APP_DB, batch_id=batch_id)
    _render_channel_top_link_cards(top_pool, manifests=manifests)
    recap_tables = _build_local_recap_tables(APP_DB, batch_id, top_pool)
    totals = list_period_channel_totals(APP_DB, batch_id=batch_id)
    total_metrics = _overview_metrics(
        totals,
        items,
        activation_weight=settings.activation_weight,
        first_pay_weight=settings.first_pay_weight,
    )
    _render_local_recap_tables(recap_tables, total_metrics=total_metrics)
    _show_frame(_top_pool_display(top_pool), height=420)
    if top_pool.empty:
        st.info("当前周期没有达到数量排名或阈值条件的素材。")
        return

    capture_pool = executable_top_pool
    if capture_pool.empty:
        st.warning("当前高价值素材还没有可执行的匹配状态，请先完成清洗匹配。")

    analysis_jobs = list_analysis_jobs(APP_DB, batch_id=batch_id)
    _render_asset_cache_status(
        _asset_cache_status_summary(top_pool, capture_pool, manifests, analysis_jobs),
        get_top_asset_cache_summary(APP_DB),
    )

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
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
        if c3.button("生成/更新复盘结果", disabled=capture_pool.empty, width="stretch"):
            reset_top_multimodal_jobs(APP_DB, batch_id, capture_pool, trigger="manual_recap")
            copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
            updated_jobs = run_top_multimodal_analysis_from_manifests(
                APP_DB,
                batch_id,
                analyzer=lambda job, manifest: analyze_top_content_with_minimax(job, manifest, env_path=ENV_PATH),
            )
            persisted = persist_multimodal_recap(
                APP_DB,
                batch_id,
                capture_pool,
                analyzer=_analysis_job_result_analyzer(batch_id),
            )
            st.success(f"已更新 {updated_jobs} 个分析任务，写入 {persisted.item_count} 条素材复盘和 {persisted.type_count} 条类型复盘。")

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

    st.subheader("类型复盘")
    _render_type_recap_result_tables(list_type_recap_items(APP_DB, batch_id=batch_id))
    st.subheader("素材复盘")
    _show_frame(_multimodal_recap_display(list_multimodal_recap_items(APP_DB, batch_id=batch_id)), height=360)
    with st.expander("素材缓存记录与复盘任务", expanded=False):
        _show_frame(_asset_cache_records_display(manifests), height=260)
        _show_frame(_analysis_jobs_display(analysis_jobs), height=260)


def _page_local_assets() -> None:
    st.title("本地总表")
    st.caption("全量本地素材库，按平台和素材 ID 去重；线上飞书只读，同步动作只更新本地总表和飞书快照。")

    with st.container(border=True):
        st.subheader("本地总素材表")
        if st.button("从线上飞书读取并更新本地总表", width="stretch"):
            _sync_feishu_ledger_to_local("manual:feishu")
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


def _run_upload_cleaning(uploads: list[object], target_dir: Path, period, overwrite_existing_channels: bool) -> None:
    copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
    with st.status("正在标准化清洗", expanded=True) as status:
        materialized = materialize_uploaded_files(
            uploads,
            target_dir,
            strip_common_period_root=True,
            replace_same_channel=overwrite_existing_channels,
        )
        status.write("已接收上传数据，正在进入清洗流程。")

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
        )
        status.update(label="数据清理结束", state="complete")
    st.success("清洗完成，本周期数据已更新。")
    if result.core_recap_xlsx:
        st.caption("已生成本周期核验结果。")


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


def _sync_feishu_ledger_to_local(batch_id: str) -> None:
    copy_missing_runtime_env(HARVESTER_ENV_PATH, ENV_PATH)
    with st.status("正在读取线上飞书台账", expanded=True) as status:
        ledger = load_feishu_content_ledger(env_path=ENV_PATH)
        written = upsert_content_assets_from_feishu(APP_DB, batch_id, ledger)
        snapshot = ledger.attrs.get("feishu_snapshot")
        if isinstance(snapshot, dict):
            persist_feishu_ledger_snapshot(APP_DB, batch_id, snapshot)
        status.update(label="本地总表已更新", state="complete")
    st.success(f"已写入或更新 {written} 条本地素材记录。")


def _display_batch_id(batch_id: object) -> str:
    text = _text(batch_id)
    return text or "当前周期"


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
    if not explicit_totals.empty:
        return explicit_totals
    return items.copy() if items is not None else pd.DataFrame()


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
        "content_url",
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
        "content_url",
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
    columns = [
        "platform",
        "content_id",
        "account",
        "title",
        "tags",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "content_url",
        "published_date",
        "updated_at",
    ]
    return _format_display_time_columns(_platform_type_display_columns(_select_display_columns(_dedupe_local_content_assets(assets), columns)))


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


def _analysis_jobs_display(jobs: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "status",
        "trigger",
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


def _channel_top_link_card_html(row: pd.Series, cover_lookup: dict[str, str], *, rank: int) -> str:
    title = _text(row.get("title")) or _text(row.get("content_id")) or "未命名素材"
    account = _text(row.get("account"))
    content_id = _text(row.get("content_id"))
    asset_key = _text(row.get("asset_key"))
    cover_path = cover_lookup.get(asset_key, "")
    cover_uri = _image_data_uri(cover_path)
    url = _text(row.get("content_url"))
    activation_cost = _safe_ratio(row.get("spend"), row.get("activations"))
    first_pay_cost = _safe_ratio(row.get("spend"), row.get("first_pay_count"))
    details = "｜".join(part for part in [account, content_id] if part)
    cover_id = f"top-cover-{abs(hash(asset_key or content_id or title))}"
    open_link = (
        f'<a class="top-link-open" href="{_safe_html(url)}" target="_blank" rel="noopener noreferrer">打开内容</a>'
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


def _top_cover_lookup(manifests: pd.DataFrame | None) -> dict[str, str]:
    if manifests is None or manifests.empty:
        return {}
    lookup: dict[str, str] = {}
    frame = manifests.copy()
    for column in ["asset_key", "cover_path", "status"]:
        if column not in frame.columns:
            frame[column] = ""
    for _, row in frame.iterrows():
        if _text(row.get("status")) != "succeeded":
            continue
        asset_key = _text(row.get("asset_key"))
        cover_path = _text(row.get("cover_path"))
        if asset_key and cover_path and asset_key not in lookup:
            lookup[asset_key] = cover_path
    return lookup


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


def _analysis_job_result_analyzer(batch_id: str):
    jobs = list_analysis_jobs(APP_DB, batch_id=batch_id)
    results: dict[str, dict[str, object]] = {}
    if not jobs.empty:
        for _, row in jobs.iterrows():
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
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
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
