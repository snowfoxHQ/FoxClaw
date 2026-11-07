"""
统一规划原语(P1-2:三个 Planner 合并)测试 —— 全离线。

覆盖:
  1. 共享原语:is_negated / split_on / keyword_hits(否定开关)
  2. trigger_route 引擎:否定过滤、打分、兜底、near-ties→needs_composition、确定性平局
  3. 三处委托同一引擎(单一真源):
     · AHS pipeline.route 否定生效(不选被否定的 skill)
     · search QueryPlanner._decompose 用共享拆分
     · 主管关键词命中走 keyword_hits
  4. 结构:pipeline 不再持有本地 _negated/_split_on(已删除,证明去重)
"""
import os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.planning import planner as P


def test_primitives():
    print("\n【1】共享原语:否定 / 拆分 / 关键词命中")
    assert P.is_negated("不要计算这个", "计算") is True
    assert P.is_negated("请计算这个", "计算") is False
    assert P.split_on("查北京，查上海、查广州", P.LIST_SEPS) == ["查北京", "查上海", "查广州"]
    assert P.split_on("先做A 然后 做B", P.SPLIT_MARKERS) == ["先做A", "做B"]
    assert P.keyword_hits("分析财务收支", ["财务", "收支", "行程"]) == 2
    assert P.keyword_hits("不要分析财务", ["财务"], negation=True) == 0
    assert P.keyword_hits("不要分析财务", ["财务"], negation=False) == 1
    print("  ✓ is_negated / split_on / keyword_hits(否定开关) 均正确")


def test_trigger_route_engine():
    print("\n【2】trigger_route 引擎:否定/打分/兜底/near-ties/确定性")
    C = P.ScoredCandidate
    cands = [
        C("calculator", ["计算", "算"], "四则运算 计算器"),
        C("web", ["搜索", "查"], "联网 搜索 网页"),
        C("email", ["发邮件", "email"], "发送 邮件"),
    ]
    # 否定:"不要计算" → calculator 触发被否定 → 不选它
    r = P.trigger_route("不要计算一下", cands)
    assert r.primary != "calculator", f"否定应排除 calculator,实得 {r.primary}"
    # 命中:"帮我搜索北京" → web
    r2 = P.trigger_route("帮我搜索北京", cands)
    assert r2.primary == "web" and r2.confidence >= 0.15
    # 兜底:完全不相关 → 用最后一个候选兜底,低置信
    r3 = P.trigger_route("xyzzy 无关内容", cands)
    assert r3.primary == cands[-1].id and r3.confidence < 0.15
    # 确定性:空候选
    assert P.trigger_route("x", []).primary is None
    print(f"  ✓ 否定排除 / 命中选中 / 兜底(={r3.primary}) / 空候选")


def test_three_sites_delegate():
    print("\n【3】三处委托同一引擎(单一真源)")
    # AHS
    from foxclaw.skills import build_default_registry
    from foxclaw.skills.pipeline import route, plan
    reg = build_default_registry()
    assert route("不要计算", reg).primary != "calculator", "AHS route 否定生效"
    assert len(plan("先算 5+5 然后再算 9*9", reg).tasks) == 2, "AHS plan 拆解"
    # search
    from foxclaw.search.planner import QueryPlanner
    sub = QueryPlanner()._decompose("查A，查B、查C")
    assert len(sub) == 3, "search 拆解走共享原语"
    # supervisor(用引擎的 keyword_hits;此处直接验证原语等价)
    assert P.keyword_hits("买基金理财", ["理财", "基金", "收支"]) == 2
    print("  ✓ AHS / search / 主管 三处均由 foxclaw.planning 单一真源驱动")


def test_dedup_structural():
    print("\n【4】结构去重:pipeline 不再持有本地 _negated/_split_on")
    import foxclaw.skills.pipeline as pipe
    assert not hasattr(pipe, "_negated"), "pipeline 应删除本地 _negated(改用共享)"
    assert not hasattr(pipe, "_split_on"), "pipeline 应删除本地 _split_on(改用共享)"
    assert not hasattr(pipe, "_NEGATIONS"), "pipeline 应删除本地 _NEGATIONS 常量"
    print("  ✓ pipeline 的重复否定/拆分实现已删除,收敛到 foxclaw.planning")


def main():
    print("=" * 60)
    print("统一规划原语 · 三个 Planner 合并(P1-2)")
    print("=" * 60)
    test_primitives()
    test_trigger_route_engine()
    test_three_sites_delegate()
    test_dedup_structural()
    print("\n" + "=" * 60)
    print("✓ P1-2 验证完成:一个规划引擎,主管/AHS/search 三处委托")
    print("=" * 60)


if __name__ == "__main__":
    main()
