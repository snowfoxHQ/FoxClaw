"""SKILL.md 指令技能:注入 Agent 提示词的 markdown 指令(非可调用工具)。无代码执行。"""
from __future__ import annotations
import re
import threading
from dataclasses import dataclass, field, asdict
import yaml
from typing import Any


@dataclass
class MarkdownSkill:
    id: str
    name: str
    description: str
    triggers: list = field(default_factory=list)
    body: str = ""
    enabled: bool = True
    source: str = "user"
    version: str = ""
    builtin: bool = False

    def to_dict(self) -> dict:
        d = asdict(self); d["trust"] = "low"; return d

    def to_meta(self) -> dict:
        d = self.to_dict(); d.pop("body", None); return d


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "skill"


def parse_skill_md(text: str, id: str | None = None) -> MarkdownSkill:
    """解析 SKILL.md(--- yaml frontmatter --- + 正文)。缺 name/description → ValueError。"""
    text = (text or "").lstrip("﻿").strip()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError("缺少 --- frontmatter ---")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as e:
        raise ValueError(f"frontmatter 非法 YAML: {e}")
    if not isinstance(fm, dict):
        raise ValueError("frontmatter 需为键值对")
    name = str(fm.get("name") or "").strip()
    desc = str(fm.get("description") or "").strip()
    if not name or not desc:
        raise ValueError("SKILL.md 必须含 name 和 description")
    trig = fm.get("triggers") or []
    if isinstance(trig, str):
        trig = [t.strip() for t in trig.split(",") if t.strip()]
    trig = [str(t) for t in trig]
    builtin = bool(fm.get("builtin", False))
    return MarkdownSkill(id=id or _slug(name), name=name, description=desc,
                         triggers=trig, body=m.group(2).strip(),
                         source="builtin" if builtin else "user",
                         version=str(fm.get("version") or "").strip(),
                         builtin=builtin)


def load_builtin_dir(reg: MarkdownSkillRegistry, root) -> list:
    """加载随包分发的指令技能:`<root>/<name>/SKILL.md` 中 **frontmatter 标了 builtin: true** 的。

    为什么只认 builtin 标记:仓库 `skills/` 下还有 `execution-contract` 之类的**开发者文档**,
    全量注入会把每轮 prompt 撑肿。显式标记 = 只把"真给模型执行的指令"注入。
    用户已安装的同 id 技能优先(不覆盖),内置只是缺省值。
    """
    from pathlib import Path
    loaded: list[Any] = []
    try:
        base = Path(root)
        if not base.is_dir():
            return loaded
        for p in sorted(base.glob("*/SKILL.md")):
            try:
                s = parse_skill_md(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not s.builtin or reg.get(s.id) is not None:
                continue
            reg.register(s)
            loaded.append(s.id)
    except Exception:
        pass
    return loaded


class MarkdownSkillRegistry:
    def __init__(self):
        self._skills = {}
        self._lock = threading.RLock()
        self.policy = None

    def register(self, s: MarkdownSkill):
        with self._lock:
            self._skills[s.id] = s

    def unregister(self, sid: str) -> bool:
        with self._lock:
            return self._skills.pop(sid, None) is not None

    def get(self, sid: str):
        with self._lock:
            return self._skills.get(sid)

    def all(self) -> list:
        with self._lock:
            return list(self._skills.values())

    def catalog(self) -> list:
        return [s.to_meta() for s in self.all()]

    def match(self, task: str) -> list:
        """有 triggers → 按 triggers(子串);无 triggers(原味 OpenClaw)→ 按 name+description 描述相关。"""
        t_raw = task or ""
        t_low = t_raw.lower()
        out = []
        pol = self.policy
        for s in self.all():
            if not s.enabled:
                continue
            if pol is not None and pol.is_blocked(s.id):
                continue
            if s.triggers:
                if any(k.lower() in t_low for k in s.triggers):
                    out.append(s)
            elif _desc_relevant(s, t_low, t_raw):
                out.append(s)
        return out


_ASCII_WORD = re.compile(r"[a-z0-9]{3,}")
_CJK_RUN = re.compile(r"[一-鿿]{2,}")


def _desc_relevant(skill, t_low: str, t_raw: str) -> bool:
    """无 triggers 时:技能 name+description 与任务是否关键词重叠。
    ASCII 词按词边界(避免 knowledge⊃now 式误命中);中文按 2-gram 子串。"""
    blob = f"{skill.name} {skill.description}"
    for w in set(_ASCII_WORD.findall(blob.lower())):
        if re.search(rf"\b{re.escape(w)}\b", t_low):
            return True
    for run in _CJK_RUN.findall(blob):
        for i in range(len(run) - 1):
            if run[i:i + 2] in t_raw:
                return True
    return False


def extract_skill_package(zip_bytes: bytes, max_uncompressed: int = 2_000_000):
    """从 OpenClaw 包(zip)按名读取 SKILL.md(+可选 _meta.json)。
    仅按名 read,不 extractall → 无 zip-slip;累计解压超上限 → 防 zip bomb。
    返回 (skill_md_text, meta_dict)。找不到 SKILL.md → ValueError。"""
    import io
    import json as _json
    import zipfile
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as e:
        raise ValueError(f"不是合法 zip: {e}")
    total = sum(i.file_size for i in zf.infolist())
    if total > max_uncompressed:
        raise ValueError(f"解压体积超上限({total} > {max_uncompressed})")
    skill_name = next((n for n in zf.namelist() if n.lower().endswith("skill.md")), None)
    if not skill_name:
        raise ValueError("包内未找到 SKILL.md")
    skill_md = zf.read(skill_name).decode("utf-8", "replace")
    meta: dict[str, Any] = {}
    meta_name = next((n for n in zf.namelist() if n.lower().endswith("_meta.json")), None)
    if meta_name:
        try:
            meta = _json.loads(zf.read(meta_name).decode("utf-8", "replace")) or {}
        except Exception:
            meta = {}
    return skill_md, meta
