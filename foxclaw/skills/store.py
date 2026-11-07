"""用户安装技能 + 内置技能启停覆盖的持久化。知识/记忆各有真源;技能注册表原为纯内存,
本层让"安装"重启不丢。文件:<data_dir>/user_skills.json。"""
from __future__ import annotations
import json
import threading
from pathlib import Path
from dataclasses import fields as _fields

from . import SkillSpec, SkillRegistry


class SkillStore:
    def __init__(self, data_dir):
        self._path = Path(data_dir) / "user_skills.json"
        self._lock = threading.Lock()

    def _spec_fields(self) -> set:
        return {f.name for f in _fields(SkillSpec) if f.name != "handler"}

    def load(self, registry: SkillRegistry):
        """启动恢复:注册用户技能 + 应用内置启停覆盖。文件缺失/损坏 → 静默跳过(全新库)。"""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        allowed = self._spec_fields()
        for d in data.get("skills", []):
            try:
                spec = SkillSpec(**{k: v for k, v in d.items() if k in allowed})
                spec.source = "user"
                registry.register(spec)
            except Exception:
                continue
        for sid, ov in (data.get("overrides") or {}).items():
            sp = registry.get(sid)
            if sp is not None and "enabled" in ov:
                sp.enabled = bool(ov["enabled"])

    def save(self, registry: SkillRegistry):
        """把用户技能 + 被改过 enabled 的内置技能原子写回。写盘失败不抛(内存注册仍生效)。"""
        with self._lock:
            user_skills, overrides = [], {}
            for sp in registry.all_including_disabled():
                if sp.source == "user":
                    user_skills.append(sp.to_dict())
                elif sp.enabled is False:
                    overrides[sp.id] = {"enabled": False}
            payload = {"skills": user_skills, "overrides": overrides}
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self._path)
            except Exception:
                pass
