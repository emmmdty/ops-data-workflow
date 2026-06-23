"""Range-scoped multimodal recap report generation."""

from __future__ import annotations

from pathlib import Path
import ast
from typing import Callable, Mapping

import pandas as pd

from .ai import generate_manual_recap_report, resolve_deepseek_settings
from .manual_recap_evidence import build_manual_recap_evidence
from .minimax_recap import DEFAULT_MINIMAX_MODEL


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
    multimodal_results: pd.DataFrame | None = None,
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
    minimax_report = _minimax_range_report(payload, multimodal_results)
    if minimax_report:
        minimax_report["range_key"] = scope["range_key"]
        minimax_report["range_label"] = scope["range_label"]
        minimax_report["range_definition"] = scope["range_definition"]
        minimax_report["batch_id"] = scope["batch_id"]
        minimax_report["provider"] = "minimax"
        minimax_report["model_identity"] = _minimax_model_identity(env_path)
        return minimax_report
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
    normalized["provider"] = "deepseek_fallback"
    normalized["model_identity"] = _deepseek_model_identity(env_path)
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


def _minimax_model_identity(env_path: Path | None) -> str:
    model = DEFAULT_MINIMAX_MODEL
    try:
        from dotenv import dotenv_values

        values = dotenv_values(env_path) if env_path is not None else {}
        configured_model = _text(values.get("MINIMAX_MODEL"))
        if configured_model:
            model = configured_model
    except Exception:
        pass
    return f"我是 {model}，多模态素材理解模型。"


def _deepseek_model_identity(env_path: Path | None) -> str:
    try:
        settings = resolve_deepseek_settings(env_path)
        model = _text(settings.model)
    except Exception:
        model = ""
    return f"我是 DeepSeek {model}，文本复盘兜底模型。" if model else "我是 DeepSeek，文本复盘兜底模型。"


def _minimax_range_report(payload: Mapping[str, object], multimodal_results: pd.DataFrame | None) -> dict[str, object]:
    if multimodal_results is None or multimodal_results.empty:
        return {}
    top_cases = payload.get("top_content_cases")
    if not isinstance(top_cases, pd.DataFrame) or top_cases.empty:
        return {}
    results = multimodal_results.copy()
    if "content_identity_key" not in results.columns:
        return {}
    results_by_identity = {
        _text(row.get("content_identity_key")): row
        for _, row in results.iterrows()
        if _text(row.get("content_identity_key"))
    }
    if not results_by_identity:
        return {}
    enriched_rows = []
    for _, row in top_cases.iterrows():
        identity = _text(row.get("content_identity_key"))
        result = results_by_identity.get(identity)
        if result is None:
            continue
        merged = row.to_dict()
        for column in [
            "content_form",
            "title_hook",
            "visual_structure",
            "information_density",
            "conversion_path",
            "reuse_points",
            "avoid_points",
            "next_period_strategy",
            "summary",
            "category_l1",
            "category_l2",
            "bilibili_content_type",
        ]:
            merged[f"mm_{column}"] = _text(result.get(column))
        if any(merged.get(f"mm_{column}") for column in ["summary", "reuse_points", "next_period_strategy"]):
            enriched_rows.append(merged)
    if not enriched_rows:
        return {}
    frame = pd.DataFrame(enriched_rows)
    overview_sections = _minimax_overview_sections(frame)
    channels = [_minimax_channel_report(channel, group) for channel, group in frame.groupby("channel", dropna=False, sort=False)]
    channels = [item for item in channels if item]
    return {
        "overview": {
            "report": _join_sentences(_section_items(overview_sections, "核心结论")),
            "next_cycle_direction": _join_sentences(_section_items(overview_sections, "下周期动作")),
            "sections": overview_sections,
        },
        "channels": channels,
        "source": "minimax_multimodal_results",
        "report_item_count": int(len(frame)),
    }


def _minimax_overview_sections(frame: pd.DataFrame) -> list[dict[str, object]]:
    total_spend = _sum_number(frame, "spend")
    total_activations = _sum_number(frame, "activations")
    top = frame.sort_values(["spend", "impressions"], ascending=[False, False]).iloc[0]
    sections = [
        {
            "title": "核心结论",
            "items": [
                f"本范围基于 {len(frame)} 条已完成 Minimax 多模态理解的素材生成，覆盖 {_unique_count(frame, 'channel')} 个渠道，消耗 {total_spend:.0f}，激活 {total_activations:.0f}。",
                f"代表素材「{_text(top.get('title'))}」的多模态结论是：{_text(top.get('mm_summary')) or _text(top.get('mm_reuse_points'))}。",
            ],
        },
        {
            "title": "增量内容",
            "items": _top_text_items(frame, "mm_reuse_points", prefix="可复用方向"),
        },
        {
            "title": "拖累内容",
            "items": _top_text_items(frame, "mm_avoid_points", prefix="需规避问题"),
        },
        {
            "title": "下周期动作",
            "items": _top_text_items(frame, "mm_next_period_strategy", prefix="执行建议"),
        },
    ]
    return _non_empty_sections(sections)


def _minimax_channel_report(channel: object, frame: pd.DataFrame) -> dict[str, object]:
    channel_name = _text(channel) or "未知渠道"
    top = frame.sort_values(["spend", "impressions"], ascending=[False, False]).iloc[0]
    sections = _non_empty_sections(
        [
            {
                "title": "素材表现",
                "items": [
                    f"{channel_name}本范围有 {len(frame)} 条 Minimax 已理解素材，消耗 {_sum_number(frame, 'spend'):.0f}，激活 {_sum_number(frame, 'activations'):.0f}。",
                    f"代表素材「{_text(top.get('title'))}」：{_text(top.get('mm_summary')) or _text(top.get('mm_visual_structure'))}。",
                ],
            },
            {"title": "视觉与钩子", "items": _top_text_items(frame, "mm_title_hook", prefix="标题钩子") + _top_text_items(frame, "mm_visual_structure", prefix="视觉结构")},
            {"title": "可复用点", "items": _top_text_items(frame, "mm_reuse_points", prefix="可复用")},
            {"title": "不建议复用点", "items": _top_text_items(frame, "mm_avoid_points", prefix="规避")},
            {"title": "执行动作", "items": _top_text_items(frame, "mm_next_period_strategy", prefix="动作")},
        ]
    )
    return {
        "channel": channel_name,
        "analysis": _join_sentences(_section_items(sections, "素材表现")),
        "next_cycle_direction": _join_sentences(_section_items(sections, "执行动作")),
        "sections": sections,
    }


def _top_text_items(frame: pd.DataFrame, column: str, *, prefix: str) -> list[str]:
    if column not in frame.columns:
        return []
    items: list[str] = []
    for _, row in frame.sort_values(["spend", "impressions"], ascending=[False, False]).iterrows():
        text = _clean_report_text(row.get(column))
        title = _text(row.get("title"))
        if not text:
            continue
        item = f"{prefix}："
        if title:
            item += f"「{title}」"
        item += text
        if item not in items:
            items.append(item)
        if len(items) >= 4:
            break
    return items


def _section_items(sections: list[dict[str, object]], title: str) -> list[str]:
    for section in sections:
        if section.get("title") == title and isinstance(section.get("items"), list):
            return [_text(item) for item in section["items"] if _text(item)]
    return []


def _non_empty_sections(sections: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for section in sections:
        items = [_text(item) for item in section.get("items", []) if _text(item)]
        if items:
            result.append({"title": _text(section.get("title")), "items": items})
    return result


def _join_sentences(items: list[str]) -> str:
    return "；".join(item for item in items if item)


def _unique_count(frame: pd.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    return int(frame[column].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique())


def _sum_number(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


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


def _clean_report_text(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            parsed = []
        if isinstance(parsed, list):
            items = [_text(item) for item in parsed if _text(item)]
            if items:
                return "；".join(items[:3])
    return text
