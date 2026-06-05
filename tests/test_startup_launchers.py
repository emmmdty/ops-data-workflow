import importlib.util
import socket
import stat
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_SCRIPT = REPO_ROOT / "scripts" / "start_lan.py"
MAC_LAUNCHER = REPO_ROOT / "启动局域网.command"
WINDOWS_LAUNCHER = REPO_ROOT / "启动局域网.cmd"


def load_startup_module():
    spec = importlib.util.spec_from_file_location("start_lan", STARTUP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_port_selection_uses_8501_when_available():
    start_lan = load_startup_module()

    assert start_lan.find_available_port("127.0.0.1", 8501, 8501) == 8501


def test_port_selection_moves_to_next_port_when_8501_is_occupied():
    start_lan = load_startup_module()
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 8501))
    occupied.listen(1)
    try:
        assert start_lan.find_available_port("127.0.0.1", 8501, 8503) == 8502
    finally:
        occupied.close()


def test_port_selection_errors_when_range_is_exhausted():
    start_lan = load_startup_module()
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 8501))
    occupied.listen(1)
    try:
        with pytest.raises(RuntimeError, match="8501-8501"):
            start_lan.find_available_port("127.0.0.1", 8501, 8501)
    finally:
        occupied.close()


def test_streamlit_command_uses_lan_host_and_selected_port():
    start_lan = load_startup_module()

    command = start_lan.build_streamlit_command("uv", 8502)

    assert command == [
        "uv",
        "run",
        "--frozen",
        "--python",
        "3.12",
        "--default-index",
        start_lan.PYPI_MIRROR,
        "streamlit",
        "run",
        "app.py",
        "--server.address=0.0.0.0",
        "--server.port=8502",
        "--server.headless=true",
    ]


def test_mac_launcher_is_executable_and_uses_utf8_mirrors_and_helper():
    content = MAC_LAUNCHER.read_text(encoding="utf-8")
    mode = MAC_LAUNCHER.stat().st_mode

    assert mode & stat.S_IXUSR
    assert "LANG=zh_CN.UTF-8" in content
    assert "LC_ALL=zh_CN.UTF-8" in content
    assert "UV_DOWNLOAD_URL=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease" in content
    assert "UV_PYTHON_INSTALL_MIRROR=https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone" in content
    assert "scripts/start_lan.py" in content


def test_windows_launcher_uses_utf8_mirrors_and_helper():
    content = WINDOWS_LAUNCHER.read_text(encoding="utf-8")

    assert "chcp 65001" in content
    assert "PYTHONUTF8=1" in content
    assert "PYTHONIOENCODING=utf-8" in content
    assert "uv-installer.ps1" in content
    assert "UV_DOWNLOAD_URL=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease" in content
    assert "UV_PYTHON_INSTALL_MIRROR=https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone" in content
    assert "scripts/start_lan.py" in content
