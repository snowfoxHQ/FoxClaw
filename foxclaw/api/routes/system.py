"""系统状态 / 模型路由 / 认知调度 / 事件总线 / 真值 / 进程 / 思考链 / Harness / 探活 / 监控与追踪。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api.current_os()`
晚绑定读取(状态在 `_state`,测试可重绑 `_state._os_instance`);路由用 `@app` 装饰器在 import 时注册。
"""

import os
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from foxclaw import __version__

import foxclaw.api as _api
from .. import app
from ..middleware import _gateway_on, _GATEWAY
from ...core import settings, update_check


@app.get("/api/status")
async def get_system_status():
    """获取所有 Agent 的状态"""
    return _api.os_instance().get_all_status()


@app.get("/api/tokens")
async def get_token_summary():
    """Token 消耗摘要(成本可观测性):总量、按 Agent、按模型。"""
    meter = getattr(_api.current_os(), "token_meter", None)
    if meter is None:
        return {"error": "token meter 未启用"}
    return {
        "summary": meter.summary(),
        "by_agent": meter.by_agent(),
        "by_provider": meter.by_provider(),
    }


@app.get("/api/tokens/report")
async def get_token_report():
    """Token 消耗的人类可读文本报告。"""
    meter = getattr(_api.current_os(), "token_meter", None)
    if meter is None:
        return {"error": "token meter 未启用"}
    return {"report": meter.report()}


@app.get("/api/model")
async def get_model():
    """当前模型路由状态 + 可用 provider 列表。"""
    return _api.os_instance().router.info()


@app.get("/api/config/amap")
async def get_amap_config():
    """下发前端高德 JSAPI Key + 安全密钥(从 .env 读取,不硬编码在前端)。"""
    return {
        "js_key": os.environ.get("AMAP_JS_KEY", ""),
        "js_scode": os.environ.get("AMAP_JS_SCODE", ""),
    }


@app.get("/api/system/info")
async def get_system_info():
    """系统级只读信息:版本、运行参数、各 Agent、模块状态(供设置面板展示)。"""
    update_check.maybe_refresh()
    agents = {}
    for aid, ag in _api.os_instance()._agents.items():
        h = ag.harness
        agents[aid] = {
            "name": getattr(ag, "AGENT_NAME", aid),
            "shadow_mode": h.is_shadow_mode,
            "shadow_rounds": h.shadow_rounds,
            "run_count": getattr(h, "_run_count", 0),
            "semantic_degraded": getattr(h, "semantic_degraded", False),
            "embedding_mode": getattr(getattr(h, "sem_drift", None), "mode", "unknown"),
        }
    return {
        "version": __version__,
        "update": update_check.status(),
        "data_dir": os.environ.get("FOXCLAW_DATA_DIR", "./foxclaw_data"),
        "shadow_rounds": settings.get_int("FOXCLAW_SHADOW_ROUNDS"),
        "model": _api.os_instance().router.info(),
        "amap": {
            "web_key": bool(os.environ.get("AMAP_MAPS_API_KEY")),
            "js_key": bool(os.environ.get("AMAP_JS_KEY")),
            "js_scode": bool(os.environ.get("AMAP_JS_SCODE")),
        },
        "agents": agents,
    }


class ModelSwitchPayload(BaseModel):
    provider: str
    model: Optional[str] = None


@app.post("/api/model/switch")
async def switch_model(p: ModelSwitchPayload):
    """运行时切换模型 provider(多模型路由核心能力)。"""
    try:
        return _api.os_instance().router.switch(p.provider, p.model)
    except Exception as e:
        raise HTTPException(400, f"切换失败: {e}")


@app.get("/api/agents/{agent_id}/status")
async def get_agent_status(agent_id: str):
    agent = _api.os_instance().get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} 不存在")
    return agent.get_status()


class SchedulePreviewPayload(BaseModel):
    task: str


@app.post("/api/scheduler/preview")
async def scheduler_preview(payload: SchedulePreviewPayload):
    """认知调度预览:给一个任务,返回调度决策(优先级/意图/路由/抢占),不实际执行。"""
    from foxclaw.core.scheduler import CognitiveScheduler, SystemContext
    sch = CognitiveScheduler()
    d = sch.schedule(payload.task, SystemContext())
    return d.to_dict()


@app.get("/api/scheduler/stats")
async def scheduler_stats():
    """调度器记账:并发配额 + 提交/运行/完成/失败/峰值(可观测性)。"""
    return _api.os_instance().scheduler.stats()


@app.get("/api/bus/history")
async def bus_history(channel: str | None = None, limit: int = 30):
    """系统事件总线历史:观测执行→审计→记忆的真值流转。"""
    try:
        return {"events": _api.os_instance().system_bus.history(channel, limit)}
    except Exception:
        return {"events": []}


@app.get("/api/truth/last")
async def truth_last():
    """真值管线最近一次的 trace 与记忆更新。"""
    tp = _api.os_instance().truth_pipeline
    return {"last_trace": tp.last_trace, "last_memory_update": tp.last_memory_update}


@app.get("/api/processes")
async def list_processes(state: str | None = None, limit: int = 30):
    """进程列表:观测任务作为"进程"的生命周期状态。"""
    try:
        return {"processes": _api.os_instance().process_manager.list(state, limit),
                "stats": _api.os_instance().process_manager.stats()}
    except Exception:
        return {"processes": [], "stats": {}}


@app.get("/api/processes/{process_id}")
async def get_process(process_id: str):
    """单个进程详情:含完整状态历史(state_history)。"""
    rec = _api.os_instance().process_manager.get(process_id)
    return rec.to_dict() if rec else {"error": "not_found"}


@app.get("/api/cognitive/state")
async def cognitive_state():
    """认知状态快照:系统此刻在想什么(目标/焦点/注意力/激活记忆/活跃进程)。"""
    try:
        return _api.os_instance().cognitive_state.snapshot()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/thoughtchain/{agent_id}/chain")
async def get_thought_chain(agent_id: str, session_id: str | None = None):
    """获取 Agent 的推理链"""
    tc = _api.os_instance().thought_chain
    if session_id:
        nodes = tc.get_chain(session_id, agent_id)
    else:
        nodes = tc.get_blocked_nodes(agent_id, limit=20)
    return {"nodes": [n.to_dict() for n in nodes]}


@app.get("/api/thoughtchain/{agent_id}/blocked")
async def get_blocked_nodes(agent_id: str, limit: int = 20):
    """获取被 Harness 截断的节点（审计用）"""
    tc = _api.os_instance().thought_chain
    nodes = tc.get_blocked_nodes(agent_id, limit=limit)
    return {"blocked": [n.to_dict() for n in nodes], "count": len(nodes)}


@app.get("/api/thoughtchain/{agent_id}/trend")
async def get_drift_trend(agent_id: str, limit: int = 100):
    """获取漂移趋势数据（前端可视化用）"""
    tc = _api.os_instance().thought_chain
    trend = tc.get_delta_trend(agent_id, limit=limit)
    return {"trend": trend, "agent_id": agent_id}


@app.get("/api/thoughtchain/{agent_id}/summary")
async def get_thought_summary(agent_id: str):
    """获取 ThoughtChain 健康摘要"""
    return _api.os_instance().thought_chain.get_summary(agent_id)


@app.get("/api/harness/{agent_id}/status")
async def get_harness_status(agent_id: str):
    """获取 Harness 状态：阈值、shadow mode、policy 摘要"""
    agent = _api.os_instance().get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} 不存在")
    return {
        "agent_id": agent_id,
        "thresholds": agent.harness.thresholds,
        "policy": agent.harness.get_policy_summary(),
        "weights": agent.harness.weights,
    }


@app.get("/api/harness/{agent_id}/policy")
async def get_policy(agent_id: str):
    """获取 Agent 的 Policy YAML（完整内容）"""
    agent = _api.os_instance().get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} 不存在")
    return agent.harness._policy


@app.get("/health")
async def health():
    """存活探针(liveness):浅、快,不触发子系统。"""
    return {"status": "ok", "service": "foxclaw", "version": __version__}


@app.get("/health/ready")
async def health_ready():
    """就绪探针(readiness):逐子系统深度检查。**开放**(探针无需鉴权),
    组件只回 ok/stub/down,不泄露 provider 名等细节。不触发 HKC 惰性初始化。"""
    o = _api.current_os()
    comps = {}
    comps["hmr"] = "ok" if getattr(o, "hmr", None) is not None else "down"
    _router = getattr(o, "router", None)
    if _router is None:
        comps["router"] = "down"
    elif getattr(_router, "is_stub", True):
        comps["router"] = "stub"
    else:
        comps["router"] = "ok"
    comps["scheduler"] = "ok" if getattr(o, "scheduler", None) is not None else "down"
    hkc_svc = getattr(o, "_hkc_service", None)
    comps["hkc"] = ("ok" if (hkc_svc is not None and getattr(hkc_svc, "available", False))
                    else ("uninitialized" if hkc_svc is None else "degraded"))
    comps["mcp_server"] = "on" if getattr(o, "mcp_manager", None) is not None else "off"
    down = [k for k, v in comps.items() if v == "down"]
    return {"status": "degraded" if down else "ok", "components": comps, "version": __version__}


@app.get("/api/metrics")
async def metrics_json():
    """可观测:各端点/LLM 的计数、错误率、延迟分位(p50/p95/avg)+ uptime。"""
    from ...core.metrics import METRICS
    return METRICS.snapshot()


@app.get("/api/traces")
async def list_traces(limit: int = 30, persisted: bool = False,
                      slow_ms: float | None = None, errors_only: bool = False):
    """请求链路。默认看内存里最近若干条;persisted=1 查**已落库的历史**
    (可加 slow_ms / errors_only 过滤)——「找出昨天那次慢请求」走这里。"""
    from ...core.tracing import TRACES
    limit = max(1, min(limit, 200))
    if persisted or slow_ms is not None or errors_only:
        return {"traces": TRACES.query(limit=limit, slow_ms=slow_ms,
                                       errors_only=errors_only), "source": "persisted"}
    return {"traces": TRACES.list(limit=limit), "source": "memory"}


@app.get("/api/traces/stats")
async def traces_stats():
    """OTLP 导出状态(未配 FOXCLAW_OTLP_ENDPOINT 则 enabled=false,完全不启用)。
    注意:本路由必须定义在 /api/traces/{trace_id} **之前**,否则 "stats" 会被当成 trace_id。"""
    from ...core.tracing import OTLP
    return {"otlp": OTLP.stats()}


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str):
    """单条链路详情:各 span 的耗时/成败(定位这次慢在哪)。内存未命中则回落到库。"""
    from ...core.tracing import TRACES
    d = TRACES.get_dict(trace_id)
    if d is None:
        raise HTTPException(404, "trace 不存在(内存环已滚出且未被采样落库)")
    return d


@app.get("/api/gateway/stats")
async def gateway_stats():
    """API 网关状态:是否启用 + 每分钟/每小时额度 + 当前追踪的 client 数 + 累计拒绝数。"""
    return {"enabled": _gateway_on(), **_GATEWAY.stats()}


@app.get("/metrics")
async def metrics_prometheus():
    """Prometheus 文本格式(供 scrape)。"""
    from ...core.metrics import METRICS
    return PlainTextResponse(METRICS.prometheus_text())
