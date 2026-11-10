"""MCP `ask_foxclaw` 的外部驱动准入。

外部客户端「绕开工具直接驱动 Agent」时,若不过统一动作门,宿主配的 deny(封杀动作)与
ask(要求人工)对它完全无效。MCP 没有交互确认通道,所以命中 ask 规则要**立即拒**,
而不是放行后等一个永远不会来的确认。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.mcp_server import _policy_error  # noqa: E402


class _StubGate:
    """只回放指定裁决,用来隔离「门怎么判」——这里只测我们怎么消费结果。"""

    def __init__(self, decision: str, code: str = ""):
        self.decision = decision
        self.code = code

    def check_action(self, skill, args, rules=None, confirmed=False, mode="strict",
                     taint_level=0, taint_threshold=0):
        assert skill.side_effect == "write" and skill.external is True
        assert confirmed is False, "外部驱动不能用 confirmed 自证"
        return SimpleNamespace(level=self.decision, reason="stub",
                               raw=SimpleNamespace(reason_code=self.code),
                               decision_id="d")


def test_no_gate_means_no_restriction():
    assert _policy_error(None, None, "分析这个月支出") is None


def test_routine_side_effect_ask_is_allowed():
    """副作用 / external 的常规 ask 由 FOXCLAW_MCP_AGENTS=1 承担授权,不重复拦。"""
    from foxclaw.skills.permission import EXTERNAL_CONFIRM

    assert _policy_error(_StubGate("ask", EXTERNAL_CONFIRM), None, "跑一下") is None


def test_ask_rule_is_refused_not_waited_on():
    """宿主显式要求确认 → 直接拒。MCP 客户端不会有人按下那个按钮。"""
    from foxclaw.skills.permission import ASK_RULE

    assert _policy_error(_StubGate("ask", ASK_RULE), None, "跑一下") == "needs_confirmation"


def test_deny_wins():
    assert _policy_error(_StubGate("deny"), None, "跑一下") == "blocked"


def test_missing_params_surface_as_error():
    from foxclaw.skills.permission import MISSING_PARAMS

    assert _policy_error(_StubGate("ask", MISSING_PARAMS), None, "") == "missing arguments"


def test_real_deny_rule_blocks_mcp_drive():
    """真 evaluate + 真规则集:宿主写 `deny=["mcp:*"]` 必须真的挡住。"""
    from foxclaw.kernel.policy_gate import PolicyGate
    from foxclaw.skills.permission import PermissionRules, evaluate

    gate = PolicyGate(action_evaluator=evaluate)
    rules = PermissionRules(deny=["mcp:*"])
    assert _policy_error(gate, rules, "帮我发封邮件") == "blocked"


def test_real_default_rules_do_not_block():
    """没配任何规则时不能被自己的门挡住(否则 MCP 驱动直接不可用)。"""
    from foxclaw.kernel.policy_gate import PolicyGate
    from foxclaw.skills.permission import PermissionRules, evaluate

    gate = PolicyGate(action_evaluator=evaluate)
    assert _policy_error(gate, PermissionRules(), "分析这个月支出") is None
