"""共享辅助函数与多步链式常量（步骤 3 从 agents.py 拆出，纯搬移）。"""

from ...core.dispatch import dispatch_failed as _dispatch_failed  # noqa: F401  (re-export)

PRIOR_MAX_STEPS = 3
PRIOR_MAX_CHARS = 300


def _with_prior(subtask: str, prior: list) -> str:
    """把前序步骤结果并进下一步的任务文本,让多步真正成链。

    为什么并进 task 文本而不是只塞 context:各领域 Agent 的 `_execute` 是各自实现的,
    未必会去读 context;而 task 文本必定进提示词 —— 这样对**所有** Agent 都生效,
    且无需改动任何一个 Agent。context 里也照样放一份结构化的(两手准备)。
    """
    if not prior:
        return subtask
    lines = "\n".join(
        f"第{p['step']}步({p['agent']}):{str(p.get('output',''))[:PRIOR_MAX_CHARS]}"
        for p in prior[-PRIOR_MAX_STEPS:]
    )
    return f"{subtask}\n\n[前序步骤结果 —— 若与本步相关,请据此作答]\n{lines}"


def _fmt_mem(m: dict, max_chars: int | None = None) -> str:
    """把一条召回记忆格式化为带结构信息的文本(标题/相关度/共享标记)。

    替代此前各领域 Agent 只取 content 的注入——那会丢弃 title/score/shared 等
    结构信息,让 LLM 无法区分"高相关共享决策"与"低相关私有闲聊"。字段可能缺失
    (不同召回路径返回的 dict 字段不一),全部安全降级:title 空 / score 非数字 /
    无 shared 时退化为纯 content,行为与改前一致。max_chars=None 不截断。
    """
    title = (m.get("title") or "").strip()
    content = (m.get("content") or "").strip()
    if max_chars is not None:
        content = content[:max_chars]
    parts = [title] if title else []
    score = m.get("score")
    if score is not None:
        try:
            parts.append(f"相关度{float(score):.2f}")
        except (TypeError, ValueError):
            pass
    if m.get("shared"):
        parts.append("[共享]")
    head = " ".join(parts)
    return f"{head}：{content}" if head else content


def _extract_step_results(dr) -> list:
    """从 dispatch_result 提取各步结果(供 LLM 反思:含成功/失败状态与输出)。"""
    if not isinstance(dr, dict):
        return []
    result = dr.get("result") or {}
    if not isinstance(result, dict):
        return []
    steps = result.get("parallel_steps") or result.get("multi_steps") or []
    out = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        out.append({
            "subtask": s.get("subtask") or s.get("step"),
            "agent": s.get("agent"),
            "output": s.get("output", ""),
            "blocked": bool(s.get("blocked")),
            "failed": bool(s.get("failed")),
        })
    return out

