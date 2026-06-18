"""Decide whether cleaned assets are in content recap scope."""

from __future__ import annotations

import pandas as pd

from .platform_normalizers.common import platform_label, text_value


ANALYSIS_STATUS_ANALYZABLE = "可分析"
ANALYSIS_STATUS_UNANALYZABLE = "不可分析"
ANALYSIS_STATUS_PENDING = "待补全"

ANALYSIS_SCOPE_COLUMNS = ["analysis_status", "is_analyzable", "unanalyzable_reason"]


def apply_analysis_scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = frame.copy()
    for column in ANALYSIS_SCOPE_COLUMNS:
        if column not in scoped.columns:
            scoped[column] = False if column == "is_analyzable" else ""
    for index, row in scoped.iterrows():
        platform = platform_label(row)
        if text_value(row.get("match_status", "")) == "已匹配":
            scoped.at[index, "analysis_status"] = ANALYSIS_STATUS_ANALYZABLE
            scoped.at[index, "is_analyzable"] = True
            scoped.at[index, "unanalyzable_reason"] = ""
            continue
        scoped.at[index, "is_analyzable"] = False
        reason = _reason(row, platform)
        if reason in {"抖音URL解析失败"}:
            scoped.at[index, "analysis_status"] = ANALYSIS_STATUS_PENDING
        else:
            scoped.at[index, "analysis_status"] = ANALYSIS_STATUS_UNANALYZABLE
        scoped.at[index, "unanalyzable_reason"] = reason
    return scoped


def _reason(row: pd.Series, platform: str) -> str:
    if platform not in {"抖音", "小红书", "B站"}:
        return "平台不在复盘范围"
    normalization_reason = text_value(row.get("normalization_reason", ""))
    match_reason = text_value(row.get("match_reason", ""))
    if "抖音URL解析失败" in normalization_reason:
        return "抖音URL解析失败"
    if "抖音标题非真实作品标题" in normalization_reason:
        return "抖音标题非真实作品标题"
    if "缺少作品ID或链接" in normalization_reason:
        return "缺少作品ID或链接"
    if "冲突" in match_reason:
        return "匹配冲突"
    if match_reason:
        return match_reason
    return "未匹配飞书自有内容"
