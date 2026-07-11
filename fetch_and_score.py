#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""paper-radar 核心：抓取技術研究來源 → SQLite 去重(生命週期) → 評分 → papers.json。

Usage:
    python fetch_and_score.py [--config config.yaml] [--db paper_radar.db]
                              [--only key1,key2] [--limit N] [--no-fetch]

設計同 rehab_radar：item_id 以 DOI 為主鍵(無 DOI 退標題+來源 hash)；first_seen 永久，
重抓只更新 last_seen/score；NEW = first_seen 在 new_days 內。
加值欄位(OA/SFX)留空，由 enrich.py(step 2)填。
"""
import argparse, hashlib, json, re, sqlite3, sys, time, urllib.parse, urllib.request
from datetime import datetime, date
from pathlib import Path

import feedparser
import yaml

SCRIPT_DIR = Path(__file__).parent
UA = "Mozilla/5.0 (paper-radar)"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
DBLP = "https://dblp.org/search/publ/api"
OPENREVIEW = "https://api2.openreview.net/notes"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_TEXTILE_DOMAIN_KEYWORDS = ("textile", "fiber", "fibre", "fabric", "yarn", "weaving",
                            "nonwoven", "smart textile")


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)          # strip HTML
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_doi(*fields):
    for f in fields:
        if not f:
            continue
        m = DOI_RE.search(f)
        if m:
            return m.group(0).rstrip(".")
    return ""


def item_id(doi, title, source):
    if doi:
        return "doi:" + doi.lower()
    raw = (title.strip().lower() + "|" + source.strip().lower()).encode("utf-8")
    return "h:" + hashlib.md5(raw).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 抓取：RSS
# --------------------------------------------------------------------------- #
def fetch_rss(feed):
    out = []
    d = feedparser.parse(feed["url"], agent=UA)
    if d.bozo and not d.entries:
        print(f"    ⚠ {feed['key']}: parse 警告 {d.get('bozo_exception')}")
    for e in d.entries[: feed.get("limit", 0) or 9999]:
        title = clean(e.get("title", ""))
        if not title:
            continue
        link = e.get("link", "") or (e.get("links", [{}])[0].get("href", "") if e.get("links") else "")
        summary = clean(e.get("summary", "") or e.get("description", ""))
        authors = ", ".join(a.get("name", "") for a in e.get("authors", [])) if e.get("authors") else clean(e.get("author", ""))
        # DOI 只從結構化欄位取（id/link/dc:identifier/prism:doi）。
        # ⚠ 絕不從 summary(摘要全文) 撈——摘要裡第一個 10.x 往往是被引用文獻的 DOI，
        # 會誤標成本文 DOI（與 PubMed 路徑同類 bug）。寧可留空讓 enrich 用標題回查。
        doi = extract_doi(e.get("id", ""), link, e.get("dc_identifier", ""),
                          e.get("prism_doi", ""))
        pub = ""
        if e.get("published_parsed"):
            pub = time.strftime("%Y-%m-%d", e["published_parsed"])
        out.append(dict(title=title, url=link, abstract=summary, authors=authors,
                        doi=doi, pub_date=pub))
    return out


# --------------------------------------------------------------------------- #
# 抓取：PubMed search (esearch → efetch)
# --------------------------------------------------------------------------- #
def fetch_pubmed(feed):
    limit = feed.get("limit", 0) or 20
    term = urllib.parse.quote(feed["term"])
    js = json.loads(http_get(f"{EUTILS}/esearch.fcgi?db=pubmed&term={term}"
                             f"&retmax={limit}&retmode=json&sort=date"))
    ids = js.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    time.sleep(0.34)   # NCBI 禮貌：<3 req/s 無 key
    xml = http_get(f"{EUTILS}/efetch.fcgi?db=pubmed&id={','.join(ids)}&retmode=xml")
    return parse_pubmed_xml(xml)


def parse_pubmed_xml(xml):
    import xml.etree.ElementTree as ET
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        print("    ⚠ efetch XML parse fail:", e)
        return out
    for art in root.findall(".//PubmedArticle"):
        title = clean("".join(art.find(".//ArticleTitle").itertext())) if art.find(".//ArticleTitle") is not None else ""
        if not title:
            continue
        abst = " ".join(clean("".join(a.itertext())) for a in art.findall(".//Abstract/AbstractText"))
        authors = []
        for a in art.findall(".//Author"):
            ln, fn = a.findtext("LastName"), a.findtext("ForeName")
            if ln:
                authors.append(f"{ln} {fn[0]}" if fn else ln)
        # ⚠ 只讀「本文」的 DOI/PMID。.//ArticleId 與 .//PMID 的後代軸會掃進
        # <PubmedData><ReferenceList> 裡每篇被引用文獻的 id（一筆 record 可達上百個），
        # 之前的 .// + 無 break 會誤取最後一個參考文獻的 DOI（系統性 DOI 錯標 bug, 2026-06-28 修）。
        doi = ""
        idlist = art.find("./PubmedData/ArticleIdList")
        if idlist is not None:
            for idn in idlist.findall("ArticleId"):
                if idn.get("IdType") == "doi":
                    doi = (idn.text or "").strip()
                    break
        if not doi:   # fallback：本文 Article 的 ELocationID（不會出現在參考文獻裡）
            el = art.find("./MedlineCitation/Article/ELocationID[@IdType='doi']")
            if el is not None and el.text:
                doi = el.text.strip()
        pmid = art.findtext("./MedlineCitation/PMID") or ""
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
        # pub date
        pd = art.find(".//PubDate")
        pub = ""
        if pd is not None:
            y, m, dd = pd.findtext("Year"), pd.findtext("Month"), pd.findtext("Day")
            if y:
                pub = "-".join(x for x in [y, (m or "").zfill(2) if m and m.isdigit() else m, dd] if x)
        out.append(dict(title=title, url=url, abstract=abst, authors=", ".join(authors),
                        doi=doi, pub_date=pub))
    return out


# --------------------------------------------------------------------------- #
# 抓取：技術研究 API
# --------------------------------------------------------------------------- #
def parse_date(date_components):
    if not date_components:
        return ""
    values = (date_components.get("date-parts", [[]])[0]
              if isinstance(date_components, dict) else date_components)
    if not values:
        return ""
    return "-".join([str(values[0]).zfill(4)] +
                    [str(value).zfill(2) for value in values[1:3]])


def author_names(authors):
    return ", ".join(" ".join(x for x in (a.get("given", ""), a.get("family", "")) if x)
                     or a.get("name", "") for a in (authors or []))


def fetch_openalex(feed):
    params = {"per-page": feed.get("limit", 20), "select":
              "id,doi,title,authorships,publication_date,abstract_inverted_index,primary_location"}
    if feed.get("search"):
        params["search"] = feed["search"]
    if feed.get("filter"):
        params["filter"] = feed["filter"]
    data = json.loads(http_get(f"{OPENALEX}?{urllib.parse.urlencode(params)}"))
    out = []
    for work in data.get("results", []):
        inverted = work.get("abstract_inverted_index") or {}
        words = sorted(((pos, word) for word, positions in inverted.items() for pos in positions))
        abstract = " ".join(word for _, word in words)
        location = work.get("primary_location") or {}
        out.append(dict(title=work.get("title", ""), url=(location.get("landing_page_url") or work.get("id", "")),
                        abstract=abstract, authors=author_names(
                            [{"name": (a.get("author") or {}).get("display_name", "")}
                             for a in work.get("authorships", [])]),
                        doi=(work.get("doi") or "").removeprefix("https://doi.org/"),
                        pub_date=work.get("publication_date") or ""))
    return [item for item in out if item["title"]]


def fetch_crossref(feed):
    params = {"rows": feed.get("limit", 20), "select":
              "DOI,title,author,URL,abstract,published,issued,container-title,type"}
    if feed.get("query"):
        params["query"] = feed["query"]
    if feed.get("filter"):
        params["filter"] = feed["filter"]
    data = json.loads(http_get(f"{CROSSREF}?{urllib.parse.urlencode(params)}"))
    out = []
    for work in data.get("message", {}).get("items", []):
        title = clean(" ".join(work.get("title", [])))
        journal = " ".join(work.get("container-title", []))
        out.append(dict(title=title, url=work.get("URL", ""), abstract=clean(work.get("abstract", "")),
                        authors=author_names(work.get("author")), doi=work.get("DOI", ""),
                        pub_date=parse_date(work.get("published") or work.get("issued")),
                        journal=journal, work_type=work.get("type", "")))
    return [item for item in out if item["title"]]


def fetch_semantic_scholar(feed):
    params = {"query": feed["query"], "limit": feed.get("limit", 20),
              "fields": "title,abstract,authors,externalIds,url,publicationDate"}
    data = json.loads(http_get(f"{S2}?{urllib.parse.urlencode(params)}"))
    return [dict(title=p.get("title", ""), url=p.get("url", ""), abstract=p.get("abstract") or "",
                 authors=", ".join(a.get("name", "") for a in p.get("authors", [])),
                 doi=(p.get("externalIds") or {}).get("DOI", ""),
                 pub_date=p.get("publicationDate") or "") for p in data.get("data", [])
            if p.get("title")]


def fetch_dblp(feed):
    params = {"q": feed["query"], "format": "json", "h": feed.get("limit", 20)}
    data = json.loads(http_get(f"{DBLP}?{urllib.parse.urlencode(params)}"))
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    return [dict(title=clean(hit.get("info", {}).get("title", "")),
                 url=hit.get("info", {}).get("url", ""), abstract="",
                 authors=clean(hit.get("info", {}).get("authors", {}).get("text", "")),
                 doi=hit.get("info", {}).get("doi", ""), pub_date=hit.get("info", {}).get("year", ""))
            for hit in hits if hit.get("info", {}).get("title")]


def fetch_openreview(feed):
    params = {"invitation": feed["invitation"], "limit": feed.get("limit", 20)}
    data = json.loads(http_get(f"{OPENREVIEW}?{urllib.parse.urlencode(params)}"))
    out = []
    for note in data.get("notes", []):
        content = note.get("content", {})
        value = lambda key: (content.get(key) or {}).get("value", "")
        authors = value("authors")
        out.append(dict(title=value("title"), url=f"https://openreview.net/forum?id={note.get('id', '')}",
                        abstract=value("abstract"), authors=", ".join(authors) if isinstance(authors, list) else authors,
                        doi="", pub_date=value("date") or note.get("cdate", "")))
    return [item for item in out if item["title"]]


def is_textile(item):
    text = " ".join(str(item.get(field, "")) for field in ("title", "abstract", "journal")).lower()
    return any(keyword in text for keyword in _TEXTILE_DOMAIN_KEYWORDS)


# --------------------------------------------------------------------------- #
# 評分（移植 rss_tagger.score_paper）
# --------------------------------------------------------------------------- #
def score_paper(paper, model):
    text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
    author_text = paper.get("authors", "").lower()
    score, tags, matched_pos = 0, [], set()

    for group in model.get("positive", []):
        for kw in group["keywords"]:
            if kw.lower() in text or kw.lower() in author_text:
                score += group["weight"]; tags.append(group["tag"]); matched_pos.add(group["tag"]); break

    exceptions = model.get("negative_exceptions", {})
    for group in model.get("negative", []):
        for kw in group["keywords"]:
            if kw.lower() in text:
                if any(ek.lower() in text for ek in exceptions.get(group["tag"], [])):
                    break
                score += group["weight"]; tags.append(f"neg:{group['tag']}"); break

    for author in model.get("bonus_authors", []):
        if author.lower() in author_text:
            score += 1; tags.append(f"author:{author}")

    for design, bonus in model.get("design_bonus", {}).items():
        if design.lower() in text:
            score += bonus; tags.append(f"design:{design}"); break

    pen = model.get("design_penalty_context", {})
    if pen and (matched_pos & set(pen.get("applies_to_tags", []))):
        for pat in pen.get("patterns", []):
            if pat.lower() in text:
                score += pen.get("penalty", -1); tags.append(f"penalty:{pat}"); break

    return score, sorted(set(tags))


# --------------------------------------------------------------------------- #
# SQLite store（生命週期）
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    item_id TEXT PRIMARY KEY,
    title TEXT, source TEXT, source_name TEXT, grp TEXT,
    authors TEXT, url TEXT, doi TEXT, abstract TEXT, pub_date TEXT,
    score INT, tags TEXT, category TEXT,
    oa_status TEXT, oa_pdf_url TEXT, oa_first_date TEXT,
    inst_subscribed INT, inst_platforms TEXT, sfx_url TEXT,
    metadata TEXT,
    enriched INT DEFAULT 0,
    first_seen TEXT, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_score ON papers(score);
CREATE INDEX IF NOT EXISTS idx_enriched ON papers(enriched);
"""


def upsert(con, p, today):
    cur = con.execute("SELECT first_seen FROM papers WHERE item_id=?", (p["item_id"],))
    row = cur.fetchone()
    if row:   # 既有 → 更新分數/last_seen，保留 first_seen
        con.execute("""UPDATE papers SET title=?, source_name=?, grp=?, authors=?, url=?,
                       doi=?, abstract=?, pub_date=?, score=?, tags=?, category=?, last_seen=?
                       WHERE item_id=?""",
                    (p["title"], p["source_name"], p["grp"], p["authors"], p["url"], p["doi"],
                     p["abstract"], p["pub_date"], p["score"], json.dumps(p["tags"], ensure_ascii=False),
                     p["category"], today, p["item_id"]))
        return "updated"
    con.execute("""INSERT INTO papers (item_id, title, source, source_name, grp, authors, url, doi,
                   abstract, pub_date, score, tags, category, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p["item_id"], p["title"], p["source"], p["source_name"], p["grp"], p["authors"],
                 p["url"], p["doi"], p["abstract"], p["pub_date"], p["score"],
                 json.dumps(p["tags"], ensure_ascii=False), p["category"], today, today))
    return "new"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(SCRIPT_DIR / "config.yaml"))
    ap.add_argument("--db", default=str(SCRIPT_DIR / "paper_radar.db"))
    ap.add_argument("--model", default=str(SCRIPT_DIR / "interest_model.json"))
    ap.add_argument("--out", default=str(SCRIPT_DIR / "papers.json"))
    ap.add_argument("--only", default="", help="只跑這些 feed key（逗號分隔）")
    ap.add_argument("--limit", type=int, default=0, help="覆寫每 feed 抓取數")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    model = json.load(open(args.model, encoding="utf-8"))
    default_limit = cfg.get("defaults", {}).get("limit", 20)
    new_days = cfg.get("defaults", {}).get("new_days", 5)
    today = date.today().isoformat()

    only = set(filter(None, args.only.split(",")))
    feeds = [f for f in cfg["feeds"] if not only or f["key"] in only]

    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)
    # 舊 DB 補欄位（idempotent；CREATE TABLE IF NOT EXISTS 不會加欄位到既有表）
    if not any(c[1] == "oa_first_date" for c in con.execute("PRAGMA table_info(papers)")):
        con.execute("ALTER TABLE papers ADD COLUMN oa_first_date TEXT")
        con.commit()
    if not any(c[1] == "metadata" for c in con.execute("PRAGMA table_info(papers)")):
        con.execute("ALTER TABLE papers ADD COLUMN metadata TEXT")
        con.commit()

    stats = {"new": 0, "updated": 0, "feeds_ok": 0, "feeds_fail": 0}
    for feed in feeds:
        feed.setdefault("limit", args.limit or default_limit)
        kind = feed.get("type", "rss")
        try:
            fetchers = {
                "rss": fetch_rss, "pubmed_search": fetch_pubmed, "openalex": fetch_openalex,
                "crossref": fetch_crossref, "semantic_scholar": fetch_semantic_scholar,
                "dblp": fetch_dblp, "openreview": fetch_openreview,
            }
            if kind not in fetchers:
                raise ValueError(f"不支援的 feed type: {kind}")
            items = fetchers[kind](feed)
            if feed.get("textile_only"):
                items = [item for item in items if is_textile(item)]
            if feed.get("allowed_types"):
                items = [item for item in items if item.get("work_type") in feed["allowed_types"]]
            if feed.get("journal_whitelist"):
                allowed_journals = {journal.lower() for journal in feed["journal_whitelist"]}
                items = [item for item in items if item.get("journal", "").lower() in allowed_journals]
            stats["feeds_ok"] += 1
        except Exception as e:
            print(f"  ✗ {feed['key']} ({kind}) FAIL: {e}")
            stats["feeds_fail"] += 1
            continue
        n_new = 0
        for it in items:
            it["doi"] = (it.get("doi") or "").strip()
            iid = item_id(it["doi"], it["title"], feed["key"])
            sc, tags = score_paper(it, model)
            source_bonus = cfg.get("defaults", {}).get("source_quality", {}).get(kind, 0)
            if source_bonus:
                sc += source_bonus
                tags.append(f"source:{kind}")
            cat = ("recommended" if sc >= model["thresholds"]["recommend"]
                   else "candidate" if sc >= model["thresholds"]["candidate"] else "skipped")
            p = dict(item_id=iid, title=it["title"], source=feed["key"],
                     source_name=feed["name"], grp=feed["group"], authors=it["authors"],
                     url=it["url"], doi=it["doi"], abstract=it["abstract"],
                     pub_date=it["pub_date"], score=sc, tags=tags, category=cat)
            res = upsert(con, p, today)
            stats[res] += 1
            if res == "new":
                n_new += 1
        print(f"  ✓ {feed['key']:14} {kind:14} items={len(items):3} new={n_new}")
        con.commit()
        if kind == "pubmed_search":
            time.sleep(0.34)

    con.commit()

    # 匯出 papers.json（給前端；含分數>=candidate，依分數排序）
    rows = con.execute("""SELECT item_id,title,source,source_name,grp,authors,url,doi,abstract,
                          pub_date,score,tags,category,oa_status,oa_pdf_url,oa_first_date,
                          inst_subscribed,inst_platforms,sfx_url,metadata,first_seen,last_seen
                          FROM papers WHERE category!='skipped'
                          ORDER BY score DESC, first_seen DESC""").fetchall()
    cols = ["item_id","title","source","source_name","group","authors","url","doi","abstract",
            "pub_date","score","tags","category","oa_status","oa_pdf_url","oa_first_date",
            "inst_subscribed","inst_platforms","sfx_url","metadata","first_seen","last_seen"]
    papers = []
    for r in rows:
        d = dict(zip(cols, r))
        # OA 但實際抓不到全文（oa_status 標 OA 卻無可用 PDF）→ 先不顯示這篇。非 OA 照常顯示。
        if d["oa_status"] and d["oa_status"] != "closed" and not d["oa_pdf_url"]:
            continue
        d["tags"] = json.loads(d["tags"] or "[]")
        d["metadata"] = json.loads(d["metadata"] or "{}")
        d["isNew"] = (date.fromisoformat(d["first_seen"]) - date.today()).days >= -new_days
        # OA 剛被機械重抓到（first_seen 以後才開放全文）→ 前端可單獨顯示「新開放」
        d["oaNew"] = bool(d["oa_first_date"]) and \
            (date.fromisoformat(d["oa_first_date"]) - date.today()).days >= -new_days
        papers.append(d)

    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    payload = dict(updated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                   topic_groups=cfg["topic_groups"],
                   counts=dict(total_db=total, exported=len(papers)),
                   papers=papers)
    json.dump(payload, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n{'='*64}")
    print(f"feeds: {stats['feeds_ok']} ok / {stats['feeds_fail']} fail")
    print(f"papers: {stats['new']} new, {stats['updated']} updated, {total} total in DB")
    print(f"exported (non-skipped): {len(papers)} → {args.out}")
    # 分數分布
    from collections import Counter
    cc = Counter(con.execute("SELECT category FROM papers").fetchall()[i][0] for i in range(total)) if total else {}
    print("category:", dict(cc))
    con.close()


if __name__ == "__main__":
    main()
