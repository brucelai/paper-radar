#!/bin/bash
# 純前端/worker 重新部署到 CF Pages（不重抓 feeds，給只改 site/ 或 worker 的情況用）。
# 在主機 ~/paper-radar 執行：bash redeploy.sh
# 註：D1 schema 遷移（ALTER）必須在有 D1:Edit token 的機器跑 → 見 docs/D1_MIGRATIONS.md。
# CF token / ntfy 設定取自專案根目錄的 .env（見 env.example）。
set -e
cd "$(dirname "$0")"
export PATH=/usr/bin:/usr/local/bin:$PATH
set -a; source .env; set +a

echo "=== 部署 site 到 CF Pages ==="
cp papers.json site/papers.json
wrangler pages deploy site --project-name=paper-radar --branch=main --commit-dirty=true
echo "=== redeploy done ==="
