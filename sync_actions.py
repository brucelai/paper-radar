#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D1 → actions_cache.json：每晚把使用者行為事件拉回主機本地，供 train_model.py 用。

- actions（current state）一定拉；action_log（append-only 歷史）表可能尚未建 → 容錯略過。
- 走 wrangler（與 notify_pending.py 同路；整站在 CF Access 後，擋直接 web API）。
- 主機 token 只需 D1:Read。
"""
import json, subprocess, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUT = SCRIPT_DIR / "actions_cache.json"


def d1(query):
    out = subprocess.run(
        ["wrangler", "d1", "execute", "paper-radar-db", "--remote", "--json",
         "--command", query],
        cwd=str(SCRIPT_DIR), capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"wrangler failed: {out.stderr[-400:]}")
    txt = out.stdout
    return json.loads(txt[txt.index("["):])[0]["results"]


def main():
    cache = {}
    cache["actions"] = d1("SELECT * FROM actions")
    try:
        cache["action_log"] = d1("SELECT * FROM action_log ORDER BY id")
    except Exception as e:
        print(f"  action_log 不可用（未 migration？）：{str(e)[:120]}")
        cache["action_log"] = []
    OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"✓ actions={len(cache['actions'])} action_log={len(cache['action_log'])} → {OUT.name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"✗ sync_actions 失敗：{e}", file=sys.stderr)
        sys.exit(1)
