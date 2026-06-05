#!/bin/zsh
set -u

export LANG=zh_CN.UTF-8
export LC_ALL=zh_CN.UTF-8
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export UV_DOWNLOAD_URL=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease
export UV_PYTHON_INSTALL_MIRROR=https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

cd "$(dirname "$0")" || exit 1

finish() {
  code=$?
  echo ""
  if [ "$code" -eq 0 ]; then
    echo "服务已关闭，可以关闭窗口。"
  else
    echo "启动过程异常结束，错误码：$code"
  fi
  echo "按回车键关闭本窗口。"
  read -r _
}
trap finish EXIT

echo "原生内容投放清洗与复盘工作台 - macOS 局域网启动"
echo "首次启动会自动检查 uv、Python 和项目依赖。"

if ! command -v curl >/dev/null 2>&1; then
  echo "未找到 curl，无法自动下载安装工具。请先安装 macOS Command Line Tools 后重试。"
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv，正在从中科大镜像安装 uv..."
  curl -LsSf https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/uv-installer.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv 安装后仍不可用，请重新打开终端或检查网络后再试。"
  exit 1
fi

echo "正在确认 Python 3.12（如缺失会从大陆镜像下载）..."
uv python install 3.12 --mirror "$UV_PYTHON_INSTALL_MIRROR" || exit 1

PYTHON_BIN="$(uv python find 3.12)" || exit 1
"$PYTHON_BIN" scripts/start_lan.py "$@"
