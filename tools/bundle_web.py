"""把仓库根的 frontend/ 复制进包内 foxclaw/web/,让 wheel/sdist 带上 Web UI。

为什么要复制
------------
`frontend/` 在仓库根、不属于任何 Python 包,`packages.find` 只收
`foxclaw*` / `hmr*` / `hkc_*`,所以它一直进不了 wheel:`pip install` 之后
`foxclaw serve` 首页 404,只有 API/CLI。这里在构建前把同一份产物搬进
`foxclaw/web/`,再由 pyproject 的 `[tool.setuptools.package-data]` 带进 wheel。
产物已 gitignore,不入版本库(源头仍是 `frontend/`)。

用法
----
  python tools/bundle_web.py          # 复制(幂等:先清空 foxclaw/web)
  python tools/bundle_web.py --check  # 只校验是否缺失/过期,不复制
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend"
DST = ROOT / "foxclaw" / "web"

# 与 npm/build.cjs 的跳过规则对齐:依赖、缓存、日志不进包
SKIP_DIRS = {"node_modules", ".vite", "__pycache__", ".git"}
SKIP_SUFFIX = {".log", ".err", ".map"}

INIT = '''"""Web UI 静态资源 —— 构建期由 tools/bundle_web.py 从仓库根 frontend/ 复制。

不要手改本目录:改动落在 frontend/(或改 frontend_src/ 后重新构建),再跑
`python tools/bundle_web.py`。本目录已 gitignore,不在版本库里。
"""
'''


def iter_files(root: Path):
    """按相对路径列出要复制的文件(已排除依赖/缓存/日志)。"""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix in SKIP_SUFFIX:
            continue
        yield rel


def bundle() -> int:
    if not SRC.is_dir():
        print(f"[bundle_web] 找不到源目录 {SRC}", file=sys.stderr)
        return 1
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    (DST / "__init__.py").write_text(INIT, encoding="utf-8")
    count = 0
    total = 0
    for rel in iter_files(SRC):
        target = DST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / rel, target)
        count += 1
        total += target.stat().st_size
    print(f"[bundle_web] {count} files / {total / 1024:.0f} KB -> foxclaw/web/")
    if not (DST / "v3" / "index.html").is_file():
        print("[bundle_web] 提示:没有 v3/index.html,首页会退回旧 deck_v2",
              file=sys.stderr)
    return 0


def check() -> int:
    if not DST.is_dir():
        print("[bundle_web] foxclaw/web/ 不存在,构建前先跑 python tools/bundle_web.py",
              file=sys.stderr)
        return 1
    missing = [str(r) for r in iter_files(SRC) if not (DST / r).is_file()]
    if missing:
        print(f"[bundle_web] foxclaw/web/ 已过期({len(missing)} 个文件缺失,"
              f"如 {missing[0]}),先跑 python tools/bundle_web.py", file=sys.stderr)
        return 1
    print("[bundle_web] foxclaw/web/ 与 frontend/ 一致")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把 frontend/ 复制进包内 foxclaw/web/")
    ap.add_argument("--check", action="store_true", help="只校验,不复制")
    args = ap.parse_args(argv)
    return check() if args.check else bundle()


if __name__ == "__main__":
    raise SystemExit(main())
