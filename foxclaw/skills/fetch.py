"""技能来源解析与下载:ClawHub / GitHub / http(s) 包地址 / 本地路径。

`foxclaw skills install <ref>` 的统一入口。ref 形式:

    @owner/slug                    ClawHub 作用域包(与 `openclaw skills install` 同源)
    owner/slug                     同上,省略 @
    github:owner/repo[/sub][@ref]  GitHub 仓库里的 SKILL.md(默认 HEAD)
    https://...zip|tar.gz|.md      直接给包地址
    ./skill.zip | ./SKILL.md       本地包 / 文件 / 目录

安全口径:ClawHub 在**下载前**先查 moderation,`isMalwareBlocked` 直接拒绝;
`isSuspicious` 只作告警交给调用方确认。包内带可执行脚本时标 `has_code` ——
foxclaw 的 SKILL.md 是纯提示词,不执行包内代码,这点必须让调用方说清楚。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .archive import PackageError, has_code, read_package

DEFAULT_REGISTRY = "https://clawhub.ai"
MAX_BYTES = 10 * 1024 * 1024
UA = "foxclaw-skills/1 (+https://github.com/snowfoxHQ/FoxClaw)"
_ARCHIVE_EXT = (".zip", ".tar.gz", ".tgz", ".tar")
_LOCAL_ID = re.compile(r"^@?([A-Za-z0-9][\w.-]*)/([\w.-]+)$")


class HttpError(PackageError):
    """远端返回非 2xx。status 保留下来供调用方区分 404(不存在)与其他错误。"""

    def __init__(self, status: int, url: str, detail: str):
        self.status = status
        super().__init__(f"{url} 返回 {status}: {detail or '(无响应体)'}")


class MalwareBlocked(PackageError):
    """来源方已判定为恶意,任何 --yes 都不放行。"""


@dataclass
class SkillPackage:
    skill_md: str
    source: str
    origin: str
    files: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    version: str = ""
    moderation: dict = field(default_factory=dict)

    @property
    def has_code(self) -> bool:
        return has_code(self.files)

    @property
    def suspicious(self) -> bool:
        return bool(self.moderation.get("isSuspicious"))


def registry() -> str:
    """ClawHub 地址。内网镜像走 FOXCLAW_CLAWHUB_REGISTRY,别改代码。"""
    return (os.environ.get("FOXCLAW_CLAWHUB_REGISTRY", "").strip()
            or DEFAULT_REGISTRY).rstrip("/")


def _http_get(url: str, timeout: float = 25.0,
              max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """GET 一个 URL,返回 (bytes, content-type)。超限/4xx 都抛 PackageError。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].lower()
            chunks, total = [], 0
            while True:
                buf = resp.read(65536)
                if not buf:
                    break
                total += len(buf)
                if total > max_bytes:
                    raise PackageError(f"下载超过上限({max_bytes} 字节): {url}")
                chunks.append(buf)
            return b"".join(chunks), ctype
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode("utf-8", "replace").strip()
        raise HttpError(e.code, url, detail) from None
    except urllib.error.URLError as e:
        raise PackageError(f"下载失败 {url}: {e.reason}") from None


def _json_get(url: str, timeout: float = 25.0) -> dict:
    raw, _ = _http_get(url, timeout)
    try:
        got = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        raise PackageError(f"{url} 返回的不是 JSON") from None
    return got if isinstance(got, dict) else {}


def fetch(ref: str, timeout: float = 25.0) -> SkillPackage:
    """把一个 ref 解析成 SkillPackage(已下载并解出 SKILL.md)。"""
    r = (ref or "").strip()
    if not r:
        raise PackageError("缺少 skill 来源")
    low = r.lower()
    if low.startswith(("http://", "https://")):
        return _from_url(r, timeout)
    if low.startswith("github:"):
        return _from_github(r[len("github:"):], timeout)
    local = Path(os.path.expanduser(r))
    if local.exists():
        return _from_local(local)
    m = _LOCAL_ID.match(r)
    if m:
        return _from_clawhub(m.group(1), m.group(2), timeout)
    raise PackageError(
        "无法识别的来源。支持:@owner/slug(ClawHub)、github:owner/repo[/子路径][@ref]、"
        "http(s):// 包地址、本地 .zip/.tar.gz/SKILL.md/目录")


def _from_clawhub(owner: str, slug: str, timeout: float) -> SkillPackage:
    """ClawHub:先查详情(拿版本 + 审核状态),再下载 zip。

    先查详情而不是直接下载,是为了在落盘前拦住 `isMalwareBlocked` —— openclaw 自己
    也把 moderation 摆在安装前的核验位置,我们不能比它更松。
    """
    base = registry()
    q_slug = urllib.parse.quote(slug, safe="")
    q_owner = urllib.parse.quote(owner, safe="")
    try:
        detail = _json_get(f"{base}/api/v1/skills/{q_slug}?ownerHandle={q_owner}", timeout)
    except HttpError as e:
        if e.status == 404:
            raise PackageError(f"ClawHub 上找不到 @{owner}/{slug}") from None
        raise
    mod = detail.get("moderation") or {}
    if mod.get("isMalwareBlocked"):
        reason = mod.get("summary") or "来源方未给说明"
        raise MalwareBlocked(f"@{owner}/{slug} 已被 ClawHub 判为恶意并拦截:{reason}")
    version = str((detail.get("latestVersion") or {}).get("version") or "")
    data, ctype = _http_get(
        f"{base}/api/v1/download?slug={q_slug}&ownerHandle={q_owner}", timeout)
    if data[:1] == b"{" or "json" in ctype:
        try:
            handoff = json.loads(data.decode("utf-8", "replace")) or {}
        except Exception:
            handoff = {}
        archive = str(handoff.get("archiveUrl") or "")
        if not archive:
            raise PackageError(f"@{owner}/{slug} 无托管版本,且返回里没有可下载地址")
        data, _ = _http_get(archive, timeout)
    pkg = read_package(data)
    return SkillPackage(
        skill_md=pkg["skill_md"], source="clawhub",
        origin=f"@{owner}/{slug}" + (f"@{version}" if version else ""),
        files=pkg["files"], meta=pkg["meta"], version=version, moderation=mod)


def _from_github(spec: str, timeout: float) -> SkillPackage:
    """github:owner/repo[/子路径][@ref] → codeload 的 tar.gz(不占 GitHub API 配额)。"""
    body, _, ref = spec.partition("@")
    parts = [p for p in body.split("/") if p]
    if len(parts) < 2:
        raise PackageError("github: 形式应为 github:owner/repo[/子路径][@ref]")
    owner, repo = parts[0], parts[1]
    sub = "/".join(parts[2:])
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref or 'HEAD'}"
    data, _ = _http_get(url, timeout)
    pkg = read_package(data, subpath=sub)
    origin = f"github:{owner}/{repo}" + (f"/{sub}" if sub else "") + (f"@{ref}" if ref else "")
    return SkillPackage(skill_md=pkg["skill_md"], source="github", origin=origin,
                        files=pkg["files"], meta=pkg["meta"])


def _from_url(url: str, timeout: float) -> SkillPackage:
    """直链:归档当包解,markdown/纯文本当单文件 SKILL.md。"""
    data, ctype = _http_get(url, timeout)
    path = urllib.parse.urlparse(url).path.lower()
    name = path.rsplit("/", 1)[-1] or "SKILL.md"
    textish = path.endswith((".md", ".markdown")) or ctype in ("text/markdown", "text/plain")
    is_archive = data[:2] == b"PK" or data[:2] == b"\x1f\x8b" or path.endswith(_ARCHIVE_EXT)
    if textish and not is_archive:
        text = data.decode("utf-8", "replace")
        return SkillPackage(skill_md=text, source="url", origin=url, files=[name])
    pkg = read_package(data)
    return SkillPackage(skill_md=pkg["skill_md"], source="url", origin=url,
                        files=pkg["files"], meta=pkg["meta"])


def _from_local(path: Path) -> SkillPackage:
    """本地:目录(找 SKILL.md)、单 .md、或 .zip/.tar.gz 包。"""
    origin = str(path)
    if path.is_dir():
        direct = path / "SKILL.md"
        if direct.is_file():
            text = direct.read_text(encoding="utf-8", errors="replace")
            meta = _read_meta(path / "_meta.json")
        else:
            cands = sorted(path.glob("*/SKILL.md")) + sorted(path.glob("*/*/SKILL.md"))
            if len(cands) != 1:
                raise PackageError(
                    f"{path} 里没找到唯一的 SKILL.md(直接放 SKILL.md,或只有一个子目录带它)")
            text = cands[0].read_text(encoding="utf-8", errors="replace")
            meta = _read_meta(cands[0].parent / "_meta.json")
        files = [p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()]
        return SkillPackage(skill_md=text, source="local", origin=origin,
                            files=files, meta=meta)
    if path.suffix.lower() in (".md", ".markdown"):
        return SkillPackage(skill_md=path.read_text(encoding="utf-8", errors="replace"),
                            source="local", origin=origin, files=[path.name])
    pkg = read_package(path.read_bytes())
    return SkillPackage(skill_md=pkg["skill_md"], source="local", origin=origin,
                        files=pkg["files"], meta=pkg["meta"])


def _read_meta(p: Path) -> dict:
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}
