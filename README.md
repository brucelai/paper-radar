# paper-radar · 技術研究雷達

個人化的 ML、Data Science、Computer Vision、Robotics 與 Textile Engineering 文獻追蹤工具。它從公開學術索引抓取新研究、依興趣模型排序，並發布到可用 Cloudflare Access 保護的私人網頁。

## 來源與領域規則

| 領域 | 主來源 | 硬限制 |
|---|---|---|
| Machine Learning | arXiv、OpenReview、OpenAlex | `cs.LG`、ML 主題、NeurIPS/ICLR 投稿流 |
| Data Science | OpenAlex、Crossref、dblp | data mining、statistical learning、analytics；期刊或會議論文 |
| Computer Vision | arXiv、OpenReview、OpenAlex | `cs.CV`、CV 主題 |
| Robotics | arXiv、OpenReview、OpenAlex | `cs.RO`、robotics/control/autonomous systems |
| Textile Engineering | Crossref、OpenAlex | Textile Research Journal、Journal of The Textile Institute、AUTEX Research Journal，且必須命中紡織關鍵詞 |

OpenAlex、Crossref、Semantic Scholar 僅用於來源查詢或 metadata 補全；它們不會放寬 textile 的白名單與關鍵詞限制。可自行在 `config.yaml` 加入 OpenML、Hugging Face Datasets、Kaggle 或其他資料集來源。

## 架構

```text
fetch_and_score.py  →  SQLite 去重、領域/興趣評分  →  papers.json
enrich.py           →  Unpaywall + Crossref/OpenAlex/Semantic Scholar metadata
rank.py             →  PRPM v2 偏好排序與多樣性重排
site/               →  Cloudflare Pages 私人閱讀與回饋介面
```

支援的 feed 類型：`rss`（含 arXiv category RSS）、`openalex`、`crossref`、`semantic_scholar`、`dblp`、`openreview`；仍保留 `pubmed_search` 以相容既有設定。

## 快速開始

需求：Python 3.11+。

```bash
git clone https://github.com/<you>/paper-radar.git
cd paper-radar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp interest_model.example.json interest_model.json
cp env.example .env

python fetch_and_score.py
python enrich.py --limit 20
```

以 `python -m http.server --directory site` 開啟前端。`site/papers.sample.json` 提供合成資料範例。

## 加值與偏好模型

`enrich.py` 會以 Unpaywall 標示開放取用，並以 Crossref、OpenAlex、Semantic Scholar 補齊期刊、出版日、主題及引用 metadata。任何外部 API 失敗都不會中斷管線。

PRPM v2 使用閱讀、評讀、整理及投票事件訓練個人偏好，並以 Thompson sampling、MMR 多樣性和探索槽排序。詳見 [`docs/DESIGN-PRPM.md`](docs/DESIGN-PRPM.md)。

## 部署與安全

部署步驟見 [`docs/DEPLOY.md`](docs/DEPLOY.md)。發布資料前，請先以 Cloudflare Access 或等效認證保護網站；將 token、`.env`、資料庫與產生的 `papers.json` 留在版本控制之外。使用任何全文連結時仍須遵守資料庫、機構與出版商的授權條款。

## 授權

[MIT](LICENSE)。
