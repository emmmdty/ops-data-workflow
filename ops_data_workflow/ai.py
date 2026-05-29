"""DeepSeek analysis generation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd
from dotenv import dotenv_values


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_SECONDS = 60.0
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str
    base_url: str
    model: str
    checked_paths: list[str]
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def public_status(self) -> str:
        if self.configured:
            return f"DeepSeek 已配置（来源：{self.source}；模型：{self.model}）。"
        checked = "；".join(self.checked_paths) if self.checked_paths else "未检查到 .env 路径"
        return f"DeepSeek 未配置：未配置 DEEPSEEK_API_KEY。已检查：{checked}"


def generate_ai_summary(
    total_summary: pd.DataFrame,
    category_summary: pd.DataFrame,
    top_content_items: pd.DataFrame,
    account_audit: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    comparison_note: str,
    env_path: Optional[Path] = None,
    platform_summary: Optional[pd.DataFrame] = None,
    platform_category_summary: Optional[pd.DataFrame] = None,
    external_context: Optional[object] = None,
) -> str:
    settings = resolve_deepseek_settings(env_path)
    if not settings.configured:
        return (
            f"{settings.public_status}，已使用本地规则生成数据摘要。\n\n"
            + _build_local_summary(total_summary, platform_summary, channel_comparison, external_context)
        )

    payload = _build_payload(
        total_summary,
        category_summary,
        top_content_items,
        account_audit,
        channel_comparison,
        comparison_note,
        platform_summary,
        platform_category_summary,
        external_context,
    )
    prompt = (
        "你是原生内容投放数据分析助手。只能使用下面 JSON 中的数字和文本，"
        "不要编造未提供的数据。外部背景只能作为可能影响因素，不能写成确定因果。"
        "主题是“不同渠道、不同栏目题材的转化率分析并针对渠道进行选题定点投流”。"
        "请用中文输出：总体分析、分渠道分析、栏目题材转化差异、定点投流建议。"
        "总体分析必须覆盖总消耗、总曝光、激活数、激活成本、付费数、付费成本的升降和可能原因。"
        "分渠道分析必须逐个渠道覆盖这六项指标。\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=DEFAULT_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": "你只基于给定结构化数据写投放分析报告。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        return f"DeepSeek 结论生成失败：{type(exc).__name__}: {exc}"


def match_missing_categories(
    items: pd.DataFrame,
    category_library: list[str],
    env_path: Optional[Path] = None,
) -> Mapping[int, str]:
    settings = resolve_deepseek_settings(env_path)
    if not settings.configured or items.empty or not category_library:
        return {}

    payload = {
        "category_library": category_library,
        "items": [
            {
                "index": int(index),
                "channel": _json_text(row.get("channel", "")),
                "title": _json_text(row.get("title", "")),
                "account": _json_text(row.get("account", "")),
                "source_file": _json_text(row.get("source_file", "")),
            }
            for index, row in items.head(200).iterrows()
        ],
    }
    prompt = (
        "请为缺失内容类别的投放内容匹配类别。只能从 category_library 中选择，不能新增类别。"
        "返回严格 JSON 对象，格式为 {\"matches\":[{\"index\":0,\"category\":\"类别\"}]}。"
        f"\n\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=DEFAULT_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": "你只做内容类别匹配，并且只能返回 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        data = _parse_json_object(content)
    except Exception:
        return {}

    allowed = set(category_library)
    matches: dict[int, str] = {}
    for item in data.get("matches", []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        category = str(item.get("category", "")).strip()
        if category in allowed:
            matches[index] = category
    return matches


def group_topic_labels(
    items: pd.DataFrame,
    env_path: Optional[Path] = None,
) -> Mapping[int, str]:
    """Use DeepSeek to assign concise topic labels to current drilldown rows."""
    settings = resolve_deepseek_settings(env_path)
    if not settings.configured or items.empty:
        return {}

    payload = {
        "items": [
            {
                "index": int(index),
                "channel": _json_text(row.get("channel", "")),
                "title": _json_text(row.get("title", "")),
                "content_id": _json_text(row.get("content_id", "")),
                "material_id": _json_text(row.get("material_id", "")),
                "content_type": _json_text(row.get("content_type", "")),
                "category_l2": _json_text(row.get("category_l2", "")),
                "category_l3": _json_text(row.get("category_l3", "")),
                "spend": _json_number(row.get("spend", 0)),
                "activations": _json_number(row.get("activations", 0)),
                "first_pay_count": _json_number(row.get("first_pay_count", 0)),
            }
            for index, row in items.head(200).iterrows()
        ],
    }
    prompt = (
        "请把这些投放内容归纳成适合运营复盘的三级题材。"
        "只能根据标题、内容类型、已有栏目题材和内容标识命名，不要编造指标。"
        "不要直接复用原标题、整句标题、内容 ID 或单条视频标题。"
        "请把相似创意归并成少量主题，通常 4-8 个主题即可。"
        "相似标题必须合并为同一个简短题材名，题材名不超过12个中文字符。"
        "返回严格 JSON 对象，格式为 {\"topics\":[{\"index\":0,\"topic\":\"短线交易\"}]}。"
        f"\n\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=DEFAULT_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": "你只做投放内容题材归纳，并且只能返回 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        data = _parse_json_object(content)
    except Exception:
        return {}

    labels: dict[int, str] = {}
    for item in data.get("topics", []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        topic = str(item.get("topic", "")).strip()
        if topic:
            labels[index] = topic[:40]
    return labels


def resolve_deepseek_settings(env_path: Optional[Path] = None) -> DeepSeekSettings:
    candidates = _candidate_env_paths(env_path)
    checked_paths = [str(path) for path in candidates]
    env_files = [(path, _load_env(path)) for path in candidates if path.exists()]

    api_key, source = _resolve_value("DEEPSEEK_API_KEY", env_files, "")
    base_url, _ = _resolve_value("DEEPSEEK_BASE_URL", env_files, DEFAULT_BASE_URL)
    model, _ = _resolve_value("DEEPSEEK_MODEL", env_files, DEFAULT_MODEL)
    return DeepSeekSettings(
        api_key=api_key,
        base_url=base_url,
        model=model,
        checked_paths=checked_paths,
        source=source,
    )


def _build_payload(
    total_summary: pd.DataFrame,
    category_summary: pd.DataFrame,
    top_content_items: pd.DataFrame,
    account_audit: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    comparison_note: str,
    platform_summary: Optional[pd.DataFrame] = None,
    platform_category_summary: Optional[pd.DataFrame] = None,
    external_context: Optional[object] = None,
) -> dict[str, Any]:
    return {
        "total_summary": _records(total_summary),
        "channel_summary": _records(platform_summary if platform_summary is not None else pd.DataFrame()),
        "channel_category_topic_summary": _records(
            platform_category_summary.head(50) if platform_category_summary is not None else pd.DataFrame()
        ),
        "top_categories": _records(category_summary.head(15)),
        "top_content_items": _records(top_content_items.head(50)),
        "account_audit": _records(account_audit),
        "channel_comparison": _records(channel_comparison),
        "comparison_note": comparison_note,
        "external_context": _external_context_payload(external_context),
    }


def _build_local_summary(
    total_summary: pd.DataFrame,
    platform_summary: Optional[pd.DataFrame],
    channel_comparison: pd.DataFrame,
    external_context: Optional[object],
) -> str:
    lines = ["## 总体分析"]
    total = total_summary[total_summary["channel"].fillna("").astype(str).eq("总计")].head(1) if "channel" in total_summary.columns else total_summary.head(1)
    total_row = total.iloc[0] if not total.empty else pd.Series(dtype=object)
    lines.append(
        "- 本周期："
        f"总消耗 {_fmt_number(total_row.get('spend'), 0)}、总曝光 {_fmt_number(total_row.get('impressions'), 0)}、"
        f"激活数 {_fmt_number(total_row.get('activations'), 0)}、激活成本 {_fmt_number(total_row.get('activation_cost'), 1)}、"
        f"付费数 {_fmt_number(total_row.get('first_pay_count'), 0)}、付费成本 {_fmt_number(total_row.get('first_pay_cost'), 1)}。"
    )
    comparison = channel_comparison.copy() if channel_comparison is not None else pd.DataFrame()
    if not comparison.empty and "channel" in comparison.columns:
        total_change = comparison[comparison["channel"].fillna("").astype(str).eq("总计")].head(1)
        if not total_change.empty:
            change = total_change.iloc[0]
            lines.append(
                "- 环比："
                f"消耗 {_fmt_change(change.get('spend_change_rate'))}、曝光 {_fmt_change(change.get('impressions_change_rate'))}、"
                f"激活 {_fmt_change(change.get('activations_change_rate'))}、激活成本 {_fmt_change(change.get('activation_cost_change_rate'))}、"
                f"付费 {_fmt_change(change.get('first_pay_count_change_rate'))}、付费成本 {_fmt_change(change.get('first_pay_cost_change_rate'))}。"
            )
    lines.append(f"- 外部背景：{_external_context_text(external_context)}")
    lines.append("## 分渠道分析")
    channels = platform_summary if platform_summary is not None else pd.DataFrame()
    if channels.empty:
        lines.append("- 暂无渠道明细。")
    else:
        for _, row in channels.head(20).iterrows():
            channel = str(row.get("channel", row.get("platform", "")) or "").strip() or "未知渠道"
            lines.append(
                f"- **{channel}**：消耗 {_fmt_number(row.get('spend'), 0)}、曝光 {_fmt_number(row.get('impressions'), 0)}、"
                f"激活 {_fmt_number(row.get('activations'), 0)}、激活成本 {_fmt_number(row.get('activation_cost'), 1)}、"
                f"付费 {_fmt_number(row.get('first_pay_count'), 0)}、付费成本 {_fmt_number(row.get('first_pay_cost'), 1)}。"
            )
    return "\n".join(lines)


def _external_context_payload(external_context: Optional[object]) -> dict[str, Any]:
    if external_context is None:
        return {"summary": "", "sources": []}
    if isinstance(external_context, Mapping):
        return {
            "summary": str(external_context.get("summary", "") or ""),
            "sources": list(external_context.get("sources", []) or []),
        }
    return {
        "summary": str(getattr(external_context, "summary", "") or ""),
        "sources": list(getattr(external_context, "sources", []) or []),
    }


def _external_context_text(external_context: Optional[object]) -> str:
    payload = _external_context_payload(external_context)
    return payload["summary"] or "未取到外部背景，以下判断仅基于站内投放数据。"


def _fmt_number(value: object, decimals: int) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "暂无"
    if decimals <= 0:
        return f"{float(number):,.0f}"
    return f"{float(number):,.{decimals}f}".rstrip("0").rstrip(".")


def _fmt_change(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "暂无"
    sign = "+" if float(number) > 0 else ""
    return f"{sign}{float(number) * 100:.1f}%"


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.copy().astype(object)
    clean = clean.where(pd.notna(clean), "")
    return clean.to_dict(orient="records")


def _json_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _json_number(value: object) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(number) else float(number)


def _candidate_env_paths(env_path: Optional[Path]) -> list[Path]:
    candidates: list[Path] = []
    if env_path:
        path = Path(env_path)
        if path.is_absolute():
            candidates.append(path)
            return candidates
        else:
            candidates.append(Path.cwd() / path)
            candidates.append(PROJECT_ROOT / path)
    candidates.extend([PROJECT_ROOT / ".env", Path.cwd() / ".env"])

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(candidate.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def _resolve_value(
    key: str,
    env_files: list[tuple[Path, dict[str, str]]],
    default: str,
) -> tuple[str, str]:
    env_value = os.environ.get(key)
    if env_value:
        return env_value, "环境变量"
    for path, values in env_files:
        value = values.get(key, "")
        if value:
            return value, str(path)
    return default, "默认值" if default else "未配置"


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _load_env(env_path: Optional[Path]) -> dict[str, str]:
    path = Path(env_path or ".env")
    if not path.exists():
        return {}
    return {str(key): str(value or "") for key, value in dotenv_values(path).items()}
