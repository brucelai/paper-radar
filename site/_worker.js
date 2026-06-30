// Cloudflare Pages advanced-mode Worker — 論文學習雷達後端
//   POST /api/action        → upsert 動作到 D1 (env.DB)
//   POST /api/upload        → 全文 PDF 存 R2 (env.PDFS) + 記 D1（含自設月配額硬擋）
//                              radar 論文帶 ?item_id；外部論文不帶 → 自動產 manual:<ts>
//   GET  /api/state         → 回傳所有未同步動作（給 /paper-sync 拉）
//   其餘                     → 靜態資源
// 整站在 CF Access 後面，呼叫者已是本人。綁定：D1=DB, R2=PDFS。
// PRPM v2：/api/action 與 /api/upload 皆對 action_log（append-only 事件歷史）雙寫，
//   包在 try/catch → 尚未 migration 建表時（缺 action_log）也不得 500。

// deepread = 🔬 品質評讀（沿用舊欄名）；content = 📚 內容整理。star/zotero 保留供舊列相容，前端已不再寫。
const COLS = { vote: "vote", seen: "seen", star: "star", zotero: "zotero", deepread: "deepread", content: "content" };
const UPLOAD_CAP = 300;            // 自設每月上傳上限（硬擋，獨立於 CF）。超過 → 429
const MAX_BYTES = 30 * 1024 * 1024; // 單檔 30MB 上限

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/action" && request.method === "POST") {
      try {
        const d = await request.json();
        const id = String(d.item_id || "").slice(0, 64);
        const col = COLS[d.key];
        if (!id || !col) return json({ error: "bad params" }, 400);
        const val = d.key === "vote" ? (d.val ? String(d.val).slice(0, 8) : null) : (d.val ? 1 : 0);
        const ts = new Date().toISOString();
        await env.DB.prepare(
          `INSERT INTO actions (item_id, doi, title, ${col}, updated, synced)
           VALUES (?1, ?2, ?3, ?4, ?5, 0)
           ON CONFLICT(item_id) DO UPDATE SET ${col}=?4, updated=?5, synced=0`
        ).bind(id, String(d.doi || "").slice(0, 120), String(d.title || "").slice(0, 300), val, ts).run();
        try {
          await env.DB.prepare(
            `INSERT INTO action_log (item_id, kind, val, ctx, ts) VALUES (?1,?2,?3,?4,?5)`
          ).bind(id, d.key, String(val), d.ctx ? JSON.stringify(d.ctx).slice(0, 200) : null, ts).run();
        } catch (e) { /* action_log 尚未遷移建表前必然失敗；不可影響主流程 */ }
        return json({ ok: true });
      } catch (e) {
        return json({ error: "fail", detail: String(e) }, 500);
      }
    }

    if (url.pathname === "/api/upload" && request.method === "POST") {
      try {
        const month = new Date().toISOString().slice(0, 7);  // YYYY-MM
        // 月配額硬擋
        const used = await env.DB.prepare("SELECT uploads FROM usage WHERE month=?1").bind(month).first();
        if (used && used.uploads >= UPLOAD_CAP)
          return json({ error: "monthly upload cap reached", cap: UPLOAD_CAP }, 429);

        const cl = parseInt(request.headers.get("content-length") || "0", 10);
        if (cl > MAX_BYTES) return json({ error: "file too large", max_mb: 30 }, 413);

        // radar 論文帶 item_id；外部論文自動產 manual:<ts>
        let id = url.searchParams.get("item_id");
        const isManual = !id;
        if (!id) id = "manual:" + Date.now().toString(36);
        const title = (url.searchParams.get("title") || "").slice(0, 300);
        const doi = (url.searchParams.get("doi") || "").slice(0, 120);
        const deepread = url.searchParams.get("deepread") === "1" ? 1 : null;  // 🔬 品質
        const content = url.searchParams.get("content") === "1" ? 1 : null;     // 📚 內容
        const key = `pdf/${id.replace(/[^a-zA-Z0-9:_-]/g, "_")}.pdf`;

        await env.PDFS.put(key, request.body, { httpMetadata: { contentType: "application/pdf" } });
        const ts = new Date().toISOString();
        await env.DB.prepare(
          `INSERT INTO actions (item_id, doi, title, pdf_key, deepread, content, updated, synced)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 0)
           ON CONFLICT(item_id) DO UPDATE SET pdf_key=?4, deepread=COALESCE(?5,deepread), content=COALESCE(?6,content), updated=?7, synced=0`
        ).bind(id, doi, title, key, deepread, content, ts).run();
        try {
          await env.DB.prepare(
            `INSERT INTO action_log (item_id, kind, val, ctx, ts) VALUES (?1,?2,?3,?4,?5)`
          ).bind(id, "upload", key, null, ts).run();
        } catch (e) { /* action_log 尚未遷移建表前必然失敗；不可影響主流程 */ }
        // usage 計數
        await env.DB.prepare(
          `INSERT INTO usage (month, uploads, bytes) VALUES (?1, 1, ?2)
           ON CONFLICT(month) DO UPDATE SET uploads=uploads+1, bytes=bytes+?2`
        ).bind(month, cl).run();
        return json({ ok: true, key, item_id: id, manual: isManual });
      } catch (e) {
        return json({ error: "fail", detail: String(e) }, 500);
      }
    }

    if (url.pathname === "/api/state" && request.method === "GET") {
      const onlyNew = url.searchParams.get("unsynced") === "1";
      const q = onlyNew ? "SELECT * FROM actions WHERE synced=0 ORDER BY updated"
                        : "SELECT * FROM actions ORDER BY updated";
      const { results } = await env.DB.prepare(q).all();
      return json({ actions: results });
    }

    return env.ASSETS.fetch(request);
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}
