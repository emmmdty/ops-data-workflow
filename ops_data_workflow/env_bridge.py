"""Copy missing runtime configuration from harvester-THS env files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


RUNTIME_ENV_KEYS = [
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_WIKI_TOKEN",
    "FEISHU_SPREADSHEET_TOKEN",
    "FEISHU_OPEN_BASE_URL",
    "FEISHU_SHEET_DOUYIN",
    "FEISHU_SHEET_XHS",
    "FEISHU_SHEET_BILIBILI",
    "FEISHU_SHEET_DOUYIN_HISTORY",
    "FEISHU_SHEET_XHS_HISTORY",
    "FEISHU_SHEET_BILIBILI_HISTORY",
    "MINIMAX_BASE_URL",
    "MINIMAX_MODEL",
    "MINIMAX_API_KEY",
    "MINIMAX_TEXT_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
]


@dataclass(frozen=True)
class EnvCopyResult:
    source_env: Path
    target_env: Path
    copied: list[str]
    kept: list[str]
    missing: list[str]


def copy_missing_runtime_env(
    source_env: Path = Path("/Users/tjk/Documents/Codex/harvester-THS/.env"),
    target_env: Path = Path(".env"),
    *,
    keys: list[str] | None = None,
) -> EnvCopyResult:
    source_env = Path(source_env)
    target_env = Path(target_env)
    keys = keys or RUNTIME_ENV_KEYS
    source_values = {str(key): str(value or "") for key, value in dotenv_values(source_env).items()} if source_env.exists() else {}
    target_values = {str(key): str(value or "") for key, value in dotenv_values(target_env).items()} if target_env.exists() else {}
    copied: list[str] = []
    kept: list[str] = []
    missing: list[str] = []
    additions: list[str] = []
    for key in keys:
        if target_values.get(key):
            kept.append(key)
            continue
        value = source_values.get(key, "")
        if not value:
            missing.append(key)
            continue
        additions.append(f"{key}={value}")
        target_values[key] = value
        copied.append(key)
    if additions:
        existing = target_env.read_text(encoding="utf-8") if target_env.exists() else ""
        separator = "\n" if existing and not existing.endswith("\n") else ""
        target_env.parent.mkdir(parents=True, exist_ok=True)
        target_env.write_text(existing + separator + "\n".join(additions) + "\n", encoding="utf-8")
    return EnvCopyResult(source_env=source_env, target_env=target_env, copied=copied, kept=kept, missing=missing)
