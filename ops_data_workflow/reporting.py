"""Report artifact generation."""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px

from .reference_tables import to_display_reference_columns


def _get_logo_base64() -> str:
    """将项目根目录下的 brand_logo.png 转为 base64 字符串，用于内嵌到 HTML 报告中。"""
    logo_path = Path(__file__).parent.parent / "brand_logo.png"
    if logo_path.exists():
        with open(logo_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""


COLUMN_LABELS = {
    "platform": "平台",
    "platform_group": "平台组",
    "channel": "渠道",
    "period_start": "周期开始",
    "period_end": "周期结束",
    "batch_period_start": "批次周期开始",
    "batch_period_end": "批次周期结束",
    "content_id": "视频/笔记id",
    "material_id": "素材ID",
    "title": "标题",
    "account_raw": "原始账号",
    "account_id": "账号ID",
    "account": "实际账号",
    "account_mapping_source": "账号映射来源",
    "author": "作者",
    "cover_url": "封面/素材链接",
    "content_url": "内容链接",
    "category_l2": "二级栏目",
    "category_l3": "三级题材",
    "category_source": "分类来源",
    "category_l2_source": "二级栏目来源",
    "category_confidence": "分类置信度",
    "review_status": "审核状态",
    "manual_category": "人工内容类别",
    "ai_category": "AI生成内容类别",
    "content_category": "最终内容类别",
    "category_display": "内容分类",
    "suggested_category": "AI生成内容类别",
    "category_status": "内容类别来源",
    "spend": "消耗",
    "impressions": "展示/曝光量",
    "clicks": "点击量",
    "activations": "激活数",
    "first_pay_count": "首次付费次数",
    "activation_cost": "激活成本",
    "first_pay_cost": "首次付费成本",
    "ctr": "点击率",
    "activation_rate": "激活率",
    "first_pay_rate": "首次付费率",
    "dedupe_key": "去重键",
    "merged_row_count": "合并行数",
    "conflict_details": "冲突详情",
    "needs_manual_review": "需要人工审核",
    "review_reasons": "审核原因",
    "source_file": "来源文件",
    "source_sheet": "来源Sheet",
    "source_row": "来源行",
    "source_file_hash": "来源文件哈希",
    "duplicate_group_id": "重复组ID",
    "review_action": "审核动作",
    "missing_column": "缺失字段",
    "action": "处理动作",
    "relative_difference": "相对差异",
    "source_account_id": "来源账号ID",
    "source_account_name": "来源账号名",
    "mapping_source": "映射来源",
    "metric": "质量指标",
    "total": "总行数",
    "note": "说明",
    "expected_account": "应覆盖账号",
    "observed_count": "素材数",
    "matched_account": "实际账号",
    "status": "状态",
    "performance_flag": "表现标签",
    "spend_current": "本期消耗",
    "spend_previous": "对比期消耗",
    "spend_change_rate": "消耗环比",
    "activations_current": "本期激活",
    "activations_previous": "对比期激活",
    "activations_change_rate": "激活环比",
    "activation_cost_current": "本期激活成本",
    "activation_cost_previous": "对比期激活成本",
    "activation_cost_change_rate": "激活成本环比",
    "first_pay_count_current": "本期付费",
    "first_pay_count_previous": "对比期付费",
    "first_pay_count_change_rate": "付费环比",
    "first_pay_cost_current": "本期付费成本",
    "first_pay_cost_previous": "对比期付费成本",
    "first_pay_cost_change_rate": "付费成本环比",
    "first_pay_rate_current": "本期首次付费率",
    "first_pay_rate_previous": "对比期首次付费率",
    "first_pay_rate_change_rate": "首次付费率环比",
    "platform_count": "覆盖平台数",
    "channel_count": "覆盖渠道数",
    "account_count": "覆盖账号数",
    "item_count": "素材数",
    "unique_content_count": "唯一视频数",
    "trend_period": "趋势周期",
    "channels": "覆盖渠道",
    "channel_count": "覆盖渠道数",
    "material_count": "素材ID数",
    "missing_spend_share": "未匹配消耗占比",
    "recommendation_action": "推荐动作",
    "heat_score": "热度评分",
    "acquisition_score": "拉新评分",
    "overall_score": "拉新综合评分",
    "spend_share": "消耗占比",
    "activation_share": "激活占比",
    "first_pay_share": "首次付费占比",
    "pending_item_count": "缺失分类素材数",
    "pending_spend": "缺失分类消耗",
    "pending_spend_share": "缺失分类消耗占比",
    "secondary_category_count": "二级分类数",
    "source_file": "来源文件",
    "sheet": "Sheet",
    "raw_field": "原始字段",
    "value": "原始分类值",
    "count": "出现次数",
}


FIELD_MAPPING_ROWS = [
    {
        "输出字段": "平台",
        "B站.xlsx": "固定：B站",
        "小红书商业化.xlsx": "固定：小红书商业化",
        "抖音商业化.xlsx": "固定：抖音商业化",
        "抖音市场部.xlsx": "固定：抖音市场部",
        "说明": "每个 Excel 作为独立平台。",
    },
    {
        "输出字段": "平台组",
        "B站.xlsx": "固定：B站",
        "小红书商业化.xlsx": "固定：小红书",
        "抖音商业化.xlsx": "固定：抖音",
        "抖音市场部.xlsx": "固定：抖音",
        "说明": "用于跨 Excel 的平台大类聚合。",
    },
    {
        "输出字段": "渠道",
        "B站.xlsx": "固定：B站",
        "小红书商业化.xlsx": "固定：小红书商业化",
        "抖音商业化.xlsx": "固定：抖音商业化",
        "抖音市场部.xlsx": "固定：抖音市场部",
        "说明": "用于渠道汇总。",
    },
    {
        "输出字段": "视频/笔记id",
        "B站.xlsx": "视频BVID，缺失时用视频AVID",
        "小红书商业化.xlsx": "笔记ID",
        "抖音商业化.xlsx": "视频id",
        "抖音市场部.xlsx": "视频id",
        "说明": "跨平台内容主键。",
    },
    {
        "输出字段": "素材ID",
        "B站.xlsx": "素材中心id",
        "小红书商业化.xlsx": "笔记ID",
        "抖音商业化.xlsx": "素材ID",
        "抖音市场部.xlsx": "素材ID",
        "说明": "投放素材标识；小红书没有单独素材ID时复用笔记ID。",
    },
    {
        "输出字段": "标题",
        "B站.xlsx": "视频标题",
        "小红书商业化.xlsx": "标题",
        "抖音商业化.xlsx": "视频标题",
        "抖音市场部.xlsx": "视频标题",
        "说明": "用于展示和分类建议。",
    },
    {
        "输出字段": "账号/作者",
        "B站.xlsx": "Up主名称/UP主昵称；已知 Up主mid 1622777305 映射为同花顺投资",
        "小红书商业化.xlsx": "发布作者",
        "抖音商业化.xlsx": "账号、账号名称、发布账号、达人名称",
        "抖音市场部.xlsx": "账号、账号名称、发布账号、达人名称",
        "说明": "只写可从原始表或可验证映射得到的账号名；无法确认则留空。",
    },
    {
        "输出字段": "账号ID",
        "B站.xlsx": "Up主mid / UID",
        "小红书商业化.xlsx": "作者ID、用户ID、小红书号、账号ID",
        "抖音商业化.xlsx": "账号ID、抖音号、达人ID、作者ID",
        "抖音市场部.xlsx": "账号ID、抖音号、达人ID、作者ID",
        "说明": "账号ID与账号名分开保存，避免把数字ID误当成账号名。",
    },
    {
        "输出字段": "人工内容类别",
        "B站.xlsx": "无原始字段，留空",
        "小红书商业化.xlsx": "内容分类优先，其次内容类型，再用内容表格按笔记ID补齐",
        "抖音商业化.xlsx": "内容类型；缺失留空",
        "抖音市场部.xlsx": "内容类型；缺失留空",
        "说明": "只保存原始表或人工映射表中明确给出的类别。",
    },
    {
        "输出字段": "AI生成内容类别",
        "B站.xlsx": "从当前数据类别库匹配",
        "小红书商业化.xlsx": "人工类别缺失时从当前数据类别库匹配",
        "抖音商业化.xlsx": "人工类别缺失时从当前数据类别库匹配",
        "抖音市场部.xlsx": "人工类别缺失时从当前数据类别库匹配",
        "说明": "优先 TAG 精确匹配，其次 DeepSeek 只能从当前人工类别/TAG类别库中选择。",
    },
    {
        "输出字段": "最终内容类别",
        "B站.xlsx": "人工类别优先，其次 AI生成内容类别",
        "小红书商业化.xlsx": "人工类别优先，其次 AI生成内容类别",
        "抖音商业化.xlsx": "人工类别优先，其次 AI生成内容类别",
        "抖音市场部.xlsx": "人工类别优先，其次 AI生成内容类别",
        "说明": "用于内容榜单、分平台内容转化和报告图表。",
    },
    {
        "输出字段": "消耗",
        "B站.xlsx": "花费",
        "小红书商业化.xlsx": "消费",
        "抖音商业化.xlsx": "消耗",
        "抖音市场部.xlsx": "消耗",
        "说明": "投放消耗金额。",
    },
    {
        "输出字段": "展示/曝光量",
        "B站.xlsx": "展示量",
        "小红书商业化.xlsx": "展现量",
        "抖音商业化.xlsx": "展示数",
        "抖音市场部.xlsx": "展示数",
        "说明": "曝光口径。",
    },
    {
        "输出字段": "点击量",
        "B站.xlsx": "点击量",
        "小红书商业化.xlsx": "点击量",
        "抖音商业化.xlsx": "点击数",
        "抖音市场部.xlsx": "无，留空",
        "说明": "缺失时留空。",
    },
    {
        "输出字段": "激活数",
        "B站.xlsx": "应用激活数",
        "小红书商业化.xlsx": "激活数",
        "抖音商业化.xlsx": "激活数",
        "抖音市场部.xlsx": "激活数",
        "说明": "拉新核心代理指标。",
    },
    {
        "输出字段": "首次付费次数",
        "B站.xlsx": "应用内付费",
        "小红书商业化.xlsx": "首次付费次数",
        "抖音商业化.xlsx": "付费次数",
        "抖音市场部.xlsx": "付费次数",
        "说明": "深层转化指标。",
    },
    {
        "输出字段": "点击率 / 激活率 / 激活成本 / 首次付费成本 / 首次付费率",
        "B站.xlsx": "统一计算",
        "小红书商业化.xlsx": "统一计算",
        "抖音商业化.xlsx": "统一计算",
        "抖音市场部.xlsx": "统一计算",
        "说明": "点击率=点击量/展示量；激活率=激活数/点击量；激活成本=消耗/激活数；首次付费成本=消耗/首次付费次数；首次付费率=首次付费次数/激活数。",
    },
    {
        "输出字段": "来源文件",
        "B站.xlsx": "B站.xlsx",
        "小红书商业化.xlsx": "小红书商业化.xlsx",
        "抖音商业化.xlsx": "抖音商业化.xlsx",
        "抖音市场部.xlsx": "抖音市场部.xlsx",
        "说明": "用于追溯原始来源。",
    },
]


def write_outputs(
    output_dir: Path,
    period_start: str,
    period_end: str,
    canonical: pd.DataFrame,
    category_summary: pd.DataFrame,
    channel_summary: pd.DataFrame,
    platform_summary: pd.DataFrame,
    platform_category_summary: pd.DataFrame,
    total_summary: pd.DataFrame,
    raw_category_stats: pd.DataFrame,
    pending_categories: pd.DataFrame,
    account_audit: Optional[pd.DataFrame] = None,
    top_content_items: Optional[pd.DataFrame] = None,
    cover_metrics: Optional[pd.DataFrame] = None,
    data_quality: Optional[pd.DataFrame] = None,
    review_queue: Optional[pd.DataFrame] = None,
    preprocessing_report: Optional[pd.DataFrame] = None,
    duplicate_merge_details: Optional[pd.DataFrame] = None,
    conflict_retention_details: Optional[pd.DataFrame] = None,
    missing_value_details: Optional[pd.DataFrame] = None,
    reference_tables: Optional[dict[str, pd.DataFrame]] = None,
    channel_comparison: Optional[pd.DataFrame] = None,
    comparison_note: str = "",
    ai_summary: str = "",
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_html = output_dir / "report.html"
    analysis_xlsx = output_dir / "analysis.xlsx"
    canonical_csv = output_dir / "canonical.csv"
    total_summary_xlsx = output_dir / "total_summary.xlsx"

    _localized(_export_canonical(canonical)).to_csv(canonical_csv, index=False, encoding="utf-8-sig")
    _write_excel(
        analysis_xlsx,
        canonical,
        category_summary,
        channel_summary,
        platform_summary,
        platform_category_summary,
        total_summary,
        raw_category_stats,
        pending_categories,
        _frame_or_empty(account_audit),
        _frame_or_empty(top_content_items),
        _frame_or_empty(cover_metrics),
        _frame_or_empty(data_quality),
        _frame_or_empty(review_queue),
        _frame_or_empty(preprocessing_report),
        _frame_or_empty(duplicate_merge_details),
        _frame_or_empty(conflict_retention_details),
        _frame_or_empty(missing_value_details),
        reference_tables or {},
        _frame_or_empty(channel_comparison),
        ai_summary,
    )
    _write_total_summary(
        total_summary_xlsx,
        canonical,
        total_summary,
        platform_summary,
        platform_category_summary,
        _frame_or_empty(review_queue),
        _frame_or_empty(preprocessing_report),
        reference_tables or {},
    )
    _write_html(
        report_html,
        period_start,
        period_end,
        canonical,
        category_summary,
        channel_summary,
        platform_summary,
        platform_category_summary,
        pending_categories,
        _frame_or_empty(account_audit),
        _frame_or_empty(top_content_items),
        _frame_or_empty(cover_metrics),
        _frame_or_empty(data_quality),
        _frame_or_empty(review_queue),
        _frame_or_empty(channel_comparison),
        comparison_note,
        ai_summary,
    )
    return report_html, analysis_xlsx, canonical_csv, total_summary_xlsx


def _write_excel(
    path: Path,
    canonical: pd.DataFrame,
    category_summary: pd.DataFrame,
    channel_summary: pd.DataFrame,
    platform_summary: pd.DataFrame,
    platform_category_summary: pd.DataFrame,
    total_summary: pd.DataFrame,
    raw_category_stats: pd.DataFrame,
    pending_categories: pd.DataFrame,
    account_audit: pd.DataFrame,
    top_content_items: pd.DataFrame,
    cover_metrics: pd.DataFrame,
    data_quality: pd.DataFrame,
    review_queue: pd.DataFrame,
    preprocessing_report: pd.DataFrame,
    duplicate_merge_details: pd.DataFrame,
    conflict_retention_details: pd.DataFrame,
    missing_value_details: pd.DataFrame,
    reference_tables: dict[str, pd.DataFrame],
    channel_comparison: pd.DataFrame,
    ai_summary: str,
) -> None:
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        _localized(_export_canonical(canonical)).to_excel(writer, sheet_name="总表", index=False)
        _localized(platform_summary).to_excel(writer, sheet_name="分渠道总数据", index=False)
        _localized(platform_category_summary).to_excel(writer, sheet_name="分渠道栏目题材排名", index=False)
        _localized(category_summary).to_excel(writer, sheet_name="内容类型分级表", index=False)
        _localized(total_summary).to_excel(writer, sheet_name="周期报告", index=False)
        _localized(_reference_frame(reference_tables, "字段映射表")).to_excel(writer, sheet_name="字段映射表", index=False)
        _localized(_reference_frame(reference_tables, "账号映射表")).to_excel(writer, sheet_name="账号映射表", index=False)
        _localized(_reference_frame(reference_tables, "账号内容类型对照表")).to_excel(writer, sheet_name="账号内容类型对照表", index=False)
        _localized(raw_category_stats).to_excel(writer, sheet_name="原始分类统计", index=False)
        _localized(review_queue).to_excel(writer, sheet_name="人工审核表", index=False)
        _localized(account_audit).to_excel(writer, sheet_name="账号覆盖校验", index=False)
        _localized(top_content_items).to_excel(writer, sheet_name="消耗Top内容", index=False)
        _localized(cover_metrics).to_excel(writer, sheet_name="封面曝光分析", index=False)
        _localized(preprocessing_report).to_excel(writer, sheet_name="数据预处理报告", index=False)
        _localized(duplicate_merge_details).to_excel(writer, sheet_name="重复合并明细", index=False)
        _localized(conflict_retention_details).to_excel(writer, sheet_name="冲突保留明细", index=False)
        _localized(missing_value_details).to_excel(writer, sheet_name="缺失值处理明细", index=False)
        _localized(data_quality).to_excel(writer, sheet_name="数据质量报告", index=False)
        _localized(pending_categories).to_excel(writer, sheet_name="缺失分类清单", index=False)
        _localized(channel_comparison).to_excel(writer, sheet_name="历史对比", index=False)
        pd.DataFrame([{"AI结论": ai_summary}]).to_excel(writer, sheet_name="AI结论", index=False)
        for sheet in writer.sheets.values():
            sheet.freeze_panes(1, 0)


def _write_total_summary(
    path: Path,
    canonical: pd.DataFrame,
    total_summary: pd.DataFrame,
    platform_summary: pd.DataFrame,
    platform_category_summary: pd.DataFrame,
    review_queue: pd.DataFrame,
    preprocessing_report: pd.DataFrame,
    reference_tables: dict[str, pd.DataFrame],
) -> None:
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        _localized(_export_canonical(canonical)).to_excel(writer, sheet_name="总表", index=False)
        _localized(platform_summary).to_excel(writer, sheet_name="分渠道总数据", index=False)
        _localized(platform_category_summary).to_excel(writer, sheet_name="分渠道栏目题材排名", index=False)
        _localized(_reference_frame(reference_tables, "内容类型分级表")).to_excel(writer, sheet_name="内容类型分级表", index=False)
        _localized(_reference_frame(reference_tables, "字段映射表")).to_excel(writer, sheet_name="字段映射表", index=False)
        _localized(_reference_frame(reference_tables, "账号映射表")).to_excel(writer, sheet_name="账号映射表", index=False)
        _localized(review_queue).to_excel(writer, sheet_name="人工审核表", index=False)
        _localized(preprocessing_report).to_excel(writer, sheet_name="数据预处理报告", index=False)
        _localized(total_summary).to_excel(writer, sheet_name="周期报告", index=False)
        for sheet in writer.sheets.values():
            sheet.freeze_panes(1, 0)


def _write_html(
    path: Path,
    period_start: str,
    period_end: str,
    canonical: pd.DataFrame,
    category_summary: pd.DataFrame,
    channel_summary: pd.DataFrame,
    platform_summary: pd.DataFrame,
    platform_category_summary: pd.DataFrame,
    pending_categories: pd.DataFrame,
    account_audit: pd.DataFrame,
    top_content_items: pd.DataFrame,
    cover_metrics: pd.DataFrame,
    data_quality: pd.DataFrame,
    review_queue: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    comparison_note: str,
    ai_summary: str,
) -> None:
    top_categories = category_summary.head(12).copy()
    if "category_display" in top_categories.columns:
        top_categories["chart_category_display"] = top_categories["category_display"].replace("", "未填写")
    first_pay_rate = "first_pay_rate"
    if top_categories.empty:
        category_chart = "<p>暂无内容类别数据。</p>"
        matrix_chart = "<p>暂无热度转化矩阵。</p>"
    else:
        category_chart = px.bar(
            top_categories.sort_values("overall_score"),
            x="chart_category_display",
            y="overall_score",
            color="activations",
            title="拉新综合榜 Top 类别",
            labels={
                "chart_category_display": "内容分类",
                "overall_score": "综合评分",
                "activations": "激活数",
            },
        ).to_html(full_html=False, include_plotlyjs=True)
        matrix_data = category_summary.copy()
        matrix_data["spend"] = pd.to_numeric(matrix_data["spend"], errors="coerce")
        matrix_data["heat_score"] = pd.to_numeric(matrix_data["heat_score"], errors="coerce")
        matrix_data[first_pay_rate] = pd.to_numeric(matrix_data[first_pay_rate], errors="coerce")
        matrix_data = matrix_data[
            matrix_data["spend"].gt(0)
            & matrix_data["heat_score"].notna()
            & matrix_data[first_pay_rate].notna()
        ]
        if matrix_data.empty:
            matrix_chart = "<p>暂无可绘制消耗气泡。</p>"
        else:
            matrix_chart = px.scatter(
                matrix_data,
                x="heat_score",
                y=first_pay_rate,
                size="spend",
                color="content_category",
                hover_name="category_display",
                title="内容类别热度 x 首次付费率矩阵",
                labels={
                    "heat_score": "热度评分",
                    first_pay_rate: "首次付费率",
                    "spend": "消耗",
                },
            ).to_html(full_html=False, include_plotlyjs=False)

    platform_chart_data = platform_category_summary.copy()
    if not platform_chart_data.empty:
        platform_chart_data["chart_category_display"] = platform_chart_data["category_display"].replace("", "未填写")
    if platform_chart_data.empty:
        platform_chart = "<p>暂无渠道对比数据。</p>"
    else:
        platform_chart = px.bar(
            platform_chart_data,
            x="chart_category_display",
            y="activations",
            color="channel",
            barmode="group",
            title="各渠道栏目题材激活数",
            labels={
                "chart_category_display": "内容分类",
                "activations": "激活数",
                "channel": "渠道",
            },
        ).to_html(full_html=False, include_plotlyjs=False)

    pending_spend = float(pending_categories["spend"].sum()) if not pending_categories.empty else 0.0
    total_spend = float(canonical["spend"].sum()) if not canonical.empty else 0.0
    pending_ratio = pending_spend / total_spend if total_spend else 0.0
    total_activations = float(canonical["activations"].sum())
    total_first_pay = float(canonical["first_pay_count"].sum())
    activation_cost = total_spend / total_activations if total_activations else 0.0
    first_pay_cost = total_spend / total_first_pay if total_first_pay else 0.0
    account_missing = 0
    if not account_audit.empty and "status" in account_audit.columns:
        account_missing = int(account_audit["status"].eq("缺失").sum())
    comparison_block = (
        f"<p class=\"muted\">{escape(comparison_note)}</p>"
        if comparison_note
        else _localized(channel_comparison).to_html(index=False, classes="dataframe", border=0)
    )

    logo_b64 = _get_logo_base64()
    favicon_tag = f'<link rel="icon" type="image/png" href="data:image/png;base64,{logo_b64}">' if logo_b64 else ""
    logo_img = f'<img src="data:image/png;base64,{logo_b64}" alt="同花顺" style="height:40px;width:auto;">' if logo_b64 else ""

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>渠道化内容投放分析与定点投流报告</title>
  {favicon_tag}
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #17202a; background: #f5f7fb; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 24px 56px; }}
    .brand-header {{ display: flex; align-items: center; gap: 14px; margin-bottom: 4px; }}
    .brand-header h1 {{ font-size: 30px; margin: 0; }}
    h1 {{ font-size: 30px; margin: 0 0 8px; }}
    h2 {{ margin-top: 34px; border-bottom: 1px solid #d9dee7; padding-bottom: 8px; }}
    .muted {{ color: #667085; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin: 24px 0; }}
    .metric {{ background: #fff; border: 1px solid #d9dee7; border-radius: 8px; padding: 14px; box-shadow: 0 1px 2px rgba(16,24,40,.04); }}
    .metric strong {{ display: block; font-size: 22px; margin-top: 6px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }}
    th, td {{ border: 1px solid #d9dee7; padding: 8px 10px; text-align: left; }}
    th {{ background: #eef2f7; }}
    .ai {{ white-space: pre-wrap; line-height: 1.65; background: #fff; border: 1px solid #d9dee7; border-radius: 8px; padding: 16px; }}
    .warning {{ background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px; padding: 14px; }}
    @media (max-width: 900px) {{ .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
  </style>
</head>
<body>
<main>
  <div class="brand-header">
    {logo_img}
    <div>
      <h1>渠道化内容投放分析与定点投流报告</h1>
      <p class="muted">周期：{escape(period_start)} 至 {escape(period_end)}</p>
    </div>
  </div>
  <section class="metric-grid">
    <div class="metric">消耗<strong>{total_spend:,.0f}</strong></div>
    <div class="metric">激活<strong>{total_activations:,.0f}</strong></div>
    <div class="metric">激活成本<strong>{activation_cost:,.1f}</strong></div>
    <div class="metric">首次付费<strong>{total_first_pay:,.0f}</strong></div>
    <div class="metric">首次付费成本<strong>{first_pay_cost:,.1f}</strong></div>
    <div class="metric">缺失分类消耗占比<strong>{pending_ratio:.1%}</strong></div>
  </section>
  <h2>AI 数据结论</h2>
  <div class="ai">{escape(ai_summary or "未生成 AI 结论。")}</div>
  <h2>历史环比</h2>
  {comparison_block}
  <h2>账号覆盖校验</h2>
  <div class="warning">当前缺失账号 {account_missing} 个。请先补齐导出数据，再将报告用于正式复盘。</div>
  {_localized(account_audit).to_html(index=False, classes="dataframe", border=0)}
  <h2>数据质量报告</h2>
  {_localized(data_quality).to_html(index=False, classes="dataframe", border=0)}
  <h2>拉新综合榜</h2>
  {category_chart}
  <h2>热度转化矩阵</h2>
  {matrix_chart}
  <h2>分渠道栏目题材排名</h2>
  {platform_chart}
  <h2>分渠道总数据</h2>
  {_localized(platform_summary).to_html(index=False, classes="dataframe", border=0)}
  <h2>分渠道栏目题材明细</h2>
  {_localized(platform_category_summary).to_html(index=False, classes="dataframe", border=0)}
  <h2>渠道汇总</h2>
  {_localized(channel_summary).to_html(index=False, classes="dataframe", border=0)}
  <h2>各渠道消耗 Top15 内容</h2>
  {_localized(top_content_items).to_html(index=False, classes="dataframe", border=0)}
  <h2>小红书 / B站封面与展现分析</h2>
  {_localized(cover_metrics).to_html(index=False, classes="dataframe", border=0)}
  <h2>缺失分类影响说明</h2>
  <div class="warning">
    共有 {len(pending_categories)} 条素材缺少最终内容类别，消耗 {pending_spend:,.0f}，
    占全部消耗 {pending_ratio:.1%}。明细会同时保留人工内容类别和 AI生成内容类别，便于复核。
  </div>
  <h2>分类审核队列</h2>
  {_localized(review_queue.head(50)).to_html(index=False, classes="dataframe", border=0)}
  <h2>内容类别榜单明细</h2>
  {_localized(category_summary.head(20)).to_html(index=False, classes="dataframe", border=0)}
</main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def localize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns={column: _localized_column_name(column) for column in frame.columns})


# 重要字段优先顺序定义
IMPORTANT_COLUMNS_ORDER = [
    # 核心标识字段
    "channel",           # 渠道
    "title",             # 标题
    "content_id",        # 视频/笔记id
    "material_id",       # 素材ID
    "account",           # 实际账号
    # 核心指标字段
    "spend",             # 消耗
    "activations",       # 激活数
    "activation_cost",   # 激活成本
    "first_pay_count",   # 付费数
    "first_pay_cost",    # 付费成本
    "first_pay_rate",    # 付费率
    # 分类字段
    "category_l2",       # 二级栏目
    "category_l3",       # 三级题材
    "category_source",   # 分类来源
    "category_l2_source",  # 二级栏目来源
    "content_category",  # 最终内容类别
    "category_display",  # 内容分类
    # 辅助字段
    "item_count",        # 素材数
    "unique_content_count",  # 唯一视频数
    "impressions",       # 展示/曝光量
    "clicks",            # 点击量
    "ctr",               # 点击率
    "account_id",        # 账号ID
    "author",            # 作者
    "source_file",       # 来源文件
    "source_sheet",      # 来源Sheet
]


def sort_columns_by_importance(frame: pd.DataFrame) -> pd.DataFrame:
    """按重要字段优先顺序排序列，重要字段在前。"""
    if len(frame.columns) == 0:
        return frame

    current_columns = list(frame.columns)
    sorted_columns = []

    # 先添加重要字段（按优先顺序）
    for col in IMPORTANT_COLUMNS_ORDER:
        if col in current_columns:
            sorted_columns.append(col)

    # 再添加剩余字段
    for col in current_columns:
        if col not in sorted_columns:
            sorted_columns.append(col)

    return frame[sorted_columns]


def localize_and_sort_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """本地化列名并按重要性排序。"""
    return localize_columns(sort_columns_by_importance(frame))


def _localized(frame: pd.DataFrame) -> pd.DataFrame:
    return localize_columns(frame)


def _frame_or_empty(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    return frame if frame is not None else pd.DataFrame()


def _export_canonical(canonical: pd.DataFrame) -> pd.DataFrame:
    return canonical.drop(
        columns=["platform", "platform_group", "primary_category", "category_l1"],
        errors="ignore",
    )


def _reference_frame(reference_tables: dict[str, pd.DataFrame], sheet_name: str) -> pd.DataFrame:
    if sheet_name in reference_tables:
        return to_display_reference_columns(reference_tables[sheet_name])
    if sheet_name == "字段映射表":
        return pd.DataFrame(FIELD_MAPPING_ROWS)
    return pd.DataFrame()


def _localized_column_name(column: object) -> str:
    text = str(column)
    if text.startswith("raw__"):
        parts = text.split("__", 2)
        if len(parts) == 3:
            return f"原始字段：{_display_raw_token(parts[1])}：{_display_raw_token(parts[2])}"
    return COLUMN_LABELS.get(text, text)


def _display_raw_token(value: str) -> str:
    return value.replace("_", "－")
