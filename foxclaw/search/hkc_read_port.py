"""
HKCReadPort —— 把 foxclaw-hkc 的检索能力接入 HSR 的 knowledge_port。

融合之后:HKC 是 Foxclaw 内部的一个模块，不是外挂的独立系统。
Foxclaw 启动时会在同一个 Python 进程里直接装配 HKCContainer
(`foxclaw/__init__.py` 里的 `_init_hkc()`)，这里只是拿 hkc_sdk 的
DirectClient 包一层，调用的是同进程里的对象方法，不发 HTTP 请求、
不需要单独起服务、不存在"服务没启动"这种降级场景。

这一层只暴露 query()，没有 ingest_text()——写不写知识回 HKC
不是 HSR 的职责范围，那是 foxclaw-hkc 自己的 KDE/KEE 摄入管线，
或者未来 Runtime 层的决定。

如果确实需要把 foxclaw-hkc 单独部署成另一个服务(比如给其它系统复用)，
client 换成 hkc_sdk.connect(base_url=...) 返回的 HTTPClient 依然能用——
DirectClient/HTTPClient 方法签名完全一致，这里的代码不用改一行。
"""
from __future__ import annotations
import logging

from .models import RawEvidence, SourceType

logger = logging.getLogger(__name__)


class HKCReadPort:
    """只读查询 foxclaw-hkc。需要一个已经连好的 hkc_sdk 客户端
    (DirectClient 或 HTTPClient，两者接口一致)；没有就永远返回空列表，
    不影响 HSR 主流程——和整个项目的降级哲学一致(没配就跳过，不报错)。"""

    def __init__(self, client=None):
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None

    def query(self, text: str, top_k: int = 5) -> list[RawEvidence]:
        if self._client is None:
            return []

        try:
            hits = self._client.search(text, mode="hybrid", top_k=top_k)
        except Exception as e:
            logger.info(f"[HKC] 搜索「{text}」失败({e})")
            return []

        out: list[RawEvidence] = []
        for h in hits:
            fact = (h.summary or h.name or "").strip()
            if not fact:
                continue
            out.append(RawEvidence(
                id=h.ku_id,
                fact=fact,
                source=SourceType.KNOWLEDGE,
                origin=f"hkc:{h.ku_id}",
                confidence=float(h.score) if h.score else 0.6,
            ))
        return out
