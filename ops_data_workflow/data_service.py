"""Stable service-level tables for Streamlit and exports."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

import pandas as pd


@dataclass(frozen=True)
class OverviewServiceTables:
    channel_overview: pd.DataFrame
    top_share: pd.DataFrame


@dataclass(frozen=True)
class AnalysisStatusTables:
    summary: pd.DataFrame
    top_pool: pd.DataFrame
    match_status: pd.DataFrame
    harvester_status: pd.DataFrame
    multimodal_status: pd.DataFrame


MULTIMODAL_RESULT_FIELDS = [
    "内容形态",
    "一级内容类型",
    "二级内容类型",
    "B站内容类型",
    "标题钩子",
    "视觉结构",
    "信息密度",
    "转化路径",
    "可复用点",
    "不建议复用点",
    "下周期策略建议",
    "共性总结",
]


def build_overview_service_tables(
    canonical: pd.DataFrame,
    top_content: pd.DataFrame | None = None,
    *,
    m: float = 0.0,
    n: float = 0.0,
) -> OverviewServiceTables:
    frame = _prepare(canonical)
    top = _prepare(top_content) if top_content is not None else frame.iloc[0:0].copy()
    channel_overview = _channel_overview(frame, float(m), float(n))
    top_share = _top_share(frame, top)
    return OverviewServiceTables(channel_overview=channel_overview, top_share=top_share)


def build_analysis_status_tables(
    canonical: pd.DataFrame,
    top_content: pd.DataFrame | None = None,
    *,
    harvester_jobs: pd.DataFrame | None = None,
    harvester_manifests: pd.DataFrame | None = None,
    multimodal_jobs: pd.DataFrame | None = None,
) -> AnalysisStatusTables:
    frame = _prepare(canonical)
    top = _prepare(top_content) if top_content is not None else frame.iloc[0:0].copy()
    match_status = _match_status(frame)
    harvester_status = _harvester_status(harvester_jobs, harvester_manifests)
    multimodal_status = _multimodal_status(multimodal_jobs)
    summary = _analysis_summary(frame, top, harvester_status, multimodal_status)
    return AnalysisStatusTables(
        summary=summary,
        top_pool=top,
        match_status=match_status,
        harvester_status=harvester_status,
        multimodal_status=multimodal_status,
    )


def _channel_overview(frame: pd.DataFrame, m: float, n: float) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "channel",
                "spend",
                "impressions",
                "activations",
                "first_pay_count",
                "content_value",
                "value_per_spend",
                "value_share",
            ]
        )
    grouped = (
        frame.groupby("channel", as_index=False, dropna=False)
        .agg(
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
        )
        .sort_values("spend", ascending=False)
    )
    grouped["content_value"] = grouped["activations"] * m + grouped["first_pay_count"] * n
    total_value = float(grouped["content_value"].sum())
    grouped["value_per_spend"] = grouped.apply(lambda row: _ratio(row["content_value"], row["spend"]), axis=1)
    grouped["value_share"] = grouped["content_value"].map(lambda value: _ratio(value, total_value))
    return grouped.reset_index(drop=True)


def _top_share(frame: pd.DataFrame, top: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "channel",
        "top_spend",
        "channel_spend",
        "top_spend_share",
        "top_impressions",
        "channel_impressions",
        "top_impressions_share",
        "top_activations",
        "channel_activations",
        "top_activations_share",
        "top_first_pay_count",
        "channel_first_pay_count",
        "top_first_pay_share",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    channel = frame.groupby("channel", as_index=False, dropna=False).agg(
        channel_spend=("spend", "sum"),
        channel_impressions=("impressions", "sum"),
        channel_activations=("activations", "sum"),
        channel_first_pay_count=("first_pay_count", "sum"),
    )
    if top.empty:
        top_grouped = pd.DataFrame(columns=["channel", "top_spend", "top_impressions", "top_activations", "top_first_pay_count"])
    else:
        top_grouped = top.groupby("channel", as_index=False, dropna=False).agg(
            top_spend=("spend", "sum"),
            top_impressions=("impressions", "sum"),
            top_activations=("activations", "sum"),
            top_first_pay_count=("first_pay_count", "sum"),
        )
    merged = channel.merge(top_grouped, on="channel", how="left").fillna(0.0)
    merged["top_spend_share"] = merged.apply(lambda row: _ratio(row["top_spend"], row["channel_spend"]), axis=1)
    merged["top_impressions_share"] = merged.apply(lambda row: _ratio(row["top_impressions"], row["channel_impressions"]), axis=1)
    merged["top_activations_share"] = merged.apply(lambda row: _ratio(row["top_activations"], row["channel_activations"]), axis=1)
    merged["top_first_pay_share"] = merged.apply(lambda row: _ratio(row["top_first_pay_count"], row["channel_first_pay_count"]), axis=1)
    return merged[columns]


def _match_status(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["match_status", "item_count", "spend", "impressions", "activations", "first_pay_count", "failure_reason"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    prepared = frame.copy()
    for column in ["match_status", "match_reason"]:
        if column not in prepared.columns:
            prepared[column] = ""
    prepared["match_status"] = prepared["match_status"].fillna("").astype(str).replace({"": "未匹配"})
    grouped = (
        prepared.groupby("match_status", as_index=False, dropna=False)
        .agg(
            item_count=("match_status", "size"),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
            failure_reason=("match_reason", _first_non_blank),
        )
        .sort_values(["spend", "item_count"], ascending=[False, False])
    )
    return grouped[columns].reset_index(drop=True)


def _harvester_status(
    jobs: pd.DataFrame | None,
    manifests: pd.DataFrame | None,
) -> pd.DataFrame:
    columns = [
        "job_id",
        "platform",
        "channel",
        "content_identity_key",
        "title",
        "status",
        "asset_dir",
        "error_message",
    ]
    if jobs is None or jobs.empty:
        return pd.DataFrame(columns=columns)
    prepared = jobs.copy()
    for column in ["job_id", "platform", "channel", "content_identity_key", "title", "status", "error_message"]:
        if column not in prepared.columns:
            prepared[column] = ""
    prepared["asset_dir"] = ""
    if manifests is not None and not manifests.empty and "job_id" in manifests.columns:
        manifest_cols = [column for column in ["job_id", "asset_dir", "error_message"] if column in manifests.columns]
        manifest_frame = manifests[manifest_cols].copy()
        if "asset_dir" not in manifest_frame.columns:
            manifest_frame["asset_dir"] = ""
        if "error_message" not in manifest_frame.columns:
            manifest_frame["error_message"] = ""
        prepared = prepared.merge(manifest_frame, on="job_id", how="left", suffixes=("", "_manifest"))
        prepared["asset_dir"] = prepared.get("asset_dir_manifest", "").fillna("")
        prepared["error_message"] = prepared["error_message"].fillna("").astype(str)
        prepared["error_message"] = prepared.apply(
            lambda row: row["error_message"] or str(row.get("error_message_manifest") or ""),
            axis=1,
        )
    return prepared[columns].reset_index(drop=True)


def _multimodal_status(jobs: pd.DataFrame | None) -> pd.DataFrame:
    columns = [
        "job_id",
        "platform",
        "channel",
        "content_identity_key",
        "title",
        "status",
        "error_message",
        *MULTIMODAL_RESULT_FIELDS,
    ]
    if jobs is None or jobs.empty:
        return pd.DataFrame(columns=columns)
    prepared = jobs.copy()
    for column in ["job_id", "platform", "channel", "content_identity_key", "title", "status", "error_message", "result_json"]:
        if column not in prepared.columns:
            prepared[column] = ""
    result_rows = [_parse_multimodal_result(value) for value in prepared["result_json"]]
    result_frame = pd.DataFrame(result_rows, columns=MULTIMODAL_RESULT_FIELDS)
    combined = pd.concat([prepared.reset_index(drop=True), result_frame], axis=1)
    return combined[columns].reset_index(drop=True)


def _analysis_summary(
    frame: pd.DataFrame,
    top: pd.DataFrame,
    harvester_status: pd.DataFrame,
    multimodal_status: pd.DataFrame,
) -> pd.DataFrame:
    rows = [
        {"metric": "总素材数", "value": int(len(frame)), "failure_reason": ""},
        {"metric": "高价值池素材数", "value": int(len(top)), "failure_reason": ""},
        {"metric": "harvester失败数", "value": _status_count(harvester_status, "failed"), "failure_reason": _first_column_non_blank(harvester_status, "error_message")},
        {"metric": "多模态失败数", "value": _status_count(multimodal_status, "failed"), "failure_reason": _first_column_non_blank(multimodal_status, "error_message")},
    ]
    return pd.DataFrame(rows, columns=["metric", "value", "failure_reason"])


def _prepare(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None:
        frame = pd.DataFrame()
    prepared = frame.copy()
    for column in ["channel", "spend", "impressions", "activations", "first_pay_count"]:
        if column not in prepared.columns:
            prepared[column] = "" if column == "channel" else 0.0
    prepared["channel"] = prepared["channel"].fillna("").astype(str)
    for column in ["spend", "impressions", "activations", "first_pay_count"]:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce").fillna(0.0)
    return prepared


def _parse_multimodal_result(value: object) -> dict[str, str]:
    payload: Mapping[str, object]
    try:
        raw = json.loads(str(value or "{}"))
        payload = raw if isinstance(raw, Mapping) else {}
    except Exception:
        payload = {}
    return {field: str(payload.get(field, "") or "") for field in MULTIMODAL_RESULT_FIELDS}


def _status_count(frame: pd.DataFrame, status: str) -> int:
    if frame.empty or "status" not in frame.columns:
        return 0
    return int(frame["status"].fillna("").astype(str).eq(status).sum())


def _first_column_non_blank(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    return _first_non_blank(frame[column])


def _first_non_blank(values: pd.Series) -> str:
    for value in values:
        text = "" if value is None else str(value).strip()
        if text:
            return text
    return ""


def _ratio(numerator: object, denominator: object) -> float:
    try:
        den = float(denominator)
        return 0.0 if den == 0 else float(numerator) / den
    except Exception:
        return 0.0
