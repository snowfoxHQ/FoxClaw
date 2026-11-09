#!/usr/bin/env python3
"""mypy 棘轮门(mypy ratchet):类型错误数只允许下降,不允许新增。

为什么需要
----------
此前 CI 用 `continue-on-error: true` 跑 mypy,注释写着「存量 425 个类型错误」,
而 2026-11 实测已经是 **673** 个。非阻塞 + 没人核对数字 = 类型债务只涨不跌,
"渐进式收紧"在事实上从未发生。本脚本把那句注释变成可执行的门禁:

  当前错误数 > 基线 → 失败(有人新增了类型错误,或 mypy 升级放大了问题)
  当前错误数 < 基线 → 通过,并提示可以把基线调低(棘轮只能往一个方向转)

用法
----
    python tools/mypy_ratchet.py            # CI:校验不新增
    python tools/mypy_ratchet.py --update   # 本地:错误数下降后刷新基线

`--update` 只在错误数 <= 基线时才写文件,防止把新增债务"洗"进基线。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "mypy_baseline.txt"
SUMMARY = re.compile(r"Found (\d+) errors? in (\d+) files?")
DETAIL = re.compile(r"^(?P<file>[^:\n]+?\.py):\d+: error: .*\[(?P<code>[a-z-]+)\]$")
TOP_N = 30
SAMPLES: dict[str, list[str]] = {}

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


def read_baseline() -> int:
    if not BASELINE.exists():
        print(f"✗ 缺少基线文件 {BASELINE.relative_to(ROOT)}(内容应为错误数,如 673)")
        sys.exit(2)
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return int(line)
    print(f"✗ 基线文件 {BASELINE.relative_to(ROOT)} 里没有数字")
    sys.exit(2)


def run_mypy() -> tuple[int, int, dict[str, int], dict[str, int]]:
    """返回 (错误数, 文件数, 按文件计数, 按错误码计数)。

    配置取自 pyproject [tool.mypy],此处不重复传参。明细统计是为了让 CI 失败时能直接
    看出「多出来的错误在哪」——此前门禁只打印总数,CI 报 486 而本地 408 时无从下手。
    """
    r = subprocess.run(
        [sys.executable, "-m", "mypy", "foxclaw"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    by_file: dict[str, int] = {}
    by_code: dict[str, int] = {}
    for line in out.splitlines():
        d = DETAIL.match(line.strip())
        if d:
            f = d.group("file").replace("\\", "/")
            by_file[f] = by_file.get(f, 0) + 1
            by_code[d.group("code")] = by_code.get(d.group("code"), 0) + 1
            SAMPLES.setdefault(f, []).append(line.strip().split("error: ", 1)[-1])
    m = SUMMARY.search(out)
    if m:
        return int(m.group(1)), int(m.group(2)), by_file, by_code
    if "Success: no issues found" in out:
        return 0, 0, {}, {}
    print(out[-4000:])
    print("✗ 无法解析 mypy 输出(格式变了?上面是原始输出)")
    sys.exit(2)


def report(by_file: dict[str, int], by_code: dict[str, int]) -> None:
    """失败时打印分布 + 样本消息,便于对照本地与 CI 的差异。"""
    if by_code:
        print("  按错误码:")
        for code, n in sorted(by_code.items(), key=lambda kv: -kv[1])[:TOP_N]:
            print(f"    {n:4d}  {code}")
    if by_file:
        print(f"  按文件(top {TOP_N}):")
        for f, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:TOP_N]:
            print(f"    {n:4d}  {f}")
    if SAMPLES:
        top_files = sorted(by_file.items(), key=lambda kv: -kv[1])[:5]
        print("  错误样本(按错误最多的文件取前 4 条):")
        for f, _ in top_files:
            for s in SAMPLES.get(f, [])[:4]:
                print(f"    {f}: {s}")


def toolchain() -> str:
    """打出 mypy 与解释器版本:本地/CI 计数不一致时,版本漂移是第一嫌疑。"""
    try:
        from importlib.metadata import version
        mypy_ver = version("mypy")
    except Exception:
        mypy_ver = "?"
    return f"mypy {mypy_ver} on Python {sys.version.split()[0]}"


def main() -> int:
    baseline = read_baseline()
    errors, files, by_file, by_code = run_mypy()
    update = "--update" in sys.argv[1:]

    print(f"mypy: {errors} errors in {files} files | baseline: {baseline}")
    print(f"  toolchain: {toolchain()}")

    if update:
        if errors > baseline:
            print(f"✗ 拒绝更新基线:{errors} > {baseline}。棘轮只允许下调,"
                  f"请先修掉新增的 {errors - baseline} 个错误")
            report(by_file, by_code)
            return 1
        BASELINE.write_text(f"{errors}\n", encoding="utf-8")
        print(f"✓ 基线已刷新为 {errors}")
        return 0

    if errors > baseline:
        print(f"✗ 类型错误增加 {errors - baseline} 个({baseline} → {errors})。"
              f"新增错误必须修掉,不能靠调高基线通过")
        report(by_file, by_code)
        return 1
    if errors < baseline:
        print(f"✓ 通过,且比基线少 {baseline - errors} 个 —— "
              f"可信后执行 `python tools/mypy_ratchet.py --update` 锁定战果")
        return 0
    print("✓ 通过(与基线持平)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
