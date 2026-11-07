"""SKILL.md 指令技能持久化。<data_dir>/markdown_skills.json。"""
from __future__ import annotations
import json
import threading
from pathlib import Path
from dataclasses import fields as _fields

from .markdown_skill import MarkdownSkill, MarkdownSkillRegistry


class MarkdownStore:
    def __init__(self, data_dir):
        self._path = Path(data_dir) / "markdown_skills.json"
        self._lock = threading.Lock()

    def load(self, reg: MarkdownSkillRegistry):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        allowed = {f.name for f in _fields(MarkdownSkill)}
        for d in data.get("skills", []):
            try:
                reg.register(MarkdownSkill(**{k: v for k, v in d.items() if k in allowed}))
            except Exception:
                continue

    def save(self, reg: MarkdownSkillRegistry):
        with self._lock:
            payload = {"skills": [s.to_dict() for s in reg.all()]}
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self._path)
            except Exception:
                pass
