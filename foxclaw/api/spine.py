"""事件脊柱辅助:WS payload 净化 + HKC 后台预热。

从 `foxclaw/api/__init__.py` 搬移出来的纯辅助函数。`_register_spine_projection`
因 `_WS_MUTED` 受测试源码级断言约束(需留在主文件 `__init__.py` 源码内)而未搬移。
"""
import json
import threading
import time as _t

import foxclaw.api as _api


def _ws_safe_payload(payload, _maxlen: int = 4000):
    """把任意事件 payload 收敛成 **JSON 安全且有界** 的形式,供 WS 广播。
    统一投影会把系统事件(execution.completed 等含原始 result/任意对象)也推给 WS,
    必须防两类真实故障:
      · 非 JSON 可序列化对象 → ws.send_json 抛异常 → broadcast 静默丢弃该连接;
      · 超大 payload(整段 LLM 回复 / 大 result,且随真值链被广播多次)→ 向所有 WS 泛洪。
    脊柱本身仍保留全保真事件(供 history / 审计 / 其它投影),净化只发生在 WS 出口。"""
    try:
        raw = json.dumps(payload, default=str, ensure_ascii=False)
    except Exception:
        return {"_repr": str(payload)[:_maxlen]}
    if len(raw) > _maxlen:
        return {"_truncated": True, "_preview": raw[:_maxlen]}
    return json.loads(raw)


def _prewarm_hkc(osi):
    """后台预热 HKC:装配容器 + 建搜索索引(bm25+faiss over 全量 KU,顺带加载 HKC
    多语言 embedding 模型)+ 载入 learning。把"首次知识查询"的一次性冷成本(~几十秒到
    1 分钟)挪到开机后台完成,之后所有知识查询都不再冷。守护线程、失败静默,绝不影响主应用。
    索引重建与查询共用 HybridSearch 锁 → 预热期间若有查询会短暂等待,预热完即快。"""

    def _run():
        t0 = _t.time()
        try:
            svc = getattr(osi, "hkc", None)
            if not svc or not getattr(svc, "available", False):
                return
            try:
                lr = getattr(osi, "learning_runtime", None)
                if lr is not None:
                    lr._ensure_hkc_loaded()
            except Exception:
                pass
            print(f"[Foxclaw API] ✓ HKC 预热完成（{_t.time()-t0:.1f}s）: 首次知识查询不再冷装配")
        except Exception as e:
            print(f"[Foxclaw API] · HKC 预热未启用(可选): {e}")

    _api._prewarm_thread = threading.Thread(target=_run, name="hkc-prewarm", daemon=True)
    _api._prewarm_thread.start()
