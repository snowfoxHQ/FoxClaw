"""
消息渠道回归测试 — 覆盖渠道抽象层 + 四适配器 + 闭环调度

验证:
  1. 渠道抽象层(标准消息 + 注册表 + handle_raw 异常隔离)
  2. Telegram(消息标准化 + 群聊识别 + 非文本忽略)
  3. 微信(iLink 鉴权头 + context_token + ClientVersion)
  4. 飞书(URL验证 + 加密解密 + 消息标准化)
  5. Discord(Gateway 事件标准化 + bot 消息忽略)
  6. 闭环调度(收→主管→回 + 幂等去重)
"""
import sys, os, json, asyncio, hashlib, base64
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def test_abstraction():
    print("\n【1】渠道抽象层")
    from foxclaw.channels import ChannelAdapter, InboundMessage, SendResult, ChannelRegistry, OutboundMessage
    class A(ChannelAdapter):
        CHANNEL = "t"
        def normalize(self, raw):
            return InboundMessage(channel="t", message_id="m", text=raw.get("text", ""),
                                  sender_id="u", conversation_id="c")
        def send(self, msg): return SendResult(True)
    processed = []
    def handler(m):
        if m.text == "bad": raise RuntimeError("boom")
        processed.append(m.text)
    ad = A(on_message=handler)
    for txt in ["a", "bad", "c"]:
        ad.handle_raw({"text": txt})
    check(processed == ["a", "c"], "handle_raw 异常隔离(单条失败不影响其他)")
    reg = ChannelRegistry(); reg.register(ad)
    check(reg.get("t") is ad, "渠道注册表")
    check(not reg.send("nope", OutboundMessage("c", "x")).ok, "未注册渠道发送失败")


def test_telegram():
    print("\n【2】Telegram")
    from foxclaw.channels.telegram import TelegramAdapter
    tg = TelegramAdapter(bot_token="fake")
    m = tg.normalize({"update_id": 1, "message": {
        "message_id": 5, "date": 1718900000, "text": "你好",
        "chat": {"id": 123, "type": "private"},
        "from": {"id": 123, "username": "snowfox"}}})
    check(m.text == "你好" and not m.is_group, "私聊消息标准化")
    g = tg.normalize({"update_id": 2, "message": {
        "message_id": 6, "date": 1, "text": "hi",
        "chat": {"id": -100, "type": "supergroup"}, "from": {"id": 9}}})
    check(g.is_group, "群聊识别")
    check(tg.normalize({"update_id": 3, "message": {"sticker": {}, "chat": {"id": 1}}}) is None, "非文本忽略")


def test_weixin():
    print("\n【3】微信")
    from foxclaw.channels.weixin import WeixinAdapter, ITEM_TEXT, MSG_FROM_USER
    wx = WeixinAdapter(token="t")
    h = wx._headers()
    check(h["AuthorizationType"] == "ilink_bot_token", "iLink 鉴权类型")
    check(h["Authorization"].startswith("Bearer "), "Bearer token")
    check(int(base64.b64decode(h["X-WECHAT-UIN"]).decode()) >= 0, "X-WECHAT-UIN 格式")
    check(wx._build_client_version("2.4.3") == 132099, "ClientVersion 计算")
    msg = {"message_id": 1, "from_user_id": "u1", "session_id": "s",
           "message_type": MSG_FROM_USER, "context_token": "ctx1",
           "item_list": [{"type": ITEM_TEXT, "text_item": {"text": "你好"}}]}
    inb = wx._normalize_msg(msg)
    check(inb.text == "你好", "消息标准化")
    check(wx._context_tokens.get("u1") == "ctx1", "记住 context_token")
    check(wx._normalize_msg({"message_type": 2, "item_list": []}) is None, "bot 回显忽略")


def test_feishu():
    print("\n【4】飞书")
    from foxclaw.channels.feishu import FeishuAdapter
    fs = FeishuAdapter(app_id="x", app_secret="y", encrypt_key="my_secret_key_123")
    r = fs.handle_event({"type": "url_verification", "challenge": "c1"})
    check(r.get("challenge") == "c1", "URL 验证 challenge")
    def encrypt(plaintext, key_str):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        key = hashlib.sha256(key_str.encode()).digest()
        iv = os.urandom(16)
        data = plaintext.encode()
        pad = 16 - len(data) % 16
        data += bytes([pad]) * pad
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return base64.b64encode(iv + enc.update(data) + enc.finalize()).decode()
    original = {"type": "event", "x": "测试"}
    dec = fs.decrypt_event(encrypt(json.dumps(original), "my_secret_key_123"))
    check(dec == original, "AES 加密解密端到端")
    fs2 = FeishuAdapter(app_id="x", app_secret="y", encrypt_key="wrong")
    check(fs2.decrypt_event(encrypt(json.dumps(original), "my_secret_key_123")) is None, "错误密钥解密失败")
    event = {"header": {"event_type": "im.message.receive_v1"},
             "event": {"message": {"message_id": "om1", "chat_id": "oc1", "chat_type": "p2p",
                                   "content": json.dumps({"text": "查日程"}), "create_time": "1718900000000"},
                       "sender": {"sender_id": {"open_id": "ou1"}}}}
    inb = fs.normalize(event)
    check(inb.text == "查日程" and not inb.is_group, "消息标准化")


def test_discord():
    print("\n【5】Discord")
    from foxclaw.channels.discord import DiscordAdapter
    dc = DiscordAdapter(bot_token="fake")
    m = dc.normalize({"op": 0, "t": "MESSAGE_CREATE", "d": {
        "id": "m1", "content": "你好", "channel_id": "c1", "guild_id": "g1",
        "author": {"id": "u1", "username": "alice", "bot": False}}})
    check(m.text == "你好" and m.is_group, "频道消息标准化(群)")
    check(dc.normalize({"op": 0, "t": "MESSAGE_CREATE", "d": {
        "id": "m2", "content": "x", "channel_id": "c",
        "author": {"id": "b", "bot": True}}}) is None, "bot 消息忽略")


def test_closed_loop():
    print("\n【6】闭环调度 + 幂等去重")
    from foxclaw.channels import ChannelAdapter, InboundMessage, SendResult
    from foxclaw.channels.dispatcher import ChannelDispatcher
    from foxclaw import Foxclaw
    import warnings
    warnings.simplefilter("ignore")

    class Mock(ChannelAdapter):
        CHANNEL = "mock"
        def __init__(self): super().__init__(); self.sent = []
        def normalize(self, raw): return None
        def send(self, msg): self.sent.append(msg.text); return SendResult(True)

    import tempfile, shutil
    data_dir = tempfile.mkdtemp(prefix="foxclaw_ch_test_")
    shutil.rmtree(data_dir, ignore_errors=True)
    os_ = Foxclaw(data_dir=data_dir, shadow_rounds=50)
    disp = ChannelDispatcher(os_.supervisor)
    mock = Mock(); disp.register_channel(mock)

    async def go():
        msg = InboundMessage(channel="mock", message_id="m1", text="介绍自己",
                             sender_id="u1", conversation_id="c1")
        r1 = await disp.handle(msg)
        check(r1["replied"], "闭环:收→主管→回")
        r2 = await disp.handle(msg)
        check(r2.get("idempotent_skip"), "重发消息被幂等跳过")
        check(len(mock.sent) == 1, "重发只回复一次")
    asyncio.run(go())
    import shutil
    shutil.rmtree(data_dir, ignore_errors=True)


def main():
    print("=" * 56)
    print("消息渠道回归测试")
    print("=" * 56)
    tests = [test_abstraction, test_telegram, test_weixin,
             test_feishu, test_discord, test_closed_loop]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ✗ {t.__name__} 失败: {e}")
            failed += 1
    print("\n" + "=" * 56)
    print(f"结果:{len(tests) - failed}/{len(tests)} 组通过")
    print("=" * 56)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
