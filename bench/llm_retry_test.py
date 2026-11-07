"""
bench/llm_retry_test.py
C 阶段 _retry_llm helper 单元测试。

覆盖:
  1. 正常调用 1 次成功
  2. 429 抛 1 次后成功
  3. 429 抛 max_retries 次后 raise
  4. 非 retryable 异常立即 raise
  5. Retry-After header 优先于 backoff
  6. 5xx 也 retry
  7. ConnectionError / Timeout retry
  8. 指数退避 (backoff 1→2→4) + max_delay 上限
  9. openai RateLimitError (name 含 RateLimit) 也 retry
"""
import os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vendor'))
from hkc.hkc_kde.extractor import Extractor


# ── Mock 异常类型 ────────────────────────────────────

class FakeResponse:
    def __init__(self, headers=None):
        self.headers = headers or {}


class Fake429Error(Exception):
    def __init__(self, msg="rate limited", retry_after=None):
        super().__init__(msg)
        self.status_code = 429
        self.headers = {"Retry-After": str(retry_after)} if retry_after else {}


class Fake5xxError(Exception):
    def __init__(self, msg="server error", status=500):
        super().__init__(msg)
        self.status_code = status


class FakeTimeoutError(TimeoutError):
    def __init__(self, msg="timed out"):
        super().__init__(msg)
        self.status_code = None


class FakeConnError(ConnectionError):
    pass


class FakeRateLimitName(Exception):
    """模拟 openai.RateLimitError(type name 含 'RateLimit',无 status_code)"""
    def __init__(self, msg="rate"):
        super().__init__(msg)
        # 不设 status_code;靠 type name 'RateLimit' 识别


class FakeValueError(Exception):
    """非 retryable"""
    def __init__(self, msg="bad request"):
        super().__init__(msg)
        self.status_code = 400


# ── 测试实例化 ───────────────────────────────────────
# KnowledgeExtractor __init__ 不调 LLM,只是记 provider/model 配置
# _retry_llm 是 instance method,任意实例都能调
ext = Extractor(provider="mock", model="mock-model")


def make_func(side_effects):
    """side_effects: 异常类或值,依次抛出,最后返回 'ok' 或 raise"""
    call_count = [0]
    sleeps = []

    def _func(*args, **kwargs):
        i = call_count[0]
        call_count[0] += 1
        if i < len(side_effects):
            item = side_effects[i]
            if isinstance(item, Exception):
                raise item
            return item
        return "ok"

    return _func, call_count, sleeps


def patch_sleep():
    """monkey-patch time.sleep 不真睡,记录时长"""
    import hkc.hkc_kde.extractor as ex
    original = ex.time.sleep
    sleeps = []
    def fake_sleep(s):
        sleeps.append(s)
    ex.time.sleep = fake_sleep
    return ex, original, sleeps


def restore_sleep(ex, original):
    ex.time.sleep = original


def t1_ok():
    """1. 正常调用 1 次成功"""
    func, calls, _ = make_func(["ok"])
    r = ext._retry_llm(func, max_retries=3, base_delay=0.01)
    assert r == "ok" and calls[0] == 1
    print("  1. ok 1 次成功: PASS")


def t2_429_retry_success():
    """2. 429 抛 1 次后成功"""
    func, calls, _ = make_func([Fake429Error(), "ok"])
    ex, original, sleeps = patch_sleep()
    try:
        r = ext._retry_llm(func, max_retries=3, base_delay=0.01, max_delay=0.1)
        assert r == "ok" and calls[0] == 2
        assert len(sleeps) == 1 and sleeps[0] == 0.01
        print("  2. 429 1 次后成功 + sleep 0.01: PASS")
    finally:
        restore_sleep(ex, original)


def t3_429_exhaust():
    """3. 429 抛 max_retries+1 次后 raise"""
    func, calls, _ = make_func([Fake429Error()] * 5)
    ex, original, sleeps = patch_sleep()
    try:
        try:
            ext._retry_llm(func, max_retries=3, base_delay=0.001, max_delay=0.01)
            assert False, "应该 raise"
        except Fake429Error:
            pass
        assert calls[0] == 4  # 1 + 3 retries
        assert len(sleeps) == 3  # 3 次 retry 才 sleep
        print("  3. 429 4 次后 raise (1+3 retry): PASS, calls={}, sleeps={}".format(calls[0], sleeps))
    finally:
        restore_sleep(ex, original)


def t4_non_retryable():
    """4. 非 retryable (400) 立即 raise"""
    func, calls, _ = make_func([FakeValueError()])
    ex, original, sleeps = patch_sleep()
    try:
        try:
            ext._retry_llm(func, max_retries=3, base_delay=0.01)
            assert False, "应该 raise"
        except FakeValueError:
            pass
        assert calls[0] == 1
        assert len(sleeps) == 0
        print("  4. 400 立即 raise 不 sleep: PASS")
    finally:
        restore_sleep(ex, original)


def t5_retry_after():
    """5. Retry-After header 优先于 backoff"""
    func, calls, _ = make_func([Fake429Error(retry_after=0.05), "ok"])
    ex, original, sleeps = patch_sleep()
    try:
        r = ext._retry_llm(func, max_retries=3, base_delay=1.0, max_delay=10.0)
        assert r == "ok"
        assert len(sleeps) == 1
        assert sleeps[0] == 0.05, f"expected 0.05, got {sleeps[0]}"
        print("  5. Retry-After=0.05 优先 backoff=1.0: PASS, slept={}".format(sleeps))
    finally:
        restore_sleep(ex, original)


def t6_5xx():
    """6. 5xx (500) retry"""
    func, calls, _ = make_func([Fake5xxError(status=500), Fake5xxError(status=503), "ok"])
    ex, original, sleeps = patch_sleep()
    try:
        r = ext._retry_llm(func, max_retries=3, base_delay=0.01, max_delay=0.1)
        assert r == "ok" and calls[0] == 3
        assert sleeps == [0.01, 0.02]
        print("  6. 5xx 500+503 后成功: PASS, sleeps={}".format(sleeps))
    finally:
        restore_sleep(ex, original)


def t7_conn_timeout():
    """7. ConnectionError / Timeout retry"""
    for cls, name in [(FakeConnError, "ConnectionError"), (FakeTimeoutError, "Timeout")]:
        func, calls, _ = make_func([cls(), "ok"])
        ex, original, sleeps = patch_sleep()
        try:
            r = ext._retry_llm(func, max_retries=3, base_delay=0.01)
            assert r == "ok" and calls[0] == 2
            assert len(sleeps) == 1
            print("  7. {} retry: PASS".format(name))
        finally:
            restore_sleep(ex, original)


def t8_backoff():
    """8. 指数退避 1→2→4 + max_delay 上限"""
    func, calls, _ = make_func([Fake429Error()] * 4)
    ex, original, sleeps = patch_sleep()
    try:
        try:
            ext._retry_llm(func, max_retries=3, base_delay=1.0, max_delay=3.0)
        except Fake429Error:
            pass
        # 3 次 retry:sleeps = [1, 2, 3] (4 cap at max_delay=3)
        assert sleeps == [1.0, 2.0, 3.0], f"expected [1,2,3], got {sleeps}"
        print("  8. 退避 1→2→3 (max_delay cap): PASS, sleeps={}".format(sleeps))
    finally:
        restore_sleep(ex, original)


def t9_rate_limit_name():
    """9. type name 含 'RateLimit' (openai.RateLimitError 风格) 也 retry"""
    func, calls, _ = make_func([FakeRateLimitName(), "ok"])
    ex, original, sleeps = patch_sleep()
    try:
        r = ext._retry_llm(func, max_retries=3, base_delay=0.01)
        assert r == "ok" and calls[0] == 2
        print("  9. RateLimit class name → retry: PASS")
    finally:
        restore_sleep(ex, original)


def main():
    print("=" * 60)
    print("C 阶段 _retry_llm helper 单元测试 (9 项)")
    print("=" * 60)
    t1_ok()
    t2_429_retry_success()
    t3_429_exhaust()
    t4_non_retryable()
    t5_retry_after()
    t6_5xx()
    t7_conn_timeout()
    t8_backoff()
    t9_rate_limit_name()
    print("=" * 60)
    print("全部 9 项 PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
