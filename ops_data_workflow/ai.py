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
OVERVIEW_RECAP_SECTION_TITLES = ("核心结论", "变化来源", "增量内容", "拖累内容", "下周期动作")
CHANNEL_RECAP_SECTION_TITLES = ("素材表现", "题材表现", "内容类型表现", "归因分析", "执行动作")
OVERVIEW_RECAP_SECTION_ALIASES = {
    "核心判断": "核心结论",
    "数据证据": "变化来源",
    "原因判断": "变化来源",
}
CHANNEL_RECAP_SECTION_ALIASES = {
    "表现判断": "素材表现",
    "有效素材": "素材表现",
    "题材/内容类型": "内容类型表现",
    "原因判断": "归因分析",
    "下一周期执行动作": "执行动作",
}
VISIBLE_EVIDENCE_ID_PATTERN = re.compile(
    r"\[\s*(?:evidence_id\s*[:：]\s*)?(?:overview\.metric|channel\.|gap\.)[^\]]+\]\s*"
)
VISIBLE_EVIDENCE_ID_LABEL_PATTERN = re.compile(
    r"(?m)(^|\n)\s*evidence_id\s*[:：]\s*(?:overview\.metric|channel\.|gap\.)[A-Za-z0-9_.:-]+\s*"
)
VISIBLE_BARE_EVIDENCE_ID_PATTERN = re.compile(
    r"(?m)(^|\n)\s*(?:overview\.metric|channel\.|gap\.)[A-Za-z0-9_.:-]+\s+"
)


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


def generate_local_summary(
    total_summary: pd.DataFrame,
    platform_summary: Optional[pd.DataFrame] = None,
    channel_comparison: Optional[pd.DataFrame] = None,
    external_context: Optional[object] = None,
) -> str:
    """Build the deterministic page summary without calling DeepSeek."""
    return _build_local_summary(
        total_summary,
        platform_summary,
        channel_comparison if channel_comparison is not None else pd.DataFrame(),
        external_context,
    )


def generate_manual_recap_report(
    total_summary: pd.DataFrame,
    platform_summary: pd.DataFrame,
    channel_comparison: pd.DataFrame,
    top_content_cases: pd.DataFrame,
    overview_recommendations: str = "",
    channel_topic_context: Optional[pd.DataFrame] = None,
    change_driver_context: Optional[Mapping[str, Any]] = None,
    historical_content_context: Optional[Mapping[str, Any]] = None,
    period_level: str = "week",
    env_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Generate the manually refreshed period recap as structured JSON."""
    settings = resolve_deepseek_settings(env_path)
    if not settings.configured:
        raise RuntimeError(settings.public_status)

    payload = {
        "total_summary": _records(total_summary),
        "channel_summary": _records(platform_summary),
        "channel_comparison": _records(channel_comparison),
        "top_content_cases": _records(top_content_cases),
        "overview_recommendations": str(overview_recommendations or "").strip(),
        "channel_topic_context": _records(channel_topic_context if channel_topic_context is not None else pd.DataFrame()),
        "change_driver_summary": change_driver_context if isinstance(change_driver_context, Mapping) else {},
        "historical_content_context": historical_content_context if isinstance(historical_content_context, Mapping) else {},
        "period_level": str(period_level or "").strip() or "week",
    }
    prompt = (
        "你是原生内容投放的周期复盘助手。请根据给定 JSON 写一份适合同事执行和对齐的跨周期变化归因复盘，"
        "重点不是展示文字写得多完整，而是清楚传递哪些素材的数据好、为什么好、下个周期可以向哪些方向调整。"
        "必须从数据中进行复盘，内部使用 evidence_id 对齐 change_driver_summary 与 historical_content_context 的证据，"
        "但最终 JSON 不得输出 evidence_id、不得输出方括号证据编号，"
        "不得展示 overview.metric、channel、gap 这类机器编号；"
        "外显内容只写自然业务语言，例如渠道、内容类型、题材、素材、消耗变化、激活变化、成本变化、增量或拖累判断。"
        "再引用 JSON 中的消耗、激活、成本、付费、环比、素材案例和内容类型证据；"
        "不要机械复述数据，禁止编造 JSON 之外的数据、封面或链接，禁止输出未在证据包出现的预算比例、目标阈值或外部原因。"
        "返回内容必须分模块，overview.sections 固定为：核心结论、变化来源、增量内容、拖累内容、下周期动作；"
        "channels[].sections 固定为：素材表现、题材表现、内容类型表现、归因分析、执行动作。"
        "渠道页 AI 只负责执行建议，不要单独输出题材或内容类型分析模块；内容类型数据只作为证据嵌入素材、题材、内容类型、归因或动作中。"
        "每个模块输出 2-4 条短要点；每条短要点必须基于输入 JSON 里的 evidence_id、指标、素材或内容类型证据生成，"
        "但短要点文本不得展示 evidence_id 或 [overview.metric...]、[channel...]、[gap...]；"
        "证据不足时写“当前数据未提供足够证据”。"
        "总览只写一份整体结论和下周期总体方向，不要逐渠道展开，也不要把多个渠道写成竞争关系或排名关系。"
        "分渠道必须逐个渠道单独给出执行建议，覆盖表现判断、有效素材、原因判断、下一周期执行方向或调整。"
        "多个渠道之间不要写成竞争关系；每个渠道只分析本渠道内部哪些素材、题材或内容类型证据支持继续、暂停、复测或调整。"
        "必须明确说明哪些内容带来增量、哪些内容拖累表现；如果无法从 historical_content_context 归因，必须写“当前数据未提供足够证据”。"
        "周周期建议要偏执行的内容，例如下周期具体补哪些素材方向、复测哪些内容、暂停哪些低效方向；"
        "月周期、季度、年度建议要偏策略、方案或预算结构调整，例如内容结构、渠道机制、投放方案、资源分配。"
        "overview_recommendations 和 channel_topic_context 只作为补充证据输入；不要把它们改写成独立的内容类型建议模块。"
        "返回严格 JSON，不要 Markdown，不要代码块。格式："
        "{\"overview\":{\"report\":\"一篇总览复盘正文...\",\"next_cycle_direction\":\"下周期总体方向...\","
        "\"sections\":[{\"title\":\"核心结论\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"变化来源\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"增量内容\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"拖累内容\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"下周期动作\",\"items\":[\"要点1\",\"要点2\"]}]},"
        "\"channels\":[{\"channel\":\"渠道名\",\"analysis\":\"AI 渠道复盘建议...\",\"next_cycle_direction\":\"下一周期执行方向...\","
        "\"sections\":[{\"title\":\"素材表现\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"题材表现\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"内容类型表现\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"归因分析\",\"items\":[\"要点1\",\"要点2\"]},"
        "{\"title\":\"执行动作\",\"items\":[\"要点1\",\"要点2\"]}]}]}。"
        f"\n\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=DEFAULT_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": "你只输出严格 JSON，用中文写周期复盘。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return _normalize_manual_recap_report(_parse_json_object(response.choices[0].message.content or ""))
    except Exception as exc:
        raise RuntimeError(f"手动复盘报告生成失败：{type(exc).__name__}: {exc}") from exc


def match_missing_categories(
    items: pd.DataFrame,
    category_library: list[str],
    env_path: Optional[Path] = None,
) -> Mapping[int, object]:
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
        "请同时给出 0 到 1 的 confidence 和简短 reason。"
        "返回严格 JSON 对象，格式为 {\"matches\":[{\"index\":0,\"category\":\"类别\",\"confidence\":0.86,\"reason\":\"依据\"}]}。"
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
    matches: dict[int, object] = {}
    for item in data.get("matches", []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        category = str(item.get("category", "")).strip()
        if category in allowed:
            confidence = _json_confidence(item.get("confidence"))
            reason = str(item.get("reason", "") or "").strip()[:80]
            matches[index] = {"category": category, "confidence": confidence, "reason": reason}
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


def _json_confidence(value: object) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return 0.65
    return float(max(0.0, min(1.0, number)))


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


def _normalize_manual_recap_report(data: Mapping[str, Any]) -> dict[str, Any]:
    overview = data.get("overview", {}) if isinstance(data, Mapping) else {}
    if not isinstance(overview, Mapping):
        overview = {}
    channels = data.get("channels", []) if isinstance(data, Mapping) else []
    if not isinstance(channels, list):
        channels = []
    overview_report = _strip_visible_evidence_ids(overview.get("report", ""))
    if not overview_report:
        overview_parts = [
            _strip_visible_evidence_ids(overview.get("summary", "")),
            _strip_visible_evidence_ids(overview.get("cause", "")),
        ]
        overview_report = "\n\n".join(part for part in overview_parts if part)
    overview_direction = _strip_manual_recap_direction_label(overview.get("next_cycle_direction", ""))
    if not overview_direction:
        overview_direction = _strip_manual_recap_direction_label(overview.get("action", ""))
    return {
        "overview": {
            "report": overview_report,
            "next_cycle_direction": overview_direction,
            "sections": _normalize_manual_recap_sections(
                overview.get("sections", []),
                OVERVIEW_RECAP_SECTION_TITLES,
                OVERVIEW_RECAP_SECTION_ALIASES,
            ),
            "summary": _strip_visible_evidence_ids(overview.get("summary", "")),
            "cause": _strip_visible_evidence_ids(overview.get("cause", "")),
            "action": _strip_visible_evidence_ids(overview.get("action", "")),
        },
        "channels": [
            {
                "channel": str(item.get("channel", "") or "").strip(),
                "analysis": _channel_analysis_text(item),
                "next_cycle_direction": _strip_manual_recap_direction_label(
                    item.get("next_cycle_direction", "") or item.get("action", "")
                ),
                "sections": _normalize_manual_recap_sections(
                    item.get("sections", []),
                    CHANNEL_RECAP_SECTION_TITLES,
                    CHANNEL_RECAP_SECTION_ALIASES,
                ),
                "summary": _strip_visible_evidence_ids(item.get("summary", "")),
                "cause": _strip_visible_evidence_ids(item.get("cause", "")),
                "action": _strip_visible_evidence_ids(item.get("action", "")),
            }
            for item in channels
            if isinstance(item, Mapping)
        ],
    }


def _normalize_manual_recap_sections(
    value: object,
    allowed_titles: tuple[str, ...],
    title_aliases: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    by_title: dict[str, list[str]] = {}
    for section in value:
        if not isinstance(section, Mapping):
            continue
        title = str(section.get("title", "") or "").strip()
        if title_aliases:
            title = str(title_aliases.get(title, title))
        if title not in allowed_titles:
            continue
        items = _normalize_manual_recap_items(section.get("items", []))
        if items:
            by_title.setdefault(title, []).extend(items)

    return [{"title": title, "items": by_title[title]} for title in allowed_titles if title in by_title]


def _strip_manual_recap_direction_label(value: object) -> str:
    text = _strip_visible_evidence_ids(value)
    for prefix in ("下一周期执行方向", "下周期总体方向", "下一周期方向", "下周期方向"):
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip("：: ").strip()
            break
    return _strip_visible_evidence_ids(text)


def _normalize_manual_recap_items(value: object) -> list[str]:
    if isinstance(value, list):
        candidates = value
    else:
        candidates = str(value or "").splitlines()
    items = []
    for item in candidates:
        text = _strip_visible_evidence_ids(item)
        if text:
            items.append(text)
    return items[:6]


def _channel_analysis_text(item: Mapping[str, Any]) -> str:
    analysis = _strip_visible_evidence_ids(item.get("analysis", ""))
    if analysis:
        return analysis
    parts = [
        _strip_visible_evidence_ids(item.get("summary", "")),
        _strip_visible_evidence_ids(item.get("cause", "")),
    ]
    return "\n\n".join(part for part in parts if part)


def _strip_visible_evidence_ids(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = VISIBLE_EVIDENCE_ID_PATTERN.sub("", text)
    text = VISIBLE_EVIDENCE_ID_LABEL_PATTERN.sub(lambda match: match.group(1) or "", text)
    text = VISIBLE_BARE_EVIDENCE_ID_PATTERN.sub(lambda match: match.group(1) or "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _load_env(env_path: Optional[Path]) -> dict[str, str]:
    path = Path(env_path or ".env")
    if not path.exists():
        return {}
    return {str(key): str(value or "") for key, value in dotenv_values(path).items()}
