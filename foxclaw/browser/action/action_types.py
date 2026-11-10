"""
Action Graph 节点在 Web 场景下的具体形态。

这里只定义"数据形状"，不含任何执行逻辑，也不含任何"该不该执行"的判断——
那是 HAR / Foxclaw 的职责。
"""

from dataclasses import dataclass, field
from typing import Optional, Any


SUPPORTED_ACTIONS = ("navigate", "click", "type", "scroll", "extract", "wait")


@dataclass
class WebAction:
    """
    一个标准化的 Web 动作节点。

    type: navigate | click | type | scroll | extract | wait
    target: 元素编号（由 SemanticReducer 分配的 id），navigate 时为 URL
    value: type 动作的输入文本；scroll 的方向/距离
    meta: 透传字段，供上层（HAR/Foxclaw）附加调试信息，Web Adapter 不解析它
    """

    type: str
    target: Optional[str] = None
    value: Optional[Any] = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.type not in SUPPORTED_ACTIONS:
            raise ValueError(
                f"不支持的动作类型: {self.type}，仅支持 {SUPPORTED_ACTIONS}"
            )

    @classmethod
    def from_dict(cls, d: dict) -> "WebAction":
        return cls(
            type=d["type"],
            target=d.get("target"),
            value=d.get("value"),
            meta=d.get("meta", {}),
        )
