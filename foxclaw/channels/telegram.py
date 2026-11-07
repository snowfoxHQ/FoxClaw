"""
Foxclaw Channels — Telegram 适配器

用 Telegram 官方 Bot API(api.telegram.org),完全合规:
  · 接入:在 @BotFather 创建 Bot,拿到 bot token
  · 收消息:getUpdates 长轮询(或 webhook,此处用轮询,本地最易跑通)
  · 发消息:sendMessage

凭证(bot token)由宿主注入,不硬编码。借鉴 OpenClaw 的 normalize/send 对称结构。
"""
from __future__ import annotations
from typing import Optional
from . import ChannelAdapter, InboundMessage, OutboundMessage, SendResult

TELEGRAM_API = "https://api.telegram.org"


class TelegramAdapter(ChannelAdapter):
    CHANNEL = "telegram"

    def __init__(self, bot_token: str, on_message=None, timeout: float = 30.0):
        super().__init__(on_message=on_message)
        self.bot_token = bot_token
        self.timeout = timeout
        self._offset = 0          # getUpdates 增量游标(避免重复拉取)

    @property
    def _base(self) -> str:
        return f"{TELEGRAM_API}/bot{self.bot_token}"

    def normalize(self, raw: dict) -> Optional[InboundMessage]:
        """Telegram update → 标准 InboundMessage。非文本消息返回 None。"""
        msg = raw.get("message") or raw.get("edited_message")
        if not msg or "text" not in msg:
            return None       # 非文本消息(贴纸/图片/已读等)暂忽略
        chat = msg.get("chat", {})
        sender = msg.get("from", {})
        chat_type = chat.get("type", "private")
        return InboundMessage(
            channel=self.CHANNEL,
            message_id=str(msg.get("message_id", "")),
            text=msg.get("text", ""),
            sender_id=str(sender.get("id", "")),
            sender_name=(sender.get("username")
                         or sender.get("first_name", "") or "").strip(),
            conversation_id=str(chat.get("id", "")),
            is_group=chat_type in ("group", "supergroup"),
            timestamp=float(msg.get("date", 0)),
            raw=raw)

    def send(self, msg: OutboundMessage) -> SendResult:
        """通过 sendMessage 发出。"""
        try:
            import httpx
        except ImportError:
            return SendResult(False, error="缺少 httpx 依赖")
        if not self.bot_token:
            return SendResult(False, error="未配置 bot token")
        payload = {"chat_id": msg.conversation_id, "text": msg.text}
        if msg.reply_to:
            payload["reply_to_message_id"] = msg.reply_to
        try:
            with httpx.Client() as client:
                resp = client.post(f"{self._base}/sendMessage",
                                   json=payload, timeout=self.timeout)
        except Exception as e:
            return SendResult(False, error=f"发送失败: {e}")
        if resp.status_code >= 400:
            return SendResult(False, error=f"Telegram API HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if not data.get("ok"):
            return SendResult(False, error=f"Telegram: {data.get('description','未知错误')}")
        return SendResult(True, message_id=str(data["result"].get("message_id", "")))

    def poll_once(self) -> list[InboundMessage]:
        """拉取一批新消息(getUpdates 长轮询一次)。返回标准化后的消息列表。

        宿主可在循环里反复调用实现持续接收;本地最易跑通的方式。
        """
        try:
            import httpx
        except ImportError:
            return []
        if not self.bot_token:
            return []
        try:
            with httpx.Client() as client:
                resp = client.get(f"{self._base}/getUpdates",
                                  params={"offset": self._offset, "timeout": 25},
                                  timeout=self.timeout + 5)
        except Exception:
            return []
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("ok"):
            return []
        out = []
        for update in data.get("result", []):
            # 推进游标:下次从最大 update_id + 1 开始,避免重复
            uid = update.get("update_id", 0)
            if uid >= self._offset:
                self._offset = uid + 1
            inbound = self.handle_raw(update)   # normalize + 触发 on_message
            if inbound:
                out.append(inbound)
        return out

    def verify_token(self) -> SendResult:
        """getMe 验证 bot token 是否有效(本地接入时先确认凭证)。"""
        try:
            import httpx
            with httpx.Client() as client:
                resp = client.get(f"{self._base}/getMe", timeout=self.timeout)
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                return SendResult(True, message_id=bot.get("username", ""))
            return SendResult(False, error=data.get("description", "token 无效"))
        except Exception as e:
            return SendResult(False, error=str(e))
