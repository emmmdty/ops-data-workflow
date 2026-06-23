"""Copy missing runtime configuration from harvester-THS env files."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

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
    "FEISHU_SHEET_STEP15_FILTERED",
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


def resolve_harvester_root(
    *,
    project_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    root = Path(project_root) if project_root is not None else Path.cwd()
    values = env if env is not None else _runtime_env_values(root)
    configured = str(values.get("HARVESTER_ROOT", "")).strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = root / configured_path
        return configured_path.resolve()
    return (root.resolve().parent / "harvester-THS").resolve()


def resolve_harvester_env_path(
    *,
    project_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    return resolve_harvester_root(project_root=project_root, env=env) / ".env"


def copy_missing_runtime_env(
    source_env: Path | None = None,
    target_env: Path = Path(".env"),
    *,
    keys: list[str] | None = None,
) -> EnvCopyResult:
    source_env = Path(source_env) if source_env is not None else resolve_harvester_env_path()
    target_env = Path(target_env)
    keys = keys or RUNTIME_ENV_KEYS
    source_values = {str(key): str(value or "") for key, value in dotenv_values(source_env).items()} if source_env.exists() else {}
    target_values = {str(key): str(value or "") for key, value in dotenv_values(target_env).items()} if target_env.exists() else {}
    copied: list[str] = []
    kept: list[str] = []
    missing: list[str] = []
    updates: dict[str, str] = {}
    for key in keys:
        has_source_value = key in source_values
        value = source_values.get(key, "")
        if not has_source_value:
            if target_values.get(key):
                kept.append(key)
                continue
            missing.append(key)
            continue
        if target_values.get(key) == value:
            kept.append(key)
            continue
        if target_values.get(key) and not key.startswith("FEISHU_"):
            kept.append(key)
            continue
        updates[key] = value
        target_values[key] = value
        copied.append(key)
    if updates:
        existing = target_env.read_text(encoding="utf-8") if target_env.exists() else ""
        target_env.parent.mkdir(parents=True, exist_ok=True)
        target_env.write_text(_merge_env_text(existing, updates), encoding="utf-8")
    return EnvCopyResult(source_env=source_env, target_env=target_env, copied=copied, kept=kept, missing=missing)


def _runtime_env_values(project_root: Path) -> dict[str, str]:
    values = {str(key): str(value or "") for key, value in dotenv_values(Path(project_root) / ".env").items()}
    values.update({key: value for key, value in os.environ.items() if key == "HARVESTER_ROOT"})
    return values


def _merge_env_text(existing: str, updates: Mapping[str, str]) -> str:
    lines = existing.splitlines()
    seen: set[str] = set()
    merged: list[str] = []
    for line in lines:
        key = _env_line_key(line)
        if key in updates:
            merged.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            merged.append(line)
    additions = [f"{key}={value}" for key, value in updates.items() if key not in seen]
    if additions:
        if merged and merged[-1] != "":
            merged.append("")
        merged.extend(additions)
    if not merged:
        merged = additions
    return "\n".join(merged) + "\n"


def _env_line_key(line: str) -> str:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        return ""
    key = line.split("=", 1)[0].strip()
    return key if key else ""
