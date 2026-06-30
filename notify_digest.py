#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""每日高分新篇推播：fetch 後挑「近 N 天新出現 + 分數達門檻 + 未推過」的論文 → ntfy 摘要。

- 門檻：預設取 interest_model.json 的 recommend threshold（高分=推薦級）；--min-score 覆寫。
- 去重：`.digest_state.json` 存已推 item_id，跨日不重推（避免 cron 重跑或隔日再推同篇）。
- 視窗：只看 first_seen 在近 `new_days`（config，預設 2）天內，避免首跑回灌整庫。
- 只有 ≥1 篇未推的新高分才送；顯示上限 12 篇，其餘仍標記已推（避免日後補推），log 丟棄數。
- 由 run.sh 尾端呼叫（fetch 完、同環境有 NTFY_TOKEN）；失敗不影響管線（run.sh 用 || true）。

env: NTFY_TOKEN, NTFY_BASE, NTFY_TOPIC, CLOUDFLARE_ACCOUNT_ID
"""
import os, json, sqlite3, datetime, urllib.request, subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "paper_radar.db"
MODEL_PATH = SCRIPT_DIR / "interest_model.json"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
STATE_PATH = SCRIPT_DIR / ".digest_state.json"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "paper-radar")
NTFY_BASE = os.environ.get("NTFY_BASE", "https://ntfy.example.com")   # JSON 發布要 POST 到 base URL（topic 路徑會被 CF 擋 403）
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")
SITE_URL = "https://paper-radar.example.com"

MAX_SHOW = 12
STATE_KEEP = 800   # state 內保留的已推 item_id 上限（防無限長）


def recommend_threshold():
    try:
        return int(json.loads(MODEL_PATH.read_text(encoding="utf-8"))["thresholds"]["recommend"])
    except Exception:
        return 3


def new_days():
    # 不引 yaml 依賴，純文字撈 new_days
    try:
        for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("new_days:"):
                return int(s.split(":", 1)[1].strip().split("#")[0])
    except Exception:
        pass
    return 2


def d1_engaged_ids():
    """D1 actions 內**任何有紀錄**的 item_id（已看過/投票/星號/sync…＝你在站上已 engage）。
    digest 一律排除這些，避免推已處理過的。查不到(token 無 D1 權限/離線)→回 None，退回只靠 state。"""
    acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    env = dict(os.environ, CLOUDFLARE_ACCOUNT_ID=acc)
    try:
        out = subprocess.run(
            ["wrangler", "d1", "execute", "paper-radar-db", "--remote", "--json",
             "--command", "SELECT item_id FROM actions"],
            cwd=str(SCRIPT_DIR), capture_output=True, text=True, timeout=60, env=env)
        if out.returncode != 0:
            print(f"⚠️ D1 查詢失敗，退回 state-only：{out.stderr[-200:]}"); return None
        txt = out.stdout
        data = json.loads(txt[txt.index("["):])
        return {r["item_id"] for r in data[0]["results"]}
    except Exception as e:
        print(f"⚠️ D1 查詢例外，退回 state-only：{e!r}"); return None


def load_state():
    if STATE_PATH.exists():
        try:
            return set(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("notified", []))
        except Exception:
            return set()
    return set()


def save_state(notified):
    keep = list(notified)[-STATE_KEEP:]
    STATE_PATH.write_text(json.dumps({"notified": keep}, ensure_ascii=False), encoding="utf-8")


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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=None, help="高分門檻（預設=模型 recommend）")
    ap.add_argument("--days", type=int, default=None, help="first_seen 視窗天數（預設=config new_days）")
    ap.add_argument("--force", action="store_true", help="忽略 state，重新挑選（測試用，仍會送）")
    ap.add_argument("--dry", action="store_true", help="只印不推、不寫 state")
    args = ap.parse_args()

    min_score = args.min_score if args.min_score is not None else recommend_threshold()
    days = args.days if args.days is not None else new_days()
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

    con = sqlite3.connect(str(DB_PATH))
    rows = con.execute(
        """SELECT item_id, title, score, source_name, oa_pdf_url, inst_subscribed, doi
           FROM papers WHERE first_seen >= ? AND score >= ?
           ORDER BY score DESC, first_seen DESC""", (since, min_score)).fetchall()
    con.close()

    state = set() if args.force else load_state()
    engaged = d1_engaged_ids() or set()   # 已在站上處理過的，一律排除
    fresh = [r for r in rows if r[0] not in state and r[0] not in engaged]
    print(f"門檻 score>={min_score}, 視窗 {days}d (>= {since})｜符合 {len(rows)}、"
          f"已engage {len(engaged)}、未推 {len(fresh)}")

    if not fresh:
        print("無新高分，不推播"); return

    shown = fresh[:MAX_SHOW]
    dropped = len(fresh) - len(shown)
    lines = []
    for item_id, title, score, src, oa, inst, doi in shown:
        badge = ("🟢" if oa else ("🏥" if inst else "·"))
        t = (title or "").strip()
        if len(t) > 60:
            t = t[:58] + "…"
        lines.append(f"{badge} [{score}] {t}")
    msg = "\n".join(lines)
    if dropped:
        msg += f"\n…另 {dropped} 篇（上站看）"
    title = f"📚 {len(fresh)} 篇新高分論文"

    if args.dry:
        print(title); print(msg); print("(dry，未推未寫 state)"); return

    ntfy(title, msg)
    save_state(state | {r[0] for r in fresh})
    print(f"已推播 {len(shown)} 篇（另標記 {len(fresh)} 篇已推）")


if __name__ == "__main__":
    main()
