"""
Foxclaw Channels — 微信 (Weixin) 适配器

照腾讯官方开源插件 Tencent/openclaw-weixin 公开的后端 API 协议(iLink Bot API)
用 Python 实现。该协议由腾讯官方文档化,README 明确支持开发者用自己的后端对接,
故这是官方公开协议的合规实现,非逆向。

接入模型(与飞书/Telegram 又不同,不套同一模子):
  · 网关:https://ilinkai.weixin.qq.com,端点前缀 ilink/bot/
  · 鉴权:AuthorizationType: ilink_bot_token + Authorization: Bearer <token>
          + X-WECHAT-UIN(随机 uint32 → 十进制串 → base64)
  · 收消息:getupdates 长轮询(带 get_updates_buf 游标)
  · 发消息:sendmessage(带 to_user_id + context_token + item_list)
  · 每个请求体带 base_info: {channel_version, bot_agent}

凭证(token)由宿主注入:用户需先用 openclaw 官方 CLI 扫码登录拿到 token
(扫码登录这一步在微信侧,本适配器不复现登录,只复现已登录后的消息收发协议)。
"""
from __future__ import annotations
import base64
import secrets
from typing import Optional
from . import ChannelAdapter, InboundMessage, OutboundMessage, SendResult

WEIXIN_BASE_URL = "https://ilinkai.weixin.qq.com"
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_CONFIG = "ilink/bot/getconfig"
EP_SEND_TYPING = "ilink/bot/sendtyping"

ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5
MSG_FROM_USER = 1
MSG_FROM_BOT = 2


class WeixinAdapter(ChannelAdapter):
    CHANNEL = "weixin"

    def __init__(self, token: str, base_url: str = WEIXIN_BASE_URL,
                 bot_agent: str = "Foxclaw", channel_version: str = "2.4.3",
                 ilink_app_id: str = "bot", on_message=None,
                 long_poll_timeout_ms: int = 35000, timeout: float = 40.0):
        super().__init__(on_message=on_message)
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.bot_agent = bot_agent
        self.channel_version = channel_version
        self.ilink_app_id = ilink_app_id
        self.long_poll_timeout_ms = long_poll_timeout_ms
        self.timeout = timeout
        self._cursor = ""
        self._context_tokens: dict[str, str] = {}

    @staticmethod
    def _build_client_version(version: str) -> int:
        """照官方 buildClientVersion: (major<<16)|(minor<<8)|patch。"""
        parts = (version.split(".") + ["0", "0", "0"])[:3]
        try:
            major, minor, patch = (int(p) for p in parts)
        except ValueError:
            major = minor = patch = 0
        return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)

    @staticmethod
    def _random_wechat_uin() -> str:
        """X-WECHAT-UIN: 随机 uint32 → 十进制字符串 → base64。"""
        uint32 = int.from_bytes(secrets.token_bytes(4), "big")
        return base64.b64encode(str(uint32).encode()).decode()

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._random_wechat_uin(),
            "iLink-App-Id": self.ilink_app_id,
            "iLink-App-ClientVersion": str(self._build_client_version(self.channel_version)),
        }
        if self.token.strip():
            headers["Authorization"] = f"Bearer {self.token.strip()}"
        return headers

    def _base_info(self) -> dict:
        return {"channel_version": self.channel_version, "bot_agent": self.bot_agent}

    def _post(self, endpoint: str, body: dict, timeout: float | None = None):
        import httpx
        url = f"{self.base_url}/{endpoint}"
        payload = {**body, "base_info": self._base_info()}
        with httpx.Client() as client:
            resp = client.post(url, json=payload, headers=self._headers(),
                               timeout=timeout or self.timeout)
        return resp

    def poll_once(self) -> list[InboundMessage]:
        """长轮询拉一批新消息。宿主在循环里反复调用即可持续接收。"""
        try:
            import httpx  # noqa
        except ImportError:
            return []
        if not self.token:
            return []
        try:
            resp = self._post(EP_GET_UPDATES,
                              {"get_updates_buf": self._cursor},
                              timeout=self.long_poll_timeout_ms / 1000 + 5)
        except Exception:
            return []
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        if data.get("ret") != 0:
            return []
        new_buf = data.get("get_updates_buf")
        if new_buf is not None:
            self._cursor = new_buf
        out = []
        for raw_msg in data.get("msgs", []):
            inbound = self._normalize_msg(raw_msg)
            if inbound:
                if self.on_message:
                    self.on_message(inbound)
                out.append(inbound)
        return out

    def normalize(self, raw: dict) -> Optional[InboundMessage]:
        """单条 WeixinMessage → 标准 InboundMessage(供 handle_raw 用)。"""
        return self._normalize_msg(raw)

    def _normalize_msg(self, msg: dict) -> Optional[InboundMessage]:
        if msg.get("message_type") not in (None, MSG_FROM_USER):
            return None
        text = ""
        for item in msg.get("item_list", []) or []:
            if item.get("type") == ITEM_TEXT and item.get("text_item"):
                text += item["text_item"].get("text", "")
        if not text:
            return None
        from_user = str(msg.get("from_user_id", ""))
        ctx = msg.get("context_token", "")
        if from_user and ctx:
            self._context_tokens[from_user] = ctx
        return InboundMessage(
            channel=self.CHANNEL,
            message_id=str(msg.get("message_id", "")),
            text=text,
            sender_id=from_user,
            sender_name="",
            conversation_id=str(msg.get("session_id", "") or from_user),
            is_group=False,
            timestamp=float(msg.get("create_time_ms", 0)) / 1000 or 0.0,
            raw=msg)

    def send(self, msg: OutboundMessage) -> SendResult:
        """发文本消息。conversation_id 作为 to_user_id;context_token 从记录里取或由 extra 传入。"""
        try:
            import httpx  # noqa
        except ImportError:
            return SendResult(False, error="缺少 httpx 依赖")
        if not self.token:
            return SendResult(False, error="未配置微信 token(需先扫码登录)")
        to_user = msg.conversation_id
        ctx = msg.extra.get("context_token") or self._context_tokens.get(to_user, "")
        body = {
            "msg": {
                "to_user_id": to_user,
                "context_token": ctx,
                "item_list": [
                    {"type": ITEM_TEXT, "text_item": {"text": msg.text}}
                ],
            }
        }
        try:
            resp = self._post(EP_SEND_MESSAGE, body)
        except Exception as e:
            return SendResult(False, error=f"微信发送失败: {e}")
        if resp.status_code >= 400:
            return SendResult(False, error=f"微信 API HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception:
            data = {}
        if data.get("ret", 0) != 0:
            return SendResult(False, error=f"微信: ret={data.get('ret')} {data.get('errmsg','')}")
        return SendResult(True)

    def reply(self, inbound: InboundMessage, text: str) -> SendResult:
        """便捷回复:自动带回该用户的 context_token(微信要求回复必须带)。"""
        ctx = inbound.raw.get("context_token", "") if inbound.raw else ""
        return self.send(OutboundMessage(
            conversation_id=inbound.conversation_id, text=text,
            extra={"context_token": ctx} if ctx else {}))

    def verify_token(self, ilink_user_id: str = "") -> SendResult:
        """探测 token 是否有效(调 getconfig)。

        官方 getconfig 需要 ilink_user_id;无则取最近一个已记录的用户。
        若从未收到过消息且未传 user_id,无法探测(返回提示)。
        """
        user_id = ilink_user_id or next(iter(self._context_tokens), "")
        if not user_id:
            return SendResult(False,
                              error="需提供 ilink_user_id(或先收到一条消息)才能探测")
        try:
            resp = self._post(EP_GET_CONFIG, {"ilink_user_id": user_id})
            ok = resp.status_code == 200 and resp.json().get("ret") == 0
            return SendResult(ok, error="" if ok else "token 无效或已过期(需重新扫码登录)")
        except Exception as e:
            return SendResult(False, error=str(e))
