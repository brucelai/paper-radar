#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨專案 R2 用量監測：查 CF GraphQL 帳號層 R2 storage + operations（**所有 bucket/專案**），
   達免費額度 80% → ntfy paper-radar。Oracle cron 每日。

env: CLOUDFLARE_API_TOKEN (需 Account Analytics:Read), CLOUDFLARE_ACCOUNT_ID, NTFY_TOKEN,
     NTFY_BASE, NTFY_TOPIC
note: R2 啟用後首次跑請人工核對一次數字（GraphQL R2 dataset 欄位）。
"""
import os, json, datetime, urllib.request

ACC = os.environ["CLOUDFLARE_ACCOUNT_ID"]
TOK = os.environ["CLOUDFLARE_API_TOKEN"]
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")
NTFY_BASE = os.environ.get("NTFY_BASE", "https://ntfy.example.com")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "paper-radar")
NTFY_URL = f"{NTFY_BASE}/{NTFY_TOPIC}"

# 免費額度（每月）
FREE = {"storage_gb": 10, "class_a": 1_000_000, "class_b": 10_000_000}
ALERT_AT = 0.80   # 達 80% 告警

# Class A = 寫/列出；Class B = 讀。其餘歸 B（保守）。
CLASS_A = {"PutObject", "PutMultipartObject", "CompleteMultipartUpload", "CreateMultipartUpload",
           "UploadPart", "CopyObject", "ListObjects", "ListBuckets", "PutBucket", "DeleteObject"}


def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql", data=body,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def ntfy(title, msg, tags="warning"):
    if not NTFY_TOKEN:
        print("NTFY_TOKEN 未設，跳過推播"); return
    body = json.dumps({"topic": NTFY_TOPIC, "title": title, "message": msg, "tags": [tags]}).encode()
    req = urllib.request.Request(NTFY_URL, data=body,
        headers={"Authorization": f"Bearer {NTFY_TOKEN}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15)


def main():
    today = datetime.date.today()
    start = today.replace(day=1).isoformat() + "T00:00:00Z"
    end = today.isoformat() + "T23:59:59Z"

    # 1) Storage（取本月最新一筆 max）
    q_store = """query($acc:String!,$s:Time!,$e:Time!){viewer{accounts(filter:{accountTag:$acc}){
      r2StorageAdaptiveGroups(limit:1,
        filter:{datetime_geq:$s,datetime_leq:$e}){max{payloadSize metadataSize objectCount}}}}}"""
    # 2) Operations（本月加總，依 actionType 分 A/B）
    q_ops = """query($acc:String!,$s:Time!,$e:Time!){viewer{accounts(filter:{accountTag:$acc}){
      r2OperationsAdaptiveGroups(limit:100,
        filter:{datetime_geq:$s,datetime_leq:$e}){sum{requests} dimensions{actionType}}}}}"""
    v = {"acc": ACC, "s": start, "e": end}

    store = gql(q_store, v)["data"]["viewer"]["accounts"][0]["r2StorageAdaptiveGroups"]
    sb = (store[0]["max"] if store else {"payloadSize": 0, "metadataSize": 0})
    storage_gb = (sb.get("payloadSize", 0) + sb.get("metadataSize", 0)) / 1e9

    ops = gql(q_ops, v)["data"]["viewer"]["accounts"][0]["r2OperationsAdaptiveGroups"]
    ca = sum(o["sum"]["requests"] for o in ops if o["dimensions"]["actionType"] in CLASS_A)
    cb = sum(o["sum"]["requests"] for o in ops if o["dimensions"]["actionType"] not in CLASS_A)

    pct = {"storage": storage_gb / FREE["storage_gb"],
           "class_a": ca / FREE["class_a"], "class_b": cb / FREE["class_b"]}
    print(f"R2 本月：storage {storage_gb:.3f}/{FREE['storage_gb']}GB ({pct['storage']*100:.1f}%) | "
          f"ClassA {ca:,}/{FREE['class_a']:,} ({pct['class_a']*100:.1f}%) | "
          f"ClassB {cb:,}/{FREE['class_b']:,} ({pct['class_b']*100:.1f}%)")

    hot = {k: p for k, p in pct.items() if p >= ALERT_AT}
    if hot:
        lines = [f"{k}: {p*100:.0f}% of free tier" for k, p in hot.items()]
        ntfy("⚠️ R2 用量逼近免費額度", "跨專案 R2 月用量：\n" + "\n".join(lines) +
             f"\n\nstorage {storage_gb:.2f}GB / A {ca:,} / B {cb:,}", tags="warning")
        print("已告警:", hot)
    else:
        print("用量正常，無告警")


if __name__ == "__main__":
    main()
