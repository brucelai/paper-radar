#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM facet extraction：對 exported（非 skipped）且尚無 facets 的論文抽結構化特徵。

facets = PRPM 的第二特徵軸（方法學/場域/族群），解決「手寫 tag 只有主題維度」。
Schema 與 enum 白名單見 docs/DESIGN-PRPM.md §3 — enum 外的值一律丟棄（防幻覺）。

Provider: Groq openai/gpt-oss-20b（免費、JSON 抽取最穩）。GROQ_API_KEY 不在 env → 整段跳過。
每晚上限 --cap 篇（預設 80），存量 ~450 篇約 6 晚補完，之後每天只有增量 ~20 篇。
"""
import argparse, json, os, sqlite3, sys, time, urllib.error, urllib.request

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-20b"

ENUMS = {
    "design": ["rct", "systematic-review", "meta-analysis", "cohort", "case-control",
               "cross-sectional", "case-series", "case-report", "narrative-review",
               "guideline", "qualitative", "pilot", "protocol", "preclinical", "other"],
    "setting": ["icu", "inpatient-rehab", "outpatient", "community", "sports",
                "telehealth", "lab", "other"],
    "population": ["stroke", "sci", "tbi", "pediatric", "geriatric", "msk", "cancer",
                   "icu", "healthy", "mixed", "other"],
    "methods": ["bayesian", "survival-analysis", "machine-learning", "deep-learning",
                "explainable-ai", "multimodal", "nlp", "imaging", "emg", "ultrasound",
                "biomechanics", "psychometrics", "economic"],
    "sample_size": ["small", "medium", "large", "na"],
}

def build_prompt(title, abstract):
    # 不用 % / format：title/abstract 常含 % 與 {}，字串拼接最安全
    return ("Classify this research paper. Reply with ONLY a JSON object, no prose:\n"
            '{"design": <one of ' + str(ENUMS["design"]) + ">,\n"
            ' "setting": <one of ' + str(ENUMS["setting"]) + ">,\n"
            ' "population": <one of ' + str(ENUMS["population"]) + ">,\n"
            ' "methods": <array, subset of ' + str(ENUMS["methods"]) + ", empty if none>,\n"
            ' "sample_size": <"small"(<50) | "medium"(50-200) | "large"(>200) | "na">}\n\n'
            "Title: " + title + "\nAbstract: " + abstract[:2500])


def call_groq(key, title, abstract):
    body = json.dumps({
        "model": MODEL, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": build_prompt(title, abstract)}],
    }).encode("utf-8")
    # ⚠️ User-Agent 必帶：Groq 前面的 Cloudflare 會擋 python-urllib 簽名（403 code 1010，
    # 同 ntfy gotcha）
    req = urllib.request.Request(GROQ_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (paper-radar; +https://example.com)"})
    with urllib.request.urlopen(req, timeout=45) as r:
        js = json.loads(r.read().decode("utf-8"))
    return json.loads(js["choices"][0]["message"]["content"])


def validate(raw):
    """enum 白名單過濾；欄位缺/非法 → 丟該欄位；全空 → None。"""
    out = {}
    for k in ("design", "setting", "population", "sample_size"):
        v = str(raw.get(k, "")).lower().strip()
        if v in ENUMS[k if k != "sample_size" else "sample_size"]:
            out[k] = v
    ms = raw.get("methods") or []
    if isinstance(ms, list):
        ok = [str(m).lower().strip() for m in ms if str(m).lower().strip() in ENUMS["methods"]]
        if ok:
            out["methods"] = sorted(set(ok))
    return out or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(SCRIPT_DIR / "paper_radar.db"))
    ap.add_argument("--cap", type=int, default=80, help="本次最多抽幾篇")
    args = ap.parse_args()

    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        print("GROQ_API_KEY 未設 → 跳過 facet extraction（管線其餘照常）")
        return

    con = sqlite3.connect(args.db)
    if not any(c[1] == "facets" for c in con.execute("PRAGMA table_info(papers)")):
        con.execute("ALTER TABLE papers ADD COLUMN facets TEXT")
        con.commit()

    rows = con.execute(
        """SELECT item_id, title, abstract FROM papers
           WHERE category != 'skipped' AND (facets IS NULL OR facets = '')
           ORDER BY first_seen DESC LIMIT ?""", (args.cap,)).fetchall()
    print(f"待抽 facets：{len(rows)} 篇（cap {args.cap}）")

    ok = fail = 0
    for iid, title, abstract in rows:
        try:
            f = None
            for attempt in range(3):
                try:
                    f = validate(call_groq(key, title or "", abstract or ""))
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < 2:   # 免費層 TPM 限制：退避後重試
                        time.sleep(30)
                        continue
                    raise
            con.execute("UPDATE papers SET facets=? WHERE item_id=?",
                        (json.dumps(f, ensure_ascii=False) if f else "{}", iid))
            con.commit()
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  ✗ {iid[:40]}: {str(e)[:100]}")
            if fail >= 5 and ok == 0:   # API 整體壞掉就別空轉
                print("連續失敗，中止本輪"); break
            if fail >= 12:              # rate limit 燒穿整批也沒意義，留給明晚
                print("失敗過多，餘量留給下一輪"); break
        time.sleep(3.5)   # Groq 免費層禮貌節流（429 後由上面 backoff 處理）
    print(f"✓ facets 完成 {ok} 篇，失敗 {fail}")
    con.close()


if __name__ == "__main__":
    main()
