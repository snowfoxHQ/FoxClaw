#!/usr/bin/env python3
"""Foxclaw 打包脚本 —— 用 `git archive` 生成交付归档。

为什么必须走 git archive
------------------------
直接对工作目录 zip 会**绕过 .gitignore**:只要有人手动压包,写在忽略规则里的
`foxclaw_data/`(真实会话数据)与 `docs/internal/`(内部机密文档)就会被一起打进去。
本项目此前正是这样发生的。`git archive` 只导出「已跟踪且未被忽略」的文件,
从流程上杜绝这类泄漏,同时保证归档内容与某个确定的 commit 一一对应、可复现。

产出两种归档(默认都生成)
------------------------
  dist/Foxclaw-internal-<版本>-<日期>.zip   完整版,含 docs/internal/,仅限内部/私有分发
  dist/Foxclaw-public-<版本>-<日期>.zip     对外分发版,排除 docs/internal/

两种归档生成后都会做一次**内容校验**:若发现密钥/运行数据/归档嵌套等不该出现的内容,
立即删除该归档并以非零码退出(宁可不出包,也不出错包)。

用法
----
  python tools/pack.py                  # internal + public 都生成
  python tools/pack.py --only public    # 只生成对外版
  python tools/pack.py --only internal  # 只生成内部版
  python tools/pack.py --ref HEAD~1     # 打包指定 commit(默认 HEAD)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# docs/internal/ 只在「内部版」允许出现
_INTERNAL_DOCS = "docs/internal/"


def read_version() -> str:
    """从 foxclaw/__init__.py 读 __version__(单一来源,避免版本号漂移)。"""
    txt = (ROOT / "foxclaw" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', txt)
    return m.group(1) if m else "0.0.0"


def _norm(name: str) -> str:
    return name.replace("\\", "/")


def _forbidden(path: str, allow_internal_docs: bool) -> str | None:
    """返回该条目被禁止的原因;允许则返回 None。"""
    p = _norm(path)
    if p.endswith("/"):
        return None
    parts = p.split("/")
    # 归档带 --prefix=Foxclaw-xxx/,故按「任意层级」匹配
    joined = "/".join(parts[1:]) if len(parts) > 1 else p

    if not allow_internal_docs and _INTERNAL_DOCS in p:
        return "内部机密文档 docs/internal/(对外分发版必须排除)"
    # .env / .env.bak-* / .env.local —— 但 .env.example 模板必须保留
    base = parts[-1]
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return "密钥配置文件"
    if base.startswith(".env.bak"):
        return "密钥备份文件"
    if "foxclaw_data/" in p or joined.startswith("foxclaw_data/"):
        return "运行时数据(含真实会话/记忆)"
    if re.search(r"\.(zip|rar|7z)$", base, re.I):
        return "嵌套归档(打包产物不应入库/入包)"
    if ".venv/" in p or "venv/" in p:
        return "虚拟环境"
    if "__pycache__/" in p or ".pytest_cache/" in p:
        return "缓存目录"
    if "新建文件夹/" in p:
        return "重复副本目录"
    if ".bak" in base:
        return "备份文件"
    return None


def check_archive(zf: Path, allow_internal_docs: bool) -> list[tuple[str, str]]:
    """校验归档内容,返回 [(条目, 原因)] 违规清单。"""
    bad: list[tuple[str, str]] = []
    with zipfile.ZipFile(zf) as z:
        for name in z.namelist():
            why = _forbidden(name, allow_internal_docs)
            if why:
                bad.append((name, why))
    return bad


def git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def ensure_repo() -> None:
    if git(["rev-parse", "--is-inside-work-tree"]).returncode != 0:
        sys.exit(f"[pack] 错误:{ROOT} 不是 git 仓库。本脚本依赖 git archive,请先 git init。")


def warn_if_dirty() -> None:
    r = git(["status", "--porcelain"])
    if r.stdout.strip():
        print("[pack] ⚠ 工作区有未提交改动 —— 只会打包已提交的内容(HEAD)。"
              "如需包含最新改动,请先 commit。")


def build(variant: str, ref: str) -> Path | None:
    version = read_version()
    stamp = _dt.date.today().strftime("%Y%m%d")
    prefix = f"Foxclaw-{variant}/"
    out = DIST / f"Foxclaw-{variant}-{version}-{stamp}.zip"

    cmd = ["archive", "--format=zip", f"--prefix={prefix}", "-o", str(out), ref]
    if variant == "public":
        # 用 git pathspec magic 排除内部文档目录
        cmd += ["--", ".", f":(exclude){_INTERNAL_DOCS.rstrip('/')}"]

    r = git(cmd)
    if r.returncode != 0:
        print(f"[pack] ✗ git archive 失败:\n{r.stderr.strip()}")
        out.unlink(missing_ok=True)
        return None

    # 内容校验 —— 宁可不出包,也不出错包
    bad = check_archive(out, allow_internal_docs=(variant == "internal"))
    if bad:
        out.unlink(missing_ok=True)
        print(f"[pack] ✗ {variant} 版校验未通过,已删除归档。违规条目:")
        for name, why in bad[:20]:
            print(f"        - {name}  ({why})")
        if len(bad) > 20:
            print(f"        ... 另有 {len(bad) - 20} 条")
        return None

    with zipfile.ZipFile(out) as z:
        n = len([x for x in z.namelist() if not x.endswith("/")])
    size = out.stat().st_size / 1024 / 1024
    print(f"[pack] ✓ {variant:<8} {out.relative_to(ROOT)}  ({n} 文件 / {size:.2f} MB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="用 git archive 生成 Foxclaw 交付归档")
    ap.add_argument("--only", choices=["public", "internal"], help="只生成指定版本")
    ap.add_argument("--ref", default="HEAD", help="要打包的 git 引用(默认 HEAD)")
    args = ap.parse_args()

    ensure_repo()
    DIST.mkdir(exist_ok=True)
    warn_if_dirty()

    variants = [args.only] if args.only else ["internal", "public"]
    ok = True
    for v in variants:
        if build(v, args.ref) is None:
            ok = False

    if not ok:
        print("[pack] 有归档未通过校验,请检查上面的违规清单。")
        return 1
    print(f"[pack] 完成。产物目录:{DIST.relative_to(ROOT)}/")
    if "internal" in variants:
        print("[pack] 提醒:internal 版含 docs/internal/,请勿对外分发。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
