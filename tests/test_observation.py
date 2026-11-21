"""SC-02 观察通道测试:ObservationIntent + ObservationRules + evaluate_observation +
PolicyGate.check_observation 分流。

验证(7 约束 / 5 验收的合成形态):
  - scope 限制:指定窗口授权不扩展到其他窗口
  - fail-closed:未声明感知源 / 未授权观察一律拒绝
  - deny 优先
  - 跨通道独立:操作 read 不自动放行屏幕;观察授权不当操作授权用
  - sensitivity → taint(SC-03 污点传播语义基础)
  - check_observation:评估器缺失 fail-closed;allow/deny 归一化为 Verdict + decision_id
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.kernel.policy_gate import PolicyGate
from foxclaw.skills import intent as intent_mod
from foxclaw.skills.intent import ObservationIntent
from foxclaw.skills.observation import ObservationRules, evaluate_observation
from foxclaw.skills.permission import PermissionRules, evaluate


def _skill(sid, side_effect="read"):
    return SimpleNamespace(id=sid, side_effect=side_effect, kind="tool",
                           destructive=False, external=False, read_only=None,
                           trust_level=None, params={})


def test_scope_limit_window():
    rules = ObservationRules(allow=["screen:window:1234"])
    assert evaluate_observation(ObservationIntent("screen", "window:1234"), rules).decision == "allow"
    assert evaluate_observation(ObservationIntent("screen", "window:5678"), rules).decision == "deny"
    assert evaluate_observation(ObservationIntent("screen", "full"), rules).decision == "deny"


def test_unbound_source_fail_closed():
    assert evaluate_observation(ObservationIntent("unknown", "full")).decision == "deny"
    assert evaluate_observation(ObservationIntent("screen", "full")).decision == "deny"


def test_deny_overrides_allow():
    rules = ObservationRules(allow=["screen:*"], deny=["screen:full"])
    assert evaluate_observation(ObservationIntent("screen", "full"), rules).decision == "deny"
    assert evaluate_observation(ObservationIntent("screen", "window:1"), rules).decision == "allow"


def test_taint_from_sensitivity():
    d = evaluate_observation(ObservationIntent("browser", "page", sensitivity="sensitive"),
                             ObservationRules(allow=["browser:*"]))
    assert d.decision == "allow" and d.taint == 2, d


def test_channels_independent():
    # 操作 read 放行,不自动放行屏幕观察(验收 2/4)。
    assert evaluate(_skill("calculator", side_effect="read"), {},
                    rules=PermissionRules()).decision == "allow"
    assert evaluate_observation(ObservationIntent("screen", "full"),
                                ObservationRules()).decision == "deny"
    # 观察授权不当操作授权用(验收 1):观察 allow screen,操作 deny 仍拦点击。
    intent_mod.register("desktop_click", "input", "click", effect="write")
    assert evaluate(_skill("desktop_click", side_effect="write"), {},
                    rules=PermissionRules(deny=["input.click"])).decision == "deny"


def test_check_observation_fail_closed():
    gate = PolicyGate()  # 未注入观察评估器
    v = gate.check_observation(ObservationIntent("screen", "full"))
    assert v.allowed is False and v.level == "deny" and v.source == "observation"
    assert v.decision_id, v


def test_check_observation_normalizes():
    gate = PolicyGate(observation_evaluator=evaluate_observation)
    ok = gate.check_observation(ObservationIntent("screen", "window:1"),
                                rules=ObservationRules(allow=["screen:window:1"]))
    assert ok.allowed is True and ok.level == "allow" and ok.decision_id, ok
    bad = gate.check_observation(ObservationIntent("screen", "full"))
    assert bad.allowed is False and bad.level == "deny" and bad.decision_id, bad
