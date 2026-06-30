#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PRPM serving：db + model_state.json → papers.json（最終排序）+ rank_log + site/profile.json。

排序 = Thompson sampling（每特徵每日抽一次 θ，不確定的特徵自然輪到高位=內建探索）
     + MMR 多樣性重排（打散同主題連發）
     + 明示探索槽（位置 6,12,18,24,30,36，卡片標 🧭，回饋帶 exploration context）。
展示分數 = posterior mean（穩定）；排序用 sampled（當日固定 seed，明日自然洗牌）。
數學與常數：docs/DESIGN-PRPM.md §4-§5、§8。

model_state.json 不存在 → exit 1（run.sh fallback：沿用 fetch_and_score 的 papers.json）。
"""
import json, math, random, sqlite3, struct, sys
from datetime import date, datetime
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
DB = SCRIPT_DIR / "paper_radar.db"
STATE = SCRIPT_DIR / "model_state.json"
MODEL = SCRIPT_DIR / "interest_model.json"
CONFIG = SCRIPT_DIR / "config.yaml"
OUT = SCRIPT_DIR / "papers.json"
PROFILE_OUT = SCRIPT_DIR / "site" / "profile.json"

SCALES = {"kw": 4.0, "neg": 4.0, "design": 2.0, "author": 1.5,
          "facet": 2.5, "src": 1.0, "grp": 0.5}
EMB_SCALE = 3.0
TS_DAMP = 0.5        # Thompson 變異阻尼：sampled = expected + DAMP*(raw−expected)
                     # 1.0=純 Thompson（小樣本時 top 位會被高變異論文佔走）；0=純 exploit
MMR_LAMBDA = 0.75
MMR_POOL = 60
EXPLORE_POSITIONS = [6, 12, 18, 24, 30, 36]   # 1-based
COLD_SUPPORT = 3
ADJ_ZONE = (0.15, 0.5)
PRIOR_N0 = {"kw": 8, "neg": 8, "design": 6, "author": 6, "facet": 6, "src": 10, "grp": 10}

sys.path.insert(0, str(SCRIPT_DIR))
from train_model import paper_features, load_priors, prior_for   # 同一份特徵/prior 定義


def unpack(b):
    return struct.unpack(f"{len(b)//4}f", b) if b else None


def cos(u, v):
    if not u or not v:
        return 0.0
    s = sum(a * b for a, b in zip(u, v))
    return max(-1.0, min(1.0, s))   # 兩邊都已 normalize（profile 是；paper 是 bge 出廠即單位向量）


def label_of(feat):
    cls, _, rest = feat.partition(":")
    return {"kw": rest, "neg": "⛔ " + rest, "design": "設計 " + rest,
            "author": "作者 " + rest.split(",")[0],
            "facet": rest.replace(":", " "), "src": "期刊 " + rest,
            "grp": "主題組 " + rest}.get(cls, feat)


def main():
    if not STATE.exists():
        print("model_state.json 不存在 → rank 退場（沿用 fetch 匯出）", file=sys.stderr)
        sys.exit(1)
    state = json.loads(STATE.read_text(encoding="utf-8"))
    fcounts = state["features"]
    profile = state.get("profile_vec")
    priors = load_priors()
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    new_days = cfg.get("defaults", {}).get("new_days", 5)
    day = date.today().isoformat()
    today = date.today()

    # --- posterior 參數 + 當日 Thompson 抽樣（每特徵一次，全篇共用）------------
    def posterior(feat):
        p0, n0 = prior_for(feat, priors)
        c = fcounts.get(feat, {})
        return p0 * n0 + c.get("pos", 0.0), (1 - p0) * n0 + c.get("neg", 0.0)

    theta_cache = {}
    def theta(feat):
        if feat not in theta_cache:
            a, b = posterior(feat)
            rng = random.Random(f"{day}:{feat}")
            theta_cache[feat] = (rng.betavariate(a, b), a / (a + b))
        return theta_cache[feat]

    # --- 撈 exported papers（同 fetch_and_score 匯出條件）----------------------
    con = sqlite3.connect(str(DB))
    dbcols = {c[1] for c in con.execute("PRAGMA table_info(papers)")}
    has_facets = "facets" in dbcols
    has_emb = "embedding" in dbcols
    base_cols = ["item_id", "title", "source", "source_name", "grp", "authors", "url",
                 "doi", "abstract", "pub_date", "score", "tags", "category", "oa_status",
                 "oa_pdf_url", "oa_first_date", "inst_subscribed", "inst_platforms",
                 "sfx_url", "first_seen", "last_seen"]
    sel = base_cols + (["facets"] if has_facets else []) + (["embedding"] if has_emb else [])
    # 缺欄容忍（舊 db 沒跑過 idempotent ALTER）：SELECT NULL AS col
    sel_sql = ",".join(c if c in dbcols else f"NULL AS {c}" for c in sel)
    rows = con.execute(f"SELECT {sel_sql} FROM papers WHERE category!='skipped'").fetchall()

    papers = []
    for r in rows:
        d = dict(zip(sel, r))
        if d["oa_status"] and d["oa_status"] != "closed" and not d["oa_pdf_url"]:
            continue   # 標 OA 卻抓不到全文 → 不顯示(同 v1 規則)
        try:
            tags = json.loads(d["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        try:
            facets = json.loads(d.get("facets") or "{}") if has_facets else {}
        except json.JSONDecodeError:
            facets = {}
        feats = paper_features(tags, facets, d["source"], d["grp"])
        emb = unpack(d.get("embedding")) if has_emb else None

        sampled = expected = 0.0
        why = []
        for f in feats:
            th, mu = theta(f)
            s_cls = SCALES.get(f.split(":", 1)[0], 1.0)
            sampled += s_cls * 2 * (th - 0.5)
            c = s_cls * 2 * (mu - 0.5)
            expected += c
            why.append((label_of(f), round(c, 2)))
        c_emb = 0.0
        if profile and emb:
            c_emb = EMB_SCALE * cos(profile, emb)
            sampled += c_emb
            expected += c_emb
            why.append(("語意相似度", round(c_emb, 2)))
        why = sorted(why, key=lambda x: -abs(x[1]))[:6]
        sampled = expected + TS_DAMP * (sampled - expected)

        d2 = {k: d[k] for k in base_cols}
        d2["group"] = d2.pop("grp")
        d2["tags"] = tags
        d2["kw_score"] = d["score"]
        d2["score"] = round(expected, 1)
        d2["why"] = [list(w) for w in why]
        d2["isNew"] = (date.fromisoformat(d["first_seen"]) - today).days >= -new_days
        d2["oaNew"] = bool(d["oa_first_date"]) and \
            (date.fromisoformat(d["oa_first_date"]) - today).days >= -new_days
        d2["facets"] = facets or None
        d2["_sampled"] = sampled
        d2["_emb"] = emb
        d2["_feats"] = feats
        d2["_support"] = max((fcounts.get(f, {}).get("n_events", 0) for f in feats), default=0)
        papers.append(d2)

    # --- 排序：sampled 降冪 → MMR top 60 → explore 槽 --------------------------
    papers.sort(key=lambda p: -p["_sampled"])
    pool, rest = papers[:MMR_POOL], papers[MMR_POOL:]
    if pool:
        smax = pool[0]["_sampled"]
        smin = pool[-1]["_sampled"]
        rng_norm = (smax - smin) or 1.0
        picked = []
        remaining = pool[:]
        while remaining:
            best, best_v = None, -1e9
            for p in remaining:
                rel = (p["_sampled"] - smin) / rng_norm
                red = max((cos(p["_emb"], q["_emb"]) for q in picked
                           if p["_emb"] and q["_emb"]), default=0.0)
                v = MMR_LAMBDA * rel - (1 - MMR_LAMBDA) * red
                if v > best_v:
                    best, best_v = p, v
            picked.append(best)
            remaining.remove(best)
        ranked = picked + rest
    else:
        ranked = papers

    # explore 槽：冷特徵 / 語意鄰接帶 / 1 個 serendipity（只挑未看過的）
    seen_ids = set()
    acts = SCRIPT_DIR / "actions_cache.json"
    if acts.exists():
        for a in json.loads(acts.read_text(encoding="utf-8")).get("actions", []):
            if a.get("seen") or a.get("vote") or a.get("deepread") or a.get("content"):
                seen_ids.add(a.get("item_id"))

    rng = random.Random(day)
    unseen = [p for p in ranked if p["item_id"] not in seen_ids]
    cold = [p for p in unseen if p["_support"] < COLD_SUPPORT]
    adjacent = [p for p in unseen if profile and p["_emb"]
                and ADJ_ZONE[0] < cos(profile, p["_emb"]) < ADJ_ZONE[1]]
    seren = [p for p in sorted(unseen, key=lambda p: p["_sampled"])[:30]]
    pools = [("冷門特徵", cold), ("鄰近領域", adjacent), ("隨機驚喜", seren)]

    explore_picks, used = [], set()
    for i, pos in enumerate(EXPLORE_POSITIONS):
        # 輪流從三個 pool 抽；serendipity 最多 1 個
        order = [pools[i % 2], pools[(i + 1) % 2]] + ([pools[2]] if i == len(EXPLORE_POSITIONS) - 1 else [])
        for reason, cand in order:
            cand = [p for p in cand if p["item_id"] not in used and ranked.index(p) >= pos]
            if cand:
                p = rng.choice(cand[:40])
                p["explore"] = True
                p["why"] = [[f"🧭 探索：{reason}", 0.0]] + p["why"][:5]
                explore_picks.append((pos, p))
                used.add(p["item_id"])
                break

    for pos, p in explore_picks:
        ranked.remove(p)
        ranked.insert(min(pos - 1, len(ranked)), p)

    for i, p in enumerate(ranked):
        p["rank"] = i + 1
        p.setdefault("explore", False)

    # --- rank_log（impression 紀錄，train_model 讀）----------------------------
    con.execute("""CREATE TABLE IF NOT EXISTS rank_log(
        day TEXT, item_id TEXT, rank INT, explore INT, sampled REAL, expected REAL,
        PRIMARY KEY(day, item_id))""")
    con.executemany(
        "INSERT OR REPLACE INTO rank_log VALUES (?,?,?,?,?,?)",
        [(day, p["item_id"], p["rank"], int(p["explore"]),
          round(p["_sampled"], 3), p["score"]) for p in ranked[:MMR_POOL]])
    con.commit()

    # --- profile.json（偏好儀表板）---------------------------------------------
    feats_view = []
    for f, c in fcounts.items():
        if c.get("n_events", 0) < 1:
            continue
        p0, n0 = prior_for(f, priors)
        a, b = p0 * n0 + c.get("pos", 0), (1 - p0) * n0 + c.get("neg", 0)
        mu = a / (a + b)
        mu30 = None
        r30 = c.get("pos30", 0) + c.get("neg30", 0)
        if r30 >= 1:
            mu30 = (c.get("pos30", 0) + 1) / (r30 + 2)
        feats_view.append({"f": f, "label": label_of(f), "mean": round(mu, 3),
                           "n": c["n_events"],
                           "delta": round(mu30 - mu, 3) if mu30 is not None else None})
    top = sorted([x for x in feats_view if x["mean"] > 0.5],
                 key=lambda x: -(x["mean"] - 0.5) * math.log1p(x["n"]))[:15]
    avoid = sorted([x for x in feats_view if x["mean"] < 0.5],
                   key=lambda x: (x["mean"] - 0.5) * math.log1p(x["n"]))[:8]
    moved = [x for x in feats_view if x["delta"] is not None and x["n"] >= 2]
    rising = sorted([x for x in moved if x["delta"] > 0.02], key=lambda x: -x["delta"])[:6]
    falling = sorted([x for x in moved if x["delta"] < -0.02], key=lambda x: x["delta"])[:6]

    exp_shown = {r[0] for r in con.execute("SELECT DISTINCT item_id FROM rank_log WHERE explore=1")}
    exp_hit = len(exp_shown & {i for i in seen_ids})   # 探索位被互動過的
    PROFILE_OUT.write_text(json.dumps({
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "signals": state.get("signals", {}),
        "n_actions": len(state.get("active_days", [])),
        "top": top, "avoid": avoid, "rising": rising, "falling": falling,
        "explore": {"shown": len(exp_shown), "engaged": exp_hit},
        "n_features": len(fcounts),
        "has_profile_vec": bool(profile),
    }, ensure_ascii=False), encoding="utf-8")

    # --- papers.json ------------------------------------------------------------
    for p in ranked:
        for k in ("_sampled", "_emb", "_feats", "_support"):
            p.pop(k, None)
    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    payload = dict(updated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                   topic_groups=cfg["topic_groups"],
                   counts=dict(total_db=total, exported=len(ranked)),
                   engine="prpm-v2",
                   papers=ranked)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    n_exp = sum(1 for p in ranked if p["explore"])
    print(f"✓ rank：{len(ranked)} 篇（explore {n_exp}）→ papers.json｜"
          f"features {len(theta_cache)}｜profile.json 已更新")
    con.close()


if __name__ == "__main__":
    main()
