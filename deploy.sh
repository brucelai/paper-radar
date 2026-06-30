#!/bin/bash
# paper-radar 正式部署路徑（走 ssh tar-pipe，不用 scp）。
# 從本機執行：bash deploy.sh [run]
#   不帶參數 → 只把程式碼/前端推到主機 ~/paper-radar
#   帶 run   → 推完立即在主機跑一次完整 run.sh（fetch→enrich→recheck→deploy）
# 只推「程式 + 前端 + 設定」，不碰 DB、.env、產生檔(papers.json)。
set -e
cd "$(dirname "$0")"
HOST=youruser@your-host.example.com
REMOTE=paper-radar

FILES="enrich.py fetch_and_score.py run.sh deploy.sh config.yaml interest_model.json \
schema.sql monitor_r2.py monitor_r2.sh notify_pending.py notify_pending.sh notify_digest.py \
sync_actions.py extract_facets.py embed_papers.py train_model.py rank.py \
docs/DESIGN-PRPM.md \
site/app.js site/index.html site/style.css site/_worker.js \
site/profile.html site/profile.js redeploy.sh"
# 註：train_interest.py / training_log.jsonl 為本機端（canonical db+log 在本機），不推主機

echo "=== 推送到 $HOST:~/$REMOTE ==="
tar czf - $FILES | ssh -o BatchMode=yes "$HOST" "cd ~/$REMOTE && tar xzf -"
echo "✅ 檔案已部署"

if [ "$1" = "run" ]; then
  echo "=== 在主機跑一次完整 pipeline ==="
  ssh -o BatchMode=yes "$HOST" "cd ~/$REMOTE && timeout 900 bash run.sh 2>&1 | tail -18"
fi
echo "=== done ==="
