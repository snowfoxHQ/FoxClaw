"""
BaseDriver —— 底层浏览器驱动的抽象接口。

driver 层的职责边界：只负责"怎么操纵一个真实浏览器"，
不关心语义压缩、不关心动作校验、不关心 Action Graph。

现在提供 PlaywrightDriver 作为默认实现。
以后如果要换成 CDP 直连或反检测方案，只需要新写一个
继承 BaseDriver 的类，上层（perception/action）完全不用改。
"""

from abc import ABC, abstractmethod
from typing import List, Any


class BaseDriver(ABC):

    @abstractmethod
    def navigate(self, url: str) -> None:
        ...

    @abstractmethod
    def query_interactive_elements(self, roles: tuple) -> List[Any]:
        ...

    @abstractmethod
    def describe_element(self, handle: Any) -> dict:
        """返回 {"role": str, "text": str, "attrs": dict}"""
        ...

    @abstractmethod
    def click(self, handle: Any) -> None:
        ...

    @abstractmethod
    def type_text(self, handle: Any, text: str) -> None:
        ...

    @abstractmethod
    def scroll(self, direction: str) -> None:
        ...

    @abstractmethod
    def wait(self, seconds: float) -> None:
        ...

    @abstractmethod
    def get_visible_text(self) -> str:
        ...

    @abstractmethod
    def current_url(self) -> str:
        ...

    @abstractmethod
    def current_title(self) -> str:
        ...

    @abstractmethod
    def close(self) -> None:
        ...
