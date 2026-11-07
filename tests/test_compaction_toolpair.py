"""M5 · 上下文压缩 · 工具消息配对保护(回归)。

病根:压缩器原按**条数**切 keep_recent,边界若切在一轮 function-calling
中间,会切出「有 tool 结果、但其 assistant.tool_calls 已被摘要掉」的孤立
片段 —— 送回 OpenAI/Anthropic 会因孤立 tool_call 引用报 400。

实测(修复前):一轮 2 个工具 + keep_recent=3 → 孤立 call_2。

本文件锁定 group_tool_calls 的分组正确性 + 各 keep 值下配对完整 +
畸形输入的安全降级(skipped_unpaired_tool 而非产出非法序列)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.compaction import Compactor, group_tool_calls


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def _tool_msgs():
    """构造真实工具循环产生的消息形态(参照 base._reply_with_tools:
    每轮 1 个 assistant 带 N 个 tool_calls + N 个 tool 结果)。"""
    return [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "问题" + "x" * 300},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "web_search", "arguments": "{}"}},
            {"id": "call_2", "type": "function",
             "function": {"name": "recall_memory", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "结果1" + "y" * 300},
        {"role": "tool", "tool_call_id": "call_2", "content": "结果2" + "z" * 300},
        {"role": "assistant", "content": "答案" + "w" * 300},
        {"role": "user", "content": "继续" + "q" * 300},
    ]


def _pairs(msgs):
    """返回 (声明的 tool_call_id 集合, 存在的 tool 结果 id 集合)。"""
    declared = {tc["id"] for m in msgs if m.get("tool_calls")
                for tc in m["tool_calls"]}
    got = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    return declared, got


def main():
    print("=" * 60)
    print("M5 · 压缩器工具消息配对保护")
    print("=" * 60)

    print("\n【1】原子组划分:assistant 与其 tool 结果同组")
    groups = group_tool_calls(_tool_msgs())
    tool_groups = [g for g in groups
                   if any(m.get("role") == "assistant" and m.get("tool_calls")
                          for m in g)]
    check(len(tool_groups) == 1,
          f"含 tool_calls 的组应唯一(实际 {len(tool_groups)})")
    check(len(tool_groups[0]) == 3,
          f"该组应含 assistant + 2 个 tool 结果(实际 {len(tool_groups[0])} 条)"
          f"→ {[m.get('role') for m in tool_groups[0]]}")
    check(sum(len(g) for g in groups) == len(_tool_msgs()),
          "分组不丢消息(总数守恒)")

    print("\n【2】各 keep_recent 值下配对均完整(修复前 keep=3 必断)")
    for keep in (1, 2, 3, 4):
        c = Compactor(router=None, max_tokens=10, keep_recent=keep)
        r = c.compact(_tool_msgs())
        declared, got = _pairs(r.messages)
        orphan, missing = got - declared, declared - got
        check(not orphan and not missing,
              f"keep={keep} 配对完整(method={r.method}"
              f"{f' orphan={orphan}' if orphan else ''}"
              f"{f' missing={missing}' if missing else ''})")

    print("\n【3】畸形输入(孤儿 tool)安全降级,不产出非法序列")
    bad = [{"role": "system", "content": "s"},
           {"role": "tool", "tool_call_id": "ghost", "content": "孤儿"},
           {"role": "user", "content": "u" * 300}]
    r = Compactor(router=None, max_tokens=5, keep_recent=1).compact(bad)
    check(r.method in ("skipped_unpaired_tool", "none", "skipped_ineffective"),
          f"孤儿输入走安全降级(method={r.method})")
    check(len(r.messages) == len(bad),
          "安全降级时原样返回,不丢消息")

    print("\n【4】无工具消息的普通对话不受影响(不回归)")
    plain = [{"role": "system", "content": "s"},
             {"role": "user", "content": "你好" + "a" * 400},
             {"role": "assistant", "content": "回答" + "b" * 400},
             {"role": "user", "content": "再问" + "c" * 400},
             {"role": "assistant", "content": "再答" + "d" * 400}]
    c = Compactor(router=None, max_tokens=10, keep_recent=2)
    r = c.compact(plain)
    check(r.messages, "普通对话压缩后非空")
    d, g = _pairs(r.messages)
    check(not (g - d), "普通对话不产生孤立 tool(无 tool 消息)")

    print("\n【5】现有 compaction 行为保持(test_governance 覆盖)")
    r2 = Compactor(router=None, max_tokens=6000, keep_recent=4).compact(plain)
    check(r2.method == "none",
          f"未超阈值时不压缩(method={r2.method})")

    print("\n" + "=" * 60)
    print("M5 全部通过")
    print("=" * 60)


def test_compaction_toolpair():
    main()


if __name__ == "__main__":
    main()
