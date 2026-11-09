"""API 层共享 OS 实例的状态与访问器。

为什么独立成模块
----------------
`foxclaw/api/__init__.py` 受 15KB 单文件体积门（tools/size_check.py）约束，装配逻辑
已贴着上限。把「状态 + 访问器」放这里，既让 13 个路由文件能统一取实例，也不让主装配
文件继续膨胀。

读写契约
--------
写入只有 `set_os_instance()`（lifespan 启动时调用一次）；读取两个入口：

  - `os_instance()`：要求实例必须就绪，未就绪抛 503（可重试），用于正常请求路径
  - `current_os()`：允许未就绪返回 None，用于「无 OS 也要降级返回」的只读路径
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException

if TYPE_CHECKING:  # 仅类型期导入,避免与 foxclaw 包根形成运行时循环
    from foxclaw import Foxclaw

_os_instance: Optional["Foxclaw"] = None


def set_os_instance(inst: "Foxclaw") -> None:
    global _os_instance
    _os_instance = inst


def current_os() -> Optional["Foxclaw"]:
    return _os_instance


def os_instance() -> "Foxclaw":
    """取共享 OS 实例;未就绪时抛 503 而不是让调用方吃到 NoneType 属性错误。"""
    inst = _os_instance
    if inst is None:
        raise HTTPException(
            status_code=503, detail="service is starting up, retry shortly")
    return inst
