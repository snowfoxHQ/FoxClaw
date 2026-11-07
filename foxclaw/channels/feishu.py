"""
Foxclaw Channels — 飞书 (Feishu/Lark) 适配器

用飞书开放平台官方 API,接入模型和 Telegram 完全不同(不套同一个模子):
  · 收消息:事件订阅 webhook —— 飞书把 im.message.receive_v1 事件 POST 到你的回调地址
    - 首次配置要响应 url_verification challenge
    - 每个事件要做签名验签(timestamp + nonce + encrypt_key + body)
    - 若开启加密,body 是 AES 加密的,要先解密
  · 发消息:调 im/v1/messages,需要 tenant_access_token
    - token 会过期(约 2 小时),要缓存 + 自动刷新

凭证:app_id + app_secret(飞书开放平台自建应用),由宿主注入。
"""
from __future__ import annotations
import hashlib
import time
from typing import Optional
from . import ChannelAdapter, InboundMessage, OutboundMessage, SendResult

FEISHU_BASE = "https://open.feishu.cn/open-apis"
LARK_BASE = "https://open.larksuite.com/open-apis"


class FeishuAdapter(ChannelAdapter):
    CHANNEL = "feishu"

    def __init__(self, app_id: str, app_secret: str,
                 verification_token: str = "", encrypt_key: str = "",
                 domain: str = "feishu", on_message=None, timeout: float = 30.0):
        super().__init__(on_message=on_message)
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token   # webhook 模式校验
        self.encrypt_key = encrypt_key                 # 若开启加密
        self.base = FEISHU_BASE if domain == "feishu" else LARK_BASE
        self.timeout = timeout
        self._token = ""           # 缓存的 tenant_access_token
        self._token_exp = 0.0      # 过期时间戳
        self._last_error = ""      # 最近一次解密/处理错误(便于排查)

    # ── 入站:事件订阅 ──────────────────────────────────────
    def decrypt_event(self, encrypt: str) -> Optional[dict]:
        """解密飞书加密事件(AES-256-CBC,key=sha256(encrypt_key))。

        用 cryptography 库(标准生态常见);若两种 AES 库都没有则记录原因。
        """
        if not self.encrypt_key:
            return None
        import base64, json
        key = hashlib.sha256(self.encrypt_key.encode()).digest()
        try:
            data = base64.b64decode(encrypt)
        except Exception:
            self._last_error = "encrypt 字段 base64 解码失败"
            return None
        if len(data) < 32 or len(data) % 16 != 0:
            self._last_error = "密文长度非法"
            return None
        iv, ciphertext = data[:16], data[16:]
        plain = self._aes_cbc_decrypt(key, iv, ciphertext)
        if plain is None:
            return None
        # PKCS7 去 padding(带校验,防损坏数据)
        if not plain:
            return None
        pad = plain[-1]
        if pad < 1 or pad > 16 or pad > len(plain):
            self._last_error = "PKCS7 padding 非法(密钥可能错误)"
            return None
        plain = plain[:-pad]
        try:
            return json.loads(plain.decode("utf-8"))
        except Exception:
            self._last_error = "解密后非合法 JSON(密钥可能错误)"
            return None

    def _aes_cbc_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> Optional[bytes]:
        """AES-256-CBC 解密。优先用 cryptography,回退 pycryptodome。"""
        # 优先 cryptography(更常见)
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            dec = cipher.decryptor()
            return dec.update(ciphertext) + dec.finalize()
        except ImportError:
            pass
        try:
            from Crypto.Cipher import AES   # pycryptodome
            return AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
        except ImportError:
            self._last_error = ("缺少 AES 库,无法解密飞书加密事件。"
                                "请安装 cryptography 或 pycryptodome")
            return None
        except Exception:
            self._last_error = "AES 解密失败"
            return None

    def verify_signature(self, timestamp: str, nonce: str, body: bytes,
                         signature: str) -> bool:
        """验签:sha256(timestamp + nonce + encrypt_key + body)。"""
        if not self.encrypt_key:
            return True       # 未开启加密则不验签(飞书此时也不发签名)
        content = (timestamp + nonce + self.encrypt_key).encode() + body
        digest = hashlib.sha256(content).hexdigest()
        return digest == signature

    def handle_event(self, payload: dict, headers: dict = None,
                     raw_body: bytes = b"") -> dict:
        """处理飞书事件回调入口。返回应答给飞书的 dict。

        宿主(FastAPI 路由)收到飞书 POST 后调本方法:
        - url_verification → 返回 challenge
        - 加密事件 → 解密
        - 验签 → normalize → on_message
        """
        headers = headers or {}
        # 1. 加密:先解密外层
        if "encrypt" in payload:
            dec = self.decrypt_event(payload["encrypt"])
            if dec is None:
                return {"error": "decrypt_failed"}
            payload = dec
        # 2. URL 验证(首次配置回调地址时)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        # 3. 验签(若有签名头)
        sig = headers.get("X-Lark-Signature") or headers.get("x-lark-signature")
        if sig:
            ts = headers.get("X-Lark-Request-Timestamp", "")
            nonce = headers.get("X-Lark-Request-Nonce", "")
            if not self.verify_signature(ts, nonce, raw_body, sig):
                return {"error": "invalid_signature"}
        # 4. 消息事件 → normalize → 回调
        inbound = self.normalize(payload)
        if inbound and self.on_message:
            self.on_message(inbound)
        return {"ok": True}

    def normalize(self, raw: dict) -> Optional[InboundMessage]:
        """飞书 im.message.receive_v1 事件 → 标准 InboundMessage。"""
        header = raw.get("header", {})
        event = raw.get("event", {})
        if header.get("event_type") != "im.message.receive_v1":
            return None
        message = event.get("message", {})
        sender = event.get("sender", {})
        sender_sid = sender.get("sender_id", {})
        # 飞书消息内容是 JSON 字符串,文本在 {"text": "..."}
        text = ""
        try:
            import json
            content = json.loads(message.get("content", "{}"))
            text = content.get("text", "")
        except Exception:
            pass
        if not text:
            return None
        chat_type = message.get("chat_type", "p2p")   # p2p=私聊, group=群
        return InboundMessage(
            channel=self.CHANNEL,
            message_id=message.get("message_id", ""),
            text=text,
            sender_id=sender_sid.get("open_id", ""),
            sender_name="",            # 飞书需额外调 API 取名,默认留空(可选 resolveSenderNames)
            conversation_id=message.get("chat_id", ""),
            is_group=(chat_type == "group"),
            timestamp=float(message.get("create_time", 0)) / 1000 or time.time(),
            raw=raw)

    # ── 出站:tenant_access_token + 发消息 ──────────────────
    def _get_token(self) -> str:
        """取 tenant_access_token,带缓存与自动刷新(过期前 5 分钟刷新)。"""
        if self._token and time.time() < self._token_exp - 300:
            return self._token
        try:
            import httpx
            with httpx.Client() as client:
                resp = client.post(
                    f"{self.base}/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                    timeout=self.timeout)
            data = resp.json()
            if data.get("code") == 0:
                self._token = data["tenant_access_token"]
                self._token_exp = time.time() + data.get("expire", 7200)
                return self._token
        except Exception:
            pass
        return ""

    def send(self, msg: OutboundMessage) -> SendResult:
        """调 im/v1/messages 发文本消息。"""
        try:
            import httpx, json
        except ImportError:
            return SendResult(False, error="缺少 httpx 依赖")
        token = self._get_token()
        if not token:
            return SendResult(False, error="获取 tenant_access_token 失败")
        # receive_id_type 用 chat_id(群和私聊的会话 ID 都用 chat_id 发)
        payload = {
            "receive_id": msg.conversation_id,
            "msg_type": "text",
            "content": json.dumps({"text": msg.text}, ensure_ascii=False),
        }
        try:
            with httpx.Client() as client:
                resp = client.post(
                    f"{self.base}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout)
        except Exception as e:
            return SendResult(False, error=f"飞书发送失败: {e}")
        data = resp.json()
        if data.get("code") != 0:
            return SendResult(False, error=f"飞书 API: {data.get('msg','未知错误')}")
        return SendResult(True, message_id=data.get("data", {}).get("message_id", ""))

    def verify_credentials(self) -> SendResult:
        """验证 app 凭证(尝试取 token)。"""
        token = self._get_token()
        return SendResult(bool(token),
                          error="" if token else "凭证无效或网络不可达")
