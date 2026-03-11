@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM Codex Team Switcher Windows 启动脚本

set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

REM 虚拟环境目录
set "VENV_DIR=%PROJECT_DIR%\venv"
set "PYTHON_BIN=python"

where %PYTHON_BIN% >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo 错误: 未找到 Python，请确保已安装 Python 并添加到 PATH
    exit /b 1
)

REM 创建虚拟环境（如果不存在）
if not exist "%VENV_DIR%" (
    echo 创建虚拟环境: %VENV_DIR%
    %PYTHON_BIN% -m venv "%VENV_DIR%"
)

REM 激活虚拟环境
call "%VENV_DIR%\Scripts\activate.bat"

REM 安装依赖
echo 安装依赖...
python -m pip install --upgrade pip
python -m pip install -r "%PROJECT_DIR%\requirements.txt"

REM 检查配置文件
if not exist "%PROJECT_DIR%\config.yaml" (
    if exist "%PROJECT_DIR%\config.example.yaml" (
        echo 创建配置文件...
        copy "%PROJECT_DIR%\config.example.yaml" "%PROJECT_DIR%\config.yaml"
        echo 请先编辑 config.yaml 填入你的配置
        echo 配置文件: %PROJECT_DIR%\config.yaml
    )
)

REM 启动应用
echo.
echo 启动 Codex Team Switcher...
echo 管理界面: http://localhost:18080
echo 代理服务: http://localhost:18888
echo.

cd /d "%PROJECT_DIR%"
python src\main.py %*

endlocal
