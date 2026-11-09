"""技能包归档读取:zip / tar.gz 统一「按名读」,不落盘、不 extractall。

`markdown_skill.extract_skill_package` 只认 zip,而 ClawHub 发 zip、GitHub 发 tar.gz。
两者共用同一套规则:拒路径穿越、限解压总量(防 zip bomb)、挑**最浅**的 SKILL.md
(GitHub 仓库里它常埋在 `skills/<name>/` 多级子目录下)。
"""
from __future__ import annotations

import io
import json
import tarfile
import zipfile

MAX_TOTAL = 8 * 1024 * 1024

_CODE_EXT = (".py", ".sh", ".bash", ".ps1", ".js", ".mjs", ".cjs", ".ts",
             ".rb", ".pl", ".bat", ".cmd", ".exe")


class PackageError(ValueError):
    """包不可用:格式坏 / 缺 SKILL.md / 超体积。"""


def safe_name(name: str) -> bool:
    """拒绝对路径、盘符与 `..` 段。按名读虽不落盘,但脏名字会流进日志与提示词。"""
    n = (name or "").replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    if not n or n.startswith("/") or ".." in n.split("/"):
        return False
    return not (len(n) > 1 and n[1] == ":")


def _shallowest(names: list[str], base: str, subpath: str = "") -> str | None:
    """挑最浅的同名文件(全小写比较)。subpath 非空时只在其下找。"""
    want = f"/{subpath.strip('/')}/" if subpath.strip("/") else ""
    cands = [
        n for n in names
        if safe_name(n)
        and n.rsplit("/", 1)[-1].lower() == base
        and (not want or want in f"/{n}")
    ]
    if not cands:
        return None
    return min(cands, key=lambda n: (n.count("/"), len(n)))


def pick_skill_md(names: list[str], subpath: str = "") -> str | None:
    """挑最浅的 SKILL.md:ClawHub 在根,GitHub 仓库常在 skills/<name>/ 下。"""
    return _shallowest(names, "skill.md", subpath)


def has_code(files: list[str]) -> bool:
    """包内是否带可执行脚本 / 独立的 scripts/ 目录。"""
    for n in files:
        low = n.lower()
        if low.endswith(_CODE_EXT) or "/scripts/" in f"/{low}":
            return True
    return False


def read_package(data: bytes, subpath: str = "", max_total: int = MAX_TOTAL) -> dict:
    """解一个技能包,返回 {files, skill_md, meta}。"""
    if not data:
        raise PackageError("空包")
    if data[:2] == b"PK":
        names, read = _open_zip(data, max_total)
    else:
        names, read = _open_tar(data, max_total)
    md = pick_skill_md(names, subpath)
    if md is None:
        where = f"(子路径 {subpath} 下)" if subpath else ""
        raise PackageError(f"包内未找到 SKILL.md{where}")
    meta: dict = {}
    meta_name = _shallowest(names, "_meta.json")
    if meta_name:
        try:
            meta = json.loads(read(meta_name).decode("utf-8", "replace")) or {}
        except Exception:
            meta = {}
    return {"files": names, "skill_md": read(md).decode("utf-8", "replace"), "meta": meta}


def _open_zip(data: bytes, max_total: int):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        raise PackageError(f"zip 解析失败: {e}") from None
    infos = [i for i in zf.infolist() if not i.is_dir()]
    total = sum(i.file_size for i in infos)
    if total > max_total:
        raise PackageError(f"解压体积超上限({total} > {max_total})")
    return [i.filename for i in infos], zf.read


def _open_tar(data: bytes, max_total: int):
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
        members = [m for m in tf.getmembers() if m.isfile()]
    except Exception as e:
        raise PackageError(f"既不是 zip 也不是 tar 包: {e}") from None
    total = sum(m.size for m in members)
    if total > max_total:
        raise PackageError(f"解压体积超上限({total} > {max_total})")
    by_name = {m.name: m for m in members}

    def read(name: str) -> bytes:
        fp = tf.extractfile(by_name[name])
        return fp.read() if fp else b""

    return [m.name for m in members], read
