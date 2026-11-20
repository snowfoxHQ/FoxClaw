"""个人数据外发闸门:问自己的账本/行程/学习,绝不交给外部技能或外发通道。

背景:联网检索能力已移除,但「个人数据不出网」裁决必须独立保留——它保护的是
所有外发路径(技能路由、MCP、外部 API),不随某个具体检索功能一起消失。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.personal_data import is_personal_query


def test_personal_queries_blocked():
    for q in ["分析一下本月支出", "本月餐饮花了多少", "今天的行程安排",
              "本周要复习什么知识点", "账户余额还剩多少", "这个月消费记录"]:
        assert is_personal_query(q) is True, f"个人数据应被识别: {q}"


def test_generic_queries_allowed():
    for q in ["今天天气怎么样", "最新的AI新闻", "python最新版本", "美元汇率",
              "你好", "帮我写个函数", ""]:
        assert is_personal_query(q) is False, f"通用问题不该被误杀: {q}"


def test_personal_marker_survives_extra_words():
    assert is_personal_query("查一下最新的本月支出") is True
    assert is_personal_query("查一下最新新闻") is False


def test_private_task_never_routed_to_external_skill():
    """跨路径防复发:技能路由也必须遵守个人数据保护,不得把私人数据发给外部技能。"""
    from foxclaw.pipeline import stage_route
    from foxclaw.pipeline.cognitive_pipeline import PipelineContext
    from foxclaw.skills import build_default_registry

    reg = build_default_registry()

    class _A:
        pass

    def _route_target(task):
        a = _A()
        a.skill_registry = reg
        ctx = PipelineContext(agent=a, task=task)
        stage_route(ctx)
        return ctx.route["target"], ctx.route.get("skill_id")

    for q in ["查一下最新的本月支出", "查一下最新的预算结余", "今天的行程 最新 查一下"]:
        t, sid = _route_target(q)
        assert t == "self", f"私人数据不得自动发给外部技能: {q} → {t}/{sid}"

    t, sid = _route_target("帮我计算 (12+8)*5")
    assert (t, sid) == ("skill", "calculator"), (t, sid)
