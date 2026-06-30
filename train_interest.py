#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""interest_model 訓練迴圈：讀 training_log.jsonl 的 vote，回頭微調 interest_model.json 權重。

設計重點（穩定、可重跑）：
- vote 是「整篇」訊號；哪些 tag 命中由 paper_radar.db 的 papers.tags 提供（log 內 tags 多為空）。
- 採「固定 prior + 票數位移」純函數，而非每次累加 → 重跑 idempotent，不會無限漂移。
  effective_weight = clamp(base_weight + delta, 下限, 上限)
  其中 delta = sign(net) * min(MAX_STEP, ceil(|net| / VOTES_PER_STEP))，net = up - down。
  首次跑：若 group 無 base_weight，把現有 weight 當 base 寫入（之後 base 不動）。
- 只自動調「正向 tag」。負向 tag（neg:）只報告不自動改（SCI 等子字串誤命中會污染，留人工）。
- 預設 dry-run 只印報告；--apply 才寫檔（先備份）。

用法：
  python train_interest.py                 # 報告（不寫檔）
  python train_interest.py --apply         # 套用並寫回 interest_model.json（備份 .bak-train）
  python train_interest.py --min-support 2 # 降低觸發門檻（資料稀疏時）
"""
import argparse, json, math, sqlite3, datetime
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_PATH = SCRIPT_DIR / "interest_model.json"
LOG_PATH = SCRIPT_DIR / "training_log.jsonl"
DB_PATH = SCRIPT_DIR / "paper_radar.db"
RUNLOG = SCRIPT_DIR / "training_runs.log"

# 調參
VOTES_PER_STEP = 3      # 每 3 淨票 = 1 個權重級距
MAX_STEP = 2            # 單一 tag 最多位移 ±2
POS_MIN, POS_MAX = 1, 5  # 正向 tag 權重夾鉗
VOTE_VALUE = {"up": 1, "down": -1, "neutral": 0}


def load_votes():
    """讀 log，每個 item_id 取最新一票（後寫覆蓋前寫）。"""
    latest = {}
    if not LOG_PATH.exists():
        return latest
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        iid = e.get("item_id")
        if not iid:
            continue
        prev = latest.get(iid)
        if prev is None or e.get("ts", "") >= prev.get("ts", ""):
            latest[iid] = e
    return latest


def db_tags(item_ids):
    """item_id -> 該篇命中的 tag 清單（含 neg:/author:/design:/penalty: 前綴）。"""
    out = {}
    if not DB_PATH.exists() or not item_ids:
        return out
    con = sqlite3.connect(str(DB_PATH))
    q = "SELECT item_id, tags FROM papers WHERE item_id IN (%s)" % ",".join("?" * len(item_ids))
    for iid, tags in con.execute(q, list(item_ids)):
        try:
            out[iid] = json.loads(tags or "[]")
        except json.JSONDecodeError:
            out[iid] = []
    con.close()
    return out


def aggregate(votes, tagmap):
    """回傳 pos[tag] / neg[tag] = {'up':n,'down':n,'neutral':n,'net':int,'papers':[...]}，
       以及 missing（log 有票但 db 查不到 tag 的 item_id）。"""
    pos = defaultdict(lambda: {"up": 0, "down": 0, "neutral": 0, "papers": []})
    neg = defaultdict(lambda: {"up": 0, "down": 0, "neutral": 0, "papers": []})
    missing = []
    for iid, e in votes.items():
        v = e.get("vote")
        if v not in VOTE_VALUE:
            continue
        tags = tagmap.get(iid)
        if tags is None:
            missing.append(iid)
            continue
        title = (e.get("title") or "")[:50]
        for t in tags:
            if t.startswith("neg:"):
                bucket = neg[t[4:]]
            elif ":" in t:   # author:/design:/penalty: 不參與 tag 學習
                continue
            else:
                bucket = pos[t]
            bucket[v] += 1
            bucket["papers"].append((v, title))
    for d in (pos, neg):
        for t, b in d.items():
            b["net"] = b["up"] - b["down"]
    return pos, neg, missing


def delta_for(net):
    if net == 0:
        return 0
    mag = min(MAX_STEP, math.ceil(abs(net) / VOTES_PER_STEP))
    return mag if net > 0 else -mag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="寫回 interest_model.json（預設只報告）")
    ap.add_argument("--min-support", type=int, default=3, help="一個 tag 至少要這麼多張有效票才調整")
    args = ap.parse_args()

    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    votes = load_votes()
    tagmap = db_tags(set(votes))
    pos, neg, missing = aggregate(votes, tagmap)

    print(f"=== interest 訓練 ({datetime.date.today().isoformat()}) ===")
    print(f"有效投票 item：{len([e for e in votes.values() if e.get('vote') in VOTE_VALUE])}"
          f"｜db 命中 tag：{len(tagmap)}｜db 查無（已老化）：{len(missing)}")
    print(f"門檻：min_support={args.min_support}, {VOTES_PER_STEP} 淨票/級, 上限 ±{MAX_STEP}\n")

    # 建 tag -> group 索引（正向）
    changes = []
    for group in model.get("positive", []):
        tag = group["tag"]
        agg = pos.get(tag)
        # 確保 base_weight 存在（首次：以現權重為 prior）
        base = group.get("base_weight", group["weight"])
        group["base_weight"] = base
        if not agg:
            continue
        support = agg["up"] + agg["down"]
        if support < args.min_support:
            print(f"· {tag:<24} 票 {agg['up']}↑/{agg['down']}↓ net{agg['net']:+d} "
                  f"(support {support} < {args.min_support}, 略過)")
            continue
        new_w = max(POS_MIN, min(POS_MAX, base + delta_for(agg["net"])))
        flag = "" if new_w == group["weight"] else f"  →  {group['weight']} ⇒ {new_w}"
        print(f"★ {tag:<24} 票 {agg['up']}↑/{agg['down']}↓ net{agg['net']:+d} "
              f"base {base} eff {new_w}{flag}")
        if new_w != group["weight"]:
            changes.append((tag, group["weight"], new_w))
            group["weight"] = new_w

    # 負向 tag 只報告（不自動改）
    flagged_neg = [(t, b) for t, b in neg.items()
                   if (b["up"] + b["down"]) >= args.min_support and b["net"] > 0]
    if flagged_neg:
        print("\n⚠️ 負向 tag 收到正評（可能 penalty 過嚴，建議人工檢視，未自動改）：")
        for t, b in flagged_neg:
            print(f"  neg:{t}  {b['up']}↑/{b['down']}↓ net{b['net']:+d}")

    if missing:
        print(f"\n（{len(missing)} 篇已從本地 db 老化，無法取 tag，本次未計入）")

    print(f"\n變更數：{len(changes)}")
    if not changes:
        print("無需調整。"); return

    if args.apply:
        bak = MODEL_PATH.with_suffix(".json.bak-train")
        bak.write_text(MODEL_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        model["updated"] = datetime.date.today().isoformat()
        MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        with RUNLOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "changes": [{"tag": t, "from": a, "to": b} for t, a, b in changes],
            }, ensure_ascii=False) + "\n")
        print(f"✅ 已寫回 {MODEL_PATH.name}（備份 {bak.name}）。記得 `bash deploy.sh` 推到 Oracle 生效。")
    else:
        print("（dry-run，未寫檔。加 --apply 套用）")


if __name__ == "__main__":
    main()
