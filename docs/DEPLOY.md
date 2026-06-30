# 部署指南 · Deployment Guide

論文雷達分兩半：**(A) 主機上的抓取管線**（cron 跑 Python）與 **(B) Cloudflare 上的私密網頁 + 動作層**。本機只跑抓取＋評分不需要任何雲服務；要「在手機上滑、跨裝置記住勾選」才需要 Cloudflare。

The radar has two halves: **(A) the fetch pipeline on a host** (Python via cron) and **(B) the private web + action layer on Cloudflare**. Running fetch+scoring locally needs no cloud; you only need Cloudflare for the swipe-on-mobile, cross-device action layer.

---

## 前置 · Prerequisites

- Python 3.11+
- Node + [`wrangler`](https://developers.cloudflare.com/workers/wrangler/)（`npm i -g wrangler`），並 `wrangler login`
- 一個 Cloudflare 帳號（D1 / R2 / Pages / Zero Trust Access 都在免費額度內）
- 一台能跑 cron 的主機（VPS / Oracle Free Tier / 家裡的 Pi 或 NAS / 排程 CI 皆可）
- 一個 [Unpaywall](https://unpaywall.org/products/api) 用的 email（免費，填進 `config.yaml`）
- （選用）一個 [ntfy](https://ntfy.sh/) topic 做推播

---

## A · 本機抓取（先跑通這個）

```bash
cp config.example.yaml config.yaml
cp interest_model.example.json interest_model.json
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python fetch_and_score.py        # 全部 feed → SQLite + papers.json
python enrich.py --limit 20      # 加值（OA / 機構訂閱）
```

`fetch_and_score.py` 會建 `paper_radar.db`（SQLite）並輸出 `papers.json`。前端讀 `papers.json` 渲染。

---

## B · Cloudflare 私密站

### 1. D1（動作資料庫）
```bash
wrangler d1 create paper-radar-db
# 把回傳的 database_id 填進 wrangler.toml（先 cp wrangler.toml.example wrangler.toml）
wrangler d1 execute paper-radar-db --remote --file=schema.sql
```

### 2. R2（選用——只有要「上傳外部 PDF」功能才需要）
```bash
wrangler r2 bucket create paper-radar-pdfs
```
不用上傳功能的話，可以略過 R2，並把前端上傳區拿掉／忽略。

### 3. Pages 部署
```bash
cp papers.json site/papers.json      # 把產生的資料放進 site/（run.sh 會自動做）
wrangler pages deploy site --project-name=paper-radar --branch=main
```
之後在 Cloudflare dashboard 把自訂網域 CNAME 綁到 `paper-radar.pages.dev`（proxied）。

### 4. Cloudflare Access（**最關鍵的一步：把站鎖起來**）
在 Zero Trust → Access → Applications 建一個 **self-hosted application** 蓋住你的網域（pages.dev 與自訂網域各建一個，或用萬用），policy = allow `你的 email`。登入方式用 email OTP 即可（要一鍵登入可另接 Google IdP）。

> ⚠️ **先設好 Access 再放資料。** 在 Access 生效前，站是全網可讀的。

### 5. 主機 cron
```bash
# 在 host 上
git clone <你的 repo>   # 或 scp / 用本 repo 的 deploy.sh tar-over-ssh
cd paper-radar
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp env.example .env     # 填 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / NTFY_TOKEN…
# crontab -e
0 22 * * *  cd ~/paper-radar && bash run.sh >> ~/paper-radar/cron.log 2>&1
```

`run.sh` = fetch → enrich → `wrangler pages deploy` → 推播 digest。

---

## token 權限 · Token scopes

| 用途 | 權限 | 放哪 |
|---|---|---|
| 主機 cron 部署 + 讀動作 | `Pages:Edit` + `Account > D1:Read` | host `.env`（最小權限） |
| schema 遷移（ALTER）| `Account > D1:Edit` | 你信任的機器，臨時用，**別長駐 host** |
| R2 用量監測（選用）| `Account Analytics:Read` | 同 host `.env` |
| 自訂網域 DNS（建 CNAME 時）| `Zone:Read` + `DNS:Edit` | 一次性 |

D1 schema 變更見 [`D1_MIGRATIONS.md`](D1_MIGRATIONS.md)。

---

## 安全檢查清單 · Security checklist

- [ ] Cloudflare Access 已生效（用無痕視窗確認沒登入會被擋）
- [ ] host token 只有 `Pages:Edit` + `D1:Read`
- [ ] `.env` / `wrangler.toml`（含真 database_id）/ `*.db` / `papers.json` 都不在版控（`git status` 確認）
- [ ] worker 的月上傳配額 / 單檔大小上限保留
- [ ] ntfy 用私有 + token 保護的 topic
- [ ] 機構訂閱層若開啟，確認自己有授權、取用遵守出版社 ToS
