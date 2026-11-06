"""
BaseAdapter —— 与 HIR v1.0 约定的 Adapter 接口保持一致，
方便直接 `vm.register_adapter(WebAdapter())` 接入 HIR Kernel。

如果你的 HIR Core 里已经有 hir.adapters.base.BaseAdapter，
直接 import 那一份替换这里的定义即可，这里只是保证
HIR-Browser 单独运行/测试时不依赖外部 HIR 包。
"""

from abc import ABC, abstractmethod


class BaseAdapter(ABC):

    @abstractmethod
    def match(self, action: dict) -> bool:
        """判断这个 Action Graph 节点是否应该由本 adapter 处理"""
        ...

    @abstractmethod
    def execute(self, action: dict) -> dict:
        """执行动作，返回归一化前的原始结果"""
        ...
