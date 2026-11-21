"""SC-01 deny 迁移测试:evaluate Stage2 的 deny 规则同时按工具名(id)与规范意图(intent key)匹配。

验证:
  1. deny 依据规范意图而非工具名(deny=["external.send"] 拦 gmail_send,id 并不匹配该键)
  2. intent key 通配(deny=["external.*"] 拦所有 external 能力)
  3. 等价路径拦截(两个不同工具名映射同一 intent,deny 该 key 全拦)
  4. 现有按 id 的 deny 规则仍生效(向后兼容,未映射能力不破坏)
  5. 未映射能力不产生 intent,deny 按 intent key 不误伤;按 id 仍可拦
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.skills import intent as intent_mod
from foxclaw.skills.permission import PermissionRules, evaluate


def _skill(sid, side_effect="read"):
    return SimpleNamespace(id=sid, side_effect=side_effect, kind="tool",
                           destructive=False, external=False, read_only=None,
                           trust_level=None, params={})


def test_deny_by_intent_not_id():
    # deny 键是 intent key(external.send),skill.id 是 gmail_send,字面并不匹配;
    # 经归一化后命中 → 证明 deny 依据规范意图而非工具名。
    d = evaluate(_skill("gmail_send", side_effect="send"), {"to": "a@b.c"},
                 rules=PermissionRules(deny=["external.send"]))
    assert d.decision == "deny" and d.stage == 2, d


def test_intent_wildcard_blocks_all_external():
    cases = [("gmail_send", {"to": "a@b.c"}, "send"), ("calendar_create", {}, "write")]
    for sid, args, effect in cases:
        d = evaluate(_skill(sid, side_effect=effect), args,
                     rules=PermissionRules(deny=["external.*"]))
        assert d.decision == "deny", (sid, d)


def test_equivalent_path_denied_by_shared_intent():
    # 两个不同工具名映射同一 intent external.send,deny 该 key 全拦 → 换工具名不可绕过。
    intent_mod.register("email_out", "external", "send", target_field="to", effect="send")
    for sid in ("gmail_send", "email_out"):
        d = evaluate(_skill(sid, side_effect="send"), {"to": "a@b.c"},
                     rules=PermissionRules(deny=["external.send"]))
        assert d.decision == "deny", (sid, d)


def test_legacy_id_deny_still_works():
    # 未映射能力仍按 id 精确/通配匹配,行为与迁移前一致。
    d = evaluate(_skill("demo"), {}, rules=PermissionRules(deny=["demo"]))
    assert d.decision == "deny", d
    d = evaluate(_skill("mcp:ask_foxclaw"), {}, rules=PermissionRules(deny=["mcp:*"]))
    assert d.decision == "deny", d


def test_unmapped_not_misdenied():
    # 未映射能力不产生 intent,deny 按无关 intent key 不应误伤;按 id 仍可拦。
    d = evaluate(_skill("unknown_tool"), {}, rules=PermissionRules(deny=["file.delete"]))
    assert d.decision == "allow", d
    d = evaluate(_skill("unknown_tool"), {}, rules=PermissionRules(deny=["unknown_tool"]))
    assert d.decision == "deny", d
