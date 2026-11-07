"""联网触发判定:个人数据问题绝不联网,外部世界问题正常联网。

背景(链路追踪抓出来的真 bug):原先纯时间词(今天/本月/最近…)也算触发,而 Foxclaw 的
领域(个人财务/行程/学习)天生满是时间词 → 「分析一下本月支出」这类**问自己账本**的任务
被误判为需联网,白跑 ~22s 的付费深度搜索。错搜代价远大于漏搜,故改为:
个人数据否决 > 外部话题触发 > 纯时间词不触发。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

def test_web_trigger():
    # ISOLATION_GUARD:本用例依赖 import 时固化的模块级单例(网关/限流器/路由配置按 env 在 import 时定型),单进程全量跑会被前序用例污染 →
    # 假红。全量请用 tests/run_all.py(每文件独立子进程)。
    if "foxclaw.core.web_search" in sys.modules:
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    from foxclaw.core.web_search import (needs_search, TOPIC_TRIGGERS,
                                        TIME_WORDS)

    print("=" * 60); print("联网触发判定验证"); print("=" * 60)

    # [1] 个人数据 → 绝不联网(答案在本地领域内核里,不在网上)
    personal = ["分析一下本月支出", "本月餐饮花了多少", "今天的行程安排",
                "本周要复习什么知识点", "账户余额还剩多少", "这个月消费记录"]
    for q in personal:
        assert needs_search(q) is False, f"个人数据不该联网: {q}"
    print(f"[1] 个人数据不联网({len(personal)} 例,含「本月支出」这类原 bug 用例)OK")

    # [2] 个人数据 + 话题词 → 仍不联网(否决优先级最高)
    #     「本月支出多少钱」含话题词「多少钱」,但问的是自己的账 → 必须否决
    assert needs_search("本月支出多少钱") is False, "个人数据否决应压过话题触发"
    assert needs_search("我的预算还剩多少钱") is False
    print("[2] 个人数据否决压过话题触发(「本月支出多少钱」不联网)OK")

    # [3] 外部世界话题 → 正常联网(不能因为修 bug 把真需求也砍了)
    external = ["今天天气怎么样", "最新的AI新闻", "iPhone现在多少钱",
                "特斯拉股价", "python最新版本", "美元汇率"]
    for q in external:
        assert needs_search(q) is True, f"外部话题应联网: {q}"
    print(f"[3] 外部话题正常联网({len(external)} 例)OK")

    # [4] 「我的」不算个人标记 —— 「我的股票现在多少钱」是真要联网的
    assert needs_search("我的股票现在多少钱") is True, "「我的」过宽会误杀真实联网需求"
    print("[4] 「我的股票现在多少钱」仍联网(个人标记不能放「我的」这类过宽词)OK")

    # [5] 纯时间词不触发(本 bug 的核心)
    for q in ["现在几点", "最近怎么样", "今天", "本月"]:
        assert needs_search(q) is False, f"纯时间词不该单独触发: {q}"
    assert not (set(TIME_WORDS) & set(TOPIC_TRIGGERS)), "时间词不得混入话题触发表(否则 bug 复发)"
    print(f"[5] 纯时间词不单独触发({len(TIME_WORDS)} 个时间词与话题表零交集)OK")

    # [6] 闲聊/编码 → 不联网
    for q in ["你好", "帮我写个函数", ""]:
        assert needs_search(q) is False, q
    print("[6] 闲聊/编码/空串 不联网 OK")

    # [7] 英文词边界仍生效(旧修复不能退化:now⊄knowledge, current⊄concurrent)
    assert needs_search("knowledge base") is False, "now 不该子串命中 knowledge"
    assert needs_search("concurrent tasks") is False, "current 不该子串命中 concurrent"
    assert needs_search("latest news") is True
    print("[7] 英文词边界不退化(knowledge/concurrent 不误命中)OK")

    # [8] 【防复发·跨路径】个人数据保护必须是**单一真源**,所有"要不要发到外部"的路径都得遵守。
    #     真实教训:这条保护原先只写在 needs_search 里;技能派发默认开启后,
    #     「查一下最新的本月支出」经**技能路由**照样把私人财务问题发去 external 的 web_search
    #     —— 同一个 bug 从没设防的旁路回来了,还多了数据外泄。
    from foxclaw.core.web_search import is_personal_query
    from foxclaw.pipeline import stage_route
    from foxclaw.pipeline.cognitive_pipeline import PipelineContext
    from foxclaw.skills import build_default_registry

    assert is_personal_query("查一下最新的本月支出") is True
    assert is_personal_query("查一下最新新闻") is False

    _reg = build_default_registry()
    class _A: pass
    def _route_target(task):
        a = _A(); a.skill_registry = _reg
        ctx = PipelineContext(agent=a, task=task)
        stage_route(ctx)
        return ctx.route["target"], ctx.route.get("skill_id")

    # 私人数据 + 足够高的置信 → 仍不得路由到 **external** 技能(否则就是外泄)
    for q in ["查一下最新的本月支出", "查一下最新的预算结余", "今天的行程 最新 查一下"]:
        t, sid = _route_target(q)
        assert t == "self", f"私人数据不得自动发给外部技能: {q} → {t}/{sid}"
    # 但真实的外部问题照常走技能(不能因为修 bug 把功能砍了)
    t, sid = _route_target("查一下最新新闻")
    assert (t, sid) == ("skill", "web_search"), (t, sid)
    # 本地只读技能不受影响
    t, sid = _route_target("帮我计算 (12+8)*5")
    assert (t, sid) == ("skill", "calculator"), (t, sid)
    print("[8] 跨路径防复发:技能路由也遵守个人数据保护(不外泄)+ 不误伤真实外部需求 OK")

    print("\n联网触发判定验证全部通过")

if __name__ == "__main__":
    test_web_trigger()
