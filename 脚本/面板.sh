#!/bin/bash
# Collaplex 音效控制面板 —— 起本地服务(默认 8789)
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python web/server.py "${1:-8789}"
