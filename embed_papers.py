#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Embedding stage：對 exported（非 skipped）且尚無 embedding 的論文算向量。

- fastembed (ONNX, CPU) BAAI/bge-small-en-v1.5，384 維 float32 → papers.embedding BLOB。
- 首跑會下載模型（~130MB 到 ~/.cache）；之後每日增量 ~20 篇、秒級。
- fastembed 裝不起來（ARM 主機極端情況）→ 印警告直接 return，管線不倒。
  備援路線（未實作，見 DESIGN §9）：CF Workers AI @cf/baai/bge-small-en-v1.5。
"""
import argparse, sqlite3, struct
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384


def to_blob(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def from_blob(b):
    return list(struct.unpack(f"{len(b)//4}f", b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(SCRIPT_DIR / "paper_radar.db"))
    ap.add_argument("--cap", type=int, default=500)
    args = ap.parse_args()

    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("fastembed 未安裝 → 跳過 embedding（pip install fastembed）")
        return

    con = sqlite3.connect(args.db)
    if not any(c[1] == "embedding" for c in con.execute("PRAGMA table_info(papers)")):
        con.execute("ALTER TABLE papers ADD COLUMN embedding BLOB")
        con.commit()

    rows = con.execute(
        """SELECT item_id, title, abstract FROM papers
           WHERE category != 'skipped' AND embedding IS NULL
           ORDER BY first_seen DESC LIMIT ?""", (args.cap,)).fetchall()
    if not rows:
        print("embedding 無新增")
        return
    print(f"待算 embedding：{len(rows)} 篇")

    model = TextEmbedding(model_name=MODEL_NAME)
    texts = [((t or "") + " " + (a or "")[:1000]).strip() for _, t, a in rows]
    for (iid, _, _), vec in zip(rows, model.embed(texts, batch_size=16)):
        con.execute("UPDATE papers SET embedding=? WHERE item_id=?",
                    (to_blob(list(map(float, vec))), iid))
    con.commit()
    con.close()
    print(f"✓ embedding 完成 {len(rows)} 篇（{MODEL_NAME}, {DIM}d）")


if __name__ == "__main__":
    main()
