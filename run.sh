#!/usr/bin/env bash

set -euo pipefail

# Codex Team Switcher 启动脚本
# 使用虚拟环境隔离依赖

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 虚拟环境目录
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "错误: 未找到 Python 可执行文件: $PYTHON_BIN"
    exit 1
fi

# 创建虚拟环境（如果不存在）
if [ ! -d "$VENV_DIR" ]; then
    echo "创建虚拟环境: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# 安装依赖
echo "安装依赖..."
python -m pip install --upgrade pip
python -m pip install -r "$PROJECT_DIR/requirements.txt"

# 检查配置文件
if [ ! -f "$PROJECT_DIR/config.yaml" ]; then
    echo "创建配置文件..."
    cp "$PROJECT_DIR/config.example.yaml" "$PROJECT_DIR/config.yaml"
    echo "请先编辑 config.yaml 填入你的 team token"
    echo "配置文件: $PROJECT_DIR/config.yaml"
fi

# 启动应用
echo "启动 Codex Team Switcher..."
echo "管理界面: http://localhost:18080"
echo "代理服务: http://localhost:18888"
echo ""

cd "$PROJECT_DIR"
exec python src/main.py "$@"
