"""主管分诊与自答策略回归。

覆盖主管在「是否命中领域关键词」×「模型是否可用」下的派发决策:
  · 关键词命中        → 直接路由到领域 Agent(快、确定),不额外调用一次 LLM 分诊;
  · 未命中 + 模型可用 → 主管**直接自答**(dispatched_to=supervisor, self_answered=True);
  · 未命中 + stub     → 走派发兜底(assistant),保持离线确定性。

⚠ 历史说明(改名前为 test_llm_routing.py):本文件曾断言「关键词未命中 → LLM 分诊精修到
finance」。那次精修实现在 `agents.py:_llm_classify`,但因每次要多一次约 5s 的串行 LLM
往返,产品上被「主管直接自答」取代而退化为**死代码**(全仓库零调用点),已随本轮删除。
原断言随之永远无法满足,长期为红,却被归因为「环境问题」——这是**误判**:它不是环境问题,
是**测试过期**。现按真实行为重写,并把三种组合都锁住。

用"假 router"模拟真机(is_stub=False),避免依赖真实 API key,全离线。
"""
import asyncio
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import foxclaw.core.web_search as ws
ws.needs_search = lambda q: False      # 自答路径不触发联网,保持离线

from foxclaw import Foxclaw


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


# 分诊 prompt 的特征串。历史上 _llm_classify 用它问模型;现已无此路径,
# 故断言它**从不出现**,即「不再为分诊多花一次 LLM 往返」。
CLASSIFY_MARK = "只输出一个词"


def main():
    print("=" * 56)
    print("主管分诊与自答策略回归")
    print("=" * 56)
    os_ = Foxclaw(data_dir=tempfile.mkdtemp())

    calls = []

    def fake(system, messages, max_tokens=2048, agent_id=""):
        content = str(messages[-1]["content"]) if messages else ""
        calls.append(content)
        if CLASSIFY_MARK in content:      # 若真有人恢复分诊,这里会暴露
            return "finance"
        return "已处理"

    # 让路由认为模型可用(非 stub)
    os_.router.provider = "deepseek"
    os_.router._client = object()
    os_.router.complete = fake
    check(os_.router.is_stub is False, "router 处于'可用'状态(非 stub)")

    print("\n【1】关键词未命中 + 模型可用 → 主管直接自答(不再二次分诊)")
    # 这句不含任何领域关键词,也不像"花了钱"的报备(没有完成态动词),
    # 所以仍然走自答 —— 花费报备现在已算作财务命中,见【1b】。
    no_kw = "讲个笑话听听"
    check(os_.supervisor._has_keyword_hit(no_kw) is False, f"「{no_kw[:12]}…」确无关键词命中")
    r = asyncio.run(os_.supervisor.run_task(no_kw))
    d1 = r["result"].get("dispatched_to")
    check(d1 == "supervisor", f"未命中 → 主管自答(dispatched_to={d1},预期 supervisor)")
    check(r["result"].get("self_answered") is True, "标记 self_answered=True(确为自答而非派发)")
    check(bool(str(r["result"].get("sub_result") or "").strip()), "自答产出非空答复")
    check(not any(CLASSIFY_MARK in c for c in calls), "全程未发出分诊 prompt(不再多花一次 LLM 往返)")

    print("\n【1b】零星花费报备(无财务关键词)→ 认作财务命中,派给财务")
    for line in ("今天买了一件衣服花了105块", "后天花了100洗车"):
        check(os_.supervisor._has_keyword_hit(line), f"「{line[:12]}…」算作领域命中")
        check(os_.supervisor._route(line) == "finance", f"「{line[:12]}…」→ finance")
    # 讨论额度/比例不算报备,但仍属于财务问题 → 也交财务,而不是主管自答
    check(os_.supervisor._route("每月房租3000占收入10000这样合理吗") == "finance",
          "房租占比咨询 → finance")

    print("\n【2】关键词命中 → 直接路由,不问 LLM 分诊(省成本)")
    calls.clear()
    r2 = asyncio.run(os_.supervisor.run_task("帮我做个预算分析"))   # "预算" 命中 finance
    d2 = r2["result"].get("dispatched_to")
    check(d2 == "finance", f"命中'预算' → 直接 finance(实为 {d2})")
    check(not any(CLASSIFY_MARK in c for c in calls), "关键词命中时不发出分诊 prompt")

    print("\n【3】stub + 关键词未命中 → 主管自处理(离线确定性)")
    os_.router.provider = "stub"
    os_.router._client = None
    calls.clear()
    r3 = asyncio.run(os_.supervisor.run_task("解释一下什么是航天器"))   # 无关键词
    check(os_.router.is_stub is True, "router 回到 stub")
    d3 = r3["result"].get("dispatched_to")
    check(d3 == "supervisor", f"stub 下未命中 → 主管自身处理(实为 {d3})")
    check(not r3["result"].get("self_answered"), "stub 下走派发路径,而非主管自答")
    check(not any(CLASSIFY_MARK in c for c in calls), "stub 下不发出分诊 prompt")

    print("\n" + "=" * 56)
    print("主管分诊与自答策略回归通过")
    print("=" * 56)


def test_supervisor_triage():
    main()


if __name__ == "__main__":
    main()
