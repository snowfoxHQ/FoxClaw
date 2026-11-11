#!/usr/bin/env python
"""复核存量冲突卡:用当前判定规则重算,把已经不成立的卡批量标记为误判。

默认 dry-run,只打印统计与样本;加 --apply 才真正调 /conflicts/bulk-dismiss。
写入必须走运行中的服务——只有它才持有 open 冲突集合与向量索引,直接改库会让
服务内的缓存与库不一致(那些因冲突被置 disputed 的 claim 也不会回到检索)。

用法:
    python tools/recheck_conflicts.py                    # 只看统计
    python tools/recheck_conflicts.py --apply            # 确认后批量关闭误判
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor" / "hkc"))

from hkc_kee.kee import _SimpleEmbedder  # noqa: E402

THRESHOLD = 0.30          # 与 KnowledgeEvolutionEngine.similarity_threshold 一致
DEFAULT_DB = ROOT / "foxclaw_data" / "hkc" / "hkc.db"


def load_open(conn):
    cols = [r[1] for r in conn.execute("pragma table_info(conflict_cards)")]
    idx = {c: i for i, c in enumerate(cols)}
    rows = conn.execute(
        "select * from conflict_cards where status='open' order by conflict_id"
    ).fetchall()
    return [
        {
            "conflict_id": r[idx["conflict_id"]],
            "a": r[idx["claim_a_id"]],
            "b": r[idx["claim_b_id"]],
            "domain": r[idx["domain"]],
        }
        for r in rows
    ]


def load_claims(conn):
    cols = [r[1] for r in conn.execute("pragma table_info(knowledge_units)")]
    idx = {c: i for i, c in enumerate(cols)}
    out = {}
    for r in conn.execute("select * from knowledge_units"):
        if r[idx["ku_type"]] != "Claim":
            continue
        try:
            extra = json.loads(r[idx["extra"]] or "{}")
        except Exception:
            extra = {}
        out[r[idx["ku_id"]]] = {
            "text": (extra.get("statement") or r[idx["name"]] or "").strip(),
            "claim_status": extra.get("claim_status", "active"),
        }
    return out


def classify(cards, claims, emb):
    buckets = {"claim_missing": [], "not_contradiction": [], "still_contradiction": []}
    for card in cards:
        ca, cb = claims.get(card["a"]), claims.get(card["b"])
        if not ca or not cb or not ca["text"] or not cb["text"]:
            buckets["claim_missing"].append(card)
            continue
        score = emb.score(ca["text"], cb["text"])
        card["score"] = round(score, 3)
        card["a_text"], card["b_text"] = ca["text"][:70], cb["text"][:70]
        key = "still_contradiction" if score < -THRESHOLD else "not_contradiction"
        buckets[key].append(card)
    return buckets


def post(base_url, path, payload, timeout=300):
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def main(argv=None):
    ap = argparse.ArgumentParser(description="复核并批量关闭误判冲突卡")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="HKC sqlite 路径(只读)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000",
                    help="运行中的服务地址(--apply 时用)")
    ap.add_argument("--apply", action="store_true", help="真正提交,默认只统计")
    ap.add_argument("--chunk", type=int, default=200, help="每次批量提交的张数")
    ap.add_argument("--sample", type=int, default=5, help="每类打印的样本数")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"找不到库文件:{db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    cards = load_open(conn)
    claims = load_claims(conn)
    if not cards:
        print("没有 open 冲突卡,无需处理")
        return 0

    buckets = classify(cards, claims, _SimpleEmbedder())
    closed = buckets["not_contradiction"] + buckets["claim_missing"]

    summary = {
        "open_total": len(cards),
        "not_contradiction": len(buckets["not_contradiction"]),
        "claim_missing": len(buckets["claim_missing"]),
        "still_contradiction": len(buckets["still_contradiction"]),
        "to_close": len(closed),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"open 冲突卡          {summary['open_total']}")
        print(f"  重算后不再矛盾     {summary['not_contradiction']}  ← 可关闭")
        print(f"  claim 已不存在     {summary['claim_missing']}  ← 可关闭")
        print(f"  仍然矛盾           {summary['still_contradiction']}  ← 留给人工裁决")

    if args.sample:
        print("\n仍然矛盾的样本(需人工看):")
        for c in buckets["still_contradiction"][:args.sample]:
            print(f"  {c['conflict_id']} score={c['score']}")
            print(f"    A: {c['a_text']}")
            print(f"    B: {c['b_text']}")

    if not args.apply:
        if closed:
            print(f"\ndry-run,未提交。确认后加 --apply 关闭 {len(closed)} 张。")
        return 0

    ids = [c["conflict_id"] for c in closed]
    done = 0
    for i in range(0, len(ids), args.chunk):
        batch = ids[i:i + args.chunk]
        res = post(args.base_url, "/conflicts/bulk-dismiss",
                   {"conflict_ids": batch, "resolved_by": "recheck",
                    "note": "复核误判：两个观点并非同一命题的正反两面"})
        done += int(res.get("dismissed", 0))
        print(f"  已关闭 {done}/{len(ids)}")
    print(f"完成:提交 {len(ids)} 张,实际关闭 {done} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
