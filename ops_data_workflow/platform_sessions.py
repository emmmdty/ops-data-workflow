"""Local browser profile management for platform logins."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from .env_bridge import resolve_harvester_root


PLATFORM_PROFILE_DIRS = {
    "douyin": ".douyin-profile",
    "bilibili": ".bilibili-profile",
    "xhs": ".xhs-profile",
}

PLATFORM_LOGIN_URLS = {
    "douyin": "https://www.douyin.com/",
    "bilibili": "https://www.bilibili.com/",
    "xhs": "https://www.xiaohongshu.com/explore",
}


@dataclass(frozen=True)
class LoginProfileResult:
    platform: str
    profile_dir: Path
    source_profile_dir: Path
    bootstrapped: bool


def login_profile_dir(platform: str, project_root: Path = Path(".")) -> Path:
    platform_key = _platform_key(platform)
    return Path(project_root) / PLATFORM_PROFILE_DIRS[platform_key]


def prepare_local_login_profile(
    platform: str,
    *,
    project_root: Path = Path("."),
    harvester_root: Path | None = None,
) -> LoginProfileResult:
    platform_key = _platform_key(platform)
    target = login_profile_dir(platform_key, project_root)
    source_root = Path(harvester_root).expanduser() if harvester_root is not None else resolve_harvester_root(project_root=project_root)
    source = source_root / PLATFORM_PROFILE_DIRS[platform_key]
    bootstrapped = False
    if not target.exists() and source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        bootstrapped = True
    else:
        target.mkdir(parents=True, exist_ok=True)
    return LoginProfileResult(platform=platform_key, profile_dir=target, source_profile_dir=source, bootstrapped=bootstrapped)


def login_url(platform: str) -> str:
    return PLATFORM_LOGIN_URLS[_platform_key(platform)]


def _platform_key(platform: str) -> str:
    value = str(platform or "").strip().lower()
    aliases = {
        "抖音": "douyin",
        "douyin": "douyin",
        "b站": "bilibili",
        "bilibili": "bilibili",
        "小红书": "xhs",
        "xiaohongshu": "xhs",
        "xhs": "xhs",
    }
    if value not in aliases:
        raise ValueError(f"不支持的平台登录：{platform}")
    return aliases[value]
