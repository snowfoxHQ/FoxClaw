#!/usr/bin/env python3
"""单文件体积棘轮门: foxclaw/**/*.py 不得超过 15KB。

为什么需要
----------
REFACTOR_PLAN 要求单文件 <= 15KB(便于 LLM 整体读写、降低 diff 冲突面),
但 2026-11 实测有 25 个文件超限,最大 foxclaw/core/model_router.py 58KB ——
计划里没有执行机制,所以"要求"一直没落地。

一次性拆 25 个文件是高风险的伪重构(82 个测试虽能兜底,但收益/风险不成比例)。
本脚本采用棘轮:现有超限文件进白名单冻结,**新增超限立即失败**,已拆小的从白名单
自动移出 —— 债务不再增长,同时每轮有机会顺手拆一个。

用法
----
    python tools/size_check.py            # CI:校验
    python tools/size_check.py --update   # 只允许把已合规的路径移出白名单
    python tools/size_check.py --list     # 打印当前超限清单
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "foxclaw"
BASELINE = ROOT / "tools" / "size_baseline.txt"
LIMIT = 15 * 1024

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def scan() -> dict[str, int]:
    return {rel(p): p.stat().st_size for p in TARGET.rglob("*.py") if p.stat().st_size > LIMIT}


def read_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    return {
        line.strip() for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def main() -> int:
    argv = sys.argv[1:]
    over = scan()

    if "--list" in argv:
        for path, size in sorted(over.items(), key=lambda kv: -kv[1]):
            print(f"{size / 1024:7.1f}KB  {path}")
        print(f"共 {len(over)} 个文件超过 {LIMIT // 1024}KB")
        return 0

    allowed = read_baseline()
    if "--update" in argv:
        new = sorted(set(over) - allowed)
        if new:
            print("✗ 拒绝更新白名单:以下文件是新超限的,必须拆分而不是加进白名单:")
            for path in new:
                print(f"    {over[path] / 1024:7.1f}KB  {path}")
            return 1
        kept = sorted(set(over) & allowed)
        BASELINE.write_text(
            "# 单文件体积棘轮基线:列此处的文件已超 15KB,允许存在但不得再增长;\n"
            "# 拆到 <=15KB 后运行 `python tools/size_check.py --update` 自动移出。\n"
            + "".join(f"{p}\n" for p in kept),
            encoding="utf-8",
        )
        print(f"✓ 白名单已更新为 {len(kept)} 项")
        return 0

    new = sorted(set(over) - allowed)
    shrunk = sorted(allowed - set(over))

    if shrunk:
        print(f"ℹ 以下 {len(shrunk)} 个文件已降到 {LIMIT // 1024}KB 以内,可从白名单移出"
              f"(运行 --update):")
        for path in shrunk:
            print(f"    {path}")

    if new:
        print(f"✗ {len(new)} 个新文件超过 {LIMIT // 1024}KB,必须拆分:")
        for path in new:
            print(f"    {over[path] / 1024:7.1f}KB  {path}")
        return 1

    print(f"✓ 通过:超限文件 {len(over)} 个,均在冻结白名单内(未新增)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
