"""
M4 · 记忆隔离召回完整性（根治）回归

验证 HMRAdapter 的 tag 预过滤根治路径:
  1. 完整性:recall(agent=X) 在"X 私有 + shared"**全集**内排序,X 自己的相关记忆一定能召回,
     不会因库里其他 Agent 有大量强相关记忆挤占全局 top-k 而被漏掉;
  2. 隔离:任一路径都只返回带 agent:X 或 shared 标签的记忆,绝不泄露他人私有;
  3. 对照:同一库里老路径(_legacy_recall,全局 top-k 再过滤)会漏掉被淹没的 X 记忆,
     根治路径(_filtered_recall)能找回 —— 直观展示"完整性缺陷被根治"。

真实 HMR(离线,ST 本地缓存);HMR 不可用时优雅跳过。
"""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.memory_adapter import create_memory_backend

QUERY = "quarterly revenue growth and budget forecast analysis"
N_FINANCE = 55           # > 老路径 fetch_k(50),< 压缩阈值(同类型 80)


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 56)
    print("M4 · 记忆隔离召回完整性（根治）")
    print("=" * 56)
    hmr = create_memory_backend(storage_path=tempfile.mkdtemp())
    if getattr(hmr, "backend", "") != "HMR":
        print("  · HMR 不可用(降级 Mock),跳过 M4 测试")
        print("结果:跳过"); return

    # office:一条与 query 相关的关键记忆 + 一条无关
    hmr.ingest("office quarterly revenue growth and budget forecast for planning",
               type="concept", agent_id="office", title="office-key")
    hmr.ingest("office meeting room booking schedule for next week",
               type="concept", agent_id="office", title="office-other")
    # finance:大量与 query 强相关记忆(会在全局排序里淹没 office 的记忆)
    for i in range(N_FINANCE):
        hmr.ingest(f"finance quarterly revenue growth and budget forecast record {i}",
                   type="concept", agent_id="finance", title=f"fin-{i}")
    # 一条共享记忆
    hmr.ingest("shared team quarterly planning memo", type="concept",
               agent_id="finance", shared=True, title="shared-memo")

    print("\n【1】完整性:根治路径在 office 全集内排序,召回其相关记忆")
    res = hmr.recall(QUERY, agent_id="office", top_k=5)
    titles = [r["title"] for r in res]
    check("office-key" in titles, f"office 的相关记忆被召回（结果:{titles}）")

    print("\n【2】隔离:office 召回零 finance 私有泄露")
    def foreign_private(r):
        tags = r.get("tags", [])
        return (any(t.startswith("agent:") and t != "agent:office" for t in tags)
                and "shared" not in tags)
    check(not any(foreign_private(r) for r in res), "office 召回结果无他人私有")

    print("\n【3】根治路径 = 在 X 全集内考虑(不受全局 top-k 挤占)")
    full = hmr._filtered_recall(QUERY, "office", top_k=100, include_shared=True)
    ftitles = {r["title"] for r in full}
    check(ftitles == {"office-key", "office-other", "shared-memo"},
          f"office 视角恰好看到自己 2 条 + 共享 1 条,零 finance（{sorted(ftitles)}）")

    print("\n【4】对照:老路径漏掉被淹没的 office 记忆,根治路径找回")
    legacy = hmr._legacy_recall(QUERY, "office", top_k=5, include_shared=True)
    filtered = hmr._filtered_recall(QUERY, "office", top_k=5, include_shared=True)
    legacy_has = any(r["title"] == "office-key" for r in legacy)
    filtered_has = any(r["title"] == "office-key" for r in filtered)
    print(f"    老路径召回 office-key: {legacy_has}   根治路径召回 office-key: {filtered_has}")
    check(filtered_has, "根治路径召回到 office-key（完整性）")
    check(not legacy_has, "老路径确实漏掉 office-key（被 finance 强相关记忆淹没,印证根治的必要）")

    print("\n【5】反向隔离:finance 召回零 office 私有")
    fin = hmr._filtered_recall(QUERY, "finance", top_k=100, include_shared=True)
    check(not any(r["title"].startswith("office-") for r in fin),
          "finance 视角看不到 office 私有")
    check(any(r["title"] == "shared-memo" for r in fin), "finance 能看到共享记忆")

    print("\n" + "=" * 56)
    print("记忆隔离召回完整性 根治通过")
    print("=" * 56)


def test_memory_recall():
    main()


if __name__ == "__main__":
    main()
