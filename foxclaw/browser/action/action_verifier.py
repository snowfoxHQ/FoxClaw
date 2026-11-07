"""
ActionVerifier —— HIR-Browser 的第二个核心壁垒。

问题：LLM 不知道自己的操作有没有生效。selector 没报错 ≠ 操作成功。
方案：执行前后各拍一次语义快照，做 diff，用 diff 结果去校验
     "这次动作是否符合预期"，而不是简单地"没抛异常就算成功"。

这里的判断规则刻意保持简单、可解释，不引入模型推理——
"是否成功"这个判断本身应该是确定性的、可复现的。
"""

from ..perception.snapshot import Snapshot
from .action_types import WebAction


class ActionVerifier:

    def verify(self, before: Snapshot, after: Snapshot, action: WebAction) -> dict:
        diff = self._diff(before, after)

        success = self._matches_expectation(diff, action)

        return {
            "success": success,
            "diff": diff,
            "url_changed": before.url != after.url,
        }

    def _diff(self, before: Snapshot, after: Snapshot) -> dict:
        before_ids = before.element_ids()
        after_ids = after.element_ids()

        return {
            "url_before": before.url,
            "url_after": after.url,
            "elements_added": len(after_ids - before_ids),
            "elements_removed": len(before_ids - after_ids),
            "title_before": before.title,
            "title_after": after.title,
        }

    def _matches_expectation(self, diff: dict, action: WebAction) -> bool:
        """
        按动作类型给出最基本的"是否生效"判断：

        - navigate: URL 必须变化
        - click: URL 变化，或页面元素结构发生变化（新增/移除元素），
                 或标题变化——三者任一即视为"页面对点击有反应"
        - type: 无法通过快照diff直接验证输入内容是否写入（需要额外读取
                目标元素的 value），这里保守返回 True，交由上层结合
                extract 结果自行判断
        - scroll / wait / extract: 不强制要求页面发生变化
        """
        if action.type == "navigate":
            return diff["url_before"] != diff["url_after"]

        if action.type == "click":
            return (
                diff["url_before"] != diff["url_after"]
                or diff["elements_added"] > 0
                or diff["elements_removed"] > 0
                or diff["title_before"] != diff["title_after"]
            )

        if action.type in ("type", "scroll", "wait", "extract"):
            return True

        return True
