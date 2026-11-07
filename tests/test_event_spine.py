"""
统一事件脊柱(EventSpine, P0 第二项)测试 —— 全离线。

覆盖:
  1. EventSpine:发布/订阅(前缀匹配) + 投影(收到每条) + 有界历史
  2. SystemBus 门面:退化为脊柱门面后,既有消费者零改;系统事件汇入脊柱
  3. Harness → 脊柱转发:harness 事件汇入脊柱;高频内部信号(goal_registered)不上脊柱
  4. Foxclaw 统一投影:harness 事件与系统事件都被**同一个投影**观测(核心统一验证)

为避免加载 7s 重模型,harness embedding 用不存在的模型名 → 降级 hash(不影响事件验证)。
"""
import os, sys, asyncio, tempfile, shutil
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"   # harness 走 hash,快
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.kernel import EventSpine


def test_spine_pubsub_prefix_projection():
    print("\n【1】EventSpine:前缀订阅 + 投影 + 历史")
    spine = EventSpine()
    got = []
    spine.subscribe("harness", lambda e: got.append(e.topic))   # 前缀订阅
    allseen = []
    spine.project(lambda e: allseen.append(e.topic))            # 投影:收到每条
    spine.publish("harness.drift.blocked", {"x": 1})
    spine.publish("memory.ingested", {"y": 2})
    assert got == ["harness.drift.blocked"], "前缀 'harness' 应只命中 harness.*"
    assert allseen == ["harness.drift.blocked", "memory.ingested"], "投影应收到每一条"
    assert len(spine.history()) == 2 and len(spine.history("harness")) == 1
    print("  ✓ 前缀匹配正确;投影全量;历史可按前缀过滤")


def test_systembus_facade():
    print("\n【2】SystemBus 门面:消费者零改 + 汇入脊柱")
    from foxclaw.core.system_bus import SystemBus
    spine = EventSpine()
    seen = []
    spine.project(lambda e: seen.append((e.topic, e.source)))
    bus = SystemBus(spine=spine)
    got = []
    bus.subscribe("execution.completed", lambda m: got.append(m.payload))  # 旧式 handler(msg)
    bus.publish("execution.completed", {"task": "t"})
    assert got == [{"task": "t"}], "旧消费者 handler(msg) 仍收到 .payload"
    assert ("execution.completed", "system_bus") in seen, "系统事件应汇入同一脊柱"
    assert bus.history("execution.completed"), "history 门面可用"
    print("  ✓ handler(msg) 兼容;系统事件经脊柱;history 正常")


def test_harness_forwards_to_spine():
    print("\n【3】Harness → 脊柱转发(goal_registered 不上脊柱)")
    from foxclaw.harness.harness import Harness
    from foxclaw.core.thought_chain import ThoughtChain
    d = tempfile.mkdtemp(prefix="spine_h_")
    try:
        spine = EventSpine()
        seen = []
        spine.project(lambda e: seen.append(e.topic))
        h = Harness("tester", ThoughtChain(str(Path(d) / "tc.db")),
                    policy_path=str(Path(d) / "p.yaml"), spine=spine)
        local = []
        h.bus.on("harness_warn", lambda data: local.append(data))   # 本地订阅仍工作
        h.bus.emit("harness_warn", {"a": 1})     # harness_warn 无内部自订阅,输出干净
        h.bus.emit("goal_registered", {"g": 1})
        assert local == [{"a": 1}], "本地订阅(向后兼容)仍收到"
        assert "harness_warn" in seen, "harness 事件应汇入脊柱"
        assert "goal_registered" not in seen, "高频内部信号不应上脊柱(降噪)"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("  ✓ 本地订阅兼容 + harness 汇入脊柱 + goal_registered 被过滤")


def test_hkc_events_bridge_to_spine():
    print("\n【3b】HKC EventBus → 脊柱桥接(知识事件不再是孤岛;memory.* 刻意不桥)")
    from foxclaw.knowledge import bridge_hkc_events

    class _FakeHKCBus:
        def __init__(self): self._h = {}
        def subscribe(self, ev, h): self._h.setdefault(ev, []).append(h)
        def publish(self, d):
            for h in self._h.get(d.get("event"), []): h(d)

    spine = EventSpine()
    seen = []
    spine.project(lambda e: seen.append((e.topic, e.source, e.payload)))
    bus = _FakeHKCBus()
    assert bridge_hkc_events(bus, spine) is True
    bus.publish({"event": "knowledge.merged", "payload": {"ku_id": "K1"}, "event_id": "EVT_1"})
    bus.publish({"event": "conflict.detected", "payload": {"card": "C1"}})
    bus.publish({"event": "ability.created", "payload": {"key": "A1"}})
    assert ("knowledge.merged", "hkc", {"ku_id": "K1"}) in seen, seen
    assert any(t == "conflict.detected" and s == "hkc" for t, s, _ in seen)
    assert any(t == "ability.created" for t, _, _ in seen)
    # memory.* 刻意不桥(HMR 域,转发会重复/混淆)
    bus.publish({"event": "memory.created", "payload": {}})
    assert not any(t == "memory.created" for t, _, _ in seen), "memory.* 不应被桥接"
    # 降级:bus/spine 为 None → 不接线、不崩
    assert bridge_hkc_events(None, spine) is False
    print("  ✓ knowledge.merged/conflict/ability 桥到脊柱(source=hkc);memory.* 不桥;None 优雅降级")


def test_unified_projection_via_foxclawos():
    print("\n【4】Foxclaw:harness 事件与系统事件被同一投影统一观测")
    d = tempfile.mkdtemp(prefix="spine_os_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        seen = []
        os_.event_spine.project(lambda e: seen.append(e.topic))
        # 两类来源:harness 总线 与 系统总线,都汇入 os_.event_spine
        os_.finance.harness.bus.emit("harness_warn", {"agent": "finance"})
        os_.system_bus.publish("execution.completed", {"task": "demo"})
        assert "harness_warn" in seen, "harness 事件应到统一投影"
        assert "execution.completed" in seen, "系统事件应到统一投影(修复:此前到不了)"
        # 且二者用的是同一条脊柱实例
        assert os_.system_bus.spine is os_.event_spine
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("  ✓ harness + 系统事件汇入同一脊柱、被同一投影观测(4→1 统一成立)")


def test_ws_projection_safe_and_bounded():
    print("\n【5】WS 投影净化:脏/大 payload 不丢连接、不泛洪(深审修复)")
    import json as _json
    import foxclaw.api as api
    # a) 净化器:非 JSON 对象 → 可序列化;超大 → 截断;正常 → 透传
    class Weird:  # 非 JSON 可序列化
        def __repr__(self): return "<Weird>"
    safe = api._ws_safe_payload({"obj": Weird(), "n": 1})
    _json.dumps(safe)   # 不应抛
    big = api._ws_safe_payload({"blob": "x" * 10000})
    assert big.get("_truncated") is True, "超大 payload 应被截断"
    assert api._ws_safe_payload({"a": 1, "b": "ok"}) == {"a": 1, "b": "ok"}, "正常 payload 透传"
    print("  ✓ 非 JSON 对象被 str 化;超大被截断;正常透传")

    # b) 端到端:投影把脏事件放进队列后,队列项必须 JSON 可序列化(否则 send_json 会丢连接)
    d = tempfile.mkdtemp(prefix="spine_ws_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        # 清空可能的既有队列
        while not api._event_queue.empty():
            api._event_queue.get_nowait()
        api._register_spine_projection(os_)
        os_.system_bus.publish("execution.completed",
                               {"agent_id": "x", "task": "t", "result": Weird()})  # 脏 result
        # 真值链会连带产生 trace/truth/memory 事件(再入 publish),全部经投影入队。
        items = []
        while not api._event_queue.empty():
            items.append(api._event_queue.get_nowait())
        assert items, "投影应把事件放进队列"
        for it in items:
            _json.dumps(it)   # 关键:每一项都必须能 send_json 而不抛 → 不会丢连接
        topics = {it["event"] for it in items}
        assert "execution.completed" in topics, f"应含 execution.completed,实得 {topics}"
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("  ✓ 脏 result 事件经投影后队列项 JSON 安全(WS 不会静默丢连接)")


def main():
    print("=" * 60)
    print("统一事件脊柱 · EventSpine(P0 第二项)")
    print("=" * 60)
    test_spine_pubsub_prefix_projection()
    test_systembus_facade()
    test_harness_forwards_to_spine()
    test_hkc_events_bridge_to_spine()
    test_unified_projection_via_foxclawos()
    test_ws_projection_safe_and_bounded()
    print("\n" + "=" * 60)
    print("✓ P0-2 验证完成:脊柱 + SystemBus门面 + Harness转发 + 统一投影")
    print("=" * 60)


if __name__ == "__main__":
    main()
