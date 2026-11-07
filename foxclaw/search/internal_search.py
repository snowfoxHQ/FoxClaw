"""
Internal Search Engine —— HSR-I 路由的内部检索器

串联 HMR(记忆)与 HKC(知识，在同一进程内直连，Foxclaw 启动时装配好后
通过 knowledge_port 传进来)：

    1. 若配置了 knowledge_port：先查它拿权威知识候选
    2. 查 HMR 拿原始记忆，补充上下文 + 给①做溯源材料
    3. 交叉关联：knowledge 候选如果能在 HMR 里找到对应记忆，
       标记 grounded_by；找不到就做置信度惩罚（孤证）
    4. 没配置 knowledge_port / 没查到候选时，自然退化为纯 HMR 检索

knowledge_port 只需要实现 query(text, top_k) -> list[RawEvidence]，
这里不关心它具体是 foxclaw-hkc、还是别的什么知识源。
"""
from __future__ import annotations
from difflib import SequenceMatcher
from .models import RawEvidence

_GROUNDING_SIMILARITY = 0.5
_UNGROUNDED_PENALTY = 0.15


class InternalSearchEngine:
    def __init__(self, memory_port=None, knowledge_port=None, agent_port=None):
        self.memory_port = memory_port      # 只读查询 HMR：query(text, top_k) -> list[RawEvidence]
        self.knowledge_port = knowledge_port  # HKC 只读查询接口(同进程 DirectClient)
        self.agent_port = agent_port        # Agent 源:领域内核 + 推理链 + 各Agent记忆 + 能力/状态

    @staticmethod
    def _safe_query(port, text: str, top_k: int) -> list[RawEvidence]:
        """【深审修复·内部源隔离】逐个内部源 try/except → [],与 external 端口的隔离对称:
        任一内部源(知识/记忆/Agent)失败都只让该源返回空,**不掀翻整个 search**。
        现有三端口虽各自兜异常,但此处防御式隔离让"接入不兜异常的自定义 port"也安全。"""
        if port is None:
            return []
        try:
            return port.query(text, top_k) or []
        except Exception:
            return []

    def search(self, text: str, top_k: int = 5) -> list[RawEvidence]:
        from foxclaw.core.tracing import span as _span
        with _span("search.internal", top_k=top_k):
            knowledge_hits = self._safe_query(self.knowledge_port, text, top_k)
            memory_hits = self._safe_query(self.memory_port, text, top_k)
            agent_hits = self._safe_query(self.agent_port, text, top_k)

        # 知识候选用「内部一切可溯源证据」(记忆 + Agent 态势)交叉验证:找得到就 grounded,
        # 找不到就做孤证惩罚。这样 HKC 权威知识与 Agent 实际持有的数据互为印证。
        if knowledge_hits:
            knowledge_hits = self._cross_link(knowledge_hits, memory_hits + agent_hits)

        return knowledge_hits + memory_hits + agent_hits

    def _cross_link(self, knowledge_hits, memory_hits) -> list[RawEvidence]:
        linked = []
        for k in knowledge_hits:
            trace = self._find_trace(k, memory_hits)
            if trace:
                k.grounded_by = trace.id
            else:
                k.confidence = max(0.0, k.confidence - _UNGROUNDED_PENALTY)
            linked.append(k)
        return linked

    def _find_trace(self, knowledge: RawEvidence, memory_hits: list[RawEvidence]):
        best, best_sim = None, 0.0
        for m in memory_hits:
            sim = SequenceMatcher(None, knowledge.fact, m.fact).ratio()
            if sim >= _GROUNDING_SIMILARITY and sim > best_sim:
                best, best_sim = m, sim
        return best
