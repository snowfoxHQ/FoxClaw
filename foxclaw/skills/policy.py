"""全局技能策略:deny 封杀 + allow 白名单(治理 kill-switch,借鉴 OpenClaw plugins.deny/allow)。
deny 压倒一切;allow 非空则只放行白名单内。user 层持久化 <data_dir>/skill_policy.json。
E1 分层:另有 **policy 层** deny(管理员锁定,env FOXCLAW_POLICY_DENY + <data_dir>/policy.json,与
user 层物理分开),**不可被运行时 undeny 撤销**;经 core/config.py 的 LayeredConfig 合并(policy∪user)。"""
from __future__ import annotations
import json
import threading
from pathlib import Path


class SkillPolicy:
    def __init__(self, data_dir):
        self._path = Path(data_dir) / "skill_policy.json"
        self._lock = threading.RLock()
        self._deny: set = set()    # user 层:运行时可 deny/undeny,持久化到 skill_policy.json
        self._allow: set = set()   # 空 = 全放行
        # E1 分层:policy 层 deny(管理员锁定,env FOXCLAW_POLICY_DENY + <data_dir>/policy.json)。
        # 与 user 层物理分开、不写回 skill_policy.json;**不可被 undeny 撤销**。默认空 = 现状。
        from ..core.config import LayeredConfig, load_policy_layer
        policy_layer = load_policy_layer(data_dir)
        # user 层传 self._deny(set 引用)→ 运行时增删被 merged_set 实时反映,无需重建 LayeredConfig
        self._layers = LayeredConfig(policy=policy_layer, user={"deny": self._deny})
        self._load()

    def is_blocked(self, skill_id: str) -> bool:
        with self._lock:
            # deny = policy 层 ∪ user 层(只增不减);deny 压倒 allow
            if skill_id in self._layers.merged_set("deny"):
                return True
            if self._allow and skill_id not in self._allow:
                return True
            return False

    def deny(self, skill_id: str):
        with self._lock:
            self._deny.add(skill_id)
        self._save()

    def undeny(self, skill_id: str) -> bool:
        with self._lock:
            if skill_id in self._layers.policy_locked("deny"):
                return False       # E1:policy 层为管理员锁定,用户 undeny 无效(deny 铁律)
            existed = skill_id in self._deny
            self._deny.discard(skill_id)
        self._save()
        return existed

    def set_allow(self, ids):
        with self._lock:
            self._allow = set(ids or [])
        self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return {"deny": sorted(self._deny), "allow": sorted(self._allow),
                    "policy_deny": sorted(self._layers.policy_locked("deny"))}  # E1:管理员锁定项(只读)

    def _load(self):
        if not self._path.exists():
            return
        try:
            d = json.loads(self._path.read_text(encoding="utf-8"))
            # 就地更新 self._deny(不重新赋值!)——LayeredConfig.user 持有本 set 的引用,
            # 重新赋值会断开引用,导致从盘加载的 user deny 不被 merged_set 反映(E1 集成的坑)。
            self._deny.clear()
            self._deny.update(d.get("deny") or [])
            self._allow = set(d.get("allow") or [])
        except Exception:
            pass

    def _save(self):
        with self._lock:
            payload = {"deny": sorted(self._deny), "allow": sorted(self._allow)}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass
