"""`foxclaw skills install|list|remove` 的实现。

为什么不放在 cli.py:那里只该有参数解析与分发(体积门 15KB,且它已承载 serve/config)。
装技能的真正复杂度在「服务在跑没跑」这一个分叉上:

* **在跑** → 必须打 /api/ahs/md-skills。直接改 markdown_skills.json 会被运行中的
  注册表下一次 `save()` 用内存快照整份覆盖,用户的安装无声消失(极难查)。
* **没跑** → 直接写同一个 JSON,格式与 MarkdownStore 完全一致,下次启动即加载。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast


def _data_dir() -> Path:
    """与 `foxclaw serve` 同源:FOXCLAW_DATA_DIR,否则 cwd 下的 foxclaw_data。"""
    raw = os.environ.get("FOXCLAW_DATA_DIR", "").strip()
    return Path(raw or "./foxclaw_data").resolve()


def _api_base() -> str:
    """本地服务地址:FOXCLAW_API 可覆盖,否则回环 + FOXCLAW_PORT。"""
    raw = os.environ.get("FOXCLAW_API", "").strip()
    if raw:
        return raw.rstrip("/")
    port = os.environ.get("FOXCLAW_PORT", "").strip() or "8000"
    return f"http://127.0.0.1:{port}"


def _api_alive(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/system/info", timeout=0.8) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _api_json(base: str, method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("FOXCLAW_API_KEY", "").strip()
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(f"{base}{path}", data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read(400).decode("utf-8", "replace").strip()
        raise RuntimeError(f"{method} {path} → {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上服务: {e.reason}") from None


def _confirm() -> bool:
    """非 TTY(管道/CI)一律不认,必须显式 --yes。"""
    if not sys.stdin.isatty():
        return False
    try:
        ans = input("安装后它会注入你 AI 的提示词、影响其行为。继续?[y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return ans.strip().lower() in ("y", "yes")


def _persist(skill, text: str) -> tuple[bool, str, bool]:
    """落地一个指令技能。返回 (是否新建, 位置说明, 是否已热加载)。"""
    base = _api_base()
    if _api_alive(base):
        got = _api_json(base, "POST", "/api/ahs/md-skills",
                        {"content": text, "id": skill.id, "acknowledged": True})
        return bool(got.get("created")), base, True
    from .markdown_skill import MarkdownSkillRegistry
    from .markdown_store import MarkdownStore

    reg = MarkdownSkillRegistry()
    store = MarkdownStore(_data_dir())
    store.load(reg)
    created = reg.get(skill.id) is None
    reg.register(skill)
    store.save(reg)
    return created, str(_data_dir() / "markdown_skills.json"), False


def install(args) -> int:
    from .fetch import MalwareBlocked, PackageError, fetch
    from .markdown_skill import parse_skill_md

    try:
        pkg = fetch(args.ref)
    except MalwareBlocked as e:
        print(f"拒绝安装:{e}", file=sys.stderr)
        return 3
    except PackageError as e:
        print(f"获取失败:{e}", file=sys.stderr)
        return 2
    want_id = (args.id or "").strip() or str(pkg.meta.get("slug") or "")
    try:
        skill = parse_skill_md(pkg.skill_md, id=want_id or None)
    except ValueError as e:
        print(f"SKILL.md 解析失败:{e}", file=sys.stderr)
        return 2
    if pkg.version and not skill.version:
        skill.version = pkg.version
    if not args.json:
        ver = f" v{skill.version}" if skill.version else ""
        print(f"来源   {pkg.source} · {pkg.origin}")
        print(f"技能   {skill.name}(id={skill.id}){ver}")
        print(f"描述   {skill.description[:100]}")
        if pkg.has_code:
            print("注意   包内带可执行脚本。foxclaw 只注入 SKILL.md、不运行这些脚本;"
                  "若正文要求 agent 去跑它们,该技能不会按预期工作。")
        if pkg.suspicious:
            print("警告   ClawHub 标记该技能可疑,请先读正文再决定。")
    if not args.yes and not _confirm():
        print("已取消:未确认。确认后加 --yes(或在本机终端里交互确认)。")
        return 1
    try:
        created, where, hot = _persist(skill, pkg.skill_md)
    except RuntimeError as e:
        print(f"写入失败:{e}", file=sys.stderr)
        return 4
    if args.json:
        print(json.dumps({"ok": True, "created": created, "skill": skill.to_meta(),
                          "source": pkg.source, "origin": pkg.origin,
                          "has_code": pkg.has_code, "where": where,
                          "hot_loaded": hot}, ensure_ascii=False))
        return 0
    print(f"{'已安装' if created else '已更新'}「{skill.name}」→ {where}")
    if not hot:
        print("提示:服务若正在运行,重启一次才会加载;不想重启就先起服务再装。")
    return 0


def list_skills(args) -> int:
    base = _api_base()
    hot = _api_alive(base)
    try:
        if hot:
            rows = _api_json(base, "GET", "/api/ahs/md-skills").get("skills") or []
        else:
            from .markdown_skill import MarkdownSkillRegistry
            from .markdown_store import MarkdownStore

            reg = MarkdownSkillRegistry()
            MarkdownStore(_data_dir()).load(reg)
            rows = reg.catalog()
    except RuntimeError as e:
        print(f"读取失败:{e}", file=sys.stderr)
        return 4
    if args.json:
        print(json.dumps({"source": "api" if hot else "file", "skills": rows},
                         ensure_ascii=False))
        return 0
    if not rows:
        print("还没有安装指令技能。`foxclaw skills install @owner/slug` 装一个。")
        return 0
    for s in rows:
        ver = f" v{s.get('version')}" if s.get("version") else ""
        off = "" if s.get("enabled", True) else " [已停用]"
        blocked = " [被封杀]" if s.get("blocked") else ""
        print(f"{str(s.get('id', '')).ljust(26)} {str(s.get('source', '?')):8}{ver}{off}{blocked}")
        print(f"  {str(s.get('description', ''))[:90]}")
    return 0


def remove(args) -> int:
    sid = (args.id or "").strip()
    if not sid:
        print("要卸载哪个?`foxclaw skills list` 看 id。", file=sys.stderr)
        return 2
    base = _api_base()
    if _api_alive(base):
        try:
            _api_json(base, "DELETE", f"/api/ahs/md-skills/{urllib.parse.quote(sid, safe='')}")
        except RuntimeError as e:
            print(f"卸载失败:{e}", file=sys.stderr)
            return 4
        print(f"已卸载 {sid}(服务已热更新)")
        return 0
    from .markdown_skill import MarkdownSkillRegistry
    from .markdown_store import MarkdownStore

    reg = MarkdownSkillRegistry()
    store = MarkdownStore(_data_dir())
    store.load(reg)
    if not reg.unregister(sid):
        print(f"没找到 id={sid} 的指令技能(`foxclaw skills list` 看现有)", file=sys.stderr)
        return 1
    store.save(reg)
    print(f"已卸载 {sid} → {_data_dir() / 'markdown_skills.json'}")
    return 0


def run(args) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            cast(Any, stream).reconfigure(errors="replace")
        except Exception:
            pass
    action = getattr(args, "skills_action", None)
    if action == "install":
        return install(args)
    if action == "list":
        return list_skills(args)
    if action == "remove":
        return remove(args)
    print("用法:`foxclaw skills install <ref>` / `list` / `remove <id>`")
    return 2
