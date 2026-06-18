"""MiniMax analyzer for high-value content recap."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values
import requests


DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M3"
DEFAULT_IMAGE_LIMIT = 3
DEFAULT_IMAGE_MAX_EDGE = 768
DEFAULT_IMAGE_JPEG_QUALITY = 72
RESULT_KEY_MAP = {
    "一级内容类型": ["一级内容类型", "category_l1", "primaryType", "primary_type"],
    "二级内容类型": ["二级内容类型", "category_l2", "secondaryType", "secondary_type"],
    "B站内容类型": ["B站内容类型", "bilibili_content_type", "bilibiliType"],
    "内容形态": ["内容形态", "content_form", "contentForm"],
    "标题钩子": ["标题钩子", "title_hook", "titleHook"],
    "视觉结构": ["视觉结构", "visual_structure", "visualStructure"],
    "信息密度": ["信息密度", "information_density", "informationDensity"],
    "转化路径": ["转化路径", "conversion_path", "conversionPath"],
    "可复用点": ["可复用点", "reuse_points", "reusePoints"],
    "不建议复用点": ["不建议复用点", "avoid_points", "avoidPoints"],
    "下周期策略建议": ["下周期策略建议", "next_period_strategy", "nextPeriodStrategy"],
    "共性总结": ["共性总结", "summary", "common_summary"],
}


def analyze_top_content_with_minimax(
    job: Mapping[str, object],
    manifest: Mapping[str, object],
    *,
    env: Mapping[str, str] | None = None,
    env_path: Path | None = Path(".env"),
    session: object | None = None,
) -> dict[str, object]:
    values = _load_env(env=env, env_path=env_path)
    api_key = _text(values.get("MINIMAX_API_KEY"))
    if not api_key:
        raise RuntimeError("缺少 MiniMax 配置：MINIMAX_API_KEY")
    base_url = (_text(values.get("MINIMAX_BASE_URL")) or DEFAULT_MINIMAX_BASE_URL).rstrip("/")
    model = _text(values.get("MINIMAX_MODEL")) or DEFAULT_MINIMAX_MODEL
    timeout = _float(values.get("MINIMAX_FETCH_TIMEOUT_SEC"), 90.0)
    client = session or requests.Session()
    response = client.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": _messages(job, manifest, values),
        },
        timeout=timeout,
    )
    if not getattr(response, "ok", False):
        raise RuntimeError(f"MiniMax API {getattr(response, 'status_code', '')}: {getattr(response, 'text', '')}")
    payload = response.json()
    parsed = _parse_response_payload(payload)
    return _normalize_result(parsed)


def _messages(
    job: Mapping[str, object],
    manifest: Mapping[str, object],
    values: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "role": "system",
            "content": "你是投放内容复盘助手，只返回结构化 JSON，不输出额外解释。",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _prompt(job, manifest)},
                *_media_content(manifest, values or {}),
            ],
        },
    ]


def _prompt(job: Mapping[str, object], manifest: Mapping[str, object]) -> str:
    metadata = _metadata(manifest)
    return "\n".join(
        [
            "请根据标题、投放数据和素材画面判断内容类型与投放表现共性。",
            "只返回 JSON，字段必须包含：一级内容类型、二级内容类型、B站内容类型、内容形态、标题钩子、视觉结构、信息密度、转化路径、可复用点、不建议复用点、下周期策略建议、共性总结。",
            "抖音和小红书给一级/二级类型；B站只给 B站内容类型。",
            "",
            f"平台：{_text(job.get('platform')) or _text(manifest.get('platform'))}",
            f"渠道：{_text(job.get('channel'))}",
            f"标题：{_text(job.get('title'))}",
            f"账号：{_text(job.get('account'))}",
            f"链接：{_text(job.get('content_url'))}",
            f"投放指标：{_text(job.get('payload_json')) or _text(job.get('metrics_json'))}",
            f"已有元数据：{json.dumps(metadata, ensure_ascii=False, sort_keys=True)}",
        ]
    )


def _media_content(manifest: Mapping[str, object], values: Mapping[str, str]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    image_limit = max(_int(values.get("MINIMAX_IMAGE_LIMIT"), DEFAULT_IMAGE_LIMIT), 0)
    max_edge = max(_int(values.get("MINIMAX_IMAGE_MAX_EDGE"), DEFAULT_IMAGE_MAX_EDGE), 64)
    quality = min(max(_int(values.get("MINIMAX_IMAGE_JPEG_QUALITY"), DEFAULT_IMAGE_JPEG_QUALITY), 35), 95)
    for path in _image_paths(manifest)[:image_limit]:
        data_url = _file_to_data_url(path, max_edge=max_edge, quality=quality)
        if data_url:
            items.append({"type": "image_url", "image_url": {"url": data_url}})
    return items


def _image_paths(manifest: Mapping[str, object]) -> list[Path]:
    raw_paths: list[object] = []
    raw_paths.append(manifest.get("cover_path") or manifest.get("coverPath"))
    raw_paths.extend(_json_list(manifest.get("screenshots_json") or manifest.get("screenshots")))
    raw_paths.extend(_json_list(manifest.get("frames_json") or manifest.get("frames")))
    paths = []
    for raw in raw_paths:
        text = _text(raw)
        if not text:
            continue
        path = Path(text).expanduser()
        if path.exists() and path.is_file():
            paths.append(path)
    return paths


def _file_to_data_url(path: Path, *, max_edge: int = DEFAULT_IMAGE_MAX_EDGE, quality: int = DEFAULT_IMAGE_JPEG_QUALITY) -> str:
    try:
        data = _compressed_image_bytes(path, max_edge=max_edge, quality=quality)
    except OSError:
        return ""
    if data:
        mime_type = "image/jpeg"
    else:
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _compressed_image_bytes(path: Path, *, max_edge: int, quality: int) -> bytes:
    try:
        from PIL import Image
    except Exception:
        return b""
    try:
        with Image.open(path) as image:
            frame = image.convert("RGB")
            if max(frame.size) > max_edge:
                frame.thumbnail((max_edge, max_edge))
            buffer = BytesIO()
            frame.save(buffer, format="JPEG", quality=quality, optimize=True)
            return buffer.getvalue()
    except Exception:
        return b""


def _parse_response_payload(payload: Mapping[str, object]) -> dict[str, object]:
    content = ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                content = _text(message.get("content"))
    if not content:
        content = _text(payload.get("content") or payload.get("text") or payload.get("result"))
    try:
        parsed = json.loads(content)
    except Exception as exc:
        parsed = _extract_json_object(content)
        if parsed is None:
            raise RuntimeError(f"MiniMax 返回不是 JSON：{content[:200]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("MiniMax 返回 JSON 不是对象。")
    return parsed


def _extract_json_object(content: str) -> dict[str, object] | None:
    decoder = json.JSONDecoder()
    text = _text(content)
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_result(parsed: Mapping[str, object]) -> dict[str, object]:
    result = {target: _first(parsed, aliases) for target, aliases in RESULT_KEY_MAP.items()}
    common_patterns = parsed.get("common_patterns") or parsed.get("共性")
    if common_patterns and not result["共性总结"]:
        if isinstance(common_patterns, list):
            result["共性总结"] = "；".join(_text(item) for item in common_patterns if _text(item))
        else:
            result["共性总结"] = _text(common_patterns)
    result["raw"] = dict(parsed)
    return result


def _first(values: Mapping[str, object], aliases: list[str]) -> str:
    for key in aliases:
        value = _text(values.get(key))
        if value:
            return value
    return ""


def _metadata(manifest: Mapping[str, object]) -> dict[str, object]:
    value = manifest.get("metadata_json") or manifest.get("metadata")
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _load_env(*, env: Mapping[str, str] | None, env_path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_path is not None and Path(env_path).exists():
        values.update({str(key): str(value or "") for key, value in dotenv_values(env_path).items()})
    values.update({key: value for key, value in os.environ.items() if key.startswith("MINIMAX_")})
    if env is not None:
        values.update({str(key): str(value or "") for key, value in env.items()})
    return values


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _int(value: object, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
