"""任务执行 / 流式直答 / 流式任务 路由与多模态护栏。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api._os_instance`
晚绑定读取;路由用 `@app` 装饰器在 import 时注册。流式限流器 `_STREAM_LIMITER` 为
模块级单例,`__init__.py` 会 re-export(测试复位其桶容量)。
"""

import json
import asyncio
import threading as _threading

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import foxclaw.api as _api
from .. import app
from ...mcp_server import _RateLimiter as _RL
from ...core import settings


# ── 请求模型 ───────────────────────────────────────────────

class TaskRequest(BaseModel):
    task: str
    agent_id: str = "supervisor"
    context: dict = {}
    images: list = []       # 多模态对话:[{media_type,data(base64)}];仅 /chat/stream 消费
    history: list = []      # 多轮上下文:[{role:'user'/'assistant',content}];前端带最近几轮,
                            # 后端拼进 LLM messages,让对话连贯(否则每轮无状态、记不住上一句)
    session_id: str = ""    # 会话归属:非空则本轮问答落盘到该会话(供"会话历史"回看)
    attachments: list = []  # 聊天附件:[{name,size,mime,url,text}];text 为提取正文(可为空),
                            # 仅 /chat/stream 消费;随问题注入 LLM + 落盘会话供回看


class GoalRequest(BaseModel):
    agent_id: str
    goal: str
    allowed_tools: list[str] = []
    entities: list[str] = []


# ── 任务执行 ───────────────────────────────────────────────

@app.post("/api/task")
async def run_task(req: TaskRequest):
    """执行任务，返回结果 + ThoughtChain 摘要。

    把认知处理 offload 到工作线程:每请求的 CPU 密集同步工作(harness 语义嵌入等)不再
    阻塞事件循环,并发请求的嵌入计算可真正并行(torch/ST CPU 前向释放 GIL),吞吐随核数扩展。
    · contextvars 随 to_thread 复制到工作线程 → 每请求会话状态仍隔离(见 base.py);
    · 内层 LLM 的 to_thread 用各自 asyncio.run 的独立事件循环 executor,不与外层竞争
      同一线程池,故无嵌套线程池死锁;
    · harness 累积态已在 harness.py 用 RLock 保护(嵌入在锁外,故并行不受锁影响)。"""
    def _work():
        # 经微内核调度器准入(默认无上限=直通;设了 FOXCLAW_MAX_CONCURRENCY 才背压)+ 生命周期事件
        return _api.os_instance().scheduler.run(
            lambda: asyncio.run(_api.os_instance().run(req.task, req.agent_id,
                                                      context=req.context)),
            agent_id=req.agent_id, task=req.task)
    return await asyncio.to_thread(_work)


# 流式限流 + 并发上限(防刷爆/资源耗尽;env 可配)
_STREAM_LIMITER = _RL(settings.get_int("FOXCLAW_STREAM_RATE"))                   # 次/分
_STREAM_SEM = _threading.Semaphore(settings.get_int("FOXCLAW_STREAM_CONCURRENCY"))


_MAX_IMAGES = 6
_MAX_IMG_B64 = 10 * 1024 * 1024        # 单图 base64 上限 ~10MB(≈7.5MB 原图)
_MAX_IMG_TOTAL = 20 * 1024 * 1024      # 所有图 base64 累计上限(防 6×10MB=60MB 聚合 payload)
_ALLOWED_IMG_MT = {"image/png", "image/jpeg", "image/webp", "image/gif"}

_MAX_ATTACHMENTS = 6                   # 单次问答最多附件数
_ATTACH_TEXT_MAX = 20000               # 单附件提取正文上限
_ATTACH_TEXT_TOTAL = 60000             # 全部附件正文累计上限(≈1.5 万 token,防撑爆上下文)


def _sanitize_images(images):
    """校验/裁剪前端传来的多模态图片:限数量、限单图大小、限累计总量、白名单类型。
    防超大 payload/OOM(前端已缩放,这是对直连/恶意客户端的后端自保)。"""
    out = []
    total = 0
    if not isinstance(images, list):
        return out
    for im in images[:_MAX_IMAGES]:
        if not isinstance(im, dict):
            continue
        mt = str(im.get("media_type", "")).lower()
        d = im.get("data", "")
        if mt not in _ALLOWED_IMG_MT:
            continue
        if not isinstance(d, str) or not d or len(d) > _MAX_IMG_B64:
            continue
        if total + len(d) > _MAX_IMG_TOTAL:    # 累计超总上限 → 停(丢弃其余)
            break
        total += len(d)
        out.append({"media_type": mt, "data": d})
    return out


def _sanitize_attachments(attachments):
    """校验/裁剪前端传来的聊天附件:限数量、字段清洗、正文长度上限 + 累计上限。
    附件是前端经 /api/upload 上传后带回的引用({name,size,mime,url,text});这里只做
    直连/恶意客户端的自保,不信任 text 之外任何字段用于文件系统操作(下载按 url 里的 id 白名单)。"""
    out = []
    total = 0
    if not isinstance(attachments, list):
        return out
    for a in attachments[:_MAX_ATTACHMENTS]:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "")[:200]
        text = str(a.get("text") or "")[:_ATTACH_TEXT_MAX]
        if not name and not text:
            continue
        if total + len(text) > _ATTACH_TEXT_TOTAL:   # 累计正文超总上限 → 截断(丢弃后续正文)
            text = text[:_ATTACH_TEXT_TOTAL - total]
        total += len(text)
        try:                                        # size 可能是脏值("big"/None),护栏绝不因此抛异常
            size = int(a.get("size")) if a.get("size") else 0
        except (TypeError, ValueError):
            size = 0
        out.append({
            "name": name,
            "size": size,
            "mime": str(a.get("mime") or "")[:100],
            "url": str(a.get("url") or "")[:500],
            "text": text,
        })
    return out


@app.post("/api/chat/stream")
async def chat_stream(req: TaskRequest, request: Request):
    """流式直答(方案A):SSE 逐字返回 Agent LLM 输出。用于对话/问答;走完整认知管线的任务执行
    用 /api/task(阻塞)或 /api/task/stream(流式,D1);此端点不跑 recall/verify/reflect 等 stage。
    背压:令牌桶限流 + 并发信号量;客户端断连 → stop_event 终止 LLM 流(省 token)。
    images:多模态对话,随问题发给视觉模型(非视觉模型 router 层报错→降级)。"""
    if not _STREAM_LIMITER.allow():
        async def _rl():
            yield "data: " + json.dumps({"error": "rate limited"}) + "\n\n"
        return StreamingResponse(_rl(), media_type="text/event-stream")

    agent = _api.os_instance()._agents.get(req.agent_id) or _api.os_instance().supervisor
    imgs = _sanitize_images(req.images)
    atts = _sanitize_attachments(req.attachments)

    async def async_sse():
        if not _STREAM_SEM.acquire(blocking=False):   # 超并发上限 → 拒(不排队)
            yield "data: " + json.dumps({"error": "busy, try again later"}) + "\n\n"
            return
        q: asyncio.Queue = asyncio.Queue()
        stop = _threading.Event()
        loop = asyncio.get_running_loop()

        def _producer():
            _parts = []

            def _on_thinking(delta: str) -> None:
                # 推理走独立事件,不混进正文:前端折叠展示,正文仍只收 {"delta"}。
                loop.call_soon_threadsafe(q.put_nowait, {"thinking": delta})

            def _on_evidence(evs: list) -> None:
                # 执行证据(核验结论 + 来源链接/文件)整块送:前端挂在答案下方供核对,
                # 不进正文(正文是给用户读的答案,证据是给人查的凭据)。
                loop.call_soon_threadsafe(q.put_nowait, {"evidence": evs})

            try:
                for chunk in agent.stream_reply(req.task, stop_event=stop, images=imgs,
                                                history=req.history, attachments=atts,
                                                on_thinking=_on_thinking,
                                                on_evidence=_on_evidence):
                    _parts.append(chunk)
                    loop.call_soon_threadsafe(q.put_nowait, {"delta": chunk})
                loop.call_soon_threadsafe(q.put_nowait, {"done": True})
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"error": str(e)})
            finally:
                # 落盘会话历史(问 + 答),供"会话/所有会话"回看。断连中止也保留已生成部分。
                if req.session_id:
                    try:
                        _api.os_instance().sessions.append(req.session_id, "user", req.task, attachments=atts)
                        _reply = "".join(_parts)
                        if _reply:
                            _api.os_instance().sessions.append(req.session_id, "assistant", _reply)
                    except Exception:
                        pass
                loop.call_soon_threadsafe(q.put_nowait, None)   # 结束哨兵

        _threading.Thread(target=_producer, daemon=True, name="chat-stream").start()
        try:
            while True:
                if await request.is_disconnected():
                    stop.set(); break                 # 断连 → 终止 LLM 流
                try:
                    item = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                if "thinking" in item:
                    # 命名事件(SSE 规范):event: thinking + data。老客户端只认 data: 行也不会被
                    # 误导成正文(delta 字段不存在);新前端按事件名折叠展示。
                    yield ("event: thinking\ndata: "
                           + json.dumps({"thinking": item["thinking"]}, ensure_ascii=False)
                           + "\n\n")
                    continue
                if "evidence" in item:
                    # 与 thinking 同理走命名事件:老客户端拿不到这个事件名也不会把凭据
                    # 当成正文拼进回答。
                    yield ("event: evidence\ndata: "
                           + json.dumps({"evidence": item["evidence"]}, ensure_ascii=False)
                           + "\n\n")
                    continue
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        finally:
            stop.set()
            _STREAM_SEM.release()

    return StreamingResponse(async_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/task/stream")
async def task_stream(req: TaskRequest, request: Request):
    """流式任务执行(D1):SSE 逐 stage 进度 + act 逐 token + 最终结果。用于任务/分析等走**完整
    认知管线**的场景(对话直答走 /api/chat/stream)。事件:`{stage}`/`{plan}`/`{delta}`/`{result}`/`{error}`。
    【P3-3】主管(P1-1 pipeline 化后)同样可走本端点:stage 事件为主管管线的
    recall/meta_check/schedule/route/plan/dispatch/aggregate;分发场景下子 Agent 的 act
    token 随 contextvar 传播一并流出。
    背压与断连处理与 chat_stream 一致:断连 → stop_event 同时停管线 stage(P3)+ 停 act LLM 生成。"""
    if not _STREAM_LIMITER.allow():
        async def _rl():
            yield "data: " + json.dumps({"error": "rate limited"}) + "\n\n"
        return StreamingResponse(_rl(), media_type="text/event-stream")

    agent = _api.os_instance()._agents.get(req.agent_id)
    # 【P3-3】P1-1 落地后主管走统一 pipeline(supervisor_stages),支持 run_task_stream,
    # 不再回落 assistant(助手已下放为技能)。未知 agent_id 仍回落主管兼容旧客户端
    # (旧前端可能还传 "assistant"/"admin"),但打 warning 不静默。
    if agent is None:
        print(f"[Foxclaw API] ⚠ /api/task/stream 收到未知 agent_id={req.agent_id},回落 supervisor")
        agent = _api.os_instance().supervisor

    async def async_sse():
        if not _STREAM_SEM.acquire(blocking=False):   # 超并发上限 → 拒(不排队)
            yield "data: " + json.dumps({"error": "busy, try again later"}) + "\n\n"
            return
        q: asyncio.Queue = asyncio.Queue()
        stop = _threading.Event()
        loop = asyncio.get_running_loop()

        def _producer():
            try:
                for evt in agent.run_task_stream(req.task, context=req.context,
                                                 stop_event=stop):
                    loop.call_soon_threadsafe(q.put_nowait, evt)
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"error": str(e)})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)   # 结束哨兵

        _threading.Thread(target=_producer, daemon=True, name="task-stream-sse").start()
        try:
            while True:
                if await request.is_disconnected():
                    stop.set(); break                 # 断连 → 停管线 + 停 act LLM
                try:
                    item = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        finally:
            stop.set()
            _STREAM_SEM.release()

    return StreamingResponse(async_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
