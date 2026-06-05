@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "UV_DOWNLOAD_URL=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease"
set "UV_PYTHON_INSTALL_MIRROR=https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone"
set "UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"

cd /d "%~dp0"

echo 原生内容投放清洗与复盘工作台 - Windows 局域网启动
echo 首次启动会自动检查 uv、Python 和项目依赖。
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo 未找到 uv，正在从中科大镜像安装 uv...
  where powershell >nul 2>nul
  if errorlevel 1 (
    echo 未找到 PowerShell，无法自动安装 uv。请先安装 PowerShell 后重试。
    goto fail
  )
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_DOWNLOAD_URL='https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease'; irm 'https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/uv-installer.ps1' | iex"
  set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
)

where uv >nul 2>nul
if errorlevel 1 (
  echo uv 安装后仍不可用，请重新打开命令行或检查网络后再试。
  goto fail
)

echo 正在确认 Python 3.12（如缺失会从大陆镜像下载）...
uv python install 3.12 --mirror "%UV_PYTHON_INSTALL_MIRROR%"
if errorlevel 1 goto fail

set "PYTHON_BIN="
for /f "delims=" %%P in ('uv python find 3.12') do set "PYTHON_BIN=%%P"
if "%PYTHON_BIN%"=="" (
  echo 未能定位 Python 3.12，请检查上方错误信息。
  goto fail
)

"%PYTHON_BIN%" "scripts/start_lan.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:fail
set "EXIT_CODE=1"

:finish
echo.
if "%EXIT_CODE%"=="0" (
  echo 服务已关闭，可以关闭窗口。
) else (
  echo 启动过程异常结束，错误码：%EXIT_CODE%
)
pause
exit /b %EXIT_CODE%
