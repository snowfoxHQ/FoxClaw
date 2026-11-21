"""FoxClaw — 意图规范化层 (Intent Canonicalization · SC-01 / G1)。

把工具 / 技能的不同调用表达,映射到统一的规范意图 `OperationIntent`,
为后续权限判定(deny 按意图匹配)与等价路径拦截提供基础。

铁律:
  1. 只负责「识别 + 规范化」,不负责权限判断,也不负责执行。
  2. effect 必须显式映射,且不得低于现有能力声明的副作用等级(不降级)。
  3. 无法识别 / 无法提取必要信息时返回 None,不猜测、不自动放行。

本模块不 import permission(无循环依赖);permission.evaluate 按需调用 canonicalize 做 deny 归一化。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationIntent:
    """规范意图(frozen:决策依据,禁止在管线中途被改)。"""
    domain: str
    action: str
    target: str | None = None
    effect: str = "read"

    def to_key(self) -> str:
        """deny/规则匹配的稳定键:domain.action(不含 target,动作级粒度)。"""
        return f"{self.domain}.{self.action}"


@dataclass(frozen=True)
class _IntentSpec:
    """显式映射条目:能力名 → 意图 + 副作用等级 + 目标字段。"""
    domain: str
    action: str
    effect: str
    target_field: str | None = None


# 现役能力的显式映射(effect 与各自 side_effect 元数据严格一致):
#   calculator      side_effect="read"  → read
#   recall_memory   side_effect="read"  → read   (工具 TOOL_CAPABILITIES)
#   gmail_send      side_effect="send"  → send
#   calendar_create side_effect="write" → write
#   browser:*       Web 动作(SC-08)——navigate/click/type 有副作用,scroll/extract/wait 只读
_DEFAULT_INTENTS: dict[str, _IntentSpec] = {
    "calculator": _IntentSpec("compute", "eval", "read"),
    "recall_memory": _IntentSpec("memory", "recall", "read"),
    "gmail_send": _IntentSpec("external", "send", "send", target_field="to"),
    "calendar_create": _IntentSpec("external", "write", "write"),
    "browser:navigate": _IntentSpec("browser", "navigate", "write", target_field="target"),
    "browser:click": _IntentSpec("browser", "click", "write"),
    "browser:type": _IntentSpec("browser", "type", "write"),
    "browser:scroll": _IntentSpec("browser", "scroll", "read"),
    "browser:extract": _IntentSpec("browser", "extract", "read"),
    "browser:wait": _IntentSpec("browser", "wait", "read"),
}


class IntentCanonicalizer:
    """把 (工具/技能名, 参数) 归一化为 OperationIntent;未识别返回 None。"""

    def __init__(self) -> None:
        self._map: dict[str, _IntentSpec] = dict(_DEFAULT_INTENTS)

    def register(self, name: str, domain: str, action: str, *,
                 target_field: str | None = None, effect: str) -> None:
        """显式注册一个能力的意图映射(供合成测试 / 未来能力扩展注入)。

        effect 必填:强制显式声明副作用等级,杜绝 send → read 之类的降级。
        """
        self._map[name] = _IntentSpec(domain, action, effect, target_field)

    def canonicalize(self, name: str, args: dict | None = None) -> OperationIntent | None:
        """归一化一个调用表达。未识别 / 缺必要目标 → None(不猜、不放行)。"""
        spec = self._map.get(name)
        if spec is None:
            return None
        args = args if isinstance(args, dict) else {}
        target: str | None = None
        if spec.target_field is not None:
            raw = args.get(spec.target_field)
            if raw is None or raw == "":
                return None
            target = str(raw)
        return OperationIntent(domain=spec.domain, action=spec.action,
                               target=target, effect=spec.effect)


_canonicalizer = IntentCanonicalizer()


def canonicalize(name: str, args: dict | None = None) -> OperationIntent | None:
    """模块级便捷入口:把调用表达归一化;未识别返回 None(不猜、不放行)。"""
    return _canonicalizer.canonicalize(name, args)


def register(name: str, domain: str, action: str, *,
             target_field: str | None = None, effect: str) -> None:
    """模块级注册入口:向共享 canonicalizer 注入能力映射(供扩展/等价路径测试)。"""
    _canonicalizer.register(name, domain, action, target_field=target_field, effect=effect)


@dataclass(frozen=True)
class ObservationIntent:
    """观察意图(frozen)。感知通道与操作通道正交:观察暴露隐私,授权看 scope。"""
    source: str        # "screen" | "browser" | "clipboard" | "uia" | "windows"
    scope: str         # "full" | "window:<id>" | "app:<name>" | "resource:<uri>"
    sensitivity: str = "public"   # 接 taint_level(SC-03 污点传播,SC-02 只定义语义)

    def to_key(self) -> str:
        """观察规则匹配键:source:scope(范围限制靠此生效)。"""
        return f"{self.source}:{self.scope}"


@dataclass(frozen=True)
class PolicyRequest:
    """统一准入门请求:按 channel 分流到操作 / 观察两条判定通道(R4 ①)。"""
    channel: str                                  # "operation" | "observation"
    intent: OperationIntent | ObservationIntent
