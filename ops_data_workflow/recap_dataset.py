"""Build the three core recap tables."""

from __future__ import annotations

import pandas as pd


CLEANED_ASSET_COLUMNS = [
    "周期",
    "平台",
    "渠道",
    "原始标题",
    "标准标题",
    "作品链接",
    "作品ID/BV号",
    "巨量链接",
    "巨量封面链接",
    "巨量素材ID",
    "消耗",
    "曝光",
    "飞书匹配结果",
    "飞书匹配标题",
    "内容类型",
    "是否可分析",
    "不可分析原因",
    "来源文件",
    "来源行号",
]

CONTENT_RECAP_COLUMNS = [
    "周期",
    "平台",
    "渠道",
    "内容类型",
    "素材数",
    "消耗",
    "曝光",
    "高价值素材",
]

UNANALYZABLE_SUMMARY_COLUMNS = [
    "周期",
    "平台",
    "渠道",
    "总素材数",
    "可分析素材数",
    "不可分析素材数",
    "不可分析素材占比",
    "不可分析消耗",
    "不可分析消耗占比",
    "主要原因",
]


def build_cleaned_asset_table(canonical: pd.DataFrame) -> pd.DataFrame:
    frame = canonical.copy()
    result = pd.DataFrame(index=frame.index)
    result["周期"] = _period(frame)
    result["平台"] = _text(frame, "platform")
    result["渠道"] = _text(frame, "channel")
    result["原始标题"] = _first_text(frame, ["original_title", "title"])
    result["标准标题"] = _first_text(frame, ["standard_title", "title"])
    result["作品链接"] = _first_text(frame, ["work_url", "content_url"])
    result["作品ID/BV号"] = _work_identity(frame)
    result["巨量链接"] = _text(frame, "ad_material_url")
    result["巨量封面链接"] = _text(frame, "ad_cover_url")
    result["巨量素材ID"] = _first_text(frame, ["ad_material_id", "material_id"])
    result["消耗"] = _numeric(frame, "spend")
    result["曝光"] = _numeric(frame, "impressions")
    result["飞书匹配结果"] = _text(frame, "analysis_status")
    result["飞书匹配标题"] = _text(frame, "matched_ledger_title")
    result["内容类型"] = _first_text(frame, ["content_category", "category_l2", "matched_content_type", "manual_category"])
    result["是否可分析"] = frame.get("analysis_status", pd.Series("", index=frame.index)).map(lambda value: "是" if str(value) == "可分析" else "否")
    result["不可分析原因"] = _text(frame, "unanalyzable_reason")
    result["来源文件"] = _text(frame, "source_file")
    result["来源行号"] = _text(frame, "source_row")
    return result[CLEANED_ASSET_COLUMNS].reset_index(drop=True)


def build_content_recap_table(asset_table: pd.DataFrame) -> pd.DataFrame:
    if asset_table.empty:
        return pd.DataFrame(columns=CONTENT_RECAP_COLUMNS)
    scoped = asset_table[asset_table["是否可分析"].astype(str).eq("是")].copy()
    if scoped.empty:
        return pd.DataFrame(columns=CONTENT_RECAP_COLUMNS)
    scoped["消耗"] = pd.to_numeric(scoped["消耗"], errors="coerce").fillna(0.0)
    scoped["曝光"] = pd.to_numeric(scoped["曝光"], errors="coerce").fillna(0.0)
    rows = []
    for key, group in scoped.groupby(["周期", "平台", "渠道", "内容类型"], dropna=False, sort=False):
        period, platform, channel, content_type = key
        top = group.sort_values(["消耗", "曝光"], ascending=[False, False]).head(5)
        rows.append(
            {
                "周期": period,
                "平台": platform,
                "渠道": channel,
                "内容类型": content_type,
                "素材数": int(len(group)),
                "消耗": float(group["消耗"].sum()),
                "曝光": float(group["曝光"].sum()),
                "高价值素材": "；".join(str(value) for value in top["标准标题"].tolist() if str(value).strip()),
            }
        )
    return pd.DataFrame(rows, columns=CONTENT_RECAP_COLUMNS)


def build_unanalyzable_summary(asset_table: pd.DataFrame) -> pd.DataFrame:
    if asset_table.empty:
        return pd.DataFrame(columns=UNANALYZABLE_SUMMARY_COLUMNS)
    frame = asset_table.copy()
    frame["消耗"] = pd.to_numeric(frame["消耗"], errors="coerce").fillna(0.0)
    rows = []
    for key, group in frame.groupby(["周期", "平台", "渠道"], dropna=False, sort=False):
        period, platform, channel = key
        total_count = int(len(group))
        total_spend = float(group["消耗"].sum())
        analyzable = group[group["是否可分析"].astype(str).eq("是")]
        not_analyzable = group[~group["是否可分析"].astype(str).eq("是")]
        reasons = not_analyzable["不可分析原因"].replace("", pd.NA).dropna()
        main_reason = str(reasons.value_counts().index[0]) if not reasons.empty else ""
        rows.append(
            {
                "周期": period,
                "平台": platform,
                "渠道": channel,
                "总素材数": total_count,
                "可分析素材数": int(len(analyzable)),
                "不可分析素材数": int(len(not_analyzable)),
                "不可分析素材占比": (len(not_analyzable) / total_count) if total_count else 0.0,
                "不可分析消耗": float(not_analyzable["消耗"].sum()),
                "不可分析消耗占比": (float(not_analyzable["消耗"].sum()) / total_spend) if total_spend else 0.0,
                "主要原因": main_reason,
            }
        )
    return pd.DataFrame(rows, columns=UNANALYZABLE_SUMMARY_COLUMNS)


def _period(frame: pd.DataFrame) -> pd.Series:
    start = _text(frame, "period_start")
    end = _text(frame, "period_end")
    return start.where(start.eq(end) | end.eq(""), start + " 至 " + end)


def _text(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype=object)
    return frame[column].fillna("").astype(str).replace({"nan": "", "None": "", "<NA>": ""})


def _first_text(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series([""] * len(frame), index=frame.index, dtype=object)
    for column in columns:
        values = _text(frame, column)
        mask = result.astype(str).str.strip().eq("") & values.astype(str).str.strip().ne("")
        result.loc[mask] = values.loc[mask]
    return result


def _work_identity(frame: pd.DataFrame) -> pd.Series:
    result = _text(frame, "work_id")
    content_id = _text(frame, "content_id")
    platform = _text(frame, "platform")
    mask = result.astype(str).str.strip().eq("") & platform.ne("抖音") & content_id.astype(str).str.strip().ne("")
    result.loc[mask] = content_id.loc[mask]
    return result


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([0.0] * len(frame), index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
