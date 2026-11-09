"""
元素编号器。

核心思路：LLM 不应该猜 CSS selector 或坐标，只需要输出一个数字 ID。
这里负责：给页面里当前可交互的元素分配编号，并维护
"编号 -> 底层浏览器句柄"的映射，供 action_executor 使用。

编号在同一个 Snapshot 生命周期内稳定；页面一旦发生导航或大幅刷新，
编号需要重新分配（旧编号即失效，这是有意为之——避免"过期编号"
被误用去点击一个已经不存在的元素）。
"""

from typing import List, Dict, Any


class ElementIndexer:

    def __init__(self):
        self._registry: Dict[str, Any] = {}
        self._counter = 0

    def reset(self):
        self._registry.clear()
        self._counter = 0

    def assign(self, handles: List[Any], describe_fn) -> List[Dict[str, Any]]:
        """
        handles: 底层 driver 返回的可交互元素句柄列表
        describe_fn: 把单个句柄转成 {"role":..., "text":..., "attrs":...} 的函数，
                     由具体 driver 实现（不同 driver 取属性的方式不同）
        """
        self.reset()
        indexed = []

        for handle in handles:
            self._counter += 1
            eid = str(self._counter)
            self._registry[eid] = handle

            desc = describe_fn(handle)
            indexed.append({
                "id": eid,
                "role": desc.get("role", "unknown"),
                "text": desc.get("text", "")[:80],
                "attrs": desc.get("attrs", {}),
            })

        return indexed

    def resolve(self, element_id: str):
        """把编号还原为底层句柄，供 action_executor 执行真实操作"""
        if element_id not in self._registry:
            raise KeyError(
                f"元素编号 {element_id} 不存在或已过期（页面可能已刷新，需要重新提取语义结构）"
            )
        return self._registry[element_id]
