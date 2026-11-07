"""
本地大并发压测（真实 uvicorn 服务）

起一个真实的 uvicorn（Foxclaw，stub LLM / 离线），用 httpx 异步客户端打高并发
`POST /api/task`，量:
  · 吞吐(req/s)、延迟分位(p50/p90/p95/p99/max)、错误率;
  · **负载下正确性**:每个请求拿到独立 session_id(无串味)、无 database is locked、无 5xx。

这把 M2 加锁 + 顶层 contextvars 的并发修复放到**真实 HTTP 服务**下验证。
纯本地、离线、零基建。用法:HF_HUB_OFFLINE=1 python bench/load_test.py [总请求数] [并发数]
"""
import asyncio
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PORT = int(os.environ.get("FOXCLAW_BENCH_PORT", "8231"))
BASE = f"http://127.0.0.1:{PORT}"

# 一批覆盖不同分诊路径的任务模板（每个请求附唯一编号,便于校验会话唯一性）
TEMPLATES = [
    "分析本月理财预算与收支",       # finance
    "总结这篇论文的核心要点",       # office
    "安排明天上午的会议日程",       # admin
    "整理并汇总这批资料",           # assistant
    "你能做什么",                   # supervisor 自答(meta)
]


def _pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def _start_server():
    """在后台线程起真实 uvicorn。返回 (server, thread)。"""
    import uvicorn
    os.environ.setdefault("FOXCLAW_DATA_DIR", tempfile.mkdtemp())
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "50"
    # 不配任何 LLM key → Router 自动降级 stub(不联网、瞬时返回)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
              "GOOGLE_API_KEY", "DASHSCOPE_API_KEY"):
        os.environ.pop(k, None)
    config = uvicorn.Config("foxclaw.api:app", host="127.0.0.1", port=PORT,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    return server, th


async def _wait_ready(timeout=120):
    import httpx
    t0 = time.time()
    async with httpx.AsyncClient() as c:
        while time.time() - t0 < timeout:
            try:
                r = await c.get(f"{BASE}/api/status", timeout=2)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return False


async def _run_load(total: int, concurrency: int):
    import httpx
    sem = asyncio.Semaphore(concurrency)
    latencies, statuses, sessions, errors = [], [], [], []
    lock = asyncio.Lock()

    async def one(i: int):
        task = f"{TEMPLATES[i % len(TEMPLATES)]}（REQ{i:05d}）"
        async with sem:
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.post(f"{BASE}/api/task",
                                     json={"task": task, "agent_id": "supervisor"})
                dt = (time.perf_counter() - t0) * 1000
                async with lock:
                    latencies.append(dt)
                    statuses.append(r.status_code)
                    if r.status_code == 200:
                        body = r.json()
                        sid = body.get("session_id")
                        if sid:
                            sessions.append(sid)
                        if isinstance(body, dict) and body.get("error") == "主管处理失败":
                            errors.append(body)
            except Exception as e:
                async with lock:
                    errors.append(repr(e))

    t0 = time.perf_counter()
    await asyncio.gather(*[one(i) for i in range(total)])
    wall = time.perf_counter() - t0
    return {
        "wall_s": wall, "throughput": total / wall if wall else 0,
        "latencies": latencies, "statuses": statuses,
        "sessions": sessions, "errors": errors,
    }


async def _server_log_has_locked():
    """负载后查一次 /api/status 确认服务仍活;database is locked 会体现在 errors/5xx。"""
    import httpx
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{BASE}/api/status", timeout=5)
            return r.status_code == 200
    except Exception:
        return False


async def main_async(total, concurrency):
    print("=" * 62)
    print(f"Foxclaw · 本地大并发压测  (总请求={total} 并发={concurrency})")
    print("=" * 62)
    ok = await _wait_ready()
    if not ok:
        print("✗ 服务未就绪"); return 1
    print("服务就绪。先测单请求基线(concurrency=1)...")

    # 基线:串行 10 个请求,量"单请求真实成本"(无并发争用),用于解释并发下的延迟来源
    base = await _run_load(10, 1)
    base_lat = base["latencies"]
    base_p50 = _pct(base_lat, 50)
    base_tps = base["throughput"]
    print(f"  单请求 p50={base_p50:.0f}ms  串行吞吐={base_tps:.1f} req/s\n")

    print(f"开始并发加载(concurrency={concurrency})...\n")
    res = await _run_load(total, concurrency)
    lat, st = res["latencies"], res["statuses"]
    n2xx = sum(1 for s in st if 200 <= s < 300)
    n5xx = sum(1 for s in st if s >= 500)
    healthy = await _server_log_has_locked()

    print("【吞吐 / 延迟】")
    print(f"  墙钟         : {res['wall_s']:.2f} s")
    print(f"  吞吐         : {res['throughput']:.1f} req/s")
    print(f"  延迟 p50/p90 : {_pct(lat,50):.0f} / {_pct(lat,90):.0f} ms")
    print(f"  延迟 p95/p99 : {_pct(lat,95):.0f} / {_pct(lat,99):.0f} ms")
    print(f"  延迟 max     : {max(lat):.0f} ms" if lat else "  无延迟样本")
    ratio = _pct(lat, 50) / base_p50 if base_p50 else 0
    speedup = base_tps and res["throughput"] / base_tps
    print("\n【规模化分析】")
    print(f"  单请求成本(串行 p50)   : {base_p50:.0f} ms")
    print(f"  并发吞吐 / 串行吞吐     : {speedup:.2f}×  "
          f"（>1 = 请求已部分并行;offload 到线程后嵌入计算并行）")
    print(f"  并发下 p50 / 串行 p50   : {ratio:.1f}×  "
          f"(< 并发数 {concurrency} 越多 → 并行度越高;=并发数则完全串行)")
    print(f"  现状: /api/task 已 offload 到线程,harness 语义嵌入在锁外并行(torch 释放 GIL);")
    print(f"        非线性加速的余量卡在 GIL(Python 侧串行)+ 本机核数。")
    print(f"  进一步提速: ① uvicorn --workers N 多进程(需先做状态外置) ② 更轻的 embedding。")
    print("\n【正确性 · 负载下】")
    print(f"  2xx / 总数   : {n2xx}/{total}   5xx: {n5xx}   异常: {len(res['errors'])}")
    uniq = len(set(res["sessions"]))
    print(f"  session 唯一 : {uniq}/{len(res['sessions'])}  "
          f"→ {'✓ 无串味(顶层并发隔离成立)' if uniq == len(res['sessions']) and uniq > 0 else '✗ 有会话碰撞'}")
    print(f"  服务负载后仍健康: {'✓' if healthy else '✗'}")
    if res["errors"]:
        print(f"  异常样例: {res['errors'][:2]}")

    passed = (n5xx == 0 and not res["errors"] and healthy
              and uniq == len(res["sessions"]) and uniq > 0)
    print("\n" + "=" * 62)
    print("结论:", "✓ 高并发下吞吐可测、零错误、审计无串味" if passed else "✗ 存在问题(见上)")
    print("=" * 62)
    return 0 if passed else 1


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    server, th = _start_server()
    try:
        rc = asyncio.run(main_async(total, concurrency))
    finally:
        server.should_exit = True
        th.join(timeout=10)
    sys.exit(rc)


if __name__ == "__main__":
    main()
