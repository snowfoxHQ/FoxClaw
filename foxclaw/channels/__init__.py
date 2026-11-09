"""
Foxclaw Channels — 渠道抽象层 (Channel Abstraction)

借鉴 OpenClaw 的渠道架构:适配器模式 + 消息标准化。
每个 IM 平台(Telegram/Slack/飞书…)一个适配器,封装该平台官方 API;
入站消息统一标准化成 InboundMessage,出站统一从 OutboundMessage 分派。
上层(主管 Agent)只跟标准消息打交道,不关心是哪个平台。

接入流程(对称):
  平台原始消息 → adapter.normalize() → InboundMessage → on_message 回调
  OutboundMessage → adapter.send() → 平台官方 API
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional
import time


@dataclass
class InboundMessage:
    """标准化的入站消息(各平台 normalize 后的统一格式)。"""
    channel: str                       # 来源渠道(telegram/slack/...)
    message_id: str
    text: str
    sender_id: str                     # 发送者唯一 ID(平台内)
    sender_name: str = ""
    conversation_id: str = ""          # 会话 ID(群=群ID,私聊=对方ID)
    is_group: bool = False
    timestamp: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)   # 平台原始数据(留底,便于调试)

    def to_dict(self) -> dict:
        return {"channel": self.channel, "message_id": self.message_id,
                "text": self.text, "sender_id": self.sender_id,
                "sender_name": self.sender_name,
                "conversation_id": self.conversation_id,
                "is_group": self.is_group, "timestamp": self.timestamp}


@dataclass
class OutboundMessage:
    """标准化的出站消息(上层产出,各适配器转成平台格式发送)。"""
    conversation_id: str               # 发往哪个会话
    text: str
    reply_to: str = ""                 # 可选:回复某条消息
    extra: dict = field(default_factory=dict)


@dataclass
class SendResult:
    ok: bool
    message_id: str = ""
    error: str = ""


class ChannelAdapter(ABC):
    """渠道适配器基类。每个平台实现 normalize/send/start。

    设计原则(借鉴 OpenClaw):
    - 适配器只负责"翻译"(平台格式 ↔ 标准格式)和"收发",不含业务逻辑
    - 凭证由宿主注入(token 等),适配器不硬编码任何密钥
    - 入站统一回调 on_message,上层无需关心平台差异
    """

    # 子类覆盖:渠道名
    CHANNEL = "base"

    def __init__(self, on_message: Callable[[InboundMessage], None] | None = None):
        self.on_message = on_message
        self._running = False
        self.last_error = ""       # 最近一次处理错误(便于排查,不抛断轮询)

    @abstractmethod
    def normalize(self, raw: dict) -> Optional[InboundMessage]:
        """把平台原始消息转成标准 InboundMessage。非消息事件返回 None。"""
        ...

    @abstractmethod
    def send(self, msg: OutboundMessage) -> SendResult:
        """把标准 OutboundMessage 通过平台官方 API 发出。"""
        ...

    def handle_raw(self, raw: dict):
        """收到平台原始消息时调用:标准化 → 触发 on_message 回调。

        全程异常隔离:单条消息处理失败不会抛出(否则会中断整批轮询,
        且已推进的游标不回滚 → 后续消息全部丢失)。失败记录在 last_error。
        """
        try:
            inbound = self.normalize(raw)
        except Exception as e:
            self.last_error = f"normalize 失败: {e}"
            return None
        if inbound is None:
            return None       # 非消息事件(如已读回执),忽略
        if self.on_message:
            try:
                self.on_message(inbound)
            except Exception as e:
                # on_message(主管处理)失败:记录但不抛,保证整批轮询不中断
                self.last_error = f"on_message 失败: {e}"
        return inbound

    def reply(self, inbound: InboundMessage, text: str) -> SendResult:
        """便捷回复:回到消息来源的会话。"""
        return self.send(OutboundMessage(
            conversation_id=inbound.conversation_id, text=text,
            reply_to=inbound.message_id))

    @property
    def running(self) -> bool:
        return self._running


class ChannelRegistry:
    """渠道注册表。管理多个已接入的适配器,统一分派。"""

    def __init__(self):
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter):
        self._adapters[adapter.CHANNEL] = adapter
        return adapter

    def get(self, channel: str) -> Optional[ChannelAdapter]:
        return self._adapters.get(channel)

    def all(self) -> list:
        return list(self._adapters.values())

    def send(self, channel: str, msg: OutboundMessage) -> SendResult:
        adapter = self.get(channel)
        if adapter is None:
            return SendResult(False, error=f"渠道未接入: {channel}")
        return adapter.send(msg)

    def status(self) -> dict:
        return {ch: {"running": a.running, "channel": a.CHANNEL}
                for ch, a in self._adapters.items()}
