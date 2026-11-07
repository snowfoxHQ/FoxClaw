"""请求链路追踪:一个 trace_id 贯穿 请求→Agent→LLM,带 span 耗时;有界环存;无 trace 零成本。
与 METRICS(聚合)分工:traces 回答"**这一次**慢在哪、调了谁"。全离线(stub)。"""
import os, sys, time, json, threading, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_tracing():
    # 关掉 .env:否则开发者 .env 的 FOXCLAW_MCP_SERVER=1 会在 import api 时构建 MCP 服务器,
    # 而本测试有**两处** `with TestClient(app)`(82/189 行),第二次进 lifespan 会撞
    # StreamableHTTPSessionManager「.run() can only be called once per instance」而失败。
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_trace_")
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        os.environ.pop(k, None)

    from foxclaw.core.tracing import (Trace, TraceStore, start_trace, current_trace,
                                     clear_trace, span, MAX_SPANS_PER_TRACE)

    print("=" * 60); print("请求链路追踪验证"); print("=" * 60)

    # [1] 无 trace 时 span 是零成本 no-op(后台任务不该报错)
    clear_trace()
    assert current_trace() is None
    with span("orphan", x=1) as s:
        assert s is None
    print("[1] 无 trace 时 span no-op(不报错/不产生垃圾)OK")

    # [2] span 记录名称/耗时/meta;嵌套按结束顺序落盘
    tr = start_trace("unit")
    with span("outer", k="v"):
        with span("inner"):
            time.sleep(0.01)
    d = tr.to_dict()
    names = [s["name"] for s in d["spans"]]
    assert names == ["inner", "outer"], names          # inner 先结束先记
    inner = d["spans"][0]
    assert inner["ok"] is True and inner["ms"] >= 9, inner
    assert d["spans"][1]["meta"] == {"k": "v"}
    print(f"[2] span 记录(嵌套/耗时 {inner['ms']}ms/meta)OK")

    # [3] 异常:记 ok=False + error,并原样抛出(不吞异常)
    try:
        with span("boom"):
            raise ValueError("kaboom")
        raise AssertionError("异常应原样抛出")
    except ValueError:
        pass
    bad = [s for s in tr.to_dict()["spans"] if s["name"] == "boom"][0]
    assert bad["ok"] is False and "kaboom" in bad["error"], bad
    print("[3] span 异常记录 ok=False + 原样抛出 OK")

    # [4] 单 trace span 上限(防病态循环撑爆内存)
    t2 = Trace("cap")
    for i in range(MAX_SPANS_PER_TRACE + 50):
        t2.add_span({"name": f"s{i}"})
    assert len(t2.to_dict()["spans"]) == MAX_SPANS_PER_TRACE and t2.to_dict()["dropped_spans"] == 50
    print(f"[4] 单 trace span 封顶 {MAX_SPANS_PER_TRACE}(超出计 dropped)OK")

    # [5] TraceStore 有界环存 + 按 id 取
    st = TraceStore(limit=3)
    ids = []
    for i in range(5):
        t = Trace(f"t{i}"); t.finish(); st.put(t); ids.append(t.trace_id)
    assert len(st.list(limit=99)) == 3, "环存未封顶"
    assert st.get(ids[-1]) is not None and st.get(ids[0]) is None, "应只留最近 3 条"
    print("[5] TraceStore 有界环存(只留最近 N)+ 按 id 取 OK")

    # [6] 线程隔离:contextvars 各线程独立,span 不串 trace
    clear_trace()
    res = {}
    def worker(name):
        t = start_trace(name)
        with span("work"):
            time.sleep(0.01)
        res[name] = (t.trace_id, [s["name"] for s in t.to_dict()["spans"]])
    ths = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(3)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert len(res) == 3 and len({v[0] for v in res.values()}) == 3, res
    assert all(v[1] == ["work"] for v in res.values()), "各线程 span 应各归各的 trace"
    print("[6] 线程隔离(3 线程 3 条独立 trace,span 不串)OK")

    # ── 端到端:经 API 请求产生 trace ─────────────────────────
    from fastapi.testclient import TestClient
    from foxclaw.api import app

    with TestClient(app) as c:
        # [7] 数据面请求 → 带 X-Trace-Id + 可查详情;链路里能看到 LLM span
        #     (用 office:主管走路由管线,不经过 act/verify,埋点覆盖要用领域 Agent 验)
        r = c.post("/api/task", json={"task": "追踪自测", "agent_id": "office"})
        assert r.status_code == 200, r.status_code
        tid = r.headers.get("X-Trace-Id")
        assert tid, "响应应带 X-Trace-Id"
        detail = c.get(f"/api/traces/{tid}").json()
        assert detail["trace_id"] == tid and detail["duration_ms"] > 0, detail
        names = [s["name"] for s in detail["spans"]]
        llm = [n for n in names if n.startswith("llm.")]
        assert llm, f"链路里应有 LLM span: {names}"
        # 跨层传播:Agent 跑在 asyncio.to_thread 的线程里,它的 span 仍落进本请求的 trace
        # → 证明 contextvars 确实穿过了线程边界(本设计的核心假设)
        mem = [n for n in names if n.startswith("memory.")]
        assert mem, f"跨线程的 Agent 记忆 span 应归属本 trace(contextvars 传播): {names}"
        assert all(s["ms"] >= 0 for s in detail["spans"]), "每个 span 都应有耗时"
        # 覆盖度:认知管线各 stage 都要在链路里(否则"慢在哪"会落在没埋点的暗区)
        stages = [n for n in names if n.startswith("stage.")]
        for must in ("stage.recall", "stage.act", "stage.verify"):
            assert must in names, f"缺 {must},埋点覆盖退化了: {names}"
        assert len(detail["spans"]) >= 6, f"链路过稀(疑似埋点退化): {names}"
        print(f"[7] 请求→trace + X-Trace-Id;{detail['span_count']} span "
              f"(管线 {len(stages)} stage + 跨线程 {mem[0]})OK")

        # [8] 列表可查;未知 id → 404
        lst = c.get("/api/traces?limit=5").json()["traces"]
        assert any(t["trace_id"] == tid for t in lst), "最近列表应含刚才那条"
        assert "spans" not in lst[0], "列表是摘要,不带 spans(省负载)"
        assert c.get("/api/traces/deadbeef0000").status_code == 404
        print(f"[8] /api/traces 列表({len(lst)} 条摘要)+ 未知 id 404 OK")

        # [9] 探针/静态不追踪(不污染)
        before = len(c.get("/api/traces?limit=200").json()["traces"])
        c.get("/health"); c.get("/metrics")
        after = c.get("/api/traces?limit=200").json()["traces"]
        assert not any(t["name"].endswith("/health") for t in after), "探针不该被追踪"
        print("[9] 探针 /health、/metrics 不进 trace OK")

    # ── 持久化 + 采样(内存环重启即失;慢的/出错的必须留得住)──────────
    import time as _t
    store = TraceStore(limit=2)
    store.attach_db(Path(os.environ["FOXCLAW_DATA_DIR"]) / "t.db")

    def _mk(name, ms, err=False):
        tr = Trace(name); tr.add_span({"name": "s", "ok": not err, "ms": ms})
        tr.duration_ms = ms; tr.start_ts = _t.time()
        return tr

    # [10] 采样:慢的必存、出错的必存、正常的(采样率 0)不存
    os.environ["FOXCLAW_TRACE_SLOW_MS"] = "500"; os.environ["FOXCLAW_TRACE_SAMPLE"] = "0"
    slow = _mk("GET /slow", 900); fast = _mk("GET /fast", 5); bad = _mk("GET /err", 5, err=True)
    for t in (slow, fast, bad):
        store.put(t)
    ids = {r["trace_id"] for r in store.query(limit=99)}
    assert slow.trace_id in ids, "慢请求必须落库"
    assert bad.trace_id in ids, "出错请求必须落库"
    assert fast.trace_id not in ids, "正常请求(采样率0)不该落库"
    print("[10] 采样策略(慢/出错必存、正常按采样率不存)OK")

    # [11] 内存环滚出后,仍能从库里捞回慢请求 —— 这才是"查昨天那次慢请求"
    for i in range(5):                       # 环 limit=2,把 slow 挤出内存
        store.put(_mk(f"GET /x{i}", 1))
    assert store.get(slow.trace_id) is None, "应已滚出内存环"
    d = store.get_dict(slow.trace_id)
    assert d and d.get("persisted") and d["duration_ms"] == 900 and d["spans"], d
    print("[11] 内存环滚出后仍可从库捞回(重启/滚动后可查历史)OK")

    # [12] 过滤:按慢阈值 / 只看出错
    assert all(r["duration_ms"] >= 500 for r in store.query(limit=99, slow_ms=500))
    errs = store.query(limit=99, errors_only=True)
    assert errs and all(r["ok"] is False for r in errs), errs
    print("[12] 历史查询过滤(slow_ms / errors_only)OK")

    # [13] 保留上限:超出删最旧(磁盘有界)
    os.environ["FOXCLAW_TRACE_RETAIN"] = "100"; os.environ["FOXCLAW_TRACE_SAMPLE"] = "1"
    for i in range(140):
        store.put(_mk(f"GET /bulk{i}", 1))
    assert len(store.query(limit=999)) <= 100, "落库未按保留上限修剪"
    os.environ["FOXCLAW_TRACE_SAMPLE"] = "0"
    print("[13] 落库保留上限(超出删最旧,磁盘有界)OK")

    # [14] 库不可用 → 优雅降级为仅内存(不能因为落库失败就把请求搞挂)
    s2 = TraceStore(limit=2)
    s2.attach_db("/nonexistent-dir-xyz/なし/t.db")
    s2.put(_mk("GET /ok", 900))              # 不应抛异常
    assert s2.query(limit=9) == [] and len(s2.list(limit=9)) == 1
    print("[14] 库不可用 → 降级仅内存,不影响请求 OK")

    # ── W3C Trace Context:零依赖跨进程传播(Foxclaw 唯一的跨进程边界=MCP 双向)──
    from foxclaw.core.tracing import parse_traceparent, to_traceparent, current_traceparent

    # [15] traceparent 解析:合法接受,非法/全零一律拒(不信外部输入)
    good = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert parse_traceparent(good) == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parse_traceparent("00-" + "0" * 32 + "-00f067aa0ba902b7-01") is None, "全零 trace-id 非法"
    for bad_tp in (None, "", "garbage", "00-short-00f067aa0ba902b7-01", "99-" + "a" * 32 + "-" + "b" * 16 + "-01"):
        assert parse_traceparent(bad_tp) is None, bad_tp
    print("[15] traceparent 解析(合法接受/非法+全零拒绝)OK")

    # [16] trace_id 符合 W3C 规格(32 hex)→ 出站 traceparent 可被对端接受
    t16 = Trace("w3c")
    assert len(t16.trace_id) == 32 and all(c in "0123456789abcdef" for c in t16.trace_id), t16.trace_id
    tp = to_traceparent(t16.trace_id)
    assert parse_traceparent(tp) == t16.trace_id, "自己生成的应能被自己解析(自洽)"
    print("[16] trace_id 32hex 合规 + 出站 traceparent 自洽 OK")

    # [17] 入站接链:带 traceparent 的请求应**复用**其 trace_id(两边链路对齐)
    with TestClient(app) as c:
        r = c.post("/api/task", json={"task": "接链自测", "agent_id": "supervisor"},
                   headers={"traceparent": good})
        assert r.headers.get("X-Trace-Id") == "4bf92f3577b34da6a3ce929d0e0e4736", r.headers.get("X-Trace-Id")
        # 非法 traceparent → 不采信,自开一条
        r2 = c.post("/api/task", json={"task": "非法头", "agent_id": "supervisor"},
                    headers={"traceparent": "garbage"})
        assert r2.headers.get("X-Trace-Id") and r2.headers["X-Trace-Id"] != "garbage"
    print("[17] 入站接链(合法 traceparent 复用其 trace_id;非法则自开)OK")

    # [18] 出站:current_traceparent 在 trace 内可用、trace 外为 None(MCP 调用据此带头)
    clear_trace()
    assert current_traceparent() is None
    t18 = start_trace("out")
    assert parse_traceparent(current_traceparent()) == t18.trace_id
    clear_trace()
    print("[18] 出站 current_traceparent(trace 内可用/trace 外 None)OK")

    # ── OTLP/HTTP-JSON 导出(可选、零依赖、绝不阻塞请求)───────────────
    from foxclaw.core.tracing import OTLP, _trace_to_otlp

    # [19] 默认关:未配 endpoint → 完全不启用(零成本)
    os.environ.pop("FOXCLAW_OTLP_ENDPOINT", None)
    assert OTLP.stats()["enabled"] is False
    OTLP.submit(_mk("GET /x", 5))                # 未配 endpoint → no-op,不该起线程/不该报错
    print("[19] 未配 endpoint → OTLP 完全不启用(零成本)OK")

    # [20] OTLP/JSON 结构:根 span + 子 span(parentSpanId 挂根)、traceId 32hex、状态码
    t20 = start_trace("GET /demo")               # 建 trace 并绑到当前上下文
    with span("child.ok"): pass
    try:
        with span("child.bad"):
            raise RuntimeError("x")
    except RuntimeError:
        pass
    t20.finish(); clear_trace()
    o = _trace_to_otlp(t20)
    sp = o["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = sp[0]; kids = sp[1:]
    assert len(root["traceId"]) == 32 and root["name"] == "GET /demo" and root["kind"] == 2
    assert all(k["parentSpanId"] == root["spanId"] for k in kids), "子 span 应挂在根 span 下"
    assert all(k["traceId"] == t20.trace_id for k in kids), "同一 trace 的 span traceId 必须一致"
    assert all(int(k["endTimeUnixNano"]) >= int(k["startTimeUnixNano"]) for k in sp), "结束不得早于开始"
    bad = [k for k in kids if k["name"] == "child.bad"][0]
    assert bad["status"]["code"] == 2 and root["status"]["code"] == 2, "出错应标 ERROR 并冒泡到根"
    ok = [k for k in kids if k["name"] == "child.ok"][0]
    assert ok["status"]["code"] == 1
    svc = o["resourceSpans"][0]["resource"]["attributes"][0]
    assert svc["key"] == "service.name" and svc["value"]["stringValue"] == "foxclaw"
    print(f"[20] OTLP/JSON 结构(根+{len(kids)} 子 span·parentSpanId·32hex traceId·状态码)OK")

    # [21] 端到端:起一个本地 collector,确认真的 POST /v1/traces 且 body 可解析
    import http.server, socketserver, threading as _th2
    received = []
    class _H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            received.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th2.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["FOXCLAW_OTLP_ENDPOINT"] = f"http://127.0.0.1:{port}"
    t21 = _mk("GET /exported", 12)
    OTLP.submit(t21)
    for _ in range(50):                          # 后台线程异步发,等它落地
        if received: break
        _t.sleep(0.1)
    srv.shutdown()
    assert received, "collector 未收到 OTLP 上报"
    path, body = received[0]
    assert path == "/v1/traces", path
    got = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert got["traceId"] == t21.trace_id and got["name"] == "GET /exported", got
    print("[21] 端到端导出(本地 collector 收到 POST /v1/traces,traceId 对得上)OK")

    # [22] 队列满 → 丢弃计数增长,submit 不阻塞(遥测绝不能拖慢请求)
    os.environ["FOXCLAW_OTLP_ENDPOINT"] = "http://127.0.0.1:1"    # 打不通 → worker 卡住,队列会满
    from foxclaw.core.tracing import _OtlpExporter
    ex = _OtlpExporter(maxsize=2)
    before = ex.dropped
    t0_ = _t.perf_counter()
    for i in range(50):
        ex.submit(_mk(f"GET /f{i}", 1))
    elapsed = (_t.perf_counter() - t0_) * 1000
    assert ex.dropped > before, "队列满时应丢弃而非阻塞"
    assert elapsed < 2000, f"submit 不该阻塞调用方(耗时 {elapsed:.0f}ms)"
    os.environ.pop("FOXCLAW_OTLP_ENDPOINT", None)
    print(f"[22] 队列满→丢弃 {ex.dropped} 条且不阻塞(50 次 submit 仅 {elapsed:.0f}ms)OK")

    print("\n请求链路追踪验证全部通过")

if __name__ == "__main__":
    test_tracing()
