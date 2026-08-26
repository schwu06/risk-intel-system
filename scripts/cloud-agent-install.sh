#!/usr/bin/env bash
# 云端开发环境安装脚本：可重复执行（幂等）。
# 依次完成：系统依赖、Python 虚拟环境、项目依赖、.env、SQLite 初始化。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# venv 需要 python3.12-venv；默认镜像未预装。
if ! dpkg -s python3.12-venv >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends python3.12-venv
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# 首次运行时从示例创建 .env；已存在则保留本机密钥。
if [ ! -f .env ]; then
  cp .env.example .env
fi

# 初始化 SQLite 数据库（幂等，不写入演示数据）。
mkdir -p data
.venv/bin/python scripts/init_db.py
