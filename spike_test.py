#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase-2 風險探針：各打一次外部相依，確認設計可行再全力建。
  1) RSS 解析 + DOI 抽取   2) PubMed esearch (MeSH 查詢)
  3) Unpaywall OA 判定      4) 機構 SFX menu 可否匿名讀 + 全文區段偵測
"""
import json, re, sys, urllib.parse, urllib.request

UA = "Mozilla/5.0 (paper-radar spike)"
EMAIL = "you@example.com"

def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def line(): print("-" * 70)

# 1) RSS 解析 + DOI ----------------------------------------------------------
print("\n[1] RSS 解析: Archives of PMR")
line()
doi_found = None
try:
    xml = get("http://www.archives-pmr.org/current.rss")
    titles = re.findall(r"<title>(.*?)</title>", xml, re.S)[1:4]   # skip channel title
    dois = re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", xml)
    for t in titles:
        print("  •", re.sub(r"<!\[CDATA\[|\]\]>", "", t).strip()[:80])
    print(f"  DOIs 抽到: {len(dois)} 個", ("例:" + dois[0]) if dois else "(RSS 無 DOI，需走 link/PubMed 補)")
    doi_found = dois[0] if dois else None
except Exception as e:
    print("  ✗ FAIL:", e)

# 2) PubMed esearch (MeSH 查詢) ----------------------------------------------
print("\n[2] PubMed esearch: ESWT MeSH 查詢")
line()
eswt_term = ('("Extracorporeal Shockwave Therapy"[Mesh] OR shockwave OR ESWT) '
             'AND (rehabilitation OR tendinopathy OR musculoskeletal)')
try:
    q = urllib.parse.quote(eswt_term)
    js = json.loads(get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                        f"?db=pubmed&term={q}&retmax=5&retmode=json&sort=date"))
    ids = js["esearchresult"]["idlist"]
    print(f"  命中總數: {js['esearchresult']['count']}  取回最新: {ids}")
    if ids:
        su = json.loads(get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
                           f"?db=pubmed&id={ids[0]}&retmode=json"))
        d = su["result"][ids[0]]
        print("  最新一篇:", d["title"][:80])
        eloc = {e["idtype"]: e["value"] for e in d.get("articleids", [])}
        if eloc.get("doi"): doi_found = eloc["doi"]
        print("  DOI:", eloc.get("doi", "(無)"))
except Exception as e:
    print("  ✗ FAIL:", e)

# 3) Unpaywall OA ------------------------------------------------------------
print(f"\n[3] Unpaywall OA 判定  (DOI={doi_found})")
line()
try:
    if doi_found:
        u = json.loads(get(f"https://api.unpaywall.org/v2/{doi_found}?email={EMAIL}"))
        loc = u.get("best_oa_location")
        print(f"  is_oa={u.get('is_oa')}  status={u.get('oa_status')}")
        print("  PDF:", (loc or {}).get("url_for_pdf") if loc else "(無 OA PDF)")
    else:
        print("  (無 DOI 可測)")
except Exception as e:
    print("  ✗ FAIL:", e)

# 4) 機構 SFX menu 可否匿名讀 ------------------------------------------------
print(f"\n[4] 機構 SFX menu 匿名讀取 + 全文區段偵測  (DOI={doi_found})")
line()
try:
    if doi_found:
        sfx = (f"https://your-institution-sfx.example.com/SID"
               f"?sid=Claude&id=doi:{urllib.parse.quote(doi_found)}")
        html = get(sfx, timeout=25)
        has_ft = ("全文" in html) or ("Full Text" in html) or ("getFullTxt" in html)
        plats = [p for p in ["Ovid", "ClinicalKey", "Wiley", "SpringerLink", "NEJM", "JAMA"] if p in html]
        print(f"  讀到 HTML {len(html)} bytes (匿名 OK)")
        print(f"  偵測『全文/Full Text』區段: {has_ft}")
        print(f"  命中平台字串: {plats or '(無)'}")
    else:
        print("  (無 DOI 可測)")
except Exception as e:
    print("  ✗ FAIL (可能需登入/IP 限制):", e)

print("\n探針結束。")
