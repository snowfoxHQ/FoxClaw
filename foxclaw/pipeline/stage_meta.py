"""stage 的命名元数据。

pipeline 的两个模块都按「函数 + stage_name 属性」标注阶段,读侧用 getattr 兜底
(见 cognitive_pipeline 里取 name 的那处)。属性赋值 `fn.stage_name = ...` 在类型
检查里不成立 —— 函数对象的类型没有这个属性;setattr 是运行期真做的同一件事,
而且让「名字」与「定义」写在同一处,不会漏标。
"""
from __future__ import annotations

from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable[..., object])


def named_stage(name: str) -> Callable[[F], F]:
    """给 stage 可调用挂 stage_name(stage 事件与轨迹标注用这个键)。"""
    def deco(fn: F) -> F:
        vars(fn)["stage_name"] = name
        return fn

    return deco
