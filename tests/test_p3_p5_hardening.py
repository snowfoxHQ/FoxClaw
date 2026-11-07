"""批次 7 · P3 收尾 + P4/P5 小项测试

  [1] P3-1:_constant_time_eq 行为(等/不等/空/None 兜底)
  [2] P3-8:_is_guarded_path 行为
  [3] P3-1+P3-8 接线防退化:两个中间件 + WS 端点统一走辅助函数(源码级)
  [4] P3-7:knowledge_ability 异常分类——isinstance 精确 / 真 HKC 异常证据 gate / 回退鸭子判断
  [5] P3-5:blocked 任务只留审计 trace + truth.skipped,不发 truth.appended、不沉淀(含正常对照)
  [6] P3-0:真值沉淀 agent_id="system" + origin tag 追溯
  [7] P3-6/P4-1:SystemBus exact 订阅/前缀默认/history exact + TruthPipeline 不被前缀事件干扰
  [8] P5-3:ProcessManager._transition 终态守卫(幂等忽略不重复 -1;非终态非法跳转仍抛)
  [9] P5-1:PermissionDecision.reason_code 五码分类 + 非 ask 留空 + to_dict 携带
"""
import asyncio
import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

FAILS = []


def check(cond, label):
    if cond:
        print(f"  ✓ {label}")
    else:
        FAILS.append(label)
        print(f"  ✗ {label}")


# 测试环境自洽:不读 .env,不打外部 API,不写真实数据目录(在任何 foxclaw import 之前)
os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_b7_")
for _k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
           "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "FOXCLAW_API_KEY"):
    os.environ[_k] = ""


# ═══════════════ [1][2] P3-1 / P3-8 辅助函数行为 ═══════════════
def test_helpers():
    print("\n【1】P3-1:_constant_time_eq 常量时间比较")
    from foxclaw.api import _constant_time_eq, _is_guarded_path
    check(_constant_time_eq("abc", "abc") is True, "相同字符串相等")
    check(_constant_time_eq("abc", "abd") is False, "末位不同不等")
    check(_constant_time_eq("abc", "abcd") is False, "长度不同不等")
    check(_constant_time_eq("", "") is True, "双空相等")
    check(_constant_time_eq("", "abc") is False, "空与非空不等")
    check(_constant_time_eq(None, "abc") is False, "None 由 or '' 兜底(不崩)")
    check(_constant_time_eq("密钥含中文", "密钥含中文") is True,
          "非 ASCII(utf-8 编码后比较)可用")

    print("\n【2】P3-8:_is_guarded_path 受保护路径判断")
    check(_is_guarded_path("/api/status") is True, "/api/* 受保护")
    check(_is_guarded_path("/knowledge/graph") is True, "/knowledge/* 受保护")
    check(_is_guarded_path("/health") is False, "/health 探针不受保护")
    check(_is_guarded_path("/") is False, "/ 前端不受保护")
    check(_is_guarded_path("/docs") is False, "/docs 不受保护")


# ═══════════════ [3] P3-1+P3-8 接线防退化(源码级) ═══════════════
def test_wiring_source():
    print("\n【3】P3-1+P3-8 接线防退化:中间件/WS 统一走辅助函数(源码级断言)")
    import foxclaw.api as api_mod

    src_guard = inspect.getsource(api_mod._api_key_guard)
    check("_is_guarded_path(path)" in src_guard, "_api_key_guard 用 _is_guarded_path")
    check("_constant_time_eq(" in src_guard, "_api_key_guard 用 _constant_time_eq")
    check('startswith("/api")' not in src_guard, "_api_key_guard 无残留 startswith 直写")

    src_gw = inspect.getsource(api_mod._gateway_mw)
    check("_is_guarded_path(path)" in src_gw, "_gateway_mw 用 _is_guarded_path")
    check("_constant_time_eq(hdr, valid)" in src_gw, "_gateway_mw 客户识别用常量时间比较")
    check("hdr == valid" not in src_gw, "_gateway_mw 无残留普通 ==")

    src_trace = inspect.getsource(api_mod._trace_mw)
    check("_is_guarded_path(path)" in src_trace, "_trace_mw 用 _is_guarded_path")

    src_ws = inspect.getsource(api_mod.harness_websocket)
    check("_constant_time_eq(" in src_ws, "WS 端点鉴权用 _constant_time_eq")
    check("provided != api_key" not in src_ws, "WS 端点无残留 !=")


# ═══════════════ [4] P3-7 isinstance 异常分类 ═══════════════
def _run_ability_with(exc):
    """用抛指定异常的假 HKC 驱动 knowledge_ability 端点,返回 (status, body)。"""
    import foxclaw.api as api_mod
    from types import SimpleNamespace

    class _Client:
        def compile_ability(self, key):
            raise exc

    fake_hkc = SimpleNamespace(available=True, client=_Client())
    old_os = api_mod._os_instance
    api_mod._os_instance = SimpleNamespace(hkc=fake_hkc)
    try:
        try:
            body = asyncio.run(api_mod.knowledge_ability("any_key"))
            return 200, body
        except Exception as e:
            from fastapi import HTTPException
            if isinstance(e, HTTPException):
                return e.status_code, {"detail": e.detail}
            raise
    finally:
        api_mod._os_instance = old_os


def test_ability_isinstance():
    print("\n【4】P3-7:knowledge_ability 异常分类(isinstance 精确 + 回退)")
    import foxclaw.api as api_mod

    check(api_mod._HKCInsuffCov is not None, "vendor 下 hkc_sdk.exceptions 可 import")

    # a) 真 HKCInsufficientCoverage → 证据 gate(sufficient=False,非 404)
    from hkc_sdk.exceptions import HKCInsufficientCoverage
    try:
        raise HKCInsufficientCoverage("覆盖不足测试")
    except HKCInsufficientCoverage as e:
        # 尽量补上端点要读的属性(不同版本构造签名可能不同)
        if not hasattr(e, "missing_skills"):
            e.missing_skills = ["s1"]
        if not hasattr(e, "coverage"):
            e.coverage = {"a": 0.5}
        status, body = _run_ability_with(e)
    check(status == 200 and body.get("sufficient") is False,
          f"真 HKC 覆盖不足 → 证据 gate(实为 {status}: {str(body)[:80]})")

    # b) 【精确化核心价值】带 missing_skills 属性的**非 HKC** 异常 → 404,不被误吞
    class _FakeInsuff(Exception):
        def __init__(self):
            super().__init__("鸭子异常")
            self.missing_skills = ["s1"]
            self.coverage = {}

    status, body = _run_ability_with(_FakeInsuff())
    check(status == 404,
          f"isinstance 模式:鸭子异常不再误判为覆盖不足(实为 {status})")

    # c) 回退:_HKCInsuffCov=None(模拟缺 vendor)时 hasattr 鸭子判断仍工作
    old = api_mod._HKCInsuffCov
    api_mod._HKCInsuffCov = None
    try:
        status, body = _run_ability_with(_FakeInsuff())
        check(status == 200 and body.get("sufficient") is False,
              f"import 失败回退 hasattr → 证据 gate(实为 {status})")
        status2, _ = _run_ability_with(RuntimeError("普通错误"))
        check(status2 == 404, "回退模式下普通异常仍 404")
    finally:
        api_mod._HKCInsuffCov = old


# ═══════════════ [5][6] P3-5 blocked 短路 + P3-0 system 归因 ═══════════════
class _MockHMR:
    def __init__(self):
        self.calls = []

    def ingest(self, content, **kw):
        self.calls.append((content, kw))


def _make_pipeline():
    from foxclaw.core.system_bus import SystemBus
    from foxclaw.core.truth_pipeline import TruthPipeline
    bus = SystemBus()
    events = []
    for ch in ("trace.recorded", "truth.appended", "truth.skipped", "memory.updated"):
        bus.subscribe(ch, lambda m, _c=ch: events.append(_c))
    hmr = _MockHMR()
    TruthPipeline(bus, hmr=hmr)
    return bus, events, hmr


def test_truth_blocked_skipped():
    print("\n【5】P3-5:blocked 任务不进真值链")
    bus, events, hmr = _make_pipeline()
    bus.publish("execution.completed", {"agent_id": "finance", "task": "危险操作",
                                        "result": "x", "blocked": True})
    check("trace.recorded" in events, "blocked 仍发 trace.recorded(审计留痕)")
    check("truth.skipped" in events, "blocked 发 truth.skipped(可观测)")
    check("truth.appended" not in events, "blocked 不发 truth.appended(不进真值链)")
    check("memory.updated" not in events, "blocked 不发 memory.updated")
    check(hmr.calls == [], "blocked 不沉淀 HMR")

    print("  —— 正常任务对照 ——")
    bus2, events2, hmr2 = _make_pipeline()
    bus2.publish("execution.completed", {"agent_id": "finance", "task": "正常任务",
                                         "result": "ok", "blocked": False})
    check("truth.appended" in events2, "正常任务发 truth.appended")
    check("truth.skipped" not in events2, "正常任务不发 truth.skipped")
    check(len(hmr2.calls) == 1, "正常任务沉淀 HMR ×1")


def test_truth_system_attribution():
    print("\n【6】P3-0:真值沉淀系统级归因")
    bus, events, hmr = _make_pipeline()
    bus.publish("execution.completed", {"agent_id": "finance", "task": "预算分析",
                                        "result": "正常", "blocked": False})
    check(len(hmr.calls) == 1, "真值沉淀 1 次")
    kw = hmr.calls[0][1]
    check(kw.get("agent_id") == "system",
          f"写共享层 agent_id=system(实为 {kw.get('agent_id')!r})")
    check("origin:finance" in (kw.get("tags") or []),
          "来源 Agent 保留在 origin tag")
    check("truth_pipeline" in (kw.get("tags") or []),
          "truth_pipeline facet 保留(策略豁免通道)")
    check(kw.get("shared") is True, "仍写共享层")


# ═══════════════ [7] P3-6/P4-1 SystemBus exact ═══════════════
def test_system_bus_exact():
    print("\n【7】P3-6/P4-1:SystemBus exact 参数")
    from foxclaw.core.system_bus import SystemBus

    bus = SystemBus()
    received = []
    bus.subscribe("execution.completed", lambda m: received.append(m.topic), exact=True)
    bus.publish("execution.completed", {})
    bus.publish("execution.completed.extra", {})
    bus.publish("execution.failed", {})
    check(received == ["execution.completed"],
          f"exact=True 只收精确 topic(实为 {received})")

    bus2 = SystemBus()
    received2 = []
    bus2.subscribe("execution", lambda m: received2.append(m.topic))
    bus2.publish("execution.completed", {})
    bus2.publish("execution.failed", {})
    check(set(received2) == {"execution.completed", "execution.failed"},
          "默认前缀匹配(行为不变)")

    bus3 = SystemBus()
    bus3.publish("execution.completed", {})
    bus3.publish("execution.completed.extra", {})
    bus3.publish("execution.failed", {})
    h = bus3.history("execution.completed", exact=True)
    topics = [(e.get("topic") or e.get("channel")) for e in h]
    check(topics == ["execution.completed"],
          f"history exact=True 只回精确项(实为 {topics})")
    h2 = bus3.history("execution.completed")
    check(len(h2) == 2, "history 默认前缀匹配(含 .extra)")

    print("  —— TruthPipeline 抗前缀干扰 ——")
    bus4, events4, hmr4 = _make_pipeline()
    bus4.publish("execution.completed.extra", {"task": "x", "result": "y"})
    bus4.publish("execution.completed", {"agent_id": "t", "task": "t",
                                         "result": "ok", "blocked": False})
    check(len(hmr4.calls) == 1,
          f"前缀事件 execution.completed.extra 不触发真值链(沉淀 {len(hmr4.calls)} 次)")


# ═══════════════ [8] P5-3 _transition 终态守卫 ═══════════════
def test_transition_terminal_guard():
    print("\n【8】P5-3:ProcessManager._transition 终态守卫")
    from foxclaw.kernel.process_manager import ProcessManager
    pm = ProcessManager()
    rec = pm.create("t1", agent_id="test")
    pm.mark_running(rec.process_id)
    pm.mark_completed(rec.process_id)
    n = pm.active_count()
    # 终态再 _transition(模拟未来新增的直接调用点)→ 幂等忽略
    pm._transition(rec, "failed")
    check(rec.state == "completed", "终态再流转 → 状态不变")
    check(pm.active_count() == n, "终态再流转 → 活跃计数不重复 -1")
    check(rec.state_history.count("completed") == 1, "终态再流转 → 历史不追加")
    # 非终态的非法跳转仍 fail-fast(行为不变)
    rec2 = pm.create("t2")
    rec2.state = "created"
    try:
        pm._transition(rec2, "completed")
        check(False, "非终态非法跳转应仍抛 ValueError")
    except ValueError:
        check(True, "非终态非法跳转(created→completed)仍抛 ValueError")


# ═══════════════ [9] P5-1 reason_code ═══════════════
def test_reason_code():
    print("\n【9】P5-1:PermissionDecision.reason_code 分类")
    from foxclaw.skills import SkillSpec
    from foxclaw.skills.permission import (
        evaluate, PermissionRules, MISSING_PARAMS, DESTRUCTIVE_CONFIRM,
        SIDE_EFFECT_CONFIRM, ASK_RULE, EXTERNAL_CONFIRM)

    def _mk(**kw):
        base = dict(id="s1", name="s", description="d", kind="local",
                    side_effect="read", handler=lambda: {})
        base.update(kw)
        return SkillSpec(**base)

    d = evaluate(_mk(params={"q": {"required": True}}), {})
    check(d.decision == "ask" and d.reason_code == MISSING_PARAMS,
          f"缺必填参数 → ask + MISSING_PARAMS(实为 {d.reason_code!r})")

    d = evaluate(_mk(destructive=True), {})
    check(d.decision == "ask" and d.reason_code == DESTRUCTIVE_CONFIRM,
          f"destructive → DESTRUCTIVE_CONFIRM(实为 {d.reason_code!r})")

    d = evaluate(_mk(side_effect="write"), {})
    check(d.decision == "ask" and d.reason_code == SIDE_EFFECT_CONFIRM,
          f"write 副作用 → SIDE_EFFECT_CONFIRM(实为 {d.reason_code!r})")

    d = evaluate(_mk(), {}, rules=PermissionRules(ask=["s1"]))
    check(d.decision == "ask" and d.reason_code == ASK_RULE,
          f"命中 ask 规则 → ASK_RULE(实为 {d.reason_code!r})")

    d = evaluate(_mk(external=True), {})
    check(d.decision == "ask" and d.reason_code == EXTERNAL_CONFIRM,
          f"外部调用 → EXTERNAL_CONFIRM(实为 {d.reason_code!r})")

    d = evaluate(_mk(), {})
    check(d.decision == "allow" and d.reason_code == "",
          "allow 侧 reason_code 留空(文稿未定义,不扩大枚举面)")
    d = evaluate(_mk(), {}, rules=PermissionRules(deny=["s1"]))
    check(d.decision == "deny" and d.reason_code == "",
          "deny 侧 reason_code 留空(deny 语义已由 decision+stage 表达)")
    d = evaluate(_mk(params={"q": {"required": True}}), {})
    check(d.to_dict().get("reason_code") == MISSING_PARAMS, "to_dict 携带 reason_code")


if __name__ == "__main__":
    test_helpers()
    test_wiring_source()
    test_ability_isinstance()
    test_truth_blocked_skipped()
    test_truth_system_attribution()
    test_system_bus_exact()
    test_transition_terminal_guard()
    test_reason_code()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"✗ 批次7 共 {len(FAILS)} 项失败:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("✓ 批次7 P3 收尾 + P4/P5 小项全部通过")
