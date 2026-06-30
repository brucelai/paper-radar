#!/bin/bash
# 待同步提醒 wrapper（host cron）。token/ntfy 取自專案根目錄的 .env。
set -e
cd "$(dirname "$0")"
export PATH=/usr/bin:/usr/local/bin:$PATH
set -a; source .env; set +a
./venv/bin/python notify_pending.py
