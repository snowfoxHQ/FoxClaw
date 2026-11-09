"""
bench/cd_audit.py
C+D 阶段 5 项 Risk 审核(commit 0bf4349):
  D1. IngestJobRegistry(max_workers=4) 真起 4 thread 并发
  D2. Extractor 16 chunk × 4 worker + 50% 429 重试 (限流雪崩测试)
  D3. progress_cb 线程安全:done 计数无重复无漏
  D4. _retry_llm max_retries 可调 (验证配置生效)
  D5. retry 不破坏 progress_cb 顺序
"""
import os, sys, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vendor'))

from hkc.hkc_kde.extractor import Extractor
from hkc.hkc_api.ingest_jobs import IngestJobRegistry


def d1_ingest_4_workers():
    """D1. 4 worker 真并发 peak=4,4 任务 ~0.3s 完成"""
    reg = IngestJobRegistry(max_workers=4)
    running = [0]
    peak = [0]
    lock = threading.Lock()

    def work(progress):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.3)
        with lock:
            running[0] -= 1
        return []

    jobs = [reg.create(f"f{i}") for i in range(4)]
    t0 = time.time()
    for j in jobs:
        reg.submit(j, work)
    while True:
        statuses = [reg.get(j.job_id)["status"] for j in jobs]
        if all(s == "done" for s in statuses):
            break
        time.sleep(0.02)
    elapsed = time.time() - t0
    reg.shutdown()
    assert peak[0] == 4, f"expected peak=4, got {peak[0]}"
    assert elapsed < 0.5, f"expected < 0.5s (并行), got {elapsed:.2f}s"
    print(f"  D1 PASS: peak={peak[0]}, 4 jobs in {elapsed*1000:.0f}ms (串行需 1.2s)")


def d2_429_snowball():
    """D2. 16 chunk × 4 worker × 100% 429 永不成功,验证不死循环 + 总调用有上界"""
    state = {"calls": 0}

    def always_429(*args, **kwargs):
        state["calls"] += 1
        err = Exception("rate limited")
        err.status_code = 429
        raise err

    ext = Extractor(provider="anthropic", model="m")
    import hkc.hkc_kde.extractor as ex_mod
    orig_sleep = ex_mod.time.sleep
    sleeps = []
    ex_mod.time.sleep = lambda s: sleeps.append(s)
    try:
        def mock_chunk(chunk, timeout=60.0):
            try:
                return ext._retry_llm(always_429, base_delay=0.001, max_delay=0.01)
            except Exception:
                return None
        ext._extract_chunk = mock_chunk

        class MockChunk:
            def __init__(self, i):
                self.chunk_id = f"c{i}"
                self.doc_id = "d0"
                self.content = "x"
                self.source_hint = ""

        chunks = [MockChunk(i) for i in range(16)]
        t0 = time.time()
        result = ext.extract(chunks)
        elapsed = time.time() - t0

        success = sum(1 for item in result.items if item is not None)
        assert success == 0, f"expected 0 success, got {success}"
        assert state["calls"] == 64, f"snowball: expected 64 calls, got {state['calls']}"
        assert len(sleeps) == 48, f"expected 48 sleeps (16 chunk * 3 retry), got {len(sleeps)}"
        assert elapsed < 3.0, f"deadlock: {elapsed:.1f}s"
        print(f"  D2 PASS: 16 chunk x 4 worker x 100% 429 -> {state['calls']} calls, {len(sleeps)} sleeps, {elapsed*1000:.0f}ms (no deadlock)")
    finally:
        ex_mod.time.sleep = orig_sleep


def d3_progress_cb_thread_safe():
    """D3. progress_cb 在 as_completed 循环调,主线程串行;16 chunk 计数无重复无漏"""
    ext = Extractor(provider="anthropic", model="m")

    import random
    random.seed(42)
    def mock_extract(chunk, timeout=60.0):
        from hkc.hkc_kde.models import RawExtraction
        return RawExtraction()
    ext._extract_chunk = mock_extract

    class MockChunk:
        def __init__(self, i):
            self.chunk_id = f"c{i}"
            self.doc_id = "d0"
            self.content = "x"
            self.source_hint = ""

    done_calls = []
    lock = threading.Lock()

    def cb(processed, total):
        with lock:
            done_calls.append(processed)

    chunks = [MockChunk(i) for i in range(16)]
    result = ext.extract(chunks, progress_cb=cb)

    assert len(done_calls) == 16, f"expected 16 callbacks, got {len(done_calls)}"
    assert sorted(done_calls) == list(range(1, 17)), \
        f"expected processed 1..16, got {sorted(done_calls)}"
    print(f"  D3 PASS: {len(done_calls)} callbacks, all 1..16 unique")


def d4_max_retries_configurable():
    """D4. _retry_llm 的 max_retries 真的生效"""
    ext = Extractor(provider="mock", model="m")

    call_count = [0]
    def always_fail():
        call_count[0] += 1
        err = Exception("rate")
        err.status_code = 429
        raise err

    import hkc.hkc_kde.extractor as ex_mod
    orig_sleep = ex_mod.time.sleep
    sleeps = []
    ex_mod.time.sleep = lambda s: sleeps.append(s)
    try:
        try:
            ext._retry_llm(always_fail, max_retries=2, base_delay=0.001)
            assert False, "expected raise"
        except Exception:
            pass
        assert call_count[0] == 3, f"max_retries=2 expected 3 calls, got {call_count[0]}"
        assert len(sleeps) == 2, f"max_retries=2 expected 2 sleeps, got {len(sleeps)}"
        print(f"  D4 PASS: max_retries=2 -> 3 calls, 2 sleeps")
    finally:
        ex_mod.time.sleep = orig_sleep


def d5_retry_no_progress_leak():
    """D5. retry 期间 progress_cb 仍只调 1 次/chunk(不管 retry 几次)"""
    import time as time_mod
    orig_sleep = time_mod.sleep
    time_mod.sleep = lambda s: None

    state = {"calls": 0}
    def always_429(*args, **kwargs):
        state["calls"] += 1
        err = Exception("rate"); err.status_code = 429
        raise err

    class MockResp:
        def __init__(self, t): self.content = [MockText(t)]
    class MockText:
        def __init__(self, t): self.text = t

    class MockClient:
        @property
        def messages(self): return self
        def create(self, *args, **kwargs): return always_429(*args, **kwargs)

    ext = Extractor(provider="anthropic", model="m")
    ext._client = MockClient()
    try:
        class MockChunk:
            def __init__(self, i):
                self.chunk_id=f"c{i}"; self.doc_id="d0"; self.content="x"; self.source_hint=""
        cb_count = [0]
        cb_lock = threading.Lock()
        def cb(p, t):
            with cb_lock:
                cb_count[0] += 1
        chunks = [MockChunk(i) for i in range(8)]
        result = ext.extract(chunks, progress_cb=cb)
        assert cb_count[0] == 8, f"expected 8 progress, got {cb_count[0]}"
        assert state["calls"] == 32, f"expected 32 calls, got {state['calls']}"
        success = sum(1 for item in result.items if item is not None)
        assert success == 0
        print(f"  D5 PASS: 8 chunks x 4 attempts, progress_cb={cb_count[0]} (1 per chunk), calls={state['calls']}")
    finally:
        time_mod.sleep = orig_sleep


def main():
    print("=" * 60)
    print("C+D 阶段 5 项 Risk 审核 (commit 0bf4349)")
    print("=" * 60)
    d1_ingest_4_workers()
    d2_429_snowball()
    d3_progress_cb_thread_safe()
    d4_max_retries_configurable()
    d5_retry_no_progress_leak()
    print("=" * 60)
    print("5/5 PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
