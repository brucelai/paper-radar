#!/bin/bash
# 跨專案 R2 用量監測 wrapper（host cron）。token/ntfy 取自專案根目錄的 .env。
set -e
cd "$(dirname "$0")"
export PATH=/usr/bin:/usr/local/bin:$PATH
set -a; source .env; set +a
./venv/bin/python monitor_r2.py
