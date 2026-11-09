"""PreferenceMixin:用户偏好采集 / 注入 / 查看 / 删除。

从 `foxclaw/agents/base.py` 搬移出来的偏好相关方法。
"""
import os
from typing import Any, Callable


class PreferenceMixin:
    AGENT_ID: str
    hmr: Any
    _think: Callable[..., Any]
    _store_memory: Callable[..., Any]
    _PREF_MARKERS = ("我喜欢", "我不喜欢", "我讨厌", "我希望", "别再", "不要再", "以后都",
                     "以后请", "记住", "我的偏好", "我习惯", "下次请", "下次记得", "我倾向",
                     "请务必", "请一律", "总是用", "我要求")

    def _maybe_store_preference(self, task):
        """检测明确的用户偏好/纠正表达 → 存为 preference 类型(shared,跨会话跨 Agent 持久)。
        保守:只在含明确偏好信号词时存,宁漏不误。偏好是"用户主动声明的持久意愿",不受 P5
        「记忆是快照非事实」约束(那管的是会过时的金额/状态)。任何异常不影响主流程。"""
        if os.environ.get("FOXCLAW_PREF_CAPTURE", "") == "0":
            return
        t = (task or "").strip()
        if not t or not any(k in t for k in self._PREF_MARKERS):
            return
        from ...core import injection_guard as _ig
        if _ig.scan(t):
            self._think(content="偏好候选含疑似注入特征,已拒绝固化(防记忆投毒)",
                        node_type="reflection", conf=0.9)
            return
        try:
            self._store_memory(f"用户偏好:{t[:200]}", mem_type="preference",
                               tags=["preference"], shared=True)
        except Exception:
            pass

    def _prefs_context(self, limit=8):
        """拉本 Agent + shared 的 preference 记忆,组装成注入文本(**独立于语义 query → 总是注入**)。
        无偏好 / HMR 不可用 → 空串(不注入,行为逐字不变)。"""
        try:
            prefs = self.hmr.recall_by_tag(agent_id=self.AGENT_ID, tag="preference", limit=limit)
        except Exception:
            return ""
        if not prefs:
            return ""
        lines = "\n".join(f"- {p.get('content', '')}" for p in prefs)
        return ("【用户偏好(按最近→最早排列)】\n" + lines +
                "\n**注意:偏好会随时间改变。若上面几条彼此冲突,或与当前对话中用户最近的说法不一致,"
                "一律以最新的(列在最前的)或当前对话中用户最近一次的表达为准,不要沿用已被更新的旧偏好。**")

    def _pref_augment(self, messages):
        """对话流式路径的偏好注入:把偏好合并进**最后一条 user 消息**(与 _web_augment 同法,避免
        连续两条 user 违反模型无关铁律,见 A3)。无偏好 → 原样返回。"""
        ctx = self._prefs_context()
        if not ctx:
            return messages
        merged = list(messages)
        for i in range(len(merged) - 1, -1, -1):
            if merged[i].get("role") == "user":
                m = dict(merged[i]); m["content"] = ctx + "\n\n" + (m.get("content") or "")
                merged[i] = m
                return merged
        return [{"role": "user", "content": ctx}] + merged

    def list_preferences(self, limit=50):
        """列出所有已记住的用户偏好(preference tag,含 shared)——供查看/清理误存(F3)。"""
        try:
            return self.hmr.recall_by_tag(agent_id=None, tag="preference", limit=limit)
        except Exception:
            return []

    def delete_preference(self, pref_id):
        """删一条误存的用户偏好(by memory id)——防误报偏好持久污染(F3)。返回是否删到。"""
        try:
            return self.hmr.delete_memory(pref_id)
        except Exception:
            return False

    _NICK_PREFIXES = ("称呼我为", "叫我", "我的名字是", "我的名字叫")

    def remember_preference(self, text: str) -> bool:
        """写一条用户主动声明的偏好(设置面板 / 首次引导用)。返回是否落库。

        与 `_maybe_store_preference`(对话里自动抓)的区别:这条不经过注入扫描,
        因为来源是设置表单而非自由文本;但同样限长 200,避免把整段话塞进偏好。
        """
        t = (text or "").strip()
        if not t:
            return False
        try:
            self._store_memory(f"用户偏好:{t[:200]}", mem_type="preference",
                               tags=["preference"], shared=True)
            return True
        except Exception:
            return False

    @classmethod
    def _nick_of(cls, content: str) -> str | None:
        """从一条 preference 文本里取出称呼;不是称呼类返回 None。"""
        c = (content or "").strip()
        if c.startswith("用户偏好:"):
            c = c[len("用户偏好:"):].strip()
        for prefix in cls._NICK_PREFIXES:
            if c.startswith(prefix):
                return c[len(prefix):].strip(" :：,，。.")
        return None

    def get_nickname(self) -> str:
        """当前称呼(取最近一条称呼类偏好;没设置过返回空串)。"""
        for p in self.list_preferences(limit=200):
            nick = self._nick_of(p.get("content", ""))
            if nick:
                return nick
        return ""

    def set_nickname(self, nick: str) -> str:
        """设置称呼;传空串 = 清除。返回生效后的值。

        先把旧的称呼条目删掉再写:否则每改一次就多一条「称呼我为 X」,注入时
        几条互相矛盾(虽然 prompt 里约定「以最新为准」,但没必要让它去猜)。
        """
        value = (nick or "").strip()[:24]
        for p in self.list_preferences(limit=200):
            if self._nick_of(p.get("content", "")):
                pid = p.get("id")
                if pid:
                    self.delete_preference(pid)
        if value:
            self.remember_preference(f"称呼我为 {value}")
        return value
