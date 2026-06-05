#!/usr/bin/env python3
"""LAN startup helper for the Streamlit workspace."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12"
DEFAULT_START_PORT = 8501
DEFAULT_END_PORT = 8599
LAN_HOST = "0.0.0.0"
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
PYTHON_MIRROR = "https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone"
TOTAL_STEPS = 6


def print_step(number: int, title: str) -> None:
    print(f"\n[{number}/{TOTAL_STEPS}] {title}", flush=True)


def run_command(command: list[str], *, env: dict[str, str], dry_run: bool = False) -> None:
    print("执行命令：" + " ".join(command), flush=True)
    if dry_run:
        print("演练模式：跳过实际执行。", flush=True)
        return
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def startup_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("UV_DEFAULT_INDEX", PYPI_MIRROR)
    env.setdefault("UV_PYTHON_INSTALL_MIRROR", PYTHON_MIRROR)
    return env


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv:
        return uv
    raise RuntimeError("未找到 uv。请从根目录双击启动脚本，它会自动安装 uv。")


def find_available_port(host: str, start_port: int, end_port: int) -> int:
    for port in range(start_port, end_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"端口 {start_port}-{end_port} 都已被占用，请先关闭其中一个服务后再启动。")


def build_python_install_command(uv_executable: str) -> list[str]:
    return [
        uv_executable,
        "python",
        "install",
        PYTHON_VERSION,
        "--mirror",
        PYTHON_MIRROR,
    ]


def build_sync_command(uv_executable: str) -> list[str]:
    return [
        uv_executable,
        "sync",
        "--frozen",
        "--python",
        PYTHON_VERSION,
        "--default-index",
        PYPI_MIRROR,
    ]


def build_streamlit_command(uv_executable: str, port: int) -> list[str]:
    return [
        uv_executable,
        "run",
        "--frozen",
        "--python",
        PYTHON_VERSION,
        "--default-index",
        PYPI_MIRROR,
        "streamlit",
        "run",
        "app.py",
        "--server.address=0.0.0.0",
        f"--server.port={port}",
        "--server.headless=true",
    ]


def local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("223.5.5.5", 80))
            addresses.append(probe.getsockname()[0])
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addresses.append(info[4][0])
    except OSError:
        pass

    unique: list[str] = []
    for address in addresses:
        if address.startswith("127.") or address.startswith("169.254."):
            continue
        if address not in unique:
            unique.append(address)
    return unique


def print_access_urls(port: int) -> None:
    print("\n启动后访问地址如下：", flush=True)
    print(f"- 本机访问：http://127.0.0.1:{port}", flush=True)
    addresses = local_ipv4_addresses()
    if addresses:
        for address in addresses:
            print(f"- 局域网访问：http://{address}:{port}", flush=True)
    else:
        print("- 暂未识别到局域网 IP，请在系统网络设置中查看本机 IP 后加端口访问。", flush=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动局域网 Streamlit 工作台")
    parser.add_argument("--dry-run", action="store_true", help="只演练自查和命令，不真正安装或启动")
    parser.add_argument("--start-port", type=int, default=DEFAULT_START_PORT, help="起始端口，默认 8501")
    parser.add_argument("--end-port", type=int, default=DEFAULT_END_PORT, help="结束端口，默认 8599")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    env = startup_env()

    try:
        print("原生内容投放清洗与复盘工作台 - 局域网一键启动", flush=True)
        print("首次启动需要下载 Python 和依赖，时间较长时请不要关闭窗口。", flush=True)

        print_step(1, "环境检查")
        print(f"项目目录：{PROJECT_ROOT}", flush=True)
        print(f"Python 版本目标：{PYTHON_VERSION}", flush=True)
        print(f"依赖镜像：{PYPI_MIRROR}", flush=True)
        uv = find_uv()
        print(f"已找到 uv：{uv}", flush=True)

        print_step(2, "确认 Python 环境")
        run_command(build_python_install_command(uv), env=env, dry_run=args.dry_run)

        print_step(3, "同步项目依赖")
        run_command(build_sync_command(uv), env=env, dry_run=args.dry_run)

        print_step(4, "检查局域网端口")
        port = find_available_port(LAN_HOST, args.start_port, args.end_port)
        if port != args.start_port:
            print(f"端口 {args.start_port} 已被占用，已自动切换到 {port}。", flush=True)
        else:
            print(f"端口 {port} 可用。", flush=True)

        print_step(5, "准备访问地址")
        print_access_urls(port)
        print("\n请保持这个窗口打开。关闭服务时，回到本窗口按 Ctrl+C。", flush=True)

        print_step(6, "前台启动 Streamlit 服务")
        command = build_streamlit_command(uv, port)
        if args.dry_run:
            run_command(command, env=env, dry_run=True)
            print("\n演练完成，未真正启动服务。", flush=True)
            return 0
        subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)
        print("\n服务已关闭，可以关闭窗口。", flush=True)
        return 0
    except KeyboardInterrupt:
        print("\n收到关闭请求，服务正在退出。可以关闭窗口。", flush=True)
        return 130
    except subprocess.CalledProcessError as exc:
        print(f"\n启动过程失败，命令退出码：{exc.returncode}", flush=True)
        print("请检查上方最后一段错误信息；网络较慢时可以重新双击启动脚本。", flush=True)
        return exc.returncode
    except RuntimeError as exc:
        print(f"\n启动前检查失败：{exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
