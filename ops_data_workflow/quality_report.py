"""Quality reporting for the lightweight recap data mart."""

from __future__ import annotations

import pandas as pd


QUALITY_COLUMNS = ["metric", "value", "count", "total", "status", "note"]


def build_quality_report(canonical: pd.DataFrame, top_content: pd.DataFrame | None = None) -> pd.DataFrame:
    frame = _prepare(canonical)
    top = _prepare(top_content) if top_content is not None else frame.iloc[0:0].copy()
    total = int(len(frame))
    matched = frame["match_status"].eq("已匹配")
    type_present = _type_present(frame)
    rows = [
        {
            "metric": "飞书匹配率",
            "value": _ratio(int(matched.sum()), total),
            "count": int(matched.sum()),
            "total": total,
            "status": "需关注" if total and int(matched.sum()) < total else "通过",
            "note": "分母=清洗后素材合并口径。",
        },
        {
            "metric": "内容类型缺失率",
            "value": _ratio(int((~type_present).sum()), total),
            "count": int((~type_present).sum()),
            "total": total,
            "status": "需补齐" if int((~type_present).sum()) else "通过",
            "note": "抖音/小红书看一级或二级类型，B站看内容类型。",
        },
    ]
    top_spend = _numeric(top, "spend").sum() if not top.empty else 0.0
    top_unmatched_spend = _numeric(top[~top["match_status"].eq("已匹配")], "spend").sum() if not top.empty else 0.0
    rows.append(
        {
            "metric": "Top未匹配消耗占比",
            "value": _ratio(top_unmatched_spend, top_spend),
            "count": int((~top["match_status"].eq("已匹配")).sum()) if not top.empty else 0,
            "total": int(len(top)),
            "status": "需补齐" if top_unmatched_spend else "通过",
            "note": "分母=Top素材池消耗。",
        }
    )
    return pd.DataFrame(rows, columns=QUALITY_COLUMNS)


def _prepare(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None:
        frame = pd.DataFrame()
    prepared = frame.copy()
    for column in [
        "platform",
        "channel",
        "match_status",
        "content_type",
        "category_l1",
        "category_l2",
        "bilibili_content_type",
        "matched_content_type",
        "matched_category_l1",
        "matched_category_l2",
        "matched_bilibili_content_type",
    ]:
        if column not in prepared.columns:
            prepared[column] = ""
        prepared[column] = prepared[column].fillna("").astype(str)
    if "spend" not in prepared.columns:
        prepared["spend"] = 0.0
    return prepared


def _type_present(frame: pd.DataFrame) -> pd.Series:
    platform = frame["platform"].fillna("").astype(str)
    common_type = _nonblank(frame["content_type"]) | _nonblank(frame["matched_content_type"])
    social_type = (
        _nonblank(frame["category_l1"])
        | _nonblank(frame["category_l2"])
        | _nonblank(frame["matched_category_l1"])
        | _nonblank(frame["matched_category_l2"])
    )
    bilibili_type = _nonblank(frame["bilibili_content_type"]) | _nonblank(frame["matched_bilibili_content_type"]) | common_type
    social_present = social_type | common_type
    return social_present.where(platform.isin(["抖音", "小红书"]), bilibili_type.where(platform.eq("B站"), common_type))


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce").fillna(0.0)


def _nonblank(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().ne("")


def _ratio(numerator: object, denominator: object) -> float:
    try:
        den = float(denominator)
        return 0.0 if den == 0 else float(numerator) / den
    except Exception:
        return 0.0
