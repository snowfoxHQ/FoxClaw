"""分层配置(E1 —— 范式「信任半径递减」的轻量落地)。

三层,信任从高到低:
  · policy   —— 管理员/最高。锁定项**不可被下层覆盖或撤销**(对应范式 deny 铁律)。
  · user     —— 运行时可改(如 skill_policy.json 的 deny/allow)。
  · runtime  —— 环境变量 / 默认值。

合并规则:
  · 标量:高层覆盖低层(policy > user > runtime)。
  · 集合(如 deny 安全规则):各层**并集,只增不减** —— 高层只能加严、不能被下层放松。

刻意只做三层(不照搬范式六层):够 Foxclaw 用,且**立即接线**到 SkillPolicy 的 deny 治理
(管理员强制 deny 某 skill、用户不可 undeny),不是「建了没接线」的孤儿设施。
"""
from __future__ import annotations
import os
import json
from pathlib import Path


class LayeredConfig:
    """三层配置合并原语。持有各层 dict 的引用 —— 若某层的集合值是可变对象(如 SkillPolicy 的
    user deny set),原地增删会被 merged_set/policy_locked 实时反映,无需重建。"""

    def __init__(self, policy: dict | None = None, user: dict | None = None, runtime: dict | None = None):
        self.policy = dict(policy or {})
        self.user = dict(user or {})
        self.runtime = dict(runtime or {})

    def get(self, key: str, default=None):
        """标量:高层优先(policy > user > runtime)。"""
        for layer in (self.policy, self.user, self.runtime):
            if key in layer:
                return layer[key]
        return default

    def merged_set(self, key: str) -> set:
        """集合类(deny 等):三层并集,只增不减(高层只能加严)。"""
        out: set = set()
        for layer in (self.policy, self.user, self.runtime):
            out |= set(layer.get(key) or [])
        return out

    def policy_locked(self, key: str) -> set:
        """policy 层锁定的项 —— 不可被下层覆盖/撤销(deny 铁律)。"""
        return set(self.policy.get(key) or [])


def load_policy_layer(data_dir=None) -> dict:
    """加载 policy 层(管理员锁定,与 user 层的 skill_policy.json **物理分开**):
      · 环境变量 `FOXCLAW_POLICY_DENY`(逗号分隔的 skill id);
      · `<data_dir>/policy.json` 的 `{"deny": [...]}`(若存在)。
    默认(两者都无)返回空 deny → 合并结果 = 现状(behavior-preserving)。"""
    deny: set = set()
    env = os.environ.get("FOXCLAW_POLICY_DENY", "")
    deny |= {s.strip() for s in env.split(",") if s.strip()}
    if data_dir:
        p = Path(data_dir) / "policy.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                deny |= set(d.get("deny") or [])
            except Exception:
                pass          # policy.json 损坏不应拖垮启动;宁可少锁不错锁,由运维排查
    return {"deny": sorted(deny)}
