"""Range-scoped LLM recap report generation."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from .ai import generate_manual_recap_report
from .manual_recap_evidence import build_manual_recap_evidence


RangeReportGenerator = Callable[..., dict[str, object]]


def build_range_recap_payload(
    *,
    batch_id: str,
    range_key: str,
    range_label: str,
    range_definition: str,
    top_pool: pd.DataFrame,
    period_totals: pd.DataFrame | None = None,
) -> dict[str, object]:
    if top_pool is None or top_pool.empty:
        raise ValueError("当前分级范围没有可用于生成报告的素材。")
    pool = top_pool.copy()
    for column in ["channel", "platform", "title", "content_identity_key"]:
        if column not in pool.columns:
            pool[column] = ""
    for column in ["spend", "impressions", "activations", "first_pay_count"]:
        if column not in pool.columns:
            pool[column] = 0.0
        pool[column] = pd.to_numeric(pool[column], errors="coerce").fillna(0.0)
    channel_summary = (
        pool.groupby(["channel", "platform"], dropna=False)
        .agg(
            item_count=("content_identity_key", "size"),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            activations=("activations", "sum"),
            first_pay_count=("first_pay_count", "sum"),
        )
        .reset_index()
        .sort_values(["spend", "impressions"], ascending=[False, False])
    )
    top_cases = pool.sort_values(["spend", "impressions"], ascending=[False, False]).head(50).reset_index(drop=True)
    evidence = build_manual_recap_evidence(
        current_items=pool,
        channel_comparison=pd.DataFrame(),
        top_content_cases=top_cases,
    )
    scope = {
        "batch_id": str(batch_id or ""),
        "range_key": str(range_key or ""),
        "range_label": str(range_label or ""),
        "range_definition": str(range_definition or ""),
        "item_count": int(len(pool)),
    }
    return {
        "scope": scope,
        "total_summary": _total_summary(pool, period_totals),
        "channel_summary": channel_summary,
        "top_content_cases": top_cases,
        **evidence,
    }


def generate_range_recap_report(
    *,
    batch_id: str,
    range_key: str,
    range_label: str,
    range_definition: str,
    top_pool: pd.DataFrame,
    period_totals: pd.DataFrame | None = None,
    period_level: str = "week",
    env_path: Path | None = None,
    report_generator: RangeReportGenerator | None = None,
) -> dict[str, object]:
    payload = build_range_recap_payload(
        batch_id=batch_id,
        range_key=range_key,
        range_label=range_label,
        range_definition=range_definition,
        top_pool=top_pool,
        period_totals=period_totals,
    )
    scope = payload["scope"]
    generator = report_generator or generate_manual_recap_report
    report = generator(
        total_summary=payload["total_summary"],
        platform_summary=payload["channel_summary"],
        channel_comparison=pd.DataFrame(),
        top_content_cases=payload["top_content_cases"],
        overview_recommendations=(
            f"报告范围：{scope['range_label']}。范围定义：{scope['range_definition']}。"
            "只分析该范围内素材，不扩展到其他分级。"
        ),
        change_driver_context=payload["change_driver_summary"],
        historical_content_context=payload["historical_content_context"],
        period_level=period_level,
        env_path=env_path,
    )
    if not isinstance(report, Mapping):
        report = {}
    normalized = dict(report)
    normalized["range_key"] = scope["range_key"]
    normalized["range_label"] = scope["range_label"]
    normalized["range_definition"] = scope["range_definition"]
    normalized["batch_id"] = scope["batch_id"]
    return normalized


def _total_summary(pool: pd.DataFrame, period_totals: pd.DataFrame | None) -> pd.DataFrame:
    spend = float(pool["spend"].sum())
    impressions = float(pool["impressions"].sum())
    activations = float(pool["activations"].sum())
    first_pay_count = float(pool["first_pay_count"].sum())
    return pd.DataFrame(
        [
            {
                "channel": "总计",
                "spend": spend,
                "impressions": impressions,
                "activations": activations,
                "first_pay_count": first_pay_count,
                "activation_cost": spend / activations if activations else 0.0,
                "first_pay_cost": spend / first_pay_count if first_pay_count else 0.0,
                "scope_total_source": "range_pool",
                "period_channel_count": 0 if period_totals is None else int(len(period_totals)),
            }
        ]
    )
