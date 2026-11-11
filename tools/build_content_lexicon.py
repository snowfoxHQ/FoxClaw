#!/usr/bin/env python
"""把外部敏感词库(如 houbb/sensitive-word)转成 ContentGuard 可挂载的词表格式。

为什么要转换而不是直接挂
──────────────────────
`ContentGuard` 载入时会对每个词做一次 `compact()`(去空白标点、全半角归一、小写),
但那只保证「扫描时能对上」,不保证词表本身干净:同一批词里 `敏感词`、`敏 感 词`、
`ＳＥＮＳＩＴＩＶＥ` 会各占一条,白白撑大自动机。转换期先统一形态,词表体积和重复率
都能压下来。

输出格式就是 `FOXCLAW_CONTENT_LEXICON` 认的那一种:一行一条 `词<TAB>类别`,`#` 开头为
注释。类别会原样出现在命中结果里,便于审计归类。

词库形态
────────
两种都认,不用改文件:
  * 单文件纯词列表(houbb 主词库):文件名不带中文时用 `--category`(默认 platform)。
  * 一个文件一个类别(`政治类.txt` / `色情类.txt` / ...):中文文件名直接当类别名。

哪些类别挂上去由你决定。脚本默认照单全收 —— 排除政治 / 色情 / 暴力这些大类是部署决策,
不该由转换工具替你拍板。用 `--exclude` / `--include`(支持 `通配*`)自己取舍。

用法
────
    # 主词库,单一类别
    python tools/build_content_lexicon.py ../houbb/sensitive_word_dict.txt

    # 分类词库,只留自己需要的
    python tools/build_content_lexicon.py dict/*.txt --exclude 政治类,色情类 --exclude "暴力*"

    # 指定类别名与输出路径,转换后自动验证能否载入
    python tools/build_content_lexicon.py dict/垃圾广告类.txt --category ads -o ads.txt

    # 挂上去用
    # FOXCLAW_CONTENT_LEXICON=content_lexicon.txt

挂上去之前先跑几天 audit
────────────────────────
六万条量级的词表里必然有大量双字词,直接挂 enforce 会明显影响正常对话。建议先
置 FOXCLAW_CONTENT_GUARD_MODE=audit 观察一段时间的命中记录,确认误伤可接受再转
enforce —— 这层是兜底,不是让产品变得不好用。
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import sys
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_guard_module():
    """按文件路径载入 core/content_guard.py —— 它只依赖标准库,不必触发包的 __init__。"""
    path = ROOT / "foxclaw" / "core" / "content_guard.py"
    spec = importlib.util.spec_from_file_location("_foxclaw_content_guard", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"找不到 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_foxclaw_content_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_text(path: Path, encoding: str) -> str:
    data = path.read_bytes()
    for enc in ([encoding] if encoding != "auto" else ["utf-8-sig", "gb18030"]):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"{path}: 无法解码,用 --encoding 指定(试过 utf-8-sig, gb18030)")


def _category_of(path: Path, fallback: str) -> str:
    stem = path.stem.strip()
    if any("\u4e00" <= ch <= "\u9fff" for ch in stem):
        return _clean_category(stem)
    return _clean_category(fallback)


def _clean_category(name: str) -> str:
    # TAB 是词与类别的分隔符,不能出现在类别里;顺手压掉换行等控制字符。
    s = " ".join(str(name or "").split())
    return s.replace("\t", "/") or "platform"


def _match_any(cat: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(cat, p) for p in patterns)


def _collect(paths: list[Path], args) -> tuple[OrderedDict, Counter]:
    """返回 (词 -> [类别...], 统计)。词已 compact,类别按出现顺序去重。"""
    words: OrderedDict[str, list[str]] = OrderedDict()
    stats = Counter()
    for p in paths:
        text = _read_text(p, args.encoding)
        default_cat = _category_of(p, args.category)
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            stats["lines"] += 1
            word, _, cat = s.partition("\t")
            cat = _clean_category(cat) if cat.strip() else default_cat
            if args.include and not _match_any(cat, args.include):
                stats["skipped_by_include"] += 1
                continue
            if args.exclude and _match_any(cat, args.exclude):
                stats["skipped_by_exclude"] += 1
                continue
            word = args.compact(word)
            if len(word) < args.min_len:
                stats["skipped_too_short"] += 1
                continue
            if args.max_len and len(word) > args.max_len:
                stats["skipped_too_long"] += 1
                continue
            cats = words.setdefault(word, [])
            if cat not in cats:
                if cats:
                    stats["merged"] += 1
                cats.append(cat)
            else:
                stats["duplicated"] += 1
    return words, stats


def _write_lexicon(path: Path, words: OrderedDict, sources: list[Path], stats: Counter) -> None:
    counts = Counter(c for cats in words.values() for c in cats)
    header = [
        "# Foxclaw 内容词表 —— 由 tools/build_content_lexicon.py 生成,勿手改",
        "# 源: " + ", ".join(p.name for p in sources),
        "# 条数: %d" % len(words),
        "# 类别: " + ", ".join(f"{c} {n}" for c, n in counts.most_common()),
        "",
    ]
    lines = sorted((f"{w}\t{'/'.join(cats)}" for w, cats in words.items()), key=str.casefold)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header + lines) + "\n", encoding="utf-8", newline="\n")


def _verify(path: Path, guard_mod, sample: int) -> bool:
    guard = guard_mod.ContentGuard(mode="enforce", lexicon_paths=[str(path)])
    if not guard.lexicon_size:
        return False
    probe = [ln.split("\t")[0] for ln in path.read_text(encoding="utf-8").splitlines()
             if ln and not ln.startswith("#")][:max(1, sample)]
    hits = sum(1 for w in probe if guard.scan(w))
    print(f"验证: ContentGuard 载入 {guard.lexicon_size} 条, 抽样 {hits}/{len(probe)} 命中")
    return hits == len(probe)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把外部敏感词库转成 FOXCLAW_CONTENT_LEXICON 可用的词表格式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="类别筛选支持通配符,例如 --exclude '政治*'。",
    )
    ap.add_argument("inputs", nargs="+", type=Path, help="词库文件,可多个")
    ap.add_argument("-o", "--output", type=Path, default=Path("content_lexicon.txt"),
                    help="输出词表路径(默认 content_lexicon.txt)")
    ap.add_argument("--category", default="platform",
                    help="文件名不带中文时使用的类别名(默认 platform)")
    ap.add_argument("--include", action="append", default=[],
                    help="只导出匹配的类别,逗号分隔,可重复,支持通配")
    ap.add_argument("--exclude", action="append", default=[],
                    help="排除匹配的类别,逗号分隔,可重复,支持通配")
    ap.add_argument("--encoding", default="auto", help="auto / utf-8 / gb18030 等,默认 auto")
    ap.add_argument("--min-len", type=int, default=2,
                    help="短于该长度的词丢弃(默认 2,与加载器一致)")
    ap.add_argument("--max-len", type=int, default=0,
                    help="长于该长度的词丢弃(默认 0 = 不限)")
    ap.add_argument("--sample", type=int, default=5, help="转换后抽样验证条数(默认 5)")
    ap.add_argument("--no-verify", action="store_true", help="跳过载入验证")
    args = ap.parse_args(argv)

    def _split(values: list[str]) -> list[str]:
        out: list[str] = []
        for v in values:
            out.extend(x.strip() for x in v.split(",") if x.strip())
        return out

    args.include = _split(args.include)
    args.exclude = _split(args.exclude)

    missing = [str(p) for p in args.inputs if not p.is_file()]
    if missing:
        raise SystemExit("找不到词库文件: " + ", ".join(missing))

    guard_mod = _load_guard_module()
    args.compact = guard_mod.compact

    words, stats = _collect(args.inputs, args)
    if not words:
        raise SystemExit("没有可导出的词 —— 检查类别筛选或 --min-len")

    _write_lexicon(args.output, words, args.inputs, stats)

    counts = Counter(c for cats in words.values() for c in cats)
    print(f"读取 {len(args.inputs)} 个文件, {stats['lines']} 行")
    print("过滤: 类别排除 %d / 不在 include %d / 过短 %d / 过长 %d / 重复 %d / 跨类别归并 %d"
          % (stats["skipped_by_exclude"], stats["skipped_by_include"],
             stats["skipped_too_short"], stats["skipped_too_long"],
             stats["duplicated"], stats["merged"]))
    print(f"输出 {len(words)} 条 → {args.output}")
    print("类别: " + ", ".join(f"{c} {n}" for c, n in counts.most_common()))

    if not args.no_verify and not _verify(args.output, guard_mod, args.sample):
        print("警告: 载入验证未通过,词表可能不可用", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
