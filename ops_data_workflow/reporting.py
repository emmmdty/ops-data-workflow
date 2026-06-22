"""Display formatting helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import pandas as pd


COLUMN_LABELS = {
    "platform": "平台",
    "platform_group": "平台",
    "channel": "渠道",
    "period_start": "周期开始",
    "period_end": "周期结束",
    "batch_period_start": "周期开始",
    "batch_period_end": "周期结束",
    "batch_id": "批次",
    "content_id": "平台编号",
    "content_identity_key": "素材身份键",
    "asset_key": "本地素材键",
    "performance_key": "周期表现键",
    "content_id_fallback": "备用内容编号",
    "material_id": "素材编号",
    "ad_material_id": "巨量素材ID",
    "title": "标题",
    "account_raw": "账号来源值",
    "account_id": "账号编号",
    "account": "账号",
    "tags": "tag词",
    "account_mapping_source": "账号映射来源",
    "account_normalized": "归一账号",
    "account_filter_status": "账号过滤状态",
    "account_filter_reason": "账号过滤原因",
    "author": "作者",
    "cover_url": "封面/素材链接",
    "content_url": "作品链接(兼容)",
    "work_id": "作品ID",
    "work_url": "作品链接",
    "ad_material_url": "巨量链接",
    "ad_cover_url": "巨量封面链接",
    "source_time": "时间",
    "duration": "时长",
    "category_l2": "二级类型",
    "category_l3": "题材",
    "category_source": "分类来源",
    "category_l2_source": "二级类型来源",
    "category_confidence": "分类置信度",
    "review_status": "质量状态",
    "content_form": "内容形式",
    "manual_category": "内容类型",
    "manual_category_source": "内容类型来源",
    "ai_category": "AI生成内容类别",
    "content_category": "最终内容类别",
    "category_display": "内容分类",
    "suggested_category": "AI生成内容类别",
    "category_status": "内容类别来源",
    "spend": "消耗",
    "impressions": "曝光量",
    "clicks": "点击量",
    "activations": "激活数",
    "first_pay_count": "付费数",
    "activation_cost": "激活成本",
    "first_pay_cost": "付费成本",
    "ctr": "点击率",
    "activation_rate": "激活率",
    "first_pay_rate": "付费率",
    "activation_cost_raw": "原始激活成本",
    "first_pay_cost_raw": "原始付费成本",
    "ctr_raw": "原始点击率",
    "activation_rate_raw": "原始激活率",
    "first_pay_rate_raw": "原始付费率",
    "likes": "点赞数",
    "comments": "评论数",
    "favorites": "收藏数",
    "follows": "关注数",
    "dedupe_key": "去重键",
    "merged_row_count": "合并行数",
    "conflict_details": "冲突详情",
    "needs_manual_review": "需质量排查",
    "review_reasons": "质量原因",
    "ledger_match_source": "投稿台账匹配来源",
    "ledger_match_key": "投稿台账匹配键",
    "ledger_content_type": "投稿台账内容类型",
    "ledger_content_type_review": "投稿台账类型审核",
    "ledger_filter_status": "投稿台账筛选状态",
    "ledger_source_file": "投稿台账来源文件",
    "ledger_source_sheet": "投稿台账来源工作表",
    "ledger_source_row": "投稿台账来源行",
    "match_risk_level": "匹配风险等级",
    "match_risk_reason": "匹配风险原因",
    "metadata_source": "公开补充来源",
    "metadata_confidence": "公开补充置信度",
    "metadata_fetched_at": "公开补充时间",
    "metadata_error": "公开补充错误",
    "metadata_review_reason": "公开补充复核原因",
    "metadata_tags": "公开补充标签",
    "metadata_content_type_candidate": "公开补充内容类型候选",
    "link_openability": "链接状态",
    "link_source": "链接来源",
    "xhs_placeholder_url": "小红书占位链接",
    "source_file": "来源文件",
    "source_sheet": "来源工作表",
    "source_row": "来源行",
    "source_file_hash": "来源文件哈希",
    "duplicate_group_id": "重复组编号",
    "review_action": "审核动作",
    "missing_column": "缺失字段",
    "action": "处理动作",
    "relative_difference": "相对差异",
    "source_account_id": "来源账号编号",
    "source_account_name": "来源账号名",
    "mapping_source": "映射来源",
    "metric": "质量指标",
    "total": "总行数",
    "note": "说明",
    "expected_account": "应覆盖账号",
    "observed_count": "素材数",
    "matched_account": "实际账号",
    "status": "状态",
    "trigger": "触发方式",
    "rule_type": "规则类型",
    "source_account": "来源账号",
    "normalized_account": "归一账号",
    "included": "是否统计",
    "filter_enabled": "是否启用过滤",
    "config_source": "配置来源",
    "config_path": "配置文件",
    "filter_reason": "过滤原因",
    "performance_flag": "表现标签",
    "spend_current": "本期消耗",
    "spend_previous": "对比期消耗",
    "spend_change_rate": "消耗环比",
    "impressions_current": "本期曝光",
    "impressions_previous": "对比期曝光",
    "impressions_change_rate": "曝光环比",
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
    "first_pay_rate_current": "本期付费率",
    "first_pay_rate_previous": "对比期付费率",
    "first_pay_rate_change_rate": "付费率环比",
    "platform_count": "覆盖平台数",
    "channel_count": "覆盖渠道数",
    "account_count": "覆盖账号数",
    "item_count": "素材数",
    "unique_content_count": "唯一视频数",
    "content_type": "内容类型",
    "raw_content_type": "原始内容类型",
    "category_l1": "一级类型",
    "bilibili_content_type": "B站内容类型",
    "matched_ledger_title": "飞书匹配标题",
    "match_status": "匹配状态",
    "match_source": "匹配来源",
    "match_key": "匹配键",
    "match_confidence": "匹配置信度",
    "match_reason": "未匹配原因",
    "job_id": "任务编号",
    "jobs_path": "任务文件",
    "manifest_path": "结果文件",
    "harvester_root": "采集项目路径",
    "error_message": "错误信息",
    "asset_dir": "素材目录",
    "asset_source": "素材来源",
    "cache_size": "缓存体积",
    "has_cover": "封面",
    "has_video": "视频",
    "cover_path": "封面文件",
    "video_path": "视频文件",
    "screenshots_json": "截图文件",
    "frames_json": "关键帧文件",
    "metadata_json": "素材元数据",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "attempts": "尝试次数",
    "max_attempts": "最大尝试次数",
    "topic_name": "题材",
    "content_types": "涉及内容类型",
    "material_count": "素材数",
    "rank_metric": "排序指标",
    "rank_value": "排序值",
    "rank_position": "排序",
    "input_hash": "输入哈希",
    "trend_period": "趋势周期",
    "channels": "覆盖渠道",
    "channel_count": "覆盖渠道数",
    "material_count": "素材编号数",
    "missing_spend_share": "未匹配消耗占比",
    "recommendation_action": "推荐动作",
    "heat_score": "热度评分",
    "acquisition_score": "拉新评分",
    "overall_score": "拉新综合评分",
    "spend_share": "消耗占比",
    "activation_share": "激活占比",
    "first_pay_share": "付费占比",
    "pending_item_count": "缺失分类素材数",
    "pending_spend": "缺失分类消耗",
    "pending_spend_share": "缺失分类消耗占比",
    "secondary_category_count": "二级分类数",
    "value": "价值",
    "share": "价值占比",
    "type_level": "类型层级",
    "analysis_status": "复盘状态",
    "high_spend_reason": "入选原因",
    "summary": "复盘总结",
    "title_hook": "标题钩子",
    "visual_structure": "视觉结构",
    "information_density": "信息密度",
    "conversion_path": "转化路径",
    "reuse_points": "可复用点",
    "avoid_points": "不建议复用点",
    "next_period_strategy": "下周期策略建议",
    "published_date": "发布时间",
    "last_seen_batch_id": "最近批次",
    "source_file": "来源文件",
    "sheet": "工作表",
    "raw_field": "原始字段",
    "raw_value": "原始分类值",
    "count": "出现次数",
}

DISPLAY_NUMERIC_COLUMNS = {
    "spend",
    "spend_share",
    "impressions",
    "clicks",
    "ctr",
    "activations",
    "activation_cost",
    "activation_rate",
    "first_pay_count",
    "first_pay_cost",
    "first_pay_rate",
    "item_count",
    "unique_content_count",
    "material_count",
    "channel_count",
    "merged_row_count",
    "rank_value",
    "rank_position",
    "activation_share",
    "first_pay_share",
    "pending_item_count",
    "pending_spend",
    "pending_spend_share",
    "secondary_category_count",
    "observed_count",
    "count",
    "total",
    "value",
    "heat_score",
    "acquisition_score",
    "overall_score",
    "missing_spend_share",
    "spend_current",
    "spend_previous",
    "spend_change_rate",
    "activations_current",
    "activations_previous",
    "activations_change_rate",
    "activation_cost_current",
    "activation_cost_previous",
    "activation_cost_change_rate",
    "first_pay_count_current",
    "first_pay_count_previous",
    "first_pay_count_change_rate",
    "first_pay_cost_current",
    "first_pay_cost_previous",
    "first_pay_cost_change_rate",
    "first_pay_rate_current",
    "first_pay_rate_previous",
    "first_pay_rate_change_rate",
}


def format_display_number(value: object, max_decimals: int = 2) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return ""
    try:
        number = Decimal(str(numeric))
    except InvalidOperation:
        return ""
    if number == 0:
        return "0"
    if max_decimals <= 0:
        rounded = number.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return "0" if rounded == 0 else f"{rounded:,.0f}"
    quantum = Decimal("1").scaleb(-int(max_decimals))
    rounded = number.quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        return "0"
    text = f"{rounded:,.{max_decimals}f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def localize_columns(frame: pd.DataFrame, *, format_numbers: bool = False) -> pd.DataFrame:
    display = _drop_internal_compatibility_columns(frame.copy())
    if format_numbers:
        for column in display.columns:
            if column in DISPLAY_NUMERIC_COLUMNS:
                display[column] = display[column].map(format_display_number)
    return display.rename(columns={column: _localized_column_name(column) for column in display.columns})


def _drop_internal_compatibility_columns(frame: pd.DataFrame) -> pd.DataFrame:
    drop_columns = []
    if "platform" in frame.columns and "platform_group" in frame.columns:
        drop_columns.append("platform_group")
    if "account" in frame.columns and "account_raw" in frame.columns:
        drop_columns.append("account_raw")
    if not drop_columns:
        return frame
    return frame.drop(columns=drop_columns)


# 重要字段优先顺序定义
IMPORTANT_COLUMNS_ORDER = [
    # 核心标识字段
    "channel",           # 渠道
    "title",             # 标题
    "content_id",        # 视频/笔记id
    "material_id",       # 素材ID
    "account",           # 实际账号
    "content_form",      # 内容形式
    "manual_category",   # 内容类型
    # 核心指标字段
    "spend",             # 消耗
    "activations",       # 激活数
    "activation_cost",   # 激活成本
    "first_pay_count",   # 付费数
    "first_pay_cost",    # 付费成本
    "first_pay_rate",    # 付费率
    # 分类字段
    "category_l2",       # 二级类型
    "category_source",   # 分类来源
    "category_l2_source",  # 二级类型来源
    "content_category",  # 最终内容类别
    "category_display",  # 内容分类
    # 辅助字段
    "item_count",        # 素材数
    "unique_content_count",  # 唯一视频数
    "impressions",       # 曝光量
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


def _localized_column_name(column: object) -> str:
    text = str(column)
    if text.startswith("raw__"):
        parts = text.split("__", 2)
        if len(parts) == 3:
            return f"原始字段：{_display_raw_token(parts[1])}：{_display_raw_token(parts[2])}"
    return COLUMN_LABELS.get(text, text)


def _display_raw_token(value: str) -> str:
    return value.replace("_", "－")
