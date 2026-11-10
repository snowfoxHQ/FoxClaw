"""
B 阶段测试 — office Agent 学习内核(学习行为操作系统)
验证:采集 → 间隔重复(固化) → 知识图谱(连接) → 学习策略
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.learning import LearningRuntime

CSV = str(Path(__file__).parent.parent / "examples" / "sample_knowledge.csv")


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 60)
    print("B 阶段 · office Agent 学习内核(学习行为操作系统)")
    print("=" * 60)

    rt = LearningRuntime()
    n = rt.import_csv(CSV)
    print(f"\n采集 {n} 个知识点")
    check(n == 8, f"采集 8 个知识点(实为 {n})")

    print("\n【固化:间隔重复】")
    ids = list(rt.items.keys())
    check(len(ids) >= 4, f"至少 4 个知识点可供复习(实为 {len(ids)})")
    rt.review(ids[0], grade=5)
    rt.review(ids[0], grade=4)
    rt.review(ids[1], grade=4)
    rt.review(ids[2], grade=1)
    rt.review(ids[2], grade=2)
    for iid in ids[:4]:
        st = rt.review_states[iid]
        item = rt.items[iid]
        print(f"  掌握度 {st.mastery:.2f} | 间隔 {st.interval}天 | "
              f"复习{st.total_reviews}次 遗忘{st.lapses}次 | {item.content[:24]}...")
    st_good, st_bad = rt.review_states[ids[0]], rt.review_states[ids[2]]
    check(st_bad.lapses == 2, f"连错两次的知识点累积 lapses=2(实为 {st_bad.lapses})")
    check(st_bad.mastery < st_good.mastery,
          f"记得差的掌握度低于记得好的({st_bad.mastery:.2f} < {st_good.mastery:.2f})")
    check(st_good.total_reviews == 2 and st_bad.total_reviews == 2,
          "复习次数被正确累计(各 2 次)")

    print("\n【连接:知识图谱】")
    conn = rt.build_connections(semantic_threshold=0.5)
    print(f"  同主题边: {conn['topic_edges']} · 语义边: {conn['semantic_edges']}")
    g = conn["graph"]
    print(f"  图谱: {g['items']} 节点, {g['edges']} 边, "
          f"{g['clusters']} 个知识簇")
    print(f"  边类型: {g['edge_types']}")
    check(conn["topic_edges"] > 0, f"同主题连接已建立({conn['topic_edges']} 条)")
    check(g["items"] == n, f"图谱节点数 = 知识点数({g['items']} == {n})")
    check(g["edges"] == conn["topic_edges"] + conn["semantic_edges"],
          f"总边数 = 同主题边 + 语义边({g['edges']} == {conn['topic_edges']}+{conn['semantic_edges']})")
    check(g["clusters"] > 0, f"聚类非空({g['clusters']} 个知识簇)")

    print("\n【连接发现】")
    related = rt.related(ids[0])
    base = rt.items[ids[0]].content[:20]
    print(f"  与「{base}...」相关的知识:")
    for r in related[:4]:
        print(f"    [{r['edge_type']}/{r['weight']}] {r['item']['content'][:24]}... ({r['reason']})")
    check(len(related) >= 1, f"「{base}…」找到 {len(related)} 条关联知识")
    check(all(("edge_type" in x and "weight" in x and "item" in x) for x in related),
          "每条关联都带 edge_type / weight / item 结构")

    print("\n【综合分析】")
    rep = rt.analyze()
    print(f"  知识点 {rep['total_items']} · 平均掌握度 {rep['avg_mastery']}")
    print(f"  主题掌握度: {rep['topic_mastery']}")
    print(f"  最薄弱主题: {rep['weakest_topic']}")
    print(f"  遗忘风险: {len(rep['forgetting_risk'])} 个高风险知识点")
    check(rep["total_items"] == n, f"报告覆盖全部知识点({rep['total_items']} == {n})")
    check(0 < rep["avg_mastery"] <= 1, f"平均掌握度落在 (0,1](实为 {rep['avg_mastery']})")
    check(rep["weakest_topic"] in rep["topic_mastery"],
          f"最薄弱主题来自主题掌握度表(weakest_topic={rep['weakest_topic']})")

    print("\n【学习策略裁决】")
    for v in rep["strategy"]:
        print(f"  [{v['action']}] {v['message']}")
    check(len(rep["strategy"]) >= 1, f"产出 {len(rep['strategy'])} 条学习策略")
    check(all(("action" in v and "message" in v) for v in rep["strategy"]),
          "每条策略都含 action / message")

    print("\n" + "=" * 60)
    print("✓ B 阶段验证完成:学习内核(间隔重复 + 知识图谱)成立")
    print("=" * 60)


def test_learning():
    main()


if __name__ == "__main__":
    main()
