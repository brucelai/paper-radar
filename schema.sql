-- D1 schema for paper-radar 動作層
-- 建立：wrangler d1 execute paper-radar-db --file=schema.sql
CREATE TABLE IF NOT EXISTS actions (
  item_id   TEXT PRIMARY KEY,
  doi       TEXT,
  title     TEXT,
  vote      TEXT,           -- 'up' / 'down' / 'neutral' / NULL（訓練 interest_model）
  seen      INTEGER,        -- ✅ 已看過（按任一動作鈕都會標）；預設隱藏，純標記不觸發下游
  star      INTEGER,        -- (deprecated) 舊「📌 收藏」；保留供舊列相容，前端已不再寫
  zotero    INTEGER,        -- (deprecated) 舊「📥 Zotero」；Zotero 改由 /paper-sync 共用前置自動加
  deepread  INTEGER,        -- 🔬 品質評讀 → /paper-review（沿用舊欄名 deepread）
  content   INTEGER,        -- 📚 內容整理 → /paper-digest
  pdf_key   TEXT,           -- R2 內全文 PDF key（📎 上傳）
  synced    INTEGER DEFAULT 0,  -- /paper-sync 處理後設 1
  updated   TEXT
);
CREATE INDEX IF NOT EXISTS idx_synced ON actions(synced);

-- 上傳用量計數（worker 月配額硬擋用）
CREATE TABLE IF NOT EXISTS usage (
  month   TEXT PRIMARY KEY,   -- YYYY-MM
  uploads INTEGER DEFAULT 0,
  bytes   INTEGER DEFAULT 0
);

-- 事件歷史（append-only，PRPM v2）。worker 在 /api/action、/api/upload 雙寫；
-- 建表需以帶 D1:Edit 權限的 token 執行本檔（cron 只需 D1:Read）。
CREATE TABLE IF NOT EXISTS action_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT,
  kind    TEXT,           -- 動作種類：vote/seen/deepread/content/upload...
  val     TEXT,
  ctx     TEXT,           -- JSON: {explore, rank, badge}（前端 persist() 帶入，最長 200 字）
  ts      TEXT
);
CREATE INDEX IF NOT EXISTS idx_al_item ON action_log(item_id);
CREATE INDEX IF NOT EXISTS idx_al_ts ON action_log(ts);
