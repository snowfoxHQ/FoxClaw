"""剥离代码里的解释性注释。

用法:
    python tools/strip_comments.py --dry          只统计,不改文件
    python tools/strip_comments.py                真正改写
    python tools/strip_comments.py a.py b.js      只处理指定文件

不能一刀切:下面这些注释被删会改变 lint / 类型检查 / 解释器行为,必须原样保留。
    #!...                shebang
    # -*- coding -*-     编码声明
    # noqa ...           ruff 抑制
    # type: ...          mypy 注解(含 `x = []  # type: list[int]`)
    # pragma / fmt: / isort: / pyright: / pylint: / nosec / coverage:
docstring 不动 —— 它是运行时对象,不是注释。

vendor/ 是内嵌第三方,不改;frontend/v3 是构建产物,由 vite 重新生成。
"""

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv", "vendor",
              "foxclaw_data", "test_data", ".pytest_cache", ".ruff_cache", ".mypy_cache",
              "build", "dist", "htmlcov"}
# 生成副本:npm/python 由 npm/build.cjs 从仓库根复制,frontend/v3 由 vite 构建
SKIP_PREFIX = {"frontend/v3/", "npm/python/"}
EXTS = {".py", ".js", ".vue", ".ps1"}

DIRECTIVE = re.compile(
    r"^#\s*(?:!|-\*-|noqa|type:|pragma|fmt:|isort:|pyright:|pylint:|ruff:|nosec|"
    r"coverage:|mypy:|noinspection|coding[:=])",
    re.I,
)

MAX_BLANK = 2


def targets() -> list[Path]:
    out = []
    for p in ROOT.rglob("*"):
        if p.suffix not in EXTS or not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if SKIP_PARTS & set(p.relative_to(ROOT).parts):
            continue
        if any(rel.startswith(x) for x in SKIP_PREFIX):
            continue
        if rel == "tools/strip_comments.py":
            continue
        out.append(p)
    return sorted(out)


def collapse_blank(lines: list[str], limit: int = MAX_BLANK) -> list[str]:
    out: list[str] = []
    run = 0
    for ln in lines:
        if ln.strip() == "":
            run += 1
            if run > limit:
                continue
        else:
            run = 0
        out.append(ln)
    return out


def strip_py(text: str) -> tuple[str, int]:
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text, 0

    lines = text.splitlines(keepends=True)
    drops: dict[int, int | None] = {}
    for t in toks:
        if t.type != tokenize.COMMENT:
            continue
        row, col = t.start
        if DIRECTIVE.match(t.string):
            continue
        line = lines[row - 1] if row - 1 < len(lines) else ""
        head = line[:col]
        drops[row] = None if head.strip() == "" else col

    if not drops:
        return text, 0

    out: list[str] = []
    for idx, line in enumerate(lines, 1):
        if idx not in drops:
            out.append(line)
            continue
        col = drops[idx]
        if col is None:
            continue
        body = line[:col].rstrip()
        eol = line[len(line.rstrip("\r\n")):]
        out.append(body + eol)
    out = collapse_blank(out)
    return "".join(out), len(drops)


def _tpl_open(s: str) -> bool:
    n = 0
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "`":
            n += 1
        i += 1
    return n % 2 == 1


def strip_js(text: str) -> tuple[str, int]:
    """只删独占整行的注释,行尾注释一律不动。

    按位置切不行:正则字面量里的 \/\/ 和字符串里的 // 都会被当成注释起点 ——
    markdown.js 的 /^https?:\/\//i 就被切掉半个函数。整行判定是安全的:
    行首出现 // 或 /* 时,它只能是注释。
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    removed = 0
    in_block = ""
    in_tpl = False
    for line in lines:
        body = line.rstrip("\r\n")
        s = body.strip()
        if in_block:
            removed += 1
            if (in_block == "<!--" and "-->" in s) or (in_block == "/*" and "*/" in s):
                in_block = ""
            continue
        if in_tpl:
            if _tpl_open(s):
                in_tpl = False
            out.append(line)
            continue
        if s.startswith("<!--"):
            removed += 1
            if "-->" not in s[4:]:
                in_block = "<!--"
            continue
        if s.startswith("/*"):
            removed += 1
            if "*/" not in s[2:]:
                in_block = "/*"
            continue
        if s.startswith("//"):
            removed += 1
            continue
        if _tpl_open(body):
            in_tpl = True
        out.append(line)
    return "".join(collapse_blank(out, 1)), removed


def strip_ps(text: str) -> tuple[str, int]:
    """PowerShell 的注释符是 # 和 <# #>。只删独占行的,行尾的不碰 —— 拆字符串不值当。"""
    out: list[str] = []
    removed = 0
    in_block = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if in_block:
            removed += 1
            if "#>" in stripped:
                in_block = False
            continue
        if stripped.startswith("<#"):
            removed += 1
            if "#>" not in stripped[2:]:
                in_block = True
            continue
        if stripped.startswith("#") and not re.match(r"#\s*requires\b", stripped, re.I):
            removed += 1
            continue
        out.append(line)
    return "".join(collapse_blank(out, 1)), removed


def main(argv: list[str]) -> int:
    dry = "--dry" in argv
    picked = [Path(a) for a in argv if not a.startswith("-")]
    files = picked or targets()
    total_files = 0
    total_lines = 0
    for p in files:
        if not p.is_absolute():
            p = ROOT / p
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        if p.suffix == ".py":
            new, n = strip_py(text)
        elif p.suffix == ".ps1":
            new, n = strip_ps(text)
        else:
            new, n = strip_js(text)
        if n == 0 or new == text:
            continue
        if p.suffix == ".py":
            try:
                compile(new, str(p), "exec")
            except SyntaxError as e:
                print(f"SKIP {p.relative_to(ROOT)}: 删后语法错误 {e}")
                continue
        total_files += 1
        total_lines += n
        if dry:
            print(f"{n:5d}  {p.relative_to(ROOT).as_posix()}")
        else:
            p.write_bytes(new.encode("utf-8"))
    print(f"\n{'[dry] ' if dry else ''}files={total_files} comment_lines={total_lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
