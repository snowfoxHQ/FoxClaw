"""G1 意图规范化测试。全离线(无真实 Shell/文件操作,不接权限管线)。

覆盖:
  1. 四项现有能力映射(domain/action/effect 与现有 side_effect 一致)
  2. 副作用保持(无 send/write → read 降级)
  3. 目标信息保留(gmail_send → args["to"])
  4. 未知能力 → None(不猜、不放行)
  5. 缺少必要目标字段 → None
  6. 合成删除等价:shell / file_delete / 文件管理器 → 同一 file.delete
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.skills.intent import IntentCanonicalizer, OperationIntent


def test_four_abilities():
    c = IntentCanonicalizer()
    cases = [
        ("calculator", None, OperationIntent(domain="compute", action="eval", effect="read")),
        ("recall_memory", None, OperationIntent(domain="memory", action="recall", effect="read")),
        ("gmail_send", {"to": "a@b.c"}, OperationIntent(domain="external", action="send",
                                                        target="a@b.c", effect="send")),
        ("calendar_create", None, OperationIntent(domain="external", action="write", effect="write")),
    ]
    for name, args, want in cases:
        got = c.canonicalize(name, args)
        assert got == want, f"{name}: {got} != {want}"
    print("  ✓ 四项能力映射正确(domain/action/effect/target)")


def test_effect_preserved():
    c = IntentCanonicalizer()
    assert c.canonicalize("gmail_send", {"to": "x"}).effect == "send"
    assert c.canonicalize("calendar_create", {}).effect == "write"
    assert c.canonicalize("calculator", {}).effect == "read"
    assert c.canonicalize("recall_memory", {}).effect == "read"
    # 显式断言 send 不得落到 read(降级)
    assert c.canonicalize("gmail_send", {"to": "x"}).effect != "read"
    print("  ✓ 副作用等级保持(无 send/write → read 降级)")


def test_unknown_rejected():
    c = IntentCanonicalizer()
    for name in ["shell", "file_delete", "文件管理器", "unknown_tool", "rm"]:
        assert c.canonicalize(name, {}) is None, f"未知能力 {name} 应返回 None"
    print("  ✓ 未知能力返回 None(不猜、不默认生成意图)")


def test_missing_target():
    c = IntentCanonicalizer()
    # gmail_send 声明 target_field="to":缺 to / 空 to 必须返回 None
    assert c.canonicalize("gmail_send", {"subject": "hi"}) is None
    assert c.canonicalize("gmail_send", {"to": ""}) is None
    assert c.canonicalize("gmail_send", {}) is None
    print("  ✓ 缺少必要目标字段返回 None")


def test_target_preserved():
    c = IntentCanonicalizer()
    got = c.canonicalize("gmail_send", {"to": "alice@example.com", "subject": "hi", "body": "x"})
    assert got.target == "alice@example.com"
    print("  ✓ 目标信息正确保留")


def test_synthetic_delete_equivalent():
    c = IntentCanonicalizer()
    # 合成删除路径:三个不同表达 → 同一 file.delete(显式 effect=write)。
    # 只验证多对一规范化,不代表生产环境已开放文件删除。
    c.register("shell", "file", "delete", target_field="path", effect="write")
    c.register("file_delete", "file", "delete", target_field="target", effect="write")
    c.register("文件管理器", "file", "delete", target_field="filename", effect="write")

    a = c.canonicalize("shell", {"path": "file.txt"})
    b = c.canonicalize("file_delete", {"target": "file.txt"})
    d = c.canonicalize("文件管理器", {"filename": "file.txt"})

    want = OperationIntent(domain="file", action="delete", target="file.txt", effect="write")
    assert a == b == d == want, f"{a} / {b} / {d}"
    print("  ✓ 三个删除表达经真实 canonicalize 得到同一 file.delete 意图")


def main():
    print("=" * 60)
    print("G1 意图规范化(Intent Canonicalization)")
    print("=" * 60)
    test_four_abilities()
    test_effect_preserved()
    test_unknown_rejected()
    test_missing_target()
    test_target_preserved()
    test_synthetic_delete_equivalent()
    print("\n✓ G1 验证完成")


if __name__ == "__main__":
    main()
