"""
Foxclaw Channels — OpenClaw 对接适配器 (OpenClaw Bridge)

定位:把 OpenClaw 当作 Foxclaw 的"上游网关"。OpenClaw 负责它擅长的渠道接入
(微信、WhatsApp 等,尤其是那些实现藏在闭源插件里、Foxclaw 无法直接重写的渠道),
Foxclaw 负责认知处理。两者通过 HTTP 桥接:

  入站:OpenClaw 收到消息 → webhook 推给 Foxclaw → 本适配器 normalize → 主管处理
  出站:主管产出回复 → 本适配器 → 调 OpenClaw 的 message API → 由 OpenClaw 发回原渠道

这样微信能力来自 OpenClaw 官方那套(含闭源插件),Foxclaw 不碰微信实现,
只跟 OpenClaw 这个"统一上游"对话。OpenClaw 接的任何渠道,都能经此桥到达 Foxclaw。

注意:这是一个对接层,不是渠道本身。真正的渠道(微信等)由 OpenClaw 侧负责。
"""
from __future__ import annotations
import json
from typing import Optional
from . import ChannelAdapter, InboundMessage, OutboundMessage, SendResult
from . import signing as _signing


class OpenClawBridgeAdapter(ChannelAdapter):
    """对接 OpenClaw 网关。入站消息带原始渠道信息,出站回 OpenClaw 由其转发。"""

    CHANNEL = "openclaw"

    def __init__(self, gateway_url: str, api_token: str = "",
                 on_message=None, timeout: float = 30.0, signer=None):
        """
        gateway_url: OpenClaw 网关地址(如 http://127.0.0.1:8080)
        api_token:   调 OpenClaw API 的鉴权 token(若 OpenClaw 配置了)
        signer:      出站签名器(`signing.OutboundSigner`)。None → 读环境变量
                     FOXCLAW_CHANNEL_SIGNING_SECRET;两者都空 → 不签(opt-in)。
                     启用后签名失败**不发**(fail-closed),不静默降级成明文。
        """
        super().__init__(on_message=on_message)
        self.gateway_url = gateway_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.signer = signer if signer is not None else _signing.from_env()

    def normalize(self, raw: dict) -> Optional[InboundMessage]:
        """OpenClaw 转发来的消息 → 标准 InboundMessage。

        OpenClaw 的 webhook payload 形态(按其 gateway 消息约定),我们提取通用字段。
        关键:保留 OpenClaw 侧的 channel(真实渠道,如 wechat)和 conversation 引用,
        以便回复时让 OpenClaw 发回正确的原渠道。
        """
        # OpenClaw 消息常见结构:{channel, message:{id,text}, sender:{id,name}, conversation:{id,isGroup}}
        msg = raw.get("message") or raw
        if not isinstance(msg, dict):
            return None
        text = msg.get("text") or msg.get("content") or ""
        if not text:
            return None       # 非文本事件忽略
        sender = raw.get("sender") or msg.get("sender") or {}
        conv = raw.get("conversation") or msg.get("conversation") or {}
        return InboundMessage(
            channel=self.CHANNEL,
            message_id=str(msg.get("id") or msg.get("message_id") or ""),
            text=text,
            sender_id=str(sender.get("id", "")),
            sender_name=str(sender.get("name") or sender.get("username") or ""),
            conversation_id=str(conv.get("id") or conv.get("conversation_id") or ""),
            is_group=bool(conv.get("isGroup") or conv.get("is_group") or False),
            raw=raw)       # 原始 payload 留底(含 upstream channel 等回发所需信息)

    def _upstream_channel(self, inbound_raw: dict) -> str:
        return (inbound_raw.get("channel")
                or inbound_raw.get("source_channel") or "")

    def send(self, msg: OutboundMessage) -> SendResult:
        """调 OpenClaw 的 message API 发出。OpenClaw 再转发回原渠道(如微信)。

        出站 payload 带上 conversation_id 和(若 extra 里有)upstream channel,
        让 OpenClaw 知道该把消息发回哪个渠道的哪个会话。
        """
        try:
            import httpx
        except ImportError:
            return SendResult(False, error="缺少 httpx 依赖")
        payload = {
            "conversation": {"id": msg.conversation_id},
            "text": msg.text,
        }
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        # 若上层指明了真实渠道(从入站 raw 透传),带上,确保发回正确渠道
        if msg.extra.get("upstream_channel"):
            payload["channel"] = msg.extra["upstream_channel"]
        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        # 先序列化成 bytes 再发:签名必须覆盖**实际发出去的字节**,否则网关侧
        # 重新序列化(键序/转义差异)会导致验签失败。content= 而非 json=,
        # 就是为了保证"签的就是发的"。
        try:
            body = json.dumps(payload, ensure_ascii=False,
                              sort_keys=True).encode("utf-8")
        except Exception as e:
            return SendResult(False, error=f"出站 payload 序列化失败: {e}")
        headers["Content-Type"] = "application/json"   # content= 不走 httpx 的 json= 推断
        if self.signer is not None and self.signer.enabled:
            try:
                headers.update(self.signer.headers(body))
            except Exception as e:
                return SendResult(False, error=f"出站签名失败(未发送): {e}")
        try:
            with httpx.Client() as client:
                resp = client.post(f"{self.gateway_url}/api/message/send",
                                   content=body, headers=headers, timeout=self.timeout)
        except Exception as e:
            return SendResult(False, error=f"对接 OpenClaw 发送失败: {e}")
        if resp.status_code >= 400:
            return SendResult(False, error=f"OpenClaw API HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception:
            data = {}
        return SendResult(True, message_id=str(data.get("message_id", "")))

    def reply(self, inbound: InboundMessage, text: str) -> SendResult:
        """便捷回复:自动透传真实渠道,确保 OpenClaw 发回正确的原渠道。"""
        upstream = self._upstream_channel(inbound.raw)
        return self.send(OutboundMessage(
            conversation_id=inbound.conversation_id, text=text,
            reply_to=inbound.message_id,
            extra={"upstream_channel": upstream} if upstream else {}))

    def verify_webhook(self, headers, body: bytes,
                       tolerance: float = _signing.DEFAULT_TOLERANCE,
                       replay: "_signing.ReplayGuard | None" = None):
        """验 OpenClaw 转发来的 webhook 签名,返回 (是否通过, 原因码)。

        与出站签名同一套 canonical 结构、同一个共享密钥(`signing.verify`)。
        未配置密钥 → 直接 (False, "missing_secret"):**fail-closed**,
        调用方据此拒收,而不是"没配就默认信"。
        """
        secret = self.signer.secret if (self.signer is not None and self.signer.enabled) else ""
        return _signing.verify(headers, body, secret, tolerance=tolerance, replay=replay)

    def verify(self) -> SendResult:
        """探测 OpenClaw 网关是否可达。"""
        try:
            import httpx
            headers = {}
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"
            with httpx.Client() as client:
                resp = client.get(f"{self.gateway_url}/api/status",
                                  headers=headers, timeout=self.timeout)
            return SendResult(resp.status_code < 400,
                              error="" if resp.status_code < 400 else f"HTTP {resp.status_code}")
        except Exception as e:
            return SendResult(False, error=str(e))
