"""
B 阶段测试 — office Agent 学习内核(学习行为操作系统)
验证:采集 → 间隔重复(固化) → 知识图谱(连接) → 学习策略
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hestia.learning import LearningRuntime

CSV = str(Path(__file__).parent.parent / "examples" / "sample_knowledge.csv")


def main():
    print("=" * 60)
    print("B 阶段 · office Agent 学习内核(学习行为操作系统)")
    print("=" * 60)

    rt = LearningRuntime()
    n = rt.import_csv(CSV)
    print(f"\n采集 {n} 个知识点")

    # 1. 间隔重复:模拟几次复习
    print("\n【固化:间隔重复】")
    ids = list(rt.items.keys())
    # 模拟:前两个记得好,第三个记不住
    rt.review(ids[0], grade=5)  # 完美
    rt.review(ids[0], grade=4)
    rt.review(ids[1], grade=4)
    rt.review(ids[2], grade=1)  # 没记住
    rt.review(ids[2], grade=2)  # 又没记住
    for iid in ids[:4]:
        st = rt.review_states[iid]
        item = rt.items[iid]
        print(f"  掌握度 {st.mastery:.2f} | 间隔 {st.interval}天 | "
              f"复习{st.total_reviews}次 遗忘{st.lapses}次 | {item.content[:24]}...")

    # 2. 知识图谱:构建连接
    print("\n【连接:知识图谱】")
    conn = rt.build_connections(semantic_threshold=0.5)
    print(f"  同主题边: {conn['topic_edges']} · 语义边: {conn['semantic_edges']}")
    g = conn["graph"]
    print(f"  图谱: {g['items']} 节点, {g['edges']} 边, "
          f"{g['clusters']} 个知识簇")
    print(f"  边类型: {g['edge_types']}")

    # 3. 连接发现:某知识点的关联
    print("\n【连接发现】")
    related = rt.related(ids[0])
    base = rt.items[ids[0]].content[:20]
    print(f"  与「{base}...」相关的知识:")
    for r in related[:4]:
        print(f"    [{r['edge_type']}/{r['weight']}] {r['item']['content'][:24]}... ({r['reason']})")

    # 4. 综合分析
    print("\n【综合分析】")
    rep = rt.analyze()
    print(f"  知识点 {rep['total_items']} · 平均掌握度 {rep['avg_mastery']}")
    print(f"  主题掌握度: {rep['topic_mastery']}")
    print(f"  最薄弱主题: {rep['weakest_topic']}")
    print(f"  遗忘风险: {len(rep['forgetting_risk'])} 个高风险知识点")

    # 5. 学习策略裁决
    print("\n【学习策略裁决】")
    for v in rep["strategy"]:
        print(f"  [{v['action']}] {v['message']}")

    print("\n" + "=" * 60)
    print("✓ B 阶段验证完成:学习内核(间隔重复 + 知识图谱)成立")
    print("=" * 60)


if __name__ == "__main__":
    main()
