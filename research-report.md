# 研究報告（改寫版）  
**主題：將 paper-radar 的資料檢索來源鎖定於 ML / DS / CV / Robotics / Textile Engineering**

## 1. 可直接採用的結論

建議以**三層來源策略**落地：

1. **核心索引層（高覆蓋）**：arXiv、OpenAlex、Crossref、Semantic Scholar、dblp、OpenReview。  
2. **領域約束層（高精準）**：  
   - ML / CV / Robotics：用 arXiv 分類碼 + OpenReview venue + OpenAlex topic 做雙重過濾。  
   - Textile：採「期刊白名單 + 纖維/紡織關鍵詞」硬限制。  
3. **資料集補充層（實驗與應用）**：OpenML、Hugging Face Datasets、Kaggle、Google Dataset Search。  

在 `paper-radar` 既有架構下，不需重寫 pipeline；只需擴充來源設定、分群規則與興趣模型即可落地。

## 2. 與現有專案的對接判斷

- 現有流程已支援來源抽取、去重、評分與輸出，可承接新來源。  
- `config.example.yaml` 的結構可直接擴展為多領域群組設定。  
- 目前醫學導向的 feed 可替換為技術領域來源，不影響後續 ranking/enrich 機制。  

## 3. 推薦來源白名單

### 3.1 核心學術索引（跨領域共用）

- **arXiv API**：適合預印本即時追蹤；可用 category 做硬過濾。  
- **OpenAlex API**：提供 works/topics 分層語義過濾（domain > field > subfield > topic）。  
- **Crossref REST API**：DOI 與出版 metadata 補全。  
- **Semantic Scholar API**：citation/network 與額外 metadata 補充。  
- **dblp Search API**：CS 會議/作者/venue 的次級驗證。  
- **OpenReview**：ML/CV/Robotics 高品質會議審稿流資料。  

### 3.2 領域專用強約束

- **Machine Learning**：`cs.LG` + OpenReview(NeurIPS/ICLR 等) + OpenAlex ML topics。  
- **Computer Vision**：`cs.CV` + OpenReview(CV 相關 venue) + OpenAlex CV topics。  
- **Robotics**：`cs.RO` + CoRL/robotics venues + OpenAlex robotics/control topics。  
- **Data Science**：OpenAlex topics + Crossref type（`journal-article` / `proceedings-article`）。  
- **Textile Engineering（重點）**：  
  - 期刊白名單：Textile Research Journal、Journal of The Textile Institute、AUTEX Research Journal。  
  - 關鍵詞白名單：`textile`, `fiber`, `fabric`, `yarn`, `weaving`, `nonwoven`, `smart textile`。  
  - OpenAlex/Crossref 僅做 metadata enrich，不作來源放寬。  

### 3.3 資料集來源（可選）

- **OpenML**：benchmark datasets/tasks/runs。  
- **Hugging Face Datasets**：CV/多模態資料。  
- **Kaggle Datasets**：產業與競賽型資料補充。  
- **Google Dataset Search**：跨站聚合查漏補缺。  

## 4. 查詢模板（可直接轉成規則）

```text
[ML]
source in {arXiv, OpenReview, OpenAlex}
AND (arxiv_category=cs.LG OR topic in ML_topics)
AND year >= current_year - 3

[DS]
source in {OpenAlex, Crossref, dblp}
AND topic in {data mining, statistical learning, analytics}
AND type in {journal-article, proceedings-article}

[CV]
source in {arXiv, OpenReview, OpenAlex}
AND (arxiv_category=cs.CV OR topic in CV_topics)

[Robotics]
source in {arXiv, OpenReview, OpenAlex}
AND (arxiv_category=cs.RO OR topic in robotics/control/autonomous_systems)

[Textile]
source_journal in {TRJ, JTI, AUTEX}
OR topic contains any {textile, fiber, fabric, yarn, weaving, nonwoven, smart textile}
AND must_match_textile_keyword >= 1
```

## 5. 最小改動實施方案（paper-radar）

1. 在 `config.yaml` 新增五個 group：`ml`, `ds`, `cv`, `robotics`, `textile`。  
2. 將各 group feeds 改為上述白名單來源（保留既有 schema 與排程方式）。  
3. 於 `interest_model.json` 新增領域關鍵詞與權重，避免跨領域誤收。  
4. `enrich.py` 保留現有 enrich 流程，新增 Crossref/OpenAlex/S2 fallback。  
5. 增加「source_quality」或「domain_match」加權欄位，讓 textile 不被高熱度 ML 論文淹沒。  

## 6. 風險與治理建議

- 部分平台有 rate limit、授權或反爬限制；應優先使用公開且穩定 API。  
- Textile 開放資料較少，需長期維護期刊白名單。  
- 建議建立月度校準機制：抽樣檢查 precision/recall，調整 topic 與權重。  

## 7. 參考來源

- arXiv API User Manual: https://info.arxiv.org/help/api/user-manual.html  
- arXiv Category Taxonomy: https://arxiv.org/category_taxonomy  
- OpenAlex Docs: https://developers.openalex.org/  
- OpenAlex Works API: https://developers.openalex.org/api-reference/works  
- OpenAlex Topics API: https://developers.openalex.org/api-reference/topics  
- Semantic Scholar API: https://www.semanticscholar.org/product/api  
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/  
- dblp Search API FAQ: https://dblp.org/faq/How+to+use+the+dblp+search+API.html  
- OpenReview: https://openreview.net/about  
- OpenML Docs: https://docs.openml.org/  
- Hugging Face Datasets: https://huggingface.co/docs/hub/datasets  
- Kaggle Datasets: https://www.kaggle.com/datasets  
- Google Dataset Search: https://datasetsearch.research.google.com/help
