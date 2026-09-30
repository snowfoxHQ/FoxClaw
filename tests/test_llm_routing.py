"""
LLM 辅助路由回归

真机 E2E 暴露:纯关键词路由会把没含确切关键词的任务("房租占比/向量数据库/会议冲突")
全落到 assistant。改进:关键词命中优先(快、确定);没命中且模型可用 → LLM 分类精修。
本测试用"假 router"模拟真机(is_stub=False),验证:
  · 关键词未命中的任务 → 被 LLM 精修到正确领域;
  · 关键词命中的任务 → 直接路由,不调用 LLM(省成本);
  · stub 模式 → 不触发 LLM(离线确定性)。
"""
import asyncio
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import hestia.core.web_search as ws
ws.needs_search = lambda q: False

from hestia import HestiaOS


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 56)
    print("LLM 辅助路由回归")
    print("=" * 56)
    os_ = HestiaOS(data_dir=tempfile.mkdtemp())

    calls = []

    def fake(system, messages, max_tokens=2048, agent_id=""):
        content = str(messages[-1]["content"]) if messages else ""
        calls.append(content)
        # 分类 prompt 的特征串 → 返回领域词;否则普通回答
        if "只输出一个词" in content:
            return "finance"
        return "已处理"

    # 让路由认为模型可用(非 stub)
    os_.router.provider = "deepseek"
    os_.router._client = object()
    os_.router.complete = fake
    check(os_.router.is_stub is False, "router 处于'可用'状态(非 stub)")

    print("\n【1】关键词未命中 → LLM 分诊精修")
    # "房租/收入" 不在任何领域关键词表 → 关键词兜底 assistant,再由 LLM 精修
    r = asyncio.run(os_.supervisor.run_task("每月房租3000占收入10000这样合理吗"))
    check(r["result"].get("dispatched_to") == "finance",
          f"未命中关键词 → LLM 精修到 finance(实为 {r['result'].get('dispatched_to')})")
    check(any("只输出一个词" in c for c in calls), "确实调用了 LLM 分类")

    print("\n【2】关键词命中 → 直接路由,不问 LLM(省成本)")
    calls.clear()
    r2 = asyncio.run(os_.supervisor.run_task("帮我做个预算分析"))   # "预算" 命中 finance
    check(r2["result"].get("dispatched_to") == "finance", "命中'预算' → 直接 finance")
    check(not any("只输出一个词" in c for c in calls), "关键词命中时不调用 LLM 分类")

    print("\n【3】stub 模式不触发 LLM(离线确定性)")
    os_.router.provider = "stub"
    os_.router._client = None
    calls.clear()
    r3 = asyncio.run(os_.supervisor.run_task("解释一下什么是航天器"))  # 无关键词
    check(os_.router.is_stub is True, "router 回到 stub")
    check(not any("只输出一个词" in c for c in calls), "stub 下不调用 LLM 分类")

    print("\n" + "=" * 56)
    print("LLM 辅助路由回归通过")
    print("=" * 56)


if __name__ == "__main__":
    main()
