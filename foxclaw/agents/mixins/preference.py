"""PreferenceMixin:用户偏好采集 / 注入 / 查看 / 删除。

从 `foxclaw/agents/base.py` 搬移出来的偏好相关方法。
"""
import os
from typing import Any, Callable


class PreferenceMixin:
    # ── 宿主 CognitiveAgent 注入的状态与方法:纯声明,不产生运行时属性 ──
    # 不声明的话,检查器在每个 self.xxx 处报 attr-defined。
    AGENT_ID: str
    hmr: Any
    _think: Callable[..., Any]
    _store_memory: Callable[..., Any]
    # 注入近期偏好(不管当前 query),让"越用越懂你"真生效。默认开;FOXCLAW_PREF_CAPTURE=0 关采集。
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
        # 偏好通道消毒(防投毒):偏好每轮以"一律以此为准"的权威口吻注入,且无法像外部数据那样
        # 包 <untrusted>(它本就该被遵循)。所以只能在**存入侧**拦——若这条"偏好"候选里夹带注入/
        # 越权特征(如用户把含"忽略以上指令/系统覆盖"的文档粘进对话),就不固化,断掉投毒源头。
        from ...core import injection_guard as _ig
        if _ig.scan(t):
            self._think(content="偏好候选含疑似注入特征,已拒绝固化(防记忆投毒)",
                        node_type="reflection", conf=0.9)
            return
        try:
            # 用 tag 标记(不受 memory_type 白名单降级);shared=True 跨会话跨 Agent 持久
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
        # prefs 已按"新→旧"排序(recall_by_tag)。偏好会随时间改变(如"最喜欢的颜色"从蓝改灰),
        # 语义相似度无法区分同话题偏好(实测"颜色"与"回答风格"偏好都 ~0.9),故不能靠自动删旧;
        # 改由指令让 LLM 以"最新的(最靠前)/当前对话最近说法"为准,避免被已更新的旧偏好带偏。
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
