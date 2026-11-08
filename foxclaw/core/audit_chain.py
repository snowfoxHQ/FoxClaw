"""审计 JSONL 的哈希链:让「改一笔、删一笔」这类事后篡改可被检测。

为什么需要
──────────
`{data_dir}/audit/*.jsonl` 是纯 append 的明文文件:谁能写这个文件,谁就能改历史
(把 `allowed=false` 改成 `true`、删掉某次 deny)。审计的价值建立在「事后还能信」上,
所以每行补两个字段:

    prev_hash = 上一条的 hash(首条为 GENESIS)
    hash      = sha256(canonical(除 hash 外的整行))

`verify()` 逐行重算,任何一行被改(hash 不匹配)或被删(后继 prev_hash 断)都会暴露。

适用边界(别高估它)
──────────────────
· 防**零散篡改**,不防「整文件重算链」——攻击者若能连改 N 行并重算全部 hash,链自洽。
  真要防这个,得把 tail hash 锚到外部(另一台机器 / 时间戳服务);本机单进程场景
  收益不抵复杂度。
· 删**最后一条**检测不到:没有后继记录指向它。删中间、改任意行都能发现。
· 老文件(该行无 hash 字段)按 legacy 段跳过校验,不会因为历史数据报错;
  一旦出现过 hash,后面的行就必须接上。
· 链是**单进程假设**(与两个 sink 的锁一致)。多进程各自 append 会让 prev_hash 冲突,
  表现为 verify 报链断——部署上应避免同目录多进程写审计。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: 首条记录的 prev_hash。用 64 个 0 而不是空串:一眼能看出「这是链头」。
GENESIS = "0" * 64

#: 从文件尾部回读的字节数:找 tail hash 只需最后一行,64KB 足够容下超长记录。
_TAIL_WINDOW = 65536


def _canonical(rec: dict) -> bytes:
    """稳定序列化:sort_keys + 紧凑分隔符,保证跨进程/跨版本重算一致。"""
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def link(rec: dict, prev_hash: str = GENESIS) -> dict:
    """给一行记录接上链,返回**新** dict(不改入参,便于调用方继续用原始 rec)。"""
    body = dict(rec)
    body["prev_hash"] = prev_hash
    body["hash"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def tail_hash(path) -> str:
    """读文件最后一条带 hash 的记录的 hash;不存在/空/无链 → GENESIS。

    倒着找而不是顺着读:审计文件可能很长,重启续链只需要最后一行;
    尾部若有写一半的残行(进程被杀),跳过继续往前找。
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL_WINDOW))
            chunk = f.read().decode("utf-8", "replace")
    except OSError:
        return GENESIS
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        h = rec.get("hash")
        if isinstance(h, str) and h:
            return h
    return GENESIS


def verify(path) -> tuple:
    """校验整条链 → (ok, 已校验条数, 问题描述)。

    问题描述为空串表示通过;不通过时给出「第几行 + 断在哪」,便于人工定位。
    """
    p = Path(path)
    if not p.exists():
        return True, 0, ""
    prev = GENESIS
    n = 0
    try:
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    return False, n, f"第 {i} 行不是合法 JSON"
                h = rec.get("hash")
                if not h:
                    continue                      # legacy 段:无链可验,跳过
                if rec.get("prev_hash") != prev:
                    return False, n, (f"第 {i} 行链断:prev_hash 与上一条 hash 不符"
                                      "(中间记录被删或被挪动)")
                body = {k: v for k, v in rec.items() if k != "hash"}
                if hashlib.sha256(_canonical(body)).hexdigest() != h:
                    return False, n, f"第 {i} 行内容被改(hash 不匹配)"
                prev = h
                n += 1
    except OSError as e:
        return False, n, f"读取失败: {e}"
    return True, n, ""


def _main(argv: list) -> int:
    if not argv:
        print("用法: python -m foxclaw.core.audit_chain <audit.jsonl> [...]")
        return 2
    bad = 0
    for p in argv:
        ok, n, msg = verify(p)
        print(f"{p}: {'OK' if ok else 'BROKEN'} ({n} 条)" + (f" - {msg}" if msg else ""))
        if not ok:
            bad = 1
    return bad


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
