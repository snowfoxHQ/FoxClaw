# Foxclaw 消息平台接入说明

把 Foxclaw 接到微信 / 飞书 / Telegram / Discord，让用户直接在消息平台里对话。各渠道按平台官方规范实现，凭证由你本地注入。

## 通用启动方式

```python
from foxclaw import Foxclaw
from foxclaw.channels.dispatcher import ChannelDispatcher

os_ = Foxclaw(data_dir="./data")
dispatcher = ChannelDispatcher(os_.supervisor)

# 注册渠道（下面每个平台示例都用这个 dispatcher）
dispatcher.register_channel(adapter)   # adapter 见下文各平台
```

注册后，该渠道收到的每条消息都会经主管处理并自动回复。

---

## Telegram（最易跑通，建议先用它验证）

**拿 token：** Telegram 里找 `@BotFather` → 发 `/newbot` → 按提示拿到 bot token。

```python
from foxclaw.channels.telegram import TelegramAdapter

tg = TelegramAdapter(bot_token="123456:ABC-DEF...")
dispatcher.register_channel(tg)

print(tg.verify_token().ok)      # 验证 token

import time
while True:
    tg.poll_once()               # 收消息并自动回复
    time.sleep(1)
```

无需公网回调地址，本地直接能跑。

---

## 微信（iLink Bot API）

**拿 token：** 通过微信官方扫码登录获得。可用 OpenClaw 官方 CLI 扫码：

```bash
openclaw channels login --channel openclaw-weixin
```

```python
from foxclaw.channels.weixin import WeixinAdapter

wx = WeixinAdapter(token="<扫码登录得到的 token>")
dispatcher.register_channel(wx)

while True:
    wx.poll_once()
```

---

## 飞书 / Lark（事件订阅 webhook）

**拿凭证：** 飞书开放平台 → 创建企业自建应用 → 拿 `App ID` + `App Secret`；在「事件订阅」里配置回调地址，记下 `Verification Token` 和 `Encrypt Key`（若开启加密）。事件订阅勾选 `im.message.receive_v1`。

飞书是平台主动推事件到你的回调地址，因此需要一个公网可达的 webhook 端点：

```python
from foxclaw.channels.feishu import FeishuAdapter

fs = FeishuAdapter(
    app_id="cli_xxx",
    app_secret="xxx",
    verification_token="xxx",   # webhook 模式需要
    encrypt_key="xxx",          # 若开启加密
)
dispatcher.register_channel(fs)

from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/feishu/events")
async def feishu_events(request: Request):
    raw_body = await request.body()
    payload = await request.json()
    return fs.handle_event(payload, headers=dict(request.headers), raw_body=raw_body)
```

开启加密后，适配器会自动完成 URL 验证、解密与签名校验。发消息使用应用凭证，自动刷新。

> 飞书加密事件解密需要 `cryptography` 库（或 pycryptodome），环境一般已自带。

---

## Discord（Gateway WebSocket）

**拿 token：** Discord Developer Portal → 创建 Application → Bot → 拿 bot token。**务必在 Bot 设置里开启 `MESSAGE CONTENT INTENT`**（否则收不到消息文本）。把 Bot 邀请进你的服务器。

```python
from foxclaw.channels.discord import DiscordAdapter

dc = DiscordAdapter(bot_token="...")
dispatcher.register_channel(dc)

print(dc.verify_token().ok)

import asyncio
asyncio.run(dc.connect_gateway())
```

> 需要 `websockets` 库：`pip install websockets`

---

## 备选：对接 OpenClaw 网关

若你已在用 OpenClaw（它接了某些渠道），可让 Foxclaw 通过 OpenClaw 网关收发：

```python
from foxclaw.channels.openclaw_bridge import OpenClawBridgeAdapter

bridge = OpenClawBridgeAdapter(gateway_url="http://127.0.0.1:8080", api_token="...")
dispatcher.register_channel(bridge)
```

---

## 多渠道同时运行

可注册多个渠道，各自独立收发，共用同一个主管：

```python
dispatcher.register_channel(tg)     # Telegram（轮询）
dispatcher.register_channel(wx)     # 微信（轮询）
dispatcher.register_channel(dc)     # Discord（WebSocket）
# 飞书走 webhook 路由
```

各渠道的会话默认相互隔离，不会串话。

---

## 依赖小结

| 渠道 | 必需依赖 | 凭证 |
|---|---|---|
| Telegram | httpx | bot token（@BotFather） |
| 微信 | httpx | 扫码登录 token |
| 飞书 | httpx, cryptography | App ID/Secret +（加密时）Encrypt Key |
| Discord | httpx, websockets | bot token + Message Content Intent |
| OpenClaw 桥 | httpx | OpenClaw 网关地址 + token |

```bash
pip install httpx cryptography websockets
```

## 安全提示

- **凭证不要硬编码**：token / app_secret 用环境变量或配置文件注入，别写进代码或提交到 Git。
- 飞书 webhook 建议保留签名验签（开启 Encrypt Key），适配器会自动验。
- 各渠道谁能跟 bot 对话由管理员控制；部署在公开环境前请确认访问范围。
