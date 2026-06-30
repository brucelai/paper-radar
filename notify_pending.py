#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""待同步提醒：查 D1 actions 內 synced=0 的「可動作」筆數 → ntfy 提醒去跑 /paper-sync。

可動作 = deepread(🔬品質) / content(📚內容) / pdf_key(📎) 任一，或 vote 非空（訓練用）。
（star/zotero 為舊欄，仍計入以相容歷史列。）純 seen=1（只標已看過、無下游動作）不計入提醒。
只有筆數 > 0 才推播。Oracle cron 每日一次。

env: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, NTFY_TOKEN, NTFY_BASE, NTFY_TOPIC
note: D1 查詢走 wrangler（與 /paper-sync 同路；CF Access 擋直接 web API）。
"""
import os, json, subprocess, urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "paper-radar")
NTFY_BASE = os.environ.get("NTFY_BASE", "https://ntfy.example.com")   # JSON 發布要 POST 到 base URL（POST 到 topic 路徑會被 CF 擋 403）
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")
SITE_URL = "https://paper-radar.example.com"

QUERY = """
SELECT
  SUM(CASE WHEN deepread=1 THEN 1 ELSE 0 END) AS quality,
  SUM(CASE WHEN content=1  THEN 1 ELSE 0 END) AS content,
  SUM(CASE WHEN pdf_key IS NOT NULL AND pdf_key<>'' THEN 1 ELSE 0 END) AS pdf,
  SUM(CASE WHEN vote IS NOT NULL AND vote<>'' THEN 1 ELSE 0 END) AS votes,
  SUM(CASE WHEN (deepread=1 OR content=1 OR star=1 OR zotero=1
                 OR (pdf_key IS NOT NULL AND pdf_key<>'')) THEN 1 ELSE 0 END) AS actionable
FROM actions WHERE synced=0
""".strip().replace("\n", " ")


def d1_query():
    out = subprocess.run(
        ["wrangler", "d1", "execute", "paper-radar-db", "--remote", "--json", "--command", QUERY],
        cwd=str(SCRIPT_DIR), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"wrangler failed: {out.stderr[-400:]}")
    # wrangler --json 可能含前置非 JSON 行；取第一個 '[' 起的 JSON
    txt = out.stdout
    data = json.loads(txt[txt.index("["):])
    return data[0]["results"][0]


def ntfy(title, msg):
    if not NTFY_TOKEN:
        print("NTFY_TOKEN 未設，跳過推播"); return
    body = json.dumps({"topic": NTFY_TOPIC, "title": title, "message": msg,
                       "tags": ["books"], "click": SITE_URL}).encode("utf-8")
    req = urllib.request.Request(NTFY_BASE, data=body, method="POST",
        headers={"Authorization": f"Bearer {NTFY_TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (paper-radar)"})
    urllib.request.urlopen(req, timeout=15)


def main():
    r = d1_query()
    n = lambda k: int(r.get(k) or 0)
    actionable, votes = n("actionable"), n("votes")
    print(f"待同步 actionable={actionable}（🔬{n('quality')} 📚{n('content')} "
          f"📎{n('pdf')}）｜vote {votes}")

    if actionable == 0 and votes == 0:
        print("無待同步，不推播"); return

    parts = []
    if n("quality"): parts.append(f"🔬 品質 {n('quality')}")
    if n("content"): parts.append(f"📚 內容 {n('content')}")
    if n("pdf"):     parts.append(f"📎 上傳 {n('pdf')}")
    if votes:        parts.append(f"👍👎 投票 {votes}")
    msg = f"你在論文雷達標了 {actionable} 篇待拉進 Obsidian。\n" + "｜".join(parts) + "\n\n在 PC 跑 /paper-sync 收回。"
    ntfy(f"📚 論文雷達：{actionable} 篇待同步", msg)
    print("已推播")


if __name__ == "__main__":
    main()
