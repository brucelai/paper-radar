#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PRPM 訓練：actions_cache.json + paper_radar.db → model_state.json。

STATELESS（設計不變量，見 docs/DESIGN-PRPM.md §1）：每晚從 D1 全量歷史重算
decayed Beta pseudo-counts + profile embedding。無增量狀態 → idempotent、無漂移、
可從 D1+db 完整重建。model_state.json 只是產物快取，不是狀態。

訊號表 / 特徵表 / prior 表：DESIGN §2-§4。常數只在這裡與 DESIGN §8 出現。
"""
import json, math, sqlite3, sys
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR / "paper_radar.db"
ACTIONS = SCRIPT_DIR / "actions_cache.json"
MODEL = SCRIPT_DIR / "interest_model.json"
OUT = SCRIPT_DIR / "model_state.json"

HALF_LIFE_DAYS = 90
CLIP_LO, CLIP_HI = -2.0, 3.0
IMPRESSION_MIN_DAYS = 3      # rank<=20 出現在 >= N 個活躍日且零互動 → impression-only
IMPRESSION_S = -0.05
RECENT_DAYS = 30             # rising/falling 視窗

# 特徵 prior（DESIGN §3）：class -> (p0 函數, n0)
PRIOR_N0 = {"kw": 8, "neg": 8, "design": 6, "author": 6, "facet": 6, "src": 10, "grp": 10}


def decay(age_days):
    return 0.5 ** (max(age_days, 0) / HALF_LIFE_DAYS)


def signal(a):
    """一列 actions → (s, ts)。s=0 且無 seen → None（無訊號）。"""
    s = 0.0
    engaged = a.get("deepread") or a.get("content") or (a.get("pdf_key") or "").strip()
    if engaged:
        s += 2.0
    v = a.get("vote") or ""
    if v == "up":
        s += 1.0
    elif v == "down":
        s -= 1.5
    elif v == "neutral":
        s -= 0.3
    if s == 0.0:
        if a.get("seen"):
            s = -0.1          # seen-only
        else:
            return None
    return max(CLIP_LO, min(CLIP_HI, s))


def paper_features(tags, facets, src, grp):
    """db 一篇 → feature 名清單（DESIGN §3 命名）。"""
    feats = []
    for t in tags:
        if t.startswith("neg:"):
            feats.append("neg:" + t[4:])
        elif t.startswith("design:"):
            feats.append(t)
        elif t.startswith("author:"):
            feats.append(t)
        elif t.startswith("penalty:"):
            continue
        else:
            feats.append("kw:" + t)
    if facets:
        for k in ("design", "setting", "population", "sample_size"):
            if facets.get(k):
                feats.append(f"facet:{k}:{facets[k]}")
        for m in facets.get("methods", []):
            feats.append(f"facet:method:{m}")
    if src:
        feats.append("src:" + src)
    if grp:
        feats.append("grp:" + grp)
    return feats


def load_priors():
    """interest_model.json → feature 名 -> (p0, n0)。未列出的特徵用 class 預設。"""
    m = json.loads(MODEL.read_text(encoding="utf-8"))
    pri = {}
    for g in m.get("positive", []):
        w = g.get("base_weight", g["weight"])
        pri["kw:" + g["tag"]] = (min(0.9, max(0.2, 0.5 + 0.07 * w)), PRIOR_N0["kw"])
    for g in m.get("negative", []):
        pri["neg:" + g["tag"]] = (min(0.9, max(0.2, 0.5 + 0.07 * g["weight"])), PRIOR_N0["neg"])
    for d in m.get("design_bonus", {}):
        pri["design:" + d] = (0.57, PRIOR_N0["design"])
    for a in m.get("bonus_authors", []):
        pri["author:" + a] = (0.57, PRIOR_N0["author"])
    return pri


def prior_for(feat, priors):
    if feat in priors:
        return priors[feat]
    cls = feat.split(":", 1)[0]
    return (0.5, PRIOR_N0.get(cls, 6))


def main():
    if not ACTIONS.exists():
        print("actions_cache.json 不存在（sync_actions 沒跑？）→ 跳過訓練")
        return
    cache = json.loads(ACTIONS.read_text(encoding="utf-8"))
    actions = cache.get("actions", [])
    today = date.today()

    con = sqlite3.connect(str(DB))
    has_facets = any(c[1] == "facets" for c in con.execute("PRAGMA table_info(papers)"))
    has_emb = any(c[1] == "embedding" for c in con.execute("PRAGMA table_info(papers)"))
    cols = "item_id, tags, source, grp" + (", facets" if has_facets else "") + \
           (", embedding" if has_emb else "")
    papers = {}
    for row in con.execute(f"SELECT {cols} FROM papers"):
        d = dict(zip([c.strip() for c in cols.split(",")], row))
        try:
            tags = json.loads(d.get("tags") or "[]")
        except json.JSONDecodeError:
            tags = []
        try:
            facets = json.loads(d.get("facets") or "{}") if has_facets else {}
        except json.JSONDecodeError:
            facets = {}
        papers[d["item_id"]] = {
            "feats": paper_features(tags, facets, d.get("source"), d.get("grp")),
            "emb": d.get("embedding"),
        }

    # --- 聚合 pseudo-counts + profile embedding ------------------------------
    counts = {}           # feat -> {"pos","neg","pos30","neg30","n_events"}
    profile = None
    active_days = set()
    sig_stats = {"engaged": 0, "up": 0, "down": 0, "neutral": 0, "seen_only": 0}

    for a in actions:
        ts = (a.get("updated") or "")[:10]
        if ts:
            active_days.add(ts)
        s = signal(a)
        if s is None:
            continue
        # 統計（給 dashboard）
        if a.get("deepread") or a.get("content") or (a.get("pdf_key") or "").strip():
            sig_stats["engaged"] += 1
        v = a.get("vote") or ""
        if v in ("up", "down", "neutral"):
            sig_stats[v] += 1
        elif s == -0.1:
            sig_stats["seen_only"] += 1

        try:
            age = (today - date.fromisoformat(ts)).days if ts else 0
        except ValueError:
            age = 0
        w = abs(s) * decay(age)
        recent = age <= RECENT_DAYS
        p = papers.get(a.get("item_id"))
        if not p:
            continue
        for f in p["feats"]:
            c = counts.setdefault(f, {"pos": 0.0, "neg": 0.0, "pos30": 0.0,
                                      "neg30": 0.0, "n_events": 0})
            key = "pos" if s > 0 else "neg"
            c[key] += w
            if recent:
                c[key + "30"] += abs(s)   # 近期窗不 decay（本來就短）
            c["n_events"] += 1
        # profile embedding：|s|>=0.3 的才算（seen-only 太弱、太雜）
        if abs(s) >= 0.3 and has_emb and p["emb"]:
            import struct
            vec = struct.unpack(f"{len(p['emb'])//4}f", p["emb"])
            if profile is None:
                profile = [0.0] * len(vec)
            k = s * decay(age)
            for i, x in enumerate(vec):
                profile[i] += k * x

    # --- impression-only（弱負向；限制條件見 DESIGN §2 caveat）----------------
    acted_ids = {a.get("item_id") for a in actions}
    has_ranklog = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='rank_log'").fetchone()
    n_impression = 0
    if has_ranklog and active_days:
        q = """SELECT item_id, COUNT(DISTINCT day) FROM rank_log
               WHERE rank <= 20 AND day IN (%s) GROUP BY item_id""" % \
            ",".join("?" * len(active_days))
        for iid, ndays in con.execute(q, sorted(active_days)):
            if ndays >= IMPRESSION_MIN_DAYS and iid not in acted_ids and iid in papers:
                for f in papers[iid]["feats"]:
                    c = counts.setdefault(f, {"pos": 0.0, "neg": 0.0, "pos30": 0.0,
                                              "neg30": 0.0, "n_events": 0})
                    c["neg"] += abs(IMPRESSION_S)
                    c["n_events"] += 1
                n_impression += 1

    # --- 輸出 -----------------------------------------------------------------
    if profile is not None:
        norm = math.sqrt(sum(x * x for x in profile))
        profile = [x / norm for x in profile] if norm > 1e-9 else None

    state = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "half_life_days": HALF_LIFE_DAYS,
        "features": counts,
        "profile_vec": profile,
        "active_days": sorted(active_days),
        "signals": sig_stats,
        "n_impression_only": n_impression,
    }
    OUT.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"✓ train：{len(actions)} actions → {len(counts)} features"
          f"｜profile_vec={'有' if profile else '無'}"
          f"｜impression-only {n_impression} 篇｜活躍日 {len(active_days)}")
    con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"✗ train_model 失敗：{e}", file=sys.stderr)
        sys.exit(1)
