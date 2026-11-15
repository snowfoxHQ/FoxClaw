#!/usr/bin/env python3
"""发布前校验 —— 版本号四处一致 + 构建产物结构合规(本地可跑,CI 同一条命令)。

为什么要在按发布按钮**之前**回答
------------------------------
PyPI / npm 的版本号不可撤回:上传后只能 yank(仍占号、仍可被 pin 装到),不能覆盖
重发。所以「四处版本号是否一致」「打出来的包里有没有 .env / 内部文档 / 运行期真实
数据」必须发布前回答 —— 等 tag 推上去、CI 跑到一半才发现漏改一处版本,npm 上就已经
留了个自己都对不上的版本。

两侧产物性质不同(别把"发布成功"当"装完能用")
------------------------------------------
HKC / HMR 内嵌在 vendor/、运行期按 `Path(__file__)/../../vendor` 找;而 setuptools
只收 `foxclaw*`,故 **wheel/sdist 不含 vendor/**:装上是能 import、但知识/记忆
子系统降级为 None 的**占位包**(与 npm/index.js 占位同一性质)。本脚本对"缺 vendor"
只 warn 不 error —— 既定事实不是事故,但发布说明必须写明。

用法
----
  python tools/release_check.py                      # 只查四处版本一致
  python tools/release_check.py --tag v0.1.0         # 另查 tag 与版本一致
  python tools/release_check.py --dist dist --npm-tgz dist/foxclaw-0.1.0.tgz
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Issue:
    """一条结果。level=error 导致非零退出;warn 只提示。"""

    level: str
    code: str
    msg: str

    def __str__(self) -> str:
        mark = "ERROR" if self.level == "error" else " WARN"
        return f"  [{mark}] {self.code}: {self.msg}"


def _dunder_version(root: Path) -> str:
    txt = (root / "foxclaw" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', txt, re.M)
    return m.group(1) if m else ""


def _npm_index_version(root: Path) -> str:
    src = (root / "npm" / "index.js").read_text(encoding="utf-8")
    m = re.search(r'export\s+const\s+version\s*=\s*["\']([^"\']+)["\']', src)
    return m.group(1) if m else ""


def _project(root: Path) -> dict:
    with (root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]


def _npm_package(root: Path) -> dict:
    return json.loads((root / "npm" / "package.json").read_text(encoding="utf-8"))


def project_name(root: Path = ROOT) -> str:
    try:
        return _project(root).get("name", "")
    except Exception:
        return ""


def collect_versions(root: Path = ROOT) -> dict:
    """四处版本号;读不到留空串,交给 check_versions 报 unreadable。"""
    def safe(fn, *a):
        try:
            return fn(*a) or ""
        except Exception:
            return ""

    return {
        "pyproject.toml:project.version": safe(lambda: _project(root)["version"]),
        "foxclaw/__init__.py:__version__": safe(_dunder_version, root),
        "npm/package.json:version": safe(lambda: _npm_package(root)["version"]),
        "npm/index.js:export const version": safe(_npm_index_version, root),
    }


def check_versions(root: Path = ROOT) -> list:
    versions = collect_versions(root)
    issues = [Issue("error", "unreadable", f"{src} 里读不到版本号")
              for src, v in versions.items() if not v]
    vals = {k: v for k, v in versions.items() if v}
    if len(set(vals.values())) > 1:
        ref = vals.get("pyproject.toml:project.version") or next(iter(vals.values()))
        for src, v in vals.items():
            if v != ref:
                issues.append(Issue("error", "version_mismatch",
                                    f"{src} = {v},与 pyproject = {ref} 不一致(发布后不可改)"))
    return issues


def readme_target(root: Path = ROOT) -> str:
    """pyproject [project].readme 指向的正文文件(相对路径);未声明返回空串。"""
    try:
        field = _project(root).get("readme")
    except Exception:                                          # noqa: BLE001
        return ""
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        return str(field.get("file", "") or "")
    return ""


def check_metadata(root: Path = ROOT) -> list:
    """项目页正文必须存在。

    0.1.1 上传后 PyPI 项目页只有一句「该软件包的作者未提供项目描述」——pyproject
    没声明 `readme`,元数据里就没有 Description。包能装、能 import,所以这条缺陷
    在发布前的任何检查里都不会暴露,只能显式断言。
    """
    path = readme_target(root)
    if not path:
        return [Issue("error", "no_readme",
                      "pyproject [project] 未声明 readme → PyPI 项目页显示「未提供项目描述」")]
    f = root / path
    if not f.is_file():
        return [Issue("error", "readme_missing", f"readme 指向的 {path} 不存在")]
    body = f.read_text(encoding="utf-8", errors="replace").strip()
    if len(body) < 200:
        return [Issue("error", "readme_too_short",
                      f"{path} 只有 {len(body)} 字符,撑不起项目页描述")]
    return []


def check_tag(tag: str, version: str) -> list:
    """tag 必须就是本次要发的版本(v 前缀可有可无)。"""
    if not tag:
        return []
    stripped = tag.strip()
    if stripped[:1] in ("v", "V"):
        stripped = stripped[1:]
    if stripped != version:
        return [Issue("error", "tag_mismatch",
                      f"tag {tag!r} 指向 {stripped},但代码里是 {version}")]
    return []


def forbidden_entry(entry: str):
    """归档内条目是否绝对不该出现;返回原因,干净则 None。"""
    p = entry.replace("\\", "/")
    if p.endswith("/"):
        return None
    parts = [x for x in p.split("/") if x]
    if not parts:
        return None
    base = parts[-1]
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return "密钥配置文件(真实 .env)"
    if "docs/internal/" in p:
        return "内部机密文档 docs/internal/"
    if "foxclaw_data/" in p:
        return "运行期真实数据(会话/记忆)"
    if re.search(r"\.(zip|rar|7z)$", base, re.I):
        return "嵌套归档"
    if "__pycache__/" in p or ".pytest_cache/" in p:
        return "缓存目录"
    if "/.venv/" in p or p.startswith(".venv/") or "/venv/" in p:
        return "虚拟环境"
    if base.endswith(".bak"):
        return "备份文件"
    return None


def archive_entries(path: Path) -> list:
    """列归档内条目名(wheel 是 zip;sdist / npm tgz 是 tar)。"""
    try:
        if path.name.endswith((".whl", ".zip")):
            with zipfile.ZipFile(path) as z:
                return z.namelist()
        if path.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(path) as t:
                return t.getnames()
    except Exception:
        return []
    return []


def _scan_forbidden(path: Path) -> list:
    out = []
    for e in archive_entries(path):
        why = forbidden_entry(e)
        if why:
            out.append(Issue("error", "forbidden_entry", f"{path.name} 含 {e}  ({why})"))
    return out[:10]


def _wheel_metadata(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith(".dist-info/METADATA"):
                    txt = z.read(n).decode("utf-8", "replace")
                    return {k.strip(): v.strip() for k, v in
                            (ln.split(": ", 1) for ln in txt.splitlines() if ": " in ln)}
    except Exception:
        pass
    return {}


def _wheel_description(path: Path) -> str:
    """wheel METADATA 里的正文(PEP 566:头部空行之后即 long_description)。

    换行符必须按 CRLF 也认:Windows 上构建出来的 METADATA 用 \\r\\n,只按 \\n\\n
    切会把整份头部当正文(或反过来判成没有正文),两种都会把结论判反。
    """
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith(".dist-info/METADATA"):
                    txt = z.read(n).decode("utf-8", "replace").replace("\r\n", "\n")
                    parts = txt.split("\n\n", 1)
                    return parts[1] if len(parts) > 1 else ""
    except Exception:                                          # noqa: BLE001
        return ""
    return ""


def check_dist(dist: Path, name: str, version: str) -> list:
    """dist/ 里恰好一个 wheel + 一个 sdist,名字/元数据/内容都对得上。"""
    if not dist.is_dir():
        return [Issue("error", "no_dist", f"产物目录不存在:{dist}")]
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    issues: list = []
    if len(wheels) != 1:
        issues.append(Issue("error", "wheel_count",
                            f"{dist} 里 wheel 数 {len(wheels)}(应为 1,旧版产物先清掉)"))
    if len(sdists) != 1:
        issues.append(Issue("error", "sdist_count", f"{dist} 里 sdist 数 {len(sdists)}(应为 1)"))
    if not wheels or not sdists:
        return issues

    whl, sdist = wheels[0], sdists[0]
    if not whl.name.startswith(f"{name}-{version}-"):
        issues.append(Issue("error", "wheel_name", f"{whl.name} 与 {name}-{version} 不符"))
    if sdist.name != f"{name}-{version}.tar.gz":
        issues.append(Issue("error", "sdist_name", f"{sdist.name} 应为 {name}-{version}.tar.gz"))

    meta = _wheel_metadata(whl)
    if meta.get("Name") != name or meta.get("Version") != version:
        issues.append(Issue("error", "meta_mismatch",
                            f"wheel METADATA 是 {meta.get('Name')}@{meta.get('Version')},"
                            f"应为 {name}@{version}"))
    if len(_wheel_description(whl).strip()) < 200:
        issues.append(Issue("error", "no_long_description",
                            f"{whl.name} 的 METADATA 没有正文 → PyPI 项目页会是空白"))

    entries = archive_entries(whl)
    for need in ("foxclaw/__init__.py", "foxclaw/cli.py"):
        if need not in entries:
            issues.append(Issue("error", "missing_entry", f"{whl.name} 缺 {need}(入口会装丢)"))
    if not any(e.endswith("foxclaw/cli.py") for e in archive_entries(sdist)):
        issues.append(Issue("error", "missing_entry", f"{sdist.name} 里没有 foxclaw/cli.py"))

    issues += _scan_forbidden(whl) + _scan_forbidden(sdist)
    if not any("/vendor/" in e for e in entries):
        issues.append(Issue("warn", "no_vendor",
                            f"{whl.name} 不含 vendor/(hkc/hmr)→ 装上是占位包,"
                            f"知识/记忆子系统降级;发布说明需写明"))
    return issues


def check_npm_tgz(tgz: Path, name: str, version: str) -> list:
    """npm 包:`npm pack` 的文件名与 package/package.json 都得对。"""
    issues: list = []
    if tgz.name != f"{name}-{version}.tgz":
        issues.append(Issue("error", "npm_name", f"{tgz.name} 应为 {name}-{version}.tgz"))
    if "package/package.json" not in archive_entries(tgz):
        issues.append(Issue("error", "npm_no_pkg", f"{tgz.name} 里没有 package/package.json"))
        return issues
    try:
        with tarfile.open(tgz) as t:
            data = json.loads(t.extractfile("package/package.json").read())
        if data.get("name") != name or data.get("version") != version:
            issues.append(Issue("error", "npm_meta",
                                f"package.json 是 {data.get('name')}@{data.get('version')},"
                                f"应为 {name}@{version}"))
    except Exception as e:                                      # noqa: BLE001
        issues.append(Issue("error", "npm_meta", f"读 {tgz.name} 的 package.json 失败:{e}"))
    return issues + _scan_forbidden(tgz)


def run(root: Path = ROOT, tag: str = "", dist: Path | None = None,
        npm_tgz: Path | None = None) -> list:
    """跑全部适用检查,返回 issues(不打印、不退出,便于测试复用)。"""
    issues = check_versions(root)
    issues += check_metadata(root)
    name, version = project_name(root), collect_versions(root).get(
        "pyproject.toml:project.version", "")
    if tag:
        issues += check_tag(tag, version)
    if dist is not None:
        issues += check_dist(dist, name, version)
    if npm_tgz is not None:
        issues += check_npm_tgz(npm_tgz, name, version)
    return issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="发布前校验:版本一致 + 产物合规")
    ap.add_argument("--root", default=str(ROOT), help="仓库根目录")
    ap.add_argument("--tag", default="", help="本次要打的 git tag(如 v0.1.0)")
    ap.add_argument("--dist", default=None, help="构建产物目录(校验 wheel/sdist)")
    ap.add_argument("--npm-tgz", default=None, help="npm pack 产出的 .tgz")
    args = ap.parse_args(argv)

    issues = run(Path(args.root), args.tag,
                 Path(args.dist) if args.dist else None,
                 Path(args.npm_tgz) if args.npm_tgz else None)
    errors = [i for i in issues if i.level == "error"]
    warns = [i for i in issues if i.level != "error"]
    if issues:
        print("[release_check] 检查结果:")
        for i in issues:
            print(i)
    if errors:
        print(f"[release_check] FAILED: {len(errors)} 个错误 / {len(warns)} 个提示 —— 不许发布")
        return 1
    print(f"[release_check] PASS({len(warns)} 个提示)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
