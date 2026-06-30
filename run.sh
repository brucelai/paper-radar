#!/bin/bash
# paper-radar host cron：fetch → enrich(incremental) → deploy。
# CF token / ntfy 設定取自專案根目錄的 .env（見 env.example）。
set -e
cd "$(dirname "$0")"
export PATH=/usr/bin:/usr/local/bin:$PATH   # cron 精簡 PATH 保險（node/wrangler 在 /usr/bin）

# 載入環境變數：CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / NTFY_* …
set -a; source .env; set +a

source venv/bin/activate

echo "=== $(date '+%F %T') fetch ==="
python fetch_and_score.py

echo "=== enrich (incremental: enriched=0 only) ==="
python enrich.py --workers 6

echo "=== recheck (太新當時抓不到的 OA，回頭重抓 60 天內論文) ==="
python enrich.py --recheck 60 --workers 6

# ===== PRPM v2（docs/DESIGN-PRPM.md）：每段失敗都降級、不擋管線 =====
echo "=== PRPM sync_actions (D1 → cache) ==="
python sync_actions.py || echo "sync_actions 失敗（略過，沿用舊 cache）"

echo "=== PRPM facets (LLM，無 GROQ_API_KEY 自動跳過) ==="
python extract_facets.py || echo "facets 失敗（略過）"

echo "=== PRPM embeddings ==="
python embed_papers.py || echo "embedding 失敗（略過）"

echo "=== PRPM train ==="
python train_model.py || echo "train 失敗（略過）"

echo "=== PRPM rank（失敗 → 沿用 fetch_and_score 的 papers.json）==="
python rank.py || echo "rank 退場，沿用 keyword 排序"

cp papers.json site/papers.json

echo "=== deploy ==="
wrangler pages deploy site --project-name=paper-radar --branch=main --commit-dirty=true

echo "=== digest（每日高分新篇 ntfy；失敗不影響管線）==="
python notify_digest.py || echo "digest 失敗（略過）"

echo "=== done $(date '+%F %T') ==="
