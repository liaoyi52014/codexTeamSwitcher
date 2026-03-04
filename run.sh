#!/bin/bash

# Codex Team Switcher 启动脚本
# 使用虚拟环境隔离依赖

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 虚拟环境目录
VENV_DIR="$PROJECT_DIR/venv"

# 创建虚拟环境（如果不存在）
if [ ! -d "$VENV_DIR" ]; then
    echo "创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# 安装依赖
echo "安装依赖..."
pip install --upgrade pip -q
pip install -r "$PROJECT_DIR/requirements.txt" -q

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
python3 src/main.py "$@"
