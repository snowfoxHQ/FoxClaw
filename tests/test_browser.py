"""
浏览器子系统(HIR-Browser 融入)测试 —— 全离线,不启动真实 chromium。

覆盖:
  1. 核心感知/校验逻辑(FakeDriver):reducer 编号稳定 + navigate 判生效 + noop-click 判失效
  2. 动作类型校验:非法 type 抛错、from_dict 透传
  3. BrowserService async 包装(注入假 session):perceive/act 经专用工作线程返回结构化结果
  4. 可选依赖降级:available()=False 时 perceive/act 返回 available=False 而非崩溃
  5. close() 幂等 + 关停后再调用被拒

真实浏览器端到端(需 playwright install chromium)不在回归里跑,避免 CI 依赖内核二进制;
本测试证明的是"融合层的线程/降级/契约"正确,浏览器操作正确性由 HIR 原生逻辑保证。
"""
import sys, asyncio
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.browser import BrowserService
from foxclaw.browser.perception.dom_extractor import DomExtractor
from foxclaw.browser.perception.element_indexer import ElementIndexer
from foxclaw.browser.perception.semantic_reducer import SemanticReducer
from foxclaw.browser.action.action_verifier import ActionVerifier
from foxclaw.browser.action.action_types import WebAction, SUPPORTED_ACTIONS


class FakeDriver:
    """模拟一个只有一个按钮的页面,可切换 url/title 模拟导航。"""
    def __init__(self):
        self.url = "https://fake.test/p1"; self.title = "P1"; self._e = ["h1"]
    def query_interactive_elements(self, roles): return self._e
    def describe_element(self, h): return {"role": "button", "text": "提交", "attrs": {}}
    def get_visible_text(self): return "fake body"
    def current_url(self): return self.url
    def current_title(self): return self.title


class FakeSnapshot:
    def to_dict(self): return {"url": "https://fake.test/p1", "title": "P1", "elements": [], "text_summary": ""}


class FakeSession:
    """假会话:不碰 playwright,验证 Service 的线程/契约。"""
    def __init__(self): self.closed = False
    def snapshot(self): return FakeSnapshot()
    def execute(self, action: WebAction):
        return {"raw": {"navigated_to": action.target}, "semantic_state": FakeSnapshot().to_dict(),
                "verified": {"success": True, "url_changed": True, "diff": {}}}
    def close(self): self.closed = True


def test_core_logic():
    print("\n【1】核心感知/校验逻辑(FakeDriver)")
    d = FakeDriver()
    reducer = SemanticReducer(DomExtractor(d), ElementIndexer())
    snap = reducer.reduce()
    assert snap.elements[0]["id"] == "1" and snap.elements[0]["role"] == "button"
    print(f"  ✓ reducer 分配编号: id={snap.elements[0]['id']} role={snap.elements[0]['role']}")

    before = reducer.reduce()
    d.url = "https://fake.test/p2"; d.title = "P2"
    after = reducer.reduce()
    v = ActionVerifier()
    nav = v.verify(before, after, WebAction(type="navigate", target="https://fake.test/p2"))
    assert nav["success"] and nav["url_changed"]
    print("  ✓ navigate 判定生效(url 变化)")

    noop = v.verify(reducer.reduce(), reducer.reduce(), WebAction(type="click", target="1"))
    assert noop["success"] is False
    print("  ✓ 页面无变化的 click 判定为未生效(抓静默失败)")


def test_action_types():
    print("\n【2】动作类型契约")
    assert set(SUPPORTED_ACTIONS) == {"navigate", "click", "type", "scroll", "extract", "wait"}
    raised = False
    try:
        WebAction(type="explode")
    except ValueError:
        raised = True
    assert raised, "非法动作类型必须抛 ValueError"
    a = WebAction.from_dict({"type": "type", "target": "3", "value": "hi", "meta": {"x": 1}})
    assert a.type == "type" and a.target == "3" and a.value == "hi" and a.meta == {"x": 1}
    print("  ✓ 非法 type 抛错 + from_dict 透传 target/value/meta")


@pytest.mark.anyio
async def test_service_wrapper():
    print("\n【3】BrowserService async 包装(注入假 session,经专用工作线程)")
    svc = BrowserService()
    fake = FakeSession()
    svc._session = fake
    svc.available = lambda: True

    p = await svc.perceive()
    assert p["ok"] and p["semantic_state"]["title"] == "P1"
    print(f"  ✓ perceive() 经工作线程返回语义结构: title={p['semantic_state']['title']}")

    r = await svc.act({"type": "navigate", "target": "https://fake.test/p2"})
    assert r["ok"] and r["verified"]["success"]
    print("  ✓ act(navigate) 返回校验后结果 verified.success=True")

    bad = await svc.act({"target": "3"})
    assert bad["ok"] is False and "非法动作" in bad["error"]
    print("  ✓ 非法动作(缺 type)被结构化拒绝")

    await svc.close()
    assert fake.closed, "close() 必须在工作线程上释放会话"
    print("  ✓ close() 释放了会话")
    await svc.close()
    after_close = await svc.perceive()
    assert after_close["ok"] is False
    print("  ✓ close() 幂等 + 关停后调用被安全拒绝")


@pytest.mark.anyio
async def test_degradation():
    print("\n【4】可选依赖降级(available=False)")
    svc = BrowserService()
    svc.available = staticmethod(lambda: False)
    p = await svc.perceive()
    assert p["ok"] is False and p["available"] is False and "playwright" in p["error"]
    a = await svc.act({"type": "navigate", "target": "x"})
    assert a["ok"] is False and a["available"] is False
    print("  ✓ playwright 缺失时 perceive/act 结构化降级,不崩溃")
    st = svc.status()
    assert set(["available", "session_active", "closed"]).issubset(st.keys())
    print(f"  ✓ status() 暴露能力: {st}")


def main():
    print("=" * 60)
    print("浏览器子系统 · HIR-Browser 融入(全离线)")
    print("=" * 60)
    test_core_logic()
    test_action_types()
    asyncio.run(test_service_wrapper())
    asyncio.run(test_degradation())
    print(f"\n  (真实浏览器可用性: playwright {'已装' if BrowserService.available() else '未装'};"
          f" 真实 e2e 需 playwright install chromium,不在回归内)")
    print("\n" + "=" * 60)
    print("✓ 浏览器子系统验证完成:核心逻辑 + async线程包装 + 降级 + 幂等close")
    print("=" * 60)


if __name__ == "__main__":
    main()
