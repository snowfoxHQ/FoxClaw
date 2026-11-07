# Foxclaw 消息平台接入说明

把 Foxclaw 接到消息平台,让用户在微信 / 飞书 / Telegram / Discord 里直接和你的认知系统对话。

## 架构总览

```
用户在某渠道发消息
   → 渠道适配器 normalize 成标准消息(InboundMessage)
   → ChannelDispatcher 交给主管 Agent 处理
   → 主管 run_task 思考、调度子 Agent
   → 回复通过同一渠道发回
```

三个角色各司其职:**适配器**只管收发(翻译平台格式 ↔ 标准格式),**主管**只管思考,**调度器(ChannelDispatcher)**把两者接起来。

所有适配器都遵循同一套接口(`normalize` / `send` / `reply`),但**内部各按平台官方规范实现**——微信是长轮询、飞书是事件订阅 webhook、Telegram 是 getUpdates、Discord 是 Gateway WebSocket,互不相同。

## 通用启动方式

```python
from foxclaw import Foxclaw
from foxclaw.channels.dispatcher import ChannelDispatcher

os_ = Foxclaw(data_dir="./data")
dispatcher = ChannelDispatcher(os_.supervisor)

# 注册渠道(下面每个平台示例都用这个 dispatcher)
dispatcher.register_channel(adapter)   # adapter 见下文各平台
```

`register_channel` 会自动把适配器的入站消息接到主管闭环——之后该渠道收到的每条消息都会经主管处理并自动回复。

---

## Telegram(最易跑通,建议先用它验证)

**拿 token:** Telegram 里找 `@BotFather` → 发 `/newbot` → 按提示拿到 bot token。

```python
from foxclaw.channels.telegram import TelegramAdapter

tg = TelegramAdapter(bot_token="123456:ABC-DEF...")
dispatcher.register_channel(tg)

# 验证 token
print(tg.verify_token().ok)

# 持续收消息(轮询):放进循环
import time
while True:
    tg.poll_once()      # 拉一批新消息,自动经主管处理并回复
    time.sleep(1)
```

收发模型:**getUpdates 长轮询**,无需公网回调地址,本地直接能跑。

---

## 微信(iLink Bot API)

照 Tencent 官方开源插件 [`Tencent/openclaw-weixin`](https://github.com/Tencent/openclaw-weixin) 公开的 iLink Bot API 协议实现。

**拿 token:** token 需用微信官方那套**扫码登录**获得(扫码登录在微信侧,Foxclaw 不复现登录,只复现登录后的消息收发)。可用 OpenClaw 官方 CLI 扫码:
```bash
openclaw channels login --channel openclaw-weixin   # 扫码后本地拿到 token
```

```python
from foxclaw.channels.weixin import WeixinAdapter

wx = WeixinAdapter(token="<扫码登录得到的 token>")
dispatcher.register_channel(wx)

# 持续收消息(长轮询)
while True:
    wx.poll_once()      # getUpdates 长轮询,自动处理并回复
```

收发模型:**getUpdates 长轮询**。回复时会自动带回 `context_token`(微信要求),已内部处理。`verify_token(ilink_user_id)` 需提供真实用户 ID 才能探测。

---

## 飞书 / Lark(事件订阅 webhook)

**拿凭证:** 飞书开放平台 → 创建企业自建应用 → 拿 `App ID` + `App Secret`;在「事件订阅」里配置回调地址,记下 `Verification Token` 和 `Encrypt Key`(若开启加密)。事件订阅勾选 `im.message.receive_v1`。

飞书是**平台主动推事件到你的回调地址**,所以需要一个公网可达的 webhook 端点(FastAPI 路由)。

```python
from foxclaw.channels.feishu import FeishuAdapter

fs = FeishuAdapter(
    app_id="cli_xxx",
    app_secret="xxx",
    verification_token="xxx",   # webhook 模式需要
    encrypt_key="xxx",          # 若开启加密
)
dispatcher.register_channel(fs)

# FastAPI 路由接飞书事件回调
from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/feishu/events")
async def feishu_events(request: Request):
    raw_body = await request.body()
    payload = await request.json()
    # handle_event 内部完成:URL验证 / 解密 / 验签 / 标准化 / 主管处理
    return fs.handle_event(payload, headers=dict(request.headers), raw_body=raw_body)
```

收发模型:**事件订阅 webhook**。`handle_event` 自动处理 URL 验证(challenge)、AES 解密、签名验签。发消息用 `tenant_access_token`(自动缓存 + 过期前刷新)。

> 注意:飞书加密事件解密需要 `cryptography` 库(或 pycryptodome)。环境一般已自带 cryptography。

---

## Discord(Gateway WebSocket)

**拿 token:** Discord Developer Portal → 创建 Application → Bot → 拿 bot token。**务必在 Bot 设置里开启 `MESSAGE CONTENT INTENT`**(否则收不到消息文本)。把 Bot 邀请进你的服务器。

```python
from foxclaw.channels.discord import DiscordAdapter

dc = DiscordAdapter(bot_token="...")
dispatcher.register_channel(dc)

# 验证 token(同时记录 bot 自身 ID,用于忽略自己的消息)
print(dc.verify_token().ok)

# 持续收消息(Gateway WebSocket,异步)
import asyncio
asyncio.run(dc.connect_gateway())   # 需要 websockets 库
```

收发模型:**Gateway WebSocket 长连接**(收) + **REST API**(发)。`connect_gateway` 实现了 HELLO→IDENTIFY→心跳→消息分发的最小闭环。生产环境也可换成成熟的 discord.py,再把消息喂给 `dc.normalize()`。

> 需要 `websockets` 库:`pip install websockets`

---

## 备选:对接 OpenClaw 网关

若你已在用 OpenClaw(它接了某些渠道),可让 Foxclaw 通过 OpenClaw 网关收发,而不必自己实现那些渠道。

```python
from foxclaw.channels.openclaw_bridge import OpenClawBridgeAdapter

bridge = OpenClawBridgeAdapter(gateway_url="http://127.0.0.1:8080", api_token="...")
dispatcher.register_channel(bridge)
```

OpenClaw 负责渠道接入,Foxclaw 负责认知处理,两者通过 HTTP 桥接。

---

## 多渠道同时运行

一个 dispatcher 可注册多个渠道,各自独立收发,共用同一个主管:

```python
dispatcher.register_channel(tg)     # Telegram(轮询)
dispatcher.register_channel(wx)     # 微信(轮询)
dispatcher.register_channel(dc)     # Discord(WebSocket)
# 飞书走 FastAPI webhook 路由

# 轮询类渠道各起一个循环/线程;WebSocket/webhook 类按各自方式跑
```

**会话隔离:** 默认按 `渠道:会话:发送者` 隔离(不同渠道的对话互不串)。若想做"跨渠道同一记忆"(同一用户在飞书和微信共享上下文),可改 `dispatcher._session_key` 只用 `sender_id`。

---

## 依赖小结

| 渠道 | 必需依赖 | 凭证 |
|---|---|---|
| Telegram | httpx | bot token(@BotFather) |
| 微信 | httpx | 扫码登录 token |
| 飞书 | httpx, cryptography | App ID/Secret + (加密时)Encrypt Key |
| Discord | httpx, websockets | bot token + Message Content Intent |
| OpenClaw 桥 | httpx | OpenClaw 网关地址 + token |

```bash
pip install httpx cryptography websockets
```

---

## 安全提示

- **凭证不要硬编码**:token / app_secret 用环境变量或配置文件注入,别写进代码或提交到 Git。
- 飞书 webhook 建议保留**签名验签**(开启 Encrypt Key),适配器会自动验。
- 各渠道的访问控制(谁能跟 bot 对话)目前在主管侧统一处理;如需更细的白名单,可在 `dispatcher.handle` 里按 `msg.sender_id` 加判断。
