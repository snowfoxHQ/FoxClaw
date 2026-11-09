"""
Foxclaw Channels — Discord 适配器

用 Discord 官方 API v10。接入模型和前三个又不同(不套同一模子):
  · 收消息:Gateway WebSocket 长连接 —— Discord 主动推事件(MESSAGE_CREATE 等)
    - 连接后要 IDENTIFY(带 bot token + intents)
    - 要按 Discord 指定的间隔发心跳(HEARTBEAT)维持连接
    - intents 决定能收哪些事件(收消息内容需 MESSAGE_CONTENT intent)
  · 发消息:REST API POST /channels/{id}/messages(带 Bot token)

凭证:bot token(Discord Developer Portal 创建应用 → Bot),由宿主注入。

说明:完整 Gateway 长连接(心跳/重连/分片)较重,本适配器提供:
  - send():REST 发送(完整可用)
  - normalize():把 Discord MESSAGE_CREATE 事件标准化
  - gateway 连接循环骨架(connect_gateway),宿主可在异步环境跑
对收消息,推荐宿主用成熟的 discord.py/WebSocket 接 on_message,再喂给 normalize。
"""
from __future__ import annotations
import json
from typing import Any, Optional
from . import ChannelAdapter, InboundMessage, OutboundMessage, SendResult

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"

# Gateway opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Intents(收消息内容需要 GUILD_MESSAGES + MESSAGE_CONTENT)
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_DIRECT_MESSAGES = 1 << 12
INTENT_MESSAGE_CONTENT = 1 << 15
DEFAULT_INTENTS = INTENT_GUILD_MESSAGES | INTENT_DIRECT_MESSAGES | INTENT_MESSAGE_CONTENT


class DiscordAdapter(ChannelAdapter):
    CHANNEL = "discord"

    def __init__(self, bot_token: str, intents: int = DEFAULT_INTENTS,
                 on_message=None, timeout: float = 30.0):
        super().__init__(on_message=on_message)
        self.bot_token = bot_token
        self.intents = intents
        self.timeout = timeout
        self._bot_user_id = ""     # 自己的 bot user id(用于忽略自己的消息)

    def _headers(self) -> dict:
        return {"Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "Foxclaw (https://github.com, 1.0)"}

    # ── 入站:标准化 Gateway MESSAGE_CREATE 事件 ───────────
    def normalize(self, raw: dict) -> Optional[InboundMessage]:
        """Discord Gateway 事件 → 标准 InboundMessage。

        raw 可以是完整 Gateway 帧({op,t,d})或直接是 MESSAGE_CREATE 的 data。
        """
        # 取出 data 部分
        if "op" in raw:
            if raw.get("op") != OP_DISPATCH or raw.get("t") != "MESSAGE_CREATE":
                return None
            data = raw.get("d", {})
        else:
            data = raw
        if not data:
            return None
        author = data.get("author", {})
        # 忽略机器人消息(含自己),避免自循环
        if author.get("bot"):
            return None
        if self._bot_user_id and str(author.get("id")) == self._bot_user_id:
            return None
        text = data.get("content", "")
        if not text:
            return None       # 非文本(纯附件/嵌入)暂忽略
        # 有 guild_id = 服务器频道(群),无 = 私信
        is_group = bool(data.get("guild_id"))
        return InboundMessage(
            channel=self.CHANNEL,
            message_id=str(data.get("id", "")),
            text=text,
            sender_id=str(author.get("id", "")),
            sender_name=author.get("username", ""),
            conversation_id=str(data.get("channel_id", "")),
            is_group=is_group,
            raw=data)

    # ── 出站:REST 发送 ────────────────────────────────────
    def send(self, msg: OutboundMessage) -> SendResult:
        """POST /channels/{channel_id}/messages 发文本。"""
        try:
            import httpx
        except ImportError:
            return SendResult(False, error="缺少 httpx 依赖")
        if not self.bot_token:
            return SendResult(False, error="未配置 Discord bot token")
        payload: dict[str, Any] = {"content": msg.text}
        if msg.reply_to:
            payload["message_reference"] = {"message_id": msg.reply_to}
        url = f"{DISCORD_API_BASE}/channels/{msg.conversation_id}/messages"
        try:
            with httpx.Client() as client:
                resp = client.post(url, json=payload, headers=self._headers(),
                                   timeout=self.timeout)
        except Exception as e:
            return SendResult(False, error=f"Discord 发送失败: {e}")
        if resp.status_code == 429:
            return SendResult(False, error="Discord 限流(429),请降低发送频率")
        if resp.status_code >= 400:
            return SendResult(False, error=f"Discord API HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception:
            data = {}
        return SendResult(True, message_id=str(data.get("id", "")))

    def verify_token(self) -> SendResult:
        """GET /users/@me 验证 bot token,并记录自己的 user id。"""
        try:
            import httpx
            with httpx.Client() as client:
                resp = client.get(f"{DISCORD_API_BASE}/users/@me",
                                  headers=self._headers(), timeout=self.timeout)
            if resp.status_code == 200:
                me = resp.json()
                self._bot_user_id = str(me.get("id", ""))
                return SendResult(True, message_id=me.get("username", ""))
            return SendResult(False, error=f"token 无效 (HTTP {resp.status_code})")
        except Exception as e:
            return SendResult(False, error=str(e))

    def build_identify(self) -> dict:
        """构造 Gateway IDENTIFY 帧(连接后发送)。"""
        return {
            "op": OP_IDENTIFY,
            "d": {
                "token": self.bot_token,
                "intents": self.intents,
                "properties": {"os": "linux", "browser": "foxclaw", "device": "foxclaw"},
            },
        }

    async def connect_gateway(self):
        """Gateway WebSocket 接收循环(异步)。需要 websockets 库。

        实现 HELLO→IDENTIFY→心跳→DISPATCH 的最小闭环。生产可换 discord.py。
        """
        try:
            import websockets
        except ImportError:
            raise RuntimeError("需要 websockets 库:pip install websockets")
        import asyncio
        async with websockets.connect(DISCORD_GATEWAY, max_size=None) as ws:
            # 1. HELLO:拿心跳间隔
            hello = json.loads(await ws.recv())
            heartbeat_ms = hello["d"]["heartbeat_interval"]
            # 2. IDENTIFY
            await ws.send(json.dumps(self.build_identify()))
            self._running = True

            async def heartbeat():
                while self._running:
                    await asyncio.sleep(heartbeat_ms / 1000)
                    try:
                        await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": None}))
                    except Exception:
                        break

            hb_task = asyncio.create_task(heartbeat())
            try:
                async for raw in ws:
                    frame = json.loads(raw)
                    if frame.get("op") == OP_DISPATCH and frame.get("t") == "MESSAGE_CREATE":
                        inbound = self.normalize(frame)
                        if inbound and self.on_message:
                            self.on_message(inbound)
            finally:
                self._running = False
                hb_task.cancel()
